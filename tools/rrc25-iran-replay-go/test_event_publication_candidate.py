import gzip
import csv
from pathlib import Path
import tempfile
import unittest

import event_publication_candidate as candidate


class EventPublicationCandidateTest(unittest.TestCase):
    def test_legacy_reference_uses_shanghai_timezone_and_ceil_slot(self) -> None:
        event, country, source_code = candidate.parse_legacy_reference(
            "country_outage/2026-02-27 09:12:32/IR/1/r"
        )
        self.assertEqual(candidate.utc_text(event), "2026-02-27T01:12:32Z")
        self.assertEqual(
            candidate.utc_text(candidate.ceil_five_minutes(event)),
            "2026-02-27T01:15:00Z",
        )
        self.assertEqual(country, "IR")
        self.assertEqual(source_code, "1")

    def test_generated_publication_has_stable_identity_and_new_id_per_progress(self) -> None:
        common = dict(
            candidate_id="domeye_data_candidate_v1_" + "a" * 32,
            incident_id="incident_v1", kind="observation", revision=2,
            observed_at="2026-02-27T01:15:00Z", fact_id="event_fact_v1_" + "b" * 32,
            derived_from=None, previous=None, correction_of="legacy_v1",
            supersedes=None, metric_dataset_id="route_metric_dataset_v1_" + "c" * 32,
            metric_slot_sha="d" * 64, is_final=False, fact_set_sha="e" * 64,
            snapshot={"schema_version": "test", "value": 1},
        )
        first = candidate.generated_publication(
            **common, sequence=1, data_through="2026-02-27T01:15:00Z"
        )
        repeated = candidate.generated_publication(
            **common, sequence=1, data_through="2026-02-27T01:15:00Z"
        )
        second = candidate.generated_publication(
            **common, sequence=2, data_through="2026-02-27T01:20:00Z"
        )
        self.assertEqual(first, repeated)
        self.assertNotEqual(first["publication_id"], second["publication_id"])
        self.assertRegex(first["payload_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(first["content_sha256"], r"^[0-9a-f]{64}$")

    def test_receipt_hash_is_independent_of_key_order(self) -> None:
        first = {"schema_version": "v1", "status": "complete", "content_sha256": ""}
        second = {"content_sha256": "", "status": "complete", "schema_version": "v1"}
        self.assertEqual(
            candidate.receipt_content_sha(first), candidate.receipt_content_sha(second)
        )

    def test_gzip_tsv_is_deterministic_and_counts_data_rows(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            paths = [root / "one.tsv.gz", root / "two.tsv.gz"]
            metadata = []
            for path in paths:
                writer = candidate.GzipTsvWriter(path, ("identity", "payload"))
                writer.write({"identity": "x", "payload": {"b": 2, "a": 1}})
                writer.write({"identity": "y", "payload": None})
                metadata.append(writer.close())
            self.assertEqual(metadata[0]["row_count"], 2)
            self.assertEqual(metadata[0]["sha256"], metadata[1]["sha256"])
            self.assertEqual(metadata[0]["content_sha256"], metadata[1]["content_sha256"])
            with gzip.open(paths[0], "rt", encoding="utf-8", newline="") as source:
                rows = list(csv.reader(source, delimiter="\t"))
            self.assertEqual(rows[0], ["identity", "payload"])
            self.assertEqual(rows[1], ["x", '{"a":1,"b":2}'])
            self.assertEqual(rows[2], ["y", r"\N"])

    def test_formal_fact_never_claims_recovery_without_normal_band(self) -> None:
        row = {
            "state_point_utc": "2026-03-11T00:00:00Z", "country_code": "IR",
            "baseline_v4": 10, "baseline_v6": 0, "cohort_visible_v4": 8,
            "cohort_visible_v6": 0, "current_visible_v4": 8,
            "current_visible_v6": 0, "announcement_v4": 0,
            "announcement_v6": 0, "withdrawal_v4": 0, "withdrawal_v6": 0,
            "slot": 4320, "source_route_state_slot_sha256": "a" * 64,
            "metric_snapshot_sha256": "b" * 64, "metric_slot_sha256": "c" * 64,
            "quality_status": "complete", "gap_status": "none",
        }
        fact = candidate.make_fact(
            "domeye_data_candidate_v1_" + "d" * 32, "incident", 3, "final",
            "2026-03-11T00:00:00Z", row,
            "route_metric_dataset_v1_" + "e" * 32, "event_fact_v1_" + "f" * 32,
        )
        self.assertEqual(fact["stage"], "final")
        self.assertEqual(fact["limitations"]["recovery_claim"], "not_assessed")
        self.assertEqual(
            fact["limitations"]["final_semantics"], "fixed_observation_window_closed"
        )


if __name__ == "__main__":
    unittest.main()
