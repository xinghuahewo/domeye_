from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from backend.data_pipeline.research.rrc25_country_outage.source_fact import (
    SOURCE_FACT_SNAPSHOT_SCHEMA_VERSION,
    SourceFactSnapshotError,
    load_frozen_incident_fact,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "config/research/iran-country-outage-source-fact-20260227.json"


class IranResearchSourceFactTests(unittest.TestCase):
    def _snapshot(self):
        return json.loads(SOURCE.read_text(encoding="utf-8"))

    def test_frozen_api_fact_normalizes_to_matched_incident(self):
        first = load_frozen_incident_fact(self._snapshot())
        second = load_frozen_incident_fact(self._snapshot())

        self.assertEqual(first, second)
        self.assertEqual(
            first.incident["incident_id"],
            "inc_v1_ab52ddcad8926f8882fed33a",
        )
        self.assertEqual(first.incident["fact_link_status"], "matched")
        self.assertEqual(first.legacy_affected_asn_count, 176)
        self.assertEqual(first.legacy_total_asn_count, 556)
        self.assertEqual(len(first.affected_asns), 176)
        self.assertEqual(len(first.snapshot_sha256), 64)
        self.assertEqual(
            self._snapshot()["schema_version"],
            SOURCE_FACT_SNAPSHOT_SCHEMA_VERSION,
        )

    def test_dual_time_conflict_is_exposed_without_merging_or_causality(self):
        frozen = load_frozen_incident_fact(self._snapshot())
        temporal = frozen.temporal_evidence

        self.assertEqual(
            (
                temporal.locator_record_start.local,
                temporal.locator_record_start.utc,
                temporal.locator_record_start.role,
            ),
            (
                "2026-02-27T09:12:32+08:00",
                "2026-02-27T01:12:32Z",
                "source_record_identity_only",
            ),
        )
        self.assertEqual(
            (
                temporal.embedded_message_candidate.local,
                temporal.embedded_message_candidate.utc,
                temporal.embedded_message_candidate.role,
            ),
            (
                "2026-02-28T22:34:40+08:00",
                "2026-02-28T14:34:40Z",
                "candidate_event_time_from_legacy_text",
            ),
        )
        self.assertEqual(temporal.difference_seconds, 134528)
        self.assertEqual(temporal.relationship_state, "unresolved_not_causal")
        self.assertFalse(temporal.single_event_time_merge_allowed)
        self.assertEqual(temporal.precursor_causality_state, "undetermined")
        self.assertEqual(len(temporal.limitations_zh), 2)
        # normalize_event 仍以旧 locator 构造 matched 身份；该时间的语义由
        # temporal_evidence 明确限制为 source_record_identity_only。
        self.assertEqual(
            frozen.incident["event_time_utc"],
            temporal.locator_record_start.utc,
        )
        projected = frozen.incident["legacy_temporal_evidence"]
        self.assertEqual(
            projected["locator_record_start"]["role"],
            "source_record_identity_only",
        )
        self.assertEqual(
            projected["embedded_message_candidate"]["utc"],
            "2026-02-28T14:34:40Z",
        )
        self.assertEqual(
            projected["relationship_state"], "unresolved_not_causal"
        )
        self.assertFalse(projected["single_event_time_merge_allowed"])

    def test_population_or_identity_drift_fails_closed(self):
        cases = []
        wrong_id = self._snapshot()
        wrong_id["expected_incident_id"] = "inc_v1_" + "0" * 24
        cases.append(wrong_id)
        wrong_count = self._snapshot()
        wrong_count["payload"]["fact_record"]["outage_as_num"] = 175
        cases.append(wrong_count)
        duplicate = self._snapshot()
        duplicate["payload"]["fact_record"]["outage_ases"][1] = duplicate[
            "payload"
        ]["fact_record"]["outage_ases"][0]
        cases.append(duplicate)
        mutable = self._snapshot()
        mutable["retrieval"]["production_mutation"] = True
        cases.append(mutable)

        for snapshot in cases:
            with self.subTest(snapshot=deepcopy(snapshot)):
                with self.assertRaises(SourceFactSnapshotError):
                    load_frozen_incident_fact(snapshot)

    def test_retrieval_and_current_locator_drift_fail_closed(self):
        cases = []
        wrong_schema = self._snapshot()
        wrong_schema["schema_version"] = (
            "iran-country-outage-source-fact-snapshot/v1"
        )
        cases.append(wrong_schema)
        wrong_endpoint = self._snapshot()
        wrong_endpoint["retrieval"]["endpoint"] = "/api/v1/events/evidence-bundle"
        cases.append(wrong_endpoint)
        wrong_retrieved_at = self._snapshot()
        wrong_retrieved_at["retrieval"]["retrieved_at_utc"] = (
            "2026-07-22T09:36:23Z"
        )
        cases.append(wrong_retrieved_at)
        invalid_retrieved_at = self._snapshot()
        invalid_retrieved_at["retrieval"]["retrieved_at_utc"] = (
            "2026-07-22 09:36:22"
        )
        cases.append(invalid_retrieved_at)
        wrong_locator = self._snapshot()
        wrong_locator["payload"]["source_record"]["record_locator"][
            "start_time"
        ] = "2026-02-27 09:12:33"
        cases.append(wrong_locator)
        wrong_fact_start = self._snapshot()
        wrong_fact_start["payload"]["fact_record"]["start_time"] = (
            "2026-02-27 09:12:33"
        )
        cases.append(wrong_fact_start)

        for snapshot in cases:
            with self.subTest(snapshot=deepcopy(snapshot)):
                with self.assertRaises(SourceFactSnapshotError):
                    load_frozen_incident_fact(snapshot)

    def test_temporal_evidence_or_embedded_text_drift_fails_closed(self):
        cases = []
        summary_mismatch = self._snapshot()
        summary_mismatch["payload"]["event"]["summary"] += " 已确认"
        cases.append(summary_mismatch)
        embedded_time_drift = self._snapshot()
        for field in (
            embedded_time_drift["payload"]["event"],
            embedded_time_drift["payload"]["fact_record"],
        ):
            key = "summary" if "summary" in field else "event_info"
            field[key] = field[key].replace("22:34:40", "22:34:41")
        cases.append(embedded_time_drift)
        event_utc_drift = self._snapshot()
        event_utc_drift["payload"]["event"]["event_time_utc"] = (
            "2026-02-27T01:12:33Z"
        )
        cases.append(event_utc_drift)
        anchor_utc_drift = self._snapshot()
        anchor_utc_drift["temporal_evidence"]["embedded_message_candidate"][
            "utc"
        ] = "2026-02-28T14:34:41Z"
        cases.append(anchor_utc_drift)
        difference_drift = self._snapshot()
        difference_drift["temporal_evidence"]["difference_seconds"] = 134529
        cases.append(difference_drift)
        relationship_drift = self._snapshot()
        relationship_drift["temporal_evidence"]["relationship_state"] = (
            "precursor_confirmed"
        )
        cases.append(relationship_drift)
        merge_enabled = self._snapshot()
        merge_enabled["temporal_evidence"]["single_event_time_merge_allowed"] = True
        cases.append(merge_enabled)
        causality_claimed = self._snapshot()
        causality_claimed["temporal_evidence"]["precursor_causality_state"] = (
            "confirmed"
        )
        cases.append(causality_claimed)
        open_temporal_schema = self._snapshot()
        open_temporal_schema["temporal_evidence"]["extra"] = True
        cases.append(open_temporal_schema)

        for snapshot in cases:
            with self.subTest(snapshot=deepcopy(snapshot)):
                with self.assertRaises(SourceFactSnapshotError):
                    load_frozen_incident_fact(snapshot)


if __name__ == "__main__":
    unittest.main()
