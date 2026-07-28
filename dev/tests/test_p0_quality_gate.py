import bz2
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from backend.data_pipeline.quality import (
    D2_REQUIRED_QUALITY_FIELDS,
    QualityGateInputError,
    build_quality_report,
    validate_report_semantics,
)
from backend.data_pipeline.route_event import (
    scan_mrt_artifacts,
    verify_artifact_manifest,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts/data/data-quality-report.schema.json"
AJV = ROOT / "frontend/node_modules/@redocly/ajv/dist/2020"
HASH = "a" * 64


def file_inventory(name, rows, seed):
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    content = hashlib.sha256((seed + ":content").encode("utf-8")).hexdigest()
    return {
        "name": name,
        "media_type": "application/x-ndjson+gzip",
        "compression": {
            "algorithm": "gzip",
            "level": 9,
            "mtime": 0,
            "header_filename": "",
        },
        "order": "runner_defined_deterministic_order_v1",
        "row_count": rows,
        "content_sha256": content,
        "sha256": digest,
        "size_bytes": max(rows, 1),
    }


def d2_manifest():
    event_counts = {event_type: 1 for event_type in (
        "hijack",
        "sub_hijack",
        "leak",
        "prefix_outage",
        "as_outage",
        "country_outage",
    )}
    summary = {
        "incident_count": 6,
        "link_count": 6,
        "collision_group_count": 1,
        "collision_incident_count": 2,
        "reverse_orphan_count": 1,
        "explained_reverse_orphan_count": 1,
        "unexplained_reverse_orphan_count": 0,
        "forward_missing_count": 0,
        "forward_ambiguous_count": 0,
        "forward_time_mismatch_count": 0,
        "unexplained_forward_reference_count": 0,
        "ambiguous_locator_group_count": 0,
        "duplicate_event_reference_count": 0,
        "quarantined_duplicate_event_count": 0,
        "malformed_or_mismatched_event_count": 0,
        "quarantine_count": 1,
        "fact_link_status_counts": {"matched": 4, "legacy_collision": 2},
        "event_type_counts": event_counts,
        "quarantine_reason_counts": {
            "invalid_identity": 1,
            "legacy_window_contamination": 1,
        },
    }
    summary.update({field: 0 for field in D2_REQUIRED_QUALITY_FIELDS})
    return {
        "schema_version": "p0_normalization_candidate_v1",
        "candidate_kind": "readonly_legacy_fact_normalization",
        "candidate_fingerprint_sha256": "1" * 64,
        "data_profile": {
            "schema_version": 1,
            "id": "quality-fixture",
            "mode": "fixed",
            "timezone": "Asia/Shanghai",
            "window_start": "2026-02-01T00:00:00+08:00",
            "window_end_exclusive": "2026-02-01T00:10:00+08:00",
            "snapshot_time": "2026-02-01T00:09:59+08:00",
            "api_profile": "core",
        },
        "window_utc": {
            "start": "2026-01-31T16:00:00Z",
            "end_exclusive": "2026-01-31T16:10:00Z",
        },
        "source": {
            "release_id": "quality-fixture-r1",
            "state_sha256": "2" * 64,
            "manifest_sha256": "3" * 64,
            "database_manifest_sha256": "4" * 64,
            "inventory_sha256": "5" * 64,
            "database": {
                "host": "127.0.0.1",
                "port": 31627,
                "name": "bgp_project",
                "system_identifier": "7663836852697006116",
                "transaction_read_only": True,
                "transaction_isolation": "repeatable read",
            },
            "provenance": {"git_sha": "b" * 40},
            "normalizer_hashes": {
                "backend/data_pipeline/normalize/facts.py": "6" * 64,
            },
        },
        "source_table_counts": {},
        "files": {
            "incidents.jsonl.gz": file_inventory("incidents.jsonl.gz", 6, "incidents"),
            "links.jsonl.gz": file_inventory("links.jsonl.gz", 6, "links"),
            "collision_groups.jsonl.gz": file_inventory(
                "collision_groups.jsonl.gz", 1, "collision"
            ),
            "quarantine.jsonl.gz": file_inventory("quarantine.jsonl.gz", 1, "quarantine"),
        },
        "summary": summary,
        "sample": {"enabled": False, "max_events": None, "admissible": True},
        "admission": {
            "status": "legacy_candidate_ready",
            "eligible_for_release_gate": True,
            "blocking_reasons": [],
            "raw_traceable": False,
        },
        "materialization_policy": {
            "large_path_payload_included": False,
            "retained_metadata": ["retained", "empty", "stored_bytes"],
            "nonempty_phase_status": "legacy_unknown",
            "missing_values_coerced_to_zero": False,
        },
        "classification": "observation_only",
        "causal_conclusion": None,
    }


def metric_summary():
    return {
        "schema_version": "metric_reconciliation_v1",
        "series_count": 10,
        "admitted_metric_count": 10,
        "formula_contract_coverage_ratio": 1,
        "strict_schema_status": "passed",
        "schema_invalid_count": 0,
        "schema_validated_series_count": 10,
        "schema_sha256": hashlib.sha256(
            (ROOT / "contracts/data/metric-series.schema.json").read_bytes()
        ).hexdigest(),
        "source_reconciliation_scope": "independent_readonly_feature_rows_and_sqlite_interval_projection_v1",
        "source_reconciliation_expected_point_count": 169920,
        "source_reconciliation_difference_count": 0,
        "source_reconciliation_difference_count_by_metric": {
            metric_name: {}
            for metric_name in (
                "anomaly_incident_count",
                "as_outage_concurrent_count",
                "bgp_announce_record_count",
                "bgp_update_record_count",
                "bgp_withdraw_ratio",
                "bgp_withdraw_record_count",
                "ipv4_24_equivalent_count",
                "ipv4_equivalent_address_count",
                "ipv6_48_equivalent_count",
                "prefix_outage_concurrent_count",
            )
        },
        "source_reconciliation_difference_count_by_type": {},
        "source_reconciliation_failure_samples": [],
        "internal_structural_difference_count": 0,
        "reconciliation_difference_count_by_metric": {
            metric_name: {}
            for metric_name in (
                "anomaly_incident_count",
                "as_outage_concurrent_count",
                "bgp_announce_record_count",
                "bgp_update_record_count",
                "bgp_withdraw_ratio",
                "bgp_withdraw_record_count",
                "ipv4_24_equivalent_count",
                "ipv4_equivalent_address_count",
                "ipv6_48_equivalent_count",
                "prefix_outage_concurrent_count",
            )
        },
        "reconciliation_difference_count_by_type": {},
        "reconciliation_failure_samples": [],
        "reconciliation_difference_count": 0,
        "unclassified_gap_count": 0,
        "unknown_missing_reason_count": 0,
        "confirmed_missing_zero_fill_count": 0,
        "outside_window_point_count": 0,
        "deterministic_summary_match": True,
        "deterministic_summary_scope": "internal_memory_vs_emitted_roundtrip_only",
        "cross_run_reproducibility_claimed": False,
        "cross_run_reproducibility_requirement": "external_p0_reproducibility_summary_a_b_required",
        "summary_fingerprint_sha256": "8" * 64,
    }


def route_summary(manifest):
    return {
        "schema_version": "route_event_index_summary_v1",
        "manifest_fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
        "import_run_id": "run_v1_" + "1" * 32,
        "artifact_count": manifest["summary"]["artifact_count"],
        "raw_record_count": 3,
        "route_event_count": 6,
        "incident_count": 6,
        "incident_route_event_link_count": 6,
        "unsupported_incident_object_count": 0,
        "by_action": {"announce": 4, "withdraw": 2},
        "by_afi_safi": {"ipv4_unicast": 6},
        "lineage_status": "raw_traceable",
        "classification": "observation_only",
        "causal_conclusion": None,
        "parser_capability": "injected_record_stream_only",
        "index_fingerprint_sha256": "9" * 64,
        "raw_reference_unresolved_count": 0,
        "processing_lineage_missing_count": 0,
        "record_hash_verification_failed_count": 0,
        "vp_identity_missing_count": 0,
        "route_event_id_conflict_count": 0,
        "invalid_asn_count": 0,
        "invalid_prefix_count": 0,
        "outside_window_record_count": 0,
    }


def reproducibility_summary(with_route=True):
    matches = {"d2": True, "d3": True, "evidence": True, "metric": True}
    if with_route:
        matches["route_event"] = True
    return {
        "schema_version": "p0_reproducibility_summary_v2",
        "execution_scope": {
            "candidates_regenerated": False,
            "source_database_access": "none",
            "source_database_connection_attempts": 0,
            "source_database_write_operations": 0,
            "raw_mrt_access": "none",
        },
        "byte_identity": {
            "scope": "full_artifact_closure",
            "all_files_rehashed": True,
            "all_corresponding_files_match": True,
            "components": {
                "fixture": {
                    "a_sha256sums_sha256": "1" * 64,
                    "b_sha256sums_sha256": "1" * 64,
                    "a_signed_file_count": 1,
                    "b_signed_file_count": 1,
                    "a_signed_size_bytes": 10,
                    "b_signed_size_bytes": 10,
                    "sha256sums_bytes_match": True,
                    "mismatch_count": 0,
                    "mismatched_files": [],
                }
            },
        },
        "semantic_validation": {
            "mode": "full_population_v1",
            "sample_only": False,
            "population_coverage_claimed": True,
            "d2_sample_comparison": {},
            "stable_id_match_ratio": 1,
            "record_count_metadata_match": True,
            "aggregate_summary_match": True,
            "fingerprint_matches": matches,
            "failure_count": 0,
            "all_results_match": True,
        },
        "full_semantic_validation": {
            "status": "passed",
            "reason": "full_population_semantic_scan_executed",
        },
        "conclusion": {
            "byte_reproducibility_status": "passed",
            "sampled_semantic_status": "passed",
            "full_semantic_reproducibility_status": "passed",
        },
    }


def context():
    return {
        "profile_sha256": "0" * 64,
        "git_sha": "b" * 40,
        "probe_fingerprint_sha256": "c" * 64,
        "data_artifact_sha256": None,
        "database_write_operations": 0,
        "started_at": "2026-07-21T01:00:00Z",
        "finished_at": "2026-07-21T01:00:01Z",
        "generated_at": "2026-07-21T01:00:02Z",
        "input_sha256s": {
            "d2": "d" * 64,
            "d3": "e" * 64,
            "route": "f" * 64,
            "metric": "2" * 64,
            "repro": "3" * 64,
            "execution": "4" * 64,
        },
    }


def single_run_assurance_context_and_summary():
    components = {
        name: {
            "sha256sums_sha256": (str(index + 1) * 64)[:64],
            "signed_file_count": 3,
            "signed_size_bytes": 100 + index,
            "verified": True,
        }
        for index, name in enumerate(("d2", "d3", "metric", "route_event"))
    }
    identity_fields = {
        "d2": (
            "candidate_fingerprint_sha256",
            "manifest_sha256",
            "sha256sums_sha256",
            "incidents_sha256",
        ),
        "d3": (
            "manifest_fingerprint_sha256",
            "manifest_sha256",
            "summary_sha256",
            "sha256sums_sha256",
        ),
        "metric": (
            "candidate_fingerprint_sha256",
            "manifest_sha256",
            "reconciliation_fingerprint_sha256",
            "sha256sums_sha256",
        ),
        "route_event": (
            "index_fingerprint_sha256",
            "parent_d3_manifest_fingerprint_sha256",
            "reconciliation_summary_sha256",
            "sha256sums_sha256",
        ),
    }
    identity = {
        name: {
            field: hashlib.sha256((name + ":" + field).encode("utf-8")).hexdigest()
            for field in fields
        }
        for name, fields in identity_fields.items()
    }
    bindings = {
        "metric_to_final_d2": True,
        "metric_to_final_d3": True,
        "route_event_to_final_d3": True,
        "shared_data_profile": True,
    }

    def side(label):
        checksum = "a" * 64
        return {
            "candidate_fingerprint_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
            "sha256sums_sha256": checksum,
            "incidents_sha256": "d" * 64,
            "record_counts": {
                "incidents.jsonl.gz": 64,
                "links.jsonl.gz": 64,
                "collision_groups.jsonl.gz": 0,
                "quarantine.jsonl.gz": 0,
            },
            "sample": {"enabled": True, "max_events": 64, "admissible": False},
            "closure": {
                "sha256sums_sha256": checksum,
                "signed_file_count": 6,
                "signed_size_bytes": 4096,
                "verified": True,
            },
            "execution_evidence": {
                "evidence_sha256": hashlib.sha256((label + " evidence").encode()).hexdigest(),
                "execution_id": "sample-" + label,
                "started_at": "2026-07-21T01:00:0{}Z".format(0 if label == "a" else 2),
                "finished_at": "2026-07-21T01:00:0{}Z".format(1 if label == "a" else 3),
                "output_dir": "/candidate/sample-" + label,
                "command_argv_sha256": hashlib.sha256((label + " command").encode()).hexdigest(),
                "stdout_sha256": hashlib.sha256((label + " stdout").encode()).hexdigest(),
                "stderr_sha256": hashlib.sha256(b"").hexdigest(),
                "candidate_sha256sums_sha256": checksum,
            },
        }

    summary = {
        "schema_version": "p0_single_run_assurance_v1",
        "assurance_mode": "final_single_candidate_plus_d2_bounded_replay_v1",
        "execution_scope": {
            "candidates_regenerated_in_this_execution": False,
            "source_database_access": "none",
            "source_database_connection_attempts": 0,
            "source_database_write_operations": 0,
            "raw_mrt_access": "none",
        },
        "final_candidate_integrity": {
            "status": "passed",
            "all_sha256_closures_verified": True,
            "components": components,
        },
        "final_candidate_identity": identity,
        "cross_artifact_binding": {"status": "passed", "checks": bindings},
        "bounded_replay": {
            "component": "d2",
            "requested_max_events": 64,
            "final_input_identity_match": True,
            "a": side("a"),
            "b": side("b"),
            "byte_identity": {
                "scope": "full_sample_candidate_closure",
                "all_files_rehashed": True,
                "all_corresponding_files_match": True,
                "sha256sums_bytes_match": True,
                "mismatch_count": 0,
                "mismatched_files": [],
            },
            "semantic_identity": {
                "scope": "full_sample_candidate_population",
                "all_records_streamed": True,
                "stable_id_scope": {"match_ratio": 1},
                "record_counts": {},
                "record_count_metadata_match": True,
                "aggregate_summary_match": True,
                "file_inventory_match": True,
                "fingerprint_match": True,
                "all_results_match": True,
            },
            "generation_independence": {
                "status": "externally_attested",
                "path_distinct": True,
                "directory_inode_distinct": True,
                "all_corresponding_file_inodes_distinct": True,
                "external_execution_evidence_provided": True,
                "cryptographic_independence_proven": False,
                "evidence_boundary": "two_distinct_execution_records_bound_to_candidate_closures_not_cryptographic_proof",
            },
            "status": "passed",
        },
        "cross_run_coverage": {
            "status": "partial",
            "replayed_components": ["d2_bounded_sample"],
            "single_candidate_components": [
                "d2_full",
                "d3",
                "metric",
                "route_event",
            ],
            "population_coverage_claimed": False,
            "full_pipeline_reproducibility_claimed": False,
        },
        "full_semantic_validation": {
            "status": "not_run",
            "reason": "user_requested_bounded_sample",
            "population_coverage_claimed": False,
        },
        "conclusion": {
            "final_artifact_integrity_status": "passed",
            "bounded_d2_replay_status": "passed",
            "cross_artifact_binding_status": "passed",
            "cross_run_coverage_status": "partial",
            "full_semantic_reproducibility_status": "not_run",
        },
        "classification": "observation_only",
        "causal_conclusion": None,
    }
    ctx = context()
    ctx["final_candidate_integrity"] = deepcopy(components)
    ctx["final_candidate_identity"] = deepcopy(identity)
    ctx["cross_artifact_binding"] = deepcopy(bindings)
    return ctx, summary


class RawFixture:
    def __init__(self, partial=False, mixed_missing=False):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "raw"
        directory = self.root / "rrc25" / "2026.01"
        directory.mkdir(parents=True)
        (directory / "updates.20260131.1600.gz").write_bytes(
            gzip.compress(b"update-0", mtime=0)
        )
        if mixed_missing:
            (directory / "updates.20260131.1605.gz").write_bytes(b"")
        elif not partial:
            (directory / "updates.20260131.1605.gz").write_bytes(
                gzip.compress(b"update-1", mtime=0)
            )
        if not mixed_missing:
            (directory / "bview.20260131.1600.bz2").write_bytes(
                bz2.compress(b"rib-0")
            )
        profile = {
            "id": "quality-fixture",
            "timezone": "Asia/Shanghai",
            "window_start": "2026-02-01T00:00:00+08:00",
            "window_end_exclusive": "2026-02-01T00:10:00+08:00",
        }
        self.manifest = scan_mrt_artifacts(self.root, profile, ["rrc25"])
        self.verification = verify_artifact_manifest(self.root, self.manifest)

    def close(self):
        self.temporary.cleanup()


def build_all(raw, *, d2=None, route=None, metric=None, repro=None, ctx=None):
    d2_value = d2 or d2_manifest()
    route_value = route if route is not None else route_summary(raw.manifest)
    return build_quality_report(
        d2_value,
        raw.manifest,
        context=ctx or context(),
        route_event_summary=route_value,
        artifact_verification_summary=raw.verification,
        metric_summary=metric if metric is not None else metric_summary(),
        reproducibility_summary=repro
        if repro is not None
        else reproducibility_summary(with_route=route_value is not None),
    )


def refingerprint_artifact_manifest(manifest):
    payload = deepcopy(manifest)
    payload.pop("manifest_fingerprint_sha256", None)
    encoded = json.dumps(
        {
            "schema": "mrt_artifact_manifest_fingerprint_v1",
            "manifest": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    manifest["manifest_fingerprint_sha256"] = hashlib.sha256(encoded).hexdigest()
    return manifest


def build_with_refingerprinted_manifest(raw, manifest):
    refingerprint_artifact_manifest(manifest)
    verification = deepcopy(raw.verification)
    verification["manifest_fingerprint_sha256"] = manifest[
        "manifest_fingerprint_sha256"
    ]
    return build_quality_report(
        d2_manifest(),
        manifest,
        context=context(),
        route_event_summary=route_summary(manifest),
        artifact_verification_summary=verification,
        metric_summary=metric_summary(),
        reproducibility_summary=reproducibility_summary(),
    )


def assert_schema_valid(testcase, report):
    with tempfile.TemporaryDirectory() as directory:
        report_path = Path(directory) / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        script = r"""
const fs = require('fs')
const Ajv2020 = require(process.argv[1]).default
const schema = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const payload = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'))
const ajv = new Ajv2020({allErrors:true,allowUnionTypes:true,strict:true,validateFormats:true})
ajv.addFormat('date-time', {type:'string', validate:(value) => /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)})
const validate = ajv.compile(schema)
if (!validate(payload)) {
  process.stderr.write(ajv.errorsText(validate.errors, {separator:'; '}))
  process.exit(2)
}
"""
        result = subprocess.run(
            ["node", "-e", script, str(AJV), str(SCHEMA), str(report_path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        testcase.assertEqual(result.returncode, 0, result.stderr)


class QualityGateTest(unittest.TestCase):
    def test_complete_inputs_are_raw_traceable_schema_valid_and_deterministic(self):
        raw = RawFixture()
        try:
            first = build_all(raw)
            second = build_all(raw)
        finally:
            raw.close()
        self.assertEqual(first.report["gate"]["status"], "passed")
        self.assertEqual(first.report["gate"]["admission_level"], "raw_traceable")
        self.assertEqual(first.failure_details_zh, ())
        self.assertNotIn("overall_score", first.report)
        self.assertEqual(first.report_bytes(), second.report_bytes())
        self.assertEqual(first.failure_details_bytes(), second.failure_details_bytes())
        validate_report_semantics(first.report)
        assert_schema_valid(self, first.report)

    def test_partial_raw_gap_passes_only_as_legacy_compatible(self):
        raw = RawFixture(partial=True)
        try:
            result = build_all(raw)
        finally:
            raw.close()
        self.assertEqual(result.report["gate"]["status"], "passed")
        self.assertEqual(result.report["gate"]["admission_level"], "legacy_compatible")
        self.assertIn("raw-full-window-source", result.report["gate"]["warning_check_ids"])
        details = [
            row for row in result.failure_details_zh
            if row["check_id"] == "raw-full-window-source"
        ]
        self.assertEqual(len(details), 1)
        self.assertFalse(details[0]["missing_detail"])
        self.assertEqual(details[0]["reason_codes"], ["source_unavailable"])
        assert_schema_valid(self, result.report)

    def test_parse_failed_gap_must_match_invalid_slot_coordinate_not_only_count(self):
        raw = RawFixture(mixed_missing=True)
        try:
            baseline = build_all(raw)
            self.assertEqual(baseline.report["gate"]["status"], "passed")
            self.assertEqual(raw.manifest["coverage"]["missing_value_state"], "mixed")

            forged = deepcopy(raw.manifest)
            ranges = forged["coverage"]["missing_ranges"]
            update_gap = next(
                row for row in ranges if row["artifact_type"] == "update"
            )
            rib_gap = next(row for row in ranges if row["artifact_type"] == "rib")
            self.assertEqual(update_gap["value_state"], "parse_failed")
            self.assertEqual(rib_gap["value_state"], "source_unavailable")
            update_gap["value_state"] = "source_unavailable"
            rib_gap["value_state"] = "parse_failed"

            result = build_with_refingerprinted_manifest(raw, forged)
        finally:
            raw.close()
        self.assertEqual(result.report["gate"]["status"], "failed")
        self.assertIn(
            "completeness-input-contracts",
            result.report["gate"]["blocking_failed_check_ids"],
        )
        detail = next(
            row
            for row in result.failure_details_zh
            if row["check_id"] == "completeness-input-contracts"
            and "parse_failed_coordinate_mismatch" in row["reason_codes"]
        )
        self.assertFalse(detail["missing_detail"])
        self.assertTrue(
            any(
                "source_unavailable_coordinate_mismatch" in row["reason_codes"]
                for row in result.failure_details_zh
                if row["check_id"] == "completeness-input-contracts"
            )
        )
        assert_schema_valid(self, result.report)

    def test_gap_ranges_reject_overlap_outside_window_and_misalignment(self):
        raw = RawFixture(partial=True)
        try:
            base_range = deepcopy(raw.manifest["coverage"]["missing_ranges"][0])
            cases = []

            duplicate = deepcopy(raw.manifest)
            duplicate["coverage"]["missing_ranges"].append(deepcopy(base_range))
            cases.append(("duplicate", duplicate, "gap_coordinate_overlap", False))

            overlaps_available = deepcopy(raw.manifest)
            overlap_range = overlaps_available["coverage"]["missing_ranges"][0]
            overlap_range["start_time_utc"] = "2026-01-31T16:00:00Z"
            overlap_range["end_time_exclusive_utc"] = "2026-01-31T16:05:00Z"
            cases.append(
                (
                    "available overlap",
                    overlaps_available,
                    "available_gap_coordinate_overlap",
                    True,
                )
            )

            outside = deepcopy(raw.manifest)
            outside_range = outside["coverage"]["missing_ranges"][0]
            outside_range["start_time_utc"] = "2026-01-31T15:55:00Z"
            outside_range["end_time_exclusive_utc"] = "2026-01-31T16:00:00Z"
            cases.append(("outside", outside, "outside_fixed_window", True))

            misaligned = deepcopy(raw.manifest)
            misaligned_range = misaligned["coverage"]["missing_ranges"][0]
            misaligned_range["start_time_utc"] = "2026-01-31T16:06:00Z"
            misaligned_range["end_time_exclusive_utc"] = "2026-01-31T16:11:00Z"
            cases.append(
                ("misaligned", misaligned, "slot_alignment_mismatch", True)
            )

            for label, forged, expected_reason, closure_must_fail in cases:
                with self.subTest(label=label):
                    result = build_with_refingerprinted_manifest(raw, forged)
                    reasons = {
                        reason
                        for row in result.failure_details_zh
                        if row["check_id"] == "completeness-input-contracts"
                        for reason in row["reason_codes"]
                    }
                    self.assertEqual(result.report["gate"]["status"], "failed")
                    self.assertIn(expected_reason, reasons)
                    if closure_must_fail:
                        self.assertIn("slot_coordinate_closure_mismatch", reasons)
                    assert_schema_valid(self, result.report)
        finally:
            raw.close()

    def test_metric_contract_requires_real_schema_validation_evidence(self):
        raw = RawFixture(partial=True)
        metric = metric_summary()
        metric.pop("strict_schema_status")
        try:
            result = build_all(raw, metric=metric)
        finally:
            raw.close()
        self.assertEqual(result.report["gate"]["status"], "failed")
        self.assertIn(
            "completeness-metric-contract",
            result.report["gate"]["blocking_failed_check_ids"],
        )
        assert_schema_valid(self, result.report)

    def test_metric_contract_requires_independent_source_projection(self):
        raw = RawFixture(partial=True)
        metric = metric_summary()
        metric["source_reconciliation_scope"] = "internal_memory_roundtrip"
        try:
            result = build_all(raw, metric=metric)
        finally:
            raw.close()
        self.assertEqual(result.report["gate"]["status"], "failed")
        self.assertIn(
            "completeness-metric-contract",
            result.report["gate"]["blocking_failed_check_ids"],
        )
        assert_schema_valid(self, result.report)

    def test_metric_source_projection_difference_is_blocking(self):
        raw = RawFixture(partial=True)
        metric = metric_summary()
        metric["source_reconciliation_difference_count"] = 1
        metric["reconciliation_difference_count"] = 1
        metric["source_reconciliation_difference_count_by_metric"][
            "bgp_withdraw_ratio"
        ] = {"formula_inputs": 1}
        metric["reconciliation_difference_count_by_metric"][
            "bgp_withdraw_ratio"
        ] = {"formula_inputs": 1}
        metric["source_reconciliation_difference_count_by_type"] = {
            "formula_inputs": 1
        }
        metric["reconciliation_difference_count_by_type"] = {"formula_inputs": 1}
        try:
            result = build_all(raw, metric=metric)
        finally:
            raw.close()
        self.assertEqual(result.report["gate"]["status"], "failed")
        self.assertIn(
            "completeness-metric-contract",
            result.report["gate"]["blocking_failed_check_ids"],
        )
        self.assertIn(
            "reproducibility-metric-reconciliation",
            result.report["gate"]["blocking_failed_check_ids"],
        )
        assert_schema_valid(self, result.report)

    def test_metric_internal_roundtrip_cannot_impersonate_external_a_b_rebuild(self):
        raw = RawFixture(partial=True)
        metric = metric_summary()
        metric["deterministic_summary_scope"] = "cross_run_a_b"
        try:
            result = build_all(raw, metric=metric)
        finally:
            raw.close()
        self.assertEqual(result.report["gate"]["status"], "failed")
        self.assertIn(
            "reproducibility-sampled-semantics",
            result.report["gate"]["blocking_failed_check_ids"],
        )
        assert_schema_valid(self, result.report)

    def test_sampled_reproducibility_is_honest_warning_and_caps_admission(self):
        raw = RawFixture()
        repro = reproducibility_summary()
        repro["semantic_validation"].update(
            {
                "mode": "deterministic_bounded_sample_v1",
                "sample_only": True,
                "population_coverage_claimed": False,
                "d2_sample_comparison": {
                    "incidents.jsonl.gz": {
                        "a_selected_count": 8,
                        "b_selected_count": 8,
                        "a_content_sha256": "2" * 64,
                        "b_content_sha256": "2" * 64,
                        "match": True,
                    }
                },
            }
        )
        repro["full_semantic_validation"] = {
            "status": "not_run",
            "reason": "user_requested_bounded_sample",
        }
        repro["conclusion"]["full_semantic_reproducibility_status"] = "not_run"
        try:
            result = build_all(raw, repro=repro)
        finally:
            raw.close()
        self.assertEqual(result.report["gate"]["status"], "passed")
        self.assertEqual(result.report["gate"]["admission_level"], "legacy_compatible")
        self.assertEqual(result.report["dimensions"]["reproducibility"]["status"], "pending")
        self.assertIn(
            "reproducibility-full-semantic-validation",
            result.report["gate"]["warning_check_ids"],
        )
        statuses = {row["check_id"]: row["status"] for row in result.report["checks"]}
        self.assertEqual(statuses["reproducibility-byte-identity"], "pass")
        self.assertEqual(statuses["reproducibility-sampled-semantics"], "pass")
        self.assertEqual(statuses["reproducibility-scope-integrity"], "pass")
        self.assertEqual(statuses["reproducibility-full-semantic-validation"], "pending")
        assert_schema_valid(self, result.report)

    def test_single_run_assurance_binds_final_inputs_and_caps_admission(self):
        raw = RawFixture()
        ctx, assurance = single_run_assurance_context_and_summary()
        try:
            result = build_all(raw, repro=assurance, ctx=ctx)
        finally:
            raw.close()
        self.assertEqual(result.report["gate"]["status"], "passed")
        self.assertEqual(result.report["gate"]["admission_level"], "legacy_compatible")
        statuses = {row["check_id"]: row["status"] for row in result.report["checks"]}
        for check_id in (
            "reproducibility-final-artifact-integrity",
            "reproducibility-final-identity-binding",
            "reproducibility-cross-artifact-binding",
            "reproducibility-byte-identity",
            "reproducibility-sampled-semantics",
            "reproducibility-scope-integrity",
        ):
            self.assertEqual(statuses[check_id], "pass")
        self.assertEqual(statuses["reproducibility-cross-run-coverage"], "pending")
        self.assertEqual(statuses["reproducibility-full-semantic-validation"], "pending")
        self.assertIn(
            "reproducibility-cross-run-coverage",
            result.report["gate"]["warning_check_ids"],
        )
        assert_schema_valid(self, result.report)

    def test_single_run_assurance_cannot_bind_another_final_candidate(self):
        raw = RawFixture()
        ctx, assurance = single_run_assurance_context_and_summary()
        assurance["final_candidate_identity"]["d2"]["manifest_sha256"] = "0" * 64
        try:
            result = build_all(raw, repro=assurance, ctx=ctx)
        finally:
            raw.close()
        self.assertEqual(result.report["gate"]["admission_level"], "not_accepted")
        self.assertIn(
            "reproducibility-final-identity-binding",
            result.report["gate"]["blocking_failed_check_ids"],
        )
        assert_schema_valid(self, result.report)

    def test_single_run_assurance_rejects_full_coverage_impersonation(self):
        raw = RawFixture()
        ctx, assurance = single_run_assurance_context_and_summary()
        assurance["cross_run_coverage"]["status"] = "full"
        assurance["cross_run_coverage"]["population_coverage_claimed"] = True
        try:
            result = build_all(raw, repro=assurance, ctx=ctx)
        finally:
            raw.close()
        self.assertEqual(result.report["gate"]["admission_level"], "not_accepted")
        self.assertIn(
            "reproducibility-cross-run-coverage",
            result.report["gate"]["blocking_failed_check_ids"],
        )
        self.assertIn(
            "reproducibility-scope-integrity",
            result.report["gate"]["blocking_failed_check_ids"],
        )
        assert_schema_valid(self, result.report)

    def test_current_aggregate_manifest_missing_quality_fields_fails_closed(self):
        raw = RawFixture()
        candidate = d2_manifest()
        for field in D2_REQUIRED_QUALITY_FIELDS:
            candidate["summary"].pop(field)
        try:
            result = build_all(raw, d2=candidate)
        finally:
            raw.close()
        self.assertEqual(result.report["gate"]["status"], "failed")
        self.assertEqual(result.report["gate"]["admission_level"], "not_accepted")
        failed_ids = set(result.report["gate"]["blocking_failed_check_ids"])
        self.assertTrue({
            "completeness-entity-identities",
            "uniqueness-stable-ids",
            "time-end-before-start",
            "phase-six-event-coverage",
            "missing-reason-complete",
            "missing-no-zero-fill",
        }.issubset(failed_ids))
        missing = [row for row in result.failure_details_zh if row["missing_detail"]]
        self.assertTrue(missing)
        for row in missing:
            self.assertTrue(row["source"])
            self.assertTrue(row["table"])
            self.assertTrue(row["key"])
            self.assertTrue(row["field"])
            self.assertRegex(row["rule_id"], r"^P0-")
            self.assertTrue(row["evidence_locator"])
        assert_schema_valid(self, result.report)

    def test_positive_aggregate_orphan_without_rows_is_explicit_missing_detail(self):
        raw = RawFixture()
        candidate = d2_manifest()
        candidate["summary"]["reverse_orphan_count"] = 2
        candidate["summary"]["unexplained_reverse_orphan_count"] = 1
        try:
            result = build_all(raw, d2=candidate)
        finally:
            raw.close()
        detail = next(
            row for row in result.failure_details_zh
            if row["check_id"] == "references-reverse-unexplained"
        )
        self.assertTrue(detail["missing_detail"])
        self.assertIn("orphan_reference", detail["reason_codes"])
        self.assertIn("missing_detail", detail["reason_codes"])
        self.assertEqual(result.report["gate"]["admission_level"], "not_accepted")

    def test_supplied_failure_row_preserves_table_key_field_and_rule(self):
        raw = RawFixture()
        candidate = d2_manifest()
        candidate["summary"]["invalid_asn_count"] = 1
        candidate["quality_failure_samples"] = {
            "completeness-entity-identities": [
                {
                    "source_ref": "incidents.jsonl.gz:42",
                    "table": "normalized_incident",
                    "primary_key": "inc_v1_deadbeef",
                    "field": "affected_objects.asn",
                    "event_time": "2026-01-31T16:01:00Z",
                    "reason_codes": ["invalid_identity"],
                    "evidence_locator": "incidents.jsonl.gz#L42",
                }
            ]
        }
        try:
            result = build_all(raw, d2=candidate)
        finally:
            raw.close()
        detail = next(
            row for row in result.failure_details_zh
            if row["check_id"] == "completeness-entity-identities"
        )
        self.assertEqual(detail["table"], "normalized_incident")
        self.assertEqual(detail["key"], "inc_v1_deadbeef")
        self.assertEqual(detail["field"], "affected_objects.asn")
        self.assertEqual(detail["rule_id"], "P0-IDENTITY-001")
        self.assertFalse(detail["missing_detail"])

    def test_claimed_raw_traceable_with_missing_ref_hash_vp_evidence_blocks(self):
        raw = RawFixture()
        route = route_summary(raw.manifest)
        route.pop("raw_reference_unresolved_count")
        route.pop("record_hash_verification_failed_count")
        route.pop("vp_identity_missing_count")
        try:
            result = build_all(raw, route=route)
        finally:
            raw.close()
        failed = set(result.report["gate"]["blocking_failed_check_ids"])
        self.assertTrue({
            "references-raw-closure",
            "raw-reference-resolvable",
            "raw-hashes-verified",
            "raw-vp-lineage-complete",
        }.issubset(failed))
        self.assertEqual(result.report["gate"]["admission_level"], "not_accepted")
        self.assertTrue(any(row["missing_detail"] for row in result.failure_details_zh))
        assert_schema_valid(self, result.report)

    def test_no_route_index_can_still_be_legacy_but_never_raw(self):
        raw = RawFixture(partial=True)
        try:
            result = build_quality_report(
                d2_manifest(),
                raw.manifest,
                context=context(),
                route_event_summary=None,
                artifact_verification_summary=raw.verification,
                metric_summary=metric_summary(),
                reproducibility_summary=reproducibility_summary(with_route=False),
            )
        finally:
            raw.close()
        self.assertEqual(result.report["gate"]["status"], "passed")
        self.assertEqual(result.report["gate"]["admission_level"], "legacy_compatible")
        self.assertNotIn("raw_traceable", result.report["gate"]["decision_reasons_zh"])

    def test_all_optional_summaries_absent_returns_schema_valid_not_accepted(self):
        raw = RawFixture(partial=True)
        try:
            result = build_quality_report(
                d2_manifest(), raw.manifest, context=context()
            )
        finally:
            raw.close()
        self.assertEqual(result.report["gate"]["status"], "failed")
        self.assertEqual(result.report["gate"]["admission_level"], "not_accepted")
        self.assertIn(
            "completeness-metric-contract",
            result.report["gate"]["blocking_failed_check_ids"],
        )
        self.assertTrue(any(row["missing_detail"] for row in result.failure_details_zh))
        assert_schema_valid(self, result.report)

    def test_tampered_artifact_manifest_is_blocking_not_silently_rehashed(self):
        raw = RawFixture()
        tampered = deepcopy(raw.manifest)
        tampered["coverage"]["available_slots"] -= 1
        try:
            result = build_quality_report(
                d2_manifest(),
                tampered,
                context=context(),
                route_event_summary=route_summary(raw.manifest),
                artifact_verification_summary=raw.verification,
                metric_summary=metric_summary(),
                reproducibility_summary=reproducibility_summary(),
            )
        finally:
            raw.close()
        self.assertIn(
            "completeness-input-contracts",
            result.report["gate"]["blocking_failed_check_ids"],
        )
        self.assertEqual(result.report["gate"]["admission_level"], "not_accepted")
        assert_schema_valid(self, result.report)

    def test_database_write_execution_evidence_is_never_accepted(self):
        raw = RawFixture()
        unsafe = context()
        unsafe["database_write_operations"] = 1
        try:
            with self.assertRaisesRegex(QualityGateInputError, "数据库写操作"):
                build_all(raw, ctx=unsafe)
        finally:
            raw.close()

    def test_semantic_validator_detects_gate_and_fingerprint_tampering(self):
        raw = RawFixture()
        try:
            result = build_all(raw)
        finally:
            raw.close()
        tampered_gate = deepcopy(result.report)
        tampered_gate["gate"]["admission_level"] = "legacy_compatible"
        with self.assertRaisesRegex(QualityGateInputError, "门禁决定"):
            validate_report_semantics(tampered_gate)
        tampered_fingerprint = deepcopy(result.report)
        tampered_fingerprint["report_fingerprint_sha256"] = "0" * 64
        with self.assertRaisesRegex(QualityGateInputError, "fingerprint"):
            validate_report_semantics(tampered_fingerprint)


if __name__ == "__main__":
    unittest.main()
