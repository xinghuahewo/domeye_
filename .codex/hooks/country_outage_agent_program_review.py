#!/usr/bin/env python3
"""配置驱动的国家中断 Agent 工程阶段防偏离回检。"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = REPOSITORY_ROOT / "config" / "agent-program"
TASK_PATH = REPOSITORY_ROOT / ".codex" / "TASK.json"
CORE_MANIFEST_PATH = REPOSITORY_ROOT / "backend" / "core.sha256"
CONFIG_SCHEMA_VERSION = "country_outage_agent_program_review_config_v1"
PROJECT_PATTERN = re.compile(r"P[0-5]")
STAGE_PATTERN = re.compile(r"S\d+")


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按工程和阶段回检国家中断 Agent 是否偏离最终效果合同。",
    )
    parser.add_argument(
        "--project",
        help="工程编号，例如 P0；与 --stage 同时提供时执行显式阶段回检。",
    )
    parser.add_argument(
        "--stage",
        help="工程内部阶段编号，例如 S0；与 --project 同时提供。",
    )
    arguments = parser.parse_args(argv)
    if bool(arguments.project) != bool(arguments.stage):
        parser.error("--project 和 --stage 必须同时提供")
    return arguments


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"无法读取文档：{path}：{error}") from error


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法读取 JSON：{path}：{error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON 必须是对象：{path}")
    return value


def safe_repository_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"工程配置缺少有效 {label}")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(f"工程配置 {label} 必须是仓库内相对路径：{value!r}")
    resolved = (REPOSITORY_ROOT / relative).resolve()
    try:
        resolved.relative_to(REPOSITORY_ROOT)
    except ValueError as error:
        raise RuntimeError(f"工程配置 {label} 越出仓库：{value!r}") from error
    return resolved


def config_path(project: str) -> Path:
    if not PROJECT_PATTERN.fullmatch(project):
        raise RuntimeError(f"工程编号必须为 P0 至 P5：{project!r}")
    return CONFIG_ROOT / f"{project}.json"


def load_project_config(project: str) -> dict[str, Any]:
    path = config_path(project)
    if not path.is_file():
        raise RuntimeError(f"工程尚未形成 Hook 配置：{path}")
    config = load_json(path)
    errors = validate_config(config, expected_project=project)
    if errors:
        raise RuntimeError("工程配置无效：" + "；".join(errors))
    return config


def requirement_ids(config: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    groups = config["requirement_groups"]
    result: dict[str, tuple[str, ...]] = {}
    for label, raw_group in groups.items():
        prefix = raw_group["prefix"]
        first = raw_group["first"]
        last = raw_group["last"]
        result[label] = tuple(
            f"{prefix}-{number:02d}" for number in range(first, last + 1)
        )
    return result


def string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_config(
    config: dict[str, Any],
    *,
    expected_project: str | None = None,
) -> list[str]:
    errors: list[str] = []
    if config.get("schema_version") != CONFIG_SCHEMA_VERSION:
        errors.append(f"schema_version 必须为 {CONFIG_SCHEMA_VERSION}")
    project = config.get("project")
    if not isinstance(project, str) or not PROJECT_PATTERN.fullmatch(project):
        errors.append("project 必须为 P0 至 P5")
    elif expected_project is not None and project != expected_project:
        errors.append(f"project 必须为 {expected_project}")

    for key in ("project_name", "acceptance_path", "plan_path"):
        if not isinstance(config.get(key), str) or not config[key]:
            errors.append(f"缺少有效 {key}")

    stages = config.get("stage_ids")
    if not string_list(stages) or not stages:
        errors.append("stage_ids 必须是非空字符串数组")
        stages = []
    elif len(set(stages)) != len(stages) or not all(
        STAGE_PATTERN.fullmatch(stage) for stage in stages
    ):
        errors.append("stage_ids 必须是唯一 S 编号")

    stage_names = config.get("stage_names")
    if not isinstance(stage_names, dict) or set(stage_names) != set(stages):
        errors.append("stage_names 必须完整覆盖 stage_ids")
    elif not all(isinstance(value, str) and value for value in stage_names.values()):
        errors.append("stage_names 的值必须是非空字符串")

    groups = config.get("requirement_groups")
    expected_by_group: dict[str, set[str]] = {}
    if not isinstance(groups, dict) or not groups:
        errors.append("requirement_groups 必须是非空对象")
        groups = {}
    else:
        seen_prefixes: set[str] = set()
        for label, raw_group in groups.items():
            if not isinstance(label, str) or not isinstance(raw_group, dict):
                errors.append("requirement_groups 的标签和值必须有效")
                continue
            prefix = raw_group.get("prefix")
            first = raw_group.get("first")
            last = raw_group.get("last")
            if (
                not isinstance(prefix, str)
                or not prefix
                or prefix in seen_prefixes
                or not isinstance(first, int)
                or not isinstance(last, int)
                or first < 1
                or last < first
            ):
                errors.append(f"requirement_groups.{label} 范围无效")
                continue
            seen_prefixes.add(prefix)
            expected_by_group[label] = {
                f"{prefix}-{number:02d}" for number in range(first, last + 1)
            }

    due = config.get("stage_due")
    if not isinstance(due, dict) or set(due) != set(stages):
        errors.append("stage_due 必须完整覆盖 stage_ids")
        due = {}
    mapped: set[str] = set()
    for stage in stages:
        raw_stage = due.get(stage)
        if not isinstance(raw_stage, dict) or set(raw_stage) != set(expected_by_group):
            errors.append(f"stage_due.{stage} 必须完整覆盖 requirement_groups")
            continue
        for label, allowed_ids in expected_by_group.items():
            values = raw_stage.get(label)
            if not string_list(values):
                errors.append(f"stage_due.{stage}.{label} 必须是字符串数组")
                continue
            unexpected = set(values) - allowed_ids
            if unexpected:
                errors.append(
                    f"stage_due.{stage}.{label} 包含未知要求：{sorted(unexpected)}"
                )
            mapped.update(values)
    all_expected = set().union(*expected_by_group.values()) if expected_by_group else set()
    if all_expected - mapped:
        errors.append(f"存在未映射最终要求：{sorted(all_expected - mapped)}")

    for key in (
        "stage_subheadings",
        "required_acceptance_phrases",
        "required_plan_phrases",
        "forbidden_acceptance_headings",
        "forbidden_plan_headings",
        "review_questions",
    ):
        if not string_list(config.get(key)):
            errors.append(f"{key} 必须是字符串数组")
    if not isinstance(config.get("final_response_template"), str) or "{stage}" not in config.get(
        "final_response_template", ""
    ):
        errors.append("final_response_template 必须包含 {stage}")
    return errors


def text_covers_requirement_id(text: str, requirement_id: str) -> bool:
    if requirement_id in text:
        return True
    prefix, raw_target = requirement_id.rsplit("-", maxsplit=1)
    target = int(raw_target)
    for start, end in re.findall(
        rf"{re.escape(prefix)}-(\d{{2}}) 至 {re.escape(prefix)}-(\d{{2}})",
        text,
    ):
        if int(start) <= target <= int(end):
            return True
    return False


def stage_body(config: dict[str, Any], plan: str, stage: str) -> str | None:
    stage_alternatives = "|".join(re.escape(value) for value in config["stage_ids"])
    match = re.search(
        rf"^### {re.escape(stage)}：[^\n]+\n(?P<body>.*?)(?=^### (?:{stage_alternatives})：|^## 五、)",
        plan,
        re.MULTILINE | re.DOTALL,
    )
    return match.group("body") if match else None


def validate_document_texts(
    config: dict[str, Any],
    acceptance: str,
    plan: str,
) -> list[str]:
    errors: list[str] = []
    expected_groups = requirement_ids(config)
    raw_groups = config["requirement_groups"]
    for label, expected in expected_groups.items():
        prefix = raw_groups[label]["prefix"]
        found = tuple(
            f"{prefix}-{value}"
            for value in re.findall(
                rf"^### {re.escape(prefix)}-(\d{{2}})：",
                acceptance,
                re.MULTILINE,
            )
        )
        if found != expected:
            errors.append(
                f"{label}最终要求必须且只能按 {expected[0]} 至 {expected[-1]} 顺序出现；"
                "当前为：" + (", ".join(found) or "无")
            )

    stage_alternatives = "|".join(re.escape(value) for value in config["stage_ids"])
    found_stages = tuple(
        re.findall(rf"^### ({stage_alternatives})：", plan, re.MULTILINE)
    )
    if found_stages != tuple(config["stage_ids"]):
        errors.append(
            "分阶段计划阶段必须且只能按配置顺序出现；当前为："
            + (", ".join(found_stages) or "无")
        )

    for stage in config["stage_ids"]:
        body = stage_body(config, plan, stage)
        if body is None:
            errors.append(f"分阶段计划缺少 {stage} 完整正文")
            continue
        for heading in config["stage_subheadings"]:
            count = body.count(heading)
            if count != 1:
                errors.append(
                    f"{stage} 必须且只能包含一个 `{heading}`；当前数量为 {count}"
                )
        for label, due_ids in config["stage_due"][stage].items():
            for requirement_id in due_ids:
                if not text_covers_requirement_id(body, requirement_id):
                    errors.append(
                        f"分阶段计划 {stage} 缺少到期{label}映射：{requirement_id}"
                    )

    for phrase in config["required_acceptance_phrases"]:
        if phrase not in acceptance:
            errors.append(f"最终验收文档缺少防偏离语义：{phrase}")
    for phrase in config["required_plan_phrases"]:
        if phrase not in plan:
            errors.append(f"分阶段计划缺少阶段封口语义：{phrase}")
    for heading in config["forbidden_acceptance_headings"]:
        if heading in acceptance:
            errors.append(f"最终验收文档越过最终效果：出现 `{heading}`")
    for heading in config["forbidden_plan_headings"]:
        if heading in plan:
            errors.append(f"分阶段计划越过头尾和效果边界：出现 `{heading}`")

    acceptance_name = Path(config["acceptance_path"]).name
    if acceptance_name not in plan:
        errors.append("分阶段计划没有引用本工程最终验收文档")
    return errors


def validate_documents(config: dict[str, Any]) -> list[str]:
    try:
        acceptance_path = safe_repository_path(config["acceptance_path"], "acceptance_path")
        plan_path = safe_repository_path(config["plan_path"], "plan_path")
    except RuntimeError as error:
        return [str(error)]
    errors: list[str] = []
    for path, label in (
        (acceptance_path, "最终验收文档"),
        (plan_path, "分阶段计划"),
    ):
        if not path.is_file():
            errors.append(f"{label}不存在：{path}")
    if errors:
        return errors
    try:
        return validate_document_texts(config, read_text(acceptance_path), read_text(plan_path))
    except RuntimeError as error:
        return [str(error)]


def path_matches(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def git_output(arguments: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", *arguments],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"无法执行 git {' '.join(arguments)}：{error}") from error
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()
        raise RuntimeError(
            f"git {' '.join(arguments)} 失败：{detail or '无错误详情'}"
        )
    return result.stdout


def changed_paths(base_commit: str) -> set[str]:
    paths: set[str] = set()
    for arguments in (
        ("diff", "--name-only", f"{base_commit}...HEAD"),
        ("diff", "--name-only"),
        ("diff", "--cached", "--name-only"),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        for line in git_output(arguments).splitlines():
            normalized = line.strip()
            if normalized:
                paths.add(normalized)
    return paths


def validate_task_boundary() -> list[str]:
    if not TASK_PATH.is_file():
        return [f"当前工作树缺少任务合同：{TASK_PATH}"]
    try:
        task = load_json(TASK_PATH)
    except RuntimeError as error:
        return [str(error)]

    errors: list[str] = []
    if task.get("worktreeRoot") != str(REPOSITORY_ROOT):
        errors.append(
            "TASK.json worktreeRoot 与 Hook 工作树不一致："
            f"{task.get('worktreeRoot')!r} != {str(REPOSITORY_ROOT)!r}"
        )
    try:
        current_branch = git_output(("branch", "--show-current")).strip()
    except RuntimeError as error:
        errors.append(str(error))
        current_branch = ""
    if task.get("targetBranch") != current_branch:
        errors.append(
            "TASK.json targetBranch 与当前分支不一致："
            f"{task.get('targetBranch')!r} != {current_branch!r}"
        )

    base_commit = task.get("baseCommit")
    allowed = task.get("allowedPaths")
    forbidden = task.get("forbiddenPaths")
    if not isinstance(base_commit, str) or not base_commit:
        errors.append("TASK.json 缺少有效 baseCommit")
    if not string_list(allowed):
        errors.append("TASK.json allowedPaths 必须是字符串数组")
    if not string_list(forbidden):
        errors.append("TASK.json forbiddenPaths 必须是字符串数组")
    if errors:
        return errors

    assert isinstance(base_commit, str)
    assert isinstance(allowed, list)
    assert isinstance(forbidden, list)
    try:
        paths = changed_paths(base_commit)
    except RuntimeError as error:
        return [str(error)]
    for path in sorted(paths):
        if path == ".codex/TASK.json":
            continue
        if path_matches(path, forbidden):
            errors.append(f"当前改动命中禁止路径：{path}")
        if not path_matches(path, allowed):
            errors.append(f"当前改动超出 TASK.json allowedPaths：{path}")
    return errors


def frozen_core_warnings() -> list[str]:
    if not CORE_MANIFEST_PATH.is_file():
        return [f"未找到核心哈希清单：{CORE_MANIFEST_PATH}"]
    try:
        changed = git_output(("status", "--porcelain", "--", "backend/core"))
    except RuntimeError as error:
        return [str(error)]
    return ["`backend/core/` 存在工作树改动"] if changed.strip() else []


def due_summary(config: dict[str, Any], stage: str) -> str:
    parts: list[str] = []
    for label, values in config["stage_due"][stage].items():
        parts.append(f"{label}：" + ("、".join(values) if values else "无新增到期项"))
    return "；".join(parts)


def review_reason(config: dict[str, Any], stage: str) -> str:
    acceptance_path = safe_repository_path(config["acceptance_path"], "acceptance_path")
    plan_path = safe_repository_path(config["plan_path"], "plan_path")
    config_file = config_path(config["project"])
    questions = "\n".join(
        f"{index}. {question}；"
        for index, question in enumerate(config["review_questions"], start=1)
    )
    warnings = frozen_core_warnings()
    warning_text = ""
    if warnings:
        warning_text = "\n\n机检发现的冻结核心偏离信号：\n- " + "\n- ".join(warnings)
    final_line = config["final_response_template"].format(stage=stage)
    return f"""国家中断 Agent {config['project']} 阶段结束回检：{stage}「{config['stage_names'][stage]}」

请完整重新阅读：
1. {acceptance_path}
2. {plan_path}
3. {config_file}

本阶段到期或必须继续保持可达的要求：{due_summary(config, stage)}。

结束本阶段前，必须依据实际结果逐项判断：
{questions}

判定规则：
- 一致：本阶段全部到期出口成立，未发现偏离；
- 已修正：偏离已在本阶段授权范围内完成修正；
- 无影响：本任务与该工程无关，且没有改变最终效果可达性；
- 存在待处理偏离：任一到期出口失败、阻塞或证据不足。必须列出要求编号、偏离位置和原因，不得宣告阶段完成。

Hook 机检只覆盖配置、合同结构、阶段映射、当前 TASK.json 路径边界和冻结核心偏离信号，不代表验收案例、参考真值、基线执行、评测、产品、模型、浏览器、部署或生产效果已通过。

最终答复必须包含一行：
“{final_line}”。{warning_text}"""


def machine_errors(config: dict[str, Any]) -> list[str]:
    return validate_documents(config) + validate_task_boundary()


def run_explicit_review(project: str, stage: str) -> int:
    try:
        config = load_project_config(project)
    except RuntimeError as error:
        sys.stderr.write(f"国家中断 Agent 计划 Hook：{error}\n")
        return 1
    if stage not in config["stage_ids"]:
        sys.stderr.write(
            f"国家中断 Agent 计划 Hook：{project} 不支持阶段 {stage}；"
            f"允许：{', '.join(config['stage_ids'])}\n"
        )
        return 2
    errors = machine_errors(config)
    if errors:
        sys.stderr.write("国家中断 Agent 计划 Hook：机器检查失败\n")
        for error in errors:
            sys.stderr.write(f"- {error}\n")
        return 1
    sys.stdout.write(review_reason(config, stage))
    sys.stdout.write("\n")
    return 0


def load_hook_input() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(f"未收到有效的 Codex Hook 输入：{error}") from error
    if not isinstance(value, dict):
        raise RuntimeError("Codex Hook 输入必须是 JSON 对象")
    return value


def run_stop_hook() -> int:
    try:
        hook_input = load_hook_input()
    except RuntimeError as error:
        emit(
            {
                "continue": True,
                "systemMessage": (
                    f"国家中断 Agent 计划 Hook 无法执行：{error}。"
                    "请人工回读当前工程最终验收文档和分阶段计划。"
                ),
            }
        )
        return 0
    if hook_input.get("hook_event_name") != "Stop":
        emit({})
        return 0
    if hook_input.get("stop_hook_active") is True:
        emit({})
        return 0

    project = os.environ.get("DOMEYE_COUNTRY_OUTAGE_AGENT_PROJECT")
    stage = os.environ.get("DOMEYE_COUNTRY_OUTAGE_AGENT_STAGE")
    if project is None and stage is None:
        emit({})
        return 0
    if not project or not stage:
        emit(
            {
                "decision": "block",
                "reason": (
                    "国家中断 Agent 计划 Hook 已被部分声明。请同时设置 "
                    "DOMEYE_COUNTRY_OUTAGE_AGENT_PROJECT 和 "
                    "DOMEYE_COUNTRY_OUTAGE_AGENT_STAGE。"
                ),
            }
        )
        return 0
    try:
        config = load_project_config(project)
    except RuntimeError as error:
        emit({"decision": "block", "reason": str(error)})
        return 0
    if stage not in config["stage_ids"]:
        emit(
            {
                "decision": "block",
                "reason": (
                    f"{project} 不支持阶段 {stage}；"
                    f"允许：{', '.join(config['stage_ids'])}。"
                ),
            }
        )
        return 0
    errors = machine_errors(config)
    if errors:
        emit(
            {
                "decision": "block",
                "reason": (
                    "国家中断 Agent 计划 Hook 机器检查失败：\n- "
                    + "\n- ".join(errors)
                    + "\n请先修正合同结构或任务边界，再结束阶段。"
                ),
            }
        )
        return 0
    emit({"decision": "block", "reason": review_reason(config, stage)})
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    if arguments.project and arguments.stage:
        return run_explicit_review(arguments.project, arguments.stage)
    return run_stop_hook()


if __name__ == "__main__":
    raise SystemExit(main())
