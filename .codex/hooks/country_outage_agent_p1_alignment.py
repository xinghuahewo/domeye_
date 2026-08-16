#!/usr/bin/env python3
"""P1 页面能力语义覆盖阶段 Alignment Hook。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from country_outage_agent_program_review import (
    load_project_config,
    machine_errors,
    read_text,
    requirement_ids,
    run_explicit_review,
    safe_repository_path,
)


STAGES = ("S0", "S1", "S2", "S3", "S4")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TASK_SPEC_VERSION = "p1-task-spec-v1.1-page-capability-coverage"
PLAN_VERSION = "p1-plan-v1.1-page-capability-coverage"
RECEIPT_SCHEMA_VERSION = "country_outage_p1_page_coverage_stage_receipt_v1"
PAGE_OUTCOME_IDS = tuple(f"PCO-{index:02d}" for index in range(1, 9))
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

REQUIRED_OUTCOMES_BY_STAGE = {
    "S0": set(PAGE_OUTCOME_IDS),
    "S1": {"PCO-03", "PCO-04"},
    "S2": set(PAGE_OUTCOME_IDS),
    "S3": set(PAGE_OUTCOME_IDS),
    "S4": set(PAGE_OUTCOME_IDS),
}

REQUIRED_ARTIFACT_KINDS = {
    "S0": {
        "page_capability_outcome_map",
        "product_semantic_truth",
        "question_explorer_contract",
    },
    "S1": {
        "deterministic_series_operator_oracle",
        "ip_question_execution_trace",
        "independent_semantic_review",
    },
    "S2": {
        "question_explorer_results",
        "single_turn_semantic_diff",
        "independent_semantic_review",
    },
    "S3": {
        "multiturn_state_trace",
        "mixed_boundary_trace",
        "failure_rollback_trace",
        "independent_semantic_review",
    },
    "S4": {
        "same_candidate_manifest",
        "browser_api_tool_evidence_state_trace",
        "independent_semantic_review",
        "unclosed_unknowns",
    },
}

PROHIBITED_CLAIMS = (
    "p2_complete",
    "rca_complete",
    "deployed",
    "production_verified",
)

ACCEPTANCE_GUARD_PHRASES = (
    "Page Capability Outcome Map",
    "`IP` 默认表示 `IPv4 + IPv6`",
    "默认附带窗口内新出现前缀作为独立补充",
    "不得因为出现“趋势”二字就升级为不可用的正式趋势制品",
    "问题探针 Agent 不得同时充当最终判卷者",
    "不得只添加针对该句的规则",
    "“IP 地址变化情况”",
    "“IP 地址变化趋势”",
)

PLAN_GUARD_PHRASES = (
    "泛指 IP 默认同时回答 IPv4 和 IPv6",
    "固定 cohort 是主答案，新出现前缀是独立补充",
    "事件内时序概括与历史/跨事件/正式趋势制品分离",
    "问题探针 Agent 与独立产品语义 Reviewer",
    "country_outage_p1_page_coverage_stage_receipt_v1",
    "--evidence-manifest",
    "无 evidence manifest 的调用只证明文档",
)


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="回检 P1 页面能力语义覆盖阶段是否偏离最终验收文档。",
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=STAGES,
        help="刚结束的 P1 覆盖加固阶段。",
    )
    parser.add_argument(
        "--evidence-manifest",
        help=(
            "阶段结束回执的仓库内相对路径。不提供时只做设计结构检查，"
            "不证明阶段出口。"
        ),
    )
    return parser.parse_args(argv)


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法读取阶段回执 {path}：{error}") from error
    if not isinstance(value, dict):
        raise RuntimeError("阶段回执必须是 JSON 对象")
    return value


def flatten_due_requirements(config: dict[str, Any], stage: str) -> set[str]:
    return {
        requirement_id
        for values in config["stage_due"][stage].values()
        for requirement_id in values
    }


def all_known_requirements(config: dict[str, Any]) -> set[str]:
    return {
        requirement_id
        for values in requirement_ids(config).values()
        for requirement_id in values
    }


def validate_page_coverage_contract(
    config: dict[str, Any],
    acceptance: str,
    plan: str,
) -> list[str]:
    """检查不能通过同步改配置与文档轻易删除的产品语义。"""

    errors: list[str] = []
    if f"版本：`{TASK_SPEC_VERSION}`" not in acceptance:
        errors.append(f"Task Spec 版本必须为 {TASK_SPEC_VERSION}")
    if f"版本：`{PLAN_VERSION}`" not in plan:
        errors.append(f"Plan 版本必须为 {PLAN_VERSION}")

    for phrase in ACCEPTANCE_GUARD_PHRASES:
        if phrase not in acceptance:
            errors.append(f"改进目标文档缺少页面覆盖防偏离语义：{phrase}")
    for phrase in PLAN_GUARD_PHRASES:
        if phrase not in plan:
            errors.append(f"分阶段计划缺少页面覆盖封口语义：{phrase}")

    for outcome_id in PAGE_OUTCOME_IDS:
        if outcome_id not in acceptance:
            errors.append(f"改进目标文档缺少页面用户结果：{outcome_id}")
        if outcome_id not in plan:
            errors.append(f"分阶段计划缺少页面用户结果：{outcome_id}")

    for stage, stage_name in config["stage_names"].items():
        if f"### {stage}：{stage_name}" not in plan:
            errors.append(f"分阶段计划的 {stage} 标题与 Hook 配置不一致")
    return errors


def artifact_path(value: object, label: str, artifact_root: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    resolved = (artifact_root / relative).resolve()
    try:
        resolved.relative_to(artifact_root.resolve())
    except ValueError:
        return None
    return resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_timestamp(value: object, label: str) -> tuple[datetime | None, list[str]]:
    if not isinstance(value, str) or not value:
        return None, [f"{label} 必须是非空 ISO-8601 时间"]
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, [f"{label} 不是有效 ISO-8601 时间：{value!r}"]
    if parsed.tzinfo is None:
        return None, [f"{label} 必须包含时区：{value!r}"]
    return parsed, []


def validate_evidence_refs(
    *,
    value: object,
    owner: Path,
    artifact_root: Path,
    candidate_id: str,
    stage: str,
) -> tuple[list[str], dict[str, str], dict[str, dict[str, Any]]]:
    errors: list[str] = []
    hashes_by_kind: dict[str, str] = {}
    values_by_kind: dict[str, dict[str, Any]] = {}
    if not isinstance(value, list) or not value:
        return [f"制品 {owner.name} 必须包含非空 evidence_refs 对象数组"], {}, {}
    for index, ref in enumerate(value):
        label = f"{owner.name}.evidence_refs[{index}]"
        if not isinstance(ref, dict):
            errors.append(f"{label} 必须是对象，不能是自报字符串")
            continue
        kind = ref.get("kind")
        raw_path = ref.get("path")
        expected_sha = ref.get("sha256")
        if not isinstance(kind, str) or not kind:
            errors.append(f"{label}.kind 必须是非空字符串")
            continue
        if kind in hashes_by_kind:
            errors.append(f"{label}.kind 重复：{kind}")
            continue
        target = artifact_path(raw_path, f"{label}.path", artifact_root)
        if target is None:
            errors.append(f"{label}.path 必须是证据根目录内相对路径")
            continue
        if not target.is_file():
            errors.append(f"{label}.path 引用文件不存在：{raw_path}")
            continue
        if not isinstance(expected_sha, str) or not SHA256_PATTERN.fullmatch(expected_sha):
            errors.append(f"{label}.sha256 必须是小写 64 位 SHA-256")
            continue
        actual_sha = sha256_file(target)
        if actual_sha != expected_sha:
            errors.append(f"{label} SHA-256 不匹配：{actual_sha} != {expected_sha}")
            continue
        hashes_by_kind[kind] = expected_sha
        try:
            raw_value = load_json_object(target)
        except RuntimeError as error:
            errors.append(f"{label} 必须指向可解析的 JSON 原始回执：{error}")
            continue
        raw_expected = {
            "evidence_kind": kind,
            "candidate_id": candidate_id,
            "stage": stage,
        }
        for raw_key, raw_expected_value in raw_expected.items():
            if raw_value.get(raw_key) != raw_expected_value:
                errors.append(
                    f"{label} 原始回执 {raw_key} 不一致："
                    f"{raw_value.get(raw_key)!r} != {raw_expected_value!r}"
                )
        if not isinstance(raw_value.get("schema_version"), str) or not raw_value["schema_version"]:
            errors.append(f"{label} 原始回执缺少非空 schema_version")
        if not isinstance(raw_value.get("run_id"), str) or not raw_value["run_id"]:
            errors.append(f"{label} 原始回执缺少非空 run_id")
        _, timestamp_errors = parse_timestamp(
            raw_value.get("captured_at"),
            f"{label}.captured_at",
        )
        errors.extend(timestamp_errors)
        values_by_kind[kind] = raw_value
    return errors, hashes_by_kind, values_by_kind


def validate_artifact_envelope(
    *,
    path: Path,
    kind: str,
    stage: str,
    candidate_id: str,
    artifact_root: Path,
) -> list[str]:
    errors: list[str] = []
    try:
        value = load_json_object(path)
    except RuntimeError as error:
        return [str(error)]
    expected = {
        "artifact_kind": kind,
        "stage": stage,
        "candidate_id": candidate_id,
        "status": "PASS",
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            errors.append(
                f"制品 {path.name} 的 {key} 不一致："
                f"{value.get(key)!r} != {expected_value!r}"
            )
    if not isinstance(value.get("schema_version"), str) or not value["schema_version"]:
        errors.append(f"制品 {path.name} 缺少非空 schema_version")
    ref_errors, evidence_hashes, evidence_values = validate_evidence_refs(
        value=value.get("evidence_refs"),
        owner=path,
        artifact_root=artifact_root,
        candidate_id=candidate_id,
        stage=stage,
    )
    errors.extend(ref_errors)

    if kind in {"page_capability_outcome_map", "question_explorer_results"}:
        outcomes = value.get("page_outcome_ids")
        if not isinstance(outcomes, list) or set(outcomes) != set(PAGE_OUTCOME_IDS):
            errors.append(f"制品 {path.name} 必须完整覆盖 PCO-01 至 PCO-08")
    if kind == "ip_question_execution_trace":
        questions = value.get("questions")
        expected_questions = {"IP地址变化情况", "IP地址变化趋势"}
        if not isinstance(questions, list) or not expected_questions.issubset(
            set(questions)
        ):
            errors.append(f"制品 {path.name} 缺少两个冻结 IP 原问题")
    if kind == "question_explorer_contract":
        actor = value.get("question_explorer_actor_id")
        if not isinstance(actor, str) or not actor:
            errors.append(f"制品 {path.name} 缺少 question_explorer_actor_id")
        allowed = value.get("allowed_actions")
        denied = value.get("denied_actions")
        if not isinstance(allowed, list) or not allowed:
            errors.append(f"制品 {path.name} 缺少非空 allowed_actions")
        if not isinstance(denied, list) or not denied:
            errors.append(f"制品 {path.name} 缺少非空 denied_actions")
        elif not {"write_truth", "mark_pass"}.issubset(set(denied)):
            errors.append(f"制品 {path.name} 未禁止问题探针写真值或自行判定 PASS")
    if kind in {"product_semantic_truth", "independent_semantic_review"}:
        if value.get("reviewer_role") != "product_semantic_truth_reviewer":
            errors.append(f"制品 {path.name} 的 Reviewer 角色不是产品语义真值审核")
        if value.get("independent_from_question_explorer") is not True:
            errors.append(f"制品 {path.name} 未证明 Reviewer 与问题探针分离")
        if value.get("verdict") != "PASS":
            errors.append(f"制品 {path.name} 的 Reviewer verdict 必须为 PASS")
        reviewed_items = value.get("reviewed_items")
        if not isinstance(reviewed_items, list) or not reviewed_items:
            errors.append(f"制品 {path.name} 缺少 reviewed_items")
        else:
            for index, item in enumerate(reviewed_items):
                label = f"{path.name}.reviewed_items[{index}]"
                if not isinstance(item, dict):
                    errors.append(f"{label} 必须是审核对象")
                    continue
                if not isinstance(item.get("case_id"), str) or not item["case_id"]:
                    errors.append(f"{label} 缺少 case_id")
                if item.get("verdict") not in {"PASS", "FAIL"}:
                    errors.append(f"{label}.verdict 必须为 PASS 或 FAIL")
                elif value.get("verdict") == "PASS" and item.get("verdict") != "PASS":
                    errors.append(f"{label}.verdict 为 FAIL 时不得上卷为 Reviewer 总体 PASS")
                if not isinstance(item.get("semantic_diff"), list):
                    errors.append(f"{label}.semantic_diff 必须是数组")
        case_author = value.get("case_author_actor_id")
        reviewer = value.get("reviewer_actor_id")
        if not isinstance(case_author, str) or not case_author:
            errors.append(f"制品 {path.name} 缺少 case_author_actor_id")
        if not isinstance(reviewer, str) or not reviewer:
            errors.append(f"制品 {path.name} 缺少 reviewer_actor_id")
        if isinstance(case_author, str) and case_author == reviewer:
            errors.append(f"制品 {path.name} 的出题者与 Reviewer actor 不得相同")
        case_author_run = value.get("case_author_run_id")
        reviewer_run = value.get("reviewer_run_id")
        if not isinstance(case_author_run, str) or not case_author_run:
            errors.append(f"制品 {path.name} 缺少 case_author_run_id")
        if not isinstance(reviewer_run, str) or not reviewer_run:
            errors.append(f"制品 {path.name} 缺少 reviewer_run_id")
        if isinstance(case_author_run, str) and case_author_run == reviewer_run:
            errors.append(f"制品 {path.name} 的出题与 Reviewer run 不得相同")
        blind_at, timestamp_errors = parse_timestamp(
            value.get("blind_truth_created_at"),
            f"{path.name}.blind_truth_created_at",
        )
        errors.extend(timestamp_errors)
        reveal_at, timestamp_errors = parse_timestamp(
            value.get("system_output_revealed_at"),
            f"{path.name}.system_output_revealed_at",
        )
        errors.extend(timestamp_errors)
        completed_at, timestamp_errors = parse_timestamp(
            value.get("review_completed_at"),
            f"{path.name}.review_completed_at",
        )
        errors.extend(timestamp_errors)
        if (
            blind_at is not None
            and reveal_at is not None
            and completed_at is not None
            and not blind_at < reveal_at <= completed_at
        ):
            errors.append(f"制品 {path.name} 未证明先产生盲审真值、后查看系统输出")
        review_hash_fields = {
            "reviewed_input_sha256": "reviewed_input",
            "blind_truth_sha256": "blind_truth",
            "system_output_sha256": "system_output",
            "case_author_actor_receipt_sha256": "case_author_actor_receipt",
            "reviewer_actor_receipt_sha256": "reviewer_actor_receipt",
        }
        for field, evidence_kind in review_hash_fields.items():
            expected_hash = evidence_hashes.get(evidence_kind)
            if value.get(field) != expected_hash or expected_hash is None:
                errors.append(
                    f"制品 {path.name} 的 {field} 必须绑定已验签 {evidence_kind}"
                )
        actor_specs = {
            "case_author_actor_receipt": (
                case_author,
                case_author_run,
                {"mark_pass", "modify_implementation"},
            ),
            "reviewer_actor_receipt": (
                reviewer,
                reviewer_run,
                {"generate_probe_cases", "modify_implementation"},
            ),
        }
        orchestrator_receipt_ids: list[str] = []
        for evidence_kind, (expected_actor, expected_run, required_denied) in actor_specs.items():
            actor_receipt = evidence_values.get(evidence_kind)
            if actor_receipt is None:
                errors.append(f"制品 {path.name} 缺少已验签 {evidence_kind}")
                continue
            if actor_receipt.get("actor_id") != expected_actor:
                errors.append(f"制品 {path.name} 的 {evidence_kind}.actor_id 未绑定角色回执")
            if actor_receipt.get("run_id") != expected_run:
                errors.append(f"制品 {path.name} 的 {evidence_kind}.run_id 未绑定角色运行")
            denied_actions = actor_receipt.get("denied_actions")
            if not isinstance(denied_actions, list) or not required_denied.issubset(
                set(denied_actions)
            ):
                errors.append(
                    f"制品 {path.name} 的 {evidence_kind} 未关闭分权禁止项"
                )
            if not isinstance(actor_receipt.get("orchestrator_receipt_id"), str) or not actor_receipt["orchestrator_receipt_id"]:
                errors.append(f"制品 {path.name} 的 {evidence_kind} 缺少 orchestrator_receipt_id")
            else:
                orchestrator_receipt_ids.append(actor_receipt["orchestrator_receipt_id"])
        if len(orchestrator_receipt_ids) == 2 and len(set(orchestrator_receipt_ids)) != 2:
            errors.append(f"制品 {path.name} 的出题与 Reviewer 不得复用 orchestrator_receipt_id")
        reviewed_input = evidence_values.get("reviewed_input")
        if reviewed_input is not None:
            if (
                reviewed_input.get("actor_id") != case_author
                or reviewed_input.get("run_id") != case_author_run
            ):
                errors.append(f"制品 {path.name} 的被审输入未绑定出题者独立运行")
        blind_truth = evidence_values.get("blind_truth")
        if blind_truth is not None:
            if (
                blind_truth.get("actor_id") != reviewer
                or blind_truth.get("run_id") != reviewer_run
            ):
                errors.append(f"制品 {path.name} 的先验真值未绑定 Reviewer 独立运行")
            if blind_truth.get("reviewed_input_sha256") != evidence_hashes.get("reviewed_input"):
                errors.append(f"制品 {path.name} 的先验真值未绑定被审输入")
            if blind_truth.get("captured_at") != value.get("blind_truth_created_at"):
                errors.append(f"制品 {path.name} 的先验真值时间与原始回执不一致")
            truth_items = blind_truth.get("truth_items")
            if not isinstance(truth_items, list) or not truth_items:
                errors.append(f"制品 {path.name} 的先验真值缺少非空 truth_items")
            else:
                for index, item in enumerate(truth_items):
                    label = f"{path.name}.truth_items[{index}]"
                    if not isinstance(item, dict):
                        errors.append(f"{label} 必须是真值对象")
                        continue
                    required_nonempty_lists = (
                        "expected_goals",
                        "required_answer_points",
                        "forbidden_claims",
                    )
                    if not isinstance(item.get("case_id"), str) or not item["case_id"]:
                        errors.append(f"{label} 缺少 case_id")
                    for field in required_nonempty_lists:
                        if not isinstance(item.get(field), list) or not item[field]:
                            errors.append(f"{label}.{field} 必须是非空数组")
                    if not isinstance(item.get("expected_entities"), dict):
                        errors.append(f"{label}.expected_entities 必须是对象")
                    if item.get("answerability") not in {
                        "supported", "partial", "unsupported", "clarify",
                        "invalid_data", "insufficient_evidence",
                    }:
                        errors.append(f"{label}.answerability 不在受控枚举中")
                truth_case_ids = {
                    item.get("case_id")
                    for item in truth_items
                    if isinstance(item, dict) and isinstance(item.get("case_id"), str)
                }
                reviewed_case_ids = {
                    item.get("case_id")
                    for item in reviewed_items
                    if isinstance(item, dict) and isinstance(item.get("case_id"), str)
                } if isinstance(reviewed_items, list) else set()
                if reviewed_case_ids != truth_case_ids:
                    errors.append(f"制品 {path.name} 的 reviewed_items 未与先验真值案例集对齐")
        system_output = evidence_values.get("system_output")
        if system_output is not None:
            if system_output.get("reviewed_input_sha256") != evidence_hashes.get("reviewed_input"):
                errors.append(f"制品 {path.name} 的系统输出未绑定被审输入")
            if system_output.get("captured_at") != value.get("system_output_revealed_at"):
                errors.append(f"制品 {path.name} 的系统输出时间与原始回执不一致")
        if stage == "S2":
            for field in (
                "question_explorer_receipt_sha256",
                "question_explorer_cases_sha256",
            ):
                expected_value = value.get(field)
                if not isinstance(expected_value, str) or not SHA256_PATTERN.fullmatch(expected_value):
                    errors.append(f"制品 {path.name} 缺少 {field}")
                elif reviewed_input is not None and reviewed_input.get(field) != expected_value:
                    errors.append(f"制品 {path.name} 的 {field} 未与被审输入绑定")
        if stage == "S4":
            review_candidate_identity = value.get("candidate_identity_sha256")
            if not isinstance(review_candidate_identity, str) or not SHA256_PATTERN.fullmatch(review_candidate_identity):
                errors.append(f"制品 {path.name} 缺少 candidate_identity_sha256")
            for evidence_kind in ("reviewed_input", "blind_truth", "system_output"):
                raw_receipt = evidence_values.get(evidence_kind)
                if (
                    raw_receipt is not None
                    and raw_receipt.get("candidate_identity_sha256")
                    != review_candidate_identity
                ):
                    errors.append(f"制品 {path.name} 的 {evidence_kind} 未绑定被审候选身份")
    if kind == "question_explorer_results":
        actor = value.get("question_explorer_actor_id")
        if not isinstance(actor, str) or not actor:
            errors.append(f"制品 {path.name} 缺少 question_explorer_actor_id")
        explorer_run = value.get("question_explorer_run_id")
        if not isinstance(explorer_run, str) or not explorer_run:
            errors.append(f"制品 {path.name} 缺少 question_explorer_run_id")
        cases = value.get("cases")
        if not isinstance(cases, list) or not cases:
            errors.append(f"制品 {path.name} 缺少非空 cases")
        else:
            case_ids: list[str] = []
            covered_outcomes: set[str] = set()
            for index, case in enumerate(cases):
                label = f"{path.name}.cases[{index}]"
                if not isinstance(case, dict):
                    errors.append(f"{label} 必须是案例对象")
                    continue
                for field in (
                    "case_id", "expression_type", "persona", "question",
                    "candidate_id", "raw_agent_receipt_ref",
                ):
                    if not isinstance(case.get(field), str) or not case[field]:
                        errors.append(f"{label} 缺少非空 {field}")
                if isinstance(case.get("case_id"), str):
                    case_ids.append(case["case_id"])
                if case.get("candidate_id") != candidate_id:
                    errors.append(f"{label}.candidate_id 与阶段候选不一致")
                outcomes = case.get("page_outcome_ids")
                if (
                    not isinstance(outcomes, list)
                    or not outcomes
                    or not set(outcomes).issubset(PAGE_OUTCOME_IDS)
                ):
                    errors.append(f"{label}.page_outcome_ids 必须是非空受控 PCO 数组")
                else:
                    covered_outcomes.update(outcomes)
                if not isinstance(case.get("conversation_seed"), list):
                    errors.append(f"{label}.conversation_seed 必须是数组")
                if not isinstance(case.get("event_identity"), dict) or not case["event_identity"]:
                    errors.append(f"{label}.event_identity 必须是非空对象")
                if case.get("review_status") not in {"candidate", "frozen", "rejected"}:
                    errors.append(f"{label}.review_status 不在受控枚举中")
            if len(case_ids) != len(set(case_ids)):
                errors.append(f"制品 {path.name} 的 case_id 必须唯一")
            declared_outcomes = value.get("page_outcome_ids")
            if isinstance(declared_outcomes, list) and covered_outcomes != set(
                declared_outcomes
            ):
                errors.append(
                    f"制品 {path.name} 的 cases 未实际覆盖声明的全部 PCO"
                )
        raw_explorer_sha = evidence_hashes.get("raw_agent_receipts")
        raw_explorer = evidence_values.get("raw_agent_receipts")
        if raw_explorer_sha is None or raw_explorer is None:
            errors.append(f"制品 {path.name} 缺少已验签 raw_agent_receipts")
        else:
            if value.get("raw_agent_receipts_sha256") != raw_explorer_sha:
                errors.append(f"制品 {path.name} 未绑定 raw_agent_receipts SHA-256")
            if (
                raw_explorer.get("actor_id") != actor
                or raw_explorer.get("run_id") != explorer_run
            ):
                errors.append(f"制品 {path.name} 的原始探针回执未绑定探针 actor/run")
            if isinstance(cases, list):
                cases_digest = hashlib.sha256(
                    json.dumps(cases, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                if (
                    value.get("cases_sha256") != cases_digest
                    or raw_explorer.get("cases_sha256") != cases_digest
                ):
                    errors.append(f"制品 {path.name} 的 cases 未与原始探针回执绑定")
    if kind == "same_candidate_manifest":
        components = value.get("component_identities")
        required_components = {
            "frontend", "backend", "runtime", "semantic_planner", "model",
            "prompt", "schema", "capability_catalog", "policy",
            "tool_contracts", "operator_contracts", "oracle", "data_publication",
        }
        if not isinstance(components, dict):
            errors.append(f"制品 {path.name} 缺少 component_identities")
        else:
            missing = required_components - set(components)
            if missing:
                errors.append(f"制品 {path.name} 缺少同候选组件身份：{sorted(missing)}")
            for component in sorted(required_components - missing):
                component_value = components.get(component)
                evidence_kind = f"component_{component}"
                if not isinstance(component_value, dict):
                    errors.append(f"制品 {path.name} 的组件 {component} 必须是身份对象")
                    continue
                identity = component_value.get("identity")
                if not isinstance(identity, str) or not identity:
                    errors.append(f"制品 {path.name} 的组件 {component} 缺少 identity")
                if component_value.get("evidence_kind") != evidence_kind:
                    errors.append(f"制品 {path.name} 的组件 {component} evidence_kind 不一致")
                evidence_sha = evidence_hashes.get(evidence_kind)
                if component_value.get("sha256") != evidence_sha or evidence_sha is None:
                    errors.append(f"制品 {path.name} 的组件 {component} 未绑定已验签源回执")
                component_receipt_value = evidence_values.get(evidence_kind)
                if component_receipt_value is None:
                    errors.append(f"制品 {path.name} 的组件 {component} 缺少原始回执")
                elif (
                    component_receipt_value.get("component") != component
                    or component_receipt_value.get("identity") != identity
                ):
                    errors.append(f"制品 {path.name} 的组件 {component} 声明与原始回执不一致")
            component_digest = hashlib.sha256(
                json.dumps(components, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if value.get("candidate_identity_sha256") != component_digest:
                errors.append(f"制品 {path.name} 的 candidate_identity_sha256 未绑定组件身份")
            component_receipt = evidence_values.get("component_manifest")
            if component_receipt is None:
                errors.append(f"制品 {path.name} 缺少已验签 component_manifest")
            elif (
                component_receipt.get("component_identities") != components
                or component_receipt.get("candidate_identity_sha256") != component_digest
            ):
                errors.append(f"制品 {path.name} 的 component_manifest 与同候选身份不一致")
    if kind == "browser_api_tool_evidence_state_trace":
        journeys = value.get("journeys")
        required_hashes = {
            "browser_receipt_sha256": "browser_receipt",
            "api_receipt_sha256": "api_receipt",
            "user_goal_plan_sha256": "user_goal_plan",
            "grounding_plan_sha256": "grounding_plan",
            "tool_receipts_sha256": "tool_receipts",
            "evidence_state_sha256": "evidence_state",
            "dialog_state_before_sha256": "dialog_state_before",
            "dialog_state_after_sha256": "dialog_state_after",
        }
        if not isinstance(journeys, list) or not journeys:
            errors.append(f"制品 {path.name} 缺少非空 journeys")
        else:
            for index, journey in enumerate(journeys):
                label = f"{path.name}.journeys[{index}]"
                if not isinstance(journey, dict):
                    errors.append(f"{label} 必须是对象")
                    continue
                if journey.get("candidate_id") != candidate_id:
                    errors.append(f"{label}.candidate_id 与阶段候选不一致")
                if not isinstance(journey.get("journey_id"), str) or not journey["journey_id"]:
                    errors.append(f"{label} 缺少 journey_id")
                if not isinstance(journey.get("run_id"), str) or not journey["run_id"]:
                    errors.append(f"{label} 缺少 run_id")
                if not isinstance(journey.get("candidate_identity_sha256"), str) or not SHA256_PATTERN.fullmatch(journey["candidate_identity_sha256"]):
                    errors.append(f"{label} 缺少 candidate_identity_sha256")
                for field, evidence_kind in required_hashes.items():
                    expected_hash = evidence_hashes.get(evidence_kind)
                    if journey.get(field) != expected_hash or expected_hash is None:
                        errors.append(f"{label}.{field} 必须绑定已验签 {evidence_kind}")
                    raw_receipt = evidence_values.get(evidence_kind)
                    if raw_receipt is not None:
                        if raw_receipt.get("journey_id") != journey.get("journey_id"):
                            errors.append(f"{label}.{field} 的 journey_id 不一致")
                        if raw_receipt.get("run_id") != journey.get("run_id"):
                            errors.append(f"{label}.{field} 的 run_id 不一致")
                        if raw_receipt.get("candidate_identity_sha256") != journey.get("candidate_identity_sha256"):
                            errors.append(f"{label}.{field} 的组件候选身份不一致")
    if kind == "unclosed_unknowns":
        unknowns = value.get("unknowns")
        if not isinstance(unknowns, list):
            errors.append(f"制品 {path.name} 的 unknowns 必须是数组")
        else:
            for index, unknown in enumerate(unknowns):
                label = f"{path.name}.unknowns[{index}]"
                if not isinstance(unknown, dict):
                    errors.append(f"{label} 必须是对象")
                    continue
                for field in ("unknown_id", "subject", "next_validation", "owner"):
                    if not isinstance(unknown.get(field), str) or not unknown[field]:
                        errors.append(f"{label} 缺少非空 {field}")
                if not isinstance(unknown.get("blocking"), bool):
                    errors.append(f"{label}.blocking 必须是布尔值")
            computed_blocking_count = sum(
                1
                for unknown in unknowns
                if isinstance(unknown, dict) and unknown.get("blocking") is True
            )
            if value.get("blocking_count") != computed_blocking_count:
                errors.append(f"制品 {path.name} 的 blocking_count 与 unknowns 计算值不一致")
        if value.get("blocking_count") != 0:
            errors.append(f"制品 {path.name} 的 blocking_count 必须为 0")
    return errors


def validate_stage_receipt(
    config: dict[str, Any],
    stage: str,
    receipt: dict[str, Any],
    *,
    artifact_root: Path,
) -> list[str]:
    errors: list[str] = []
    expected_identity = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "stage": stage,
        "task_spec_version": TASK_SPEC_VERSION,
        "plan_version": PLAN_VERSION,
        "status": "PASS",
    }
    for key, expected in expected_identity.items():
        if receipt.get(key) != expected:
            errors.append(
                f"阶段回执 {key} 不一致：{receipt.get(key)!r} != {expected!r}"
            )

    candidate_id = receipt.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        errors.append("阶段回执缺少非空 candidate_id")

    raw_requirements = receipt.get("requirement_ids")
    if not isinstance(raw_requirements, list) or not all(
        isinstance(item, str) for item in raw_requirements
    ):
        errors.append("阶段回执 requirement_ids 必须是字符串数组")
    else:
        actual_requirements = set(raw_requirements)
        due = flatten_due_requirements(config, stage)
        missing = due - actual_requirements
        unknown = actual_requirements - all_known_requirements(config)
        if missing:
            errors.append(f"阶段回执缺少到期要求：{sorted(missing)}")
        if unknown:
            errors.append(f"阶段回执包含未知要求：{sorted(unknown)}")

    raw_outcomes = receipt.get("page_outcome_ids")
    if not isinstance(raw_outcomes, list) or not all(
        isinstance(item, str) for item in raw_outcomes
    ):
        errors.append("阶段回执 page_outcome_ids 必须是字符串数组")
    else:
        actual_outcomes = set(raw_outcomes)
        unknown_outcomes = actual_outcomes - set(PAGE_OUTCOME_IDS)
        missing_outcomes = REQUIRED_OUTCOMES_BY_STAGE[stage] - actual_outcomes
        if unknown_outcomes:
            errors.append(f"阶段回执包含未知 PCO：{sorted(unknown_outcomes)}")
        if missing_outcomes:
            errors.append(f"阶段回执缺少到期 PCO：{sorted(missing_outcomes)}")

    artifacts = receipt.get("artifacts")
    artifact_kinds: set[str] = set()
    artifact_paths: set[str] = set()
    normalized_artifact_paths: set[Path] = set()
    artifact_kind_by_path: dict[str, str] = {}
    artifact_values_by_kind: dict[str, dict[str, Any]] = {}
    artifact_values_by_path: dict[str, dict[str, Any]] = {}
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("阶段回执 artifacts 必须是非空数组")
    else:
        for index, artifact in enumerate(artifacts):
            label = f"artifacts[{index}]"
            if not isinstance(artifact, dict):
                errors.append(f"{label} 必须是对象")
                continue
            kind = artifact.get("kind")
            raw_path = artifact.get("path")
            expected_sha = artifact.get("sha256")
            if not isinstance(kind, str) or not kind:
                errors.append(f"{label}.kind 必须是非空字符串")
            else:
                if kind in artifact_kinds:
                    errors.append(f"{label}.kind 重复：{kind}")
                artifact_kinds.add(kind)
            path = artifact_path(raw_path, f"{label}.path", artifact_root)
            if path is None:
                errors.append(f"{label}.path 必须是制品根目录内相对路径")
            else:
                assert isinstance(raw_path, str)
                if path in normalized_artifact_paths:
                    errors.append(f"{label}.path 规范化后重复：{raw_path}")
                normalized_artifact_paths.add(path)
                artifact_paths.add(raw_path)
                if isinstance(kind, str):
                    artifact_kind_by_path[raw_path] = kind
                if not path.is_file():
                    errors.append(f"{label}.path 制品不存在：{raw_path}")
                elif not isinstance(expected_sha, str) or not SHA256_PATTERN.fullmatch(
                    expected_sha
                ):
                    errors.append(f"{label}.sha256 必须是小写 64 位 SHA-256")
                else:
                    actual_sha = sha256_file(path)
                    if actual_sha != expected_sha:
                        errors.append(
                            f"{label} 制品 SHA-256 不匹配："
                            f"{actual_sha} != {expected_sha}"
                        )
                    elif isinstance(kind, str) and isinstance(candidate_id, str):
                        errors.extend(
                            validate_artifact_envelope(
                                path=path,
                                kind=kind,
                                stage=stage,
                                candidate_id=candidate_id,
                                artifact_root=artifact_root,
                            )
                        )
                        try:
                            artifact_value = load_json_object(path)
                            artifact_values_by_kind[kind] = artifact_value
                            artifact_values_by_path[raw_path] = artifact_value
                        except RuntimeError:
                            pass
        missing_kinds = REQUIRED_ARTIFACT_KINDS[stage] - artifact_kinds
        if missing_kinds:
            errors.append(f"阶段回执缺少必需制品类型：{sorted(missing_kinds)}")

    selected_review: dict[str, Any] | None = None
    semantic_review = receipt.get("semantic_review")
    if not isinstance(semantic_review, dict):
        errors.append("阶段回执缺少 semantic_review 对象")
    else:
        if semantic_review.get("role_separated") is not True:
            errors.append("独立产品语义 Reviewer 必须与问题探针分离")
        if semantic_review.get("verdict") != "PASS":
            errors.append("阶段回执的独立产品语义结论必须为 PASS")
        receipt_ref = semantic_review.get("receipt_ref")
        if not isinstance(receipt_ref, str) or receipt_ref not in artifact_paths:
            errors.append("semantic_review.receipt_ref 必须指向已验签制品")
        else:
            expected_review_kind = (
                "product_semantic_truth" if stage == "S0" else "independent_semantic_review"
            )
            if artifact_kind_by_path.get(receipt_ref) != expected_review_kind:
                errors.append(
                    "semantic_review.receipt_ref 必须指向当前阶段的 "
                    f"{expected_review_kind} 制品"
                )
            else:
                selected_review = artifact_values_by_path.get(receipt_ref)

    explorer = artifact_values_by_kind.get("question_explorer_results")
    review = selected_review if stage != "S0" else None
    if explorer is not None and review is not None:
        if explorer.get("question_explorer_actor_id") != review.get("case_author_actor_id"):
            errors.append("独立 Reviewer 的 case_author_actor_id 未绑定当前问题探针 actor")
        if explorer.get("question_explorer_run_id") != review.get("case_author_run_id"):
            errors.append("独立 Reviewer 的 case_author_run_id 未绑定当前问题探针 run")
        if (
            explorer.get("raw_agent_receipts_sha256")
            != review.get("question_explorer_receipt_sha256")
        ):
            errors.append("独立 Reviewer 的被审输入未绑定当前探针原始回执")
        if (
            explorer.get("cases_sha256")
            != review.get("question_explorer_cases_sha256")
        ):
            errors.append("独立 Reviewer 的被审输入未绑定当前探针案例集")
        explorer_case_ids = {
            item.get("case_id")
            for item in explorer.get("cases", [])
            if isinstance(item, dict) and isinstance(item.get("case_id"), str)
        }
        reviewed_case_ids = {
            item.get("case_id")
            for item in review.get("reviewed_items", [])
            if isinstance(item, dict) and isinstance(item.get("case_id"), str)
        }
        if explorer_case_ids != reviewed_case_ids:
            errors.append("独立 Reviewer 未逐案审核当前问题探针案例集")

    candidate_manifest = artifact_values_by_kind.get("same_candidate_manifest")
    journey_trace = artifact_values_by_kind.get(
        "browser_api_tool_evidence_state_trace"
    )
    if candidate_manifest is not None and journey_trace is not None:
        manifest_identity = candidate_manifest.get("candidate_identity_sha256")
        if review is not None and review.get("candidate_identity_sha256") != manifest_identity:
            errors.append("独立 Reviewer 回执未绑定 same_candidate_manifest")
        journeys = journey_trace.get("journeys")
        if isinstance(journeys, list):
            for index, journey in enumerate(journeys):
                if (
                    isinstance(journey, dict)
                    and journey.get("candidate_identity_sha256") != manifest_identity
                ):
                    errors.append(
                        "browser/API/Tool/Evidence/State 旅程 "
                        f"journeys[{index}] 未绑定 same_candidate_manifest"
                    )

    blockers = receipt.get("unresolved_blockers")
    if not isinstance(blockers, list):
        errors.append("阶段回执 unresolved_blockers 必须是数组")
    elif blockers:
        errors.append("阶段回执仍存在未关闭阻断，不得标记 PASS")

    claims = receipt.get("prohibited_claims")
    if not isinstance(claims, dict):
        errors.append("阶段回执缺少 prohibited_claims 对象")
    else:
        for claim in PROHIBITED_CLAIMS:
            if claims.get(claim) is not False:
                errors.append(f"阶段回执禁止越级声明 {claim} 必须为 false")
    return errors


def contract_errors(config: dict[str, Any]) -> list[str]:
    errors = machine_errors(config)
    try:
        acceptance_path = safe_repository_path(
            config["acceptance_path"], "acceptance_path"
        )
        plan_path = safe_repository_path(config["plan_path"], "plan_path")
        acceptance = read_text(acceptance_path)
        plan = read_text(plan_path)
    except RuntimeError as error:
        return errors + [str(error)]
    return errors + validate_page_coverage_contract(config, acceptance, plan)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    try:
        config = load_project_config("P1")
    except RuntimeError as error:
        sys.stderr.write(f"P1 页面能力覆盖 Hook：{error}\n")
        return 1

    errors = contract_errors(config)
    if errors:
        sys.stderr.write("P1 页面能力覆盖 Hook：合同机检失败\n")
        for error in errors:
            sys.stderr.write(f"- {error}\n")
        return 1

    if arguments.evidence_manifest:
        try:
            manifest_path = safe_repository_path(
                arguments.evidence_manifest, "evidence_manifest"
            )
            receipt = load_json_object(manifest_path)
        except RuntimeError as error:
            sys.stderr.write(f"P1 页面能力覆盖 Hook：{error}\n")
            return 1
        receipt_errors = validate_stage_receipt(
            config,
            arguments.stage,
            receipt,
            artifact_root=REPOSITORY_ROOT,
        )
        if receipt_errors:
            sys.stderr.write("P1 页面能力覆盖 Hook：阶段回执检查失败\n")
            for error in receipt_errors:
                sys.stderr.write(f"- {error}\n")
            return 1

    result = run_explicit_review("P1", arguments.stage)
    if result != 0:
        return result
    if arguments.evidence_manifest:
        sys.stdout.write(
            "\n页面能力覆盖阶段回执机检：PASS。"
            "该结果仍不替代独立 Reviewer 的语义内容审核。\n"
        )
    else:
        sys.stdout.write(
            "\n未提供 --evidence-manifest：本次只完成设计合同、"
            "阶段映射和任务边界检查，不得宣称阶段出口成立。\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
