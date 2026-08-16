#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"
# shellcheck source=../lib/database-common.sh
source "${SCRIPT_DIR}/../lib/database-common.sh"
# shellcheck source=../lib/backend-common.sh
source "${SCRIPT_DIR}/../lib/backend-common.sh"
# shellcheck source=../lib/data-profile.sh
source "${SCRIPT_DIR}/../lib/data-profile.sh"

domeye_core_require_realtime_profile || exit 1

if (( $# < 1 || $# > 3 )); then
    printf '用法：%s <release-id> [数据库配置] [发布清单]\n' "${0##*/}" >&2
    exit 2
fi

readonly RELEASE_ID="$1"
readonly DATABASE_ENV_FILE="${2:-${DOMEYE_CORE_DATABASE_CONFIG_DEFAULT}}"
readonly RELEASE_MANIFEST="${3:-${DOMEYE_CORE_DEFAULT_ARTIFACT_ROOT}/releases/${RELEASE_ID}/${DOMEYE_CORE_RELEASE_MANIFEST}}"
domeye_artifact_validate_release_id "${RELEASE_ID}"
domeye_database_load_env "${DATABASE_ENV_FILE}"
domeye_database_validate_config

readonly RELEASE_DATA_ROOT="${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/${RELEASE_ID}"
readonly TARGET_DATA_DIR="${RELEASE_DATA_ROOT}/postgres"
readonly STATE_FILE="${RELEASE_DATA_ROOT}/restore-state.json"
readonly REVALIDATION_MARKER="${RELEASE_DATA_ROOT}/restore-revalidation-in-progress"
readonly BACKEND_ROOT='/home/bgpdata/Domeye-Core'
readonly BACKEND_ENV="${BACKEND_ROOT}/backend/.env"
readonly RELEASE_STATE_DIR="${BACKEND_ROOT}/var/releases"
readonly CURRENT_RELEASE_STATE="${RELEASE_STATE_DIR}/database-current"
readonly STABLE_BACKEND_ENV_BACKUP="${RELEASE_STATE_DIR}/backend-env-before-${RELEASE_ID}"
readonly ACTIVATION_JOURNAL="${RELEASE_STATE_DIR}/database-rollback.json"
readonly ACTIVATION_ROLLBACK_STATUS="${RELEASE_STATE_DIR}/activation-rollback-status-${RELEASE_ID}.json"

for command_name in awk curl jq ps readlink screen tr; do
    domeye_artifact_require_command "${command_name}"
done
domeye_artifact_require_regular_file "${STATE_FILE}"
domeye_artifact_json_file "${STATE_FILE}"
domeye_artifact_require_regular_file "${TARGET_DATA_DIR}/PG_VERSION"
domeye_artifact_require_regular_file "${RELEASE_MANIFEST}"
if [[ -e "${REVALIDATION_MARKER}" || -L "${REVALIDATION_MARKER}" ]]; then
    domeye_artifact_error '候选数据库正在复验或上次复验未完成，拒绝激活'
    exit 1
fi
if [[ -f "${CURRENT_RELEASE_STATE}" && "$(<"${CURRENT_RELEASE_STATE}")" == "${RELEASE_ID}" ]]; then
    domeye_artifact_error "数据库发布已经处于活动状态：${RELEASE_ID}"
    exit 1
fi
if [[ -e "${STABLE_BACKEND_ENV_BACKUP}" ]]; then
    domeye_artifact_error "该 release-id 已存在切换前配置备份，拒绝覆盖：${STABLE_BACKEND_ENV_BACKUP}"
    exit 1
fi
if [[ -e "${ACTIVATION_JOURNAL}" && ( ! -f "${ACTIVATION_JOURNAL}" || -L "${ACTIVATION_JOURNAL}" ) ]]; then
    domeye_artifact_error "数据库回滚日志不是普通文件：${ACTIVATION_JOURNAL}"
    exit 1
fi
if [[ -e "${ACTIVATION_ROLLBACK_STATUS}" || -L "${ACTIVATION_ROLLBACK_STATUS}" ]]; then
    domeye_artifact_require_regular_file "${ACTIVATION_ROLLBACK_STATUS}"
    domeye_artifact_json_file "${ACTIVATION_ROLLBACK_STATUS}"
    if ! jq -e \
        --arg release_id "${RELEASE_ID}" \
        '.schema_version == 1
         and .release_id == $release_id
         and .phase == "rollback_complete"
         and .changes_started == true
         and .rollback_ok == true' \
        "${ACTIVATION_ROLLBACK_STATUS}" >/dev/null; then
        domeye_artifact_error \
            "发现未确认完整回滚的历史激活状态，拒绝再次切换：${ACTIVATION_ROLLBACK_STATUS}"
        exit 1
    fi
    rm -f -- "${ACTIVATION_ROLLBACK_STATUS}"
fi
if ! jq -e \
    --arg release_id "${RELEASE_ID}" \
    '.schema_version == 1
     and .phase == "verified"
     and .release_id == $release_id
     and (.system_identifier | type) == "string"
     and (.system_identifier | test("^[0-9]+$"))' \
    "${STATE_FILE}" >/dev/null; then
    domeye_artifact_error '候选数据库恢复状态未通过 verified 门禁或与 release-id 不一致'
    exit 1
fi
if [[ -e "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}" && ! -L "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}" ]]; then
    domeye_artifact_error "活动数据库路径不是软链接，拒绝替换：${DOMEYE_CORE_DATABASE_ACTIVE_LINK}"
    exit 1
fi

previous_link_existed=false
previous_target=''
if [[ -L "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}" ]]; then
    previous_link_existed=true
    previous_target="$(readlink -f "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}")"
    if [[ "${previous_target}" != "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/"*/postgres ]]; then
        domeye_artifact_error "原活动数据库路径越界：${previous_target}"
        exit 1
    fi
fi

backend_was_running=false
if screen -ls 2>/dev/null | awk '$1 ~ /^[0-9]+\.domeye_core_app$/ {found=1} END {exit !found}'; then
    backend_was_running=true
fi
journal_backend_was_running="${backend_was_running}"
if [[ -n "${DOMEYE_CORE_PREVIOUS_BACKEND_WAS_RUNNING:-}" ]]; then
    if [[ "${DOMEYE_CORE_PREVIOUS_BACKEND_WAS_RUNNING}" != true && "${DOMEYE_CORE_PREVIOUS_BACKEND_WAS_RUNNING}" != false ]]; then
        domeye_artifact_error '切换前后端运行状态覆盖值必须为 true 或 false'
        exit 1
    fi
    journal_backend_was_running="${DOMEYE_CORE_PREVIOUS_BACKEND_WAS_RUNNING}"
fi
defer_backend_restart_on_rollback=false
case "${DOMEYE_CORE_DEFER_BACKEND_RESTART_ON_ROLLBACK:-false}" in
    true) defer_backend_restart_on_rollback=true ;;
    false|'') ;;
    *)
        domeye_artifact_error 'DOMEYE_CORE_DEFER_BACKEND_RESTART_ON_ROLLBACK 只能为 true 或 false'
        exit 1
        ;;
esac
previous_runtime_info_dir="${DOMEYE_CORE_PREVIOUS_INFO_DIR:-}"
if [[ -z "${previous_runtime_info_dir}" && "${backend_was_running}" == true ]]; then
    if ! previous_runtime_info_dir="$(domeye_core_capture_backend_info_dir)"; then
        domeye_artifact_error '无法捕获切换前后端进程的实际 INFO_DIR'
        exit 1
    fi
fi
if [[ -n "${previous_runtime_info_dir}" ]]; then
    domeye_core_validate_info_dir "${previous_runtime_info_dir}"
elif [[ "${journal_backend_was_running}" == true ]]; then
    domeye_artifact_error '切换前后端处于运行状态，但没有可回滚的实际 INFO_DIR'
    exit 1
fi
database_was_running=false
if [[ "$(docker inspect --format '{{.State.Running}}' "${DOMEYE_CORE_DATABASE_CONTAINER}" 2>/dev/null || true)" == true ]]; then
    database_was_running=true
fi

install -d -m 0750 "${RELEASE_STATE_DIR}"
previous_current_state_existed=false
previous_current_state=''
if [[ -f "${CURRENT_RELEASE_STATE}" && ! -L "${CURRENT_RELEASE_STATE}" ]]; then
    previous_current_state_existed=true
    previous_current_state="$(<"${CURRENT_RELEASE_STATE}")"
    domeye_artifact_validate_release_id "${previous_current_state}"
elif [[ -e "${CURRENT_RELEASE_STATE}" ]]; then
    domeye_artifact_error "原数据库 current 状态不是普通文件：${CURRENT_RELEASE_STATE}"
    exit 1
fi
previous_previous_state_existed=false
previous_previous_state=''
if [[ -f "${RELEASE_STATE_DIR}/database-previous" && ! -L "${RELEASE_STATE_DIR}/database-previous" ]]; then
    previous_previous_state_existed=true
    previous_previous_state="$(<"${RELEASE_STATE_DIR}/database-previous")"
elif [[ -e "${RELEASE_STATE_DIR}/database-previous" ]]; then
    domeye_artifact_error '原数据库 previous 状态不是普通文件'
    exit 1
fi
backend_env_existed=false
backend_env_backup="${RELEASE_STATE_DIR}/.backend-env-before-${RELEASE_ID}-$$"
activation_journal_backup="${RELEASE_STATE_DIR}/.database-rollback.before-${RELEASE_ID}-$$"
source_rollback_state_backup="${RELEASE_STATE_DIR}/.source-rollback.before-${RELEASE_ID}-$$"
cleanup_prepared_backups() {
    local exit_code=$?
    local cleanup_exit_code="${exit_code}"

    if ! rm -f -- \
        "${backend_env_backup}" \
        "${activation_journal_backup}" \
        "${source_rollback_state_backup}"; then
        domeye_artifact_error '激活预检失败，且临时敏感备份清理失败'
        cleanup_exit_code=70
    fi
    return "${cleanup_exit_code}"
}
trap cleanup_prepared_backups EXIT
if [[ -f "${BACKEND_ENV}" && ! -L "${BACKEND_ENV}" ]]; then
    install -m 0600 "${BACKEND_ENV}" "${backend_env_backup}"
    backend_env_existed=true
elif [[ -e "${BACKEND_ENV}" ]]; then
    domeye_artifact_error "后端 .env 不是普通文件：${BACKEND_ENV}"
    exit 1
fi

source_rollback_marker_needed=false
if [[ "${previous_link_existed}" == false && "${backend_env_existed}" == true ]]; then
    previous_db_host="$(domeye_core_backend_env_value DB_HOST)"
    previous_db_port="$(domeye_core_backend_env_value DB_PORT)"
    if [[ "${previous_db_host}" != "${DOMEYE_CORE_BACKEND_DB_HOST}" \
        || "${previous_db_port}" != "${DOMEYE_CORE_BACKEND_DB_PORT}" ]]; then
        if [[ -z "${previous_runtime_info_dir}" ]]; then
            previous_runtime_info_dir="$(domeye_core_backend_env_value INFO_DIR)"
        fi
        if [[ -z "${previous_runtime_info_dir}" ]]; then
            domeye_artifact_error '切换前源库配置缺少可回滚的 INFO_DIR'
            exit 1
        fi
        domeye_core_validate_info_dir "${previous_runtime_info_dir}"
        source_rollback_marker_needed=true
    fi
fi

activation_journal_existed=false
if [[ -f "${ACTIVATION_JOURNAL}" && ! -L "${ACTIVATION_JOURNAL}" ]]; then
    install -m 0600 "${ACTIVATION_JOURNAL}" "${activation_journal_backup}"
    activation_journal_existed=true
fi
source_rollback_state_existed=false
if [[ -e "${DOMEYE_CORE_SOURCE_ROLLBACK_STATE}" || -L "${DOMEYE_CORE_SOURCE_ROLLBACK_STATE}" ]]; then
    domeye_artifact_require_regular_file "${DOMEYE_CORE_SOURCE_ROLLBACK_STATE}"
    domeye_artifact_json_file "${DOMEYE_CORE_SOURCE_ROLLBACK_STATE}"
    install -m 0600 "${DOMEYE_CORE_SOURCE_ROLLBACK_STATE}" "${source_rollback_state_backup}"
    source_rollback_state_existed=true
fi

write_activation_status() {
    local phase="$1"
    local changes_started_json="$2"
    local rollback_ok_json="$3"
    local failure_summary="$4"
    local status_tmp="${ACTIVATION_ROLLBACK_STATUS}.tmp.$$"
    local updated_at

    if ! updated_at="$(domeye_artifact_iso_utc_now)"; then
        return 1
    fi
    if ! jq -n \
        --argjson schema_version 1 \
        --arg release_id "${RELEASE_ID}" \
        --arg phase "${phase}" \
        --arg updated_at "${updated_at}" \
        --argjson changes_started "${changes_started_json}" \
        --argjson rollback_ok "${rollback_ok_json}" \
        --arg failure_summary "${failure_summary}" \
        '{
          schema_version: $schema_version,
          release_id: $release_id,
          phase: $phase,
          updated_at: $updated_at,
          changes_started: $changes_started,
          rollback_ok: $rollback_ok,
          failure_summary: (if $failure_summary == "" then null else $failure_summary end)
        }' > "${status_tmp}"; then
        rm -f -- "${status_tmp}"
        return 1
    fi
    if ! chmod 0600 "${status_tmp}" || ! mv -T -- "${status_tmp}" "${ACTIVATION_ROLLBACK_STATUS}"; then
        rm -f -- "${status_tmp}"
        return 1
    fi
}

restore_active_link() {
    local rollback_link

    if [[ "${previous_link_existed}" == true ]]; then
        if [[ "${previous_target}" != "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/"*/postgres \
            || ! -f "${previous_target}/PG_VERSION" \
            || -L "${previous_target}/PG_VERSION" ]]; then
            return 1
        fi
        rollback_link="${DOMEYE_CORE_DATABASE_ACTIVE_LINK}.rollback.$$"
        rm -f -- "${rollback_link}"
        ln -s "${previous_target}" "${rollback_link}" \
            && mv -T -- "${rollback_link}" "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}"
    else
        if [[ -e "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}" \
            && ! -L "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}" ]]; then
            return 1
        fi
        rm -f -- "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}"
    fi
}

restore_backend_env() {
    local env_restore_source env_restore_tmp

    if [[ "${backend_env_existed}" == true ]]; then
        env_restore_source="${backend_env_backup}"
        if [[ ! -f "${env_restore_source}" || -L "${env_restore_source}" ]]; then
            env_restore_source="${STABLE_BACKEND_ENV_BACKUP}"
        fi
        if [[ ! -f "${env_restore_source}" || -L "${env_restore_source}" ]]; then
            return 1
        fi
        env_restore_tmp="${BACKEND_ENV}.rollback.$$"
        install -m 0600 "${env_restore_source}" "${env_restore_tmp}" \
            && mv -T -- "${env_restore_tmp}" "${BACKEND_ENV}"
    else
        if [[ -e "${BACKEND_ENV}" && ( ! -f "${BACKEND_ENV}" || -L "${BACKEND_ENV}" ) ]]; then
            return 1
        fi
        rm -f -- "${BACKEND_ENV}"
    fi
}

restore_text_state() {
    local target="$1"
    local existed="$2"
    local value="$3"
    local mode="$4"
    local restore_tmp

    if [[ "${existed}" == true ]]; then
        if [[ -e "${target}" && ( ! -f "${target}" || -L "${target}" ) ]]; then
            return 1
        fi
        restore_tmp="${target}.rollback.$$"
        if ! printf '%s\n' "${value}" > "${restore_tmp}" \
            || ! chmod "${mode}" "${restore_tmp}" \
            || ! mv -T -- "${restore_tmp}" "${target}"; then
            rm -f -- "${restore_tmp}"
            return 1
        fi
    else
        if [[ -e "${target}" && ( ! -f "${target}" || -L "${target}" ) ]]; then
            return 1
        fi
        rm -f -- "${target}"
    fi
}

restore_activation_journal() {
    local journal_restore_tmp

    if [[ "${activation_journal_existed}" == true ]]; then
        if [[ ! -f "${activation_journal_backup}" || -L "${activation_journal_backup}" ]]; then
            return 1
        fi
        journal_restore_tmp="${ACTIVATION_JOURNAL}.rollback.$$"
        install -m 0600 "${activation_journal_backup}" "${journal_restore_tmp}" \
            && mv -T -- "${journal_restore_tmp}" "${ACTIVATION_JOURNAL}"
    else
        if [[ -e "${ACTIVATION_JOURNAL}" \
            && ( ! -f "${ACTIVATION_JOURNAL}" || -L "${ACTIVATION_JOURNAL}" ) ]]; then
            return 1
        fi
        rm -f -- "${ACTIVATION_JOURNAL}"
    fi
}

restore_source_rollback_state() {
    local marker_restore_tmp

    if [[ "${source_rollback_state_existed}" == true ]]; then
        if [[ ! -f "${source_rollback_state_backup}" || -L "${source_rollback_state_backup}" ]]; then
            return 1
        fi
        marker_restore_tmp="${DOMEYE_CORE_SOURCE_ROLLBACK_STATE}.rollback.$$"
        install -m 0600 "${source_rollback_state_backup}" "${marker_restore_tmp}" \
            && mv -T -- "${marker_restore_tmp}" "${DOMEYE_CORE_SOURCE_ROLLBACK_STATE}"
    elif [[ "${source_rollback_marker_needed}" == true ]]; then
        domeye_core_write_source_rollback_state \
            "${previous_runtime_info_dir}" \
            "activation-auto-rollback-${RELEASE_ID}"
    else
        domeye_core_clear_source_rollback_state
    fi
}

changes_started=false
rollback_needed=true
rollback() {
    local exit_code=$?
    local rollback_exit_code="${exit_code}"
    local rollback_failed=false
    local failure_summary=''
    local database_stopped=false
    local backend_stopped=false
    local link_restored=false
    local env_restored=false
    local source_marker_restored=false
    local current_state_restored=false
    local previous_state_restored=false
    local journal_restored=false
    local database_restart_ready=true
    local -a rollback_failures=()

    trap - EXIT
    if [[ "${rollback_needed}" == true && "${changes_started}" == true ]]; then
        set +e
        write_activation_status 'rolling_back' true null '' >/dev/null 2>&1 || true

        run_activation_rollback_step() {
            local label="$1"
            local step_rc
            shift
            if "$@" >/dev/null 2>&1; then
                return 0
            else
                step_rc=$?
                rollback_failed=true
                rollback_failures+=("${label}(exit=${step_rc})")
                domeye_artifact_error "激活自动回滚步骤失败：${label}（退出码 ${step_rc}）"
                return "${step_rc}"
            fi
        }

        if run_activation_rollback_step '停止后端' "${BACKEND_ROOT}/deploy/stop-backend.sh"; then
            backend_stopped=true
        fi
        if run_activation_rollback_step '停止独立数据库' \
            "${SCRIPT_DIR}/dbctl.sh" down "${DATABASE_ENV_FILE}"; then
            database_stopped=true
        fi
        if [[ "${database_stopped}" == true ]]; then
            if run_activation_rollback_step '恢复活动数据库链接' restore_active_link; then
                link_restored=true
            fi
        else
            rollback_failed=true
            rollback_failures+=('恢复活动数据库链接(skipped=database-running)')
        fi
        if run_activation_rollback_step '恢复后端配置' restore_backend_env; then
            env_restored=true
        fi
        if run_activation_rollback_step \
            '恢复 database-current' \
            restore_text_state \
            "${CURRENT_RELEASE_STATE}" \
            "${previous_current_state_existed}" \
            "${previous_current_state}" \
            0640; then
            current_state_restored=true
        fi
        if run_activation_rollback_step \
            '恢复 database-previous' \
            restore_text_state \
            "${RELEASE_STATE_DIR}/database-previous" \
            "${previous_previous_state_existed}" \
            "${previous_previous_state}" \
            0640; then
            previous_state_restored=true
        fi
        if run_activation_rollback_step '恢复数据库回滚日志' restore_activation_journal; then
            journal_restored=true
        fi

        if [[ "${env_restored}" == true ]]; then
            if run_activation_rollback_step '恢复源库回滚标记' restore_source_rollback_state; then
                source_marker_restored=true
            fi
        else
            rollback_failed=true
            rollback_failures+=('恢复源库回滚标记(skipped=env-not-restored)')
        fi

        if [[ "${previous_link_existed}" == true && "${database_was_running}" == true ]]; then
            if [[ "${link_restored}" == true ]]; then
                if ! run_activation_rollback_step \
                    '重启切换前独立数据库' \
                    "${SCRIPT_DIR}/dbctl.sh" up "${DATABASE_ENV_FILE}"; then
                    database_restart_ready=false
                fi
            else
                database_restart_ready=false
            fi
        fi
        if [[ "${journal_backend_was_running}" == true \
            && "${defer_backend_restart_on_rollback}" != true ]]; then
            if [[ "${env_restored}" == true \
                && "${source_marker_restored}" == true \
                && "${link_restored}" == true \
                && "${database_restart_ready}" == true \
                && "${backend_stopped}" == true \
                && "${current_state_restored}" == true \
                && "${previous_state_restored}" == true \
                && "${journal_restored}" == true ]]; then
                if ! run_activation_rollback_step \
                    '重启切换前后端' \
                    env \
                    DOMEYE_CORE_ALLOW_ROLLBACK_CONFIG=true \
                    DOMEYE_CORE_ROLLBACK_INFO_DIR="${previous_runtime_info_dir}" \
                    "${BACKEND_ROOT}/deploy/start-backend.sh"; then
                    run_activation_rollback_step \
                        '清理未通过健康检查的切换前后端' \
                        "${BACKEND_ROOT}/deploy/stop-backend.sh"
                fi
            else
                rollback_failed=true
                rollback_failures+=('重启切换前后端(skipped=restore-incomplete)')
            fi
        fi

        if [[ "${rollback_failed}" == false ]]; then
            if ! rm -f -- \
                "${STABLE_BACKEND_ENV_BACKUP}" \
                "${backend_env_backup}" \
                "${activation_journal_backup}" \
                "${source_rollback_state_backup}"; then
                rollback_failed=true
                rollback_failures+=('清理激活临时备份(exit=1)')
            fi
        fi
        if (( ${#rollback_failures[@]} > 0 )); then
            failure_summary="$(IFS=';'; printf '%s' "${rollback_failures[*]}")"
        fi
        if [[ "${rollback_failed}" == true ]]; then
            write_activation_status \
                'rollback_failed' true false "${failure_summary}" \
                || domeye_artifact_error '激活回滚失败，且持久回滚状态写入失败'
            rollback_exit_code=70
        elif ! write_activation_status 'rollback_complete' true true ''; then
            domeye_artifact_error '激活已回滚，但持久回滚完成状态写入失败'
            rollback_exit_code=70
        fi
    elif [[ "${rollback_needed}" == true && "${changes_started}" == false ]]; then
        set +e
        if ! rm -f -- \
            "${backend_env_backup}" \
            "${activation_journal_backup}" \
            "${source_rollback_state_backup}" \
            "${ACTIVATION_ROLLBACK_STATUS}"; then
            domeye_artifact_error '激活尚未开始，但清理预备状态失败'
            rollback_exit_code=70
        fi
    fi
    exit "${rollback_exit_code}"
}
trap rollback EXIT

write_activation_status 'prepared' false null ''
write_activation_status 'changes_started' true null ''
changes_started=true
if [[ "${source_rollback_marker_needed}" == true ]]; then
    domeye_core_write_source_rollback_state \
        "${previous_runtime_info_dir}" \
        "activation-preflight-${RELEASE_ID}"
fi
DOMEYE_CORE_SKIP_BACKEND_ENV_BACKUP=true \
    "${SCRIPT_DIR}/configure-backend-env.sh" "${DATABASE_ENV_FILE}"

if [[ "${previous_target}" != "${TARGET_DATA_DIR}" ]]; then
    "${BACKEND_ROOT}/deploy/stop-backend.sh"
    "${SCRIPT_DIR}/dbctl.sh" down "${DATABASE_ENV_FILE}"
    link_tmp="${DOMEYE_CORE_DATABASE_ACTIVE_LINK}.tmp.$$"
    ln -s "${TARGET_DATA_DIR}" "${link_tmp}"
    mv -T -- "${link_tmp}" "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}"
else
    "${BACKEND_ROOT}/deploy/stop-backend.sh"
fi

"${SCRIPT_DIR}/dbctl.sh" up "${DATABASE_ENV_FILE}"
"${BACKEND_ROOT}/deploy/start-backend.sh"
curl --fail --silent --show-error --max-time 5 'http://127.0.0.1:28473/api/v1/healthz' >/dev/null
# PostgreSQL 12 的单个 psql --command 只保留最后一个结果集，状态与计数必须分开查询。
database_readonly="$(docker exec \
    "${DOMEYE_CORE_DATABASE_CONTAINER}" \
    psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_READER_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --command 'SHOW transaction_read_only;')"
database_count="$(docker exec \
    "${DOMEYE_CORE_DATABASE_CONTAINER}" \
    psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_READER_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --command 'SELECT count(*) FROM public.feature_country;')"
if [[ "${database_readonly}" != on || ! "${database_count}" =~ ^[1-9][0-9]*$ ]]; then
    domeye_artifact_error '生产只读账号未能查询独立 feature_country 超表'
    exit 1
fi
curl --fail --silent --show-error --max-time 75 --get \
    --data-urlencode 'target=1299' \
    --data-urlencode 'start_time=2026-07-17 19:30:00' \
    --data-urlencode 'end_time=2026-07-17 20:30:00' \
    'http://127.0.0.1:28473/api/v1/features/top' \
    | jq -e 'type == "array" and length > 0' >/dev/null
"${BACKEND_ROOT}/deploy/acceptance/smoke.sh" "${RELEASE_MANIFEST}"

domeye_core_clear_source_rollback_state

state_tmp="${RELEASE_STATE_DIR}/.database-current.tmp.$$"
printf '%s\n' "${RELEASE_ID}" > "${state_tmp}"
chmod 0640 "${state_tmp}"
mv -- "${state_tmp}" "${RELEASE_STATE_DIR}/database-current"
if [[ "${previous_link_existed}" == true && "${previous_target}" != "${TARGET_DATA_DIR}" ]]; then
    previous_state_tmp="${RELEASE_STATE_DIR}/.database-previous.tmp.$$"
    printf '%s\n' "${previous_target}" > "${previous_state_tmp}"
    chmod 0640 "${previous_state_tmp}"
    mv -- "${previous_state_tmp}" "${RELEASE_STATE_DIR}/database-previous"
elif [[ "${previous_link_existed}" == false ]]; then
    rm -f -- "${RELEASE_STATE_DIR}/database-previous"
fi

if [[ -f "${backend_env_backup}" ]]; then
    mv -- "${backend_env_backup}" "${STABLE_BACKEND_ENV_BACKUP}"
    printf '切换前的后端配置保留为：%s\n' "${STABLE_BACKEND_ENV_BACKUP}"
fi
journal_tmp="${RELEASE_STATE_DIR}/.activation-before-${RELEASE_ID}.tmp.$$"
previous_info_existed=false
if [[ -n "${previous_runtime_info_dir}" ]]; then
    previous_info_existed=true
fi
jq -n \
    --arg release_id "${RELEASE_ID}" \
    --arg created_at "$(domeye_artifact_iso_utc_now)" \
    --arg previous_target "${previous_target}" \
    --arg previous_info_dir "${previous_runtime_info_dir}" \
    --argjson backend_env_existed "${backend_env_existed}" \
    --argjson backend_was_running "${journal_backend_was_running}" \
    --argjson database_was_running "${database_was_running}" \
    --argjson previous_link_existed "${previous_link_existed}" \
    --argjson previous_info_existed "${previous_info_existed}" \
    --argjson rollback_available true \
    '{
      release_id: $release_id,
      created_at: $created_at,
      rollback_available: $rollback_available,
      backend_env_existed: $backend_env_existed,
      backend_was_running: $backend_was_running,
      database_was_running: $database_was_running,
      previous_link_existed: $previous_link_existed,
      previous_info_existed: $previous_info_existed,
      previous_target: (if $previous_target == "" then null else $previous_target end),
      previous_info_dir: (if $previous_info_dir == "" then null else $previous_info_dir end)
    }' > "${journal_tmp}"
chmod 0600 "${journal_tmp}"
mv -T -- "${journal_tmp}" "${ACTIVATION_JOURNAL}"
rollback_needed=false
if ! rm -f -- \
    "${activation_journal_backup}" \
    "${source_rollback_state_backup}" \
    "${ACTIVATION_ROLLBACK_STATUS}"; then
    domeye_artifact_error '数据库已完成持久切换，但激活临时状态未能全部清理，请人工检查'
fi
printf '数据库发布已激活，后端已重启：%s\n' "${RELEASE_ID}"
