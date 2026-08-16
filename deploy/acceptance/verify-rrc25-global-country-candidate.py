#!/usr/bin/env python3
"""核验伊朗与非伊朗样本共用 61 点国家中断观测合同。"""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


REFERENCES = {
    "IR": "country_outage/2026-02-27 09:12:32/IR/1/r",
    "MS": "country_outage/2026-02-28 18:05:00/MS/1/rrc25",
}
EXPECTED = {
    "IR": {"asn": 563, "prefix_vp": 384767},
    "MS": {"asn": 1, "prefix_vp": 43},
}
IDENTITY_FIELDS = (
    "revision",
    "publication_id",
    "publication_state",
    "observation_state",
    "data_mode",
    "data_through",
    "updated_at",
    "is_final",
    "processing_status",
    "missing_slot_count",
    "incident_id",
    "cohort_id",
    "window_start_utc",
    "window_end_utc",
    "capability_contract_version",
)


def get_json(url: str) -> tuple[dict[str, Any], dict[str, str]]:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=120) as response:
        value = json.load(response)
        headers = {key.lower(): item for key, item in response.headers.items()}
    if not isinstance(value, dict):
        raise AssertionError(f"响应不是对象：{url}")
    return value, headers


def verify_country(base_url: str, code: str) -> dict[str, Any]:
    resolution, _ = get_json(
        f"{base_url}/api/v2/events/resolve?"
        + urlencode({"ref": REFERENCES[code]})
    )
    assert resolution["schema_version"] == "country_outage_resolution_v2"
    assert resolution["observation_state"] == "state_complete"
    assert resolution["data_mode"] == "mixed"
    assert resolution["data_through"] == "2026-02-28T15:05:00Z"
    incident_id = str(resolution["incident_id"])
    publication_id = str(resolution["publication_id"])
    payloads: dict[str, dict[str, Any]] = {}
    etags: list[str] = []
    for endpoint in ("overview", "series", "asns", "audit"):
        payload, headers = get_json(
            f"{base_url}/api/v2/country-outages/"
            f"{quote(incident_id, safe='')}/{endpoint}?"
            + urlencode({"publication_id": publication_id})
        )
        payloads[endpoint] = payload
        etags.append(headers.get("etag", ""))
    identities = [
        tuple(
            json.dumps(payload[field], ensure_ascii=False, sort_keys=True)
            for field in IDENTITY_FIELDS
        )
        for payload in payloads.values()
    ]
    assert len(set(identities)) == 1
    assert json.loads(identities[0][1]) == publication_id
    assert all(etags)

    overview = payloads["overview"]
    series = payloads["series"]
    asns = payloads["asns"]
    audit = payloads["audit"]
    assert overview["event_identity"]["country_code"] == code
    assert overview["observation_scope"]["observation_count"] == 61
    assert overview["observation_scope"]["expected_observation_count"] == 61
    assert overview["observation_scope"]["missing_observation_count"] == 0
    assert overview["cohort"]["origin_asn_count"] == EXPECTED[code]["asn"]
    assert overview["cohort"]["prefix_vp_count"] == EXPECTED[code]["prefix_vp"]
    assert overview["capabilities"]["country_resources"]["state"] == "unavailable"
    assert overview["capabilities"]["update_activity"]["state"] == "available"
    assert len(series["series"]) == 61
    assert series["series"][-1]["observed_at_utc"] == "2026-02-28T15:05:00Z"
    assert len(series["country_update_series"]) == 61
    assert (
        series["country_update_series"][-1]["observed_at_utc"]
        == "2026-02-28T15:05:00Z"
    )
    assert series["resource_series"] == []
    assert asns["total"] == EXPECTED[code]["asn"]
    assert len(asns["observed_at_utc"]) == 61
    assert audit["consumed_deliverable_hashes_verified"] is True
    assert audit["evidence_level"] == "aggregated_route_state_with_artifact_hashes"

    baseline_publication = next(
        item["publication_id"]
        for item in audit["revision_history"]
        if item["publication_kind"] == "baseline"
    )
    baseline_series, _ = get_json(
        f"{base_url}/api/v2/country-outages/"
        f"{quote(incident_id, safe='')}/series?"
        + urlencode({"publication_id": baseline_publication})
    )
    assert baseline_series["revision"] == series["revision"]
    assert baseline_series["data_mode"] == "replay"
    assert baseline_series["data_through"] == "2026-02-28T15:00:00Z"
    assert len(baseline_series["series"]) == 60
    assert baseline_series["series"] == series["series"][:60]
    return {
        "incident_id": incident_id,
        "publication_id": publication_id,
        "revision": series["revision"],
        "data_mode": series["data_mode"],
        "data_through": series["data_through"],
        "series_points": len(series["series"]),
        "country_update_points": len(series["country_update_series"]),
        "asn_total": asns["total"],
        "baseline_publication_points": len(baseline_series["series"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    arguments = parser.parse_args()
    base_url = arguments.base_url.rstrip("/")
    result = {
        "schema_version": "rrc25-global-country-candidate-acceptance/v1",
        "base_url": base_url,
        "countries": {
            code: verify_country(base_url, code)
            for code in ("IR", "MS")
        },
        "status": "pass",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
