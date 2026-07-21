import gzip
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dev.data_profile import load_data_profile
from dev.data_quality import p0_metric_candidate as metric_candidate_module
from dev.data_quality.p0_metric_candidate import (
    ALL_METRICS,
    ARTIFACT_FINGERPRINT_SCHEMA,
    KNOWN_PROCESSING_GAPS_UTC,
    RECONCILIATION_FILE,
    MetricCandidateError,
    _canonical_bytes,
    _canonical_sha256,
    _required_d2_tables,
    generate_metric_candidate,
)


ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
SAMPLE_START = datetime(2026, 2, 24, 0, 0, tzinfo=UTC)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def utc_text(value):
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def raw_profile(profile):
    return {
        key: profile[key]
        for key in (
            "schema_version",
            "id",
            "mode",
            "timezone",
            "window_start",
            "window_end_exclusive",
            "snapshot_time",
            "api_profile",
        )
    }


def incident(
    suffix,
    event_type,
    event_time,
    *,
    end_time=None,
    object_type=None,
    object_id=None,
):
    affected = []
    if object_type is not None:
        affected.append(
            {
                "object_type": object_type,
                "object_id": object_id,
                "role": "affected",
                "source_field": "detail_url.problem",
            }
        )
    return {
        "schema_version": "p0_incident_normalization_v1",
        "incident_id": "inc_v1_" + suffix,
        "incident_id_schema": "incident_id_v1",
        "event_type": event_type,
        "event_time_utc": utc_text(event_time),
        "end_time_utc": utc_text(end_time) if end_time is not None else None,
        "duration_seconds": int((end_time - event_time).total_seconds())
        if end_time is not None
        else None,
        "affected_objects": affected,
        "fact_link_status": "matched",
        "field_quality": []
        if end_time is not None
        else [
            {
                "field": "end_time_utc",
                "status": "not_retained",
                "missing_reason": "legacy_field_not_retained",
            }
        ],
        "classification": "observation_only",
        "causal_conclusion": None,
    }


class RecordingCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.closed = False

    def execute(self, query, parameters=None):
        self.connection.executed.append((query, parameters))
        if 'FROM "public"."feature_country"' in query:
            self.rows = list(self.connection.feature_rows)

    def fetchmany(self, size=1000):
        batch, self.rows = self.rows[:size], self.rows[size:]
        return batch

    def close(self):
        self.closed = True


class RecordingConnection:
    def __init__(self, feature_rows):
        self.feature_rows = feature_rows
        self.executed = []
        self.rollback_count = 0
        self.cursors = []

    def cursor(self, *args, **kwargs):
        cursor = RecordingCursor(self)
        self.cursors.append(cursor)
        return cursor

    def rollback(self):
        self.rollback_count += 1


def context_fixture():
    return {
        "release_id": "20260717T124354Z",
        "port": 31627,
        "system_identifier": "7663836852697006116",
        "state_sha256": "1" * 64,
        "manifest_sha256": "2" * 64,
        "database_manifest_sha256": "3" * 64,
        "inventory_sha256": "4" * 64,
    }


def security_fixture(*args, **kwargs):
    return {
        "database": "bgp_project",
        "current_user": "domeye_core_reader",
        "system_identifier": "7663836852697006116",
        "transaction_read_only": True,
        "transaction_isolation": "repeatable read",
    }


class CandidateFixture:
    def __init__(self, root, profile, slots, incidents, *, invalid_slots=()):
        self.root = Path(root)
        self.root.mkdir(parents=True)
        self.profile = profile
        self.slots = list(slots)
        self.invalid_slots = list(invalid_slots)
        self.d2 = self.root / "d2"
        self.d3 = self.root / "d3"
        self.d2.mkdir()
        self.d3.mkdir()
        self._write_d2(incidents)
        self._write_d3()

    def _write_d2(self, records):
        payload = b"".join(_canonical_bytes(record, newline=True) for record in records)
        incidents_path = self.d2 / "incidents.jsonl.gz"
        with incidents_path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                compressed.write(payload)
        inventory = {
            "name": incidents_path.name,
            "media_type": "application/x-ndjson+gzip",
            "compression": {
                "algorithm": "gzip",
                "level": 9,
                "mtime": 0,
                "header_filename": "",
            },
            "order": "runner_defined_deterministic_order_v1",
            "row_count": len(records),
            "content_sha256": hashlib.sha256(payload).hexdigest(),
            "sha256": sha256(incidents_path),
            "size_bytes": incidents_path.stat().st_size,
        }
        source_counts = {name: 0 for name in sorted(_required_d2_tables())}
        family = {
            "hijack": "hijack",
            "sub_hijack": "sub_hijack",
            "leak": "leak_event",
            "prefix_outage": "prefix_outage",
            "as_outage": "as_outage",
            "country_outage": "country_outage",
        }
        for record in records:
            month = datetime.fromisoformat(
                record["event_time_utc"].replace("Z", "+00:00")
            ).astimezone(timezone(timedelta(hours=8))).strftime("%Y%m")
            source_counts["event_table_{}".format(month)] += 1
            source_counts["{}_{}".format(family[record["event_type"]], month)] += 1
        manifest = {
            "schema_version": "p0_normalization_candidate_v1",
            "candidate_fingerprint_sha256": "a" * 64,
            "data_profile": raw_profile(self.profile),
            "source_table_counts": source_counts,
            "files": {"incidents.jsonl.gz": inventory},
            "summary": {"incident_count": len(records)},
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
        manifest_path = self.d2 / "manifest.json"
        write_json(manifest_path, manifest)
        (self.d2 / "SHA256SUMS").write_text(
            "{}  incidents.jsonl.gz\n{}  manifest.json\n".format(
                sha256(incidents_path), sha256(manifest_path)
            ),
            encoding="utf-8",
        )

    def _write_d3(self):
        profile = {
            "id": self.profile["id"],
            "timezone": self.profile["timezone"],
            "window_start": self.profile["window_start"],
            "window_end_exclusive": self.profile["window_end_exclusive"],
            "window_start_utc": utc_text(self.profile["parsed"]["start"]),
            "window_end_exclusive_utc": utc_text(self.profile["parsed"]["end_exclusive"]),
        }
        artifacts = []
        for index, slot in enumerate(self.slots):
            digest = hashlib.sha256("slot-{}".format(index).encode()).hexdigest()
            artifacts.append(
                {
                    "artifact_id": "art_v1_" + digest[:32],
                    "artifact_id_schema": "artifact_id_v1",
                    "collector_id": "rrc25",
                    "artifact_type": "update",
                    "artifact_time_utc": utc_text(slot),
                    "relative_path": "rrc25/{}/updates.{}.gz".format(
                        slot.strftime("%Y.%m"), slot.strftime("%Y%m%d.%H%M")
                    ),
                    "filename_family": "updates",
                    "compression": "gz",
                    "size_bytes": index + 1,
                    "file_sha256": digest,
                }
            )
        invalid_in_window = [
            {
                "collector_id": "rrc25",
                "artifact_type": "update",
                "artifact_time_utc": utc_text(slot),
                "relative_path": "rrc25/{}/updates.{}.gz".format(
                    slot.strftime("%Y.%m"), slot.strftime("%Y%m%d.%H%M")
                ),
                "filename_family": "updates",
                "compression": "gz",
                "size_bytes": 0,
                "file_sha256": hashlib.sha256(b"").hexdigest(),
                "value_state": "parse_failed",
                "missing_reason": "empty_file",
            }
            for slot in self.invalid_slots
        ]
        invalid_by_reason = {
            "compressed_stream_invalid": {"file_count": 0, "size_bytes": 0},
            "compression_magic_mismatch": {"file_count": 0, "size_bytes": 0},
            "empty_file": {
                "file_count": len(invalid_in_window),
                "size_bytes": 0,
            },
        }
        manifest = {
            "schema_version": 1,
            "manifest_kind": "mrt_artifact_manifest",
            "artifact_id_schema": "artifact_id_v1",
            "data_profile": profile,
            "filename_timestamp_timezone": "UTC",
            "collector_allowlist": ["rrc25"],
            "scan_policy": {
                "out_of_window": "exclude_without_hash",
                "invalid_in_window": "full_hash_quarantine_exclude_from_available_slots",
                "compression_envelope_validation": "full_stream_to_eof_crc_or_equivalent",
                "duplicate_content": {
                    "valid_artifact": "reject_across_paths",
                    "invalid_compressed_stream_invalid": "reject_across_paths",
                    "invalid_empty_file": "allow_across_unique_paths_and_slots",
                    "invalid_compression_magic_mismatch": "reject_across_paths",
                },
                "directory_scope": metric_candidate_module._expected_d3_directory_scope(
                    self.profile["parsed"]["start"].astimezone(UTC),
                    self.profile["parsed"]["end_exclusive"].astimezone(UTC),
                ),
            },
            "artifacts": artifacts,
            "invalid_in_window": invalid_in_window,
            "summary": {
                "artifact_count": len(artifacts),
                "size_bytes": sum(item["size_bytes"] for item in artifacts),
                "by_artifact_type": {
                    "update": {
                        "artifact_count": len(artifacts),
                        "size_bytes": sum(item["size_bytes"] for item in artifacts),
                    },
                    "rib": {"artifact_count": 0, "size_bytes": 0},
                },
                "by_collector": [],
                "excluded_out_of_window": {
                    "file_count": 0,
                    "size_bytes": 0,
                    "by_reason": {},
                    "boundary_samples": [],
                },
                "invalid_in_window": {
                    "file_count": len(invalid_in_window),
                    "size_bytes": 0,
                    "by_missing_reason": invalid_by_reason,
                },
            },
            "coverage": {
                "expected_slots": 0,
                "available_slots": len(artifacts),
                "missing_slots": 0,
                "coverage_ratio": 0,
                "coverage_status": "partial",
                "missing_value_state": "source_unavailable",
                "by_collector": [
                    {
                        "collector_id": "rrc25",
                        "by_artifact_type": {
                            "update": {
                                "expected_slots": 16992,
                                "available_slots": len(artifacts),
                                "missing_slots": 16992 - len(artifacts),
                                "coverage_ratio": round(len(artifacts) / 16992, 8),
                                "coverage_status": "partial",
                                "missing_ranges": [],
                            },
                            "rib": {
                                "expected_slots": 177,
                                "available_slots": 0,
                                "missing_slots": 177,
                                "coverage_ratio": 0,
                                "coverage_status": "partial",
                                "missing_ranges": [],
                            },
                        },
                    }
                ],
                "missing_ranges": [],
            },
        }
        manifest["manifest_fingerprint_sha256"] = _canonical_sha256(
            {"schema": ARTIFACT_FINGERPRINT_SCHEMA, "manifest": manifest}
        )
        self.d3_manifest = self.d3 / "p0-artifact-manifest.json"
        write_json(self.d3_manifest, manifest)
        self.d3_summary = self.d3 / "p0-artifact-manifest.summary.zh.json"
        write_json(
            self.d3_summary,
            {
                "verification": {
                    "verified": True,
                    "invalid_in_window_count": len(invalid_in_window),
                },
                "manifest": {
                    "sha256": sha256(self.d3_manifest),
                    "fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
                },
                "directory_scope": manifest["scan_policy"]["directory_scope"],
                "invalid_in_window": {
                    "file_count": len(invalid_in_window),
                    "size_bytes": 0,
                    "by_missing_reason": invalid_by_reason,
                    "records": invalid_in_window,
                },
            },
        )
        self.d3_checksums = self.d3 / "SHA256SUMS"
        self.d3_checksums.write_text(
            "{}  {}\n{}  {}\n".format(
                sha256(self.d3_manifest),
                self.d3_manifest.name,
                sha256(self.d3_summary),
                self.d3_summary.name,
            ),
            encoding="utf-8",
        )


def load_series(output):
    records = []
    with gzip.open(Path(output) / "metric-series.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            records.append(json.loads(line))
    return {record["metric_name"]: record for record in records}


def assert_schema_valid(test_case, payloads):
    import subprocess

    ajv_module = ROOT / "frontend/node_modules/@redocly/ajv/dist/2020"
    schema_path = ROOT / "contracts/data/metric-series.schema.json"
    script = r"""
const fs = require('fs')
const Ajv2020 = require(process.argv[1]).default
const schema = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const ajv = new Ajv2020({allErrors:true,allowUnionTypes:true,strict:true,validateFormats:true})
ajv.addFormat('date-time',{type:'string',validate:(value)=>/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)})
const validate = ajv.compile(schema)
for (const payload of JSON.parse(fs.readFileSync(0,'utf8'))) {
  if (!validate(payload)) { process.stderr.write(ajv.errorsText(validate.errors)); process.exit(1) }
}
"""
    result = subprocess.run(
        ["node", "-e", script, str(ajv_module), str(schema_path)],
        cwd=str(ROOT),
        input=json.dumps(payloads, ensure_ascii=False, allow_nan=False),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    test_case.assertEqual(result.returncode, 0, result.stderr)


class MetricCandidateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.base = Path(self.temporary.name)
        self.profile = load_data_profile()

    def tearDown(self):
        self.temporary.cleanup()

    def run_candidate(
        self,
        fixture,
        feature_rows,
        output_name="output",
        connection=None,
        **overrides,
    ):
        connection = connection or RecordingConnection(feature_rows)
        output = self.base / output_name
        values = {
            "profile": self.profile,
            "context": context_fixture(),
            "database_config": {
                "DOMEYE_CORE_DB_READER_USER": "domeye_core_reader",
                "DOMEYE_CORE_DB_NAME": "bgp_project",
            },
            "provenance": {
                "git_sha": "b" * 40,
                "git_dirty": True,
                "git_status_sha256": "c" * 64,
                "probe_sha256": "d" * 64,
                "data_profile_sha256": "e" * 64,
                "data_profile_loader_sha256": "f" * 64,
            },
            "project_root": ROOT,
            "pipeline_root": ROOT,
            "d2_candidate_dir": fixture.d2,
            "d3_manifest_path": fixture.d3_manifest,
            "d3_summary_path": fixture.d3_summary,
            "d3_checksum_path": fixture.d3_checksums,
            "output_dir": output,
            "generated_at": "2026-07-20T12:30:00Z",
            "sample_window_start": utc_text(SAMPLE_START),
            "sample_window_end_exclusive": utc_text(SAMPLE_START + timedelta(minutes=10)),
            "security_verifier": security_fixture,
        }
        values.update(overrides)
        manifest = generate_metric_candidate(connection, **values)
        return manifest, output, connection

    def test_all_ten_metrics_are_schema_valid_readonly_and_deterministic(self):
        slots = [SAMPLE_START, SAMPLE_START + timedelta(minutes=5)]
        records = [
            incident(
                "0" * 23 + "1",
                "prefix_outage",
                SAMPLE_START,
                end_time=SAMPLE_START + timedelta(minutes=9),
                object_type="prefix",
                object_id="10.0.0.0/24",
            ),
            incident(
                "0" * 23 + "2",
                "as_outage",
                SAMPLE_START + timedelta(minutes=1),
                end_time=SAMPLE_START + timedelta(minutes=8),
                object_type="asn",
                object_id="4134",
            ),
            incident("0" * 23 + "3", "hijack", SAMPLE_START + timedelta(minutes=2)),
        ]
        fixture = CandidateFixture(self.base / "inputs", self.profile, slots, records)
        rows = [
            (
                (slot + timedelta(hours=8)).replace(tzinfo=None),
                100 + index,
                20 + index,
                (100 + index) * 256,
                7 + index,
                3 + index,
            )
            for index, slot in enumerate(slots)
        ]
        first, first_output, connection = self.run_candidate(fixture, rows, "first")
        second, second_output, _ = self.run_candidate(fixture, rows, "second")

        self.assertEqual(first["summary"]["generated_metric_names"], list(ALL_METRICS))
        self.assertEqual(first["summary"]["generated_metric_count"], 10)
        self.assertEqual(first["admission"]["status"], "not_eligible")
        self.assertEqual(first["admission"]["blocking_reasons"], ["fixture_sample_not_admissible"])
        statements = [" ".join(query.split()) for query, _ in connection.executed]
        self.assertEqual(statements[0], "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self.assertTrue(any('FROM "public"."feature_country"' in query for query in statements))
        self.assertGreaterEqual(connection.rollback_count, 2)

        series = load_series(first_output)
        self.assertEqual(set(series), set(ALL_METRICS))
        self.assertEqual(series["anomaly_incident_count"]["points"][0]["value"], 3)
        self.assertEqual(series["prefix_outage_concurrent_count"]["points"][0]["value"], 1)
        assert_schema_valid(self, list(series.values()))

        reconciliation = json.loads(
            (first_output / RECONCILIATION_FILE).read_text(encoding="utf-8")
        )
        self.assertEqual(reconciliation["schema_version"], "metric_reconciliation_v1")
        self.assertEqual(reconciliation["series_count"], 10)
        self.assertEqual(reconciliation["admitted_metric_count"], 10)
        self.assertEqual(reconciliation["formula_contract_coverage_ratio"], 1)
        self.assertEqual(reconciliation["reconciliation_difference_count"], 0)
        self.assertEqual(reconciliation["unclassified_gap_count"], 0)
        self.assertEqual(reconciliation["unknown_missing_reason_count"], 0)
        self.assertEqual(reconciliation["confirmed_missing_zero_fill_count"], 0)
        self.assertEqual(reconciliation["outside_window_point_count"], 0)
        self.assertEqual(reconciliation["strict_schema_status"], "passed")
        self.assertEqual(reconciliation["schema_invalid_count"], 0)
        self.assertEqual(reconciliation["schema_validated_series_count"], 10)
        self.assertEqual(
            reconciliation["source_reconciliation_scope"],
            "independent_readonly_feature_rows_and_sqlite_interval_projection_v1",
        )
        self.assertEqual(reconciliation["source_reconciliation_expected_point_count"], 20)
        self.assertEqual(reconciliation["source_reconciliation_actual_point_count"], 20)
        self.assertEqual(reconciliation["source_reconciliation_difference_count"], 0)
        self.assertEqual(reconciliation["source_reconciliation_difference_count_by_type"], {})
        self.assertEqual(reconciliation["source_reconciliation_failure_samples"], [])
        self.assertEqual(reconciliation["reconciliation_difference_count_by_type"], {})
        self.assertEqual(reconciliation["reconciliation_failure_samples"], [])
        self.assertEqual(
            set(reconciliation["source_reconciliation_difference_count_by_metric"]),
            set(ALL_METRICS),
        )
        self.assertTrue(
            all(
                not differences
                for differences in reconciliation[
                    "source_reconciliation_difference_count_by_metric"
                ].values()
            )
        )
        self.assertEqual(reconciliation["internal_structural_difference_count"], 0)
        self.assertEqual(reconciliation["internal_roundtrip_difference_count"], 0)
        self.assertEqual(
            reconciliation["schema_sha256"],
            sha256(ROOT / "contracts/data/metric-series.schema.json"),
        )
        self.assertRegex(reconciliation["validator_module_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(reconciliation["deterministic_summary_match"])
        self.assertEqual(
            reconciliation["deterministic_summary_scope"],
            "internal_memory_vs_emitted_roundtrip_only",
        )
        self.assertFalse(reconciliation["cross_run_reproducibility_claimed"])
        self.assertEqual(
            reconciliation["internal_rebuild"]["first_summary_sha256"],
            reconciliation["internal_rebuild"]["second_summary_sha256"],
        )

        for line in (first_output / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, sha256(first_output / name))

        for name in (
            "metric-series.jsonl.gz",
            RECONCILIATION_FILE,
            "manifest.json",
            "摘要.md",
            "SHA256SUMS",
        ):
            self.assertEqual((first_output / name).read_bytes(), (second_output / name).read_bytes())
        self.assertEqual(first["candidate_fingerprint_sha256"], second["candidate_fingerprint_sha256"])

    def test_independent_source_projection_blocks_emitted_point_mutation(self):
        slots = [SAMPLE_START, SAMPLE_START + timedelta(minutes=5)]
        fixture = CandidateFixture(self.base / "inputs", self.profile, slots, [])
        rows = [
            (
                (slot + timedelta(hours=8)).replace(tzinfo=None),
                100,
                20,
                25600,
                7,
                3,
            )
            for slot in slots
        ]
        original_reader = metric_candidate_module._read_emitted_metric_series

        def read_with_mutation(path):
            records = original_reader(path)
            announce = next(
                record
                for record in records
                if record["metric_name"] == "bgp_announce_record_count"
            )
            announce["points"][0]["value"] += 1
            return records

        with patch.object(
            metric_candidate_module,
            "_read_emitted_metric_series",
            side_effect=read_with_mutation,
        ):
            manifest, output, _ = self.run_candidate(fixture, rows)

        reconciliation = json.loads(
            (output / RECONCILIATION_FILE).read_text(encoding="utf-8")
        )
        self.assertGreater(reconciliation["reconciliation_difference_count"], 0)
        self.assertEqual(reconciliation["source_reconciliation_difference_count"], 1)
        self.assertEqual(
            reconciliation["source_reconciliation_difference_count_by_metric"][
                "bgp_announce_record_count"
            ],
            {"value": 1},
        )
        self.assertEqual(
            reconciliation["source_reconciliation_difference_count_by_type"],
            {"value": 1},
        )
        sample = reconciliation["source_reconciliation_failure_samples"][0]
        self.assertEqual(sample["metric_name"], "bgp_announce_record_count")
        self.assertEqual(sample["difference_type"], "value")
        self.assertEqual(sample["time"], utc_text(SAMPLE_START))
        self.assertIn("metric_reconciliation_mismatch", manifest["admission"]["blocking_reasons"])
        self.assertIn("metric_internal_rebuild_mismatch", manifest["admission"]["blocking_reasons"])

    def test_withdraw_ratio_source_projection_rechecks_formula_inputs(self):
        slots = [SAMPLE_START, SAMPLE_START + timedelta(minutes=5)]
        fixture = CandidateFixture(self.base / "inputs", self.profile, slots, [])
        rows = [
            (
                (slot + timedelta(hours=8)).replace(tzinfo=None),
                100,
                20,
                25600,
                7,
                3,
            )
            for slot in slots
        ]
        original_reader = metric_candidate_module._read_emitted_metric_series

        def read_with_ratio_input_mutation(path):
            records = original_reader(path)
            ratio = next(
                record
                for record in records
                if record["metric_name"] == "bgp_withdraw_ratio"
            )
            ratio["points"][0]["formula_inputs"][
                "numerator_withdraw_count"
            ] += 1
            return records

        with patch.object(
            metric_candidate_module,
            "_read_emitted_metric_series",
            side_effect=read_with_ratio_input_mutation,
        ):
            manifest, output, _ = self.run_candidate(fixture, rows)

        reconciliation = json.loads(
            (output / RECONCILIATION_FILE).read_text(encoding="utf-8")
        )
        self.assertEqual(reconciliation["reconciliation_difference_count"], 1)
        self.assertEqual(
            reconciliation["reconciliation_difference_count_by_metric"][
                "bgp_withdraw_ratio"
            ],
            {"formula_inputs": 1},
        )
        self.assertEqual(
            reconciliation["reconciliation_difference_count_by_type"],
            {"formula_inputs": 1},
        )
        self.assertEqual(reconciliation["internal_structural_difference_count"], 1)
        self.assertIn("metric_reconciliation_mismatch", manifest["admission"]["blocking_reasons"])
        self.assertIn("metric_internal_structure_mismatch", manifest["admission"]["blocking_reasons"])

    def test_verified_source_without_dense_feature_row_is_processing_gap_not_zero(self):
        slots = [SAMPLE_START + timedelta(minutes=5 * index) for index in range(3)]
        fixture = CandidateFixture(self.base / "inputs", self.profile, slots, [])
        rows = [
            (
                slots[0].astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None),
                100,
                20,
                25600,
                7,
                3,
            ),
            (
                slots[2].astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None),
                102,
                22,
                26112,
                9,
                4,
            ),
        ]
        manifest, output, _ = self.run_candidate(
            fixture,
            rows,
            sample_window_end_exclusive=utc_text(SAMPLE_START + timedelta(minutes=15)),
        )
        self.assertEqual(manifest["summary"]["feature_processing_gap_slot_count"], 1)
        announce = load_series(output)["bgp_announce_record_count"]
        point = announce["points"][1]
        self.assertEqual(point["value_state"], "processing_gap")
        self.assertEqual(point["missing_reason"], "processing_gap")
        self.assertIsNone(point["value"])
        self.assertEqual(announce["coverage"]["processing_gap_sample_count"], 1)

    def test_known_invalid_source_row_is_ignored_and_remains_null(self):
        valid_slot = SAMPLE_START
        invalid_slot = SAMPLE_START + timedelta(minutes=5)
        fixture = CandidateFixture(
            self.base / "inputs",
            self.profile,
            [valid_slot],
            [],
            invalid_slots=[invalid_slot],
        )
        rows = [
            (
                (slot + timedelta(hours=8)).replace(tzinfo=None),
                100,
                20,
                25600,
                7,
                3,
            )
            for slot in (valid_slot, invalid_slot)
        ]
        manifest, output, _ = self.run_candidate(fixture, rows)

        self.assertEqual(manifest["summary"]["generated_metric_count"], 10)
        self.assertEqual(manifest["summary"]["feature_source_available_slot_count"], 1)
        self.assertEqual(manifest["summary"]["feature_invalid_source_slot_count"], 1)
        self.assertEqual(
            manifest["summary"]["feature_rows_on_invalid_source_slot_ignored_count"],
            1,
        )
        self.assertEqual(manifest["summary"]["feature_observed_slot_count"], 1)
        self.assertEqual(manifest["summary"]["metric_blockers"], {})
        for metric_name in metric_candidate_module.FEATURE_METRICS:
            self.assertEqual(
                manifest["summary"]["metric_limitations"][metric_name],
                {
                    "d3_invalid_update_artifact_slot_parse_failed": 1,
                    "feature_row_on_invalid_update_artifact_ignored": 1,
                },
            )
        points = load_series(output)["bgp_announce_record_count"]["points"]
        self.assertEqual(points[0]["value"], 7)
        self.assertEqual(points[0]["value_state"], "observed_nonzero")
        self.assertIsNone(points[1]["value"])
        self.assertEqual(points[1]["value_state"], "parse_failed")
        self.assertEqual(points[1]["missing_reason"], "parse_failed")
        self.assertEqual(
            manifest["admission"]["blocking_reasons"],
            ["fixture_sample_not_admissible"],
        )

    def test_multiple_empty_invalid_slots_share_sha_without_breaking_metric_closure(self):
        valid_slot = SAMPLE_START
        invalid_slots = [
            SAMPLE_START + timedelta(minutes=5),
            SAMPLE_START + timedelta(minutes=10),
        ]
        fixture = CandidateFixture(
            self.base / "inputs",
            self.profile,
            [valid_slot],
            [],
            invalid_slots=invalid_slots,
        )
        rows = [
            (
                (slot + timedelta(hours=8)).replace(tzinfo=None),
                100,
                20,
                25600,
                7,
                3,
            )
            for slot in [valid_slot, *invalid_slots]
        ]
        manifest, output, _ = self.run_candidate(
            fixture,
            rows,
            sample_window_end_exclusive=utc_text(
                SAMPLE_START + timedelta(minutes=15)
            ),
        )
        self.assertEqual(manifest["summary"]["generated_metric_count"], 10)
        self.assertEqual(manifest["summary"]["feature_invalid_source_slot_count"], 2)
        self.assertEqual(
            manifest["summary"]["feature_rows_on_invalid_source_slot_ignored_count"],
            2,
        )
        self.assertEqual(
            manifest["sources"]["d3_artifacts"]["invalid_in_window_reason_counts"],
            {"empty_file": 2},
        )
        points = load_series(output)["bgp_announce_record_count"]["points"]
        self.assertEqual(
            [(point["value"], point["value_state"]) for point in points],
            [
                (7, "observed_nonzero"),
                (None, "parse_failed"),
                (None, "parse_failed"),
            ],
        )

    def test_unknown_extra_feature_row_still_blocks_feature_metrics(self):
        valid_slot = SAMPLE_START
        unknown_slot = SAMPLE_START + timedelta(minutes=5)
        fixture = CandidateFixture(
            self.base / "inputs", self.profile, [valid_slot], []
        )
        rows = [
            (
                (slot + timedelta(hours=8)).replace(tzinfo=None),
                100,
                20,
                25600,
                7,
                3,
            )
            for slot in (valid_slot, unknown_slot)
        ]
        manifest, output, _ = self.run_candidate(fixture, rows)
        self.assertEqual(manifest["summary"]["generated_metric_count"], 3)
        self.assertEqual(
            set(manifest["summary"]["missing_metric_names"]),
            set(metric_candidate_module.FEATURE_METRICS),
        )
        for metric_name in metric_candidate_module.FEATURE_METRICS:
            self.assertEqual(
                manifest["summary"]["metric_blockers"][metric_name][
                    "feature_rows_without_verified_update_artifact"
                ],
                1,
            )
        self.assertIn(
            "metric_specific_data_blockers",
            manifest["admission"]["blocking_reasons"],
        )
        self.assertNotIn(
            "bgp_announce_record_count", load_series(output)
        )

    def test_self_consistent_unknown_invalid_reason_is_rejected_before_query(self):
        invalid_slot = SAMPLE_START + timedelta(minutes=5)
        fixture = CandidateFixture(
            self.base / "inputs",
            self.profile,
            [SAMPLE_START],
            [],
            invalid_slots=[invalid_slot],
        )
        manifest = json.loads(fixture.d3_manifest.read_text(encoding="utf-8"))
        manifest["invalid_in_window"][0]["missing_reason"] = "unknown_reason"
        fingerprint_payload = dict(manifest)
        fingerprint_payload.pop("manifest_fingerprint_sha256")
        manifest["manifest_fingerprint_sha256"] = _canonical_sha256(
            {
                "schema": ARTIFACT_FINGERPRINT_SCHEMA,
                "manifest": fingerprint_payload,
            }
        )
        write_json(fixture.d3_manifest, manifest)
        summary = json.loads(fixture.d3_summary.read_text(encoding="utf-8"))
        summary["manifest"]["sha256"] = sha256(fixture.d3_manifest)
        summary["manifest"]["fingerprint_sha256"] = manifest[
            "manifest_fingerprint_sha256"
        ]
        write_json(fixture.d3_summary, summary)
        fixture.d3_checksums.write_text(
            "{}  {}\n{}  {}\n".format(
                sha256(fixture.d3_manifest),
                fixture.d3_manifest.name,
                sha256(fixture.d3_summary),
                fixture.d3_summary.name,
            ),
            encoding="utf-8",
        )
        connection = RecordingConnection([])
        with self.assertRaisesRegex(MetricCandidateError, "missing_reason"):
            self.run_candidate(fixture, [], connection=connection)
        self.assertEqual(connection.executed, [])

    def test_self_consistent_forged_directory_scope_is_rejected_before_query(self):
        fixture = CandidateFixture(
            self.base / "inputs", self.profile, [SAMPLE_START], []
        )
        manifest = json.loads(fixture.d3_manifest.read_text(encoding="utf-8"))
        forged_scope = manifest["scan_policy"]["directory_scope"]
        forged_scope["included_month_directories"].append("2026.04")
        fingerprint_payload = dict(manifest)
        fingerprint_payload.pop("manifest_fingerprint_sha256")
        manifest["manifest_fingerprint_sha256"] = _canonical_sha256(
            {
                "schema": ARTIFACT_FINGERPRINT_SCHEMA,
                "manifest": fingerprint_payload,
            }
        )
        write_json(fixture.d3_manifest, manifest)
        summary = json.loads(fixture.d3_summary.read_text(encoding="utf-8"))
        summary["directory_scope"] = forged_scope
        summary["manifest"]["sha256"] = sha256(fixture.d3_manifest)
        summary["manifest"]["fingerprint_sha256"] = manifest[
            "manifest_fingerprint_sha256"
        ]
        write_json(fixture.d3_summary, summary)
        fixture.d3_checksums.write_text(
            "{}  {}\n{}  {}\n".format(
                sha256(fixture.d3_manifest),
                fixture.d3_manifest.name,
                sha256(fixture.d3_summary),
                fixture.d3_summary.name,
            ),
            encoding="utf-8",
        )
        connection = RecordingConnection([])
        with self.assertRaisesRegex(MetricCandidateError, "directory_scope"):
            self.run_candidate(fixture, [], connection=connection)
        self.assertEqual(connection.executed, [])

    def test_full_frozen_window_has_10271_valid_one_invalid_and_six_processing_gaps(self):
        source_start = datetime(2026, 2, 24, 0, 0, tzinfo=UTC)
        window_end = datetime(2026, 3, 31, 16, 0, tzinfo=UTC)
        frozen_gap_start = datetime(2026, 3, 30, 23, 30, tzinfo=UTC)
        invalid_slot = datetime(2026, 3, 30, 20, 45, tzinfo=UTC)
        slots = []
        rows = []
        cursor = source_start
        while cursor < window_end:
            if cursor != invalid_slot:
                slots.append(cursor)
            if not frozen_gap_start <= cursor < frozen_gap_start + timedelta(minutes=30):
                rows.append(
                    (
                        (cursor + timedelta(hours=8)).replace(tzinfo=None),
                        100,
                        20,
                        25600,
                        7,
                        3,
                    )
                )
            cursor += timedelta(minutes=5)
        fixture = CandidateFixture(
            self.base / "inputs",
            self.profile,
            slots,
            [],
            invalid_slots=[invalid_slot],
        )
        manifest, output, _ = self.run_candidate(
            fixture,
            rows,
            sample_window_start=None,
            sample_window_end_exclusive=None,
        )
        self.assertEqual(manifest["summary"]["feature_source_available_slot_count"], 10271)
        self.assertEqual(manifest["summary"]["feature_invalid_source_slot_count"], 1)
        self.assertEqual(manifest["summary"]["feature_observed_slot_count"], 10265)
        self.assertEqual(
            manifest["summary"]["feature_rows_on_invalid_source_slot_ignored_count"],
            1,
        )
        self.assertEqual(manifest["summary"]["feature_processing_gap_slot_count"], 6)
        self.assertEqual(manifest["summary"]["feature_processing_gap_slots"], [
            utc_text(slot) for slot in KNOWN_PROCESSING_GAPS_UTC
        ])
        self.assertTrue(manifest["admission"]["eligible_for_release_gate"])
        announce = load_series(output)["bgp_announce_record_count"]
        states = Counter(point["value_state"] for point in announce["points"])
        self.assertEqual(
            states,
            Counter(
                {
                    "source_unavailable": 6720,
                    "observed_nonzero": 10265,
                    "processing_gap": 6,
                    "parse_failed": 1,
                }
            ),
        )

    def test_d3_manifest_tampering_is_rejected_before_database_or_output(self):
        fixture = CandidateFixture(
            self.base / "inputs", self.profile, [SAMPLE_START], []
        )
        fixture.d3_manifest.write_text(
            fixture.d3_manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        connection = RecordingConnection([])
        output = self.base / "output"
        with self.assertRaisesRegex(MetricCandidateError, "SHA256"):
            generate_metric_candidate(
                connection,
                profile=self.profile,
                context=context_fixture(),
                database_config={
                    "DOMEYE_CORE_DB_READER_USER": "domeye_core_reader",
                    "DOMEYE_CORE_DB_NAME": "bgp_project",
                },
                provenance={},
                project_root=ROOT,
                pipeline_root=ROOT,
                d2_candidate_dir=fixture.d2,
                d3_manifest_path=fixture.d3_manifest,
                d3_summary_path=fixture.d3_summary,
                d3_checksum_path=fixture.d3_checksums,
                output_dir=output,
                generated_at="2026-07-20T12:30:00Z",
                sample_window_start=utc_text(SAMPLE_START),
                sample_window_end_exclusive=utc_text(SAMPLE_START + timedelta(minutes=5)),
                security_verifier=security_fixture,
            )
        self.assertEqual(connection.executed, [])
        self.assertFalse(output.exists())

    def test_database_failure_rolls_back_and_removes_partial_staging(self):
        fixture = CandidateFixture(
            self.base / "inputs", self.profile, [SAMPLE_START], []
        )
        bad_rows = [
            (
                (SAMPLE_START + timedelta(hours=8)).replace(tzinfo=None),
                None,
                20,
                25600,
                7,
                3,
            )
        ]
        connection = RecordingConnection(bad_rows)
        output = self.base / "output"
        with self.assertRaisesRegex(MetricCandidateError, "非负非空整数"):
            self.run_candidate(
                fixture,
                bad_rows,
                connection=connection,
                sample_window_end_exclusive=utc_text(SAMPLE_START + timedelta(minutes=5)),
            )
        self.assertGreaterEqual(connection.rollback_count, 1)
        self.assertFalse(output.exists())
        self.assertEqual(
            [path.name for path in self.base.iterdir() if ".tmp." in path.name], []
        )

    def test_missing_normalized_end_time_emits_legacy_unknown_without_fabrication(self):
        slots = [SAMPLE_START, SAMPLE_START + timedelta(minutes=5)]
        records = [
            incident(
                "0" * 23 + "1",
                "prefix_outage",
                SAMPLE_START,
                object_type="prefix",
                object_id="10.0.0.0/24",
            ),
            incident(
                "0" * 23 + "2",
                "as_outage",
                SAMPLE_START,
                object_type="asn",
                object_id="4134",
            ),
        ]
        fixture = CandidateFixture(self.base / "inputs", self.profile, slots, records)
        rows = [
            (
                (slot + timedelta(hours=8)).replace(tzinfo=None),
                100,
                20,
                25600,
                7,
                3,
            )
            for slot in slots
        ]
        manifest, output, _ = self.run_candidate(fixture, rows)
        self.assertEqual(manifest["summary"]["generated_metric_count"], 10)
        self.assertEqual(manifest["summary"]["missing_metric_names"], [])
        self.assertEqual(
            set(manifest["summary"]["not_fully_computable_metric_names"]),
            {"prefix_outage_concurrent_count", "as_outage_concurrent_count"},
        )
        for metric_name in manifest["summary"]["not_fully_computable_metric_names"]:
            self.assertEqual(
                manifest["summary"]["metric_limitations"][metric_name][
                    "end_time_explicitly_unavailable"
                ],
                1,
            )
        series = load_series(output)
        for metric_name in manifest["summary"]["not_fully_computable_metric_names"]:
            self.assertEqual(
                [point["value_state"] for point in series[metric_name]["points"]],
                ["legacy_unknown", "legacy_unknown"],
            )
            self.assertTrue(
                all(point["value"] is None for point in series[metric_name]["points"])
            )
        reconciliation = json.loads(
            (output / RECONCILIATION_FILE).read_text(encoding="utf-8")
        )
        self.assertEqual(reconciliation["legacy_unknown_point_count"], 4)
        self.assertEqual(reconciliation["unknown_missing_reason_count"], 0)
        self.assertEqual(reconciliation["confirmed_missing_zero_fill_count"], 0)
        self.assertEqual(reconciliation["reconciliation_difference_count"], 0)
        self.assertEqual(manifest["admission"]["blocking_reasons"], [
            "fixture_sample_not_admissible"
        ])

    def test_expanded_native_asn_set_counts_each_known_member(self):
        slots = [SAMPLE_START, SAMPLE_START + timedelta(minutes=5)]
        record = incident(
            "0" * 23 + "1",
            "as_outage",
            SAMPLE_START,
            end_time=SAMPLE_START + timedelta(minutes=9),
            object_type="asn",
            object_id="36040",
        )
        record["affected_objects"].append(
            {
                "object_type": "asn",
                "object_id": "211612",
                "role": "affected",
                "source_field": "detail_url.problem",
            }
        )
        fixture = CandidateFixture(self.base / "inputs", self.profile, slots, [record])
        rows = [
            (
                (slot + timedelta(hours=8)).replace(tzinfo=None),
                100,
                20,
                25600,
                7,
                3,
            )
            for slot in slots
        ]
        manifest, output, _ = self.run_candidate(fixture, rows)
        series = load_series(output)["as_outage_concurrent_count"]
        self.assertEqual([point["value"] for point in series["points"]], [2, 2])
        self.assertNotIn(
            "as_outage_concurrent_count", manifest["summary"]["metric_limitations"]
        )
        self.assertEqual(
            manifest["summary"]["legacy_unknown_point_count_by_metric"].get(
                "as_outage_concurrent_count", 0
            ),
            0,
        )

    def test_legacy_unknown_only_starts_at_the_first_affected_concurrency_sample(self):
        slots = [SAMPLE_START + timedelta(minutes=5 * index) for index in range(3)]
        records = [
            incident(
                "0" * 23 + "1",
                "prefix_outage",
                SAMPLE_START + timedelta(minutes=6),
                object_type="prefix",
                object_id="10.0.0.0/24",
            )
        ]
        fixture = CandidateFixture(self.base / "inputs", self.profile, slots, records)
        rows = [
            (
                (slot + timedelta(hours=8)).replace(tzinfo=None),
                100,
                20,
                25600,
                7,
                3,
            )
            for slot in slots
        ]
        _, output, _ = self.run_candidate(
            fixture,
            rows,
            sample_window_end_exclusive=utc_text(SAMPLE_START + timedelta(minutes=15)),
        )
        points = load_series(output)["prefix_outage_concurrent_count"]["points"]
        self.assertEqual(
            [(point["value"], point["value_state"]) for point in points],
            [
                (0, "observed_zero"),
                (None, "legacy_unknown"),
                (None, "legacy_unknown"),
            ],
        )

    def test_duplicate_stable_id_is_deduplicated_and_blocks_admission(self):
        slots = [SAMPLE_START, SAMPLE_START + timedelta(minutes=5)]
        record = incident("0" * 23 + "1", "hijack", SAMPLE_START)
        fixture = CandidateFixture(self.base / "inputs", self.profile, slots, [record, record])
        rows = [
            (
                (slot + timedelta(hours=8)).replace(tzinfo=None),
                100,
                20,
                25600,
                7,
                3,
            )
            for slot in slots
        ]
        manifest, output, _ = self.run_candidate(fixture, rows)
        anomaly = load_series(output)["anomaly_incident_count"]
        self.assertEqual(anomaly["points"][0]["value"], 1)
        self.assertEqual(manifest["summary"]["unique_incident_count"], 1)
        self.assertEqual(manifest["summary"]["duplicate_identical_count"], 1)
        self.assertEqual(
            manifest["summary"]["metric_blockers"]["anomaly_incident_count"][
                "duplicate_incident_ids"
            ],
            1,
        )

    def test_frozen_processing_gap_constant_is_exactly_six_aligned_slots(self):
        self.assertEqual(len(KNOWN_PROCESSING_GAPS_UTC), 6)
        self.assertEqual(
            KNOWN_PROCESSING_GAPS_UTC,
            tuple(
                datetime(2026, 3, 30, 23, 30, tzinfo=UTC)
                + timedelta(minutes=5 * index)
                for index in range(6)
            ),
        )


if __name__ == "__main__":
    unittest.main()
