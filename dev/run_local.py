#!/usr/bin/env python3
"""启动可自动清理的本地开发栈。"""

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dev.data_profile import load_data_profile  # noqa: E402


FRONTEND_DIR = ROOT / "frontend"
BACKEND_DIR = ROOT / "backend"
FIXTURE_PATH = ROOT / "dev" / "fixtures" / "api-snapshot.json"
MOCK_TIMEOUT_MS = 250
MINIMUM_TIMEOUT_DELAY_SECONDS = 0.5
SENSITIVE_CHILD_ENV_NAMES = {
    "AUTO_INIT_DB",
    "BASE_DATA_PATH",
    "DATABASE_URL",
    "DB_HOST",
    "DB_NAME",
    "DB_PASSWORD",
    "DB_PORT",
    "DB_USER",
    "DEBUG",
    "FLASK_CONFIG",
    "HOST",
    "INFO_DIR",
    "LOAD_CORE_DATA_ON_STARTUP",
    "PGDATABASE",
    "PGHOST",
    "PGPASSWORD",
    "PGPORT",
    "PGUSER",
    "PORT",
    "RIB_HISTORY_FILE",
    "SECRET_KEY",
    "SOURCE",
    "SSH_HOST",
    "SSH_HOST2",
    "SSH_PWD",
    "SSH_PWD2",
    "SSH_USER",
    "SSH_USER2",
}
SENSITIVE_CHILD_ENV_PREFIXES = ("DOMEYE_CORE_DB_", "MAIL_", "SMTP_", "SOURCE_DB_")


def load_data_window():
    profile = load_data_profile()
    with FIXTURE_PATH.open("r", encoding="utf-8") as fixture_file:
        window = json.load(fixture_file)["data_window"]
    expected_window = {
        "start_time": profile["local"]["start"],
        "end_time": profile["local"]["snapshot"],
        "timezone": profile["timezone"],
    }
    if window != expected_window:
        raise RuntimeError("开发快照窗口与 config/data-profile.json 不一致")

    return {
        "id": profile["id"],
        "timezone": profile["timezone"],
        "start": profile["local"]["frontend_start"],
        "end": profile["local"]["frontend_end"],
        "snapshot": profile["local"]["snapshot"],
        "backend_start": profile["local"]["start"],
        "backend_end_exclusive": profile["local"]["end_exclusive"],
    }


def sanitized_environment(source=None):
    """清除开发子进程不应继承的数据库、生产和旧采集配置。"""

    env = dict(os.environ if source is None else source)
    for name in tuple(env):
        if name in SENSITIVE_CHILD_ENV_NAMES or name.startswith(SENSITIVE_CHILD_ENV_PREFIXES):
            env.pop(name, None)
    return env


def configure_mock_scenario(env):
    """让 timeout 场景的客户端截止时间必定短于服务端延迟。"""

    scenario = env.get("DOMEYE_MOCK_SCENARIO", "normal").strip().lower()
    if scenario != "timeout":
        return scenario

    try:
        delay_seconds = float(env.get("DOMEYE_MOCK_DELAY_SECONDS", "3"))
    except ValueError:
        delay_seconds = 3.0
    if not math.isfinite(delay_seconds) or delay_seconds <= 0:
        delay_seconds = 3.0
    delay_seconds = max(delay_seconds, MINIMUM_TIMEOUT_DELAY_SECONDS)
    env["DOMEYE_MOCK_DELAY_SECONDS"] = "{:g}".format(delay_seconds)
    env["VITE_API_TIMEOUT_MS"] = str(MOCK_TIMEOUT_MS)
    return scenario


def available_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def remote_tunnel_command(local_port, remote_host, remote_port):
    return [
        "ssh",
        "-N",
        "-F", "/dev/null",
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-L", "127.0.0.1:{}:127.0.0.1:{}".format(local_port, remote_port),
        remote_host,
    ]


def wait_for_port(port, process, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("SSH 开发 API 隧道提前退出，退出码 {}".format(process.returncode))
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("SSH 开发 API 隧道未在 {} 秒内就绪".format(timeout))


def _read_json_url(url, expected_status=200):
    try:
        response = urlopen(url, timeout=1)
    except HTTPError as error:
        if error.code != expected_status:
            raise
        response = error
    with response:
        if response.status != expected_status:
            raise RuntimeError("开发 API 返回意外状态码 {}".format(response.status))
        return json.loads(response.read().decode("utf-8"))


def wait_for_remote_api(port, process, data_window, timeout=10):
    """同时核对服务身份与固定窗口守卫，避免隧道连到错误 API。"""

    deadline = time.time() + timeout
    health_url = "http://127.0.0.1:{}/api/v1/healthz".format(port)
    rejected_date = data_window["backend_end_exclusive"].split(" ", 1)[0]
    guard_url = (
        "http://127.0.0.1:{port}/api/v1/events"
        "?date={date}_{date}&page_num=1&page_size=10"
    ).format(port=port, date=rejected_date)
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("SSH 开发 API 隧道提前退出，退出码 {}".format(process.returncode))
        try:
            health = _read_json_url(health_url)
            rejected = _read_json_url(guard_url, expected_status=400)
            message = rejected.get("msg", "") if isinstance(rejected, dict) else ""
            if (
                isinstance(health, dict)
                and health.get("status") == "ok"
                and health.get("service") == "domeye-core"
                and isinstance(rejected, dict)
                and rejected.get("status") is False
                and data_window["backend_start"] in message
                and data_window["snapshot"] in message
            ):
                return
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(0.2)
    raise RuntimeError("SSH 隧道目标不是已验证的 2–3 月 Domeye 开发 API")


def require_command(name):
    if shutil.which(name) is None:
        raise SystemExit("缺少开发命令：{}".format(name))


def start_process(command, cwd, env):
    return subprocess.Popen(command, cwd=str(cwd), env=env)


def terminate(processes):
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    deadline = time.time() + 5
    for process in reversed(processes):
        if process.poll() is None:
            try:
                process.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                process.kill()
    for process in reversed(processes):
        if process.poll() is None:
            process.wait()


def start_development_processes(args, common_env, data_window, api_port, frontend_port, processes):
    """启动 API 与前端；任一步失败时由调用方统一清理已启动进程。"""

    if args.api == "mock":
        scenario = configure_mock_scenario(common_env)
        api_command = [
            sys.executable,
            str(ROOT / "dev" / "mock_server.py"),
            "--port",
            str(api_port),
        ]
        api_label = "固定开发快照（{}）".format(scenario)
        processes.append(start_process(api_command, ROOT, common_env))
    elif args.api == "real":
        raise RuntimeError(
            "feb-mar-2026 数据档禁止本地真实后端，真实数据联调请使用 "
            "make dev API_MODE=remote"
        )
    else:
        require_command("ssh")
        remote_host = common_env.get("DOMEYE_DEV_REMOTE_HOST", "root@10.99.8.16")
        try:
            remote_port = int(common_env.get("DOMEYE_DEV_REMOTE_API_PORT", "31629"))
        except ValueError as error:
            raise RuntimeError("DOMEYE_DEV_REMOTE_API_PORT 必须是端口数字") from error
        if not 1 <= remote_port <= 65535:
            raise RuntimeError("DOMEYE_DEV_REMOTE_API_PORT 超出有效范围")
        tunnel = start_process(
            remote_tunnel_command(api_port, remote_host, remote_port),
            ROOT,
            common_env,
        )
        processes.append(tunnel)
        wait_for_port(api_port, tunnel)
        wait_for_remote_api(api_port, tunnel, data_window)
        api_label = "服务器两个月开发库（经 SSH 隧道）"

    if args.mode == "preview":
        subprocess.run(["npm", "run", "build"], cwd=str(FRONTEND_DIR), env=common_env, check=True)
        frontend_command = [
            "npm", "run", "preview", "--", "--host", "127.0.0.1",
            "--port", str(frontend_port), "--strictPort",
        ]
    else:
        frontend_command = [
            "npm", "run", "dev", "--", "--host", "127.0.0.1",
            "--port", str(frontend_port), "--strictPort",
        ]
    processes.append(start_process(frontend_command, FRONTEND_DIR, common_env))
    return api_label


def run(args):
    require_command("npm")
    api_port = available_port()
    frontend_port = available_port()
    while frontend_port == api_port:
        frontend_port = available_port()

    common_env = sanitized_environment()
    common_env["VITE_API_PROXY_TARGET"] = "http://127.0.0.1:{}".format(api_port)
    common_env["VITE_PORT"] = str(frontend_port)
    common_env["VITE_COMPONENT_PREVIEW"] = "true"
    data_window = load_data_window()
    common_env["VITE_DATA_WINDOW_START"] = data_window["start"]
    common_env["VITE_DATA_WINDOW_END"] = data_window["end"]
    processes = []
    try:
        api_label = start_development_processes(
            args,
            common_env,
            data_window,
            api_port,
            frontend_port,
            processes,
        )

        print("\nDomeye 本地{}已启动".format("预览" if args.mode == "preview" else "开发栈"), flush=True)
        print("  前端：http://127.0.0.1:{}".format(frontend_port), flush=True)
        print("  API ：http://127.0.0.1:{}/api/v1/".format(api_port), flush=True)
        print("  数据：{}".format(api_label), flush=True)
        print("  窗口：{} 至 {}".format(data_window["start"], data_window["end"]), flush=True)
        print("  退出：Ctrl-C（临时进程会自动清理）\n", flush=True)

        stopping = [False]

        def stop(_signum, _frame):
            stopping[0] = True

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        while not stopping[0]:
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    raise RuntimeError("本地开发进程提前退出，退出码 {}".format(return_code))
            time.sleep(0.2)
    finally:
        terminate(processes)


def main():
    parser = argparse.ArgumentParser(description="启动 Domeye 本地开发或预览环境")
    parser.add_argument("mode", choices=("dev", "preview"))
    parser.add_argument("--api", choices=("mock", "real", "remote"), default="mock")
    args = parser.parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        pass
    except (RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))


if __name__ == "__main__":
    main()
