from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import shadow_migration_candidate as candidate


class ShadowMigrationCandidateTest(unittest.TestCase):
    def test_legacy_table_inventory_is_frozen(self) -> None:
        self.assertEqual(len(candidate.OLD_TABLE_SPECS), 37)
        self.assertEqual(len(set(candidate.OLD_TABLE_NAMES)), 37)
        self.assertIn("country_outage_202602", candidate.OLD_TABLE_NAMES)
        self.assertIn("country_outage_202603", candidate.OLD_TABLE_NAMES)
        self.assertIn("feature_country", candidate.OLD_TABLE_NAMES)
        self.assertTrue(all(row["time_column"] in {"s_time", "t"} for row in candidate.OLD_TABLE_SPECS))

    def test_legacy_naive_time_is_asia_shanghai_not_utc(self) -> None:
        self.assertEqual(
            candidate.parse_legacy_time("2026-02-27T09:12:32"),
            "2026-02-27T01:12:32Z",
        )
        self.assertIsNone(candidate.parse_legacy_time(None))

    def test_111_legacy_rows_close_as_81_plus_29_plus_1(self) -> None:
        base = datetime(2026, 2, 27, 9, 12, 32)
        rows = []
        incidents = {}
        for index in range(111):
            local = (base + timedelta(minutes=index)).isoformat(timespec="seconds")
            country = "IR" if index < 81 else ("" if index == 110 else "ZZ")
            row = {
                "source": "r", "country": country, "outage_id": index + 1,
                "country_chinese_name": None, "s_time": local, "e_time": None,
                "duration": None, "outage_level": "legacy",
                "outage_level_descr": None, "max_outage_as_ratio": 0.1,
                "max_outage_as_num": 1, "total_as_num": 10,
                "outage_ases": [], "event_info": None,
            }
            rows.append(row)
            reference = f"country_outage/{local.replace('T', ' ')}/{country}/{index + 1}/r"
            if index < 81:
                incidents[reference] = {
                    "incident_id": f"incident_{index:03d}",
                    "legacy_reference": reference, "country_code": country,
                    "legacy_event_time_utc": candidate.parse_legacy_time(local),
                    "source_code": "r", "collector_id": "rrc25",
                    "window_start_utc": candidate.WINDOW_START,
                    "window_end_exclusive_utc": candidate.WINDOW_END,
                }
        legacy, fields, counts = candidate.normalize_country_rows(
            "domeye_data_candidate_v1_" + "a" * 32,
            "shadow_import_batch_v1_" + "b" * 32, rows, incidents,
        )
        self.assertEqual(len(legacy), 111)
        self.assertEqual(len(fields), 666)
        self.assertEqual(counts, {
            "reconciled_to_unified_incident": 81,
            "trace_only_not_in_frozen_publication_registry": 29,
            "quarantined_invalid_country_code": 1,
        })
        self.assertEqual(
            sum(row["comparison"] == "not_comparable" for row in fields), 222,
        )
        self.assertEqual(
            sum(row["disposition_status"] == "quarantined" for row in fields), 6,
        )
        self.assertTrue(all(row["import_batch_id"].startswith("shadow_import_batch_v1_") for row in legacy))

    def test_release_bundle_has_one_pointer_target_and_no_second_state(self) -> None:
        candidate_id = "domeye_data_candidate_v1_" + "b" * 32
        sources = {}
        for index, stage in enumerate(("s1", "s2", "s3", "s4", "s5"), 1):
            sources[stage] = {
                "root": f"/formal/{stage}", "manifest_sha256": str(index) * 64,
                "manifest": {
                    "dataset_id": f"{stage}_dataset", "content_sha256": str(index) * 64,
                    "schema_version": f"{stage}/v1",
                },
            }
        objects, refs, bundles, bindings, bundle_ids = candidate.make_release_rows(
            candidate_id, "shadow_migration_dataset_v1_" + "c" * 32,
            "d" * 64, "e" * 64, sources, {"s3": {}, "s4": {}, "s5": {}},
        )
        self.assertEqual((len(objects), len(refs), len(bundles), len(bindings)), (10, 8, 3, 9))
        unified = next(row for row in bundles if row["bundle_id"] == bundle_ids["unified"])
        self.assertEqual(unified["bundle_mode"], "unified")
        self.assertEqual(set(unified["coherent_components"]), {
            "candidate_id", "route_event_dataset_id", "route_state_dataset_id",
            "metric_dataset_id", "event_publication_dataset_id", "read_model_dataset_id",
            "migration_dataset_id", "collector_id", "window_start_utc",
            "window_end_exclusive_utc", "state_point_count", "country_bucket_count",
        })
        route_state_objects = [row for row in objects if row["object_kind"] == "route_state_evidence"]
        self.assertEqual(len(route_state_objects), 1)
        legacy = next(row for row in objects if row["object_kind"] == "legacy_postgresql_snapshot")
        self.assertFalse(legacy["runtime_readable"])

    def test_all_37_legacy_tables_receive_534_field_dispositions(self) -> None:
        tables = []
        for index in range(37):
            column_count = 30 if index == 36 else 14
            columns = [
                {
                    "column_name": "source" if column == 0 else (
                        "t" if column == 1 else f"field_{column}"
                    ),
                    "data_type": "text", "is_nullable": "YES",
                }
                for column in range(column_count)
            ]
            tables.append({
                "source_table": f"table_{index:02d}",
                "semantic_family": "feature_country",
                "source_time_column": "t", "scope_row_count": index,
                "disposition": "trace_only_legacy_metric_semantics_not_comparable",
                "payload": {"schema_fragment": {"columns": columns}},
            })
        rows = candidate.build_source_field_reconciliation(
            "domeye_data_candidate_v1_" + "c" * 32,
            "shadow_import_batch_v1_" + "d" * 32,
            tables,
        )
        self.assertEqual(len(rows), 534)
        self.assertTrue(all(row["disposition_status"] == "closed" for row in rows))
        self.assertEqual(sum(row["comparison"] == "mapped" for row in rows), 74)

    def test_ddl_enforces_atomic_pointer_and_runtime_view(self) -> None:
        ddl = (Path(__file__).parent / "sql" / "rrc25-shadow-migration-store-v1.sql").read_text()
        for phrase in (
            "release pointer may change only through atomic switch",
            "target release bundle is not complete",
            "target release bundle has non-formal object",
            "stale release pointer version",
            "CREATE VIEW domeye_runtime.selected_release",
            "CREATE VIEW domeye_control.retention_eligibility",
            "selected_by_production boolean NOT NULL DEFAULT false CHECK (NOT selected_by_production)",
        ):
            self.assertIn(phrase, ddl)

    def test_psql_script_markers_are_machine_parseable(self) -> None:
        result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=b"BEGIN\nM\t7b2278223a317d\nS\t7b22736f757263655f7461626c65223a2274227d\nCOMMIT\n",
            stderr=b"",
        )
        self.assertEqual(candidate._hex_json_lines(result, "M"), [{"x": 1}])
        self.assertEqual(candidate._hex_json_lines(result, "S"), [{"source_table": "t"}])

    def test_dlae_16_is_written_only_after_runtime_acceptance(self) -> None:
        source = Path(candidate.__file__).read_text()
        runtime_index = source.index("runtime_result = runtime_acceptance(runtime)")
        dlae16_index = source.index("insert_dlae_evidence(database, manifest, (16,))")
        summary_index = source.index("summary = database_summary(database)", runtime_index)
        self.assertLess(runtime_index, dlae16_index)
        self.assertLess(dlae16_index, summary_index)

    def test_contract_schemas_are_valid_json(self) -> None:
        contracts = Path(__file__).parents[2] / "contracts" / "data"
        paths = sorted(contracts.glob("rrc25-shadow-migration*.schema.json"))
        self.assertEqual(len(paths), 2)
        for path in paths:
            self.assertIsInstance(json.loads(path.read_text()), dict)


if __name__ == "__main__":
    unittest.main()
