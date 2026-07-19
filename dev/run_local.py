#!/usr/bin/env python3
"""启动可自动清理的本地开发栈。"""

import argparse
import datetime
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
FRONTEND_DIR = ROOT / "frontend"
BACKEND_DIR = ROOT / "backend"
FIXTURE_PATH = ROOT / "dev" / "fixtures" / "api-snapshot.json"
MOCK_TIMEOUT_MS = 250
MINIMUM_TIMEOUT_DELAY_SECONDS = 0.5


def load_data_window():
    with FIXTURE_PATH.open("r", encoding="utf-8") as fixture_file:
        window = json.load(fixture_file)["data_window"]
    try:
        start = datetime.datetime.strptime(window["start_time"], "%Y-%m-%d %H:%M:%S")
        end = datetime.datetime.strptime(window["end_time"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("开发数据窗口必须使用 YYYY-MM-DD HH:MM:SS 格式") from error
    if start >= end:
        raise RuntimeError("开发数据窗口起点必须早于终点")

    return {
        "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
        "snapshot": end.strftime("%Y-%m-%d %H:%M:%S"),
        "backend_start": start.strftime("%Y-%m-%d %H:%M:%S"),
        "backend_end_exclusive": (end + datetime.timedelta(seconds=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }


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
        "-o", "BatchMode=yes",
        "-o", "ClearAllForwardings=yes",
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


def wait_for_remote_api(port, process, timeout=10):
    """同时核对服务身份与固定窗口守卫，避免隧道连到错误 API。"""

    deadline = time.time() + timeout
    health_url = "http://127.0.0.1:{}/api/v1/healthz".format(port)
    guard_url = (
        "http://127.0.0.1:{}/api/v1/events"
        "?date=2026-04-01_2026-04-01&page_num=1&page_size=10"
    ).format(port)
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
                and "2026-02-01 00:00:00" in message
                and "2026-03-31 23:59:59" in message
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
        wait_for_remote_api(api_port, tunnel)
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

    common_env = os.environ.copy()
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
