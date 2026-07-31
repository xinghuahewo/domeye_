#!/usr/bin/env bash

set -Eeuo pipefail

readonly PROJECT_ROOT='/home/bgpdata/Domeye-Core'
readonly BACKEND_DIR="${PROJECT_ROOT}/backend"
readonly SCRIPT_PATH="${PROJECT_ROOT}/dev/backend/manage-dev-api.sh"
readonly INFO_STAGE_SCRIPT="${PROJECT_ROOT}/dev/backend/stage-dev-info.sh"
readonly DEV_DATA_ROOT='/home/bgpdata/Domeye-Core-dev-data'
readonly API_ROOT="${DEV_DATA_ROOT}/api"
readonly INFO_DIR="${API_ROOT}/info"
readonly SECRET_KEY_FILE="${API_ROOT}/secret-key"
readonly VENV_DIR="${API_ROOT}/.venv"
readonly LOG_DIR="${API_ROOT}/log"
readonly LOG_FILE="${LOG_DIR}/backend-screen.log"
readonly APP_LOG_DIR="${LOG_DIR}/app"
readonly LOCK_FILE="${DEV_DATA_ROOT}/.api-manage.lock"
readonly DATABASE_CONFIG='/home/bgpdata/Domeye-Core-data/config/database.env'
readonly DATABASE_STATE="${DEV_DATA_ROOT}/state.json"
readonly DATABASE_VERIFY_SQL="${PROJECT_ROOT}/dev/database/verify-feb-mar.sql"
readonly DATABASE_MERGED_DIR="${DEV_DATA_ROOT}/overlay/merged"
readonly DATABASE_CONTAINER='domeye_core_dev_pg'
readonly DATABASE_ROLE_LABEL='development-database'
readonly DATABASE_INSTANCE_LABEL='domeye-core-dev-feb-mar-2026'
readonly DATABASE_STATE_LABEL="${DATABASE_STATE}"
readonly EXPECTED_DATABASE_RELEASE_ID='20260717T124354Z'
readonly EXPECTED_DATABASE_RELEASE_DIR='/home/bgpdata/Domeye-Core-artifacts/releases/20260717T124354Z'
readonly EXPECTED_LOWER_PGDATA='/home/bgpdata/Domeye-Core-data/work/resume-20260717T124354Z-attempt3/postgres'
readonly API_PROFILE="${DOMEYE_CORE_API_PROFILE:-remote}"
case "${API_PROFILE}" in
    remote)
        SCREEN_NAME='domeye_core_dev_api'
        API_INSTANCE='domeye-core-dev-api-v1'
        API_PORT='31629'
        ;;
    core)
        SCREEN_NAME='domeye_core_app'
        API_INSTANCE='domeye-core-feb-mar-2026'
        API_PORT='28473'
        ;;
    *)
        printf '错误：不支持的开发 API 运行档：%s\n' "${API_PROFILE}" >&2
        exit 2
        ;;
esac
readonly SCREEN_NAME API_INSTANCE API_PORT
readonly API_HOST='127.0.0.1'
readonly HEALTH_URL="http://${API_HOST}:${API_PORT}/api/v1/healthz"
readonly DATABASE_SMOKE_URL="http://${API_HOST}:${API_PORT}/api/v1/events?datetime=2026-03-31%2000%3A00%3A00_2026-03-31%2023%3A59%3A59&page_num=1&page_size=10"
readonly STATIC_AS_WARMUP_URL="http://${API_HOST}:${API_PORT}/api/v1/features/ases?start_time=2026-03-31%2000%3A00%3A00&end_time=2026-03-31%2023%3A59%3A59&page_num=1&page_size=5"
readonly DATA_START='2026-02-01 00:00:00'
readonly DATA_END_EXCLUSIVE='2026-04-01 00:00:00'
readonly SNAPSHOT_TIME='2026-03-31 23:59:59'
readonly UV='/home/bgpdata/.local/bin/uv'
readonly RUNTIME_PATH='/home/bgpdata/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
readonly COUNTRY_OUTAGE_AGENT_RUNTIME_ENV='/home/bgpdata/Domeye-Core-runtime/config/country-outage-agent.env'
readonly COUNTRY_OUTAGE_AGENT_EXPECTED_URL='http://127.0.0.1:28474'
readonly COUNTRY_OUTAGE_AGENT_EXPECTED_IDENTITY_MODE='internal_fixed_history'
readonly COUNTRY_OUTAGE_AGENT_EXPECTED_SCOPE='country_outage_event_read:IR'
readonly COUNTRY_OUTAGE_AGENT_NODE='/home/bgpdata/.local/node-v22.23.1-linux-x64/bin/node'
readonly COUNTRY_OUTAGE_AGENT_PROBE_SCRIPT="${PROJECT_ROOT}/deploy/country-outage-agent/probe-sidecar.mjs"

DB_NAME=''
DB_READER_USER=''
DB_READER_PASSWORD=''
SECRET_KEY_VALUE=''
DB_PORT=''
DB_IMAGE_ID=''
DB_RELEASE_ID=''
DB_SYSTEM_IDENTIFIER=''
DB_CHECKPOINT_KEY=''
COUNTRY_OUTAGE_AGENT_ENABLED=false
COUNTRY_OUTAGE_AGENT_URL_VALUE=''
COUNTRY_OUTAGE_AGENT_SHARED_TOKEN_VALUE=''
COUNTRY_OUTAGE_AGENT_IDENTITY_MODE_VALUE=''
COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID_VALUE=''
COUNTRY_OUTAGE_AGENT_CONFIG_SHA256_VALUE=''

error() {
    printf '错误：%s\n' "$*" >&2
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        error "缺少命令：$1"
        return 1
    }
}

require_root() {
    if (( EUID != 0 )); then
        error '服务器开发 API 必须由 root 管理，以确保 Screen 归属唯一'
        return 1
    fi
}

validate_project_boundary() {
    if [[ ! -d "${PROJECT_ROOT}" || -L "${PROJECT_ROOT}" \
        || "$(readlink -f "${PROJECT_ROOT}")" != "${PROJECT_ROOT}" \
        || ! -f "${BACKEND_DIR}/run.py" || -L "${BACKEND_DIR}/run.py" \
        || ! -f "${BACKEND_DIR}/uv.lock" || -L "${BACKEND_DIR}/uv.lock" \
        || ! -f "${DATABASE_VERIFY_SQL}" || -L "${DATABASE_VERIFY_SQL}" \
        || "$(readlink -f "${DATABASE_VERIFY_SQL}")" != "${DATABASE_VERIFY_SQL}" ]]; then
        error "开发 API 项目边界或后端入口无效：${PROJECT_ROOT}"
        return 1
    fi
}

validate_dev_data_root() {
    local mode
    if [[ ! -d "${DEV_DATA_ROOT}" || -L "${DEV_DATA_ROOT}" \
        || "$(readlink -f "${DEV_DATA_ROOT}")" != "${DEV_DATA_ROOT}" \
        || "$(stat -c '%u' "${DEV_DATA_ROOT}")" != '0' ]]; then
        error "开发数据根目录不存在、越界或非 root 拥有：${DEV_DATA_ROOT}"
        return 1
    fi
    mode="$(stat -c '%a' "${DEV_DATA_ROOT}")"
    if (( (8#${mode} & 8#022) != 0 )); then
        error "开发数据根目录不得被组或其他用户写入：${DEV_DATA_ROOT}"
        return 1
    fi
}

ensure_api_root() {
    validate_dev_data_root
    if [[ -e "${API_ROOT}" || -L "${API_ROOT}" ]]; then
        local mode
        if [[ ! -d "${API_ROOT}" || -L "${API_ROOT}" \
            || "$(readlink -f "${API_ROOT}")" != "${API_ROOT}" \
            || "$(stat -c '%u' "${API_ROOT}")" != '0' ]]; then
            error "开发 API 根目录不安全：${API_ROOT}"
            return 1
        fi
        mode="$(stat -c '%a' "${API_ROOT}")"
        if (( (8#${mode} & 8#022) != 0 )); then
            error "开发 API 根目录不得被组或其他用户写入：${API_ROOT}"
            return 1
        fi
        return 0
    fi
    install -d -o 0 -g 0 -m 0750 "${API_ROOT}"
}

load_development_secret() {
    if [[ ! -f "${SECRET_KEY_FILE}" || -L "${SECRET_KEY_FILE}" \
        || "$(readlink -f "${SECRET_KEY_FILE}")" != "${SECRET_KEY_FILE}" \
        || "$(stat -c '%u' "${SECRET_KEY_FILE}")" != '0' \
        || "$(stat -c '%a' "${SECRET_KEY_FILE}")" != '600' ]]; then
        error "开发 SECRET_KEY 必须是 root 拥有的 0600 普通文件：${SECRET_KEY_FILE}"
        return 1
    fi
    SECRET_KEY_VALUE="$(<"${SECRET_KEY_FILE}")"
    if [[ ! "${SECRET_KEY_VALUE}" =~ ^[0-9a-f]{64}$ ]]; then
        error '开发 SECRET_KEY 内容必须是 32 字节随机值的小写十六进制形式'
        return 1
    fi
}

ensure_development_secret() {
    ensure_api_root
    if [[ -e "${SECRET_KEY_FILE}" || -L "${SECRET_KEY_FILE}" ]]; then
        load_development_secret
        return
    fi
    local temporary="${API_ROOT}/.secret-key.tmp.$$"
    if ! (
        umask 077
        openssl rand -hex 32 > "${temporary}"
    ); then
        rm -f -- "${temporary}"
        error '生成独立开发 SECRET_KEY 失败'
        return 1
    fi
    if ! chmod 0600 "${temporary}" \
        || ! chown 0:0 "${temporary}" \
        || ! mv -T -- "${temporary}" "${SECRET_KEY_FILE}"; then
        rm -f -- "${temporary}"
        error '安装独立开发 SECRET_KEY 失败'
        return 1
    fi
    load_development_secret || return 1
}

read_config_value() {
    local key="$1"
    local -a values
    mapfile -t values < <(
        awk -v wanted="${key}" '
            /^[[:space:]]*(#|$)/ { next }
            {
                separator = index($0, "=")
                if (separator == 0) next
                name = substr($0, 1, separator - 1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
                if (name != wanted) next
                value = substr($0, separator + 1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
                print value
            }
        ' "${DATABASE_CONFIG}"
    )
    if (( ${#values[@]} != 1 )); then
        error "数据库配置键必须恰好出现一次：${key}"
        return 1
    fi
    local value="${values[0]}"
    if [[ "${value}" == \"*\" && "${value}" == *\" ]]; then
        value="${value:1:${#value}-2}"
    elif [[ "${value}" == \'*\' && "${value}" == *\' ]]; then
        value="${value:1:${#value}-2}"
    fi
    if [[ -z "${value}" || "${value}" =~ [[:space:]] ]]; then
        error "数据库配置键不得为空或包含空白：${key}"
        return 1
    fi
    printf '%s\n' "${value}"
}

read_country_outage_agent_config_value() {
    local key="$1"
    local -a values
    mapfile -t values < <(
        awk -v wanted="${key}" '
            /^[[:space:]]*(#|$)/ { next }
            {
                separator = index($0, "=")
                if (separator == 0) next
                name = substr($0, 1, separator - 1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
                if (name != wanted) next
                value = substr($0, separator + 1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
                print value
            }
        ' "${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV}"
    )
    if (( ${#values[@]} != 1 )); then
        error "国家中断 Agent 运行配置键必须恰好出现一次：${key}"
        return 1
    fi
    local value="${values[0]}"
    if [[ "${value}" == \"*\" && "${value}" == *\" ]]; then
        value="${value:1:${#value}-2}"
    elif [[ "${value}" == \'*\' && "${value}" == *\' ]]; then
        value="${value:1:${#value}-2}"
    fi
    if [[ -z "${value}" || "${value}" =~ [[:space:]] ]]; then
        error "国家中断 Agent 运行配置键不得为空或包含空白：${key}"
        return 1
    fi
    printf '%s\n' "${value}"
}

load_country_outage_agent_runtime_config() {
    COUNTRY_OUTAGE_AGENT_ENABLED=false
    COUNTRY_OUTAGE_AGENT_URL_VALUE=''
    COUNTRY_OUTAGE_AGENT_SHARED_TOKEN_VALUE=''
    COUNTRY_OUTAGE_AGENT_IDENTITY_MODE_VALUE=''
    COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID_VALUE=''
    COUNTRY_OUTAGE_AGENT_CONFIG_SHA256_VALUE=''

    # 远程开发档保持既有行为；只有对外的固定历史观测档消费正式 Agent 配置。
    if [[ "${API_PROFILE}" != 'core' ]]; then
        return 0
    fi
    if [[ ! -e "${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV}" \
        && ! -L "${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV}" ]]; then
        return 0
    fi

    local config_dir="${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV%/*}"
    local dir_mode
    if [[ "${config_dir}" != '/home/bgpdata/Domeye-Core-runtime/config' \
        || ! -d "${config_dir}" || -L "${config_dir}" \
        || "$(readlink -f "${config_dir}")" != "${config_dir}" \
        || "$(stat -c '%u' "${config_dir}")" != '0' ]]; then
        error "国家中断 Agent 配置目录不是 root 拥有的固定实际目录：${config_dir}"
        return 1
    fi
    dir_mode="$(stat -c '%a' "${config_dir}")"
    if (( (8#${dir_mode} & 8#022) != 0 )); then
        error '国家中断 Agent 配置目录不得被组或其他用户写入'
        return 1
    fi
    if [[ ! -f "${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV}" \
        || -L "${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV}" \
        || "$(readlink -f "${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV}")" \
            != "${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV}" \
        || "$(stat -c '%u' "${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV}")" != '0' \
        || "$(stat -c '%a' "${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV}")" != '600' ]]; then
        error '国家中断 Agent 运行配置必须是 root 拥有的 0600 实际普通文件'
        return 1
    fi

    COUNTRY_OUTAGE_AGENT_URL_VALUE="$(
        read_country_outage_agent_config_value COUNTRY_OUTAGE_AGENT_URL
    )" || return 1
    COUNTRY_OUTAGE_AGENT_SHARED_TOKEN_VALUE="$(
        read_country_outage_agent_config_value COUNTRY_OUTAGE_AGENT_SHARED_TOKEN
    )" || return 1
    COUNTRY_OUTAGE_AGENT_IDENTITY_MODE_VALUE="$(
        read_country_outage_agent_config_value COUNTRY_OUTAGE_AGENT_IDENTITY_MODE
    )" || return 1
    COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID_VALUE="$(
        read_country_outage_agent_config_value COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID
    )" || return 1

    if [[ "${COUNTRY_OUTAGE_AGENT_URL_VALUE}" \
        != "${COUNTRY_OUTAGE_AGENT_EXPECTED_URL}" ]]; then
        error "国家中断 Agent URL 必须固定为 ${COUNTRY_OUTAGE_AGENT_EXPECTED_URL}"
        return 1
    fi
    if (( ${#COUNTRY_OUTAGE_AGENT_SHARED_TOKEN_VALUE} < 32 \
        || ${#COUNTRY_OUTAGE_AGENT_SHARED_TOKEN_VALUE} > 256 )) \
        || [[ ! "${COUNTRY_OUTAGE_AGENT_SHARED_TOKEN_VALUE}" \
            =~ ^[A-Za-z0-9._~-]+$ ]]; then
        error '国家中断 Agent 内部凭据必须是 32 至 256 位安全随机字符'
        return 1
    fi
    if [[ "${COUNTRY_OUTAGE_AGENT_IDENTITY_MODE_VALUE}" \
        != "${COUNTRY_OUTAGE_AGENT_EXPECTED_IDENTITY_MODE}" ]]; then
        error "固定历史观测环境只允许身份模式 ${COUNTRY_OUTAGE_AGENT_EXPECTED_IDENTITY_MODE}"
        return 1
    fi
    if [[ ! "${COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID_VALUE}" \
        =~ ^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$ ]]; then
        error '国家中断 Agent 内部用户标识不符合安全格式'
        return 1
    fi
    if [[ ! -x "${COUNTRY_OUTAGE_AGENT_NODE}" \
        || ! -f "${COUNTRY_OUTAGE_AGENT_PROBE_SCRIPT}" \
        || -L "${COUNTRY_OUTAGE_AGENT_PROBE_SCRIPT}" \
        || "$(readlink -f "${COUNTRY_OUTAGE_AGENT_PROBE_SCRIPT}")" \
            != "${COUNTRY_OUTAGE_AGENT_PROBE_SCRIPT}" ]]; then
        error '国家中断 Agent 固定 Node.js 或 readiness 探针无效'
        return 1
    fi
    COUNTRY_OUTAGE_AGENT_CONFIG_SHA256_VALUE="$(
        sha256sum "${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV}" | awk '{print $1}'
    )"
    COUNTRY_OUTAGE_AGENT_ENABLED=true
}

country_outage_agent_readiness_request() {
    if [[ "${COUNTRY_OUTAGE_AGENT_ENABLED}" != true ]]; then
        return 0
    fi
    env -i \
        HOME=/home/bgpdata \
        PATH="${RUNTIME_PATH}" \
        "${COUNTRY_OUTAGE_AGENT_NODE}" \
        "${COUNTRY_OUTAGE_AGENT_PROBE_SCRIPT}" \
        "${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV}" \
        >/dev/null
}

load_database_config() {
    local config_dir="${DATABASE_CONFIG%/*}"
    local dir_mode
    if [[ ! -d "${config_dir}" || -L "${config_dir}" \
        || "$(readlink -f "${config_dir}")" != "${config_dir}" \
        || "$(stat -c '%u' "${config_dir}")" != '0' ]]; then
        error "数据库配置目录必须是 root 拥有的实际目录：${config_dir}"
        return 1
    fi
    dir_mode="$(stat -c '%a' "${config_dir}")"
    if (( (8#${dir_mode} & 8#022) != 0 )); then
        error "数据库配置目录不得被组或其他用户写入：${config_dir}"
        return 1
    fi
    if [[ ! -f "${DATABASE_CONFIG}" || -L "${DATABASE_CONFIG}" \
        || "$(readlink -f "${DATABASE_CONFIG}")" != "${DATABASE_CONFIG}" \
        || "$(stat -c '%u' "${DATABASE_CONFIG}")" != '0' \
        || "$(stat -c '%a' "${DATABASE_CONFIG}")" != '600' ]]; then
        error "数据库配置必须是 root 拥有的 0600 实际普通文件：${DATABASE_CONFIG}"
        return 1
    fi
    DB_NAME="$(read_config_value DOMEYE_CORE_DB_NAME)" || return 1
    DB_READER_USER="$(read_config_value DOMEYE_CORE_DB_READER_USER)" || return 1
    DB_READER_PASSWORD="$(read_config_value DOMEYE_CORE_DB_READER_PASSWORD)" || return 1
    if [[ ! "${DB_NAME}" =~ ^[A-Za-z_][A-Za-z0-9_$-]*$ \
        || ! "${DB_READER_USER}" =~ ^[A-Za-z_][A-Za-z0-9_$-]*$ ]]; then
        error '开发数据库名称或只读角色名不符合安全格式'
        return 1
    fi
    if [[ "${DB_READER_PASSWORD}" == change-* ]]; then
        error '开发 API 拒绝使用示例数据库密码'
        return 1
    fi
}

validate_database_state() {
    if [[ ! -f "${DATABASE_STATE}" || -L "${DATABASE_STATE}" ]]; then
        error "开发数据库状态文件不存在：${DATABASE_STATE}"
        return 1
    fi
    local mode
    mode="$(stat -c '%a' "${DATABASE_STATE}")"
    if [[ "$(stat -c '%u' "${DATABASE_STATE}")" != '0' ]] \
        || (( (8#${mode} & 8#077) != 0 )); then
        error "开发数据库状态文件必须由 root 拥有且权限不宽于 0600：${DATABASE_STATE}"
        return 1
    fi
    if ! jq -e \
        --arg start "${DATA_START}" \
        --arg end_exclusive "${DATA_END_EXCLUSIVE}" \
        --arg release_id "${EXPECTED_DATABASE_RELEASE_ID}" \
        --arg release_dir "${EXPECTED_DATABASE_RELEASE_DIR}" \
        --arg lower_pgdata "${EXPECTED_LOWER_PGDATA}" \
        --argjson port 31627 \
        '.schema_version == 2
         and .phase == "verified"
         and .release_id == $release_id
         and .release_dir == $release_dir
         and .lower_pgdata == $lower_pgdata
         and .data_start == $start
         and .data_end_exclusive == $end_exclusive
         and (.image_id | test("^sha256:[0-9a-f]{64}$"))
         and .port == $port
         and (.system_identifier | test("^[0-9]+$"))
         and (.checkpoint_key | test("^[0-9a-f]{64}$"))
         and (.pruned_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))
         and (.verified_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))' \
        "${DATABASE_STATE}" >/dev/null; then
        error '开发数据库必须是固定 2–3 月窗口的 verified 状态'
        return 1
    fi
    DB_PORT="$(jq -er '.port' "${DATABASE_STATE}")"
    DB_IMAGE_ID="$(jq -er '.image_id' "${DATABASE_STATE}")"
    DB_RELEASE_ID="$(jq -er '.release_id' "${DATABASE_STATE}")"
    DB_SYSTEM_IDENTIFIER="$(jq -er '.system_identifier' "${DATABASE_STATE}")"
    DB_CHECKPOINT_KEY="$(jq -er '.checkpoint_key' "${DATABASE_STATE}")"
    if [[ "$(sha256sum "${DATABASE_VERIFY_SQL}" | awk '{print $1}')" \
        != "$(jq -er '.hashes.verify_sql' "${DATABASE_STATE}")" ]]; then
        error '当前验收 SQL 尚未对开发数据库成功执行；请先停止 API 并运行数据库 verify'
        return 1
    fi
    if [[ "${DB_PORT}" == "${API_PORT}" ]]; then
        error '开发数据库端口与开发 API 端口冲突'
        return 1
    fi
}

validate_database_container() {
    if [[ ! -d "${DATABASE_MERGED_DIR}" || -L "${DATABASE_MERGED_DIR}" ]] \
        || ! mountpoint -q "${DATABASE_MERGED_DIR}" \
        || [[ "$(findmnt -n -o TARGET --target "${DATABASE_MERGED_DIR}")" != "${DATABASE_MERGED_DIR}" \
            || "$(findmnt -n -o FSTYPE --target "${DATABASE_MERGED_DIR}")" != 'overlay' ]]; then
        error '开发数据库 merged 目录不是预期 OverlayFS 挂载'
        return 1
    fi
    local container_json
    if ! container_json="$(docker inspect --type container "${DATABASE_CONTAINER}" 2>/dev/null)"; then
        error "开发数据库容器不存在：${DATABASE_CONTAINER}"
        return 1
    fi
    if ! jq -e \
        --arg name "/${DATABASE_CONTAINER}" \
        --arg image "${DB_IMAGE_ID}" \
        --arg port "${DB_PORT}" \
        --arg source "${DATABASE_MERGED_DIR}" \
        --arg role_label "${DATABASE_ROLE_LABEL}" \
        --arg instance_label "${DATABASE_INSTANCE_LABEL}" \
        --arg state_label "${DATABASE_STATE_LABEL}" \
        --arg release_id "${DB_RELEASE_ID}" \
        --arg system_identifier "${DB_SYSTEM_IDENTIFIER}" \
        --arg checkpoint_key "${DB_CHECKPOINT_KEY}" \
        'length == 1
         and .[0].Name == $name
         and .[0].Image == $image
         and .[0].Config.Labels["io.domeye.core.role"] == $role_label
         and .[0].Config.Labels["io.domeye.core.instance"] == $instance_label
         and .[0].Config.Labels["io.domeye.core.state"] == $state_label
         and .[0].Config.Labels["io.domeye.core.release-id"] == $release_id
         and .[0].Config.Labels["io.domeye.core.system-identifier"] == $system_identifier
         and .[0].Config.Labels["io.domeye.core.checkpoint-key"] == $checkpoint_key
         and .[0].State.Running == true
         and .[0].State.Status == "running"
         and ((.[0].NetworkSettings.Ports["5432/tcp"] // []) | length) == 1
         and .[0].NetworkSettings.Ports["5432/tcp"][0].HostIp == "127.0.0.1"
         and .[0].NetworkSettings.Ports["5432/tcp"][0].HostPort == $port
         and ([.[0].Mounts[]
             | select(.Type == "bind"
                 and .Source == $source
                 and .Destination == "/var/lib/postgresql/data"
                 and .RW == true)] | length) == 1' \
        <<<"${container_json}" >/dev/null; then
        error '开发数据库容器的名称、镜像、端口、挂载或运行状态不匹配'
        return 1
    fi
}

validate_reader_role() {
    local result
    local query
    query="
            SELECT CASE WHEN
                current_setting('default_transaction_read_only') = 'on'
                AND NOT r.rolsuper
                AND NOT r.rolcreaterole
                AND NOT r.rolcreatedb
                AND NOT r.rolreplication
                AND NOT r.rolbypassrls
                AND NOT has_schema_privilege(current_user, 'public', 'CREATE')
                AND NOT EXISTS (
                    SELECT 1
                    FROM pg_class AS c
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind IN ('r', 'p')
                      AND (
                          has_table_privilege(current_user, c.oid, 'INSERT')
                          OR has_table_privilege(current_user, c.oid, 'UPDATE')
                          OR has_table_privilege(current_user, c.oid, 'DELETE')
                          OR has_table_privilege(current_user, c.oid, 'TRUNCATE')
                      )
                )
                THEN 'ok' ELSE 'unsafe' END
            FROM pg_roles AS r
            WHERE r.rolname = current_user;
        "
    if ! result="$(
        printf '%s\n' "${DB_READER_PASSWORD}" \
            | docker exec --interactive "${DATABASE_CONTAINER}" \
                /bin/sh -c '
                    IFS= read -r PGPASSWORD || exit 1
                    export PGPASSWORD
                    exec psql -X --set ON_ERROR_STOP=1 --quiet --tuples-only --no-align \
                        --host 127.0.0.1 \
                        --port 5432 \
                        --username "$1" \
                        --dbname "$2" \
                        --command "$3"
                ' domeye-dev-reader-check "${DB_READER_USER}" "${DB_NAME}" "${query}" \
                2>/dev/null
    )"; then
        error '开发数据库只读角色连接验证失败'
        return 1
    fi
    if [[ "$(tr -d '[:space:]' <<<"${result}")" != 'ok' ]]; then
        error '开发 API 数据库角色不满足默认只读和无写权限要求'
        return 1
    fi
}

validate_database() {
    validate_database_state || return 1
    validate_database_container || return 1
    validate_reader_role || return 1
}

validate_info() {
    "${INFO_STAGE_SCRIPT}" --verify-installed >/dev/null || return 1
    local info_release
    info_release="$(jq -er '.release_id' "${API_ROOT}/info-manifest.json")" || return 1
    if [[ -n "${DB_RELEASE_ID}" && "${info_release}" != "${DB_RELEASE_ID}" ]]; then
        error "开发信息制品与数据库 release-id 不一致：${info_release} != ${DB_RELEASE_ID}"
        return 1
    fi
}

list_sessions() {
    screen -ls 2>/dev/null | awk -v suffix=".${SCREEN_NAME}" '
        $1 ~ /^[0-9]+\./ && substr($1, length($1) - length(suffix) + 1) == suffix {
            print $1
        }
    '
}

session_has_marker() {
    local session="$1"
    [[ "${session}" =~ ^[0-9]+\.${SCREEN_NAME}$ ]] || return 1
    local -a queue children
    queue=("${session%%.*}")
    local pid child
    while (( ${#queue[@]} > 0 )); do
        pid="${queue[0]}"
        queue=("${queue[@]:1}")
        if [[ -r "/proc/${pid}/environ" ]] \
            && tr '\0' '\n' < "/proc/${pid}/environ" \
                | awk -F= -v expected="${API_INSTANCE}" '
                    $1 == "DOMEYE_DEV_API_INSTANCE" {
                        sub(/^[^=]*=/, "")
                        if ($0 == expected) found = 1
                    }
                    END { exit(found ? 0 : 1) }
                '; then
            return 0
        fi
        mapfile -t children < <(
            ps -o pid= --ppid "${pid}" 2>/dev/null \
                | awk '{$1=$1; if ($1 ~ /^[0-9]+$/) print $1}'
        )
        for child in "${children[@]}"; do
            queue+=("${child}")
        done
    done
    return 1
}

api_process_matches() {
    local session="$1"
    [[ "${session}" =~ ^[0-9]+\.${SCREEN_NAME}$ ]] || return 1
    local -a queue children
    queue=("${session%%.*}")
    local pid child
    while (( ${#queue[@]} > 0 )); do
        pid="${queue[0]}"
        queue=("${queue[@]:1}")
        if [[ -r "/proc/${pid}/environ" ]] \
            && tr '\0' '\n' < "/proc/${pid}/environ" \
                | awk -F= \
                    -v marker="${API_INSTANCE}" \
                    -v host="${API_HOST}" \
                    -v port="${API_PORT}" \
                    -v info_dir="${INFO_DIR}" \
                    -v log_dir="${APP_LOG_DIR}" \
                    -v db_port="${DB_PORT}" \
                    -v db_user="${DB_READER_USER}" \
                    -v data_start="${DATA_START}" \
                    -v data_end="${DATA_END_EXCLUSIVE}" \
                    -v snapshot="${SNAPSHOT_TIME}" \
                    -v no_bytecode="1" \
                    -v agent_required="${COUNTRY_OUTAGE_AGENT_ENABLED}" \
                    -v agent_url="${COUNTRY_OUTAGE_AGENT_URL_VALUE}" \
                    -v agent_identity_mode="${COUNTRY_OUTAGE_AGENT_IDENTITY_MODE_VALUE}" \
                    -v agent_user="${COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID_VALUE}" \
                    -v agent_config_sha="${COUNTRY_OUTAGE_AGENT_CONFIG_SHA256_VALUE}" '
                    function value() { sub(/^[^=]*=/, ""); return $0 }
                    $1 == "DOMEYE_DEV_API_INSTANCE" && value() == marker { a=1 }
                    $1 == "HOST" && value() == host { b=1 }
                    $1 == "PORT" && value() == port { c=1 }
                    $1 == "INFO_DIR" && value() == info_dir { d=1 }
                    $1 == "DOMEYE_LOG_DIR" && value() == log_dir { o=1 }
                    $1 == "DB_HOST" && value() == "127.0.0.1" { e=1 }
                    $1 == "DB_PORT" && value() == db_port { f=1 }
                    $1 == "DB_USER" && value() == db_user { g=1 }
                    $1 == "DOMEYE_DATA_WINDOW_START" && value() == data_start { h=1 }
                    $1 == "DOMEYE_DATA_WINDOW_END_EXCLUSIVE" && value() == data_end { i=1 }
                    $1 == "DOMEYE_DATA_SNAPSHOT_TIME" && value() == snapshot { j=1 }
                    $1 == "DOMEYE_ENFORCE_DATA_WINDOW" && value() == "true" { k=1 }
                    $1 == "AUTO_INIT_DB" && value() == "false" { l=1 }
                    $1 == "LOAD_CORE_DATA_ON_STARTUP" && value() == "false" { m=1 }
                    $1 == "DOMEYE_CORE_SKIP_LOCAL_ENV" && value() == "true" { n=1 }
                    $1 == "PYTHONDONTWRITEBYTECODE" && value() == no_bytecode { p=1 }
                    $1 == "COUNTRY_OUTAGE_AGENT_URL" && value() == agent_url { q=1 }
                    $1 == "COUNTRY_OUTAGE_AGENT_SHARED_TOKEN" && length(value()) >= 32 { r=1 }
                    $1 == "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE" && value() == agent_identity_mode { s=1 }
                    $1 == "COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID" && value() == agent_user { t=1 }
                    $1 == "COUNTRY_OUTAGE_AGENT_CONFIG_SHA256" && value() == agent_config_sha { u=1 }
                    END {
                        agent_ok = agent_required != "true" || (q&&r&&s&&t&&u)
                        exit(a&&b&&c&&d&&e&&f&&g&&h&&i&&j&&k&&l&&m&&n&&o&&p&&agent_ok ? 0 : 1)
                    }
                '; then
            return 0
        fi
        mapfile -t children < <(
            ps -o pid= --ppid "${pid}" 2>/dev/null \
                | awk '{$1=$1; if ($1 ~ /^[0-9]+$/) print $1}'
        )
        for child in "${children[@]}"; do
            queue+=("${child}")
        done
    done
    return 1
}

api_health_request() {
    local payload
    if ! payload="$(curl --fail --silent --show-error --max-time 3 "${HEALTH_URL}" 2>/dev/null)"; then
        return 1
    fi
    jq -e '.status == "ok" and .service == "domeye-core"' <<<"${payload}" >/dev/null
}

api_database_smoke_request() {
    local payload
    if ! payload="$(curl --fail --silent --show-error --max-time 90 \
        "${DATABASE_SMOKE_URL}" 2>/dev/null)"; then
        return 1
    fi
    jq -e '
        type == "object"
        and (.data | type) == "array"
        and (.data | length) > 0
        and has("total_page")
        and has("record_count")
        and ((.record_count | tonumber?) // 0) > 0
    ' <<<"${payload}" >/dev/null
}

api_static_as_warmup_request() {
    if [[ "${API_PROFILE}" != 'remote' ]]; then
        return 0
    fi

    local http_code
    if ! http_code="$(curl --silent --show-error --max-time 45 \
        --output /dev/null --write-out '%{http_code}' \
        "${STATIC_AS_WARMUP_URL}" 2>/dev/null)"; then
        return 1
    fi
    [[ "${http_code}" == '200' ]]
}

tail_log() {
    if [[ -f "${LOG_FILE}" && ! -L "${LOG_FILE}" ]]; then
        printf '\n最近的开发 API 日志：\n' >&2
        tail -n 30 "${LOG_FILE}" >&2 || true
    fi
}

port_is_busy() {
    ss -H -ltn "sport = :${API_PORT}" | grep -q .
}

sync_environment() {
    if [[ ! -x "${UV}" ]]; then
        error "uv 不存在或不可执行：${UV}"
        return 1
    fi
    install -d -m 0750 "${API_ROOT}"
    (
        cd -- "${BACKEND_DIR}"
        env -i \
            HOME=/home/bgpdata \
            USER=root \
            LOGNAME=root \
            LANG=C.UTF-8 \
            PATH="${RUNTIME_PATH}" \
            UV_PROJECT_ENVIRONMENT="${VENV_DIR}" \
            PYTHONDONTWRITEBYTECODE=1 \
            "${UV}" sync --frozen
    )
    if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
        error "uv sync --frozen 未生成独立开发环境：${VENV_DIR}"
        return 1
    fi
}

stop_exact_session() {
    local session="$1"
    [[ "${session}" =~ ^[0-9]+\.${SCREEN_NAME}$ ]] || {
        error "拒绝停止非精确开发 API Screen：${session}"
        return 1
    }
    screen -S "${session}" -X quit
    local attempt
    for (( attempt = 1; attempt <= 30; attempt++ )); do
        if ! list_sessions | grep -Fxq "${session}"; then
            return 0
        fi
        sleep 0.2
    done
    error "开发 API Screen 未在 6 秒内停止：${session}"
    return 1
}

serve_internal() {
    require_root
    for command_name in awk docker findmnt install jq mountpoint readlink stat; do
        require_command "${command_name}"
    done
    validate_project_boundary
    ensure_api_root
    load_development_secret
    load_database_config
    validate_database
    validate_info
    load_country_outage_agent_runtime_config

    export HOME=/home/bgpdata
    export USER=root
    export LOGNAME=root
    export LANG=C.UTF-8
    export PATH="${RUNTIME_PATH}"
    export DOMEYE_DEV_API_INSTANCE="${API_INSTANCE}"
    export DOMEYE_CORE_SKIP_LOCAL_ENV=true
    export FLASK_CONFIG=production
    export HOST="${API_HOST}"
    export PORT="${API_PORT}"
    export DEBUG=false
    export AUTO_INIT_DB=false
    export LOAD_CORE_DATA_ON_STARTUP=false
    export SOURCE=r
    export INFO_DIR
    export DOMEYE_LOG_DIR="${APP_LOG_DIR}"
    export DB_HOST=127.0.0.1
    export DB_PORT="${DB_PORT}"
    export DB_NAME="${DB_NAME}"
    export DB_USER="${DB_READER_USER}"
    export DB_PASSWORD="${DB_READER_PASSWORD}"
    export SECRET_KEY="${SECRET_KEY_VALUE}"
    export MAIL_ENABLED=false
    export FEATURE_COUNTRY_TABLE=feature_country
    export FEATURE_OTHER_TABLE=feature_other
    export FEATURE_ASN_MONTHLY_ENABLED=true
    export FEATURE_ASN_OLD_SUFFIX=_old
    export DOMEYE_ENFORCE_DATA_WINDOW=true
    export DOMEYE_DATA_WINDOW_START="${DATA_START}"
    export DOMEYE_DATA_WINDOW_END_EXCLUSIVE="${DATA_END_EXCLUSIVE}"
    export DOMEYE_DATA_SNAPSHOT_TIME="${SNAPSHOT_TIME}"
    export PYTHONUNBUFFERED=1
    export PYTHONDONTWRITEBYTECODE=1
    export UV_PROJECT_ENVIRONMENT="${VENV_DIR}"
    if [[ "${COUNTRY_OUTAGE_AGENT_ENABLED}" == true ]]; then
        export COUNTRY_OUTAGE_AGENT_URL="${COUNTRY_OUTAGE_AGENT_URL_VALUE}"
        export COUNTRY_OUTAGE_AGENT_SHARED_TOKEN="${COUNTRY_OUTAGE_AGENT_SHARED_TOKEN_VALUE}"
        export COUNTRY_OUTAGE_AGENT_IDENTITY_MODE="${COUNTRY_OUTAGE_AGENT_IDENTITY_MODE_VALUE}"
        export COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID="${COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID_VALUE}"
        export COUNTRY_OUTAGE_AGENT_CONFIG_SHA256="${COUNTRY_OUTAGE_AGENT_CONFIG_SHA256_VALUE}"
    fi
    DB_READER_PASSWORD=''
    SECRET_KEY_VALUE=''
    COUNTRY_OUTAGE_AGENT_SHARED_TOKEN_VALUE=''

    exec "${UV}" run --directory "${BACKEND_DIR}" --frozen python run.py
}

start_action() {
    ensure_api_root
    install -d -o 0 -g 0 -m 0750 "${LOG_DIR}"
    install -d -o 0 -g 0 -m 0750 "${APP_LOG_DIR}"
    ensure_development_secret
    load_database_config
    validate_database
    validate_info
    load_country_outage_agent_runtime_config
    if [[ "${COUNTRY_OUTAGE_AGENT_ENABLED}" == true ]] \
        && ! country_outage_agent_readiness_request; then
        error '国家中断 Agent 已配置，但 Sidecar readiness 未通过；拒绝启动固定后端'
        return 1
    fi

    local -a sessions
    mapfile -t sessions < <(list_sessions)
    if (( ${#sessions[@]} > 1 )); then
        error "发现多个同名开发 API Screen，拒绝自动操作：${sessions[*]}"
        return 1
    fi
    if (( ${#sessions[@]} == 1 )); then
        if ! api_process_matches "${sessions[0]}" \
            || ! api_health_request \
            || ! api_database_smoke_request; then
            error "同名 Screen 存在，但身份或健康状态不符合开发 API 约定"
            return 1
        fi
        printf '开发 API 已在运行：%s\n' "${sessions[0]}"
        printf '健康检查：%s\n' "${HEALTH_URL}"
        return 0
    fi
    if port_is_busy; then
        error "开发 API 端口已被其他进程占用：${API_HOST}:${API_PORT}"
        return 1
    fi

    sync_environment
    START_CREATED=true
    START_COMPLETE=false
    cleanup_started_api() {
        local exit_code=$?
        if [[ "${START_CREATED}" == true && "${START_COMPLETE}" != true ]]; then
            local -a cleanup_sessions
            mapfile -t cleanup_sessions < <(list_sessions)
            local cleanup_session
            for cleanup_session in "${cleanup_sessions[@]}"; do
                if session_has_marker "${cleanup_session}"; then
                    stop_exact_session "${cleanup_session}" || true
                fi
            done
        fi
        return "${exit_code}"
    }
    trap cleanup_started_api EXIT
    screen \
        -L \
        -Logfile "${LOG_FILE}" \
        -dmS "${SCREEN_NAME}" \
        env -i \
            HOME=/home/bgpdata \
            USER=root \
            LOGNAME=root \
            LANG=C.UTF-8 \
            PATH="${RUNTIME_PATH}" \
            DOMEYE_CORE_API_PROFILE="${API_PROFILE}" \
            DOMEYE_DEV_API_INSTANCE="${API_INSTANCE}" \
            /bin/bash "${SCRIPT_PATH}" _serve

    local started_session=''
    local attempt
    for (( attempt = 1; attempt <= 60; attempt++ )); do
        mapfile -t sessions < <(list_sessions)
        if (( ${#sessions[@]} == 1 )); then
            started_session="${sessions[0]}"
            if api_process_matches "${started_session}" && api_health_request; then
                if ! validate_database; then
                    error '开发 API 启动后的数据库身份或只读复验失败'
                    tail_log
                    return 1
                fi
                if ! api_database_smoke_request; then
                    error '开发 API 健康探针已通过，但固定 3 月 31 日事件查询未能通过只读数据库冒烟'
                    tail_log
                    return 1
                fi
                if ! api_static_as_warmup_request; then
                    error '开发 API 静态 ASN 数据预热失败'
                    tail_log
                    return 1
                fi
                if [[ "${COUNTRY_OUTAGE_AGENT_ENABLED}" == true ]] \
                    && ! country_outage_agent_readiness_request; then
                    error '固定后端已启动，但国家中断 Agent readiness 未通过'
                    tail_log
                    return 1
                fi
                START_COMPLETE=true
                trap - EXIT
                printf '开发 API 启动成功：%s\n' "${started_session}"
                printf '健康检查：%s\n' "${HEALTH_URL}"
                printf '数据库冒烟：%s\n' "${DATABASE_SMOKE_URL}"
                if [[ "${API_PROFILE}" == 'remote' ]]; then
                    printf '静态 ASN 预热：%s\n' "${STATIC_AS_WARMUP_URL}"
                fi
                return 0
            fi
        elif (( ${#sessions[@]} > 1 )); then
            error '开发 API 启动期间出现多个同名 Screen，拒绝继续'
            tail_log
            return 1
        fi
        sleep 1
    done

    if [[ -n "${started_session}" ]] && session_has_marker "${started_session}"; then
        stop_exact_session "${started_session}" || true
    fi
    error '开发 API 未在 60 秒内通过身份与健康检查'
    tail_log
    return 1
}

stop_action() {
    local -a sessions
    mapfile -t sessions < <(list_sessions)
    if (( ${#sessions[@]} == 0 )); then
        printf '开发 API 未运行，无需停止。\n'
        return 0
    fi
    if (( ${#sessions[@]} != 1 )); then
        error "发现多个同名开发 API Screen，拒绝停止：${sessions[*]}"
        return 1
    fi
    if ! session_has_marker "${sessions[0]}"; then
        error '同名 Screen 没有开发 API 身份标记，拒绝停止'
        return 1
    fi
    stop_exact_session "${sessions[0]}"
    printf '二三月固定数据 API 已停止：%s；未操作数据库或 .env。\n' "${sessions[0]}"
}

health_action() {
    load_database_config
    validate_database
    validate_info
    load_country_outage_agent_runtime_config
    local -a sessions
    mapfile -t sessions < <(list_sessions)
    if (( ${#sessions[@]} != 1 )) || ! api_process_matches "${sessions[0]}"; then
        error '开发 API Screen 未唯一运行或运行环境不匹配'
        return 1
    fi
    if ! api_health_request; then
        error "开发 API 健康检查失败：${HEALTH_URL}"
        return 1
    fi
    if ! api_database_smoke_request; then
        error '开发 API 健康探针通过，但固定窗口事件查询失败'
        return 1
    fi
    if [[ "${COUNTRY_OUTAGE_AGENT_ENABLED}" == true ]] \
        && ! country_outage_agent_readiness_request; then
        error '固定后端健康，但国家中断 Agent readiness 失败'
        return 1
    fi
    printf '开发 API 健康：%s（Screen %s，数据库只读）\n' "${HEALTH_URL}" "${sessions[0]}"
    printf '数据库冒烟：%s\n' "${DATABASE_SMOKE_URL}"
    if [[ "${COUNTRY_OUTAGE_AGENT_ENABLED}" == true ]]; then
        printf '国家中断 Agent：ready（external evidence not_configured / disabled）\n'
    fi
}

status_action() {
    local failed=false
    if ! load_country_outage_agent_runtime_config; then
        printf '国家中断 Agent 配置：无效\n'
        failed=true
    fi
    if load_database_config && validate_database; then
        printf '开发数据库：verified / running / readonly（127.0.0.1:%s）\n' "${DB_PORT}"
    else
        printf '开发数据库：不可用或身份不匹配\n'
        failed=true
    fi
    if validate_info; then
        printf '开发信息制品：四文件哈希通过（%s）\n' "${INFO_DIR}"
    else
        printf '开发信息制品：不可用或哈希不匹配\n'
        failed=true
    fi

    local -a sessions
    mapfile -t sessions < <(list_sessions)
    if (( ${#sessions[@]} == 0 )); then
        printf '开发 API Screen：stopped\n'
        return 1
    fi
    if (( ${#sessions[@]} != 1 )) || ! api_process_matches "${sessions[0]}"; then
        printf '开发 API Screen：数量或身份异常\n'
        return 1
    fi
    printf '开发 API Screen：%s\n' "${sessions[0]}"
    if api_health_request; then
        printf '开发 API 健康：ok（%s）\n' "${HEALTH_URL}"
    else
        printf '开发 API 健康：failed（%s）\n' "${HEALTH_URL}"
        failed=true
    fi
    if [[ "${COUNTRY_OUTAGE_AGENT_ENABLED}" == true ]]; then
        if country_outage_agent_readiness_request; then
            printf '国家中断 Agent：ready（external evidence not_configured / disabled）\n'
        else
            printf '国家中断 Agent：failed\n'
            failed=true
        fi
    else
        printf '国家中断 Agent：未配置（核心观测 API 仍可用）\n'
    fi
    [[ "${failed}" == false ]]
}

main() {
    if (( $# != 1 )); then
        error '用法：manage-dev-api.sh <start|stop|status|health>'
        return 2
    fi
    if [[ "$1" == '_serve' ]]; then
        serve_internal
        return
    fi

    require_root
    for command_name in awk env flock grep install ps readlink screen sha256sum sleep stat tr; do
        require_command "${command_name}"
    done
    validate_project_boundary
    validate_dev_data_root
    case "$1" in
        start)
            for command_name in chmod chown curl docker find findmnt jq mountpoint mv openssl rm ss stat tail; do
                require_command "${command_name}"
            done
            ensure_api_root
            exec 9>"${LOCK_FILE}"
            if ! flock -n 9; then
                error '另一个开发 API 启停操作正在运行'
                return 1
            fi
            start_action
            ;;
        stop)
            exec 9>"${LOCK_FILE}"
            if ! flock -n 9; then
                error '另一个开发 API 启停操作正在运行'
                return 1
            fi
            stop_action
            ;;
        status)
            for command_name in curl docker find findmnt jq mountpoint stat; do
                require_command "${command_name}"
            done
            status_action
            ;;
        health)
            for command_name in curl docker find findmnt jq mountpoint stat; do
                require_command "${command_name}"
            done
            health_action
            ;;
        *)
            error "未知操作：$1"
            return 2
            ;;
    esac
}

main "$@"
