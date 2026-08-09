from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROGRAM_HOOK_PATH = (
    REPOSITORY_ROOT / ".codex" / "hooks" / "country_outage_agent_program_review.py"
)
P1_HOOK_PATH = (
    REPOSITORY_ROOT / ".codex" / "hooks" / "country_outage_agent_p1_alignment.py"
)


def load_program_hook():
    specification = importlib.util.spec_from_file_location(
        "country_outage_agent_program_review_for_p1",
        PROGRAM_HOOK_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 Hook：{PROGRAM_HOOK_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageAgentP1AlignmentTest(unittest.TestCase):
    def run_hook(self, stage: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(P1_HOOK_PATH), "--stage", stage],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_p1_config_documents_and_task_boundary_are_valid(self) -> None:
        module = load_program_hook()
        config = module.load_project_config("P1")
        self.assertEqual(module.validate_config(config, expected_project="P1"), [])
        self.assertEqual(module.validate_documents(config), [])
        self.assertEqual(module.validate_task_boundary(), [])

    def test_all_p1_stages_emit_semantic_review_without_claiming_acceptance(self) -> None:
        for stage in (f"S{index}" for index in range(5)):
            with self.subTest(stage=stage):
                result = self.run_hook(stage)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"阶段结束回检：{stage}", result.stdout)
                self.assertIn("开放 UserGoalPlan", result.stdout)
                self.assertIn("独立专家角色", result.stdout)
                self.assertIn("不代表验收案例", result.stdout)
                self.assertIn(
                    f"国家中断 Agent P1 最终验收回检：{stage}",
                    result.stdout,
                )

    def test_requirement_or_flexibility_drift_is_rejected(self) -> None:
        module = load_program_hook()
        config = module.load_project_config("P1")
        acceptance_path = module.safe_repository_path(
            config["acceptance_path"], "acceptance_path"
        )
        plan_path = module.safe_repository_path(config["plan_path"], "plan_path")
        acceptance = module.read_text(acceptance_path)
        plan = module.read_text(plan_path)

        errors = module.validate_document_texts(
            config,
            acceptance.replace("### P1-CTR-12：", "### P1-CTR-99：", 1),
            plan,
        )
        self.assertTrue(any("P1-CTR-01 至 P1-CTR-16" in error for error in errors))

        errors = module.validate_document_texts(
            config,
            acceptance,
            plan.replace("本计划不是固定任务清单", "本计划是固定任务清单", 1),
        )
        self.assertTrue(any("本计划不是固定任务清单" in error for error in errors))

    def test_plan_cannot_drop_a_due_requirement(self) -> None:
        module = load_program_hook()
        config = module.load_project_config("P1")
        acceptance_path = module.safe_repository_path(
            config["acceptance_path"], "acceptance_path"
        )
        plan_path = module.safe_repository_path(config["plan_path"], "plan_path")
        acceptance = module.read_text(acceptance_path)
        plan = module.read_text(plan_path)
        changed = plan.replace("`P1-SCE-06`、`P1-SCE-08`、`P1-SCE-11`", "`P1-SCE-06`", 1)
        errors = module.validate_document_texts(config, acceptance, changed)
        self.assertTrue(any("P1-SCE-08" in error for error in errors))
        self.assertTrue(any("P1-SCE-11" in error for error in errors))

    def test_s1_vertical_slice_does_not_claim_all_single_turn_scenarios(self) -> None:
        module = load_program_hook()
        config = module.load_project_config("P1")
        self.assertEqual(
            config["stage_due"]["S1"]["联合场景"],
            ["P1-SCE-01"],
        )
        plan_path = module.safe_repository_path(config["plan_path"], "plan_path")
        s1_body = module.stage_body(config, module.read_text(plan_path), "S1")
        self.assertIsNotNone(s1_body)
        self.assertIn("不因 S1 的一条垂直切片提前宣告通过", s1_body)

    def test_goal_fidelity_and_grounding_safety_use_separate_gates(self) -> None:
        module = load_program_hook()
        config = module.load_project_config("P1")
        acceptance_path = module.safe_repository_path(
            config["acceptance_path"], "acceptance_path"
        )
        acceptance = module.read_text(acceptance_path)
        self.assertIn("`UserGoalPlan` 目标保真率不低于 95%", acceptance)
        self.assertIn("`GroundingPlan` 合法性是 100% 硬门", acceptance)
        self.assertIn("任何非法节点到达执行器", acceptance)
        self.assertNotIn("Semantic Plan 正确率", acceptance)

    def test_invalid_stage_is_rejected(self) -> None:
        result = self.run_hook("S5")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)


if __name__ == "__main__":
    unittest.main()
