#!/usr/bin/env python3
"""P2-S1 执行单元设计阶段防跑偏检查。

该 Hook 只验证设计人口、阶段依赖、结构、摘要和边界；它不证明 Tool、Operator、
调查运行时、页面、部署或生产能力已经实现。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
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
ALIGNMENT_HOOK = Path(
    ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py"
)
ALIGNMENT_HOOK_TESTS = Path(
    "dev/tests/test_country_outage_p2_s1_design_alignment_hook.py"
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
EXPECTED_OPERATORS = tuple(f"OP-{index:02d}" for index in range(5, 40))
EXPECTED_HOST_UNITS = (
    "PLAN-CAP-01",
    "PLAN-CAP-02",
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
EXPECTED_CAPABILITIES = tuple(f"CAP-P2-{index:03d}" for index in range(1, 65))
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

REGISTRY_RESOLVER_ID = "country_outage_p2_registry_resolver"
REGISTRY_RESOLVER_VERSION = "1.0.0"
REGISTRY_RESOLVER_CONTRACT_DIGEST = "sha256:" + "7" * 64
REGISTRY_RESOLVER_IMPLEMENTATION_DIGEST = "sha256:" + "8" * 64
REGISTRY_STORE_ATTESTATION_CONTRACT_DIGEST = "sha256:" + "b" * 64
PLAN_ADMISSION_VALIDATOR_ID = "country_outage_p2_plan_admission_validator"
PLAN_ADMISSION_VALIDATOR_VERSION = "1.0.0"
PLAN_ADMISSION_VALIDATOR_CONTRACT_DIGEST = "sha256:" + "9" * 64
PLAN_ADMISSION_VALIDATOR_IMPLEMENTATION_DIGEST = "sha256:" + "a" * 64
VALIDATED_PLAN_STORE_ATTESTATION_CONTRACT_DIGEST = "sha256:" + "c" * 64
VALIDATED_PLAN_VALIDATOR_ID = "country_outage_p2_validated_plan_instance_validator"
VALIDATED_PLAN_VALIDATOR_VERSION = "1.0.0"
VALIDATED_PLAN_VALIDATOR_CONTRACT_DIGEST = "sha256:" + "d" * 64
VALIDATED_PLAN_VALIDATOR_IMPLEMENTATION_DIGEST = "sha256:" + "e" * 64
COMMITTED_GRAPH_STORE_ATTESTATION_CONTRACT_DIGEST = "sha256:" + "f" * 64
COMMITTED_GRAPH_VALIDATOR_ID = "country_outage_p2_committed_evidence_graph_validator"
COMMITTED_GRAPH_VALIDATOR_VERSION = "1.0.0"
COMMITTED_GRAPH_VALIDATOR_CONTRACT_DIGEST = "sha256:" + "1" * 64
COMMITTED_GRAPH_VALIDATOR_IMPLEMENTATION_DIGEST = "sha256:" + "2" * 64
ORACLE_STORE_ATTESTATION_CONTRACT_DIGEST = (
    "sha256:e9319c71900cfb5022d98c772ae2830ac1dd98a75c2826af92a34e1580f30e38"
)
STUDENT_ANSWER_ARTIFACT_STORE_ATTESTATION_CONTRACT_DIGEST = (
    "sha256:40ed52847561a6937a7796d86c402a86bf97f99cbc3b841110c44c101be45eb2"
)
ALIGNMENT_RECEIPT_STORE_ATTESTATION_CONTRACT_DIGEST = (
    "sha256:06aa4faee43588b08b3d21a418a81c75f748b976e35e4ee82934a84f058aa48a"
)
SOURCE_COMPLETENESS_VALIDATOR_ID = "country_outage_p2_source_completeness_validator"
SOURCE_COMPLETENESS_VALIDATOR_VERSION = "1.0.0"
SOURCE_COMPLETENESS_VALIDATOR_CONTRACT_DIGEST = "sha256:" + "3" * 64
SOURCE_COMPLETENESS_VALIDATOR_IMPLEMENTATION_DIGEST = "sha256:" + "4" * 64
NODE_RESULT_STORE_ATTESTATION_CONTRACT_DIGEST = "sha256:" + "5" * 64
TOOL11_PATH_ASN_MEMBERSHIP_PROFILE_ID = "PROFILE-PATH-ASN-MEMBERSHIP-1.0.0"
TOOL11_PATH_ASN_MEMBERSHIP_PROFILE_DIGEST = (
    "28acec6edd232fd9aa38885175bcd715b9ea72f240efca6b3c5b7080394655e2"
)
TOOL11_PATH_ASN_ELIGIBLE_ROW_PREDICATE = (
    "visibility=visible AND common_path_status IN (ordered,unordered)"
)
TOOL12_NATIVE_FILTER_PROFILE_ID = "PROFILE-WINDOW-PATH-ASSOCIATION-FILTER-1.0.0"
AS_PATH_CANONICALIZATION_PROFILE_DIGEST = (
    "eb4d2081ee69ab0254b7af461122cf315b6bcdf24551c22de7e8dccc6d965966"
)
TOOL12_NATIVE_FILTER_PROFILE_DIGEST = (
    "46ca0955b30a4d43088c214ec5bdf84fbf9b65987bd65047257e85e1d7778eb7"
)
TOOL12_FILTER_RECEIPT_STORE_ATTESTATION_CONTRACT_DIGEST = "sha256:" + "7" * 64
TOOL12_FILTER_MATERIALIZER_ID = "country_outage_p2_tool12_filter_materializer"
TOOL12_FILTER_MATERIALIZER_VERSION = "1.0.0"
TOOL12_FILTER_MATERIALIZER_CONTRACT_DIGEST = "sha256:" + "8" * 64
TOOL12_FILTER_MATERIALIZER_IMPLEMENTATION_DIGEST = "sha256:" + "a" * 64
OP19_PROJECTION_RECEIPT_STORE_ATTESTATION_CONTRACT_DIGEST = "sha256:" + "b" * 64
OP19_SOURCE_PROJECTOR_ID = "country_outage_p2_op19_source_projector"
OP19_SOURCE_PROJECTOR_VERSION = "1.0.0"
OP19_SOURCE_PROJECTOR_CONTRACT_DIGEST = "sha256:" + "c" * 64
OP19_SOURCE_PROJECTOR_IMPLEMENTATION_DIGEST = "sha256:" + "d" * 64
NODE_RESULT_VALIDATOR_ID = "country_outage_p2_committed_node_result_validator"
NODE_RESULT_VALIDATOR_VERSION = "1.0.0"
NODE_RESULT_VALIDATOR_CONTRACT_DIGEST = "sha256:" + "6" * 64
NODE_RESULT_VALIDATOR_IMPLEMENTATION_DIGEST = "sha256:" + "9" * 64

# S1D-3 已封存的 Operator Schema 和 S1D-4 事务合同必须按内容寻址解析。
# 值在相应设计制品变化时由 Hook 单测显式升级，调用侧不得通过重签 Mapping 改写。
OPERATOR_CONTRACT_SCHEMA_CANONICAL_DIGEST = (
    "94ea0f3c5692d486290f86eddb1633e10d56bae9ca973b809ca6a64180b51fb1"
)
RUNTIME_CONSISTENCY_CONTRACT_CANONICAL_DIGEST = (
    "1da7186e69a1cbb1746d6933a8e793e44edf6d8df1064a8d32b5cde752b18573"
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


def _load_json_strict(path: Path) -> Any:
    """在普通JSON约束之上拒绝NaN/Infinity等非标准数字常量。"""

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

    def reject_nonstandard_constant(value: str) -> None:
        _fail(
            "artifact_json_nonstandard_number",
            f"JSON 包含非标准数字常量：{path}: {value}",
        )

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonstandard_constant,
        )
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
    return _digest_without_fields(payload, "receipt_digest")


def _digest_without_fields(payload: Mapping[str, Any], *excluded_fields: str) -> str:
    """对闭合 JSON object 做冻结的 UTF-8 canonical-json sha256。"""

    body = dict(payload)
    for field in excluded_fields:
        body.pop(field, None)
    _reject_non_finite_numbers(body, code="artifact_non_finite_number")
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plan_identity_data_projection_digest(identity: Mapping[str, Any]) -> str:
    projected = {
        key: value
        for key, value in identity.items()
        if key != "binding_digest"
    }
    registry_digest = projected.get("registry_snapshot_digest")
    if isinstance(registry_digest, str) and registry_digest.startswith("sha256:"):
        projected["registry_snapshot_digest"] = registry_digest.removeprefix(
            "sha256:"
        )
    return _digest_without_fields(projected)


def _reject_non_finite_numbers(value: Any, *, code: str) -> None:
    """递归拒绝 JSON 标准不允许的 NaN 与正负无穷。"""

    if isinstance(value, float) and not math.isfinite(value):
        _fail(code, "规范 JSON 不允许 NaN 或 Infinity")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_non_finite_numbers(item, code=code)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_non_finite_numbers(item, code=code)


def _canonical_export_scalar(value: Any) -> str:
    """将非字符串表格单元冻结为 compact canonical JSON；字符串保持原值。"""

    if isinstance(value, str):
        return value
    _reject_non_finite_numbers(value, code="export_non_finite_number")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_export_bytes(
    resolved_members: Sequence[Mapping[str, Any]], export_format: str
) -> bytes:
    """按登记格式序列化完整 ResultSet；不重新生成成员。"""

    _reject_non_finite_numbers(resolved_members, code="export_non_finite_number")
    if export_format == "json":
        return json.dumps(
            list(resolved_members),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    columns = sorted({str(field) for member in resolved_members for field in member})
    if export_format == "csv":
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(
            buffer,
            fieldnames=columns,
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(
            {
                column: _canonical_export_scalar(member.get(column))
                for column in columns
            }
            for member in resolved_members
        )
        return buffer.getvalue().encode("utf-8")
    if export_format == "markdown":
        def markdown_cell(value: Any) -> str:
            return _canonical_export_scalar(value).replace("|", "\\|").replace(
                "\n", "<br>"
            )

        lines = [
            "| " + " | ".join(columns) + " |",
            "| " + " | ".join("---" for _ in columns) + " |",
        ]
        lines.extend(
            "| "
            + " | ".join(markdown_cell(member.get(column)) for column in columns)
            + " |"
            for member in resolved_members
        )
        return ("\n".join(lines) + "\n").encode("utf-8")
    _fail("export_format_not_registered", "导出格式没有冻结的确定性序列化器")


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
    Draft202012Validator = _draft202012_validator()
    try:
        Draft202012Validator.check_schema(payload)
    except Exception as exc:
        _fail("draft202012_schema_invalid", f"Draft 2020-12 Schema 无效：{path}: {exc}")


def _draft202012_validator() -> Any:
    """加载冻结的 Draft 2020-12 验证器；缺失时失败关闭。"""

    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        _fail(
            "draft202012_validator_unavailable",
            "S1D Schema 联合验证需要 jsonschema Draft202012Validator；"
            "请使用冻结依赖运行，例如 uv run --with jsonschema==4.25.1",
        )
    return Draft202012Validator


def _draft202012_format_checker() -> Any:
    try:
        from jsonschema import FormatChecker
    except ImportError:
        _fail("draft202012_validator_unavailable", "缺少 jsonschema FormatChecker")
    checker = FormatChecker()
    required_formats = {"date-time", "uri-reference"}
    if not required_formats.issubset(checker.checkers):
        _fail(
            "draft202012_format_checker_unavailable",
            "缺少 date-time/uri-reference 严格检查器；请安装 "
            "jsonschema[format-nongpl]==4.25.1",
        )
    return checker


def _validate_draft202012_instance(
    payload: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    expected_schema_id: str,
    subject: str,
) -> None:
    """实例入口的第一道门：固定 Schema 身份、元模式与完整实例验证。"""

    if schema.get("$id") != expected_schema_id:
        _fail("instance_schema_identity_mismatch", f"{subject} 使用了未冻结 Schema：{schema.get('$id')}")
    Draft202012Validator = _draft202012_validator()
    try:
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(
                schema,
                format_checker=_draft202012_format_checker(),
            ).iter_errors(payload),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
    except Exception as exc:
        _fail("draft202012_schema_invalid", f"{subject} Schema 无法验证：{exc}")
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "$"
        _fail(
            "instance_schema_validation_failed",
            f"{subject} 未通过完整 Draft 2020-12 Schema：{location}: {first.message}; "
            f"error_count={len(errors)}",
        )


def _validate_draft202012_subschema_instance(
    payload: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    definition_name: str,
    subject: str,
) -> None:
    """从冻结根 Schema 解析一个本地 $defs，保留其根 URI 与本地引用语义。"""

    definition = schema.get("$defs", {}).get(definition_name)
    if not isinstance(definition, Mapping):
        _fail("instance_subschema_unresolved", f"{subject} 未解析到 $defs/{definition_name}")
    Draft202012Validator = _draft202012_validator()
    try:
        Draft202012Validator.check_schema(schema)
        root_validator = Draft202012Validator(
            schema,
            format_checker=_draft202012_format_checker(),
        )
        errors = sorted(
            root_validator.evolve(schema=definition).iter_errors(payload),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
    except Exception as exc:
        _fail("draft202012_schema_invalid", f"{subject} 子Schema无法验证：{exc}")
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "$"
        _fail(
            "instance_subschema_validation_failed",
            f"{subject} 未通过 $defs/{definition_name}：{location}: {first.message}; "
            f"error_count={len(errors)}",
        )


def _operator_output_definition_name(
    output_schema_ref: Any, operator_contract_schema: Mapping[str, Any]
) -> str:
    if not isinstance(output_schema_ref, str) or "#/$defs/" not in output_schema_ref:
        _fail("operator_output_schema_ref_invalid", "Operator输出Schema ref不是冻结$defs引用")
    base, definition_name = output_schema_ref.split("#/$defs/", 1)
    if base not in {
        "operator-contract.schema.json",
        str(operator_contract_schema.get("$id")),
    } or not definition_name or "/" in definition_name:
        _fail("operator_output_schema_ref_invalid", "Operator输出Schema ref不属于冻结Operator合同")
    return definition_name


def _validate_operator_edge_projection_views(
    operator_id: str, result: Mapping[str, Any]
) -> None:
    """验证 Evidence edge 是同一 Operator 结果的闭合只读视图。

    这里只执行字段复制、一一对应、摘要和固定方向校验；不重新计算路径、
    集合、时间、连接或一致性业务语义。
    """

    projection_operator_ids = {
        "OP-15",
        "OP-16",
        "OP-25",
        "OP-27",
        "OP-29",
        "OP-33",
        "OP-37",
    }
    if operator_id not in projection_operator_ids:
        return
    raw = result.get("edge_projections")
    if raw is None:
        one = result.get("edge_projection")
        projections = [one] if isinstance(one, Mapping) else []
    elif isinstance(raw, list):
        projections = raw
    else:
        _fail("operator_edge_projection_view_invalid", f"{operator_id} edge投影视图不是数组")
    for projection in projections:
        if not isinstance(projection, Mapping):
            _fail("operator_edge_projection_view_invalid", f"{operator_id} edge投影视图不是对象")
        body = projection.get("relation_projection")
        if (
            not isinstance(body, Mapping)
            or projection.get("publishable") is not True
            or projection.get("relation_projection_digest")
            != _digest_without_fields({"projection": body})
        ):
            _fail("operator_edge_projection_view_invalid", f"{operator_id} edge投影本体或摘要不闭合")

    def endpoint(
        projection: Mapping[str, Any], side: str, digest: Any, typed_value: Any
    ) -> bool:
        return projection.get(f"{side}_endpoint") == {
            "domain_value_digest": digest,
            "typed_value": typed_value,
        }

    def asn_value_digest(asn: Any) -> str:
        return _digest_without_fields(
            {
                "value_schema_ref": "https://domeye.example/types/asn.json",
                "value": asn,
            }
        )

    if operator_id == "OP-15":
        expected_body = {
            "outcome": result.get("outcome"),
            "target_asn": result.get("target_asn"),
            "ordered_positions": result.get("ordered_positions"),
            "path_digest": result.get("path_digest"),
            "path_canonicalization_profile_id": result.get(
                "path_canonicalization_profile_id"
            ),
            "path_canonicalization_profile_digest": result.get(
                "path_canonicalization_profile_digest"
            ),
            "operator_input_digest": result.get("input_digest"),
        }
        if result.get("outcome") == "found":
            if len(projections) != 1:
                _fail("operator_edge_projection_view_invalid", "OP-15 found必须有且仅有一个path_contains视图")
            projection = projections[0]
            if (
                projection.get("relation_type") != "path_contains"
                or projection.get("relation_projection") != expected_body
                or not endpoint(projection, "from", result.get("path_digest"), None)
                or not endpoint(
                    projection,
                    "to",
                    asn_value_digest(result.get("target_asn")),
                    result.get("target_asn"),
                )
            ):
                _fail("operator_edge_projection_view_invalid", "OP-15 path_contains视图未逐字段绑定核心结果")
        elif projections:
            _fail("operator_edge_projection_view_invalid", "OP-15 非found结果不得发布关系视图")
        return

    if operator_id == "OP-16":
        expected: list[tuple[str, Mapping[str, Any]]] = []
        for side, field in (("left", "left_neighbors"), ("right", "right_neighbors")):
            for neighbor in result.get(field, []):
                expected.append((side, neighbor))
        if result.get("outcome") != "computed":
            expected = []
        if len(projections) != len(expected):
            _fail("operator_edge_projection_view_invalid", "OP-16 edge视图必须与邻接成员一一对应")
        remaining = list(projections)
        for side, neighbor in expected:
            body = {
                "target_asn": result.get("target_asn"),
                "neighbor_side": side,
                "target_position": neighbor.get("target_position"),
                "neighbor_position": neighbor.get("neighbor_position"),
                "neighbor_asn": neighbor.get("neighbor_asn"),
                "path_digest": result.get("path_digest"),
                "position_receipt_digest": result.get("position_receipt_digest"),
            }
            matches = [
                item
                for item in remaining
                if item.get("relation_type") == "directly_adjacent_in_path"
                and item.get("relation_projection") == body
                and endpoint(
                    item,
                    "from",
                    asn_value_digest(result.get("target_asn")),
                    result.get("target_asn"),
                )
                and endpoint(
                    item,
                    "to",
                    asn_value_digest(neighbor.get("neighbor_asn")),
                    neighbor.get("neighbor_asn"),
                )
            ]
            if len(matches) != 1:
                _fail("operator_edge_projection_view_invalid", "OP-16 邻接视图未逐成员绑定target与neighbor ASN")
            remaining.remove(matches[0])
        return

    if operator_id == "OP-25":
        expected_body = {
            "intersection_set_digest": result.get("set_digest"),
            "intersection_count": result.get("member_count"),
            "left_digest": result.get("left_digest"),
            "right_digest": result.get("right_digest"),
        }
        expected_count = 1 if result.get("member_count", 0) > 0 else 0
        if len(projections) != expected_count or (
            projections
            and (
                projections[0].get("relation_type") != "set_intersects"
                or projections[0].get("relation_projection") != expected_body
                or not endpoint(projections[0], "from", result.get("left_digest"), None)
                or not endpoint(projections[0], "to", result.get("right_digest"), None)
            )
        ):
            _fail("operator_edge_projection_view_invalid", "OP-25 交集视图未绑定非空交集结果")
        return

    if operator_id == "OP-27":
        eligible = (
            result.get("outcome") == "computed"
            and result.get("ratio_exact") == "1/1"
            and result.get("denominator_count", 0) > 0
        )
        expected_body = {
            key: result.get(key)
            for key in (
                "direction",
                "intersection_count",
                "denominator_count",
                "ratio_exact",
                "outcome",
                "left_digest",
                "right_digest",
            )
        }
        if len(projections) != (1 if eligible else 0) or (
            projections
            and (
                projections[0].get("relation_type") != "set_contains"
                or projections[0].get("relation_projection") != expected_body
                or not endpoint(projections[0], "from", result.get("right_digest"), None)
                or not endpoint(projections[0], "to", result.get("left_digest"), None)
            )
        ):
            _fail("operator_edge_projection_view_invalid", "OP-27 set_contains视图未绑定R包含L的1/1结果")
        return

    if operator_id == "OP-29":
        relation = result.get("relation")
        edge_type = {
            "same_slot": "same_window",
            "left_precedes_within": "precedes",
            "left_precedes_outside": "precedes",
            "right_precedes_within": "follows",
            "right_precedes_outside": "follows",
        }.get(relation)
        expected_body = {
            key: result.get(key)
            for key in (
                "relation",
                "delta_seconds",
                "comparable",
                "profile_digest",
                "left_digest",
                "right_digest",
            )
        }
        if len(projections) != (1 if edge_type else 0) or (
            projections
            and (
                projections[0].get("relation_type") != edge_type
                or projections[0].get("relation_projection") != expected_body
                or not endpoint(projections[0], "from", result.get("left_digest"), None)
                or not endpoint(projections[0], "to", result.get("right_digest"), None)
            )
        ):
            _fail("operator_edge_projection_view_invalid", "OP-29 时间关系视图未按冻结方向逐字段绑定")
        return

    if operator_id == "OP-33":
        expected = [
            {
                "join_key": item.get("join_key"),
                "new_prefix_state_digest": item.get("new_prefix_state_digest"),
                "route_state_digest": item.get("route_state_digest"),
                "left_population_digest": result.get("left_digest"),
                "right_population_digest": result.get("right_digest"),
            }
            for item in result.get("matched", [])
        ]
        bodies = [item.get("relation_projection") for item in projections]
        if len(bodies) != len(expected) or sorted(
            (_digest_without_fields({"projection": item}) for item in bodies)
        ) != sorted(
            (_digest_without_fields({"projection": item}) for item in expected)
        ) or any(
            item.get("relation_type") != "at_time"
            or not endpoint(item, "from", item.get("relation_projection", {}).get("new_prefix_state_digest"), None)
            or not endpoint(item, "to", item.get("relation_projection", {}).get("route_state_digest"), None)
            for item in projections
        ):
            _fail("operator_edge_projection_view_invalid", "OP-33 at_time视图必须与matched行一一对应")
        return

    expected_body = {
        key: result.get(key)
        for key in (
            "class",
            "basis_codes",
            "temporal_receipt_digest",
            "profile_digest",
            "left_digest",
            "right_digest",
        )
    }
    eligible = result.get("class") == "conflict"
    if len(projections) != (1 if eligible else 0) or (
        projections
        and (
            projections[0].get("relation_type") != "conflicts_with"
            or projections[0].get("relation_projection") != expected_body
            or not endpoint(projections[0], "from", result.get("left_digest"), None)
            or not endpoint(projections[0], "to", result.get("right_digest"), None)
        )
    ):
        _fail("operator_edge_projection_view_invalid", "OP-37 conflict视图未逐字段绑定一致性结果")


def _schema_defs(
    payload: Mapping[str, Any], path: Path, required: Iterable[str]
) -> Mapping[str, Any]:
    defs = payload.get("$defs")
    if not isinstance(defs, dict):
        _fail("artifact_schema_invalid", f"schema 缺少 $defs：{path}")
    missing = sorted(set(required) - set(defs))
    if missing:
        _fail("artifact_schema_invalid", f"schema 缺少定义：{path}: {missing}")
    return defs


def _require_closed_schema_object(
    schema: Any,
    *,
    code: str,
    subject: str,
    required_fields: Iterable[str] = (),
) -> Mapping[str, Any]:
    if (
        not isinstance(schema, dict)
        or schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
    ):
        _fail(code, f"{subject} 必须是 additionalProperties=false 的闭合 object")
    required = schema.get("required")
    properties = schema.get("properties")
    if not isinstance(required, list) or not isinstance(properties, dict):
        _fail(code, f"{subject} 缺少 required/properties")
    expected = set(required_fields)
    if not expected.issubset(set(required)) or not expected.issubset(set(properties)):
        _fail(code, f"{subject} 缺少字段：{sorted(expected - set(required))}")
    return schema


def _validate_local_schema_refs(payload: Mapping[str, Any], path: Path) -> None:
    defs = payload.get("$defs")
    if not isinstance(defs, dict):
        _fail("artifact_schema_invalid", f"schema 缺少 $defs：{path}")

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.removeprefix("#/$defs/").split("/", 1)[0]
                if name not in defs:
                    _fail("schema_local_ref_missing", f"{path} 引用不存在定义：{ref}")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)


def _validate_runtime_validator_contract(
    payload: Mapping[str, Any],
    *,
    expected_ids: Iterable[str],
    code: str,
    subject: str,
) -> None:
    contract = payload.get("x-runtime-validator-contract")
    if not isinstance(contract, dict) or not isinstance(contract.get("validators"), list):
        _fail(code, f"{subject} 缺少实例闭包 Validator 合同")
    validators = contract["validators"]
    ids: list[str] = []
    for validator in validators:
        if not isinstance(validator, dict) or not isinstance(validator.get("validator_id"), str):
            _fail(code, f"{subject} Validator 缺少 validator_id")
        if not isinstance(validator.get("checks"), list) or not validator["checks"] or any(
            not isinstance(item, str) or not item for item in validator["checks"]
        ) or not isinstance(validator.get("failure_disposition"), str) or not validator[
            "failure_disposition"
        ]:
            _fail(code, f"{subject} Validator {validator.get('validator_id')} 未闭合")
        ids.append(validator["validator_id"])
    _validate_exact_ids(ids, tuple(expected_ids), kind=f"{subject}_runtime_validator")


def _trusted_registry_entries(
    trusted_registry_store: Mapping[str, Mapping[str, Any]],
    *,
    expected_snapshot_id: str | None,
    expected_snapshot_digest: str,
) -> Mapping[str, Mapping[str, Any]]:
    """只从 Host 受信存储按快照身份解析 Registry 准入视图。"""

    if set(trusted_registry_store) != {
        "store_contract_id",
        "trust_origin",
        "attestation_provider_id",
        "attestation_contract_digest",
        "snapshot_views",
    }:
        _fail("registry_store_invalid", "受信Registry store信封不闭合")
    if (
        trusted_registry_store.get("store_contract_id")
        != "country_outage_p2_trusted_registry_store_v1"
        or trusted_registry_store.get("trust_origin")
        != "host_authenticated_registry_store"
        or trusted_registry_store.get("attestation_provider_id")
        != "country_outage_p2_registry_store_host"
        or trusted_registry_store.get("attestation_contract_digest")
        != REGISTRY_STORE_ATTESTATION_CONTRACT_DIGEST
    ):
        _fail("registry_store_untrusted", "Registry store不是Host认证依赖")
    snapshots = trusted_registry_store.get("snapshot_views")
    if not isinstance(snapshots, Mapping):
        _fail("registry_store_invalid", "Registry store快照索引无效")
    if not isinstance(expected_snapshot_id, str):
        _fail("registry_snapshot_identity_invalid", "Registry snapshot id缺失")
    registry_view = snapshots.get(expected_snapshot_id)
    if not isinstance(registry_view, Mapping):
        _fail("registry_snapshot_unresolved", "受信Registry store中不存在指定快照")

    required = {
        "view_contract_id",
        "trusted_snapshot_verified",
        "registry_snapshot_id",
        "registry_snapshot_digest",
        "registry_snapshot_data_digest",
        "entries",
        "resolution_receipt",
        "resolution_receipt_digest",
        "view_digest",
    }
    if set(registry_view) != required:
        _fail("registry_view_invalid", "Registry准入视图字段不闭合")
    if registry_view.get("view_contract_id") != "country_outage_p2_registry_admission_view_v1":
        _fail("registry_view_invalid", "Registry准入视图合同身份错误")
    if registry_view.get("trusted_snapshot_verified") is not True:
        _fail("registry_view_untrusted", "Registry准入视图没有可信快照验证回执")
    if expected_snapshot_id is not None and registry_view.get(
        "registry_snapshot_id"
    ) != expected_snapshot_id:
        _fail("registry_view_identity_mismatch", "Registry准入视图snapshot id不一致")
    governance_digest = registry_view.get("registry_snapshot_digest")
    data_digest = registry_view.get("registry_snapshot_data_digest")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(governance_digest)):
        _fail("registry_view_identity_mismatch", "Registry治理摘要必须保留sha256前缀")
    if not re.fullmatch(r"[0-9a-f]{64}", str(data_digest)):
        _fail("registry_view_identity_mismatch", "Registry数据层摘要必须是显式裸hex投影")
    if governance_digest != f"sha256:{data_digest}":
        _fail(
            "registry_snapshot_digest_projection_mismatch",
            "Registry治理摘要与数据层摘要必须是同一摘要的显式前缀投影",
        )
    expected_digest = str(expected_snapshot_digest)
    if expected_digest.startswith("sha256:"):
        digest_matches = expected_digest == governance_digest
    else:
        digest_matches = expected_digest == data_digest
    if not digest_matches:
        _fail("registry_view_identity_mismatch", "Registry准入视图snapshot digest不一致")
    resolution_receipt = registry_view.get("resolution_receipt")
    resolution_digest = registry_view.get("resolution_receipt_digest")
    if (
        not isinstance(resolution_receipt, Mapping)
        or not re.fullmatch(r"[0-9a-f]{64}", str(resolution_digest))
        or resolution_receipt.get("receipt_digest") != resolution_digest
        or _digest_without_fields(resolution_receipt, "receipt_digest")
        != resolution_digest
        or resolution_receipt.get("receipt_kind")
        != "registry_snapshot_resolution"
        or resolution_receipt.get("resolver_id")
        != REGISTRY_RESOLVER_ID
        or resolution_receipt.get("resolver_version") != REGISTRY_RESOLVER_VERSION
        or resolution_receipt.get("resolver_contract_digest")
        != REGISTRY_RESOLVER_CONTRACT_DIGEST
        or resolution_receipt.get("resolver_implementation_digest")
        != REGISTRY_RESOLVER_IMPLEMENTATION_DIGEST
        or resolution_receipt.get("registry_snapshot_id")
        != registry_view.get("registry_snapshot_id")
        or resolution_receipt.get("registry_snapshot_digest")
        != governance_digest
        or resolution_receipt.get("registry_snapshot_data_digest")
        != data_digest
        or resolution_receipt.get("entries_digest")
        != _digest_without_fields({"entries": registry_view.get("entries")})
        or resolution_receipt.get("disposition") != "passed"
    ):
        _fail("registry_view_invalid", "Registry准入视图缺少已绑定的resolver回执")
    if registry_view.get("view_digest") != _digest_without_fields(registry_view, "view_digest"):
        _fail("registry_view_digest_mismatch", "Registry准入视图摘要无法重算")
    entries = registry_view.get("entries")
    if not isinstance(entries, dict) or any(
        not isinstance(unit_id, str) or not isinstance(entry, Mapping)
        for unit_id, entry in entries.items()
    ):
        _fail("registry_view_invalid", "Registry准入视图entries无效")
    return entries


def _validate_identity_time_order(identity: Mapping[str, Any], *, subject: str) -> None:
    """验证同一事实身份的观测窗与 data-through 单调关系。"""

    try:
        start = datetime.fromisoformat(str(identity.get("window_start_utc")).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(identity.get("window_end_utc")).replace("Z", "+00:00"))
        through = datetime.fromisoformat(str(identity.get("data_through_utc")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        _fail("identity_time_order_invalid", f"{subject} 时间字段无法比较")
    if start > end or end > through:
        _fail(
            "identity_time_order_invalid",
            f"{subject} 必须满足 window_start_utc <= window_end_utc <= data_through_utc",
        )


def validate_investigation_plan_instance(
    payload: Mapping[str, Any],
    *,
    schema: Mapping[str, Any],
    trusted_registry_store: Mapping[str, Mapping[str, Any]],
    trusted_admission_receipt_store: Mapping[str, Mapping[str, Any]],
    parameter_bindings: Mapping[str, Mapping[str, Any]],
    trusted_node_result_receipt_store: Mapping[str, Any] | None = None,
    previous_plan_definition: Mapping[str, Any] | None = None,
    previous_investigation_snapshot: Mapping[str, Any] | None = None,
) -> None:
    """验证 S1D-4 计划实例中 JSON Schema 无法表达的修订、DAG 与终态闭包。"""

    _validate_draft202012_instance(
        payload,
        schema,
        expected_schema_id=(
            "https://domeye.example/contracts/agent/country-outage-p2-s1/"
            "investigation-plan.schema.json"
        ),
        subject="InvestigationPlan",
    )

    definition = payload.get("plan_definition")
    snapshot = payload.get("investigation_snapshot")
    if not isinstance(definition, dict) or not isinstance(snapshot, dict):
        _fail("plan_instance_invalid", "计划实例缺少plan_definition或investigation_snapshot")
    if snapshot.get("plan_id") != definition.get("plan_id") or snapshot.get(
        "plan_revision"
    ) != definition.get("plan_revision"):
        _fail("plan_snapshot_binding_mismatch", "调查快照引用了不同计划身份")
    for subject, revision_key, parent_key, previous in (
        (
            definition,
            "plan_revision",
            "parent_plan_revision",
            previous_plan_definition,
        ),
        (
            snapshot,
            "investigation_revision",
            "parent_investigation_revision",
            previous_investigation_snapshot,
        ),
    ):
        revision = subject.get(revision_key)
        parent = subject.get(parent_key)
        if revision == 1 and (parent is not None or previous is not None):
            _fail("revision_parent_invalid", f"{revision_key}=1时父修订必须为空")
        if isinstance(revision, int) and revision > 1:
            if not isinstance(previous, Mapping):
                _fail("previous_revision_required", f"{revision_key}>1时必须提供前一完整修订")
            previous_revision = previous.get(revision_key)
            if (
                not isinstance(previous_revision, int)
                or revision != previous_revision + 1
                or parent != previous_revision
            ):
                _fail("revision_chain_invalid", f"{revision_key}必须连续且parent指向前一修订")
    if definition.get("plan_state") == "admitted":
        admission_digest = definition.get("admission_receipt_digest")
        admission_receipt = trusted_admission_receipt_store.get(admission_digest)
        if (
            not isinstance(admission_receipt, Mapping)
            or admission_receipt.get("receipt_digest") != admission_digest
            or _digest_without_fields(admission_receipt, "receipt_digest")
            != admission_digest
            or admission_receipt.get("receipt_kind") != "plan_admission"
            or admission_receipt.get("validator_id")
            != PLAN_ADMISSION_VALIDATOR_ID
            or admission_receipt.get("validator_version")
            != PLAN_ADMISSION_VALIDATOR_VERSION
            or admission_receipt.get("validator_contract_digest")
            != PLAN_ADMISSION_VALIDATOR_CONTRACT_DIGEST
            or admission_receipt.get("validator_implementation_digest")
            != PLAN_ADMISSION_VALIDATOR_IMPLEMENTATION_DIGEST
            or admission_receipt.get("plan_id") != definition.get("plan_id")
            or admission_receipt.get("plan_revision")
            != definition.get("plan_revision")
            or admission_receipt.get("plan_subject_digest")
            != _digest_without_fields(definition, "admission_receipt_digest")
            or admission_receipt.get("identity_digest")
            != definition.get("identity", {}).get("binding_digest")
            or admission_receipt.get("dag_digest") != definition.get("dag_digest")
            or admission_receipt.get("registry_snapshot_id")
            != definition.get("registry_snapshot_id")
            or admission_receipt.get("registry_snapshot_digest")
            != definition.get("registry_snapshot_digest")
            or admission_receipt.get("parameter_bindings_digest")
            != _digest_without_fields({"parameter_bindings": parameter_bindings})
            or admission_receipt.get("disposition") != "passed"
        ):
            _fail("plan_admission_receipt_unresolved", "admitted计划的准入回执未绑定计划语义")
    identity = definition.get("identity", {})
    if identity.get("binding_digest") != _digest_without_fields(identity, "binding_digest"):
        _fail("plan_identity_digest_mismatch", "计划 identity.binding_digest 无法重算")
    _validate_identity_time_order(identity, subject="InvestigationPlan identity")
    if definition.get("registry_snapshot_id") != identity.get(
        "registry_snapshot_id"
    ) or definition.get("registry_snapshot_digest") != identity.get(
        "registry_snapshot_digest"
    ):
        _fail("plan_registry_binding_mismatch", "计划与 identity 的 Registry 绑定不一致")
    registry_units = _trusted_registry_entries(
        trusted_registry_store,
        expected_snapshot_id=definition.get("registry_snapshot_id"),
        expected_snapshot_digest=definition.get("registry_snapshot_digest"),
    )
    nodes = definition.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        _fail("plan_dag_invalid", "计划节点为空")
    by_id: dict[str, Mapping[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("node_id"), str):
            _fail("plan_dag_invalid", "计划节点身份无效")
        node_id = node["node_id"]
        if node_id in by_id:
            _fail("plan_dag_invalid", f"重复节点：{node_id}")
        if not isinstance(node.get("execution_unit"), dict):
            _fail("composite_plan_node_forbidden", f"{node_id}未引用单一execution_unit对象")
        unit = node["execution_unit"]
        unit_id = unit.get("unit_id")
        expected_kind = next(
            (
                kind
                for prefix, kind in (
                    ("PLAN-CAP-", "plan_capability"),
                    ("TOOL-", "tool"),
                    ("OP-", "operator"),
                    ("GATE-", "gate"),
                    ("BOUNDARY-", "boundary"),
                    ("RENDERER-", "renderer"),
                    ("DELIVERY-", "delivery"),
                )
                if isinstance(unit_id, str) and unit_id.startswith(prefix)
            ),
            None,
        )
        if unit.get("unit_kind") != expected_kind:
            _fail("execution_unit_kind_mismatch", f"{unit_id} 与 unit_kind 不一致")
        registry = registry_units.get(unit_id) if isinstance(unit_id, str) else None
        if not isinstance(registry, Mapping) or registry.get("lifecycle_state") != "active":
            _fail("execution_unit_not_registry_admitted", f"{unit_id} 不是当前 Registry active 单元")
        for field in (
            "unit_kind",
            "unit_version",
            "contract_digest",
            "atomic_capability_id",
            "atomic_capability_version",
            "capability_contract_digest",
        ):
            if unit.get(field) != registry.get(field):
                _fail("execution_unit_registry_binding_mismatch", f"{unit_id}.{field} 与 Registry 不一致")
        for unit_field, registry_field in (
            ("unit_implementation_digest", "implementation_digest"),
            ("unit_semantic_digest", "semantic_digest"),
        ):
            if unit.get(unit_field) != registry.get(registry_field):
                _fail("execution_unit_registry_binding_mismatch", f"{unit_id}.{unit_field} 与 Registry 不一致")
        if unit_id in {"TOOL-13", "OP-34", "PLAN-CAP-02"} or registry.get("p2_v1_admission") != "allowed":
            _fail("deferred_execution_unit_admission_forbidden", f"{unit_id} 不得进入P2 v1计划")
        output_schema_refs = registry.get("output_schema_refs")
        if (
            not isinstance(output_schema_refs, list)
            or node.get("expected_output_schema_ref") not in output_schema_refs
        ):
            _fail(
                "execution_unit_output_schema_binding_mismatch",
                f"{unit_id}节点输出Schema不属于Registry单元合同",
            )
        parameters = parameter_bindings.get(node_id)
        input_schema = registry.get("input_schema")
        input_schema_ref = registry.get("input_schema_ref")
        if not isinstance(parameters, Mapping) or not isinstance(input_schema, Mapping):
            _fail("execution_unit_parameters_unresolved", f"{unit_id}参数或输入Schema未解析")
        _validate_draft202012_instance(
            parameters,
            input_schema,
            expected_schema_id=str(input_schema_ref),
            subject=f"{unit_id} parameters",
        )
        if node.get("parameters_digest") != _digest_without_fields(
            {"parameters": parameters}
        ):
            _fail("execution_unit_parameters_digest_mismatch", f"{unit_id}参数摘要不一致")
        by_id[node_id] = node
    if set(parameter_bindings) != set(by_id):
        _fail("execution_unit_parameters_unresolved", "参数绑定必须与计划节点一一对应")
    if definition.get("dag_digest") != _digest_without_fields({"nodes": nodes}):
        _fail("plan_dag_digest_mismatch", "计划 dag_digest 无法从节点定义重算")
    if previous_plan_definition is not None:
        if previous_plan_definition.get("plan_id") != definition.get("plan_id"):
            _fail("revision_chain_identity_mismatch", "前一计划修订属于其他plan_id")
        semantic_fields = (
            "goal_digest",
            "identity",
            "registry_snapshot_id",
            "registry_snapshot_digest",
            "nodes",
            "dag_digest",
            "budget",
            "answer_execution_policy",
            "permission_set_digest",
        )
        if all(
            definition.get(field) == previous_plan_definition.get(field)
            for field in semantic_fields
        ):
            _fail("plan_revision_without_semantic_change", "计划语义未变化却创建了新计划修订")
    for node_id, node in by_id.items():
        for dependency in node.get("depends_on", []):
            if dependency not in by_id:
                _fail("plan_dag_invalid", f"{node_id}依赖不存在节点：{dependency}")
            if by_id[dependency].get("wave", 0) >= node.get("wave", 0):
                _fail("plan_wave_invalid", f"{node_id} wave未晚于依赖节点")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            _fail("plan_dag_cycle", f"DAG存在环：{node_id}")
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in by_id[node_id].get("depends_on", []):
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in by_id:
        visit(node_id)
    ancestor_cache: dict[str, set[str]] = {}

    def ancestors(node_id: str) -> set[str]:
        if node_id not in ancestor_cache:
            result: set[str] = set()
            for dependency in by_id[node_id].get("depends_on", []):
                result.add(dependency)
                result.update(ancestors(dependency))
            ancestor_cache[node_id] = result
        return ancestor_cache[node_id]

    latest_ancestor_artifacts: dict[str, tuple[int, Mapping[str, Any]]] = {}
    for execution in snapshot.get("node_execution_revisions", []):
        if not isinstance(execution, Mapping) or execution.get("state") not in {
            "committed",
            "reused",
        }:
            continue
        source_node_id = execution.get("node_id")
        source_revision = execution.get("execution_revision")
        if not isinstance(source_node_id, str) or not isinstance(source_revision, int):
            continue
        previous = latest_ancestor_artifacts.get(source_node_id)
        if previous is None or source_revision > previous[0]:
            latest_ancestor_artifacts[source_node_id] = (
                source_revision,
                execution,
            )

    for node_id, node in by_id.items():
        parameters = parameter_bindings[node_id]
        registry = registry_units[node["execution_unit"]["unit_id"]]
        input_schema = registry["input_schema"]
        registered_input_names = set(input_schema.get("properties", {}))
        seen_input_names: set[str] = set()
        for binding in node.get("input_bindings", []):
            input_name = binding.get("input_name")
            if input_name not in registered_input_names or input_name not in parameters:
                _fail(
                    "plan_input_binding_schema_mismatch",
                    f"{node_id} 输入 {input_name} 不属于登记Input Schema或没有实际参数值",
                )
            if input_name in seen_input_names:
                _fail("plan_input_binding_duplicate", f"{node_id} 输入 {input_name} 重复绑定")
            seen_input_names.add(input_name)
            expected_source_digest = _digest_without_fields(
                {
                    "input_name": input_name,
                    "source_kind": binding.get("source_kind"),
                    "source_ref": binding.get("source_ref"),
                    "bound_parameter_value": parameters[input_name],
                }
            )
            if binding.get("source_digest") != expected_source_digest:
                _fail(
                    "plan_input_binding_digest_mismatch",
                    f"{node_id} 输入 {input_name} 的来源摘要未绑定实际参数值",
                )
            source_kind = binding.get("source_kind")
            if source_kind in {
                "node_result",
                "result_set",
                "operator_receipt",
            }:
                source_ref = binding.get("source_ref")
                if source_ref not in ancestors(node_id):
                    _fail("plan_input_binding_not_ancestor", f"{node_id} 输入没有绑定祖先节点")
                artifact = latest_ancestor_artifacts.get(source_ref)
                trusted_receipts = (
                    trusted_node_result_receipt_store.get("receipts", {})
                    if isinstance(trusted_node_result_receipt_store, Mapping)
                    else {}
                )
                execution = artifact[1] if artifact is not None else None
                receipt_digest = (
                    execution.get("receipt_digest")
                    if isinstance(execution, Mapping)
                    else None
                )
                receipt = trusted_receipts.get(receipt_digest)
                if (
                    artifact is None
                    or not isinstance(execution, Mapping)
                    or not isinstance(trusted_node_result_receipt_store, Mapping)
                    or set(trusted_node_result_receipt_store)
                    != {
                        "store_contract_id",
                        "trust_origin",
                        "caller_mutable",
                        "attestation_provider_id",
                        "attestation_contract_digest",
                        "receipts",
                    }
                    or trusted_node_result_receipt_store.get("store_contract_id")
                    != "country_outage_p2_trusted_node_result_receipt_store_v1"
                    or trusted_node_result_receipt_store.get("trust_origin")
                    != "host_authenticated_runtime_store"
                    or trusted_node_result_receipt_store.get("caller_mutable")
                    is not False
                    or trusted_node_result_receipt_store.get(
                        "attestation_provider_id"
                    )
                    != "country_outage_p2_node_result_store_host"
                    or trusted_node_result_receipt_store.get(
                        "attestation_contract_digest"
                    )
                    != NODE_RESULT_STORE_ATTESTATION_CONTRACT_DIGEST
                    or not isinstance(receipt, Mapping)
                    or receipt.get("receipt_digest") != receipt_digest
                    or _digest_without_fields(receipt, "receipt_digest")
                    != receipt_digest
                    or receipt.get("receipt_kind")
                    != "committed_node_execution_result"
                    or receipt.get("validator_id") != NODE_RESULT_VALIDATOR_ID
                    or receipt.get("validator_version")
                    != NODE_RESULT_VALIDATOR_VERSION
                    or receipt.get("validator_contract_digest")
                    != NODE_RESULT_VALIDATOR_CONTRACT_DIGEST
                    or receipt.get("validator_implementation_digest")
                    != NODE_RESULT_VALIDATOR_IMPLEMENTATION_DIGEST
                    or receipt.get("plan_id") != definition.get("plan_id")
                    or receipt.get("plan_revision")
                    != definition.get("plan_revision")
                    or receipt.get("node_id") != source_ref
                    or receipt.get("execution_revision") != execution.get(
                        "execution_revision"
                    )
                    or receipt.get("input_digest") != execution.get("input_digest")
                    or receipt.get("result_digest")
                    != execution.get("result_digest")
                    or receipt.get("registry_snapshot_id")
                    != definition.get("registry_snapshot_id")
                    or receipt.get("registry_snapshot_digest")
                    != definition.get("registry_snapshot_digest")
                    or not isinstance(receipt.get("transaction_commit_digest"), str)
                    or receipt.get("disposition")
                    not in {"committed", "reused"}
                    or receipt.get("disposition") != execution.get("state")
                    or binding.get("source_artifact_digest")
                    != receipt.get("result_digest")
                ):
                    _fail(
                        "plan_input_binding_artifact_mismatch",
                        f"{node_id} 输入没有绑定祖先节点最新已提交结果摘要",
                    )
            elif binding.get("source_artifact_digest") is not None:
                _fail(
                    "plan_input_binding_artifact_mismatch",
                    f"{node_id} 非制品来源不得携带source_artifact_digest",
                )
        if seen_input_names != set(parameters):
            _fail(
                "plan_input_binding_coverage_mismatch",
                f"{node_id} input_bindings必须逐一覆盖全部实际参数",
            )
    revisions = snapshot.get("node_execution_revisions", [])
    latest: dict[str, Mapping[str, Any]] = {}
    seen_revisions: set[tuple[str, int]] = set()
    for execution in revisions:
        if not isinstance(execution, dict):
            _fail("node_execution_invalid", "节点执行修订必须为对象")
        node_id = execution.get("node_id")
        revision = execution.get("execution_revision")
        if node_id not in by_id or not isinstance(revision, int):
            _fail("node_execution_invalid", "节点执行修订引用未知节点")
        key = (node_id, revision)
        if key in seen_revisions:
            _fail("node_execution_invalid", "节点执行修订重复")
        seen_revisions.add(key)
        parent = execution.get("parent_execution_revision")
        if revision == 1 and parent is not None:
            _fail("revision_parent_invalid", "execution_revision=1时父修订必须为空")
        if revision > 1 and parent != revision - 1:
            _fail("revision_chain_invalid", "execution_revision必须连续且parent=revision-1")
        state = execution.get("state")
        result = execution.get("result_digest")
        receipt = execution.get("receipt_digest")
        failure = execution.get("failure_code")
        if state in {"committed", "reused"} and (
            not result or not receipt or failure is not None
        ):
            _fail("node_execution_result_state_invalid", "成功节点结果、回执和失败状态矛盾")
        if state in {"failed", "cancelled", "skipped_dependency_failed"} and (
            result is not None or not receipt or not failure
        ):
            _fail("node_execution_result_state_invalid", "失败节点不得携带结果且必须有失败回执")
        if node_id not in latest or revision > latest[node_id]["execution_revision"]:
            latest[node_id] = execution
    for node_id in by_id:
        node_revisions = sorted(
            revision
            for candidate_node_id, revision in seen_revisions
            if candidate_node_id == node_id
        )
        if node_revisions and node_revisions != list(range(1, node_revisions[-1] + 1)):
            _fail("revision_chain_invalid", f"{node_id} execution revision链存在缺口")
    status = snapshot.get("status")
    if previous_investigation_snapshot is not None:
        if previous_investigation_snapshot.get("investigation_id") != snapshot.get(
            "investigation_id"
        ):
            _fail("revision_chain_identity_mismatch", "前一调查修订属于其他investigation_id")
        allowed_status_transitions = {
            "pending": {"running", "failed", "cancelled"},
            "running": {
                "running",
                "cancel_requested",
                "completed",
                "partially_completed",
                "failed",
                "cancelled",
            },
            "cancel_requested": {"cancel_requested", "cancelled", "partially_completed"},
        }
        previous_status = previous_investigation_snapshot.get("status")
        terminal_rebind = (
            previous_status in {"completed", "partially_completed"}
            and status == previous_status
            and isinstance(previous_investigation_snapshot.get("plan_revision"), int)
            and snapshot.get("plan_revision")
            == previous_investigation_snapshot.get("plan_revision") + 1
        )
        if (
            status not in allowed_status_transitions.get(previous_status, set())
            and not terminal_rebind
        ):
            _fail("investigation_state_transition_invalid", "调查修订状态迁移不合法")
    if status in {"completed", "partially_completed"} and not isinstance(
        snapshot.get("evidence_graph_revision"), int
    ):
        _fail("investigation_graph_revision_missing", "调查终态缺少Evidence Graph修订")
    if status == "completed":
        for node_id, node in by_id.items():
            if node.get("requiredness") not in {"deferred", "boundary_only"} and latest.get(
                node_id, {}
            ).get("state") not in {"committed", "reused"}:
                _fail("investigation_completed_coverage_open", f"执行节点未完成：{node_id}")
    if status == "partially_completed":
        terminal_states = {
            "committed",
            "reused",
            "failed",
            "cancelled",
            "skipped_dependency_failed",
        }
        executable_nodes = {
            node_id: node
            for node_id, node in by_id.items()
            if node.get("requiredness") not in {"deferred", "boundary_only"}
        }
        if any(
            latest.get(node_id, {}).get("state") not in terminal_states
            for node_id in executable_nodes
        ):
            _fail(
                "investigation_partial_terminal_coverage_open",
                "partially_completed必须为每个非deferred节点记录唯一最新终态",
            )
        if not any(
            latest[node_id].get("state")
            in {"failed", "cancelled", "skipped_dependency_failed"}
            for node_id in executable_nodes
        ):
            _fail("investigation_partial_status_unjustified", "partially_completed没有失败、取消或跳过支路")
    for node_id, node in by_id.items():
        for dependency in node.get("depends_on", []):
            if node.get("dependency_mode") != "hard":
                continue
            dependency_state = latest.get(dependency, {}).get("state")
            node_state = latest.get(node_id, {}).get("state")
            if dependency_state not in {"committed", "reused"}:
                if node_state in {"committed", "reused"}:
                    _fail("dependency_state_violation", f"{node_id}在hard dependency未成功时提交")
                if (
                    status in {"completed", "partially_completed"}
                    and node.get("requiredness") not in {"deferred", "boundary_only"}
                    and node_state != "skipped_dependency_failed"
                ):
                    _fail(
                        "dependency_state_violation",
                        f"{node_id}的hard dependency未成功时必须显式记录skipped_dependency_failed",
                    )
    if snapshot.get("snapshot_digest") != _digest_without_fields(snapshot, "snapshot_digest"):
        _fail("investigation_snapshot_digest_mismatch", "调查 snapshot_digest 无法重算")


def validate_result_set_instance(
    payload: Mapping[str, Any],
    *,
    schema: Mapping[str, Any],
    resolved_members: Sequence[Mapping[str, Any]],
    trusted_registry_store: Mapping[str, Mapping[str, Any]],
    receipt_store: Mapping[str, Mapping[str, Any]],
    trusted_filter_receipt_store: Mapping[str, Any] | None = None,
    previous_result_set: Mapping[str, Any] | None = None,
) -> None:
    """验证分页、计数、稳定排序、去重及 preview 与源 ResultSet 的绑定。"""

    _validate_draft202012_instance(
        payload,
        schema,
        expected_schema_id=(
            "https://domeye.example/contracts/agent/country-outage-p2-s1/"
            "result-set.schema.json"
        ),
        subject="ResultSet",
    )

    revision = payload.get("result_set_revision")
    parent = payload.get("parent_result_set_revision")
    if revision == 1 and (parent is not None or previous_result_set is not None):
        _fail("revision_parent_invalid", "ResultSet revision=1时父修订必须为空")
    if isinstance(revision, int) and revision > 1:
        previous_revision = (
            previous_result_set.get("result_set_revision")
            if isinstance(previous_result_set, Mapping)
            else None
        )
        if revision != (previous_revision or -1) + 1 or parent != previous_revision:
            _fail("revision_chain_invalid", "ResultSet修订必须连续且指向前一完整修订")
        if previous_result_set.get("result_set_id") != payload.get("result_set_id"):
            _fail("revision_chain_identity_mismatch", "ResultSet前一修订属于其他result_set_id")
    identity = payload.get("source_identity", {})
    if identity.get("identity_digest") != _digest_without_fields(identity, "identity_digest"):
        _fail("result_set_identity_digest_mismatch", "ResultSet source_identity 摘要无法重算")
    _validate_identity_time_order(identity, subject="ResultSet source_identity")
    registry_tools = _trusted_registry_entries(
        trusted_registry_store,
        expected_snapshot_id=identity.get("registry_snapshot_id"),
        expected_snapshot_digest=identity.get("registry_snapshot_digest"),
    )
    tool = payload.get("source_tool", {})
    tool_id = tool.get("tool_id")
    registry = registry_tools.get(tool_id) if isinstance(tool_id, str) else None
    if not isinstance(registry, Mapping) or registry.get("lifecycle_state") != "active":
        _fail("result_set_source_tool_not_admitted", f"{tool_id} 不是 Registry active Tool")
    if registry.get("unit_kind") != "tool" or tool.get("tool_version") != registry.get(
        "unit_version"
    ) or str(tool.get("contract_digest")) != str(registry.get("contract_digest")).removeprefix(
        "sha256:"
    ):
        _fail("result_set_source_tool_binding_mismatch", f"{tool_id} 与 Registry 不一致")
    output_populations = registry.get("output_populations")
    if not isinstance(output_populations, list):
        _fail("result_set_population_not_registered", f"{tool_id}未登记输出人口")
    registered_population = next(
        (
            item
            for item in output_populations
            if isinstance(item, Mapping)
            and item.get("population_id") == payload.get("source_population_id")
        ),
        None,
    )
    if not isinstance(registered_population, Mapping):
        _fail("result_set_population_not_registered", "ResultSet人口不属于Tool登记输出")
    member_schema = registered_population.get("member_schema")
    member_schema_ref = registered_population.get("member_schema_ref")
    member_schema_digest = (
        _digest_without_fields(member_schema)
        if isinstance(member_schema, Mapping)
        else None
    )
    if (
        not isinstance(member_schema, Mapping)
        or payload.get("source_population_schema_ref") != member_schema_ref
        or payload.get("source_population_schema_digest") != member_schema_digest
    ):
        _fail("result_set_population_schema_binding_mismatch", "ResultSet成员Schema未绑定Registry人口")
    for index, member in enumerate(resolved_members):
        _validate_draft202012_instance(
            member,
            member_schema,
            expected_schema_id=str(member_schema_ref),
            subject=f"ResultSet member[{index}]",
        )
    if payload.get("query_digest") != _digest_without_fields(
        {"normalized_query": payload.get("normalized_query")}
    ):
        _fail("result_set_query_digest_mismatch", "query_digest 无法重算")
    if payload.get("stable_sort_digest") != _digest_without_fields(
        {"stable_sort": payload.get("stable_sort")}
    ):
        _fail("result_set_sort_digest_mismatch", "stable_sort_digest 无法重算")
    verified_top_receipts: dict[str, Mapping[str, Any]] = {}
    for receipt_kind, receipt_digest in (
        ("query", payload.get("query_receipt_digest")),
        ("freeze", payload.get("freeze_receipt_digest")),
    ):
        receipt = receipt_store.get(receipt_digest)
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("receipt_digest") != receipt_digest
            or _digest_without_fields(receipt, "receipt_digest") != receipt_digest
        ):
            _fail("result_set_receipt_unresolved", "ResultSet query/freeze回执未闭合")
        verified_top_receipts[receipt_kind] = receipt
    query_receipt = verified_top_receipts["query"]
    if (
        query_receipt.get("receipt_kind") != "query"
        or query_receipt.get("query_digest") != payload.get("query_digest")
        or query_receipt.get("identity_digest") != identity.get("identity_digest")
        or query_receipt.get("tool_run_id") != tool.get("tool_run_id")
        or query_receipt.get("source_population_id")
        != payload.get("source_population_id")
        or query_receipt.get("source_population_schema_digest")
        != payload.get("source_population_schema_digest")
        or query_receipt.get("source_dataset_digest")
        != payload.get("source_dataset_digest")
        or query_receipt.get("disposition") != "passed"
    ):
        _fail("result_set_receipt_binding_mismatch", "query回执未绑定查询、身份或Tool run")

    normalized_query = payload.get("normalized_query")
    tool11_contains_asn = (
        normalized_query.get("contains_asn")
        if tool_id == "TOOL-11" and isinstance(normalized_query, Mapping)
        else None
    )
    if tool11_contains_asn is not None:
        required_filter_receipt_fields = {
            "receipt_kind",
            "tool_run_id",
            "identity_digest",
            "state_point_utc",
            "query_digest",
            "source_population_id",
            "source_dataset_digest",
            "contains_asn",
            "path_asn_membership_profile_digest",
            "path_asn_membership_index_digest",
            "path_asn_membership_materialization_receipt_digest",
            "eligible_row_predicate",
            "matched_member_keys_digest",
            "total_count",
            "disposition",
        }
        if (
            payload.get("source_population_id")
            != "materialized_route_state_rows_at_exact_time"
            or not required_filter_receipt_fields.issubset(query_receipt)
            or query_receipt.get("state_point_utc")
            != normalized_query.get("state_point_utc")
            or query_receipt.get("contains_asn") != tool11_contains_asn
            or query_receipt.get("path_asn_membership_profile_digest")
            != TOOL11_PATH_ASN_MEMBERSHIP_PROFILE_DIGEST
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(query_receipt.get("path_asn_membership_index_digest")),
            )
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(
                    query_receipt.get(
                        "path_asn_membership_materialization_receipt_digest"
                    )
                ),
            )
            or query_receipt.get("eligible_row_predicate")
            != TOOL11_PATH_ASN_ELIGIBLE_ROW_PREDICATE
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(query_receipt.get("matched_member_keys_digest")),
            )
            or query_receipt.get("total_count") != payload.get("total_count")
            or not isinstance(query_receipt.get("total_count"), int)
        ):
            _fail(
                "tool11_contains_asn_receipt_invalid",
                "TOOL-11 contains_asn查询未绑定预物化索引、Profile、目标ASN与总体",
            )
        for member in resolved_members:
            path_segments = member.get("path_segments")
            observed_asns = {
                asn
                for segment in path_segments
                if isinstance(segment, Mapping)
                for asn in segment.get("asns", [])
            } if isinstance(path_segments, list) else set()
            if (
                member.get("visibility") != "visible"
                or member.get("common_path_status") not in {"ordered", "unordered"}
                or tool11_contains_asn not in observed_asns
            ):
                _fail(
                    "tool11_contains_asn_member_mismatch",
                    "TOOL-11 contains_asn返回行不满足活动路径人口或不包含目标ASN",
                )

    tool12_filter_active = tool_id == "TOOL-12" and isinstance(
        normalized_query, Mapping
    ) and (
        normalized_query.get("contains_asn") is not None
        or normalized_query.get("anchor_before_known_origin") is True
    )
    if tool12_filter_active:
        contains_asn = normalized_query.get("contains_asn")
        anchor_asn = normalized_query.get("anchor_asn")
        anchor_before = normalized_query.get("anchor_before_known_origin") is True
        required_tool12_filter_fields = {
            "receipt_kind",
            "tool_id",
            "tool_run_id",
            "identity_digest",
            "publication_id",
            "query_digest",
            "source_population_id",
            "source_population_schema_digest",
            "source_dataset_digest",
            "filter_profile_id",
            "filter_profile_digest",
            "path_asn_membership_index_id",
            "anchor_before_known_origin_index_id",
            "path_association_index_digest",
            "path_association_materialization_receipt_digest",
            "anchor_population_source_ref",
            "eligible_anchor_asns_digest",
            "eligible_anchor_asn_count",
            "target_contains_asn",
            "target_anchor_asn",
            "anchor_before_known_origin",
            "anchor_population_eligible",
            "matched_member_keys_digest",
            "matched_total_count",
            "disposition",
        }
        if (
            payload.get("source_population_id")
            != "window_path_association_evidence_rows"
            or not required_tool12_filter_fields.issubset(query_receipt)
            or query_receipt.get("tool_id") != "TOOL-12"
            or query_receipt.get("publication_id") != identity.get("publication_id")
            or query_receipt.get("filter_profile_id")
            != TOOL12_NATIVE_FILTER_PROFILE_ID
            or query_receipt.get("filter_profile_digest")
            != TOOL12_NATIVE_FILTER_PROFILE_DIGEST
            or query_receipt.get("target_contains_asn") != contains_asn
            or query_receipt.get("target_anchor_asn") != anchor_asn
            or query_receipt.get("anchor_before_known_origin") is not anchor_before
            or not isinstance(query_receipt.get("anchor_population_source_ref"), Mapping)
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(query_receipt.get("eligible_anchor_asns_digest"))
            )
            or not isinstance(query_receipt.get("eligible_anchor_asn_count"), int)
            or query_receipt.get("eligible_anchor_asn_count") < 0
            or not isinstance(query_receipt.get("path_asn_membership_index_id"), str)
            or not query_receipt.get("path_asn_membership_index_id")
            or not isinstance(query_receipt.get("anchor_before_known_origin_index_id"), str)
            or not query_receipt.get("anchor_before_known_origin_index_id")
            or any(
                not re.fullmatch(r"[0-9a-f]{64}", str(query_receipt.get(field)))
                for field in (
                    "path_association_index_digest",
                    "path_association_materialization_receipt_digest",
                    "matched_member_keys_digest",
                )
            )
            or query_receipt.get("matched_total_count") != payload.get("total_count")
        ):
            _fail(
                "tool12_native_filter_receipt_invalid",
                "TOOL-12原生过滤回执未绑定Profile、索引、目标、人口或完整成员摘要",
            )
        filter_receipts = (
            trusted_filter_receipt_store.get("receipts")
            if isinstance(trusted_filter_receipt_store, Mapping)
            else None
        )
        materialization_digest = query_receipt.get(
            "path_association_materialization_receipt_digest"
        )
        materialization_receipt = (
            filter_receipts.get(materialization_digest)
            if isinstance(filter_receipts, Mapping)
            else None
        )
        if (
            not isinstance(trusted_filter_receipt_store, Mapping)
            or set(trusted_filter_receipt_store)
            != {
                "store_contract_id",
                "trust_origin",
                "caller_mutable",
                "attestation_provider_id",
                "attestation_contract_digest",
                "receipts",
            }
            or trusted_filter_receipt_store.get("store_contract_id")
            != "country_outage_p2_tool12_filter_receipt_store_v1"
            or trusted_filter_receipt_store.get("trust_origin")
            != "host_authenticated_runtime_store"
            or trusted_filter_receipt_store.get("caller_mutable") is not False
            or trusted_filter_receipt_store.get("attestation_provider_id")
            != "country_outage_p2_tool12_filter_receipt_store_host"
            or trusted_filter_receipt_store.get("attestation_contract_digest")
            != TOOL12_FILTER_RECEIPT_STORE_ATTESTATION_CONTRACT_DIGEST
            or not isinstance(materialization_receipt, Mapping)
            or materialization_receipt.get("receipt_digest")
            != materialization_digest
            or _digest_without_fields(materialization_receipt, "receipt_digest")
            != materialization_digest
            or materialization_receipt.get("receipt_kind")
            != "tool12_filter_materialization"
            or materialization_receipt.get("materializer_id")
            != TOOL12_FILTER_MATERIALIZER_ID
            or materialization_receipt.get("materializer_version")
            != TOOL12_FILTER_MATERIALIZER_VERSION
            or materialization_receipt.get("materializer_contract_digest")
            != TOOL12_FILTER_MATERIALIZER_CONTRACT_DIGEST
            or materialization_receipt.get("materializer_implementation_digest")
            != TOOL12_FILTER_MATERIALIZER_IMPLEMENTATION_DIGEST
            or materialization_receipt.get("publication_id")
            != identity.get("publication_id")
            or materialization_receipt.get("source_dataset_digest")
            != payload.get("source_dataset_digest")
            or materialization_receipt.get("filter_profile_id")
            != TOOL12_NATIVE_FILTER_PROFILE_ID
            or materialization_receipt.get("filter_profile_digest")
            != TOOL12_NATIVE_FILTER_PROFILE_DIGEST
            or materialization_receipt.get("path_asn_membership_index_id")
            != query_receipt.get("path_asn_membership_index_id")
            or materialization_receipt.get("anchor_before_known_origin_index_id")
            != query_receipt.get("anchor_before_known_origin_index_id")
            or materialization_receipt.get("path_association_index_digest")
            != query_receipt.get("path_association_index_digest")
            or materialization_receipt.get("anchor_population_source_ref")
            != query_receipt.get("anchor_population_source_ref")
            or materialization_receipt.get("eligible_anchor_asns_digest")
            != query_receipt.get("eligible_anchor_asns_digest")
            or materialization_receipt.get("eligible_anchor_asn_count")
            != query_receipt.get("eligible_anchor_asn_count")
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(materialization_receipt.get("indexed_member_keys_digest")),
            )
            or materialization_receipt.get("disposition") != "passed"
        ):
            _fail(
                "tool12_filter_materialization_receipt_unresolved",
                "TOOL-12必须从Host受信store解析并验证过滤索引物化回执",
            )
        anchor_population_ref = materialization_receipt.get(
            "anchor_population_source_ref"
        )
        eligible_anchor_asns = materialization_receipt.get("eligible_anchor_asns")
        required_anchor_population_ref_fields = {
            "tool_id",
            "result_set_id",
            "result_set_revision",
            "manifest_digest",
            "content_digest",
            "freeze_receipt_digest",
            "publication_id",
            "source_population_id",
        }
        if (
            not isinstance(anchor_population_ref, Mapping)
            or set(anchor_population_ref) != required_anchor_population_ref_fields
            or anchor_population_ref.get("tool_id") != "TOOL-04"
            or anchor_population_ref.get("publication_id")
            != identity.get("publication_id")
            or anchor_population_ref.get("source_population_id")
            != "ever_affected_asn_summary_rows"
            or not isinstance(anchor_population_ref.get("result_set_id"), str)
            or not anchor_population_ref.get("result_set_id")
            or not isinstance(anchor_population_ref.get("result_set_revision"), int)
            or anchor_population_ref.get("result_set_revision") < 1
            or any(
                not re.fullmatch(
                    r"[0-9a-f]{64}", str(anchor_population_ref.get(field))
                )
                for field in (
                    "manifest_digest",
                    "content_digest",
                    "freeze_receipt_digest",
                )
            )
            or not isinstance(eligible_anchor_asns, list)
            or any(not isinstance(asn, int) or asn < 0 for asn in eligible_anchor_asns)
            or eligible_anchor_asns != sorted(set(eligible_anchor_asns))
            or materialization_receipt.get("eligible_anchor_asn_count")
            != len(eligible_anchor_asns)
            or materialization_receipt.get("eligible_anchor_asns_digest")
            != _digest_without_fields({"members": eligible_anchor_asns})
            or (
                anchor_before
                and query_receipt.get("anchor_population_eligible")
                is not (anchor_asn in eligible_anchor_asns)
            )
            or (anchor_before and anchor_asn not in eligible_anchor_asns)
        ):
            _fail(
                "tool12_anchor_population_binding_invalid",
                "TOOL-12 anchor必须由同publication的完整ever-affected-AS人口及冻结回执证明",
            )
        member_identity = payload.get("member_identity")
        if payload.get("set_completeness") == "complete" and isinstance(
            member_identity, str
        ):
            expected_member_keys_digest = _digest_without_fields(
                {"member_keys": [member.get(member_identity) for member in resolved_members]}
            )
            if query_receipt.get("matched_member_keys_digest") != expected_member_keys_digest:
                _fail(
                    "tool12_native_filter_receipt_invalid",
                    "TOOL-12 complete成员键摘要与可信过滤回执不一致",
                )
        for member in resolved_members:
            path_segments = member.get("path_segments")
            observed_asns = {
                asn
                for segment in path_segments
                if isinstance(segment, Mapping)
                for asn in segment.get("asns", [])
            } if isinstance(path_segments, list) else set()
            if contains_asn is not None and contains_asn not in observed_asns:
                _fail(
                    "tool12_native_filter_member_mismatch",
                    "TOOL-12 contains_asn返回路径不包含目标ASN",
                )
            if anchor_before and (
                member.get("anchor_asn") != anchor_asn
                or member.get("ordered_sequence_eligible") is not True
                or not isinstance(member.get("known_origin_asn"), int)
                or member.get("origin_status") != "known"
                or member.get("observed_origin_asn")
                != member.get("known_origin_asn")
            ):
                _fail(
                    "tool12_native_filter_member_mismatch",
                    "TOOL-12 anchor-before返回行未匹配anchor、known origin或有序资格",
                )
            if anchor_before:
                if not isinstance(path_segments, list) or not path_segments or any(
                    not isinstance(segment, Mapping)
                    or segment.get("segment_type") != "as_sequence"
                    or not isinstance(segment.get("asns"), list)
                    for segment in path_segments
                ):
                    _fail(
                        "tool12_native_filter_member_mismatch",
                        "TOOL-12 anchor-before只接受完全有序as_sequence路径",
                    )
                flattened = [
                    asn
                    for segment in path_segments
                    for asn in segment.get("asns", [])
                ]
                collapsed: list[Any] = []
                for asn in flattened:
                    if not collapsed or collapsed[-1] != asn:
                        collapsed.append(asn)
                origin_asn = member.get("known_origin_asn")
                anchor_positions = [
                    index for index, asn in enumerate(collapsed) if asn == anchor_asn
                ]
                origin_positions = [
                    index for index, asn in enumerate(collapsed) if asn == origin_asn
                ]
                if (
                    anchor_asn == origin_asn
                    or not anchor_positions
                    or not origin_positions
                    or max(anchor_positions) >= min(origin_positions)
                    or collapsed[-1] != origin_asn
                ):
                    _fail(
                        "tool12_native_filter_member_mismatch",
                        "TOOL-12 anchor-before返回行的类型化路径不证明anchor严格早于known origin",
                    )

    pages = payload.get("page_manifest")
    if not isinstance(pages, list):
        _fail("result_set_page_chain_invalid", "page_manifest不是数组")
    segments = payload.get("member_segments")
    if not isinstance(segments, list):
        _fail("result_set_segment_invalid", "member_segments不是数组")
    if len(pages) != len(segments):
        _fail("result_set_segment_invalid", "每个page必须恰好绑定一个member segment")
    segment_by_ref: dict[str, Mapping[str, Any]] = {}
    root_evidence_refs = set(payload.get("evidence_refs", []))
    offset = 0
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or page.get("page_index") != index:
            _fail("result_set_page_chain_invalid", "页号必须从0连续递增")
        expected_token_in = None if index == 0 else pages[index - 1].get("token_out")
        if page.get("token_in") != expected_token_in:
            _fail("result_set_page_chain_invalid", "page token链断裂")
        for field, root_field in (
            ("identity_digest", "source_identity"),
            ("query_digest", "query_digest"),
            ("stable_sort_digest", "stable_sort_digest"),
            ("source_population_id", "source_population_id"),
            (
                "source_population_schema_digest",
                "source_population_schema_digest",
            ),
            ("source_dataset_digest", "source_dataset_digest"),
        ):
            expected = (
                payload.get(root_field, {}).get("identity_digest")
                if field == "identity_digest"
                else payload.get(root_field)
            )
            if page.get(field) != expected:
                _fail("result_set_page_identity_drift", f"分页{field}与根不一致")
        if not set(page.get("evidence_refs", [])).issubset(root_evidence_refs):
            _fail("result_set_page_evidence_open", "分页 Evidence 引用未被根 ResultSet 闭合")
        page_receipt_digest = page.get("page_receipt_digest")
        page_receipt = receipt_store.get(page_receipt_digest)
        if (
            not isinstance(page_receipt, Mapping)
            or page_receipt.get("receipt_digest") != page_receipt_digest
            or _digest_without_fields(page_receipt, "receipt_digest") != page_receipt_digest
        ):
            _fail("result_set_receipt_unresolved", "page回执未闭合")
        segment_ref = page.get("member_segment_ref")
        segment = segments[index]
        if not isinstance(segment, dict) or segment.get("segment_ref") != segment_ref:
            _fail("result_set_segment_invalid", "page与segment引用不一致")
        if segment_ref in segment_by_ref:
            _fail("result_set_segment_invalid", "segment_ref重复")
        segment_by_ref[segment_ref] = segment
        count = page.get("member_count")
        if (
            segment.get("page_index") != index
            or segment.get("member_count") != count
            or not isinstance(count, int)
            or count < 0
        ):
            _fail("result_set_segment_invalid", "segment页号或成员数与page不一致")
        page_members = list(resolved_members[offset : offset + count])
        offset += count
        expected_segment_digest = _digest_without_fields({"members": page_members})
        if segment.get("segment_digest") != expected_segment_digest:
            _fail("result_set_segment_digest_mismatch", "segment_digest 无法从成员重算")
        if page.get("page_content_digest") != _digest_without_fields(
            {"page_index": index, "member_segment_ref": segment_ref, "members": page_members}
        ):
            _fail("result_set_page_digest_mismatch", "page_content_digest 无法从成员重算")
        if (
            page_receipt.get("receipt_kind") != "page"
            or page_receipt.get("page_index") != index
            or page_receipt.get("page_content_digest") != page.get("page_content_digest")
            or page_receipt.get("identity_digest") != identity.get("identity_digest")
            or page_receipt.get("source_population_id")
            != payload.get("source_population_id")
            or page_receipt.get("source_population_schema_digest")
            != payload.get("source_population_schema_digest")
            or page_receipt.get("source_dataset_digest")
            != payload.get("source_dataset_digest")
            or page_receipt.get("disposition") != "passed"
        ):
            _fail("result_set_receipt_binding_mismatch", "page回执未绑定页内容与身份")
        sort_fields = [item.get("field") for item in payload.get("stable_sort", [])]
        first_key = [page_members[0].get(field) for field in sort_fields] if page_members else None
        last_key = [page_members[-1].get(field) for field in sort_fields] if page_members else None
        if page.get("first_sort_key") != first_key or page.get("last_sort_key") != last_key:
            _fail("result_set_page_sort_summary_mismatch", "page首尾排序键与成员不一致")
    returned = payload.get("returned_count")
    total = payload.get("total_count")
    completeness = payload.get("set_completeness")
    if sum(page.get("member_count", -1) for page in pages) != returned:
        _fail("result_set_count_mismatch", "分页成员数之和不等于returned_count")
    if len(resolved_members) != returned:
        _fail("result_set_count_mismatch", "解析成员数不等于returned_count")
    if completeness == "complete":
        if returned != total or payload.get("resume_page_token") is not None:
            _fail("result_set_complete_mismatch", "complete结果必须returned=total且无续页token")
        if returned > 0 and (not pages or pages[-1].get("token_out") is not None):
            _fail("result_set_complete_mismatch", "complete结果分页链未闭合")
    elif completeness == "partial_page":
        if returned <= 0 or not pages or not segments or not payload.get("resume_page_token"):
            _fail("result_set_partial_page_open", "partial_page必须闭合至少一页并提供续页token")
        if pages[-1].get("token_out") != payload.get("resume_page_token"):
            _fail("result_set_partial_page_open", "partial_page根续页token必须等于末页token_out")
        if isinstance(total, int) and total <= returned:
            _fail(
                "result_set_partial_page_open",
                "partial_page已知total_count时必须严格大于returned_count",
            )
    elif completeness == "source_incomplete":
        if not payload.get("limitations"):
            _fail("result_set_source_incomplete_open", "source_incomplete必须说明源数据限制")
        if isinstance(total, int) and returned > total:
            _fail(
                "result_set_source_incomplete_open",
                "source_incomplete已知total_count时returned_count不得超过total_count",
            )
        last_token = pages[-1].get("token_out") if pages else None
        if payload.get("resume_page_token") != last_token:
            _fail(
                "result_set_source_incomplete_open",
                "source_incomplete根续页token必须与末页token_out一致",
            )
        completeness_receipt_digest = payload.get(
            "source_completeness_receipt_digest"
        )
        completeness_receipt = receipt_store.get(completeness_receipt_digest)
        expected_limitations_digest = _digest_without_fields(
            {"limitations": payload.get("limitations")}
        )
        if (
            not isinstance(completeness_receipt, Mapping)
            or completeness_receipt.get("receipt_digest")
            != completeness_receipt_digest
            or _digest_without_fields(completeness_receipt, "receipt_digest")
            != completeness_receipt_digest
            or completeness_receipt.get("receipt_kind")
            != "source_completeness"
            or completeness_receipt.get("validator_id")
            != SOURCE_COMPLETENESS_VALIDATOR_ID
            or completeness_receipt.get("validator_version")
            != SOURCE_COMPLETENESS_VALIDATOR_VERSION
            or completeness_receipt.get("validator_contract_digest")
            != SOURCE_COMPLETENESS_VALIDATOR_CONTRACT_DIGEST
            or completeness_receipt.get("validator_implementation_digest")
            != SOURCE_COMPLETENESS_VALIDATOR_IMPLEMENTATION_DIGEST
            or completeness_receipt.get("tool_run_id") != tool.get("tool_run_id")
            or completeness_receipt.get("source_population_id")
            != payload.get("source_population_id")
            or completeness_receipt.get("source_dataset_digest")
            != payload.get("source_dataset_digest")
            or completeness_receipt.get("source_completeness")
            != "source_incomplete"
            or completeness_receipt.get("limitations_digest")
            != expected_limitations_digest
            or completeness_receipt.get("returned_count") != returned
            or completeness_receipt.get("total_count") != total
            or completeness_receipt.get("resume_page_token")
            != payload.get("resume_page_token")
            or completeness_receipt.get("disposition") != "passed"
        ):
            _fail(
                "result_set_source_incomplete_provenance_unresolved",
                "source_incomplete缺少冻结Tool/Host来源完整性回执",
            )
    elif payload.get("source_completeness_receipt_digest") is not None:
        _fail(
            "result_set_source_incomplete_provenance_unresolved",
            "非source_incomplete结果不得携带来源不完整回执",
        )
    dedupe_fields = payload.get("dedupe_key", [])
    sort_fields = [item.get("field") for item in payload.get("stable_sort", [])]
    seen: set[tuple[Any, ...]] = set()
    for member in resolved_members:
        required_member_fields = set(dedupe_fields + sort_fields + [payload.get("member_identity")])
        missing_fields = sorted(field for field in required_member_fields if field not in member)
        if missing_fields:
            _fail("result_set_member_field_missing", f"ResultSet成员缺少字段：{missing_fields}")
        for sort_item in payload.get("stable_sort", []):
            if sort_item.get("nulls") == "FORBIDDEN" and member.get(
                sort_item.get("field")
            ) is None:
                _fail("result_set_sort_null_forbidden", "稳定排序字段不允许null")
        key = tuple(member.get(field) for field in dedupe_fields)
        if key in seen:
            _fail("result_set_duplicate_member", "ResultSet存在重复成员")
        seen.add(key)
    sort_contract = payload.get("stable_sort", [])

    def compare(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
        for item in sort_contract:
            field = item.get("field")
            left_value = left.get(field)
            right_value = right.get(field)
            if left_value == right_value:
                continue
            direction = -1 if item.get("direction") == "DESC" else 1
            return direction * (-1 if left_value < right_value else 1)
        return 0

    for left, right in zip(resolved_members, resolved_members[1:]):
        if compare(left, right) > 0:
            _fail("result_set_sort_regression", "ResultSet稳定排序发生回退")
    if payload.get("manifest_digest") != _digest_without_fields(
        {"page_manifest": pages, "member_segments": segments}
    ):
        _fail("result_set_manifest_digest_mismatch", "manifest_digest 无法重算")
    if payload.get("content_digest") != _digest_without_fields(
        {"members": list(resolved_members)}
    ):
        _fail("result_set_content_digest_mismatch", "content_digest 无法重算")
    expected_result_set_id = "result-set-sha256:" + _digest_without_fields(
        {
            "source_identity": identity,
            "source_tool": tool,
            "normalized_query": payload.get("normalized_query"),
            "stable_sort": payload.get("stable_sort"),
            "source_population_id": payload.get("source_population_id"),
            "source_population_schema_ref": payload.get(
                "source_population_schema_ref"
            ),
            "source_population_schema_digest": payload.get(
                "source_population_schema_digest"
            ),
            "source_dataset_digest": payload.get("source_dataset_digest"),
        }
    )
    if payload.get("result_set_id") != expected_result_set_id:
        _fail("result_set_id_digest_mismatch", "result_set_id 无法从冻结输入与内容重算")
    freeze_receipt = verified_top_receipts["freeze"]
    if (
        freeze_receipt.get("receipt_kind") != "freeze"
        or freeze_receipt.get("result_set_id") != payload.get("result_set_id")
        or freeze_receipt.get("manifest_digest") != payload.get("manifest_digest")
        or freeze_receipt.get("content_digest") != payload.get("content_digest")
        or freeze_receipt.get("returned_count") != returned
        or freeze_receipt.get("total_count") != total
        or freeze_receipt.get("set_completeness") != completeness
        or freeze_receipt.get("source_population_id")
        != payload.get("source_population_id")
        or freeze_receipt.get("source_population_schema_digest")
        != payload.get("source_population_schema_digest")
        or freeze_receipt.get("source_dataset_digest")
        != payload.get("source_dataset_digest")
        or freeze_receipt.get("disposition") != "passed"
    ):
        _fail("result_set_receipt_binding_mismatch", "freeze回执未绑定ResultSet冻结内容")
    member_identity = payload.get("member_identity")
    member_refs = [member.get(member_identity) for member in resolved_members]
    if any(not isinstance(ref, str) or not ref for ref in member_refs):
        _fail("result_set_member_identity_invalid", "成员缺少冻结的member_identity字段")
    if len(member_refs) != len(set(member_refs)):
        _fail("result_set_member_identity_invalid", "member_identity必须在ResultSet内唯一")
    if (
        tool11_contains_asn is not None
        and completeness == "complete"
        and query_receipt.get("matched_member_keys_digest")
        != _digest_without_fields({"member_keys": member_refs})
    ):
        _fail(
            "tool11_contains_asn_receipt_invalid",
            "TOOL-11 complete查询的匹配成员键摘要不能由冻结成员顺序重算",
        )
    for preview in payload.get("preview_views", []):
        if preview.get("source_result_set_id") != payload.get("result_set_id") or preview.get(
            "source_result_set_revision"
        ) != payload.get("result_set_revision") or preview.get("stable_sort_digest") != payload.get(
            "stable_sort_digest"
        ):
            _fail("preview_source_binding_mismatch", "preview引用了其他ResultSet或排序")
        refs = preview.get("member_refs", [])
        if preview.get("returned_count") != len(refs) or len(refs) > preview.get("limit", -1):
            _fail("preview_count_invalid", "preview数量、成员或limit不一致")
        expected_refs = member_refs[: min(preview.get("limit", 0), len(member_refs))]
        if refs != expected_refs:
            _fail("preview_member_subset_invalid", "preview必须是冻结稳定排序结果的确定性前缀")
        if preview.get("view_digest") != _digest_without_fields(preview, "view_digest"):
            _fail("preview_digest_mismatch", "preview view_digest 无法重算")


def validate_op05_ranking_instance(
    input_envelope: Mapping[str, Any],
    output_envelope: Mapping[str, Any],
    *,
    operator_schema: Mapping[str, Any],
) -> None:
    """验证 OP-05 三键全序、competition并列名次和逐行结果位置。"""

    _validate_draft202012_subschema_instance(
        input_envelope,
        operator_schema,
        definition_name="op05InputEnvelope",
        subject="OP-05 InputEnvelope",
    )
    _validate_draft202012_subschema_instance(
        output_envelope,
        operator_schema,
        definition_name="op05OutputEnvelope",
        subject="OP-05 OutputEnvelope",
    )
    inputs = input_envelope.get("inputs")
    result = output_envelope.get("result")
    if not isinstance(inputs, Mapping) or not isinstance(result, Mapping):
        _fail("op05_ranking_schema_invalid", "OP-05输入或输出payload不是闭合对象")
    identity = input_envelope.get("identity")
    profile_digest = "012bf52458d4115c97c52716635345d9a64b79bf964aac8f7cbe4c433af103b2"
    if (
        input_envelope.get("operator_id") != "OP-05"
        or output_envelope.get("operator_id") != "OP-05"
        or input_envelope.get("parameter_profile_id")
        != "PROFILE-AS-SEVERITY-RANK-1.0.0"
        or output_envelope.get("parameter_profile_id")
        != "PROFILE-AS-SEVERITY-RANK-1.0.0"
        or input_envelope.get("parameter_profile_digest") != profile_digest
        or output_envelope.get("parameter_profile_digest") != profile_digest
        or inputs.get("identity") != identity
        or output_envelope.get("identity") != identity
        or input_envelope.get("input_completeness") != "complete"
        or output_envelope.get("input_completeness") != "complete"
        or output_envelope.get("completeness") != "complete"
    ):
        _fail("op05_ranking_identity_mismatch", "OP-05身份、Profile或完整性未闭合")

    members = inputs.get("members")
    if not isinstance(members, list) or inputs.get("set_completeness") != "complete":
        _fail("op05_ranking_input_invalid", "OP-05只接受完整AS summary集合")
    if len({member.get("asn") for member in members if isinstance(member, Mapping)}) != len(members):
        _fail("op05_ranking_input_invalid", "OP-05输入ASN必须唯一")
    ordered = sorted(
        members,
        key=lambda member: (
            -member["peak_invisible_direction_count"],
            -member["peak_complete_prefix_count"],
            member["asn"],
        ),
    )
    ranked_members: list[dict[str, Any]] = []
    rank_groups: list[dict[str, Any]] = []
    current_key: tuple[int, int] | None = None
    current_rank = 0
    for position, member in enumerate(ordered, start=1):
        severity_key = (
            member["peak_invisible_direction_count"],
            member["peak_complete_prefix_count"],
        )
        if severity_key != current_key:
            current_key = severity_key
            current_rank = position
            rank_groups.append(
                {
                    "rank": current_rank,
                    "member_asns": [],
                    "severity_key": list(severity_key),
                }
            )
        rank_groups[-1]["member_asns"].append(member["asn"])
        ranked_members.append(
            {
                "asn": member["asn"],
                "severity_rank_global": current_rank,
                "result_position": position,
                "severity_key": list(severity_key),
            }
        )

    population_evidence = inputs.get("population_evidence_ref")
    expected_evidence: list[Mapping[str, Any]] = [population_evidence]
    seen = {_digest_without_fields(population_evidence)}
    for member in ordered:
        evidence_ref = member.get("evidence_ref")
        evidence_digest = _digest_without_fields(evidence_ref)
        if evidence_digest not in seen:
            expected_evidence.append(evidence_ref)
            seen.add(evidence_digest)
    expected_result = {
        "ordered_asns": [member["asn"] for member in ordered],
        "ranked_members": ranked_members,
        "rank_groups": rank_groups,
        "sort_profile_id": "PROFILE-AS-SEVERITY-RANK-1.0.0",
        "input_digest": _digest_without_fields(inputs),
        "evidence_refs": expected_evidence,
    }
    expected_state = "empty" if not members else "computed"
    if (
        result != expected_result
        or output_envelope.get("result_state") != expected_state
        or output_envelope.get("evidence_refs") != expected_evidence
        or output_envelope.get("input_digests") != input_envelope.get("input_digests")
        or output_envelope.get("fact_lineage") != input_envelope.get("input_digests")
        or output_envelope.get("output_digest")
        != _digest_without_fields(output_envelope, "output_digest")
    ):
        _fail("op05_ranking_output_mismatch", "OP-05排序、并列名次、位置或Evidence无法确定性重算")


def validate_op19_projection_instance(
    input_envelope: Mapping[str, Any],
    output_envelope: Mapping[str, Any],
    *,
    operator_schema: Mapping[str, Any],
    source_result_set: Mapping[str, Any],
    source_members: Sequence[Mapping[str, Any]],
    result_set_schema: Mapping[str, Any],
    trusted_registry_store: Mapping[str, Mapping[str, Any]],
    result_receipt_store: Mapping[str, Mapping[str, Any]],
    trusted_filter_receipt_store: Mapping[str, Any],
    trusted_projection_receipt_store: Mapping[str, Any],
) -> None:
    """验证 OP-19 对完整 TOOL-12 ResultSet 的原子集合投影及逐成员谱系。"""

    pre_inputs = input_envelope.get("inputs")
    pre_query = source_result_set.get("normalized_query")
    if (
        not isinstance(pre_inputs, Mapping)
        or not isinstance(pre_query, Mapping)
        or pre_query.get("anchor_asn") != pre_inputs.get("anchor_asn")
        or pre_query.get("anchor_before_known_origin") is not True
    ):
        _fail(
            "op19_source_result_set_binding_mismatch",
            "OP-19源查询必须是同一anchor ASN的完整anchor-before人口",
        )
    validate_result_set_instance(
        source_result_set,
        schema=result_set_schema,
        resolved_members=source_members,
        trusted_registry_store=trusted_registry_store,
        receipt_store=result_receipt_store,
        trusted_filter_receipt_store=trusted_filter_receipt_store,
    )
    _validate_draft202012_subschema_instance(
        input_envelope,
        operator_schema,
        definition_name="op19InputEnvelope",
        subject="OP-19 InputEnvelope",
    )
    _validate_draft202012_subschema_instance(
        output_envelope,
        operator_schema,
        definition_name="op19OutputEnvelope",
        subject="OP-19 OutputEnvelope",
    )
    inputs = input_envelope.get("inputs")
    result = output_envelope.get("result")
    if not isinstance(inputs, Mapping) or not isinstance(result, Mapping):
        _fail("op19_projection_schema_invalid", "OP-19 输入或输出payload不是闭合对象")

    source_identity = source_result_set.get("source_identity")
    identity_fields = (
        "incident_id",
        "publication_id",
        "publication_revision",
        "publication_digest",
        "collector_id",
        "cohort_id",
        "cohort_digest",
        "window_start_utc",
        "window_end_utc",
        "data_through_utc",
        "registry_snapshot_id",
        "registry_snapshot_digest",
        "binding_generation",
    )
    expected_identity = {
        field: source_identity.get(field)
        for field in identity_fields
    } if isinstance(source_identity, Mapping) else None
    if (
        input_envelope.get("operator_id") != "OP-19"
        or output_envelope.get("operator_id") != "OP-19"
        or input_envelope.get("identity") != expected_identity
        or output_envelope.get("identity") != expected_identity
        or inputs.get("identity") != expected_identity
        or input_envelope.get("input_completeness") != "complete"
        or output_envelope.get("input_completeness") != "complete"
        or output_envelope.get("completeness") != "complete"
    ):
        _fail("op19_projection_identity_mismatch", "OP-19 未绑定完整源ResultSet身份")

    expected_source_ref = {
        "result_set_id": source_result_set.get("result_set_id"),
        "result_set_revision": source_result_set.get("result_set_revision"),
        "manifest_digest": source_result_set.get("manifest_digest"),
        "content_digest": source_result_set.get("content_digest"),
        "freeze_receipt_digest": source_result_set.get("freeze_receipt_digest"),
        "query_receipt_digest": source_result_set.get("query_receipt_digest"),
        "source_population_id": source_result_set.get("source_population_id"),
        "source_dataset_digest": source_result_set.get("source_dataset_digest"),
        "member_identity": source_result_set.get("member_identity"),
    }
    query_receipt_digest = source_result_set.get("query_receipt_digest")
    normalized_query = source_result_set.get("normalized_query")
    anchor_asn = inputs.get("anchor_asn")
    if (
        source_result_set.get("source_tool", {}).get("tool_id") != "TOOL-12"
        or source_result_set.get("set_completeness") != "complete"
        or not isinstance(normalized_query, Mapping)
        or normalized_query.get("anchor_asn") != anchor_asn
        or normalized_query.get("anchor_before_known_origin") is not True
        or inputs.get("set_completeness") != "complete"
        or inputs.get("source_result_set_ref") != expected_source_ref
        or inputs.get("source_result_set_query_receipt_digest")
        != query_receipt_digest
        or inputs.get("population_filter_receipt_digest")
        != query_receipt_digest
    ):
        _fail("op19_source_result_set_binding_mismatch", "OP-19 未精确绑定完整TOOL-12源ResultSet及query receipt")

    expected_associations: list[dict[str, Any]] = []
    source_manifest: list[dict[str, Any]] = []
    for member in source_members:
        member_key = member.get("path_association_id")
        member_digest = _digest_without_fields(member)
        source_manifest.append({"source_member_key": member_key, "source_member_digest": member_digest})
        if member.get("anchor_asn") != anchor_asn:
            _fail("op19_source_member_mismatch", "OP-19 源成员anchor不属于冻结输入人口")
        expected_associations.append(
            {
                "source_member_key": member_key,
                "source_member_digest": member_digest,
                "anchor_asn": member.get("anchor_asn"),
                "known_origin_asn": member.get("known_origin_asn"),
                "origin_status": member.get("origin_status"),
                "observed_origin_asn": member.get("observed_origin_asn"),
                "path_digest": member.get("path_digest"),
                "path_canonicalization_profile_id": member.get(
                    "path_canonicalization_profile_id"
                ),
                "path_canonicalization_profile_digest": member.get(
                    "path_canonicalization_profile_digest"
                ),
                "evidence_ref": member.get("evidence_ref"),
            }
        )
    if inputs.get("association_members") != expected_associations:
        _fail("op19_source_member_projection_mismatch", "OP-19 association_members不是源ResultSet全成员的一一结构投影")

    projection_digest = inputs.get("host_projection_receipt_digest")
    projection_receipts = (
        trusted_projection_receipt_store.get("receipts")
        if isinstance(trusted_projection_receipt_store, Mapping)
        else None
    )
    projection_receipt = (
        projection_receipts.get(projection_digest)
        if isinstance(projection_receipts, Mapping)
        else None
    )
    if (
        not isinstance(trusted_projection_receipt_store, Mapping)
        or set(trusted_projection_receipt_store)
        != {
            "store_contract_id",
            "trust_origin",
            "caller_mutable",
            "attestation_provider_id",
            "attestation_contract_digest",
            "receipts",
        }
        or trusted_projection_receipt_store.get("store_contract_id")
        != "country_outage_p2_op19_projection_receipt_store_v1"
        or trusted_projection_receipt_store.get("trust_origin")
        != "host_authenticated_runtime_store"
        or trusted_projection_receipt_store.get("caller_mutable") is not False
        or trusted_projection_receipt_store.get("attestation_provider_id")
        != "country_outage_p2_op19_projection_receipt_store_host"
        or trusted_projection_receipt_store.get("attestation_contract_digest")
        != OP19_PROJECTION_RECEIPT_STORE_ATTESTATION_CONTRACT_DIGEST
        or not isinstance(projection_receipt, Mapping)
        or projection_receipt.get("receipt_digest") != projection_digest
        or _digest_without_fields(projection_receipt, "receipt_digest")
        != projection_digest
        or projection_receipt.get("receipt_kind") != "op19_source_projection"
        or projection_receipt.get("projector_id") != OP19_SOURCE_PROJECTOR_ID
        or projection_receipt.get("projector_version")
        != OP19_SOURCE_PROJECTOR_VERSION
        or projection_receipt.get("projector_contract_digest")
        != OP19_SOURCE_PROJECTOR_CONTRACT_DIGEST
        or projection_receipt.get("projector_implementation_digest")
        != OP19_SOURCE_PROJECTOR_IMPLEMENTATION_DIGEST
        or projection_receipt.get("source_result_set_id")
        != source_result_set.get("result_set_id")
        or projection_receipt.get("source_result_set_revision")
        != source_result_set.get("result_set_revision")
        or projection_receipt.get("source_content_digest")
        != source_result_set.get("content_digest")
        or projection_receipt.get("source_query_receipt_digest")
        != query_receipt_digest
        or projection_receipt.get("source_member_manifest_digest")
        != _digest_without_fields({"members": source_manifest})
        or projection_receipt.get("projected_members_digest")
        != _digest_without_fields({"association_members": expected_associations})
        or projection_receipt.get("projected_member_count") != len(source_members)
        or projection_receipt.get("disposition") != "passed"
    ):
        _fail("op19_projection_receipt_unresolved", "OP-19 Host结构投影回执未从受信store解析或未覆盖全源人口")

    grouped: dict[int, list[dict[str, Any]]] = {}
    for association in expected_associations:
        grouped.setdefault(association["known_origin_asn"], []).append(association)
    origins = sorted(grouped)
    population_evidence_ref = inputs.get("population_evidence_ref")
    if (
        not isinstance(population_evidence_ref, Mapping)
        or population_evidence_ref.get("source_digest") != query_receipt_digest
    ):
        _fail(
            "op19_population_evidence_binding_mismatch",
            "OP-19人口证据未绑定冻结源ResultSet的query receipt",
        )
    expected_evidence: list[Mapping[str, Any]] = [population_evidence_ref]
    seen_evidence: set[str] = {_digest_without_fields(population_evidence_ref)}
    contributions: list[dict[str, Any]] = []
    for origin_asn in origins:
        group = grouped[origin_asn]
        group_evidence: list[Mapping[str, Any]] = []
        for association in group:
            evidence_ref = association["evidence_ref"]
            evidence_digest = _digest_without_fields(evidence_ref)
            if evidence_digest not in { _digest_without_fields(item) for item in group_evidence }:
                group_evidence.append(evidence_ref)
            if evidence_digest not in seen_evidence:
                expected_evidence.append(evidence_ref)
                seen_evidence.add(evidence_digest)
        contribution = {
            "origin_asn": origin_asn,
            "source_member_keys": [item["source_member_key"] for item in group],
            "source_member_digests": [item["source_member_digest"] for item in group],
            "evidence_refs": group_evidence,
        }
        contribution["contribution_digest"] = _digest_without_fields(contribution)
        contributions.append(contribution)
    expected_result = {
        "anchor_asn": anchor_asn,
        "members": origins,
        "member_contributions": contributions,
        "member_count": len(origins),
        "set_digest": _digest_without_fields({"members": origins}),
        "input_digest": _digest_without_fields(inputs),
        "evidence_refs": expected_evidence,
    }
    expected_input_digests = [source_result_set.get("content_digest"), projection_digest]
    expected_result_state = "empty" if not origins else "computed"
    if (
        result != expected_result
        or input_envelope.get("input_digests") != expected_input_digests
        or output_envelope.get("input_digests") != expected_input_digests
        or output_envelope.get("result_state") != expected_result_state
        or output_envelope.get("evidence_refs") != expected_evidence
        or output_envelope.get("fact_lineage") != expected_input_digests
        or output_envelope.get("output_digest")
        != _digest_without_fields(output_envelope, "output_digest")
    ):
        _fail("op19_output_closure_mismatch", "OP-19 origin集合、贡献谱系、计数或摘要不可由完整输入重算")


def validate_complete_export_eligibility(
    payload: Mapping[str, Any],
    *,
    schema: Mapping[str, Any],
    resolved_members: Sequence[Mapping[str, Any]],
    trusted_registry_store: Mapping[str, Mapping[str, Any]],
    receipt_store: Mapping[str, Mapping[str, Any]],
) -> None:
    """独立验证完整导出资格；preview 或不完整 ResultSet 永不合格。"""

    validate_result_set_instance(
        payload,
        schema=schema,
        resolved_members=resolved_members,
        trusted_registry_store=trusted_registry_store,
        receipt_store=receipt_store,
    )
    if payload.get("state") != "frozen" or payload.get("set_completeness") != "complete":
        _fail("result_set_export_ineligible", "完整导出只接受frozen complete ResultSet")
    if payload.get("returned_count") != payload.get("total_count"):
        _fail("result_set_export_ineligible", "完整导出人口未对账")


def validate_complete_export_artifact(
    payload: Mapping[str, Any],
    *,
    schema: Mapping[str, Any],
    resolved_members: Sequence[Mapping[str, Any]],
    trusted_registry_store: Mapping[str, Mapping[str, Any]],
    receipt_store: Mapping[str, Mapping[str, Any]],
    export_artifact: Mapping[str, Any],
    export_manifest: Mapping[str, Any],
    authorization_receipt: Mapping[str, Any],
    export_bytes: bytes,
) -> None:
    """验证完整导出制品；资格、成员、manifest与字节摘要必须同源闭合。"""

    validate_complete_export_eligibility(
        payload,
        schema=schema,
        resolved_members=resolved_members,
        trusted_registry_store=trusted_registry_store,
        receipt_store=receipt_store,
    )
    if (
        authorization_receipt.get("receipt_kind") != "export_authorization"
        or authorization_receipt.get("result_set_id") != payload.get("result_set_id")
        or authorization_receipt.get("result_set_revision")
        != payload.get("result_set_revision")
        or export_manifest.get("format")
        not in authorization_receipt.get("allowed_formats", [])
        or authorization_receipt.get("disposition") != "authorized"
        or authorization_receipt.get("receipt_digest")
        != _digest_without_fields(authorization_receipt, "receipt_digest")
    ):
        _fail("export_authorization_invalid", "导出授权回执未绑定冻结ResultSet或格式")
    member_digests = [
        _digest_without_fields({"member": member}) for member in resolved_members
    ]
    export_format = export_manifest.get("format")
    canonical_export_bytes = _canonical_export_bytes(resolved_members, str(export_format))
    if export_bytes != canonical_export_bytes:
        _fail("export_bytes_not_canonical", "导出字节不等于冻结ResultSet成员的确定性序列化")
    bytes_digest = hashlib.sha256(export_bytes).hexdigest()
    expected_manifest_fields = {
        "export_id",
        "authorization_id",
        "source_result_set_id",
        "source_result_set_revision",
        "source_manifest_digest",
        "source_content_digest",
        "format",
        "member_count",
        "ordered_member_digests",
        "temporary_artifact_ref",
        "export_bytes_sha256",
        "generation_origin",
        "manifest_digest",
    }
    if (
        set(export_manifest) != expected_manifest_fields
        or export_manifest.get("authorization_id")
        != authorization_receipt.get("authorization_id")
        or export_manifest.get("source_result_set_id") != payload.get("result_set_id")
        or export_manifest.get("source_result_set_revision")
        != payload.get("result_set_revision")
        or export_manifest.get("source_manifest_digest")
        != payload.get("manifest_digest")
        or export_manifest.get("source_content_digest") != payload.get("content_digest")
        or export_manifest.get("member_count") != len(resolved_members)
        or export_manifest.get("ordered_member_digests") != member_digests
        or export_manifest.get("export_bytes_sha256") != bytes_digest
        or export_manifest.get("generation_origin")
        != "deterministic_serializer_without_llm_member_generation"
        or export_manifest.get("manifest_digest")
        != _digest_without_fields(export_manifest, "manifest_digest")
    ):
        _fail("export_manifest_binding_mismatch", "导出manifest未绑定ResultSet成员、顺序或字节摘要")
    if (
        set(export_artifact)
        != {
            "artifact_ref",
            "format",
            "byte_length",
            "sha256",
            "visibility_state",
            "manifest_digest",
        }
        or export_artifact.get("artifact_ref")
        != export_manifest.get("temporary_artifact_ref")
        or export_artifact.get("format") != export_manifest.get("format")
        or export_artifact.get("byte_length") != len(export_bytes)
        or export_artifact.get("sha256") != bytes_digest
        or export_artifact.get("visibility_state") != "staged"
        or export_artifact.get("manifest_digest")
        != export_manifest.get("manifest_digest")
    ):
        _fail("export_artifact_binding_mismatch", "导出临时制品未绑定manifest与实际字节")


def _evidence_graph_content_digest(payload: Mapping[str, Any]) -> str:
    """唯一的 EvidenceGraph 内容摘要配方，供提交验证与受信解析共同使用。"""

    return _digest_without_fields(
        {
            "graph_id": payload.get("graph_id"),
            "graph_revision": payload.get("graph_revision"),
            "parent_graph_revision": payload.get("parent_graph_revision"),
            "investigation_id": payload.get("investigation_id"),
            "investigation_revision": payload.get("investigation_revision"),
            "plan_id": payload.get("plan_id"),
            "plan_revision": payload.get("plan_revision"),
            "plan_digest": payload.get("plan_digest"),
            "identity_digest": payload.get("identity_digest"),
            "registry_snapshot_id": payload.get("registry_snapshot_id"),
            "registry_snapshot_digest": payload.get("registry_snapshot_digest"),
            "nodes": payload.get("nodes"),
            "edges": payload.get("edges"),
            "root_node_ids": payload.get("root_node_ids"),
        }
    )


def validate_evidence_graph_instance(
    payload: Mapping[str, Any],
    *,
    schema: Mapping[str, Any],
    trusted_registry_store: Mapping[str, Mapping[str, Any]],
    result_sets: Mapping[tuple[str, int], Mapping[str, Any]],
    plan_definition: Mapping[str, Any],
    investigation_snapshot: Mapping[str, Any],
    receipt_store: Mapping[str, Mapping[str, Any]],
    operator_contract_schema: Mapping[str, Any] | None = None,
    result_set_members: Mapping[
        tuple[str, int], Mapping[str, Mapping[str, Any]]
    ] | None = None,
    previous_graph: Mapping[str, Any] | None = None,
) -> None:
    """验证 Evidence Graph 引用、身份、producer 与支持关系闭包。"""

    _validate_draft202012_instance(
        payload,
        schema,
        expected_schema_id=(
            "https://domeye.example/contracts/agent/country-outage-p2-s1/"
            "evidence-graph.schema.json"
        ),
        subject="EvidenceGraph",
    )
    revision = payload.get("graph_revision")
    parent = payload.get("parent_graph_revision")
    if revision == 1 and (parent is not None or previous_graph is not None):
        _fail("revision_parent_invalid", "EvidenceGraph revision=1时父修订必须为空")
    if isinstance(revision, int) and revision > 1:
        previous_revision = (
            previous_graph.get("graph_revision")
            if isinstance(previous_graph, Mapping)
            else None
        )
        if revision != (previous_revision or -1) + 1 or parent != previous_revision:
            _fail("revision_chain_invalid", "EvidenceGraph修订必须连续且指向前一完整修订")
        if previous_graph.get("graph_id") != payload.get("graph_id"):
            _fail("revision_chain_identity_mismatch", "EvidenceGraph前一修订属于其他graph_id")
    if (
        payload.get("investigation_id") != investigation_snapshot.get("investigation_id")
        or payload.get("investigation_revision")
        != investigation_snapshot.get("investigation_revision")
        or payload.get("plan_id") != plan_definition.get("plan_id")
        or payload.get("plan_revision") != plan_definition.get("plan_revision")
        or investigation_snapshot.get("plan_id") != plan_definition.get("plan_id")
        or investigation_snapshot.get("plan_revision") != plan_definition.get("plan_revision")
    ):
        _fail("evidence_graph_plan_investigation_binding_mismatch", "EvidenceGraph未绑定同一计划与调查修订")
    expected_plan_digest = _digest_without_fields({"plan_definition": plan_definition})
    if payload.get("plan_digest") != expected_plan_digest:
        _fail("evidence_graph_plan_digest_mismatch", "EvidenceGraph plan_digest 无法重算")
    plan_identity = plan_definition.get("identity", {})
    if payload.get("identity_digest") != _plan_identity_data_projection_digest(
        plan_identity
    ):
        _fail("evidence_graph_identity_mismatch", "EvidenceGraph身份不等于计划身份的数据摘要投影")
    normalized_registry_digest = str(plan_definition.get("registry_snapshot_digest", ""))
    if normalized_registry_digest.startswith("sha256:"):
        normalized_registry_digest = normalized_registry_digest.removeprefix("sha256:")
    if (
        payload.get("registry_snapshot_id") != plan_definition.get("registry_snapshot_id")
        or payload.get("registry_snapshot_digest") != normalized_registry_digest
    ):
        _fail("evidence_graph_registry_binding_mismatch", "EvidenceGraph Registry绑定不等于计划")
    registry_producers = _trusted_registry_entries(
        trusted_registry_store,
        expected_snapshot_id=payload.get("registry_snapshot_id"),
        expected_snapshot_digest=payload.get("registry_snapshot_digest"),
    )

    def resolved_receipt(digest: Any, *, subject: str) -> Mapping[str, Any]:
        receipt = receipt_store.get(digest)
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("receipt_digest") != digest
            or _digest_without_fields(receipt, "receipt_digest") != digest
        ):
            _fail("evidence_graph_receipt_unresolved", f"{subject} 回执未闭合")
        return receipt

    nodes = payload.get("nodes")
    edges = payload.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        _fail("evidence_graph_instance_invalid", "图节点或边不是数组")
    typed_edge_population = {
        "at_time",
        "precedes",
        "same_window",
        "follows",
        "path_contains",
        "directly_adjacent_in_path",
        "set_intersects",
        "set_contains",
        "conflicts_with",
    }
    if any(
        isinstance(edge, Mapping)
        and edge.get("edge_type") in typed_edge_population
        for edge in edges
    ) and (
        not isinstance(operator_contract_schema, Mapping)
        or _digest_without_fields(operator_contract_schema)
        != OPERATOR_CONTRACT_SCHEMA_CANONICAL_DIGEST
    ):
        _fail(
            "operator_contract_schema_identity_mismatch",
            "关系图必须解析S1D-3封存的内容寻址Operator Schema",
        )
    by_id: dict[str, Mapping[str, Any]] = {}
    for node in nodes:
        node_id = node.get("node_id") if isinstance(node, dict) else None
        if not isinstance(node_id, str) or node_id in by_id:
            _fail("evidence_graph_duplicate_node", "图节点身份缺失或重复")
        if node.get("identity_digest") != payload.get("identity_digest"):
            _fail("evidence_graph_identity_mismatch", "图节点身份与图身份不一致")
        if payload.get("graph_state") == "committed" and node.get("committed") is not True:
            _fail("evidence_graph_uncommitted_node", "已提交图包含未提交节点")
        producer = node.get("producer_ref", {})
        producer_kind = producer.get("producer_kind")
        if node.get("node_type") == "observed_fact" and producer_kind != "tool":
            _fail("evidence_graph_producer_mismatch", "observed_fact必须由Tool产生")
        if node.get("node_type") == "derived_fact" and producer_kind != "operator":
            _fail("evidence_graph_producer_mismatch", "derived_fact必须由Operator产生")
        producer_id = producer.get("producer_id")
        registry = (
            registry_producers.get(producer_id)
            if isinstance(producer_id, str)
            else None
        )
        if not isinstance(registry, Mapping) or registry.get("lifecycle_state") != "active":
            _fail("evidence_graph_producer_not_admitted", f"未登记或非active producer：{producer_id}")
        if (
            producer.get("producer_kind") != registry.get("unit_kind")
            or producer.get("producer_version") != registry.get("unit_version")
            or producer.get("contract_digest")
            != str(registry.get("contract_digest")).removeprefix("sha256:")
        ):
            _fail("evidence_graph_producer_binding_mismatch", f"{producer_id} 与 Registry 不一致")
        producer_receipt = resolved_receipt(
            producer.get("run_receipt_digest"), subject=f"{node_id} producer"
        )
        if (
            producer_receipt.get("producer_id") != producer_id
            or producer_receipt.get("output_digest") != node.get("payload_digest")
        ):
            _fail("evidence_graph_producer_receipt_mismatch", f"{node_id} producer回执未绑定节点输出")
        if node.get("payload_digest") != _digest_without_fields({"payload": node.get("payload")}):
            _fail("evidence_graph_payload_digest_mismatch", f"{node_id} payload_digest 无法重算")
        node_type = node.get("node_type")
        node_payload = node.get("payload", {})
        if node_type == "observed_fact":
            allowed_schema_refs = registry.get("output_schema_refs", [])
            if node_payload.get("fact_schema_ref") not in allowed_schema_refs:
                _fail("evidence_graph_fact_schema_not_admitted", f"{node_id} fact_schema_ref 未登记")
            value_projection = node_payload.get("fact_value_projection")
            if value_projection is not None:
                expected_value_digest = _digest_without_fields(
                    {
                        "value_schema_ref": value_projection.get(
                            "value_schema_ref"
                        ),
                        "value": value_projection.get("value"),
                    }
                )
                if (
                    value_projection.get("value_digest")
                    != expected_value_digest
                    or node_payload.get("fact_value_digest")
                    != expected_value_digest
                ):
                    _fail(
                        "evidence_graph_fact_value_projection_mismatch",
                        f"{node_id} typed事实投影与领域摘要不一致",
                    )
            source_ref = node_payload.get("source_result_set_ref")
            if source_ref is not None:
                key = (source_ref.get("result_set_id"), source_ref.get("result_set_revision"))
                source_result_set = result_sets.get(key)
                if not isinstance(source_result_set, Mapping):
                    _fail("evidence_graph_result_set_unresolved", f"{node_id} observed fact引用未知ResultSet")
                if source_result_set.get("source_identity", {}).get(
                    "identity_digest"
                ) != payload.get("identity_digest"):
                    _fail("evidence_graph_result_set_binding_mismatch", f"{node_id} observed fact ResultSet身份不一致")
                if any(
                    source_ref.get(field) != source_result_set.get(field)
                    for field in ("manifest_digest", "content_digest")
                ):
                    _fail("evidence_graph_result_set_binding_mismatch", f"{node_id} observed fact ResultSet摘要不一致")
                if (
                    source_result_set.get("state") != "frozen"
                    or source_ref.get("freeze_receipt_digest")
                    != source_result_set.get("freeze_receipt_digest")
                    or source_ref.get("source_completeness")
                    != source_result_set.get("set_completeness")
                    or node.get("completeness")
                    != source_result_set.get("set_completeness")
                ):
                    _fail("evidence_graph_observed_result_set_not_frozen", f"{node_id} observed fact来源未绑定冻结ResultSet")
                freeze_receipt = resolved_receipt(
                    source_ref.get("freeze_receipt_digest"),
                    subject=f"{node_id} ResultSet freeze",
                )
                if (
                    freeze_receipt.get("receipt_kind") != "freeze"
                    or freeze_receipt.get("result_set_id")
                    != source_ref.get("result_set_id")
                    or freeze_receipt.get("manifest_digest")
                    != source_ref.get("manifest_digest")
                    or freeze_receipt.get("content_digest")
                    != source_ref.get("content_digest")
                    or freeze_receipt.get("disposition") != "passed"
                ):
                    _fail("evidence_graph_observed_result_set_receipt_mismatch", f"{node_id} ResultSet冻结回执不一致")
                members_for_set = (result_set_members or {}).get(key, {})
                source_member = members_for_set.get(source_ref.get("member_ref"))
                if not isinstance(source_member, Mapping):
                    _fail("evidence_graph_observed_member_unresolved", f"{node_id} observed fact成员未解析")
                expected_member_digest = _digest_without_fields({"member": source_member})
                if source_ref.get("member_digest") != expected_member_digest:
                    _fail("evidence_graph_observed_member_digest_mismatch", f"{node_id} observed fact成员摘要不一致")
                if node_payload.get("fact_value_digest") != expected_member_digest:
                    projection_digest = source_ref.get("projection_receipt_digest")
                    if projection_digest is None:
                        _fail("evidence_graph_observed_projection_receipt_missing", f"{node_id} 字段投影缺少Operator回执")
                    projection = resolved_receipt(
                        projection_digest, subject=f"{node_id} fact projection"
                    )
                    if (
                        projection.get("receipt_kind") != "fact_projection"
                        or projection.get("source_member_digest")
                        != expected_member_digest
                        or projection.get("output_digest")
                        != node_payload.get("fact_value_digest")
                        or projection.get("disposition") != "passed"
                    ):
                        _fail("evidence_graph_observed_projection_mismatch", f"{node_id} 字段投影回执不一致")
                elif source_ref.get("projection_receipt_digest") is not None:
                    _fail("evidence_graph_observed_projection_mismatch", f"{node_id} 完整成员事实不得夹带投影回执")
        if node_type == "derived_fact" and node_payload.get("operator_id") != producer_id:
            _fail("evidence_graph_operator_payload_mismatch", f"{node_id} operator_id 与 producer 不一致")
        if node_type == "result_set":
            key = (node_payload.get("result_set_id"), node_payload.get("result_set_revision"))
            source_result_set = result_sets.get(key)
            if not isinstance(source_result_set, Mapping):
                _fail("evidence_graph_result_set_unresolved", f"{node_id} 引用未知 ResultSet")
            if source_result_set.get("source_identity", {}).get(
                "identity_digest"
            ) != payload.get("identity_digest"):
                _fail("evidence_graph_result_set_binding_mismatch", f"{node_id} ResultSet身份与图不一致")
            for field in ("manifest_digest", "content_digest"):
                if node_payload.get(field) != source_result_set.get(field):
                    _fail("evidence_graph_result_set_binding_mismatch", f"{node_id}.{field} 与 ResultSet 不一致")
            view_ref = node_payload.get("view_ref")
            views = {
                view.get("view_id")
                for view in source_result_set.get("preview_views", [])
                if isinstance(view, Mapping)
            }
            if view_ref is not None and view_ref not in views:
                _fail("evidence_graph_result_set_binding_mismatch", f"{node_id} 引用未知 preview view")
            expected_completeness = (
                "partial_page" if view_ref is not None else source_result_set.get("set_completeness")
            )
            if node.get("completeness") != expected_completeness:
                _fail("evidence_graph_result_set_binding_mismatch", f"{node_id} completeness 与 ResultSet 不一致")
        by_id[node_id] = node
    if any(root not in by_id for root in payload.get("root_node_ids", [])):
        _fail("evidence_graph_dangling_ref", "root引用不存在节点")

    def node_domain_digest(node: Mapping[str, Any]) -> Any:
        node_payload = node.get("payload", {})
        return {
            "observed_fact": node_payload.get("fact_value_digest"),
            "derived_fact": node_payload.get("operator_output_digest"),
            "result_set": node_payload.get("content_digest"),
        }.get(node.get("node_type"), node.get("payload_digest"))

    def node_relation_binding(node: Mapping[str, Any]) -> Mapping[str, Any]:
        projection = node.get("payload", {}).get("fact_value_projection")
        return {
            "node_id": node.get("node_id"),
            "node_payload_digest": node.get("payload_digest"),
            "domain_value_digest": node_domain_digest(node),
            "typed_value_schema_ref": (
                projection.get("value_schema_ref")
                if isinstance(projection, Mapping)
                else None
            ),
            "typed_value": (
                projection.get("value")
                if isinstance(projection, Mapping)
                else None
            ),
        }

    plan_identity = plan_definition.get("identity", {})
    expected_operator_identity = {
        field: (
            str(plan_identity.get(field)).removeprefix("sha256:")
            if field == "registry_snapshot_digest"
            else plan_identity.get(field)
        )
        for field in (
            "incident_id",
            "publication_id",
            "publication_revision",
            "publication_digest",
            "collector_id",
            "cohort_id",
            "cohort_digest",
            "window_start_utc",
            "window_end_utc",
            "data_through_utc",
            "registry_snapshot_id",
            "registry_snapshot_digest",
            "binding_generation",
        )
    }

    def validate_operator_artifact(
        artifact: Mapping[str, Any], *, expected_operator_id: str, subject: str
    ) -> Mapping[str, Any]:
        expected_fields = {
            "receipt_kind",
            "operator_id",
            "operator_version",
            "contract_digest",
            "output_schema_ref",
            "operator_output",
            "output_digest",
            "disposition",
            "receipt_digest",
        }
        if set(artifact) != expected_fields:
            _fail("evidence_graph_operator_artifact_schema_invalid", f"{subject}字段不闭合")
        registered_operator = registry_producers.get(expected_operator_id)
        if (
            not isinstance(registered_operator, Mapping)
            or registered_operator.get("unit_kind") != "operator"
            or registered_operator.get("lifecycle_state") != "active"
            or artifact.get("operator_version") != registered_operator.get("unit_version")
            or artifact.get("contract_digest")
            != str(registered_operator.get("contract_digest")).removeprefix("sha256:")
            or artifact.get("output_schema_ref")
            not in registered_operator.get("output_schema_refs", [])
        ):
            _fail("evidence_graph_relation_operator_not_admitted", f"{subject}未绑定Registry active Operator")
        output = artifact.get("operator_output")
        if not isinstance(output, Mapping) or not isinstance(operator_contract_schema, Mapping):
            _fail("evidence_graph_operator_output_schema_unresolved", f"{subject}缺少冻结Operator Schema")
        definition_name = _operator_output_definition_name(
            artifact.get("output_schema_ref"), operator_contract_schema
        )
        if definition_name != f"op{expected_operator_id.split('-')[1]}OutputEnvelope":
            _fail("evidence_graph_operator_output_schema_mismatch", f"{subject}输出Schema与Operator不一致")
        _validate_draft202012_subschema_instance(
            output,
            operator_contract_schema,
            definition_name=definition_name,
            subject=subject,
        )
        _validate_operator_edge_projection_views(
            expected_operator_id, output.get("result", {})
        )
        output_digest = output.get("output_digest")
        if (
            artifact.get("receipt_kind") != "registered_operator_output"
            or artifact.get("operator_id") != expected_operator_id
            or artifact.get("operator_version") != output.get("operator_version")
            or output.get("operator_id") != expected_operator_id
            or output.get("identity") != expected_operator_identity
            or artifact.get("output_digest") != output_digest
            or output_digest != _digest_without_fields(output, "output_digest")
            or artifact.get("disposition") != "passed"
        ):
            _fail("evidence_graph_relation_output_binding_mismatch", f"{subject}未绑定真实Operator OutputEnvelope")
        return output

    def operator_artifact_by_output_digest(output_digest: Any, *, subject: str) -> Mapping[str, Any]:
        matches = [
            item
            for item in receipt_store.values()
            if isinstance(item, Mapping) and item.get("output_digest") == output_digest
        ]
        if len(matches) != 1:
            _fail("evidence_graph_operator_output_unresolved", f"{subject}未唯一解析Operator输出")
        return matches[0]

    seen_edges: set[str] = set()
    forbidden = {"causes", "responsible_for", "customer_of", "recovered_from"}
    for edge in edges:
        edge_id = edge.get("edge_id") if isinstance(edge, dict) else None
        if not isinstance(edge_id, str) or edge_id in seen_edges:
            _fail("evidence_graph_duplicate_edge", "图边身份缺失或重复")
        seen_edges.add(edge_id)
        source = edge.get("from_node_id")
        target = edge.get("to_node_id")
        if source not in by_id or target not in by_id:
            _fail("evidence_graph_dangling_ref", "edge引用不存在节点")
        if edge.get("edge_type") in forbidden:
            _fail("evidence_graph_forbidden_relation", "事实图包含禁止关系")
        if by_id[source].get("committed") is not True or by_id[target].get("committed") is not True:
            _fail("evidence_graph_uncommitted_reference", "图边不得引用未提交节点")
        edge_producer = edge.get("producer_ref", {})
        edge_registry = registry_producers.get(edge_producer.get("producer_id"))
        if not isinstance(edge_registry, Mapping) or edge_registry.get("lifecycle_state") != "active":
            _fail("evidence_graph_producer_not_admitted", "图边 producer 未登记或非active")
        if (
            edge_producer.get("producer_kind") != edge_registry.get("unit_kind")
            or edge_producer.get("producer_version") != edge_registry.get("unit_version")
            or edge_producer.get("contract_digest")
            != str(edge_registry.get("contract_digest")).removeprefix("sha256:")
        ):
            _fail("evidence_graph_producer_binding_mismatch", "图边 producer 与 Registry 不一致")
        edge_producer_receipt = resolved_receipt(
            edge_producer.get("run_receipt_digest"), subject=f"{edge_id} producer"
        )
        if edge_producer_receipt.get(
            "producer_id", edge_producer_receipt.get("operator_id")
        ) != edge_producer.get("producer_id"):
            _fail("evidence_graph_producer_receipt_mismatch", f"{edge_id} producer回执不一致")
        edge_digest_body = dict(edge)
        edge_digest_body.pop("edge_digest", None)
        edge_digest_producer = dict(edge_digest_body.get("producer_ref", {}))
        edge_digest_producer.pop("run_receipt_digest", None)
        edge_digest_body["producer_ref"] = edge_digest_producer
        if edge.get("edge_digest") != _digest_without_fields(edge_digest_body):
            _fail("evidence_graph_edge_digest_mismatch", f"{edge_id} edge_digest 无法重算")
        typed_relation_types = {
            "at_time",
            "precedes",
            "same_window",
            "follows",
            "path_contains",
            "directly_adjacent_in_path",
            "set_intersects",
            "set_contains",
            "conflicts_with",
        }
        if edge.get("edge_type") not in typed_relation_types and edge_producer_receipt.get(
            "output_digest"
        ) != edge.get("edge_digest"):
            _fail("evidence_graph_producer_receipt_mismatch", f"{edge_id} producer回执未绑定边输出")
        if edge.get("edge_type") == "supports" and by_id[source].get("node_type") in {
            "execution_failure",
            "unknown",
        }:
            _fail("evidence_graph_support_ineligible", "失败或未知节点不得支持事实")
        if edge.get("edge_type") == "supports" and (
            by_id[source].get("committed") is not True
            or (
                by_id[source].get("node_type") == "result_set"
                and by_id[source].get("payload", {}).get("view_ref") is not None
            )
        ):
            _fail("evidence_graph_support_ineligible", "preview或未提交节点不得支持事实")
        if edge.get("edge_type") == "supports" and by_id[target].get("node_type") not in {
            "observed_fact",
            "derived_fact",
        }:
            _fail("evidence_graph_support_ineligible", "supports目标必须是事实节点")
        if edge.get("edge_type") == "member_of" and by_id[target].get("node_type") != "result_set":
            _fail("evidence_graph_relation_target_invalid", "member_of目标必须是result_set")
        if edge.get("edge_type") == "limited_by" and by_id[target].get("node_type") != "limitation":
            _fail("evidence_graph_relation_target_invalid", "limited_by目标必须是limitation")
        if edge.get("edge_type") == "requires_external_evidence" and by_id[target].get(
            "node_type"
        ) != "unknown":
            _fail("evidence_graph_relation_target_invalid", "requires_external_evidence目标必须是unknown")
        relation_ref = edge.get("relation_receipt_ref")
        if relation_ref is not None:
            relation_receipt = resolved_receipt(relation_ref, subject=f"{edge_id} relation")
            _validate_draft202012_subschema_instance(
                relation_receipt,
                schema,
                definition_name="relationReceipt",
                subject=f"{edge_id} relation receipt",
            )
            from_binding = relation_receipt.get("from_node_binding", {})
            to_binding = relation_receipt.get("to_node_binding", {})
            operator_binding = relation_receipt.get("operator_binding", {})
            projection = relation_receipt.get("projection", {})
            if (
                relation_receipt.get("relation_type") != edge.get("edge_type")
                or from_binding != node_relation_binding(by_id[source])
                or to_binding != node_relation_binding(by_id[target])
                or relation_receipt.get("identity_digest") != payload.get("identity_digest")
                or relation_receipt.get("registry_snapshot_id")
                != payload.get("registry_snapshot_id")
                or str(relation_receipt.get("registry_snapshot_digest")).removeprefix("sha256:")
                != str(payload.get("registry_snapshot_digest")).removeprefix("sha256:")
                or relation_receipt.get("projection_digest")
                != _digest_without_fields({"projection": projection})
                or relation_receipt.get("disposition") != "passed"
            ):
                _fail("evidence_graph_relation_receipt_mismatch", f"{edge_id} relation回执不一致")
            if (
                not isinstance(operator_binding, Mapping)
                or operator_binding.get("operator_id") != edge_producer.get("producer_id")
                or operator_binding.get("operator_version")
                != edge_producer.get("producer_version")
                or operator_binding.get("contract_digest")
                != edge_producer.get("contract_digest")
                or operator_binding.get("run_receipt_digest")
                != edge_producer.get("run_receipt_digest")
                or operator_binding.get("output_schema_ref")
                not in edge_registry.get("output_schema_refs", [])
                or operator_binding.get("output_digest")
                != edge_producer_receipt.get("output_digest")
                or edge_producer.get("producer_kind") != "operator"
            ):
                _fail("evidence_graph_relation_operator_not_admitted", f"{edge_id} relation未绑定登记Operator输出")
            operator_output = validate_operator_artifact(
                edge_producer_receipt,
                expected_operator_id=str(operator_binding.get("operator_id")),
                subject=f"{edge_id} Operator output",
            )
            if (
                edge_producer_receipt.get("contract_digest") != operator_binding.get("contract_digest")
                or edge_producer_receipt.get("output_schema_ref") != operator_binding.get("output_schema_ref")
            ):
                _fail("evidence_graph_relation_output_binding_mismatch", f"{edge_id} Operator制品与relation绑定不一致")

            relation_type = edge.get("edge_type")
            operator_id = operator_binding.get("operator_id")
            result = operator_output.get("result", {})
            output_digest = operator_output.get("output_digest")
            projection_candidates = result.get("edge_projections")
            if projection_candidates is None:
                one_projection = result.get("edge_projection")
                projection_candidates = (
                    [one_projection] if isinstance(one_projection, Mapping) else []
                )
            asn_schema_ref = "https://domeye.example/types/asn.json"
            if operator_id == "OP-15" and (
                to_binding.get("typed_value_schema_ref") != asn_schema_ref
                or to_binding.get("typed_value") != result.get("target_asn")
            ):
                _fail(
                    "evidence_graph_relation_endpoint_semantics_mismatch",
                    f"{edge_id} path_contains目标端必须绑定OP-15 target_asn事实",
                )
            if operator_id == "OP-16" and (
                from_binding.get("typed_value_schema_ref") != asn_schema_ref
                or to_binding.get("typed_value_schema_ref") != asn_schema_ref
                or from_binding.get("typed_value") != result.get("target_asn")
                or to_binding.get("typed_value")
                != projection.get("neighbor_asn")
            ):
                _fail(
                    "evidence_graph_relation_endpoint_semantics_mismatch",
                    f"{edge_id} direct adjacency两端必须绑定OP-16 target/neighbor ASN事实",
                )
            expected_edge_projection = {
                "relation_type": relation_type,
                "from_endpoint": {
                    "domain_value_digest": from_binding.get(
                        "domain_value_digest"
                    ),
                    "typed_value": from_binding.get("typed_value"),
                },
                "to_endpoint": {
                    "domain_value_digest": to_binding.get("domain_value_digest"),
                    "typed_value": to_binding.get("typed_value"),
                },
                "relation_projection": projection,
                "relation_projection_digest": relation_receipt.get(
                    "projection_digest"
                ),
                "publishable": True,
            }
            if relation_type in typed_relation_types:
                if expected_edge_projection not in projection_candidates:
                    _fail(
                        "evidence_graph_relation_endpoint_semantics_mismatch",
                        f"{edge_id} 端点与关系必须精确引用Operator输出的可发布edge投影视图",
                    )
                # 链式Operator只核验上游制品身份、摘要与字段相等；Host不重算
                # 路径邻接、集合覆盖、时间方向或一致性业务语义。
                if operator_id == "OP-16":
                    position_artifact = operator_artifact_by_output_digest(
                        result.get("position_receipt_digest"),
                        subject=f"{edge_id} OP-15 position",
                    )
                    position_output = validate_operator_artifact(
                        position_artifact,
                        expected_operator_id="OP-15",
                        subject=f"{edge_id} OP-15 position",
                    )
                    if (
                        result.get("position_receipt_digest")
                        != position_output.get("output_digest")
                        or result.get("path_digest")
                        != position_output.get("result", {}).get("path_digest")
                    ):
                        _fail(
                            "evidence_graph_relation_output_binding_mismatch",
                            f"{edge_id} OP-16未绑定真实OP-15位置回执",
                        )
                if operator_id == "OP-37":
                    temporal_artifact = operator_artifact_by_output_digest(
                        result.get("temporal_receipt_digest"),
                        subject=f"{edge_id} OP-29 temporal",
                    )
                    temporal_output = validate_operator_artifact(
                        temporal_artifact,
                        expected_operator_id="OP-29",
                        subject=f"{edge_id} OP-29 temporal",
                    )
                    temporal_result = temporal_output.get("result", {})
                    if (
                        result.get("temporal_receipt_digest")
                        != temporal_output.get("output_digest")
                        or result.get("left_digest")
                        != temporal_result.get("left_digest")
                        or result.get("right_digest")
                        != temporal_result.get("right_digest")
                    ):
                        _fail(
                            "evidence_graph_relation_output_binding_mismatch",
                            f"{edge_id} OP-37未绑定同一事实对的真实OP-29回执",
                        )
                continue
        elif edge.get("edge_type") in typed_relation_types:
            _fail("evidence_graph_relation_receipt_schema_invalid", f"{edge_id} 语义关系缺少typed receipt")

    derived_sources: dict[str, list[str]] = {}
    for edge in edges:
        if edge.get("edge_type") == "derived_from":
            target = edge.get("to_node_id")
            source = edge.get("from_node_id")
            if by_id[target].get("node_type") != "derived_fact":
                _fail("evidence_graph_provenance_invalid", "derived_from目标必须是derived_fact")
            derived_sources.setdefault(target, []).append(source)
    for node_id, node in by_id.items():
        if node.get("node_type") != "derived_fact":
            continue
        provenance = node.get("provenance_node_ids", [])
        if set(provenance) != set(derived_sources.get(node_id, [])) or len(provenance) != len(
            derived_sources.get(node_id, [])
        ):
            _fail("evidence_graph_provenance_invalid", f"{node_id} provenance与derived_from边不一致")
        producer_registry = registry_producers[node.get("producer_ref", {}).get("producer_id")]
        if any(
            by_id[source].get("node_type") in {"execution_failure", "unknown"}
            or (
                by_id[source].get("node_type") == "result_set"
                and (
                    by_id[source].get("payload", {}).get("view_ref") is not None
                    or by_id[source].get("completeness") != "complete"
                )
            )
            for source in provenance
        ) and producer_registry.get("monotonic_incomplete_input_allowed") is not True:
            _fail("evidence_graph_provenance_input_ineligible", f"{node_id} 使用了失败、未知、preview或不完整人口")
        expected_input_digests = [by_id[source]["payload_digest"] for source in provenance]
        if node.get("payload", {}).get("operator_input_digests") != expected_input_digests:
            _fail("evidence_graph_operator_input_digest_mismatch", f"{node_id} Operator输入摘要未闭合")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit_derived(node_id: str) -> None:
        if node_id in visiting:
            _fail("evidence_graph_provenance_cycle", f"derived_from存在环：{node_id}")
        if node_id in visited:
            return
        visiting.add(node_id)
        for source in derived_sources.get(node_id, []):
            if by_id[source].get("node_type") == "derived_fact":
                visit_derived(source)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in derived_sources:
        visit_derived(node_id)
    if payload.get("graph_digest") != _evidence_graph_content_digest(payload):
        _fail("evidence_graph_digest_mismatch", "graph_digest 无法从图内容重算")
    if payload.get("graph_state") == "committed":
        closure = resolved_receipt(payload.get("closure_receipt_digest"), subject="graph closure")
        commit = resolved_receipt(payload.get("commit_receipt_digest"), subject="graph commit")
        if (
            closure.get("graph_digest") != payload.get("graph_digest")
            or closure.get("disposition") != "passed"
            or commit.get("graph_digest") != payload.get("graph_digest")
            or commit.get("disposition") != "committed"
        ):
            _fail("evidence_graph_commit_receipt_mismatch", "EvidenceGraph closure/commit回执未绑定图摘要")


def _resolve_trusted_committed_graph(
    store: Mapping[str, Any], *, graph_digest: Any
) -> Mapping[str, Any]:
    if set(store) != {
        "store_contract_id",
        "trust_origin",
        "caller_mutable",
        "attestation_provider_id",
        "attestation_contract_digest",
        "graphs",
    } or (
        store.get("store_contract_id")
        != "country_outage_p2_trusted_committed_graph_store_v1"
        or store.get("trust_origin") != "host_authenticated_runtime_store"
        or store.get("caller_mutable") is not False
        or store.get("attestation_provider_id")
        != "country_outage_p2_committed_graph_store_host"
        or store.get("attestation_contract_digest")
        != COMMITTED_GRAPH_STORE_ATTESTATION_CONTRACT_DIGEST
    ):
        _fail("committed_graph_store_untrusted", "EvidenceGraph未从Host受信提交图存储解析")
    record = store.get("graphs", {}).get(graph_digest)
    if not isinstance(record, Mapping) or set(record) != {
        "graph",
        "validation_receipt",
        "validation_context",
    }:
        _fail("committed_graph_unresolved", "受信存储中不存在指定EvidenceGraph")
    graph = record.get("graph")
    receipt = record.get("validation_receipt")
    context = record.get("validation_context")
    if (
        not isinstance(graph, Mapping)
        or not isinstance(receipt, Mapping)
        or not isinstance(context, Mapping)
        or set(context)
        != {
            "schema",
            "plan_definition",
            "investigation_snapshot",
            "trusted_registry_store",
            "result_set_records",
            "result_set_member_records",
            "receipt_store",
            "operator_contract_schema",
            "previous_graph",
        }
    ):
        _fail("committed_graph_unresolved", "受信EvidenceGraph记录无效")
    result_sets: dict[tuple[Any, Any], Mapping[str, Any]] = {}
    result_set_records = context.get("result_set_records")
    if not isinstance(result_set_records, list):
        _fail("committed_graph_validation_context_invalid", "ResultSet重放上下文必须是JSON-safe排序记录")
    prior_result_key: tuple[str, int] | None = None
    for record_item in result_set_records:
        if not isinstance(record_item, Mapping) or set(record_item) != {
            "result_set_id",
            "result_set_revision",
            "result_set",
        }:
            _fail("committed_graph_validation_context_invalid", "ResultSet重放记录字段不闭合")
        key = (record_item.get("result_set_id"), record_item.get("result_set_revision"))
        if (
            not isinstance(key[0], str)
            or not isinstance(key[1], int)
            or key in result_sets
            or (prior_result_key is not None and key <= prior_result_key)
            or not isinstance(record_item.get("result_set"), Mapping)
            or record_item["result_set"].get("result_set_id") != key[0]
            or record_item["result_set"].get("result_set_revision") != key[1]
        ):
            _fail("committed_graph_validation_context_invalid", "ResultSet重放记录身份、顺序或人口不闭合")
        result_sets[key] = record_item["result_set"]
        prior_result_key = key
    result_set_members: dict[tuple[Any, Any], dict[str, Mapping[str, Any]]] = {}
    member_records = context.get("result_set_member_records")
    if not isinstance(member_records, list):
        _fail("committed_graph_validation_context_invalid", "ResultSet成员重放上下文必须是JSON-safe排序记录")
    prior_member_set_key: tuple[str, int] | None = None
    for member_record in member_records:
        if not isinstance(member_record, Mapping) or set(member_record) != {
            "result_set_id",
            "result_set_revision",
            "members",
        }:
            _fail("committed_graph_validation_context_invalid", "ResultSet成员重放记录字段不闭合")
        key = (member_record.get("result_set_id"), member_record.get("result_set_revision"))
        rows = member_record.get("members")
        if (
            key not in result_sets
            or key in result_set_members
            or (prior_member_set_key is not None and key <= prior_member_set_key)
            or not isinstance(rows, list)
        ):
            _fail("committed_graph_validation_context_invalid", "ResultSet成员重放记录身份、顺序或来源不闭合")
        members: dict[str, Mapping[str, Any]] = {}
        prior_member_ref: str | None = None
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != {"member_ref", "member"}:
                _fail("committed_graph_validation_context_invalid", "ResultSet成员记录字段不闭合")
            member_ref = row.get("member_ref")
            if (
                not isinstance(member_ref, str)
                or member_ref in members
                or (prior_member_ref is not None and member_ref <= prior_member_ref)
                or not isinstance(row.get("member"), Mapping)
            ):
                _fail("committed_graph_validation_context_invalid", "ResultSet成员身份、顺序或内容不闭合")
            members[member_ref] = row["member"]
            prior_member_ref = member_ref
        result_set_members[key] = members
        prior_member_set_key = key
    validate_evidence_graph_instance(
        graph,
        schema=context["schema"],
        plan_definition=context["plan_definition"],
        investigation_snapshot=context["investigation_snapshot"],
        trusted_registry_store=context["trusted_registry_store"],
        result_sets=result_sets,
        receipt_store=context["receipt_store"],
        result_set_members=result_set_members,
        operator_contract_schema=context["operator_contract_schema"],
        previous_graph=context["previous_graph"],
    )
    recomputed_graph_digest = _evidence_graph_content_digest(graph)
    if graph.get("graph_digest") != recomputed_graph_digest:
        _fail("committed_graph_payload_digest_mismatch", "受信EvidenceGraph内容与graph_digest不一致")
    if (
        receipt.get("receipt_digest") != _digest_without_fields(receipt, "receipt_digest")
        or receipt.get("receipt_kind") != "validated_committed_evidence_graph"
        or receipt.get("validator_id") != COMMITTED_GRAPH_VALIDATOR_ID
        or receipt.get("validator_version") != COMMITTED_GRAPH_VALIDATOR_VERSION
        or receipt.get("validator_contract_digest")
        != COMMITTED_GRAPH_VALIDATOR_CONTRACT_DIGEST
        or receipt.get("validator_implementation_digest")
        != COMMITTED_GRAPH_VALIDATOR_IMPLEMENTATION_DIGEST
        or receipt.get("graph_id") != graph.get("graph_id")
        or receipt.get("graph_revision") != graph.get("graph_revision")
        or receipt.get("graph_digest") != graph.get("graph_digest")
        or graph.get("graph_digest") != graph_digest
        or receipt.get("graph_state") != "committed"
        or graph.get("graph_state") != "committed"
        or receipt.get("plan_revision") != graph.get("plan_revision")
        or receipt.get("plan_digest") != graph.get("plan_digest")
        or receipt.get("identity_digest") != graph.get("identity_digest")
        or receipt.get("registry_snapshot_id") != graph.get("registry_snapshot_id")
        or receipt.get("registry_snapshot_digest")
        != graph.get("registry_snapshot_digest")
        or receipt.get("validation_context_digest")
        != _digest_without_fields({"validation_context": context})
        or receipt.get("disposition") != "passed"
    ):
        _fail("committed_graph_validation_receipt_invalid", "EvidenceGraph受信验证回执未绑定提交图")
    return graph


def _resolve_trusted_validated_plan(
    store: Mapping[str, Any],
    *,
    plan_id: Any,
    plan_revision: Any,
    plan_schema: Mapping[str, Any],
) -> Mapping[str, Any]:
    if set(store) != {
        "store_contract_id",
        "trust_origin",
        "caller_mutable",
        "attestation_provider_id",
        "attestation_contract_digest",
        "plans",
    } or (
        store.get("store_contract_id") != "country_outage_p2_trusted_validated_plan_store_v1"
        or store.get("trust_origin") != "host_authenticated_runtime_store"
        or store.get("caller_mutable") is not False
        or store.get("attestation_provider_id") != "country_outage_p2_validated_plan_store_host"
        or store.get("attestation_contract_digest")
        != VALIDATED_PLAN_STORE_ATTESTATION_CONTRACT_DIGEST
    ):
        _fail("validated_plan_store_untrusted", "降级计划未从Host受信验证计划存储解析")
    key = f"{plan_id}@{plan_revision}"
    record = store.get("plans", {}).get(key)
    if not isinstance(record, Mapping) or set(record) != {
        "plan",
        "validation_receipt",
        "validation_context",
    }:
        _fail("validated_plan_unresolved", "受信存储中不存在指定计划修订")
    plan = record.get("plan")
    receipt = record.get("validation_receipt")
    context = record.get("validation_context")
    if (
        not isinstance(plan, Mapping)
        or not isinstance(receipt, Mapping)
        or not isinstance(context, Mapping)
        or set(context)
        != {
            "trusted_registry_store",
            "trusted_admission_receipt_store",
            "trusted_node_result_receipt_store",
            "parameter_bindings",
            "previous_plan_definition",
            "previous_investigation_snapshot",
        }
    ):
        _fail("validated_plan_unresolved", "受信计划记录无效")
    validate_investigation_plan_instance(
        plan,
        schema=plan_schema,
        trusted_registry_store=context["trusted_registry_store"],
        trusted_admission_receipt_store=context[
            "trusted_admission_receipt_store"
        ],
        trusted_node_result_receipt_store=context[
            "trusted_node_result_receipt_store"
        ],
        parameter_bindings=context["parameter_bindings"],
        previous_plan_definition=context["previous_plan_definition"],
        previous_investigation_snapshot=context[
            "previous_investigation_snapshot"
        ],
    )
    definition = plan.get("plan_definition", {})
    policy = definition.get("answer_execution_policy", {})
    plan_digest = _digest_without_fields({"plan_definition": definition})
    if (
        receipt.get("receipt_digest") != _digest_without_fields(receipt, "receipt_digest")
        or receipt.get("receipt_kind") != "validated_investigation_plan"
        or receipt.get("validator_id") != VALIDATED_PLAN_VALIDATOR_ID
        or receipt.get("validator_version") != VALIDATED_PLAN_VALIDATOR_VERSION
        or receipt.get("validator_contract_digest") != VALIDATED_PLAN_VALIDATOR_CONTRACT_DIGEST
        or receipt.get("validator_implementation_digest")
        != VALIDATED_PLAN_VALIDATOR_IMPLEMENTATION_DIGEST
        or receipt.get("plan_id") != definition.get("plan_id")
        or receipt.get("plan_revision") != definition.get("plan_revision")
        or definition.get("plan_id") != plan_id
        or definition.get("plan_revision") != plan_revision
        or receipt.get("plan_payload_digest") != _digest_without_fields({"plan": plan})
        or receipt.get("plan_digest") != plan_digest
        or receipt.get("admission_receipt_digest")
        != definition.get("admission_receipt_digest")
        or receipt.get("effective_teacher_required") != policy.get("teacher_required")
        or receipt.get("authorization_digest") != policy.get("authorization_digest")
        or receipt.get("validation_context_digest")
        != _digest_without_fields({"validation_context": context})
        or receipt.get("disposition") != "passed"
        or definition.get("plan_state") != "admitted"
    ):
        _fail("validated_plan_receipt_invalid", "受信计划验证回执未绑定完整已准入计划")
    return plan


def validate_dual_model_flow_instance(
    payload: Mapping[str, Any],
    *,
    schema: Mapping[str, Any],
    previous_flow: Mapping[str, Any] | None = None,
    evidence_graph: Mapping[str, Any] | None = None,
    trusted_committed_graph_store: Mapping[str, Any] | None = None,
    trusted_validated_plan_store: Mapping[str, Any] | None = None,
    investigation_plan_schema: Mapping[str, Any] | None = None,
    trusted_oracle_store: Mapping[str, Any] | None = None,
    trusted_student_answer_artifact_store: Mapping[str, Any] | None = None,
    trusted_alignment_receipt_store: Mapping[str, Any] | None = None,
    publish_receipt: Mapping[str, Any] | None = None,
) -> None:
    """验证 Sol→Validator→DS 的角色、同绑定、Gate、修订与降级闭包。"""

    _validate_draft202012_instance(
        payload,
        schema,
        expected_schema_id=(
            "https://domeye.example/contracts/agent/country-outage-p2-s1/"
            "dual-model-answer-flow.schema.json"
        ),
        subject="DualModelAnswerFlow",
    )
    revision = payload.get("flow_revision")
    parent = payload.get("parent_flow_revision")
    if revision == 1 and (parent is not None or previous_flow is not None):
        _fail("revision_parent_invalid", "DualModel flow revision=1时父修订必须为空")
    if isinstance(revision, int) and revision > 1:
        previous_revision = (
            previous_flow.get("flow_revision")
            if isinstance(previous_flow, Mapping)
            else None
        )
        if revision != (previous_revision or -1) + 1 or parent != previous_revision:
            _fail("revision_chain_invalid", "DualModel flow修订必须连续且指向前一完整修订")
        if previous_flow.get("flow_id") != payload.get("flow_id"):
            _fail("revision_chain_identity_mismatch", "DualModel前一修订属于其他flow_id")

    root_digest = payload.get("shared_answer_binding_digest")
    binding = payload.get("shared_answer_binding", {})
    if root_digest != _digest_without_fields(binding):
        _fail("dual_model_binding_digest_mismatch", "shared_answer_binding_digest 无法重算")
    _validate_identity_time_order(binding, subject="DualModel shared_answer_binding")
    if payload.get("final_disposition") in {"aligned_published", "ds_unaligned_degraded"}:
        if not isinstance(evidence_graph, Mapping) or not isinstance(
            trusted_committed_graph_store, Mapping
        ):
            _fail("committed_graph_unresolved", "回答发布必须解析Host受信的已验证提交图")
        trusted_graph = _resolve_trusted_committed_graph(
            trusted_committed_graph_store,
            graph_digest=binding.get("evidence_graph_digest"),
        )
        if trusted_graph != evidence_graph:
            _fail("committed_graph_context_mismatch", "调用上下文EvidenceGraph不是受信存储中的同一对象")
        evidence_graph = trusted_graph
    if isinstance(evidence_graph, Mapping):
        identity_projection = {
            field: binding.get(field)
            for field in (
                "incident_id",
                "publication_id",
                "publication_revision",
                "publication_digest",
                "collector_id",
                "cohort_id",
                "cohort_digest",
                "window_start_utc",
                "window_end_utc",
                "data_through_utc",
                "finality",
                "registry_snapshot_id",
                "registry_snapshot_digest",
                "binding_generation",
            )
        }
        identity_projection["registry_snapshot_digest"] = str(
            identity_projection["registry_snapshot_digest"]
        ).removeprefix("sha256:")
        if (
            _digest_without_fields(identity_projection)
            != evidence_graph.get("identity_digest")
            or binding.get("evidence_graph_revision")
            != evidence_graph.get("graph_revision")
            or binding.get("evidence_graph_digest") != evidence_graph.get("graph_digest")
            or binding.get("plan_id") != evidence_graph.get("plan_id")
            or binding.get("plan_revision") != evidence_graph.get("plan_revision")
            or binding.get("investigation_plan_digest") != evidence_graph.get("plan_digest")
            or binding.get("registry_snapshot_id")
            != evidence_graph.get("registry_snapshot_id")
            or str(binding.get("registry_snapshot_digest")).removeprefix("sha256:")
            != str(evidence_graph.get("registry_snapshot_digest")).removeprefix("sha256:")
        ):
            _fail("dual_model_evidence_identity_mismatch", "DualModel共享身份未绑定同一EvidenceGraph与Plan")
    for identity_name in ("teacher_model_identity", "student_model_identity"):
        identity = payload.get(identity_name, {})
        if identity.get("identity_digest") != _digest_without_fields(identity, "identity_digest"):
            _fail("model_identity_digest_mismatch", f"{identity_name}.identity_digest 无法重算")
    student_identity = payload.get("student_model_identity", {})
    if (
        student_identity.get("provider") != "deepseek"
        or student_identity.get("model") != "deepseek-v4-flash"
        or student_identity.get("version") != "deepseek-v4-flash-pi-0.84.1-v1"
        or student_identity.get("expected_response_model") != "deepseek-v4-flash"
        or student_identity.get("pi_version") != "0.84.1"
        or student_identity.get("candidate_resource_sha256")
        != "ac00eeb087bc9651fd27391066d9d16a416aad887cb552737696289ded3ce2b5"
        or student_identity.get("profile_registry_sha256")
        != "e8881aa2b79f495da3ea551bb3b2423af45c118f5e622ac1877852bf0087bf4f"
    ):
        _fail("ds_model_identity_mismatch", "DualModel未绑定S1D-5冻结的DS候选资源与评测Profile身份")
    teacher_plan_run = payload.get("teacher_plan_run_receipt")
    teacher_plan_grounding = payload.get("teacher_plan_grounding_receipt")
    teacher_run = payload.get("teacher_run_receipt")
    teacher_reference = payload.get("teacher_reference")
    teacher_validation = payload.get("teacher_validation_receipt")
    teacher_coverage = payload.get("teacher_oracle_coverage_receipt")
    students = payload.get("student_runs", [])
    student_validation = payload.get("student_validation_receipt")
    alignment = payload.get("alignment_run_receipt")
    published = payload.get("published_answer")
    objects = [
        teacher_plan_run,
        teacher_run,
        teacher_reference,
        teacher_validation,
        teacher_coverage,
        student_validation,
        alignment,
        published,
    ]
    objects.extend(
        run.get("run_receipt") for run in students if isinstance(run, dict)
    )
    objects.extend(
        run.get("validation_receipt") for run in students if isinstance(run, dict)
    )
    if any(
        item is not None and item.get("shared_answer_binding_digest") != root_digest
        for item in objects
        if isinstance(item, dict)
    ):
        _fail("dual_model_shared_binding_mismatch", "双模型对象未共享同一binding digest")
    ordinals = [run.get("revision_ordinal") for run in students]
    if ordinals not in ([], [0], [0, 1]):
        _fail("student_revision_sequence_invalid", "Student修订只能是[]、[0]或[0,1]")
    if len(students) == 2 and not isinstance(payload.get("structured_feedback"), dict):
        _fail("student_feedback_missing", "第二次Student运行缺少唯一结构化反馈")
    if len(students) < 2 and payload.get("structured_feedback") is not None:
        _fail("student_feedback_unexpected", "没有第二次Student运行时不得携带结构化反馈")

    def resolve_trusted_student_artifact(artifact: Mapping[str, Any]) -> None:
        artifact_ref = artifact.get("artifact_ref")
        stored = (
            trusted_student_answer_artifact_store.get("artifacts", {}).get(artifact_ref)
            if isinstance(trusted_student_answer_artifact_store, Mapping)
            and isinstance(
                trusted_student_answer_artifact_store.get("artifacts"), Mapping
            )
            else None
        )
        if (
            not isinstance(trusted_student_answer_artifact_store, Mapping)
            or set(trusted_student_answer_artifact_store)
            != {
                "store_contract_id",
                "trust_origin",
                "caller_mutable",
                "attestation_provider_id",
                "attestation_contract_digest",
                "artifacts",
            }
            or trusted_student_answer_artifact_store.get("store_contract_id")
            != "country_outage_p2_trusted_student_answer_artifact_store_v1"
            or trusted_student_answer_artifact_store.get("trust_origin")
            != "host_authenticated_runtime_store"
            or trusted_student_answer_artifact_store.get("caller_mutable") is not False
            or trusted_student_answer_artifact_store.get("attestation_provider_id")
            != "country_outage_p2_student_answer_artifact_store_host"
            or trusted_student_answer_artifact_store.get(
                "attestation_contract_digest"
            )
            != STUDENT_ANSWER_ARTIFACT_STORE_ATTESTATION_CONTRACT_DIGEST
            or not isinstance(stored, Mapping)
            or stored != artifact
        ):
            _fail(
                "student_answer_artifact_unresolved",
                "Student回答制品必须解析Host受信、内容寻址且可回放的Artifact",
            )

    def validate_alignment_receipt_trust() -> None:
        receipt_digest = alignment.get("receipt_digest") if isinstance(alignment, Mapping) else None
        stored = (
            trusted_alignment_receipt_store.get("receipts", {}).get(receipt_digest)
            if isinstance(trusted_alignment_receipt_store, Mapping)
            and isinstance(trusted_alignment_receipt_store.get("receipts"), Mapping)
            else None
        )
        if (
            not isinstance(trusted_alignment_receipt_store, Mapping)
            or set(trusted_alignment_receipt_store)
            != {
                "store_contract_id",
                "trust_origin",
                "caller_mutable",
                "attestation_provider_id",
                "attestation_contract_digest",
                "receipts",
            }
            or trusted_alignment_receipt_store.get("store_contract_id")
            != "country_outage_p2_trusted_alignment_receipt_store_v1"
            or trusted_alignment_receipt_store.get("trust_origin")
            != "host_authenticated_runtime_store"
            or trusted_alignment_receipt_store.get("caller_mutable") is not False
            or trusted_alignment_receipt_store.get("attestation_provider_id")
            != "country_outage_p2_alignment_receipt_store_host"
            or trusted_alignment_receipt_store.get("attestation_contract_digest")
            != ALIGNMENT_RECEIPT_STORE_ATTESTATION_CONTRACT_DIGEST
            or not isinstance(stored, Mapping)
            or stored != alignment
        ):
            _fail(
                "alignment_receipt_untrusted",
                "Alignment结果必须解析Host确定性Evaluator写入的受信回执",
            )

    def validate_gates(receipt: Any, *, require_pass: bool | None) -> None:
        if not isinstance(receipt, dict):
            _fail("validation_receipt_missing", "验证回执缺失")
        results = receipt.get("gate_results", [])
        if [item.get("gate_id") for item in results] != [
            "GATE-01",
            "GATE-02",
            "GATE-03",
            "GATE-04",
            "GATE-05",
        ]:
            _fail("validation_gate_population_invalid", "Gate必须按GATE-01至05各一次")
        calculated = all(item.get("passed") is True for item in results)
        if any(
            item.get("receipt_digest") != _digest_without_fields(item, "receipt_digest")
            for item in results
        ):
            _fail("validation_gate_receipt_digest_mismatch", "单Gate回执摘要无法重算")
        if receipt.get("all_gates_passed") is not calculated:
            _fail("validation_gate_summary_mismatch", "Gate汇总与逐Gate结果不一致")
        if receipt.get("receipt_digest") != _digest_without_fields(receipt, "receipt_digest"):
            _fail("validation_receipt_digest_mismatch", "验证回执摘要无法重算")
        if require_pass is True and (
            receipt.get("all_gates_passed") is not True
            or any(item.get("passed") is not True for item in results)
        ):
            _fail("validation_gate_failed", "发布前五个Gate必须全部通过")
        if require_pass is False and calculated:
            _fail("validation_gate_rejection_mismatch", "拒绝回执必须至少有一个失败Gate")

    def validate_teacher_planning_chain() -> None:
        if (
            not isinstance(teacher_plan_run, dict)
            or not isinstance(teacher_plan_grounding, dict)
        ):
            _fail("teacher_plan_run_invalid", "Sol planning或Host Grounding回执缺失")
        if (
            teacher_plan_run.get("role") != "teacher"
            or teacher_plan_run.get("run_phase") != "sol_planning"
            or teacher_plan_run.get("disposition") != "completed"
            or teacher_plan_run.get("exact_model_identity")
            != payload.get("teacher_model_identity")
            or teacher_plan_run.get("output_digest")
            != binding.get("teacher_semantic_plan_digest")
            or teacher_plan_run.get("validation_receipt_digest")
            != binding.get("teacher_plan_grounding_receipt_digest")
        ):
            _fail("teacher_plan_run_invalid", "Sol planning运行没有绑定TeacherSemanticPlan与Host Grounding回执")
        expected_plan_input = _digest_without_fields(
            {
                "role": "teacher",
                "run_phase": "sol_planning",
                "question_digest": binding.get("question_digest"),
                "goal_digest": binding.get("goal_digest"),
                "incident_id": binding.get("incident_id"),
                "publication_id": binding.get("publication_id"),
                "publication_revision": binding.get("publication_revision"),
                "collector_id": binding.get("collector_id"),
                "prompt_digest": binding.get("prompt_digest"),
                "policy_digest": binding.get("policy_digest"),
            }
        )
        if teacher_plan_run.get("role_specific_input_digest") != expected_plan_input:
            _fail("teacher_plan_input_digest_mismatch", "Sol planning输入摘要无法重算")
        if (
            teacher_plan_grounding.get("teacher_semantic_plan_digest")
            != binding.get("teacher_semantic_plan_digest")
            or teacher_plan_grounding.get("grounding_plan_digest")
            != binding.get("grounding_plan_digest")
            or teacher_plan_grounding.get("registry_snapshot_digest")
            != binding.get("registry_snapshot_digest")
            or teacher_plan_grounding.get("disposition") != "passed"
            or teacher_plan_grounding.get("receipt_digest")
            != binding.get("teacher_plan_grounding_receipt_digest")
            or teacher_plan_grounding.get("receipt_digest")
            != _digest_without_fields(teacher_plan_grounding, "receipt_digest")
        ):
            _fail("teacher_plan_grounding_invalid", "Host Grounding回执未绑定Teacher提议与GroundingPlan")

    def validate_teacher_chain(
        *,
        require_pass: bool,
        coverage_required: bool = True,
        coverage_must_pass: bool = True,
    ) -> None:
        validate_teacher_planning_chain()
        if (
            not isinstance(teacher_run, dict)
            or not isinstance(teacher_reference, dict)
            or (coverage_required and not isinstance(teacher_coverage, dict))
        ):
            _fail("teacher_run_invalid", "Teacher Reference或Oracle覆盖回执缺失")
        if teacher_run.get("role") != "teacher" or teacher_run.get(
            "exact_model_identity"
        ) != payload.get("teacher_model_identity") or teacher_run.get(
            "run_phase"
        ) != "sol_reference":
            _fail("teacher_run_invalid", "Teacher角色或精确模型身份不一致")
        if teacher_reference.get("output_digest") != _digest_without_fields(
            teacher_reference, "output_digest"
        ):
            _fail("teacher_reference_digest_mismatch", "TeacherReference output_digest 无法重算")
        if teacher_run.get("output_digest") != teacher_reference.get("output_digest"):
            _fail("teacher_output_binding_mismatch", "Teacher run output未绑定TeacherReference")
        validate_gates(teacher_validation, require_pass=require_pass)
        if teacher_validation.get("subject_digest") != teacher_reference.get("output_digest"):
            _fail("teacher_validation_subject_mismatch", "Teacher验证subject未绑定TeacherReference")
        if teacher_run.get("validation_receipt_digest") != teacher_validation.get(
            "receipt_digest"
        ):
            _fail("teacher_validation_receipt_binding_mismatch", "Teacher run未绑定验证回执")
        expected_input = _digest_without_fields(
            {
                "role": "teacher",
                "run_phase": "sol_reference",
                "shared_answer_binding_digest": root_digest,
            }
        )
        if teacher_run.get("role_specific_input_digest") != expected_input:
            _fail("teacher_input_digest_mismatch", "Teacher role_specific_input_digest 无法重算")
        if not coverage_required:
            if teacher_coverage is not None:
                _fail("teacher_oracle_coverage_invalid", "Teacher Gate拒绝后不得伪造Oracle覆盖回执")
            coverage_passed = False
        else:
            coverage_passed = (
                teacher_coverage.get("required_fact_ids_complete") is True
                and teacher_coverage.get("required_boundary_assertions_complete") is True
                and teacher_coverage.get("required_unknowns_complete") is True
                and teacher_coverage.get("prohibited_assertion_count") == 0
                and teacher_coverage.get("disposition") == "passed"
            )
        if coverage_required:
            oracle_digest = teacher_coverage.get("oracle_digest")
        else:
            oracle_digest = None
        oracle_record = (
            trusted_oracle_store.get("oracles", {}).get(oracle_digest)
            if isinstance(trusted_oracle_store, Mapping)
            and isinstance(trusted_oracle_store.get("oracles"), Mapping)
            else None
        )
        if coverage_required and (
            not isinstance(trusted_oracle_store, Mapping)
            or set(trusted_oracle_store)
            != {
                "store_contract_id",
                "trust_origin",
                "caller_mutable",
                "attestation_provider_id",
                "attestation_contract_digest",
                "oracles",
            }
            or trusted_oracle_store.get("store_contract_id")
            != "country_outage_p2_trusted_oracle_store_v1"
            or trusted_oracle_store.get("trust_origin")
            != "host_authenticated_runtime_store"
            or trusted_oracle_store.get("caller_mutable") is not False
            or trusted_oracle_store.get("attestation_provider_id")
            != "country_outage_p2_oracle_store_host"
            or trusted_oracle_store.get("attestation_contract_digest")
            != ORACLE_STORE_ATTESTATION_CONTRACT_DIGEST
            or not isinstance(oracle_record, Mapping)
            or oracle_record.get("oracle_digest") != oracle_digest
            or oracle_record.get("question_id") != binding.get("question_id")
            or not isinstance(
                oracle_record.get("allowed_boundary_assertion_ids"), list
            )
            or not set(
                oracle_record.get("required_boundary_assertion_ids", [])
            ).issubset(set(oracle_record.get("allowed_boundary_assertion_ids", [])))
            or oracle_digest != _digest_without_fields(oracle_record, "oracle_digest")
        ):
            _fail("teacher_oracle_unresolved", "Oracle覆盖必须解析Host受信的同题内容寻址Oracle")
        calculated_fact_complete = coverage_required and set(
            oracle_record.get("required_fact_ids", [])
        ).issubset(set(teacher_reference.get("required_fact_ids", [])))
        calculated_boundary_complete = coverage_required and set(
            oracle_record.get("required_boundary_assertion_ids", [])
        ).issubset(set(teacher_reference.get("boundary_assertions", [])))
        calculated_unknown_complete = coverage_required and set(
            oracle_record.get("required_unknown_ids", [])
        ).issubset(set(teacher_reference.get("unknowns", [])))
        calculated_prohibited_count = len(
            set(oracle_record.get("prohibited_assertion_ids", []))
            & (
                set(teacher_reference.get("required_fact_ids", []))
                | set(teacher_reference.get("boundary_assertions", []))
                | set(teacher_reference.get("unknowns", []))
                | set(teacher_reference.get("answer_outline", []))
            )
        ) if coverage_required else 0
        calculated_coverage_passed = (
            calculated_fact_complete
            and calculated_boundary_complete
            and calculated_unknown_complete
            and calculated_prohibited_count == 0
        )
        if coverage_required and (
            teacher_coverage.get("shared_answer_binding_digest") != root_digest
            or teacher_coverage.get("question_id") != binding.get("question_id")
            or teacher_coverage.get("teacher_reference_digest")
            != teacher_reference.get("output_digest")
            or teacher_coverage.get("receipt_digest")
            != _digest_without_fields(teacher_coverage, "receipt_digest")
            or teacher_coverage.get("required_fact_ids_complete")
            is not calculated_fact_complete
            or teacher_coverage.get("required_boundary_assertions_complete")
            is not calculated_boundary_complete
            or teacher_coverage.get("required_unknowns_complete")
            is not calculated_unknown_complete
            or teacher_coverage.get("prohibited_assertion_count")
            != calculated_prohibited_count
            or coverage_passed is not calculated_coverage_passed
        ):
            _fail("teacher_oracle_coverage_invalid", "TeacherReference未通过同题Oracle覆盖验证")
        if coverage_required and coverage_must_pass is not coverage_passed:
            _fail("teacher_oracle_coverage_invalid", "Oracle覆盖终态与Teacher流程分支不一致")
        if isinstance(evidence_graph, Mapping):
            graph_fact_ids = {
                node.get("payload", {}).get("fact_id")
                for node in evidence_graph.get("nodes", [])
                if isinstance(node, Mapping)
                and node.get("committed") is True
                and node.get("node_type") in {"observed_fact", "derived_fact"}
            }
            graph_evidence_refs = {
                ref
                for node in evidence_graph.get("nodes", [])
                if isinstance(node, Mapping) and node.get("committed") is True
                for ref in node.get("evidence_refs", [])
            }
            if not set(teacher_reference.get("required_fact_ids", [])).issubset(
                graph_fact_ids
            ) or not set(teacher_reference.get("evidence_refs", [])).issubset(
                graph_evidence_refs
            ):
                _fail("teacher_reference_evidence_unclosed", "TeacherReference引用了EvidenceGraph外事实或Evidence")

    def validate_typed_answer_payload(answer_payload: Mapping[str, Any]) -> None:
        if not isinstance(evidence_graph, Mapping):
            _fail("student_claim_evidence_unclosed", "类型化Claim缺少已提交EvidenceGraph")
        oracle_records = (
            trusted_oracle_store.get("oracles")
            if isinstance(trusted_oracle_store, Mapping)
            else None
        )
        matching_oracles = [
            record
            for record in oracle_records.values()
            if isinstance(record, Mapping)
            and record.get("question_id") == binding.get("question_id")
            and record.get("oracle_digest")
            == _digest_without_fields(record, "oracle_digest")
        ] if isinstance(oracle_records, Mapping) else []
        if (
            not isinstance(trusted_oracle_store, Mapping)
            or trusted_oracle_store.get("store_contract_id")
            != "country_outage_p2_trusted_oracle_store_v1"
            or trusted_oracle_store.get("trust_origin")
            != "host_authenticated_runtime_store"
            or trusted_oracle_store.get("caller_mutable") is not False
            or trusted_oracle_store.get("attestation_provider_id")
            != "country_outage_p2_oracle_store_host"
            or trusted_oracle_store.get("attestation_contract_digest")
            != ORACLE_STORE_ATTESTATION_CONTRACT_DIGEST
            or len(matching_oracles) != 1
        ):
            _fail(
                "student_claim_boundary_binding_mismatch",
                "Student类型化Claim必须解析唯一同题Host受信Oracle边界策略",
            )
        answer_oracle_record = matching_oracles[0]
        nodes = [
            node
            for node in evidence_graph.get("nodes", [])
            if isinstance(node, Mapping) and node.get("committed") is True
        ]
        nodes_by_id = {node.get("node_id"): node for node in nodes}
        facts_by_id = {
            node.get("payload", {}).get("fact_id"): node
            for node in nodes
            if node.get("node_type") in {"observed_fact", "derived_fact"}
        }
        claims = answer_payload.get("claims")
        if (
            not isinstance(claims, list)
            or len({claim.get("claim_id") for claim in claims if isinstance(claim, Mapping)})
            != len(claims)
        ):
            _fail("student_claim_schema_invalid", "Student类型化Claim ID必须唯一")
        expected_global_evidence: list[str] = []
        limitation_texts: list[str] = []
        unknown_texts: list[str] = []
        asserted_boundary_ids: set[str] = set()
        allowed_boundary_ids = set(
            answer_oracle_record.get("allowed_boundary_assertion_ids", [])
        )
        required_boundary_ids = set(
            answer_oracle_record.get("required_boundary_assertion_ids", [])
        )
        prohibited_assertion_ids = set(
            answer_oracle_record.get("prohibited_assertion_ids", [])
        )
        for claim in claims:
            if not isinstance(claim, Mapping):
                _fail("student_claim_schema_invalid", "Student Claim不是闭合对象")
            kind = claim.get("claim_kind")
            if kind in {"observed_fact", "derived_fact"}:
                fact_ids = claim.get("fact_ids", [])
                source_nodes = [facts_by_id.get(fact_id) for fact_id in fact_ids]
                if (
                    not fact_ids
                    or any(node is None for node in source_nodes)
                    or any(node.get("node_type") != kind for node in source_nodes)
                    or claim.get("source_node_ids")
                    != [node.get("node_id") for node in source_nodes]
                ):
                    _fail(
                        "student_claim_fact_binding_mismatch",
                        "observed/derived Claim未逐fact绑定同类型已提交图节点",
                    )
                expected_value_digests = [
                    (
                        node.get("payload", {}).get("fact_value_digest")
                        if kind == "observed_fact"
                        else node.get("payload", {}).get("operator_output_digest")
                    )
                    for node in source_nodes
                ]
                expected_claim_evidence: list[str] = []
                for node in source_nodes:
                    for evidence_ref in node.get("evidence_refs", []):
                        if evidence_ref not in expected_claim_evidence:
                            expected_claim_evidence.append(evidence_ref)
                        if evidence_ref not in expected_global_evidence:
                            expected_global_evidence.append(evidence_ref)
                if (
                    claim.get("source_value_digests") != expected_value_digests
                    or claim.get("evidence_refs") != expected_claim_evidence
                    or claim.get("claim_relation")
                    != (
                        "states_observed_fact"
                        if kind == "observed_fact"
                        else "states_derived_fact"
                    )
                    or claim.get("verification_requirements") != []
                ):
                    _fail(
                        "student_claim_fact_binding_mismatch",
                        "事实Claim的值摘要、Evidence或关系未与图节点逐项一致",
                    )
            elif kind in {"knowledge_explanation", "testable_hypothesis"}:
                if (
                    any(
                        claim.get(field)
                        for field in (
                            "fact_ids",
                            "source_node_ids",
                            "source_value_digests",
                            "evidence_refs",
                        )
                    )
                    or not claim.get("verification_requirements")
                ):
                    _fail(
                        "student_claim_knowledge_boundary_mismatch",
                        "知识解释或假设不得冒充事件事实Evidence且必须列验证需求",
                    )
            elif kind in {"limitation", "unknown"}:
                source_node_ids = claim.get("source_node_ids", [])
                source_nodes = [nodes_by_id.get(node_id) for node_id in source_node_ids]
                if (
                    any(node is None or node.get("node_type") != kind for node in source_nodes)
                    or claim.get("fact_ids")
                    or claim.get("evidence_refs")
                    or claim.get("source_value_digests")
                    != [node.get("payload_digest") for node in source_nodes]
                    or not claim.get("verification_requirements")
                ):
                    _fail(
                        "student_claim_boundary_binding_mismatch",
                        "limitation/unknown Claim必须保持非事实类型并绑定可选同类型节点",
                    )
                (limitation_texts if kind == "limitation" else unknown_texts).append(
                    claim.get("text")
                )
            else:
                _fail("student_claim_schema_invalid", "Student Claim类型未登记")
            claim_boundary_ids = set(claim.get("boundary_assertion_ids", []))
            if (
                not claim_boundary_ids
                or not claim_boundary_ids.issubset(allowed_boundary_ids)
                or claim_boundary_ids & prohibited_assertion_ids
            ):
                _fail(
                    "student_claim_boundary_binding_mismatch",
                    "每条Claim的边界断言必须来自同题Host受信Oracle且不得命中禁止断言",
                )
            asserted_boundary_ids.update(claim_boundary_ids)
        if not required_boundary_ids.issubset(asserted_boundary_ids):
            _fail(
                "student_claim_boundary_binding_mismatch",
                "Student类型化Claim未覆盖同题Oracle要求的全部边界断言",
            )
        if (
            answer_payload.get("evidence_refs") != expected_global_evidence
            or answer_payload.get("limitations") != limitation_texts
            or answer_payload.get("unknowns") != unknown_texts
        ):
            _fail(
                "student_claim_global_projection_mismatch",
                "回答级Evidence、limitations与unknowns必须从类型化Claim精确投影",
            )

    def validate_student_chain(
        *, aligned: bool, last_require_pass: bool
    ) -> Mapping[str, Any]:
        if not students:
            _fail("student_run_missing", "发布缺少Student运行")
        for index, run in enumerate(students):
            receipt = run.get("run_receipt", {})
            validation = run.get("validation_receipt")
            expected_phase = "ds_first_answer" if index == 0 else "ds_revision"
            if receipt.get("role") != "student" or receipt.get(
                "disposition"
            ) != "completed" or receipt.get("exact_model_identity") != payload.get(
                "student_model_identity"
            ) or receipt.get("run_phase") != expected_phase or run.get(
                "revision_ordinal"
            ) != index:
                _fail("student_run_invalid", "Student角色、身份或状态错误")
            if receipt.get("output_digest") != run.get("student_answer_digest"):
                _fail("student_output_binding_mismatch", "Student run output未绑定StudentAnswer")
            answer_artifact = run.get("student_answer_artifact")
            if (
                not isinstance(answer_artifact, Mapping)
                or answer_artifact.get("answer_digest")
                != run.get("student_answer_digest")
                or not isinstance(answer_artifact.get("artifact_ref"), str)
                or answer_artifact.get("artifact_ref")
                != f"artifact:student-answer:{run.get('student_answer_digest')}"
                or not isinstance(answer_artifact.get("artifact_receipt_digest"), str)
                or answer_artifact.get("answer_digest")
                != _digest_without_fields(answer_artifact.get("answer_payload", {}))
                or answer_artifact.get("artifact_receipt_digest")
                != _digest_without_fields(answer_artifact, "artifact_receipt_digest")
            ):
                _fail("student_answer_artifact_unresolved", "Student回答摘要没有绑定可重放Artifact引用")
            resolve_trusted_student_artifact(answer_artifact)
            validate_typed_answer_payload(answer_artifact.get("answer_payload", {}))
            validate_gates(
                validation,
                require_pass=(last_require_pass if index == len(students) - 1 else False),
            )
            if validation.get("subject_digest") != run.get("student_answer_digest"):
                _fail("student_validation_subject_mismatch", "Student验证subject未绑定StudentAnswer")
            if receipt.get("validation_receipt_digest") != validation.get("receipt_digest"):
                _fail("student_validation_receipt_binding_mismatch", "Student run未绑定自己的验证回执")
            expected_input = _digest_without_fields(
                {
                    "role": "student",
                    "run_phase": expected_phase,
                    "revision_ordinal": run.get("revision_ordinal"),
                    "shared_answer_binding_digest": root_digest,
                    "teacher_reference_digest": run.get("teacher_reference_digest"),
                    "teacher_validation_receipt_digest": run.get(
                        "teacher_validation_receipt_digest"
                    ),
                    "teacher_oracle_coverage_receipt_digest": run.get(
                        "teacher_oracle_coverage_receipt_digest"
                    ),
                    "structured_feedback_digest": (
                        payload.get("structured_feedback", {}).get("feedback_digest")
                        if index == 1
                        else None
                    ),
                }
            )
            if receipt.get("role_specific_input_digest") != expected_input:
                _fail("student_input_digest_mismatch", "Student role_specific_input_digest 无法重算")
            if aligned and (
                run.get("teacher_reference_digest") != teacher_reference.get("output_digest")
                or run.get("teacher_validation_receipt_digest")
                != teacher_validation.get("receipt_digest")
                or run.get("teacher_oracle_coverage_receipt_digest")
                != teacher_coverage.get("receipt_digest")
            ):
                _fail("student_teacher_input_binding_mismatch", "Student未绑定已验证TeacherReference与Oracle覆盖回执")
        selected = students[-1]
        if student_validation != selected.get("validation_receipt"):
            _fail("student_validation_root_mismatch", "根Student验证回执必须等于最后一次Student验证")
        if len(students) == 2:
            feedback = payload.get("structured_feedback")
            first = students[0]
            if (
                not isinstance(feedback, Mapping)
                or feedback.get("producer_kind")
                != "host_deterministic_alignment_evaluator"
                or feedback.get("source_student_answer_digest") != first.get("student_answer_digest")
                or feedback.get("source_validation_receipt_digest")
                != first.get("validation_receipt", {}).get("receipt_digest")
                or feedback.get("feedback_digest")
                != _digest_without_fields(feedback, "feedback_digest")
            ):
                _fail("student_feedback_binding_mismatch", "结构化反馈未绑定首答及其失败验证")
        return selected

    def validate_publish(selected: Mapping[str, Any], *, aligned_claim: bool) -> None:
        selected_answer_digest = selected.get("student_answer_digest")
        selected_payload = selected.get("student_answer_artifact", {}).get(
            "answer_payload", {}
        )
        if not isinstance(published, Mapping) or published.get(
            "aligned_claim"
        ) is not aligned_claim:
            _fail("aligned_claim_invalid", "发布回答的alignment声明与流程不一致")
        if published.get("answer_digest") != _digest_without_fields(published, "answer_digest"):
            _fail("published_answer_digest_mismatch", "发布回答摘要无法重算")
        if not isinstance(evidence_graph, Mapping) or evidence_graph.get(
            "graph_state"
        ) != "committed":
            _fail("published_answer_evidence_unclosed", "发布回答缺少已提交EvidenceGraph")
        if (
            binding.get("evidence_graph_revision") != evidence_graph.get("graph_revision")
            or binding.get("evidence_graph_digest") != evidence_graph.get("graph_digest")
        ):
            _fail("published_answer_evidence_unclosed", "共享绑定没有指向同一EvidenceGraph")
        allowed_refs = {
            ref
            for node in evidence_graph.get("nodes", [])
            if isinstance(node, Mapping) and node.get("committed") is True
            for ref in node.get("evidence_refs", [])
        }
        if not set(published.get("evidence_refs", [])).issubset(allowed_refs):
            _fail("published_answer_evidence_unclosed", "发布回答引用了图外Evidence")
        if (
            published.get("claims") != selected_payload.get("claims")
            or published.get("claims_digest")
            != _digest_without_fields({"claims": selected_payload.get("claims")})
            or published.get("evidence_refs") != selected_payload.get("evidence_refs")
            or published.get("limitations") != selected_payload.get("limitations")
            or published.get("unknowns") != selected_payload.get("unknowns")
        ):
            _fail(
                "published_claim_projection_mismatch",
                "发布Claim及其Evidence/边界字段必须精确来自最终Student类型化制品",
            )
        if (
            not isinstance(publish_receipt, Mapping)
            or publish_receipt.get("receipt_digest") != payload.get("publish_receipt_digest")
            or _digest_without_fields(publish_receipt, "receipt_digest")
            != payload.get("publish_receipt_digest")
            or publish_receipt.get("answer_digest") != published.get("answer_digest")
            or publish_receipt.get("student_answer_digest") != selected_answer_digest
            or publish_receipt.get("shared_answer_binding_digest") != root_digest
            or publish_receipt.get("disposition") != "committed"
        ):
            _fail("publish_receipt_binding_mismatch", "发布回执未绑定最终回答、Student输出与共享上下文")

    def validate_alignment_result(
        selected_answer_digest: str, *, require_pass: bool
    ) -> None:
        metrics = (
            alignment.get("hard_gate_metrics", {})
            if isinstance(alignment, Mapping)
            else {}
        )
        expected_pass = all(
            metrics.get(metric) == 1
            for metric in (
                "fact_precision",
                "evidence_ref_precision",
                "boundary_compliance",
            )
        )
        expected_metric_inputs_digest = _digest_without_fields(
            {
                "question_id": binding.get("question_id"),
                "oracle_digest": teacher_coverage.get("oracle_digest"),
                "evidence_graph_digest": binding.get("evidence_graph_digest"),
                "teacher_reference_digest": teacher_reference.get("output_digest"),
                "teacher_oracle_coverage_receipt_digest": teacher_coverage.get(
                    "receipt_digest"
                ),
                "student_answer_digest": selected_answer_digest,
            }
        )
        if (
            not isinstance(alignment, Mapping)
            or alignment.get("hard_gates_passed") is not expected_pass
            or alignment.get("hard_gates_passed") is not require_pass
            or alignment.get("disposition")
            != ("passed" if require_pass else "rejected")
            or alignment.get("teacher_reference_digest")
            != teacher_reference.get("output_digest")
            or alignment.get("student_answer_digest") != selected_answer_digest
            or alignment.get("oracle_digest") != teacher_coverage.get("oracle_digest")
            or alignment.get("evidence_graph_digest")
            != binding.get("evidence_graph_digest")
            or alignment.get("teacher_oracle_coverage_receipt_digest")
            != teacher_coverage.get("receipt_digest")
            or alignment.get("metric_inputs_digest")
            != expected_metric_inputs_digest
            or alignment.get("receipt_digest")
            != _digest_without_fields(alignment, "receipt_digest")
        ):
            _fail("alignment_digest_binding_mismatch", "Alignment未绑定可信输入、冻结指标与最终Student输出")
        validate_alignment_receipt_trust()

    disposition = payload.get("final_disposition")
    exact_state = {
        "aligned_published": "published",
        "ds_unaligned_degraded": "degraded_published",
        "teacher_unavailable": "stopped_waiting_teacher",
        "teacher_rejected": "teacher_rejected",
        "student_rejected": "failed",
        "alignment_rejected": "alignment_failed",
    }
    if disposition in exact_state and payload.get("flow_state") != exact_state[disposition]:
        _fail("dual_model_disposition_state_invalid", "DualModel终态disposition与flow_state不一致")
    unavailable_phase = payload.get("teacher_unavailable_phase")
    if disposition != "teacher_unavailable" and unavailable_phase != "none":
        _fail("dual_model_disposition_state_invalid", "非Teacher不可用终态不得携带不可用阶段")
    if disposition == "none" and payload.get("flow_state") not in {
        "awaiting_teacher",
        "teacher_running",
        "teacher_validated",
        "student_running",
        "degraded_authorized",
    }:
        _fail("dual_model_disposition_state_invalid", "none只能用于未发布的非终态flow_state")
    if disposition == "aligned_published":
        if teacher_run.get("disposition") != "completed":
            _fail("teacher_run_invalid", "aligned发布的Teacher未完成")
        validate_teacher_chain(require_pass=True)
        selected = validate_student_chain(aligned=True, last_require_pass=True)
        validate_alignment_result(
            selected.get("student_answer_digest"), require_pass=True
        )
        validate_publish(selected, aligned_claim=True)
    elif disposition == "teacher_rejected":
        if students:
            _fail("teacher_rejected_forwarded_to_student", "Teacher拒绝后不得启动Student")
        if isinstance(teacher_validation, Mapping) and teacher_validation.get(
            "all_gates_passed"
        ) is True:
            validate_teacher_chain(
                require_pass=True,
                coverage_required=True,
                coverage_must_pass=False,
            )
        else:
            validate_teacher_chain(
                require_pass=False,
                coverage_required=False,
            )
        if any(
            payload.get(field) is not None
            for field in (
                "structured_feedback",
                "student_validation_receipt",
                "alignment_run_receipt",
                "published_answer",
                "publish_receipt_digest",
                "degraded_authorization",
            )
        ):
            _fail("teacher_rejection_state_open", "Teacher拒绝后不得留下Student、Alignment或发布制品")
    elif disposition == "ds_unaligned_degraded":
        authorization = payload.get("degraded_authorization")
        if any(
            payload.get(field) is not None
            for field in (
                "teacher_plan_run_receipt",
                "teacher_plan_grounding_receipt",
                "teacher_run_receipt",
                "teacher_reference",
                "teacher_validation_receipt",
                "teacher_oracle_coverage_receipt",
            )
        ):
            _fail("degraded_teacher_artifact_forbidden", "降级发布不得保留Teacher运行或引用制品")
        if not isinstance(authorization, dict) or authorization.get("user_confirmed") is not True:
            _fail("silent_degrade_forbidden", "降级缺少用户明确授权")
        if authorization.get("authorization_digest") != _digest_without_fields(
            authorization, "authorization_digest"
        ):
            _fail("degraded_authorization_digest_mismatch", "降级授权摘要无法从完整授权重算")
        parent = authorization.get("parent_plan_revision")
        new = authorization.get("new_plan_revision")
        if not isinstance(parent, int) or not isinstance(new, int) or new <= parent or new != binding.get(
            "plan_revision"
        ):
            _fail("degraded_plan_revision_invalid", "降级未创建并绑定新计划修订")
        if not isinstance(published, dict) or published.get("aligned_claim") is not False:
            _fail("aligned_claim_invalid", "降级回答不得声称完成Sol→DS对齐")
        if any(
            run.get("teacher_reference_digest") is not None
            or run.get("teacher_validation_receipt_digest") is not None
            or run.get("teacher_oracle_coverage_receipt_digest") is not None
            for run in students
        ):
            _fail("degraded_teacher_input_invalid", "降级Student不得携带无效Teacher输入")
        selected = validate_student_chain(aligned=False, last_require_pass=True)
        if not isinstance(trusted_validated_plan_store, Mapping) or not isinstance(
            investigation_plan_schema, Mapping
        ):
            _fail("validated_plan_unresolved", "降级发布缺少Host受信完整计划与冻结Schema")
        degraded_plan = _resolve_trusted_validated_plan(
            trusted_validated_plan_store,
            plan_id=binding.get("plan_id"),
            plan_revision=new,
            plan_schema=investigation_plan_schema,
        )
        degraded_definition = degraded_plan.get("plan_definition", {})
        degraded_policy = degraded_definition.get("answer_execution_policy", {})
        degraded_plan_digest = _digest_without_fields(
            {"plan_definition": degraded_definition}
        )
        if (
            degraded_policy.get("teacher_required") is not False
            or degraded_policy.get("mode") != "ds_unaligned_explicitly_authorized"
            or degraded_policy.get("authorization_digest")
            != authorization.get("authorization_digest")
            or degraded_definition.get("parent_plan_revision") != parent
            or degraded_plan_digest != binding.get("investigation_plan_digest")
            or not isinstance(evidence_graph, Mapping)
            or degraded_definition.get("plan_id") != evidence_graph.get("plan_id")
            or degraded_definition.get("plan_revision") != evidence_graph.get("plan_revision")
            or degraded_plan_digest != evidence_graph.get("plan_digest")
            or binding.get("evidence_graph_revision") != evidence_graph.get("graph_revision")
            or binding.get("evidence_graph_digest") != evidence_graph.get("graph_digest")
        ):
            _fail("degraded_plan_admission_invalid", "降级新计划、授权与已验证提交图未闭合到同一修订")
        validate_publish(selected, aligned_claim=False)
    elif disposition == "student_rejected":
        validate_teacher_chain(require_pass=True)
        validate_student_chain(aligned=True, last_require_pass=False)
        if any(
            payload.get(field) is not None
            for field in ("alignment_run_receipt", "published_answer", "publish_receipt_digest")
        ):
            _fail("dual_model_disposition_state_invalid", "Student拒绝分支不得进入Alignment或发布")
    elif disposition == "alignment_rejected":
        validate_teacher_chain(require_pass=True)
        selected = validate_student_chain(aligned=True, last_require_pass=True)
        if (
            published is not None
            or payload.get("publish_receipt_digest") is not None
        ):
            _fail("dual_model_disposition_state_invalid", "Alignment拒绝分支状态未闭合")
        validate_alignment_result(
            selected.get("student_answer_digest"), require_pass=False
        )
    elif disposition == "teacher_unavailable":
        common_invalid = students or any(
            payload.get(field) is not None
            for field in (
                "teacher_reference",
                "teacher_validation_receipt",
                "teacher_oracle_coverage_receipt",
                "structured_feedback",
                "student_validation_receipt",
                "alignment_run_receipt",
                "degraded_authorization",
                "published_answer",
                "publish_receipt_digest",
            )
        )
        if common_invalid or unavailable_phase != "sol_reference":
            _fail("dual_model_disposition_state_invalid", "Teacher不可用默认必须停止且不得发布")
        else:
            validate_teacher_planning_chain()
            expected_reference_input = _digest_without_fields(
                {
                    "role": "teacher",
                    "run_phase": "sol_reference",
                    "shared_answer_binding_digest": root_digest,
                }
            )
            if (
                not isinstance(teacher_run, Mapping)
                or teacher_run.get("role") != "teacher"
                or teacher_run.get("run_phase") != "sol_reference"
                or teacher_run.get("disposition") != "unavailable"
                or teacher_run.get("validation_receipt_digest") is not None
                or teacher_run.get("output_digest") is not None
                or teacher_run.get("role_specific_input_digest")
                != expected_reference_input
                or teacher_run.get("exact_model_identity")
                != payload.get("teacher_model_identity")
            ):
                _fail("dual_model_disposition_state_invalid", "Sol reference不可用终态制品不闭合")
    elif disposition == "none":
        flow_state = payload.get("flow_state")
        if payload.get("effective_teacher_required") is True:
            validate_teacher_planning_chain()
        if (
            published is not None
            or payload.get("publish_receipt_digest") is not None
            or payload.get("alignment_run_receipt") is not None
            or payload.get("student_validation_receipt") is not None
            or students
        ):
            _fail("dual_model_disposition_state_invalid", "非终态流程不得携带发布回答")
        if flow_state in {"awaiting_teacher", "teacher_running"} and any(
            payload.get(field) is not None
            for field in (
                "teacher_run_receipt",
                "teacher_reference",
                "teacher_validation_receipt",
                "degraded_authorization",
            )
        ):
            _fail("dual_model_disposition_state_invalid", "Teacher未完成状态不得夹带已完成Teacher制品")
        if flow_state in {"teacher_validated", "student_running"}:
            validate_teacher_chain(require_pass=True)
            if payload.get("degraded_authorization") is not None:
                _fail("dual_model_disposition_state_invalid", "Teacher必需分支不得夹带降级授权")
        if flow_state == "degraded_authorized":
            authorization = payload.get("degraded_authorization")
            if (
                payload.get("effective_teacher_required") is not False
                or any(
                    payload.get(field) is not None
                    for field in (
                        "teacher_run_receipt",
                        "teacher_reference",
                        "teacher_validation_receipt",
                    )
                )
                or not isinstance(authorization, Mapping)
                or authorization.get("user_confirmed") is not True
            ):
                _fail("dual_model_disposition_state_invalid", "degraded_authorized状态制品不闭合")
    else:
        _fail("dual_model_disposition_state_invalid", f"未闭合的DualModel disposition：{disposition}")


def _trusted_runtime_store_items(
    store: Mapping[str, Any],
    *,
    expected_contract_id: str,
    items_field: str,
) -> Mapping[str, Any]:
    """解析 Host-only runtime store；事务请求不得直接夹带这些对象。"""

    if set(store) != {
        "store_contract_id",
        "trust_origin",
        "caller_mutable",
        items_field,
    }:
        _fail("transaction_trusted_store_invalid", f"{expected_contract_id}信封不闭合")
    if (
        store.get("store_contract_id") != expected_contract_id
        or store.get("trust_origin") != "host_authenticated_runtime_store"
        or store.get("caller_mutable") is not False
        or not isinstance(store.get(items_field), Mapping)
    ):
        _fail("transaction_trusted_store_invalid", f"{expected_contract_id}不是Host受信依赖")
    return store[items_field]


def validate_transaction_record_instance(
    payload: Mapping[str, Any],
    *,
    schema: Mapping[str, Any],
    consistency_contract: Mapping[str, Any],
    current_pointer: Mapping[str, Any] | None,
    trusted_transaction_request_store: Mapping[str, Any],
    trusted_prepared_artifact_store: Mapping[str, Any],
    trusted_gate_receipt_store: Mapping[str, Any],
    commit_receipt: Mapping[str, Any] | None,
    recovery_receipt: Mapping[str, Any] | None,
    existing_transaction: Mapping[str, Any] | None = None,
) -> None:
    """验证五类提交共通信封的状态、父修订、幂等键与可见性条件。"""

    _validate_draft202012_instance(
        payload,
        schema,
        expected_schema_id=(
            "https://domeye.example/contracts/agent/country-outage-p2-s1/"
            "transaction-record.schema.json"
        ),
        subject="TransactionRecord",
    )
    recomputed_contract_digest = _digest_without_fields(
        consistency_contract, "contract_content_digest"
    )
    if (
        consistency_contract.get("contract_content_digest")
        != RUNTIME_CONSISTENCY_CONTRACT_CANONICAL_DIGEST
        or recomputed_contract_digest
        != RUNTIME_CONSISTENCY_CONTRACT_CANONICAL_DIGEST
    ):
        _fail(
            "transaction_gate_registry_attestation_invalid",
            "事务必须解析Hook封存的内容寻址运行时一致性合同",
        )

    boundaries = {
        boundary.get("id"): boundary
        for boundary in consistency_contract.get("boundaries", [])
        if isinstance(boundary, Mapping)
    }
    kind = payload.get("consistency_kind")
    boundary = boundaries.get(kind)
    if not isinstance(boundary, Mapping):
        _fail("transaction_consistency_kind_contract_invalid", "事务类型没有冻结边界合同")
    requests = _trusted_runtime_store_items(
        trusted_transaction_request_store,
        expected_contract_id="country_outage_p2_trusted_transaction_request_store_v1",
        items_field="requests",
    )
    transaction_request = requests.get(payload.get("request_digest"))
    if not isinstance(transaction_request, Mapping):
        _fail("transaction_request_unresolved", "事务请求未从Host受信store解析")
    _validate_draft202012_instance(
        transaction_request,
        consistency_contract.get("transaction_request_schema", {}),
        expected_schema_id=(
            "https://domeye.example/contracts/agent/country-outage-p2-s1/"
            "transaction-request.schema.json"
        ),
        subject="TransactionRequest",
    )
    idempotency_components = transaction_request.get("components")
    expected_recipe = boundary.get("idempotency_key_recipe", [])
    if (
        not isinstance(idempotency_components, Mapping)
        or
        set(idempotency_components) != set(expected_recipe)
        or transaction_request.get("payload") != dict(idempotency_components)
        or transaction_request.get("consistency_kind") != kind
    ):
        _fail("transaction_idempotency_components_invalid", "幂等键组件与边界recipe不一致")
    for field, value in idempotency_components.items():
        if field.endswith("_revision") or field == "binding_generation":
            valid = isinstance(value, int) and not isinstance(value, bool) and value >= 1
        elif field.endswith("_digest"):
            valid = bool(re.fullmatch(r"sha256:[0-9a-f]{64}", str(value)))
        elif field == "format":
            valid = value in {"json", "csv", "markdown"}
        else:
            valid = isinstance(value, str) and bool(value)
        if not valid:
            _fail(
                "transaction_idempotency_components_invalid",
                f"幂等组件{field}不满足冻结类型",
            )
    expected_request_digest = "sha256:" + _digest_without_fields(
        {"request": transaction_request}
    )
    if payload.get("request_digest") != expected_request_digest:
        _fail("transaction_idempotency_request_digest_mismatch", "request_digest未绑定规范化事务请求")
    binding_digest = transaction_request.get("binding_digest")
    scope_digest = "sha256:" + _digest_without_fields(
        {
            "consistency_kind": kind,
            "binding_digest": binding_digest,
            "components": dict(idempotency_components),
        }
    )
    expected_key = f"{kind}:" + _digest_without_fields(
        {"recipe": expected_recipe, "components": dict(idempotency_components)}
    )
    if payload.get("idempotency_key") != expected_key:
        _fail("transaction_idempotency_key_mismatch", "幂等键无法从冻结recipe重算")

    prepared_artifacts = _trusted_runtime_store_items(
        trusted_prepared_artifact_store,
        expected_contract_id="country_outage_p2_trusted_prepared_artifact_store_v1",
        items_field="artifacts",
    )
    validation_receipt_map = _trusted_runtime_store_items(
        trusted_gate_receipt_store,
        expected_contract_id="country_outage_p2_trusted_gate_receipt_store_v1",
        items_field="receipts",
    )
    if len(payload.get("prepared_artifact_refs", [])) != len(
        payload.get("prepared_artifact_digests", [])
    ):
        _fail("transaction_prepare_set_mismatch", "prepared refs与digests数量不一致")
    refs = payload.get("prepared_artifact_refs", [])
    digests = payload.get("prepared_artifact_digests", [])
    if refs != boundary.get("atomic_write_set") or set(prepared_artifacts) != set(refs):
        _fail("transaction_consistency_kind_contract_invalid", "prepared artifacts未精确覆盖边界原子写集合")
    for ref, digest in zip(refs, digests):
        artifact = prepared_artifacts[ref]
        _validate_draft202012_instance(
            artifact,
            consistency_contract.get("prepared_artifact_envelope_schema", {}),
            expected_schema_id=(
                "https://domeye.example/contracts/agent/country-outage-p2-s1/"
                "prepared-artifact-envelope.schema.json"
            ),
            subject=f"PreparedArtifact:{ref}",
        )
        role_contracts = consistency_contract.get(
            "prepared_artifact_role_contracts", {}
        ).get("by_consistency_kind", {})
        role_contract = role_contracts.get(kind, {}).get(ref)
        if not isinstance(role_contract, Mapping):
            _fail("transaction_artifact_role_contract_unresolved", f"{kind}/{ref}未登记role contract")
        expected_schema_ref = role_contract.get("artifact_schema_ref")
        payload_schema = role_contract.get("payload_schema")
        if (
            not isinstance(payload_schema, Mapping)
            or payload_schema.get("$id") != expected_schema_ref
        ):
            _fail("transaction_artifact_role_contract_unresolved", f"{kind}/{ref} role Schema未闭合")
        _validate_draft202012_instance(
            artifact.get("payload"),
            payload_schema,
            expected_schema_id=str(expected_schema_ref),
            subject=f"PreparedArtifactPayload:{ref}",
        )
        prepare_receipt = artifact.get("prepare_receipt", {})
        if (
            artifact.get("artifact_role") != ref
            or artifact.get("artifact_ref") != ref
            or artifact.get("artifact_schema_ref") != expected_schema_ref
            or artifact.get("binding_digest") != binding_digest
            or artifact.get("request_digest") != payload.get("request_digest")
            or artifact.get("scope_digest") != scope_digest
            or artifact.get("payload_digest")
            != "sha256:" + _digest_without_fields({"payload": artifact.get("payload")})
            or prepare_receipt.get("artifact_ref") != ref
            or prepare_receipt.get("payload_digest") != artifact.get("payload_digest")
            or prepare_receipt.get("request_digest") != payload.get("request_digest")
            or prepare_receipt.get("scope_digest") != scope_digest
            or prepare_receipt.get("receipt_digest")
            != "sha256:" + _digest_without_fields(prepare_receipt, "receipt_digest")
        ):
            _fail("transaction_artifact_binding_mismatch", f"{ref} typed artifact未绑定事务请求与准备回执")
        expected = "sha256:" + _digest_without_fields({"artifact": prepared_artifacts[ref]})
        if digest != expected:
            _fail("transaction_prepared_artifact_digest_mismatch", f"{ref} 摘要无法重算")
    validation_receipts = payload.get("validation_receipts", [])
    expected_gates = boundary.get("validation_gates", [])
    if [item.get("gate_id") for item in validation_receipts] != expected_gates or set(
        validation_receipt_map
    ) != set(expected_gates):
        _fail("transaction_consistency_kind_contract_invalid", "validation receipts未精确覆盖边界Gate")
    expected_subject_bindings = [
        {
            "artifact_role": ref,
            "artifact_ref": ref,
            "artifact_digest": digest,
        }
        for ref, digest in zip(refs, digests)
    ]
    gate_registry = consistency_contract.get("trusted_gate_validator_registry")
    expected_gate_population = {
        (boundary_id, gate_id)
        for boundary_id, boundary_contract in boundaries.items()
        for gate_id in boundary_contract.get("validation_gates", [])
    }
    gate_entries = gate_registry.get("entries", []) if isinstance(gate_registry, Mapping) else []
    gate_entries_by_key = {
        (entry.get("consistency_kind"), entry.get("gate_id")): entry
        for entry in gate_entries
        if isinstance(entry, Mapping)
    }
    if not isinstance(gate_registry, Mapping) or any(
        (
            gate_registry.get("source") != "host_trusted_static_contract",
            gate_registry.get("caller_supplied_validator_identity_forbidden") is not True,
            gate_registry.get("closed_gate_population_from_each_boundary_validation_gates")
            is not True,
            gate_registry.get("expected_gate_entry_count") != 25,
            gate_registry.get("subject_contract")
            != "all_prepared_artifact_role_ref_digest_bindings_in_boundary_order",
            len(gate_entries) != 25,
            set(gate_entries_by_key) != expected_gate_population,
            gate_registry.get("registry_content_digest")
            != _digest_without_fields(gate_registry, "registry_content_digest"),
        )
    ):
        _fail("transaction_gate_registry_invalid", "Host Gate Registry合同未闭合")
    for (entry_kind, entry_gate_id), entry in gate_entries_by_key.items():
        output_schema = entry.get("output_schema")
        if (
            set(entry)
            != {
                "consistency_kind",
                "gate_id",
                "gate_version",
                "validator_id",
                "gate_contract_digest",
                "implementation_digest",
                "implementation_artifact",
                "subject_contract",
                "output_schema_ref",
                "output_schema",
            }
            or entry.get("gate_version") != "1.0.0"
            or entry.get("validator_id")
            != f"HOST-GATE::{entry_kind}::{entry_gate_id}"
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(entry.get("gate_contract_digest")))
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(entry.get("implementation_digest")))
            or entry.get("subject_contract")
            != "all_prepared_artifact_role_ref_digest_bindings_in_boundary_order"
            or not isinstance(output_schema, Mapping)
            or output_schema.get("$id") != entry.get("output_schema_ref")
        ):
            _fail("transaction_gate_registry_invalid", f"{entry_kind}/{entry_gate_id} Gate条目未闭合")
        _validate_schema_file(output_schema, Path(f"trusted_gate_registry:{entry_kind}/{entry_gate_id}"))
        implementation_artifact = entry.get("implementation_artifact")
        expected_gate_contract_digest = "sha256:" + _digest_without_fields(
            {
                "consistency_kind": entry_kind,
                "gate_id": entry_gate_id,
                "gate_version": entry.get("gate_version"),
                "subject_contract": entry.get("subject_contract"),
                "output_schema": output_schema,
            }
        )
        if (
            not isinstance(implementation_artifact, Mapping)
            or entry.get("implementation_digest")
            != "sha256:" + _digest_without_fields(implementation_artifact)
            or entry.get("gate_contract_digest") != expected_gate_contract_digest
            or implementation_artifact.get("validator_id")
            != entry.get("validator_id")
            or implementation_artifact.get("consistency_kind") != entry_kind
            or implementation_artifact.get("gate_id") != entry_gate_id
            or implementation_artifact.get("output_schema_ref")
            != entry.get("output_schema_ref")
            or implementation_artifact.get("design_only") is not True
            or implementation_artifact.get("runtime_implemented") is not False
        ):
            _fail(
                "transaction_gate_registry_attestation_invalid",
                f"{entry_kind}/{entry_gate_id} Gate声明实现没有内容寻址闭包",
            )
    for receipt_ref in validation_receipts:
        gate_id = receipt_ref["gate_id"]
        receipt = validation_receipt_map[gate_id]
        expected_digest = "sha256:" + _digest_without_fields(receipt, "receipt_digest")
        gate_entry = gate_entries_by_key[(kind, gate_id)]
        expected_subject_set_digest = "sha256:" + _digest_without_fields(
            {"subject_bindings": expected_subject_bindings}
        )
        gate_output = receipt.get("gate_output")
        _validate_draft202012_instance(
            gate_output,
            gate_entry["output_schema"],
            expected_schema_id=gate_entry["output_schema_ref"],
            subject=f"GateOutput:{kind}/{gate_id}",
        )
        if (
            receipt_ref != receipt
            or
            receipt_ref.get("receipt_digest") != expected_digest
            or receipt.get("receipt_digest") != expected_digest
            or receipt.get("gate_version") != "1.0.0"
            or receipt.get("validator_id") != gate_entry.get("validator_id")
            or receipt.get("gate_contract_digest")
            != gate_entry.get("gate_contract_digest")
            or receipt.get("implementation_digest")
            != gate_entry.get("implementation_digest")
            or receipt.get("output_schema_ref") != gate_entry.get("output_schema_ref")
            or receipt.get("transaction_id") != payload.get("transaction_id")
            or receipt.get("request_digest") != payload.get("request_digest")
            or receipt.get("binding_digest") != binding_digest
            or receipt.get("subject_bindings") != expected_subject_bindings
            or gate_output
            != {
                "gate_id": gate_id,
                "subject_set_digest": expected_subject_set_digest,
                "passed": receipt.get("passed"),
                "failure_code": receipt.get("failure_code"),
            }
            or receipt.get("output_digest")
            != "sha256:" + _digest_without_fields({"gate_output": gate_output})
            or (receipt.get("passed") is True) != (receipt.get("failure_code") is None)
        ):
            _fail("transaction_validation_receipt_mismatch", f"{gate_id} Gate回执未闭合")
    if payload.get("commit_point") != boundary.get("commit_point"):
        _fail("transaction_consistency_kind_contract_invalid", "commit_point与事务边界不一致")
    if payload.get("recovery_action") not in boundary.get("allowed_recovery_actions", []):
        _fail("transaction_recovery_action_invalid", "recovery_action不属于该事务边界")

    parent_revision = payload.get("parent_revision")
    parent_digest = payload.get("parent_digest")
    expected_current = payload.get("expected_current_digest")
    committed_revision = payload.get("committed_revision")
    state = payload.get("transaction_state")
    is_compare_and_swap_conflict = (
        state == "aborted"
        and payload.get("disposition") == "rejected_conflict"
        and payload.get("conflict_kind") == "compare_and_swap"
    )
    if is_compare_and_swap_conflict:
        if parent_digest != expected_current:
            _fail("transaction_cas_conflict_record_invalid", "CAS冲突必须保留同一尝试父摘要与expected摘要")
        if current_pointer is None:
            if parent_revision is None or expected_current is None:
                _fail("transaction_cas_conflict_record_invalid", "空当前指针上的CAS冲突必须记录非空尝试指针")
        else:
            if expected_current == current_pointer.get("digest"):
                _fail("transaction_cas_conflict_not_observed", "expected digest等于live digest时不得伪报CAS冲突")
            if not isinstance(parent_revision, int) or parent_revision < 1:
                _fail("transaction_cas_attempt_identity_invalid", "CAS冲突必须记录有效的尝试父修订")
        expected_committed_revision = None
    elif current_pointer is None:
        if any(value is not None for value in (parent_revision, parent_digest, expected_current)):
            _fail("transaction_cas_binding_mismatch", "genesis事务父指针必须全为空")
        expected_committed_revision = 1
    else:
        if (
            parent_revision != current_pointer.get("revision")
            or parent_digest != current_pointer.get("digest")
            or expected_current != current_pointer.get("digest")
        ):
            _fail("transaction_cas_binding_mismatch", "事务父修订、父摘要或CAS摘要不等于当前指针")
        expected_committed_revision = parent_revision + 1

    if existing_transaction is not None:
        if existing_transaction.get("idempotency_key") != payload.get("idempotency_key"):
            _fail("transaction_existing_key_mismatch", "existing transaction不是同一幂等键")
        if existing_transaction.get("request_digest") != payload.get("request_digest"):
            if payload.get("transaction_state") != "aborted" or payload.get(
                "disposition"
            ) != "rejected_conflict" or payload.get("conflict_kind") != "idempotency":
                _fail("transaction_idempotency_conflict", "同幂等键不同请求必须rejected_conflict")
        elif existing_transaction.get("outcome_digest") != payload.get("outcome_digest"):
            _fail("transaction_idempotent_outcome_mismatch", "同幂等键同请求产生了不同结果")
    if state == "committed" and (
        not payload.get("commit_marker")
        or not payload.get("committed_digest")
        or payload.get("disposition") != "committed"
        or payload.get("recovery_action") != "none"
        or any(item.get("passed") is not True for item in validation_receipts)
    ):
        _fail("transaction_commit_state_invalid", "committed事务缺少提交标记、修订或摘要")
    if state == "committed":
        if committed_revision != expected_committed_revision:
            _fail("transaction_revision_chain_invalid", "committed revision必须为genesis 1或parent+1")
        if payload.get("commit_marker") != f"{payload.get('commit_point')}:{payload.get('transaction_id')}":
            _fail("transaction_commit_marker_mismatch", "commit_marker无法从提交点与事务ID重算")
        expected_committed_digest = "sha256:" + _digest_without_fields(
            {
                "prepared_artifact_refs": refs,
                "prepared_artifact_digests": digests,
                "validation_receipts": validation_receipts,
            }
        )
        if payload.get("committed_digest") != expected_committed_digest:
            _fail("transaction_committed_digest_mismatch", "committed_digest无法从已验证准备集重算")
        artifact_set_digest = "sha256:" + _digest_without_fields(
            {"prepared_artifact_refs": refs, "prepared_artifact_digests": digests}
        )
        gate_set_digest = "sha256:" + _digest_without_fields(
            {"validation_receipts": validation_receipts}
        )
        if (
            not isinstance(commit_receipt, Mapping)
            or set(commit_receipt)
            != {
                "receipt_kind",
                "transaction_id",
                "consistency_kind",
                "request_digest",
                "binding_digest",
                "scope_digest",
                "parent_revision",
                "expected_current_digest",
                "committed_revision",
                "artifact_set_digest",
                "gate_set_digest",
                "commit_point",
                "committed_digest",
                "disposition",
                "receipt_digest",
            }
            or commit_receipt.get("receipt_kind") != "transaction_commit"
            or commit_receipt.get("transaction_id") != payload.get("transaction_id")
            or commit_receipt.get("consistency_kind") != kind
            or commit_receipt.get("request_digest") != payload.get("request_digest")
            or commit_receipt.get("binding_digest") != binding_digest
            or commit_receipt.get("scope_digest") != scope_digest
            or commit_receipt.get("parent_revision") != parent_revision
            or commit_receipt.get("expected_current_digest") != expected_current
            or commit_receipt.get("committed_revision") != committed_revision
            or commit_receipt.get("artifact_set_digest") != artifact_set_digest
            or commit_receipt.get("gate_set_digest") != gate_set_digest
            or commit_receipt.get("commit_point") != payload.get("commit_point")
            or commit_receipt.get("committed_digest")
            != payload.get("committed_digest")
            or commit_receipt.get("disposition") != "committed"
            or commit_receipt.get("receipt_digest")
            != "sha256:" + _digest_without_fields(commit_receipt, "receipt_digest")
            or payload.get("commit_receipt_digest")
            != commit_receipt.get("receipt_digest")
        ):
            _fail("transaction_commit_receipt_mismatch", "commit receipt未绑定事务请求、准备集、Gate与CAS")
    if state in {"prepared", "aborted"} and any(
        payload.get(field) is not None
        for field in ("commit_marker", "committed_revision", "committed_digest")
    ):
        _fail("transaction_half_commit_visible", "未提交事务暴露了提交字段")
    if state == "prepared" and payload.get("disposition") != "prepared":
        _fail("transaction_state_disposition_mismatch", "prepared事务disposition错误")
    if state == "aborted" and payload.get("disposition") not in {
        "aborted",
        "rejected_conflict",
        "recovered",
    }:
        _fail("transaction_state_disposition_mismatch", "aborted事务disposition错误")
    if state == "prepared" and (
        payload.get("recovery_action") not in {"none", "resume_prepare"}
        or payload.get("recovery_receipt_digest") is not None
        or commit_receipt is not None
        or recovery_receipt is not None
    ):
        _fail("transaction_state_recovery_matrix_invalid", "prepared事务恢复状态不闭合")
    if state == "aborted":
        disposition = payload.get("disposition")
        action = payload.get("recovery_action")
        allowed_actions = {
            "aborted": {"discard_prepare"},
            "rejected_conflict": {"retry_compare_and_swap"},
            "recovered": {
                "discard_prepare",
                "retry_compare_and_swap",
                "preserve_current_pointer",
                "preserve_previous_final",
            },
        }
        if action not in allowed_actions.get(disposition, set()):
            _fail("transaction_state_recovery_matrix_invalid", "aborted事务disposition与recovery_action不一致")
        if disposition == "rejected_conflict" and payload.get("conflict_kind") not in {
            "idempotency",
            "compare_and_swap",
        }:
            _fail("transaction_state_recovery_matrix_invalid", "冲突事务缺少conflict_kind")
        if disposition != "rejected_conflict" and payload.get("conflict_kind") is not None:
            _fail("transaction_state_recovery_matrix_invalid", "非冲突事务不得携带conflict_kind")
        if (
            not isinstance(recovery_receipt, Mapping)
            or set(recovery_receipt)
            != {
                "receipt_kind",
                "transaction_id",
                "action",
                "reason_code",
                "retry_of_transaction_id",
                "preserved_pointer_revision",
                "preserved_pointer_digest",
                "staging_disposition",
                "final_reference_preserved",
                "receipt_digest",
            }
            or recovery_receipt.get("receipt_kind") != "transaction_recovery"
            or recovery_receipt.get("transaction_id") != payload.get("transaction_id")
            or recovery_receipt.get("action") != action
            or recovery_receipt.get("preserved_pointer_revision")
            != (current_pointer or {}).get("revision")
            or recovery_receipt.get("preserved_pointer_digest")
            != (current_pointer or {}).get("digest")
            or recovery_receipt.get("staging_disposition")
            not in {"discarded", "preserved_for_retry"}
            or recovery_receipt.get("final_reference_preserved") is not True
            or recovery_receipt.get("receipt_digest")
            != "sha256:" + _digest_without_fields(recovery_receipt, "receipt_digest")
            or payload.get("recovery_receipt_digest")
            != recovery_receipt.get("receipt_digest")
            or commit_receipt is not None
        ):
            _fail("transaction_recovery_receipt_mismatch", "recovery receipt未证明旧指针与最终引用被保留")
    expected_outcome_digest = "sha256:" + _digest_without_fields(
        {
            "transaction_id": payload.get("transaction_id"),
            "transaction_state": state,
            "disposition": payload.get("disposition"),
            "committed_revision": payload.get("committed_revision"),
            "committed_digest": payload.get("committed_digest"),
            "recovery_action": payload.get("recovery_action"),
            "conflict_kind": payload.get("conflict_kind"),
        }
    )
    if payload.get("outcome_digest") != expected_outcome_digest:
        _fail("transaction_outcome_digest_mismatch", "outcome_digest无法从事务终态重算")


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
    deferred_capabilities = capability_map.get("deferred_p2_1_capabilities")
    if not isinstance(deferred_capabilities, list):
        _fail("deferred_capability_contract_missing", "缺少P2.1 deferred capability目录")
    deferred_ids = [
        item.get("capability_id")
        for item in deferred_capabilities
        if isinstance(item, Mapping)
    ]
    if deferred_ids != ["CAP-P2-064"]:
        _fail("deferred_capability_contract_missing", "CAP-P2-064必须仅进入P2.1 deferred目录")
    deferred_entry = deferred_capabilities[0]
    if (
        deferred_entry.get("unit_id") != "PLAN-CAP-02"
        or deferred_entry.get("affected_question_ids") != ["Q20", "Q23", "Q26"]
        or deferred_entry.get("p2_v1_grounding_allowed") is not False
        or deferred_entry.get("hidden_fan_out_replacement_forbidden") is not True
    ):
        _fail("deferred_capability_contract_missing", "PLAN-CAP-02 P2.1边界未冻结")
    referenced_capabilities.extend(deferred_ids)
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
        "CAP-P2-062": "two_complete_state_interval_sets",
        "CAP-P2-063": "complete_fixed_cohort_member_result_set",
        "CAP-P2-064": "one_complete_typed_member_set_and_one_frozen_template_group",
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
        "Q09": {"CAP-P2-062", "CAP-P2-063"},
        "Q14": {"CAP-P2-011"},
        "Q16": {"CAP-P2-021", "CAP-P2-048"},
        "Q18": {"CAP-P2-011"},
        "Q20": {"CAP-P2-031"},
        "Q21": {"CAP-P2-028", "CAP-P2-029"},
        "Q22": {"CAP-P2-036"},
        "Q23": {"CAP-P2-003", "CAP-P2-007", "CAP-P2-048"},
        "Q26": {"CAP-P2-031", "CAP-P2-033", "CAP-P2-034"},
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
    if "CAP-P2-042" in set(questions_by_id["Q09"]["required_capability_ids"]):
        _fail("question_capability_review_gap", "Q09区间集合交集不得复用点时间比较OP-29")
    for question_id in ("Q20", "Q23", "Q26"):
        if "CAP-P2-064" in set(questions_by_id[question_id].get("required_capability_ids", [])) | set(
            questions_by_id[question_id].get("optional_capability_ids", [])
        ):
            _fail("p2_v1_fan_out_dependency_forbidden", f"{question_id}不得引用P2.1 deferred CAP-P2-064")
    expected_new_capability_units = {
        "CAP-P2-062": ["OP-38"],
        "CAP-P2-063": ["OP-39"],
        "CAP-P2-064": ["PLAN-CAP-02"],
    }
    for capability_id, expected_units in expected_new_capability_units.items():
        if capabilities_by_id[capability_id].get("unit_ids") != expected_units:
            _fail("capability_unit_unknown", f"{capability_id}必须只绑定{expected_units}")

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
    plan_cap_02 = next(item for item in decomposition["atomic_units"] if item["unit_id"] == "PLAN-CAP-02")
    if plan_cap_02.get("disposition") != "deferred_p2_1_not_in_p2_v1_plan_schema":
        _fail("p2_v1_fan_out_dependency_forbidden", "PLAN-CAP-02必须deferred且不进入P2 v1 Plan Schema")
    deferred_contract = decomposition.get("host_plan_capability_contracts", {}).get("PLAN-CAP-02", {})
    if (
        deferred_contract.get("disposition") != "deferred_p2_1"
        or deferred_contract.get("p2_v1_execution_allowed") is not False
        or deferred_contract.get("p2_v1_schema_included") is not False
        or deferred_contract.get("runtime_ready_claim") is not False
    ):
        _fail("p2_v1_fan_out_dependency_forbidden", "PLAN-CAP-02 deferred合同允许P2 v1执行或Schema准入")
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
    expected_ds_flow_fields = [
        "provider",
        "model",
        "version",
        "expected_response_model",
        "pi_version",
        "candidate_resource_sha256",
        "profile_registry_sha256",
        "identity_digest",
    ]
    if (
        ds_identity.get("provider") != "deepseek"
        or ds_identity.get("model") != "deepseek-v4-flash"
        or ds_identity.get("version") != "deepseek-v4-flash-pi-0.84.1-v1"
        or ds_identity.get("expected_response_model") != "deepseek-v4-flash"
        or ds_identity.get("pi_version") != "0.84.1"
        or ds_identity.get("candidate_resource_sha256")
        != "ac00eeb087bc9651fd27391066d9d16a416aad887cb552737696289ded3ce2b5"
        or ds_identity.get("profile_registry_sha256")
        != "e8881aa2b79f495da3ea551bb3b2423af45c118f5e622ac1877852bf0087bf4f"
        or ds_identity.get("flow_identity_required_fields")
        != expected_ds_flow_fields
    ):
        _fail("ds_model_identity_contract_missing", "DS候选资源、响应模型与Profile身份未精确冻结")
    teacher_plan_binding = model_roles.get("teacher_plan_binding")
    if (
        not isinstance(teacher_plan_binding, Mapping)
        or teacher_plan_binding.get("planning_unavailable_prevents_dual_answer_flow_creation")
        is not True
        or teacher_plan_binding.get("dual_flow_teacher_unavailable_phase")
        != "sol_reference_only_after_host_grounding"
        or teacher_plan_binding.get("host_grounding_required") is not True
        or teacher_plan_binding.get("teacher_plan_is_executable_plan") is not False
    ):
        _fail("model_role_contract_invalid", "Sol Semantic Plan与Host Grounding职责未分离")
    validators = model_roles.get("roles")
    alignment_evaluator = (
        validators.get("alignment_evaluator")
        if isinstance(validators, Mapping)
        else None
    )
    if (
        not isinstance(alignment_evaluator, Mapping)
        or alignment_evaluator.get("owner_kind") != "host_deterministic_evaluator"
        or alignment_evaluator.get("trusted_receipt_store")
        != "country_outage_p2_trusted_alignment_receipt_store_v1"
        or alignment_evaluator.get("caller_self_attested_metric_receipt_forbidden")
        is not True
        or alignment_evaluator.get("teacher_is_truth_oracle") is not False
    ):
        _fail("model_role_contract_invalid", "Alignment必须由Host确定性Evaluator生成受信回执")
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
            "p2_runtime_model_path_implemented",
            "runtime_integrated",
            "production_deployed",
        )
    ):
        _fail("model_role_boundary_drift", "模型角色不得声称P2运行时已实现、集成或部署")
    if design_boundary.get("ds_identity_frozen") is not True:
        _fail("ds_model_identity_contract_missing", "S1D-5候选必须冻结DS评测身份")
    if design_boundary.get("s1d5_design_evaluation_model_replay_executed") is not True:
        _fail("model_replay_evidence_missing", "S1D-5候选必须明确记录离线模型重放已经执行")
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
    if tool11.get("active_path_rule") != (
        "只有visibility=visible且common_path_status属于ordered或unordered的path可称该时点活动观测路径"
    ):
        _fail("active_path_rule_drift", "TOOL-11 活动路径规则必须使用合法统一状态枚举")
    source_view_requirements = payload.get("source_view_requirements", [])
    route_state_view = next(
        (
            item
            for item in source_view_requirements
            if isinstance(item, Mapping)
            and item.get("source_population_id")
            == "materialized_route_state_rows_at_exact_time"
        ),
        None,
    )
    membership_profile = (
        route_state_view.get("path_asn_membership_profile", {})
        if isinstance(route_state_view, Mapping)
        else {}
    )
    contains_contract = tool11.get("contains_asn_filter_contract")
    required_index_metadata = {
        "path_asn_membership_index_id",
        "path_asn_membership_profile_digest",
        "path_asn_membership_index_digest",
        "path_asn_membership_materialization_receipt_digest",
    }
    required_query_receipt_fields = {
        "receipt_kind",
        "tool_run_id",
        "identity_digest",
        "state_point_utc",
        "query_digest",
        "source_population_id",
        "source_dataset_digest",
        "contains_asn",
        "path_asn_membership_profile_digest",
        "path_asn_membership_index_digest",
        "path_asn_membership_materialization_receipt_digest",
        "eligible_row_predicate",
        "matched_member_keys_digest",
        "total_count",
        "disposition",
    }
    if (
        not isinstance(route_state_view, Mapping)
        or set(route_state_view.get("required_source_metadata", []))
        != required_index_metadata
        or membership_profile.get("profile_id")
        != TOOL11_PATH_ASN_MEMBERSHIP_PROFILE_ID
        or membership_profile.get("profile_digest")
        != TOOL11_PATH_ASN_MEMBERSHIP_PROFILE_DIGEST
        or _digest_without_fields(membership_profile, "profile_digest")
        != TOOL11_PATH_ASN_MEMBERSHIP_PROFILE_DIGEST
        or membership_profile.get("source_field") != "path_segments"
        or membership_profile.get("query_time_path_parsing_forbidden") is not True
        or membership_profile.get("ambiguous_invalid_unknown_not_applicable_excluded")
        is not True
        or not isinstance(contains_contract, Mapping)
        or contains_contract.get("source")
        != "pre_materialized_path_asn_membership_index_on_same_route_state_population"
        or contains_contract.get("profile_id")
        != TOOL11_PATH_ASN_MEMBERSHIP_PROFILE_ID
        or contains_contract.get("profile_digest")
        != TOOL11_PATH_ASN_MEMBERSHIP_PROFILE_DIGEST
        or contains_contract.get("eligible_row_predicate")
        != TOOL11_PATH_ASN_ELIGIBLE_ROW_PREDICATE
        or contains_contract.get("query_time_path_parsing_forbidden") is not True
        or contains_contract.get("llm_filtering_forbidden") is not True
        or contains_contract.get("trusted_query_receipt_required_when_filter_present")
        is not True
        or set(contains_contract.get("query_receipt_must_bind", []))
        != required_query_receipt_fields
    ):
        _fail("route_state_contains_asn_index_contract_open", "TOOL-11 contains_asn未绑定预物化索引与可信查询回执")
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
    native_filter = tool12.get("native_filter_contract")
    required_tool12_receipt_fields = {
        "tool_id",
        "publication_id",
        "source_population_id",
        "source_dataset_digest",
        "query_digest",
        "filter_profile_id",
        "filter_profile_digest",
        "path_asn_membership_index_id",
        "anchor_before_known_origin_index_id",
        "path_association_index_digest",
        "path_association_materialization_receipt_digest",
        "anchor_population_source_ref",
        "eligible_anchor_asns_digest",
        "eligible_anchor_asn_count",
        "target_contains_asn",
        "target_anchor_asn",
        "anchor_before_known_origin",
        "anchor_population_eligible",
        "matched_member_keys_digest",
        "matched_total_count",
        "disposition",
        "receipt_digest",
    }
    if (
        not isinstance(native_filter, Mapping)
        or native_filter.get("profile_id") != TOOL12_NATIVE_FILTER_PROFILE_ID
        or native_filter.get("profile_digest") != TOOL12_NATIVE_FILTER_PROFILE_DIGEST
        or native_filter.get("filters")
        != ["contains_asn", "anchor_asn_plus_anchor_before_known_origin"]
        or native_filter.get("query_time_path_parsing_forbidden") is not True
        or native_filter.get("llm_filtering_forbidden") is not True
        or native_filter.get("trusted_query_receipt_required_when_filter_present") is not True
        or set(native_filter.get("query_receipt_must_bind", []))
        != required_tool12_receipt_fields
    ):
        _fail("tool12_native_filter_contract_open", "TOOL-12原生过滤未绑定预物化索引与可信查询回执")
    tool12_fields = tool12.get("output_field_schemas", {})
    if (
        tool12_fields.get("origin_status", {}).get("const") != "known"
        or "observed_origin_asn" not in tool12_fields
        or not {"origin_status", "observed_origin_asn"}.issubset(
            tool12.get("output_member_fields", [])
        )
        or set(tool12.get("output_row_constraints", {}).get("origin_invariants", []))
        != {
            "origin_status == known",
            "observed_origin_asn == known_origin_asn",
            "ordered_sequence_eligible=true时去除连续prepend后AS_PATH末尾ASN == known_origin_asn",
        }
    ):
        _fail("tool12_origin_tail_contract_open", "TOOL-12 known origin未冻结为实际AS_PATH末尾ASN")
    if tool12_fields.get("route_observation_count", {}).get("minimum") != 1:
        _fail("path_association_count_invalid", "TOOL-12 关联行必须至少有一次观测")
    if tool12_fields.get("peer_asn_direction_ids", {}).get("minItems") != 1:
        _fail("path_direction_population_empty", "TOOL-12 每条路径关联至少必须有一个观察方向")
    if tool12_fields.get("path_segments", {}).get("minItems") != 1:
        _fail("empty_ordered_path_allowed", "TOOL-12 不得允许空路径进入路径Operator")
    if "https://domeye.example/contracts/data/route-event.schema.json#/$defs/asPathSegment" not in json.dumps(
        tool12_fields.get("path_segments", {}), sort_keys=True
    ):
        _fail("typed_path_segment_schema_missing", "TOOL-12 必须保留类型化 AS_PATH segment")
    common_path_status_enum = {
        "ordered",
        "unordered",
        "ambiguous",
        "invalid",
        "unknown",
        "not_applicable",
    }
    path_profile = payload.get("path_canonicalization_profile")
    if not isinstance(path_profile, dict):
        _fail("path_digest_profile_unfrozen", "缺少目录级路径规范化Profile")
    expected_path_profile = {
        "profile_id": "AS-PATH-CANONICALIZATION-1.0.0",
        "profile_version": "1.0.0",
        "input_schema_id": (
            "https://domeye.example/contracts/data/route-event.schema.json"
            "#/$defs/asPathSegment"
        ),
        "segment_order_rule": "保留输入segment顺序",
        "sequence_rule": "as_sequence与confederation_sequence内ASN顺序和prepend重复均原样保留",
        "set_rule": "as_set与confederation_set内ASN按数值升序稳定排序且不去重",
        "segment_type_rule": "segment_type进入摘要且不得拍平、合并或转换",
        "encoding_rule": "对规范化segment数组执行RFC8785 canonical JSON UTF-8编码",
        "path_digest_algorithm": "sha256_rfc8785_canonical_json",
        "empty_path_forbidden": True,
        "source_native_required": True,
        "profile_digest": "eb4d2081ee69ab0254b7af461122cf315b6bcdf24551c22de7e8dccc6d965966",
    }
    if path_profile != expected_path_profile:
        _fail("path_digest_profile_unfrozen", "路径规范化Profile 1.0.0正文或固定摘要漂移")
    path_profile_body = dict(path_profile)
    declared_path_profile_digest = path_profile_body.pop("profile_digest", None)
    computed_path_profile_digest = hashlib.sha256(
        json.dumps(
            path_profile_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if declared_path_profile_digest != computed_path_profile_digest:
        _fail("path_digest_profile_unfrozen", "路径规范化Profile摘要与内容不一致")
    for tool in (tool11, tool12):
        unit_id = tool["unit_id"]
        if "common_path_status" not in tool.get("output_member_fields", []):
            _fail("common_path_status_missing", f"{unit_id} 缺少统一路径状态字段")
        status_schema = tool.get("output_field_schemas", {}).get(
            "common_path_status", {}
        )
        if set(status_schema.get("enum", [])) != common_path_status_enum:
            _fail("common_path_status_drift", f"{unit_id} 统一路径状态枚举漂移")
        constraints_text = json.dumps(
            tool.get("output_row_constraints", {}), ensure_ascii=False, sort_keys=True
        )
        if "common_path_status" not in constraints_text:
            _fail("common_path_status_mapping_missing", f"{unit_id} 缺少原生状态映射约束")
        required_path_identity_fields = {
            "path_digest",
            "path_canonicalization_profile_id",
            "path_canonicalization_profile_digest",
        }
        if not required_path_identity_fields.issubset(
            tool.get("output_member_fields", [])
        ) or not required_path_identity_fields.issubset(
            tool.get("output_field_schemas", {})
        ):
            _fail("path_digest_source_missing", f"{unit_id} 未原生发布规范路径摘要身份")
        path_digest_schema = tool["output_field_schemas"]["path_digest"]
        expected_path_digest_type = ["string", "null"] if unit_id == "TOOL-11" else "string"
        if (
            path_digest_schema.get("type") != expected_path_digest_type
            or path_digest_schema.get("pattern") != "^[a-f0-9]{64}$"
        ):
            _fail("path_digest_schema_drift", f"{unit_id} path_digest类型或格式漂移")
        if tool["output_field_schemas"]["path_canonicalization_profile_id"].get(
            "const"
        ) != path_profile["profile_id"] or tool["output_field_schemas"][
            "path_canonicalization_profile_digest"
        ].get("const") != declared_path_profile_digest:
            _fail("path_digest_profile_unfrozen", f"{unit_id} 路径规范化Profile未冻结")
    tool11_constraints = tool11["output_row_constraints"].get("allOf", [])

    def _has_path_digest_branch(statuses: set[str], digest_type: str) -> bool:
        for branch in tool11_constraints:
            condition = branch.get("if", {}).get("properties", {}).get("path_status", {})
            condition_values = set(condition.get("enum", []))
            effect = branch.get("then", {}).get("properties", {}).get("path_digest", {})
            if condition_values == statuses and effect.get("type") == digest_type:
                if digest_type == "string":
                    return effect.get("pattern") == "^[a-f0-9]{64}$"
                return True
        return False

    if not _has_path_digest_branch({"known_ordered", "known_unordered"}, "string"):
        _fail("path_digest_known_binding_missing", "TOOL-11 known path必须绑定非空path_digest")
    if not _has_path_digest_branch({"unknown", "not_applicable"}, "null"):
        _fail("path_digest_null_binding_missing", "TOOL-11 unknown/not_applicable必须绑定null path_digest")
    source_requirements = {
        item.get("source_population_id"): item
        for item in payload.get("source_view_requirements", [])
        if isinstance(item, dict)
    }
    for population_id in (
        "materialized_route_state_rows_at_exact_time",
        "window_path_association_evidence_rows",
    ):
        source_fields = set(
            source_requirements.get(population_id, {}).get("required_source_fields", [])
        )
        if not {
            "common_path_status",
            "path_digest",
            "path_canonicalization_profile_id",
            "path_canonicalization_profile_digest",
        }.issubset(source_fields):
            _fail(
                "path_digest_source_missing",
                f"{population_id} 必须原生存储统一状态与路径摘要，禁止Operator隐式适配",
            )
    tool12_source = source_requirements.get("window_path_association_evidence_rows", {})
    tool12_profile = tool12_source.get("filter_profile")
    if (
        set(tool12_source.get("required_source_metadata", []))
        != {
            "path_asn_membership_index_id",
            "anchor_before_known_origin_index_id",
            "path_association_filter_profile_digest",
            "path_association_index_digest",
            "path_association_materialization_receipt_digest",
            "anchor_population_source_ref",
            "eligible_anchor_asns_digest",
            "eligible_anchor_asn_count",
        }
        or not isinstance(tool12_profile, Mapping)
        or tool12_profile.get("profile_id") != TOOL12_NATIVE_FILTER_PROFILE_ID
        or tool12_profile.get("profile_digest") != TOOL12_NATIVE_FILTER_PROFILE_DIGEST
        or _digest_without_fields(tool12_profile, "profile_digest")
        != TOOL12_NATIVE_FILTER_PROFILE_DIGEST
        or tool12_profile.get("profile_digest_recipe")
        != "sha256(canonical(filter_profile excluding profile_digest))"
        or tool12_profile.get("query_time_path_parsing_forbidden") is not True
    ):
        _fail("tool12_native_filter_contract_open", "TOOL-12 source view未冻结原生过滤索引Profile")
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
        if unit_id in {
            "OP-15",
            "OP-16",
            "OP-25",
            "OP-27",
            "OP-29",
            "OP-33",
            "OP-37",
        }:
            expected_split[
                "evidence_edge_projection_is_output_view_not_second_business_transform"
            ] = True
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
    if (
        "ranked_members"
        not in op05.get("output_contract", {}).get("required_fields", [])
        or op05.get("output_contract", {})
        .get("field_types", {})
        .get("ranked_members")
        != "array<severity_ranked_member>"
        or "competition rank（1,1,3）"
        not in op05.get("algorithm_contract", {}).get("tie_rule", "")
        or "result_position为全序1..n"
        not in op05.get("algorithm_contract", {}).get("tie_rule", "")
    ):
        _fail(
            "as_rank_output_contract_open",
            "OP-05必须逐ASN发布competition severity rank与稳定result position",
        )
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
    op07 = operators_by_id["OP-07"]
    if not {
        "target_state",
        "window",
        "grid_step_seconds",
        "series_digest",
    }.issubset(set(op07.get("output_contract", {}).get("required_fields", []))):
        _fail("state_interval_context_missing", "OP-07必须原样携带目标状态、窗口、网格与输入序列摘要")
    op38 = operators_by_id["OP-38"]
    if (
        op38.get("input_semantic") != "two_complete_state_interval_sets"
        or op38.get("output_semantic") != "state_interval_overlap_set"
        or op38.get("complexity_contract") != {"time": "O(n+m)", "space": "O(k)"}
        or op38.get("input_contract", {}).get("complete_input_required") is not True
        or "端点相接不算重叠" not in op38.get("algorithm_contract", {}).get("rule", "")
    ):
        _fail("interval_set_intersection_contract_open", "OP-38半开完整区间集交集合同未闭合")
    op39 = operators_by_id["OP-39"]
    if (
        op39.get("input_semantic") != "complete_fixed_cohort_member_result_set"
        or op39.get("output_semantic") != "fixed_cohort_prefix_set"
        or op39.get("complexity_contract") != {"time": "O(n log n)", "space": "O(k)"}
        or op39.get("input_contract", {}).get("complete_input_required") is not True
        or "(afi,canonical_prefix)" not in op39.get("algorithm_contract", {}).get("rule", "")
    ):
        _fail("fixed_cohort_prefix_projection_contract_open", "OP-39固定cohort前缀集合投影合同未闭合")
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
    op19_required = set(op19["input_contract"]["required_fields"])
    op19_invariants = set(op19["input_contract"].get("invariants", []))
    if not {
        "population_filter_receipt_digest",
        "source_result_set_query_receipt_digest",
        "source_result_set_ref",
        "host_projection_receipt_digest",
        "population_evidence_ref",
    }.issubset(op19_required) or not {
        "population_filter_receipt_digest == source_result_set_query_receipt_digest",
        "source_result_set_ref.query_receipt_digest == population_filter_receipt_digest",
        "association_members are a one-to-one Host-attested structural projection of every frozen source ResultSet member",
    }.issubset(op19_invariants) or "不在Operator内判断contains/order" not in op19["algorithm_contract"]["rule"]:
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
    projection_operator_ids = {
        "OP-15",
        "OP-16",
        "OP-25",
        "OP-27",
        "OP-29",
        "OP-33",
        "OP-37",
    }
    edge_projection_contract = schema.get(
        "x-evidence-edge-projection-contract"
    )
    if (
        not isinstance(edge_projection_contract, Mapping)
        or set(edge_projection_contract.get("applies_to", []))
        != projection_operator_ids
        or any(
            edge_projection_contract.get(field) is not True
            for field in (
                "projection_is_same_operator_result_read_view_not_second_business_transform",
                "operator_emits_relation_type_endpoint_domain_digest_optional_typed_asn_projection_digest_and_publishability",
                "operator_emits_typed_relation_projection_preimage",
                "eligibility_cardinality_endpoint_and_field_copy_are_per_operator_frozen",
                "relation_projection_digest_is_sha256_of_canonical_projection_wrapper",
                "host_only_validates_schema_artifact_digest_reference_and_field_equality",
                "host_recomputation_of_path_set_time_join_or_consistency_semantics_forbidden",
            )
        )
        or any(
            operators_by_id[unit_id]
            .get("split_test", {})
            .get(
                "evidence_edge_projection_is_output_view_not_second_business_transform"
            )
            is not True
            for unit_id in projection_operator_ids
        )
    ):
        _fail(
            "operator_edge_projection_atomicity_open",
            "Evidence edge投影必须是同一Operator结果视图，不得成为第二项业务变换",
        )
    catalog_projection_contract = payload.get("evidence_edge_projection_contract")
    expected_projection_contracts = {
        "OP-15": ("outcome == found", "exactly_one_if_eligible_else_zero", "pathContainsEdgeProjectionBody", "from.path_digest_and_null;to.target_asn_nonnull"),
        "OP-16": ("outcome == computed", "one_per_left_or_right_neighbor", "directAdjacencyEdgeProjectionBody", "from.target_asn_nonnull;to.neighbor_asn_nonnull"),
        "OP-25": ("member_count > 0", "exactly_one_if_eligible_else_zero", "setIntersectionEdgeProjectionBody", "from.left_digest;to.right_digest"),
        "OP-27": ("outcome == computed && denominator_count > 0 && ratio_exact == 1/1", "exactly_one_if_eligible_else_zero", "setContainsEdgeProjectionBody", "from.right_digest;to.left_digest"),
        "OP-29": ("relation in comparable_relation_enum", "exactly_one_if_eligible_else_zero", "temporalEdgeProjectionBody", "from.left_digest;to.right_digest"),
        "OP-33": ("matched row exists", "one_per_matched_row", "atTimeEdgeProjectionBody", "from.new_prefix_state_digest;to.route_state_digest"),
        "OP-37": ("class == conflict", "exactly_one_if_eligible_else_zero", "conflictEdgeProjectionBody", "from.left_digest;to.right_digest"),
    }
    if (
        not isinstance(catalog_projection_contract, Mapping)
        or catalog_projection_contract.get("contract_id")
        != "P2-S1-OPERATOR-EDGE-READ-VIEW-1.0.0"
        or catalog_projection_contract.get("same_result_read_view_only") is not True
        or catalog_projection_contract.get("second_business_transform_forbidden") is not True
        or catalog_projection_contract.get("host_business_semantic_recomputation_forbidden") is not True
        or catalog_projection_contract.get("common_required_fields")
        != [
            "relation_type",
            "from_endpoint",
            "to_endpoint",
            "relation_projection",
            "relation_projection_digest",
            "publishable",
        ]
        or catalog_projection_contract.get("projection_digest_recipe")
        != "sha256_rfc8785({projection: relation_projection})"
        or {
            unit_id: (
                contract.get("eligibility"),
                contract.get("cardinality"),
                contract.get("projection_schema"),
                contract.get("endpoint_rule"),
            )
            for unit_id, contract in catalog_projection_contract.get(
                "unit_contracts", {}
            ).items()
            if isinstance(contract, Mapping)
        }
        != expected_projection_contracts
    ):
        _fail(
            "operator_edge_projection_atomicity_open",
            "逐Operator edge只读视图的资格、基数、字段投影与端点规则未冻结",
        )
    edge_projection_schema = schema_defs.get("evidenceEdgeProjection", {})
    if (
        "relation_projection"
        not in edge_projection_schema.get("required", [])
        or set(
            ref.get("$ref", "").rsplit("/", 1)[-1]
            for ref in edge_projection_schema.get("properties", {})
            .get("relation_projection", {})
            .get("oneOf", [])
            if isinstance(ref, Mapping)
        )
        != {contract[2] for contract in expected_projection_contracts.values()}
    ):
        _fail(
            "operator_edge_projection_atomicity_open",
            "Operator edge投影视图未携带可验证的关系投影本体",
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
    plan_path, result_path, graph_path, model_path, consistency_path = (
        repo_root / relative for relative in ARTIFACTS_BY_STAGE["S1D-4"]
    )
    plan = _load_json(plan_path)
    result_set = _load_json(result_path)
    graph = _load_json(graph_path)
    model_flow = _load_json(model_path)
    tool_catalog = _load_json(repo_root / ARTIFACTS_BY_STAGE["S1D-2"][0])
    tool_identity = tool_catalog.get("common_field_schemas", {}).get("identity", {})
    frozen_identity_fields = set(tool_identity.get("required", []))
    frozen_finality = set(
        tool_identity.get("properties", {}).get("finality", {}).get("enum", [])
    )
    if not frozen_identity_fields or frozen_finality != {"event_end_unknown", "event_end_known"}:
        _fail("cross_stage_identity_contract_drift", "无法读取S1D-2冻结身份合同")
    for payload, path in (
        (plan, plan_path),
        (result_set, result_path),
        (graph, graph_path),
        (model_flow, model_path),
    ):
        _validate_schema_file(payload, path)
        _validate_local_schema_refs(payload, path)
        _require_closed_schema_object(
            payload,
            code="s1d4_schema_open",
            subject=path.name,
        )
        metadata = payload.get("x-contract-metadata")
        if not isinstance(metadata, dict) or metadata.get("stage") != "S1D-4" or metadata.get(
            "status"
        ) != "design_contract_only" or not metadata.get("artifact_id"):
            _fail("s1d4_schema_metadata_missing", f"{path.name} 缺少设计期制品身份")

    plan_defs = _schema_defs(
        plan,
        plan_path,
        (
            "identityBinding",
            "executionUnitRef",
            "planNode",
            "planDefinition",
            "answerExecutionPolicy",
            "nodeExecutionRevision",
            "investigationSnapshot",
            "designBoundary",
        ),
    )
    _require_closed_schema_object(
        plan_defs["executionUnitRef"],
        code="plan_execution_unit_ref_open",
        subject="executionUnitRef",
        required_fields=(
            "unit_id",
            "unit_kind",
            "unit_version",
            "contract_digest",
            "unit_implementation_digest",
            "unit_semantic_digest",
            "atomic_capability_id",
            "atomic_capability_version",
            "capability_contract_digest",
        ),
    )
    plan_identity = _require_closed_schema_object(
        plan_defs["identityBinding"],
        code="cross_stage_identity_contract_drift",
        subject="identityBinding",
        required_fields=frozen_identity_fields,
    )
    if set(plan_identity["properties"]["finality"].get("enum", [])) != frozen_finality:
        _fail("cross_stage_identity_contract_drift", "Plan finality 与S1D-2漂移")
    execution_unit_ref = plan_defs["executionUnitRef"]
    unit_pattern = execution_unit_ref["properties"]["unit_id"].get("pattern", "")
    if "PLAN-CAP" not in unit_pattern or execution_unit_ref["properties"].get(
        "unit_implementation_digest"
    ) != {"$ref": "#/$defs/prefixedSha256"} or execution_unit_ref["properties"].get(
        "unit_semantic_digest"
    ) != {"$ref": "#/$defs/prefixedSha256"}:
        _fail("registry_admission_binding_open", "Plan节点未对齐S0A Registry准入身份")
    input_binding_contract = plan.get("x-input-binding-contract")
    if not isinstance(input_binding_contract, dict) or any(
        input_binding_contract.get(field) is not True
        for field in (
            "input_name_must_be_registered_input_schema_property",
            "input_name_must_have_bound_parameter_value",
            "duplicate_input_name_forbidden",
            "every_bound_parameter_has_exactly_one_input_binding",
            "node_result_result_set_and_operator_receipt_source_ref_must_be_ancestor_node",
            "node_result_result_set_and_operator_receipt_source_artifact_digest_must_equal_latest_committed_ancestor_result_digest",
            "latest_committed_ancestor_result_digest_must_resolve_from_host_trusted_node_result_receipt_store",
            "snapshot_self_reported_result_and_receipt_digest_are_not_sufficient",
        )
    ) or input_binding_contract.get("source_digest_recipe") != [
        "input_name",
        "source_kind",
        "source_ref",
        "bound_parameter_value",
    ]:
        _fail("plan_input_binding_contract_open", "Plan输入绑定未冻结到Registry字段与实际参数值")
    plan_node = _require_closed_schema_object(
        plan_defs["planNode"],
        code="composite_plan_node_forbidden",
        subject="planNode",
        required_fields=(
            "node_id",
            "execution_unit",
            "depends_on",
            "requiredness",
            "input_bindings",
            "completeness_requirement",
            "incomplete_input_policy",
        ),
    )
    plan_node_properties = plan_node["properties"]
    forbidden_node_fields = {
        "execution_units",
        "fallback_units",
        "internal_dag",
        "embedded_capabilities",
        "mode",
    }
    if forbidden_node_fields & set(plan_node_properties) or plan_node_properties.get(
        "execution_unit"
    ) != {"$ref": "#/$defs/executionUnitRef"}:
        _fail("composite_plan_node_forbidden", "一个计划节点必须只引用一个原子执行单元")
    plan_definition = _require_closed_schema_object(
        plan_defs["planDefinition"],
        code="plan_revision_contract_open",
        subject="planDefinition",
        required_fields=("plan_id", "plan_revision", "parent_plan_revision", "plan_state", "nodes", "answer_execution_policy"),
    )
    _require_closed_schema_object(
        plan_defs["answerExecutionPolicy"],
        code="plan_answer_execution_policy_open",
        subject="answerExecutionPolicy",
        required_fields=("teacher_required", "mode", "authorization_digest"),
    )
    investigation = _require_closed_schema_object(
        plan_defs["investigationSnapshot"],
        code="plan_revision_contract_open",
        subject="investigationSnapshot",
        required_fields=(
            "investigation_id",
            "investigation_revision",
            "parent_investigation_revision",
            "plan_revision",
            "status",
            "node_execution_revisions",
        ),
    )
    execution = _require_closed_schema_object(
        plan_defs["nodeExecutionRevision"],
        code="plan_revision_contract_open",
        subject="nodeExecutionRevision",
        required_fields=(
            "node_id",
            "execution_revision",
            "parent_execution_revision",
            "state",
            "idempotency_key",
            "receipt_digest",
        ),
    )
    expected_plan_states = {"draft", "admitted", "rejected", "superseded"}
    expected_investigation_states = {
        "pending",
        "running",
        "cancel_requested",
        "completed",
        "partially_completed",
        "failed",
        "cancelled",
    }
    expected_node_states = {
        "pending",
        "ready",
        "running",
        "prepared",
        "committed",
        "failed",
        "cancelled",
        "skipped_dependency_failed",
        "reused",
    }
    if set(plan_definition["properties"]["plan_state"].get("enum", [])) != expected_plan_states:
        _fail("plan_state_contract_drift", "plan_state 人口漂移")
    if set(investigation["properties"]["status"].get("enum", [])) != expected_investigation_states:
        _fail("plan_state_contract_drift", "investigation status 人口漂移")
    if set(execution["properties"]["state"].get("enum", [])) != expected_node_states:
        _fail("plan_state_contract_drift", "node execution state 人口漂移")
    if plan_definition["properties"].get("nodes", {}).get("items") != {
        "$ref": "#/$defs/planNode"
    } or investigation["properties"].get("node_execution_revisions", {}).get("items") != {
        "$ref": "#/$defs/nodeExecutionRevision"
    }:
        _fail("plan_typed_collection_open", "计划节点或执行修订数组未绑定冻结Schema")
    plan_condition_text = json.dumps(plan_definition.get("allOf", []), sort_keys=True)
    investigation_condition_text = json.dumps(investigation.get("allOf", []), sort_keys=True)
    execution_condition_text = json.dumps(execution.get("allOf", []), sort_keys=True)
    if not all(
        marker in plan_condition_text
        for marker in (
            '"plan_revision": {"const": 1}',
            '"parent_plan_revision": {"type": "null"}',
            '"plan_state": {"const": "admitted"}',
            '"admission_receipt_digest"',
        )
    ) or not all(
        marker in investigation_condition_text
        for marker in (
            '"investigation_revision": {"const": 1}',
            '"parent_investigation_revision": {"type": "null"}',
            '"partially_completed"',
            '"evidence_graph_revision"',
        )
    ) or not all(
        marker in execution_condition_text
        for marker in (
            '"execution_revision": {"const": 1}',
            '"parent_execution_revision": {"type": "null"}',
            '"committed"',
            '"result_digest"',
            '"receipt_digest"',
            '"failure_code"',
            '"skipped_dependency_failed"',
        )
    ):
        _fail("plan_revision_contract_open", "三层revision或节点结果状态条件约束未闭合")
    state_contract = plan.get("x-plan-state-contract")
    expected_plan_transitions = {
        "draft->admitted",
        "draft->rejected",
        "admitted->superseded",
    }
    expected_investigation_transitions = {
        "pending->running",
        "running->cancel_requested",
        "running->completed",
        "running->partially_completed",
        "running->failed",
        "running->cancelled",
        "cancel_requested->cancelled",
        "cancel_requested->partially_completed",
        "completed->completed(plan_revision_changed)",
        "partially_completed->partially_completed(plan_revision_changed)",
    }
    if not isinstance(state_contract, dict) or set(
        state_contract.get("plan_definition_transitions", [])
    ) != expected_plan_transitions or set(
        state_contract.get("investigation_transitions", [])
    ) != expected_investigation_transitions or not state_contract.get("node_transitions"):
        _fail("plan_state_contract_drift", "状态迁移合同漂移")
    revision_contract = plan.get("x-revision-contract")
    if not isinstance(revision_contract, dict) or any(
        revision_contract.get(field) is not expected
        for field, expected in (
            ("same_parameter_rerun_changes_plan_revision", False),
            ("parameter_or_permission_change_requires_new_plan_revision", True),
            ("history_overwrite_forbidden", True),
        )
    ) or any(
        not revision_contract.get(field)
        for field in (
            "plan_revision_semantic",
            "investigation_revision_semantic",
            "execution_revision_semantic",
        )
    ):
        _fail("plan_revision_contract_open", "三层 revision 语义未冻结")
    composition = plan.get("x-composition-contract")
    if not isinstance(composition, dict) or composition.get("composition_location") != "InvestigationPlan" or any(
        composition.get(field) is not True
        for field in (
            "one_node_one_atomic_execution_unit",
            "embedded_execution_units_forbidden",
            "fallback_units_forbidden",
            "internal_dag_forbidden",
            "hard_dependency_failure_propagates_only_to_dependents",
            "independent_branch_may_continue",
            "required_identity_failure_stops_before_other_units",
        )
    ):
        _fail("composite_plan_node_forbidden", "组合边界或局部失败传播未闭合")
    if composition.get("p2_v1_deferred_units") != ["TOOL-13", "OP-34", "PLAN-CAP-02"] or composition.get(
        "p2_v1_deferred_units_admission_forbidden"
    ) is not True:
        _fail("deferred_unit_policy_open", "P2 v1 deferred 单元准入边界未冻结")
    plan_schema_text = json.dumps(plan, ensure_ascii=False, sort_keys=True)
    if any(
        forbidden in plan_schema_text
        for forbidden in ("fan_out_groups", "fan_out_member", "fanOutGroup", "fanOutTemplateNode")
    ):
        _fail("p2_v1_fan_out_schema_forbidden", "P2 v1 Plan Schema不得包含PLAN-CAP-02 fan-out结构")
    incomplete_policy = plan.get("x-incomplete-input-contract")
    incomplete_enum = set(
        plan_node_properties.get("incomplete_input_policy", {}).get("enum", [])
    )
    plan_node_text = json.dumps(plan_node, ensure_ascii=False, sort_keys=True)
    if (
        incomplete_enum
        != {"fail_closed", "operator_declared_monotonic_lower_bound"}
        or not isinstance(incomplete_policy, dict)
        or incomplete_policy.get("default") != "fail_closed"
        or incomplete_policy.get(
            "lower_bound_requires_registered_operator_monotonicity_contract"
        )
        is not True
        or incomplete_policy.get("preview_is_not_completeness") is not True
        or "monotonicity_contract_ref" not in plan_node_text
        or '"const": "operator"' not in plan_node_text
    ):
        _fail("incomplete_input_policy_open", "不完整输入默认失败关闭或单调性例外未闭合")
    digest_normalization = plan.get("x-digest-normalization-contract")
    if not isinstance(digest_normalization, dict) or digest_normalization.get(
        "data_and_evidence_digest_format"
    ) != "lowercase_hex64" or digest_normalization.get(
        "governance_registry_and_admission_digest_format"
    ) != "sha256:lowercase_hex64" or digest_normalization.get(
        "projected_governance_digest_must_equal_bound_data_digest_when_same_artifact"
    ) is not True or digest_normalization.get("implicit_prefix_addition_or_removal_forbidden") is not True:
        _fail("registry_admission_binding_open", "S0A治理摘要与S1数据摘要的规范化合同未冻结")
    plan_digest_contract = plan.get("x-digest-recomputation-contract")
    plan_registry_context = plan.get("x-registry-admission-view-contract")
    if not isinstance(plan_digest_contract, dict) or set(plan_digest_contract) != {
        "canonical_json",
        "identity_binding_digest",
        "dag_digest",
        "admission_plan_subject_digest",
        "snapshot_digest",
    } or not isinstance(plan_registry_context, dict) or plan_registry_context.get(
        "view_contract_id"
    ) != "country_outage_p2_registry_admission_view_v1" or plan_registry_context.get(
        "source"
    ) != "trusted_registry_store_lookup_only" or plan_registry_context.get(
        "trusted_store_contract_id"
    ) != "country_outage_p2_trusted_registry_store_v1" or any(
        plan_registry_context.get(field) is not True
        for field in (
            "trusted_store_host_attestation_required",
            "registry_entries_never_accepted_from_request_payload",
        )
    ) or plan_registry_context.get(
        "caller_supplied_view_forbidden"
    ) is not True or plan_registry_context.get(
        "resolver_receipt_binds_snapshot_entries_digest_and_resolver_version"
    ) is not True or any(
        plan_registry_context.get(field) is not True
        for field in (
            "resolver_contract_and_implementation_digests_fixed",
            "governance_digest_must_match_exactly_with_sha256_prefix",
            "data_digest_requires_explicit_projection_field",
            "execution_unit_input_parameters_and_output_schema_bound_to_registry_contract",
            "admission_receipt_from_trusted_store_only",
        )
    ) or plan_registry_context.get("p2_v1_deferred_units") != [
        "TOOL-13",
        "OP-34",
    ]:
        _fail("plan_runtime_context_open", "Plan摘要或可信Registry准入视图合同未冻结")
    _validate_runtime_validator_contract(
        plan,
        expected_ids=(
            "plan_identity_time_and_admission_receipt_closure",
            "registry_admission_binding",
            "plan_snapshot_binding_equality",
            "plan_dag_and_wave_closure",
            "investigation_terminal_state_closure",
            "plan_revision_change_classifier",
        ),
        code="plan_instance_validator_open",
        subject="investigation_plan",
    )

    result_defs = _schema_defs(
        result_set,
        result_path,
        ("sourceIdentity", "sourceTool", "pageManifestEntry", "previewView", "designBoundary"),
    )
    result_properties = result_set.get("properties", {})
    result_identity = _require_closed_schema_object(
        result_defs["sourceIdentity"],
        code="cross_stage_identity_contract_drift",
        subject="ResultSet sourceIdentity",
        required_fields=frozen_identity_fields | {"identity_digest"},
    )
    if set(result_identity["properties"]["finality"].get("enum", [])) != frozen_finality:
        _fail("cross_stage_identity_contract_drift", "ResultSet finality 与S1D-2漂移")
    if result_properties.get("state", {}).get("const") != "frozen" or result_properties.get(
        "manifest_digest"
    ) != {"$ref": "#/$defs/sha256"} or result_properties.get(
        "freeze_receipt_digest"
    ) != {"$ref": "#/$defs/sha256"}:
        _fail("result_set_freeze_contract_open", "完整或不完整ResultSet都必须形成不可变冻结回执")
    if result_properties.get("page_manifest", {}).get("items") != {
        "$ref": "#/$defs/pageManifestEntry"
    } or result_properties.get("member_segments", {}).get("items") != {
        "$ref": "#/$defs/memberSegment"
    } or result_properties.get("preview_views", {}).get("items") != {
        "$ref": "#/$defs/previewView"
    }:
        _fail("result_set_typed_collection_open", "ResultSet分页、成员段或预览数组未绑定冻结Schema")
    completeness = result_properties.get("set_completeness", {}).get("enum", [])
    if set(completeness) != {"complete", "partial_page", "source_incomplete"} or "preview" in completeness:
        _fail("result_set_completeness_conflated", "preview 不得成为 ResultSet 完整性状态")
    source_completeness_contract = result_set.get(
        "x-source-completeness-contract"
    )
    if (
        result_properties.get("source_completeness_receipt_digest")
        != {
            "oneOf": [
                {"$ref": "#/$defs/sha256"},
                {"type": "null"},
            ]
        }
        or not isinstance(source_completeness_contract, Mapping)
        or source_completeness_contract.get(
            "source_incomplete_requires_host_tool_receipt"
        )
        is not True
        or source_completeness_contract.get("receipt_validator_id")
        != SOURCE_COMPLETENESS_VALIDATOR_ID
        or source_completeness_contract.get("receipt_validator_version")
        != SOURCE_COMPLETENESS_VALIDATOR_VERSION
        or source_completeness_contract.get("receipt_validator_contract_digest")
        != SOURCE_COMPLETENESS_VALIDATOR_CONTRACT_DIGEST
        or source_completeness_contract.get(
            "receipt_validator_implementation_digest"
        )
        != SOURCE_COMPLETENESS_VALIDATOR_IMPLEMENTATION_DIGEST
        or set(source_completeness_contract.get("receipt_binds", []))
        != {
            "tool_run_id",
            "source_population_id",
            "source_dataset_digest",
            "source_completeness",
            "limitations_digest",
            "returned_count",
            "total_count",
            "resume_page_token",
        }
    ):
        _fail(
            "result_set_source_incomplete_provenance_open",
            "source_incomplete必须绑定冻结来源完整性回执",
        )
    page = _require_closed_schema_object(
        result_defs["pageManifestEntry"],
        code="result_set_page_closure_open",
        subject="pageManifestEntry",
        required_fields=(
            "page_index",
            "token_in",
            "token_out",
            "identity_digest",
            "query_digest",
            "stable_sort_digest",
            "source_population_id",
            "source_population_schema_digest",
            "source_dataset_digest",
            "member_count",
            "page_content_digest",
            "page_receipt_digest",
        ),
    )
    preview = _require_closed_schema_object(
        result_defs["previewView"],
        code="result_set_preview_overclaim",
        subject="previewView",
        required_fields=(
            "view_id",
            "source_result_set_id",
            "source_result_set_revision",
            "limit",
            "returned_count",
            "member_refs",
            "represents_complete_population",
        ),
    )
    if preview["properties"]["represents_complete_population"].get("const") is not False:
        _fail("result_set_preview_overclaim", "预览不得声称代表完整人口")
    closure = result_set.get("x-result-set-closure-contract")
    if not isinstance(closure, dict) or any(
        closure.get(field) is not True
        for field in (
            "token_chain_must_close",
            "page_identity_query_sort_source_digests_must_match",
            "stable_sort_must_not_regress_across_pages",
            "dedupe_key_unique_across_pages",
            "sum_page_member_count_equals_returned_count",
            "complete_requires_returned_equals_total",
            "partial_with_known_total_requires_returned_strictly_less_than_total",
            "manifest_and_content_digests_recomputable",
        )
    ) or closure.get("first_token_in", "missing") is not None or closure.get(
        "last_token_out_for_complete", "missing"
    ) is not None or closure.get("source_identity_time_order") != (
        "window_start_utc <= window_end_utc <= data_through_utc"
    ):
        _fail("result_set_page_closure_open", "完整分页链闭包未冻结")
    result_digest_contract = result_set.get("x-digest-recomputation-contract")
    result_runtime_context = result_set.get("x-runtime-context-contract")
    if not isinstance(result_digest_contract, dict) or not {
        "query_digest",
        "stable_sort_digest",
        "segment_digest",
        "page_content_digest",
        "manifest_digest",
        "content_digest",
        "result_set_id",
        "preview_view_digest",
    }.issubset(result_digest_contract) or not isinstance(
        result_runtime_context, dict
    ) or result_runtime_context.get("registry_view_source") != "trusted_registry_store_lookup_only" or result_runtime_context.get(
        "trusted_store_contract_id"
    ) != "country_outage_p2_trusted_registry_store_v1" or any(
        result_runtime_context.get(field) is not True
        for field in (
            "trusted_store_host_attestation_required",
            "registry_entries_never_accepted_from_request_payload",
        )
    ) or result_runtime_context.get(
        "query_page_and_freeze_receipts_resolve_and_recompute"
    ) is not True or result_runtime_context.get(
        "caller_supplied_registry_view_forbidden"
    ) is not True or result_runtime_context.get(
        "source_population_id_and_member_schema_must_equal_registered_tool_output_population"
    ) is not True or result_runtime_context.get(
        "every_resolved_member_must_validate_against_registered_member_schema"
    ) is not True:
        _fail("result_set_digest_contract_open", "ResultSet摘要、Registry或回执上下文未冻结")
    preview_contract = result_set.get("x-preview-contract")
    if not isinstance(preview_contract, dict) or preview_contract.get(
        "preview_is_view_not_completeness"
    ) is not True or preview_contract.get("preview_may_not_support_population_claims") is not True:
        _fail("result_set_preview_overclaim", "preview 视图边界未冻结")
    export = result_set.get("x-export-contract")
    if not isinstance(export, dict) or any(
        export.get(field) is not True
        for field in (
            "requires_frozen_complete_result_set",
            "llm_member_regeneration_forbidden",
            "failed_export_preserves_previous_final_artifact",
            "authorization_receipt_binds_result_set_revision_and_format",
            "manifest_binds_source_manifest_content_ordered_member_digests_and_count",
            "temporary_artifact_binds_manifest_format_byte_length_and_actual_sha256",
            "actual_bytes_must_equal_registered_format_canonical_serialization_of_ordered_members",
        )
    ) or export.get("generation_origin") != (
        "deterministic_serializer_without_llm_member_generation"
    ) or export.get("canonical_serializers") != {
        "json": "utf8_json_sort_keys_compact_separators_ensure_ascii_false",
        "csv": "utf8_sorted_columns_rfc4180_lf_with_canonical_json_non_string_scalars",
        "markdown": "utf8_sorted_columns_gfm_table_lf_with_canonical_json_non_string_scalars",
    }:
        _fail("result_set_export_boundary_open", "完整导出边界未闭合")
    incomplete_operator = result_set.get("x-incomplete-operator-contract")
    if not isinstance(incomplete_operator, dict) or incomplete_operator.get(
        "default_disposition"
    ) != "fail_closed" or incomplete_operator.get(
        "population_jaccard_coverage_and_total_relation_forbidden"
    ) is not True or incomplete_operator.get(
        "lower_bound_requires_registered_monotonicity_contract"
    ) is not True:
        _fail("incomplete_input_policy_open", "不完整 ResultSet 的 Operator 消费边界未闭合")
    tool11_query_contract = result_set.get("x-tool11-contains-asn-query-contract")
    if not isinstance(tool11_query_contract, Mapping) or any(
        tool11_query_contract.get(field) is not True
        for field in (
            "trusted_query_receipt_required",
            "pre_materialized_membership_index_required",
            "query_time_path_parsing_forbidden",
            "llm_filtering_forbidden",
            "returned_member_must_be_visible_and_have_common_path_status_ordered_or_unordered",
            "returned_typed_path_segments_must_contain_target_asn",
            "query_receipt_must_bind_profile_index_materialization_query_source_target_matched_keys_and_total",
        )
    ) or tool11_query_contract.get("source_population_unchanged") != (
        "materialized_route_state_rows_at_exact_time"
    ) or tool11_query_contract.get("profile_id") != (
        TOOL11_PATH_ASN_MEMBERSHIP_PROFILE_ID
    ) or tool11_query_contract.get("profile_digest") != (
        TOOL11_PATH_ASN_MEMBERSHIP_PROFILE_DIGEST
    ) or tool11_query_contract.get("eligible_row_predicate") != (
        TOOL11_PATH_ASN_ELIGIBLE_ROW_PREDICATE
    ) or tool11_query_contract.get("complete_matched_member_keys_digest_recipe") != (
        "sha256(canonical({member_keys: resolved_members[].member_identity in frozen result order}))"
    ) or tool11_query_contract.get("missing_or_mismatched_receipt_disposition") != (
        "reject_result_set_freeze"
    ):
        _fail("route_state_contains_asn_index_contract_open", "ResultSet未冻结TOOL-11条件查询回执闭包")
    tool12_query_contract = result_set.get("x-tool12-native-filter-query-contract")
    if (
        not isinstance(tool12_query_contract, Mapping)
        or tool12_query_contract.get("source_population_unchanged")
        != "window_path_association_evidence_rows"
        or tool12_query_contract.get("profile_id")
        != TOOL12_NATIVE_FILTER_PROFILE_ID
        or tool12_query_contract.get("profile_digest")
        != TOOL12_NATIVE_FILTER_PROFILE_DIGEST
        or any(
            tool12_query_contract.get(field) is not True
            for field in (
                "trusted_query_receipt_required",
                "pre_materialized_filter_indexes_required",
                "query_time_path_parsing_forbidden",
                "llm_filtering_forbidden",
                "contains_asn_returned_path_segments_must_contain_target_asn",
                "anchor_before_returned_row_must_match_anchor_and_verified_strict_path_order",
                "anchor_before_requires_anchor_asn_and_eligible_anchor_population",
                "anchor_population_eligibility_must_resolve_complete_same_publication_ever_affected_as_result_set",
                "known_origin_must_equal_observed_origin_and_collapsed_ordered_path_tail",
                "trusted_materialization_receipt_must_resolve_and_bind_profile_source_indexes_and_indexed_population",
                "query_receipt_must_bind_profile_indexes_materialization_query_source_targets_matched_keys_and_total",
                "op19_population_filter_receipt_digest_must_equal_result_set_query_receipt_digest",
            )
        )
        or tool12_query_contract.get("missing_or_mismatched_receipt_disposition")
        != "reject_result_set_freeze"
    ):
        _fail(
            "tool12_native_filter_contract_open",
            "ResultSet未冻结TOOL-12 Profile、anchor人口与known-origin末尾条件回执闭包",
        )
    result_text = json.dumps(result_set.get("allOf", []), ensure_ascii=False, sort_keys=True)
    if not all(
        marker in result_text
        for marker in (
            '"const": "complete"',
            '"const": "partial_page"',
            '"const": "source_incomplete"',
            '"resume_page_token"',
            '"freeze_receipt_digest"',
            '"total_count": {"const": 0}',
            '"page_manifest": {"minItems": 1}',
        )
    ):
        _fail("result_set_completeness_conflated", "ResultSet completeness 条件约束不完整")
    if page["properties"].get("page_index", {}).get("minimum") != 0:
        _fail("result_set_page_closure_open", "page_index 必须从0开始")
    _validate_runtime_validator_contract(
        result_set,
        expected_ids=(
            "result_set_page_chain_closure",
            "result_set_count_and_digest_closure",
            "source_incomplete_provenance_closure",
            "preview_source_binding_and_count_closure",
            "complete_export_eligibility",
            "complete_export_artifact_closure",
        ),
        code="result_set_instance_validator_open",
        subject="result_set",
    )

    graph_defs = _schema_defs(
        graph,
        graph_path,
        (
            "graphNode",
            "graphEdge",
            "observedFactPayload",
            "factValueProjection",
            "derivedFactPayload",
            "resultSetPayload",
            "limitationPayload",
            "unknownPayload",
            "executionFailurePayload",
            "relationNodeBinding",
            "relationOperatorBinding",
            "relationReceipt",
            "pathContainsProjection",
            "directAdjacencyProjection",
            "setIntersectionProjection",
            "setContainsProjection",
            "temporalProjection",
            "atTimeProjection",
            "conflictProjection",
        ),
    )
    graph_node = _require_closed_schema_object(
        graph_defs["graphNode"],
        code="evidence_node_population_drift",
        subject="graphNode",
        required_fields=(
            "node_id",
            "node_type",
            "identity_digest",
            "producer_ref",
            "provenance_node_ids",
            "evidence_refs",
            "payload",
            "payload_digest",
            "committed",
        ),
    )
    expected_node_types = {
        "observed_fact",
        "derived_fact",
        "result_set",
        "limitation",
        "unknown",
        "execution_failure",
    }
    if set(graph_node["properties"]["node_type"].get("enum", [])) != expected_node_types:
        _fail("evidence_node_population_drift", "Evidence Graph 节点类型人口漂移")
    graph_properties = graph.get("properties", {})
    if graph_properties.get("nodes", {}).get("items") != {"$ref": "#/$defs/graphNode"} or graph_properties.get(
        "edges", {}
    ).get("items") != {"$ref": "#/$defs/graphEdge"}:
        _fail("evidence_graph_typed_collection_open", "Evidence Graph节点或边数组未绑定冻结Schema")
    graph_condition_text = json.dumps(graph.get("allOf", []), sort_keys=True)
    node_condition_text = json.dumps(graph_node.get("allOf", []), sort_keys=True)
    if not all(
        marker in graph_condition_text
        for marker in (
            '"graph_state": {"const": "committed"}',
            '"committed": {"const": true}',
            '"closure_receipt_digest"',
            '"commit_receipt_digest"',
        )
    ) or not all(
        marker in node_condition_text
        for marker in (
            '"node_type": {"const": "observed_fact"}',
            '"payload": {"$ref": "#/$defs/observedFactPayload"}',
            '"producer_kind": {"const": "tool"}',
            '"node_type": {"const": "derived_fact"}',
            '"payload": {"$ref": "#/$defs/derivedFactPayload"}',
            '"producer_kind": {"const": "operator"}',
            '"node_type": {"const": "execution_failure"}',
        )
    ):
        _fail("evidence_payload_binding_open", "图提交状态或node_type到payload/producer绑定未闭合")
    graph_edge = _require_closed_schema_object(
        graph_defs["graphEdge"],
        code="evidence_edge_population_drift",
        subject="graphEdge",
        required_fields=(
            "edge_id",
            "edge_type",
            "from_node_id",
            "to_node_id",
            "producer_ref",
            "relation_receipt_ref",
            "edge_digest",
        ),
    )
    expected_edge_types = {
        "derived_from",
        "member_of",
        "at_time",
        "precedes",
        "same_window",
        "follows",
        "path_contains",
        "directly_adjacent_in_path",
        "set_intersects",
        "set_contains",
        "supports",
        "conflicts_with",
        "limited_by",
        "requires_external_evidence",
    }
    edge_types = set(graph_edge["properties"]["edge_type"].get("enum", []))
    forbidden_relations = {"causes", "responsible_for", "customer_of", "recovered_from"}
    if edge_types != expected_edge_types:
        _fail("evidence_edge_population_drift", "Evidence Graph 边类型人口漂移")
    if forbidden_relations & edge_types or set(graph.get("x-forbidden-fact-relations", [])) != forbidden_relations:
        _fail("forbidden_fact_relation_open", "事实图禁止的因果、责任、商业或恢复关系未闭合")
    graph_closure = graph.get("x-graph-closure-contract")
    if not isinstance(graph_closure, dict) or any(
        graph_closure.get(field) is not True
        for field in (
            "node_and_edge_ids_unique",
            "root_and_edge_endpoints_must_exist",
            "all_committed_fact_nodes_share_identity_and_registry_binding",
            "graph_identity_digest_is_data_digest_projection_of_plan_identity_binding",
            "derived_from_subgraph_acyclic",
            "derived_fact_provenance_matches_incoming_edges_and_operator_input_digests",
            "execution_failure_unknown_and_preview_cannot_support_population_facts",
            "uncommitted_nodes_cannot_be_referenced",
        )
    ) or graph_closure.get("member_of_target_type") != "result_set" or graph_closure.get(
        "limited_by_target_type"
    ) != "limitation" or graph_closure.get("requires_external_evidence_target_type") != "unknown":
        _fail("evidence_graph_closure_open", "Evidence Graph 引用、身份或来源闭包未冻结")
    graph_digest_contract = graph.get("x-digest-recomputation-contract")
    graph_runtime_context = graph.get("x-runtime-context-contract")
    if not isinstance(graph_digest_contract, dict) or not {
        "node_payload_digest",
        "edge_digest",
        "graph_digest",
        "receipt_digest",
    }.issubset(graph_digest_contract) or not isinstance(
        graph_runtime_context, dict
    ) or graph_runtime_context.get("registry_view_source") != "trusted_registry_store_lookup_only" or graph_runtime_context.get(
        "trusted_store_contract_id"
    ) != "country_outage_p2_trusted_registry_store_v1" or any(
        graph_runtime_context.get(field) is not True
        for field in (
            "trusted_store_host_attestation_required",
            "registry_entries_never_accepted_from_request_payload",
        )
    ) or graph_runtime_context.get(
        "plan_definition_and_investigation_snapshot_required"
    ) is not True or graph_runtime_context.get(
        "producer_relation_closure_and_commit_receipts_resolve_and_recompute"
    ) is not True or graph_runtime_context.get(
        "observed_fact_result_set_member_freeze_and_projection_receipts_resolve"
    ) is not True or any(
        graph_runtime_context.get(field) is not True
        for field in (
            "trusted_revalidation_context_uses_sorted_json_safe_result_set_records",
            "trusted_revalidation_context_includes_sorted_result_set_member_records",
            "tuple_keyed_runtime_maps_are_rebuilt_only_after_context_digest_validation",
        )
    ) or graph_runtime_context.get(
        "operator_output_schema_resolves_from_frozen_operator_contract"
    ) is not True:
        _fail("evidence_graph_digest_contract_open", "Evidence Graph摘要或跨对象上下文未冻结")
    relation = graph.get("x-relation-semantic-contract")
    if not isinstance(relation, dict) or any(
        relation.get(field) is not True
        for field in (
            "directly_adjacent_in_path_requires_exact_op16_neighbor_member_reference",
            "path_contains_is_not_adjacency",
            "concurrent_state_is_not_path_at_time",
            "as_owned_prefix_population_must_not_equal_path_association_prefix_population",
            "temporal_edges_require_registered_operator_receipt",
            "operator_output_artifact_must_be_structured_and_registry_admitted",
            "operator_output_envelope_must_validate_against_registered_operator_output_schema",
            "operator_output_digest_must_recompute_from_full_output_envelope_without_output_digest",
            "relation_projection_must_equal_registered_operator_result_field_projection",
            "operator_edge_projection_must_embed_same_typed_relation_projection_preimage",
            "operator_edge_projection_digest_must_equal_relation_receipt_projection_digest",
            "relation_node_bindings_separate_node_payload_digest_and_domain_value_digest",
            "typed_asn_relation_endpoints_must_equal_node_fact_value_projection_and_operator_edge_projection",
            "operator_output_digest_is_bound_by_operator_binding_not_duplicated_inside_projection",
            "set_contains_is_reverse_edge_of_op27_left_fully_covered_by_right",
            "host_may_validate_receipt_mapping_but_may_not_recompute_path_time_or_set_semantics",
        )
    ) or relation.get("typed_receipt_schema_version") != (
        "country_outage_p2_relation_receipt_v1"
    ) or relation.get("typed_asn_value_schema_ref_const") != (
        "https://domeye.example/types/asn.json"
    ) or relation.get("edge_operator_mapping") != {
        "path_contains": ["OP-15"],
        "directly_adjacent_in_path": ["OP-16"],
        "precedes": ["OP-29"],
        "same_window": ["OP-29"],
        "follows": ["OP-29"],
        "conflicts_with": ["OP-37"],
        "set_intersects": ["OP-25"],
        "set_contains": ["OP-27"],
        "at_time": ["OP-33"],
    } or set(relation.get("typed_relation_receipt_required_fields", [])) != {
        "receipt_schema_version",
        "receipt_kind",
        "relation_type",
        "from_node_binding",
        "to_node_binding",
        "identity_digest",
        "registry_snapshot_id",
        "registry_snapshot_digest",
        "operator_binding",
        "projection",
        "projection_digest",
        "disposition",
        "receipt_digest",
    }:
        _fail("path_relation_semantic_open", "路径、时间或前缀人口关系语义未闭合")
    _require_closed_schema_object(
        graph_defs["relationReceipt"],
        code="evidence_relation_receipt_schema_open",
        subject="relationReceipt",
        required_fields=relation.get("typed_relation_receipt_required_fields", []),
    )
    knowledge = graph.get("x-knowledge-boundary")
    if not isinstance(knowledge, dict) or any(
        knowledge.get(field) is not True
        for field in (
            "world_knowledge_fact_node_forbidden",
            "teacher_reference_fact_source_forbidden",
            "reasoning_and_hypothesis_layer_is_external_to_fact_graph",
        )
    ):
        _fail("world_knowledge_fact_boundary_open", "世界知识或TeacherReference不得成为事件事实来源")
    failure_payload = graph_defs["executionFailurePayload"]
    if failure_payload.get("properties", {}).get("publishable_fact_output", {}).get("const") is not False:
        _fail("evidence_graph_closure_open", "执行失败不得产生可发布事实")
    _validate_runtime_validator_contract(
        graph,
        expected_ids=(
            "evidence_graph_reference_and_identity_closure",
            "evidence_graph_provenance_closure",
            "evidence_graph_support_eligibility",
            "evidence_graph_relation_semantics",
        ),
        code="evidence_graph_instance_validator_open",
        subject="evidence_graph",
    )

    model_defs = _schema_defs(
        model_flow,
        model_path,
        (
            "sharedAnswerBinding",
            "teacherModelIdentity",
            "studentModelIdentity",
            "modelRunReceipt",
            "teacherPlanRunReceipt",
            "teacherPlanGroundingReceipt",
            "teacherRunReceipt",
            "completedTeacherRunReceipt",
            "studentModelRunReceipt",
            "teacherReference",
            "teacherOracleCoverageReceipt",
            "validationReceipt",
            "passedValidationReceipt",
            "rejectedValidationReceipt",
            "passedGateResult",
            "studentRun",
            "studentAnswerArtifactReference",
            "studentAnswerPayload",
            "completedStudentRun",
            "degradedStudentRun",
            "structuredFeedback",
            "alignmentRunReceipt",
            "passedAlignmentRunReceipt",
            "passedHardGateMetrics",
            "degradedAuthorization",
            "publishedAnswer",
        ),
    )
    expected_order = [
        "gpt-5.6-sol",
        "teacher_reference_validator",
        "ds_student",
        "student_answer_validator",
        "alignment_evaluator",
    ]
    model_properties = model_flow.get("properties", {})
    if model_properties.get("execution_order", {}).get("const") != expected_order or model_properties.get(
        "default_teacher_required", {}
    ).get("const") is not True:
        _fail("dual_model_order_drift", "回答链必须默认 teacher_required 且先Sol后DS")
    teacher_identity_text = json.dumps(model_defs["teacherModelIdentity"], sort_keys=True)
    if '"const": "gpt-5.6-sol"' not in teacher_identity_text or '"const": "openai"' not in teacher_identity_text:
        _fail("dual_model_order_drift", "Teacher身份必须绑定gpt-5.6-sol")
    shared = _require_closed_schema_object(
        model_defs["sharedAnswerBinding"],
        code="shared_binding_contract_open",
        subject="sharedAnswerBinding",
        required_fields=(
            "question_digest",
            "publication_digest",
            "cohort_digest",
            "data_through_utc",
            "finality",
            "binding_generation",
            "grounding_plan_digest",
            "teacher_semantic_plan_digest",
            "teacher_plan_grounding_receipt_digest",
            "plan_id",
            "plan_revision",
            "investigation_plan_digest",
            "evidence_bundle_digest",
            "evidence_graph_revision",
            "evidence_graph_digest",
            "registry_snapshot_digest",
            "boundary_policy_digest",
            "world_knowledge_bundle_digest",
            "world_knowledge_policy",
            "prompt_digest",
            "policy_digest",
        ),
    )
    if shared["properties"]["world_knowledge_policy"].get("const") != "explanation_and_hypothesis_only_not_event_evidence":
        _fail("world_knowledge_fact_boundary_open", "世界知识只能用于解释和假设")
    if not frozen_identity_fields.issubset(set(shared["required"])) or set(
        shared["properties"]["finality"].get("enum", [])
    ) != frozen_finality:
        _fail("cross_stage_identity_contract_drift", "双模型共享绑定未继承完整S1D-2身份")
    if model_properties.get("teacher_plan_run_receipt", {}).get("oneOf", [None])[0] != {
        "$ref": "#/$defs/teacherPlanRunReceipt"
    } or model_properties.get("teacher_plan_grounding_receipt", {}).get("oneOf", [None])[0] != {
        "$ref": "#/$defs/teacherPlanGroundingReceipt"
    } or model_properties.get("teacher_run_receipt", {}).get("oneOf", [None])[0] != {
        "$ref": "#/$defs/teacherRunReceipt"
    } or model_properties.get("teacher_oracle_coverage_receipt", {}).get("oneOf", [None])[0] != {
        "$ref": "#/$defs/teacherOracleCoverageReceipt"
    } or model_properties.get("student_runs", {}).get("items") != {
        "$ref": "#/$defs/studentRun"
    }:
        _fail("dual_model_role_binding_open", "Teacher或Student运行回执未绑定角色Schema")
    teacher_run_text = json.dumps(model_defs["teacherRunReceipt"], sort_keys=True)
    student_run_receipt_text = json.dumps(model_defs["studentModelRunReceipt"], sort_keys=True)
    if '"role": {"const": "teacher"}' not in teacher_run_text or "teacherModelIdentity" not in teacher_run_text or '"role": {"const": "student"}' not in student_run_receipt_text or "studentModelIdentity" not in student_run_receipt_text:
        _fail("dual_model_role_binding_open", "模型运行角色与精确模型身份未闭合")
    binding = model_flow.get("x-shared-binding-equality-contract")
    if not isinstance(binding, dict) or any(
        binding.get(field) is not True
        for field in (
            "teacher_reference_student_runs_alignment_and_published_answer_must_equal_root_shared_binding_digest",
            "teacher_and_student_must_share_grounding_plan_evidence_bundle_evidence_graph_registry_and_boundary_policy",
            "world_knowledge_bundle_if_present_must_be_shared_and_explanation_only",
        )
    ) or binding.get("student_only_additional_inputs") != [
        "validated_teacher_reference",
        "teacher_reference_validation_receipt",
        "teacher_oracle_coverage_receipt",
    ] or binding.get("student_additional_inputs_may_change_shared_binding") is not False:
        _fail("shared_binding_contract_open", "Sol/DS同绑定与同证据合同未闭合")
    dual_digest_contract = model_flow.get("x-digest-recomputation-contract")
    dual_runtime_context = model_flow.get("x-runtime-context-contract")
    if not isinstance(dual_digest_contract, dict) or not {
        "shared_answer_binding_digest",
        "teacher_reference_output_digest",
        "validation_receipt_digest",
        "alignment_receipt_digest",
        "published_answer_digest",
        "teacher_oracle_coverage_receipt_digest",
        "teacher_plan_grounding_receipt_digest",
        "teacher_plan_role_specific_input_digest",
        "teacher_reference_role_specific_input_digest",
        "student_role_specific_input_digest",
        "student_answer_digest",
        "student_answer_artifact_receipt_digest",
        "publish_receipt_digest",
    }.issubset(dual_digest_contract) or not isinstance(
        dual_runtime_context, dict
    ) or dual_runtime_context.get("published_answer_requires_host_trusted_validated_committed_evidence_graph") is not True or dual_runtime_context.get(
        "nonterminal_or_rejected_dispositions_cannot_publish"
    ) is not True or any(
        dual_runtime_context.get(field) is not True
        for field in (
            "shared_binding_identity_projection_must_equal_evidence_graph_identity_digest",
            "shared_binding_plan_graph_revision_digest_and_registry_must_equal_evidence_graph",
            "teacher_reference_fact_ids_and_evidence_refs_must_be_closed_by_graph",
            "flow_revision_parent_must_share_flow_id",
            "final_disposition_and_flow_state_are_bidirectionally_closed",
            "degraded_plan_resolves_from_host_trusted_validated_plan_store",
            "caller_supplied_plan_or_graph_attestation_forbidden",
            "teacher_oracle_resolves_from_host_trusted_content_addressed_store",
            "caller_supplied_or_mutated_oracle_record_forbidden",
            "trusted_plan_record_must_pass_full_investigation_plan_instance_validator_with_bound_context",
            "trusted_graph_record_must_pass_full_evidence_graph_instance_validator_with_bound_context",
        )
    ) or dual_runtime_context.get("shared_binding_time_order") != (
        "window_start_utc <= window_end_utc <= data_through_utc"
    ):
        _fail("dual_model_digest_contract_open", "双模型摘要、Evidence或发布上下文未冻结")
    trusted_stores = model_flow.get("x-trusted-runtime-store-contracts")
    expected_store_bindings = {
        "validated_plan_store": {
            "store_contract_id": "country_outage_p2_trusted_validated_plan_store_v1",
            "trust_origin": "host_authenticated_runtime_store",
            "caller_mutable": False,
            "attestation_provider_id": "country_outage_p2_validated_plan_store_host",
            "attestation_contract_digest": VALIDATED_PLAN_STORE_ATTESTATION_CONTRACT_DIGEST,
            "record_fields": ["plan", "validation_receipt", "validation_context"],
            "validation_context_fields": [
                "trusted_registry_store",
                "trusted_admission_receipt_store",
                "trusted_node_result_receipt_store",
                "parameter_bindings",
                "previous_plan_definition",
                "previous_investigation_snapshot",
            ],
            "receipt_validator_id": VALIDATED_PLAN_VALIDATOR_ID,
            "receipt_validator_version": VALIDATED_PLAN_VALIDATOR_VERSION,
            "receipt_validator_contract_digest": VALIDATED_PLAN_VALIDATOR_CONTRACT_DIGEST,
            "receipt_validator_implementation_digest": VALIDATED_PLAN_VALIDATOR_IMPLEMENTATION_DIGEST,
        },
        "committed_graph_store": {
            "store_contract_id": "country_outage_p2_trusted_committed_graph_store_v1",
            "trust_origin": "host_authenticated_runtime_store",
            "caller_mutable": False,
            "attestation_provider_id": "country_outage_p2_committed_graph_store_host",
            "attestation_contract_digest": COMMITTED_GRAPH_STORE_ATTESTATION_CONTRACT_DIGEST,
            "record_fields": ["graph", "validation_receipt", "validation_context"],
            "validation_context_fields": [
                "schema",
                "plan_definition",
                "investigation_snapshot",
                "trusted_registry_store",
                "result_set_records",
                "result_set_member_records",
                "receipt_store",
                "operator_contract_schema",
                "previous_graph",
            ],
            "receipt_validator_id": COMMITTED_GRAPH_VALIDATOR_ID,
            "receipt_validator_version": COMMITTED_GRAPH_VALIDATOR_VERSION,
            "receipt_validator_contract_digest": COMMITTED_GRAPH_VALIDATOR_CONTRACT_DIGEST,
            "receipt_validator_implementation_digest": COMMITTED_GRAPH_VALIDATOR_IMPLEMENTATION_DIGEST,
        },
        "oracle_store": {
            "store_contract_id": "country_outage_p2_trusted_oracle_store_v1",
            "trust_origin": "host_authenticated_runtime_store",
            "caller_mutable": False,
            "attestation_provider_id": "country_outage_p2_oracle_store_host",
            "attestation_contract_digest": ORACLE_STORE_ATTESTATION_CONTRACT_DIGEST,
            "record_fields": [
                "question_id",
                "required_fact_ids",
                "required_boundary_assertion_ids",
                "allowed_boundary_assertion_ids",
                "required_unknown_ids",
                "prohibited_assertion_ids",
                "oracle_digest",
            ],
            "record_digest_recipe": "sha256(canonical(record excluding oracle_digest))",
            "store_resolution_key": "oracle_digest",
            "question_binding_required": True,
            "request_payload_may_not_supply_or_mutate_records": True,
            "host_attestation_required": True,
        },
        "student_answer_artifact_store": {
            "store_contract_id": "country_outage_p2_trusted_student_answer_artifact_store_v1",
            "trust_origin": "host_authenticated_runtime_store",
            "caller_mutable": False,
            "attestation_provider_id": "country_outage_p2_student_answer_artifact_store_host",
            "attestation_contract_digest": STUDENT_ANSWER_ARTIFACT_STORE_ATTESTATION_CONTRACT_DIGEST,
            "record_fields": [
                "artifact_ref",
                "artifact_schema_ref",
                "answer_payload",
                "answer_digest",
                "artifact_receipt_digest",
            ],
            "record_digest_recipe": "answer_digest=sha256(canonical(answer_payload)); artifact_receipt_digest=sha256(canonical(record excluding artifact_receipt_digest))",
            "store_resolution_key": "artifact_ref",
            "request_payload_record_must_equal_host_record": True,
            "host_attestation_required": True,
        },
        "alignment_receipt_store": {
            "store_contract_id": "country_outage_p2_trusted_alignment_receipt_store_v1",
            "trust_origin": "host_authenticated_runtime_store",
            "caller_mutable": False,
            "attestation_provider_id": "country_outage_p2_alignment_receipt_store_host",
            "attestation_contract_digest": ALIGNMENT_RECEIPT_STORE_ATTESTATION_CONTRACT_DIGEST,
            "record_schema_ref": "#/$defs/alignmentRunReceipt",
            "store_resolution_key": "receipt_digest",
            "request_payload_record_must_equal_host_record": True,
            "host_attestation_required": True,
        },
    }
    if trusted_stores != expected_store_bindings:
        _fail("dual_model_trusted_store_contract_open", "双模型受信计划与提交图存储合同未冻结")
    teacher_reference = _require_closed_schema_object(
        model_defs["teacherReference"],
        code="teacher_truth_conflation",
        subject="teacherReference",
        required_fields=(
            "shared_answer_binding_digest",
            "required_fact_ids",
            "evidence_refs",
            "boundary_assertions",
            "unknowns",
            "teacher_reference_is_ground_truth",
            "private_chain_of_thought_persisted",
        ),
    )
    if teacher_reference["properties"]["teacher_reference_is_ground_truth"].get("const") is not False or teacher_reference[
        "properties"
    ]["private_chain_of_thought_persisted"].get("const") is not False:
        _fail("teacher_truth_conflation", "TeacherReference不是ground truth且不得保存私有思维链")
    teacher_contract = model_flow.get("x-teacher-validation-contract")
    if not isinstance(teacher_contract, dict) or any(
        teacher_contract.get(field) is not expected
        for field, expected in (
            ("evidence_truth_precedes_teacher", True),
            ("teacher_reference_not_ground_truth", True),
            ("invalid_teacher_reference_forwarded_to_student", False),
            ("teacher_reference_requires_passed_oracle_coverage_receipt_before_student", True),
            ("missing_evidence_invalid_reference_causality_customer_relation_and_recovery_claims_fail_closed", True),
            ("private_chain_of_thought_required_or_persisted", False),
        )
    ):
        _fail("teacher_truth_conflation", "TeacherReference Validator失败关闭规则未闭合")
    flow_text = json.dumps(model_flow.get("allOf", []), ensure_ascii=False, sort_keys=True)
    if not all(
        marker in flow_text
        for marker in (
            '"const": "teacher_rejected"',
            '"student_runs": {"maxItems": 0}',
            "rejectedValidationReceipt",
            "completedTeacherRunReceipt",
            '"revision_ordinal": {"const": 0}',
            '"revision_ordinal": {"const": 1}',
            "completedStudentRun",
            "degradedStudentRun",
            '"aligned_claim": {"const": true}',
            '"aligned_claim": {"const": false}',
        )
    ):
        _fail("teacher_rejected_forwarding_open", "TeacherReference拒绝后不得启动DS")
    teacher_rejected_state_branch = next(
        (
            item
            for item in model_flow.get("allOf", [])
            if item.get("if", {})
            .get("properties", {})
            .get("flow_state", {})
            .get("const")
            == "teacher_rejected"
        ),
        None,
    )
    teacher_rejected_disposition_branch = next(
        (
            item
            for item in model_flow.get("allOf", [])
            if item.get("if", {})
            .get("properties", {})
            .get("final_disposition", {})
            .get("const")
            == "teacher_rejected"
        ),
        None,
    )
    if (
        not isinstance(teacher_rejected_state_branch, Mapping)
        or teacher_rejected_state_branch.get("then", {})
        .get("properties", {})
        .get("student_runs", {})
        .get("maxItems")
        != 0
        or not isinstance(teacher_rejected_disposition_branch, Mapping)
        or teacher_rejected_disposition_branch.get("then", {})
        .get("properties", {})
        .get("flow_state", {})
        .get("const")
        != "teacher_rejected"
    ):
        _fail("teacher_rejected_forwarding_open", "Teacher拒绝状态与disposition必须双向闭合且不得启动DS")
    terminal_state_map = {
        "aligned_published": "published",
        "ds_unaligned_degraded": "degraded_published",
        "teacher_unavailable": "stopped_waiting_teacher",
        "teacher_rejected": "teacher_rejected",
        "student_rejected": "failed",
        "alignment_rejected": "alignment_failed",
    }
    for final_disposition, expected_state in terminal_state_map.items():
        branch = next(
            (
                item
                for item in model_flow.get("allOf", [])
                if item.get("if", {})
                .get("properties", {})
                .get("final_disposition", {})
                .get("const")
                == final_disposition
            ),
            None,
        )
        if (
            not isinstance(branch, Mapping)
            or branch.get("then", {})
            .get("properties", {})
            .get("flow_state", {})
            .get("const")
            != expected_state
        ):
            _fail("dual_model_terminal_state_matrix_open", "DualModel终态与disposition没有双向闭合")
    terminal_branches = {
        item.get("if", {})
        .get("properties", {})
        .get("final_disposition", {})
        .get("const"): item.get("then", {}).get("properties", {})
        for item in model_flow.get("allOf", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("if"), Mapping)
        and isinstance(item.get("then"), Mapping)
    }
    unavailable_properties = terminal_branches.get("teacher_unavailable", {})
    if (
        "teacher_unavailable_phase" not in model_flow.get("required", [])
        or model_properties.get("teacher_unavailable_phase", {}).get("enum")
        != ["none", "sol_reference"]
        or unavailable_properties.get("teacher_unavailable_phase", {}).get("const")
        != "sol_reference"
        or unavailable_properties.get("teacher_plan_run_receipt", {}).get("$ref")
        != "#/$defs/completedTeacherPlanRunReceipt"
        or unavailable_properties.get("teacher_plan_grounding_receipt", {}).get("$ref")
        != "#/$defs/teacherPlanGroundingReceipt"
        or unavailable_properties.get("teacher_run_receipt", {}).get("$ref")
        != "#/$defs/unavailableTeacherRunReceipt"
        or any(
            unavailable_properties.get(field, {}).get("type") != "null"
            for field in (
                "teacher_reference",
                "teacher_validation_receipt",
                "teacher_oracle_coverage_receipt",
                "structured_feedback",
                "student_validation_receipt",
                "alignment_run_receipt",
                "degraded_authorization",
                "published_answer",
                "publish_receipt_digest",
            )
        )
        or unavailable_properties.get("student_runs", {}).get("maxItems") != 0
    ):
        _fail("dual_model_terminal_state_matrix_open", "Teacher不可用阶段或终态字段未穷尽闭合")
    for disposition, required_refs in {
        "student_rejected": {
            "teacher_plan_run_receipt": "#/$defs/completedTeacherPlanRunReceipt",
            "teacher_plan_grounding_receipt": "#/$defs/teacherPlanGroundingReceipt",
            "teacher_run_receipt": "#/$defs/completedTeacherRunReceipt",
            "teacher_validation_receipt": "#/$defs/passedValidationReceipt",
            "teacher_oracle_coverage_receipt": "#/$defs/passedTeacherOracleCoverageReceipt",
            "student_validation_receipt": "#/$defs/rejectedValidationReceipt",
        },
        "alignment_rejected": {
            "teacher_plan_run_receipt": "#/$defs/completedTeacherPlanRunReceipt",
            "teacher_plan_grounding_receipt": "#/$defs/teacherPlanGroundingReceipt",
            "teacher_run_receipt": "#/$defs/completedTeacherRunReceipt",
            "teacher_validation_receipt": "#/$defs/passedValidationReceipt",
            "teacher_oracle_coverage_receipt": "#/$defs/passedTeacherOracleCoverageReceipt",
            "student_validation_receipt": "#/$defs/passedValidationReceipt",
            "alignment_run_receipt": "#/$defs/rejectedAlignmentRunReceipt",
        },
    }.items():
        properties = terminal_branches.get(disposition, {})
        if any(
            properties.get(field, {}).get("$ref") != schema_ref
            for field, schema_ref in required_refs.items()
        ) or any(
            properties.get(field, {}).get("type") != "null"
            for field in ("published_answer", "publish_receipt_digest")
        ):
            _fail("dual_model_terminal_state_matrix_open", f"{disposition}终态字段未穷尽闭合")
    student_runs = model_properties.get("student_runs", {})
    feedback = model_defs["structuredFeedback"]
    feedback_contract = model_flow.get("x-feedback-contract")
    if student_runs.get("maxItems") != 2 or feedback.get("properties", {}).get("feedback_round", {}).get(
        "const"
    ) != 1 or not isinstance(feedback_contract, dict) or feedback_contract.get(
        "structured_feedback_max"
    ) != 1 or feedback.get("properties", {}).get("producer_kind", {}).get(
        "const"
    ) != "host_deterministic_alignment_evaluator" or feedback_contract.get(
        "feedback_owner"
    ) != "host_deterministic_alignment_evaluator" or feedback_contract.get(
        "additional_sol_feedback_call_allowed"
    ) is not False or feedback_contract.get("sol_call_budget") != {
        "planning": 1,
        "reference": 1,
        "feedback": 0,
        "maximum_total": 2,
    } or feedback_contract.get("student_revision_max") != 1 or feedback_contract.get(
        "revision_requires_all_five_gates"
    ) is not True or feedback_contract.get("online_weight_prompt_or_policy_mutation_forbidden") is not True:
        _fail("student_revision_limit_open", "结构化反馈与DS修订上限未冻结")
    student_run = model_defs["studentRun"]
    if student_run.get("properties", {}).get("may_call_tools", {}).get("const") is not False or student_run.get(
        "properties", {}
    ).get("may_add_event_facts", {}).get("const") is not False:
        _fail("shared_binding_contract_open", "DS不得调用Tool或增加事件事实")
    student_required = set(student_run.get("required", []))
    answer_artifact = model_defs["studentAnswerArtifactReference"]
    if (
        "teacher_oracle_coverage_receipt_digest" not in student_required
        or "student_answer_artifact" not in student_required
        or answer_artifact.get("additionalProperties") is not False
        or not {
            "artifact_ref",
            "artifact_schema_ref",
            "answer_payload",
            "answer_digest",
            "artifact_receipt_digest",
        }.issubset(set(answer_artifact.get("required", [])))
    ):
        _fail("dual_model_role_binding_open", "DS输入覆盖回执或可重放回答Artifact未闭合")
    student_identity_text = json.dumps(model_defs["studentModelIdentity"], sort_keys=True)
    if any(
        marker not in student_identity_text
        for marker in (
            '"const": "deepseek"',
            '"const": "deepseek-v4-flash"',
            '"const": "deepseek-v4-flash-pi-0.84.1-v1"',
            '"const": "0.84.1"',
            '"const": "ac00eeb087bc9651fd27391066d9d16a416aad887cb552737696289ded3ce2b5"',
            '"const": "e8881aa2b79f495da3ea551bb3b2423af45c118f5e622ac1877852bf0087bf4f"',
        )
    ):
        _fail("ds_model_identity_mismatch", "DualModel Schema未冻结S1D-5 DS响应模型、候选资源与评测Profile")
    alignment = model_flow.get("x-alignment-contract")
    if not isinstance(alignment, dict) or set(alignment.get("hard_gate_metrics", [])) != {
        "fact_precision",
        "evidence_ref_precision",
        "boundary_compliance",
    } or alignment.get("teacher_is_truth_oracle") is not False or alignment.get(
        "text_similarity_is_advisory_only"
    ) is not True or alignment.get("text_similarity_may_override_hard_gate") is not False:
        _fail("alignment_hard_gate_open", "Alignment硬门不得被文本相似度替代")
    degraded = model_flow.get("x-degraded-contract")
    if not isinstance(degraded, dict) or any(
        degraded.get(field) is not True
        for field in (
            "silent_degrade_forbidden",
            "explicit_user_authorization_required",
            "new_plan_revision_required",
            "degraded_teacher_artifacts_must_be_null",
            "degraded_authorization_digest_must_recompute",
            "degraded_plan_must_be_full_schema_valid_and_host_store_validated",
            "degraded_plan_policy_must_bind_authorization_digest_and_teacher_required_false",
            "new_plan_revision_and_digest_must_equal_committed_evidence_graph",
        )
    ) or degraded.get("teacher_unavailable_default") != "stopped_waiting_teacher" or degraded.get(
        "degraded_answer_may_claim_sol_ds_alignment"
    ) is not False:
        _fail("silent_degrade_open", "Sol不可用时不得静默降级")
    validation_receipt = model_defs["validationReceipt"]
    gate_results = validation_receipt.get("properties", {}).get("gate_results", {})
    gate_contract_text = json.dumps(gate_results, sort_keys=True)
    passed_validation_text = json.dumps(model_defs["passedValidationReceipt"], sort_keys=True)
    passed_alignment_text = json.dumps(model_defs["passedAlignmentRunReceipt"], sort_keys=True)
    if gate_results.get("minItems") != 5 or gate_results.get("maxItems") != 5 or any(
        f'"const": "GATE-0{index}"' not in gate_contract_text for index in range(1, 6)
    ) or "passedGateResult" not in passed_validation_text:
        _fail("validation_gate_population_open", "五个Gate必须恰好各一次且passed回执要求全部通过")
    if "passedHardGateMetrics" not in passed_alignment_text or any(
        model_defs["passedHardGateMetrics"].get("allOf", [])[1].get("properties", {}).get(metric, {}).get("const") != 1
        for metric in ("fact_precision", "evidence_ref_precision", "boundary_compliance")
    ):
        _fail("alignment_hard_gate_open", "passed Alignment必须满足冻结硬门阈值")
    _validate_runtime_validator_contract(
        model_flow,
        expected_ids=(
            "dual_model_shared_binding_equality",
            "dual_model_role_output_and_validation_binding",
            "dual_model_state_and_revision_closure",
            "degraded_authorization_plan_binding",
        ),
        code="dual_model_instance_validator_open",
        subject="dual_model_answer_flow",
    )

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
    envelope = payload.get("transaction_envelope")
    expected_envelope_fields = {
        "transaction_id",
        "consistency_kind",
        "idempotency_key",
        "request_digest",
        "parent_revision",
        "parent_digest",
        "expected_current_digest",
        "prepared_artifact_refs",
        "prepared_artifact_digests",
        "validation_receipts",
        "commit_point",
        "commit_marker",
        "commit_receipt_digest",
        "committed_revision",
        "committed_digest",
        "recovery_action",
        "recovery_receipt_digest",
        "conflict_kind",
        "outcome_digest",
        "disposition",
    }
    if not isinstance(envelope, dict) or set(envelope.get("required_fields", [])) != expected_envelope_fields or envelope.get(
        "closed_object_required"
    ) is not True or envelope.get("current_pointer_update") != "compare_and_swap_expected_current_digest" or envelope.get(
        "same_idempotency_key_same_request_digest_same_outcome"
    ) is not True or envelope.get("same_idempotency_key_different_request_digest_disposition") != "reject_conflict":
        _fail("commit_envelope_open", "通用提交信封、CAS或幂等规则未闭合")
    if any(
        envelope.get(field) is not True
        for field in (
            "normal_commit_requires_expected_current_digest_equal_live_pointer",
            "compare_and_swap_rejected_conflict_requires_attempted_expected_digest_different_from_live_pointer",
            "compare_and_swap_conflict_recovery_receipt_preserves_live_pointer",
        )
    ):
        _fail("commit_envelope_open", "CAS成功与真实冲突条件未分别冻结")
    transaction_schema = payload.get("transaction_record_schema")
    _validate_schema_file(transaction_schema, consistency_path)
    transaction_required = expected_envelope_fields | {"transaction_state"}
    transaction = _require_closed_schema_object(
        transaction_schema,
        code="commit_transaction_schema_open",
        subject="transaction_record_schema",
        required_fields=transaction_required,
    )
    gate_receipt_schema = transaction.get("properties", {}).get(
        "validation_receipts", {}
    ).get("items", {})
    _require_closed_schema_object(
        gate_receipt_schema,
        code="commit_gate_receipt_schema_open",
        subject="transaction_validation_receipt",
        required_fields=(
            "gate_id",
            "gate_version",
            "gate_contract_digest",
            "validator_id",
            "implementation_digest",
            "output_schema_ref",
            "transaction_id",
            "request_digest",
            "binding_digest",
            "subject_bindings",
            "gate_output",
            "output_digest",
            "passed",
            "failure_code",
            "receipt_digest",
        ),
    )
    transaction_text = json.dumps(transaction, sort_keys=True)
    if not all(
        marker in transaction_text
        for marker in (
            '"transaction_state": {"const": "committed"}',
            '"commit_marker": {"minLength": 1, "type": "string"}',
            '"committed_digest"',
            '"prepared"',
            '"aborted"',
            '"request_digest"',
            '"same_key_different_request_digest_rejected_conflict": true',
            '"committed_revision_is_parent_plus_one_or_genesis_one": true',
            '"consistency_kind_binds_atomic_write_set_validation_gates_commit_point_and_recovery": true',
        )
    ):
        _fail("commit_transaction_schema_open", "事务实例状态、摘要或幂等冲突条件未闭合")
    transaction_digest_contract = payload.get("transaction_digest_recomputation_contract")
    if not isinstance(transaction_digest_contract, dict) or set(
        transaction_digest_contract
    ) != {
        "canonical_json",
        "idempotency_key",
        "request_digest",
        "scope_digest",
        "prepared_artifact_digest",
        "validation_receipt_digest",
        "committed_digest",
        "commit_marker",
        "outcome_digest",
    }:
        _fail("commit_transaction_digest_contract_open", "事务摘要与commit marker配方未冻结")
    request_schema = payload.get("transaction_request_schema")
    artifact_schema = payload.get("prepared_artifact_envelope_schema")
    _validate_schema_file(request_schema, consistency_path)
    _validate_schema_file(artifact_schema, consistency_path)
    _require_closed_schema_object(
        artifact_schema,
        code="commit_artifact_schema_open",
        subject="prepared_artifact_envelope_schema",
        required_fields=(
            "artifact_contract_id",
            "artifact_role",
            "artifact_ref",
            "artifact_schema_ref",
            "artifact_revision",
            "binding_digest",
            "request_digest",
            "scope_digest",
            "payload",
            "payload_digest",
            "prepare_receipt",
            "visibility_state",
        ),
    )
    typed_receipts = payload.get("typed_receipt_contract")
    role_contract_root = payload.get("prepared_artifact_role_contracts")
    role_contracts = (
        role_contract_root.get("by_consistency_kind", {})
        if isinstance(role_contract_root, Mapping)
        else {}
    )
    gate_registry = payload.get("trusted_gate_validator_registry")
    trusted_resolution = payload.get("trusted_runtime_resolution_contract")
    state_matrix = payload.get("state_disposition_recovery_matrix")
    if (
        not isinstance(typed_receipts, dict)
        or typed_receipts.get("artifact_schema_ref_recipe")
        != "https://domeye.example/contracts/runtime-artifacts/{consistency_kind}/{artifact_role}.schema.json"
        or typed_receipts.get("gate_version") != "1.0.0"
        or typed_receipts.get("gate_validator_id_recipe")
        != "HOST-GATE::{consistency_kind}::{gate_id}"
        or typed_receipts.get("commit_receipt_required_for_committed") is not True
        or typed_receipts.get("recovery_receipt_required_for_recovered") is not True
        or typed_receipts.get(
            "gate_implementation_digest_and_output_schema_ref_required"
        )
        is not True
        or typed_receipts.get("gate_output_digest_must_bind_typed_gate_output")
        is not True
        or not isinstance(gate_registry, Mapping)
        or gate_registry.get("source") != "host_trusted_static_contract"
        or gate_registry.get("caller_supplied_validator_identity_forbidden")
        is not True
        or gate_registry.get("expected_gate_entry_count") != 25
        or not isinstance(trusted_resolution, Mapping)
        or any(
            trusted_resolution.get(field) is not True
            for field in (
                "caller_supplied_request_artifact_or_gate_objects_forbidden",
                "prepared_payload_schema_resolves_from_prepared_artifact_role_contracts",
                "gate_identity_resolves_from_trusted_gate_validator_registry",
                "gate_output_digest_and_subject_bindings_must_recompute",
                "host_validates_schema_identity_digest_receipt_cas_and_visibility_only",
                "host_recomputation_of_tool_or_operator_business_semantics_forbidden",
            )
        )
        or trusted_resolution.get("transaction_request_source")
        != "host_trusted_transaction_request_store"
        or trusted_resolution.get("prepared_artifact_source")
        != "host_trusted_prepared_artifact_store"
        or trusted_resolution.get("gate_receipt_source")
        != "host_trusted_gate_receipt_store"
        or not isinstance(state_matrix, dict)
        or state_matrix.get("committed", {}).get("actions") != ["none"]
        or "none" in state_matrix.get("aborted", {}).get("recovered", {}).get("actions", [])
    ):
        _fail("commit_typed_receipt_contract_open", "事务typed artifact、Gate或恢复状态矩阵未冻结")
    expected_write_sets = {
        "node_result_commit_consistency": {
            "typed_node_result_or_failure_receipt",
            "node_evidence_fragment",
            "identity_digest",
            "input_and_output_digests",
            "execution_receipt",
            "node_execution_state",
        },
        "investigation_revision_commit_consistency": {
            "investigation_revision_snapshot",
            "parent_revision_and_digest",
            "committed_node_revision_refs",
            "investigation_status",
            "evidence_graph_revision_ref",
            "current_pointer",
        },
        "evidence_graph_commit_consistency": {
            "graph_nodes",
            "graph_edges",
            "root_node_ids",
            "graph_digest",
            "closure_receipt",
            "graph_commit_marker",
        },
        "dialog_state_commit_consistency": {
            "dialog_turn_record",
            "investigation_revision_ref",
            "evidence_graph_revision_ref",
            "validated_answer_ref",
            "binding_generation",
            "dialog_current_pointer",
        },
        "export_commit_consistency": {
            "temporary_export_artifact",
            "export_manifest",
            "format_and_member_count_receipt",
            "export_sha256",
            "final_export_reference",
        },
    }
    if (
        not isinstance(role_contract_root, Mapping)
        or role_contract_root.get("closed_consistency_kind_population") is not True
        or role_contract_root.get("closed_role_population_per_kind") is not True
        or role_contract_root.get("schema_ref_must_equal_payload_schema_id") is not True
        or set(role_contracts) != set(expected_write_sets)
    ):
        _fail("commit_artifact_role_contract_open", "prepared artifact role人口未闭合")
    for consistency_kind, expected_roles in expected_write_sets.items():
        contracts_for_kind = role_contracts.get(consistency_kind)
        if not isinstance(contracts_for_kind, Mapping) or set(
            contracts_for_kind
        ) != expected_roles:
            _fail("commit_artifact_role_contract_open", f"{consistency_kind} role合同人口漂移")
        for role, contract in contracts_for_kind.items():
            if not isinstance(contract, Mapping):
                _fail("commit_artifact_role_contract_open", f"{consistency_kind}/{role}合同无效")
            role_schema = contract.get("payload_schema")
            _validate_schema_file(role_schema, consistency_path)
            expected_ref = (
                "https://domeye.example/contracts/runtime-artifacts/"
                f"{consistency_kind}/{role}.schema.json"
            )
            if (
                contract.get("artifact_schema_ref") != expected_ref
                or role_schema.get("$id") != expected_ref
                or role_schema.get("additionalProperties") is not False
            ):
                _fail("commit_artifact_role_contract_open", f"{consistency_kind}/{role} Schema未闭合")
    expected_gate_pairs = {
        (boundary.get("id"), gate_id)
        for boundary in payload.get("boundaries", [])
        for gate_id in boundary.get("validation_gates", [])
    }
    gate_entries = gate_registry.get("entries", [])
    if (
        set(gate_registry)
        != {
            "contract_id",
            "source",
            "caller_supplied_validator_identity_forbidden",
            "closed_gate_population_from_each_boundary_validation_gates",
            "expected_gate_entry_count",
            "subject_contract",
            "entries",
            "registry_content_digest",
        }
        or len(gate_entries) != 25
        or {
            (entry.get("consistency_kind"), entry.get("gate_id"))
            for entry in gate_entries
            if isinstance(entry, Mapping)
        }
        != expected_gate_pairs
        or gate_registry.get("registry_content_digest")
        != _digest_without_fields(gate_registry, "registry_content_digest")
    ):
        _fail("commit_gate_registry_population_open", "25个Gate Registry条目未精确覆盖五类边界")
    implementation_digests: set[str] = set()
    for entry in gate_entries:
        output_schema = entry.get("output_schema")
        if (
            not isinstance(entry, Mapping)
            or set(entry)
            != {
                "consistency_kind",
                "gate_id",
                "gate_version",
                "validator_id",
                "gate_contract_digest",
                "implementation_digest",
                "implementation_artifact",
                "subject_contract",
                "output_schema_ref",
                "output_schema",
            }
            or entry.get("validator_id")
            != f"HOST-GATE::{entry.get('consistency_kind')}::{entry.get('gate_id')}"
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(entry.get("gate_contract_digest")))
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(entry.get("implementation_digest")))
            or not isinstance(output_schema, Mapping)
            or output_schema.get("$id") != entry.get("output_schema_ref")
            or output_schema.get("properties", {}).get("gate_id", {}).get("const")
            != entry.get("gate_id")
        ):
            _fail("commit_gate_registry_entry_open", "Gate Registry条目身份、实现或输出Schema未闭合")
        _validate_schema_file(output_schema, consistency_path)
        implementation_artifact = entry.get("implementation_artifact")
        if (
            not isinstance(implementation_artifact, Mapping)
            or entry.get("implementation_digest")
            != "sha256:" + _digest_without_fields(implementation_artifact)
            or entry.get("gate_contract_digest")
            != "sha256:"
            + _digest_without_fields(
                {
                    "consistency_kind": entry.get("consistency_kind"),
                    "gate_id": entry.get("gate_id"),
                    "gate_version": entry.get("gate_version"),
                    "subject_contract": entry.get("subject_contract"),
                    "output_schema": output_schema,
                }
            )
            or implementation_artifact.get("validator_id")
            != entry.get("validator_id")
            or implementation_artifact.get("consistency_kind")
            != entry.get("consistency_kind")
            or implementation_artifact.get("gate_id") != entry.get("gate_id")
            or implementation_artifact.get("output_schema_ref")
            != entry.get("output_schema_ref")
            or implementation_artifact.get("design_only") is not True
            or implementation_artifact.get("runtime_implemented") is not False
        ):
            _fail(
                "commit_gate_registry_entry_open",
                "Gate Registry声明实现与合同必须按内容寻址冻结",
            )
        implementation_digests.add(entry["implementation_digest"])
    if len(implementation_digests) != 25:
        _fail("commit_gate_registry_entry_open", "Gate implementation_digest必须逐登记实现唯一冻结")
    required_boundary_markers = {
        "node_result_commit_consistency": {
            "preconditions": {
                "node_references_exactly_one_registered_atomic_unit",
                "identity_and_registry_snapshot_match_plan",
            },
            "postconditions": {
                "failed_cancelled_or_skipped_node_has_no_publishable_fact_output",
            },
            "forbidden_visibility": {"result_without_evidence", "evidence_without_result"},
        },
        "investigation_revision_commit_consistency": {
            "preconditions": {
                "hard_dependency_failure_propagation_is_closed",
                "independent_branch_outcome_is_preserved",
            },
            "postconditions": {
                "old_revision_remains_replayable",
                "parameter_permission_or_degrade_change_requires_new_admitted_plan_revision",
            },
            "forbidden_visibility": {"history_overwrite", "stale_parent_pointer_overwrite"},
        },
        "evidence_graph_commit_consistency": {
            "preconditions": {"all_nodes_share_identity_and_registry_binding"},
            "postconditions": {"nodes_edges_and_closure_receipt_visible_together"},
            "forbidden_visibility": {
                "dangling_edge",
                "cross_publication_fact",
                "execution_failure_supports_fact",
            },
        },
        "dialog_state_commit_consistency": {
            "preconditions": {
                "student_answer_validation_passed",
                "alignment_passed_or_explicit_degraded_authorization_exists",
            },
            "postconditions": {"failed_answer_does_not_advance_dialog_state"},
            "forbidden_visibility": {
                "dialog_advances_after_failed_student_validation",
                "dialog_advances_after_invalid_teacher_reference",
            },
        },
        "export_commit_consistency": {
            "preconditions": {"source_result_set_revision_is_frozen_and_complete"},
            "postconditions": {"failed_export_preserves_previous_final_artifact"},
            "forbidden_visibility": {
                "preview_exported_as_complete",
                "incomplete_result_set_exported_as_complete",
                "failed_export_removes_previous_final_artifact",
                "llm_regenerated_members",
            },
        },
    }
    expected_boundary_contract = {
        "node_result_commit_consistency": {
            "validation_gates": {"schema_valid", "identity_valid", "digest_valid", "evidence_refs_closed", "result_completeness_valid"},
            "commit_point": "node_execution_revision_commit_marker",
            "idempotency_key_recipe": {"investigation_id", "plan_revision", "node_id", "execution_revision", "input_digest", "unit_contract_digest"},
            "crash_recovery": "discard_or_resume_prepared_revision_then_compare_and_swap_commit_marker",
        },
        "investigation_revision_commit_consistency": {
            "validation_gates": {"revision_transition_valid", "required_coverage_valid", "partial_status_matches_failures", "no_history_overwrite", "compare_and_swap_parent_valid"},
            "commit_point": "investigation_current_pointer_compare_and_swap",
            "idempotency_key_recipe": {"investigation_id", "investigation_revision", "parent_digest", "snapshot_digest"},
            "crash_recovery": "leave_old_current_pointer_and_retry_same_revision_idempotently",
        },
        "evidence_graph_commit_consistency": {
            "validation_gates": {"node_and_edge_ids_unique", "no_dangling_or_cross_identity_refs", "derived_from_acyclic", "edge_type_semantics_valid", "failure_unknown_and_preview_support_rules_valid"},
            "commit_point": "evidence_graph_revision_commit_marker",
            "idempotency_key_recipe": {"graph_id", "graph_revision", "parent_graph_digest", "graph_digest"},
            "crash_recovery": "discard_uncommitted_graph_staging_or_revalidate_and_commit_same_digest",
        },
        "dialog_state_commit_consistency": {
            "validation_gates": {"answer_evidence_refs_closed", "boundary_compliance_passed", "teacher_student_flow_disposition_valid", "binding_generation_not_stale", "compare_and_swap_parent_valid"},
            "commit_point": "dialog_current_pointer_compare_and_swap",
            "idempotency_key_recipe": {"dialog_id", "turn_id", "binding_generation", "validated_answer_digest"},
            "crash_recovery": "preserve_previous_dialog_pointer_and_retry_same_turn_commit",
        },
        "export_commit_consistency": {
            "validation_gates": {"source_result_set_digest_matches", "no_llm_member_regeneration", "export_member_count_matches_result_set", "export_sha256_matches", "temporary_artifact_fsync_completed"},
            "commit_point": "atomic_replace_final_export_reference",
            "idempotency_key_recipe": {"authorization_id", "result_set_id", "result_set_revision", "content_digest", "format"},
            "crash_recovery": "delete_or_reuse_valid_temporary_artifact_without_replacing_final_on_failure",
        },
    }
    expected_recovery_actions = {
        "node_result_commit_consistency": {"none", "resume_prepare", "discard_prepare", "retry_compare_and_swap"},
        "investigation_revision_commit_consistency": {"none", "discard_prepare", "retry_compare_and_swap", "preserve_current_pointer"},
        "evidence_graph_commit_consistency": {"none", "resume_prepare", "discard_prepare", "retry_compare_and_swap", "preserve_current_pointer"},
        "dialog_state_commit_consistency": {"none", "discard_prepare", "retry_compare_and_swap", "preserve_current_pointer"},
        "export_commit_consistency": {"none", "resume_prepare", "discard_prepare", "preserve_previous_final"},
    }
    boundary_allowed_fields = {
        "id",
        "scope",
        "preconditions",
        "atomic_write_set",
        "validation_gates",
        "commit_point",
        "postconditions",
        "idempotency_key_recipe",
        "crash_recovery",
        "allowed_recovery_actions",
        "forbidden_visibility",
    }
    for boundary in payload["boundaries"]:
        boundary_id = boundary["id"]
        if set(boundary) != boundary_allowed_fields:
            _fail("commit_boundary_contract_incomplete", f"{boundary_id} 合同字段不闭合")
        for field in (
            "scope",
            "preconditions",
            "atomic_write_set",
            "validation_gates",
            "commit_point",
            "postconditions",
            "idempotency_key_recipe",
            "crash_recovery",
            "allowed_recovery_actions",
            "forbidden_visibility",
        ):
            value = boundary.get(field)
            if not value or (field not in {"scope", "commit_point", "crash_recovery"} and not isinstance(value, list)):
                _fail("commit_boundary_contract_incomplete", f"{boundary_id} 缺少 {field}")
        if set(boundary.get("atomic_write_set", [])) != expected_write_sets[boundary_id]:
            _fail("commit_atomic_write_set_open", f"{boundary_id} 原子写集合漂移")
        if set(boundary.get("allowed_recovery_actions", [])) != expected_recovery_actions[boundary_id]:
            _fail("commit_boundary_semantic_drift", f"{boundary_id} recovery_action人口漂移")
        for field, expected_markers in required_boundary_markers[boundary_id].items():
            if not expected_markers.issubset(set(boundary.get(field, []))):
                _fail("commit_boundary_contract_incomplete", f"{boundary_id}.{field} 关键规则缺失")
        expected_contract = expected_boundary_contract[boundary_id]
        if set(boundary.get("validation_gates", [])) != expected_contract["validation_gates"] or boundary.get(
            "commit_point"
        ) != expected_contract["commit_point"] or set(boundary.get("idempotency_key_recipe", [])) != expected_contract[
            "idempotency_key_recipe"
        ] or boundary.get("crash_recovery") != expected_contract["crash_recovery"]:
            _fail("commit_boundary_semantic_drift", f"{boundary_id} Gate、提交点、幂等或恢复语义漂移")
    rules = payload.get("global_rules")
    if not isinstance(rules, dict) or any(
        rules.get(field) is not True
        for field in (
            "prepared_artifacts_are_not_public",
            "commit_marker_is_single_visibility_point",
            "history_is_immutable",
            "partial_failure_creates_new_revision",
            "retry_or_recovery_must_be_idempotent",
            "cross_publication_or_registry_mix_forbidden",
            "cancel_during_prepare_may_abort_but_never_publish_half_state",
            "runtime_transaction_may_not_hide_composite_execution_unit",
        )
    ):
        _fail("commit_boundary_contract_incomplete", "全局提交、恢复或功能原子性边界未闭合")
    expected_commit_order = [
        "prepare_node_result_and_evidence_fragment",
        "validate_identity_schema_digest_and_evidence",
        "commit_node_result",
        "prepare_and_validate_evidence_graph_closure",
        "commit_evidence_graph_revision",
        "commit_investigation_revision_with_graph_ref",
        "validate_final_student_answer",
        "commit_dialog_state",
        "optionally_commit_export_reference",
    ]
    expected_failure_injections = {
        "cancel_during_node_prepare",
        "investigation_parent_compare_and_swap_conflict",
        "graph_edge_write_failure",
        "student_validation_failure_before_dialog_commit",
        "export_validation_failure_before_final_replace",
        "same_idempotency_key_retry",
    }
    if payload.get("global_commit_order") != expected_commit_order or set(
        payload.get("required_failure_injections", [])
    ) != expected_failure_injections:
        _fail("commit_order_or_failure_injection_drift", "全局提交顺序或故障注入人口漂移")
    design_boundary = payload.get("design_boundary")
    if not isinstance(design_boundary, dict) or any(
        design_boundary.get(field) is not expected
        for field, expected in (
            ("design_only", True),
            ("runtime_implemented", False),
            ("production_deployed", False),
        )
    ):
        _fail("runtime_claim_forbidden", "S1D-4不得声称运行时已实现或生产已部署")
    recomputed_contract_digest = _digest_without_fields(
        payload, "contract_content_digest"
    )
    if (
        payload.get("contract_content_digest")
        != RUNTIME_CONSISTENCY_CONTRACT_CANONICAL_DIGEST
        or recomputed_contract_digest
        != RUNTIME_CONSISTENCY_CONTRACT_CANONICAL_DIGEST
    ):
        _fail(
            "runtime_consistency_contract_digest_mismatch",
            "运行时一致性合同必须与Hook封存内容摘要完全一致",
        )
    return [
        "investigation_three_revision_state_machine",
        "one_plan_node_one_atomic_execution_unit",
        "result_set_page_and_preview_closure",
        "evidence_graph_identity_and_relation_closure",
        "world_knowledge_outside_event_fact_graph",
        "sol_teacher_validator_ds_student_shared_binding",
        "single_feedback_single_student_revision",
        "runtime_five_commit_consistency_boundaries",
    ]


def _validate_s1d5(repo_root: Path) -> list[str]:
    oracle_path, budget_path, alignment_path, review_path = (
        repo_root / relative for relative in ARTIFACTS_BY_STAGE["S1D-5"]
    )
    oracle = _load_json(oracle_path)
    for payload, path in (
        (oracle, oracle_path),
    ):
        if payload.get("content_digest") != _digest_without_fields(
            payload, "content_digest"
        ):
            _fail("s1d5_content_digest_mismatch", f"S1D-5制品内容摘要无法重算：{path}")
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
    if budget.get("content_digest") != _digest_without_fields(
        budget, "content_digest"
    ):
        _fail("s1d5_content_digest_mismatch", f"S1D-5制品内容摘要无法重算：{budget_path}")
    alignment = _load_json(alignment_path)
    if not isinstance(alignment, dict):
        _fail("model_alignment_contract_invalid", "模型对齐评测必须是 JSON object")
    if alignment.get("content_digest") != _digest_without_fields(
        alignment, "content_digest"
    ):
        _fail("s1d5_content_digest_mismatch", f"S1D-5制品内容摘要无法重算：{alignment_path}")
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
    if alignment.get("promotion_gate", {}).get("candidate_promotable") is not False:
        _fail("model_alignment_promotion_overclaim", "实际模型对齐证据未闭合时不得晋级")
    if budget.get("measurement_closure", {}).get(
        "performance_acceptance_blocked"
    ) is not True:
        _fail("performance_acceptance_overclaim", "未实测Tool/Operator/Sol/端到端时性能验收必须阻断")
    review = _load_json(review_path)
    if not isinstance(review, dict):
        _fail("review_contract_invalid", "产品语义审查必须是 JSON object")
    if review.get("content_digest") != _digest_without_fields(
        review, "content_digest"
    ):
        _fail("s1d5_content_digest_mismatch", f"S1D-5制品内容摘要无法重算：{review_path}")
    review_inputs = review.get("review_inputs")
    binding = review.get("review_input_binding")
    if (
        not isinstance(review_inputs, list)
        or not isinstance(binding, Mapping)
        or binding.get("content_digests_bound") is not True
        or binding.get("binding_status") != "frozen_for_independent_review"
        or binding.get("review_may_start_without_frozen_digests") is not False
        or not isinstance(binding.get("bound_at"), str)
        or not isinstance(binding.get("input_sha256"), Mapping)
        or set(binding.get("input_sha256", {})) != set(review_inputs)
    ):
        _fail("review_input_binding_open", "独立审查未绑定完整冻结输入摘要")
    actual_review_input_sha256 = {
        name: _sha256(review_path.parent / name) for name in review_inputs
    }
    if (
        dict(binding.get("input_sha256", {})) != actual_review_input_sha256
        or binding.get("input_binding_digest")
        != _digest_without_fields(actual_review_input_sha256)
    ):
        _fail("review_input_binding_stale", "独立审查输入摘要与当前候选不一致")
    if not review.get("builder_id") or not review.get("reviewer_id"):
        _fail("reviewer_identity_missing", "必须提供 builder_id 与 reviewer_id")
    if "pending" in str(review.get("reviewer_id")).lower():
        _fail("reviewer_identity_missing", "不得用pending reviewer身份通过审查")
    if review["builder_id"] == review["reviewer_id"]:
        _fail("reviewer_independence_failed", "Builder 与 Reviewer 不得为同一身份")
    for field in ("independent_product_semantic_review", "independent_bgp_review"):
        independent_review = review.get(field)
        if (
            not isinstance(independent_review, Mapping)
            or "pending" in str(independent_review.get("reviewer_id")).lower()
            or independent_review.get("execution_status") != "completed"
            or not isinstance(independent_review.get("started_at"), str)
            or not isinstance(independent_review.get("completed_at"), str)
            or not isinstance(independent_review.get("findings"), list)
            or not isinstance(independent_review.get("finding_dispositions"), list)
            or independent_review.get("hard_gate_passed") is not True
            or independent_review.get("disposition") != "passed"
        ):
            _fail("independent_review_incomplete", f"{field}未以完整身份、时间和发现处置通过")
    allowed_finding_statuses = {
        "closed_by_design_contract_and_tests",
        "runtime_promotion_blocker_not_design_acceptance_blocker",
    }
    if any(
        item.get("status") not in allowed_finding_statuses
        for item in review.get("known_blocking_findings", [])
        if isinstance(item, Mapping)
    ):
        _fail("review_finding_disposition_open", "审查发现未分层闭合为设计闭合或运行晋级阻断")
    disposition = review.get("stage_review_disposition")
    if (
        review.get("status")
        != "design_semantic_review_passed_runtime_promotion_blocked"
        or not isinstance(disposition, Mapping)
        or disposition.get("review_passed") is not True
        or disposition.get("high_risk_semantics_closed") is not True
        or disposition.get("implementation_handoff_allowed") is not True
        or disposition.get("s1d5_acceptance_allowed") is not True
        or disposition.get("model_alignment_passed") is not False
        or disposition.get("performance_acceptance") is not False
        or disposition.get("runtime_model_promotion") is not False
        or disposition.get("disposition")
        != "design_semantic_review_passed_runtime_promotion_blocked"
    ):
        _fail("review_disposition_overclaim", "S1D-5必须仅通过设计语义审查并保持运行、模型和性能晋级阻断")
    return [
        "oracle_scenario_closure",
        "budget_contract",
        "sol_ds_alignment_contract",
        "frozen_review_input_digest_closure",
        "independent_product_semantic_review",
        "independent_bgp_review",
        "design_acceptance_runtime_promotion_separation",
    ]


def _validate_s1d6(repo_root: Path) -> list[str]:
    candidate_relative, manifest_relative = ARTIFACTS_BY_STAGE["S1D-6"]
    candidate_path = repo_root / candidate_relative
    manifest_path = repo_root / manifest_relative
    candidate = _load_json_strict(candidate_path)
    manifest = _load_json_strict(manifest_path)
    if not isinstance(candidate, dict):
        _fail("candidate_invalid", "candidate 必须是 JSON object")
    if not isinstance(manifest, dict):
        _fail("manifest_invalid", "acceptance-manifest 必须是 JSON object")

    expected_candidate_keys = {
        "schema_version",
        "artifact_id",
        "stage",
        "status",
        "candidate_kind",
        "design_candidate_id",
        "candidate_semantic_digest",
        "content_digest",
        "source_artifact_count",
        "source_artifact_set_digest",
        "acceptance_manifest_path",
        "task_binding",
        "parent_candidate",
        "prior_stage_receipts",
        "coverage_and_scope",
        "execution_unit_scope",
        "bgp_semantic_contract",
        "model_flow_disposition",
        "review_disposition",
        "acceptance_layers",
        "runtime_promotion_blockers",
        "implementation_handoff",
        "design_only",
        "runtime_implemented",
        "production_deployed",
    }
    if set(candidate) != expected_candidate_keys:
        _fail(
            "candidate_schema_invalid",
            f"candidate字段人口不闭合；actual={sorted(candidate)}, expected={sorted(expected_candidate_keys)}",
        )
    expected_candidate_constants = {
        "schema_version": "country_outage_p2_s1_final_design_candidate_v1",
        "artifact_id": "country-outage-p2-s1-final-design-candidate",
        "stage": "S1D-6",
        "status": "design_contract_accepted_for_implementation_handoff",
        "candidate_kind": "single_content_addressed_design_handoff_candidate",
        "acceptance_manifest_path": manifest_relative.as_posix(),
        "design_only": True,
        "runtime_implemented": False,
        "production_deployed": False,
    }
    for key, expected in expected_candidate_constants.items():
        if candidate.get(key) != expected:
            _fail("candidate_boundary_drift", f"candidate.{key} 必须为 {expected!r}")

    semantic_digest = _digest_without_fields(
        candidate,
        "design_candidate_id",
        "candidate_semantic_digest",
        "content_digest",
    )
    expected_candidate_id = f"country-outage-p2-s1-s1d-6-{semantic_digest[:24]}"
    if candidate.get("candidate_semantic_digest") != semantic_digest:
        _fail("candidate_semantic_digest_mismatch", "candidate语义摘要无法重算")
    if candidate.get("design_candidate_id") != expected_candidate_id:
        _fail("candidate_identity_mismatch", "candidate身份无法由语义摘要重算")
    if candidate.get("content_digest") != _digest_without_fields(candidate, "content_digest"):
        _fail("candidate_content_digest_mismatch", "candidate内容摘要无法重算")

    source_relatives = _s1d6_source_artifacts()
    source_entries = [_s1d6_artifact_entry(repo_root, path) for path in source_relatives]
    source_digest = _s1d6_artifact_set_digest(source_entries)
    if candidate.get("source_artifact_count") != len(source_entries):
        _fail("candidate_source_population_mismatch", "candidate源制品数量错误")
    if candidate.get("source_artifact_set_digest") != source_digest:
        _fail("candidate_source_digest_mismatch", "candidate未绑定当前冻结源制品集合")

    task = _load_json_strict(repo_root / Path(".codex/TASK.json"))
    task_binding = candidate.get("task_binding")
    expected_task_binding = {
        "task_id": task.get("taskId"),
        "target_version": task.get("targetVersion"),
        "base_commit": task.get("baseCommit"),
        "task_contract_sha256": _sha256(repo_root / Path(".codex/TASK.json")),
        "task_spec_sha256": _sha256(repo_root / TASK_SPEC),
        "phase_plan_sha256": _sha256(repo_root / PHASE_PLAN),
        "alignment_hook_sha256": _sha256(repo_root / ALIGNMENT_HOOK),
        "alignment_hook_tests_sha256": _sha256(repo_root / ALIGNMENT_HOOK_TESTS),
    }
    if task_binding != expected_task_binding:
        _fail("candidate_task_binding_mismatch", "candidate任务、文档或Hook绑定错误")
    if task.get("taskId") != "country-outage-agent-p2-s1d6-final-design-acceptance-20260813":
        _fail("candidate_task_binding_mismatch", "S1D-6必须绑定获授权的最终设计验收任务")

    prior_receipts = _validate_prior_receipts(repo_root, "S1D-6")
    expected_receipt_chain: list[dict[str, Any]] = []
    for stage in STAGES[:6]:
        relative = RECEIPT_ROOT / f"{stage}.json"
        payload = _load_json_strict(repo_root / relative)
        expected_receipt_chain.append(
            {
                "stage": stage,
                "path": relative.as_posix(),
                "design_candidate_id": payload.get("design_candidate_id"),
                "receipt_digest": payload.get("receipt_digest"),
                "sha256": _sha256(repo_root / relative),
            }
        )
    if candidate.get("prior_stage_receipts") != expected_receipt_chain:
        _fail("candidate_receipt_chain_mismatch", "candidate未精确绑定S1D-0至S1D-5回执链")
    s1d5 = _load_json_strict(repo_root / RECEIPT_ROOT / "S1D-5.json")
    if candidate.get("parent_candidate") != {
        "stage": "S1D-5",
        "design_candidate_id": s1d5.get("design_candidate_id"),
        "receipt_digest": s1d5.get("receipt_digest"),
    }:
        _fail("candidate_parent_mismatch", "candidate父候选必须是当前S1D-5回执")
    if prior_receipts != {
        item["stage"]: item["receipt_digest"] for item in expected_receipt_chain
    }:
        _fail("candidate_receipt_chain_mismatch", "候选回执链与当前阶段回执不一致")

    capability_map = _load_json_strict(repo_root / ARTIFACTS_BY_STAGE["S1D-1"][0])
    questions = capability_map.get("questions")
    if not isinstance(questions, list):
        _fail("candidate_question_population_mismatch", "问题映射缺少questions")
    question_ids = [item.get("question_id") for item in questions if isinstance(item, dict)]
    answerability = {
        item["question_id"]: item.get("answerability")
        for item in questions
        if isinstance(item, dict) and isinstance(item.get("question_id"), str)
    }
    expected_coverage = {
        "question_count": 28,
        "question_ids": list(EXPECTED_QUESTIONS),
        "question_population_digest": _digest_without_fields(
            {"question_ids": list(EXPECTED_QUESTIONS), "answerability": answerability}
        ),
        "design_covered_count": 28,
        "runtime_answerability_certified": False,
        "p2_v1_not_executable_question_ids": ["Q24"],
        "external_evidence_required_question_ids": ["Q29", "Q30"],
        "supported_subset_with_deferred_fanout": {
            "Q20": ["逐路径ASN位置", "批量邻接"],
            "Q23": ["逐活动路径ASN位置", "跨并列峰值自动展开"],
            "Q26": ["逐origin再次查询", "逐origin分别统计"],
        },
    }
    if question_ids != list(EXPECTED_QUESTIONS) or candidate.get("coverage_and_scope") != expected_coverage:
        _fail("candidate_question_population_mismatch", "candidate问题人口、可答边界或延期子目标漂移")

    tool_catalog = _load_json_strict(repo_root / ARTIFACTS_BY_STAGE["S1D-2"][0])
    operator_catalog = _load_json_strict(repo_root / ARTIFACTS_BY_STAGE["S1D-3"][0])
    decomposition = _load_json_strict(repo_root / ARTIFACTS_BY_STAGE["S1D-1"][2])
    tools = tool_catalog.get("tools")
    operators = operator_catalog.get("operators")
    if not isinstance(tools, list) or not isinstance(operators, list):
        _fail("candidate_execution_unit_population_mismatch", "Tool/Operator目录人口无效")
    tool_ids = [item.get("unit_id") for item in tools if isinstance(item, dict)]
    operator_ids = [item.get("unit_id") for item in operators if isinstance(item, dict)]
    p2_v1_operator_ids = [item for item in EXPECTED_OPERATORS if item != "OP-34"]
    expected_execution_scope = {
        "existing_registry_dependencies": list(EXISTING_EXECUTION_UNITS),
        "p2_v1_tool_ids": [f"TOOL-{index:02d}" for index in range(7, 13)],
        "p2_1_deferred_tool_ids": ["TOOL-13"],
        "p2_v1_operator_ids": p2_v1_operator_ids,
        "p2_1_deferred_operator_ids": ["OP-34"],
        "p2_v1_plan_capability_ids": ["PLAN-CAP-01"],
        "p2_1_deferred_plan_capability_ids": ["PLAN-CAP-02"],
        "control_unit_ids": list(EXPECTED_HOST_UNITS[2:]),
        "new_execution_units_runtime_ready": False,
        "composition_location": "InvestigationPlan",
        "atomicity_rule": "one_tool_one_fact_population_and_one_operator_one_deterministic_transform",
        "hidden_fan_out_replacement_forbidden": True,
    }
    if tool_ids != list(EXPECTED_TOOLS) or operator_ids != list(EXPECTED_OPERATORS):
        _fail("candidate_execution_unit_population_mismatch", "Tool或Operator精确人口漂移")
    if candidate.get("execution_unit_scope") != expected_execution_scope:
        _fail("candidate_execution_unit_population_mismatch", "candidate执行单元或原子边界漂移")
    deferred_plan = decomposition.get("host_plan_capability_contracts", {}).get("PLAN-CAP-02")
    tool13 = next((item for item in tools if item.get("unit_id") == "TOOL-13"), None)
    op34 = next((item for item in operators if item.get("unit_id") == "OP-34"), None)
    if (
        not isinstance(deferred_plan, dict)
        or deferred_plan.get("disposition") != "deferred_p2_1"
        or deferred_plan.get("p2_v1_execution_allowed") is not False
        or deferred_plan.get("runtime_ready_claim") is not False
        or not isinstance(tool13, dict)
        or tool13.get("disposition") != "deferred_p2_1"
        or tool13.get("runtime_ready_claim") is not False
        or not isinstance(op34, dict)
        or op34.get("disposition") != "deferred_p2_1"
        or op34.get("runtime_ready_claim") is not False
        or any(item.get("runtime_ready_claim") is not False for item in tools + operators)
    ):
        _fail("candidate_deferred_scope_drift", "P2.1延期单元或新单元runtime-ready边界漂移")

    expected_bgp_contract = {
        "collector_scope": "rrc25_only",
        "event_identity_cardinality": "single_incident_publication_revision_cohort_window",
        "as_sort": [
            "peak_invisible_direction_count DESC",
            "peak_complete_prefix_count DESC",
            "asn ASC",
        ],
        "as_rank_semantics": "first_two_keys_define_competition_rank_asn_only_defines_result_position",
        "complete_invisible_semantics": "fixed_population_expected_directions_control_plane_classification_not_withdraw_global_unreachability_or_user_outage",
        "as_path_semantics": "typed_segments_preserved_as_set_and_confederation_not_forced_linear_prepend_collapse_only_by_registered_profile",
        "tool12_semantics": "window_level_association_not_path_at_time_eligible_anchor_from_same_publication_complete_ever_affected_as_set_noneligible_unsupported_known_origin_equals_observed_origin_and_collapsed_ordered_tail_anchor_strictly_before_origin",
        "op19_semantics": "observed_downstream_origin_set_not_customer_cone_customer_zone_or_business_relationship",
        "relationship_boundary": "time_set_path_and_metric_relationships_do_not_prove_cause_responsibility_recovery_national_or_user_impact",
    }
    if candidate.get("bgp_semantic_contract") != expected_bgp_contract:
        _fail("candidate_bgp_semantic_drift", "candidate未冻结BGP关键语义或越过证据边界")

    model_role = _load_json_strict(repo_root / ARTIFACTS_BY_STAGE["S1D-1"][3])
    alignment = _load_json_strict(repo_root / ARTIFACTS_BY_STAGE["S1D-5"][2])
    budget = _load_json_strict(repo_root / ARTIFACTS_BY_STAGE["S1D-5"][1])
    review = _load_json_strict(repo_root / ARTIFACTS_BY_STAGE["S1D-5"][3])
    expected_model_flow = {
        "execution_order": ["gpt-5.6-sol", "host_grounding_and_validation", "ds_student"],
        "teacher_reference_is_ground_truth": False,
        "evidence_truth_precedes_teacher": True,
        "teacher_required": True,
        "successful_student_revision_max": 1,
        "student_provider": "deepseek",
        "student_model": "deepseek-v4-flash",
        "student_version": "deepseek-v4-flash-pi-0.84.1-v1",
        "monetary_limit_mode": "unlimited",
        "all_attempt_usage_cost_latency_receipts_required": True,
        "text_similarity_may_override_hard_gates": False,
    }
    if candidate.get("model_flow_disposition") != expected_model_flow:
        _fail("candidate_model_flow_drift", "candidate Sol→Host→DS、模型身份或晋级规则漂移")
    if (
        model_role.get("execution_order") != ["gpt-5.6-sol", "ds_student"]
        or model_role.get("teacher_reference_is_ground_truth") is not False
        or model_role.get("feedback_and_revision_policy", {}).get("successful_student_revision_max") != 1
        or alignment.get("promotion_gate", {}).get("candidate_promotable") is not False
        or budget.get("measurement_closure", {}).get("performance_acceptance_blocked") is not True
    ):
        _fail("candidate_model_flow_drift", "上游模型或性能合同不支持candidate声明")

    product_review = review.get("independent_product_semantic_review", {})
    bgp_review = review.get("independent_bgp_review", {})
    expected_review_disposition = {
        "s1d5_review_input_binding_digest": review.get("review_input_binding", {}).get("input_binding_digest"),
        "product_semantic_reviewer_id": product_review.get("reviewer_id"),
        "product_semantic_design_review_passed": product_review.get("hard_gate_passed"),
        "bgp_reviewer_id": bgp_review.get("reviewer_id"),
        "bgp_design_review_passed": bgp_review.get("hard_gate_passed"),
    }
    if candidate.get("review_disposition") != expected_review_disposition:
        _fail("candidate_review_binding_mismatch", "candidate未绑定S1D-5两份独立设计审查")
    if (
        product_review.get("execution_status") != "completed"
        or product_review.get("disposition") != "passed"
        or bgp_review.get("execution_status") != "completed"
        or bgp_review.get("disposition") != "passed"
    ):
        _fail("candidate_review_binding_mismatch", "独立设计审查未完成或未通过")

    expected_layers = {
        "design_contract_accepted": True,
        "product_semantic_design_review_passed": True,
        "bgp_design_review_passed": True,
        "implementation_handoff_allowed": True,
        "model_alignment_passed": False,
        "performance_acceptance": False,
        "runtime_model_promotion": False,
        "registry_admission_performed": False,
        "runtime_implemented": False,
        "production_deployed": False,
    }
    if candidate.get("acceptance_layers") != expected_layers:
        _fail("candidate_acceptance_layer_overclaim", "candidate验收分层发生越级声明")
    expected_blockers = [
        {
            "blocker_id": "HOST_HARD_GATE_METRICS_NOT_MEASURED",
            "blocks": ["model_alignment", "runtime_model_promotion"],
        },
        {
            "blocker_id": "TRUSTED_STUDENT_ARTIFACT_NOT_SUPPLIED",
            "blocks": ["model_alignment", "runtime_model_promotion"],
        },
        {
            "blocker_id": "SHARED_REPLAY_BINDING_DIGESTS_NOT_SUPPLIED",
            "blocks": ["model_alignment", "runtime_model_promotion"],
        },
        {
            "blocker_id": "SOL_ROLE_USAGE_COST_LATENCY_NOT_MEASURED",
            "blocks": ["performance_acceptance", "runtime_model_promotion"],
        },
        {
            "blocker_id": "TOOL_OPERATOR_E2E_PERFORMANCE_NOT_MEASURED",
            "blocks": ["performance_acceptance", "runtime_model_promotion"],
        },
    ]
    if candidate.get("runtime_promotion_blockers") != expected_blockers:
        _fail("candidate_runtime_blocker_mismatch", "candidate运行晋级阻断不完整或被弱化")
    if (
        alignment.get("shared_replay_binding", {}).get("verification_status")
        != "not_verifiable_without_digests"
        or alignment.get("successful_ds_revision_after_scope_rebaseline", {}).get(
            "host_trusted_student_artifact_status"
        ) != "not_supplied_blocks_alignment_acceptance"
        or budget.get("measurement_closure", {}).get("tool_measurement_status") != "not_measured"
        or budget.get("measurement_closure", {}).get("operator_measurement_status") != "not_measured"
        or budget.get("measurement_closure", {}).get("sol_planning_measurement_status") != "not_measured"
        or budget.get("measurement_closure", {}).get("sol_reference_measurement_status") != "not_measured"
    ):
        _fail("candidate_runtime_blocker_mismatch", "上游证据不支持当前运行阻断声明")

    expected_handoff = {
        "next_authorized_actions": ["implementation_planning", "implementation_s0"],
        "direct_deployment_authorized": False,
        "registry_activation_authorized": False,
        "runtime_promotion_authorized": False,
        "production_change_authorized": False,
        "waves": _s1d6_expected_handoff_waves(),
        "p2_1_requires_separate_task": ["PLAN-CAP-02", "TOOL-13", "OP-34"],
    }
    if candidate.get("implementation_handoff") != expected_handoff:
        _fail("candidate_handoff_drift", "实现交接顺序、延期边界或授权范围漂移")

    expected_manifest_keys = {
        "schema_version",
        "artifact_id",
        "stage",
        "status",
        "acceptance_scope",
        "design_candidate_id",
        "candidate_ref",
        "source_artifact_set_digest",
        "prior_receipt_chain_digest",
        "artifact_count",
        "artifacts",
        "artifact_set_digest",
        "closure_assertions",
        "acceptance_layers",
        "final_independent_reviews",
        "design_only",
        "runtime_implemented",
        "production_deployed",
        "content_digest",
    }
    if set(manifest) != expected_manifest_keys:
        _fail("manifest_schema_invalid", "acceptance-manifest字段人口不闭合")
    expected_manifest_constants = {
        "schema_version": "country_outage_p2_s1_acceptance_manifest_v1",
        "artifact_id": "country-outage-p2-s1-final-design-acceptance-manifest",
        "stage": "S1D-6",
        "status": "accepted_design_contract_for_implementation_handoff",
        "acceptance_scope": "design_contract_only_runtime_model_performance_and_deployment_blocked",
        "design_candidate_id": expected_candidate_id,
        "source_artifact_set_digest": source_digest,
        "design_only": True,
        "runtime_implemented": False,
        "production_deployed": False,
    }
    for key, expected in expected_manifest_constants.items():
        if manifest.get(key) != expected:
            _fail("manifest_boundary_drift", f"manifest.{key} 必须为 {expected!r}")
    expected_manifest_entries = source_entries + [
        _s1d6_artifact_entry(repo_root, candidate_relative)
    ]
    expected_manifest_entries = sorted(expected_manifest_entries, key=lambda item: item["path"])
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        _fail("manifest_invalid", "manifest.artifacts必须是数组")
    seen: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {"path", "role", "stage", "sha256", "size_bytes"}:
            _fail("manifest_invalid", "manifest artifact字段必须精确闭合")
        path_text = item.get("path")
        if not isinstance(path_text, str) or not _safe_repo_relative_posix_path(path_text):
            _fail("manifest_path_invalid", f"manifest包含不安全路径：{path_text!r}")
        if path_text in seen:
            _fail("manifest_duplicate_path", f"manifest包含重复路径：{path_text}")
        seen.add(path_text)
        if not isinstance(item.get("sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None:
            _fail("manifest_sha256_invalid", f"manifest摘要格式错误：{path_text}")
        target = repo_root / Path(path_text)
        _regular_file(target)
    if artifacts != expected_manifest_entries:
        _fail("manifest_artifact_population_mismatch", "manifest必须按path稳定排序并精确覆盖31项制品")
    if manifest.get("artifact_count") != len(expected_manifest_entries):
        _fail("manifest_artifact_population_mismatch", "manifest artifact_count错误")
    artifact_set_digest = _s1d6_artifact_set_digest(expected_manifest_entries)
    if manifest.get("artifact_set_digest") != artifact_set_digest:
        _fail("manifest_artifact_set_digest_mismatch", "manifest制品集合摘要无法重算")
    expected_candidate_ref = {
        "path": candidate_relative.as_posix(),
        "sha256": _sha256(candidate_path),
        "candidate_semantic_digest": semantic_digest,
        "content_digest": candidate.get("content_digest"),
    }
    if manifest.get("candidate_ref") != expected_candidate_ref:
        _fail("manifest_candidate_binding_mismatch", "manifest未精确绑定candidate")
    chain_digest = _digest_without_fields({"stage_receipts": expected_receipt_chain})
    if manifest.get("prior_receipt_chain_digest") != chain_digest:
        _fail("manifest_receipt_chain_mismatch", "manifest未绑定当前S1D-0至S1D-5回执链")
    expected_closure = {
        "exact_artifact_population": True,
        "duplicate_paths": [],
        "dangling_references": [],
        "all_stage_receipts_current": True,
        "question_capability_execution_oracle_closed_for_design": True,
        "p2_v1_and_p2_1_machine_separated": True,
        "design_acceptance_separated_from_runtime_promotion": True,
        "single_candidate_identity_for_s1d6_and_final": True,
    }
    if manifest.get("closure_assertions") != expected_closure:
        _fail("manifest_closure_assertion_mismatch", "manifest闭包断言漂移")
    if manifest.get("acceptance_layers") != expected_layers:
        _fail("manifest_acceptance_layer_overclaim", "manifest验收分层越级")
    if manifest.get("content_digest") != _digest_without_fields(manifest, "content_digest"):
        _fail("manifest_content_digest_mismatch", "manifest内容摘要无法重算")
    reviews = manifest.get("final_independent_reviews")
    expected_reviewers = {
        "codex-agent:/root/s1d5_product_semantic_final": "product_semantic_final_candidate_review",
        "codex-agent:/root/bgp_evaluator": "bgp_final_candidate_review",
    }
    if not isinstance(reviews, list) or len(reviews) != 2:
        _fail("manifest_final_review_invalid", "manifest必须包含两份独立最终候选审查")
    actual_reviewers: dict[str, str] = {}
    for item in reviews:
        if not isinstance(item, dict) or set(item) != {
            "review_kind",
            "reviewer_id",
            "reviewer_identity_status",
            "execution_status",
            "hard_gate_passed",
            "disposition",
            "candidate_content_digest",
            "source_artifact_set_digest",
            "artifact_set_digest",
            "started_at",
            "completed_at",
        }:
            _fail("manifest_final_review_invalid", "最终候选审查字段不闭合")
        reviewer_id = item.get("reviewer_id")
        if not isinstance(reviewer_id, str) or reviewer_id in actual_reviewers:
            _fail("manifest_final_review_invalid", "最终候选审查者重复或无效")
        actual_reviewers[reviewer_id] = str(item.get("review_kind"))
        if (
            item.get("reviewer_identity_status") != "independent_from_builder_verified"
            or item.get("execution_status") != "completed"
            or item.get("hard_gate_passed") is not True
            or item.get("disposition") != "passed_design_candidate_for_implementation_handoff"
            or item.get("candidate_content_digest") != candidate.get("content_digest")
            or item.get("source_artifact_set_digest") != source_digest
            or item.get("artifact_set_digest") != artifact_set_digest
            or not isinstance(item.get("started_at"), str)
            or not isinstance(item.get("completed_at"), str)
        ):
            _fail("manifest_final_review_invalid", "最终候选审查状态、身份或摘要绑定错误")
    if actual_reviewers != expected_reviewers:
        _fail("manifest_final_review_invalid", "最终候选必须由独立产品语义与BGP审查者分别复核")
    return [
        "single_final_design_candidate_identity",
        "exact_31_artifact_manifest_closure",
        "s1d0_to_s1d5_receipt_chain_closure",
        "question_population_and_deferred_scope_closure",
        "execution_unit_atomicity_and_runtime_boundary_closure",
        "bgp_semantic_handoff_boundary",
        "sol_host_ds_design_acceptance_runtime_promotion_separation",
        "independent_final_candidate_reviews",
    ]


def _s1d6_source_artifacts() -> list[Path]:
    paths = [Path(".codex/TASK.json"), TASK_SPEC, PHASE_PLAN, ALIGNMENT_HOOK, ALIGNMENT_HOOK_TESTS]
    for stage in STAGES[1:6]:
        paths.extend(ARTIFACTS_BY_STAGE[stage])
    paths.extend(RECEIPT_ROOT / f"{stage}.json" for stage in STAGES[:6])
    return sorted(paths, key=lambda path: path.as_posix())


def _s1d6_artifact_role_stage(relative: Path) -> tuple[str, str]:
    if relative == Path(".codex/TASK.json"):
        return "task_contract", "all"
    if relative in (TASK_SPEC, PHASE_PLAN):
        return "governance_document", "all"
    if relative == ALIGNMENT_HOOK:
        return "alignment_hook", "all"
    if relative == ALIGNMENT_HOOK_TESTS:
        return "alignment_hook_tests", "all"
    if relative == ARTIFACTS_BY_STAGE["S1D-6"][0]:
        return "final_design_candidate", "S1D-6"
    for stage in STAGES[1:6]:
        if relative in ARTIFACTS_BY_STAGE[stage]:
            return "design_contract", stage
    for stage in STAGES[:6]:
        if relative == RECEIPT_ROOT / f"{stage}.json":
            return "stage_receipt", stage
    _fail("manifest_artifact_population_mismatch", f"未知S1D-6制品角色：{relative}")
    raise AssertionError("unreachable")


def _s1d6_artifact_entry(repo_root: Path, relative: Path) -> dict[str, Any]:
    path = repo_root / relative
    _regular_file(path)
    role, stage = _s1d6_artifact_role_stage(relative)
    return {
        "path": relative.as_posix(),
        "role": role,
        "stage": stage,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _s1d6_artifact_set_digest(entries: Sequence[Mapping[str, Any]]) -> str:
    return _digest_without_fields({"artifacts": list(entries)})


def _safe_repo_relative_posix_path(value: str) -> bool:
    if not value or value.startswith("/") or "\\" in value:
        return False
    path = PurePosixPath(value)
    return path.as_posix() == value and all(part not in ("", ".", "..") for part in path.parts)


def _s1d6_expected_handoff_waves() -> list[dict[str, Any]]:
    return [
        {
            "wave_id": "W0-source-and-governance",
            "depends_on": [],
            "unit_ids": [],
            "exit": "source_schema_view_registry_admission_and_trusted_receipt_store_contracts_implemented",
        },
        {
            "wave_id": "W1-as-prefix",
            "depends_on": ["W0-source-and-governance"],
            "unit_ids": [
                *[f"TOOL-{index:02d}" for index in range(7, 11)],
                *[f"OP-{index:02d}" for index in range(5, 15)],
                "OP-35",
                "OP-36",
            ],
            "exit": "asn_prefix_and_state_time_contracts_implemented_and_tested",
        },
        {
            "wave_id": "W2-window-path",
            "depends_on": ["W0-source-and-governance"],
            "unit_ids": ["TOOL-12", *[f"OP-{index:02d}" for index in range(15, 29)]],
            "exit": "window_path_projection_count_and_set_contracts_implemented_and_tested",
        },
        {
            "wave_id": "W3-interval-and-cohort-set",
            "depends_on": ["W1-as-prefix", "W2-window-path"],
            "unit_ids": ["OP-38", "OP-39"],
            "exit": "interval_intersection_and_fixed_cohort_prefix_set_contracts_implemented_and_tested",
        },
        {
            "wave_id": "W4-path-at-time",
            "depends_on": ["W0-source-and-governance", "W2-window-path"],
            "unit_ids": [
                "TOOL-11",
                *[f"OP-{index:02d}" for index in range(29, 34)],
                "OP-37",
            ],
            "exit": "materialized_route_state_checkpoint_projection_and_path_membership_contracts_implemented_and_tested",
        },
        {
            "wave_id": "W5-controlled-composition",
            "depends_on": ["W1-as-prefix", "W2-window-path", "W3-interval-and-cohort-set", "W4-path-at-time"],
            "unit_ids": ["PLAN-CAP-01", *list(EXPECTED_HOST_UNITS[2:])],
            "exit": "no_dynamic_fanout_plan_result_graph_commit_delivery_and_sol_host_ds_contracts_implemented",
        },
        {
            "wave_id": "W6-offline-certification",
            "depends_on": ["W5-controlled-composition"],
            "unit_ids": [],
            "exit": "28_question_same_binding_host_hard_gates_performance_cancel_rerun_and_boundary_evidence_passed",
        },
    ]


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


def _stage_artifact_digests(repo_root: Path, stage: str) -> dict[str, str]:
    if stage == "S1D-0":
        return {}
    if stage == "final":
        relatives = [
            relative
            for candidate_stage in STAGES[1:]
            for relative in ARTIFACTS_BY_STAGE[candidate_stage]
        ]
    else:
        relatives = list(ARTIFACTS_BY_STAGE[stage])
    return {
        relative.as_posix(): _sha256(repo_root / relative)
        for relative in relatives
    }


def _design_candidate_id(receipt: Mapping[str, Any]) -> str:
    """由阶段、父回执与本阶段内容摘要生成不可变设计候选身份。"""

    if receipt.get("stage") in ("S1D-6", "final"):
        final_design_candidate_id = receipt.get("final_design_candidate_id")
        if isinstance(final_design_candidate_id, str) and final_design_candidate_id:
            return final_design_candidate_id

    preimage = {
        "stage": receipt.get("stage"),
        "task_spec_sha256": receipt.get("task_spec_sha256"),
        "phase_plan_sha256": receipt.get("phase_plan_sha256"),
        "alignment_hook_sha256": receipt.get("alignment_hook_sha256"),
        "alignment_hook_tests_sha256": receipt.get("alignment_hook_tests_sha256"),
        "stage_artifact_sha256": receipt.get("stage_artifact_sha256"),
        "prior_receipts": receipt.get("prior_receipts"),
    }
    digest = _digest_without_fields(preimage)
    stage_slug = str(receipt.get("stage", "unknown")).lower()
    return f"country-outage-p2-s1-{stage_slug}-{digest[:24]}"


def _validate_prior_receipts(repo_root: Path, stage: str) -> dict[str, str]:
    required = _required_stages(stage)
    prior = required[:-1] if stage != "final" else required
    result: dict[str, str] = {}
    current_task_spec_sha256 = _sha256(repo_root / TASK_SPEC)
    current_phase_plan_sha256 = _sha256(repo_root / PHASE_PLAN)
    current_alignment_hook_sha256 = _sha256(repo_root / ALIGNMENT_HOOK)
    current_alignment_hook_tests_sha256 = _sha256(repo_root / ALIGNMENT_HOOK_TESTS)
    for prior_stage in prior:
        path = repo_root / RECEIPT_ROOT / f"{prior_stage}.json"
        payload = _load_json(path)
        if not isinstance(payload, dict):
            _fail("prior_receipt_invalid", f"阶段回执必须是 JSON object：{path}")
        if payload.get("stage") != prior_stage or payload.get("status") != "alignment_passed":
            _fail("prior_receipt_invalid", f"阶段回执状态或阶段错误：{path}")
        if payload.get("receipt_digest") != _canonical_digest(payload):
            _fail("prior_receipt_digest_mismatch", f"阶段回执摘要不匹配：{path}")
        if payload.get("design_candidate_id") != _design_candidate_id(payload):
            _fail("prior_candidate_identity_mismatch", f"阶段候选身份无法由内容重算：{path}")
        if payload.get("prior_receipts") != result:
            _fail("prior_receipt_chain_mismatch", f"阶段回执父链不连续：{path}")
        if payload.get("task_spec_sha256") != current_task_spec_sha256:
            _fail("prior_receipt_stale", f"阶段回执未绑定当前 Task Spec：{path}")
        if payload.get("phase_plan_sha256") != current_phase_plan_sha256:
            _fail("prior_receipt_stale", f"阶段回执未绑定当前阶段计划：{path}")
        if payload.get("alignment_hook_sha256") != current_alignment_hook_sha256:
            _fail("prior_receipt_stale", f"阶段回执未绑定当前 Alignment Hook：{path}")
        if payload.get("alignment_hook_tests_sha256") != current_alignment_hook_tests_sha256:
            _fail("prior_receipt_stale", f"阶段回执未绑定当前 Alignment Hook 测试：{path}")
        expected_artifacts = _stage_artifact_digests(repo_root, prior_stage)
        if payload.get("stage_artifact_sha256") != expected_artifacts:
            _fail("prior_receipt_stale", f"阶段回执未绑定当前阶段制品：{path}")
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
        "alignment_hook_sha256": _sha256(repo_root / ALIGNMENT_HOOK),
        "alignment_hook_tests_sha256": _sha256(repo_root / ALIGNMENT_HOOK_TESTS),
        "stage_artifact_sha256": _stage_artifact_digests(repo_root, stage),
        "prior_receipts": prior_receipts,
        "checks": checks,
        "design_only": True,
        "runtime_implemented": False,
        "production_deployed": False,
    }
    if stage in ("S1D-6", "final"):
        candidate = _load_json_strict(
            repo_root / ARTIFACTS_BY_STAGE["S1D-6"][0]
        )
        final_design_candidate_id = candidate.get("design_candidate_id")
        if not isinstance(final_design_candidate_id, str) or not final_design_candidate_id:
            _fail("candidate_identity_missing", "最终候选缺少可绑定的design_candidate_id")
        receipt["final_design_candidate_id"] = final_design_candidate_id
    receipt["design_candidate_id"] = _design_candidate_id(receipt)
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
