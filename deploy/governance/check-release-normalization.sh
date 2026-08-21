#!/usr/bin/env bash

set -Eeuo pipefail

die() {
    printf '发布归一检查失败：%s\n' "$*" >&2
    exit 1
}

valid_release_id() {
    [[ "$1" =~ ^[0-9]{8}T[0-9]{6}Z-[a-z0-9._-]+$ ]]
}

require_commands() {
    local command_name
    for command_name in "$@"; do
        command -v "${command_name}" >/dev/null 2>&1 \
            || die "缺少命令：${command_name}"
    done
}

require_regular_file() {
    local path="$1"
    local label="$2"
    [[ -f "${path}" && ! -L "${path}" ]] \
        || die "${label}不是实际普通文件：${path}"
}

require_actual_directory() {
    local path="$1"
    local label="$2"
    [[ -d "${path}" && ! -L "${path}" \
        && "$(readlink -f -- "${path}")" == "${path}" ]] \
        || die "${label}不是规范实际目录：${path}"
}

require_port_closed() {
    local port="$1"
    local label="$2"
    local sockets
    sockets="$(ss -H -ltn "sport = :${port}")" \
        || die "无法读取${label}监听状态"
    [[ -z "${sockets}" ]] || die "${label}仍在监听 127.0.0.1:${port}"
}

require_screen_absent() {
    local screen_name="$1"
    local label="$2"
    local sessions
    sessions="$(
        (screen -ls 2>/dev/null || true) \
            | awk -v suffix=".${screen_name}" '
                $1 ~ /^[0-9]+\./ \
                    && substr($1, length($1) - length(suffix) + 1) == suffix {
                    print $1
                }
            '
    )"
    [[ -z "${sessions}" ]] || die "${label} Screen 仍在运行"
}

require_public_path_retired() {
    local path="$1"
    local label="$2"
    local status
    status="$(curl --disable --noproxy '*' --proto '=http' --max-redirs 0 \
        -sS -o /dev/null -w '%{http_code}' --max-time 5 \
        "http://127.0.0.1:28471${path}")" \
        || die "无法读取${label}退役状态"
    [[ "${status}" == '404' ]] || die "${label}仍可公开路由：HTTP ${status}"
}

sha256_hex() {
    local value
    value="$(sha256sum -- "$1" | awk 'NR == 1 {print $1}')" \
        || die "无法计算 SHA-256：$1"
    [[ "${value}" =~ ^[a-f0-9]{64}$ ]] \
        || die "SHA-256 格式无效：$1"
    printf '%s\n' "${value}"
}

json_value() {
    local path="$1"
    local filter="$2"
    local label="$3"
    local value
    value="$(jq -er "${filter}" "${path}")" \
        || die "无法读取${label}：${path}"
    printf '%s\n' "${value}"
}

frontend_tree_sha256() {
    local tree_path="$1"
    local symlink_path unexpected_path
    require_actual_directory "${tree_path}" 'Frontend 树'
    require_regular_file "${tree_path}/index.html" 'Frontend index.html '
    symlink_path="$(find "${tree_path}" -type l -print -quit)"
    [[ -z "${symlink_path}" ]] \
        || die "Frontend 树包含符号链接：${symlink_path}"
    unexpected_path="$(find "${tree_path}" ! -type d ! -type f -print -quit)"
    [[ -z "${unexpected_path}" ]] \
        || die "Frontend 树包含非常规对象：${unexpected_path}"
    (
        cd -- "${tree_path}"
        find . -type f -print0 \
            | LC_ALL=C sort -z \
            | while IFS= read -r -d '' relative_path; do
                printf '%s\0' "${relative_path#./}"
                printf '%s\0' "$(sha256_hex "${relative_path}")"
            done
    ) | sha256sum | awk '{print $1}'
}

validate_embedded_base64_json() {
    local evidence="$1"
    local body_filter="$2"
    local digest_filter="$3"
    local object_filter="$4"
    local label="$5"
    local body expected_sha actual_sha reencoded

    body="$(jq -er "${body_filter}" "${evidence}")" \
        || die "${label}缺少冻结原始字节"
    expected_sha="$(jq -er "${digest_filter}" "${evidence}")" \
        || die "${label}缺少冻结摘要"
    [[ "${expected_sha}" =~ ^sha256:[a-f0-9]{64}$ ]] \
        || die "${label}冻结摘要格式无效"
    actual_sha="$(printf '%s' "${body}" | base64 --decode \
        | sha256sum | awk 'NR == 1 {print $1}')" \
        || die "${label}不是有效 base64"
    [[ "sha256:${actual_sha}" == "${expected_sha}" ]] \
        || die "${label}冻结字节摘要不一致"
    reencoded="$(printf '%s' "${body}" | base64 --decode \
        | base64 | tr -d '\n')" || die "${label}无法重编码"
    [[ "${reencoded}" == "${body}" ]] \
        || die "${label}不是规范 base64"
    if [[ -n "${object_filter}" ]] \
        && ! printf '%s' "${body}" | base64 --decode \
            | jq -e --slurpfile evidence "${evidence}" \
                ". == (\$evidence[0] | ${object_filter})" >/dev/null; then
        die "${label}冻结对象与投影不一致"
    elif [[ -z "${object_filter}" ]] \
        && ! printf '%s' "${body}" | base64 --decode \
            | jq -e . >/dev/null; then
        die "${label}冻结字节不是有效 JSON"
    fi
}

validate_interactive_answer_raw_evidence() {
    local evidence="$1"
    local label="$2"

    validate_embedded_base64_json "${evidence}" \
        '.interactive_answer.promotion_receipt_body_base64' \
        '.interactive_answer.promotion_receipt_sha256' \
        '.interactive_answer.promotion_receipt' \
        "${label} promotion 回执"
    validate_embedded_base64_json "${evidence}" \
        '.interactive_answer.promotion_receipt.public_response.create_response_body_base64' \
        '.interactive_answer.promotion_receipt.public_response.create_response_sha256' \
        '' \
        "${label}创建响应"
    validate_embedded_base64_json "${evidence}" \
        '.interactive_answer.promotion_receipt.public_response.turn_response_body_base64' \
        '.interactive_answer.promotion_receipt.public_response.turn_response_sha256' \
        '' \
        "${label} Turn 响应"
    validate_embedded_base64_json "${evidence}" \
        '.interactive_answer.promotion_receipt.public_response.response_body_base64' \
        '.interactive_answer.promotion_receipt.public_response.response_sha256' \
        '' \
        "${label}最终公开响应"
    validate_embedded_base64_json "${evidence}" \
        '.interactive_answer.promotion_receipt.internal_record.response_body_base64' \
        '.interactive_answer.promotion_receipt.internal_record.response_sha256' \
        '' \
        "${label}内部记录响应"
}

validate_release_contracts() {
    local release_id="$1"
    local candidate="$2"
    local deployment="$3"
    local state="$4"
    local canary_verification="$5"
    local production_verification="$6"
    local canary_verification_sha production_verification_sha

    valid_release_id "${release_id}" || die 'release-id 格式无效'
    require_regular_file "${candidate}" 'CANDIDATE-MANIFEST.json '
    require_regular_file "${deployment}" 'DEPLOYMENT.json '
    require_regular_file "${state}" 'ACTIVATION-STATE.json '
    require_regular_file "${canary_verification}" 'CANARY-VERIFICATION.json '
    require_regular_file "${production_verification}" 'PRODUCTION-VERIFICATION.json '
    canary_verification_sha="sha256:$(sha256_hex "${canary_verification}")"
    production_verification_sha="sha256:$(sha256_hex "${production_verification}")"

    jq -e --arg release_id "${release_id}" \
        --arg canary_verification_sha "${canary_verification_sha}" \
        --arg production_verification_sha "${production_verification_sha}" \
        --slurpfile deployment "${deployment}" \
        --slurpfile state "${state}" \
        --slurpfile canary "${canary_verification}" \
        --slurpfile production "${production_verification}" '
      def exact_keys($required; $optional):
        (type == "object")
        and ((($required - keys_unsorted) | length) == 0)
        and (((keys_unsorted - ($required + $optional)) | length) == 0);
      def sha256_digest:
        type == "string" and test("^sha256:[a-f0-9]{64}$");
      def promotion_contract($receipt; $candidate):
        ($receipt | exact_keys([
          "promotion_id","schema_version","component","release_id",
          "promotion_state","verified_at_utc","release_manifest_sha256",
          "active_receipt_sha256","candidate_id","acceptance_record_id",
          "public_response","internal_record","result"
        ]; []))
        and ($receipt.public_response | exact_keys([
          "origin","base_path","conversation_id","turn_id","question",
          "create_response_sha256","create_response_body_base64",
          "turn_response_sha256","turn_response_body_base64",
          "response_sha256","response_body_base64",
          "conversation_deduplicated","turn_deduplicated","turn_number",
          "conversation_turn_count","turn_projection_sha256",
          "answer_text_sha256"
        ]; []))
        and ($receipt.internal_record | exact_keys([
          "origin","base_path","record_schema_version","record_id",
          "record_digest","response_sha256","response_body_base64",
          "public_projection_sha256","runtime_result_sha256"
        ]; []))
        and ($receipt.result | exact_keys([
          "state","answer_success","workflow_completed","answer_source",
          "guard_schema_version","guard_decision","guard_assessment_status",
          "style_policy_id","style_policy_digest","style_assessment_passed",
          "final_answer_digest","oracle_digest","public_answer_present",
          "internal_record_verified","public_internal_projection_equal",
          "fallback_or_rejection_present"
        ]; []))
        and (($receipt.promotion_id // "")
          | test("^promotion-sha256:[a-f0-9]{64}$"))
        and $receipt.schema_version == "domeye_interactive_agent_promotion_v2"
        and $receipt.component == "domeye_interactive_agent_sidecar"
        and $receipt.release_id == $candidate.interactive_agent.release_id
        and $receipt.promotion_state == "verified"
        and (($receipt.verified_at_utc // "") | type == "string" and length > 0)
        and $receipt.release_manifest_sha256
          == $candidate.interactive_agent.release_manifest_sha256
        and $receipt.active_receipt_sha256
          == $candidate.interactive_agent.active_state_sha256
        and $receipt.candidate_id == $candidate.interactive_agent.candidate_id
        and $receipt.acceptance_record_id
          == $candidate.interactive_agent.acceptance_record_id
        and $receipt.public_response.origin == "http://127.0.0.1:28471"
        and $receipt.public_response.base_path
          == "/api/v2/country-outage/chat"
        and (($receipt.public_response.conversation_id // "")
          | test("^conversation_sha256_[a-f0-9]{64}$"))
        and (($receipt.public_response.turn_id // "")
          | test("^turn_sha256_[a-f0-9]{64}$"))
        and (($receipt.public_response.question // "")
          | type == "string" and length > 0)
        and ($receipt.public_response.create_response_sha256 | sha256_digest)
        and (($receipt.public_response.create_response_body_base64 // "")
          | type == "string" and length > 0)
        and ($receipt.public_response.turn_response_sha256 | sha256_digest)
        and (($receipt.public_response.turn_response_body_base64 // "")
          | type == "string" and length > 0)
        and ($receipt.public_response.response_sha256 | sha256_digest)
        and (($receipt.public_response.response_body_base64 // "")
          | type == "string" and length > 0)
        and $receipt.public_response.conversation_deduplicated == false
        and $receipt.public_response.turn_deduplicated == false
        and $receipt.public_response.turn_number == 1
        and $receipt.public_response.conversation_turn_count == 1
        and ($receipt.public_response.turn_projection_sha256 | sha256_digest)
        and ($receipt.public_response.answer_text_sha256 | sha256_digest)
        and $receipt.internal_record.origin == "http://127.0.0.1:28476"
        and $receipt.internal_record.base_path
          == "/country-outage/chat/internal"
        and $receipt.internal_record.record_schema_version
          == "domeye_interactive_agent_turn_internal_record_v1"
        and (($receipt.internal_record.record_id // "")
          | type == "string" and length > 0)
        and ($receipt.internal_record.record_digest | sha256_digest)
        and ($receipt.internal_record.response_sha256 | sha256_digest)
        and (($receipt.internal_record.response_body_base64 // "")
          | type == "string" and length > 0)
        and ($receipt.internal_record.public_projection_sha256 | sha256_digest)
        and ($receipt.internal_record.runtime_result_sha256 | sha256_digest)
        and $receipt.internal_record.public_projection_sha256
          == $receipt.public_response.turn_projection_sha256
        and $receipt.result.state == "completed"
        and $receipt.result.answer_success == true
        and $receipt.result.workflow_completed == true
        and $receipt.result.answer_source == "renderer"
        and $receipt.result.guard_schema_version
          == "domeye_agent_response_guard_v2"
        and $receipt.result.guard_decision == "pass"
        and $receipt.result.guard_assessment_status == "evaluated"
        and (($receipt.result.style_policy_id // "")
          | type == "string" and length > 0)
        and ($receipt.result.style_policy_digest | sha256_digest)
        and $receipt.result.style_assessment_passed == true
        and ($receipt.result.final_answer_digest | sha256_digest)
        and ($receipt.result.oracle_digest | sha256_digest)
        and $receipt.result.public_answer_present == true
        and $receipt.result.internal_record_verified == true
        and $receipt.result.public_internal_projection_equal == true
        and $receipt.result.fallback_or_rejection_present == false;
      def answer_contract(
        $answer; $candidate; $status; $base_url; $lifecycle; $production
      ):
        ($answer | exact_keys(([
          "status","base_url","release_id","candidate_id",
          "acceptance_record_id","conversation_id","turn_id","question",
          "response_sha256",
          "promotion_receipt_sha256","promotion_receipt_body_base64",
          "promotion_receipt"
        ] + (if $production then [
          "manager_status_sha256","lifecycle_state","production_verified"
        ] else [] end)); []))
        and $answer.status == $status
        and $answer.base_url == $base_url
        and $answer.release_id == $candidate.interactive_agent.release_id
        and $answer.candidate_id == $candidate.interactive_agent.candidate_id
        and $answer.acceptance_record_id
          == $candidate.interactive_agent.acceptance_record_id
        and $answer.conversation_id
          == $answer.promotion_receipt.public_response.conversation_id
        and $answer.turn_id
          == $answer.promotion_receipt.public_response.turn_id
        and $answer.question
          == $answer.promotion_receipt.public_response.question
        and $answer.response_sha256
          == $answer.promotion_receipt.public_response.response_sha256
        and ($answer.promotion_receipt_sha256 | sha256_digest)
        and (($answer.promotion_receipt_body_base64 // "")
          | type == "string" and length > 0)
        and (if $production then
          ($answer.manager_status_sha256 | sha256_digest)
          and $answer.lifecycle_state == $lifecycle
          and $answer.production_verified == true
        else true end)
        and promotion_contract($answer.promotion_receipt; $candidate);
      def runtime_contract($verification; $candidate; $mode; $status;
        $base_url; $lifecycle; $production):
        $verification.schema_version
          == "domeye_country_outage_general_runtime_verification_v2"
        and $verification.release_id == $candidate.release_id
        and $verification.mode == $mode
        and $verification.status == $status
        and $verification.deterministic_runtime.schema_version
          == "country_outage_general_runtime_verification_v1"
        and $verification.deterministic_runtime.release_id
          == $candidate.release_id
        and $verification.deterministic_runtime.mode == $mode
        and $verification.deterministic_runtime.status == "passed"
        and $verification.deterministic_runtime.repeat_order_concurrent_equal
          == true
        and $verification.deterministic_runtime.boundaries.database_changed
          == false
        and $verification.deterministic_runtime.boundaries.nginx_changed
          == false
        and $verification.deterministic_runtime.boundaries.read_api_checks_model_calls
          == 0
        and $verification.deterministic_runtime.boundaries.interactive_agent_bound
          == true
        and answer_contract($verification.interactive_answer; $candidate;
          $status; $base_url; $lifecycle; $production);
      . as $candidate
      | ($deployment | length) == 1
      and ($state | length) == 1
      and ($canary | length) == 1
      and ($production | length) == 1
      and .schema_version == "domeye_country_outage_general_release_candidate_v2"
      and .release_id == $release_id
      and .status == "built"
      and ((.source.commit // "") | test("^[a-f0-9]{40}$"))
      and .source.annotated_tag == $release_id
      and ((.source.archive_sha256 // "") | test("^sha256:[a-f0-9]{64}$"))
      and ((.source.manifest_sha256 // "") | test("^[a-f0-9]{64}$"))
      and .source.authority == {
        mode:"interactive_agent_release",
        release_id:$release_id,
        commit:.source.commit,
        annotated_tag:$release_id,
        archive_sha256:.source.archive_sha256,
        equality_verified:true
      }
      and .components.backend.release_id == ($release_id + "-backend")
      and ((.components.backend.binding_sha256 // "") | test("^[a-f0-9]{64}$"))
      and ((.components.backend.sha256sums_sha256 // "") | test("^[a-f0-9]{64}$"))
      and .components.frontend.release_id == ($release_id + "-frontend")
      and ((.components.frontend.manifest_sha256 // "") | test("^[a-f0-9]{64}$"))
      and ((.components.frontend.tree_sha256 // "") | test("^[a-f0-9]{64}$"))
      and ((.components.frontend.sha256sums_sha256 // "") | test("^[a-f0-9]{64}$"))
      and (.interactive_agent as $interactive
        | ($interactive | exact_keys([
          "release_id","path","release_manifest_path",
          "release_manifest_sha256","release_manifest_schema_version",
          "active_state_path","active_state_sha256","candidate_id",
          "candidate_manifest_path","candidate_manifest_sha256",
          "acceptance_record_path","acceptance_record_id",
          "acceptance_record_sha256","acceptance_replay_receipt_path",
          "acceptance_replay_receipt_sha256","readiness_schema_version",
          "readiness_identity_sha256","interactive_answer_attempt_limit",
          "cost_policy","endpoint","activation_scope",
          "candidate_production_deployed"
        ]; []))
        and $interactive.release_id == $release_id
        and (($interactive.path // "") | startswith("/"))
        and $interactive.release_manifest_path
          == ($interactive.path + "/RELEASE-MANIFEST.json")
        and ($interactive.release_manifest_sha256 | sha256_digest)
        and $interactive.release_manifest_schema_version
          == "domeye_interactive_agent_release_manifest_v2"
        and (($interactive.active_state_path // "") | startswith("/"))
        and ($interactive.active_state_sha256 | sha256_digest)
        and (($interactive.candidate_id // "")
          | test("^manifest:sha256:[a-f0-9]{64}$"))
        and $interactive.candidate_manifest_path == ($interactive.path
          + "/project/contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json")
        and ($interactive.candidate_manifest_sha256 | sha256_digest)
        and (($interactive.acceptance_record_path // "")
          | startswith($interactive.path
            + "/project/evaluation/country-outage/first-vertical-slice/runs/"))
        and ($interactive.acceptance_record_path
          | endswith("/acceptance-record-final.json"))
        and (($interactive.acceptance_record_id // "")
          | test("^acceptance-record-sha256:[a-f0-9]{64}$"))
        and ($interactive.acceptance_record_sha256 | sha256_digest)
        and $interactive.acceptance_replay_receipt_path
          == ($interactive.path + "/deployment/ACCEPTANCE-REPLAY.json")
        and ($interactive.acceptance_replay_receipt_sha256 | sha256_digest)
        and $interactive.readiness_schema_version
          == "domeye_interactive_agent_release_probe_v2"
        and ($interactive.readiness_identity_sha256 | sha256_digest)
        and $interactive.interactive_answer_attempt_limit == 10
        and $interactive.cost_policy == "audit_only"
        and $interactive.endpoint == {
          url:"http://127.0.0.1:28476",host:"127.0.0.1",port:28476,
          base_path:"/country-outage/chat"
        }
        and $interactive.activation_scope == "local_evaluation_only"
        and $interactive.candidate_production_deployed == false)
      and .protected_runtime.database_changed == false
      and ((.protected_runtime.database_state_sha256 // "") | test("^[a-f0-9]{64}$"))
      and .protected_runtime.nginx_changed == false
      and ((.protected_runtime.nginx_main_sha256 // "") | test("^[a-f0-9]{64}$"))
      and ((.protected_runtime.nginx_site_sha256 // "") | test("^[a-f0-9]{64}$"))
      and .build_boundaries.model_calls_during_prepare == 0
      and .rollback == {mode:"fail_closed",previous_release_id:null}
      and .promotion_contract == {
        candidate_canary_production_same_artifacts:true,
        rebuild_allowed:false
      }
      and $state[0].schema_version == "domeye_country_outage_general_activation_v2"
      and $state[0].release_id == $release_id
      and $state[0].phase == "production_verified"
      and $state[0].status == "passed"
      and $state[0].candidate.backend.release_id == .components.backend.release_id
      and $state[0].candidate.backend.path == .components.backend.path
      and $state[0].candidate.frontend.release_id == .components.frontend.release_id
      and $state[0].candidate.interactive_agent == {
        release_id:.interactive_agent.release_id,
        candidate_id:.interactive_agent.candidate_id,
        acceptance_record_id:.interactive_agent.acceptance_record_id
      }
      and $state[0].verification == {
        canary:{path:"CANARY-VERIFICATION.json",sha256:$canary_verification_sha},
        production:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_verification_sha}
      }
      and $state[0].rollback == {mode:"fail_closed",previous_release_id:null}
      and $deployment[0].schema_version == "domeye_country_outage_general_deployment_v2"
      and $deployment[0].release_id == $release_id
      and $deployment[0].status == "production_verified"
      and $deployment[0].production_verified == true
      and $deployment[0].artifacts_rebuilt_during_promotion == false
      and $deployment[0].components.backend == {
        release_id:.components.backend.release_id,
        path:.components.backend.path
      }
      and $deployment[0].components.frontend.release_id == .components.frontend.release_id
      and $deployment[0].components.frontend.source_artifact_path == (.components.frontend.path + "/dist")
      and $deployment[0].components.frontend.tree_sha256 == .components.frontend.tree_sha256
      and $deployment[0].components.interactive_agent == {
        release_id:.interactive_agent.release_id,
        candidate_id:.interactive_agent.candidate_id,
        acceptance_record_id:.interactive_agent.acceptance_record_id
      }
      and $deployment[0].cutover_quarantine.canonical_actual_directory == true
      and $deployment[0].cutover_quarantine.nginx_reference_present == false
      and $deployment[0].cutover_quarantine.routed == false
      and $deployment[0].cutover_quarantine.automatic_restore == false
      and $deployment[0].verification == {
        canary:{path:"CANARY-VERIFICATION.json",sha256:$canary_verification_sha},
        production:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_verification_sha}
      }
      and $deployment[0].rollback == {
        mode:"fail_closed",previous_release_id:null,available:false
      }
      and runtime_contract($canary[0]; $candidate; "canary";
        "canary_verified"; "http://127.0.0.1:38672"; "deployed"; false)
      and runtime_contract($production[0]; $candidate; "production";
        "production_verified"; "http://127.0.0.1:28471"; "verified"; true)
      and $canary[0].interactive_answer.promotion_receipt.public_response.question
        == $production[0].interactive_answer.promotion_receipt.public_response.question
      and $canary[0].interactive_answer.promotion_receipt.result.oracle_digest
        == $production[0].interactive_answer.promotion_receipt.result.oracle_digest
      and $canary[0].interactive_answer.promotion_receipt.result.style_policy_id
        == $production[0].interactive_answer.promotion_receipt.result.style_policy_id
      and $canary[0].interactive_answer.promotion_receipt.result.style_policy_digest
        == $production[0].interactive_answer.promotion_receipt.result.style_policy_digest
      and $canary[0].interactive_answer.promotion_receipt.public_response.conversation_id
        != $production[0].interactive_answer.promotion_receipt.public_response.conversation_id
      and $canary[0].interactive_answer.promotion_receipt.public_response.turn_id
        != $production[0].interactive_answer.promotion_receipt.public_response.turn_id
    ' "${candidate}" >/dev/null \
        || die 'v2 Candidate、激活、部署或 canary/production 正确回答合同不一致'

    validate_interactive_answer_raw_evidence "${canary_verification}" 'Canary'
    validate_interactive_answer_raw_evidence "${production_verification}" 'Production'
}

if [[ "${1:-}" == '--test-contracts' ]]; then
    (( $# == 7 )) \
        || die '用法：check-release-normalization.sh --test-contracts <release-id> <candidate> <deployment> <activation-state> <canary-verification> <production-verification>'
    require_commands awk base64 jq readlink sha256sum tr
    validate_release_contracts "$2" "$3" "$4" "$5" "$6" "$7"
    jq -n --arg release_id "$2" \
        '{schema_version:"domeye_release_normalization_contract_fixture_v1",status:"fixture_passed",release_id:$release_id}'
    exit 0
fi

if (( $# != 1 )); then
    die '用法：check-release-normalization.sh <release-id>'
fi
readonly RELEASE_ID="$1"
valid_release_id "${RELEASE_ID}" || die 'release-id 格式无效'

require_commands awk base64 chmod cmp curl date env find git jq readlink screen \
    sha256sum ss mktemp rm rmdir sort tr

readonly RUNTIME_ROOT='/home/bgpdata/Domeye-Core-runtime'
readonly REPOSITORY='/home/bgpdata/Domeye-Core'
readonly GOVERNANCE_ROOT='/home/bgpdata/Domeye-Core-governance'
readonly UNIFIED_ROOT="${RUNTIME_ROOT}/unified-releases/${RELEASE_ID}"
readonly CANDIDATE="${UNIFIED_ROOT}/CANDIDATE-MANIFEST.json"
readonly DEPLOYMENT="${UNIFIED_ROOT}/DEPLOYMENT.json"
readonly STATE="${UNIFIED_ROOT}/ACTIVATION-STATE.json"
readonly CANARY_VERIFICATION="${UNIFIED_ROOT}/CANARY-VERIFICATION.json"
readonly PRODUCTION_VERIFICATION="${UNIFIED_ROOT}/PRODUCTION-VERIFICATION.json"
readonly DATABASE_STATE='/home/bgpdata/Domeye-Core-dev-data/state.json'
readonly NGINX_MAIN='/etc/nginx/nginx.conf'
readonly NGINX_SITE='/etc/nginx/conf.d/domeye-core.conf'
readonly BACKEND_CURRENT="${RUNTIME_ROOT}/current"
readonly FRONTEND_CURRENT="${RUNTIME_ROOT}/web/state/frontend-current"
readonly FRONTEND_PUBLIC="${RUNTIME_ROOT}/web/dist"
readonly INTERACTIVE_RUNTIME_ROOT="${RUNTIME_ROOT}/country-outage-interactive-agent"
readonly INTERACTIVE_CURRENT="${INTERACTIVE_RUNTIME_ROOT}/current"
readonly INTERACTIVE_ACTIVE="${INTERACTIVE_RUNTIME_ROOT}/state/active.json"

validate_release_contracts "${RELEASE_ID}" "${CANDIDATE}" "${DEPLOYMENT}" \
    "${STATE}" "${CANARY_VERIFICATION}" "${PRODUCTION_VERIFICATION}"

readonly COMMIT="$(json_value "${CANDIDATE}" '.source.commit' 'Candidate commit')"
readonly TAG="$(json_value "${CANDIDATE}" '.source.annotated_tag' 'Candidate annotated tag')"
readonly SOURCE_PATH="$(json_value "${CANDIDATE}" '.source.path' 'Source 制品路径')"
readonly SOURCE_ARCHIVE="$(json_value "${CANDIDATE}" '.source.archive_path' 'Source 归档路径')"
readonly SOURCE_ARCHIVE_SHA="$(json_value "${CANDIDATE}" '.source.archive_sha256' 'Source 归档摘要')"
readonly SOURCE_MANIFEST_SHA="$(json_value "${CANDIDATE}" '.source.manifest_sha256' 'Source manifest 摘要')"
readonly BACKEND_RELEASE="$(json_value "${CANDIDATE}" '.components.backend.release_id' 'Backend release-id')"
readonly BACKEND_PATH="$(json_value "${CANDIDATE}" '.components.backend.path' 'Backend 制品路径')"
readonly BACKEND_BINDING_SHA="$(json_value "${CANDIDATE}" '.components.backend.binding_sha256' 'Backend binding 摘要')"
readonly BACKEND_SUMS_SHA="$(json_value "${CANDIDATE}" '.components.backend.sha256sums_sha256' 'Backend SHA256SUMS 摘要')"
readonly FRONTEND_RELEASE="$(json_value "${CANDIDATE}" '.components.frontend.release_id' 'Frontend release-id')"
readonly FRONTEND_PATH="$(json_value "${CANDIDATE}" '.components.frontend.path' 'Frontend 制品路径')"
readonly FRONTEND_MANIFEST_SHA="$(json_value "${CANDIDATE}" '.components.frontend.manifest_sha256' 'Frontend manifest 摘要')"
readonly FRONTEND_TREE_SHA="$(json_value "${CANDIDATE}" '.components.frontend.tree_sha256' 'Frontend 树摘要')"
readonly FRONTEND_SUMS_SHA="$(json_value "${CANDIDATE}" '.components.frontend.sha256sums_sha256' 'Frontend SHA256SUMS 摘要')"
readonly DATABASE_STATE_SHA="$(json_value "${CANDIDATE}" '.protected_runtime.database_state_sha256' '数据库状态摘要')"
readonly NGINX_MAIN_SHA="$(json_value "${CANDIDATE}" '.protected_runtime.nginx_main_sha256' 'Nginx 主配置摘要')"
readonly NGINX_SITE_SHA="$(json_value "${CANDIDATE}" '.protected_runtime.nginx_site_sha256' 'Nginx site 摘要')"
readonly INTERACTIVE_RELEASE="$(json_value "${CANDIDATE}" '.interactive_agent.release_id' 'Interactive Agent release-id')"
readonly INTERACTIVE_PATH="$(json_value "${CANDIDATE}" '.interactive_agent.path' 'Interactive Agent 制品路径')"
readonly INTERACTIVE_MANIFEST="$(json_value "${CANDIDATE}" '.interactive_agent.release_manifest_path' 'Interactive Agent manifest 路径')"
readonly INTERACTIVE_MANIFEST_SHA="$(json_value "${CANDIDATE}" '.interactive_agent.release_manifest_sha256' 'Interactive Agent manifest 摘要')"
readonly INTERACTIVE_ACTIVE_PATH="$(json_value "${CANDIDATE}" '.interactive_agent.active_state_path' 'Interactive Agent active 路径')"
readonly INTERACTIVE_ACTIVE_SHA="$(json_value "${CANDIDATE}" '.interactive_agent.active_state_sha256' 'Interactive Agent active 摘要')"
readonly INTERACTIVE_CANDIDATE_ID="$(json_value "${CANDIDATE}" '.interactive_agent.candidate_id' 'Interactive Agent Candidate ID')"
readonly INTERACTIVE_CANDIDATE="$(json_value "${CANDIDATE}" '.interactive_agent.candidate_manifest_path' 'Interactive Agent Candidate 路径')"
readonly INTERACTIVE_CANDIDATE_SHA="$(json_value "${CANDIDATE}" '.interactive_agent.candidate_manifest_sha256' 'Interactive Agent Candidate 摘要')"
readonly INTERACTIVE_ACCEPTANCE="$(json_value "${CANDIDATE}" '.interactive_agent.acceptance_record_path' 'Interactive Agent Acceptance 路径')"
readonly INTERACTIVE_ACCEPTANCE_ID="$(json_value "${CANDIDATE}" '.interactive_agent.acceptance_record_id' 'Interactive Agent Acceptance ID')"
readonly INTERACTIVE_ACCEPTANCE_SHA="$(json_value "${CANDIDATE}" '.interactive_agent.acceptance_record_sha256' 'Interactive Agent Acceptance 摘要')"
readonly INTERACTIVE_ACCEPTANCE_REPLAY="$(json_value "${CANDIDATE}" '.interactive_agent.acceptance_replay_receipt_path' 'Interactive Agent Acceptance replay 路径')"
readonly INTERACTIVE_ACCEPTANCE_REPLAY_SHA="$(json_value "${CANDIDATE}" '.interactive_agent.acceptance_replay_receipt_sha256' 'Interactive Agent Acceptance replay 摘要')"
readonly INTERACTIVE_READINESS_SHA="$(json_value "${CANDIDATE}" '.interactive_agent.readiness_identity_sha256' 'Interactive Agent readiness 摘要')"

trusted_git() {
    /usr/bin/env -i HOME="${HOME:-/root}" PATH=/usr/bin:/bin \
        LANG=C LC_ALL=C SSH_AUTH_SOCK="${SSH_AUTH_SOCK:-}" \
        GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
        GIT_CONFIG_SYSTEM=/dev/null GIT_NO_REPLACE_OBJECTS=1 \
        GIT_OPTIONAL_LOCKS=0 GIT_TERMINAL_PROMPT=0 \
        GIT_SSH_COMMAND='/usr/bin/ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes' \
        /usr/bin/git --no-replace-objects -C "${REPOSITORY}" "$@"
}

trusted_git_line_count() {
    { trusted_git "$@" 2>/dev/null || true; } \
        | /usr/bin/awk 'END { print NR + 0 }'
}

require_actual_directory "${REPOSITORY}" '生产 Git checkout '
readonly TRUSTED_REMOTE_NAMES="$(trusted_git remote 2>/dev/null || true)"
readonly TRUSTED_REMOTE_NAME_COUNT="$(trusted_git_line_count remote)"
readonly TRUSTED_RAW_ORIGIN="$(trusted_git config --local --get-all remote.origin.url 2>/dev/null || true)"
readonly TRUSTED_RAW_ORIGIN_COUNT="$(trusted_git_line_count config --local --get-all remote.origin.url)"
readonly TRUSTED_RAW_PUSH_COUNT="$(trusted_git_line_count config --local --get-all remote.origin.pushurl)"
readonly TRUSTED_ORIGIN="$(trusted_git remote get-url --all origin 2>/dev/null || true)"
readonly TRUSTED_ORIGIN_COUNT="$(trusted_git_line_count remote get-url --all origin)"
readonly TRUSTED_PUSH="$(trusted_git remote get-url --push --all origin 2>/dev/null || true)"
readonly TRUSTED_PUSH_COUNT="$(trusted_git_line_count remote get-url --push --all origin)"
[[ "${TRUSTED_REMOTE_NAME_COUNT}" == 1 && "${TRUSTED_REMOTE_NAMES}" == origin \
    && "${TRUSTED_RAW_ORIGIN_COUNT}" == 1 \
    && "${TRUSTED_RAW_ORIGIN}" == 'git@github.com:xinghuahewo/domeye_.git' \
    && "${TRUSTED_RAW_PUSH_COUNT}" == 0 \
    && "${TRUSTED_ORIGIN_COUNT}" == 1 \
    && "${TRUSTED_ORIGIN}" == 'git@github.com:xinghuahewo/domeye_.git' \
    && "${TRUSTED_PUSH_COUNT}" == 1 \
    && "${TRUSTED_PUSH}" == 'git@github.com:xinghuahewo/domeye_.git' ]] \
    || die '生产 checkout 的 origin 不是唯一且不可改写的官方 GitHub SSH remote'
[[ "$(trusted_git symbolic-ref -q HEAD)" == 'refs/heads/main' \
    && "$(trusted_git rev-parse HEAD)" == "${COMMIT}" \
    && "$(trusted_git rev-parse refs/heads/main)" == "${COMMIT}" \
    && "$(trusted_git rev-parse refs/remotes/origin/main)" == "${COMMIT}" ]] \
    || die 'HEAD、main、origin/main 与 Candidate commit 不一致'
trusted_git diff-index --quiet HEAD -- \
    || die '生产 checkout 的已跟踪文件不是干净 Candidate commit'
[[ "$(trusted_git cat-file -t "${TAG}")" == 'tag' \
    && "$(trusted_git rev-parse "${TAG}^{}")" == "${COMMIT}" ]] \
    || die '发布 tag 不是指向 Candidate commit 的 annotated tag'

require_actual_directory "${SOURCE_PATH}" 'Source 制品目录 '
readonly SOURCE_MANIFEST="${SOURCE_PATH}/SOURCE-MANIFEST.json"
require_regular_file "${SOURCE_MANIFEST}" 'SOURCE-MANIFEST.json '
require_regular_file "${SOURCE_ARCHIVE}" 'Source 归档 '
[[ "${SOURCE_ARCHIVE}" == "${SOURCE_PATH}/artifacts/source.tar.gz" \
    && "$(sha256_hex "${SOURCE_MANIFEST}")" == "${SOURCE_MANIFEST_SHA}" \
    && "sha256:$(sha256_hex "${SOURCE_ARCHIVE}")" == "${SOURCE_ARCHIVE_SHA}" ]] \
    || die 'Source 路径、manifest 或归档摘要相对 Candidate 漂移'
jq -e --arg release_id "${RELEASE_ID}-source" --arg commit "${COMMIT}" \
    --arg tag "${TAG}" --arg archive "${SOURCE_ARCHIVE}" \
    --arg archive_sha "${SOURCE_ARCHIVE_SHA}" \
    --arg authority_release "${INTERACTIVE_RELEASE}" '
  .schema_version == "domeye_country_outage_general_source_v2"
  and .release_id == $release_id
  and .commit == $commit
  and .annotated_tag == $tag
  and .archive_path == $archive
  and .archive_sha256 == $archive_sha
  and .source_authority == {
    mode:"interactive_agent_release",
    release_id:$authority_release,
    equality_verified:true
  }
' "${SOURCE_MANIFEST}" >/dev/null \
    || die 'Source manifest 与 Git/Candidate/Interactive Agent 身份不一致'

require_actual_directory "${BACKEND_PATH}" 'Backend 制品目录 '
readonly BACKEND_BINDING="${BACKEND_PATH}/BACKEND-SOURCE-BINDING.json"
readonly BACKEND_SUMS="${BACKEND_PATH}/SHA256SUMS"
require_regular_file "${BACKEND_BINDING}" 'BACKEND-SOURCE-BINDING.json '
require_regular_file "${BACKEND_SUMS}" 'Backend SHA256SUMS '
require_regular_file "${BACKEND_PATH}/backend/core.sha256" 'Backend core.sha256 '
require_regular_file "${BACKEND_PATH}/GIT-COMMIT" 'Backend GIT-COMMIT '
require_regular_file "${BACKEND_PATH}/RELEASE-TAG" 'Backend RELEASE-TAG '
[[ "$(sha256_hex "${BACKEND_BINDING}")" == "${BACKEND_BINDING_SHA}" \
    && "$(sha256_hex "${BACKEND_SUMS}")" == "${BACKEND_SUMS_SHA}" \
    && "$(<"${BACKEND_PATH}/GIT-COMMIT")" == "${COMMIT}" \
    && "$(<"${BACKEND_PATH}/RELEASE-TAG")" == "${TAG}" ]] \
    || die 'Backend binding、摘要或源码身份相对 Candidate 漂移'
(
    cd -- "${BACKEND_PATH}"
    sha256sum -c SHA256SUMS >/dev/null
) || die 'Backend 冻结文件摘要校验失败'
(
    cd -- "${BACKEND_PATH}/backend"
    sha256sum -c core.sha256 >/dev/null
) || die 'Backend Core 摘要校验失败'
jq -e --slurpfile candidate "${CANDIDATE}" \
    --arg binding_path "${BACKEND_PATH}" \
    --arg candidate_path "${CANDIDATE}" '
  .schema_version == "domeye_country_outage_general_backend_binding_v2"
  and .release_id == $candidate[0].components.backend.release_id
  and .runtime_root == $binding_path
  and .unified_candidate == {
    release_id:$candidate[0].release_id,
    manifest_path:$candidate_path
  }
  and .source_commit == $candidate[0].source.commit
  and .source_tag == $candidate[0].source.annotated_tag
  and .source_archive_sha256 == $candidate[0].source.archive_sha256
  and .source_authority == $candidate[0].source.authority
  and .database_state_sha256 == $candidate[0].protected_runtime.database_state_sha256
  and .nginx_main_sha256 == $candidate[0].protected_runtime.nginx_main_sha256
  and .nginx_site_sha256 == $candidate[0].protected_runtime.nginx_site_sha256
  and .data_selection_sha256 == $candidate[0].frozen_data.production_selection_sha256
  and .country_outage_registry_sha256 == $candidate[0].frozen_data.country_outage_registry_sha256
  and .general_read_model_manifest_sha256 == $candidate[0].frozen_data.general_read_model_manifest_sha256
  and .interactive_agent == $candidate[0].interactive_agent
  and .boundaries.database_changed == false
  and .boundaries.nginx_changed == false
  and .boundaries.interactive_agent_bound == true
  and .boundaries.model_calls_during_prepare == 0
' "${BACKEND_BINDING}" >/dev/null \
    || die 'Backend binding 未与 v2 Candidate 完整绑定'
[[ "$(readlink -f -- "${BACKEND_CURRENT}")" == "${BACKEND_PATH}" ]] \
    || die 'Backend current 未指向 Candidate 制品'

require_actual_directory "${FRONTEND_PATH}" 'Frontend 制品目录 '
readonly FRONTEND_MANIFEST="${FRONTEND_PATH}/FRONTEND-MANIFEST.json"
readonly FRONTEND_SUMS="${FRONTEND_PATH}/SHA256SUMS"
require_regular_file "${FRONTEND_MANIFEST}" 'FRONTEND-MANIFEST.json '
require_regular_file "${FRONTEND_SUMS}" 'Frontend SHA256SUMS '
[[ "$(sha256_hex "${FRONTEND_MANIFEST}")" == "${FRONTEND_MANIFEST_SHA}" \
    && "$(sha256_hex "${FRONTEND_SUMS}")" == "${FRONTEND_SUMS_SHA}" ]] \
    || die 'Frontend manifest 或 SHA256SUMS 相对 Candidate 漂移'
(
    cd -- "${FRONTEND_PATH}"
    sha256sum -c SHA256SUMS >/dev/null
) || die 'Frontend 冻结文件摘要校验失败'
jq -e --arg release_id "${FRONTEND_RELEASE}" --arg commit "${COMMIT}" \
    --arg tag "${TAG}" --arg tree_sha "${FRONTEND_TREE_SHA}" '
  .schema_version == "domeye_country_outage_general_frontend_manifest_v1"
  and .release_id == $release_id
  and .source == {commit:$commit,annotated_tag:$tag}
  and .tree_sha256 == $tree_sha
' "${FRONTEND_MANIFEST}" >/dev/null \
    || die 'Frontend manifest 与 Candidate Source 身份不一致'
[[ "$(frontend_tree_sha256 "${FRONTEND_PATH}/dist")" == "${FRONTEND_TREE_SHA}" ]] \
    || die 'Frontend 候选树摘要漂移'
require_actual_directory "${FRONTEND_PUBLIC}" 'Frontend 公共目录 '
require_regular_file "${FRONTEND_CURRENT}" 'Frontend current state '
[[ "$(<"${FRONTEND_CURRENT}")" == "${FRONTEND_RELEASE}" \
    && "$(frontend_tree_sha256 "${FRONTEND_PUBLIC}")" == "${FRONTEND_TREE_SHA}" ]] \
    || die 'Frontend 公共字节或 current state 未绑定 Candidate'

require_regular_file "${DATABASE_STATE}" '数据库状态 '
require_regular_file "${NGINX_MAIN}" 'Nginx 主配置 '
require_regular_file "${NGINX_SITE}" 'Nginx site 配置 '
[[ "$(sha256_hex "${DATABASE_STATE}")" == "${DATABASE_STATE_SHA}" ]] \
    || die '数据库状态摘要相对 Candidate 漂移'
[[ "$(sha256_hex "${NGINX_MAIN}")" == "${NGINX_MAIN_SHA}" \
    && "$(sha256_hex "${NGINX_SITE}")" == "${NGINX_SITE_SHA}" ]] \
    || die 'Nginx 配置摘要相对 Candidate 漂移'

require_actual_directory "${INTERACTIVE_PATH}" 'Interactive Agent 制品目录 '
require_regular_file "${INTERACTIVE_MANIFEST}" 'Interactive Agent RELEASE-MANIFEST.json '
require_regular_file "${INTERACTIVE_ACTIVE_PATH}" 'Interactive Agent active.json '
require_regular_file "${INTERACTIVE_CANDIDATE}" 'Interactive Agent Candidate '
require_regular_file "${INTERACTIVE_ACCEPTANCE}" 'Interactive Agent Acceptance '
require_regular_file "${INTERACTIVE_ACCEPTANCE_REPLAY}" 'Interactive Agent Acceptance replay '
[[ "${INTERACTIVE_ACTIVE_PATH}" == "${INTERACTIVE_ACTIVE}" \
    && "$(readlink -f -- "${INTERACTIVE_CURRENT}")" == "${INTERACTIVE_PATH}" \
    && "sha256:$(sha256_hex "${INTERACTIVE_MANIFEST}")" == "${INTERACTIVE_MANIFEST_SHA}" \
    && "sha256:$(sha256_hex "${INTERACTIVE_ACTIVE_PATH}")" == "${INTERACTIVE_ACTIVE_SHA}" \
    && "sha256:$(sha256_hex "${INTERACTIVE_CANDIDATE}")" == "${INTERACTIVE_CANDIDATE_SHA}" \
    && "sha256:$(sha256_hex "${INTERACTIVE_ACCEPTANCE}")" == "${INTERACTIVE_ACCEPTANCE_SHA}" \
    && "sha256:$(sha256_hex "${INTERACTIVE_ACCEPTANCE_REPLAY}")" == "${INTERACTIVE_ACCEPTANCE_REPLAY_SHA}" \
    && "$(json_value "${INTERACTIVE_CANDIDATE}" '.candidate_id' 'Interactive Agent Candidate ID')" == "${INTERACTIVE_CANDIDATE_ID}" \
    && "$(json_value "${INTERACTIVE_ACCEPTANCE}" '.acceptance_record_id' 'Interactive Agent Acceptance ID')" == "${INTERACTIVE_ACCEPTANCE_ID}" ]] \
    || die 'Interactive Agent current/release/active/Candidate/Acceptance 身份漂移'
jq -e --arg release_id "${INTERACTIVE_RELEASE}" \
    --arg commit "${COMMIT}" --arg tag "${TAG}" \
    --arg archive_sha "${SOURCE_ARCHIVE_SHA}" \
    --arg candidate_id "${INTERACTIVE_CANDIDATE_ID}" \
    --arg candidate_path "${INTERACTIVE_CANDIDATE#${INTERACTIVE_PATH}/}" \
    --arg candidate_sha "${INTERACTIVE_CANDIDATE_SHA}" \
    --arg acceptance_path "${INTERACTIVE_ACCEPTANCE#${INTERACTIVE_PATH}/}" \
    --arg acceptance_id "${INTERACTIVE_ACCEPTANCE_ID}" \
    --arg acceptance_sha "${INTERACTIVE_ACCEPTANCE_SHA}" \
    --arg replay_path "${INTERACTIVE_ACCEPTANCE_REPLAY#${INTERACTIVE_PATH}/}" \
    --arg replay_sha "${INTERACTIVE_ACCEPTANCE_REPLAY_SHA}" '
  .schema_version == "domeye_interactive_agent_release_manifest_v2"
  and .component == "domeye_interactive_agent_sidecar"
  and .release_id == $release_id
  and .source.commit == $commit
  and .source.annotated_tag == $tag
  and .source.archive_sha256 == $archive_sha
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
  and .runtime == {
    entrypoint:"agent-sidecar/dist/src/cli/serve-interactive-agent.js",
    host:"127.0.0.1",port:28476,base_path:"/country-outage/chat",
    activation_scope:"local_evaluation_only",candidate_production_deployed:false
  }
  and .live_verification.public_backend_origin == "http://127.0.0.1:28471"
  and .live_verification.backend_base_path == "/api/v2/country-outage/chat"
  and .live_verification.internal_sidecar_origin == "http://127.0.0.1:28476"
  and .live_verification.internal_record_base_path
    == "/country-outage/chat/internal"
  and .live_verification.public_conversation_schema_version
    == "domeye_interactive_agent_conversation_v2"
  and .live_verification.internal_record_schema_version
    == "domeye_interactive_agent_turn_internal_record_v1"
  and ((.live_verification.oracle_digest // "") | test("^sha256:[a-f0-9]{64}$"))
  and (
    .rollback == {mode:"fail_closed",previous_release_id:null}
    or (
      .rollback.mode == "same_schema_only"
      and (.rollback.previous_release_id | type == "string")
      and (.rollback.previous_release_id
        | test("^[0-9]{8}T[0-9]{6}Z-country-outage-interactive-agent-[a-z0-9][a-z0-9-]{0,31}$"))
      and .rollback.previous_release_id != $release_id
    )
  )
' "${INTERACTIVE_MANIFEST}" >/dev/null \
    || die 'Interactive Agent release manifest 未与 Source/Candidate/Acceptance/28476 绑定'
jq -e --arg release_id "${INTERACTIVE_RELEASE}" \
    --arg manifest_sha "${INTERACTIVE_MANIFEST_SHA}" \
    --arg candidate_id "${INTERACTIVE_CANDIDATE_ID}" '
  .schema_version == "domeye_interactive_agent_active_v1"
  and .component == "domeye_interactive_agent_sidecar"
  and .release_id == $release_id
  and .deployment_state == "deployed"
  and .release_manifest_sha256 == $manifest_sha
  and .candidate_id == $candidate_id
  and .runtime.screen_name == "domeye_interactive_agent_sidecar"
  and (.runtime.pid | type == "number" and . > 0)
  and .runtime.entrypoint == "agent-sidecar/dist/src/cli/serve-interactive-agent.js"
  and .runtime.host == "127.0.0.1"
  and .runtime.port == 28476
  and .runtime.base_path == "/country-outage/chat"
  and (
    .rollback == {mode:"fail_closed",previous_release_id:null}
    or (
      .rollback.mode == "same_schema_only"
      and (.rollback.previous_release_id | type == "string")
      and (.rollback.previous_release_id
        | test("^[0-9]{8}T[0-9]{6}Z-country-outage-interactive-agent-[a-z0-9][a-z0-9-]{0,31}$"))
      and .rollback.previous_release_id != $release_id
    )
  )
' "${INTERACTIVE_ACTIVE_PATH}" >/dev/null \
    || die 'Interactive Agent active 回执未与 release/Candidate/28476 绑定'
jq -e '
  .payload.schema_version == "domeye_first_slice_candidate_manifest_v2"
  and .payload.budget_policy.model_api_attempt_limit == 10
  and .payload.budget_policy.cost_policy == "audit_only"
  and .payload.budget_policy.monetary_limit_usd == null
' "${INTERACTIVE_CANDIDATE}" >/dev/null \
    || die 'Interactive Agent Candidate 不满足 10 次尝试且费用仅审计合同'
jq -e --arg candidate_id "${INTERACTIVE_CANDIDATE_ID}" \
    --arg acceptance_id "${INTERACTIVE_ACCEPTANCE_ID}" '
  .schema_version == "domeye_first_slice_acceptance_record_v2"
  and .candidate_id == $candidate_id
  and .acceptance_record_id == $acceptance_id
  and .evaluation_phase == "formal"
  and .acceptance_state == "accepted"
  and .dg1_decision == "GO"
' "${INTERACTIVE_ACCEPTANCE}" >/dev/null \
    || die 'Interactive Agent Acceptance 不是外部批准的 Formal accepted/GO 证据'
readonly INTERACTIVE_SOURCE_COPY="${INTERACTIVE_PATH}/source/source.tar.gz"
require_regular_file "${INTERACTIVE_SOURCE_COPY}" 'Interactive Agent Source 归档 '
[[ "sha256:$(sha256_hex "${INTERACTIVE_SOURCE_COPY}")" == "${SOURCE_ARCHIVE_SHA}" ]] \
    || die 'Interactive Agent Source 归档与 General Source 不一致'
readonly INTERACTIVE_SUMS="${INTERACTIVE_PATH}/SHA256SUMS"
readonly INTERACTIVE_VERIFIER="${INTERACTIVE_PATH}/deployment/verify-release.mjs"
readonly BACKEND_INTERACTIVE_VERIFIER="${BACKEND_PATH}/deploy/country-outage-agent/p1-chat/verify-release.mjs"
readonly TRUSTED_NODE='/home/bgpdata/.local/node-v22.23.1-linux-x64/bin/node'
require_regular_file "${INTERACTIVE_SUMS}" 'Interactive Agent SHA256SUMS '
require_regular_file "${INTERACTIVE_VERIFIER}" 'release-local Interactive Agent verifier '
require_regular_file "${BACKEND_INTERACTIVE_VERIFIER}" 'Backend 冻结 Interactive Agent verifier '
require_regular_file "${TRUSTED_NODE}" '受信 Node '
[[ -x "${TRUSTED_NODE}" ]] || die '受信 Node 不可执行'
(
    cd -- "${INTERACTIVE_PATH}"
    sha256sum -c SHA256SUMS >/dev/null
) || die 'Interactive Agent release 冻结文件摘要校验失败'
cmp -s "${INTERACTIVE_VERIFIER}" "${BACKEND_INTERACTIVE_VERIFIER}" \
    || die 'release-local verifier 与统一 Backend 冻结源码不一致'

readonly INTERACTIVE_MANAGER="${BACKEND_PATH}/deploy/country-outage-agent/p1-chat/manage.sh"
require_regular_file "${INTERACTIVE_MANAGER}" 'Interactive Agent manager '
[[ -x "${INTERACTIVE_MANAGER}" ]] || die 'Interactive Agent manager 不可执行'
INTERACTIVE_STATUS="$("${INTERACTIVE_MANAGER}" status)" \
    || die 'Interactive Agent 组合状态或 28476 实际运行身份无效'
readonly INTERACTIVE_STATUS
jq -e --arg release_id "${INTERACTIVE_RELEASE}" \
    --arg manifest_sha "${INTERACTIVE_MANIFEST_SHA}" \
    --arg candidate_id "${INTERACTIVE_CANDIDATE_ID}" '
  .schema_version == "domeye_interactive_agent_release_probe_v2"
  and .ready == true
  and .component == "domeye_interactive_agent_sidecar"
  and .lifecycle_state == "verified"
  and .release_id == $release_id
  and .release_manifest_sha256 == $manifest_sha
  and .candidate_id == $candidate_id
  and .candidate_activation_scope == "local_evaluation_only"
  and .candidate_production_deployed == false
  and .current_target_matches == true
  and .deployment_active == true
  and .promotion_state == "verified"
  and .production_verified == true
' <<<"${INTERACTIVE_STATUS}" >/dev/null \
    || die 'Interactive Agent 未形成 verified 外部 promotion 状态'
readonly READINESS_IDENTITY="$(jq -cS '{
  schema_version,ready,component,release_id,release_manifest_sha256,
  candidate_id,candidate_activation_scope,candidate_production_deployed
}' <<<"${INTERACTIVE_STATUS}")"
[[ "sha256:$(printf '%s' "${READINESS_IDENTITY}" | sha256sum | awk '{print $1}')" \
    == "${INTERACTIVE_READINESS_SHA}" ]] \
    || die 'Interactive Agent readiness 身份相对 Candidate 漂移'
readonly EXPECTED_MANAGER_STATUS_SHA="$(json_value "${PRODUCTION_VERIFICATION}" '.interactive_answer.manager_status_sha256' 'production manager status 摘要')"
[[ "sha256:$(printf '%s\n' "${INTERACTIVE_STATUS}" | sha256sum | awk '{print $1}')" \
    == "${EXPECTED_MANAGER_STATUS_SHA}" ]] \
    || die 'Interactive Agent 当前 manager status 与生产冻结状态不一致'

readonly PROMOTION="${INTERACTIVE_RUNTIME_ROOT}/state/promotions/${INTERACTIVE_RELEASE}.json"
readonly PROMOTION_SHA="$(json_value "${PRODUCTION_VERIFICATION}" '.interactive_answer.promotion_receipt_sha256' 'promotion 回执摘要')"
readonly CANARY_PROMOTION_SHA="$(json_value "${CANARY_VERIFICATION}" '.interactive_answer.promotion_receipt_sha256' 'canary promotion 回执摘要')"
require_regular_file "${PROMOTION}" 'Interactive Agent promotion 回执 '
[[ "sha256:$(sha256_hex "${PROMOTION}")" == "${PROMOTION_SHA}" ]] \
    || die 'Interactive Agent 外部 promotion 回执摘要漂移'

receipt_replay_root="$(mktemp -d \
    /tmp/domeye-release-normalization-receipts.XXXXXX)" \
    || die '无法创建 promotion 冻结重放目录'
readonly receipt_replay_root
chmod 0700 "${receipt_replay_root}" \
    || die '无法收紧 promotion 冻结重放目录权限'
readonly CANARY_REPLAY_RECEIPT="${receipt_replay_root}/canary-promotion.json"
readonly PRODUCTION_REPLAY_RECEIPT="${receipt_replay_root}/production-promotion.json"
cleanup_replay_receipts() {
    rm -f -- "${CANARY_REPLAY_RECEIPT}" "${PRODUCTION_REPLAY_RECEIPT}"
    rmdir -- "${receipt_replay_root}" 2>/dev/null || true
}
trap cleanup_replay_receipts EXIT
(
    umask 077
    json_value "${CANARY_VERIFICATION}" \
        '.interactive_answer.promotion_receipt_body_base64' \
        'canary promotion 原始回执' \
        | base64 --decode > "${CANARY_REPLAY_RECEIPT}"
    json_value "${PRODUCTION_VERIFICATION}" \
        '.interactive_answer.promotion_receipt_body_base64' \
        'production promotion 原始回执' \
        | base64 --decode > "${PRODUCTION_REPLAY_RECEIPT}"
) || die '无法解码 canary/production promotion 冻结回执'
require_regular_file "${CANARY_REPLAY_RECEIPT}" 'Canary promotion 冻结重放文件 '
require_regular_file "${PRODUCTION_REPLAY_RECEIPT}" 'Production promotion 冻结重放文件 '
[[ "sha256:$(sha256_hex "${CANARY_REPLAY_RECEIPT}")" \
        == "${CANARY_PROMOTION_SHA}" \
    && "sha256:$(sha256_hex "${PRODUCTION_REPLAY_RECEIPT}")" \
        == "${PROMOTION_SHA}" ]] \
    || die 'Canary/Production promotion 冻结重放字节摘要漂移'
cmp -s "${PRODUCTION_REPLAY_RECEIPT}" "${PROMOTION}" \
    || die 'Interactive Agent 外部 promotion 回执与生产冻结字节不一致'

"${TRUSTED_NODE}" "${INTERACTIVE_VERIFIER}" promotion-receipt \
    "${INTERACTIVE_PATH}" "${INTERACTIVE_ACTIVE_PATH}" \
    "${CANARY_REPLAY_RECEIPT}" >/dev/null \
    || die 'Canary promotion 未通过 release-local verifier 冻结重放'
"${TRUSTED_NODE}" "${INTERACTIVE_VERIFIER}" promotion-receipt \
    "${INTERACTIVE_PATH}" "${INTERACTIVE_ACTIVE_PATH}" \
    "${PRODUCTION_REPLAY_RECEIPT}" >/dev/null \
    || die 'Production promotion 未通过 release-local verifier 冻结重放'
readonly ORACLE_DIGEST="$(json_value "${INTERACTIVE_MANIFEST}" '.live_verification.oracle_digest' 'Interactive Agent Oracle 摘要')"
jq -e -s --arg oracle "${ORACLE_DIGEST}" '
  length == 2
  and all(.[].interactive_answer.promotion_receipt.result;
    .oracle_digest == $oracle
    and .answer_source == "renderer"
    and .guard_schema_version == "domeye_agent_response_guard_v2"
    and .guard_decision == "pass"
    and .guard_assessment_status == "evaluated"
    and .style_assessment_passed == true
    and .public_answer_present == true
    and .internal_record_verified == true
    and .public_internal_projection_equal == true
    and .fallback_or_rejection_present == false)
' "${CANARY_VERIFICATION}" "${PRODUCTION_VERIFICATION}" >/dev/null \
    || die 'Canary/Production 回答未与 Renderer、Guard v2、Style、Oracle、公私投影和零回退闭合'

readonly BACKEND_MANAGER="${BACKEND_PATH}/deploy/country-outage-general-page/manage-runtime.sh"
require_regular_file "${BACKEND_MANAGER}" 'General Backend manager '
[[ -x "${BACKEND_MANAGER}" ]] || die 'General Backend manager 不可执行'
BACKEND_STATUS="$(DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
    "${BACKEND_MANAGER}" status)" \
    || die 'General Backend 生产进程、28473 监听或 v2 完成状态无效'
readonly BACKEND_STATUS
jq -e --arg release_id "${BACKEND_RELEASE}" \
    --arg runtime_root "${BACKEND_PATH}" \
    --argjson interactive_agent "${INTERACTIVE_STATUS}" '
  .status == "running"
  and .mode == "production"
  and .release_id == $release_id
  and .runtime_root == $runtime_root
  and (.pid | type == "number" and . > 0)
  and .port == 28473
  and .interactive_agent == $interactive_agent
  and .workflow_completion == {
    state:"verified",
    requires_renderer_guard_correct_answer:true,
    requires_general_production_evidence:true,
    health_check_is_completion:false
  }
' <<<"${BACKEND_STATUS}" >/dev/null \
    || die 'General Backend 实际身份未与 Candidate 和正确回答闭合'
curl --disable --noproxy '*' --proto '=http' --max-redirs 0 \
    -fsS --max-time 5 http://127.0.0.1:28471/api/v1/healthz \
    | jq -e '.status == "ok" and .service == "domeye-core"' >/dev/null \
    || die '28471 公共 Backend 健康检查失败'

require_port_closed 28474 '旧报告 Agent'
require_port_closed 28475 '旧 P1 Chat'
require_screen_absent 'domeye_country_outage_agent' '旧报告 Agent'
require_screen_absent 'domeye_country_outage_p1_chat' '旧 P1 Chat'
require_public_path_retired '/api/v2/country-outage/reports' '旧报告与追问 API'
require_public_path_retired \
    '/api/v2/country-outage/investigations/retired-surface-probe' \
    '旧组合调查 API'
require_public_path_retired \
    '/api/v2/country-outage/capabilities/external-evidence' \
    '旧外部证据能力 API'
require_public_path_retired \
    '/api/v2/country-outage/runs/retired-surface-probe/abort' \
    '旧运行中止 API'

governance_scripts_checked=false
readonly INSTALL_RECEIPT="${GOVERNANCE_ROOT}/installations/${RELEASE_ID}.json"
if [[ -e "${INSTALL_RECEIPT}" || -L "${INSTALL_RECEIPT}" ]]; then
    require_regular_file "${INSTALL_RECEIPT}" '治理安装回执 '
    readonly GIT_DIR="$(trusted_git rev-parse --absolute-git-dir)"
    readonly HOOK_TARGET="${GIT_DIR}/hooks/pre-receive"
    require_regular_file "${HOOK_TARGET}" '服务器 pre-receive Hook '
    require_regular_file "$0" '当前归一化门禁 '
    [[ "$(readlink -f -- "$0")" \
        == "${GOVERNANCE_ROOT}/bin/check-release-normalization.sh" ]] \
        || die '当前归一化门禁不是受管安装目标'
    jq -e --arg release_id "${RELEASE_ID}" \
        --arg repository "${REPOSITORY}" \
        --arg hook_target "${HOOK_TARGET}" \
        --arg hook_sha "$(sha256_hex "${HOOK_TARGET}")" \
        --arg gate_target "$(readlink -f -- "$0")" \
        --arg gate_sha "$(sha256_hex "$0")" '
      .schema_version == "domeye_governance_installation_v1"
      and .release_id == $release_id
      and .status == "installed"
      and .repository == $repository
      and .hook.target == $hook_target
      and .hook.sha256 == $hook_sha
      and .normalization_gate.target == $gate_target
      and .normalization_gate.sha256 == $gate_sha
    ' "${INSTALL_RECEIPT}" >/dev/null \
        || die '治理脚本安装回执与当前 Hook/归一门禁不一致'
    governance_scripts_checked=true
fi

jq -n --arg release_id "${RELEASE_ID}" --arg commit "${COMMIT}" \
    --arg tag "${TAG}" --arg checked_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg candidate_id "${INTERACTIVE_CANDIDATE_ID}" \
    --arg candidate_manifest_sha "${INTERACTIVE_CANDIDATE_SHA}" \
    --arg acceptance_id "${INTERACTIVE_ACCEPTANCE_ID}" \
    --arg acceptance_sha "${INTERACTIVE_ACCEPTANCE_SHA}" \
    --arg acceptance_replay_sha "${INTERACTIVE_ACCEPTANCE_REPLAY_SHA}" \
    --arg release_manifest_sha "${INTERACTIVE_MANIFEST_SHA}" \
    --arg canary_verification_sha "sha256:$(sha256_hex "${CANARY_VERIFICATION}")" \
    --arg production_verification_sha "sha256:$(sha256_hex "${PRODUCTION_VERIFICATION}")" \
    --arg canary_promotion_sha "${CANARY_PROMOTION_SHA}" \
    --arg production_promotion_sha "${PROMOTION_SHA}" \
    --argjson governance_scripts "${governance_scripts_checked}" '
  {
    schema_version:"domeye_release_normalization_gate_v2",
    status:"passed",
    release_id:$release_id,
    commit:$commit,
    annotated_tag:$tag,
    candidate_id:$candidate_id,
    candidate_manifest_sha256:$candidate_manifest_sha,
    acceptance_record_id:$acceptance_id,
    acceptance_record_sha256:$acceptance_sha,
    acceptance_replay_receipt_sha256:$acceptance_replay_sha,
    release_manifest_sha256:$release_manifest_sha,
    verification_sha256:{
      canary:$canary_verification_sha,
      production:$production_verification_sha
    },
    promotion_receipt_sha256:{
      canary:$canary_promotion_sha,
      production:$production_promotion_sha
    },
    checked_at:$checked_at,
    checks:{
      git_main_origin_tag:true,
      source_manifest_and_archive:true,
      backend_candidate_manifest_and_process:true,
      interactive_agent_28476_and_external_promotion:true,
      legacy_agent_surfaces_retired:true,
      release_local_canary_and_production_replay:true,
      frontend_candidate_and_public_bytes:true,
      nginx_config:true,
      database_unchanged:true,
      activation_and_deployment:true,
      renderer_guard_oracle_correct_answer:true,
      canary_production_distinct_1x1:true,
      fallback_or_rejection_zero:true,
      governance_scripts:$governance_scripts
    }
  }'
