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
        "用法：${0##*/} <只读旧INFO目录> <当前四文件制品目录> <候选容器> <数据库管理员> <数据库名> <Core后端目录> <S5证据目录> <运行状态目录> <S6证据目录>" \
        >&2
}

if (( $# != 9 )); then
    usage
    exit 2
fi

readonly SOURCE_INFO_DIR="$1"
readonly CURRENT_INFO_ARTIFACT_DIR="$2"
readonly CANDIDATE_CONTAINER="$3"
readonly DATABASE_ADMIN="$4"
readonly DATABASE_NAME="$5"
readonly CORE_BACKEND_ROOT="$6"
readonly S5_EVIDENCE_DIR="$7"
readonly STATE_DIR="$8"
readonly S6_EVIDENCE_DIR="$9"
readonly EVIDENCE_ROOT="$(cd -- "${S5_EVIDENCE_DIR}/.." && pwd)"
readonly RUNTIME_ROLE="domeye_core_reader"
readonly READER_SQL="${REPOSITORY_ROOT}/deploy/database/sql/create-reader.sql"

for command_name in docker install jq sha256sum openssl mktemp ln unlink \
    rmdir sed chmod strace find; do
    domeye_artifact_require_command "${command_name}"
done
for directory in \
    "${SOURCE_INFO_DIR}" \
    "${CORE_BACKEND_ROOT}" \
    "${S5_EVIDENCE_DIR}" \
    "${STATE_DIR}" \
    "${EVIDENCE_ROOT}"; do
    if [[ ! -d "${directory}" || -L "${directory}" ]]; then
        domeye_artifact_error "S6 输入目录无效或为软链接：${directory}"
        exit 1
    fi
done
for required_path in \
    "${CORE_BACKEND_ROOT}/core.sha256" \
    "${S5_EVIDENCE_DIR}/static-info-manifest.json" \
    "${S5_EVIDENCE_DIR}/static-info-release-acceptance.json" \
    "${S5_EVIDENCE_DIR}/stage-gate-S5.json" \
    "${S5_EVIDENCE_DIR}/SHA256SUMS" \
    "${STATE_DIR}/backend-state.json" \
    "${READER_SQL}"; do
    domeye_artifact_require_regular_file "${required_path}"
done
domeye_static_info_assert_offline_candidate "${CANDIDATE_CONTAINER}"

(
    cd -- "${S5_EVIDENCE_DIR}"
    sha256sum -c SHA256SUMS
)
readonly S5_MANIFEST="${S5_EVIDENCE_DIR}/static-info-manifest.json"
readonly S5_RECEIPT="${S5_EVIDENCE_DIR}/stage-gate-S5.json"
if ! jq -e \
    --slurpfile manifest "${S5_MANIFEST}" \
    '.component == "static_info_stage_gate"
     and .stage_id == "S5"
     and .status == "pass"
     and .deviation_count == 0
     and .subject.content_id == $manifest[0].content_id
     and .subject.manifest_sha256 == $manifest[0].manifest_sha256' \
    "${S5_RECEIPT}" >/dev/null; then
    domeye_artifact_error 'S5 回执与 manifest 不一致或未通过'
    exit 1
fi
if [[ -e "${S6_EVIDENCE_DIR}" || -L "${S6_EVIDENCE_DIR}" ]]; then
    domeye_artifact_error \
        "S6 证据目录已存在，拒绝覆盖：${S6_EVIDENCE_DIR}"
    exit 1
fi

install -d -m 0700 "${S6_EVIDENCE_DIR}"
readonly TRACE_DIR="${S6_EVIDENCE_DIR}/traces"
install -d -m 0700 "${TRACE_DIR}"
readonly S6_MANIFEST="${S6_EVIDENCE_DIR}/static-info-manifest.json"
readonly S6_CLOSURE="${S6_EVIDENCE_DIR}/static-info-closure.json"
readonly S6_RECEIPT="${S6_EVIDENCE_DIR}/stage-gate-S6.json"
readonly TRACE_SUMS="${S6_EVIDENCE_DIR}/static-info-runtime-traces.sha256"
install -m 0600 "${S5_MANIFEST}" "${S6_MANIFEST}"

READER_SECRET=''
SOCKET_LINK_ROOT=''
S6_COMPLETED=false
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
    if [[ "${S6_COMPLETED}" != true \
        && -d "${S6_EVIDENCE_DIR}" \
        && ! -L "${S6_EVIDENCE_DIR}" ]]; then
        domeye_static_info_archive_incomplete_evidence "${S6_EVIDENCE_DIR}" \
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
    domeye_artifact_error '缺少可执行的 Python 3.10 S6 验收环境'
    exit 1
fi
if ! "${PYTHON_BIN}" -c \
    'import psycopg2' >/dev/null 2>&1; then
    domeye_artifact_error 'S6 验收环境缺少 psycopg2'
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
SOCKET_LINK_ROOT="$(mktemp -d /tmp/domeye-info-s6.XXXXXX)"
ln -s "${CANDIDATE_SOCKET_TARGET}" "${SOCKET_LINK_ROOT}/pg"
readonly CANDIDATE_SOCKET_DIR="${SOCKET_LINK_ROOT}/pg"

(
    cd -- "${REPOSITORY_ROOT}"
    env \
        PGPASSWORD="${READER_SECRET}" \
        PYTHONDONTWRITEBYTECODE=1 \
        PYTHONPATH="${REPOSITORY_ROOT}:${CORE_BACKEND_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
        "${PYTHON_BIN}" \
        -m backend.info_pipeline.s6 \
        --source-dir "${SOURCE_INFO_DIR}" \
        --current-info-artifact-dir "${CURRENT_INFO_ARTIFACT_DIR}" \
        --manifest "${S6_MANIFEST}" \
        --repository-root "${REPOSITORY_ROOT}" \
        --core-backend-root "${CORE_BACKEND_ROOT}" \
        --evidence-root "${EVIDENCE_ROOT}" \
        --s5-evidence-dir "${S5_EVIDENCE_DIR}" \
        --state-dir "${STATE_DIR}" \
        --trace-dir "${TRACE_DIR}" \
        --db-host "${CANDIDATE_SOCKET_DIR}" \
        --db-port 5432 \
        --db-name "${DATABASE_NAME}" \
        --db-reader "${RUNTIME_ROLE}" \
        --db-admin "${DATABASE_ADMIN}" \
        --container "${CANDIDATE_CONTAINER}" \
        --production-container bgp_project_pg \
        --production-container domeye_core_dev_pg \
        --minimum-process-runs 12 \
        --minimum-observation-seconds 60 \
        --output "${S6_CLOSURE}"
)

if ! jq -e \
    '.status == "pass"
     and .final_acceptance_status == "pass"
     and .passed_requirement_count == 12
     and .runtime_direct_info_file_read_count == 0
     and .legacy_database_connection_count == 0
     and .current_release_available == true
     and .previous_release_available == true
     and .file_rollback_artifact_available == true
     and .observation_period_complete == true
     and .referenced_content_preserved == true
     and .ordinary_runtime.implicit_file_fallback == false
     and .ordinary_runtime.process_kinds_complete == true
     and .ordinary_runtime.mixed_content_run_count == 0
     and .production_side_effect_count == 0
     and .cleanup_performed == false' \
    "${S6_CLOSURE}" >/dev/null; then
    domeye_artifact_error 'S6 运行时收口或最终门禁未通过'
    exit 1
fi

(
    cd -- "${TRACE_DIR}"
    find . -maxdepth 1 -type f -name '*.strace' -print0 \
        | sort -z \
        | xargs -0 sha256sum \
        > "${TRACE_SUMS}"
)
(
    cd -- "${TRACE_DIR}"
    sha256sum -c "${TRACE_SUMS}"
)

"${REPOSITORY_ROOT}/deploy/database/static-info-stage-end-hook.sh" \
    S6 \
    "${S6_EVIDENCE_DIR}" \
    "${S6_RECEIPT}" \
    "${S5_RECEIPT}"

(
    cd -- "${S6_EVIDENCE_DIR}"
    sha256sum \
        static-info-manifest.json \
        static-info-closure.json \
        static-info-runtime-traces.sha256 \
        stage-gate-S6.json \
        > SHA256SUMS
)
chmod 0600 \
    "${S6_MANIFEST}" \
    "${S6_CLOSURE}" \
    "${TRACE_SUMS}" \
    "${S6_RECEIPT}" \
    "${S6_EVIDENCE_DIR}/SHA256SUMS"

S6_COMPLETED=true
printf 'static INFO S6 运行时收口及最终验收已闭合：%s\n' \
    "${S6_EVIDENCE_DIR}"
