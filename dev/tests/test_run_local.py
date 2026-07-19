import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


RUN_LOCAL_PATH = Path(__file__).resolve().parents[1] / "run_local.py"
SPEC = importlib.util.spec_from_file_location("domeye_run_local", RUN_LOCAL_PATH)
RUN_LOCAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN_LOCAL)


class RemoteTunnelTest(unittest.TestCase):
    def test_remote_tunnel_only_forwards_loopback_api(self):
        command = RUN_LOCAL.remote_tunnel_command(
            local_port=51234,
            remote_host="root@10.99.8.16",
            remote_port=31629,
        )
        self.assertEqual(command[0:2], ["ssh", "-N"])
        self.assertIn("ExitOnForwardFailure=yes", command)
        self.assertIn("127.0.0.1:51234:127.0.0.1:31629", command)
        self.assertEqual(command[-1], "root@10.99.8.16")


class DevelopmentWindowTest(unittest.TestCase):
    def test_fixture_window_keeps_seconds_and_derives_exclusive_end(self):
        window = RUN_LOCAL.load_data_window()
        self.assertEqual(window["start"], "2026-02-01T00:00:00")
        self.assertEqual(window["end"], "2026-03-31T23:59:59")
        self.assertEqual(window["snapshot"], "2026-03-31 23:59:59")
        self.assertEqual(window["backend_start"], "2026-02-01 00:00:00")
        self.assertEqual(window["backend_end_exclusive"], "2026-04-01 00:00:00")

    def test_timeout_scenario_deadline_is_shorter_than_mock_delay(self):
        env = {
            "DOMEYE_MOCK_SCENARIO": "timeout",
            "DOMEYE_MOCK_DELAY_SECONDS": "0.1",
            "VITE_API_TIMEOUT_MS": "60000",
        }
        self.assertEqual(RUN_LOCAL.configure_mock_scenario(env), "timeout")
        self.assertLess(
            int(env["VITE_API_TIMEOUT_MS"]),
            float(env["DOMEYE_MOCK_DELAY_SECONDS"]) * 1000,
        )

    def test_normal_scenario_does_not_override_frontend_timeout(self):
        env = {"DOMEYE_MOCK_SCENARIO": "normal", "VITE_API_TIMEOUT_MS": "9000"}
        self.assertEqual(RUN_LOCAL.configure_mock_scenario(env), "normal")
        self.assertEqual(env["VITE_API_TIMEOUT_MS"], "9000")

    def test_real_backend_enforces_fixture_window(self):
        window = RUN_LOCAL.load_data_window()
        env = RUN_LOCAL.build_real_backend_env({}, window, 32123)
        self.assertEqual(env["DOMEYE_DATA_SNAPSHOT_TIME"], "2026-03-31 23:59:59")
        self.assertEqual(env["DOMEYE_ENFORCE_DATA_WINDOW"], "true")
        self.assertEqual(env["DOMEYE_DATA_WINDOW_START"], "2026-02-01 00:00:00")
        self.assertEqual(
            env["DOMEYE_DATA_WINDOW_END_EXCLUSIVE"],
            "2026-04-01 00:00:00",
        )
        self.assertEqual(env["PORT"], "32123")

    def test_startup_failure_cleans_every_process_started_so_far(self):
        marker = object()

        def fail_after_api_start(_args, _env, _window, _api_port, _frontend_port, processes):
            processes.append(marker)
            raise RuntimeError("前端构建失败")

        args = SimpleNamespace(mode="preview", api="mock")
        with patch.object(RUN_LOCAL, "require_command"), \
             patch.object(RUN_LOCAL, "available_port", side_effect=[32101, 32103]), \
             patch.object(RUN_LOCAL, "start_development_processes", side_effect=fail_after_api_start), \
             patch.object(RUN_LOCAL, "terminate") as terminate:
            with self.assertRaisesRegex(RuntimeError, "前端构建失败"):
                RUN_LOCAL.run(args)

        terminate.assert_called_once_with([marker])


if __name__ == "__main__":
    unittest.main()
