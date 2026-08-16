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
        "用法：${0##*/} <只读INFO目录> <候选容器> <数据库管理员> <数据库名> <S1证据目录> <S2证据目录>" \
        >&2
}

if (( $# != 6 )); then
    usage
    exit 2
fi

readonly SOURCE_INFO_DIR="$1"
readonly CANDIDATE_CONTAINER="$2"
readonly DATABASE_USER="$3"
readonly DATABASE_NAME="$4"
readonly S1_EVIDENCE_DIR="$5"
readonly S2_EVIDENCE_DIR="$6"

for command_name in docker install jq sha256sum; do
    domeye_artifact_require_command "${command_name}"
done
if [[ ! -d "${SOURCE_INFO_DIR}" || -L "${SOURCE_INFO_DIR}" ]]; then
    domeye_artifact_error \
        "static INFO 来源必须是实际目录且禁止软链接：${SOURCE_INFO_DIR}"
    exit 1
fi
if [[ ! -d "${S1_EVIDENCE_DIR}" || -L "${S1_EVIDENCE_DIR}" ]]; then
    domeye_artifact_error "S1 证据目录无效：${S1_EVIDENCE_DIR}"
    exit 1
fi
domeye_static_info_assert_offline_candidate "${CANDIDATE_CONTAINER}"

readonly S1_MANIFEST="${S1_EVIDENCE_DIR}/static-info-manifest.json"
readonly S1_RECEIPT="${S1_EVIDENCE_DIR}/stage-gate-S1.json"
readonly S1_CHECKSUMS="${S1_EVIDENCE_DIR}/SHA256SUMS"
for required_path in "${S1_MANIFEST}" "${S1_RECEIPT}" "${S1_CHECKSUMS}"; do
    domeye_artifact_require_regular_file "${required_path}"
done
(
    cd -- "${S1_EVIDENCE_DIR}"
    sha256sum -c SHA256SUMS
)
if ! jq -e \
    --slurpfile manifest "${S1_MANIFEST}" \
    '.component == "static_info_stage_gate"
     and .stage_id == "S1"
     and .status == "pass"
     and .deviation_count == 0
     and .subject.content_id == $manifest[0].content_id
     and .subject.manifest_sha256 == $manifest[0].manifest_sha256' \
    "${S1_RECEIPT}" >/dev/null; then
    domeye_artifact_error 'S1 回执与 manifest 不一致或未通过'
    exit 1
fi

if [[ -e "${S2_EVIDENCE_DIR}" || -L "${S2_EVIDENCE_DIR}" ]]; then
    reuse_status=0
    domeye_static_info_reuse_s2_evidence \
        "${REPOSITORY_ROOT}" \
        "${SOURCE_INFO_DIR}" \
        "${CANDIDATE_CONTAINER}" \
        "${DATABASE_USER}" \
        "${DATABASE_NAME}" \
        "${S1_EVIDENCE_DIR}" \
        "${S2_EVIDENCE_DIR}" \
        || reuse_status=$?
    case "${reuse_status}" in
        0)
            printf 'static INFO S2 全量候选已复核；未激活：%s\n' \
                "${S2_EVIDENCE_DIR}"
            exit 0
            ;;
        1) ;;
        *) exit "${reuse_status}" ;;
    esac
fi

install -d -m 0700 "${S2_EVIDENCE_DIR}"
readonly SPOOL_DIR="${S2_EVIDENCE_DIR}/.spool"
install -d -m 0700 "${SPOOL_DIR}"
readonly S2_MANIFEST="${S2_EVIDENCE_DIR}/static-info-manifest.json"
readonly S2_QUALITY="${S2_EVIDENCE_DIR}/static-info-full-quality.json"
readonly S2_RESULT="${S2_EVIDENCE_DIR}/static-info-full-load-result.json"
readonly S2_RECEIPT="${S2_EVIDENCE_DIR}/stage-gate-S2.json"
install -m 0600 "${S1_MANIFEST}" "${S2_MANIFEST}"

readonly PYTHON_BIN="$(
    domeye_static_info_python "${REPOSITORY_ROOT}"
)"
if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
    domeye_artifact_error '缺少可执行的 Python 3.10 INFO 导入环境'
    exit 1
fi
if ! "${PYTHON_BIN}" -c 'import openpyxl, xlrd' >/dev/null 2>&1; then
    domeye_artifact_error 'INFO 导入 Python 环境缺少 openpyxl 或 xlrd'
    exit 1
fi

(
    cd -- "${REPOSITORY_ROOT}"
    DOMEYE_CORE_INFO_SPOOL_DIR="${SPOOL_DIR}" \
    PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" \
        -m backend.info_pipeline load-full \
        --source-dir "${SOURCE_INFO_DIR}" \
        --manifest "${S2_MANIFEST}" \
        --container "${CANDIDATE_CONTAINER}" \
        --db-user "${DATABASE_USER}" \
        --db-name "${DATABASE_NAME}" \
        --quality-output "${S2_QUALITY}" \
        --result "${S2_RESULT}"
)
rmdir "${SPOOL_DIR}"

if ! jq -e \
    '(.status == "completed" or .status == "already_completed")
     and .scope == "all_24_files"
     and .activated == false
     and .database_release_status == "validating"
     and .source_file_count == 24
     and .reconciled_source_file_count == 24
     and .unreconciled_record_count == 0
     and .visible_record_traceability_percent == 100
     and .quarantine_reason_coverage_percent == 100' \
    "${S2_RESULT}" >/dev/null; then
    domeye_artifact_error 'S2 导入结果未达到 24 文件闭合边界'
    exit 1
fi

"${REPOSITORY_ROOT}/deploy/database/static-info-stage-end-hook.sh" \
    S2 \
    "${S2_EVIDENCE_DIR}" \
    "${S2_RECEIPT}" \
    "${S1_RECEIPT}"

(
    cd -- "${S2_EVIDENCE_DIR}"
    sha256sum \
        static-info-manifest.json \
        static-info-full-quality.json \
        static-info-full-load-result.json \
        stage-gate-S2.json \
        > SHA256SUMS
)
chmod 0600 \
    "${S2_MANIFEST}" \
    "${S2_QUALITY}" \
    "${S2_RESULT}" \
    "${S2_RECEIPT}" \
    "${S2_EVIDENCE_DIR}/SHA256SUMS"

printf 'static INFO S2 全量候选已闭合；未激活：%s\n' "${S2_EVIDENCE_DIR}"
