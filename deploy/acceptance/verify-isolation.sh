#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"
# shellcheck source=../lib/database-common.sh
source "${SCRIPT_DIR}/../lib/database-common.sh"

if (( $# < 2 || $# > 5 )); then
    printf '用法：%s <发布清单> <需要隐藏的旧目录> [数据库配置] [数据库端口] [信息目录]\n' "${0##*/}" >&2
    exit 2
fi

readonly MANIFEST_PATH="$1"
readonly HIDDEN_PATH="$2"
readonly DATABASE_ENV_FILE="${3:-}"
readonly ISOLATION_DB_PORT="${4:-29429}"
readonly PROJECT_ROOT='/home/bgpdata/Domeye-Core'
readonly BACKEND_DIR="${PROJECT_ROOT}/backend"
readonly ISOLATION_INFO_DIR="${5:-${BACKEND_DIR}/info}"
readonly UV_BIN='/home/bgpdata/.local/bin/uv'

for command_name in curl find jq mount ps setsid ss unshare; do
    domeye_artifact_require_command "${command_name}"
done
domeye_artifact_require_regular_file "${MANIFEST_PATH}"
if [[ "${HIDDEN_PATH}" != '/home/bgpdata/Domeye' ]]; then
    domeye_artifact_error "隔离验收只允许隐藏固定旧目录：${HIDDEN_PATH}"
    exit 1
fi
if [[ ! -d "${HIDDEN_PATH}" || -L "${HIDDEN_PATH}" ]]; then
    domeye_artifact_error "待隐藏路径不存在或是软链接：${HIDDEN_PATH}"
    exit 1
fi
if [[ ! -d "${ISOLATION_INFO_DIR}" || -L "${ISOLATION_INFO_DIR}" ]]; then
    domeye_artifact_error "隔离验收信息目录无效：${ISOLATION_INFO_DIR}"
    exit 1
fi
for info_name in important_as.csv as_entity.csv ip_bgp_entity.csv country.xlsx; do
    domeye_artifact_require_regular_file "${ISOLATION_INFO_DIR}/${info_name}"
done
if [[ -n "${DATABASE_ENV_FILE}" ]]; then
    domeye_database_load_env "${DATABASE_ENV_FILE}"
    domeye_database_validate_config
else
    DOMEYE_CORE_DB_NAME=''
    DOMEYE_CORE_DB_READER_USER=''
    DOMEYE_CORE_DB_READER_PASSWORD=''
    DOMEYE_CORE_SECRET_KEY=''
fi

test_port=''
for (( attempt = 1; attempt <= 100; attempt++ )); do
    candidate_port="$(( 32000 + RANDOM % 12000 ))"
    if ! ss -H -ltn "sport = :${candidate_port}" | grep -q .; then
        test_port="${candidate_port}"
        break
    fi
done
if [[ -z "${test_port}" ]]; then
    domeye_artifact_error '未找到可用的高位隔离验收端口'
    exit 1
fi

feature_start="$(jq -r '.acceptance.feature_window.start_time' "${MANIFEST_PATH}")"
feature_end="$(jq -r '.acceptance.feature_window.end_time' "${MANIFEST_PATH}")"
feature_asn="$(jq -r '.acceptance.feature_window.asn' "${MANIFEST_PATH}")"

unshare --mount --propagation private /usr/bin/env -i \
    PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bgpdata/.local/bin' \
    DOMEYE_ISOLATION_DB_NAME="${DOMEYE_CORE_DB_NAME}" \
    DOMEYE_ISOLATION_DB_USER="${DOMEYE_CORE_DB_READER_USER}" \
    DOMEYE_ISOLATION_DB_PASSWORD="${DOMEYE_CORE_DB_READER_PASSWORD}" \
    DOMEYE_ISOLATION_SECRET_KEY="${DOMEYE_CORE_SECRET_KEY}" \
    DOMEYE_ISOLATION_DB_PORT="${ISOLATION_DB_PORT}" \
    /usr/bin/bash -Eeuo pipefail -c '
        hidden_path="$1"
        backend_dir="$2"
        uv_bin="$3"
        test_port="$4"
        feature_start="$5"
        feature_end="$6"
        feature_asn="$7"
        info_dir="$8"
        empty_mount="$(mktemp -d)"
        log_file="$(mktemp)"
        test_pid=""
        cleanup_inner() {
            local exit_code=$?
            if [[ -n "${test_pid}" ]]; then
                kill -- "-${test_pid}" >/dev/null 2>&1 || true
                wait "${test_pid}" 2>/dev/null || true
            fi
            umount "${hidden_path}" >/dev/null 2>&1 || true
            if (( exit_code != 0 )); then
                printf "隔离验收后端最近日志：\n" >&2
                tail -n 80 "${log_file}" >&2 || true
            fi
            rm -rf -- "${empty_mount}"
            rm -f -- "${log_file}"
            return "${exit_code}"
        }
        trap cleanup_inner EXIT

        mount --bind "${empty_mount}" "${hidden_path}"
        cd -- "${backend_dir}"
        database_env=()
        if [[ -n "${DOMEYE_ISOLATION_DB_NAME}" ]]; then
            database_env=(
                DB_HOST=127.0.0.1
                "DB_PORT=${DOMEYE_ISOLATION_DB_PORT}"
                "DB_NAME=${DOMEYE_ISOLATION_DB_NAME}"
                "DB_USER=${DOMEYE_ISOLATION_DB_USER}"
                "DB_PASSWORD=${DOMEYE_ISOLATION_DB_PASSWORD}"
                "SECRET_KEY=${DOMEYE_ISOLATION_SECRET_KEY}"
            )
        fi
        setsid env \
            HOST=127.0.0.1 \
            PORT="${test_port}" \
            DEBUG=false \
            AUTO_INIT_DB=false \
            LOAD_CORE_DATA_ON_STARTUP=false \
            INFO_DIR="${info_dir}" \
            PYTHONUNBUFFERED=1 \
            "${database_env[@]}" \
            "${uv_bin}" run --frozen python run.py \
            >"${log_file}" 2>&1 &
        test_pid=$!

        for (( inner_attempt = 1; inner_attempt <= 60; inner_attempt++ )); do
            if curl --fail --silent --max-time 2 "http://127.0.0.1:${test_port}/api/v1/healthz" >/dev/null 2>&1; then
                break
            fi
            if ! kill -0 "${test_pid}" >/dev/null 2>&1; then
                tail -n 50 "${log_file}" >&2
                exit 1
            fi
            sleep 1
        done

        curl --fail --silent --show-error --max-time 90 --get \
            --data-urlencode "target=${feature_asn}" \
            --data-urlencode "start_time=${feature_start}" \
            --data-urlencode "end_time=${feature_end}" \
            "http://127.0.0.1:${test_port}/api/v1/features/top" \
            | jq -e "type == \"array\" and length > 0" >/dev/null

        mapfile -t test_processes < <(
            ps -eo pid=,pgid= | awk -v pgid="${test_pid}" "\$2 == pgid {print \$1}"
        )
        if (( ${#test_processes[@]} == 0 )); then
            printf "未找到隔离后端进程组成员。\n" >&2
            exit 1
        fi
        for process_pid in "${test_processes[@]}"; do
            if [[ -d "/proc/${process_pid}/fd" ]] \
                && find "/proc/${process_pid}/fd" \( -lname "${hidden_path}" -o -lname "${hidden_path}/*" \) -print -quit | grep -q .; then
                printf "隔离进程仍持有旧目录文件描述符：PID %s。\n" "${process_pid}" >&2
                exit 1
            fi
            [[ -r "/proc/${process_pid}/environ" ]] || continue
            if tr "\0" "\n" < "/proc/${process_pid}/environ" | grep -E "=${hidden_path}(/|$)" >/dev/null; then
                printf "隔离进程环境仍包含旧目录：PID %s。\n" "${process_pid}" >&2
                exit 1
            fi
            if tr "\0" "\n" < "/proc/${process_pid}/environ" | grep -E "^(DOMEYE_CORE_DB_ADMIN_PASSWORD|SOURCE_DB_[A-Z_]+)=" >/dev/null; then
                printf "隔离进程环境泄露管理员或源数据库配置：PID %s。\n" "${process_pid}" >&2
                exit 1
            fi
        done
        if grep -E "${hidden_path}(/|$)" "${log_file}" >/dev/null; then
            printf "隔离进程日志出现旧目录引用。\n" >&2
            exit 1
        fi
    ' _ \
    "${HIDDEN_PATH}" \
    "${BACKEND_DIR}" \
    "${UV_BIN}" \
    "${test_port}" \
    "${feature_start}" \
    "${feature_end}" \
    "${feature_asn}" \
    "${ISOLATION_INFO_DIR}"

printf '旧目录不可见条件下的冷启动、真实特征查询和文件描述符检查通过。\n'
