#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/backend-common.sh
source "${SCRIPT_DIR}/lib/backend-common.sh"
# shellcheck source=lib/data-profile.sh
source "${SCRIPT_DIR}/lib/data-profile.sh"

if [[ "${DOMEYE_CORE_ACTIVE_DATA_PROFILE}" == 'feb-mar-2026' ]]; then
    status_code=0
    printf '当前数据档：%s（%s <= t < %s）\n' \
        "${DOMEYE_CORE_ACTIVE_DATA_PROFILE}" \
        "${DOMEYE_CORE_FIXED_DATA_START}" \
        "${DOMEYE_CORE_FIXED_DATA_END_EXCLUSIVE}"
    "${DOMEYE_CORE_ROOT}/dev/database/manage-dev-database.sh" status || status_code=1
    "${DOMEYE_CORE_ROOT}/deploy/manage-fixed-backend.sh" status || status_code=1
    if curl --fail --silent --show-error --max-time 3 "${DOMEYE_CORE_FRONTEND_URL}" >/dev/null 2>&1; then
        printf '前端入口：正常（%s）\n' "${DOMEYE_CORE_FRONTEND_URL}"
    else
        printf '前端入口：失败（%s）\n' "${DOMEYE_CORE_FRONTEND_URL}"
        status_code=1
    fi
    exit "${status_code}"
fi

domeye_core_require_command screen
domeye_core_require_command curl
domeye_core_require_command docker

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

if [[ "$(docker inspect --format '{{.State.Running}}' "${DOMEYE_CORE_DB_CONTAINER}" 2>/dev/null || true)" == 'true' ]]; then
    printf '独立数据库容器：运行中（%s）\n' "${DOMEYE_CORE_DB_CONTAINER}"
    if docker exec "${DOMEYE_CORE_DB_CONTAINER}" pg_isready -q >/dev/null 2>&1; then
        printf '独立数据库：可连接（%s:%s）\n' "${DOMEYE_CORE_BACKEND_DB_HOST}" "${DOMEYE_CORE_BACKEND_DB_PORT}"
    else
        printf '独立数据库：未就绪（%s:%s）\n' "${DOMEYE_CORE_BACKEND_DB_HOST}" "${DOMEYE_CORE_BACKEND_DB_PORT}"
        status_code=1
    fi
else
    printf '独立数据库容器：未运行（%s）\n' "${DOMEYE_CORE_DB_CONTAINER}"
    status_code=1
fi

for info_file in important_as.csv as_entity.csv ip_bgp_entity.csv country.xlsx; do
    info_path="${DOMEYE_CORE_INFO_DIR}/${info_file}"
    if [[ ! -f "${info_path}" || -L "${info_path}" ]]; then
        printf '基础信息文件：异常（%s）\n' "${info_path}"
        status_code=1
    fi
done

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
