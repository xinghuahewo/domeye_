#!/usr/bin/env python3
"""阶段结束时回检 Domeye 数据层 224-310 是否偏离最终效果合同。"""

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
ACCEPTANCE_PATH = REPOSITORY_ROOT / "docs" / "Domeye数据层224-310最终验收文档.md"
PLAN_PATH = REPOSITORY_ROOT / "docs" / "Domeye数据层224-310分阶段计划.md"
TASK_PATH = REPOSITORY_ROOT / ".codex" / "TASK.json"

STAGE_IDS = tuple(f"S{index}" for index in range(7))
ACCEPTANCE_IDS = tuple(f"DLAE-{index:02d}" for index in range(1, 17))
STAGE_NAMES = {
    "S0": "最终效果、窗口合同与现状基线冻结",
    "S1": "不可变原始证据与完整 RouteEvent 效果闭合",
    "S2": "RouteState、检查点与确定性重放效果闭合",
    "S3": "统一指标、存储职责、质量与水位效果闭合",
    "S4": "事件生命周期、Publication 与订正效果闭合",
    "S5": "在线读模型、API 与报告快照效果闭合",
    "S6": "旧库影子迁移、安全边界与整体最终验收",
}
STAGE_DUE_IDS = {
    "S0": ("DLAE-01", "DLAE-16"),
    "S1": ("DLAE-02", "DLAE-03"),
    "S2": ("DLAE-04", "DLAE-08"),
    "S3": ("DLAE-05", "DLAE-06", "DLAE-07"),
    "S4": ("DLAE-09", "DLAE-10", "DLAE-11"),
    "S5": ("DLAE-12", "DLAE-13"),
    "S6": ("DLAE-14", "DLAE-15", "DLAE-16"),
}

WINDOW_TOKENS = (
    "`rrc25`",
    "`[2026-02-24T00:00:00Z, 2026-03-11T00:00:00Z)`",
    "4,320",
    "241",
    "1,041,120",
    "`2026-03-11T00:00:00Z`",
)

REQUIRED_ACCEPTANCE_PHRASES = (
    "本文只设计最终效果",
    "底层证据和发布历史不可变",
    "完整 RouteEvent",
    "紧凑 RouteDelta 不冒充完整 RouteEvent",
    "国家、ASN 和 collector 五分钟指标进入 PostgreSQL/TimescaleDB",
    "缺失不得以零值落库",
    "`attempted_through` 表示已尝试处理位置",
    "`data_through` 表示连续、完整并通过质量门",
    "Publication 不可变，当前指针可原子推进",
    "Observation Publication 只依赖确定性观测",
    "在线请求不扫描原始 MRT",
    "报告冻结一个可重现的数据快照",
    "旧 PostgreSQL 表冻结只读",
    "DLAE-01 至 DLAE-16 全部",
)

REQUIRED_PLAN_PHRASES = (
    "只定义阶段入口、阶段出口和实施边界",
    "从始至终只使用 RRC25",
    "数据范围固定为 224-310",
    "未达到当前阶段出口时不得进入下一阶段",
    "每个阶段结束必须调用 Domeye 数据层 224-310 最终验收防偏离 Hook",
    "Hook 结构检查通过不等于",
    "S6 只有在 DLAE-01 至 DLAE-16 全部",
    "python3 .codex/hooks/data_layer_224_310_review.py --stage S0",
)


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="回检 Domeye 数据层 224-310 是否偏离最终效果合同。",
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


def text_covers_acceptance_id(text: str, acceptance_id: str) -> bool:
    if acceptance_id in text:
        return True
    target = int(acceptance_id.removeprefix("DLAE-"))
    for start, end in re.findall(
        r"DLAE-(\d{2}) 至 DLAE-(\d{2})",
        text,
    ):
        if int(start) <= target <= int(end):
            return True
    return False


def validate_documents() -> list[str]:
    """检查合同结构、冻结窗口和防偏离语义，不判断业务效果。"""
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
        acceptance = read_text(ACCEPTANCE_PATH)
        plan = read_text(PLAN_PATH)
    except RuntimeError as error:
        return [str(error)]

    found_acceptance_ids = tuple(
        f"DLAE-{value}"
        for value in re.findall(
            r"^### DLAE-(\d{2})：",
            acceptance,
            re.MULTILINE,
        )
    )
    if found_acceptance_ids != ACCEPTANCE_IDS:
        errors.append(
            "最终验收编号必须且只能按 DLAE-01 至 DLAE-16 顺序出现；当前为："
            + (", ".join(found_acceptance_ids) or "无")
        )

    found_stage_ids = tuple(
        f"S{value}"
        for value in re.findall(r"^### S([0-6])：", plan, re.MULTILINE)
    )
    if found_stage_ids != STAGE_IDS:
        errors.append(
            "分阶段计划必须且只能按 S0 至 S6 顺序出现；当前为："
            + (", ".join(found_stage_ids) or "无")
        )

    for heading in ("#### 入口", "#### 出口", "#### 边界"):
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
        "#### 排期",
    ):
        if heading in plan:
            errors.append(f"分阶段计划越过头尾与边界：出现 `{heading}`。")

    for token in WINDOW_TOKENS:
        if token not in acceptance:
            errors.append(f"最终验收文档缺少冻结窗口标识：{token}")
        if token not in plan:
            errors.append(f"分阶段计划缺少冻结窗口标识：{token}")

    for phrase in REQUIRED_ACCEPTANCE_PHRASES:
        if phrase not in acceptance:
            errors.append(f"最终验收文档缺少防偏离语义：{phrase}")
    for phrase in REQUIRED_PLAN_PHRASES:
        if phrase not in plan:
            errors.append(f"分阶段计划缺少阶段封口语义：{phrase}")

    if ACCEPTANCE_PATH.name not in plan:
        errors.append("分阶段计划没有引用最终验收文档。")

    for stage, due_ids in STAGE_DUE_IDS.items():
        match = re.search(
            rf"^### {stage}：[^\n]+\n(?P<body>.*?)(?=^### S[0-6]：|^## 五、)",
            plan,
            re.MULTILINE | re.DOTALL,
        )
        if not match:
            errors.append(f"分阶段计划缺少 {stage} 完整正文。")
            continue
        body = match.group("body")
        for due_id in due_ids:
            if not text_covers_acceptance_id(body, due_id):
                errors.append(f"分阶段计划 {stage} 缺少到期映射：{due_id}")

    return errors


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
    """核对当前阶段任务合同，不预设后续阶段的实现路径。"""
    if not TASK_PATH.is_file():
        return [f"当前工作树缺少任务合同：{TASK_PATH}"]
    try:
        task = load_json(TASK_PATH)
    except RuntimeError as error:
        return [str(error)]

    errors: list[str] = []
    root = task.get("worktreeRoot")
    if root != str(REPOSITORY_ROOT):
        errors.append(
            "TASK.json worktreeRoot 与 Hook 工作树不一致："
            f"{root!r} != {str(REPOSITORY_ROOT)!r}"
        )

    branch = task.get("targetBranch")
    try:
        current_branch = git_output(("branch", "--show-current")).strip()
    except RuntimeError as error:
        return [str(error)]
    if branch != current_branch:
        errors.append(
            "TASK.json targetBranch 与当前分支不一致："
            f"{branch!r} != {current_branch!r}"
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
    """冻结核心检查只报告信号，不冒充数据层验收。"""
    try:
        changed = git_output(("status", "--porcelain", "--", "backend/core"))
    except RuntimeError as error:
        return [str(error)]
    if changed.strip():
        return ["`backend/core/` 存在工作树改动。"]
    return []


def review_reason(stage: str) -> str:
    due_ids = "、".join(STAGE_DUE_IDS[stage])
    warnings = frozen_core_warnings()
    warning_text = ""
    if warnings:
        warning_text = "\n\n机检发现的冻结核心偏离信号：\n- " + "\n- ".join(warnings)
    return f"""Domeye 数据层 224-310 阶段结束回检：{stage}「{STAGE_NAMES[stage]}」

请完整重新阅读：
1. {ACCEPTANCE_PATH}
2. {PLAN_PATH}

本阶段到期要求：{due_ids}。尚未到期的 DLAE 也必须保持可达。

结束本阶段前，必须根据当前候选的实际结果逐项判断：
1. 阶段入口是否真实成立；阶段出口和到期 DLAE 是否具有同一候选身份的可复核证据；
2. collector 是否仍唯一为 rrc25，窗口是否仍为 [2026-02-24T00:00:00Z, 2026-03-11T00:00:00Z)，状态点、4,320 槽、241 桶和 mapping 是否一致；
3. 原始对象是否不可变且有摘要；两个修复制品是否保留替代关系而没有覆盖原坏文件；
4. RouteEvent 是否保留完整 AS_PATH、属性和原始坐标；是否把紧凑 RouteDelta 冒充完整 RouteEvent；
5. Seed RIB、事件顺序、RouteState、检查点和投影器版本是否支持确定性恢复；缺槽或不兼容时是否失败关闭；
6. 原始证据和高基数状态是否留在文件证据层，国家/ASN 五分钟指标是否进入统一在线时序层，事件与 Publication 是否进入事务层；
7. 是否继续新增国家/月度业务表；缺失、unknown、不适用和真实零值是否被混淆；
8. attempted_through 与 data_through 是否分离；data_through 是否越过未闭合缺口；
9. 事件阶段、Observation Publication、revision、订正和 current 指针是否以追加与原子切换推进，旧版本是否仍可读；
10. Observation 与 Analysis 是否各自有身份并公开滞后；正式输出是否绑定兼容版本；
11. API 是否只读预计算快照；冷请求是否仍扫描 MRT、RouteEvent 或解压全量 ASN 状态；是否用扩大超时代替读模型；
12. 报告是否冻结 incident、publication、revision、window、fact set、trend 和 evidence refs；当前指针推进是否改变旧报告；
13. 旧表是否只读影子迁移、双读对账且可回退；是否发生原地改表、提前删除或部分切换；
14. 当前任务是否修改 TASK.json 禁止路径、冻结核心、生产数据库、生产数据、部署配置或未授权范围；
15. 是否把 RRC25 控制面观测扩大为全国断网、用户影响、原因、攻击、责任、传播或完全恢复；
16. 是否把 Hook、文档、测试数量、API 200、静态截图、候选或历史记录写成最终或生产效果已通过；
17. 后续 DLAE 是否仍然可达，是否通过删除、降低或改写最终验收文档规避偏离。

判定规则：
- 一致：本阶段全部到期出口成立，未发现偏离；
- 已修正：偏离已经在本阶段授权边界内完成修正；
- 无影响：本任务与本阶段无关，且没有改变最终效果可达性；
- 存在待处理偏离：任一入口、到期出口、边界或证据失败。必须列出 DLAE 编号、偏离位置和原因，不得宣告阶段完成。

Hook 机检只覆盖合同结构、冻结窗口、阶段映射和当前 TASK.json 路径边界，不代表数据摄取、重放、指标、迁移、API、性能、报告、恢复、安全、候选或生产效果已通过。

最终答复必须包含一行：
“Domeye 数据层 224-310 最终验收回检：{stage} 一致 / 已修正 / 无影响 / 存在待处理偏离（DLAE 编号与原因）”。{warning_text}"""


def run_explicit_stage_review(stage: str) -> int:
    errors = validate_documents() + validate_task_boundary()
    if errors:
        sys.stderr.write("Domeye 数据层 224-310 防偏离 Hook：机器检查失败\n")
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
                    f"Domeye 数据层 224-310 防偏离 Hook 无法执行：{error}。"
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

    requested_stage = os.environ.get("DOMEYE_DATA_LAYER_224_310_STAGE")
    if requested_stage not in STAGE_IDS:
        emit({})
        return 0

    errors = validate_documents() + validate_task_boundary()
    if errors:
        emit(
            {
                "decision": "block",
                "reason": (
                    "Domeye 数据层 224-310 防偏离 Hook 机器检查失败：\n- "
                    + "\n- ".join(errors)
                    + "\n请先修正合同结构、冻结窗口或任务边界，再结束阶段。"
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
