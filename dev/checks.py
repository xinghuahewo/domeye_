#!/usr/bin/env python3
"""按改动风险执行快速、集成和发布检查。"""

import argparse
from pathlib import Path
import shlex
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
RISK_NAMES = {
    0: "L0 文案/样式/文档",
    1: "L1 普通前端功能",
    2: "L2 API/业务逻辑/构建配置",
    3: "L3 Schema/数据/权限/部署",
}
IGNORED_PREFIXES = ("frontend/dist/", "frontend/node_modules/", "backend/.venv/")


def git_lines(*arguments):
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", *arguments],
        cwd=str(ROOT),
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def changed_files(base_ref=None):
    files = set()
    if base_ref:
        files.update(git_lines("diff", "--name-only", "--diff-filter=ACMR", "{}...HEAD".format(base_ref)))
    files.update(git_lines("diff", "--name-only", "--diff-filter=ACMR"))
    files.update(git_lines("diff", "--cached", "--name-only", "--diff-filter=ACMR"))
    files.update(git_lines("ls-files", "--others", "--exclude-standard"))
    return sorted(
        path for path in files
        if not path.startswith(IGNORED_PREFIXES)
    )


def classify(path):
    if path.startswith(("dev/database/", "dev/backend/")):
        return 3
    if path.endswith(".md") or path.startswith("docs/"):
        return 0
    if path.endswith((".css", ".scss", ".sass", ".less", ".svg")):
        return 0
    if path.startswith(("deploy/", ".github/workflows/")):
        return 3
    if path.startswith("backend/core/"):
        return 3
    if path.startswith(("backend/database/", "deploy/database/")):
        return 3
    if path in ("backend/init_db.py",) or "migration" in path.lower() or "schema" in path.lower():
        return 3
    if path.startswith(("backend/", "dev/", "contracts/")):
        return 2
    if path in ("Makefile", "frontend/package.json", "frontend/package-lock.json", "frontend/vite.config.ts"):
        return 2
    if path.startswith(("frontend/src/api/", "frontend/src/types/")):
        return 2
    if path in ("frontend/src/utils/normalize.ts", "frontend/src/utils/normalize.test.ts"):
        return 2
    if path.startswith("frontend/"):
        return 1
    return 2


def risk_for(files):
    return max((classify(path) for path in files), default=1)


def frontend_tests(files):
    frontend = [path for path in files if path.startswith("frontend/")]
    if not frontend:
        return []
    selected = set()
    for path in frontend:
        if path.endswith(".test.ts"):
            selected.add(path.removeprefix("frontend/") if hasattr(str, "removeprefix") else path[len("frontend/"):])
        elif path.startswith("frontend/src/utils/normalize"):
            selected.add("src/utils/normalize.test.ts")
        elif path.startswith("frontend/src/api/client"):
            selected.add("src/api/client.test.ts")
        elif path.startswith(("frontend/src/api/", "frontend/src/types/")):
            selected.update(("src/api/client.test.ts", "src/utils/normalize.test.ts"))
        elif path in ("frontend/package.json", "frontend/package-lock.json", "frontend/vite.config.ts"):
            return ["ALL"]
    return sorted(selected)


def backend_tests(files):
    backend = [path for path in files if path.startswith("backend/")]
    if not backend:
        return []
    selected = set()
    for path in backend:
        if path in ("backend/pyproject.toml", "backend/uv.lock"):
            return ["ALL"]
        if path.endswith("test_core_app.py") or path in (
            "backend/run.py", "backend/web/flask_app.py", "backend/web/api/route.py",
        ):
            selected.add("web/tests/test_core_app.py")
        elif "dashboard" in path:
            selected.add("web/tests/test_dashboard_api.py")
        elif "event" in path or any(name in path for name in ("hijack", "outage", "leak")):
            selected.add("web/tests/test_events_api.py")
        elif "feature" in path:
            selected.add("web/tests/test_features_api.py")
        elif "prefix_quantity" in path:
            selected.add("web/tests/test_prefix_quantity.py")
        elif path.startswith(("backend/config/", "backend/database/", "backend/services/", "backend/web/api/")):
            return ["ALL"]
        elif path.startswith("backend/web/tests/"):
            selected.add(path[len("backend/"):])
    return sorted(selected)


def shell_files(files, all_files=False):
    if all_files:
        paths = set(ROOT.glob("deploy/**/*.sh"))
        paths.update(ROOT.glob("dev/database/*.sh"))
        paths.update(ROOT.glob("dev/backend/*.sh"))
        return sorted(str(path.relative_to(ROOT)) for path in paths)
    return sorted(path for path in files if path.endswith(".sh"))


def command_text(command):
    return " ".join(shlex.quote(part) for part in command)


def execute(label, command, cwd=ROOT):
    print("\n[检查] {}".format(label), flush=True)
    print("       {}".format(command_text(command)), flush=True)
    started = time.monotonic()
    subprocess.run(command, cwd=str(cwd), check=True)
    print("[通过] {}（{:.1f}s）".format(label, time.monotonic() - started), flush=True)


def print_risk(files):
    risk = risk_for(files)
    print("自动风险等级：{}".format(RISK_NAMES[risk]))
    if not files:
        print("改动文件：无（按 L1 日常开发默认值处理）")
        return risk
    print("改动文件：")
    for path in files:
        print("  L{}  {}".format(classify(path), path))
    return risk


def fast_checks(files):
    commands = []
    if any(path.startswith("dev/") or path == "Makefile" for path in files):
        commands.append(("开发流水线单元测试", [sys.executable, "-m", "unittest", "discover", "-s", "dev/tests", "-p", "test_*.py"], ROOT))
    if any(path.startswith("contracts/") or path == "frontend/src/types/openapi.generated.d.ts" for path in files):
        commands.append(("OpenAPI 生成类型一致性", [sys.executable, "dev/verify_openapi_types.py"], ROOT))
        commands.append(("OpenAPI 与 Flask 路由一致性", ["uv", "run", "--frozen", "pytest", "web/tests/test_openapi_contract.py"], ROOT / "backend"))

    frontend = [path for path in files if path.startswith("frontend/")]
    if frontend:
        commands.append(("前端类型检查", ["npm", "run", "typecheck"], ROOT / "frontend"))
        selected = frontend_tests(files)
        if selected == ["ALL"]:
            commands.append(("前端测试", ["npm", "test"], ROOT / "frontend"))
        elif selected:
            commands.append(("受影响前端测试", ["npm", "test", "--", *selected], ROOT / "frontend"))

    backend = [path for path in files if path.startswith("backend/")]
    if backend:
        selected = backend_tests(files)
        test_args = [] if selected == ["ALL"] or not selected else selected
        commands.append(("受影响后端测试", ["uv", "run", "--frozen", "pytest", *test_args], ROOT / "backend"))
        commands.append(("迁移核心哈希", ["sha256sum", "-c", "core.sha256"], ROOT / "backend"))

    shells = shell_files(files)
    if shells:
        commands.append(("受影响 Shell 语法", ["bash", "-n", *shells], ROOT))
    return commands


def integration_checks(files):
    commands = fast_checks(files)
    if any(path.startswith("frontend/") for path in files):
        commands = [item for item in commands if item[0] not in ("前端测试", "受影响前端测试")]
        commands.append(("前端全量测试", ["npm", "test"], ROOT / "frontend"))
        commands.append(("前端构建", ["npm", "run", "build"], ROOT / "frontend"))
    if any(path.startswith("backend/") for path in files):
        commands = [item for item in commands if item[0] != "受影响后端测试"]
        commands.append(("后端契约与集成测试", ["uv", "run", "--frozen", "pytest"], ROOT / "backend"))
    return commands


def release_checks():
    return [
        ("开发流水线单元测试", [sys.executable, "-m", "unittest", "discover", "-s", "dev/tests", "-p", "test_*.py"], ROOT),
        ("OpenAPI 生成类型一致性", [sys.executable, "dev/verify_openapi_types.py"], ROOT),
        ("前端全量测试", ["npm", "test"], ROOT / "frontend"),
        ("前端生产构建", ["npm", "run", "build"], ROOT / "frontend"),
        ("后端全量测试", ["uv", "run", "--frozen", "pytest"], ROOT / "backend"),
        ("迁移核心哈希", ["sha256sum", "-c", "core.sha256"], ROOT / "backend"),
        ("全部发布脚本语法", ["bash", "-n", *shell_files([], all_files=True)], ROOT),
    ]


def main():
    parser = argparse.ArgumentParser(description="Domeye 分层检查入口")
    parser.add_argument("ring", choices=("risk", "fast", "integration", "release"))
    parser.add_argument("--base-ref")
    args = parser.parse_args()
    files = [] if args.ring == "release" else changed_files(args.base_ref)
    if args.ring != "release":
        print_risk(files)
    if args.ring == "risk":
        return

    if args.ring == "fast":
        commands = fast_checks(files)
    elif args.ring == "integration":
        commands = integration_checks(files)
    else:
        print("发布环：执行全量本地只读校验；生产数据库恢复与切换需使用显式的 check-release-full。")
        commands = release_checks()

    if not commands:
        print("当前改动无需执行自动检查。")
        return
    started = time.monotonic()
    for label, command, cwd in commands:
        execute(label, command, cwd)
    print("\n{}完成，总耗时 {:.1f}s。".format(args.ring, time.monotonic() - started))


if __name__ == "__main__":
    main()
