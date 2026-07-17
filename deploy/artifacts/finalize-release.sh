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
readonly INFO_MANIFEST_PATH="${RELEASE_DIR}/${DOMEYE_CORE_INFO_MANIFEST}"
readonly DATABASE_MANIFEST_PATH="${RELEASE_DIR}/${DOMEYE_CORE_DATABASE_MANIFEST}"
readonly MANIFEST_PATH="${RELEASE_DIR}/${DOMEYE_CORE_RELEASE_MANIFEST}"
readonly CHECKSUM_PATH="${RELEASE_DIR}/${DOMEYE_CORE_CHECKSUM_FILE}"

for command_name in jq sha256sum; do
    domeye_artifact_require_command "${command_name}"
done
for file_path in "${INFO_MANIFEST_PATH}" "${DATABASE_MANIFEST_PATH}"; do
    domeye_artifact_require_regular_file "${file_path}"
    domeye_artifact_json_file "${file_path}"
done

info_release="$(jq -r '.release_id' "${INFO_MANIFEST_PATH}")"
database_release="$(jq -r '.release_id' "${DATABASE_MANIFEST_PATH}")"
if [[ "${info_release}" != "${database_release}" ]]; then
    domeye_artifact_error "信息与数据库组件的 release-id 不一致"
    exit 1
fi
domeye_artifact_validate_release_id "${info_release}"

for file_name in \
    "${DOMEYE_CORE_INFO_ARCHIVE}" \
    "${DOMEYE_CORE_DATABASE_ARCHIVE}" \
    "${DOMEYE_CORE_IMAGE_ARCHIVE}" \
    'database-inventory.json' \
    'database-schema.sql' \
    "${DOMEYE_CORE_INFO_MANIFEST}" \
    "${DOMEYE_CORE_DATABASE_MANIFEST}"; do
    domeye_artifact_require_regular_file "${RELEASE_DIR}/${file_name}"
done

manifest_tmp="${RELEASE_DIR}/.${DOMEYE_CORE_RELEASE_MANIFEST}.tmp.$$"
jq -n \
    --argjson schema_version 1 \
    --arg release_id "${info_release}" \
    --arg created_at "$(domeye_artifact_iso_utc_now)" \
    --arg data_start "${DOMEYE_CORE_DATA_START}" \
    --slurpfile info "${INFO_MANIFEST_PATH}" \
    --slurpfile database "${DATABASE_MANIFEST_PATH}" \
    '{
      schema_version: $schema_version,
      release_id: $release_id,
      created_at: $created_at,
      data_start: $data_start,
      snapshot_time: $database[0].snapshot_time,
      info: $info[0],
      database: $database[0],
      acceptance: {
        nonempty_window: {start_time: "2026-06-11 22:00:00", end_time: "2026-06-12 00:19:59"},
        feature_window: {start_time: "2026-07-17 19:30:00", end_time: "2026-07-17 20:30:00", asn: "1299"},
        event_details: [
          {type: "as_outage", start_time: "2026-06-12 00:18:46", problem: "215199", event_id: 3830, source: "r"},
          {type: "prefix_outage", start_time: "2026-06-12 00:19:35", problem: "2605:9cc0:c07::-48", event_id: 128, source: "r"},
          {type: "hijack", start_time: "2026-06-12 00:18:02", problem: "2a0f:7802:e2bd::-48", event_id: 11, source: "r"},
          {type: "country_outage", start_time: "2026-06-11 22:39:30", problem: "LA", event_id: 106, source: "r"},
          {type: "sub_hijack", start_time: "2026-06-12 00:17:49", problem: "154.201.8.0-21", event_id: 3620, source: "r"},
          {type: "leak", start_time: "2026-06-12 00:19:49", problem: "2400:cb00:df02::-48", event_id: 1, source: "r"}
        ]
      }
    }' > "${manifest_tmp}"
chmod 0600 "${manifest_tmp}"
mv -- "${manifest_tmp}" "${MANIFEST_PATH}"

checksum_tmp="${RELEASE_DIR}/.${DOMEYE_CORE_CHECKSUM_FILE}.tmp.$$"
(
    cd -- "${RELEASE_DIR}"
    sha256sum \
        "${DOMEYE_CORE_INFO_ARCHIVE}" \
        "${DOMEYE_CORE_DATABASE_ARCHIVE}" \
        "${DOMEYE_CORE_IMAGE_ARCHIVE}" \
        database-inventory.json \
        database-schema.sql \
        "${DOMEYE_CORE_INFO_MANIFEST}" \
        "${DOMEYE_CORE_DATABASE_MANIFEST}" \
        "${DOMEYE_CORE_RELEASE_MANIFEST}"
) > "${checksum_tmp}"
chmod 0600 "${checksum_tmp}"
mv -- "${checksum_tmp}" "${CHECKSUM_PATH}"
printf '发布制品已定稿：%s\n' "${RELEASE_DIR}"
