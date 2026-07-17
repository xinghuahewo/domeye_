#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if (( $# < 4 || $# > 5 )); then
    printf '用法：%s <源库配置> <独立库配置> <release-id> <上一发布目录> [制品根目录]\n' "${0##*/}" >&2
    exit 2
fi

readonly SOURCE_ENV_FILE="$1"
readonly DATABASE_ENV_FILE="$2"
readonly RELEASE_ID="$3"
readonly BASE_RELEASE_DIR="$4"
readonly ARTIFACT_ROOT="${5:-/home/bgpdata/Domeye-Core-artifacts}"

exec "${SCRIPT_DIR}/build-database-artifact.sh" \
    "${SOURCE_ENV_FILE}" \
    "${DATABASE_ENV_FILE}" \
    "${RELEASE_ID}" \
    "${ARTIFACT_ROOT}" \
    "${BASE_RELEASE_DIR}"
