#!/usr/bin/env python3
"""在阶段或任务结束时回检伊朗事件数据观测页最终验收要求。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_PATH = REPOSITORY_ROOT / "docs" / "伊朗事件数据观测页最终验收文档.md"
PLAN_PATH = REPOSITORY_ROOT / "docs" / "伊朗事件数据观测页分阶段计划.md"
STAGE_IDS = tuple(f"S{index}" for index in range(6))
ACCEPTANCE_IDS = tuple(f"FA-{index:02d}" for index in range(1, 13))
STAGE_NAMES = {
    "S0": "最终效果与语义冻结",
    "S1": "观测数据边界闭合",
    "S2": "主时间轴效果闭合",
    "S3": "维度与持续效果闭合",
    "S4": "页面语义与边界收口",
    "S5": "最终视觉验收",
}


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="回检伊朗事件数据观测页是否偏离最终验收文档。",
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

    found_acceptance_ids = tuple(
        f"FA-{value}"
        for value in re.findall(r"^### FA-(\d{2})：", acceptance, re.MULTILINE)
    )
    if found_acceptance_ids != ACCEPTANCE_IDS:
        errors.append(
            "最终验收文档的要求编号必须且只能按 FA-01 至 FA-12 顺序出现；"
            f"当前为：{', '.join(found_acceptance_ids) or '无'}"
        )

    found_stage_ids = tuple(
        f"S{value}"
        for value in re.findall(r"^### S([0-5])：", plan, re.MULTILINE)
    )
    if found_stage_ids != STAGE_IDS:
        errors.append(
            "分阶段计划的阶段编号必须且只能按 S0 至 S5 顺序出现；"
            f"当前为：{', '.join(found_stage_ids) or '无'}"
        )

    required_acceptance_phrases = (
        "图表是页面主体",
        "页面不负责替用户解释这些变化意味着什么",
        "Prefix×VP 不得简写或冒充为",
        "通用模板不得硬编码国家、collector 或时间粒度",
        "episode、wave",
        "FA-01 至 FA-12 全部满足",
    )
    for phrase in required_acceptance_phrases:
        if phrase not in acceptance:
            errors.append(f"最终验收文档缺少防偏离语义：{phrase}")

    required_plan_phrases = (
        "只规定整体从什么状态开始",
        "每个阶段结束必须调用最终验收防偏离 Hook",
        "未达到本阶段出口时不得进入下一阶段",
        "S5 只有在 FA-01 至 FA-12 全部通过时",
    )
    for phrase in required_plan_phrases:
        if phrase not in plan:
            errors.append(f"分阶段计划缺少阶段封口语义：{phrase}")

    if ACCEPTANCE_PATH.name not in plan:
        errors.append("分阶段计划没有引用最终验收文档。")

    return errors


def review_reason(stage: str | None) -> str:
    stage_label = (
        f"{stage}「{STAGE_NAMES[stage]}」"
        if stage in STAGE_NAMES
        else "当前任务（未声明阶段）"
    )
    return f"""结束 {stage_label} 前，必须执行一次“最终验收防偏离回检”。

请完整重新阅读：
1. {ACCEPTANCE_PATH}
2. {PLAN_PATH}

针对本阶段刚完成的实际结果逐项判断：
1. 本阶段入口是否真实成立，出口是否全部到期，是否跨越本阶段或全局边界；
2. 当前结果是否仍以图表展示数据为主体，而不是重新用摘要、卡片、标题、Tooltip 或标注替用户形成分析结论；
3. 是否只展示数量、比例、差值、窗口内峰谷、持续时间和分布等描述性统计；
4. 是否出现 episode、wave、ongoing、恢复、候选前兆、因果、根因、结论等级、行动建议或服务影响外推；
5. 关键时间是否绑定对应指标和值，是否把不同指标的极值误合并为一个“事件时间”；
6. Prefix、Prefix×VP、ASN 状态和 UPDATE 报文的对象、单位、分母、窗口及统计范围是否清楚；
7. 是否把单 RRC25 collector 扩张为全国或全球事实，或把 BGP 控制面变化扩张为用户服务中断；
8. 是否把窗口起点当作精确起点，把窗口末回升当作恢复，或把窗口内排序当作长期正常带异常；
9. 是否把缺失或未知写成 0、正常、未发生或已恢复；
10. 尚未到期的 FA 要求是否仍然可达，是否为了当前阶段实现便利而削弱或改写最终验收文档。
11. 通用模板是否仍硬编码国家、collector、时区或时间粒度，缺少某类数据时是否错误显示零值或其他事件占位。

判定规则：
- 一致：本阶段出口成立，未发现偏离；
- 已修正：发现的偏离已在本阶段授权范围内修正；
- 存在待处理偏离：任一到期出口失败、阻塞或证据不足。必须列出 FA 编号、偏离位置和原因，不得宣告阶段完成；
- 与本页面无关的任务：只报告“无影响”，不得制造无关修改。

结构检查通过只代表两份合同文档完整，不代表本阶段或最终页面已经通过。
最终答复必须包含一行：
“最终验收回检：{stage or '无阶段'} 一致 / 已修正 / 无影响 / 存在待处理偏离（FA 编号与原因）”。"""


def run_explicit_stage_review(stage: str) -> int:
    errors = validate_documents()
    if errors:
        sys.stderr.write("最终验收防偏离 Hook：结构检查失败\n")
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
                    f"最终验收防偏离 Hook 无法执行：{error}"
                    "。请人工回读最终验收文档和分阶段计划。"
                ),
            }
        )
        return 0

    if hook_input.get("hook_event_name") != "Stop":
        emit({})
        return 0

    # Codex 已因本 Hook 继续过一次时直接放行，避免 Stop Hook 自循环。
    if hook_input.get("stop_hook_active") is True:
        emit({})
        return 0

    errors = validate_documents()
    if errors:
        emit(
            {
                "decision": "block",
                "reason": (
                    "最终验收防偏离 Hook 的合同结构检查失败：\n- "
                    + "\n- ".join(errors)
                    + "\n请先修正文档结构，再结束任务。"
                ),
            }
        )
        return 0

    requested_stage = os.environ.get("DOMEYE_EVENT_OBSERVATION_STAGE")
    stage = requested_stage if requested_stage in STAGE_IDS else None
    emit({"decision": "block", "reason": review_reason(stage)})
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    if arguments.stage:
        return run_explicit_stage_review(arguments.stage)
    return run_stop_hook()


if __name__ == "__main__":
    raise SystemExit(main())
