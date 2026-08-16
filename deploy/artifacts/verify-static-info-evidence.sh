#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"

if (( $# != 2 )); then
    printf '用法：%s <static INFO 证据包> <core_four_files|all_24_files>\n' \
        "${0##*/}" >&2
    exit 2
fi

readonly ARCHIVE_PATH="$1"
readonly SCOPE="$2"
domeye_artifact_require_regular_file "${ARCHIVE_PATH}"
if [[ "${SCOPE}" != 'core_four_files' && "${SCOPE}" != 'all_24_files' ]]; then
    domeye_artifact_error "static INFO 证据 scope 无效：${SCOPE}"
    exit 2
fi
for command_name in chmod mktemp rm sha256sum tar zstd; do
    domeye_artifact_require_command "${command_name}"
done

readonly VERIFY_ROOT="$(mktemp -d "${ARCHIVE_PATH%/*}/.static-info-evidence-verify.XXXXXX")"
cleanup() {
    if [[ -d "${VERIFY_ROOT}" && ! -L "${VERIFY_ROOT}" \
        && "${VERIFY_ROOT}" == "${ARCHIVE_PATH%/*}/.static-info-evidence-verify."* ]]; then
        rm -rf -- "${VERIFY_ROOT}"
    fi
}
trap cleanup EXIT

members=(
    static-info/static-info-manifest.json
    static-info/static-info-quality.json
    static-info/static-info-load-result.json
    static-info/stage-gate-S0.json
    static-info/stage-gate-S1.json
    static-info/SHA256SUMS
    static-info/contract-root/docs/INFO目录数据落库最终验收文档.md
    static-info/contract-root/docs/INFO目录数据落库分阶段计划.md
    static-info/contract-root/contracts/info/static-info-final-acceptance-v1.json
    static-info/contract-root/SHA256SUMS
)
if [[ "${SCOPE}" == 'all_24_files' ]]; then
    members+=(
        static-info/S2/static-info-manifest.json
        static-info/S2/static-info-full-quality.json
        static-info/S2/static-info-full-load-result.json
        static-info/S2/stage-gate-S2.json
        static-info/S2/SHA256SUMS
    )
fi

zstd --quiet --decompress --stdout "${ARCHIVE_PATH}" \
    | tar --extract --file=- \
        --directory "${VERIFY_ROOT}" \
        --no-same-owner \
        --no-same-permissions \
        "${members[@]}"

for member in "${members[@]}"; do
    domeye_artifact_require_regular_file "${VERIFY_ROOT}/${member}"
done
readonly EVIDENCE_DIR="${VERIFY_ROOT}/static-info"
readonly CONTRACT_ROOT="${EVIDENCE_DIR}/contract-root"
(
    cd -- "${EVIDENCE_DIR}"
    sha256sum -c SHA256SUMS >/dev/null
)
(
    cd -- "${CONTRACT_ROOT}"
    sha256sum -c SHA256SUMS >/dev/null
)
if [[ "${SCOPE}" == 'all_24_files' ]]; then
    (
        cd -- "${EVIDENCE_DIR}/S2"
        sha256sum -c SHA256SUMS >/dev/null
    )
fi

python_candidate="${DOMEYE_CORE_INFO_PYTHON:-${REPOSITORY_ROOT}/backend/.venv/bin/python}"
if [[ -x "${python_candidate}" ]]; then
    python_bin="${python_candidate}"
elif command -v python3 >/dev/null 2>&1; then
    python_bin="$(command -v python3)"
else
    domeye_artifact_error '缺少可执行的 Python 3，无法复核 INFO 阶段回执'
    exit 2
fi
readonly MACHINE_CONTRACT="${CONTRACT_ROOT}/contracts/info/static-info-final-acceptance-v1.json"
readonly S1_REVERIFY="${EVIDENCE_DIR}/.stage-gate-S1-reverify.json"
(
    cd -- "${REPOSITORY_ROOT}"
    PYTHONDONTWRITEBYTECODE=1 "${python_bin}" \
        -m backend.info_pipeline.stage_gate \
        --stage S1 \
        --evidence-dir "${EVIDENCE_DIR}" \
        --output "${S1_REVERIFY}" \
        --previous-receipt "${EVIDENCE_DIR}/stage-gate-S0.json" \
        --repository-root "${CONTRACT_ROOT}" \
        --contract "${MACHINE_CONTRACT}" \
        >/dev/null
)
rm -f -- "${S1_REVERIFY}"

if [[ "${SCOPE}" == 'all_24_files' ]]; then
    readonly S2_REVERIFY="${EVIDENCE_DIR}/S2/.stage-gate-S2-reverify.json"
    (
        cd -- "${REPOSITORY_ROOT}"
        PYTHONDONTWRITEBYTECODE=1 "${python_bin}" \
            -m backend.info_pipeline.stage_gate \
            --stage S2 \
            --evidence-dir "${EVIDENCE_DIR}/S2" \
            --output "${S2_REVERIFY}" \
            --previous-receipt "${EVIDENCE_DIR}/stage-gate-S1.json" \
            --repository-root "${CONTRACT_ROOT}" \
            --contract "${MACHINE_CONTRACT}" \
            >/dev/null
    )
    rm -f -- "${S2_REVERIFY}"
fi

printf 'static INFO 证据包及阶段回执复核通过：%s\n' "${ARCHIVE_PATH}"
