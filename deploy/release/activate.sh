#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly DEPLOY_DIR="${SCRIPT_DIR}/.."
# shellcheck source=../lib/artifact-common.sh
source "${DEPLOY_DIR}/lib/artifact-common.sh"
# shellcheck source=../lib/backend-common.sh
source "${DEPLOY_DIR}/lib/backend-common.sh"
# shellcheck source=../lib/frontend-common.sh
source "${DEPLOY_DIR}/lib/frontend-common.sh"
# shellcheck source=../lib/database-common.sh
source "${DEPLOY_DIR}/lib/database-common.sh"
# shellcheck source=../lib/release-common.sh
source "${DEPLOY_DIR}/lib/release-common.sh"

domeye_core_require_realtime_profile || exit 1

if (( $# != 4 )); then
    printf '用法：CONFIRM_RELEASE_ID=<release-id> %s <发布目录> <待隐藏旧目录> <数据库配置> <发布机主机名>\n' \
        "${0##*/}" >&2
    exit 2
fi

readonly RELEASE_DIR="${1%/}"
readonly HIDDEN_PATH="${2%/}"
readonly DATABASE_ENV_FILE="$3"
readonly EXPECTED_HOST="$4"
readonly MANIFEST_PATH="${RELEASE_DIR}/${DOMEYE_CORE_RELEASE_MANIFEST}"
readonly NGINX_TARGET='/etc/nginx/conf.d/domeye-core.conf'

for command_name in awk cat diff git head hostname install jq mkdir mv nginx readlink sha256sum stat systemctl; do
    domeye_artifact_require_command "${command_name}"
done
domeye_release_require_root
domeye_release_require_host "${EXPECTED_HOST}"
domeye_release_validate_paths "${RELEASE_DIR}" "${HIDDEN_PATH}" "${DATABASE_ENV_FILE}"
domeye_release_require_clean_checkout
"${DEPLOY_DIR}/artifacts/verify-release.sh" "${RELEASE_DIR}"

readonly RELEASE_ID="$(jq -er '.release_id' "${MANIFEST_PATH}")"
domeye_artifact_validate_release_id "${RELEASE_ID}"
if [[ "${CONFIRM_RELEASE_ID:-}" != "${RELEASE_ID}" ]]; then
    domeye_artifact_error 'CONFIRM_RELEASE_ID 必须与候选状态中的 release-id 完全一致'
    exit 2
fi
if [[ "${RELEASE_DIR}" != "${DOMEYE_CORE_ARTIFACT_ROOT}/releases/${RELEASE_ID}" ]]; then
    domeye_artifact_error '发布目录与清单 release-id 不一致'
    exit 1
fi

readonly PREPARE_DIR="$(domeye_release_prepare_dir "${RELEASE_ID}")"
readonly STATE_FILE="$(domeye_release_state_file "${RELEASE_ID}")"
domeye_release_validate_state_file "${STATE_FILE}"

inputs_json="$(domeye_release_inputs_json \
    "${RELEASE_DIR}" "${HIDDEN_PATH}" "${DATABASE_ENV_FILE}" "${EXPECTED_HOST}")"
readonly INPUTS_JSON="${inputs_json}"
readonly INPUT_FINGERPRINT="$(printf '%s\n' "${INPUTS_JSON}" | domeye_release_json_sha256)"
unset inputs_json
if ! jq -e \
    --arg release_id "${RELEASE_ID}" \
    --arg stage 'prepared' \
    --arg fingerprint "${INPUT_FINGERPRINT}" \
    --arg prepare_dir "${PREPARE_DIR}" \
    '.schema_version == 1
     and .release_id == $release_id
     and .stage == $stage
     and .input_fingerprint == $fingerprint
     and .prepare_dir == $prepare_dir
     and (.completed_gates | index("candidate_verified")) != null' \
    "${STATE_FILE}" >/dev/null \
    || ! diff -u \
        <(jq -S . <<< "${INPUTS_JSON}") \
        <(jq -S '.inputs' "${STATE_FILE}") \
        >/dev/null; then
    domeye_artifact_error '候选准备状态与当前发布输入不一致，拒绝激活'
    exit 1
fi

readonly FRONTEND_DIST="$(jq -r '.frontend.dist' "${STATE_FILE}")"
readonly EXPECTED_FRONTEND_SHA="$(jq -r '.frontend.tree_sha256' "${STATE_FILE}")"
readonly FRONTEND_CHECKPOINT="$(jq -r '.frontend.checkpoint' "${STATE_FILE}")"
readonly EXPECTED_FRONTEND_CHECKPOINT_SHA="$(jq -r '.frontend.checkpoint_sha256' "${STATE_FILE}")"
readonly RESTORE_STATE="$(jq -r '.database.restore_state' "${STATE_FILE}")"
readonly EXPECTED_RESTORE_SHA="$(jq -r '.database.restore_state_sha256' "${STATE_FILE}")"
readonly EXPECTED_SYSTEM_IDENTIFIER="$(jq -r '.database.system_identifier' "${STATE_FILE}")"
if [[ "${FRONTEND_DIST}" != "${PREPARE_DIR}/frontend-dist" \
    || "${FRONTEND_CHECKPOINT}" != "${PREPARE_DIR}/frontend-build.json" \
    || ! "${EXPECTED_FRONTEND_SHA}" =~ ^[0-9a-f]{64}$ \
    || ! "${EXPECTED_FRONTEND_CHECKPOINT_SHA}" =~ ^[0-9a-f]{64}$ ]]; then
    domeye_artifact_error '候选前端状态字段无效'
    exit 1
fi
domeye_frontend_validate_tree "${FRONTEND_DIST}"
domeye_release_require_mode "${FRONTEND_CHECKPOINT}" 600
if [[ "$(domeye_artifact_sha256 "${FRONTEND_CHECKPOINT}")" != "${EXPECTED_FRONTEND_CHECKPOINT_SHA}" \
    || "$(domeye_frontend_tree_sha256 "${FRONTEND_DIST}")" != "${EXPECTED_FRONTEND_SHA}" ]] \
    || ! jq -e \
        --arg release_id "${RELEASE_ID}" \
        --arg fingerprint "${INPUT_FINGERPRINT}" \
        --arg dist "${FRONTEND_DIST}" \
        --arg tree_sha256 "${EXPECTED_FRONTEND_SHA}" \
        '.schema_version == 1
         and .release_id == $release_id
         and .input_fingerprint == $fingerprint
         and .dist == $dist
         and .tree_sha256 == $tree_sha256' \
        "${FRONTEND_CHECKPOINT}" >/dev/null; then
    domeye_artifact_error 'prepare 后候选前端制品发生变化'
    exit 1
fi
if [[ "${RESTORE_STATE}" != "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/${RELEASE_ID}/restore-state.json" \
    || ! "${EXPECTED_RESTORE_SHA}" =~ ^[0-9a-f]{64}$ \
    || ! "${EXPECTED_SYSTEM_IDENTIFIER}" =~ ^[0-9]+$ ]]; then
    domeye_artifact_error '候选数据库状态字段无效'
    exit 1
fi
domeye_release_require_mode "${RESTORE_STATE}" 600
if [[ "$(domeye_artifact_sha256 "${RESTORE_STATE}")" != "${EXPECTED_RESTORE_SHA}" ]] \
    || ! jq -e --arg release_id "${RELEASE_ID}" --arg system_identifier "${EXPECTED_SYSTEM_IDENTIFIER}" \
        '.schema_version == 1 and .phase == "verified"
         and .release_id == $release_id
         and .system_identifier == $system_identifier' \
        "${RESTORE_STATE}" >/dev/null; then
    domeye_artifact_error 'prepare 后数据库恢复状态发生变化'
    exit 1
fi

lock_owned=false
activation_complete=false
activation_nonce=''
cleanup() {
    local exit_code=$?
    if [[ "${activation_complete}" != true && -n "${activation_nonce}" \
        && -f "${STATE_FILE}" && ! -L "${STATE_FILE}" ]]; then
        if jq -e --arg nonce "${activation_nonce}" \
            '.stage == "activating" and .activation.nonce == $nonce' \
            "${STATE_FILE}" >/dev/null 2>&1; then
            jq \
                --arg stage 'activation_failed' \
                --arg failed_at "$(domeye_artifact_iso_utc_now)" \
                --argjson exit_code "${exit_code}" \
                '.stage = $stage
                 | .activation.nonce = null
                 | .activation.failed_at = $failed_at
                 | .activation.exit_code = $exit_code
                 | .updated_at = $failed_at' \
                "${STATE_FILE}" | domeye_release_atomic_state "${STATE_FILE}" || true
        fi
    fi
    if [[ "${lock_owned}" == true ]]; then
        domeye_release_release_lock
    fi
    return "${exit_code}"
}
trap cleanup EXIT

domeye_release_acquire_lock activate "${RELEASE_ID}"
lock_owned=true

nginx_previous_existed=false
nginx_previous_sha=''
nginx_backup=''
if [[ -f "${NGINX_TARGET}" && ! -L "${NGINX_TARGET}" ]]; then
    nginx_previous_existed=true
    nginx_backup="${PREPARE_DIR}/nginx-before-activate.conf"
    if [[ -e "${nginx_backup}" || -L "${nginx_backup}" ]]; then
        domeye_artifact_error '发现未由当前 prepared 状态绑定的 Nginx 激活备份'
        exit 1
    fi
    install -m 0600 "${NGINX_TARGET}" "${nginx_backup}"
    nginx_previous_sha="$(domeye_artifact_sha256 "${nginx_backup}")"
elif [[ -e "${NGINX_TARGET}" || -L "${NGINX_TARGET}" ]]; then
    domeye_artifact_error "Nginx 目标不是普通文件：${NGINX_TARGET}"
    exit 1
fi
nginx_was_active=false
if systemctl is-active --quiet nginx; then
    nginx_was_active=true
fi

activation_nonce="$(head -c 32 /dev/urandom | sha256sum | awk '{print $1}')"
if [[ ! "${activation_nonce}" =~ ^[0-9a-f]{64}$ ]]; then
    domeye_artifact_error '无法生成发布激活随机令牌'
    exit 1
fi
jq \
    --arg stage 'activating' \
    --arg nonce "${activation_nonce}" \
    --arg started_at "$(domeye_artifact_iso_utc_now)" \
    --arg nginx_target "${NGINX_TARGET}" \
    --arg nginx_backup "${nginx_backup}" \
    --arg nginx_previous_sha "${nginx_previous_sha}" \
    --argjson nginx_previous_existed "${nginx_previous_existed}" \
    --argjson nginx_was_active "${nginx_was_active}" \
    '.stage = $stage
     | .activation = {
         nonce: $nonce,
         started_at: $started_at,
         nginx: {
           target: $nginx_target,
           previous_existed: $nginx_previous_existed,
           previous_backup: (if $nginx_backup == "" then null else $nginx_backup end),
           previous_sha256: (if $nginx_previous_sha == "" then null else $nginx_previous_sha end),
           was_active: $nginx_was_active
         }
       }
     | .updated_at = $started_at' \
    "${STATE_FILE}" | domeye_release_atomic_state "${STATE_FILE}"

DOMEYE_CORE_RELEASE_ACTIVATION=1 \
DOMEYE_CORE_RELEASE_ACTIVATION_NONCE="${activation_nonce}" \
DOMEYE_CORE_RELEASE_PREPARE_STATE="${STATE_FILE}" \
DOMEYE_CORE_CANDIDATE_FRONTEND_DIST="${FRONTEND_DIST}" \
    "${DEPLOY_DIR}/acceptance/full-acceptance.sh" \
    "${RELEASE_DIR}" "${HIDDEN_PATH}" "${DATABASE_ENV_FILE}"

installed_nginx_sha="$(domeye_artifact_sha256 "${NGINX_TARGET}")"
jq \
    --arg stage 'active' \
    --arg gate 'activated' \
    --arg activated_at "$(domeye_artifact_iso_utc_now)" \
    --arg installed_nginx_sha "${installed_nginx_sha}" \
    '.stage = $stage
     | .completed_gates = ((.completed_gates + [$gate]) | unique)
     | .activation.nonce = null
     | .activation.activated_at = $activated_at
     | .activation.nginx.installed_sha256 = $installed_nginx_sha
     | .updated_at = $activated_at' \
    "${STATE_FILE}" | domeye_release_atomic_state "${STATE_FILE}"
activation_complete=true

printf '发布已完成显式激活：%s\n' "${RELEASE_ID}"
