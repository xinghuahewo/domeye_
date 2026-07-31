#!/usr/bin/env python3
"""只读校验 Domeye Core 当前架构文档与机器合同是否一致。"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]

CURRENT_DOCS = (
    Path("README.md"),
    Path("docs/README.md"),
    Path("docs/DomeyeCore前后端总览.md"),
    Path("frontend/README.md"),
    Path("backend/README.md"),
    Path("deploy/README.md"),
    Path("deploy/inventory/README.md"),
)

FRONTEND_ROUTE_START = "<!-- architecture-docs:frontend-routes:start -->"
FRONTEND_ROUTE_END = "<!-- architecture-docs:frontend-routes:end -->"
API_ROUTE_START = "<!-- architecture-docs:api-routes:start -->"
API_ROUTE_END = "<!-- architecture-docs:api-routes:end -->"
DOC_INDEX_START = "<!-- architecture-docs:docs-index:start -->"
DOC_INDEX_END = "<!-- architecture-docs:docs-index:end -->"

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
CURRENT_NATURES = {
    "当前架构说明",
    "当前开发说明",
    "当前流程说明",
    "当前运维说明",
}
ALLOWED_NATURES = CURRENT_NATURES | {
    "API 合同",
    "产品合同",
    "设计目标",
    "门禁说明",
    "阶段计划",
    "阶段回检",
    "历史基线",
    "验收记录",
    "风险批准记录",
    "上下文记录",
    "研究记录",
    "数据清单",
    "执行手册",
}

STALE_CLAIMS = (
    "报告接口保持关闭",
    "登录、研判、通知、报告和任务编排接口保持关闭",
    "所有接口均为 GET",
    "仅注册精简前端使用的只读 GET 接口",
    "不新增国家/AS 页面功能",
)

MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
FLASK_PARAMETER = re.compile(r"<(?:(?:[^:>]+):)?([^>]+)>")


def read_text(root: Path, relative: Path | str) -> str:
    path = root / relative
    return path.read_text(encoding="utf-8")


def marker_block(text: str, start: str, end: str, label: str) -> str:
    start_count = text.count(start)
    end_count = text.count(end)
    if start_count != 1 or end_count != 1:
        raise ValueError(
            f"{label} 标记数量无效：start={start_count}，end={end_count}"
        )
    start_index = text.index(start) + len(start)
    end_index = text.index(end, start_index)
    if start_index >= end_index:
        raise ValueError(f"{label} 标记顺序无效")
    return text[start_index:end_index]


def normalize_route_path(value: str) -> str:
    normalized = FLASK_PARAMETER.sub(r"{\1}", value.strip())
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if normalized != "/":
        normalized = normalized.rstrip("/")
    return normalized


def parse_frontend_source_routes(root: Path) -> set[str]:
    source = read_text(root, "frontend/src/router/index.ts")
    return {
        normalize_route_path(value)
        for value in re.findall(r"\bpath:\s*['\"]([^'\"]+)['\"]", source)
    }


def parse_frontend_documented_routes(root: Path) -> set[str]:
    text = read_text(root, "frontend/README.md")
    block = marker_block(
        text,
        FRONTEND_ROUTE_START,
        FRONTEND_ROUTE_END,
        "前端路由",
    )
    routes: set[str] = set()
    for line in block.splitlines():
        match = re.match(r"^\|\s*`([^`]+)`\s*\|", line)
        if match:
            routes.add(normalize_route_path(match.group(1)))
    return routes


def resource_method_map(root: Path) -> dict[str, set[str]]:
    methods: dict[str, set[str]] = {}
    for path in sorted((root / "backend/web/api").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            class_methods = {
                item.name.upper()
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name.lower() in HTTP_METHODS
            }
            if class_methods:
                methods.setdefault(node.name, set()).update(class_methods)
    return methods


def registered_routes_for_file(
    root: Path,
    relative: str,
    prefix: str,
    methods: dict[str, set[str]],
) -> set[tuple[str, str]]:
    path = root / relative
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    routes: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not isinstance(function, ast.Attribute) or function.attr != "add_resource":
            continue
        if len(node.args) < 2 or not isinstance(node.args[0], ast.Name):
            continue
        resource_name = node.args[0].id
        resource_methods = methods.get(resource_name)
        if not resource_methods:
            continue
        for argument in node.args[1:]:
            if not isinstance(argument, ast.Constant) or not isinstance(
                argument.value, str
            ):
                continue
            full_path = normalize_route_path(f"{prefix}{argument.value}")
            routes.update(
                (method, full_path) for method in sorted(resource_methods)
            )
    return routes


def parse_backend_registered_routes(root: Path) -> set[tuple[str, str]]:
    methods = resource_method_map(root)
    return (
        registered_routes_for_file(
            root,
            "backend/web/api/route.py",
            "/api/v1",
            methods,
        )
        | registered_routes_for_file(
            root,
            "backend/web/api/v2/route.py",
            "/api/v2",
            methods,
        )
    )


def parse_openapi_routes(root: Path) -> set[tuple[str, str]]:
    contract = json.loads(read_text(root, "contracts/openapi.json"))
    routes: set[tuple[str, str]] = set()
    for route, operations in contract["paths"].items():
        full_path = (
            normalize_route_path(route)
            if route.startswith("/api/v2/")
            else normalize_route_path(f"/api/v1{route}")
        )
        for method in operations:
            if method.lower() in HTTP_METHODS:
                routes.add((method.upper(), full_path))
    return routes


def parse_documented_api_routes(root: Path) -> set[tuple[str, str]]:
    text = read_text(root, "backend/README.md")
    block = marker_block(text, API_ROUTE_START, API_ROUTE_END, "后端 API")
    routes: set[tuple[str, str]] = set()
    for line in block.splitlines():
        match = re.match(
            r"^\|\s*`(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)`\s*"
            r"\|\s*`([^`]+)`\s*\|",
            line,
        )
        if match:
            routes.add((match.group(1), normalize_route_path(match.group(2))))
    return routes


def compare_sets(
    label: str,
    expected: set[object],
    actual: set[object],
) -> list[str]:
    errors: list[str] = []
    missing = sorted(expected - actual, key=str)
    extra = sorted(actual - expected, key=str)
    if missing:
        errors.append(f"{label} 缺少：{missing}")
    if extra:
        errors.append(f"{label} 多出：{extra}")
    return errors


def data_profile_errors(root: Path) -> list[str]:
    errors: list[str] = []
    profile = json.loads(read_text(root, "config/data-profile.json"))
    required = {
        "id": profile["id"],
        "timezone": profile["timezone"],
        "window_start": profile["window_start"],
        "window_end_exclusive": profile["window_end_exclusive"],
        "snapshot_time": profile["snapshot_time"],
    }
    for relative in (
        Path("docs/DomeyeCore前后端总览.md"),
        Path("frontend/README.md"),
    ):
        text = read_text(root, relative)
        for field, value in required.items():
            if str(value) not in text:
                errors.append(f"{relative} 缺少数据档字段 {field}={value}")
    if str(profile["id"]) not in read_text(root, "README.md"):
        errors.append("README.md 缺少当前数据档 ID")

    allowed_dates = {
        profile["window_start"][:10],
        profile["window_end_exclusive"][:10],
        profile["snapshot_time"][:10],
    }
    allowed_times = {
        profile["window_start"],
        profile["window_end_exclusive"],
        profile["snapshot_time"],
    }
    for relative in CURRENT_DOCS:
        text = read_text(root, relative)
        for timezone_value in re.findall(r"\bAsia/[A-Za-z_]+\b", text):
            if timezone_value != profile["timezone"]:
                errors.append(f"{relative} 包含冲突时区：{timezone_value}")
        for timestamp in re.findall(
            r"\b20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00\b",
            text,
        ):
            if timestamp not in allowed_times:
                errors.append(f"{relative} 包含冲突固定时间：{timestamp}")
        for date_value in re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", text):
            if date_value not in allowed_dates:
                errors.append(f"{relative} 包含冲突固定日期：{date_value}")
    return errors


def nginx_root(root: Path) -> str:
    text = read_text(root, "deploy/nginx/domeye-core.conf")
    sanitized = re.sub(r"#.*$", "", text, flags=re.MULTILINE)
    values = re.findall(r"(?m)^\s*root\s+([^\s;]+)\s*;", sanitized)
    if len(values) != 1:
        raise ValueError(f"Nginx root 数量不是 1：{values}")
    return values[0]


def frontend_install_target(root: Path) -> str:
    text = read_text(root, "deploy/lib/frontend-common.sh")
    runtime_match = re.search(
        r"readonly DOMEYE_CORE_FRONTEND_RUNTIME_ROOT='([^']+)'",
        text,
    )
    target_match = re.search(
        r'readonly DOMEYE_CORE_FRONTEND_TARGET='
        r'"\$\{DOMEYE_CORE_FRONTEND_RUNTIME_ROOT\}(/[^"]+)"',
        text,
    )
    if not runtime_match or not target_match:
        raise ValueError("无法解析前端安装目标")
    return f"{runtime_match.group(1)}{target_match.group(1)}"


def deployment_path_errors(root: Path) -> list[str]:
    try:
        served_root = nginx_root(root)
        install_target = frontend_install_target(root)
    except ValueError as error:
        return [str(error)]
    errors = []
    if served_root != install_target:
        errors.append(
            f"Nginx root 与前端安装目标不一致：{served_root} != {install_target}"
        )
    for relative in (
        Path("docs/DomeyeCore前后端总览.md"),
        Path("frontend/README.md"),
        Path("deploy/README.md"),
    ):
        if served_root not in read_text(root, relative):
            errors.append(f"{relative} 未记录机器合同中的前端目标：{served_root}")
    return errors


def frontend_package_errors(root: Path) -> list[str]:
    package = json.loads(read_text(root, "frontend/package.json"))
    readme = read_text(root, "frontend/README.md")
    errors: list[str] = []
    for script_name in package["scripts"]:
        command = "npm test" if script_name == "test" else f"npm run {script_name}"
        if command not in readme:
            errors.append(f"frontend/README.md 缺少 package 命令：{command}")
    dependency_labels = {
        "vue": "Vue 3",
        "vue-router": "Vue Router",
        "axios": "Axios",
        "echarts": "ECharts",
        "typescript": "TypeScript",
        "vite": "Vite",
        "vitest": "Vitest",
        "openapi-typescript": "openapi-typescript",
    }
    available = set(package["dependencies"]) | set(package["devDependencies"])
    for dependency, label in dependency_labels.items():
        if dependency in available and label not in readme:
            errors.append(f"frontend/README.md 缺少依赖说明：{label}")
    return errors


def local_link_errors(root: Path) -> tuple[list[str], int]:
    errors: list[str] = []
    checked = 0
    for relative in CURRENT_DOCS:
        document = root / relative
        text = document.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            if (
                not target
                or target.startswith("#")
                or re.match(r"^[a-z][a-z0-9+.-]*:", target, flags=re.I)
            ):
                continue
            target_path = unquote(target.split("#", 1)[0])
            if not target_path:
                continue
            checked += 1
            resolved = (document.parent / target_path).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                errors.append(f"{relative} 链接越出仓库：{raw_target}")
                continue
            if not resolved.exists():
                errors.append(f"{relative} 链接不存在：{raw_target}")
    return errors, checked


def doc_index_errors(root: Path) -> tuple[list[str], int]:
    errors: list[str] = []
    text = read_text(root, "docs/README.md")
    try:
        block = marker_block(text, DOC_INDEX_START, DOC_INDEX_END, "文档索引")
    except ValueError as error:
        return [str(error)], 0

    row_pattern = re.compile(
        r"^\|\s*\[[^\]]+\]\(([^)#]+\.md)(?:#[^)]+)?\)\s*"
        r"\|\s*`([^`]+)`\s*\|",
        flags=re.MULTILINE,
    )
    rows = [
        (Path(unquote(target)).name, nature)
        for target, nature in row_pattern.findall(block)
    ]
    indexed = Counter(filename for filename, _ in rows)
    expected = {
        path.name
        for path in (root / "docs").glob("*.md")
        if path.name != "README.md"
    }
    errors.extend(compare_sets("docs/README.md 索引", expected, set(indexed)))
    duplicates = sorted(name for name, count in indexed.items() if count != 1)
    if duplicates:
        errors.append(f"docs/README.md 索引重复：{duplicates}")

    nature_by_file = {filename: nature for filename, nature in rows}
    historical_name = re.compile(
        r"最终验收|联合验收|验收记录|阶段回检|现状基线|A0基线|"
        r"分阶段计划|阶段性任务计划|建设计划"
    )
    for filename, nature in rows:
        if nature not in ALLOWED_NATURES:
            errors.append(f"{filename} 的文档性质无效：{nature}")
        if historical_name.search(filename) and nature in CURRENT_NATURES:
            errors.append(f"历史文件不得标成当前说明：{filename} -> {nature}")
    for filename in expected:
        if filename not in nature_by_file:
            continue
        if (
            "验收" in filename
            and filename != "开发与验收流水线.md"
            and nature_by_file[filename] != "验收记录"
        ):
            errors.append(
                f"验收文件必须明确标为验收记录：{filename}"
            )
    return errors, len(indexed)


def stale_claim_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for relative in CURRENT_DOCS:
        text = read_text(root, relative)
        for claim in STALE_CLAIMS:
            if claim in text:
                errors.append(f"{relative} 仍包含失效描述：{claim}")
        release_match = re.search(
            r"\b20\d{6}T\d{6}Z-[A-Za-z0-9._-]*prod[A-Za-z0-9._-]*\b",
            text,
        )
        if release_match:
            errors.append(
                f"{relative} 把易漂移生产 release 写入当前说明："
                f"{release_match.group(0)}"
            )

    index = read_text(root, "docs/README.md")
    overview = read_text(root, "docs/DomeyeCore前后端总览.md")
    required_boundaries = (
        (index, "历史验收记录不得作为当前生产运行身份依据", "文档索引"),
        (overview, "历史验收记录不得作为当前生产运行身份依据", "架构总览"),
        (
            overview,
            "生产状态必须通过实时库存证据确认",
            "架构总览",
        ),
    )
    for text, required, label in required_boundaries:
        if required not in text:
            errors.append(f"{label} 缺少证据边界：{required}")
    return errors


def verify_repository(root: Path = ROOT) -> tuple[list[str], dict[str, int | str]]:
    errors: list[str] = []
    for relative in CURRENT_DOCS:
        if not (root / relative).is_file():
            errors.append(f"当前文档不存在：{relative}")
    if errors:
        return errors, {}

    profile = json.loads(read_text(root, "config/data-profile.json"))
    frontend_source = parse_frontend_source_routes(root)
    frontend_docs = parse_frontend_documented_routes(root)
    openapi_routes = parse_openapi_routes(root)
    registered_routes = parse_backend_registered_routes(root)
    documented_routes = parse_documented_api_routes(root)

    errors.extend(
        compare_sets("前端 README 路由", frontend_source, frontend_docs)
    )
    errors.extend(
        compare_sets("OpenAPI 与 Flask 注册路由", registered_routes, openapi_routes)
    )
    errors.extend(
        compare_sets("后端 README API", registered_routes, documented_routes)
    )
    errors.extend(data_profile_errors(root))
    errors.extend(deployment_path_errors(root))
    errors.extend(frontend_package_errors(root))
    link_errors, link_count = local_link_errors(root)
    errors.extend(link_errors)
    index_errors, indexed_count = doc_index_errors(root)
    errors.extend(index_errors)
    errors.extend(stale_claim_errors(root))

    summary: dict[str, int | str] = {
        "profile": profile["id"],
        "frontend_routes": len(frontend_source),
        "api_routes": len(registered_routes),
        "indexed_docs": indexed_count,
        "local_links": link_count,
    }
    return errors, summary


def format_errors(errors: Iterable[str]) -> str:
    return "\n".join(f"- {error}" for error in errors)


def main() -> int:
    try:
        errors, summary = verify_repository(ROOT)
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"架构文档检查失败：{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    if errors:
        print("架构文档检查失败：", file=sys.stderr)
        print(format_errors(errors), file=sys.stderr)
        return 1
    print(
        "架构文档检查通过："
        f"profile={summary['profile']}，"
        f"frontend_routes={summary['frontend_routes']}，"
        f"api_routes={summary['api_routes']}，"
        f"indexed_docs={summary['indexed_docs']}，"
        f"local_links={summary['local_links']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
