#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"
# shellcheck source=../lib/static-info-common.sh
source "${SCRIPT_DIR}/../lib/static-info-common.sh"

usage() {
    printf '%s\n' \
        "用法：${0##*/} <只读INFO目录> <候选容器> <数据库管理员> <数据库名> <Core后端目录> <S3证据目录> <S4证据目录>" \
        >&2
}

if (( $# != 7 )); then
    usage
    exit 2
fi

readonly SOURCE_INFO_DIR="$1"
readonly CANDIDATE_CONTAINER="$2"
readonly DATABASE_ADMIN="$3"
readonly DATABASE_NAME="$4"
readonly CORE_BACKEND_ROOT="$5"
readonly S3_EVIDENCE_DIR="$6"
readonly S4_EVIDENCE_DIR="$7"
readonly RUNTIME_ROLE="domeye_core_reader"
readonly READER_SQL="${REPOSITORY_ROOT}/deploy/database/sql/create-reader.sql"

for command_name in docker install jq sha256sum openssl mktemp ln readlink \
    unlink rmdir sed; do
    domeye_artifact_require_command "${command_name}"
done
for directory in \
    "${SOURCE_INFO_DIR}" \
    "${CORE_BACKEND_ROOT}" \
    "${S3_EVIDENCE_DIR}"; do
    if [[ ! -d "${directory}" || -L "${directory}" ]]; then
        domeye_artifact_error "S4 输入目录无效或为软链接：${directory}"
        exit 1
    fi
done
for required_path in \
    "${CORE_BACKEND_ROOT}/core.sha256" \
    "${S3_EVIDENCE_DIR}/static-info-manifest.json" \
    "${S3_EVIDENCE_DIR}/stage-gate-S3.json" \
    "${S3_EVIDENCE_DIR}/SHA256SUMS" \
    "${READER_SQL}"; do
    domeye_artifact_require_regular_file "${required_path}"
done
domeye_static_info_assert_offline_candidate "${CANDIDATE_CONTAINER}"

(
    cd -- "${S3_EVIDENCE_DIR}"
    sha256sum -c SHA256SUMS
)
readonly S3_MANIFEST="${S3_EVIDENCE_DIR}/static-info-manifest.json"
readonly S3_RECEIPT="${S3_EVIDENCE_DIR}/stage-gate-S3.json"
if ! jq -e \
    --slurpfile manifest "${S3_MANIFEST}" \
    '.component == "static_info_stage_gate"
     and .stage_id == "S3"
     and .status == "pass"
     and .deviation_count == 0
     and .subject.content_id == $manifest[0].content_id
     and .subject.manifest_sha256 == $manifest[0].manifest_sha256' \
    "${S3_RECEIPT}" >/dev/null; then
    domeye_artifact_error 'S3 回执与 manifest 不一致或未通过'
    exit 1
fi
if [[ -e "${S4_EVIDENCE_DIR}" || -L "${S4_EVIDENCE_DIR}" ]]; then
    domeye_artifact_error \
        "S4 证据目录已存在，拒绝覆盖；失败现场应先独立归档：${S4_EVIDENCE_DIR}"
    exit 1
fi

install -d -m 0700 "${S4_EVIDENCE_DIR}"
readonly S4_MANIFEST="${S4_EVIDENCE_DIR}/static-info-manifest.json"
readonly S4_DETECTOR="${S4_EVIDENCE_DIR}/static-info-detector-ab.json"
readonly S4_PERFORMANCE="${S4_EVIDENCE_DIR}/static-info-performance.json"
readonly S4_SECURITY="${S4_EVIDENCE_DIR}/static-info-security.json"
readonly S4_OPERATIONS="${S4_EVIDENCE_DIR}/static-info-operations.json"
readonly S4_RECEIPT="${S4_EVIDENCE_DIR}/stage-gate-S4.json"
install -m 0600 "${S3_MANIFEST}" "${S4_MANIFEST}"

readonly PYTHON_BIN="$(
    domeye_static_info_python "${REPOSITORY_ROOT}"
)"
if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
    domeye_artifact_error '缺少可执行的 Python 3.10 S4 验收环境'
    exit 1
fi
if ! "${PYTHON_BIN}" -c \
    'import pandas, openpyxl, xlrd, psycopg2' >/dev/null 2>&1; then
    domeye_artifact_error \
        'S4 验收环境缺少 pandas、Excel 解析器或 psycopg2'
    exit 1
fi

readonly CONTENT_ID="$(jq -er '.content_id' "${S4_MANIFEST}")"
readonly MANIFEST_SHA256="$(jq -er '.manifest_sha256' "${S4_MANIFEST}")"
READER_SECRET="$(openssl rand -hex 32)"
SOCKET_LINK_ROOT=''
cleanup() {
    unset READER_SECRET
    if [[ -n "${SOCKET_LINK_ROOT}" \
        && -L "${SOCKET_LINK_ROOT}/pg" ]]; then
        unlink "${SOCKET_LINK_ROOT}/pg"
    fi
    if [[ -n "${SOCKET_LINK_ROOT}" \
        && -d "${SOCKET_LINK_ROOT}" ]]; then
        rmdir "${SOCKET_LINK_ROOT}"
    fi
}
trap cleanup EXIT

# 只在离线候选库更新既有运行角色。联系人、隔离、导入运行和原始记录表不会授权。
{
    printf '%s\n' \
        'CREATE TEMP TABLE pg_temp.domeye_reader_secret(value text NOT NULL);'
    printf "INSERT INTO pg_temp.domeye_reader_secret(value) VALUES ('%s');\n" \
        "${READER_SECRET}"
    sed -n '1,$p' "${READER_SQL}"
} | docker exec -i "${CANDIDATE_CONTAINER}" \
    psql \
    -U "${DATABASE_ADMIN}" \
    -d "${DATABASE_NAME}" \
    -X \
    -v "reader_role=${RUNTIME_ROLE}" \
    -v "database_name=${DATABASE_NAME}" \
    >/dev/null

readonly CANDIDATE_PID="$(
    docker inspect -f '{{.State.Pid}}' "${CANDIDATE_CONTAINER}"
)"
if [[ ! "${CANDIDATE_PID}" =~ ^[1-9][0-9]*$ ]]; then
    domeye_artifact_error '候选数据库容器 PID 无效'
    exit 1
fi
readonly CANDIDATE_MERGED_DIR="$(
    docker inspect -f '{{index .GraphDriver.Data "MergedDir"}}' \
        "${CANDIDATE_CONTAINER}"
)"
readonly CANDIDATE_SOCKET_TARGET="${CANDIDATE_MERGED_DIR}/run/postgresql"
if [[ -z "${CANDIDATE_MERGED_DIR}" \
    || ! -S "${CANDIDATE_SOCKET_TARGET}/.s.PGSQL.5432" ]]; then
    domeye_artifact_error '找不到候选数据库 Unix Socket'
    exit 1
fi
SOCKET_LINK_ROOT="$(mktemp -d /tmp/domeye-info-s4.XXXXXX)"
ln -s "${CANDIDATE_SOCKET_TARGET}" "${SOCKET_LINK_ROOT}/pg"
readonly CANDIDATE_SOCKET_DIR="${SOCKET_LINK_ROOT}/pg"

(
    cd -- "${REPOSITORY_ROOT}"
    env \
        INFO_DIR="${SOURCE_INFO_DIR}" \
        PGPASSWORD="${READER_SECRET}" \
        PYTHONDONTWRITEBYTECODE=1 \
        PYTHONPATH="${REPOSITORY_ROOT}:${CORE_BACKEND_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
        "${PYTHON_BIN}" \
        -m backend.info_pipeline.s4 acceptance \
        --source-dir "${SOURCE_INFO_DIR}" \
        --manifest "${S4_MANIFEST}" \
        --core-backend-root "${CORE_BACKEND_ROOT}" \
        --db-host "${CANDIDATE_SOCKET_DIR}" \
        --db-port 5432 \
        --db-name "${DATABASE_NAME}" \
        --db-user "${RUNTIME_ROLE}" \
        --db-admin "${DATABASE_ADMIN}" \
        --container "${CANDIDATE_CONTAINER}" \
        --production-container bgp_project_pg \
        --production-container domeye_core_dev_pg \
        --content-id "${CONTENT_ID}" \
        --manifest-sha256 "${MANIFEST_SHA256}" \
        --output-dir "${S4_EVIDENCE_DIR}"
)

if ! jq -e \
    '.status == "pass"
     and .event_type_count == 6
     and .unapproved_difference_count == 0
     and .core_hash_unchanged == true' \
    "${S4_DETECTOR}" >/dev/null; then
    domeye_artifact_error 'S4 六类检测 A/B 未通过'
    exit 1
fi
if ! jq -e \
    '.status == "pass"
     and .exact_query_p95_ms <= 20
     and .exact_query_p99_ms <= 50
     and .longest_prefix_match_p95_ms <= 30
     and .snapshot_load_time_regression_percent <= 10
     and .snapshot_peak_rss_regression_percent <= 10
     and .detector_throughput_regression_percent <= 5
     and .request_path_full_table_load_count == 0
     and .capacity_status == "pass"' \
    "${S4_PERFORMANCE}" >/dev/null; then
    domeye_artifact_error 'S4 性能或容量门禁未通过'
    exit 1
fi
if ! jq -e \
    '.status == "pass"
     and .unauthorized_write_success_count == 0
     and .contact_plaintext_exposure_count == 0
     and .check_production_side_effect_count == 0
     and .runtime_role_read_only == true' \
    "${S4_SECURITY}" >/dev/null; then
    domeye_artifact_error 'S4 权限或隐私门禁未通过'
    exit 1
fi
if ! jq -e \
    '.status == "pass"
     and .release_state_observable == true
     and .per_file_counts_observable == true
     and .checkpoint_resumable == true
     and .same_input_reproducible == true
     and .activated == false' \
    "${S4_OPERATIONS}" >/dev/null; then
    domeye_artifact_error 'S4 运维可观测性门禁未通过'
    exit 1
fi

"${REPOSITORY_ROOT}/deploy/database/static-info-stage-end-hook.sh" \
    S4 \
    "${S4_EVIDENCE_DIR}" \
    "${S4_RECEIPT}" \
    "${S3_RECEIPT}"

(
    cd -- "${S4_EVIDENCE_DIR}"
    sha256sum \
        static-info-manifest.json \
        static-info-detector-ab.json \
        static-info-performance.json \
        static-info-security.json \
        static-info-operations.json \
        stage-gate-S4.json \
        > SHA256SUMS
)
chmod 0600 \
    "${S4_MANIFEST}" \
    "${S4_DETECTOR}" \
    "${S4_PERFORMANCE}" \
    "${S4_SECURITY}" \
    "${S4_OPERATIONS}" \
    "${S4_RECEIPT}" \
    "${S4_EVIDENCE_DIR}/SHA256SUMS"

printf 'static INFO S4 检测与非功能边界已闭合；未激活：%s\n' \
    "${S4_EVIDENCE_DIR}"
