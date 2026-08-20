#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
# shellcheck source=../lib/artifact-common.sh
source "${PROJECT_ROOT}/deploy/lib/artifact-common.sh"
# shellcheck source=../lib/frontend-common.sh
source "${PROJECT_ROOT}/deploy/lib/frontend-common.sh"

error() {
    printf '国家中断通用观测制品准备错误：%s\n' "$*" >&2
}

if (( $# != 8 )); then
    printf '用法：%s <release-id> <source-archive> <source-commit> <source-tag> <approved-candidate-id> <approved-acceptance-record-id> <previous-backend-root> <general-read-model-root>\n' \
        "${0##*/}" >&2
    exit 2
fi

readonly RELEASE_ID="$1"
readonly SOURCE_ARCHIVE="$2"
readonly SOURCE_COMMIT="$3"
readonly SOURCE_TAG="$4"
readonly APPROVED_CANDIDATE_ID="$5"
readonly APPROVED_ACCEPTANCE_RECORD_ID="$6"
readonly PREVIOUS_BACKEND="$7"
readonly GENERAL_READ_MODEL="$8"
readonly RUNTIME_RELEASE_ROOT='/home/bgpdata/Domeye-Core-runtime/releases'
readonly UNIFIED_ROOT="/home/bgpdata/Domeye-Core-runtime/unified-releases/${RELEASE_ID}"
readonly BACKEND_RELEASE_ID="${RELEASE_ID}-backend"
readonly FRONTEND_RELEASE_ID="${RELEASE_ID}-frontend"
readonly SOURCE_RELEASE_ID="${RELEASE_ID}-source"
readonly BACKEND_TARGET="${RUNTIME_RELEASE_ROOT}/${BACKEND_RELEASE_ID}"
readonly FRONTEND_TARGET="${RUNTIME_RELEASE_ROOT}/${FRONTEND_RELEASE_ID}"
readonly SOURCE_TARGET="${RUNTIME_RELEASE_ROOT}/${SOURCE_RELEASE_ID}"
readonly NODE_BIN_DIR='/home/bgpdata/.local/node-v22.23.1-linux-x64/bin'
readonly INTERACTIVE_AGENT_RUNTIME_ROOT='/home/bgpdata/Domeye-Core-runtime/country-outage-interactive-agent'
readonly INTERACTIVE_AGENT_RELEASE_ROOT="${INTERACTIVE_AGENT_RUNTIME_ROOT}/releases"
readonly INTERACTIVE_AGENT_CURRENT="${INTERACTIVE_AGENT_RUNTIME_ROOT}/current"
readonly INTERACTIVE_AGENT_ACTIVE="${INTERACTIVE_AGENT_RUNTIME_ROOT}/state/active.json"
readonly INTERACTIVE_AGENT_CONFIG='/home/bgpdata/Domeye-Core-runtime/config/country-outage-interactive-agent.env'
readonly INTERACTIVE_AGENT_MANAGER="${PROJECT_ROOT}/deploy/country-outage-agent/p1-chat/manage.sh"
readonly INTERACTIVE_AGENT_CANDIDATE_RELATIVE='project/contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json'
readonly DATABASE_STATE='/home/bgpdata/Domeye-Core-dev-data/state.json'
readonly FRONTEND_CURRENT_STATE='/home/bgpdata/Domeye-Core-runtime/web/state/frontend-current'
readonly NGINX_MAIN='/etc/nginx/nginx.conf'
readonly NGINX_SITE='/etc/nginx/conf.d/domeye-core.conf'

if (( EUID != 0 )); then
    error '制品准备必须由 root 执行'
    exit 1
fi
if [[ ! "${RELEASE_ID}" =~ ^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9._-]{0,47}$ ]]; then
    error "release-id 格式无效：${RELEASE_ID}"
    exit 2
fi
if [[ ! "${SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ || -z "${SOURCE_TAG}" ]]; then
    error '源码提交或 tag 身份无效'
    exit 2
fi
if [[ ! "${APPROVED_CANDIDATE_ID}" =~ ^manifest:sha256:[a-f0-9]{64}$ \
    || ! "${APPROVED_ACCEPTANCE_RECORD_ID}" \
        =~ ^acceptance-record-sha256:[a-f0-9]{64}$ ]]; then
    error '外部批准的 Candidate 或 Acceptance 身份无效'
    exit 2
fi
if [[ "${RELEASE_ID}" != "${SOURCE_TAG}" ]]; then
    error '统一 release-id 必须与 annotated tag 完全一致'
    exit 2
fi
for command_name in basename cmp cp date find install jq npm readlink sha256sum stat tar; do
    command -v "${command_name}" >/dev/null 2>&1 || {
        error "缺少命令：${command_name}"
        exit 1
    }
done
for file in \
    "${SOURCE_ARCHIVE}" \
    "${DATABASE_STATE}" \
    "${FRONTEND_CURRENT_STATE}" \
    "${NGINX_MAIN}" \
    "${NGINX_SITE}"; do
    [[ -f "${file}" && ! -L "${file}" ]] || {
        error "输入不是普通文件：${file}"
        exit 1
    }
done
for directory in "${PREVIOUS_BACKEND}" "${GENERAL_READ_MODEL}"; do
    [[ -d "${directory}" && ! -L "${directory}" \
        && "$(readlink -f -- "${directory}")" == "${directory}" ]] || {
        error "输入不是规范实际目录：${directory}"
        exit 1
    }
done
[[ "${PREVIOUS_BACKEND}" == "${RUNTIME_RELEASE_ROOT}/"*-backend ]] || {
    error '前序 Backend 不在受控 release 根'
    exit 1
}
for target in "${BACKEND_TARGET}" "${FRONTEND_TARGET}" "${SOURCE_TARGET}" "${UNIFIED_ROOT}"; do
    if [[ -e "${target}" || -L "${target}" ]]; then
        error "目标已存在，create-only 拒绝覆盖：${target}"
        exit 1
    fi
done
[[ -x "${NODE_BIN_DIR}/node" && -x "${NODE_BIN_DIR}/npm" ]] || {
    error '固定 Node.js 工具链不存在'
    exit 1
}
[[ "$("${NODE_BIN_DIR}/node" --version)" == 'v22.23.1' ]] || {
    error '固定 Node.js 版本冲突'
    exit 1
}
cmp -s "${GENERAL_READ_MODEL}/manifest.json" "${GENERAL_READ_MODEL}/COMPLETE.json" || {
    error '通用读模型 manifest 与 COMPLETE 不一致'
    exit 1
}

if ! source_archive_sha="$(sha256sum -- "${SOURCE_ARCHIVE}" | awk '{print $1}')"; then
    error '无法计算 General Source 归档摘要'
    exit 1
fi
readonly SOURCE_ARCHIVE_SHA256="sha256:${source_archive_sha}"
interactive_agent_path=''
interactive_agent_release_id=''
interactive_agent_release_manifest=''
interactive_agent_release_manifest_sha256=''
interactive_agent_active_sha256=''
interactive_agent_candidate_manifest=''
interactive_agent_candidate_manifest_sha256=''
interactive_agent_candidate_id=''
interactive_agent_acceptance_record=''
interactive_agent_acceptance_record_sha256=''
interactive_agent_acceptance_record_id=''
interactive_agent_acceptance_replay_receipt=''
interactive_agent_acceptance_replay_receipt_sha256=''
interactive_agent_readiness_identity_sha256=''
interactive_agent_answer_attempt_limit=''
interactive_agent_cost_policy=''

verify_interactive_agent_binding() {
    [[ -x "${INTERACTIVE_AGENT_MANAGER}" && ! -L "${INTERACTIVE_AGENT_MANAGER}" ]] || {
        error 'Interactive Agent 新发布管理器不是固定可执行普通文件'
        return 1
    }
    [[ -L "${INTERACTIVE_AGENT_CURRENT}" ]] || {
        error 'Interactive Agent current 不是符号链接'
        return 1
    }
    [[ -f "${INTERACTIVE_AGENT_ACTIVE}" && ! -L "${INTERACTIVE_AGENT_ACTIVE}" ]] || {
        error 'Interactive Agent active.json 不是普通文件'
        return 1
    }

    interactive_agent_path="$(readlink -f -- "${INTERACTIVE_AGENT_CURRENT}")" || {
        error '无法解析 Interactive Agent current'
        return 1
    }
    [[ -d "${interactive_agent_path}" && ! -L "${interactive_agent_path}" \
        && "${interactive_agent_path}" == "${INTERACTIVE_AGENT_RELEASE_ROOT}/"* ]] || {
        error 'Interactive Agent current 不在固定 release 根'
        return 1
    }
    interactive_agent_release_id="$(basename -- "${interactive_agent_path}")"
    [[ "${interactive_agent_release_id}" =~ ^[0-9]{8}T[0-9]{6}Z-country-outage-interactive-agent-[a-z0-9][a-z0-9-]{0,31}$ ]] || {
        error 'Interactive Agent release-id 无效'
        return 1
    }
    [[ "${RELEASE_ID}" == "${SOURCE_TAG}" \
        && "${SOURCE_TAG}" == "${interactive_agent_release_id}" ]] || {
        error '统一 release-id、annotated tag 与 Interactive Agent release-id 必须完全一致'
        return 1
    }
    interactive_agent_release_manifest="${interactive_agent_path}/RELEASE-MANIFEST.json"
    interactive_agent_candidate_manifest="${interactive_agent_path}/${INTERACTIVE_AGENT_CANDIDATE_RELATIVE}"
    [[ -f "${interactive_agent_release_manifest}" \
        && ! -L "${interactive_agent_release_manifest}" ]] || {
        error 'Interactive Agent RELEASE-MANIFEST.json 不是普通文件'
        return 1
    }
    local acceptance_relative replay_relative
    if ! acceptance_relative="$(jq -er '.acceptance.record_path' \
        "${interactive_agent_release_manifest}")" \
        || ! replay_relative="$(jq -er '.acceptance.replay_receipt_path' \
            "${interactive_agent_release_manifest}")"; then
        error '无法读取 Interactive Agent Acceptance 证据路径'
        return 1
    fi
    [[ "${acceptance_relative}" \
        == project/evaluation/country-outage/first-vertical-slice/runs/*/acceptance-record-final.json \
        && "${replay_relative}" == 'deployment/ACCEPTANCE-REPLAY.json' ]] || {
        error 'Interactive Agent Acceptance 证据路径越界'
        return 1
    }
    interactive_agent_acceptance_record="${interactive_agent_path}/${acceptance_relative}"
    interactive_agent_acceptance_replay_receipt="${interactive_agent_path}/${replay_relative}"
    local file
    for file in \
        "${interactive_agent_release_manifest}" \
        "${interactive_agent_candidate_manifest}" \
        "${interactive_agent_acceptance_record}" \
        "${interactive_agent_acceptance_replay_receipt}" \
        "${INTERACTIVE_AGENT_CONFIG}"; do
        [[ -f "${file}" && ! -L "${file}" ]] || {
            error "Interactive Agent 绑定文件不是普通文件：${file}"
            return 1
        }
    done

    if ! "${INTERACTIVE_AGENT_MANAGER}" verify-release \
        "${interactive_agent_release_id}" >/dev/null; then
        error 'Interactive Agent release 未通过新发布管理器不可变校验'
        return 1
    fi
    local interactive_agent_status
    if ! interactive_agent_status="$("${INTERACTIVE_AGENT_MANAGER}" status)"; then
        error 'Interactive Agent 未通过新发布管理器组合状态校验'
        return 1
    fi
    if ! jq -e \
        --arg release_id "${interactive_agent_release_id}" '
          .schema_version == "domeye_interactive_agent_release_probe_v2"
          and .ready == true
          and .component == "domeye_interactive_agent_sidecar"
          and .lifecycle_state == "deployed"
          and .release_id == $release_id
          and .candidate_activation_scope == "local_evaluation_only"
          and .candidate_production_deployed == false
          and .current_target_matches == true
          and .deployment_active == true
          and .promotion_state == "absent"
          and .production_verified == false
        ' <<<"${interactive_agent_status}" >/dev/null; then
        error 'Interactive Agent manager status 不代表 deployed/verified 身份闭包'
        return 1
    fi

    local release_sha active_sha candidate_sha acceptance_sha replay_sha
    if ! release_sha="$(sha256sum -- "${interactive_agent_release_manifest}" \
        | awk '{print $1}')" \
        || ! active_sha="$(sha256sum -- "${INTERACTIVE_AGENT_ACTIVE}" \
            | awk '{print $1}')" \
        || ! candidate_sha="$(sha256sum -- "${interactive_agent_candidate_manifest}" \
            | awk '{print $1}')" \
        || ! acceptance_sha="$(sha256sum -- "${interactive_agent_acceptance_record}" \
            | awk '{print $1}')" \
        || ! replay_sha="$(sha256sum -- "${interactive_agent_acceptance_replay_receipt}" \
            | awk '{print $1}')"; then
        error '无法计算 Interactive Agent release/active/Candidate/Acceptance 摘要'
        return 1
    fi
    interactive_agent_release_manifest_sha256="sha256:${release_sha}"
    interactive_agent_active_sha256="sha256:${active_sha}"
    interactive_agent_candidate_manifest_sha256="sha256:${candidate_sha}"
    interactive_agent_acceptance_record_sha256="sha256:${acceptance_sha}"
    interactive_agent_acceptance_replay_receipt_sha256="sha256:${replay_sha}"
    interactive_agent_candidate_id="$(jq -er '.candidate_id' \
        "${interactive_agent_candidate_manifest}")" || {
        error '无法读取 Interactive Agent Candidate ID'
        return 1
    }
    interactive_agent_acceptance_record_id="$(jq -er '.acceptance_record_id' \
        "${interactive_agent_acceptance_record}")" || {
        error '无法读取 Interactive Agent Acceptance Record ID'
        return 1
    }
    [[ "${interactive_agent_candidate_id}" == "${APPROVED_CANDIDATE_ID}" \
        && "${interactive_agent_acceptance_record_id}" \
            == "${APPROVED_ACCEPTANCE_RECORD_ID}" ]] || {
        error 'Interactive Agent 与外部批准的 Candidate/Acceptance 身份不一致'
        return 1
    }
    if ! jq -e \
        --arg candidate_id "${interactive_agent_candidate_id}" \
        --arg acceptance_id "${interactive_agent_acceptance_record_id}" '
          .schema_version == "domeye_first_slice_acceptance_record_v2"
          and .candidate_id == $candidate_id
          and .acceptance_record_id == $acceptance_id
          and .evaluation_phase == "formal"
          and .acceptance_state == "accepted"
          and .dg1_decision == "GO"
        ' "${interactive_agent_acceptance_record}" >/dev/null; then
        error 'Interactive Agent Acceptance Record 不是外部批准的 Formal accepted/GO 证据'
        return 1
    fi
    if ! interactive_agent_answer_attempt_limit="$(jq -er \
        '.payload.budget_policy.model_api_attempt_limit' \
        "${interactive_agent_candidate_manifest}")" \
        || ! interactive_agent_cost_policy="$(jq -er \
            '.payload.budget_policy.cost_policy' \
            "${interactive_agent_candidate_manifest}")"; then
        error '无法读取 Interactive Agent 尝试次数与费用策略'
        return 1
    fi
    if [[ "${interactive_agent_answer_attempt_limit}" != '10' \
        || "${interactive_agent_cost_policy}" != 'audit_only' ]] \
        || ! jq -e '.payload.budget_policy.monetary_limit_usd == null' \
            "${interactive_agent_candidate_manifest}" >/dev/null; then
        error 'Interactive Agent 必须固定 10 次尝试且费用策略仅审计、不设金额上限'
        return 1
    fi
    local interactive_agent_readiness_identity
    interactive_agent_readiness_identity="$(jq -cS '{
      schema_version,ready,component,release_id,release_manifest_sha256,
      candidate_id,candidate_activation_scope,candidate_production_deployed
    }' <<<"${interactive_agent_status}")" || {
        error '无法规范化 Interactive Agent readiness 身份'
        return 1
    }
    local readiness_sha
    if ! readiness_sha="$(printf '%s' "${interactive_agent_readiness_identity}" \
        | sha256sum | awk '{print $1}')"; then
        error '无法计算 Interactive Agent readiness 身份摘要'
        return 1
    fi
    interactive_agent_readiness_identity_sha256="sha256:${readiness_sha}"

    if ! jq -e \
        --arg release_id "${interactive_agent_release_id}" \
        --arg candidate_id "${interactive_agent_candidate_id}" \
        --arg candidate_sha "${interactive_agent_candidate_manifest_sha256}" \
        --arg candidate_path "${INTERACTIVE_AGENT_CANDIDATE_RELATIVE}" \
        --arg acceptance_path "${acceptance_relative}" \
        --arg acceptance_id "${interactive_agent_acceptance_record_id}" \
        --arg acceptance_sha "${interactive_agent_acceptance_record_sha256}" \
        --arg replay_path "${replay_relative}" \
        --arg replay_sha "${interactive_agent_acceptance_replay_receipt_sha256}" \
        --arg source_commit "${SOURCE_COMMIT}" \
        --arg source_tag "${SOURCE_TAG}" \
        --arg source_archive_sha "${SOURCE_ARCHIVE_SHA256}" '
          .schema_version == "domeye_interactive_agent_release_manifest_v2"
          and .component == "domeye_interactive_agent_sidecar"
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
          and .live_verification.public_conversation_schema_version == "domeye_interactive_agent_conversation_v2"
          and .live_verification.internal_record_schema_version == "domeye_interactive_agent_turn_internal_record_v1"
          and .rollback == {mode:"fail_closed",previous_release_id:null}
        ' "${interactive_agent_release_manifest}" >/dev/null; then
        error 'General Source 与 Interactive Agent 权威 Source/Candidate 身份不一致'
        return 1
    fi
    if ! jq -e \
        --arg release_id "${interactive_agent_release_id}" \
        --arg release_sha "${interactive_agent_release_manifest_sha256}" \
        --arg candidate_id "${interactive_agent_candidate_id}" '
          .schema_version == "domeye_interactive_agent_active_v1"
          and .component == "domeye_interactive_agent_sidecar"
          and .deployment_state == "deployed"
          and .release_id == $release_id
          and .release_manifest_sha256 == $release_sha
          and .candidate_id == $candidate_id
          and .runtime.host == "127.0.0.1"
          and .runtime.port == 28476
          and .runtime.base_path == "/country-outage/chat"
        ' "${INTERACTIVE_AGENT_ACTIVE}" >/dev/null; then
        error 'Interactive Agent active/release/Candidate 身份不一致'
        return 1
    fi
    if ! jq -e \
        --arg release_sha "${interactive_agent_release_manifest_sha256}" \
        --arg candidate_id "${interactive_agent_candidate_id}" '
          .schema_version == "domeye_interactive_agent_release_probe_v2"
          and .release_manifest_sha256 == $release_sha
          and .candidate_id == $candidate_id
        ' <<<"${interactive_agent_status}" >/dev/null; then
        error 'Interactive Agent manager status 摘要与冻结制品不一致'
        return 1
    fi
}

if ! verify_interactive_agent_binding; then
    exit 1
fi

backend_candidate="$(mktemp -d "${RUNTIME_RELEASE_ROOT}/.prepare-${BACKEND_RELEASE_ID}.XXXXXX")"
frontend_candidate="$(mktemp -d "${RUNTIME_RELEASE_ROOT}/.prepare-${FRONTEND_RELEASE_ID}.XXXXXX")"
source_candidate="$(mktemp -d "${RUNTIME_RELEASE_ROOT}/.prepare-${SOURCE_RELEASE_ID}.XXXXXX")"
unified_candidate="$(mktemp -d "/home/bgpdata/Domeye-Core-runtime/unified-releases/.prepare-${RELEASE_ID}.XXXXXX")"
published=false
cleanup() {
    local exit_code=$?
    if [[ "${published}" != true ]]; then
        local path
        for path in "${backend_candidate}" "${frontend_candidate}" "${source_candidate}" "${unified_candidate}"; do
            case "${path}" in
                "${RUNTIME_RELEASE_ROOT}/.prepare-${RELEASE_ID}"*|\
                "/home/bgpdata/Domeye-Core-runtime/unified-releases/.prepare-${RELEASE_ID}"*)
                    if [[ -d "${path}" && ! -L "${path}" ]]; then
                        chmod -R u+w "${path}" 2>/dev/null || true
                        find "${path}" -depth -delete
                    fi
                    ;;
                *) error "拒绝清理边界外候选：${path}" ;;
            esac
        done
    fi
    return "${exit_code}"
}
trap cleanup EXIT

tar -xzf "${SOURCE_ARCHIVE}" -C "${backend_candidate}"
[[ -f "${backend_candidate}/backend/run.py" \
    && -f "${backend_candidate}/deploy/country-outage-general-page/manage-runtime.sh" \
    && -f "${backend_candidate}/contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json" ]] || {
    error '源码归档不包含 S6 运行入口'
    exit 1
}
cmp -s \
    "${backend_candidate}/contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json" \
    "${interactive_agent_candidate_manifest}" || {
    error 'General Source 内 Candidate 与已部署 Interactive Agent Candidate 不一致'
    exit 1
}
cp -a --reflink=auto "${PREVIOUS_BACKEND}/venv" "${backend_candidate}/venv"
cp -a --reflink=auto "${PREVIOUS_BACKEND}/data-layer" "${backend_candidate}/data-layer"
cp -a --reflink=auto "${PREVIOUS_BACKEND}/country-outage-registry.json" \
    "${backend_candidate}/country-outage-registry.json"
cp -a --reflink=auto "${GENERAL_READ_MODEL}" "${backend_candidate}/general-read-model"
install -d -m 0750 "${source_candidate}/artifacts"
cp "${SOURCE_ARCHIVE}" "${source_candidate}/artifacts/source.tar.gz"

previous_release_id="$(jq -er '.release_id' "${PREVIOUS_BACKEND}/BACKEND-SOURCE-BINDING.json")"
database_state_sha="$(sha256sum "${DATABASE_STATE}" | awk '{print $1}')"
nginx_main_sha="$(sha256sum "${NGINX_MAIN}" | awk '{print $1}')"
nginx_site_sha="$(sha256sum "${NGINX_SITE}" | awk '{print $1}')"
data_selection_sha="$(sha256sum "${backend_candidate}/data-layer/PRODUCTION-SELECTION.json" | awk '{print $1}')"
registry_sha="$(sha256sum "${backend_candidate}/country-outage-registry.json" | awk '{print $1}')"
general_manifest_sha="$(sha256sum "${backend_candidate}/general-read-model/manifest.json" | awk '{print $1}')"

jq -n \
    --arg schema_version domeye_country_outage_general_source_v2 \
    --arg release_id "${SOURCE_RELEASE_ID}" \
    --arg commit "${SOURCE_COMMIT}" \
    --arg tag "${SOURCE_TAG}" \
    --arg archive_path "${SOURCE_TARGET}/artifacts/source.tar.gz" \
    --arg archive_sha256 "${SOURCE_ARCHIVE_SHA256}" \
    --arg authority_release_id "${interactive_agent_release_id}" \
    --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{schema_version:$schema_version,release_id:$release_id,commit:$commit,annotated_tag:$tag,archive_path:$archive_path,archive_sha256:$archive_sha256,source_authority:{mode:"interactive_agent_release",release_id:$authority_release_id,equality_verified:true},created_at:$created_at}' \
    > "${source_candidate}/SOURCE-MANIFEST.json"
chmod 0644 "${source_candidate}/SOURCE-MANIFEST.json"

jq -n \
    --arg schema_version domeye_country_outage_general_backend_binding_v2 \
    --arg release_id "${BACKEND_RELEASE_ID}" \
    --arg runtime_root "${BACKEND_TARGET}" \
    --arg unified_release_id "${RELEASE_ID}" \
    --arg unified_candidate_manifest_path "${UNIFIED_ROOT}/CANDIDATE-MANIFEST.json" \
    --arg source_release_id "${SOURCE_RELEASE_ID}" \
    --arg source_commit "${SOURCE_COMMIT}" \
    --arg source_tag "${SOURCE_TAG}" \
    --arg source_archive_sha256 "${SOURCE_ARCHIVE_SHA256}" \
    --arg authority_release_id "${interactive_agent_release_id}" \
    --arg database_state_sha256 "${database_state_sha}" \
    --arg nginx_main_sha256 "${nginx_main_sha}" \
    --arg nginx_site_sha256 "${nginx_site_sha}" \
    --arg data_selection_sha256 "${data_selection_sha}" \
    --arg registry_sha256 "${registry_sha}" \
    --arg general_read_model_sha256 "${general_manifest_sha}" \
    --arg interactive_agent_path "${interactive_agent_path}" \
    --arg interactive_agent_release_manifest "${interactive_agent_release_manifest}" \
    --arg interactive_agent_release_manifest_sha256 "${interactive_agent_release_manifest_sha256}" \
    --arg interactive_agent_active "${INTERACTIVE_AGENT_ACTIVE}" \
    --arg interactive_agent_active_sha256 "${interactive_agent_active_sha256}" \
    --arg interactive_agent_candidate_id "${interactive_agent_candidate_id}" \
    --arg interactive_agent_candidate_manifest "${interactive_agent_candidate_manifest}" \
    --arg interactive_agent_candidate_manifest_sha256 "${interactive_agent_candidate_manifest_sha256}" \
    --arg interactive_agent_acceptance_record "${interactive_agent_acceptance_record}" \
    --arg interactive_agent_acceptance_record_id "${interactive_agent_acceptance_record_id}" \
    --arg interactive_agent_acceptance_record_sha256 "${interactive_agent_acceptance_record_sha256}" \
    --arg interactive_agent_acceptance_replay_receipt "${interactive_agent_acceptance_replay_receipt}" \
    --arg interactive_agent_acceptance_replay_receipt_sha256 "${interactive_agent_acceptance_replay_receipt_sha256}" \
    --arg interactive_agent_readiness_identity_sha256 "${interactive_agent_readiness_identity_sha256}" \
    --argjson interactive_answer_attempt_limit "${interactive_agent_answer_attempt_limit}" \
    --arg interactive_agent_cost_policy "${interactive_agent_cost_policy}" \
    --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{
      schema_version:$schema_version,
      release_id:$release_id,
      runtime_root:$runtime_root,
      unified_candidate:{release_id:$unified_release_id,manifest_path:$unified_candidate_manifest_path},
      source_release_id:$source_release_id,
      source_commit:$source_commit,
      source_tag:$source_tag,
      source_archive_sha256:$source_archive_sha256,
      source_authority:{mode:"interactive_agent_release",release_id:$authority_release_id,commit:$source_commit,annotated_tag:$source_tag,archive_sha256:$source_archive_sha256,equality_verified:true},
      database_state_sha256:$database_state_sha256,
      nginx_main_sha256:$nginx_main_sha256,
      nginx_site_sha256:$nginx_site_sha256,
      data_selection_sha256:$data_selection_sha256,
      country_outage_registry_sha256:$registry_sha256,
      general_read_model_manifest_sha256:$general_read_model_sha256,
      interactive_agent:{
        release_id:$authority_release_id,
        path:$interactive_agent_path,
        release_manifest_path:$interactive_agent_release_manifest,
        release_manifest_sha256:$interactive_agent_release_manifest_sha256,
        release_manifest_schema_version:"domeye_interactive_agent_release_manifest_v2",
        active_state_path:$interactive_agent_active,
        active_state_sha256:$interactive_agent_active_sha256,
        candidate_id:$interactive_agent_candidate_id,
        candidate_manifest_path:$interactive_agent_candidate_manifest,
        candidate_manifest_sha256:$interactive_agent_candidate_manifest_sha256,
        acceptance_record_path:$interactive_agent_acceptance_record,
        acceptance_record_id:$interactive_agent_acceptance_record_id,
        acceptance_record_sha256:$interactive_agent_acceptance_record_sha256,
        acceptance_replay_receipt_path:$interactive_agent_acceptance_replay_receipt,
        acceptance_replay_receipt_sha256:$interactive_agent_acceptance_replay_receipt_sha256,
        readiness_schema_version:"domeye_interactive_agent_release_probe_v2",
        readiness_identity_sha256:$interactive_agent_readiness_identity_sha256,
        interactive_answer_attempt_limit:$interactive_answer_attempt_limit,
        cost_policy:$interactive_agent_cost_policy,
        endpoint:{url:"http://127.0.0.1:28476",host:"127.0.0.1",port:28476,base_path:"/country-outage/chat"},
        activation_scope:"local_evaluation_only",
        candidate_production_deployed:false
      },
      created_at:$created_at,
      boundaries:{
        collector:"rrc25",
        window_start_utc:"2026-02-24T00:00:00Z",
        window_end_exclusive_utc:"2026-03-11T00:00:00Z",
        database_changed:false,
        nginx_changed:false,
        interactive_agent_bound:true,
        model_calls_during_prepare:0,
        network_rca:false
      }
    }' > "${backend_candidate}/BACKEND-SOURCE-BINDING.json"
printf '%s\n' "${SOURCE_COMMIT}" > "${backend_candidate}/GIT-COMMIT"
printf '%s\n' "${SOURCE_TAG}" > "${backend_candidate}/RELEASE-TAG"

(
    cd -- "${backend_candidate}/backend"
    sha256sum -c core.sha256
)
(
    cd -- "${backend_candidate}/frontend"
    export PATH="${NODE_BIN_DIR}:/home/bgpdata/.local/bin:/usr/local/bin:/usr/bin:/bin"
    npm ci
    npm run api:types
    npm run typecheck
    npm test -- --run
    npm run build -- --outDir "${frontend_candidate}/dist" --emptyOutDir
)
if [[ -d "${backend_candidate}/frontend/node_modules" \
    && ! -L "${backend_candidate}/frontend/node_modules" ]]; then
    find "${backend_candidate}/frontend/node_modules" -depth -delete
fi
domeye_frontend_validate_tree "${frontend_candidate}/dist"
frontend_tree_sha="$(domeye_frontend_tree_sha256 "${frontend_candidate}/dist")"
jq -n \
    --arg schema_version domeye_country_outage_general_frontend_manifest_v1 \
    --arg release_id "${FRONTEND_RELEASE_ID}" \
    --arg commit "${SOURCE_COMMIT}" \
    --arg tag "${SOURCE_TAG}" \
    --arg tree_sha256 "${frontend_tree_sha}" \
    --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{schema_version:$schema_version,release_id:$release_id,source:{commit:$commit,annotated_tag:$tag},tree_sha256:$tree_sha256,created_at:$created_at,tests:{status:"passed",command:"npm test -- --run",typecheck:"passed",build:"passed"}}' \
    > "${frontend_candidate}/FRONTEND-MANIFEST.json"
(
    cd -- "${frontend_candidate}"
    find dist -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > SHA256SUMS
    sha256sum FRONTEND-MANIFEST.json >> SHA256SUMS
)

source_manifest_sha="$(sha256sum "${source_candidate}/SOURCE-MANIFEST.json" | awk '{print $1}')"
frontend_manifest_sha="$(sha256sum "${frontend_candidate}/FRONTEND-MANIFEST.json" | awk '{print $1}')"
(
    cd -- "${backend_candidate}"
    sha256sum \
        BACKEND-SOURCE-BINDING.json \
        GIT-COMMIT \
        RELEASE-TAG \
        backend/core.sha256 \
        contracts/openapi.json \
        contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json \
        country-outage-registry.json \
        data-layer/PRODUCTION-SELECTION.json \
        data-layer/production-index.json \
        deploy/country-outage-agent/p1-chat/manage.sh \
        deploy/country-outage-agent/p1-chat/probe.mjs \
        deploy/country-outage-agent/p1-chat/verify-release.mjs \
        deploy/country-outage-general-page/activate-runtime.sh \
        deploy/country-outage-general-page/manage-runtime.sh \
        deploy/country-outage-general-page/prepare-runtime-release.sh \
        deploy/country-outage-general-page/rollback-runtime.sh \
        deploy/country-outage-general-page/verify-runtime.sh \
        deploy/lib/artifact-common.sh \
        deploy/lib/frontend-common.sh \
        general-read-model/manifest.json \
        general-read-model/COMPLETE.json \
        > SHA256SUMS
)
backend_binding_sha="$(sha256sum "${backend_candidate}/BACKEND-SOURCE-BINDING.json" | awk '{print $1}')"
backend_sums_sha="$(sha256sum "${backend_candidate}/SHA256SUMS" | awk '{print $1}')"
frontend_sums_sha="$(sha256sum "${frontend_candidate}/SHA256SUMS" | awk '{print $1}')"
previous_frontend_release_id="$(< "${FRONTEND_CURRENT_STATE}")" || {
    error '无法读取切换前 Frontend 身份'
    exit 1
}
[[ -n "${previous_frontend_release_id}" \
    && "${previous_frontend_release_id}" != *$'\n'* \
    && "${previous_frontend_release_id}" != *$'\r'* ]] || {
    error '切换前 Frontend 身份无效'
    exit 1
}

jq -n \
    --arg schema_version domeye_country_outage_general_release_candidate_v2 \
    --arg release_id "${RELEASE_ID}" \
    --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg commit "${SOURCE_COMMIT}" \
    --arg tag "${SOURCE_TAG}" \
    --arg archive_path "${SOURCE_TARGET}/artifacts/source.tar.gz" \
    --arg archive_sha256 "${SOURCE_ARCHIVE_SHA256}" \
    --arg source_path "${SOURCE_TARGET}" \
    --arg source_manifest_sha256 "${source_manifest_sha}" \
    --arg backend_release_id "${BACKEND_RELEASE_ID}" \
    --arg backend_path "${BACKEND_TARGET}" \
    --arg backend_binding_sha256 "${backend_binding_sha}" \
    --arg backend_sha256sums_sha256 "${backend_sums_sha}" \
    --arg frontend_release_id "${FRONTEND_RELEASE_ID}" \
    --arg frontend_path "${FRONTEND_TARGET}" \
    --arg frontend_manifest_sha256 "${frontend_manifest_sha}" \
    --arg frontend_tree_sha256 "${frontend_tree_sha}" \
    --arg frontend_sha256sums_sha256 "${frontend_sums_sha}" \
    --arg previous_backend_release_id "${previous_release_id}" \
    --arg previous_backend_path "${PREVIOUS_BACKEND}" \
    --arg previous_frontend_release_id "${previous_frontend_release_id}" \
    --arg database_state_sha256 "${database_state_sha}" \
    --arg nginx_main_sha256 "${nginx_main_sha}" \
    --arg nginx_site_sha256 "${nginx_site_sha}" \
    --arg data_selection_sha256 "${data_selection_sha}" \
    --arg general_read_model_manifest_sha256 "${general_manifest_sha}" \
    --arg country_outage_registry_sha256 "${registry_sha}" \
    --arg interactive_agent_release_id "${interactive_agent_release_id}" \
    --arg interactive_agent_path "${interactive_agent_path}" \
    --arg interactive_agent_release_manifest "${interactive_agent_release_manifest}" \
    --arg interactive_agent_release_manifest_sha256 "${interactive_agent_release_manifest_sha256}" \
    --arg interactive_agent_active "${INTERACTIVE_AGENT_ACTIVE}" \
    --arg interactive_agent_active_sha256 "${interactive_agent_active_sha256}" \
    --arg interactive_agent_candidate_id "${interactive_agent_candidate_id}" \
    --arg interactive_agent_candidate_manifest "${interactive_agent_candidate_manifest}" \
    --arg interactive_agent_candidate_manifest_sha256 "${interactive_agent_candidate_manifest_sha256}" \
    --arg interactive_agent_acceptance_record "${interactive_agent_acceptance_record}" \
    --arg interactive_agent_acceptance_record_id "${interactive_agent_acceptance_record_id}" \
    --arg interactive_agent_acceptance_record_sha256 "${interactive_agent_acceptance_record_sha256}" \
    --arg interactive_agent_acceptance_replay_receipt "${interactive_agent_acceptance_replay_receipt}" \
    --arg interactive_agent_acceptance_replay_receipt_sha256 "${interactive_agent_acceptance_replay_receipt_sha256}" \
    --arg interactive_agent_readiness_identity_sha256 "${interactive_agent_readiness_identity_sha256}" \
    --argjson interactive_answer_attempt_limit "${interactive_agent_answer_attempt_limit}" \
    --arg interactive_agent_cost_policy "${interactive_agent_cost_policy}" \
    '{
      schema_version:$schema_version,
      release_id:$release_id,
      status:"built",
      created_at:$created_at,
      source:{commit:$commit,annotated_tag:$tag,archive_path:$archive_path,archive_sha256:$archive_sha256,path:$source_path,manifest_sha256:$source_manifest_sha256,authority:{mode:"interactive_agent_release",release_id:$interactive_agent_release_id,commit:$commit,annotated_tag:$tag,archive_sha256:$archive_sha256,equality_verified:true}},
      components:{
        backend:{release_id:$backend_release_id,path:$backend_path,binding_sha256:$backend_binding_sha256,sha256sums_sha256:$backend_sha256sums_sha256,tests:"core and affected backend passed"},
        frontend:{release_id:$frontend_release_id,path:$frontend_path,manifest_sha256:$frontend_manifest_sha256,tree_sha256:$frontend_tree_sha256,sha256sums_sha256:$frontend_sha256sums_sha256,tests:{status:"passed",command:"npm test -- --run"},typecheck:"passed",build:"passed"}
      },
      frozen_data:{
        production_selection_sha256:$data_selection_sha256,
        general_read_model_manifest_sha256:$general_read_model_manifest_sha256,
        country_outage_registry_sha256:$country_outage_registry_sha256,
        collector:"rrc25",
        window_start_utc:"2026-02-24T00:00:00Z",
        window_end_exclusive_utc:"2026-03-11T00:00:00Z"
      },
      interactive_agent:{
        release_id:$interactive_agent_release_id,
        path:$interactive_agent_path,
        release_manifest_path:$interactive_agent_release_manifest,
        release_manifest_sha256:$interactive_agent_release_manifest_sha256,
        release_manifest_schema_version:"domeye_interactive_agent_release_manifest_v2",
        active_state_path:$interactive_agent_active,
        active_state_sha256:$interactive_agent_active_sha256,
        candidate_id:$interactive_agent_candidate_id,
        candidate_manifest_path:$interactive_agent_candidate_manifest,
        candidate_manifest_sha256:$interactive_agent_candidate_manifest_sha256,
        acceptance_record_path:$interactive_agent_acceptance_record,
        acceptance_record_id:$interactive_agent_acceptance_record_id,
        acceptance_record_sha256:$interactive_agent_acceptance_record_sha256,
        acceptance_replay_receipt_path:$interactive_agent_acceptance_replay_receipt,
        acceptance_replay_receipt_sha256:$interactive_agent_acceptance_replay_receipt_sha256,
        readiness_schema_version:"domeye_interactive_agent_release_probe_v2",
        readiness_identity_sha256:$interactive_agent_readiness_identity_sha256,
        interactive_answer_attempt_limit:$interactive_answer_attempt_limit,
        cost_policy:$interactive_agent_cost_policy,
        endpoint:{url:"http://127.0.0.1:28476",host:"127.0.0.1",port:28476,base_path:"/country-outage/chat"},
        activation_scope:"local_evaluation_only",
        candidate_production_deployed:false
      },
      protected_runtime:{database_changed:false,database_state_sha256:$database_state_sha256,nginx_changed:false,nginx_main_sha256:$nginx_main_sha256,nginx_site_sha256:$nginx_site_sha256},
      build_boundaries:{model_calls_during_prepare:0},
      cutover_baseline:{backend:{release_id:$previous_backend_release_id,path:$previous_backend_path},frontend:{release_id:$previous_frontend_release_id},purpose:"pre_cutover_identity_and_stop_only",restorable:false},
      rollback:{mode:"fail_closed",previous_release_id:null},
      promotion_contract:{candidate_canary_production_same_artifacts:true,rebuild_allowed:false}
    }' > "${unified_candidate}/CANDIDATE-MANIFEST.json"

chmod -R u=rwX,go=rX "${backend_candidate}" "${frontend_candidate}" "${source_candidate}"
chmod -R a-w "${backend_candidate}" "${frontend_candidate}" "${source_candidate}"
chmod 0750 "${unified_candidate}"
chmod 0640 "${unified_candidate}/CANDIDATE-MANIFEST.json"
mv -T -- "${source_candidate}" "${SOURCE_TARGET}"
mv -T -- "${backend_candidate}" "${BACKEND_TARGET}"
mv -T -- "${frontend_candidate}" "${FRONTEND_TARGET}"
mv -T -- "${unified_candidate}" "${UNIFIED_ROOT}"
published=true
trap - EXIT

jq -c '{release_id,status,source:.source.commit,backend:.components.backend.release_id,frontend:.components.frontend.release_id,rollback}' \
    "${UNIFIED_ROOT}/CANDIDATE-MANIFEST.json"
