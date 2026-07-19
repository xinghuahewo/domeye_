#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"
# shellcheck source=../lib/database-common.sh
source "${SCRIPT_DIR}/../lib/database-common.sh"
# shellcheck source=../lib/backend-common.sh
source "${SCRIPT_DIR}/../lib/backend-common.sh"
# shellcheck source=../lib/data-profile.sh
source "${SCRIPT_DIR}/../lib/data-profile.sh"

domeye_core_require_realtime_profile || exit 1

if (( $# > 1 )); then
    printf '用法：%s [数据库配置]\n' "${0##*/}" >&2
    exit 2
fi

readonly DATABASE_ENV_FILE="${1:-${DOMEYE_CORE_DATABASE_CONFIG_DEFAULT}}"
readonly PROJECT_ROOT='/home/bgpdata/Domeye-Core'
readonly RELEASE_STATE_DIR="${PROJECT_ROOT}/var/releases"
readonly CURRENT_STATE="${RELEASE_STATE_DIR}/database-current"
readonly ACTIVATION_JOURNAL="${RELEASE_STATE_DIR}/database-rollback.json"

defer_backend_restart=false
case "${DOMEYE_CORE_DEFER_BACKEND_RESTART_ON_ROLLBACK:-false}" in
    true) defer_backend_restart=true ;;
    false|'') ;;
    *)
        domeye_artifact_error 'DOMEYE_CORE_DEFER_BACKEND_RESTART_ON_ROLLBACK 只能为 true 或 false'
        exit 1
        ;;
esac

domeye_database_load_env "${DATABASE_ENV_FILE}"
domeye_database_validate_config
domeye_artifact_require_regular_file "${ACTIVATION_JOURNAL}"
domeye_artifact_json_file "${ACTIVATION_JOURNAL}"
readonly CURRENT_RELEASE="$(jq -r '.release_id' "${ACTIVATION_JOURNAL}")"
domeye_artifact_validate_release_id "${CURRENT_RELEASE}"
readonly BACKEND_ENV_BACKUP="${RELEASE_STATE_DIR}/backend-env-before-${CURRENT_RELEASE}"
if ! jq -e \
        '.rollback_available == true
         and (.backend_env_existed | type) == "boolean"
         and (.backend_was_running | type) == "boolean"
         and (.database_was_running | type) == "boolean"
         and (.previous_link_existed | type) == "boolean"
         and (.previous_info_existed | type) == "boolean"' \
        "${ACTIVATION_JOURNAL}" >/dev/null; then
    domeye_artifact_error '没有可消费的数据库回滚日志'
    exit 1
fi

readonly BACKEND_ENV_EXISTED="$(jq -r '.backend_env_existed' "${ACTIVATION_JOURNAL}")"
readonly BACKEND_WAS_RUNNING="$(jq -r '.backend_was_running' "${ACTIVATION_JOURNAL}")"
readonly DATABASE_WAS_RUNNING="$(jq -r '.database_was_running' "${ACTIVATION_JOURNAL}")"
readonly PREVIOUS_LINK_EXISTED="$(jq -r '.previous_link_existed' "${ACTIVATION_JOURNAL}")"
readonly PREVIOUS_INFO_EXISTED="$(jq -r '.previous_info_existed' "${ACTIVATION_JOURNAL}")"
previous_info_dir="$(jq -r '.previous_info_dir // empty' "${ACTIVATION_JOURNAL}")"
if [[ "${BACKEND_ENV_EXISTED}" == true ]]; then
    domeye_artifact_require_regular_file "${BACKEND_ENV_BACKUP}"
elif [[ -e "${BACKEND_ENV_BACKUP}" ]]; then
    domeye_artifact_error "激活日志声明原 .env 不存在，但发现意外备份：${BACKEND_ENV_BACKUP}"
    exit 1
fi
if [[ "${PREVIOUS_INFO_EXISTED}" == true ]]; then
    domeye_core_validate_info_dir "${previous_info_dir}"
elif [[ "${PREVIOUS_INFO_EXISTED}" == false && -n "${previous_info_dir}" ]]; then
    domeye_artifact_error '激活日志声明原 INFO_DIR 不存在，但记录了路径'
    exit 1
fi

previous_target="$(jq -r '.previous_target // empty' "${ACTIVATION_JOURNAL}")"
if [[ "${PREVIOUS_LINK_EXISTED}" == true ]]; then
    if [[ "${previous_target}" != "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/"*/postgres || ! -f "${previous_target}/PG_VERSION" ]]; then
        domeye_artifact_error "上一数据库发布路径无效：${previous_target}"
        exit 1
    fi
elif [[ -n "${previous_target}" ]]; then
    domeye_artifact_error '激活日志声明原活动链接不存在，但记录了上一数据库路径'
    exit 1
fi

observed_current_release=''
if [[ -f "${CURRENT_STATE}" && ! -L "${CURRENT_STATE}" ]]; then
    observed_current_release="$(<"${CURRENT_STATE}")"
    domeye_artifact_validate_release_id "${observed_current_release}"
elif [[ -e "${CURRENT_STATE}" ]]; then
    domeye_artifact_error "数据库 current 状态不是普通文件：${CURRENT_STATE}"
    exit 1
fi
previous_release=''
if [[ -n "${previous_target}" ]]; then
    previous_release="$(basename -- "$(dirname -- "${previous_target}")")"
    domeye_artifact_validate_release_id "${previous_release}"
fi
if [[ -n "${observed_current_release}" && "${observed_current_release}" != "${CURRENT_RELEASE}" && "${observed_current_release}" != "${previous_release}" ]]; then
    domeye_artifact_error "数据库 current 状态与回滚日志不匹配：${observed_current_release}"
    exit 1
fi

"${PROJECT_ROOT}/deploy/stop-backend.sh"
"${SCRIPT_DIR}/dbctl.sh" down "${DATABASE_ENV_FILE}"
if [[ "${BACKEND_ENV_EXISTED}" == true ]]; then
    install -m 0600 "${BACKEND_ENV_BACKUP}" "${PROJECT_ROOT}/backend/.env"
else
    rm -f -- "${PROJECT_ROOT}/backend/.env"
fi

if [[ -n "${previous_target}" ]]; then
    rollback_link="${DOMEYE_CORE_DATABASE_ACTIVE_LINK}.manual-rollback.$$"
    ln -s "${previous_target}" "${rollback_link}"
    mv -T -- "${rollback_link}" "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}"
    if [[ "${DATABASE_WAS_RUNNING}" == true ]]; then
        "${SCRIPT_DIR}/dbctl.sh" up "${DATABASE_ENV_FILE}"
    fi
else
    rm -f -- "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}"
fi

if [[ -z "${previous_target}" && "${BACKEND_ENV_EXISTED}" == true && "${PREVIOUS_INFO_EXISTED}" == true ]]; then
    domeye_core_write_source_rollback_state \
        "${previous_info_dir}" \
        "manual-rollback-${CURRENT_RELEASE}"
else
    domeye_core_clear_source_rollback_state
fi

if [[ "${BACKEND_WAS_RUNNING}" == true && "${defer_backend_restart}" != true ]]; then
    DOMEYE_CORE_ALLOW_ROLLBACK_CONFIG=true \
        DOMEYE_CORE_ROLLBACK_INFO_DIR="${previous_info_dir}" \
        "${PROJECT_ROOT}/deploy/start-backend.sh"
fi
if [[ -n "${previous_release}" ]]; then
    state_tmp="${RELEASE_STATE_DIR}/.database-current.rollback.$$"
    printf '%s\n' "${previous_release}" > "${state_tmp}"
    chmod 0640 "${state_tmp}"
    mv -- "${state_tmp}" "${CURRENT_STATE}"
else
    rm -f -- "${CURRENT_STATE}"
fi
journal_tmp="${RELEASE_STATE_DIR}/.database-rollback-consumed.tmp.$$"
jq --arg rolled_back_at "$(domeye_artifact_iso_utc_now)" \
    '.rollback_available = false | .rolled_back_at = $rolled_back_at' \
    "${ACTIVATION_JOURNAL}" > "${journal_tmp}"
chmod 0600 "${journal_tmp}"
mv -- "${journal_tmp}" "${ACTIVATION_JOURNAL}"
rm -f -- "${BACKEND_ENV_BACKUP}"
printf 'Domeye Core 已回滚到切换前的后端配置%s。\n' "$(if [[ -n "${previous_target}" ]]; then printf '和独立数据库发布'; else printf ''; fi)"
