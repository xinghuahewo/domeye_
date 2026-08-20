#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly DEFAULT_RUNTIME_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
readonly RUNTIME_ROOT="${DOMEYE_COUNTRY_OUTAGE_RUNTIME_ROOT_OVERRIDE:-${DEFAULT_RUNTIME_ROOT}}"
readonly MODE="${DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE:-production}"
readonly DATABASE_CONFIG='/home/bgpdata/Domeye-Core-data/config/database.env'
readonly DATABASE_STATE='/home/bgpdata/Domeye-Core-dev-data/state.json'
readonly AGENT_CONFIG='/home/bgpdata/Domeye-Core-runtime/config/country-outage-agent.env'
readonly INTERACTIVE_AGENT_CONFIG='/home/bgpdata/Domeye-Core-runtime/config/country-outage-interactive-agent.env'
readonly INTERACTIVE_AGENT_RUNTIME_ROOT='/home/bgpdata/Domeye-Core-runtime/country-outage-interactive-agent'
readonly INTERACTIVE_AGENT_RELEASE_ROOT="${INTERACTIVE_AGENT_RUNTIME_ROOT}/releases"
readonly INTERACTIVE_AGENT_CURRENT="${INTERACTIVE_AGENT_RUNTIME_ROOT}/current"
readonly INTERACTIVE_AGENT_ACTIVE="${INTERACTIVE_AGENT_RUNTIME_ROOT}/state/active.json"
readonly INTERACTIVE_AGENT_MANAGER="${RUNTIME_ROOT}/deploy/country-outage-agent/p1-chat/manage.sh"
readonly FIRST_SLICE_CANDIDATE="${RUNTIME_ROOT}/contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json"
readonly INFO_DIR='/home/bgpdata/Domeye-Core-dev-data/api/info'
readonly P0_DATA_DIR='/home/bgpdata/Domeye-Core-artifacts/releases/20260720T160000Z-p0-legacy/data-quality/api-candidate'
readonly RUNTIME_PATH='/home/bgpdata/.local/node-v22.23.1-linux-x64/bin:/home/bgpdata/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
INTERACTIVE_AGENT_STATUS_JSON='{}'

error() {
    printf '国家中断通用观测运行时错误：%s\n' "$*" >&2
}

sha256_file() {
    sha256sum -- "$1" | awk '{print $1}'
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

verify_interactive_agent_binding() {
    local binding="${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json"
    local unified_release_id unified_candidate ia_binding source_authority
    if ! unified_release_id="$(jq -er '.unified_candidate.release_id' \
        "${binding}")" \
        || ! unified_candidate="$(jq -er '.unified_candidate.manifest_path' \
            "${binding}")" \
        || ! ia_binding="$(jq -ce '.interactive_agent' "${binding}")" \
        || ! source_authority="$(jq -ce '.source_authority' "${binding}")"; then
        error '无法读取 Backend 的统一 Candidate/Interactive Agent 绑定'
        return 1
    fi
    [[ "${unified_release_id}" =~ ^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9._-]{0,47}$ \
        && "${unified_candidate}" == "/home/bgpdata/Domeye-Core-runtime/unified-releases/${unified_release_id}/CANDIDATE-MANIFEST.json" \
        && -f "${unified_candidate}" && ! -L "${unified_candidate}" ]] || {
        error '统一 Candidate 路径或 release-id 无效'
        return 1
    }

    local backend_binding_sha backend_sums_sha
    if ! backend_binding_sha="$(sha256_file "${binding}")" \
        || ! backend_sums_sha="$(sha256_file "${RUNTIME_ROOT}/SHA256SUMS")"; then
        error '无法计算 Backend 绑定或全制品摘要'
        return 1
    fi
    if ! jq -e \
        --arg release_id "${unified_release_id}" \
        --arg runtime_root "${RUNTIME_ROOT}" \
        --arg binding_sha "${backend_binding_sha}" \
        --arg sums_sha "${backend_sums_sha}" \
        --argjson interactive_agent "${ia_binding}" \
        --argjson source_authority "${source_authority}" '
          .schema_version == "domeye_country_outage_general_release_candidate_v2"
          and .release_id == $release_id
          and .status == "built"
          and .source.annotated_tag == $release_id
          and .source.authority == $source_authority
          and .source.commit == $source_authority.commit
          and .source.annotated_tag == $source_authority.annotated_tag
          and .source.archive_sha256 == $source_authority.archive_sha256
          and .source.authority.mode == "interactive_agent_release"
          and .source.authority.release_id == $release_id
          and .source.authority.equality_verified == true
          and .components.backend.path == $runtime_root
          and .components.backend.binding_sha256 == $binding_sha
          and .components.backend.sha256sums_sha256 == $sums_sha
          and .interactive_agent == $interactive_agent
          and .interactive_agent.release_id == $release_id
          and .cutover_baseline.purpose == "pre_cutover_identity_and_stop_only"
          and .cutover_baseline.restorable == false
          and .build_boundaries.model_calls_during_prepare == 0
          and .rollback == {mode:"fail_closed",previous_release_id:null}
        ' "${unified_candidate}" >/dev/null; then
        error '统一 Candidate 未精确绑定 Backend/Source/Interactive Agent 或失败关闭合同'
        return 1
    fi

    local source_archive source_archive_sha
    if ! source_archive="$(jq -er '.source.archive_path' \
        "${unified_candidate}")" \
        || ! source_archive_sha="$(sha256_file "${source_archive}")"; then
        error '无法读取或计算统一 Candidate Source 归档摘要'
        return 1
    fi
    [[ -f "${source_archive}" && ! -L "${source_archive}" \
        && "sha256:${source_archive_sha}" \
            == "$(jq -er '.source.archive_sha256' "${unified_candidate}")" ]] || {
        error '统一 Candidate Source 归档摘要漂移'
        return 1
    }

    local ia_release_id ia_path ia_release_manifest ia_release_sha
    local ia_active ia_active_sha ia_candidate_id ia_candidate ia_candidate_sha
    local ia_acceptance ia_acceptance_id ia_acceptance_sha
    local ia_acceptance_replay ia_acceptance_replay_sha
    local ia_release_schema ia_readiness_schema ia_readiness_sha ia_url
    local ia_attempt_limit ia_cost_policy
    if ! ia_release_id="$(jq -er '.interactive_agent.release_id' "${binding}")" \
        || ! ia_path="$(jq -er '.interactive_agent.path' "${binding}")" \
        || ! ia_release_manifest="$(jq -er \
            '.interactive_agent.release_manifest_path' "${binding}")" \
        || ! ia_release_sha="$(jq -er \
            '.interactive_agent.release_manifest_sha256' "${binding}")" \
        || ! ia_active="$(jq -er \
            '.interactive_agent.active_state_path' "${binding}")" \
        || ! ia_active_sha="$(jq -er \
            '.interactive_agent.active_state_sha256' "${binding}")" \
        || ! ia_candidate_id="$(jq -er \
            '.interactive_agent.candidate_id' "${binding}")" \
        || ! ia_candidate="$(jq -er \
            '.interactive_agent.candidate_manifest_path' "${binding}")" \
        || ! ia_candidate_sha="$(jq -er \
            '.interactive_agent.candidate_manifest_sha256' "${binding}")" \
        || ! ia_acceptance="$(jq -er \
            '.interactive_agent.acceptance_record_path' "${binding}")" \
        || ! ia_acceptance_id="$(jq -er \
            '.interactive_agent.acceptance_record_id' "${binding}")" \
        || ! ia_acceptance_sha="$(jq -er \
            '.interactive_agent.acceptance_record_sha256' "${binding}")" \
        || ! ia_acceptance_replay="$(jq -er \
            '.interactive_agent.acceptance_replay_receipt_path' "${binding}")" \
        || ! ia_acceptance_replay_sha="$(jq -er \
            '.interactive_agent.acceptance_replay_receipt_sha256' "${binding}")" \
        || ! ia_release_schema="$(jq -er \
            '.interactive_agent.release_manifest_schema_version' "${binding}")" \
        || ! ia_readiness_schema="$(jq -er \
            '.interactive_agent.readiness_schema_version' "${binding}")" \
        || ! ia_readiness_sha="$(jq -er \
            '.interactive_agent.readiness_identity_sha256' "${binding}")" \
        || ! ia_url="$(jq -er '.interactive_agent.endpoint.url' \
            "${binding}")" \
        || ! ia_attempt_limit="$(jq -er \
            '.interactive_agent.interactive_answer_attempt_limit' \
            "${binding}")" \
        || ! ia_cost_policy="$(jq -er \
            '.interactive_agent.cost_policy' "${binding}")"; then
        error 'Interactive Agent 绑定字段不完整'
        return 1
    fi
    [[ "${ia_release_id}" == "${unified_release_id}" \
        && "${ia_path}" == "${INTERACTIVE_AGENT_RELEASE_ROOT}/${ia_release_id}" \
        && "${ia_release_manifest}" == "${ia_path}/RELEASE-MANIFEST.json" \
        && "${ia_active}" == "${INTERACTIVE_AGENT_ACTIVE}" \
        && "${ia_candidate}" == "${ia_path}/project/contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json" \
        && "${ia_acceptance}" \
            == "${ia_path}/project/evaluation/country-outage/first-vertical-slice/runs/"*/acceptance-record-final.json \
        && "${ia_acceptance_replay}" \
            == "${ia_path}/deployment/ACCEPTANCE-REPLAY.json" \
        && "${ia_release_schema}" == 'domeye_interactive_agent_release_manifest_v2' \
        && "${ia_readiness_schema}" == 'domeye_interactive_agent_release_probe_v2' \
        && "${ia_url}" == 'http://127.0.0.1:28476' \
        && "${ia_attempt_limit}" == '10' \
        && "${ia_cost_policy}" == 'audit_only' ]] || {
        error 'Interactive Agent release/路径/端点绑定漂移'
        return 1
    }
    local file
    for file in \
        "${ia_release_manifest}" \
        "${ia_active}" \
        "${ia_candidate}" \
        "${ia_acceptance}" \
        "${ia_acceptance_replay}" \
        "${FIRST_SLICE_CANDIDATE}"; do
        [[ -f "${file}" && ! -L "${file}" ]] || {
            error "Interactive Agent 绑定文件不是普通文件：${file}"
            return 1
        }
    done
    [[ -L "${INTERACTIVE_AGENT_CURRENT}" \
        && "$(readlink -f -- "${INTERACTIVE_AGENT_CURRENT}")" == "${ia_path}" ]] || {
        error 'Interactive Agent current 未指向绑定 release'
        return 1
    }

    local actual_release_sha actual_active_sha actual_candidate_sha
    local actual_acceptance_sha actual_acceptance_replay_sha
    if ! actual_release_sha="$(sha256_file "${ia_release_manifest}")" \
        || ! actual_active_sha="$(sha256_file "${ia_active}")" \
        || ! actual_candidate_sha="$(sha256_file "${ia_candidate}")" \
        || ! actual_acceptance_sha="$(sha256_file "${ia_acceptance}")" \
        || ! actual_acceptance_replay_sha="$(sha256_file \
            "${ia_acceptance_replay}")"; then
        error '无法计算 Interactive Agent Candidate/Acceptance 绑定摘要'
        return 1
    fi
    [[ "sha256:${actual_release_sha}" == "${ia_release_sha}" \
        && "sha256:${actual_active_sha}" == "${ia_active_sha}" \
        && "sha256:${actual_candidate_sha}" == "${ia_candidate_sha}" \
        && "sha256:${actual_acceptance_sha}" == "${ia_acceptance_sha}" \
        && "sha256:${actual_acceptance_replay_sha}" \
            == "${ia_acceptance_replay_sha}" ]] || {
        error 'Interactive Agent release/active/Candidate/Acceptance 摘要漂移'
        return 1
    }
    if ! cmp -s "${FIRST_SLICE_CANDIDATE}" "${ia_candidate}"; then
        error 'Backend Source Candidate 与已部署 Interactive Agent Candidate 不一致'
        return 1
    fi
    if ! jq -e \
        --arg candidate_id "${ia_candidate_id}" \
        --argjson attempt_limit "${ia_attempt_limit}" \
        --arg cost_policy "${ia_cost_policy}" '
          .candidate_id == $candidate_id
          and .payload.schema_version == "domeye_first_slice_candidate_manifest_v2"
          and .payload.budget_policy.model_api_attempt_limit == $attempt_limit
          and .payload.budget_policy.cost_policy == $cost_policy
          and .payload.budget_policy.monetary_limit_usd == null
        ' "${ia_candidate}" >/dev/null; then
        error 'Interactive Agent 尝试次数或仅审计费用策略漂移'
        return 1
    fi
    if ! jq -e \
        --arg candidate_id "${ia_candidate_id}" \
        --arg acceptance_id "${ia_acceptance_id}" '
          .schema_version == "domeye_first_slice_acceptance_record_v2"
          and .candidate_id == $candidate_id
          and .acceptance_record_id == $acceptance_id
          and .evaluation_phase == "formal"
          and .acceptance_state == "accepted"
          and .dg1_decision == "GO"
        ' "${ia_acceptance}" >/dev/null; then
        error 'Interactive Agent Acceptance Record 外部批准身份漂移'
        return 1
    fi
    local source_commit source_tag source_archive_bound
    if ! source_commit="$(jq -er '.source_commit' "${binding}")" \
        || ! source_tag="$(jq -er '.source_tag' "${binding}")" \
        || ! source_archive_bound="$(jq -er \
            '.source_archive_sha256' "${binding}")"; then
        error '无法读取 General Source 权威等式'
        return 1
    fi
    [[ "${source_tag}" == "${unified_release_id}" \
        && "${unified_release_id}" == "${ia_release_id}" ]] || {
        error '统一 release-id、annotated tag 与 Interactive Agent release-id 不相等'
        return 1
    }
    if ! jq -e \
        --arg release_id "${ia_release_id}" \
        --arg release_sha "${ia_release_sha}" \
        --arg candidate_id "${ia_candidate_id}" \
        --arg candidate_sha "${ia_candidate_sha}" \
        --arg candidate_path "project/contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json" \
        --arg acceptance_path "${ia_acceptance#${ia_path}/}" \
        --arg acceptance_id "${ia_acceptance_id}" \
        --arg acceptance_sha "${ia_acceptance_sha}" \
        --arg replay_path "${ia_acceptance_replay#${ia_path}/}" \
        --arg replay_sha "${ia_acceptance_replay_sha}" \
        --arg source_commit "${source_commit}" \
        --arg source_tag "${source_tag}" \
        --arg source_archive_sha "${source_archive_bound}" '
          .schema_version == "domeye_interactive_agent_release_manifest_v2"
          and .release_id == $release_id
          and .source.commit == $source_commit
          and .source.annotated_tag == $source_tag
          and .source.archive_sha256 == $source_archive_sha
          and .candidate.manifest_path == $candidate_path
          and .candidate.candidate_id == $candidate_id
          and .candidate.manifest_sha256 == $candidate_sha
          and .candidate.schema_version == "domeye_first_slice_candidate_manifest_v2"
          and .candidate.activation_scope == "local_evaluation_only"
          and .candidate.production_deployed == false
          and .acceptance.record_path == $acceptance_path
          and .acceptance.record_id == $acceptance_id
          and .acceptance.record_sha256 == $acceptance_sha
          and .acceptance.evaluation_phase == "formal"
          and .acceptance.acceptance_state == "accepted"
          and .acceptance.dg1_decision == "GO"
          and .acceptance.replay_receipt_path == $replay_path
          and .acceptance.replay_receipt_sha256 == $replay_sha
          and .runtime.host == "127.0.0.1"
          and .runtime.port == 28476
          and .runtime.base_path == "/country-outage/chat"
          and .live_verification.public_backend_origin == "http://127.0.0.1:28471"
          and .live_verification.backend_base_path == "/api/v2/country-outage/chat"
          and .live_verification.internal_sidecar_origin == "http://127.0.0.1:28476"
          and .live_verification.internal_record_base_path == "/country-outage/chat/internal"
        ' "${ia_release_manifest}" >/dev/null; then
        error 'Interactive Agent RELEASE-MANIFEST 与 General Source 等式漂移'
        return 1
    fi
    if ! jq -e \
        --arg release_id "${ia_release_id}" \
        --arg release_sha "${ia_release_sha}" \
        --arg candidate_id "${ia_candidate_id}" '
          .schema_version == "domeye_interactive_agent_active_v1"
          and .deployment_state == "deployed"
          and .release_id == $release_id
          and .release_manifest_sha256 == $release_sha
          and .candidate_id == $candidate_id
          and .runtime.host == "127.0.0.1"
          and .runtime.port == 28476
          and .runtime.base_path == "/country-outage/chat"
        ' "${ia_active}" >/dev/null; then
        error 'Interactive Agent active.json 身份漂移'
        return 1
    fi

    [[ -x "${INTERACTIVE_AGENT_MANAGER}" && ! -L "${INTERACTIVE_AGENT_MANAGER}" ]] || {
        error 'Backend release 内新 Interactive Agent manager 无效'
        return 1
    }
    local immutable_manager="${ia_path}/project/deploy/country-outage-agent/p1-chat/manage.sh"
    [[ -f "${immutable_manager}" && ! -L "${immutable_manager}" ]] || {
        error 'Interactive Agent release 缺少不可变 manager 源文件'
        return 1
    }
    if ! cmp -s "${INTERACTIVE_AGENT_MANAGER}" "${immutable_manager}"; then
        error 'Backend release manager 与 Interactive Agent 不可变 Source 不一致'
        return 1
    fi
    if ! "${INTERACTIVE_AGENT_MANAGER}" verify-release \
        "${ia_release_id}" >/dev/null; then
        error 'Interactive Agent release 未通过绑定 manager 校验'
        return 1
    fi
    local status readiness_identity actual_readiness_sha
    if ! status="$("${INTERACTIVE_AGENT_MANAGER}" status)"; then
        error 'Interactive Agent 未通过绑定 manager status 组合校验'
        return 1
    fi
    if ! jq -e \
        --arg release_id "${ia_release_id}" \
        --arg release_sha "${ia_release_sha}" \
        --arg candidate_id "${ia_candidate_id}" '
          .schema_version == "domeye_interactive_agent_release_probe_v2"
          and .ready == true
          and .component == "domeye_interactive_agent_sidecar"
          and (.lifecycle_state == "deployed" or .lifecycle_state == "verified")
          and .release_id == $release_id
          and .release_manifest_sha256 == $release_sha
          and .candidate_id == $candidate_id
          and .candidate_activation_scope == "local_evaluation_only"
          and .candidate_production_deployed == false
          and .current_target_matches == true
          and .deployment_active == true
          and (
            (.lifecycle_state == "deployed"
             and .promotion_state == "absent"
             and .production_verified == false)
            or
            (.lifecycle_state == "verified"
             and .promotion_state == "verified"
             and .production_verified == true)
          )
        ' <<<"${status}" >/dev/null; then
        error 'Interactive Agent manager status 不代表 deployed/verified 身份闭包'
        return 1
    fi
    if ! readiness_identity="$(jq -cS '{
      schema_version,ready,component,release_id,release_manifest_sha256,
      candidate_id,candidate_activation_scope,candidate_production_deployed
    }' <<<"${status}")" \
        || ! actual_readiness_sha="$(printf '%s' "${readiness_identity}" \
            | sha256sum | awk '{print $1}')"; then
        error '无法重算 Interactive Agent readiness 身份摘要'
        return 1
    fi
    [[ "sha256:${actual_readiness_sha}" == "${ia_readiness_sha}" ]] || {
        error 'Interactive Agent readiness 身份摘要漂移'
        return 1
    }
    INTERACTIVE_AGENT_STATUS_JSON="${status}"
}

validate_runtime() {
    if ! validate_runtime_root; then
        return 1
    fi
    for command_name in awk cmp curl jq pgrep readlink screen sha256sum ss stat tr; do
        command -v "${command_name}" >/dev/null 2>&1 || {
            error "缺少命令：${command_name}"
            return 1
        }
    done
    for file in \
        "${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json" \
        "${RUNTIME_ROOT}/SHA256SUMS" \
        "${RUNTIME_ROOT}/backend/core.sha256" \
        "${RUNTIME_ROOT}/backend/run.py" \
        "${FIRST_SLICE_CANDIDATE}" \
        "${RUNTIME_ROOT}/data-layer/PRODUCTION-SELECTION.json" \
        "${RUNTIME_ROOT}/country-outage-registry.json" \
        "${DATABASE_STATE}"; do
        [[ -f "${file}" && ! -L "${file}" ]] || {
            error "运行时缺少普通文件：${file}"
            return 1
        }
    done
    [[ -x "${RUNTIME_ROOT}/venv/bin/python" ]] || {
        error '运行时 Python 不可执行'
        return 1
    }
    if ! (
        cd -- "${RUNTIME_ROOT}"
        sha256sum -c SHA256SUMS >/dev/null
    ); then
        error 'Backend release 全制品摘要不一致'
        return 1
    fi
    if ! jq -e --arg runtime_root "${RUNTIME_ROOT}" \
        '(.release_id | type == "string" and endswith("-backend"))
         and (.source_commit | test("^[0-9a-f]{40}$"))
         and (.source_tag | type == "string" and length > 0)
         and (.source_archive_sha256 | test("^sha256:[a-f0-9]{64}$"))
         and .schema_version == "domeye_country_outage_general_backend_binding_v2"
         and .runtime_root == $runtime_root
         and .unified_candidate.release_id == .source_tag
         and .source_authority == {
           mode:"interactive_agent_release",
           release_id:.source_tag,
           commit:.source_commit,
           annotated_tag:.source_tag,
           archive_sha256:.source_archive_sha256,
           equality_verified:true
         }
         and .interactive_agent.release_id == .source_tag
         and .interactive_agent.release_manifest_schema_version == "domeye_interactive_agent_release_manifest_v2"
         and .interactive_agent.readiness_schema_version == "domeye_interactive_agent_release_probe_v2"
         and (.interactive_agent.candidate_id | test("^manifest:sha256:[a-f0-9]{64}$"))
         and (.interactive_agent.candidate_manifest_path | endswith("/project/contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json"))
         and (.interactive_agent.candidate_manifest_sha256 | test("^sha256:[a-f0-9]{64}$"))
         and (.interactive_agent.acceptance_record_id | test("^acceptance-record-sha256:[a-f0-9]{64}$"))
         and (.interactive_agent.acceptance_record_sha256 | test("^sha256:[a-f0-9]{64}$"))
         and (.interactive_agent.acceptance_replay_receipt_sha256 | test("^sha256:[a-f0-9]{64}$"))
         and .interactive_agent.interactive_answer_attempt_limit == 10
         and .interactive_agent.cost_policy == "audit_only"
         and .interactive_agent.endpoint == {url:"http://127.0.0.1:28476",host:"127.0.0.1",port:28476,base_path:"/country-outage/chat"}
         and .interactive_agent.activation_scope == "local_evaluation_only"
         and .interactive_agent.candidate_production_deployed == false
         and .boundaries.collector == "rrc25"
         and .boundaries.database_changed == false
         and .boundaries.nginx_changed == false
         and .boundaries.interactive_agent_bound == true
         and .boundaries.model_calls_during_prepare == 0
         and .boundaries.window_start_utc == "2026-02-24T00:00:00Z"
         and .boundaries.window_end_exclusive_utc == "2026-03-11T00:00:00Z"' \
        "${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json" >/dev/null; then
        error 'Backend 来源绑定无效'
        return 1
    fi
    local database_state_sha bound_database_state_sha
    if ! database_state_sha="$(sha256_file "${DATABASE_STATE}")" \
        || ! bound_database_state_sha="$(jq -er '.database_state_sha256' \
            "${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json")"; then
        error '无法计算或读取固定数据库状态摘要'
        return 1
    fi
    [[ "${database_state_sha}" == "${bound_database_state_sha}" ]] || {
        error '固定数据库状态摘要与 Backend 绑定不一致'
        return 1
    }
    if ! jq -e '
        .schema_version == 2
        and .phase == "verified"
        and .port == 31627
        and .data_start == "2026-02-01 00:00:00"
        and .data_end_exclusive == "2026-04-01 00:00:00"
    ' "${DATABASE_STATE}" >/dev/null; then
        error '固定数据库状态不是已验真的二三月只读档'
        return 1
    fi
    if ! (
        cd -- "${RUNTIME_ROOT}/backend"
        sha256sum -c core.sha256 >/dev/null
    ); then
        error '冻结 Core 摘要不一致'
        return 1
    fi
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
    if ! require_secure_config "${DATABASE_CONFIG}" \
        || ! require_secure_config "${AGENT_CONFIG}" \
        || ! require_secure_config "${INTERACTIVE_AGENT_CONFIG}"; then
        return 1
    fi
    local report_agent_url interactive_agent_url
    if ! report_agent_url="$(read_config_value \
        "${AGENT_CONFIG}" COUNTRY_OUTAGE_AGENT_URL)" \
        || ! interactive_agent_url="$(read_config_value \
            "${INTERACTIVE_AGENT_CONFIG}" COUNTRY_OUTAGE_AGENT_URL)"; then
        return 1
    fi
    [[ "${report_agent_url}" == 'http://127.0.0.1:28474' ]] || {
        error '报告 Agent 仅允许保留在固定 127.0.0.1:28474，不得成为聊天回退'
        return 1
    }
    [[ "${interactive_agent_url}" == 'http://127.0.0.1:28476' ]] || {
        error 'Interactive Agent Sidecar URL 必须固定为 127.0.0.1:28476'
        return 1
    }
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
    local expected_release expected_agent_config_sha
    if ! expected_release="$(release_id)" \
        || ! expected_agent_config_sha="$(sha256_file "${AGENT_CONFIG}")"; then
        return 1
    fi
    local root_pid="${session%%.*}"
    local pid
    while IFS= read -r pid; do
        [[ -n "${pid}" && -r "/proc/${pid}/environ" ]] || continue
        if [[ "$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)" \
            == "${RUNTIME_ROOT}/backend" ]] \
            && tr '\0' '\n' < "/proc/${pid}/environ" | awk -F= \
                -v release="${expected_release}" \
                -v mode="${RUNTIME_MODE}" \
                -v port="${API_PORT}" \
                -v report_agent_url="http://127.0.0.1:28474" \
                -v interactive_agent_url="http://127.0.0.1:28476" \
                -v agent_config_sha="${expected_agent_config_sha}" '
                    $1 == "DOMEYE_P0_PRODUCTION_RELEASE_ID" && $2 == release { a=1 }
                    $1 == "DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE" && $2 == mode { b=1 }
                    $1 == "PORT" && $2 == port { c=1 }
                    $1 == "DOMEYE_P0_RUNTIME_MODE" && $2 == mode { d=1 }
                    $1 == "COUNTRY_OUTAGE_AGENT_URL" && $2 == report_agent_url { e=1 }
                    $1 == "COUNTRY_OUTAGE_INTERACTIVE_AGENT_SIDECAR_URL" && $2 == interactive_agent_url { f=1 }
                    $1 == "DOMEYE_COUNTRY_OUTAGE_AGENT_CONFIG_SHA256" && $2 == agent_config_sha { g=1 }
                    END {
                        exit(a && b && c && d && e && f && g ? 0 : 1)
                    }
                '; then
            printf '%s\n' "${pid}"
            return 0
        fi
    done < <(pgrep -P "${root_pid}" -f 'python.*run.py' 2>/dev/null || true)
    return 1
}

listener_output_matches_runtime() {
    local pid="$1"
    local sockets="$2"
    [[ "${pid}" =~ ^[1-9][0-9]*$ ]] || return 1
    awk -v address="127.0.0.1:${API_PORT}" -v marker="pid=${pid}," '
      NF {
        count += 1
        if ($4 == address && index($0, marker) > 0) matched += 1
      }
      END { exit !(count == 1 && matched == 1) }
    ' <<<"${sockets}"
}

assert_runtime_listener() {
    local session="$1"
    local pid sockets
    pid="$(session_process "${session}")" || {
        error "运行时进程身份不匹配：${session}"
        return 1
    }
    if ! sockets="$(ss -H -ltnp "sport = :${API_PORT}")"; then
        error "无法查询 ${API_PORT} 监听进程"
        return 1
    fi
    if ! listener_output_matches_runtime "${pid}" "${sockets}"; then
        error "${API_PORT} 唯一回环监听与 Backend 入口 PID 不一致"
        return 1
    fi
    printf '%s\n' "${pid}"
}

assert_runtime_port_closed() {
    local sockets
    if ! sockets="$(ss -H -ltn "sport = :${API_PORT}")"; then
        error "无法查询 ${API_PORT} 关闭状态"
        return 1
    fi
    [[ -z "${sockets}" ]] || {
        error "Screen 已停止但 ${API_PORT} 仍有孤儿监听"
        return 1
    }
}

workflow_completion_state() {
    local unified_candidate unified_root deployment canary production state
    local canary_sha production_sha selected_release
    unified_candidate="$(jq -er '.unified_candidate.manifest_path' \
        "${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json")" || return 1
    case "${unified_candidate}" in
        */CANDIDATE-MANIFEST.json) ;;
        *) return 1 ;;
    esac
    unified_root="${unified_candidate%/CANDIDATE-MANIFEST.json}"
    deployment="${unified_root}/DEPLOYMENT.json"
    canary="${unified_root}/CANARY-VERIFICATION.json"
    production="${unified_root}/PRODUCTION-VERIFICATION.json"
    state="${unified_root}/ACTIVATION-STATE.json"
    [[ -f "${deployment}" && ! -L "${deployment}" \
        && -f "${canary}" && ! -L "${canary}" \
        && -f "${production}" && ! -L "${production}" \
        && -f "${state}" && ! -L "${state}" ]] || {
        printf 'pending\n'
        return 0
    }
    canary_sha="$(sha256_file "${canary}")" || return 1
    production_sha="$(sha256_file "${production}")" || return 1
    selected_release="$(jq -er '.unified_candidate.release_id' \
        "${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json")" || return 1
    if jq -e --arg release_id "${selected_release}" \
        --arg canary_sha "sha256:${canary_sha}" \
        --arg production_sha "sha256:${production_sha}" \
        --slurpfile candidate "${unified_candidate}" \
        --slurpfile state "${state}" \
        --slurpfile canary "${canary}" \
        --slurpfile production "${production}" '
      .schema_version == "domeye_country_outage_general_deployment_v2"
      and .release_id == $release_id
      and .status == "production_verified"
      and .production_verified == true
      and .verification == {
        canary:{path:"CANARY-VERIFICATION.json",sha256:$canary_sha},
        production:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_sha}
      }
      and .components.interactive_agent == {
        release_id:$candidate[0].interactive_agent.release_id,
        candidate_id:$candidate[0].interactive_agent.candidate_id,
        acceptance_record_id:$candidate[0].interactive_agent.acceptance_record_id
      }
      and $state[0].release_id == $release_id
      and $state[0].phase == "production_verified"
      and $state[0].status == "passed"
      and $state[0].candidate.interactive_agent == {
        release_id:$candidate[0].interactive_agent.release_id,
        candidate_id:$candidate[0].interactive_agent.candidate_id,
        acceptance_record_id:$candidate[0].interactive_agent.acceptance_record_id
      }
      and $state[0].verification == {
        canary:{path:"CANARY-VERIFICATION.json",sha256:$canary_sha},
        production:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_sha}
      }
      and $canary[0].schema_version
        == "domeye_country_outage_general_runtime_verification_v2"
      and $canary[0].release_id == $release_id
      and $canary[0].mode == "canary"
      and $canary[0].status == "canary_verified"
      and $production[0].schema_version
        == "domeye_country_outage_general_runtime_verification_v2"
      and $production[0].release_id == $release_id
      and $production[0].mode == "production"
      and $production[0].status == "production_verified"
      and ($canary[0].interactive_answer | .status == "canary_verified"
        and .promotion_receipt.schema_version == "domeye_interactive_agent_promotion_v2"
        and .promotion_receipt.candidate_id == $candidate[0].interactive_agent.candidate_id
        and .promotion_receipt.acceptance_record_id == $candidate[0].interactive_agent.acceptance_record_id
        and .promotion_receipt.result.state == "completed"
        and .promotion_receipt.result.answer_success == true
        and .promotion_receipt.result.workflow_completed == true
        and .promotion_receipt.result.answer_source == "renderer"
        and .promotion_receipt.result.guard_decision == "pass"
        and .promotion_receipt.result.internal_record_verified == true
        and .promotion_receipt.result.public_internal_projection_equal == true
        and .promotion_receipt.result.fallback_or_rejection_present == false)
      and ($production[0].interactive_answer | .status == "production_verified"
        and .production_verified == true
        and .promotion_receipt.schema_version == "domeye_interactive_agent_promotion_v2"
        and .promotion_receipt.candidate_id == $candidate[0].interactive_agent.candidate_id
        and .promotion_receipt.acceptance_record_id == $candidate[0].interactive_agent.acceptance_record_id
        and .promotion_receipt.result.state == "completed"
        and .promotion_receipt.result.answer_success == true
        and .promotion_receipt.result.workflow_completed == true
        and .promotion_receipt.result.answer_source == "renderer"
        and .promotion_receipt.result.guard_decision == "pass"
        and .promotion_receipt.result.internal_record_verified == true
        and .promotion_receipt.result.public_internal_projection_equal == true
        and .promotion_receipt.result.fallback_or_rejection_present == false)
      and $canary[0].interactive_answer.promotion_receipt.public_response.conversation_id
        != $production[0].interactive_answer.promotion_receipt.public_response.conversation_id
      and $canary[0].interactive_answer.promotion_receipt.public_response.turn_id
        != $production[0].interactive_answer.promotion_receipt.public_response.turn_id
    ' "${deployment}" >/dev/null; then
        printf 'verified\n'
    else
        printf 'pending\n'
    fi
}

serve_runtime() {
    if ! validate_runtime; then
        return 1
    fi
    if ! verify_interactive_agent_binding; then
        return 1
    fi
    local db_name db_port db_user db_password secret_key
    local agent_url agent_token agent_identity agent_user agent_config_sha
    local interactive_agent_url interactive_agent_token
    if ! db_name="$(read_config_value \
        "${DATABASE_CONFIG}" DOMEYE_CORE_DB_NAME)" \
        || ! db_user="$(read_config_value \
            "${DATABASE_CONFIG}" DOMEYE_CORE_DB_READER_USER)" \
        || ! db_password="$(read_config_value \
            "${DATABASE_CONFIG}" DOMEYE_CORE_DB_READER_PASSWORD)" \
        || ! secret_key="$(read_config_value \
            "${DATABASE_CONFIG}" DOMEYE_CORE_SECRET_KEY)" \
        || ! agent_url="$(read_config_value \
            "${AGENT_CONFIG}" COUNTRY_OUTAGE_AGENT_URL)" \
        || ! agent_token="$(read_config_value \
            "${AGENT_CONFIG}" COUNTRY_OUTAGE_AGENT_SHARED_TOKEN)" \
        || ! agent_identity="$(read_config_value \
            "${AGENT_CONFIG}" COUNTRY_OUTAGE_AGENT_IDENTITY_MODE)" \
        || ! agent_user="$(read_config_value \
            "${AGENT_CONFIG}" COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID)" \
        || ! agent_config_sha="$(sha256_file "${AGENT_CONFIG}")" \
        || ! interactive_agent_url="$(read_config_value \
            "${INTERACTIVE_AGENT_CONFIG}" COUNTRY_OUTAGE_AGENT_URL)" \
        || ! interactive_agent_token="$(read_config_value \
            "${INTERACTIVE_AGENT_CONFIG}" COUNTRY_OUTAGE_AGENT_SHARED_TOKEN)"; then
        error '无法读取 Backend 固定运行配置'
        return 1
    fi
    # database.env 仍保留历史 29429；当前 feb-mar-2026 数据档的权威端口只来自
    # 已由 Backend 绑定摘要保护的 state.json，禁止把旧配置漂移带入候选运行时。
    if ! db_port="$(jq -er '.port' "${DATABASE_STATE}")"; then
        error '无法读取固定二三月数据库端口'
        return 1
    fi
    [[ "${interactive_agent_url}" == 'http://127.0.0.1:28476' ]] || {
        error 'Interactive Agent Sidecar URL 必须固定为 127.0.0.1:28476'
        return 1
    }
    [[ "${interactive_agent_token}" == "${agent_token}" ]] || {
        error 'Interactive Agent 与现有 Agent 内部共享 Token 不一致'
        return 1
    }
    local selected_release log_root general_read_model
    if ! selected_release="$(release_id)"; then
        error '无法读取 Backend release-id'
        return 1
    fi
    log_root="/home/bgpdata/Domeye-Core-runtime/log/${selected_release}/${RUNTIME_MODE}"
    if ! install -d -o 0 -g 0 -m 0750 "${log_root}" "${log_root}/app"; then
        error '无法创建 Backend 日志目录'
        return 1
    fi
    general_read_model=''
    if [[ -d "${RUNTIME_ROOT}/general-read-model" ]]; then
        general_read_model="${RUNTIME_ROOT}/general-read-model"
    fi
    ss -H -ltn "sport = :${db_port}" | awk '
        $4 == "127.0.0.1:31627" { found=1 }
        END { exit(found ? 0 : 1) }
    ' || {
        error '固定二三月只读数据库未监听 127.0.0.1:31627'
        return 1
    }

    if ! exec env -i \
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
            DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE="${RUNTIME_MODE}" \
            P0_DATA_RELEASE_DIR="${P0_DATA_DIR}" \
            P0_DATA_PRODUCTION_ACTIVE=true \
            DOMEYE_DATA_LAYER_224_310_SELECTION="${RUNTIME_ROOT}/data-layer/PRODUCTION-SELECTION.json" \
            DOMEYE_COUNTRY_OUTAGE_REGISTRY="${RUNTIME_ROOT}/country-outage-registry.json" \
            DOMEYE_COUNTRY_OUTAGE_GENERAL_READ_MODEL="${general_read_model}" \
            COUNTRY_OUTAGE_AGENT_URL="${agent_url}" \
            COUNTRY_OUTAGE_AGENT_SHARED_TOKEN="${agent_token}" \
            COUNTRY_OUTAGE_AGENT_IDENTITY_MODE="${agent_identity}" \
            COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID="${agent_user}" \
            COUNTRY_OUTAGE_INTERACTIVE_AGENT_SIDECAR_URL="${interactive_agent_url}" \
            DOMEYE_COUNTRY_OUTAGE_AGENT_CONFIG_SHA256="${agent_config_sha}" \
            PYTHONUNBUFFERED=1 \
            PYTHONDONTWRITEBYTECODE=1 \
            VIRTUAL_ENV="${RUNTIME_ROOT}/venv" \
            bash -c 'cd -- "$1" && exec "$2" run.py' \
                _ "${RUNTIME_ROOT}/backend" "${RUNTIME_ROOT}/venv/bin/python"; then
        error '无法 exec Backend 运行入口'
        return 1
    fi
}

start_runtime() {
    if ! validate_runtime; then
        return 1
    fi
    if ! verify_interactive_agent_binding; then
        return 1
    fi
    mapfile -t sessions < <(list_sessions)
    if (( ${#sessions[@]} > 1 )); then
        error "发现多个同名会话：${sessions[*]}"
        return 1
    fi
    if (( ${#sessions[@]} == 1 )); then
        assert_runtime_listener "${sessions[0]}" >/dev/null || {
            error "既有会话或 ${API_PORT} 监听身份不匹配：${sessions[0]}"
            return 1
        }
        printf '运行时进程已就绪（流程完成仍需 Renderer + Guard 正确回答）：%s\n' \
            "${sessions[0]}"
        return 0
    fi

    local selected_release log_root
    if ! selected_release="$(release_id)"; then
        error '无法读取 Backend release-id'
        return 1
    fi
    log_root="/home/bgpdata/Domeye-Core-runtime/log/${selected_release}/${RUNTIME_MODE}"
    if ! install -d -o 0 -g 0 -m 0750 "${log_root}" "${log_root}/app"; then
        error '无法创建 Backend 启动日志目录'
        return 1
    fi
    if ! screen -L -Logfile "${log_root}/screen.log" -dmS "${SCREEN_NAME}" \
        env -i \
            HOME=/home/bgpdata \
            USER=root \
            LOGNAME=root \
            LANG=C.UTF-8 \
            LC_ALL=C.UTF-8 \
            PATH="${RUNTIME_PATH}" \
            DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE="${MODE}" \
            DOMEYE_COUNTRY_OUTAGE_RUNTIME_ROOT_OVERRIDE="${RUNTIME_ROOT}" \
            "${SCRIPT_DIR}/manage-runtime.sh" _serve; then
        error '无法创建 Backend Screen 会话'
        return 1
    fi

    local attempt
    for (( attempt = 1; attempt <= 60; attempt++ )); do
        mapfile -t sessions < <(list_sessions)
        if (( ${#sessions[@]} == 1 )) \
            && assert_runtime_listener "${sessions[0]}" >/dev/null \
            && curl --disable --noproxy '*' --proto '=http' --max-redirs 0 \
                -fsS --max-time 2 \
                "http://127.0.0.1:${API_PORT}/api/v1/healthz" >/dev/null 2>&1; then
            if ! verify_interactive_agent_binding; then
                error 'Backend 就绪后 Interactive Agent 身份发生漂移'
                return 1
            fi
            printf '运行时进程已就绪（流程完成仍需 Renderer + Guard 正确回答）：%s / %s\n' \
                "${sessions[0]}" "${selected_release}"
            return 0
        fi
        sleep 0.5
    done
    error "运行时 30 秒内未就绪：${selected_release}"
    tail -80 "${log_root}/screen.log" >&2 || true
    return 1
}

stop_runtime() {
    if ! validate_runtime_root; then
        return 1
    fi
    for command_name in awk grep jq pgrep readlink screen tr; do
        command -v "${command_name}" >/dev/null 2>&1 || {
            error "停止运行时缺少命令：${command_name}"
            return 1
        }
    done
    [[ -f "${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json" \
        && ! -L "${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json" ]] || {
        error '停止目标缺少 Backend 身份绑定'
        return 1
    }
    if ! jq -e '
      .schema_version == "domeye_country_outage_general_backend_binding_v2"
      and (.release_id | type == "string" and endswith("-backend"))
    ' "${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json" >/dev/null; then
        error '停止目标 Backend 身份绑定无效'
        return 1
    fi
    mapfile -t sessions < <(list_sessions)
    if (( ${#sessions[@]} == 0 )); then
        if ! assert_runtime_port_closed; then
            return 1
        fi
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
    if ! screen -S "${sessions[0]}" -X quit; then
        error "无法请求停止 Backend 会话：${sessions[0]}"
        return 1
    fi
    local attempt
    for (( attempt = 1; attempt <= 40; attempt++ )); do
        if ! list_sessions | grep -Fxq "${sessions[0]}"; then
            if assert_runtime_port_closed; then
                printf '运行时已停止：%s\n' "$(release_id)"
                return 0
            fi
        fi
        sleep 0.25
    done
    error "会话未停止：${sessions[0]}"
    return 1
}

status_runtime() {
    if ! validate_runtime; then
        return 1
    fi
    if ! verify_interactive_agent_binding; then
        return 1
    fi
    mapfile -t sessions < <(list_sessions)
    (( ${#sessions[@]} == 1 )) || {
        error "运行时会话数量不是 1：${#sessions[@]}"
        return 1
    }
    local pid
    pid="$(assert_runtime_listener "${sessions[0]}")" || {
        error "运行时进程或 ${API_PORT} 监听身份不匹配：${sessions[0]}"
        return 1
    }
    if ! curl --disable --noproxy '*' --proto '=http' --max-redirs 0 \
        -fsS --max-time 5 \
        "http://127.0.0.1:${API_PORT}/api/v1/healthz" \
        | jq -e '.status == "ok" and .service == "domeye-core"' >/dev/null; then
        error 'Backend 健康检查失败'
        return 1
    fi
    local completion_state
    completion_state="$(workflow_completion_state)" || {
        error '无法重算公共回答完成状态'
        return 1
    }
    jq -n \
        --arg status running \
        --arg mode "${RUNTIME_MODE}" \
        --arg release_id "$(release_id)" \
        --arg runtime_root "${RUNTIME_ROOT}" \
        --arg session "${sessions[0]}" \
        --argjson pid "${pid}" \
        --argjson port "${API_PORT}" \
        --arg completion_state "${completion_state}" \
        --argjson interactive_agent "${INTERACTIVE_AGENT_STATUS_JSON}" \
        '{status:$status,mode:$mode,release_id:$release_id,runtime_root:$runtime_root,session:$session,pid:$pid,port:$port,interactive_agent:$interactive_agent,workflow_completion:{state:$completion_state,requires_renderer_guard_correct_answer:true,requires_general_production_evidence:true,health_check_is_completion:false}}'
}

if (( $# != 1 )); then
    printf '用法：%s start|stop|status\n' "${0##*/}" >&2
    exit 2
fi

case "$1" in
    start) start_runtime ;;
    stop) stop_runtime ;;
    status) status_runtime ;;
    _serve) serve_runtime ;;
    *)
        error "未知命令：$1"
        exit 2
        ;;
esac
