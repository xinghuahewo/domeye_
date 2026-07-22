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
RISK_CHECK_MATRIX = {
    0: ("check-fast",),
    1: ("check-fast",),
    2: ("check-fast", "check-integration"),
    3: ("check-fast", "check-integration", "check-release"),
}
STATEFUL_BOUNDARY_FLAGS = {
    "core-baseline",
    "data-range",
    "database-lifecycle",
    "deployment-switch",
    "migration-baseline",
    "nginx-switch",
    "production-env",
    "security-config",
}
P0_QUALITY_GATE_PATHS = {
    "Makefile",
    "backend/data_pipeline/normalize/__init__.py",
    "backend/data_pipeline/normalize/facts.py",
    "backend/data_pipeline/quality/__init__.py",
    "backend/data_pipeline/quality/gate.py",
    "backend/data_pipeline/route_event/artifacts.py",
    "config/data-profile.json",
    "contracts/data/data-quality-report.schema.json",
    "dev/checks.py",
    "dev/data_quality/p0_artifact_manifest.py",
    "dev/data_quality/p0_normalize_candidate.py",
    "dev/data_quality/p0_quality_gate.py",
    "dev/data_quality/p0_reproducibility.py",
    "dev/tests/test_checks.py",
    "dev/tests/test_p0_quality_gate.py",
    "dev/tests/test_p0_quality_gate_cli.py",
    "dev/tests/test_p0_reproducibility.py",
}


class ClassificationError(RuntimeError):
    """改动路径无法被现有风险规则可靠分类。"""


def git_output(*arguments, text=True):
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", *arguments],
        cwd=str(ROOT),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
    )
    return result.stdout


def git_lines(*arguments):
    return [line.strip() for line in git_output(*arguments).splitlines() if line.strip()]


def parse_name_status(output):
    """解析 `git diff --name-status -z`，重命名同时返回新旧路径。"""

    tokens = output.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    paths = []
    index = 0
    while index < len(tokens):
        status = tokens[index].decode("ascii", errors="strict")
        index += 1
        path_count = 2 if status.startswith(("R", "C")) else 1
        if not status or index + path_count > len(tokens):
            raise ClassificationError("Git name-status 输出不完整，风险分类失败关闭")
        for _ in range(path_count):
            paths.append(tokens[index].decode("utf-8", errors="strict"))
            index += 1
    return paths


def diff_paths(*arguments):
    return parse_name_status(
        git_output("diff", "--name-status", "-z", "--find-renames", *arguments, text=False)
    )


def changed_files(base_ref=None):
    files = set()
    if base_ref:
        merge_bases = git_lines("merge-base", base_ref, "HEAD")
        if len(merge_bases) != 1:
            raise ClassificationError("无法为 {} 确定唯一 merge base".format(base_ref))
        files.update(diff_paths("{}...HEAD".format(merge_bases[0])))
    files.update(diff_paths())
    files.update(diff_paths("--cached"))
    files.update(git_lines("ls-files", "--others", "--exclude-standard"))
    return sorted(
        path for path in files
        if not path.startswith(IGNORED_PREFIXES)
    )


def classify(path):
    if not path or path.startswith("/") or ".." in Path(path).parts:
        raise ClassificationError("改动路径格式无效：{}".format(path))
    if path in ("Makefile", "dev/checks.py") or path.startswith(".github/workflows/"):
        return 3
    if path == "config/data-profile.json":
        return 3
    if path.startswith("config/research/") and path.endswith(".json"):
        return 3
    if path == "config/performance-budget.json":
        return 2
    if path == "openspec/config.yaml":
        return 2
    if path in ("openspec/changes/archive/.gitkeep", "openspec/specs/.gitkeep"):
        return 0
    if path == "backend/core.sha256":
        return 3
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
    if path in ("frontend/package.json", "frontend/package-lock.json", "frontend/vite.config.ts"):
        return 2
    if path.startswith(("frontend/src/api/", "frontend/src/types/")):
        return 2
    if path in ("frontend/src/utils/normalize.ts", "frontend/src/utils/normalize.test.ts"):
        return 2
    if path.startswith("frontend/"):
        return 1
    if path in (".gitignore", ".python-version"):
        return 2
    raise ClassificationError("未识别文件没有风险规则：{}".format(path))


def boundary_flags(path):
    flags = set()
    lower_path = path.lower()
    if path in ("Makefile", "dev/checks.py") or path.startswith(".github/workflows/"):
        flags.add("pipeline-rules")
    if path in ("backend/pyproject.toml", "backend/uv.lock", "frontend/package-lock.json"):
        flags.add("dependency-lock")
    if path == "config/data-profile.json" or path in (
        "backend/config/data_window.py",
        "deploy/lib/data-profile.sh",
    ):
        flags.add("data-range")
    if path == "backend/core.sha256" or path.startswith("backend/core/"):
        flags.add("core-baseline")
    if path.startswith("backend/database/"):
        flags.add("db-access")
    if path.startswith(("dev/database/", "deploy/database/")):
        flags.add("database-lifecycle")
    if path == "backend/init_db.py" or "migration" in lower_path or "schema" in lower_path:
        flags.add("migration-baseline")
    if path.startswith("deploy/nginx/"):
        flags.update(("deployment-switch", "nginx-switch"))
    if path.startswith("deploy/release/"):
        flags.add("deployment-switch")
    if path in (
        "deploy/start-backend.sh",
        "deploy/stop-backend.sh",
        "deploy/manage-fixed-backend.sh",
        "deploy/acceptance/full-acceptance.sh",
        "deploy/database/activate-database.sh",
        "deploy/database/configure-backend-env.sh",
        "deploy/database/rollback-database.sh",
    ):
        flags.add("deployment-switch")
    if path.endswith((".env", ".env.example")) or "source.env" in lower_path:
        flags.update(("production-env", "security-config"))
    if path.startswith("deploy/lib/"):
        flags.add("deployment-switch")
    return flags


def risk_for(files):
    return max((classify(path) for path in files), default=1)


def risk_summary(files):
    risk = risk_for(files)
    flags = set()
    for path in files:
        flags.update(boundary_flags(path))
    return {
        "risk": risk,
        "flags": tuple(sorted(flags)),
        "required_checks": RISK_CHECK_MATRIX[risk],
        "stateful": bool(flags & STATEFUL_BOUNDARY_FLAGS),
    }


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
    summary = risk_summary(files)
    risk = summary["risk"]
    print("RISK_LEVEL=L{}".format(risk))
    print("BOUNDARY_FLAGS={}".format(",".join(summary["flags"]) or "none"))
    print("REQUIRED_CHECKS={}".format(",".join(summary["required_checks"])))
    print("STATEFUL_RELEASE_REQUIRED={}".format("yes" if summary["stateful"] else "no"))
    print("自动风险等级：{}".format(RISK_NAMES[risk]))
    if not files:
        print("改动文件：无（按 L1 日常开发默认值处理）")
        return risk
    print("改动文件：")
    for path in files:
        flags = ",".join(sorted(boundary_flags(path))) or "none"
        print("  L{}  {}  [{}]".format(classify(path), path, flags))
    return risk


def fast_checks(files):
    commands = []
    if any(path in (
        "config/data-profile.json",
        "dev/data_profile.py",
        "dev/verify_data_profile.py",
        "dev/run_local.py",
        "dev/fixtures/api-snapshot.json",
        "deploy/lib/data-profile.sh",
        "deploy/lib/artifact-common.sh",
        "deploy/build-fixed-frontend.sh",
    ) for path in files):
        commands.append(("唯一数据档一致性", [sys.executable, "dev/verify_data_profile.py"], ROOT))
    if any(path.startswith("dev/") or path == "Makefile" for path in files):
        commands.append(("开发流水线单元测试", [sys.executable, "-m", "unittest", "discover", "-s", "dev/tests", "-p", "test_*.py"], ROOT))
    if any(
        path.startswith("contracts/data/")
        or path in (
            "dev/verify_p0_contracts.py",
            "dev/data_quality/validate_p0_contracts.cjs",
        )
        for path in files
    ):
        commands.append(("P0 数据合同", [sys.executable, "dev/verify_p0_contracts.py"], ROOT))
    if any(path == "contracts/openapi.json" or path == "frontend/src/types/openapi.generated.d.ts" for path in files):
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
    if any(path in P0_QUALITY_GATE_PATHS for path in files):
        commands.append(
            (
                "P0 数据质量门禁离线 fixture",
                ["make", "check-data-p0", "P0_QUALITY_FIXTURE=1"],
                ROOT,
            )
        )
    return commands


def release_checks():
    return [
        ("唯一数据档一致性", [sys.executable, "dev/verify_data_profile.py"], ROOT),
        ("开发流水线单元测试", [sys.executable, "-m", "unittest", "discover", "-s", "dev/tests", "-p", "test_*.py"], ROOT),
        ("P0 数据合同", [sys.executable, "dev/verify_p0_contracts.py"], ROOT),
        ("P0 数据质量门禁离线 fixture", ["make", "check-data-p0", "P0_QUALITY_FIXTURE=1"], ROOT),
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
    try:
        files = [] if args.ring == "release" else changed_files(args.base_ref)
        if args.ring != "release":
            print_risk(files)
    except (ClassificationError, subprocess.CalledProcessError, UnicodeError) as error:
        print("风险分类失败关闭：{}".format(error), file=sys.stderr)
        raise SystemExit(2)
    if args.ring == "risk":
        return

    if args.ring == "fast":
        commands = fast_checks(files)
    elif args.ring == "integration":
        commands = integration_checks(files)
    else:
        print("发布环：只执行全量本地只读校验；该命令不会恢复数据库或切换生产服务。")
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
