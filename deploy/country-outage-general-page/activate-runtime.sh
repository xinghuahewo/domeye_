#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly RUNTIME_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
readonly BINDING="${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json"
readonly RELEASE_ID="$(jq -er '.release_id | sub("-backend$"; "")' "${BINDING}")"
readonly UNIFIED_ROOT="/home/bgpdata/Domeye-Core-runtime/unified-releases/${RELEASE_ID}"
readonly CANDIDATE="${UNIFIED_ROOT}/CANDIDATE-MANIFEST.json"
readonly STATE="${UNIFIED_ROOT}/ACTIVATION-STATE.json"
readonly DEPLOYMENT="${UNIFIED_ROOT}/DEPLOYMENT.json"
readonly CURRENT_LINK='/home/bgpdata/Domeye-Core-runtime/current'
readonly FRONTEND_CURRENT='/home/bgpdata/Domeye-Core-runtime/web/state/frontend-current'
readonly LOCK='/home/bgpdata/Domeye-Core-runtime/var/country-outage-general-release.lock'
readonly MANAGER="${RUNTIME_ROOT}/deploy/country-outage-general-page/manage-runtime.sh"
readonly FRONTEND_INSTALLER="${RUNTIME_ROOT}/deploy/artifacts/install-frontend-build.sh"
readonly FRONTEND_ROLLBACK="${RUNTIME_ROOT}/deploy/artifacts/rollback-frontend-build.sh"

error() {
    printf '国家中断通用观测生产激活错误：%s\n' "$*" >&2
}

atomic_state() {
    local phase="$1"
    local status="$2"
    local tmp="${UNIFIED_ROOT}/.ACTIVATION-STATE.tmp.$$"
    jq -n \
        --arg schema_version domeye_country_outage_general_activation_v1 \
        --arg release_id "${RELEASE_ID}" \
        --arg phase "${phase}" \
        --arg status "${status}" \
        --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg previous_backend_path "${PREVIOUS_BACKEND}" \
        --arg previous_backend_release_id "${PREVIOUS_BACKEND_RELEASE}" \
        --arg previous_frontend_release_id "${PREVIOUS_FRONTEND_RELEASE}" \
        --arg candidate_backend_path "${RUNTIME_ROOT}" \
        --arg candidate_backend_release_id "${CANDIDATE_BACKEND_RELEASE}" \
        --arg candidate_frontend_release_id "${CANDIDATE_FRONTEND_RELEASE}" \
        '{schema_version:$schema_version,release_id:$release_id,phase:$phase,status:$status,updated_at:$updated_at,previous:{backend_path:$previous_backend_path,backend_release_id:$previous_backend_release_id,frontend_release_id:$previous_frontend_release_id},candidate:{backend_path:$candidate_backend_path,backend_release_id:$candidate_backend_release_id,frontend_release_id:$candidate_frontend_release_id},rollback:{script:($candidate_backend_path + "/deploy/country-outage-general-page/rollback-runtime.sh")}}' \
        > "${tmp}"
    chmod 0600 "${tmp}"
    mv -T -- "${tmp}" "${STATE}"
}

set_current_link() {
    local target="$1"
    [[ "${target}" == /home/bgpdata/Domeye-Core-runtime/releases/*-backend \
        && -d "${target}" && ! -L "${target}" ]] || {
        error "Backend 指针目标无效：${target}"
        return 1
    }
    local temporary="/home/bgpdata/Domeye-Core-runtime/.current-${RELEASE_ID}.$$"
    [[ ! -e "${temporary}" && ! -L "${temporary}" ]] || return 1
    ln -s "${target}" "${temporary}"
    mv -Tf -- "${temporary}" "${CURRENT_LINK}"
}

readonly PREVIOUS_BACKEND="$(jq -er '.rollback.backend_path' "${CANDIDATE}")"
readonly PREVIOUS_BACKEND_RELEASE="$(jq -er '.rollback.backend_release_id' "${CANDIDATE}")"
readonly PREVIOUS_FRONTEND_RELEASE="$(jq -er '.rollback.frontend_release_id' "${CANDIDATE}")"
readonly CANDIDATE_BACKEND_RELEASE="$(jq -er '.components.backend.release_id' "${CANDIDATE}")"
readonly CANDIDATE_FRONTEND_RELEASE="$(jq -er '.components.frontend.release_id' "${CANDIDATE}")"
readonly CANDIDATE_FRONTEND_DIST="$(jq -er '.components.frontend.path + "/dist"' "${CANDIDATE}")"
readonly EXPECTED_DATABASE_SHA="$(jq -er '.protected_runtime.database_state_sha256' "${CANDIDATE}")"
readonly EXPECTED_NGINX_MAIN_SHA="$(jq -er '.protected_runtime.nginx_main_sha256' "${CANDIDATE}")"
readonly EXPECTED_NGINX_SITE_SHA="$(jq -er '.protected_runtime.nginx_site_sha256' "${CANDIDATE}")"

mutation_started=false
activation_complete=false

rollback_after_failure() {
    local original_exit="$1"
    local failed=false
    set +e
    atomic_state rolling_back activation_failed
    if DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
        "${MANAGER}" status >/dev/null 2>&1; then
        DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
            "${MANAGER}" stop || failed=true
    fi
    set_current_link "${PREVIOUS_BACKEND}" || failed=true
    if [[ -f "${FRONTEND_CURRENT}" && ! -L "${FRONTEND_CURRENT}" \
        && "$(<"${FRONTEND_CURRENT}")" == "${CANDIDATE_FRONTEND_RELEASE}" ]]; then
        "${FRONTEND_ROLLBACK}" || failed=true
    fi
    DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
        DOMEYE_COUNTRY_OUTAGE_RUNTIME_ROOT_OVERRIDE="${PREVIOUS_BACKEND}" \
        "${MANAGER}" start || failed=true
    curl -fsS --max-time 10 http://127.0.0.1:28471/api/v1/healthz >/dev/null \
        || failed=true
    if [[ "${failed}" == false ]]; then
        atomic_state rolled_back activation_failed_recovered
        error "激活失败，已恢复 ${PREVIOUS_BACKEND_RELEASE} / ${PREVIOUS_FRONTEND_RELEASE}"
        return "${original_exit}"
    fi
    atomic_state rollback_failed manual_intervention_required || true
    error '激活失败且自动恢复未闭合，需要立即人工处理'
    return 70
}

cleanup() {
    local exit_code=$?
    if [[ "${mutation_started}" == true && "${activation_complete}" != true ]]; then
        trap - EXIT
        rollback_after_failure "${exit_code}"
        return $?
    fi
    return "${exit_code}"
}

if (( EUID != 0 )); then
    error '生产激活必须由 root 执行'
    exit 1
fi
if [[ "${CONFIRM_RELEASE_ID:-}" != "${RELEASE_ID}" ]]; then
    error 'CONFIRM_RELEASE_ID 必须与 release-id 完全一致'
    exit 2
fi
for command_name in curl date flock jq ln mv nginx readlink sha256sum; do
    command -v "${command_name}" >/dev/null 2>&1 || {
        error "缺少命令：${command_name}"
        exit 1
    }
done
[[ -f "${CANDIDATE}" && ! -L "${CANDIDATE}" \
    && -x "${MANAGER}" && -x "${FRONTEND_INSTALLER}" && -x "${FRONTEND_ROLLBACK}" ]] || {
    error '候选证据或生命周期脚本缺失'
    exit 1
}
jq -e --arg release_id "${RELEASE_ID}" --arg backend_path "${RUNTIME_ROOT}" \
    '.schema_version == "domeye_country_outage_general_release_candidate_v1"
     and .release_id == $release_id and .status == "built"
     and .components.backend.path == $backend_path
     and .promotion_contract.candidate_canary_production_same_artifacts == true
     and .promotion_contract.rebuild_allowed == false
     and .protected_runtime.database_changed == false
     and .protected_runtime.nginx_changed == false
     and .protected_runtime.sidecar_changed == false
     and .protected_runtime.paid_model_calls == 0' "${CANDIDATE}" >/dev/null || {
    error '候选合同无效'
    exit 1
}
if [[ -e "${STATE}" || -L "${STATE}" || -e "${DEPLOYMENT}" || -L "${DEPLOYMENT}" ]]; then
    error '激活或部署证据已存在，拒绝重复激活'
    exit 1
fi
exec 9>"${LOCK}"
flock -n 9 || {
    error '已有激活或回滚操作正在执行'
    exit 1
}
[[ "$(readlink -f "${CURRENT_LINK}")" == "${PREVIOUS_BACKEND}" ]] || {
    error '当前 Backend 不是候选绑定的回滚基线'
    exit 1
}
[[ -f "${FRONTEND_CURRENT}" && ! -L "${FRONTEND_CURRENT}" \
    && "$(<"${FRONTEND_CURRENT}")" == "${PREVIOUS_FRONTEND_RELEASE}" ]] || {
    error '当前 Frontend 不是候选绑定的回滚基线'
    exit 1
}
[[ "$(sha256sum /home/bgpdata/Domeye-Core-dev-data/state.json | awk '{print $1}')" \
    == "${EXPECTED_DATABASE_SHA}" ]] || {
    error '数据库状态摘要相对候选发生变化'
    exit 1
}
[[ "$(sha256sum /etc/nginx/nginx.conf | awk '{print $1}')" == "${EXPECTED_NGINX_MAIN_SHA}" \
    && "$(sha256sum /etc/nginx/conf.d/domeye-core.conf | awk '{print $1}')" \
        == "${EXPECTED_NGINX_SITE_SHA}" ]] || {
    error 'Nginx 摘要相对候选发生变化'
    exit 1
}
nginx -t >/dev/null
DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=canary "${MANAGER}" status >/dev/null 2>&1 && {
    error 'canary 尚未停止，拒绝生产激活'
    exit 1
}
DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
    DOMEYE_COUNTRY_OUTAGE_RUNTIME_ROOT_OVERRIDE="${PREVIOUS_BACKEND}" \
    "${MANAGER}" status >/dev/null
curl -fsS --max-time 10 http://127.0.0.1:28471/api/v1/healthz >/dev/null

atomic_state prepared passed
trap cleanup EXIT
mutation_started=true
atomic_state switching_backend in_progress
DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
    DOMEYE_COUNTRY_OUTAGE_RUNTIME_ROOT_OVERRIDE="${PREVIOUS_BACKEND}" \
    "${MANAGER}" stop
set_current_link "${RUNTIME_ROOT}"
DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production "${MANAGER}" start
DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production "${MANAGER}" status >/dev/null

atomic_state switching_frontend in_progress
"${FRONTEND_INSTALLER}" "${CANDIDATE_FRONTEND_DIST}" "${CANDIDATE_FRONTEND_RELEASE}"
[[ "$(<"${FRONTEND_CURRENT}")" == "${CANDIDATE_FRONTEND_RELEASE}" ]] || {
    error 'Frontend current 未指向候选'
    exit 1
}
curl -fsS --max-time 10 http://127.0.0.1:28471/ | grep -F '<div id="app"></div>' >/dev/null
curl -fsS --max-time 10 http://127.0.0.1:28471/api/v1/healthz \
    | jq -e '.status == "ok" and .service == "domeye-core"' >/dev/null
[[ "$(readlink -f "${CURRENT_LINK}")" == "${RUNTIME_ROOT}" ]]
[[ "$(sha256sum /home/bgpdata/Domeye-Core-dev-data/state.json | awk '{print $1}')" \
    == "${EXPECTED_DATABASE_SHA}" ]]
[[ "$(sha256sum /etc/nginx/nginx.conf | awk '{print $1}')" == "${EXPECTED_NGINX_MAIN_SHA}" \
    && "$(sha256sum /etc/nginx/conf.d/domeye-core.conf | awk '{print $1}')" \
        == "${EXPECTED_NGINX_SITE_SHA}" ]]

atomic_state active passed
jq -n \
    --arg schema_version domeye_country_outage_general_deployment_v1 \
    --arg release_id "${RELEASE_ID}" \
    --arg deployed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg backend_release_id "${CANDIDATE_BACKEND_RELEASE}" \
    --arg backend_path "${RUNTIME_ROOT}" \
    --arg frontend_release_id "${CANDIDATE_FRONTEND_RELEASE}" \
    --arg frontend_path "$(jq -er '.components.frontend.path' "${CANDIDATE}")" \
    --arg rollback_backend_release_id "${PREVIOUS_BACKEND_RELEASE}" \
    --arg rollback_frontend_release_id "${PREVIOUS_FRONTEND_RELEASE}" \
    '{schema_version:$schema_version,release_id:$release_id,status:"deployed",deployed_at:$deployed_at,artifacts_rebuilt_during_promotion:false,components:{backend:{release_id:$backend_release_id,path:$backend_path},frontend:{release_id:$frontend_release_id,path:$frontend_path}},protected_runtime:{database_changed:false,nginx_changed:false,sidecar_changed:false,paid_model_calls:0},rollback:{backend_release_id:$rollback_backend_release_id,frontend_release_id:$rollback_frontend_release_id,available:true}}' \
    > "${DEPLOYMENT}"
chmod 0640 "${DEPLOYMENT}"
activation_complete=true
trap - EXIT
printf '国家中断通用观测生产激活成功：%s\n' "${RELEASE_ID}"
