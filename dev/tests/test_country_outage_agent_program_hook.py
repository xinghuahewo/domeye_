from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = (
    REPOSITORY_ROOT
    / ".codex"
    / "hooks"
    / "country_outage_agent_program_review.py"
)


def load_hook_module():
    specification = importlib.util.spec_from_file_location(
        "country_outage_agent_program_review",
        HOOK_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 Hook：{HOOK_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageAgentProgramHookTest(unittest.TestCase):
    def run_hook(
        self,
        *arguments: str,
        input_text: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        effective_environment = os.environ.copy()
        if environment:
            effective_environment.update(environment)
        return subprocess.run(
            [sys.executable, str(HOOK_PATH), *arguments],
            cwd=REPOSITORY_ROOT,
            input=input_text,
            capture_output=True,
            text=True,
            env=effective_environment,
            timeout=20,
            check=False,
        )

    def test_p0_config_documents_and_task_boundary_are_valid(self) -> None:
        module = load_hook_module()
        config = module.load_project_config("P0")
        self.assertEqual(module.validate_config(config, expected_project="P0"), [])
        self.assertEqual(module.validate_documents(config), [])
        self.assertEqual(module.validate_task_boundary(), [])

    def test_p1_config_documents_and_task_boundary_are_valid(self) -> None:
        module = load_hook_module()
        config = module.load_project_config("P1")
        self.assertEqual(module.validate_config(config, expected_project="P1"), [])
        self.assertEqual(module.validate_documents(config), [])
        self.assertEqual(module.validate_task_boundary(), [])

    def test_p1_1_config_documents_and_task_boundary_are_valid(self) -> None:
        module = load_hook_module()
        config = module.load_project_config("P1.1")
        self.assertEqual(module.validate_config(config, expected_project="P1.1"), [])
        self.assertEqual(module.validate_documents(config), [])
        self.assertEqual(module.validate_task_boundary(), [])

    def test_document_validation_detects_boundary_and_numbering_drift(self) -> None:
        module = load_hook_module()
        config = module.load_project_config("P0")
        acceptance_path = module.safe_repository_path(
            config["acceptance_path"], "acceptance_path"
        )
        plan_path = module.safe_repository_path(config["plan_path"], "plan_path")
        acceptance = module.read_text(acceptance_path)
        plan = module.read_text(plan_path)

        errors = module.validate_document_texts(
            config,
            acceptance.replace("第一版固定为 35 个验收案例", "若干案例"),
            plan,
        )
        self.assertTrue(
            any("第一版固定为 35 个验收案例" in error for error in errors)
        )

        errors = module.validate_document_texts(
            config,
            acceptance.replace("### P0-BE-09：", "### P0-BE-10：", 1),
            plan,
        )
        self.assertTrue(any("P0-BE-01 至 P0-BE-09" in error for error in errors))

    def test_p1_document_validation_detects_boundary_and_numbering_drift(self) -> None:
        module = load_hook_module()
        config = module.load_project_config("P1")
        acceptance_path = module.safe_repository_path(
            config["acceptance_path"], "acceptance_path"
        )
        plan_path = module.safe_repository_path(config["plan_path"], "plan_path")
        acceptance = module.read_text(acceptance_path)
        plan = module.read_text(plan_path)

        errors = module.validate_document_texts(
            config,
            acceptance.replace(
                "RRC25 事件绑定聊天问答",
                "开放式通用聊天机器人",
            ),
            plan,
        )
        self.assertTrue(
            any("RRC25 事件绑定聊天问答" in error for error in errors)
        )

        errors = module.validate_document_texts(
            config,
            acceptance.replace("### P1-BE-12：", "### P1-BE-13：", 1),
            plan,
        )
        self.assertTrue(any("P1-BE-01 至 P1-BE-12" in error for error in errors))

    def test_p1_1_document_validation_detects_boundary_and_numbering_drift(self) -> None:
        module = load_hook_module()
        config = module.load_project_config("P1.1")
        acceptance_path = module.safe_repository_path(
            config["acceptance_path"], "acceptance_path"
        )
        plan_path = module.safe_repository_path(config["plan_path"], "plan_path")
        acceptance = module.read_text(acceptance_path)
        plan = module.read_text(plan_path)

        errors = module.validate_document_texts(
            config,
            acceptance.replace(
                "模型输出是不可信计划提案",
                "模型输出可以直接执行",
            ),
            plan,
        )
        self.assertTrue(
            any("模型输出是不可信计划提案" in error for error in errors)
        )

        errors = module.validate_document_texts(
            config,
            acceptance.replace(
                "用户目标和请求含义保持开放",
                "用户目标只能来自已有 capability",
                1,
            ),
            plan,
        )
        self.assertTrue(
            any("用户目标和请求含义保持开放" in error for error in errors)
        )

        errors = module.validate_document_texts(
            config,
            acceptance.replace("P0 v1.3 Capability Discovery Ledger", "旧 P0 案例"),
            plan,
        )
        self.assertTrue(
            any("P0 v1.3 Capability Discovery Ledger" in error for error in errors)
        )

        errors = module.validate_document_texts(
            config,
            acceptance,
            plan.replace("Shadow 无任何算子执行权", "Shadow 可执行算子", 1),
        )
        self.assertTrue(any("Shadow 无任何算子执行权" in error for error in errors))

        errors = module.validate_document_texts(
            config,
            acceptance.replace(
                "### P1.1-GATE-14：",
                "### P1.1-GATE-15：",
                1,
            ),
            plan,
        )
        self.assertTrue(
            any("P1.1-GATE-01 至 P1.1-GATE-14" in error for error in errors)
        )

    def test_every_p0_stage_emits_review_without_claiming_effect_acceptance(self) -> None:
        for stage in (f"S{index}" for index in range(4)):
            with self.subTest(stage=stage):
                result = self.run_hook("--project", "P0", "--stage", stage)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"阶段结束回检：{stage}", result.stdout)
                self.assertIn("本阶段到期或必须继续保持可达的要求", result.stdout)
                self.assertIn("不代表验收案例、参考真值、基线执行、评测", result.stdout)
                self.assertIn(
                    f"国家中断 Agent P0 最终验收回检：{stage}",
                    result.stdout,
                )

    def test_every_p1_stage_emits_review_without_claiming_effect_acceptance(self) -> None:
        for stage in (f"S{index}" for index in range(4)):
            with self.subTest(stage=stage):
                result = self.run_hook("--project", "P1", "--stage", stage)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"阶段结束回检：{stage}", result.stdout)
                self.assertIn("本阶段到期或必须继续保持可达的要求", result.stdout)
                self.assertIn("不代表验收案例、参考真值、基线执行、评测", result.stdout)
                self.assertIn(
                    f"国家中断 Agent P1 最终验收回检：{stage}",
                    result.stdout,
                )

    def test_every_p1_1_stage_emits_review_without_claiming_effect_acceptance(self) -> None:
        for stage in (f"S{index}" for index in range(5)):
            with self.subTest(stage=stage):
                result = self.run_hook("--project", "P1.1", "--stage", stage)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"阶段结束回检：{stage}", result.stdout)
                self.assertIn("本阶段到期或必须继续保持可达的要求", result.stdout)
                self.assertIn("不代表验收案例、参考真值、基线执行、评测", result.stdout)
                self.assertIn(
                    f"国家中断 Agent P1.1 最终验收回检：{stage}",
                    result.stdout,
                )

    def test_unknown_project_and_stage_are_rejected(self) -> None:
        unknown_project = self.run_hook("--project", "P6", "--stage", "S0")
        self.assertEqual(unknown_project.returncode, 1)
        self.assertIn("工程编号必须为 P0 至 P5 或已登记衍生工程 P1.1", unknown_project.stderr)

        unknown_stage = self.run_hook("--project", "P0", "--stage", "S4")
        self.assertEqual(unknown_stage.returncode, 2)
        self.assertIn("P0 不支持阶段 S4", unknown_stage.stderr)

        incomplete = self.run_hook("--project", "P0")
        self.assertEqual(incomplete.returncode, 2)
        self.assertIn("必须同时提供", incomplete.stderr)

    def test_stop_hook_ignores_unrelated_work(self) -> None:
        result = self.run_hook(
            input_text=json.dumps({"hook_event_name": "Stop"}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})

    def test_stop_hook_blocks_partial_declaration(self) -> None:
        result = self.run_hook(
            input_text=json.dumps({"hook_event_name": "Stop"}),
            environment={"DOMEYE_COUNTRY_OUTAGE_AGENT_PROJECT": "P0"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("decision"), "block")
        self.assertIn("请同时设置", payload.get("reason", ""))

    def test_stop_hook_requests_review_for_declared_stage(self) -> None:
        result = self.run_hook(
            input_text=json.dumps({"hook_event_name": "Stop"}),
            environment={
                "DOMEYE_COUNTRY_OUTAGE_AGENT_PROJECT": "P0",
                "DOMEYE_COUNTRY_OUTAGE_AGENT_STAGE": "S2",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("decision"), "block")
        reason = payload.get("reason", "")
        self.assertIn("P0-FE-03", reason)
        self.assertIn(
            "P0-BE-03、P0-BE-04、P0-BE-05、P0-BE-06、P0-BE-07",
            reason,
        )
        self.assertIn("Hook 机检只覆盖", reason)

    def test_stop_hook_requests_p1_review_for_declared_stage(self) -> None:
        result = self.run_hook(
            input_text=json.dumps({"hook_event_name": "Stop"}),
            environment={
                "DOMEYE_COUNTRY_OUTAGE_AGENT_PROJECT": "P1",
                "DOMEYE_COUNTRY_OUTAGE_AGENT_STAGE": "S2",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("decision"), "block")
        reason = payload.get("reason", "")
        self.assertIn("P1-FE-05、P1-FE-06、P1-FE-07", reason)
        self.assertIn("P1-BE-02、P1-BE-03、P1-BE-07、P1-BE-08", reason)
        self.assertIn("P1-SCE-05、P1-SCE-06、P1-SCE-07、P1-SCE-10", reason)
        self.assertIn("Hook 机检只覆盖", reason)

    def test_stop_hook_requests_p1_1_review_for_declared_stage(self) -> None:
        result = self.run_hook(
            input_text=json.dumps({"hook_event_name": "Stop"}),
            environment={
                "DOMEYE_COUNTRY_OUTAGE_AGENT_PROJECT": "P1.1",
                "DOMEYE_COUNTRY_OUTAGE_AGENT_STAGE": "S3",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("decision"), "block")
        reason = payload.get("reason", "")
        self.assertIn(
            "P1.1-EFF-03、P1.1-EFF-09、P1.1-EFF-10、P1.1-EFF-11、P1.1-EFF-14、P1.1-EFF-15",
            reason,
        )
        self.assertIn(
            "P1.1-GATE-04、P1.1-GATE-06、P1.1-GATE-07、P1.1-GATE-10、P1.1-GATE-12",
            reason,
        )
        self.assertIn(
            "P1.1-SCE-04、P1.1-SCE-05、P1.1-SCE-09、P1.1-SCE-10",
            reason,
        )
        self.assertIn("Hook 机检只覆盖", reason)

    def test_stop_hook_avoids_recursion(self) -> None:
        result = self.run_hook(
            input_text=json.dumps(
                {"hook_event_name": "Stop", "stop_hook_active": True}
            ),
            environment={
                "DOMEYE_COUNTRY_OUTAGE_AGENT_PROJECT": "P0",
                "DOMEYE_COUNTRY_OUTAGE_AGENT_STAGE": "S3",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})

    def test_hook_is_registered(self) -> None:
        hooks = json.loads(
            (REPOSITORY_ROOT / ".codex" / "hooks.json").read_text(
                encoding="utf-8"
            )
        )
        commands = [
            hook.get("command", "")
            for group in hooks["hooks"]["Stop"]
            for hook in group.get("hooks", [])
        ]
        self.assertTrue(
            any(HOOK_PATH.name in command for command in commands),
            commands,
        )


if __name__ == "__main__":
    unittest.main()
