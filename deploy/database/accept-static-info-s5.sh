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
        "用法：${0##*/} <只读INFO目录> <候选容器> <数据库管理员> <数据库名> <Core后端目录> <S4证据目录> <运行状态目录> <S5证据目录> <授权ID> <确认content_id>" \
        >&2
}

if (( $# != 10 )); then
    usage
    exit 2
fi

readonly SOURCE_INFO_DIR="$1"
readonly CANDIDATE_CONTAINER="$2"
readonly DATABASE_ADMIN="$3"
readonly DATABASE_NAME="$4"
readonly CORE_BACKEND_ROOT="$5"
readonly S4_EVIDENCE_DIR="$6"
readonly STATE_DIR="$7"
readonly S5_EVIDENCE_DIR="$8"
readonly AUTHORIZATION_ID="$9"
readonly CONFIRMED_CONTENT_ID="${10}"
readonly EVIDENCE_ROOT="$(cd -- "${S4_EVIDENCE_DIR}/.." && pwd)"
readonly RUNTIME_ROLE="domeye_core_reader"
readonly READER_SQL="${REPOSITORY_ROOT}/deploy/database/sql/create-reader.sql"

for command_name in docker install jq sha256sum openssl mktemp ln readlink \
    unlink rmdir sed chmod mv; do
    domeye_artifact_require_command "${command_name}"
done
for directory in \
    "${SOURCE_INFO_DIR}" \
    "${CORE_BACKEND_ROOT}" \
    "${S4_EVIDENCE_DIR}" \
    "${EVIDENCE_ROOT}"; do
    if [[ ! -d "${directory}" || -L "${directory}" ]]; then
        domeye_artifact_error "S5 输入目录无效或为软链接：${directory}"
        exit 1
    fi
done
for required_path in \
    "${CORE_BACKEND_ROOT}/core.sha256" \
    "${S4_EVIDENCE_DIR}/static-info-manifest.json" \
    "${S4_EVIDENCE_DIR}/static-info-detector-ab.json" \
    "${S4_EVIDENCE_DIR}/stage-gate-S4.json" \
    "${S4_EVIDENCE_DIR}/SHA256SUMS" \
    "${READER_SQL}"; do
    domeye_artifact_require_regular_file "${required_path}"
done
domeye_static_info_assert_offline_candidate "${CANDIDATE_CONTAINER}"

(
    cd -- "${S4_EVIDENCE_DIR}"
    sha256sum -c SHA256SUMS
)
readonly S4_MANIFEST="${S4_EVIDENCE_DIR}/static-info-manifest.json"
readonly S4_RECEIPT="${S4_EVIDENCE_DIR}/stage-gate-S4.json"
if ! jq -e \
    --slurpfile manifest "${S4_MANIFEST}" \
    '.component == "static_info_stage_gate"
     and .stage_id == "S4"
     and .status == "pass"
     and .deviation_count == 0
     and .subject.content_id == $manifest[0].content_id
     and .subject.manifest_sha256 == $manifest[0].manifest_sha256' \
    "${S4_RECEIPT}" >/dev/null; then
    domeye_artifact_error 'S4 回执与 manifest 不一致或未通过'
    exit 1
fi
readonly CONTENT_ID="$(jq -er '.content_id' "${S4_MANIFEST}")"
if [[ "${AUTHORIZATION_ID}" == '' \
    || "${CONFIRMED_CONTENT_ID}" != "${CONTENT_ID}" ]]; then
    domeye_artifact_error 'S5 要求非空授权 ID 和逐字匹配的 content_id 确认'
    exit 1
fi
if [[ -e "${S5_EVIDENCE_DIR}" || -L "${S5_EVIDENCE_DIR}" ]]; then
    domeye_artifact_error \
        "S5 证据目录已存在，拒绝覆盖；失败现场应独立归档：${S5_EVIDENCE_DIR}"
    exit 1
fi
if [[ -e "${STATE_DIR}" && ( -L "${STATE_DIR}" || ! -d "${STATE_DIR}" ) ]]; then
    domeye_artifact_error "运行状态路径不是实际目录：${STATE_DIR}"
    exit 1
fi

install -d -m 0700 "${STATE_DIR}" "${S5_EVIDENCE_DIR}"
readonly S5_MANIFEST="${S5_EVIDENCE_DIR}/static-info-manifest.json"
readonly S5_ACCEPTANCE="${S5_EVIDENCE_DIR}/static-info-release-acceptance.json"
readonly S5_RECEIPT="${S5_EVIDENCE_DIR}/stage-gate-S5.json"
install -m 0600 "${S4_MANIFEST}" "${S5_MANIFEST}"

READER_SECRET=''
SOCKET_LINK_ROOT=''
S5_COMPLETED=false
cleanup() {
    local exit_code=$?
    unset READER_SECRET
    if [[ -n "${SOCKET_LINK_ROOT}" \
        && -L "${SOCKET_LINK_ROOT}/pg" ]]; then
        unlink "${SOCKET_LINK_ROOT}/pg"
    fi
    if [[ -n "${SOCKET_LINK_ROOT}" \
        && -d "${SOCKET_LINK_ROOT}" ]]; then
        rmdir "${SOCKET_LINK_ROOT}"
    fi
    if [[ "${S5_COMPLETED}" != true \
        && -d "${S5_EVIDENCE_DIR}" \
        && ! -L "${S5_EVIDENCE_DIR}" ]]; then
        domeye_static_info_archive_incomplete_evidence "${S5_EVIDENCE_DIR}" \
            || true
    fi
    exit "${exit_code}"
}
trap cleanup EXIT

PYTHON_CANDIDATE="${DOMEYE_CORE_INFO_PYTHON:-${CORE_BACKEND_ROOT}/.venv/bin/python}"
if [[ -x "${PYTHON_CANDIDATE}" ]]; then
    readonly PYTHON_BIN="${PYTHON_CANDIDATE}"
else
    readonly PYTHON_BIN="$(
        domeye_static_info_python "${REPOSITORY_ROOT}"
    )"
fi
if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
    domeye_artifact_error '缺少可执行的 Python 3.10 S5 验收环境'
    exit 1
fi
if ! "${PYTHON_BIN}" -c \
    'import pandas, openpyxl, xlrd, psycopg2' >/dev/null 2>&1; then
    domeye_artifact_error \
        'S5 验收环境缺少 pandas、Excel 解析器或 psycopg2'
    exit 1
fi

READER_SECRET="$(openssl rand -hex 32)"

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
SOCKET_LINK_ROOT="$(mktemp -d /tmp/domeye-info-s5.XXXXXX)"
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
        -m backend.info_pipeline.s5 \
        --source-dir "${SOURCE_INFO_DIR}" \
        --manifest "${S5_MANIFEST}" \
        --core-backend-root "${CORE_BACKEND_ROOT}" \
        --evidence-root "${EVIDENCE_ROOT}" \
        --s4-evidence-dir "${S4_EVIDENCE_DIR}" \
        --state-dir "${STATE_DIR}" \
        --db-host "${CANDIDATE_SOCKET_DIR}" \
        --db-port 5432 \
        --db-name "${DATABASE_NAME}" \
        --db-reader "${RUNTIME_ROLE}" \
        --db-admin "${DATABASE_ADMIN}" \
        --container "${CANDIDATE_CONTAINER}" \
        --production-container bgp_project_pg \
        --production-container domeye_core_dev_pg \
        --authorization-id "${AUTHORIZATION_ID}" \
        --confirm-content-id "${CONFIRMED_CONTENT_ID}" \
        --output "${S5_ACCEPTANCE}"
)

if ! jq -e \
    --arg content_id "${CONTENT_ID}" \
    '.status == "pass"
     and .active_content_id == $content_id
     and .activation_authorized == true
     and .authorization.scope == "isolated_offline_candidate_only"
     and .authorization.production_activation_authorized == false
     and .activated == true
     and .safe_boundary_observed == true
     and .mixed_content_run_count == 0
     and .rollback_tested == true
     and .previous_release_available == true
     and .failure_evidence_preserved == true
     and .business_data_unchanged == true
     and .production_side_effect_count == 0' \
    "${S5_ACCEPTANCE}" >/dev/null; then
    domeye_artifact_error 'S5 受控激活、单 release 或回滚门禁未通过'
    exit 1
fi

"${REPOSITORY_ROOT}/deploy/database/static-info-stage-end-hook.sh" \
    S5 \
    "${S5_EVIDENCE_DIR}" \
    "${S5_RECEIPT}" \
    "${S4_RECEIPT}"

(
    cd -- "${S5_EVIDENCE_DIR}"
    sha256sum \
        static-info-manifest.json \
        static-info-release-acceptance.json \
        stage-gate-S5.json \
        > SHA256SUMS
)
chmod 0600 \
    "${S5_MANIFEST}" \
    "${S5_ACCEPTANCE}" \
    "${S5_RECEIPT}" \
    "${S5_EVIDENCE_DIR}/SHA256SUMS"

S5_COMPLETED=true
printf 'static INFO S5 受控激活与文件回滚边界已闭合：%s\n' \
    "${S5_EVIDENCE_DIR}"
