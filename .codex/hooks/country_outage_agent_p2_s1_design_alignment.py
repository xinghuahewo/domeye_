#!/usr/bin/env python3
"""P2-S1 执行单元设计阶段防跑偏检查。

该 Hook 只验证设计人口、阶段依赖、结构、摘要和边界；它不证明 Tool、Operator、
调查运行时、页面、部署或生产能力已经实现。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


TASK_SPEC = Path(
    "docs/agent/P2-组合式调查/实体调查执行单元设计/"
    "Task-Spec-目标与最终验收文档.md"
)
PHASE_PLAN = Path(
    "docs/agent/P2-组合式调查/实体调查执行单元设计/"
    "Plan-Tool与Operator设计分阶段计划.md"
)
CONTRACT_ROOT = Path(
    "contracts/agent/country-outage-p2-s1-execution-unit-design"
)
RECEIPT_ROOT = Path(
    "evaluation/country-outage/p2-s1-execution-unit-design/stages"
)

STAGES = tuple(f"S1D-{index}" for index in range(7))
VALID_STAGES = STAGES + ("final",)

EXPECTED_QUESTIONS = (
    "Q01",
    "Q02",
    "Q03",
    "Q04",
    "Q05",
    "Q06",
    "Q07",
    "Q08",
    "Q09",
    "Q10",
    "Q13",
    "Q14",
    "Q16",
    "Q17",
    "Q18",
    "Q19",
    "Q20",
    "Q21",
    "Q22",
    "Q23",
    "Q24",
    "Q26",
    "Q27",
    "Q29",
    "Q30",
    "Q31",
    "Q32",
    "Q33",
)

EXPECTED_TOOLS = tuple(f"TOOL-{index:02d}" for index in range(7, 14))
EXPECTED_OPERATORS = tuple(f"OP-{index:02d}" for index in range(5, 38))
EXPECTED_HOST_UNITS = (
    "PLAN-CAP-01",
    "GATE-01",
    "GATE-02",
    "GATE-03",
    "GATE-04",
    "GATE-05",
    "BOUNDARY-01",
    "RENDERER-01",
    "RENDERER-02",
    "RENDERER-03",
    "DELIVERY-01",
)
EXPECTED_EXECUTION_UNITS = EXPECTED_TOOLS + EXPECTED_OPERATORS + EXPECTED_HOST_UNITS
EXISTING_EXECUTION_UNITS = tuple(f"TOOL-{index:02d}" for index in range(1, 7)) + tuple(
    f"OP-{index:02d}" for index in range(1, 5)
)
EXPECTED_CAPABILITIES = tuple(f"CAP-P2-{index:03d}" for index in range(1, 62))
S1D1_SCENARIOS = (
    "normal",
    "empty",
    "missing",
    "wrong_identity",
    "boundary",
    "large_result",
)

FUNCTION_ATOMICITY_MARKERS = (
    "execution_unit_function_atomicity",
    "tool_single_read_semantic",
    "operator_single_transform_semantic",
    "atomic_split_test",
    "execution_unit_failure_atomicity",
    "组合只发生在 InvestigationPlan",
)

RUNTIME_COMMIT_CONSISTENCY = (
    "node_result_commit_consistency",
    "investigation_revision_commit_consistency",
    "evidence_graph_commit_consistency",
    "dialog_state_commit_consistency",
    "export_commit_consistency",
)

DUAL_MODEL_MARKERS = (
    "sol_then_ds_execution_order",
    "gpt-5.6-sol",
    "ds_student_model_id_required",
    "evidence_truth_precedes_teacher",
    "teacher_reference_not_ground_truth",
    "teacher_required=true",
)

ARTIFACTS_BY_STAGE: Mapping[str, tuple[Path, ...]] = {
    "S1D-1": (
        CONTRACT_ROOT / "question-capability-map.json",
        CONTRACT_ROOT / "question-oracle-seed.json",
        CONTRACT_ROOT / "execution-unit-decomposition.json",
        CONTRACT_ROOT / "model-role-contract.json",
    ),
    "S1D-2": (
        CONTRACT_ROOT / "tool-catalog.json",
        CONTRACT_ROOT / "tool-contract.schema.json",
        CONTRACT_ROOT / "tool-atomicity-review.json",
    ),
    "S1D-3": (
        CONTRACT_ROOT / "operator-catalog.json",
        CONTRACT_ROOT / "operator-contract.schema.json",
        CONTRACT_ROOT / "operator-atomicity-review.json",
    ),
    "S1D-4": (
        CONTRACT_ROOT / "investigation-plan.schema.json",
        CONTRACT_ROOT / "result-set.schema.json",
        CONTRACT_ROOT / "evidence-graph.schema.json",
        CONTRACT_ROOT / "dual-model-answer-flow.schema.json",
        CONTRACT_ROOT / "runtime-commit-consistency-contract.json",
    ),
    "S1D-5": (
        CONTRACT_ROOT / "oracle.json",
        CONTRACT_ROOT / "cost-performance-budget.json",
        CONTRACT_ROOT / "model-alignment-evaluation.json",
        CONTRACT_ROOT / "product-semantic-review.json",
    ),
    "S1D-6": (
        CONTRACT_ROOT / "candidate.json",
        CONTRACT_ROOT / "acceptance-manifest.json",
    ),
}


class AlignmentError(RuntimeError):
    """带稳定错误码的设计对齐错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise AlignmentError(code, message)


def _regular_file(path: Path, *, code: str = "artifact_missing") -> None:
    if not path.exists():
        _fail(code, f"缺少必需文件：{path}")
    if path.is_symlink() or not path.is_file():
        _fail("unsafe_artifact", f"必需制品不是普通文件或被符号链接替换：{path}")


def _read_text(path: Path) -> str:
    _regular_file(path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        _fail("artifact_encoding_invalid", f"文件不是有效 UTF-8：{path}: {exc}")
    raise AssertionError("unreachable")


def _load_json(path: Path) -> Any:
    text = _read_text(path)

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(
                    "artifact_json_duplicate_key",
                    f"JSON 包含重复键，无法形成唯一规范化语义：{path}: {key}",
                )
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        _fail("artifact_json_invalid", f"JSON 无法解析：{path}: {exc}")
    raise AssertionError("unreachable")


def _sha256(path: Path) -> str:
    _regular_file(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("receipt_digest", None)
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _extract_block(text: str, begin: str, end: str) -> str:
    if text.count(begin) != 1 or text.count(end) != 1:
        _fail("document_marker_invalid", f"文档标记必须各出现一次：{begin} / {end}")
    start = text.index(begin) + len(begin)
    stop = text.index(end)
    if stop <= start:
        _fail("document_marker_invalid", f"文档标记顺序错误：{begin} / {end}")
    return text[start:stop]


def _table_ids(block: str, pattern: str) -> list[str]:
    result: list[str] = []
    matcher = re.compile(pattern)
    for line in block.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        match = matcher.search(line)
        if match:
            result.append(match.group(1))
    return result


def _validate_exact_ids(
    actual: Sequence[str], expected: Sequence[str], *, kind: str
) -> None:
    duplicate = sorted({item for item in actual if actual.count(item) > 1})
    if duplicate:
        _fail(f"{kind}_duplicate", f"{kind} 出现重复 ID：{', '.join(duplicate)}")
    actual_set = set(actual)
    expected_set = set(expected)
    if actual_set != expected_set or len(actual) != len(expected):
        missing = sorted(expected_set - actual_set)
        extra = sorted(actual_set - expected_set)
        _fail(
            f"{kind}_coverage_mismatch",
            f"{kind} 人口不闭合；missing={missing}, extra={extra}, "
            f"actual={len(actual)}, expected={len(expected)}",
        )


def _require_markers(
    text: str, markers: Iterable[str], *, code: str, subject: str
) -> None:
    missing = [marker for marker in markers if marker not in text]
    if missing:
        _fail(code, f"{subject} 缺少标记：{missing}")


def _validate_task_spec(text: str) -> list[str]:
    _require_markers(
        text,
        (
            "country-outage-agent-p2-s1-execution-unit-design-v1",
            "28 题覆盖必须为 28/28。Tool 设计和 Operator 设计必须分阶段。",
            "设计完成不等于运行时实现",
            "生产部署：禁止。远程写入：禁止。运行时实现：本任务不执行。",
            "peak_invisible_direction_count DESC",
            "peak_complete_prefix_count DESC",
            "asn ASC",
        ),
        code="task_spec_marker_missing",
        subject="Task Spec",
    )
    for marker in FUNCTION_ATOMICITY_MARKERS:
        if marker not in text:
            _fail("function_atomicity_marker_missing", f"Task Spec 缺少功能原子性标记：{marker}")
    for marker in RUNTIME_COMMIT_CONSISTENCY:
        if marker not in text:
            _fail("commit_consistency_marker_missing", f"Task Spec 缺少提交一致性标记：{marker}")
    for marker in DUAL_MODEL_MARKERS:
        if marker not in text:
            _fail("dual_model_marker_missing", f"Task Spec 缺少双模型标记：{marker}")
    teacher_step = "→ gpt-5.6-sol 生成 TeacherSemanticPlan"
    student_step = "→ DS 使用同一 GroundingPlan"
    if (
        teacher_step not in text
        or student_step not in text
        or text.index(teacher_step) >= text.index(student_step)
    ):
        _fail(
            "model_execution_order_drift",
            "回答流程必须先执行 gpt-5.6-sol Teacher，再执行 DS Student",
        )

    question_block = _extract_block(
        text, "<!-- QUESTION_MAP_BEGIN -->", "<!-- QUESTION_MAP_END -->"
    )
    questions = _table_ids(question_block, r"\|\s*(Q\d{2})\s*\|")
    _validate_exact_ids(questions, EXPECTED_QUESTIONS, kind="question")

    unit_block = _extract_block(
        text,
        "<!-- EXECUTION_UNIT_MAP_BEGIN -->",
        "<!-- EXECUTION_UNIT_MAP_END -->",
    )
    units = _table_ids(
        unit_block,
        r"\|\s*`?((?:TOOL|OP|GATE|RENDERER)-\d{2}|PLAN-CAP-\d{2}|BOUNDARY-\d{2}|DELIVERY-\d{2})\b",
    )
    _validate_exact_ids(units, EXPECTED_EXECUTION_UNITS, kind="execution_unit")
    return [
        "task_spec_version",
        "question_population_28_of_28",
        "execution_unit_population",
        "as_sort_contract",
        "execution_unit_function_atomicity",
        "runtime_commit_consistency_separated",
        "sol_then_ds_evidence_grounded_flow",
        "design_only_boundary",
    ]


def _validate_phase_plan(text: str) -> list[str]:
    _require_markers(
        text,
        (
            "country-outage-agent-p2-s1-execution-unit-design-plan-v1",
            "S1D-2 只设计 Tool，S1D-3 只设计 Operator；不得合并、倒序或并行退出。",
            "execution_unit_function_atomicity",
            "atomic_split_test",
            "sol_then_ds_execution_order",
            "evidence_truth_precedes_teacher",
            "design_only=true",
            "runtime_implemented=false",
            "production_deployed=false",
            "最终检查阶段别名为 `final`",
        ),
        code="plan_marker_missing",
        subject="阶段计划",
    )

    headings: dict[str, int] = {}
    for stage in STAGES:
        match = re.search(rf"^##\s+[^\n]*{re.escape(stage)}[^\n]*$", text, re.MULTILINE)
        if match is None:
            _fail("plan_stage_missing", f"阶段计划缺少独立标题：{stage}")
        headings[stage] = match.start()
    if [headings[stage] for stage in STAGES] != sorted(headings.values()):
        _fail("plan_stage_order_invalid", "S1D-0 至 S1D-6 的标题顺序错误")

    tool_heading = re.search(r"^##\s+[^\n]*S1D-2：Tool 设计\s*$", text, re.MULTILINE)
    operator_heading = re.search(
        r"^##\s+[^\n]*S1D-3：Operator 设计\s*$", text, re.MULTILINE
    )
    if tool_heading is None or operator_heading is None:
        _fail(
            "tool_operator_stage_separation_missing",
            "Tool 与 Operator 必须拥有独立且固定的 S1D-2/S1D-3 标题",
        )
    if tool_heading.start() >= operator_heading.start():
        _fail(
            "tool_operator_stage_separation_missing",
            "Tool 设计必须先于 Operator 设计退出",
        )
    return [
        "phase_plan_version",
        "seven_stage_sequence",
        "tool_operator_stage_separation",
        "stage_atomic_candidate_rule",
        "design_only_receipt_rule",
    ]


def _object_list_ids(payload: Any, key: str, id_key: str, path: Path) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        _fail("artifact_schema_invalid", f"{path} 必须包含数组字段 {key}")
    ids: list[str] = []
    for index, item in enumerate(payload[key]):
        if not isinstance(item, dict) or not isinstance(item.get(id_key), str):
            _fail(
                "artifact_schema_invalid",
                f"{path} 的 {key}[{index}] 缺少字符串字段 {id_key}",
            )
        ids.append(item[id_key])
    return ids


def _validate_schema_file(payload: Any, path: Path) -> None:
    if not isinstance(payload, dict):
        _fail("artifact_schema_invalid", f"schema 必须是 JSON object：{path}")
    if "$schema" not in payload or not isinstance(payload.get("type"), (str, list)):
        _fail("artifact_schema_invalid", f"schema 缺少 $schema/type：{path}")


def _validate_s1d1(repo_root: Path) -> list[str]:
    map_path = repo_root / ARTIFACTS_BY_STAGE["S1D-1"][0]
    oracle_path = repo_root / ARTIFACTS_BY_STAGE["S1D-1"][1]
    decomposition_path = repo_root / ARTIFACTS_BY_STAGE["S1D-1"][2]
    model_role_path = repo_root / ARTIFACTS_BY_STAGE["S1D-1"][3]
    capability_map = _load_json(map_path)
    map_ids = _object_list_ids(capability_map, "questions", "question_id", map_path)
    oracle = _load_json(oracle_path)
    oracle_ids = _object_list_ids(oracle, "questions", "question_id", oracle_path)
    _validate_exact_ids(map_ids, EXPECTED_QUESTIONS, kind="capability_question")
    _validate_exact_ids(oracle_ids, EXPECTED_QUESTIONS, kind="oracle_seed_question")

    capability_ids = _object_list_ids(
        capability_map, "capabilities", "capability_id", map_path
    )
    _validate_exact_ids(
        capability_ids, EXPECTED_CAPABILITIES, kind="capability"
    )
    capability_set = set(capability_ids)
    capabilities_by_id = {
        item["capability_id"]: item for item in capability_map["capabilities"]
    }
    allowed_units = set(EXISTING_EXECUTION_UNITS + EXPECTED_EXECUTION_UNITS)
    referenced_capabilities: list[str] = []
    for index, capability in enumerate(capability_map["capabilities"]):
        unit_ids = capability.get("unit_ids")
        if not isinstance(unit_ids, list) or not unit_ids or any(
            not isinstance(unit_id, str) for unit_id in unit_ids
        ):
            _fail(
                "artifact_schema_invalid",
                f"{map_path} capabilities[{index}] 缺少 unit_ids",
            )
        unknown_units = sorted(set(unit_ids) - allowed_units)
        if unknown_units:
            _fail(
                "capability_unit_unknown",
                f"{capability.get('capability_id')} 引用未知执行单元：{unknown_units}",
            )
        for field in ("atomic_action", "source_population", "output_semantic"):
            if not isinstance(capability.get(field), str) or not capability[field].strip():
                _fail(
                    "capability_contract_incomplete",
                    f"{capability.get('capability_id')} 缺少 {field}",
                )
    for question in capability_map["questions"]:
        refs = question.get("required_capability_ids")
        optional = question.get("optional_capability_ids", [])
        if not isinstance(refs, list) or not refs or not isinstance(optional, list):
            _fail(
                "question_capability_mapping_invalid",
                f"{question.get('question_id')} 缺少 required_capability_ids",
            )
        combined = refs + optional
        if any(not isinstance(item, str) for item in combined):
            _fail(
                "question_capability_mapping_invalid",
                f"{question.get('question_id')} capability 引用必须为字符串",
            )
        missing = sorted(set(combined) - capability_set)
        if missing:
            _fail(
                "question_capability_reference_missing",
                f"{question.get('question_id')} 引用未声明 capability：{missing}",
            )
        referenced_capabilities.extend(combined)
    unused = sorted(capability_set - set(referenced_capabilities))
    if unused:
        _fail("unused_capability", f"未被 28 题使用的 capability：{unused}")
    q24 = next(item for item in capability_map["questions"] if item["question_id"] == "Q24")
    if q24.get("answerability") != "deferred_p2_1":
        _fail("deferred_boundary_missing", "Q24 必须标记 answerability=deferred_p2_1")
    required_source_populations = {
        "CAP-P2-011": "fixed_cohort_member_rows",
        "CAP-P2-015": "materialized_route_state_rows_at_exact_time",
        "CAP-P2-016": "window_path_association_evidence_rows",
        "CAP-P2-029": "one_structured_path_and_op15_position_receipt",
        "CAP-P2-030": "two_op15_position_receipts_with_same_path_digest",
        "CAP-P2-032": "complete_anchor_before_known_origin_path_association_set",
        "CAP-P2-042": "two_typed_timed_facts_and_comparability_profile",
    }
    for capability_id, expected_population in required_source_populations.items():
        if capabilities_by_id[capability_id].get("source_population") != expected_population:
            _fail(
                "review_semantic_fix_missing",
                f"{capability_id} source_population 必须为 {expected_population}",
            )
    if capabilities_by_id["CAP-P2-015"].get("disposition") != "new_p2_v1_source_view_required":
        _fail(
            "route_state_source_view_boundary_missing",
            "TOOL-11 必须显式声明 source_view_required",
        )
    questions_by_id = {item["question_id"]: item for item in capability_map["questions"]}
    required_question_capabilities = {
        "Q06": {"CAP-P2-059"},
        "Q08": {"CAP-P2-060"},
        "Q16": {"CAP-P2-021", "CAP-P2-048"},
        "Q18": {"CAP-P2-011"},
        "Q21": {"CAP-P2-028", "CAP-P2-029"},
        "Q22": {"CAP-P2-036"},
        "Q23": {"CAP-P2-003", "CAP-P2-007", "CAP-P2-048"},
        "Q31": {"CAP-P2-061"},
        "Q32": {"CAP-P2-061"},
    }
    for question_id, expected_refs in required_question_capabilities.items():
        actual_refs = set(questions_by_id[question_id]["required_capability_ids"])
        missing_refs = sorted(expected_refs - actual_refs)
        if missing_refs:
            _fail(
                "question_capability_review_gap",
                f"{question_id} 缺少独立审查要求的 capability：{missing_refs}",
            )

    decomposition = _load_json(decomposition_path)
    if not isinstance(decomposition, dict) or not isinstance(
        decomposition.get("decisions"), list
    ):
        _fail("artifact_schema_invalid", f"{decomposition_path} 必须包含 decisions")
    required_splits = {
        "query_entity_states": {"TOOL-07", "TOOL-08", "TOOL-09", "TOOL-10"},
        "entity_time_join": {"PLAN-CAP-01", "OP-33"},
        "prefix_state_transition": {"OP-06", "OP-07", "OP-08", "OP-09"},
        "observed_path_structure": {"OP-15", "OP-16", "OP-17", "OP-18", "OP-19"},
        "set_relation": {"OP-25", "OP-26", "OP-27", "OP-28"},
        "evidence_graph_validator": {"GATE-01", "GATE-02", "GATE-03", "GATE-04", "GATE-05"},
        "export_result_set": {"RENDERER-01", "RENDERER-02", "RENDERER-03", "DELIVERY-01"},
    }
    decisions = {
        item.get("candidate_id"): item
        for item in decomposition["decisions"]
        if isinstance(item, dict)
    }
    for candidate_id, replacements in required_splits.items():
        item = decisions.get(candidate_id)
        if item is None or item.get("disposition") != "split_required":
            _fail(
                "atomic_decomposition_missing",
                f"复合候选必须标记 split_required：{candidate_id}",
            )
        if set(item.get("replacement_unit_ids", [])) != replacements:
            _fail(
                "atomic_decomposition_mismatch",
                f"复合候选拆分人口错误：{candidate_id}",
            )
    unit_ids = _object_list_ids(
        decomposition, "atomic_units", "unit_id", decomposition_path
    )
    _validate_exact_ids(unit_ids, EXPECTED_EXECUTION_UNITS, kind="decomposed_unit")
    atomic_capability_ids: list[str] = []
    for item in decomposition["atomic_units"]:
        for field in (
            "atomic_capability_id",
            "single_responsibility",
            "source_population",
            "output_semantic",
            "disposition",
        ):
            if not isinstance(item.get(field), str) or not item[field].strip():
                _fail(
                    "execution_unit_atomicity_invalid",
                    f"{item.get('unit_id')} 缺少 {field}",
                )
        atomic_capability_ids.append(item["atomic_capability_id"])
    if len(atomic_capability_ids) != len(set(atomic_capability_ids)):
        _fail("atomic_capability_duplicate", "atomic_capability_id 必须全局唯一")
    units_by_id = {item["unit_id"]: item for item in decomposition["atomic_units"]}
    required_unit_populations = {
        "TOOL-07": "fixed_cohort_member_rows",
        "TOOL-11": "materialized_route_state_rows_at_exact_time",
        "TOOL-12": "window_path_association_evidence_rows",
        "OP-16": "one_structured_path_and_op15_position_receipt",
        "OP-17": "two_op15_position_receipts_with_same_path_digest",
        "OP-19": "complete_anchor_before_known_origin_path_association_set",
        "OP-29": "two_typed_timed_facts_and_comparability_profile",
    }
    for unit_id, expected_population in required_unit_populations.items():
        if units_by_id[unit_id].get("source_population") != expected_population:
            _fail(
                "review_semantic_fix_missing",
                f"{unit_id} source_population 必须为 {expected_population}",
            )
    common = decomposition.get("atomic_unit_common_contract")
    if not isinstance(common, dict):
        _fail("execution_unit_atomicity_invalid", "缺少 atomic_unit_common_contract")
    if common.get("embedded_capabilities") != []:
        _fail("composite_execution_unit_forbidden", "原子单元不得内嵌 capability")
    if common.get("composition_location") != "investigation_plan_or_host_pipeline":
        _fail("execution_unit_internal_composition_forbidden", "组合位置合同漂移")
    if common.get("partial_success_forbidden") is not True:
        _fail("atomic_failure_boundary_missing", "原子执行单元必须禁止内部部分成功")
    split_test = common.get("split_test")
    if not isinstance(split_test, dict) or split_test.get("disposition") != "atomic_as_designed":
        _fail("atomic_split_test_failed", "S1D-1 原子人口未通过 atomic_split_test")
    if any(
        split_test.get(field) is not False
        for field in (
            "contains_multiple_business_verbs",
            "subcapabilities_independently_reusable",
            "mode_changes_population_or_semantic",
            "partial_subresult_independently_meaningful",
            "internally_invokes_another_execution_unit",
        )
    ):
        _fail("atomic_split_test_failed", "atomic_split_test 仍检测到可拆分能力")
    coverage = decomposition.get("coverage_assertions")
    if not isinstance(coverage, dict):
        _fail("artifact_schema_invalid", "缺少 coverage_assertions")
    _validate_exact_ids(
        coverage.get("design_covered_question_ids", []),
        EXPECTED_QUESTIONS,
        kind="design_covered_question",
    )
    if coverage.get("p2_v1_not_executable_question_ids") != ["Q24"]:
        _fail("deferred_boundary_missing", "Q24 必须设计覆盖但 P2 v1 不可执行")
    if coverage.get("runtime_ready_claim") is not False:
        _fail("runtime_claim_forbidden", "S1D-1 不得声称 runtime ready")

    if set(oracle.get("scenario_classes", [])) != set(S1D1_SCENARIOS):
        _fail("oracle_seed_scenario_coverage_mismatch", "Oracle seed 场景人口漂移")
    map_answerability = {
        item["question_id"]: item.get("answerability")
        for item in capability_map["questions"]
    }
    for item in oracle["questions"]:
        question_id = item["question_id"]
        if item.get("answerability") != map_answerability[question_id]:
            _fail(
                "oracle_seed_answerability_mismatch",
                f"{question_id} 的 Oracle 与能力图 answerability 不一致",
            )
        scenarios = item.get("scenario_expectations")
        if not isinstance(scenarios, dict) or set(scenarios) != set(S1D1_SCENARIOS):
            _fail(
                "oracle_seed_scenario_coverage_mismatch",
                f"{question_id} 场景人口不闭合",
            )
        if not isinstance(item.get("required_assertions"), list) or not isinstance(
            item.get("prohibited_assertions"), list
        ):
            _fail("artifact_schema_invalid", f"{question_id} 缺少断言 Oracle seed")

    model_roles = _load_json(model_role_path)
    if not isinstance(model_roles, dict):
        _fail("model_role_contract_invalid", "model-role-contract 必须是 JSON object")
    if model_roles.get("execution_order") != ["gpt-5.6-sol", "ds_student"]:
        _fail(
            "model_execution_order_drift",
            "模型执行顺序必须为 gpt-5.6-sol 后 ds_student",
        )
    if model_roles.get("teacher_required") is not True:
        _fail("teacher_required_drift", "teacher_required 必须为 true")
    if model_roles.get("truth_source") != "validated_evidence_bundle":
        _fail("teacher_truth_conflation", "真值来源必须是 validated_evidence_bundle")
    if model_roles.get("teacher_reference_is_ground_truth") is not False:
        _fail("teacher_truth_conflation", "TeacherReference 不得标记为 ground truth")
    ds_identity = model_roles.get("ds_student_identity")
    if not isinstance(ds_identity, dict):
        _fail("ds_model_identity_contract_missing", "缺少 ds_student_identity")
    if ds_identity.get("logical_alias") != "ds_student":
        _fail("ds_model_identity_contract_missing", "DS 逻辑别名必须为 ds_student")
    if ds_identity.get("freeze_exact_model_by_stage") != "S1D-5":
        _fail("ds_model_identity_contract_missing", "DS 精确模型身份必须在 S1D-5 前冻结")
    shared_binding = model_roles.get("shared_answer_binding")
    if not isinstance(shared_binding, dict):
        _fail("shared_answer_binding_missing", "缺少 shared_answer_binding")
    for field in (
        "same_grounding_plan_required",
        "same_evidence_bundle_required",
        "same_registry_snapshot_required",
        "same_publication_required",
    ):
        if shared_binding.get(field) is not True:
            _fail("shared_answer_binding_drift", f"{field} 必须为 true")
    required_binding_fields = set(shared_binding.get("required_fields", []))
    for field in (
        "grounding_plan_digest",
        "evidence_bundle_digest",
        "registry_snapshot_digest",
        "prompt_digest",
        "policy_digest",
    ):
        if field not in required_binding_fields:
            _fail("shared_answer_binding_missing", f"共享回答绑定缺少 {field}")
    design_boundary = model_roles.get("design_boundary")
    if not isinstance(design_boundary, dict) or any(
        design_boundary.get(field) is not False
        for field in (
            "model_calls_implemented",
            "ds_identity_frozen",
            "runtime_integrated",
            "production_deployed",
        )
    ):
        _fail("model_role_boundary_drift", "S1D-1 模型角色不得声称已运行或已部署")
    return [
        "question_capability_closure",
        "question_oracle_seed_closure",
        "execution_unit_atomic_decomposition",
        "q24_design_covered_runtime_deferred",
        "shared_answer_binding_contract",
        "sol_teacher_ds_student_role_contract",
    ]


def _validate_atomic_unit_records(
    records: Sequence[Any],
    *,
    expected_ids: Sequence[str],
    population_key: str,
    kind: str,
) -> None:
    ids: list[str] = []
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            _fail("execution_unit_atomicity_invalid", f"{kind}[{index}] 必须是 object")
        unit_id = item.get("unit_id")
        if not isinstance(unit_id, str):
            _fail("execution_unit_atomicity_invalid", f"{kind}[{index}] 缺少 unit_id")
        ids.append(unit_id)
        for field in ("atomic_capability_id", "single_responsibility", population_key):
            if not isinstance(item.get(field), str) or not item[field].strip():
                _fail(
                    "execution_unit_atomicity_invalid",
                    f"{unit_id} 缺少功能原子字段 {field}",
                )
        if item.get("composition_location") != "investigation_plan":
            _fail(
                "execution_unit_internal_composition_forbidden",
                f"{unit_id} 的组合只能发生在 investigation_plan",
            )
        embedded = item.get("embedded_capabilities")
        if embedded != []:
            _fail(
                "composite_execution_unit_forbidden",
                f"{unit_id} 内嵌其他能力：{embedded}",
            )
        split_test = item.get("split_test")
        if not isinstance(split_test, dict) or split_test.get("disposition") != "atomic_as_designed":
            _fail(
                "atomic_split_test_failed",
                f"{unit_id} 未通过 atomic_split_test",
            )
    _validate_exact_ids(ids, expected_ids, kind=kind)


def _validate_atomicity_review(
    payload: Any, *, expected_ids: Sequence[str], path: Path, kind: str
) -> None:
    ids = _object_list_ids(payload, "reviews", "unit_id", path)
    _validate_exact_ids(ids, expected_ids, kind=f"{kind}_atomicity_review")
    for item in payload["reviews"]:
        if item.get("disposition") != "atomic_as_designed":
            _fail("atomicity_review_failed", f"{item.get('unit_id')} 未通过功能原子性审查")
        if not item.get("reviewer_id"):
            _fail("reviewer_identity_missing", f"{item.get('unit_id')} 缺少原子性 reviewer_id")


def _validate_s1d2(repo_root: Path) -> list[str]:
    catalog_path = repo_root / ARTIFACTS_BY_STAGE["S1D-2"][0]
    schema_path = repo_root / ARTIFACTS_BY_STAGE["S1D-2"][1]
    review_path = repo_root / ARTIFACTS_BY_STAGE["S1D-2"][2]
    payload = _load_json(catalog_path)
    if not isinstance(payload, dict) or not isinstance(payload.get("tools"), list):
        _fail("artifact_schema_invalid", f"{catalog_path} 必须包含 tools")
    _validate_atomic_unit_records(
        payload["tools"],
        expected_ids=EXPECTED_TOOLS,
        population_key="output_population",
        kind="tool",
    )
    tools_by_id = {item["unit_id"]: item for item in payload["tools"]}
    for tool in payload["tools"]:
        unit_id = tool["unit_id"]
        if tool.get("source_population") != tool.get("output_population"):
            _fail(
                "tool_population_drift",
                f"{unit_id} 的读取人口与输出人口必须相同",
            )
        for field in (
            "source_schema_ref",
            "source_readiness",
            "input_contract",
            "input_field_schemas",
            "member_identity",
            "output_member_fields",
            "output_field_schemas",
            "stable_sort",
            "dedupe_key",
            "pagination_contract_ref",
            "evidence_contract",
            "forbidden_embedded_actions",
            "forbidden_conclusions",
            "question_refs",
        ):
            if field not in tool or tool[field] in (None, "", []):
                _fail("tool_contract_incomplete", f"{unit_id} 缺少 {field}")
        if tool.get("pagination_contract_ref") != "#/common_result_set_contract":
            _fail("tool_pagination_contract_drift", f"{unit_id} 分页合同引用漂移")
        input_contract = tool["input_contract"]
        if input_contract.get("optional") != ["page_token"]:
            _fail("tool_page_token_contract_missing", f"{unit_id} 缺少可选 page_token")
        if input_contract.get("page_size_min") != 1:
            _fail("tool_pagination_contract_drift", f"{unit_id} page_size_min 必须为 1")
        if tool.get("runtime_ready_claim") is not False:
            _fail("runtime_claim_forbidden", f"{unit_id} 不得声称 runtime ready")
        if set(input_contract.get("optional_filters", [])) - set(
            tool["input_field_schemas"]
        ):
            _fail("tool_typed_input_incomplete", f"{unit_id} filter 缺少 typed schema")
        if set(tool["output_member_fields"]) - set(tool["output_field_schemas"]):
            _fail("tool_typed_output_incomplete", f"{unit_id} output 缺少 typed schema")
        split_test = tool.get("split_test", {})
        if split_test.get("business_read_count") != 1 or split_test.get(
            "fact_population_count"
        ) != 1:
            _fail("atomic_split_test_failed", f"{unit_id} 不是单一事实人口读取")
        for forbidden_false in (
            "mode_changes_population",
            "independently_publishable_subreads",
            "internally_invokes_other_units",
        ):
            if split_test.get(forbidden_false) is not False:
                _fail("atomic_split_test_failed", f"{unit_id} 的 {forbidden_false} 漂移")
    tool07 = tools_by_id["TOOL-07"]
    if tool07.get("source_population") != "fixed_cohort_member_rows" or (
        "expected_peer_asn_direction_ids" not in tool07.get("output_member_fields", [])
        or "expected_route_observation_keys" not in tool07.get("output_member_fields", [])
    ):
        _fail("expected_direction_population_missing", "TOOL-07 必须发布原生 expected directions")
    tool11 = tools_by_id["TOOL-11"]
    if (
        tool11.get("source_population")
        != "materialized_route_state_rows_at_exact_time"
        or tool11.get("disposition") != "new_p2_v1_source_view_required"
    ):
        _fail("route_state_source_view_boundary_missing", "TOOL-11 必须只读预物化时点人口")
    tool11_forbidden = set(tool11.get("forbidden_embedded_actions", []))
    if not {"select_checkpoint", "replay_route_events", "project_route_state"}.issubset(
        tool11_forbidden
    ):
        _fail("route_state_internal_replay_forbidden", "TOOL-11 未禁止内部回放或投影")
    required_route_state_key = {
        "publication_id",
        "state_point_utc",
        "collector_id",
        "vp_id",
        "peer_id",
        "prefix",
        "afi",
    }
    if set(tool11.get("member_identity", [])) != required_route_state_key:
        _fail("route_state_member_identity_drift", "TOOL-11 必须保留 VP/peer 级事实键")
    path_segments_schema = tool11.get("output_field_schemas", {}).get(
        "path_segments", {}
    )
    if "https://domeye.example/contracts/data/route-event.schema.json#/$defs/asPathSegment" not in json.dumps(
        path_segments_schema, sort_keys=True
    ):
        _fail("typed_path_segment_schema_missing", "TOOL-11 必须保留类型化 AS_PATH segment")
    if not isinstance(tool11.get("output_row_constraints"), dict):
        _fail("route_state_cross_field_constraints_missing", "TOOL-11 缺少状态联动约束")
    tool12 = tools_by_id["TOOL-12"]
    if (
        tool12.get("source_population") != "window_path_association_evidence_rows"
        or tool12.get("time_semantics") != "window_level_association_not_path_at_time"
    ):
        _fail("path_association_time_semantic_drift", "TOOL-12 不得冒充 path-at-time")
    if "preview" not in tool12.get("forbidden_embedded_actions", []):
        _fail("tool_preview_boundary_missing", "TOOL-12 必须禁止内嵌预览")
    domain = tool12.get("association_population_domain")
    if not isinstance(domain, dict) or domain.get("anchor_population") != (
        "ever_affected_asns_registered_by_bound_publication"
    ):
        _fail("path_association_domain_missing", "TOOL-12 必须冻结完整性人口域")
    tool12_fields = tool12.get("output_field_schemas", {})
    if tool12_fields.get("route_observation_count", {}).get("minimum") != 1:
        _fail("path_association_count_invalid", "TOOL-12 关联行必须至少有一次观测")
    if tool12_fields.get("path_segments", {}).get("minItems") != 1:
        _fail("empty_ordered_path_allowed", "TOOL-12 不得允许空路径进入路径Operator")
    if "https://domeye.example/contracts/data/route-event.schema.json#/$defs/asPathSegment" not in json.dumps(
        tool12_fields.get("path_segments", {}), sort_keys=True
    ):
        _fail("typed_path_segment_schema_missing", "TOOL-12 必须保留类型化 AS_PATH segment")
    deferred = [
        item
        for item in payload["tools"]
        if item.get("unit_id") == "TOOL-13" and item.get("disposition") == "deferred_p2_1"
    ]
    if len(deferred) != 1:
        _fail("deferred_boundary_missing", "TOOL-13 必须标记 disposition=deferred_p2_1")
    tool13 = tools_by_id["TOOL-13"]
    adapter = tool13.get("source_adapter_mapping")
    if not isinstance(adapter, dict) or adapter.get("event_time_utc") != "event_time_utc":
        _fail("route_event_adapter_drift", "TOOL-13 必须直接复用 event_time_utc")
    if adapter.get("vp_id") != "vp_id" or adapter.get("path_segments") != "as_path.segments":
        _fail("route_event_adapter_drift", "TOOL-13 不得重算 vp_id 或拍平 path segment")
    population_contract = tool13.get("source_population_contract")
    if not isinstance(population_contract, dict) or population_contract.get(
        "record_kind"
    ) != "route_event":
        _fail("route_event_population_unclosed", "TOOL-13 必须排除历史占位和RIB快照")
    _validate_schema_file(_load_json(schema_path), schema_path)
    schema = _load_json(schema_path)
    schema_required = set(schema.get("required", []))
    required_schema_fields = {
        "source_population",
        "output_population",
        "input_contract",
        "input_field_schemas",
        "member_identity",
        "stable_sort",
        "dedupe_key",
        "evidence_contract",
        "output_field_schemas",
        "forbidden_embedded_actions",
        "split_test",
        "runtime_ready_claim",
    }
    if not required_schema_fields.issubset(schema_required):
        _fail(
            "tool_contract_schema_incomplete",
            f"Tool schema 缺少 required 字段：{sorted(required_schema_fields - schema_required)}",
        )
    if schema.get("additionalProperties") is not False:
        _fail("tool_contract_schema_open", "Tool schema 顶层必须 additionalProperties=false")
    input_schema = schema.get("properties", {}).get("input_contract", {})
    if input_schema.get("additionalProperties") is not False:
        _fail("tool_contract_schema_open", "Tool input schema 必须封闭")
    completeness = set(
        payload.get("common_result_set_contract", {}).get("set_completeness_enum", [])
    )
    if completeness != {"complete", "partial_page", "source_incomplete"}:
        _fail("tool_result_completeness_drift", "audit_only/unavailable 不得生成 ResultSet")
    review = _load_json(review_path)
    _validate_atomicity_review(
        review,
        expected_ids=EXPECTED_TOOLS,
        path=review_path,
        kind="tool",
    )
    if review.get("overall_disposition") != "passed" or review.get(
        "remaining_blockers"
    ) != []:
        _fail("atomicity_review_failed", "Tool 独立审查仍有阻断项")
    boundary = review.get("boundary")
    if not isinstance(boundary, dict) or boundary.get("runtime_implemented") is not False:
        _fail("runtime_claim_forbidden", "Tool 审查不得声称运行时已实现")
    return [
        "tool_catalog_closure",
        "tool_13_deferred",
        "tool_contract_schema",
        "tool_function_atomicity",
        "tool_source_readiness_boundary",
        "route_state_materialized_view_boundary",
        "window_path_association_semantics",
    ]


def _validate_s1d3(repo_root: Path) -> list[str]:
    catalog_path = repo_root / ARTIFACTS_BY_STAGE["S1D-3"][0]
    schema_path = repo_root / ARTIFACTS_BY_STAGE["S1D-3"][1]
    review_path = repo_root / ARTIFACTS_BY_STAGE["S1D-3"][2]
    payload = _load_json(catalog_path)
    if not isinstance(payload, dict) or not isinstance(payload.get("operators"), list):
        _fail("artifact_schema_invalid", f"{catalog_path} 必须包含 operators")
    _validate_atomic_unit_records(
        payload["operators"],
        expected_ids=EXPECTED_OPERATORS,
        population_key="output_semantic",
        kind="operator",
    )
    scope = payload.get("scope")
    if not isinstance(scope, dict):
        _fail("operator_scope_missing", "Operator catalog 缺少设计边界")
    expected_scope_flags = {
        "pure_deterministic_transforms_only": True,
        "external_data_forbidden": True,
        "model_inference_forbidden": True,
        "runtime_implemented": False,
        "production_deployed": False,
    }
    for field, expected in expected_scope_flags.items():
        if scope.get(field) is not expected:
            _fail("operator_scope_drift", f"Operator scope 漂移：{field}")
    common_execution = payload.get("common_execution_contract")
    if not isinstance(common_execution, dict):
        _fail("operator_execution_contract_missing", "缺少纯函数执行合同")
    for field, expected in {
        "pure_function": True,
        "network_access": False,
        "source_read": False,
        "state_mutation": False,
        "clock_read": False,
        "randomness": False,
        "partial_success_publishable": False,
    }.items():
        if common_execution.get(field) is not expected:
            _fail("operator_purity_drift", f"Operator 纯函数边界漂移：{field}")
    common_types = payload.get("common_typed_fields")
    if not isinstance(common_types, dict) or not common_types:
        _fail("operator_type_closure_missing", "缺少 common_typed_fields")
    profiles = payload.get("parameter_profiles")
    if not isinstance(profiles, list):
        _fail("operator_profile_missing", "缺少 parameter_profiles")
    profile_ids = _object_list_ids(
        payload, "parameter_profiles", "profile_id", catalog_path
    )
    expected_profiles = {
        "PROFILE-AS-SEVERITY-RANK-1.0.0",
        "PROFILE-STATE-TARGET-1.0.0",
        "PROFILE-STATE-INTERVAL-1.0.0",
        "PROFILE-PEAK-SEVERITY-1.0.0",
        "PROFILE-FIRST-CROSSING-1.0.0",
        "PROFILE-TEMPORAL-COMPARABILITY-1.0.0",
        "PROFILE-EVIDENCE-CONSISTENCY-1.0.0",
        "PROFILE-VP-CONSISTENCY-1.0.0",
        "PROFILE-ROUTE-CHANGE-1.0.0",
    }
    if set(profile_ids) != expected_profiles or len(profile_ids) != len(expected_profiles):
        _fail("operator_profile_population_drift", "Operator parameter profile 人口漂移")
    profiles_by_id = {item["profile_id"]: item for item in profiles}
    expected_profile_contract_digests = {
        "PROFILE-AS-SEVERITY-RANK-1.0.0": "012bf52458d4115c97c52716635345d9a64b79bf964aac8f7cbe4c433af103b2",
        "PROFILE-STATE-TARGET-1.0.0": "84e62744a3e688c2346c7062ac6d1d9367fa8786652b0e1a6a39e2608a0ea5b3",
        "PROFILE-STATE-INTERVAL-1.0.0": "907c6a44b300a4c105ff41f30bf737e4d8c8454d988d4192171f0131a0f50cf5",
        "PROFILE-PEAK-SEVERITY-1.0.0": "bff91f99198f646913b9bf169c700c1a4c08091fd17bb154edbbeaf1d94eaecc",
        "PROFILE-FIRST-CROSSING-1.0.0": "f16290ec21950def8dd11597764b3bc90d196aa38f47e2b469caa6d2b3997173",
        "PROFILE-TEMPORAL-COMPARABILITY-1.0.0": "00799ab935da941491819473bd1f96d34b59ac3a853136fce92f0ed355f91ef8",
        "PROFILE-EVIDENCE-CONSISTENCY-1.0.0": "e3d92bfdafec1d6f68d48ff62b95267e87fe7009b6e55eded06b7c7765ac7661",
        "PROFILE-VP-CONSISTENCY-1.0.0": "1877cab88025d964e9c42a634b9bfe35d1039bb05870b2c1947c8274e89b9e70",
        "PROFILE-ROUTE-CHANGE-1.0.0": "09931100517e16b985176c87446aec6ffabc509af1c8777a5ff0eff8e76a2b74",
    }
    for profile_id, profile in profiles_by_id.items():
        if set(profile) != {"profile_id", "profile_contract_digest", "purpose", "parameters"}:
            _fail("operator_profile_unfrozen", f"{profile_id} 合同字段不闭合")
        profile_body = dict(profile)
        declared_digest = profile_body.pop("profile_contract_digest")
        computed_digest = hashlib.sha256(
            json.dumps(
                profile_body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if declared_digest != computed_digest or declared_digest != (
            expected_profile_contract_digests[profile_id]
        ):
            _fail("operator_profile_unfrozen", f"{profile_id} 内容或固定摘要漂移")
    profile_binding = payload.get("profile_binding_contract")
    expected_profile_instance_paths = {
        "OP-29": "inputs.comparability_profile.profile_digest",
        "OP-36": "inputs.threshold_profile_instance.profile_digest",
        "OP-37": "inputs.consistency_profile.profile_digest",
    }
    if not isinstance(profile_binding, dict) or (
        profile_binding.get("contract_id") != "P2-S1-PROFILE-BINDING-1.0.0"
        or profile_binding.get("no_profile_invariant_id")
        != "PROFILE-BINDING-NONE-1.0.0"
        or profile_binding.get("profile_digest_equality_invariant_id")
        != "PROFILE-BINDING-DIGEST-EQUALITY-1.0.0"
        or profile_binding.get("profile_instance_input_paths")
        != expected_profile_instance_paths
        or profile_binding.get("profile_result_output_paths")
        != {unit_id: "result.profile_digest" for unit_id in expected_profile_instance_paths}
        or profile_binding.get("runtime_validator_contract_id")
        != "VALIDATOR-P2-S1-PROFILE-BINDING-1.0.0"
        or profile_binding.get("runtime_validator_implemented") is not False
    ):
        _fail("operator_profile_binding_open", "Profile ID、实例摘要和Envelope绑定合同未闭合")
    profile_binding_rules = profile_binding.get("rules", [])
    if not isinstance(profile_binding_rules, list) or not all(
        marker in "\n".join(profile_binding_rules)
        for marker in (
            "当且仅当",
            "输入Envelope与输出Envelope",
            "OP-29",
            "OP-36",
            "OP-37",
            "不得发布Evidence",
        )
    ):
        _fail("operator_profile_binding_open", "Profile绑定不变量规则不完整")
    first_crossing_profile = profiles_by_id["PROFILE-FIRST-CROSSING-1.0.0"].get(
        "parameters", {}
    )
    if first_crossing_profile.get("grid_step_seconds") != 300 or set(
        first_crossing_profile.get("profile_instance_required_fields", [])
    ) != {
        "metric_field",
        "threshold_exact",
        "comparison",
        "grid_step_seconds",
        "gap_policy",
    } or "indeterminate" not in first_crossing_profile.get("gap_policy", ""):
        _fail("threshold_profile_unfrozen", "首次越阈Profile未冻结参数和缺口规则")
    temporal_profile = profiles_by_id[
        "PROFILE-TEMPORAL-COMPARABILITY-1.0.0"
    ].get("parameters", {})
    if temporal_profile.get("tolerance_seconds") != 300 or temporal_profile.get(
        "allowed_granularity_seconds"
    ) != 300 or temporal_profile.get("fact_temporal_kind_matrix") != {
        "exact_point:exact_point": "comparable",
        "exact_point:interval": "not_comparable",
        "interval:exact_point": "not_comparable",
        "interval:interval": "not_comparable",
    }:
        _fail("temporal_profile_unfrozen", "时间可比性Profile未冻结矩阵和容差")
    consistency_profile = profiles_by_id[
        "PROFILE-EVIDENCE-CONSISTENCY-1.0.0"
    ].get("parameters", {})
    if consistency_profile.get("peak_time_misalignment_alone_is_conflict") is not False:
        _fail("consistency_profile_unfrozen", "峰值错位不得自动映射为冲突")
    consistency_mapping = consistency_profile.get("mapping")
    if not isinstance(consistency_mapping, dict) or set(consistency_mapping.values()) != {
        "missing",
        "not_comparable",
        "consistent",
        "partially_consistent",
        "conflict",
    }:
        _fail("consistency_profile_unfrozen", "证据一致性Profile映射不闭合")
    if (
        consistency_mapping.get(
            "same_slot+verified_mutually_exclusive_true_assertions"
        )
        != "conflict"
        or "any_temporal_relation+verified_mutually_exclusive_true_assertions"
        in consistency_mapping
        or consistency_mapping.get("any_temporal_relation+unknown_assertion_relation")
        != "not_comparable"
    ):
        _fail("consistency_cross_time_conflict", "冲突只允许同槽互斥断言且unknown必须不可比较")
    vp_profile = profiles_by_id["PROFILE-VP-CONSISTENCY-1.0.0"].get(
        "parameters", {}
    )
    if vp_profile.get("classification_precedence") != [
        "empty_expected",
        "missing_present",
        "unknown_present",
        "mixed_or_divergent",
        "single_consistent_group",
    ]:
        _fail("vp_profile_unfrozen", "VP一致性分类优先级未冻结")
    route_profile = profiles_by_id["PROFILE-ROUTE-CHANGE-1.0.0"].get(
        "parameters", {}
    )
    if route_profile.get("class_predicate_precedence") != [
        "state_unknown",
        "explicit_withdraw",
        "origin_change",
        "replacement_path",
        "path_only_change",
        "reannouncement",
    ] or len(route_profile.get("predicate_contract", {})) != 6:
        _fail("route_change_profile_unfrozen", "Route change谓词和优先级未冻结")

    type_contract = payload.get("type_expression_contract")
    registered_types = payload.get("registered_type_refs")
    if not isinstance(type_contract, dict) or not isinstance(registered_types, dict):
        _fail("operator_type_registry_missing", "缺少机器类型表达式与注册表")
    primitive_types = set(type_contract.get("primitive_types", []))
    container_names = {"array", "nonempty_array", "set", "complete_array", "complete_set"}

    def validate_type_expression(expression: Any, *, context: str) -> None:
        if not isinstance(expression, str) or not expression:
            _fail("operator_type_unresolved", f"{context} 类型表达式无效")
        base = expression[:-5] if expression.endswith("|null") else expression
        if base.startswith("enum:"):
            if len(base.removeprefix("enum:").split("|")) < 2:
                _fail("operator_type_unresolved", f"{context} enum人口不足")
            return
        if base.startswith("const:"):
            if not base.removeprefix("const:"):
                _fail("operator_type_unresolved", f"{context} const为空")
            return
        container = re.fullmatch(r"([a-z_]+)<([^<>]+)>", base)
        if container:
            if container.group(1) not in container_names:
                _fail("operator_type_unresolved", f"{context} 容器类型未登记")
            validate_type_expression(container.group(2), context=context)
            return
        if base not in primitive_types and base not in registered_types:
            _fail("operator_type_unresolved", f"{context} 未登记类型：{base}")

    for field, expression in common_types.items():
        validate_type_expression(expression, context=f"common_typed_fields.{field}")
    compiler_contract = payload.get("payload_schema_compilation_contract")
    if not isinstance(compiler_contract, dict) or compiler_contract.get(
        "compiler_contract_id"
    ) != "p2_s1_operator_payload_schema_compiler_v1":
        _fail("operator_payload_schema_compiler_missing", "缺少payload schema编译合同")
    compiler_rules = compiler_contract.get("rules", [])
    if not isinstance(compiler_rules, list) or not any(
        "additionalProperties=false" in rule for rule in compiler_rules
    ) or compiler_contract.get("runtime_compilation_implemented") is not False:
        _fail("operator_payload_schema_compiler_open", "payload schema必须闭合且不得声称已实现")
    envelope = payload.get("common_output_envelope")
    if not isinstance(envelope, dict) or envelope.get("applies_to_all_operators") is not True:
        _fail("operator_output_envelope_missing", "公共输出Envelope未应用到全部Operator")
    required_envelope_fields = {
        "identity",
        "operator_id",
        "operator_version",
        "parameter_profile_id",
        "parameter_profile_digest",
        "input_digests",
        "input_completeness",
        "result_state",
        "completeness",
        "result",
        "evidence_refs",
        "fact_lineage",
        "output_digest",
    }
    if set(envelope.get("required_fields", [])) != required_envelope_fields or set(
        envelope.get("field_types", {})
    ) != required_envelope_fields:
        _fail("operator_output_envelope_incomplete", "公共输出Envelope字段不闭合")
    for field, expression in envelope["field_types"].items():
        validate_type_expression(expression, context=f"common_output_envelope.{field}")
    if envelope.get("unclosed_evidence_publishable") is not False:
        _fail("operator_output_envelope_incomplete", "未闭合Evidence不得发布")

    decomposition = _load_json(
        repo_root / CONTRACT_ROOT / "execution-unit-decomposition.json"
    )
    decomposed = {
        item["unit_id"]: item
        for item in decomposition.get("atomic_units", [])
        if isinstance(item, dict) and str(item.get("unit_id", "")).startswith("OP-")
    }
    capability_ids: list[str] = []
    output_semantics: list[str] = []
    operators_by_id = {item["unit_id"]: item for item in payload["operators"]}
    capability_map = _load_json(
        repo_root / CONTRACT_ROOT / "question-capability-map.json"
    )
    capability_units = {
        item["capability_id"]: set(item.get("unit_ids", []))
        for item in capability_map.get("capabilities", [])
        if isinstance(item, dict)
    }
    expected_question_refs: dict[str, set[str]] = {
        unit_id: set() for unit_id in EXPECTED_OPERATORS
    }
    for question in capability_map.get("questions", []):
        capability_refs = list(question.get("required_capability_ids", [])) + list(
            question.get("optional_capability_ids", [])
        )
        units = set().union(
            *(capability_units.get(capability_id, set()) for capability_id in capability_refs)
        )
        for unit_id in units & set(EXPECTED_OPERATORS):
            expected_question_refs[unit_id].add(question["question_id"])
    for item in payload["operators"]:
        unit_id = item["unit_id"]
        capability_ids.append(item.get("atomic_capability_id"))
        output_semantics.append(item.get("output_semantic"))
        expected = decomposed.get(unit_id)
        if expected is None:
            _fail("operator_decomposition_missing", f"{unit_id} 无S1D-1原子人口")
        if item.get("input_semantic") != expected.get("source_population"):
            _fail(
                "operator_input_semantic_drift",
                f"{unit_id} input_semantic 与S1D-1不一致",
            )
        if item.get("output_semantic") != expected.get("output_semantic"):
            _fail(
                "operator_output_semantic_drift",
                f"{unit_id} output_semantic 与S1D-1不一致",
            )
        if item.get("atomic_capability_id") != expected.get("atomic_capability_id"):
            _fail(
                "operator_capability_drift",
                f"{unit_id} atomic_capability_id 与S1D-1不一致",
            )
        if item.get("runtime_ready_claim") is not False:
            _fail("runtime_claim_forbidden", f"{unit_id} 不得声称runtime ready")
        schema_prefix = unit_id.lower().replace("-", "")
        expected_input_ref = (
            f"operator-contract.schema.json#/$defs/{schema_prefix}InputEnvelope"
        )
        expected_output_ref = (
            f"operator-contract.schema.json#/$defs/{schema_prefix}OutputEnvelope"
        )
        if item.get("input_schema_ref") != expected_input_ref or item.get(
            "output_schema_ref"
        ) != expected_output_ref:
            _fail("operator_schema_ref_missing", f"{unit_id} 未绑定自身闭合输入输出Envelope")
        for field in (
            "input_contract",
            "output_contract",
            "algorithm_contract",
            "result_state_contract",
            "complexity_contract",
        ):
            if not isinstance(item.get(field), dict):
                _fail("operator_contract_incomplete", f"{unit_id} 缺少 {field}")
        input_contract = item["input_contract"]
        output_contract = item["output_contract"]
        if input_contract.get("identity_equality_required") is not True:
            _fail("operator_identity_boundary_missing", f"{unit_id} 未冻结输入身份一致")
        profile_id = input_contract.get("parameter_profile_id")
        if profile_id is not None and profile_id not in expected_profiles:
            _fail("operator_profile_unknown", f"{unit_id} 引用了未知Profile")
        for contract_name, contract in (
            ("input", input_contract),
            ("output", output_contract),
        ):
            required_fields = contract.get("required_fields")
            local_types = contract.get("field_types")
            if not isinstance(required_fields, list) or not isinstance(local_types, dict):
                _fail(
                    "operator_type_closure_missing",
                    f"{unit_id} {contract_name} typed contract不完整",
                )
            missing_types = set(required_fields) - set(local_types) - set(common_types)
            if missing_types:
                _fail(
                    "operator_type_closure_missing",
                    f"{unit_id} {contract_name}字段未定型：{sorted(missing_types)}",
                )
            for field, expression in {
                **{key: common_types[key] for key in required_fields if key in common_types},
                **{key: value for key, value in local_types.items() if key in required_fields},
            }.items():
                validate_type_expression(
                    expression,
                    context=f"{unit_id}.{contract_name}.{field}",
                )
        algorithm = item["algorithm_contract"]
        if not all(
            isinstance(algorithm.get(field), str) and algorithm[field].strip()
            for field in ("operation", "rule", "tie_rule")
        ):
            _fail("operator_algorithm_incomplete", f"{unit_id} 算法合同不完整")
        states = item["result_state_contract"]
        if set(states) != {"empty", "missing", "unknown", "not_computable"}:
            _fail("operator_value_state_incomplete", f"{unit_id} 值状态合同不闭合")
        if not isinstance(item.get("evidence_inheritance"), str) or not item[
            "evidence_inheritance"
        ].strip():
            _fail("operator_evidence_inheritance_missing", f"{unit_id} 缺Evidence继承")
        if not isinstance(item.get("forbidden_conclusions"), list) or not item[
            "forbidden_conclusions"
        ]:
            _fail("operator_boundary_missing", f"{unit_id} 缺禁止结论")
        if set(item.get("question_refs", [])) != expected_question_refs[unit_id]:
            _fail(
                "operator_question_ref_drift",
                f"{unit_id} question_refs 与能力图反向索引不一致",
            )
        split = item.get("split_test")
        expected_split = {
            "business_transform_count": 1,
            "primary_output_semantic_count": 1,
            "mode_changes_semantic": False,
            "independently_publishable_subtransforms": False,
            "internally_invokes_other_units": False,
            "disposition": "atomic_as_designed",
        }
        if split != expected_split:
            _fail("atomic_split_test_failed", f"{unit_id} 功能原子性拆分测试失败")
    if len(capability_ids) != len(set(capability_ids)):
        _fail("operator_capability_duplicate", "Operator atomic_capability_id 必须唯一")
    if len(output_semantics) != len(set(output_semantics)):
        _fail("operator_output_semantic_duplicate", "Operator主输出语义必须唯一")
    deferred = [
        item
        for item in payload["operators"]
        if item.get("unit_id") == "OP-34" and item.get("disposition") == "deferred_p2_1"
    ]
    if len(deferred) != 1:
        _fail("deferred_boundary_missing", "OP-34 必须标记 disposition=deferred_p2_1")
    op05 = next(item for item in payload["operators"] if item.get("unit_id") == "OP-05")
    expected_sort = [
        "peak_invisible_direction_count DESC",
        "peak_complete_prefix_count DESC",
        "asn ASC",
    ]
    if op05.get("default_sort") != expected_sort:
        _fail("as_sort_contract_drift", f"OP-05 默认排序漂移：{op05.get('default_sort')}")
    op06 = operators_by_id["OP-06"]
    if "left_censored" not in op06["output_contract"]["field_types"].get(
        "outcome", ""
    ) or "首个有效槽" not in op06["algorithm_contract"]["rule"]:
        _fail("first_occurrence_censoring_missing", "OP-06 缺少左删失语义")
    op35 = operators_by_id["OP-35"]
    if "right_censored" not in op35["output_contract"]["field_types"].get(
        "outcome", ""
    ):
        _fail("last_occurrence_censoring_missing", "OP-35 缺少右删失语义")
    op36 = operators_by_id["OP-36"]
    op36_outcomes = op36["output_contract"]["field_types"].get("outcome", "")
    if not all(
        marker in op36_outcomes
        for marker in ("crossed", "left_censored", "no_crossing", "indeterminate_gap")
    ) or "false_to_true" not in op36["algorithm_contract"]["operation"] or (
        "全部相邻槽可比较" not in op36["algorithm_contract"]["rule"]
    ):
        _fail("threshold_crossing_contract_incomplete", "OP-36 首次越阈语义不闭合")
    if op36["input_contract"].get("required_fields") != [
        "identity",
        "ordered_numeric_points",
        "threshold_profile_instance",
        "series_digest",
    ] or op36["input_contract"].get("field_types", {}).get(
        "threshold_profile_instance"
    ) != "first_crossing_profile_instance":
        _fail("threshold_profile_instance_unbound", "OP-36 必须直接消费闭合Profile实例")
    op12 = operators_by_id["OP-12"]
    if "unranked_indeterminate_gap" not in op12["output_contract"][
        "required_fields"
    ] or "indeterminate_gap" not in op12["algorithm_contract"]["rule"]:
        _fail("crossing_rank_gap_population_missing", "OP-12 必须隔离不确定缺口人口")
    op16 = operators_by_id["OP-16"]
    if "op15_position_receipt" not in op16["input_contract"]["required_fields"] or (
        "再次搜索" not in op16["algorithm_contract"]["rule"]
    ):
        _fail("path_operator_composite", "OP-16 必须只消费OP-15位置回执")
    op17 = operators_by_id["OP-17"]
    if not {"left_position_receipt", "right_position_receipt", "path_digest"}.issubset(
        op17["input_contract"]["required_fields"]
    ) or "再次解析" not in op17["algorithm_contract"]["rule"]:
        _fail("path_operator_composite", "OP-17 必须只比较同路径双位置回执")
    op19 = operators_by_id["OP-19"]
    if "population_filter_receipt_digest" not in op19["input_contract"][
        "required_fields"
    ] or "不在Operator内判断contains/order" not in op19["algorithm_contract"]["rule"]:
        _fail("downstream_projection_composite", "OP-19 不得内嵌路径定位或顺序判断")
    op15 = operators_by_id["OP-15"]
    if "common_path_status" not in op15["input_contract"]["required_fields"] or (
        "path_parse_status" in op15["input_contract"]["required_fields"]
    ) or "absent" in json.dumps(op15, ensure_ascii=False):
        _fail("common_path_status_not_consumed", "OP-15 必须直接消费统一路径状态且不得残留absent")
    op10 = operators_by_id["OP-10"]
    if op10["input_contract"].get("invariants") != [
        "peak_complete_prefix_count <= fixed_prefix_count"
    ]:
        _fail("ratio_input_invariant_missing", "OP-10 缺少分子不大于分母约束")
    op09 = operators_by_id["OP-09"]
    if op09["input_contract"].get("parameter_profile_id") != (
        "PROFILE-PEAK-SEVERITY-1.0.0"
    ):
        _fail("peak_severity_profile_missing", "OP-09 severity字段未绑定登记Profile")
    if operators_by_id["OP-22"].get("input_semantic") != "complete_canonical_path_set":
        _fail("path_count_population_drift", "OP-22 只能计算唯一路径数")
    if operators_by_id["OP-23"].get("input_semantic") != "complete_prefix_set":
        _fail("prefix_count_population_drift", "OP-23 只能计算唯一前缀数")
    for unit_id in ("OP-22", "OP-23", "OP-24"):
        if operators_by_id[unit_id]["complexity_contract"].get("time") != "O(n)":
            _fail("count_complexity_drift", f"{unit_id} 对账复杂度必须为O(n)")
    op21 = operators_by_id["OP-21"]
    if "peer_asn_direction_ids" not in op21["algorithm_contract"]["rule"] or (
        "并集展平" not in op21["algorithm_contract"]["rule"]
    ):
        _fail("peer_direction_projection_drift", "OP-21 必须直接展平Tool路径行的方向集合")
    op29 = operators_by_id["OP-29"]
    temporal_outcomes = op29["output_contract"]["field_types"].get("relation", "")
    required_temporal = {
        "same_slot",
        "left_precedes_within",
        "right_precedes_within",
        "left_precedes_outside",
        "right_precedes_outside",
        "missing_left",
        "missing_right",
        "missing_both",
        "not_comparable",
    }
    if not all(marker in temporal_outcomes for marker in required_temporal):
        _fail("temporal_relation_enum_incomplete", "OP-29 有向时间关系人口不闭合")
    expected_profile_invariants = {
        "OP-29": "comparability_profile.profile_digest == Envelope.parameter_profile_digest",
        "OP-36": "threshold_profile_instance.profile_digest == Envelope.parameter_profile_digest",
        "OP-37": "consistency_profile.profile_digest == Envelope.parameter_profile_digest",
    }
    for unit_id, invariant in expected_profile_invariants.items():
        if operators_by_id[unit_id]["input_contract"].get("invariants") != [invariant]:
            _fail("operator_profile_binding_open", f"{unit_id} Profile实例摘要未绑定Envelope")
        if operators_by_id[unit_id]["output_contract"].get("invariants") != [
            "profile_digest == Envelope.parameter_profile_digest"
        ]:
            _fail("operator_profile_binding_open", f"{unit_id} 输出Profile摘要未绑定Envelope")
    op37 = operators_by_id["OP-37"]
    if "op29_temporal_receipt" not in op37["input_contract"]["required_fields"] or (
        "不重新计算时间关系" not in op37["algorithm_contract"]["rule"]
    ):
        _fail("consistency_operator_composite", "OP-37 必须消费OP-29回执且不得重算")
    if op37["result_state_contract"].get("unknown") != "not_comparable":
        _fail("consistency_unknown_mapping_drift", "OP-37 unknown必须唯一映射为not_comparable")
    _validate_schema_file(_load_json(schema_path), schema_path)
    schema = _load_json(schema_path)
    required_schema_fields = set(schema.get("required", []))
    for field in (
        "input_contract",
        "output_contract",
        "algorithm_contract",
        "result_state_contract",
        "evidence_inheritance",
        "complexity_contract",
        "split_test",
        "runtime_ready_claim",
    ):
        if field not in required_schema_fields:
            _fail("operator_schema_open", f"Operator schema required缺少 {field}")
    if schema.get("additionalProperties") is not False:
        _fail("operator_schema_open", "Operator schema顶层必须闭合")
    schema_defs = schema.get("$defs")
    if not isinstance(schema_defs, dict):
        _fail("operator_schema_defs_missing", "Operator schema缺少$defs")
    required_defs = {
        "publicationIdentity",
        "evidenceRef",
        "operatorInputEnvelope",
        "operatorOutputEnvelope",
        "typedStatePoint",
        "typedNumericPoint",
        "halfOpenStateInterval",
        "typedTimedFact",
        "typedFact",
        "op10Receipt",
        "op11Receipt",
        "op15Receipt",
        "op29Receipt",
        "op36Receipt",
        "pathEvidence",
        "verifiedAnchorBeforeKnownOriginAssociation",
        "routeObservationKey",
        "newPrefixState",
        "routeStateAtTime",
        "routeEvent",
    }
    if not required_defs.issubset(schema_defs):
        _fail("operator_schema_defs_missing", "Operator关键typed $defs不闭合")
    for type_name, reference in registered_types.items():
        if reference.startswith("operator-contract.schema.json#/$defs/"):
            def_name = reference.rsplit("/", 1)[-1]
            if def_name not in schema_defs:
                _fail(
                    "operator_type_ref_unresolved",
                    f"登记类型未解析：{type_name} -> {def_name}",
                )
    output_envelope_schema = schema_defs["operatorOutputEnvelope"]
    input_envelope_schema = schema_defs["operatorInputEnvelope"]
    if set(output_envelope_schema.get("required", [])) != required_envelope_fields or (
        output_envelope_schema.get("additionalProperties") is not False
    ):
        _fail("operator_output_envelope_incomplete", "输出Envelope schema不闭合")
    evidence_schema = output_envelope_schema.get("properties", {}).get(
        "evidence_refs", {}
    )
    if evidence_schema.get("minItems") != 1:
        _fail("operator_output_evidence_empty", "Operator输出必须至少继承一个Evidence引用")

    def _has_profile_id_digest_binding(envelope_schema: Mapping[str, Any]) -> bool:
        null_branch = False
        nonnull_branch = False
        for branch in envelope_schema.get("allOf", []):
            profile_condition = (
                branch.get("if", {})
                .get("properties", {})
                .get("parameter_profile_id", {})
            )
            digest_effect = (
                branch.get("then", {})
                .get("properties", {})
                .get("parameter_profile_digest", {})
            )
            if profile_condition.get("type") == "null" and digest_effect.get("type") == "null":
                null_branch = True
            if profile_condition.get("type") == "string" and digest_effect.get("$ref") == "#/$defs/digest":
                nonnull_branch = True
        return null_branch and nonnull_branch

    if not _has_profile_id_digest_binding(input_envelope_schema) or not (
        _has_profile_id_digest_binding(output_envelope_schema)
    ):
        _fail("operator_profile_binding_open", "输入输出Envelope未机器约束Profile ID与摘要同时为空或同时存在")
    for item in payload["operators"]:
        prefix = item["unit_id"].lower().replace("-", "")
        expected_definitions = {
            f"{prefix}InputPayload",
            f"{prefix}ResultPayload",
            f"{prefix}InputEnvelope",
            f"{prefix}OutputEnvelope",
        }
        if not expected_definitions.issubset(schema_defs):
            _fail("operator_payload_schema_missing", f"{item['unit_id']} 缺少独立payload schema")
        for side, definition_name in (
            ("input", f"{prefix}InputPayload"),
            ("output", f"{prefix}ResultPayload"),
        ):
            payload_schema = schema_defs[definition_name]
            required_fields = item[f"{side}_contract"]["required_fields"]
            if (
                payload_schema.get("type") != "object"
                or payload_schema.get("additionalProperties") is not False
                or payload_schema.get("required") != required_fields
                or set(payload_schema.get("properties", {})) != set(required_fields)
            ):
                _fail(
                    "operator_payload_schema_open",
                    f"{item['unit_id']} {side} payload schema未与合同闭合",
                )
    op09_schema = schema_defs["typedSeverityStatePoint"]
    expected_severity_refs = {
        "#/$defs/prefixSeverityStatePoint",
        "#/$defs/asnSeverityStatePoint",
    }
    if {
        item.get("$ref") for item in op09_schema.get("oneOf", [])
    } != expected_severity_refs:
        _fail("peak_input_value_type_missing", "OP-09 必须使用来源可构造的同质前缀或ASN状态点")
    prefix_severity = schema_defs.get("prefixSeverityStatePoint", {})
    asn_severity = schema_defs.get("asnSeverityStatePoint", {})
    if not {
        "expected_direction_count",
        "visible_direction_count",
        "invisible_direction_count",
        "unknown_direction_count",
    }.issubset(prefix_severity.get("required", [])) or not {
        "fixed_prefix_count",
        "partial_prefix_count",
        "complete_prefix_count",
        "unknown_prefix_count",
        "invisible_direction_count",
    }.issubset(asn_severity.get("required", [])):
        _fail("peak_input_value_type_missing", "OP-09 severity状态点与TOOL-08/09原生字段不闭合")
    op09_payload_variants = schema_defs["op09InputPayload"].get("oneOf", [])
    op09_variant_text = json.dumps(op09_payload_variants, sort_keys=True)
    if len(op09_payload_variants) != 2 or not all(
        marker in op09_variant_text
        for marker in (
            "#/$defs/prefixSeverityStatePoint",
            "#/$defs/asnSeverityStatePoint",
            '"const": "invisible_direction_count"',
            '"complete_prefix_count"',
            '"partial_prefix_count"',
        )
    ):
        _fail("peak_input_population_mixed", "OP-09 schema必须拒绝前缀与ASN状态点混合序列")
    if "firstCrossingProfileInstance" not in schema_defs:
        _fail("threshold_profile_instance_unbound", "缺少首次越阈Profile实例schema")
    path_evidence_schema = schema_defs["pathEvidence"]
    if "peer_asn_direction_ids" not in path_evidence_schema.get("required", []) or (
        "peer_direction_id" in path_evidence_schema.get("required", [])
    ):
        _fail("path_direction_population_drift", "pathEvidence必须保留一行多方向集合")
    expected_path_profile_digest = (
        "eb4d2081ee69ab0254b7af461122cf315b6bcdf24551c22de7e8dccc6d965966"
    )
    for def_name in (
        "op15Receipt",
        "pathEvidence",
        "verifiedAnchorBeforeKnownOriginAssociation",
        "routeStateAtTime",
        "op15InputPayload",
        "op15ResultPayload",
    ):
        definition = schema_defs[def_name]
        if definition.get("properties", {}).get(
            "path_canonicalization_profile_digest", {}
        ).get("const") != expected_path_profile_digest:
            _fail("path_digest_profile_unfrozen", f"{def_name} 未绑定冻结路径Profile摘要")
    route_state_schema = schema_defs["routeStateAtTime"]
    if not {
        "path_digest",
        "path_canonicalization_profile_id",
        "path_canonicalization_profile_digest",
    }.issubset(route_state_schema.get("required", [])):
        _fail("path_digest_source_missing", "routeStateAtTime未保留Tool原生路径身份")
    op15_input_schema = schema_defs["op15InputPayload"]
    op15_input_text = json.dumps(op15_input_schema.get("allOf", []), sort_keys=True)
    if not all(
        marker in op15_input_text
        for marker in (
            '"ordered"',
            '"unordered"',
            '"unknown"',
            '"not_applicable"',
            '"path_digest"',
            '"type": "null"',
        )
    ):
        _fail("op15_pathless_state_open", "OP-15 known与pathless输入条件未闭合")
    review = _load_json(review_path)
    _validate_atomicity_review(
        review,
        expected_ids=EXPECTED_OPERATORS,
        path=review_path,
        kind="operator",
    )
    if review.get("overall_disposition") != "passed" or review.get(
        "remaining_blockers"
    ) != []:
        _fail("atomicity_review_failed", "Operator 独立审查仍有阻断项")
    boundary = review.get("boundary")
    if not isinstance(boundary, dict) or any(
        boundary.get(field) is not False
        for field in ("runtime_implemented", "production_deployed")
    ):
        _fail("runtime_claim_forbidden", "Operator审查不得声称已实现或部署")
    return [
        "operator_catalog_closure",
        "op_05_sort",
        "op_34_deferred",
        "operator_function_atomicity",
        "operator_typed_contract_closure",
        "operator_value_state_closure",
        "operator_path_receipt_composition_boundary",
        "operator_temporal_consistency_boundary",
    ]


def _validate_s1d4(repo_root: Path) -> list[str]:
    for relative in ARTIFACTS_BY_STAGE["S1D-4"][:4]:
        path = repo_root / relative
        _validate_schema_file(_load_json(path), path)
    consistency_path = repo_root / ARTIFACTS_BY_STAGE["S1D-4"][4]
    payload = _load_json(consistency_path)
    boundaries = _object_list_ids(payload, "boundaries", "id", consistency_path)
    _validate_exact_ids(
        boundaries,
        RUNTIME_COMMIT_CONSISTENCY,
        kind="runtime_commit_consistency",
    )
    if payload.get("distinct_from_execution_unit_function_atomicity") is not True:
        _fail(
            "atomicity_scope_conflated",
            "运行时提交一致性必须明确区别于执行单元功能原子性",
        )
    return [
        "investigation_schemas",
        "dual_model_answer_flow_schema",
        "runtime_commit_consistency_contract",
    ]


def _validate_s1d5(repo_root: Path) -> list[str]:
    oracle_path, budget_path, alignment_path, review_path = (
        repo_root / relative for relative in ARTIFACTS_BY_STAGE["S1D-5"]
    )
    oracle = _load_json(oracle_path)
    ids = _object_list_ids(oracle, "questions", "question_id", oracle_path)
    _validate_exact_ids(ids, EXPECTED_QUESTIONS, kind="oracle_question")
    expected_scenarios = {
        "normal",
        "empty",
        "missing",
        "null",
        "wrong_identity",
        "unavailable",
        "boundary",
        "large_result",
        "tamper",
        "cancel",
        "rerun",
        "partial_failure",
    }
    scenarios = set(oracle.get("scenario_classes", []))
    if not expected_scenarios.issubset(scenarios):
        _fail(
            "oracle_scenario_coverage_mismatch",
            f"Oracle 缺少场景：{sorted(expected_scenarios - scenarios)}",
        )
    budget = _load_json(budget_path)
    if not isinstance(budget, dict) or not budget.get("budgets"):
        _fail("budget_contract_missing", "成本性能合同缺少 budgets")
    alignment = _load_json(alignment_path)
    if not isinstance(alignment, dict):
        _fail("model_alignment_contract_invalid", "模型对齐评测必须是 JSON object")
    if alignment.get("teacher_model_id") != "gpt-5.6-sol":
        _fail("teacher_model_identity_drift", "Teacher 模型必须为 gpt-5.6-sol")
    ds_identity = alignment.get("ds_student_identity")
    if not isinstance(ds_identity, dict) or any(
        not isinstance(ds_identity.get(field), str) or not ds_identity[field].strip()
        for field in ("provider", "model", "version")
    ):
        _fail("ds_model_identity_unfrozen", "S1D-5 必须冻结 DS provider/model/version")
    if any("latest" in ds_identity[field].lower() for field in ("model", "version")):
        _fail("ds_model_identity_unfrozen", "DS 精确模型身份不得使用 latest")
    alignment_ids = _object_list_ids(
        alignment, "questions", "question_id", alignment_path
    )
    _validate_exact_ids(
        alignment_ids,
        EXPECTED_QUESTIONS,
        kind="model_alignment_question",
    )
    required_hard_metrics = {
        "fact_precision",
        "evidence_ref_precision",
        "boundary_compliance",
    }
    if not required_hard_metrics.issubset(set(alignment.get("hard_gate_metrics", []))):
        _fail("model_alignment_hard_gates_missing", "Sol/DS 对齐缺少事实、Evidence 或边界硬门")
    if alignment.get("text_similarity_is_sufficient") is not False:
        _fail("text_similarity_overclaimed", "文本相似度不得单独作为 DS 晋级条件")
    if alignment.get("teacher_reference_requires_evidence_validation") is not True:
        _fail("teacher_truth_conflation", "TeacherReference 必须先通过 Evidence Validator")
    review = _load_json(review_path)
    if not isinstance(review, dict):
        _fail("review_contract_invalid", "产品语义审查必须是 JSON object")
    if not review.get("builder_id") or not review.get("reviewer_id"):
        _fail("reviewer_identity_missing", "必须提供 builder_id 与 reviewer_id")
    if review["builder_id"] == review["reviewer_id"]:
        _fail("reviewer_independence_failed", "Builder 与 Reviewer 不得为同一身份")
    return [
        "oracle_scenario_closure",
        "budget_contract",
        "sol_ds_alignment_contract",
        "independent_review",
    ]


def _validate_s1d6(repo_root: Path) -> list[str]:
    candidate_path, manifest_path = (
        repo_root / relative for relative in ARTIFACTS_BY_STAGE["S1D-6"]
    )
    candidate = _load_json(candidate_path)
    required_flags = {
        "design_only": True,
        "runtime_implemented": False,
        "production_deployed": False,
    }
    if not isinstance(candidate, dict):
        _fail("candidate_invalid", "candidate 必须是 JSON object")
    for key, expected in required_flags.items():
        if candidate.get(key) is not expected:
            _fail("candidate_boundary_drift", f"candidate.{key} 必须为 {expected}")
    if not candidate.get("design_candidate_id"):
        _fail("candidate_identity_missing", "candidate 缺少 design_candidate_id")

    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("artifacts"), list):
        _fail("manifest_invalid", "acceptance-manifest 缺少 artifacts")
    declared: dict[str, str] = {}
    for item in manifest["artifacts"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            _fail("manifest_invalid", "manifest artifact 缺少 path")
        if not isinstance(item.get("sha256"), str):
            _fail("manifest_invalid", f"manifest artifact 缺少 sha256：{item}")
        declared[item["path"]] = item["sha256"]

    required_artifacts: list[Path] = [TASK_SPEC, PHASE_PLAN]
    for stage in STAGES[1:6]:
        required_artifacts.extend(ARTIFACTS_BY_STAGE[stage])
    for relative in required_artifacts:
        path = repo_root / relative
        expected = declared.get(relative.as_posix())
        if expected is None:
            _fail("manifest_closure_missing", f"manifest 未声明制品：{relative}")
        if expected != _sha256(path):
            _fail("manifest_digest_mismatch", f"manifest 摘要不匹配：{relative}")
    return ["final_candidate_boundary", "acceptance_manifest_digest_closure"]


STAGE_VALIDATORS = {
    "S1D-1": _validate_s1d1,
    "S1D-2": _validate_s1d2,
    "S1D-3": _validate_s1d3,
    "S1D-4": _validate_s1d4,
    "S1D-5": _validate_s1d5,
    "S1D-6": _validate_s1d6,
}


def _required_stages(stage: str) -> tuple[str, ...]:
    if stage == "final":
        return STAGES
    return STAGES[: STAGES.index(stage) + 1]


def _validate_prior_receipts(repo_root: Path, stage: str) -> dict[str, str]:
    required = _required_stages(stage)
    prior = required[:-1] if stage != "final" else required
    result: dict[str, str] = {}
    current_task_spec_sha256 = _sha256(repo_root / TASK_SPEC)
    current_phase_plan_sha256 = _sha256(repo_root / PHASE_PLAN)
    for prior_stage in prior:
        path = repo_root / RECEIPT_ROOT / f"{prior_stage}.json"
        payload = _load_json(path)
        if not isinstance(payload, dict):
            _fail("prior_receipt_invalid", f"阶段回执必须是 JSON object：{path}")
        if payload.get("stage") != prior_stage or payload.get("status") != "alignment_passed":
            _fail("prior_receipt_invalid", f"阶段回执状态或阶段错误：{path}")
        if payload.get("receipt_digest") != _canonical_digest(payload):
            _fail("prior_receipt_digest_mismatch", f"阶段回执摘要不匹配：{path}")
        if payload.get("task_spec_sha256") != current_task_spec_sha256:
            _fail("prior_receipt_stale", f"阶段回执未绑定当前 Task Spec：{path}")
        if payload.get("phase_plan_sha256") != current_phase_plan_sha256:
            _fail("prior_receipt_stale", f"阶段回执未绑定当前阶段计划：{path}")
        for key, expected in (
            ("design_only", True),
            ("runtime_implemented", False),
            ("production_deployed", False),
        ):
            if payload.get(key) is not expected:
                _fail("prior_receipt_boundary_drift", f"阶段回执边界错误：{path}: {key}")
        result[prior_stage] = payload["receipt_digest"]
    return result


def run_alignment(
    repo_root: Path,
    stage: str,
    *,
    require_prior_receipts: bool = True,
) -> dict[str, Any]:
    """验证指定阶段并返回尚未写盘的回执。"""

    repo_root = repo_root.resolve()
    if stage not in VALID_STAGES:
        _fail("stage_invalid", f"未知阶段：{stage}; allowed={VALID_STAGES}")

    spec_path = repo_root / TASK_SPEC
    plan_path = repo_root / PHASE_PLAN
    checks = _validate_task_spec(_read_text(spec_path))
    checks.extend(_validate_phase_plan(_read_text(plan_path)))

    prior_receipts: dict[str, str] = {}
    if require_prior_receipts and stage != "S1D-0":
        prior_receipts = _validate_prior_receipts(repo_root, stage)

    for required_stage in _required_stages(stage):
        if required_stage == "S1D-0":
            continue
        for relative in ARTIFACTS_BY_STAGE[required_stage]:
            _regular_file(repo_root / relative)
        checks.extend(STAGE_VALIDATORS[required_stage](repo_root))

    receipt: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_design_alignment_receipt_v1",
        "stage": stage,
        "status": "alignment_passed",
        "task_spec_sha256": _sha256(spec_path),
        "phase_plan_sha256": _sha256(plan_path),
        "prior_receipts": prior_receipts,
        "checks": checks,
        "design_only": True,
        "runtime_implemented": False,
        "production_deployed": False,
    }
    receipt["receipt_digest"] = _canonical_digest(receipt)
    return receipt


def write_receipt(repo_root: Path, output: Path, receipt: Mapping[str, Any]) -> Path:
    """在仓库范围内以同目录临时文件 + os.replace 原子写入回执。"""

    root = repo_root.resolve()
    target = output if output.is_absolute() else root / output
    target = target.resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        _fail("output_outside_repo", f"回执路径必须位于仓库内：{target}")
    if target.exists() and target.is_symlink():
        _fail("unsafe_output", f"回执目标不得是符号链接：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)
    return target


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--stage", choices=VALID_STAGES, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        help="可选阶段回执路径；省略时只检查并向 stdout 输出",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        receipt = run_alignment(args.repo_root, args.stage)
        if args.output is not None:
            path = write_receipt(args.repo_root, args.output, receipt)
            print(
                json.dumps(
                    {
                        "status": "alignment_passed",
                        "stage": args.stage,
                        "receipt": str(path),
                        "receipt_digest": receipt["receipt_digest"],
                        "design_only": True,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        else:
            print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except AlignmentError as exc:
        print(
            json.dumps(
                {
                    "status": "alignment_failed",
                    "error_code": exc.code,
                    "message": str(exc),
                    "design_only": True,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
