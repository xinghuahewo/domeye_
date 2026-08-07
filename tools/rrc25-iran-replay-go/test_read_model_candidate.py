from __future__ import annotations

import gzip
import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("read_model_candidate.py")
SPEC = importlib.util.spec_from_file_location("read_model_candidate", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class ReadModelCandidateTests(unittest.TestCase):
    def test_deterministic_gzip_and_snapshot_hash(self) -> None:
        raw = b'{"value":1}\n'
        first = module.deterministic_gzip(raw)
        second = module.deterministic_gzip(raw)
        self.assertEqual(first, second)
        self.assertEqual(gzip.decompress(first), raw)
        payload = {"schema_version": "test/v1", "value": 1, "snapshot_sha256": ""}
        digest = module.snapshot_hash(payload)
        payload["snapshot_sha256"] = digest
        self.assertEqual(module.snapshot_hash(payload), digest)
        payload["value"] = 2
        self.assertNotEqual(module.snapshot_hash(payload), digest)

    def test_postgresql_utc_rendering_is_normalized_before_identity(self) -> None:
        value = {
            "at": "2026-03-11T00:00:00+00:00",
            "nested": ["2026-02-24T00:00:00+00:00", "unchanged"],
        }
        self.assertEqual(
            module.normalize_utc_strings(value),
            {
                "at": "2026-03-11T00:00:00Z",
                "nested": ["2026-02-24T00:00:00Z", "unchanged"],
            },
        )

    def test_go_struct_json_order_is_not_rewritten_during_evidence_audit(self) -> None:
        row = {"collector_id": "rrc25", "peer_ip": "192.0.2.1", "peer_asn": 64496}
        self.assertEqual(
            module.ordered_json_bytes(row),
            b'{"collector_id":"rrc25","peer_ip":"192.0.2.1","peer_asn":64496}',
        )
        self.assertNotEqual(
            module.ordered_json_bytes(row), module.canonical_bytes(row),
        )
        catalog = {
            "schema_version": "rrc25-prefix-vp-evidence-catalog/v1",
            "status": "complete",
            "content_sha256": "value",
        }
        semantic = dict(catalog)
        semantic["content_sha256"] = ""
        self.assertTrue(module.ordered_json_bytes(semantic).startswith(b'{"schema_version"'))

    def test_report_refresh_creates_new_version_without_rewriting_v1(self) -> None:
        event = {
            "incident": {
                "incident_id": "incident_test", "legacy_reference": "country_outage/test/IR/1/r",
                "country_code": "IR", "country_name": "伊朗", "event_type": "country_outage",
                "detected_at": "2026-02-27T00:00:00Z", "source_system": "test",
            },
            "pointer": {"pointer_version": 3},
            "observation": {
                "publication_id": "observation_test", "revision": 3,
                "sequence_in_revision": 1, "data_through": module.WINDOW_END,
                "fact_set_sha256": "a" * 64, "content_sha256": "b" * 64,
                "snapshot": {
                    "quality": {"state": "complete", "gap": "none"},
                    "metric_dataset_id": "metric_test", "limitations": ["观测限制"],
                },
            },
            "analysis": {
                "publication_id": "analysis_test", "revision": 3,
                "sequence_in_revision": 1, "data_through": module.WINDOW_END,
                "derived_from_observation_publication_id": "observation_test",
                "content_sha256": "c" * 64,
                "snapshot": {"trend_profile": {"direction": "down"}, "limitations": []},
            },
            "facts": [
                {"stage": "detected"}, {"stage": "ongoing"}, {"stage": "final"},
            ],
        }
        series = {
            "series_id": "series_test", "artifact_uri": "series/IR.json.gz",
            "artifact_sha256": "d" * 64, "content_sha256": "e" * 64,
            "compressed_size_bytes": 123, "point_count": module.STATE_POINT_COUNT,
        }
        evidence = {
            "evidence_view_id": "evidence_test", "publication_id": "observation_test",
            "derived_from_route_state_id": "route_state_checkpoint_test",
            "projector_version": "1.0.0", "content_sha256": "f" * 64,
            "row_count": 10, "page_count": 1,
            "payload": {"limitations": ["派生视图限制"]},
        }
        event_rows, reports, pointers, _, _ = module.build_event_and_reports(
            "candidate_test", "dataset_test", [event], {"IR": series},
            {"incident_test": evidence},
        )
        self.assertEqual(len(event_rows), 1)
        self.assertEqual(len(reports), 2)
        self.assertEqual([row["report_version"] for row in reports], [1, 2])
        self.assertNotEqual(reports[0]["snapshot_sha256"], reports[1]["snapshot_sha256"])
        self.assertEqual(pointers[0]["current_report_snapshot_id"], reports[1]["report_snapshot_id"])
        self.assertEqual(
            reports[0]["payload"]["observation_publication"]["publication_id"],
            reports[1]["payload"]["observation_publication"]["publication_id"],
        )

    def test_tsv_round_trip_preserves_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.tsv.gz"
            meta = module.write_tsv_gzip(
                path, ("id", "payload"),
                [{"id": "row1", "payload": {"中文": "值", "number": 1}}],
            )
            rows = module.read_tsv_gzip(path)
            self.assertEqual(meta["row_count"], 1)
            self.assertEqual(json.loads(rows[0]["payload"])["中文"], "值")

    def test_http_handler_reads_only_compiled_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = module.deterministic_gzip(b'{"rows":[]}\n')
            page_path = root / "prefix-vp/pages/IR/page-000001.json.gz"
            page_path.parent.mkdir(parents=True)
            page_path.write_bytes(page)
            access_log = root / "access.jsonl"

            class FakeRuntime:
                def __init__(self) -> None:
                    self.root = root
                    self.manifest = {"candidate_id": "candidate", "dataset_id": "dataset"}
                    self.events = {
                        "incident": {
                            "incident": {"incident_id": "incident", "country_code": "IR"},
                            "series_ref": {"series_id": "series", "artifact_uri": "series/IR.json.gz"},
                        }
                    }
                    self.events_by_ref = {"country_outage/test/IR/1/r": "incident"}
                    self.series = {"series": module.deterministic_gzip(b'{"point_count":4320}\n')}
                    self.evidence = {
                        "incident": {
                            "page_count": 1,
                            "page_uri_template": "prefix-vp/pages/IR/page-{page:06d}.json.gz",
                        }
                    }
                    self.reports = {"report": {"report_snapshot_id": "report"}}
                    self.report_pointer = {"incident": "report"}

                def log(self, route: str, sources: list[str]) -> None:
                    with access_log.open("ab") as output:
                        output.write(module.canonical_bytes({
                            "route": route, "sources": sources,
                            "raw_mrt_scanned": False, "route_event_scanned": False,
                            "full_asn_state_scanned": False,
                            "publication_recomputed": False,
                        }) + b"\n")

            server = module.ThreadingHTTPServer(
                ("127.0.0.1", 0), module.make_handler(FakeRuntime())
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(base + "/api/v1/events/incident") as response:
                    self.assertEqual(response.status, 200)
                with urllib.request.urlopen(base + "/api/v1/events/incident/series") as response:
                    self.assertEqual(response.headers["Content-Encoding"], "gzip")
                with urllib.request.urlopen(base + "/api/v1/events/incident/evidence/pages/1") as response:
                    self.assertEqual(response.status, 200)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            records = [json.loads(line) for line in access_log.read_text().splitlines()]
            self.assertTrue(records)
            self.assertTrue(all(not row["raw_mrt_scanned"] for row in records))
            self.assertTrue(all(not row["route_event_scanned"] for row in records))
            self.assertTrue(all(not row["full_asn_state_scanned"] for row in records))
            self.assertTrue(all(not row["publication_recomputed"] for row in records))

    def test_database_contract_keeps_prefix_vp_as_derived_view(self) -> None:
        ddl = (Path(__file__).parent / "sql/rrc25-read-model-store-v1.sql").read_text()
        self.assertIn("prefix_vp_evidence_view", ddl)
        self.assertIn("derived_from_route_state_id", ddl)
        self.assertNotIn("CREATE TABLE domeye_read.route_state", ddl)
        self.assertNotIn("CREATE TABLE domeye_read.prefix_vp_state", ddl)


if __name__ == "__main__":
    unittest.main()
