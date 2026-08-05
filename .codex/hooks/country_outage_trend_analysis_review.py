#!/usr/bin/env python3
"""阶段结束时回检国家中断趋势分析是否偏离最终效果合同。"""

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
ACCEPTANCE_PATH = REPOSITORY_ROOT / "docs" / "国家中断趋势分析最终验收文档.md"
PLAN_PATH = REPOSITORY_ROOT / "docs" / "国家中断趋势分析分阶段计划.md"
TASK_PATH = REPOSITORY_ROOT / ".codex" / "TASK.json"
CORE_MANIFEST_PATH = REPOSITORY_ROOT / "backend" / "core.sha256"
STAGE_VERIFIER_PATHS = {
    "S0": REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s0.py",
    "S1": REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s1.py",
    "S2": REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s2.py",
    "S3": REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s3.py",
    "S4": REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s4.py",
}

STAGE_IDS = tuple(f"S{index}" for index in range(7))
ACCEPTANCE_IDS = tuple(f"TAE-{index:02d}" for index in range(1, 16))
STAGE_NAMES = {
    "S0": "最终效果、数据语义与价值基线冻结",
    "S1": "TrendProfile 身份、质量与基线效果闭合",
    "S2": "关键点、阶段与窗口账本效果闭合",
    "S3": "地址族、ASN 迁移与时间对应效果闭合",
    "S4": "Evidence Graph、阅读旅程与组合追问效果闭合",
    "S5": "RRC25 同期国家投影参照效果闭合",
    "S6": "整体效果、可信度与增量价值验收",
}
STAGE_DUE_IDS = {
    "S0": ACCEPTANCE_IDS,
    "S1": ("TAE-01", "TAE-02", "TAE-03", "TAE-04"),
    "S2": ("TAE-05", "TAE-06", "TAE-07"),
    "S3": ("TAE-08", "TAE-09", "TAE-10"),
    "S4": ("TAE-12", "TAE-13", "TAE-14"),
    "S5": ("TAE-11",),
    "S6": ACCEPTANCE_IDS,
}

REQUIRED_ACCEPTANCE_PHRASES = (
    "本文只设计最终效果",
    "Evidence-grounded Incident Analysis",
    "从始至终只描述 RRC25 BGP",
    "`mixed`、`unmatched` 和",
    "`insufficient_data`",
    "缺失不补零、不连线",
    "窗口起点不自动解释为正常基线",
    "损失、回升与残留",
    "`unknown` 作为独立状态",
    "只表示时间关系",
    "同期全球投影提供参照",
    "Evidence Graph 不包含 Hypothesis 节点",
    "模型只能对已绑定的结构组织受控中文",
    "正确弃答率为 100%",
    "TAE-01 至 TAE-15 全部",
)

REQUIRED_PLAN_PHRASES = (
    "只定义阶段入口、阶段出口和实施边界",
    "从始至终只使用 RRC25",
    "未达到当前阶段出口时不得进入下一阶段",
    "每个阶段结束必须调用国家中断趋势分析最终验收防偏离 Hook",
    "Hook 结构检查通过不等于",
    "S6 只有在 TAE-01 至 TAE-15 全部",
    "python3 .codex/hooks/country_outage_trend_analysis_review.py --stage S0",
)


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="回检国家中断趋势分析是否偏离最终效果合同。",
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


def text_covers_acceptance_id(text: str, acceptance_id: str) -> bool:
    if acceptance_id in text:
        return True
    target = int(acceptance_id.removeprefix("TAE-"))
    for start, end in re.findall(
        r"TAE-(\d{2}) 至 TAE-(\d{2})",
        text,
    ):
        if int(start) <= target <= int(end):
            return True
    return False


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法读取 JSON：{path}：{error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON 必须是对象：{path}")
    return value


def validate_documents() -> list[str]:
    """只检查合同结构和防偏离语义，不判断业务效果。"""
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
        f"TAE-{value}"
        for value in re.findall(
            r"^### TAE-(\d{2})：", acceptance, re.MULTILINE
        )
    )
    if found_acceptance_ids != ACCEPTANCE_IDS:
        errors.append(
            "最终验收编号必须且只能按 TAE-01 至 TAE-15 顺序出现；"
            "当前为："
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

    forbidden_stage_headings = (
        "#### 实施步骤",
        "#### 任务清单",
        "#### 具体做法",
        "#### 技术方案",
    )
    for heading in forbidden_stage_headings:
        if heading in plan:
            errors.append(f"分阶段计划越过头尾与边界：出现 `{heading}`。")

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
    """核对当前任务合同；不固定后续阶段的代码路径。"""
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
    """不可变核心检查只输出偏离信号，不冒充业务验收。"""
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


def validate_stage_artifacts(stage: str) -> list[str]:
    """阶段专属机器基线只在到期阶段执行，且不冒充业务效果验收。"""
    verifier_path = STAGE_VERIFIER_PATHS.get(stage)
    if verifier_path is None:
        return []
    if not verifier_path.is_file():
        return [f"{stage} 阶段校验器不存在：{verifier_path}"]
    try:
        result = subprocess.run(
            [sys.executable, str(verifier_path)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return [f"无法执行 {stage} 阶段校验器：{error}"]
    if result.returncode == 0:
        return []
    detail = (result.stdout + result.stderr).strip()
    return [f"{stage} 阶段校验失败：{detail or '无错误详情'}"]


def review_reason(stage: str) -> str:
    due_ids = "、".join(STAGE_DUE_IDS[stage])
    warnings = frozen_core_warnings()
    warning_text = ""
    if warnings:
        warning_text = "\n\n机检发现的冻结核心偏离信号：\n- " + "\n- ".join(warnings)
    return f"""国家中断趋势分析阶段结束回检：{stage}「{STAGE_NAMES[stage]}」

请完整重新阅读：
1. {ACCEPTANCE_PATH}
2. {PLAN_PATH}

本阶段到期或需继续保持可达的要求：{due_ids}。

结束本阶段前，必须根据实际结果逐项判断：
1. 阶段入口是否真实成立，出口和到期 TAE 是否有同一候选身份的可复核证据；
2. 事件、国家、RRC25、窗口、publication、revision、data_through、统计人口和最终性是否仍唯一绑定；
3. 质量、缺失、unknown、首尾对齐和基线类型是否先于趋势分类；是否补零、连线或把起点写成正常基线；
4. 任意曲线是否仍能形成关键点、原子状态和阶段；复杂或不足曲线是否允许 mixed、unmatched 或 insufficient_data；
5. 损失、回升、残留、槽积分和阈值槽是否公开操作数、公式、单位、方向和基线；
6. IPv4 与 IPv6 是否同时显示分母；ASN unknown 是否在列表、迁移和聚合中人口闭合；
7. UPDATE、ANNOUNCE、WITHDRAW、资源、Prefix×VP 和 ASN 是否标明不同统计人口；时间对应是否被误写成因果；
8. 同期全球投影是否只使用同 RRC25、同时间网格、同 mapping 和兼容指标；是否被误写成真实事件或正常带；
9. 每个 Claim 是否有 Evidence、Limitation 和 Unknown；Evidence Graph v1 是否混入 Hypothesis 或自由因果边；
10. 图表、摘要、报告、追问、Markdown 与 PDF 是否使用同一 TrendProfile 和 Evidence Graph；模型是否改写确定性结果；
11. 原因、攻击、政策、用户影响、责任和窗口外恢复是否仍正确弃答；是否扩展到多 collector、外部证据或 Network RCA；
12. 本阶段是否只在 TASK.json 允许路径内改动，是否修改冻结核心、数据库、数据范围、生产配置或未授权能力；
13. 是否把 Hook、文档、测试计数、API 200 或截图写成业务、候选或生产效果已通过；
14. 尚未到期的 TAE 是否仍然可达，是否通过删除、降低或改写最终验收文档规避偏离。

判定规则：
- 一致：本阶段全部到期出口成立，未发现偏离；
- 已修正：偏离已在本阶段授权范围内完成修正；
- 无影响：本任务与趋势分析无关，且没有改变最终效果可达性；
- 存在待处理偏离：任一到期出口失败、阻塞或证据不足。必须列出 TAE 编号、偏离位置和原因，不得宣告阶段完成。

Hook 机检只覆盖合同结构、阶段映射和当前 TASK.json 路径边界，不代表数据、算法、API、页面、报告、追问、下载、模型、可访问性、用户价值或生产效果已通过。

最终答复必须包含一行：
“国家中断趋势分析最终验收回检：{stage} 一致 / 已修正 / 无影响 / 存在待处理偏离（TAE 编号与原因）”。{warning_text}"""


def run_explicit_stage_review(stage: str) -> int:
    errors = (
        validate_documents()
        + validate_task_boundary()
        + validate_stage_artifacts(stage)
    )
    if errors:
        sys.stderr.write("国家中断趋势分析防偏离 Hook：机器检查失败\n")
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
                    f"国家中断趋势分析防偏离 Hook 无法执行：{error}。"
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

    requested_stage = os.environ.get("DOMEYE_COUNTRY_OUTAGE_TREND_STAGE")
    if requested_stage not in STAGE_IDS:
        emit({})
        return 0

    errors = (
        validate_documents()
        + validate_task_boundary()
        + validate_stage_artifacts(requested_stage)
    )
    if errors:
        emit(
            {
                "decision": "block",
                "reason": (
                    "国家中断趋势分析防偏离 Hook 机器检查失败：\n- "
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
