#!/usr/bin/env bash

# 离线制品脚本共用约定。调用方必须先启用 set -Eeuo pipefail。
# shellcheck source=data-profile.sh
source "${BASH_SOURCE[0]%/*}/data-profile.sh"
readonly DOMEYE_CORE_DATA_START="${DOMEYE_CORE_FIXED_DATA_START}"
readonly DOMEYE_CORE_DEFAULT_ARTIFACT_ROOT='/home/bgpdata/Domeye-Core-artifacts'
readonly DOMEYE_CORE_DEFAULT_INFO_TARGET='/home/bgpdata/Domeye-Core/backend/info'
readonly DOMEYE_CORE_DEFAULT_DATA_ROOT='/home/bgpdata/Domeye-Core-data'
readonly DOMEYE_CORE_INFO_ARCHIVE='info.tar.zst'
readonly DOMEYE_CORE_DATABASE_ARCHIVE='database.dump.zst'
readonly DOMEYE_CORE_IMAGE_ARCHIVE='database-image.tar.zst'
readonly DOMEYE_CORE_STATIC_INFO_EVIDENCE='static-info-evidence.tar.zst'
readonly DOMEYE_CORE_INFO_MANIFEST='info-manifest.json'
readonly DOMEYE_CORE_DATABASE_MANIFEST='database-manifest.json'
readonly DOMEYE_CORE_RELEASE_MANIFEST='manifest.json'
readonly DOMEYE_CORE_CHECKSUM_FILE='SHA256SUMS'
readonly DOMEYE_CORE_INFO_FILES=(
    'important_as.csv'
    'as_entity.csv'
    'ip_bgp_entity.csv'
    'country.xlsx'
)

domeye_artifact_error() {
    printf '错误：%s\n' "$*" >&2
}

domeye_artifact_require_command() {
    local command_name="$1"
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        domeye_artifact_error "缺少命令：${command_name}"
        return 1
    fi
}

domeye_artifact_validate_release_id() {
    local release_id="$1"
    if [[ ! "${release_id}" =~ ^[0-9]{8}T[0-9]{6}Z(-[a-z0-9][a-z0-9._-]{0,47})?$ ]]; then
        domeye_artifact_error "release-id 格式无效：${release_id}；应类似 20260717T120000Z 或 20260717T120000Z-core-01"
        return 1
    fi
}

domeye_artifact_require_regular_file() {
    local path="$1"
    if [[ ! -f "${path}" || -L "${path}" ]]; then
        domeye_artifact_error "要求普通文件且禁止软链接：${path}"
        return 1
    fi
}

domeye_artifact_sha256() {
    sha256sum "$1" | awk '{print $1}'
}

domeye_artifact_iso_utc_now() {
    date -u '+%Y-%m-%dT%H:%M:%SZ'
}

domeye_artifact_release_dir() {
    local artifact_root="$1"
    local release_id="$2"
    printf '%s/releases/%s\n' "${artifact_root%/}" "${release_id}"
}

domeye_artifact_assert_safe_release_dir() {
    local artifact_root="$1"
    local release_dir="$2"
    local expected_prefix="${artifact_root%/}/releases/"

    if [[ "${release_dir}" != "${expected_prefix}"* || "${release_dir}" == "${expected_prefix}" ]]; then
        domeye_artifact_error "拒绝使用越界发布目录：${release_dir}"
        return 1
    fi
    if [[ -L "${artifact_root}" || -L "${artifact_root%/}/releases" \
        || -L "${release_dir}" ]]; then
        domeye_artifact_error "制品根、releases 或发布目录不能是软链接：${release_dir}"
        return 1
    fi
    if [[ -e "${release_dir}" && ! -d "${release_dir}" ]]; then
        domeye_artifact_error "发布路径已存在但不是目录：${release_dir}"
        return 1
    fi
}

domeye_artifact_json_file() {
    local file="$1"
    jq -e . "${file}" >/dev/null
}
