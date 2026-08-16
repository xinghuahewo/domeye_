#!/usr/bin/env python3
"""机器核对国家中断通用观测页 S0 现状差距是否准确冻结。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPOSITORY_ROOT / "docs" / "国家中断通用观测页S0现状基线.md"
ACCEPTANCE_PATH = REPOSITORY_ROOT / "docs" / "国家中断通用观测页最终验收文档.md"
PLAN_PATH = REPOSITORY_ROOT / "docs" / "国家中断通用观测页分阶段计划.md"

EXPECTED_GFA_IDS = tuple(f"GFA-{index:02d}" for index in range(1, 17))
ALLOWED_S0_STATES = {"部分具备", "待建设"}

REQUIRED_BASELINE_PHRASES = (
    "版本：2.1",
    "冻结时间：2026-08-07 18:30（Asia/Shanghai）",
    "RRC25-only",
    "20260807T072925Z-data-layer-224-310-prod21",
    "b09d6733cdfa4a7bab83463fb862f954a369212a",
    "domeye_data_candidate_v1_ce3aa006fe1f7dd3e723db9b13baf097",
    "read_model_dataset_v1_bad9b4c0bd32f7d026356c82dab1b50e",
    "81 个事件、43 个国家",
    "4,320 个五分钟状态点",
    "此前马拉维的“观测源不是唯一 rrc25”错误在当前生产已不再复现",
    "2026-03-10 01:43:06",
    "结束与恢复未知",
    "route_event_dataset_v1_a408005061499629321017426e99a629",
    "2,448,056,457 条",
    "route_state_dataset_v1_c2f7f7c7c63c824f4e92ed4c90787bcb",
    "终态 60,499,595 个 key",
    "正式 RouteEvent 动作只允许 `announce`、`withdraw`、`rib_snapshot`",
    "没有找到同时满足 4,320 槽、逐条 peer 身份",
    "不重新生成 2,448,056,457 条 RouteEvent",
    "4,320 个 RRC25 UPDATE MRT",
    "不读 RIB 路由元素，不生成 RouteEvent",
    "不得为每个事件重复做 81 次全窗重放",
    "通用观测页最终验收回检：S0 一致",
)

SOURCE_EXPECTATIONS = {
    "backend/services/data_layer_224_310_runtime.py": (
        'WINDOW_START = "2026-02-24T00:00:00Z"',
        'WINDOW_END = "2026-03-11T00:00:00Z"',
        "STATE_POINT_COUNT = 4320",
        "def empty_asn_page(",
        '"prefix_vp_semantics": "derived_view_not_independent_fact"',
    ),
    "frontend/src/pages/AsnPage.vue": (
        "const defaults = recentRange(24)",
    ),
    "backend/services/asn_service.py": (
        "ASN 工作台最多支持 24 小时窗口",
    ),
    "backend/data_pipeline/route_event/index.py": (
        'ALLOWED_ACTIONS = frozenset(("announce", "withdraw", "rib_snapshot"))',
        "STATE_CHANGE 不得伪造成 RouteEvent",
    ),
    "docs/data/Domeye数据层224-310S1验收记录.md": (
        "route_event_dataset_v1_a408005061499629321017426e99a629",
        "2,448,056,457",
    ),
    "docs/data/Domeye数据层224-310S2验收记录.md": (
        "route_state_dataset_v1_c2f7f7c7c63c824f4e92ed4c90787bcb",
        "60,499,595",
    ),
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def verify() -> dict[str, object]:
    errors: list[str] = []
    checks: list[str] = []

    for path in (BASELINE_PATH, ACCEPTANCE_PATH, PLAN_PATH):
        if not path.is_file():
            errors.append(f"缺少文件：{path.relative_to(REPOSITORY_ROOT)}")
    if errors:
        return {"schema_version": "country_outage_generalization_s0_verification_v1", "status": "fail", "errors": errors}

    baseline = read_text(BASELINE_PATH)
    acceptance = read_text(ACCEPTANCE_PATH)
    plan = read_text(PLAN_PATH)

    for phrase in REQUIRED_BASELINE_PHRASES:
        if phrase not in baseline:
            errors.append(f"S0 基线缺少冻结事实：{phrase}")
    checks.append("S0 当前身份、页面差距和影响式数据计划已记录")

    rows = re.findall(
        r"^\| (GFA-\d{2}) \| ([^|]+?) \|",
        baseline,
        flags=re.MULTILINE,
    )
    row_ids = tuple(row[0] for row in rows)
    if row_ids != EXPECTED_GFA_IDS:
        errors.append(
            "S0 差距矩阵必须按 GFA-01 至 GFA-16 各出现一次；当前为："
            + ("、".join(row_ids) or "无")
        )
    for gfa_id, state in rows:
        normalized = state.strip()
        if normalized not in ALLOWED_S0_STATES:
            errors.append(f"{gfa_id} 在 S0 使用了未获准状态：{normalized}")
    checks.append("GFA-01 至 GFA-16 状态人口闭合且未冒充已交付")

    for required in (
        "版本：2.1",
        "GFA-01 至 GFA-16",
        "前台不出现 `Prefix×VP`",
        "同一 peer ASN 的多个 BGP 会话只算一个方向",
    ):
        if required not in acceptance:
            errors.append(f"最终验收合同缺少 S0 引用语义：{required}")
    for required in (
        "版本：2.1",
        "### S0：最终效果与现状差距冻结",
        "不扫描原始 MRT，不执行重放",
        "需要原始 MRT 定向补提",
    ):
        if required not in plan:
            errors.append(f"分阶段计划缺少 S0 边界：{required}")
    checks.append("S0 基线仍受 v2.1 最终合同与阶段边界约束")

    for relative_path, phrases in SOURCE_EXPECTATIONS.items():
        path = REPOSITORY_ROOT / relative_path
        if not path.is_file():
            errors.append(f"现状证据文件不存在：{relative_path}")
            continue
        content = read_text(path)
        for phrase in phrases:
            if phrase not in content:
                errors.append(f"现状证据漂移：{relative_path} 缺少 {phrase}")
    checks.append("全窗、空 ASN、AS 24 小时和 RouteEvent 会话缺口具有源码证据")

    forbidden_claims = (
        "GFA-01 至 GFA-16 全部通过",
        "S1 已完成",
        "新版页面已上线",
        "无需读取任何原始 MRT",
        "重新生成全部 RouteEvent",
    )
    for claim in forbidden_claims:
        if claim in baseline:
            errors.append(f"S0 基线越级或违反影响式重跑边界：{claim}")
    checks.append("S0 未越级声明 S1-S6 或无判断全量重跑")

    return {
        "schema_version": "country_outage_generalization_s0_verification_v1",
        "status": "pass" if not errors else "fail",
        "stage": "S0",
        "check_count": len(checks),
        "checks": checks,
        "errors": errors,
    }


def main() -> int:
    result = verify()
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
