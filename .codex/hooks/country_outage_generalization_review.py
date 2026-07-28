#!/usr/bin/env python3
"""阶段结束时回检国家中断通用观测页是否偏离最终效果合同。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_PATH = (
    REPOSITORY_ROOT / "docs" / "国家中断通用观测页最终验收文档.md"
)
PLAN_PATH = REPOSITORY_ROOT / "docs" / "国家中断通用观测页分阶段计划.md"
STAGE_IDS = tuple(f"S{index}" for index in range(6))
ACCEPTANCE_IDS = tuple(f"GFA-{index:02d}" for index in range(1, 13))
STAGE_NAMES = {
    "S0": "最终效果与现状基线冻结",
    "S1": "通用事件身份与能力合同闭合",
    "S2": "多国家通用读取效果闭合",
    "S3": "持续追加与一致截止点闭合",
    "S4": "缺口、补正与最终性语义闭合",
    "S5": "最终通用效果验收",
}
STAGE_DUE_IDS = {
    "S0": ACCEPTANCE_IDS,
    "S1": ("GFA-01", "GFA-03", "GFA-04", "GFA-09", "GFA-11"),
    "S2": ("GFA-02", "GFA-10", "GFA-12"),
    "S3": ("GFA-05", "GFA-06"),
    "S4": ("GFA-07", "GFA-08", "GFA-11"),
    "S5": ACCEPTANCE_IDS,
}


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="回检国家中断通用观测页是否偏离最终验收文档。",
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
        f"GFA-{value}"
        for value in re.findall(
            r"^### GFA-(\d{2})：", acceptance, re.MULTILINE
        )
    )
    if found_acceptance_ids != ACCEPTANCE_IDS:
        errors.append(
            "最终验收文档的要求编号必须且只能按 GFA-01 至 GFA-12 "
            "顺序出现；当前为："
            + (", ".join(found_acceptance_ids) or "无")
        )

    found_stage_ids = tuple(
        f"S{value}"
        for value in re.findall(r"^### S([0-5])：", plan, re.MULTILINE)
    )
    if found_stage_ids != STAGE_IDS:
        errors.append(
            "分阶段计划的阶段编号必须且只能按 S0 至 S5 顺序出现；当前为："
            + (", ".join(found_stage_ids) or "无")
        )

    required_acceptance_phrases = (
        "伊朗事件仍应保持当前完整观测能力",
        "能力至少能区分可用、构建中、不可用和不适用",
        "正常追加新时间点不产生新 revision",
        "缺槽保持断线或明确缺口，不得补成零",
        "新增国家事件只增加数据，不增加国家专用代码",
        "GFA-01 至 GFA-12 全部",
    )
    for phrase in required_acceptance_phrases:
        if phrase not in acceptance:
            errors.append(f"最终验收文档缺少防偏离语义：{phrase}")

    required_plan_phrases = (
        "只定义阶段入口、阶段出口和实施边界",
        "未达到本阶段出口时不得进入下一阶段",
        "每个阶段结束必须调用通用观测页最终验收防偏离 Hook",
        "S5 只有在 GFA-01 至 GFA-12 全部通过后",
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
    due_ids = (
        "、".join(STAGE_DUE_IDS[stage])
        if stage in STAGE_DUE_IDS
        else "与当前改动相关的 GFA"
    )
    return f"""结束 {stage_label} 前，必须执行一次最终验收防偏离回检。

请完整重新阅读：
1. {ACCEPTANCE_PATH}
2. {PLAN_PATH}

本阶段重点回检：{due_ids}。

必须根据本阶段实际结果逐项判断：
1. 阶段入口是否真实成立，出口是否全部到期，是否跨越本阶段或全局边界；
2. 是否仍以伊朗现有效果为无损基线，通用化有没有删减固定 cohort、
   Prefix×VP、ASN 状态、双栈、报文、质量或审计能力；
3. 任意合法 country_outage 是否进入同一身份、API 和页面合同，是否出现国家
   专用路由、字段、组件或硬编码分支；
4. 数据较少的事件是否通过 capability 诚实降级，是否出现空矩阵、全零矩阵、
   伊朗数据或其他事件数据占位；
5. overview、series、ASN 分页和 audit 是否绑定相同 revision、cohort 和
   data_through；
6. 正常新数据是否只追加时间点并推进 data_through，是否错误地每槽创建
   revision、改写旧点或要求重新部署页面；
7. 缺槽、暂缺、解析失败和窗口外是否保持缺失语义，是否被补零、连线或解释成
   正常、未发生、恢复；
8. 迟到数据、mapping、算法或源数据补正是否形成可识别 revision，是否静默
   覆盖旧结果；
9. Prefix、Prefix×VP、ASN、UPDATE、资源等价值、collector、地址族、cohort、
   mapping 和算法版本是否仍可区分；
10. ASN 等大集合是否保持服务端分页、稳定筛选和 revision 一致性；
11. 至少一个非伊朗事件是否能只增加数据接入，是否为了当前样本便利削弱最终
    通用效果；
12. 尚未到期的 GFA 是否仍然可达，是否通过删除、降低或改写最终验收文档规避
    当前偏离。

判定规则：
- 一致：本阶段全部到期出口成立，未发现偏离；
- 已修正：偏离已在本阶段授权范围内完成修正；
- 无影响：本任务与国家中断通用观测页无关，且没有改变其可达性；
- 存在待处理偏离：任一到期出口失败、阻塞或证据不足。必须列出 GFA 编号、
  偏离位置和原因，不得宣告阶段完成。

结构检查通过只代表两份合同文档完整，不代表阶段效果或最终页面已经通过。
最终答复必须包含一行：
“通用观测页最终验收回检：{stage or '无阶段'} 一致 / 已修正 / 无影响 /
存在待处理偏离（GFA 编号与原因）”。"""


def run_explicit_stage_review(stage: str) -> int:
    errors = validate_documents()
    if errors:
        sys.stderr.write("通用观测页最终验收防偏离 Hook：结构检查失败\n")
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
                    f"通用观测页最终验收防偏离 Hook 无法执行：{error}。"
                    "请人工回读最终验收文档和分阶段计划。"
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
                    "通用观测页最终验收防偏离 Hook 的合同结构检查失败：\n- "
                    + "\n- ".join(errors)
                    + "\n请先修正文档结构，再结束任务。"
                ),
            }
        )
        return 0

    requested_stage = os.environ.get("DOMEYE_COUNTRY_OUTAGE_STAGE")
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
