from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPOSITORY_ROOT / "dev" / "tools" / "validate_country_outage_p1_s4.py"


def load_validator():
    specification = importlib.util.spec_from_file_location(
        "validate_country_outage_p1_s4", VALIDATOR_PATH
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("无法加载 S4 校验器")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageP1S4ValidatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = load_validator()
        cls.p0 = cls.validator.read_json(cls.validator.P0_CASES_PATH)
        cls.results = cls.validator.read_json(cls.validator.S4_RESULTS_PATH)
        cls.live = cls.validator.read_json(cls.validator.LIVE_EVIDENCE_PATH)
        cls.p0_base = cls.validator.read_json(cls.validator.P0_BASE_CASES_PATH)
        cls.identity = cls.validator.read_json(cls.validator.IDENTITY_PATH)
        cls.semantic_candidate = cls.validator.read_json(
            cls.validator.SEMANTIC_CANDIDATE_PATH
        )
        cls.semantic_evaluation = cls.validator.read_json(
            cls.validator.SEMANTIC_EVALUATION_PATH
        )
        cls.budgets = cls.validator.read_json(cls.validator.BUDGETS_PATH)
        cls.browser = cls.validator.read_json(cls.validator.BROWSER_API_PATH)
        cls.joint = cls.validator.read_json(cls.validator.JOINT_ACCEPTANCE_PATH)

    def test_35_case_contract_is_exact(self) -> None:
        self.assertEqual(
            self.validator.validate_results(self.p0, self.results, self.live), []
        )

    def test_hard_gate_cannot_be_claimed_without_p0_contract(self) -> None:
        changed = copy.deepcopy(self.results)
        changed["results"][0]["hard_gates_passed"] = ["fact_match"]
        errors = self.validator.validate_results(self.p0, changed)
        self.assertTrue(any("硬门" in error for error in errors))

    def test_answerability_cannot_be_softened(self) -> None:
        changed = copy.deepcopy(self.results)
        boundary = next(
            item for item in changed["results"] if item["case_id"] == "P013-B-02"
        )
        boundary["actual_answerability"] = "partial"
        errors = self.validator.validate_results(self.p0, changed)
        self.assertTrue(any("P013-B-02" in error for error in errors))

    def test_proof_pointer_must_resolve_same_raw_case(self) -> None:
        changed = copy.deepcopy(self.results)
        changed["results"][0]["proof"][0]["json_pointer"] = "/cases/1"
        errors = self.validator.validate_results(self.p0, changed, self.live)
        self.assertTrue(any("同一案例" in error for error in errors))

    def test_raw_fact_tampering_is_detected(self) -> None:
        changed = copy.deepcopy(self.live)
        case = next(
            item for item in changed["cases"] if item["case_id"] == "P013-D-01"
        )
        attempt = case["attempts"][0]
        evidence = attempt["turns"][0]["evidence"]
        fact = next(
            item
            for item in evidence
            if item["evidence_ref"] == "peaks.interrupted_prefix_count.value"
        )
        fact["value"] = 3854
        errors = self.validator.validate_live_evidence(
            self.p0, self.p0_base, changed, self.identity
        )
        self.assertTrue(any("冻结事实" in error for error in errors))

    def test_b05_economic_boundary_cannot_reuse_responsibility_text(self) -> None:
        changed = copy.deepcopy(self.live)
        case = next(
            item for item in changed["cases"] if item["case_id"] == "P013-B-05"
        )
        turn = case["attempts"][0]["turns"][0]
        economic = next(
            item
            for item in turn["results"]
            if item["normalized_kind"] == "economic_impact"
        )
        economic["text"] = "当前只有 RRC25，不能判断责任主体。"
        errors = self.validator.validate_live_evidence(
            self.p0, self.p0_base, changed, self.identity
        )
        self.assertTrue(any("economic_impact" in error for error in errors))

    def test_controlled_failure_pipeline_checkpoint_is_a_hard_gate(self) -> None:
        changed = copy.deepcopy(self.live)
        case = next(
            item for item in changed["cases"] if item["case_id"] == "P013-X-04"
        )
        case["controlled_failure_receipt"]["pipeline_checkpoints"][
            "tool_execution"
        ]["status"] = "passed"
        errors = self.validator.validate_live_evidence(
            self.p0, self.p0_base, changed, self.identity
        )
        self.assertTrue(any("Tool execution" in error for error in errors))

    def test_controlled_failure_state_tampering_is_detected(self) -> None:
        changed = copy.deepcopy(self.live)
        case = next(
            item for item in changed["cases"] if item["case_id"] == "P013-X-05"
        )
        receipt = case["controlled_failure_receipt"]["state_receipt"]
        receipt["after"]["evidence_state"]["revision"] += 1
        errors = self.validator.validate_live_evidence(
            self.p0, self.p0_base, changed, self.identity
        )
        self.assertTrue(any("evidence_state 未完整回滚" in error for error in errors))

    def test_current_prompt_and_grounding_are_hard_gates(self) -> None:
        changed = copy.deepcopy(self.semantic_evaluation)
        changed["grounding_legality"]["rate"] = 0.99
        errors = self.validator.validate_semantic_current_candidate(
            self.semantic_candidate, changed, self.identity
        )
        self.assertTrue(any("GroundingPlan" in error for error in errors))

    def test_runtime_budget_orders_model_before_proxy(self) -> None:
        changed = copy.deepcopy(self.budgets)
        changed["budgets"]["semantic_model_timeout_ms"] = 90000
        errors = self.validator.validate_budgets(changed)
        self.assertTrue(any("模型超时" in error for error in errors))

    def test_browser_receipt_must_bind_current_candidate(self) -> None:
        changed = copy.deepcopy(self.browser)
        changed["candidate_id"] = "p1-runtime-v2-stale"
        errors = self.validator.validate_browser_joint_acceptance(
            changed, self.joint, self.identity
        )
        self.assertTrue(any("浏览器/API 回执与当前候选" in error for error in errors))

    def test_joint_must_reference_same_conversation(self) -> None:
        changed = copy.deepcopy(self.joint)
        changed["browser_journey"]["conversation_id"] = "p1v2_other"
        errors = self.validator.validate_browser_joint_acceptance(
            self.browser, changed, self.identity
        )
        self.assertTrue(any("不是同一会话" in error for error in errors))

    def test_timeline_op03_receipt_is_a_hard_gate(self) -> None:
        changed = copy.deepcopy(self.browser)
        timeline = changed["raw_response"]["turns"][0]["answer"]
        timeline["execution_trace"]["nodes"] = [
            node for node in timeline["execution_trace"]["nodes"]
            if node["execution_unit"] != "OP-03"
        ]
        errors = self.validator.validate_browser_joint_acceptance(
            changed, self.joint, self.identity
        )
        self.assertTrue(any("OP-03" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
