#!/usr/bin/env python3
"""校验国家中断 Agent P1 runtime-v2 的 S4 联合验收制品。

本校验器只验证制品闭合、候选身份和 P0 案例合同，不把结构绿灯
充当浏览器效果、产品语义真值或生产验证。
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
P0_CASES_PATH = REPOSITORY_ROOT / "evaluation/country-outage/p0-v1-3/cases.json"
P0_BASE_CASES_PATH = REPOSITORY_ROOT / "evaluation/country-outage/p0-v1/cases.json"
S4_ROOT = REPOSITORY_ROOT / "evaluation/country-outage/p1-runtime-v2"
S4_RESULTS_PATH = S4_ROOT / "s4-p0-v1-3-results.json"
LIVE_EVIDENCE_PATH = S4_ROOT / "s4-p0-live-evidence.json"
SEMANTIC_CANDIDATE_PATH = S4_ROOT / "s4-semantic-current-prompt-candidate.json"
SEMANTIC_EVALUATION_PATH = S4_ROOT / "s4-semantic-current-prompt-evaluation.json"
IDENTITY_PATH = S4_ROOT / "candidate-identity.json"
BUDGETS_PATH = S4_ROOT / "s4-runtime-budgets.json"
MANIFEST_PATH = S4_ROOT / "manifest.json"
BROWSER_API_PATH = S4_ROOT / "s4-browser-api-conversation.json"
JOINT_ACCEPTANCE_PATH = S4_ROOT / "s4-joint-acceptance.json"
EXPECTED_REVISION = "p0-v1.3-20260809-ir-r1"
EXPECTED_COLLECTOR = "rrc25"
EXPECTED_CATEGORY_COUNTS = {
    "direct": 20,
    "multi_turn": 5,
    "boundary": 5,
    "exception": 5,
}
ANSWERABILITY_MAP = {
    "answerable": {"supported"},
    "partial": {"partial"},
    "clarify": {"clarify"},
    "unsupported": {"unsupported"},
    "invalid_data": {"invalid_data", "invalid_data_fail_closed"},
}

# 每一项依次对应一轮；required 必须出现，allowed 之外的目标会被判为扩写。
GOAL_CONTRACTS: dict[str, list[tuple[set[str], set[str]]]] = {
    "P013-D-01": [({"event_summary"}, {"event_summary"})],
    "P013-D-02": [({"observation_window"}, {"observation_window"})],
    "P013-D-03": [({"detection_time"}, {"detection_time"})],
    "P013-D-04": [({"prefix_peak"}, {"prefix_peak"})],
    "P013-D-05": [({"address_family_change"}, {"address_family_change"})],
    "P013-D-06": [({"recovery_status"}, {"recovery_status", "event_end_state"})],
    "P013-D-07": [({"current_prefix_state"}, {"current_prefix_state"})],
    "P013-D-08": [({"current_scope"}, {"current_scope"})],
    "P013-D-09": [({"top_affected_asns"}, {"top_affected_asns"})],
    "P013-D-10": [({"asn_detail"}, {"asn_detail"})],
    "P013-D-11": [({"remaining_vs_peak"}, {"remaining_vs_peak"})],
    "P013-D-12": [({"address_family_compare"}, {"address_family_compare"})],
    "P013-D-13": [({"bgp_update_activity"}, {"bgp_update_activity"})],
    "P013-D-14": [({"metric_semantics"}, {"metric_semantics"})],
    "P013-D-15": [({"new_prefix_resources"}, {"new_prefix_resources"})],
    "P013-D-16": [({"path_sample"}, {"path_sample", "evidence_trace"})],
    "P013-D-17": [({"evidence_trace"}, {"evidence_trace"})],
    "P013-D-18": [({"data_completeness"}, {"data_completeness"})],
    "P013-D-19": [
        ({"event_identity"}, {"event_identity", "observation_window"})
    ],
    "P013-D-20": [({"rrc25_proof_boundary"}, {"rrc25_proof_boundary"})],
    "P013-M-01": [
        ({"event_summary"}, {"event_summary"}),
        ({"prefix_peak"}, {"prefix_peak"}),
        ({"metric_followup"}, {"metric_followup"}),
    ],
    "P013-M-02": [
        ({"asn_detail"}, {"asn_detail"}),
        ({"asn_detail"}, {"asn_detail"}),
    ],
    "P013-M-03": [
        ({"address_family_compare"}, {"address_family_compare"}),
        ({"path_sample"}, {"path_sample"}),
    ],
    "P013-M-04": [
        ({"prefix_peak"}, {"prefix_peak"}),
        ({"event_switch"}, {"event_switch"}),
        ({"event_switch"}, {"event_switch"}),
    ],
    "P013-M-05": [
        ({"prefix_peak"}, {"prefix_peak"}),
        ({"evidence_trace"}, {"evidence_trace"}),
        ({"cause_or_responsibility"}, {"cause_or_responsibility"}),
    ],
    "P013-B-01": [
        (
            {"current_prefix_state", "real_user_or_national_impact"},
            {"current_prefix_state", "real_user_or_national_impact"},
        )
    ],
    "P013-B-02": [
        (
            {"technical_mechanism_attribution"},
            {"technical_mechanism_attribution", "cause_or_responsibility"},
        )
    ],
    "P013-B-03": [
        ({"real_user_or_national_impact"}, {"real_user_or_national_impact"})
    ],
    "P013-B-04": [({"external_evidence"}, {"external_evidence"})],
    "P013-B-05": [
        (
            {
                "cause_or_responsibility",
                "real_user_or_national_impact",
                "economic_impact",
            },
            {
                "cause_or_responsibility",
                "real_user_or_national_impact",
                "economic_impact",
            },
        )
    ],
    "P013-X-01": [
        ({"event_end_state"}, {"event_end_state", "observation_window"})
    ],
    "P013-X-02": [
        ({"capability_absent_not_zero"}, {"capability_absent_not_zero"})
    ],
    "P013-X-03": [({"asn_detail"}, {"asn_detail"})],
}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_repository_path(value: object, label: str) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    target = (REPOSITORY_ROOT / relative).resolve()
    try:
        target.relative_to(REPOSITORY_ROOT)
    except ValueError:
        return None
    return target


def resolve_json_pointer(document: object, pointer: str) -> object:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError("JSON Pointer 必须以 / 开头")
    current = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise ValueError("JSON Pointer 穿过非容器值")
    return current


def accepted_live_attempt(case: dict[str, Any]) -> dict[str, Any] | None:
    accepted_number = case.get("accepted_attempt")
    attempts = case.get("attempts")
    if not isinstance(accepted_number, int) or not isinstance(attempts, list):
        return None
    return next(
        (
            item
            for item in attempts
            if isinstance(item, dict) and item.get("attempt") == accepted_number
        ),
        None,
    )


def expected_questions(base_case: dict[str, Any]) -> list[str]:
    turns = base_case.get("turns")
    if isinstance(turns, list):
        return [
            str(item.get("user"))
            for item in turns
            if isinstance(item, dict) and isinstance(item.get("user"), str)
        ]
    question = base_case.get("question")
    return [question] if isinstance(question, str) else []


def validate_controlled_failure_receipt(
    case_id: str,
    live_case: dict[str, Any],
    base_case: dict[str, Any],
    identity: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    expected = {
        "P013-X-04": {
            "test_name": (
                "P0-X-04 overview 与 series 跨 publication 冲突时整轮失败且旧状态不变"
            ),
            "gate": "pre_execution_fact_bundle_identity",
            "error_code": "publication_identity_conflict",
        },
        "P013-X-05": {
            "test_name": (
                "P0-X-05 series 声明人口与轨道长度不一致时整轮失败且不计算极值"
            ),
            "gate": "pre_execution_series_shape",
            "error_code": "invalid_series_shape",
        },
    }[case_id]
    receipt = live_case.get("controlled_failure_receipt")
    if not isinstance(receipt, dict):
        return [f"{case_id} 缺少结构化 controlled_failure_receipt"]
    if receipt.get("schema_version") != (
        "country_outage_p1_controlled_failure_receipt_v1"
    ):
        errors.append(f"{case_id} 故障回执 schema_version 错误")
    if receipt.get("candidate_id") != identity.get("candidate_id"):
        errors.append(f"{case_id} 故障回执未绑定当前候选")
    if receipt.get("case_id") != case_id:
        errors.append(f"{case_id} 故障回执案例身份错误")
    questions = expected_questions(base_case)
    if len(questions) != 1 or receipt.get("question") != questions[0]:
        errors.append(f"{case_id} 故障回执未使用冻结原问题")
    if receipt.get("test_name") != expected["test_name"]:
        errors.append(f"{case_id} 故障回执测试身份错误")
    runtime_identity = receipt.get("runtime_identity")
    expected_sources = {
        "implementation": "p1-runtime-v2-conversation",
        "runtime_source": "agent-sidecar/src/chat/runtime-v2-conversation.ts",
        "test_source": "agent-sidecar/tests/p1-runtime-v2-conversation.test.ts",
    }
    if runtime_identity != expected_sources:
        errors.append(f"{case_id} 故障回执 runtime/test identity 错误")
    identity_artifacts = {
        item.get("path")
        for item in identity.get("basis", {}).get("artifacts", [])
        if isinstance(item, dict)
    }
    if not {
        expected_sources["runtime_source"], expected_sources["test_source"]
    }.issubset(identity_artifacts):
        errors.append(f"{case_id} 故障回执源文件未受 candidate hash 约束")

    fault = receipt.get("fault_injection")
    if not isinstance(fault, dict) or fault.get("gate") != expected["gate"]:
        errors.append(f"{case_id} 未记录精确预执行故障注入点")
    elif case_id == "P013-X-04":
        if (
            fault.get("field") != "series.publication_id"
            or fault.get("actual")
            != "country_outage_publication_conflict_fixture"
            or not isinstance(fault.get("expected"), str)
            or fault.get("expected") == fault.get("actual")
        ):
            errors.append(f"{case_id} publication 冲突输入不成立")
    elif (
        fault.get("declared_point_count") != 3455
        or fault.get("timestamps_length") != 3454
        or fault.get("track") != "fixed_visible_ipv4_address_count"
        or fault.get("track_length") != 3454
    ):
        errors.append(f"{case_id} series 长度故障输入不符合冻结 fixture")

    checkpoints = receipt.get("pipeline_checkpoints")
    if not isinstance(checkpoints, dict):
        errors.append(f"{case_id} 缺少 pipeline_checkpoints")
    else:
        if checkpoints.get("user_goal_plan") != {
            "status": "not_reached", "planner_call_count": 0
        }:
            errors.append(f"{case_id} 未证明 UserGoalPlan 在预检失败后未到达")
        if checkpoints.get("grounding_plan") != {
            "status": "not_reached", "nodes": []
        }:
            errors.append(f"{case_id} 未证明 GroundingPlan 在预检失败后未到达")
        if checkpoints.get("tool_execution") != {
            "status": "not_started", "nodes": []
        }:
            errors.append(f"{case_id} 未证明 Tool execution 未开始")

    failure = receipt.get("failure")
    if not isinstance(failure, dict):
        errors.append(f"{case_id} 缺少失败发布回执")
    else:
        error = failure.get("error")
        if (
            failure.get("turn_state") != "failed"
            or not isinstance(error, dict)
            or error.get("code") != expected["error_code"]
        ):
            errors.append(f"{case_id} 未记录预期失败码")
        if (
            failure.get("published_answer") is not None
            or failure.get("published_evidence") != []
            or failure.get("model_generated_fact_count") != 0
        ):
            errors.append(f"{case_id} 预检失败后仍发布答案、证据或模型事实")

    state = receipt.get("state_receipt")
    if not isinstance(state, dict) or state.get("status") != "rolled_back":
        errors.append(f"{case_id} 缺少 rolled_back 状态回执")
    else:
        before = state.get("before")
        after = state.get("after")
        required_state = {
            "binding", "evidence_state", "dialog_state",
            "active_binding_generation",
        }
        if (
            not isinstance(before, dict)
            or not isinstance(after, dict)
            or set(before) != required_state
            or set(after) != required_state
        ):
            errors.append(f"{case_id} 状态回执未保存完整前后快照")
        else:
            for key in sorted(required_state):
                if before.get(key) != after.get(key):
                    errors.append(f"{case_id} {key} 未完整回滚")
            binding = before.get("binding")
            evidence_state = before.get("evidence_state")
            if (
                not isinstance(binding, dict)
                or binding.get("collector_id") != "rrc25"
                or not isinstance(evidence_state, dict)
                or evidence_state.get("collector_id") != "rrc25"
                or evidence_state.get("publication_id")
                != binding.get("publication_id")
                or evidence_state.get("revision") != binding.get("revision")
            ):
                errors.append(f"{case_id} 回滚快照的 EvidenceState/Binding 身份无效")
        if state.get("equality") != {
            "binding": True,
            "evidence_state": True,
            "dialog_state": True,
            "active_binding_generation": True,
        }:
            errors.append(f"{case_id} 回滚相等性回执不完整")
    return errors


def validate_live_evidence(
    p0: dict[str, Any],
    p0_base: dict[str, Any],
    live: dict[str, Any],
    identity: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    candidate_id = identity.get("candidate_id")
    if live.get("candidate_id") != candidate_id:
        errors.append("P0 原始回执与候选身份不一致")
    if live.get("schema_version") != "country_outage_p1_s4_p0_live_evidence_v2":
        errors.append("P0 原始回执 schema_version 错误")
    retry = live.get("retry_semantics")
    if not isinstance(retry, dict) or retry.get("automatic_retry_inside_turn") is not False:
        errors.append("P0 原始回执未关闭轮内自动重试")
    counts = live.get("counts")
    if not isinstance(counts, dict) or any(
        counts.get(key) != value
        for key, value in {
            "total": 35,
            "live_model_api": 33,
            "controlled_deterministic_injection": 2,
            "accepted": 35,
            "failed": 0,
        }.items()
    ):
        errors.append(f"P0 原始回执计数不闭合：{counts!r}")

    p0_cases = p0.get("cases")
    base_cases = p0_base.get("cases")
    live_cases = live.get("cases")
    if not all(isinstance(value, list) for value in (p0_cases, base_cases, live_cases)):
        return errors + ["P0、P0 base 和 live cases 必须是数组"]
    p0_by_id = {
        item["case_id"]: item
        for item in p0_cases
        if isinstance(item, dict) and isinstance(item.get("case_id"), str)
    }
    base_by_id = {
        item["case_id"]: item
        for item in base_cases
        if isinstance(item, dict) and isinstance(item.get("case_id"), str)
    }
    live_by_id = {
        item["case_id"]: item
        for item in live_cases
        if isinstance(item, dict) and isinstance(item.get("case_id"), str)
    }
    if len(live_by_id) != 35 or set(live_by_id) != set(p0_by_id):
        return errors + ["P0 原始回执不是冻结 35 案的唯一全集"]

    for case_id, contract in p0_by_id.items():
        live_case = live_by_id[case_id]
        base_case = base_by_id.get(contract.get("base_case_id"))
        if base_case is None:
            errors.append(f"{case_id} 找不到 P0 base 真值")
            continue
        if live_case.get("success") is not True:
            errors.append(f"{case_id} 原始执行未成功")
        if case_id in {"P013-X-04", "P013-X-05"}:
            if live_case.get("evidence_mode") != "controlled_deterministic_injection":
                errors.append(f"{case_id} 必须使用受控确定性故障注入")
            if live_case.get("actual_answerability") != "invalid_data_fail_closed":
                errors.append(f"{case_id} 未证明整轮 invalid_data 失败关闭")
            errors.extend(validate_controlled_failure_receipt(
                case_id, live_case, base_case, identity
            ))
            continue
        if live_case.get("evidence_mode") != "live_model_api":
            errors.append(f"{case_id} 不是实时模型/API 回执")
            continue
        attempt = accepted_live_attempt(live_case)
        if attempt is None or attempt.get("success") is not True:
            errors.append(f"{case_id} 缺少可解析 accepted_attempt")
            continue
        turns = attempt.get("turns")
        questions = expected_questions(base_case)
        if not isinstance(turns, list) or [
            item.get("question") for item in turns if isinstance(item, dict)
        ] != questions:
            errors.append(f"{case_id} 未按冻结原问题/轮次执行")
            continue
        final_answerability = turns[-1].get("answerability") if turns else None
        allowed = ANSWERABILITY_MAP.get(str(contract.get("expected_mode")), set())
        if final_answerability not in allowed:
            errors.append(
                f"{case_id} 原始回执可回答性 {final_answerability!r} 不符合真值"
            )
        requirements = GOAL_CONTRACTS.get(case_id)
        if requirements is None or len(requirements) != len(turns):
            errors.append(f"{case_id} 缺少逐轮目标合同")
            continue

        all_evidence: list[dict[str, Any]] = []
        for index, (turn, (required, allowed_goals)) in enumerate(
            zip(turns, requirements), start=1
        ):
            if not isinstance(turn, dict) or turn.get("state") != "completed":
                errors.append(f"{case_id} 第 {index} 轮未完成")
                continue
            plan = turn.get("user_goal_plan")
            grounding = turn.get("grounding_plan")
            validation = turn.get("validation")
            execution = turn.get("execution_trace")
            receipt = turn.get("state_receipt")
            binding = turn.get("binding")
            evidence = turn.get("evidence")
            results = turn.get("results")
            if not isinstance(plan, dict) or plan.get("original_question") != turn.get("question"):
                errors.append(f"{case_id} 第 {index} 轮 UserGoalPlan 未忠实绑定原问题")
                continue
            goals = plan.get("goals")
            kinds = {
                item.get("normalized_kind")
                for item in goals
                if isinstance(item, dict)
            } if isinstance(goals, list) else set()
            if not required.issubset(kinds) or not kinds.issubset(allowed_goals):
                errors.append(
                    f"{case_id} 第 {index} 轮目标漂移：{sorted(kinds)}，"
                    f"required={sorted(required)} allowed={sorted(allowed_goals)}"
                )
            if not isinstance(grounding, dict) or (
                grounding.get("validation", {}).get("status") != "passed"
            ):
                errors.append(f"{case_id} 第 {index} 轮 GroundingPlan 未通过")
            if not isinstance(validation, dict) or any(
                validation.get(key) != "passed"
                for key in (
                    "user_goal_schema",
                    "grounding_schema",
                    "grounding_legality",
                    "answer_evidence",
                )
            ):
                errors.append(f"{case_id} 第 {index} 轮验证链未全绿")
            if not isinstance(execution, dict) or (
                execution.get("planner_outcome") != "accepted"
                or execution.get("model_generated_fact_count") != 0
            ):
                errors.append(f"{case_id} 第 {index} 轮执行轨迹不可信")
            if not isinstance(receipt, dict) or receipt.get("status") not in {
                "committed", "none"
            }:
                errors.append(f"{case_id} 第 {index} 轮状态事务未闭合")
            if not isinstance(binding, dict) or (
                binding.get("collector_id") != "rrc25"
                or binding.get("publication_id")
                != live.get("event_binding_request", {}).get("publication_id")
                or binding.get("revision") != 1
            ):
                errors.append(f"{case_id} 第 {index} 轮绑定身份漂移")
            if not isinstance(evidence, list) or not isinstance(results, list):
                errors.append(f"{case_id} 第 {index} 轮结果/证据不是数组")
                continue
            for item in evidence:
                if not isinstance(item, dict) or (
                    item.get("publication_id") != binding.get("publication_id")
                    or item.get("revision") != binding.get("revision")
                    or item.get("collector_id") != "rrc25"
                ):
                    errors.append(f"{case_id} 第 {index} 轮证据身份漂移")
                    break
            all_evidence.extend(item for item in evidence if isinstance(item, dict))
            for result in results:
                if not isinstance(result, dict):
                    errors.append(f"{case_id} 第 {index} 轮目标结果无效")
                    continue
                if result.get("answerability") in {
                    "unsupported", "clarify", "invalid_data"
                } and result.get("evidence_refs") not in ([], None):
                    errors.append(f"{case_id} 第 {index} 轮越界/无效目标发布了事实证据")

        # 可回答/局部可回答案例的结构化事实必须真实出现在原始 evidence 中。
        conceptual_prefixes = (
            "capability_observations.",
            "semantic_boundaries.",
        )
        expected_facts = base_case.get("expected", {}).get("facts", [])
        for fact in expected_facts if isinstance(expected_facts, list) else []:
            if not isinstance(fact, dict):
                continue
            ref = fact.get("evidence_ref")
            if (
                contract.get("expected_mode") in {"unsupported", "invalid_data"}
                or (isinstance(ref, str) and ref.startswith(conceptual_prefixes))
            ):
                continue
            if not any(
                item.get("evidence_ref") == ref
                and item.get("value") == fact.get("value")
                and item.get("unit") == fact.get("unit")
                for item in all_evidence
            ):
                errors.append(f"{case_id} 缺少冻结事实 {ref}={fact.get('value')!r}")

        if case_id == "P013-D-08":
            for fact in contract.get("additional_expected_facts", []):
                metric = fact.get("metric")
                checks = [
                    (f"peaks.{metric}.value", fact.get("maximum"), "asn"),
                    (
                        f"peaks.{metric}.state_point_utc",
                        fact.get("maximum_at_utc"),
                        "UTC",
                    ),
                ]
                for ref, value, unit in checks:
                    if not any(
                        item.get("evidence_ref") == ref
                        and item.get("value") == value
                        and item.get("unit") == unit
                        for item in all_evidence
                    ):
                        errors.append(f"{case_id} 缺少 P0 v1.3 追加事实 {ref}")
    return errors


def validate_semantic_current_candidate(
    candidate: dict[str, Any],
    evaluation: dict[str, Any],
    identity: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    candidate_id = identity.get("candidate_id")
    if candidate.get("candidate_id") != candidate_id:
        errors.append("当前 Prompt 原始候选与 candidate_id 不一致")
    if evaluation.get("candidate_id") != candidate_id:
        errors.append("当前 Prompt 评测与 candidate_id 不一致")
    if candidate.get("prompt_identity") != (
        "runtime-prompt-v2-current-s4:conversation:has_dialog_state=true"
    ):
        errors.append("语义评测不是实际 conversation 的 has_dialog_state=true Prompt")
    counts = candidate.get("counts")
    if not isinstance(counts, dict) or counts.get("total") != 24 or (
        counts.get("accepted") != 24 or counts.get("failed") != 0
    ):
        errors.append(f"当前 Prompt 原始候选不是 24/24：{counts!r}")
    metrics = evaluation.get("metrics")
    if not isinstance(metrics, dict) or (
        metrics.get("machine_gate_passed") is not True
        or not isinstance(metrics.get("machine_goal_fidelity"), (int, float))
        or metrics["machine_goal_fidelity"] < 0.95
    ):
        errors.append(f"UserGoalPlan 目标保真率未达到 95%：{metrics!r}")
    grounding = evaluation.get("grounding_legality")
    if not isinstance(grounding, dict) or (
        grounding.get("rate") != 1.0
        or grounding.get("validated_case_count") != 24
        or grounding.get("status") != "passed"
    ):
        errors.append(f"GroundingPlan 合法性不是 24/24 及 100%：{grounding!r}")
    return errors


def validate_results(
    p0: dict[str, Any],
    results_document: dict[str, Any],
    live_evidence: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    if results_document.get("schema_version") != (
        "country_outage_p1_runtime_v2_p0_results_v2"
    ):
        errors.append("S4 结果必须使用可解析证据指针 v2")
    if p0.get("revision") != EXPECTED_REVISION:
        errors.append("P0 案例修订版不是冻结入口")
    if results_document.get("p0_entry_revision") != EXPECTED_REVISION:
        errors.append("S4 结果未绑定 P0 v1.3 冻结修订版")
    if results_document.get("collector_id") != EXPECTED_COLLECTOR:
        errors.append("S4 结果不是 RRC25-only")

    p0_cases = p0.get("cases")
    results = results_document.get("results")
    if not isinstance(p0_cases, list) or not isinstance(results, list):
        return errors + ["P0 cases 和 S4 results 必须是数组"]

    p0_by_id = {
        item.get("case_id"): item
        for item in p0_cases
        if isinstance(item, dict) and isinstance(item.get("case_id"), str)
    }
    result_ids = [
        item.get("case_id") for item in results if isinstance(item, dict)
    ]
    if len(p0_cases) != 35 or len(p0_by_id) != 35:
        errors.append("P0 冻结案例不是 35 个唯一 ID")
    if len(results) != 35 or len(set(result_ids)) != 35:
        errors.append("S4 结果不是 35 个唯一案例")
    if set(result_ids) != set(p0_by_id):
        errors.append("S4 案例 ID 集与 P0 冻结集不一致")

    category_counts = Counter(
        item.get("category") for item in p0_cases if isinstance(item, dict)
    )
    if dict(category_counts) != EXPECTED_CATEGORY_COUNTS:
        errors.append(f"P0 分类计数不一致：{dict(category_counts)}")

    for result in results:
        if not isinstance(result, dict):
            errors.append("S4 result 条目必须是对象")
            continue
        case_id = result.get("case_id")
        expected = p0_by_id.get(case_id)
        if expected is None:
            continue
        expected_mode = expected.get("expected_mode")
        if result.get("expected_answerability") != expected_mode:
            errors.append(f"{case_id} expected_answerability 未忠实复制 P0 真值")
        allowed_actual = ANSWERABILITY_MAP.get(str(expected_mode), set())
        if result.get("actual_answerability") not in allowed_actual:
            errors.append(
                f"{case_id} 实际可回答性 {result.get('actual_answerability')!r} "
                f"不符合 {expected_mode!r}"
            )
        if result.get("passed") is not True:
            errors.append(f"{case_id} 未通过")
        if result.get("hard_gates_passed") != expected.get("hard_gates"):
            errors.append(f"{case_id} 硬门列表与 P0 真值不一致")
        proof = result.get("proof")
        if not isinstance(proof, list) or len(proof) != 1:
            errors.append(f"{case_id} 必须有且仅有一个原始回执证据指针")
            continue
        pointer = proof[0]
        if not isinstance(pointer, dict) or pointer.get("artifact") != (
            "evaluation/country-outage/p1-runtime-v2/s4-p0-live-evidence.json"
        ) or pointer.get("record_id") != case_id or not isinstance(
            pointer.get("json_pointer"), str
        ):
            errors.append(f"{case_id} 证据指针不是可解析 artifact/json_pointer")
            continue
        if live_evidence is not None:
            try:
                record = resolve_json_pointer(
                    live_evidence, pointer["json_pointer"]
                )
            except (KeyError, IndexError, ValueError, TypeError):
                errors.append(f"{case_id} 证据指针无法解析")
            else:
                if not isinstance(record, dict) or record.get("case_id") != case_id:
                    errors.append(f"{case_id} 证据指针没有指向同一案例")

    counts = results_document.get("counts")
    expected_counts = {
        "total": 35,
        "passed": 35,
        "direct": "20/20",
        "multi_turn": "5/5",
        "boundary": "5/5",
        "exception": "5/5",
    }
    if counts != expected_counts:
        errors.append(f"S4 结果计数不精确：{counts!r}")
    return errors


def validate_identity(identity: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if identity.get("schema_version") != "country_outage_p1_candidate_identity_v2":
        errors.append("候选身份 schema_version 错误")
    canonical = identity.get("basis_canonical_json")
    basis = identity.get("basis")
    if not isinstance(canonical, str) or not canonical:
        return errors + ["候选身份缺少 basis_canonical_json"]
    try:
        parsed_basis = json.loads(canonical)
    except json.JSONDecodeError:
        return errors + ["basis_canonical_json 不是合法 JSON"]
    if parsed_basis != basis:
        errors.append("basis_canonical_json 与 basis 不一致")
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if identity.get("identity_basis_sha256") != digest:
        errors.append("候选身份摘要不匹配")
    if identity.get("candidate_id") != f"p1-runtime-v2-{digest[:16]}":
        errors.append("candidate_id 未由完整身份基线派生")
    if not isinstance(basis, dict):
        return errors + ["候选身份 basis 必须是对象"]
    if basis.get("base_spec_commit") != "6cb2bd3":
        errors.append("候选身份未绑定 Task Spec 基线提交")
    if basis.get("p0_entry_revision") != EXPECTED_REVISION:
        errors.append("候选身份未绑定 P0 v1.3 修订版")
    if basis.get("collector_id") != EXPECTED_COLLECTOR:
        errors.append("候选身份不是 RRC25-only")
    model = basis.get("semantic_model")
    if not isinstance(model, dict) or not all(
        isinstance(model.get(key), str) and model.get(key)
        for key in ("provider", "model", "model_identity")
    ):
        errors.append("候选身份未完整绑定语义模型")
    artifacts = basis.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return errors + ["候选身份未绑定关键制品"]
    paths: set[str] = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            errors.append(f"候选制品 {index} 不是对象")
            continue
        relative = artifact.get("path")
        target = safe_repository_path(relative, f"artifact[{index}]")
        if target is None or not target.is_file():
            errors.append(f"候选制品不存在或越界：{relative!r}")
            continue
        if relative in paths:
            errors.append(f"候选制品重复：{relative}")
        paths.add(str(relative))
        if artifact.get("size_bytes") != target.stat().st_size:
            errors.append(f"候选制品大小漂移：{relative}")
        if artifact.get("sha256") != sha256(target):
            errors.append(f"候选制品哈希漂移：{relative}")
    required_paths = {
        "docs/agent/P1-聊天问答/Task-Spec-最终验收文档.md",
        "docs/agent/P1-聊天问答/Plan-分阶段计划.md",
        "contracts/agent/country-outage-p1-runtime-v2/capability-catalog.json",
        "contracts/agent/country-outage-p1-runtime-v2/tool-contracts.json",
        "contracts/agent/country-outage-p1-runtime-v2/semantic-plan.schema.json",
        "contracts/agent/country-outage-p1-runtime-v2/policy.json",
        "agent-sidecar/src/chat/runtime-v2-semantic.ts",
        "agent-sidecar/src/chat/runtime-v2-conversation.ts",
        "evaluation/country-outage/p1-runtime-v2/s4-runtime-budgets.json",
        "evaluation/country-outage/p0-v1-3/manifest.json",
    }
    missing = sorted(required_paths - paths)
    if missing:
        errors.append(f"候选身份缺少关键制品：{missing}")
    return errors


def validate_budgets(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if document.get("collector_id") != EXPECTED_COLLECTOR:
        errors.append("运行预算不是 RRC25-only")
    budgets = document.get("budgets")
    if not isinstance(budgets, dict):
        return errors + ["运行预算 budgets 缺失"]
    numeric = [
        "semantic_model_timeout_ms",
        "backend_proxy_connect_timeout_ms",
        "backend_proxy_read_timeout_ms",
        "upstream_data_api_timeout_ms",
        "registered_read_tool_timeout_ms",
        "deterministic_operator_timeout_ms",
        "conversation_ttl_ms",
    ]
    if any(not isinstance(budgets.get(key), int) or budgets[key] <= 0 for key in numeric):
        errors.append("运行预算必须为正整数")
        return errors
    if budgets["semantic_model_timeout_ms"] >= budgets["backend_proxy_read_timeout_ms"]:
        errors.append("模型超时必须早于代理读超时")
    if budgets["upstream_data_api_timeout_ms"] > budgets["registered_read_tool_timeout_ms"]:
        errors.append("上游 API 超时不得超过 Tool 超时")
    retry = document.get("retry_policy")
    if not isinstance(retry, dict) or retry.get("automatic_retry_inside_turn") is not False:
        errors.append("候选内部不得在同轮自动重试")
    observed = document.get("observed_local_behavior")
    if not isinstance(observed, dict) or observed.get("production_slo_claim") is not False:
        errors.append("本地预算不得声称生产 SLO")
    return errors


def validate_manifest_linkage(
    manifest: dict[str, Any], identity: dict[str, Any], results: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    candidate_id = identity.get("candidate_id")
    if manifest.get("candidate_id") != candidate_id:
        errors.append("manifest 与候选身份 candidate_id 不一致")
    if results.get("candidate_id") != candidate_id:
        errors.append("35 案例结果与候选身份 candidate_id 不一致")
    if manifest.get("p0_entry_revision") != EXPECTED_REVISION:
        errors.append("manifest 的 P0 入口修订版错误")
    if manifest.get("collector_id") != EXPECTED_COLLECTOR:
        errors.append("manifest 不是 RRC25-only")
    return errors


def validate_browser_joint_acceptance(
    browser: dict[str, Any],
    joint: dict[str, Any],
    identity: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    candidate_id = identity.get("candidate_id")
    if browser.get("schema_version") != (
        "country_outage_p1_s4_browser_api_conversation_v1"
    ):
        errors.append("浏览器/API 回执 schema_version 错误")
    if joint.get("schema_version") != "country_outage_p1_s4_joint_acceptance_v2":
        errors.append("Joint Acceptance schema_version 错误")
    if browser.get("candidate_id") != candidate_id:
        errors.append("浏览器/API 回执与当前候选身份不一致")
    if joint.get("candidate_id") != candidate_id:
        errors.append("Joint Acceptance 与当前候选身份不一致")
    if browser.get("http_status") != 200:
        errors.append("浏览器同会话 API 回执不是 HTTP 200")
    raw = browser.get("raw_response")
    if isinstance(raw, dict) and isinstance(raw.get("conversation"), dict):
        conversation = raw["conversation"]
    else:
        conversation = raw
    if not isinstance(conversation, dict):
        return errors + ["浏览器/API 回执缺少完整原始 conversation"]
    conversation_id = conversation.get("conversation_id")
    journey = joint.get("browser_journey")
    if (
        not isinstance(conversation_id, str)
        or browser.get("conversation_id") != conversation_id
        or not isinstance(journey, dict)
        or journey.get("conversation_id") != conversation_id
    ):
        errors.append("浏览器、API 与 Joint Artifact 不是同一会话")
    binding = conversation.get("binding")
    expected_binding = identity.get("basis", {}).get("event_binding", {})
    for key in (
        "event_type", "incident_id", "publication_id", "revision",
        "collector_id", "cohort_id", "data_through", "is_final_in_data_range",
    ):
        expected_value = (
            identity.get("basis", {}).get("collector_id")
            if key == "collector_id" else expected_binding.get(key)
        )
        if not isinstance(binding, dict) or binding.get(key) != expected_value:
            errors.append(f"浏览器会话 binding.{key} 与候选身份不一致")
    turns = conversation.get("turns")
    expected_turns = [
        {
            "question": "按时间线列出这次事件的已知事实。",
            "answerability": "supported",
            "goal_kinds": ["fact_timeline"],
            "evidence_count": 18,
        },
        {
            "question": "伊朗人现在还有互联网吗，是不是全国都断了？",
            "answerability": "partial",
            "goal_kinds": [
                "current_prefix_state", "real_user_or_national_impact"
            ],
            "evidence_count": 5,
        },
        {
            "question": "这次观测覆盖多大范围？",
            "answerability": "supported",
            "goal_kinds": ["current_scope"],
            "evidence_count": 9,
        },
    ]
    if not isinstance(turns, list) or len(turns) != 3:
        return errors + ["浏览器同会话必须精确包含三轮联合旅程"]
    joint_turns = journey.get("turns") if isinstance(journey, dict) else None
    if not isinstance(joint_turns, list) or len(joint_turns) != 3:
        errors.append("Joint Artifact 缺少三轮逐轮摘要")
        joint_turns = []
    for index, (turn, expected) in enumerate(zip(turns, expected_turns), start=1):
        if not isinstance(turn, dict):
            errors.append(f"浏览器第 {index} 轮不是对象")
            continue
        answer = turn.get("answer")
        if (
            turn.get("turn_number") != index
            or turn.get("question") != expected["question"]
            or turn.get("state") != "completed"
            or not isinstance(answer, dict)
            or answer.get("answerability") != expected["answerability"]
        ):
            errors.append(f"浏览器第 {index} 轮问题、状态或可回答性漂移")
            continue
        goals = answer.get("semantic_plan", {}).get(
            "user_goal_plan", {}
        ).get("goals")
        kinds = [
            item.get("normalized_kind")
            for item in goals
            if isinstance(item, dict)
        ] if isinstance(goals, list) else []
        if kinds != expected["goal_kinds"]:
            errors.append(f"浏览器第 {index} 轮 UserGoalPlan 漂移：{kinds}")
        evidence = answer.get("evidence")
        trace = answer.get("execution_trace")
        if not isinstance(evidence, list) or len(evidence) != expected["evidence_count"]:
            errors.append(f"浏览器第 {index} 轮证据数不闭合")
        if (
            not isinstance(trace, dict)
            or trace.get("model_generated_fact_count") != 0
            or answer.get("validation", {}).get("grounding_legality") != "passed"
        ):
            errors.append(f"浏览器第 {index} 轮 Grounding/模型事实硬门失败")
        if index <= len(joint_turns):
            summary = joint_turns[index - 1]
            if (
                not isinstance(summary, dict)
                or summary.get("question") != expected["question"]
                or summary.get("answerability") != expected["answerability"]
                or summary.get("user_goal_kinds") != expected["goal_kinds"]
                or summary.get("evidence_count") != expected["evidence_count"]
            ):
                errors.append(f"Joint Artifact 第 {index} 轮没有忠实摘要原始 API")

    timeline = turns[0].get("answer", {})
    timeline_nodes = timeline.get("execution_trace", {}).get("nodes")
    op03 = next(
        (
            node for node in timeline_nodes
            if isinstance(node, dict) and node.get("execution_unit") == "OP-03"
        ),
        None,
    ) if isinstance(timeline_nodes, list) else None
    expected_op03_refs = [
        *(f"derived.fact_timeline.ordered_fact_nodes.{index}" for index in range(6)),
        "derived.fact_timeline.terminal_unknown",
    ]
    if (
        not isinstance(op03, dict)
        or op03.get("status") != "passed"
        or op03.get("evidence_refs") != expected_op03_refs
    ):
        errors.append("CAP-018 时间线缺少当前候选 OP-03 六节点与 terminal unknown 回执")
    timeline_evidence = {
        item.get("evidence_ref"): item
        for item in timeline.get("evidence", [])
        if isinstance(item, dict)
    }
    if any(reference not in timeline_evidence for reference in expected_op03_refs):
        errors.append("CAP-018 OP-03 证据指针未解析到当前浏览器/API 回执")
    elif timeline_evidence["derived.fact_timeline.terminal_unknown"].get(
        "value"
    ) != "event_end_unknown":
        errors.append("CAP-018 终点未知语义漂移")

    raw_pointer = journey.get("raw_api_receipt") if isinstance(journey, dict) else None
    if not isinstance(raw_pointer, dict) or raw_pointer != {
        "artifact": (
            "evaluation/country-outage/p1-runtime-v2/"
            "s4-browser-api-conversation.json"
        ),
        "json_pointer": "/raw_response",
        "http_status": 200,
    }:
        errors.append("Joint Artifact 原始 API 指针不闭合")
    boundary = joint.get("boundary")
    if not isinstance(boundary, dict) or any(
        boundary.get(key) is not value
        for key, value in {
            "local_candidate_only": True,
            "merged": False,
            "deployed": False,
            "production_verified": False,
            "p2_or_rca": False,
            "external_sources_connected": False,
        }.items()
    ):
        errors.append("Joint Acceptance 越过本地/RRC25/P1 边界")
    return errors


def main() -> int:
    documents = {
        "p0": read_json(P0_CASES_PATH),
        "p0_base": read_json(P0_BASE_CASES_PATH),
        "results": read_json(S4_RESULTS_PATH),
        "live": read_json(LIVE_EVIDENCE_PATH),
        "semantic_candidate": read_json(SEMANTIC_CANDIDATE_PATH),
        "semantic_evaluation": read_json(SEMANTIC_EVALUATION_PATH),
        "identity": read_json(IDENTITY_PATH),
        "budgets": read_json(BUDGETS_PATH),
        "manifest": read_json(MANIFEST_PATH),
        "browser": read_json(BROWSER_API_PATH),
        "joint": read_json(JOINT_ACCEPTANCE_PATH),
    }
    errors = []
    errors.extend(
        validate_results(
            documents["p0"], documents["results"], documents["live"]
        )
    )
    errors.extend(
        validate_live_evidence(
            documents["p0"],
            documents["p0_base"],
            documents["live"],
            documents["identity"],
        )
    )
    errors.extend(
        validate_semantic_current_candidate(
            documents["semantic_candidate"],
            documents["semantic_evaluation"],
            documents["identity"],
        )
    )
    errors.extend(validate_identity(documents["identity"]))
    errors.extend(validate_budgets(documents["budgets"]))
    errors.extend(validate_browser_joint_acceptance(
        documents["browser"], documents["joint"], documents["identity"]
    ))
    errors.extend(
        validate_manifest_linkage(
            documents["manifest"], documents["identity"], documents["results"]
        )
    )
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(
        "PASS: S4 35 案原始回执、当前 Prompt 语义、100% Grounding、"
        "候选身份、运行预算与 manifest 链接闭合"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
