from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = (
    REPOSITORY_ROOT / "dev" / "tools" / "validate_country_outage_p0_baseline.py"
)
CASE_PATH = REPOSITORY_ROOT / "evaluation" / "country-outage" / "p0-v1" / "cases.json"
EVIDENCE_PATH = (
    REPOSITORY_ROOT
    / "evaluation"
    / "country-outage"
    / "p0-v1"
    / "evidence"
    / "ir-20260227-r1.json"
)
MANIFEST_PATH = (
    REPOSITORY_ROOT / "evaluation" / "country-outage" / "p0-v1" / "manifest.json"
)


def load_validator():
    specification = importlib.util.spec_from_file_location(
        "validate_country_outage_p0_baseline",
        VALIDATOR_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载校验器：{VALIDATOR_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageP0BaselineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = load_validator()
        cls.case_set = cls.validator.read_json(CASE_PATH)
        cls.evidence = cls.validator.read_json(EVIDENCE_PATH)

    def test_evidence_and_35_case_contract_are_valid(self) -> None:
        self.assertEqual(self.validator.validate_evidence(self.evidence), [])
        self.assertEqual(
            self.validator.validate_case_set(self.case_set, self.evidence),
            [],
        )

    def test_case_count_drift_is_rejected(self) -> None:
        changed = copy.deepcopy(self.case_set)
        changed["cases"].pop()
        errors = self.validator.validate_case_set(changed, self.evidence)
        self.assertTrue(any("案例总数必须为 35" in error for error in errors))
        self.assertTrue(any("案例分类数量" in error for error in errors))

    def test_reference_fact_drift_is_rejected(self) -> None:
        changed = copy.deepcopy(self.case_set)
        changed["cases"][3]["expected"]["facts"][1]["value"] = 3854
        errors = self.validator.validate_case_set(changed, self.evidence)
        self.assertTrue(any("事实与证据不一致" in error for error in errors))

    def test_publication_binding_drift_is_rejected(self) -> None:
        changed = copy.deepcopy(self.case_set)
        changed["event_binding"]["publication_id"] = "publication-conflict"
        errors = self.validator.validate_case_set(changed, self.evidence)
        self.assertTrue(any("event_binding.publication_id" in error for error in errors))

    def test_multi_turn_without_context_gate_is_rejected(self) -> None:
        changed = copy.deepcopy(self.case_set)
        multi_turn = next(
            case for case in changed["cases"] if case["category"] == "multi_turn"
        )
        multi_turn["hard_gates"].remove("context_isolation")
        errors = self.validator.validate_case_set(changed, self.evidence)
        self.assertTrue(any("context_isolation" in error for error in errors))

    def test_exception_without_failure_closed_is_rejected(self) -> None:
        changed = copy.deepcopy(self.case_set)
        exception = next(
            case for case in changed["cases"] if case["category"] == "exception"
        )
        exception["expected"]["failure_closed"] = False
        errors = self.validator.validate_case_set(changed, self.evidence)
        self.assertTrue(any("failure_closed=true" in error for error in errors))

    def test_manifest_and_all_delivery_artifacts_are_valid(self) -> None:
        self.assertEqual(self.validator.validate_manifest(MANIFEST_PATH), [])


if __name__ == "__main__":
    unittest.main()
