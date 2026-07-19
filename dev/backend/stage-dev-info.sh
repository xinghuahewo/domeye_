#!/usr/bin/env bash

set -Eeuo pipefail

readonly ARTIFACT_ROOT='/home/bgpdata/Domeye-Core-artifacts'
readonly API_ROOT='/home/bgpdata/Domeye-Core-dev-data/api'
readonly INFO_DIR="${API_ROOT}/info"
readonly INSTALLED_MANIFEST="${API_ROOT}/info-manifest.json"
readonly STAGE_LOCK="${API_ROOT}/.info-stage.lock"
readonly DATA_START='2026-02-01 00:00:00'
readonly INFO_ARCHIVE_NAME='info.tar.zst'
readonly INFO_MANIFEST_NAME='info-manifest.json'
readonly RELEASE_MANIFEST_NAME='manifest.json'
readonly -a INFO_FILES_SORTED=(
    'as_entity.csv'
    'country.xlsx'
    'important_as.csv'
    'ip_bgp_entity.csv'
)

STAGE_WORK_DIR=''

error() {
    printf '错误：%s\n' "$*" >&2
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        error "缺少命令：$1"
        return 1
    }
}

require_root() {
    if (( EUID != 0 )); then
        error '开发信息制品必须由 root 安装或复验'
        return 1
    fi
}

ensure_api_root() {
    local data_root='/home/bgpdata/Domeye-Core-dev-data'
    local mode
    if [[ ! -d "${data_root}" || -L "${data_root}" \
        || "$(readlink -f "${data_root}")" != "${data_root}" \
        || "$(stat -c '%u' "${data_root}")" != '0' ]]; then
        error "开发数据根目录不存在、越界或非 root 拥有：${data_root}"
        return 1
    fi
    mode="$(stat -c '%a' "${data_root}")"
    if (( (8#${mode} & 8#022) != 0 )); then
        error "开发数据根目录不得被组或其他用户写入：${data_root}"
        return 1
    fi
    if [[ -e "${API_ROOT}" || -L "${API_ROOT}" ]]; then
        if [[ ! -d "${API_ROOT}" || -L "${API_ROOT}" \
            || "$(readlink -f "${API_ROOT}")" != "${API_ROOT}" \
            || "$(stat -c '%u' "${API_ROOT}")" != '0' ]]; then
            error "开发 API 根目录不安全：${API_ROOT}"
            return 1
        fi
        mode="$(stat -c '%a' "${API_ROOT}")"
        if (( (8#${mode} & 8#022) != 0 )); then
            error "开发 API 根目录不得被组或其他用户写入：${API_ROOT}"
            return 1
        fi
        return 0
    fi
    install -d -o 0 -g 0 -m 0750 "${API_ROOT}"
}

require_regular_file() {
    local path="$1"
    if [[ ! -f "${path}" || -L "${path}" ]]; then
        error "要求普通文件且禁止软链接：${path}"
        return 1
    fi
}

require_trusted_directory() {
    local path="$1"
    local mode
    if [[ ! -d "${path}" || -L "${path}" \
        || "$(readlink -f "${path}")" != "${path}" \
        || "$(stat -c '%u' "${path}")" != '0' ]]; then
        error "目录必须是 root 拥有的实际目录：${path}"
        return 1
    fi
    mode="$(stat -c '%a' "${path}")"
    if (( (8#${mode} & 8#022) != 0 )); then
        error "目录不得被组或其他用户写入：${path}"
        return 1
    fi
}

require_trusted_file() {
    local path="$1"
    local mode
    require_regular_file "${path}" || return 1
    if [[ "$(readlink -f "${path}")" != "${path}" \
        || "$(stat -c '%u' "${path}")" != '0' ]]; then
        error "文件必须是 root 拥有的实际普通文件：${path}"
        return 1
    fi
    mode="$(stat -c '%a' "${path}")"
    if (( (8#${mode} & 8#022) != 0 )); then
        error "文件不得被组或其他用户写入：${path}"
        return 1
    fi
}

cleanup_stage_work_dir() {
    local exit_code=$?
    if [[ -n "${STAGE_WORK_DIR}" \
        && "${STAGE_WORK_DIR}" == "${API_ROOT}/.info-stage-"* \
        && -d "${STAGE_WORK_DIR}" && ! -L "${STAGE_WORK_DIR}" ]]; then
        rm -rf -- "${STAGE_WORK_DIR}"
    fi
    STAGE_WORK_DIR=''
    return "${exit_code}"
}

sha256_file() {
    sha256sum "$1" | awk '{print $1}'
}

validate_release_id() {
    local release_id="$1"
    if [[ ! "${release_id}" =~ ^[0-9]{8}T[0-9]{6}Z(-[a-z0-9][a-z0-9._-]{0,47})?$ ]]; then
        error "release-id 格式无效：${release_id}"
        return 1
    fi
}

validate_manifest_shape() {
    local manifest="$1"
    require_trusted_file "${manifest}" || return 1
    if ! jq -e \
        --arg data_start "${DATA_START}" \
        --arg archive_name "${INFO_ARCHIVE_NAME}" \
        '.schema_version == 1
         and .component == "info"
         and (.release_id | type) == "string"
         and (.created_at | type) == "string"
         and .data_start == $data_start
         and .archive.name == $archive_name
         and (.archive.sha256 | test("^[0-9a-f]{64}$"))
         and (.archive.size | type) == "number" and .archive.size >= 0
         and (.files | type) == "array" and (.files | length) == 4
         and all(.files[];
             (.name | type) == "string"
             and (.sha256 | test("^[0-9a-f]{64}$"))
             and (.size | type) == "number" and .size >= 0
             and (.record_count | type) == "number" and .record_count >= 0)' \
        "${manifest}" >/dev/null; then
        error "信息组件清单结构无效：${manifest}"
        return 1
    fi

    local manifest_names_text
    manifest_names_text="$(jq -r '.files[].name' "${manifest}" | LC_ALL=C sort)" || return 1
    local -a manifest_names
    mapfile -t manifest_names <<<"${manifest_names_text}"
    if (( ${#manifest_names[@]} != ${#INFO_FILES_SORTED[@]} )); then
        error '信息组件清单不是四文件白名单'
        return 1
    fi
    local index
    for index in "${!INFO_FILES_SORTED[@]}"; do
        if [[ "${manifest_names[index]}" != "${INFO_FILES_SORTED[index]}" ]]; then
            error "信息组件清单包含非白名单文件：${manifest_names[index]}"
            return 1
        fi
    done
}

validate_payload_dir() {
    local payload_dir="${1%/}"
    local manifest="$2"
    require_trusted_directory "${payload_dir}" || return 1

    local actual_names_text
    actual_names_text="$(
        find "${payload_dir}" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort
    )" || return 1
    local -a actual_names
    mapfile -t actual_names <<<"${actual_names_text}"
    if (( ${#actual_names[@]} != ${#INFO_FILES_SORTED[@]} )); then
        error "开发 INFO_DIR 必须且只能包含四个白名单文件：${payload_dir}"
        return 1
    fi

    local index file_name file_path expected_sha expected_size
    for index in "${!INFO_FILES_SORTED[@]}"; do
        file_name="${INFO_FILES_SORTED[index]}"
        if [[ "${actual_names[index]}" != "${file_name}" ]]; then
            error "开发 INFO_DIR 存在非白名单成员：${actual_names[index]}"
            return 1
        fi
        file_path="${payload_dir}/${file_name}"
        require_trusted_file "${file_path}" || return 1
        expected_sha="$(jq -er --arg name "${file_name}" '.files[] | select(.name == $name) | .sha256' "${manifest}")" || return 1
        expected_size="$(jq -er --arg name "${file_name}" '.files[] | select(.name == $name) | .size' "${manifest}")" || return 1
        if [[ "$(stat -c '%s' "${file_path}")" != "${expected_size}" \
            || "$(sha256_file "${file_path}")" != "${expected_sha}" ]]; then
            error "开发信息文件大小或 SHA256 不一致：${file_name}"
            return 1
        fi
    done
}

verify_installed() {
    validate_manifest_shape "${INSTALLED_MANIFEST}" || return 1
    validate_payload_dir "${INFO_DIR}" "${INSTALLED_MANIFEST}" || return 1
}

validate_component_pair() {
    local release_manifest="$1"
    local info_manifest="$2"
    require_trusted_file "${release_manifest}" || return 1
    validate_manifest_shape "${info_manifest}" || return 1
    if ! jq -e --slurpfile info "${info_manifest}" \
        --arg data_start "${DATA_START}" \
        '.schema_version == 1
         and .release_id == $info[0].release_id
         and .data_start == $data_start
         and .info == $info[0]' \
        "${release_manifest}" >/dev/null; then
        error 'manifest.json 内嵌的 info 组件与 info-manifest.json 不一致'
        return 1
    fi
}

installed_matches_release() {
    local desired_manifest="$1"
    [[ -f "${INSTALLED_MANIFEST}" && ! -L "${INSTALLED_MANIFEST}" ]] || return 1
    validate_manifest_shape "${INSTALLED_MANIFEST}" || return 1
    if ! diff -q \
        <(jq -S . "${INSTALLED_MANIFEST}") \
        <(jq -S . "${desired_manifest}") \
        >/dev/null; then
        return 1
    fi
    validate_payload_dir "${INFO_DIR}" "${desired_manifest}" || return 1
}

validate_archive_members() {
    local archive="$1"
    local archive_names_text archive_lines_text
    archive_names_text="$(
        zstd --quiet --decompress --stdout "${archive}" \
            | tar --list --file=- \
            | LC_ALL=C sort
    )" || {
        error '无法读取信息归档成员'
        return 1
    }
    local -a archive_names archive_lines
    mapfile -t archive_names <<<"${archive_names_text}"
    if (( ${#archive_names[@]} != ${#INFO_FILES_SORTED[@]} )); then
        error '信息归档成员数量不是四个'
        return 1
    fi
    local index
    for index in "${!INFO_FILES_SORTED[@]}"; do
        if [[ "${archive_names[index]}" != "${INFO_FILES_SORTED[index]}" ]]; then
            error "信息归档包含非白名单成员：${archive_names[index]}"
            return 1
        fi
    done

    archive_lines_text="$(
        zstd --quiet --decompress --stdout "${archive}" \
            | LC_ALL=C tar --list --verbose --numeric-owner --file=-
    )" || {
        error '无法读取信息归档成员类型'
        return 1
    }
    mapfile -t archive_lines <<<"${archive_lines_text}"
    if (( ${#archive_lines[@]} != ${#INFO_FILES_SORTED[@]} )); then
        error '信息归档详细成员数量异常'
        return 1
    fi
    local line
    for line in "${archive_lines[@]}"; do
        if [[ "${line:0:1}" != '-' ]]; then
            error '信息归档包含非普通文件或软链接'
            return 1
        fi
    done
}

install_component() {
    if (( $# != 1 )); then
        error '用法：stage-dev-info.sh <发布目录>'
        return 2
    fi
    local release_dir="${1%/}"
    if [[ "${release_dir}" != "${ARTIFACT_ROOT}/releases/"* \
        || "${release_dir}" == "${ARTIFACT_ROOT}/releases" \
        || ! -d "${release_dir}" ]]; then
        error "发布目录越界、不存在或是软链接：${release_dir}"
        return 1
    fi
    require_trusted_directory "${ARTIFACT_ROOT}" || return 1
    require_trusted_directory "${ARTIFACT_ROOT}/releases" || return 1
    require_trusted_directory "${release_dir}" || return 1

    local release_manifest="${release_dir}/${RELEASE_MANIFEST_NAME}"
    local info_manifest="${release_dir}/${INFO_MANIFEST_NAME}"
    local archive="${release_dir}/${INFO_ARCHIVE_NAME}"
    require_trusted_file "${release_manifest}" || return 1
    require_trusted_file "${info_manifest}" || return 1
    require_trusted_file "${archive}" || return 1

    local expected_release_id="${release_dir##*/}"
    validate_release_id "${expected_release_id}" || return 1
    STAGE_WORK_DIR="${API_ROOT}/.info-stage-${expected_release_id}-$$"
    if [[ -e "${STAGE_WORK_DIR}" || -L "${STAGE_WORK_DIR}" ]]; then
        error "开发信息受控暂存目录已存在：${STAGE_WORK_DIR}"
        return 1
    fi
    install -d -o 0 -g 0 -m 0700 "${STAGE_WORK_DIR}" || return 1
    trap cleanup_stage_work_dir EXIT

    local copied_release_manifest="${STAGE_WORK_DIR}/${RELEASE_MANIFEST_NAME}"
    local copied_info_manifest="${STAGE_WORK_DIR}/${INFO_MANIFEST_NAME}"
    local copied_archive="${STAGE_WORK_DIR}/${INFO_ARCHIVE_NAME}"
    install -o 0 -g 0 -m 0600 "${release_manifest}" "${copied_release_manifest}" || return 1
    install -o 0 -g 0 -m 0600 "${info_manifest}" "${copied_info_manifest}" || return 1
    validate_component_pair "${copied_release_manifest}" "${copied_info_manifest}" || return 1
    local release_id
    release_id="$(jq -er '.release_id' "${copied_info_manifest}")" || return 1
    validate_release_id "${release_id}" || return 1
    if [[ "${expected_release_id}" != "${release_id}" ]]; then
        error '发布目录名与 info release-id 不一致'
        return 1
    fi

    if installed_matches_release "${copied_info_manifest}"; then
        cleanup_stage_work_dir
        trap - EXIT
        printf '开发信息制品已存在且四文件哈希一致，直接复用：%s\n' "${INFO_DIR}"
        return 0
    fi
    if [[ -e "${INFO_DIR}" || -L "${INFO_DIR}" \
        || -e "${INSTALLED_MANIFEST}" || -L "${INSTALLED_MANIFEST}" ]]; then
        if [[ -d "${INFO_DIR}" && ! -L "${INFO_DIR}" \
            && ! -e "${INSTALLED_MANIFEST}" ]] \
            && validate_payload_dir "${INFO_DIR}" "${copied_info_manifest}"; then
            mv -T -- "${copied_info_manifest}" "${INSTALLED_MANIFEST}"
            cleanup_stage_work_dir
            trap - EXIT
            printf '已为完整的开发四文件补齐组件清单：%s\n' "${INFO_DIR}"
            return 0
        fi
        error '已有开发信息目录与目标制品不一致，拒绝覆盖；请先停止 API 并人工复核'
        return 1
    fi

    install -o 0 -g 0 -m 0600 "${archive}" "${copied_archive}" || return 1
    local expected_archive_sha expected_archive_size
    expected_archive_sha="$(jq -er '.archive.sha256' "${copied_info_manifest}")" || return 1
    expected_archive_size="$(jq -er '.archive.size' "${copied_info_manifest}")" || return 1
    if [[ "$(stat -c '%s' "${copied_archive}")" != "${expected_archive_size}" \
        || "$(sha256_file "${copied_archive}")" != "${expected_archive_sha}" ]]; then
        error '信息归档大小或 SHA256 与组件清单不一致'
        return 1
    fi
    validate_archive_members "${copied_archive}" || return 1

    local candidate="${STAGE_WORK_DIR}/payload"
    install -d -o 0 -g 0 -m 0750 "${candidate}" || return 1

    zstd --quiet --decompress --stdout "${copied_archive}" \
        | tar --extract --file=- --directory="${candidate}" \
            --no-same-owner --no-same-permissions || return 1
    local file_name
    for file_name in "${INFO_FILES_SORTED[@]}"; do
        require_regular_file "${candidate}/${file_name}" || return 1
        chown 0:0 "${candidate}/${file_name}" || return 1
        chmod 0640 "${candidate}/${file_name}" || return 1
    done
    validate_payload_dir "${candidate}" "${copied_info_manifest}" || return 1

    mv -T -- "${candidate}" "${INFO_DIR}" || return 1
    mv -T -- "${copied_info_manifest}" "${INSTALLED_MANIFEST}" || return 1
    cleanup_stage_work_dir
    trap - EXIT
    printf '开发信息制品已安装（未校验数据库大文件）：%s\n' "${INFO_DIR}"
}

main() {
    require_root || return 1
    for command_name in awk chmod chown diff find flock install jq mv readlink rm sha256sum sort stat; do
        require_command "${command_name}" || return 1
    done
    ensure_api_root || return 1
    if [[ -e "${STAGE_LOCK}" || -L "${STAGE_LOCK}" ]]; then
        require_trusted_file "${STAGE_LOCK}" || return 1
    fi
    exec 9>"${STAGE_LOCK}"
    chmod 0600 "${STAGE_LOCK}" || return 1
    if ! flock -n 9; then
        error '另一个开发信息制品安装或复验正在运行'
        return 1
    fi
    if (( $# == 1 )) && [[ "$1" == '--verify-installed' ]]; then
        verify_installed || return 1
        printf '开发信息四文件复验通过：%s\n' "${INFO_DIR}"
        return 0
    fi
    for command_name in tar zstd; do
        require_command "${command_name}" || return 1
    done
    install_component "$@"
}

main "$@"
