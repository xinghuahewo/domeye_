#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly TEST_ROOT="${DOMEYE_COUNTRY_OUTAGE_P1_TEST_ROOT:-}"
if [[ -n "${TEST_ROOT}" ]]; then
    [[ "${TEST_ROOT}" =~ ^/((private/)?tmp)/domeye-country-outage-p1-test\.[A-Za-z0-9._-]+$ \
        && -d "${TEST_ROOT}" && ! -L "${TEST_ROOT}" ]] || {
        printf 'P1 Chat 部署错误：测试根目录不在允许的临时目录边界\n' >&2
        exit 1
    }
    readonly RUNTIME_BASE="${TEST_ROOT}/runtime"
    readonly NODE_BIN_DIR="${TEST_ROOT}/tools/node/bin"
    readonly AUDIT_DIRECTORY="${TEST_ROOT}/audit"
    readonly TEST_MODE=true
else
    readonly RUNTIME_BASE='/home/bgpdata/Domeye-Core-runtime'
    readonly NODE_BIN_DIR='/home/bgpdata/.local/node-v22.23.1-linux-x64/bin'
    readonly AUDIT_DIRECTORY='/var/log/domeye/country-outage-p1-pi-audit'
    readonly TEST_MODE=false
fi
readonly RUNTIME_ROOT="${RUNTIME_BASE}/country-outage-p1-chat"
readonly RELEASE_ROOT="${RUNTIME_ROOT}/releases"
readonly CURRENT_LINK="${RUNTIME_ROOT}/current"
readonly STATE_ROOT="${RUNTIME_ROOT}/state"
readonly ACTIVE_STATE="${STATE_ROOT}/active.json"
readonly ROLLBACK_STATE="${STATE_ROOT}/rollback.json"
readonly CONFIG_FILE="${RUNTIME_BASE}/config/country-outage-p1-chat.env"
readonly LOCK_FILE="${STATE_ROOT}/lifecycle.lock"
readonly SCREEN_NAME='domeye_country_outage_p1_chat'
readonly NODE="${NODE_BIN_DIR}/node"
readonly NPM="${NODE_BIN_DIR}/npm"

error() { printf 'P1 Chat 部署错误：%s\n' "$*" >&2; }
info() { printf '%s\n' "$*"; }
sha256_file() { sha256sum "$1" | awk '{print $1}'; }

require_root() {
    [[ "${TEST_MODE}" == true || "${EUID}" -eq 0 ]] || {
        error '生命周期操作必须由 root 执行'
        return 1
    }
}

require_commands() {
    local command_name
    for command_name in awk chmod cp date find flock grep install jq ln mktemp mv \
        readlink screen sed sha256sum sleep ss stat tar; do
        command -v "${command_name}" >/dev/null 2>&1 || {
            error "缺少命令 ${command_name}"
            return 1
        }
    done
}

validate_release_id() {
    [[ "$1" =~ ^[0-9]{8}T[0-9]{6}Z-country-outage-p1-chat-[a-z0-9][a-z0-9-]{0,31}$ ]] || {
        error "release-id 无效：$1"
        return 1
    }
}

release_directory() {
    validate_release_id "$1" || return 1
    printf '%s/%s\n' "${RELEASE_ROOT}" "$1"
}

owner_mode() {
    local path="$1" mode="$2" expected_uid=0 expected_gid=0
    if [[ "${TEST_MODE}" == true ]]; then
        expected_uid="$(id -u)"
        expected_gid="$(id -g)"
    fi
    local actual_uid actual_gid actual_mode
    if stat -c '%u' "${path}" >/dev/null 2>&1; then
        actual_uid="$(stat -c '%u' "${path}")"
        actual_gid="$(stat -c '%g' "${path}")"
        actual_mode="$(stat -c '%a' "${path}")"
    else
        actual_uid="$(stat -f '%u' "${path}")"
        actual_gid="$(stat -f '%g' "${path}")"
        actual_mode="$(stat -f '%Lp' "${path}")"
        actual_mode="${actual_mode#0}"
    fi
    [[ "${actual_uid}" == "${expected_uid}" \
        && "${actual_gid}" == "${expected_gid}" \
        && "${actual_mode}" == "${mode}" ]] || {
        error "所有者或权限不符 ${path}：${actual_uid}:${actual_gid}:${actual_mode}，期望 ${expected_uid}:${expected_gid}:${mode}"
        return 1
    }
}

ensure_runtime_directories() {
    install -d -m 0700 "${RUNTIME_ROOT}" "${RELEASE_ROOT}" "${STATE_ROOT}" \
        "${RUNTIME_BASE}/config" "${AUDIT_DIRECTORY}"
}

read_config_value() {
    local key="$1"
    awk -v wanted="${key}" '
      /^[[:space:]]*(#|$)/ {next}
      {p=index($0,"="); if(p<2) next; if(substr($0,1,p-1)==wanted){n++;v=substr($0,p+1)}}
      END {if(n!=1) exit 2; print v}
    ' "${CONFIG_FILE}"
}

validate_config() {
    [[ -f "${CONFIG_FILE}" && ! -L "${CONFIG_FILE}" ]] || {
        error "配置不是普通文件：${CONFIG_FILE}"
        return 1
    }
    owner_mode "${CONFIG_FILE}" 600 || {
        error 'P1 配置必须由受信用户持有且为 0600'
        return 1
    }
    local allowed=' COUNTRY_OUTAGE_AGENT_URL COUNTRY_OUTAGE_AGENT_SHARED_TOKEN COUNTRY_OUTAGE_AGENT_HOST COUNTRY_OUTAGE_AGENT_PORT DOMEYE_API_BASE_URL COUNTRY_OUTAGE_P1_API_TIMEOUT_MS COUNTRY_OUTAGE_P1_MODEL_TIMEOUT_MS COUNTRY_OUTAGE_P1_TURN_TIMEOUT_MS COUNTRY_OUTAGE_PI_CERTIFIED_REGISTRY_PATH COUNTRY_OUTAGE_PI_PROFILE COUNTRY_OUTAGE_PI_AUTH_PATH COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY '
    local line key value seen=' '
    while IFS= read -r line || [[ -n "${line}" ]]; do
        [[ -z "${line}" || "${line}" == \#* ]] && continue
        [[ "${line}" == *=* && "${line}" != *$'\r'* ]] || { error '配置行无效'; return 1; }
        key="${line%%=*}"; value="${line#*=}"
        [[ "${key}" =~ ^[A-Z][A-Z0-9_]*$ && "${allowed}" == *" ${key} "* ]] || {
            error "未授权配置键 ${key}"; return 1;
        }
        [[ -n "${value}" && "${value}" != *[[:space:]]* ]] || {
            error "配置值为空或含空白 ${key}"; return 1;
        }
        [[ "${seen}" != *" ${key} "* ]] || { error "配置键重复 ${key}"; return 1; }
        seen+="${key} "
    done < "${CONFIG_FILE}"
    local required
    for required in ${allowed}; do read_config_value "${required}" >/dev/null || {
        error "配置键必须恰好出现一次 ${required}"; return 1;
    }; done
    [[ "$(read_config_value COUNTRY_OUTAGE_AGENT_URL)" == 'http://127.0.0.1:28475' \
        && "$(read_config_value COUNTRY_OUTAGE_AGENT_HOST)" == '127.0.0.1' \
        && "$(read_config_value COUNTRY_OUTAGE_AGENT_PORT)" == '28475' \
        && "$(read_config_value DOMEYE_API_BASE_URL)" == 'http://127.0.0.1:28473/api/v2/' \
        && "$(read_config_value COUNTRY_OUTAGE_PI_PROFILE)" == 'deepseek-v4-flash-pi-0.84.1-v1' \
        && "$(read_config_value COUNTRY_OUTAGE_PI_CERTIFIED_REGISTRY_PATH)" == "${CURRENT_LINK}/agent-sidecar/resources/certified-models/country-outage-p1-semantic-models-v1.json" \
        && "$(read_config_value COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY)" == "${AUDIT_DIRECTORY}" ]] || {
        error 'P1 固定运行配置漂移'
        return 1
    }
    local token auth
    token="$(read_config_value COUNTRY_OUTAGE_AGENT_SHARED_TOKEN)"
    [[ ${#token} -ge 32 ]] || { error '共享 Token 长度不足'; return 1; }
    auth="$(read_config_value COUNTRY_OUTAGE_PI_AUTH_PATH)"
    [[ -f "${auth}" && ! -L "${auth}" ]] || { error '模型凭据文件无效'; return 1; }
    owner_mode "${auth}" 600 || { error '模型凭据必须为 0600'; return 1; }
    owner_mode "${AUDIT_DIRECTORY}" 700 || { error '审计目录必须为 0700'; return 1; }
}

verify_release() {
    local release_id="$1" directory
    directory="$(release_directory "${release_id}")"
    [[ -d "${directory}" && ! -L "${directory}" \
        && "$(readlink -f -- "${directory}")" == "${directory}" ]] || {
        error "release 目录无效 ${directory}"; return 1;
    }
    [[ -f "${directory}/SHA256SUMS" && ! -L "${directory}/SHA256SUMS" ]] || {
        error '缺少 SHA256SUMS'; return 1;
    }
    (cd -- "${directory}" && sha256sum -c SHA256SUMS >/dev/null)
    "${NODE}" "${directory}/deployment/verify-release.mjs" "${directory}" >/dev/null
}

prepare_release() {
    (( $# == 4 )) || { error '用法：prepare <release-id> <source.tar.gz> <commit> <annotated-tag>'; return 2; }
    local release_id="$1" source_archive="$2" source_commit="$3" source_tag="$4"
    validate_release_id "${release_id}"
    [[ "${source_commit}" =~ ^[0-9a-f]{40}$ && -n "${source_tag}" ]] || {
        error '提交或 tag 身份无效'; return 1;
    }
    [[ -f "${source_archive}" && ! -L "${source_archive}" ]] || {
        error '源码归档无效'; return 1;
    }
    local target candidate extracted certification registry source_sha
    target="$(release_directory "${release_id}")"
    [[ ! -e "${target}" && ! -L "${target}" ]] || { error 'release 已存在'; return 1; }
    candidate="$(mktemp -d "${RELEASE_ROOT}/.prepare-${release_id}.XXXXXX")"
    extracted="$(mktemp -d "${RELEASE_ROOT}/.source-${release_id}.XXXXXX")"
    cleanup_prepare() {
        local path
        for path in "${candidate}" "${extracted}"; do
            if [[ -d "${path}" && ! -L "${path}" ]]; then chmod -R u+w "${path}" 2>/dev/null || true; find "${path}" -depth -delete; fi
        done
    }
    trap cleanup_prepare RETURN
    chmod 0700 "${candidate}" "${extracted}"
    tar -xzf "${source_archive}" -C "${extracted}"
    [[ -f "${extracted}/agent-sidecar/package-lock.json" \
        && -f "${extracted}/evaluation/country-outage/p1-prod-release/attempt-004/manifest.json" ]] || {
        error '源码归档缺少 P1 正式制品'; return 1;
    }
    install -d -m 0700 "${candidate}/agent-sidecar" "${candidate}/certification" \
        "${candidate}/source-identity" "${candidate}/deployment"
    cp -R "${extracted}/evaluation/country-outage/p1-prod-release/attempt-004/." \
        "${candidate}/certification/"
    cp "${SCRIPT_DIR}/verify-release.mjs" "${SCRIPT_DIR}/probe.mjs" \
        "${candidate}/deployment/"
    certification="${candidate}/certification/manifest.json"
    while IFS= read -r relative_path; do
        [[ -f "${extracted}/${relative_path}" && ! -L "${extracted}/${relative_path}" ]] || {
            error "认证源码缺失 ${relative_path}"; return 1;
        }
        install -d -m 0700 "${candidate}/source-identity/$(dirname -- "${relative_path}")"
        cp "${extracted}/${relative_path}" "${candidate}/source-identity/${relative_path}"
    done < <(jq -er '.source_identity.files[].path' "${certification}")
    local runtime_contract_root='contracts/agent/country-outage-p1-page-coverage/s2'
    install -d -m 0700 "${candidate}/${runtime_contract_root}"
    local runtime_contract
    for runtime_contract in semantic-plan.schema.json capability-catalog.json \
        tool-contracts.json oracle.json policy.json; do
        cp "${candidate}/source-identity/${runtime_contract_root}/${runtime_contract}" \
            "${candidate}/${runtime_contract_root}/${runtime_contract}"
    done
    local trend_contract_root='contracts/agent/country-outage-p1-trend-operator/v1'
    install -d -m 0700 "${candidate}/${trend_contract_root}"
    local trend_contract
    for trend_contract in operator-contract.json trend-profiles.json \
        synthetic-oracle.json p1-integration-contract.json; do
        [[ -f "${extracted}/${trend_contract_root}/${trend_contract}" \
            && ! -L "${extracted}/${trend_contract_root}/${trend_contract}" ]] || {
            error "趋势算子合同缺失 ${trend_contract}"; return 1;
        }
        cp "${extracted}/${trend_contract_root}/${trend_contract}" \
            "${candidate}/${trend_contract_root}/${trend_contract}"
    done
    (
        # 全量 Sidecar 测试会读取仓库根目录下的 contracts、dev 与评测制品，
        # 因此必须在完整源码归档中执行；通过并裁剪生产依赖后再复制运行制品。
        cd -- "${extracted}/agent-sidecar"
        export PATH="${NODE_BIN_DIR}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        "${NPM}" ci --ignore-scripts
        "${NODE}" scripts/apply_pi_response_model_patch.mjs --apply
        "${NPM}" test
        "${NPM}" audit --omit=dev --audit-level=high
        "${NPM}" prune --omit=dev --ignore-scripts
        "${NODE}" scripts/apply_pi_response_model_patch.mjs --apply
        "${NODE}" scripts/apply_pi_response_model_patch.mjs --verify
    )
    cp -R "${extracted}/agent-sidecar/." "${candidate}/agent-sidecar/"
    while IFS= read -r bin_directory; do find "${bin_directory}" -depth -delete; done < <(
        find "${candidate}/agent-sidecar/node_modules" -type d -name .bin -print
    )
    find "${candidate}" -type l -print -quit | grep -q . && {
        error '运行制品包含符号链接'; return 1;
    }
    local trend_identity="${candidate}/TREND-OPERATOR-IDENTITY.json"
    jq -n \
        --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '{schema_version:"country_outage_p1_trend_operator_identity_v1",created_at:$created_at,execution_unit:"OP-04",capability_id:"CAP-TREND-001",operator_id:"event-window-trend",operator_version:"1.2.0",profile_registry_version:"country-outage-p1-trend-profile-v1",model_dependency:"none",files:[]}' \
        > "${trend_identity}"
    local trend_identity_path trend_identity_tmp
    for trend_identity_path in \
        agent-sidecar/src/chat/event-window-trend.ts \
        agent-sidecar/src/chat/trend-aware-grounder.ts \
        agent-sidecar/src/chat/page-capability-executor.ts \
        agent-sidecar/src/chat/runtime-v2-conversation.ts \
        agent-sidecar/src/cli/formal-p1-sidecar.ts \
        agent-sidecar/dist/src/chat/event-window-trend.js \
        agent-sidecar/dist/src/chat/trend-aware-grounder.js \
        agent-sidecar/dist/src/chat/page-capability-executor.js \
        agent-sidecar/dist/src/chat/runtime-v2-conversation.js \
        agent-sidecar/dist/src/cli/formal-p1-sidecar.js \
        "${trend_contract_root}/operator-contract.json" \
        "${trend_contract_root}/trend-profiles.json" \
        "${trend_contract_root}/p1-integration-contract.json"; do
        [[ -f "${candidate}/${trend_identity_path}" \
            && ! -L "${candidate}/${trend_identity_path}" ]] || {
            error "趋势身份文件缺失 ${trend_identity_path}"; return 1;
        }
        trend_identity_tmp="${trend_identity}.tmp"
        jq --arg path "${trend_identity_path}" \
            --arg sha "$(sha256_file "${candidate}/${trend_identity_path}")" \
            '.files += [{path:$path,sha256:$sha}]' \
            "${trend_identity}" > "${trend_identity_tmp}"
        mv "${trend_identity_tmp}" "${trend_identity}"
    done
    registry="${candidate}/agent-sidecar/resources/certified-models/country-outage-p1-semantic-models-v1.json"
    source_sha="$(sha256_file "${source_archive}")"
    jq -n \
        --arg release_id "${release_id}" \
        --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg commit "${source_commit}" \
        --arg tag "${source_tag}" \
        --arg source_sha "${source_sha}" \
        --arg cert_sha "$(sha256_file "${certification}")" \
        --arg registry_sha "$(sha256_file "${registry}")" \
        --arg trend_identity_sha "$(sha256_file "${trend_identity}")" \
        --arg trend_integration_sha "$(sha256_file "${candidate}/${trend_contract_root}/p1-integration-contract.json")" \
        --arg trend_profiles_sha "$(sha256_file "${candidate}/${trend_contract_root}/trend-profiles.json")" \
        '{schema_version:"country_outage_p1_chat_release_v1",component:"country_outage_p1_chat_sidecar",release_id:$release_id,created_at:$created_at,source:{commit:$commit,annotated_tag:$tag,archive_sha256:$source_sha},runtime:{host:"127.0.0.1",port:28475,node_version:"v22.23.1",pi_version:"0.84.1",maximum_provider_request_count_per_turn:1,event_window_trend_operator:{execution_unit:"OP-04",capability_id:"CAP-TREND-001",operator_id:"event-window-trend",operator_version:"1.2.0",model_dependency:"none"}},billing:{business_cost_limit:null,per_provider_call_usage_and_estimated_cost:"required"},boundaries:{collector:"rrc25",event_type:"country_outage",report_capability:"disabled",external_evidence:"disabled",network_rca:false},hashes:{certification_manifest:$cert_sha,certified_registry:$registry_sha,trend_operator_identity:$trend_identity_sha,trend_integration_contract:$trend_integration_sha,trend_profiles:$trend_profiles_sha},checks:{agent_sidecar_tests:"passed",production_dependency_audit:"passed",vendor_patch:"verified",event_window_trend_integration:"verified"}}' \
        > "${candidate}/RELEASE-MANIFEST.json"
    "${NODE}" "${candidate}/deployment/verify-release.mjs" "${candidate}" >/dev/null
    (
        cd -- "${candidate}"
        find . -type f ! -name SHA256SUMS -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > SHA256SUMS
    )
    chmod -R go-w "${candidate}"
    mv "${candidate}" "${target}"
    candidate=''
    trap - RETURN
    cleanup_prepare
    info "P1 release 已准备：${target}"
}

list_sessions() {
    screen -ls 2>&1 | awk -v name="${SCREEN_NAME}" '$1 ~ ("^[0-9]+\\." name "$") {print $1}'
}

stop_runtime() {
    local -a sessions
    mapfile -t sessions < <(list_sessions)
    (( ${#sessions[@]} <= 1 )) || { error '发现多个 P1 Screen 会话'; return 1; }
    if (( ${#sessions[@]} == 0 )); then info 'P1 Sidecar 未运行'; return 0; fi
    screen -S "${sessions[0]}" -X quit
    local attempt
    for ((attempt=1; attempt<=30; attempt++)); do
        mapfile -t sessions < <(list_sessions)
        (( ${#sessions[@]} == 0 )) && { info 'P1 Sidecar 已停止'; return 0; }
        sleep 0.2
    done
    error 'P1 Sidecar 未在 6 秒内停止'; return 1
}

start_runtime() {
    (( $# == 1 )) || { error '用法：start <release-id>'; return 2; }
    local release_id="$1" directory previous='' link_candidate log_file
    verify_release "${release_id}"
    validate_config
    directory="$(release_directory "${release_id}")"
    local -a sessions environment
    mapfile -t sessions < <(list_sessions)
    (( ${#sessions[@]} == 0 )) || { error 'P1 Sidecar 已运行，请先 stop'; return 1; }
    if [[ -f "${ACTIVE_STATE}" ]]; then
        previous="$(jq -er '.release_id' "${ACTIVE_STATE}")"
        cp "${ACTIVE_STATE}" "${ROLLBACK_STATE}"
    fi
    link_candidate="${RUNTIME_ROOT}/.current-${release_id}"
    ln -s "${directory}" "${link_candidate}"
    mv -Tf "${link_candidate}" "${CURRENT_LINK}"
    while IFS= read -r line || [[ -n "${line}" ]]; do
        [[ -z "${line}" || "${line}" == \#* ]] && continue
        environment+=("${line}")
    done < "${CONFIG_FILE}"
    log_file="${RUNTIME_ROOT}/p1-chat-${release_id}.log"
    screen -L -Logfile "${log_file}" -dmS "${SCREEN_NAME}" \
        env -i HOME=/home/bgpdata USER=root LOGNAME=root LANG=C.UTF-8 LC_ALL=C.UTF-8 \
        PATH="${NODE_BIN_DIR}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
        "${environment[@]}" \
        "${NODE}" "${CURRENT_LINK}/agent-sidecar/dist/src/cli/serve-formal-p1.js"
    local attempt
    for ((attempt=1; attempt<=120; attempt++)); do
        if "${NODE}" "${CURRENT_LINK}/deployment/probe.mjs" "${CONFIG_FILE}" >/dev/null 2>&1; then
            jq -n --arg release_id "${release_id}" --arg previous "${previous}" \
                --arg activated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
                --arg manifest_sha "$(sha256_file "${directory}/RELEASE-MANIFEST.json")" \
                '{schema_version:"country_outage_p1_chat_active_v1",release_id:$release_id,previous_release_id:(if $previous=="" then null else $previous end),activated_at:$activated_at,release_manifest_sha256:$manifest_sha}' \
                > "${ACTIVE_STATE}"
            chmod 0600 "${ACTIVE_STATE}"
            info "P1 Sidecar 已启动：${release_id}"
            return 0
        fi
        sleep 0.5
    done
    stop_runtime || true
    if [[ -n "${previous}" ]]; then
        ln -s "$(release_directory "${previous}")" "${link_candidate}"
        mv -Tf "${link_candidate}" "${CURRENT_LINK}"
    else
        unlink "${CURRENT_LINK}" 2>/dev/null || true
    fi
    error "P1 Sidecar 60 秒内未就绪，日志 ${log_file}"
    return 1
}

status_runtime() {
    [[ -f "${ACTIVE_STATE}" ]] || { error '没有 P1 active 状态'; return 1; }
    local release_id
    release_id="$(jq -er '.release_id' "${ACTIVE_STATE}")"
    verify_release "${release_id}"
    validate_config
    "${NODE}" "${CURRENT_LINK}/deployment/probe.mjs" "${CONFIG_FILE}"
    jq . "${ACTIVE_STATE}"
}

rollback_runtime() {
    [[ -f "${ROLLBACK_STATE}" ]] || { error '没有可回滚 P1 状态'; return 1; }
    local release_id
    release_id="$(jq -er '.release_id' "${ROLLBACK_STATE}")"
    stop_runtime
    start_runtime "${release_id}"
}

main() {
    require_root
    require_commands
    ensure_runtime_directories
    exec 9>"${LOCK_FILE}"
    flock -n 9 || { error '另一个 P1 生命周期操作正在运行'; return 1; }
    local action="${1:-}"; shift || true
    case "${action}" in
        prepare) prepare_release "$@" ;;
        start) start_runtime "$@" ;;
        stop) stop_runtime "$@" ;;
        status|probe) status_runtime "$@" ;;
        rollback) rollback_runtime "$@" ;;
        verify) verify_release "$@" ;;
        _test_validate_config) validate_config ;;
        *) error '用法：manage.sh {prepare|start|stop|status|rollback|verify} ...'; return 2 ;;
    esac
}

main "$@"
