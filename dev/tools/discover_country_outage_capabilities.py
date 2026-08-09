#!/usr/bin/env python3
"""只读探测 country_outage 页面背后的正式 API，并向标准输出生成证据摘要。"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://10.99.8.16:28471"
DEFAULT_EVENT_REF = "country_outage/2026-02-27 09:12:32/IR/1/r"


def request_json(url: str, timeout: float) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        response = urlopen(request, timeout=timeout)
        raw = response.read()
        status = response.status
        headers = response.headers
    except HTTPError as error:
        raw = error.read()
        status = error.code
        headers = error.headers
    except URLError as error:
        return {
            "status": None,
            "error": str(error.reason),
            "url": url,
        }
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    return {
        "status": status,
        "content_type": headers.get("Content-Type"),
        "etag": headers.get("ETag"),
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "url": url,
        "payload": payload,
    }


def endpoint(base_url: str, path: str, params: dict[str, Any] | None = None) -> str:
    url = f"{base_url.rstrip('/')}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    return url


def extrema(series: dict[str, Any]) -> dict[str, Any]:
    timestamps = series.get("timestamps")
    tracks = series.get("tracks")
    if not isinstance(timestamps, list) or not isinstance(tracks, dict):
        return {}
    result: dict[str, Any] = {}
    for metric, values in tracks.items():
        if not isinstance(metric, str) or not isinstance(values, list):
            continue
        observed = [
            (index, value)
            for index, value in enumerate(values)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if not observed:
            result[metric] = {"state": "unavailable"}
            continue
        minimum = min(observed, key=lambda item: item[1])
        maximum = max(observed, key=lambda item: item[1])
        result[metric] = {
            "state": "observed",
            "count": len(observed),
            "null_count": len(values) - len(observed),
            "first": values[0],
            "first_at": timestamps[0],
            "last": values[-1],
            "last_at": timestamps[-1],
            "minimum": minimum[1],
            "minimum_at": timestamps[minimum[0]],
            "maximum": maximum[1],
            "maximum_at": timestamps[maximum[0]],
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--event-ref", default=DEFAULT_EVENT_REF)
    parser.add_argument("--timeout", type=float, default=20.0)
    arguments = parser.parse_args()

    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    resolve = request_json(
        endpoint(
            arguments.base_url,
            "/api/v2/events/resolve",
            {"ref": arguments.event_ref},
        ),
        arguments.timeout,
    )
    result: dict[str, Any] = {
        "schema_version": "country_outage_capability_probe_v1",
        "probe_mode": "read_only",
        "started_at": started_at,
        "base_url": arguments.base_url,
        "event_ref": arguments.event_ref,
        "health": request_json(
            endpoint(arguments.base_url, "/api/v1/healthz"),
            arguments.timeout,
        ),
        "resolve": resolve,
    }
    payload = resolve.get("payload")
    if not isinstance(payload, dict) or resolve.get("status") != 200:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 2

    incident_id = payload.get("incident_id")
    publication_id = payload.get("publication_id")
    if not isinstance(incident_id, str) or not isinstance(publication_id, str):
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 3

    root = f"/api/v2/country-outages/{quote(incident_id, safe='')}"
    common = {"publication_id": publication_id}
    probes = {
        "overview": (f"{root}/overview", common),
        "series": (f"{root}/series", common),
        "asns": (
            f"{root}/asns",
            {**common, "page": 1, "page_size": 2, "classification": "all"},
        ),
        "path_downstreams": (
            f"{root}/path-downstreams",
            {**common, "page": 1, "page_size": 2, "scope": "all"},
        ),
        "audit": (f"{root}/audit", common),
        "trend": (f"{root}/trend", common),
        "wrong_publication": (
            f"{root}/overview",
            {"publication_id": "p0-intentionally-invalid-publication"},
        ),
        "invalid_asn_query": (
            f"{root}/asns",
            {**common, "classification": "p0-invalid"},
        ),
    }
    result["probes"] = {
        name: request_json(endpoint(arguments.base_url, path, params), arguments.timeout)
        for name, (path, params) in probes.items()
    }
    result["external_evidence"] = request_json(
        endpoint(
            arguments.base_url,
            "/api/v2/country-outage/capabilities/external-evidence",
        ),
        arguments.timeout,
    )
    series_payload = result["probes"]["series"].get("payload")
    result["series_extrema"] = (
        extrema(series_payload) if isinstance(series_payload, dict) else {}
    )
    result["completed_at"] = (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
