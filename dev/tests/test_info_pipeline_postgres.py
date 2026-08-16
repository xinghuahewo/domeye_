import os
import csv
import json
import subprocess
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from backend.info_pipeline import full_loader, manifest as manifest_module
from backend.info_pipeline.full_loader import load_full_files
from backend.info_pipeline.loader import LoadError, load_core_files
from backend.info_pipeline.manifest import build_manifest
from backend.info_pipeline.quality import probe_core_files
from backend.info_pipeline.shadow import compare_shadow_backends
from dev.tests.test_info_pipeline import InfoFixture, _fake_xls_inspector


ROOT = Path(__file__).resolve().parents[2]
SQL_V1 = ROOT / "deploy" / "database" / "sql" / "info-schema-v1.sql"
SQL_V2 = ROOT / "deploy" / "database" / "sql" / "info-schema-v2.sql"
SQL_INTEGRITY = (
    ROOT / "deploy" / "database" / "sql" / "validate-integrity.sql"
)


class LocalPsql:
    """仅供显式启用的本机临时 PostgreSQL 集成测试使用。"""

    def __init__(self, database: str) -> None:
        self._command = [
            "psql",
            "-h",
            os.environ.get("DOMEYE_TEST_PGHOST", "/tmp"),
            "-p",
            os.environ.get("DOMEYE_TEST_PGPORT", "5432"),
            "-X",
            "--quiet",
            "--no-align",
            "--tuples-only",
            "--set",
            "ON_ERROR_STOP=1",
            "--dbname",
            database,
        ]

    def _run(self, *, input_value, text: bool, capture: bool = False) -> str:
        result = subprocess.run(
            self._command,
            input=input_value,
            text=text,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            raise LoadError(f"本机测试 SQL 执行失败：{detail[-4000:]}")
        output = result.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return output.strip()

    def execute(self, sql: str, *, capture: bool = False) -> str:
        return self._run(input_value=sql, text=True, capture=capture)

    def execute_file(self, path: Path) -> None:
        self._run(input_value=path.read_bytes(), text=False)

    def copy_stage(self, head_sql, spool, tail_sql) -> None:
        self.copy_streams((head_sql, spool, "\\.\n", tail_sql))

    def copy_streams(self, segments) -> None:
        payload_parts = []
        for segment in segments:
            if isinstance(segment, str):
                payload_parts.append(segment)
            else:
                segment.seek(0)
                payload_parts.append(segment.read())
        payload = "".join(payload_parts)
        self._run(input_value=payload, text=True)

    @contextmanager
    def csv_rows(self, query):
        result = subprocess.run(
            self._command,
            input=(
                "COPY (\n"
                + query.strip().rstrip(";")
                + "\n) TO STDOUT WITH (FORMAT CSV, ENCODING 'UTF8');\n"
            ),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            raise LoadError(
                f"本机测试 COPY 查询失败：{result.stderr[-4000:]}"
            )
        yield csv.reader(result.stdout.splitlines(), strict=True)


@unittest.skipUnless(
    os.environ.get("DOMEYE_TEST_LOCAL_POSTGRES") == "1",
    "仅在显式指定隔离的本机 PostgreSQL 时运行",
)
class FullPipelinePostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = os.environ.get("DOMEYE_TEST_PGHOST", "/tmp")
        cls.port = os.environ.get("DOMEYE_TEST_PGPORT", "5432")
        cls.database = "domeye_info_test_" + uuid.uuid4().hex[:16]
        cls._admin("CREATE DATABASE " + cls.database)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "database"):
            cls._admin(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                f"WHERE datname = '{cls.database}'"
            )
            cls._admin("DROP DATABASE IF EXISTS " + cls.database)

    @classmethod
    def _admin(cls, sql):
        result = subprocess.run(
            [
                "psql",
                "-h",
                cls.host,
                "-p",
                cls.port,
                "-X",
                "--quiet",
                "--set",
                "ON_ERROR_STOP=1",
                "--dbname",
                "postgres",
                "--command",
                sql,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-4000:])

    def test_s1_to_s2_reconciles_and_remains_inactive(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            InfoFixture(source)
            database = LocalPsql(self.database)
            with mock.patch.object(
                manifest_module,
                "_inspect_xls",
                side_effect=_fake_xls_inspector,
            ), mock.patch.object(
                full_loader,
                "_iter_xls",
                side_effect=lambda _path: iter(
                    [
                        (
                            1,
                            {
                                "asn": "64500",
                                "org_name": "测试组织",
                                "country": "CN",
                            },
                        )
                    ]
                ),
            ):
                manifest = build_manifest(
                    source,
                    source_release_label="postgres-integration",
                )
                quality = probe_core_files(source, manifest)
                s1 = load_core_files(
                    source,
                    manifest,
                    quality,
                    database,
                    schema_sql=SQL_V1,
                    code_commit="integration-test",
                )
                repeated_s1 = load_core_files(
                    source,
                    manifest,
                    quality,
                    database,
                    schema_sql=SQL_V1,
                    code_commit="integration-test",
                )
                s2, full_quality = load_full_files(
                    source,
                    manifest,
                    database,
                    schema_sql=SQL_V2,
                )
                repeated, repeated_quality = load_full_files(
                    source,
                    manifest,
                    database,
                    schema_sql=SQL_V2,
                )
                shadow_report = compare_shadow_backends(
                    source,
                    manifest,
                    database,
                )

            self.assertEqual(s1["status"], "completed")
            self.assertEqual(repeated_s1["status"], "already_completed")
            self.assertEqual(
                repeated_s1["manifest_sha256"],
                manifest["manifest_sha256"],
            )
            self.assertEqual(
                repeated_s1["database_release_status"],
                "validating",
            )
            self.assertFalse(repeated_s1["activated"])
            self.assertEqual(s2["status"], "completed")
            self.assertEqual(repeated["status"], "already_completed")
            self.assertEqual(full_quality["status"], "pass")
            self.assertEqual(
                full_quality["quarantine_mirror_failure_count"],
                0,
            )
            self.assertEqual(
                full_quality["accepted_record_visibility_failure_count"],
                0,
            )
            self.assertEqual(
                full_quality["source_role_activation_failure_count"],
                0,
            )
            self.assertEqual(repeated_quality, full_quality)
            self.assertEqual(
                shadow_report["status"],
                "pass",
                json.dumps(
                    shadow_report["sections"],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
            self.assertEqual(
                shadow_report[
                    "deterministic_query_unapproved_difference_count"
                ],
                0,
            )
            self.assertEqual(
                shadow_report["full_set_unapproved_difference_count"],
                0,
            )
            self.assertEqual(
                shadow_report["snapshot_unapproved_difference_count"],
                0,
            )
            self.assertEqual(
                shadow_report["contact_plaintext_exposure_count"],
                0,
            )
            self.assertEqual(s2["source_file_count"], 24)
            self.assertEqual(s2["reconciled_source_file_count"], 24)
            self.assertEqual(s2["unreconciled_record_count"], 0)
            self.assertEqual(s2["visible_record_traceability_percent"], 100)
            self.assertEqual(
                s2["quarantine_reason_coverage_percent"],
                100,
            )
            integrity_input = (
                "\\set data_start 2024-01-01\n"
                "\\set snapshot_time 2024-01-31T00:00:00Z\n"
                "\\set snapshot_month 202401\n"
                + SQL_INTEGRITY.read_text(encoding="utf-8")
            )
            integrity_output = database._run(
                input_value=integrity_input,
                text=True,
                capture=True,
            )
            integrity = json.loads(integrity_output)
            self.assertTrue(integrity["static_info"]["ok"])
            self.assertEqual(
                integrity["static_info"]["reconciliation_failure_count"],
                0,
            )
            state = database.execute(
                "SELECT status || '|' || "
                "(EXISTS (SELECT 1 FROM info.active_release))::text "
                "FROM info.dataset_release;",
                capture=True,
            )
            self.assertEqual(state, "validating|false")
            trace_state = database.execute(
                "SELECT "
                "(SELECT count(*) FROM info.source_record "
                " WHERE restricted_payload IS NOT NULL) || '|' || "
                "(SELECT count(*) FROM info.mapping_record) || '|' || "
                "(SELECT count(*) FROM info.mapping_record "
                " WHERE item_count = 0);",
                capture=True,
            )
            restricted_count, mapping_count, empty_mapping_count = (
                int(value) for value in trace_state.split("|")
            )
            self.assertEqual(restricted_count, 20)
            self.assertGreater(mapping_count, 0)
            self.assertGreater(empty_mapping_count, 0)

            database.execute(
                "UPDATE info.legacy_record SET source_active = true;"
            )
            database.execute(
                "DELETE FROM info.legacy_record "
                "WHERE (release_sk, source_file_sk, source_row_no) = ("
                "SELECT release_sk, source_file_sk, source_row_no "
                "FROM info.legacy_record ORDER BY source_file_sk, source_row_no "
                "LIMIT 1);"
            )
            release_sk = int(
                database.execute(
                    "SELECT release_sk FROM info.dataset_release;",
                    capture=True,
                )
            )
            corrupted_quality = full_loader._collect_full_quality(
                database,
                manifest,
                release_sk,
            )
            self.assertEqual(corrupted_quality["status"], "fail")
            self.assertGreater(
                corrupted_quality["source_role_activation_failure_count"],
                0,
            )
            self.assertGreater(
                corrupted_quality[
                    "accepted_record_visibility_failure_count"
                ],
                0,
            )
            corrupted_integrity = json.loads(
                database._run(
                    input_value=integrity_input,
                    text=True,
                    capture=True,
                )
            )
            self.assertFalse(corrupted_integrity["static_info"]["ok"])
            self.assertGreater(
                corrupted_integrity["static_info"][
                    "reconciliation_failure_count"
                ],
                0,
            )


if __name__ == "__main__":
    unittest.main()
