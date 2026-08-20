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

# 当前 General v2 的四份最终证据必须形成同一 Candidate/正确回答闭包；
# 这个纯合同入口只供本地敌对夹具使用，不输出生产 passed schema。
readonly NORMALIZATION_ROOT="${TEST_ROOT}/normalization"
readonly NORMALIZATION_RELEASE='20260820T000000Z-country-outage-interactive-agent-fixture'
readonly NORMALIZATION_CANDIDATE="${NORMALIZATION_ROOT}/CANDIDATE-MANIFEST.json"
readonly NORMALIZATION_DEPLOYMENT="${NORMALIZATION_ROOT}/DEPLOYMENT.json"
readonly NORMALIZATION_STATE="${NORMALIZATION_ROOT}/ACTIVATION-STATE.json"
readonly NORMALIZATION_VERIFICATION="${NORMALIZATION_ROOT}/PRODUCTION-VERIFICATION.json"
readonly NORMALIZATION_PROMOTION="${NORMALIZATION_ROOT}/promotion.json"
readonly NORMALIZATION_RESPONSE="${NORMALIZATION_ROOT}/response.json"
readonly HASH_A='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
readonly HASH_B='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
readonly HASH_C='cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'
readonly HASH_D='dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd'
readonly HASH_E='eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
readonly HASH_F='ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
readonly SOURCE_COMMIT='0123456789abcdef0123456789abcdef01234567'
readonly CANDIDATE_ID="manifest:sha256:${HASH_B}"
install -d -m 0700 "${NORMALIZATION_ROOT}"

jq -n --arg release_id "${NORMALIZATION_RELEASE}" \
    --arg commit "${SOURCE_COMMIT}" --arg candidate_id "${CANDIDATE_ID}" \
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
      active_state_path:"/fixture/active.json",
      active_state_sha256:("sha256:" + $h_e),
      candidate_id:$candidate_id,
      candidate_manifest_path:"/fixture/interactive/project/contracts/agent/domeye-first-vertical-slice/v1/candidate.json",
      candidate_manifest_sha256:("sha256:" + $h_f),
      readiness_schema_version:"domeye_interactive_agent_release_probe_v1",
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

printf '%s\n' '{"conversation":{"state":"completed","answer_source":"renderer"}}' \
    > "${NORMALIZATION_RESPONSE}"
response_sha="sha256:$(sha256sum "${NORMALIZATION_RESPONSE}" | awk '{print $1}')"
response_body="$(base64 < "${NORMALIZATION_RESPONSE}" | tr -d '\n')"
jq -n --arg release_id "${NORMALIZATION_RELEASE}" \
    --arg candidate_id "${CANDIDATE_ID}" \
    --arg manifest_sha "sha256:${HASH_D}" --arg active_sha "sha256:${HASH_E}" \
    --arg response_sha "${response_sha}" --arg response_body "${response_body}" \
    --arg oracle "sha256:${HASH_C}" --arg h_a "${HASH_A}" '
  {
    promotion_id:("promotion-sha256:" + $h_a),
    schema_version:"domeye_interactive_agent_promotion_v1",
    component:"domeye_interactive_agent_sidecar",
    release_id:$release_id,promotion_state:"verified",
    verified_at_utc:"2026-08-20T00:10:00Z",
    release_manifest_sha256:$manifest_sha,
    active_receipt_sha256:$active_sha,
    candidate_id:$candidate_id,
    backend:{
      origin:"http://127.0.0.1:28471",base_path:"/api/v2/country-outage/chat",
      conversation_id:("conversation_sha256_" + $h_a),
      turn_id:("turn_sha256_" + $h_a),
      question:"fixture 固定问题",response_sha256:$response_sha,
      response_body_base64:$response_body
    },
    result:{
      state:"completed",answer_success:true,workflow_completed:true,
      answer_source:"renderer",guard_decision:"pass",oracle_digest:$oracle,
      public_answer_present:true,fallback_or_rejection_present:false
    }
  }
' > "${NORMALIZATION_PROMOTION}"
promotion_sha="sha256:$(sha256sum "${NORMALIZATION_PROMOTION}" | awk '{print $1}')"
promotion_body="$(base64 < "${NORMALIZATION_PROMOTION}" | tr -d '\n')"

jq -n --arg release_id "${NORMALIZATION_RELEASE}" \
    --arg candidate_id "${CANDIDATE_ID}" \
    --arg promotion_sha "${promotion_sha}" --arg promotion_body "${promotion_body}" \
    --arg manager_sha "sha256:${HASH_B}" --slurpfile promotion "${NORMALIZATION_PROMOTION}" '
  {
    schema_version:"domeye_country_outage_general_runtime_verification_v2",
    status:"production_verified",mode:"production",release_id:$release_id,
    deterministic_runtime:{
      schema_version:"country_outage_general_runtime_verification_v1",
      status:"passed",mode:"production",release_id:$release_id,
      repeat_order_concurrent_equal:true,
      boundaries:{database_changed:false,nginx_changed:false,read_api_checks_model_calls:0,interactive_agent_bound:true}
    },
    interactive_answer:{
      status:"production_verified",base_url:"http://127.0.0.1:28471",
      release_id:$release_id,candidate_id:$candidate_id,
      manager_status_sha256:$manager_sha,
      promotion_receipt_sha256:$promotion_sha,
      promotion_receipt_body_base64:$promotion_body,
      promotion_receipt:$promotion[0],
      lifecycle_state:"verified",production_verified:true,
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
  }
' > "${NORMALIZATION_VERIFICATION}"

verification_sha="sha256:$(sha256sum "${NORMALIZATION_VERIFICATION}" | awk '{print $1}')"
jq -n --arg release_id "${NORMALIZATION_RELEASE}" \
    --arg candidate_id "${CANDIDATE_ID}" --arg tree_sha "${HASH_F}" \
    --arg verification_sha "${verification_sha}" '
  {
    schema_version:"domeye_country_outage_general_deployment_v2",
    release_id:$release_id,status:"production_verified",production_verified:true,
    artifacts_rebuilt_during_promotion:false,
    components:{
      backend:{release_id:($release_id + "-backend"),path:"/fixture/backend"},
      frontend:{release_id:($release_id + "-frontend"),path:"/fixture/public",source_artifact_path:"/fixture/frontend/dist",tree_sha256:$tree_sha},
      interactive_agent:{release_id:$release_id,candidate_id:$candidate_id}
    },
    cutover_quarantine:{path:"/fixture/quarantine",canonical_actual_directory:true,nginx_reference_present:false,routed:false,automatic_restore:false},
    verification:{path:"PRODUCTION-VERIFICATION.json",sha256:$verification_sha},
    rollback:{mode:"fail_closed",previous_release_id:null,available:false}
  }
' > "${NORMALIZATION_DEPLOYMENT}"

jq -n --arg release_id "${NORMALIZATION_RELEASE}" '
  {
    schema_version:"domeye_country_outage_general_activation_v2",
    release_id:$release_id,phase:"production_verified",status:"passed",
    candidate:{
      backend:{release_id:($release_id + "-backend"),path:"/fixture/backend"},
      frontend:{release_id:($release_id + "-frontend")},
      interactive_agent:{release_id:$release_id}
    },
    rollback:{mode:"fail_closed",previous_release_id:null}
  }
' > "${NORMALIZATION_STATE}"

"${SCRIPT_ROOT}/check-release-normalization.sh" --test-contracts \
    "${NORMALIZATION_RELEASE}" "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" \
    "${NORMALIZATION_VERIFICATION}" \
    | jq -e '
      .schema_version == "domeye_release_normalization_contract_fixture_v1"
      and .status == "fixture_passed"
    ' >/dev/null || fail '真实 General v2 合同正例未通过'

expect_contract_reject() {
    local label="$1"
    local candidate="$2"
    local deployment="$3"
    local state="$4"
    local verification="$5"
    local output
    if output="$("${SCRIPT_ROOT}/check-release-normalization.sh" --test-contracts \
        "${NORMALIZATION_RELEASE}" "${candidate}" "${deployment}" \
        "${state}" "${verification}" 2>&1)"; then
        fail "${label}：预期拒绝，但 v2 合同门禁成功"
    fi
    grep -F '发布归一检查失败：' <<<"${output}" >/dev/null \
        || fail "${label}：没有由归一合同失败关闭：${output}"
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
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" "${NORMALIZATION_VERIFICATION}"
mutate_fixture "${NORMALIZATION_DEPLOYMENT}" "${bad}" \
    '.schema_version = "domeye_unified_release_deployment_v1"'
expect_contract_reject '旧 unified Deployment v1' "${NORMALIZATION_CANDIDATE}" \
    "${bad}" "${NORMALIZATION_STATE}" "${NORMALIZATION_VERIFICATION}"
mutate_fixture "${NORMALIZATION_STATE}" "${bad}" \
    '.schema_version = "domeye_unified_release_activation_v1"'
expect_contract_reject '旧 activation schema' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${bad}" "${NORMALIZATION_VERIFICATION}"
mutate_fixture "${NORMALIZATION_VERIFICATION}" "${bad}" \
    '.schema_version = "domeye_unified_release_verification_v1"'
expect_contract_reject '旧 VERIFICATION v1' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" "${bad}"
mutate_fixture "${NORMALIZATION_CANDIDATE}" "${bad}" \
    '.interactive_agent.endpoint.port = 28475 | .interactive_agent.endpoint.url = "http://127.0.0.1:28475"'
expect_contract_reject '旧 Sidecar 端口' "${bad}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" "${NORMALIZATION_VERIFICATION}"
mutate_fixture "${NORMALIZATION_VERIFICATION}" "${bad}" \
    '.interactive_answer.guard_decision = "block"'
expect_contract_reject 'Guard block 不能完成' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" "${bad}"
mutate_fixture "${NORMALIZATION_VERIFICATION}" "${bad}" \
    '.interactive_answer.fallback_or_rejection_present = true'
expect_contract_reject '拒绝或回退不能完成' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" "${bad}"
mutate_fixture "${NORMALIZATION_VERIFICATION}" "${bad}" \
    '.interactive_answer.answer_source = "deterministic_fallback"'
expect_contract_reject '非 Renderer 回答不能完成' "${NORMALIZATION_CANDIDATE}" \
    "${NORMALIZATION_DEPLOYMENT}" "${NORMALIZATION_STATE}" "${bad}"
mutate_fixture "${NORMALIZATION_DEPLOYMENT}" "${bad}" \
    '.verification.sha256 = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"'
expect_contract_reject '生产证据摘要漂移' "${NORMALIZATION_CANDIDATE}" \
    "${bad}" "${NORMALIZATION_STATE}" "${NORMALIZATION_VERIFICATION}"

for required_text in \
    'refs/heads/main' \
    'refs/remotes/origin/main' \
    'source.archive_sha256' \
    'SOURCE-MANIFEST.json' \
    'BACKEND-SOURCE-BINDING.json' \
    'FRONTEND-MANIFEST.json' \
    'ACTIVATION-STATE.json' \
    'PRODUCTION-VERIFICATION.json' \
    'domeye_country_outage_general_release_candidate_v2' \
    'domeye_interactive_agent_promotion_v1' \
    'http://127.0.0.1:28476' \
    'answer_source == "renderer"' \
    'fallback_or_rejection_present == false' \
    'protected_runtime.database_changed == false'; do
    grep -F "${required_text}" "${SCRIPT_ROOT}/check-release-normalization.sh" >/dev/null \
        || fail "归一检查缺少固定合同：${required_text}"
done

for forbidden_text in \
    'domeye_unified_release_candidate_v1' \
    '/VERIFICATION.json' \
    '/IDENTITY-EQUATION.json' \
    '/country-outage-agent/current'; do
    if grep -F "${forbidden_text}" "${SCRIPT_ROOT}/check-release-normalization.sh" >/dev/null; then
        fail "归一检查仍绑定旧发布合同：${forbidden_text}"
    fi
done

printf '治理夹具通过：主干审批、快进保护、正式 tag 不可变及 General v2 正反归一合同均符合预期。\n'
