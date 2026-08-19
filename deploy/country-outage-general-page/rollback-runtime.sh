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
readonly FAIL_CLOSED_EVIDENCE="${UNIFIED_ROOT}/FAIL-CLOSED.json"
readonly CURRENT_LINK='/home/bgpdata/Domeye-Core-runtime/current'
readonly FRONTEND_CURRENT='/home/bgpdata/Domeye-Core-runtime/web/state/frontend-current'
readonly LOCK='/home/bgpdata/Domeye-Core-runtime/var/country-outage-general-release.lock'
readonly MANAGER="${RUNTIME_ROOT}/deploy/country-outage-general-page/manage-runtime.sh"
readonly INTERACTIVE_AGENT_MANAGER="${RUNTIME_ROOT}/deploy/country-outage-agent/p1-chat/manage.sh"
readonly CANDIDATE_BACKEND_RELEASE="$(jq -er '.components.backend.release_id' "${CANDIDATE}")"
readonly CANDIDATE_FRONTEND_RELEASE="$(jq -er '.components.frontend.release_id' "${CANDIDATE}")"
readonly INTERACTIVE_AGENT_RELEASE_ID="$(jq -er '.interactive_agent.release_id' "${CANDIDATE}")"
FAIL_CLOSED_TEMP=''
FAIL_CLOSED_SHA=''
FAIL_CLOSED_AT=''

error() {
    printf '国家中断通用观测生产失败关闭错误：%s\n' "$*" >&2
}

sha256_hex_file() {
    local path="$1"
    local value
    if ! value="$(sha256sum -- "${path}" | awk 'NR == 1 {print $1}')"; then
        error "无法计算文件摘要：${path}"
        return 1
    fi
    [[ "${value}" =~ ^[a-f0-9]{64}$ ]] || {
        error "文件摘要格式无效：${path}"
        return 1
    }
    printf '%s\n' "${value}"
}

screen_session_is_absent() {
    local screen_name="$1"
    local sessions screen_status
    if sessions="$(screen -ls 2>&1)"; then
        screen_status=0
    else
        screen_status=$?
    fi
    (( screen_status == 0 || screen_status == 1 )) || {
        error "无法查询 Screen 会话：${screen_name}"
        return 1
    }
    if awk -v expected="${screen_name}" '
      $1 ~ /^[0-9]+\./ {
        name=$1
        sub(/^[0-9]+\./, "", name)
        if (name == expected) found=1
      }
      END {exit(found ? 0 : 1)}
    ' <<<"${sessions}"; then
        error "Screen 会话仍存在：${screen_name}"
        return 1
    fi
}

backend_port_is_closed() {
    local port="$1"
    local listeners
    if ! listeners="$(ss -H -ltn "sport = :${port}")"; then
        error "无法查询 Backend 监听状态：${port}"
        return 1
    fi
    [[ -z "${listeners}" ]] || {
        error "Backend 端口仍有监听者：${port}"
        return 1
    }
}

canary_backend_is_closed() {
    if ! backend_port_is_closed 38672 \
        || ! screen_session_is_absent domeye_country_outage_general_canary; then
        return 1
    fi
    if curl -fsS --max-time 5 \
        http://127.0.0.1:38672/api/v1/healthz >/dev/null 2>&1; then
        error 'canary Backend 38672 仍返回成功'
        return 1
    fi
}

production_backend_is_closed() {
    if ! backend_port_is_closed 28473 \
        || ! screen_session_is_absent domeye_core_app; then
        return 1
    fi
    if curl -fsS --max-time 5 \
        http://127.0.0.1:28471/api/v1/healthz >/dev/null 2>&1; then
        error '公共 Backend 路由仍返回成功'
        return 1
    fi
}

interactive_agent_is_closed() {
    local listeners
    if ! listeners="$(ss -H -ltn 'sport = :28476')"; then
        error '无法查询 Interactive Agent 监听状态'
        return 1
    fi
    [[ -z "${listeners}" ]] || {
        error '28476 仍有 Interactive Agent 监听者'
        return 1
    }
    if ! screen_session_is_absent domeye_interactive_agent_sidecar; then
        return 1
    fi
    if "${INTERACTIVE_AGENT_MANAGER}" status >/dev/null 2>&1; then
        error 'Interactive Agent 停止后仍报告 active'
        return 1
    fi
}

check_rollback() {
    [[ -f "${CANDIDATE}" && ! -L "${CANDIDATE}" \
        && -f "${STATE}" && ! -L "${STATE}" \
        && -f "${DEPLOYMENT}" && ! -L "${DEPLOYMENT}" \
        && -x "${MANAGER}" && -x "${INTERACTIVE_AGENT_MANAGER}" ]] || {
        error 'fail_closed 证据或生命周期脚本缺失'
        return 1
    }
    if ! jq -e --arg release_id "${RELEASE_ID}" '
      .schema_version == "domeye_country_outage_general_release_candidate_v2"
      and .release_id == $release_id
      and .rollback == {mode:"fail_closed",previous_release_id:null}
    ' "${CANDIDATE}" >/dev/null; then
        error '候选不是首发 fail_closed 合同'
        return 1
    fi
    if ! jq -e --arg release_id "${RELEASE_ID}" '
      .release_id == $release_id
      and .phase == "production_verified"
      and .status == "passed"
      and .rollback == {mode:"fail_closed",previous_release_id:null}
    ' "${STATE}" >/dev/null; then
        error '当前激活状态不是 production_verified/passed'
        return 1
    fi
    if ! jq -e --arg release_id "${RELEASE_ID}" '
      .release_id == $release_id
      and .status == "production_verified"
      and .production_verified == true
      and .rollback.mode == "fail_closed"
      and .rollback.previous_release_id == null
      and .rollback.available == false
    ' "${DEPLOYMENT}" >/dev/null; then
        error '部署回执未声明首发 fail_closed'
        return 1
    fi
    [[ "$(readlink -f -- "${CURRENT_LINK}")" == "${RUNTIME_ROOT}" ]] || {
        error '当前 Backend 不是本次新架构候选'
        return 1
    }
    [[ -f "${FRONTEND_CURRENT}" && ! -L "${FRONTEND_CURRENT}" \
        && "$(<"${FRONTEND_CURRENT}")" == "${CANDIDATE_FRONTEND_RELEASE}" ]] || {
        error '当前 Frontend 不是本次候选'
        return 1
    }
    if ! DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
        "${MANAGER}" status >/dev/null; then
        error '候选 Backend 当前运行身份无效'
        return 1
    fi
    local interactive_status
    if ! interactive_status="$("${INTERACTIVE_AGENT_MANAGER}" status)"; then
        error 'Interactive Agent 当前组合状态无效'
        return 1
    fi
    if ! jq -e --arg release_id "${INTERACTIVE_AGENT_RELEASE_ID}" '
      .release_id == $release_id
      and .lifecycle_state == "verified"
      and .promotion_state == "verified"
      and .production_verified == true
    ' <<<"${interactive_status}" >/dev/null; then
        error 'Interactive Agent 尚未形成 verified 生产状态'
        return 1
    fi
    if [[ -e "${FAIL_CLOSED_EVIDENCE}" || -L "${FAIL_CLOSED_EVIDENCE}" ]]; then
        error 'FAIL-CLOSED.json 已存在，拒绝重复执行'
        return 1
    fi
    jq -n --arg status ready --arg mode fail_closed \
        --arg release_id "${RELEASE_ID}" \
        --arg backend_release_id "${CANDIDATE_BACKEND_RELEASE}" \
        --arg frontend_release_id "${CANDIDATE_FRONTEND_RELEASE}" \
        --arg interactive_release_id "${INTERACTIVE_AGENT_RELEASE_ID}" '
      {
        status:$status,
        mode:$mode,
        release_id:$release_id,
        targets:{
          backend_release_id:$backend_release_id,
          frontend_release_id:$frontend_release_id,
          interactive_agent_release_id:$interactive_release_id
        },
        action:"stop_only",
        restore_route:false
      }
    '
}

write_failed_closed_state() {
    local phase="$1"
    local status="$2"
    local detail="$3"
    local evidence_sha="${4:-}"
    local temporary="${UNIFIED_ROOT}/.ACTIVATION-STATE.fail-closed.$$"
    if ! jq --arg phase "${phase}" --arg status "${status}" \
        --arg detail "${detail}" \
        --arg evidence_sha "${evidence_sha}" \
        --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
      .phase = $phase
      | .status = $status
      | .detail = $detail
      | .updated_at = $updated_at
      | .rollback = {mode:"fail_closed",previous_release_id:null}
      | .fail_closed = {
          complete:($evidence_sha != ""),
          evidence_path:(if $evidence_sha == "" then null else "FAIL-CLOSED.json" end),
          evidence_sha256:(if $evidence_sha == "" then null else $evidence_sha end)
        }
    ' "${STATE}" > "${temporary}"; then
        error '无法生成 failed_closed 状态'
        return 1
    fi
    if ! chmod 0600 "${temporary}" \
        || ! mv -T -- "${temporary}" "${STATE}"; then
        unlink "${temporary}" 2>/dev/null || true
        error '无法原子写入 failed_closed 状态'
        return 1
    fi
}

write_failed_closed_deployment() {
    local complete="$1"
    local evidence_sha="$2"
    local temporary="${UNIFIED_ROOT}/.DEPLOYMENT.fail-closed.$$"
    if ! jq --arg closed_at "${FAIL_CLOSED_AT}" \
        --arg evidence_sha "${evidence_sha}" \
        --argjson complete "${complete}" '
      .status = "failed_closed"
      | .production_verified = false
      | .was_production_verified = true
      | .closed_at = $closed_at
      | .fail_closed = {
          complete:$complete,
          evidence_path:(if $complete then "FAIL-CLOSED.json" else null end),
          evidence_sha256:(if $complete then $evidence_sha else null end)
        }
    ' "${DEPLOYMENT}" > "${temporary}"; then
        error '无法生成 failed_closed DEPLOYMENT 状态'
        return 1
    fi
    if ! chmod 0640 "${temporary}" \
        || ! mv -T -- "${temporary}" "${DEPLOYMENT}"; then
        unlink "${temporary}" 2>/dev/null || true
        error '无法原子把 DEPLOYMENT 转为 failed_closed'
        return 1
    fi
}

prepare_fail_closed_evidence() {
    FAIL_CLOSED_TEMP="${UNIFIED_ROOT}/.FAIL-CLOSED.tmp.$$"
    if ! jq -n --arg release_id "${RELEASE_ID}" \
        --arg closed_at "${FAIL_CLOSED_AT}" \
        --arg backend_release_id "${CANDIDATE_BACKEND_RELEASE}" \
        --arg frontend_release_id "${CANDIDATE_FRONTEND_RELEASE}" \
        --arg interactive_release_id "${INTERACTIVE_AGENT_RELEASE_ID}" '
      {
        schema_version:"domeye_country_outage_general_fail_closed_v1",
        release_id:$release_id,
        status:"failed_closed",
        mode:"fail_closed",
        closed_at:$closed_at,
        stopped:{
          backend_release_id:$backend_release_id,
          interactive_agent_release_id:$interactive_release_id
        },
        frontend:{release_id:$frontend_release_id,route_restored:false},
        public_backend_port_open:false,
        interactive_agent_port_open:false,
        old_route_restored:false
      }
    ' > "${FAIL_CLOSED_TEMP}"; then
        error '无法生成 FAIL-CLOSED 回执'
        return 1
    fi
    if ! chmod 0640 "${FAIL_CLOSED_TEMP}"; then
        error '无法收紧 FAIL-CLOSED 临时回执权限'
        return 1
    fi
    local evidence_hex
    if ! evidence_hex="$(sha256_hex_file "${FAIL_CLOSED_TEMP}")"; then
        error '无法计算 FAIL-CLOSED 临时回执摘要'
        return 1
    fi
    FAIL_CLOSED_SHA="sha256:${evidence_hex}"
}

publish_fail_closed_evidence() {
    local temporary_hex published_hex
    [[ -n "${FAIL_CLOSED_TEMP}" && -f "${FAIL_CLOSED_TEMP}" \
        && ! -L "${FAIL_CLOSED_TEMP}" \
        && "${FAIL_CLOSED_SHA}" =~ ^sha256:[a-f0-9]{64}$ ]] || {
        error '待发布 FAIL-CLOSED 回执路径或预期摘要无效'
        return 1
    }
    if ! temporary_hex="$(sha256_hex_file "${FAIL_CLOSED_TEMP}")"; then
        error '无法复核待发布 FAIL-CLOSED 回执摘要'
        return 1
    fi
    [[ "sha256:${temporary_hex}" == "${FAIL_CLOSED_SHA}" ]] || {
        error '待发布 FAIL-CLOSED 回执摘要漂移'
        return 1
    }
    if ! mv -n -- "${FAIL_CLOSED_TEMP}" "${FAIL_CLOSED_EVIDENCE}"; then
        error '无法原子写入 create-only FAIL-CLOSED 回执'
        return 1
    fi
    [[ ! -e "${FAIL_CLOSED_TEMP}" && ! -L "${FAIL_CLOSED_TEMP}" \
        && -f "${FAIL_CLOSED_EVIDENCE}" \
        && ! -L "${FAIL_CLOSED_EVIDENCE}" ]] || {
        error 'FAIL-CLOSED 回执已存在或原子写入未闭合'
        return 1
    }
    if ! published_hex="$(sha256_hex_file "${FAIL_CLOSED_EVIDENCE}")"; then
        error '无法复核已发布 FAIL-CLOSED 回执摘要'
        return 1
    fi
    [[ "sha256:${published_hex}" == "${FAIL_CLOSED_SHA}" ]] || {
        error '已发布 FAIL-CLOSED 回执摘要漂移'
        return 1
    }
    FAIL_CLOSED_TEMP=''
}

if (( $# != 1 )); then
    printf '用法：%s --check|--execute\n' "${0##*/}" >&2
    exit 2
fi
if (( EUID != 0 )); then
    error '生产失败关闭必须由 root 执行'
    exit 1
fi
for command_name in awk chmod curl date flock jq mv readlink screen sha256sum \
    ss unlink; do
    command -v "${command_name}" >/dev/null 2>&1 || {
        error "缺少命令：${command_name}"
        exit 1
    }
done

case "$1" in
    --check)
        check_rollback
        ;;
    --execute)
        if [[ "${CONFIRM_RELEASE_ID:-}" != "${RELEASE_ID}" ]]; then
            error 'CONFIRM_RELEASE_ID 必须与待失败关闭 release-id 完全一致'
            exit 2
        fi
        exec 9>"${LOCK}"
        flock -n 9 || {
            error '已有激活或 fail_closed 操作正在执行'
            exit 1
        }
        if ! check_rollback >/dev/null; then
            exit 1
        fi
        backend_closed=true
        interactive_closed=true
        backend_stop_failed=false
        if ! DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=canary \
            "${MANAGER}" stop; then
            backend_stop_failed=true
        fi
        if ! DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
            "${MANAGER}" stop; then
            backend_stop_failed=true
        fi
        if ! canary_backend_is_closed \
            || ! production_backend_is_closed; then
            backend_closed=false
        fi
        if [[ "${backend_stop_failed}" == true \
            && "${backend_closed}" == true ]]; then
            printf 'Backend stop 命令曾失败，但 28473/38672 与对应会话均已证明关闭\n' >&2
        fi
        if ! "${INTERACTIVE_AGENT_MANAGER}" rollback; then
            if ! "${INTERACTIVE_AGENT_MANAGER}" stop; then
                interactive_closed=false
            fi
        fi
        if ! interactive_agent_is_closed; then
            interactive_closed=false
        fi
        FAIL_CLOSED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        if [[ "${backend_closed}" != true \
            || "${interactive_closed}" != true ]]; then
            deployment_marked=false
            state_marked=false
            if write_failed_closed_deployment false ''; then
                deployment_marked=true
            fi
            if write_failed_closed_state fail_closed_incomplete \
                manual_intervention_required \
                '无法证明 Backend 与 Interactive Agent 均已关闭；未恢复旧路由'; then
                state_marked=true
            fi
            if [[ "${deployment_marked}" != true \
                || "${state_marked}" != true ]]; then
                error '同时无法完整记录 fail_closed_incomplete 状态'
            fi
            error '未能证明完整 failed_closed；没有恢复任何旧路由'
            exit 70
        fi
        if ! prepare_fail_closed_evidence; then
            exit 70
        fi
        if ! write_failed_closed_deployment false ''; then
            exit 70
        fi
        if ! write_failed_closed_state fail_closing in_progress \
            'Backend 与 Interactive Agent 已停止；正在发布 create-only FAIL-CLOSED 证据'; then
            exit 70
        fi
        if ! publish_fail_closed_evidence; then
            exit 70
        fi
        if ! write_failed_closed_state failed_closed failed_closed \
            'Backend 与 Interactive Agent 已停止；没有恢复旧路由' \
            "${FAIL_CLOSED_SHA}"; then
            exit 70
        fi
        if ! write_failed_closed_deployment true "${FAIL_CLOSED_SHA}"; then
            exit 70
        fi
        printf '生产已失败关闭且未恢复任何旧路由：%s\n' "${RELEASE_ID}"
        ;;
    *)
        error "未知参数：$1"
        exit 2
        ;;
esac
