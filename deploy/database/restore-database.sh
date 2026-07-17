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

for command_name in docker jq mktemp readlink sha256sum zstd; do
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
readonly EXPECTED_DUMP_SHA="$(jq -r '.archive.sha256' "${DATABASE_MANIFEST}")"

zstd --quiet --decompress --stdout "${IMAGE_ARCHIVE}" | docker image load >/dev/null
readonly EXPECTED_IMAGE_REF="$(jq -r '.image.ref' "${DATABASE_MANIFEST}")"
readonly EXPECTED_IMAGE_ID="$(jq -r '.image.id' "${DATABASE_MANIFEST}")"
if [[ "${DOMEYE_CORE_DB_IMAGE}" != "${EXPECTED_IMAGE_REF}" ]]; then
    domeye_artifact_error "数据库配置镜像与制品不一致：${DOMEYE_CORE_DB_IMAGE} != ${EXPECTED_IMAGE_REF}"
    exit 1
fi
if [[ "$(docker image inspect --format '{{.Id}}' "${EXPECTED_IMAGE_REF}")" != "${EXPECTED_IMAGE_ID}" ]]; then
    domeye_artifact_error '加载后的数据库镜像 ID 与发布清单不一致'
    exit 1
fi

reuse_existing=false
if [[ -f "${STATE_FILE}" && -f "${DATA_DIR}/PG_VERSION" ]]; then
    if [[ "$(jq -r '.database_sha256' "${STATE_FILE}")" != "${EXPECTED_DUMP_SHA}" || "$(jq -r '.image_id' "${STATE_FILE}")" != "${EXPECTED_IMAGE_ID}" ]]; then
        domeye_artifact_error "已有同名恢复目录，但状态与制品不一致：${RELEASE_DATA_ROOT}"
        exit 1
    fi
    reuse_existing=true
elif [[ -e "${RELEASE_DATA_ROOT}" ]]; then
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
restore_complete="${reuse_existing}"
work_dir=''
cleanup() {
    local exit_code=$?
    domeye_database_remove_candidate_container "${CANDIDATE_CONTAINER}" || true
    if [[ "${restore_complete}" != true && "${RELEASE_DATA_ROOT}" == "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/${RELEASE_ID}" && -d "${RELEASE_DATA_ROOT}" ]]; then
        rm -rf -- "${RELEASE_DATA_ROOT}"
    fi
    if [[ -n "${work_dir}" && -d "${work_dir}" ]]; then
        rm -rf -- "${work_dir}"
    fi
    return "${exit_code}"
}
trap cleanup EXIT

if [[ "${reuse_existing}" == true ]]; then
    docker run --detach \
        --name "${CANDIDATE_CONTAINER}" \
        --memory "${DOMEYE_CORE_DATABASE_MEMORY}" \
        --shm-size 4g \
        --env "POSTGRES_DB=${DOMEYE_CORE_DB_NAME}" \
        --env "POSTGRES_USER=${DOMEYE_CORE_DB_ADMIN_USER}" \
        --volume "${DATA_DIR}:/var/lib/postgresql/data" \
        "${DOMEYE_CORE_DB_IMAGE}" \
        postgres \
        -c "shared_buffers=${DOMEYE_CORE_DATABASE_SHARED_BUFFERS}" \
        -c 'listen_addresses=*' \
        -c 'timescaledb.telemetry_level=off' \
        >/dev/null
    domeye_database_wait_container "${CANDIDATE_CONTAINER}"
else
    domeye_database_start_candidate "${CANDIDATE_CONTAINER}" "${DATA_DIR}"
    domeye_database_restore_archive "${CANDIDATE_CONTAINER}" "${DATABASE_ARCHIVE}"
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

reader_check="$(docker exec \
    "${CANDIDATE_CONTAINER}" \
    psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_READER_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --command 'SHOW transaction_read_only; SELECT count(*) FROM public.feature_country;')"
if [[ "${reader_check%%$'\n'*}" != 'on' || ! "${reader_check##*$'\n'}" =~ ^[1-9][0-9]*$ ]]; then
    domeye_artifact_error '恢复后的只读账号不能读取 feature_country 超表'
    exit 1
fi

state_tmp="${RELEASE_DATA_ROOT}/.restore-state.tmp.$$"
jq -n \
    --arg release_id "${RELEASE_ID}" \
    --arg restored_at "$(domeye_artifact_iso_utc_now)" \
    --arg database_sha256 "${EXPECTED_DUMP_SHA}" \
    --arg image_id "${EXPECTED_IMAGE_ID}" \
    '{release_id: $release_id, restored_at: $restored_at, database_sha256: $database_sha256, image_id: $image_id}' \
    > "${state_tmp}"
chmod 0600 "${state_tmp}"
mv -- "${state_tmp}" "${STATE_FILE}"
rm -rf -- "${work_dir}"
restore_complete=true
if [[ "${reuse_existing}" == true ]]; then
    printf '已有数据库发布的镜像、inventory 和只读查询复验完成：%s\n' "${RELEASE_DATA_ROOT}"
else
    printf '独立数据库候选发布恢复并校验完成：%s\n' "${RELEASE_DATA_ROOT}"
fi
