#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly DEFAULT_RUNTIME_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
readonly RUNTIME_ROOT="${DOMEYE_COUNTRY_OUTAGE_RUNTIME_ROOT_OVERRIDE:-${DEFAULT_RUNTIME_ROOT}}"
readonly MODE="${DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE:-production}"
readonly DATABASE_CONFIG='/home/bgpdata/Domeye-Core-data/config/database.env'
readonly AGENT_CONFIG='/home/bgpdata/Domeye-Core-runtime/config/country-outage-agent.env'
readonly INFO_DIR='/home/bgpdata/Domeye-Core-dev-data/api/info'
readonly P0_DATA_DIR='/home/bgpdata/Domeye-Core-artifacts/releases/20260720T160000Z-p0-legacy/data-quality/api-candidate'
readonly RUNTIME_PATH='/home/bgpdata/.local/node-v22.23.1-linux-x64/bin:/home/bgpdata/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'

error() {
    printf '国家中断通用观测运行时错误：%s\n' "$*" >&2
}

sha256_file() {
    sha256sum "$1" | awk '{print $1}'
}

read_config_value() {
    local file="$1"
    local key="$2"
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
        ' "${file}"
    )
    if (( ${#values[@]} != 1 )); then
        error "配置键必须恰好出现一次：${key}"
        return 1
    fi
    local value="${values[0]}"
    if [[ "${value}" == \"*\" && "${value}" == *\" ]]; then
        value="${value:1:${#value}-2}"
    elif [[ "${value}" == \'*\' && "${value}" == *\' ]]; then
        value="${value:1:${#value}-2}"
    fi
    if [[ -z "${value}" || "${value}" == *$'\n'* || "${value}" == *$'\r'* ]]; then
        error "配置键为空或含换行：${key}"
        return 1
    fi
    printf '%s\n' "${value}"
}

require_secure_config() {
    local file="$1"
    [[ -f "${file}" && ! -L "${file}" ]] || {
        error "安全配置不是普通文件：${file}"
        return 1
    }
    [[ "$(stat -c '%u:%g:%a' "${file}")" == '0:0:600' ]] || {
        error "安全配置必须为 root:root 0600：${file}"
        return 1
    }
}

validate_runtime_root() {
    [[ "${RUNTIME_ROOT}" == /home/bgpdata/Domeye-Core-runtime/releases/*-backend ]] || {
        error "运行时目录不在受控 release 根：${RUNTIME_ROOT}"
        return 1
    }
    [[ -d "${RUNTIME_ROOT}" && ! -L "${RUNTIME_ROOT}" ]] || {
        error "运行时目录不存在或是符号链接：${RUNTIME_ROOT}"
        return 1
    }
    [[ "$(readlink -f -- "${RUNTIME_ROOT}")" == "${RUNTIME_ROOT}" ]] || {
        error "运行时目录规范路径冲突：${RUNTIME_ROOT}"
        return 1
    }
}

validate_runtime() {
    validate_runtime_root
    for command_name in awk curl jq pgrep readlink screen sha256sum ss stat tr; do
        command -v "${command_name}" >/dev/null 2>&1 || {
            error "缺少命令：${command_name}"
            return 1
        }
    done
    for file in \
        "${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json" \
        "${RUNTIME_ROOT}/backend/core.sha256" \
        "${RUNTIME_ROOT}/backend/run.py" \
        "${RUNTIME_ROOT}/data-layer/PRODUCTION-SELECTION.json" \
        "${RUNTIME_ROOT}/country-outage-registry.json"; do
        [[ -f "${file}" && ! -L "${file}" ]] || {
            error "运行时缺少普通文件：${file}"
            return 1
        }
    done
    [[ -x "${RUNTIME_ROOT}/venv/bin/python" ]] || {
        error '运行时 Python 不可执行'
        return 1
    }
    jq -e --arg runtime_root "${RUNTIME_ROOT}" \
        '(.release_id | type == "string" and endswith("-backend"))
         and (.source_commit | test("^[0-9a-f]{40}$"))
         and (.source_tag | type == "string" and length > 0)
         and .boundaries.collector == "rrc25"
         and .boundaries.database_changed == false
         and (
           (.schema_version == "domeye_country_outage_general_backend_binding_v1"
            and .runtime_root == $runtime_root
            and .boundaries.window_start_utc == "2026-02-24T00:00:00Z"
            and .boundaries.window_end_exclusive_utc == "2026-03-11T00:00:00Z")
           or
           (.schema_version == "domeye_backend_source_binding_v2"
            and .data_layer.window_start_utc == "2026-02-24T00:00:00Z"
            and .data_layer.window_end_exclusive_utc == "2026-03-11T00:00:00Z")
         )' \
        "${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json" >/dev/null || {
        error 'Backend 来源绑定无效'
        return 1
    }
    (
        cd -- "${RUNTIME_ROOT}/backend"
        sha256sum -c core.sha256 >/dev/null
    ) || {
        error '冻结 Core 摘要不一致'
        return 1
    }
    if [[ -e "${RUNTIME_ROOT}/general-read-model" || -L "${RUNTIME_ROOT}/general-read-model" ]]; then
        [[ -d "${RUNTIME_ROOT}/general-read-model" \
            && ! -L "${RUNTIME_ROOT}/general-read-model" \
            && -f "${RUNTIME_ROOT}/general-read-model/manifest.json" \
            && -f "${RUNTIME_ROOT}/general-read-model/COMPLETE.json" ]] || {
            error '通用读模型目录不完整'
            return 1
        }
        cmp -s \
            "${RUNTIME_ROOT}/general-read-model/manifest.json" \
            "${RUNTIME_ROOT}/general-read-model/COMPLETE.json" || {
            error '通用读模型 manifest 与 COMPLETE 不一致'
            return 1
        }
    fi
    require_secure_config "${DATABASE_CONFIG}"
    require_secure_config "${AGENT_CONFIG}"
}

case "${MODE}" in
    production)
        readonly SCREEN_NAME='domeye_core_app'
        readonly API_PORT='28473'
        readonly RUNTIME_MODE='production'
        ;;
    canary)
        readonly SCREEN_NAME='domeye_country_outage_general_canary'
        readonly API_PORT='38672'
        readonly RUNTIME_MODE='canary'
        ;;
    *)
        error "运行模式只能为 production 或 canary：${MODE}"
        exit 2
        ;;
esac

list_sessions() {
    screen -ls 2>/dev/null | awk -v suffix=".${SCREEN_NAME}" '
        $1 ~ /^[0-9]+\./ && substr($1, length($1) - length(suffix) + 1) == suffix {
            print $1
        }
    '
}

release_id() {
    jq -er '.release_id' "${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json"
}

session_process() {
    local session="$1"
    local expected_release
    expected_release="$(release_id)"
    local root_pid="${session%%.*}"
    local pid
    while IFS= read -r pid; do
        [[ -n "${pid}" && -r "/proc/${pid}/environ" ]] || continue
        if [[ "$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)" \
            == "${RUNTIME_ROOT}/backend" ]] \
            && tr '\0' '\n' < "/proc/${pid}/environ" | awk -F= \
                -v release="${expected_release}" \
                -v mode="${RUNTIME_MODE}" \
                -v port="${API_PORT}" '
                    $1 == "DOMEYE_P0_PRODUCTION_RELEASE_ID" && $2 == release { a=1 }
                    $1 == "DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE" && $2 == mode { b=1 }
                    $1 == "PORT" && $2 == port { c=1 }
                    END { exit(a && b && c ? 0 : 1) }
                '; then
            printf '%s\n' "${pid}"
            return 0
        fi
    done < <(pgrep -P "${root_pid}" -f 'python.*run.py' 2>/dev/null || true)
    return 1
}

start_runtime() {
    validate_runtime
    mapfile -t sessions < <(list_sessions)
    if (( ${#sessions[@]} > 1 )); then
        error "发现多个同名会话：${sessions[*]}"
        return 1
    fi
    if (( ${#sessions[@]} == 1 )); then
        session_process "${sessions[0]}" >/dev/null || {
            error "既有会话身份不匹配：${sessions[0]}"
            return 1
        }
        printf '运行时已启动：%s\n' "${sessions[0]}"
        return 0
    fi

    local db_name db_port db_user db_password secret_key
    local agent_url agent_token agent_identity agent_user agent_config_sha
    db_name="$(read_config_value "${DATABASE_CONFIG}" DOMEYE_CORE_DB_NAME)"
    db_port="$(read_config_value "${DATABASE_CONFIG}" DOMEYE_CORE_DB_PORT)"
    db_user="$(read_config_value "${DATABASE_CONFIG}" DOMEYE_CORE_DB_READER_USER)"
    db_password="$(read_config_value "${DATABASE_CONFIG}" DOMEYE_CORE_DB_READER_PASSWORD)"
    secret_key="$(read_config_value "${DATABASE_CONFIG}" DOMEYE_CORE_SECRET_KEY)"
    agent_url="$(read_config_value "${AGENT_CONFIG}" COUNTRY_OUTAGE_AGENT_URL)"
    agent_token="$(read_config_value "${AGENT_CONFIG}" COUNTRY_OUTAGE_AGENT_SHARED_TOKEN)"
    agent_identity="$(read_config_value "${AGENT_CONFIG}" COUNTRY_OUTAGE_AGENT_IDENTITY_MODE)"
    agent_user="$(read_config_value "${AGENT_CONFIG}" COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID)"
    agent_config_sha="$(sha256_file "${AGENT_CONFIG}")"
    local selected_release log_root general_read_model
    selected_release="$(release_id)"
    log_root="/home/bgpdata/Domeye-Core-runtime/log/${selected_release}/${RUNTIME_MODE}"
    install -d -o 0 -g 0 -m 0750 "${log_root}" "${log_root}/app"
    general_read_model=''
    if [[ -d "${RUNTIME_ROOT}/general-read-model" ]]; then
        general_read_model="${RUNTIME_ROOT}/general-read-model"
    fi

    screen -L -Logfile "${log_root}/screen.log" -dmS "${SCREEN_NAME}" \
        env -i \
            HOME=/home/bgpdata \
            USER=root \
            LOGNAME=root \
            LANG=C.UTF-8 \
            LC_ALL=C.UTF-8 \
            PATH="${RUNTIME_PATH}" \
            FLASK_CONFIG=production \
            HOST=127.0.0.1 \
            PORT="${API_PORT}" \
            DEBUG=false \
            AUTO_INIT_DB=false \
            LOAD_CORE_DATA_ON_STARTUP=false \
            SOURCE=r \
            INFO_DIR="${INFO_DIR}" \
            DOMEYE_LOG_DIR="${log_root}/app" \
            DB_HOST=127.0.0.1 \
            DB_PORT="${db_port}" \
            DB_NAME="${db_name}" \
            DB_USER="${db_user}" \
            DB_PASSWORD="${db_password}" \
            SECRET_KEY="${secret_key}" \
            MAIL_ENABLED=false \
            FEATURE_COUNTRY_TABLE=feature_country \
            FEATURE_OTHER_TABLE=feature_other \
            FEATURE_ASN_MONTHLY_ENABLED=true \
            FEATURE_ASN_OLD_SUFFIX=_old \
            DOMEYE_ENFORCE_DATA_WINDOW=true \
            DOMEYE_DATA_WINDOW_START='2026-02-01 00:00:00' \
            DOMEYE_DATA_WINDOW_END_EXCLUSIVE='2026-04-01 00:00:00' \
            DOMEYE_DATA_SNAPSHOT_TIME='2026-03-31 23:59:59' \
            DOMEYE_CORE_SKIP_LOCAL_ENV=true \
            DOMEYE_DEV_API_INSTANCE="domeye-country-outage-general-${RUNTIME_MODE}-${selected_release}" \
            DOMEYE_P0_RELEASE_ID=20260806T054822Z-country-outage-224-310-scope-revert-prod20-backend \
            DOMEYE_P0_PRODUCTION_RELEASE_ID="${selected_release}" \
            DOMEYE_P0_RUNTIME_MODE="${RUNTIME_MODE}" \
            P0_DATA_RELEASE_DIR="${P0_DATA_DIR}" \
            P0_DATA_PRODUCTION_ACTIVE=true \
            DOMEYE_DATA_LAYER_224_310_SELECTION="${RUNTIME_ROOT}/data-layer/PRODUCTION-SELECTION.json" \
            DOMEYE_COUNTRY_OUTAGE_REGISTRY="${RUNTIME_ROOT}/country-outage-registry.json" \
            DOMEYE_COUNTRY_OUTAGE_GENERAL_READ_MODEL="${general_read_model}" \
            COUNTRY_OUTAGE_AGENT_URL="${agent_url}" \
            COUNTRY_OUTAGE_AGENT_SHARED_TOKEN="${agent_token}" \
            COUNTRY_OUTAGE_AGENT_IDENTITY_MODE="${agent_identity}" \
            COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID="${agent_user}" \
            DOMEYE_COUNTRY_OUTAGE_AGENT_CONFIG_SHA256="${agent_config_sha}" \
            PYTHONUNBUFFERED=1 \
            PYTHONDONTWRITEBYTECODE=1 \
            VIRTUAL_ENV="${RUNTIME_ROOT}/venv" \
            "${RUNTIME_ROOT}/venv/bin/python" run.py

    local attempt
    for (( attempt = 1; attempt <= 60; attempt++ )); do
        mapfile -t sessions < <(list_sessions)
        if (( ${#sessions[@]} == 1 )) \
            && session_process "${sessions[0]}" >/dev/null \
            && curl -fsS --max-time 2 \
                "http://127.0.0.1:${API_PORT}/api/v1/healthz" >/dev/null 2>&1; then
            printf '运行时启动成功：%s / %s\n' "${sessions[0]}" "${selected_release}"
            return 0
        fi
        sleep 0.5
    done
    error "运行时 30 秒内未就绪：${selected_release}"
    tail -80 "${log_root}/screen.log" >&2 || true
    return 1
}

stop_runtime() {
    validate_runtime
    mapfile -t sessions < <(list_sessions)
    if (( ${#sessions[@]} == 0 )); then
        printf '运行时未启动：%s\n' "$(release_id)"
        return 0
    fi
    if (( ${#sessions[@]} != 1 )); then
        error "发现多个同名会话：${sessions[*]}"
        return 1
    fi
    session_process "${sessions[0]}" >/dev/null || {
        error "拒绝停止身份不匹配的会话：${sessions[0]}"
        return 1
    }
    screen -S "${sessions[0]}" -X quit
    local attempt
    for (( attempt = 1; attempt <= 40; attempt++ )); do
        if ! list_sessions | grep -Fxq "${sessions[0]}"; then
            printf '运行时已停止：%s\n' "$(release_id)"
            return 0
        fi
        sleep 0.25
    done
    error "会话未停止：${sessions[0]}"
    return 1
}

status_runtime() {
    validate_runtime
    mapfile -t sessions < <(list_sessions)
    (( ${#sessions[@]} == 1 )) || {
        error "运行时会话数量不是 1：${#sessions[@]}"
        return 1
    }
    local pid
    pid="$(session_process "${sessions[0]}")" || {
        error "运行时进程身份不匹配：${sessions[0]}"
        return 1
    }
    curl -fsS --max-time 5 "http://127.0.0.1:${API_PORT}/api/v1/healthz" \
        | jq -e '.status == "ok" and .service == "domeye-core"' >/dev/null
    jq -n \
        --arg status active \
        --arg mode "${RUNTIME_MODE}" \
        --arg release_id "$(release_id)" \
        --arg runtime_root "${RUNTIME_ROOT}" \
        --arg session "${sessions[0]}" \
        --argjson pid "${pid}" \
        --argjson port "${API_PORT}" \
        '{status:$status,mode:$mode,release_id:$release_id,runtime_root:$runtime_root,session:$session,pid:$pid,port:$port}'
}

if (( $# != 1 )); then
    printf '用法：%s start|stop|status\n' "${0##*/}" >&2
    exit 2
fi

case "$1" in
    start) start_runtime ;;
    stop) stop_runtime ;;
    status) status_runtime ;;
    *)
        error "未知命令：$1"
        exit 2
        ;;
esac
