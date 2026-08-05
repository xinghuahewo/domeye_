from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.country_outage_trend_product import (  # noqa: E402
    TrendProductValidationError,
    answer_trend_question_v1,
    compile_trend_product_v1,
    validate_evidence_graph_v1,
)


VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s4.py"
FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s4-v1.json"


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageTrendAnalysisS4Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = load_module("s4_verifier_for_test", VERIFIER_PATH)
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.product = cls.verifier.build_candidate()

    def test_s4_candidate_verifier_passes(self) -> None:
        self.assertEqual(self.verifier.validate(), [])

    def test_claims_have_all_three_relation_types(self) -> None:
        graph = self.product["evidence_graph"]
        self.assertEqual(validate_evidence_graph_v1(graph), [])
        for claim_id in self.product["claim_ids"]:
            relations = {
                edge["relation"] for edge in graph["edges"] if edge["from"] == claim_id
            }
            self.assertEqual(relations, {"supported_by", "limited_by", "unknown_about"})

    def test_graph_contains_no_hypothesis_or_causal_relation(self) -> None:
        graph = self.product["evidence_graph"]
        self.assertFalse(graph["hypothesis_nodes_allowed"])
        self.assertFalse(graph["causal_relations_allowed"])
        self.assertNotIn("Hypothesis", {node["node_type"] for node in graph["nodes"]})

    def test_every_evidence_is_bound_to_same_snapshot(self) -> None:
        snapshot = self.product["snapshot"]
        expected = {
            "incident_id": snapshot["incident_id"],
            "publication_id": snapshot["publication_id"],
            "revision": snapshot["revision"],
            "data_through": snapshot["data_through"],
        }
        for node in self.product["evidence_graph"]["nodes"]:
            if node["node_type"] == "Evidence":
                self.assertEqual(node["snapshot_ref"], expected)

    def test_identity_conflict_fails_closed(self) -> None:
        candidate = self.verifier.build_candidate()
        context = deepcopy(candidate["contexts"]["asn"])
        context["snapshot"]["revision"] += 1
        with self.assertRaises(TrendProductValidationError) as captured:
            compile_trend_product_v1(candidate["profile"], asn_context=context)
        self.assertEqual(captured.exception.code, "context_identity_conflict")

    def test_all_surfaces_share_product_id(self) -> None:
        contract = self.product["render_contract"]
        self.assertEqual(contract["source_product_id"], self.product["product_id"])
        self.assertEqual(contract["surfaces"], self.fixture["expected"]["required_surfaces"])
        self.assertFalse(contract["model_may_rewrite_deterministic_values"])

    def test_whitelisted_questions_are_evidence_bound(self) -> None:
        for case in self.fixture["questions"]:
            answer = answer_trend_question_v1(self.product, case["text"])
            self.assertEqual(answer["status"], case["status"])
            self.assertEqual(answer["operator"], case["operator"])
            self.assertEqual(answer["product_id"], self.product["product_id"])
            if answer["status"] == "answered":
                self.assertTrue(answer["claim_refs"])
                self.assertTrue(answer["evidence_refs"])

    def test_boundary_questions_abstain_with_limitation_and_unknown(self) -> None:
        for question in (
            "原因是什么？",
            "这是攻击吗？",
            "用户是否无法访问？",
            "谁应该负责？",
            "窗口后完全恢复了吗？",
        ):
            answer = answer_trend_question_v1(self.product, question)
            self.assertEqual(answer["status"], "abstained")
            self.assertTrue(answer["limitation_refs"])
            self.assertTrue(answer["unknown_refs"])

    def test_unknown_question_does_not_create_fact(self) -> None:
        answer = answer_trend_question_v1(self.product, "给我一个未来预测")
        self.assertEqual(answer["status"], "unsupported")
        self.assertEqual(answer["claim_refs"], [])
        self.assertEqual(answer["evidence_refs"], [])

    def test_repeated_compilation_is_identical(self) -> None:
        self.assertEqual(self.product, self.verifier.build_candidate())


if __name__ == "__main__":
    unittest.main()

