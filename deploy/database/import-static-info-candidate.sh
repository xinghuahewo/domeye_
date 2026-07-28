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
        "用法：${0##*/} <只读INFO目录> <release-id> <候选容器> <数据库管理员> <数据库名> <证据目录> [代码提交]" \
        >&2
}

if (( $# < 6 || $# > 7 )); then
    usage
    exit 2
fi

readonly SOURCE_INFO_DIR="$1"
readonly RELEASE_ID="$2"
readonly CANDIDATE_CONTAINER="$3"
readonly DATABASE_USER="$4"
readonly DATABASE_NAME="$5"
readonly EVIDENCE_DIR="$6"
readonly CODE_COMMIT="${7:-unknown}"

domeye_artifact_validate_release_id "${RELEASE_ID}"
for command_name in docker install jq sha256sum; do
    domeye_artifact_require_command "${command_name}"
done
domeye_static_info_assert_offline_candidate "${CANDIDATE_CONTAINER}"

domeye_static_info_load_shadow \
    "${REPOSITORY_ROOT}" \
    "${SOURCE_INFO_DIR}" \
    "${RELEASE_ID}" \
    "${CANDIDATE_CONTAINER}" \
    "${DATABASE_USER}" \
    "${DATABASE_NAME}" \
    "${EVIDENCE_DIR}" \
    "${CODE_COMMIT}"

printf 'static INFO shadow release 已导入候选库；未激活：%s\n' "${EVIDENCE_DIR}"
