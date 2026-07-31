import copy
import gzip
import hashlib
import json
import os
from pathlib import Path

import pytest

from services import p0_data_service


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROFILE_SHA = "44efcf2b5f3c3aee0a9f3966b6eef4e9855fbfd6ea244dcf15231865f38e0b46"
INVENTORY_SHA = "b" * 64
RELEASE_ID = "p0-fixed-overlay-test"
EVENT_CONFIG = {
    "hijack": ("hijack_202603", {"source": "r", "prefix": "203.0.113.0/24", "hijack_eventid": 1}, "prefix", "203.0.113.0/24"),
    "sub_hijack": ("sub_hijack_202603", {"source": "r", "prefix": "203.0.113.0/24", "sub_hijack_eventid": 2}, "prefix", "203.0.113.0/24"),
    "leak": ("leak_event_202603", {"source": "r", "prefix": "198.51.100.0/24", "leak_event_id": 3}, "prefix", "198.51.100.0/24"),
    "prefix_outage": ("prefix_outage_202603", {"source": "r", "prefix": "192.0.2.0/24", "outage_id": 4, "asn": "64504"}, "prefix", "192.0.2.0/24"),
    "as_outage": ("as_outage_202603", {"source": "r", "asn": "64505", "outage_id": 5}, "asn", "64505"),
    "country_outage": ("country_outage_202603", {"source": "r", "country": "CN", "outage_id": 6}, "country", "CN"),
}


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(canonical(value) + "\n", encoding="utf-8")


def write_gzip_jsonl(path, rows):
    content = b"".join((canonical(row) + "\n").encode("utf-8") for row in rows)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            compressed.write(content)
    return {
        "name": path.name,
        "media_type": "application/x-ndjson+gzip",
        "row_count": len(rows),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "sha256": sha(path),
        "size_bytes": path.stat().st_size,
    }


def read_gzip_jsonl(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def inventory(path):
    return {
        "name": path.name,
        "media_type": "application/json",
        "sha256": sha(path),
        "size_bytes": path.stat().st_size,
    }


def close_component(directory):
    names = sorted(path.name for path in directory.iterdir() if path.name != "SHA256SUMS")
    (directory / "SHA256SUMS").write_text(
        "".join("{}  {}\n".format(sha(directory / name), name) for name in names),
        encoding="utf-8",
    )


def profile():
    return {
        "schema_version": 1,
        "id": "feb-mar-2026",
        "mode": "fixed",
        "timezone": "Asia/Shanghai",
        "window_start": "2026-03-31T07:25:00+08:00",
        "window_end_exclusive": "2026-03-31T07:45:00+08:00",
        "snapshot_time": "2026-03-31T07:44:59+08:00",
        "api_profile": "core",
    }


def create_d2(root):
    directory = root / "d2"
    directory.mkdir()
    files = {}
    for name, rows in (
        ("incidents.jsonl.gz", [{"incident": index} for index in range(6)]),
        ("links.jsonl.gz", [{"link": index} for index in range(6)]),
        ("collision_groups.jsonl.gz", []),
        ("quarantine.jsonl.gz", []),
    ):
        files[name] = write_gzip_jsonl(directory / name, rows)
    manifest = {
        "schema_version": "p0_normalization_candidate_v1",
        "candidate_kind": "readonly_legacy_fact_normalization",
        "candidate_fingerprint_sha256": "1" * 64,
        "data_profile": profile(),
        "window_utc": {
            "start": "2026-03-30T23:25:00Z",
            "end_exclusive": "2026-03-30T23:45:00Z",
        },
        "source": {
            "release_id": RELEASE_ID,
            "state_sha256": "a" * 64,
            "manifest_sha256": "c" * 64,
            "database_manifest_sha256": "d" * 64,
            "inventory_sha256": INVENTORY_SHA,
            "database": {"system_identifier": "test-system-identifier"},
            "provenance": {
                "data_profile_sha256": PROFILE_SHA,
                "probe_sha256": "e" * 64,
            },
            "normalizer_hashes": {"normalizer.py": "2" * 64},
        },
        "source_table_counts": {},
        "files": files,
        "summary": {
            "incident_count": 6,
            "link_count": 6,
            "unexplained_reverse_orphan_count": 0,
            "unexplained_forward_reference_count": 0,
            "event_type_counts": {name: 1 for name in EVENT_CONFIG},
        },
        "sample": {"enabled": False, "max_events": None, "admissible": True},
        "admission": {
            "status": "legacy_candidate_ready",
            "eligible_for_release_gate": True,
            "blocking_reasons": [],
            "raw_traceable": False,
        },
        "classification": "observation_only",
        "causal_conclusion": None,
    }
    manifest["candidate_fingerprint_sha256"] = p0_data_service._d2_fingerprint(
        manifest
    )
    write_json(directory / "manifest.json", manifest)
    (directory / "摘要.md").write_text("# D2 候选\n", encoding="utf-8")
    close_component(directory)
    return manifest


def create_d3(root):
    directory = root / "d3"
    directory.mkdir()
    raw_profile = profile()
    update_artifacts = []
    for index, slot in enumerate(("2325", "2330"), 1):
        digest = str(index) * 64
        update_artifacts.append(
            {
                "artifact_id": "art_v1_"
                + hashlib.sha256(
                    canonical(
                        {"schema": "artifact_id_v1", "file_sha256": digest}
                    ).encode("utf-8")
                ).hexdigest()[:32],
                "artifact_id_schema": "artifact_id_v1",
                "collector_id": "rrc25",
                "artifact_type": "update",
                "artifact_time_utc": "2026-03-30T23:{}:00Z".format(slot[-2:]),
                "relative_path": "rrc25/2026.03/updates.20260330.{}.gz".format(slot),
                "filename_family": "updates",
                "compression": "gz",
                "size_bytes": index * 100,
                "file_sha256": digest,
            }
        )
    update_ranges = [
        {
            "start_time_utc": "2026-03-30T23:35:00Z",
            "end_time_exclusive_utc": "2026-03-30T23:40:00Z",
            "slot_count": 1,
            "value_state": "parse_failed",
        },
        {
            "start_time_utc": "2026-03-30T23:40:00Z",
            "end_time_exclusive_utc": "2026-03-30T23:45:00Z",
            "slot_count": 1,
            "value_state": "source_unavailable",
        },
    ]
    payload = {
        "schema_version": 1,
        "manifest_kind": "mrt_artifact_manifest",
        "artifact_id_schema": "artifact_id_v1",
        "data_profile": {
            "id": raw_profile["id"],
            "timezone": raw_profile["timezone"],
            "window_start": raw_profile["window_start"],
            "window_end_exclusive": raw_profile["window_end_exclusive"],
            "window_start_utc": "2026-03-30T23:25:00Z",
            "window_end_exclusive_utc": "2026-03-30T23:45:00Z",
        },
        "filename_timestamp_timezone": "UTC",
        "collector_allowlist": ["rrc25"],
        "scan_policy": {
            "out_of_window": "exclude_without_hash",
            "invalid_in_window": "full_hash_quarantine_exclude_from_available_slots",
            "compression_envelope_validation": "full_stream_to_eof_crc_or_equivalent",
        },
        "artifacts": update_artifacts,
        "invalid_in_window": [
            {
                "collector_id": "rrc25",
                "artifact_type": "update",
                "artifact_time_utc": "2026-03-30T23:35:00Z",
                "relative_path": "rrc25/2026.03/updates.20260330.2335.gz",
                "filename_family": "updates",
                "compression": "gz",
                "size_bytes": 0,
                "file_sha256": hashlib.sha256(b"").hexdigest(),
                "value_state": "parse_failed",
                "missing_reason": "empty_file",
            }
        ],
        "summary": {
            "artifact_count": 2,
            "size_bytes": 300,
            "by_artifact_type": {
                "rib": {"artifact_count": 0, "size_bytes": 0},
                "update": {"artifact_count": 2, "size_bytes": 300},
            },
            "by_collector": [
                {"collector_id": "rrc25", "artifact_count": 2, "size_bytes": 300}
            ],
            "excluded_out_of_window": {"file_count": 0, "size_bytes": 0},
            "invalid_in_window": {
                "file_count": 1,
                "size_bytes": 0,
                "by_missing_reason": {
                    "compressed_stream_invalid": {"file_count": 0, "size_bytes": 0},
                    "compression_magic_mismatch": {"file_count": 0, "size_bytes": 0},
                    "empty_file": {"file_count": 1, "size_bytes": 0},
                },
            },
        },
        "coverage": {
            "expected_slots": 4,
            "available_slots": 2,
            "missing_slots": 2,
            "coverage_ratio": 0.5,
            "coverage_status": "partial",
            "missing_value_state": "mixed",
            "by_collector": [
                {
                    "collector_id": "rrc25",
                    "by_artifact_type": {
                        "update": {
                            "expected_slots": 4,
                            "available_slots": 2,
                            "missing_slots": 2,
                            "coverage_ratio": 0.5,
                            "coverage_status": "partial",
                            "missing_ranges": update_ranges,
                        },
                        "rib": {
                            "expected_slots": 0,
                            "available_slots": 0,
                            "missing_slots": 0,
                            "coverage_ratio": 1.0,
                            "coverage_status": "complete",
                            "missing_ranges": [],
                        },
                    },
                }
            ],
            "missing_ranges": [
                {"collector_id": "rrc25", "artifact_type": "update", **item}
                for item in update_ranges
            ],
        },
    }
    manifest = {
        **payload,
        "manifest_fingerprint_sha256": canonical_sha(
            {"schema": "mrt_artifact_manifest_fingerprint_v1", "manifest": payload}
        ),
    }
    manifest_path = directory / "p0-artifact-manifest.json"
    write_json(manifest_path, manifest)
    summary = {
        "schema_version": 1,
        "provenance": {"data_profile": {"sha256": PROFILE_SHA}},
        "manifest": {
            "sha256": sha(manifest_path),
            "fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
        },
        "verification": {
            "verified": True,
            "manifest_fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
        },
    }
    write_json(directory / "p0-artifact-manifest.summary.zh.json", summary)
    close_component(directory)
    return manifest


def rewrite_d3_manifest(directory, manifest):
    payload = dict(manifest)
    payload.pop("manifest_fingerprint_sha256", None)
    manifest["manifest_fingerprint_sha256"] = canonical_sha(
        {"schema": "mrt_artifact_manifest_fingerprint_v1", "manifest": payload}
    )
    manifest_path = directory / "p0-artifact-manifest.json"
    write_json(manifest_path, manifest)

    summary_path = directory / "p0-artifact-manifest.summary.zh.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["manifest"]["sha256"] = sha(manifest_path)
    summary["manifest"]["fingerprint_sha256"] = manifest[
        "manifest_fingerprint_sha256"
    ]
    summary["verification"]["manifest_fingerprint_sha256"] = manifest[
        "manifest_fingerprint_sha256"
    ]
    write_json(summary_path, summary)
    close_component(directory)


def rewrite_metric_series(directory, records):
    series_path = directory / "metric-series.jsonl.gz"
    metric_inventory = write_gzip_jsonl(series_path, records)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][series_path.name] = metric_inventory
    manifest["candidate_fingerprint_sha256"] = p0_data_service._metric_fingerprint(
        manifest
    )
    write_json(manifest_path, manifest)
    close_component(directory)


def rewrite_metric_reconciliation(directory, reconciliation):
    reconciliation_path = directory / "metric-reconciliation-summary.json"
    payload = dict(reconciliation)
    payload.pop("summary_fingerprint_sha256", None)
    reconciliation["summary_fingerprint_sha256"] = canonical_sha(
        {
            "schema": "metric_reconciliation_summary_fingerprint_v1",
            "summary": payload,
        }
    )
    write_json(reconciliation_path, reconciliation)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][reconciliation_path.name] = inventory(reconciliation_path)
    manifest["candidate_fingerprint_sha256"] = p0_data_service._metric_fingerprint(
        manifest
    )
    write_json(manifest_path, manifest)
    close_component(directory)


def normalized_incident(event_type):
    table, key, object_type, object_id = EVENT_CONFIG[event_type]
    event_id = list(EVENT_CONFIG).index(event_type) + 1
    status = "observed_paths" if event_type in {"hijack", "prefix_outage", "as_outage"} else "not_applicable"
    phase = {
        "source_field": "fixture_phase",
        "semantics": "route_observation_not_causal_trace",
        "supports_recovery": False,
        "status": status,
        "missing_reason": None if status == "observed_paths" else "fixture_missing",
        "observations": [[64500, 64501]] if status == "observed_paths" else None,
    }
    return {
        "schema_version": "p0_incident_normalization_v1",
        "incident_id": "inc_v1_" + format(event_id, "x") * 24,
        "incident_id_schema": "incident_id_v1",
        "event_type": event_type,
        "source_code": "r",
        "source_table": table,
        "source_primary_key": key,
        "detail_reference": "{}/2026-03-31 07:30:00/{}/{}/r".format(event_type, object_id.replace("/", "-"), event_id),
        "event_time_utc": "2026-03-30T23:{:02d}:00Z".format(25 + event_id),
        "end_time_utc": None,
        "duration_seconds": None,
        "risk_level": None,
        "affected_objects": [
            {
                "object_type": object_type,
                "object_id": object_id,
                "role": "affected",
                "source_field": "detail_url.problem",
            }
        ],
        "collection_quality": [],
        "phase_coverage": {name: copy.deepcopy(phase) for name in ("before", "during", "after")},
        "fact_link_status": "matched",
        "field_quality": [
            {
                "field": "detector_version",
                "status": "not_retained",
                "missing_reason": "legacy_field_not_retained",
            }
        ],
        "collision_group_id": None,
        "quarantine_id": None,
        "detector_version": None,
        "classification": "observation_only",
        "causal_conclusion": None,
    }


def create_metric(root, d2, d3):
    directory = root / "metric"
    directory.mkdir()
    template = json.loads(
        (PROJECT_ROOT / "contracts/data/fixtures/metric-series/valid-global-announce-with-gaps.json").read_text(encoding="utf-8")
    )
    records = []
    for metric_name, definition in sorted(p0_data_service.METRIC_DEFINITIONS.items()):
        record = copy.deepcopy(template)
        record["metric_name"] = metric_name
        record["unit"] = definition.unit
        record["aggregation"] = definition.aggregation
        record["formula"] = definition.formula
        record["formula_version"] = definition.formula_version
        record["window"]["end"] = "2026-03-30T23:45:00Z"
        record["expected_sample_count"] = 4
        if metric_name in p0_data_service.D3_ALIGNED_METRICS:
            record["points"][2]["value"] = None
            record["points"][2]["value_state"] = "parse_failed"
            record["points"][2]["missing_reason"] = "parse_failed"
            record["points"].append(
                {
                    "time": "2026-03-30T23:40:00Z",
                    "value": None,
                    "value_state": "source_unavailable",
                    "missing_reason": "source_unavailable",
                    "formula_inputs": None,
                }
            )
            record["source_observed_sample_count"] = 2
            record["metric_observed_sample_count"] = 1
            record["subject_active_sample_count"] = 1
            record["coverage"] = {
                "source_coverage_ratio": 0.5,
                "metric_coverage_ratio": 0.25,
                "subject_activity_density": 0.5,
                "source_gap_sample_count": 2,
                "processing_gap_sample_count": 1,
                "classification_complete": True,
            }
            record["source_refs"] = [
                {
                    "source_layer": "raw_observation",
                    "ref_id": "manifest:{}".format(
                        d3["manifest_fingerprint_sha256"]
                    ),
                    "locator": "artifact-manifest/update-slots",
                    "sha256": sha(root / "d3/p0-artifact-manifest.json"),
                }
            ]
        else:
            record["points"] = [
                {
                    "time": "2026-03-30T23:{:02d}:00Z".format(25 + 5 * index),
                    "value": 1,
                    "value_state": "observed_nonzero",
                    "missing_reason": None,
                    "formula_inputs": None,
                }
                for index in range(4)
            ]
            record["source_observed_sample_count"] = 4
            record["metric_observed_sample_count"] = 4
            record["subject_active_sample_count"] = 4
            record["coverage"] = {
                "source_coverage_ratio": 1.0,
                "metric_coverage_ratio": 1.0,
                "subject_activity_density": 1.0,
                "source_gap_sample_count": 0,
                "processing_gap_sample_count": 0,
                "classification_complete": True,
            }
            record["source_refs"] = [
                {
                    "source_layer": "detection_fact",
                    "ref_id": "normalized-incidents:{}".format(
                        d2["candidate_fingerprint_sha256"]
                    ),
                    "locator": "incidents.jsonl.gz",
                    "sha256": d2["files"]["incidents.jsonl.gz"]["sha256"],
                }
            ]
        records.append(record)
    metric_inventory = write_gzip_jsonl(
        directory / "metric-series.jsonl.gz", records
    )
    metric_names = [record["metric_name"] for record in records]
    point_count = sum(len(record["points"]) for record in records)
    value_state_counts = {}
    missing_reason_counts = {}
    legacy_unknown_by_metric = {}
    for record in records:
        legacy_count = 0
        for point in record["points"]:
            state = point["value_state"]
            value_state_counts[state] = value_state_counts.get(state, 0) + 1
            reason = point["missing_reason"]
            if reason is not None:
                missing_reason_counts[reason] = missing_reason_counts.get(reason, 0) + 1
            legacy_count += state == "legacy_unknown"
        if legacy_count:
            legacy_unknown_by_metric[record["metric_name"]] = legacy_count
    schema_sha = sha(PROJECT_ROOT / "contracts/data/metric-series.schema.json")
    reconciliation = {
        "schema_version": "metric_reconciliation_v1",
        "series_count": len(records),
        "point_count": point_count,
        "admitted_metric_count": len(records),
        "formula_contract_coverage_ratio": 1,
        "reconciliation_difference_count": 0,
        "unclassified_gap_count": 0,
        "unknown_missing_reason_count": 0,
        "confirmed_missing_zero_fill_count": 0,
        "outside_window_point_count": 0,
        "legacy_unknown_point_count": 0,
        "legacy_unknown_point_count_by_metric": legacy_unknown_by_metric,
        "value_state_counts": dict(sorted(value_state_counts.items())),
        "missing_reason_counts": dict(sorted(missing_reason_counts.items())),
        "duplicate_metric_name_count": 0,
        "series_fingerprint_sha256": canonical_sha(
            {"schema": "metric_series_set_fingerprint_v1", "series": records}
        ),
        "source_reconciliation_scope": "independent_readonly_feature_rows_and_sqlite_interval_projection_v1",
        "source_reconciliation_expected_metric_count": len(records),
        "source_reconciliation_expected_point_count": point_count,
        "source_reconciliation_actual_point_count": point_count,
        "source_reconciliation_invalid_series_count": 0,
        "source_reconciliation_difference_count": 0,
        "source_reconciliation_difference_count_by_metric": {
            name: {} for name in metric_names
        },
        "source_reconciliation_difference_count_by_type": {},
        "source_reconciliation_failure_sample_limit": 20,
        "source_reconciliation_failure_sample_truncated_count": 0,
        "source_reconciliation_failure_samples": [],
        "internal_structural_difference_count": 0,
        "internal_roundtrip_difference_count": 0,
        "reconciliation_difference_count_by_metric": {
            name: {} for name in metric_names
        },
        "reconciliation_difference_count_by_type": {},
        "reconciliation_failure_sample_limit": 20,
        "reconciliation_failure_sample_truncated_count": 0,
        "reconciliation_failure_samples": [],
        "deterministic_summary_match": True,
        "internal_rebuild": {
            "method": "in_memory_vs_emitted_gzip_reparse_v1",
            "first_summary_sha256": "1" * 64,
            "second_summary_sha256": "1" * 64,
        },
        "strict_schema_status": "passed",
        "schema_invalid_count": 0,
        "schema_validated_series_count": len(records),
        "schema_sha256": schema_sha,
        "validator": "ajv_2020_strict_streaming_jsonl_v1",
        "validator_module_sha256": "2" * 64,
        "deterministic_summary_scope": "internal_memory_vs_emitted_roundtrip_only",
        "cross_run_reproducibility_claimed": False,
        "cross_run_reproducibility_requirement": "external_p0_reproducibility_summary_a_b_required",
    }
    reconciliation["summary_fingerprint_sha256"] = canonical_sha(
        {
            "schema": "metric_reconciliation_summary_fingerprint_v1",
            "summary": reconciliation,
        }
    )
    write_json(directory / "metric-reconciliation-summary.json", reconciliation)
    reconciliation_inventory = inventory(
        directory / "metric-reconciliation-summary.json"
    )
    d3_manifest_name = "p0-artifact-manifest.json"
    d3_summary_name = "p0-artifact-manifest.summary.zh.json"
    sources = {
        "database": {"release_id": RELEASE_ID},
        "d2_normalization": {
            "fingerprint_sha256": d2["candidate_fingerprint_sha256"],
            "manifest_sha256": sha(root / "d2/manifest.json"),
            "checksums_sha256": sha(root / "d2/SHA256SUMS"),
        },
        "d3_artifacts": {
            "fingerprint_sha256": d3["manifest_fingerprint_sha256"],
            "manifest_sha256": sha(root / "d3" / d3_manifest_name),
            "summary_sha256": sha(root / "d3" / d3_summary_name),
            "checksums_sha256": sha(root / "d3/SHA256SUMS"),
        },
        "contracts": {
            "contracts/data/metric-series.schema.json": schema_sha,
        },
    }
    summary = {
        "expected_metric_count": len(p0_data_service.METRIC_DEFINITIONS),
        "generated_metric_names": metric_names,
        "generated_metric_count": len(records),
        "missing_metric_names": [],
        "feature_source_available_slot_count": 2,
        "feature_invalid_source_slot_count": 1,
    }
    manifest = {
        "schema_version": "p0_metric_candidate_v1",
        "candidate_kind": "readonly_global_metric_series",
        "candidate_fingerprint_sha256": "0" * 64,
        "data_profile": profile(),
        "metric_window_utc": {
            "start": "2026-03-30T23:25:00Z",
            "end_exclusive": "2026-03-30T23:45:00Z",
        },
        "generated_at": "2026-07-20T12:30:00Z",
        "source_slot_policies": {"missing_values_coerced_to_zero": False},
        "sources": sources,
        "provenance": {"data_profile_sha256": PROFILE_SHA},
        "files": {
            "metric-series.jsonl.gz": metric_inventory,
            "metric-reconciliation-summary.json": reconciliation_inventory,
        },
        "summary": summary,
        "sample": {"enabled": False, "admissible": True},
        "admission": {"status": "metric_candidate_ready", "eligible_for_release_gate": True, "blocking_reasons": []},
        "classification": "observation_only",
        "causal_conclusion": None,
    }
    manifest["candidate_fingerprint_sha256"] = canonical_sha(
        {
            "schema_version": manifest["schema_version"],
            "data_profile": manifest["data_profile"],
            "metric_window_utc": manifest["metric_window_utc"],
            "generated_at": manifest["generated_at"],
            "sources": manifest["sources"],
            "files": manifest["files"],
            "summary": summary,
            "sample": manifest["sample"],
            "classification": "observation_only",
            "causal_conclusion": None,
        }
    )
    write_json(directory / "manifest.json", manifest)
    (directory / "摘要.md").write_text("# Metric 候选\n", encoding="utf-8")
    close_component(directory)
    return manifest


def create_quality(root, d2, d3, metric):
    directory = root / "quality"
    directory.mkdir()
    report = json.loads(
        (PROJECT_ROOT / "contracts/data/fixtures/data-quality-report/valid-legacy-compatible.json").read_text(encoding="utf-8")
    )
    report["data_profile"] = {
        "profile_id": profile()["id"],
        "profile_sha256": PROFILE_SHA,
        "window": {
            "start": "2026-03-30T23:25:00Z",
            "end": "2026-03-30T23:45:00Z",
            "boundary": "[start,end)",
            "timezone": "Asia/Shanghai",
        },
        "snapshot_time": "2026-03-30T23:44:59Z",
    }
    report["source_release"]["release_id"] = RELEASE_ID
    report["source_release"]["data_artifact_sha256"] = sha(
        root / "d3/p0-artifact-manifest.json"
    )
    report["gate"] = {
        "status": "passed",
        "admission_level": "legacy_compatible",
        "blocking_failed_check_ids": [],
        "blocking_pending_check_ids": [],
        "warning_check_ids": ["raw-full-window"],
        "decision_reasons_zh": [
            "业务事实满足历史兼容准入；原始证据或全量语义复现未达到全窗口通过。",
            "原始覆盖、追溯或复现范围警告仍需保留，不能提升数据身份。",
        ],
    }
    payload = dict(report)
    payload.pop("report_fingerprint_sha256", None)
    report["report_fingerprint_sha256"] = canonical_sha(
        {"schema": "data_quality_report_fingerprint_v1", "report": payload}
    )
    write_json(directory / "data-quality-report.json", report)
    write_gzip_jsonl(directory / "失败明细.jsonl.gz", [])
    (directory / "中文摘要.md").write_text("# 质量报告\n", encoding="utf-8")

    audited_d2 = copy.deepcopy(d2)
    audited_d2["quality_failure_samples"] = {}
    write_json(directory / "d2-candidate-manifest.json", audited_d2)
    write_json(directory / "d2-original-candidate-manifest.json", d2)
    write_json(directory / "d3-artifact-manifest.json", d3)
    d3_verification = json.loads(
        (root / "d3/p0-artifact-manifest.summary.zh.json").read_text(
            encoding="utf-8"
        )
    )
    write_json(
        directory / "d3-artifact-verification-summary.json", d3_verification
    )
    write_json(
        directory / "metric-reconciliation-summary.json",
        json.loads(
            (root / "metric/metric-reconciliation-summary.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    write_json(
        directory / "route-event-reconciliation-summary.json",
        {
            "schema_version": "route_event_index_summary_v1",
            "pilot_only": True,
            "production_complete": False,
            "route_event_count": 1,
            "lineage_status": "raw_traceable",
        },
    )
    write_json(
        directory / "reproducibility-summary.json",
        {
            "schema_version": "p0_reproducibility_summary_v1",
            "status": "passed",
        },
    )
    write_json(
        directory / "quality-gate-execution-context.json",
        {
            "schema_version": "p0_quality_gate_execution_v1",
            "database_access": "none",
            "database_connection_attempts": 0,
            "database_write_operations": 0,
        },
    )
    (directory / "data-profile.json").write_bytes(
        (PROJECT_ROOT / "config/data-profile.json").read_bytes()
    )
    source_inputs = {
        "d2_original_manifest_sha256": sha(
            directory / "d2-original-candidate-manifest.json"
        ),
        "d2_audited_manifest_sha256": sha(
            directory / "d2-candidate-manifest.json"
        ),
        "d2": sha(directory / "d2-candidate-manifest.json"),
        "d2_original": sha(directory / "d2-original-candidate-manifest.json"),
        "d2_audited": sha(directory / "d2-candidate-manifest.json"),
        "d3": sha(directory / "d3-artifact-manifest.json"),
        "route": sha(directory / "route-event-reconciliation-summary.json"),
        "metric": sha(directory / "metric-reconciliation-summary.json"),
        "repro": sha(directory / "reproducibility-summary.json"),
        "execution": sha(directory / "quality-gate-execution-context.json"),
        "d3_verification": sha(
            directory / "d3-artifact-verification-summary.json"
        ),
        "profile": sha(directory / "data-profile.json"),
    }
    write_json(
        directory / "输入闭包.json",
        {
            "schema_version": "p0_quality_gate_input_closure_v1",
            "profile_id": profile()["id"],
            "database_access": "none",
            "database_connection_attempts": 0,
            "database_write_operations": 0,
            "source_inputs": source_inputs,
            "programs": {
                "dev/data_quality/p0_quality_gate.py": "1" * 64,
                "backend/data_pipeline/quality/gate.py": "2" * 64,
                "contracts/data/data-quality-report.schema.json": "3" * 64,
            },
            "report_fingerprint_sha256": report["report_fingerprint_sha256"],
        },
    )
    close_component(directory)


def create_release(root):
    root.mkdir()
    d2 = create_d2(root)
    d3 = create_d3(root)
    metric = create_metric(root, d2, d3)
    create_quality(root, d2, d3, metric)
    return root


def add_retained_legacy_d4(root):
    directory = root / "d4"
    directory.mkdir()
    reconciliation_path = directory / "evidence-reconciliation-summary.json"
    write_json(
        reconciliation_path,
        {
            "schema_version": "evidence_reconciliation_v1",
            "summary_fingerprint_sha256": "4" * 64,
        },
    )
    manifest = {
        "schema_version": "p0_evidence_candidate_v1",
        "candidate_kind": "six_event_contract_investigation_sample",
        "classification": "observation_only",
        "causal_conclusion": None,
        "admission": {
            "status": "sample_only_not_full_population",
            "eligible_for_release_gate": False,
            "raw_traceable": False,
            "represents_full_evidence_population": False,
            "blocking_reasons": ["six_event_sample_not_full_evidence_population"],
        },
        "files": {
            reconciliation_path.name: inventory(reconciliation_path),
        },
    }
    write_json(directory / "manifest.json", manifest)
    (directory / "摘要.md").write_text("# 保留 D4 样本\n", encoding="utf-8")
    close_component(directory)

    quality = root / "quality"
    archived_reconciliation = quality / reconciliation_path.name
    archived_reconciliation.write_bytes(reconciliation_path.read_bytes())
    closure_path = quality / "输入闭包.json"
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    closure["source_inputs"]["evidence"] = sha(archived_reconciliation)
    write_json(closure_path, closure)
    close_component(quality)
    return directory


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    p0_data_service.reset_p0_data_cache()
    monkeypatch.delenv("P0_DATA_RELEASE_DIR", raising=False)
    monkeypatch.delenv("P0_DATA_PRODUCTION_ACTIVE", raising=False)
    yield
    p0_data_service.reset_p0_data_cache()


@pytest.fixture()
def release(tmp_path, monkeypatch):
    path = create_release(tmp_path / "release")
    monkeypatch.setenv("P0_DATA_RELEASE_DIR", str(path))
    return path


def test_normal_candidate_exposes_honest_status_metric_and_quality(client, release):
    status_response = client.get("/api/v1/p0/status")
    assert status_response.status_code == 200
    status = status_response.get_json()
    assert status["repository_state"] == "candidate"
    assert status["production_active"] is False
    assert any(
        item["code"] == "candidate_not_production_active"
        for item in status["limitations"]
    )
    assert status["raw_coverage"]["status"] == "partial"
    assert status["raw_coverage"]["coverage_ratio"] == 0.5
    assert status["raw_coverage"]["artifact_type"] == "update"
    assert status["raw_coverage"]["expected_count"] == 4
    assert status["raw_coverage"]["observed_count"] == 2
    assert status["raw_coverage"]["present_count"] == 3
    assert status["raw_coverage"]["collector_scope"] == ["rrc25"]
    assert status["raw_coverage"]["missing_count"] == 2
    assert status["raw_coverage"]["presence_ratio"] == 0.75
    assert status["raw_coverage"]["missing_value_state"] == "mixed"
    assert status["raw_coverage"]["missing_state_counts"] == {
        "source_unavailable": 1,
        "parse_failed": 1,
    }
    assert status["raw_coverage"]["invalid_reason_counts"] == {
        "compressed_stream_invalid": 0,
        "compression_magic_mismatch": 0,
        "empty_file": 1,
    }
    assert [item["metric_name"] for item in status["available_metrics"]] == sorted(
        p0_data_service.METRIC_DEFINITIONS
    )

    metric_response = client.get("/api/v1/p0/metrics/bgp_announce_record_count")
    assert metric_response.status_code == 200
    metric = metric_response.get_json()["metric"]
    assert metric["unit"] == "bgp_update_record"
    assert metric["formula_version"] == "announce_count_v1"
    assert metric["points"][1]["value"] is None
    assert metric["points"][1]["value_state"] == "processing_gap"
    assert metric["points"][1]["missing_reason"] == "processing_gap"

    incident_metric_response = client.get(
        "/api/v1/p0/metrics/anomaly_incident_count"
    )
    assert incident_metric_response.status_code == 200
    incident_points = incident_metric_response.get_json()["metric"]["points"]
    assert all(point["value_state"] == "observed_nonzero" for point in incident_points)

    quality_response = client.get("/api/v1/p0/quality")
    assert quality_response.status_code == 200
    quality = quality_response.get_json()
    assert quality["report"]["gate"]["admission_level"] == "legacy_compatible"
    assert quality["production_active"] is False


def test_explicit_production_activation_is_consistent_across_p0_endpoints(
    client, release, monkeypatch
):
    monkeypatch.setenv("P0_DATA_PRODUCTION_ACTIVE", "true")

    responses = [
        client.get("/api/v1/p0/status"),
        client.get("/api/v1/p0/metrics/bgp_announce_record_count"),
        client.get("/api/v1/p0/quality"),
    ]

    for response in responses:
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["repository_state"] == "production"
        assert payload["production_active"] is True
        assert not any(
            item["code"] == "candidate_not_production_active"
            for item in payload["limitations"]
        )


def test_invalid_production_activation_flag_fails_closed(client, release, monkeypatch):
    monkeypatch.setenv("P0_DATA_PRODUCTION_ACTIVE", "yes")

    response = client.get("/api/v1/p0/status")

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["error"]["code"] == "candidate_repository_unavailable"
    assert "P0_DATA_PRODUCTION_ACTIVE" in payload["error"]["message_zh"]


def test_unconfigured_repository_returns_explicit_503(client):
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 503
    payload = response.get_json()
    assert payload["error"]["code"] == "candidate_repository_unavailable"
    assert "P0_DATA_RELEASE_DIR" in payload["error"]["message_zh"]


def test_candidate_repository_rejects_unapproved_top_level_entry(client, release):
    (release / "README.txt").write_text("not part of the frozen layout\n", encoding="utf-8")

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    payload = response.get_json()
    assert payload["error"]["code"] == "candidate_artifact_conflict"
    assert "未准入顶层条目" in payload["error"]["message_zh"]
    assert "README.txt" in payload["error"]["message_zh"]


def test_retained_legacy_d4_requires_quality_hash_binding(client, release):
    directory = add_retained_legacy_d4(release)
    p0_data_service.reset_p0_data_cache()
    assert client.get("/api/v1/p0/status").status_code == 200

    reconciliation_path = directory / "evidence-reconciliation-summary.json"
    write_json(
        reconciliation_path,
        {
            "schema_version": "evidence_reconciliation_v1",
            "summary_fingerprint_sha256": "5" * 64,
        },
    )
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][reconciliation_path.name] = inventory(reconciliation_path)
    write_json(manifest_path, manifest)
    close_component(directory)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "Quality 输入闭包" in response.get_json()["error"]["message_zh"]


def test_tampered_component_is_409_and_never_empty_data(client, release):
    assert client.get("/api/v1/p0/status").status_code == 200
    metric_file = release / "metric/metric-series.jsonl.gz"
    with metric_file.open("ab") as stream:
        stream.write(b"tampered")
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "candidate_artifact_conflict"


def test_d3_update_invalid_slot_must_be_unique(client, release):
    directory = release / "d3"
    manifest_path = directory / "p0-artifact-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["invalid_in_window"].append(copy.deepcopy(manifest["invalid_in_window"][0]))
    rewrite_d3_manifest(directory, manifest)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "重复槽" in response.get_json()["error"]["message_zh"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("value_state", "source_unavailable", "缺失状态或原因非法"),
        ("missing_reason", "unknown_reason", "缺失状态或原因非法"),
        ("artifact_type", "rib", "制品类型与文件族不一致"),
    ),
)
def test_d3_update_invalid_slot_contract_is_strict(
    client, release, field, value, message
):
    directory = release / "d3"
    manifest_path = directory / "p0-artifact-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["invalid_in_window"][0][field] = value
    rewrite_d3_manifest(directory, manifest)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert message in response.get_json()["error"]["message_zh"]


def test_d3_update_invalid_slot_must_belong_to_missing_range(client, release):
    directory = release / "d3"
    manifest_path = directory / "p0-artifact-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["invalid_in_window"][0]["artifact_time_utc"] = "2026-01-31T16:25:00Z"
    rewrite_d3_manifest(directory, manifest)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "文件名、目录与槽时间不一致" in response.get_json()["error"]["message_zh"]


@pytest.mark.parametrize("artifact_type", ("update", "rib"))
def test_d3_expected_slots_are_recomputed_from_profile_window_and_interval(
    client, release, artifact_type
):
    directory = release / "d3"
    manifest_path = directory / "p0-artifact-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    typed = manifest["coverage"]["by_collector"][0]["by_artifact_type"][
        artifact_type
    ]
    typed["expected_slots"] += 1
    rewrite_d3_manifest(directory, manifest)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "expected_slots 未按固定半开窗口和间隔复算" in response.get_json()[
        "error"
    ]["message_zh"]


def test_d3_artifact_and_invalid_slot_must_be_mutually_exclusive(client, release):
    directory = release / "d3"
    manifest_path = directory / "p0-artifact-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["artifact_time_utc"] = "2026-03-30T23:35:00Z"
    manifest["artifacts"][0][
        "relative_path"
    ] = "rrc25/2026.03/updates.20260330.2335.gz"
    rewrite_d3_manifest(directory, manifest)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "不互斥" in response.get_json()["error"]["message_zh"]


def test_d3_missing_ranges_must_exhaust_every_non_artifact_slot(client, release):
    directory = release / "d3"
    manifest_path = directory / "p0-artifact-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["coverage"]["by_collector"][0]["by_artifact_type"]["update"][
        "missing_ranges"
    ].pop()
    rewrite_d3_manifest(directory, manifest)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "missing_ranges 与 missing_slots 不闭合" in response.get_json()[
        "error"
    ]["message_zh"]


def test_d3_artifact_path_family_and_time_must_match(client, release):
    directory = release / "d3"
    manifest_path = directory / "p0-artifact-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0][
        "relative_path"
    ] = "rrc25/2026.03/updates.20260330.2330.gz"
    rewrite_d3_manifest(directory, manifest)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "文件名、目录与槽时间不一致" in response.get_json()["error"][
        "message_zh"
    ]


def test_d2_candidate_fingerprint_is_recomputed(client, release):
    directory = release / "d2"
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["candidate_fingerprint_sha256"] = "1" * 64
    write_json(manifest_path, manifest)
    close_component(directory)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert (
        "D2 candidate fingerprint 不一致"
        in response.get_json()["error"]["message_zh"]
    )


def test_metric_points_must_uniquely_exhaust_fixed_five_minute_window(
    client, release
):
    directory = release / "metric"
    records = read_gzip_jsonl(directory / "metric-series.jsonl.gz")
    record = next(
        row for row in records if row["metric_name"] == "bgp_announce_record_count"
    )
    record["points"][1]["time"] = record["points"][0]["time"]
    rewrite_metric_series(directory, records)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "固定五分钟窗口唯一穷尽" in response.get_json()["error"]["message_zh"]


def test_metric_record_summary_is_recomputed_from_point_states(client, release):
    directory = release / "metric"
    records = read_gzip_jsonl(directory / "metric-series.jsonl.gz")
    record = next(
        row for row in records if row["metric_name"] == "bgp_announce_record_count"
    )
    record["source_observed_sample_count"] += 1
    rewrite_metric_series(directory, records)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "逐点 value_state 闭合" in response.get_json()["error"]["message_zh"]


def test_feature_metric_parse_failed_slot_must_exactly_match_d3(client, release):
    directory = release / "metric"
    records = read_gzip_jsonl(directory / "metric-series.jsonl.gz")
    record = next(
        row for row in records if row["metric_name"] == "bgp_announce_record_count"
    )
    record["points"][2]["value_state"] = "source_unavailable"
    record["points"][2]["missing_reason"] = "source_unavailable"
    rewrite_metric_series(directory, records)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "与 D3 UPDATE 缺槽状态逐槽不一致" in response.get_json()["error"][
        "message_zh"
    ]


def test_feature_metric_processing_gap_is_allowed_only_when_d3_is_available(
    client, release
):
    directory = release / "metric"
    records = read_gzip_jsonl(directory / "metric-series.jsonl.gz")
    record = next(
        row for row in records if row["metric_name"] == "bgp_announce_record_count"
    )
    record["points"][3]["value_state"] = "processing_gap"
    record["points"][3]["missing_reason"] = "processing_gap"
    record["source_observed_sample_count"] = 3
    record["coverage"].update(
        {
            "source_coverage_ratio": 0.75,
            "subject_activity_density": round(1 / 3, 10),
            "source_gap_sample_count": 1,
            "processing_gap_sample_count": 2,
        }
    )
    rewrite_metric_series(directory, records)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "与 D3 UPDATE 缺槽状态逐槽不一致" in response.get_json()["error"][
        "message_zh"
    ]


def test_metric_manifest_declared_metric_set_and_count_must_close(client, release):
    directory = release / "metric"
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["summary"]["generated_metric_names"].pop()
    manifest["summary"]["generated_metric_count"] -= 1
    manifest["candidate_fingerprint_sha256"] = p0_data_service._metric_fingerprint(
        manifest
    )
    write_json(manifest_path, manifest)
    close_component(directory)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "指标集合不一致" in response.get_json()["error"]["message_zh"]


def test_metric_reconciliation_state_summary_is_recomputed(client, release):
    directory = release / "metric"
    reconciliation_path = directory / "metric-reconciliation-summary.json"
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    reconciliation["value_state_counts"]["observed_nonzero"] += 1
    rewrite_metric_reconciliation(directory, reconciliation)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "summary 未由逐点状态闭合" in response.get_json()["error"]["message_zh"]


def test_metric_gzip_valid_magic_with_broken_deflate_is_stable_409(client, release):
    directory = release / "metric"
    series_path = directory / "metric-series.jsonl.gz"
    series_path.write_bytes(
        b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff\x07" + b"\x00" * 8
    )
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][series_path.name] = inventory(series_path)
    manifest["candidate_fingerprint_sha256"] = p0_data_service._metric_fingerprint(
        manifest
    )
    write_json(manifest_path, manifest)
    close_component(directory)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    payload = response.get_json()
    assert payload["error"]["code"] == "candidate_artifact_conflict"
    assert "gzip 解压失败" in payload["error"]["message_zh"]


def test_non_admitted_metric_candidate_is_404(client, release):
    directory = release / "metric"
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["admission"] = {
        "status": "not_admitted",
        "eligible_for_release_gate": False,
        "blocking_reasons": ["fixture_not_admitted"],
    }
    manifest["candidate_fingerprint_sha256"] = p0_data_service._metric_fingerprint(
        manifest
    )
    write_json(manifest_path, manifest)
    close_component(directory)
    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/metrics/bgp_withdraw_record_count")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "candidate_resource_not_found"


def test_path_traversal_identifiers_are_rejected_without_file_access(client, release):
    metric = client.get("/api/v1/p0/metrics/%2E%2E")
    assert metric.status_code == 400
    assert metric.get_json()["error"]["code"] == "invalid_identifier"


def test_metric_reconciliation_difference_is_rejected_after_full_rehash(client, release):
    directory = release / "metric"
    reconciliation_path = directory / "metric-reconciliation-summary.json"
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    metric_name = sorted(p0_data_service.METRIC_DEFINITIONS)[0]
    reconciliation["source_reconciliation_difference_count"] = 1
    reconciliation["reconciliation_difference_count"] = 1
    reconciliation["source_reconciliation_difference_count_by_metric"][metric_name] = {
        "value": 1
    }
    reconciliation["reconciliation_difference_count_by_metric"][metric_name] = {
        "value": 1
    }
    reconciliation["source_reconciliation_difference_count_by_type"] = {"value": 1}
    reconciliation["reconciliation_difference_count_by_type"] = {"value": 1}
    fingerprint_payload = dict(reconciliation)
    fingerprint_payload.pop("summary_fingerprint_sha256")
    reconciliation["summary_fingerprint_sha256"] = canonical_sha(
        {
            "schema": "metric_reconciliation_summary_fingerprint_v1",
            "summary": fingerprint_payload,
        }
    )
    write_json(reconciliation_path, reconciliation)

    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][reconciliation_path.name] = inventory(reconciliation_path)
    manifest["candidate_fingerprint_sha256"] = p0_data_service._metric_fingerprint(
        manifest
    )
    write_json(manifest_path, manifest)
    close_component(directory)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "独立源对账" in response.get_json()["error"]["message_zh"]


def test_quality_raw_traceable_claim_rejected_for_partial_sample_candidate(client, release):
    directory = release / "quality"
    report_path = directory / "data-quality-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    raw_check = next(
        check for check in report["checks"] if check["check_id"] == "raw-full-window"
    )
    raw_check["status"] = "pass"
    report["check_summary"] = {
        "total_check_count": len(report["checks"]),
        "passed_check_count": len(report["checks"]),
        "failed_check_count": 0,
        "pending_check_count": 0,
        "blocking_failed_check_count": 0,
        "blocking_pending_check_count": 0,
    }
    report["gate"] = {
        "status": "passed",
        "admission_level": "raw_traceable",
        "blocking_failed_check_ids": [],
        "blocking_pending_check_ids": [],
        "warning_check_ids": [],
        "decision_reasons_zh": ["业务事实与全窗口原始引用均通过逐维度门禁。"],
    }
    payload = dict(report)
    payload.pop("report_fingerprint_sha256")
    report["report_fingerprint_sha256"] = canonical_sha(
        {"schema": "data_quality_report_fingerprint_v1", "report": payload}
    )
    write_json(report_path, report)
    closure_path = directory / "输入闭包.json"
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    closure["report_fingerprint_sha256"] = report["report_fingerprint_sha256"]
    write_json(closure_path, closure)
    close_component(directory)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "不得提升为 raw_traceable" in response.get_json()["error"]["message_zh"]


def test_malformed_quality_report_is_a_stable_artifact_conflict(client, release):
    directory = release / "quality"
    report_path = directory / "data-quality-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["checks"][0].pop("dimension")
    payload = dict(report)
    payload.pop("report_fingerprint_sha256")
    report["report_fingerprint_sha256"] = canonical_sha(
        {"schema": "data_quality_report_fingerprint_v1", "report": payload}
    )
    write_json(report_path, report)

    closure_path = directory / "输入闭包.json"
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    closure["report_fingerprint_sha256"] = report["report_fingerprint_sha256"]
    write_json(closure_path, closure)
    close_component(directory)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "candidate_artifact_conflict"
    assert "质量报告语义复算失败" in response.get_json()["error"]["message_zh"]


def test_quality_input_closure_must_bind_archived_input_file(client, release):
    directory = release / "quality"
    closure_path = directory / "输入闭包.json"
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    closure["source_inputs"]["metric"] = closure["source_inputs"]["route"]
    write_json(closure_path, closure)
    close_component(directory)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "归档输入文件" in response.get_json()["error"]["message_zh"]


@pytest.mark.parametrize(
    ("field", "replacement_field"),
    (
        ("d2", "d2_original"),
        ("d2_audited", "d2_original"),
        ("d2_audited_manifest_sha256", "d2_original_manifest_sha256"),
        ("d2_original", "d2_audited"),
        ("d2_original_manifest_sha256", "d2_audited_manifest_sha256"),
    ),
)
def test_quality_input_closure_must_bind_d2_aliases(
    client, release, field, replacement_field
):
    directory = release / "quality"
    closure_path = directory / "输入闭包.json"
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    closure["source_inputs"][field] = closure["source_inputs"][replacement_field]
    write_json(closure_path, closure)
    close_component(directory)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "归档输入文件" in response.get_json()["error"]["message_zh"]


@pytest.mark.parametrize("mutation", ("missing", "unknown"))
def test_quality_input_closure_requires_exact_source_key_set(
    client, release, mutation
):
    directory = release / "quality"
    closure_path = directory / "输入闭包.json"
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        closure["source_inputs"].pop("d2_original")
    else:
        closure["source_inputs"]["unknown_input"] = "0" * 64
    write_json(closure_path, closure)
    close_component(directory)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "输入闭包身份" in response.get_json()["error"]["message_zh"]


def test_quality_archived_original_d2_must_match_current_component(client, release):
    directory = release / "quality"
    original_path = directory / "d2-original-candidate-manifest.json"
    original = json.loads(original_path.read_text(encoding="utf-8"))
    original["source"]["release_id"] = "different-release"
    write_json(original_path, original)

    closure_path = directory / "输入闭包.json"
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    changed_sha = sha(original_path)
    closure["source_inputs"]["d2_original"] = changed_sha
    closure["source_inputs"]["d2_original_manifest_sha256"] = changed_sha
    write_json(closure_path, closure)
    close_component(directory)

    p0_data_service.reset_p0_data_cache()
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409
    assert "当前 D2 组件不一致" in response.get_json()["error"]["message_zh"]


def test_cache_is_invalidated_even_when_mtime_is_restored(client, release):
    assert client.get("/api/v1/p0/status").status_code == 200
    metric_file = release / "metric/metric-series.jsonl.gz"
    previous = metric_file.stat()
    with metric_file.open("ab") as stream:
        stream.write(b"changed")
    os.utime(metric_file, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    response = client.get("/api/v1/p0/status")
    assert response.status_code == 409


def test_p0_openapi_contract_is_readonly_strict_and_uses_frozen_data_schemas():
    contract = json.loads(
        (PROJECT_ROOT / "contracts/openapi.json").read_text(encoding="utf-8")
    )
    expected_responses = {
        "/p0/status": {"200", "409", "503"},
        "/p0/metrics/{metric_name}": {"200", "400", "404", "409", "503"},
        "/p0/quality": {"200", "409", "503"},
    }
    for path, statuses in expected_responses.items():
        assert set(contract["paths"][path]) == {"get"}
        assert set(contract["paths"][path]["get"]["responses"]) == statuses
    schemas = contract["components"]["schemas"]
    for name in (
        "P0ErrorResponse",
        "P0Limitation",
        "P0Profile",
        "P0Releases",
        "P0QualityDecision",
        "P0AvailableMetric",
        "P0RawCoverage",
        "P0DataStatus",
        "P0MetricResponse",
        "P0QualityResponse",
    ):
        assert schemas[name]["additionalProperties"] is False
    assert schemas["P0MetricResponse"]["properties"]["metric"] == {
        "$ref": "./data/metric-series.schema.json"
    }
    assert schemas["P0QualityResponse"]["properties"]["report"] == {
        "$ref": "./data/data-quality-report.schema.json"
    }
    expected_repository_states = [
        {
            "properties": {
                "repository_state": {"const": "candidate"},
                "production_active": {"const": False},
            }
        },
        {
            "properties": {
                "repository_state": {"const": "production"},
                "production_active": {"const": True},
            }
        },
    ]
    for name in (
        "P0DataStatus",
        "P0MetricResponse",
        "P0QualityResponse",
    ):
        assert schemas[name]["oneOf"] == expected_repository_states
        assert schemas[name]["properties"]["repository_state"]["enum"] == [
            "candidate",
            "production",
        ]
        assert schemas[name]["properties"]["production_active"] == {
            "type": "boolean"
        }
    raw_coverage = schemas["P0RawCoverage"]
    assert "missing_state_counts" in raw_coverage["required"]
    assert "present_count" in raw_coverage["required"]
    assert "presence_ratio" in raw_coverage["required"]
    assert "invalid_reason_counts" in raw_coverage["required"]
    assert raw_coverage["properties"]["missing_value_state"]["enum"] == [
        None,
        "source_unavailable",
        "parse_failed",
        "mixed",
    ]
    missing_states = raw_coverage["properties"]["missing_state_counts"]
    assert missing_states["additionalProperties"] is False
    assert missing_states["required"] == ["source_unavailable", "parse_failed"]
