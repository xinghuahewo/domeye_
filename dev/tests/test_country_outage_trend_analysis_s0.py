from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = (
    REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s0.py"
)
FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "dev"
    / "fixtures"
    / "country-outage-trend-analysis-s0-v1.json"
)


def load_verifier_module():
    specification = importlib.util.spec_from_file_location(
        "verify_country_outage_trend_analysis_s0",
        VERIFIER_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 S0 校验器：{VERIFIER_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class CountryOutageTrendAnalysisS0Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = load_verifier_module()

    def test_frozen_s0_baseline_is_valid(self) -> None:
        self.assertEqual(self.verifier.validate(), [])

    def test_rrc25_boundary_fails_closed(self) -> None:
        candidate = copy.deepcopy(load_fixture())
        candidate["boundaries"]["collector"] = "rrc00"
        errors = self.verifier.validate(candidate)
        self.assertTrue(any("collector" in error for error in errors), errors)

    def test_non_observed_value_cannot_be_filled_with_zero(self) -> None:
        candidate = copy.deepcopy(load_fixture())
        candidate["acceptance_curves"][7]["slots"][2]["value"] = 0
        errors = self.verifier.validate(candidate)
        self.assertTrue(any("禁止补零" in error for error in errors), errors)

    def test_unknown_slots_are_not_counted_as_observed(self) -> None:
        candidate = copy.deepcopy(load_fixture())
        candidate["acceptance_curves"][8]["expected"]["slot_state_counts"] = {
            "observed": 12
        }
        errors = self.verifier.validate(candidate)
        self.assertTrue(any("unknown 独立语义" in error for error in errors), errors)

    def test_curve_key_point_must_be_recomputable(self) -> None:
        candidate = copy.deepcopy(load_fixture())
        candidate["acceptance_curves"][0]["expected"]["key_points"][
            "extreme_index"
        ] = 6
        errors = self.verifier.validate(candidate)
        self.assertTrue(any("不可由原始槽重算" in error for error in errors), errors)

    def test_value_gate_cannot_claim_unmeasured_results(self) -> None:
        candidate = copy.deepcopy(load_fixture())
        candidate["value_gate"]["candidate"]["measurement_status"] = "passed"
        errors = self.verifier.validate(candidate)
        self.assertTrue(any("不得伪造" in error for error in errors), errors)

    def test_runtime_identity_cannot_be_promoted_by_s0(self) -> None:
        candidate = copy.deepcopy(load_fixture())
        candidate["identity_separation"]["current_runtime"] = "verified"
        errors = self.verifier.validate(candidate)
        self.assertTrue(any("严格分离" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
