#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"

if (( $# < 1 || $# > 2 )); then
    printf '用法：%s <发布清单> [入口地址]\n' "${0##*/}" >&2
    exit 2
fi

readonly MANIFEST_PATH="$1"
readonly BASE_URL="${2:-http://127.0.0.1:28471}"
readonly API_URL="${BASE_URL%/}/api/v1"

for command_name in curl jq mktemp; do
    domeye_artifact_require_command "${command_name}"
done
domeye_artifact_require_regular_file "${MANIFEST_PATH}"
domeye_artifact_json_file "${MANIFEST_PATH}"

work_dir="$(mktemp -d)"
cleanup() {
    local exit_code=$?
    if [[ -n "${work_dir}" && -d "${work_dir}" ]]; then
        rm -rf -- "${work_dir}"
    fi
    return "${exit_code}"
}
trap cleanup EXIT

request_json() {
    local label="$1"
    local endpoint="$2"
    local assertion="$3"
    shift 3
    local output="${work_dir}/response-$RANDOM.json"

    curl --fail --silent --show-error --max-time 90 \
        --get "${API_URL}/${endpoint}" "$@" > "${output}"
    if ! jq -e "${assertion}" "${output}" >/dev/null; then
        domeye_artifact_error "接口响应不符合冒烟断言：${label}"
        return 1
    fi
    printf '接口通过：%s\n' "${label}"
}

request_json '健康检查' 'healthz' 'type == "object" and (.status == true or .status == "ok")'
nonempty_start="$(jq -r '.acceptance.nonempty_window.start_time' "${MANIFEST_PATH}")"
nonempty_end="$(jq -r '.acceptance.nonempty_window.end_time' "${MANIFEST_PATH}")"
request_json '事件列表' 'events' 'type == "object" and (.data | type == "array" and length > 0)' \
    --data-urlencode 'page_num=1' \
    --data-urlencode 'page_size=10' \
    --data-urlencode "date=${nonempty_start}_${nonempty_end}"
request_json '最新事件' 'events/top' 'type == "array"' \
    --data-urlencode 'event_type=["前缀劫持","子前缀劫持","前缀中断","AS中断","国家中断","路由泄漏"]'

while IFS=$'\t' read -r event_type start_time problem event_id source; do
    encoded_type="$(jq -rn --arg value "${event_type}" '$value | @uri')"
    encoded_time="$(jq -rn --arg value "${start_time}" '$value | @uri')"
    encoded_problem="$(jq -rn --arg value "${problem}" '$value | @uri')"
    encoded_source="$(jq -rn --arg value "${source}" '$value | @uri')"
    request_json \
        "事件详情 ${event_type}" \
        "${encoded_type}/${encoded_time}/${encoded_problem}/${event_id}/${encoded_source}" \
        'type == "object" and length > 0'
done < <(jq -r '.acceptance.event_details[] | [.type, .start_time, .problem, (.event_id | tostring), .source] | @tsv' "${MANIFEST_PATH}")

feature_start="$(jq -r '.acceptance.feature_window.start_time' "${MANIFEST_PATH}")"
feature_end="$(jq -r '.acceptance.feature_window.end_time' "${MANIFEST_PATH}")"
feature_asn="$(jq -r '.acceptance.feature_window.asn' "${MANIFEST_PATH}")"
request_json '采集点综合特征' 'features/top' 'type == "array" and length > 0' \
    --data-urlencode 'target=collector' \
    --data-urlencode "start_time=${feature_start}" \
    --data-urlencode "end_time=${feature_end}"
request_json 'ASN 综合特征' 'features/top' 'type == "array" and length > 0' \
    --data-urlencode "target=${feature_asn}" \
    --data-urlencode "start_time=${feature_start}" \
    --data-urlencode "end_time=${feature_end}"
request_json '国家特征列表' 'features/countries' 'type == "object" and (.data | type == "array" and length > 0)' \
    --data-urlencode "start_time=${feature_start}" \
    --data-urlencode "end_time=${feature_end}" \
    --data-urlencode 'page_num=1' \
    --data-urlencode 'page_size=5'
request_json 'ASN 特征列表' 'features/ases' 'type == "object" and (.data | type == "array" and length > 0)' \
    --data-urlencode "asn=${feature_asn}" \
    --data-urlencode "start_time=${feature_start}" \
    --data-urlencode "end_time=${feature_end}" \
    --data-urlencode 'page_num=1' \
    --data-urlencode 'page_size=5'

for outage_endpoint in country-as country-prefix global-as global-prefix; do
    request_json \
        "中断时序 ${outage_endpoint}" \
        "features/outages/${outage_endpoint}" \
        'type == "array" and length > 0 and any(.[]; (.outage_count | type) == "number" and .outage_count > 0)' \
        --data-urlencode "start_time=${nonempty_start}" \
        --data-urlencode "end_time=${nonempty_end}"
done
request_json '中断时序 as-prefix' 'features/outages/as-prefix' 'type == "array" and length > 0 and any(.[]; (.outage_count | type) == "number" and .outage_count > 0)' \
    --data-urlencode "asn=${feature_asn}" \
    --data-urlencode "start_time=${nonempty_start}" \
    --data-urlencode "end_time=${nonempty_end}"

request_json '仪表盘总量' 'dashboard/counts/total' 'type == "array" or type == "object"'
request_json '仪表盘分类' 'dashboard/counts/type' 'type == "array" or type == "object"' \
    --data-urlencode 'event_type=前缀劫持'

login_status="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 "${API_URL}/login")"
if [[ "${login_status}" != '404' ]]; then
    domeye_artifact_error "已删除接口 /login 应返回 404，实际为 ${login_status}"
    exit 1
fi
printf '接口通过：已删除接口保持 404\n'

for route in / /events /events/detail /features /not-a-real-route; do
    page_status="$(curl --silent --output "${work_dir}/page.html" --write-out '%{http_code}' --max-time 15 "${BASE_URL%/}${route}")"
    if [[ "${page_status}" != '200' ]] || ! grep -q '<div id="app"></div>' "${work_dir}/page.html"; then
        domeye_artifact_error "前端直达或刷新失败：${route}（HTTP ${page_status}）"
        exit 1
    fi
    printf '前端路由通过：%s\n' "${route}"
done

printf '核心接口与前端路由冒烟全部通过。\n'
