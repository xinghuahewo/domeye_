from copy import deepcopy
import hashlib
import unittest

from backend.data_pipeline.research.rrc25_country_outage.input_coverage import (
    ResearchCoverageError,
    reconcile_event_window_coverage,
)
from backend.data_pipeline.research.rrc25_country_outage.input_resolver import (
    canonical_json,
    resolve_research_inputs,
)
from backend.data_pipeline.route_event.artifacts import artifact_id_v1


def artifact(kind, time_text, number):
    digest = hashlib.sha256(f"{kind}-{time_text}-{number}".encode()).hexdigest()
    family = "updates" if kind == "update" else "bview"
    compact = time_text.replace("-", "").replace(":", "").replace("T", ".")[:13]
    return {
        "artifact_id": artifact_id_v1(digest),
        "artifact_type": kind,
        "artifact_time_utc": time_text,
        "collector_id": "rrc25",
        "relative_path": f"rrc25/fixture/{family}.{compact}.gz",
        "file_sha256": digest,
        "size_bytes": 100 + number,
        "compression": "gz",
    }


def profile():
    return {
        "study_id": "iran-rrc25-fixture",
        "collector_id": "rrc25",
        "country_code": "IR",
        "window": {
            "start_utc": "2026-02-27T16:00:00Z",
            "end_exclusive_utc": "2026-02-27T16:20:00Z",
            "granularity_seconds": 300,
        },
    }


def parent(rows, invalid=()):
    payload = {"artifacts": rows, "invalid_in_window": list(invalid)}
    fingerprint = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    manifest = {
        "schema_version": 1,
        "manifest_kind": "mrt_artifact_manifest",
        "artifact_id_schema": "artifact_id_v1",
        "manifest_fingerprint_sha256": fingerprint,
        "scan_policy": {
            "compression_envelope_validation": "full_stream_to_eof_crc_or_equivalent",
            "invalid_in_window": "full_hash_quarantine_exclude_from_available_slots",
        },
        **payload,
    }
    verification = {
        "verified": True,
        "manifest_fingerprint_sha256": fingerprint,
        "artifact_count": len(rows),
        "invalid_in_window_count": len(invalid),
    }
    return manifest, verification


def complete_rows():
    rows = [
        artifact("rib", "2026-02-27T08:00:00Z", 1),
        artifact("rib", "2026-02-27T16:00:00Z", 2),
    ]
    for index, minute in enumerate((0, 5, 10, 15), start=3):
        rows.append(artifact("update", f"2026-02-27T16:{minute:02d}:00Z", index))
    return rows


class ResearchInputCoverageTest(unittest.TestCase):
    def resolve(self, rows, invalid=()):
        manifest, verification = parent(rows, invalid)
        selection = resolve_research_inputs(manifest, verification, profile())
        return manifest, verification, selection

    def test_complete_event_window_closes_verified_integrity_and_roles(self):
        manifest, verification, selection = self.resolve(complete_rows())
        first = reconcile_event_window_coverage(manifest, verification, selection)
        second = reconcile_event_window_coverage(manifest, verification, selection)

        self.assertEqual(first, second)
        self.assertEqual(first["coverage_state"], "complete")
        self.assertEqual(first["analysis"]["updates"]["expected_count"], 4)
        self.assertEqual(first["analysis"]["updates"]["available_count"], 4)
        self.assertEqual(first["analysis"]["updates"]["slot_ranges"][0]["slot_count"], 4)
        self.assertEqual(first["analysis"]["ribs"]["available_count"], 1)
        self.assertTrue(first["integrity_evidence"]["manifest_verified"])

    def test_missing_source_slot_remains_source_unavailable(self):
        rows = [
            row
            for row in complete_rows()
            if row["artifact_time_utc"] != "2026-02-27T16:10:00Z"
        ]
        manifest, verification, selection = self.resolve(rows)
        result = reconcile_event_window_coverage(manifest, verification, selection)

        self.assertEqual(result["coverage_state"], "incomplete")
        update = result["analysis"]["updates"]
        self.assertEqual(update["source_unavailable_count"], 1)
        self.assertEqual(update["parse_failed_count"], 0)
        self.assertIn("source_unavailable", [row["value_state"] for row in update["slot_ranges"]])

    def test_quarantined_slot_is_parse_failed_not_source_unavailable(self):
        target = next(
            row
            for row in complete_rows()
            if row["artifact_time_utc"] == "2026-02-27T16:10:00Z"
        )
        rows = [row for row in complete_rows() if row is not target]
        # 上面的 identity 比较不会命中重新构造的 list，因此按槽过滤。
        rows = [
            row
            for row in rows
            if row["artifact_time_utc"] != "2026-02-27T16:10:00Z"
        ]
        invalid = {
            key: value
            for key, value in target.items()
            if key not in {"artifact_id"}
        } | {
            "value_state": "parse_failed",
            "missing_reason": "compressed_stream_invalid",
        }
        manifest, verification, selection = self.resolve(rows, (invalid,))
        result = reconcile_event_window_coverage(manifest, verification, selection)

        update = result["analysis"]["updates"]
        self.assertEqual(update["parse_failed_count"], 1)
        self.assertEqual(update["source_unavailable_count"], 0)
        failed = [row for row in update["slot_ranges"] if row["value_state"] == "parse_failed"]
        self.assertEqual(failed[0]["missing_reason"], "compressed_stream_invalid")

    def test_rejects_role_tamper_and_advertised_count_drift(self):
        manifest, verification, selection = self.resolve(complete_rows())
        forged = deepcopy(selection)
        forged["roles"]["analysis_updates"][0]["size_bytes"] += 1
        forged["selected_unique_size_bytes"] += 1
        with self.assertRaisesRegex(ResearchCoverageError, "父 manifest"):
            reconcile_event_window_coverage(manifest, verification, forged)

        forged = deepcopy(selection)
        forged["coverage"]["analysis_updates"]["observed_count"] = 3
        with self.assertRaisesRegex(ResearchCoverageError, "coverage.analysis_updates"):
            reconcile_event_window_coverage(manifest, verification, forged)


if __name__ == "__main__":
    unittest.main()
