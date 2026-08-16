from urllib.parse import quote
from unittest.mock import patch

from web.api.v2.country_outages import _etag_response


LEGACY_REFERENCE = "country_outage/2026-03-01 00:00:00/CN/2/r"
LEGACY_DETAIL = {
    "outage_country": "中国",
    "attacked_country": "中国",
    "start_time": "2026-03-01 00:00:00",
    "end_time": "",
    "duration": "",
    "total_as_num": 100,
    "outage_as_num": 5,
    "outage_ases": [64500, 64501],
    "event_level": "middle",
    "event_descr": "旧事实测试",
    "event_info": "国家中断旧事实摘要",
}
COMMON_METADATA = {
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
}


def _resolve(client, reference=LEGACY_REFERENCE):
    return client.get(
        "/api/v2/events/resolve",
        query_string={"ref": reference},
    )


def test_resolver_distinguishes_invalid_missing_and_legacy_summary(client):
    invalid = _resolve(
        client,
        "leak/2026-03-01 00:00:00/CN/2/r",
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["observation_state"] == "invalid_reference"

    with patch(
        "services.country_outage_service.get_event_detail_data",
        return_value={},
    ):
        missing = _resolve(client)
    assert missing.status_code == 404
    assert missing.get_json()["observation_state"] == "event_not_found"

    with patch(
        "services.country_outage_service.get_event_detail_data",
        return_value=LEGACY_DETAIL,
    ):
        legacy = _resolve(client)
    payload = legacy.get_json()
    assert legacy.status_code == 200
    assert payload["observation_state"] == "legacy_summary"
    assert payload["data_mode"] == "legacy"
    assert payload["data_through"] is None
    assert payload["incident_id"].startswith("legacy_country_outage_v1.")
    assert payload["publication_id"].startswith("publication_legacy_v1_")
    assert {
        item["state"] for item in payload["capabilities"].values()
    } <= {
        "available",
        "building",
        "unavailable",
        "not_applicable",
    }


def test_legacy_summary_uses_same_four_api_contracts_without_zero_fill(client):
    with patch(
        "services.country_outage_service.get_event_detail_data",
        return_value=LEGACY_DETAIL,
    ):
        resolution = _resolve(client).get_json()
        incident_id = quote(resolution["incident_id"], safe="")
        responses = [
            client.get(f"/api/v2/country-outages/{incident_id}/overview"),
            client.get(f"/api/v2/country-outages/{incident_id}/series"),
            client.get(f"/api/v2/country-outages/{incident_id}/asns"),
            client.get(f"/api/v2/country-outages/{incident_id}/audit"),
        ]

    assert all(response.status_code == 200 for response in responses)
    payloads = [response.get_json() for response in responses]
    common_values = [
        {key: payload[key] for key in COMMON_METADATA}
        for payload in payloads
    ]
    assert common_values.count(common_values[0]) == 4

    overview, series, asns, audit = payloads
    assert overview["cohort"] is None
    assert overview["legacy_summary"]["affected_asn_count"] == 5
    assert overview["capabilities"]["asn_matrix"]["state"] == "unavailable"
    assert series["series"] == []
    assert series["resource_series"] == []
    assert asns["total"] == 0
    assert asns["items"] == []
    assert audit["evidence_level"] == "legacy_summary"
    assert audit["route_state_file"]["filename"] is None


def test_legacy_summary_identity_is_stable_and_reversible(client):
    with patch(
        "services.country_outage_service.get_event_detail_data",
        return_value=LEGACY_DETAIL,
    ):
        first = _resolve(client).get_json()
        second = _resolve(client).get_json()
        overview = client.get(
            "/api/v2/country-outages/"
            + quote(first["incident_id"], safe="")
            + "/overview"
        ).get_json()

    assert first["incident_id"] == second["incident_id"]
    assert overview["event_identity"]["legacy_reference"] == LEGACY_REFERENCE
    assert overview["event_identity"]["country_code"] == "CN"
    assert overview["event_identity"]["country_name"] == "中国"


def test_v2_etag_changes_when_response_contract_content_changes(app):
    common = {
        "incident_id": "incident-test",
        "publication_id": "publication-test",
        "revision": 1,
        "data_through": "2026-03-01T00:00:00Z",
    }
    with app.test_request_context("/api/v2/country-outages/test/audit"):
        first = _etag_response(
            {**common, "evidence_level": "legacy_summary"},
            "audit",
        )
        second = _etag_response(
            {
                **common,
                "evidence_level": (
                    "aggregated_route_state_with_artifact_hashes"
                ),
            },
            "audit",
        )

    assert first[2]["ETag"] != second[2]["ETag"]
