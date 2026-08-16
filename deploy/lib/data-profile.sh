#!/usr/bin/env bash

# 当前开发阶段的数据策略只允许从 config/data-profile.json 读取。
# shellcheck disable=SC2034
if [[ "${DOMEYE_CORE_DATA_PROFILE_LOADED:-}" == '1' ]]; then
    return 0
fi

if [[ -n "${DOMEYE_CORE_DATA_PROFILE_FILE:-}" ]]; then
    profile_file="${DOMEYE_CORE_DATA_PROFILE_FILE}"
elif [[ -f '/home/bgpdata/Domeye-Core/config/data-profile.json' ]]; then
    profile_file='/home/bgpdata/Domeye-Core/config/data-profile.json'
else
    profile_file="$(cd -- "${BASH_SOURCE[0]%/*}/../.." && pwd)/config/data-profile.json"
fi
readonly DOMEYE_CORE_DATA_PROFILE_FILE="${profile_file}"
unset profile_file
if [[ ! -f "${DOMEYE_CORE_DATA_PROFILE_FILE}" || -L "${DOMEYE_CORE_DATA_PROFILE_FILE}" ]]; then
    printf '错误：数据档不存在、不是普通文件或是软链接：%s\n' \
        "${DOMEYE_CORE_DATA_PROFILE_FILE}" >&2
    return 1
fi
if ! command -v jq >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1; then
    printf '错误：读取数据档需要 jq 和 sha256sum。\n' >&2
    return 1
fi

profile_fields="$(jq -er '
  if (.schema_version == 1
      and .id == "feb-mar-2026"
      and .mode == "fixed"
      and .timezone == "Asia/Shanghai"
      and (.window_start | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+]08:00$"))
      and (.window_end_exclusive | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+]08:00$"))
      and (.snapshot_time | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+]08:00$"))
      and ((.snapshot_time | sub("[+]08:00$"; "Z") | fromdateiso8601) + 1
           == (.window_end_exclusive | sub("[+]08:00$"; "Z") | fromdateiso8601))
      and (.api_profile | type) == "string" and (.api_profile | length) > 0)
  then [
    .id,
    .timezone,
    .window_start,
    .window_end_exclusive,
    .snapshot_time,
    .api_profile
  ] | @tsv
  else error("invalid data profile")
  end
' "${DOMEYE_CORE_DATA_PROFILE_FILE}")" || {
    printf '错误：数据档结构或字段无效：%s\n' "${DOMEYE_CORE_DATA_PROFILE_FILE}" >&2
    return 1
}
IFS=$'\t' read -r \
    profile_id \
    profile_timezone \
    profile_start \
    profile_end_exclusive \
    profile_snapshot \
    profile_api_name <<< "${profile_fields}"
unset profile_fields

if [[ "${profile_start:0:19}" > "${profile_snapshot:0:19}" \
    || "${profile_snapshot:0:19}" > "${profile_end_exclusive:0:19}" ]]; then
    printf '错误：数据档时间顺序无效。\n' >&2
    return 1
fi

readonly DOMEYE_CORE_ACTIVE_DATA_PROFILE="${profile_id}"
readonly DOMEYE_CORE_DATA_TIMEZONE="${profile_timezone}"
readonly DOMEYE_CORE_FIXED_DATA_START="${profile_start:0:10} ${profile_start:11:8}"
readonly DOMEYE_CORE_FIXED_DATA_END_EXCLUSIVE="${profile_end_exclusive:0:10} ${profile_end_exclusive:11:8}"
readonly DOMEYE_CORE_FIXED_SNAPSHOT_TIME="${profile_snapshot:0:10} ${profile_snapshot:11:8}"
readonly DOMEYE_CORE_FIXED_API_PROFILE="${profile_api_name}"
readonly DOMEYE_CORE_DATA_PROFILE_SHA256="$(sha256sum "${DOMEYE_CORE_DATA_PROFILE_FILE}" | awk '{print $1}')"
readonly DOMEYE_CORE_DATA_PROFILE_LOADED=1
unset profile_id profile_timezone profile_start profile_end_exclusive profile_snapshot
unset profile_api_name

domeye_core_require_realtime_profile() {
    if [[ "${DOMEYE_CORE_ACTIVE_DATA_PROFILE}" != 'realtime-release' ]]; then
        printf '错误：当前数据档为 %s，禁止连接、激活或恢复实时数据库。\n' \
            "${DOMEYE_CORE_ACTIVE_DATA_PROFILE}" >&2
        return 1
    fi
}

domeye_core_require_source_database_access() {
    if [[ "${DOMEYE_CORE_ACTIVE_DATA_PROFILE}" != 'realtime-release' ]]; then
        printf '错误：当前数据档为 %s，禁止读取原生产数据库。\n' \
            "${DOMEYE_CORE_ACTIVE_DATA_PROFILE}" >&2
        return 1
    fi
}
