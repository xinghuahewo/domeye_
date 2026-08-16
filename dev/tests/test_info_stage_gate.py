import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.info_pipeline.catalog import DATA_FILE_SPECS
from backend.info_pipeline.manifest import importer_config_sha256
from backend.info_pipeline.stage_gate import run_stage_gate


ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_checksums(directory: Path, names):
    lines = []
    for name in names:
        digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}\n")
    (directory / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def _manifest():
    return {
        "schema_version": 1,
        "component": "static_info",
        "parser_version": "info-parser-v5",
        "full_importer_version": "info-full-importer-v2",
        "importer_config_sha256": importer_config_sha256(),
        "file_count": 24,
        "files": [
            {
                "name": spec.name,
                "dataset_kind": spec.dataset_kind,
                "file_format": spec.file_format,
                "role": spec.role,
                "parser": spec.parser,
                "encoding": spec.encoding,
                "delimiter": spec.delimiter,
                "source_priority": spec.source_priority,
                "size_bytes": 0,
                "sha256": "3" * 64,
                "modified_time_ns": 0,
                "inode": 0,
                "physical_line_count": 0,
                "header": [],
                "header_sha256": "4" * 64,
                "logical_record_count": 0,
                "count_method": "test-fixture",
            }
            for spec in DATA_FILE_SPECS
        ],
        "content_id": "info_v1_" + "1" * 32,
        "manifest_sha256": "2" * 64,
    }


def _quality():
    return {
        "status": "pass",
        "blocking_failure_count": 0,
        "scope": "core_four_files",
        "content_id": "info_v1_" + "1" * 32,
        "manifest_sha256": "2" * 64,
    }


def _load_result():
    return {
        "status": "completed",
        "scope": "core_four_files",
        "database_release_status": "validating",
        "activated": False,
        "content_id": "info_v1_" + "1" * 32,
        "manifest_sha256": "2" * 64,
    }


def _full_quality():
    return {
        "status": "pass",
        "blocking_failure_count": 0,
        "scope": "all_24_files",
        "business_traceability_failure_count": 0,
        "accepted_record_visibility_failure_count": 0,
        "blocking_quality_flag_count": 0,
        "quarantine_mirror_failure_count": 0,
        "source_role_activation_failure_count": 0,
        "files": {
            spec.name: {
                "load_status": "loaded",
                "logical_record_count": 1,
                "loaded_record_count": 1,
                "quarantined_record_count": 0,
                "source_record_count": 1,
                "source_record_accepted_count": 1,
                "source_record_quarantined_count": 0,
                "quarantine_missing_reason_count": 0,
            }
            for spec in DATA_FILE_SPECS
        },
        "content_id": "info_v1_" + "1" * 32,
        "manifest_sha256": "2" * 64,
    }


def _full_result():
    return {
        "status": "completed",
        "scope": "all_24_files",
        "activated": False,
        "database_release_status": "validating",
        "source_file_count": 24,
        "reconciled_source_file_count": 24,
        "unreconciled_record_count": 0,
        "visible_record_traceability_percent": 100,
        "quarantine_reason_coverage_percent": 100,
        "content_id": "info_v1_" + "1" * 32,
        "manifest_sha256": "2" * 64,
    }


def _shadow_diff():
    return {
        "schema_version": 1,
        "component": "static_info_shadow_diff",
        "status": "pass",
        "scope": "all_static_queries_and_snapshot",
        "activated": False,
        "deterministic_query_unapproved_difference_count": 0,
        "full_set_unapproved_difference_count": 0,
        "snapshot_unapproved_difference_count": 0,
        "contact_plaintext_exposure_count": 0,
        "content_id": "info_v1_" + "1" * 32,
        "manifest_sha256": "2" * 64,
    }


def _detector_ab():
    return {
        "status": "pass",
        "event_type_count": 6,
        "unapproved_difference_count": 0,
        "core_hash_unchanged": True,
        "content_id": "info_v1_" + "1" * 32,
        "manifest_sha256": "2" * 64,
    }


def _performance():
    return {
        "status": "pass",
        "exact_query_p95_ms": 1,
        "exact_query_p99_ms": 2,
        "longest_prefix_match_p95_ms": 3,
        "snapshot_load_time_regression_percent": -50,
        "snapshot_peak_rss_regression_percent": -50,
        "detector_throughput_regression_percent": 0,
        "request_path_full_table_load_count": 0,
        "capacity_status": "pass",
        "content_id": "info_v1_" + "1" * 32,
        "manifest_sha256": "2" * 64,
    }


def _security():
    return {
        "status": "pass",
        "unauthorized_write_success_count": 0,
        "contact_plaintext_exposure_count": 0,
        "check_production_side_effect_count": 0,
        "runtime_role_read_only": True,
        "content_id": "info_v1_" + "1" * 32,
        "manifest_sha256": "2" * 64,
    }


def _operations():
    return {
        "status": "pass",
        "release_state_observable": True,
        "per_file_counts_observable": True,
        "checkpoint_resumable": True,
        "same_input_reproducible": True,
        "activated": False,
        "content_id": "info_v1_" + "1" * 32,
        "manifest_sha256": "2" * 64,
    }


class StaticInfoStageGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.evidence = Path(self.temporary.name)
        _write_json(self.evidence / "static-info-manifest.json", _manifest())
        _write_json(self.evidence / "static-info-quality.json", _quality())

    def tearDown(self):
        self.temporary.cleanup()

    def test_s0_and_s1_form_a_pass_receipt_chain(self):
        s0_path = self.evidence / "stage-gate-S0.json"
        s0_report = run_stage_gate("S0", self.evidence, s0_path)
        self.assertEqual(s0_report["status"], "pass")
        self.assertEqual(s0_report["deviation_count"], 0)
        self.assertEqual(s0_path.stat().st_mode & 0o777, 0o600)

        _write_json(
            self.evidence / "static-info-load-result.json",
            _load_result(),
        )
        s1_report = run_stage_gate(
            "S1",
            self.evidence,
            self.evidence / "stage-gate-S1.json",
            previous_receipt=s0_path,
        )
        self.assertEqual(s1_report["status"], "pass")
        self.assertEqual(
            {
                item["requirement_id"]: item["status"]
                for item in s1_report["requirements"]
            }["FA-01"],
            "pass",
        )

    def test_s1_fails_closed_without_previous_receipt(self):
        _write_json(
            self.evidence / "static-info-load-result.json",
            _load_result(),
        )
        report = run_stage_gate(
            "S1",
            self.evidence,
            self.evidence / "stage-gate-S1-missing-previous.json",
        )
        self.assertEqual(report["status"], "fail")
        self.assertIn(
            "PREVIOUS-MISSING",
            {item["check_id"] for item in report["deviations"]},
        )

    def test_s3_requires_zero_query_set_and_snapshot_differences(self):
        s0_path = self.evidence / "stage-gate-S0.json"
        run_stage_gate("S0", self.evidence, s0_path)
        _write_json(
            self.evidence / "static-info-load-result.json",
            _load_result(),
        )
        s1_path = self.evidence / "stage-gate-S1.json"
        run_stage_gate(
            "S1",
            self.evidence,
            s1_path,
            previous_receipt=s0_path,
        )
        _write_json(
            self.evidence / "static-info-full-quality.json",
            _full_quality(),
        )
        _write_json(
            self.evidence / "static-info-full-load-result.json",
            _full_result(),
        )
        s2_path = self.evidence / "stage-gate-S2.json"
        run_stage_gate(
            "S2",
            self.evidence,
            s2_path,
            previous_receipt=s1_path,
        )
        _write_json(
            self.evidence / "static-info-shadow-diff.json",
            _shadow_diff(),
        )
        s3_path = self.evidence / "stage-gate-S3.json"
        report = run_stage_gate(
            "S3",
            self.evidence,
            s3_path,
            previous_receipt=s2_path,
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["deviation_count"], 0)

        failed = _shadow_diff()
        failed["snapshot_unapproved_difference_count"] = 1
        failed["status"] = "fail"
        _write_json(
            self.evidence / "static-info-shadow-diff.json",
            failed,
        )
        failure_report = run_stage_gate(
            "S3",
            self.evidence,
            self.evidence / "stage-gate-S3-failed.json",
            previous_receipt=s2_path,
        )
        self.assertEqual(failure_report["status"], "fail")

    def test_s4_requires_all_four_real_acceptance_boundaries(self):
        s0_path = self.evidence / "stage-gate-S0.json"
        run_stage_gate("S0", self.evidence, s0_path)
        _write_json(
            self.evidence / "static-info-load-result.json",
            _load_result(),
        )
        s1_path = self.evidence / "stage-gate-S1.json"
        run_stage_gate(
            "S1",
            self.evidence,
            s1_path,
            previous_receipt=s0_path,
        )
        _write_json(
            self.evidence / "static-info-full-quality.json",
            _full_quality(),
        )
        _write_json(
            self.evidence / "static-info-full-load-result.json",
            _full_result(),
        )
        s2_path = self.evidence / "stage-gate-S2.json"
        run_stage_gate(
            "S2",
            self.evidence,
            s2_path,
            previous_receipt=s1_path,
        )
        _write_json(
            self.evidence / "static-info-shadow-diff.json",
            _shadow_diff(),
        )
        s3_path = self.evidence / "stage-gate-S3.json"
        run_stage_gate(
            "S3",
            self.evidence,
            s3_path,
            previous_receipt=s2_path,
        )
        for name, value in (
            ("static-info-detector-ab.json", _detector_ab()),
            ("static-info-performance.json", _performance()),
            ("static-info-security.json", _security()),
            ("static-info-operations.json", _operations()),
        ):
            _write_json(self.evidence / name, value)

        report = run_stage_gate(
            "S4",
            self.evidence,
            self.evidence / "stage-gate-S4.json",
            previous_receipt=s3_path,
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["deviation_count"], 0)
        due = {
            item["requirement_id"]: item["status"]
            for item in report["requirements"]
        }
        for requirement in ("FA-07", "FA-08", "FA-09", "FA-11"):
            self.assertEqual(due[requirement], "pass")

        failed = _performance()
        failed["exact_query_p95_ms"] = 20.000001
        failed["status"] = "fail"
        _write_json(
            self.evidence / "static-info-performance.json",
            failed,
        )
        failure = run_stage_gate(
            "S4",
            self.evidence,
            self.evidence / "stage-gate-S4-failed.json",
            previous_receipt=s3_path,
        )
        self.assertEqual(failure["status"], "fail")
        self.assertIn(
            "S4-QUERY-P95",
            {item["check_id"] for item in failure["deviations"]},
        )

    def test_s0_rejects_duplicate_names_hidden_behind_file_count(self):
        manifest = _manifest()
        manifest["files"][-1]["name"] = manifest["files"][0]["name"]
        _write_json(self.evidence / "static-info-manifest.json", manifest)
        report = run_stage_gate(
            "S0",
            self.evidence,
            self.evidence / "stage-gate-S0-duplicate-file.json",
        )
        self.assertEqual(report["status"], "fail")
        self.assertIn(
            "COMMON-MANIFEST-EXACT-FILE-SET",
            {item["check_id"] for item in report["deviations"]},
        )

    def test_s0_rejects_source_role_drift(self):
        manifest = _manifest()
        manifest["files"][0]["role"] = "legacy"
        manifest["files"][0]["sha256"] = "not-a-sha256"
        _write_json(self.evidence / "static-info-manifest.json", manifest)
        report = run_stage_gate(
            "S0",
            self.evidence,
            self.evidence / "stage-gate-S0-role-drift.json",
        )
        self.assertEqual(report["status"], "fail")
        self.assertIn(
            "COMMON-MANIFEST-FILE-CONTRACTS",
            {item["check_id"] for item in report["deviations"]},
        )
        self.assertIn(
            "COMMON-MANIFEST-FILE-IDENTITIES",
            {item["check_id"] for item in report["deviations"]},
        )

    def test_s1_detects_replaced_s0_evidence(self):
        s0_path = self.evidence / "stage-gate-S0.json"
        run_stage_gate("S0", self.evidence, s0_path)
        manifest = _manifest()
        manifest["source_release_label"] = "replaced-after-s0"
        _write_json(self.evidence / "static-info-manifest.json", manifest)
        _write_json(
            self.evidence / "static-info-load-result.json",
            _load_result(),
        )

        report = run_stage_gate(
            "S1",
            self.evidence,
            self.evidence / "stage-gate-S1-replaced.json",
            previous_receipt=s0_path,
        )
        self.assertEqual(report["status"], "fail")
        self.assertIn(
            "PREVIOUS-ARTIFACT-static-info-manifest.json",
            {item["check_id"] for item in report["deviations"]},
        )

    def test_contract_documents_are_hash_pinned(self):
        report = run_stage_gate(
            "S0",
            self.evidence,
            self.evidence / "stage-gate-S0-contract.json",
        )
        contract = json.loads(
            (
                ROOT
                / "contracts"
                / "info"
                / "static-info-final-acceptance-v1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            report["acceptance_document_sha256"],
            contract["acceptance_document"]["sha256"],
        )
        self.assertEqual(
            report["stage_plan_document_sha256"],
            contract["stage_plan_document"]["sha256"],
        )
        s2 = next(stage for stage in contract["stages"] if stage["id"] == "S2")
        full_quality_artifact = next(
            artifact
            for artifact in s2["artifacts"]
            if artifact["path"] == "static-info-full-quality.json"
        )
        exact_file_check = next(
            check
            for check in full_quality_artifact["checks"]
            if check["id"] == "S2-QUALITY-EXACT-FILE-SET"
        )
        self.assertEqual(
            set(exact_file_check["expected"]),
            {spec.name for spec in DATA_FILE_SPECS},
        )
        importer_config_check = next(
            check
            for artifact in contract["common_artifacts"]
            for check in artifact["checks"]
            if check["id"] == "COMMON-MANIFEST-IMPORTER-CONFIG"
        )
        self.assertEqual(
            importer_config_check["expected"],
            importer_config_sha256(),
        )

    def test_s2_requires_full_reconciliation_and_marks_due_requirements(self):
        s0_path = self.evidence / "stage-gate-S0.json"
        run_stage_gate("S0", self.evidence, s0_path)
        _write_json(
            self.evidence / "static-info-load-result.json",
            _load_result(),
        )
        s1_path = self.evidence / "stage-gate-S1.json"
        run_stage_gate(
            "S1",
            self.evidence,
            s1_path,
            previous_receipt=s0_path,
        )

        with tempfile.TemporaryDirectory() as temporary:
            s2_evidence = Path(temporary)
            _write_json(s2_evidence / "static-info-manifest.json", _manifest())
            _write_json(
                s2_evidence / "static-info-full-quality.json",
                _full_quality(),
            )
            _write_json(
                s2_evidence / "static-info-full-load-result.json",
                _full_result(),
            )
            report = run_stage_gate(
                "S2",
                s2_evidence,
                s2_evidence / "stage-gate-S2.json",
                previous_receipt=s1_path,
            )
            self.assertEqual(report["status"], "pass")
            status_by_requirement = {
                item["requirement_id"]: item["status"]
                for item in report["requirements"]
            }
            self.assertEqual(status_by_requirement["FA-02"], "pass")
            self.assertEqual(status_by_requirement["FA-04"], "pass")
            self.assertEqual(status_by_requirement["FA-03"], "not_due")

    def test_s2_rejects_forged_green_aggregate_with_bad_file_detail(self):
        s0_path = self.evidence / "stage-gate-S0.json"
        run_stage_gate("S0", self.evidence, s0_path)
        _write_json(
            self.evidence / "static-info-load-result.json",
            _load_result(),
        )
        s1_path = self.evidence / "stage-gate-S1.json"
        run_stage_gate(
            "S1",
            self.evidence,
            s1_path,
            previous_receipt=s0_path,
        )

        with tempfile.TemporaryDirectory() as temporary:
            s2_evidence = Path(temporary)
            _write_json(s2_evidence / "static-info-manifest.json", _manifest())
            quality = _full_quality()
            quality["files"]["website_entity.csv"]["loaded_record_count"] = 0
            quality["accepted_record_visibility_failure_count"] = 1
            quality["source_role_activation_failure_count"] = 1
            _write_json(
                s2_evidence / "static-info-full-quality.json",
                quality,
            )
            _write_json(
                s2_evidence / "static-info-full-load-result.json",
                _full_result(),
            )
            report = run_stage_gate(
                "S2",
                s2_evidence,
                s2_evidence / "stage-gate-S2.json",
                previous_receipt=s1_path,
            )
            self.assertEqual(report["status"], "fail")
            self.assertIn(
                "S2-QUALITY-PER-FILE-RECONCILIATION",
                {
                    item["check_id"]
                    for item in report["deviations"]
                },
            )
            self.assertIn(
                "S2-SOURCE-ROLE-ACTIVATION-FAILURES",
                {
                    item["check_id"]
                    for item in report["deviations"]
                },
            )
            self.assertIn(
                "S2-ACCEPTED-RECORD-VISIBILITY-FAILURES",
                {
                    item["check_id"]
                    for item in report["deviations"]
                },
            )

    def test_existing_shadow_loader_calls_s0_and_s1_hooks(self):
        common = (
            ROOT / "deploy" / "lib" / "static-info-common.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("static-info-stage-end-hook.sh", common)
        self.assertIn("stage-gate-S0.json", common)
        self.assertIn("stage-gate-S1.json", common)
        self.assertLess(
            common.index("stage_zero_receipt}"),
            common.index("-m backend.info_pipeline load-core"),
        )

    def test_shell_hook_runs_the_same_fail_closed_gate(self):
        output = self.evidence / "stage-gate-S0-shell.json"
        result = subprocess.run(
            [
                str(
                    ROOT
                    / "deploy"
                    / "database"
                    / "static-info-stage-end-hook.sh"
                ),
                "S0",
                str(self.evidence),
                str(output),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(output.read_text(encoding="utf-8"))["status"],
            "pass",
        )

    @unittest.skipUnless(
        shutil.which("zstd") and shutil.which("sha256sum"),
        "缺少 static INFO 证据打包命令",
    )
    def test_s2_evidence_bundle_is_self_contained_and_reverifiable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "static-info"
            evidence.mkdir()
            _write_json(evidence / "static-info-manifest.json", _manifest())
            _write_json(evidence / "static-info-quality.json", _quality())
            s0_path = evidence / "stage-gate-S0.json"
            run_stage_gate("S0", evidence, s0_path)
            _write_json(
                evidence / "static-info-load-result.json",
                _load_result(),
            )
            s1_path = evidence / "stage-gate-S1.json"
            run_stage_gate(
                "S1",
                evidence,
                s1_path,
                previous_receipt=s0_path,
            )
            _write_checksums(
                evidence,
                (
                    "static-info-manifest.json",
                    "static-info-quality.json",
                    "static-info-load-result.json",
                    "stage-gate-S0.json",
                    "stage-gate-S1.json",
                ),
            )

            s2_evidence = evidence / "S2"
            s2_evidence.mkdir()
            _write_json(
                s2_evidence / "static-info-manifest.json",
                _manifest(),
            )
            _write_json(
                s2_evidence / "static-info-full-quality.json",
                _full_quality(),
            )
            _write_json(
                s2_evidence / "static-info-full-load-result.json",
                _full_result(),
            )
            run_stage_gate(
                "S2",
                s2_evidence,
                s2_evidence / "stage-gate-S2.json",
                previous_receipt=s1_path,
            )
            _write_checksums(
                s2_evidence,
                (
                    "static-info-manifest.json",
                    "static-info-full-quality.json",
                    "static-info-full-load-result.json",
                    "stage-gate-S2.json",
                ),
            )
            failed_attempt = root / "static-info.incomplete.test"
            failed_attempt.mkdir()
            (failed_attempt / "failure-marker.txt").write_text(
                "保留失败证据\n",
                encoding="utf-8",
            )
            archive = root / "static-info-evidence.tar.zst"
            bundle = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1"; source "$2"; '
                    'domeye_static_info_bundle_evidence "$3" "$4" "$5" "$6"',
                    "bundle-test",
                    str(ROOT / "deploy" / "lib" / "artifact-common.sh"),
                    str(ROOT / "deploy" / "lib" / "static-info-common.sh"),
                    str(ROOT),
                    str(evidence),
                    "all_24_files",
                    str(archive),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(bundle.returncode, 0, bundle.stderr)
            listing = subprocess.run(
                [
                    "bash",
                    "-c",
                    'zstd --quiet --decompress --stdout "$1" | tar -tf -',
                    "list-bundle",
                    str(archive),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(listing.returncode, 0, listing.stderr)
            self.assertIn(
                "static-info.incomplete.test/failure-marker.txt",
                listing.stdout,
            )
            verify = subprocess.run(
                [
                    str(
                        ROOT
                        / "deploy"
                        / "artifacts"
                        / "verify-static-info-evidence.sh"
                    ),
                    str(archive),
                    "all_24_files",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(verify.returncode, 0, verify.stderr)

            verifier_runtime = subprocess.run(
                [
                    "bash",
                    "-c",
                    "mapfile -t values < <(printf 'x\\n'); "
                    "find \"$1\" -maxdepth 0 -printf '%f\\n' >/dev/null",
                    "runtime-check",
                    str(root),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if verifier_runtime.returncode != 0:
                return

            release = root / "release"
            release.mkdir()
            release_id = "20260724T000000Z-info-test"
            data_start = "2026-02-01 00:00:00"
            for name in (
                "database-image.tar.zst",
                "database.dump.zst",
                "database-schema.sql",
                "info.tar.zst",
            ):
                (release / name).write_bytes((name + "\n").encode("utf-8"))
            shutil.copy2(
                archive,
                release / "static-info-evidence.tar.zst",
            )
            static_info = {
                "present": True,
                "implementation_scope": "all_24_files",
                "content_id": _manifest()["content_id"],
            }
            discarded = {"total": 0, "by_month_type": []}
            inventory = {
                "integrity": {
                    "table_whitelist": {"ok": True},
                    "detail_references": {
                        "ok": True,
                        "malformed_count": 0,
                        "orphan_count": 0,
                        "discarded_malformed_event_rows": discarded,
                    },
                },
                "static_info": static_info,
            }
            _write_json(release / "database-inventory.json", inventory)

            def digest(name):
                return hashlib.sha256((release / name).read_bytes()).hexdigest()

            info_manifest = {
                "release_id": release_id,
                "data_start": data_start,
                "archive": {
                    "name": "info.tar.zst",
                    "sha256": digest("info.tar.zst"),
                },
            }
            _write_json(release / "info-manifest.json", info_manifest)
            database_manifest = {
                "release_id": release_id,
                "data_start": data_start,
                "archive": {
                    "name": "database.dump.zst",
                    "sha256": digest("database.dump.zst"),
                },
                "image": {
                    "archive": "database-image.tar.zst",
                    "archive_sha256": digest("database-image.tar.zst"),
                },
                "inventory": {
                    "name": "database-inventory.json",
                    "sha256": digest("database-inventory.json"),
                },
                "schema": {
                    "name": "database-schema.sql",
                    "sha256": digest("database-schema.sql"),
                },
                "integrity": {
                    "table_whitelist_ok": True,
                    "malformed_detail_count": 0,
                    "orphan_detail_count": 0,
                    "discarded_malformed_event_rows": discarded,
                },
                "static_info": static_info,
                "static_info_evidence": {
                    "name": "static-info-evidence.tar.zst",
                    "sha256": digest("static-info-evidence.tar.zst"),
                    "size": (
                        release / "static-info-evidence.tar.zst"
                    ).stat().st_size,
                    "scope": "all_24_files",
                    "content_id": _manifest()["content_id"],
                },
            }
            _write_json(
                release / "database-manifest.json",
                database_manifest,
            )
            _write_json(
                release / "manifest.json",
                {
                    "release_id": release_id,
                    "data_start": data_start,
                    "info": info_manifest,
                    "database": database_manifest,
                },
            )
            _write_checksums(
                release,
                (
                    "database-image.tar.zst",
                    "database-inventory.json",
                    "database-manifest.json",
                    "database-schema.sql",
                    "database.dump.zst",
                    "info-manifest.json",
                    "info.tar.zst",
                    "manifest.json",
                    "static-info-evidence.tar.zst",
                ),
            )
            release_verify = subprocess.run(
                [
                    str(ROOT / "deploy" / "artifacts" / "verify-release.sh"),
                    str(release),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                release_verify.returncode,
                0,
                release_verify.stderr,
            )


if __name__ == "__main__":
    unittest.main()
