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


if __name__ == "__main__":
    unittest.main()
