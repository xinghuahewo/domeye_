from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from services.data_layer_224_310_runtime import (
    DataLayerIntegrityError,
    DataLayer224310Runtime,
    reset_data_layer_runtime_for_tests,
)


CANDIDATE = "domeye_data_candidate_v1_ce3aa006fe1f7dd3e723db9b13baf097"
DATASET = "read_model_dataset_v1_bad9b4c0bd32f7d026356c82dab1b50e"


def canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def tree_digest(root: Path) -> str:
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        raw = path.read_bytes()
        rows.append(f"{digest(raw)}  {len(raw)}  {path.relative_to(root).as_posix()}")
    return digest(("\n".join(rows) + "\n").encode())


def write_json(path: Path, value) -> None:
    path.write_bytes(canonical(value) + b"\n")


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def event_payload(index: int) -> dict:
    incident_id = f"incident-test-{index}"
    hour = 9 + index // 60
    minute = index % 60
    reference = (
        f"country_outage/2026-02-27 {hour:02d}:{minute:02d}:00/IR/{index + 1}/r"
    )
    publication_id = f"observation-publication-{index}"
    analysis_id = f"analysis-publication-{index}"
    return {
        "schema_version": "rrc25-event-read-model/v1",
        "candidate_id": CANDIDATE,
        "read_model_dataset_id": DATASET,
        "snapshot_id": f"snapshot-{index}",
        "snapshot_sha256": "a" * 64,
        "incident": {
            "incident_id": incident_id,
            "legacy_reference": reference,
            "country_code": "IR",
            "country_name": "伊朗",
            "event_type": "country_outage",
            "detected_at": "2026-02-27T01:15:00Z",
        },
        "observation_publication": {
            "publication_id": publication_id,
            "revision": 3,
            "data_through": "2026-03-11T00:00:00Z",
            "content_sha256": "b" * 64,
        },
        "analysis_publication": {
            "publication_id": analysis_id,
            "revision": 3,
            "data_through": "2026-03-11T00:00:00Z",
            "content_sha256": "c" * 64,
            "trend_profile": {
                "metric": "combined_fixed_cohort_visibility_ratio",
                "direction": "down",
                "start": 1.0,
                "end": 0.9,
                "change": -0.1,
                "minimum": {"at": "2026-02-28T00:00:00Z", "value": 0.8},
                "maximum": {"at": "2026-02-24T00:05:00Z", "value": 1.0},
            },
        },
        "fact_set": [
            {
                "fact_id": f"fact-{index}-1",
                "stage": "detected",
                "observed_at": "2026-02-27T01:15:00Z",
            },
            {
                "fact_id": f"fact-{index}-2",
                "stage": "final",
                "observed_at": "2026-03-11T00:00:00Z",
            },
        ],
        "series_ref": {
            "series_id": "series-ir",
            "artifact_uri": "series/IR.json.gz",
            "artifact_sha256": "",
            "content_sha256": "series-content",
        },
        "evidence_refs": [
            {
                "evidence_view_id": "evidence-ir",
                "derived_from_route_state_id": "route-state-final",
                "content_sha256": "e" * 64,
            }
        ],
        "limitations": ["仅描述 RRC25 控制面观测。"],
    }


def build_fixture(tmp_path: Path) -> Path:
    release = tmp_path / "release"
    read_model = release / "read-model"
    (read_model / "series").mkdir(parents=True)
    values = [[100] * 4320, [10] * 4320, [90] * 4320, [9] * 4320]
    values += [[91] * 4320, [9] * 4320, [2] * 4320, [0] * 4320]
    values += [[1] * 4320, [0] * 4320]
    compact = {
        "schema_version": "rrc25-compact-country-series/v1",
        "candidate_id": CANDIDATE,
        "collector_id": "rrc25",
        "country_code": "IR",
        "series_id": "series-ir",
        "content_sha256": "series-content",
        "first_state_point_utc": "2026-02-24T00:05:00Z",
        "point_count": 4320,
        "step_seconds": 300,
        "columns": [
            "baseline_v4", "baseline_v6", "cohort_visible_v4",
            "cohort_visible_v6", "current_visible_v4", "current_visible_v6",
            "announcement_v4", "announcement_v6", "withdrawal_v4",
            "withdrawal_v6",
        ],
        "values": values,
        "quality": {"status": "complete", "missing": 0, "finality": "final"},
    }
    series_raw = gzip.compress(canonical(compact))
    series_path = read_model / "series" / "IR.json.gz"
    series_path.write_bytes(series_raw)

    events = []
    for index in range(81):
        payload = event_payload(index)
        payload["series_ref"]["artifact_sha256"] = digest(series_raw)
        events.append({"payload": json.dumps(payload, ensure_ascii=False)})
    write_tsv(read_model / "event-read-model.tsv.gz", events)
    manifest = {
        "schema_version": "rrc25-read-model-store/v1",
        "status": "complete",
        "collector_id": "rrc25",
        "candidate_id": CANDIDATE,
        "dataset_id": DATASET,
        "window_start_utc": "2026-02-24T00:00:00Z",
        "window_end_exclusive_utc": "2026-03-11T00:00:00Z",
        "state_point_count": 4320,
        "event_count": 81,
        "run_id": "run-test",
        "implementation_id": "1" * 40,
        "api_read_semantics": "precompiled_read_model_only",
        "prefix_vp_semantics": "derived_view_not_independent_fact",
        "source_route_state_content_sha256": "f" * 64,
    }
    write_json(read_model / "manifest.json", manifest)
    manifest_sha = digest((read_model / "manifest.json").read_bytes())
    read_model_tree_sha = tree_digest(read_model)
    country = {
        "cohort_id": "cohort-ir",
        "prefix_vp_count": 110,
        "ipv4_prefix_vp_count": 100,
        "ipv6_prefix_vp_count": 10,
        "origin_asn_count": 12,
        "vantage_point_count": 4,
    }
    countries = {"IR": country}
    countries.update({f"X{index}": country for index in range(42)})
    production_index = {
        "schema_version": "domeye_data_layer_production_index_v1",
        "status": "complete",
        "collector_id": "rrc25",
        "candidate_id": CANDIDATE,
        "read_model_dataset_id": DATASET,
        "read_model_manifest_sha256": manifest_sha,
        "read_model_tree_sha256": read_model_tree_sha,
        "mapping_version": "mapping-test",
        "countries": countries,
    }
    write_json(release / "production-index.json", production_index)
    selection = {
        "schema_version": "domeye_data_layer_production_selection_v1",
        "status": "selected",
        "selected_by_production": True,
        "selected_at": "2026-08-07T00:00:00Z",
        "collector_id": "rrc25",
        "window_start_utc": "2026-02-24T00:00:00Z",
        "window_end_exclusive_utc": "2026-03-11T00:00:00Z",
        "candidate_id": CANDIDATE,
        "read_model_dataset_id": DATASET,
        "shadow_migration_dataset_id": "shadow-test",
        "read_model_root": "read-model",
        "read_model_manifest_sha256": manifest_sha,
        "read_model_tree_sha256": read_model_tree_sha,
        "production_index_path": "production-index.json",
        "production_index_sha256": digest((release / "production-index.json").read_bytes()),
    }
    write_json(release / "PRODUCTION-SELECTION.json", selection)
    return release / "PRODUCTION-SELECTION.json"


def test_runtime_reads_only_selected_precompiled_publication(tmp_path, monkeypatch):
    selection = build_fixture(tmp_path)
    monkeypatch.setenv("DOMEYE_DATA_LAYER_224_310_SELECTION", str(selection))
    reset_data_layer_runtime_for_tests()
    runtime = DataLayer224310Runtime(selection)
    reference = "country_outage/2026-02-27 09:00:00/IR/1/r"
    resolution = runtime.resolve(reference)
    assert resolution["selected_by_production"] is True
    assert resolution["publication_id"] == "observation-publication-0"
    overview = runtime.overview("incident-test-0", resolution["publication_id"])
    assert overview["observation_scope"]["observation_count"] == 4320
    assert overview["cohort"]["prefix_vp_count"] == 110
    assert overview["capabilities"]["asn_matrix"]["state"] == "unavailable"
    series = runtime.series("incident-test-0", resolution["publication_id"])
    assert series["schema_version"] == "country_outage_compact_series_v1"
    assert len(series["series_contract"]["values"][0]) == 4320
    trend = runtime.trend("incident-test-0", resolution["publication_id"])
    assert trend["snapshot"]["publication_id"] == resolution["publication_id"]
    assert trend["evidence_graph"]["causal_relations_allowed"] is False


def test_runtime_fails_closed_when_read_model_changes(tmp_path):
    selection = build_fixture(tmp_path)
    target = selection.parent / "read-model" / "series" / "IR.json.gz"
    target.write_bytes(target.read_bytes() + b"tampered")
    with pytest.raises(DataLayerIntegrityError, match="目录摘要"):
        DataLayer224310Runtime(selection)
