#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly RUNTIME_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
readonly BINDING="${RUNTIME_ROOT}/BACKEND-SOURCE-BINDING.json"
readonly RELEASE_ID="$(jq -er '.release_id | sub("-backend$"; "")' "${BINDING}")"
readonly UNIFIED_ROOT="/home/bgpdata/Domeye-Core-runtime/unified-releases/${RELEASE_ID}"
readonly CANDIDATE="${UNIFIED_ROOT}/CANDIDATE-MANIFEST.json"
readonly MANAGER="${RUNTIME_ROOT}/deploy/country-outage-general-page/manage-runtime.sh"
# shellcheck source=../lib/artifact-common.sh
source "${RUNTIME_ROOT}/deploy/lib/artifact-common.sh"
# shellcheck source=../lib/frontend-common.sh
source "${RUNTIME_ROOT}/deploy/lib/frontend-common.sh"

error() {
    printf '国家中断通用观测运行时验证错误：%s\n' "$*" >&2
}

if (( $# != 1 )); then
    printf '用法：%s canary|production\n' "${0##*/}" >&2
    exit 2
fi
readonly MODE="$1"
case "${MODE}" in
    canary)
        readonly BASE_URL='http://127.0.0.1:38672'
        readonly EVIDENCE="${UNIFIED_ROOT}/CANARY-VERIFICATION.json"
        ;;
    production)
        readonly BASE_URL='http://127.0.0.1:28471'
        readonly EVIDENCE="${UNIFIED_ROOT}/PRODUCTION-VERIFICATION.json"
        ;;
    *)
        error "验证模式无效：${MODE}"
        exit 2
        ;;
esac

if (( EUID != 0 )); then
    error '运行时验证必须由 root 执行'
    exit 1
fi
for command_name in cmp curl git jq readlink sha256sum; do
    command -v "${command_name}" >/dev/null 2>&1 || {
        error "缺少命令：${command_name}"
        exit 1
    }
done
[[ -f "${CANDIDATE}" && ! -L "${CANDIDATE}" ]] || {
    error '统一候选证据缺失'
    exit 1
}
if [[ -e "${EVIDENCE}" || -L "${EVIDENCE}" ]]; then
    error "验证证据已存在，create-only 拒绝覆盖：${EVIDENCE}"
    exit 1
fi

source_commit="$(jq -er '.source.commit' "${CANDIDATE}")"
source_tag="$(jq -er '.source.annotated_tag' "${CANDIDATE}")"
source_archive="$(jq -er '.source.archive_path' "${CANDIDATE}")"
source_archive_sha="$(jq -er '.source.archive_sha256' "${CANDIDATE}")"
frontend_path="$(jq -er '.components.frontend.path' "${CANDIDATE}")"
frontend_tree_sha="$(jq -er '.components.frontend.tree_sha256' "${CANDIDATE}")"
sidecar_path="$(jq -er '.protected_runtime.sidecar_path' "${CANDIDATE}")"
sidecar_release="$(jq -er '.protected_runtime.sidecar_release_id' "${CANDIDATE}")"
sidecar_manifest_sha="$(jq -er '.protected_runtime.sidecar_manifest_sha256' "${CANDIDATE}")"

[[ "$(git -C /home/bgpdata/Domeye-Core rev-parse refs/heads/codex/prod)" == "${source_commit}" ]] || {
    error '生产主干与候选提交不一致'
    exit 1
}
[[ "$(git -C /home/bgpdata/Domeye-Core cat-file -t "${source_tag}")" == tag \
    && "$(git -C /home/bgpdata/Domeye-Core rev-parse "${source_tag}^{}")" == "${source_commit}" ]] || {
    error 'annotated tag 与候选提交不一致'
    exit 1
}
[[ "$(sha256sum "${source_archive}" | awk '{print $1}')" == "${source_archive_sha}" ]]
(
    cd -- "${RUNTIME_ROOT}"
    sha256sum -c SHA256SUMS >/dev/null
    cd backend
    sha256sum -c core.sha256 >/dev/null
)
(
    cd -- "${frontend_path}"
    sha256sum -c SHA256SUMS >/dev/null
)
[[ "$(domeye_frontend_tree_sha256 "${frontend_path}/dist")" == "${frontend_tree_sha}" ]]
cmp -s "${RUNTIME_ROOT}/general-read-model/manifest.json" \
    "${RUNTIME_ROOT}/general-read-model/COMPLETE.json"
[[ "$(sha256sum /home/bgpdata/Domeye-Core-dev-data/state.json | awk '{print $1}')" \
    == "$(jq -er '.protected_runtime.database_state_sha256' "${CANDIDATE}")" ]]
[[ "$(sha256sum /etc/nginx/nginx.conf | awk '{print $1}')" \
    == "$(jq -er '.protected_runtime.nginx_main_sha256' "${CANDIDATE}")" ]]
[[ "$(sha256sum /etc/nginx/conf.d/domeye-core.conf | awk '{print $1}')" \
    == "$(jq -er '.protected_runtime.nginx_site_sha256' "${CANDIDATE}")" ]]
[[ "$(readlink -f /home/bgpdata/Domeye-Core-runtime/country-outage-agent/current)" \
    == "${sidecar_path}" ]]
[[ "$(jq -er '.release_id' /home/bgpdata/Domeye-Core-runtime/country-outage-agent/state/active.json)" \
    == "${sidecar_release}" ]]
[[ "$(sha256sum "${sidecar_path}/RELEASE-MANIFEST.json" | awk '{print $1}')" \
    == "${sidecar_manifest_sha}" ]]
DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE="${MODE}" "${MANAGER}" status >/dev/null
if [[ "${MODE}" == production ]]; then
    [[ "$(readlink -f /home/bgpdata/Domeye-Core-runtime/current)" == "${RUNTIME_ROOT}" ]]
    [[ "$(< /home/bgpdata/Domeye-Core-runtime/web/state/frontend-current)" \
        == "$(jq -er '.components.frontend.release_id' "${CANDIDATE}")" ]]
fi

temporary="${UNIFIED_ROOT}/.${MODE}-verification.tmp.$$"
python3 - "${BASE_URL}" "${MODE}" "${RELEASE_ID}" "${temporary}" <<'PY'
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

base_url, mode, release_id, output_path = sys.argv[1:]
references = {
    "IR": "country_outage/2026-02-27 09:12:32/IR/1/r",
    "MW": "country_outage/2026-03-09 22:09:38/MW/2/r",
}


def fetch(path: str) -> tuple[dict[str, Any], int, float, str]:
    started = time.perf_counter()
    request = Request(base_url + path, headers={"Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        raw = response.read()
        etag = response.headers.get("ETag", "")
    elapsed = (time.perf_counter() - started) * 1000
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    return payload, len(raw), elapsed, etag


def get_event(country: str, reference: str) -> dict[str, Any]:
    resolution, resolution_size, resolution_ms, resolution_etag = fetch(
        "/api/v2/events/resolve?" + urlencode({"ref": reference})
    )
    assert resolution["schema_version"] == "country_outage_general_resolution_v1"
    assert resolution["country_code"] == country
    incident = quote(resolution["incident_id"], safe="")
    publication = resolution["publication_id"]
    paths = {
        "overview": f"/api/v2/country-outages/{incident}/overview?" + urlencode({"publication_id": publication}),
        "series": f"/api/v2/country-outages/{incident}/series?" + urlencode({"publication_id": publication}),
        "asns": f"/api/v2/country-outages/{incident}/asns?" + urlencode({"publication_id": publication, "page": 1, "page_size": 20}),
        "paths": f"/api/v2/country-outages/{incident}/path-downstreams?" + urlencode({"publication_id": publication, "page": 1, "page_size": 15}),
    }
    payloads: dict[str, Any] = {}
    sizes = [resolution_size]
    latencies = [resolution_ms]
    etags = [resolution_etag]
    for name, path in paths.items():
        payload, size, elapsed, etag = fetch(path)
        payloads[name] = payload
        sizes.append(size)
        latencies.append(elapsed)
        etags.append(etag)
    assert payloads["overview"]["schema_version"] == "country_outage_general_overview_v1"
    assert payloads["series"]["schema_version"] == "country_outage_general_series_v1"
    assert payloads["asns"]["schema_version"] == "country_outage_general_affected_as_page_v1"
    assert payloads["paths"]["schema_version"] == "country_outage_general_path_downstream_page_v1"
    identities = {
        (payload["incident_id"], payload["publication_id"], payload["revision"], payload["window_start_utc"], payload["window_end_utc"])
        for payload in payloads.values()
    }
    assert len(identities) == 1
    assert all(etags)
    assert payloads["asns"]["page_size"] == 20
    assert payloads["paths"]["page_size"] == 15
    digest = hashlib.sha256(
        json.dumps(
            {"resolution": resolution, **payloads},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "country": country,
        "reference": reference,
        "incident_id": resolution["incident_id"],
        "publication_id": publication,
        "digest": digest,
        "state_points": payloads["series"]["point_count"],
        "affected_as_total": payloads["asns"]["total"],
        "path_total": payloads["paths"]["total"],
        "max_response_bytes": max(sizes),
        "latencies_ms": latencies,
    }


first = {country: get_event(country, ref) for country, ref in references.items()}
second = {country: get_event(country, ref) for country, ref in reversed(list(references.items()))}
assert {key: value["digest"] for key, value in first.items()} == {
    key: value["digest"] for key, value in second.items()
}
jobs = [(country, ref) for _ in range(4) for country, ref in references.items()]
with ThreadPoolExecutor(max_workers=8) as executor:
    concurrent = list(executor.map(lambda item: get_event(*item), jobs))
for item in concurrent:
    assert item["digest"] == first[item["country"]]["digest"]

as_window_path = "/api/v1/features/ases/overview?" + urlencode({
    "start_time": "2026-02-27 08:10:00",
    "end_time": "2026-03-11 08:00:00",
    "asn": "48715",
    "limit": 6,
    "event_window": "true",
    "event_reference": references["IR"],
})
as_window, as_window_size, as_window_ms, as_window_etag = fetch(as_window_path)
assert as_window_etag
assert as_window["scope_kind"] == "event_window_selected_asn"
assert as_window["scope_size"] == 1
assert as_window["start_time"] == "2026-02-27 08:10:00"
assert as_window["end_time"] == "2026-03-11 08:00:00"
assert as_window["selected_asn"]["asn"] == "48715"
assert len(as_window["selected_asn"]["series"]) == 540

ir = first["IR"]
mw = first["MW"]
assert (ir["state_points"], ir["affected_as_total"], ir["path_total"]) == (3455, 525, 1956)
assert (mw["state_points"], mw["affected_as_total"], mw["path_total"]) == (57, 8, 18)

wrong_publication_path = (
    f"/api/v2/country-outages/{quote(ir['incident_id'], safe='')}/overview?"
    + urlencode({"publication_id": "country_outage_publication_v1_wrong"})
)
try:
    fetch(wrong_publication_path)
    raise AssertionError("错误 publication 未失败关闭")
except HTTPError as error:
    assert error.code == 404
invalid_scope_path = (
    f"/api/v2/country-outages/{quote(ir['incident_id'], safe='')}/path-downstreams?"
    + urlencode({"publication_id": ir["publication_id"], "scope": "dependency"})
)
try:
    fetch(invalid_scope_path)
    raise AssertionError("非法路径语义未失败关闭")
except HTTPError as error:
    assert error.code == 400
wrong_as_window_path = "/api/v1/features/ases/overview?" + urlencode({
    "start_time": "2026-02-27 08:15:00",
    "end_time": "2026-03-11 08:00:00",
    "asn": "48715",
    "event_window": "true",
    "event_reference": references["IR"],
})
try:
    fetch(wrong_as_window_path)
    raise AssertionError("错误 AS 事件窗口未失败关闭")
except HTTPError as error:
    assert error.code == 400

latencies = [value for event in [*first.values(), *second.values(), *concurrent] for value in event["latencies_ms"]]
latencies.append(as_window_ms)
latencies_sorted = sorted(latencies)
p95 = latencies_sorted[max(0, int(len(latencies_sorted) * 0.95) - 1)]
max_response_bytes = max(
    as_window_size,
    *(event["max_response_bytes"] for event in first.values()),
)
assert max_response_bytes < 1_000_000
assert p95 < 2_000

result = {
    "schema_version": "country_outage_general_runtime_verification_v1",
    "status": "passed",
    "mode": mode,
    "release_id": release_id,
    "base_url": base_url,
    "events": {key: {k: v for k, v in value.items() if k != "latencies_ms"} for key, value in first.items()},
    "repeat_order_concurrent_equal": True,
    "concurrent_runs": len(concurrent),
    "as_event_window": {
        "asn": 48715,
        "scope_kind": as_window["scope_kind"],
        "series_points": len(as_window["selected_asn"]["series"]),
        "response_bytes": as_window_size,
    },
    "failure_closed": {
        "wrong_publication_http": 404,
        "invalid_path_scope_http": 400,
        "wrong_as_event_window_http": 400,
    },
    "performance": {
        "sample_count": len(latencies),
        "p95_ms": round(p95, 3),
        "max_ms": round(max(latencies), 3),
        "max_response_bytes": max_response_bytes,
    },
    "boundaries": {
        "collector": "rrc25",
        "window": "[2026-02-24T00:00:00Z,2026-03-11T00:00:00Z)",
        "database_changed": False,
        "nginx_changed": False,
        "sidecar_changed": False,
        "paid_model_calls": 0,
    },
}
path = Path(output_path)
path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
path.chmod(0o640)
print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
PY
mv -T -- "${temporary}" "${EVIDENCE}"
