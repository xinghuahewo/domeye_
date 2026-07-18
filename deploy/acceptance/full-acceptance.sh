#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly DEPLOY_DIR="${SCRIPT_DIR}/.."
# shellcheck source=../lib/artifact-common.sh
source "${DEPLOY_DIR}/lib/artifact-common.sh"
# shellcheck source=../lib/backend-common.sh
source "${DEPLOY_DIR}/lib/backend-common.sh"
# shellcheck source=../lib/database-common.sh
source "${DEPLOY_DIR}/lib/database-common.sh"
# shellcheck source=../lib/frontend-common.sh
source "${DEPLOY_DIR}/lib/frontend-common.sh"

if (( $# < 2 || $# > 3 )); then
    printf '用法：%s <发布目录> <待隐藏旧目录> [数据库配置]\n' "${0##*/}" >&2
    exit 2
fi

readonly RELEASE_DIR="${1%/}"
readonly HIDDEN_PATH="$2"
readonly DATABASE_ENV_FILE="${3:-/home/bgpdata/Domeye-Core-data/config/database.env}"
readonly PROJECT_ROOT='/home/bgpdata/Domeye-Core'
readonly MANIFEST_PATH="${RELEASE_DIR}/${DOMEYE_CORE_RELEASE_MANIFEST}"
readonly NGINX_SOURCE="${PROJECT_ROOT}/deploy/nginx/domeye-core.conf"
readonly NGINX_TARGET='/etc/nginx/conf.d/domeye-core.conf'
readonly INFO_CURRENT_STATE="${DOMEYE_CORE_RELEASE_STATE_DIR}/info-current"
readonly INFO_ROLLBACK_JOURNAL="${DOMEYE_CORE_RELEASE_STATE_DIR}/info-rollback.json"
readonly DATABASE_CURRENT_STATE="${DOMEYE_CORE_RELEASE_STATE_DIR}/database-current"
readonly DATABASE_ROLLBACK_JOURNAL="${DOMEYE_CORE_RELEASE_STATE_DIR}/database-rollback.json"
readonly FRONTEND_CURRENT_STATE="${DOMEYE_CORE_FRONTEND_CURRENT_STATE}"
readonly FRONTEND_ROLLBACK_JOURNAL="${DOMEYE_CORE_FRONTEND_ROLLBACK_JOURNAL}"
readonly FRONTEND_INSTALL_STATUS="${DOMEYE_CORE_FRONTEND_INSTALL_STATUS}"

for command_name in awk chmod find jq mktemp nginx ps readlink screen sha256sum sort systemctl tr; do
    domeye_artifact_require_command "${command_name}"
done
if [[ ! -x "${DOMEYE_CORE_NODE_BIN_DIR}/node" || ! -x "${DOMEYE_CORE_NODE_BIN_DIR}/npm" ]]; then
    domeye_artifact_error "缺少项目隔离的 Node.js 22.23.1：${DOMEYE_CORE_NODE_BIN_DIR}"
    exit 1
fi
if [[ "$("${DOMEYE_CORE_NODE_BIN_DIR}/node" --version)" != 'v22.23.1' ]]; then
    domeye_artifact_error '前端验收必须使用固定 Node.js v22.23.1'
    exit 1
fi

nginx_backup=''
nginx_change_attempted=false
production_activated=false
activation_started=false
info_installed=false
info_install_attempted=false
frontend_installed=false
frontend_install_attempted=false
backend_stopped_for_switch=false
backend_stop_attempted=false
production_backend_was_running=false
previous_runtime_info_dir=''
acceptance_complete=false
rollback_failed=false
declare -a rollback_failures=()
release_id=''
nginx_was_active=false
nginx_target_existed=false
if systemctl is-active --quiet nginx; then
    nginx_was_active=true
fi

run_rollback_step() {
    local label="$1"
    shift
    local step_rc

    if "$@"; then
        printf '自动回滚步骤完成：%s\n' "${label}" >&2
        return 0
    else
        step_rc=$?
        rollback_failed=true
        rollback_failures+=("${label}(exit=${step_rc})")
        domeye_artifact_error "自动回滚步骤失败：${label}（退出码 ${step_rc}）"
        return "${step_rc}"
    fi
}

info_release_committed() {
    [[ -n "${release_id}" ]] || return 1
    [[ -f "${INFO_CURRENT_STATE}" && ! -L "${INFO_CURRENT_STATE}" ]] || return 1
    [[ "$(<"${INFO_CURRENT_STATE}")" == "${release_id}" ]] || return 1
    [[ -f "${INFO_ROLLBACK_JOURNAL}" && ! -L "${INFO_ROLLBACK_JOURNAL}" ]] || return 1
    jq -e \
        --arg release_id "${release_id}" \
        '.release_id == $release_id and .rollback_available == true' \
        "${INFO_ROLLBACK_JOURNAL}" >/dev/null 2>&1
}

info_target_matches_release() {
    local info_manifest="${RELEASE_DIR}/${DOMEYE_CORE_INFO_MANIFEST}"
    local file_name expected_sha

    [[ -n "${release_id}" ]] || return 1
    [[ -f "${info_manifest}" && ! -L "${info_manifest}" ]] || return 1
    [[ -d "${DOMEYE_CORE_DEFAULT_INFO_TARGET}" \
        && ! -L "${DOMEYE_CORE_DEFAULT_INFO_TARGET}" ]] || return 1
    for file_name in "${DOMEYE_CORE_INFO_FILES[@]}"; do
        [[ -f "${DOMEYE_CORE_DEFAULT_INFO_TARGET}/${file_name}" \
            && ! -L "${DOMEYE_CORE_DEFAULT_INFO_TARGET}/${file_name}" ]] || return 1
        expected_sha="$(jq -r \
            --arg name "${file_name}" \
            '.files[] | select(.name == $name) | .sha256' \
            "${info_manifest}")"
        [[ "${expected_sha}" =~ ^[0-9a-f]{64}$ ]] || return 1
        [[ "$(sha256sum "${DOMEYE_CORE_DEFAULT_INFO_TARGET}/${file_name}" | awk '{print $1}')" \
            == "${expected_sha}" ]] || return 1
    done
}

info_release_partially_visible() {
    [[ -n "${release_id}" ]] || return 1
    if [[ ! -d "${DOMEYE_CORE_DEFAULT_INFO_TARGET}" \
        || -L "${DOMEYE_CORE_DEFAULT_INFO_TARGET}" ]]; then
        return 0
    fi
    if [[ -f "${INFO_CURRENT_STATE}" && ! -L "${INFO_CURRENT_STATE}" \
        && "$(<"${INFO_CURRENT_STATE}")" == "${release_id}" ]]; then
        return 0
    fi
    if [[ -f "${INFO_ROLLBACK_JOURNAL}" && ! -L "${INFO_ROLLBACK_JOURNAL}" ]] \
        && jq -e --arg release_id "${release_id}" \
            '.release_id == $release_id' \
            "${INFO_ROLLBACK_JOURNAL}" >/dev/null 2>&1; then
        return 0
    fi
    info_target_matches_release
}

frontend_release_committed() {
    local expected_tree_sha256

    [[ -n "${release_id}" ]] || return 1
    [[ -f "${FRONTEND_CURRENT_STATE}" && ! -L "${FRONTEND_CURRENT_STATE}" ]] || return 1
    [[ "$(<"${FRONTEND_CURRENT_STATE}")" == "${release_id}" ]] || return 1
    [[ -f "${FRONTEND_ROLLBACK_JOURNAL}" \
        && ! -L "${FRONTEND_ROLLBACK_JOURNAL}" ]] || return 1
    jq -e \
        --arg release_id "${release_id}" \
        --arg target_dir "${DOMEYE_CORE_FRONTEND_TARGET}" \
        '.release_id == $release_id
         and .target_dir == $target_dir
         and .rollback_available == true
         and (.tree_sha256 | type) == "string"
         and (.tree_sha256 | test("^[0-9a-f]{64}$"))' \
        "${FRONTEND_ROLLBACK_JOURNAL}" >/dev/null 2>&1 || return 1
    expected_tree_sha256="$(jq -r '.tree_sha256' "${FRONTEND_ROLLBACK_JOURNAL}")"
    domeye_frontend_validate_tree "${DOMEYE_CORE_FRONTEND_TARGET}" >/dev/null 2>&1 \
        || return 1
    [[ "$(domeye_frontend_tree_sha256 "${DOMEYE_CORE_FRONTEND_TARGET}")" \
        == "${expected_tree_sha256}" ]]
}

frontend_install_status_matches_release() {
    [[ -n "${release_id}" ]] || return 1
    [[ -f "${FRONTEND_INSTALL_STATUS}" && ! -L "${FRONTEND_INSTALL_STATUS}" ]] \
        || return 1
    jq -e \
        --arg release_id "${release_id}" \
        --arg target "${DOMEYE_CORE_FRONTEND_TARGET}" \
        '(.phase == "prepared" or .phase == "restored")
         and .release_id == $release_id
         and .target == $target' \
        "${FRONTEND_INSTALL_STATUS}" >/dev/null 2>&1
}

frontend_release_partially_visible() {
    [[ -n "${release_id}" ]] || return 1
    if [[ -f "${FRONTEND_CURRENT_STATE}" && ! -L "${FRONTEND_CURRENT_STATE}" \
        && "$(<"${FRONTEND_CURRENT_STATE}")" == "${release_id}" ]]; then
        return 0
    fi
    if [[ -f "${FRONTEND_ROLLBACK_JOURNAL}" \
        && ! -L "${FRONTEND_ROLLBACK_JOURNAL}" ]] \
        && jq -e --arg release_id "${release_id}" \
            '.release_id == $release_id' \
            "${FRONTEND_ROLLBACK_JOURNAL}" >/dev/null 2>&1; then
        return 0
    fi
    if [[ -e "${FRONTEND_INSTALL_STATUS}" || -L "${FRONTEND_INSTALL_STATUS}" ]]; then
        return 0
    fi
    return 1
}

database_release_committed() {
    local expected_target

    [[ -n "${release_id}" ]] || return 1
    [[ -f "${DATABASE_CURRENT_STATE}" && ! -L "${DATABASE_CURRENT_STATE}" ]] || return 1
    [[ "$(<"${DATABASE_CURRENT_STATE}")" == "${release_id}" ]] || return 1
    [[ -f "${DATABASE_ROLLBACK_JOURNAL}" && ! -L "${DATABASE_ROLLBACK_JOURNAL}" ]] || return 1
    jq -e \
        --arg release_id "${release_id}" \
        '.release_id == $release_id and .rollback_available == true' \
        "${DATABASE_ROLLBACK_JOURNAL}" >/dev/null 2>&1 || return 1
    [[ -L "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}" ]] || return 1
    expected_target="${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/${release_id}/postgres"
    [[ "$(readlink -f "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}")" == "${expected_target}" ]]
}

activation_internal_rollback_is_safe() {
    local activation_status

    [[ -n "${release_id}" ]] || return 1
    activation_status="${DOMEYE_CORE_RELEASE_STATE_DIR}/activation-rollback-status-${release_id}.json"
    if [[ ! -e "${activation_status}" && ! -L "${activation_status}" ]]; then
        return 0
    fi
    [[ -f "${activation_status}" && ! -L "${activation_status}" ]] || return 1
    jq -e \
        --arg release_id "${release_id}" \
        '.schema_version == 1
         and .release_id == $release_id
         and ((.phase == "prepared"
               and .changes_started == false
               and .rollback_ok == null)
              or (.phase == "rollback_complete"
                  and .changes_started == true
                  and .rollback_ok == true))' \
        "${activation_status}" >/dev/null 2>&1
}

mapfile -t initial_backend_sessions < <(domeye_core_list_backend_sessions)
if (( ${#initial_backend_sessions[@]} > 1 )); then
    domeye_artifact_error "发现多个 Domeye Core 后端会话：${initial_backend_sessions[*]}"
    exit 1
elif (( ${#initial_backend_sessions[@]} == 1 )); then
    production_backend_was_running=true
    if ! previous_runtime_info_dir="$(domeye_core_capture_backend_info_dir)"; then
        domeye_artifact_error '无法从切换前 Domeye Core 后端进程安全捕获实际 INFO_DIR'
        exit 1
    fi
fi

if [[ ! -e "${DOMEYE_CORE_RELEASE_STATE_DIR}/database-current" \
    && ! -L "${DOMEYE_CORE_RELEASE_STATE_DIR}/database-current" \
    && ! -e "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}" \
    && ! -L "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}" ]]; then
    preflight_db_host="$(domeye_core_backend_env_value DB_HOST)"
    preflight_db_port="$(domeye_core_backend_env_value DB_PORT)"
    if [[ "${preflight_db_host}" != "${DOMEYE_CORE_BACKEND_DB_HOST}" \
        || "${preflight_db_port}" != "${DOMEYE_CORE_BACKEND_DB_PORT}" ]]; then
        preflight_info_dir="${previous_runtime_info_dir:-$(domeye_core_backend_env_value INFO_DIR)}"
        domeye_core_write_source_rollback_state \
            "${preflight_info_dir}" \
            'full-acceptance-preflight'
    fi
fi
readonly ACCEPTANCE_WORK_DIR="$(mktemp -d /tmp/domeye-core-full-acceptance.XXXXXX)"
readonly CANDIDATE_FRONTEND_DIST="${ACCEPTANCE_WORK_DIR}/frontend-dist"
chmod 0755 "${ACCEPTANCE_WORK_DIR}"
rollback_full_acceptance() {
    local original_exit_code=$?
    local final_exit_code="${original_exit_code}"
    local nginx_restore_ready=true
    local backend_restart_allowed=true
    local activation_state_uncertain=false
    local info_state_uncertain=false
    local frontend_state_uncertain=false
    local info_rollback_ok=true
    local frontend_rollback_ok=true
    local database_rollback_ok=true
    local backend_confirmed_stopped=false
    local restart_backend_after_rollback=false
    local nginx_rollback_ok=true

    trap - EXIT
    set +e
    if [[ "${acceptance_complete}" != true ]]; then
        if [[ "${frontend_install_attempted}" == true \
            && "${frontend_installed}" != true ]]; then
            if frontend_release_committed; then
                frontend_installed=true
                printf '检测到前端构建已完成持久切换，将按发布日志回滚。\n' >&2
            elif frontend_release_partially_visible; then
                if frontend_install_status_matches_release; then
                    if run_rollback_step \
                        '恢复未完成的前端构建切换' \
                        "${DEPLOY_DIR}/artifacts/rollback-frontend-build.sh"; then
                        printf '未完成的前端构建切换已按 prepared 状态收敛。\n' >&2
                    else
                        frontend_state_uncertain=true
                        frontend_rollback_ok=false
                        backend_restart_allowed=false
                    fi
                else
                    frontend_state_uncertain=true
                    frontend_rollback_ok=false
                    backend_restart_allowed=false
                    run_rollback_step '确认前端构建未发生不完整切换' false
                fi
            fi
        fi
        if [[ "${info_install_attempted}" == true && "${info_installed}" != true ]]; then
            if info_release_committed; then
                info_installed=true
                printf '检测到信息制品已完成持久切换，将按发布日志回滚。\n' >&2
            elif info_release_partially_visible; then
                info_state_uncertain=true
                backend_restart_allowed=false
                run_rollback_step '确认信息制品未发生不完整切换' false
            fi
        fi
        if [[ "${activation_started}" == true && "${production_activated}" != true ]]; then
            if database_release_committed; then
                production_activated=true
                printf '检测到数据库已完成持久切换，将按发布日志回滚。\n' >&2
            elif ! activation_internal_rollback_is_safe; then
                activation_state_uncertain=true
                backend_restart_allowed=false
                run_rollback_step '确认数据库激活已完整回滚' false
            fi
        fi
        if [[ "${production_activated}" == true \
            || "${activation_started}" == true \
            || "${info_installed}" == true \
            || "${frontend_installed}" == true ]]; then
            if run_rollback_step '停止切换过程中的后端' "${DEPLOY_DIR}/stop-backend.sh"; then
                backend_confirmed_stopped=true
            else
                backend_restart_allowed=false
            fi
        fi
        if [[ "${frontend_installed}" == true ]]; then
            if ! run_rollback_step \
                '恢复切换前前端构建' \
                "${DEPLOY_DIR}/artifacts/rollback-frontend-build.sh"; then
                backend_restart_allowed=false
                frontend_rollback_ok=false
            fi
        fi
        if [[ "${info_installed}" == true ]]; then
            if ! run_rollback_step \
                '恢复切换前信息目录' \
                "${DEPLOY_DIR}/artifacts/rollback-info-artifact.sh"; then
                backend_restart_allowed=false
                info_rollback_ok=false
            fi
        fi
        if [[ "${production_activated}" == true ]]; then
            if ! run_rollback_step \
                '恢复切换前数据库与后端配置' \
                env \
                DOMEYE_CORE_DEFER_BACKEND_RESTART_ON_ROLLBACK=true \
                "${DEPLOY_DIR}/database/rollback-database.sh" \
                "${DATABASE_ENV_FILE}"; then
                backend_restart_allowed=false
                database_rollback_ok=false
            fi
            if [[ "${production_backend_was_running}" == true \
                && "${backend_restart_allowed}" == true \
                && "${frontend_rollback_ok}" == true \
                && "${info_rollback_ok}" == true \
                && "${database_rollback_ok}" == true \
                && "${backend_confirmed_stopped}" == true ]]; then
                restart_backend_after_rollback=true
            elif [[ "${production_backend_was_running}" != true ]]; then
                if ! run_rollback_step \
                    '恢复切换前后端停止状态' \
                    "${DEPLOY_DIR}/stop-backend.sh"; then
                    backend_restart_allowed=false
                fi
            fi
        elif [[ "${activation_started}" == true \
            || "${backend_stop_attempted}" == true \
            || "${backend_stopped_for_switch}" == true ]]; then
            if run_rollback_step '清理未完成切换的后端' "${DEPLOY_DIR}/stop-backend.sh"; then
                backend_confirmed_stopped=true
            else
                backend_restart_allowed=false
            fi
            if [[ "${activation_state_uncertain}" == true ]]; then
                run_rollback_step \
                    '停止状态不确定的独立数据库' \
                    "${DEPLOY_DIR}/database/dbctl.sh" down "${DATABASE_ENV_FILE}"
            fi
            if [[ "${production_backend_was_running}" == true \
                && "${backend_restart_allowed}" == true \
                && "${backend_confirmed_stopped}" == true ]]; then
                restart_backend_after_rollback=true
            fi
        fi
        if [[ "${nginx_change_attempted}" == true ]]; then
            if [[ "${nginx_target_existed}" == true ]]; then
                if [[ -n "${nginx_backup}" && -f "${nginx_backup}" && ! -L "${nginx_backup}" ]]; then
                    run_rollback_step \
                        '恢复切换前 Nginx 配置文件' \
                        install -m 0644 "${nginx_backup}" "${NGINX_TARGET}" \
                        || { nginx_restore_ready=false; nginx_rollback_ok=false; }
                else
                    run_rollback_step '恢复切换前 Nginx 配置文件（备份缺失）' false
                    nginx_restore_ready=false
                    nginx_rollback_ok=false
                fi
            elif [[ -e "${NGINX_TARGET}" || -L "${NGINX_TARGET}" ]]; then
                if [[ -f "${NGINX_TARGET}" && ! -L "${NGINX_TARGET}" ]]; then
                    run_rollback_step \
                        '移除新安装的 Nginx 配置文件' \
                        rm -f -- "${NGINX_TARGET}" \
                        || { nginx_restore_ready=false; nginx_rollback_ok=false; }
                else
                    run_rollback_step '移除新安装的 Nginx 配置文件（目标异常）' false
                    nginx_restore_ready=false
                    nginx_rollback_ok=false
                fi
            fi
            if [[ "${nginx_was_active}" != true ]]; then
                if ! run_rollback_step '恢复切换前 Nginx 停止状态' systemctl stop nginx; then
                    nginx_rollback_ok=false
                fi
            elif [[ "${nginx_restore_ready}" == true ]]; then
                if run_rollback_step '校验恢复后的 Nginx 配置' nginx -t; then
                    if ! run_rollback_step '重新加载恢复后的 Nginx 配置' systemctl reload nginx; then
                        nginx_rollback_ok=false
                    fi
                else
                    nginx_rollback_ok=false
                fi
            else
                domeye_artifact_error '跳过 Nginx 校验和服务状态恢复：配置文件恢复步骤失败'
                nginx_rollback_ok=false
            fi
        fi
        if [[ "${restart_backend_after_rollback}" == true \
            && "${backend_restart_allowed}" == true \
            && "${frontend_rollback_ok}" == true \
            && "${nginx_rollback_ok}" == true \
            && "${backend_confirmed_stopped}" == true ]]; then
            if ! run_rollback_step \
                '恢复切换前后端' \
                env \
                DOMEYE_CORE_ALLOW_ROLLBACK_CONFIG=true \
                DOMEYE_CORE_ROLLBACK_INFO_DIR="${previous_runtime_info_dir}" \
                "${DEPLOY_DIR}/start-backend.sh"; then
                backend_restart_allowed=false
            fi
        fi
        if [[ "${backend_restart_allowed}" != true \
            || "${activation_state_uncertain}" == true \
            || "${info_state_uncertain}" == true \
            || "${frontend_state_uncertain}" == true \
            || "${frontend_rollback_ok}" != true \
            || "${nginx_rollback_ok}" != true ]]; then
            run_rollback_step \
                '在回滚状态不确定时保持后端停止' \
                "${DEPLOY_DIR}/stop-backend.sh"
        fi
    fi
    if [[ "${ACCEPTANCE_WORK_DIR}" == /tmp/domeye-core-full-acceptance.* && -d "${ACCEPTANCE_WORK_DIR}" ]]; then
        if [[ "${acceptance_complete}" == true ]]; then
            if ! rm -rf -- "${ACCEPTANCE_WORK_DIR}"; then
                domeye_artifact_error \
                    "独立部署已通过，但验收临时目录清理失败，请人工删除：${ACCEPTANCE_WORK_DIR}"
            fi
        else
            run_rollback_step '清理完整验收临时目录' rm -rf -- "${ACCEPTANCE_WORK_DIR}"
        fi
    fi
    if [[ "${rollback_failed}" == true ]]; then
        final_exit_code=1
        domeye_artifact_error \
            "自动回滚总状态：失败；原始退出码=${original_exit_code}；失败步骤=${rollback_failures[*]}"
    elif [[ "${acceptance_complete}" != true ]]; then
        printf '自动回滚总状态：成功；原始退出码=%s\n' "${original_exit_code}" >&2
    fi
    exit "${final_exit_code}"
}
trap rollback_full_acceptance EXIT

"${DEPLOY_DIR}/artifacts/verify-release.sh" "${RELEASE_DIR}"
release_id="$(jq -er '.release_id' "${MANIFEST_PATH}")"
domeye_artifact_validate_release_id "${release_id}"
if frontend_release_committed; then
    domeye_artifact_error \
        "前端 release-id 已处于活动状态，拒绝把旧提交误认成本次安装：${release_id}"
    exit 1
fi
if frontend_release_partially_visible; then
    domeye_artifact_error \
        '发现与本次 release-id 冲突或尚未收敛的前端安装状态，请先人工复核/回滚'
    exit 1
fi
"${DEPLOY_DIR}/database/restore-database.sh" "${RELEASE_DIR}" "${DATABASE_ENV_FILE}"

(
    cd -- "${PROJECT_ROOT}/backend"
    /home/bgpdata/.local/bin/uv sync --frozen
    /home/bgpdata/.local/bin/uv run --frozen pytest
    sha256sum -c core.sha256
)
(
    cd -- "${PROJECT_ROOT}/frontend"
    export PATH="${DOMEYE_CORE_RUNTIME_PATH}"
    [[ "$(node --version)" == 'v22.23.1' ]]
    npm ci
    npm test
    npm run build -- --outDir "${CANDIDATE_FRONTEND_DIST}" --emptyOutDir
)
chmod -R u=rwX,go=rX "${CANDIDATE_FRONTEND_DIST}"

DOMEYE_CORE_CANDIDATE_FRONTEND_DIST="${CANDIDATE_FRONTEND_DIST}" \
    "${SCRIPT_DIR}/candidate-stack.sh" "${RELEASE_DIR}" "${DATABASE_ENV_FILE}" "${HIDDEN_PATH}"

if [[ -f "${NGINX_TARGET}" && ! -L "${NGINX_TARGET}" ]]; then
    nginx_target_existed=true
    nginx_backup="${PROJECT_ROOT}/var/releases/nginx-before-$(date -u '+%Y%m%dT%H%M%SZ')-$$.conf"
    install -d -m 0750 "${PROJECT_ROOT}/var/releases"
    install -m 0644 "${NGINX_TARGET}" "${nginx_backup}"
elif [[ -e "${NGINX_TARGET}" || -L "${NGINX_TARGET}" ]]; then
    domeye_artifact_error "Nginx 目标不是普通文件：${NGINX_TARGET}"
    exit 1
fi

if [[ "${production_backend_was_running}" == true ]]; then
    backend_stop_attempted=true
    "${DEPLOY_DIR}/stop-backend.sh"
    backend_stopped_for_switch=true
fi
info_install_attempted=true
"${DEPLOY_DIR}/artifacts/install-info-artifact.sh" "${RELEASE_DIR}"
info_installed=true
activation_started=true
if DOMEYE_CORE_PREVIOUS_BACKEND_WAS_RUNNING="${production_backend_was_running}" \
        DOMEYE_CORE_PREVIOUS_INFO_DIR="${previous_runtime_info_dir}" \
        DOMEYE_CORE_DEFER_BACKEND_RESTART_ON_ROLLBACK=true \
        "${DEPLOY_DIR}/database/activate-database.sh" \
        "${release_id}" "${DATABASE_ENV_FILE}" "${MANIFEST_PATH}"; then
    production_activated=true
else
    activation_exit_code=$?
    domeye_artifact_error "数据库激活失败（退出码 ${activation_exit_code}），开始按持久状态回滚"
    exit "${activation_exit_code}"
fi
"${DEPLOY_DIR}/status.sh"

frontend_install_attempted=true
if "${DEPLOY_DIR}/artifacts/install-frontend-build.sh" \
        "${CANDIDATE_FRONTEND_DIST}" "${release_id}"; then
    frontend_installed=true
else
    frontend_exit_code=$?
    domeye_artifact_error \
        "前端构建原子安装失败（退出码 ${frontend_exit_code}），开始按持久状态回滚"
    exit "${frontend_exit_code}"
fi

nginx_change_attempted=true
install -m 0644 "${NGINX_SOURCE}" "${NGINX_TARGET}"
if ! nginx -t; then
    if [[ -n "${nginx_backup}" ]]; then
        install -m 0644 "${nginx_backup}" "${NGINX_TARGET}"
    else
        rm -f -- "${NGINX_TARGET}"
    fi
    domeye_artifact_error 'Nginx 配置检查失败，已恢复原配置'
    exit 1
fi
if [[ "${nginx_was_active}" == true ]]; then
    systemctl reload nginx
else
    systemctl start nginx
fi

"${DEPLOY_DIR}/status.sh"
"${SCRIPT_DIR}/smoke.sh" "${MANIFEST_PATH}"
"${SCRIPT_DIR}/verify-isolation.sh" "${MANIFEST_PATH}" "${HIDDEN_PATH}" "${DATABASE_ENV_FILE}" 29429

acceptance_complete=true
printf 'Domeye Core 独立部署完整验收通过：%s\n' "${release_id}"
