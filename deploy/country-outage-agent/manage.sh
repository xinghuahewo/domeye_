#!/usr/bin/env bash

set -Eeuo pipefail

readonly COA_MANAGE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/common.sh
source "${COA_MANAGE_DIR}/lib/common.sh"

coa_require_root() {
    if [[ "${COA_TEST_MODE}" != true && "${EUID}" -ne 0 ]]; then
        coa_error '国家中断 Agent 生命周期操作必须由 root 执行'
        return 1
    fi
}

coa_preflight_common() {
    coa_require_root
    for command_name in awk chmod cp date diff find flock git grep id install jq ln \
        mktemp mv python3 readlink screen sed sha256sum sleep ss stat tr unlink; do
        coa_require_command "${command_name}"
    done
    coa_ensure_runtime_directories
    coa_validate_runtime_config
    coa_require_fixed_node
}

coa_copy_release_sources() {
    local candidate="$1"
    local source_sidecar="${COA_PROJECT_ROOT}/agent-sidecar"
    local source_acceptance="${COA_PROJECT_ROOT}/config/country-outage-agent-core-acceptance-v3.json"
    local source_fact_contract="${COA_PROJECT_ROOT}/contracts/agent/country-outage-report-facts-v1.schema.json"
    local source_deployment="${COA_PROJECT_ROOT}/deploy/country-outage-agent"

    for source_path in "${source_sidecar}" "${source_deployment}"; do
        if [[ ! -d "${source_path}" || -L "${source_path}" ]]; then
            coa_error "release 来源目录无效：${source_path}"
            return 1
        fi
    done
    if [[ ! -f "${source_acceptance}" || -L "${source_acceptance}" ]]; then
        coa_error "冻结验收配置无效：${source_acceptance}"
        return 1
    fi
    if [[ ! -f "${source_fact_contract}" || -L "${source_fact_contract}" ]]; then
        coa_error "国家中断事实 schema 无效：${source_fact_contract}"
        return 1
    fi

    install -d -m 0700 \
        "${candidate}/agent-sidecar" \
        "${candidate}/config" \
        "${candidate}/contracts/agent" \
        "${candidate}/deployment"
    cp -R \
        "${source_sidecar}/package.json" \
        "${source_sidecar}/package-lock.json" \
        "${source_sidecar}/tsconfig.json" \
        "${source_sidecar}/src" \
        "${source_sidecar}/tests" \
        "${source_sidecar}/scripts" \
        "${source_sidecar}/resources" \
        "${source_sidecar}/vendor-patches" \
        "${candidate}/agent-sidecar/"
    cp -R "${source_deployment}/." "${candidate}/deployment/"
    cp "${source_acceptance}" \
        "${candidate}/config/country-outage-agent-core-acceptance-v3.json"
    cp "${source_fact_contract}" \
        "${candidate}/contracts/agent/country-outage-report-facts-v1.schema.json"
    if find "${candidate}" -type l -print -quit | grep -q .; then
        coa_error '显式复制的 release 来源包含符号链接'
        return 1
    fi
}

coa_prepare_python_runtime() {
    local candidate="$1"
    local bootstrap resolved_bootstrap
    bootstrap="$(coa_config_value COUNTRY_OUTAGE_AGENT_PYTHON_BOOTSTRAP)"
    resolved_bootstrap="$(coa_require_trusted_executable "${bootstrap}")"
    local major_minor full_version
    major_minor="$("${resolved_bootstrap}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    full_version="$("${resolved_bootstrap}" -c 'import platform; print(platform.python_version())')"
    if [[ "${major_minor}" != '3.10' ]]; then
        coa_error "PDF bootstrap Python 必须为 3.10，实际 ${major_minor}"
        return 1
    fi
    "${resolved_bootstrap}" -m venv --copies "${candidate}/pdf-venv"
    "${candidate}/pdf-venv/bin/python" -m pip \
        --disable-pip-version-check \
        install \
        --no-deps \
        --only-binary=:all: \
        --requirement "${candidate}/deployment/requirements-pdf.txt"
    "${candidate}/pdf-venv/bin/python" - <<'PY'
from importlib.metadata import version

expected = {
    "charset-normalizer": "3.4.9",
    "pillow": "12.2.0",
    "pypdf": "6.10.0",
    "reportlab": "4.4.9",
    "typing-extensions": "4.15.0",
}
actual = {name: version(name) for name in expected}
if actual != expected:
    raise SystemExit(f"PDF 依赖版本漂移：{actual!r}")
import pypdf  # noqa: F401
import reportlab  # noqa: F401
PY
    local font_path
    font_path="$(coa_config_value DOMEYE_REPORT_FONT_PATH)"
    "${candidate}/pdf-venv/bin/python" - "${font_path}" <<'PY'
import sys
from reportlab.pdfbase.ttfonts import TTFont

TTFont("DomeyeDeploymentPreflight", sys.argv[1])
PY
    if [[ -L "${candidate}/pdf-venv/lib64" \
        && "$(readlink "${candidate}/pdf-venv/lib64")" == lib ]]; then
        unlink "${candidate}/pdf-venv/lib64"
    fi
    if find "${candidate}/pdf-venv" -type l -print -quit | grep -q .; then
        coa_error 'PDF venv 含非预期符号链接'
        return 1
    fi
    jq -n \
        --arg schema_version 'country_outage_pdf_runtime_v1' \
        --arg python_path "${resolved_bootstrap}" \
        --arg python_sha256 "$(coa_sha256 "${resolved_bootstrap}")" \
        --arg python_version "${full_version}" \
        --arg requirements_sha256 "$(coa_sha256 "${candidate}/deployment/requirements-pdf.txt")" \
        '{
          schema_version: $schema_version,
          bootstrap: {
            canonical_path: $python_path,
            sha256: $python_sha256,
            version: $python_version
          },
          requirements_sha256: $requirements_sha256
        }' > "${candidate}/PDF-RUNTIME.json"
    chmod 0600 "${candidate}/PDF-RUNTIME.json"
}

coa_prepare_node_runtime() {
    local candidate="$1"
    (
        cd -- "${candidate}/agent-sidecar"
        PATH="${COA_NODE_BIN_DIR}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
            "${COA_NPM}" ci --ignore-scripts
        "${COA_NODE}" scripts/apply_pi_response_model_patch.mjs --apply
        PATH="${COA_NODE_BIN_DIR}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
            "${COA_NPM}" test
        PATH="${COA_NODE_BIN_DIR}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
            "${COA_NPM}" prune --omit=dev --ignore-scripts
        # npm prune 会重写 lockfile 的 omit 标记。运行依赖可以裁剪，但不可把工具
        # 写回结果冒充已认证源码；从只读绑定来源恢复原始锁文件后再做 21/21 复核。
        cp "${COA_PROJECT_ROOT}/agent-sidecar/package-lock.json" package-lock.json
        "${COA_NODE}" scripts/apply_pi_response_model_patch.mjs --apply
        "${COA_NODE}" scripts/apply_pi_response_model_patch.mjs --verify
    )
    while IFS= read -r bin_directory; do
        find "${bin_directory}" -depth -delete
    done < <(
        find "${candidate}/agent-sidecar/node_modules" \
            -type d -name .bin -print
    )
    if find "${candidate}" -type l -print -quit | grep -q .; then
        coa_error '依赖安装后的 release 候选包含符号链接'
        return 1
    fi
}

coa_write_manifest() {
    local candidate="$1"
    local release_id="$2"
    local git_sha="$3"
    local registry risk acceptance requirements font_sha
    registry="${candidate}/agent-sidecar/resources/certified-models/country-outage-pi-models-v1.json"
    risk="${candidate}/agent-sidecar/resources/risk-exceptions/country-outage-pi-ghsa-mh99-v99m-4gvg-v2.json"
    acceptance="${candidate}/config/country-outage-agent-core-acceptance-v3.json"
    requirements="${candidate}/deployment/requirements-pdf.txt"
    font_sha="$(coa_config_value DOMEYE_REPORT_FONT_SHA256)"
    jq -n \
        --argjson schema_version 1 \
        --arg component 'country_outage_agent_sidecar' \
        --arg release_id "${release_id}" \
        --arg created_at "$(coa_iso_utc_now)" \
        --arg git_sha "${git_sha}" \
        --arg data_profile 'feb-mar-2026' \
        --arg collector 'rrc25' \
        --arg country_scope 'IR' \
        --arg external_evidence 'disabled' \
        --arg node_version "${COA_EXPECTED_NODE_VERSION}" \
        --arg pi_version '0.82.1' \
        --arg model_profile "${COA_EXPECTED_PROFILE}" \
        --arg registry_sha256 "$(coa_sha256 "${registry}")" \
        --arg risk_exception_sha256 "$(coa_sha256 "${risk}")" \
        --arg acceptance_sha256 "$(coa_sha256 "${acceptance}")" \
        --arg pdf_requirements_sha256 "$(coa_sha256 "${requirements}")" \
        --arg pdf_runtime_sha256 "$(coa_sha256 "${candidate}/PDF-RUNTIME.json")" \
        --arg font_sha256 "${font_sha}" \
        '{
          schema_version: $schema_version,
          component: $component,
          release_id: $release_id,
          created_at: $created_at,
          git_sha: $git_sha,
          data_profile: $data_profile,
          collector: $collector,
          country_scope: $country_scope,
          external_evidence: $external_evidence,
          node_version: $node_version,
          pi_version: $pi_version,
          model_profile: $model_profile,
          hashes: {
            certified_registry: $registry_sha256,
            risk_exception: $risk_exception_sha256,
            core_acceptance: $acceptance_sha256,
            pdf_requirements: $pdf_requirements_sha256,
            pdf_runtime: $pdf_runtime_sha256
          },
          font_sha256: $font_sha256
        }' > "${candidate}/RELEASE-MANIFEST.json"
    chmod 0600 "${candidate}/RELEASE-MANIFEST.json"
}

COA_PREPARE_CANDIDATE=''
coa_cleanup_prepare_candidate() {
    if [[ -n "${COA_PREPARE_CANDIDATE}" \
        && -d "${COA_PREPARE_CANDIDATE}" \
        && ! -L "${COA_PREPARE_CANDIDATE}" ]]; then
        case "${COA_PREPARE_CANDIDATE}" in
            "${COA_RELEASE_ROOT}/.prepare-"*)
                chmod -R u+w "${COA_PREPARE_CANDIDATE}" 2>/dev/null || true
                find "${COA_PREPARE_CANDIDATE}" -depth -delete
                ;;
            *)
                coa_error '拒绝清理边界外的 prepare 候选目录'
                ;;
        esac
    fi
}

prepare_action() {
    if (( $# != 2 && $# != 3 )); then
        coa_error '用法：prepare.sh <release-id> <expected-git-sha> [bound-immutable-source-root]'
        return 2
    fi
    local release_id="$1"
    local expected_git_sha="$2"
    local immutable_source_root="${3:-}"
    local release_dir candidate
    coa_validate_release_id "${release_id}"
    coa_preflight_common
    if [[ -n "${immutable_source_root}" ]]; then
        coa_require_bound_immutable_source \
            "${immutable_source_root}" "${expected_git_sha}"
        COA_PROJECT_ROOT="${immutable_source_root}"
        export COA_PROJECT_ROOT
    else
        coa_require_clean_source_checkout "${expected_git_sha}"
    fi
    coa_acquire_lock

    release_dir="$(coa_release_dir "${release_id}")"
    if [[ -e "${release_dir}" || -L "${release_dir}" ]]; then
        coa_error "release-id 已存在，拒绝覆盖：${release_dir}"
        return 1
    fi
    candidate="$(mktemp -d "${COA_RELEASE_ROOT}/.prepare-${release_id}.XXXXXX")"
    COA_PREPARE_CANDIDATE="${candidate}"
    trap coa_cleanup_prepare_candidate EXIT
    chmod 0700 "${candidate}"
    : > "${candidate}/.PREPARING"
    chmod 0600 "${candidate}/.PREPARING"
    coa_info "开始组装 Sidecar 候选：${candidate}"

    (
        cd -- "${COA_PROJECT_ROOT}/backend"
        sha256sum -c core.sha256
    )
    python3 "${COA_PROJECT_ROOT}/.codex/hooks/country_outage_agent_review.py" \
        --profile core --stage A5

    coa_copy_release_sources "${candidate}"
    coa_prepare_node_runtime "${candidate}"
    coa_verify_profile_and_exception "${candidate}"
    coa_prepare_python_runtime "${candidate}"
    coa_write_manifest "${candidate}" "${release_id}" "${expected_git_sha}"
    rm -f -- "${candidate}/.PREPARING"
    coa_write_release_checksums "${candidate}"
    chmod -R a-w "${candidate}"
    mv -- "${candidate}" "${release_dir}"
    COA_PREPARE_CANDIDATE=''
    trap - EXIT
    if ! coa_verify_release "${release_id}"; then
        chmod -R u+w "${release_dir}" 2>/dev/null || true
        find "${release_dir}" -depth -delete
        coa_error '最终 release 复验失败，已清理精确目标且未切换 current'
        return 1
    fi
    coa_info "国家中断 Agent 不可变 release 已准备：${release_dir}"
    coa_info '未切换 current、未启动 Sidecar、未修改数据库/backend core/外部能力。'
}

coa_active_state_release() {
    if [[ ! -f "${COA_ACTIVE_STATE}" || -L "${COA_ACTIVE_STATE}" ]]; then
        return 1
    fi
    jq -er '.release_id' "${COA_ACTIVE_STATE}"
}

coa_validate_active_state() {
    local release_id="$1"
    coa_require_secure_file "${COA_ACTIVE_STATE}" 600 || return 1
    if ! jq -e --arg release_id "${release_id}" \
        '.schema_version == 1
         and .component == "country_outage_agent_sidecar"
         and .release_id == $release_id
         and (.status == "active" or .status == "stopped")
         and (.config_sha256 | test("^[0-9a-f]{64}$"))
         and (.manifest_sha256 | test("^[0-9a-f]{64}$"))
         and (.checksums_sha256 | test("^[0-9a-f]{64}$"))
         and (.log_file | type) == "string"' \
        "${COA_ACTIVE_STATE}" >/dev/null; then
        coa_error 'Sidecar active state 无效或与 release 不一致'
        return 1
    fi
}

coa_write_pending_state() {
    local release_id="$1"
    local previous_release="$2"
    local config_sha="$3"
    local log_file="$4"
    local release_dir
    release_dir="$(coa_release_dir "${release_id}")"
    jq -n \
        --argjson schema_version 1 \
        --arg component 'country_outage_agent_sidecar' \
        --arg status 'starting' \
        --arg release_id "${release_id}" \
        --arg previous_release_id "${previous_release}" \
        --arg started_at "$(coa_iso_utc_now)" \
        --arg config_sha256 "${config_sha}" \
        --arg manifest_sha256 "$(coa_sha256 "${release_dir}/RELEASE-MANIFEST.json")" \
        --arg checksums_sha256 "$(coa_sha256 "${release_dir}/SHA256SUMS")" \
        --arg log_file "${log_file}" \
        '{
          schema_version: $schema_version,
          component: $component,
          status: $status,
          release_id: $release_id,
          previous_release_id: (if $previous_release_id == "" then null else $previous_release_id end),
          started_at: $started_at,
          config_sha256: $config_sha256,
          manifest_sha256: $manifest_sha256,
          checksums_sha256: $checksums_sha256,
          log_file: $log_file
        }' | coa_atomic_write_json "${COA_ACTIVE_STATE}"
}

coa_mark_active() {
    local release_id="$1"
    local ready_record="$2"
    jq \
        --arg status 'active' \
        --arg activated_at "$(coa_iso_utc_now)" \
        --arg ready_record "${ready_record}" \
        '.status = $status
         | .activated_at = $activated_at
         | .ready_record = $ready_record' \
        "${COA_ACTIVE_STATE}" | coa_atomic_write_json "${COA_ACTIVE_STATE}"
}

coa_write_rollback_state() {
    local activated_release="$1"
    local previous_release="$2"
    if [[ -z "${previous_release}" || "${activated_release}" == "${previous_release}" ]]; then
        return 0
    fi
    jq -n \
        --argjson schema_version 1 \
        --arg component 'country_outage_agent_sidecar' \
        --arg status 'available' \
        --arg activated_release_id "${activated_release}" \
        --arg previous_release_id "${previous_release}" \
        --arg created_at "$(coa_iso_utc_now)" \
        '{
          schema_version: $schema_version,
          component: $component,
          status: $status,
          activated_release_id: $activated_release_id,
          previous_release_id: $previous_release_id,
          created_at: $created_at
        }' | coa_atomic_write_json "${COA_ROLLBACK_STATE}"
}

coa_start_screen() {
    local release_id="$1"
    local config_sha="$2"
    local log_file="$3"
    local release_dir
    release_dir="$(coa_release_dir "${release_id}")"
    umask 077
    : > "${log_file}"
    chmod 0600 "${log_file}"
    screen \
        -L \
        -Logfile "${log_file}" \
        -dmS "${COA_SCREEN_NAME}" \
        env -i \
            HOME=/root \
            USER=root \
            LOGNAME=root \
            LANG=C.UTF-8 \
            PATH="${COA_NODE_BIN_DIR}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
            DOMEYE_COUNTRY_OUTAGE_AGENT_INSTANCE=country-outage-agent-fixed-history-v1 \
            DOMEYE_COUNTRY_OUTAGE_AGENT_RELEASE_ID="${release_id}" \
            DOMEYE_COUNTRY_OUTAGE_AGENT_CONFIG_SHA256="${config_sha}" \
            /bin/bash "${release_dir}/deployment/manage.sh" \
                _serve "${release_id}" "${config_sha}"
}

coa_wait_ready() {
    local release_id="$1"
    local config_sha="$2"
    local log_file="$3"
    local attempt ready_line session
    for (( attempt = 1; attempt <= 90; attempt++ )); do
        if session="$(coa_require_single_matching_session "${release_id}" "${config_sha}" 2>/dev/null)" \
            && grep -F '"event":"country_outage_agent_sidecar_ready"' "${log_file}" >/dev/null 2>&1 \
            && coa_require_port_listening 2>/dev/null; then
            ready_line="$(
                grep -F '"event":"country_outage_agent_sidecar_ready"' "${log_file}" \
                    | tail -n 1
            )"
            if jq -e \
                --arg profile "${COA_EXPECTED_PROFILE}" \
                '.event == "country_outage_agent_sidecar_ready"
                 and .host == "127.0.0.1"
                 and .port == 28474
                 and .collector == "rrc25"
                 and .narrator == "pi-sdk-certified"
                 and .modelProfile == $profile
                 and .externalEvidence == "disabled"
                 and .externalEvidenceProvider == "disabled"' \
                <<< "${ready_line}" >/dev/null \
                && coa_probe_sidecar >/dev/null; then
                printf '%s\n' "${ready_line}"
                return 0
            fi
        fi
        sleep 1
    done
    coa_error "Sidecar 未在 90 秒内通过 ready、监听和 disabled Provider 探针：${log_file}"
    return 1
}

coa_activate_release() {
    local release_id="$1"
    local preserve_rollback="$2"
    local release_dir previous_release config_sha log_dir log_file ready_record
    release_dir="$(coa_release_dir "${release_id}")" || return 1
    coa_verify_release "${release_id}" || return 1
    coa_verify_profile_and_exception "${release_dir}" || return 1
    coa_require_port_free || return 1

    previous_release="$(coa_current_release_id 2>/dev/null || true)"
    coa_atomic_switch_current "${release_id}" || return 1
    config_sha="$(coa_sha256 "${COA_CONFIG_FILE}")" || return 1
    log_dir="${COA_LOG_ROOT}/${release_id}"
    log_file="${log_dir}/screen-$(date -u '+%Y%m%dT%H%M%SZ')-$$.log"
    if ! coa_validate_runtime_paths_for_current "${release_id}" \
        || ! install -d -m 0700 "${log_dir}" \
        || ! coa_require_secure_directory "${log_dir}" 700 \
        || [[ -e "${log_file}" || -L "${log_file}" ]] \
        || ! coa_write_pending_state "${release_id}" "${previous_release}" "${config_sha}" "${log_file}"; then
        if [[ -n "${previous_release}" ]]; then
            coa_atomic_switch_current "${previous_release}" || true
        elif [[ -L "${COA_CURRENT_LINK}" ]]; then
            unlink "${COA_CURRENT_LINK}"
        fi
        coa_error 'Sidecar 启动前状态准备失败，current 已尽力恢复'
        return 1
    fi

    if ! coa_start_screen "${release_id}" "${config_sha}" "${log_file}" \
        || ! ready_record="$(coa_wait_ready "${release_id}" "${config_sha}" "${log_file}")"; then
        local failed_listed
        local -a failed_sessions
        failed_sessions=()
        failed_listed="$(coa_list_sessions)" || return 70
        if [[ -n "${failed_listed}" ]]; then
            mapfile -t failed_sessions <<< "${failed_listed}"
        fi
        if (( ${#failed_sessions[@]} == 1 )) \
            && coa_session_has_marker "${failed_sessions[0]}" "${release_id}" "${config_sha}"; then
            if ! coa_stop_exact_session \
                "${failed_sessions[0]}" "${release_id}" "${config_sha}"; then
                coa_error '启动失败后无法确认新 Sidecar 已停止；拒绝切换 current'
                return 70
            fi
        fi
        if ! coa_require_no_managed_sessions; then
            coa_error '启动失败后受管 Sidecar Screen 未清空；拒绝切换 current'
            return 70
        fi
        if ! coa_require_port_free; then
            coa_error '启动失败后 28474 仍被占用；拒绝切换 current'
            return 70
        fi
        if [[ -n "${previous_release}" ]]; then
            if ! coa_atomic_switch_current "${previous_release}"; then
                coa_error '启动失败且无法恢复上一 current，需要人工处理'
                return 70
            fi
        else
            if [[ -L "${COA_CURRENT_LINK}" ]]; then
                unlink "${COA_CURRENT_LINK}" || return 70
            fi
        fi
        jq \
            --arg status 'start_failed' \
            --arg failed_at "$(coa_iso_utc_now)" \
            '.status = $status | .failed_at = $failed_at' \
            "${COA_ACTIVE_STATE}" | coa_atomic_write_json "${COA_ACTIVE_STATE}" || true
        return 1
    fi

    if ! coa_mark_active "${release_id}" "${ready_record}"; then
        local active_session
        active_session="$(coa_require_single_matching_session "${release_id}" "${config_sha}" 2>/dev/null || true)"
        if [[ -n "${active_session}" ]]; then
            if ! coa_stop_exact_session \
                "${active_session}" "${release_id}" "${config_sha}"; then
                coa_error 'active state 提交失败后无法确认新 Sidecar 已停止；拒绝切换 current'
                return 70
            fi
        fi
        if ! coa_require_no_managed_sessions; then
            coa_error 'active state 提交失败后受管 Sidecar Screen 未清空；拒绝切换 current'
            return 70
        fi
        if ! coa_require_port_free; then
            coa_error 'active state 提交失败后 28474 仍被占用；拒绝切换 current'
            return 70
        fi
        if [[ -n "${previous_release}" ]]; then
            if ! coa_atomic_switch_current "${previous_release}"; then
                coa_error 'active state 提交失败且无法恢复上一 current，需要人工处理'
                return 70
            fi
        elif [[ -L "${COA_CURRENT_LINK}" ]]; then
            unlink "${COA_CURRENT_LINK}" || return 70
        fi
        coa_error 'ready 后无法提交 active state，Sidecar 已停止且 current 已尽力恢复'
        return 1
    fi

    # 只有新 release 已通过 ready 且 active state 已提交后，才替换回滚指针。
    # 这样失败候选不会提前覆盖上一活动 release 原有的回滚链。
    if [[ "${preserve_rollback}" != true ]] \
        && ! coa_write_rollback_state "${release_id}" "${previous_release}"; then
        local committed_session
        committed_session="$(
            coa_require_single_matching_session \
                "${release_id}" "${config_sha}" 2>/dev/null || true
        )"
        if [[ -n "${committed_session}" ]]; then
            if ! coa_stop_exact_session \
                "${committed_session}" "${release_id}" "${config_sha}"; then
                coa_error '回滚状态提交失败后无法确认新 Sidecar 已停止；拒绝切换 current'
                return 70
            fi
        fi
        if ! coa_require_no_managed_sessions; then
            coa_error '回滚状态提交失败后受管 Sidecar Screen 未清空；拒绝切换 current'
            return 70
        fi
        if ! coa_require_port_free; then
            coa_error '回滚状态提交失败后 28474 仍被占用；拒绝切换 current'
            return 70
        fi
        if [[ -n "${previous_release}" ]]; then
            if ! coa_atomic_switch_current "${previous_release}"; then
                coa_error '无法恢复上一 current，需要人工处理'
                return 70
            fi
            if ! coa_activate_release "${previous_release}" true; then
                coa_error '上一 release 未能恢复运行，需要人工处理'
                return 70
            fi
        elif [[ -L "${COA_CURRENT_LINK}" ]]; then
            unlink "${COA_CURRENT_LINK}" || return 70
        fi
        coa_error '无法提交新回滚状态；已停止新 Sidecar 并尽力恢复上一 release'
        return 1
    fi
}

start_action() {
    if (( $# != 1 )); then
        coa_error '用法：start.sh <release-id>'
        return 2
    fi
    local release_id="$1"
    coa_validate_release_id "${release_id}"
    coa_preflight_common
    coa_acquire_lock

    local listed
    local -a sessions=()
    listed="$(coa_list_sessions)" || return 1
    if [[ -n "${listed}" ]]; then
        mapfile -t sessions <<< "${listed}"
    fi
    if (( ${#sessions[@]} > 0 )); then
        local config_sha current_release
        config_sha="$(coa_sha256 "${COA_CONFIG_FILE}")"
        current_release="$(coa_current_release_id 2>/dev/null || true)"
        if (( ${#sessions[@]} == 1 )) \
            && [[ "${current_release}" == "${release_id}" ]] \
            && coa_validate_active_state "${release_id}" \
            && [[ "$(jq -r '.status' "${COA_ACTIVE_STATE}")" == active ]] \
            && [[ "$(jq -r '.config_sha256' "${COA_ACTIVE_STATE}")" == "${config_sha}" ]] \
            && coa_session_has_marker "${sessions[0]}" "${release_id}" "${config_sha}" \
            && coa_probe_sidecar >/dev/null; then
            coa_info "Sidecar 已在运行：${sessions[0]}（${release_id}）"
            return 0
        fi
        coa_error '发现既有但身份不匹配的 Sidecar Screen，拒绝自动接管'
        return 1
    fi
    coa_activate_release "${release_id}" false || return 1
    coa_info "国家中断 Agent Sidecar 已启动：${release_id}"
}

stop_action() {
    if (( $# != 0 )); then
        coa_error '用法：stop.sh'
        return 2
    fi
    coa_require_root
    for command_name in awk flock grep jq screen sha256sum sleep stat tr; do
        coa_require_command "${command_name}"
    done
    coa_ensure_runtime_directories
    coa_acquire_lock

    local release_id config_sha session
    release_id="$(coa_current_release_id)" || {
        coa_error '没有活动 current 指针'
        return 1
    }
    coa_validate_active_state "${release_id}"
    config_sha="$(jq -r '.config_sha256' "${COA_ACTIVE_STATE}")"
    session="$(coa_require_single_matching_session "${release_id}" "${config_sha}")"
    coa_stop_exact_session "${session}" "${release_id}" "${config_sha}"
    jq \
        --arg status 'stopped' \
        --arg stopped_at "$(coa_iso_utc_now)" \
        '.status = $status | .stopped_at = $stopped_at' \
        "${COA_ACTIVE_STATE}" | coa_atomic_write_json "${COA_ACTIVE_STATE}"
    coa_info "国家中断 Agent Sidecar 已停止：${release_id}；current 与 release 均保留。"
}

status_action() {
    if (( $# != 0 )); then
        coa_error '用法：status.sh'
        return 2
    fi
    coa_require_root
    for command_name in awk grep jq screen sha256sum ss stat tr; do
        coa_require_command "${command_name}"
    done
    coa_validate_runtime_config
    coa_require_fixed_node

    local release_id config_sha state_config_sha session release_dir
    release_id="$(coa_current_release_id)" || {
        coa_error 'current 指针不存在或无效'
        return 1
    }
    release_dir="$(coa_release_dir "${release_id}")"
    coa_verify_release "${release_id}"
    coa_verify_profile_and_exception "${release_dir}" >/dev/null
    coa_validate_active_state "${release_id}"
    config_sha="$(coa_sha256 "${COA_CONFIG_FILE}")"
    state_config_sha="$(jq -r '.config_sha256' "${COA_ACTIVE_STATE}")"
    if [[ "${config_sha}" != "${state_config_sha}" ]]; then
        coa_error '运行配置在 Sidecar 启动后发生变化，必须停止并显式重启'
        return 1
    fi
    session="$(coa_require_single_matching_session "${release_id}" "${config_sha}")"
    coa_require_port_listening
    coa_probe_sidecar
    coa_info "Sidecar Screen：${session}"
    coa_info "活动 release：${release_id}"
    coa_info '数据档：feb-mar-2026；collector：rrc25；国家 scope：IR'
    coa_info '外部证据：disabled / not_configured'
}

rollback_action() {
    if (( $# != 1 )); then
        coa_error '用法：CONFIRM_RELEASE_ID=<当前 release-id> rollback.sh <当前 release-id>'
        return 2
    fi
    local current_release="$1"
    coa_validate_release_id "${current_release}"
    if [[ "${CONFIRM_RELEASE_ID:-}" != "${current_release}" ]]; then
        coa_error 'CONFIRM_RELEASE_ID 必须与当前 release-id 完全一致'
        return 2
    fi
    coa_preflight_common
    coa_acquire_lock
    if [[ "$(coa_current_release_id)" != "${current_release}" ]]; then
        coa_error 'current 指针与待回滚 release-id 不一致'
        return 1
    fi
    coa_validate_active_state "${current_release}"
    coa_require_secure_file "${COA_ROLLBACK_STATE}" 600
    local previous_release
    previous_release="$(jq -er \
        --arg current "${current_release}" \
        'select(.schema_version == 1
          and .component == "country_outage_agent_sidecar"
          and .status == "available"
          and .activated_release_id == $current)
         | .previous_release_id' \
        "${COA_ROLLBACK_STATE}")" || {
        coa_error '没有与当前 release 匹配的可用回滚状态'
        return 1
    }
    coa_validate_release_id "${previous_release}"
    coa_verify_release "${previous_release}"

    local current_config_sha session
    current_config_sha="$(jq -r '.config_sha256' "${COA_ACTIVE_STATE}")"
    local listed
    local -a sessions=()
    listed="$(coa_list_sessions)" || return 1
    if [[ -n "${listed}" ]]; then
        mapfile -t sessions <<< "${listed}"
    fi
    if (( ${#sessions[@]} == 1 )); then
        session="${sessions[0]}"
        coa_stop_exact_session "${session}" "${current_release}" "${current_config_sha}"
    elif (( ${#sessions[@]} > 1 )); then
        coa_error '发现多个 Sidecar Screen，拒绝不确定回滚'
        return 1
    else
        coa_info '当前 Sidecar 已无 Screen 进程，继续按已验证 current/active state 回滚。'
    fi

    if ! coa_activate_release "${previous_release}" true; then
        coa_error '上一 release 启动失败；尝试恢复回滚前 release'
        coa_atomic_switch_current "${current_release}" || true
        coa_activate_release "${current_release}" true || true
        jq \
            --arg status 'rollback_failed' \
            --arg failed_at "$(coa_iso_utc_now)" \
            '.status = $status | .failed_at = $failed_at' \
            "${COA_ROLLBACK_STATE}" | coa_atomic_write_json "${COA_ROLLBACK_STATE}" || true
        return 1
    fi
    jq \
        --arg status 'consumed' \
        --arg rolled_back_at "$(coa_iso_utc_now)" \
        '.status = $status | .rolled_back_at = $rolled_back_at' \
        "${COA_ROLLBACK_STATE}" | coa_atomic_write_json "${COA_ROLLBACK_STATE}"
    coa_info "国家中断 Agent 已回滚：${current_release} -> ${previous_release}"
    coa_info '未删除任何 release，未修改数据库、backend/core 或外部能力。'
}

serve_action() {
    if (( $# != 2 )); then
        coa_error '_serve 参数无效'
        return 2
    fi
    local release_id="$1"
    local expected_config_sha="$2"
    coa_validate_release_id "${release_id}"
    if (( ${#expected_config_sha} != 64 )) \
        || [[ ! "${expected_config_sha}" =~ ^[0-9a-f]+$ ]]; then
        coa_error '_serve config SHA256 无效'
        return 2
    fi
    if [[ "$(coa_current_release_id)" != "${release_id}" ]]; then
        coa_error '_serve current 指针与 release 不一致'
        return 1
    fi
    coa_validate_runtime_config
    if [[ "$(coa_sha256 "${COA_CONFIG_FILE}")" != "${expected_config_sha}" ]]; then
        coa_error '_serve 运行配置 SHA256 已漂移'
        return 1
    fi
    coa_require_fixed_node
    coa_verify_release "${release_id}"
    coa_verify_profile_and_exception "$(coa_release_dir "${release_id}")"
    coa_validate_runtime_paths_for_current "${release_id}"
    coa_export_formal_environment
    cd -- "${COA_CURRENT_LINK}/agent-sidecar"
    exec "${COA_NODE}" dist/src/cli/serve-formal.js
}

test_validate_config_action() {
    coa_validate_runtime_config
    coa_info 'fixture config validation passed'
}

test_verify_release_action() {
    if (( $# != 1 )); then
        return 2
    fi
    coa_ensure_runtime_directories
    coa_validate_runtime_config
    coa_verify_release "$1"
}

main() {
    if (( $# < 1 )); then
        coa_error '用法：manage.sh <prepare|start|stop|status|rollback> ...'
        return 2
    fi
    local action="$1"
    shift
    case "${action}" in
        prepare) prepare_action "$@" ;;
        start) start_action "$@" ;;
        stop) stop_action "$@" ;;
        status) status_action "$@" ;;
        rollback) rollback_action "$@" ;;
        _serve) serve_action "$@" ;;
        _test_validate_config) test_validate_config_action "$@" ;;
        _test_verify_release) test_verify_release_action "$@" ;;
        *)
            coa_error "未知操作：${action}"
            return 2
            ;;
    esac
}

main "$@"
