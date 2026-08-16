#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
readonly DATA_ROOT='/home/bgpdata/Domeye-Core-dev-data'
readonly CONFIG_FILE='/home/bgpdata/Domeye-Core-data/config/database.env'
readonly EXPECTED_LOWER_PGDATA='/home/bgpdata/Domeye-Core-data/work/resume-20260717T124354Z-attempt3/postgres'
readonly EXPECTED_RELEASE_DIR='/home/bgpdata/Domeye-Core-artifacts/releases/20260717T124354Z'
readonly EXPECTED_RELEASE_ID='20260717T124354Z'
readonly CANDIDATE_WORK_ROOT='/home/bgpdata/Domeye-Core-data/work'
readonly CANDIDATE_GUARD_ROOT="${CANDIDATE_WORK_ROOT}/.candidate-use-locks"
readonly CANDIDATE_GUARD_DIR="${CANDIDATE_GUARD_ROOT}/${EXPECTED_RELEASE_ID}.dev-overlay.lock"
readonly STATE_FILE="${DATA_ROOT}/state.json"
readonly LOWER_VIEW_DIR="${DATA_ROOT}/overlay/lower-readonly"
readonly UPPER_DIR="${DATA_ROOT}/overlay/upper"
readonly WORK_DIR="${DATA_ROOT}/overlay/work"
readonly MERGED_DIR="${DATA_ROOT}/overlay/merged"
readonly CONTAINER_NAME='domeye_core_dev_pg'
readonly CONTAINER_DATA_DESTINATION='/var/lib/postgresql/data'
readonly FIXED_PORT='31627'
readonly DATA_START='2026-02-01 00:00:00'
readonly DATA_END_EXCLUSIVE='2026-04-01 00:00:00'
readonly LOCK_DIR="${DATA_ROOT}/.manage.lock"
readonly PRUNE_SQL="${SCRIPT_DIR}/prune-feb-mar.sql"
readonly VERIFY_SQL="${SCRIPT_DIR}/verify-feb-mar.sql"
readonly CONTAINER_ROLE_LABEL='development-database'
readonly CONTAINER_INSTANCE_LABEL='domeye-core-dev-feb-mar-2026'
readonly CONTAINER_STATE_LABEL="${STATE_FILE}"
readonly DEV_API_SCREEN_NAME='domeye_core_dev_api'
readonly CORE_API_SCREEN_NAME='domeye_core_app'

VALIDATED_RELEASE_MANIFEST_SHA=''
VALIDATED_DATABASE_MANIFEST_SHA=''
VALIDATED_INVENTORY_SHA=''
VALIDATED_PG_VERSION_SHA=''
VALIDATED_PG_CONTROL_SHA=''
VALIDATED_SYSTEM_IDENTIFIER=''
VALIDATED_IMAGE_ID=''
VALIDATED_CHECKPOINT_KEY=''
VALIDATED_GUARD_DIR=''
GUARD_CREATED_THIS_RUN=false

error() {
    printf '错误：%s\n' "$*" >&2
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        error "缺少命令：$1"
        exit 1
    }
}

require_root() {
    if (( EUID != 0 )); then
        error 'OverlayFS 开发库必须由 root 管理'
        exit 1
    fi
}

assert_dev_api_not_running() {
    if screen -ls 2>/dev/null | awk \
        -v dev_suffix=".${DEV_API_SCREEN_NAME}" \
        -v core_suffix=".${CORE_API_SCREEN_NAME}" '
        $1 ~ /^[0-9]+\./ && (
            substr($1, length($1) - length(dev_suffix) + 1) == dev_suffix
            || substr($1, length($1) - length(core_suffix) + 1) == core_suffix
        ) {
            found = 1
        }
        END { exit(found ? 0 : 1) }
    '; then
        error "二三月 API 仍在运行；数据库复验或启停前必须停止远程开发档和核心冻结档"
        exit 1
    fi
}

file_sha256() {
    sha256sum -- "$1" | awk '{print $1}'
}

assert_fixed_runtime_paths() {
    if [[ -n "${DOMEYE_DEV_DATABASE_ROOT:-}" && "${DOMEYE_DEV_DATABASE_ROOT:-}" != "${DATA_ROOT}" ]]; then
        error "开发数据库根目录固定为 ${DATA_ROOT}，拒绝环境变量覆盖"
        exit 1
    fi
    if [[ -n "${DOMEYE_DEV_DATABASE_CONFIG:-}" && "${DOMEYE_DEV_DATABASE_CONFIG:-}" != "${CONFIG_FILE}" ]]; then
        error "开发数据库配置固定为 ${CONFIG_FILE}，拒绝环境变量覆盖"
        exit 1
    fi
    if [[ "$(readlink -m -- "${DATA_ROOT}")" != "${DATA_ROOT}" ]]; then
        error "开发数据库根目录包含软链接祖先或不是规范路径：${DATA_ROOT}"
        exit 1
    fi
    if [[ -e "${DATA_ROOT}" || -L "${DATA_ROOT}" ]]; then
        if [[ ! -d "${DATA_ROOT}" || -L "${DATA_ROOT}" || "$(readlink -f -- "${DATA_ROOT}")" != "${DATA_ROOT}" ]]; then
            error "开发数据库根目录不是安全的普通目录：${DATA_ROOT}"
            exit 1
        fi
        local owner_uid mode
        owner_uid="$(stat -c '%u' "${DATA_ROOT}")"
        mode="$(stat -c '%a' "${DATA_ROOT}")"
        if [[ "${owner_uid}" != '0' ]] || (( (8#${mode} & 8#022) != 0 )); then
            error "开发数据库根目录必须由 root 拥有且不可被组或其他用户写入：${DATA_ROOT}"
            exit 1
        fi
    fi
}

require_regular_file() {
    local path="$1"
    local label="$2"
    if [[ ! -f "${path}" || -L "${path}" || "$(readlink -f -- "${path}")" != "${path}" ]]; then
        error "${label}不是规范普通文件：${path}"
        return 1
    fi
    local mode
    mode="$(stat -c '%a' "${path}")"
    if (( (8#${mode} & 8#022) != 0 )); then
        error "${label}不能被组或其他用户写入：${path}"
        return 1
    fi
}

load_database_config() {
    require_regular_file "${CONFIG_FILE}" '数据库配置'
    local owner_uid mode
    owner_uid="$(stat -c '%u' "${CONFIG_FILE}")"
    mode="$(stat -c '%a' "${CONFIG_FILE}")"
    if [[ "${owner_uid}" != '0' ]] || (( (8#${mode} & 8#077) != 0 )); then
        error "数据库配置必须由 root 拥有且权限不宽于 0600：${CONFIG_FILE}"
        exit 1
    fi
    # shellcheck disable=SC1090
    source "${CONFIG_FILE}"
    : "${DOMEYE_CORE_DB_NAME:?缺少 DOMEYE_CORE_DB_NAME}"
    : "${DOMEYE_CORE_DB_ADMIN_USER:?缺少 DOMEYE_CORE_DB_ADMIN_USER}"
    : "${DOMEYE_CORE_DB_READER_USER:?缺少 DOMEYE_CORE_DB_READER_USER}"
    local value
    for value in "${DOMEYE_CORE_DB_NAME}" "${DOMEYE_CORE_DB_ADMIN_USER}" "${DOMEYE_CORE_DB_READER_USER}"; do
        if [[ -z "${value}" || "${value}" == *$'\n'* || "${value}" == *$'\r'* ]]; then
            error '数据库名称和角色名称不能为空或包含换行'
            exit 1
        fi
    done
}

lock() {
    assert_fixed_runtime_paths
    install -d -o 0 -g 0 -m 0750 "${DATA_ROOT}"
    assert_fixed_runtime_paths
    if ! mkdir -m 0700 "${LOCK_DIR}" 2>/dev/null; then
        error '已有开发数据库管理操作正在运行；陈旧锁必须人工复核后处理'
        exit 1
    fi
    trap cleanup_main_lock EXIT
}

cleanup_main_lock() {
    if [[ "${GUARD_CREATED_THIS_RUN}" == true \
        && -d "${CANDIDATE_GUARD_DIR}" \
        && ! -e "${CANDIDATE_GUARD_DIR}/owner.json" \
        && ! -L "${CANDIDATE_GUARD_DIR}/owner.json" ]]; then
        rmdir "${CANDIDATE_GUARD_DIR}" 2>/dev/null || true
    fi
    rmdir "${LOCK_DIR}" 2>/dev/null || true
}

state_value() {
    jq -er "$1" "${STATE_FILE}"
}

checkpoint_key_for() {
    local release_id="$1"
    local system_identifier="$2"
    local prune_sha="$3"
    local inventory_sha="$4"
    {
        printf 'release_id=%s\n' "${release_id}"
        printf 'system_identifier=%s\n' "${system_identifier}"
        printf 'data_start=%s\n' "${DATA_START}"
        printf 'data_end_exclusive=%s\n' "${DATA_END_EXCLUSIVE}"
        printf 'prune_sql_sha256=%s\n' "${prune_sha}"
        printf 'inventory_sha256=%s\n' "${inventory_sha}"
    } | sha256sum | awk '{print $1}'
}

validate_state_structure() {
    require_regular_file "${STATE_FILE}" '开发数据库状态文件'
    local owner_uid mode
    owner_uid="$(stat -c '%u' "${STATE_FILE}")"
    mode="$(stat -c '%a' "${STATE_FILE}")"
    if [[ "${owner_uid}" != '0' ]] || (( (8#${mode} & 8#077) != 0 )); then
        error '开发数据库状态文件必须由 root 拥有且权限为 0600'
        exit 1
    fi

    jq -e \
        --arg release_id "${EXPECTED_RELEASE_ID}" \
        --arg release_dir "${EXPECTED_RELEASE_DIR}" \
        --arg lower_pgdata "${EXPECTED_LOWER_PGDATA}" \
        --arg guard_dir "${CANDIDATE_GUARD_DIR}" \
        --arg data_start "${DATA_START}" \
        --arg data_end "${DATA_END_EXCLUSIVE}" \
        --argjson port "${FIXED_PORT}" \
        '.schema_version == 2
         and (.phase == "preparing" or .phase == "pruned" or .phase == "verified")
         and .release_id == $release_id
         and .release_dir == $release_dir
         and .lower_pgdata == $lower_pgdata
         and (.system_identifier | type) == "string"
         and (.system_identifier | test("^[0-9]+$"))
         and .guard_dir == $guard_dir
         and (.image_id | test("^sha256:[0-9a-f]{64}$"))
         and .port == $port
         and .data_start == $data_start
         and .data_end_exclusive == $data_end
         and (.checkpoint_key | test("^[0-9a-f]{64}$"))
         and (.hashes.release_manifest | test("^[0-9a-f]{64}$"))
         and (.hashes.database_manifest | test("^[0-9a-f]{64}$"))
         and (.hashes.inventory | test("^[0-9a-f]{64}$"))
         and (.hashes.pg_version | test("^[0-9a-f]{64}$"))
         and (.hashes.pg_control | test("^[0-9a-f]{64}$"))
         and (.hashes.prune_sql | test("^[0-9a-f]{64}$"))
         and (.hashes.verify_sql | test("^[0-9a-f]{64}$"))
         and (.created_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))
         and (.updated_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))
         and ((.pruned_at == null) or (.pruned_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")))
         and ((.verified_at == null) or (.verified_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")))
         and (if .phase == "preparing" then .pruned_at == null and .verified_at == null else true end)
         and (if .phase == "pruned" then .pruned_at != null and .verified_at == null else true end)
         and (if .phase == "verified" then .pruned_at != null and .verified_at != null else true end)' \
        "${STATE_FILE}" >/dev/null || {
        error '开发数据库状态文件无效'
        exit 1
    }
}

assert_candidate_guard() {
    local guard_dir="$1"
    local lower_pgdata="$2"
    local release_id="$3"
    local system_identifier="$4"
    local owner_file="${guard_dir}/owner.json"
    if [[ ! -d "${guard_dir}" || -L "${guard_dir}" || "$(readlink -f -- "${guard_dir}")" != "${guard_dir}" ]]; then
        error "候选占用锁不存在或不安全：${guard_dir}"
        return 1
    fi
    require_regular_file "${owner_file}" '候选占用锁所有者文件'
    jq -e \
        --arg lower_pgdata "${lower_pgdata}" \
        --arg release_id "${release_id}" \
        --arg system_identifier "${system_identifier}" \
        --arg state_file "${STATE_FILE}" \
        '.schema_version == 1
         and .owner == "domeye-dev-overlay"
         and .lower_pgdata == $lower_pgdata
         and .release_id == $release_id
         and .system_identifier == $system_identifier
         and .state_file == $state_file' \
        "${owner_file}" >/dev/null || {
        error "候选占用锁属于其他操作：${guard_dir}"
        return 1
    }
}

acquire_candidate_guard_directory() {
    if [[ "$(readlink -m -- "${CANDIDATE_GUARD_ROOT}")" != "${CANDIDATE_GUARD_ROOT}" ]]; then
        error "候选占用锁根目录不安全：${CANDIDATE_GUARD_ROOT}"
        exit 1
    fi
    install -d -o 0 -g 0 -m 0750 "${CANDIDATE_GUARD_ROOT}"
    if [[ -e "${CANDIDATE_GUARD_DIR}" || -L "${CANDIDATE_GUARD_DIR}" ]]; then
        if [[ ! -d "${CANDIDATE_GUARD_DIR}" || -L "${CANDIDATE_GUARD_DIR}" \
            || "$(readlink -f -- "${CANDIDATE_GUARD_DIR}")" != "${CANDIDATE_GUARD_DIR}" ]]; then
            error "候选占用锁路径不安全：${CANDIDATE_GUARD_DIR}"
            exit 1
        fi
        GUARD_CREATED_THIS_RUN=false
    else
        if ! mkdir -m 0700 "${CANDIDATE_GUARD_DIR}" 2>/dev/null; then
            error "候选 PGDATA 已被其他数据库流程占用：${CANDIDATE_GUARD_DIR}"
            exit 1
        fi
        GUARD_CREATED_THIS_RUN=true
    fi
}

finalize_candidate_guard() {
    local lower_pgdata="$1"
    local release_id="$2"
    local system_identifier="$3"
    local owner_file="${CANDIDATE_GUARD_DIR}/owner.json"
    if [[ -e "${owner_file}" || -L "${owner_file}" ]]; then
        assert_candidate_guard "${CANDIDATE_GUARD_DIR}" "${lower_pgdata}" "${release_id}" "${system_identifier}"
    elif [[ "${GUARD_CREATED_THIS_RUN}" == true ]]; then
        local owner_tmp="${CANDIDATE_GUARD_DIR}/.owner.json.tmp.$$"
        jq -n \
            --argjson schema_version 1 \
            --arg owner 'domeye-dev-overlay' \
            --arg lower_pgdata "${lower_pgdata}" \
            --arg release_id "${release_id}" \
            --arg system_identifier "${system_identifier}" \
            --arg state_file "${STATE_FILE}" \
            --arg created_at "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
            '{
              schema_version: $schema_version,
              owner: $owner,
              lower_pgdata: $lower_pgdata,
              release_id: $release_id,
              system_identifier: $system_identifier,
              state_file: $state_file,
              created_at: $created_at
            }' > "${owner_tmp}"
        chmod 0600 "${owner_tmp}"
        mv -T -- "${owner_tmp}" "${owner_file}"
        GUARD_CREATED_THIS_RUN=false
    else
        error "既有候选占用锁没有可信所有者文件：${CANDIDATE_GUARD_DIR}"
        exit 1
    fi
    VALIDATED_GUARD_DIR="${CANDIDATE_GUARD_DIR}"
}

assert_lower_safe() {
    local lower_pgdata="$1"
    if [[ "${lower_pgdata}" != "${EXPECTED_LOWER_PGDATA}" ]]; then
        error "本轮开发库只允许固定候选 PGDATA：${EXPECTED_LOWER_PGDATA}"
        exit 1
    fi
    if [[ ! -d "${lower_pgdata}" || -L "${lower_pgdata}" || "$(readlink -f -- "${lower_pgdata}")" != "${lower_pgdata}" ]]; then
        error "候选 PGDATA 不是规范普通目录：${lower_pgdata}"
        exit 1
    fi
    if [[ ! -f "${lower_pgdata}/PG_VERSION" || -L "${lower_pgdata}/PG_VERSION" || "$(< "${lower_pgdata}/PG_VERSION")" != '12' ]]; then
        error '候选 PGDATA 不是 PostgreSQL 12 数据目录'
        exit 1
    fi
    if [[ -e "${lower_pgdata}/postmaster.pid" || -L "${lower_pgdata}/postmaster.pid" ]]; then
        error '候选 PGDATA 正被直接运行或含有 postmaster.pid，拒绝作为 lowerdir'
        exit 1
    fi
}

assert_lower_not_mounted_by_any_container() {
    local lower_pgdata="$1"
    local container_id mount_source mount_real
    while IFS= read -r container_id; do
        [[ -n "${container_id}" ]] || continue
        while IFS= read -r mount_source; do
            [[ -n "${mount_source}" && -e "${mount_source}" ]] || continue
            mount_real="$(readlink -f -- "${mount_source}")"
            if [[ "${mount_real}" == "${lower_pgdata}" \
                || "${mount_real}" == "${lower_pgdata}/"* ]]; then
                error "固定 lower PGDATA 本身或其子路径已被 Docker 容器直接挂载：${container_id} -> ${mount_real}"
                return 1
            fi
        done < <(docker inspect --format '{{range .Mounts}}{{println .Source}}{{end}}' "${container_id}")
    done < <(docker ps -aq)
}

read_offline_system_identifier() {
    local lower_pgdata="$1"
    local image_id="$2"
    docker run --rm \
        --env 'LC_ALL=C' \
        --user postgres \
        --volume "${lower_pgdata}:${CONTAINER_DATA_DESTINATION}:ro" \
        --entrypoint pg_controldata \
        "${image_id}" \
        "${CONTAINER_DATA_DESTINATION}" \
        | awk -F ': *' '/Database system identifier/ {print $2; exit}'
}

validate_release_and_candidate() {
    local lower_pgdata="$1"
    local release_dir="$2"
    if [[ "${lower_pgdata}" != "${EXPECTED_LOWER_PGDATA}" || "${release_dir}" != "${EXPECTED_RELEASE_DIR}" ]]; then
        error '本轮开发数据库只允许 README 中固定的候选 PGDATA 与发布目录'
        exit 2
    fi
    assert_lower_safe "${lower_pgdata}"
    if [[ ! -d "${release_dir}" || -L "${release_dir}" || "$(readlink -f -- "${release_dir}")" != "${release_dir}" ]]; then
        error "固定发布目录不存在或不安全：${release_dir}"
        exit 1
    fi

    local manifest="${release_dir}/manifest.json"
    local database_manifest="${release_dir}/database-manifest.json"
    local inventory="${release_dir}/database-inventory.json"
    require_regular_file "${manifest}" '发布总清单'
    require_regular_file "${database_manifest}" '数据库组件清单'
    require_regular_file "${inventory}" '数据库 inventory'
    require_regular_file "${lower_pgdata}/PG_VERSION" '候选 PG_VERSION'
    require_regular_file "${lower_pgdata}/global/pg_control" '候选 pg_control'

    local inventory_sha image_id system_identifier pg_version_sha pg_control_sha
    inventory_sha="$(file_sha256 "${inventory}")"
    image_id="$(jq -er '.image.id' "${database_manifest}")"
    if [[ ! "${image_id}" =~ ^sha256:[0-9a-f]{64}$ \
        || "$(docker image inspect --format '{{.Id}}' "${image_id}" 2>/dev/null || true)" != "${image_id}" ]]; then
        error "冻结数据库镜像不存在或 ID 无效：${image_id}"
        exit 1
    fi

    jq -e \
        --arg release_id "${EXPECTED_RELEASE_ID}" \
        --arg data_start "${DATA_START}" \
        --arg inventory_sha "${inventory_sha}" \
        --slurpfile database "${database_manifest}" \
        '.release_id == $release_id
         and .data_start == $data_start
         and .database == $database[0]
         and $database[0].release_id == $release_id
         and $database[0].data_start == $data_start
         and $database[0].inventory.name == "database-inventory.json"
         and $database[0].inventory.sha256 == $inventory_sha
         and ($database[0].image.id | test("^sha256:[0-9a-f]{64}$"))' \
        "${manifest}" >/dev/null || {
        error '发布总清单、数据库组件清单与 inventory 不一致'
        exit 1
    }

    jq -e \
        --arg data_start "${DATA_START}" \
        --arg data_end "${DATA_END_EXCLUSIVE}" \
        '
        def retained:
            . == "feature_country"
            or test("^(event_table|hijack|sub_hijack|leak_event|prefix_outage|as_outage|country_outage)_20260[23]$")
            or test("^feature_(other|us|br|cn|ru|in|gb|id|de|au|pl)_20260[23]$");
        .schema_version == 1
        and .data_start == $data_start
        and .integrity.table_whitelist.ok == true
        and .integrity.detail_references.ok == true
        and .integrity.detail_references.malformed_count == 0
        and .integrity.detail_references.orphan_count == 0
        and ([.tables[] | select(.name | retained)] | length) == 37
        and ([.tables[] | select(.name | retained) | .name] | unique | length) == 37
        and all(
            .tables[];
            if ((.name | retained) and .name != "feature_country") then
                ((.min_time == null or .min_time >= $data_start)
                 and (.max_time == null or .max_time < $data_end))
            else true end
        )
        and any(
            .tables[];
            .name == "feature_country"
            and .row_count > 0
            and .min_time != null
            and .max_time != null
            and .min_time < $data_end
            and .max_time >= $data_start
        )
        and ([.tables[] | select(.name | test("^event_table_20260[23]$")) | .row_count] | add // 0) > 0
        ' "${inventory}" >/dev/null || {
        error '发布 inventory 不满足 2、3 月开发库的范围、非空或引用完整性前提'
        exit 1
    }

    acquire_candidate_guard_directory
    assert_lower_safe "${lower_pgdata}"
    assert_lower_not_mounted_by_any_container "${lower_pgdata}"
    system_identifier="$(read_offline_system_identifier "${lower_pgdata}" "${image_id}")"
    if [[ ! "${system_identifier}" =~ ^[0-9]+$ ]]; then
        error '无法读取固定候选 PGDATA 的离线 system identifier'
        exit 1
    fi
    pg_version_sha="$(file_sha256 "${lower_pgdata}/PG_VERSION")"
    pg_control_sha="$(file_sha256 "${lower_pgdata}/global/pg_control")"
    finalize_candidate_guard "${lower_pgdata}" "${EXPECTED_RELEASE_ID}" "${system_identifier}"
    assert_lower_safe "${lower_pgdata}"
    assert_lower_not_mounted_by_any_container "${lower_pgdata}"

    VALIDATED_RELEASE_MANIFEST_SHA="$(file_sha256 "${manifest}")"
    VALIDATED_DATABASE_MANIFEST_SHA="$(file_sha256 "${database_manifest}")"
    VALIDATED_INVENTORY_SHA="${inventory_sha}"
    VALIDATED_PG_VERSION_SHA="${pg_version_sha}"
    VALIDATED_PG_CONTROL_SHA="${pg_control_sha}"
    VALIDATED_SYSTEM_IDENTIFIER="${system_identifier}"
    VALIDATED_IMAGE_ID="${image_id}"
    VALIDATED_CHECKPOINT_KEY="$(checkpoint_key_for \
        "${EXPECTED_RELEASE_ID}" \
        "${system_identifier}" \
        "$(file_sha256 "${PRUNE_SQL}")" \
        "${inventory_sha}")"
}

write_initial_state() {
    local created_at="$1"
    local temporary="${DATA_ROOT}/.state.json.tmp.$$"
    jq -n \
        --argjson schema_version 2 \
        --arg phase 'preparing' \
        --arg release_id "${EXPECTED_RELEASE_ID}" \
        --arg release_dir "${EXPECTED_RELEASE_DIR}" \
        --arg lower_pgdata "${EXPECTED_LOWER_PGDATA}" \
        --arg system_identifier "${VALIDATED_SYSTEM_IDENTIFIER}" \
        --arg guard_dir "${VALIDATED_GUARD_DIR}" \
        --arg image_id "${VALIDATED_IMAGE_ID}" \
        --argjson port "${FIXED_PORT}" \
        --arg data_start "${DATA_START}" \
        --arg data_end_exclusive "${DATA_END_EXCLUSIVE}" \
        --arg checkpoint_key "${VALIDATED_CHECKPOINT_KEY}" \
        --arg release_manifest_sha "${VALIDATED_RELEASE_MANIFEST_SHA}" \
        --arg database_manifest_sha "${VALIDATED_DATABASE_MANIFEST_SHA}" \
        --arg inventory_sha "${VALIDATED_INVENTORY_SHA}" \
        --arg pg_version_sha "${VALIDATED_PG_VERSION_SHA}" \
        --arg pg_control_sha "${VALIDATED_PG_CONTROL_SHA}" \
        --arg prune_sql_sha "$(file_sha256 "${PRUNE_SQL}")" \
        --arg verify_sql_sha "$(file_sha256 "${VERIFY_SQL}")" \
        --arg created_at "${created_at}" \
        '{
          schema_version: $schema_version,
          phase: $phase,
          release_id: $release_id,
          release_dir: $release_dir,
          lower_pgdata: $lower_pgdata,
          system_identifier: $system_identifier,
          guard_dir: $guard_dir,
          image_id: $image_id,
          port: $port,
          data_start: $data_start,
          data_end_exclusive: $data_end_exclusive,
          checkpoint_key: $checkpoint_key,
          hashes: {
            release_manifest: $release_manifest_sha,
            database_manifest: $database_manifest_sha,
            inventory: $inventory_sha,
            pg_version: $pg_version_sha,
            pg_control: $pg_control_sha,
            prune_sql: $prune_sql_sha,
            verify_sql: $verify_sql_sha
          },
          created_at: $created_at,
          updated_at: $created_at,
          pruned_at: null,
          verified_at: null
        }' > "${temporary}"
    chmod 0600 "${temporary}"
    mv -T -- "${temporary}" "${STATE_FILE}"
}

transition_state() {
    local expected_phase="$1"
    local next_phase="$2"
    local transitioned_at
    transitioned_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    local temporary="${DATA_ROOT}/.state.json.tmp.$$"
    jq -e \
        --arg expected_phase "${expected_phase}" \
        --arg next_phase "${next_phase}" \
        --arg transitioned_at "${transitioned_at}" \
        '
        if .phase != $expected_phase then
            error("phase mismatch")
        else
            .phase = $next_phase
            | .updated_at = $transitioned_at
            | if $next_phase == "pruned" then .pruned_at = $transitioned_at
              elif $next_phase == "verified" then .verified_at = $transitioned_at
              else . end
        end
        ' "${STATE_FILE}" > "${temporary}" || {
        error "状态转换失败：${expected_phase} -> ${next_phase}"
        return 1
    }
    chmod 0600 "${temporary}"
    mv -T -- "${temporary}" "${STATE_FILE}"
    validate_state_structure
}

record_verification() {
    local expected_phase="$1"
    local verified_at verify_sha temporary
    verified_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    verify_sha="$(file_sha256 "${VERIFY_SQL}")"
    temporary="${DATA_ROOT}/.state.json.tmp.$$"
    jq -e \
        --arg expected_phase "${expected_phase}" \
        --arg verified_at "${verified_at}" \
        --arg verify_sha "${verify_sha}" \
        '
        if .phase != $expected_phase then
            error("phase mismatch")
        else
            .phase = "verified"
            | .updated_at = $verified_at
            | .verified_at = $verified_at
            | .hashes.verify_sql = $verify_sha
        end
        ' "${STATE_FILE}" > "${temporary}" || {
        error "验收状态落盘失败：${expected_phase} -> verified"
        return 1
    }
    chmod 0600 "${temporary}"
    mv -T -- "${temporary}" "${STATE_FILE}"
    validate_state_structure
}

invalidate_verification() {
    local invalidated_at temporary
    invalidated_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    temporary="${DATA_ROOT}/.state.json.tmp.$$"
    jq -e \
        --arg invalidated_at "${invalidated_at}" \
        '
        if .phase != "verified" then
            error("phase mismatch")
        else
            .phase = "pruned"
            | .updated_at = $invalidated_at
            | .verified_at = null
        end
        ' "${STATE_FILE}" > "${temporary}" || {
        error '旧 verified 状态失效落盘失败'
        return 1
    }
    chmod 0600 "${temporary}"
    mv -T -- "${temporary}" "${STATE_FILE}"
    validate_state_structure
}

assert_state_provenance() {
    validate_state_structure
    local expected_checkpoint lower_pgdata
    lower_pgdata="$(state_value '.lower_pgdata')"
    require_regular_file "${lower_pgdata}/PG_VERSION" '候选 PG_VERSION'
    require_regular_file "${lower_pgdata}/global/pg_control" '候选 pg_control'
    if [[ "$(file_sha256 "${lower_pgdata}/PG_VERSION")" != "$(state_value '.hashes.pg_version')" \
        || "$(file_sha256 "${lower_pgdata}/global/pg_control")" != "$(state_value '.hashes.pg_control')" ]]; then
        error '候选 PG_VERSION 或 global/pg_control 在开发库建立后发生变化'
        exit 1
    fi
    if [[ "$(file_sha256 "${PRUNE_SQL}")" != "$(state_value '.hashes.prune_sql')" ]]; then
        error '裁剪 SQL 与持久检查点固定的哈希不一致'
        exit 1
    fi
    expected_checkpoint="$(checkpoint_key_for \
        "$(state_value '.release_id')" \
        "$(state_value '.system_identifier')" \
        "$(state_value '.hashes.prune_sql')" \
        "$(state_value '.hashes.inventory')")"
    if [[ "${expected_checkpoint}" != "$(state_value '.checkpoint_key')" ]]; then
        error '状态文件中的裁剪检查点摘要不一致'
        exit 1
    fi
    assert_candidate_guard \
        "$(state_value '.guard_dir')" \
        "$(state_value '.lower_pgdata')" \
        "$(state_value '.release_id')" \
        "$(state_value '.system_identifier')"
}

assert_state_lower_identity() {
    assert_state_provenance
    local lower_pgdata image_id expected_system actual_system
    lower_pgdata="$(state_value '.lower_pgdata')"
    image_id="$(state_value '.image_id')"
    expected_system="$(state_value '.system_identifier')"
    assert_lower_safe "${lower_pgdata}"
    assert_lower_not_mounted_by_any_container "${lower_pgdata}"
    if [[ "$(docker image inspect --format '{{.Id}}' "${image_id}" 2>/dev/null || true)" != "${image_id}" ]]; then
        error "状态固定的数据库镜像不可用：${image_id}"
        exit 1
    fi
    actual_system="$(read_offline_system_identifier "${lower_pgdata}" "${image_id}")"
    if [[ "${actual_system}" != "${expected_system}" ]]; then
        error '候选 lower PGDATA 的 system identifier 已变化'
        exit 1
    fi
    assert_lower_not_mounted_by_any_container "${lower_pgdata}"
}

assert_overlay_paths() {
    local path
    for path in "${DATA_ROOT}" "${DATA_ROOT}/overlay" "${LOWER_VIEW_DIR}" "${UPPER_DIR}" "${WORK_DIR}" "${MERGED_DIR}"; do
        if [[ -L "${path}" || "$(readlink -m -- "${path}")" != "${path}" ]]; then
            error "开发 Overlay 路径不安全：${path}"
            exit 1
        fi
        if [[ -e "${path}" && ! -d "${path}" ]]; then
            error "开发 Overlay 路径不是目录：${path}"
            exit 1
        fi
    done
    for path in "${UPPER_DIR}" "${WORK_DIR}"; do
        if [[ -e "${path}" ]] && mountpoint -q "${path}"; then
            error "Overlay upper/work 不能是独立挂载点：${path}"
            exit 1
        fi
    done
}

assert_lower_view_mount() {
    local lower_pgdata="$1"
    if ! mountpoint -q "${LOWER_VIEW_DIR}"; then
        error '只读 lower view 尚未挂载'
        return 1
    fi
    if [[ "$(stat -Lc '%d:%i' "${lower_pgdata}")" != "$(stat -Lc '%d:%i' "${LOWER_VIEW_DIR}")" ]]; then
        error '只读 lower bind mount 与固定候选 inode 不一致'
        return 1
    fi
    if ! findmnt -n -o OPTIONS --target "${LOWER_VIEW_DIR}" | tr ',' '\n' | grep -qx 'ro'; then
        error '候选 lower bind mount 不是只读挂载'
        return 1
    fi
}

mount_lower_view() {
    local lower_pgdata="$1"
    install -d -o 0 -g 0 -m 0700 "${LOWER_VIEW_DIR}"
    if mountpoint -q "${LOWER_VIEW_DIR}"; then
        assert_lower_view_mount "${lower_pgdata}"
        return
    fi
    mount --bind "${lower_pgdata}" "${LOWER_VIEW_DIR}"
    mount -o remount,bind,ro "${LOWER_VIEW_DIR}"
    assert_lower_view_mount "${lower_pgdata}"
}

overlay_option() {
    local option_name="$1"
    findmnt -n -o OPTIONS --target "${MERGED_DIR}" \
        | tr ',' '\n' \
        | sed -n "s/^${option_name}=//p"
}

assert_overlay_mount() {
    if ! mountpoint -q "${MERGED_DIR}"; then
        error 'Overlay merged 目录尚未挂载'
        return 1
    fi
    if [[ "$(findmnt -n -o FSTYPE --target "${MERGED_DIR}")" != 'overlay' \
        || "$(overlay_option lowerdir)" != "${LOWER_VIEW_DIR}" \
        || "$(overlay_option upperdir)" != "${UPPER_DIR}" \
        || "$(overlay_option workdir)" != "${WORK_DIR}" ]]; then
        error '已有 merged mount 的类型或 lower/upper/work 身份不一致'
        return 1
    fi
}

mount_overlay() {
    local lower_pgdata="$1"
    assert_state_lower_identity
    assert_overlay_paths
    install -d -o 0 -g 0 -m 0750 "${DATA_ROOT}/overlay"
    install -d -o 0 -g 0 -m 0700 "${LOWER_VIEW_DIR}" "${UPPER_DIR}" "${WORK_DIR}" "${MERGED_DIR}"
    mount_lower_view "${lower_pgdata}"
    if mountpoint -q "${MERGED_DIR}"; then
        assert_overlay_mount
        return
    fi
    mount -t overlay overlay \
        -o "lowerdir=${LOWER_VIEW_DIR},upperdir=${UPPER_DIR},workdir=${WORK_DIR}" \
        "${MERGED_DIR}"
    assert_overlay_mount
}

port_is_busy() {
    ss -H -ltn "sport = :${FIXED_PORT}" | grep -q .
}

container_exists() {
    docker inspect "${CONTAINER_NAME}" >/dev/null 2>&1
}

container_identity_matches() {
    local inspect_json
    inspect_json="$(docker inspect "${CONTAINER_NAME}" 2>/dev/null)" || return 1
    jq -e \
        --arg role "${CONTAINER_ROLE_LABEL}" \
        --arg instance "${CONTAINER_INSTANCE_LABEL}" \
        --arg state "${CONTAINER_STATE_LABEL}" \
        --arg release_id "$(state_value '.release_id')" \
        --arg system_identifier "$(state_value '.system_identifier')" \
        --arg checkpoint_key "$(state_value '.checkpoint_key')" \
        --arg image_id "$(state_value '.image_id')" \
        --arg merged_dir "${MERGED_DIR}" \
        --arg destination "${CONTAINER_DATA_DESTINATION}" \
        --arg port "${FIXED_PORT}" \
        '
        length == 1
        and .[0].Image == $image_id
        and .[0].Config.Labels["io.domeye.core.role"] == $role
        and .[0].Config.Labels["io.domeye.core.instance"] == $instance
        and .[0].Config.Labels["io.domeye.core.state"] == $state
        and .[0].Config.Labels["io.domeye.core.release-id"] == $release_id
        and .[0].Config.Labels["io.domeye.core.system-identifier"] == $system_identifier
        and .[0].Config.Labels["io.domeye.core.checkpoint-key"] == $checkpoint_key
        and (
            [.[0].Mounts[] | select(.Destination == $destination)] as $mounts
            | ($mounts | length) == 1
            and $mounts[0].Source == $merged_dir
            and $mounts[0].RW == true
        )
        and (
            .[0].HostConfig.PortBindings["5432/tcp"] as $bindings
            | ($bindings | length) == 1
            and $bindings[0].HostIp == "127.0.0.1"
            and $bindings[0].HostPort == $port
        )
        ' <<< "${inspect_json}" >/dev/null
}

assert_container_identity() {
    if ! container_identity_matches; then
        error "同名容器不属于当前开发数据库，拒绝复用、停止或执行 SQL：${CONTAINER_NAME}"
        return 1
    fi
}

wait_container() {
    local attempt
    for (( attempt = 1; attempt <= 90; attempt++ )); do
        if docker exec "${CONTAINER_NAME}" pg_isready -q \
            -U "${DOMEYE_CORE_DB_ADMIN_USER}" \
            -d "${DOMEYE_CORE_DB_NAME}"; then
            local live_system_identifier
            live_system_identifier="$(docker exec "${CONTAINER_NAME}" \
                psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
                --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
                --dbname "${DOMEYE_CORE_DB_NAME}" \
                --command 'SELECT system_identifier::text FROM pg_control_system();')"
            if [[ "${live_system_identifier}" != "$(state_value '.system_identifier')" ]]; then
                error '运行中开发容器的 system identifier 与状态不一致'
                return 1
            fi
            return
        fi
        sleep 1
    done
    error '开发数据库 90 秒内未就绪'
    return 1
}

start_container() {
    assert_overlay_mount
    if container_exists; then
        assert_container_identity
        if [[ "$(docker inspect --format '{{.State.Running}}' "${CONTAINER_NAME}")" != 'true' ]]; then
            if port_is_busy; then
                error "固定开发数据库端口已占用：${FIXED_PORT}"
                return 1
            fi
            docker start "${CONTAINER_NAME}" >/dev/null
        fi
        wait_container
        return
    fi
    if port_is_busy; then
        error "固定开发数据库端口已占用：${FIXED_PORT}"
        return 1
    fi

    docker run --detach \
        --name "${CONTAINER_NAME}" \
        --label "io.domeye.core.role=${CONTAINER_ROLE_LABEL}" \
        --label "io.domeye.core.instance=${CONTAINER_INSTANCE_LABEL}" \
        --label "io.domeye.core.state=${CONTAINER_STATE_LABEL}" \
        --label "io.domeye.core.release-id=$(state_value '.release_id')" \
        --label "io.domeye.core.system-identifier=$(state_value '.system_identifier')" \
        --label "io.domeye.core.checkpoint-key=$(state_value '.checkpoint_key')" \
        --memory 8g \
        --shm-size 2g \
        --publish "127.0.0.1:${FIXED_PORT}:5432" \
        --volume "${MERGED_DIR}:${CONTAINER_DATA_DESTINATION}" \
        "$(state_value '.image_id')" \
        postgres \
        -c 'shared_buffers=1GB' \
        -c 'listen_addresses=*' \
        -c 'timescaledb.telemetry_level=off' \
        >/dev/null
    assert_container_identity
    wait_container
}

stop_owned_container() {
    if ! container_exists; then
        return 0
    fi
    assert_container_identity || return 2
    if [[ "$(docker inspect --format '{{.State.Running}}' "${CONTAINER_NAME}")" == 'true' ]]; then
        docker stop --time 30 "${CONTAINER_NAME}" >/dev/null
    fi
}

unmount_owned_overlay() {
    if mountpoint -q "${MERGED_DIR}"; then
        assert_overlay_mount
        umount "${MERGED_DIR}"
    fi
    if mountpoint -q "${LOWER_VIEW_DIR}"; then
        assert_lower_view_mount "$(state_value '.lower_pgdata')"
        umount "${LOWER_VIEW_DIR}"
    fi
}

psql_state_arguments() {
    printf '%s\n' \
        "release_id=$(state_value '.release_id')" \
        "system_identifier=$(state_value '.system_identifier')" \
        "checkpoint_key=$(state_value '.checkpoint_key')" \
        "prune_sql_sha256=$(state_value '.hashes.prune_sql')" \
        "inventory_sha256=$(state_value '.hashes.inventory')" \
        "reader_role=${DOMEYE_CORE_DB_READER_USER}" \
        "data_start=${DATA_START}" \
        "data_end_exclusive=${DATA_END_EXCLUSIVE}"
}

run_sql() {
    local sql_file="$1"
    local source_preflight="${2:-off}"
    assert_container_identity
    local -a set_arguments=()
    local argument
    while IFS= read -r argument; do
        set_arguments+=(--set "${argument}")
    done < <(psql_state_arguments)
    docker exec --interactive "${CONTAINER_NAME}" \
        psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --set "source_preflight=${source_preflight}" \
        "${set_arguments[@]}" \
        < "${sql_file}"
}

admin_scalar() {
    local sql="$1"
    assert_container_identity
    docker exec "${CONTAINER_NAME}" \
        psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --command "${sql}"
}

checkpoint_matches() {
    local relation checkpoint_key
    relation="$(admin_scalar "SELECT coalesce(to_regclass('domeye_dev.prune_checkpoint')::text, '');")" || return 1
    [[ "${relation}" == 'domeye_dev.prune_checkpoint' ]] || return 1
    checkpoint_key="$(admin_scalar 'SELECT checkpoint_key FROM domeye_dev.prune_checkpoint WHERE singleton;')" || return 1
    [[ "${checkpoint_key}" == "$(state_value '.checkpoint_key')" ]]
}

verify_database() {
    local verification
    verification="$(run_sql "${VERIFY_SQL}")" || return 1
    if ! jq -e \
        '.ok == true
         and .public_table_count == 37
         and .reader_table_count == 37
         and .detail_reference_type_count == 6
         and .feature_country_min != null
         and .feature_country_max != null' \
        <<< "${verification}" >/dev/null; then
        error '开发数据库验收输出无效'
        return 1
    fi
    printf '开发数据库验收通过：%s\n' "${verification}"
}

validate_source_before_prune() {
    local inventory="${EXPECTED_RELEASE_DIR}/database-inventory.json"
    require_regular_file "${inventory}" '数据库 inventory'
    if [[ "$(file_sha256 "${inventory}")" != "$(state_value '.hashes.inventory')" ]]; then
        error '首次裁剪前 release inventory 哈希与状态不一致'
        return 1
    fi

    local expected_tables actual_tables preflight
    expected_tables="$(jq -c \
        '[.tables[]
          | select((.schema_name // "public") == "public")
          | .table_name // .name]
         | sort' \
        "${inventory}")"
    actual_tables="$(admin_scalar \
        "SELECT coalesce(jsonb_agg(tablename ORDER BY tablename), '[]'::jsonb)::text FROM pg_tables WHERE schemaname = 'public';")"
    if ! jq -en \
        --argjson expected "${expected_tables}" \
        --argjson actual "${actual_tables}" \
        '$expected == $actual' >/dev/null; then
        error '首次裁剪前源候选 public 表集合与 release inventory 不一致'
        return 1
    fi

    preflight="$(run_sql "${VERIFY_SQL}" on)" || return 1
    if ! jq -e \
        '.ok == true
         and .mode == "source-preflight"
         and .public_table_count == .expected_table_count
         and .public_table_count == 109
         and .feature_country_min != null
         and .feature_country_max != null' \
        <<< "${preflight}" >/dev/null; then
        error '首次裁剪前源候选表集合或时间范围验收输出无效'
        return 1
    fi
    printf '源候选只读预检通过：%s\n' "${preflight}"
}

reconcile_and_verify() {
    local phase
    phase="$(state_value '.phase')"
    if [[ "${phase}" == 'preparing' ]]; then
        if checkpoint_matches; then
            transition_state preparing pruned
        else
            validate_source_before_prune
            run_sql "${PRUNE_SQL}"
            if ! checkpoint_matches; then
                error '裁剪 SQL 已返回，但持久检查点不存在或不匹配'
                return 1
            fi
            transition_state preparing pruned
        fi
        phase='pruned'
    fi
    if [[ "${phase}" == 'pruned' ]]; then
        verify_database
        record_verification pruned
    elif [[ "${phase}" == 'verified' ]]; then
        invalidate_verification
        verify_database
        record_verification pruned
    else
        error "不支持的开发数据库阶段：${phase}"
        return 1
    fi
}

prepare_database() {
    if (( $# < 2 || $# > 3 )); then
        error '用法：manage-dev-database.sh prepare <固定候选 PGDATA> <固定发布目录> [31627]'
        exit 2
    fi
    local lower_arg="${1%/}"
    local release_arg="${2%/}"
    local port="${3:-${FIXED_PORT}}"
    if [[ "${lower_arg}" != "${EXPECTED_LOWER_PGDATA}" \
        || "${release_arg}" != "${EXPECTED_RELEASE_DIR}" \
        || "${port}" != "${FIXED_PORT}" ]]; then
        error '候选 PGDATA、发布目录和端口均已固定，拒绝其他参数'
        exit 2
    fi

    assert_overlay_paths
    validate_release_and_candidate "${lower_arg}" "${release_arg}"
    if [[ -f "${STATE_FILE}" ]]; then
        assert_state_provenance
        if [[ "$(state_value '.hashes.release_manifest')" != "${VALIDATED_RELEASE_MANIFEST_SHA}" \
            || "$(state_value '.hashes.database_manifest')" != "${VALIDATED_DATABASE_MANIFEST_SHA}" \
            || "$(state_value '.hashes.inventory')" != "${VALIDATED_INVENTORY_SHA}" \
            || "$(state_value '.hashes.pg_version')" != "${VALIDATED_PG_VERSION_SHA}" \
            || "$(state_value '.hashes.pg_control')" != "${VALIDATED_PG_CONTROL_SHA}" \
            || "$(state_value '.system_identifier')" != "${VALIDATED_SYSTEM_IDENTIFIER}" \
            || "$(state_value '.image_id')" != "${VALIDATED_IMAGE_ID}" \
            || "$(state_value '.checkpoint_key')" != "${VALIDATED_CHECKPOINT_KEY}" ]]; then
            error '现有状态与固定候选或发布证据不一致，拒绝覆盖'
            exit 1
        fi
    else
        if container_exists; then
            error "状态文件不存在但同名容器已存在，拒绝接管：${CONTAINER_NAME}"
            exit 1
        fi
        if [[ -e "${UPPER_DIR}" || -L "${UPPER_DIR}" \
            || -e "${WORK_DIR}" || -L "${WORK_DIR}" ]] \
            || mountpoint -q "${MERGED_DIR}" \
            || mountpoint -q "${LOWER_VIEW_DIR}"; then
            error 'Overlay 数据或挂载已存在但没有状态文件，必须人工复核'
            exit 1
        fi
        write_initial_state "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    fi

    local completed=false
    cleanup_prepare() {
        local exit_code=$?
        if [[ "${completed}" != true ]]; then
            local may_unmount=true
            if container_exists; then
                if ! stop_owned_container; then
                    may_unmount=false
                fi
            fi
            if [[ "${may_unmount}" == true ]]; then
                unmount_owned_overlay || true
            fi
            printf '开发库准备未完成；状态、upperdir 与候选占用锁均已保留，请使用完全相同的命令续跑。\n' >&2
        fi
        rmdir "${LOCK_DIR}" 2>/dev/null || true
        return "${exit_code}"
    }
    trap cleanup_prepare EXIT

    mount_overlay "$(state_value '.lower_pgdata')"
    start_container
    reconcile_and_verify
    completed=true
    trap - EXIT
    rmdir "${LOCK_DIR}" 2>/dev/null || true
    printf '开发数据库已就绪：127.0.0.1:%s（%s <= t < %s）\n' \
        "${FIXED_PORT}" "${DATA_START}" "${DATA_END_EXCLUSIVE}"
    printf 'Overlay 新增空间：'
    du -sh "${UPPER_DIR}" | awk '{print $1}'
}

start_database() {
    if (( $# != 0 )); then
        error 'start 不接受额外参数'
        exit 2
    fi
    assert_state_lower_identity
    if [[ "$(state_value '.phase')" != 'verified' ]]; then
        error '开发数据库尚未 verified，需使用固定 prepare 命令从检查点续跑'
        exit 1
    fi
    mount_overlay "$(state_value '.lower_pgdata')"
    start_container
    invalidate_verification
    verify_database
    record_verification pruned
    printf '开发数据库已启动：127.0.0.1:%s\n' "${FIXED_PORT}"
}

verify_database_action() {
    if (( $# != 0 )); then
        error 'verify 不接受额外参数'
        exit 2
    fi
    assert_state_lower_identity
    mount_overlay "$(state_value '.lower_pgdata')"
    start_container
    local phase
    phase="$(state_value '.phase')"
    if [[ "${phase}" == 'preparing' ]]; then
        if ! checkpoint_matches; then
            error '数据库内尚无裁剪成功检查点，请使用固定 prepare 命令续跑'
            exit 1
        fi
        transition_state preparing pruned
        phase='pruned'
    fi
    if [[ "${phase}" == 'verified' ]]; then
        invalidate_verification
        phase='pruned'
    fi
    verify_database
    if [[ "${phase}" == 'pruned' ]]; then
        record_verification pruned
    else
        error "无法验收未知状态：${phase}"
        exit 1
    fi
}

stop_database() {
    if (( $# != 0 )); then
        error 'stop 不接受额外参数'
        exit 2
    fi
    validate_state_structure
    if container_exists; then
        stop_owned_container
    fi
    unmount_owned_overlay
    printf '开发数据库已停止；upperdir、状态和候选占用锁均保留。\n'
}

status_database() {
    if (( $# != 0 )); then
        error 'status 不接受额外参数'
        exit 2
    fi
    if [[ ! -f "${STATE_FILE}" ]]; then
        printf '开发数据库尚未准备。\n'
        return 1
    fi
    assert_state_provenance
    printf '阶段：%s\n' "$(state_value '.phase')"
    printf '发布：%s\n' "$(state_value '.release_id')"
    printf 'system identifier：%s\n' "$(state_value '.system_identifier')"
    printf '端口：127.0.0.1:%s\n' "${FIXED_PORT}"
    printf '窗口：%s <= t < %s\n' "${DATA_START}" "${DATA_END_EXCLUSIVE}"
    printf '候选占用锁：%s\n' "$(state_value '.guard_dir')"
    if container_exists; then
        assert_container_identity
        printf '容器：%s\n' "$(docker inspect --format '{{.State.Status}}' "${CONTAINER_NAME}")"
    else
        printf '容器：stopped\n'
    fi
    if [[ -d "${UPPER_DIR}" ]]; then
        printf 'Overlay 新增空间：'
        du -sh "${UPPER_DIR}" | awk '{print $1}'
    fi
}

main() {
    require_root
    for command_name in \
        awk chmod date dirname docker du findmnt grep install jq mkdir mount mountpoint \
        mv readlink rmdir screen sed sha256sum sleep ss stat tr umount; do
        require_command "${command_name}"
    done
    require_regular_file "${PRUNE_SQL}" '开发库裁剪 SQL'
    require_regular_file "${VERIFY_SQL}" '开发库验收 SQL'
    assert_fixed_runtime_paths
    if (( $# < 1 )); then
        error '用法：manage-dev-database.sh <prepare|start|stop|verify|status> ...'
        exit 2
    fi
    local action="$1"
    shift
    lock
    load_database_config
    case "${action}" in
        prepare) assert_dev_api_not_running; prepare_database "$@" ;;
        start) assert_dev_api_not_running; start_database "$@" ;;
        stop) assert_dev_api_not_running; stop_database "$@" ;;
        verify) assert_dev_api_not_running; verify_database_action "$@" ;;
        status) status_database "$@" ;;
        *)
            error "未知操作：${action}"
            exit 2
            ;;
    esac
}

main "$@"
