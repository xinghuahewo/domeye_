#!/usr/bin/env python3
"""阶段结束时回检国家中断调查 Agent 是否偏离最终效果合同。"""

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
ACCEPTANCE_PATH = REPOSITORY_ROOT / "docs" / "国家中断调查Agent最终验收文档.md"
PLAN_PATH = REPOSITORY_ROOT / "docs" / "国家中断调查Agent分阶段计划.md"
TASK_PATH = REPOSITORY_ROOT / ".codex" / "TASK.json"
CORE_MANIFEST_PATH = REPOSITORY_ROOT / "backend" / "core.sha256"

STAGE_IDS = tuple(f"I{index}" for index in range(6))
FRONTEND_IDS = tuple(f"IFE-{index:02d}" for index in range(1, 11))
BACKEND_IDS = tuple(f"IBE-{index:02d}" for index in range(1, 14))
SCENARIO_IDS = tuple(f"ISE-{index:02d}" for index in range(1, 13))

STAGE_NAMES = {
    "I0": "最终效果、问题范围和阶段映射冻结",
    "I1": "事件绑定的基础聊天与直接事实问答闭合",
    "I2": "问题澄清、多目标理解与调查路由闭合",
    "I3": "组合分析、证据图和同事实回答闭合",
    "I4": "有限观察假设与人工确认闭合",
    "I5": "前后端联合旅程、可信度和最终效果验收",
}

STAGE_DUE_FRONTEND = {
    "I0": FRONTEND_IDS,
    "I1": ("IFE-01", "IFE-02", "IFE-04", "IFE-05", "IFE-06"),
    "I2": ("IFE-03", "IFE-08", "IFE-09"),
    "I3": ("IFE-02", "IFE-04", "IFE-05"),
    "I4": ("IFE-04", "IFE-07"),
    "I5": FRONTEND_IDS,
}
STAGE_DUE_BACKEND = {
    "I0": BACKEND_IDS,
    "I1": ("IBE-01", "IBE-04", "IBE-08", "IBE-09", "IBE-10"),
    "I2": ("IBE-02", "IBE-03", "IBE-10"),
    "I3": ("IBE-05", "IBE-06", "IBE-08", "IBE-12", "IBE-13"),
    "I4": ("IBE-06", "IBE-07", "IBE-08", "IBE-09"),
    "I5": BACKEND_IDS,
}
STAGE_DUE_SCENARIOS = {
    "I0": SCENARIO_IDS,
    "I1": ("ISE-01", "ISE-04", "ISE-06", "ISE-07"),
    "I2": ("ISE-02", "ISE-05", "ISE-09"),
    "I3": ("ISE-02", "ISE-03", "ISE-06", "ISE-10"),
    "I4": ("ISE-07", "ISE-08"),
    "I5": SCENARIO_IDS,
}

REQUIRED_ACCEPTANCE_PHRASES = (
    "本文只设计最终效果",
    "从始至终只使用 RRC25",
    "聊天式调查工作台",
    "可回答”“部分回答”“证据不足”或“超出范围",
    "有限假设",
    "Evidence Graph",
    "模型只组织经过校验的事实和边界",
    "正确弃答率为 100%",
    "不得称为 Network RCA Agent",
    "Hook 只",
)

REQUIRED_PLAN_PHRASES = (
    "只定义阶段入口、前端效果、后端效果、阶段出口与边界",
    "从始至终只使用 RRC25",
    "未达到当前阶段出口时不得进入下一阶段",
    "每个阶段结束必须调用国家中断调查 Agent 最终验收防偏离 Hook",
    "Hook 结构检查通过不等于",
    "I5 Hook 给出最终“一致”或“已修正”结论",
    "python3 .codex/hooks/country_outage_investigation_agent_review.py --stage I0",
)


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="回检国家中断调查 Agent 是否偏离最终效果合同。",
    )
    parser.add_argument(
        "--stage",
        choices=STAGE_IDS,
        help="显式阶段结束回检；省略时作为 Codex Stop Hook 运行。",
    )
    return parser.parse_args(argv)


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


def text_covers_requirement_id(text: str, requirement_id: str) -> bool:
    if requirement_id in text:
        return True
    prefix, raw_target = requirement_id.split("-", maxsplit=1)
    target = int(raw_target)
    for start, end in re.findall(
        rf"{re.escape(prefix)}-(\d{{2}}) 至 {re.escape(prefix)}-(\d{{2}})",
        text,
    ):
        if int(start) <= target <= int(end):
            return True
    return False


def stage_body(plan: str, stage: str) -> str | None:
    match = re.search(
        rf"^### {stage}：[^\n]+\n(?P<body>.*?)(?=^### I[0-5]：|^## 五、)",
        plan,
        re.MULTILINE | re.DOTALL,
    )
    return match.group("body") if match else None


def validate_document_texts(acceptance: str, plan: str) -> list[str]:
    """只检查合同结构、阶段映射和防偏离语义。"""
    errors: list[str] = []

    found_frontend = tuple(
        f"IFE-{value}"
        for value in re.findall(r"^### IFE-(\d{2})：", acceptance, re.MULTILINE)
    )
    if found_frontend != FRONTEND_IDS:
        errors.append(
            "前端最终效果必须且只能按 IFE-01 至 IFE-10 顺序出现；当前为："
            + (", ".join(found_frontend) or "无")
        )

    found_backend = tuple(
        f"IBE-{value}"
        for value in re.findall(r"^### IBE-(\d{2})：", acceptance, re.MULTILINE)
    )
    if found_backend != BACKEND_IDS:
        errors.append(
            "后端最终效果必须且只能按 IBE-01 至 IBE-13 顺序出现；当前为："
            + (", ".join(found_backend) or "无")
        )

    found_scenarios = tuple(
        f"ISE-{value}"
        for value in re.findall(r"^### ISE-(\d{2})：", acceptance, re.MULTILINE)
    )
    if found_scenarios != SCENARIO_IDS:
        errors.append(
            "联合场景必须且只能按 ISE-01 至 ISE-12 顺序出现；当前为："
            + (", ".join(found_scenarios) or "无")
        )

    found_stages = tuple(
        f"I{value}"
        for value in re.findall(r"^### I([0-5])：", plan, re.MULTILINE)
    )
    if found_stages != STAGE_IDS:
        errors.append(
            "分阶段计划必须且只能按 I0 至 I5 顺序出现；当前为："
            + (", ".join(found_stages) or "无")
        )

    expected_stage_headings = (
        "#### 入口",
        "#### 前端效果",
        "#### 后端效果",
        "#### 出口",
        "#### 边界",
    )
    for heading in expected_stage_headings:
        count = plan.count(heading)
        if count != len(STAGE_IDS):
            errors.append(
                f"每个阶段必须且只能包含一个 `{heading}`；当前数量为 {count}。"
            )

    for heading in (
        "#### 实施步骤",
        "#### 任务清单",
        "#### 具体做法",
        "#### 技术方案",
        "#### 文件改动",
    ):
        if heading in plan:
            errors.append(f"分阶段计划越过头尾、前后端效果和边界：出现 `{heading}`。")

    for heading in (
        "## 技术栈",
        "## 实施方案",
        "## 代码结构",
        "## 开发步骤",
    ):
        if heading in acceptance:
            errors.append(f"最终验收文档越过最终效果：出现 `{heading}`。")

    for phrase in REQUIRED_ACCEPTANCE_PHRASES:
        if phrase not in acceptance:
            errors.append(f"最终验收文档缺少防偏离语义：{phrase}")
    for phrase in REQUIRED_PLAN_PHRASES:
        if phrase not in plan:
            errors.append(f"分阶段计划缺少阶段封口语义：{phrase}")

    if ACCEPTANCE_PATH.name not in plan:
        errors.append("分阶段计划没有引用最终验收文档。")

    due_groups = (
        ("前端", STAGE_DUE_FRONTEND),
        ("后端", STAGE_DUE_BACKEND),
        ("联合场景", STAGE_DUE_SCENARIOS),
    )
    for stage in STAGE_IDS:
        body = stage_body(plan, stage)
        if body is None:
            errors.append(f"分阶段计划缺少 {stage} 完整正文。")
            continue
        for label, due_map in due_groups:
            for requirement_id in due_map[stage]:
                if not text_covers_requirement_id(body, requirement_id):
                    errors.append(
                        f"分阶段计划 {stage} 缺少到期{label}映射：{requirement_id}"
                    )
    return errors


def validate_documents() -> list[str]:
    errors: list[str] = []
    for path, label in (
        (ACCEPTANCE_PATH, "最终验收文档"),
        (PLAN_PATH, "分阶段计划文档"),
    ):
        if not path.is_file():
            errors.append(f"{label}不存在：{path}")
    if errors:
        return errors
    try:
        return validate_document_texts(read_text(ACCEPTANCE_PATH), read_text(PLAN_PATH))
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
        errors.append("TASK.json 缺少有效 baseCommit。")
    if not isinstance(allowed, list) or not all(
        isinstance(value, str) for value in allowed
    ):
        errors.append("TASK.json allowedPaths 必须是字符串数组。")
    if not isinstance(forbidden, list) or not all(
        isinstance(value, str) for value in forbidden
    ):
        errors.append("TASK.json forbiddenPaths 必须是字符串数组。")
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
    warnings: list[str] = []
    if not CORE_MANIFEST_PATH.is_file():
        return [f"未找到核心哈希清单：{CORE_MANIFEST_PATH}"]
    try:
        changed = git_output(("status", "--porcelain", "--", "backend/core"))
    except RuntimeError as error:
        return [str(error)]
    if changed.strip():
        warnings.append("`backend/core/` 存在工作树改动。")
    return warnings


def due_requirement_summary(stage: str) -> str:
    return "；".join(
        (
            "前端：" + "、".join(STAGE_DUE_FRONTEND[stage]),
            "后端：" + "、".join(STAGE_DUE_BACKEND[stage]),
            "联合场景：" + "、".join(STAGE_DUE_SCENARIOS[stage]),
        )
    )


def review_reason(stage: str) -> str:
    warnings = frozen_core_warnings()
    warning_text = ""
    if warnings:
        warning_text = "\n\n机检发现的冻结核心偏离信号：\n- " + "\n- ".join(warnings)
    return f"""国家中断调查 Agent 阶段结束回检：{stage}「{STAGE_NAMES[stage]}」

请完整重新阅读：
1. {ACCEPTANCE_PATH}
2. {PLAN_PATH}

本阶段到期或必须继续保持可达的要求：{due_requirement_summary(stage)}。

结束本阶段前，必须依据实际结果逐项判断：
1. 本阶段入口是否真实成立，前端效果、后端效果、出口和到期 IFE、IBE、ISE 是否具有同一候选身份的可复核证据；
2. 事件、国家、唯一 RRC25、publication、revision、cohort、窗口、data_through 和 finality 是否始终唯一绑定；
3. 单一或组合问题是否正确识别对象、多个分析目标、可回答程度与必要澄清；低置信度是否被误写成确定理解；
4. 调查是否只使用当前 capability 允许的只读事实和白名单分析；是否出现外部网络、数据库直读、生产操作或无界调用；
5. 关键数字、时间点、排序、地址族差异和时间对应是否由确定性结果提供；缺失是否补零、连线或跨缺口计算；
6. 每个 Claim 是否有同快照 Evidence 和 Limitation；Unknown 是否保持未知；页面、报告、趋势和调查回答是否共享同一事实语义；
7. 有限假设是否经过用户确认并同时展示支持、反对和缺失证据；是否被升级为全国断网、用户影响、原因、责任或 RCA 结论；
8. 模型是否只组织已校验事实，是否改写数字、方向、可回答性、假设状态或未知，是否暴露思考过程；
9. 多轮指代、事件切换、新 publication、correction 和会话到期是否清楚分隔，是否混用旧事实或其他用户状态；
10. 工具、模型、校验、连接、超时、取消和权限失败是否关闭；组合问题的局部失败是否被伪装成整体成功；
11. 聊天页是否以结论、关键事实、证据、限制和未知呈现，是否能完成证据下钻、取消、重连、桌面、窄屏和键盘旅程；
12. 到期量化门槛是否基于冻结问题集和同一候选，是否用不同候选、截图、API 200、测试数量或单次模型成功拼接通过；
13. 当前改动是否只在 TASK.json 允许路径内，是否改变冻结核心、数据库、collector、现有报告合同、生产配置或未授权能力；
14. 尚未到期的最终要求是否仍然可达，是否通过删除、降低、改写最终验收文档或阶段出口规避偏离。

判定规则：
- 一致：本阶段全部到期出口成立，未发现偏离；
- 已修正：偏离已在本阶段授权范围内完成修正；
- 无影响：本任务与调查 Agent 无关，且没有改变最终效果可达性；
- 存在待处理偏离：任一到期出口失败、阻塞或证据不足。必须列出 IFE、IBE、ISE 或边界编号、偏离位置和原因，不得宣告阶段完成。

Hook 机检只覆盖合同结构、阶段映射、当前 TASK.json 路径边界和冻结核心偏离信号，不代表前端、后端、数据、模型、浏览器、可访问性、用户价值、部署或生产效果已通过。

最终答复必须包含一行：
“国家中断调查 Agent 最终验收回检：{stage} 一致 / 已修正 / 无影响 / 存在待处理偏离（要求编号与原因）”。{warning_text}"""


def run_explicit_stage_review(stage: str) -> int:
    errors = validate_documents() + validate_task_boundary()
    if errors:
        sys.stderr.write("国家中断调查 Agent 防偏离 Hook：机器检查失败\n")
        for error in errors:
            sys.stderr.write(f"- {error}\n")
        return 1
    sys.stdout.write(review_reason(stage))
    sys.stdout.write("\n")
    return 0


def load_hook_input() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(f"未收到有效的 Codex Hook 输入：{error}") from error
    if not isinstance(value, dict):
        raise RuntimeError("Codex Hook 输入必须是 JSON 对象。")
    return value


def run_stop_hook() -> int:
    try:
        hook_input = load_hook_input()
    except RuntimeError as error:
        emit(
            {
                "continue": True,
                "systemMessage": (
                    f"国家中断调查 Agent 防偏离 Hook 无法执行：{error}。"
                    "请人工回读最终验收文档和分阶段计划。"
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

    requested_stage = os.environ.get("DOMEYE_COUNTRY_OUTAGE_INVESTIGATION_STAGE")
    if requested_stage not in STAGE_IDS:
        emit({})
        return 0

    errors = validate_documents() + validate_task_boundary()
    if errors:
        emit(
            {
                "decision": "block",
                "reason": (
                    "国家中断调查 Agent 防偏离 Hook 机器检查失败：\n- "
                    + "\n- ".join(errors)
                    + "\n请先修正合同结构或任务边界，再结束阶段。"
                ),
            }
        )
        return 0

    emit({"decision": "block", "reason": review_reason(requested_stage)})
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    if arguments.stage:
        return run_explicit_stage_review(arguments.stage)
    return run_stop_hook()


if __name__ == "__main__":
    raise SystemExit(main())
