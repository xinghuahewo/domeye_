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
    / "country_outage_investigation_agent_review.py"
)


def load_hook_module():
    specification = importlib.util.spec_from_file_location(
        "country_outage_investigation_agent_review",
        HOOK_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 Hook：{HOOK_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageInvestigationAgentHookTest(unittest.TestCase):
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

    def test_contract_documents_and_task_boundary_are_valid(self) -> None:
        module = load_hook_module()
        self.assertEqual(module.validate_documents(), [])
        self.assertEqual(module.validate_task_boundary(), [])

    def test_document_validation_detects_boundary_or_numbering_drift(self) -> None:
        module = load_hook_module()
        acceptance = module.read_text(module.ACCEPTANCE_PATH)
        plan = module.read_text(module.PLAN_PATH)

        errors = module.validate_document_texts(
            acceptance.replace("从始至终只使用 RRC25", "使用观测数据", 1),
            plan,
        )
        self.assertTrue(any("从始至终只使用 RRC25" in error for error in errors))

        errors = module.validate_document_texts(
            acceptance.replace("### IBE-13：", "### IBE-14：", 1),
            plan,
        )
        self.assertTrue(any("IBE-01 至 IBE-13" in error for error in errors))

    def test_every_explicit_stage_emits_review_without_claiming_acceptance(self) -> None:
        for stage in (f"I{index}" for index in range(6)):
            with self.subTest(stage=stage):
                result = self.run_hook("--stage", stage)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"阶段结束回检：{stage}", result.stdout)
                self.assertIn("本阶段到期或必须继续保持可达的要求", result.stdout)
                self.assertIn("不代表前端、后端、数据、模型", result.stdout)
                self.assertIn(
                    f"国家中断调查 Agent 最终验收回检：{stage}",
                    result.stdout,
                )

    def test_invalid_stage_is_rejected(self) -> None:
        result = self.run_hook("--stage", "I6")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_stop_hook_ignores_unrelated_work(self) -> None:
        result = self.run_hook(
            input_text=json.dumps({"hook_event_name": "Stop"}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})

    def test_stop_hook_requests_review_for_declared_stage(self) -> None:
        result = self.run_hook(
            input_text=json.dumps({"hook_event_name": "Stop"}),
            environment={"DOMEYE_COUNTRY_OUTAGE_INVESTIGATION_STAGE": "I3"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("decision"), "block")
        reason = payload.get("reason", "")
        self.assertIn("IFE-02、IFE-04、IFE-05", reason)
        self.assertIn("IBE-05、IBE-06、IBE-08、IBE-12、IBE-13", reason)
        self.assertIn("Hook 机检只覆盖", reason)

    def test_stop_hook_avoids_recursion(self) -> None:
        result = self.run_hook(
            input_text=json.dumps(
                {"hook_event_name": "Stop", "stop_hook_active": True}
            ),
            environment={"DOMEYE_COUNTRY_OUTAGE_INVESTIGATION_STAGE": "I4"},
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
