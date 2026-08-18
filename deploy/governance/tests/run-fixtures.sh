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

for command_name in git grep install jq mktemp; do
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

for required_text in \
    'refs/heads/main' \
    'source.archive_sha256' \
    'BACKEND-SOURCE-BINDING.json' \
    'FRONTEND-MANIFEST.json' \
    'RELEASE-MANIFEST.json' \
    'database.changed == false'; do
    grep -F "${required_text}" "${SCRIPT_ROOT}/check-release-normalization.sh" >/dev/null \
        || fail "归一检查缺少固定合同：${required_text}"
done

printf '治理夹具通过：主干审批、快进保护、正式 tag 不可变和归一合同均符合预期。\n'
