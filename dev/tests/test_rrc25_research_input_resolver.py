from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import unittest

from backend.data_pipeline.research.rrc25_country_outage.input_resolver import (
    ResearchInputError,
    canonical_json,
    resolve_research_inputs,
)
from backend.data_pipeline.route_event.artifacts import artifact_id_v1


UTC = timezone.utc


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


def profile(start="2026-02-27T16:00:00Z", end="2026-02-27T16:20:00Z"):
    return {
        "study_id": "iran-rrc25-fixture",
        "collector_id": "rrc25",
        "country_code": "IR",
        "window": {
            "start_utc": start,
            "end_exclusive_utc": end,
            "observation_end_utc": end,
            "granularity_seconds": 300,
        },
    }


def manifest(rows):
    fingerprint = hashlib.sha256(canonical_json(rows).encode()).hexdigest()
    value = {
        "schema_version": 1,
        "manifest_kind": "mrt_artifact_manifest",
        "manifest_fingerprint_sha256": fingerprint,
        "artifacts": rows,
    }
    verification = {
        "verified": True,
        "manifest_fingerprint_sha256": fingerprint,
        "artifact_count": len(rows),
    }
    return value, verification


class ResearchInputResolverTest(unittest.TestCase):
    def complete_rows(self):
        rows = [
            artifact("rib", "2026-02-27T08:00:00Z", 1),
            artifact("rib", "2026-02-27T16:00:00Z", 2),
        ]
        for index, minute in enumerate((0, 5, 10, 15), start=3):
            rows.append(artifact("update", f"2026-02-27T16:{minute:02d}:00Z", index))
        return rows

    def test_resolves_exact_start_seed_prior_reference_and_half_open_slots(self):
        parent, verification = manifest(self.complete_rows())
        first = resolve_research_inputs(parent, verification, profile())
        second = resolve_research_inputs(parent, verification, profile())

        self.assertEqual(first, second)
        self.assertEqual(first["status"], "complete")
        self.assertEqual(
            first["roles"]["state_seed_rib"]["artifact_time_utc"],
            "2026-02-27T16:00:00Z",
        )
        self.assertEqual(
            first["roles"]["baseline_reference_rib"]["artifact_time_utc"],
            "2026-02-27T08:00:00Z",
        )
        self.assertEqual(len(first["roles"]["analysis_updates"]), 4)
        self.assertEqual(len(first["roles"]["analysis_ribs"]), 1)
        self.assertEqual(first["roles"]["catch_up_updates"], [])
        self.assertNotIn("generated_at", canonical_json(first))

    def test_missing_analysis_slot_is_incomplete_not_zero_filled(self):
        rows = self.complete_rows()
        rows = [row for row in rows if row["artifact_time_utc"] != "2026-02-27T16:10:00Z"]
        parent, verification = manifest(rows)
        result = resolve_research_inputs(parent, verification, profile())
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["coverage"]["analysis_updates"]["observed_count"], 3)
        self.assertEqual(result["coverage"]["analysis_updates"]["missing_count"], 1)
        self.assertEqual(result["failures"][0]["code"], "analysis_update_slots_missing")

    def test_prior_seed_uses_complete_catch_up_and_is_also_reference(self):
        start = datetime(2026, 2, 27, 16, 0, tzinfo=UTC)
        rows = [artifact("rib", "2026-02-27T08:00:00Z", 1)]
        current = datetime(2026, 2, 27, 8, 0, tzinfo=UTC)
        number = 2
        while current < start + timedelta(minutes=20):
            rows.append(artifact("update", current.strftime("%Y-%m-%dT%H:%M:%SZ"), number))
            current += timedelta(minutes=5)
            number += 1
        parent, verification = manifest(rows)
        result = resolve_research_inputs(parent, verification, profile())
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(len(result["roles"]["catch_up_updates"]), 96)
        self.assertEqual(
            result["roles"]["state_seed_rib"]["artifact_id"],
            result["roles"]["baseline_reference_rib"]["artifact_id"],
        )
        self.assertEqual(
            [row["code"] for row in result["failures"]],
            ["analysis_rib_slots_missing"],
        )

    def test_rejects_unverified_or_wrong_collector_manifest(self):
        parent, verification = manifest(self.complete_rows())
        forged = deepcopy(verification)
        forged["verified"] = False
        with self.assertRaisesRegex(ResearchInputError, "尚未完整验证"):
            resolve_research_inputs(parent, forged, profile())

        parent["artifacts"][0]["collector_id"] = "rrc00"
        with self.assertRaisesRegex(ResearchInputError, "collector"):
            resolve_research_inputs(parent, verification, profile())

    def test_rejects_forged_artifact_identity_unsafe_path_and_duplicate_slot(self):
        rows = self.complete_rows()
        rows[0]["artifact_id"] = "art_v1_" + "0" * 32
        parent, verification = manifest(rows)
        with self.assertRaisesRegex(ResearchInputError, "artifact_id"):
            resolve_research_inputs(parent, verification, profile())

        rows = self.complete_rows()
        rows[0]["relative_path"] = "../bview.20260227.0800.gz"
        parent, verification = manifest(rows)
        with self.assertRaisesRegex(ResearchInputError, "relative_path"):
            resolve_research_inputs(parent, verification, profile())

        rows = self.complete_rows()
        rows.append(artifact("update", "2026-02-27T16:00:00Z", 99))
        parent, verification = manifest(rows)
        with self.assertRaisesRegex(ResearchInputError, "槽不得重复"):
            resolve_research_inputs(parent, verification, profile())


if __name__ == "__main__":
    unittest.main()
