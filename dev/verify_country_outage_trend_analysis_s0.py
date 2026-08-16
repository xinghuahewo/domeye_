#!/usr/bin/env python3
"""验证 S0 效果、语义、验收曲线与价值基线；不验证趋势实现。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "dev"
    / "fixtures"
    / "country-outage-trend-analysis-s0-v1.json"
)
SCHEMA_PATH = (
    REPOSITORY_ROOT
    / "contracts"
    / "agent"
    / "country-outage-trend-analysis-s0-v1.schema.json"
)
BASELINE_DOC_PATH = REPOSITORY_ROOT / "docs" / "国家中断趋势分析S0基线.md"

EXPECTED_CURVE_TYPES = (
    "single_wave",
    "multi_wave",
    "oscillation",
    "plateau",
    "mixed",
    "unmatched",
    "small_denominator",
    "missing_slots",
    "unknown",
)
EXPECTED_TASK_IDS = tuple(f"UT-{index:02d}" for index in range(1, 6))
EXPECTED_REFUSAL_IDS = tuple(f"RF-{index:02d}" for index in range(1, 7))
EXPECTED_CAPABILITY_STATES = {
    "fixed_snapshot_identity": "existing_source",
    "observation_series_resource_audit": "existing_source",
    "deterministic_derived_facts": "existing_source",
    "bounded_report_qa_download": "existing_source",
    "asn_report_provenance": "data_exists_not_productized",
    "global_replay_projection": "data_exists_not_productized",
    "trend_profile": "target",
    "evidence_graph_v1": "target",
    "trend_reading_journey_and_operators": "target",
    "multi_source_hypothesis_rca": "absent_out_of_scope",
    "current_production_runtime_identity": "runtime_unverified",
}
EXPECTED_BASELINES = {
    "fixed_cohort",
    "window_start",
    "contemporaneous_reference",
    "unavailable",
}
EXPECTED_ASN_STATES = {
    "fully_visible",
    "partially_visible",
    "fully_invisible",
    "unknown",
}
REQUIRED_PROHIBITED_CAPABILITIES = {
    "multi_collector",
    "external_evidence",
    "hypothesis_generation",
    "investigation_graph",
    "network_rca",
    "database_rebuild",
    "backend_core_modification",
    "production_switch",
}
REQUIRED_PROHIBITED_CLAIMS = {
    "national_internet_outage",
    "user_impact",
    "service_impact",
    "attack",
    "cause",
    "policy_action",
    "responsibility",
    "post_window_full_recovery",
}
REQUIRED_BASELINE_DOC_PHRASES = (
    "S0 只冻结语义、验收输入与价值测量合同",
    "不是趋势功能实现证据",
    "从始至终只使用 RRC25",
    "现有源码能力",
    "数据存在但尚未产品化",
    "目标效果",
    "当前生产运行时未核验",
    "缺失不补零、不连线",
    "unknown 不并入可见状态",
    "B0",
    "B1",
    "B2",
    "TAE-01 至 TAE-15",
)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须为对象：{path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def duplicate_values(values: list[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def validate_schema_shell(schema: dict[str, Any]) -> list[str]:
    """验证本仓库依赖的 schema 外壳，不引入额外 Python 包。"""
    errors: list[str] = []
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("Schema 必须使用 JSON Schema 2020-12。")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        errors.append("Schema 顶层必须是禁止额外字段的 object。")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return errors + ["Schema 缺少 properties。"]
    if properties.get("schema_version", {}).get("const") != "country_outage_trend_analysis_s0_v1":
        errors.append("Schema 没有冻结 S0 schema_version。")
    curve = schema.get("$defs", {}).get("acceptanceCurve", {})
    curve_types = set(curve.get("properties", {}).get("type", {}).get("enum", []))
    if curve_types != set(EXPECTED_CURVE_TYPES):
        errors.append("Schema 曲线类型没有且仅有冻结的九类。")
    return errors


def validate_contract_identity(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    contract = document.get("effect_contract")
    if not isinstance(contract, dict):
        return ["缺少 effect_contract 对象。"]
    for path_key, hash_key in (
        ("acceptance_path", "acceptance_sha256"),
        ("plan_path", "plan_sha256"),
    ):
        relative = contract.get(path_key)
        expected_hash = contract.get(hash_key)
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            errors.append(f"effect_contract 缺少 {path_key}/{hash_key}。")
            continue
        path = REPOSITORY_ROOT / relative
        if not path.is_file():
            errors.append(f"效果合同文件不存在：{relative}")
            continue
        actual_hash = sha256(path)
        if actual_hash != expected_hash:
            errors.append(
                f"效果合同摘要漂移：{relative}：{actual_hash} != {expected_hash}"
            )
    commit = contract.get("contract_commit")
    if not isinstance(commit, str):
        errors.append("effect_contract 缺少 contract_commit。")
    else:
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            errors.append(f"冻结合同提交不可解析：{commit}")
    return errors


def validate_boundaries(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    boundaries = document.get("boundaries")
    if not isinstance(boundaries, dict):
        return ["缺少 boundaries 对象。"]
    expected_scalars = {
        "collector": "rrc25",
        "collector_count": 1,
        "observation_scope": "bgp_control_plane",
        "snapshot_mode": "fixed_read_only",
    }
    for key, expected in expected_scalars.items():
        if boundaries.get(key) != expected:
            errors.append(f"边界 {key} 必须为 {expected!r}。")
    if set(boundaries.get("prohibited_capabilities", [])) != REQUIRED_PROHIBITED_CAPABILITIES:
        errors.append("prohibited_capabilities 没有完整冻结能力边界。")
    if set(boundaries.get("prohibited_claims", [])) != REQUIRED_PROHIBITED_CLAIMS:
        errors.append("prohibited_claims 没有完整冻结弃答边界。")

    vocabulary = document.get("vocabulary")
    if not isinstance(vocabulary, dict):
        return errors + ["缺少 vocabulary 对象。"]
    if set(vocabulary.get("baseline_types", [])) != EXPECTED_BASELINES:
        errors.append("四种基线语义没有完整冻结。")
    if set(vocabulary.get("asn_states", [])) != EXPECTED_ASN_STATES:
        errors.append("ASN 四状态没有完整冻结或 unknown 被移除。")
    for key, values in vocabulary.items():
        if not isinstance(values, list) or not values:
            errors.append(f"vocabulary.{key} 必须是非空数组。")
        elif duplicate_values(values):
            errors.append(f"vocabulary.{key} 存在重复项：{duplicate_values(values)}")
    return errors


def validate_capabilities(document: dict[str, Any]) -> list[str]:
    inventory = document.get("capability_inventory")
    if not isinstance(inventory, list):
        return ["capability_inventory 必须为数组。"]
    errors: list[str] = []
    ids = [item.get("id") for item in inventory if isinstance(item, dict)]
    if duplicate_values([value for value in ids if isinstance(value, str)]):
        errors.append("capability_inventory 存在重复 id。")
    by_id = {
        item.get("id"): item
        for item in inventory
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if set(by_id) != set(EXPECTED_CAPABILITY_STATES):
        errors.append("能力盘点项与 S0 冻结清单不一致。")
    for capability_id, expected_state in EXPECTED_CAPABILITY_STATES.items():
        item = by_id.get(capability_id, {})
        if item.get("state") != expected_state:
            errors.append(
                f"能力 {capability_id} 状态必须为 {expected_state}，"
                f"当前为 {item.get('state')!r}。"
            )
        if not item.get("evidence") or not item.get("boundary"):
            errors.append(f"能力 {capability_id} 缺少 evidence 或 boundary。")

    contracts_path = REPOSITORY_ROOT / "agent-sidecar" / "src" / "domain" / "contracts.ts"
    contracts = contracts_path.read_text(encoding="utf-8")
    if "collectorId: 'rrc25'" not in contracts:
        errors.append("现有 Sidecar 快照合同没有保持 collectorId='rrc25'。")
    if "endpoint: 'overview' | 'series' | 'audit'" not in contracts:
        errors.append("ASN provenance 缺口基线已变化，必须重新盘点而非沿用 S0 结论。")
    return errors


def compute_key_points(slots: list[dict[str, Any]]) -> dict[str, int]:
    values = [slot["value"] for slot in slots]
    extreme_index = min(range(len(values)), key=lambda index: (values[index], index))
    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    largest_drop_end_index = min(
        range(1, len(values)), key=lambda index: (deltas[index - 1], index)
    )
    largest_recovery_end_index = min(
        range(1, len(values)), key=lambda index: (-deltas[index - 1], index)
    )
    return {
        "start_index": 0,
        "end_index": len(values) - 1,
        "extreme_index": extreme_index,
        "largest_drop_end_index": largest_drop_end_index,
        "largest_recovery_end_index": largest_recovery_end_index,
    }


def validate_curves(document: dict[str, Any]) -> list[str]:
    curves = document.get("acceptance_curves")
    if not isinstance(curves, list):
        return ["acceptance_curves 必须为数组。"]
    errors: list[str] = []
    ids = [curve.get("id") for curve in curves if isinstance(curve, dict)]
    types = [curve.get("type") for curve in curves if isinstance(curve, dict)]
    if ids != [f"CURVE-{index:02d}" for index in range(1, 10)]:
        errors.append("验收曲线必须按 CURVE-01 至 CURVE-09 顺序且唯一。")
    if tuple(types) != EXPECTED_CURVE_TYPES:
        errors.append("验收曲线必须按冻结的九类顺序且每类恰好一个。")

    for curve in curves:
        if not isinstance(curve, dict):
            errors.append("验收曲线项必须为对象。")
            continue
        curve_id = curve.get("id", "<unknown>")
        denominator = curve.get("denominator")
        slots = curve.get("slots")
        expected = curve.get("expected")
        if not isinstance(denominator, int) or denominator < 1:
            errors.append(f"{curve_id} denominator 必须是正整数。")
            continue
        if curve.get("slot_seconds") != 300:
            errors.append(f"{curve_id} 必须使用 300 秒时间槽。")
        if curve.get("baseline_type") not in EXPECTED_BASELINES:
            errors.append(f"{curve_id} 使用未冻结的基线类型。")
        if not isinstance(slots, list) or len(slots) != 12:
            errors.append(f"{curve_id} 必须恰有 12 个槽。")
            continue
        if not isinstance(expected, dict):
            errors.append(f"{curve_id} 缺少 expected。")
            continue

        state_counts: Counter[str] = Counter()
        for index, slot in enumerate(slots):
            if not isinstance(slot, dict):
                errors.append(f"{curve_id} 槽 {index} 必须为对象。")
                continue
            if slot.get("index") != index or slot.get("offset_seconds") != index * 300:
                errors.append(f"{curve_id} 槽 {index} 的索引或时间偏移不连续。")
            state = slot.get("state")
            value = slot.get("value")
            state_counts[state] += 1
            if state == "observed":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    errors.append(f"{curve_id} observed 槽 {index} 必须有数值。")
                elif not 0 <= value <= denominator:
                    errors.append(f"{curve_id} 槽 {index} 超出 0..denominator。")
            elif value is not None:
                errors.append(f"{curve_id} 非 observed 槽 {index} 必须保持 null，禁止补零。")

        observed_count = state_counts.get("observed", 0)
        if expected.get("observed_slot_count") != observed_count:
            errors.append(f"{curve_id} observed_slot_count 与槽状态不一致。")
        if expected.get("non_observed_slot_count") != len(slots) - observed_count:
            errors.append(f"{curve_id} non_observed_slot_count 与槽状态不一致。")
        if expected.get("slot_state_counts") != dict(state_counts):
            errors.append(f"{curve_id} slot_state_counts 未保持缺失与 unknown 独立语义。")

        key_points = expected.get("key_points")
        if observed_count == len(slots):
            actual = compute_key_points(slots)
            if key_points != actual:
                errors.append(f"{curve_id} 冻结关键点不可由原始槽重算：{actual}")
            if expected.get("quality_status") != "complete":
                errors.append(f"{curve_id} 完整曲线质量必须为 complete。")
        else:
            if key_points is not None:
                errors.append(f"{curve_id} 非完整曲线 S0 必须降级且不冻结跨缺槽关键点。")
            if expected.get("pattern_status") != "insufficient_data":
                errors.append(f"{curve_id} 非完整曲线必须为 insufficient_data。")

    return errors


def validate_tasks_and_value(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    tasks = document.get("user_tasks")
    refusals = document.get("refusal_cases")
    if not isinstance(tasks, list) or [item.get("id") for item in tasks] != list(EXPECTED_TASK_IDS):
        errors.append("核心用户任务必须按 UT-01 至 UT-05 完整冻结。")
    if not isinstance(refusals, list) or [item.get("id") for item in refusals] != list(EXPECTED_REFUSAL_IDS):
        errors.append("弃答集必须按 RF-01 至 RF-06 完整冻结。")
    elif any(item.get("required_disposition") != "abstain" for item in refusals):
        errors.append("所有越界问题都必须冻结为 abstain。")

    gate = document.get("value_gate")
    if not isinstance(gate, dict):
        return errors + ["缺少 value_gate。"]
    systems = [*gate.get("baselines", []), gate.get("candidate", {})]
    if [item.get("id") for item in systems] != ["B0", "B1", "B2"]:
        errors.append("Value Gate 必须区分 B0、B1、B2。")
    if any(item.get("measurement_status") != "to_measure" for item in systems):
        errors.append("S0 不得伪造 B0/B1/B2 已测结果。")
    metrics = gate.get("metrics", [])
    metric_by_id = {
        item.get("id"): item
        for item in metrics
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for metric_id in (
        "numeric_accuracy",
        "phase_boundary_consistency",
        "claim_evidence_consistency",
        "correct_abstention_rate",
        "evidence_coverage",
        "core_task_completion",
    ):
        metric = metric_by_id.get(metric_id, {})
        if metric.get("target") != 1.0 or metric.get("measurement_status") != "to_measure":
            errors.append(f"Value Gate 指标 {metric_id} 必须冻结目标 1.0 且保持待测。")
    pass_rule = gate.get("pass_rule", "")
    for phrase in ("B2", "B0/B1", "覆盖率", "中位用时", "不低于基线"):
        if phrase not in pass_rule:
            errors.append(f"Value Gate 通过规则缺少：{phrase}")
    return errors


def validate_references_and_identity(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    references = document.get("reference_data")
    if not isinstance(references, list):
        return ["reference_data 必须为数组。"]
    by_id = {
        item.get("id"): item
        for item in references
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    ir = by_id.get("historical_ir_fixed_snapshot", {})
    global_replay = by_id.get("historical_rrc25_global_replay", {})
    if ir.get("identity", {}).get("collector") != "rrc25" or ir.get("scale", {}).get("origin_asn") != 563:
        errors.append("历史 IR 固定快照引用与已验收基线不一致。")
    if ir.get("scale", {}).get("fixed_prefix_vp") != 384767:
        errors.append("历史 IR Prefix×VP 人口与已验收基线不一致。")
    replay_identity = global_replay.get("identity", {})
    if replay_identity.get("run_id") != "global_run_v1_6427ae1ad52037c83d7ec3bb9b5e7757":
        errors.append("全球重放 run_id 与历史验收记录不一致。")
    if replay_identity.get("dataset_id") != "global_dataset_v1_d015e120c2d02d39596af86ea8f8fb7c":
        errors.append("全球重放 dataset_id 与历史验收记录不一致。")
    if global_replay.get("scale", {}).get("country_and_unknown_buckets") != 241:
        errors.append("全球重放国家及未知桶规模与历史验收记录不一致。")

    identity = document.get("identity_separation")
    expected_identity = {
        "source_candidate": "contract_commit_only",
        "data_artifacts": "historical_accepted",
        "target_effects": "not_implemented_in_s0",
        "current_runtime": "runtime_unverified",
        "production_claim": "not_claimed",
    }
    if identity != expected_identity:
        errors.append("源码候选、历史数据、目标效果、运行时与生产身份没有严格分离。")
    return errors


def validate_baseline_doc() -> list[str]:
    if not BASELINE_DOC_PATH.is_file():
        return [f"S0 中文基线文档不存在：{BASELINE_DOC_PATH}"]
    text = BASELINE_DOC_PATH.read_text(encoding="utf-8")
    return [
        f"S0 中文基线文档缺少语义：{phrase}"
        for phrase in REQUIRED_BASELINE_DOC_PHRASES
        if phrase not in text
    ]


def validate(document: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    try:
        schema = load_json(SCHEMA_PATH)
        candidate = load_json(FIXTURE_PATH) if document is None else document
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [str(error)]

    errors.extend(validate_schema_shell(schema))
    if candidate.get("schema_version") != "country_outage_trend_analysis_s0_v1":
        errors.append("机器基线 schema_version 不正确。")
    if candidate.get("stage") != "S0":
        errors.append("机器基线 stage 必须为 S0。")
    if candidate.get("status") != "design_baseline_not_implementation_evidence":
        errors.append("机器基线必须明确不是实现证据。")
    errors.extend(validate_contract_identity(candidate))
    errors.extend(validate_boundaries(candidate))
    errors.extend(validate_capabilities(candidate))
    errors.extend(validate_curves(candidate))
    errors.extend(validate_tasks_and_value(candidate))
    errors.extend(validate_references_and_identity(candidate))
    errors.extend(validate_baseline_doc())
    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("国家中断趋势分析 S0 机器基线：失败", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("国家中断趋势分析 S0 机器基线：通过")
    print("- 仅证明 S0 效果、语义、曲线、任务与价值测量合同一致")
    print("- 不证明趋势功能、候选版本、生产运行时或最终 TAE 已通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
