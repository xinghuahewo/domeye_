import inspect
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from dev.data_profile import load_data_profile
from dev.data_quality.p0_probe import (
    PROBE_PATH,
    ProbeError,
    _apply_duplicate_stats,
    _apply_table_stats,
    _begin_readonly_transaction,
    _build_checks,
    _build_malformed_locator_query,
    _build_reference_query,
    _build_table_stats_query,
    _build_timeseries_activity_query,
    _build_timeseries_coverage_query,
    _canonical_sha256,
    _collect_timeseries_coverage,
    _expected_tables,
    _git_provenance,
    _load_project_data_profile,
    _profile_months,
    _quality_gate_summary,
    _read_database_env,
    _read_table_catalog,
    _validate_release_context,
    _validate_security_row,
    _schema_fingerprint_payload,
    _write_json,
    probe_database,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RecordingCursor:
    def __init__(self, rows=None):
        self.executed = []
        self.rows = list(rows or [])

    def execute(self, query, parameters=None):
        self.executed.append((query, parameters))

    def fetchone(self):
        if not self.rows:
            return None
        return self.rows.pop(0)


class ProfileAndHashTest(unittest.TestCase):
    def test_fixed_profile_expands_to_exact_37_tables(self):
        profile = load_data_profile()
        self.assertEqual(_profile_months(profile), ["202602", "202603"])
        tables = _expected_tables(profile)
        self.assertEqual(len(tables), 37)
        self.assertEqual(tables[0], "feature_country")
        self.assertIn("country_outage_202603", tables)
        self.assertNotIn("event_table_202604", tables)

    def test_canonical_schema_hash_is_order_stable_and_change_sensitive(self):
        left = {"columns": [{"name": "a", "type": "text"}], "indexes": [], "kind": "r"}
        reordered = {"kind": "r", "indexes": [], "columns": [{"type": "text", "name": "a"}]}
        changed = {"kind": "r", "indexes": [], "columns": [{"type": "bigint", "name": "a"}]}
        self.assertEqual(_canonical_sha256(left), _canonical_sha256(reordered))
        self.assertNotEqual(_canonical_sha256(left), _canonical_sha256(changed))
        self.assertRegex(_canonical_sha256(left), r"^[0-9a-f]{64}$")

    def test_schema_fingerprint_excludes_timescale_physical_chunk_drift(self):
        payload = {
            "relation_kind": "r",
            "is_partition": False,
            "partition_key": None,
            "parent": None,
            "columns": [{"name": "t", "data_type": "timestamp"}],
            "constraints": [],
            "indexes": [],
            "partitions": [
                {"schema": "_timescaledb_internal", "name": "_hyper_1_1_chunk", "bound": None}
            ],
            "timescaledb": {
                "is_hypertable": True,
                "num_dimensions": 1,
                "num_chunks": 1,
                "compression_enabled": False,
            },
        }
        changed_physical_layout = deepcopy(payload)
        changed_physical_layout["partitions"][0]["name"] = "_hyper_1_99_chunk"
        changed_physical_layout["timescaledb"]["num_chunks"] = 99
        self.assertEqual(
            _canonical_sha256(_schema_fingerprint_payload(payload)),
            _canonical_sha256(_schema_fingerprint_payload(changed_physical_layout)),
        )
        changed_column = deepcopy(payload)
        changed_column["columns"][0]["data_type"] = "timestamp with time zone"
        self.assertNotEqual(
            _canonical_sha256(_schema_fingerprint_payload(payload)),
            _canonical_sha256(_schema_fingerprint_payload(changed_column)),
        )

    def test_provenance_hashes_actual_probe_profile_and_git_worktree(self):
        profile_path = PROJECT_ROOT / "config" / "data-profile.json"
        provenance = _git_provenance(
            PROJECT_ROOT,
            probe_path=PROBE_PATH,
            data_profile_path=profile_path,
        )
        self.assertRegex(provenance["git_sha"], r"^[0-9a-f]{40}$")
        self.assertRegex(provenance["git_status_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(provenance["probe_sha256"], self.sha256(PROBE_PATH))
        self.assertEqual(provenance["data_profile_sha256"], self.sha256(profile_path))
        self.assertEqual(_load_project_data_profile(PROJECT_ROOT)["id"], "feb-mar-2026")

    @staticmethod
    def sha256(path):
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()


class EnvironmentSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def write_env(self, mode=0o600):
        path = self.root / "database.env"
        path.write_text(
            "\n".join(
                [
                    "DOMEYE_CORE_DB_NAME=bgp_project",
                    "DOMEYE_CORE_DB_ADMIN_PASSWORD=must-not-be-returned",
                    "DOMEYE_CORE_DB_READER_USER=domeye_core_reader",
                    "DOMEYE_CORE_DB_READER_PASSWORD=reader:secret#value",
                    "DOMEYE_CORE_DB_PORT=29429",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        path.chmod(mode)
        return path

    def test_env_accepts_0600_and_returns_only_reader_fields(self):
        values = _read_database_env(self.write_env())
        self.assertEqual(
            set(values),
            {
                "DOMEYE_CORE_DB_NAME",
                "DOMEYE_CORE_DB_READER_USER",
                "DOMEYE_CORE_DB_READER_PASSWORD",
            },
        )
        self.assertNotIn("DOMEYE_CORE_DB_ADMIN_PASSWORD", values)
        self.assertEqual(values["DOMEYE_CORE_DB_READER_PASSWORD"], "reader:secret#value")

    def test_env_rejects_group_or_other_permissions(self):
        with self.assertRaisesRegex(ProbeError, "权限"):
            _read_database_env(self.write_env(0o640))

    def test_env_rejects_symlink(self):
        real = self.write_env()
        link = self.root / "database-link.env"
        link.symlink_to(real)
        with self.assertRaisesRegex(ProbeError, "软链接"):
            _read_database_env(link)

    def test_probe_payload_builder_never_reads_password_for_output(self):
        source = inspect.getsource(probe_database)
        self.assertNotIn('database_config["DOMEYE_CORE_DB_READER_PASSWORD"]', source)


class ReleaseContextTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.release_dir = self.root / "releases" / "20260717T124354Z"
        self.release_dir.mkdir(parents=True)
        self.state_path = self.root / "state.json"
        self.profile = load_data_profile()

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def sha(path):
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_context(self):
        inventory_path = self.release_dir / "database-inventory.json"
        inventory_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "tables": [
                        {
                            "name": "feature_country",
                            "schema_hash": "a" * 32,
                            "row_count": 1,
                            "min_time": "2026-02-24 08:00:00",
                            "max_time": "2026-03-31 23:55:00",
                        }
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        database_manifest = {
            "schema_version": 1,
            "component": "database",
            "release_id": "20260717T124354Z",
            "inventory": {
                "name": "database-inventory.json",
                "sha256": self.sha(inventory_path),
            },
        }
        database_manifest_path = self.release_dir / "database-manifest.json"
        database_manifest_path.write_text(
            json.dumps(database_manifest, sort_keys=True), encoding="utf-8"
        )
        manifest_path = self.release_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "release_id": "20260717T124354Z",
                    "database": database_manifest,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        state = {
            "schema_version": 2,
            "phase": "verified",
            "release_id": "20260717T124354Z",
            "release_dir": str(self.release_dir.resolve()),
            "system_identifier": "7663836852697006116",
            "port": 31627,
            "data_start": self.profile["local"]["start"],
            "data_end_exclusive": self.profile["local"]["end_exclusive"],
            "hashes": {
                "release_manifest": self.sha(manifest_path),
                "database_manifest": self.sha(database_manifest_path),
                "inventory": self.sha(inventory_path),
            },
        }
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        return inventory_path

    def test_context_cross_checks_release_and_all_hashes(self):
        self.write_context()
        context = _validate_release_context(
            profile=self.profile,
            state_path=self.state_path,
            release_dir=self.release_dir,
        )
        self.assertEqual(context["release_id"], "20260717T124354Z")
        self.assertEqual(context["port"], 31627)
        self.assertEqual(context["legacy_schema_hashes"]["feature_country"], "a" * 32)

    def test_context_rejects_inventory_changed_after_manifest(self):
        inventory_path = self.write_context()
        inventory_path.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ProbeError, "inventory SHA256"):
            _validate_release_context(
                profile=self.profile,
                state_path=self.state_path,
                release_dir=self.release_dir,
            )

    def test_context_rejects_state_port_or_window_override(self):
        self.write_context()
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["port"] = 0
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(ProbeError, "端口"):
            _validate_release_context(
                profile=self.profile,
                state_path=self.state_path,
                release_dir=self.release_dir,
            )


class ReadonlyAndSqlTest(unittest.TestCase):
    def test_catalog_query_does_not_use_reserved_collation_alias(self):
        source = inspect.getsource(_read_table_catalog)
        self.assertIn("pg_collation AS collation_value", source)
        self.assertNotIn("pg_collation AS collation ON", source)

    def test_begin_is_repeatable_read_read_only_and_timeouts_are_bounded(self):
        cursor = RecordingCursor()
        _begin_readonly_transaction(cursor, 900_000)
        self.assertEqual(
            cursor.executed[0][0],
            "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        )
        self.assertTrue(all("CREATE" not in query for query, _ in cursor.executed))
        with self.assertRaisesRegex(ProbeError, "statement timeout"):
            _begin_readonly_transaction(cursor, 999)

    def test_security_row_rejects_writable_or_privileged_identity(self):
        valid = (
            "domeye_core_reader",
            "bgp_project",
            "on",
            "on",
            "repeatable read",
            "12.16",
            "7663836852697006116",
            False,
            False,
            False,
            False,
            False,
            ["default_transaction_read_only=on"],
            "2026-07-20T00:00:00Z",
        )
        result = _validate_security_row(
            valid,
            "domeye_core_reader",
            "bgp_project",
            "7663836852697006116",
        )
        self.assertTrue(result["transaction_read_only"])
        privileged = list(valid)
        privileged[7] = True
        with self.assertRaisesRegex(ProbeError, "高权限"):
            _validate_security_row(
                privileged,
                "domeye_core_reader",
                "bgp_project",
                "7663836852697006116",
            )
        writable_default = list(valid)
        writable_default[3] = "off"
        with self.assertRaisesRegex(ProbeError, "只读"):
            _validate_security_row(
                writable_default,
                "domeye_core_reader",
                "bgp_project",
                "7663836852697006116",
            )

    def test_table_stats_query_quotes_catalog_identifiers_and_binds_window(self):
        columns = [
            {"name": 'text";DROP TABLE x;--', "type_category": "S", "type_name": "text"},
            {"name": "metric", "type_category": "N", "type_name": "int8"},
            {"name": "payload", "type_category": "U", "type_name": "jsonb"},
        ]
        query = _build_table_stats_query('table"name', "t", columns)
        self.assertIn('"table""name"', query)
        self.assertIn('"text"";DROP TABLE x;--"', query)
        self.assertEqual(query.count("%s"), 2)
        self.assertTrue(query.lstrip().startswith("SELECT"))
        self.assertEqual(query.count("SELECT"), 1)
        self.assertTrue(query.rstrip().endswith('"public"."table""name"'))

    def test_reference_query_contains_both_directions_ambiguity_and_time_checks(self):
        query = _build_reference_query("202603", "prefix_outage")
        self.assertIn("match_summary", query)
        self.assertIn("reverse_summary", query)
        self.assertIn("forward_ambiguous_count", query)
        self.assertIn("duplicate_locator_group_count", query)
        self.assertIn("duplicate_event_locator_group_count", query)
        self.assertIn("duplicate_event_locator_excess_count", query)
        self.assertIn("duplicate_event_locators AS MATERIALIZED", query)
        self.assertIn("AS event_references", query)
        self.assertNotIn("AS references", query)
        self.assertIn("exact_time_match_count", query)
        self.assertIn("partition_mismatch_count", query)
        self.assertIn("'asn', fact.\"asn\"", query)
        self.assertIn("declared_event_type = %(declared_event_type)s", query)
        self.assertNotIn("CREATE TEMP", query)
        self.assertNotIn("ANALYZE", query)

        malformed = _build_malformed_locator_query("202603")
        self.assertIn("event_type = 'hijack' AND declared_event_type = '前缀劫持'", malformed)
        self.assertIn("event_type = 'country_outage' AND declared_event_type = '国家中断'", malformed)

    def test_source_coverage_query_uses_collect_grid_and_compressed_ranges(self):
        query = _build_timeseries_coverage_query(
            ["feature_country"], collect_only=True
        )
        self.assertIn("interval '5 minutes'", query)
        self.assertIn('"country" = \'collect\'', query)
        self.assertIn("missing_sample_count", query)
        self.assertIn("missing_ranges", query)
        self.assertIn("row_number() OVER", query)
        self.assertIn("off_grid_sample_count", query)
        self.assertNotIn("CREATE", query)

    def test_sparse_asn_activity_query_has_no_expected_grid_or_missing_semantics(self):
        query = _build_timeseries_activity_query(
            ["feature_other_202602", 'feature_other_202603"unsafe']
        )
        self.assertIn('"feature_other_202603""unsafe"', query)
        self.assertIn("activity_timestamp_count", query)
        self.assertNotIn("generate_series", query)
        self.assertNotIn("missing", query)
        self.assertNotIn("coverage", query)

    def test_collection_keeps_source_coverage_separate_from_sparse_activity(self):
        source_result = {
            "expected_sample_count": 16992,
            "raw_observed_timestamp_count": 10266,
            "observed_sample_count": 10266,
            "missing_sample_count": 6726,
            "off_grid_sample_count": 0,
            "first_observed_at": "2026-02-24 08:00:00",
            "last_observed_at": "2026-03-31 23:55:00",
            "missing_ranges": [],
            "missing_samples": [],
            "off_grid_samples": [],
        }
        rows = [(source_result,)] + [
            (
                {
                    "activity_timestamp_count": index,
                    "first_activity_at": "2026-02-24 08:00:00",
                    "last_activity_at": "2026-03-31 23:55:00",
                },
            )
            for index in range(1, 12)
        ]
        result = _collect_timeseries_coverage(
            RecordingCursor(rows),
            profile=load_data_profile(),
            sample_limit=20,
        )
        self.assertEqual(len(result["source_series"]), 1)
        self.assertEqual(result["source_series"][0]["series_id"], "feature_country.collect")
        self.assertEqual(len(result["activity_series"]), 11)
        self.assertEqual(result["totals"]["source_missing_sample_count"], 6726)
        for item in result["activity_series"]:
            self.assertEqual(item["series_semantics"], "subject_activity_sparse")
            self.assertNotIn("expected_sample_count", item)
            self.assertNotIn("missing_reason", item)
            self.assertNotIn("coverage_ratio", item)


class StatisticsAndChecksTest(unittest.TestCase):
    def test_column_statistics_keep_zero_and_empty_separate_from_null(self):
        table = {
            "name": "feature_country",
            "columns": [
                {"name": "t", "type_category": "D", "type_name": "timestamp"},
                {"name": "country", "type_category": "S", "type_name": "text"},
                {"name": "announ_num", "type_category": "N", "type_name": "int8"},
                {"name": "payload", "type_category": "U", "type_name": "jsonb"},
            ],
        }
        # base: rows/min/max/outside；随后每列依次 null/empty-string/empty-json/zero。
        row = (
            10,
            "2026-02-24 08:00:00",
            "2026-03-31 23:55:00",
            0,
            0,
            None,
            None,
            None,
            1,
            2,
            None,
            None,
            0,
            None,
            None,
            7,
            3,
            None,
            4,
            None,
        )
        cursor = RecordingCursor([row])
        _apply_table_stats(
            cursor,
            table,
            start=load_data_profile()["parsed"]["start"].replace(tzinfo=None),
            end_exclusive=load_data_profile()["parsed"]["end_exclusive"].replace(tzinfo=None),
        )
        country = table["columns"][1]
        metric = table["columns"][2]
        payload = table["columns"][3]
        self.assertEqual(country["null_count"], 1)
        self.assertEqual(country["empty_string_count"], 2)
        self.assertIsNone(country["zero_count"])
        self.assertEqual(metric["zero_count"], 7)
        self.assertEqual(metric["zero_rate"], 0.7)
        self.assertEqual(payload["empty_json_count"], 4)
        self.assertNotEqual(metric["zero_count"], metric["null_count"])

    def test_valid_primary_key_proves_duplicate_count_without_scan(self):
        cursor = RecordingCursor()
        table = {
            "row_count": 20,
            "primary_key": {
                "validated": True,
                "backing_index_valid": True,
                "columns": ["source", "prefix", "event_id"],
            },
            "columns": [],
        }
        _apply_duplicate_stats(cursor, table)
        self.assertEqual(cursor.executed, [])
        self.assertEqual(table["duplicate_basis"]["kind"], "enforced_primary_key")
        self.assertEqual(table["duplicate_basis"]["duplicate_excess_row_count"], 0)

    def test_unknown_timeseries_gaps_are_pending_not_zero_filled(self):
        table = {
            "name": "feature_country",
            "out_of_window_count": 0,
            "primary_key": {
                "validated": True,
                "backing_index_valid": True,
            },
            "duplicate_basis": {"duplicate_excess_row_count": 0},
            "legacy_schema_md5": "a" * 32,
        }
        checks = _build_checks(
            expected_tables=["feature_country"],
            actual_tables=["feature_country"],
            tables=[table],
            reference_integrity=None,
            timeseries_coverage={
                "totals": {
                    "source_series_with_missing_samples": 1,
                    "source_series_with_off_grid_samples": 0,
                    "source_missing_sample_count": 6726,
                    "source_off_grid_sample_count": 0,
                }
            },
        )
        coverage = next(
            item for item in checks if item["check_id"] == "timeseries.unclassified_missing"
        )
        self.assertEqual(coverage["status"], "pending")
        self.assertEqual(coverage["evidence"]["source_missing_sample_count"], 6726)

    def test_pending_check_prevents_quality_gate_pass(self):
        summary = _quality_gate_summary(
            [
                {"check_id": "one", "status": "pass"},
                {"check_id": "two", "status": "pending"},
            ]
        )
        self.assertEqual(summary["status"], "pending")
        self.assertEqual(summary["blocking_failure_count"], 0)
        self.assertEqual(summary["pending_checks"], ["two"])


class OutputSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def test_output_is_0600_valid_json_and_never_overwritten(self):
        output = self.root / "data-quality" / "p0-quality-probe.json"
        _write_json({"schema_version": 1, "password": None}, str(output))
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["schema_version"], 1)
        with self.assertRaisesRegex(ProbeError, "拒绝覆盖"):
            _write_json({"schema_version": 1}, str(output))

    def test_output_rejects_symlink_ancestor(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "linked"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(ProbeError, "软链接"):
            _write_json({"schema_version": 1}, str(link / "probe.json"))


if __name__ == "__main__":
    unittest.main()
