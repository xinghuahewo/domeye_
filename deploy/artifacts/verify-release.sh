#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"

if (( $# != 1 )); then
    printf '用法：%s <发布目录>\n' "${0##*/}" >&2
    exit 2
fi

readonly RELEASE_DIR="${1%/}"
readonly MANIFEST_PATH="${RELEASE_DIR}/${DOMEYE_CORE_RELEASE_MANIFEST}"
readonly INFO_MANIFEST_PATH="${RELEASE_DIR}/${DOMEYE_CORE_INFO_MANIFEST}"
readonly DATABASE_MANIFEST_PATH="${RELEASE_DIR}/${DOMEYE_CORE_DATABASE_MANIFEST}"
readonly CHECKSUM_PATH="${RELEASE_DIR}/${DOMEYE_CORE_CHECKSUM_FILE}"
EXPECTED_FILES=(
    'database-image.tar.zst'
    'database-inventory.json'
    'database-manifest.json'
    'database-schema.sql'
    'database.dump.zst'
    'info-manifest.json'
    'info.tar.zst'
    'manifest.json'
)

for command_name in diff find jq sha256sum sort stat; do
    domeye_artifact_require_command "${command_name}"
done
if [[ ! -d "${RELEASE_DIR}" || -L "${RELEASE_DIR}" ]]; then
    domeye_artifact_error "发布路径必须是实际目录：${RELEASE_DIR}"
    exit 1
fi
for file_path in "${MANIFEST_PATH}" "${INFO_MANIFEST_PATH}" "${DATABASE_MANIFEST_PATH}" "${CHECKSUM_PATH}"; do
    domeye_artifact_require_regular_file "${file_path}"
done
for manifest_file in "${MANIFEST_PATH}" "${INFO_MANIFEST_PATH}" "${DATABASE_MANIFEST_PATH}"; do
    domeye_artifact_json_file "${manifest_file}"
done
static_info_evidence_name="$(
    jq -r '.static_info_evidence.name // empty' "${DATABASE_MANIFEST_PATH}"
)"
if [[ -n "${static_info_evidence_name}" ]]; then
    if [[ "${static_info_evidence_name}" != "${DOMEYE_CORE_STATIC_INFO_EVIDENCE}" ]]; then
        domeye_artifact_error \
            "static INFO 证据包名称无效：${static_info_evidence_name}"
        exit 1
    fi
    EXPECTED_FILES+=("${static_info_evidence_name}")
fi

mapfile -t actual_entries < <(find "${RELEASE_DIR}" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort)
mapfile -t expected_entries < <(
    printf '%s\n' "${EXPECTED_FILES[@]}" "${DOMEYE_CORE_CHECKSUM_FILE}" | sort
)
if ! diff -u \
    <(printf '%s\n' "${expected_entries[@]}") \
    <(printf '%s\n' "${actual_entries[@]}") \
    >/dev/null; then
    domeye_artifact_error '发布目录顶层文件集合与组件清单不一致'
    exit 1
fi
for entry_name in "${actual_entries[@]}"; do
    domeye_artifact_require_regular_file "${RELEASE_DIR}/${entry_name}"
done

declare -A checksum_by_name=()
checksum_count=0
while read -r expected_sha file_name extra; do
    if [[ -n "${extra:-}" || ! "${expected_sha}" =~ ^[0-9a-f]{64}$ || ! "${file_name}" =~ ^[A-Za-z0-9._-]+$ ]]; then
        domeye_artifact_error "SHA256SUMS 行格式无效：${expected_sha} ${file_name} ${extra:-}"
        exit 1
    fi
    if [[ -n "${checksum_by_name[${file_name}]+present}" ]]; then
        domeye_artifact_error "SHA256SUMS 包含重复文件：${file_name}"
        exit 1
    fi
    checksum_by_name["${file_name}"]="${expected_sha}"
    checksum_count=$(( checksum_count + 1 ))
done < "${CHECKSUM_PATH}"

if (( checksum_count != ${#EXPECTED_FILES[@]} )); then
    domeye_artifact_error "SHA256SUMS 必须恰好包含 ${#EXPECTED_FILES[@]} 个发布文件，实际 ${checksum_count} 个"
    exit 1
fi
for file_name in "${EXPECTED_FILES[@]}"; do
    if [[ -z "${checksum_by_name[${file_name}]+present}" ]]; then
        domeye_artifact_error "SHA256SUMS 缺少预期文件：${file_name}"
        exit 1
    fi
    domeye_artifact_require_regular_file "${RELEASE_DIR}/${file_name}"
    actual_sha="$(domeye_artifact_sha256 "${RELEASE_DIR}/${file_name}")"
    if [[ "${actual_sha}" != "${checksum_by_name[${file_name}]}" ]]; then
        domeye_artifact_error "发布文件 SHA256 不一致：${file_name}"
        exit 1
    fi
done

release_id="$(jq -r '.release_id' "${MANIFEST_PATH}")"
domeye_artifact_validate_release_id "${release_id}"
if [[ "$(jq -r '.release_id' "${INFO_MANIFEST_PATH}")" != "${release_id}" || "$(jq -r '.release_id' "${DATABASE_MANIFEST_PATH}")" != "${release_id}" ]]; then
    domeye_artifact_error '总清单与组件清单的 release-id 不一致'
    exit 1
fi
if [[ "$(jq -r '.data_start' "${MANIFEST_PATH}")" != "${DOMEYE_CORE_DATA_START}" || "$(jq -r '.data_start' "${INFO_MANIFEST_PATH}")" != "${DOMEYE_CORE_DATA_START}" || "$(jq -r '.data_start' "${DATABASE_MANIFEST_PATH}")" != "${DOMEYE_CORE_DATA_START}" ]]; then
    domeye_artifact_error '总清单或组件清单的数据起点错误'
    exit 1
fi

if ! diff -u <(jq -S '.info' "${MANIFEST_PATH}") <(jq -S . "${INFO_MANIFEST_PATH}") >/dev/null \
    || ! diff -u <(jq -S '.database' "${MANIFEST_PATH}") <(jq -S . "${DATABASE_MANIFEST_PATH}") >/dev/null; then
    domeye_artifact_error '总清单内嵌组件与独立组件清单不一致'
    exit 1
fi

check_component_file() {
    local component_manifest="$1"
    local name_query="$2"
    local hash_query="$3"
    local expected_name="$4"
    local actual_name embedded_sha
    actual_name="$(jq -r "${name_query}" "${component_manifest}")"
    embedded_sha="$(jq -r "${hash_query}" "${component_manifest}")"
    if [[ "${actual_name}" != "${expected_name}" ]]; then
        domeye_artifact_error "组件清单文件名不符合约定：${actual_name}"
        return 1
    fi
    if [[ ! "${embedded_sha}" =~ ^[0-9a-f]{64}$ || "${embedded_sha}" != "${checksum_by_name[${expected_name}]}" ]]; then
        domeye_artifact_error "组件清单内嵌哈希与 SHA256SUMS 不一致：${expected_name}"
        return 1
    fi
}

check_component_file "${INFO_MANIFEST_PATH}" '.archive.name' '.archive.sha256' "${DOMEYE_CORE_INFO_ARCHIVE}"
check_component_file "${DATABASE_MANIFEST_PATH}" '.archive.name' '.archive.sha256' "${DOMEYE_CORE_DATABASE_ARCHIVE}"
check_component_file "${DATABASE_MANIFEST_PATH}" '.image.archive' '.image.archive_sha256' "${DOMEYE_CORE_IMAGE_ARCHIVE}"
check_component_file "${DATABASE_MANIFEST_PATH}" '.inventory.name' '.inventory.sha256' 'database-inventory.json'
check_component_file "${DATABASE_MANIFEST_PATH}" '.schema.name' '.schema.sha256' 'database-schema.sql'
if [[ -n "${static_info_evidence_name}" ]]; then
    check_component_file \
        "${DATABASE_MANIFEST_PATH}" \
        '.static_info_evidence.name' \
        '.static_info_evidence.sha256' \
        "${DOMEYE_CORE_STATIC_INFO_EVIDENCE}"
    if ! jq -e \
        --argjson actual_size "$(
            stat -c '%s' "${RELEASE_DIR}/${DOMEYE_CORE_STATIC_INFO_EVIDENCE}"
        )" \
        '(.static_info_evidence.scope == "core_four_files"
          or .static_info_evidence.scope == "all_24_files")
         and .static_info_evidence.scope == .static_info.implementation_scope
         and .static_info_evidence.content_id == .static_info.content_id
         and .static_info_evidence.size == $actual_size' \
        "${DATABASE_MANIFEST_PATH}" >/dev/null; then
        domeye_artifact_error \
            'static INFO 证据包元数据与数据库组件清单不一致'
        exit 1
    fi
    "${SCRIPT_DIR}/verify-static-info-evidence.sh" \
        "${RELEASE_DIR}/${DOMEYE_CORE_STATIC_INFO_EVIDENCE}" \
        "$(jq -r '.static_info_evidence.scope' "${DATABASE_MANIFEST_PATH}")"
fi

if ! jq -e \
    '.integrity.table_whitelist.ok == true
     and .integrity.detail_references.ok == true
     and .integrity.detail_references.malformed_count == 0
     and .integrity.detail_references.orphan_count == 0
     and (.integrity.detail_references.discarded_malformed_event_rows.total | type) == "number"
     and .integrity.detail_references.discarded_malformed_event_rows.total >= 0
     and ([.integrity.detail_references.discarded_malformed_event_rows.by_month_type[].row_count] | add // 0) == .integrity.detail_references.discarded_malformed_event_rows.total' \
    "${RELEASE_DIR}/database-inventory.json" >/dev/null; then
    domeye_artifact_error '数据库 inventory 未记录通过的白名单与事件详情引用完整性门禁'
    exit 1
fi
if ! diff -u \
    <(jq -S '{table_whitelist_ok: .integrity.table_whitelist.ok, malformed_detail_count: .integrity.detail_references.malformed_count, orphan_detail_count: .integrity.detail_references.orphan_count, discarded_malformed_event_rows: .integrity.detail_references.discarded_malformed_event_rows}' "${RELEASE_DIR}/database-inventory.json") \
    <(jq -S '.integrity | {table_whitelist_ok, malformed_detail_count, orphan_detail_count, discarded_malformed_event_rows}' "${DATABASE_MANIFEST_PATH}") \
    >/dev/null; then
    domeye_artifact_error '数据库组件清单与 inventory 的完整性摘要不一致'
    exit 1
fi
if ! diff -u \
    <(jq -S '.static_info' "${RELEASE_DIR}/database-inventory.json") \
    <(jq -S '.static_info' "${DATABASE_MANIFEST_PATH}") \
    >/dev/null; then
    domeye_artifact_error '数据库组件清单与 inventory 的 static INFO 摘要不一致'
    exit 1
fi

printf '发布制品文件集合与清单交叉校验通过：%s\n' "${RELEASE_DIR}"
