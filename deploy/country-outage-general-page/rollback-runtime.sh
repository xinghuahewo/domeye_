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
readonly FRONTEND_ROLLBACK="${RUNTIME_ROOT}/deploy/artifacts/rollback-frontend-build.sh"
readonly PREVIOUS_BACKEND="$(jq -er '.rollback.backend_path' "${CANDIDATE}")"
readonly PREVIOUS_BACKEND_RELEASE="$(jq -er '.rollback.backend_release_id' "${CANDIDATE}")"
readonly PREVIOUS_FRONTEND_RELEASE="$(jq -er '.rollback.frontend_release_id' "${CANDIDATE}")"
readonly CANDIDATE_BACKEND_RELEASE="$(jq -er '.components.backend.release_id' "${CANDIDATE}")"
readonly CANDIDATE_FRONTEND_RELEASE="$(jq -er '.components.frontend.release_id' "${CANDIDATE}")"

error() {
    printf '国家中断通用观测生产回滚错误：%s\n' "$*" >&2
}

check_rollback() {
    [[ -f "${CANDIDATE}" && ! -L "${CANDIDATE}" \
        && -f "${STATE}" && ! -L "${STATE}" \
        && -f "${DEPLOYMENT}" && ! -L "${DEPLOYMENT}" \
        && -x "${MANAGER}" && -x "${FRONTEND_ROLLBACK}" ]] || {
        error '回滚证据或脚本缺失'
        return 1
    }
    [[ -d "${PREVIOUS_BACKEND}" && ! -L "${PREVIOUS_BACKEND}" \
        && "${PREVIOUS_BACKEND}" == /home/bgpdata/Domeye-Core-runtime/releases/*-backend ]] || {
        error '回滚 Backend 目标无效'
        return 1
    }
    jq -e --arg release_id "${RELEASE_ID}" \
        '.release_id == $release_id and .phase == "active" and .status == "passed"' \
        "${STATE}" >/dev/null || {
        error '当前激活状态不是 active/passed'
        return 1
    }
    jq -e --arg release_id "${RELEASE_ID}" \
        '.release_id == $release_id and .status == "deployed" and .rollback.available == true' \
        "${DEPLOYMENT}" >/dev/null || {
        error '部署证据未声明可回滚'
        return 1
    }
    [[ "$(readlink -f "${CURRENT_LINK}")" == "${RUNTIME_ROOT}" ]] || {
        error '当前 Backend 不是本次候选'
        return 1
    }
    [[ -f "${FRONTEND_CURRENT}" && ! -L "${FRONTEND_CURRENT}" \
        && "$(<"${FRONTEND_CURRENT}")" == "${CANDIDATE_FRONTEND_RELEASE}" ]] || {
        error '当前 Frontend 不是本次候选'
        return 1
    }
    DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production "${MANAGER}" status >/dev/null
    jq -n \
        --arg status ready \
        --arg release_id "${RELEASE_ID}" \
        --arg backend_release_id "${PREVIOUS_BACKEND_RELEASE}" \
        --arg backend_path "${PREVIOUS_BACKEND}" \
        --arg frontend_release_id "${PREVIOUS_FRONTEND_RELEASE}" \
        '{status:$status,release_id:$release_id,rollback:{backend_release_id:$backend_release_id,backend_path:$backend_path,frontend_release_id:$frontend_release_id}}'
}

set_current_link() {
    local temporary="/home/bgpdata/Domeye-Core-runtime/.current-rollback-${RELEASE_ID}.$$"
    [[ ! -e "${temporary}" && ! -L "${temporary}" ]]
    ln -s "${PREVIOUS_BACKEND}" "${temporary}"
    mv -Tf -- "${temporary}" "${CURRENT_LINK}"
}

write_rolled_back_state() {
    local tmp="${UNIFIED_ROOT}/.ACTIVATION-STATE.rollback.$$"
    jq \
        --arg phase rolled_back \
        --arg status passed \
        --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '.phase = $phase | .status = $status | .updated_at = $updated_at' \
        "${STATE}" > "${tmp}"
    chmod 0600 "${tmp}"
    mv -T -- "${tmp}" "${STATE}"
}

if (( $# != 1 )); then
    printf '用法：%s --check|--execute\n' "${0##*/}" >&2
    exit 2
fi
if (( EUID != 0 )); then
    error '生产回滚必须由 root 执行'
    exit 1
fi
case "$1" in
    --check)
        check_rollback
        ;;
    --execute)
        if [[ "${CONFIRM_RELEASE_ID:-}" != "${RELEASE_ID}" ]]; then
            error 'CONFIRM_RELEASE_ID 必须与待回滚 release-id 完全一致'
            exit 2
        fi
        exec 9>"${LOCK}"
        flock -n 9 || {
            error '已有激活或回滚操作正在执行'
            exit 1
        }
        check_rollback >/dev/null
        DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production "${MANAGER}" stop
        set_current_link
        "${FRONTEND_ROLLBACK}"
        DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
            DOMEYE_COUNTRY_OUTAGE_RUNTIME_ROOT_OVERRIDE="${PREVIOUS_BACKEND}" \
            "${MANAGER}" start
        DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
            DOMEYE_COUNTRY_OUTAGE_RUNTIME_ROOT_OVERRIDE="${PREVIOUS_BACKEND}" \
            "${MANAGER}" status >/dev/null
        [[ "$(<"${FRONTEND_CURRENT}")" == "${PREVIOUS_FRONTEND_RELEASE}" ]]
        curl -fsS --max-time 10 http://127.0.0.1:28471/api/v1/healthz >/dev/null
        write_rolled_back_state
        printf '生产已回滚：%s / %s\n' \
            "${PREVIOUS_BACKEND_RELEASE}" "${PREVIOUS_FRONTEND_RELEASE}"
        ;;
    *)
        error "未知参数：$1"
        exit 2
        ;;
esac
