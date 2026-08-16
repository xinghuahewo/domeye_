#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"
# shellcheck source=../lib/frontend-common.sh
source "${SCRIPT_DIR}/../lib/frontend-common.sh"

if (( $# != 0 )); then
    printf '用法：%s\n' "${0##*/}" >&2
    exit 2
fi

readonly TARGET_DIR="${DOMEYE_CORE_FRONTEND_TARGET}"
readonly TARGET_PARENT="${TARGET_DIR%/*}"
readonly STATE_DIR="${DOMEYE_CORE_FRONTEND_STATE_DIR}"
readonly CURRENT_STATE="${DOMEYE_CORE_FRONTEND_CURRENT_STATE}"
readonly JOURNAL="${DOMEYE_CORE_FRONTEND_ROLLBACK_JOURNAL}"
readonly STATUS="${DOMEYE_CORE_FRONTEND_INSTALL_STATUS}"
readonly LOCK_DIR="${DOMEYE_CORE_FRONTEND_INSTALL_LOCK}"
readonly LOCK_OWNER="${LOCK_DIR}/owner-pid"

for command_name in awk cmp find install jq mv sha256sum sort; do
    domeye_artifact_require_command "${command_name}"
done
if [[ ! -d "${TARGET_PARENT}" || -L "${TARGET_PARENT}" ]]; then
    domeye_artifact_error "前端目录不存在或是软链接：${TARGET_PARENT}"
    exit 1
fi
if [[ ! -d "${STATE_DIR}" || -L "${STATE_DIR}" ]]; then
    domeye_artifact_error "发布状态目录不存在或是软链接：${STATE_DIR}"
    exit 1
fi

# SIGKILL 会留下目录锁。只有同一份普通 prepared 状态明确记录的安装 PID
# 已不存在时，回滚脚本才接管空锁；无状态或仍存活的锁一律拒绝。
if [[ -e "${LOCK_DIR}" || -L "${LOCK_DIR}" ]]; then
    if [[ ! -d "${LOCK_DIR}" || -L "${LOCK_DIR}" ]]; then
        domeye_artifact_error "前端安装锁不是实际目录：${LOCK_DIR}"
        exit 1
    fi
    if [[ ! -f "${LOCK_OWNER}" || -L "${LOCK_OWNER}" ]]; then
        domeye_artifact_error '前端锁所有者不是普通文件，拒绝接管锁'
        exit 1
    fi
    installer_pid="$(<"${LOCK_OWNER}")"
    if [[ ! "${installer_pid}" =~ ^[0-9]+$ || "${installer_pid}" -lt 2 ]]; then
        domeye_artifact_error '前端锁所有者 PID 无效，拒绝接管锁'
        exit 1
    fi
    if kill -0 "${installer_pid}" 2>/dev/null; then
        domeye_artifact_error "前端安装进程仍存活，拒绝并发回滚：PID ${installer_pid}"
        exit 1
    fi
    domeye_artifact_require_regular_file "${STATUS}"
    domeye_artifact_json_file "${STATUS}"
    if ! jq -e --argjson owner_pid "${installer_pid}" \
        '(.phase == "prepared" or .phase == "restored")
         and .installer_pid == $owner_pid' "${STATUS}" >/dev/null; then
        domeye_artifact_error '死锁所有者与 prepared 状态不绑定，拒绝接管'
        exit 1
    fi
    rm -f -- "${LOCK_OWNER}"
    if ! rmdir -- "${LOCK_DIR}"; then
        domeye_artifact_error "无法接管非空或变化中的前端安装锁：${LOCK_DIR}"
        exit 1
    fi
fi
if ! mkdir -- "${LOCK_DIR}"; then
    domeye_artifact_error "已有前端安装或回滚任务持有锁：${LOCK_DIR}"
    exit 1
fi

lock_owned=true
rollback_complete=false
switch_attempted=false
cleanup_failed=false
discard_dir=''
current_backup=''
journal_backup=''
previous_target_existed=false
previous_dir=''

cleanup() {
    local exit_code=$?
    local final_exit_code="${exit_code}"
    local restore_tmp
    local lock_cleanup_failed=false

    if [[ "${rollback_complete}" != true && "${switch_attempted}" == true ]]; then
        if [[ "${previous_target_existed}" == true ]]; then
            if [[ ! -e "${previous_dir}" && ! -L "${previous_dir}" ]]; then
                if [[ -d "${TARGET_DIR}" && ! -L "${TARGET_DIR}" ]]; then
                    mv -T -- "${TARGET_DIR}" "${previous_dir}" || cleanup_failed=true
                else
                    domeye_artifact_error '回滚恢复时找不到已切入的上一前端目录'
                    cleanup_failed=true
                fi
            elif [[ ! -d "${previous_dir}" || -L "${previous_dir}" ]]; then
                domeye_artifact_error '回滚恢复时上一前端备份路径状态异常'
                cleanup_failed=true
            elif [[ -e "${TARGET_DIR}" || -L "${TARGET_DIR}" ]]; then
                if [[ ! -d "${TARGET_DIR}" || -L "${TARGET_DIR}" \
                    || -e "${discard_dir}" || -L "${discard_dir}" ]]; then
                    domeye_artifact_error '回滚恢复时目标、备份和丢弃目录状态冲突'
                    cleanup_failed=true
                fi
            fi
        fi
        if [[ "${cleanup_failed}" == false && -d "${discard_dir}" && ! -L "${discard_dir}" ]]; then
            if [[ -e "${TARGET_DIR}" || -L "${TARGET_DIR}" ]]; then
                domeye_artifact_error '回滚恢复时当前前端目标仍然存在'
                cleanup_failed=true
            else
                mv -T -- "${discard_dir}" "${TARGET_DIR}" || cleanup_failed=true
            fi
        elif [[ "${cleanup_failed}" == false \
            && ( -e "${discard_dir}" || -L "${discard_dir}" ) ]]; then
            domeye_artifact_error '回滚丢弃路径状态异常'
            cleanup_failed=true
        elif [[ "${cleanup_failed}" == false \
            && ( ! -d "${TARGET_DIR}" || -L "${TARGET_DIR}" ) ]]; then
            domeye_artifact_error '回滚恢复时当前前端目录和丢弃目录同时缺失'
            cleanup_failed=true
        fi

        restore_tmp="${STATE_DIR}/.frontend-current.rollback-restore.$$"
        if ! install -m 0640 "${current_backup}" "${restore_tmp}" \
            || ! mv -T -- "${restore_tmp}" "${CURRENT_STATE}"; then
            rm -f -- "${restore_tmp}"
            cleanup_failed=true
        fi
        restore_tmp="${STATE_DIR}/.frontend-journal.rollback-restore.$$"
        if ! install -m 0600 "${journal_backup}" "${restore_tmp}" \
            || ! mv -T -- "${restore_tmp}" "${JOURNAL}"; then
            rm -f -- "${restore_tmp}"
            cleanup_failed=true
        fi
    fi

    if [[ "${cleanup_failed}" == false ]]; then
        for cleanup_path in "${current_backup}" "${journal_backup}"; do
            if [[ -n "${cleanup_path}" ]]; then
                rm -f -- "${cleanup_path}" || cleanup_failed=true
            fi
        done
    fi
    if [[ "${lock_owned}" == true ]]; then
        if [[ -f "${LOCK_OWNER}" && ! -L "${LOCK_OWNER}" ]]; then
            rm -f -- "${LOCK_OWNER}" || lock_cleanup_failed=true
        elif [[ -e "${LOCK_OWNER}" || -L "${LOCK_OWNER}" ]]; then
            domeye_artifact_error "前端回滚锁所有者文件异常：${LOCK_OWNER}"
            lock_cleanup_failed=true
        fi
        rmdir -- "${LOCK_DIR}" || lock_cleanup_failed=true
        [[ "${lock_cleanup_failed}" == false ]] || cleanup_failed=true
        lock_owned=false
    fi
    if [[ "${cleanup_failed}" == true ]]; then
        domeye_artifact_error '前端回滚未能完整收敛，已保留现场并返回 70'
        final_exit_code=70
    fi
    return "${final_exit_code}"
}
trap cleanup EXIT

printf '%s\n' "$$" > "${LOCK_OWNER}"
chmod 0600 "${LOCK_OWNER}"
if [[ -e "${STATUS}" || -L "${STATUS}" ]]; then
    domeye_artifact_require_regular_file "${STATUS}"
    domeye_artifact_json_file "${STATUS}"
    status_owner_tmp="${STATE_DIR}/.frontend-install-status.owner.$$"
    if [[ -e "${status_owner_tmp}" || -L "${status_owner_tmp}" ]]; then
        domeye_artifact_error "前端状态所有者临时文件已存在：${status_owner_tmp}"
        exit 70
    fi
    jq --argjson installer_pid "$$" '.installer_pid = $installer_pid' \
        "${STATUS}" > "${status_owner_tmp}"
    chmod 0600 "${status_owner_tmp}"
    mv -T -- "${status_owner_tmp}" "${STATUS}"
fi

prepared_recovered=false
if [[ -e "${STATUS}" || -L "${STATUS}" ]]; then
    domeye_artifact_require_regular_file "${STATUS}"
    domeye_artifact_json_file "${STATUS}"
    if ! jq -e --arg target "${TARGET_DIR}" \
        '(.phase == "prepared" or .phase == "restored")
         and (.release_id | type) == "string"
         and .target == $target
         and (.staged | type) == "string"
         and (.backup | type) == "string"
         and (.tree_sha256 | type) == "string"
         and (.tree_sha256 | test("^[0-9a-f]{64}$"))
         and (.target_existed | type) == "boolean"
         and (if .target_existed
              then (.previous_tree_sha256 | type) == "string"
                   and (.previous_tree_sha256 | test("^[0-9a-f]{64}$"))
              else .previous_tree_sha256 == null end)
         and (.current_existed | type) == "boolean"
         and (if .current_existed
              then (.current_backup | type) == "string"
                   and (.previous_current_sha256 | type) == "string"
                   and (.previous_current_sha256 | test("^[0-9a-f]{64}$"))
              else .current_backup == null and .previous_current_sha256 == null end)
         and (.journal_existed | type) == "boolean"
         and (if .journal_existed
              then (.journal_backup | type) == "string"
                   and (.previous_journal_sha256 | type) == "string"
                   and (.previous_journal_sha256 | test("^[0-9a-f]{64}$"))
              else .journal_backup == null and .previous_journal_sha256 == null end)
         and (.previous_release_known | type) == "boolean"' \
        "${STATUS}" >/dev/null; then
        domeye_artifact_error '前端 prepared 状态字段无效'
        exit 70
    fi

    status_release="$(jq -r '.release_id' "${STATUS}")"
    domeye_artifact_validate_release_id "${status_release}"
    status_phase="$(jq -r '.phase' "${STATUS}")"
    status_staged="$(jq -r '.staged' "${STATUS}")"
    status_backup="$(jq -r '.backup' "${STATUS}")"
    status_tree_sha256="$(jq -r '.tree_sha256' "${STATUS}")"
    status_target_existed="$(jq -r '.target_existed' "${STATUS}")"
    status_previous_tree_sha256="$(jq -r '.previous_tree_sha256 // empty' "${STATUS}")"
    status_current_existed="$(jq -r '.current_existed' "${STATUS}")"
    status_journal_existed="$(jq -r '.journal_existed' "${STATUS}")"
    status_previous_release_known="$(jq -r '.previous_release_known' "${STATUS}")"
    status_current_backup="$(jq -r '.current_backup // empty' "${STATUS}")"
    status_previous_current_sha256="$(jq -r '.previous_current_sha256 // empty' "${STATUS}")"
    status_journal_backup="$(jq -r '.journal_backup // empty' "${STATUS}")"
    status_previous_journal_sha256="$(jq -r '.previous_journal_sha256 // empty' "${STATUS}")"
    status_previous_release="$(jq -r '.previous_release // empty' "${STATUS}")"

    domeye_frontend_require_safe_generated_path \
        "${status_staged}" "${TARGET_PARENT}/.dist-install-${status_release}." '^[A-Za-z0-9]{6}$'
    domeye_frontend_require_safe_generated_path \
        "${status_backup}" "${TARGET_PARENT}/.dist-backup-${status_release}-" \
        '^[0-9]{8}T[0-9]{6}Z-[0-9]+$'
    if [[ "${status_current_existed}" == true ]]; then
        if [[ "${status_current_backup}" != "${STATE_DIR}/.frontend-current.before-${status_release}-"* \
            || ! "${status_current_backup##*-}" =~ ^[0-9]+$ ]]; then
            domeye_artifact_error 'prepared 状态中的旧 frontend-current 备份路径无效'
            exit 70
        fi
    elif [[ -n "${status_current_backup}" ]]; then
        domeye_artifact_error 'prepared 状态声明 current 原本不存在，但记录了备份'
        exit 70
    fi
    if [[ "${status_journal_existed}" == true ]]; then
        if [[ "${status_journal_backup}" != "${STATE_DIR}/.frontend-rollback.before-${status_release}-"* \
            || ! "${status_journal_backup##*-}" =~ ^[0-9]+$ ]]; then
            domeye_artifact_error 'prepared 状态中的旧 journal 备份路径无效'
            exit 70
        fi
    elif [[ -n "${status_journal_backup}" ]]; then
        domeye_artifact_error 'prepared 状态声明 journal 原本不存在，但记录了备份'
        exit 70
    fi
    if [[ "${status_previous_release_known}" == true ]]; then
        domeye_artifact_validate_release_id "${status_previous_release}"
        if [[ "${status_current_existed}" != true || "${status_target_existed}" != true ]]; then
            domeye_artifact_error 'prepared 状态的上一 release-id 与原目录/current 状态矛盾'
            exit 70
        fi
    elif [[ -n "${status_previous_release}" ]]; then
        domeye_artifact_error 'prepared 状态声明上一 release-id 未知，但记录了值'
        exit 70
    fi

    if [[ "${status_phase}" == restored ]]; then
        if [[ "${status_target_existed}" == true ]]; then
            domeye_frontend_validate_tree "${TARGET_DIR}" || exit 70
            if [[ "$(domeye_frontend_tree_sha256 "${TARGET_DIR}")" != "${status_previous_tree_sha256}" ]]; then
                domeye_artifact_error 'restored 状态下旧前端目标树尚未恢复'
                exit 70
            fi
        elif [[ -e "${TARGET_DIR}" || -L "${TARGET_DIR}" ]]; then
            domeye_artifact_error 'restored 状态声明原目标不存在，但当前目标仍存在'
            exit 70
        fi
        if [[ -e "${status_backup}" || -L "${status_backup}" ]]; then
            domeye_artifact_error 'restored 状态下旧前端备份仍未切回目标'
            exit 70
        fi
        if [[ "${status_current_existed}" == true ]]; then
            domeye_artifact_require_regular_file "${CURRENT_STATE}" || exit 70
            if [[ "$(domeye_artifact_sha256 "${CURRENT_STATE}")" != "${status_previous_current_sha256}" ]]; then
                domeye_artifact_error 'restored 状态下 frontend-current 尚未恢复'
                exit 70
            fi
        elif [[ -e "${CURRENT_STATE}" || -L "${CURRENT_STATE}" ]]; then
            domeye_artifact_error 'restored 状态下 frontend-current 应不存在'
            exit 70
        fi
        if [[ "${status_journal_existed}" == true ]]; then
            domeye_artifact_require_regular_file "${JOURNAL}" || exit 70
            if [[ "$(domeye_artifact_sha256 "${JOURNAL}")" != "${status_previous_journal_sha256}" ]]; then
                domeye_artifact_error 'restored 状态下旧 journal 尚未恢复'
                exit 70
            fi
        elif [[ -e "${JOURNAL}" || -L "${JOURNAL}" ]]; then
            domeye_artifact_error 'restored 状态下 journal 应不存在'
            exit 70
        fi
        if [[ -e "${status_staged}" || -L "${status_staged}" ]]; then
            domeye_frontend_validate_tree "${status_staged}" || exit 70
            if [[ "$(domeye_frontend_tree_sha256 "${status_staged}")" != "${status_tree_sha256}" ]]; then
                domeye_artifact_error 'restored 状态下候选树哈希异常，拒绝清理'
                exit 70
            fi
            rm -rf -- "${status_staged}"
        fi
        for old_state_backup in "${status_current_backup}" "${status_journal_backup}"; do
            if [[ -n "${old_state_backup}" ]]; then
                if [[ -L "${old_state_backup}" \
                    || ( -e "${old_state_backup}" && ! -f "${old_state_backup}" ) ]]; then
                    domeye_artifact_error "restored 状态下旧状态备份异常：${old_state_backup}"
                    exit 70
                fi
                rm -f -- "${old_state_backup}"
            fi
        done
        rm -f -- "${STATUS}"
        prepared_recovered=true
        rollback_complete=true
        printf '前端未完成安装的 restored 状态已清理完成。\n'
    fi

    status_committed=false
    if [[ "${prepared_recovered}" != true \
        && -f "${CURRENT_STATE}" && ! -L "${CURRENT_STATE}" \
        && "$(<"${CURRENT_STATE}")" == "${status_release}" \
        && -f "${JOURNAL}" && ! -L "${JOURNAL}" ]] \
        && jq -e --arg release_id "${status_release}" \
            --arg target "${TARGET_DIR}" \
            --arg tree_sha256 "${status_tree_sha256}" \
            --arg previous_tree_sha256 "${status_previous_tree_sha256}" \
            --arg previous_dir "$(if [[ "${status_target_existed}" == true ]]; then printf '%s' "${status_backup}"; fi)" \
            --arg previous_release "${status_previous_release}" \
            --argjson previous_target_existed "${status_target_existed}" \
            --argjson previous_release_known "${status_previous_release_known}" \
            '.release_id == $release_id
             and .target_dir == $target
             and .tree_sha256 == $tree_sha256
             and .rollback_available == true
             and .previous_target_existed == $previous_target_existed
             and ((.previous_tree_sha256 // "") == $previous_tree_sha256)
             and ((.previous_dir // "") == $previous_dir)
             and .previous_release_known == $previous_release_known
             and ((.previous_release // "") == $previous_release)' \
            "${JOURNAL}" >/dev/null \
        && domeye_frontend_validate_tree "${TARGET_DIR}" \
        && [[ "$(domeye_frontend_tree_sha256 "${TARGET_DIR}")" == "${status_tree_sha256}" ]]; then
        status_committed=true
    fi

    if [[ "${status_committed}" == true ]]; then
        for old_state_backup in "${status_current_backup}" "${status_journal_backup}"; do
            if [[ -n "${old_state_backup}" ]]; then
                if [[ -L "${old_state_backup}" \
                    || ( -e "${old_state_backup}" && ! -f "${old_state_backup}" ) ]]; then
                    domeye_artifact_error "prepared 收敛时发现异常状态备份：${old_state_backup}"
                    exit 70
                fi
                rm -f -- "${old_state_backup}"
            fi
        done
        rm -f -- "${STATUS}"
        printf '检测到前端安装已持久提交；已清理残留 prepared 状态并继续正常回滚。\n'
    elif [[ "${prepared_recovered}" != true ]]; then
        # 未形成 current+journal+目标树一致提交：按 prepared 路径恢复安装前状态。
        if [[ "${status_current_existed}" == true ]]; then
            domeye_artifact_require_regular_file "${status_current_backup}" || exit 70
        fi
        if [[ "${status_journal_existed}" == true ]]; then
            domeye_artifact_require_regular_file "${status_journal_backup}" || exit 70
            domeye_artifact_json_file "${status_journal_backup}" || exit 70
        fi

        if [[ "${status_target_existed}" == true ]]; then
            if [[ -d "${status_backup}" && ! -L "${status_backup}" ]]; then
                domeye_frontend_validate_tree "${status_backup}" || exit 70
                if [[ "$(domeye_frontend_tree_sha256 "${status_backup}")" != "${status_previous_tree_sha256}" ]]; then
                    domeye_artifact_error 'prepared 恢复时旧前端备份树哈希不一致'
                    exit 70
                fi
                if [[ -e "${TARGET_DIR}" || -L "${TARGET_DIR}" ]]; then
                    if [[ ! -d "${TARGET_DIR}" || -L "${TARGET_DIR}" \
                        || -e "${status_staged}" || -L "${status_staged}" ]]; then
                        domeye_artifact_error 'prepared 恢复时目标、备份和候选目录状态冲突'
                        exit 70
                    fi
                    domeye_frontend_validate_tree "${TARGET_DIR}" || exit 70
                    if [[ "$(domeye_frontend_tree_sha256 "${TARGET_DIR}")" != "${status_tree_sha256}" ]]; then
                        domeye_artifact_error 'prepared 恢复时目标不是记录的候选前端树'
                        exit 70
                    fi
                    mv -T -- "${TARGET_DIR}" "${status_staged}"
                fi
                if [[ -e "${TARGET_DIR}" || -L "${TARGET_DIR}" ]]; then
                    domeye_artifact_error 'prepared 恢复时旧前端目标位置仍被占用'
                    exit 70
                fi
                mv -T -- "${status_backup}" "${TARGET_DIR}"
            else
                if [[ ! -d "${TARGET_DIR}" || -L "${TARGET_DIR}" \
                    || ! -d "${status_staged}" || -L "${status_staged}" ]]; then
                    domeye_artifact_error 'prepared 恢复无法确认首次 mv 尚未发生'
                    exit 70
                fi
                if [[ "$(domeye_frontend_tree_sha256 "${TARGET_DIR}")" != "${status_previous_tree_sha256}" ]]; then
                    domeye_artifact_error 'prepared 恢复无法确认目标仍是安装前前端树'
                    exit 70
                fi
            fi
        else
            if [[ -e "${status_backup}" || -L "${status_backup}" ]]; then
                domeye_artifact_error 'prepared 状态声明原目标不存在，但出现了备份目录'
                exit 70
            fi
            if [[ -e "${TARGET_DIR}" || -L "${TARGET_DIR}" ]]; then
                if [[ ! -d "${TARGET_DIR}" || -L "${TARGET_DIR}" \
                    || -e "${status_staged}" || -L "${status_staged}" ]]; then
                    domeye_artifact_error '首次安装的 prepared 恢复遇到目标和候选冲突'
                    exit 70
                fi
                domeye_frontend_validate_tree "${TARGET_DIR}" || exit 70
                if [[ "$(domeye_frontend_tree_sha256 "${TARGET_DIR}")" != "${status_tree_sha256}" ]]; then
                    domeye_artifact_error '首次安装的目标不是记录的候选前端树'
                    exit 70
                fi
                mv -T -- "${TARGET_DIR}" "${status_staged}"
            elif [[ ! -d "${status_staged}" || -L "${status_staged}" ]]; then
                domeye_artifact_error '首次安装的目标和候选目录同时缺失'
                exit 70
            fi
        fi

        if [[ "${status_current_existed}" == true ]]; then
            state_tmp="${STATE_DIR}/.frontend-current.prepared-restore.$$"
            install -m 0640 "${status_current_backup}" "${state_tmp}"
            mv -T -- "${state_tmp}" "${CURRENT_STATE}"
        elif [[ -e "${CURRENT_STATE}" && ( ! -f "${CURRENT_STATE}" || -L "${CURRENT_STATE}" ) ]]; then
            domeye_artifact_error 'prepared 恢复拒绝删除非普通 frontend-current'
            exit 70
        else
            rm -f -- "${CURRENT_STATE}"
        fi
        if [[ "${status_journal_existed}" == true ]]; then
            state_tmp="${STATE_DIR}/.frontend-journal.prepared-restore.$$"
            install -m 0600 "${status_journal_backup}" "${state_tmp}"
            mv -T -- "${state_tmp}" "${JOURNAL}"
        elif [[ -e "${JOURNAL}" && ( ! -f "${JOURNAL}" || -L "${JOURNAL}" ) ]]; then
            domeye_artifact_error 'prepared 恢复拒绝删除非普通 frontend-rollback.json'
            exit 70
        else
            rm -f -- "${JOURNAL}"
        fi

        if [[ "${status_target_existed}" == true ]]; then
            domeye_frontend_validate_tree "${TARGET_DIR}" || exit 70
        elif [[ -e "${TARGET_DIR}" || -L "${TARGET_DIR}" ]]; then
            domeye_artifact_error '首次安装 prepared 恢复后目标仍然存在'
            exit 70
        fi
        if [[ "${status_current_existed}" == true ]]; then
            cmp -s -- "${status_current_backup}" "${CURRENT_STATE}" || exit 70
            if [[ "$(domeye_artifact_sha256 "${CURRENT_STATE}")" != "${status_previous_current_sha256}" ]]; then
                domeye_artifact_error 'prepared 恢复后的 frontend-current 哈希不一致'
                exit 70
            fi
        elif [[ -e "${CURRENT_STATE}" || -L "${CURRENT_STATE}" ]]; then
            domeye_artifact_error 'prepared 恢复后 frontend-current 未恢复为不存在'
            exit 70
        fi
        if [[ "${status_journal_existed}" == true ]]; then
            cmp -s -- "${status_journal_backup}" "${JOURNAL}" || exit 70
            if [[ "$(domeye_artifact_sha256 "${JOURNAL}")" != "${status_previous_journal_sha256}" ]]; then
                domeye_artifact_error 'prepared 恢复后的 journal 哈希不一致'
                exit 70
            fi
        elif [[ -e "${JOURNAL}" || -L "${JOURNAL}" ]]; then
            domeye_artifact_error 'prepared 恢复后 journal 未恢复为不存在'
            exit 70
        fi
        domeye_frontend_validate_tree "${status_staged}" || exit 70
        if [[ "$(domeye_frontend_tree_sha256 "${status_staged}")" != "${status_tree_sha256}" ]]; then
            domeye_artifact_error 'prepared 恢复后的候选树哈希不一致，拒绝清理'
            exit 70
        fi
        status_restore_tmp="${STATE_DIR}/.frontend-install-status.restored.$$"
        jq --arg restored_at "$(domeye_artifact_iso_utc_now)" \
            '.phase = "restored" | .restored_at = $restored_at' \
            "${STATUS}" > "${status_restore_tmp}"
        chmod 0600 "${status_restore_tmp}"
        mv -T -- "${status_restore_tmp}" "${STATUS}"

        rm -rf -- "${status_staged}"
        rm -f -- "${status_current_backup}" "${status_journal_backup}"
        rm -f -- "${STATUS}"
        prepared_recovered=true
        rollback_complete=true
        printf '前端未完成安装已按 prepared 状态恢复到安装前状态。\n'
    fi
fi

if [[ "${prepared_recovered}" == true ]]; then
    exit 0
fi

# 没有 prepared 状态，或 prepared 已确认是完整提交：消费一次性回滚日志。
domeye_artifact_require_regular_file "${CURRENT_STATE}"
domeye_artifact_require_regular_file "${JOURNAL}"
domeye_artifact_json_file "${JOURNAL}"
readonly CURRENT_RELEASE="$(<"${CURRENT_STATE}")"
domeye_artifact_validate_release_id "${CURRENT_RELEASE}"
if [[ "$(jq -r '.release_id' "${JOURNAL}")" != "${CURRENT_RELEASE}" ]] \
    || [[ "$(jq -r '.target_dir // empty' "${JOURNAL}")" != "${TARGET_DIR}" ]] \
    || ! jq -e \
        '.rollback_available == true
         and (.tree_sha256 | type) == "string"
         and (.tree_sha256 | test("^[0-9a-f]{64}$"))
         and (.previous_target_existed | type) == "boolean"
         and (if .previous_target_existed
              then (.previous_tree_sha256 | type) == "string"
                   and (.previous_tree_sha256 | test("^[0-9a-f]{64}$"))
              else .previous_tree_sha256 == null end)
         and (.previous_release_known | type) == "boolean"' \
        "${JOURNAL}" >/dev/null; then
    domeye_artifact_error '没有与当前前端发布匹配的可用回滚日志'
    exit 1
fi
current_tree_sha256="$(jq -r '.tree_sha256' "${JOURNAL}")"
domeye_frontend_validate_tree "${TARGET_DIR}"
if [[ "$(domeye_frontend_tree_sha256 "${TARGET_DIR}")" != "${current_tree_sha256}" ]]; then
    domeye_artifact_error '当前前端目录与回滚日志的 tree-sha256 不一致'
    exit 1
fi

previous_target_existed="$(jq -r '.previous_target_existed' "${JOURNAL}")"
previous_tree_sha256="$(jq -r '.previous_tree_sha256 // empty' "${JOURNAL}")"
previous_release_known="$(jq -r '.previous_release_known' "${JOURNAL}")"
previous_dir="$(jq -r '.previous_dir // empty' "${JOURNAL}")"
previous_release="$(jq -r '.previous_release // empty' "${JOURNAL}")"
if [[ "${previous_target_existed}" == true ]]; then
    domeye_frontend_require_safe_generated_path \
        "${previous_dir}" "${TARGET_PARENT}/.dist-backup-${CURRENT_RELEASE}-" \
        '^[0-9]{8}T[0-9]{6}Z-[0-9]+$'
    domeye_frontend_validate_tree "${previous_dir}"
    if [[ "$(domeye_frontend_tree_sha256 "${previous_dir}")" != "${previous_tree_sha256}" ]]; then
        domeye_artifact_error '上一前端备份目录与回滚日志的树哈希不一致'
        exit 1
    fi
elif [[ -n "${previous_dir}" ]]; then
    domeye_artifact_error '回滚日志声明原前端目录不存在，但记录了备份目录'
    exit 1
fi
if [[ "${previous_release_known}" == true ]]; then
    domeye_artifact_validate_release_id "${previous_release}"
    if [[ "${previous_target_existed}" != true ]]; then
        domeye_artifact_error '回滚日志记录了上一 release-id，但原前端目录不存在'
        exit 1
    fi
elif [[ -n "${previous_release}" ]]; then
    domeye_artifact_error '回滚日志声明上一版本未知，但记录了 release-id'
    exit 1
fi

discard_dir="${TARGET_PARENT}/.dist-rollback-discard-${CURRENT_RELEASE}-$(date -u '+%Y%m%dT%H%M%SZ')-$$"
if [[ -e "${discard_dir}" || -L "${discard_dir}" ]]; then
    domeye_artifact_error "前端回滚丢弃路径已存在：${discard_dir}"
    exit 1
fi
current_backup="${STATE_DIR}/.frontend-current.rollback-before-${CURRENT_RELEASE}-$$"
journal_backup="${STATE_DIR}/.frontend-journal.rollback-before-${CURRENT_RELEASE}-$$"
for generated_path in "${current_backup}" "${journal_backup}"; do
    if [[ -e "${generated_path}" || -L "${generated_path}" ]]; then
        domeye_artifact_error "前端回滚临时路径已存在：${generated_path}"
        exit 1
    fi
done
install -m 0640 "${CURRENT_STATE}" "${current_backup}"
install -m 0600 "${JOURNAL}" "${journal_backup}"

switch_attempted=true
mv -T -- "${TARGET_DIR}" "${discard_dir}"
if [[ "${previous_target_existed}" == true ]]; then
    mv -T -- "${previous_dir}" "${TARGET_DIR}"
fi

if [[ "${previous_release_known}" == true ]]; then
    current_tmp="${STATE_DIR}/.frontend-current.rollback.$$"
    printf '%s\n' "${previous_release}" > "${current_tmp}"
    chmod 0640 "${current_tmp}"
    mv -T -- "${current_tmp}" "${CURRENT_STATE}"
else
    rm -f -- "${CURRENT_STATE}"
fi

journal_tmp="${STATE_DIR}/.frontend-rollback-consumed.tmp.$$"
jq --arg rolled_back_at "$(domeye_artifact_iso_utc_now)" \
    '.rollback_available = false | .rolled_back_at = $rolled_back_at' \
    "${JOURNAL}" > "${journal_tmp}"
chmod 0600 "${journal_tmp}"
mv -T -- "${journal_tmp}" "${JOURNAL}"
rollback_complete=true

if ! rm -rf -- "${discard_dir}"; then
    domeye_artifact_error "回滚已提交，但无法清理被替换的前端目录：${discard_dir}"
    exit 70
fi
printf '前端构建已回滚到安装前状态；一次性回滚日志已消费。\n'
