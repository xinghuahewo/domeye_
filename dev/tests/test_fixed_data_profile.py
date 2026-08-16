from pathlib import Path
import unittest
import json


ROOT = Path(__file__).resolve().parents[2]
DATA_PROFILE = json.loads(
    (ROOT / "config" / "data-profile.json").read_text(encoding="utf-8")
)
PROFILE = (ROOT / "deploy" / "lib" / "data-profile.sh").read_text(encoding="utf-8")
API_MANAGER = (ROOT / "dev" / "backend" / "manage-dev-api.sh").read_text(
    encoding="utf-8"
)
DATABASE_MANAGER = (ROOT / "dev" / "database" / "manage-dev-database.sh").read_text(
    encoding="utf-8"
)
START_BACKEND = (ROOT / "deploy" / "start-backend.sh").read_text(encoding="utf-8")
ACTIVATE_DATABASE = (ROOT / "deploy" / "database" / "activate-database.sh").read_text(
    encoding="utf-8"
)
ROLLBACK_DATABASE = (ROOT / "deploy" / "database" / "rollback-database.sh").read_text(
    encoding="utf-8"
)
BUILD_DATABASE = (ROOT / "deploy" / "database" / "build-database-artifact.sh").read_text(
    encoding="utf-8"
)
FULL_ACCEPTANCE = (ROOT / "deploy" / "acceptance" / "full-acceptance.sh").read_text(
    encoding="utf-8"
)
FIXED_FRONTEND = (ROOT / "deploy" / "build-fixed-frontend.sh").read_text(
    encoding="utf-8"
)
FRONTEND_COMMON = (ROOT / "deploy" / "lib" / "frontend-common.sh").read_text(
    encoding="utf-8"
)
VITE_CONFIG = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
EVENTS_PAGE = (ROOT / "frontend" / "src" / "pages" / "EventsPage.vue").read_text(
    encoding="utf-8"
)
RELEASE_COMMANDS = [
    (ROOT / "deploy" / "release" / name).read_text(encoding="utf-8")
    for name in ("prepare.sh", "activate.sh", "rollback.sh")
]
RELEASE_GC = (ROOT / "deploy" / "release" / "gc.sh").read_text(encoding="utf-8")


class FixedDataProfileContractTest(unittest.TestCase):
    def test_profile_is_pinned_to_february_and_march(self):
        self.assertEqual(DATA_PROFILE["id"], "feb-mar-2026")
        self.assertEqual(DATA_PROFILE["timezone"], "Asia/Shanghai")
        self.assertEqual(DATA_PROFILE["window_start"], "2026-02-01T00:00:00+08:00")
        self.assertEqual(
            DATA_PROFILE["window_end_exclusive"],
            "2026-04-01T00:00:00+08:00",
        )
        self.assertEqual(DATA_PROFILE["snapshot_time"], "2026-03-31T23:59:59+08:00")
        self.assertIn("config/data-profile.json", PROFILE)

    def test_core_profile_reuses_verified_manager_on_existing_api_port(self):
        self.assertIn("SCREEN_NAME='domeye_core_app'", API_MANAGER)
        self.assertIn("API_INSTANCE='domeye-core-feb-mar-2026'", API_MANAGER)
        self.assertIn("API_PORT='28473'", API_MANAGER)
        self.assertIn('DOMEYE_CORE_API_PROFILE="${API_PROFILE}"', API_MANAGER)

    def test_database_mutations_are_blocked_while_either_api_is_running(self):
        self.assertIn("CORE_API_SCREEN_NAME='domeye_core_app'", DATABASE_MANAGER)
        self.assertIn('core_suffix=".${CORE_API_SCREEN_NAME}"', DATABASE_MANAGER)

    def test_legacy_backend_start_is_blocked_in_fixed_profile(self):
        self.assertIn("deploy/manage-fixed-backend.sh", START_BACKEND)
        self.assertIn("domeye_core_require_realtime_profile", START_BACKEND)

    def test_realtime_activation_rollback_and_source_read_are_blocked(self):
        self.assertIn("domeye_core_require_realtime_profile", ACTIVATE_DATABASE)
        self.assertIn("domeye_core_require_realtime_profile", ROLLBACK_DATABASE)
        self.assertIn("domeye_core_require_source_database_access", BUILD_DATABASE)

    def test_full_acceptance_is_blocked_before_any_mutation(self):
        gate = FULL_ACCEPTANCE.index("domeye_core_require_realtime_profile || exit 1")
        first_stop = FULL_ACCEPTANCE.index('"${DEPLOY_DIR}/stop-backend.sh"')
        first_restore = FULL_ACCEPTANCE.index('"${DEPLOY_DIR}/database/restore-database.sh"')
        self.assertLess(gate, first_stop)
        self.assertLess(gate, first_restore)

    def test_production_frontend_is_built_with_fixed_window(self):
        self.assertIn('VITE_DATA_WINDOW_START="${DOMEYE_CORE_FIXED_DATA_START/ /T}"', FIXED_FRONTEND)
        self.assertIn('VITE_DATA_WINDOW_END="${DOMEYE_CORE_FIXED_SNAPSHOT_TIME/ /T}"', FIXED_FRONTEND)
        self.assertIn("install-frontend-build.sh", FIXED_FRONTEND)
        self.assertIn(
            'DOMEYE_CORE_FRONTEND_TARGET="${DOMEYE_CORE_FRONTEND_RUNTIME_ROOT}/web/dist"',
            FRONTEND_COMMON,
        )

    def test_plain_frontend_build_defaults_to_the_unique_data_profile(self):
        self.assertIn("../config/data-profile.json", VITE_CONFIG)
        self.assertIn("profile.window_start.slice(0, 19)", VITE_CONFIG)
        self.assertIn("profile.snapshot_time.slice(0, 19)", VITE_CONFIG)
        self.assertIn("'import.meta.env.VITE_DATA_WINDOW_START'", VITE_CONFIG)
        self.assertIn("'import.meta.env.VITE_DATA_WINDOW_END'", VITE_CONFIG)

    def test_events_page_fails_closed_without_the_fixed_window(self):
        resolve_index = EVENTS_PAGE.index("const dataWindow = resolveDataWindow(import.meta.env)")
        default_index = EVENTS_PAGE.index("const defaultDates = dataWindow")
        request_index = EVENTS_PAGE.index("result.value = await getEvents")
        gate_index = EVENTS_PAGE.index("if (!dataWindow)")
        self.assertLess(resolve_index, default_index)
        self.assertLess(gate_index, request_index)
        self.assertIn("已阻止按当前日期查询", EVENTS_PAGE)

    def test_stateful_release_commands_are_blocked_by_fixed_profile(self):
        for command in RELEASE_COMMANDS:
            self.assertIn("domeye_core_require_realtime_profile || exit 1", command)
        execute_gate = RELEASE_GC.index('if [[ "${execute_gc}" == true ]]')
        realtime_gate = RELEASE_GC.index("domeye_core_require_realtime_profile || exit 1")
        delete_command = RELEASE_GC.index('rm -rf -- "${candidate_path}"')
        self.assertLess(execute_gate, realtime_gate)
        self.assertLess(realtime_gate, delete_command)


if __name__ == "__main__":
    unittest.main()
