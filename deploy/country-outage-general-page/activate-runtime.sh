#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly RUNTIME_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
readonly BINDING="${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json"
readonly RELEASE_ID="$(jq -er '.release_id | sub("-backend$"; "")' "${BINDING}")"
readonly UNIFIED_ROOT="/home/bgpdata/Domeye-Core-runtime/unified-releases/${RELEASE_ID}"
readonly CANDIDATE="${UNIFIED_ROOT}/CANDIDATE-MANIFEST.json"
readonly CANARY_EVIDENCE="${UNIFIED_ROOT}/CANARY-VERIFICATION.json"
readonly PRODUCTION_EVIDENCE="${UNIFIED_ROOT}/PRODUCTION-VERIFICATION.json"
readonly STATE="${UNIFIED_ROOT}/ACTIVATION-STATE.json"
readonly DEPLOYMENT="${UNIFIED_ROOT}/DEPLOYMENT.json"
readonly CURRENT_LINK='/home/bgpdata/Domeye-Core-runtime/current'
readonly FRONTEND_WEB_ROOT='/home/bgpdata/Domeye-Core-runtime/web'
readonly FRONTEND_TARGET="${FRONTEND_WEB_ROOT}/dist"
readonly FRONTEND_QUARANTINE_ROOT="${FRONTEND_WEB_ROOT}/quarantine"
readonly FRONTEND_CURRENT='/home/bgpdata/Domeye-Core-runtime/web/state/frontend-current'
readonly NGINX_SITE='/etc/nginx/conf.d/domeye-core.conf'
readonly LOCK='/home/bgpdata/Domeye-Core-runtime/var/country-outage-general-release.lock'
readonly MANAGER="${RUNTIME_ROOT}/deploy/country-outage-general-page/manage-runtime.sh"
readonly VERIFY="${RUNTIME_ROOT}/deploy/country-outage-general-page/verify-runtime.sh"
readonly INTERACTIVE_AGENT_MANAGER="${RUNTIME_ROOT}/deploy/country-outage-agent/p1-chat/manage.sh"
readonly TRUSTED_NODE='/home/bgpdata/.local/node-v22.23.1-linux-x64/bin/node'
# shellcheck source=../lib/artifact-common.sh
source "${RUNTIME_ROOT}/deploy/lib/artifact-common.sh"
# shellcheck source=../lib/frontend-common.sh
source "${RUNTIME_ROOT}/deploy/lib/frontend-common.sh"

error() {
    printf '国家中断通用观测生产激活错误：%s\n' "$*" >&2
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

readonly BASELINE_BACKEND="$(jq -er '.cutover_baseline.backend.path' "${CANDIDATE}")"
readonly BASELINE_BACKEND_RELEASE="$(jq -er '.cutover_baseline.backend.release_id' "${CANDIDATE}")"
readonly BASELINE_FRONTEND_RELEASE="$(jq -er '.cutover_baseline.frontend.release_id' "${CANDIDATE}")"
readonly BASELINE_MANAGER="${BASELINE_BACKEND}/deploy/country-outage-general-page/manage-runtime.sh"
readonly CANDIDATE_BACKEND_RELEASE="$(jq -er '.components.backend.release_id' "${CANDIDATE}")"
readonly CANDIDATE_FRONTEND_RELEASE="$(jq -er '.components.frontend.release_id' "${CANDIDATE}")"
readonly CANDIDATE_FRONTEND_DIST="$(jq -er '.components.frontend.path + "/dist"' "${CANDIDATE}")"
readonly CANDIDATE_FRONTEND_TREE_SHA="$(jq -er '.components.frontend.tree_sha256' "${CANDIDATE}")"
readonly INTERACTIVE_AGENT_RELEASE_ID="$(jq -er '.interactive_agent.release_id' "${CANDIDATE}")"
readonly INTERACTIVE_AGENT_PATH="$(jq -er '.interactive_agent.path' "${CANDIDATE}")"
readonly INTERACTIVE_AGENT_CANDIDATE_ID="$(jq -er '.interactive_agent.candidate_id' "${CANDIDATE}")"
readonly INTERACTIVE_AGENT_ACTIVE_PATH="$(jq -er '.interactive_agent.active_state_path' "${CANDIDATE}")"
readonly INTERACTIVE_AGENT_ACTIVE_SHA="$(jq -er '.interactive_agent.active_state_sha256' "${CANDIDATE}")"
readonly INTERACTIVE_AGENT_RELEASE_MANIFEST_SHA="$(jq -er '.interactive_agent.release_manifest_sha256' "${CANDIDATE}")"
readonly EXPECTED_DATABASE_SHA="$(jq -er '.protected_runtime.database_state_sha256' "${CANDIDATE}")"
readonly EXPECTED_NGINX_MAIN_SHA="$(jq -er '.protected_runtime.nginx_main_sha256' "${CANDIDATE}")"
readonly EXPECTED_NGINX_SITE_SHA="$(jq -er '.protected_runtime.nginx_site_sha256' "${CANDIDATE}")"
FRONTEND_QUARANTINE_PATH=''

atomic_state() {
    local phase="$1"
    local status="$2"
    local detail="$3"
    local temporary="${UNIFIED_ROOT}/.ACTIVATION-STATE.tmp.$$"
    if ! jq -n \
        --arg release_id "${RELEASE_ID}" --arg phase "${phase}" \
        --arg status "${status}" --arg detail "${detail}" \
        --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg backend_release_id "${CANDIDATE_BACKEND_RELEASE}" \
        --arg backend_path "${RUNTIME_ROOT}" \
        --arg frontend_release_id "${CANDIDATE_FRONTEND_RELEASE}" \
        --arg interactive_release_id "${INTERACTIVE_AGENT_RELEASE_ID}" '
      {
        schema_version:"domeye_country_outage_general_activation_v2",
        release_id:$release_id,
        phase:$phase,
        status:$status,
        detail:$detail,
        updated_at:$updated_at,
        candidate:{
          backend:{release_id:$backend_release_id,path:$backend_path},
          frontend:{release_id:$frontend_release_id},
          interactive_agent:{release_id:$interactive_release_id}
        },
        rollback:{mode:"fail_closed",previous_release_id:null}
      }
    ' > "${temporary}"; then
        error '无法生成激活状态临时文件'
        return 1
    fi
    if ! chmod 0600 "${temporary}" \
        || ! mv -T -- "${temporary}" "${STATE}"; then
        unlink "${temporary}" 2>/dev/null || true
        error '无法原子写入激活状态'
        return 1
    fi
}

activate_backend_pointer() {
    local temporary="/home/bgpdata/Domeye-Core-runtime/.current-${RELEASE_ID}.$$"
    [[ "${RUNTIME_ROOT}" == /home/bgpdata/Domeye-Core-runtime/releases/*-backend \
        && -d "${RUNTIME_ROOT}" && ! -L "${RUNTIME_ROOT}" \
        && ! -e "${temporary}" && ! -L "${temporary}" ]] || {
        error '候选 Backend 或临时 current 路径无效'
        return 1
    }
    if ! ln -s "${RUNTIME_ROOT}" "${temporary}" \
        || ! mv -Tf -- "${temporary}" "${CURRENT_LINK}"; then
        unlink "${temporary}" 2>/dev/null || true
        error '无法原子切换 Backend current 到新架构候选'
        return 1
    fi
    [[ "$(readlink -f -- "${CURRENT_LINK}")" == "${RUNTIME_ROOT}" ]] || {
        error 'Backend current 未精确指向候选'
        return 1
    }
}

discard_frontend_staging() {
    local path="$1"
    case "${path}" in
        "/home/bgpdata/Domeye-Core-runtime/web/.dist-cutover-${RELEASE_ID}."*) ;;
        *) error '拒绝清理边界外 Frontend staging'; return 1 ;;
    esac
    if [[ -d "${path}" && ! -L "${path}" ]]; then
        if ! find "${path}" -depth -delete; then
            error "Frontend staging 清理失败，保留供审计：${path}"
            return 1
        fi
    elif [[ -e "${path}" || -L "${path}" ]]; then
        error "Frontend staging 类型异常，保留供审计：${path}"
        return 1
    fi
}

verify_quarantine_not_routed() {
    local quarantine_root="$1"
    local grep_status routing_summary
    [[ -f "${NGINX_SITE}" && ! -L "${NGINX_SITE}" ]] || {
        error 'Nginx site 不是可审计普通文件'
        return 1
    }
    if grep -F "${quarantine_root}" "${NGINX_SITE}" >/dev/null; then
        error 'Nginx 配置引用了 quarantine，不能声明 routed:false'
        return 1
    else
        grep_status=$?
    fi
    (( grep_status == 1 )) || {
        error '无法审计 Nginx 是否引用 quarantine'
        return 1
    }
    if ! routing_summary="$(awk -v expected_root="${FRONTEND_TARGET}" '
      {
        line = $0
        sub(/[[:space:]]*#.*/, "", line)
        if (line ~ /^[[:space:]]*alias[[:space:]]+/) {
          aliases++
        }
        if (line ~ /^[[:space:]]*root[[:space:]]+/) {
          roots++
          sub(/^[[:space:]]*root[[:space:]]+/, "", line)
          sub(/[[:space:]]*;[[:space:]]*$/, "", line)
          if (line == expected_root) {
            expected_roots++
          }
        }
      }
      END {
        printf "%d:%d:%d\n", roots + 0, expected_roots + 0, aliases + 0
      }
    ' "${NGINX_SITE}")"; then
        error '无法审计 Nginx root/alias 路由边界'
        return 1
    fi
    [[ "${routing_summary}" == '1:1:0' ]] || {
        error "Nginx 必须仅以 ${FRONTEND_TARGET} 为唯一 root 且不得配置 alias"
        return 1
    }
}

atomic_frontend_cutover() {
    domeye_frontend_validate_tree "${CANDIDATE_FRONTEND_DIST}" || return 1
    [[ "$(domeye_frontend_tree_sha256 "${CANDIDATE_FRONTEND_DIST}")" \
        == "${CANDIDATE_FRONTEND_TREE_SHA}" ]] || {
        error '候选 Frontend 树摘要漂移'
        return 1
    }
    [[ -d "${FRONTEND_WEB_ROOT}" && ! -L "${FRONTEND_WEB_ROOT}" \
        && "$(readlink -f -- "${FRONTEND_WEB_ROOT}")" \
            == "${FRONTEND_WEB_ROOT}" \
        && -d "${FRONTEND_TARGET}" && ! -L "${FRONTEND_TARGET}" \
        && "$(readlink -f -- "${FRONTEND_TARGET}")" \
            == "${FRONTEND_TARGET}" \
        && -f "${FRONTEND_CURRENT}" && ! -L "${FRONTEND_CURRENT}" ]] || {
        error 'Frontend web/dist 必须是非 symlink 规范实际目录且身份文件有效'
        return 1
    }
    local exchange quarantine_root
    exchange="$(mktemp -d "${FRONTEND_WEB_ROOT}/.dist-cutover-${RELEASE_ID}.XXXXXX")" || {
        error '无法创建 Frontend 实际 staging 目录'
        return 1
    }
    quarantine_root="${FRONTEND_QUARANTINE_ROOT}"
    FRONTEND_QUARANTINE_PATH="${quarantine_root}/${RELEASE_ID}-baseline"
    if ! install -d -m 0750 "${quarantine_root}"; then
        discard_frontend_staging "${exchange}" || return 70
        error '无法创建 Frontend 非路由 quarantine 根'
        return 1
    fi
    [[ -d "${quarantine_root}" && ! -L "${quarantine_root}" \
        && "$(readlink -f -- "${quarantine_root}")" \
            == "${quarantine_root}" ]] || {
        discard_frontend_staging "${exchange}" || return 70
        error 'Frontend quarantine 必须是非 symlink 规范实际目录'
        return 1
    }
    if ! verify_quarantine_not_routed "${quarantine_root}"; then
        discard_frontend_staging "${exchange}" || return 70
        return 1
    fi
    [[ ! -e "${FRONTEND_QUARANTINE_PATH}" \
        && ! -L "${FRONTEND_QUARANTINE_PATH}" ]] || {
        discard_frontend_staging "${exchange}" || return 70
        error 'Frontend create-only quarantine 已存在'
        return 1
    }
    if ! cp -a -- "${CANDIDATE_FRONTEND_DIST}/." "${exchange}/"; then
        discard_frontend_staging "${exchange}" || return 70
        error '无法复制候选 Frontend 到实际 staging 目录'
        return 1
    fi
    if ! find "${exchange}" -type d -exec chmod 0755 {} + \
        || ! find "${exchange}" -type f -exec chmod 0644 {} + \
        || ! domeye_frontend_validate_tree "${exchange}" \
        || [[ "$(domeye_frontend_tree_sha256 "${exchange}")" \
            != "${CANDIDATE_FRONTEND_TREE_SHA}" ]]; then
        discard_frontend_staging "${exchange}" || return 70
        error 'Frontend 实际 staging 树未通过候选摘要校验'
        return 1
    fi
    [[ -d "${exchange}" && ! -L "${exchange}" ]] || {
        error 'Frontend 原子交换 staging 类型无效'
        return 1
    }
    if ! python3 - "${FRONTEND_TARGET}" "${exchange}" <<'PY'
from __future__ import annotations

import ctypes
import os
import sys

current, candidate = (os.fsencode(value) for value in sys.argv[1:])
libc = ctypes.CDLL(None, use_errno=True)
renameat2 = libc.renameat2
renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
renameat2.restype = ctypes.c_int
if renameat2(-100, current, -100, candidate, 2) != 0:
    value = ctypes.get_errno()
    raise OSError(value, os.strerror(value))
PY
    then
        discard_frontend_staging "${exchange}" || return 70
        error 'Frontend renameat2 实际目录原子交换失败'
        return 1
    fi
    [[ -d "${FRONTEND_TARGET}" && ! -L "${FRONTEND_TARGET}" \
        && "$(domeye_frontend_tree_sha256 "${FRONTEND_TARGET}")" \
            == "${CANDIDATE_FRONTEND_TREE_SHA}" ]] || {
        error 'Frontend 原子交换后的公开实际目录不是候选树'
        return 1
    }
    case "${exchange}" in
        "/home/bgpdata/Domeye-Core-runtime/web/.dist-cutover-${RELEASE_ID}."*) ;;
        *) error 'Frontend 退役对象路径越界'; return 1 ;;
    esac
    if [[ ! -d "${exchange}" || -L "${exchange}" ]]; then
        error 'Frontend 原子交换后的退役对象类型无效'
        return 1
    fi
    if ! mv -T -- "${exchange}" "${FRONTEND_QUARANTINE_PATH}"; then
        error '无法把退役 Frontend 实际树移入 create-only 非路由 quarantine'
        return 1
    fi
    [[ -d "${FRONTEND_WEB_ROOT}" && ! -L "${FRONTEND_WEB_ROOT}" \
        && "$(readlink -f -- "${FRONTEND_WEB_ROOT}")" \
            == "${FRONTEND_WEB_ROOT}" \
        && -d "${quarantine_root}" && ! -L "${quarantine_root}" \
        && "$(readlink -f -- "${quarantine_root}")" == "${quarantine_root}" \
        && -d "${FRONTEND_QUARANTINE_PATH}" \
        && ! -L "${FRONTEND_QUARANTINE_PATH}" \
        && "$(readlink -f -- "${FRONTEND_QUARANTINE_PATH}")" \
            == "${FRONTEND_QUARANTINE_PATH}" \
        && -d "${FRONTEND_TARGET}" && ! -L "${FRONTEND_TARGET}" \
        && "$(readlink -f -- "${FRONTEND_TARGET}")" \
            == "${FRONTEND_TARGET}" ]] || {
        error 'Frontend 交换后 web/dist/quarantine 规范目录证据不完整'
        return 1
    }
    if ! verify_quarantine_not_routed "${quarantine_root}"; then
        error 'Frontend 交换后 quarantine 非路由证明失败'
        return 1
    fi

    local current_temporary="/home/bgpdata/Domeye-Core-runtime/web/state/.frontend-current-${RELEASE_ID}.$$"
    [[ ! -e "${current_temporary}" && ! -L "${current_temporary}" ]] || return 1
    if ! printf '%s\n' "${CANDIDATE_FRONTEND_RELEASE}" \
        > "${current_temporary}" \
        || ! chmod 0640 "${current_temporary}" \
        || ! mv -T -- "${current_temporary}" "${FRONTEND_CURRENT}"; then
        unlink "${current_temporary}" 2>/dev/null || true
        error '无法原子更新 Frontend 候选身份'
        return 1
    fi
    [[ "$(<"${FRONTEND_CURRENT}")" == "${CANDIDATE_FRONTEND_RELEASE}" ]] || {
        error 'Frontend current 未绑定候选 release'
        return 1
    }
}

remove_canary_replay_file() {
    local path="$1"
    if [[ -e "${path}" || -L "${path}" ]]; then
        if ! unlink "${path}"; then
            error "无法清理 canary 现场重放临时回执，保留供审计：${path}"
            return 1
        fi
    fi
}

replay_canary_answer() {
    local verifier="${INTERACTIVE_AGENT_PATH}/deployment/verify-release.mjs"
    local source_verifier="${RUNTIME_ROOT}/deploy/country-outage-agent/p1-chat/verify-release.mjs"
    [[ -x "${TRUSTED_NODE}" \
        && -f "${verifier}" && ! -L "${verifier}" \
        && -f "${source_verifier}" && ! -L "${source_verifier}" \
        && -f "${INTERACTIVE_AGENT_ACTIVE_PATH}" \
        && ! -L "${INTERACTIVE_AGENT_ACTIVE_PATH}" ]] || {
        error 'canary 现场重放工具或 active 回执缺失'
        return 1
    }
    if ! cmp -s "${verifier}" "${source_verifier}"; then
        error 'canary release verifier 与 General Source 不一致'
        return 1
    fi
    [[ "sha256:$(sha256sum "${INTERACTIVE_AGENT_PATH}/RELEASE-MANIFEST.json" | awk '{print $1}')" \
        == "${INTERACTIVE_AGENT_RELEASE_MANIFEST_SHA}" \
        && "sha256:$(sha256sum "${INTERACTIVE_AGENT_ACTIVE_PATH}" | awk '{print $1}')" \
            == "${INTERACTIVE_AGENT_ACTIVE_SHA}" ]] || {
        error 'canary 现场重放前 Interactive Agent release/active 摘要漂移'
        return 1
    }

    local frozen_receipt
    frozen_receipt="$(mktemp "${UNIFIED_ROOT}/.canary-promotion-receipt.XXXXXX")" || {
        error '无法创建 canary 现场重放临时文件'
        return 1
    }
    if ! jq -er '.interactive_answer.validation_receipt_body_base64' \
        "${CANARY_EVIDENCE}" | base64 --decode > "${frozen_receipt}"; then
        remove_canary_replay_file "${frozen_receipt}" || return 1
        error 'CANARY 证据中的 verifier 原始回执 base64 无效'
        return 1
    fi
    if ! chmod 0600 "${frozen_receipt}"; then
        remove_canary_replay_file "${frozen_receipt}" || return 1
        error '无法收紧 canary 现场重放临时文件权限'
        return 1
    fi
    local expected_receipt_sha
    if ! expected_receipt_sha="$(jq -er \
        '.interactive_answer.validation_sha256' \
        "${CANARY_EVIDENCE}")"; then
        remove_canary_replay_file "${frozen_receipt}" || return 1
        error 'CANARY 证据缺少 verifier 原始回执摘要'
        return 1
    fi
    [[ "${expected_receipt_sha}" =~ ^sha256:[a-f0-9]{64}$ ]] || {
        remove_canary_replay_file "${frozen_receipt}" || return 1
        error 'CANARY verifier 原始回执摘要格式无效'
        return 1
    }
    local frozen_receipt_hex
    if ! frozen_receipt_hex="$(sha256_hex_file "${frozen_receipt}")"; then
        remove_canary_replay_file "${frozen_receipt}" || return 1
        error '无法计算 CANARY verifier 原始回执摘要'
        return 1
    fi
    if [[ "sha256:${frozen_receipt_hex}" != "${expected_receipt_sha}" ]]; then
        remove_canary_replay_file "${frozen_receipt}" || return 1
        error 'CANARY verifier 原始回执字节摘要漂移'
        return 1
    fi
    if ! jq -e --slurpfile receipt "${frozen_receipt}" '
      .interactive_answer.validation_receipt == $receipt[0]
      and .interactive_answer.response_sha256
        == $receipt[0].backend.response_sha256
      and $receipt[0].result.state == "completed"
      and $receipt[0].result.answer_success == true
      and $receipt[0].result.workflow_completed == true
      and $receipt[0].result.answer_source == "renderer"
      and $receipt[0].result.guard_decision == "pass"
      and $receipt[0].result.public_answer_present == true
      and $receipt[0].result.fallback_or_rejection_present == false
    ' "${CANARY_EVIDENCE}" >/dev/null; then
        remove_canary_replay_file "${frozen_receipt}" || return 1
        error 'CANARY 投影与冻结 verifier 原始回执不一致'
        return 1
    fi
    if ! "${TRUSTED_NODE}" "${verifier}" promotion-receipt \
        "${INTERACTIVE_AGENT_PATH}" "${INTERACTIVE_AGENT_ACTIVE_PATH}" \
        "${frozen_receipt}" >/dev/null; then
        remove_canary_replay_file "${frozen_receipt}" || return 1
        error 'CANARY 原始响应未通过当前 release Guard/Oracle/trace/model 现场重放'
        return 1
    fi
    if ! frozen_receipt_hex="$(sha256_hex_file "${frozen_receipt}")"; then
        error "CANARY 现场重放后无法计算回执摘要，保留供审计：${frozen_receipt}"
        return 1
    fi
    if [[ "sha256:${frozen_receipt_hex}" != "${expected_receipt_sha}" ]]; then
        error "CANARY 现场重放期间回执字节漂移，保留供审计：${frozen_receipt}"
        return 1
    fi
    if ! remove_canary_replay_file "${frozen_receipt}"; then
        return 1
    fi
    local live_status
    if ! live_status="$("${INTERACTIVE_AGENT_MANAGER}" status)"; then
        error 'CANARY 现场重放后 Interactive Agent 组合状态无效'
        return 1
    fi
    if ! jq -e --arg release_id "${INTERACTIVE_AGENT_RELEASE_ID}" \
        --arg candidate_id "${INTERACTIVE_AGENT_CANDIDATE_ID}" '
      .release_id == $release_id
      and .candidate_id == $candidate_id
      and .lifecycle_state == "deployed"
      and .promotion_state == "absent"
      and .production_verified == false
    ' <<<"${live_status}" >/dev/null; then
        error 'CANARY 现场重放后 Interactive Agent 不再是 deployed/未晋级状态'
        return 1
    fi
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

stop_public_backend_fail_closed() {
    local ignored=false
    if ! DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=canary \
        "${MANAGER}" stop >/dev/null 2>&1; then
        ignored=true
    fi
    if ! DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
        "${MANAGER}" stop >/dev/null 2>&1; then
        ignored=true
    fi
    if [[ -x "${BASELINE_MANAGER}" ]]; then
        if ! DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
            "${BASELINE_MANAGER}" stop >/dev/null 2>&1; then
            ignored=true
        fi
    fi
    if ! canary_backend_is_closed; then
        return 1
    fi
    if ! production_backend_is_closed; then
        return 1
    fi
    if [[ "${ignored}" == true ]]; then
        printf 'Backend 停止命令曾返回失败，但 canary/production 会话、端口与路由均已证明关闭\n' >&2
    fi
}

stop_interactive_agent_fail_closed() {
    if ! "${INTERACTIVE_AGENT_MANAGER}" rollback >/dev/null 2>&1; then
        if ! "${INTERACTIVE_AGENT_MANAGER}" stop >/dev/null 2>&1; then
            error 'Interactive Agent manager 无法完成 fail_closed 停止'
            return 1
        fi
    fi
    local listeners
    if ! listeners="$(ss -H -ltn 'sport = :28476')"; then
        error '无法查询 Interactive Agent 监听状态'
        return 1
    fi
    [[ -z "${listeners}" ]] || {
        error 'Interactive Agent 28476 仍有监听者'
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

fail_closed_after_activation_error() {
    local original_exit="$1"
    local backend_closed=true
    local interactive_closed=true
    set +e
    if ! atomic_state fail_closing in_progress \
        '激活未完成，正在停止新旧公共进程；不会恢复旧路由'; then
        error '无法先写入 fail_closing 状态；仍继续停止公共进程'
    fi
    if ! stop_public_backend_fail_closed; then
        backend_closed=false
    fi
    if ! stop_interactive_agent_fail_closed; then
        interactive_closed=false
    fi
    if [[ "${backend_closed}" == true && "${interactive_closed}" == true ]]; then
        if ! atomic_state failed_closed failed_closed \
            '公共 Backend 与 Interactive Agent 已停止；未恢复任何旧路由'; then
            error '进程已失败关闭，但无法写入 failed_closed 状态'
            return 70
        fi
        error '激活失败，已失败关闭；没有发布回答，也没有恢复旧路由'
        (( original_exit == 0 )) && original_exit=1
        return "${original_exit}"
    fi
    if ! atomic_state fail_closed_incomplete manual_intervention_required \
        '无法证明全部公共进程关闭；没有完成发布，需要人工处置'; then
        error '同时无法写入 fail_closed_incomplete 状态'
    fi
    error '激活失败且无法证明完整失败关闭，需要立即人工处置'
    return 70
}

mutation_started=false
activation_complete=false
cleanup() {
    local exit_code=$?
    if [[ "${mutation_started}" == true && "${activation_complete}" != true ]]; then
        trap - EXIT
        fail_closed_after_activation_error "${exit_code}"
        return $?
    fi
    return "${exit_code}"
}

write_deployment() {
    local production_hex production_sha
    if ! production_hex="$(sha256_hex_file "${PRODUCTION_EVIDENCE}")"; then
        error '无法计算 PRODUCTION-VERIFICATION 最终摘要'
        return 1
    fi
    production_sha="sha256:${production_hex}"
    local temporary="${UNIFIED_ROOT}/.DEPLOYMENT.tmp.$$"
    if ! jq -n \
        --arg release_id "${RELEASE_ID}" \
        --arg verified_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg backend_release_id "${CANDIDATE_BACKEND_RELEASE}" \
        --arg backend_path "${RUNTIME_ROOT}" \
        --arg frontend_release_id "${CANDIDATE_FRONTEND_RELEASE}" \
        --arg frontend_path "${FRONTEND_TARGET}" \
        --arg frontend_source_path "${CANDIDATE_FRONTEND_DIST}" \
        --arg frontend_tree_sha "${CANDIDATE_FRONTEND_TREE_SHA}" \
        --arg frontend_quarantine_path "${FRONTEND_QUARANTINE_PATH}" \
        --arg interactive_release_id "${INTERACTIVE_AGENT_RELEASE_ID}" \
        --arg candidate_id "${INTERACTIVE_AGENT_CANDIDATE_ID}" \
        --arg production_sha "${production_sha}" '
      {
        schema_version:"domeye_country_outage_general_deployment_v2",
        release_id:$release_id,
        status:"production_verified",
        production_verified:true,
        verified_at:$verified_at,
        artifacts_rebuilt_during_promotion:false,
        components:{
          backend:{release_id:$backend_release_id,path:$backend_path},
          frontend:{
            release_id:$frontend_release_id,
            path:$frontend_path,
            source_artifact_path:$frontend_source_path,
            tree_sha256:$frontend_tree_sha
          },
          interactive_agent:{release_id:$interactive_release_id,candidate_id:$candidate_id}
        },
        cutover_quarantine:{
          path:$frontend_quarantine_path,
          canonical_actual_directory:true,
          nginx_reference_present:false,
          routed:false,
          automatic_restore:false
        },
        verification:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_sha},
        rollback:{mode:"fail_closed",previous_release_id:null,available:false}
      }
    ' > "${temporary}"; then
        error '无法生成 production_verified 部署回执'
        return 1
    fi
    chmod 0640 "${temporary}" || return 1
    if ! mv -n -- "${temporary}" "${DEPLOYMENT}"; then
        error '无法原子写入 create-only 部署回执'
        return 1
    fi
    [[ ! -e "${temporary}" && ! -L "${temporary}" \
        && -f "${DEPLOYMENT}" && ! -L "${DEPLOYMENT}" ]] || {
        error 'DEPLOYMENT 已存在或原子写入未闭合'
        return 1
    }
}

if (( EUID != 0 )); then
    error '生产激活必须由 root 执行'
    exit 1
fi
if [[ "${CONFIRM_RELEASE_ID:-}" != "${RELEASE_ID}" ]]; then
    error 'CONFIRM_RELEASE_ID 必须与 release-id 完全一致'
    exit 2
fi
for command_name in awk base64 chmod cmp cp curl date find flock grep install jq \
    ln mktemp mv nginx python3 readlink screen sha256sum ss unlink; do
    command -v "${command_name}" >/dev/null 2>&1 || {
        error "缺少命令：${command_name}"
        exit 1
    }
done
[[ -f "${CANDIDATE}" && ! -L "${CANDIDATE}" \
    && -f "${CANARY_EVIDENCE}" && ! -L "${CANARY_EVIDENCE}" \
    && -x "${MANAGER}" && -x "${VERIFY}" \
    && -x "${INTERACTIVE_AGENT_MANAGER}" \
    && -x "${BASELINE_MANAGER}" ]] || {
    error '候选、canary 证据或新旧停止工具缺失'
    exit 1
}
if ! jq -e --arg release_id "${RELEASE_ID}" \
    --arg backend_path "${RUNTIME_ROOT}" '
  .schema_version == "domeye_country_outage_general_release_candidate_v2"
  and .release_id == $release_id
  and .status == "built"
  and .source.annotated_tag == $release_id
  and .components.backend.path == $backend_path
  and .promotion_contract.candidate_canary_production_same_artifacts == true
  and .promotion_contract.rebuild_allowed == false
  and .protected_runtime.database_changed == false
  and .protected_runtime.nginx_changed == false
  and .rollback == {mode:"fail_closed",previous_release_id:null}
  and .interactive_agent.release_id == $release_id
  and .interactive_agent.endpoint == {
    url:"http://127.0.0.1:28476",
    host:"127.0.0.1",
    port:28476,
    base_path:"/country-outage/chat"
  }
' "${CANDIDATE}" >/dev/null; then
    error '统一候选不是新架构首发 fail_closed 合同'
    exit 1
fi
if ! jq -e --arg release_id "${INTERACTIVE_AGENT_RELEASE_ID}" '
  .schema_version == "domeye_interactive_agent_release_manifest_v1"
  and .release_id == $release_id
  and .rollback == {mode:"fail_closed",previous_release_id:null}
' "${INTERACTIVE_AGENT_PATH}/RELEASE-MANIFEST.json" >/dev/null; then
    error 'Interactive Agent 不是首个 fail_closed release'
    exit 1
fi
if ! jq -e --arg release_id "${RELEASE_ID}" \
    --arg interactive_release_id "${INTERACTIVE_AGENT_RELEASE_ID}" \
    --arg candidate_id "${INTERACTIVE_AGENT_CANDIDATE_ID}" '
  .status == "canary_verified"
  and .mode == "canary"
  and .release_id == $release_id
  and .interactive_answer.status == "canary_verified"
  and .interactive_answer.base_url == "http://127.0.0.1:38672"
  and .interactive_answer.release_id == $interactive_release_id
  and .interactive_answer.candidate_id == $candidate_id
  and (.interactive_answer.conversation_id | test("^conversation_sha256_[a-f0-9]{64}$"))
  and (.interactive_answer.turn_id | test("^turn_sha256_[a-f0-9]{64}$"))
  and (.interactive_answer.response_sha256 | test("^sha256:[a-f0-9]{64}$"))
  and (.interactive_answer.validation_sha256 | test("^sha256:[a-f0-9]{64}$"))
  and (.interactive_answer.validation_receipt_body_base64 | type == "string" and length > 0)
  and .interactive_answer.answer_source == "renderer"
  and .interactive_answer.guard_decision == "pass"
  and .interactive_answer.public_answer_present == true
  and .interactive_answer.fallback_or_rejection_present == false
  and .interactive_answer.validation.state == "completed"
  and .interactive_answer.validation.answer_success == true
  and .interactive_answer.validation.workflow_completed == true
  and .interactive_answer.validation.answer_source == "renderer"
  and .interactive_answer.validation.guard_decision == "pass"
  and .interactive_answer.validation.public_answer_present == true
  and .interactive_answer.validation.fallback_or_rejection_present == false
' "${CANARY_EVIDENCE}" >/dev/null; then
    error 'CANARY-VERIFICATION.json 未证明本次 correct direct Renderer + Guard 回答'
    exit 1
fi
if [[ -e "${STATE}" || -L "${STATE}" \
    || -e "${DEPLOYMENT}" || -L "${DEPLOYMENT}" \
    || -e "${PRODUCTION_EVIDENCE}" || -L "${PRODUCTION_EVIDENCE}" ]]; then
    error '激活、部署或生产验证证据已存在，拒绝重复激活'
    exit 1
fi
exec 9>"${LOCK}"
flock -n 9 || {
    error '已有激活或 fail_closed 操作正在执行'
    exit 1
}
[[ "$(readlink -f -- "${CURRENT_LINK}")" == "${BASELINE_BACKEND}" \
    && "$(jq -er '.release_id' "${BASELINE_BACKEND}/BACKEND-SOURCE-BINDING.json")" \
        == "${BASELINE_BACKEND_RELEASE}" ]] || {
    error '当前 Backend 与 cutover_baseline 观测身份不一致'
    exit 1
}
[[ "$(<"${FRONTEND_CURRENT}")" == "${BASELINE_FRONTEND_RELEASE}" ]] || {
    error '当前 Frontend 与 cutover_baseline 观测身份不一致'
    exit 1
}
[[ "$(sha256sum /home/bgpdata/Domeye-Core-dev-data/state.json | awk '{print $1}')" \
    == "${EXPECTED_DATABASE_SHA}" \
    && "$(sha256sum /etc/nginx/nginx.conf | awk '{print $1}')" \
        == "${EXPECTED_NGINX_MAIN_SHA}" \
    && "$(sha256sum "${NGINX_SITE}" | awk '{print $1}')" \
        == "${EXPECTED_NGINX_SITE_SHA}" ]] || {
    error '数据库或 Nginx 摘要相对候选发生变化'
    exit 1
}
if ! nginx -t >/dev/null; then
    error 'Nginx 配置校验失败'
    exit 1
fi
if ! DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=canary \
    "${MANAGER}" status >/dev/null; then
    error 'canary_verified 证据对应的候选 Backend 已不在运行'
    exit 1
fi
if ! replay_canary_answer; then
    error '切换前 CANARY 正确回答现场重放失败'
    exit 1
fi
if ! DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
    "${BASELINE_MANAGER}" status >/dev/null; then
    error 'cutover_baseline Backend 无法验证，拒绝切换'
    exit 1
fi
if ! curl -fsS --max-time 10 \
    http://127.0.0.1:28471/api/v1/healthz >/dev/null; then
    error '切换前公共 Backend 不健康，拒绝误判当前身份'
    exit 1
fi

if ! atomic_state prepared passed 'canary 已正确回答，等待单向生产切换'; then
    exit 1
fi
trap cleanup EXIT
mutation_started=true

if ! DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=canary \
    "${MANAGER}" stop; then
    error '无法停止 canary Backend'
    exit 1
fi
if ! canary_backend_is_closed; then
    error 'canary stop 后未证明 38672 与对应 Screen 会话关闭'
    exit 1
fi
if ! atomic_state retiring_baseline in_progress '仅停止 cutover_baseline；不保留恢复路径'; then
    exit 1
fi
if ! DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
    "${BASELINE_MANAGER}" stop; then
    error '无法安全停止 cutover_baseline Backend'
    exit 1
fi
if ! production_backend_is_closed; then
    error 'cutover_baseline 停止后公共路由未失败关闭'
    exit 1
fi

if ! atomic_state switching_backend in_progress '正在把唯一公共 Backend 指针切到候选'; then
    exit 1
fi
if ! activate_backend_pointer; then
    exit 1
fi
if ! DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
    "${MANAGER}" start; then
    error '候选 Backend 启动失败'
    exit 1
fi
if ! DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE=production \
    "${MANAGER}" status >/dev/null; then
    error '候选 Backend 运行身份无效'
    exit 1
fi

if ! atomic_state switching_frontend in_progress '正在原子切换候选 Frontend；不生成旧前端恢复路径'; then
    exit 1
fi
if ! atomic_frontend_cutover; then
    exit 1
fi
if ! curl -fsS --max-time 10 http://127.0.0.1:28471/ \
    | grep -F '<div id="app"></div>' >/dev/null; then
    error '候选 Frontend 公开页面校验失败'
    exit 1
fi

if ! atomic_state verifying_production in_progress '等待公共固定问题 Renderer + Guard 正确回答'; then
    exit 1
fi
if ! "${VERIFY}" production; then
    error '公共生产验证失败；拒绝把拒绝、回退或 provider failure 计为完成'
    exit 1
fi
if ! jq -e --arg release_id "${RELEASE_ID}" '
  .release_id == $release_id
  and .mode == "production"
  and .status == "production_verified"
  and .interactive_answer.status == "production_verified"
  and .interactive_answer.production_verified == true
  and .interactive_answer.answer_source == "renderer"
  and .interactive_answer.guard_decision == "pass"
  and .interactive_answer.public_answer_present == true
  and .interactive_answer.fallback_or_rejection_present == false
' "${PRODUCTION_EVIDENCE}" >/dev/null; then
    error 'PRODUCTION-VERIFICATION.json 未证明正确公共回答'
    exit 1
fi
if ! atomic_state production_verified passed '公共 Backend 固定问题已正确回答并通过 Guard/Oracle/trace/model 重放'; then
    exit 1
fi
if ! write_deployment; then
    exit 1
fi
activation_complete=true
trap - EXIT
printf '国家中断通用观测生产激活并验证成功：%s\n' "${RELEASE_ID}"
