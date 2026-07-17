#!/usr/bin/env bash

# 部署脚本共用的只读配置。这里刻意使用固定路径和固定会话名，避免误操作原项目。
readonly DOMEYE_CORE_ROOT='/home/bgpdata/Domeye-Core'
readonly DOMEYE_CORE_BACKEND_DIR="${DOMEYE_CORE_ROOT}/backend"
readonly DOMEYE_CORE_UV='/home/bgpdata/.local/bin/uv'
readonly DOMEYE_CORE_NODE_BIN_DIR='/home/bgpdata/.local/node-v22.23.1-linux-x64/bin'
readonly DOMEYE_CORE_RUNTIME_PATH="${DOMEYE_CORE_NODE_BIN_DIR}:/home/bgpdata/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
readonly DOMEYE_CORE_SCREEN_NAME='domeye_core_app'
readonly DOMEYE_CORE_BACKEND_HOST='127.0.0.1'
readonly DOMEYE_CORE_BACKEND_PORT='28473'
readonly DOMEYE_CORE_FRONTEND_PORT='28471'
readonly DOMEYE_CORE_BACKEND_DB_HOST='127.0.0.1'
readonly DOMEYE_CORE_BACKEND_DB_PORT='29429'
readonly DOMEYE_CORE_DB_CONTAINER='domeye_core_pg'
readonly DOMEYE_CORE_INFO_DIR="${DOMEYE_CORE_BACKEND_DIR}/info"
readonly DOMEYE_CORE_VENV_DIR="${DOMEYE_CORE_BACKEND_DIR}/.venv"
readonly DOMEYE_CORE_ARTIFACT_ROOT='/home/bgpdata/Domeye-Core-artifacts'
readonly DOMEYE_CORE_DATA_ROOT='/home/bgpdata/Domeye-Core-data'
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

domeye_core_validate_info_dir() {
    local info_dir="${1%/}"
    if [[ ! "${info_dir}" =~ ^/home/bgpdata/[A-Za-z0-9._-]+/backend/info$ || ! -d "${info_dir}" || -L "${info_dir}" ]]; then
        domeye_core_error "INFO_DIR 不在允许的项目边界或不是实际目录：${info_dir}"
        return 1
    fi
    local info_name
    for info_name in important_as.csv as_entity.csv ip_bgp_entity.csv country.xlsx; do
        if [[ ! -f "${info_dir}/${info_name}" || -L "${info_dir}/${info_name}" ]]; then
            domeye_core_error "INFO_DIR 缺少普通文件：${info_dir}/${info_name}"
            return 1
        fi
    done
}

# 仅从 Domeye Core Screen 的进程树读取 INFO_DIR；不会输出其他环境变量。
domeye_core_capture_backend_info_dir() {
    local -a sessions queue descendants children
    mapfile -t sessions < <(domeye_core_list_backend_sessions)
    if (( ${#sessions[@]} != 1 )); then
        return 1
    fi

    local screen_pid="${sessions[0]%%.*}"
    queue=("${screen_pid}")
    descendants=()
    local current_pid child_pid process_info captured_info=''
    while (( ${#queue[@]} > 0 )); do
        current_pid="${queue[0]}"
        queue=("${queue[@]:1}")
        descendants+=("${current_pid}")
        mapfile -t children < <(ps -o pid= --ppid "${current_pid}" 2>/dev/null | awk '{$1=$1; if ($1 ~ /^[0-9]+$/) print $1}')
        for child_pid in "${children[@]}"; do
            queue+=("${child_pid}")
        done
    done

    for current_pid in "${descendants[@]}"; do
        [[ -r "/proc/${current_pid}/environ" ]] || continue
        process_info="$(tr '\0' '\n' < "/proc/${current_pid}/environ" | awk -F= '$1 == "INFO_DIR" {sub(/^[^=]*=/, ""); print; exit}')"
        if [[ -n "${process_info}" ]]; then
            captured_info="${process_info%/}"
        fi
    done
    [[ -n "${captured_info}" ]] || return 1
    domeye_core_validate_info_dir "${captured_info}" || return 1
    printf '%s\n' "${captured_info}"
}
