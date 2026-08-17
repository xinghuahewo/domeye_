#!/usr/bin/env bash

set -Eeuo pipefail

readonly RUNTIME_ROOT='/home/bgpdata/Domeye-Core-runtime'
readonly REPOSITORY='/home/bgpdata/Domeye-Core'

die() {
    printf '发布归一检查失败：%s\n' "$*" >&2
    exit 1
}

if (( $# != 1 )); then
    die '用法：check-release-normalization.sh <release-id>'
fi
readonly RELEASE_ID="$1"
[[ "${RELEASE_ID}" =~ ^[0-9]{8}T[0-9]{6}Z-[a-z0-9._-]+$ ]] \
    || die 'release-id 格式无效'

for command_name in awk cmp curl date find git grep jq pgrep readlink screen \
    sha256sum sort tr wc; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || die "缺少命令：${command_name}"
done

readonly UNIFIED="${RUNTIME_ROOT}/unified-releases/${RELEASE_ID}"
readonly CANDIDATE="${UNIFIED}/CANDIDATE-MANIFEST.json"
readonly DEPLOYMENT="${UNIFIED}/DEPLOYMENT.json"
readonly VERIFICATION="${UNIFIED}/VERIFICATION.json"
readonly EQUATION="${UNIFIED}/IDENTITY-EQUATION.json"
for evidence in "${CANDIDATE}" "${DEPLOYMENT}" "${VERIFICATION}" "${EQUATION}"; do
    [[ -f "${evidence}" && ! -L "${evidence}" ]] \
        || die "缺少实际普通证据文件：${evidence}"
done

readonly COMMIT="$(jq -er '.source.commit' "${CANDIDATE}")"
readonly TAG="$(jq -er '.source.annotated_tag' "${CANDIDATE}")"
readonly SOURCE_ARCHIVE="$(jq -er '.source.archive_path' "${CANDIDATE}")"
readonly SOURCE_ARCHIVE_SHA="$(jq -er '.source.archive_sha256' "${CANDIDATE}")"
readonly BACKEND_RELEASE="$(jq -er '.components.backend.release_id' "${CANDIDATE}")"
readonly BACKEND_PATH="$(jq -er '.components.backend.path' "${CANDIDATE}")"
readonly FRONTEND_RELEASE="$(jq -er '.components.frontend.release_id' "${CANDIDATE}")"
readonly FRONTEND_PATH="$(jq -er '.components.frontend.path' "${CANDIDATE}")"
readonly SIDECAR_RELEASE="$(jq -er '.components.sidecar.release_id' "${CANDIDATE}")"
readonly SIDECAR_PATH="$(jq -er '.components.sidecar.path' "${CANDIDATE}")"
readonly NGINX_SHA="$(jq -er '.frozen_runtime.nginx_config_sha256' "${CANDIDATE}")"
readonly DATABASE_STATE_SHA="$(jq -er '.frozen_runtime.database_state_sha256' "${CANDIDATE}")"

jq -e --arg release_id "${RELEASE_ID}" \
    '.schema_version == "domeye_unified_release_candidate_v1"
     and .release_id == $release_id
     and .status == "built"
     and .frozen_runtime.database_changed == false
     and .promotion_contract.candidate_canary_production_same_artifacts == true
     and .promotion_contract.rebuild_allowed == false' \
    "${CANDIDATE}" >/dev/null \
    || die '统一候选合同无效'
jq -e --arg release_id "${RELEASE_ID}" \
    --arg backend_release "${BACKEND_RELEASE}" \
    --arg backend_path "${BACKEND_PATH}" \
    --arg frontend_release "${FRONTEND_RELEASE}" \
    --arg sidecar_release "${SIDECAR_RELEASE}" \
    --arg sidecar_path "${SIDECAR_PATH}" \
    --arg nginx_sha "${NGINX_SHA}" \
    --arg database_state_sha "${DATABASE_STATE_SHA}" \
    '.schema_version == "domeye_unified_release_deployment_v1"
     and .release_id == $release_id
     and .status == "deployed"
     and .artifacts_rebuilt_during_promotion == false
     and .components.backend.release_id == $backend_release
     and .components.backend.current_path == $backend_path
     and .components.frontend.release_id == $frontend_release
     and .components.sidecar.release_id == $sidecar_release
     and .components.sidecar.current_path == $sidecar_path
     and .nginx.config_sha256 == $nginx_sha
     and .database.changed == false
     and .database.state_sha256 == $database_state_sha' \
    "${DEPLOYMENT}" >/dev/null \
    || die '部署证据与统一候选不一致'
jq -e --arg release_id "${RELEASE_ID}" --arg commit "${COMMIT}" \
    --arg tag "${TAG}" --arg archive_sha "${SOURCE_ARCHIVE_SHA}" \
    '.schema_version == "domeye_unified_release_verification_v1"
     and .release_id == $release_id
     and .status == "verified"
     and .source_identity.commit == $commit
     and .source_identity.annotated_tag == $tag
     and .source_identity.source_archive_sha256 == $archive_sha
     and .boundaries.database_changed == false
     and .budget.within_cap == true' \
    "${VERIFICATION}" >/dev/null \
    || die '生产验证证据与统一候选不一致'

[[ "$(git -C "${REPOSITORY}" rev-parse refs/heads/main)" == "${COMMIT}" ]] \
    || die 'main 与候选 commit 不一致'
[[ "$(git -C "${REPOSITORY}" cat-file -t "${TAG}")" == 'tag' ]] \
    || die '发布 tag 不是 annotated tag'
[[ "$(git -C "${REPOSITORY}" rev-parse "${TAG}^{}")" == "${COMMIT}" ]] \
    || die '发布 tag 解引用后与候选 commit 不一致'
[[ "$(sha256sum "${SOURCE_ARCHIVE}" | awk '{print $1}')" == "${SOURCE_ARCHIVE_SHA}" ]] \
    || die '源码归档 SHA-256 不一致'

[[ "$(jq -er '.source_commit' "${BACKEND_PATH}/BACKEND-SOURCE-BINDING.json")" == "${COMMIT}" ]] \
    || die '后端源码绑定不一致'
[[ "$(jq -er '.source.commit' "${FRONTEND_PATH}/FRONTEND-MANIFEST.json")" == "${COMMIT}" ]] \
    || die '前端源码绑定不一致'
[[ "$(jq -er '.git_sha' "${SIDECAR_PATH}/RELEASE-MANIFEST.json")" == "${COMMIT}" ]] \
    || die 'Sidecar 源码绑定不一致'

[[ "$(readlink -f "${RUNTIME_ROOT}/current")" == "${BACKEND_PATH}" ]] \
    || die '后端 current 未指向候选制品'
backend_session="$(screen -ls | awk '$1 ~ /\.domeye_core_app$/ {print $1}')"
[[ -n "${backend_session}" ]] || die '后端 Screen 不存在'
backend_pid="$(pgrep -P "${backend_session%%.*}" -f 'python.*run.py' | head -n 1)"
[[ -n "${backend_pid}" ]] || die '后端实际进程不存在'
[[ "$(readlink -f "/proc/${backend_pid}/cwd")" == "${BACKEND_PATH}/backend" ]] \
    || die '后端实际进程 cwd 与 release 不一致'
tr '\0' '\n' <"/proc/${backend_pid}/environ" \
    | grep -Fx "DOMEYE_P0_PRODUCTION_RELEASE_ID=${BACKEND_RELEASE}" >/dev/null \
    || die '后端实际进程 release ID 不一致'

[[ "$(readlink -f "${RUNTIME_ROOT}/country-outage-agent/current")" == "${SIDECAR_PATH}" ]] \
    || die 'Sidecar current 未指向候选制品'
[[ "$(jq -er '.release_id' "${RUNTIME_ROOT}/country-outage-agent/state/active.json")" == "${SIDECAR_RELEASE}" ]] \
    || die 'Sidecar active state 与候选不一致'
"${SIDECAR_PATH}/deployment/status.sh" >/dev/null \
    || die 'Sidecar 实际进程状态检查失败'

[[ "$(<"${RUNTIME_ROOT}/web/state/frontend-current")" == "${FRONTEND_RELEASE}" ]] \
    || die '前端 current state 与候选不一致'
while IFS= read -r -d '' relative_path; do
    cmp -s "${FRONTEND_PATH}/dist/${relative_path}" \
        "${RUNTIME_ROOT}/web/dist/${relative_path}" \
        || die "前端实际文件与候选不一致：${relative_path}"
done < <(cd "${FRONTEND_PATH}/dist" && find . -type f -print0 \
    | sort -z | while IFS= read -r -d '' path; do printf '%s\0' "${path#./}"; done)
[[ "$(find "${FRONTEND_PATH}/dist" -type f | wc -l)" \
    == "$(find "${RUNTIME_ROOT}/web/dist" -type f | wc -l)" ]] \
    || die '前端实际文件数量与候选不一致'

[[ "$(sha256sum /etc/nginx/conf.d/domeye-core.conf | awk '{print $1}')" == "${NGINX_SHA}" ]] \
    || die 'Nginx 配置摘要不一致'
curl -fsS --max-time 5 http://127.0.0.1:28471/api/v1/healthz \
    | jq -e '.status == "ok"' >/dev/null \
    || die '生产健康检查失败'
jq -e '.status == "verified" and .budget.within_cap == true' \
    "${VERIFICATION}" >/dev/null \
    || die '生产验证或费用门禁未通过'
jq -e --arg commit "${COMMIT}" --arg backend_release "${BACKEND_RELEASE}" \
    --arg frontend_release "${FRONTEND_RELEASE}" --arg sidecar_release "${SIDECAR_RELEASE}" \
    '.status == "equal"
     and .equation.remote_production_branch.commit == $commit
     and .equation.annotated_tag.target == $commit
     and .equation.source_archive.commit == $commit
     and .equation.component_manifests.backend_source_commit == $commit
     and .equation.component_manifests.frontend_source_commit == $commit
     and .equation.component_manifests.sidecar_source_commit == $commit
     and .equation.actual_runtime.backend.release_id == $backend_release
     and .equation.actual_runtime.frontend.release_id == $frontend_release
     and .equation.actual_runtime.sidecar.release_id == $sidecar_release
     and .equation.database.changed == false' \
    "${EQUATION}" >/dev/null \
    || die '发布身份等式证据未闭合'

governance_scripts_checked=false
if jq -e '.governance != null' "${CANDIDATE}" >/dev/null; then
    readonly EXPECTED_HOOK_SHA="$(jq -er '.governance.pre_receive.sha256' "${CANDIDATE}")"
    readonly EXPECTED_GATE_SHA="$(jq -er '.governance.normalization_gate.sha256' "${CANDIDATE}")"
    readonly INSTALL_RECEIPT="$(jq -er '.governance.installation_receipt' "${CANDIDATE}")"
    [[ "$(sha256sum "${REPOSITORY}/.git/hooks/pre-receive" | awk '{print $1}')" \
        == "${EXPECTED_HOOK_SHA}" ]] \
        || die '服务器 Hook 与统一候选版本不一致'
    [[ "$(sha256sum "$0" | awk '{print $1}')" == "${EXPECTED_GATE_SHA}" ]] \
        || die '服务器归一检查与统一候选版本不一致'
    jq -e --arg release_id "${RELEASE_ID}" --arg hook_sha "${EXPECTED_HOOK_SHA}" \
        --arg gate_sha "${EXPECTED_GATE_SHA}" \
        '.schema_version == "domeye_governance_installation_v1"
         and .release_id == $release_id
         and .status == "installed"
         and .hook.sha256 == $hook_sha
         and .normalization_gate.sha256 == $gate_sha' \
        "${INSTALL_RECEIPT}" >/dev/null \
        || die '治理脚本安装回执与统一候选不一致'
    governance_scripts_checked=true
fi

jq -n --arg release_id "${RELEASE_ID}" --arg commit "${COMMIT}" \
    --arg tag "${TAG}" --arg checked_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --argjson governance_scripts "${governance_scripts_checked}" \
    '{schema_version:"domeye_release_normalization_gate_v1",status:"passed",release_id:$release_id,commit:$commit,annotated_tag:$tag,checked_at:$checked_at,checks:{git:true,source_archive:true,backend_manifest_and_process:true,sidecar_manifest_and_process:true,frontend_candidate_and_nginx_bytes:true,nginx_config:true,database_unchanged:true,production_verification:true,identity_equation:true,governance_scripts:$governance_scripts}}'
