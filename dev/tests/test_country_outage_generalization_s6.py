from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy/country-outage-general-page"
SCRIPTS = tuple(sorted(DEPLOY.glob("*.sh")))


def script(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


class CountryOutageGeneralizationS6Test(unittest.TestCase):
    def test_release_scripts_are_complete_executable_and_valid_bash(self) -> None:
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

    def test_prepare_binds_one_deployed_interactive_agent_and_fail_closed(self) -> None:
        text = script("prepare-runtime-release.sh")
        for phrase in (
            "domeye_country_outage_general_release_candidate_v2",
            "interactive_agent",
            "cutover_baseline",
            "country-outage-interactive-agent",
            "http://127.0.0.1:28476",
            "domeye_interactive_agent_release_probe_v1",
            "release_manifest_sha256",
            "active_state_sha256",
            "candidate_manifest_sha256",
            "readiness_identity_sha256",
            'mode:"fail_closed"',
            "previous_release_id:null",
            "candidate_canary_production_same_artifacts:true",
            "rebuild_allowed:false",
            "interactive_answer_attempt_limit",
            "audit_only",
            "monetary_limit_usd == null",
            "model_calls_during_prepare:0",
        ):
            self.assertIn(phrase, text)
        self.assertIn("source.archive_sha256", text)
        for frozen_path in (
            "deploy/country-outage-general-page/prepare-runtime-release.sh",
            "deploy/country-outage-general-page/manage-runtime.sh",
            "deploy/country-outage-general-page/activate-runtime.sh",
            "deploy/country-outage-general-page/verify-runtime.sh",
            "deploy/country-outage-general-page/rollback-runtime.sh",
            "deploy/country-outage-agent/p1-chat/manage.sh",
            "deploy/country-outage-agent/p1-chat/probe.mjs",
            "deploy/country-outage-agent/p1-chat/verify-release.mjs",
            "contracts/agent/domeye-first-vertical-slice/v1/candidate.json",
        ):
            self.assertIn(frozen_path, text)
        self.assertNotIn("protected_runtime.sidecar_", text)
        self.assertNotIn("rollback:{backend_release_id", text)
        self.assertNotIn("paid_model_calls", text)
        self.assertNotIn("rm -rf", text)

    def test_manager_replays_bound_interactive_agent_identity(self) -> None:
        text = script("manage-runtime.sh")
        for phrase in (
            "verify_interactive_agent_binding",
            "listener_output_matches_runtime",
            "assert_runtime_listener",
            "assert_runtime_port_closed",
            '127.0.0.1:${API_PORT}',
            'pid=${pid},',
            "ss -H -ltnp",
            "domeye_interactive_agent_release_probe_v1",
            "COUNTRY_OUTAGE_INTERACTIVE_AGENT_SIDECAR_URL",
            "http://127.0.0.1:28476",
            "country-outage-interactive-agent.env",
            "readiness_identity_sha256",
            "sha256sum -c core.sha256",
            "PRODUCTION-VERIFICATION.json",
            "ACTIVATION-STATE.json",
            "DEPLOYMENT.json",
            "requires_general_production_evidence:true",
            'cd -- "$1" && exec "$2" run.py',
        ):
            self.assertIn(phrase, text)
        for forbidden in (
            "COUNTRY_OUTAGE_P1_CHAT_SIDECAR_URL",
            "country-outage-p1-chat",
            "28475",
            "/rebind",
            "serve-formal-p1",
            "pkill",
            "killall",
            "rm -rf",
        ):
            self.assertNotIn(forbidden, text)
        start_body = text.split("start_runtime() {", 1)[1].split(
            "stop_runtime() {", 1
        )[0]
        stop_body = text.split("stop_runtime() {", 1)[1].split(
            "status_runtime() {", 1
        )[0]
        self.assertIn('assert_runtime_listener "${sessions[0]}"', start_body)
        self.assertIn("assert_runtime_port_closed", stop_body)
        completion_body = text.split("workflow_completion_state() {", 1)[1].split(
            "serve_runtime() {", 1
        )[0]
        self.assertIn(".unified_candidate.release_id", completion_body)
        self.assertNotIn('selected_release="$(release_id)"', completion_body)
        self.assertNotIn(
            'state:(if $interactive_agent.production_verified then "verified"',
            text,
        )
        for secret_argument in (
            "DB_PASSWORD=",
            "SECRET_KEY=",
            "COUNTRY_OUTAGE_AGENT_SHARED_TOKEN=",
        ):
            self.assertNotIn(secret_argument, start_body)

    def test_canary_requires_this_turn_direct_renderer_guard_and_oracle(self) -> None:
        text = script("verify-runtime.sh")
        for phrase in (
            "CANARY-VERIFICATION.json",
            "canary_verified",
            "/country-outage/chat",
            "conversation_id",
            "turn_id",
            "verify-release.mjs",
            "promotion",
            "response_sha256",
            "validation_receipt_body_base64",
            "sha256_hex_file",
            "answer_source",
            "renderer",
            "guard_decision",
            "pass",
        ):
            self.assertIn(phrase, text)
        for forbidden in (
            "deterministic_fallback",
            "clarification_required",
            "answer_not_accepted",
        ):
            # 成功证据不允许把这些失败分支作为可接受来源；若脚本需要扫描，
            # 必须只以拒绝条件出现，不能出现成功映射。
            self.assertNotIn(f'{forbidden}:true', text)
        self.assertIn(
            ".conversation.conversation_id == $conversation_id", text
        )
        self.assertIn(
            "select(.turn_id == $turn_id)", text
        )
        self.assertIn(
            ".result.fallback_or_rejection_present == false", text
        )

    def test_runtime_verifier_embedded_python_compiles_and_is_deterministic(self) -> None:
        text = script("verify-runtime.sh")
        embedded_python = text.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        compile(embedded_python, "verify-runtime.sh:<embedded-python>", "exec")
        for phrase in (
            "ThreadPoolExecutor",
            "repeat_order_concurrent_equal",
            "wrong_publication_http",
            "invalid_path_scope_http",
            "wrong_as_event_window_http",
            "event_window_selected_asn",
            'len(as_window["selected_asn"]["series"]) == 540',
            "max_response_bytes",
        ):
            self.assertIn(phrase, embedded_python)

    def test_production_completion_requires_verified_promotion(self) -> None:
        verify = script("verify-runtime.sh")
        activate = script("activate-runtime.sh")
        for phrase in (
            "promote",
            "production_verified",
            "production_verified == true",
        ):
            self.assertIn(phrase, verify)
        for phrase in (
            "CANARY-VERIFICATION.json",
            "canary_verified",
            "promotion-receipt",
            "PRODUCTION-VERIFICATION.json",
            "production_verified",
            "fail_closed",
            "canary_backend_is_closed",
            "production_backend_is_closed",
            "screen_session_is_absent",
        ):
            self.assertIn(phrase, activate)
        for forbidden in (
            "rollback_after_failure",
            "FRONTEND_ROLLBACK",
            'set_current_link "${PREVIOUS_BACKEND}"',
            "activation_failed_recovered",
        ):
            self.assertNotIn(forbidden, activate)
        self.assertLess(
            activate.index("if ! replay_canary_answer"),
            activate.index("mutation_started=true"),
        )
        self.assertLess(
            activate.index('"${BASELINE_MANAGER}" stop'),
            activate.index("if ! activate_backend_pointer"),
        )
        self.assertLess(
            activate.index('"${MANAGER}" stop'),
            activate.index("if ! canary_backend_is_closed"),
        )
        self.assertLess(
            activate.index('"${VERIFY}" production'),
            activate.index("if ! atomic_state production_verified"),
        )
        self.assertLess(
            activate.index("if ! atomic_state production_verified"),
            activate.index("if ! write_deployment"),
        )
        self.assertLess(
            activate.index("if ! write_deployment"),
            activate.index("activation_complete=true"),
        )

    def test_frontend_cutover_is_one_way_and_keeps_old_tree_quarantined(self) -> None:
        text = script("activate-runtime.sh")
        for phrase in (
            "renameat2",
            "FRONTEND_QUARANTINE_PATH",
            "create-only 非路由 quarantine",
            "verify_quarantine_not_routed",
            "automatic_restore:false",
        ):
            self.assertIn(phrase, text)
        self.assertLess(
            text.index("renameat2(-100, current, -100, candidate, 2)"),
            text.index('mv -T -- "${exchange}" "${FRONTEND_QUARANTINE_PATH}"'),
        )
        self.assertIn(
            '"$(readlink -f -- "${FRONTEND_QUARANTINE_PATH}")"', text
        )
        for forbidden in (
            "rollback-frontend-build.sh",
            "FRONTEND_ROLLBACK",
            "restore_frontend",
            "automatic_restore:true",
        ):
            self.assertNotIn(forbidden, text)

    def test_first_release_rollback_only_fails_closed(self) -> None:
        text = script("rollback-runtime.sh")
        for phrase in (
            "--check",
            "--execute",
            "check_rollback",
            "fail_closed",
            "failed_closed",
            "CONFIRM_RELEASE_ID",
        ):
            self.assertIn(phrase, text)
        for forbidden in (
            "FRONTEND_ROLLBACK",
            "PREVIOUS_BACKEND",
            "PREVIOUS_FRONTEND",
            "set_current_link",
            "rollback-frontend-build.sh",
        ):
            self.assertNotIn(forbidden, text)
        execute = text.split("--execute)", 1)[1]
        ordered = (
            "if ! prepare_fail_closed_evidence",
            "if ! write_failed_closed_deployment false ''",
            "if ! write_failed_closed_state fail_closing in_progress",
            "if ! publish_fail_closed_evidence",
            "if ! write_failed_closed_state failed_closed failed_closed",
            'if ! write_failed_closed_deployment true "${FAIL_CLOSED_SHA}"',
        )
        positions = [execute.index(item) for item in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(".production_verified = false", text)
        self.assertIn(".was_production_verified = true", text)

    def test_public_cutover_has_no_old_route_or_request_fallback(self) -> None:
        combined = "\n".join(path.read_text(encoding="utf-8") for path in SCRIPTS)
        for forbidden in (
            "serve-formal-p1",
            "COUNTRY_OUTAGE_P1_CHAT_SIDECAR_URL",
            "country-outage-p1-chat",
            "28475",
            "/rebind",
        ):
            self.assertNotIn(forbidden, combined)
        self.assertNotIn("fallback_route", combined)
        self.assertNotIn("route_selector", combined)


if __name__ == "__main__":
    unittest.main()
