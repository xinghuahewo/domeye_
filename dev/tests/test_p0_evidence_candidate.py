import gzip
import hashlib
import json
import copy
from pathlib import Path
import tempfile
import unittest

from backend.data_pipeline.normalize import build_collision_group
from dev.data_quality import p0_evidence_candidate as candidate
from backend.data_pipeline import evidence as evidence_module
from backend.data_pipeline.evidence import build_evidence_bundle_v2
from dev.tests.test_p0_evidence_bundle import legacy_kwargs, normalized_incident


ROOT = Path(__file__).resolve().parents[2]
PROFILE_SHA = "a" * 64
INVENTORY_SHA = "b" * 64
STATE_SHA = "c" * 64
MANIFEST_SHA = "d" * 64
DATABASE_MANIFEST_SHA = "e" * 64


def canonical_bytes(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_bytes(canonical_bytes(value))


def write_gzip_jsonl(path, rows):
    content = b"".join(canonical_bytes(row) for row in rows)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as stream:
            stream.write(content)
    return {
        "name": path.name,
        "media_type": "application/x-ndjson+gzip",
        "compression": {
            "algorithm": "gzip",
            "level": 9,
            "mtime": 0,
            "header_filename": "",
        },
        "order": "runner_defined_deterministic_order_v1",
        "row_count": len(rows),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "sha256": sha256(path),
        "size_bytes": path.stat().st_size,
    }


def link_for(incident, status="matched"):
    return {
        "incident_id": incident["incident_id"],
        "detail_reference": incident["detail_reference"],
        "event_type": incident["event_type"],
        "source_table": incident["source_table"],
        "status": status,
        "matched_source_primary_key": (
            incident["source_primary_key"] if status in {"matched", "legacy_collision"} else None
        ),
        "candidate_source_primary_keys": [incident["source_primary_key"]],
        "locator_risks": [],
        "unresolved_reasons": [],
        "collision_group_id": incident["collision_group_id"],
        "classification": "observation_only",
        "causal_conclusion": None,
    }


def create_d2(directory, *, omit_type=None, collision_first=False):
    directory.mkdir()
    incidents = []
    links = []
    collisions = []
    for event_type in candidate.EVENT_TYPES:
        if event_type == omit_type:
            continue
        incident = normalized_incident(event_type)
        if event_type == "hijack" and collision_first:
            collided = json.loads(json.dumps(incident))
            collided["incident_id"] = "inc_v1_" + "a" * 24
            collided["fact_link_status"] = "legacy_collision"
            collided["collision_group_id"] = "lcg_v1_" + "7" * 32
            collided["phase_coverage"] = {
                phase_name: {
                    "source_field": "fixture_phase",
                    "semantics": "route_observation_not_causal_trace",
                    "supports_recovery": False,
                    "status": "source_fact_collision",
                    "missing_reason": "fact_record_reused_by_multiple_incidents",
                    "observations": None,
                }
                for phase_name in ("before", "during", "after")
            }
            incidents.append(collided)
            links.append(link_for(collided, "legacy_collision"))
            collision = build_collision_group(
                source_table=collided["source_table"],
                source_primary_key=collided["source_primary_key"],
                incident_ids=[collided["incident_id"], "inc_v1_" + "f" * 24],
            )
            # D2 测试重点是候选排除，collision JSONL 本身仍需满足边界。
            collision["collision_group_id"] = collided["collision_group_id"]
            collisions.append(collision)
            matched = normalized_incident("hijack", suffix="9" * 24)
            matched["detail_reference"] = matched["detail_reference"].replace("/1/r", "/9/r")
            matched["source_primary_key"] = {
                "source": "r",
                "prefix": "203.0.113.0/24",
                "hijack_eventid": 9,
            }
            incidents.append(matched)
            links.append(link_for(matched))
        else:
            incidents.append(incident)
            links.append(link_for(incident))

    inventories = {}
    rows_by_file = {
        "incidents.jsonl.gz": incidents,
        "links.jsonl.gz": links,
        "collision_groups.jsonl.gz": collisions,
        "quarantine.jsonl.gz": [],
    }
    for name, rows in rows_by_file.items():
        inventories[name] = write_gzip_jsonl(directory / name, rows)

    event_counts = {event_type: 1 for event_type in candidate.EVENT_TYPES}
    summary = {
        "incident_count": len(incidents),
        "link_count": len(links),
        "collision_group_count": len(collisions),
        "collision_incident_count": 1 if collision_first else 0,
        "reverse_orphan_count": 0,
        "explained_reverse_orphan_count": 0,
        "unexplained_reverse_orphan_count": 0,
        "forward_missing_count": 0,
        "forward_ambiguous_count": 0,
        "forward_time_mismatch_count": 0,
        "unexplained_forward_reference_count": 0,
        "ambiguous_locator_group_count": 0,
        "duplicate_event_reference_count": 0,
        "quarantined_duplicate_event_count": 0,
        "malformed_or_mismatched_event_count": 0,
        "fact_link_status_counts": {
            "matched": len(incidents) - (1 if collision_first else 0),
            **({"legacy_collision": 1} if collision_first else {}),
        },
        # omit_type 场景故意让 manifest 声称六类都有，以验证流式选择仍失败关闭。
        "event_type_counts": event_counts,
        "quarantine_reason_counts": {},
        "quarantine_count": 0,
    }
    profile = {
        "schema_version": 1,
        "id": "fixed-feb-mar-2026",
        "mode": "fixed-historical",
        "timezone": "Asia/Shanghai",
        "window_start": "2026-02-01T00:00:00+08:00",
        "window_end_exclusive": "2026-04-01T00:00:00+08:00",
        "snapshot_time": "2026-03-31T23:59:59+08:00",
        "api_profile": "fixed-data-window",
    }
    manifest = {
        "schema_version": "p0_normalization_candidate_v1",
        "candidate_kind": "readonly_legacy_fact_normalization",
        "candidate_fingerprint_sha256": "1" * 64,
        "data_profile": profile,
        "window_utc": {
            "start": "2026-01-31T16:00:00Z",
            "end_exclusive": "2026-03-31T16:00:00Z",
        },
        "source": {
            "release_id": "20260720T000000Z",
            "state_sha256": STATE_SHA,
            "manifest_sha256": MANIFEST_SHA,
            "database_manifest_sha256": DATABASE_MANIFEST_SHA,
            "inventory_sha256": INVENTORY_SHA,
            "database": {
                "host": "127.0.0.1",
                "port": 31627,
                "name": "bgp_project",
                "system_identifier": "fixture-system",
                "transaction_read_only": True,
                "transaction_isolation": "repeatable read",
            },
            "provenance": {"data_profile_sha256": PROFILE_SHA},
            "normalizer_hashes": {
                "backend/data_pipeline/normalize/__init__.py": "2" * 64,
                "backend/data_pipeline/normalize/facts.py": "3" * 64,
            },
        },
        "source_table_counts": {},
        "files": inventories,
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
    write_json(directory / "manifest.json", manifest)
    (directory / "摘要.md").write_text("# D2 fixture\n", encoding="utf-8")
    checksum_names = [*candidate.JSONL_FILES, "manifest.json", "摘要.md"]
    (directory / "SHA256SUMS").write_text(
        "".join("{}  {}\n".format(sha256(directory / name), name) for name in sorted(checksum_names)),
        encoding="utf-8",
    )
    return manifest


def create_d3(directory):
    directory.mkdir()
    manifest_path = directory / "artifact-manifest.json"
    profile = {
        "id": "fixed-feb-mar-2026",
        "timezone": "Asia/Shanghai",
        "window_start": "2026-02-01T00:00:00+08:00",
        "window_end_exclusive": "2026-04-01T00:00:00+08:00",
        "window_start_utc": "2026-01-31T16:00:00Z",
        "window_end_exclusive_utc": "2026-03-31T16:00:00Z",
    }
    update_coverage = {
        "expected_slots": 2,
        "available_slots": 1,
        "missing_slots": 1,
        "coverage_ratio": 0.5,
        "coverage_status": "partial",
        "missing_ranges": [
            {
                "start_time_utc": "2026-01-31T16:00:00Z",
                "end_time_exclusive_utc": "2026-01-31T16:05:00Z",
                "slot_count": 1,
                "value_state": "source_unavailable",
            }
        ],
    }
    rib_coverage = {
        "expected_slots": 1,
        "available_slots": 0,
        "missing_slots": 1,
        "coverage_ratio": 0,
        "coverage_status": "partial",
        "missing_ranges": [
            {
                "start_time_utc": "2026-01-31T16:00:00Z",
                "end_time_exclusive_utc": "2026-02-01T00:00:00Z",
                "slot_count": 1,
                "value_state": "source_unavailable",
            }
        ],
    }
    payload = {
        "schema_version": 1,
        "manifest_kind": "mrt_artifact_manifest",
        "artifact_id_schema": "artifact_id_v1",
        "data_profile": profile,
        "filename_timestamp_timezone": "UTC",
        "collector_allowlist": ["rrc25"],
        "scan_policy": {
            "out_of_window": "exclude_without_hash",
            "compression_envelope_validation": "full_stream_to_eof_crc_or_equivalent",
        },
        "artifacts": [
            {
                "artifact_id": "art_v1_" + "4" * 32,
                "artifact_id_schema": "artifact_id_v1",
                "collector_id": "rrc25",
                "artifact_type": "update",
                "artifact_time_utc": "2026-02-24T00:00:00Z",
                "relative_path": "rrc25/updates.20260224.0000.gz",
                "filename_family": "updates",
                "compression": "gz",
                "size_bytes": 100,
                "file_sha256": "5" * 64,
            }
        ],
        "summary": {
            "artifact_count": 1,
            "size_bytes": 100,
            "by_artifact_type": {
                "rib": {"artifact_count": 0, "size_bytes": 0},
                "update": {"artifact_count": 1, "size_bytes": 100},
            },
            "by_collector": [
                {"collector_id": "rrc25", "artifact_count": 1, "size_bytes": 100}
            ],
            "excluded_out_of_window": {
                "file_count": 0,
                "size_bytes": 0,
                "by_reason": {
                    "before_window": {"file_count": 0, "size_bytes": 0},
                    "at_or_after_window_end": {"file_count": 0, "size_bytes": 0},
                },
                "boundary_samples": [],
            },
        },
        "coverage": {
            "expected_slots": 3,
            "available_slots": 1,
            "missing_slots": 2,
            "coverage_ratio": 0.33333333,
            "coverage_status": "partial",
            "missing_value_state": "source_unavailable",
            "by_collector": [
                {
                    "collector_id": "rrc25",
                    "by_artifact_type": {
                        "rib": rib_coverage,
                        "update": update_coverage,
                    },
                }
            ],
            "missing_ranges": [],
        },
    }
    manifest = {
        **payload,
        "manifest_fingerprint_sha256": candidate._canonical_sha256(
            {"schema": "mrt_artifact_manifest_fingerprint_v1", "manifest": payload}
        ),
    }
    write_json(manifest_path, manifest)
    manifest_sha = sha256(manifest_path)
    summary_path = directory / "artifact-manifest.summary.zh.json"
    summary = {
        "schema_version": 1,
        "summary_kind": "p0_raw_artifact_manifest_summary_zh",
        "provenance": {"data_profile": {"file_name": "data-profile.json", "sha256": PROFILE_SHA}},
        "manifest": {
            "file_name": manifest_path.name,
            "sha256": manifest_sha,
            "fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
        },
        "verification": {
            "verified": True,
            "artifact_count": 1,
            "manifest_fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
        },
    }
    write_json(summary_path, summary)
    (directory / "SHA256SUMS").write_text(
        "{}  {}\n{}  {}\n".format(
            manifest_sha,
            manifest_path.name,
            sha256(summary_path),
            summary_path.name,
        ),
        encoding="utf-8",
    )
    return manifest_path


class EvidenceCandidateCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.d2 = self.root / "d2"
        self.d3 = self.root / "d3"
        create_d2(self.d2)
        self.artifact_manifest = create_d3(self.d3)

    def tearDown(self):
        self.temporary.cleanup()

    def build(self, output, **overrides):
        values = {
            "d2_candidate": self.d2,
            "d3_artifact_manifest": self.artifact_manifest,
            "output_dir": output,
            "pipeline_root": ROOT,
            "schema_path": ROOT / "contracts/data/evidence-bundle-v2.schema.json",
            "ajv_module": ROOT / "frontend/node_modules/@redocly/ajv/dist/2020",
            "generated_at": "2026-07-20T12:00:00Z",
        }
        values.update(overrides)
        return candidate.build_candidate(**values)

    def test_generates_six_schema_valid_legacy_bundles_and_resolvable_registry(self):
        output = self.root / "out"
        manifest = self.build(output)
        self.assertEqual(manifest["validation"]["bundle_count"], 6)
        self.assertEqual(manifest["validation"]["event_type_count"], 6)
        self.assertEqual(manifest["admission"]["status"], "sample_only_not_full_population")
        self.assertFalse(manifest["admission"]["represents_full_evidence_population"])
        self.assertFalse(manifest["admission"]["eligible_for_release_gate"])
        self.assertEqual(manifest["validation"]["strict_schema_status"], "passed")
        self.assertEqual(manifest["validation"]["auto_zero_fill_count"], 0)
        self.assertEqual(manifest["inputs"]["route_event_index"]["status"], "not_provided")
        self.assertEqual(manifest["inputs"]["metric_series"]["status"], "not_provided")
        reconciliation = json.loads(
            (output / candidate.RECONCILIATION_FILE).read_text(encoding="utf-8")
        )
        self.assertEqual(reconciliation["schema_version"], "evidence_reconciliation_v1")
        self.assertEqual(reconciliation["scope"], "six_event_contract_investigation_sample")
        self.assertTrue(reconciliation["sample_only"])
        self.assertFalse(reconciliation["population_coverage_claimed"])
        self.assertEqual(reconciliation["bundle_count"], 6)
        self.assertEqual(reconciliation["event_type_count"], 6)
        for field in (
            "schema_invalid_count",
            "classification_violation_count",
            "causal_conclusion_nonnull_count",
            "evidence_id_conflict_count",
            "unresolved_evidence_reference_count",
            "unresolved_route_event_reference_count",
            "outside_window_record_count",
            "unknown_missing_reason_count",
            "auto_zero_fill_count",
        ):
            self.assertEqual(reconciliation[field], 0, field)
        self.assertIsInstance(reconciliation["legacy_unknown_value_count"], int)
        self.assertEqual(
            manifest["reconciliation"]["summary_fingerprint_sha256"],
            reconciliation["summary_fingerprint_sha256"],
        )
        registry = json.loads((output / "evidence-registry.json").read_text(encoding="utf-8"))
        self.assertEqual(registry["entry_count"], len(registry["entries"]))
        self.assertGreater(registry["entry_count"], 6)

        bundles = {}
        bundle_evidence_ids = set()
        for event_type, selection in manifest["selection"].items():
            bundle = json.loads((output / selection["bundle_file"]).read_text(encoding="utf-8"))
            bundles[bundle["bundle_id"]] = bundle
            bundle_evidence_ids.update(item["evidence_id"] for item in bundle["evidence_registry"])
            self.assertEqual(bundle["incident"]["event_type"], event_type)
            self.assertEqual(bundle["coverage_summary"]["admission_level"], "legacy_compatible")
            self.assertEqual(bundle["route_event_refs"], [])
            self.assertEqual(bundle["raw_record_refs"], [])
            self.assertEqual(bundle["metric_windows"], [])
            self.assertIsNone(bundle["source_fact_mapping"]["source_facts"][0]["record_hash"])
            self.assertEqual(bundle["conclusion"]["classification"], "observation_only")
            self.assertIsNone(bundle["conclusion"]["causal_conclusion"])
        self.assertEqual(set(registry["entries"]), bundle_evidence_ids)
        self.assertEqual(
            bundles[manifest["selection"]["leak"]["bundle_id"]]["source_fact_mapping"]["source_facts"][0]["table_name"],
            "leak_event_202603",
        )
        for evidence_id, entry in registry["entries"].items():
            bundle = bundles[entry["bundle_id"]]
            expected = next(
                item for item in bundle["evidence_registry"] if item["evidence_id"] == evidence_id
            )
            self.assertEqual(entry["registry_item"], expected)
            self.assertEqual(entry["bundle_file"], manifest["selection"][bundle["incident"]["event_type"]]["bundle_file"])

        checksum_lines = (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        for line in checksum_lines:
            digest, name = line.split("  ", 1)
            self.assertEqual(sha256(output / name), digest)

    def test_same_inputs_and_generated_at_produce_byte_identical_directories(self):
        first = self.root / "first"
        second = self.root / "second"
        self.build(first)
        self.build(second)
        first_files = {path.name: path.read_bytes() for path in first.iterdir()}
        second_files = {path.name: path.read_bytes() for path in second.iterdir()}
        self.assertEqual(first_files, second_files)

    def test_tampered_d2_file_fails_before_any_output(self):
        with (self.d2 / "incidents.jsonl.gz").open("ab") as stream:
            stream.write(b"tampered")
        output = self.root / "out"
        with self.assertRaisesRegex(candidate.EvidenceCandidateError, "SHA256"):
            self.build(output)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".out.tmp.*")), [])

    def test_tampered_d3_summary_fails_before_any_output(self):
        summary = self.d3 / "artifact-manifest.summary.zh.json"
        with summary.open("ab") as stream:
            stream.write(b"tampered")
        output = self.root / "out"
        with self.assertRaisesRegex(candidate.EvidenceCandidateError, "SHA256"):
            self.build(output)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".out.tmp.*")), [])

    def test_strict_schema_failure_removes_partially_written_staging(self):
        invalidating_schema = self.root / "invalidating-schema.json"
        write_json(
            invalidating_schema,
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["must_not_exist_in_bundle"],
                "properties": {"must_not_exist_in_bundle": {"const": True}},
            },
        )
        output = self.root / "out"
        with self.assertRaisesRegex(candidate.EvidenceCandidateError, "Schema"):
            self.build(output, schema_path=invalidating_schema)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".out.tmp.*")), [])

    def test_missing_event_type_fails_closed_and_cleans_staging(self):
        missing = self.root / "d2-missing"
        create_d2(missing, omit_type="country_outage")
        output = self.root / "out"
        with self.assertRaisesRegex(candidate.EvidenceCandidateError, "country_outage"):
            self.build(output, d2_candidate=missing)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".out.tmp.*")), [])

    def test_collision_is_excluded_and_next_safe_matched_incident_is_selected(self):
        collided = self.root / "d2-collision"
        create_d2(collided, collision_first=True)
        output = self.root / "out"
        manifest = self.build(output, d2_candidate=collided)
        self.assertEqual(manifest["selection"]["hijack"]["incident_id"], "inc_v1_" + "9" * 24)
        self.assertEqual(manifest["selection"]["hijack"]["fact_link_status"], "matched")

    def test_existing_output_is_never_overwritten(self):
        output = self.root / "out"
        output.mkdir()
        owner = output / "owner.txt"
        owner.write_text("owner", encoding="utf-8")
        with self.assertRaisesRegex(candidate.EvidenceCandidateError, "拒绝覆盖"):
            self.build(output)
        self.assertEqual(owner.read_text(encoding="utf-8"), "owner")

    def test_explicit_legacy_unknown_is_counted_but_not_unknown_missing_reason(self):
        bundle = build_evidence_bundle_v2(
            normalized_incident("hijack"), **legacy_kwargs()
        )
        bundle = copy.deepcopy(bundle)
        bundle["field_quality"][0]["value_state"] = "legacy_unknown"
        bundle["field_quality"][0]["missing_reason"] = "legacy_unknown"
        counts = candidate._bundle_reconciliation_counts(bundle)
        self.assertEqual(counts["unknown_missing_reason_count"], 0)
        self.assertGreaterEqual(counts["legacy_unknown_value_count"], 1)

    def test_missing_reason_and_missing_pointer_zero_are_counted_separately(self):
        bundle = build_evidence_bundle_v2(
            normalized_incident("hijack"), **legacy_kwargs()
        )
        bundle = copy.deepcopy(bundle)
        bundle["field_quality"][0] = {
            **bundle["field_quality"][0],
            "field_path": "/coverage_summary/unexplained_source_fact_count",
            "value_state": "not_retained",
            "missing_reason": None,
        }
        counts = candidate._bundle_reconciliation_counts(bundle)
        self.assertEqual(counts["unknown_missing_reason_count"], 1)
        self.assertEqual(counts["auto_zero_fill_count"], 1)

    def test_reconciliation_failure_reports_each_nonzero_blocking_count(self):
        bundle = copy.deepcopy(
            build_evidence_bundle_v2(
                normalized_incident("hijack"), **legacy_kwargs()
            )
        )
        bundle["incident"]["start_time"] = "2026-04-01T00:00:00Z"
        with self.assertRaisesRegex(
            candidate.EvidenceCandidateError,
            r'outside_window_record_count"?:1',
        ):
            candidate._build_reconciliation_summary(
                [bundle],
                evidence_module,
                ROOT / "contracts/data/evidence-bundle-v2.schema.json",
                ROOT / "frontend/node_modules/@redocly/ajv/dist/2020",
            )


if __name__ == "__main__":
    unittest.main()
