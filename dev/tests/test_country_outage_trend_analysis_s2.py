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
    analyze_trend_profile_v1,
    compile_trend_profile_v1,
)


S0_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s0-v1.json"
S1_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s1-v1.json"
S2_VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s2.py"
S1_VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s1.py"


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageTrendAnalysisS2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.s1_verifier = load_module("s1_verifier_for_s2_test", S1_VERIFIER_PATH)
        cls.s2_verifier = load_module("s2_verifier_for_s2_test", S2_VERIFIER_PATH)
        cls.s0 = json.loads(S0_FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.s1 = json.loads(S1_FIXTURE_PATH.read_text(encoding="utf-8"))

    def request(self, curve_id: str):
        return self.s1_verifier.request_for_curve(self.s0, self.s1, curve_id)

    def profile(self, curve_id: str):
        return analyze_trend_profile_v1(
            compile_trend_profile_v1(self.request(curve_id))
        )

    def test_s2_candidate_verifier_passes(self) -> None:
        self.assertEqual(self.s2_verifier.validate(), [])

    def test_pattern_is_optional_summary_over_complete_structure(self) -> None:
        for curve_id, status in (("CURVE-05", "mixed"), ("CURVE-06", "unmatched")):
            with self.subTest(curve_id=curve_id):
                analysis = self.profile(curve_id)["analysis"]
                self.assertEqual(analysis["pattern"]["status"], status)
                self.assertIsNone(analysis["pattern"]["label"])
                self.assertEqual(len(analysis["key_points"]), 5)
                self.assertTrue(analysis["atomic_states"])
                self.assertTrue(analysis["phases"])
                self.assertTrue(analysis["derived_facts"])

    def test_tied_key_points_use_earliest_slot(self) -> None:
        analysis = self.profile("CURVE-04")["analysis"]
        points = self.s2_verifier.key_point_indices(analysis)
        self.assertEqual(points["extreme_minimum"], 2)
        self.assertEqual(points["largest_single_slot_drop_end"], 2)
        self.assertEqual(points["largest_single_slot_recovery_end"], 3)

    def test_multi_wave_is_not_collapsed_into_oscillation_or_single_wave(self) -> None:
        pattern = self.profile("CURVE-02")["analysis"]["pattern"]
        self.assertEqual(pattern["status"], "matched")
        self.assertEqual(pattern["label"], "multi_wave")
        self.assertEqual(pattern["features"]["abrupt_drop_indices"], [2, 5])

    def test_small_denominator_warns_without_amplifying_atomic_state(self) -> None:
        analysis = self.profile("CURVE-07")["analysis"]
        self.assertEqual(analysis["pattern"]["warnings"], ["small_denominator"])
        states = {item["state"] for item in analysis["atomic_states"]}
        self.assertEqual(states, {"stable", "rise", "decline"})

    def test_missing_and_unknown_do_not_cross_gap(self) -> None:
        for curve_id, expected_states in (
            ("CURVE-08", {"missing", "processing_gap", "not_observed"}),
            ("CURVE-09", {"unknown"}),
        ):
            with self.subTest(curve_id=curve_id):
                analysis = self.profile(curve_id)["analysis"]
                self.assertEqual(analysis["status"], "insufficient_data")
                self.assertEqual(analysis["key_points"], [])
                self.assertEqual(analysis["phases"], [])
                self.assertEqual(analysis["derived_facts"], [])
                self.assertEqual(
                    {item["state"] for item in analysis["atomic_states"]},
                    expected_states,
                )

    def test_window_ledger_exposes_formula_operands_units_and_sources(self) -> None:
        analysis = self.profile("CURVE-01")["analysis"]
        facts = {item["metric"]: item for item in analysis["derived_facts"]}
        self.assertEqual(facts["loss_magnitude"]["value"], 28)
        self.assertEqual(facts["extreme_to_end_rebound"]["value"], 12)
        self.assertEqual(facts["end_residual_from_start"]["value"], 16)
        self.assertEqual(
            facts["fixed_cohort_visibility_gap_integral"]["unit"],
            "prefix_vp_slot",
        )
        for fact in facts.values():
            self.assertTrue(fact["formula"])
            self.assertGreaterEqual(len(fact["operands"]), 2)
            self.assertTrue(fact["source_refs"])
            self.assertEqual(fact["rounding"]["decimal_places"], 6)

    def test_threshold_slots_never_claim_continuous_duration(self) -> None:
        thresholds = self.profile("CURVE-01")["analysis"]["window_ledger"][
            "threshold_slots"
        ]
        self.assertEqual(
            [item["observed_slot_count"] for item in thresholds],
            [9, 9, 4],
        )
        self.assertTrue(
            all(item["continuous_duration_claimed"] is False for item in thresholds)
        )

    def test_analysis_is_idempotent(self) -> None:
        profile = self.profile("CURVE-01")
        self.assertEqual(analyze_trend_profile_v1(profile), profile)

    def test_tampered_foundation_and_analysis_fail_closed(self) -> None:
        foundation = compile_trend_profile_v1(self.request("CURVE-01"))
        tampered_foundation = deepcopy(foundation)
        tampered_foundation["slots"][3]["value"] = 81
        with self.assertRaises(TrendProfileValidationError) as captured:
            analyze_trend_profile_v1(tampered_foundation)
        self.assertEqual(captured.exception.code, "profile_identity_conflict")

        analyzed = analyze_trend_profile_v1(foundation)
        tampered_analysis = deepcopy(analyzed)
        tampered_analysis["analysis"]["pattern"]["label"] = "plateau"
        with self.assertRaises(TrendProfileValidationError) as captured:
            analyze_trend_profile_v1(tampered_analysis)
        self.assertEqual(captured.exception.code, "analysis_identity_conflict")

    def test_injected_missing_slot_removes_cross_gap_outputs(self) -> None:
        request = self.request("CURVE-01")
        request["slots"][4]["state"] = "missing"
        request["slots"][4]["value"] = None
        analysis = analyze_trend_profile_v1(
            compile_trend_profile_v1(request)
        )["analysis"]
        self.assertEqual(analysis["status"], "insufficient_data")
        self.assertEqual(analysis["key_points"], [])
        self.assertEqual(analysis["window_ledger"]["status"], "unavailable")

    def test_country_rename_does_not_change_curve_interpretation(self) -> None:
        base_request = self.request("CURVE-01")
        renamed_request = deepcopy(base_request)
        renamed_request["snapshot"].update(
            {
                "event_reference": "country_outage/2000-01-01 00:00:00/XY/1/synthetic",
                "incident_id": "incident_renamed",
                "country_code": "XY",
                "publication_id": "publication_renamed",
            }
        )
        base = analyze_trend_profile_v1(compile_trend_profile_v1(base_request))
        renamed = analyze_trend_profile_v1(compile_trend_profile_v1(renamed_request))
        self.assertEqual(base["analysis"]["pattern"], renamed["analysis"]["pattern"])
        self.assertEqual(
            self.s2_verifier.key_point_indices(base["analysis"]),
            self.s2_verifier.key_point_indices(renamed["analysis"]),
        )


if __name__ == "__main__":
    unittest.main()
