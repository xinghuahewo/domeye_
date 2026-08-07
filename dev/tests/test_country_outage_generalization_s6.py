from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy/country-outage-general-page"
SCRIPTS = tuple(sorted(DEPLOY.glob("*.sh")))
VERIFIER = ROOT / "dev/verify_country_outage_generalization_s6.py"


class CountryOutageGeneralizationS6Test(unittest.TestCase):
    def test_release_scripts_are_complete_and_valid_bash(self) -> None:
        self.assertEqual(
            [path.name for path in SCRIPTS],
            [
                "activate-runtime.sh",
                "manage-runtime.sh",
                "prepare-runtime-release.sh",
                "rollback-runtime.sh",
                "verify-runtime.sh",
            ],
        )
        for path in SCRIPTS:
            with self.subTest(path=path.name):
                result = subprocess.run(
                    ["bash", "-n", str(path)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(path.stat().st_mode & 0o100)

    def test_manager_uses_exact_runtime_and_process_identity(self) -> None:
        text = (DEPLOY / "manage-runtime.sh").read_text(encoding="utf-8")
        for phrase in (
            "/home/bgpdata/Domeye-Core-runtime/releases/*-backend",
            "session_process",
            "DOMEYE_P0_PRODUCTION_RELEASE_ID",
            "DOMEYE_COUNTRY_OUTAGE_GENERAL_RUNTIME_MODE",
            "DOMEYE_COUNTRY_OUTAGE_GENERAL_READ_MODEL",
            "sha256sum -c core.sha256",
        ):
            self.assertIn(phrase, text)
        for forbidden in ("pkill", "killall", "rm -rf"):
            self.assertNotIn(forbidden, text)

    def test_prepare_is_create_only_and_reuses_frozen_data(self) -> None:
        text = (DEPLOY / "prepare-runtime-release.sh").read_text(encoding="utf-8")
        for phrase in (
            "create-only 拒绝覆盖",
            "${PREVIOUS_BACKEND}/data-layer",
            "${GENERAL_READ_MODEL}",
            "database_changed:false",
            "nginx_changed:false",
            "sidecar_changed:false",
            "paid_model_calls:0",
            "candidate_canary_production_same_artifacts:true",
            "rebuild_allowed:false",
        ):
            self.assertIn(phrase, text)
        self.assertNotIn("rm -rf", text)

    def test_activation_has_automatic_recovery_and_explicit_confirmation(self) -> None:
        text = (DEPLOY / "activate-runtime.sh").read_text(encoding="utf-8")
        for phrase in (
            "CONFIRM_RELEASE_ID",
            "rollback_after_failure",
            "trap cleanup EXIT",
            "set_current_link \"${PREVIOUS_BACKEND}\"",
            "artifacts_rebuilt_during_promotion:false",
            "database_changed:false",
            "nginx_changed:false",
            "sidecar_changed:false",
        ):
            self.assertIn(phrase, text)

    def test_rollback_has_read_only_check_before_execute(self) -> None:
        text = (DEPLOY / "rollback-runtime.sh").read_text(encoding="utf-8")
        self.assertIn("--check", text)
        self.assertIn("--execute", text)
        self.assertIn("check_rollback", text)
        self.assertIn("CONFIRM_RELEASE_ID", text)
        self.assertNotIn("rm -rf", text)

    def test_runtime_verification_covers_repeat_concurrency_and_failure(self) -> None:
        shell = (DEPLOY / "verify-runtime.sh").read_text(encoding="utf-8")
        python = VERIFIER.read_text(encoding="utf-8")
        for phrase in (
            "ThreadPoolExecutor",
            "repeat_order_concurrent_equal",
            "wrong_publication_http",
            "invalid_path_scope_http",
            "max_response_bytes",
        ):
            self.assertIn(phrase, shell)
        self.assertIn("GFA_IDS", python)
        self.assertIn("run_stage_verifiers", python)
        self.assertIn("run_remote_probe", python)
        self.assertIn("rollback", python)


if __name__ == "__main__":
    unittest.main()
