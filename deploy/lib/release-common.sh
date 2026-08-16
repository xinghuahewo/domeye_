#!/usr/bin/env bash

# 发布命令状态机的共用只读校验。调用方必须先启用 set -Eeuo pipefail，
# 并先加载 artifact-common.sh、backend-common.sh 和 frontend-common.sh。
readonly DOMEYE_CORE_RELEASE_COMMAND_ROOT="${DOMEYE_CORE_RELEASE_STATE_DIR}/prepared"
readonly DOMEYE_CORE_RELEASE_COMMAND_LOCK="${DOMEYE_CORE_RELEASE_STATE_DIR}/release-command.lock"

domeye_release_require_root() {
    if (( EUID != 0 )); then
        domeye_artifact_error '发布准备、激活和回滚必须由 root 在指定发布机执行'
        return 1
    fi
}

domeye_release_host_identity() {
    hostname -f 2>/dev/null || hostname
}

domeye_release_require_host() {
    local expected_host="$1"
    local actual_host

    if [[ -z "${expected_host}" || "${expected_host}" == *[[:space:]]* ]]; then
        domeye_artifact_error '必须显式指定不含空白的发布机主机名'
        return 1
    fi
    actual_host="$(domeye_release_host_identity)"
    if [[ "${actual_host}" != "${expected_host}" ]]; then
        domeye_artifact_error "当前机器不是指定发布机：${actual_host} != ${expected_host}"
        return 1
    fi
}

domeye_release_require_mode() {
    local path="$1"
    local expected_mode="$2"
    local actual_mode

    domeye_artifact_require_regular_file "${path}" || return 1
    actual_mode="$(stat -c '%a' "${path}")"
    if [[ "${actual_mode}" != "${expected_mode}" ]]; then
        domeye_artifact_error "文件权限必须为 ${expected_mode}：${path}（当前 ${actual_mode}）"
        return 1
    fi
}

domeye_release_prepare_dir() {
    local release_id="$1"
    domeye_artifact_validate_release_id "${release_id}" || return 1
    printf '%s/%s\n' "${DOMEYE_CORE_RELEASE_COMMAND_ROOT}" "${release_id}"
}

domeye_release_state_file() {
    local release_id="$1"
    printf '%s/prepare-state.json\n' "$(domeye_release_prepare_dir "${release_id}")"
}

domeye_release_acquire_lock() {
    local operation="$1"
    local release_id="$2"
    local owner_tmp

    install -d -m 0750 "${DOMEYE_CORE_RELEASE_STATE_DIR}" "${DOMEYE_CORE_RELEASE_COMMAND_ROOT}"
    if [[ -L "${DOMEYE_CORE_RELEASE_STATE_DIR}" || -L "${DOMEYE_CORE_RELEASE_COMMAND_ROOT}" ]]; then
        domeye_artifact_error '发布状态根目录不能是软链接'
        return 1
    fi
    if ! mkdir -m 0700 "${DOMEYE_CORE_RELEASE_COMMAND_LOCK}" 2>/dev/null; then
        domeye_artifact_error "已有发布命令持有全局锁：${DOMEYE_CORE_RELEASE_COMMAND_LOCK}"
        return 1
    fi
    owner_tmp="${DOMEYE_CORE_RELEASE_COMMAND_LOCK}/.owner.json.tmp.$$"
    jq -n \
        --argjson schema_version 1 \
        --arg operation "${operation}" \
        --arg release_id "${release_id}" \
        --arg host "$(domeye_release_host_identity)" \
        --argjson pid "$$" \
        --arg started_at "$(domeye_artifact_iso_utc_now)" \
        '{
          schema_version: $schema_version,
          operation: $operation,
          release_id: $release_id,
          host: $host,
          pid: $pid,
          started_at: $started_at
        }' > "${owner_tmp}"
    chmod 0600 "${owner_tmp}"
    mv -T -- "${owner_tmp}" "${DOMEYE_CORE_RELEASE_COMMAND_LOCK}/owner.json"
}

domeye_release_release_lock() {
    local owner="${DOMEYE_CORE_RELEASE_COMMAND_LOCK}/owner.json"
    if [[ -f "${owner}" && ! -L "${owner}" ]]; then
        rm -f -- "${owner}"
    fi
    rmdir "${DOMEYE_CORE_RELEASE_COMMAND_LOCK}" 2>/dev/null || true
}

domeye_release_validate_paths() {
    local release_dir="$1"
    local hidden_path="$2"
    local database_env_file="$3"
    local release_real hidden_real env_real

    if [[ ! -d "${DOMEYE_CORE_ROOT}" || -L "${DOMEYE_CORE_ROOT}" \
        || "$(readlink -f -- "${DOMEYE_CORE_ROOT}")" != "${DOMEYE_CORE_ROOT}" ]]; then
        domeye_artifact_error "项目根目录不存在、越界或是软链接：${DOMEYE_CORE_ROOT}"
        return 1
    fi
    if [[ ! -d "${release_dir}" || -L "${release_dir}" ]]; then
        domeye_artifact_error "发布目录不存在或是软链接：${release_dir}"
        return 1
    fi
    release_real="$(readlink -f -- "${release_dir}")"
    if [[ "${release_real}" != "${DOMEYE_CORE_ARTIFACT_ROOT}/releases/"* ]]; then
        domeye_artifact_error "发布目录越界：${release_real}"
        return 1
    fi
    if [[ ! -d "${hidden_path}" || -L "${hidden_path}" ]]; then
        domeye_artifact_error "隔离验证目录不存在或是软链接：${hidden_path}"
        return 1
    fi
    hidden_real="$(readlink -f -- "${hidden_path}")"
    if [[ "${hidden_real}" == "${DOMEYE_CORE_ROOT}" \
        || "${hidden_real}" == "${DOMEYE_CORE_ROOT}/"* ]]; then
        domeye_artifact_error '待隐藏目录不能是 Domeye Core 项目或其子目录'
        return 1
    fi
    domeye_release_require_mode "${database_env_file}" 600 || return 1
    env_real="$(readlink -f -- "${database_env_file}")"
    if [[ "${env_real}" != '/home/bgpdata/Domeye-Core-data/config/database.env' ]]; then
        domeye_artifact_error "数据库配置不是固定独立库配置：${env_real}"
        return 1
    fi
}

domeye_release_require_clean_checkout() {
    local status_output
    status_output="$(git -C "${DOMEYE_CORE_ROOT}" status --porcelain --untracked-files=all)"
    if [[ -n "${status_output}" ]]; then
        domeye_artifact_error '发布机项目检出不是干净状态，拒绝生成或激活候选'
        return 1
    fi
}

domeye_release_inputs_json() {
    local release_dir="$1"
    local hidden_path="$2"
    local database_env_file="$3"
    local expected_host="$4"
    local manifest_path="${release_dir}/${DOMEYE_CORE_RELEASE_MANIFEST}"
    local checksums_path="${release_dir}/${DOMEYE_CORE_CHECKSUM_FILE}"
    local machine_id_path='/etc/machine-id'

    for input_path in \
        "${manifest_path}" \
        "${checksums_path}" \
        "${DOMEYE_CORE_ROOT}/contracts/openapi.json" \
        "${DOMEYE_CORE_ROOT}/dev/fixtures/api-snapshot.json" \
        "${DOMEYE_CORE_ROOT}/backend/core.sha256" \
        "${DOMEYE_CORE_ROOT}/deploy/nginx/domeye-core.conf" \
        "${DOMEYE_CORE_DATA_PROFILE_FILE}" \
        "${database_env_file}" \
        "${machine_id_path}"; do
        domeye_artifact_require_regular_file "${input_path}" || return 1
    done

    jq -n \
        --arg release_dir "$(readlink -f -- "${release_dir}")" \
        --arg hidden_path "$(readlink -f -- "${hidden_path}")" \
        --arg database_env_file "$(readlink -f -- "${database_env_file}")" \
        --arg expected_host "${expected_host}" \
        --arg host "$(domeye_release_host_identity)" \
        --arg machine_id_sha256 "$(domeye_artifact_sha256 "${machine_id_path}")" \
        --arg git_sha "$(git -C "${DOMEYE_CORE_ROOT}" rev-parse --verify HEAD)" \
        --arg manifest_sha256 "$(domeye_artifact_sha256 "${manifest_path}")" \
        --arg checksums_sha256 "$(domeye_artifact_sha256 "${checksums_path}")" \
        --arg database_env_sha256 "$(domeye_artifact_sha256 "${database_env_file}")" \
        --arg data_profile_sha256 "${DOMEYE_CORE_DATA_PROFILE_SHA256}" \
        --arg openapi_sha256 "$(domeye_artifact_sha256 "${DOMEYE_CORE_ROOT}/contracts/openapi.json")" \
        --arg fixture_sha256 "$(domeye_artifact_sha256 "${DOMEYE_CORE_ROOT}/dev/fixtures/api-snapshot.json")" \
        --arg core_manifest_sha256 "$(domeye_artifact_sha256 "${DOMEYE_CORE_ROOT}/backend/core.sha256")" \
        --arg nginx_sha256 "$(domeye_artifact_sha256 "${DOMEYE_CORE_ROOT}/deploy/nginx/domeye-core.conf")" \
        '{
          release_dir: $release_dir,
          hidden_path: $hidden_path,
          database_env_file: $database_env_file,
          expected_host: $expected_host,
          host: $host,
          machine_id_sha256: $machine_id_sha256,
          git_sha: $git_sha,
          manifest_sha256: $manifest_sha256,
          checksums_sha256: $checksums_sha256,
          database_env_sha256: $database_env_sha256,
          data_profile_sha256: $data_profile_sha256,
          openapi_sha256: $openapi_sha256,
          fixture_sha256: $fixture_sha256,
          core_manifest_sha256: $core_manifest_sha256,
          nginx_sha256: $nginx_sha256
        }'
}

domeye_release_json_sha256() {
    jq -S -c . | sha256sum | awk '{print $1}'
}

domeye_release_atomic_state() {
    local state_file="$1"
    local pending_file="${state_file}.tmp.$$"

    cat > "${pending_file}"
    chmod 0600 "${pending_file}"
    jq -e . "${pending_file}" >/dev/null
    mv -T -- "${pending_file}" "${state_file}"
}

domeye_release_validate_state_file() {
    local state_file="$1"
    domeye_release_require_mode "${state_file}" 600 || return 1
    domeye_artifact_json_file "${state_file}"
}
