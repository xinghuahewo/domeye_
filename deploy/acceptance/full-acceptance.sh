#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly DEPLOY_DIR="${SCRIPT_DIR}/.."
# shellcheck source=../lib/artifact-common.sh
source "${DEPLOY_DIR}/lib/artifact-common.sh"
# shellcheck source=../lib/backend-common.sh
source "${DEPLOY_DIR}/lib/backend-common.sh"

if (( $# < 2 || $# > 3 )); then
    printf '用法：%s <发布目录> <待隐藏旧目录> [数据库配置]\n' "${0##*/}" >&2
    exit 2
fi

readonly RELEASE_DIR="${1%/}"
readonly HIDDEN_PATH="$2"
readonly DATABASE_ENV_FILE="${3:-/home/bgpdata/Domeye-Core-data/config/database.env}"
readonly PROJECT_ROOT='/home/bgpdata/Domeye-Core'
readonly MANIFEST_PATH="${RELEASE_DIR}/${DOMEYE_CORE_RELEASE_MANIFEST}"
readonly NGINX_SOURCE="${PROJECT_ROOT}/deploy/nginx/domeye-core.conf"
readonly NGINX_TARGET='/etc/nginx/conf.d/domeye-core.conf'

for command_name in awk nginx ps screen systemctl tr; do
    domeye_artifact_require_command "${command_name}"
done
if [[ ! -x "${DOMEYE_CORE_NODE_BIN_DIR}/node" || ! -x "${DOMEYE_CORE_NODE_BIN_DIR}/npm" ]]; then
    domeye_artifact_error "缺少项目隔离的 Node.js 22.23.1：${DOMEYE_CORE_NODE_BIN_DIR}"
    exit 1
fi
if [[ "$("${DOMEYE_CORE_NODE_BIN_DIR}/node" --version)" != 'v22.23.1' ]]; then
    domeye_artifact_error '前端验收必须使用固定 Node.js v22.23.1'
    exit 1
fi

nginx_backup=''
nginx_changed=false
production_activated=false
activation_started=false
info_installed=false
backend_stopped_for_switch=false
production_backend_was_running=false
previous_runtime_info_dir=''
acceptance_complete=false
mapfile -t initial_backend_sessions < <(domeye_core_list_backend_sessions)
if (( ${#initial_backend_sessions[@]} > 1 )); then
    domeye_artifact_error "发现多个 Domeye Core 后端会话：${initial_backend_sessions[*]}"
    exit 1
elif (( ${#initial_backend_sessions[@]} == 1 )); then
    production_backend_was_running=true
    if ! previous_runtime_info_dir="$(domeye_core_capture_backend_info_dir)"; then
        domeye_artifact_error '无法从切换前 Domeye Core 后端进程安全捕获实际 INFO_DIR'
        exit 1
    fi
fi
rollback_full_acceptance() {
    local exit_code=$?
    trap - EXIT
    if [[ "${acceptance_complete}" != true ]]; then
        set +e
        if [[ "${production_activated}" == true ]]; then
            "${DEPLOY_DIR}/stop-backend.sh" >/dev/null 2>&1
        fi
        if [[ "${info_installed}" == true ]]; then
            "${DEPLOY_DIR}/artifacts/rollback-info-artifact.sh" >&2
        fi
        if [[ "${production_activated}" == true ]]; then
            "${DEPLOY_DIR}/database/rollback-database.sh" "${DATABASE_ENV_FILE}" >&2
            if [[ "${production_backend_was_running}" != true ]]; then
                "${DEPLOY_DIR}/stop-backend.sh" >/dev/null 2>&1
            fi
        elif [[ "${activation_started}" == true || "${backend_stopped_for_switch}" == true ]]; then
            "${DEPLOY_DIR}/stop-backend.sh" >/dev/null 2>&1
            if [[ "${production_backend_was_running}" == true ]]; then
                DOMEYE_CORE_ALLOW_ROLLBACK_CONFIG=true \
                    DOMEYE_CORE_ROLLBACK_INFO_DIR="${previous_runtime_info_dir}" \
                    "${DEPLOY_DIR}/start-backend.sh" >/dev/null 2>&1
            fi
        fi
        if [[ "${nginx_changed}" == true ]]; then
            if [[ -n "${nginx_backup}" && -f "${nginx_backup}" ]]; then
                install -m 0644 "${nginx_backup}" "${NGINX_TARGET}"
                nginx -t >/dev/null 2>&1 && systemctl reload nginx
            elif [[ -f "${NGINX_TARGET}" ]]; then
                rm -f -- "${NGINX_TARGET}"
                nginx -t >/dev/null 2>&1 && systemctl reload nginx
            fi
        fi
    fi
    exit "${exit_code}"
}
trap rollback_full_acceptance EXIT

"${DEPLOY_DIR}/artifacts/verify-release.sh" "${RELEASE_DIR}"
"${DEPLOY_DIR}/database/restore-database.sh" "${RELEASE_DIR}" "${DATABASE_ENV_FILE}"

(
    cd -- "${PROJECT_ROOT}/backend"
    /home/bgpdata/.local/bin/uv sync --frozen
    /home/bgpdata/.local/bin/uv run --frozen pytest
    sha256sum -c core.sha256
)
(
    cd -- "${PROJECT_ROOT}/frontend"
    export PATH="${DOMEYE_CORE_RUNTIME_PATH}"
    [[ "$(node --version)" == 'v22.23.1' ]]
    npm ci
    npm test
    npm run build
)

"${SCRIPT_DIR}/candidate-stack.sh" "${RELEASE_DIR}" "${DATABASE_ENV_FILE}" "${HIDDEN_PATH}"

if [[ -f "${NGINX_TARGET}" && ! -L "${NGINX_TARGET}" ]]; then
    nginx_backup="${PROJECT_ROOT}/var/releases/nginx-before-$(date -u '+%Y%m%dT%H%M%SZ')-$$.conf"
    install -d -m 0750 "${PROJECT_ROOT}/var/releases"
    install -m 0644 "${NGINX_TARGET}" "${nginx_backup}"
elif [[ -e "${NGINX_TARGET}" ]]; then
    domeye_artifact_error "Nginx 目标不是普通文件：${NGINX_TARGET}"
    exit 1
fi

install -m 0644 "${NGINX_SOURCE}" "${NGINX_TARGET}"
nginx_changed=true
if ! nginx -t; then
    if [[ -n "${nginx_backup}" ]]; then
        install -m 0644 "${nginx_backup}" "${NGINX_TARGET}"
    else
        rm -f -- "${NGINX_TARGET}"
    fi
    domeye_artifact_error 'Nginx 配置检查失败，已恢复原配置'
    exit 1
fi
if systemctl is-active --quiet nginx; then
    systemctl reload nginx
else
    systemctl start nginx
fi

release_id="$(jq -r '.release_id' "${MANIFEST_PATH}")"
if [[ "${production_backend_was_running}" == true ]]; then
    "${DEPLOY_DIR}/stop-backend.sh"
    backend_stopped_for_switch=true
fi
"${DEPLOY_DIR}/artifacts/install-info-artifact.sh" "${RELEASE_DIR}"
info_installed=true
activation_started=true
DOMEYE_CORE_PREVIOUS_BACKEND_WAS_RUNNING="${production_backend_was_running}" \
    DOMEYE_CORE_PREVIOUS_INFO_DIR="${previous_runtime_info_dir}" \
    "${DEPLOY_DIR}/database/activate-database.sh" "${release_id}" "${DATABASE_ENV_FILE}" "${MANIFEST_PATH}"
production_activated=true
"${DEPLOY_DIR}/status.sh"
"${SCRIPT_DIR}/smoke.sh" "${MANIFEST_PATH}"
"${SCRIPT_DIR}/verify-isolation.sh" "${MANIFEST_PATH}" "${HIDDEN_PATH}" "${DATABASE_ENV_FILE}" 29429

acceptance_complete=true
printf 'Domeye Core 独立部署完整验收通过：%s\n' "${release_id}"
