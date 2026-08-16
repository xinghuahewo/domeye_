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

from services.country_outage_trend_profile import (  # noqa: E402
    TrendProfileValidationError,
    align_activity_context_v1,
    compare_address_families_v1,
    compile_asn_state_context_v1,
)


S0_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s0-v1.json"
S1_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s1-v1.json"
S3_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s3-v1.json"
S1_VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s1.py"
S3_VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s3.py"


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageTrendAnalysisS3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.s1_verifier = load_module("s1_verifier_for_s3_test", S1_VERIFIER_PATH)
        cls.s3_verifier = load_module("s3_verifier_for_s3_test", S3_VERIFIER_PATH)
        cls.s0 = json.loads(S0_FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.s1 = json.loads(S1_FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.s3 = json.loads(S3_FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.ipv4, cls.ipv6 = cls.s3_verifier.build_address_family_profiles(
            cls.s0, cls.s1, cls.s3, cls.s1_verifier
        )
        cls.base_profile = cls.s3_verifier.analyzed_curve(
            cls.s0,
            cls.s1,
            cls.s1_verifier,
            cls.s3["asn_case"]["profile_curve_id"],
        )
        cls.asn_rows = cls.s3_verifier.build_asn_rows(cls.s3)
        cls.tracks = cls.s3_verifier.build_activity_tracks(cls.s3)

    def test_s3_candidate_verifier_passes(self) -> None:
        self.assertEqual(self.s3_verifier.validate(), [])

    def test_address_family_comparison_always_carries_both_denominators(self) -> None:
        context = compare_address_families_v1(self.ipv4, self.ipv6)
        self.assertEqual(context["families"]["ipv4"]["denominator"]["value"], 1000)
        self.assertEqual(context["families"]["ipv6"]["denominator"]["value"], 100)
        self.assertEqual(context["comparison"]["denominator_ratio"], 10)
        for slot in context["comparison"]["divergence_slots"]:
            self.assertEqual(slot["ipv4_denominator"], 1000)
            self.assertEqual(slot["ipv6_denominator"], 100)

    def test_address_family_maximum_divergence_and_extreme_alignment(self) -> None:
        comparison = compare_address_families_v1(self.ipv4, self.ipv6)["comparison"]
        self.assertEqual(comparison["maximum_divergence"]["slot_index"], 2)
        self.assertEqual(
            comparison["maximum_divergence"]["ipv4_minus_ipv6_percentage_points"],
            17,
        )
        self.assertEqual(comparison["extreme_alignment"]["relation"], "same_slot")
        self.assertIn("address_family_denominator_asymmetry", comparison["warnings"])

    def test_address_family_revision_conflict_fails_closed(self) -> None:
        changed = self.s3_verifier.analyzed_curve(
            self.s0,
            self.s1,
            self.s1_verifier,
            "CURVE-02",
            population="ipv6_fixed_prefix_vp",
            snapshot_mutation={"revision": 2},
        )
        with self.assertRaises(TrendProfileValidationError) as captured:
            compare_address_families_v1(self.ipv4, changed)
        self.assertEqual(captured.exception.code, "address_family_identity_conflict")

    def test_address_family_quality_insufficiency_degrades_without_cross_gap_comparison(self) -> None:
        degraded_ipv6 = self.s3_verifier.analyzed_curve(
            self.s0,
            self.s1,
            self.s1_verifier,
            "CURVE-08",
            population="ipv6_fixed_prefix_vp",
        )
        context = compare_address_families_v1(self.ipv4, degraded_ipv6)
        self.assertEqual(context["comparison"]["status"], "insufficient_data")
        self.assertEqual(context["comparison"]["divergence_slots"], [])
        self.assertIsNone(context["comparison"]["maximum_divergence"])
        self.assertIsNone(context["comparison"]["extreme_alignment"])
        self.assertIsNone(context["families"]["ipv6"]["end_residual"])

    def test_unknown_closes_every_asn_slot_population(self) -> None:
        context = compile_asn_state_context_v1(self.base_profile, self.asn_rows)
        for slot in context["slot_population"]:
            self.assertEqual(sum(slot["state_counts"].values()), 5)
            self.assertEqual(slot["state_counts"]["unknown"], 1)
        self.assertEqual(
            context["slot_population"][-1]["state_counts"],
            {
                "fully_visible": 2,
                "partially_visible": 1,
                "fully_invisible": 1,
                "unknown": 1,
            },
        )

    def test_asn_transition_matrices_are_four_by_four_and_closed(self) -> None:
        context = compile_asn_state_context_v1(self.base_profile, self.asn_rows)
        for matrix in context["transition_matrices"]:
            self.assertEqual(len(matrix["cells"]), 16)
            self.assertEqual(sum(cell["asn_count"] for cell in matrix["cells"]), 5)

    def test_asn_priority_keeps_scale_and_persistence_without_score(self) -> None:
        context = compile_asn_state_context_v1(self.base_profile, self.asn_rows)
        views = context["priority_views"]
        self.assertEqual(
            [item["asn"] for item in views["by_observation_scale"]],
            [100, 200, 300, 400, 500],
        )
        self.assertEqual(
            [item["asn"] for item in views["by_persistence"]],
            [200, 300, 500, 100, 400],
        )
        self.assertIsNone(views["single_impact_score"])
        for item in [*views["by_observation_scale"], *views["by_persistence"]]:
            self.assertIn("baseline_prefix_vp_count", item)
            self.assertIn("longest_observed_non_fully_visible_run", item)

    def test_persistent_not_at_start_is_not_confused_with_unknown(self) -> None:
        context = compile_asn_state_context_v1(self.base_profile, self.asn_rows)
        values = {item["asn"]: item for item in context["asns"]}
        self.assertTrue(values[200]["persistent_not_at_start"])
        self.assertTrue(values[300]["persistent_not_at_start"])
        self.assertFalse(values[400]["persistent_not_at_start"])
        self.assertEqual(values[400]["start_state"], "unknown")
        self.assertEqual(values[400]["end_state"], "unknown")

    def test_activity_tracks_keep_populations_and_do_no_cross_arithmetic(self) -> None:
        context = align_activity_context_v1(self.base_profile, self.tracks)
        self.assertEqual(
            {track["statistical_population"] for track in context["tracks"]},
            {
                "update_message",
                "announce_message",
                "withdraw_message",
                "resource_record",
            },
        )
        self.assertFalse(context["cross_population_arithmetic_performed"])
        self.assertIsNone(context["common_impact_score"])
        self.assertIsNone(context["causal_claim"])

    def test_activity_relations_are_time_only(self) -> None:
        context = align_activity_context_v1(self.base_profile, self.tracks)
        relations = {item["relation"] for item in context["temporal_relations"]}
        self.assertTrue({"same_slot", "adjacent_slot", "lagged_slot"}.issubset(relations))
        self.assertTrue(
            all(item["causal_interpretation"] is None for item in context["temporal_relations"])
        )

    def test_invalid_asn_state_fails_closed(self) -> None:
        rows = deepcopy(self.asn_rows)
        rows[0]["slots"][0]["state"] = "visible"
        with self.assertRaises(TrendProfileValidationError) as captured:
            compile_asn_state_context_v1(self.base_profile, rows)
        self.assertEqual(captured.exception.code, "invalid_asn_state")

    def test_non_observed_activity_slot_rejects_value(self) -> None:
        tracks = deepcopy(self.tracks)
        tracks[0]["slots"][0]["state"] = "missing"
        with self.assertRaises(TrendProfileValidationError) as captured:
            align_activity_context_v1(self.base_profile, tracks)
        self.assertEqual(captured.exception.code, "non_observed_activity_value")

    def test_all_three_contexts_repeat_deterministically(self) -> None:
        first_family = compare_address_families_v1(self.ipv4, self.ipv6)
        second_family = compare_address_families_v1(
            deepcopy(self.ipv4), deepcopy(self.ipv6)
        )
        self.assertEqual(first_family, second_family)
        self.assertEqual(
            compile_asn_state_context_v1(self.base_profile, self.asn_rows),
            compile_asn_state_context_v1(
                deepcopy(self.base_profile), deepcopy(self.asn_rows)
            ),
        )
        self.assertEqual(
            align_activity_context_v1(self.base_profile, self.tracks),
            align_activity_context_v1(
                deepcopy(self.base_profile), deepcopy(self.tracks)
            ),
        )


if __name__ == "__main__":
    unittest.main()
