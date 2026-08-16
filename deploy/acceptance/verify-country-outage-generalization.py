#!/usr/bin/env python3
"""验证增强观测与真实 legacy_summary 事件共用国家中断通用 API。"""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


IRAN_REFERENCE = "country_outage/2026-02-27 09:12:32/IR/1/r"
NON_IRAN_REFERENCE = "country_outage/2026-03-06 18:06:28/GM/1/r"
COMMON_FIELDS = (
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
    with urlopen(request, timeout=90) as response:
        payload = json.load(response)
        headers = {key.lower(): value for key, value in response.headers.items()}
    if not isinstance(payload, dict):
        raise AssertionError(f"响应不是 JSON 对象：{url}")
    return payload, headers


def resolve(base_url: str, reference: str) -> dict[str, Any]:
    query = urlencode({"ref": reference})
    payload, _ = get_json(f"{base_url}/api/v2/events/resolve?{query}")
    assert payload["schema_version"] == "country_outage_resolution_v2"
    assert payload["legacy_reference"] == reference
    assert isinstance(payload["publication_id"], str)
    assert payload["publication_id"]
    return payload


def event_payloads(
    base_url: str,
    incident_id: str,
    publication_id: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    encoded = quote(incident_id, safe="")
    payloads: dict[str, dict[str, Any]] = {}
    etags: dict[str, str] = {}
    for endpoint in ("overview", "series", "asns", "audit"):
        payload, headers = get_json(
            f"{base_url}/api/v2/country-outages/{encoded}/{endpoint}?"
            + urlencode({"publication_id": publication_id})
        )
        payloads[endpoint] = payload
        etags[endpoint] = headers.get("etag", "")
    identities = [
        tuple(payload[field] for field in COMMON_FIELDS)
        for payload in payloads.values()
    ]
    assert identities.count(identities[0]) == 4, "四接口发布身份不一致"
    assert identities[0][1] == publication_id, "接口未固定解析时 publication"
    assert all(etags.values()), "四接口必须返回 ETag"
    return payloads, etags


def verify_iran(base_url: str) -> dict[str, Any]:
    resolution = resolve(base_url, IRAN_REFERENCE)
    assert resolution["observation_state"] == "state_complete"
    assert resolution["data_mode"] == "replay"
    payloads, etags = event_payloads(
        base_url,
        resolution["incident_id"],
        resolution["publication_id"],
    )
    overview = payloads["overview"]
    series = payloads["series"]
    asns = payloads["asns"]
    assert overview["event_identity"]["country_code"] == "IR"
    assert overview["cohort"]["origin_asn_count"] == 563
    assert overview["cohort"]["prefix_vp_count"] == 384767
    assert overview["cohort"]["ipv4_prefix_vp_count"] == 383804
    assert overview["cohort"]["ipv6_prefix_vp_count"] == 963
    assert overview["observation_scope"]["vantage_point_count"] == 96
    assert overview["observation_scope"]["expected_observation_count"] == 60
    assert overview["observation_scope"]["missing_observation_count"] == 0
    assert overview["processing_status"]["state"] == "final"
    assert overview["missing_slot_count"] == 0
    assert len(series["series"]) == 60
    assert len(series["resource_series"]) == 60
    assert asns["total"] == 563
    assert asns["page"] == 1
    assert asns["page_size"] == 60
    assert asns["page_count"] == 10
    first_page = asns["items"]

    encoded = quote(resolution["incident_id"], safe="")
    second, second_headers = get_json(
        f"{base_url}/api/v2/country-outages/{encoded}/asns?"
        + urlencode(
            {
                "page": 2,
                "page_size": 60,
                "sort": "longest_fully_invisible_desc",
                "publication_id": resolution["publication_id"],
            }
        )
    )
    assert second["revision"] == asns["revision"]
    assert second["publication_id"] == asns["publication_id"]
    assert second["data_through"] == asns["data_through"]
    assert second["page"] == 2
    assert second["total"] == 563
    first_asns = {str(item["asn"]) for item in first_page}
    second_asns = {str(item["asn"]) for item in second["items"]}
    assert first_asns.isdisjoint(second_asns), "相邻分页出现重复 ASN"
    assert second_headers.get("etag") != etags["asns"], "分页 ETag 未绑定查询"

    return {
        "incident_id": resolution["incident_id"],
        "revision": overview["revision"],
        "data_through": overview["data_through"],
        "series_points": len(series["series"]),
        "resource_points": len(series["resource_series"]),
        "asn_total": asns["total"],
        "asn_page_count": asns["page_count"],
    }


def verify_non_iran(base_url: str) -> dict[str, Any]:
    resolution = resolve(base_url, NON_IRAN_REFERENCE)
    assert resolution["observation_state"] == "legacy_summary"
    assert resolution["data_mode"] == "legacy"
    payloads, _ = event_payloads(
        base_url,
        resolution["incident_id"],
        resolution["publication_id"],
    )
    overview = payloads["overview"]
    series = payloads["series"]
    asns = payloads["asns"]
    audit = payloads["audit"]
    assert overview["event_identity"]["country_code"] == "GM"
    assert overview["event_identity"]["country_name"] == "冈比亚"
    assert overview["cohort"] is None
    assert overview["publication_id"] == resolution["publication_id"]
    assert overview["legacy_summary"]["affected_asn_count"] == 1
    assert overview["legacy_summary"]["total_asn_count"] == 10
    assert overview["capabilities"]["asn_matrix"]["state"] == "unavailable"
    assert series["series"] == []
    assert series["resource_series"] == []
    assert asns["total"] == 0
    assert asns["items"] == []
    assert audit["evidence_level"] == "legacy_summary"
    assert audit["route_state_file"]["filename"] is None
    return {
        "incident_id": resolution["incident_id"],
        "revision": overview["revision"],
        "data_through": overview["data_through"],
        "country": overview["event_identity"]["country_name"],
        "affected_asn_count": overview["legacy_summary"][
            "affected_asn_count"
        ],
        "total_asn_count": overview["legacy_summary"]["total_asn_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    result = {
        "schema_version": "country_outage_generalization_acceptance_v1",
        "base_url": base_url,
        "iran": verify_iran(base_url),
        "non_iran": verify_non_iran(base_url),
        "status": "passed",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
