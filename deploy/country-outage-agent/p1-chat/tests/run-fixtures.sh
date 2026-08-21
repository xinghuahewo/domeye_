#!/usr/bin/env bash

set -Eeuo pipefail

readonly TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly DEPLOY_DIR="$(cd -- "${TEST_DIR}/.." && pwd -P)"
readonly REPOSITORY="$(cd -- "${DEPLOY_DIR}/../../.." && pwd -P)"
readonly FIXTURE_ROOT="$(mktemp -d /private/tmp/domeye-interactive-agent-test.XXXXXX)"
readonly NODE="$(command -v node)"
readonly SYSTEM_JQ="$(command -v jq)"
readonly SYSTEM_UNLINK="$(command -v unlink)"
readonly TOKEN='fixture-token-abcdefghijklmnopqrstuvwxyz'
readonly VERIFIER_TOKEN='fixture-verifier-token-abcdefghijklmnopqrstuvwxyz'
readonly RELEASE_ID='20260819T120000Z-country-outage-interactive-agent-fixture'
readonly FIXED_QUESTION='在这次冻结 publication 的观测窗口内，RRC25 看到的固定前缀可见 IPv4 地址量最低是多少，首次在什么观测时刻出现？首值、末值、最大值和极差分别是多少？'
readonly CONVERSATION_ID="conversation_sha256_$(printf 'a%.0s' {1..64})"
readonly TURN_ID="turn_sha256_$(printf 'b%.0s' {1..64})"
readonly FORMAL_GATE_CANDIDATE_ID="manifest:sha256:$(printf 'e%.0s' {1..64})"
readonly RUNTIME_ROOT="${FIXTURE_ROOT}/runtime/country-outage-interactive-agent"
readonly STATE_ROOT="${RUNTIME_ROOT}/state"
readonly CONFIG="${FIXTURE_ROOT}/runtime/config/country-outage-interactive-agent.env"
readonly REAL_RELEASE_ROOT="${RUNTIME_ROOT}/releases/${RELEASE_ID}"
readonly BINDING_RELEASE_ID='20260819T120001Z-country-outage-interactive-agent-binding'
readonly BINDING_RELEASE_ROOT="${RUNTIME_ROOT}/releases/${BINDING_RELEASE_ID}"
readonly WRONG_RELEASE_ROOT="${RUNTIME_ROOT}/releases/20260819T120002Z-country-outage-interactive-agent-wrong"

cleanup() {
    if [[ -d "${FIXTURE_ROOT}" && ! -L "${FIXTURE_ROOT}" ]]; then
        chmod -R u+w "${FIXTURE_ROOT}" 2>/dev/null || true
        find "${FIXTURE_ROOT}" -depth -delete
    fi
}
trap cleanup EXIT

fail() {
    printf '失败：%s\n' "$*" >&2
    exit 1
}

assert_fails() {
    local label="$1"; shift
    if "$@" >"${FIXTURE_ROOT}/unexpected-success.out" \
        2>"${FIXTURE_ROOT}/expected-failure.err"; then
        fail "应失败却成功：${label}"
    fi
}

file_mode() {
    if stat -c '%a' "$1" >/dev/null 2>&1; then
        stat -c '%a' "$1"
    else
        stat -f '%Lp' "$1" | sed 's/^0*//'
    fi
}

mkdir -p "${RUNTIME_ROOT}/releases" "${STATE_ROOT}" \
    "${FIXTURE_ROOT}/runtime/config" "${FIXTURE_ROOT}/tools/node/bin" \
    "${FIXTURE_ROOT}/tools/bin"
chmod 0700 "${FIXTURE_ROOT}" "${FIXTURE_ROOT}/runtime" \
    "${FIXTURE_ROOT}/runtime/config" "${RUNTIME_ROOT}" \
    "${RUNTIME_ROOT}/releases" "${STATE_ROOT}" "${FIXTURE_ROOT}/tools" \
    "${FIXTURE_ROOT}/tools/node" "${FIXTURE_ROOT}/tools/node/bin" \
    "${FIXTURE_ROOT}/tools/bin"
ln -s "${NODE}" "${FIXTURE_ROOT}/tools/node/bin/node"
ln -s "$(command -v npm)" "${FIXTURE_ROOT}/tools/node/bin/npm"
for command_name in flock screen ss; do
    printf '#!/usr/bin/env sh\nexit 0\n' \
        > "${FIXTURE_ROOT}/tools/bin/${command_name}"
    chmod 0500 "${FIXTURE_ROOT}/tools/bin/${command_name}"
done

printf '{"fixture":true}\n' \
    > "${FIXTURE_ROOT}/runtime/config/country-outage-pi-auth.json"
chmod 0600 "${FIXTURE_ROOT}/runtime/config/country-outage-pi-auth.json"
chgrp "$(id -g)" \
    "${FIXTURE_ROOT}/runtime/config/country-outage-pi-auth.json"
cat > "${CONFIG}" <<EOF
COUNTRY_OUTAGE_AGENT_URL=http://127.0.0.1:28476
COUNTRY_OUTAGE_AGENT_SHARED_TOKEN=${TOKEN}
COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN=${VERIFIER_TOKEN}
COUNTRY_OUTAGE_AGENT_HOST=127.0.0.1
COUNTRY_OUTAGE_AGENT_PORT=28476
DOMEYE_API_BASE_URL=http://127.0.0.1:28473/api/v2/
COUNTRY_OUTAGE_FIRST_SLICE_PROJECT_ROOT=${RUNTIME_ROOT}/current/project
COUNTRY_OUTAGE_FIRST_SLICE_CANDIDATE_MANIFEST=${RUNTIME_ROOT}/current/project/contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json
COUNTRY_OUTAGE_PI_AUTH_PATH=${FIXTURE_ROOT}/runtime/config/country-outage-pi-auth.json
COUNTRY_OUTAGE_INTERACTIVE_AGENT_API_TIMEOUT_MS=15000
COUNTRY_OUTAGE_INTERACTIVE_AGENT_CONVERSATION_TTL_MS=1800000
COUNTRY_OUTAGE_INTERACTIVE_AGENT_TURN_TIMEOUT_MS=120000
EOF
chmod 0600 "${CONFIG}"
chgrp "$(id -g)" "${CONFIG}"

manager() {
    PATH="${FIXTURE_ROOT}/tools/bin:${PATH}" \
    DOMEYE_INTERACTIVE_AGENT_TEST_ROOT="${FIXTURE_ROOT}" \
        "${DEPLOY_DIR}/manage.sh" "$@"
}

verifier() {
    DOMEYE_INTERACTIVE_AGENT_TEST_ROOT="${FIXTURE_ROOT}" \
        "${NODE}" "${DEPLOY_DIR}/verify-release.mjs" "$@"
}

# 配置只接受新 Interactive Agent 的固定键和值。
manager _test_validate_config >/dev/null \
    || fail '新 Interactive Agent 固定配置未通过'
cp "${CONFIG}" "${FIXTURE_ROOT}/config.saved.env"
sed '/^COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN=/d' "${CONFIG}" \
    > "${FIXTURE_ROOT}/config.no-verifier.env"
mv "${FIXTURE_ROOT}/config.no-verifier.env" "${CONFIG}"
chmod 0600 "${CONFIG}"
chgrp "$(id -g)" "${CONFIG}"
assert_fails '缺少独立验证器 Token' manager _test_validate_config
cp "${FIXTURE_ROOT}/config.saved.env" "${CONFIG}"
sed 's/^COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN=.*/COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN=short/' \
    "${CONFIG}" > "${FIXTURE_ROOT}/config.short-verifier.env"
mv "${FIXTURE_ROOT}/config.short-verifier.env" "${CONFIG}"
chmod 0600 "${CONFIG}"
chgrp "$(id -g)" "${CONFIG}"
assert_fails '独立验证器 Token 过短' manager _test_validate_config
cp "${FIXTURE_ROOT}/config.saved.env" "${CONFIG}"
sed "s/^COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN=.*/COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN=${TOKEN}/" \
    "${CONFIG}" > "${FIXTURE_ROOT}/config.same-verifier.env"
mv "${FIXTURE_ROOT}/config.same-verifier.env" "${CONFIG}"
chmod 0600 "${CONFIG}"
chgrp "$(id -g)" "${CONFIG}"
assert_fails '独立验证器 Token 不得复用共享 Token' manager _test_validate_config
cp "${FIXTURE_ROOT}/config.saved.env" "${CONFIG}"
chmod 0600 "${CONFIG}"
chgrp "$(id -g)" "${CONFIG}"
sed 's/^COUNTRY_OUTAGE_AGENT_SHARED_TOKEN=.*/COUNTRY_OUTAGE_AGENT_SHARED_TOKEN=CHANGE_ME_SHARED_TOKEN_ABCDEFGHIJKLMNOPQRSTUVWXYZ/' \
    "${CONFIG}" > "${FIXTURE_ROOT}/config.shared-placeholder.env"
mv "${FIXTURE_ROOT}/config.shared-placeholder.env" "${CONFIG}"
chmod 0600 "${CONFIG}"
chgrp "$(id -g)" "${CONFIG}"
assert_fails '共享 Token 占位符不得启动' manager _test_validate_config
cp "${FIXTURE_ROOT}/config.saved.env" "${CONFIG}"
sed 's/^COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN=.*/COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN=replace-with-verifier-token-placeholder/' \
    "${CONFIG}" > "${FIXTURE_ROOT}/config.verifier-placeholder.env"
mv "${FIXTURE_ROOT}/config.verifier-placeholder.env" "${CONFIG}"
chmod 0600 "${CONFIG}"
chgrp "$(id -g)" "${CONFIG}"
assert_fails '验证器 Token 占位符不得启动' manager _test_validate_config
cp "${FIXTURE_ROOT}/config.saved.env" "${CONFIG}"
chmod 0600 "${CONFIG}"
chgrp "$(id -g)" "${CONFIG}"
printf 'UNAUTHORIZED_RELEASE_BUDGET=1\n' >> "${CONFIG}"
assert_fails '未授权配置键' manager _test_validate_config
cp "${FIXTURE_ROOT}/config.saved.env" "${CONFIG}"
sed 's/COUNTRY_OUTAGE_AGENT_PORT=28476/COUNTRY_OUTAGE_AGENT_PORT=28477/' \
    "${CONFIG}" > "${FIXTURE_ROOT}/config.drift.env"
mv "${FIXTURE_ROOT}/config.drift.env" "${CONFIG}"
chmod 0600 "${CONFIG}"
chgrp "$(id -g)" "${CONFIG}"
assert_fails '生产端口漂移' manager _test_validate_config
cp "${FIXTURE_ROOT}/config.saved.env" "${CONFIG}"
chmod 0600 "${CONFIG}"
chgrp "$(id -g)" "${CONFIG}"

# 配置继续固定为 current symlink；子进程环境必须绑定同一个真实 release，
# 并拒绝 current 逃逸 release 根目录或串到另一个 release。
mkdir -p "${BINDING_RELEASE_ROOT}/project/$(dirname \
    'contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json')" \
    "${WRONG_RELEASE_ROOT}" "${FIXTURE_ROOT}/escaped-release"
printf '{}\n' > "${BINDING_RELEASE_ROOT}/project/contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json"
ln -s "${BINDING_RELEASE_ROOT}" "${RUNTIME_ROOT}/current"
manager _test_launch_environment "${BINDING_RELEASE_ID}" \
    > "${FIXTURE_ROOT}/launch-environment.out" \
    || fail '首发 current symlink 未能绑定到真实 release'
cat > "${FIXTURE_ROOT}/launch-environment.expected" <<EOF
COUNTRY_OUTAGE_FIRST_SLICE_PROJECT_ROOT=${BINDING_RELEASE_ROOT}/project
COUNTRY_OUTAGE_FIRST_SLICE_CANDIDATE_MANIFEST=${BINDING_RELEASE_ROOT}/project/contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json
EOF
cmp -s "${FIXTURE_ROOT}/launch-environment.expected" \
    "${FIXTURE_ROOT}/launch-environment.out" \
    || fail '子进程 Project/Candidate 没有绑定同一真实 release 目录'
if grep -F "${TOKEN}" "${FIXTURE_ROOT}/launch-environment.out" >/dev/null \
    || grep -F "${VERIFIER_TOKEN}" "${FIXTURE_ROOT}/launch-environment.out" >/dev/null; then
    fail '启动参数动态输出泄露共享或验证器 Token'
fi
readonly LAUNCH_BLOCK="${FIXTURE_ROOT}/launch-block.txt"
sed -n '/if ! screen -L -Logfile/,/then$/p' "${DEPLOY_DIR}/manage.sh" \
    > "${LAUNCH_BLOCK}"
if grep -E 'COUNTRY_OUTAGE_AGENT_(SHARED|VERIFIER)_TOKEN=|environment=' \
    "${LAUNCH_BLOCK}" >/dev/null; then
    fail 'screen 启动 argv 静态包含 Token 或展开的 environment 数组'
fi
unlink "${RUNTIME_ROOT}/current"

ln -s "${FIXTURE_ROOT}/escaped-release" "${RUNTIME_ROOT}/current"
assert_fails 'current 路径逃逸 release 根目录' \
    manager _test_launch_environment "${BINDING_RELEASE_ID}"
grep -F 'current 解析路径逃逸 release 根目录' \
    "${FIXTURE_ROOT}/expected-failure.err" >/dev/null \
    || fail 'current 路径逃逸未命中启动路径门'
unlink "${RUNTIME_ROOT}/current"

ln -s "${WRONG_RELEASE_ROOT}" "${RUNTIME_ROOT}/current"
assert_fails 'current 指向错误 release' \
    manager _test_launch_environment "${BINDING_RELEASE_ID}"
grep -F 'current 指向错误 release' \
    "${FIXTURE_ROOT}/expected-failure.err" >/dev/null \
    || fail '错误 release 未命中启动路径门'
unlink "${RUNTIME_ROOT}/current"

# 28476 必须只绑定 IPv4 loopback；相同 PID 的 wildcard/IPv6 wildcard 也拒绝。
printf '%s\n' \
    'LISTEN 0 511 127.0.0.1:28476 0.0.0.0:* users:(("node",pid=4242,fd=20))' \
    > "${FIXTURE_ROOT}/listener-loopback.txt"
printf '%s\n' \
    'LISTEN 0 511 0.0.0.0:28476 0.0.0.0:* users:(("node",pid=4242,fd=20))' \
    > "${FIXTURE_ROOT}/listener-wildcard-v4.txt"
printf '%s\n' \
    'LISTEN 0 511 [::]:28476 [::]:* users:(("node",pid=4242,fd=20))' \
    > "${FIXTURE_ROOT}/listener-wildcard-v6.txt"
manager _test_listener_identity 4242 \
    "${FIXTURE_ROOT}/listener-loopback.txt" >/dev/null \
    || fail '127.0.0.1:28476 精确监听未通过'
assert_fails '拒绝 0.0.0.0:28476 wildcard' \
    manager _test_listener_identity 4242 \
    "${FIXTURE_ROOT}/listener-wildcard-v4.txt"
assert_fails '拒绝 [::]:28476 wildcard' \
    manager _test_listener_identity 4242 \
    "${FIXTURE_ROOT}/listener-wildcard-v6.txt"

# 发布管理器和探针的安全参数必须保持显式：root curl 不读取 curlrc、不走代理、
# 不跟随重定向；readiness 绑定回答合同；内部记录仅使用 verifier token 和 loopback。
readonly BACKEND_REQUEST_BLOCK="${FIXTURE_ROOT}/backend-request-block.txt"
sed -n '/^backend_request()/,/^}/p' "${DEPLOY_DIR}/manage.sh" \
    > "${BACKEND_REQUEST_BLOCK}"
grep -F 'local -a arguments=(--disable --noproxy' \
    "${BACKEND_REQUEST_BLOCK}" >/dev/null \
    || fail 'Backend curl 参数数组首位不是 --disable/--noproxy'
grep -F -- "--proto '=http'" "${BACKEND_REQUEST_BLOCK}" >/dev/null \
    && grep -F -- '--max-redirs 0' "${BACKEND_REQUEST_BLOCK}" >/dev/null \
    || fail 'Backend curl 未固定 HTTP 协议或零重定向'
grep -F "'answer_presentation_contract'" "${DEPLOY_DIR}/probe.mjs" >/dev/null \
    || fail 'readiness 未绑定 answer_presentation_contract'
grep -F 'Authorization: `Bearer ${config.verifierToken}`' \
    "${DEPLOY_DIR}/probe.mjs" >/dev/null \
    && grep -F '${FIXED_URL}/country-outage/chat/internal/conversations/' \
        "${DEPLOY_DIR}/probe.mjs" >/dev/null \
    || fail 'internal-record 未固定 verifier token 与 loopback URL'
if rg -n '\bfetch\(|\bcurl\b' "${DEPLOY_DIR}/verify-release.mjs" >/dev/null; then
    fail 'promotion-receipt verifier 不得 live GET，只能重放冻结字节'
fi
assert_fails 'prepare 缺少外部 Candidate/Acceptance 双 ID' \
    manager prepare fixture-release fixture.tar.gz \
    aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa fixture-tag only-candidate-id
grep -F 'prepare <release-id> <source.tar.gz> <commit> <annotated-tag> <approved-candidate-id> <approved-acceptance-record-id>' \
    "${FIXTURE_ROOT}/expected-failure.err" >/dev/null \
    || fail 'prepare 未把外部 Candidate/Acceptance pin 设为必填'
grep -F "active_schema" "${DEPLOY_DIR}/manage.sh" >/dev/null \
    && grep -F "domeye_interactive_agent_release_manifest_v1" \
        "${DEPLOY_DIR}/manage.sh" >/dev/null \
    && grep -F 'v1→v2 迁移 release 不得把旧合同绑定为回滚前序' \
        "${DEPLOY_DIR}/manage.sh" >/dev/null \
    || fail 'v1 active 到 v2 的迁移未保持 fail_closed 且禁止旧前序回滚'

# v2 promotion 必须冻结一次全新 create、一次第一 Turn 和最终唯一 Turn；
# 公开投影严格最小，任何去重、内部字段、旧 schema 或失败终态都拒绝。
jq -n --arg candidate_id "${FORMAL_GATE_CANDIDATE_ID}" '{
  candidate_id:$candidate_id,
  payload:{
  contract:{version:"domeye.first-vertical-slice/v1.0",digest:("sha256:" + ("1" * 64))},
  answer_presentation_contract:{version:"domeye.first-vertical-slice.answer-presentation/v1.0",digest:("sha256:" + ("2" * 64))},
  data_identity:{
  event_type:"country_outage",incident_id:"incident_go_v1_fixture",
  publication_id:"country_outage_publication_v1_fixture",revision:1,
  collector_id:"rrc25",cohort_id:"country_event_cohort_v1_fixture",
  country_code:"IR",window_start_utc:"2026-02-27T00:10:00Z",
  window_end_utc:"2026-03-11T00:00:00Z",data_through:"2026-03-11T00:00:00Z",
  is_final_in_data_range:false,lifecycle_state:"event_end_unknown"
}}}' > "${FIXTURE_ROOT}/v2-public-candidate.json"
jq -n --arg conversation_id "${CONVERSATION_ID}" '{
  conversation:{
    schema_version:"domeye_interactive_agent_conversation_v2",
    conversation_id:$conversation_id,
    binding:{
      event_type:"country_outage",incident_id:"incident_go_v1_fixture",
      publication_id:"country_outage_publication_v1_fixture",revision:1,
      collector_id:"rrc25",cohort_id:"country_event_cohort_v1_fixture",
      country_code:"IR",window_start_utc:"2026-02-27T00:10:00Z",
      window_end_utc:"2026-03-11T00:00:00Z",data_through:"2026-03-11T00:00:00Z",
      is_final_in_data_range:false,lifecycle_state:"event_end_unknown",
      event_reference:"country_outage/2026-02-27 09:12:32/IR/1/r"
    },turns:[],expires_at:"2026-08-21T01:30:00Z",
    created_at:"2026-08-21T01:00:00Z"
  },deduplicated:false
}' > "${FIXTURE_ROOT}/v2-create.json"
jq -n --arg turn_id "${TURN_ID}" --arg question "${FIXED_QUESTION}" '{
  turn:{turn_id:$turn_id,turn_number:1,question:$question,state:"executing",
    answer_success:false,workflow_completed:false,
    created_at:"2026-08-21T01:00:01Z"},deduplicated:false
}' > "${FIXTURE_ROOT}/v2-turn.json"
jq -n --arg conversation_id "${CONVERSATION_ID}" \
    --arg turn_id "${TURN_ID}" --arg question "${FIXED_QUESTION}" '{
  conversation:{
    schema_version:"domeye_interactive_agent_conversation_v2",
    conversation_id:$conversation_id,
    binding:{
      event_type:"country_outage",incident_id:"incident_go_v1_fixture",
      publication_id:"country_outage_publication_v1_fixture",revision:1,
      collector_id:"rrc25",cohort_id:"country_event_cohort_v1_fixture",
      country_code:"IR",window_start_utc:"2026-02-27T00:10:00Z",
      window_end_utc:"2026-03-11T00:00:00Z",data_through:"2026-03-11T00:00:00Z",
      is_final_in_data_range:false,lifecycle_state:"event_end_unknown",
      event_reference:"country_outage/2026-02-27 09:12:32/IR/1/r"
    },turns:[{
      turn_id:$turn_id,turn_number:1,question:$question,state:"completed",
      answer_success:true,workflow_completed:true,
      answer:{schema_version:"domeye_interactive_agent_turn_answer_v2",
        answerability:"supported",answer_source:"renderer",
        answer_text:"最低值为 9,577,728，首次观测于 2026 年 2 月 28 日 14:35 UTC。",
        basis:{source_label_zh:"Domeye 国家中断观测数据",
          observed_object_zh:"RRC25 观测到的固定前缀可见 IPv4 地址量",
          window_start_utc:"2026-02-27T00:10:00Z",
          window_end_utc:"2026-03-11T00:00:00Z",
          important_boundary_zh:"仅表示 RRC25 单一观察点的 BGP 控制面观测，不能据此推断全国或用户实际影响、原因、责任或真实恢复。"}
      },created_at:"2026-08-21T01:00:01Z",completed_at:"2026-08-21T01:00:10Z"
    }],expires_at:"2026-08-21T01:30:00Z",created_at:"2026-08-21T01:00:00Z"
  }
}' > "${FIXTURE_ROOT}/v2-final.json"
verifier _test-v2-public-evidence \
    "${FIXTURE_ROOT}/v2-create.json" "${FIXTURE_ROOT}/v2-turn.json" \
    "${FIXTURE_ROOT}/v2-final.json" "${FIXTURE_ROOT}/v2-public-candidate.json" \
    "${CONVERSATION_ID}" "${TURN_ID}" \
    || fail 'v2 最小公开 create/turn/final 正向夹具未通过'
"${NODE}" --input-type=module - \
    "${FIXTURE_ROOT}/v2-final.json" \
    "${FIXTURE_ROOT}/v2-public-candidate.json" \
    "${FIXTURE_ROOT}/v2-internal.json" <<'EOF'
import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'

const [finalPath, candidatePath, outputPath] = process.argv.slice(2)
const response = JSON.parse(readFileSync(finalPath, 'utf8'))
const candidate = JSON.parse(readFileSync(candidatePath, 'utf8'))
const turn = response.conversation.turns[0]
const canonical = (value) => {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  return `{${Object.keys(value).sort().map((key) =>
    `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
}
const digest = (value) => `sha256:${createHash('sha256')
  .update(canonical(value)).digest('hex')}`
const textDigest = (value) => `sha256:${createHash('sha256')
  .update(value, 'utf8').digest('hex')}`
const body = {
  schema_version: 'domeye_interactive_agent_turn_internal_record_v1',
  conversation_id: response.conversation.conversation_id,
  turn_id: turn.turn_id,
  candidate_id: candidate.candidate_id,
  contract_version: candidate.payload.contract.version,
  contract_digest: candidate.payload.contract.digest,
  answer_presentation_contract_version:
    candidate.payload.answer_presentation_contract.version,
  answer_presentation_contract_digest:
    candidate.payload.answer_presentation_contract.digest,
  data_identity: candidate.payload.data_identity,
  identity_receipt: { fixture: true },
  authorization_derivation: { fixture: true },
  public_projection: turn,
  public_answer_sha256: textDigest(turn.answer.answer_text),
  public_projection_sha256: digest(turn),
  runtime_result: { fixture: true },
  failure: null,
  recorded_at_utc: '2026-08-21T01:00:11Z',
}
const recordDigest = digest(body)
const record = {
  ...body,
  record_id: `turn-internal-record-sha256:${recordDigest.slice(7)}`,
  record_digest: recordDigest,
}
writeFileSync(outputPath, `${JSON.stringify({ record }, null, 2)}\n`)
EOF
verifier _test-v2-internal-binding \
    "${FIXTURE_ROOT}/v2-final.json" "${FIXTURE_ROOT}/v2-internal.json" \
    "${FIXTURE_ROOT}/v2-public-candidate.json" \
    || fail '同 conversation/turn 的内部记录摘要正向夹具未通过'
jq '.record.record_digest=("sha256:" + ("0" * 64))' \
    "${FIXTURE_ROOT}/v2-internal.json" > "${FIXTURE_ROOT}/v2-internal-digest-drift.json"
assert_fails '内部 record_digest 篡改' verifier _test-v2-internal-binding \
    "${FIXTURE_ROOT}/v2-final.json" \
    "${FIXTURE_ROOT}/v2-internal-digest-drift.json" \
    "${FIXTURE_ROOT}/v2-public-candidate.json"
jq '.record.public_projection.question="篡改问题"' \
    "${FIXTURE_ROOT}/v2-internal.json" \
    > "${FIXTURE_ROOT}/v2-internal-projection-drift.json"
assert_fails '内部 public_projection 篡改' verifier _test-v2-internal-binding \
    "${FIXTURE_ROOT}/v2-final.json" \
    "${FIXTURE_ROOT}/v2-internal-projection-drift.json" \
    "${FIXTURE_ROOT}/v2-public-candidate.json"
jq '.record.public_answer_sha256=("sha256:" + ("3" * 64))' \
    "${FIXTURE_ROOT}/v2-internal.json" \
    > "${FIXTURE_ROOT}/v2-internal-answer-drift.json"
assert_fails '内部 public_answer_sha256 篡改' verifier _test-v2-internal-binding \
    "${FIXTURE_ROOT}/v2-final.json" \
    "${FIXTURE_ROOT}/v2-internal-answer-drift.json" \
    "${FIXTURE_ROOT}/v2-public-candidate.json"
jq '.deduplicated=true' "${FIXTURE_ROOT}/v2-create.json" \
    > "${FIXTURE_ROOT}/v2-create-deduplicated.json"
assert_fails 'Conversation create 去重不得晋级' verifier _test-v2-public-evidence \
    "${FIXTURE_ROOT}/v2-create-deduplicated.json" "${FIXTURE_ROOT}/v2-turn.json" \
    "${FIXTURE_ROOT}/v2-final.json" "${FIXTURE_ROOT}/v2-public-candidate.json" \
    "${CONVERSATION_ID}" "${TURN_ID}"
jq '.deduplicated=true' "${FIXTURE_ROOT}/v2-turn.json" \
    > "${FIXTURE_ROOT}/v2-turn-deduplicated.json"
assert_fails 'Turn create 去重不得晋级' verifier _test-v2-public-evidence \
    "${FIXTURE_ROOT}/v2-create.json" "${FIXTURE_ROOT}/v2-turn-deduplicated.json" \
    "${FIXTURE_ROOT}/v2-final.json" "${FIXTURE_ROOT}/v2-public-candidate.json" \
    "${CONVERSATION_ID}" "${TURN_ID}"
jq '.turn.turn_number=2' "${FIXTURE_ROOT}/v2-turn.json" \
    > "${FIXTURE_ROOT}/v2-turn-number-two.json"
assert_fails '非第一 Turn 不得晋级' verifier _test-v2-public-evidence \
    "${FIXTURE_ROOT}/v2-create.json" "${FIXTURE_ROOT}/v2-turn-number-two.json" \
    "${FIXTURE_ROOT}/v2-final.json" "${FIXTURE_ROOT}/v2-public-candidate.json" \
    "${CONVERSATION_ID}" "${TURN_ID}"
jq '.conversation.turns += [.conversation.turns[0]]' \
    "${FIXTURE_ROOT}/v2-final.json" > "${FIXTURE_ROOT}/v2-final-two-turns.json"
assert_fails '会话不是唯一一 Turn' verifier _test-v2-public-evidence \
    "${FIXTURE_ROOT}/v2-create.json" "${FIXTURE_ROOT}/v2-turn.json" \
    "${FIXTURE_ROOT}/v2-final-two-turns.json" "${FIXTURE_ROOT}/v2-public-candidate.json" \
    "${CONVERSATION_ID}" "${TURN_ID}"
jq '.conversation.turns[0].answer.candidate_id="manifest:sha256:internal-leak"' \
    "${FIXTURE_ROOT}/v2-final.json" > "${FIXTURE_ROOT}/v2-final-internal-field.json"
assert_fails '公开回答夹带 Candidate 内部字段' verifier _test-v2-public-evidence \
    "${FIXTURE_ROOT}/v2-create.json" "${FIXTURE_ROOT}/v2-turn.json" \
    "${FIXTURE_ROOT}/v2-final-internal-field.json" "${FIXTURE_ROOT}/v2-public-candidate.json" \
    "${CONVERSATION_ID}" "${TURN_ID}"
jq '.conversation.schema_version="domeye_interactive_agent_conversation_v1"' \
    "${FIXTURE_ROOT}/v2-final.json" > "${FIXTURE_ROOT}/v1-final.json"
assert_fails '旧 Conversation v1 不得晋级' verifier _test-v2-public-evidence \
    "${FIXTURE_ROOT}/v2-create.json" "${FIXTURE_ROOT}/v2-turn.json" \
    "${FIXTURE_ROOT}/v1-final.json" "${FIXTURE_ROOT}/v2-public-candidate.json" \
    "${CONVERSATION_ID}" "${TURN_ID}"
jq '.conversation.turns[0].state="stopped" |
  .conversation.turns[0].answer_success=false |
  .conversation.turns[0].workflow_completed=false |
  .conversation.turns[0].answer={schema_version:"domeye_interactive_agent_turn_answer_v2",answerability:"stopped",answer_source:"none",answer_text:"未形成答案"}' \
    "${FIXTURE_ROOT}/v2-final.json" > "${FIXTURE_ROOT}/v2-final-stopped.json"
assert_fails 'stopped 不能算完成' verifier _test-v2-public-evidence \
    "${FIXTURE_ROOT}/v2-create.json" "${FIXTURE_ROOT}/v2-turn.json" \
    "${FIXTURE_ROOT}/v2-final-stopped.json" "${FIXTURE_ROOT}/v2-public-candidate.json" \
    "${CONVERSATION_ID}" "${TURN_ID}"
printf '%s\n' '{"one":1,"one":2}' > "${FIXTURE_ROOT}/duplicate-key.json"
assert_fails '受信 JSON 拒绝重复 key' verifier _test-json-no-duplicate \
    "${FIXTURE_ROOT}/duplicate-key.json"
verifier _test-v2-promotion-timeline \
    '2026-08-21T01:00:05Z' '2026-08-21T01:00:03Z' \
    '2026-08-21T01:00:04Z' \
    || fail 'promotion 合法时间线未通过'
assert_fails 'verified_at 不得早于公开 Turn 完成时间' \
    verifier _test-v2-promotion-timeline \
    '2026-08-21T01:00:02Z' '2026-08-21T01:00:03Z' \
    '2026-08-21T01:00:01Z'
assert_fails 'verified_at 不得早于内部记录形成时间' \
    verifier _test-v2-promotion-timeline \
    '2026-08-21T01:00:02Z' '2026-08-21T01:00:01Z' \
    '2026-08-21T01:00:03Z'
assert_fails '不存在的 UTC 日历日期不得被自动归一化' \
    verifier _test-v2-promotion-timeline \
    '2026-02-30T01:00:05Z' '2026-02-28T01:00:03Z' \
    '2026-02-28T01:00:04Z'
assert_fails '验收重放拒绝无效外部 Candidate pin' \
    "${NODE}" "${DEPLOY_DIR}/verify-release.mjs" acceptance-replay \
    "${REPOSITORY}" \
    'evaluation/country-outage/first-vertical-slice/runs/formal-20260819T1839/acceptance-record-final.json' \
    'manifest:sha256:bad' \
    'acceptance-record-sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
assert_fails '旧 v1 Candidate/Acceptance 不得进入 v2 重放' \
    "${NODE}" "${DEPLOY_DIR}/verify-release.mjs" acceptance-replay \
    "${REPOSITORY}" \
    'evaluation/country-outage/first-vertical-slice/runs/formal-20260819T1839/acceptance-record-final.json' \
    'manifest:sha256:4236c3a8c94cc9bc4c01df8791139961bc84bda01b61fc7e07eaeed07772044d' \
    'acceptance-record-sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'

# 来源门从固定 checkout 验 annotated tag，并精确比对规范解包树。
readonly TRUSTED_CHECKOUT="${FIXTURE_ROOT}/trusted-checkout"
readonly TRUSTED_ORIGIN="${FIXTURE_ROOT}/trusted-origin.git"
git init -q --bare "${TRUSTED_ORIGIN}"
mkdir "${TRUSTED_CHECKOUT}"
git -C "${TRUSTED_CHECKOUT}" init -q -b main
git -C "${TRUSTED_CHECKOUT}" config user.name 'Domeye Fixture'
git -C "${TRUSTED_CHECKOUT}" config user.email 'fixture@domeye.invalid'
git -C "${TRUSTED_CHECKOUT}" remote add origin "${TRUSTED_ORIGIN}"
printf '可信源码\n' > "${TRUSTED_CHECKOUT}/README.md"
mkdir "${TRUSTED_CHECKOUT}/bin"
printf '#!/usr/bin/env sh\nexit 0\n' > "${TRUSTED_CHECKOUT}/bin/serve"
chmod 0755 "${TRUSTED_CHECKOUT}/bin/serve"
git -C "${TRUSTED_CHECKOUT}" add README.md bin/serve
git -C "${TRUSTED_CHECKOUT}" commit -q -m 'fixture source'
readonly SOURCE_COMMIT="$(git -C "${TRUSTED_CHECKOUT}" rev-parse HEAD)"
git -C "${TRUSTED_CHECKOUT}" tag -a "${RELEASE_ID}" \
    -m 'fixture annotated release'
git -C "${TRUSTED_CHECKOUT}" push -q -u origin main
git -C "${TRUSTED_CHECKOUT}" push -q origin "refs/tags/${RELEASE_ID}"
readonly SOURCE_ARCHIVE="${FIXTURE_ROOT}/source-good.tar.gz"
git -C "${TRUSTED_CHECKOUT}" archive --format=tar.gz \
    --output="${SOURCE_ARCHIVE}" "${SOURCE_COMMIT}"
manager _test_verify_source_archive "${SOURCE_ARCHIVE}" \
    "${SOURCE_COMMIT}" "${RELEASE_ID}" >/dev/null \
    || fail '合法 annotated tag 与 git archive 未通过来源门'

# 外部 Git 身份变量不得把受信命令重定向到诱饵仓库。
readonly DECOY_CHECKOUT="${FIXTURE_ROOT}/decoy-checkout"
mkdir "${DECOY_CHECKOUT}"
git -C "${DECOY_CHECKOUT}" init -q -b main
git -C "${DECOY_CHECKOUT}" config user.name 'Domeye Decoy'
git -C "${DECOY_CHECKOUT}" config user.email 'decoy@domeye.invalid'
printf '诱饵源码\n' > "${DECOY_CHECKOUT}/README.md"
git -C "${DECOY_CHECKOUT}" add README.md
git -C "${DECOY_CHECKOUT}" commit -q -m 'decoy source'
GIT_DIR="${DECOY_CHECKOUT}/.git" \
GIT_WORK_TREE="${DECOY_CHECKOUT}" \
GIT_OBJECT_DIRECTORY="${DECOY_CHECKOUT}/.git/objects" \
    manager _test_verify_source_archive "${SOURCE_ARCHIVE}" \
        "${SOURCE_COMMIT}" "${RELEASE_ID}" >/dev/null \
    || fail '外部 Git 身份变量重定向了受信 checkout'

# 调用者 PATH 中的伪 Git 也不得进入受信 Git 路径。
readonly FAKE_GIT_MARKER="${FIXTURE_ROOT}/fake-git-invoked"
printf '#!/bin/sh\nprintf invoked > "%s"\nexit 99\n' \
    "${FAKE_GIT_MARKER}" > "${FIXTURE_ROOT}/tools/bin/git"
chmod 0500 "${FIXTURE_ROOT}/tools/bin/git"
manager _test_verify_source_archive "${SOURCE_ARCHIVE}" \
    "${SOURCE_COMMIT}" "${RELEASE_ID}" >/dev/null \
    || fail '固定系统 Git 路径未通过合法来源闭包'
[[ ! -e "${FAKE_GIT_MARKER}" ]] \
    || fail '受信 Git 调用了 PATH 中的伪 git'
rm "${FIXTURE_ROOT}/tools/bin/git"

# 空/非空附加 fetch URL、额外 remote、本地 URL rewrite 或独立 pushurl
# 都必须在 fetch 前失败关闭。
git -C "${TRUSTED_CHECKOUT}" config --add remote.origin.url ''
assert_fails '受信 origin 不得追加空 fetch URL' \
    manager _test_verify_source_archive "${SOURCE_ARCHIVE}" \
    "${SOURCE_COMMIT}" "${RELEASE_ID}"
git -C "${TRUSTED_CHECKOUT}" config --unset-all remote.origin.url
git -C "${TRUSTED_CHECKOUT}" config remote.origin.url "${TRUSTED_ORIGIN}"
git -C "${TRUSTED_CHECKOUT}" config --add remote.origin.url \
    "${TRUSTED_ORIGIN}-extra"
assert_fails '受信 origin 不得追加第二个 fetch URL' \
    manager _test_verify_source_archive "${SOURCE_ARCHIVE}" \
    "${SOURCE_COMMIT}" "${RELEASE_ID}"
git -C "${TRUSTED_CHECKOUT}" config --unset-all remote.origin.url
git -C "${TRUSTED_CHECKOUT}" config remote.origin.url "${TRUSTED_ORIGIN}"
git -C "${TRUSTED_CHECKOUT}" remote add extra "${TRUSTED_ORIGIN}"
assert_fails '受信 checkout 不得存在额外 remote' \
    manager _test_verify_source_archive "${SOURCE_ARCHIVE}" \
    "${SOURCE_COMMIT}" "${RELEASE_ID}"
git -C "${TRUSTED_CHECKOUT}" remote remove extra
git -C "${TRUSTED_CHECKOUT}" config \
    "url.file://${FIXTURE_ROOT}/missing-origin.insteadOf" "${TRUSTED_ORIGIN}"
assert_fails '受信 origin 不得被本地 insteadOf 改写' \
    manager _test_verify_source_archive "${SOURCE_ARCHIVE}" \
    "${SOURCE_COMMIT}" "${RELEASE_ID}"
git -C "${TRUSTED_CHECKOUT}" config --unset-all \
    "url.file://${FIXTURE_ROOT}/missing-origin.insteadOf"
git -C "${TRUSTED_CHECKOUT}" remote set-url --add --push origin \
    "${TRUSTED_ORIGIN}-push"
assert_fails '受信 origin 不得配置独立 pushurl' \
    manager _test_verify_source_archive "${SOURCE_ARCHIVE}" \
    "${SOURCE_COMMIT}" "${RELEASE_ID}"
git -C "${TRUSTED_CHECKOUT}" config --unset-all remote.origin.pushurl

git -C "${TRUSTED_CHECKOUT}" tag lightweight-fixture
assert_fails 'lightweight tag' manager _test_verify_source_archive \
    "${SOURCE_ARCHIVE}" "${SOURCE_COMMIT}" lightweight-fixture
assert_fails 'tag 解引用 commit 漂移' manager _test_verify_source_archive \
    "${SOURCE_ARCHIVE}" 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' "${RELEASE_ID}"

mkdir "${FIXTURE_ROOT}/tampered-tree"
tar -xzf "${SOURCE_ARCHIVE}" -C "${FIXTURE_ROOT}/tampered-tree"
printf '篡改\n' >> "${FIXTURE_ROOT}/tampered-tree/README.md"
tar -czf "${FIXTURE_ROOT}/source-content-drift.tar.gz" \
    -C "${FIXTURE_ROOT}/tampered-tree" .
assert_fails '归档文件内容漂移' manager _test_verify_source_archive \
    "${FIXTURE_ROOT}/source-content-drift.tar.gz" \
    "${SOURCE_COMMIT}" "${RELEASE_ID}"
cp "${SOURCE_ARCHIVE}" "${FIXTURE_ROOT}/source.saved.tar.gz"
find "${FIXTURE_ROOT}/tampered-tree" -depth -delete
mkdir "${FIXTURE_ROOT}/tampered-tree"
tar -xzf "${SOURCE_ARCHIVE}" -C "${FIXTURE_ROOT}/tampered-tree"
chmod 0644 "${FIXTURE_ROOT}/tampered-tree/bin/serve"
tar -czf "${FIXTURE_ROOT}/source-mode-drift.tar.gz" \
    -C "${FIXTURE_ROOT}/tampered-tree" .
assert_fails '归档执行位漂移' manager _test_verify_source_archive \
    "${FIXTURE_ROOT}/source-mode-drift.tar.gz" \
    "${SOURCE_COMMIT}" "${RELEASE_ID}"
find "${FIXTURE_ROOT}/tampered-tree" -depth -delete
mkdir -p "${FIXTURE_ROOT}/tampered-tree/extra-empty-directory"
tar -xzf "${SOURCE_ARCHIVE}" -C "${FIXTURE_ROOT}/tampered-tree"
tar -czf "${FIXTURE_ROOT}/source-empty-directory-drift.tar.gz" \
    -C "${FIXTURE_ROOT}/tampered-tree" .
assert_fails '归档额外空目录漂移' manager _test_verify_source_archive \
    "${FIXTURE_ROOT}/source-empty-directory-drift.tar.gz" \
    "${SOURCE_COMMIT}" "${RELEASE_ID}"

# Candidate 必须由 base_commit 的首个单父、candidate-only 子提交冻结，之后不得再改。
readonly CANDIDATE_RELATIVE='contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json'
readonly CHAIN_CANDIDATE="${TRUSTED_CHECKOUT}/${CANDIDATE_RELATIVE}"
mkdir -p "$(dirname "${CHAIN_CANDIDATE}")"
jq -n --arg base "${SOURCE_COMMIT}" \
    '{payload:{base_commit:$base},fixture_state:"sealed"}' \
    > "${CHAIN_CANDIDATE}"
git -C "${TRUSTED_CHECKOUT}" add "${CANDIDATE_RELATIVE}"
git -C "${TRUSTED_CHECKOUT}" commit -q -m 'freeze candidate only'
printf '发布集成保持 Candidate 不变\n' > "${TRUSTED_CHECKOUT}/RELEASE-INTEGRATION.md"
git -C "${TRUSTED_CHECKOUT}" add RELEASE-INTEGRATION.md
git -C "${TRUSTED_CHECKOUT}" commit -q -m 'integrate release without candidate drift'
readonly VALID_CHAIN_SOURCE="$(git -C "${TRUSTED_CHECKOUT}" rev-parse HEAD)"
manager _test_verify_candidate_git_chain \
    "${VALID_CHAIN_SOURCE}" "${CHAIN_CANDIDATE}" >/dev/null \
    || fail '合法 Candidate Git 父链未通过'

jq '.payload.base_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"' \
    "${CHAIN_CANDIDATE}" > "${FIXTURE_ROOT}/candidate-fake-base.json"
assert_fails '伪造 Candidate base_commit' \
    manager _test_verify_candidate_git_chain \
    "${VALID_CHAIN_SOURCE}" "${FIXTURE_ROOT}/candidate-fake-base.json"
grep -F 'base_commit 不是 release source commit 的受信祖先' \
    "${FIXTURE_ROOT}/expected-failure.err" >/dev/null \
    || fail '伪 base_commit 未命中祖先门'

readonly NON_CANDIDATE_ONLY_BASE="${VALID_CHAIN_SOURCE}"
jq --arg base "${NON_CANDIDATE_ONLY_BASE}" \
    '.payload.base_commit=$base | .fixture_state="bad-first-child"' \
    "${CHAIN_CANDIDATE}" > "${FIXTURE_ROOT}/candidate.next.json"
mv "${FIXTURE_ROOT}/candidate.next.json" "${CHAIN_CANDIDATE}"
printf '不应夹带\n' > "${TRUSTED_CHECKOUT}/EXTRA.md"
git -C "${TRUSTED_CHECKOUT}" add "${CANDIDATE_RELATIVE}" EXTRA.md
git -C "${TRUSTED_CHECKOUT}" commit -q -m 'invalid mixed candidate commit'
readonly NON_CANDIDATE_ONLY_SOURCE="$(git -C "${TRUSTED_CHECKOUT}" rev-parse HEAD)"
assert_fails '首个 Candidate 子提交夹带其他路径' \
    manager _test_verify_candidate_git_chain \
    "${NON_CANDIDATE_ONLY_SOURCE}" "${CHAIN_CANDIDATE}"
grep -F '必须且只能修改 candidate.json' \
    "${FIXTURE_ROOT}/expected-failure.err" >/dev/null \
    || fail '非 candidate-only 首子提交未命中路径门'

readonly LATER_CHANGE_BASE="${NON_CANDIDATE_ONLY_SOURCE}"
jq --arg base "${LATER_CHANGE_BASE}" \
    '.payload.base_commit=$base | .fixture_state="second-seal"' \
    "${CHAIN_CANDIDATE}" > "${FIXTURE_ROOT}/candidate.next.json"
mv "${FIXTURE_ROOT}/candidate.next.json" "${CHAIN_CANDIDATE}"
git -C "${TRUSTED_CHECKOUT}" add "${CANDIDATE_RELATIVE}"
git -C "${TRUSTED_CHECKOUT}" commit -q -m 'second candidate-only seal'
jq '.fixture_state="changed-after-seal"' \
    "${CHAIN_CANDIDATE}" > "${FIXTURE_ROOT}/candidate.next.json"
mv "${FIXTURE_ROOT}/candidate.next.json" "${CHAIN_CANDIDATE}"
git -C "${TRUSTED_CHECKOUT}" add "${CANDIDATE_RELATIVE}"
git -C "${TRUSTED_CHECKOUT}" commit -q -m 'invalid later candidate change'
readonly LATER_CHANGE_SOURCE="$(git -C "${TRUSTED_CHECKOUT}" rev-parse HEAD)"
assert_fails 'Candidate seal 后再次修改' \
    manager _test_verify_candidate_git_chain \
    "${LATER_CHANGE_SOURCE}" "${CHAIN_CANDIDATE}"
grep -F '首个 Candidate commit 后被再次修改' \
    "${FIXTURE_ROOT}/expected-failure.err" >/dev/null \
    || fail 'Candidate 后续变化未命中历史门'

printf 'main 已推进\n' > "${TRUSTED_CHECKOUT}/NEXT.md"
git -C "${TRUSTED_CHECKOUT}" add NEXT.md
git -C "${TRUSTED_CHECKOUT}" commit -q -m 'advance main'
assert_fails 'main 与发布 commit 漂移' manager _test_verify_source_archive \
    "${SOURCE_ARCHIVE}" "${SOURCE_COMMIT}" "${RELEASE_ID}"
readonly ADVANCED_COMMIT="$(git -C "${TRUSTED_CHECKOUT}" rev-parse HEAD)"
git -C "${TRUSTED_CHECKOUT}" push -q --force origin \
    "${ADVANCED_COMMIT}:refs/heads/main"
git -C "${TRUSTED_CHECKOUT}" update-ref refs/heads/main "${SOURCE_COMMIT}"
assert_fails 'origin/main 与发布 commit 漂移' manager _test_verify_source_archive \
    "${SOURCE_ARCHIVE}" "${SOURCE_COMMIT}" "${RELEASE_ID}"

# 当前任务不伪造 v2 Candidate 或双签正式运行；完整正向由 Evaluator 的临时
# Ed25519 bundle 测试覆盖。发布夹具只保留旧 v1 的显式拒绝与故障注入骨架。
mkdir -p "${REAL_RELEASE_ROOT}/source" "${REAL_RELEASE_ROOT}/deployment"
printf '{}\n' > "${REAL_RELEASE_ROOT}/RELEASE-MANIFEST.json"

# 即使 verify_release 被 if 调用、末尾 verifier 返回成功，中间 SHA 门失败也不能被吞掉。
printf '%064d  %s\n' 0 'RELEASE-MANIFEST.json' \
    > "${REAL_RELEASE_ROOT}/SHA256SUMS"
unlink "${FIXTURE_ROOT}/tools/node/bin/node"
printf '#!/usr/bin/env sh\nexit 0\n' \
    > "${FIXTURE_ROOT}/tools/node/bin/node"
chmod 0500 "${FIXTURE_ROOT}/tools/node/bin/node"
assert_fails 'if 条件上下文不能吞掉 SHA256SUMS 中间门失败' \
    manager _test_verify_release_condition "${RELEASE_ID}"
unlink "${FIXTURE_ROOT}/tools/node/bin/node"
ln -s "${NODE}" "${FIXTURE_ROOT}/tools/node/bin/node"

# write_active 在 if 条件上下文中也必须显式拒绝 jq 中间失败，且不能留下空回执。
{
    printf '#!/usr/bin/env sh\n'
    printf 'if [ "${1:-}" = "-n" ]; then exit 90; fi\n'
    printf 'exec "%s" "$@"\n' "${SYSTEM_JQ}"
} > "${FIXTURE_ROOT}/tools/bin/jq"
chmod 0500 "${FIXTURE_ROOT}/tools/bin/jq"
assert_fails 'if 条件上下文不能吞掉 active JSON 生成失败' \
    manager _test_write_active_condition "${RELEASE_ID}" 4242
[[ ! -e "${STATE_ROOT}/active.json" \
    && -z "$(find "${STATE_ROOT}" -maxdepth 1 -name '.active.*' -print -quit)" ]] \
    || fail 'write_active 失败后留下空或临时 active 回执'
"${SYSTEM_UNLINK}" "${FIXTURE_ROOT}/tools/bin/jq"

# restore 的 release/config/archive 前置门失败时，不能触发 screen 启动或公开请求。
chmod u+w "${FIXTURE_ROOT}/tools/bin/screen"
{
    printf '#!/usr/bin/env sh\n'
    printf 'printf "screen-called\\n" >> "%s"\n' \
        "${FIXTURE_ROOT}/screen-calls.log"
    printf 'exit 1\n'
} > "${FIXTURE_ROOT}/tools/bin/screen"
chmod 0500 "${FIXTURE_ROOT}/tools/bin/screen"
assert_fails 'restore 前置 release 门失败不得启动或请求' \
    manager _test_restore_previous_condition "${RELEASE_ID}"
[[ ! -e "${FIXTURE_ROOT}/screen-calls.log" \
    && ! -e "${RUNTIME_ROOT}/current" ]] \
    || fail 'restore 在不可变 release 验证前触发了启动'
chmod u+w "${FIXTURE_ROOT}/tools/bin/screen"
printf '#!/usr/bin/env sh\nexit 0\n' \
    > "${FIXTURE_ROOT}/tools/bin/screen"
chmod 0500 "${FIXTURE_ROOT}/tools/bin/screen"

# current 清理失败时必须保留 active 证据并返回失败，不能宣称 stop/rollback 完成。
ln -s "${REAL_RELEASE_ROOT}" "${RUNTIME_ROOT}/current"
printf '{"fixture":"active-evidence"}\n' > "${STATE_ROOT}/active.json"
chmod 0600 "${STATE_ROOT}/active.json"
printf '#!/usr/bin/env sh\nexit 91\n' \
    > "${FIXTURE_ROOT}/tools/bin/unlink"
chmod 0500 "${FIXTURE_ROOT}/tools/bin/unlink"
assert_fails 'current 无法清除时不得删除 active 或报告成功' \
    manager _test_clear_state_condition
[[ -L "${RUNTIME_ROOT}/current" && -f "${STATE_ROOT}/active.json" ]] \
    || fail 'current 清理失败后没有保留 active 状态证据'
"${SYSTEM_UNLINK}" "${FIXTURE_ROOT}/tools/bin/unlink"
"${SYSTEM_UNLINK}" "${RUNTIME_ROOT}/current"
"${SYSTEM_UNLINK}" "${STATE_ROOT}/active.json"

# promotion 归档是原子、0600、不可覆盖；旧回执离开 active 路径后不能再被复用。
readonly PROMOTION_ROOT="${STATE_ROOT}/promotions"
readonly HISTORY_ROOT="${STATE_ROOT}/promotion-history/${RELEASE_ID}"
mkdir -p "${PROMOTION_ROOT}"
chmod 0700 "${PROMOTION_ROOT}"
readonly PROMOTION_FILE="${PROMOTION_ROOT}/${RELEASE_ID}.json"
printf '%s\n' '{"promotion_id":"promotion-sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","verified_at_utc":"2026-08-19T12:02:00Z"}' \
    > "${PROMOTION_FILE}"
chmod 0600 "${PROMOTION_FILE}"
chgrp "$(id -g)" "${PROMOTION_FILE}"
manager _test_archive_promotion "${RELEASE_ID}" >/dev/null \
    || fail '旧 promotion 未能原子归档'
[[ ! -e "${PROMOTION_FILE}" ]] || fail '旧 promotion 仍留在 active 路径'
readonly HISTORY_FILE="$(find "${HISTORY_ROOT}" -type f -name '*.json' -print -quit)"
[[ -n "${HISTORY_FILE}" && "$(file_mode "${HISTORY_FILE}")" == '600' ]] \
    || fail 'promotion history 不是受信 0600 普通文件'
printf '%s\n' '{"promotion_id":"promotion-sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","verified_at_utc":"2026-08-19T12:02:00Z"}' \
    > "${PROMOTION_FILE}"
chmod 0600 "${PROMOTION_FILE}"
chgrp "$(id -g)" "${PROMOTION_FILE}"
assert_fails 'promotion history 不可覆盖' \
    manager _test_archive_promotion "${RELEASE_ID}"
[[ -f "${PROMOTION_FILE}" ]] || fail '归档碰撞错误地移走了待处理 promotion'

# promotion 只能在四份临时 raw 已清理后原子发布。顺序或失败分支一旦漂移，
# cleanup 失败就可能留下被 status 解释为 verified 的回执。
readonly RAW_CLEANUP_LINE="$(rg -n 'if ! cleanup_promotion_raw_responses; then' \
    "${DEPLOY_DIR}/manage.sh" | tail -1 | cut -d: -f1)"
readonly PROMOTION_PUBLISH_LINE="$(rg -n 'if ! mv -n "\$\{receipt_tmp\}" "\$\{promotion\}"; then' \
    "${DEPLOY_DIR}/manage.sh" | tail -1 | cut -d: -f1)"
[[ "${RAW_CLEANUP_LINE}" =~ ^[1-9][0-9]*$ \
    && "${PROMOTION_PUBLISH_LINE}" =~ ^[1-9][0-9]*$ \
    && ${RAW_CLEANUP_LINE} -lt ${PROMOTION_PUBLISH_LINE} ]] \
    || fail 'promotion raw cleanup 未发生在最终 mv 前'
sed -n "${RAW_CLEANUP_LINE},${PROMOTION_PUBLISH_LINE}p" \
    "${DEPLOY_DIR}/manage.sh" > "${FIXTURE_ROOT}/promotion-cleanup-order.txt"
grep -F '临时原始响应清理失败，未写 verified promotion' \
    "${FIXTURE_ROOT}/promotion-cleanup-order.txt" >/dev/null \
    && grep -F 'return 1' "${FIXTURE_ROOT}/promotion-cleanup-order.txt" >/dev/null \
    || fail 'raw cleanup 失败分支未在 promotion 发布前失败关闭'

# Probe 的所有受信 JSON 路径必须复用 duplicate-key parser；带重复
# deployment_state 的 active 即使 promotion 参数为 '-' 也不能被后值覆盖。
grep -F 'parseJsonWithoutDuplicateKeys' "${DEPLOY_DIR}/probe.mjs" >/dev/null \
    && grep -F "const value = parseJsonWithoutDuplicateKeys(readFileSync(file, 'utf8'))" \
        "${DEPLOY_DIR}/probe.mjs" >/dev/null \
    && grep -F "envelope = parseJsonWithoutDuplicateKeys(body.toString('utf8'))" \
        "${DEPLOY_DIR}/probe.mjs" >/dev/null \
    || fail 'Probe state/internal raw 未统一使用 duplicate-key parser'
readonly PROBE_ACTIVE_VERIFY_LINE="$(rg -n \
    'const active = verifyActive\(args\[3\], verified\)' \
    "${DEPLOY_DIR}/probe.mjs" | cut -d: -f1)"
readonly PROBE_NO_PROMOTION_LINE="$(rg -n \
    "if \(args\[4\] === '-'\)" "${DEPLOY_DIR}/probe.mjs" | cut -d: -f1)"
[[ "${PROBE_ACTIVE_VERIFY_LINE}" =~ ^[1-9][0-9]*$ \
    && "${PROBE_NO_PROMOTION_LINE}" =~ ^[1-9][0-9]*$ \
    && ${PROBE_ACTIVE_VERIFY_LINE} -lt ${PROBE_NO_PROMOTION_LINE} ]] \
    || fail "Probe status 的 promotion '-' 分支绕过了 active 受信解析"
printf '%s\n' '{"deployment_state":"deployed","deployment_state":"verified"}' \
    > "${FIXTURE_ROOT}/duplicate-active.json"
assert_fails '重复 deployment_state 的 active JSON 必拒' \
    verifier _test-json-no-duplicate "${FIXTURE_ROOT}/duplicate-active.json"

# 生产 GitHub 访问只能使用唯一、不可改写的官方 SSH remote；测试模式仍只接受
# 本夹具的本地 bare origin。
grep -F "expected_origin='git@github.com:xinghuahewo/domeye_.git'" \
    "${DEPLOY_DIR}/manage.sh" >/dev/null \
    && grep -F '/usr/bin/env -i HOME=' "${DEPLOY_DIR}/manage.sh" >/dev/null \
    && grep -F 'PATH=/usr/bin:/bin' "${DEPLOY_DIR}/manage.sh" >/dev/null \
    && grep -F '/usr/bin/git --no-replace-objects' "${DEPLOY_DIR}/manage.sh" >/dev/null \
    && grep -F "GIT_SSH_COMMAND='/usr/bin/ssh " "${DEPLOY_DIR}/manage.sh" >/dev/null \
    && grep -F 'raw_origin_count' "${DEPLOY_DIR}/manage.sh" >/dev/null \
    && grep -F 'remote.origin.pushurl' "${DEPLOY_DIR}/manage.sh" >/dev/null \
    || fail 'Interactive Agent 生命周期入口未锁定 SSH-only Git 环境'
if grep -F 'https://github.com/xinghuahewo/domeye_.git' \
    "${DEPLOY_DIR}/manage.sh" >/dev/null; then
    fail 'Interactive Agent 生命周期入口仍接受 GitHub HTTPS'
fi

# historical policy 是唯一允许保留的旧审计输入；其余发布路径不得出现旧入口。
readonly POLICY='deploy/country-outage-agent/p1-chat/certification-impact-policy.json'
[[ "$(sha256sum "${REPOSITORY}/${POLICY}" | awk '{print $1}')" \
    == 'ba0756b7886c562f9fc522e927a5d4914178b456ec9a863eb780e5cdc57b9f64' \
    && "$(git -C "${REPOSITORY}" show "HEAD:${POLICY}" | sha256sum | awk '{print $1}')" \
        == 'ba0756b7886c562f9fc522e927a5d4914178b456ec9a863eb780e5cdc57b9f64' ]] \
    || fail '历史 certification impact policy 字节发生变化'
readonly LEGACY_PATTERN='serve-''formal-''p1|OP-''04|CAP-TREND-''001|COUNTRY_OUTAGE_P1_CHAT_''SIDECAR_URL|/re''bind'
if rg -n "${LEGACY_PATTERN}" \
    "${DEPLOY_DIR}" \
    --glob '!certification-impact-policy.json' >/dev/null; then
    fail '新 Interactive Agent 发布目录仍含旧入口或 P2 promotion 语义'
fi

printf 'Interactive Agent release v2 fail-closed fixtures passed; signed 30/30 positive covered by Evaluator tests\n'
