#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/backend-common.sh
source "${SCRIPT_DIR}/lib/backend-common.sh"

domeye_core_require_command screen

mapfile -t existing_sessions < <(domeye_core_list_backend_sessions)

if (( ${#existing_sessions[@]} == 0 )); then
    printf '后端已经停止，无需操作。\n'
    exit 0
fi

if (( ${#existing_sessions[@]} > 1 )); then
    domeye_core_error "发现多个同名后端会话，拒绝自动停止：${existing_sessions[*]}"
    exit 1
fi

readonly target_session="${existing_sessions[0]}"
printf '正在停止后端会话：%s\n' "${target_session}"
screen -S "${target_session}" -X quit

for (( attempt = 1; attempt <= 10; attempt++ )); do
    mapfile -t remaining_sessions < <(domeye_core_list_backend_sessions)
    if (( ${#remaining_sessions[@]} == 0 )); then
        printf '后端已停止。\n'
        exit 0
    fi
    sleep 1
done

domeye_core_error "后端会话未在 10 秒内退出：${target_session}"
exit 1
