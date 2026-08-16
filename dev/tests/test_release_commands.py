from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
PREPARE = (ROOT / "deploy" / "release" / "prepare.sh").read_text(encoding="utf-8")
ACTIVATE = (ROOT / "deploy" / "release" / "activate.sh").read_text(encoding="utf-8")
ROLLBACK = (ROOT / "deploy" / "release" / "rollback.sh").read_text(encoding="utf-8")
GC = (ROOT / "deploy" / "release" / "gc.sh").read_text(encoding="utf-8")
FULL_ACCEPTANCE = (ROOT / "deploy" / "acceptance" / "full-acceptance.sh").read_text(
    encoding="utf-8"
)


class ReleaseCommandBoundaryTest(unittest.TestCase):
    def test_check_command_cannot_reach_full_acceptance(self):
        check_target = MAKEFILE.split("check-release-full:", 1)[1].split(
            "release-prepare:", 1
        )[0]
        self.assertNotIn("full-acceptance.sh", check_target)
        self.assertIn("不允许恢复数据库或切换生产服务", check_target)

    def test_prepare_has_resumable_gates_but_no_production_activation(self):
        for stage in (
            "inputs_verified",
            "database_verified",
            "code_verified",
            "frontend_built",
            "prepared",
        ):
            self.assertIn(stage, PREPARE)
        self.assertIn("restore-database.sh", PREPARE)
        self.assertIn("candidate-stack.sh", PREPARE)
        self.assertNotIn("activate-database.sh", PREPARE)
        self.assertNotIn("install-info-artifact.sh", PREPARE)

    def test_activate_requires_confirmed_state_lock_and_nonce(self):
        confirm_gate = ACTIVATE.index('"${CONFIRM_RELEASE_ID:-}" != "${RELEASE_ID}"')
        state_gate = ACTIVATE.index("候选准备状态与当前发布输入不一致")
        mutation = ACTIVATE.index("full-acceptance.sh")
        self.assertLess(confirm_gate, mutation)
        self.assertLess(state_gate, mutation)
        self.assertIn("domeye_release_acquire_lock activate", ACTIVATE)
        self.assertIn("DOMEYE_CORE_RELEASE_ACTIVATION_NONCE", ACTIVATE)

    def test_full_acceptance_rejects_direct_invocation_before_stateful_steps(self):
        direct_gate = FULL_ACCEPTANCE.index("DOMEYE_CORE_RELEASE_ACTIVATION")
        state_gate = FULL_ACCEPTANCE.index('.activation.nonce == $nonce')
        first_restore = FULL_ACCEPTANCE.index('"${DEPLOY_DIR}/database/restore-database.sh"')
        source_marker = FULL_ACCEPTANCE.index("domeye_core_write_source_rollback_state")
        self.assertLess(direct_gate, state_gate)
        self.assertLess(state_gate, first_restore)
        self.assertLess(state_gate, source_marker)
        self.assertNotIn("npm run build", FULL_ACCEPTANCE)
        self.assertNotIn("uv sync", FULL_ACCEPTANCE)

    def test_rollback_validates_all_component_journals_before_stopping(self):
        journal_gate = ROLLBACK.index("组件回滚日志不可用或版本不一致")
        first_stop = ROLLBACK.index('"${DEPLOY_DIR}/stop-backend.sh"')
        self.assertLess(journal_gate, first_stop)
        self.assertIn("NGINX_INSTALLED_SHA", ROLLBACK)
        self.assertIn("CONFIRM_RELEASE_ID", ROLLBACK)

    def test_gc_is_dry_run_and_fail_closed_by_default(self):
        self.assertIn("execute_gc=false", GC)
        self.assertIn("CONFIRM_RELEASE_ID", GC)
        self.assertIn("active-or-rollback-reference", GC)
        self.assertIn("mounted-or-container-used", GC)
        self.assertIn("retention-window", GC)
        self.assertIn("以上仅为 dry-run；未删除任何目录", GC)


if __name__ == "__main__":
    unittest.main()
