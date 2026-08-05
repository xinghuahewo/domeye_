from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s6.py"


def load_verifier():
    specification = importlib.util.spec_from_file_location("s6_verifier_test", VERIFIER_PATH)
    if specification is None or specification.loader is None:
        raise RuntimeError("无法加载 S6 校验器")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageTrendAnalysisS6Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verifier = load_verifier()
        cls.fixture = cls.verifier.load_json(cls.verifier.FIXTURE_PATH)
        cls.product = cls.verifier.build_candidate()

    def test_machine_acceptance_without_document(self):
        errors, result = self.verifier.validate(require_final_document=False)
        self.assertEqual(errors, [])
        self.assertEqual(result["metrics"]["b2_coverage"], 1.0)

    def test_projection_order_and_repetition_are_content_identical(self):
        expected = self.verifier.canonical_json(self.product)
        self.assertEqual(self.verifier.canonical_json(self.verifier.build_candidate()), expected)
        for seed in (0, 1, 5, 11):
            actual = self.verifier.build_candidate_with_projection_order(seed)
            self.assertEqual(self.verifier.canonical_json(actual), expected)

    def test_all_tae_and_surface_contracts_are_frozen(self):
        self.assertEqual(
            [item["id"] for item in self.fixture["tae_matrix"]],
            [f"TAE-{index:02d}" for index in range(1, 16)],
        )
        self.assertEqual(
            [item["surface"] for item in self.fixture["surface_contract"]],
            ["data", "api", "page", "report", "qa", "download", "value"],
        )

    def test_all_refusals_are_bound_to_same_product_boundary(self):
        for case in self.fixture["refusal_cases"]:
            answer = self.verifier.answer_trend_question_v1(self.product, case["question"])
            self.assertEqual(answer["status"], "abstained", case["id"])
            self.assertEqual(answer["operator"], "evidence_boundary", case["id"])
            self.assertEqual(answer["product_id"], self.product["product_id"])
            self.assertTrue(answer["limitation_refs"])
            self.assertTrue(answer["unknown_refs"])

    def test_tampered_graph_does_not_validate(self):
        graph = deepcopy(self.product["evidence_graph"])
        claim = next(node for node in graph["nodes"] if node["node_type"] == "Claim")
        graph["edges"] = [
            edge
            for edge in graph["edges"]
            if not (edge["from"] == claim["node_id"] and edge["relation"] == "limited_by")
        ]
        self.assertTrue(self.verifier.validate_evidence_graph_v1(graph))


if __name__ == "__main__":
    unittest.main()
