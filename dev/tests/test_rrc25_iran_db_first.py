from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from dev.data_quality import rrc25_iran_db_first as target


FIXTURE_AFFECTED_ASNS = [
    *[str(value) for value in range(1, 173)],
    "48715",
    "42337",
    "39501",
    "61008",
]


class FakeCursor:
    def __init__(
        self,
        *,
        fact_rows=None,
        country_rows=None,
        as_outage_rows=None,
        prefix_outage_rows=None,
        as_rows=None,
    ):
        self.fact_rows = fact_rows if fact_rows is not None else [make_fact()]
        self.country_rows = country_rows if country_rows is not None else make_country_rows()
        self.as_outage_rows = (
            as_outage_rows
            if as_outage_rows is not None
            else make_as_outage_rows()
        )
        self.prefix_outage_rows = (
            prefix_outage_rows
            if prefix_outage_rows is not None
            else make_prefix_outage_rows()
        )
        self.as_rows = as_rows if as_rows is not None else make_as_rows()
        self.statements = []
        self.current = []
        self.closed = False

    def execute(self, query, params=None):
        self.statements.append((query, params))
        if "SELECT current_user" in query:
            self.current = [
                (
                    "domeye_core_reader",
                    "domeye_core",
                    "on",
                    "on",
                    "repeatable read",
                    "12.22",
                    "123456789",
                    False,
                    False,
                    False,
                    False,
                    False,
                    [],
                    datetime(2026, 7, 23, 1, 2, 3, tzinfo=timezone.utc),
                )
            ]
        elif "has_table_privilege" in query:
            self.current = [
                ("feature_country", True, False),
                ("feature_other_202602", True, False),
                ("feature_other_202603", True, False),
                ("country_outage_202602", True, False),
                ("as_outage_202602", True, False),
                ("prefix_outage_202602", True, False),
            ]
        elif "rrc25_iran_db_first:fact" in query:
            self.current = list(self.fact_rows)
        elif "rrc25_iran_db_first:country_series" in query:
            self.current = list(self.country_rows)
        elif "rrc25_iran_db_first:as_outage_facts" in query:
            self.current = list(self.as_outage_rows)
        elif "rrc25_iran_db_first:prefix_outage_facts" in query:
            self.current = list(self.prefix_outage_rows)
        elif "rrc25_iran_db_first:asn_history" in query:
            self.current = list(self.as_rows)
        else:
            self.current = []

    def fetchone(self):
        return self.current[0] if self.current else None

    def fetchall(self):
        return list(self.current)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.rollback_count = 0
        self.commit_count = 0
        self.closed = False

    def cursor(self):
        return self.fake_cursor

    def rollback(self):
        self.rollback_count += 1

    def commit(self):
        self.commit_count += 1

    def close(self):
        self.closed = True


def make_fact():
    return (
        "r",
        "IR",
        1,
        datetime(2026, 2, 27, 9, 12, 32),
        None,
        None,
        "high",
        0.317,
        176,
        556,
        FIXTURE_AFFECTED_ASNS,
        "北京时间 2026-02-28 22:34:40 , 伊朗发生大规模路由回撤中断",
        "伊朗",
        "高风险",
    )


def make_country_rows():
    anchors = [
        datetime(2026, 2, 28, 0, 0),
        datetime(2026, 2, 28, 6, 35),
        datetime(2026, 2, 28, 18, 45),
        datetime(2026, 2, 28, 22, 30),
        datetime(2026, 2, 28, 22, 35),
        datetime(2026, 2, 28, 22, 40),
        datetime(2026, 2, 28, 22, 45),
        datetime(2026, 2, 28, 22, 50),
        datetime(2026, 2, 28, 22, 55),
        datetime(2026, 2, 28, 23, 0),
    ]
    return [
        (observed_at, 1_000, 200, 256_000, 7, 3)
        for observed_at in anchors
    ]


def make_as_outage_rows():
    rows = []
    for position, asn in enumerate(FIXTURE_AFFECTED_ASNS, start=1):
        if position <= 99:
            started_at = datetime(2026, 2, 28, 18, 45)
        elif position <= 174:
            started_at = datetime(2026, 2, 28, 20, 0)
        else:
            started_at = datetime(2026, 2, 28, 22, 30)
        ratio = 1.0 if position <= 97 else 0.5
        rows.append(
            (
                "r",
                asn,
                1,
                started_at,
                None,
                ratio,
                10 if ratio == 1.0 else 5,
                10,
                "high",
            )
        )
    return sorted(rows, key=lambda row: (row[3], row[1], row[2]))


def _fixture_prefix(index):
    return "10.{}.{}.{}/32".format(
        index // 65_536,
        (index // 256) % 256,
        index % 256,
    )


def make_prefix_outage_rows():
    rows = []
    group_specs = (
        (63, datetime(2026, 2, 28, 6, 35), 3),
        (1_015, datetime(2026, 2, 28, 18, 45), 126),
        (1_697, datetime(2026, 2, 28, 20, 0), 176),
        (67, datetime(2026, 2, 28, 22, 30), 2),
    )
    index = 0
    for count, started_at, asn_population in group_specs:
        for item in range(count):
            asn = FIXTURE_AFFECTED_ASNS[item % asn_population]
            prefix = _fixture_prefix(index)
            if started_at == datetime(2026, 2, 28, 20, 0) and item == 0:
                asn = "61008"
                prefix = "2001:db8::/48"
            rows.append(
                (
                    "r",
                    prefix,
                    1,
                    asn,
                    started_at,
                    None,
                    "high",
                )
            )
            index += 1
    # 同一 prefix/asn 的不同旧事件 ID 造成 41 条重复事实。
    for duplicate in range(41):
        base = rows[63 + 1_015 + duplicate]
        rows.append(
            (
                base[0],
                base[1],
                2,
                base[3],
                base[4],
                base[5],
                base[6],
            )
        )
    return sorted(rows, key=lambda row: (row[4], row[1], row[3], row[2]))


def make_as_rows():
    rows = []
    for asn in [*FIXTURE_AFFECTED_ASNS, "900001", "900002", "900003"]:
        rows.append(
            (
                datetime(2026, 2, 1, 0, 0),
                asn,
                10,
                1,
                2_560,
                1,
                0,
            )
        )
    for asn in FIXTURE_AFFECTED_ASNS:
        rows.append(
            (
                datetime(2026, 2, 28, 22, 35),
                asn,
                0,
                0,
                0,
                0,
                1,
            )
        )
    return rows


def make_profile():
    return {
        "timezone": "Asia/Shanghai",
        "parsed": {
            "start": datetime(2026, 2, 1, tzinfo=target.TIMEZONE),
            "end_exclusive": datetime(2026, 4, 1, tzinfo=target.TIMEZONE),
        },
    }


def make_context():
    return {
        "release_id": "release-20260723T000000Z-abcdef12",
        "system_identifier": "123456789",
        "state_sha256": "1" * 64,
        "manifest_sha256": "2" * 64,
        "database_manifest_sha256": "3" * 64,
        "inventory_sha256": "4" * 64,
        "legacy_schema_hashes": {
            name: hashlib.md5(name.encode()).hexdigest()
            for name in target.REQUIRED_TABLES
        },
    }


def make_config():
    return {
        "DOMEYE_CORE_DB_NAME": "domeye_core",
        "DOMEYE_CORE_DB_READER_USER": "domeye_core_reader",
        "DOMEYE_CORE_DB_READER_PASSWORD": "not-used",
    }


class DBFirstTests(unittest.TestCase):
    def test_exports_fixed_database_snapshot_and_rolls_back(self):
        cursor = FakeCursor()
        connection = FakeConnection(cursor)

        payload = target.export_database(
            connection,
            profile=make_profile(),
            context=make_context(),
            database_config=make_config(),
        )

        self.assertEqual(payload["scope"]["incident_ref"], target.INCIDENT_REF)
        self.assertEqual(
            payload["country_series"]["coverage"]["expected_slot_count"], 1928
        )
        self.assertEqual(
            payload["country_series"]["coverage"]["observed_slot_count"], 10
        )
        self.assertEqual(payload["fact"]["affected_asn_count"], 176)
        self.assertEqual(payload["fact"]["total_asn_count"], 556)
        self.assertTrue(
            payload["fact"]["internal_consistency"][
                "outage_set_count_matches_affected_count"
            ]
        )
        reconciliation = payload["event_fact_reconciliation"]
        self.assertEqual(reconciliation["as_outage"]["unique_asn_count"], 176)
        self.assertEqual(
            reconciliation["as_outage"]["legacy_peak_ratio_1_asn_count"], 97
        )
        self.assertEqual(
            reconciliation["as_outage"][
                "legacy_peak_ratio_between_0_and_1_asn_count"
            ],
            79,
        )
        self.assertEqual(reconciliation["prefix_outage"]["fact_row_count"], 2_883)
        self.assertEqual(
            reconciliation["prefix_outage"]["distinct_prefix_asn_count"], 2_842
        )
        self.assertTrue(
            reconciliation["country_outage_bidirectional_reconciliation"][
                "exact_set_match"
            ]
        )
        self.assertEqual(
            payload["fact"]["temporal_semantics"]["difference_seconds"], 134_528
        )
        self.assertEqual(payload["fact"]["duration"]["value_state"], "unknown")
        buckets = payload["fact_bucket_analysis"]
        self.assertEqual(
            [
                (
                    row["as_outage_fact_count"],
                    row["as_outage_unique_asn_count"],
                    row["prefix_outage_fact_count"],
                    row["prefix_outage_unique_prefix_count"],
                    row["prefix_outage_unique_asn_count"],
                )
                for row in buckets
            ],
            [
                (0, 0, 63, 63, 3),
                (99, 99, 1_015, 1_015, 126),
                (2, 2, 67, 67, 2),
            ],
        )
        self.assertEqual(
            payload["minimal_raw_request"]["representative_asns"],
            ["48715", "42337", "39501", "61008"],
        )
        self.assertEqual(len(payload["minimal_raw_request"]["update_slots"]), 13)
        self.assertEqual(payload["minimal_raw_request"]["status"], "not_executed")
        self.assertTrue(payload["minimal_raw_request"]["representative_prefixes"])
        self.assertEqual(
            payload["metric_findings"]["baseline"]["values"][
                "ipv4_address_equivalent"
            ],
            256_000,
        )
        self.assertEqual(
            payload["execution"]["transaction_finalization"], "rollback_completed"
        )
        self.assertEqual(connection.rollback_count, 1)
        self.assertEqual(connection.commit_count, 0)
        self.assertTrue(cursor.closed)
        self.assertFalse(payload["scope"]["raw_read_performed"])

    def test_business_queries_bind_all_values(self):
        cursor = FakeCursor()
        connection = FakeConnection(cursor)
        target.export_database(
            connection,
            profile=make_profile(),
            context=make_context(),
            database_config=make_config(),
        )

        business = [
            (query, params)
            for query, params in cursor.statements
            if "rrc25_iran_db_first:" in query
        ]
        self.assertEqual(len(business), 5)
        for query, params in business:
            self.assertIsNotNone(params)
            self.assertNotIn("2026-02-27 09:12:32", query)
            self.assertNotIn("'IR'", query)
            self.assertNotIn("'伊朗'", query)
            self.assertNotIn("'r'", query)
        fact_query, fact_params = business[0]
        self.assertGreaterEqual(fact_query.count("%s"), 4)
        self.assertEqual(
            fact_params,
            (
                "r",
                "IR",
                1,
                datetime(2026, 2, 27, 9, 12, 32),
            ),
        )
        self.assertIn("READ ONLY", cursor.statements[0][0])

    def test_missing_fixed_fact_fails_closed_and_rolls_back(self):
        cursor = FakeCursor(fact_rows=[])
        connection = FakeConnection(cursor)

        with self.assertRaisesRegex(target.DBFirstError, "精确命中一条"):
            target.export_database(
                connection,
                profile=make_profile(),
                context=make_context(),
                database_config=make_config(),
            )

        self.assertEqual(connection.rollback_count, 1)
        self.assertEqual(connection.commit_count, 0)
        self.assertTrue(cursor.closed)

    def test_reader_security_failure_happens_before_business_queries(self):
        cursor = FakeCursor()
        connection = FakeConnection(cursor)

        def reject(*_args, **_kwargs):
            raise target.p0_probe.ProbeError("reader 不安全")

        with self.assertRaisesRegex(target.p0_probe.ProbeError, "reader 不安全"):
            target.export_database(
                connection,
                profile=make_profile(),
                context=make_context(),
                database_config=make_config(),
                security_verifier=reject,
            )

        self.assertEqual(connection.rollback_count, 1)
        self.assertFalse(
            any(
                "rrc25_iran_db_first:" in query
                for query, _params in cursor.statements
            )
        )

    def test_publishes_create_only_chinese_summary_and_checksums(self):
        cursor = FakeCursor()
        payload = target.export_database(
            FakeConnection(cursor),
            profile=make_profile(),
            context=make_context(),
            database_config=make_config(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve() / "db-first"
            inventory = target.publish_artifacts(payload, output)
            self.assertEqual(
                {item["name"] for item in inventory["files"]},
                {
                    "iran-db-first.json",
                    "伊朗数据库先行复算摘要.md",
                    "SHA256SUMS",
                },
            )
            loaded = json.loads(
                (output / "iran-db-first.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                loaded["content_fingerprint_sha256"],
                payload["content_fingerprint_sha256"],
            )
            summary = (output / "伊朗数据库先行复算摘要.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("数据库先行复算摘要", summary)
            self.assertIn("2883", summary)
            self.assertIn("2842", summary)
            self.assertIn("97/79", summary)
            self.assertIn("134528", summary)
            checksums = (output / "SHA256SUMS").read_text(encoding="utf-8")
            for name in ("iran-db-first.json", "伊朗数据库先行复算摘要.md"):
                digest = hashlib.sha256((output / name).read_bytes()).hexdigest()
                self.assertIn("{}  {}".format(digest, name), checksums)
            with self.assertRaisesRegex(target.DBFirstError, "拒绝覆盖"):
                target.publish_artifacts(payload, output)

    def test_country_and_as_fact_set_differences_are_bidirectional(self):
        rows = make_as_outage_rows()
        rows = [row for row in rows if row[1] != "172"]
        extra = list(rows[0])
        extra[1] = "999999"
        rows.append(tuple(extra))
        rows.sort(key=lambda row: (row[3], row[1], row[2]))

        analysis, _buckets = target._event_fact_analysis(
            rows, make_prefix_outage_rows(), target._normalize_fact([make_fact()])
        )

        difference = analysis["country_outage_bidirectional_reconciliation"]
        self.assertEqual(difference["missing_from_as_outage_facts"], ["172"])
        self.assertEqual(difference["extra_in_as_outage_facts"], ["999999"])
        self.assertFalse(difference["exact_set_match"])

    def test_content_fingerprint_excludes_runtime_observation_fields(self):
        first = target.export_database(
            FakeConnection(FakeCursor()),
            profile=make_profile(),
            context=make_context(),
            database_config=make_config(),
        )
        second = dict(first)
        second["generated_at_utc"] = "2099-01-01T00:00:00Z"
        second["database_security"] = dict(first["database_security"])
        second["database_security"]["query_started_at"] = "2099-01-01T00:00:01Z"
        second["execution"] = {
            "transaction_mode": "changed_runtime_only",
            "transaction_finalization": "changed_runtime_only",
            "output_semantics": "changed_runtime_only",
        }

        self.assertEqual(
            first["content_fingerprint_sha256"],
            target._content_fingerprint(second),
        )

    def test_direct_script_help_can_import_project_package(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(target.__file__).resolve()),
                "--help",
            ],
            cwd="/",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--output-directory", completed.stdout)

    def test_event_info_embedded_time_is_required(self):
        row = list(make_fact())
        row[11] = "伊朗发生大规模路由回撤中断"
        with self.assertRaisesRegex(target.DBFirstError, "22:34:40"):
            target._normalize_fact([tuple(row)])

    def test_missing_inventory_table_is_rejected_before_cursor(self):
        context = make_context()
        del context["legacy_schema_hashes"]["feature_other_202603"]
        cursor = FakeCursor()
        connection = FakeConnection(cursor)

        with self.assertRaisesRegex(target.DBFirstError, "必需表"):
            target.export_database(
                connection,
                profile=make_profile(),
                context=context,
                database_config=make_config(),
            )

        self.assertEqual(cursor.statements, [])
        self.assertEqual(connection.rollback_count, 0)


if __name__ == "__main__":
    unittest.main()
