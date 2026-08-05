#!/usr/bin/env bash

set -Eeuo pipefail

die() {
    printf '治理脚本安装失败：%s\n' "$*" >&2
    exit 1
}

if (( $# < 1 || $# > 3 )); then
    die '用法：install.sh <release-id> [repository] [governance-root]'
fi

readonly RELEASE_ID="$1"
readonly REPOSITORY="${2:-/home/bgpdata/Domeye-Core}"
readonly GOVERNANCE_ROOT="${3:-/home/bgpdata/Domeye-Core-governance}"
readonly SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

[[ "${RELEASE_ID}" =~ ^[0-9]{8}T[0-9]{6}Z-[a-z0-9._-]+$ ]] \
    || die 'release-id 格式无效'
[[ "$(id -u)" == '0' ]] || die '只允许 root 安装服务器治理脚本'
[[ -d "${REPOSITORY}" && ! -L "${REPOSITORY}" ]] \
    || die "Git 仓库必须是实际目录：${REPOSITORY}"
[[ "${GOVERNANCE_ROOT}" == /* ]] || die 'governance-root 必须是绝对路径'

for command_name in awk date git id install jq mktemp mv readlink rm sha256sum; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || die "缺少命令：${command_name}"
done

readonly GIT_DIR="$(git -C "${REPOSITORY}" rev-parse --absolute-git-dir)"
readonly HOOK_SOURCE="${SOURCE_ROOT}/pre-receive"
readonly GATE_SOURCE="${SOURCE_ROOT}/check-release-normalization.sh"
readonly HOOK_TARGET="${GIT_DIR}/hooks/pre-receive"
readonly GATE_TARGET="${GOVERNANCE_ROOT}/bin/check-release-normalization.sh"
readonly APPROVAL_ROOT="${GOVERNANCE_ROOT}/approvals"
readonly BACKUP_ROOT="${GOVERNANCE_ROOT}/backups/${RELEASE_ID}"
readonly RECEIPT="${GOVERNANCE_ROOT}/installations/${RELEASE_ID}.json"

[[ -f "${HOOK_SOURCE}" && ! -L "${HOOK_SOURCE}" ]] \
    || die "缺少版本化 Hook：${HOOK_SOURCE}"
[[ -f "${GATE_SOURCE}" && ! -L "${GATE_SOURCE}" ]] \
    || die "缺少版本化归一检查：${GATE_SOURCE}"

readonly HOOK_SOURCE_SHA="$(sha256sum "${HOOK_SOURCE}" | awk '{print $1}')"
readonly GATE_SOURCE_SHA="$(sha256sum "${GATE_SOURCE}" | awk '{print $1}')"

if [[ -e "${RECEIPT}" ]]; then
    [[ -f "${RECEIPT}" && ! -L "${RECEIPT}" ]] \
        || die "安装回执不是普通文件：${RECEIPT}"
    [[ "$(sha256sum "${HOOK_TARGET}" | awk '{print $1}')" == "${HOOK_SOURCE_SHA}" ]] \
        || die '同 release-id 已有回执，但服务器 Hook 与版本化来源不一致'
    [[ "$(sha256sum "${GATE_TARGET}" | awk '{print $1}')" == "${GATE_SOURCE_SHA}" ]] \
        || die '同 release-id 已有回执，但服务器归一检查与版本化来源不一致'
    jq -e --arg release_id "${RELEASE_ID}" \
        '.schema_version == "domeye_governance_installation_v1"
         and .release_id == $release_id
         and .status == "installed"' "${RECEIPT}" >/dev/null \
        || die '既有安装回执内容无效'
    printf '治理脚本已按同一 release-id 安装，无需重复覆盖：%s\n' "${RECEIPT}"
    exit 0
fi

umask 077
install -d -m 0755 "${GIT_DIR}/hooks" "${GOVERNANCE_ROOT}/bin" \
    "${GOVERNANCE_ROOT}/backups" "${GOVERNANCE_ROOT}/installations"
install -d -m 0700 "${APPROVAL_ROOT}" "${BACKUP_ROOT}"

old_hook_present=false
old_gate_present=false
old_hook_sha='absent'
old_gate_sha='absent'
if [[ -e "${HOOK_TARGET}" ]]; then
    [[ -f "${HOOK_TARGET}" && ! -L "${HOOK_TARGET}" ]] \
        || die "旧 Hook 不是普通文件：${HOOK_TARGET}"
    old_hook_present=true
    old_hook_sha="$(sha256sum "${HOOK_TARGET}" | awk '{print $1}')"
    install -m 0755 "${HOOK_TARGET}" "${BACKUP_ROOT}/pre-receive"
fi
if [[ -e "${GATE_TARGET}" ]]; then
    [[ -f "${GATE_TARGET}" && ! -L "${GATE_TARGET}" ]] \
        || die "旧归一检查不是普通文件：${GATE_TARGET}"
    old_gate_present=true
    old_gate_sha="$(sha256sum "${GATE_TARGET}" | awk '{print $1}')"
    install -m 0755 "${GATE_TARGET}" \
        "${BACKUP_ROOT}/check-release-normalization.sh"
fi

hook_temp="$(mktemp "${HOOK_TARGET}.new.XXXXXX")"
gate_temp="$(mktemp "${GATE_TARGET}.new.XXXXXX")"
receipt_temp="$(mktemp "${GOVERNANCE_ROOT}/installations/.${RELEASE_ID}.XXXXXX")"
hook_installed=false
gate_installed=false

cleanup() {
    rm -f -- "${hook_temp:-}" "${gate_temp:-}" "${receipt_temp:-}"
}

rollback_on_error() {
    local rc=$?
    set +e
    if [[ "${hook_installed}" == true ]]; then
        if [[ "${old_hook_present}" == true ]]; then
            install -m 0755 "${BACKUP_ROOT}/pre-receive" "${HOOK_TARGET}"
        else
            rm -f -- "${HOOK_TARGET}"
        fi
    fi
    if [[ "${gate_installed}" == true ]]; then
        if [[ "${old_gate_present}" == true ]]; then
            install -m 0755 "${BACKUP_ROOT}/check-release-normalization.sh" \
                "${GATE_TARGET}"
        else
            rm -f -- "${GATE_TARGET}"
        fi
    fi
    cleanup
    printf '治理脚本安装失败，已尝试恢复安装前版本。\n' >&2
    exit "${rc}"
}

trap cleanup EXIT
trap rollback_on_error ERR

install -m 0755 "${HOOK_SOURCE}" "${hook_temp}"
install -m 0755 "${GATE_SOURCE}" "${gate_temp}"
[[ "$(sha256sum "${hook_temp}" | awk '{print $1}')" == "${HOOK_SOURCE_SHA}" ]]
[[ "$(sha256sum "${gate_temp}" | awk '{print $1}')" == "${GATE_SOURCE_SHA}" ]]

mv -f -- "${gate_temp}" "${GATE_TARGET}"
gate_installed=true
mv -f -- "${hook_temp}" "${HOOK_TARGET}"
hook_installed=true

jq -n \
    --arg release_id "${RELEASE_ID}" \
    --arg installed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg repository "${REPOSITORY}" \
    --arg hook_target "${HOOK_TARGET}" \
    --arg hook_sha256 "${HOOK_SOURCE_SHA}" \
    --arg previous_hook_sha256 "${old_hook_sha}" \
    --arg gate_target "${GATE_TARGET}" \
    --arg gate_sha256 "${GATE_SOURCE_SHA}" \
    --arg previous_gate_sha256 "${old_gate_sha}" \
    --arg backup_root "${BACKUP_ROOT}" \
    '{schema_version:"domeye_governance_installation_v1",status:"installed",release_id:$release_id,installed_at:$installed_at,repository:$repository,hook:{target:$hook_target,sha256:$hook_sha256,previous_sha256:$previous_hook_sha256},normalization_gate:{target:$gate_target,sha256:$gate_sha256,previous_sha256:$previous_gate_sha256},backup_root:$backup_root}' \
    >"${receipt_temp}"
install -m 0644 "${receipt_temp}" "${RECEIPT}"

trap - ERR
printf '治理脚本安装完成：%s\n' "${RECEIPT}"
