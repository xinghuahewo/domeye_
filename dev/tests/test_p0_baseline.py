import csv
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from dev.data_profile import load_data_profile
from dev.data_quality.p0_baseline import (
    AS_ACTIVITY_SERIES,
    BaselineError,
    DEFAULT_PROFILE,
    EVENT_TYPES,
    REQUIRED_PROBE_CHECKS,
    _canonical_sha256,
    build_baseline,
)


COVERAGE_COLUMNS = [
    "event_type",
    "fact_table_family",
    "problem_field",
    "event_id_field",
    "start_time_field",
    "end_time_field",
    "duration_field",
    "affected_object_fields",
    "before_field",
    "during_field",
    "after_field",
    "vp_identity_status",
    "raw_reference_status",
    "lineage_level",
    "notes",
]
PROJECT_ROOT = DEFAULT_PROFILE.parents[1]
PROBE_PROGRAM = PROJECT_ROOT / "dev" / "data_quality" / "p0_probe.py"
PROFILE_LOADER = PROJECT_ROOT / "dev" / "data_profile.py"


def table_names():
    families = [
        "event_table",
        "hijack",
        "sub_hijack",
        "leak_event",
        "prefix_outage",
        "as_outage",
        "country_outage",
        "feature_other",
        "feature_us",
        "feature_br",
        "feature_cn",
        "feature_ru",
        "feature_in",
        "feature_gb",
        "feature_id",
        "feature_de",
        "feature_au",
        "feature_pl",
    ]
    return ["feature_country"] + [
        "{}_{}".format(family, month)
        for month in ("202602", "202603")
        for family in families
    ]


class P0BaselineTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.release_dir = self.root / "releases" / "20260717T124354Z"
        self.release_dir.mkdir(parents=True)
        self.state_path = self.root / "state.json"
        self.coverage_path = self.root / "coverage.csv"
        self.raw_path = self.root / "raw.json"
        self.probe_path = self.root / "p0-quality-probe.json"
        self.profile = load_data_profile()
        self._write_release_context()
        self._write_coverage()
        self._write_raw_inventory()
        self._write_probe(self._probe_fixture())

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _write_json(path, payload):
        path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )

    def _write_release_context(self):
        # 基础发布 inventory 故意只含一张七月口径表；baseline 不得把它当当前表事实。
        inventory_path = self.release_dir / "database-inventory.json"
        self._write_json(
            inventory_path,
            {
                "schema_version": 1,
                "tables": [
                    {
                        "name": "feature_country",
                        "row_count": 999999999,
                        "min_time": "2026-07-01 00:00:00",
                        "max_time": "2026-07-17 00:00:00",
                        "schema_hash": "a" * 32,
                    }
                ],
            },
        )
        database_manifest = {
            "schema_version": 1,
            "component": "database",
            "release_id": "20260717T124354Z",
            "inventory": {
                "name": "database-inventory.json",
                "sha256": self._sha(inventory_path),
            },
        }
        database_manifest_path = self.release_dir / "database-manifest.json"
        self._write_json(database_manifest_path, database_manifest)
        manifest_path = self.release_dir / "manifest.json"
        self._write_json(
            manifest_path,
            {
                "schema_version": 1,
                "release_id": "20260717T124354Z",
                "database": database_manifest,
            },
        )
        self._write_json(
            self.state_path,
            {
                "schema_version": 2,
                "phase": "verified",
                "release_id": "20260717T124354Z",
                "release_dir": str(self.release_dir.resolve()),
                "system_identifier": "7663836852697006116",
                "port": 31627,
                "data_start": self.profile["local"]["start"],
                "data_end_exclusive": self.profile["local"]["end_exclusive"],
                "hashes": {
                    "release_manifest": self._sha(manifest_path),
                    "database_manifest": self._sha(database_manifest_path),
                    "inventory": self._sha(inventory_path),
                },
            },
        )

    def _write_coverage(self):
        with self.coverage_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=COVERAGE_COLUMNS)
            writer.writeheader()
            for event_type in EVENT_TYPES:
                writer.writerow(
                    {
                        "event_type": event_type,
                        "fact_table_family": "leak_event" if event_type == "leak" else event_type,
                        "problem_field": "prefix",
                        "event_id_field": "event_id",
                        "start_time_field": "s_time",
                        "end_time_field": "e_time",
                        "duration_field": "duration",
                        "affected_object_fields": "prefix",
                        "before_field": "",
                        "during_field": "",
                        "after_field": "",
                        "vp_identity_status": "not_retained",
                        "raw_reference_status": "not_retained",
                        "lineage_level": "legacy_compatible",
                        "notes": "测试",
                    }
                )

    def _write_raw_inventory(self):
        database_manifest_path = self.release_dir / "database-manifest.json"
        self._write_json(
            self.raw_path,
            {
                "schema_version": 1,
                "audit_kind": "p0_raw_artifact_inventory",
                "data_profile": {
                    "id": self.profile["id"],
                    "profile_sha256": self._sha(DEFAULT_PROFILE),
                    "timezone": self.profile["timezone"],
                    "window_start": self.profile["window_start"],
                    "window_end_exclusive": self.profile["window_end_exclusive"],
                    "snapshot_time": self.profile["snapshot_time"],
                },
                "status": "partial",
                "coverage": {
                    "update_expected_slot_count": 16992,
                    "update_available_slot_count": 10272,
                    "missing_local_range": {
                        "start": "2026-02-01T00:00:00+08:00",
                        "end_exclusive": "2026-02-24T08:00:00+08:00",
                        "missing_update_slot_count": 6720,
                    },
                },
                "vp_identity_available": False,
                "record_level_reference_available": False,
                "full_file_manifest_available": False,
                "full_file_checksum_available": False,
                "processing_lineage_available": False,
                "source_release_conflict": {
                    "release_id": "20260717T124354Z",
                    "database_manifest_sha256": self._sha(database_manifest_path),
                },
                "artifacts": [],
                "limitations": ["测试中的部分覆盖"],
            },
        )

    def _probe_fixture(self):
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        git_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        source_series = {
            "series_id": "feature_country.collect",
            "table_names": ["feature_country"],
            "subject_filter": {"country": "collect"},
            "granularity_seconds": 300,
            "expected_sample_count": 16992,
            "raw_observed_timestamp_count": 10266,
            "observed_sample_count": 10266,
            "missing_sample_count": 6726,
            "off_grid_sample_count": 0,
            "first_observed_at": "2026-02-24 08:00:00",
            "last_observed_at": "2026-03-31 23:55:00",
            "missing_ranges": [
                {
                    "start": "2026-02-01 00:00:00",
                    "end_exclusive": "2026-02-24 08:00:00",
                    "sample_count": 6720,
                },
                {
                    "start": "2026-03-31 07:30:00",
                    "end_exclusive": "2026-03-31 08:00:00",
                    "sample_count": 6,
                },
            ],
            "missing_samples": ["2026-02-01 00:00:00"],
            "off_grid_samples": [],
            "coverage_ratio": 0.604166666667,
            "coverage_status": "observed_gap",
            "missing_reason": "legacy_unknown",
        }
        activity_series = [
            {
                "series_id": series_id,
                "table_names": [
                    "{}_202602".format(series_id),
                    "{}_202603".format(series_id),
                ],
                "series_semantics": "subject_activity_sparse",
                "activity_timestamp_count": index,
                "first_activity_at": "2026-02-24 08:00:00",
                "last_activity_at": "2026-03-31 23:55:00",
            }
            for index, series_id in enumerate(AS_ACTIVITY_SERIES, start=1)
        ]
        timeseries = {
            "granularity_seconds": 300,
            "source_series": [source_series],
            "activity_series": activity_series,
            "totals": {
                "source_series_count": 1,
                "source_series_with_missing_samples": 1,
                "source_series_with_off_grid_samples": 0,
                "source_missing_sample_count": 6726,
                "source_off_grid_sample_count": 0,
                "activity_series_count": 11,
                "activity_timestamp_count": sum(range(1, 12)),
            },
        }
        checks = [
            {
                "check_id": check_id,
                "status": "pending" if check_id == "timeseries.unclassified_missing" else "pass",
                "summary": "fixture",
                "evidence": None,
            }
            for check_id in sorted(REQUIRED_PROBE_CHECKS)
        ]
        pending = ["timeseries.unclassified_missing"]
        payload = {
            "schema_version": 1,
            "probe_kind": "p0_quality_probe",
            "data_profile": {
                key: self.profile[key]
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
            },
            "source": {
                "release_id": "20260717T124354Z",
                "state_sha256": self._sha(self.state_path),
                "manifest_sha256": state["hashes"]["release_manifest"],
                "database_manifest_sha256": state["hashes"]["database_manifest"],
                "inventory_sha256": state["hashes"]["inventory"],
                "database": {
                    "host": "127.0.0.1",
                    "port": 31627,
                    "name": "bgp_project",
                    "server_version": "12.16",
                    "system_identifier": "7663836852697006116",
                },
                "current_user": "domeye_core_reader",
                "transaction_read_only": True,
                "default_transaction_read_only": True,
                "transaction_isolation": "repeatable read",
                "query_started_at": "2026-07-20T00:00:00Z",
                "query_completed_at": "2026-07-20T00:01:00Z",
                "git_sha": "a" * 40,
                "git_dirty": False,
                "git_status_sha256": "b" * 64,
                "project_root": str(PROJECT_ROOT.resolve()),
                "probe_path": str(PROBE_PROGRAM.resolve()),
                "probe_sha256": self._sha(PROBE_PROGRAM),
                "data_profile_path": str(DEFAULT_PROFILE.resolve()),
                "data_profile_sha256": self._sha(DEFAULT_PROFILE),
                "data_profile_loader_sha256": self._sha(PROFILE_LOADER),
            },
            "tables": [{"name": name, "row_count": 7} for name in table_names()],
            "timeseries_coverage": timeseries,
            "reference_integrity": {"totals": {}},
            "checks": checks,
            "quality_gate": {
                "status": "pending",
                "blocking_failure_count": 0,
                "blocking_failures": [],
                "pending_check_count": 1,
                "pending_checks": pending,
            },
            "blocking_failure_count": 0,
            "pending_check_count": 1,
        }
        payload["source"]["git_sha"] = git_sha
        return payload

    def _fingerprint(self, probe):
        source = probe["source"]
        return _canonical_sha256(
            {
                "schema_version": probe["schema_version"],
                "probe_kind": probe["probe_kind"],
                "data_profile": probe["data_profile"],
                "release_id": source["release_id"],
                "inventory_sha256": source["inventory_sha256"],
                "provenance": {
                    key: source[key]
                    for key in (
                        "git_sha",
                        "git_dirty",
                        "git_status_sha256",
                        "probe_sha256",
                        "data_profile_sha256",
                        "data_profile_loader_sha256",
                    )
                },
                "tables": probe["tables"],
                "timeseries_coverage": probe["timeseries_coverage"],
                "reference_integrity": probe["reference_integrity"],
                "checks": probe["checks"],
            }
        )

    def _write_probe(self, probe, refresh_fingerprint=True):
        if refresh_fingerprint:
            probe["result_fingerprint_sha256"] = self._fingerprint(probe)
        self._write_json(self.probe_path, probe)

    def build(self):
        return build_baseline(
            project_root=PROJECT_ROOT,
            state_path=self.state_path,
            release_dir=self.release_dir,
            quality_probe_path=self.probe_path,
            coverage_path=self.coverage_path,
            raw_inventory_path=self.raw_path,
            generated_at="2026-07-20T00:00:00Z",
        )

    def test_exact_classification_reaches_legacy_compatible_without_raw_claim(self):
        payload = self.build()
        self.assertTrue(payload["summary"]["baseline_ready"])
        self.assertTrue(payload["summary"]["legacy_compatible"])
        self.assertFalse(payload["summary"]["raw_traceable"])
        self.assertEqual(payload["summary"]["p0_data_status"], "legacy_compatible")
        self.assertEqual(payload["summary"]["pending_checks"], ["raw.traceability"])
        grid = payload["timeseries_semantics"]["source_observation_grid"]
        self.assertEqual(grid["source_unavailable"]["slot_count"], 6720)
        self.assertEqual(grid["processing_gap"]["slot_count"], 6)
        self.assertEqual(grid["unclassified_slot_count"], 0)
        self.assertFalse(grid["zero_filled"])

    def test_base_inventory_is_provenance_only_and_live_tables_win(self):
        payload = self.build()
        self.assertEqual(payload["scope"]["live_table_row_count"], 37 * 7)
        self.assertEqual(len(payload["tables"]), 37)
        self.assertEqual(
            payload["source_artifacts"]["base_database_inventory"]["usage"],
            "source_provenance_only",
        )
        self.assertNotEqual(payload["scope"]["live_table_row_count"], 999999999)

    def test_tampered_probe_fingerprint_is_rejected(self):
        probe = json.loads(self.probe_path.read_text(encoding="utf-8"))
        probe["tables"][0]["row_count"] = 8
        self._write_probe(probe, refresh_fingerprint=False)
        with self.assertRaisesRegex(BaselineError, "指纹"):
            self.build()

    def test_probe_program_hash_must_match_retained_executable(self):
        probe = json.loads(self.probe_path.read_text(encoding="utf-8"))
        probe["source"]["probe_sha256"] = "c" * 64
        self._write_probe(probe)
        with self.assertRaisesRegex(BaselineError, "执行程序 SHA256"):
            self.build()

    def test_probe_profile_mismatch_is_rejected_even_with_valid_fingerprint(self):
        probe = json.loads(self.probe_path.read_text(encoding="utf-8"))
        probe["data_profile"]["timezone"] = "UTC"
        self._write_probe(probe)
        with self.assertRaisesRegex(BaselineError, "data_profile"):
            self.build()

    def test_probe_release_mismatch_is_rejected_even_with_valid_fingerprint(self):
        probe = json.loads(self.probe_path.read_text(encoding="utf-8"))
        probe["source"]["release_id"] = "20260720T000000Z"
        self._write_probe(probe)
        with self.assertRaisesRegex(BaselineError, "release_id"):
            self.build()

    def test_missing_live_table_is_rejected(self):
        probe = json.loads(self.probe_path.read_text(encoding="utf-8"))
        probe["tables"] = probe["tables"][:-1]
        self._write_probe(probe)
        with self.assertRaisesRegex(BaselineError, "37 张表"):
            self.build()

    def test_unclassified_or_inexact_missing_range_blocks_legacy_compatible(self):
        probe = json.loads(self.probe_path.read_text(encoding="utf-8"))
        source = probe["timeseries_coverage"]["source_series"][0]
        source["missing_ranges"][1]["start"] = "2026-03-31 07:35:00"
        self._write_probe(probe)
        payload = self.build()
        self.assertFalse(payload["summary"]["legacy_compatible"])
        self.assertEqual(payload["summary"]["p0_data_status"], "not_accepted")
        self.assertIn(
            "timeseries.source_observation_grid",
            payload["summary"]["blocking_failures"],
        )
        self.assertIsNone(
            payload["timeseries_semantics"]["source_observation_grid"][
                "unclassified_slot_count"
            ]
        )

    def test_known_reference_defect_does_not_hide_completed_d0_audit(self):
        probe = json.loads(self.probe_path.read_text(encoding="utf-8"))
        for check in probe["checks"]:
            if check["check_id"] == "references.reverse":
                check["status"] = "fail"
        probe["quality_gate"] = {
            "status": "fail",
            "blocking_failure_count": 1,
            "blocking_failures": ["references.reverse"],
            "pending_check_count": 1,
            "pending_checks": ["timeseries.unclassified_missing"],
        }
        probe["blocking_failure_count"] = 1
        self._write_probe(probe)
        payload = self.build()
        self.assertTrue(payload["summary"]["d0_audit_complete"])
        self.assertTrue(payload["summary"]["baseline_ready"])
        self.assertFalse(payload["summary"]["legacy_compatible"])
        self.assertEqual(payload["summary"]["p0_data_status"], "not_accepted")

    def test_missing_ranges_cannot_be_replaced_by_limited_missing_samples(self):
        probe = json.loads(self.probe_path.read_text(encoding="utf-8"))
        probe["timeseries_coverage"]["source_series"][0].pop("missing_ranges")
        self._write_probe(probe)
        payload = self.build()
        self.assertFalse(payload["summary"]["legacy_compatible"])

    def test_coverage_matrix_must_contain_exact_six_types(self):
        with self.coverage_path.open(encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        with self.coverage_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=COVERAGE_COLUMNS)
            writer.writeheader()
            writer.writerows(rows[:-1])
        with self.assertRaisesRegex(BaselineError, "六种事件"):
            self.build()


if __name__ == "__main__":
    unittest.main()
