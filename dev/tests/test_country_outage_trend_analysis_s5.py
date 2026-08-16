from __future__ import annotations

from copy import deepcopy
import importlib.util
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
    compile_contemporaneous_reference_v1,
    compile_trend_product_v1,
)


VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s5.py"


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageTrendAnalysisS5Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = load_module("s5_verifier_for_test", VERIFIER_PATH)
        cls.product = cls.verifier.build_candidate()
        cls.context = cls.product["contexts"]["contemporaneous_reference"]

    def test_s5_candidate_verifier_passes(self) -> None:
        self.assertEqual(self.verifier.validate(), [])

    def test_target_and_reference_use_same_normalized_population(self) -> None:
        self.assertEqual(self.context["status"], "complete")
        self.assertEqual(self.context["target"]["denominator"], 100)
        self.assertEqual(
            self.context["normalization"]["visibility"],
            "visible_prefix_vp_count / fixed_prefix_vp_denominator",
        )

    def test_small_unknown_and_degraded_projections_are_explicit(self) -> None:
        self.assertEqual(
            self.context["exclusion_reason_counts"],
            {"small_denominator": 1, "quality_not_complete": 1, "unknown_bucket": 1},
        )
        self.assertEqual(self.context["projection_bucket_count"], 8)
        self.assertEqual(self.context["comparable_country_count"], 5)

    def test_all_four_distribution_effects_are_recomputable(self) -> None:
        positions = self.context["distribution_positions"]
        self.assertEqual(positions["maximum_decline_percentage_points"]["empirical_percentile"], 80.0)
        self.assertEqual(positions["persistence_below_95_slot_count"]["empirical_percentile"], 80.0)
        self.assertEqual(positions["asn_migration_ratio"]["empirical_percentile"], 80.0)
        shape = next(item for item in self.context["curve_shape_distribution"] if item["is_target_shape"])
        self.assertEqual(shape["country_share"], 0.6)

    def test_common_fluctuation_is_not_failure_claim(self) -> None:
        common = self.context["common_fluctuation"]
        self.assertEqual(common["target_largest_drop_slot"]["declining_country_share"], 0.8)
        self.assertFalse(common["collector_failure_claim"])

    def test_reference_claim_is_shared_by_graph_report_and_qa(self) -> None:
        claim = next(
            node
            for node in self.product["evidence_graph"]["nodes"]
            if node.get("claim_kind") == "contemporaneous_reference"
        )
        answer = answer_trend_question_v1(self.product, "同期全球参照的百分位是多少？")
        self.assertEqual(answer["claim_refs"], [claim["node_id"]])
        self.assertIn(claim["node_id"], self.product["claim_ids"])
        self.assertIn("page", self.product["render_contract"]["surfaces"])
        self.assertIn("report", self.product["render_contract"]["surfaces"])

    def test_reference_identity_mismatch_fails_closed(self) -> None:
        profile = self.product["profile"]
        request = self.verifier.build_reference_input(profile)
        request["identity"]["mapping_version"] = ""
        with self.assertRaises(TrendProductValidationError) as captured:
            compile_contemporaneous_reference_v1(profile, request)
        self.assertEqual(captured.exception.code, "reference_identity_unavailable")

    def test_insufficient_target_is_preserved_as_degraded_claim(self) -> None:
        base = self.product
        request = self.verifier.build_reference_input(base["profile"])
        for projection in request["projections"]:
            if projection["country_code"] not in {"XZ", "__UNKNOWN__"}:
                projection["quality_status"] = "degraded"
        context = compile_contemporaneous_reference_v1(base["profile"], request)
        self.assertEqual(context["status"], "insufficient_data")
        product = compile_trend_product_v1(
            base["profile"],
            contemporaneous_reference_context=context,
        )
        claim = next(
            node
            for node in product["evidence_graph"]["nodes"]
            if node.get("claim_kind") == "contemporaneous_reference"
        )
        self.assertEqual(claim["values"]["status"], "insufficient_data")
        self.assertIn("不可用", claim["text"])

    def test_context_content_address_rejects_manual_rewrite(self) -> None:
        context = deepcopy(self.context)
        context["comparable_country_count"] += 1
        with self.assertRaises(TrendProductValidationError) as captured:
            compile_trend_product_v1(
                self.product["profile"],
                contemporaneous_reference_context=context,
            )
        self.assertEqual(
            captured.exception.code,
            "invalid_contemporaneous_reference_context",
        )


if __name__ == "__main__":
    unittest.main()
