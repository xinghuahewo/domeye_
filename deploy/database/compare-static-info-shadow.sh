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
        "用法：${0##*/} <只读INFO目录> <候选容器> <数据库管理员> <数据库名> <S2证据目录> <S3证据目录>" \
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
readonly S2_EVIDENCE_DIR="$5"
readonly S3_EVIDENCE_DIR="$6"

for command_name in docker install jq sha256sum; do
    domeye_artifact_require_command "${command_name}"
done
if [[ ! -d "${SOURCE_INFO_DIR}" || -L "${SOURCE_INFO_DIR}" ]]; then
    domeye_artifact_error \
        "static INFO 来源必须是实际目录且禁止软链接：${SOURCE_INFO_DIR}"
    exit 1
fi
if [[ ! -d "${S2_EVIDENCE_DIR}" || -L "${S2_EVIDENCE_DIR}" ]]; then
    domeye_artifact_error "S2 证据目录无效：${S2_EVIDENCE_DIR}"
    exit 1
fi
domeye_static_info_assert_offline_candidate "${CANDIDATE_CONTAINER}"

readonly S2_MANIFEST="${S2_EVIDENCE_DIR}/static-info-manifest.json"
readonly S2_RECEIPT="${S2_EVIDENCE_DIR}/stage-gate-S2.json"
readonly S2_CHECKSUMS="${S2_EVIDENCE_DIR}/SHA256SUMS"
for required_path in "${S2_MANIFEST}" "${S2_RECEIPT}" "${S2_CHECKSUMS}"; do
    domeye_artifact_require_regular_file "${required_path}"
done
(
    cd -- "${S2_EVIDENCE_DIR}"
    sha256sum -c SHA256SUMS
)
if ! jq -e \
    --slurpfile manifest "${S2_MANIFEST}" \
    '.component == "static_info_stage_gate"
     and .stage_id == "S2"
     and .status == "pass"
     and .deviation_count == 0
     and .subject.content_id == $manifest[0].content_id
     and .subject.manifest_sha256 == $manifest[0].manifest_sha256' \
    "${S2_RECEIPT}" >/dev/null; then
    domeye_artifact_error 'S2 回执与 manifest 不一致或未通过'
    exit 1
fi
if [[ -e "${S3_EVIDENCE_DIR}" || -L "${S3_EVIDENCE_DIR}" ]]; then
    domeye_artifact_error \
        "S3 证据目录已存在，拒绝覆盖；失败现场应先独立归档：${S3_EVIDENCE_DIR}"
    exit 1
fi

install -d -m 0700 "${S3_EVIDENCE_DIR}"
readonly S3_MANIFEST="${S3_EVIDENCE_DIR}/static-info-manifest.json"
readonly S3_DIFF="${S3_EVIDENCE_DIR}/static-info-shadow-diff.json"
readonly S3_RECEIPT="${S3_EVIDENCE_DIR}/stage-gate-S3.json"
install -m 0600 "${S2_MANIFEST}" "${S3_MANIFEST}"

readonly PYTHON_BIN="$(
    domeye_static_info_python "${REPOSITORY_ROOT}"
)"
if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
    domeye_artifact_error '缺少可执行的 Python 3.10 INFO 影子对账环境'
    exit 1
fi
if ! "${PYTHON_BIN}" -c 'import openpyxl, xlrd' >/dev/null 2>&1; then
    domeye_artifact_error 'INFO 影子对账环境缺少 openpyxl 或 xlrd'
    exit 1
fi

(
    cd -- "${REPOSITORY_ROOT}"
    PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" \
        -m backend.info_pipeline shadow-diff \
        --source-dir "${SOURCE_INFO_DIR}" \
        --manifest "${S3_MANIFEST}" \
        --container "${CANDIDATE_CONTAINER}" \
        --db-user "${DATABASE_USER}" \
        --db-name "${DATABASE_NAME}" \
        --output "${S3_DIFF}"
)

if ! jq -e \
    '.status == "pass"
     and .scope == "all_static_queries_and_snapshot"
     and .deterministic_query_unapproved_difference_count == 0
     and .full_set_unapproved_difference_count == 0
     and .snapshot_unapproved_difference_count == 0
     and .contact_plaintext_exposure_count == 0
     and .activated == false
     and ([.sections[].status] | all(. == "pass"))' \
    "${S3_DIFF}" >/dev/null; then
    domeye_artifact_error 'S3 文件/数据库影子语义对账未通过'
    exit 1
fi

"${REPOSITORY_ROOT}/deploy/database/static-info-stage-end-hook.sh" \
    S3 \
    "${S3_EVIDENCE_DIR}" \
    "${S3_RECEIPT}" \
    "${S2_RECEIPT}"

(
    cd -- "${S3_EVIDENCE_DIR}"
    sha256sum \
        static-info-manifest.json \
        static-info-shadow-diff.json \
        stage-gate-S3.json \
        > SHA256SUMS
)
chmod 0600 \
    "${S3_MANIFEST}" \
    "${S3_DIFF}" \
    "${S3_RECEIPT}" \
    "${S3_EVIDENCE_DIR}/SHA256SUMS"

printf 'static INFO S3 查询与快照语义已闭合；未激活：%s\n' \
    "${S3_EVIDENCE_DIR}"
