#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"
# shellcheck source=../lib/frontend-common.sh
source "${SCRIPT_DIR}/../lib/frontend-common.sh"

usage() {
    printf '用法：%s <候选 dist 目录> <release-id>\n' "${0##*/}" >&2
}

if (( $# != 2 )); then
    usage
    exit 2
fi

readonly SOURCE_DIR="${1%/}"
readonly RELEASE_ID="$2"
readonly TARGET_DIR="${DOMEYE_CORE_FRONTEND_TARGET}"
readonly TARGET_PARENT="${TARGET_DIR%/*}"
readonly STATE_DIR="${DOMEYE_CORE_FRONTEND_STATE_DIR}"
readonly CURRENT_STATE="${DOMEYE_CORE_FRONTEND_CURRENT_STATE}"
readonly JOURNAL="${DOMEYE_CORE_FRONTEND_ROLLBACK_JOURNAL}"
readonly STATUS="${DOMEYE_CORE_FRONTEND_INSTALL_STATUS}"
readonly LOCK_DIR="${DOMEYE_CORE_FRONTEND_INSTALL_LOCK}"
readonly LOCK_OWNER="${LOCK_DIR}/owner-pid"

domeye_artifact_validate_release_id "${RELEASE_ID}"
for command_name in awk cp find install jq mktemp mv sha256sum sort; do
    domeye_artifact_require_command "${command_name}"
done
domeye_frontend_validate_tree "${SOURCE_DIR}"
if [[ ! -d "${TARGET_PARENT}" || -L "${TARGET_PARENT}" ]]; then
    domeye_artifact_error "前端目录不存在或是软链接：${TARGET_PARENT}"
    exit 1
fi
if [[ -L "${STATE_DIR}" || ( -e "${STATE_DIR}" && ! -d "${STATE_DIR}" ) ]]; then
    domeye_artifact_error "发布状态目录无效：${STATE_DIR}"
    exit 1
fi
install -d -m 0750 "${STATE_DIR}"
if [[ -e "${STATUS}" || -L "${STATUS}" ]]; then
    domeye_artifact_error "存在尚未收敛的前端安装状态，请先执行一次 rollback-frontend-build.sh 收敛状态：${STATUS}"
    exit 1
fi
if ! mkdir -- "${LOCK_DIR}"; then
    domeye_artifact_error "已有前端安装或回滚任务持有锁：${LOCK_DIR}"
    exit 1
fi

# 从持锁后的第一条可能失败命令开始，所有变量均有安全默认值且 EXIT 已接管。
lock_owned=true
staged_dir=''
backup_dir=''
current_backup=''
journal_backup=''
current_tmp=''
journal_tmp=''
status_tmp=''
target_existed=false
previous_tree_sha256=''
previous_release=''
previous_current_sha256=''
previous_journal_sha256=''
previous_release_known=false
current_existed=false
journal_existed=false
switch_attempted=false
status_written=false
commit_complete=false

cleanup() {
    local exit_code=$?
    local final_exit_code="${exit_code}"
    local cleanup_failed=false
    local lock_cleanup_failed=false
    local restore_tmp

    if [[ "${commit_complete}" != true && "${switch_attempted}" == true ]]; then
        if [[ "${target_existed}" == true ]]; then
            if [[ -d "${backup_dir}" && ! -L "${backup_dir}" ]]; then
                if [[ -e "${TARGET_DIR}" || -L "${TARGET_DIR}" ]]; then
                    if [[ -d "${TARGET_DIR}" && ! -L "${TARGET_DIR}" \
                        && ! -e "${staged_dir}" && ! -L "${staged_dir}" ]]; then
                        mv -T -- "${TARGET_DIR}" "${staged_dir}" || cleanup_failed=true
                    else
                        domeye_artifact_error '前端自动恢复时发现目标、候选目录状态冲突'
                        cleanup_failed=true
                    fi
                fi
                if [[ "${cleanup_failed}" == false \
                    && ! -e "${TARGET_DIR}" && ! -L "${TARGET_DIR}" ]]; then
                    mv -T -- "${backup_dir}" "${TARGET_DIR}" || cleanup_failed=true
                fi
            elif [[ ! -d "${TARGET_DIR}" || -L "${TARGET_DIR}" ]]; then
                domeye_artifact_error '旧前端目录移动状态不完整，无法自动恢复'
                cleanup_failed=true
            fi
        elif [[ -e "${TARGET_DIR}" || -L "${TARGET_DIR}" ]]; then
            if [[ -d "${TARGET_DIR}" && ! -L "${TARGET_DIR}" \
                && ! -e "${staged_dir}" && ! -L "${staged_dir}" ]]; then
                mv -T -- "${TARGET_DIR}" "${staged_dir}" || cleanup_failed=true
            else
                domeye_artifact_error '原前端目录不存在时出现不完整安装目标'
                cleanup_failed=true
            fi
        elif [[ ! -d "${staged_dir}" || -L "${staged_dir}" ]]; then
            domeye_artifact_error '前端候选目录和安装目标同时缺失，无法自动恢复'
            cleanup_failed=true
        fi

        if [[ "${current_existed}" == true ]]; then
            restore_tmp="${STATE_DIR}/.frontend-current.restore.$$"
            if ! install -m 0640 "${current_backup}" "${restore_tmp}" \
                || ! mv -T -- "${restore_tmp}" "${CURRENT_STATE}"; then
                rm -f -- "${restore_tmp}"
                cleanup_failed=true
            fi
        elif [[ -e "${CURRENT_STATE}" \
            && ( ! -f "${CURRENT_STATE}" || -L "${CURRENT_STATE}" ) ]]; then
            domeye_artifact_error 'frontend-current 已被替换为非普通文件，拒绝删除'
            cleanup_failed=true
        else
            rm -f -- "${CURRENT_STATE}" || cleanup_failed=true
        fi

        if [[ "${journal_existed}" == true ]]; then
            restore_tmp="${STATE_DIR}/.frontend-rollback.restore.$$"
            if ! install -m 0600 "${journal_backup}" "${restore_tmp}" \
                || ! mv -T -- "${restore_tmp}" "${JOURNAL}"; then
                rm -f -- "${restore_tmp}"
                cleanup_failed=true
            fi
        elif [[ -e "${JOURNAL}" && ( ! -f "${JOURNAL}" || -L "${JOURNAL}" ) ]]; then
            domeye_artifact_error 'frontend-rollback.json 已被替换为非普通文件，拒绝删除'
            cleanup_failed=true
        else
            rm -f -- "${JOURNAL}" || cleanup_failed=true
        fi
    fi

    if [[ "${commit_complete}" != true && "${cleanup_failed}" == false \
        && -n "${staged_dir}" && -d "${staged_dir}" && ! -L "${staged_dir}" ]]; then
        rm -rf -- "${staged_dir}" || cleanup_failed=true
    elif [[ "${commit_complete}" != true && "${cleanup_failed}" == false \
        && -n "${staged_dir}" && ( -e "${staged_dir}" || -L "${staged_dir}" ) ]]; then
        domeye_artifact_error "候选清理路径状态异常：${staged_dir}"
        cleanup_failed=true
    fi
    if [[ "${cleanup_failed}" == false ]]; then
        for cleanup_path in "${current_tmp}" "${journal_tmp}" "${status_tmp}" \
            "${current_backup}" "${journal_backup}"; do
            if [[ -n "${cleanup_path}" ]]; then
                rm -f -- "${cleanup_path}" || cleanup_failed=true
            fi
        done
    fi
    if [[ "${lock_owned}" == true ]]; then
        if [[ -f "${LOCK_OWNER}" && ! -L "${LOCK_OWNER}" ]]; then
            rm -f -- "${LOCK_OWNER}" || lock_cleanup_failed=true
        elif [[ -e "${LOCK_OWNER}" || -L "${LOCK_OWNER}" ]]; then
            domeye_artifact_error "前端安装锁所有者文件异常：${LOCK_OWNER}"
            lock_cleanup_failed=true
        fi
        rmdir -- "${LOCK_DIR}" || lock_cleanup_failed=true
        [[ "${lock_cleanup_failed}" == false ]] || cleanup_failed=true
        lock_owned=false
    fi
    if [[ "${status_written}" == true ]]; then
        if [[ "${commit_complete}" == true ]]; then
            if rm -f -- "${STATUS}"; then
                status_written=false
            else
                domeye_artifact_error "前端安装已提交，但无法清理 prepared 状态：${STATUS}"
            fi
        elif [[ "${cleanup_failed}" == false ]]; then
            rm -f -- "${STATUS}" || cleanup_failed=true
            status_written=false
        fi
    fi
    if [[ "${cleanup_failed}" == true ]]; then
        domeye_artifact_error '前端安装未能完整收敛，已保留 prepared 状态并返回 70'
        final_exit_code=70
    fi
    return "${final_exit_code}"
}
trap cleanup EXIT

printf '%s\n' "$$" > "${LOCK_OWNER}"
chmod 0600 "${LOCK_OWNER}"

staged_dir="$(mktemp -d "${TARGET_PARENT}/.dist-install-${RELEASE_ID}.XXXXXX")"
backup_dir="${TARGET_PARENT}/.dist-backup-${RELEASE_ID}-$(date -u '+%Y%m%dT%H%M%SZ')-$$"
current_backup="${STATE_DIR}/.frontend-current.before-${RELEASE_ID}-$$"
journal_backup="${STATE_DIR}/.frontend-rollback.before-${RELEASE_ID}-$$"
current_tmp="${STATE_DIR}/.frontend-current.tmp.$$"
journal_tmp="${STATE_DIR}/.frontend-rollback.tmp.$$"
status_tmp="${STATE_DIR}/.frontend-install-status.tmp.$$"
for generated_path in "${backup_dir}" "${current_backup}" "${journal_backup}" \
    "${current_tmp}" "${journal_tmp}" "${status_tmp}"; do
    if [[ -e "${generated_path}" || -L "${generated_path}" ]]; then
        domeye_artifact_error "前端安装临时路径已存在：${generated_path}"
        exit 1
    fi
done

if [[ -e "${CURRENT_STATE}" || -L "${CURRENT_STATE}" ]]; then
    domeye_artifact_require_regular_file "${CURRENT_STATE}"
    previous_release="$(<"${CURRENT_STATE}")"
    domeye_artifact_validate_release_id "${previous_release}"
    if [[ "${previous_release}" == "${RELEASE_ID}" ]]; then
        domeye_artifact_error "frontend-current 已是本次 release-id，拒绝重复安装并复用旧回滚日志：${RELEASE_ID}"
        exit 1
    fi
    install -m 0640 "${CURRENT_STATE}" "${current_backup}"
    current_existed=true
    previous_release_known=true
    previous_current_sha256="$(domeye_artifact_sha256 "${CURRENT_STATE}")"
fi
if [[ -e "${JOURNAL}" || -L "${JOURNAL}" ]]; then
    domeye_artifact_require_regular_file "${JOURNAL}"
    domeye_artifact_json_file "${JOURNAL}"
    install -m 0600 "${JOURNAL}" "${journal_backup}"
    journal_existed=true
    previous_journal_sha256="$(domeye_artifact_sha256 "${JOURNAL}")"
fi
if [[ -e "${TARGET_DIR}" || -L "${TARGET_DIR}" ]]; then
    domeye_frontend_validate_tree "${TARGET_DIR}"
    target_existed=true
    previous_tree_sha256="$(domeye_frontend_tree_sha256 "${TARGET_DIR}")"
elif [[ "${previous_release_known}" == true ]]; then
    domeye_artifact_error 'frontend-current 存在，但当前前端目录不存在'
    exit 1
fi

readonly SOURCE_TREE_SHA256="$(domeye_frontend_tree_sha256 "${SOURCE_DIR}")"
cp -a -- "${SOURCE_DIR}/." "${staged_dir}/"
# 构建产物统一为 Nginx 可遍历、可读取权限，不依赖调用方预先 chmod。
find "${staged_dir}" -type d -exec chmod 0755 {} +
find "${staged_dir}" -type f -exec chmod 0644 {} +
domeye_frontend_validate_tree "${staged_dir}"
if [[ "$(domeye_frontend_tree_sha256 "${staged_dir}")" != "${SOURCE_TREE_SHA256}" ]]; then
    domeye_artifact_error '前端候选复制后的确定性树哈希不一致'
    exit 1
fi

jq -n \
    --arg release_id "${RELEASE_ID}" \
    --arg created_at "$(domeye_artifact_iso_utc_now)" \
    --arg target "${TARGET_DIR}" \
    --arg staged "${staged_dir}" \
    --arg backup "${backup_dir}" \
    --arg tree_sha256 "${SOURCE_TREE_SHA256}" \
    --arg previous_tree_sha256 "${previous_tree_sha256}" \
    --arg current_backup "$(if [[ "${current_existed}" == true ]]; then printf '%s' "${current_backup}"; fi)" \
    --arg previous_current_sha256 "${previous_current_sha256}" \
    --arg journal_backup "$(if [[ "${journal_existed}" == true ]]; then printf '%s' "${journal_backup}"; fi)" \
    --arg previous_journal_sha256 "${previous_journal_sha256}" \
    --arg previous_release "${previous_release}" \
    --argjson installer_pid "$$" \
    --argjson target_existed "${target_existed}" \
    --argjson current_existed "${current_existed}" \
    --argjson journal_existed "${journal_existed}" \
    --argjson previous_release_known "${previous_release_known}" \
    '{
      release_id: $release_id,
      phase: "prepared",
      created_at: $created_at,
      installer_pid: $installer_pid,
      target: $target,
      staged: $staged,
      backup: $backup,
      tree_sha256: $tree_sha256,
      target_existed: $target_existed,
      previous_tree_sha256: (if $target_existed then $previous_tree_sha256 else null end),
      current_existed: $current_existed,
      current_backup: (if $current_existed then $current_backup else null end),
      previous_current_sha256: (if $current_existed then $previous_current_sha256 else null end),
      journal_existed: $journal_existed,
      journal_backup: (if $journal_existed then $journal_backup else null end),
      previous_journal_sha256: (if $journal_existed then $previous_journal_sha256 else null end),
      previous_release_known: $previous_release_known,
      previous_release: (if $previous_release_known then $previous_release else null end)
    }' > "${status_tmp}"
chmod 0600 "${status_tmp}"
mv -T -- "${status_tmp}" "${STATUS}"
status_written=true

switch_attempted=true
if [[ "${target_existed}" == true ]]; then
    mv -T -- "${TARGET_DIR}" "${backup_dir}"
fi
mv -T -- "${staged_dir}" "${TARGET_DIR}"
if [[ "$(domeye_frontend_tree_sha256 "${TARGET_DIR}")" != "${SOURCE_TREE_SHA256}" ]]; then
    domeye_artifact_error '切入后的前端树哈希与候选不一致'
    exit 1
fi

printf '%s\n' "${RELEASE_ID}" > "${current_tmp}"
chmod 0640 "${current_tmp}"
mv -T -- "${current_tmp}" "${CURRENT_STATE}"

jq -n \
    --arg release_id "${RELEASE_ID}" \
    --arg created_at "$(domeye_artifact_iso_utc_now)" \
    --arg target_dir "${TARGET_DIR}" \
    --arg tree_sha256 "${SOURCE_TREE_SHA256}" \
    --arg previous_tree_sha256 "${previous_tree_sha256}" \
    --arg previous_dir "$(if [[ "${target_existed}" == true ]]; then printf '%s' "${backup_dir}"; fi)" \
    --arg previous_release "${previous_release}" \
    --argjson rollback_available true \
    --argjson previous_target_existed "${target_existed}" \
    --argjson previous_release_known "${previous_release_known}" \
    '{
      release_id: $release_id,
      created_at: $created_at,
      target_dir: $target_dir,
      tree_sha256: $tree_sha256,
      rollback_available: $rollback_available,
      previous_target_existed: $previous_target_existed,
      previous_tree_sha256: (if $previous_target_existed then $previous_tree_sha256 else null end),
      previous_dir: (if $previous_dir == "" then null else $previous_dir end),
      previous_release_known: $previous_release_known,
      previous_release: (if $previous_release_known then $previous_release else null end)
    }' > "${journal_tmp}"
chmod 0600 "${journal_tmp}"
mv -T -- "${journal_tmp}" "${JOURNAL}"

# journal 是最后一个提交点；提交判定同时核对 current、journal 和实际目标树。
if [[ "$(<"${CURRENT_STATE}")" != "${RELEASE_ID}" ]] \
    || [[ "$(jq -r '.release_id' "${JOURNAL}")" != "${RELEASE_ID}" ]] \
    || [[ "$(jq -r '.tree_sha256' "${JOURNAL}")" != "${SOURCE_TREE_SHA256}" ]] \
    || [[ "$(domeye_frontend_tree_sha256 "${TARGET_DIR}")" != "${SOURCE_TREE_SHA256}" ]]; then
    domeye_artifact_error '前端 current、journal 与实际目标未形成一致提交'
    exit 1
fi
commit_complete=true

# 此时 current+journal+目标树已经构成持久提交。EXIT 会先释放锁、再清理
# prepared 状态；清理失败只告警，显式回滚仍可按 committed 分支收敛。
printf '前端构建已原子安装：%s（release-id：%s，tree-sha256：%s）\n' \
    "${TARGET_DIR}" "${RELEASE_ID}" "${SOURCE_TREE_SHA256}"
if [[ "${target_existed}" == true ]]; then
    printf '旧前端目录保留为：%s\n' "${backup_dir}"
fi
