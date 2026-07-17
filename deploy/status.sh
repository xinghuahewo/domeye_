#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/backend-common.sh
source "${SCRIPT_DIR}/lib/backend-common.sh"

domeye_core_require_command screen
domeye_core_require_command curl

status_code=0
mapfile -t existing_sessions < <(domeye_core_list_backend_sessions)

if (( ${#existing_sessions[@]} == 0 )); then
    printf 'Screen 后端：未运行\n'
    status_code=1
elif (( ${#existing_sessions[@]} > 1 )); then
    printf 'Screen 后端：异常，存在多个同名会话（%s）\n' "${existing_sessions[*]}"
    status_code=1
else
    printf 'Screen 后端：运行中（%s）\n' "${existing_sessions[0]}"
fi

if curl --fail --silent --show-error --max-time 3 "${DOMEYE_CORE_HEALTH_URL}" >/dev/null 2>&1; then
    printf '后端健康检查：正常（%s）\n' "${DOMEYE_CORE_HEALTH_URL}"
else
    printf '后端健康检查：失败（%s）\n' "${DOMEYE_CORE_HEALTH_URL}"
    status_code=1
fi

if curl --fail --silent --show-error --max-time 3 "${DOMEYE_CORE_FRONTEND_URL}" >/dev/null 2>&1; then
    printf '前端入口：正常（%s）\n' "${DOMEYE_CORE_FRONTEND_URL}"
else
    printf '前端入口：失败（%s）\n' "${DOMEYE_CORE_FRONTEND_URL}"
    status_code=1
fi

exit "${status_code}"
