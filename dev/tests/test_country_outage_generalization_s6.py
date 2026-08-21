from __future__ import annotations

import base64
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy/country-outage-general-page"
NGINX_CONFIG = ROOT / "deploy/nginx/domeye-core.conf"
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

    def test_runtime_verification_requires_unique_official_ssh_origin(self) -> None:
        text = script("verify-runtime.sh")
        for phrase in (
            "/usr/bin/env -i HOME=",
            "PATH=/usr/bin:/bin",
            "/usr/bin/git --no-replace-objects",
            "GIT_SSH_COMMAND='/usr/bin/ssh ",
            "GIT_CONFIG_NOSYSTEM=1",
            "GIT_NO_REPLACE_OBJECTS=1",
            "GIT_OPTIONAL_LOCKS=0",
            "GIT_TERMINAL_PROMPT=0",
            "StrictHostKeyChecking=yes",
            "remote.origin.url",
            "remote.origin.pushurl",
            "remote get-url --all origin",
            "remote get-url --push --all origin",
            "trusted_raw_origin_count",
            "trusted_raw_push_count",
            "trusted_origin_count",
            "trusted_push_count",
            "git@github.com:xinghuahewo/domeye_.git",
            "唯一且不可改写的官方 GitHub SSH remote",
        ):
            self.assertIn(phrase, text)
        self.assertNotIn(
            "https://github.com/xinghuahewo/domeye_.git",
            text,
        )

    def test_prepare_binds_one_deployed_interactive_agent_v2_and_fail_closed(self) -> None:
        text = script("prepare-runtime-release.sh")
        for phrase in (
            "domeye_country_outage_general_release_candidate_v2",
            "interactive_agent",
            "cutover_baseline",
            "country-outage-interactive-agent",
            "http://127.0.0.1:28476",
            "domeye_interactive_agent_release_manifest_v2",
            "domeye_interactive_agent_release_probe_v2",
            "domeye_first_slice_candidate_manifest_v2",
            "domeye_first_slice_acceptance_record_v2",
            "<approved-candidate-id>",
            "<approved-acceptance-record-id>",
            "APPROVED_CANDIDATE_ID",
            "APPROVED_ACCEPTANCE_RECORD_ID",
            "release_manifest_sha256",
            "release_manifest_schema_version",
            "active_state_sha256",
            "candidate_manifest_sha256",
            "acceptance_record_id",
            "acceptance_record_sha256",
            "acceptance_replay_receipt_sha256",
            "readiness_schema_version",
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
            "contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json",
        ):
            self.assertIn(frozen_path, text)
        self.assertNotIn("protected_runtime.sidecar_", text)
        self.assertNotIn("rollback:{backend_release_id", text)
        self.assertNotIn("paid_model_calls", text)
        self.assertNotIn("rm -rf", text)

    def test_prepare_records_frontend_test_command_without_stale_count(self) -> None:
        text = script("prepare-runtime-release.sh")
        self.assertEqual(
            text.count('tests:{status:"passed",command:"npm test -- --run"'),
            2,
        )
        self.assertNotRegex(text, r"tests\s*:\s*\d+")
        self.assertNotRegex(
            text,
            r"tests\s*:\s*\{\s*frontend\s*:\s*\d+",
        )

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
            "domeye_interactive_agent_release_manifest_v2",
            "domeye_interactive_agent_release_probe_v2",
            "domeye_first_slice_acceptance_record_v2",
            "acceptance_record_id",
            "acceptance_record_sha256",
            "acceptance_replay_receipt_sha256",
            "COUNTRY_OUTAGE_INTERACTIVE_AGENT_SIDECAR_URL",
            "DOMEYE_COUNTRY_OUTAGE_INTERACTIVE_AGENT_CONFIG_SHA256",
            "http://127.0.0.1:28476",
            "country-outage-interactive-agent.env",
            "readiness_identity_sha256",
            "sha256sum -c core.sha256",
            "PRODUCTION-VERIFICATION.json",
            "CANARY-VERIFICATION.json",
            "ACTIVATION-STATE.json",
            "DEPLOYMENT.json",
            "public_internal_projection_equal",
            "requires_general_production_evidence:true",
            'cd -- "$1" && exec "$2" run.py',
        ):
            self.assertIn(phrase, text)
        for forbidden in (
            "COUNTRY_OUTAGE_P1_CHAT_SIDECAR_URL",
            "country-outage-p1-chat",
            "country-outage-agent.env",
            "DOMEYE_COUNTRY_OUTAGE_AGENT_CONFIG_SHA256",
            "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE",
            "COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID",
            "28474",
            "28475",
            "/rebind",
            "serve-formal-p1",
            "pkill",
            "killall",
            "rm -rf",
            "domeye_interactive_agent_release_manifest_v1",
            "domeye_interactive_agent_release_probe_v1",
            "first-vertical-slice/v1/candidate.json",
        ):
            self.assertNotIn(forbidden, text)
        self.assertEqual(
            text.count("curl --disable --noproxy '*' --proto '=http' --max-redirs 0"),
            2,
        )
        start_body = text.split("start_runtime() {", 1)[1].split(
            "stop_runtime() {", 1
        )[0]
        stop_body = text.split("stop_runtime() {", 1)[1].split(
            "status_runtime() {", 1
        )[0]
        self.assertIn('assert_runtime_listener "${sessions[0]}"', start_body)
        self.assertIn("readonly STARTUP_TIMEOUT_SECONDS=120", text)
        self.assertIn(
            "deadline=$(( SECONDS + STARTUP_TIMEOUT_SECONDS ))", start_body
        )
        self.assertIn("while (( SECONDS < deadline )); do", start_body)
        self.assertIn(
            'error "启动前闭包校验或运行时进程在 ${STARTUP_TIMEOUT_SECONDS} 秒内未就绪：${selected_release}"',
            start_body,
        )
        self.assertIn("Backend Screen 在就绪前退出", start_body)
        self.assertIn(
            'assert_runtime_listener "${sessions[0]}" >/dev/null 2>&1',
            start_body,
        )
        self.assertNotIn("attempt <= 60", start_body)
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
            "probe.mjs",
            "internal-record",
            "promotion",
            "domeye_interactive_agent_promotion_v2",
            "public_response",
            "internal_record",
            "create_response_body_base64",
            "turn_response_body_base64",
            "response_body_base64",
            "conversation_deduplicated",
            "turn_deduplicated",
            "conversation_turn_count",
            "internal_record_verified",
            "public_internal_projection_equal",
            "response_sha256",
            "promotion_receipt_body_base64",
            "sha256_hex_file",
            "answer_source",
            "renderer",
            "guard_decision",
            "pass",
            "--disable",
            "--noproxy '*'",
            "--proto '=http'",
            "--max-redirs 0",
        ):
            self.assertIn(phrase, text)
        self.assertNotIn("public_response:$proof[0].public_response", text)
        self.assertNotIn("internal_record:$proof[0].internal_record", text)
        self.assertNotIn("public_response:$promotion[0].public_response", text)
        self.assertNotIn("internal_record:$promotion[0].internal_record", text)
        for redundant_projection in (
            "answer_source:$proof[0].result.answer_source",
            "guard_decision:$proof[0].result.guard_decision",
            "validation:{",
            "answer_source:$promotion[0].result.answer_source",
            "guard_decision:$promotion[0].result.guard_decision",
            "internal_record_verified:$promotion[0].result.internal_record_verified",
        ):
            self.assertNotIn(redundant_projection, text)
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
        self.assertIn("select(.deduplicated == false)", text)
        self.assertIn("(.conversation.turns | length) == 0", text)
        self.assertIn("select(.turn.turn_number == 1)", text)
        self.assertIn("(.conversation.turns | length) == 1", text)
        canary_body = text.split("verify_canary_answer() {", 1)[1].split(
            "promote_production_answer() {", 1
        )[0]
        self.assertIn("new Date().toISOString()", canary_body)
        verified_at_body = canary_body.split("local verified_at", 1)[1]
        self.assertNotIn(
            'verified_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"',
            verified_at_body,
        )
        publication_tail = text.rsplit('chmod 0640 "${temporary}"', 1)[1]
        self.assertLess(
            publication_tail.index("cleanup_verification_raw_responses"),
            publication_tail.index('mv -n -- "${temporary}" "${EVIDENCE}"'),
        )
        self.assertLess(
            publication_tail.index('mv -n -- "${temporary}" "${EVIDENCE}"'),
            publication_tail.index("trap - EXIT"),
        )
        self.assertIn("未发布完成证据", publication_tail)

    def test_canary_and_production_use_distinct_fresh_turns(self) -> None:
        text = script("verify-runtime.sh")
        self.assertIn(
            'readonly CANARY_EVIDENCE="${UNIFIED_ROOT}/CANARY-VERIFICATION.json"',
            text,
        )
        self.assertIn("production conversation/turn 与 canary 重复", text)
        self.assertIn(".public_response.conversation_id", text)
        self.assertIn(".public_response.turn_id", text)
        self.assertIn("!= $canary_conversation_id", text)
        self.assertIn("!= $canary_turn_id", text)

    def test_runtime_verifier_embedded_python_compiles_and_is_deterministic(self) -> None:
        text = script("verify-runtime.sh")
        self.assertIn("ProxyHandler({})", text)
        self.assertIn("class NoRedirectHandler(HTTPRedirectHandler)", text)
        self.assertIn("DIRECT_OPENER.open(request", text)
        self.assertNotIn("urlopen(request", text)
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
        self.assertIn(
            "def fetch(path: str, timeout_seconds: int = 30)",
            embedded_python,
        )
        self.assertIn(
            "with DIRECT_OPENER.open(request, timeout=timeout_seconds)",
            embedded_python,
        )
        self.assertEqual(embedded_python.count("timeout_seconds=125"), 1)
        self.assertEqual(
            embedded_python.count(
                "as_window_path, timeout_seconds=125"
            ),
            1,
        )

        nginx = NGINX_CONFIG.read_text(encoding="utf-8")
        production_v1_proxy = nginx.split(
            "location ^~ /api/v1/ {", 1
        )[1].split("\n    }", 1)[0]
        production_v2_proxy = nginx.split(
            "location ^~ /api/v2/ {", 1
        )[1].split("\n    }", 1)[0]
        self.assertEqual(
            production_v1_proxy.count("proxy_connect_timeout 3s;"), 1
        )
        self.assertEqual(
            production_v1_proxy.count("proxy_read_timeout 125s;"), 1
        )
        self.assertNotIn("proxy_read_timeout 75s;", production_v1_proxy)
        self.assertEqual(
            production_v1_proxy.count("proxy_send_timeout 60s;"), 1
        )
        self.assertEqual(
            production_v2_proxy.count("proxy_connect_timeout 3s;"), 1
        )
        self.assertEqual(
            production_v2_proxy.count("proxy_read_timeout 75s;"), 1
        )
        self.assertEqual(
            production_v2_proxy.count("proxy_send_timeout 60s;"), 1
        )

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
            "domeye_interactive_agent_release_manifest_v2",
            "domeye_interactive_agent_promotion_v2",
            "public_response",
            "internal_record",
            "acceptance_record_id",
            "internal_record_verified",
            "public_internal_projection_equal",
            "PRODUCTION-VERIFICATION.json",
            "production_verified",
            "fail_closed",
            "canary_backend_is_closed",
            "production_backend_is_closed",
            "screen_session_is_absent",
            "curl --disable --noproxy '*' --proto '=http' --max-redirs 0",
            "baseline_backend_is_active",
            "127.0.0.1:28473",
            "acceptance_record_id",
            'canary:{path:"CANARY-VERIFICATION.json",sha256:$canary_sha}',
            'production:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_sha}',
        ):
            self.assertIn(phrase, activate)
        self.assertNotIn('"${BASELINE_MANAGER}" status', activate)
        self.assertIn('"${BASELINE_MANAGER}" stop', activate)
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

    def test_large_promotion_receipt_is_read_from_file_not_process_argv(self) -> None:
        text = script("verify-runtime.sh")
        self.assertEqual(text.count("--rawfile promotion_body"), 2)
        self.assertEqual(
            text.count(
                "promotion_receipt_body_base64:($promotion_body | @base64)"
            ),
            2,
        )
        self.assertNotIn("--arg promotion_body_base64", text)
        self.assertNotIn("base64 -w 0", text)

        receipt = {
            "schema_version": "domeye_interactive_agent_promotion_v2",
            "promotion_state": "verified",
            "note": "中文边界",
            "padding": "x" * (3 * 1024 * 1024),
        }
        receipt_bytes = (
            json.dumps(receipt, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "promotion.json"
            receipt_path.write_bytes(receipt_bytes)
            result = subprocess.run(
                [
                    "/usr/bin/jq",
                    "-n",
                    "--rawfile",
                    "promotion_body",
                    str(receipt_path),
                    "--slurpfile",
                    "promotion",
                    str(receipt_path),
                    "{promotion_receipt_body_base64:"
                    "($promotion_body | @base64),"
                    "promotion_receipt:$promotion[0]}",
                ],
                capture_output=True,
                check=False,
                timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        evidence = json.loads(result.stdout)
        self.assertEqual(
            evidence["promotion_receipt_body_base64"],
            base64.b64encode(receipt_bytes).decode("ascii"),
        )
        self.assertEqual(
            base64.b64decode(
                evidence["promotion_receipt_body_base64"], validate=True
            ),
            receipt_bytes,
        )
        self.assertEqual(evidence["promotion_receipt"], receipt)

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
            "if ! write_failed_closed_state failed_closed failed_closed",
            'if ! write_failed_closed_deployment true "${FAIL_CLOSED_SHA}"',
            'if ! verify_failed_closed_closure "${FAIL_CLOSED_TEMP}"',
            "if ! publish_fail_closed_evidence",
            "if ! verify_failed_closed_closure; then",
        )
        positions = [execute.index(item) for item in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(".production_verified = false", text)
        self.assertIn(".was_production_verified = true", text)

    def test_fail_closed_receipt_binds_and_preserves_pre_rollback_v2_evidence(
        self,
    ) -> None:
        text = script("rollback-runtime.sh")
        for phrase in (
            'readonly CANARY_EVIDENCE="${UNIFIED_ROOT}/CANARY-VERIFICATION.json"',
            'readonly PRODUCTION_EVIDENCE="${UNIFIED_ROOT}/PRODUCTION-VERIFICATION.json"',
            "freeze_pre_rollback_evidence",
            "frozen_pre_rollback_evidence_is_unchanged",
            "verify_failed_closed_closure",
            'verify_failed_closed_closure "${FAIL_CLOSED_TEMP}"',
            '.workflow_completion.state == "verified"',
            '.schema_version == "domeye_country_outage_general_activation_v2"',
            '.schema_version == "domeye_country_outage_general_deployment_v2"',
            "domeye_country_outage_general_fail_closed_v2",
            "pre_rollback_evidence",
            "general_candidate",
            "candidate_manifest_sha256",
            "acceptance_record_id",
            "acceptance_record_sha256",
            "acceptance_replay_receipt_sha256",
            'canary:{path:"CANARY-VERIFICATION.json",sha256:$canary_sha}',
            'production:{path:"PRODUCTION-VERIFICATION.json",sha256:$production_sha}',
            "INTERACTIVE_AGENT_CURRENT",
            "INTERACTIVE_AGENT_ACTIVE",
            '.candidate.backend.release_id == $backend_release_id',
            '.candidate.frontend.release_id == $frontend_release_id',
            '.components.backend.release_id == $backend_release_id',
            '.components.frontend.release_id == $frontend_release_id',
        ):
            self.assertIn(phrase, text)

        execute = text.split("--execute)", 1)[1]
        self.assertLess(
            execute.index("if ! frozen_pre_rollback_evidence_is_unchanged"),
            execute.index("if ! prepare_fail_closed_evidence"),
        )
        self.assertLess(
            execute.index('if ! verify_failed_closed_closure "${FAIL_CLOSED_TEMP}"'),
            execute.index("if ! publish_fail_closed_evidence"),
        )
        self.assertLess(
            execute.index("if ! publish_fail_closed_evidence"),
            execute.index("if ! verify_failed_closed_closure; then"),
        )
        self.assertLess(
            execute.index("if ! verify_failed_closed_closure; then"),
            execute.index("生产已失败关闭且未恢复任何旧路由"),
        )
        self.assertEqual(
            text.count("curl --disable --noproxy '*' --proto '=http' --max-redirs 0"),
            2,
        )
        for evidence in ("CANDIDATE", "CANARY_EVIDENCE", "PRODUCTION_EVIDENCE"):
            self.assertNotIn(f'unlink "${{{evidence}}}"', text)
            self.assertNotIn(f'mv -T -- "${{{evidence}}}"', text)

        for input_name in ("CANDIDATE", "STATE", "DEPLOYMENT"):
            marker = f"' \"${{{input_name}}}\" >/dev/null; then"
            jq_block = text.split(marker, 1)[0].rsplit("if ! jq -e", 1)[1]
            used = set(re.findall(r"\$([a-z][a-z0-9_]*)", jq_block))
            bound = set(
                re.findall(r"--arg(?:json)?\s+([a-z][a-z0-9_]*)", jq_block)
            )
            self.assertEqual(
                used - bound,
                set(),
                f"{input_name} jq 使用了未绑定变量：{sorted(used - bound)}",
            )
            jq_filter = jq_block.split("'", 1)[1]
            command = ["jq", "-n"]
            for name in sorted(used):
                command.extend(("--arg", name, "fixture"))
            command.append(jq_filter)
            compiled = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                compiled.returncode,
                0,
                f"{input_name} jq 无法编译：{compiled.stderr}",
            )

    def test_general_release_consumers_have_no_v1_interactive_contracts(self) -> None:
        combined = "\n".join(
            script(name)
            for name in (
                "prepare-runtime-release.sh",
                "manage-runtime.sh",
                "verify-runtime.sh",
                "activate-runtime.sh",
            )
        )
        for forbidden in (
            "domeye_interactive_agent_release_manifest_v1",
            "domeye_interactive_agent_release_probe_v1",
            "domeye_interactive_agent_promotion_v1",
            "first-vertical-slice/v1/candidate.json",
        ):
            self.assertNotIn(forbidden, combined)

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
        governance = (
            ROOT / "deploy/governance/check-release-normalization.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "curl --disable --noproxy '*' --proto '=http' --max-redirs 0",
            governance,
        )
        for required in (
            "legacy_agent_surfaces_retired:true",
            "require_port_closed 28474",
            "require_port_closed 28475",
            "require_screen_absent 'domeye_country_outage_agent'",
            "require_screen_absent 'domeye_country_outage_p1_chat'",
            "/api/v2/country-outage/reports",
            "/api/v2/country-outage/investigations/retired-surface-probe",
        ):
            self.assertIn(required, governance)
        self.assertNotIn("fallback_route", combined)
        self.assertNotIn("route_selector", combined)


if __name__ == "__main__":
    unittest.main()
