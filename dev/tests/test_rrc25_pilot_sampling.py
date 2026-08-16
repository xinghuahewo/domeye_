from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import unittest

from backend.data_pipeline.research.rrc25_country_outage.bounded_pilot_worker import (
    _selection_identity,
)
from backend.data_pipeline.research.rrc25_country_outage.input_resolver import (
    canonical_json,
    resolve_research_inputs,
)
from backend.data_pipeline.research.rrc25_country_outage.pilot_sampling import (
    PilotSamplingError,
    SPARSE_SAMPLING_FAILURE_CODE,
    build_sparse_pilot_selection,
)
from backend.data_pipeline.route_event.artifacts import artifact_id_v1


UTC = timezone.utc


def _artifact(kind, time_text, number):
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


def _profile(end="2026-02-27T16:30:00Z"):
    return {
        "study_id": "iran-rrc25-pilot-sampling-fixture",
        "collector_id": "rrc25",
        "country_code": "IR",
        "window": {
            "start_utc": "2026-02-27T16:00:00Z",
            "end_exclusive_utc": end,
            "granularity_seconds": 300,
        },
    }


def _manifest(rows):
    fingerprint = hashlib.sha256(canonical_json(rows).encode()).hexdigest()
    manifest = {
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
    return manifest, verification


def _full_selection(*, minutes=(0, 5, 10, 15, 20, 25), end="2026-02-27T16:30:00Z"):
    rows = [
        _artifact("rib", "2026-02-27T08:00:00Z", 1),
        _artifact("rib", "2026-02-27T16:00:00Z", 2),
    ]
    for index, minute in enumerate(minutes, start=3):
        rows.append(
            _artifact("update", f"2026-02-27T16:{minute:02d}:00Z", index)
        )
    manifest, verification = _manifest(rows)
    return resolve_research_inputs(manifest, verification, _profile(end=end))


def _selection_with_catch_up():
    start = datetime(2026, 2, 27, 16, 0, tzinfo=UTC)
    rows = [_artifact("rib", "2026-02-27T08:00:00Z", 1)]
    cursor = datetime(2026, 2, 27, 8, 0, tzinfo=UTC)
    number = 2
    while cursor < start + timedelta(minutes=30):
        rows.append(
            _artifact("update", cursor.strftime("%Y-%m-%dT%H:%M:%SZ"), number)
        )
        cursor += timedelta(minutes=5)
        number += 1
    manifest, verification = _manifest(rows)
    return resolve_research_inputs(manifest, verification, _profile())


class PilotSamplingTests(unittest.TestCase):
    def test_builds_deterministic_sparse_selection_with_worker_identity(self):
        source = _full_selection()
        source_before = deepcopy(source)
        updates = source["roles"]["analysis_updates"]
        allowlist = [updates[4]["artifact_id"], updates[1]["artifact_id"]]

        result = build_sparse_pilot_selection(source, allowlist)
        reversed_result = build_sparse_pilot_selection(source, list(reversed(allowlist)))

        self.assertEqual(source, source_before)
        self.assertEqual(result, reversed_result)
        self.assertEqual(
            [row["artifact_time_utc"] for row in result["roles"]["analysis_updates"]],
            ["2026-02-27T16:05:00Z", "2026-02-27T16:20:00Z"],
        )
        self.assertEqual(result["schema_version"], source["schema_version"])
        self.assertEqual(result["window"], source["window"])
        self.assertEqual(
            result["roles"]["state_seed_rib"], source["roles"]["state_seed_rib"]
        )
        self.assertEqual(
            result["roles"]["baseline_reference_rib"],
            source["roles"]["baseline_reference_rib"],
        )
        self.assertEqual(
            result["roles"]["analysis_ribs"], source["roles"]["analysis_ribs"]
        )
        self.assertEqual(
            result["coverage"]["analysis_updates"],
            {"expected_count": 6, "observed_count": 2, "missing_count": 4},
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(
            [failure["code"] for failure in result["failures"]],
            [SPARSE_SAMPLING_FAILURE_CODE],
        )
        details = result["failures"][0]["details"]
        self.assertEqual(details["source_observed_count"], 6)
        self.assertEqual(details["selected_count"], 2)
        self.assertEqual(details["sampled_out_count"], 4)
        self.assertEqual(details["coverage_missing_count"], 4)
        self.assertEqual(details["selected_artifact_ids"], [
            row["artifact_id"] for row in result["roles"]["analysis_updates"]
        ])

        unique_rows = {
            row["artifact_id"]: row
            for role in result["roles"].values()
            for row in (
                [role] if isinstance(role, dict) else role or []
            )
        }
        self.assertEqual(result["selected_unique_artifact_count"], len(unique_rows))
        self.assertEqual(
            result["selected_unique_size_bytes"],
            sum(row["size_bytes"] for row in unique_rows.values()),
        )
        self.assertEqual(
            _selection_identity(result),
            (result["selection_id"], result["semantic_fingerprint_sha256"]),
        )

    def test_preserves_source_gap_separately_from_intentional_sampling_gap(self):
        source = _full_selection(minutes=(0, 5, 15, 20, 25))
        requested = [
            source["roles"]["analysis_updates"][0]["artifact_id"],
            source["roles"]["analysis_updates"][-1]["artifact_id"],
        ]

        result = build_sparse_pilot_selection(source, requested)

        self.assertEqual(
            [failure["code"] for failure in result["failures"]],
            ["analysis_update_slots_missing", SPARSE_SAMPLING_FAILURE_CODE],
        )
        self.assertEqual(
            result["failures"][0]["details"]["slots"],
            ["2026-02-27T16:10:00Z"],
        )
        sparse = result["failures"][1]["details"]
        self.assertEqual(sparse["source_missing_count"], 1)
        self.assertEqual(sparse["sampled_out_count"], 3)
        self.assertEqual(sparse["coverage_missing_count"], 4)
        self.assertEqual(
            result["coverage"]["analysis_updates"],
            {"expected_count": 6, "observed_count": 2, "missing_count": 4},
        )
        _selection_identity(result)

    def test_never_reports_complete_even_when_allowlist_covers_short_window(self):
        source = _full_selection(
            minutes=(0, 5, 10, 15),
            end="2026-02-27T16:20:00Z",
        )
        requested = [
            row["artifact_id"] for row in source["roles"]["analysis_updates"]
        ]

        result = build_sparse_pilot_selection(source, requested)

        self.assertEqual(result["coverage"]["analysis_updates"]["missing_count"], 0)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["failures"][-1]["code"], SPARSE_SAMPLING_FAILURE_CODE)
        self.assertEqual(result["failures"][-1]["details"]["sampled_out_count"], 0)
        _selection_identity(result)

    def test_rejects_empty_too_many_duplicate_and_unknown_ids(self):
        source = _full_selection()
        ids = [row["artifact_id"] for row in source["roles"]["analysis_updates"]]

        with self.assertRaisesRegex(PilotSamplingError, "1..5"):
            build_sparse_pilot_selection(source, [])
        with self.assertRaisesRegex(PilotSamplingError, "1..5"):
            build_sparse_pilot_selection(source, ids)
        with self.assertRaisesRegex(PilotSamplingError, "不得重复"):
            build_sparse_pilot_selection(source, [ids[0], ids[0]])
        with self.assertRaisesRegex(PilotSamplingError, "未知 artifact_id"):
            build_sparse_pilot_selection(source, ["art_v1_unknown"])

    def test_rejects_rib_and_catch_up_update_as_non_analysis_update(self):
        source = _full_selection()
        with self.assertRaisesRegex(PilotSamplingError, "不是 analysis UPDATE"):
            build_sparse_pilot_selection(
                source,
                [source["roles"]["state_seed_rib"]["artifact_id"]],
            )

        catch_up_source = _selection_with_catch_up()
        with self.assertRaisesRegex(PilotSamplingError, "不是 analysis UPDATE"):
            build_sparse_pilot_selection(
                catch_up_source,
                [catch_up_source["roles"]["catch_up_updates"][0]["artifact_id"]],
            )

    def test_rejects_forged_identity_and_resealed_semantic_drift(self):
        source = _full_selection()
        forged = deepcopy(source)
        forged["semantic_fingerprint_sha256"] = "0" * 64
        with self.assertRaisesRegex(PilotSamplingError, "fingerprint"):
            build_sparse_pilot_selection(
                forged,
                [source["roles"]["analysis_updates"][0]["artifact_id"]],
            )

        forged = deepcopy(source)
        forged["coverage"]["analysis_updates"]["observed_count"] = 5
        semantic = {
            key: value
            for key, value in forged.items()
            if key not in {"selection_id", "semantic_fingerprint_sha256"}
        }
        forged["semantic_fingerprint_sha256"] = hashlib.sha256(
            canonical_json(semantic).encode("utf-8")
        ).hexdigest()
        forged["selection_id"] = "rsel_v1_" + hashlib.sha256(
            canonical_json(
                {
                    "schema": "rrc25_country_outage_input_selection_id_v1",
                    "selection": semantic,
                }
            ).encode("utf-8")
        ).hexdigest()[:32]
        with self.assertRaisesRegex(PilotSamplingError, "coverage"):
            build_sparse_pilot_selection(
                forged,
                [source["roles"]["analysis_updates"][0]["artifact_id"]],
            )


if __name__ == "__main__":
    unittest.main()
