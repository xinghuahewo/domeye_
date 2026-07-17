#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"

usage() {
    printf '用法：%s <发布目录>\n' "${0##*/}" >&2
}

if (( $# != 1 )); then
    usage
    exit 2
fi

readonly RELEASE_DIR="${1%/}"
readonly MANIFEST_PATH="${RELEASE_DIR}/${DOMEYE_CORE_INFO_MANIFEST}"
readonly ARCHIVE_PATH="${RELEASE_DIR}/${DOMEYE_CORE_INFO_ARCHIVE}"
readonly TARGET_DIR="${DOMEYE_CORE_DEFAULT_INFO_TARGET}"
readonly TARGET_PARENT="${TARGET_DIR%/*}"
readonly RELEASE_STATE_DIR='/home/bgpdata/Domeye-Core/var/releases'
readonly INFO_ROLLBACK_JOURNAL="${RELEASE_STATE_DIR}/info-rollback.json"

for command_name in jq sha256sum tar zstd mktemp; do
    domeye_artifact_require_command "${command_name}"
done
domeye_artifact_require_regular_file "${MANIFEST_PATH}"
domeye_artifact_require_regular_file "${ARCHIVE_PATH}"
domeye_artifact_json_file "${MANIFEST_PATH}"

if [[ "$(jq -r '.component' "${MANIFEST_PATH}")" != 'info' ]]; then
    domeye_artifact_error "不是信息制品清单：${MANIFEST_PATH}"
    exit 1
fi
if [[ "$(jq -r '.data_start' "${MANIFEST_PATH}")" != "${DOMEYE_CORE_DATA_START}" ]]; then
    domeye_artifact_error "信息制品的数据起点不符合约定"
    exit 1
fi

expected_archive_sha="$(jq -r '.archive.sha256' "${MANIFEST_PATH}")"
actual_archive_sha="$(domeye_artifact_sha256 "${ARCHIVE_PATH}")"
if [[ "${actual_archive_sha}" != "${expected_archive_sha}" ]]; then
    domeye_artifact_error "信息归档 SHA256 不一致"
    exit 1
fi

mapfile -t archive_names < <(zstd --quiet --decompress --stdout "${ARCHIVE_PATH}" | tar --list --file=- | sort)
mapfile -t expected_names < <(jq -r '.files[].name' "${MANIFEST_PATH}" | sort)
mapfile -t whitelist_names < <(printf '%s\n' "${DOMEYE_CORE_INFO_FILES[@]}" | sort)
if (( ${#archive_names[@]} != ${#DOMEYE_CORE_INFO_FILES[@]} || ${#expected_names[@]} != ${#DOMEYE_CORE_INFO_FILES[@]} )); then
    domeye_artifact_error '归档成员数量不是四个'
    exit 1
fi
for index in "${!DOMEYE_CORE_INFO_FILES[@]}"; do
    if [[ "${archive_names[index]}" != "${whitelist_names[index]}" || "${expected_names[index]}" != "${whitelist_names[index]}" ]]; then
        domeye_artifact_error "归档白名单不一致，位置 ${index}"
        exit 1
    fi
done

if [[ ! -d "${TARGET_PARENT}" || -L "${TARGET_PARENT}" ]]; then
    domeye_artifact_error "后端目录不存在或是软链接：${TARGET_PARENT}"
    exit 1
fi
install -d -m 0750 "${RELEASE_STATE_DIR}"

readonly RELEASE_ID="$(jq -r '.release_id' "${MANIFEST_PATH}")"
domeye_artifact_validate_release_id "${RELEASE_ID}"
readonly CANDIDATE_DIR="${TARGET_PARENT}/.info-install-${RELEASE_ID}-$$"
backup_dir=''
previous_release=''
previous_release_known=false
if [[ -f "${RELEASE_STATE_DIR}/info-current" && ! -L "${RELEASE_STATE_DIR}/info-current" ]]; then
    previous_release="$(<"${RELEASE_STATE_DIR}/info-current")"
    domeye_artifact_validate_release_id "${previous_release}"
    previous_release_known=true
elif [[ -e "${RELEASE_STATE_DIR}/info-current" ]]; then
    domeye_artifact_error '原信息 current 状态不是普通文件'
    exit 1
fi
target_existed=false
activated=false
cleanup() {
    local exit_code=$?
    if [[ "${activated}" == true ]]; then
        if [[ -d "${TARGET_DIR}" && ! -L "${TARGET_DIR}" ]]; then
            mv -- "${TARGET_DIR}" "${CANDIDATE_DIR}"
        fi
        if [[ -n "${backup_dir}" && -d "${backup_dir}" ]]; then
            mv -- "${backup_dir}" "${TARGET_DIR}"
        fi
        if [[ "${previous_release_known}" == true ]]; then
            printf '%s\n' "${previous_release}" > "${RELEASE_STATE_DIR}/.info-current.restore.$$"
            chmod 0640 "${RELEASE_STATE_DIR}/.info-current.restore.$$"
            mv -- "${RELEASE_STATE_DIR}/.info-current.restore.$$" "${RELEASE_STATE_DIR}/info-current"
        else
            rm -f -- "${RELEASE_STATE_DIR}/info-current"
        fi
    fi
    if [[ -d "${CANDIDATE_DIR}" ]]; then
        rm -rf -- "${CANDIDATE_DIR}"
    fi
    return "${exit_code}"
}
trap cleanup EXIT

install -d -m 0750 "${CANDIDATE_DIR}"
zstd --quiet --decompress --stdout "${ARCHIVE_PATH}" \
    | tar --extract --file=- --directory="${CANDIDATE_DIR}" --no-same-owner --no-same-permissions

for file_name in "${DOMEYE_CORE_INFO_FILES[@]}"; do
    candidate_path="${CANDIDATE_DIR}/${file_name}"
    domeye_artifact_require_regular_file "${candidate_path}"
    expected_sha="$(jq -r --arg name "${file_name}" '.files[] | select(.name == $name) | .sha256' "${MANIFEST_PATH}")"
    expected_size="$(jq -r --arg name "${file_name}" '.files[] | select(.name == $name) | .size' "${MANIFEST_PATH}")"
    if [[ "$(domeye_artifact_sha256 "${candidate_path}")" != "${expected_sha}" ]]; then
        domeye_artifact_error "文件 SHA256 不一致：${file_name}"
        exit 1
    fi
    if [[ "$(stat -c '%s' "${candidate_path}")" != "${expected_size}" ]]; then
        domeye_artifact_error "文件大小不一致：${file_name}"
        exit 1
    fi
    chmod 0640 "${candidate_path}"
done

if [[ -e "${TARGET_DIR}" ]]; then
    if [[ ! -d "${TARGET_DIR}" || -L "${TARGET_DIR}" ]]; then
        domeye_artifact_error "拒绝替换非目录或软链接目标：${TARGET_DIR}"
        exit 1
    fi
    backup_dir="${RELEASE_STATE_DIR}/info-backup-$(date -u '+%Y%m%dT%H%M%SZ')-$$"
    mv -- "${TARGET_DIR}" "${backup_dir}"
    target_existed=true
fi

activated=true
mv -- "${CANDIDATE_DIR}" "${TARGET_DIR}"
state_tmp="${RELEASE_STATE_DIR}/.info-current.tmp.$$"
printf '%s\n' "${RELEASE_ID}" > "${state_tmp}"
chmod 0640 "${state_tmp}"
mv -- "${state_tmp}" "${RELEASE_STATE_DIR}/info-current"
journal_tmp="${RELEASE_STATE_DIR}/.info-rollback.tmp.$$"
jq -n \
    --arg release_id "${RELEASE_ID}" \
    --arg created_at "$(domeye_artifact_iso_utc_now)" \
    --arg previous_dir "${backup_dir}" \
    --arg previous_release "${previous_release}" \
    --argjson rollback_available true \
    --argjson previous_target_existed "${target_existed}" \
    --argjson previous_release_known "${previous_release_known}" \
    '{
      release_id: $release_id,
      created_at: $created_at,
      rollback_available: $rollback_available,
      previous_target_existed: $previous_target_existed,
      previous_dir: (if $previous_dir == "" then null else $previous_dir end),
      previous_release_known: $previous_release_known,
      previous_release: (if $previous_release_known then $previous_release else null end)
    }' > "${journal_tmp}"
chmod 0600 "${journal_tmp}"
mv -- "${journal_tmp}" "${INFO_ROLLBACK_JOURNAL}"
activated=false

printf '信息制品安装完成：%s\n' "${TARGET_DIR}"
if [[ -n "${backup_dir}" ]]; then
    printf '旧信息目录保留为：%s\n' "${backup_dir}"
fi
