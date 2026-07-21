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
    printf '用法：%s <发布目录> <待隐藏旧目录> <数据库配置> <发布机主机名>\n' "${0##*/}" >&2
    exit 2
fi

readonly RELEASE_DIR="${1%/}"
readonly HIDDEN_PATH="${2%/}"
readonly DATABASE_ENV_FILE="$3"
readonly EXPECTED_HOST="$4"
readonly MANIFEST_PATH="${RELEASE_DIR}/${DOMEYE_CORE_RELEASE_MANIFEST}"

for command_name in awk cat diff git hostname install jq mkdir mktemp mv npm python3 readlink rm sha256sum stat; do
    domeye_artifact_require_command "${command_name}"
done
domeye_release_require_root
domeye_release_require_host "${EXPECTED_HOST}"
domeye_release_validate_paths "${RELEASE_DIR}" "${HIDDEN_PATH}" "${DATABASE_ENV_FILE}"
domeye_release_require_clean_checkout
"${DEPLOY_DIR}/artifacts/verify-release.sh" "${RELEASE_DIR}"

readonly RELEASE_ID="$(jq -er '.release_id' "${MANIFEST_PATH}")"
domeye_artifact_validate_release_id "${RELEASE_ID}"
if [[ "${RELEASE_DIR}" != "${DOMEYE_CORE_ARTIFACT_ROOT}/releases/${RELEASE_ID}" ]]; then
    domeye_artifact_error "发布目录与清单 release-id 不一致：${RELEASE_DIR}"
    exit 1
fi

lock_owned=false
build_temp=''
cleanup() {
    local exit_code=$?
    if [[ -n "${build_temp}" && "${build_temp}" == "${PREPARE_DIR}/.frontend-dist.tmp."* \
        && -d "${build_temp}" && ! -L "${build_temp}" ]]; then
        rm -rf -- "${build_temp}"
    fi
    if [[ "${lock_owned}" == true ]]; then
        domeye_release_release_lock
    fi
    if (( exit_code != 0 )); then
        printf '发布准备失败；已完成检查点和候选数据库均保留，可在输入指纹不变时重试：%s\n' \
            "${RELEASE_ID}" >&2
    fi
    return "${exit_code}"
}
trap cleanup EXIT

domeye_release_acquire_lock prepare "${RELEASE_ID}"
lock_owned=true

readonly PREPARE_DIR="$(domeye_release_prepare_dir "${RELEASE_ID}")"
readonly STATE_FILE="$(domeye_release_state_file "${RELEASE_ID}")"
readonly FRONTEND_DIST="${PREPARE_DIR}/frontend-dist"
readonly FRONTEND_CHECKPOINT="${PREPARE_DIR}/frontend-build.json"
if [[ -e "${PREPARE_DIR}" || -L "${PREPARE_DIR}" ]]; then
    if [[ ! -d "${PREPARE_DIR}" || -L "${PREPARE_DIR}" ]]; then
        domeye_artifact_error "候选准备目录不是实际目录：${PREPARE_DIR}"
        exit 1
    fi
else
    install -d -m 0750 "${PREPARE_DIR}"
fi

inputs_json="$(domeye_release_inputs_json \
    "${RELEASE_DIR}" "${HIDDEN_PATH}" "${DATABASE_ENV_FILE}" "${EXPECTED_HOST}")"
readonly INPUTS_JSON="${inputs_json}"
readonly INPUT_FINGERPRINT="$(printf '%s\n' "${INPUTS_JSON}" | domeye_release_json_sha256)"
unset inputs_json

if [[ -e "${STATE_FILE}" || -L "${STATE_FILE}" ]]; then
    domeye_release_validate_state_file "${STATE_FILE}"
    if ! jq -e \
        --arg release_id "${RELEASE_ID}" \
        --arg fingerprint "${INPUT_FINGERPRINT}" \
        --arg prepare_dir "${PREPARE_DIR}" \
        '(.schema_version == 1)
         and .release_id == $release_id
         and .input_fingerprint == $fingerprint
         and .prepare_dir == $prepare_dir
         and (.stage == "inputs_verified"
              or .stage == "database_verified"
              or .stage == "code_verified"
              or .stage == "frontend_built"
              or .stage == "prepared")
         and (.completed_gates | type) == "array"' \
        "${STATE_FILE}" >/dev/null \
        || ! diff -u \
            <(jq -S . <<< "${INPUTS_JSON}") \
            <(jq -S '.inputs' "${STATE_FILE}") \
            >/dev/null; then
        domeye_artifact_error '既有发布准备状态与当前输入指纹不一致，拒绝覆盖或猜测续跑点'
        exit 1
    fi
else
    if [[ -e "${FRONTEND_DIST}" || -L "${FRONTEND_DIST}" \
        || -e "${FRONTEND_CHECKPOINT}" || -L "${FRONTEND_CHECKPOINT}" ]]; then
        domeye_artifact_error '发现没有状态文件绑定的前端候选或检查点，必须先人工复核或使用安全 GC'
        exit 1
    fi
    jq -n \
        --argjson schema_version 1 \
        --arg release_id "${RELEASE_ID}" \
        --arg stage 'inputs_verified' \
        --arg prepare_dir "${PREPARE_DIR}" \
        --arg state_file "${STATE_FILE}" \
        --arg input_fingerprint "${INPUT_FINGERPRINT}" \
        --arg created_at "$(domeye_artifact_iso_utc_now)" \
        --argjson inputs "${INPUTS_JSON}" \
        '{
          schema_version: $schema_version,
          release_id: $release_id,
          stage: $stage,
          prepare_dir: $prepare_dir,
          state_file: $state_file,
          input_fingerprint: $input_fingerprint,
          inputs: $inputs,
          completed_gates: ["inputs_verified"],
          database: null,
          frontend: null,
          activation: null,
          created_at: $created_at,
          updated_at: $created_at
        }' | domeye_release_atomic_state "${STATE_FILE}"
fi

stage="$(jq -r '.stage' "${STATE_FILE}")"
validate_frontend_checkpoint() {
    local expected_tree expected_checkpoint_sha
    domeye_release_require_mode "${FRONTEND_CHECKPOINT}" 600 || return 1
    expected_tree="$(jq -r '.frontend.tree_sha256 // empty' "${STATE_FILE}")"
    expected_checkpoint_sha="$(jq -r '.frontend.checkpoint_sha256 // empty' "${STATE_FILE}")"
    if [[ ! "${expected_tree}" =~ ^[0-9a-f]{64}$ \
        || ! "${expected_checkpoint_sha}" =~ ^[0-9a-f]{64}$ \
        || "$(domeye_artifact_sha256 "${FRONTEND_CHECKPOINT}")" != "${expected_checkpoint_sha}" ]] \
        || ! jq -e \
            --arg release_id "${RELEASE_ID}" \
            --arg fingerprint "${INPUT_FINGERPRINT}" \
            --arg dist "${FRONTEND_DIST}" \
            --arg tree_sha256 "${expected_tree}" \
            '.schema_version == 1
             and .release_id == $release_id
             and .input_fingerprint == $fingerprint
             and .dist == $dist
             and .tree_sha256 == $tree_sha256' \
            "${FRONTEND_CHECKPOINT}" >/dev/null; then
        domeye_artifact_error '前端构建完成检查点与 prepared 状态不一致'
        return 1
    fi
    domeye_frontend_validate_tree "${FRONTEND_DIST}" || return 1
    if [[ "$(domeye_frontend_tree_sha256 "${FRONTEND_DIST}")" != "${expected_tree}" ]]; then
        domeye_artifact_error '前端候选目录与构建完成检查点哈希不一致'
        return 1
    fi
}
validate_recorded_database() {
    local restore_state expected_sha expected_system
    restore_state="$(jq -r '.database.restore_state // empty' "${STATE_FILE}")"
    expected_sha="$(jq -r '.database.restore_state_sha256 // empty' "${STATE_FILE}")"
    expected_system="$(jq -r '.database.system_identifier // empty' "${STATE_FILE}")"
    if [[ "${restore_state}" != "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/${RELEASE_ID}/restore-state.json" \
        || ! "${expected_sha}" =~ ^[0-9a-f]{64}$ \
        || ! "${expected_system}" =~ ^[0-9]+$ ]]; then
        domeye_artifact_error '发布准备状态中的数据库检查点字段无效'
        return 1
    fi
    domeye_release_require_mode "${restore_state}" 600 || return 1
    if [[ "$(domeye_artifact_sha256 "${restore_state}")" != "${expected_sha}" ]] \
        || ! jq -e --arg release_id "${RELEASE_ID}" --arg system_identifier "${expected_system}" \
            '.schema_version == 1 and .phase == "verified"
             and .release_id == $release_id
             and .system_identifier == $system_identifier' \
            "${restore_state}" >/dev/null; then
        domeye_artifact_error '已记录的数据库恢复状态发生变化或不再是 verified'
        return 1
    fi
}
if [[ "${stage}" != 'inputs_verified' ]]; then
    validate_recorded_database
fi
if [[ "${stage}" == 'prepared' ]]; then
    validate_frontend_checkpoint
    printf '发布候选此前已准备完成且输入指纹未变化：%s\n' "${RELEASE_ID}"
    exit 0
fi

if [[ "${stage}" == 'inputs_verified' ]]; then
    "${DEPLOY_DIR}/database/restore-database.sh" "${RELEASE_DIR}" "${DATABASE_ENV_FILE}"
    restore_state="${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/${RELEASE_ID}/restore-state.json"
    domeye_release_require_mode "${restore_state}" 600
    if ! jq -e --arg release_id "${RELEASE_ID}" \
        '.schema_version == 1 and .phase == "verified" and .release_id == $release_id
         and (.system_identifier | test("^[0-9]+$"))' "${restore_state}" >/dev/null; then
        domeye_artifact_error '数据库恢复状态未达到 verified'
        exit 1
    fi
    jq \
        --arg stage 'database_verified' \
        --arg gate 'database_verified' \
        --arg state_file "${restore_state}" \
        --arg state_sha256 "$(domeye_artifact_sha256 "${restore_state}")" \
        --arg system_identifier "$(jq -r '.system_identifier' "${restore_state}")" \
        --arg updated_at "$(domeye_artifact_iso_utc_now)" \
        '.stage = $stage
         | .completed_gates = ((.completed_gates + [$gate]) | unique)
         | .database = {
             restore_state: $state_file,
             restore_state_sha256: $state_sha256,
             system_identifier: $system_identifier
           }
         | .updated_at = $updated_at' \
        "${STATE_FILE}" | domeye_release_atomic_state "${STATE_FILE}"
    stage='database_verified'
fi

if [[ "${stage}" == 'database_verified' ]]; then
    (
        cd -- "${DOMEYE_CORE_ROOT}"
        export PATH="${DOMEYE_CORE_RUNTIME_PATH}"
        python3 dev/checks.py release
    )
    domeye_release_require_clean_checkout
    jq \
        --arg stage 'code_verified' \
        --arg gate 'code_verified' \
        --arg updated_at "$(domeye_artifact_iso_utc_now)" \
        '.stage = $stage
         | .completed_gates = ((.completed_gates + [$gate]) | unique)
         | .updated_at = $updated_at' \
        "${STATE_FILE}" | domeye_release_atomic_state "${STATE_FILE}"
    stage='code_verified'
fi

if [[ "${stage}" == 'code_verified' ]]; then
    if [[ -e "${FRONTEND_DIST}" || -L "${FRONTEND_DIST}" ]]; then
        if [[ ! -f "${FRONTEND_CHECKPOINT}" || -L "${FRONTEND_CHECKPOINT}" ]]; then
            domeye_artifact_error \
                '前端候选已出现但缺少可信构建完成检查点，拒绝猜测崩溃窗口'
            exit 1
        fi
        domeye_release_require_mode "${FRONTEND_CHECKPOINT}" 600
        checkpoint_tree_sha="$(jq -r '.tree_sha256 // empty' "${FRONTEND_CHECKPOINT}")"
        if [[ ! "${checkpoint_tree_sha}" =~ ^[0-9a-f]{64}$ ]] \
            || ! jq -e \
                --arg release_id "${RELEASE_ID}" \
                --arg fingerprint "${INPUT_FINGERPRINT}" \
                --arg dist "${FRONTEND_DIST}" \
                '.schema_version == 1
                 and .release_id == $release_id
                 and .input_fingerprint == $fingerprint
                 and .dist == $dist' \
                "${FRONTEND_CHECKPOINT}" >/dev/null \
            || [[ "$(domeye_frontend_tree_sha256 "${FRONTEND_DIST}")" != "${checkpoint_tree_sha}" ]]; then
            domeye_artifact_error '既有前端候选与构建完成检查点不一致'
            exit 1
        fi
        frontend_tree_sha="${checkpoint_tree_sha}"
    else
        if [[ -e "${FRONTEND_CHECKPOINT}" || -L "${FRONTEND_CHECKPOINT}" ]]; then
            domeye_artifact_error '存在前端构建检查点但候选目录缺失'
            exit 1
        fi
        build_temp="$(mktemp -d "${PREPARE_DIR}/.frontend-dist.tmp.XXXXXX")"
        (
            cd -- "${DOMEYE_CORE_ROOT}/frontend"
            export PATH="${DOMEYE_CORE_RUNTIME_PATH}"
            [[ "$(node --version)" == 'v22.23.1' ]]
            npm run build -- --outDir "${build_temp}" --emptyOutDir
        )
        chmod -R u=rwX,go=rX "${build_temp}"
        domeye_frontend_validate_tree "${build_temp}"
        frontend_tree_sha="$(domeye_frontend_tree_sha256 "${build_temp}")"
        mv -T -- "${build_temp}" "${FRONTEND_DIST}"
        build_temp=''
        jq -n \
            --argjson schema_version 1 \
            --arg release_id "${RELEASE_ID}" \
            --arg input_fingerprint "${INPUT_FINGERPRINT}" \
            --arg dist "${FRONTEND_DIST}" \
            --arg tree_sha256 "${frontend_tree_sha}" \
            --arg built_at "$(domeye_artifact_iso_utc_now)" \
            '{
              schema_version: $schema_version,
              release_id: $release_id,
              input_fingerprint: $input_fingerprint,
              dist: $dist,
              tree_sha256: $tree_sha256,
              built_at: $built_at
            }' | domeye_release_atomic_state "${FRONTEND_CHECKPOINT}"
    fi
    frontend_checkpoint_sha="$(domeye_artifact_sha256 "${FRONTEND_CHECKPOINT}")"
    jq \
        --arg stage 'frontend_built' \
        --arg gate 'frontend_built' \
        --arg dist "${FRONTEND_DIST}" \
        --arg tree_sha256 "${frontend_tree_sha}" \
        --arg checkpoint "${FRONTEND_CHECKPOINT}" \
        --arg checkpoint_sha256 "${frontend_checkpoint_sha}" \
        --arg updated_at "$(domeye_artifact_iso_utc_now)" \
        '.stage = $stage
         | .completed_gates = ((.completed_gates + [$gate]) | unique)
         | .frontend = {
             dist: $dist,
             tree_sha256: $tree_sha256,
             checkpoint: $checkpoint,
             checkpoint_sha256: $checkpoint_sha256
           }
         | .updated_at = $updated_at' \
        "${STATE_FILE}" | domeye_release_atomic_state "${STATE_FILE}"
    stage='frontend_built'
fi

if [[ "${stage}" == 'frontend_built' ]]; then
    validate_frontend_checkpoint
    expected_tree_sha="$(jq -r '.frontend.tree_sha256' "${STATE_FILE}")"
    if [[ "$(domeye_frontend_tree_sha256 "${FRONTEND_DIST}")" != "${expected_tree_sha}" ]]; then
        domeye_artifact_error '候选验收前前端目录已发生变化'
        exit 1
    fi
    DOMEYE_CORE_CANDIDATE_FRONTEND_DIST="${FRONTEND_DIST}" \
        "${DEPLOY_DIR}/acceptance/candidate-stack.sh" \
        "${RELEASE_DIR}" "${DATABASE_ENV_FILE}" "${HIDDEN_PATH}"
    jq \
        --arg stage 'prepared' \
        --arg gate 'candidate_verified' \
        --arg prepared_at "$(domeye_artifact_iso_utc_now)" \
        '.stage = $stage
         | .completed_gates = ((.completed_gates + [$gate]) | unique)
         | .prepared_at = $prepared_at
         | .updated_at = $prepared_at' \
        "${STATE_FILE}" | domeye_release_atomic_state "${STATE_FILE}"
fi

printf '发布候选准备完成，未切换生产流量：%s\n' "${RELEASE_ID}"
