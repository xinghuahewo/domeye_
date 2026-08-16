#!/usr/bin/env python3
"""计算 P1 S2 UserGoalPlan 子目标保真率；目标语义仍须独立专家复核。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any


GOAL_ID = re.compile(r"^goal-[1-9][0-9]*$")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 根节点不是对象")
    return value


def _normalize_and_validate_plan(
    value: Any,
    question: str,
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(value, dict):
        return None, ["user_goal_plan_not_object"], warnings
    required = {
        "plan_revision",
        "original_question",
        "goals",
        "state_proposal",
        "planner_identity",
        "confidence",
    }
    if set(value) != required:
        errors.append("user_goal_plan_fields_not_exact")
    if value.get("plan_revision") != "user-goal-plan-v2":
        errors.append("plan_revision_invalid")
    if value.get("original_question") != question:
        errors.append("original_question_drift")
    if not isinstance(value.get("planner_identity"), str) or not value["planner_identity"]:
        errors.append("planner_identity_invalid")
    confidence = value.get("confidence")
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0 <= confidence <= 1
    ):
        errors.append("confidence_invalid")

    state = value.get("state_proposal")
    if not isinstance(state, dict) or set(state) != {
        "inherit", "set", "clear", "reason_codes"
    }:
        errors.append("state_proposal_invalid")
    else:
        inherit = state.get("inherit")
        if (
            inherit == ["event_reference"]
            and state.get("set") == {}
            and state.get("clear") == []
        ):
            state = dict(state)
            state["inherit"] = []
            value = dict(value)
            value["state_proposal"] = state
            warnings.append("host_removed_redundant_event_binding_inherit")
        if state.get("inherit") != [] or state.get("set") != {} or state.get("clear") != []:
            errors.append("model_state_mutation_forbidden")
        if not isinstance(state.get("reason_codes"), list):
            errors.append("state_reason_codes_invalid")

    goals = value.get("goals")
    if not isinstance(goals, list) or not 1 <= len(goals) <= 12:
        errors.append("goals_invalid")
        return value, errors, warnings
    goal_fields = {
        "goal_id",
        "requested_goal",
        "normalized_kind",
        "entities",
        "references",
        "ambiguity",
        "context_dependencies",
    }
    for index, goal in enumerate(goals, start=1):
        if not isinstance(goal, dict) or set(goal) != goal_fields:
            errors.append(f"goal_{index}_fields_not_exact")
            continue
        if goal.get("goal_id") != f"goal-{index}" or not GOAL_ID.fullmatch(
            str(goal.get("goal_id", ""))
        ):
            errors.append(f"goal_{index}_id_invalid")
        if not isinstance(goal.get("requested_goal"), str) or not goal["requested_goal"].strip():
            errors.append(f"goal_{index}_requested_goal_missing")
        if not isinstance(goal.get("normalized_kind"), str) or not goal["normalized_kind"]:
            errors.append(f"goal_{index}_kind_invalid")
        if not isinstance(goal.get("entities"), dict):
            errors.append(f"goal_{index}_entities_invalid")
        if not isinstance(goal.get("references"), list):
            errors.append(f"goal_{index}_references_invalid")
        if goal.get("ambiguity") not in {"none", "non_blocking", "blocking"}:
            errors.append(f"goal_{index}_ambiguity_invalid")
        if not isinstance(goal.get("context_dependencies"), list):
            errors.append(f"goal_{index}_context_dependencies_invalid")
    return value, errors, warnings


def _goal_matches(reference: dict[str, Any], candidate: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    accepted_kinds = {
        reference["normalized_kind"],
        *reference.get("accepted_kind_aliases", []),
    }
    if candidate.get("normalized_kind") not in accepted_kinds:
        failures.append("normalized_kind_semantics")
    required_entities = reference.get("required_entities", {})
    candidate_entities = candidate.get("entities", {})
    if not isinstance(candidate_entities, dict) or any(
        candidate_entities.get(key) != expected
        for key, expected in required_entities.items()
    ):
        failures.append("required_entities")
    if candidate.get("ambiguity") != reference.get("ambiguity"):
        failures.append("ambiguity")
    if set(candidate.get("context_dependencies", [])) != set(
        reference.get("context_dependencies", [])
    ):
        failures.append("context_dependencies")
    if not isinstance(candidate.get("requested_goal"), str) or not candidate[
        "requested_goal"
    ].strip():
        failures.append("goal_preserved")
    return not failures, failures


def evaluate(variants: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    cases = variants.get("cases")
    results = candidate.get("results")
    if not isinstance(cases, list) or not isinstance(results, list):
        raise ValueError("variants.cases 或 candidate.results 无效")
    candidate_by_id = {
        item.get("case_id"): item
        for item in results
        if isinstance(item, dict) and isinstance(item.get("case_id"), str)
    }
    case_results: list[dict[str, Any]] = []
    correct = 0
    denominator = 0
    schema_valid_count = 0
    extra_goal_count = 0
    grounding_valid_count = 0
    for case in cases:
        case_id = case["case_id"]
        question = case["question"]
        references = case["reference_goals"]
        denominator += len(references)
        candidate_case = candidate_by_id.get(case_id)
        if isinstance(candidate_case, dict):
            grounding = candidate_case.get("grounding_plan")
            attempts = candidate_case.get("attempts")
            accepted_attempt = candidate_case.get("accepted_attempt")
            accepted_answer = None
            if isinstance(attempts, list) and isinstance(accepted_attempt, int):
                accepted = next(
                    (
                        item
                        for item in attempts
                        if isinstance(item, dict)
                        and item.get("attempt") == accepted_attempt
                    ),
                    None,
                )
                if isinstance(accepted, dict):
                    accepted_answer = accepted.get("answer")
            validation = (
                accepted_answer.get("validation")
                if isinstance(accepted_answer, dict)
                else None
            )
            if (
                isinstance(grounding, dict)
                and isinstance(grounding.get("validation"), dict)
                and grounding["validation"].get("status") == "passed"
                and isinstance(validation, dict)
                and validation.get("grounding_schema") == "passed"
                and validation.get("grounding_legality") == "passed"
            ):
                grounding_valid_count += 1
        plan_value = None if candidate_case is None else candidate_case.get("user_goal_plan")
        plan, schema_errors, warnings = _normalize_and_validate_plan(
            plan_value,
            question,
        )
        goal_results: list[dict[str, Any]] = []
        extras = 0
        if plan is not None and not schema_errors:
            schema_valid_count += 1
            goals = plan["goals"]
            for index, reference in enumerate(references):
                candidate_goal = goals[index] if index < len(goals) else {}
                passed, failures = _goal_matches(reference, candidate_goal)
                if passed:
                    correct += 1
                goal_results.append(
                    {
                        "reference_index": index + 1,
                        "passed": passed,
                        "failures": failures,
                        "candidate_kind": candidate_goal.get("normalized_kind"),
                        "requested_goal": candidate_goal.get("requested_goal"),
                        "expert_goal_semantics_required": True,
                    }
                )
            extras = max(0, len(goals) - len(references))
            denominator += extras
            extra_goal_count += extras
        else:
            goal_results = [
                {
                    "reference_index": index + 1,
                    "passed": False,
                    "failures": ["plan_schema_invalid"],
                    "candidate_kind": None,
                    "requested_goal": None,
                    "expert_goal_semantics_required": True,
                }
                for index in range(len(references))
            ]
        case_results.append(
            {
                "case_id": case_id,
                "schema_valid": not schema_errors,
                "schema_errors": schema_errors,
                "host_normalization_warnings": warnings,
                "goal_results": goal_results,
                "extra_goal_count": extras,
            }
        )
    fidelity = correct / denominator if denominator else 0.0
    return {
        "schema_version": "country_outage_p1_s2_semantic_evaluation_v1",
        "evaluated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "variant_revision": variants.get("revision"),
        "candidate_id": candidate.get("candidate_id", "unknown"),
        "candidate_identity": candidate.get("candidate_identity", "unknown"),
        "prompt_identity": candidate.get("prompt_identity", "unknown"),
        "blind_input": candidate.get("blind_input", "unknown"),
        "evaluation_characterization": (
            "冻结 runtime prompt 回归集；逐题输入未包含参考答案，"
            "但不据此宣称开放集泛化能力"
        ),
        "counts": {
            "case_count": len(cases),
            "schema_valid_case_count": schema_valid_count,
            "reference_subgoal_count": sum(len(case["reference_goals"]) for case in cases),
            "extra_goal_count": extra_goal_count,
            "scoring_denominator": denominator,
            "machine_correct_subgoal_count": correct,
        },
        "metrics": {
            "machine_goal_fidelity": fidelity,
            "required_goal_fidelity": variants["scoring"]["minimum_fidelity"],
            "machine_gate_passed": fidelity >= variants["scoring"]["minimum_fidelity"],
            "expert_goal_semantics_review": "pending",
            "final_goal_fidelity_gate": "pending_expert_review",
        },
        "grounding_legality": {
            "required": 1.0,
            "validated_case_count": grounding_valid_count,
            "total_case_count": len(cases),
            "rate": grounding_valid_count / len(cases) if cases else 0.0,
            "status": (
                "passed" if grounding_valid_count == len(cases)
                else "failed"
            ),
        },
        "case_results": case_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(_read_object(args.variants), _read_object(args.candidate))
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if result["metrics"]["machine_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
