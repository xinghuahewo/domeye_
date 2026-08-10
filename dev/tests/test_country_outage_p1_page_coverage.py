from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class CountryOutageP1PageCoverageTest(unittest.TestCase):
    def test_s3_runtime_sources_exist(self) -> None:
        for relative in (
            "agent-sidecar/src/chat/runtime-v2-conversation.ts",
            "agent-sidecar/src/chat/runtime-v2-semantic.ts",
            "agent-sidecar/src/chat/page-capability-executor.ts",
            "agent-sidecar/tests/p1-page-capability-conversation.test.ts",
            "dev/tools/capture_country_outage_p1_page_coverage_s3.mjs",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_s3_journey_set_covers_every_page_outcome(self) -> None:
        path = (
            ROOT
            / "contracts/agent/country-outage-p1-page-coverage/s3/journeys.json"
        )
        journeys = json.loads(path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(journeys), 5)
        self.assertGreaterEqual(
            sum(len(item["turns"]) for item in journeys),
            15,
        )
        self.assertEqual(
            {outcome for item in journeys for outcome in item["page_outcome_ids"]},
            {f"PCO-{index:02d}" for index in range(1, 9)},
        )

    def test_s3_journey_and_turn_ids_are_unique(self) -> None:
        path = (
            ROOT
            / "contracts/agent/country-outage-p1-page-coverage/s3/journeys.json"
        )
        journeys = json.loads(path.read_text(encoding="utf-8"))
        journey_ids = [item["journey_id"] for item in journeys]
        turn_ids = [
            turn["turn_id"] for item in journeys for turn in item["turns"]
        ]
        self.assertEqual(len(journey_ids), len(set(journey_ids)))
        self.assertEqual(len(turn_ids), len(set(turn_ids)))

    def test_model_has_no_direct_state_write_authority(self) -> None:
        source = (
            ROOT / "agent-sidecar/src/chat/runtime-v2-semantic.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("assertModelStateProposalIsEmpty", source)
        self.assertIn("模型没有状态写权限", source)
        self.assertIn("materializeDialogContext", source)

    def test_state_transaction_has_postflight_and_rollback(self) -> None:
        source = (
            ROOT / "agent-sidecar/src/chat/runtime-v2-conversation.ts"
        ).read_text(encoding="utf-8")
        for token in (
            "binding_revalidated",
            "failure_receipt",
            "rolled_back",
            "event_binding_suspended_until_rebind",
            "binding_generation",
            "tool_timeout",
        ):
            self.assertIn(token, source)

    def test_dialog_and_evidence_state_are_separate(self) -> None:
        source = (
            ROOT / "agent-sidecar/src/chat/runtime-v2-conversation.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("evidence_state: P1RuntimeV2EvidenceState", source)
        self.assertIn("dialog_state: P1ConversationState", source)
        self.assertIn("immutable: true", source)


if __name__ == "__main__":
    unittest.main()
