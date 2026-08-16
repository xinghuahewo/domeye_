from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import pytest

from services.country_outage_general_read_model import (
    READ_MODEL_ENV,
    CountryOutageGeneralReadModelRuntime,
    GeneralReadModelIntegrityError,
    GeneralReadModelPublicationNotFound,
    reset_country_outage_general_read_model_for_tests,
)


REFERENCE = "country_outage/2026-02-27 09:12:32/IR/1/r"
INCIDENT_ID = "incident-test-general-read-model"
PUBLICATION_ID = "country_outage_publication_v1_0123456789abcdef0123456789abcdef"
EVENT_ID = "country_event_read_model_v1_0123456789abcdef0123456789abcdef"


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def content_sha(value):
    payload = dict(value)
    payload.pop("content_sha256", None)
    return hashlib.sha256(canonical(payload)).hexdigest()


def write_object(root: Path, relative: str, value: dict):
    raw = canonical(value) + b"\n"
    compressed = gzip.compress(raw, mtime=0)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compressed)
    return {
        "path": relative,
        "size_bytes": len(compressed),
        "sha256": hashlib.sha256(compressed).hexdigest(),
        "content_sha256": hashlib.sha256(raw).hexdigest(),
    }


def write_rows(root: Path, relative: str, rows: list[dict]):
    raw = b"".join(canonical(row) + b"\n" for row in rows)
    compressed = gzip.compress(raw, mtime=0)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compressed)
    return {
        "path": relative,
        "row_count": len(rows),
        "size_bytes": len(compressed),
        "sha256": hashlib.sha256(compressed).hexdigest(),
        "content_sha256": hashlib.sha256(raw).hexdigest(),
    }


@pytest.fixture()
def general_store(tmp_path: Path) -> Path:
    root = tmp_path / "read-model"
    root.mkdir()
    overview = {
        "schema_version": "country-outage-general-overview-artifact/v1",
        "event_read_model_id": EVENT_ID,
        "publication_id": PUBLICATION_ID,
        "revision": 1,
        "publication_state": "published",
        "observation_state": "evidence_complete",
        "quality_state": "complete",
        "missing_slot_count": 0,
        "collector_id": "rrc25",
        "incident_id": INCIDENT_ID,
        "legacy_reference": REFERENCE,
        "country_code": "IR",
        "detected_at_utc": "2026-02-27T01:12:32Z",
        "event_end_at_utc": None,
        "event_duration_seconds": None,
        "lifecycle_state": "event_end_unknown",
        "is_final_in_data_range": False,
        "window_start_utc": "2026-02-27T00:10:00Z",
        "window_end_utc": "2026-02-27T00:20:00Z",
        "data_through": "2026-02-27T00:20:00Z",
        "interval_seconds": 300,
        "state_point_count": 2,
        "cohort": {
            "cohort_id": "country_event_cohort_v1_test",
            "fixed_prefix_count": 2,
            "fixed_asn_count": 2,
            "independent_direction_relation_count": 4,
            "new_prefix_count": 0,
        },
        "final_values": {
            "interrupted_prefix_count": 1,
            "completely_interrupted_prefix_count": 0,
            "affected_asn_count": 1,
            "route_interrupted_asn_count": 0,
        },
        "peaks": {
            "interrupted_prefix_count": {
                "value": 1,
                "state_point_utc": "2026-02-27T00:20:00Z",
            }
        },
        "affected_as_count": 2,
        "route_interrupted_as_count": 1,
        "path_downstream_relation_count": 2,
        "concurrent_path_downstream_relation_count": 1,
        "capabilities": {
            "overview": "available",
            "event_series": "available",
            "affected_as": "available",
            "path_downstreams": "available",
            "full_path_evidence": "audit_only",
        },
        "semantic_boundary": "rrc25_control_plane_observation_not_user_impact_or_cause",
    }
    overview["content_sha256"] = content_sha(overview)
    series = {
        "schema_version": "country-outage-general-series-artifact/v1",
        "event_read_model_id": EVENT_ID,
        "publication_id": PUBLICATION_ID,
        "incident_id": INCIDENT_ID,
        "event_metric_id": "country_event_metric_v1_test",
        "cohort_id": "country_event_cohort_v1_test",
        "window_start_utc": "2026-02-27T00:10:00Z",
        "window_end_utc": "2026-02-27T00:20:00Z",
        "interval_seconds": 300,
        "point_count": 2,
        "timestamps": ["2026-02-27T00:15:00Z", "2026-02-27T00:20:00Z"],
        "track_definitions": {
            "interrupted_prefix_count": {
                "label": "前缀路由中断",
                "unit": "prefix",
                "definition": "测试",
            }
        },
        "tracks": {"interrupted_prefix_count": [0, 1]},
    }
    series["content_sha256"] = content_sha(series)
    asns = [
        {
            "schema_version": "country-outage-general-affected-as/v1",
            "event_read_model_id": EVENT_ID,
            "publication_id": PUBLICATION_ID,
            "rank": rank,
            "asn": asn,
            "as_name": f"AS-{asn}",
            "organization": "测试组织",
            "nature": "ISP",
            "name_state": "observed",
            "organization_state": "observed",
            "nature_state": "observed",
            "event_classification": classification,
            "fixed_prefix_count": 1,
            "peak_partial_prefix_count": int(classification == "affected"),
            "peak_complete_prefix_count": int(classification == "route_interrupted"),
            "peak_invisible_direction_count": 2,
            "path_downstream_asn_count": 1,
            "concurrent_downstream_asn_count": int(rank == 1),
        }
        for rank, (asn, classification) in enumerate(
            ((64500, "route_interrupted"), (64501, "affected")), start=1
        )
    ]
    downstreams = [
        {
            "schema_version": "country-outage-general-path-downstream/v1",
            "event_read_model_id": EVENT_ID,
            "publication_id": PUBLICATION_ID,
            "affected_asn": 64500 + index,
            "downstream_asn": 64600 + index,
            "downstream_as_name": f"DOWN-{index}",
            "downstream_organization": "测试下游",
            "downstream_nature": "ISP",
            "downstream_name_state": "observed",
            "downstream_organization_state": "observed",
            "downstream_nature_state": "observed",
            "observed_path_count": 1,
            "associated_fixed_prefix_count": 1,
            "independent_direction_count": 1,
            "route_observation_count": 1,
            "concurrent_state_point_count": int(index == 0),
            "first_concurrent_state_point_utc": (
                "2026-02-27T00:20:00Z" if index == 0 else None
            ),
            "last_concurrent_state_point_utc": (
                "2026-02-27T00:20:00Z" if index == 0 else None
            ),
            "peak_concurrent_interrupted_prefix_count": int(index == 0),
            "peak_concurrent_ipv4_address_count": 256 if index == 0 else 0,
            "peak_concurrent_ipv6_slash48_count": 0,
            "path_samples": [
                {
                    "prefix": f"192.0.{index}.0/24",
                    "address_family": "ipv4",
                    "as_path_id": f"asp_v1_{index:064x}",
                    "as_path_canonical": f"64496 {64500 + index} {64600 + index}",
                    "independent_peer_asns": [64496],
                    "route_observation_count": 1,
                }
            ],
            "relationship_semantics": "observed_ordered_rrc25_path_association_not_dependency_or_cause",
        }
        for index in range(2)
    ]
    directory = "events/IR/slot-test"
    event = {
        "schema_version": "country-outage-general-event-read-model/v1",
        "status": "complete",
        "event_read_model_id": EVENT_ID,
        "publication_id": PUBLICATION_ID,
        "revision": 1,
        "publication_state": "published",
        "incident_id": INCIDENT_ID,
        "legacy_reference": REFERENCE,
        "country_code": "IR",
        "cohort_id": "country_event_cohort_v1_test",
        "event_metric_id": "country_event_metric_v1_test",
        "event_as_path_id": "country_event_as_path_v1_test",
        "window_start_utc": "2026-02-27T00:10:00Z",
        "window_end_utc": "2026-02-27T00:20:00Z",
        "data_through": "2026-02-27T00:20:00Z",
        "lifecycle_state": "event_end_unknown",
        "is_final_in_data_range": False,
        "state_point_count": 2,
        "affected_as_count": 2,
        "path_downstream_relation_count": 2,
        "path_sample_count": 2,
        "overview": write_object(root, f"{directory}/overview.json.gz", overview),
        "series": write_object(root, f"{directory}/series.json.gz", series),
        "affected_as": write_rows(root, f"{directory}/affected-as.jsonl.gz", asns),
        "path_downstreams": write_rows(
            root, f"{directory}/path-downstreams.jsonl.gz", downstreams
        ),
    }
    event["content_sha256"] = content_sha(event)
    manifest = {
        "schema_version": "country-outage-general-read-model-store/v1",
        "status": "complete",
        "run_id": "general_read_model_run_v1_0123456789abcdef0123456789abcdef",
        "dataset_id": "general_read_model_dataset_v1_0123456789abcdef0123456789abcdef",
        "collector_id": "rrc25",
        "window_start_utc": "2026-02-24T00:00:00Z",
        "window_end_exclusive_utc": "2026-03-11T00:00:00Z",
        "implementation_id": "git:" + "1" * 40,
        "source_event_cohort_dataset_id": "event_cohort_dataset_v1_test",
        "source_event_cohort_content_sha256": "1" * 64,
        "source_event_cohort_manifest_sha256": "2" * 64,
        "source_event_metric_dataset_id": "event_metric_dataset_v1_test",
        "source_event_metric_content_sha256": "3" * 64,
        "source_event_metric_manifest_sha256": "4" * 64,
        "source_event_as_path_dataset_id": "event_as_path_dataset_v1_test",
        "source_event_as_path_content_sha256": "5" * 64,
        "source_event_as_path_manifest_sha256": "6" * 64,
        "source_lifecycle_snapshot_id": "event_lifecycle_snapshot_v1_test",
        "source_lifecycle_snapshot_content_sha256": "7" * 64,
        "api_read_semantics": "precompiled_event_window_read_model_only",
        "pagination_semantics": "stable_server_side_pages_maximum_60_items",
        "path_evidence_semantics": "bounded_real_path_samples_full_evidence_remains_in_s3_audit_artifact",
        "causal_boundary": "rrc25_path_association_is_not_dependency_propagation_user_impact_or_cause",
        "event_count": 1,
        "state_point_count": 2,
        "affected_as_count": 2,
        "path_downstream_relation_count": 2,
        "path_sample_count": 2,
        "events": [event],
    }
    manifest["content_sha256"] = content_sha(manifest)
    raw = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"
    (root / "manifest.json").write_bytes(raw)
    (root / "COMPLETE.json").write_bytes(raw)
    return root


def test_runtime_binds_one_publication_and_event_window(general_store: Path):
    runtime = CountryOutageGeneralReadModelRuntime(general_store)
    resolution = runtime.resolve(REFERENCE)
    assert resolution is not None
    assert resolution["publication_id"] == PUBLICATION_ID
    assert resolution["window_start_utc"] == "2026-02-27T00:10:00Z"
    assert resolution["window_end_utc"] == "2026-02-27T00:20:00Z"
    assert resolution["is_final_in_data_range"] is False
    overview = runtime.overview(INCIDENT_ID, PUBLICATION_ID)
    series = runtime.series(INCIDENT_ID, PUBLICATION_ID)
    assert overview is not None and series is not None
    assert overview["cohort"]["fixed_prefix_count"] == 2
    assert series["point_count"] == 2
    assert series["tracks"]["interrupted_prefix_count"] == [0, 1]
    with pytest.raises(GeneralReadModelPublicationNotFound):
        runtime.overview(INCIDENT_ID, "wrong-publication")


def test_runtime_pages_asns_and_bounded_real_paths(general_store: Path):
    runtime = CountryOutageGeneralReadModelRuntime(general_store)
    asns = runtime.affected_asns(
        INCIDENT_ID,
        PUBLICATION_ID,
        page=1,
        page_size=1,
        classification="all",
    )
    assert asns is not None
    assert asns["total"] == 2 and len(asns["items"]) == 1
    assert asns["items"][0]["event_classification"] == "route_interrupted"
    paths = runtime.path_downstreams(
        INCIDENT_ID,
        PUBLICATION_ID,
        page=1,
        page_size=60,
        affected_asn=64500,
        scope="concurrent",
    )
    assert paths is not None
    assert paths["total"] == 1
    assert len(paths["items"][0]["path_samples"]) == 1
    assert paths["items"][0]["relationship_semantics"].endswith(
        "not_dependency_or_cause"
    )


def test_runtime_fails_closed_when_artifact_changes(general_store: Path):
    runtime = CountryOutageGeneralReadModelRuntime(general_store)
    target = general_store / runtime.manifest["events"][0]["series"]["path"]
    target.write_bytes(target.read_bytes() + b"tampered")
    with pytest.raises(GeneralReadModelIntegrityError):
        runtime.series(INCIDENT_ID, PUBLICATION_ID)


def test_http_candidate_uses_general_read_model_before_old_global_model(
    client,
    monkeypatch,
    general_store: Path,
):
    monkeypatch.setenv(READ_MODEL_ENV, str(general_store))
    reset_country_outage_general_read_model_for_tests()
    resolution = client.get(
        "/api/v2/events/resolve", query_string={"ref": REFERENCE}
    )
    assert resolution.status_code == 200
    identity = resolution.get_json()
    assert identity["schema_version"] == "country_outage_general_resolution_v1"
    assert identity["publication_id"] == PUBLICATION_ID
    incident = quote(identity["incident_id"], safe="")
    common = {"publication_id": PUBLICATION_ID}
    overview = client.get(
        f"/api/v2/country-outages/{incident}/overview", query_string=common
    )
    series = client.get(
        f"/api/v2/country-outages/{incident}/series", query_string=common
    )
    asns = client.get(
        f"/api/v2/country-outages/{incident}/asns",
        query_string={**common, "page_size": 1},
    )
    paths = client.get(
        f"/api/v2/country-outages/{incident}/path-downstreams",
        query_string={**common, "affected_asn": 64500, "scope": "concurrent"},
    )
    audit = client.get(
        f"/api/v2/country-outages/{incident}/audit", query_string=common
    )
    assert [item.status_code for item in (overview, series, asns, paths, audit)] == [
        200,
        200,
        200,
        200,
        200,
    ]
    assert overview.get_json()["schema_version"] == "country_outage_general_overview_v1"
    assert series.get_json()["point_count"] == 2
    assert asns.get_json()["total"] == 2
    assert paths.get_json()["total"] == 1
    assert audit.get_json()["dataset_id"].startswith("general_read_model_dataset_v1_")
    invalid_sort = client.get(
        f"/api/v2/country-outages/{incident}/asns",
        query_string={**common, "sort": "longest_fully_invisible_desc"},
    )
    assert invalid_sort.status_code == 400
    assert invalid_sort.get_json()["observation_state"] == "invalid_query"
    wrong = client.get(
        f"/api/v2/country-outages/{incident}/overview",
        query_string={"publication_id": "wrong"},
    )
    assert wrong.status_code == 404
    assert wrong.get_json()["observation_state"] == "publication_not_found"
    reset_country_outage_general_read_model_for_tests()


def test_path_endpoint_does_not_return_empty_success_when_not_ready(
    client,
    monkeypatch,
):
    monkeypatch.delenv(READ_MODEL_ENV, raising=False)
    reset_country_outage_general_read_model_for_tests()
    response = client.get(
        f"/api/v2/country-outages/{INCIDENT_ID}/path-downstreams"
    )
    assert response.status_code == 503
    assert response.get_json()["observation_state"] == "unavailable"


def test_selected_invalid_store_fails_closed_instead_of_falling_back(
    client,
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setenv(READ_MODEL_ENV, str(tmp_path / "missing-read-model"))
    reset_country_outage_general_read_model_for_tests()
    response = client.get(
        "/api/v2/events/resolve", query_string={"ref": REFERENCE}
    )
    assert response.status_code == 503
    payload = response.get_json()
    assert payload["observation_state"] == "unavailable"
    assert "目录不存在或不是绝对目录" in payload["msg"]
