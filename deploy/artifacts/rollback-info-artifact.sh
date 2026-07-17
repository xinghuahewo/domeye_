#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"

if (( $# != 0 )); then
    printf '用法：%s\n' "${0##*/}" >&2
    exit 2
fi

readonly TARGET_DIR="${DOMEYE_CORE_DEFAULT_INFO_TARGET}"
readonly TARGET_PARENT="${TARGET_DIR%/*}"
readonly STATE_DIR='/home/bgpdata/Domeye-Core/var/releases'
readonly CURRENT_STATE="${STATE_DIR}/info-current"
readonly JOURNAL="${STATE_DIR}/info-rollback.json"

domeye_artifact_require_regular_file "${CURRENT_STATE}"
domeye_artifact_require_regular_file "${JOURNAL}"
domeye_artifact_json_file "${JOURNAL}"
current_release="$(<"${CURRENT_STATE}")"
domeye_artifact_validate_release_id "${current_release}"
if [[ "$(jq -r '.release_id' "${JOURNAL}")" != "${current_release}" ]] \
    || ! jq -e \
        '.rollback_available == true
         and (.previous_target_existed | type) == "boolean"
         and (.previous_release_known | type) == "boolean"' \
        "${JOURNAL}" >/dev/null; then
    domeye_artifact_error '没有与当前信息发布匹配的可用回滚日志'
    exit 1
fi

previous_target_existed="$(jq -r '.previous_target_existed' "${JOURNAL}")"
previous_release_known="$(jq -r '.previous_release_known' "${JOURNAL}")"
previous_dir="$(jq -r '.previous_dir // empty' "${JOURNAL}")"
previous_release="$(jq -r '.previous_release // empty' "${JOURNAL}")"
if [[ "${previous_target_existed}" == true ]]; then
    if [[ "${previous_dir}" != "${STATE_DIR}/info-backup-"* || ! -d "${previous_dir}" || -L "${previous_dir}" ]]; then
        domeye_artifact_error "上一信息目录无效：${previous_dir}"
        exit 1
    fi
elif [[ -n "${previous_dir}" ]]; then
    domeye_artifact_error '回滚日志声明原信息目录不存在，但记录了备份目录'
    exit 1
fi
if [[ "${previous_release_known}" == true ]]; then
    domeye_artifact_validate_release_id "${previous_release}"
elif [[ -n "${previous_release}" ]]; then
    domeye_artifact_error '回滚日志声明原信息版本未知，但记录了 release-id'
    exit 1
fi
if [[ ! -d "${TARGET_DIR}" || -L "${TARGET_DIR}" ]]; then
    domeye_artifact_error "当前信息目录无效：${TARGET_DIR}"
    exit 1
fi

discard_dir="${TARGET_PARENT}/.info-rollback-discard-${current_release}-$$"
mv -- "${TARGET_DIR}" "${discard_dir}"
rollback_complete=false
cleanup() {
    local exit_code=$?
    if [[ "${rollback_complete}" != true ]]; then
        if [[ "${previous_target_existed}" == true && -d "${TARGET_DIR}" && ! -e "${previous_dir}" ]]; then
            mv -- "${TARGET_DIR}" "${previous_dir}" || true
        fi
        if [[ -d "${discard_dir}" && ! -e "${TARGET_DIR}" ]]; then
            mv -- "${discard_dir}" "${TARGET_DIR}" || true
        fi
        printf '%s\n' "${current_release}" > "${CURRENT_STATE}.restore.$$" 2>/dev/null || true
        chmod 0640 "${CURRENT_STATE}.restore.$$" 2>/dev/null || true
        mv -- "${CURRENT_STATE}.restore.$$" "${CURRENT_STATE}" 2>/dev/null || true
        if [[ -f "${JOURNAL}" ]]; then
            jq '.rollback_available = true | del(.rolled_back_at)' "${JOURNAL}" > "${JOURNAL}.restore.$$" 2>/dev/null \
                && chmod 0600 "${JOURNAL}.restore.$$" 2>/dev/null \
                && mv -- "${JOURNAL}.restore.$$" "${JOURNAL}" 2>/dev/null || true
        fi
    fi
    return "${exit_code}"
}
trap cleanup EXIT

if [[ "${previous_target_existed}" == true ]]; then
    mv -- "${previous_dir}" "${TARGET_DIR}"
fi
if [[ "${previous_release_known}" == true ]]; then
    state_tmp="${STATE_DIR}/.info-current.rollback.$$"
    printf '%s\n' "${previous_release}" > "${state_tmp}"
    chmod 0640 "${state_tmp}"
    mv -- "${state_tmp}" "${CURRENT_STATE}"
else
    rm -f -- "${CURRENT_STATE}"
fi

journal_tmp="${STATE_DIR}/.info-rollback-consumed.tmp.$$"
jq --arg rolled_back_at "$(domeye_artifact_iso_utc_now)" \
    '.rollback_available = false | .rolled_back_at = $rolled_back_at' \
    "${JOURNAL}" > "${journal_tmp}"
chmod 0600 "${journal_tmp}"
mv -- "${journal_tmp}" "${JOURNAL}"
rm -rf -- "${discard_dir}"
rollback_complete=true

printf '基础信息目录已回滚到安装前状态。\n'
