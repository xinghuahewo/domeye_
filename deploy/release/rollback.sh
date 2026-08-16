#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly DEPLOY_DIR="${SCRIPT_DIR}/.."
# shellcheck source=../lib/artifact-common.sh
source "${DEPLOY_DIR}/lib/artifact-common.sh"
# shellcheck source=../lib/backend-common.sh
source "${DEPLOY_DIR}/lib/backend-common.sh"
# shellcheck source=../lib/frontend-common.sh
source "${DEPLOY_DIR}/lib/frontend-common.sh"
# shellcheck source=../lib/database-common.sh
source "${DEPLOY_DIR}/lib/database-common.sh"
# shellcheck source=../lib/release-common.sh
source "${DEPLOY_DIR}/lib/release-common.sh"

domeye_core_require_realtime_profile || exit 1

if (( $# != 3 )); then
    printf '用法：CONFIRM_RELEASE_ID=<release-id> %s <release-id> <数据库配置> <发布机主机名>\n' \
        "${0##*/}" >&2
    exit 2
fi

readonly RELEASE_ID="$1"
readonly DATABASE_ENV_FILE="$2"
readonly EXPECTED_HOST="$3"
readonly STATE_FILE="$(domeye_release_state_file "${RELEASE_ID}")"
readonly DATABASE_CURRENT="${DOMEYE_CORE_RELEASE_STATE_DIR}/database-current"
readonly INFO_CURRENT="${DOMEYE_CORE_RELEASE_STATE_DIR}/info-current"
readonly FRONTEND_CURRENT="${DOMEYE_CORE_FRONTEND_CURRENT_STATE}"
readonly DATABASE_JOURNAL="${DOMEYE_CORE_RELEASE_STATE_DIR}/database-rollback.json"
readonly INFO_JOURNAL="${DOMEYE_CORE_RELEASE_STATE_DIR}/info-rollback.json"
readonly FRONTEND_JOURNAL="${DOMEYE_CORE_FRONTEND_ROLLBACK_JOURNAL}"

for command_name in awk cat hostname install jq mkdir mv nginx readlink rm sha256sum stat systemctl; do
    domeye_artifact_require_command "${command_name}"
done
domeye_artifact_validate_release_id "${RELEASE_ID}"
domeye_release_require_root
domeye_release_require_host "${EXPECTED_HOST}"
if [[ "${CONFIRM_RELEASE_ID:-}" != "${RELEASE_ID}" ]]; then
    domeye_artifact_error 'CONFIRM_RELEASE_ID 必须与待回滚 release-id 完全一致'
    exit 2
fi
domeye_release_require_mode "${DATABASE_ENV_FILE}" 600
if [[ "$(readlink -f -- "${DATABASE_ENV_FILE}")" \
    != '/home/bgpdata/Domeye-Core-data/config/database.env' ]]; then
    domeye_artifact_error '回滚只能使用固定独立数据库配置'
    exit 1
fi
domeye_release_validate_state_file "${STATE_FILE}"
if ! jq -e --arg release_id "${RELEASE_ID}" --arg host "${EXPECTED_HOST}" \
    '.schema_version == 1
     and .release_id == $release_id
     and .stage == "active"
     and .inputs.host == $host
     and (.completed_gates | index("activated")) != null
     and (.activation.nginx.target | type) == "string"
     and (.activation.nginx.previous_existed | type) == "boolean"
     and (.activation.nginx.was_active | type) == "boolean"
     and (.activation.nginx.installed_sha256 | test("^[0-9a-f]{64}$"))' \
    "${STATE_FILE}" >/dev/null; then
    domeye_artifact_error '发布状态不是可回滚的 active 状态'
    exit 1
fi

for current_file in "${DATABASE_CURRENT}" "${INFO_CURRENT}" "${FRONTEND_CURRENT}"; do
    domeye_artifact_require_regular_file "${current_file}"
    if [[ "$(<"${current_file}")" != "${RELEASE_ID}" ]]; then
        domeye_artifact_error "活动组件版本与待回滚版本不一致：${current_file}"
        exit 1
    fi
done
for journal_file in "${DATABASE_JOURNAL}" "${INFO_JOURNAL}" "${FRONTEND_JOURNAL}"; do
    domeye_artifact_require_regular_file "${journal_file}"
    domeye_artifact_json_file "${journal_file}"
    if ! jq -e --arg release_id "${RELEASE_ID}" \
        '.release_id == $release_id and .rollback_available == true' \
        "${journal_file}" >/dev/null; then
        domeye_artifact_error "组件回滚日志不可用或版本不一致：${journal_file}"
        exit 1
    fi
done

readonly NGINX_TARGET="$(jq -r '.activation.nginx.target' "${STATE_FILE}")"
readonly NGINX_INSTALLED_SHA="$(jq -r '.activation.nginx.installed_sha256' "${STATE_FILE}")"
readonly NGINX_PREVIOUS_EXISTED="$(jq -r '.activation.nginx.previous_existed' "${STATE_FILE}")"
readonly NGINX_PREVIOUS_BACKUP="$(jq -r '.activation.nginx.previous_backup // empty' "${STATE_FILE}")"
readonly NGINX_PREVIOUS_SHA="$(jq -r '.activation.nginx.previous_sha256 // empty' "${STATE_FILE}")"
readonly NGINX_WAS_ACTIVE="$(jq -r '.activation.nginx.was_active' "${STATE_FILE}")"
if [[ "${NGINX_TARGET}" != '/etc/nginx/conf.d/domeye-core.conf' \
    || ! -f "${NGINX_TARGET}" || -L "${NGINX_TARGET}" \
    || "$(domeye_artifact_sha256 "${NGINX_TARGET}")" != "${NGINX_INSTALLED_SHA}" ]]; then
    domeye_artifact_error '当前 Nginx 配置与激活状态不一致，拒绝开始部分回滚'
    exit 1
fi
if [[ "${NGINX_PREVIOUS_EXISTED}" == true ]]; then
    domeye_release_require_mode "${NGINX_PREVIOUS_BACKUP}" 600
    if [[ ! "${NGINX_PREVIOUS_SHA}" =~ ^[0-9a-f]{64}$ \
        || "$(domeye_artifact_sha256 "${NGINX_PREVIOUS_BACKUP}")" != "${NGINX_PREVIOUS_SHA}" ]]; then
        domeye_artifact_error '切换前 Nginx 备份与激活状态不一致'
        exit 1
    fi
elif [[ -n "${NGINX_PREVIOUS_BACKUP}" || -n "${NGINX_PREVIOUS_SHA}" ]]; then
    domeye_artifact_error '激活状态声明原 Nginx 配置不存在，但仍记录了备份'
    exit 1
fi

lock_owned=false
rollback_complete=false
cleanup() {
    local exit_code=$?
    if [[ "${rollback_complete}" != true && -f "${STATE_FILE}" && ! -L "${STATE_FILE}" ]]; then
        if jq -e '.stage == "rolling_back"' "${STATE_FILE}" >/dev/null 2>&1; then
            jq \
                --arg stage 'rollback_failed' \
                --arg failed_at "$(domeye_artifact_iso_utc_now)" \
                --argjson exit_code "${exit_code}" \
                '.stage = $stage
                 | .rollback.failed_at = $failed_at
                 | .rollback.exit_code = $exit_code
                 | .updated_at = $failed_at' \
                "${STATE_FILE}" | domeye_release_atomic_state "${STATE_FILE}" || true
        fi
    fi
    if [[ "${lock_owned}" == true ]]; then
        domeye_release_release_lock
    fi
    return "${exit_code}"
}
trap cleanup EXIT

domeye_release_acquire_lock rollback "${RELEASE_ID}"
lock_owned=true
jq \
    --arg stage 'rolling_back' \
    --arg started_at "$(domeye_artifact_iso_utc_now)" \
    '.stage = $stage
     | .rollback = {started_at: $started_at}
     | .updated_at = $started_at' \
    "${STATE_FILE}" | domeye_release_atomic_state "${STATE_FILE}"

"${DEPLOY_DIR}/stop-backend.sh"
"${DEPLOY_DIR}/artifacts/rollback-frontend-build.sh"
"${DEPLOY_DIR}/artifacts/rollback-info-artifact.sh"
"${DEPLOY_DIR}/database/rollback-database.sh" "${DATABASE_ENV_FILE}"

if [[ "${NGINX_PREVIOUS_EXISTED}" == true ]]; then
    install -m 0644 "${NGINX_PREVIOUS_BACKUP}" "${NGINX_TARGET}"
else
    rm -f -- "${NGINX_TARGET}"
fi
nginx -t
if [[ "${NGINX_WAS_ACTIVE}" == true ]]; then
    if systemctl is-active --quiet nginx; then
        systemctl reload nginx
    else
        systemctl start nginx
    fi
else
    systemctl stop nginx
fi
"${DEPLOY_DIR}/status.sh"

jq \
    --arg stage 'rolled_back' \
    --arg gate 'rolled_back' \
    --arg rolled_back_at "$(domeye_artifact_iso_utc_now)" \
    '.stage = $stage
     | .completed_gates = ((.completed_gates + [$gate]) | unique)
     | .rollback.rolled_back_at = $rolled_back_at
     | .updated_at = $rolled_back_at' \
    "${STATE_FILE}" | domeye_release_atomic_state "${STATE_FILE}"
rollback_complete=true

printf '发布已显式回滚：%s\n' "${RELEASE_ID}"
