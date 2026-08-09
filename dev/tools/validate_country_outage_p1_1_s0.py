#!/usr/bin/env python3
"""校验国家中断 Agent P1.1 S0 合同、对照集、冻结基线与阶段回执。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLAN_SCHEMA_PATH = REPOSITORY_ROOT / "contracts/agent/country-outage-semantic-plan-p1-1.schema.json"
CASE_SCHEMA_PATH = REPOSITORY_ROOT / "contracts/agent/country-outage-semantic-case-p1-1.schema.json"
CATALOG_PATH = REPOSITORY_ROOT / "contracts/agent/country-outage-capability-catalog-p1-1.json"
POLICY_PATH = REPOSITORY_ROOT / "contracts/agent/country-outage-semantic-policy-p1-1.json"
FIXTURE_ROOT = REPOSITORY_ROOT / "contracts/agent/fixtures/country-outage-semantic-plan-p1-1"
CASE_SET_PATH = REPOSITORY_ROOT / "evaluation/country-outage/p1-1-s0/semantic-contrast-set.json"
BASELINE_RECEIPT_PATH = REPOSITORY_ROOT / "evaluation/country-outage/p1-1-s0/p1-v1-baseline-receipt.json"
STAGE_RECEIPT_PATH = REPOSITORY_ROOT / "evaluation/country-outage/p1-1-s0/stage-receipt.json"
EXPECTED_PLAN_REVISION = "country_outage_semantic_plan_p1_1_v1"
EXPECTED_CATALOG_REVISION = "country_outage_capability_catalog_p1_1_v1"
EXPECTED_POLICY_REVISION = "country_outage_semantic_policy_p1_1_v1"
EXPECTED_BASELINE_CANDIDATE = "p1-candidate-61354911c7793d75"
EXPECTED_S0_REQUIREMENTS = {
    "P1.1-EFF-04",
    "P1.1-EFF-05",
    "P1.1-EFF-13",
    "P1.1-EFF-16",
    "P1.1-GATE-01",
    "P1.1-GATE-02",
    "P1.1-GATE-08",
    "P1.1-GATE-14",
    "P1.1-SCE-01",
    "P1.1-SCE-02",
}
SYSTEM_IDENTITY_FIELDS = {
    "incident_id",
    "legacy_reference",
    "publication_id",
    "revision",
    "collector_id",
    "cohort_id",
    "country_code",
    "window_start_utc",
    "window_end_utc",
    "data_through",
    "is_final_in_data_range",
    "lifecycle_state",
}


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法读取 JSON {path}：{error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON 根必须是对象：{path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def enum_values(schema: dict[str, Any], name: str) -> set[Any]:
    definition = schema.get("$defs", {}).get(name, {})
    values = definition.get("enum")
    if not isinstance(values, list):
        return set()
    return set(values)


def nested_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from nested_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from nested_keys(nested)


def validate_plan_schema_contract(schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if schema.get("additionalProperties") is not False:
        errors.append("Semantic Plan 顶层必须 additionalProperties=false")
    expected_required = {
        "schema_revision",
        "plan_kind",
        "goals",
        "state_transition_proposal",
        "clarification_proposal",
        "reason_codes",
    }
    if set(schema.get("required", [])) != expected_required:
        errors.append("Semantic Plan 顶层 required 字段漂移")
    revision = schema.get("properties", {}).get("schema_revision", {}).get("const")
    if revision != EXPECTED_PLAN_REVISION:
        errors.append("Semantic Plan schema_revision 无效")
    if schema.get("properties", {}).get("plan_kind", {}).get("const") != "untrusted_semantic_plan_proposal":
        errors.append("Semantic Plan 必须明确为不可信提案")
    for definition in (
        "goal",
        "entities",
        "operator_proposal",
        "state_set",
        "state_transition_proposal",
        "clarification_proposal",
    ):
        if schema.get("$defs", {}).get(definition, {}).get("additionalProperties") is not False:
            errors.append(f"Semantic Plan {definition} 必须 additionalProperties=false")
    if not enum_values(schema, "requested_goal"):
        errors.append("Semantic Plan 缺少 requested_goal 白名单")
    if not enum_values(schema, "operator_id"):
        errors.append("Semantic Plan 缺少 operator_id 白名单")
    return errors


def catalog_maps(catalog: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    capabilities = {
        value["capability_id"]: value
        for value in catalog.get("capabilities", [])
        if isinstance(value, dict) and isinstance(value.get("capability_id"), str)
    }
    operators = {
        value["operator_id"]: value
        for value in catalog.get("operator_contracts", [])
        if isinstance(value, dict) and isinstance(value.get("operator_id"), str)
    }
    return capabilities, operators


def validate_catalog(
    schema: dict[str, Any], catalog: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    if catalog.get("catalog_revision") != EXPECTED_CATALOG_REVISION:
        errors.append("Capability Catalog revision 无效")
    if catalog.get("semantic_plan_schema_revision") != EXPECTED_PLAN_REVISION:
        errors.append("Capability Catalog 未绑定 Semantic Plan revision")
    binding = catalog.get("collector_binding", {})
    if binding != {"value": "rrc25", "owner": "system", "model_mutable": False}:
        errors.append("Capability Catalog collector 必须由系统固定为 rrc25")
    raw_capabilities = catalog.get("capabilities", [])
    raw_operators = catalog.get("operator_contracts", [])
    capabilities, operators = catalog_maps(catalog)
    if len(capabilities) != len(raw_capabilities):
        errors.append("Capability Catalog capability_id 必须唯一")
    if len(operators) != len(raw_operators):
        errors.append("Capability Catalog operator_id 必须唯一")

    schema_capabilities = enum_values(schema, "capability_id") - {None}
    if set(capabilities) != schema_capabilities:
        errors.append("Capability Catalog 与 Semantic Plan capability 白名单不一致")
    schema_operators = enum_values(schema, "operator_id")
    if set(operators) != schema_operators:
        errors.append("Capability Catalog 与 Semantic Plan operator 白名单不一致")

    schema_goals = enum_values(schema, "requested_goal")
    catalog_goals: set[str] = set()
    referenced_operators: set[str] = set()
    entity_fields = set(
        schema.get("$defs", {}).get("entities", {}).get("properties", {})
    )
    for capability_id, capability in capabilities.items():
        goals = capability.get("requested_goals")
        capability_operators = capability.get("operators")
        allowed_entities = capability.get("allowed_entities")
        if not isinstance(goals, list) or not goals:
            errors.append(f"能力 {capability_id} 缺少 requested_goals")
            continue
        catalog_goals.update(goals)
        if not isinstance(capability_operators, list):
            errors.append(f"能力 {capability_id} operators 必须是数组")
        else:
            referenced_operators.update(capability_operators)
        if not isinstance(allowed_entities, list) or set(allowed_entities) - entity_fields:
            errors.append(f"能力 {capability_id} 包含未知 entity")
        if capability.get("execution_kind") in {"policy_only", "state_binding"} and capability_operators:
            errors.append(f"能力 {capability_id} 不得绑定执行算子")
    if catalog_goals != schema_goals:
        errors.append("Capability Catalog 未完整且仅覆盖 requested_goal 白名单")
    if referenced_operators != schema_operators:
        errors.append("Capability Catalog 未完整且仅引用 operator 白名单")
    return errors


def validate_policy(schema: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if policy.get("policy_revision") != EXPECTED_POLICY_REVISION:
        errors.append("Semantic Policy revision 无效")
    if policy.get("semantic_plan_schema_revision") != EXPECTED_PLAN_REVISION:
        errors.append("Semantic Policy 未绑定 Semantic Plan revision")
    if policy.get("capability_catalog_revision") != EXPECTED_CATALOG_REVISION:
        errors.append("Semantic Policy 未绑定 Capability Catalog revision")
    ownership = policy.get("field_ownership", {})
    model_fields = set(ownership.get("model_proposal_fields", []))
    system_groups = (
        set(ownership.get("system_owned_identity_fields", []))
        | set(ownership.get("system_owned_result_fields", []))
        | set(ownership.get("system_owned_trace_fields", []))
    )
    if model_fields & system_groups:
        errors.append("模型提案字段与系统所有字段重叠")
    if not SYSTEM_IDENTITY_FIELDS <= set(ownership.get("system_owned_identity_fields", [])):
        errors.append("Semantic Policy 未完整冻结系统事件身份字段")

    covered_goals: set[str] = set()
    for rule in policy.get("answerability_policy", []):
        if not isinstance(rule, dict):
            errors.append("answerability_policy 条目必须是对象")
            continue
        covered_goals.update(rule.get("requested_goals", []))
        if not str(rule.get("owner", "")).startswith("deterministic_"):
            errors.append("answerability 必须由确定性组件拥有")
    if covered_goals != enum_values(schema, "requested_goal"):
        errors.append("answerability_policy 未完整且仅覆盖 requested_goal")

    identities = {
        item.get("plan_kind"): item
        for item in policy.get("plan_identities", [])
        if isinstance(item, dict)
    }
    if set(identities) != {"baseline", "shadow", "reviewed", "executed"}:
        errors.append("计划身份必须严格覆盖 baseline/shadow/reviewed/executed")
    for plan_kind in ("shadow", "reviewed"):
        identity = identities.get(plan_kind, {})
        if any(
            identity.get(field) is not False
            for field in ("may_execute", "may_publish", "may_commit_state_after_validation")
        ):
            errors.append(f"{plan_kind} 计划必须完全隔离执行、发布和状态提交")
    commit = set(policy.get("state_contract", {}).get("atomic_commit_prerequisites", []))
    for required in (
        "proposal_schema_valid",
        "identity_valid",
        "capability_valid",
        "facts_valid",
        "evidence_valid",
        "answer_publication_success",
        "turn_is_current_and_not_cancelled",
    ):
        if required not in commit:
            errors.append(f"状态原子提交缺少前置条件：{required}")
    return errors


def validate_plan(
    plan: dict[str, Any],
    schema: dict[str, Any],
    catalog: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    allowed_top = set(schema.get("properties", {}))
    required_top = set(schema.get("required", []))
    actual_top = set(plan)
    if actual_top - allowed_top:
        errors.append(f"计划包含未定义顶层字段：{sorted(actual_top - allowed_top)}")
    if required_top - actual_top:
        errors.append(f"计划缺少顶层字段：{sorted(required_top - actual_top)}")
    forged = set(nested_keys(plan)) & SYSTEM_IDENTITY_FIELDS
    if forged:
        errors.append(f"计划包含系统身份字段：{sorted(forged)}")
    if plan.get("schema_revision") != EXPECTED_PLAN_REVISION:
        errors.append("计划 schema_revision 无效")
    if plan.get("plan_kind") != "untrusted_semantic_plan_proposal":
        errors.append("计划未标记为不可信提案")

    capabilities, operators = catalog_maps(catalog)
    goals = plan.get("goals")
    if not isinstance(goals, list) or not 1 <= len(goals) <= 8:
        errors.append("计划 goals 数量必须为 1 至 8")
        goals = []
    goal_ids: set[str] = set()
    allowed_goal_keys = {
        "goal_id",
        "requested_goal",
        "capability_proposal",
        "entities",
        "operator_proposal",
        "reason_codes",
    }
    required_goal_keys = allowed_goal_keys
    requested_goal_enum = enum_values(schema, "requested_goal")
    reason_enum = enum_values(schema, "reason_code")
    entity_fields = set(schema.get("$defs", {}).get("entities", {}).get("properties", {}))
    for index, goal in enumerate(goals, start=1):
        if not isinstance(goal, dict):
            errors.append(f"goal {index} 必须是对象")
            continue
        if set(goal) - allowed_goal_keys:
            errors.append(f"goal {index} 包含未定义字段")
        if required_goal_keys - set(goal):
            errors.append(f"goal {index} 缺少字段")
        goal_id = goal.get("goal_id")
        if not isinstance(goal_id, str) or not re.fullmatch(r"goal_[1-8]", goal_id):
            errors.append(f"goal {index} goal_id 无效")
        elif goal_id in goal_ids:
            errors.append(f"goal_id 重复：{goal_id}")
        else:
            goal_ids.add(goal_id)
        requested_goal = goal.get("requested_goal")
        if requested_goal not in requested_goal_enum:
            errors.append(f"goal {goal_id} requested_goal 不在白名单")
        capability_id = goal.get("capability_proposal")
        capability = capabilities.get(capability_id)
        if capability is None:
            errors.append(f"goal {goal_id} capability 不在 Catalog")
            continue
        if requested_goal not in capability.get("requested_goals", []):
            errors.append(f"goal {goal_id} capability 与 requested_goal 不匹配")
        entities = goal.get("entities")
        if not isinstance(entities, dict):
            errors.append(f"goal {goal_id} entities 必须是对象")
            entities = {}
        if set(entities) - entity_fields:
            errors.append(f"goal {goal_id} 包含未知 entity")
        if set(entities) - set(capability.get("allowed_entities", [])):
            errors.append(f"goal {goal_id} entity 超出 capability 允许范围")
        operator_proposal = goal.get("operator_proposal")
        if operator_proposal is None:
            if capability.get("execution_kind") == "operator":
                errors.append(f"goal {goal_id} 的执行能力缺少 operator_proposal")
        elif not isinstance(operator_proposal, dict):
            errors.append(f"goal {goal_id} operator_proposal 必须是对象或 null")
        else:
            if set(operator_proposal) != {"operator_id", "parameters"}:
                errors.append(f"goal {goal_id} operator_proposal 字段无效")
            operator_id = operator_proposal.get("operator_id")
            if operator_id not in operators:
                errors.append(f"goal {goal_id} operator 不在白名单")
            elif operator_id not in capability.get("operators", []):
                errors.append(f"goal {goal_id} operator 与 capability 不匹配")
            parameters = operator_proposal.get("parameters")
            if not isinstance(parameters, dict):
                errors.append(f"goal {goal_id} operator parameters 必须是对象")
            elif operator_id in operators and set(parameters) - set(operators[operator_id].get("parameters", [])):
                errors.append(f"goal {goal_id} operator parameter 不在白名单")
        if not isinstance(goal.get("reason_codes"), list) or set(goal.get("reason_codes", [])) - reason_enum:
            errors.append(f"goal {goal_id} reason_codes 无效")

    transition = plan.get("state_transition_proposal")
    if not isinstance(transition, dict) or set(transition) != {"inherit", "set", "clear", "reason_codes"}:
        errors.append("state_transition_proposal 字段无效")
    else:
        state_fields = enum_values(schema, "state_field")
        inherit = transition.get("inherit")
        clear = transition.get("clear")
        state_set = transition.get("set")
        if not isinstance(inherit, list) or set(inherit) - state_fields:
            errors.append("state inherit 字段无效")
        if not isinstance(clear, list) or set(clear) - state_fields:
            errors.append("state clear 字段无效")
        if isinstance(inherit, list) and isinstance(clear, list) and set(inherit) & set(clear):
            errors.append("同一状态字段不得同时 inherit 和 clear")
        allowed_set = set(schema.get("$defs", {}).get("state_set", {}).get("properties", {}))
        if not isinstance(state_set, dict) or set(state_set) - allowed_set:
            errors.append("state set 字段无效")
        if not isinstance(transition.get("reason_codes"), list) or set(transition.get("reason_codes", [])) - reason_enum:
            errors.append("state reason_codes 无效")

    clarification = plan.get("clarification_proposal")
    if clarification is not None:
        if not isinstance(clarification, dict):
            errors.append("clarification_proposal 必须是对象或 null")
        elif clarification.get("goal_id") not in goal_ids:
            errors.append("clarification_proposal goal_id 不存在")
    if not isinstance(plan.get("reason_codes"), list) or set(plan.get("reason_codes", [])) - reason_enum:
        errors.append("计划 reason_codes 无效")
    return errors


def expected_policy_decision(goal: str) -> str | None:
    if goal == "cause_request":
        return "insufficient_evidence"
    if goal in {
        "responsibility_request",
        "user_impact_request",
        "external_evidence_request",
        "unsupported_metric_request",
    }:
        return "unsupported"
    return None


def validate_case_set(
    case_set: dict[str, Any],
    schema: dict[str, Any],
    catalog: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if case_set.get("case_set_revision") != "country_outage_semantic_contrast_p1_1_s0_v1":
        errors.append("语义对照集 revision 无效")
    if case_set.get("semantic_plan_schema_revision") != EXPECTED_PLAN_REVISION:
        errors.append("语义对照集未绑定 Semantic Plan revision")
    if case_set.get("capability_catalog_revision") != EXPECTED_CATALOG_REVISION:
        errors.append("语义对照集未绑定 Capability Catalog revision")
    cases = case_set.get("cases")
    if not isinstance(cases, list) or len(cases) < 15:
        errors.append("语义对照集不得少于 15 条")
        cases = []
    ids = [case.get("case_id") for case in cases if isinstance(case, dict)]
    if len(ids) != len(set(ids)):
        errors.append("语义对照集 case_id 必须唯一")
    if ids != [f"P1.1-SEM-{index:02d}" for index in range(1, len(cases) + 1)]:
        errors.append("语义对照集 case_id 必须从 01 连续编号")
    counts = Counter(case.get("category") for case in cases if isinstance(case, dict))
    minimums = case_set.get("review_policy", {}).get("category_minimums", {})
    for category, minimum in minimums.items():
        if counts[category] < minimum:
            errors.append(f"语义对照集 {category} 少于 {minimum} 条")

    capabilities, _ = catalog_maps(catalog)
    requested_goals = enum_values(schema, "requested_goal")
    allowed_case_keys = set(load_json(CASE_SCHEMA_PATH).get("properties", {}))
    state_fields = enum_values(schema, "state_field")
    for case in cases:
        if not isinstance(case, dict):
            errors.append("语义案例必须是对象")
            continue
        case_id = case.get("case_id", "unknown")
        if set(case) != allowed_case_keys:
            errors.append(f"{case_id} 字段未严格匹配案例 Schema")
        review = case.get("review", {})
        if review.get("status") != "approved" or review.get("reviewed_by") != "P1.1-S0-contract-review":
            errors.append(f"{case_id} 尚未通过人工合同审核")
        goals = case.get("expected_goals")
        if not isinstance(goals, list) or not goals:
            errors.append(f"{case_id} 缺少 expected_goals")
            continue
        for goal in goals:
            if not isinstance(goal, dict):
                errors.append(f"{case_id} expected_goal 必须是对象")
                continue
            requested_goal = goal.get("requested_goal")
            if requested_goal not in requested_goals:
                errors.append(f"{case_id} requested_goal 不在白名单")
            capability = capabilities.get(goal.get("capability"))
            if capability is None or requested_goal not in capability.get("requested_goals", []):
                errors.append(f"{case_id} capability 与 requested_goal 不匹配")
                continue
            operator = goal.get("operator")
            if operator is None:
                if capability.get("execution_kind") == "operator":
                    errors.append(f"{case_id} 执行能力缺少 operator")
            elif operator not in capability.get("operators", []):
                errors.append(f"{case_id} operator 与 capability 不匹配")
            decision = goal.get("policy_decision")
            required_boundary_decision = expected_policy_decision(str(requested_goal))
            if required_boundary_decision and decision != required_boundary_decision:
                errors.append(f"{case_id} 边界目标政策决策错误")
            if decision in {"unsupported", "insufficient_evidence", "reject"} and operator is not None:
                errors.append(f"{case_id} 非执行政策目标不得绑定 operator")
        state = case.get("expected_state_effect", {})
        if set(state) != {"inherit", "set", "clear"}:
            errors.append(f"{case_id} expected_state_effect 字段无效")
        else:
            if set(state.get("inherit", [])) - state_fields:
                errors.append(f"{case_id} inherit 包含未知状态字段")
            if set(state.get("clear", [])) - state_fields:
                errors.append(f"{case_id} clear 包含未知状态字段")
            if set(state.get("set", {})) - state_fields:
                errors.append(f"{case_id} set 包含未知状态字段")
        if not isinstance(case.get("prohibited_outcomes"), list) or not case["prohibited_outcomes"]:
            errors.append(f"{case_id} 缺少 prohibited_outcomes")

    by_id = {case["case_id"]: case for case in cases if isinstance(case, dict) and "case_id" in case}
    sem01_goals = [goal.get("requested_goal") for goal in by_id.get("P1.1-SEM-01", {}).get("expected_goals", [])]
    if sem01_goals != ["address_family_change"] or "event_switch" in sem01_goals:
        errors.append("P1.1-SEM-01 必须证明‘IP地址变换情况’不是事件切换")
    sem08_goals = [goal.get("requested_goal") for goal in by_id.get("P1.1-SEM-08", {}).get("expected_goals", [])]
    if sem08_goals != ["event_switch", "address_family_change"]:
        errors.append("P1.1-SEM-08 必须同时保留事件切换和地址变化")
    sem14 = by_id.get("P1.1-SEM-14", {})
    if [goal.get("requested_goal") for goal in sem14.get("expected_goals", [])] != ["asn_detail"] or "pending_clarification" not in sem14.get("expected_state_effect", {}).get("clear", []):
        errors.append("P1.1-SEM-14 必须清除旧澄清并改问 ASN")
    if len(by_id.get("P1.1-SEM-17", {}).get("expected_goals", [])) != 5:
        errors.append("P1.1-SEM-17 必须保留五个复合目标")
    return errors


def validate_baseline_receipt(receipt: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if receipt.get("candidate_id") != EXPECTED_BASELINE_CANDIDATE:
        errors.append("P1-v1 基线回执 candidate_id 无效")
    if receipt.get("collector_id") != "rrc25":
        errors.append("P1-v1 基线回执 collector 必须为 rrc25")
    counts = receipt.get("counts", {})
    if counts != {
        "direct": {"passed": 20, "total": 20},
        "multi_turn": {"passed": 5, "total": 5},
        "boundary": {"passed": 5, "total": 5},
        "exception": {"passed": 5, "total": 5},
    }:
        errors.append("P1-v1 基线回执必须保持 35/35")
    hard_gates = receipt.get("hard_gates", {})
    if hard_gates != {
        "all_35_passed": True,
        "event_binding_percent": 100,
        "publication_binding_percent": 100,
        "forbidden_assertion_hits": 0,
        "invalid_answer_publications": 0,
    }:
        errors.append("P1-v1 基线回执硬门禁无效")
    for artifact in receipt.get("source_artifacts", []):
        if not isinstance(artifact, dict):
            errors.append("P1-v1 基线 source_artifacts 条目无效")
            continue
        path = REPOSITORY_ROOT / str(artifact.get("path", ""))
        if not path.is_file():
            errors.append(f"P1-v1 冻结源制品不存在：{path}")
        elif artifact.get("sha256") != sha256(path):
            errors.append(f"P1-v1 冻结源制品摘要漂移：{artifact.get('path')}")
    if receipt.get("boundary") != {
        "implemented": True,
        "tested": True,
        "accepted": True,
        "deployed": False,
        "production_verified": False,
        "p1_1_implemented": False,
    }:
        errors.append("P1-v1 基线回执状态边界无效")
    return errors


def validate_stage_receipt(receipt: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if receipt.get("stage") != "S0" or receipt.get("conclusion") != "一致":
        errors.append("P1.1 S0 阶段回执必须明确 S0 一致")
    if receipt.get("contract_candidate_id") != manifest.get("contract_candidate_id"):
        errors.append("P1.1 S0 阶段回执候选身份与 manifest 不一致")
    if set(receipt.get("requirements", [])) != EXPECTED_S0_REQUIREMENTS:
        errors.append("P1.1 S0 阶段回执要求映射不完整")
    hook = receipt.get("hook", {})
    if hook.get("command") != "python3 .codex/hooks/country_outage_agent_program_review.py --project P1.1 --stage S0" or hook.get("exit_code") != 0:
        errors.append("P1.1 S0 阶段回执缺少成功 Hook 命令")
    if receipt.get("status_boundary") != {
        "contract_complete": True,
        "fixtures_tested": True,
        "p1_baseline_reverified": True,
        "semantic_planner_implemented": False,
        "model_provider_connected": False,
        "shadow_running": False,
        "deployed": False,
        "production_verified": False,
    }:
        errors.append("P1.1 S0 阶段回执状态边界无效")
    for evidence_path in receipt.get("evidence_paths", []):
        path = REPOSITORY_ROOT / evidence_path
        if not path.is_file():
            errors.append(f"P1.1 S0 阶段证据不存在：{evidence_path}")
    return errors


def safe_repo_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    path = (REPOSITORY_ROOT / relative).resolve()
    try:
        path.relative_to(REPOSITORY_ROOT)
    except ValueError:
        return None
    return path


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != "country_outage_p1_1_s0_manifest_v1":
        errors.append("P1.1 S0 manifest schema_version 无效")
    if manifest.get("base_commit") != "46c7a002b78867340d475d6e1d272192a3fa1817":
        errors.append("P1.1 S0 manifest base_commit 无效")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("P1.1 S0 manifest 缺少 artifacts")
        artifacts = []
    paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append("P1.1 S0 manifest artifact 必须是对象")
            continue
        raw_path = artifact.get("path")
        path = safe_repo_path(raw_path)
        if path is None:
            errors.append(f"P1.1 S0 manifest 路径无效：{raw_path!r}")
            continue
        if raw_path in paths:
            errors.append(f"P1.1 S0 manifest 路径重复：{raw_path}")
            continue
        paths.add(raw_path)
        if not path.is_file():
            errors.append(f"P1.1 S0 manifest 制品不存在：{raw_path}")
            continue
        if artifact.get("sha256") != sha256(path):
            errors.append(f"P1.1 S0 manifest 摘要不一致：{raw_path}")
        if artifact.get("size_bytes") != path.stat().st_size:
            errors.append(f"P1.1 S0 manifest 大小不一致：{raw_path}")
    required_paths = {
        str(PLAN_SCHEMA_PATH.relative_to(REPOSITORY_ROOT)),
        str(CASE_SCHEMA_PATH.relative_to(REPOSITORY_ROOT)),
        str(CATALOG_PATH.relative_to(REPOSITORY_ROOT)),
        str(POLICY_PATH.relative_to(REPOSITORY_ROOT)),
        str(CASE_SET_PATH.relative_to(REPOSITORY_ROOT)),
        str(BASELINE_RECEIPT_PATH.relative_to(REPOSITORY_ROOT)),
        str(STAGE_RECEIPT_PATH.relative_to(REPOSITORY_ROOT)),
    }
    if required_paths - paths:
        errors.append(f"P1.1 S0 manifest 缺少核心制品：{sorted(required_paths - paths)}")
    if manifest.get("artifact_count") != len(artifacts):
        errors.append("P1.1 S0 manifest artifact_count 不一致")
    return errors


def validate_fixtures(
    schema: dict[str, Any], catalog: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    expected = {
        "valid-address-change.json": True,
        "valid-compound-boundary.json": True,
        "invalid-system-identity.json": False,
        "invalid-invented-operator.json": False,
        "invalid-operator-capability-pair.json": False,
    }
    actual_files = {path.name for path in FIXTURE_ROOT.glob("*.json")}
    if actual_files != set(expected):
        errors.append("Semantic Plan fixtures 集合漂移")
    for name, should_pass in expected.items():
        path = FIXTURE_ROOT / name
        if not path.is_file():
            continue
        fixture_errors = validate_plan(load_json(path), schema, catalog)
        if should_pass and fixture_errors:
            errors.append(f"合法 fixture {name} 被拒绝：{fixture_errors}")
        if not should_pass and not fixture_errors:
            errors.append(f"非法 fixture {name} 未被拒绝")
    return errors


def validate_all(manifest_path: Path) -> list[str]:
    try:
        schema = load_json(PLAN_SCHEMA_PATH)
        load_json(CASE_SCHEMA_PATH)
        catalog = load_json(CATALOG_PATH)
        policy = load_json(POLICY_PATH)
        case_set = load_json(CASE_SET_PATH)
        baseline = load_json(BASELINE_RECEIPT_PATH)
        stage_receipt = load_json(STAGE_RECEIPT_PATH)
        manifest = load_json(manifest_path)
    except RuntimeError as error:
        return [str(error)]
    return (
        validate_plan_schema_contract(schema)
        + validate_catalog(schema, catalog)
        + validate_policy(schema, policy)
        + validate_fixtures(schema, catalog)
        + validate_case_set(case_set, schema, catalog)
        + validate_baseline_receipt(baseline)
        + validate_manifest(manifest)
        + validate_stage_receipt(stage_receipt, manifest)
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验国家中断 Agent P1.1 S0 合同候选")
    parser.add_argument("manifest", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    path = arguments.manifest
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    errors = validate_all(path)
    if errors:
        sys.stderr.write("P1.1 S0 校验失败\n")
        for error in errors:
            sys.stderr.write(f"- {error}\n")
        return 1
    case_set = load_json(CASE_SET_PATH)
    manifest = load_json(path)
    sys.stdout.write(
        "PASS: "
        f"{manifest['contract_candidate_id']}，"
        f"Semantic Plan/Catalog/Policy/fixtures 有效，"
        f"语义对照 {len(case_set['cases'])} 条已审核，"
        "P1-v1 35/35 冻结基线与 S0 边界通过\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
