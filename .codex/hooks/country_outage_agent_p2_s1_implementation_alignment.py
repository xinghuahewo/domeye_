#!/usr/bin/env python3
"""P2-S1 实现工程阶段防跑偏 Hook。

当前任务只验收 S1I-P0 实现规划基线。W0-W6 入口同时定义为 fail-closed，
未来实施任务必须提供同候选的 wave evidence 才能通过。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable, Sequence


DESIGN_CANDIDATE_ID = "country-outage-p2-s1-s1d-6-04135cee55b39ce5d574f7e4"
DESIGN_CANDIDATE_SHA256 = "dfc764ff34ca2d79f4580f3eb4f9792c4a10ed907c485182f877a9079b31f957"
DESIGN_CANDIDATE_CONTENT_DIGEST = "d0256d9f1246191df2d48432655ea384acb2e5a6844b15a78f80e4c9f5e55e74"
DESIGN_MANIFEST_SHA256 = "d5e5a6e31d600f7437c612792396a27d3418d82576dcbd6f871dd87b7c9abdbe"
S1D6_RECEIPT_SHA256 = "bd219692b3c899ec699a813c875f9ec0d36e394ffc99e8fc910c105c4c8eb883"
S1D6_RECEIPT_DIGEST = "e9bebac7d23ca78f4a942e4b7d21c9203d3de8420dcfc1db4a87627e91fea827"
FINAL_RECEIPT_SHA256 = "eeb5df8ec8433b9e70f73c9e94a31639c2d7fe012f39435d97f9f0b7047e55fb"
FINAL_RECEIPT_DIGEST = "3ef7e71ddd9bcaf2ed0fb762cc0b3d217c6f4b8047f2eacbbaaba7712bfa0619"
QUESTION_IDS = [
    "Q01", "Q02", "Q03", "Q04", "Q05", "Q06", "Q07", "Q08", "Q09", "Q10",
    "Q13", "Q14", "Q16", "Q17", "Q18", "Q19", "Q20", "Q21", "Q22", "Q23",
    "Q24", "Q26", "Q27", "Q29", "Q30", "Q31", "Q32", "Q33",
]
TOOL_IDS = [f"TOOL-{number:02d}" for number in range(7, 13)]
OPERATOR_IDS = [
    *[f"OP-{number:02d}" for number in range(5, 34)],
    *[f"OP-{number:02d}" for number in range(35, 40)],
]
CONTROL_IDS = [
    "GATE-01", "GATE-02", "GATE-03", "GATE-04", "GATE-05", "BOUNDARY-01",
    "RENDERER-01", "RENDERER-02", "RENDERER-03", "DELIVERY-01",
]
STAGE_SEQUENCE = ["S1I-P0", "W0", "W1", "W2", "W3", "W4", "W5", "W6"]
WAVE_CONTRACT = {
    "W0": {
        "depends_on": [],
        "effect": "source_schema_view_registry_admission_and_trusted_receipt_store_implemented",
        "unit_ids": [],
    },
    "W1": {
        "depends_on": ["W0"],
        "effect": "asn_prefix_and_state_time_drilldown_implemented",
        "unit_ids": [
            "TOOL-07", "TOOL-08", "TOOL-09", "TOOL-10",
            *[f"OP-{number:02d}" for number in range(5, 15)], "OP-35", "OP-36",
        ],
    },
    "W2": {
        "depends_on": ["W0"],
        "effect": "complete_window_path_projection_set_and_count_implemented",
        "unit_ids": ["TOOL-12", *[f"OP-{number:02d}" for number in range(15, 29)]],
    },
    "W3": {
        "depends_on": ["W1", "W2"],
        "effect": "state_interval_overlap_and_fixed_cohort_prefix_set_implemented",
        "unit_ids": ["OP-38", "OP-39"],
    },
    "W4": {
        "depends_on": ["W0", "W2"],
        "effect": "exact_time_route_state_path_and_vp_consistency_implemented",
        "unit_ids": ["TOOL-11", "OP-29", "OP-30", "OP-31", "OP-32", "OP-33", "OP-37"],
    },
    "W5": {
        "depends_on": ["W1", "W2", "W3", "W4"],
        "effect": "investigation_plan_result_graph_api_ui_delivery_and_sol_host_ds_implemented",
        "unit_ids": ["PLAN-CAP-01", *CONTROL_IDS],
    },
    "W6": {
        "depends_on": ["W5"],
        "effect": "same_candidate_28_question_offline_certification_accepted",
        "unit_ids": [],
    },
}

TASK_PATH = Path(".codex/TASK.json")
TARGET_PATH = Path(
    "docs/agent/P2-组合式调查/实体调查实现工程/"
    "Task-Spec-P2-S1实现工程目标与最终验收.md"
)
PLAN_PATH = Path(
    "docs/agent/P2-组合式调查/实体调查实现工程/"
    "Plan-P2-S1实现工程分阶段计划.md"
)
BASELINE_PATH = Path(
    "contracts/agent/country-outage-p2-s1-implementation/implementation-baseline.json"
)
DESIGN_CANDIDATE_PATH = Path(
    "contracts/agent/country-outage-p2-s1-execution-unit-design/candidate.json"
)
DESIGN_MANIFEST_PATH = Path(
    "contracts/agent/country-outage-p2-s1-execution-unit-design/acceptance-manifest.json"
)
S1D6_PATH = Path("evaluation/country-outage/p2-s1-execution-unit-design/stages/S1D-6.json")
FINAL_PATH = Path("evaluation/country-outage/p2-s1-execution-unit-design/stages/final.json")
P0_RECEIPT_PATH = Path(
    "evaluation/country-outage/p2-s1-implementation-planning/stages/S1I-P0.json"
)
WAVE_EVIDENCE_ROOT = Path(
    "contracts/agent/country-outage-p2-s1-implementation/wave-evidence"
)
WAVE_RECEIPT_ROOT = Path("evaluation/country-outage/p2-s1-implementation/stages")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class AlignmentError(RuntimeError):
    """实现规划或实施候选偏离冻结目标。"""


def _reject_constant(value: str) -> None:
    raise ValueError(f"禁止非有限 JSON 数值：{value}")


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"重复 JSON key：{key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise AlignmentError(f"无法严格读取 JSON：{path}：{error}") from error
    if not isinstance(value, dict):
        raise AlignmentError(f"JSON 顶层必须是对象：{path}")
    return value


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError as error:
        raise AlignmentError(f"无法读取文件：{path}：{error}") from error


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise AlignmentError(f"无法规范化 JSON：{error}") from error


def object_digest(value: dict[str, Any], excluded: set[str] | None = None) -> str:
    projected = {key: copy.deepcopy(item) for key, item in value.items() if key not in (excluded or set())}
    return sha256_bytes(canonical_json(projected))


def expect(condition: bool, code: str, detail: str) -> None:
    if not condition:
        raise AlignmentError(f"{code}：{detail}")


def exact(value: Any, expected: Any, code: str, detail: str) -> None:
    expect(value == expected, code, detail)


def require_hex64(value: Any, code: str, detail: str) -> str:
    expect(isinstance(value, str) and HEX64.fullmatch(value) is not None, code, detail)
    return value


def require_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise AlignmentError(f"无法读取文档：{path}：{error}") from error


def validate_frozen_design(root: Path) -> list[str]:
    candidate_path = root / DESIGN_CANDIDATE_PATH
    manifest_path = root / DESIGN_MANIFEST_PATH
    s1d6_path = root / S1D6_PATH
    final_path = root / FINAL_PATH
    exact(file_sha256(candidate_path), DESIGN_CANDIDATE_SHA256, "design_candidate_stale", "设计候选字节摘要漂移")
    exact(file_sha256(manifest_path), DESIGN_MANIFEST_SHA256, "design_manifest_stale", "设计验收清单字节摘要漂移")
    exact(file_sha256(s1d6_path), S1D6_RECEIPT_SHA256, "s1d6_receipt_stale", "S1D-6 回执字节摘要漂移")
    exact(file_sha256(final_path), FINAL_RECEIPT_SHA256, "final_receipt_stale", "final 回执字节摘要漂移")
    candidate = load_json(candidate_path)
    manifest = load_json(manifest_path)
    s1d6 = load_json(s1d6_path)
    final = load_json(final_path)
    exact(candidate.get("design_candidate_id"), DESIGN_CANDIDATE_ID, "design_candidate_identity_mismatch", "设计候选 ID 不匹配")
    exact(candidate.get("content_digest"), DESIGN_CANDIDATE_CONTENT_DIGEST, "design_candidate_content_mismatch", "设计候选 content digest 不匹配")
    exact(candidate.get("runtime_implemented"), False, "design_runtime_overclaim", "冻结设计候选不得声称运行时已实现")
    exact(candidate.get("production_deployed"), False, "design_deployment_overclaim", "冻结设计候选不得声称已部署")
    exact(manifest.get("design_candidate_id"), DESIGN_CANDIDATE_ID, "design_manifest_identity_mismatch", "Manifest 候选 ID 不匹配")
    reviews = manifest.get("final_independent_reviews")
    expect(isinstance(reviews, list) and len(reviews) == 2, "design_review_open", "必须有产品语义和 BGP 两份独立终审")
    exact(
        {item.get("review_kind") for item in reviews if isinstance(item, dict)},
        {"product_semantic_final_candidate_review", "bgp_final_candidate_review"},
        "design_review_population_mismatch",
        "独立终审人口不匹配",
    )
    for review in reviews:
        expect(isinstance(review, dict), "design_review_invalid", "终审记录必须是对象")
        exact(review.get("execution_status"), "completed", "design_review_incomplete", "终审必须完成")
        exact(review.get("hard_gate_passed"), True, "design_review_failed", "终审硬门必须通过")
        exact(review.get("candidate_content_digest"), DESIGN_CANDIDATE_CONTENT_DIGEST, "design_review_binding_mismatch", "终审未绑定同一候选")
    for stage, receipt, receipt_digest in (
        ("S1D-6", s1d6, S1D6_RECEIPT_DIGEST),
        ("final", final, FINAL_RECEIPT_DIGEST),
    ):
        exact(receipt.get("stage"), stage, "design_receipt_stage_mismatch", f"{stage} 回执 stage 不匹配")
        exact(receipt.get("status"), "alignment_passed", "design_receipt_failed", f"{stage} 回执未通过")
        exact(receipt.get("design_candidate_id"), DESIGN_CANDIDATE_ID, "design_receipt_identity_mismatch", f"{stage} 未绑定同一候选")
        exact(receipt.get("receipt_digest"), receipt_digest, "design_receipt_digest_mismatch", f"{stage} receipt digest 不匹配")
        exact(object_digest(receipt, {"receipt_digest"}), receipt_digest, "design_receipt_not_recomputable", f"{stage} receipt digest 不可重算")
    return [
        "frozen_design_candidate_verified",
        "design_manifest_and_independent_reviews_verified",
        "s1d6_and_final_receipts_verified",
    ]


def validate_documents(root: Path) -> list[str]:
    target = require_text(root / TARGET_PATH)
    plan = require_text(root / PLAN_PATH)
    target_sections = [
        "## 四、必须达成的最终效果",
        "### 4.1 用户调查效果",
        "### 4.2 BGP 事实效果",
        "### 4.3 组合运行效果",
        "### 4.4 开发者与原子性效果",
        "### 4.5 运行治理效果",
        "## 九、最终验收旅程",
        "## 十一、实现完成定义",
    ]
    plan_sections = [
        "## 四、S1I-P0：实现规划与防跑偏基线",
        "## 五、W0：Source、Schema、Registry 与可信回执基础",
        "## 六、W1：ASN、前缀与状态时间",
        "## 七、W2：窗口路径、投影、集合与计数",
        "## 八、W3：异常区间交集与固定 cohort 前缀集合",
        "## 九、W4：Path-at-time、观察方向与 VP 一致性",
        "## 十、W5：组合调查运行时、接口和页面",
        "## 十一、W6：同候选离线认证与实现验收",
    ]
    for marker in target_sections:
        expect(marker in target, "target_effect_section_missing", f"目标文档缺少：{marker}")
    for marker in plan_sections:
        expect(marker in plan, "implementation_wave_section_missing", f"实施计划缺少：{marker}")
    for phrase in (
        "用户提出一个复杂国家中断调查目标",
        "一个 Tool 只读取一种冻结事实人口",
        "一个 Operator 只执行一种登记的确定性变换",
        "Sol→Host Grounding/Validation→DS",
        "implementation acceptance、runtime promotion 与 production deployment 是三个独立状态",
        "生产部署：禁止" if "生产部署：禁止" in target else "生产部署",
    ):
        expect(phrase in target, "target_effect_statement_missing", f"目标文档缺少效果声明：{phrase}")
    for stage in STAGE_SEQUENCE:
        expect(stage in plan, "implementation_stage_missing", f"实施计划缺少阶段：{stage}")
    expect(
        "写隐式循环" in plan
        and all(owner in plan for owner in ("Host", "Tool", "Operator", "Renderer", "API handler", "模型")),
        "hidden_fanout_boundary_missing",
        "实施计划未禁止各执行边界隐藏 fan-out",
    )
    expect("一个 Tool 只读取一种冻结事实人口" in plan, "tool_atomicity_goal_missing", "实施计划未冻结 Tool 功能原子性")
    expect("一个 Operator 只执行一种登记的确定性业务变换" in plan, "operator_atomicity_goal_missing", "实施计划未冻结 Operator 功能原子性")
    return ["effect_first_target_document_verified", "w0_w6_effect_and_exit_plan_verified"]


def validate_task(root: Path) -> list[str]:
    task = load_json(root / TASK_PATH)
    exact(task.get("taskId"), "country-outage-agent-p2-s1-implementation-planning-20260813", "task_identity_mismatch", "实现规划 Task ID 不匹配")
    exact(task.get("targetVersion"), "country-outage-agent-p2-s1-implementation-planning-v1", "task_version_mismatch", "实现规划目标版本不匹配")
    transition = task.get("taskTransition")
    expect(isinstance(transition, dict), "task_transition_missing", "缺少实现规划任务迁移记录")
    exact(transition.get("frozenDesignCandidateId"), DESIGN_CANDIDATE_ID, "task_design_binding_mismatch", "Task 未绑定冻结设计候选")
    exact(transition.get("frozenDesignCandidateSha256"), DESIGN_CANDIDATE_SHA256, "task_design_sha_mismatch", "Task 设计摘要不匹配")
    exact(transition.get("frozenDesignManifestSha256"), DESIGN_MANIFEST_SHA256, "task_manifest_sha_mismatch", "Task Manifest 摘要不匹配")
    forbidden = task.get("forbiddenPaths")
    expect(isinstance(forbidden, list), "task_forbidden_paths_missing", "缺少 forbiddenPaths")
    for pattern in ("backend/**", "frontend/**", "tools/**", "deploy/**", "contracts/data/**"):
        expect(pattern in forbidden, "planning_scope_expanded", f"实现规划任务未禁止修改 {pattern}")
    return ["implementation_planning_task_boundary_verified"]


def validate_baseline(root: Path) -> tuple[dict[str, Any], list[str]]:
    baseline = load_json(root / BASELINE_PATH)
    exact(baseline.get("schema_version"), "country_outage_p2_s1_implementation_baseline_v1", "baseline_schema_mismatch", "实现基线 Schema 不匹配")
    content_digest = require_hex64(baseline.get("content_digest"), "baseline_digest_invalid", "实现基线 content digest 必须是 lower hex64")
    exact(object_digest(baseline, {"content_digest"}), content_digest, "baseline_digest_mismatch", "实现基线 content digest 不可重算")
    frozen = baseline.get("frozen_design")
    expect(isinstance(frozen, dict), "baseline_design_binding_missing", "实现基线缺少 frozen_design")
    exact(frozen.get("candidate_id"), DESIGN_CANDIDATE_ID, "baseline_design_id_mismatch", "基线设计候选 ID 漂移")
    exact(frozen.get("candidate_sha256"), DESIGN_CANDIDATE_SHA256, "baseline_design_sha_mismatch", "基线设计候选 SHA 漂移")
    exact(frozen.get("acceptance_manifest_sha256"), DESIGN_MANIFEST_SHA256, "baseline_manifest_sha_mismatch", "基线 manifest SHA 漂移")
    exact(frozen.get("s1d6_receipt_digest"), S1D6_RECEIPT_DIGEST, "baseline_s1d6_digest_mismatch", "基线 S1D-6 receipt 漂移")
    exact(frozen.get("final_receipt_digest"), FINAL_RECEIPT_DIGEST, "baseline_final_digest_mismatch", "基线 final receipt 漂移")
    questions = baseline.get("question_contract")
    expect(isinstance(questions, dict), "question_contract_missing", "基线缺少问题人口")
    exact(questions.get("question_count"), 28, "question_count_drift", "问题数量必须为 28")
    exact(questions.get("question_ids"), QUESTION_IDS, "question_population_drift", "问题人口或顺序漂移")
    exact(questions.get("p2_v1_not_executable_question_ids"), ["Q24"], "non_executable_scope_drift", "Q24 边界漂移")
    exact(questions.get("external_evidence_required_question_ids"), ["Q29", "Q30"], "external_evidence_scope_drift", "外部证据问题边界漂移")
    population = baseline.get("implementation_population")
    expect(isinstance(population, dict), "implementation_population_missing", "基线缺少实现人口")
    exact(population.get("tool_ids"), TOOL_IDS, "tool_population_drift", "P2 v1 Tool 人口漂移")
    exact(population.get("operator_ids"), OPERATOR_IDS, "operator_population_drift", "P2 v1 Operator 人口漂移")
    exact(population.get("plan_capability_ids"), ["PLAN-CAP-01"], "plan_population_drift", "P2 v1 Plan capability 人口漂移")
    exact(population.get("control_unit_ids"), CONTROL_IDS, "control_population_drift", "控制与交付人口漂移")
    deferred = baseline.get("deferred_scope")
    expect(isinstance(deferred, dict), "deferred_scope_missing", "基线缺少 P2.1 延期边界")
    exact(deferred.get("tool_ids"), ["TOOL-13"], "deferred_tool_drift", "P2.1 Tool 延期人口漂移")
    exact(deferred.get("operator_ids"), ["OP-34"], "deferred_operator_drift", "P2.1 Operator 延期人口漂移")
    exact(deferred.get("plan_capability_ids"), ["PLAN-CAP-02"], "deferred_plan_drift", "P2.1 Plan capability 延期人口漂移")
    atomicity = baseline.get("atomicity_contract")
    expect(isinstance(atomicity, dict) and all(atomicity.get(key) is True for key in (
        "one_tool_one_fact_population", "one_operator_one_deterministic_transform",
        "hidden_fan_out_forbidden", "operator_external_read_forbidden",
        "operator_model_call_forbidden", "renderer_business_transform_forbidden",
        "tool_business_transform_forbidden", "tool_internal_unit_call_forbidden",
    )), "atomicity_contract_open", "Tool/Operator 功能原子性合同未闭合")
    exact(baseline.get("stage_sequence"), STAGE_SEQUENCE, "stage_sequence_drift", "实施阶段顺序漂移")
    waves = baseline.get("wave_contract")
    expect(isinstance(waves, list) and len(waves) == 7, "wave_contract_missing", "W0-W6 合同必须正好七项")
    actual_waves = {item.get("stage"): item for item in waves if isinstance(item, dict)}
    exact(set(actual_waves), set(WAVE_CONTRACT), "wave_population_drift", "W0-W6 阶段人口漂移")
    for stage, expected in WAVE_CONTRACT.items():
        exact(actual_waves[stage].get("depends_on"), expected["depends_on"], "wave_dependency_drift", f"{stage} 依赖漂移")
        exact(actual_waves[stage].get("effect"), expected["effect"], "wave_effect_drift", f"{stage} 最终效果漂移")
        exact(actual_waves[stage].get("unit_ids"), expected["unit_ids"], "wave_unit_population_drift", f"{stage} 执行单元人口漂移")
    layers = baseline.get("acceptance_layers")
    expect(isinstance(layers, dict), "acceptance_layers_missing", "缺少分层验收状态")
    exact(layers.get("design_contract_accepted"), True, "design_acceptance_lost", "设计合同必须保持已通过")
    for key in (
        "implementation_planning_accepted", "implementation_accepted", "model_alignment_passed",
        "performance_acceptance", "production_deployed", "registry_admission_performed",
        "runtime_implemented", "runtime_model_promotion",
    ):
        exact(layers.get(key), False, "implementation_status_overclaim", f"规划阶段不得把 {key} 标为 true")
    boundary = baseline.get("boundary_contract")
    expect(isinstance(boundary, dict), "boundary_contract_missing", "缺少边界合同")
    exact(boundary.get("collector_id"), "rrc25", "collector_scope_drift", "collector 必须为 rrc25")
    exact(boundary.get("publication_cardinality"), 1, "publication_scope_drift", "必须为单一 publication")
    for key in (
        "external_data", "network_rca", "customer_cone_inference", "recovery_judgment",
        "user_impact_judgment", "cross_country_comparison", "cross_event_comparison",
        "production_change_authorized",
    ):
        exact(boundary.get(key), False, "boundary_overclaim", f"边界字段 {key} 必须为 false")
    exact(boundary.get("read_only"), True, "read_only_boundary_lost", "P2 v1 必须只读")
    model = baseline.get("model_flow_contract")
    expect(isinstance(model, dict), "model_flow_contract_missing", "缺少 Sol→Host→DS 合同")
    exact(model.get("execution_order"), ["gpt-5.6-sol", "host_grounding_and_validation", "ds_student"], "model_order_drift", "模型执行顺序漂移")
    exact(model.get("teacher_reference_is_ground_truth"), False, "teacher_truth_conflation", "Teacher 不得成为事实真值")
    exact(model.get("successful_student_revision_max"), 1, "student_revision_scope_drift", "DS 成功修订上限必须为 1")
    effects = baseline.get("effect_contract")
    expect(isinstance(effects, dict), "effect_contract_missing", "机器基线缺少最终效果合同")
    for key, minimum in (("user_effects", 10), ("bgp_fact_effects", 6), ("runtime_effects", 8), ("developer_effects", 6), ("governance_effects", 6)):
        value = effects.get(key)
        expect(isinstance(value, list) and len(value) >= minimum and len(value) == len(set(value)), "effect_population_open", f"{key} 最终效果人口不完整或重复")
    return baseline, [
        "implementation_effect_contract_verified",
        "question_and_unit_populations_verified",
        "tool_and_operator_atomicity_verified",
        "w0_w6_dependencies_and_effects_verified",
        "p2_1_and_product_boundaries_verified",
        "sol_host_ds_boundary_verified",
    ]


def validate_wave(root: Path, stage: str, baseline: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    contract = WAVE_CONTRACT[stage]
    evidence_path = root / WAVE_EVIDENCE_ROOT / f"{stage}.json"
    evidence = load_json(evidence_path)
    exact(evidence.get("schema_version"), "country_outage_p2_s1_implementation_wave_evidence_v1", "wave_evidence_schema_mismatch", f"{stage} evidence Schema 不匹配")
    exact(evidence.get("stage"), stage, "wave_evidence_stage_mismatch", f"{stage} evidence stage 不匹配")
    exact(evidence.get("status"), "implementation_wave_accepted", "wave_not_accepted", f"{stage} 尚未形成实现验收证据")
    exact(evidence.get("design_candidate_id"), DESIGN_CANDIDATE_ID, "wave_design_binding_mismatch", f"{stage} 未绑定冻结设计候选")
    exact(evidence.get("baseline_content_digest"), baseline.get("content_digest"), "wave_baseline_binding_mismatch", f"{stage} 未绑定当前实现基线")
    implementation_candidate_id = evidence.get("implementation_candidate_id")
    expect(isinstance(implementation_candidate_id, str) and implementation_candidate_id, "implementation_candidate_missing", f"{stage} 缺少实现候选 ID")
    exact(evidence.get("effect"), contract["effect"], "wave_effect_not_verified", f"{stage} 未证明冻结最终效果")
    exact(evidence.get("effect_verified"), True, "wave_effect_not_verified", f"{stage} 最终效果未通过")
    exact(evidence.get("implemented_unit_ids"), contract["unit_ids"], "wave_unit_population_mismatch", f"{stage} 实现单元人口漂移")
    exact(evidence.get("atomic_split_tests_passed"), True, "wave_atomicity_failed", f"{stage} atomic split tests 未通过")
    exact(evidence.get("p2_1_units_included"), [], "p2_1_unit_smuggled", f"{stage} 混入 P2.1 单元")
    exact(evidence.get("production_deployed"), False, "wave_deployment_overclaim", f"{stage} 不得声称已部署")
    tests = evidence.get("test_receipts")
    expect(isinstance(tests, list) and tests, "wave_test_receipts_missing", f"{stage} 缺少测试回执")
    for index, item in enumerate(tests):
        expect(isinstance(item, dict), "wave_test_receipt_invalid", f"{stage} test_receipts[{index}] 必须是对象")
        exact(item.get("passed"), True, "wave_test_failed", f"{stage} test_receipts[{index}] 未通过")
        require_hex64(item.get("receipt_digest"), "wave_test_digest_invalid", f"{stage} test_receipts[{index}] 摘要无效")
    if stage == "W0":
        source_receipts = evidence.get("source_and_governance_receipts")
        expect(isinstance(source_receipts, list) and source_receipts, "w0_source_receipts_missing", "W0 缺少 source/governance 回执")
    else:
        registry = evidence.get("registry_snapshot")
        expect(isinstance(registry, dict), "wave_registry_snapshot_missing", f"{stage} 缺少 Registry snapshot")
        require_hex64(registry.get("digest"), "wave_registry_digest_invalid", f"{stage} Registry digest 无效")
    prior_receipt_digests: list[str] = []
    required_prior_stages = ["S1I-P0", *contract["depends_on"]]
    supplied_prior = evidence.get("prior_stage_receipt_digests")
    expect(isinstance(supplied_prior, dict), "wave_prior_receipts_missing", f"{stage} 缺少 prior stage receipts")
    for prior_stage in required_prior_stages:
        prior_path = root / (P0_RECEIPT_PATH if prior_stage == "S1I-P0" else WAVE_RECEIPT_ROOT / f"{prior_stage}.json")
        prior = load_json(prior_path)
        exact(prior.get("stage"), prior_stage, "wave_prior_stage_mismatch", f"{stage} 的 {prior_stage} 回执 stage 不匹配")
        exact(prior.get("status"), "alignment_passed", "wave_prior_stage_failed", f"{stage} 的 {prior_stage} 回执未通过")
        digest = require_hex64(prior.get("receipt_digest"), "wave_prior_digest_invalid", f"{prior_stage} receipt digest 无效")
        exact(object_digest(prior, {"receipt_digest"}), digest, "wave_prior_digest_mismatch", f"{prior_stage} receipt digest 不可重算")
        exact(supplied_prior.get(prior_stage), digest, "wave_prior_binding_mismatch", f"{stage} 未绑定当前 {prior_stage} 回执")
        prior_receipt_digests.append(digest)
    exact(set(supplied_prior), set(required_prior_stages), "wave_prior_population_mismatch", f"{stage} prior receipt 人口不是精确依赖闭包")
    return evidence, [f"{stage.lower()}_implementation_effect_verified", f"{stage.lower()}_atomicity_and_evidence_verified"], prior_receipt_digests


def run_alignment(root: Path, stage: str) -> dict[str, Any]:
    expect(stage in STAGE_SEQUENCE, "unsupported_stage", f"不支持阶段：{stage}")
    checks: list[str] = []
    checks.extend(validate_task(root))
    checks.extend(validate_frozen_design(root))
    checks.extend(validate_documents(root))
    baseline, baseline_checks = validate_baseline(root)
    checks.extend(baseline_checks)
    prior_receipt_digests: list[str] = []
    implementation_candidate_id: str | None = None
    evidence_sha256: str | None = None
    if stage != "S1I-P0":
        evidence, wave_checks, prior_receipt_digests = validate_wave(root, stage, baseline)
        checks.extend(wave_checks)
        implementation_candidate_id = evidence["implementation_candidate_id"]
        evidence_sha256 = file_sha256(root / WAVE_EVIDENCE_ROOT / f"{stage}.json")
    receipt: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_implementation_alignment_receipt_v1",
        "stage": stage,
        "status": "alignment_passed",
        "design_candidate_id": DESIGN_CANDIDATE_ID,
        "implementation_candidate_id": implementation_candidate_id,
        "task_sha256": file_sha256(root / TASK_PATH),
        "target_document_sha256": file_sha256(root / TARGET_PATH),
        "phase_plan_sha256": file_sha256(root / PLAN_PATH),
        "baseline_sha256": file_sha256(root / BASELINE_PATH),
        "baseline_content_digest": baseline["content_digest"],
        "hook_sha256": file_sha256(Path(__file__).resolve()),
        "hook_tests_sha256": file_sha256(
            root / "dev/tests/test_country_outage_p2_s1_implementation_alignment_hook.py"
        ),
        "wave_evidence_sha256": evidence_sha256,
        "prior_stage_receipt_digests": prior_receipt_digests,
        "checks": checks,
        "design_contract_accepted": True,
        "implementation_planning": stage == "S1I-P0",
        "implementation_wave": stage != "S1I-P0",
        "runtime_implemented": stage == "W6",
        "production_deployed": False,
    }
    receipt["receipt_digest"] = object_digest(receipt)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P2-S1 实现工程阶段防跑偏 Hook")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--stage", required=True, choices=STAGE_SEQUENCE)
    parser.add_argument("--output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.repo_root).resolve()
    try:
        receipt = run_alignment(root, args.stage)
        if args.output:
            output = (root / args.output).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({
            "stage": args.stage,
            "status": receipt["status"],
            "design_candidate_id": receipt["design_candidate_id"],
            "receipt_digest": receipt["receipt_digest"],
            "output": args.output,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except AlignmentError as error:
        print(f"P2-S1 实现对齐检查失败：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
