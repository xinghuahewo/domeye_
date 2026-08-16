from pathlib import Path
import unittest


DEV_ROOT = Path(__file__).resolve().parents[1]
DATABASE_SCRIPT = (DEV_ROOT / "database" / "manage-dev-database.sh").read_text(
    encoding="utf-8"
)
API_SCRIPT = (DEV_ROOT / "backend" / "manage-dev-api.sh").read_text(
    encoding="utf-8"
)


class ServerLifecycleContractTest(unittest.TestCase):
    def test_reverification_invalidates_old_success_before_querying(self):
        self.assertIn("invalidate_verification()", DATABASE_SCRIPT)
        self.assertIn(
            "invalidate_verification\n        verify_database\n        record_verification pruned",
            DATABASE_SCRIPT,
        )
        self.assertIn("assert_dev_api_not_running; verify_database_action", DATABASE_SCRIPT)

    def test_development_api_uses_and_identifies_isolated_application_logs(self):
        self.assertIn(
            'readonly APP_LOG_DIR="${LOG_DIR}/app"',
            API_SCRIPT,
        )
        self.assertIn(
            'export DOMEYE_LOG_DIR="${APP_LOG_DIR}"',
            API_SCRIPT,
        )
        self.assertIn(
            '$1 == "DOMEYE_LOG_DIR" && value() == log_dir { o=1 }',
            API_SCRIPT,
        )
        self.assertIn('export PYTHONDONTWRITEBYTECODE=1', API_SCRIPT)
        self.assertIn(".hashes.verify_sql", API_SCRIPT)
        self.assertIn("    export INFO_DIR\n", API_SCRIPT)
        self.assertNotIn('export INFO_DIR="${INFO_DIR}"', API_SCRIPT)

    def test_remote_development_api_prewarms_static_as_data_before_ready(self):
        self.assertIn('STATIC_AS_WARMUP_URL=', API_SCRIPT)
        self.assertIn('if [[ "${API_PROFILE}" != \'remote\' ]]; then', API_SCRIPT)
        warmup = API_SCRIPT.index('if ! api_static_as_warmup_request; then')
        ready = API_SCRIPT.index('START_COMPLETE=true', warmup)
        self.assertLess(warmup, ready)

    def test_fixed_core_api_only_loads_agent_from_restricted_runtime_config(self):
        self.assertIn(
            "COUNTRY_OUTAGE_AGENT_RUNTIME_ENV="
            "'/home/bgpdata/Domeye-Core-runtime/config/country-outage-agent.env'",
            API_SCRIPT,
        )
        self.assertIn('if [[ "${API_PROFILE}" != \'core\' ]]; then', API_SCRIPT)
        self.assertIn(
            '$(stat -c \'%a\' "${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV}")" != \'600\'',
            API_SCRIPT,
        )
        self.assertIn(
            "COUNTRY_OUTAGE_AGENT_EXPECTED_IDENTITY_MODE='internal_fixed_history'",
            API_SCRIPT,
        )
        self.assertIn(
            "COUNTRY_OUTAGE_AGENT_EXPECTED_SCOPE='country_outage_event_read:IR'",
            API_SCRIPT,
        )
        self.assertNotIn("source \"${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV}\"", API_SCRIPT)

    def test_fixed_core_api_requires_disabled_external_readiness_before_ready(self):
        readiness = API_SCRIPT.index("country_outage_agent_readiness_request()")
        start_gate = API_SCRIPT.index(
            "国家中断 Agent 已配置，但 Sidecar readiness 未通过"
        )
        ready = API_SCRIPT.index("START_COMPLETE=true", start_gate)
        self.assertLess(readiness, start_gate)
        self.assertLess(start_gate, ready)
        self.assertIn("probe-sidecar.mjs", API_SCRIPT)
        self.assertIn('"${COUNTRY_OUTAGE_AGENT_PROBE_SCRIPT}"', API_SCRIPT)
        self.assertIn("env -i", API_SCRIPT)

    def test_agent_token_is_not_printed_or_passed_on_screen_command_line(self):
        screen_start = API_SCRIPT.split("screen \\", 1)[1].split(
            "/bin/bash \"${SCRIPT_PATH}\" _serve",
            1,
        )[0]
        self.assertNotIn("COUNTRY_OUTAGE_AGENT_SHARED_TOKEN", screen_start)
        self.assertNotIn("printf", screen_start)
        readiness = API_SCRIPT.split(
            "country_outage_agent_readiness_request()",
            1,
        )[1].split("load_database_config()", 1)[0]
        self.assertNotIn("Authorization: Bearer", readiness)
        self.assertNotIn("-H ", readiness)

    def test_fixed_core_process_identity_binds_current_agent_configuration(self):
        self.assertIn(
            'COUNTRY_OUTAGE_AGENT_CONFIG_SHA256_VALUE="$(\n'
            '        sha256sum "${COUNTRY_OUTAGE_AGENT_RUNTIME_ENV}"',
            API_SCRIPT,
        )
        self.assertIn(
            'export COUNTRY_OUTAGE_AGENT_CONFIG_SHA256='
            '"${COUNTRY_OUTAGE_AGENT_CONFIG_SHA256_VALUE}"',
            API_SCRIPT,
        )
        self.assertIn(
            '$1 == "COUNTRY_OUTAGE_AGENT_CONFIG_SHA256" '
            "&& value() == agent_config_sha { u=1 }",
            API_SCRIPT,
        )


if __name__ == "__main__":
    unittest.main()
