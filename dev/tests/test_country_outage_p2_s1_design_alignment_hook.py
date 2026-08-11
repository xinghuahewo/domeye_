from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = (
    REPO_ROOT / ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py"
)
SPEC = importlib.util.spec_from_file_location("p2_s1_design_alignment", HOOK_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"无法加载 Hook：{HOOK_PATH}")
HOOK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOOK)


class CountryOutageP2S1DesignAlignmentHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for relative in (HOOK.TASK_SPEC, HOOK.PHASE_PLAN):
            source = REPO_ROOT / relative
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _replace(self, relative: Path, old: str, new: str) -> None:
        path = self.root / relative
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def _copy_stage_artifacts(self, stage: str) -> None:
        for relative in HOOK.ARTIFACTS_BY_STAGE[stage]:
            source = REPO_ROOT / relative
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def _mutate_json(self, relative: Path, mutate) -> None:
        path = self.root / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        mutate(payload)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _assert_error(self, code: str, stage: str = "S1D-0") -> None:
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.run_alignment(
                self.root,
                stage,
                require_prior_receipts=False,
            )
        self.assertEqual(code, captured.exception.code)

    def test_s1d0_passes_with_exact_population_and_boundaries(self) -> None:
        receipt = HOOK.run_alignment(self.root, "S1D-0")
        self.assertEqual("alignment_passed", receipt["status"])
        self.assertEqual("S1D-0", receipt["stage"])
        self.assertTrue(receipt["design_only"])
        self.assertFalse(receipt["runtime_implemented"])
        self.assertFalse(receipt["production_deployed"])
        self.assertIn("question_population_28_of_28", receipt["checks"])
        self.assertIn("tool_operator_stage_separation", receipt["checks"])

    def test_missing_question_is_rejected(self) -> None:
        path = self.root / HOOK.TASK_SPEC
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        path.write_text(
            "".join(line for line in lines if not line.startswith("| Q33 |")),
            encoding="utf-8",
        )
        self._assert_error("question_coverage_mismatch")

    def test_duplicate_question_is_rejected(self) -> None:
        path = self.root / HOOK.TASK_SPEC
        text = path.read_text(encoding="utf-8")
        q01 = next(line for line in text.splitlines() if line.startswith("| Q01 |"))
        marker = "<!-- QUESTION_MAP_END -->"
        path.write_text(text.replace(marker, f"{q01}\n{marker}", 1), encoding="utf-8")
        self._assert_error("question_duplicate")

    def test_missing_function_atomicity_definition_is_rejected(self) -> None:
        self._replace(
            HOOK.TASK_SPEC,
            "execution_unit_function_atomicity",
            "execution-unit-function-atomicity",
        )
        self._assert_error("function_atomicity_marker_missing")

    def test_composite_tool_record_is_rejected(self) -> None:
        record = {
            "unit_id": "TOOL-07",
            "atomic_capability_id": "read.as_prefix_members",
            "single_responsibility": "查询成员并计算严重性排名",
            "output_population": "as_prefix_members",
            "composition_location": "investigation_plan",
            "embedded_capabilities": ["rank_as_severity"],
            "split_test": {"disposition": "atomic_as_designed"},
        }
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK._validate_atomic_unit_records(
                [record],
                expected_ids=("TOOL-07",),
                population_key="output_population",
                kind="tool",
            )
        self.assertEqual("composite_execution_unit_forbidden", captured.exception.code)

    def test_missing_evidence_before_teacher_rule_is_rejected(self) -> None:
        self._replace(
            HOOK.TASK_SPEC,
            "evidence_truth_precedes_teacher",
            "teacher_truth_precedes_evidence",
        )
        self._assert_error("dual_model_marker_missing")

    def test_ds_before_sol_is_rejected(self) -> None:
        self._replace(
            HOOK.TASK_SPEC,
            "→ gpt-5.6-sol 生成 TeacherSemanticPlan",
            "→ DS 生成 StudentSemanticPlan",
        )
        self._assert_error("model_execution_order_drift")

    def test_missing_plan_stage_is_rejected(self) -> None:
        self._replace(
            HOOK.PHASE_PLAN,
            "## 六、S1D-2：Tool 设计",
            "## 六、Tool 设计",
        )
        self._assert_error("plan_stage_missing")

    def test_tool_and_operator_combined_stage_is_rejected(self) -> None:
        self._replace(
            HOOK.PHASE_PLAN,
            "## 六、S1D-2：Tool 设计",
            "## 六、S1D-2：Tool 与 Operator 设计",
        )
        self._assert_error("tool_operator_stage_separation_missing")

    def test_design_only_boundary_drift_is_rejected(self) -> None:
        self._replace(
            HOOK.TASK_SPEC,
            "生产部署：禁止。远程写入：禁止。运行时实现：本任务不执行。",
            "生产部署：允许。远程写入：允许。运行时实现：本任务执行。",
        )
        self._assert_error("task_spec_marker_missing")

    def test_future_stage_without_artifacts_is_rejected(self) -> None:
        self._assert_error("artifact_missing", stage="S1D-1")

    def test_s1d1_passes_with_closed_question_capability_and_atomic_unit_maps(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        receipt = HOOK.run_alignment(
            self.root,
            "S1D-1",
            require_prior_receipts=False,
        )
        self.assertEqual("alignment_passed", receipt["status"])
        self.assertIn("execution_unit_atomic_decomposition", receipt["checks"])
        self.assertIn("shared_answer_binding_contract", receipt["checks"])

    def test_s1d1_rejects_q24_runtime_boundary_drift(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-1"][0]

        def mutate(payload) -> None:
            q24 = next(item for item in payload["questions"] if item["question_id"] == "Q24")
            q24["answerability"] = "new_tool_operator_required"

        self._mutate_json(relative, mutate)
        self._assert_error("deferred_boundary_missing", stage="S1D-1")

    def test_s1d1_rejects_atomic_decomposition_population_drift(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-1"][2]

        def mutate(payload) -> None:
            decision = next(
                item for item in payload["decisions"] if item["candidate_id"] == "set_relation"
            )
            decision["replacement_unit_ids"].remove("OP-28")

        self._mutate_json(relative, mutate)
        self._assert_error("atomic_decomposition_mismatch", stage="S1D-1")

    def test_s1d1_rejects_oracle_seed_scenario_gap(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-1"][1]

        def mutate(payload) -> None:
            del payload["questions"][0]["scenario_expectations"]["large_result"]

        self._mutate_json(relative, mutate)
        self._assert_error("oracle_seed_scenario_coverage_mismatch", stage="S1D-1")

    def test_s1d1_rejects_model_execution_order_drift(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-1"][3]

        def mutate(payload) -> None:
            payload["execution_order"] = ["ds_student", "gpt-5.6-sol"]

        self._mutate_json(relative, mutate)
        self._assert_error("model_execution_order_drift", stage="S1D-1")

    def test_s1d1_rejects_prior_receipt_bound_to_stale_documents(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        s1d0 = HOOK.run_alignment(self.root, "S1D-0")
        HOOK.write_receipt(
            self.root,
            HOOK.RECEIPT_ROOT / "S1D-0.json",
            s1d0,
        )
        task_spec = self.root / HOOK.TASK_SPEC
        task_spec.write_text(
            task_spec.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.run_alignment(self.root, "S1D-1")
        self.assertEqual("prior_receipt_stale", captured.exception.code)

    def test_prior_receipt_binds_stage_artifact_digests(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        s1d0 = HOOK.run_alignment(self.root, "S1D-0")
        HOOK.write_receipt(self.root, HOOK.RECEIPT_ROOT / "S1D-0.json", s1d0)
        s1d1 = HOOK.run_alignment(self.root, "S1D-1")
        HOOK.write_receipt(self.root, HOOK.RECEIPT_ROOT / "S1D-1.json", s1d1)
        capability_map = self.root / HOOK.ARTIFACTS_BY_STAGE["S1D-1"][0]
        capability_map.write_text(
            capability_map.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.run_alignment(self.root, "S1D-2")
        self.assertEqual("prior_receipt_stale", captured.exception.code)

    def test_s1d2_passes_with_typed_atomic_tool_contracts(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        receipt = HOOK.run_alignment(
            self.root,
            "S1D-2",
            require_prior_receipts=False,
        )
        self.assertEqual("alignment_passed", receipt["status"])
        self.assertIn("route_state_materialized_view_boundary", receipt["checks"])
        self.assertIn("window_path_association_semantics", receipt["checks"])

    def test_s1d2_rejects_runtime_ready_claim(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]

        def mutate(payload) -> None:
            payload["tools"][0]["runtime_ready_claim"] = True

        self._mutate_json(relative, mutate)
        self._assert_error("runtime_claim_forbidden", stage="S1D-2")

    def test_s1d2_rejects_duplicate_json_key(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]
        path = self.root / relative
        text = path.read_text(encoding="utf-8")
        needle = '"optional": [\n          "page_token"\n        ],'
        replacement = (
            '"optional": [\n          "page_token"\n        ],\n'
            '        "optional": [\n          "page_token"\n        ],'
        )
        self.assertIn(needle, text)
        path.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
        self._assert_error("artifact_json_duplicate_key", stage="S1D-2")

    def test_s1d2_rejects_empty_ordered_path_contract(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]

        def mutate(payload) -> None:
            tool12 = next(item for item in payload["tools"] if item["unit_id"] == "TOOL-12")
            del tool12["output_field_schemas"]["path_segments"]["minItems"]

        self._mutate_json(relative, mutate)
        self._assert_error("empty_ordered_path_allowed", stage="S1D-2")

    def test_s1d2_rejects_missing_common_path_status(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]

        def mutate(payload) -> None:
            tool11 = next(item for item in payload["tools"] if item["unit_id"] == "TOOL-11")
            tool11["output_member_fields"].remove("common_path_status")

        self._mutate_json(relative, mutate)
        self._assert_error("common_path_status_missing", stage="S1D-2")

    def test_s1d2_rejects_empty_path_direction_population(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]

        def mutate(payload) -> None:
            tool12 = next(item for item in payload["tools"] if item["unit_id"] == "TOOL-12")
            del tool12["output_field_schemas"]["peer_asn_direction_ids"]["minItems"]

        self._mutate_json(relative, mutate)
        self._assert_error("path_direction_population_empty", stage="S1D-2")

    def test_receipt_is_written_atomically_with_self_digest(self) -> None:
        receipt = HOOK.run_alignment(self.root, "S1D-0")
        output = HOOK.RECEIPT_ROOT / "S1D-0.json"
        target = HOOK.write_receipt(self.root, output, receipt)
        stored = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(stored["receipt_digest"], HOOK._canonical_digest(stored))
        leftovers = list(target.parent.glob(f".{target.name}.*.tmp"))
        self.assertEqual([], leftovers)


if __name__ == "__main__":
    unittest.main()
