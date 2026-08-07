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
HOOK_PATH = (
    REPOSITORY_ROOT
    / ".codex"
    / "hooks"
    / "country_outage_generalization_review.py"
)


def load_hook_module():
    specification = importlib.util.spec_from_file_location(
        "country_outage_generalization_review",
        HOOK_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 Hook：{HOOK_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageGeneralizationHookTest(unittest.TestCase):
    def run_hook(
        self,
        *arguments: str,
        input_text: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        effective_environment = os.environ.copy()
        effective_environment.pop("DOMEYE_COUNTRY_OUTAGE_STAGE", None)
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
        self.assertEqual(module.validate_stage_artifacts("S0"), [])

    def test_s0_stage_verifier_failure_blocks_hook(self) -> None:
        module = load_hook_module()
        with tempfile.TemporaryDirectory() as directory:
            failing = Path(directory) / "failing_s0.py"
            failing.write_text(
                "import json\n"
                "print(json.dumps({'status': 'fail', 'stage': 'S0'}))\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            module.STAGE_VERIFIER_PATHS = {"S0": failing}
            errors = module.validate_stage_artifacts("S0")
        self.assertTrue(
            any("S0 阶段 verifier 失败" in item for item in errors),
            errors,
        )

    def test_s1_stage_verifier_is_registered(self) -> None:
        module = load_hook_module()
        verifier = module.STAGE_VERIFIER_PATHS.get("S1")
        self.assertEqual(
            verifier,
            REPOSITORY_ROOT / "dev" / "verify_country_outage_generalization_s1.py",
        )

    def test_every_explicit_stage_emits_review_without_claiming_acceptance(
        self,
    ) -> None:
        for stage in (f"S{index}" for index in range(7)):
            with self.subTest(stage=stage):
                result = self.run_hook("--stage", stage)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"阶段结束回检：{stage}", result.stdout)
                self.assertIn(
                    "不代表数据、算法、制品、API、页面",
                    result.stdout,
                )
                self.assertIn(
                    f"通用观测页最终验收回检：{stage}",
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
            environment={"DOMEYE_COUNTRY_OUTAGE_STAGE": "S3"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload.get("decision"), "block")
        reason = payload.get("reason", "")
        self.assertIn("GFA-10、GFA-12", reason)
        self.assertIn("Hook 机检只覆盖", reason)

    def test_stop_hook_avoids_recursion(self) -> None:
        result = self.run_hook(
            input_text=json.dumps(
                {"hook_event_name": "Stop", "stop_hook_active": True}
            ),
            environment={"DOMEYE_COUNTRY_OUTAGE_STAGE": "S2"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})

    def test_peer_session_semantic_drift_is_rejected(self) -> None:
        module = load_hook_module()
        original = module.ACCEPTANCE_PATH.read_text(encoding="utf-8")
        changed = original.replace(
            "同一 peer ASN 的多个 BGP 会话只算一个方向",
            "同一 peer ASN 的多个 BGP 会话算作多个方向",
            1,
        )
        self.assertNotEqual(changed, original)
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / module.ACCEPTANCE_PATH.name
            candidate.write_text(changed, encoding="utf-8")
            module.ACCEPTANCE_PATH = candidate
            errors = module.validate_documents()
        self.assertTrue(
            any("同一 peer ASN 的多个 BGP 会话只算一个方向" in item for item in errors),
            errors,
        )

    def test_prefix_vp_frontend_drift_is_rejected(self) -> None:
        module = load_hook_module()
        original = module.ACCEPTANCE_PATH.read_text(encoding="utf-8")
        changed = original.replace(
            "前台不出现 `Prefix×VP`",
            "前台可以出现 `Prefix×VP`",
            1,
        )
        self.assertNotEqual(changed, original)
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / module.ACCEPTANCE_PATH.name
            candidate.write_text(changed, encoding="utf-8")
            module.ACCEPTANCE_PATH = candidate
            errors = module.validate_documents()
        self.assertTrue(
            any("前台不出现 `Prefix×VP`" in item for item in errors),
            errors,
        )

    def test_internal_artifact_copy_drift_is_rejected(self) -> None:
        checks = (
            (
                "普通页面不得展示 `PRODUCT`、`PUBLICATION`、`REVISION`、`DATA THROUGH`",
                "普通页面可以展示 `PRODUCT`、`PUBLICATION`、`REVISION`、`DATA THROUGH`",
            ),
            (
                "`incident_go_v1_*`、`trend_product_v1_*`、`observation_publication_v1_*`",
                "内部身份可以直接展示",
            ),
            (
                "冻结一致性是系统责任，不是需要向用户解释的页面内容",
                "冻结一致性需要在页面向用户解释",
            ),
        )
        for required, replacement in checks:
            with self.subTest(required=required):
                module = load_hook_module()
                original = module.ACCEPTANCE_PATH.read_text(encoding="utf-8")
                changed = original.replace(required, replacement)
                self.assertNotEqual(changed, original)
                with tempfile.TemporaryDirectory() as directory:
                    candidate = Path(directory) / module.ACCEPTANCE_PATH.name
                    candidate.write_text(changed, encoding="utf-8")
                    module.ACCEPTANCE_PATH = candidate
                    errors = module.validate_documents()
                self.assertTrue(
                    any(required in item for item in errors),
                    errors,
                )

    def test_plan_cannot_add_implementation_steps(self) -> None:
        module = load_hook_module()
        original = module.PLAN_PATH.read_text(encoding="utf-8")
        changed = original.replace(
            "#### 入口",
            "#### 实施步骤\n\n- 写具体代码。\n\n#### 入口",
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / module.PLAN_PATH.name
            candidate.write_text(changed, encoding="utf-8")
            module.PLAN_PATH = candidate
            errors = module.validate_documents()
        self.assertTrue(
            any("分阶段计划越过头尾与边界" in item for item in errors),
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
