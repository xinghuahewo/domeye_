#!/usr/bin/env bash

# 国家中断 Agent 固定历史数据档部署公共库。
# 本文件只定义函数；调用方负责启用 set -Eeuo pipefail。

readonly COA_DEPLOY_LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly COA_DEPLOY_DIR="$(cd -- "${COA_DEPLOY_LIB_DIR}/.." && pwd -P)"

coa_error() {
    printf '错误：%s\n' "$*" >&2
}

coa_info() {
    printf '%s\n' "$*"
}

coa_require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        coa_error "缺少命令：$1"
        return 1
    }
}

coa_stat_uid() {
    if stat -c '%u' "$1" >/dev/null 2>&1; then
        stat -c '%u' "$1"
    else
        stat -f '%u' "$1"
    fi
}

coa_stat_gid() {
    if stat -c '%g' "$1" >/dev/null 2>&1; then
        stat -c '%g' "$1"
    else
        stat -f '%g' "$1"
    fi
}

coa_stat_mode() {
    local value
    if stat -c '%a' "$1" >/dev/null 2>&1; then
        value="$(stat -c '%a' "$1")"
    else
        value="$(stat -f '%Lp' "$1")"
    fi
    printf '%s\n' "${value#0}"
}

coa_sha256() {
    sha256sum "$1" | awk '{print $1}'
}

coa_realpath() {
    local target="$1"
    if command -v realpath >/dev/null 2>&1; then
        realpath "${target}"
        return
    fi
    local directory name
    directory="$(cd -- "$(dirname -- "${target}")" && pwd -P)"
    name="$(basename -- "${target}")"
    printf '%s/%s\n' "${directory}" "${name}"
}

coa_initialize_paths() {
    local test_root="${DOMEYE_COUNTRY_OUTAGE_AGENT_TEST_ROOT:-}"
    if [[ -n "${test_root}" ]]; then
        if [[ ! "${test_root}" =~ ^/((private/)?tmp)/domeye-country-outage-agent-test\.[A-Za-z0-9._-]+$ \
            || ! -d "${test_root}" || -L "${test_root}" ]]; then
            coa_error '测试根目录必须是 /tmp 下既有的 domeye-country-outage-agent-test.* 实际目录'
            return 1
        fi
        COA_TEST_MODE=true
        COA_PROJECT_ROOT="${test_root}/project"
        COA_RUNTIME_BASE="${test_root}/runtime"
        COA_RUNTIME_ROOT="${COA_RUNTIME_BASE}/country-outage-agent"
        COA_CONFIG_ROOT="${COA_RUNTIME_BASE}/config"
        COA_CONFIG_FILE="${COA_CONFIG_ROOT}/country-outage-agent.env"
        COA_AUDIT_FIXED_PATH="${test_root}/audit"
        COA_NODE_BIN_DIR="${test_root}/tools/node/bin"
    else
        COA_TEST_MODE=false
        COA_PROJECT_ROOT='/home/bgpdata/Domeye-Core'
        COA_RUNTIME_BASE='/home/bgpdata/Domeye-Core-runtime'
        COA_RUNTIME_ROOT="${COA_RUNTIME_BASE}/country-outage-agent"
        COA_CONFIG_ROOT="${COA_RUNTIME_BASE}/config"
        COA_CONFIG_FILE="${COA_CONFIG_ROOT}/country-outage-agent.env"
        COA_AUDIT_FIXED_PATH='/var/log/domeye/country-outage-pi-audit'
        COA_NODE_BIN_DIR='/home/bgpdata/.local/node-v22.23.1-linux-x64/bin'
    fi

    COA_RELEASE_ROOT="${COA_RUNTIME_ROOT}/releases"
    COA_CURRENT_LINK="${COA_RUNTIME_ROOT}/current"
    COA_STATE_ROOT="${COA_RUNTIME_ROOT}/state"
    COA_LOG_ROOT="${COA_RUNTIME_ROOT}/log"
    COA_ACTIVE_STATE="${COA_STATE_ROOT}/active.json"
    COA_ROLLBACK_STATE="${COA_STATE_ROOT}/rollback.json"
    COA_LOCK_FILE="${COA_STATE_ROOT}/lifecycle.lock"
    COA_SCREEN_NAME='domeye_country_outage_agent'
    COA_NODE="${COA_NODE_BIN_DIR}/node"
    COA_NPM="${COA_NODE_BIN_DIR}/npm"
    COA_EXPECTED_AGENT_URL='http://127.0.0.1:28474'
    COA_EXPECTED_API_BASE_URL='http://127.0.0.1:28473/api/v2/'
    COA_EXPECTED_IDENTITY_MODE='internal_fixed_history'
    COA_EXPECTED_PROFILE='deepseek-v4-flash-pi-0.82.1-v1'
    COA_EXPECTED_NARRATOR='pi-sdk-certified'
    COA_EXPECTED_NODE_VERSION='v22.23.1'
    COA_EXPECTED_PDF_PYTHON="${COA_CURRENT_LINK}/pdf-venv/bin/python"
    COA_EXPECTED_REGISTRY="${COA_CURRENT_LINK}/agent-sidecar/resources/certified-models/country-outage-pi-models-v1.json"
    COA_REQUIREMENTS_FILE="${COA_DEPLOY_DIR}/requirements-pdf.txt"

    export COA_TEST_MODE COA_PROJECT_ROOT COA_RUNTIME_BASE COA_RUNTIME_ROOT
    export COA_CONFIG_ROOT COA_CONFIG_FILE COA_AUDIT_FIXED_PATH
    export COA_RELEASE_ROOT COA_CURRENT_LINK COA_STATE_ROOT COA_LOG_ROOT
    export COA_ACTIVE_STATE COA_ROLLBACK_STATE COA_LOCK_FILE
    export COA_SCREEN_NAME COA_NODE_BIN_DIR COA_NODE COA_NPM
    export COA_EXPECTED_AGENT_URL COA_EXPECTED_API_BASE_URL
    export COA_EXPECTED_IDENTITY_MODE COA_EXPECTED_PROFILE
    export COA_EXPECTED_NARRATOR COA_EXPECTED_NODE_VERSION
    export COA_EXPECTED_PDF_PYTHON COA_EXPECTED_REGISTRY
    export COA_REQUIREMENTS_FILE
}

coa_validate_release_id() {
    local release_id="$1"
    local timestamp suffix
    timestamp="${release_id%%-country-outage-agent-core-*}"
    suffix="${release_id#*-country-outage-agent-core-}"
    if [[ ! "${timestamp}" =~ ^[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z$ \
        || "${suffix}" == "${release_id}" \
        || ${#suffix} -lt 1 \
        || ${#suffix} -gt 32 \
        || ! "${suffix}" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
        coa_error "release-id 格式无效：${release_id}"
        return 1
    fi
}

coa_release_dir() {
    local release_id="$1"
    coa_validate_release_id "${release_id}" || return 1
    printf '%s/%s\n' "${COA_RELEASE_ROOT}" "${release_id}"
}

coa_require_no_symlink_ancestors() {
    local target="$1"
    local current
    if [[ "${target}" != /* ]]; then
        coa_error "路径必须是绝对路径：${target}"
        return 1
    fi
    current="${target}"
    while [[ "${current}" != '/' ]]; do
        if [[ -L "${current}" ]]; then
            coa_error "路径不得经过符号链接：${current}"
            return 1
        fi
        current="$(dirname -- "${current}")"
    done
}

coa_require_owner_mode() {
    local target="$1"
    local expected_mode="$2"
    local expected_uid expected_gid
    if [[ "${COA_TEST_MODE}" == true ]]; then
        expected_uid="$(id -u)"
        expected_gid="$(id -g)"
    else
        expected_uid='0'
        expected_gid='0'
    fi
    if [[ "$(coa_stat_uid "${target}")" != "${expected_uid}" \
        || "$(coa_stat_gid "${target}")" != "${expected_gid}" \
        || "$(coa_stat_mode "${target}")" != "${expected_mode}" ]]; then
        coa_error "所有者或权限不符合要求：${target}（应为 uid=${expected_uid} gid=${expected_gid} mode=${expected_mode}）"
        return 1
    fi
}

coa_require_secure_directory() {
    local target="$1"
    local mode="$2"
    if [[ ! -d "${target}" || -L "${target}" ]]; then
        coa_error "安全目录不存在、不是目录或是符号链接：${target}"
        return 1
    fi
    coa_require_no_symlink_ancestors "${target}" || return 1
    coa_require_owner_mode "${target}" "${mode}"
}

coa_require_secure_file() {
    local target="$1"
    local mode="$2"
    if [[ ! -f "${target}" || -L "${target}" ]]; then
        coa_error "安全文件不存在、不是普通文件或是符号链接：${target}"
        return 1
    fi
    coa_require_no_symlink_ancestors "${target}" || return 1
    coa_require_owner_mode "${target}" "${mode}"
}

coa_require_trusted_readonly_file() {
    local target="$1"
    local expected_uid expected_gid mode numeric_mode
    if [[ ! -f "${target}" || -L "${target}" ]]; then
        coa_error "可信输入不存在、不是普通文件或是符号链接：${target}"
        return 1
    fi
    coa_require_no_symlink_ancestors "${target}" || return 1
    if [[ "${COA_TEST_MODE}" == true ]]; then
        expected_uid="$(id -u)"
        expected_gid="$(id -g)"
    else
        expected_uid='0'
        expected_gid='0'
    fi
    mode="$(coa_stat_mode "${target}")"
    numeric_mode=$((8#${mode}))
    if [[ "$(coa_stat_uid "${target}")" != "${expected_uid}" \
        || "$(coa_stat_gid "${target}")" != "${expected_gid}" \
        || $((numeric_mode & 022)) -ne 0 ]]; then
        coa_error "可信输入必须由指定用户拥有且不可被 group/other 写入：${target}"
        return 1
    fi
}

coa_require_trusted_executable() {
    local configured_path="$1"
    local resolved_path
    if [[ ! -x "${configured_path}" ]]; then
        coa_error "可信可执行文件不存在或不可执行：${configured_path}"
        return 1
    fi
    resolved_path="$(coa_realpath "${configured_path}")" || return 1
    coa_require_trusted_readonly_file "${resolved_path}" || return 1
    printf '%s\n' "${resolved_path}"
}

coa_config_key_allowed() {
    case "$1" in
        COUNTRY_OUTAGE_AGENT_URL|\
        COUNTRY_OUTAGE_AGENT_SHARED_TOKEN|\
        COUNTRY_OUTAGE_AGENT_IDENTITY_MODE|\
        COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID|\
        COUNTRY_OUTAGE_AGENT_NARRATOR|\
        COUNTRY_OUTAGE_AGENT_HOST|\
        COUNTRY_OUTAGE_AGENT_PORT|\
        COUNTRY_OUTAGE_AGENT_REPORT_CACHE_TTL_MS|\
        COUNTRY_OUTAGE_AGENT_PYTHON_BOOTSTRAP|\
        DOMEYE_API_BASE_URL|\
        DOMEYE_API_TIMEOUT_MS|\
        DOMEYE_REPORT_PYTHON_EXECUTABLE|\
        DOMEYE_REPORT_FONT_PATH|\
        DOMEYE_REPORT_FONT_SHA256|\
        DOMEYE_REPORT_PDF_TIMEOUT_MS|\
        COUNTRY_OUTAGE_PI_CERTIFIED_REGISTRY_PATH|\
        COUNTRY_OUTAGE_PI_PROFILE|\
        COUNTRY_OUTAGE_PI_AUTH_PATH|\
        COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

coa_required_config_keys() {
    printf '%s\n' \
        COUNTRY_OUTAGE_AGENT_URL \
        COUNTRY_OUTAGE_AGENT_SHARED_TOKEN \
        COUNTRY_OUTAGE_AGENT_IDENTITY_MODE \
        COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID \
        COUNTRY_OUTAGE_AGENT_NARRATOR \
        COUNTRY_OUTAGE_AGENT_HOST \
        COUNTRY_OUTAGE_AGENT_PORT \
        COUNTRY_OUTAGE_AGENT_REPORT_CACHE_TTL_MS \
        COUNTRY_OUTAGE_AGENT_PYTHON_BOOTSTRAP \
        DOMEYE_API_BASE_URL \
        DOMEYE_API_TIMEOUT_MS \
        DOMEYE_REPORT_PYTHON_EXECUTABLE \
        DOMEYE_REPORT_FONT_PATH \
        DOMEYE_REPORT_FONT_SHA256 \
        DOMEYE_REPORT_PDF_TIMEOUT_MS \
        COUNTRY_OUTAGE_PI_CERTIFIED_REGISTRY_PATH \
        COUNTRY_OUTAGE_PI_PROFILE \
        COUNTRY_OUTAGE_PI_AUTH_PATH \
        COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY
}

coa_config_value() {
    local wanted="$1"
    awk -v wanted="${wanted}" '
        /^[[:space:]]*(#|$)/ { next }
        {
            separator = index($0, "=")
            if (separator == 0) next
            key = substr($0, 1, separator - 1)
            if (key != wanted) next
            count += 1
            value = substr($0, separator + 1)
        }
        END {
            if (count != 1) exit 2
            print value
        }
    ' "${COA_CONFIG_FILE}"
}

coa_validate_config_shape() {
    coa_require_secure_directory "${COA_CONFIG_ROOT}" 700 || return 1
    coa_require_secure_file "${COA_CONFIG_FILE}" 600 || return 1

    local line key value seen_keys=''
    local line_number=0
    while IFS= read -r line || [[ -n "${line}" ]]; do
        line_number=$((line_number + 1))
        if [[ "${line}" =~ ^[[:space:]]*$ || "${line}" =~ ^[[:space:]]*# ]]; then
            continue
        fi
        if [[ "${line}" == *$'\r'* || "${line}" != *=* ]]; then
            coa_error "运行配置第 ${line_number} 行格式无效"
            return 1
        fi
        key="${line%%=*}"
        value="${line#*=}"
        if [[ ! "${key}" =~ ^[A-Z][A-Z0-9_]*$ ]] \
            || ! coa_config_key_allowed "${key}"; then
            coa_error "运行配置包含未授权键：${key}"
            return 1
        fi
        if [[ -z "${value}" || "${value}" =~ [[:space:]] ]]; then
            coa_error "运行配置 ${key} 不能为空或包含空白字符"
            return 1
        fi
        case $'\n'"${seen_keys}"$'\n' in
            *$'\n'"${key}"$'\n'*)
                coa_error "运行配置键重复：${key}"
                return 1
                ;;
        esac
        seen_keys="${seen_keys}"$'\n'"${key}"
    done < "${COA_CONFIG_FILE}"

    while IFS= read -r key; do
        if ! value="$(coa_config_value "${key}")"; then
            coa_error "运行配置键必须恰好出现一次：${key}"
            return 1
        fi
        if [[ -z "${value}" ]]; then
            coa_error "运行配置键不能为空：${key}"
            return 1
        fi
    done < <(coa_required_config_keys)
}

coa_require_exact_config_value() {
    local key="$1"
    local expected="$2"
    local actual
    actual="$(coa_config_value "${key}")" || {
        coa_error "无法读取运行配置键：${key}"
        return 1
    }
    if [[ "${actual}" != "${expected}" ]]; then
        coa_error "运行配置 ${key} 必须固定为 ${expected}"
        return 1
    fi
}

coa_validate_runtime_config() {
    coa_validate_config_shape || return 1
    coa_require_exact_config_value COUNTRY_OUTAGE_AGENT_URL "${COA_EXPECTED_AGENT_URL}" || return 1
    coa_require_exact_config_value COUNTRY_OUTAGE_AGENT_IDENTITY_MODE "${COA_EXPECTED_IDENTITY_MODE}" || return 1
    coa_require_exact_config_value COUNTRY_OUTAGE_AGENT_NARRATOR "${COA_EXPECTED_NARRATOR}" || return 1
    coa_require_exact_config_value COUNTRY_OUTAGE_AGENT_HOST '127.0.0.1' || return 1
    coa_require_exact_config_value COUNTRY_OUTAGE_AGENT_PORT '28474' || return 1
    coa_require_exact_config_value COUNTRY_OUTAGE_AGENT_REPORT_CACHE_TTL_MS '3600000' || return 1
    coa_require_exact_config_value DOMEYE_API_BASE_URL "${COA_EXPECTED_API_BASE_URL}" || return 1
    coa_require_exact_config_value DOMEYE_API_TIMEOUT_MS '5000' || return 1
    coa_require_exact_config_value DOMEYE_REPORT_PYTHON_EXECUTABLE "${COA_EXPECTED_PDF_PYTHON}" || return 1
    coa_require_exact_config_value DOMEYE_REPORT_PDF_TIMEOUT_MS '45000' || return 1
    coa_require_exact_config_value COUNTRY_OUTAGE_PI_CERTIFIED_REGISTRY_PATH "${COA_EXPECTED_REGISTRY}" || return 1
    coa_require_exact_config_value COUNTRY_OUTAGE_PI_PROFILE "${COA_EXPECTED_PROFILE}" || return 1
    coa_require_exact_config_value COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY "${COA_AUDIT_FIXED_PATH}" || return 1

    local token internal_user font_path font_sha auth_path bootstrap
    token="$(coa_config_value COUNTRY_OUTAGE_AGENT_SHARED_TOKEN)"
    internal_user="$(coa_config_value COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID)"
    font_path="$(coa_config_value DOMEYE_REPORT_FONT_PATH)"
    font_sha="$(coa_config_value DOMEYE_REPORT_FONT_SHA256)"
    auth_path="$(coa_config_value COUNTRY_OUTAGE_PI_AUTH_PATH)"
    bootstrap="$(coa_config_value COUNTRY_OUTAGE_AGENT_PYTHON_BOOTSTRAP)"

    if (( ${#token} < 32 || ${#token} > 256 )) \
        || [[ ! "${token}" =~ ^[A-Za-z0-9._~-]+$ ]]; then
        coa_error 'COUNTRY_OUTAGE_AGENT_SHARED_TOKEN 必须是 32 至 256 位安全随机字符'
        return 1
    fi
    if (( ${#internal_user} < 1 || ${#internal_user} > 128 )) \
        || [[ ! "${internal_user}" =~ ^[A-Za-z0-9@._:-]+$ ]]; then
        coa_error 'COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID 格式无效'
        return 1
    fi
    if (( ${#font_sha} != 64 )) || [[ ! "${font_sha}" =~ ^[0-9a-f]+$ ]]; then
        coa_error 'DOMEYE_REPORT_FONT_SHA256 必须是小写 SHA256'
        return 1
    fi
    for path_value in "${font_path}" "${auth_path}" "${bootstrap}"; do
        if [[ "${path_value}" != /* || "${path_value}" == *'/../'* || "${path_value}" == */.. ]]; then
            coa_error "运行配置包含非规范绝对路径：${path_value}"
            return 1
        fi
    done

    coa_require_secure_file "${auth_path}" 600 || return 1
    coa_require_secure_directory "${COA_AUDIT_FIXED_PATH}" 700 || return 1
    coa_require_trusted_readonly_file "${font_path}" || return 1
    if [[ "$(coa_sha256 "${font_path}")" != "${font_sha}" ]]; then
        coa_error '中文字体 SHA256 与冻结配置不一致'
        return 1
    fi
    coa_require_trusted_executable "${bootstrap}" >/dev/null || return 1
}

coa_ensure_runtime_directories() {
    local expected_uid expected_gid
    if [[ "${COA_TEST_MODE}" == true ]]; then
        expected_uid="$(id -u)"
        expected_gid="$(id -g)"
    else
        expected_uid='0'
        expected_gid='0'
    fi
    install -d -m 0700 -o "${expected_uid}" -g "${expected_gid}" \
        "${COA_RUNTIME_ROOT}" "${COA_RELEASE_ROOT}" "${COA_STATE_ROOT}" "${COA_LOG_ROOT}"
    coa_require_secure_directory "${COA_RUNTIME_ROOT}" 700
    coa_require_secure_directory "${COA_RELEASE_ROOT}" 700
    coa_require_secure_directory "${COA_STATE_ROOT}" 700
    coa_require_secure_directory "${COA_LOG_ROOT}" 700
}

coa_require_fixed_node() {
    if [[ ! -x "${COA_NODE}" || ! -x "${COA_NPM}" ]]; then
        coa_error "缺少固定 Node.js/npm：${COA_NODE_BIN_DIR}"
        return 1
    fi
    local actual
    actual="$("${COA_NODE}" --version)"
    if [[ "${actual}" != "${COA_EXPECTED_NODE_VERSION}" ]]; then
        coa_error "Node.js 版本漂移：${actual} != ${COA_EXPECTED_NODE_VERSION}"
        return 1
    fi
}

coa_require_clean_source_checkout() {
    local expected_git_sha="$1"
    local actual_git_sha status_output
    if (( ${#expected_git_sha} != 40 )) \
        || [[ ! "${expected_git_sha}" =~ ^[0-9a-f]+$ ]]; then
        coa_error 'expected-git-sha 必须是完整 40 位小写提交 SHA'
        return 1
    fi
    if [[ ! -d "${COA_PROJECT_ROOT}/.git" ]]; then
        coa_error "项目目录不是 Git 检出：${COA_PROJECT_ROOT}"
        return 1
    fi
    actual_git_sha="$(git -C "${COA_PROJECT_ROOT}" rev-parse --verify HEAD)"
    if [[ "${actual_git_sha}" != "${expected_git_sha}" ]]; then
        coa_error "项目 HEAD 与 expected-git-sha 不一致：${actual_git_sha}"
        return 1
    fi
    status_output="$(git -C "${COA_PROJECT_ROOT}" status --porcelain --untracked-files=all)"
    if [[ -n "${status_output}" ]]; then
        coa_error '项目检出不是干净状态，拒绝组装不可变 Sidecar release'
        return 1
    fi
}

coa_require_bound_immutable_source() {
    local source_root="$1"
    local expected_git_sha="$2"
    local binding="${source_root}/COUNTRY-OUTAGE-SOURCE-BINDING.json"
    local archive="${source_root}/artifacts/domeye-country-outage-production-code-v1.tar.gz"
    local actual_root

    if (( ${#expected_git_sha} != 40 )) \
        || [[ ! "${expected_git_sha}" =~ ^[0-9a-f]+$ ]]; then
        coa_error 'expected-git-sha 必须是完整 40 位小写提交 SHA'
        return 1
    fi
    if [[ "${COA_TEST_MODE}" == true ]]; then
        case "${source_root}" in
            "${COA_RUNTIME_BASE}/releases/"*) ;;
            *)
                coa_error "测试不可变来源不在固定 release 根内：${source_root}"
                return 1
                ;;
        esac
    else
        case "${source_root}" in
            /home/bgpdata/Domeye-Core-runtime/releases/*) ;;
            *)
                coa_error "不可变来源不在生产 release 根内：${source_root}"
                return 1
                ;;
        esac
    fi
    if [[ ! -d "${source_root}" || -L "${source_root}" ]]; then
        coa_error "不可变来源不存在、不是目录或是符号链接：${source_root}"
        return 1
    fi
    actual_root="$(coa_realpath "${source_root}")" || return 1
    if [[ "${actual_root}" != "${source_root}" ]]; then
        coa_error "不可变来源路径不是规范实际路径：${source_root}"
        return 1
    fi
    coa_require_trusted_readonly_file "${binding}" || return 1
    coa_require_trusted_readonly_file "${archive}" || return 1
    if ! jq -e \
        --arg source_root "${source_root}" \
        --arg git_sha "${expected_git_sha}" \
        --arg archive_sha256 "$(coa_sha256 "${archive}")" \
        '.schema_version == "domeye_country_outage_source_binding_v1"
         and .source_root == $source_root
         and .approved_overlay_git_sha == $git_sha
         and .combined_archive.path
             == "artifacts/domeye-country-outage-production-code-v1.tar.gz"
         and .combined_archive.sha256 == $archive_sha256
         and .base.release_id == "20260728T032500Z-legacy-semantic-guardrails-prod-05"
         and .base.archive_sha256
             == "7aa5893b15a7edff8ff3d44434320c49d3cb2bed3d8ce2bb42b70b0f69a340fd"
         and .boundaries.collector == "rrc25"
         and .boundaries.country_scope == "IR"
         and .boundaries.external_evidence == "disabled"
         and .boundaries.backend_core == "preserved_14_of_14"
         and .boundaries.database_changed == false' \
        "${binding}" >/dev/null; then
        coa_error '不可变来源绑定合同、基础归档或安全边界无效'
        return 1
    fi
}

coa_release_payload_list() {
    local release_dir="$1"
    (
        cd -- "${release_dir}"
        find . -type f \
            ! -name 'RELEASE-MANIFEST.json' \
            ! -name 'SHA256SUMS' \
            -print \
            | sed 's#^\./##' \
            | LC_ALL=C sort
    )
}

coa_write_release_checksums() {
    local release_dir="$1"
    (
        cd -- "${release_dir}"
        while IFS= read -r relative; do
            sha256sum "${relative}"
        done < <(coa_release_payload_list "${release_dir}")
        sha256sum RELEASE-MANIFEST.json
    ) > "${release_dir}/SHA256SUMS"
}

coa_verify_release() {
    local release_id="$1"
    local release_dir
    release_dir="$(coa_release_dir "${release_id}")" || return 1
    if [[ ! -d "${release_dir}" || -L "${release_dir}" ]]; then
        coa_error "release 不存在、不是目录或是符号链接：${release_dir}"
        return 1
    fi
    if [[ -e "${release_dir}/.PREPARING" || -L "${release_dir}/.PREPARING" ]]; then
        coa_error "release 仍处于未完成状态：${release_dir}"
        return 1
    fi
    if find "${release_dir}" -type l -print -quit | grep -q .; then
        coa_error "不可变 release 内不得包含符号链接：${release_dir}"
        return 1
    fi
    for required in RELEASE-MANIFEST.json SHA256SUMS; do
        if [[ ! -f "${release_dir}/${required}" || -L "${release_dir}/${required}" ]]; then
            coa_error "release 缺少普通文件：${required}"
            return 1
        fi
    done
    if ! jq -e --arg release_id "${release_id}" \
        '.schema_version == 1
         and .component == "country_outage_agent_sidecar"
         and .data_profile == "feb-mar-2026"
         and .collector == "rrc25"
         and .country_scope == "IR"
         and .external_evidence == "disabled"
         and .release_id == $release_id
         and (.git_sha | test("^[0-9a-f]{40}$"))
         and .node_version == "v22.23.1"
         and .pi_version == "0.82.1"
         and .model_profile == "deepseek-v4-flash-pi-0.82.1-v1"
         and (.font_sha256 | test("^[0-9a-f]{64}$"))
         and (.hashes.pdf_runtime | test("^[0-9a-f]{64}$"))
         and (.created_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))' \
        "${release_dir}/RELEASE-MANIFEST.json" >/dev/null; then
        coa_error 'release manifest 结构或冻结身份无效'
        return 1
    fi
    (
        cd -- "${release_dir}"
        sha256sum -c SHA256SUMS >/dev/null
    ) || {
        coa_error 'release 文件 SHA256 校验失败'
        return 1
    }

    if ! diff -u \
        <(
            {
                cut -c 67- "${release_dir}/SHA256SUMS"
                printf 'SHA256SUMS\n'
            } | LC_ALL=C sort
        ) \
        <(
            cd -- "${release_dir}"
            find . -type f -print | sed 's#^\./##' | LC_ALL=C sort
        ) >/dev/null; then
        coa_error 'release 顶层/递归文件集合与 SHA256SUMS 不闭合'
        return 1
    fi

    local configured_font_sha manifest_font_sha
    configured_font_sha="$(coa_config_value DOMEYE_REPORT_FONT_SHA256)"
    manifest_font_sha="$(jq -r '.font_sha256' "${release_dir}/RELEASE-MANIFEST.json")"
    if [[ "${configured_font_sha}" != "${manifest_font_sha}" ]]; then
        coa_error 'release 绑定的中文字体 SHA256 与当前配置不一致'
        return 1
    fi
    if ! "${release_dir}/pdf-venv/bin/python" -c \
        'import pypdf, reportlab; print("pdf-runtime-ready")' >/dev/null; then
        coa_error 'release 内 PDF Python 在最终路径不可运行'
        return 1
    fi
}

coa_current_release_id() {
    if [[ ! -L "${COA_CURRENT_LINK}" ]]; then
        return 1
    fi
    local target
    target="$(coa_realpath "${COA_CURRENT_LINK}")"
    case "${target}" in
        "${COA_RELEASE_ROOT}/"*)
            ;;
        *)
            coa_error "current 指针越界：${target}"
            return 1
            ;;
    esac
    if [[ ! -d "${target}" || -L "${target}" ]]; then
        coa_error "current 指针目标不是实际 release 目录：${target}"
        return 1
    fi
    basename -- "${target}"
}

coa_atomic_switch_current() {
    local release_id="$1"
    local release_dir temporary_link
    release_dir="$(coa_release_dir "${release_id}")" || return 1
    coa_verify_release "${release_id}" || return 1
    temporary_link="${COA_RUNTIME_ROOT}/.current.${release_id}.$$"
    if [[ -e "${temporary_link}" || -L "${temporary_link}" ]]; then
        coa_error "临时 current 指针已存在：${temporary_link}"
        return 1
    fi
    if [[ -e "${COA_CURRENT_LINK}" && ! -L "${COA_CURRENT_LINK}" ]]; then
        coa_error "current 必须不存在或是受管符号链接：${COA_CURRENT_LINK}"
        return 1
    fi
    ln -s -- "${release_dir}" "${temporary_link}" || return 1
    mv -Tf -- "${temporary_link}" "${COA_CURRENT_LINK}" || return 1
    if [[ "$(coa_current_release_id)" != "${release_id}" ]]; then
        coa_error 'current 指针切换后身份复验失败'
        return 1
    fi
}

coa_atomic_write_json() {
    local target="$1"
    local temporary="${target}.tmp.$$"
    if [[ -L "${target}" || -e "${target}" && ! -f "${target}" ]]; then
        coa_error "状态目标不是普通文件：${target}"
        return 1
    fi
    umask 077
    cat > "${temporary}" || return 1
    chmod 0600 "${temporary}" || return 1
    jq -e . "${temporary}" >/dev/null || return 1
    mv -f -- "${temporary}" "${target}" || return 1
}

coa_list_sessions() {
    local output status
    if output="$(screen -ls 2>/dev/null)"; then
        status=0
    else
        status=$?
    fi
    if (( status != 0 && status != 1 )); then
        coa_error "无法读取 Sidecar Screen 列表（screen -ls=${status}）"
        return 1
    fi
    awk -v suffix=".${COA_SCREEN_NAME}" '
        $1 ~ /^[0-9]+\./ && substr($1, length($1) - length(suffix) + 1) == suffix {
            print $1
        }
    ' <<< "${output}"
}

coa_require_no_managed_sessions() {
    local sessions
    sessions="$(coa_list_sessions)" || return 1
    if [[ -n "${sessions}" ]]; then
        coa_error "受管 Sidecar Screen 尚未清空：${sessions//$'\n'/, }"
        return 1
    fi
}

coa_session_has_marker() {
    local session="$1"
    local expected_release="$2"
    local expected_config_sha="$3"
    if [[ ! "${session}" =~ ^[0-9]+\.${COA_SCREEN_NAME}$ ]]; then
        return 1
    fi
    if [[ "${COA_TEST_MODE}" == true ]]; then
        return 0
    fi
    local screen_pid="${session%%.*}"
    local -a queue children
    local pid child
    queue=("${screen_pid}")
    while (( ${#queue[@]} > 0 )); do
        pid="${queue[0]}"
        queue=("${queue[@]:1}")
        if [[ -r "/proc/${pid}/environ" ]] \
            && tr '\0' '\n' < "/proc/${pid}/environ" \
                | awk -F= \
                    -v instance='country-outage-agent-fixed-history-v1' \
                    -v release="${expected_release}" \
                    -v config_sha="${expected_config_sha}" '
                    $1 == "DOMEYE_COUNTRY_OUTAGE_AGENT_INSTANCE" && $2 == instance { a=1 }
                    $1 == "DOMEYE_COUNTRY_OUTAGE_AGENT_RELEASE_ID" && $2 == release { b=1 }
                    $1 == "DOMEYE_COUNTRY_OUTAGE_AGENT_CONFIG_SHA256" && $2 == config_sha { c=1 }
                    END { exit(a&&b&&c ? 0 : 1) }
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

coa_require_single_matching_session() {
    local release_id="$1"
    local config_sha="$2"
    local listed
    local -a sessions=()
    listed="$(coa_list_sessions)" || return 1
    if [[ -n "${listed}" ]]; then
        mapfile -t sessions <<< "${listed}"
    fi
    if (( ${#sessions[@]} != 1 )); then
        coa_error "Sidecar Screen 数量不是 1：${#sessions[@]}"
        return 1
    fi
    if ! coa_session_has_marker "${sessions[0]}" "${release_id}" "${config_sha}"; then
        coa_error 'Sidecar Screen 运行身份与 release/config 不一致'
        return 1
    fi
    printf '%s\n' "${sessions[0]}"
}

coa_stop_exact_session() {
    local session="$1"
    local release_id="$2"
    local config_sha="$3"
    if ! coa_session_has_marker "${session}" "${release_id}" "${config_sha}"; then
        coa_error "拒绝停止身份不匹配的 Screen：${session}"
        return 1
    fi
    screen -S "${session}" -X quit || return 1
    local attempt listed
    for (( attempt = 1; attempt <= 30; attempt++ )); do
        listed="$(coa_list_sessions)" || return 1
        if ! grep -Fxq "${session}" <<< "${listed}"; then
            return 0
        fi
        sleep 0.2
    done
    coa_error "Sidecar Screen 未在 6 秒内停止：${session}"
    return 1
}

coa_require_backend_health() {
    curl --config /dev/null --noproxy '*' --proto '=http' --max-redirs 0 \
        --fail --silent --show-error --max-time 5 \
        'http://127.0.0.1:28473/api/v1/healthz' >/dev/null
}

coa_require_port_free() {
    local listeners
    if ! listeners="$(ss -H -ltn 'sport = :28474')"; then
        coa_error '无法查询 127.0.0.1:28474 监听状态'
        return 1
    fi
    if grep -q . <<< "${listeners}"; then
        coa_error '127.0.0.1:28474 已被占用'
        return 1
    fi
}

coa_require_port_listening() {
    local listeners
    if ! listeners="$(ss -H -ltn 'sport = :28474')"; then
        coa_error '无法查询 127.0.0.1:28474 监听状态'
        return 1
    fi
    if ! grep -q . <<< "${listeners}"; then
        coa_error '127.0.0.1:28474 未监听'
        return 1
    fi
}

coa_acquire_lock() {
    exec 9>"${COA_LOCK_FILE}"
    if ! flock -n 9; then
        coa_error '另一个国家中断 Agent 生命周期操作正在执行'
        return 1
    fi
}

coa_verify_profile_and_exception() {
    local release_dir="$1"
    local verifier="${release_dir}/deployment/verify-formal-release.mjs"
    if [[ ! -f "${verifier}" || -L "${verifier}" ]]; then
        coa_error "release 缺少不可变正式预检器：${verifier}"
        return 1
    fi
    "${COA_NODE}" "${verifier}" \
        "${release_dir}" \
        "${COA_EXPECTED_PROFILE}"
}

coa_probe_sidecar() {
    local probe="${COA_CURRENT_LINK}/deployment/probe-sidecar.mjs"
    if [[ ! -f "${probe}" ]]; then
        coa_error 'current release 缺少 readiness 探针'
        return 1
    fi
    "${COA_NODE}" "${probe}" "${COA_CONFIG_FILE}"
}

coa_validate_runtime_paths_for_current() {
    local release_id="$1"
    local expected_release actual_python actual_registry
    expected_release="$(coa_release_dir "${release_id}")"
    actual_python="$(coa_realpath "$(coa_config_value DOMEYE_REPORT_PYTHON_EXECUTABLE)")"
    actual_registry="$(coa_realpath "$(coa_config_value COUNTRY_OUTAGE_PI_CERTIFIED_REGISTRY_PATH)")"
    if [[ "${actual_python}" != "${expected_release}/pdf-venv/bin/python" \
        || "${actual_registry}" != "${expected_release}/agent-sidecar/resources/certified-models/country-outage-pi-models-v1.json" ]]; then
        coa_error 'current 指针下的 Python 或认证注册表未解析到目标 release'
        return 1
    fi
    if [[ ! -x "${actual_python}" || ! -f "${actual_registry}" ]]; then
        coa_error '目标 release 缺少 Python 或认证注册表'
        return 1
    fi
}

coa_export_formal_environment() {
    export COUNTRY_OUTAGE_AGENT_NARRATOR="$(coa_config_value COUNTRY_OUTAGE_AGENT_NARRATOR)"
    export COUNTRY_OUTAGE_AGENT_HOST="$(coa_config_value COUNTRY_OUTAGE_AGENT_HOST)"
    export COUNTRY_OUTAGE_AGENT_PORT="$(coa_config_value COUNTRY_OUTAGE_AGENT_PORT)"
    export COUNTRY_OUTAGE_AGENT_SHARED_TOKEN="$(coa_config_value COUNTRY_OUTAGE_AGENT_SHARED_TOKEN)"
    export COUNTRY_OUTAGE_AGENT_REPORT_CACHE_TTL_MS="$(coa_config_value COUNTRY_OUTAGE_AGENT_REPORT_CACHE_TTL_MS)"
    export DOMEYE_API_BASE_URL="$(coa_config_value DOMEYE_API_BASE_URL)"
    export DOMEYE_API_TIMEOUT_MS="$(coa_config_value DOMEYE_API_TIMEOUT_MS)"
    export DOMEYE_REPORT_PYTHON_EXECUTABLE="$(coa_config_value DOMEYE_REPORT_PYTHON_EXECUTABLE)"
    export DOMEYE_REPORT_FONT_PATH="$(coa_config_value DOMEYE_REPORT_FONT_PATH)"
    export DOMEYE_REPORT_PDF_TIMEOUT_MS="$(coa_config_value DOMEYE_REPORT_PDF_TIMEOUT_MS)"
    export COUNTRY_OUTAGE_PI_CERTIFIED_REGISTRY_PATH="$(coa_config_value COUNTRY_OUTAGE_PI_CERTIFIED_REGISTRY_PATH)"
    export COUNTRY_OUTAGE_PI_PROFILE="$(coa_config_value COUNTRY_OUTAGE_PI_PROFILE)"
    export COUNTRY_OUTAGE_PI_AUTH_PATH="$(coa_config_value COUNTRY_OUTAGE_PI_AUTH_PATH)"
    export COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY="$(coa_config_value COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY)"
}

coa_iso_utc_now() {
    date -u '+%Y-%m-%dT%H:%M:%SZ'
}

coa_initialize_paths
