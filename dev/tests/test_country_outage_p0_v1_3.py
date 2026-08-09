from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPOSITORY_ROOT / "dev" / "tools" / "validate_country_outage_p0_v1_3.py"
ROOT = REPOSITORY_ROOT / "evaluation" / "country-outage" / "p0-v1-3"


def load_validator():
    specification = importlib.util.spec_from_file_location(
        "validate_country_outage_p0_v1_3", VALIDATOR_PATH
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("无法加载 P0 v1.3 校验器")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageP0V13Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = load_validator()
        cls.live = cls.validator.read_json(ROOT / "evidence" / "live-probe-20260809.json")
        cls.surface = cls.validator.read_json(ROOT / "system-surface.json")
        cls.ledger = cls.validator.read_json(ROOT / "capability-ledger.json")
        cls.unknowns = cls.validator.read_json(ROOT / "unknown-ledger.json")
        cls.oracle = cls.validator.read_json(ROOT / "oracle-seed.json")
        cls.cases = cls.validator.read_json(ROOT / "cases.json")
        cls.disposition = cls.validator.read_json(ROOT / "p1-disposition.json")
        cls.capability_ids = {
            item["capability_id"] for item in cls.ledger["capabilities"]
        }

    def test_live_runtime_snapshot_is_bounded(self) -> None:
        self.assertEqual(self.validator.validate_live(self.live), [])

    def test_surface_and_unknowns_are_complete(self) -> None:
        self.assertEqual(
            self.validator.validate_surface(self.surface, self.capability_ids), []
        )
        self.assertEqual(self.validator.validate_unknowns(self.unknowns), [])

    def test_capability_ledger_and_p1_partition_are_complete(self) -> None:
        self.assertEqual(self.validator.validate_ledger(self.ledger), [])
        self.assertEqual(
            self.validator.validate_disposition(self.disposition, self.capability_ids),
            [],
        )

    def test_oracle_seed_is_valid(self) -> None:
        self.assertEqual(
            self.validator.validate_oracle(self.oracle, self.capability_ids), []
        )

    def test_35_case_revision_inherits_immutable_truth(self) -> None:
        self.assertEqual(
            self.validator.validate_cases(self.cases, self.capability_ids), []
        )

    def test_page_visible_without_page_evidence_is_rejected(self) -> None:
        changed = copy.deepcopy(self.ledger)
        changed["capabilities"][0]["evidence_dimensions"]["page_consumes"] = False
        errors = self.validator.validate_ledger(changed)
        self.assertTrue(any("page_visible" in error for error in errors))

    def test_feasibility_cannot_be_promoted_to_api(self) -> None:
        changed = copy.deepcopy(self.ledger)
        candidate = next(
            item for item in changed["capabilities"] if item["capability_id"] == "CAP-016"
        )
        candidate["evidence_dimensions"]["api_published"] = True
        errors = self.validator.validate_ledger(changed)
        self.assertTrue(any("越级" in error for error in errors))

    def test_unknown_without_next_validation_is_rejected(self) -> None:
        changed = copy.deepcopy(self.unknowns)
        changed["unknowns"][0]["next_validation"] = ""
        errors = self.validator.validate_unknowns(changed)
        self.assertTrue(any("next_validation" in error for error in errors))

    def test_oracle_truth_drift_is_rejected(self) -> None:
        changed = copy.deepcopy(self.oracle)
        changed["seeds"][1]["expected"]["maximum"] = 3854
        errors = self.validator.validate_oracle(changed, self.capability_ids)
        self.assertTrue(any("峰值真值漂移" in error for error in errors))

    def test_timeline_oracle_without_evidence_is_rejected(self) -> None:
        changed = copy.deepcopy(self.oracle)
        timeline = next(
            item for item in changed["seeds"] if item["oracle_id"] == "ORC-09"
        )
        timeline["expected"]["ordered_fact_nodes"][2]["evidence_ref"] = ""
        errors = self.validator.validate_oracle(changed, self.capability_ids)
        self.assertTrue(any("ORC-09" in error for error in errors))

    def test_as_peak_oracle_drift_is_rejected(self) -> None:
        changed = copy.deepcopy(self.oracle)
        peaks = next(
            item for item in changed["seeds"] if item["oracle_id"] == "ORC-10"
        )
        peaks["expected"]["affected_asn_count"]["maximum"] = 525
        errors = self.validator.validate_oracle(changed, self.capability_ids)
        self.assertTrue(any("ORC-10" in error for error in errors))

    def test_case_capability_and_question_drift_are_rejected(self) -> None:
        changed = copy.deepcopy(self.cases)
        changed["cases"][0]["question"] = "另一个问题"
        changed["cases"][0]["capability_ids"] = ["CAP-999"]
        errors = self.validator.validate_cases(changed, self.capability_ids)
        self.assertTrue(any("不可变 base case" in error for error in errors))
        self.assertTrue(any("未知能力" in error for error in errors))

    def test_partial_case_must_answer_observable_subgoal(self) -> None:
        changed = copy.deepcopy(self.cases)
        case = next(
            item for item in changed["cases"] if item["case_id"] == "P013-B-01"
        )
        case["capability_ids"] = ["CAP-023"]
        errors = self.validator.validate_cases(changed, self.capability_ids)
        self.assertTrue(any("P013-B-01" in error for error in errors))

    def test_as_scope_case_requires_concrete_peak_truth(self) -> None:
        changed = copy.deepcopy(self.cases)
        case = next(
            item for item in changed["cases"] if item["case_id"] == "P013-D-08"
        )
        case["additional_expected_facts"] = []
        errors = self.validator.validate_cases(changed, self.capability_ids)
        self.assertTrue(any("P013-D-08" in error for error in errors))

    def test_disposition_overlap_is_rejected(self) -> None:
        changed = copy.deepcopy(self.disposition)
        changed["reject"].append(changed["adopt"][0])
        errors = self.validator.validate_disposition(changed, self.capability_ids)
        self.assertTrue(any("数量" in error or "互斥" in error for error in errors))

    def test_manifest_and_stage_receipts_are_valid(self) -> None:
        receipts = self.validator.read_json(ROOT / "stage-receipts.json")
        self.assertEqual(self.validator.validate_stage_receipts(receipts), [])
        self.assertEqual(
            self.validator.validate_manifest(ROOT / "manifest.json"), []
        )


if __name__ == "__main__":
    unittest.main()
