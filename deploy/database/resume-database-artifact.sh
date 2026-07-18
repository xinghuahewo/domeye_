#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SQL_DIR="${SCRIPT_DIR}/sql"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"
# shellcheck source=../lib/database-common.sh
source "${SCRIPT_DIR}/../lib/database-common.sh"

usage() {
    printf '用法：%s <独立库配置> <release-id> <候选构建目录> [制品根目录]\n' "${0##*/}" >&2
}

if (( $# < 3 || $# > 4 )); then
    usage
    exit 2
fi

readonly DATABASE_ENV_FILE="$1"
readonly RELEASE_ID="$2"
readonly CANDIDATE_ROOT="${3%/}"
readonly ARTIFACT_ROOT="${4:-${DOMEYE_CORE_DEFAULT_ARTIFACT_ROOT}}"

domeye_artifact_validate_release_id "${RELEASE_ID}"
for command_name in awk date docker jq mktemp readlink sha256sum stat tail tar zstd; do
    domeye_artifact_require_command "${command_name}"
done
for sql_file in inventory.sql validate-integrity.sql create-reader.sql prune.sql; do
    domeye_artifact_require_regular_file "${SQL_DIR}/${sql_file}"
done

domeye_database_load_env "${DATABASE_ENV_FILE}"
domeye_database_validate_config

readonly RELEASE_DIR="$(domeye_artifact_release_dir "${ARTIFACT_ROOT}" "${RELEASE_ID}")"
domeye_artifact_assert_safe_release_dir "${ARTIFACT_ROOT}" "${RELEASE_DIR}"

require_unlinked_path() {
    local path="$1"
    local expected_type="$2"
    if [[ ! -e "${path}" || -L "${path}" ]]; then
        domeye_artifact_error "续跑路径不存在或是软链接：${path}"
        return 1
    fi
    if [[ "$(readlink -f -- "${path}")" != "${path}" ]]; then
        domeye_artifact_error "续跑路径包含软链接或非规范路径：${path}"
        return 1
    fi
    case "${expected_type}" in
        directory) [[ -d "${path}" ]] ;;
        file) [[ -f "${path}" ]] ;;
        *) return 2 ;;
    esac || {
        domeye_artifact_error "续跑路径类型不符合要求：${path}"
        return 1
    }
}

candidate_basename="${CANDIDATE_ROOT##*/}"
candidate_parent="${CANDIDATE_ROOT%/*}"
candidate_pid="${candidate_basename#build-${RELEASE_ID}-}"
if [[ "${candidate_parent}" != "${DOMEYE_CORE_DATABASE_WORK_ROOT}"
    || "${candidate_basename}" == "${candidate_pid}"
    || ! "${candidate_pid}" =~ ^[0-9]+$ ]]; then
    domeye_artifact_error "候选构建目录不属于指定 release-id：${CANDIDATE_ROOT}"
    exit 1
fi

readonly STATE_FILE="${CANDIDATE_ROOT}/build-state.json"
readonly CHECKPOINT_MARKER="${CANDIDATE_ROOT}/prune-sql-complete"
readonly PRUNE_FAILED_MARKER="${CANDIDATE_ROOT}/prune-attempt-failed"
readonly CANDIDATE_DATA_DIR="${CANDIDATE_ROOT}/postgres"
require_unlinked_path "${ARTIFACT_ROOT%/}" directory
require_unlinked_path "${ARTIFACT_ROOT%/}/releases" directory
require_unlinked_path "${RELEASE_DIR}" directory
require_unlinked_path "${CANDIDATE_ROOT}" directory
require_unlinked_path "${STATE_FILE}" file
require_unlinked_path "${CANDIDATE_DATA_DIR}" directory
domeye_artifact_json_file "${STATE_FILE}"
if [[ -e "${PRUNE_FAILED_MARKER}" || -L "${PRUNE_FAILED_MARKER}" ]]; then
    domeye_artifact_error '上次续跑中的 prune.sql 未完整成功，禁止再次裁剪；必须人工复核或隔离重建'
    exit 1
fi

if ! jq -e \
    --arg release_id "${RELEASE_ID}" \
    --arg data_start "${DOMEYE_CORE_DATA_START}" \
    --arg artifact_root "${ARTIFACT_ROOT%/}" \
    --arg release_dir "${RELEASE_DIR}" \
    --arg candidate_data_dir "${CANDIDATE_DATA_DIR}" \
    '(.schema_version == 1)
     and (.safe_checkpoint == "pre_prune_context"
          or .safe_checkpoint == "prune_sql_complete"
          or .safe_checkpoint == "database_component_published")
     and (.release_id == $release_id)
     and (.data_start == $data_start)
     and (.artifact_root == $artifact_root)
     and (.release_dir == $release_dir)
     and (.candidate_data_dir == $candidate_data_dir)
     and ((.snapshot_time | type) == "string")
     and ((.snapshot_local | type) == "string")
     and ((.snapshot_month | type) == "string")
     and ((.evidence_dir | type) == "string")
     and ((.image.ref | type) == "string")
     and ((.image.id | type) == "string")
     and ((.prune_sql_sha256 | test("^[0-9a-f]{64}$")))
     and ((.prune_output_sha256 == null) or (.prune_output_sha256 | test("^[0-9a-f]{64}$")))
     and (if (.safe_checkpoint == "prune_sql_complete" or .safe_checkpoint == "database_component_published")
          then (.prune_output_sha256 != null) else true end)
     and ((.system_identifier | test("^[0-9]+$")))
     and ((.component_created_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")))
     and ((.base_release == null) or ((.base_release | type) == "string"))
     and (.provenance.mode == "source_snapshot"
          or .provenance.mode == "incremental_refresh"
          or .provenance.mode == "prebuilt_full_dump")
     and (if .provenance.mode == "incremental_refresh" then
            (.provenance.base_release.release_id == .base_release)
            and (.provenance.base_release.manifest_sha256 | test("^[0-9a-f]{64}$"))
            and (.provenance.base_release.checksums_sha256 | test("^[0-9a-f]{64}$"))
            and (.provenance.base_release.database_sha256 | test("^[0-9a-f]{64}$"))
          else true end)
     and ((.staged_outputs | type) == "object")' \
    "${STATE_FILE}" >/dev/null; then
    domeye_artifact_error 'build-state.json 不是可续跑的安全检查点'
    exit 1
fi

readonly SNAPSHOT_TIME="$(jq -r '.snapshot_time' "${STATE_FILE}")"
readonly SNAPSHOT_LOCAL="$(jq -r '.snapshot_local' "${STATE_FILE}")"
readonly SNAPSHOT_MONTH="$(jq -r '.snapshot_month' "${STATE_FILE}")"
readonly EVIDENCE_DIR="$(jq -r '.evidence_dir' "${STATE_FILE}")"
readonly STATE_IMAGE_REF="$(jq -r '.image.ref' "${STATE_FILE}")"
readonly STATE_IMAGE_ID="$(jq -r '.image.id' "${STATE_FILE}")"
readonly STATE_SYSTEM_IDENTIFIER="$(jq -r '.system_identifier' "${STATE_FILE}")"
readonly STATE_SAFE_CHECKPOINT="$(jq -r '.safe_checkpoint' "${STATE_FILE}")"
readonly PRUNE_OUTPUT="${EVIDENCE_DIR}/prune-output.txt"
readonly PRUNE_OUTPUT_CHECKSUM="${EVIDENCE_DIR}/prune-output.sha256"
readonly PRUNE_PENDING_SUCCESS="${EVIDENCE_DIR}/prune-output.pending.success"

if [[ ! "${SNAPSHOT_TIME}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ \
    || ! "${SNAPSHOT_LOCAL}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}[[:space:]][0-9]{2}:[0-9]{2}:[0-9]{2}$ \
    || ! "${SNAPSHOT_MONTH}" =~ ^[0-9]{6}$ \
    || "${SNAPSHOT_MONTH}" != "${SNAPSHOT_LOCAL:0:4}${SNAPSHOT_LOCAL:5:2}" ]]; then
    domeye_artifact_error 'build-state.json 的快照时间字段无效'
    exit 1
fi
case "${EVIDENCE_DIR}" in
    "${RELEASE_DIR}/.database-build."*) ;;
    *)
        domeye_artifact_error "裁剪证据目录越界：${EVIDENCE_DIR}"
        exit 1
        ;;
esac
if [[ "${STATE_SAFE_CHECKPOINT}" == 'database_component_published' ]]; then
    completed_outputs=(
        "${DOMEYE_CORE_DATABASE_ARCHIVE}"
        "${DOMEYE_CORE_IMAGE_ARCHIVE}"
        'database-inventory.json'
        'database-schema.sql'
        "${DOMEYE_CORE_DATABASE_MANIFEST}"
    )
    for completed_name in "${completed_outputs[@]}"; do
        completed_path="${RELEASE_DIR}/${completed_name}"
        completed_sha="$(jq -r --arg name "${completed_name}" '.staged_outputs[$name].sha256 // empty' "${STATE_FILE}")"
        completed_size="$(jq -r --arg name "${completed_name}" '.staged_outputs[$name].size // empty' "${STATE_FILE}")"
        if [[ ! -f "${completed_path}" || -L "${completed_path}" \
            || ! "${completed_sha}" =~ ^[0-9a-f]{64}$ \
            || ! "${completed_size}" =~ ^[0-9]+$ \
            || "$(domeye_artifact_sha256 "${completed_path}")" != "${completed_sha}" \
            || "$(stat -c '%s' "${completed_path}")" != "${completed_size}" ]]; then
            domeye_artifact_error "已发布数据库组件与完成检查点不一致：${completed_name}"
            exit 1
        fi
    done
    if [[ -e "${EVIDENCE_DIR}" || -L "${EVIDENCE_DIR}" ]]; then
        require_unlinked_path "${EVIDENCE_DIR}" directory
        rm -rf -- "${EVIDENCE_DIR}"
    fi
    printf '数据库组件此前已续跑完成；遗留构建证据已收口：%s\n' "${RELEASE_DIR}"
    exit 0
fi
require_unlinked_path "${EVIDENCE_DIR}" directory

shopt -s nullglob
pending_prune_outputs=("${EVIDENCE_DIR}"/.prune-output.pending.*)
shopt -u nullglob
if [[ -e "${PRUNE_OUTPUT}" || -L "${PRUNE_OUTPUT}" ]]; then
    if (( ${#pending_prune_outputs[@]} > 0 )); then
        domeye_artifact_error '已存在原子裁剪输出时又发现 pending 输出，拒绝猜测裁剪次数'
        exit 1
    fi
    if [[ -e "${PRUNE_PENDING_SUCCESS}" || -L "${PRUNE_PENDING_SUCCESS}" ]]; then
        require_unlinked_path "${PRUNE_PENDING_SUCCESS}" file
        rm -f -- "${PRUNE_PENDING_SUCCESS}"
    fi
elif (( ${#pending_prune_outputs[@]} > 0 )); then
    if (( ${#pending_prune_outputs[@]} != 1 )); then
        domeye_artifact_error '发现多个裁剪 pending 输出，拒绝自动晋升'
        exit 1
    fi
    pending_prune_output="${pending_prune_outputs[0]}"
    require_unlinked_path "${pending_prune_output}" file
    require_unlinked_path "${PRUNE_PENDING_SUCCESS}" file
    mapfile -t pending_success_lines < "${PRUNE_PENDING_SUCCESS}"
    if (( ${#pending_success_lines[@]} != 1 )) \
        || [[ "${pending_success_lines[0]}" != "pending=$(basename -- "${pending_prune_output}")" ]] \
        || ! tail -n 1 "${pending_prune_output}" \
            | jq -e \
                '(.total | type) == "number"
                 and .total >= 0
                 and (.by_month_type | type) == "array"
                 and ([.by_month_type[].row_count] | add // 0) == .total' \
                >/dev/null; then
        domeye_artifact_error '裁剪 pending 输出缺少可信的成功哨兵或末行审计'
        exit 1
    fi
    mv -T -- "${pending_prune_output}" "${PRUNE_OUTPUT}"
    rm -f -- "${PRUNE_PENDING_SUCCESS}"
elif [[ -e "${PRUNE_PENDING_SUCCESS}" || -L "${PRUNE_PENDING_SUCCESS}" ]]; then
    domeye_artifact_error '裁剪成功哨兵存在，但对应 pending 和最终输出均缺失'
    exit 1
fi

prune_checkpoint_complete=false
if [[ -e "${PRUNE_OUTPUT}" || -L "${PRUNE_OUTPUT}" ]]; then
    require_unlinked_path "${PRUNE_OUTPUT}" file
    if [[ -e "${PRUNE_OUTPUT_CHECKSUM}" || -L "${PRUNE_OUTPUT_CHECKSUM}" ]]; then
        require_unlinked_path "${PRUNE_OUTPUT_CHECKSUM}" file
    fi
    if [[ -e "${CHECKPOINT_MARKER}" || -L "${CHECKPOINT_MARKER}" ]]; then
        require_unlinked_path "${CHECKPOINT_MARKER}" file
    fi
    prune_checkpoint_complete=true
elif [[ "${STATE_SAFE_CHECKPOINT}" == 'prune_sql_complete' \
    || -e "${CHECKPOINT_MARKER}" || -L "${CHECKPOINT_MARKER}" \
    || -e "${PRUNE_OUTPUT_CHECKSUM}" || -L "${PRUNE_OUTPUT_CHECKSUM}" ]]; then
    domeye_artifact_error '裁剪完成状态存在，但缺少原子发布的 prune-output.txt'
    exit 1
fi

if [[ "${STATE_IMAGE_REF}" != "${DOMEYE_CORE_DB_IMAGE}" ]]; then
    domeye_artifact_error '数据库配置中的镜像引用与 build-state.json 不一致'
    exit 1
fi
if [[ ! "${STATE_IMAGE_ID}" =~ ^sha256:[0-9a-f]{64}$ \
    || "$(docker image inspect --format '{{.Id}}' "${STATE_IMAGE_ID}" 2>/dev/null || true)" != "${STATE_IMAGE_ID}" ]]; then
    domeye_artifact_error '检查点固定的不可变 image ID 在本机不可用'
    exit 1
fi
DOMEYE_CORE_DB_IMAGE_RUNTIME="${STATE_IMAGE_ID}"
if [[ "$(domeye_artifact_sha256 "${SQL_DIR}/prune.sql")" != "$(jq -r '.prune_sql_sha256' "${STATE_FILE}")" ]]; then
    domeye_artifact_error '当前 prune.sql 与检查点哈希不一致'
    exit 1
fi
checkpoint_prune_sha=''
if [[ "${prune_checkpoint_complete}" == true ]]; then
    checkpoint_prune_sha="$(domeye_artifact_sha256 "${PRUNE_OUTPUT}")"
    if ! tail -n 1 "${PRUNE_OUTPUT}" \
        | jq -e \
            '(.total | type) == "number"
             and .total >= 0
             and (.by_month_type | type) == "array"
             and ([.by_month_type[].row_count] | add // 0) == .total' \
            >/dev/null; then
        domeye_artifact_error '原子裁剪输出缺少有效的末行审计 JSON'
        exit 1
    fi
    if [[ -e "${PRUNE_OUTPUT_CHECKSUM}" || -L "${PRUNE_OUTPUT_CHECKSUM}" ]]; then
        if [[ "$(awk 'END {print NR}' "${PRUNE_OUTPUT_CHECKSUM}")" != '1' ]]; then
            domeye_artifact_error '裁剪输出校验文件必须恰好包含一行'
            exit 1
        fi
        read -r recorded_prune_sha checkpoint_prune_name checkpoint_prune_extra < "${PRUNE_OUTPUT_CHECKSUM}"
        if [[ -n "${checkpoint_prune_extra:-}" \
            || "${recorded_prune_sha}" != "${checkpoint_prune_sha}" \
            || "${checkpoint_prune_name}" != 'prune-output.txt' ]]; then
            domeye_artifact_error '裁剪输出与既有检查点哈希不一致'
            exit 1
        fi
    else
        prune_checksum_tmp="${EVIDENCE_DIR}/.prune-output.sha256.tmp.$$"
        printf '%s  %s\n' "${checkpoint_prune_sha}" 'prune-output.txt' > "${prune_checksum_tmp}"
        chmod 0600 "${prune_checksum_tmp}"
        mv -T -- "${prune_checksum_tmp}" "${PRUNE_OUTPUT_CHECKSUM}"
    fi
    state_prune_sha="$(jq -r '.prune_output_sha256 // empty' "${STATE_FILE}")"
    if [[ -n "${state_prune_sha}" && "${state_prune_sha}" != "${checkpoint_prune_sha}" ]]; then
        domeye_artifact_error 'build-state.json 与独立裁剪输出校验文件不一致'
        exit 1
    fi
    if [[ ! -e "${CHECKPOINT_MARKER}" && ! -L "${CHECKPOINT_MARKER}" ]]; then
        install -m 0600 /dev/null "${CHECKPOINT_MARKER}"
    fi
fi

offline_system_identifier="$(docker run --rm \
    --env 'LC_ALL=C' \
    --user postgres \
    --volume "${CANDIDATE_DATA_DIR}:/var/lib/postgresql/data:ro" \
    --entrypoint pg_controldata \
    "${STATE_IMAGE_ID}" \
    /var/lib/postgresql/data \
    | awk -F ': *' '/Database system identifier/ {print $2; exit}')"
if [[ "${offline_system_identifier}" != "${STATE_SYSTEM_IDENTIFIER}" ]]; then
    domeye_artifact_error '候选 PGDATA 的离线 system identifier 与检查点不一致'
    exit 1
fi

candidate_real="$(readlink -f -- "${CANDIDATE_DATA_DIR}")"
while IFS= read -r existing_container; do
    [[ -n "${existing_container}" ]] || continue
    while IFS= read -r mount_source; do
        [[ -n "${mount_source}" && -e "${mount_source}" ]] || continue
        if [[ "$(readlink -f -- "${mount_source}")" == "${candidate_real}" ]]; then
            domeye_artifact_error "候选 PGDATA 已被容器挂载，拒绝并发续跑：${existing_container}"
            exit 1
        fi
    done < <(docker inspect --format '{{range .Mounts}}{{println .Source}}{{end}}' "${existing_container}")
done < <(docker ps -aq)

readonly LOCK_DIR="${RELEASE_DIR}/.database-build.lock"
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
    domeye_artifact_error "同一发布版本正在构建或续跑数据库制品：${RELEASE_ID}"
    exit 1
fi

container_suffix="${RELEASE_ID//[^a-zA-Z0-9]/_}"
readonly CANDIDATE_CONTAINER="domeye_core_resume_${container_suffix}_$$"
work_dir=''

cleanup() {
    local exit_code=$?
    domeye_database_remove_candidate_container "${CANDIDATE_CONTAINER}" || true
    if [[ -d "${work_dir}" && "${work_dir}" == "${RELEASE_DIR}/.database-resume."* ]]; then
        rm -rf -- "${work_dir}"
    fi
    rmdir "${LOCK_DIR}" 2>/dev/null || true
    if (( exit_code != 0 )); then
        printf '续跑失败；候选 PGDATA、build-state.json 与原裁剪证据均已保留：%s\n' "${CANDIDATE_ROOT}" >&2
    fi
    return "${exit_code}"
}
trap cleanup EXIT

if [[ "${STATE_SAFE_CHECKPOINT}" == 'pre_prune_context' && "${prune_checkpoint_complete}" == true ]]; then
    state_tmp="${CANDIDATE_ROOT}/.build-state.tmp.$$"
    jq --arg prune_output_sha256 "${checkpoint_prune_sha}" \
        '.safe_checkpoint = "prune_sql_complete"
         | .current_stage = "post_prune_validation"
         | .prune_output_sha256 = $prune_output_sha256' \
        "${STATE_FILE}" > "${state_tmp}"
    chmod 0600 "${state_tmp}"
    mv -T -- "${state_tmp}" "${STATE_FILE}"
fi

work_dir="$(mktemp -d "${RELEASE_DIR}/.database-resume.XXXXXX")"

output_names=(
    "${DOMEYE_CORE_DATABASE_ARCHIVE}"
    "${DOMEYE_CORE_IMAGE_ARCHIVE}"
    'database-inventory.json'
    'database-schema.sql'
    "${DOMEYE_CORE_DATABASE_MANIFEST}"
)
for finalized_name in "${DOMEYE_CORE_RELEASE_MANIFEST}" "${DOMEYE_CORE_CHECKSUM_FILE}"; do
    if [[ -e "${RELEASE_DIR}/${finalized_name}" || -L "${RELEASE_DIR}/${finalized_name}" ]]; then
        domeye_artifact_error "发布已进入总清单定稿阶段，拒绝续写数据库组件：${finalized_name}"
        exit 1
    fi
done
for output_name in "${output_names[@]}"; do
    output_path="${RELEASE_DIR}/${output_name}"
    [[ -e "${output_path}" || -L "${output_path}" ]] || continue
    require_unlinked_path "${output_path}" file
    trusted_sha="$(jq -r --arg name "${output_name}" '.staged_outputs[$name].sha256 // empty' "${STATE_FILE}")"
    trusted_size="$(jq -r --arg name "${output_name}" '.staged_outputs[$name].size // empty' "${STATE_FILE}")"
    if [[ ! "${trusted_sha}" =~ ^[0-9a-f]{64}$ || ! "${trusted_size}" =~ ^[0-9]+$ \
        || "$(domeye_artifact_sha256 "${output_path}")" != "${trusted_sha}" \
        || "$(stat -c '%s' "${output_path}")" != "${trusted_size}" ]]; then
        domeye_artifact_error "已有数据库输出不匹配可信 staging，拒绝覆盖：${output_name}"
        exit 1
    fi
done
if [[ -e "${RELEASE_DIR}/${DOMEYE_CORE_DATABASE_MANIFEST}" ]]; then
    for output_name in "${output_names[@]:0:4}"; do
        if [[ ! -f "${RELEASE_DIR}/${output_name}" || -L "${RELEASE_DIR}/${output_name}" ]]; then
            domeye_artifact_error 'database-manifest.json 已存在，但数据库组件集合不完整'
            exit 1
        fi
    done
fi

docker run --detach \
    --name "${CANDIDATE_CONTAINER}" \
    --memory "${DOMEYE_CORE_DATABASE_MEMORY}" \
    --shm-size 4g \
    --volume "${CANDIDATE_DATA_DIR}:/var/lib/postgresql/data" \
    "${STATE_IMAGE_ID}" \
    postgres \
    -c "shared_buffers=${DOMEYE_CORE_DATABASE_SHARED_BUFFERS}" \
    -c 'listen_addresses=*' \
    -c 'timescaledb.telemetry_level=off' \
    >/dev/null
domeye_database_wait_container "${CANDIDATE_CONTAINER}"

live_system_identifier="$(domeye_database_psql "${CANDIDATE_CONTAINER}" --quiet --no-align --tuples-only --command 'SELECT system_identifier::text FROM pg_control_system();')"
if [[ "${live_system_identifier}" != "${STATE_SYSTEM_IDENTIFIER}" ]]; then
    domeye_artifact_error '启动后的候选库 system identifier 与检查点不一致'
    exit 1
fi

if [[ "${prune_checkpoint_complete}" != true ]]; then
    resume_prune_pending="${EVIDENCE_DIR}/.prune-output.pending.$$"
    if ! docker exec --interactive \
        "${CANDIDATE_CONTAINER}" \
        psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
            --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
            --dbname "${DOMEYE_CORE_DB_NAME}" \
            --set "data_start=${DOMEYE_CORE_DATA_START}" \
            --set "snapshot_local=${SNAPSHOT_LOCAL}" \
            --set "snapshot_month=${SNAPSHOT_MONTH}" \
            < "${SQL_DIR}/prune.sql" \
            > "${resume_prune_pending}"; then
        install -m 0600 /dev/null "${PRUNE_FAILED_MARKER}" || true
        domeye_artifact_error '续跑中的 prune.sql 失败；候选库保留但不得再次自动裁剪'
        exit 1
    fi
    resume_prune_success_tmp="${EVIDENCE_DIR}/.prune-output.pending.success.tmp.$$"
    printf 'pending=%s\n' "$(basename -- "${resume_prune_pending}")" > "${resume_prune_success_tmp}"
    chmod 0600 "${resume_prune_success_tmp}"
    mv -T -- "${resume_prune_success_tmp}" "${PRUNE_PENDING_SUCCESS}"
    mv -T -- "${resume_prune_pending}" "${PRUNE_OUTPUT}"
    rm -f -- "${PRUNE_PENDING_SUCCESS}"
    checkpoint_prune_sha="$(domeye_artifact_sha256 "${PRUNE_OUTPUT}")"
    prune_checksum_tmp="${EVIDENCE_DIR}/.prune-output.sha256.tmp.$$"
    printf '%s  %s\n' "${checkpoint_prune_sha}" 'prune-output.txt' > "${prune_checksum_tmp}"
    chmod 0600 "${prune_checksum_tmp}"
    mv -T -- "${prune_checksum_tmp}" "${PRUNE_OUTPUT_CHECKSUM}"
    install -m 0600 /dev/null "${CHECKPOINT_MARKER}"
    state_tmp="${CANDIDATE_ROOT}/.build-state.tmp.$$"
    jq --arg prune_output_sha256 "${checkpoint_prune_sha}" \
        '.safe_checkpoint = "prune_sql_complete"
         | .current_stage = "post_prune_validation"
         | .prune_output_sha256 = $prune_output_sha256' \
        "${STATE_FILE}" > "${state_tmp}"
    chmod 0600 "${state_tmp}"
    mv -T -- "${state_tmp}" "${STATE_FILE}"
    prune_checkpoint_complete=true
fi

readonly PRUNE_AUDIT="${work_dir}/prune-audit.json"
tail -n 1 "${PRUNE_OUTPUT}" > "${PRUNE_AUDIT}"
domeye_artifact_json_file "${PRUNE_AUDIT}"
base_release_id="$(jq -r '.base_release // empty' "${STATE_FILE}")"
if [[ -n "${base_release_id}" ]]; then
    domeye_artifact_validate_release_id "${base_release_id}"
    base_release_dir="${ARTIFACT_ROOT%/}/releases/${base_release_id}"
    "${SCRIPT_DIR}/../artifacts/verify-release.sh" "${base_release_dir}"
    if [[ "$(domeye_artifact_sha256 "${base_release_dir}/${DOMEYE_CORE_RELEASE_MANIFEST}")" \
            != "$(jq -r '.provenance.base_release.manifest_sha256' "${STATE_FILE}")" \
        || "$(domeye_artifact_sha256 "${base_release_dir}/${DOMEYE_CORE_CHECKSUM_FILE}")" \
            != "$(jq -r '.provenance.base_release.checksums_sha256' "${STATE_FILE}")" \
        || "$(domeye_artifact_sha256 "${base_release_dir}/${DOMEYE_CORE_DATABASE_ARCHIVE}")" \
            != "$(jq -r '.provenance.base_release.database_sha256' "${STATE_FILE}")" ]]; then
        domeye_artifact_error '增量刷新基准发布与构建检查点哈希不一致'
        exit 1
    fi
    base_snapshot_local="$(jq -r '.snapshot_local' "${base_release_dir}/${DOMEYE_CORE_DATABASE_MANIFEST}")"
    base_month="${base_snapshot_local:0:7}"
    base_month="${base_month//-/}"
    jq -n \
        --arg base_month "${base_month}" \
        --slurpfile previous "${base_release_dir}/database-inventory.json" \
        --slurpfile current "${PRUNE_AUDIT}" \
        '([$previous[0].integrity.detail_references.discarded_malformed_event_rows.by_month_type[]
           | select(.month < $base_month)] + $current[0].by_month_type)
         | sort_by(.month, .event_type)
         | {total: ([.[].row_count] | add // 0), by_month_type: .}' \
        > "${PRUNE_AUDIT}.merged"
    mv -- "${PRUNE_AUDIT}.merged" "${PRUNE_AUDIT}"
fi
if ! jq -e \
    '(.total | type) == "number"
     and .total >= 0
     and ([.by_month_type[].row_count] | add // 0) == .total' \
    "${PRUNE_AUDIT}" >/dev/null; then
    domeye_artifact_error '异常事件裁剪审计结果无效'
    exit 1
fi

domeye_database_apply_reader "${CANDIDATE_CONTAINER}" "${SQL_DIR}/create-reader.sql"
readonly POSTGRES_VERSION="$(domeye_database_psql "${CANDIDATE_CONTAINER}" --quiet --no-align --tuples-only --command 'SHOW server_version;')"
readonly TIMESCALEDB_VERSION="$(domeye_database_psql "${CANDIDATE_CONTAINER}" --quiet --no-align --tuples-only --command "SELECT extversion FROM pg_extension WHERE extname='timescaledb';")"
if [[ "${POSTGRES_VERSION}" != '12.16' || "${TIMESCALEDB_VERSION}" != '2.11.2' ]]; then
    domeye_artifact_error "数据库版本不符合冻结基线：PostgreSQL=${POSTGRES_VERSION}，TimescaleDB=${TIMESCALEDB_VERSION}"
    exit 1
fi
reader_readonly="$(docker exec "${CANDIDATE_CONTAINER}" psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 --username "${DOMEYE_CORE_DB_READER_USER}" --dbname "${DOMEYE_CORE_DB_NAME}" --command 'SHOW transaction_read_only;')"
reader_count="$(docker exec "${CANDIDATE_CONTAINER}" psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 --username "${DOMEYE_CORE_DB_READER_USER}" --dbname "${DOMEYE_CORE_DB_NAME}" --command 'SELECT count(*) FROM public.feature_country;')"
if [[ "${reader_readonly}" != 'on' || ! "${reader_count}" =~ ^[0-9]+$ || "${reader_count}" == '0' ]]; then
    domeye_artifact_error '只读账号门禁失败'
    exit 1
fi
if docker exec "${CANDIDATE_CONTAINER}" psql -X --quiet --set ON_ERROR_STOP=1 --username "${DOMEYE_CORE_DB_READER_USER}" --dbname "${DOMEYE_CORE_DB_NAME}" --command 'CREATE TABLE public.__domeye_readonly_probe(id integer);' >/dev/null 2>&1; then
    domeye_database_psql "${CANDIDATE_CONTAINER}" --command 'DROP TABLE IF EXISTS public.__domeye_readonly_probe;'
    domeye_artifact_error '只读账号意外获得了建表能力'
    exit 1
fi

readonly INTEGRITY_TMP="${work_dir}/database-integrity.json"
docker exec --interactive "${CANDIDATE_CONTAINER}" psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
    --username "${DOMEYE_CORE_DB_ADMIN_USER}" --dbname "${DOMEYE_CORE_DB_NAME}" \
    --set "data_start=${DOMEYE_CORE_DATA_START}" --set "snapshot_time=${SNAPSHOT_TIME}" --set "snapshot_month=${SNAPSHOT_MONTH}" \
    < "${SQL_DIR}/validate-integrity.sql" > "${INTEGRITY_TMP}"
domeye_artifact_json_file "${INTEGRITY_TMP}"
jq --slurpfile discarded "${PRUNE_AUDIT}" '.detail_references.discarded_malformed_event_rows = $discarded[0]' "${INTEGRITY_TMP}" > "${INTEGRITY_TMP}.merged"
mv -- "${INTEGRITY_TMP}.merged" "${INTEGRITY_TMP}"
if ! jq -e '.table_whitelist.ok == true and .detail_references.ok == true and .detail_references.malformed_count == 0 and .detail_references.orphan_count == 0' "${INTEGRITY_TMP}" >/dev/null; then
    domeye_artifact_error '候选库白名单或事件详情引用完整性门禁失败'
    exit 1
fi

readonly INVENTORY_RAW="${work_dir}/database-inventory-raw.json"
readonly INVENTORY_TMP="${work_dir}/database-inventory.json"
docker exec --interactive "${CANDIDATE_CONTAINER}" psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
    --username "${DOMEYE_CORE_DB_ADMIN_USER}" --dbname "${DOMEYE_CORE_DB_NAME}" \
    --set "data_start=${DOMEYE_CORE_DATA_START}" --set "snapshot_time=${SNAPSHOT_TIME}" \
    < "${SQL_DIR}/inventory.sql" > "${INVENTORY_RAW}"
domeye_artifact_json_file "${INVENTORY_RAW}"
jq -s '.[0] + {integrity: .[1]}' "${INVENTORY_RAW}" "${INTEGRITY_TMP}" > "${INVENTORY_TMP}"
domeye_artifact_json_file "${INVENTORY_TMP}"
if ! jq -e --arg start "${DOMEYE_CORE_DATA_START}" --arg snapshot_end "${SNAPSHOT_LOCAL}" \
    'all(.tables[]; ((.min_time == null or .min_time >= $start) and (.max_time == null or .max_time <= $snapshot_end)))' \
    "${INVENTORY_TMP}" >/dev/null; then
    domeye_artifact_error '候选库存在超出固定时间范围的数据'
    exit 1
fi

readonly SCHEMA_TMP="${work_dir}/database-schema.sql"
docker exec "${CANDIDATE_CONTAINER}" pg_dump --schema-only --no-owner --no-acl \
    --username "${DOMEYE_CORE_DB_ADMIN_USER}" --dbname "${DOMEYE_CORE_DB_NAME}" > "${SCHEMA_TMP}"
chmod 0600 "${SCHEMA_TMP}"
readonly DATABASE_TMP="${work_dir}/${DOMEYE_CORE_DATABASE_ARCHIVE}"
docker exec "${CANDIDATE_CONTAINER}" pg_dump --format=custom --compress=0 --no-owner --no-acl \
    --username "${DOMEYE_CORE_DB_ADMIN_USER}" --dbname "${DOMEYE_CORE_DB_NAME}" \
    | zstd --quiet --threads=0 -6 -o "${DATABASE_TMP}"
chmod 0600 "${DATABASE_TMP}"
readonly IMAGE_TMP="${work_dir}/${DOMEYE_CORE_IMAGE_ARCHIVE}"
docker image save "${STATE_IMAGE_ID}" | zstd --quiet --threads=0 -6 -o "${IMAGE_TMP}"
chmod 0600 "${IMAGE_TMP}"
image_archive_config="$(zstd --quiet --decompress --stdout "${IMAGE_TMP}" \
    | tar --extract --to-stdout --file=- manifest.json \
    | jq -er 'if length == 1 then .[0].Config else error("image count") end')"
if [[ "${image_archive_config}" != "${STATE_IMAGE_ID#sha256:}.json" ]]; then
    domeye_artifact_error '数据库镜像归档的 config digest 与固定 image ID 不一致'
    exit 1
fi
readonly IMAGE_DIGEST="$(docker image inspect --format '{{join .RepoDigests ","}}' "${STATE_IMAGE_ID}")"

declare -A final_sha final_size staging_path
staging_path["${DOMEYE_CORE_DATABASE_ARCHIVE}"]="${DATABASE_TMP}"
staging_path["${DOMEYE_CORE_IMAGE_ARCHIVE}"]="${IMAGE_TMP}"
staging_path['database-inventory.json']="${INVENTORY_TMP}"
staging_path['database-schema.sql']="${SCHEMA_TMP}"
for output_name in "${output_names[@]:0:4}"; do
    output_path="${RELEASE_DIR}/${output_name}"
    if [[ -e "${output_path}" ]]; then
        final_sha["${output_name}"]="$(domeye_artifact_sha256 "${output_path}")"
        final_size["${output_name}"]="$(stat -c '%s' "${output_path}")"
        if [[ "${output_name}" == 'database-inventory.json' || "${output_name}" == 'database-schema.sql' ]]; then
            if [[ "$(domeye_artifact_sha256 "${staging_path[${output_name}]}")" != "${final_sha[${output_name}]}" ]]; then
                domeye_artifact_error "重跑结果与已有可信输出不一致：${output_name}"
                exit 1
            fi
        fi
    else
        final_sha["${output_name}"]="$(domeye_artifact_sha256 "${staging_path[${output_name}]}")"
        final_size["${output_name}"]="$(stat -c '%s' "${staging_path[${output_name}]}")"
    fi
done

state_tmp="${CANDIDATE_ROOT}/.build-state.tmp.$$"
jq \
    --arg database_name "${DOMEYE_CORE_DATABASE_ARCHIVE}" --arg database_sha "${final_sha[${DOMEYE_CORE_DATABASE_ARCHIVE}]}" --argjson database_size "${final_size[${DOMEYE_CORE_DATABASE_ARCHIVE}]}" \
    --arg image_name "${DOMEYE_CORE_IMAGE_ARCHIVE}" --arg image_sha "${final_sha[${DOMEYE_CORE_IMAGE_ARCHIVE}]}" --argjson image_size "${final_size[${DOMEYE_CORE_IMAGE_ARCHIVE}]}" \
    --arg inventory_sha "${final_sha[database-inventory.json]}" --argjson inventory_size "${final_size[database-inventory.json]}" \
    --arg schema_sha "${final_sha[database-schema.sql]}" --argjson schema_size "${final_size[database-schema.sql]}" \
    --arg manifest_name "${DOMEYE_CORE_DATABASE_MANIFEST}" \
    '. as $state
     | .current_stage = "publish_pending"
     | .staged_outputs = (
         {
           ($database_name): {sha256: $database_sha, size: $database_size},
           ($image_name): {sha256: $image_sha, size: $image_size},
           "database-inventory.json": {sha256: $inventory_sha, size: $inventory_size},
           "database-schema.sql": {sha256: $schema_sha, size: $schema_size}
         }
         + (if $state.staged_outputs[$manifest_name] == null then {}
            else {($manifest_name): $state.staged_outputs[$manifest_name]} end)
       )' "${STATE_FILE}" > "${state_tmp}"
chmod 0600 "${state_tmp}"
mv -T -- "${state_tmp}" "${STATE_FILE}"

publish_resume_output() {
    local staged_path="$1"
    local output_name="$2"
    local target_path="${RELEASE_DIR}/${output_name}"
    local expected_sha expected_size
    expected_sha="$(jq -r --arg name "${output_name}" '.staged_outputs[$name].sha256' "${STATE_FILE}")"
    expected_size="$(jq -r --arg name "${output_name}" '.staged_outputs[$name].size' "${STATE_FILE}")"

    if [[ ! -e "${target_path}" && ! -L "${target_path}" ]]; then
        mv --no-clobber -- "${staged_path}" "${target_path}"
    fi
    if [[ ! -f "${target_path}" || -L "${target_path}"
        || "$(domeye_artifact_sha256 "${target_path}")" != "${expected_sha}"
        || "$(stat -c '%s' "${target_path}")" != "${expected_size}" ]]; then
        domeye_artifact_error "数据库输出与可信 staging 不一致，拒绝覆盖：${output_name}"
        return 1
    fi
    if [[ -e "${staged_path}" ]]; then
        rm -f -- "${staged_path}"
    fi
}

for output_name in "${output_names[@]:0:4}"; do
    publish_resume_output "${staging_path[${output_name}]}" "${output_name}"
done

readonly MANIFEST_TMP="${work_dir}/${DOMEYE_CORE_DATABASE_MANIFEST}"
jq -n \
    --argjson schema_version 1 --arg release_id "${RELEASE_ID}" \
    --arg created_at "$(jq -r '.component_created_at' "${STATE_FILE}")" \
    --arg data_start "${DOMEYE_CORE_DATA_START}" --arg snapshot_time "${SNAPSHOT_TIME}" --arg snapshot_local "${SNAPSHOT_LOCAL}" \
    --arg postgres_version "${POSTGRES_VERSION}" --arg timescaledb_version "${TIMESCALEDB_VERSION}" \
    --arg archive_name "${DOMEYE_CORE_DATABASE_ARCHIVE}" --arg archive_sha256 "$(domeye_artifact_sha256 "${RELEASE_DIR}/${DOMEYE_CORE_DATABASE_ARCHIVE}")" --argjson archive_size "$(stat -c '%s' "${RELEASE_DIR}/${DOMEYE_CORE_DATABASE_ARCHIVE}")" \
    --arg inventory_sha256 "$(domeye_artifact_sha256 "${RELEASE_DIR}/database-inventory.json")" \
    --arg schema_sha256 "$(domeye_artifact_sha256 "${RELEASE_DIR}/database-schema.sql")" \
    --arg image_archive "${DOMEYE_CORE_IMAGE_ARCHIVE}" --arg image_archive_sha256 "$(domeye_artifact_sha256 "${RELEASE_DIR}/${DOMEYE_CORE_IMAGE_ARCHIVE}")" \
    --arg image_ref "${STATE_IMAGE_REF}" --arg image_id "${STATE_IMAGE_ID}" --arg image_digest "${IMAGE_DIGEST}" \
    --slurpfile inventory "${RELEASE_DIR}/database-inventory.json" --slurpfile state "${STATE_FILE}" \
    '{
      schema_version: $schema_version, component: "database", release_id: $release_id, created_at: $created_at,
      data_start: $data_start, snapshot_time: $snapshot_time, snapshot_local: $snapshot_local, snapshot_timezone: "Asia/Shanghai",
      base_release: $state[0].base_release,
      versions: {postgresql: $postgres_version, timescaledb: $timescaledb_version},
      archive: {name: $archive_name, sha256: $archive_sha256, size: $archive_size},
      inventory: {name: "database-inventory.json", sha256: $inventory_sha256, table_count: ($inventory[0].tables | length)},
      integrity: {
        source: "database-inventory.json", table_whitelist_ok: $inventory[0].integrity.table_whitelist.ok,
        malformed_detail_count: $inventory[0].integrity.detail_references.malformed_count,
        orphan_detail_count: $inventory[0].integrity.detail_references.orphan_count,
        discarded_malformed_event_rows: $inventory[0].integrity.detail_references.discarded_malformed_event_rows
      },
      schema: {name: "database-schema.sql", sha256: $schema_sha256},
      image: {archive: $image_archive, archive_sha256: $image_archive_sha256, ref: $image_ref, id: $image_id, digest: $image_digest},
      provenance: $state[0].provenance
    }' > "${MANIFEST_TMP}"
chmod 0600 "${MANIFEST_TMP}"
manifest_sha="$(domeye_artifact_sha256 "${MANIFEST_TMP}")"
manifest_size="$(stat -c '%s' "${MANIFEST_TMP}")"
manifest_target="${RELEASE_DIR}/${DOMEYE_CORE_DATABASE_MANIFEST}"
if [[ -e "${manifest_target}" ]]; then
    if [[ "$(domeye_artifact_sha256 "${manifest_target}")" != "${manifest_sha}" || "$(stat -c '%s' "${manifest_target}")" != "${manifest_size}" ]]; then
        domeye_artifact_error '重跑生成的 manifest 与已有可信 manifest 不一致'
        exit 1
    fi
fi
state_tmp="${CANDIDATE_ROOT}/.build-state.tmp.$$"
jq --arg name "${DOMEYE_CORE_DATABASE_MANIFEST}" --arg sha "${manifest_sha}" --argjson size "${manifest_size}" \
    '.staged_outputs[$name] = {sha256: $sha, size: $size}' "${STATE_FILE}" > "${state_tmp}"
chmod 0600 "${state_tmp}"
mv -T -- "${state_tmp}" "${STATE_FILE}"
publish_resume_output "${MANIFEST_TMP}" "${DOMEYE_CORE_DATABASE_MANIFEST}"

state_tmp="${CANDIDATE_ROOT}/.build-state.tmp.$$"
jq '.safe_checkpoint = "database_component_published" | .current_stage = "complete"' "${STATE_FILE}" > "${state_tmp}"
chmod 0600 "${state_tmp}"
mv -T -- "${state_tmp}" "${STATE_FILE}"
require_unlinked_path "${EVIDENCE_DIR}" directory
rm -rf -- "${EVIDENCE_DIR}"
printf '数据库制品续跑完成；候选 PGDATA 保留到完整发布验收结束：%s\n' "${CANDIDATE_ROOT}"
