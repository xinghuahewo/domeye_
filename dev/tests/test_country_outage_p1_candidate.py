from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = ROOT / "dev" / "tools" / "validate_country_outage_p1_candidate.py"
RESULT_PATH = ROOT / "evaluation" / "country-outage" / "p1-v1" / "candidate-result.json"


def load_validator():
    spec = importlib.util.spec_from_file_location("p1_candidate_validator", VALIDATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 P1 候选校验器")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CountryOutageP1CandidateValidatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_validator()
        cls.result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))

    def test_current_candidate_result_is_complete_and_bound(self):
        self.assertEqual(self.validator.validate_result(ROOT, self.result), [])

    def test_one_failed_case_cannot_be_hidden_by_average(self):
        value = copy.deepcopy(self.result)
        value["results"][0]["passed"] = False
        errors = self.validator.validate_result(ROOT, value)
        self.assertTrue(any("案例未通过" in error for error in errors))

    def test_identity_and_forbidden_assertion_gates_are_hard(self):
        value = copy.deepcopy(self.result)
        value["results"][0]["identity_failure_count"] = 1
        value["results"][1]["forbidden_assertion_hits"] = ["越界断言"]
        errors = self.validator.validate_result(ROOT, value)
        self.assertTrue(any("身份不一致" in error for error in errors))
        self.assertTrue(any("禁止断言" in error for error in errors))

    def test_manifest_rejects_artifact_digest_drift(self):
        manifest_path = ROOT / "evaluation" / "country-outage" / "p1-v1" / "manifest.json"
        if not manifest_path.exists():
            self.skipTest("manifest 在最终制品生成步骤写入")
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        value["artifacts"][0]["sha256"] = "0" * 64
        errors = self.validator.validate_manifest(ROOT, value, self.result)
        self.assertTrue(any("摘要漂移" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
