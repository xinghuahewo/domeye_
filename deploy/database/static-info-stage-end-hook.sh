#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

usage() {
    printf '%s\n' \
        "用法：${0##*/} <S0..S6> <证据目录> <回执文件> [前序阶段回执]" \
        >&2
}

if (( $# < 3 || $# > 4 )); then
    usage
    exit 2
fi

readonly STAGE_ID="$1"
readonly EVIDENCE_DIR="$2"
readonly OUTPUT_PATH="$3"
readonly PREVIOUS_RECEIPT="${4:-}"
readonly PYTHON_CANDIDATE="${DOMEYE_CORE_INFO_PYTHON:-${REPOSITORY_ROOT}/backend/.venv/bin/python}"

if [[ -x "${PYTHON_CANDIDATE}" ]]; then
    readonly PYTHON_BIN="${PYTHON_CANDIDATE}"
elif command -v python3 >/dev/null 2>&1; then
    readonly PYTHON_BIN="$(command -v python3)"
else
    printf '错误：缺少可执行的 Python 3\n' >&2
    exit 2
fi

readonly -a HOOK_ARGUMENTS=(
    --stage "${STAGE_ID}"
    --evidence-dir "${EVIDENCE_DIR}"
    --output "${OUTPUT_PATH}"
)

cd -- "${REPOSITORY_ROOT}"
if [[ -n "${PREVIOUS_RECEIPT}" ]]; then
    PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" \
        -m backend.info_pipeline.stage_gate \
        "${HOOK_ARGUMENTS[@]}" \
        --previous-receipt "${PREVIOUS_RECEIPT}"
else
    PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" \
        -m backend.info_pipeline.stage_gate \
        "${HOOK_ARGUMENTS[@]}"
fi
