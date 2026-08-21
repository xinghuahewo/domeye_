#!/usr/bin/env bash

set -Eeuo pipefail

readonly TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/domeye-governance-fixtures.XXXXXX")"
readonly SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REMOTE="${TEST_ROOT}/remote.git"
readonly WORK="${TEST_ROOT}/work"
readonly APPROVALS="${TEST_ROOT}/approvals"

cleanup() {
    rm -rf -- "${TEST_ROOT}"
}
trap cleanup EXIT

fail() {
    printf '治理夹具失败：%s\n' "$*" >&2
    exit 1
}

expect_reject() {
    local label="$1"
    shift
    local output
    if output="$({ "$@"; } 2>&1)"; then
        fail "${label}：预期拒绝，但命令成功"
    fi
    grep -F 'Domeye 远端门禁拒绝：' <<<"${output}" >/dev/null \
        || fail "${label}：失败不是由治理 Hook 给出：${output}"
}

write_approval() {
    local commit="$1"
    local target="${APPROVALS}/${commit}.json"
    local temporary="${target}.new"
    jq -n --arg commit "${commit}" \
        '{schema_version:"domeye_main_approval_v1",commit:$commit,review:{status:"approved",evidence:"fixture-review"},ci:{status:"passed",evidence:"fixture-ci"},approved_at:"2026-08-05T00:00:00Z"}' \
        >"${temporary}"
    chmod 0600 "${temporary}"
    mv "${temporary}" "${target}"
}

for command_name in base64 cp git grep install jq mktemp mv sha256sum tr; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || fail "缺少测试命令：${command_name}"
done

bash -n \
    "${SCRIPT_ROOT}/pre-receive" \
    "${SCRIPT_ROOT}/check-release-normalization.sh" \
    "${SCRIPT_ROOT}/install.sh" \
    "${SCRIPT_ROOT}/tests/check-doc-links.sh"

python3 "${SCRIPT_ROOT}/tests/test-audit-server-runtime-governance.py"

git init --bare -q "${REMOTE}"
git init -q "${WORK}"
git -C "${WORK}" config user.name 'Domeye Governance Fixture'
git -C "${WORK}" config user.email 'governance-fixture@invalid.local'
git -C "${WORK}" remote add origin "${REMOTE}"
install -d -m 0700 "${APPROVALS}"
git -C "${REMOTE}" config domeye.governanceApprovalRoot "${APPROVALS}"
install -m 0755 "${SCRIPT_ROOT}/pre-receive" "${REMOTE}/hooks/pre-receive"

printf 'base\n' >"${WORK}/fixture.txt"
git -C "${WORK}" add fixture.txt
git -C "${WORK}" commit -q -m 'fixture: base'
base_commit="$(git -C "${WORK}" rev-parse HEAD)"
write_approval "${base_commit}"
git -C "${WORK}" push -q origin HEAD:refs/heads/main

printf 'task\n' >>"${WORK}/fixture.txt"
git -C "${WORK}" commit -qam 'fixture: task branch'
git -C "${WORK}" push -q origin HEAD:refs/heads/codex/fixture-task

second_commit="$(git -C "${WORK}" rev-parse HEAD)"
expect_reject '缺少审批的主干更新' \
    git -C "${WORK}" push origin HEAD:refs/heads/main
write_approval "${second_commit}"
git -C "${WORK}" push -q origin HEAD:refs/heads/main

lightweight_tag='20260805T000001Z-fixture-lightweight'
git -C "${WORK}" tag "${lightweight_tag}"
expect_reject '正式轻量 tag' \
    git -C "${WORK}" push origin "refs/tags/${lightweight_tag}"
git -C "${WORK}" tag -d "${lightweight_tag}" >/dev/null

annotated_tag='20260805T000002Z-fixture-annotated'
git -C "${WORK}" tag -a "${annotated_tag}" -m 'fixture annotated release'
git -C "${WORK}" push -q origin "refs/tags/${annotated_tag}"
expect_reject '正式 tag 删除' \
    git -C "${WORK}" push origin ":refs/tags/${annotated_tag}"

expect_reject '生产主干非快进' \
    git -C "${WORK}" push origin "+${base_commit}:refs/heads/main"

invalid_output="$("${SCRIPT_ROOT}/check-release-normalization.sh" invalid 2>&1 || true)"
grep -F 'release-id 格式无效' <<<"${invalid_output}" >/dev/null \
    || fail '归一检查没有拒绝无效 release-id'

# 当前 General v2 的五份最终证据必须形成同一 Candidate/双次正确回答闭包；
# 这个纯合同入口只供本地敌对夹具使用，不输出生产 passed schema。
readonly NORMALIZATION_ROOT="${TEST_ROOT}/normalization"
readonly NORMALIZATION_RELEASE='20260820T000000Z-country-outage-interactive-agent-fixture'
readonly NORMALIZATION_CANDIDATE="${NORMALIZATION_ROOT}/CANDIDATE-MANIFEST.json"
readonly NORMALIZATION_DEPLOYMENT="${NORMALIZATION_ROOT}/DEPLOYMENT.json"
readonly NORMALIZATION_STATE="${NORMALIZATION_ROOT}/ACTIVATION-STATE.json"
readonly NORMALIZATION_CANARY="${NORMALIZATION_ROOT}/CANARY-VERIFICATION.json"
readonly NORMALIZATION_PRODUCTION="${NORMALIZATION_ROOT}/PRODUCTION-VERIFICATION.json"
readonly NORMALIZATION_CANARY_PROMOTION="${NORMALIZATION_ROOT}/canary-promotion.json"
readonly NORMALIZATION_PRODUCTION_PROMOTION="${NORMALIZATION_ROOT}/production-promotion.json"
readonly HASH_A='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
readonly HASH_B='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
readonly HASH_C='cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'
readonly HASH_D='dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd'
readonly HASH_E='eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
readonly HASH_F='ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
readonly SOURCE_COMMIT='0123456789abcdef0123456789abcdef01234567'
readonly CANDIDATE_ID="manifest:sha256:${HASH_B}"
readonly ACCEPTANCE_ID="acceptance-record-sha256:${HASH_C}"
install -d -m 0700 "${NORMALIZATION_ROOT}"

jq -n --arg release_id "${NORMALIZATION_RELEASE}" \
    --arg commit "${SOURCE_COMMIT}" --arg candidate_id "${CANDIDATE_ID}" \
    --arg acceptance_id "${ACCEPTANCE_ID}" \
    --arg h_a "${HASH_A}" --arg h_b "${HASH_B}" --arg h_c "${HASH_C}" \
    --arg h_d "${HASH_D}" --arg h_e "${HASH_E}" --arg h_f "${HASH_F}" '
  {
    schema_version:"domeye_country_outage_general_release_candidate_v2",
    release_id:$release_id,
    status:"built",
    source:{
      commit:$commit,
      annotated_tag:$release_id,
      archive_path:"/fixture/source/artifacts/source.tar.gz",
      archive_sha256:("sha256:" + $h_a),
      path:"/fixture/source",
      manifest_sha256:$h_b,
      authority:{
        mode:"interactive_agent_release",release_id:$release_id,
        commit:$commit,annotated_tag:$release_id,
        archive_sha256:("sha256:" + $h_a),equality_verified:true
      }
    },
    components:{
      backend:{release_id:($release_id + "-backend"),path:"/fixture/backend",binding_sha256:$h_c,sha256sums_sha256:$h_d},
      frontend:{release_id:($release_id + "-frontend"),path:"/fixture/frontend",manifest_sha256:$h_e,tree_sha256:$h_f,sha256sums_sha256:$h_a}
    },
    frozen_data:{production_selection_sha256:$h_a,general_read_model_manifest_sha256:$h_b,country_outage_registry_sha256:$h_c},
    interactive_agent:{
      release_id:$release_id,path:"/fixture/interactive",
      release_manifest_path:"/fixture/interactive/RELEASE-MANIFEST.json",
      release_manifest_sha256:("sha256:" + $h_d),
      release_manifest_schema_version:"domeye_interactive_agent_release_manifest_v2",
      active_state_path:"/fixture/active.json",
      active_state_sha256:("sha256:" + $h_e),
      candidate_id:$candidate_id,
      candidate_manifest_path:"/fixture/interactive/project/contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json",
      candidate_manifest_sha256:("sha256:" + $h_f),
      acceptance_record_path:"/fixture/interactive/project/evaluation/country-outage/first-vertical-slice/runs/formal-fixture/acceptance-record-final.json",
      acceptance_record_id:$acceptance_id,
      acceptance_record_sha256:("sha256:" + $h_a),
      acceptance_replay_receipt_path:"/fixture/interactive/deployment/ACCEPTANCE-REPLAY.json",
      acceptance_replay_receipt_sha256:("sha256:" + $h_b),
      readiness_schema_version:"domeye_interactive_agent_release_probe_v2",
      readiness_identity_sha256:("sha256:" + $h_a),
      interactive_answer_attempt_limit:10,cost_policy:"audit_only",
      endpoint:{url:"http://127.0.0.1:28476",host:"127.0.0.1",port:28476,base_path:"/country-outage/chat"},
      activation_scope:"local_evaluation_only",candidate_production_deployed:false
    },
    protected_runtime:{
      database_changed:false,database_state_sha256:$h_b,
      nginx_changed:false,nginx_main_sha256:$h_c,nginx_site_sha256:$h_d
    },
    build_boundaries:{model_calls_during_prepare:0},
    rollback:{mode:"fail_closed",previous_release_id:null},
    promotion_contract:{candidate_canary_production_same_artifacts:true,rebuild_allowed:false}
  }
' > "${NORMALIZATION_CANDIDATE}"

build_promotion_fixture() {
    local label="$1"
    local conversation_hash="$2"
    local turn_hash="$3"
    local verified_at="$4"
    local output="$5"
    local conversation_id="conversation_sha256_${conversation_hash}"
    local turn_id="turn_sha256_${turn_hash}"
    local create_response="${NORMALIZATION_ROOT}/${label}-create.json"
    local turn_response="${NORMALIZATION_ROOT}/${label}-turn.json"
    local final_response="${NORMALIZATION_ROOT}/${label}-final.json"
    local internal_response="${NORMALIZATION_ROOT}/${label}-internal.json"

    jq -n --arg conversation_id "${conversation_id}" \
        '{conversation:{conversation_id:$conversation_id,turns:[]},deduplicated:false}' \
        > "${create_response}"
    jq -n --arg conversation_id "${conversation_id}" --arg turn_id "${turn_id}" '
      {conversation:{conversation_id:$conversation_id,turns:[{turn_id:$turn_id,turn_number:1}]},turn:{turn_id:$turn_id,turn_number:1},deduplicated:false}
    ' > "${turn_response}"
    jq -n --arg conversation_id "${conversation_id}" --arg turn_id "${turn_id}" '
      {conversation:{conversation_id:$conversation_id,turns:[{turn_id:$turn_id,turn_number:1,state:"completed",answer_source:"renderer"}]}}
    ' > "${final_response}"
    jq -n --arg conversation_id "${conversation_id}" --arg turn_id "${turn_id}" \
        --arg h_f "${HASH_F}" '
      {schema_version:"domeye_interactive_agent_turn_internal_record_v1",record_id:("turn-internal-record-sha256:" + $h_f),conversation_id:$conversation_id,turn_id:$turn_id}
    ' > "${internal_response}"

    local create_sha turn_sha response_sha internal_sha
    local create_body turn_body response_body internal_body
    create_sha="sha256:$(sha256sum "${create_response}" | awk '{print $1}')"
    turn_sha="sha256:$(sha256sum "${turn_response}" | awk '{print $1}')"
    response_sha="sha256:$(sha256sum "${final_response}" | awk '{print $1}')"
    internal_sha="sha256:$(sha256sum "${internal_response}" | awk '{print $1}')"
    create_body="$(base64 < "${create_response}" | tr -d '\n')"
    turn_body="$(base64 < "${turn_response}" | tr -d '\n')"
    response_body="$(base64 < "${final_response}" | tr -d '\n')"
    internal_body="$(base64 < "${internal_response}" | tr -d '\n')"

    jq -n --arg release_id "${NORMALIZATION_RELEASE}" \
        --arg candidate_id "${CANDIDATE_ID}" \
        --arg acceptance_id "${ACCEPTANCE_ID}" \
        --arg manifest_sha "sha256:${HASH_D}" \
        --arg active_sha "sha256:${HASH_E}" \
        --arg verified_at "${verified_at}" \
        --arg conversation_id "${conversation_id}" --arg turn_id "${turn_id}" \
        --arg create_sha "${create_sha}" --arg create_body "${create_body}" \
        --arg turn_sha "${turn_sha}" --arg turn_body "${turn_body}" \
        --arg response_sha "${response_sha}" --arg response_body "${response_body}" \
        --arg internal_sha "${internal_sha}" --arg internal_body "${internal_body}" \
        --arg h_a "${HASH_A}" --arg h_b "${HASH_B}" --arg h_c "${HASH_C}" \
        --arg h_d "${HASH_D}" --arg h_e "${HASH_E}" \
        --arg promotion_hash "${turn_hash}" '
      {
        promotion_id:("promotion-sha256:" + $promotion_hash),
        schema_version:"domeye_interactive_agent_promotion_v2",
        component:"domeye_interactive_agent_sidecar",
        release_id:$release_id,promotion_state:"verified",
        verified_at_utc:$verified_at,
        release_manifest_sha256:$manifest_sha,
        active_receipt_sha256:$active_sha,
        candidate_id:$candidate_id,
        acceptance_record_id:$acceptance_id,
        public_response:{
          origin:"http://127.0.0.1:28471",
          base_path:"/api/v2/country-outage/chat",
          conversation_id:$conversation_id,turn_id:$turn_id,
          question:"fixture 固定问题",
          create_response_sha256:$create_sha,
          create_response_body_base64:$create_body,
          turn_response_sha256:$turn_sha,
          turn_response_body_base64:$turn_body,
          response_sha256:$response_sha,
          response_body_base64:$response_body,
          conversation_deduplicated:false,turn_deduplicated:false,
          turn_number:1,conversation_turn_count:1,
          turn_projection_sha256:("sha256:" + $h_d),
          answer_text_sha256:("sha256:" + $h_e)
        },
        internal_record:{
          origin:"http://127.0.0.1:28476",
          base_path:"/country-outage/chat/internal",
          record_schema_version:"domeye_interactive_agent_turn_internal_record_v1",
          record_id:("turn-internal-record-sha256:" + $h_a),
          record_digest:("sha256:" + $h_b),
          response_sha256:$internal_sha,
          response_body_base64:$internal_body,
          public_projection_sha256:("sha256:" + $h_d),
          runtime_result_sha256:("sha256:" + $h_e)
        },
        result:{
          state:"completed",answer_success:true,workflow_completed:true,
          answer_source:"renderer",
          guard_schema_version:"domeye_agent_response_guard_v2",
          guard_decision:"pass",guard_assessment_status:"evaluated",
          style_policy_id:"domeye_answer_style_policy_v1",
          style_policy_digest:("sha256:" + $h_b),
          style_assessment_passed:true,
          final_answer_digest:("sha256:" + $h_a),
          oracle_digest:("sha256:" + $h_c),
          public_answer_present:true,internal_record_verified:true,
          public_internal_projection_equal:true,
          fallback_or_rejection_present:false
        }
      }
    ' > "${output}"
}

build_verification_fixture() {
    local mode="$1"
    local status="$2"
    local base_url="$3"
    local lifecycle="$4"
    local production_verified="$5"
    local promotion="$6"
    local output="$7"
    local promotion_sha promotion_body
    promotion_sha="sha256:$(sha256sum "${promotion}" | awk '{print $1}')"
    promotion_body="$(base64 < "${promotion}" | tr -d '\n')"
    jq -n --arg release_id "${NORMALIZATION_RELEASE}" \
        --arg candidate_id "${CANDIDATE_ID}" \
        --arg acceptance_id "${ACCEPTANCE_ID}" \
        --arg mode "${mode}" --arg status "${status}" \
        --arg base_url "${base_url}" --arg lifecycle "${lifecycle}" \
        --argjson production_verified "${production_verified}" \
        --arg promotion_sha "${promotion_sha}" \
        --arg promotion_body "${promotion_body}" \
        --arg manager_sha "sha256:${HASH_B}" \
        --slurpfile promotion "${promotion}" '
      {
        schema_version:"domeye_country_outage_general_runtime_verification_v2",
        status:$status,mode:$mode,release_id:$release_id,
        deterministic_runtime:{
          schema_version:"country_outage_general_runtime_verification_v1",
          status:"passed",mode:$mode,release_id:$release_id,
          repeat_order_concurrent_equal:true,
          boundaries:{database_changed:false,nginx_changed:false,read_api_checks_model_calls:0,interactive_agent_bound:true}
        },
        interactive_answer:({
          status:$status,base_url:$base_url,release_id:$release_id,
          candidate_id:$candidate_id,acceptance_record_id:$acceptance_id,
          conversation_id:$promotion[0].public_response.conversation_id,
          turn_id:$promotion[0].public_response.turn_id,
          question:$promotion[0].public_response.question,
          response_sha256:$promotion[0].public_response.response_sha256,
          promotion_receipt_sha256:$promotion_sha,
          promotion_receipt_body_base64:$promotion_body,
          promotion_receipt:$promotion[0]
        } + (if $production_verified then
          {manager_status_sha256:$manager_sha,lifecycle_state:$lifecycle,
           production_verified:true}
        else {} end))
      }
    ' > "${output}"
}

build_promotion_fixture canary "${HASH_A}" "${HASH_B}" \
    '2026-08-20T00:10:00Z' "${NORMALIZATION_CANARY_PROMOTION}"
build_promotion_fixture production "${HASH_D}" "${HASH_E}" \
    '2026-08-20T00:20:00Z' "${NORMALIZATION_PRODUCTION_PROMOTION}"
build_verification_fixture canary canary_verified \
    'http://127.0.0.1:38672' deployed false \
    "${NORMALIZATION_CANARY_PROMOTION}" "${NORMALIZATION_CANARY}"
build_verification_fixture production production_verified \
    'http://127.0.0.1:28471' verified true \
    "${NORMALIZATION_PRODUCTION_PROMOTION}" "${NORMALIZATION_PRODUCTION}"

canary_sha="sha256:$(sha256sum "${NORMALIZATION_CANARY}" | awk '{print $1}')"
production_sha="sha256:$(sha256sum "${NORMALIZATION_PRODUCTION}" | awk '{print $1}')"
jq -n --arg release_id "${NORMALIZATION_RELEASE}" \
    --arg candidate_id "${CANDIDATE_ID}" \
    --arg acceptance_id "${ACCEPTANCE_ID}" --arg tree_sha "${HASH_F}" \
    --arg canary_sha "${canary_sha}" --arg production_sha "${production_sha}" '
  {
    schema_version:"domeye_country_outage_general_deployment_v2",
    release_id:$release_id,status:"production_verified",production_verified:true,
    artifacts_rebuilt_during_promotion:false,
    components:{
      backend:{release_id:($release_id + "-backend"),path:"/fixture/backend"},
      frontend:{release_id:($release_id + "-frontend"),path:"/fixture/public",source_artifact_path:"/fixture/frontend/dist",tree_sha256:$tree_sha},
      interactive_agent:{release_id:$release_id,candidate_id:$candidate_id,acceptance_record_id:$acceptance_id}
    },
    cutover_quarantine:{path:"/fixture/quarantine",canonical_actual_directory:true,nginx_reference_present:false,routed:false,automatic_restore:false},
    verification:{
      canary:{path:"CANARY-VERIFICATION.json",sha256:$canary_sha},
      production:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_sha}
    },
    rollback:{mode:"fail_closed",previous_release_id:null,available:false}
  }
' > "${NORMALIZATION_DEPLOYMENT}"

jq -n --arg release_id "${NORMALIZATION_RELEASE}" \
    --arg candidate_id "${CANDIDATE_ID}" \
    --arg acceptance_id "${ACCEPTANCE_ID}" \
    --arg canary_sha "${canary_sha}" --arg production_sha "${production_sha}" '
  {
    schema_version:"domeye_country_outage_general_activation_v2",
    release_id:$release_id,phase:"production_verified",status:"passed",
    candidate:{
      backend:{release_id:($release_id + "-backend"),path:"/fixture/backend"},
      frontend:{release_id:($release_id + "-frontend")},
      interactive_agent:{release_id:$release_id,candidate_id:$candidate_id,acceptance_record_id:$acceptance_id}
    },
    verification:{
      canary:{path:"CANARY-VERIFICATION.json",sha256:$canary_sha},
      production:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_sha}
    },
    rollback:{mode:"fail_closed",previous_release_id:null}
  }
' > "${NORMALIZATION_STATE}"

"${SCRIPT_ROOT}/check-release-normalization.sh" --test-contracts \
    "${NORMALIZATION_RELEASE}" "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${NORMALIZATION_PRODUCTION}" \
    | jq -e '
      .schema_version == "domeye_release_normalization_contract_fixture_v1"
      and .status == "fixture_passed"
    ' >/dev/null || fail '真实 General v2 合同正例未通过'

expect_contract_reject() {
    local label="$1"
    local candidate="$2"
    local deployment="$3"
    local state="$4"
    local canary="$5"
    local production="$6"
    local output
    if output="$("${SCRIPT_ROOT}/check-release-normalization.sh" --test-contracts \
        "${NORMALIZATION_RELEASE}" "${candidate}" "${deployment}" \
        "${state}" "${canary}" "${production}" 2>&1)"; then
        fail "${label}：预期拒绝，但 v2 合同门禁成功"
    fi
    grep -F '发布归一检查失败：' <<<"${output}" >/dev/null \
        || fail "${label}：没有由归一合同失败关闭：${output}"
}

mutate_receipt_fixture() {
    local source="$1"
    local target="$2"
    local filter="$3"
    local staged="${target}.staged"
    local receipt="${target}.receipt"
    local receipt_sha receipt_body
    jq "${filter}" "${source}" > "${staged}"
    jq '.interactive_answer.promotion_receipt' "${staged}" > "${receipt}"
    receipt_sha="sha256:$(sha256sum "${receipt}" | awk '{print $1}')"
    receipt_body="$(base64 < "${receipt}" | tr -d '\n')"
    jq --arg receipt_sha "${receipt_sha}" --arg receipt_body "${receipt_body}" '
      .interactive_answer.promotion_receipt_sha256 = $receipt_sha
      | .interactive_answer.promotion_receipt_body_base64 = $receipt_body
    ' "${staged}" > "${target}.new"
    mv "${target}.new" "${target}"
}

mutate_fixture() {
    local source="$1"
    local target="$2"
    local filter="$3"
    jq "${filter}" "${source}" > "${target}.new"
    mv "${target}.new" "${target}"
}

bad="${NORMALIZATION_ROOT}/bad.json"
mutate_fixture "${NORMALIZATION_CANDIDATE}" "${bad}" \
    '.schema_version = "domeye_unified_release_candidate_v1"'
expect_contract_reject '旧 unified Candidate v1' "${bad}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${NORMALIZATION_PRODUCTION}"
mutate_fixture "${NORMALIZATION_DEPLOYMENT}" "${bad}" \
    '.schema_version = "domeye_unified_release_deployment_v1"'
expect_contract_reject '旧 unified Deployment v1' "${NORMALIZATION_CANDIDATE}" \
    "${bad}" "${NORMALIZATION_STATE}" "${NORMALIZATION_CANARY}" \
    "${NORMALIZATION_PRODUCTION}"
mutate_fixture "${NORMALIZATION_STATE}" "${bad}" \
    '.schema_version = "domeye_unified_release_activation_v1"'
expect_contract_reject '旧 activation schema' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${bad}" "${NORMALIZATION_CANARY}" \
    "${NORMALIZATION_PRODUCTION}"
mutate_fixture "${NORMALIZATION_CANARY}" "${bad}" \
    '.schema_version = "domeye_unified_release_verification_v1"'
expect_contract_reject '旧 VERIFICATION v1' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" "${bad}" \
    "${NORMALIZATION_PRODUCTION}"
mutate_fixture "${NORMALIZATION_CANDIDATE}" "${bad}" \
    '.interactive_agent.release_manifest_schema_version = "domeye_interactive_agent_release_manifest_v1"'
expect_contract_reject '旧 Interactive Agent manifest v1' "${bad}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${NORMALIZATION_PRODUCTION}"
mutate_fixture "${NORMALIZATION_CANDIDATE}" "${bad}" \
    '.interactive_agent.readiness_schema_version = "domeye_interactive_agent_release_probe_v1"'
expect_contract_reject '旧 Interactive Agent probe v1' "${bad}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${NORMALIZATION_PRODUCTION}"
mutate_fixture "${NORMALIZATION_CANDIDATE}" "${bad}" \
    '.interactive_agent.candidate_manifest_path = "/fixture/interactive/project/contracts/agent/domeye-first-vertical-slice/v1/candidate.json"'
expect_contract_reject '旧 Candidate v1 路径' "${bad}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${NORMALIZATION_PRODUCTION}"
mutate_fixture "${NORMALIZATION_CANDIDATE}" "${bad}" \
    '.interactive_agent.acceptance_record_id = "acceptance-record-sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"'
expect_contract_reject '外部 Acceptance 身份漂移' "${bad}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${NORMALIZATION_PRODUCTION}"
mutate_fixture "${NORMALIZATION_CANDIDATE}" "${bad}" \
    '.interactive_agent.endpoint.port = 28475 | .interactive_agent.endpoint.url = "http://127.0.0.1:28475"'
expect_contract_reject '旧 Sidecar 端口' "${bad}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${NORMALIZATION_PRODUCTION}"
mutate_receipt_fixture "${NORMALIZATION_PRODUCTION}" "${bad}" \
    '.interactive_answer.promotion_receipt.schema_version = "domeye_interactive_agent_promotion_v1"'
expect_contract_reject '旧 promotion v1' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${bad}"
mutate_receipt_fixture "${NORMALIZATION_PRODUCTION}" "${bad}" \
    '.interactive_answer.promotion_receipt.result.guard_decision = "block"'
expect_contract_reject 'Guard block 不能完成' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${bad}"
mutate_receipt_fixture "${NORMALIZATION_PRODUCTION}" "${bad}" \
    '.interactive_answer.promotion_receipt.result.guard_schema_version = "domeye_agent_response_guard_v1"'
expect_contract_reject 'Guard v1 不能完成' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${bad}"
mutate_receipt_fixture "${NORMALIZATION_PRODUCTION}" "${bad}" \
    '.interactive_answer.promotion_receipt.result.style_assessment_passed = false'
expect_contract_reject '样式评估失败不能完成' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${bad}"
mutate_receipt_fixture "${NORMALIZATION_PRODUCTION}" "${bad}" \
    '.interactive_answer.promotion_receipt.result.fallback_or_rejection_present = true'
expect_contract_reject '拒绝或回退不能完成' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${bad}"
mutate_receipt_fixture "${NORMALIZATION_PRODUCTION}" "${bad}" \
    '.interactive_answer.promotion_receipt.result.answer_source = "deterministic_fallback"'
expect_contract_reject '非 Renderer 回答不能完成' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${bad}"
mutate_receipt_fixture "${NORMALIZATION_PRODUCTION}" "${bad}" \
    '.interactive_answer.promotion_receipt.result.public_internal_projection_equal = false'
expect_contract_reject '公私投影不等不能完成' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${bad}"
mutate_receipt_fixture "${NORMALIZATION_PRODUCTION}" "${bad}" \
    '.interactive_answer.promotion_receipt.public_response.conversation_turn_count = 2'
expect_contract_reject '会话不是 1x1' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${bad}"
mutate_receipt_fixture "${NORMALIZATION_PRODUCTION}" "${bad}" \
    '.interactive_answer.promotion_receipt.public_response.conversation_id = "conversation_sha256_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" | .interactive_answer.promotion_receipt.public_response.turn_id = "turn_sha256_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" | .interactive_answer.conversation_id = .interactive_answer.promotion_receipt.public_response.conversation_id | .interactive_answer.turn_id = .interactive_answer.promotion_receipt.public_response.turn_id'
expect_contract_reject 'production 重用 canary conversation/turn' \
    "${NORMALIZATION_CANDIDATE}" "${NORMALIZATION_DEPLOYMENT}" \
    "${NORMALIZATION_STATE}" "${NORMALIZATION_CANARY}" "${bad}"
mutate_fixture "${NORMALIZATION_PRODUCTION}" "${bad}" \
    '.interactive_answer.conversation_id = "conversation_sha256_ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"'
expect_contract_reject 'wrapper conversation 未精确投影 receipt' \
    "${NORMALIZATION_CANDIDATE}" "${NORMALIZATION_DEPLOYMENT}" \
    "${NORMALIZATION_STATE}" "${NORMALIZATION_CANARY}" "${bad}"
mutate_fixture "${NORMALIZATION_PRODUCTION}" "${bad}" \
    '.interactive_answer.internal_record = .interactive_answer.promotion_receipt.internal_record'
expect_contract_reject '公共 wrapper 泄漏冗余 internal_record' \
    "${NORMALIZATION_CANDIDATE}" "${NORMALIZATION_DEPLOYMENT}" \
    "${NORMALIZATION_STATE}" "${NORMALIZATION_CANARY}" "${bad}"
mutate_receipt_fixture "${NORMALIZATION_PRODUCTION}" "${bad}" \
    '.interactive_answer.promotion_receipt.public_response.response_body_base64 = "e30K"'
expect_contract_reject '冻结公开响应字节漂移' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${bad}"
mutate_receipt_fixture "${NORMALIZATION_PRODUCTION}" "${bad}" \
    '.interactive_answer.promotion_receipt.internal_record.response_body_base64 = "e30K"'
expect_contract_reject '冻结内部记录字节漂移' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_CANARY}" "${bad}"
mutate_fixture "${NORMALIZATION_DEPLOYMENT}" "${bad}" \
    '.verification.canary.sha256 = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"'
expect_contract_reject 'Canary 证据摘要漂移' "${NORMALIZATION_CANDIDATE}" \
    "${bad}" "${NORMALIZATION_STATE}" "${NORMALIZATION_CANARY}" \
    "${NORMALIZATION_PRODUCTION}"

for required_text in \
    'refs/heads/main' \
    'refs/remotes/origin/main' \
    'git@github.com:xinghuahewo/domeye_.git' \
    '/usr/bin/env -i HOME=' \
    'PATH=/usr/bin:/bin' \
    '/usr/bin/git --no-replace-objects' \
    "GIT_SSH_COMMAND='/usr/bin/ssh " \
    'TRUSTED_RAW_ORIGIN_COUNT' \
    'TRUSTED_RAW_PUSH_COUNT' \
    'TRUSTED_ORIGIN_COUNT' \
    'TRUSTED_PUSH_COUNT' \
    'remote.origin.pushurl' \
    '唯一且不可改写的官方 GitHub SSH remote' \
    'source.archive_sha256' \
    'SOURCE-MANIFEST.json' \
    'BACKEND-SOURCE-BINDING.json' \
    'FRONTEND-MANIFEST.json' \
    'ACTIVATION-STATE.json' \
    'CANARY-VERIFICATION.json' \
    'PRODUCTION-VERIFICATION.json' \
    'domeye_country_outage_general_release_candidate_v2' \
    'domeye_interactive_agent_release_manifest_v2' \
    'domeye_interactive_agent_release_probe_v2' \
    'domeye_interactive_agent_promotion_v2' \
    'first-vertical-slice/v1.1/candidate.json' \
    'acceptance_record_id' \
    'public_response' \
    'internal_record' \
    'conversation_turn_count' \
    'guard_schema_version' \
    'style_assessment_passed' \
    'deployment/verify-release.mjs' \
    'promotion-receipt' \
    'http://127.0.0.1:28476' \
    'legacy_agent_surfaces_retired:true' \
    'require_port_closed 28474' \
    'require_port_closed 28475' \
    "require_screen_absent 'domeye_country_outage_agent'" \
    "require_screen_absent 'domeye_country_outage_p1_chat'" \
    '/api/v2/country-outage/reports' \
    '/api/v2/country-outage/investigations/retired-surface-probe' \
    'answer_source == "renderer"' \
    'fallback_or_rejection_present == false' \
    'protected_runtime.database_changed == false'; do
    grep -F "${required_text}" "${SCRIPT_ROOT}/check-release-normalization.sh" >/dev/null \
        || fail "归一检查缺少固定合同：${required_text}"
done

for forbidden_text in \
    'https://github.com/xinghuahewo/domeye_.git' \
    'domeye_unified_release_candidate_v1' \
    'domeye_interactive_agent_release_manifest_v1' \
    'domeye_interactive_agent_release_probe_v1' \
    'domeye_interactive_agent_promotion_v1' \
    'first-vertical-slice/v1/candidate.json' \
    '/VERIFICATION.json' \
    '/IDENTITY-EQUATION.json' \
    '/country-outage-agent/current'; do
    if grep -F "${forbidden_text}" "${SCRIPT_ROOT}/check-release-normalization.sh" >/dev/null; then
        fail "归一检查仍绑定旧发布合同：${forbidden_text}"
    fi
done

[[ "$(grep -Fc '"${INTERACTIVE_VERIFIER}" promotion-receipt' \
    "${SCRIPT_ROOT}/check-release-normalization.sh")" == 2 ]] \
    || fail '归一 live 路径没有分别重放 canary 与 production 冻结回执'
for forbidden_replay_text in 'internal-record' 'probe.mjs'; do
    if grep -F "${forbidden_replay_text}" \
        "${SCRIPT_ROOT}/check-release-normalization.sh" >/dev/null; then
        fail "归一 live 路径不得读取内部 live 证据：${forbidden_replay_text}"
    fi
done

printf '治理夹具通过：主干审批、快进保护、正式 tag 不可变及 General v2 正反归一合同均符合预期。\n'
