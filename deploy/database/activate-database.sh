#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"
# shellcheck source=../lib/database-common.sh
source "${SCRIPT_DIR}/../lib/database-common.sh"
# shellcheck source=../lib/backend-common.sh
source "${SCRIPT_DIR}/../lib/backend-common.sh"

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
readonly BACKEND_ROOT='/home/bgpdata/Domeye-Core'
readonly BACKEND_ENV="${BACKEND_ROOT}/backend/.env"
readonly RELEASE_STATE_DIR="${BACKEND_ROOT}/var/releases"
readonly CURRENT_RELEASE_STATE="${RELEASE_STATE_DIR}/database-current"
readonly STABLE_BACKEND_ENV_BACKUP="${RELEASE_STATE_DIR}/backend-env-before-${RELEASE_ID}"
readonly ACTIVATION_JOURNAL="${RELEASE_STATE_DIR}/database-rollback.json"

for command_name in awk curl jq ps readlink screen tr; do
    domeye_artifact_require_command "${command_name}"
done
domeye_artifact_require_regular_file "${STATE_FILE}"
domeye_artifact_require_regular_file "${TARGET_DATA_DIR}/PG_VERSION"
domeye_artifact_require_regular_file "${RELEASE_MANIFEST}"
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
if [[ "$(jq -r '.release_id' "${STATE_FILE}")" != "${RELEASE_ID}" ]]; then
    domeye_artifact_error '候选数据库恢复状态与 release-id 不一致'
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
if [[ -f "${BACKEND_ENV}" && ! -L "${BACKEND_ENV}" ]]; then
    install -m 0600 "${BACKEND_ENV}" "${backend_env_backup}"
    backend_env_existed=true
elif [[ -e "${BACKEND_ENV}" ]]; then
    domeye_artifact_error "后端 .env 不是普通文件：${BACKEND_ENV}"
    exit 1
fi

changes_started=false
rollback_needed=true
rollback() {
    local exit_code=$?
    trap - EXIT
    if [[ "${rollback_needed}" == true && "${changes_started}" == true ]]; then
        set +e
        "${BACKEND_ROOT}/deploy/stop-backend.sh" >/dev/null 2>&1
        "${SCRIPT_DIR}/dbctl.sh" down "${DATABASE_ENV_FILE}" >/dev/null 2>&1

        if [[ "${previous_link_existed}" == true ]]; then
            rollback_link="${DOMEYE_CORE_DATABASE_ACTIVE_LINK}.rollback.$$"
            ln -s "${previous_target}" "${rollback_link}"
            mv -T -- "${rollback_link}" "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}"
        else
            rm -f -- "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}"
        fi

        if [[ "${backend_env_existed}" == true ]]; then
            env_restore_source="${backend_env_backup}"
            if [[ ! -f "${env_restore_source}" && -f "${STABLE_BACKEND_ENV_BACKUP}" ]]; then
                env_restore_source="${STABLE_BACKEND_ENV_BACKUP}"
            fi
            if [[ -f "${env_restore_source}" ]]; then
                install -m 0600 "${env_restore_source}" "${BACKEND_ENV}"
            fi
        elif [[ "${backend_env_existed}" == false ]]; then
            rm -f -- "${BACKEND_ENV}"
        fi

        if [[ "${previous_current_state_existed}" == true ]]; then
            printf '%s\n' "${previous_current_state}" > "${CURRENT_RELEASE_STATE}.rollback.$$"
            chmod 0640 "${CURRENT_RELEASE_STATE}.rollback.$$"
            mv -- "${CURRENT_RELEASE_STATE}.rollback.$$" "${CURRENT_RELEASE_STATE}"
        else
            rm -f -- "${CURRENT_RELEASE_STATE}"
        fi
        if [[ "${previous_previous_state_existed}" == true ]]; then
            printf '%s\n' "${previous_previous_state}" > "${RELEASE_STATE_DIR}/database-previous.rollback.$$"
            chmod 0640 "${RELEASE_STATE_DIR}/database-previous.rollback.$$"
            mv -- "${RELEASE_STATE_DIR}/database-previous.rollback.$$" "${RELEASE_STATE_DIR}/database-previous"
        else
            rm -f -- "${RELEASE_STATE_DIR}/database-previous"
        fi
        rm -f -- "${STABLE_BACKEND_ENV_BACKUP}" "${backend_env_backup}"

        if [[ "${previous_link_existed}" == true && "${database_was_running}" == true ]]; then
            "${SCRIPT_DIR}/dbctl.sh" up "${DATABASE_ENV_FILE}" >/dev/null 2>&1
        fi
        if [[ "${backend_was_running}" == true ]]; then
            DOMEYE_CORE_ALLOW_ROLLBACK_CONFIG=true \
                DOMEYE_CORE_ROLLBACK_INFO_DIR="${previous_runtime_info_dir}" \
                "${BACKEND_ROOT}/deploy/start-backend.sh" >/dev/null 2>&1
        fi
    fi
    exit "${exit_code}"
}
trap rollback EXIT

changes_started=true
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
database_probe="$(docker exec \
    --env "PGPASSWORD=${DOMEYE_CORE_DB_READER_PASSWORD}" \
    "${DOMEYE_CORE_DATABASE_CONTAINER}" \
    psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_READER_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --command 'SHOW transaction_read_only; SELECT count(*) FROM public.feature_country;')"
if [[ "${database_probe%%$'\n'*}" != on || ! "${database_probe##*$'\n'}" =~ ^[1-9][0-9]*$ ]]; then
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
mv -- "${journal_tmp}" "${ACTIVATION_JOURNAL}"
rollback_needed=false
printf '数据库发布已激活，后端已重启：%s\n' "${RELEASE_ID}"
