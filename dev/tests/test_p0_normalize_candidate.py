import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dev.data_profile import load_data_profile
from dev.data_quality.p0_normalize_candidate import (
    CandidateError,
    DeterministicGzipJsonlWriter,
    EVENT_TYPES,
    FACT_SPECS,
    _SidecarIndex,
    _ambiguity_query,
    _classify_quarantine,
    _collision_query,
    _count_query,
    _duplicate_event_query,
    _event_join_query,
    _malformed_event_query,
    _normalizer_source_hashes,
    _orphan_query,
    _preflight_exceptions,
    _source_table_names,
    _write_incidents,
    _write_orphans,
    normalize_candidate,
)
from backend.data_pipeline import normalize as normalizer


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RecordingCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.executed = []
        self.closed = False

    def execute(self, query, parameters=None):
        self.executed.append((query, parameters))
        self.connection.executed.append((query, parameters))
        if "AS table_name" in query and "row_count" in query:
            self.rows = [(name, 0) for name in _source_table_names(("202602", "202603"))]
        else:
            self.rows = []

    def fetchall(self):
        return list(self.rows)

    def fetchmany(self, size=1000):
        if not self.rows:
            return []
        chunk, self.rows = self.rows[:size], self.rows[size:]
        return chunk

    def close(self):
        self.closed = True


class RecordingConnection:
    def __init__(self):
        self.executed = []
        self.cursors = []
        self.rollback_count = 0

    def cursor(self, *args, **kwargs):
        cursor = RecordingCursor(self)
        self.cursors.append(cursor)
        return cursor

    def rollback(self):
        self.rollback_count += 1


def security_result(**overrides):
    result = {
        "database": "bgp_project",
        "system_identifier": "7663836852697006116",
        "transaction_read_only": True,
        "transaction_isolation": "repeatable read",
    }
    result.update(overrides)
    return result


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


class DeterministicArtifactTest(unittest.TestCase):
    def test_normalizer_provenance_rejects_appledouble_python_files(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            root = Path(temporary)
            package = root / "backend" / "data_pipeline" / "normalize"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("\n", encoding="utf-8")
            (package / "facts.py").write_text("VALUE = 1\n", encoding="utf-8")
            hashes = _normalizer_source_hashes(root)
            self.assertEqual(
                sorted(hashes),
                [
                    "backend/data_pipeline/normalize/__init__.py",
                    "backend/data_pipeline/normalize/facts.py",
                ],
            )
            (package / "._facts.py").write_bytes(b"appledouble")
            with self.assertRaisesRegex(CandidateError, "额外=.*._facts.py"):
                _normalizer_source_hashes(root)

    def test_gzip_jsonl_is_byte_deterministic_and_uses_utc_z(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            root = Path(temporary)
            left = root / "left.jsonl.gz"
            right = root / "right.jsonl.gz"
            records = [
                {"b": 2, "a": 1},
                {"event_time": "2026-03-01T00:00:00Z", "value": None},
            ]
            inventories = []
            for path in (left, right):
                writer = DeterministicGzipJsonlWriter(path)
                for record in records:
                    writer.write(record)
                writer.close()
                inventories.append(writer.inventory())
            self.assertEqual(left.read_bytes(), right.read_bytes())
            self.assertEqual(inventories[0]["content_sha256"], inventories[1]["content_sha256"])
            self.assertEqual(inventories[0]["sha256"], hashlib.sha256(left.read_bytes()).hexdigest())
            self.assertEqual(gzip.decompress(left.read_bytes()).decode("utf-8"),
                             '{"a":1,"b":2}\n{"event_time":"2026-03-01T00:00:00Z","value":null}\n')
            # gzip MTIME 四字节必须固定为 0，且头部不携带源文件名。
            self.assertEqual(left.read_bytes()[4:8], b"\x00\x00\x00\x00")
            self.assertFalse(left.read_bytes()[3] & 0x08)

    def test_existing_output_directory_is_rejected(self):
        profile = load_data_profile()
        connection = RecordingConnection()
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            output = Path(temporary) / "candidate"
            output.mkdir()
            with self.assertRaisesRegex(CandidateError, "必须新建"):
                normalize_candidate(
                    connection,
                    profile=profile,
                    context=context_fixture(),
                    database_config={
                        "DOMEYE_CORE_DB_READER_USER": "domeye_core_reader",
                        "DOMEYE_CORE_DB_NAME": "bgp_project",
                    },
                    provenance={},
                    project_root=PROJECT_ROOT,
                    output_dir=output,
                    max_events=1,
                    security_verifier=lambda *args, **kwargs: security_result(),
                )
        self.assertFalse(connection.executed)


class ReadonlyTransactionTest(unittest.TestCase):
    def run_candidate(self, output, connection, preflight):
        with patch(
            "dev.data_quality.p0_normalize_candidate._preflight_exceptions",
            side_effect=preflight,
        ), patch("dev.data_quality.p0_normalize_candidate._write_orphans"), patch(
            "dev.data_quality.p0_normalize_candidate._write_malformed_events"
        ), patch("dev.data_quality.p0_normalize_candidate._write_incidents"):
            return normalize_candidate(
                connection,
                profile=load_data_profile(),
                context=context_fixture(),
                database_config={
                    "DOMEYE_CORE_DB_READER_USER": "domeye_core_reader",
                    "DOMEYE_CORE_DB_NAME": "bgp_project",
                },
                provenance={"git_sha": "a" * 40},
                project_root=PROJECT_ROOT,
                output_dir=output,
                max_events=1,
                security_verifier=lambda *args, **kwargs: security_result(),
            )

    def test_success_begins_readonly_repeatable_read_and_always_rolls_back(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            connection = RecordingConnection()
            output = Path(temporary) / "candidate"
            manifest = self.run_candidate(output, connection, lambda *args, **kwargs: None)
            statements = [query.strip() for query, _ in connection.executed]
            self.assertEqual(statements[0], "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            self.assertGreaterEqual(connection.rollback_count, 1)
            self.assertTrue(output.is_dir())
            self.assertTrue(manifest["sample"]["enabled"])
            self.assertFalse(manifest["admission"]["eligible_for_release_gate"])
            self.assertEqual((output / "manifest.json").stat().st_mode & 0o777, 0o440)

    def test_exception_still_rolls_back_and_removes_partial_staging(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            connection = RecordingConnection()
            output = Path(temporary) / "candidate"

            def explode(*args, **kwargs):
                raise RuntimeError("fixture failure")

            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                self.run_candidate(output, connection, explode)
            self.assertGreaterEqual(connection.rollback_count, 1)
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(temporary).iterdir()), [])


class ReconciliationTest(unittest.TestCase):
    def test_orphan_scan_forces_hash_join_then_restores_planner_defaults(self):
        class EmptyIndex:
            def commit(self):
                pass

        connection = RecordingConnection()
        _write_orphans(
            connection,
            months=(),
            normalizer=normalizer,
            window_start="2026-02-01T00:00:00+08:00",
            window_end="2026-04-01T00:00:00+08:00",
            quarantine_writer=object(),
            index=EmptyIndex(),
            counters={},
        )
        statements = [" ".join(query.split()) for query, _ in connection.executed]
        self.assertEqual(
            statements,
            [
                "SET LOCAL enable_mergejoin = off",
                "SET LOCAL enable_nestloop = off",
                "SET LOCAL enable_mergejoin = on",
                "SET LOCAL enable_nestloop = on",
            ],
        )

    def test_known_country_orphan_is_quarantined_with_both_reasons(self):
        row = {
            "source_table": "country_outage_202603",
            "source": "r",
            "country": "",
            "outage_id": 1,
            "s_time": "2026-03-10T08:24:31Z",
            "event_info": "北京时间 2026-04-26 00:24:38 发生异常",
        }
        record = _classify_quarantine(
            normalizer,
            fact=row,
            window_start="2026-02-01T00:00:00+08:00",
            window_end="2026-04-01T00:00:00+08:00",
        )
        self.assertEqual(record["record_kind"], "fact_record")
        self.assertEqual(
            record["reason_codes"],
            ["invalid_identity", "legacy_window_contamination"],
        )
        self.assertIsNone(record["causal_conclusion"])
        self.assertEqual(record["legacy_payload"]["s_time"], "2026-03-10T08:24:31Z")

    def test_collision_preflight_uses_public_builder_and_disk_index(self):
        class CollisionCursor:
            def __init__(self):
                self.rows = []

            def execute(self, query, parameters=None):
                if "HAVING count(*) > 1" in query:
                    self.rows = []
                elif parameters == ("hijack", "202603") and "grouped_refs" in query:
                    self.rows = [
                        (
                            "80.244.11.0-24",
                            "1",
                            "r",
                            {"source": "r", "prefix": "80.244.11.0/24", "hijack_eventid": 1},
                            [
                                "hijack/2026-03-04 19:35:43/80.244.11.0-24/1/r",
                                "hijack/2026-03-20 14:11:03/80.244.11.0-24/1/r",
                            ],
                        )
                    ]
                else:
                    self.rows = []

            def fetchall(self):
                return list(self.rows)

        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            root = Path(temporary)
            index = _SidecarIndex(root / "index.sqlite3")
            writer = DeterministicGzipJsonlWriter(root / "collisions.jsonl.gz")
            counters = {
                "duplicate_event_reference_count": 0,
                "ambiguous_locator_group_count": 0,
                "collision_group_count": 0,
                "collision_incident_count": 0,
            }
            try:
                _preflight_exceptions(
                    CollisionCursor(),
                    months=("202602", "202603"),
                    normalizer=normalizer,
                    index=index,
                    collision_writer=writer,
                    counters=counters,
                )
                writer.close()
                self.assertEqual(counters["collision_group_count"], 1)
                self.assertEqual(counters["collision_incident_count"], 2)
                payload = json.loads(gzip.decompress((root / "collisions.jsonl.gz").read_bytes()))
                self.assertEqual(payload["field_status"], "source_fact_collision")
                exception = index.get_exception(
                    '["hijack_202603","80.244.11.0-24","1","r"]'
                )
                self.assertEqual(exception[0], "collision")
            finally:
                writer.close()
                index.close()

    def test_prequarantined_malformed_event_is_not_processed_as_incident(self):
        detail_url = "hijack/2026-03-04 19:35:43/80.244.11.0-24/1/r"

        class OneEventCursor:
            def __init__(self):
                self.rows = []

            def execute(self, query, parameters=None):
                if parameters and parameters[-1] == "hijack":
                    self.rows = [
                        (
                            {
                                "source_table": "event_table_202603",
                                "source": "r",
                                "event_type": "错误声明类型",
                                "level": "high",
                                "s_time": "2026-03-04T11:35:43Z",
                                "detail_url": detail_url,
                            },
                            None,
                            {},
                        )
                    ]
                else:
                    self.rows = []

            def fetchmany(self, size=1000):
                rows, self.rows = self.rows, []
                return rows

            def close(self):
                pass

        class OneEventConnection:
            def cursor(self, *args, **kwargs):
                return OneEventCursor()

        class MemoryWriter:
            def __init__(self):
                self.records = []

            def write(self, record):
                self.records.append(record)

        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            index = _SidecarIndex(Path(temporary) / "index.sqlite3")
            index.put_quarantined_event("202603", detail_url)
            incident_writer = MemoryWriter()
            link_writer = MemoryWriter()
            quarantine_writer = MemoryWriter()
            counters = {
                "incident_count": 0,
                "link_count": 0,
                "forward_missing_count": 0,
                "forward_ambiguous_count": 0,
                "forward_time_mismatch_count": 0,
                "fact_link_status_counts": {},
                "event_type_counts": {},
                "malformed_or_mismatched_event_count": 0,
                "quarantine_reason_counts": {},
                "quarantined_duplicate_event_count": 0,
            }
            try:
                _write_incidents(
                    OneEventConnection(),
                    months=("202603",),
                    normalizer=normalizer,
                    incident_writer=incident_writer,
                    link_writer=link_writer,
                    quarantine_writer=quarantine_writer,
                    index=index,
                    counters=counters,
                    max_events=None,
                    window_start="2026-02-01T00:00:00+08:00",
                    window_end="2026-04-01T00:00:00+08:00",
                )
            finally:
                index.close()
            self.assertEqual(incident_writer.records, [])
            self.assertEqual(link_writer.records, [])
            self.assertEqual(quarantine_writer.records, [])
            self.assertEqual(counters["incident_count"], 0)

    def test_single_fact_with_wrong_start_time_is_unresolved_not_selected(self):
        detail_url = "hijack/2026-03-04 19:35:43/80.244.11.0-24/1/r"

        class MismatchCursor:
            def __init__(self):
                self.rows = []

            def execute(self, query, parameters=None):
                if parameters and parameters[-1] == "hijack":
                    self.rows = [
                        (
                            {
                                "source_table": "event_table_202603",
                                "source": "r",
                                "event_type": "前缀劫持",
                                "level": "high",
                                "s_time": "2026-03-04T11:35:43Z",
                                "detail_url": detail_url,
                            },
                            {
                                "source_table": "hijack_202603",
                                "source": "r",
                                "prefix": "80.244.11.0/24",
                                "hijack_eventid": 1,
                                "s_time": "2026-03-20T06:11:03Z",
                                "e_time": None,
                                "duration": None,
                                "hijack_level": "high",
                            },
                            {},
                        )
                    ]
                else:
                    self.rows = []

            def fetchmany(self, size=1000):
                rows, self.rows = self.rows, []
                return rows

            def close(self):
                pass

        class MismatchConnection:
            def cursor(self, *args, **kwargs):
                return MismatchCursor()

        class MemoryWriter:
            def __init__(self):
                self.records = []

            def write(self, record):
                self.records.append(record)

        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            index = _SidecarIndex(Path(temporary) / "index.sqlite3")
            incident_writer = MemoryWriter()
            link_writer = MemoryWriter()
            quarantine_writer = MemoryWriter()
            counters = {
                "incident_count": 0,
                "link_count": 0,
                "forward_missing_count": 0,
                "forward_ambiguous_count": 0,
                "forward_time_mismatch_count": 0,
                "fact_link_status_counts": {},
                "event_type_counts": {},
                "malformed_or_mismatched_event_count": 0,
                "quarantine_reason_counts": {},
                "quarantined_duplicate_event_count": 0,
            }
            try:
                _write_incidents(
                    MismatchConnection(),
                    months=("202603",),
                    normalizer=normalizer,
                    incident_writer=incident_writer,
                    link_writer=link_writer,
                    quarantine_writer=quarantine_writer,
                    index=index,
                    counters=counters,
                    max_events=None,
                    window_start="2026-02-01T00:00:00+08:00",
                    window_end="2026-04-01T00:00:00+08:00",
                )
            finally:
                index.close()
            self.assertEqual(incident_writer.records[0]["fact_link_status"], "unresolved")
            self.assertIsNone(link_writer.records[0]["matched_source_primary_key"])
            self.assertEqual(link_writer.records[0]["unresolved_reasons"], ["fact_start_time_mismatch"])
            self.assertEqual(counters["forward_time_mismatch_count"], 1)

    def test_all_postgresql_builders_are_select_only(self):
        months = ("202602", "202603")
        queries = [_duplicate_event_query(months), _count_query(_source_table_names(months))]
        for month in months:
            queries.append(_malformed_event_query(month))
            for event_type in EVENT_TYPES:
                queries.extend(
                    (
                        _ambiguity_query(month, event_type),
                        _collision_query(month, event_type, months),
                        _orphan_query(month, event_type, months),
                        _event_join_query(month, event_type),
                    )
                )
        forbidden = (" INSERT ", " UPDATE ", " DELETE ", " TRUNCATE ", " ALTER ", " DROP ", " COMMIT")
        for query in queries:
            normalized = " " + " ".join(query.upper().split()) + " "
            for token in forbidden:
                self.assertNotIn(token, normalized)


if __name__ == "__main__":
    unittest.main()
