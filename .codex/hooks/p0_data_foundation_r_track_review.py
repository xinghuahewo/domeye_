#!/usr/bin/env python3
"""阶段结束时回检 P0 数据基础 R 轨是否偏离最终效果合同。"""

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
ACCEPTANCE_PATH = REPOSITORY_ROOT / "docs" / "P0数据基础R轨最终验收文档.md"
PLAN_PATH = REPOSITORY_ROOT / "docs" / "P0数据基础R轨分阶段计划.md"
CORE_MANIFEST_PATH = REPOSITORY_ROOT / "backend" / "core.sha256"
IDENTITY_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "data"
    / "P0数据基础R轨R0生产者身份清单.json"
)
IDENTITY_VALIDATOR_PATH = (
    REPOSITORY_ROOT / "dev" / "data_quality" / "p0_producer_identity.py"
)

STAGE_IDS = tuple(f"R{index}" for index in range(6))
ACCEPTANCE_IDS = tuple(f"RFA-{index:02d}" for index in range(1, 13))
STAGE_NAMES = {
    "R0": "生产者身份补绑",
    "R1": "独立语义对账门禁",
    "R2": "遗留语义风险测量与结案",
    "R3": "原始槽补齐与解析修复",
    "R4": "全窗口 RouteEvent 闭合",
    "R5": "全量语义复现与发布评审",
}
STAGE_DUE_IDS = {
    "R0": ("RFA-01", "RFA-08", "RFA-09", "RFA-11"),
    "R1": ("RFA-02", "RFA-09", "RFA-11"),
    "R2": ("RFA-03", "RFA-08", "RFA-09", "RFA-11"),
    "R3": ("RFA-04", "RFA-07", "RFA-09"),
    "R4": ("RFA-05", "RFA-07", "RFA-11"),
    "R5": ACCEPTANCE_IDS,
}

# 全阶段通用边界，任何阶段结束都必须逐条自证未偏离。
STANDING_BOUNDARIES = (
    "未修改 `backend/core/` 业务逻辑，未通过更新哈希清单掩盖变化；",
    "未以源精确对账结果宣称数据正确；",
    "未以代码阅读结论宣称二三月数据已被污染或未被污染；",
    "未把缺测、解析失败或源不可得表示为 0、正常或已恢复；",
    "未以抽样覆盖率表述全窗口覆盖率；",
    "未在 RFA-04 至 RFA-06 未满足时声明 `raw_traceable`；",
    "对外结论均标明来源身份与结论类型（代码事实 / 候选验收 / 已部署事实 / 未来设计）；",
    "未通过降低最终验收要求消除偏离。",
)


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="回检 P0 数据基础 R 轨是否偏离最终验收文档。",
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
    """只做结构完整性检查，不判断实施内容是否正确。"""
    errors: list[str] = []
    if not ACCEPTANCE_PATH.is_file():
        errors.append(f"最终验收文档不存在：{ACCEPTANCE_PATH}")
    if not PLAN_PATH.is_file():
        errors.append(f"分阶段计划文档不存在：{PLAN_PATH}")
    if errors:
        return errors

    acceptance_text = read_text(ACCEPTANCE_PATH)
    found_acceptance_ids = tuple(
        f"RFA-{value}"
        for value in re.findall(
            r"^### RFA-(\d{2})：", acceptance_text, re.MULTILINE
        )
    )
    if found_acceptance_ids != ACCEPTANCE_IDS:
        errors.append(
            "最终验收文档的要求编号必须且只能按 RFA-01 至 RFA-12 顺序出现；"
            f"当前为：{', '.join(found_acceptance_ids) or '无'}"
        )

    plan_text = read_text(PLAN_PATH)
    found_stage_ids = tuple(
        f"R{value}"
        for value in re.findall(r"^### R([0-5])：", plan_text, re.MULTILINE)
    )
    if found_stage_ids != STAGE_IDS:
        errors.append(
            "分阶段计划的阶段编号必须且只能按 R0 至 R5 顺序出现；"
            f"当前为：{', '.join(found_stage_ids) or '无'}"
        )

    for section in ("#### 入口", "#### 出口", "#### 边界"):
        if plan_text.count(section) < len(STAGE_IDS):
            errors.append(
                f"分阶段计划的 `{section}` 数量少于阶段数，阶段边界未写全。"
            )

    # RFA-02 的命名纪律本身必须在合同里存在，否则门禁失去区分能力。
    if "独立语义对账" not in acceptance_text or "源精确对账" not in acceptance_text:
        errors.append(
            "最终验收文档未同时出现「源精确对账」与「独立语义对账」，"
            "复制保真与语义正确的分离命名已失效。"
        )
    required_plan_phrases = (
        "Markdown 完成声明不再构成阶段出口",
        "scope_stopped",
        "不得以表族通配符代替逐表记录",
        "R3 → R4 → R5 串行",
    )
    for phrase in required_plan_phrases:
        if phrase not in plan_text:
            errors.append(f"分阶段计划缺少防偏离语义：{phrase}")
    if ACCEPTANCE_PATH.name not in plan_text:
        errors.append("分阶段计划没有引用最终验收文档。")
    return errors


def check_frozen_core() -> list[str]:
    """RFA-08：冻结核心不得被修改。只读检查工作树与文件哈希。"""
    warnings: list[str] = []
    if not CORE_MANIFEST_PATH.is_file():
        warnings.append(f"未找到核心哈希清单：{CORE_MANIFEST_PATH}")
        return warnings
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--", "backend/core"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        warnings.append(f"无法检查 `backend/core/` 工作树状态：{error}")
        return warnings

    changed = [line for line in completed.stdout.splitlines() if line.strip()]
    if changed:
        warnings.append(
            "`backend/core/` 存在未提交改动，违反 RFA-08：\n  "
            + "\n  ".join(changed)
        )
    try:
        hash_check = subprocess.run(
            ["sha256sum", "-c", CORE_MANIFEST_PATH.name],
            cwd=CORE_MANIFEST_PATH.parent,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        warnings.append(f"无法执行 `core.sha256` 校验：{error}")
        return warnings
    if hash_check.returncode != 0:
        detail = (hash_check.stdout + hash_check.stderr).strip()
        warnings.append(
            "`core.sha256` 校验失败，违反 RFA-08："
            + (f"\n  {detail}" if detail else "")
        )
    return warnings


def validate_r0_identity_manifest() -> list[str]:
    """RFA-01：执行 37 表机器清单校验，不从 Markdown 完成声明推断。"""
    if not IDENTITY_VALIDATOR_PATH.is_file():
        return [f"R0 生产者身份校验器不存在：{IDENTITY_VALIDATOR_PATH}"]
    if not IDENTITY_MANIFEST_PATH.is_file():
        return [f"R0 生产者身份机器清单不存在：{IDENTITY_MANIFEST_PATH}"]
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(IDENTITY_VALIDATOR_PATH),
                "--manifest",
                str(IDENTITY_MANIFEST_PATH),
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return [f"无法执行 R0 生产者身份机器校验：{error}"]
    if completed.returncode != 0:
        detail = (completed.stdout + completed.stderr).strip()
        return [
            "R0 生产者身份机器清单未通过："
            + (detail if detail else "校验器未返回错误详情")
        ]
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return ["R0 生产者身份校验器未返回有效 JSON"]
    if (
        result.get("verdict") != "passed"
        or result.get("table_count") != 37
        or result.get("blank_identity_count") != 0
    ):
        return [f"R0 生产者身份机器校验结果不满足出口：{result}"]
    return []


def validate_stage(stage: str) -> list[str]:
    errors = validate_documents()
    if stage == "R0":
        errors.extend(validate_r0_identity_manifest())
    return errors


def review_reason(stage: str | None) -> str:
    due_ids = STAGE_DUE_IDS.get(stage or "", ACCEPTANCE_IDS)
    if stage:
        heading = f"P0 数据基础 R 轨阶段结束回检：{stage} {STAGE_NAMES[stage]}"
        due_line = f"本阶段到期要求：{'、'.join(due_ids)}"
    else:
        heading = "P0 数据基础 R 轨最终验收防偏离回检"
        due_line = f"未指定阶段，按全部要求回检：{'、'.join(ACCEPTANCE_IDS)}"

    lines = [
        heading,
        "",
        f"最终效果合同：{ACCEPTANCE_PATH.relative_to(REPOSITORY_ROOT)}",
        f"分阶段计划：{PLAN_PATH.relative_to(REPOSITORY_ROOT)}",
        "",
        due_line,
        "",
        "结束本阶段前，请逐条自证：",
    ]
    lines.extend(f"{index}. {item}" for index, item in enumerate(STANDING_BOUNDARIES, 1))

    if stage == "R0":
        manifest_errors = validate_r0_identity_manifest()
        if manifest_errors:
            lines.extend(["", "RFA-01 机器清单校验失败："])
            lines.extend(f"- {item}" for item in manifest_errors)
        else:
            lines.extend(
                [
                    "",
                    "RFA-01 机器清单校验：通过（37/37，留空数 0）。",
                ]
            )

    core_warnings = check_frozen_core()
    if core_warnings:
        lines.extend(["", "机检发现的偏离信号："])
        lines.extend(f"- {item}" for item in core_warnings)

    lines.extend(
        [
            "",
            "机检只覆盖结构与冻结核心状态；数据测量、覆盖率、闭合范围与等级声明"
            "必须人工核对归档证据后写入阶段回检记录。",
            "存在偏离时先修正，不得通过改写最终验收要求消除。",
        ]
    )
    return "\n".join(lines)


def run_explicit_stage_review(stage: str) -> int:
    errors = validate_stage(stage)
    if errors:
        sys.stderr.write("P0 数据基础 R 轨防偏离 Hook：结构检查失败\n")
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
                    f"P0 数据基础 R 轨防偏离 Hook 无法执行：{error}。"
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

    requested_stage = os.environ.get("DOMEYE_P0_R_TRACK_STAGE")
    if requested_stage not in STAGE_IDS:
        # 只在调用方显式声明 R 轨阶段时介入，避免阻塞无关任务。
        emit({})
        return 0

    errors = validate_stage(requested_stage)
    if errors:
        emit(
            {
                "decision": "block",
                "reason": (
                    "P0 数据基础 R 轨防偏离 Hook 的合同结构检查失败：\n- "
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
