#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SQL_DIR="${SCRIPT_DIR}/sql"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"
# shellcheck source=../lib/database-common.sh
source "${SCRIPT_DIR}/../lib/database-common.sh"

if (( $# < 1 || $# > 2 )); then
    printf '用法：%s <发布目录> [数据库配置]\n' "${0##*/}" >&2
    exit 2
fi

readonly RELEASE_DIR="${1%/}"
readonly DATABASE_ENV_FILE="${2:-${DOMEYE_CORE_DATABASE_CONFIG_DEFAULT}}"
readonly RELEASE_MANIFEST="${RELEASE_DIR}/${DOMEYE_CORE_RELEASE_MANIFEST}"
readonly DATABASE_MANIFEST="${RELEASE_DIR}/${DOMEYE_CORE_DATABASE_MANIFEST}"
readonly DATABASE_ARCHIVE="${RELEASE_DIR}/${DOMEYE_CORE_DATABASE_ARCHIVE}"
readonly IMAGE_ARCHIVE="${RELEASE_DIR}/${DOMEYE_CORE_IMAGE_ARCHIVE}"

for command_name in docker install jq mktemp readlink sha256sum tar zstd; do
    domeye_artifact_require_command "${command_name}"
done
"${SCRIPT_DIR}/../artifacts/verify-release.sh" "${RELEASE_DIR}"
domeye_database_load_env "${DATABASE_ENV_FILE}"
domeye_database_validate_config

readonly RELEASE_ID="$(jq -r '.release_id' "${RELEASE_MANIFEST}")"
domeye_artifact_validate_release_id "${RELEASE_ID}"
readonly RELEASE_DATA_ROOT="${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/${RELEASE_ID}"
readonly DATA_DIR="${RELEASE_DATA_ROOT}/postgres"
readonly STATE_FILE="${RELEASE_DATA_ROOT}/restore-state.json"
readonly CHECKPOINT_FILE="${RELEASE_DATA_ROOT}/restore-checkpoint.json"
readonly ARCHIVE_COMPLETE_FILE="${RELEASE_DATA_ROOT}/restore-archive-complete.tsv"
readonly REVALIDATION_MARKER="${RELEASE_DATA_ROOT}/restore-revalidation-in-progress"
readonly RESTORE_LOCK_DIR="${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/.${RELEASE_ID}.restore.lock"
readonly EXPECTED_DUMP_SHA="$(jq -r '.archive.sha256' "${DATABASE_MANIFEST}")"

install -d -m 0750 "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}"
if [[ -L "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}" ]]; then
    domeye_artifact_error "数据库发布根目录不能是软链接：${DOMEYE_CORE_DATABASE_RELEASE_ROOT}"
    exit 1
fi
if ! mkdir "${RESTORE_LOCK_DIR}" 2>/dev/null; then
    domeye_artifact_error "同一 release-id 已有数据库恢复或复验在运行：${RELEASE_ID}"
    exit 1
fi
release_restore_lock_cleanup() {
    rmdir "${RESTORE_LOCK_DIR}" 2>/dev/null || true
}
trap release_restore_lock_cleanup EXIT

readonly EXPECTED_IMAGE_REF="$(jq -r '.image.ref' "${DATABASE_MANIFEST}")"
readonly EXPECTED_IMAGE_ID="$(jq -r '.image.id' "${DATABASE_MANIFEST}")"
if [[ "${DOMEYE_CORE_DB_IMAGE}" != "${EXPECTED_IMAGE_REF}" ]]; then
    domeye_artifact_error "数据库配置镜像与制品不一致：${DOMEYE_CORE_DB_IMAGE} != ${EXPECTED_IMAGE_REF}"
    exit 1
fi
image_archive_config="$(zstd --quiet --decompress --stdout "${IMAGE_ARCHIVE}" \
    | tar --extract --to-stdout --file=- manifest.json \
    | jq -er 'if length == 1 then .[0].Config else error("image count") end')"
if [[ ! "${EXPECTED_IMAGE_ID}" =~ ^sha256:[0-9a-f]{64}$ \
    || "${image_archive_config}" != "${EXPECTED_IMAGE_ID#sha256:}.json" ]]; then
    domeye_artifact_error '数据库镜像归档与发布清单中的 image ID 不一致'
    exit 1
fi
zstd --quiet --decompress --stdout "${IMAGE_ARCHIVE}" | docker image load >/dev/null
if [[ "$(docker image inspect --format '{{.Id}}' "${EXPECTED_IMAGE_ID}" 2>/dev/null || true)" != "${EXPECTED_IMAGE_ID}" ]]; then
    domeye_artifact_error '加载后的数据库镜像 ID 与发布清单不一致'
    exit 1
fi
DOMEYE_CORE_DB_IMAGE_RUNTIME="${EXPECTED_IMAGE_ID}"

reuse_existing=false
reuse_verified=false
resume_archive_complete=false
archive_restored_at=''
archive_post_restore_complete=true
if [[ -L "${RELEASE_DATA_ROOT}" ]]; then
    domeye_artifact_error "数据库发布目录不能是软链接：${RELEASE_DATA_ROOT}"
    exit 1
fi
for restore_state_path in \
    "${STATE_FILE}" \
    "${CHECKPOINT_FILE}" \
    "${ARCHIVE_COMPLETE_FILE}" \
    "${REVALIDATION_MARKER}"; do
    if [[ -e "${restore_state_path}" || -L "${restore_state_path}" ]]; then
        if [[ ! -f "${restore_state_path}" || -L "${restore_state_path}" ]]; then
            domeye_artifact_error "恢复状态路径不是普通文件：${restore_state_path}"
            exit 1
        fi
    fi
done
validate_restore_checkpoint() {
    domeye_artifact_json_file "${CHECKPOINT_FILE}" || return 1
    jq -e \
        --arg release_id "${RELEASE_ID}" \
        --arg database_sha "${EXPECTED_DUMP_SHA}" \
        --arg image_id "${EXPECTED_IMAGE_ID}" \
        '.schema_version == 1
         and .phase == "restored_unverified"
         and .release_id == $release_id
         and .database_sha256 == $database_sha
         and .image_id == $image_id
         and (.restored_at | type) == "string"
         and (.system_identifier | type) == "string"
         and (.system_identifier | test("^[0-9]+$"))' \
        "${CHECKPOINT_FILE}" >/dev/null
}

archive_marker_present=false
if [[ -f "${ARCHIVE_COMPLETE_FILE}" ]]; then
    mapfile -t archive_marker_lines < "${ARCHIVE_COMPLETE_FILE}"
    if (( ${#archive_marker_lines[@]} != 6 )) \
        || [[ "${archive_marker_lines[0]}" != 'phase=archive_complete' \
        || "${archive_marker_lines[1]}" != "release_id=${RELEASE_ID}" \
        || "${archive_marker_lines[2]}" != "database_sha256=${EXPECTED_DUMP_SHA}" \
        || "${archive_marker_lines[3]}" != "image_id=${EXPECTED_IMAGE_ID}" \
        || ! "${archive_marker_lines[4]}" =~ ^restored_at=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ \
        || ! "${archive_marker_lines[5]}" =~ ^post_restore_complete=(true|false)$ ]]; then
        domeye_artifact_error "完整恢复标记与制品不一致：${RELEASE_DATA_ROOT}"
        exit 1
    fi
    archive_restored_at="${archive_marker_lines[4]#restored_at=}"
    archive_post_restore_complete="${archive_marker_lines[5]#post_restore_complete=}"
    archive_marker_present=true
fi

if [[ -f "${STATE_FILE}" && -f "${DATA_DIR}/PG_VERSION" ]]; then
    domeye_artifact_json_file "${STATE_FILE}"
    if [[ -L "${STATE_FILE}" || -L "${DATA_DIR}" ]] \
        || ! jq -e \
            --arg release_id "${RELEASE_ID}" \
            --arg database_sha "${EXPECTED_DUMP_SHA}" \
            --arg image_id "${EXPECTED_IMAGE_ID}" \
            '.schema_version == 1
             and .phase == "verified"
             and .release_id == $release_id
             and .database_sha256 == $database_sha
             and .image_id == $image_id
             and (.system_identifier | type) == "string"
             and (.system_identifier | test("^[0-9]+$"))' \
            "${STATE_FILE}" >/dev/null; then
        domeye_artifact_error "已有同名恢复目录，但状态与制品不一致：${RELEASE_DATA_ROOT}"
        exit 1
    fi
    if [[ -f "${CHECKPOINT_FILE}" ]]; then
        if ! validate_restore_checkpoint \
            || [[ "$(jq -r '.system_identifier' "${CHECKPOINT_FILE}")" != "$(jq -r '.system_identifier' "${STATE_FILE}")" \
            || "$(jq -r '.restored_at' "${CHECKPOINT_FILE}")" != "$(jq -r '.restored_at' "${STATE_FILE}")" ]]; then
            domeye_artifact_error 'verified 状态与残留恢复检查点不一致，必须人工复核'
            exit 1
        fi
        rm -f -- "${CHECKPOINT_FILE}"
    fi
    if [[ "${archive_marker_present}" == true ]]; then
        if [[ "${archive_post_restore_complete}" != true \
            || "${archive_restored_at}" != "$(jq -r '.restored_at' "${STATE_FILE}")" ]]; then
            domeye_artifact_error 'verified 状态与残留完整恢复标记不一致，必须人工复核'
            exit 1
        fi
        rm -f -- "${ARCHIVE_COMPLETE_FILE}"
        archive_marker_present=false
    fi
    reuse_existing=true
    reuse_verified=true
elif [[ -f "${CHECKPOINT_FILE}" && -f "${DATA_DIR}/PG_VERSION" ]]; then
    if [[ -L "${CHECKPOINT_FILE}" || -L "${DATA_DIR}" ]]; then
        domeye_artifact_error "待校验恢复检查点包含软链接：${RELEASE_DATA_ROOT}"
        exit 1
    fi
    if ! validate_restore_checkpoint; then
        domeye_artifact_error "待校验恢复检查点与制品不一致：${RELEASE_DATA_ROOT}"
        exit 1
    fi
    if [[ "${archive_marker_present}" == true ]]; then
        if [[ "${archive_post_restore_complete}" != true \
            || "${archive_restored_at}" != "$(jq -r '.restored_at' "${CHECKPOINT_FILE}")" ]]; then
            domeye_artifact_error '恢复检查点与残留完整恢复标记不一致，必须人工复核'
            exit 1
        fi
        rm -f -- "${ARCHIVE_COMPLETE_FILE}"
        archive_marker_present=false
    fi
    reuse_existing=true
elif [[ "${archive_marker_present}" == true && -f "${DATA_DIR}/PG_VERSION" ]]; then
    if [[ -L "${ARCHIVE_COMPLETE_FILE}" || -L "${DATA_DIR}" || -L "${RELEASE_DATA_ROOT}" ]]; then
        domeye_artifact_error "完整恢复标记包含软链接：${RELEASE_DATA_ROOT}"
        exit 1
    fi
    reuse_existing=true
    resume_archive_complete=true
elif [[ -e "${RELEASE_DATA_ROOT}" || -L "${RELEASE_DATA_ROOT}" ]]; then
    domeye_artifact_error "候选恢复目录已存在且不完整，拒绝覆盖：${RELEASE_DATA_ROOT}"
    exit 1
fi

if [[ "${reuse_existing}" == true ]]; then
    active_target=''
    if [[ -L "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}" ]]; then
        active_target="$(readlink -f "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}")"
    fi
    if [[ "${active_target}" == "$(readlink -f "${DATA_DIR}")" ]]; then
        domeye_artifact_error '该发布已经是活动 PGDATA，拒绝以候选容器再次挂载；请使用 status.sh 验证生产实例'
        exit 1
    fi
    while read -r running_container; do
        [[ -n "${running_container}" ]] || continue
        mounted_source="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Source}}{{end}}{{end}}' "${running_container}")"
        if [[ -n "${mounted_source}" && "$(readlink -f "${mounted_source}")" == "$(readlink -f "${DATA_DIR}")" ]]; then
            domeye_artifact_error "候选 PGDATA 正被运行中的容器挂载，拒绝双开：${running_container}"
            exit 1
        fi
    done < <(docker ps --quiet)
fi

container_suffix="${RELEASE_ID//[^a-zA-Z0-9]/_}"
readonly CANDIDATE_CONTAINER="domeye_core_restore_${container_suffix}_$$"
restore_complete=false
restore_archive_finished="${reuse_existing}"
release_root_owned=false
work_dir=''
cleanup() {
    local exit_code=$?
    if [[ "${DOMEYE_DATABASE_ARCHIVE_RESTORED:-false}" == true ]]; then
        restore_archive_finished=true
    fi
    domeye_database_remove_candidate_container "${CANDIDATE_CONTAINER}" || true
    if [[ "${release_root_owned}" == true \
        && "${restore_archive_finished}" != true \
        && "${RELEASE_DATA_ROOT}" == "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/${RELEASE_ID}" \
        && -d "${RELEASE_DATA_ROOT}" ]]; then
        rm -rf -- "${RELEASE_DATA_ROOT}"
    elif [[ "${restore_complete}" != true && "${restore_archive_finished}" == true ]]; then
        if [[ -f "${CHECKPOINT_FILE}" || -f "${ARCHIVE_COMPLETE_FILE}" \
            || -f "${REVALIDATION_MARKER}" ]]; then
            printf '恢复归档已完成，后置门禁失败；候选 PGDATA 已保留，可直接续跑：%s\n' \
                "${RELEASE_DATA_ROOT}" >&2
        else
            printf '恢复归档已完成但续跑标记未落盘；候选 PGDATA 已保留，必须先人工复核：%s\n' \
                "${RELEASE_DATA_ROOT}" >&2
        fi
    fi
    if [[ -n "${work_dir}" && -d "${work_dir}" ]]; then
        rm -rf -- "${work_dir}"
    fi
    rmdir "${RESTORE_LOCK_DIR}" 2>/dev/null || true
    return "${exit_code}"
}
trap cleanup EXIT

if [[ "${reuse_verified}" == true ]]; then
    if [[ -e "${REVALIDATION_MARKER}" || -L "${REVALIDATION_MARKER}" ]]; then
        if [[ ! -f "${REVALIDATION_MARKER}" || -L "${REVALIDATION_MARKER}" ]]; then
            domeye_artifact_error "恢复复验标记不是普通文件：${REVALIDATION_MARKER}"
            exit 1
        fi
    else
        install -m 0600 /dev/null "${REVALIDATION_MARKER}"
    fi
elif [[ -e "${REVALIDATION_MARKER}" || -L "${REVALIDATION_MARKER}" ]]; then
    domeye_artifact_error '未验证候选库意外存在复验标记，必须人工复核'
    exit 1
fi

if [[ "${reuse_existing}" == true ]]; then
    docker run --detach \
        --name "${CANDIDATE_CONTAINER}" \
        --memory "${DOMEYE_CORE_DATABASE_MEMORY}" \
        --shm-size 4g \
        --env "POSTGRES_DB=${DOMEYE_CORE_DB_NAME}" \
        --env "POSTGRES_USER=${DOMEYE_CORE_DB_ADMIN_USER}" \
        --volume "${DATA_DIR}:/var/lib/postgresql/data" \
        "${EXPECTED_IMAGE_ID}" \
        postgres \
        -c "shared_buffers=${DOMEYE_CORE_DATABASE_SHARED_BUFFERS}" \
        -c 'listen_addresses=*' \
        -c 'timescaledb.telemetry_level=off' \
        >/dev/null
    domeye_database_wait_container "${CANDIDATE_CONTAINER}"
else
    install -d -m 0750 "${RELEASE_DATA_ROOT}"
    release_root_owned=true
    domeye_database_start_candidate "${CANDIDATE_CONTAINER}" "${DATA_DIR}"
    if ! domeye_database_restore_archive "${CANDIDATE_CONTAINER}" "${DATABASE_ARCHIVE}"; then
        if [[ "${DOMEYE_DATABASE_ARCHIVE_RESTORED:-false}" != true ]]; then
            domeye_artifact_error '数据库归档恢复失败，候选库不可作为可信续跑点'
            exit 1
        fi
        archive_post_restore_complete=false
    fi
    restore_archive_finished=true
    archive_restored_at="$(domeye_artifact_iso_utc_now)"
    archive_complete_tmp="${RELEASE_DATA_ROOT}/.restore-archive-complete.tmp.$$"
    {
        printf 'phase=archive_complete\n'
        printf 'release_id=%s\n' "${RELEASE_ID}"
        printf 'database_sha256=%s\n' "${EXPECTED_DUMP_SHA}"
        printf 'image_id=%s\n' "${EXPECTED_IMAGE_ID}"
        printf 'restored_at=%s\n' "${archive_restored_at}"
        printf 'post_restore_complete=%s\n' "${archive_post_restore_complete}"
    } > "${archive_complete_tmp}"
    chmod 0600 "${archive_complete_tmp}"
    mv -T -- "${archive_complete_tmp}" "${ARCHIVE_COMPLETE_FILE}"
    resume_archive_complete=true
fi

if [[ "${resume_archive_complete}" == true ]]; then
    if [[ "${archive_post_restore_complete}" != true ]]; then
        if ! domeye_database_psql \
            "${CANDIDATE_CONTAINER}" \
            --command 'SELECT timescaledb_post_restore();'; then
            domeye_artifact_error 'TimescaleDB post_restore 仍失败；已保留完整恢复候选库供续跑'
            exit 1
        fi
        archive_post_restore_complete=true
        archive_complete_tmp="${RELEASE_DATA_ROOT}/.restore-archive-complete.tmp.$$"
        {
            printf 'phase=archive_complete\n'
            printf 'release_id=%s\n' "${RELEASE_ID}"
            printf 'database_sha256=%s\n' "${EXPECTED_DUMP_SHA}"
            printf 'image_id=%s\n' "${EXPECTED_IMAGE_ID}"
            printf 'restored_at=%s\n' "${archive_restored_at}"
            printf 'post_restore_complete=true\n'
        } > "${archive_complete_tmp}"
        chmod 0600 "${archive_complete_tmp}"
        mv -T -- "${archive_complete_tmp}" "${ARCHIVE_COMPLETE_FILE}"
    fi
    restored_system_identifier="$(domeye_database_psql \
        "${CANDIDATE_CONTAINER}" \
        --quiet --no-align --tuples-only \
        --command 'SELECT system_identifier FROM pg_control_system();')"
    if [[ ! "${restored_system_identifier}" =~ ^[0-9]+$ ]]; then
        domeye_artifact_error '完整恢复已结束，但无法取得 PostgreSQL system identifier；已保留候选库供人工复核'
        exit 1
    fi
    checkpoint_tmp="${RELEASE_DATA_ROOT}/.restore-checkpoint.tmp.$$"
    jq -n \
        --argjson schema_version 1 \
        --arg phase 'restored_unverified' \
        --arg release_id "${RELEASE_ID}" \
        --arg restored_at "${archive_restored_at}" \
        --arg database_sha256 "${EXPECTED_DUMP_SHA}" \
        --arg image_id "${EXPECTED_IMAGE_ID}" \
        --arg system_identifier "${restored_system_identifier}" \
        '{
          schema_version: $schema_version,
          phase: $phase,
          release_id: $release_id,
          restored_at: $restored_at,
          database_sha256: $database_sha256,
          image_id: $image_id,
          system_identifier: $system_identifier
        }' > "${checkpoint_tmp}"
    chmod 0600 "${checkpoint_tmp}"
    mv -T -- "${checkpoint_tmp}" "${CHECKPOINT_FILE}"
    rm -f -- "${ARCHIVE_COMPLETE_FILE}"
fi

current_system_identifier="$(domeye_database_psql \
    "${CANDIDATE_CONTAINER}" \
    --quiet --no-align --tuples-only \
    --command 'SELECT system_identifier FROM pg_control_system();')"
if [[ "${reuse_verified}" == true ]]; then
    expected_system_identifier="$(jq -r '.system_identifier' "${STATE_FILE}")"
else
    expected_system_identifier="$(jq -r '.system_identifier' "${CHECKPOINT_FILE}")"
fi
if [[ ! "${current_system_identifier}" =~ ^[0-9]+$ \
    || "${current_system_identifier}" != "${expected_system_identifier}" ]]; then
    domeye_artifact_error '候选 PGDATA 的 PostgreSQL system identifier 与恢复状态不一致'
    exit 1
fi
domeye_database_apply_reader "${CANDIDATE_CONTAINER}" "${SQL_DIR}/create-reader.sql"

postgres_version="$(domeye_database_psql "${CANDIDATE_CONTAINER}" --quiet --no-align --tuples-only --command 'SHOW server_version;')"
timescaledb_version="$(domeye_database_psql "${CANDIDATE_CONTAINER}" --quiet --no-align --tuples-only --command "SELECT extversion FROM pg_extension WHERE extname='timescaledb';")"
if [[ "${postgres_version}" != "$(jq -r '.versions.postgresql' "${DATABASE_MANIFEST}")" || "${timescaledb_version}" != "$(jq -r '.versions.timescaledb' "${DATABASE_MANIFEST}")" ]]; then
    domeye_artifact_error '恢复后的 PostgreSQL 或 TimescaleDB 版本不符合发布清单'
    exit 1
fi

install -d -m 0750 "${DOMEYE_CORE_DATABASE_WORK_ROOT}"
work_dir="$(mktemp -d "${DOMEYE_CORE_DATABASE_WORK_ROOT}/restore-check-${RELEASE_ID}.XXXXXX")"
snapshot_time="$(jq -r '.snapshot_time' "${DATABASE_MANIFEST}")"
snapshot_local="$(jq -r '.snapshot_local' "${DATABASE_MANIFEST}")"
snapshot_month="${snapshot_local:0:7}"
snapshot_month="${snapshot_month//-/}"
readonly snapshot_time snapshot_local snapshot_month

readonly RESTORED_INTEGRITY="${work_dir}/database-integrity.json"
docker exec --interactive \
    "${CANDIDATE_CONTAINER}" \
    psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --set "data_start=${DOMEYE_CORE_DATA_START}" \
        --set "snapshot_time=${snapshot_time}" \
        --set "snapshot_month=${snapshot_month}" \
        < "${SQL_DIR}/validate-integrity.sql" \
        > "${RESTORED_INTEGRITY}"
domeye_artifact_json_file "${RESTORED_INTEGRITY}"
jq --slurpfile published "${RELEASE_DIR}/database-inventory.json" \
    '.detail_references.discarded_malformed_event_rows = $published[0].integrity.detail_references.discarded_malformed_event_rows' \
    "${RESTORED_INTEGRITY}" > "${RESTORED_INTEGRITY}.merged"
mv -- "${RESTORED_INTEGRITY}.merged" "${RESTORED_INTEGRITY}"
if ! jq -e \
    '.table_whitelist.ok == true
     and .detail_references.ok == true
     and .detail_references.malformed_count == 0
     and .detail_references.orphan_count == 0' \
    "${RESTORED_INTEGRITY}" >/dev/null; then
    jq '.table_whitelist, .detail_references' "${RESTORED_INTEGRITY}" >&2
    domeye_artifact_error '恢复库白名单或事件详情引用完整性门禁失败'
    exit 1
fi

readonly RESTORED_INVENTORY_RAW="${work_dir}/database-inventory-raw.json"
readonly RESTORED_INVENTORY="${work_dir}/database-inventory.json"
docker exec --interactive \
    "${CANDIDATE_CONTAINER}" \
    psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --set "data_start=${DOMEYE_CORE_DATA_START}" \
        --set "snapshot_time=${snapshot_time}" \
        < "${SQL_DIR}/inventory.sql" \
        > "${RESTORED_INVENTORY_RAW}"
domeye_artifact_json_file "${RESTORED_INVENTORY_RAW}"
jq -s '.[0] + {integrity: .[1]}' "${RESTORED_INVENTORY_RAW}" "${RESTORED_INTEGRITY}" > "${RESTORED_INVENTORY}"
domeye_artifact_json_file "${RESTORED_INVENTORY}"

if ! diff -u \
    <(jq -S '{tables, integrity}' "${RELEASE_DIR}/database-inventory.json") \
    <(jq -S '{tables, integrity}' "${RESTORED_INVENTORY}") \
    >/dev/null; then
    domeye_artifact_error '恢复后的表行数、时间范围或 schema hash 与制品不一致'
    exit 1
fi

# PostgreSQL 12 的单个 psql --command 只保留最后一个结果集，状态与计数必须分开查询。
reader_readonly="$(docker exec \
    "${CANDIDATE_CONTAINER}" \
    psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_READER_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --command 'SHOW transaction_read_only;')"
reader_count="$(docker exec \
    "${CANDIDATE_CONTAINER}" \
    psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_READER_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --command 'SELECT count(*) FROM public.feature_country;')"
if [[ "${reader_readonly}" != 'on' || ! "${reader_count}" =~ ^[1-9][0-9]*$ ]]; then
    domeye_artifact_error '恢复后的只读账号不能读取 feature_country 超表'
    exit 1
fi

state_tmp="${RELEASE_DATA_ROOT}/.restore-state.tmp.$$"
jq -n \
    --argjson schema_version 1 \
    --arg phase 'verified' \
    --arg release_id "${RELEASE_ID}" \
    --arg restored_at "$(if [[ -f "${CHECKPOINT_FILE}" ]]; then jq -r '.restored_at' "${CHECKPOINT_FILE}"; else jq -r '.restored_at' "${STATE_FILE}"; fi)" \
    --arg verified_at "$(domeye_artifact_iso_utc_now)" \
    --arg database_sha256 "${EXPECTED_DUMP_SHA}" \
    --arg image_id "${EXPECTED_IMAGE_ID}" \
    --arg system_identifier "${current_system_identifier}" \
    '{
      schema_version: $schema_version,
      phase: $phase,
      release_id: $release_id,
      restored_at: $restored_at,
      verified_at: $verified_at,
      database_sha256: $database_sha256,
      image_id: $image_id,
      system_identifier: $system_identifier
    }' > "${state_tmp}"
chmod 0600 "${state_tmp}"
mv -T -- "${state_tmp}" "${STATE_FILE}"
if [[ -e "${CHECKPOINT_FILE}" || -L "${CHECKPOINT_FILE}" ]]; then
    if [[ ! -f "${CHECKPOINT_FILE}" || -L "${CHECKPOINT_FILE}" ]]; then
        domeye_artifact_error "恢复检查点不是普通文件，拒绝清理：${CHECKPOINT_FILE}"
        exit 1
    fi
    rm -f -- "${CHECKPOINT_FILE}"
fi
if [[ -e "${REVALIDATION_MARKER}" || -L "${REVALIDATION_MARKER}" ]]; then
    if [[ ! -f "${REVALIDATION_MARKER}" || -L "${REVALIDATION_MARKER}" ]]; then
        domeye_artifact_error "恢复复验标记不是普通文件，拒绝清理：${REVALIDATION_MARKER}"
        exit 1
    fi
    rm -f -- "${REVALIDATION_MARKER}"
fi
rm -rf -- "${work_dir}"
restore_complete=true
if [[ "${reuse_existing}" == true ]]; then
    printf '已有数据库发布的镜像、inventory 和只读查询复验完成：%s\n' "${RELEASE_DATA_ROOT}"
else
    printf '独立数据库候选发布恢复并校验完成：%s\n' "${RELEASE_DATA_ROOT}"
fi
