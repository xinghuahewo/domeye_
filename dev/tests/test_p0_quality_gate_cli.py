from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import bz2
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from backend.data_pipeline import normalize as normalizer
from backend.data_pipeline.route_event import scan_mrt_artifacts, verify_artifact_manifest
from dev.data_quality import p0_quality_gate as cli
from dev.tests.test_p0_quality_gate import (
    assert_schema_valid,
    evidence_summary,
    metric_summary,
    reproducibility_summary,
    single_run_assurance_context_and_summary,
)


ROOT = Path(__file__).resolve().parents[2]
PROFILE = json.loads((ROOT / "config/data-profile.json").read_text(encoding="utf-8"))
GIT_SHA = "b" * 40


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
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_checksums(directory, names):
    path = Path(directory) / "SHA256SUMS"
    path.write_text(
        "".join(f"{sha256(Path(directory) / name)}  {name}\n" for name in sorted(names)),
        encoding="utf-8",
    )
    return path


def refingerprint_summary(value, schema):
    payload = dict(value)
    payload.pop("summary_fingerprint_sha256", None)
    value["summary_fingerprint_sha256"] = hashlib.sha256(
        canonical_bytes({"schema": schema, "summary": payload})[:-1]
    ).hexdigest()
    return value


def closure(directory):
    checksum_path = Path(directory) / "SHA256SUMS"
    index = cli._checksum_index(checksum_path, "fixture SHA256SUMS")
    return {
        "sha256sums_sha256": sha256(checksum_path),
        "signed_file_count": len(index),
        "signed_size_bytes": sum((Path(directory) / name).stat().st_size for name in index),
        "verified": True,
    }


def gzip_rows(rows):
    buffer = io.BytesIO()
    content = hashlib.sha256()
    with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=buffer, mtime=0) as stream:
        for row in rows:
            line = canonical_bytes(row)
            content.update(line)
            stream.write(line)
    payload = buffer.getvalue()
    return payload, content.hexdigest()


def inventory(name, rows, payload, content_sha):
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
        "row_count": len(rows),
        "content_sha256": content_sha,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def incident_fixture(event_type, minute):
    local = f"2026-02-01 00:{minute:02d}:00"
    source = "r"
    event_id = minute + 1
    definitions = {
        "hijack": ("10.0.0.0-24", {"prefix": "10.0.0.0/24", "hijack_eventid": event_id, "hijack_level": "high"}),
        "sub_hijack": ("10.0.1.0-24", {"prefix": "10.0.1.0/24", "sub_hijack_eventid": event_id, "sub_hijack_level": "high"}),
        "leak": ("10.0.2.0-24", {"prefix": "10.0.2.0/24", "leak_event_id": event_id, "leak_level": "high"}),
        "prefix_outage": ("10.0.3.0-24", {"prefix": "10.0.3.0/24", "outage_id": event_id, "asn": "64503", "outage_level": "high"}),
        "as_outage": ("64504", {"asn": "64504", "outage_id": event_id, "outage_level": "high"}),
        "country_outage": ("US", {"country": "US", "outage_id": event_id, "outage_level": "high"}),
    }
    problem, fact = definitions[event_type]
    detail = f"{event_type}/{local}/{problem}/{event_id}/{source}"
    source_table = f"{cli.FACT_FAMILIES[event_type]}_202602"
    fact.update(
        {
            "source": source,
            "source_table": source_table,
            "s_time": f"2026-01-31T16:{minute:02d}:00Z",
            "e_time": None,
            "duration": None,
        }
    )
    incident = normalizer.normalize_event(
        {"detail_url": detail, "event_type": event_type, "source": source},
        fact,
        {"fact_link_status": "matched", "source_table": source_table},
    )
    link = {
        "incident_id": incident["incident_id"],
        "detail_reference": detail,
        "event_type": event_type,
        "source_table": source_table,
        "status": "matched",
        "matched_source_primary_key": incident["source_primary_key"],
        "candidate_source_primary_keys": [incident["source_primary_key"]],
        "locator_risks": [],
        "unresolved_reasons": [],
        "collision_group_id": None,
        "classification": "observation_only",
        "causal_conclusion": None,
    }
    return incident, link


def candidate_fingerprint(manifest):
    source = manifest["source"]
    payload = {
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
    return hashlib.sha256(canonical_bytes(payload)[:-1]).hexdigest()


class QualityGateCliFixture:
    def __init__(self, base):
        self.base = Path(base)
        self.profile = self.base / "data-profile.json"
        self.profile.write_bytes(canonical_bytes(PROFILE))
        self.d2_dir = self.base / "d2"
        self.d2_dir.mkdir()
        self.d3_dir = self.base / "d3"
        self.d3_dir.mkdir()
        self.aux_dir = self.base / "aux"
        self.aux_dir.mkdir()
        self.execution_dir = self.base / "execution"
        self.execution_dir.mkdir()
        self.raw_root = self.base / "raw"
        self.raw_dir = self.raw_root / "rrc25" / "2026.01"
        self.raw_dir.mkdir(parents=True)
        (self.raw_dir / "updates.20260131.1600.gz").write_bytes(gzip.compress(b"update", mtime=0))
        (self.raw_dir / "bview.20260131.1600.bz2").write_bytes(bz2.compress(b"rib"))
        self.incidents = []
        self.links = []
        for minute, event_type in enumerate(cli.EVENT_TYPES):
            incident, link = incident_fixture(event_type, minute)
            self.incidents.append(incident)
            self.links.append(link)
        self.collisions = [
            normalizer.build_collision_group(
                source_table="hijack_202602",
                source_primary_key={
                    "source": "r",
                    "prefix": "10.0.0.0/24",
                    "hijack_eventid": 1,
                },
                incident_ids=[self.incidents[0]["incident_id"], self.incidents[1]["incident_id"]],
            )
        ]
        self.quarantine = [
            normalizer.build_quarantine_record(
                source_table="country_outage_202603",
                source_primary_key={"source": "r", "country": "", "outage_id": 1},
                reasons=("invalid_identity", "legacy_window_contamination"),
                record_kind="fact_record",
                legacy_payload={"country": "", "outage_id": 1, "source": "r"},
            )
        ]
        self.write_d2()
        self.write_d3()
        self.write_auxiliary()
        self.write_execution()

    def write_d2(self):
        rows_by_name = {
            "incidents.jsonl.gz": self.incidents,
            "links.jsonl.gz": self.links,
            "collision_groups.jsonl.gz": self.collisions,
            "quarantine.jsonl.gz": self.quarantine,
        }
        files = {}
        for name, rows in rows_by_name.items():
            payload, content_sha = gzip_rows(rows)
            (self.d2_dir / name).write_bytes(payload)
            files[name] = inventory(name, rows, payload, content_sha)
        summary = {
            "incident_count": len(self.incidents),
            "link_count": len(self.links),
            "collision_group_count": len(self.collisions),
            "collision_incident_count": sum(len(row["incident_ids"]) for row in self.collisions),
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
            "quarantine_count": len(self.quarantine),
            "fact_link_status_counts": {"matched": len(self.links)},
            "event_type_counts": {event_type: 1 for event_type in cli.EVENT_TYPES},
            "quarantine_reason_counts": {
                "invalid_identity": 1,
                "legacy_window_contamination": 1,
            },
        }
        manifest = {
            "schema_version": "p0_normalization_candidate_v1",
            "candidate_kind": "readonly_legacy_fact_normalization",
            "candidate_fingerprint_sha256": "0" * 64,
            "data_profile": deepcopy(PROFILE),
            "window_utc": {
                "start": "2026-01-31T16:00:00Z",
                "end_exclusive": "2026-03-31T16:00:00Z",
            },
            "source": {
                "release_id": "quality-cli-fixture-r1",
                "state_sha256": "1" * 64,
                "manifest_sha256": "2" * 64,
                "database_manifest_sha256": "3" * 64,
                "inventory_sha256": "4" * 64,
                "database": {
                    "host": "127.0.0.1",
                    "port": 31627,
                    "name": "bgp_project",
                    "system_identifier": "7663836852697006116",
                    "transaction_read_only": True,
                    "transaction_isolation": "repeatable read",
                },
                "provenance": {
                    "git_sha": GIT_SHA,
                    "probe_sha256": sha256(ROOT / cli.D2_RUNNER_RELATIVE_PATH),
                    "data_profile_sha256": sha256(self.profile),
                },
                "normalizer_hashes": {
                    "backend/data_pipeline/normalize/__init__.py": sha256(
                        ROOT / "backend/data_pipeline/normalize/__init__.py"
                    ),
                    "backend/data_pipeline/normalize/facts.py": sha256(
                        ROOT / "backend/data_pipeline/normalize/facts.py"
                    ),
                },
            },
            "source_table_counts": {},
            "files": files,
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
        manifest["candidate_fingerprint_sha256"] = candidate_fingerprint(manifest)
        self.d2_manifest = self.d2_dir / "manifest.json"
        self.d2_manifest.write_bytes(canonical_bytes(manifest))
        (self.d2_dir / "摘要.md").write_text("fixture\n", encoding="utf-8")
        self.d2_checksums = write_checksums(
            self.d2_dir,
            [*rows_by_name, "manifest.json", "摘要.md"],
        )

    def write_d3(self):
        manifest = scan_mrt_artifacts(self.raw_root, PROFILE, ["rrc25"])
        verification = verify_artifact_manifest(self.raw_root, manifest)
        self.d3_manifest = self.d3_dir / "p0-artifact-manifest.json"
        self.d3_manifest.write_bytes(canonical_bytes(manifest))
        manifest_sha = sha256(self.d3_manifest)
        summary = {
            "schema_version": 1,
            "summary_kind": "p0_raw_artifact_manifest_summary_zh",
            "manifest": {
                "file_name": self.d3_manifest.name,
                "sha256": manifest_sha,
                "fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
            },
            "provenance": {
                "data_profile": {
                    "file_name": self.profile.name,
                    "sha256": sha256(self.profile),
                },
                "scanner": {
                    "relative_path": cli.D3_SCANNER_RELATIVE_PATH.as_posix(),
                    "sha256": sha256(ROOT / cli.D3_SCANNER_RELATIVE_PATH),
                    "module_path_verified": True,
                },
                "cli": {
                    "relative_path": cli.D3_CLI_RELATIVE_PATH.as_posix(),
                    "sha256": sha256(ROOT / cli.D3_CLI_RELATIVE_PATH),
                },
            },
            "verification": verification,
        }
        self.d3_summary = self.d3_dir / "p0-artifact-manifest.summary.zh.json"
        self.d3_summary.write_bytes(canonical_bytes(summary))
        self.d3_checksums = write_checksums(
            self.d3_dir, [self.d3_manifest.name, self.d3_summary.name]
        )
        self.d3 = manifest

    def write_auxiliary(self):
        route = {
            "schema_version": "route_event_index_summary_v1",
            "manifest_fingerprint_sha256": self.d3["manifest_fingerprint_sha256"],
            "route_event_count": 0,
            "lineage_status": "legacy_untraceable",
            "raw_reference_unresolved_count": 0,
            "processing_lineage_missing_count": 0,
            "record_hash_verification_failed_count": 0,
            "vp_identity_missing_count": 0,
            "route_event_id_conflict_count": 0,
            "invalid_asn_count": 0,
            "invalid_prefix_count": 0,
            "outside_window_record_count": 0,
        }
        payloads = {
            "route.json": route,
            "evidence.json": evidence_summary(),
            "metric.json": metric_summary(),
            "repro.json": reproducibility_summary(with_route=True),
        }
        for name, value in payloads.items():
            (self.aux_dir / name).write_bytes(canonical_bytes(value))
        self.aux_checksums = write_checksums(self.aux_dir, payloads)

    def write_execution(self, **overrides):
        value = {
            "schema_version": "p0_quality_gate_execution_v1",
            "git_sha": GIT_SHA,
            "probe_fingerprint_sha256": "c" * 64,
            "started_at": "2026-07-21T01:00:00Z",
            "finished_at": "2026-07-21T01:00:01Z",
            "generated_at": "2026-07-21T01:00:02Z",
            "database_access": "none",
            "database_connection_attempts": 0,
            "database_write_operations": 0,
        }
        value.update(overrides)
        self.execution = self.execution_dir / "execution.json"
        self.execution.write_bytes(canonical_bytes(value))
        self.execution_checksums = write_checksums(self.execution_dir, [self.execution.name])

    def arguments(self, output, *, include_aux=True):
        result = [
            "--data-profile", str(self.profile),
            "--d2-manifest", str(self.d2_manifest),
            "--d2-checksums", str(self.d2_checksums),
            "--d3-manifest", str(self.d3_manifest),
            "--d3-verification-summary", str(self.d3_summary),
            "--d3-checksums", str(self.d3_checksums),
            "--execution-context", str(self.execution),
            "--execution-checksums", str(self.execution_checksums),
            "--pipeline-root", str(ROOT),
            "--output-dir", str(output),
        ]
        if include_aux:
            for option, name in (
                ("route", "route.json"),
                ("evidence", "evidence.json"),
                ("metric", "metric.json"),
                ("reproducibility", "repro.json"),
            ):
                result.extend(
                    [
                        f"--{option}-summary",
                        str(self.aux_dir / name),
                        f"--{option}-checksums",
                        str(self.aux_checksums),
                    ]
                )
        return result

    def single_run_arguments(self, output):
        d2 = json.loads(self.d2_manifest.read_text(encoding="utf-8"))
        d2_manifest_sha = sha256(self.d2_manifest)
        d2_index = cli._checksum_index(self.d2_checksums, "D2 fixture")
        d3_manifest_sha = sha256(self.d3_manifest)
        d3_summary_sha = sha256(self.d3_summary)

        route_dir = self.base / "route-v1"
        evidence_dir = self.base / "evidence-v1"
        metric_dir = self.base / "metric-v1"
        assurance_dir = self.base / "assurance-v1"
        for directory in (route_dir, evidence_dir, metric_dir, assurance_dir):
            directory.mkdir()

        route = {
            "schema_version": "route_event_index_summary_v1",
            "manifest_fingerprint_sha256": self.d3["manifest_fingerprint_sha256"],
            "index_fingerprint_sha256": "9" * 64,
            "build_scope": {
                "data_profile": {
                    key: PROFILE[key]
                    for key in ("id", "timezone", "window_start", "window_end_exclusive")
                }
            },
            "route_event_count": 0,
            "lineage_status": "legacy_untraceable",
            "raw_reference_unresolved_count": 0,
            "processing_lineage_missing_count": 0,
            "record_hash_verification_failed_count": 0,
            "vp_identity_missing_count": 0,
            "route_event_id_conflict_count": 0,
            "invalid_asn_count": 0,
            "invalid_prefix_count": 0,
            "outside_window_record_count": 0,
        }
        route_path = route_dir / "route-event-reconciliation-summary.json"
        route_path.write_bytes(canonical_bytes(route))
        route_checksums = write_checksums(route_dir, [route_path.name])

        evidence = refingerprint_summary(
            evidence_summary(), "evidence_reconciliation_fingerprint_v1"
        )
        evidence_path = evidence_dir / "evidence-reconciliation-summary.json"
        evidence_path.write_bytes(canonical_bytes(evidence))
        evidence_manifest = {
            "candidate_fingerprint_sha256": "a" * 64,
            "data_profile": deepcopy(PROFILE),
            "inputs": {
                "d2": {
                    "candidate_fingerprint_sha256": d2[
                        "candidate_fingerprint_sha256"
                    ],
                    "manifest_sha256": d2_manifest_sha,
                },
                "d3_artifacts": {
                    "manifest_fingerprint_sha256": self.d3[
                        "manifest_fingerprint_sha256"
                    ],
                    "manifest_sha256": d3_manifest_sha,
                    "summary_sha256": d3_summary_sha,
                },
            },
        }
        evidence_manifest_path = evidence_dir / "manifest.json"
        evidence_manifest_path.write_bytes(canonical_bytes(evidence_manifest))
        evidence_checksums = write_checksums(
            evidence_dir, [evidence_path.name, evidence_manifest_path.name]
        )

        metric = refingerprint_summary(
            metric_summary(), "metric_reconciliation_summary_fingerprint_v1"
        )
        metric_path = metric_dir / "metric-reconciliation-summary.json"
        metric_path.write_bytes(canonical_bytes(metric))
        metric_manifest = {
            "candidate_fingerprint_sha256": "b" * 64,
            "data_profile": deepcopy(PROFILE),
            "sources": {
                "d2_normalization": {
                    "fingerprint_sha256": d2["candidate_fingerprint_sha256"],
                    "manifest_sha256": d2_manifest_sha,
                    "checksums_sha256": sha256(self.d2_checksums),
                    "incidents_sha256": d2_index["incidents.jsonl.gz"],
                },
                "d3_artifacts": {
                    "fingerprint_sha256": self.d3["manifest_fingerprint_sha256"],
                    "manifest_sha256": d3_manifest_sha,
                    "summary_sha256": d3_summary_sha,
                    "checksums_sha256": sha256(self.d3_checksums),
                },
            },
        }
        metric_manifest_path = metric_dir / "manifest.json"
        metric_manifest_path.write_bytes(canonical_bytes(metric_manifest))
        metric_checksums = write_checksums(
            metric_dir, [metric_path.name, metric_manifest_path.name]
        )

        actual = cli._build_assurance_context(
            d2=d2,
            d2_manifest_sha=d2_manifest_sha,
            d2_checksums=d2_index,
            d2_closure=closure(self.d2_dir),
            d3=self.d3,
            d3_manifest_sha=d3_manifest_sha,
            d3_summary_sha=d3_summary_sha,
            d3_closure=closure(self.d3_dir),
            d4=evidence_manifest,
            d4_manifest_sha=sha256(evidence_manifest_path),
            d4_summary=evidence,
            d4_closure=closure(evidence_dir),
            metric_manifest=metric_manifest,
            metric_manifest_sha=sha256(metric_manifest_path),
            metric_summary=metric,
            metric_closure=closure(metric_dir),
            route_summary=route,
            route_summary_sha=sha256(route_path),
            route_closure=closure(route_dir),
        )
        _, assurance = single_run_assurance_context_and_summary()
        assurance["final_candidate_integrity"]["components"] = actual[
            "final_candidate_integrity"
        ]
        assurance["final_candidate_identity"] = actual["final_candidate_identity"]
        assurance["cross_artifact_binding"]["checks"] = actual[
            "cross_artifact_binding"
        ]
        assurance_path = assurance_dir / "assurance-summary.json"
        assurance_path.write_bytes(canonical_bytes(assurance))
        (assurance_dir / "摘要.md").write_text("fixture assurance\n", encoding="utf-8")
        assurance_checksums = write_checksums(
            assurance_dir, [assurance_path.name, "摘要.md"]
        )

        result = self.arguments(output, include_aux=False)
        result.extend(
            [
                "--route-summary", str(route_path),
                "--route-checksums", str(route_checksums),
                "--evidence-summary", str(evidence_path),
                "--evidence-manifest", str(evidence_manifest_path),
                "--evidence-checksums", str(evidence_checksums),
                "--metric-summary", str(metric_path),
                "--metric-manifest", str(metric_manifest_path),
                "--metric-checksums", str(metric_checksums),
                "--reproducibility-summary", str(assurance_path),
                "--reproducibility-checksums", str(assurance_checksums),
            ]
        )
        return result


class QualityGateCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.fixture = QualityGateCliFixture(self.base)

    def tearDown(self):
        self.temporary.cleanup()

    def invoke(self, arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(arguments)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_partial_raw_and_explicit_zero_route_pass_legacy_with_schema_and_sha_closure(self):
        output = self.base / "quality"
        status, stdout, stderr = self.invoke(self.fixture.arguments(output))
        self.assertEqual((status, stderr), (0, ""))
        result = json.loads(stdout)
        report = json.loads((output / "data-quality-report.json").read_text(encoding="utf-8"))
        self.assertEqual(result["状态"], "passed")
        self.assertEqual(report["gate"]["admission_level"], "legacy_compatible")
        self.assertIn("raw-route-event-index", report["gate"]["warning_check_ids"])
        self.assertEqual(
            next(row for row in report["checks"] if row["check_id"] == "raw-route-event-index")["observed"]["value"],
            0,
        )
        audited = json.loads((output / "d2-candidate-manifest.json").read_text(encoding="utf-8"))
        for field in cli._load_gate_module(ROOT)[0].D2_REQUIRED_QUALITY_FIELDS:
            self.assertEqual(audited["summary"][field], 0)
        warning_details = [
            json.loads(line)
            for line in gzip.decompress((output / "失败明细.jsonl.gz").read_bytes())
            .decode("utf-8")
            .splitlines()
        ]
        self.assertTrue(warning_details)
        self.assertTrue(all(row["check_id"] in report["gate"]["warning_check_ids"] for row in warning_details))
        checksum_lines = (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        for line in checksum_lines:
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, sha256(output / name))
        assert_schema_valid(self, report)

    def test_single_run_assurance_uses_separate_closures_and_exact_final_bindings(self):
        output = self.base / "quality-single-run"
        status, stdout, stderr = self.invoke(
            self.fixture.single_run_arguments(output)
        )

        self.assertEqual((status, stderr), (0, ""))
        result = json.loads(stdout)
        report = json.loads(
            (output / "data-quality-report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(result["状态"], "passed")
        self.assertEqual(result["语义复核范围"], "d2_bounded_replay_64")
        self.assertEqual(result["全量语义复现"], "not_run")
        self.assertEqual(report["gate"]["admission_level"], "legacy_compatible")
        statuses = {row["check_id"]: row["status"] for row in report["checks"]}
        self.assertEqual(statuses["reproducibility-final-artifact-integrity"], "pass")
        self.assertEqual(statuses["reproducibility-final-identity-binding"], "pass")
        self.assertEqual(statuses["reproducibility-cross-artifact-binding"], "pass")
        self.assertEqual(statuses["reproducibility-cross-run-coverage"], "pending")
        self.assertEqual(statuses["reproducibility-full-semantic-validation"], "pending")
        assert_schema_valid(self, report)

    def test_single_run_assurance_rejects_unsigned_extra_candidate_file(self):
        output = self.base / "quality-single-run-extra"
        arguments = self.fixture.single_run_arguments(output)
        (self.base / "evidence-v1" / "unsigned.tmp").write_text(
            "not signed\n", encoding="utf-8"
        )

        status, _, stderr = self.invoke(arguments)

        self.assertEqual(status, 2)
        self.assertIn("文件集合不闭合", stderr)
        self.assertFalse(output.exists())

    def test_single_run_assurance_recomputes_reconciliation_fingerprint(self):
        output = self.base / "quality-single-run-fingerprint"
        arguments = self.fixture.single_run_arguments(output)
        evidence_dir = self.base / "evidence-v1"
        summary_path = evidence_dir / "evidence-reconciliation-summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["summary_fingerprint_sha256"] = "0" * 64
        summary_path.write_bytes(canonical_bytes(summary))
        write_checksums(
            evidence_dir,
            ["evidence-reconciliation-summary.json", "manifest.json"],
        )

        status, _, stderr = self.invoke(arguments)

        self.assertEqual(status, 2)
        self.assertIn("fingerprint 复算不一致", stderr)
        self.assertFalse(output.exists())

    def test_row_failures_are_counted_and_located_without_zero_guessing(self):
        row = self.fixture.incidents[0]
        row["affected_objects"].append(
            {"object_type": "asn", "object_id": "AS64500", "role": "affected", "source_field": "fixture"}
        )
        row["end_time_utc"] = "2026-01-31T15:59:59Z"
        row["phase_coverage"]["before"]["missing_reason"] = None
        row["duration_seconds"] = 0
        self.fixture.write_d2()
        output = self.base / "quality-failed"
        status, _, stderr = self.invoke(self.fixture.arguments(output))
        self.assertEqual((status, stderr), (1, ""))
        report = json.loads((output / "data-quality-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["gate"]["admission_level"], "not_accepted")
        details = [
            json.loads(line)
            for line in gzip.decompress((output / "失败明细.jsonl.gz").read_bytes()).decode("utf-8").splitlines()
        ]
        by_check = {row["check_id"]: row for row in details}
        self.assertEqual(by_check["completeness-entity-identities"]["source"], "incidents.jsonl.gz:1")
        self.assertEqual(by_check["time-end-before-start"]["field"], "end_time_utc")
        self.assertIn("phase-six-event-coverage", by_check)
        self.assertIn("missing-no-zero-fill", by_check)

    def test_same_input_bytes_produce_identical_output_bytes(self):
        first = self.base / "quality-a"
        second = self.base / "quality-b"
        self.assertEqual(self.invoke(self.fixture.arguments(first))[0], 0)
        self.assertEqual(self.invoke(self.fixture.arguments(second))[0], 0)
        first_files = sorted(path.name for path in first.iterdir())
        self.assertEqual(first_files, sorted(path.name for path in second.iterdir()))
        for name in first_files:
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)

    def test_tamper_and_database_write_evidence_fail_without_partial_output(self):
        self.fixture.d3_manifest.write_bytes(self.fixture.d3_manifest.read_bytes() + b" ")
        output = self.base / "tampered"
        status, _, stderr = self.invoke(self.fixture.arguments(output))
        self.assertEqual(status, 2)
        self.assertIn("SHA256SUMS", stderr)
        self.assertFalse(output.exists())
        self.assertFalse(any(path.name.startswith(".tampered.tmp") for path in self.base.iterdir()))

        self.fixture.write_d3()
        self.fixture.write_execution(database_write_operations=1)
        output = self.base / "unsafe"
        status, _, stderr = self.invoke(self.fixture.arguments(output))
        self.assertEqual(status, 2)
        self.assertIn("拒绝任何数据库连接或写操作", stderr)
        self.assertFalse(output.exists())

    def test_metric_summary_schema_hash_must_match_current_contract(self):
        metric_path = self.fixture.aux_dir / "metric.json"
        metric = json.loads(metric_path.read_text(encoding="utf-8"))
        metric["schema_sha256"] = "0" * 64
        metric_path.write_bytes(canonical_bytes(metric))
        self.fixture.aux_checksums = write_checksums(
            self.fixture.aux_dir,
            ["route.json", "evidence.json", "metric.json", "repro.json"],
        )
        output = self.base / "metric-schema-mismatch"
        status, _, stderr = self.invoke(self.fixture.arguments(output))
        self.assertEqual(status, 2)
        self.assertIn("MetricSeries Schema", stderr)
        self.assertFalse(output.exists())

    def test_declared_quality_count_must_match_rows_and_is_never_overwritten(self):
        manifest = json.loads(self.fixture.d2_manifest.read_text(encoding="utf-8"))
        manifest["summary"]["stable_id_conflict_count"] = 1
        manifest["candidate_fingerprint_sha256"] = candidate_fingerprint(manifest)
        self.fixture.d2_manifest.write_bytes(canonical_bytes(manifest))
        self.fixture.d2_checksums = write_checksums(
            self.fixture.d2_dir,
            [*cli.D2_JSONL_FILES, "manifest.json", "摘要.md"],
        )
        output = self.base / "declared-mismatch"
        status, _, stderr = self.invoke(self.fixture.arguments(output))
        self.assertEqual(status, 2)
        self.assertIn("summary.stable_id_conflict_count 与逐行审计不一致", stderr)
        self.assertFalse(output.exists())

    def test_missing_optional_summaries_fail_closed_but_keep_archivable_report(self):
        output = self.base / "missing"
        status, _, stderr = self.invoke(self.fixture.arguments(output, include_aux=False))
        self.assertEqual((status, stderr), (1, ""))
        report = json.loads((output / "data-quality-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["gate"]["admission_level"], "not_accepted")
        self.assertIn("completeness-evidence-contract", report["gate"]["blocking_failed_check_ids"])
        self.assertIn("completeness-metric-contract", report["gate"]["blocking_failed_check_ids"])

    def test_missing_route_alone_is_blocking_unknown_not_a_guessed_zero(self):
        output = self.base / "missing-route"
        arguments = self.fixture.arguments(output)
        route_index = arguments.index("--route-summary")
        del arguments[route_index : route_index + 4]
        status, _, stderr = self.invoke(arguments)
        self.assertEqual((status, stderr), (1, ""))
        report = json.loads((output / "data-quality-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["gate"]["admission_level"], "not_accepted")
        entity_check = next(
            row for row in report["checks"] if row["check_id"] == "completeness-entity-identities"
        )
        self.assertEqual(entity_check["status"], "fail")
        self.assertIsNone(entity_check["observed"]["value"])
        self.assertGreater(entity_check["unknown_count"], 0)
        self.assertFalse((output / "route-event-reconciliation-summary.json").exists())

    def test_symlink_input_is_rejected_and_existing_output_is_never_overwritten(self):
        linked = self.base / "linked-profile.json"
        linked.symlink_to(self.fixture.profile)
        arguments = self.fixture.arguments(self.base / "linked-output")
        arguments[arguments.index(str(self.fixture.profile))] = str(linked)
        status, _, stderr = self.invoke(arguments)
        self.assertEqual(status, 2)
        self.assertIn("符号链接", stderr)

        output = self.base / "owned"
        output.mkdir()
        marker = output / "owner.txt"
        marker.write_text("保留\n", encoding="utf-8")
        status, _, stderr = self.invoke(self.fixture.arguments(output))
        self.assertEqual(status, 2)
        self.assertIn("禁止覆盖", stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), "保留\n")


if __name__ == "__main__":
    unittest.main()
