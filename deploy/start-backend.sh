#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/backend-common.sh
source "${SCRIPT_DIR}/lib/backend-common.sh"
# shellcheck source=lib/data-profile.sh
source "${SCRIPT_DIR}/lib/data-profile.sh"

if [[ "${DOMEYE_CORE_ACTIVE_DATA_PROFILE}" == 'feb-mar-2026' ]]; then
    domeye_core_error '当前固定使用 2026 年 2–3 月数据；请使用 deploy/manage-fixed-backend.sh 启动后端'
    exit 1
fi
domeye_core_require_realtime_profile

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

configured_db_host="$(domeye_core_backend_env_value DB_HOST)"
configured_db_port="$(domeye_core_backend_env_value DB_PORT)"
readonly DATABASE_CURRENT_STATE="${DOMEYE_CORE_ROOT}/var/releases/database-current"
readonly DATABASE_ACTIVE_LINK="${DOMEYE_CORE_DATA_ROOT}/postgres"

explicit_rollback_mode=false
case "${DOMEYE_CORE_ALLOW_ROLLBACK_CONFIG:-false}" in
    true) explicit_rollback_mode=true ;;
    false|'') ;;
    *)
        domeye_core_error 'DOMEYE_CORE_ALLOW_ROLLBACK_CONFIG 只能为 true 或 false'
        exit 1
        ;;
esac

db_config_is_independent=false
if [[ "${configured_db_host}" == "${DOMEYE_CORE_BACKEND_DB_HOST}" \
    && "${configured_db_port}" == "${DOMEYE_CORE_BACKEND_DB_PORT}" ]]; then
    db_config_is_independent=true
fi

persistent_source_rollback_mode=false
if [[ "${explicit_rollback_mode}" != true \
    && ! -e "${DATABASE_CURRENT_STATE}" && ! -L "${DATABASE_CURRENT_STATE}" \
    && ! -e "${DATABASE_ACTIVE_LINK}" && ! -L "${DATABASE_ACTIVE_LINK}" \
    && "${db_config_is_independent}" != true ]]; then
    runtime_info_dir="$(domeye_core_backend_env_value INFO_DIR)"
    if [[ -z "${runtime_info_dir}" ]]; then
        domeye_core_error '非独立数据库配置缺少 INFO_DIR'
        exit 1
    fi
    domeye_core_validate_source_rollback_state "${runtime_info_dir%/}"
    persistent_source_rollback_mode=true
fi

runtime_info_dir="${runtime_info_dir:-${DOMEYE_CORE_INFO_DIR}}"
if [[ "${explicit_rollback_mode}" != true && "${persistent_source_rollback_mode}" != true ]]; then
    if [[ "${db_config_is_independent}" != true ]]; then
        domeye_core_error "生产 .env 必须连接独立数据库 ${DOMEYE_CORE_BACKEND_DB_HOST}:${DOMEYE_CORE_BACKEND_DB_PORT}"
        exit 1
    fi
else
    runtime_info_dir=''
    if [[ "${explicit_rollback_mode}" == true ]]; then
        runtime_info_dir="${DOMEYE_CORE_ROLLBACK_INFO_DIR:-}"
    fi
    if [[ -z "${runtime_info_dir}" ]]; then
        runtime_info_dir="$(domeye_core_backend_env_value INFO_DIR)"
    fi
    if [[ -z "${runtime_info_dir}" ]]; then
        domeye_core_error '回滚启动缺少切换前实际 INFO_DIR'
        exit 1
    fi
    if [[ "${persistent_source_rollback_mode}" == true ]]; then
        printf '检测到无活动独立数据库的持久回滚态，将使用已恢复的 .env。\n'
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
