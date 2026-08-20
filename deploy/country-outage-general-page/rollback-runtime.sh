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
readonly CANARY_EVIDENCE="${UNIFIED_ROOT}/CANARY-VERIFICATION.json"
readonly PRODUCTION_EVIDENCE="${UNIFIED_ROOT}/PRODUCTION-VERIFICATION.json"
readonly FAIL_CLOSED_EVIDENCE="${UNIFIED_ROOT}/FAIL-CLOSED.json"
readonly CURRENT_LINK='/home/bgpdata/Domeye-Core-runtime/current'
readonly FRONTEND_CURRENT='/home/bgpdata/Domeye-Core-runtime/web/state/frontend-current'
readonly LOCK='/home/bgpdata/Domeye-Core-runtime/var/country-outage-general-release.lock'
readonly MANAGER="${RUNTIME_ROOT}/deploy/country-outage-general-page/manage-runtime.sh"
readonly INTERACTIVE_AGENT_MANAGER="${RUNTIME_ROOT}/deploy/country-outage-agent/p1-chat/manage.sh"
readonly INTERACTIVE_AGENT_CURRENT='/home/bgpdata/Domeye-Core-runtime/country-outage-interactive-agent/current'
readonly INTERACTIVE_AGENT_ACTIVE='/home/bgpdata/Domeye-Core-runtime/country-outage-interactive-agent/state/active.json'
readonly CANDIDATE_BACKEND_RELEASE="$(jq -er '.components.backend.release_id' "${CANDIDATE}")"
readonly CANDIDATE_FRONTEND_RELEASE="$(jq -er '.components.frontend.release_id' "${CANDIDATE}")"
readonly INTERACTIVE_AGENT_RELEASE_ID="$(jq -er '.interactive_agent.release_id' "${CANDIDATE}")"
readonly INTERACTIVE_AGENT_RELEASE_MANIFEST_SHA="$(jq -er '.interactive_agent.release_manifest_sha256' "${CANDIDATE}")"
readonly INTERACTIVE_AGENT_CANDIDATE_ID="$(jq -er '.interactive_agent.candidate_id' "${CANDIDATE}")"
readonly INTERACTIVE_AGENT_CANDIDATE_MANIFEST_SHA="$(jq -er '.interactive_agent.candidate_manifest_sha256' "${CANDIDATE}")"
readonly INTERACTIVE_AGENT_ACCEPTANCE_RECORD_ID="$(jq -er '.interactive_agent.acceptance_record_id' "${CANDIDATE}")"
readonly INTERACTIVE_AGENT_ACCEPTANCE_RECORD_SHA="$(jq -er '.interactive_agent.acceptance_record_sha256' "${CANDIDATE}")"
readonly INTERACTIVE_AGENT_ACCEPTANCE_REPLAY_SHA="$(jq -er '.interactive_agent.acceptance_replay_receipt_sha256' "${CANDIDATE}")"
FAIL_CLOSED_TEMP=''
FAIL_CLOSED_SHA=''
FAIL_CLOSED_AT=''
PRE_ROLLBACK_CANDIDATE_SHA=''
PRE_ROLLBACK_CANARY_SHA=''
PRE_ROLLBACK_PRODUCTION_SHA=''

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

freeze_pre_rollback_evidence() {
    local candidate_hex canary_hex production_hex
    [[ -f "${CANDIDATE}" && ! -L "${CANDIDATE}" \
        && -f "${CANARY_EVIDENCE}" && ! -L "${CANARY_EVIDENCE}" \
        && -f "${PRODUCTION_EVIDENCE}" && ! -L "${PRODUCTION_EVIDENCE}" ]] || {
        error '发布前 Candidate、canary 或 production 证据不是普通文件'
        return 1
    }
    candidate_hex="$(sha256_hex_file "${CANDIDATE}")" || return 1
    canary_hex="$(sha256_hex_file "${CANARY_EVIDENCE}")" || return 1
    production_hex="$(sha256_hex_file "${PRODUCTION_EVIDENCE}")" || return 1
    PRE_ROLLBACK_CANDIDATE_SHA="sha256:${candidate_hex}"
    PRE_ROLLBACK_CANARY_SHA="sha256:${canary_hex}"
    PRE_ROLLBACK_PRODUCTION_SHA="sha256:${production_hex}"
}

frozen_pre_rollback_evidence_is_unchanged() {
    local candidate_hex canary_hex production_hex
    [[ "${PRE_ROLLBACK_CANDIDATE_SHA}" =~ ^sha256:[a-f0-9]{64}$ \
        && "${PRE_ROLLBACK_CANARY_SHA}" =~ ^sha256:[a-f0-9]{64}$ \
        && "${PRE_ROLLBACK_PRODUCTION_SHA}" =~ ^sha256:[a-f0-9]{64}$ \
        && -f "${CANDIDATE}" && ! -L "${CANDIDATE}" \
        && -f "${CANARY_EVIDENCE}" && ! -L "${CANARY_EVIDENCE}" \
        && -f "${PRODUCTION_EVIDENCE}" && ! -L "${PRODUCTION_EVIDENCE}" ]] || {
        error '发布前冻结证据缺失或类型漂移'
        return 1
    }
    candidate_hex="$(sha256_hex_file "${CANDIDATE}")" || return 1
    canary_hex="$(sha256_hex_file "${CANARY_EVIDENCE}")" || return 1
    production_hex="$(sha256_hex_file "${PRODUCTION_EVIDENCE}")" || return 1
    [[ "sha256:${candidate_hex}" == "${PRE_ROLLBACK_CANDIDATE_SHA}" \
        && "sha256:${canary_hex}" == "${PRE_ROLLBACK_CANARY_SHA}" \
        && "sha256:${production_hex}" == "${PRE_ROLLBACK_PRODUCTION_SHA}" ]] || {
        error '发布前冻结证据摘要漂移'
        return 1
    }
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
    if curl --disable --noproxy '*' --proto '=http' --max-redirs 0 \
        -fsS --max-time 5 \
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
    if curl --disable --noproxy '*' --proto '=http' --max-redirs 0 \
        -fsS --max-time 5 \
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
    [[ ! -e "${INTERACTIVE_AGENT_CURRENT}" && ! -L "${INTERACTIVE_AGENT_CURRENT}" \
        && ! -e "${INTERACTIVE_AGENT_ACTIVE}" && ! -L "${INTERACTIVE_AGENT_ACTIVE}" ]] || {
        error 'Interactive Agent current 或 active 状态仍存在'
        return 1
    }
    if "${INTERACTIVE_AGENT_MANAGER}" status >/dev/null 2>&1; then
        error 'Interactive Agent 停止后仍报告 active'
        return 1
    fi
}

check_rollback() {
    [[ -f "${CANDIDATE}" && ! -L "${CANDIDATE}" \
        && -f "${STATE}" && ! -L "${STATE}" \
        && -f "${DEPLOYMENT}" && ! -L "${DEPLOYMENT}" \
        && -f "${CANARY_EVIDENCE}" && ! -L "${CANARY_EVIDENCE}" \
        && -f "${PRODUCTION_EVIDENCE}" && ! -L "${PRODUCTION_EVIDENCE}" \
        && -x "${MANAGER}" && -x "${INTERACTIVE_AGENT_MANAGER}" ]] || {
        error 'fail_closed 证据或生命周期脚本缺失'
        return 1
    }
    if ! jq -e --arg release_id "${RELEASE_ID}" \
        --arg interactive_release_id "${INTERACTIVE_AGENT_RELEASE_ID}" \
        --arg release_manifest_sha "${INTERACTIVE_AGENT_RELEASE_MANIFEST_SHA}" \
        --arg candidate_id "${INTERACTIVE_AGENT_CANDIDATE_ID}" \
        --arg candidate_manifest_sha "${INTERACTIVE_AGENT_CANDIDATE_MANIFEST_SHA}" \
        --arg acceptance_id "${INTERACTIVE_AGENT_ACCEPTANCE_RECORD_ID}" \
        --arg acceptance_sha "${INTERACTIVE_AGENT_ACCEPTANCE_RECORD_SHA}" \
        --arg acceptance_replay_sha "${INTERACTIVE_AGENT_ACCEPTANCE_REPLAY_SHA}" '
      .schema_version == "domeye_country_outage_general_release_candidate_v2"
      and .release_id == $release_id
      and .status == "built"
      and .rollback == {mode:"fail_closed",previous_release_id:null}
      and .interactive_agent.release_id == $interactive_release_id
      and .interactive_agent.release_manifest_sha256 == $release_manifest_sha
      and .interactive_agent.candidate_id == $candidate_id
      and .interactive_agent.candidate_manifest_sha256 == $candidate_manifest_sha
      and .interactive_agent.acceptance_record_id == $acceptance_id
      and .interactive_agent.acceptance_record_sha256 == $acceptance_sha
      and .interactive_agent.acceptance_replay_receipt_sha256 == $acceptance_replay_sha
      and ($release_manifest_sha | test("^sha256:[a-f0-9]{64}$"))
      and ($candidate_id | test("^manifest:sha256:[a-f0-9]{64}$"))
      and ($candidate_manifest_sha | test("^sha256:[a-f0-9]{64}$"))
      and ($acceptance_id | test("^acceptance-record-sha256:[a-f0-9]{64}$"))
      and ($acceptance_sha | test("^sha256:[a-f0-9]{64}$"))
      and ($acceptance_replay_sha | test("^sha256:[a-f0-9]{64}$"))
    ' "${CANDIDATE}" >/dev/null; then
        error '候选不是首发 fail_closed 合同'
        return 1
    fi
    if ! freeze_pre_rollback_evidence; then
        return 1
    fi
    if ! jq -e --arg release_id "${RELEASE_ID}" \
        --arg backend_release_id "${CANDIDATE_BACKEND_RELEASE}" \
        --arg frontend_release_id "${CANDIDATE_FRONTEND_RELEASE}" \
        --arg interactive_release_id "${INTERACTIVE_AGENT_RELEASE_ID}" \
        --arg candidate_id "${INTERACTIVE_AGENT_CANDIDATE_ID}" \
        --arg acceptance_id "${INTERACTIVE_AGENT_ACCEPTANCE_RECORD_ID}" \
        --arg canary_sha "${PRE_ROLLBACK_CANARY_SHA}" \
        --arg production_sha "${PRE_ROLLBACK_PRODUCTION_SHA}" '
      .schema_version == "domeye_country_outage_general_activation_v2"
      and .release_id == $release_id
      and .phase == "production_verified"
      and .status == "passed"
      and .candidate.backend.release_id == $backend_release_id
      and .candidate.frontend.release_id == $frontend_release_id
      and .candidate.interactive_agent == {
        release_id:$interactive_release_id,
        candidate_id:$candidate_id,
        acceptance_record_id:$acceptance_id
      }
      and .verification == {
        canary:{path:"CANARY-VERIFICATION.json",sha256:$canary_sha},
        production:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_sha}
      }
      and .rollback == {mode:"fail_closed",previous_release_id:null}
    ' "${STATE}" >/dev/null; then
        error '当前激活状态未绑定 production_verified Candidate 与双证据'
        return 1
    fi
    if ! jq -e --arg release_id "${RELEASE_ID}" \
        --arg backend_release_id "${CANDIDATE_BACKEND_RELEASE}" \
        --arg frontend_release_id "${CANDIDATE_FRONTEND_RELEASE}" \
        --arg interactive_release_id "${INTERACTIVE_AGENT_RELEASE_ID}" \
        --arg candidate_id "${INTERACTIVE_AGENT_CANDIDATE_ID}" \
        --arg acceptance_id "${INTERACTIVE_AGENT_ACCEPTANCE_RECORD_ID}" \
        --arg canary_sha "${PRE_ROLLBACK_CANARY_SHA}" \
        --arg production_sha "${PRE_ROLLBACK_PRODUCTION_SHA}" '
      .schema_version == "domeye_country_outage_general_deployment_v2"
      and .release_id == $release_id
      and .status == "production_verified"
      and .production_verified == true
      and .components.backend.release_id == $backend_release_id
      and .components.frontend.release_id == $frontend_release_id
      and .components.interactive_agent == {
        release_id:$interactive_release_id,
        candidate_id:$candidate_id,
        acceptance_record_id:$acceptance_id
      }
      and .verification == {
        canary:{path:"CANARY-VERIFICATION.json",sha256:$canary_sha},
        production:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_sha}
      }
      and .rollback.mode == "fail_closed"
      and .rollback.previous_release_id == null
      and .rollback.available == false
    ' "${DEPLOYMENT}" >/dev/null; then
        error '部署回执未绑定 production_verified Candidate 与双证据'
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
    local runtime_status
    if ! runtime_status="$(DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
        "${MANAGER}" status)"; then
        error '候选 Backend 当前运行身份无效'
        return 1
    fi
    if ! jq -e --arg release_id "${RELEASE_ID}" '
      .status == "running"
      and .mode == "production"
      and .release_id == $release_id
      and .workflow_completion.state == "verified"
      and .workflow_completion.requires_renderer_guard_correct_answer == true
      and .workflow_completion.requires_general_production_evidence == true
    ' <<<"${runtime_status}" >/dev/null; then
        error '候选 Backend 仍未形成 Renderer + Guard 双证据完成态'
        return 1
    fi
    local interactive_status
    if ! interactive_status="$("${INTERACTIVE_AGENT_MANAGER}" status)"; then
        error 'Interactive Agent 当前组合状态无效'
        return 1
    fi
    if ! jq -e --arg release_id "${INTERACTIVE_AGENT_RELEASE_ID}" \
        --arg candidate_id "${INTERACTIVE_AGENT_CANDIDATE_ID}" '
      .release_id == $release_id
      and .candidate_id == $candidate_id
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
        --arg interactive_release_id "${INTERACTIVE_AGENT_RELEASE_ID}" \
        --arg candidate_sha "${PRE_ROLLBACK_CANDIDATE_SHA}" \
        --arg release_manifest_sha "${INTERACTIVE_AGENT_RELEASE_MANIFEST_SHA}" \
        --arg candidate_id "${INTERACTIVE_AGENT_CANDIDATE_ID}" \
        --arg candidate_manifest_sha "${INTERACTIVE_AGENT_CANDIDATE_MANIFEST_SHA}" \
        --arg acceptance_id "${INTERACTIVE_AGENT_ACCEPTANCE_RECORD_ID}" \
        --arg acceptance_sha "${INTERACTIVE_AGENT_ACCEPTANCE_RECORD_SHA}" \
        --arg acceptance_replay_sha "${INTERACTIVE_AGENT_ACCEPTANCE_REPLAY_SHA}" \
        --arg canary_sha "${PRE_ROLLBACK_CANARY_SHA}" \
        --arg production_sha "${PRE_ROLLBACK_PRODUCTION_SHA}" '
      {
        schema_version:"domeye_country_outage_general_fail_closed_v2",
        release_id:$release_id,
        status:"failed_closed",
        mode:"fail_closed",
        closed_at:$closed_at,
        pre_rollback_evidence:{
          general_candidate:{
            path:"CANDIDATE-MANIFEST.json",
            sha256:$candidate_sha
          },
          interactive_agent:{
            release_id:$interactive_release_id,
            release_manifest_sha256:$release_manifest_sha,
            candidate_id:$candidate_id,
            candidate_manifest_sha256:$candidate_manifest_sha,
            acceptance_record_id:$acceptance_id,
            acceptance_record_sha256:$acceptance_sha,
            acceptance_replay_receipt_sha256:$acceptance_replay_sha
          },
          verification:{
            canary:{path:"CANARY-VERIFICATION.json",sha256:$canary_sha},
            production:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_sha}
          }
        },
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

discard_fail_closed_temp() {
    if [[ -n "${FAIL_CLOSED_TEMP}" && -f "${FAIL_CLOSED_TEMP}" \
        && ! -L "${FAIL_CLOSED_TEMP}" ]]; then
        unlink -- "${FAIL_CLOSED_TEMP}" 2>/dev/null || true
    fi
    FAIL_CLOSED_TEMP=''
}

verify_failed_closed_closure() {
    local evidence_path="${1:-${FAIL_CLOSED_EVIDENCE}}"
    if ! frozen_pre_rollback_evidence_is_unchanged; then
        return 1
    fi
    [[ -f "${evidence_path}" && ! -L "${evidence_path}" \
        && -f "${STATE}" && ! -L "${STATE}" \
        && -f "${DEPLOYMENT}" && ! -L "${DEPLOYMENT}" ]] || {
        error 'failed_closed 最终回执或状态文件缺失'
        return 1
    }
    local published_hex
    published_hex="$(sha256_hex_file "${evidence_path}")" || return 1
    [[ "sha256:${published_hex}" == "${FAIL_CLOSED_SHA}" ]] || {
        error 'failed_closed 最终回执摘要漂移'
        return 1
    }
    if ! jq -e \
        --arg release_id "${RELEASE_ID}" \
        --arg closed_at "${FAIL_CLOSED_AT}" \
        --arg evidence_sha "${FAIL_CLOSED_SHA}" \
        --arg backend_release_id "${CANDIDATE_BACKEND_RELEASE}" \
        --arg frontend_release_id "${CANDIDATE_FRONTEND_RELEASE}" \
        --arg interactive_release_id "${INTERACTIVE_AGENT_RELEASE_ID}" \
        --arg candidate_sha "${PRE_ROLLBACK_CANDIDATE_SHA}" \
        --arg release_manifest_sha "${INTERACTIVE_AGENT_RELEASE_MANIFEST_SHA}" \
        --arg candidate_id "${INTERACTIVE_AGENT_CANDIDATE_ID}" \
        --arg candidate_manifest_sha "${INTERACTIVE_AGENT_CANDIDATE_MANIFEST_SHA}" \
        --arg acceptance_id "${INTERACTIVE_AGENT_ACCEPTANCE_RECORD_ID}" \
        --arg acceptance_sha "${INTERACTIVE_AGENT_ACCEPTANCE_RECORD_SHA}" \
        --arg acceptance_replay_sha "${INTERACTIVE_AGENT_ACCEPTANCE_REPLAY_SHA}" \
        --arg canary_sha "${PRE_ROLLBACK_CANARY_SHA}" \
        --arg production_sha "${PRE_ROLLBACK_PRODUCTION_SHA}" \
        --slurpfile state "${STATE}" \
        --slurpfile deployment "${DEPLOYMENT}" '
      .schema_version == "domeye_country_outage_general_fail_closed_v2"
      and .release_id == $release_id
      and .status == "failed_closed"
      and .mode == "fail_closed"
      and .closed_at == $closed_at
      and .pre_rollback_evidence == {
        general_candidate:{path:"CANDIDATE-MANIFEST.json",sha256:$candidate_sha},
        interactive_agent:{
          release_id:$interactive_release_id,
          release_manifest_sha256:$release_manifest_sha,
          candidate_id:$candidate_id,
          candidate_manifest_sha256:$candidate_manifest_sha,
          acceptance_record_id:$acceptance_id,
          acceptance_record_sha256:$acceptance_sha,
          acceptance_replay_receipt_sha256:$acceptance_replay_sha
        },
        verification:{
          canary:{path:"CANARY-VERIFICATION.json",sha256:$canary_sha},
          production:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_sha}
        }
      }
      and .stopped == {
        backend_release_id:$backend_release_id,
        interactive_agent_release_id:$interactive_release_id
      }
      and .frontend == {release_id:$frontend_release_id,route_restored:false}
      and .public_backend_port_open == false
      and .interactive_agent_port_open == false
      and .old_route_restored == false
      and $state[0].schema_version == "domeye_country_outage_general_activation_v2"
      and $state[0].release_id == $release_id
      and $state[0].phase == "failed_closed"
      and $state[0].status == "failed_closed"
      and $state[0].candidate.backend.release_id == $backend_release_id
      and $state[0].candidate.frontend.release_id == $frontend_release_id
      and $state[0].candidate.interactive_agent == {
        release_id:$interactive_release_id,
        candidate_id:$candidate_id,
        acceptance_record_id:$acceptance_id
      }
      and $state[0].verification == {
        canary:{path:"CANARY-VERIFICATION.json",sha256:$canary_sha},
        production:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_sha}
      }
      and $state[0].rollback == {mode:"fail_closed",previous_release_id:null}
      and $state[0].fail_closed == {
        complete:true,
        evidence_path:"FAIL-CLOSED.json",
        evidence_sha256:$evidence_sha
      }
      and $deployment[0].schema_version == "domeye_country_outage_general_deployment_v2"
      and $deployment[0].release_id == $release_id
      and $deployment[0].status == "failed_closed"
      and $deployment[0].production_verified == false
      and $deployment[0].was_production_verified == true
      and $deployment[0].closed_at == $closed_at
      and $deployment[0].components.backend.release_id == $backend_release_id
      and $deployment[0].components.frontend.release_id == $frontend_release_id
      and $deployment[0].components.interactive_agent == {
        release_id:$interactive_release_id,
        candidate_id:$candidate_id,
        acceptance_record_id:$acceptance_id
      }
      and $deployment[0].verification == {
        canary:{path:"CANARY-VERIFICATION.json",sha256:$canary_sha},
        production:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_sha}
      }
      and $deployment[0].rollback == {
        mode:"fail_closed",
        previous_release_id:null,
        available:false
      }
      and $deployment[0].fail_closed == {
        complete:true,
        evidence_path:"FAIL-CLOSED.json",
        evidence_sha256:$evidence_sha
      }
    ' "${evidence_path}" >/dev/null; then
        error 'failed_closed 最终 Candidate、双验证或状态闭包无效'
        return 1
    fi
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
        if ! frozen_pre_rollback_evidence_is_unchanged; then
            write_failed_closed_deployment false '' || true
            write_failed_closed_state fail_closed_incomplete \
                manual_intervention_required \
                'Backend 与 Interactive Agent 已停止，但发布前冻结证据发生漂移；未恢复旧路由' \
                || true
            error '发布前 Candidate 或双验证证据漂移；没有发布完成回执'
            exit 70
        fi
        if ! prepare_fail_closed_evidence; then
            write_failed_closed_deployment false '' || true
            write_failed_closed_state fail_closed_incomplete \
                manual_intervention_required \
                'Backend 与 Interactive Agent 已停止，但无法生成绑定证据的回执；未恢复旧路由' \
                || true
            discard_fail_closed_temp
            exit 70
        fi
        if ! write_failed_closed_deployment false ''; then
            write_failed_closed_state fail_closed_incomplete \
                manual_intervention_required \
                'Backend 与 Interactive Agent 已停止，但无法记录 DEPLOYMENT 失败关闭状态；未恢复旧路由' \
                || true
            discard_fail_closed_temp
            exit 70
        fi
        if ! write_failed_closed_state fail_closing in_progress \
            'Backend 与 Interactive Agent 已停止；正在发布 create-only FAIL-CLOSED 证据'; then
            discard_fail_closed_temp
            exit 70
        fi
        if ! write_failed_closed_state failed_closed failed_closed \
            'Backend 与 Interactive Agent 已停止；没有恢复旧路由' \
            "${FAIL_CLOSED_SHA}"; then
            write_failed_closed_deployment false '' || true
            write_failed_closed_state fail_closed_incomplete \
                manual_intervention_required \
                'Backend 与 Interactive Agent 已停止，但无法形成待发布最终状态；未恢复旧路由' \
                || true
            discard_fail_closed_temp
            exit 70
        fi
        if ! write_failed_closed_deployment true "${FAIL_CLOSED_SHA}"; then
            write_failed_closed_state fail_closed_incomplete \
                manual_intervention_required \
                'Backend 与 Interactive Agent 已停止且待发布回执已生成，但 DEPLOYMENT 最终闭包失败；未恢复旧路由' \
                || true
            discard_fail_closed_temp
            exit 70
        fi
        if ! verify_failed_closed_closure "${FAIL_CLOSED_TEMP}"; then
            write_failed_closed_deployment false '' || true
            write_failed_closed_state fail_closed_incomplete \
                manual_intervention_required \
                'Backend 与 Interactive Agent 已停止，但待发布回执与最终状态闭包无效；未恢复旧路由' \
                || true
            discard_fail_closed_temp
            exit 70
        fi
        if ! publish_fail_closed_evidence; then
            write_failed_closed_deployment false '' || true
            write_failed_closed_state fail_closed_incomplete \
                manual_intervention_required \
                'Backend 与 Interactive Agent 已停止，但 create-only 回执未能发布；未恢复旧路由' \
                || true
            discard_fail_closed_temp
            exit 70
        fi
        if ! verify_failed_closed_closure; then
            write_failed_closed_deployment false '' || true
            write_failed_closed_state fail_closed_incomplete \
                manual_intervention_required \
                'Backend 与 Interactive Agent 已停止，但最终 Candidate 与双验证闭包无效；未恢复旧路由' \
                || true
            exit 70
        fi
        printf '生产已失败关闭且未恢复任何旧路由：%s\n' "${RELEASE_ID}"
        ;;
    *)
        error "未知参数：$1"
        exit 2
        ;;
esac
