#!/usr/bin/env python3
"""阶段结束时回检国家中断报告与追问 Agent 是否偏离最终效果合同。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_PATH = (
    REPOSITORY_ROOT / "docs" / "国家中断报告与追问Agent最终验收文档.md"
)
PLAN_PATH = REPOSITORY_ROOT / "docs" / "国家中断报告与追问Agent分阶段计划.md"
CORE_MANIFEST_PATH = REPOSITORY_ROOT / "backend" / "core.sha256"

STAGE_IDS = tuple(f"A{index}" for index in range(6))
FRONTEND_IDS = tuple(f"FE-{index:02d}" for index in range(1, 10))
REPORT_IDS = tuple(f"RG-{index:02d}" for index in range(1, 14))
SCENARIO_IDS = tuple(f"SCE-{index:02d}" for index in range(1, 11))
STAGE_NAMES = {
    "A0": "两条验收主线和量化基线冻结",
    "A1": "报告输入、快照和事实闭合",
    "A2": "基础报告生成逻辑闭合",
    "A3": "前端和 Domeye-only 追问闭合",
    "A4": "外部证据、模型、安全和运行闭合",
    "A5": "前端与报告生成逻辑联合验收",
}
STAGE_DUE_FRONTEND = {
    "A0": FRONTEND_IDS,
    "A1": (),
    "A2": (),
    "A3": (
        "FE-01",
        "FE-02",
        "FE-03",
        "FE-04",
        "FE-05",
        "FE-06",
        "FE-08",
        "FE-09",
    ),
    "A4": ("FE-07",),
    "A5": FRONTEND_IDS,
}
STAGE_DUE_REPORT = {
    "A0": REPORT_IDS,
    "A1": ("RG-01", "RG-02", "RG-03", "RG-04"),
    "A2": ("RG-05", "RG-06", "RG-08", "RG-12", "RG-13"),
    "A3": ("RG-07",),
    "A4": ("RG-09", "RG-10", "RG-11", "RG-13"),
    "A5": REPORT_IDS,
}
STAGE_DUE_SCENARIOS = {
    "A0": SCENARIO_IDS,
    "A1": ("SCE-02", "SCE-03"),
    "A2": ("SCE-01", "SCE-02", "SCE-03", "SCE-09"),
    "A3": ("SCE-04", "SCE-08", "SCE-09", "SCE-10"),
    "A4": ("SCE-05", "SCE-06", "SCE-07", "SCE-10"),
    "A5": SCENARIO_IDS,
}


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="回检国家中断报告与追问 Agent 是否偏离最终验收文档。",
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


def validate_documents() -> list[str]:
    """只检查合同结构与关键边界，不判断阶段业务效果是否已经实现。"""
    errors: list[str] = []
    if not ACCEPTANCE_PATH.is_file():
        errors.append(f"最终验收文档不存在：{ACCEPTANCE_PATH}")
    if not PLAN_PATH.is_file():
        errors.append(f"分阶段计划文档不存在：{PLAN_PATH}")
    if errors:
        return errors

    try:
        acceptance = read_text(ACCEPTANCE_PATH)
        plan = read_text(PLAN_PATH)
    except RuntimeError as error:
        return [str(error)]

    found_frontend_ids = tuple(
        f"FE-{value}"
        for value in re.findall(r"^### FE-(\d{2})：", acceptance, re.MULTILINE)
    )
    if found_frontend_ids != FRONTEND_IDS:
        errors.append(
            "最终验收文档的前端编号必须且只能按 FE-01 至 FE-09 "
            "顺序出现；当前为："
            + (", ".join(found_frontend_ids) or "无")
        )

    found_report_ids = tuple(
        f"RG-{value}"
        for value in re.findall(r"^### RG-(\d{2})：", acceptance, re.MULTILINE)
    )
    if found_report_ids != REPORT_IDS:
        errors.append(
            "最终验收文档的报告逻辑编号必须且只能按 RG-01 至 RG-13 "
            "顺序出现；当前为："
            + (", ".join(found_report_ids) or "无")
        )

    found_scenario_ids = tuple(
        re.findall(r"^\| (SCE-\d{2}) \|", acceptance, re.MULTILINE)
    )
    if found_scenario_ids != SCENARIO_IDS:
        errors.append(
            "最终验收文档的场景编号必须且只能按 SCE-01 至 SCE-10 "
            "顺序出现；当前为："
            + (", ".join(found_scenario_ids) or "无")
        )

    found_stage_ids = tuple(
        f"A{value}"
        for value in re.findall(r"^### A([0-5])：", plan, re.MULTILINE)
    )
    if found_stage_ids != STAGE_IDS:
        errors.append(
            "分阶段计划的阶段编号必须且只能按 A0 至 A5 顺序出现；当前为："
            + (", ".join(found_stage_ids) or "无")
        )

    for section in ("#### 入口", "#### 出口", "#### 边界"):
        if plan.count(section) != len(STAGE_IDS):
            errors.append(
                f"分阶段计划必须为每个阶段且只为每个阶段提供 `{section}`；"
                f"当前数量为 {plan.count(section)}。"
            )

    required_acceptance_phrases = (
        "本文只设计最终效果",
        "最终验收只有两个要点",
        "前端",
        "报告生成逻辑",
        "技术报告研读工作台",
        "只处理当前用户有权访问的已有合法 `country_outage` 事件",
        "从始至终只使用 RRC25",
        "一份报告固定绑定一个发布快照",
        "正式报告最低数据门槛",
        "数据观测",
        "报告与追问",
        "就此追问",
        "返回最新",
        "仅使用 Domeye 数据",
        "外部证据补充",
        "用户上传",
        "只提取所需事实、摘要和必要短引文，不复制整篇文章",
        "模型与 API 差异不改变正式结果",
        "同一标准基础报告",
        "重新生成完整报告或完整回答",
        "短期会话",
        "PDF 和 Markdown",
        "空白页或缺字",
        "必须冻结的量化门槛",
        "干净环境重放",
        "FE-01 至 FE-09、RG-01 至 RG-13 和 SCE-01 至 SCE-10",
    )
    for phrase in required_acceptance_phrases:
        if phrase not in acceptance:
            errors.append(f"最终验收文档缺少防偏离语义：{phrase}")

    required_plan_phrases = (
        "只定义阶段入口、阶段出口和实施边界",
        "目标只包含前端最终效果和报告生成逻辑",
        "未达到本阶段出口时不得进入下一阶段",
        "每个阶段结束必须调用国家中断报告与追问 Agent 最终验收防偏离 Hook",
        "不修改 `backend/core/` 业务逻辑",
        "从始至终只使用 RRC25",
        "不支持任意国家、任意时间、collector 选择、多 collector",
        "外部搜索只在用户显式授权后",
        "不建设永久会话历史",
        "已形成版本化验收配置",
        "“数据观测”和“报告与追问”",
        "桌面、平板、手机、键盘、读屏、滚动、焦点",
        "不允许通用聊天外观、草稿流、强制滚动",
        "跨用户内容不可见",
        "PDF 与 Markdown 使用同一通过校验的报告制品",
        "FE-01 至 FE-09 全部通过",
        "RG-01 至 RG-13 全部通过",
        "SCE-01 至 SCE-10 全部在最终验收环境通过",
    )
    for phrase in required_plan_phrases:
        if phrase not in plan:
            errors.append(f"分阶段计划缺少阶段封口语义：{phrase}")

    if ACCEPTANCE_PATH.name not in plan:
        errors.append("分阶段计划没有引用最终验收文档。")

    return errors


def check_frozen_core() -> list[str]:
    """检查不可变核心边界；不把检查通过解释为阶段业务通过。"""
    warnings: list[str] = []
    if not CORE_MANIFEST_PATH.is_file():
        return [f"未找到核心哈希清单：{CORE_MANIFEST_PATH}"]

    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "--", "backend/core"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return [f"无法检查 `backend/core/` 工作树状态：{error}"]

    changed = [line for line in status_result.stdout.splitlines() if line.strip()]
    if changed:
        warnings.append(
            "`backend/core/` 存在工作树变化：\n  " + "\n  ".join(changed)
        )

    try:
        hash_result = subprocess.run(
            ["sha256sum", "-c", CORE_MANIFEST_PATH.name],
            cwd=CORE_MANIFEST_PATH.parent,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        warnings.append(f"无法执行 `core.sha256` 校验：{error}")
        return warnings

    if hash_result.returncode != 0:
        detail = (hash_result.stdout + hash_result.stderr).strip()
        warnings.append(
            "`core.sha256` 校验失败："
            + (f"\n  {detail}" if detail else "未返回错误详情")
        )
    return warnings


def review_reason(stage: str) -> str:
    due_frontend = "、".join(STAGE_DUE_FRONTEND[stage]) or "无新增到期项"
    due_report = "、".join(STAGE_DUE_REPORT[stage]) or "无新增到期项"
    due_scenarios = "、".join(STAGE_DUE_SCENARIOS[stage])
    core_warnings = check_frozen_core()
    core_section = ""
    if core_warnings:
        core_section = (
            "\n\n机检发现的不可变核心偏离信号：\n- "
            + "\n- ".join(core_warnings)
        )

    return f"""国家中断报告与追问 Agent 阶段结束回检：{stage} \
{STAGE_NAMES[stage]}

请完整重新阅读：
1. {ACCEPTANCE_PATH}
2. {PLAN_PATH}

本阶段到期或需冻结的前端要求：{due_frontend}。
本阶段到期或需冻结的报告逻辑要求：{due_report}。
本阶段到期或需冻结的场景：{due_scenarios}。

结束本阶段前，必须根据实际结果逐项判断：
1. 本阶段入口是否真实成立，出口、到期 FE、RG 和场景是否有实际证据，是否越过
   本阶段或全局边界；
2. 前端是否仍是事件内的技术报告研读工作台；数据观测、报告、追问、证据模式、
   状态、短期会话、新 revision、下载、移动端、滚动、焦点和读屏是否符合到期 FE；
3. 报告逻辑是否仍只覆盖已有合法 country_outage、用户触发和 RRC25，是否扩展到
   任意国家时间、第二 collector、通用 RCA、归因、处置或写入；
4. 报告、追问、外部附录、下载和审计是否固定在同一快照；最低数据门槛、缺槽、
   能力降级和身份冲突是否失败关闭；
5. 关键数字是否可追溯和重复计算，是否混淆 Prefix、Prefix×VP、ASN、UPDATE、
   等价资源、IP、用户，或把时间对应写成因果；
6. 报告是否达到面向人的中文叙事，项目知识是否替代隐藏记忆；模型备用是否已经
   认证并完整重生成；干净环境是否不依赖 Codex 记忆；
7. 外部搜索是否只由用户显式授权，是否独立分区、来源分级、逐项引用且只使用
   必要摘要；是否错误读取内网、登录页面、浏览器会话或上传文件；
8. 权限、缓存和用户隔离是否贯穿生成、追问、外部搜索和下载；提示注入、不可信
   Markdown、危险 URL、Shell、文件、SQL 和异常容量是否受阻；
9. PDF 与 Markdown 是否来自同一已校验制品，身份、摘要、中文字体、分页、表格、
   长链接和失败降级是否可核对；
10. A0 冻结的量化门槛是否仍按同一版本执行；尚未到期的 FE、RG 和 SCE 是否仍
    可达，是否通过删除、降级或改写合同规避偏离。{core_section}

判定规则：
- 一致：本阶段全部到期出口成立，未发现偏离；
- 已修正：偏离已在本阶段授权范围内完成修正；
- 无影响：本任务与国家中断报告 Agent 无关，且没有改变最终效果可达性；
- 存在待处理偏离：任一到期出口失败、阻塞或证据不足。必须列出 FE、RG 或 SCE 编号、
  偏离位置和原因，不得宣告阶段完成。

Hook 机检只覆盖合同结构和不可变核心状态，不代表数据与 Agent 工作面、报告、
追问、下载、外部证据、移动端、可访问性、模型、安全、短期重连、量化门槛、
干净环境重放或生产效果已经通过。

最终答复必须包含一行：
“国家中断报告 Agent 最终验收回检：{stage} 一致 / 已修正 / 无影响 /
存在待处理偏离（FE、RG 或 SCE 编号与原因）”。"""


def run_explicit_stage_review(stage: str) -> int:
    errors = validate_documents()
    if errors:
        sys.stderr.write("国家中断报告 Agent 防偏离 Hook：结构检查失败\n")
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
                    f"国家中断报告 Agent 防偏离 Hook 无法执行：{error}。"
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

    requested_stage = os.environ.get("DOMEYE_COUNTRY_OUTAGE_AGENT_STAGE")
    if requested_stage not in STAGE_IDS:
        # 只在调用方显式声明 Agent 阶段时介入，避免阻塞无关任务。
        emit({})
        return 0

    errors = validate_documents()
    if errors:
        emit(
            {
                "decision": "block",
                "reason": (
                    "国家中断报告 Agent 防偏离 Hook 的合同结构检查失败：\n- "
                    + "\n- ".join(errors)
                    + "\n请先修正文档结构，再结束任务。"
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
