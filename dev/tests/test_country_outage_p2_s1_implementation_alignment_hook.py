from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / ".codex/hooks/country_outage_agent_p2_s1_implementation_alignment.py"
SPEC = importlib.util.spec_from_file_location("p2_s1_implementation_alignment", HOOK_PATH)
assert SPEC is not None and SPEC.loader is not None
HOOK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOOK)


class ImplementationAlignmentHookTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths = [
            HOOK.TASK_PATH,
            HOOK.TARGET_PATH,
            HOOK.PLAN_PATH,
            HOOK.BASELINE_PATH,
            HOOK.DESIGN_CANDIDATE_PATH,
            HOOK.DESIGN_MANIFEST_PATH,
            HOOK.S1D6_PATH,
            HOOK.FINAL_PATH,
            Path("dev/tests/test_country_outage_p2_s1_implementation_alignment_hook.py"),
        ]
        for relative in self.paths:
            source = ROOT / relative
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def load(self, relative: Path) -> dict:
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def write(self, relative: Path, value: dict) -> None:
        (self.root / relative).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def refresh_baseline_digest(self, baseline: dict) -> None:
        baseline["content_digest"] = HOOK.object_digest(baseline, {"content_digest"})

    def assert_alignment_error(self, code: str, action) -> None:
        with self.assertRaises(HOOK.AlignmentError) as caught:
            action()
        self.assertIn(code, str(caught.exception))

    def test_s1ip0_positive_closes_effect_first_implementation_plan(self) -> None:
        receipt = HOOK.run_alignment(self.root, "S1I-P0")
        self.assertEqual(receipt["status"], "alignment_passed")
        self.assertEqual(receipt["stage"], "S1I-P0")
        self.assertEqual(receipt["design_candidate_id"], HOOK.DESIGN_CANDIDATE_ID)
        self.assertTrue(receipt["implementation_planning"])
        self.assertFalse(receipt["runtime_implemented"])
        self.assertFalse(receipt["production_deployed"])
        self.assertEqual(
            receipt["receipt_digest"],
            HOOK.object_digest(receipt, {"receipt_digest"}),
        )

    def test_rejects_target_without_user_effect_section(self) -> None:
        path = self.root / HOOK.TARGET_PATH
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("### 4.1 用户调查效果", "### 4.1 临时章节", 1), encoding="utf-8")
        self.assert_alignment_error(
            "target_effect_section_missing",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_wave_plan_without_effect_stage(self) -> None:
        path = self.root / HOOK.PLAN_PATH
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("## 十、W5：组合调查运行时、接口和页面", "## 十、临时阶段", 1), encoding="utf-8")
        self.assert_alignment_error(
            "implementation_wave_section_missing",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_baseline_content_tamper(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["effect_contract"]["user_effects"].append("ghost_effect")
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "baseline_digest_mismatch",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_tool_population_expansion_even_when_resigned(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["implementation_population"]["tool_ids"].append("TOOL-13")
        self.refresh_baseline_digest(baseline)
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "tool_population_drift",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_operator_population_merge_even_when_resigned(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["implementation_population"]["operator_ids"].remove("OP-16")
        self.refresh_baseline_digest(baseline)
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "operator_population_drift",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_atomicity_relaxation_even_when_resigned(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["atomicity_contract"]["one_tool_one_fact_population"] = False
        self.refresh_baseline_digest(baseline)
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "atomicity_contract_open",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_p2_1_fanout_smuggling_even_when_resigned(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["implementation_population"]["plan_capability_ids"].append("PLAN-CAP-02")
        self.refresh_baseline_digest(baseline)
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "plan_population_drift",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_runtime_overclaim_even_when_resigned(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["acceptance_layers"]["runtime_implemented"] = True
        self.refresh_baseline_digest(baseline)
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "implementation_status_overclaim",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_production_overclaim_even_when_resigned(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["acceptance_layers"]["production_deployed"] = True
        self.refresh_baseline_digest(baseline)
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "implementation_status_overclaim",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_sol_ds_order_drift_even_when_resigned(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["model_flow_contract"]["execution_order"] = [
            "ds_student", "gpt-5.6-sol", "host_grounding_and_validation"
        ]
        self.refresh_baseline_digest(baseline)
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "model_order_drift",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_frozen_design_candidate_tamper(self) -> None:
        candidate = self.load(HOOK.DESIGN_CANDIDATE_PATH)
        candidate["runtime_implemented"] = True
        self.write(HOOK.DESIGN_CANDIDATE_PATH, candidate)
        self.assert_alignment_error(
            "design_candidate_stale",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_duplicate_json_key(self) -> None:
        path = self.root / HOOK.BASELINE_PATH
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("{", '{"schema_version":"duplicate",', 1), encoding="utf-8")
        self.assert_alignment_error(
            "重复 JSON key",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_nan(self) -> None:
        path = self.root / HOOK.BASELINE_PATH
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace('"status": "implementation_planning_draft"', '"non_finite": NaN,\n  "status": "implementation_planning_draft"', 1), encoding="utf-8")
        self.assert_alignment_error(
            "禁止非有限 JSON 数值",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_w0_fails_closed_without_real_wave_evidence(self) -> None:
        self.assert_alignment_error(
            "无法严格读取 JSON",
            lambda: HOOK.run_alignment(self.root, "W0"),
        )

    def test_w1_rejects_missing_required_w0_dependency(self) -> None:
        evidence_path = self.root / HOOK.WAVE_EVIDENCE_ROOT / "W1.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence = {
            "schema_version": "country_outage_p2_s1_implementation_wave_evidence_v1",
            "stage": "W1",
            "status": "implementation_wave_accepted",
            "design_candidate_id": HOOK.DESIGN_CANDIDATE_ID,
            "baseline_content_digest": self.load(HOOK.BASELINE_PATH)["content_digest"],
            "implementation_candidate_id": "impl-candidate",
            "effect": HOOK.WAVE_CONTRACT["W1"]["effect"],
            "effect_verified": True,
            "implemented_unit_ids": HOOK.WAVE_CONTRACT["W1"]["unit_ids"],
            "atomic_split_tests_passed": True,
            "p2_1_units_included": [],
            "production_deployed": False,
            "test_receipts": [{"passed": True, "receipt_digest": "a" * 64}],
            "registry_snapshot": {"digest": "b" * 64},
            "prior_stage_receipt_digests": {},
        }
        self.write(HOOK.WAVE_EVIDENCE_ROOT / "W1.json", evidence)
        self.assert_alignment_error(
            "无法严格读取 JSON",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )


if __name__ == "__main__":
    unittest.main()
