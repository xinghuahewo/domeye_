#!/usr/bin/env python3
"""阶段结束时回检国家中断通用观测页是否偏离最终效果合同。"""

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
ACCEPTANCE_PATH = (
    REPOSITORY_ROOT / "docs" / "国家中断通用观测页最终验收文档.md"
)
PLAN_PATH = REPOSITORY_ROOT / "docs" / "国家中断通用观测页分阶段计划.md"
TASK_PATH = REPOSITORY_ROOT / ".codex" / "TASK.json"
CORE_MANIFEST_PATH = REPOSITORY_ROOT / "backend" / "core.sha256"
STAGE_VERIFIER_PATHS = {
    "S0": REPOSITORY_ROOT / "dev" / "verify_country_outage_generalization_s0.py",
    "S1": REPOSITORY_ROOT / "dev" / "verify_country_outage_generalization_s1.py",
    "S2": REPOSITORY_ROOT / "dev" / "verify_country_outage_generalization_s2.py",
}

STAGE_IDS = tuple(f"S{index}" for index in range(7))
ACCEPTANCE_IDS = tuple(f"GFA-{index:02d}" for index in range(1, 17))
STAGE_NAMES = {
    "S0": "最终效果与现状差距冻结",
    "S1": "观察方向、会话事实与事件前 cohort 出口闭合",
    "S2": "前缀、AS 与 IP 事件投影出口闭合",
    "S3": "AS 属性与路径关联下游出口闭合",
    "S4": "Publication、读模型与 API 出口闭合",
    "S5": "页面阅读与 AS 同窗下钻出口闭合",
    "S6": "同一候选整体效果与生产身份验收",
}
STAGE_DUE_IDS = {
    "S0": ACCEPTANCE_IDS,
    "S1": ("GFA-02", "GFA-03", "GFA-04", "GFA-14"),
    "S2": ("GFA-05", "GFA-06", "GFA-07", "GFA-08", "GFA-09"),
    "S3": ("GFA-10", "GFA-12"),
    "S4": ("GFA-01", "GFA-14", "GFA-15"),
    "S5": ("GFA-11", "GFA-13", "GFA-15", "GFA-16"),
    "S6": ACCEPTANCE_IDS,
}

REQUIRED_ACCEPTANCE_PHRASES = (
    "本文只设计最终效果",
    "即 224-310",
    "首次检测前一小时",
    "最后一个完整五分钟状态点",
    "一个独立观察方向定义为一个 RRC25 peer ASN",
    "同一 peer ASN 的多个 BGP 会话只算一个方向",
    "会话掉线不会被伪写成 WITHDRAW",
    "至少一个预期方向不可见、至少一个预期方向仍可见时，为部分中断",
    "全部预期方向不可见时，为完全中断",
    "全部固定前缀均为完全中断时，该 AS 为 AS 路由中断",
    "新前缀只表示事件窗口内新增观测",
    "IPv6 使用 `/48` 等价量",
    "AS 路由中断排在受影响 AS 之前",
    "路径关联下游只从 cohort 冻结点实际观测到的 RRC25 AS_PATH 提取",
    "第一版不使用 AS relationship、customer cone",
    "前台不出现 `Prefix×VP`",
    "普通页面不得展示 `PRODUCT`、`PUBLICATION`、`REVISION`、`DATA THROUGH`",
    "`incident_go_v1_*`、`trend_product_v1_*`、`observation_publication_v1_*`",
    "冻结一致性是系统责任，不是需要向用户解释的页面内容",
    "不占用普通页面、折叠区、提示框或页尾",
    "一次加载全部 AS×时间",
    "GFA-01 至 GFA-16 全部",
)

REQUIRED_PLAN_PHRASES = (
    "只定义阶段入口、阶段出口和实施边界",
    "即 224-310",
    "RouteState 仍是唯一状态事实",
    "peer session down 不得伪造成 WITHDRAW",
    "第一版不使用 customer cone、AS relationship",
    "未达到当前阶段出口时不得进入下一阶段",
    "每个阶段结束必须调用通用观测页最终验收防偏离 Hook",
    "Hook 结构检查通过不等于",
    "S6 只有在 GFA-01 至 GFA-16 全部",
    "python3 .codex/hooks/country_outage_generalization_review.py --stage S0",
)


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="回检国家中断通用观测页是否偏离最终效果合同。",
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
    target = int(acceptance_id.removeprefix("GFA-"))
    for start, end in re.findall(r"GFA-(\d{2}) 至 GFA-(\d{2})", text):
        if int(start) <= target <= int(end):
            return True
    return False


def normalize_whitespace(text: str) -> str:
    """允许 Markdown 正常换行，但不放松关键词和语义。"""
    return re.sub(r"\s+", " ", text)


def validate_documents() -> list[str]:
    """只检查合同结构和关键语义，不判断阶段业务效果。"""
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
        f"GFA-{value}"
        for value in re.findall(r"^### GFA-(\d{2})：", acceptance, re.MULTILINE)
    )
    if found_acceptance_ids != ACCEPTANCE_IDS:
        errors.append(
            "最终验收编号必须且只能按 GFA-01 至 GFA-16 顺序出现；当前为："
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
    ):
        if heading in plan:
            errors.append(f"分阶段计划越过头尾与边界：出现 `{heading}`。")

    normalized_acceptance = normalize_whitespace(acceptance)
    normalized_plan = normalize_whitespace(plan)
    for phrase in REQUIRED_ACCEPTANCE_PHRASES:
        if phrase not in normalized_acceptance:
            errors.append(f"最终验收文档缺少防偏离语义：{phrase}")
    for phrase in REQUIRED_PLAN_PHRASES:
        if phrase not in normalized_plan:
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
    """核对当前任务合同；不把合同通过写成业务验收通过。"""
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
    if not CORE_MANIFEST_PATH.is_file():
        return [f"未找到核心哈希清单：{CORE_MANIFEST_PATH}"]
    try:
        changed = git_output(("status", "--porcelain", "--", "backend/core"))
    except RuntimeError as error:
        return [str(error)]
    return ["`backend/core/` 存在工作树改动。"] if changed.strip() else []


def validate_stage_artifacts(stage: str) -> list[str]:
    """运行当前已到期阶段的实际 verifier，不把文字回检冒充阶段证据。"""
    verifier = STAGE_VERIFIER_PATHS.get(stage)
    if verifier is None:
        return []
    if not verifier.is_file():
        return [f"{stage} 阶段 verifier 不存在：{verifier}"]
    try:
        result = subprocess.run(
            [sys.executable, str(verifier)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return [f"{stage} 阶段 verifier 无法执行：{error}"]
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()
        return [f"{stage} 阶段 verifier 失败：{detail or '无错误详情'}"]
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        return [f"{stage} 阶段 verifier 输出不是 JSON：{error}"]
    if not isinstance(payload, dict) or payload.get("status") != "pass":
        return [f"{stage} 阶段 verifier 未返回 pass：{payload!r}"]
    if payload.get("stage") != stage:
        return [
            f"{stage} 阶段 verifier 身份冲突：{payload.get('stage')!r}"
        ]
    return []


def review_reason(stage: str) -> str:
    due_ids = "、".join(STAGE_DUE_IDS[stage])
    warnings = frozen_core_warnings()
    warning_text = ""
    if warnings:
        warning_text = "\n\n机检发现的冻结核心偏离信号：\n- " + "\n- ".join(
            warnings
        )
    return f"""国家中断通用观测页阶段结束回检：{stage}「{STAGE_NAMES[stage]}」

请完整重新阅读：
1. {ACCEPTANCE_PATH}
2. {PLAN_PATH}

本阶段到期或需继续保持可达的要求：{due_ids}。

结束本阶段前，必须根据实际结果逐项判断：
1. 阶段入口是否真实成立，出口和到期 GFA 是否有同一候选身份的可复核证据；
2. 是否仍限制在 224-310、RRC25 和既有 country_outage 事件事实，不改检测时间、阈值或合并规则；
3. 活动与最终事件窗口、首次中断前最后完整槽和固定 cohort 是否唯一；新前缀是否保持独立轨道；
4. 独立方向是否按 peer ASN 去重；同一 peer ASN 的多会话是否只算一个方向；session down、路由不可见和未知是否保持不同事实；
5. 部分/完全中断前缀、受影响 AS、AS 路由中断是否只由固定 RouteState 确定性投影，人口和排序是否闭合；
6. IPv4 是否按唯一前缀计算，IPv6 是否使用 /48 等价量；是否因方向、会话、路径或重叠前缀重复计数；
7. AS 名称与性质缺失是否保持未知；AS 详情是否继承同一事件窗口并能返回原阅读位置；
8. 路径关联下游是否只来自事件前实际 RRC25 AS_PATH；是否混入 relationship、customer cone、依赖或因果；
9. 页面是否仍按概览、前缀趋势、AS 趋势、IP 趋势、受影响 AS、路径下游组织；前台、折叠区、提示框或页尾是否出现 Prefix×VP、内部制品 ID、PRODUCT、PUBLICATION、REVISION、DATA THROUGH 或“同一冻结制品、证据、限制与未知”等工程自证文字；
10. 首屏和趋势是否有界，大集合是否稳定分页；是否默认下发 AS×时间、前缀×方向或完整路径矩阵；
11. 缺槽、未知、身份冲突和未就绪能力是否失败关闭；是否用零、空图、旧结果或其他事件结果伪装正常；
12. 是否把路由控制面事实写成全国断网、用户影响、原因、客户依赖、责任或窗口外恢复；
13. 当前改动是否只在 TASK.json 允许路径内；是否改变冻结核心、数据库、原始制品、collector、生产配置或未授权能力；
14. 是否把 Hook、文档、测试计数、API 200 或截图写成阶段业务、候选、部署或生产最终效果已通过；
15. 尚未到期的 GFA 是否仍可达，是否通过删除、降低或改写最终验收文档规避当前偏离。

判定规则：
- 一致：本阶段全部到期出口成立，未发现偏离；
- 已修正：偏离已在本阶段授权范围内完成修正；
- 无影响：本任务与通用观测页无关，且没有改变最终效果可达性；
- 存在待处理偏离：任一到期出口失败、阻塞或证据不足。必须列出 GFA 编号、偏离位置和原因，不得宣告阶段完成。

Hook 机检只覆盖合同结构、阶段映射和当前 TASK.json 路径边界，不代表数据、算法、制品、API、页面、浏览器、性能、用户任务、部署或生产效果已通过。

最终答复必须包含一行：
“通用观测页最终验收回检：{stage} 一致 / 已修正 / 无影响 / 存在待处理偏离（GFA 编号与原因）”。{warning_text}"""


def run_explicit_stage_review(stage: str) -> int:
    errors = (
        validate_documents()
        + validate_task_boundary()
        + validate_stage_artifacts(stage)
    )
    if errors:
        sys.stderr.write("通用观测页最终验收防偏离 Hook：机器检查失败\n")
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
    if hook_input.get("stop_hook_active") is True:
        emit({})
        return 0

    requested_stage = os.environ.get("DOMEYE_COUNTRY_OUTAGE_STAGE")
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
                    "通用观测页最终验收防偏离 Hook 机器检查失败：\n- "
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
