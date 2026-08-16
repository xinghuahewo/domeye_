from __future__ import annotations

from argparse import Namespace
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from dev.data_quality import p0_reproducibility as repro


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_bytes(value):
    return (canonical(value) + "\n").encode("utf-8")


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def write_json(path, value):
    path.write_bytes(canonical_bytes(value))


def write_pretty_json(path, value):
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def write_jsonl_gzip(path, rows):
    payload = b"".join(canonical_bytes(row) for row in rows)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as stream:
            stream.write(payload)
    return payload


def inventory(path, *, row_count=None, content=None):
    result = {
        "name": path.name,
        "media_type": "application/json",
        "sha256": sha(path.read_bytes()),
        "size_bytes": path.stat().st_size,
    }
    if row_count is not None:
        result["row_count"] = row_count
    if content is not None:
        result["content_sha256"] = sha(content)
    return result


def seal(directory):
    lines = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.name != "SHA256SUMS":
            lines.append("{}  {}".format(sha(path.read_bytes()), path.name))
    (directory / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


PROFILE = {
    "schema_version": 1,
    "id": "feb-mar-2026",
    "mode": "fixed",
    "timezone": "Asia/Shanghai",
    "window_start": "2026-02-01T00:00:00+08:00",
    "window_end_exclusive": "2026-04-01T00:00:00+08:00",
    "snapshot_time": "2026-03-31T23:59:59+08:00",
    "api_profile": "core",
}


class Fixture:
    def __init__(self, root):
        self.root = root
        self.d2_fingerprint = "1" * 64
        self.d3_fingerprint = None

    def d2(self, name, *, incident_suffix="1"):
        directory = self.root / name
        directory.mkdir()
        incident_id = "inc_v1_" + incident_suffix * 24
        rows = {
            "incidents.jsonl.gz": [{"incident_id": incident_id}],
            "links.jsonl.gz": [{"incident_id": incident_id, "detail_reference": "x"}],
            "collision_groups.jsonl.gz": [],
            "quarantine.jsonl.gz": [],
        }
        files = {}
        for filename, values in rows.items():
            payload = write_jsonl_gzip(directory / filename, values)
            files[filename] = inventory(
                directory / filename,
                row_count=len(values),
                content=payload,
            )
        summary = {
            "incident_count": 1,
            "link_count": 1,
            "collision_group_count": 0,
            "quarantine_count": 0,
            "unexplained_reverse_orphan_count": 0,
            "unexplained_forward_reference_count": 0,
        }
        manifest = {
            "schema_version": "p0_normalization_candidate_v1",
            "candidate_kind": "readonly_legacy_fact_normalization",
            "candidate_fingerprint_sha256": "0" * 64,
            "data_profile": PROFILE,
            "window_utc": {
                "start": "2026-01-31T16:00:00Z",
                "end_exclusive": "2026-03-31T16:00:00Z",
            },
            "source": {
                "release_id": "fixture-r1",
                "state_sha256": "8" * 64,
                "manifest_sha256": "9" * 64,
                "database_manifest_sha256": "a" * 64,
                "inventory_sha256": "b" * 64,
                "database": {
                    "system_identifier": "fixture-system",
                    "transaction_read_only": True,
                },
                "provenance": {"probe_sha256": "c" * 64},
                "normalizer_hashes": {"facts.py": "2" * 64},
            },
            "source_table_counts": {"event_table_202602": 1},
            "files": files,
            "summary": summary,
            "sample": {"enabled": False, "max_events": None, "admissible": True},
            "admission": {
                "status": "legacy_candidate_ready",
                "eligible_for_release_gate": True,
                "blocking_reasons": [],
                "raw_traceable": False,
            },
            "materialization_policy": {"missing_values_coerced_to_zero": False},
            "classification": "observation_only",
            "causal_conclusion": None,
        }
        manifest["candidate_fingerprint_sha256"] = repro._canonical_sha256(
            repro._d2_fingerprint_payload(manifest, "fixture D2")
        )
        self.d2_fingerprint = manifest["candidate_fingerprint_sha256"]
        # 与 p0_normalize_candidate.py 的冻结 D2 producer 字节格式一致。
        write_pretty_json(directory / "manifest.json", manifest)
        (directory / "摘要.md").write_text("# D2\n", encoding="utf-8")
        seal(directory)
        return directory

    def d3(self, name, *, artifact_sha="3" * 64):
        directory = self.root / name
        directory.mkdir()
        artifact_id = repro._artifact_id(artifact_sha)
        payload = {
            "schema_version": 1,
            "manifest_kind": "mrt_artifact_manifest",
            "artifact_id_schema": "artifact_id_v1",
            "data_profile": {
                "id": PROFILE["id"],
                "timezone": PROFILE["timezone"],
                "window_start": PROFILE["window_start"],
                "window_end_exclusive": PROFILE["window_end_exclusive"],
                "window_start_utc": "2026-01-31T16:00:00Z",
                "window_end_exclusive_utc": "2026-03-31T16:00:00Z",
            },
            "filename_timestamp_timezone": "UTC",
            "collector_allowlist": ["rrc25"],
            "scan_policy": {
                "out_of_window": "exclude_without_hash",
                "compression_envelope_validation": "full_stream_to_eof_crc_or_equivalent",
            },
            "artifacts": [
                {
                    "artifact_id": artifact_id,
                    "artifact_id_schema": "artifact_id_v1",
                    "collector_id": "rrc25",
                    "artifact_type": "update",
                    "artifact_time_utc": "2026-02-01T00:00:00Z",
                    "relative_path": "rrc25/updates.gz",
                    "filename_family": "updates",
                    "compression": "gz",
                    "size_bytes": 10,
                    "file_sha256": artifact_sha,
                }
            ],
            "invalid_in_window": [],
            "summary": {
                "artifact_count": 1,
                "size_bytes": 10,
                "invalid_in_window": {
                    "file_count": 0,
                    "size_bytes": 0,
                    "by_missing_reason": {
                        "compressed_stream_invalid": {"file_count": 0, "size_bytes": 0},
                        "compression_magic_mismatch": {"file_count": 0, "size_bytes": 0},
                        "empty_file": {"file_count": 0, "size_bytes": 0},
                    },
                },
            },
            "coverage": {
                "expected_slots": 1,
                "available_slots": 1,
                "missing_slots": 0,
                "coverage_ratio": 1,
                "coverage_status": "complete",
                "missing_value_state": None,
                "missing_ranges": [],
                "by_collector": [
                    {
                        "collector_id": "rrc25",
                        "by_artifact_type": {
                            "update": {
                                "expected_slots": 1,
                                "available_slots": 1,
                                "missing_slots": 0,
                            }
                        },
                    }
                ],
            },
        }
        fingerprint = repro._canonical_sha256(
            {"schema": "mrt_artifact_manifest_fingerprint_v1", "manifest": payload}
        )
        manifest = {**payload, "manifest_fingerprint_sha256": fingerprint}
        self.d3_fingerprint = fingerprint
        manifest_path = directory / "p0-artifact-manifest.json"
        write_json(manifest_path, manifest)
        write_json(
            directory / "p0-artifact-manifest.summary.zh.json",
            {
                "schema_version": 1,
                "summary_kind": "p0_raw_artifact_manifest_summary_zh",
                "manifest": {
                    "sha256": sha(manifest_path.read_bytes()),
                    "fingerprint_sha256": fingerprint,
                },
                "verification": {
                    "verified": True,
                    "manifest_fingerprint_sha256": fingerprint,
                },
            },
        )
        seal(directory)
        return directory

    def metric(self, name, d2_dir, d3_dir, *, value=1):
        directory = self.root / name
        directory.mkdir()
        rows = [
            {
                "metric_name": "bgp_announce_record_count",
                "points": [{"timestamp": "2026-02-01T00:00:00Z", "value": value}],
            }
        ]
        series_payload = write_jsonl_gzip(directory / "metric-series.jsonl.gz", rows)
        reconciliation_payload = {
            "schema_version": "metric_reconciliation_v1",
            "series_count": 1,
            "point_count": 1,
            "deterministic_summary_match": True,
        }
        reconciliation = {
            **reconciliation_payload,
            "summary_fingerprint_sha256": repro._canonical_sha256(
                {
                    "schema": "metric_reconciliation_summary_fingerprint_v1",
                    "summary": reconciliation_payload,
                }
            ),
        }
        write_pretty_json(directory / "metric-reconciliation-summary.json", reconciliation)
        files = {
            "metric-series.jsonl.gz": inventory(
                directory / "metric-series.jsonl.gz", row_count=1, content=series_payload
            ),
            "metric-reconciliation-summary.json": inventory(
                directory / "metric-reconciliation-summary.json"
            ),
        }
        d2_manifest = json.loads((d2_dir / "manifest.json").read_text(encoding="utf-8"))
        d3_manifest_path = d3_dir / "p0-artifact-manifest.json"
        d3_summary_path = d3_dir / "p0-artifact-manifest.summary.zh.json"
        d3_manifest = json.loads(d3_manifest_path.read_text(encoding="utf-8"))
        manifest = {
            "schema_version": "p0_metric_candidate_v1",
            "candidate_kind": "readonly_global_metric_series",
            "candidate_fingerprint_sha256": "0" * 64,
            "data_profile": PROFILE,
            "metric_window_utc": {
                "start": "2026-01-31T16:00:00Z",
                "end_exclusive": "2026-03-31T16:00:00Z",
            },
            "generated_at": "2026-07-20T12:00:00Z",
            "source_slot_policies": {"missing_values_coerced_to_zero": False},
            "sources": {
                "d2_normalization": {
                    "fingerprint_sha256": d2_manifest["candidate_fingerprint_sha256"],
                    "manifest_sha256": sha((d2_dir / "manifest.json").read_bytes()),
                    "checksums_sha256": sha((d2_dir / "SHA256SUMS").read_bytes()),
                    "incidents_sha256": sha((d2_dir / "incidents.jsonl.gz").read_bytes()),
                },
                "d3_artifacts": {
                    "fingerprint_sha256": d3_manifest["manifest_fingerprint_sha256"],
                    "manifest_sha256": sha(d3_manifest_path.read_bytes()),
                    "summary_sha256": sha(d3_summary_path.read_bytes()),
                    "checksums_sha256": sha((d3_dir / "SHA256SUMS").read_bytes()),
                },
            },
            "provenance": {"git_sha": "7" * 40},
            "files": files,
            "summary": {"generated_metric_count": 1, "value_sum": value},
            "sample": {"enabled": False},
            "classification": "observation_only",
            "causal_conclusion": None,
        }
        manifest["candidate_fingerprint_sha256"] = repro._canonical_sha256(
            repro._candidate_fingerprint_payload(manifest, "metric")
        )
        write_pretty_json(directory / "manifest.json", manifest)
        (directory / "摘要.md").write_text("# Metric\n", encoding="utf-8")
        seal(directory)
        return directory

    def route(self, name):
        directory = self.root / name
        directory.mkdir()
        index_path = directory / "p0-route-event-pilot.sqlite3"
        connection = sqlite3.connect(str(index_path))
        connection.executescript(
            """
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
            CREATE TABLE artifact(artifact_id TEXT PRIMARY KEY);
            CREATE TABLE raw_record(artifact_id TEXT NOT NULL,record_ordinal INTEGER NOT NULL);
            CREATE TABLE vantage_point(vp_id TEXT PRIMARY KEY);
            CREATE TABLE as_path(path_id TEXT PRIMARY KEY);
            CREATE TABLE route_event(route_event_id TEXT PRIMARY KEY);
            CREATE TABLE incident_observation(incident_id TEXT PRIMARY KEY);
            CREATE TABLE incident_route_event_link(
              incident_id TEXT,route_event_id TEXT,object_type TEXT,object_id TEXT
            );
            """
        )
        route_event_id = "rte_v1_" + "8" * 32
        connection.execute("INSERT INTO artifact VALUES(?)", ("art_v1_" + "9" * 32,))
        connection.execute("INSERT INTO raw_record VALUES(?,?)", ("art_v1_" + "9" * 32, 0))
        connection.execute("INSERT INTO route_event VALUES(?)", (route_event_id,))
        summary = {
            "schema_version": "route_event_index_summary_v1",
            "manifest_fingerprint_sha256": self.d3_fingerprint,
            "import_run_id": "run_v1_" + "a" * 32,
            "build_scope": {
                "scope_mode": "bounded_update_pilot",
                "pilot_only": True,
                "production_complete": False,
                "limits": {},
                "data_profile": {
                    "id": PROFILE["id"],
                    "timezone": PROFILE["timezone"],
                    "window_start": PROFILE["window_start"],
                    "window_end_exclusive": PROFILE["window_end_exclusive"],
                },
                "raw_reference_contract": "fixture",
                "limitations": ["fixture_pilot_only"],
            },
            "raw_record_count": 1,
            "route_event_count": 1,
        }
        for key, value in (
            ("schema_version", "p0_route_event_index_v1"),
            ("summary", summary),
        ):
            connection.execute(
                "INSERT INTO metadata(key,value_json) VALUES(?,?)", (key, canonical(value))
            )
        connection.commit()
        fingerprint = repro._route_index_fingerprint(connection)
        connection.execute(
            "INSERT INTO metadata(key,value_json) VALUES(?,?)",
            ("index_fingerprint_sha256", canonical(fingerprint)),
        )
        connection.commit()
        connection.close()
        write_json(
            directory / "route-event-reconciliation-summary.json",
            {**summary, "index_fingerprint_sha256": fingerprint},
        )
        write_json(
            directory / "update-pilot-selection.json",
            {"schema_version": 1, "selection_kind": "mrt_update_pilot_selection"},
        )
        (directory / "摘要.md").write_text("# RouteEvent pilot\n", encoding="utf-8")
        seal(directory)
        return directory


class ReproducibilityCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def build_inputs(self):
        fixture = Fixture(self.root)
        d2a = fixture.d2("d2a")
        d2b = fixture.d2("d2b")
        d3a = fixture.d3("d3a")
        d3b = fixture.d3("d3b")
        ma = fixture.metric("ma", d2a, d3a)
        mb = fixture.metric("mb", d2b, d3b)
        return d2a, d2b, d3a, d3b, ma, mb

    def args(self, inputs, output="out"):
        d2a, d2b, d3a, d3b, ma, mb = inputs
        return Namespace(
            d2_a=str(d2a),
            d2_b=str(d2b),
            d3_a=str(d3a),
            d3_b=str(d3b),
            metric_a=str(ma),
            metric_b=str(mb),
            route_a=None,
            route_b=None,
            output_dir=str(self.root / output),
        )

    def test_identical_reruns_generate_passing_deterministic_summary(self):
        inputs = self.build_inputs()
        first = repro.run(self.args(inputs, "first"))
        second = repro.run(self.args(inputs, "second"))
        semantic = first["semantic_validation"]
        self.assertTrue(first["byte_identity"]["all_corresponding_files_match"])
        self.assertTrue(semantic["record_count_metadata_match"])
        self.assertTrue(semantic["aggregate_summary_match"])
        self.assertTrue(all(semantic["fingerprint_matches"].values()))
        self.assertEqual(semantic["stable_id_match_ratio"], 1)
        self.assertEqual(first["full_semantic_validation"]["status"], "passed")
        self.assertEqual(
            (self.root / "first" / "reproducibility-summary.json").read_bytes(),
            (self.root / "second" / "reproducibility-summary.json").read_bytes(),
        )
        self.assertEqual(
            (self.root / "first" / "SHA256SUMS").read_bytes(),
            (self.root / "second" / "SHA256SUMS").read_bytes(),
        )

    def test_generated_contract_is_consumed_by_d5_reproducibility_checks(self):
        from backend.data_pipeline.quality import build_quality_report
        from dev.tests.test_p0_quality_gate import (
            RawFixture,
            context,
            d2_manifest,
            metric_summary,
        )

        generated = repro.run(self.args(self.build_inputs()))
        raw = RawFixture(partial=True)
        try:
            result = build_quality_report(
                d2_manifest(),
                raw.manifest,
                context=context(),
                artifact_verification_summary=raw.verification,
                metric_summary=metric_summary(),
                reproducibility_summary=generated,
            )
        finally:
            raw.close()
        statuses = {row["check_id"]: row["status"] for row in result.report["checks"]}
        self.assertEqual(statuses["reproducibility-byte-identity"], "pass")
        self.assertEqual(statuses["reproducibility-sampled-semantics"], "pass")
        self.assertEqual(statuses["reproducibility-scope-integrity"], "pass")
        self.assertEqual(statuses["reproducibility-full-semantic-validation"], "pass")

    def test_optional_route_event_sqlite_is_logically_recomputed_and_compared(self):
        inputs = self.build_inputs()
        fixture = Fixture(self.root)
        fixture.d3_fingerprint = json.loads(
            (inputs[2] / "p0-artifact-manifest.json").read_text(encoding="utf-8")
        )["manifest_fingerprint_sha256"]
        route_a = fixture.route("route-a")
        route_b = fixture.route("route-b")
        args = self.args(inputs)
        args.route_a = str(route_a)
        args.route_b = str(route_b)
        result = repro.run(args)
        semantic = result["semantic_validation"]
        self.assertTrue(semantic["fingerprint_matches"]["route_event"])
        self.assertEqual(semantic["record_counts"]["route_event"]["route_events"]["a"], 1)
        self.assertEqual(semantic["stable_id_scope"]["by_kind"]["route_event"], {"a": 1, "b": 1})

    def test_real_metric_difference_outputs_false_instead_of_hardcoded_true(self):
        inputs = list(self.build_inputs())
        fixture = Fixture(self.root)
        fixture.d3_fingerprint = json.loads(
            (inputs[2] / "p0-artifact-manifest.json").read_text(encoding="utf-8")
        )["manifest_fingerprint_sha256"]
        # 使用相同输入身份但不同落盘 series/summary，模拟非确定性输出。
        inputs[5] = fixture.metric("mb-different", inputs[1], inputs[3], value=2)
        result = repro.run(self.args(tuple(inputs)))
        semantic = result["semantic_validation"]
        self.assertFalse(semantic["aggregate_summary_match"])
        self.assertFalse(semantic["fingerprint_matches"]["metric"])
        self.assertTrue(semantic["record_count_metadata_match"])

    def test_stable_id_difference_is_measured_from_stream(self):
        inputs = list(self.build_inputs())
        fixture = Fixture(self.root)
        changed_d2 = fixture.d2("d2b-different", incident_suffix="2")
        inputs[1] = changed_d2
        inputs[5] = fixture.metric("mb-different-d2", changed_d2, inputs[3])
        # B 侧下游重新绑定其实际 D2；输入根身份仍可比较，实际稳定 ID 集不同。
        result = repro.run(self.args(tuple(inputs)))
        semantic = result["semantic_validation"]
        self.assertLess(semantic["stable_id_match_ratio"], 1)
        self.assertFalse(semantic["fingerprint_matches"]["d2"])

    def test_bounded_mode_separates_full_bytes_from_sampled_semantics(self):
        args = self.args(self.build_inputs())
        args.d2_record_limit = 1
        result = repro.run(args)
        semantic = result["semantic_validation"]
        self.assertEqual(result["schema_version"], "p0_reproducibility_summary_v2")
        self.assertTrue(result["byte_identity"]["all_files_rehashed"])
        self.assertTrue(result["byte_identity"]["all_corresponding_files_match"])
        self.assertEqual(semantic["mode"], "deterministic_bounded_sample_v1")
        self.assertIs(semantic["sample_only"], True)
        self.assertIs(semantic["population_coverage_claimed"], False)
        self.assertTrue(all(row["match"] for row in semantic["d2_sample_comparison"].values()))
        self.assertEqual(result["full_semantic_validation"]["status"], "not_run")
        self.assertEqual(
            result["conclusion"]["full_semantic_reproducibility_status"], "not_run"
        )

    def test_d2_file_inventory_difference_cannot_hide_behind_reused_fingerprint(self):
        inputs = self.build_inputs()
        changed = inputs[1]
        incident_id = "inc_v1_" + "1" * 24
        payload = write_jsonl_gzip(
            changed / "links.jsonl.gz",
            [{"incident_id": incident_id, "detail_reference": "changed"}],
        )
        manifest_path = changed / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["links.jsonl.gz"] = inventory(
            changed / "links.jsonl.gz", row_count=1, content=payload
        )
        # 故意复用旧字段，验证工具不盲信 producer 提供的 candidate fingerprint。
        write_pretty_json(manifest_path, manifest)
        seal(changed)
        with self.assertRaisesRegex(repro.ReproducibilityError, "D2 candidate fingerprint"):
            repro.run(self.args(inputs))

    def test_d2_manifest_requires_exact_frozen_pretty_serialization(self):
        inputs = self.build_inputs()
        manifest_path = inputs[0] / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        write_json(manifest_path, manifest)
        seal(inputs[0])
        with self.assertRaisesRegex(repro.ReproducibilityError, "不是规范 JSON 字节"):
            repro.run(self.args(inputs))

    def test_d2_manifest_rejects_duplicate_key_even_when_resigned(self):
        inputs = self.build_inputs()
        manifest_path = inputs[0] / "manifest.json"
        payload = manifest_path.read_text(encoding="utf-8")
        manifest_path.write_text(
            payload.replace(
                "{\n",
                '{\n  "schema_version": "duplicate",\n',
                1,
            ),
            encoding="utf-8",
        )
        seal(inputs[0])
        with self.assertRaisesRegex(repro.ReproducibilityError, "重复字段"):
            repro.run(self.args(inputs))

    def test_d2_manifest_rejects_overflow_number_even_when_resigned(self):
        inputs = self.build_inputs()
        manifest_path = inputs[0] / "manifest.json"
        payload = manifest_path.read_text(encoding="utf-8")
        manifest_path.write_text(
            payload.replace("{\n", '{\n  "overflow": 1e999,\n', 1),
            encoding="utf-8",
        )
        seal(inputs[0])
        with self.assertRaisesRegex(repro.ReproducibilityError, "非有限 JSON 数值"):
            repro.run(self.args(inputs))

    def test_d2_admission_is_recomputed_not_trusted(self):
        inputs = self.build_inputs()
        manifest_path = inputs[0] / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["admission"]["status"] = "not_eligible"
        write_pretty_json(manifest_path, manifest)
        seal(inputs[0])
        with self.assertRaisesRegex(repro.ReproducibilityError, "admission"):
            repro.run(self.args(inputs))

    def test_d2_sample_state_is_derived_from_max_events(self):
        inputs = self.build_inputs()
        manifest_path = inputs[0] / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sample"] = {"enabled": False, "max_events": 1, "admissible": True}
        manifest["candidate_fingerprint_sha256"] = repro._canonical_sha256(
            repro._d2_fingerprint_payload(manifest, "fixture D2")
        )
        write_pretty_json(manifest_path, manifest)
        seal(inputs[0])
        with self.assertRaisesRegex(repro.ReproducibilityError, "sample 与 max_events"):
            repro.run(self.args(inputs))

    def test_d2_sample_max_events_rejects_nonproducer_value(self):
        inputs = self.build_inputs()
        manifest_path = inputs[0] / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sample"] = {"enabled": True, "max_events": 0, "admissible": False}
        manifest["candidate_fingerprint_sha256"] = repro._canonical_sha256(
            repro._d2_fingerprint_payload(manifest, "fixture D2")
        )
        write_pretty_json(manifest_path, manifest)
        seal(inputs[0])
        with self.assertRaisesRegex(repro.ReproducibilityError, "sample.max_events"):
            repro.run(self.args(inputs))

    def test_d2_fingerprint_matches_independent_frozen_producer_formula(self):
        inputs = self.build_inputs()
        manifest = json.loads((inputs[0] / "manifest.json").read_text(encoding="utf-8"))
        source = manifest["source"]
        producer_payload = {
            "schema_version": "p0_normalization_candidate_v1",
            "data_profile": manifest["data_profile"],
            "source_release": {
                "release_id": source["release_id"],
                "system_identifier": source["database"]["system_identifier"],
                "state_sha256": source["state_sha256"],
                "manifest_sha256": source["manifest_sha256"],
                "database_manifest_sha256": source["database_manifest_sha256"],
                "inventory_sha256": source["inventory_sha256"],
            },
            "runner_sha256": source["provenance"]["probe_sha256"],
            "normalizer_hashes": source["normalizer_hashes"],
            "source_table_counts": manifest["source_table_counts"],
            "files": manifest["files"],
            "summary": manifest["summary"],
            "sample": manifest["sample"],
            "classification": manifest["classification"],
            "causal_conclusion": manifest["causal_conclusion"],
        }
        expected = hashlib.sha256(canonical(producer_payload).encode("utf-8")).hexdigest()
        self.assertEqual(manifest["candidate_fingerprint_sha256"], expected)

    def test_metric_d3_file_identity_must_close(self):
        inputs = self.build_inputs()
        directory = inputs[4]
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sources"]["d3_artifacts"]["summary_sha256"] = "f" * 64
        manifest["candidate_fingerprint_sha256"] = repro._canonical_sha256(
            repro._candidate_fingerprint_payload(manifest, "metric")
        )
        write_pretty_json(manifest_path, manifest)
        seal(directory)
        with self.assertRaisesRegex(repro.ReproducibilityError, "Metric 未闭合当前 D3"):
            repro.run(self.args(inputs))

    def test_metric_manifest_requires_exact_frozen_pretty_serialization(self):
        inputs = self.build_inputs()
        manifest_path = inputs[4] / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        write_json(manifest_path, manifest)
        seal(inputs[4])
        with self.assertRaisesRegex(repro.ReproducibilityError, "不是规范 JSON 字节"):
            repro.run(self.args(inputs))

    def test_unsigned_extra_file_fails_closed_without_output(self):
        inputs = self.build_inputs()
        (inputs[0] / "unsigned.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(repro.ReproducibilityError, "闭包"):
            repro.run(self.args(inputs))
        self.assertFalse((self.root / "out").exists())

    def test_symlink_input_fails_closed(self):
        inputs = list(self.build_inputs())
        linked = self.root / "d2-link"
        linked.symlink_to(inputs[0], target_is_directory=True)
        inputs[0] = linked
        with self.assertRaisesRegex(repro.ReproducibilityError, "符号链接"):
            repro.run(self.args(tuple(inputs)))

    def test_tampered_signed_file_fails_closed(self):
        inputs = self.build_inputs()
        with (inputs[4] / "metric-series.jsonl.gz").open("ab") as stream:
            stream.write(b"tamper")
        with self.assertRaisesRegex(repro.ReproducibilityError, "SHA256"):
            repro.run(self.args(inputs))

    def test_different_generation_input_is_rejected_as_incomparable(self):
        inputs = self.build_inputs()
        manifest_path = inputs[5] / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["generated_at"] = "2026-07-20T12:00:01Z"
        manifest["candidate_fingerprint_sha256"] = repro._canonical_sha256(
            repro._candidate_fingerprint_payload(manifest, "metric")
        )
        write_pretty_json(manifest_path, manifest)
        seal(inputs[5])
        with self.assertRaisesRegex(repro.ReproducibilityError, "输入身份不同"):
            repro.run(self.args(inputs))

    def test_existing_output_is_never_overwritten(self):
        inputs = self.build_inputs()
        output = self.root / "out"
        output.mkdir()
        owner = output / "owner.txt"
        owner.write_text("owner", encoding="utf-8")
        with self.assertRaisesRegex(repro.ReproducibilityError, "拒绝覆盖"):
            repro.run(self.args(inputs))
        self.assertEqual(owner.read_text(encoding="utf-8"), "owner")


if __name__ == "__main__":
    unittest.main()
