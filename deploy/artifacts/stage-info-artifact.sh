#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"

if (( $# != 2 )); then
    printf '用法：%s <发布目录> <不存在的候选目录>\n' "${0##*/}" >&2
    exit 2
fi

readonly RELEASE_DIR="${1%/}"
readonly TARGET_DIR="${2%/}"
readonly TARGET_PARENT="${TARGET_DIR%/*}"
readonly MANIFEST_PATH="${RELEASE_DIR}/${DOMEYE_CORE_INFO_MANIFEST}"
readonly ARCHIVE_PATH="${RELEASE_DIR}/${DOMEYE_CORE_INFO_ARCHIVE}"

for command_name in jq sort stat tar zstd; do
    domeye_artifact_require_command "${command_name}"
done
"${SCRIPT_DIR}/verify-release.sh" "${RELEASE_DIR}"
if [[ -e "${TARGET_DIR}" || ! -d "${TARGET_PARENT}" || -L "${TARGET_PARENT}" ]]; then
    domeye_artifact_error "候选信息目录必须不存在，且父目录必须是实际目录：${TARGET_DIR}"
    exit 1
fi

mapfile -t archive_names < <(zstd --quiet --decompress --stdout "${ARCHIVE_PATH}" | tar --list --file=- | sort)
mapfile -t expected_names < <(jq -r '.files[].name' "${MANIFEST_PATH}" | sort)
mapfile -t whitelist_names < <(printf '%s\n' "${DOMEYE_CORE_INFO_FILES[@]}" | sort)
if (( ${#archive_names[@]} != ${#DOMEYE_CORE_INFO_FILES[@]} || ${#expected_names[@]} != ${#DOMEYE_CORE_INFO_FILES[@]} )); then
    domeye_artifact_error '候选信息归档成员数量不是四个'
    exit 1
fi
for index in "${!DOMEYE_CORE_INFO_FILES[@]}"; do
    if [[ "${archive_names[index]}" != "${whitelist_names[index]}" || "${expected_names[index]}" != "${whitelist_names[index]}" ]]; then
        domeye_artifact_error "候选信息归档白名单不一致，位置 ${index}"
        exit 1
    fi
done

staged=false
cleanup() {
    local exit_code=$?
    if [[ "${staged}" != true && -d "${TARGET_DIR}" && ! -L "${TARGET_DIR}" ]]; then
        rm -rf -- "${TARGET_DIR}"
    fi
    return "${exit_code}"
}
trap cleanup EXIT

install -d -m 0750 "${TARGET_DIR}"
zstd --quiet --decompress --stdout "${ARCHIVE_PATH}" \
    | tar --extract --file=- --directory="${TARGET_DIR}" --no-same-owner --no-same-permissions

for file_name in "${DOMEYE_CORE_INFO_FILES[@]}"; do
    candidate_path="${TARGET_DIR}/${file_name}"
    domeye_artifact_require_regular_file "${candidate_path}"
    expected_sha="$(jq -r --arg name "${file_name}" '.files[] | select(.name == $name) | .sha256' "${MANIFEST_PATH}")"
    expected_size="$(jq -r --arg name "${file_name}" '.files[] | select(.name == $name) | .size' "${MANIFEST_PATH}")"
    if [[ "$(domeye_artifact_sha256 "${candidate_path}")" != "${expected_sha}" || "$(stat -c '%s' "${candidate_path}")" != "${expected_size}" ]]; then
        domeye_artifact_error "候选信息文件大小或 SHA256 不一致：${file_name}"
        exit 1
    fi
    chmod 0640 "${candidate_path}"
done

staged=true
printf '候选四文件信息制品已解包：%s\n' "${TARGET_DIR}"
