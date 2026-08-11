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

EXPECTED_EXECUTION_UNITS = (
    "TOOL-07",
    "TOOL-08",
    "TOOL-09",
    "TOOL-10",
    "TOOL-11",
    "TOOL-12",
    "TOOL-13",
    "OP-05",
    "OP-06",
    "OP-07",
    "OP-08",
    "OP-09",
    "OP-10",
    "OP-11",
    "OP-12",
    "OP-13",
    "OP-14",
    "VALIDATOR-01",
    "DELIVERY-01",
)

EXPECTED_TOOLS = tuple(f"TOOL-{index:02d}" for index in range(7, 14))
EXPECTED_OPERATORS = tuple(f"OP-{index:02d}" for index in range(5, 15))

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
    try:
        return json.loads(text)
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
        r"\|\s*`?((?:TOOL|OP)-\d{2}|VALIDATOR-\d{2}|DELIVERY-\d{2})\b",
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
    map_ids = _object_list_ids(_load_json(map_path), "questions", "question_id", map_path)
    oracle_ids = _object_list_ids(
        _load_json(oracle_path), "questions", "question_id", oracle_path
    )
    _validate_exact_ids(map_ids, EXPECTED_QUESTIONS, kind="capability_question")
    _validate_exact_ids(oracle_ids, EXPECTED_QUESTIONS, kind="oracle_seed_question")
    decomposition = _load_json(decomposition_path)
    if not isinstance(decomposition, dict) or not isinstance(
        decomposition.get("decisions"), list
    ):
        _fail("artifact_schema_invalid", f"{decomposition_path} 必须包含 decisions")
    required_splits = {
        "query_entity_states": {"TOOL-07", "TOOL-08", "TOOL-09", "TOOL-10"},
        "observed_path_structure": {"OP-08", "OP-09", "OP-10", "OP-11"},
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
    return [
        "question_capability_closure",
        "question_oracle_seed_closure",
        "execution_unit_atomic_decomposition",
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
    deferred = [
        item
        for item in payload["tools"]
        if item.get("unit_id") == "TOOL-13" and item.get("disposition") == "deferred_p2_1"
    ]
    if len(deferred) != 1:
        _fail("deferred_boundary_missing", "TOOL-13 必须标记 disposition=deferred_p2_1")
    _validate_schema_file(_load_json(schema_path), schema_path)
    _validate_atomicity_review(
        _load_json(review_path),
        expected_ids=EXPECTED_TOOLS,
        path=review_path,
        kind="tool",
    )
    return [
        "tool_catalog_closure",
        "tool_13_deferred",
        "tool_contract_schema",
        "tool_function_atomicity",
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
    deferred = [
        item
        for item in payload["operators"]
        if item.get("unit_id") == "OP-14" and item.get("disposition") == "deferred_p2_1"
    ]
    if len(deferred) != 1:
        _fail("deferred_boundary_missing", "OP-14 必须标记 disposition=deferred_p2_1")
    op05 = next(item for item in payload["operators"] if item.get("unit_id") == "OP-05")
    expected_sort = [
        "peak_invisible_direction_count DESC",
        "peak_complete_prefix_count DESC",
        "asn ASC",
    ]
    if op05.get("default_sort") != expected_sort:
        _fail("as_sort_contract_drift", f"OP-05 默认排序漂移：{op05.get('default_sort')}")
    _validate_schema_file(_load_json(schema_path), schema_path)
    _validate_atomicity_review(
        _load_json(review_path),
        expected_ids=EXPECTED_OPERATORS,
        path=review_path,
        kind="operator",
    )
    return [
        "operator_catalog_closure",
        "op_05_sort",
        "op_14_deferred",
        "operator_function_atomicity",
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
    for prior_stage in prior:
        path = repo_root / RECEIPT_ROOT / f"{prior_stage}.json"
        payload = _load_json(path)
        if not isinstance(payload, dict):
            _fail("prior_receipt_invalid", f"阶段回执必须是 JSON object：{path}")
        if payload.get("stage") != prior_stage or payload.get("status") != "alignment_passed":
            _fail("prior_receipt_invalid", f"阶段回执状态或阶段错误：{path}")
        if payload.get("receipt_digest") != _canonical_digest(payload):
            _fail("prior_receipt_digest_mismatch", f"阶段回执摘要不匹配：{path}")
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
