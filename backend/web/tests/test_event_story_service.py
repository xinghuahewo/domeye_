import gzip
import hashlib
import json
import shutil
from unittest.mock import patch
from urllib.parse import quote

import pytest

from services import event_story_service
from services import country_outage_registry
from data_pipeline.country_outage_publication import publish_country_outage
from services.country_outage_service import CountryOutageNotFound


LEGACY_REFERENCE = "country_outage/2026-02-27 09:12:32/IR/1/r"


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


def _append_snapshot_package(source, target):
    shutil.copytree(source, target)
    snapshots_path = target / "country-snapshots.jsonl.gz"
    with gzip.open(snapshots_path, "rt", encoding="utf-8") as stream:
        snapshots = [json.loads(line) for line in stream if line.strip()]
    appended = _snapshot("2026-02-28T10:25:00Z", 1, 1, 0, 78)
    snapshots.append(appended)
    _write_jsonl_gzip(snapshots_path, snapshots)

    states_path = target / "asn-states.jsonl.gz"
    with gzip.open(states_path, "rt", encoding="utf-8") as stream:
        states = [json.loads(line) for line in stream if line.strip()]
    states.extend(
        [
            {
                "asn": 64500,
                "classification": "fully_invisible",
                "observed_at": appended["observed_at"],
            },
            {
                "asn": 64501,
                "classification": "partially_visible",
                "observed_at": appended["observed_at"],
            },
        ]
    )
    _write_jsonl_gzip(states_path, states)

    incident = json.loads((target / "incident.json").read_text(encoding="utf-8"))
    incident["observation_end_at"] = appended["observed_at"]
    _write_json(target / "incident.json", incident)
    quality = json.loads((target / "QUALITY.json").read_text(encoding="utf-8"))
    quality["observation_count"] = len(snapshots)
    quality["last_observation_at"] = appended["observed_at"]
    _write_json(target / "QUALITY.json", quality)

    complete = json.loads((target / "COMPLETE.json").read_text(encoding="utf-8"))
    complete["completed_at"] = "2026-07-25T01:05:01Z"
    complete["deliverable_sha256"] = {
        filename: _sha256(target / filename)
        for filename in event_story_service.CONSUMED_DELIVERABLES
    }
    complete["deliverable_sha256"]["route-states.jsonl.gz"] = "a" * 64
    _write_json(target / "COMPLETE.json", complete)
    event_story_service._load_package.cache_clear()
    return target


def _remove_snapshot_package(source, target, removed_at):
    shutil.copytree(source, target)
    snapshots_path = target / "country-snapshots.jsonl.gz"
    with gzip.open(snapshots_path, "rt", encoding="utf-8") as stream:
        snapshots = [
            json.loads(line)
            for line in stream
            if line.strip()
            and json.loads(line)["observed_at"] != removed_at
        ]
    _write_jsonl_gzip(snapshots_path, snapshots)

    states_path = target / "asn-states.jsonl.gz"
    with gzip.open(states_path, "rt", encoding="utf-8") as stream:
        states = [
            json.loads(line)
            for line in stream
            if line.strip()
            and json.loads(line)["observed_at"] != removed_at
        ]
    _write_jsonl_gzip(states_path, states)

    quality = json.loads((target / "QUALITY.json").read_text(encoding="utf-8"))
    quality["observation_count"] = len(snapshots)
    quality["last_observation_at"] = snapshots[-1]["observed_at"]
    _write_json(target / "QUALITY.json", quality)
    complete = json.loads((target / "COMPLETE.json").read_text(encoding="utf-8"))
    complete["deliverable_sha256"] = {
        filename: _sha256(target / filename)
        for filename in event_story_service.CONSUMED_DELIVERABLES
    }
    complete["deliverable_sha256"]["route-states.jsonl.gz"] = "a" * 64
    _write_json(target / "COMPLETE.json", complete)
    event_story_service._load_package.cache_clear()
    return target


def _correct_snapshot_package(source, target, corrected_at):
    shutil.copytree(source, target)
    snapshots_path = target / "country-snapshots.jsonl.gz"
    with gzip.open(snapshots_path, "rt", encoding="utf-8") as stream:
        snapshots = [json.loads(line) for line in stream if line.strip()]
    corrected = next(
        snapshot
        for snapshot in snapshots
        if snapshot["observed_at"] == corrected_at
    )
    corrected["visible_prefix_vp_count"] += 1
    corrected["visible_prefix_vp_ratio"] = (
        corrected["visible_prefix_vp_count"]
        / corrected["baseline_prefix_vp_count"]
    )
    _write_jsonl_gzip(snapshots_path, snapshots)
    complete = json.loads((target / "COMPLETE.json").read_text(encoding="utf-8"))
    complete["completed_at"] = "2026-07-25T01:10:01Z"
    complete["deliverable_sha256"] = {
        filename: _sha256(target / filename)
        for filename in event_story_service.CONSUMED_DELIVERABLES
    }
    complete["deliverable_sha256"]["route-states.jsonl.gz"] = "a" * 64
    _write_json(target / "COMPLETE.json", complete)
    event_story_service._load_package.cache_clear()
    return target


def _remove_country_updates_snapshot_package(source, target):
    shutil.copytree(source, target)
    snapshots_path = target / "country-snapshots.jsonl.gz"
    with gzip.open(snapshots_path, "rt", encoding="utf-8") as stream:
        snapshots = [json.loads(line) for line in stream if line.strip()]
    for snapshot in snapshots:
        snapshot.pop("country_update_counts", None)
    _write_jsonl_gzip(snapshots_path, snapshots)

    complete = json.loads((target / "COMPLETE.json").read_text(encoding="utf-8"))
    complete["deliverable_sha256"] = {
        filename: _sha256(target / filename)
        for filename in event_story_service.CONSUMED_DELIVERABLES
    }
    complete["deliverable_sha256"]["route-states.jsonl.gz"] = "a" * 64
    _write_json(target / "COMPLETE.json", complete)
    event_story_service._load_package.cache_clear()
    return target


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
        "country_update_counts": {"announce": 4, "withdraw": 1},
    }


def _resource_series():
    return [
        {
            "time": "2026-02-28 18:05:00",
            "announce": 30,
            "withdraw": 2,
            "v4Prefix_num": 1000,
            "v6Prefix_num": 2000,
            "v4IP_num": 256000,
        },
        {
            "time": "2026-02-28 18:10:00",
            "announce": 12,
            "withdraw": 4,
            "v4Prefix_num": 990,
            "v6Prefix_num": 2000,
            "v4IP_num": 253440,
        },
        {
            "time": "2026-02-28 18:15:00",
            "announce": 8,
            "withdraw": 6,
            "v4Prefix_num": 970,
            "v6Prefix_num": 1980,
            "v4IP_num": 248320,
        },
        {
            "time": "2026-02-28 18:20:00",
            "announce": 10,
            "withdraw": 3,
            "v4Prefix_num": 975,
            "v6Prefix_num": 1980,
            "v4IP_num": 249600,
        },
    ]


@pytest.fixture()
def story_package(tmp_path):
    incident = {
        "incident_id": "incident-test",
        "legacy_ref": LEGACY_REFERENCE,
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
        legacy_reference=LEGACY_REFERENCE,
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


def test_country_observation_exposes_only_descriptive_data(story_package):
    observation = event_story_service.get_country_outage_observation(
        registration={
            "incident_id": "incident-test",
            "legacy_reference": LEGACY_REFERENCE,
            "country": {"code": "IR", "name": "伊朗"},
            "package_uri": str(story_package),
            "collector_ids": ["rrc25"],
            "vantage_point_count": 96,
            "interval_seconds": 300,
            "revision": 4,
            "publication_state": "published",
            "resource_source": {"state": "available"},
        },
        legacy_detail={"start_time": "2026-02-27 09:12:32"},
        package_directory=story_package,
        resource_series=_resource_series(),
    )

    assert observation["schema_version"] == "country_outage_observation_v2"
    assert observation["revision"] == 4
    assert observation["observation_state"] == "state_complete"
    assert observation["data_mode"] == "replay"
    assert (
        observation["capability_contract_version"]
        == "country_outage_capabilities_v1"
    )
    assert observation["capabilities"]["fixed_cohort"]["state"] == "available"
    assert observation["event_identity"]["display_name"] == "伊朗 BGP 路由观测"
    assert observation["observation_scope"]["collector_id"] == "rrc25"
    assert observation["cohort"]["prefix_vp_count"] == 100
    assert observation["normal_band"]["state"] == "unavailable"
    assert observation["series"][0]["visible_prefix_vp_delta"] is None
    assert observation["series"][1]["visible_prefix_vp_delta"] == -5
    assert observation["series"][1]["visible_prefix_vp_ratio_delta_pp"] == (
        pytest.approx(-5)
    )
    assert observation["resource_series"][1]["ipv4_24_equivalent_delta"] == -10
    assert observation["resource_series"][1]["ipv4_address_delta"] == -2560
    assert observation["resource_series"][1]["update_total"] == 16
    assert observation["country_update_series"][0]["update_total"] == 5
    assert observation["country_update_series"][0]["announce_count"] == 4
    assert (
        observation["country_update_metric_extrema"]["withdraw_count"]["max"][
            "value"
        ]
        == 1
    )
    assert len(observation["asn_state"]["timelines"]) == 2
    assert observation["asn_state"]["timelines"][0]["states"] == [2, 2, 2, 2]
    assert {
        item["key"] for item in observation["metric_definitions"]
    } >= {
        "visible_prefix_vp_count",
        "ipv4_24_equivalent_count",
        "ipv6_48_equivalent_count",
        "country_update_counts",
    }
    assert not {
        "lifecycle",
        "precursor",
        "claims",
        "unknowns",
        "actions",
    } & set(observation)


def test_unavailable_country_updates_do_not_publish_null_extrema(
    story_package,
    tmp_path,
):
    package = _remove_country_updates_snapshot_package(
        story_package,
        tmp_path / "without-country-updates",
    )
    observation = event_story_service.get_country_outage_observation(
        registration={
            "incident_id": "incident-test",
            "legacy_reference": LEGACY_REFERENCE,
            "country": {"code": "IR", "name": "伊朗"},
            "package_uri": str(package),
            "collector_ids": ["rrc25"],
            "vantage_point_count": 96,
            "interval_seconds": 300,
            "revision": 4,
            "publication_state": "published",
            "resource_source": {"state": "available"},
        },
        legacy_detail={"start_time": "2026-02-27 09:12:32"},
        package_directory=package,
        resource_series=_resource_series(),
    )

    assert observation["country_update_series"] == []
    assert observation["country_update_metric_extrema"] == {}
    assert not any(
        metric.startswith("country_")
        for metric in observation["metric_extrema"]
    )
    assert all(
        extrema["min"] is not None and extrema["max"] is not None
        for extrema in observation["metric_extrema"].values()
    )


def test_v2_publication_pin_keeps_four_interfaces_atomic(
    client,
    story_package,
    tmp_path,
    monkeypatch,
):
    appended_package = _append_snapshot_package(
        story_package,
        tmp_path / "package-appended",
    )
    registry_path = tmp_path / "registry.json"
    registry = {
        "schema_version": "country_outage_observation_registry_v1",
        "observations": [
            {
                "incident_id": "incident-test",
                "legacy_reference": LEGACY_REFERENCE,
                "country": {"code": "IR", "name": "伊朗"},
                "display_name": "伊朗 BGP 路由观测",
                "collector_ids": ["rrc25"],
                "vantage_point_count": 96,
                "interval_seconds": 300,
                "revision": 1,
                "publication_state": "published",
                "observation_state": "state_complete",
                "data_mode": "live",
                "data_through": "2026-02-28T10:20:00Z",
                "updated_at": "2026-07-25T01:00:01Z",
                "is_final": False,
                "resource_source": {"state": "unavailable"},
                "package_uri": str(story_package),
            }
        ],
    }
    _write_json(registry_path, registry)
    monkeypatch.setenv(
        country_outage_registry.REGISTRY_ENV,
        str(registry_path),
    )
    country_outage_registry._read_registry.cache_clear()

    initial_resolution = client.get(
        "/api/v2/events/resolve",
        query_string={"ref": LEGACY_REFERENCE},
    ).get_json()
    initial_publication_id = initial_resolution["publication_id"]
    initial_series = client.get(
        "/api/v2/country-outages/incident-test/series",
        query_string={"publication_id": initial_publication_id},
    ).get_json()
    assert len(initial_series["series"]) == 4

    publish_country_outage(
        registry_path,
        incident_id="incident-test",
        publication={
            "publication_id": "publication-b",
            "package_uri": str(appended_package),
            "revision": 1,
            "publication_state": "published",
            "observation_state": "state_complete",
            "data_mode": "live",
            "data_through": "2026-02-28T10:25:00Z",
            "updated_at": "2026-07-25T01:05:01Z",
            "is_final": False,
        },
        kind="append",
    )

    resolution = client.get(
        "/api/v2/events/resolve",
        query_string={"ref": LEGACY_REFERENCE},
    ).get_json()
    assert resolution["publication_id"] == "publication-b"
    assert resolution["latest_revision"] == 1

    pinned_payloads = []
    for endpoint in ("overview", "series", "asns", "audit"):
        response = client.get(
            f"/api/v2/country-outages/incident-test/{endpoint}",
            query_string={"publication_id": initial_publication_id},
        )
        assert response.status_code == 200
        pinned_payloads.append(response.get_json())
    assert {
        (
            payload["publication_id"],
            payload["revision"],
            payload["data_through"],
        )
        for payload in pinned_payloads
    } == {(initial_publication_id, 1, "2026-02-28T10:20:00Z")}
    assert len(pinned_payloads[1]["series"]) == 4

    current_series = client.get(
        "/api/v2/country-outages/incident-test/series",
        query_string={"publication_id": "publication-b"},
    ).get_json()
    assert current_series["publication_id"] == "publication-b"
    assert current_series["revision"] == 1
    assert current_series["data_through"] == "2026-02-28T10:25:00Z"
    assert len(current_series["series"]) == 5

    missing = client.get(
        "/api/v2/country-outages/incident-test/overview",
        query_string={"publication_id": "publication-missing"},
    )
    assert missing.status_code == 404
    assert missing.get_json()["observation_state"] == "publication_not_found"

    corrected_package = _correct_snapshot_package(
        appended_package,
        tmp_path / "package-corrected",
        "2026-02-28T10:10:00Z",
    )
    publish_country_outage(
        registry_path,
        incident_id="incident-test",
        publication={
            "publication_id": "publication-c",
            "package_uri": str(corrected_package),
            "revision": 2,
            "publication_state": "published",
            "observation_state": "state_complete",
            "data_mode": "live",
            "data_through": "2026-02-28T10:25:00Z",
            "updated_at": "2026-07-25T01:10:01Z",
            "is_final": False,
            "supersedes_publication_id": "publication-b",
            "correction_reason": "迟到源数据补齐并重算历史槽",
            "missing_slots": [],
        },
        kind="correction",
    )
    corrected_resolution = client.get(
        "/api/v2/events/resolve",
        query_string={"ref": LEGACY_REFERENCE},
    ).get_json()
    assert corrected_resolution["publication_id"] == "publication-c"
    assert corrected_resolution["latest_revision"] == 2
    old_series = client.get(
        "/api/v2/country-outages/incident-test/series",
        query_string={"publication_id": "publication-b"},
    ).get_json()
    corrected_series = client.get(
        "/api/v2/country-outages/incident-test/series",
        query_string={"publication_id": "publication-c"},
    ).get_json()
    assert old_series["series"][1]["visible_prefix_vp_count"] == 90
    assert corrected_series["series"][1]["visible_prefix_vp_count"] == 91
    corrected_audit = client.get(
        "/api/v2/country-outages/incident-test/audit",
        query_string={"publication_id": "publication-c"},
    ).get_json()
    assert corrected_audit["algorithm_version"] == (
        "rrc25-iran-go-replay/test"
    )
    assert corrected_audit["mapping_version"] == "mapping-test"
    assert corrected_audit["source_system"] == (
        "country_outage_observation_package"
    )
    assert corrected_audit["evidence_level"] == (
        "aggregated_route_state_with_artifact_hashes"
    )
    assert corrected_audit["supersedes_publication_id"] == "publication-b"
    assert corrected_audit["correction_reason"] == (
        "迟到源数据补齐并重算历史槽"
    )
    assert len(corrected_audit["revision_history"]) == 3

    publish_country_outage(
        registry_path,
        incident_id="incident-test",
        publication={
            "publication_id": "publication-d",
            "package_uri": str(corrected_package),
            "revision": 2,
            "publication_state": "published",
            "observation_state": "state_complete",
            "data_mode": "live",
            "data_through": "2026-02-28T10:25:00Z",
            "updated_at": "2026-07-25T01:15:01Z",
            "is_final": False,
            "processing_status": {
                "state": "failed",
                "updated_at": "2026-07-25T01:15:01Z",
                "attempted_through": "2026-02-28T10:30:00Z",
                "reason": "parse_failed",
                "last_complete_data_through": "2026-02-28T10:25:00Z",
            },
        },
        kind="status",
    )
    failed_resolution = client.get(
        "/api/v2/events/resolve",
        query_string={"ref": LEGACY_REFERENCE},
    ).get_json()
    assert failed_resolution["publication_id"] == "publication-d"
    assert failed_resolution["latest_revision"] == 2
    assert failed_resolution["data_through"] == "2026-02-28T10:25:00Z"
    assert failed_resolution["processing_status"]["state"] == "failed"
    failed_payloads = [
        client.get(
            f"/api/v2/country-outages/incident-test/{endpoint}",
            query_string={"publication_id": "publication-d"},
        ).get_json()
        for endpoint in ("overview", "series", "asns", "audit")
    ]
    assert {
        (
            payload["revision"],
            payload["data_through"],
            payload["processing_status"]["state"],
        )
        for payload in failed_payloads
    } == {(2, "2026-02-28T10:25:00Z", "failed")}


def test_declared_missing_slot_is_null_and_breaks_state_continuity(
    story_package,
    tmp_path,
):
    gap_package = _remove_snapshot_package(
        story_package,
        tmp_path / "package-gap",
        "2026-02-28T10:10:00Z",
    )
    observation = event_story_service.get_country_outage_observation(
        registration={
            "incident_id": "incident-test",
            "legacy_reference": LEGACY_REFERENCE,
            "country": {"code": "IR", "name": "伊朗"},
            "package_uri": str(gap_package),
            "collector_ids": ["rrc25"],
            "vantage_point_count": 96,
            "interval_seconds": 300,
            "revision": 1,
            "publication_state": "published",
            "data_through": "2026-02-28T10:20:00Z",
            "is_final": False,
            "missing_slots": [
                {
                    "observed_at": "2026-02-28T10:10:00Z",
                    "slot_state": "source_unavailable",
                    "missing_reason": "source_file_missing",
                }
            ],
        },
        package_directory=gap_package,
    )

    assert len(observation["series"]) == 4
    gap = observation["series"][1]
    assert gap["slot_state"] == "source_unavailable"
    assert gap["missing_reason"] == "source_file_missing"
    assert gap["visible_prefix_vp_count"] is None
    assert gap["announce_count"] is None
    assert observation["series"][2]["visible_prefix_vp_delta"] is None
    assert observation["series"][2]["announce_delta"] is None
    assert observation["observation_scope"]["observation_count"] == 3
    assert observation["observation_scope"]["expected_observation_count"] == 4
    assert observation["observation_scope"]["missing_observation_count"] == 1
    timeline = observation["asn_state"]["timelines"][0]
    assert timeline["states"][1] == -1
    assert timeline["state_slot_counts"]["unknown"] == 1
    assert observation["audit"]["missing_slots"] == [
        {
            "observed_at": "2026-02-28T10:10:00Z",
            "slot_state": "source_unavailable",
            "missing_reason": "source_file_missing",
        }
    ]


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
            legacy_reference=LEGACY_REFERENCE,
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


def test_event_observation_api_combines_replay_and_core_resource_series(client):
    payload = {
        "schema_version": "event_observation_v1",
        "event_identity": {"country_name": "伊朗"},
        "observation_scope": {},
        "cohort": {},
        "normal_band": {},
        "rule_marker": {},
        "metric_definitions": [],
        "series": [],
        "metric_extrema": {},
        "resource_series": [],
        "resource_metric_extrema": {},
        "annotations": [],
        "asn_state": {},
        "limitations": [],
        "audit": {},
    }
    start_time = quote("2026-02-27 09:12:32", safe="")
    window_start_local = "2026-02-28T18:05:00+08:00"
    window_end_local = "2026-02-28T23:00:00+08:00"
    with patch(
        "web.api.events.api.resolve_country_outage",
        return_value={"incident_id": "incident-test"},
    ), patch(
        "web.api.events.api.get_country_outage_query_context",
        return_value={
            "country_name": "伊朗",
            "window_start_local": window_start_local,
            "window_end_local": window_end_local,
            "resource_state": "available",
        },
    ), patch(
        "web.api.events.api.get_event_detail_data",
        return_value={"start_time": "2026-02-27 09:12:32"},
    ), patch(
        "web.api.events.api.get_country_feature_series",
        return_value={
            "data": [
                {
                    "country": "伊朗",
                    "time_series_data": _resource_series(),
                }
            ]
        },
    ) as feature_query, patch(
        "web.api.events.api.get_legacy_country_outage_observation",
        return_value=payload,
    ) as observation_query:
        response = client.get(
            f"/api/v1/events/observations/country_outage/{start_time}/IR/1/r"
        )

    assert response.status_code == 200
    assert response.get_json()["event_identity"]["country_name"] == "伊朗"
    feature_query.assert_called_once_with(
        start_time="2026-02-28 18:05:00",
        end_time="2026-02-28 23:00:00",
        country="伊朗",
        page_num="1",
        page_size="5",
    )
    assert observation_query.call_args.kwargs["resource_series"] == (
        _resource_series()
    )


def test_event_observation_api_skips_resource_query_for_other_events(client):
    start_time = quote("2026-03-01 00:00:00", safe="")
    with patch(
        "web.api.events.api.get_country_feature_series",
    ) as feature_query, patch(
        "services.country_outage_service.get_event_detail_data",
        return_value={},
    ), patch(
        "web.api.events.api.get_event_detail_data",
    ) as detail_query:
        response = client.get(
            f"/api/v1/events/observations/country_outage/{start_time}/CN/2/r"
        )

    assert response.status_code == 404
    assert response.get_json()["observation_state"] == "not_configured"
    feature_query.assert_not_called()
    detail_query.assert_not_called()
