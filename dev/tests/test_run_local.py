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
        self.assertEqual(command[command.index("-F") + 1], "/dev/null")
        self.assertNotIn("ClearAllForwardings=yes", command)
        self.assertIn("ExitOnForwardFailure=yes", command)
        self.assertIn("127.0.0.1:51234:127.0.0.1:31629", command)
        self.assertEqual(command[-1], "root@10.99.8.16")

    def test_remote_api_probe_uses_exclusive_window_date_twice(self):
        process = SimpleNamespace(poll=lambda: None)
        window = RUN_LOCAL.load_data_window()
        responses = [
            {"status": "ok", "service": "domeye-core"},
            {
                "status": False,
                "msg": "开发数据仅支持 {} 至 {}：事件日期超出窗口".format(
                    window["backend_start"],
                    window["snapshot"],
                ),
            },
        ]
        with patch.object(RUN_LOCAL, "_read_json_url", side_effect=responses) as read_json:
            RUN_LOCAL.wait_for_remote_api(51234, process, window, timeout=0.1)

        self.assertEqual(
            read_json.call_args_list[1].args[0],
            "http://127.0.0.1:51234/api/v1/events"
            "?date=2026-04-01_2026-04-01&page_num=1&page_size=10",
        )
        self.assertEqual(read_json.call_args_list[1].kwargs, {"expected_status": 400})


class DevelopmentWindowTest(unittest.TestCase):
    def test_fixture_window_keeps_seconds_and_derives_exclusive_end(self):
        window = RUN_LOCAL.load_data_window()
        self.assertEqual(window["start"], "2026-02-01T00:00:00")
        self.assertEqual(window["end"], "2026-03-31T23:59:59")
        self.assertEqual(window["snapshot"], "2026-03-31 23:59:59")
        self.assertEqual(window["backend_start"], "2026-02-01 00:00:00")
        self.assertEqual(window["backend_end_exclusive"], "2026-04-01 00:00:00")

    def test_child_environment_drops_database_and_production_values(self):
        sanitized = RUN_LOCAL.sanitized_environment({
            "PATH": "/usr/bin",
            "SSH_AUTH_SOCK": "/tmp/agent.sock",
            "DOMEYE_DEV_REMOTE_HOST": "dev@example.invalid",
            "DB_HOST": "production-db",
            "PGPASSWORD": "secret",
            "SOURCE_DB_PASSWORD": "source-secret",
            "DOMEYE_CORE_DB_READER_PASSWORD": "reader-secret",
            "INFO_DIR": "/old/project/info",
            "MAIL_PASSWORD": "mail-secret",
        })
        self.assertEqual(sanitized["PATH"], "/usr/bin")
        self.assertEqual(sanitized["SSH_AUTH_SOCK"], "/tmp/agent.sock")
        self.assertEqual(sanitized["DOMEYE_DEV_REMOTE_HOST"], "dev@example.invalid")
        for name in (
            "DB_HOST",
            "PGPASSWORD",
            "SOURCE_DB_PASSWORD",
            "DOMEYE_CORE_DB_READER_PASSWORD",
            "INFO_DIR",
            "MAIL_PASSWORD",
        ):
            self.assertNotIn(name, sanitized)

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

    def test_local_real_backend_is_disabled_in_fixed_profile(self):
        args = SimpleNamespace(mode="dev", api="real")
        with self.assertRaisesRegex(RuntimeError, "API_MODE=remote"):
            RUN_LOCAL.start_development_processes(
                args,
                {},
                RUN_LOCAL.load_data_window(),
                32123,
                32125,
                [],
            )

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
