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
    compile_trend_profile_v1,
    profile_compatibility_v1,
)


VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s1.py"
S0_FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "dev"
    / "fixtures"
    / "country-outage-trend-analysis-s0-v1.json"
)
S1_FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "dev"
    / "fixtures"
    / "country-outage-trend-analysis-s1-v1.json"
)


def load_verifier_module():
    specification = importlib.util.spec_from_file_location(
        "verify_country_outage_trend_analysis_s1",
        VERIFIER_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 S1 校验器：{VERIFIER_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageTrendAnalysisS1Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = load_verifier_module()
        cls.s0 = json.loads(S0_FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.s1 = json.loads(S1_FIXTURE_PATH.read_text(encoding="utf-8"))

    def request(self, curve_id: str, **kwargs):
        return self.verifier.request_for_curve(
            self.s0,
            self.s1,
            curve_id,
            **kwargs,
        )

    def test_s1_candidate_verifier_passes(self) -> None:
        self.assertEqual(self.verifier.validate(), [])

    def test_same_input_is_byte_semantically_deterministic(self) -> None:
        request = self.request("CURVE-01")
        first = compile_trend_profile_v1(request)
        second = compile_trend_profile_v1(deepcopy(request))
        self.assertEqual(first, second)
        self.assertEqual(
            first["profile_id"],
            f"trend_profile_v1_{first['input_digest'][:32]}",
        )

    def test_revision_creates_new_profile_without_rewriting_old(self) -> None:
        request = self.request("CURVE-01")
        first = compile_trend_profile_v1(request)
        revised = deepcopy(request)
        revised["snapshot"]["revision"] = 2
        revised["snapshot"]["publication_id"] = "publication_revision_2"
        second = compile_trend_profile_v1(revised)
        self.assertNotEqual(first["profile_id"], second["profile_id"])
        compatibility = profile_compatibility_v1(first, second)
        self.assertFalse(compatibility["compatible"])
        self.assertIn("snapshot.revision", compatibility["mismatches"])

    def test_all_nine_curves_keep_quality_before_analysis(self) -> None:
        expected = {
            "CURVE-01": "complete",
            "CURVE-02": "complete",
            "CURVE-03": "complete",
            "CURVE-04": "complete",
            "CURVE-05": "complete",
            "CURVE-06": "complete",
            "CURVE-07": "complete",
            "CURVE-08": "degraded",
            "CURVE-09": "unavailable",
        }
        for curve_id, quality in expected.items():
            with self.subTest(curve_id=curve_id):
                profile = compile_trend_profile_v1(self.request(curve_id))
                self.assertEqual(profile["quality"]["status"], quality)
                self.assertEqual(
                    profile["analysis"]["status"],
                    "not_computed_in_s1",
                )
                self.assertEqual(profile["analysis"]["key_points"], [])
                self.assertEqual(profile["analysis"]["phases"], [])

    def test_missing_and_unknown_remain_distinct(self) -> None:
        missing = compile_trend_profile_v1(self.request("CURVE-08"))
        counts = missing["quality"]["slot_state_counts"]
        self.assertEqual(counts["missing"], 1)
        self.assertEqual(counts["processing_gap"], 1)
        self.assertEqual(counts["not_observed"], 1)
        unknown = compile_trend_profile_v1(self.request("CURVE-09"))
        self.assertEqual(unknown["quality"]["slot_state_counts"]["unknown"], 12)
        self.assertEqual(unknown["quality"]["observed_slot_count"], 0)

    def test_non_observed_slot_rejects_zero_fill(self) -> None:
        request = self.request("CURVE-08")
        request["slots"][2]["value"] = 0
        with self.assertRaises(TrendProfileValidationError) as captured:
            compile_trend_profile_v1(request)
        self.assertEqual(captured.exception.code, "non_observed_value_present")

    def test_window_start_baseline_degrades_when_start_is_unknown(self) -> None:
        profile = compile_trend_profile_v1(
            self.request("CURVE-09", baseline_type="window_start")
        )
        self.assertEqual(profile["baseline"]["type"], "unavailable")
        self.assertIsNone(profile["baseline"]["value"])
        self.assertIn(
            "window_start_not_observed",
            profile["baseline"]["limitations"],
        )

    def test_four_baseline_types_are_distinct_and_not_normal_band(self) -> None:
        for baseline_case in self.s1["baseline_cases"]:
            with self.subTest(baseline=baseline_case["type"]):
                request = self.request(
                    "CURVE-01",
                    baseline_type=baseline_case["type"],
                    baseline_override=baseline_case,
                )
                baseline = compile_trend_profile_v1(request)["baseline"]
                self.assertEqual(baseline["type"], baseline_case["type"])
                self.assertEqual(
                    baseline["interpretation"],
                    "observation_reference_not_normal_baseline",
                )

    def test_ratio_and_percentage_point_keep_units_and_denominator(self) -> None:
        for unit, scale, expected_baseline in (
            ("ratio", 1.0, 1),
            ("percentage_point", 100.0, 100),
        ):
            with self.subTest(unit=unit):
                request = self.request("CURVE-01")
                denominator = request["metric"]["denominator"]["value"]
                request["metric"]["unit"] = unit
                for slot in request["slots"]:
                    slot["value"] = slot["value"] / denominator * scale
                profile = compile_trend_profile_v1(request)
                self.assertEqual(profile["metric"]["unit"], unit)
                self.assertEqual(
                    profile["metric"]["denominator"]["value"],
                    denominator,
                )
                self.assertEqual(profile["baseline"]["value"], expected_baseline)

    def test_direct_comparison_rejects_different_denominator(self) -> None:
        standard = compile_trend_profile_v1(self.request("CURVE-01"))
        small = compile_trend_profile_v1(self.request("CURVE-07"))
        result = profile_compatibility_v1(standard, small)
        self.assertFalse(result["compatible"])
        self.assertIn("metric.denominator", result["mismatches"])

    def test_wrong_collector_fails_closed(self) -> None:
        request = self.request("CURVE-01")
        request["snapshot"]["collector_id"] = "rrc00"
        with self.assertRaises(TrendProfileValidationError) as captured:
            compile_trend_profile_v1(request)
        self.assertEqual(captured.exception.code, "unsupported_collector")

    def test_reference_grid_conflict_fails_closed(self) -> None:
        request = self.request(
            "CURVE-01",
            baseline_type="contemporaneous_reference",
        )
        request["baseline"]["reference_time_grid"]["slot_seconds"] = 60
        with self.assertRaises(TrendProfileValidationError) as captured:
            compile_trend_profile_v1(request)
        self.assertEqual(captured.exception.code, "reference_time_grid_conflict")


if __name__ == "__main__":
    unittest.main()
