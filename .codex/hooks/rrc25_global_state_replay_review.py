#!/usr/bin/env python3
"""阶段结束时回检 RRC25 伊朗同期全局状态重放是否偏离最终效果合同。"""

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
    REPOSITORY_ROOT / "docs" / "RRC25伊朗同期全局状态重放最终验收文档.md"
)
PLAN_PATH = (
    REPOSITORY_ROOT / "docs" / "RRC25伊朗同期全局状态重放分阶段计划.md"
)
STAGE_IDS = tuple(f"S{index}" for index in range(7))
ACCEPTANCE_IDS = tuple(f"GSR-{index:02d}" for index in range(1, 15))
STAGE_NAMES = {
    "S0": "输入、基线与最终效果冻结",
    "S1": "全球状态、国家落位与 cohort 合同闭合",
    "S2": "全球 RIB 初始状态闭合",
    "S3": "单 UPDATE 流与 60 点状态闭合",
    "S4": "国家投影、伊朗对账与非伊朗覆盖闭合",
    "S5": "通用 API、页面与增量接续效果闭合",
    "S6": "最终重现与整体效果验收",
}
STAGE_DUE_IDS = {
    "S0": ACCEPTANCE_IDS,
    "S1": (
        "GSR-02",
        "GSR-03",
        "GSR-04",
        "GSR-05",
        "GSR-07",
        "GSR-08",
        "GSR-11",
        "GSR-12",
        "GSR-13",
    ),
    "S2": (
        "GSR-01",
        "GSR-02",
        "GSR-04",
        "GSR-05",
        "GSR-07",
        "GSR-11",
        "GSR-12",
        "GSR-13",
    ),
    "S3": ("GSR-03", "GSR-06", "GSR-08", "GSR-11", "GSR-12"),
    "S4": ("GSR-05", "GSR-07", "GSR-09", "GSR-10", "GSR-13"),
    "S5": ("GSR-10", "GSR-13", "GSR-14"),
    "S6": ACCEPTANCE_IDS,
}


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="回检 RRC25 伊朗同期全局状态重放是否偏离最终验收文档。",
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
        f"GSR-{value}"
        for value in re.findall(
            r"^### GSR-(\d{2})：", acceptance, re.MULTILINE
        )
    )
    if found_acceptance_ids != ACCEPTANCE_IDS:
        errors.append(
            "最终验收文档的要求编号必须且只能按 GSR-01 至 GSR-14 "
            "顺序出现；当前为："
            + (", ".join(found_acceptance_ids) or "无")
        )

    found_stage_ids = tuple(
        f"S{value}"
        for value in re.findall(r"^### S([0-6])：", plan, re.MULTILINE)
    )
    if found_stage_ids != STAGE_IDS:
        errors.append(
            "分阶段计划的阶段编号必须且只能按 S0 至 S6 顺序出现；当前为："
            + (", ".join(found_stage_ids) or "无")
        )

    required_acceptance_phrases = (
        "RIB 只初始化一次",
        "84 个 UPDATE 只形成一条有序更新流",
        "全球固定 cohort 等于全部国家 cohort 与显式未知桶之和",
        "伊朗固定 cohort 仍为 563 个 origin ASN、384,767 个 Prefix×VP",
        "不再为每个国家单独读取 RIB 或重放原始 UPDATE",
        "任意合法 `country_outage` 事件都通过同一事件解析和页面路由",
        "data_through 单调推进",
        "GSR-01 至 GSR-14 全部",
    )
    for phrase in required_acceptance_phrases:
        if phrase not in acceptance:
            errors.append(f"最终验收文档缺少防偏离语义：{phrase}")

    required_plan_phrases = (
        "只定义阶段入口、阶段出口和实施边界",
        "未达到本阶段出口时不得进入下一阶段",
        "每阶段结束必须调用全局状态重放最终验收防偏离 Hook",
        "至少追加一批时间连续的新 UPDATE",
        "S6 只有在 GSR-01 至 GSR-14 全部通过后",
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
        else "与当前改动相关的 GSR"
    )
    final_stage = stage or "无阶段"
    return f"""结束 {stage_label} 前，必须执行一次最终验收防偏离回检。

请完整重新阅读：
1. {ACCEPTANCE_PATH}
2. {PLAN_PATH}

本阶段重点回检：{due_ids}。

必须根据本阶段实际结果逐项判断：
1. 阶段入口是否真实成立，出口是否全部到期，是否跨越本阶段或全局边界；
2. 输入是否仍严格冻结为 RRC25 08:00 RIB、25 个 catch-up UPDATE 和 59 个
   正式 UPDATE，是否越过 UTC 08:00–15:00 或增加 collector；
3. RIB 是否只初始化一次并形成完整 RRC25 全球状态，是否因性能或验收便利只
   保留伊朗或少数国家；
4. 84 个 UPDATE 是否只形成一条有序状态流，是否被国家任务重复解析或应用，
   并行处理是否改变 record 与 element 顺序；
5. origin ASN 国家 mapping、版本、未知 origin、AS_SET、歧义桶和动态换源是否
   显式且可对账，是否把 AS_PATH 中间节点误当国家归属；
6. 所有国家是否由同一 seed RIB 冻结 cohort，Prefix、Prefix×VP、origin ASN
   和地址族人口是否保持可区分；
7. 正式窗口是否严格生成北京时间 18:05–23:00 的 60 个共同状态点，缺槽和
   失败是否保持缺失而没有补零或连线；
8. 全球是否等于全部国家与未知桶之和，国家内可见/不可见 Prefix×VP 和 ASN
   分类是否逐槽闭合；
9. ANNOUNCE、替换、跨国家 origin 迁移与 WITHDRAW 是否依据上一状态正确更新，
   是否出现重复人口或旧国家残余；
10. 伊朗是否仍为 563 个 origin ASN、384,767 个 Prefix×VP，其中 IPv4
    383,804、IPv6 963，并保持 60 点、ASN 与 UPDATE 活动逐项一致；
11. 所有非零 cohort 国家是否均生成同合同数据，大、中、小非伊朗样本是否只
    用于验收，是否为无真实事件国家伪造中断、恢复或原因；
12. RIB、catch-up 和正式窗口 checkpoint 是否可识别、可拒绝错配，并且中途
    恢复、连续运行和独立重复运行得到相同结果与哈希；
13. 缺失、损坏、错位、解析失败、unsupported、mapping unknown 和 source
    unavailable 是否保持失败关闭，data_through 是否只推进到最后完整槽；
14. run、dataset、revision、mapping、算法、输入清单、质量门和输出哈希是否
    可审计，是否保持原始 MRT、旧数据库和既有伊朗交付包只读；
15. 任意合法 country_outage 事件是否都通过同一事件解析和页面路由进入通用
    页面，country、collector、窗口、cohort、revision 和 data_through 是否来自
    事件与数据合同，是否残留伊朗默认值或国家专用分支；
16. overview、series、ASN 分页和 audit 是否消费同一国家合同，23:00 末状态
    是否在不重读 RIB 的情况下至少接续一批新 UPDATE，完整处理后
    data_through 是否单调推进且页面可读取新增时间点；
17. 尚未到期的 GSR 是否仍可达，是否通过改写合同、扩大输入、修改
    backend/core、旧 Detection、切换生产或降低伊朗基线规避当前偏离。

判定规则：
- 一致：本阶段全部到期出口成立，未发现偏离；
- 已修正：偏离已在本阶段授权范围内完成修正；
- 无影响：本任务与本次全局状态重放无关，且没有改变其可达性；
- 存在待处理偏离：任一到期出口失败、阻塞或证据不足。必须列出 GSR 编号、
  偏离位置和原因，不得宣告阶段完成。

结构检查通过只代表两份合同文档完整，不代表数据、恢复、API、页面或最终效果
已经通过。最终答复必须包含一行：
“RRC25 全局状态重放最终验收回检：{final_stage} 一致 / 已修正 / 无影响 /
存在待处理偏离（GSR 编号与原因）”。"""


def run_explicit_stage_review(stage: str) -> int:
    errors = validate_documents()
    if errors:
        sys.stderr.write("RRC25 全局状态重放最终验收防偏离 Hook：结构检查失败\n")
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
                    f"RRC25 全局状态重放最终验收防偏离 Hook 无法执行：{error}。"
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
                    "RRC25 全局状态重放最终验收防偏离 Hook "
                    "的合同结构检查失败：\n- "
                    + "\n- ".join(errors)
                    + "\n请先修正文档结构，再结束任务。"
                ),
            }
        )
        return 0

    requested_stage = os.environ.get("DOMEYE_RRC25_GLOBAL_REPLAY_STAGE")
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
