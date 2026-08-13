#!/usr/bin/env python3
"""生成 P2-S1 阶段测试的可重放、规范化运行回执。

本脚本只执行冻结测试集合并记录真实子进程结果；不修改业务状态。输出中的
``normalized_output_sha256`` 来自实际 stdout/stderr，在剔除非确定性耗时后计算。
阶段 Hook 以固定 runner 字节、测试字节、实现字节和回执文件摘要共同验收，
调用方不能用手填 ``passed=true`` 替代真实运行。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[4]
RUNNER_VERSION = "1.0.0"
RUNNER_PATH = "contracts/agent/country-outage-p2-s1-implementation/tools/run_stage_tests.py"

W1_UNITS = [
    "TOOL-07", "TOOL-08", "TOOL-09", "TOOL-10",
    *[f"OP-{number:02d}" for number in range(5, 15)], "OP-35", "OP-36",
]
W2_UNITS = ["TOOL-12", *[f"OP-{number:02d}" for number in range(15, 29)]]
W3_UNITS = ["OP-38", "OP-39"]
W4_UNITS = ["TOOL-11", *[f"OP-{number:02d}" for number in range(29, 34)], "OP-37"]


def wave_units_for_suite(suite_id: str) -> list[str]:
    wave = suite_id.split("-", 1)[0]
    return {"w1": W1_UNITS, "w2": W2_UNITS, "w3": W3_UNITS, "w4": W4_UNITS}[wave]


def _test_id(module: str, class_name: str, method: str) -> str:
    return f"{module}.{class_name}.{method}"


TOOLS = "backend.web.tests.test_country_outage_p2_s1_tools"
OPERATORS = "backend.web.tests.test_country_outage_p2_s1_operators"

SUITES: dict[str, dict[str, Any]] = {
    "w0-python": {
        "stage": "W0",
        "category": "source_and_store_positive_boundary_attack",
        "modules": [
            "dev.tests.test_country_outage_p2_s1_w0_source_governance",
            "backend.web.tests.test_country_outage_p2_s1_source_store",
        ],
        "tested_unit_ids": [],
        "artifacts": [
            "dev/tests/test_country_outage_p2_s1_w0_source_governance.py",
            "backend/web/tests/test_country_outage_p2_s1_source_store.py",
            "tools/build_country_outage_p2_s1_source_views.py",
            "backend/services/country_outage_p2_s1_source_store.py",
        ],
    },
    "w0-typescript": {
        "stage": "W0",
        "category": "registry_and_receipt_positive_boundary_attack",
        "typescript": True,
        "tests": ["agent-sidecar/tests/p2-s1-w0-source-governance.test.ts"],
        "tested_unit_ids": [],
        "artifacts": [
            "agent-sidecar/tests/p2-s1-w0-source-governance.test.ts",
            "agent-sidecar/src/chat/p2-s1-registry-runtime.ts",
            "agent-sidecar/src/chat/p2-s1-trusted-receipt-store.ts",
        ],
    },
    "w1-positive": {
        "stage": "W1", "category": "positive", "tested_unit_ids": W1_UNITS,
        "tests": [
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_tool07_reads_only_fixed_cohort_population_and_filters_materialized_fields"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_tool08_exact_state_and_half_open_range_are_atomic_row_predicates"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_tool09_and_tool10_keep_distinct_fact_populations"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_actual_request_result_and_failure_envelopes_validate_draft202012_schema"),
            _test_id(OPERATORS, "OperatorW1Tests", "test_op05_three_keys_competition_rank_and_position"),
            _test_id(OPERATORS, "OperatorW1Tests", "test_state_first_last_cutoff_intervals_and_censoring"),
            _test_id(OPERATORS, "OperatorW1Tests", "test_peak_ratio_and_exact_ratio_rank"),
            _test_id(OPERATORS, "OperatorW1Tests", "test_threshold_crossing_left_censored_gap_and_rank"),
        ],
    },
    "w1-boundary": {
        "stage": "W1", "category": "boundary", "tested_unit_ids": W1_UNITS,
        "tests": [
            _test_id(OPERATORS, "OperatorW1Tests", "test_interval_gap_and_unknown_break_runs"),
            _test_id(OPERATORS, "OperatorW1Tests", "test_complete_empty_requires_valid_population_binding"),
            _test_id(OPERATORS, "OperatorW1Tests", "test_op13_asn_binding_normal_ties_and_attacks"),
            _test_id(OPERATORS, "OperatorAtomicityAndBoundaryTests", "test_actual_input_output_examples_validate_frozen_draft202012_schema"),
        ],
    },
    "w1-attack": {
        "stage": "W1", "category": "attack", "tested_unit_ids": W1_UNITS,
        "tests": [
            _test_id(OPERATORS, "OperatorW1Tests", "test_op10_op36_trusted_projection_binding_attacks"),
            _test_id(OPERATORS, "OperatorAtomicityAndBoundaryTests", "test_cross_publication_and_incomplete_inputs_fail_closed"),
            _test_id(OPERATORS, "OperatorAtomicityAndBoundaryTests", "test_operator_functions_do_not_call_other_operator_functions"),
            _test_id(OPERATORS, "OperatorAtomicityAndBoundaryTests", "test_module_has_no_file_network_tool_or_model_dependency"),
        ],
    },
    "w2-positive": {
        "stage": "W2", "category": "positive", "tested_unit_ids": W2_UNITS,
        "tests": [
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_tool12_contains_asn_uses_verified_native_index"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_tool12_contains_asn_zero_is_complete_empty"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_tool12_anchor_and_contains_intersection_binds_all_native_receipt_fields"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_deterministic_replay_preserves_ids_digests_and_receipts"),
            _test_id(OPERATORS, "OperatorW2Tests", "test_path_positions_prepend_neighbors_and_order"),
            _test_id(OPERATORS, "OperatorW2Tests", "test_unordered_path_is_not_linearized"),
            _test_id(OPERATORS, "OperatorW2Tests", "test_path_prefix_path_direction_projections"),
            _test_id(OPERATORS, "OperatorW2Tests", "test_observed_downstream_projection_not_customer_cone"),
            _test_id(OPERATORS, "OperatorW2Tests", "test_independent_count_operators_and_tamper"),
            _test_id(OPERATORS, "OperatorW2Tests", "test_set_operations_and_empty_semantics"),
        ],
    },
    "w2-boundary": {
        "stage": "W2", "category": "boundary", "tested_unit_ids": W2_UNITS,
        "tests": [
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_tool12_noneligible_anchor_is_unsupported_with_trusted_failure_receipt"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_tool12_eligible_anchor_with_additional_zero_match_is_complete_empty"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_input_contract_rejects_wrong_shapes_ranges_and_silent_rounding"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_actual_request_result_and_failure_envelopes_validate_draft202012_schema"),
            _test_id(OPERATORS, "OperatorW2Tests", "test_set_operations_and_empty_semantics"),
            _test_id(OPERATORS, "OperatorAtomicityAndBoundaryTests", "test_actual_input_output_examples_validate_frozen_draft202012_schema"),
        ],
    },
    "w2-attack": {
        "stage": "W2", "category": "attack", "tested_unit_ids": W2_UNITS,
        "tests": [
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_query_receipt_tamper_is_detected"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_store_index_object_replacement_after_verification_fails_before_receipt"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_store_row_object_replacement_after_verification_fails_before_receipt"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_unsupported_fields_false_anchor_and_deferred_tool_fail_closed"),
            _test_id(OPERATORS, "OperatorW2Tests", "test_op15_compact_receipt_requires_resolved_full_output"),
            _test_id(OPERATORS, "OperatorW2Tests", "test_set_population_attacks_fail_closed"),
            _test_id(OPERATORS, "OperatorAtomicityAndBoundaryTests", "test_operator_functions_do_not_call_other_operator_functions"),
        ],
    },
    "w3-positive": {
        "stage": "W3", "category": "positive", "tested_unit_ids": W3_UNITS,
        "tests": [
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op38_half_open_overlap_empty_and_interval_attacks"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op39_prefix_projection_dedup_empty_and_attacks"),
        ],
    },
    "w3-boundary": {
        "stage": "W3", "category": "boundary", "tested_unit_ids": W3_UNITS,
        "tests": [
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op38_half_open_overlap_empty_and_interval_attacks"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op39_prefix_projection_dedup_empty_and_attacks"),
        ],
    },
    "w3-attack": {
        "stage": "W3", "category": "attack", "tested_unit_ids": W3_UNITS,
        "tests": [
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op38_half_open_overlap_empty_and_interval_attacks"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op39_prefix_projection_dedup_empty_and_attacks"),
        ],
    },
    "w4-positive": {
        "stage": "W4", "category": "positive", "tested_unit_ids": W4_UNITS,
        "tests": [
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_tool11_reads_one_exact_state_population_without_replay_or_second_population"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op29_directed_relations_missing_not_comparable_and_attacks"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_vp_consistency_precedence_empty_and_attacks"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op33_exact_join_preserves_unmatched_and_rejects_future_fill"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op37_only_same_slot_verified_exclusive_is_conflict_and_receipt_attacks"),
        ],
    },
    "w4-boundary": {
        "stage": "W4", "category": "boundary", "tested_unit_ids": W4_UNITS,
        "tests": [
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_tool11_contains_asn_uses_same_population_native_index_and_closes_empty"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_tool11_requires_exact_grid_point_and_never_fills_nearest_or_future_state"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op29_directed_relations_missing_not_comparable_and_attacks"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_vp_consistency_precedence_empty_and_attacks"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op33_exact_join_preserves_unmatched_and_rejects_future_fill"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op37_only_same_slot_verified_exclusive_is_conflict_and_receipt_attacks"),
        ],
    },
    "w4-attack": {
        "stage": "W4", "category": "attack", "tested_unit_ids": W4_UNITS,
        "tests": [
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_tool11_query_receipt_hmac_binds_target_time_index_and_members"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_tool11_rejects_query_time_join_replay_and_nearest_state_controls"),
            _test_id(TOOLS, "CountryOutageP2S1ToolsTest", "test_tool11_index_content_profile_and_row_tamper_fail_before_receipt"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op29_directed_relations_missing_not_comparable_and_attacks"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_vp_consistency_precedence_empty_and_attacks"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op33_exact_join_preserves_unmatched_and_rejects_future_fill"),
            _test_id(OPERATORS, "OperatorW3W4Tests", "test_op37_only_same_slot_verified_exclusive_is_conflict_and_receipt_attacks"),
        ],
    },
}


def test_case_units(suite_id: str, test_id: str) -> list[str]:
    """登记每个实际测试选择器直接覆盖的原子单元，禁止整波自报。"""

    name = test_id.rsplit(".", 1)[-1]
    if suite_id.startswith("w0-"):
        return []
    if name == "test_actual_input_output_examples_validate_frozen_draft202012_schema":
        return [unit for unit in wave_units_for_suite(suite_id) if unit.startswith("OP-")]
    if name == "test_actual_request_result_and_failure_envelopes_validate_draft202012_schema":
        if suite_id.startswith("w1-"):
            return ["TOOL-07", "TOOL-08", "TOOL-09", "TOOL-10"]
        if suite_id.startswith("w2-"):
            return ["TOOL-12"]
        return ["TOOL-11"]
    explicit: dict[str, list[str]] = {
        "test_tool07_reads_only_fixed_cohort_population_and_filters_materialized_fields": ["TOOL-07"],
        "test_tool08_exact_state_and_half_open_range_are_atomic_row_predicates": ["TOOL-08"],
        "test_tool09_and_tool10_keep_distinct_fact_populations": ["TOOL-09", "TOOL-10"],
        "test_op05_three_keys_competition_rank_and_position": ["OP-05"],
        "test_state_first_last_cutoff_intervals_and_censoring": ["OP-06", "OP-07", "OP-08", "OP-35"],
        "test_peak_ratio_and_exact_ratio_rank": ["OP-09", "OP-10", "OP-14"],
        "test_threshold_crossing_left_censored_gap_and_rank": ["OP-12", "OP-36"],
        "test_interval_gap_and_unknown_break_runs": ["OP-07"],
        "test_complete_empty_requires_valid_population_binding": ["OP-06"],
        "test_op13_asn_binding_normal_ties_and_attacks": ["OP-11", "OP-13"],
        "test_op10_op36_trusted_projection_binding_attacks": ["OP-10", "OP-12", "OP-14", "OP-36"],
        "test_cross_publication_and_incomplete_inputs_fail_closed": ["OP-05"],
        "test_operator_functions_do_not_call_other_operator_functions": [unit for unit in wave_units_for_suite(suite_id) if unit.startswith("OP-")],
        "test_module_has_no_file_network_tool_or_model_dependency": [unit for unit in wave_units_for_suite(suite_id) if unit.startswith("OP-")],
        "test_tool12_contains_asn_uses_verified_native_index": ["TOOL-12"],
        "test_tool12_contains_asn_zero_is_complete_empty": ["TOOL-12"],
        "test_tool12_anchor_and_contains_intersection_binds_all_native_receipt_fields": ["TOOL-12"],
        "test_deterministic_replay_preserves_ids_digests_and_receipts": ["TOOL-12"],
        "test_path_positions_prepend_neighbors_and_order": ["OP-15", "OP-16", "OP-17"],
        "test_unordered_path_is_not_linearized": ["OP-15"],
        "test_path_prefix_path_direction_projections": ["OP-18", "OP-20", "OP-21"],
        "test_observed_downstream_projection_not_customer_cone": ["OP-19"],
        "test_independent_count_operators_and_tamper": ["OP-22", "OP-23", "OP-24"],
        "test_set_operations_and_empty_semantics": ["OP-25", "OP-26", "OP-27", "OP-28"],
        "test_tool12_noneligible_anchor_is_unsupported_with_trusted_failure_receipt": ["TOOL-12"],
        "test_tool12_eligible_anchor_with_additional_zero_match_is_complete_empty": ["TOOL-12"],
        "test_input_contract_rejects_wrong_shapes_ranges_and_silent_rounding": ["TOOL-12"],
        "test_page_token_tamper_and_cross_tool_reuse_fail_closed": ["TOOL-12"],
        "test_query_receipt_tamper_is_detected": ["TOOL-12"],
        "test_store_index_object_replacement_after_verification_fails_before_receipt": ["TOOL-12"],
        "test_store_row_object_replacement_after_verification_fails_before_receipt": ["TOOL-12"],
        "test_unsupported_fields_false_anchor_and_deferred_tool_fail_closed": ["TOOL-12"],
        "test_op15_compact_receipt_requires_resolved_full_output": ["OP-15", "OP-17"],
        "test_set_population_attacks_fail_closed": ["OP-25"],
        "test_tool11_reads_one_exact_state_population_without_replay_or_second_population": ["TOOL-11"],
        "test_tool11_contains_asn_uses_same_population_native_index_and_closes_empty": ["TOOL-11"],
        "test_tool11_requires_exact_grid_point_and_never_fills_nearest_or_future_state": ["TOOL-11"],
        "test_tool11_query_receipt_hmac_binds_target_time_index_and_members": ["TOOL-11"],
        "test_tool11_rejects_query_time_join_replay_and_nearest_state_controls": ["TOOL-11"],
        "test_tool11_index_content_profile_and_row_tamper_fail_before_receipt": ["TOOL-11"],
        "test_op29_directed_relations_missing_not_comparable_and_attacks": ["OP-29"],
        "test_vp_consistency_precedence_empty_and_attacks": ["OP-30", "OP-31", "OP-32"],
        "test_op33_exact_join_preserves_unmatched_and_rejects_future_fill": ["OP-33"],
        "test_op37_only_same_slot_verified_exclusive_is_conflict_and_receipt_attacks": ["OP-29", "OP-37"],
        "test_op38_half_open_overlap_empty_and_interval_attacks": ["OP-38"],
        "test_op39_prefix_projection_dedup_empty_and_attacks": ["OP-39"],
    }
    units = explicit.get(name)
    if units is None:
        raise RuntimeError(f"{suite_id} 测试 {test_id} 缺少逐单元 coverage 登记")
    return units


def coverage_for_test_case(suite_id: str, test_id: str) -> dict[str, Any]:
    """区分实际调用、实际调用并验 Schema，以及不执行单元的静态检查。"""

    name = test_id.rsplit(".", 1)[-1]
    unit_ids = test_case_units(suite_id, test_id)
    if name in {
        "test_operator_functions_do_not_call_other_operator_functions",
        "test_module_has_no_file_network_tool_or_model_dependency",
    }:
        coverage_kind = "static_atomicity_analysis"
        executed_unit_ids: list[str] = []
    elif name in {
        "test_actual_input_output_examples_validate_frozen_draft202012_schema",
        "test_actual_request_result_and_failure_envelopes_validate_draft202012_schema",
    }:
        coverage_kind = "direct_execution_and_schema_validation"
        executed_unit_ids = list(unit_ids)
    else:
        coverage_kind = "direct_execution"
        executed_unit_ids = list(unit_ids)
    return {
        "test_id": test_id,
        "coverage_kind": coverage_kind,
        "unit_ids": unit_ids,
        "executed_unit_ids": executed_unit_ids,
    }


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_binding(relative: str) -> dict[str, Any]:
    path = REPO_ROOT / relative
    data = path.read_bytes()
    return {"path": relative, "size_bytes": len(data), "sha256": sha256_bytes(data)}


def normalize_output(text: str) -> str:
    text = text.replace(str(REPO_ROOT), "<REPO_ROOT>")
    text = re.sub(r"Ran (\d+) tests? in [0-9.]+s", r"Ran \1 tests in <ELAPSED>", text)
    text = re.sub(r"duration_ms: [0-9.]+", "duration_ms: <ELAPSED>", text)
    text = re.sub(r"# duration_ms [0-9.]+", "# duration_ms <ELAPSED>", text)
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").splitlines()).strip() + "\n"


def parse_python_count(text: str) -> int:
    match = re.search(r"Ran (\d+) tests? in", text)
    if match is None:
        raise RuntimeError("无法从 unittest 输出解析测试数量")
    return int(match.group(1))


def parse_node_summary(text: str, key: str) -> int:
    matches = re.findall(rf"^# {re.escape(key)} (\d+)$", text, flags=re.MULTILINE)
    if not matches:
        raise RuntimeError(f"无法从 node:test 输出解析 {key}")
    return int(matches[-1])


def run_suite(suite_id: str) -> dict[str, Any]:
    definition = SUITES[suite_id]
    started = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if definition.get("typescript"):
        command = ["bash", "-lc", "npm run build && node --test dist/tests/p2-s1-w0-source-governance.test.js"]
        cwd = REPO_ROOT / "agent-sidecar"
    else:
        selectors = definition.get("tests") or definition.get("modules")
        command = [sys.executable, "-m", "unittest", "-v", *selectors]
        cwd = REPO_ROOT
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    finished = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    raw_output = f"[stdout]\n{completed.stdout}\n[stderr]\n{completed.stderr}"
    normalized = normalize_output(raw_output)
    if definition.get("typescript"):
        tests_run = parse_node_summary(raw_output, "tests")
        failures = parse_node_summary(raw_output, "fail")
        skipped = parse_node_summary(raw_output, "skipped")
        errors = 0
    else:
        tests_run = parse_python_count(raw_output)
        failures = 0 if completed.returncode == 0 else 1
        errors = 0 if completed.returncode == 0 else 1
        skipped = 0
    selected_test_ids = list(definition.get("tests") or definition.get("modules") or [])
    test_case_coverage = [coverage_for_test_case(suite_id, test_id) for test_id in selected_test_ids]
    covered_unit_ids = sorted({
        unit_id for item in test_case_coverage for unit_id in item["unit_ids"]
    })
    executed_unit_ids = sorted({
        unit_id for item in test_case_coverage for unit_id in item["executed_unit_ids"]
    })
    record: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_stage_test_run_receipt_v1",
        "runner_id": "country_outage_p2_s1_stage_test_runner",
        "runner_version": RUNNER_VERSION,
        "suite_id": suite_id,
        "stage": definition["stage"],
        "category": definition["category"],
        "started_at_utc": started,
        "completed_at_utc": finished,
        "command": ["python3" if item == sys.executable else item for item in command],
        "working_directory": "." if cwd == REPO_ROOT else cwd.relative_to(REPO_ROOT).as_posix(),
        "selected_test_ids": selected_test_ids,
        "test_case_coverage": test_case_coverage,
        "tested_unit_ids": covered_unit_ids,
        "tested_execution_unit_ids": executed_unit_ids,
        "artifact_bindings": [file_binding(RUNNER_PATH), *[
            file_binding(relative) for relative in definition["artifacts"]
        ]]
        if "artifacts" in definition else [
            file_binding(RUNNER_PATH),
            file_binding("backend/web/tests/test_country_outage_p2_s1_tools.py"),
            file_binding("backend/web/tests/test_country_outage_p2_s1_operators.py"),
            file_binding("backend/services/country_outage_p2_s1_tools.py"),
            file_binding("backend/services/country_outage_p2_s1_operators.py"),
            file_binding("contracts/agent/country-outage-p2-s1-implementation/w1-w2-tool-runtime.schema.json"),
            file_binding("contracts/agent/country-outage-p2-s1-implementation/w1-w2-structural-binding.schema.json"),
            file_binding("contracts/agent/country-outage-p2-s1-execution-unit-design/operator-contract.schema.json"),
        ],
        "exit_code": completed.returncode,
        "tests_run": tests_run,
        "failure_count": failures,
        "error_count": errors,
        "skipped_count": skipped,
        "passed": completed.returncode == 0 and failures == 0 and errors == 0,
        "normalized_output": normalized,
        "normalized_output_sha256": sha256_bytes(normalized.encode("utf-8")),
    }
    record["receipt_digest"] = sha256_bytes(canonical_json(record))
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=sorted(SUITES), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    record = run_suite(args.suite)
    output = json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(output)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
