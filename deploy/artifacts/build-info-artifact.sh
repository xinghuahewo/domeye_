#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"

usage() {
    printf '用法：%s <只读信息目录> <release-id> [制品根目录]\n' "${0##*/}" >&2
}

if (( $# < 2 || $# > 3 )); then
    usage
    exit 2
fi

readonly SOURCE_INFO_DIR="$1"
readonly RELEASE_ID="$2"
readonly ARTIFACT_ROOT="${3:-${DOMEYE_CORE_DEFAULT_ARTIFACT_ROOT}}"

domeye_artifact_validate_release_id "${RELEASE_ID}"
for command_name in jq sha256sum tar zstd stat awk mktemp; do
    domeye_artifact_require_command "${command_name}"
done

if [[ ! -d "${SOURCE_INFO_DIR}" || -L "${SOURCE_INFO_DIR}" ]]; then
    domeye_artifact_error "信息来源必须是实际目录且不能是软链接：${SOURCE_INFO_DIR}"
    exit 1
fi

readonly RELEASE_DIR="$(domeye_artifact_release_dir "${ARTIFACT_ROOT}" "${RELEASE_ID}")"
domeye_artifact_assert_safe_release_dir "${ARTIFACT_ROOT}" "${RELEASE_DIR}"
install -d -m 0750 "${ARTIFACT_ROOT}/releases" "${RELEASE_DIR}"

readonly LOCK_DIR="${RELEASE_DIR}/.info-build.lock"
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
    domeye_artifact_error "同一发布版本正在构建信息制品：${RELEASE_ID}"
    exit 1
fi

work_dir=''
cleanup() {
    local exit_code=$?
    if [[ -n "${work_dir}" && -d "${work_dir}" ]]; then
        rm -rf -- "${work_dir}"
    fi
    rmdir "${LOCK_DIR}" 2>/dev/null || true
    return "${exit_code}"
}
trap cleanup EXIT

if [[ -e "${RELEASE_DIR}/${DOMEYE_CORE_INFO_ARCHIVE}" || -e "${RELEASE_DIR}/${DOMEYE_CORE_INFO_MANIFEST}" ]]; then
    domeye_artifact_error "该 release-id 已包含信息制品，拒绝覆盖：${RELEASE_ID}"
    exit 1
fi

work_dir="$(mktemp -d "${RELEASE_DIR}/.info-build.XXXXXX")"
readonly PAYLOAD_DIR="${work_dir}/payload"
install -d -m 0750 "${PAYLOAD_DIR}"

for file_name in "${DOMEYE_CORE_INFO_FILES[@]}"; do
    source_path="${SOURCE_INFO_DIR%/}/${file_name}"
    domeye_artifact_require_regular_file "${source_path}"
    install -m 0640 "${source_path}" "${PAYLOAD_DIR}/${file_name}"
done

readonly ARCHIVE_TMP="${work_dir}/${DOMEYE_CORE_INFO_ARCHIVE}"
tar \
    --create \
    --file=- \
    --directory="${PAYLOAD_DIR}" \
    --sort=name \
    --mtime='@0' \
    --owner=0 \
    --group=0 \
    --numeric-owner \
    "${DOMEYE_CORE_INFO_FILES[@]}" \
    | zstd --quiet --threads=0 -19 -o "${ARCHIVE_TMP}"
chmod 0600 "${ARCHIVE_TMP}"

readonly FILE_ROWS="${work_dir}/files.jsonl"
: > "${FILE_ROWS}"
for file_name in "${DOMEYE_CORE_INFO_FILES[@]}"; do
    file_path="${PAYLOAD_DIR}/${file_name}"
    if [[ "${file_name}" == *.csv ]]; then
        record_count="$(awk 'END { print NR > 0 ? NR - 1 : 0 }' "${file_path}")"
        count_method='CSV 物理行数减去表头'
    else
        domeye_artifact_require_command unzip
        row_count="$(unzip -p "${file_path}" 'xl/worksheets/sheet1.xml' | grep -o '<row' | wc -l | tr -d ' ')"
        record_count="$(( row_count > 0 ? row_count - 1 : 0 ))"
        count_method='第一个工作表 XML 行数减去表头'
    fi

    jq -cn \
        --arg name "${file_name}" \
        --arg sha256 "$(domeye_artifact_sha256 "${file_path}")" \
        --argjson size "$(stat -c '%s' "${file_path}")" \
        --argjson record_count "${record_count}" \
        --arg count_method "${count_method}" \
        '{name: $name, sha256: $sha256, size: $size, record_count: $record_count, count_method: $count_method}' \
        >> "${FILE_ROWS}"
done

readonly MANIFEST_TMP="${work_dir}/${DOMEYE_CORE_INFO_MANIFEST}"
jq -n \
    --argjson schema_version 1 \
    --arg release_id "${RELEASE_ID}" \
    --arg created_at "$(domeye_artifact_iso_utc_now)" \
    --arg data_start "${DOMEYE_CORE_DATA_START}" \
    --arg archive "${DOMEYE_CORE_INFO_ARCHIVE}" \
    --arg archive_sha256 "$(domeye_artifact_sha256 "${ARCHIVE_TMP}")" \
    --argjson archive_size "$(stat -c '%s' "${ARCHIVE_TMP}")" \
    --slurpfile files "${FILE_ROWS}" \
    '{schema_version: $schema_version, component: "info", release_id: $release_id, created_at: $created_at, data_start: $data_start, archive: {name: $archive, sha256: $archive_sha256, size: $archive_size}, files: $files}' \
    > "${MANIFEST_TMP}"
chmod 0600 "${MANIFEST_TMP}"

mv -- "${ARCHIVE_TMP}" "${RELEASE_DIR}/${DOMEYE_CORE_INFO_ARCHIVE}"
mv -- "${MANIFEST_TMP}" "${RELEASE_DIR}/${DOMEYE_CORE_INFO_MANIFEST}"
printf '信息制品已生成：%s\n' "${RELEASE_DIR}"
