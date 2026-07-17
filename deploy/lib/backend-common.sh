#!/usr/bin/env bash

# 部署脚本共用的只读配置。这里刻意使用固定路径和固定会话名，避免误操作原项目。
readonly DOMEYE_CORE_ROOT='/home/bgpdata/Domeye-Core'
readonly DOMEYE_CORE_BACKEND_DIR="${DOMEYE_CORE_ROOT}/backend"
readonly DOMEYE_CORE_UV='/home/bgpdata/.local/bin/uv'
readonly DOMEYE_CORE_SCREEN_NAME='domeye_core_app'
readonly DOMEYE_CORE_BACKEND_HOST='127.0.0.1'
readonly DOMEYE_CORE_BACKEND_PORT='28473'
readonly DOMEYE_CORE_FRONTEND_PORT='28471'
readonly DOMEYE_CORE_INFO_DIR='/home/bgpdata/Domeye/backend/info'
readonly DOMEYE_CORE_LOG_DIR="${DOMEYE_CORE_ROOT}/var/log"
readonly DOMEYE_CORE_BACKEND_LOG="${DOMEYE_CORE_LOG_DIR}/backend-screen.log"
readonly DOMEYE_CORE_HEALTH_URL="http://${DOMEYE_CORE_BACKEND_HOST}:${DOMEYE_CORE_BACKEND_PORT}/api/v1/healthz"
readonly DOMEYE_CORE_FRONTEND_URL="http://127.0.0.1:${DOMEYE_CORE_FRONTEND_PORT}/"

domeye_core_error() {
    printf '错误：%s\n' "$*" >&2
}

domeye_core_require_command() {
    local command_name="$1"

    if ! command -v "${command_name}" >/dev/null 2>&1; then
        domeye_core_error "缺少命令：${command_name}"
        return 1
    fi
}

# 只返回完整名称恰好为 PID.domeye_core_app 的 Screen 会话。
# 原项目的 PID.app 不会被匹配。
domeye_core_list_backend_sessions() {
    screen -ls 2>/dev/null | awk -v suffix=".${DOMEYE_CORE_SCREEN_NAME}" '
        $1 ~ /^[0-9]+\./ && substr($1, length($1) - length(suffix) + 1) == suffix {
            print $1
        }
    '
}

domeye_core_tail_backend_log() {
    if [[ -f "${DOMEYE_CORE_BACKEND_LOG}" ]]; then
        printf '\n最近的后端日志：\n' >&2
        tail -n 30 "${DOMEYE_CORE_BACKEND_LOG}" >&2 || true
    fi
}
