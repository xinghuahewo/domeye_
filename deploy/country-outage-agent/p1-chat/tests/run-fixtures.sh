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
readonly RELEASE_ID='20260819T120000Z-country-outage-interactive-agent-fixture'
readonly FIXED_QUESTION='在这次冻结 publication 的观测窗口内，RRC25 看到的固定前缀可见 IPv4 地址量最低是多少，首次在什么观测时刻出现？首值、末值、最大值和极差分别是多少？'
readonly CONVERSATION_ID="conversation_sha256_$(printf 'a%.0s' {1..64})"
readonly TURN_ID="turn_sha256_$(printf 'b%.0s' {1..64})"
readonly STALE_CONVERSATION_ID="conversation_sha256_$(printf 'c%.0s' {1..64})"
readonly STALE_TURN_ID="turn_sha256_$(printf 'd%.0s' {1..64})"
readonly RUNTIME_ROOT="${FIXTURE_ROOT}/runtime/country-outage-interactive-agent"
readonly STATE_ROOT="${RUNTIME_ROOT}/state"
readonly CONFIG="${FIXTURE_ROOT}/runtime/config/country-outage-interactive-agent.env"
readonly REAL_RELEASE_ROOT="${RUNTIME_ROOT}/releases/${RELEASE_ID}"
readonly REAL_PROJECT="${REAL_RELEASE_ROOT}/project"
readonly BINDING_RELEASE_ID='20260819T120001Z-country-outage-interactive-agent-binding'
readonly BINDING_RELEASE_ROOT="${RUNTIME_ROOT}/releases/${BINDING_RELEASE_ID}"
readonly WRONG_RELEASE_ROOT="${RUNTIME_ROOT}/releases/20260819T120002Z-country-outage-interactive-agent-wrong"
readonly HISTORICAL_ACCEPTED_COMMIT='cb8e30855fba04c54d3ad3bb3dca573ac6fe3d17'
readonly HISTORICAL_ACCEPTANCE_RELATIVE='evaluation/country-outage/first-vertical-slice/runs/formal-20260819T1839/acceptance-record-final.json'

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
COUNTRY_OUTAGE_AGENT_HOST=127.0.0.1
COUNTRY_OUTAGE_AGENT_PORT=28476
DOMEYE_API_BASE_URL=http://127.0.0.1:28473/api/v2/
COUNTRY_OUTAGE_FIRST_SLICE_PROJECT_ROOT=${RUNTIME_ROOT}/current/project
COUNTRY_OUTAGE_FIRST_SLICE_CANDIDATE_MANIFEST=${RUNTIME_ROOT}/current/project/contracts/agent/domeye-first-vertical-slice/v1/candidate.json
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
    'contracts/agent/domeye-first-vertical-slice/v1/candidate.json')" \
    "${WRONG_RELEASE_ROOT}" "${FIXTURE_ROOT}/escaped-release"
printf '{}\n' > "${BINDING_RELEASE_ROOT}/project/contracts/agent/domeye-first-vertical-slice/v1/candidate.json"
ln -s "${BINDING_RELEASE_ROOT}" "${RUNTIME_ROOT}/current"
manager _test_launch_environment "${BINDING_RELEASE_ID}" \
    > "${FIXTURE_ROOT}/launch-environment.out" \
    || fail '首发 current symlink 未能绑定到真实 release'
cat > "${FIXTURE_ROOT}/launch-environment.expected" <<EOF
COUNTRY_OUTAGE_FIRST_SLICE_PROJECT_ROOT=${BINDING_RELEASE_ROOT}/project
COUNTRY_OUTAGE_FIRST_SLICE_CANDIDATE_MANIFEST=${BINDING_RELEASE_ROOT}/project/contracts/agent/domeye-first-vertical-slice/v1/candidate.json
EOF
cmp -s "${FIXTURE_ROOT}/launch-environment.expected" \
    "${FIXTURE_ROOT}/launch-environment.out" \
    || fail '子进程 Project/Candidate 没有绑定同一真实 release 目录'
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

# 最终 GET 只能使用本次 create/POST 返回的会话和 Turn；旧正确会话不能晋级。
jq -n --arg conversation_id "${CONVERSATION_ID}" \
    --arg turn_id "${TURN_ID}" --arg question "${FIXED_QUESTION}" \
    '{conversation:{conversation_id:$conversation_id,turns:[{turn_id:$turn_id,question:$question}]}}' \
    > "${FIXTURE_ROOT}/promotion-binding-good.json"
verifier _test-promotion-binding \
    "${FIXTURE_ROOT}/promotion-binding-good.json" \
    "${CONVERSATION_ID}" "${TURN_ID}" "${FIXED_QUESTION}" \
    || fail '本次 conversation_id/turn_id 精确绑定未通过'
jq --arg stale "${STALE_CONVERSATION_ID}" \
    '.conversation.conversation_id=$stale' \
    "${FIXTURE_ROOT}/promotion-binding-good.json" \
    > "${FIXTURE_ROOT}/promotion-binding-stale-conversation.json"
assert_fails '旧正确 conversation 不得晋级' \
    verifier _test-promotion-binding \
    "${FIXTURE_ROOT}/promotion-binding-stale-conversation.json" \
    "${CONVERSATION_ID}" "${TURN_ID}" "${FIXED_QUESTION}"
jq --arg stale "${STALE_TURN_ID}" \
    '.conversation.turns[0].turn_id=$stale' \
    "${FIXTURE_ROOT}/promotion-binding-good.json" \
    > "${FIXTURE_ROOT}/promotion-binding-stale-turn.json"
assert_fails '旧正确 Turn 不得晋级' \
    verifier _test-promotion-binding \
    "${FIXTURE_ROOT}/promotion-binding-stale-turn.json" \
    "${CONVERSATION_ID}" "${TURN_ID}" "${FIXED_QUESTION}"

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
readonly CANDIDATE_RELATIVE='contracts/agent/domeye-first-vertical-slice/v1/candidate.json'
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

# 使用仓库中真实完整的旧 DG1 证据重放 finalizer；它能精确重建，但 28/30
# 不能满足更严格的发布门。这里不把旧证据改写成假 30/30。
mkdir -p "${REAL_PROJECT}" "${REAL_RELEASE_ROOT}/source" \
    "${REAL_RELEASE_ROOT}/deployment"
git -C "${REPOSITORY}" archive "${HISTORICAL_ACCEPTED_COMMIT}" \
    | tar -xf - -C "${REAL_PROJECT}"
if [[ -d "${REAL_PROJECT}/agent-sidecar/node_modules" ]]; then
    find "${REAL_PROJECT}/agent-sidecar/node_modules" -depth -delete
fi
cp -al "${REPOSITORY}/agent-sidecar/node_modules" \
    "${REAL_PROJECT}/agent-sidecar/node_modules"
(
    cd -- "${REAL_PROJECT}/agent-sidecar"
    "$(command -v npm)" run build >/dev/null
)
"${NODE}" "${DEPLOY_DIR}/verify-release.mjs" acceptance-replay \
    "${REAL_PROJECT}" "${HISTORICAL_ACCEPTANCE_RELATIVE}" \
    > "${REAL_RELEASE_ROOT}/deployment/ACCEPTANCE-REPLAY.json" \
    || fail '真实 28/30 Acceptance 未能由正式 finalizer 精确重放'
jq -e '
  .schema_version=="domeye_interactive_agent_acceptance_replay_v1" and
  .candidate_source_files_verified==true and .record_exact_match==true
' "${REAL_RELEASE_ROOT}/deployment/ACCEPTANCE-REPLAY.json" >/dev/null \
    || fail 'Acceptance replay receipt 语义无效'

# 成功公开回答的 provider 闭包固定为三次 cognition + 末次唯一 Renderer；
# 四次都必须绑定 Candidate 模型身份、audit_only、顺序时间与 completed 终态。
readonly REAL_EVIDENCE="${REAL_PROJECT}/$(dirname "${HISTORICAL_ACCEPTANCE_RELATIVE}")/evidence.jsonl"
readonly REAL_CANDIDATE="${REAL_PROJECT}/contracts/agent/domeye-first-vertical-slice/v1/candidate.json"
jq -s '
  first(.[] | select(.record_type=="j1_trial" and .payload.passed==true)) |
  {usage:.payload.evidence.usage}
' "${REAL_EVIDENCE}" > "${FIXTURE_ROOT}/provider-usage-good.json"
verifier _test-provider-usage \
    "${FIXTURE_ROOT}/provider-usage-good.json" "${REAL_CANDIDATE}" \
    || fail '真实成功 Answer 的四次 provider usage 未通过'
jq '.usage.attempts[1].provider="untrusted-provider"' \
    "${FIXTURE_ROOT}/provider-usage-good.json" \
    > "${FIXTURE_ROOT}/provider-usage-provider-drift.json"
assert_fails '任一 attempt provider 漂移' verifier _test-provider-usage \
    "${FIXTURE_ROOT}/provider-usage-provider-drift.json" "${REAL_CANDIDATE}"
jq '.usage.attempts[2].response_model="wrong-response-model"' \
    "${FIXTURE_ROOT}/provider-usage-good.json" \
    > "${FIXTURE_ROOT}/provider-usage-response-model-drift.json"
assert_fails '任一 attempt response_model 漂移' verifier _test-provider-usage \
    "${FIXTURE_ROOT}/provider-usage-response-model-drift.json" \
    "${REAL_CANDIDATE}"
jq '.usage.attempts[2].phase="renderer"' \
    "${FIXTURE_ROOT}/provider-usage-good.json" \
    > "${FIXTURE_ROOT}/provider-usage-renderer-not-last.json"
assert_fails 'Renderer 不是唯一末次 attempt' verifier _test-provider-usage \
    "${FIXTURE_ROOT}/provider-usage-renderer-not-last.json" "${REAL_CANDIDATE}"
jq '.usage.attempt_count=5 | .usage.attempts += [(.usage.attempts[-1] | .attempt_id=5)]' \
    "${FIXTURE_ROOT}/provider-usage-good.json" \
    > "${FIXTURE_ROOT}/provider-usage-extra-attempt.json"
assert_fails '成功闭包夹带第五次调用' verifier _test-provider-usage \
    "${FIXTURE_ROOT}/provider-usage-extra-attempt.json" "${REAL_CANDIDATE}"
jq '.usage.attempts[3].outcome="failed" | .usage.attempts[3].failure_code="provider_failure"' \
    "${FIXTURE_ROOT}/provider-usage-good.json" \
    > "${FIXTURE_ROOT}/provider-usage-failed-terminal.json"
assert_fails 'provider failure 不得晋级' verifier _test-provider-usage \
    "${FIXTURE_ROOT}/provider-usage-failed-terminal.json" "${REAL_CANDIDATE}"
jq '.usage.attempts[2].started_at_utc=.usage.attempts[0].started_at_utc' \
    "${FIXTURE_ROOT}/provider-usage-good.json" \
    > "${FIXTURE_ROOT}/provider-usage-time-order.json"
assert_fails 'attempt 时间顺序漂移' verifier _test-provider-usage \
    "${FIXTURE_ROOT}/provider-usage-time-order.json" "${REAL_CANDIDATE}"
git -C "${REPOSITORY}" archive --format=tar.gz \
    --output="${REAL_RELEASE_ROOT}/source/source.tar.gz" \
    "${HISTORICAL_ACCEPTED_COMMIT}"
cp "${DEPLOY_DIR}/verify-release.mjs" "${DEPLOY_DIR}/probe.mjs" \
    "${REAL_RELEASE_ROOT}/deployment/"

"${NODE}" --input-type=module - \
    "${REAL_RELEASE_ROOT}" "${RELEASE_ID}" \
    "${HISTORICAL_ACCEPTED_COMMIT}" "${HISTORICAL_ACCEPTANCE_RELATIVE}" <<'EOF'
import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const [root, releaseId, commit, acceptanceRelative] = process.argv.slice(2)
const candidateRelative =
  'contracts/agent/domeye-first-vertical-slice/v1/candidate.json'
const candidatePath = join(root, 'project', candidateRelative)
const acceptancePath = join(root, 'project', acceptanceRelative)
const replayPath = join(root, 'deployment/ACCEPTANCE-REPLAY.json')
const sourcePath = join(root, 'source/source.tar.gz')
const candidate = JSON.parse(readFileSync(candidatePath, 'utf8'))
const acceptance = JSON.parse(readFileSync(acceptancePath, 'utf8'))
const sha = (path) =>
  `sha256:${createHash('sha256').update(readFileSync(path)).digest('hex')}`
const canonical = (value) => {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  return `{${Object.keys(value).sort().map((key) =>
    `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
}
const digest = (value) =>
  `sha256:${createHash('sha256').update(canonical(value)).digest('hex')}`
const oracle = {
  metric: 'fixed_visible_ipv4_address_count',
  unit: 'unique_ipv4_address',
  time_slot_count: 3455,
  observed_point_count: 3455,
  null_point_count: 0,
  first: 10156800,
  first_at_utc: '2026-02-27T00:10:00Z',
  last: 10069760,
  last_at_utc: '2026-03-11T00:00:00Z',
  minimum: 9577728,
  minimum_at_utc: '2026-02-28T14:35:00Z',
  maximum: 10156800,
  maximum_at_utc: '2026-02-27T00:10:00Z',
  difference: 579072,
  net_change: -87040,
}
const manifest = {
  schema_version: 'domeye_interactive_agent_release_manifest_v1',
  component: 'domeye_interactive_agent_sidecar',
  release_id: releaseId,
  created_at_utc: '2026-08-19T12:00:00Z',
  source: {
    commit,
    annotated_tag: releaseId,
    archive_path: 'source/source.tar.gz',
    archive_sha256: sha(sourcePath),
  },
  candidate: {
    manifest_path: `project/${candidateRelative}`,
    candidate_id: candidate.candidate_id,
    manifest_sha256: sha(candidatePath),
    manifest_payload_digest: candidate.candidate_id.slice('manifest:'.length),
    activation_scope: 'local_evaluation_only',
    production_deployed: false,
  },
  acceptance: {
    record_path: `project/${acceptanceRelative}`,
    record_id: acceptance.acceptance_record_id,
    record_sha256: sha(acceptancePath),
    replay_receipt_path: 'deployment/ACCEPTANCE-REPLAY.json',
    replay_receipt_sha256: sha(replayPath),
  },
  runtime: {
    entrypoint: 'agent-sidecar/dist/src/cli/serve-interactive-agent.js',
    host: '127.0.0.1',
    port: 28476,
    base_path: '/country-outage/chat',
    activation_scope: 'local_evaluation_only',
    candidate_production_deployed: false,
  },
  live_verification: {
    public_backend_origin: 'http://127.0.0.1:28471',
    backend_base_path: '/api/v2/country-outage/chat',
    event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
    question: '在这次冻结 publication 的观测窗口内，RRC25 看到的固定前缀可见 IPv4 地址量最低是多少，首次在什么观测时刻出现？首值、末值、最大值和极差分别是多少？',
    oracle,
    oracle_digest: digest(oracle),
  },
  rollback: { mode: 'fail_closed', previous_release_id: null },
}
writeFileSync(
  join(root, 'RELEASE-MANIFEST.json'),
  `${JSON.stringify(manifest, null, 2)}\n`,
)
EOF

assert_fails '真实 DG1 GO 的 28/30 不能通过发布门' \
    "${NODE}" "${DEPLOY_DIR}/verify-release.mjs" "${REAL_RELEASE_ROOT}"
grep -F '30/30' "${FIXTURE_ROOT}/expected-failure.err" >/dev/null \
    || fail '28/30 拒绝没有落在发布级 Renderer 门'

# 真实 Candidate loader 必须逐 source_files 拒绝任一字节漂移。
readonly SOURCE_DRIFT_FILE="${REAL_PROJECT}/agent-sidecar/dist/src/agent/pi-answer-renderer.js"
cp "${SOURCE_DRIFT_FILE}" "${FIXTURE_ROOT}/source-file.saved"
printf '\n// fixture drift\n' >> "${SOURCE_DRIFT_FILE}"
assert_fails 'Candidate source_files 字节漂移' \
    "${NODE}" "${DEPLOY_DIR}/verify-release.mjs" acceptance-replay \
    "${REAL_PROJECT}" "${HISTORICAL_ACCEPTANCE_RELATIVE}"
cp "${FIXTURE_ROOT}/source-file.saved" "${SOURCE_DRIFT_FILE}"

# 使用真实 Renderer draft 验正式 Guard：原回答 pass，占位错误文本 block。
"${NODE}" --input-type=module - \
    "${REAL_PROJECT}" "${HISTORICAL_ACCEPTANCE_RELATIVE}" <<'EOF'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { pathToFileURL } from 'node:url'

const [project, acceptanceRelative] = process.argv.slice(2)
const findingModule = await import(pathToFileURL(join(
  project,
  'agent-sidecar/dist/src/agent/finding-answer.js',
)).href)
const candidate = JSON.parse(readFileSync(join(
  project,
  'contracts/agent/domeye-first-vertical-slice/v1/candidate.json',
), 'utf8'))
const evidencePath = join(project, dirname(acceptanceRelative), 'evidence.jsonl')
const trial = readFileSync(evidencePath, 'utf8').trimEnd().split('\n')
  .map(JSON.parse)
  .find((item) => item.record_type === 'j1_trial' && item.payload.passed)
const closure = trial.payload.evidence.replay_closure
const context = findingModule.buildCountryOutageAnswerContext(
  closure.finding,
  candidate.payload.contract.digest,
)
const pass = findingModule.guardCountryOutageResponse(
  context,
  closure.renderer_draft,
)
if (pass.decision !== 'pass' || pass.reason_codes.length !== 0) process.exit(1)
const placeholder = structuredClone(closure.renderer_draft)
placeholder.text = '固定问题已正确回答。'
const blocked = findingModule.guardCountryOutageResponse(context, placeholder)
if (blocked.decision !== 'block' || blocked.reason_codes.length === 0) process.exit(1)
EOF

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

printf 'Interactive Agent release fail-closed fixtures passed; real 30/30 positive pending\n'
