from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class CountryOutageP1PageCoverageTest(unittest.TestCase):
    def test_s4_user_surface_and_narrow_proxy_exist(self) -> None:
        for relative in (
            "frontend/src/pages/CountryOutageChatPage.vue",
            "frontend/src/api/countryOutageChat.ts",
            "backend/web/api/v2/country_outage_chat_proxy.py",
            "agent-sidecar/tests/p1-page-capability-http.test.ts",
            "dev/tools/capture_country_outage_p1_page_coverage_s4.mjs",
            "dev/tools/validate_country_outage_p1_page_coverage.py",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_s4_live_receipt_keeps_two_ip_goals_and_execution_chain(self) -> None:
        path = (
            ROOT
            / "evaluation/country-outage/p1-page-coverage/s4/raw/api-receipt.json"
        )
        conversation = json.loads(path.read_text(encoding="utf-8"))[
            "response"
        ]["conversation"]
        self.assertEqual(
            [turn["question"] for turn in conversation["turns"]],
            ["IP地址变化情况", "IP地址变化趋势"],
        )
        for turn in conversation["turns"]:
            answer = turn["answer"]
            self.assertEqual(answer["answerability"], "supported")
            self.assertEqual(
                [
                    goal["normalized_kind"]
                    for goal in answer["semantic_plan"]["user_goal_plan"]["goals"]
                ],
                ["address_family_change", "new_prefix_resources"],
            )
            self.assertEqual(
                {
                    goal["entities"]["address_family"]
                    for goal in answer["semantic_plan"]["user_goal_plan"]["goals"]
                },
                {"both"},
            )
            self.assertEqual(
                len(answer["semantic_plan"]["grounding_plan"]["nodes"]), 6
            )
            self.assertEqual(len(answer["execution_trace"]["nodes"]), 6)
            self.assertEqual(answer["state_receipt"]["status"], "committed")
            self.assertFalse(
                any(item["source"] == "model" for item in answer["evidence"])
            )

    def test_s4_component_and_journey_identity_are_cross_bound(self) -> None:
        root = ROOT / "evaluation/country-outage/p1-page-coverage/s4"
        candidate = json.loads(
            (root / "same-candidate-manifest.json").read_text(encoding="utf-8")
        )
        trace = json.loads(
            (root / "browser-api-tool-evidence-state-trace.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(candidate["component_identities"]),
            {
                "frontend",
                "backend",
                "runtime",
                "semantic_planner",
                "model",
                "prompt",
                "schema",
                "capability_catalog",
                "policy",
                "tool_contracts",
                "operator_contracts",
                "oracle",
                "data_publication",
            },
        )
        self.assertEqual(len(trace["journeys"]), 1)
        self.assertEqual(
            trace["journeys"][0]["candidate_identity_sha256"],
            candidate["candidate_identity_sha256"],
        )

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
