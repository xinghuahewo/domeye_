from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = REPOSITORY_ROOT / ".codex" / "hooks" / "data_layer_224_310_review.py"


def load_hook_module():
    specification = importlib.util.spec_from_file_location(
        "data_layer_224_310_review",
        HOOK_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 Hook：{HOOK_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class DataLayer224310HookTest(unittest.TestCase):
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

    def test_every_stage_emits_review_without_claiming_business_acceptance(self) -> None:
        for stage in (f"S{index}" for index in range(7)):
            with self.subTest(stage=stage):
                result = self.run_hook("--stage", stage)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"阶段结束回检：{stage}", result.stdout)
                self.assertIn("不代表数据摄取、重放、指标、迁移", result.stdout)
                self.assertIn(
                    f"Domeye 数据层 224-310 最终验收回检：{stage}",
                    result.stdout,
                )

    def test_invalid_stage_is_rejected(self) -> None:
        result = self.run_hook("--stage", "S7")
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
            environment={"DOMEYE_DATA_LAYER_224_310_STAGE": "S3"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("decision"), "block")
        reason = payload.get("reason", "")
        self.assertIn("DLAE-05、DLAE-06、DLAE-07", reason)
        self.assertIn("Hook 机检只覆盖", reason)

    def test_stop_hook_avoids_recursion(self) -> None:
        result = self.run_hook(
            input_text=json.dumps(
                {"hook_event_name": "Stop", "stop_hook_active": True}
            ),
            environment={"DOMEYE_DATA_LAYER_224_310_STAGE": "S2"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})

    def test_fixed_window_drift_is_rejected(self) -> None:
        module = load_hook_module()
        acceptance = module.ACCEPTANCE_PATH.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary_directory:
            drifted_path = Path(temporary_directory) / "acceptance.md"
            drifted_path.write_text(
                acceptance.replace("4,320", "4,321"),
                encoding="utf-8",
            )
            module.ACCEPTANCE_PATH = drifted_path
            errors = module.validate_documents()
        self.assertTrue(
            any("4,320" in error for error in errors),
            errors,
        )

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
