#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly DEPLOY_DIR="${SCRIPT_DIR}/.."
# shellcheck source=../lib/artifact-common.sh
source "${DEPLOY_DIR}/lib/artifact-common.sh"
# shellcheck source=../lib/database-common.sh
source "${DEPLOY_DIR}/lib/database-common.sh"

if (( $# < 3 || $# > 4 )); then
    printf '用法：%s <发布目录> <数据库配置> <待隐藏旧目录> [入口地址输出文件]\n' "${0##*/}" >&2
    exit 2
fi

readonly RELEASE_DIR="${1%/}"
readonly DATABASE_ENV_FILE="$2"
readonly HIDDEN_PATH="$3"
readonly URL_OUTPUT_FILE="${4:-}"
readonly MANIFEST_PATH="${RELEASE_DIR}/${DOMEYE_CORE_RELEASE_MANIFEST}"
readonly PROJECT_ROOT='/home/bgpdata/Domeye-Core'
readonly RELEASE_ID="$(jq -r '.release_id' "${MANIFEST_PATH}")"
readonly RELEASE_DATA_ROOT="${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/${RELEASE_ID}"
readonly DATA_DIR="${RELEASE_DATA_ROOT}/postgres"
readonly RESTORE_STATE="${RELEASE_DATA_ROOT}/restore-state.json"

for command_name in curl docker find jq nginx ps setsid ss; do
    domeye_artifact_require_command "${command_name}"
done
"${DEPLOY_DIR}/artifacts/verify-release.sh" "${RELEASE_DIR}"
domeye_database_load_env "${DATABASE_ENV_FILE}"
domeye_database_validate_config
domeye_artifact_require_regular_file "${DATA_DIR}/PG_VERSION"
domeye_artifact_require_regular_file "${RESTORE_STATE}"

if [[ -L "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}" && "$(readlink -f "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}")" == "${DATA_DIR}" ]]; then
    domeye_artifact_error '候选 PGDATA 已是生产活动目录，拒绝并发启动第二个 PostgreSQL 实例'
    exit 1
fi
if [[ "$(jq -r '.image_id' "${RESTORE_STATE}")" != "$(docker image inspect --format '{{.Id}}' "${DOMEYE_CORE_DB_IMAGE}")" ]]; then
    domeye_artifact_error '候选数据库镜像 ID 与恢复状态不一致'
    exit 1
fi

choose_port() {
    local candidate
    for (( choose_attempt = 1; choose_attempt <= 200; choose_attempt++ )); do
        candidate="$(( 30000 + RANDOM % 15000 ))"
        if ! ss -H -ltn "sport = :${candidate}" | grep -q .; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    domeye_artifact_error '未找到可用的候选高位端口'
    return 1
}

readonly CANDIDATE_DB_PORT="$(choose_port)"
readonly CANDIDATE_BACKEND_PORT="$(choose_port)"
readonly CANDIDATE_FRONTEND_PORT="$(choose_port)"
if [[ "${CANDIDATE_DB_PORT}" == "${CANDIDATE_BACKEND_PORT}" || "${CANDIDATE_DB_PORT}" == "${CANDIDATE_FRONTEND_PORT}" || "${CANDIDATE_BACKEND_PORT}" == "${CANDIDATE_FRONTEND_PORT}" ]]; then
    domeye_artifact_error '随机端口发生重复，请重新执行候选验收'
    exit 1
fi

container_suffix="${RELEASE_ID//[^a-zA-Z0-9]/_}"
readonly CANDIDATE_CONTAINER="domeye_core_accept_${container_suffix}_$$"
work_dir="$(mktemp -d)"
readonly BACKEND_LOG="${work_dir}/backend.log"
readonly NGINX_CONF="${work_dir}/nginx.conf"
readonly NGINX_PID_FILE="${work_dir}/nginx.pid"
readonly CANDIDATE_INFO_DIR="${work_dir}/info"
backend_pid=''

cleanup() {
    local exit_code=$?
    if [[ -n "${backend_pid}" ]]; then
        kill -- "-${backend_pid}" >/dev/null 2>&1 || true
        wait "${backend_pid}" 2>/dev/null || true
    fi
    if [[ -f "${NGINX_PID_FILE}" ]]; then
        kill "$(<"${NGINX_PID_FILE}")" >/dev/null 2>&1 || true
    fi
    domeye_database_remove_candidate_container "${CANDIDATE_CONTAINER}" || true
    if (( exit_code != 0 )) && [[ -f "${BACKEND_LOG}" ]]; then
        printf '候选后端最近日志：\n' >&2
        tail -n 80 "${BACKEND_LOG}" >&2 || true
    fi
    if [[ -d "${work_dir}" ]]; then
        rm -rf -- "${work_dir}"
    fi
    return "${exit_code}"
}
trap cleanup EXIT

"${DEPLOY_DIR}/artifacts/stage-info-artifact.sh" "${RELEASE_DIR}" "${CANDIDATE_INFO_DIR}"

docker run --detach \
    --name "${CANDIDATE_CONTAINER}" \
    --memory "${DOMEYE_CORE_DATABASE_MEMORY}" \
    --shm-size 4g \
    --publish "127.0.0.1:${CANDIDATE_DB_PORT}:5432" \
    --env "POSTGRES_DB=${DOMEYE_CORE_DB_NAME}" \
    --env "POSTGRES_USER=${DOMEYE_CORE_DB_ADMIN_USER}" \
    --env "POSTGRES_PASSWORD=${DOMEYE_CORE_DB_ADMIN_PASSWORD}" \
    --volume "${DATA_DIR}:/var/lib/postgresql/data" \
    "${DOMEYE_CORE_DB_IMAGE}" \
    postgres \
    -c "shared_buffers=${DOMEYE_CORE_DATABASE_SHARED_BUFFERS}" \
    -c 'listen_addresses=*' \
    -c 'timescaledb.telemetry_level=off' \
    >/dev/null
domeye_database_wait_container "${CANDIDATE_CONTAINER}"

(
    cd -- "${PROJECT_ROOT}/backend"
    exec setsid env -i \
        HOME=/home/bgpdata \
        USER=bgpdata \
        LANG=C.UTF-8 \
        PATH='/home/bgpdata/.local/node-v22.23.1-linux-x64/bin:/home/bgpdata/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' \
        FLASK_CONFIG=production \
        SECRET_KEY="${DOMEYE_CORE_SECRET_KEY}" \
        HOST=127.0.0.1 \
        PORT="${CANDIDATE_BACKEND_PORT}" \
        DEBUG=false \
        AUTO_INIT_DB=false \
        LOAD_CORE_DATA_ON_STARTUP=false \
        INFO_DIR="${CANDIDATE_INFO_DIR}" \
        DB_HOST=127.0.0.1 \
        DB_PORT="${CANDIDATE_DB_PORT}" \
        DB_NAME="${DOMEYE_CORE_DB_NAME}" \
        DB_USER="${DOMEYE_CORE_DB_READER_USER}" \
        DB_PASSWORD="${DOMEYE_CORE_DB_READER_PASSWORD}" \
        PYTHONUNBUFFERED=1 \
        /home/bgpdata/.local/bin/uv run --frozen python run.py
) > "${BACKEND_LOG}" 2>&1 &
backend_pid=$!

for (( backend_attempt = 1; backend_attempt <= 90; backend_attempt++ )); do
    if curl --fail --silent --max-time 2 "http://127.0.0.1:${CANDIDATE_BACKEND_PORT}/api/v1/healthz" >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "${backend_pid}" >/dev/null 2>&1; then
        domeye_artifact_error '候选后端在健康检查前退出'
        exit 1
    fi
    sleep 1
done
if ! curl --fail --silent --max-time 3 "http://127.0.0.1:${CANDIDATE_BACKEND_PORT}/api/v1/healthz" >/dev/null; then
    domeye_artifact_error '候选后端 90 秒内未就绪'
    exit 1
fi
mapfile -t backend_processes < <(
    ps -eo pid=,pgid= | awk -v pgid="${backend_pid}" '$2 == pgid {print $1}'
)
if (( ${#backend_processes[@]} == 0 )); then
    domeye_artifact_error '未找到候选后端进程组成员'
    exit 1
fi
for process_pid in "${backend_processes[@]}"; do
    if [[ -d "/proc/${process_pid}/fd" ]] \
        && find "/proc/${process_pid}/fd" \( -lname "${HIDDEN_PATH}" -o -lname "${HIDDEN_PATH}/*" \) -print -quit | grep -q .; then
        domeye_artifact_error "候选后端进程仍持有旧项目文件描述符：PID ${process_pid}"
        exit 1
    fi
    [[ -r "/proc/${process_pid}/environ" ]] || continue
    if tr '\0' '\n' < "/proc/${process_pid}/environ" \
        | grep -E '^(DOMEYE_CORE_DB_ADMIN_PASSWORD|SOURCE_DB_[A-Z_]+)=' >/dev/null; then
        domeye_artifact_error "候选后端进程环境泄露管理员或源数据库配置：PID ${process_pid}"
        exit 1
    fi
    if tr '\0' '\n' < "/proc/${process_pid}/environ" \
        | grep -E "=${HIDDEN_PATH}(/|$)" >/dev/null; then
        domeye_artifact_error "候选后端进程环境仍包含旧项目路径：PID ${process_pid}"
        exit 1
    fi
done

{
    printf '%s\n' \
        'worker_processes 1;' \
        "pid ${NGINX_PID_FILE};" \
        "error_log ${work_dir}/nginx-error.log;" \
        'events { worker_connections 256; }' \
        'http {' \
        '  access_log off;' \
        '  include /etc/nginx/mime.types;' \
        '  server {' \
        "    listen 127.0.0.1:${CANDIDATE_FRONTEND_PORT};" \
        "    root ${PROJECT_ROOT}/frontend/dist;" \
        '    index index.html;' \
        '    location = /api/v1 { return 308 /api/v1/; }' \
        '    location ^~ /api/v1/ {' \
        "      proxy_pass http://127.0.0.1:${CANDIDATE_BACKEND_PORT};" \
        '      proxy_http_version 1.1;' \
        '      proxy_connect_timeout 3s;' \
        '      proxy_read_timeout 90s;' \
        '    }' \
        '    location / { try_files $uri $uri/ /index.html; }' \
        '  }' \
        '}'
} > "${NGINX_CONF}"
nginx -t -c "${NGINX_CONF}" -p "${work_dir}/"
nginx -c "${NGINX_CONF}" -p "${work_dir}/"

readonly CANDIDATE_URL="http://127.0.0.1:${CANDIDATE_FRONTEND_PORT}"
"${SCRIPT_DIR}/smoke.sh" "${MANIFEST_PATH}" "${CANDIDATE_URL}"
"${SCRIPT_DIR}/verify-isolation.sh" \
    "${MANIFEST_PATH}" \
    "${HIDDEN_PATH}" \
    "${DATABASE_ENV_FILE}" \
    "${CANDIDATE_DB_PORT}" \
    "${CANDIDATE_INFO_DIR}"

if [[ -n "${URL_OUTPUT_FILE}" ]]; then
    printf '%s\n' "${CANDIDATE_URL}" > "${URL_OUTPUT_FILE}"
fi
printf '候选数据库、临时后端、临时 Nginx、核心冒烟和旧目录隔离验收全部通过。\n'
