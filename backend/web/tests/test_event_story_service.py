import gzip
import hashlib
import json
from unittest.mock import patch
from urllib.parse import quote

import pytest

from services import event_story_service


def _write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _write_jsonl_gzip(path, values):
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True))
            stream.write("\n")


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(at, affected, fully_invisible, partially_visible, visible_prefix):
    return {
        "snapshot_id": "snapshot-" + at,
        "observed_at": at,
        "affected_asn_count": affected,
        "affected_asn_ratio": affected / 2,
        "visible_origin_asn_count": 2 - fully_invisible,
        "visible_origin_asn_ratio": (2 - fully_invisible) / 2,
        "baseline_prefix_vp_count": 100,
        "visible_prefix_vp_count": visible_prefix,
        "visible_prefix_vp_ratio": visible_prefix / 100,
        "dual_stack_classifications": {
            "fully_invisible": list(range(fully_invisible)),
            "partially_visible": list(range(partially_visible)),
            "fully_visible": [],
            "ipv4_invisible_ipv6_visible": [],
        },
        "ipv4": {
            "baseline_origin_asn_count": 2,
            "visible_origin_asn_count": 2 - fully_invisible,
            "baseline_prefix_vp_count": 80,
            "visible_prefix_vp_count": max(0, visible_prefix - 20),
        },
        "ipv6": {
            "baseline_origin_asn_count": 1,
            "visible_origin_asn_count": 1,
            "baseline_prefix_vp_count": 20,
            "visible_prefix_vp_count": 20,
        },
        "update_counts": {"announce": 10, "withdraw": 3},
    }


@pytest.fixture()
def story_package(tmp_path):
    incident = {
        "incident_id": "incident-test",
        "legacy_ref": event_story_service.IRAN_LEGACY_REF,
        "country_code": "IR",
        "collector_id": "rrc25",
        "cohort_id": "cohort-test",
        "detected_at": "2026-02-28T10:10:00Z",
        "onset_at": "2026-02-28T10:05:00Z",
        "peak_at": "2026-02-28T10:15:00Z",
        "trough_at": "2026-02-28T10:20:00Z",
        "partial_recovery_at": None,
        "full_recovery_at": None,
        "observation_end_at": "2026-02-28T10:20:00Z",
        "duration_state": "interval",
        "recovery_state": "ongoing",
        "normal_band": {"state": "unknown"},
        "episodes": [{"episode_id": "episode-test"}],
        "algorithm_version": "rrc25-iran-go-replay/test",
    }
    cohort = {
        "cohort_id": "cohort-test",
        "seed_observed_at": "2026-02-28T08:00:00Z",
        "baseline_origin_asn_count": 2,
        "baseline_prefix_vp_count": 100,
        "mapping_version": "mapping-test",
        "members": [
            {
                "asn": 64500,
                "afi": 4,
                "prefix_vp_count": 60,
                "prefixes": ["192.0.2.0/24"],
            },
            {
                "asn": 64501,
                "afi": 6,
                "prefix_vp_count": 40,
                "prefixes": ["2001:db8::/32"],
            },
        ],
    }
    snapshots = [
        _snapshot("2026-02-28T10:05:00Z", 1, 0, 1, 95),
        _snapshot("2026-02-28T10:10:00Z", 1, 1, 0, 90),
        _snapshot("2026-02-28T10:15:00Z", 2, 1, 1, 80),
        _snapshot("2026-02-28T10:20:00Z", 1, 1, 0, 75),
    ]
    asn_states = []
    for snapshot in snapshots:
        asn_states.extend(
            [
                {
                    "asn": 64500,
                    "classification": "fully_invisible",
                    "observed_at": snapshot["observed_at"],
                },
                {
                    "asn": 64501,
                    "classification": "partially_visible",
                    "observed_at": snapshot["observed_at"],
                },
            ]
        )

    _write_json(tmp_path / "incident.json", incident)
    _write_json(tmp_path / "cohort.json", cohort)
    _write_json(tmp_path / "episodes.json", {"episodes": incident["episodes"]})
    _write_json(
        tmp_path / "waves.json",
        {"waves": [{"causal_relation": "not_assessed"}]},
    )
    _write_json(
        tmp_path / "input-summary.json",
        {
            "catch_up_updates": [{}] * 2,
            "formal_updates": [{}] * 3,
        },
    )
    _write_json(
        tmp_path / "QUALITY.json",
        {
            "status": "pass",
            "failures": None,
            "observation_count": len(snapshots),
            "last_observation_at": snapshots[-1]["observed_at"],
            "input_compressed_bytes": 123,
            "rib_physical_records": 10,
            "rib_entries": 20,
            "update_physical_records": 30,
            "update_route_events": 40,
        },
    )
    _write_jsonl_gzip(tmp_path / "country-snapshots.jsonl.gz", snapshots)
    _write_jsonl_gzip(tmp_path / "asn-states.jsonl.gz", asn_states)
    hashes = {
        filename: _sha256(tmp_path / filename)
        for filename in event_story_service.CONSUMED_DELIVERABLES
    }
    hashes["route-states.jsonl.gz"] = "a" * 64
    _write_json(
        tmp_path / "COMPLETE.json",
        {
            "status": "complete",
            "completed_at": "2026-07-24T08:46:05Z",
            "deliverable_sha256": hashes,
        },
    )
    event_story_service._load_package.cache_clear()
    return tmp_path


def test_iran_story_answers_product_contract_boundaries(story_package):
    story = event_story_service.get_iran_event_story(
        legacy_reference=event_story_service.IRAN_LEGACY_REF,
        legacy_detail={
            "start_time": "2026-02-27 09:12:32",
            "outage_as_num": 176,
            "total_as_num": 556,
        },
        package_directory=story_package,
    )

    assert story["schema_version"] == "event_detail_story_v1"
    assert story["contract_scope"]["control_plane_only"] is True
    assert story["observation"]["left_censored"] is True
    assert story["observation"]["right_censored"] is True
    assert story["baseline"]["state"] == "unknown"
    assert story["detection"]["onset"]["precision"] == (
        "left_censored_at_window_start"
    )
    assert story["detection"]["rule"]["confirm_duration_seconds"] == 300
    assert story["lifecycle"]["current_state"] == "ongoing"
    assert story["lifecycle"]["partial_recovery_at_local"] is None
    assert story["impact"]["persistent_asns"][0]["asn"] == "64500"
    assert len(story["series"]) == 4
    assert len(story["unknowns"]) >= 5
    assert all(
        {"question", "reason", "evidence_needed", "next_action"} <= set(item)
        for item in story["unknowns"]
    )
    assert story["evidence"]["consumed_deliverable_hashes_verified"] is True
    assert story["comparisons"][1]["value"] == "176/556"


def test_non_acceptance_event_does_not_receive_iran_story(story_package):
    assert event_story_service.get_iran_event_story(
        legacy_reference="country_outage/2026-02-01 00:00:00/CN/1/r",
        package_directory=story_package,
    ) is None


def test_consumed_deliverable_hash_mismatch_fails_closed(story_package):
    (story_package / "incident.json").write_text("{}", encoding="utf-8")
    event_story_service._load_package.cache_clear()

    with pytest.raises(
        event_story_service.EventStoryUnavailable,
        match="哈希不匹配",
    ):
        event_story_service.get_iran_event_story(
            legacy_reference=event_story_service.IRAN_LEGACY_REF,
            package_directory=story_package,
        )


def test_event_story_api_exposes_product_story(client):
    payload = {
        "schema_version": "event_detail_story_v1",
        "contract_scope": {},
        "event": {"country_name": "伊朗"},
        "observation": {},
        "baseline": {},
        "detection": {},
        "impact": {},
        "series": [],
        "lifecycle": {},
        "precursor": {},
        "comparisons": [],
        "claims": [],
        "unknowns": [],
        "actions": [],
        "evidence": {},
    }
    start_time = quote("2026-02-27 09:12:32", safe="")
    with patch(
        "web.api.events.api.get_event_detail_data",
        return_value={"outage_as_num": 176, "total_as_num": 556},
    ), patch(
        "web.api.events.api.get_iran_event_story",
        return_value=payload,
    ):
        response = client.get(
            f"/api/v1/events/story/country_outage/{start_time}/IR/1/r"
        )

    assert response.status_code == 200
    assert response.get_json()["event"]["country_name"] == "伊朗"
