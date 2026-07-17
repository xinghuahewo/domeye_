#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/backend-common.sh
source "${SCRIPT_DIR}/lib/backend-common.sh"

domeye_core_require_command screen

mapfile -t existing_sessions < <(domeye_core_list_backend_sessions)

if (( ${#existing_sessions[@]} > 1 )); then
    domeye_core_error "发现多个同名后端会话，拒绝自动操作：${existing_sessions[*]}"
    exit 1
fi

if (( ${#existing_sessions[@]} == 1 )); then
    printf '后端已在运行：%s\n' "${existing_sessions[0]}"
    exit 0
fi

if [[ ! -x "${DOMEYE_CORE_UV}" ]]; then
    domeye_core_error "uv 不存在或不可执行：${DOMEYE_CORE_UV}"
    exit 1
fi

if [[ ! -f "${DOMEYE_CORE_BACKEND_DIR}/run.py" ]]; then
    domeye_core_error "后端入口不存在：${DOMEYE_CORE_BACKEND_DIR}/run.py"
    exit 1
fi

if [[ ! -f "${DOMEYE_CORE_BACKEND_DIR}/uv.lock" ]]; then
    domeye_core_error "依赖锁文件不存在：${DOMEYE_CORE_BACKEND_DIR}/uv.lock"
    exit 1
fi

if [[ ! -f "${DOMEYE_CORE_BACKEND_DIR}/.env" ]]; then
    domeye_core_error "缺少生产环境配置：${DOMEYE_CORE_BACKEND_DIR}/.env"
    exit 1
fi

configured_db_host="$(awk -F= '$1 == "DB_HOST" {gsub(/^[[:space:]\"\047]+|[[:space:]\"\047]+$/, "", $2); print $2; exit}' "${DOMEYE_CORE_BACKEND_DIR}/.env")"
configured_db_port="$(awk -F= '$1 == "DB_PORT" {gsub(/^[[:space:]\"\047]+|[[:space:]\"\047]+$/, "", $2); print $2; exit}' "${DOMEYE_CORE_BACKEND_DIR}/.env")"
runtime_info_dir="${DOMEYE_CORE_INFO_DIR}"
if [[ "${DOMEYE_CORE_ALLOW_ROLLBACK_CONFIG:-false}" != true ]]; then
    if [[ "${configured_db_host}" != "${DOMEYE_CORE_BACKEND_DB_HOST}" || "${configured_db_port}" != "${DOMEYE_CORE_BACKEND_DB_PORT}" ]]; then
        domeye_core_error "生产 .env 必须连接独立数据库 ${DOMEYE_CORE_BACKEND_DB_HOST}:${DOMEYE_CORE_BACKEND_DB_PORT}"
        exit 1
    fi
else
    runtime_info_dir="${DOMEYE_CORE_ROLLBACK_INFO_DIR:-}"
    if [[ -z "${runtime_info_dir}" ]]; then
        runtime_info_dir="$(awk -F= '$1 == "INFO_DIR" {sub(/^[^=]*=/, ""); gsub(/^[[:space:]\"\047]+|[[:space:]\"\047]+$/, ""); print; exit}' "${DOMEYE_CORE_BACKEND_DIR}/.env")"
    fi
    if [[ -z "${runtime_info_dir}" ]]; then
        domeye_core_error '回滚启动缺少切换前实际 INFO_DIR'
        exit 1
    fi
fi

domeye_core_validate_info_dir "${runtime_info_dir}"

if [[ ! -x "${DOMEYE_CORE_VENV_DIR}/bin/python" ]]; then
    domeye_core_error "缺少 uv 锁定环境，请先执行 uv sync --frozen：${DOMEYE_CORE_VENV_DIR}"
    exit 1
fi

install -d -m 0750 "${DOMEYE_CORE_LOG_DIR}"

# 所有关键运行参数在进程环境中显式覆盖，避免迁移来的 .env 使用旧端口或开启调试模式。
screen \
    -L \
    -Logfile "${DOMEYE_CORE_BACKEND_LOG}" \
    -dmS "${DOMEYE_CORE_SCREEN_NAME}" \
    env -i \
        HOME=/home/bgpdata \
        USER=bgpdata \
        LANG=C.UTF-8 \
        PATH="${DOMEYE_CORE_RUNTIME_PATH}" \
        FLASK_CONFIG=production \
        HOST="${DOMEYE_CORE_BACKEND_HOST}" \
        PORT="${DOMEYE_CORE_BACKEND_PORT}" \
        DEBUG=false \
        AUTO_INIT_DB=false \
        LOAD_CORE_DATA_ON_STARTUP=false \
        INFO_DIR="${runtime_info_dir%/}" \
        PYTHONUNBUFFERED=1 \
        UV_PROJECT_ENVIRONMENT="${DOMEYE_CORE_VENV_DIR}" \
        "${DOMEYE_CORE_UV}" run \
            --directory "${DOMEYE_CORE_BACKEND_DIR}" \
            --frozen \
            python run.py

if ! command -v curl >/dev/null 2>&1; then
    printf '后端会话已启动：%s（未找到 curl，跳过健康检查）\n' "${DOMEYE_CORE_SCREEN_NAME}"
    exit 0
fi

for (( attempt = 1; attempt <= 30; attempt++ )); do
    mapfile -t running_sessions < <(domeye_core_list_backend_sessions)
    if (( ${#running_sessions[@]} == 0 )); then
        domeye_core_error '后端进程在健康检查完成前退出。'
        domeye_core_tail_backend_log
        exit 1
    fi

    if curl --fail --silent --show-error --max-time 2 "${DOMEYE_CORE_HEALTH_URL}" >/dev/null 2>&1; then
        printf '后端启动成功：%s\n' "${running_sessions[0]}"
        printf '健康检查：%s\n' "${DOMEYE_CORE_HEALTH_URL}"
        exit 0
    fi

    sleep 1
done

domeye_core_error "后端会话仍在运行，但 30 秒内未通过健康检查：${DOMEYE_CORE_HEALTH_URL}"
domeye_core_tail_backend_log
exit 1
