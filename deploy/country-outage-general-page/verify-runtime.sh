#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly RUNTIME_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
readonly BINDING="${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json"
readonly RELEASE_ID="$(jq -er '.release_id | sub("-backend$"; "")' "${BINDING}")"
readonly UNIFIED_ROOT="/home/bgpdata/Domeye-Core-runtime/unified-releases/${RELEASE_ID}"
readonly CANDIDATE="${UNIFIED_ROOT}/CANDIDATE-MANIFEST.json"
readonly MANAGER="${RUNTIME_ROOT}/deploy/country-outage-general-page/manage-runtime.sh"
readonly INTERACTIVE_AGENT_MANAGER="${RUNTIME_ROOT}/deploy/country-outage-agent/p1-chat/manage.sh"
readonly TRUSTED_NODE='/home/bgpdata/.local/node-v22.23.1-linux-x64/bin/node'
# shellcheck source=../lib/artifact-common.sh
source "${RUNTIME_ROOT}/deploy/lib/artifact-common.sh"
# shellcheck source=../lib/frontend-common.sh
source "${RUNTIME_ROOT}/deploy/lib/frontend-common.sh"

error() {
    printf '国家中断通用观测运行时验证错误：%s\n' "$*" >&2
}

sha256_hex_file() {
    local path="$1"
    local value
    if ! value="$(sha256sum -- "${path}" | awk 'NR == 1 {print $1}')"; then
        error "无法计算文件摘要：${path}"
        return 1
    fi
    [[ "${value}" =~ ^[a-f0-9]{64}$ ]] || {
        error "文件摘要格式无效：${path}"
        return 1
    }
    printf '%s\n' "${value}"
}

trusted_git() {
    env -u GIT_DIR -u GIT_WORK_TREE -u GIT_COMMON_DIR -u GIT_INDEX_FILE \
        -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES \
        -u GIT_CONFIG -u GIT_CONFIG_COUNT -u GIT_CONFIG_PARAMETERS \
        -u GIT_NAMESPACE -u GIT_CEILING_DIRECTORIES \
        GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
        GIT_CONFIG_SYSTEM=/dev/null \
        git --no-replace-objects -C /home/bgpdata/Domeye-Core "$@"
}

if (( $# != 1 )); then
    printf '用法：%s canary|production\n' "${0##*/}" >&2
    exit 2
fi
readonly MODE="$1"
case "${MODE}" in
    canary)
        readonly BASE_URL='http://127.0.0.1:38672'
        readonly EVIDENCE="${UNIFIED_ROOT}/CANARY-VERIFICATION.json"
        ;;
    production)
        readonly BASE_URL='http://127.0.0.1:28471'
        readonly EVIDENCE="${UNIFIED_ROOT}/PRODUCTION-VERIFICATION.json"
        ;;
    *)
        error "验证模式无效：${MODE}"
        exit 2
        ;;
esac

if (( EUID != 0 )); then
    error '运行时验证必须由 root 执行'
    exit 1
fi
for command_name in base64 chmod cmp curl date dirname env find git jq mktemp mv \
    python3 readlink sha256sum sleep unlink; do
    command -v "${command_name}" >/dev/null 2>&1 || {
        error "缺少命令：${command_name}"
        exit 1
    }
done
[[ -f "${CANDIDATE}" && ! -L "${CANDIDATE}" ]] || {
    error '统一候选证据缺失'
    exit 1
}
if ! jq -e --arg release_id "${RELEASE_ID}" '
  .schema_version == "domeye_country_outage_general_release_candidate_v2"
  and .release_id == $release_id
  and .source.annotated_tag == $release_id
  and .interactive_agent.release_id == $release_id
  and .rollback == {mode:"fail_closed",previous_release_id:null}
  and .interactive_agent.endpoint == {
    url:"http://127.0.0.1:28476",
    host:"127.0.0.1",
    port:28476,
    base_path:"/country-outage/chat"
  }
' "${CANDIDATE}" >/dev/null; then
    error '统一候选不是 v2 新架构首发合同'
    exit 1
fi
if [[ -e "${EVIDENCE}" || -L "${EVIDENCE}" ]]; then
    error "验证证据已存在，create-only 拒绝覆盖：${EVIDENCE}"
    exit 1
fi

source_commit="$(jq -er '.source.commit' "${CANDIDATE}")"
source_tag="$(jq -er '.source.annotated_tag' "${CANDIDATE}")"
source_archive="$(jq -er '.source.archive_path' "${CANDIDATE}")"
source_archive_sha="$(jq -er '.source.archive_sha256' "${CANDIDATE}")"
frontend_path="$(jq -er '.components.frontend.path' "${CANDIDATE}")"
frontend_tree_sha="$(jq -er '.components.frontend.tree_sha256' "${CANDIDATE}")"
interactive_agent_release_id="$(jq -er '.interactive_agent.release_id' "${CANDIDATE}")"
interactive_agent_path="$(jq -er '.interactive_agent.path' "${CANDIDATE}")"
interactive_agent_release_manifest_sha="$(jq -er '.interactive_agent.release_manifest_sha256' "${CANDIDATE}")"
interactive_agent_active_path="$(jq -er '.interactive_agent.active_state_path' "${CANDIDATE}")"
interactive_agent_active_sha="$(jq -er '.interactive_agent.active_state_sha256' "${CANDIDATE}")"
interactive_agent_candidate_id="$(jq -er '.interactive_agent.candidate_id' "${CANDIDATE}")"
interactive_agent_candidate_path="$(jq -er '.interactive_agent.candidate_manifest_path' "${CANDIDATE}")"
interactive_agent_candidate_sha="$(jq -er '.interactive_agent.candidate_manifest_sha256' "${CANDIDATE}")"
interactive_agent_readiness_sha="$(jq -er '.interactive_agent.readiness_identity_sha256' "${CANDIDATE}")"

trusted_origin="$(trusted_git remote get-url origin 2>/dev/null || true)"
[[ "${trusted_origin}" == 'git@github.com:xinghuahewo/domeye_.git' \
    || "${trusted_origin}" == 'https://github.com/xinghuahewo/domeye_.git' ]] || {
    error '固定 checkout 的 origin 不是 GitHub 权威仓库'
    exit 1
}
[[ "$(trusted_git rev-parse refs/heads/main)" == "${source_commit}" \
    && "$(trusted_git rev-parse refs/remotes/origin/main)" \
        == "${source_commit}" ]] || {
    error '生产主干与候选提交不一致'
    exit 1
}
[[ "$(trusted_git cat-file -t "${source_tag}")" == tag \
    && "$(trusted_git rev-parse "${source_tag}^{}")" == "${source_commit}" ]] || {
    error 'annotated tag 与候选提交不一致'
    exit 1
}
if [[ "sha256:$(sha256sum "${source_archive}" | awk '{print $1}')" \
    != "${source_archive_sha}" ]]; then
    error 'Source 归档摘要相对统一候选漂移'
    exit 1
fi
if ! (
    cd -- "${RUNTIME_ROOT}"
    sha256sum -c SHA256SUMS >/dev/null
    cd backend
    sha256sum -c core.sha256 >/dev/null
); then
    error 'Backend 冻结制品摘要校验失败'
    exit 1
fi
if ! (
    cd -- "${frontend_path}"
    sha256sum -c SHA256SUMS >/dev/null
); then
    error 'Frontend 冻结制品摘要校验失败'
    exit 1
fi
if [[ "$(domeye_frontend_tree_sha256 "${frontend_path}/dist")" \
    != "${frontend_tree_sha}" ]]; then
    error 'Frontend 候选树摘要漂移'
    exit 1
fi
if ! cmp -s "${RUNTIME_ROOT}/general-read-model/manifest.json" \
    "${RUNTIME_ROOT}/general-read-model/COMPLETE.json"; then
    error '通用读模型 manifest 与 COMPLETE 不一致'
    exit 1
fi
if [[ "$(sha256sum /home/bgpdata/Domeye-Core-dev-data/state.json | awk '{print $1}')" \
    != "$(jq -er '.protected_runtime.database_state_sha256' "${CANDIDATE}")" \
    || "$(sha256sum /etc/nginx/nginx.conf | awk '{print $1}')" \
        != "$(jq -er '.protected_runtime.nginx_main_sha256' "${CANDIDATE}")" \
    || "$(sha256sum /etc/nginx/conf.d/domeye-core.conf | awk '{print $1}')" \
        != "$(jq -er '.protected_runtime.nginx_site_sha256' "${CANDIDATE}")" ]]; then
    error '数据库或 Nginx 摘要相对候选漂移'
    exit 1
fi
[[ -x "${INTERACTIVE_AGENT_MANAGER}" && -x "${TRUSTED_NODE}" \
    && -d "${interactive_agent_path}" && ! -L "${interactive_agent_path}" \
    && -f "${interactive_agent_path}/RELEASE-MANIFEST.json" \
    && ! -L "${interactive_agent_path}/RELEASE-MANIFEST.json" \
    && -f "${interactive_agent_active_path}" \
    && ! -L "${interactive_agent_active_path}" \
    && -f "${interactive_agent_candidate_path}" \
    && ! -L "${interactive_agent_candidate_path}" ]] || {
    error 'Interactive Agent 冻结文件或受信发布工具缺失'
    exit 1
}
[[ "sha256:$(sha256sum "${interactive_agent_path}/RELEASE-MANIFEST.json" | awk '{print $1}')" \
    == "${interactive_agent_release_manifest_sha}" \
    && "sha256:$(sha256sum "${interactive_agent_active_path}" | awk '{print $1}')" \
        == "${interactive_agent_active_sha}" \
    && "sha256:$(sha256sum "${interactive_agent_candidate_path}" | awk '{print $1}')" \
        == "${interactive_agent_candidate_sha}" ]] || {
    error 'Interactive Agent release/active/Candidate 摘要相对候选漂移'
    exit 1
}
[[ "$(jq -er '.candidate_id' "${interactive_agent_candidate_path}")" \
    == "${interactive_agent_candidate_id}" ]] || {
    error 'Interactive Agent Candidate ID 相对候选漂移'
    exit 1
}
[[ "$(readlink -f /home/bgpdata/Domeye-Core-runtime/country-outage-interactive-agent/current)" \
    == "${interactive_agent_path}" ]] || {
    error 'Interactive Agent current 相对候选漂移'
    exit 1
}
interactive_agent_status=''
if ! interactive_agent_status="$("${INTERACTIVE_AGENT_MANAGER}" status)"; then
    error 'Interactive Agent 当前组合状态无效'
    exit 1
fi
if ! jq -e --arg release_id "${interactive_agent_release_id}" \
    --arg candidate_id "${interactive_agent_candidate_id}" '
      .schema_version == "domeye_interactive_agent_release_probe_v1"
      and .ready == true
      and .release_id == $release_id
      and .candidate_id == $candidate_id
      and .candidate_activation_scope == "local_evaluation_only"
      and .candidate_production_deployed == false
      and .current_target_matches == true
      and .deployment_active == true
      and .lifecycle_state == "deployed"
      and .promotion_state == "absent"
      and .production_verified == false
    ' <<<"${interactive_agent_status}" >/dev/null; then
    error 'Interactive Agent 不是尚未生产晋级的 deployed 状态'
    exit 1
fi
interactive_agent_readiness_identity="$(jq -cS '{
  schema_version,ready,component,release_id,release_manifest_sha256,
  candidate_id,candidate_activation_scope,candidate_production_deployed
}' <<<"${interactive_agent_status}")"
[[ "sha256:$(printf '%s' "${interactive_agent_readiness_identity}" | sha256sum | awk '{print $1}')" \
    == "${interactive_agent_readiness_sha}" ]] || {
    error 'Interactive Agent readiness 身份相对候选漂移'
    exit 1
}
DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE="${MODE}" "${MANAGER}" status >/dev/null
if [[ "${MODE}" == production ]]; then
    [[ "$(readlink -f /home/bgpdata/Domeye-Core-runtime/current)" == "${RUNTIME_ROOT}" \
        && "$(< /home/bgpdata/Domeye-Core-runtime/web/state/frontend-current)" \
            == "$(jq -er '.components.frontend.release_id' "${CANDIDATE}")" ]] || {
        error '生产 Backend/Frontend 指针未绑定同一候选'
        exit 1
    }
    [[ -d "${DOMEYE_CORE_FRONTEND_TARGET}" \
        && ! -L "${DOMEYE_CORE_FRONTEND_TARGET}" \
        && "$(domeye_frontend_tree_sha256 "${DOMEYE_CORE_FRONTEND_TARGET}")" \
            == "${frontend_tree_sha}" ]] || {
        error '生产 Frontend 实际目录未绑定候选树摘要'
        exit 1
    }
fi

backend_request() {
    local method="$1"
    local url="$2"
    local body="${3:-}"
    local -a arguments=(
        --fail-with-body --silent --show-error --max-time 125
        --request "${method}" --header 'Accept: application/json'
    )
    if [[ -n "${body}" ]]; then
        arguments+=(--header 'Content-Type: application/json' --data-binary "@${body}")
    fi
    curl "${arguments[@]}" "${url}"
}

verify_canary_answer() {
    local working_root="$1"
    local output="$2"
    local release_manifest="${interactive_agent_path}/RELEASE-MANIFEST.json"
    local verifier="${interactive_agent_path}/deployment/verify-release.mjs"
    local trusted_verifier="${RUNTIME_ROOT}/deploy/country-outage-agent/p1-chat/verify-release.mjs"
    [[ -f "${verifier}" && ! -L "${verifier}" \
        && -f "${trusted_verifier}" && ! -L "${trusted_verifier}" \
        && "$(readlink -f -- "${verifier}")" == "${verifier}" ]] || {
        error 'canary 受信 release verifier 缺失'
        return 1
    }
    if ! cmp -s "${verifier}" "${trusted_verifier}"; then
        error 'canary release verifier 与本次 General Source 不一致'
        return 1
    fi

    local api_base="${BASE_URL}/api/v2/country-outage/chat/"
    local event_reference publication revision question request_id
    event_reference="$(jq -er '.live_verification.event_reference' \
        "${release_manifest}")" || return 1
    question="$(jq -er '.live_verification.question' \
        "${release_manifest}")" || return 1
    publication="$(jq -er '.payload.data_identity.publication_id' \
        "${interactive_agent_candidate_path}")" || return 1
    revision="$(jq -er '.payload.data_identity.revision' \
        "${interactive_agent_candidate_path}")" || return 1
    request_id="canary-${RELEASE_ID}-$(date -u +%s)-${RANDOM}"

    local create_body="${working_root}/canary-create-request.json"
    local create_response="${working_root}/canary-create-response.json"
    local turn_body="${working_root}/canary-turn-request.json"
    local turn_response="${working_root}/canary-turn-response.json"
    local final_response="${working_root}/canary-final-response.json"
    local validation="${working_root}/canary-validation.json"
    if ! jq -n --arg reference "${event_reference}" \
        --arg publication "${publication}" --argjson revision "${revision}" \
        --arg key "${request_id}-create" \
        '{event_reference:$reference,publication_id:$publication,revision:$revision,idempotency_key:$key}' \
        > "${create_body}"; then
        error '无法构造 canary 固定会话请求'
        return 1
    fi
    if ! backend_request POST "${api_base}conversations" \
        "${create_body}" > "${create_response}"; then
        error 'canary Backend 创建会话失败'
        return 1
    fi
    local conversation_id turn_id
    conversation_id="$(jq -er '.conversation.conversation_id' \
        "${create_response}")" || {
        error 'canary 创建响应缺少 conversation_id'
        return 1
    }
    [[ "${conversation_id}" =~ ^conversation_sha256_[a-f0-9]{64}$ ]] || {
        error 'canary conversation_id 不是新架构身份'
        return 1
    }
    if ! jq -n --arg question "${question}" \
        --arg key "${request_id}-turn" \
        '{question:$question,idempotency_key:$key}' > "${turn_body}"; then
        error '无法构造 canary 固定问题 Turn'
        return 1
    fi
    if ! backend_request POST \
        "${api_base}conversations/${conversation_id}/turns" \
        "${turn_body}" > "${turn_response}"; then
        error 'canary Backend 创建 Turn 失败'
        return 1
    fi
    turn_id="$(jq -er '.turn.turn_id' "${turn_response}")" || {
        error 'canary Turn 响应缺少 turn_id'
        return 1
    }
    [[ "${turn_id}" =~ ^turn_sha256_[a-f0-9]{64}$ ]] || {
        error 'canary turn_id 不是新架构身份'
        return 1
    }
    if ! jq -e --arg turn_id "${turn_id}" --arg question "${question}" '
      .turn.turn_id == $turn_id and .turn.question == $question
    ' "${turn_response}" >/dev/null; then
        error 'canary Turn 响应未绑定本次固定问题'
        return 1
    fi

    local deadline state current_epoch
    current_epoch="$(date -u +%s)" || return 1
    deadline=$(( current_epoch + 125 ))
    while true; do
        if ! backend_request GET \
            "${api_base}conversations/${conversation_id}" \
            > "${final_response}"; then
            error 'canary Backend 获取最终会话失败'
            return 1
        fi
        if ! jq -e --arg conversation_id "${conversation_id}" \
            --arg turn_id "${turn_id}" '
          .conversation.conversation_id == $conversation_id
          and ([.conversation.turns[]? | select(.turn_id == $turn_id)] | length) == 1
        ' "${final_response}" >/dev/null; then
            error 'canary 最终响应未精确绑定本次 conversation/turn'
            return 1
        fi
        state="$(jq -er --arg turn_id "${turn_id}" '
          .conversation.turns[] | select(.turn_id == $turn_id) | .state
        ' "${final_response}")" || return 1
        [[ "${state}" == executing ]] || break
        current_epoch="$(date -u +%s)" || return 1
        (( current_epoch < deadline )) || {
            error 'canary 固定问题等待超时；未形成完成证据'
            return 1
        }
        if ! sleep 1; then
            error 'canary 固定问题等待被中断'
            return 1
        fi
    done

    local verified_at
    verified_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)" || return 1
    if ! "${TRUSTED_NODE}" "${verifier}" promotion \
        "${interactive_agent_path}" "${interactive_agent_active_path}" \
        "${final_response}" "${verified_at}" \
        "${conversation_id}" "${turn_id}" > "${validation}"; then
        error 'canary 回答未通过 release 内 Renderer + Guard + Oracle/trace/model 完整重放'
        return 1
    fi
    if ! jq -e '
      .promotion_state == "verified"
      and .result.state == "completed"
      and .result.answer_success == true
      and .result.workflow_completed == true
      and .result.answer_source == "renderer"
      and .result.guard_decision == "pass"
      and .result.public_answer_present == true
      and .result.fallback_or_rejection_present == false
    ' "${validation}" >/dev/null; then
        error 'canary verifier 输出不是直接 Renderer 正确回答'
        return 1
    fi
    local response_hex validation_hex response_sha validation_sha
    local validation_body_base64
    if ! response_hex="$(sha256_hex_file "${final_response}")"; then
        error '无法冻结 canary Backend 原始响应摘要'
        return 1
    fi
    if ! validation_hex="$(sha256_hex_file "${validation}")"; then
        error '无法冻结 canary verifier 回执摘要'
        return 1
    fi
    response_sha="sha256:${response_hex}"
    validation_sha="sha256:${validation_hex}"
    validation_body_base64="$(base64 -w 0 "${validation}")" || {
        error '无法冻结 canary verifier 原始回执'
        return 1
    }
    if ! jq -n --arg base_url "${BASE_URL}" \
        --arg release_id "${interactive_agent_release_id}" \
        --arg candidate_id "${interactive_agent_candidate_id}" \
        --arg conversation_id "${conversation_id}" --arg turn_id "${turn_id}" \
        --arg question "${question}" --arg response_sha "${response_sha}" \
        --arg validation_sha "${validation_sha}" \
        --arg validation_body_base64 "${validation_body_base64}" \
        --slurpfile proof "${validation}" '
      {
        status:"canary_verified",
        base_url:$base_url,
        release_id:$release_id,
        candidate_id:$candidate_id,
        conversation_id:$conversation_id,
        turn_id:$turn_id,
        question:$question,
        response_sha256:$response_sha,
        validation_sha256:$validation_sha,
        validation_receipt_body_base64:$validation_body_base64,
        validation_receipt:$proof[0],
        answer_source:$proof[0].result.answer_source,
        guard_decision:$proof[0].result.guard_decision,
        oracle_digest:$proof[0].result.oracle_digest,
        public_answer_present:$proof[0].result.public_answer_present,
        fallback_or_rejection_present:$proof[0].result.fallback_or_rejection_present,
        validation:{
          state:$proof[0].result.state,
          answer_success:$proof[0].result.answer_success,
          workflow_completed:$proof[0].result.workflow_completed,
          answer_source:$proof[0].result.answer_source,
          guard_decision:$proof[0].result.guard_decision,
          oracle_digest:$proof[0].result.oracle_digest,
          public_answer_present:$proof[0].result.public_answer_present,
          fallback_or_rejection_present:$proof[0].result.fallback_or_rejection_present
        }
      }
    ' > "${output}"; then
        error '无法生成 canary 正确回答证据'
        return 1
    fi
}

promote_production_answer() {
    local working_root="$1"
    local output="$2"
    if ! "${INTERACTIVE_AGENT_MANAGER}" promote \
        "${interactive_agent_release_id}"; then
        error 'Interactive Agent 公开固定问题晋级失败'
        return 1
    fi
    local status_file="${working_root}/interactive-agent-production-status.json"
    if ! "${INTERACTIVE_AGENT_MANAGER}" status > "${status_file}"; then
        error 'Interactive Agent 晋级后的组合状态无效'
        return 1
    fi
    if ! jq -e --arg release_id "${interactive_agent_release_id}" \
        --arg candidate_id "${interactive_agent_candidate_id}" '
      .schema_version == "domeye_interactive_agent_release_probe_v1"
      and .ready == true
      and .release_id == $release_id
      and .candidate_id == $candidate_id
      and .lifecycle_state == "verified"
      and .promotion_state == "verified"
      and .production_verified == true
    ' "${status_file}" >/dev/null; then
        error 'Interactive Agent status 未证明 production_verified == true'
        return 1
    fi
    local interactive_runtime_root promotion_file
    interactive_runtime_root="$(dirname -- "$(dirname -- "${interactive_agent_path}")")"
    promotion_file="${interactive_runtime_root}/state/promotions/${interactive_agent_release_id}.json"
    [[ -f "${promotion_file}" && ! -L "${promotion_file}" ]] || {
        error 'Interactive Agent verified promotion 回执缺失'
        return 1
    }
    local status_hex promotion_hex status_sha promotion_sha
    local promotion_body_base64
    if ! status_hex="$(sha256_hex_file "${status_file}")"; then
        error '无法冻结 production manager status 摘要'
        return 1
    fi
    if ! promotion_hex="$(sha256_hex_file "${promotion_file}")"; then
        error '无法冻结 production promotion 回执摘要'
        return 1
    fi
    status_sha="sha256:${status_hex}"
    promotion_sha="sha256:${promotion_hex}"
    promotion_body_base64="$(base64 -w 0 "${promotion_file}")" || {
        error '无法冻结生产 promotion 原始回执'
        return 1
    }
    if ! jq -n --arg base_url "${BASE_URL}" \
        --arg release_id "${interactive_agent_release_id}" \
        --arg candidate_id "${interactive_agent_candidate_id}" \
        --arg status_sha "${status_sha}" --arg promotion_sha "${promotion_sha}" \
        --arg promotion_body_base64 "${promotion_body_base64}" \
        --slurpfile status "${status_file}" --slurpfile promotion "${promotion_file}" '
      {
        status:"production_verified",
        base_url:$base_url,
        release_id:$release_id,
        candidate_id:$candidate_id,
        manager_status_sha256:$status_sha,
        promotion_receipt_sha256:$promotion_sha,
        promotion_receipt_body_base64:$promotion_body_base64,
        promotion_receipt:$promotion[0],
        lifecycle_state:$status[0].lifecycle_state,
        production_verified:$status[0].production_verified,
        conversation_id:$promotion[0].backend.conversation_id,
        turn_id:$promotion[0].backend.turn_id,
        question:$promotion[0].backend.question,
        response_sha256:$promotion[0].backend.response_sha256,
        answer_source:$promotion[0].result.answer_source,
        guard_decision:$promotion[0].result.guard_decision,
        oracle_digest:$promotion[0].result.oracle_digest,
        public_answer_present:$promotion[0].result.public_answer_present,
        fallback_or_rejection_present:$promotion[0].result.fallback_or_rejection_present
      }
    ' > "${output}"; then
        error '无法生成生产正确回答证据'
        return 1
    fi
}

working_directory="$(mktemp -d "${UNIFIED_ROOT}/.${MODE}-verification.XXXXXX")"
runtime_receipt="${working_directory}/runtime.json"
answer_receipt="${working_directory}/answer.json"
temporary="${UNIFIED_ROOT}/.${MODE}-verification.tmp.$$"
cleanup_verification() {
    local exit_code=$?
    local cleanup_failed=false
    if [[ -e "${temporary}" || -L "${temporary}" ]]; then
        if ! unlink "${temporary}"; then
            cleanup_failed=true
        fi
    fi
    if [[ -d "${working_directory}" && ! -L "${working_directory}" ]]; then
        if ! find "${working_directory}" -depth -delete; then
            cleanup_failed=true
        fi
    elif [[ -e "${working_directory}" || -L "${working_directory}" ]]; then
        cleanup_failed=true
    fi
    if [[ "${cleanup_failed}" == true ]]; then
        error "验证临时原始响应清理失败，保留路径供审计：${working_directory}"
        return 70
    fi
    return "${exit_code}"
}
trap cleanup_verification EXIT

python3 - "${BASE_URL}" "${MODE}" "${RELEASE_ID}" "${runtime_receipt}" <<'PY'
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

base_url, mode, release_id, output_path = sys.argv[1:]
references = {
    "IR": "country_outage/2026-02-27 09:12:32/IR/1/r",
    "MW": "country_outage/2026-03-09 22:09:38/MW/2/r",
}


def fetch(path: str, timeout_seconds: int = 30) -> tuple[dict[str, Any], int, float, str]:
    started = time.perf_counter()
    request = Request(base_url + path, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read()
        etag = response.headers.get("ETag", "")
    elapsed = (time.perf_counter() - started) * 1000
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    return payload, len(raw), elapsed, etag


def get_event(country: str, reference: str) -> dict[str, Any]:
    resolution, resolution_size, resolution_ms, resolution_etag = fetch(
        "/api/v2/events/resolve?" + urlencode({"ref": reference})
    )
    assert resolution["schema_version"] == "country_outage_general_resolution_v1"
    assert resolution["country_code"] == country
    incident = quote(resolution["incident_id"], safe="")
    publication = resolution["publication_id"]
    paths = {
        "overview": f"/api/v2/country-outages/{incident}/overview?" + urlencode({"publication_id": publication}),
        "series": f"/api/v2/country-outages/{incident}/series?" + urlencode({"publication_id": publication}),
        "asns": f"/api/v2/country-outages/{incident}/asns?" + urlencode({"publication_id": publication, "page": 1, "page_size": 20}),
        "paths": f"/api/v2/country-outages/{incident}/path-downstreams?" + urlencode({"publication_id": publication, "page": 1, "page_size": 15}),
    }
    payloads: dict[str, Any] = {}
    sizes = [resolution_size]
    latencies = [resolution_ms]
    etags = [resolution_etag]
    for name, path in paths.items():
        payload, size, elapsed, etag = fetch(path)
        payloads[name] = payload
        sizes.append(size)
        latencies.append(elapsed)
        etags.append(etag)
    assert payloads["overview"]["schema_version"] == "country_outage_general_overview_v1"
    assert payloads["series"]["schema_version"] == "country_outage_general_series_v1"
    assert payloads["asns"]["schema_version"] == "country_outage_general_affected_as_page_v1"
    assert payloads["paths"]["schema_version"] == "country_outage_general_path_downstream_page_v1"
    identities = {
        (payload["incident_id"], payload["publication_id"], payload["revision"], payload["window_start_utc"], payload["window_end_utc"])
        for payload in payloads.values()
    }
    assert len(identities) == 1
    assert all(etags)
    assert payloads["asns"]["page_size"] == 20
    assert payloads["paths"]["page_size"] == 15
    digest = hashlib.sha256(
        json.dumps(
            {"resolution": resolution, **payloads},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "country": country,
        "reference": reference,
        "incident_id": resolution["incident_id"],
        "publication_id": publication,
        "digest": digest,
        "state_points": payloads["series"]["point_count"],
        "affected_as_total": payloads["asns"]["total"],
        "path_total": payloads["paths"]["total"],
        "max_response_bytes": max(sizes),
        "latencies_ms": latencies,
    }


first = {country: get_event(country, ref) for country, ref in references.items()}
second = {country: get_event(country, ref) for country, ref in reversed(list(references.items()))}
assert {key: value["digest"] for key, value in first.items()} == {
    key: value["digest"] for key, value in second.items()
}
jobs = [(country, ref) for _ in range(4) for country, ref in references.items()]
with ThreadPoolExecutor(max_workers=8) as executor:
    concurrent = list(executor.map(lambda item: get_event(*item), jobs))
for item in concurrent:
    assert item["digest"] == first[item["country"]]["digest"]

as_window_path = "/api/v1/features/ases/overview?" + urlencode({
    "start_time": "2026-02-27 08:10:00",
    "end_time": "2026-03-11 08:00:00",
    "asn": "48715",
    "limit": 6,
    "event_window": "true",
    "event_reference": references["IR"],
})
as_window, as_window_size, as_window_ms, _ = fetch(
    as_window_path, timeout_seconds=125
)
assert as_window["scope_kind"] == "event_window_selected_asn"
assert as_window["scope_size"] == 1
assert as_window["start_time"] == "2026-02-27 08:10:00"
assert as_window["end_time"] == "2026-03-11 08:00:00"
assert as_window["selected_asn"]["asn"] == "48715"
assert len(as_window["selected_asn"]["series"]) == 540

ir = first["IR"]
mw = first["MW"]
assert (ir["state_points"], ir["affected_as_total"], ir["path_total"]) == (3455, 525, 1956)
assert (mw["state_points"], mw["affected_as_total"], mw["path_total"]) == (57, 8, 18)

wrong_publication_path = (
    f"/api/v2/country-outages/{quote(ir['incident_id'], safe='')}/overview?"
    + urlencode({"publication_id": "country_outage_publication_v1_wrong"})
)
try:
    fetch(wrong_publication_path)
    raise AssertionError("错误 publication 未失败关闭")
except HTTPError as error:
    assert error.code == 404
invalid_scope_path = (
    f"/api/v2/country-outages/{quote(ir['incident_id'], safe='')}/path-downstreams?"
    + urlencode({"publication_id": ir["publication_id"], "scope": "dependency"})
)
try:
    fetch(invalid_scope_path)
    raise AssertionError("非法路径语义未失败关闭")
except HTTPError as error:
    assert error.code == 400
wrong_as_window_path = "/api/v1/features/ases/overview?" + urlencode({
    "start_time": "2026-02-27 08:15:00",
    "end_time": "2026-03-11 08:00:00",
    "asn": "48715",
    "event_window": "true",
    "event_reference": references["IR"],
})
try:
    fetch(wrong_as_window_path)
    raise AssertionError("错误 AS 事件窗口未失败关闭")
except HTTPError as error:
    assert error.code == 400

latencies = [value for event in [*first.values(), *second.values(), *concurrent] for value in event["latencies_ms"]]
latencies.append(as_window_ms)
latencies_sorted = sorted(latencies)
p95 = latencies_sorted[max(0, int(len(latencies_sorted) * 0.95) - 1)]
max_response_bytes = max(
    as_window_size,
    *(event["max_response_bytes"] for event in first.values()),
)
assert max_response_bytes < 1_000_000
assert p95 < 2_000

result = {
    "schema_version": "country_outage_general_runtime_verification_v1",
    "status": "passed",
    "mode": mode,
    "release_id": release_id,
    "base_url": base_url,
    "events": {key: {k: v for k, v in value.items() if k != "latencies_ms"} for key, value in first.items()},
    "repeat_order_concurrent_equal": True,
    "concurrent_runs": len(concurrent),
    "as_event_window": {
        "asn": 48715,
        "scope_kind": as_window["scope_kind"],
        "series_points": len(as_window["selected_asn"]["series"]),
        "response_bytes": as_window_size,
    },
    "failure_closed": {
        "wrong_publication_http": 404,
        "invalid_path_scope_http": 400,
        "wrong_as_event_window_http": 400,
    },
    "performance": {
        "sample_count": len(latencies),
        "p95_ms": round(p95, 3),
        "max_ms": round(max(latencies), 3),
        "max_response_bytes": max_response_bytes,
    },
    "boundaries": {
        "collector": "rrc25",
        "window": "[2026-02-24T00:00:00Z,2026-03-11T00:00:00Z)",
        "database_changed": False,
        "nginx_changed": False,
        "read_api_checks_model_calls": 0,
        "interactive_agent_bound": True,
    },
}
path = Path(output_path)
path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
path.chmod(0o640)
print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
PY
if [[ "${MODE}" == canary ]]; then
    if ! verify_canary_answer "${working_directory}" "${answer_receipt}"; then
        error 'canary 未形成 correct direct Renderer + Guard 完整回答'
        exit 1
    fi
    verification_status='canary_verified'
else
    if ! promote_production_answer "${working_directory}" "${answer_receipt}"; then
        error 'production 未形成 verified promotion；拒绝完成发布'
        exit 1
    fi
    verification_status='production_verified'
fi
if ! jq -n --arg status "${verification_status}" \
    --arg mode "${MODE}" --arg release_id "${RELEASE_ID}" \
    --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --slurpfile runtime "${runtime_receipt}" \
    --slurpfile answer "${answer_receipt}" '
  {
    schema_version:"domeye_country_outage_general_runtime_verification_v2",
    status:$status,
    mode:$mode,
    release_id:$release_id,
    created_at:$created_at,
    deterministic_runtime:$runtime[0],
    interactive_answer:$answer[0]
  }
' > "${temporary}"; then
    error '无法生成最终运行时验证证据'
    exit 1
fi
chmod 0640 "${temporary}"
if ! mv -n -- "${temporary}" "${EVIDENCE}"; then
    error '无法原子写入 create-only 验证证据'
    exit 1
fi
[[ ! -e "${temporary}" && ! -L "${temporary}" \
    && -f "${EVIDENCE}" && ! -L "${EVIDENCE}" ]] || {
    error '验证证据已存在或原子写入未闭合'
    exit 1
}
trap - EXIT
if ! cleanup_verification; then
    exit 70
fi
jq -c '{release_id,status,mode,interactive_answer:(.interactive_answer | {conversation_id,turn_id,answer_source,guard_decision})}' \
    "${EVIDENCE}"
