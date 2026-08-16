#!/usr/bin/env python3
"""验证 S2 关键点、原子状态、阶段、模式降级和窗口账本。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.country_outage_trend_profile import (  # noqa: E402
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_RULE,
    analyze_trend_profile_v1,
    compile_trend_profile_v1,
)


S0_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s0-v1.json"
S1_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s1-v1.json"
S2_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s2-v1.json"
SCHEMA_PATH = REPOSITORY_ROOT / "contracts" / "agent" / "country-outage-trend-profile-v1.schema.json"
DOC_PATH = REPOSITORY_ROOT / "docs" / "国家中断趋势分析S2验收记录.md"
S1_VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s1.py"

EXPECTED_FACT_METRICS = {
    "start_to_extreme_change",
    "loss_magnitude",
    "extreme_to_end_rebound",
    "end_residual_from_start",
    "window_rebound_ratio",
    "fixed_cohort_visibility_gap_integral",
    "window_start_visibility_gap_integral",
}
REQUIRED_DOC_PHRASES = (
    "先原子、再阶段、最后可选模式",
    "不使用曲线 ID、国家或事件名称",
    "mixed",
    "unmatched",
    "insufficient_data",
    "缺失不补零、不连线",
    "回升只表示窗口内观测值变化",
    "Prefix×VP 槽",
    "不连续槽不冒充连续时长",
    "TAE-05",
    "TAE-06",
    "TAE-07",
    "不是页面、报告、生产或最终验收证据",
)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须为对象：{path}")
    return value


def load_s1_verifier():
    specification = importlib.util.spec_from_file_location(
        "verify_country_outage_trend_analysis_s1_for_s2",
        S1_VERIFIER_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 S1 校验器：{S1_VERIFIER_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key_point_indices(analysis: dict[str, Any]) -> dict[str, int]:
    return {
        point["kind"]: point["slot_index"]
        for point in analysis["key_points"]
    }


def fact_values(analysis: dict[str, Any]) -> dict[str, int | float | None]:
    return {
        fact["metric"]: fact["value"]
        for fact in analysis["derived_facts"]
    }


def atomic_vocabulary(analysis: dict[str, Any]) -> set[str]:
    return {
        *[item["state"] for item in analysis["atomic_states"]],
        *[
            tag
            for item in analysis["atomic_states"]
            for tag in item["tags"]
        ],
    }


def phase_signature(analysis: dict[str, Any]) -> list[tuple[str, int, int, tuple[str, ...]]]:
    return [
        (
            phase["kind"],
            phase["start_slot_index"],
            phase["end_slot_index"],
            tuple(phase["tags"]),
        )
        for phase in analysis["phases"]
    ]


def validate_schema_shell(schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    analysis = schema.get("properties", {}).get("analysis", {})
    if len(analysis.get("oneOf", [])) != 2:
        errors.append("TrendProfile Schema 必须同时允许 S1 pending 与 S2 result。")
    result = schema.get("$defs", {}).get("analysisResult", {})
    required = set(result.get("required", []))
    expected = {
        "analysis_id",
        "status",
        "algorithm_version",
        "rule",
        "pattern",
        "key_points",
        "atomic_states",
        "phases",
        "derived_facts",
        "window_ledger",
        "evidence_refs",
        "limitations",
    }
    if required != expected or result.get("additionalProperties") is not False:
        errors.append("S2 analysisResult Schema 字段没有完整冻结。")
    return errors


def validate_phase_closure(profile: dict[str, Any]) -> list[str]:
    analysis = profile["analysis"]
    if analysis["status"] != "complete":
        if analysis["phases"]:
            return ["数据不足画像不得跨缺口生成阶段。"]
        return []
    errors: list[str] = []
    atomic_ids = [item["atomic_id"] for item in analysis["atomic_states"]]
    phase_atomic_ids = [
        atomic_id
        for phase in analysis["phases"]
        for atomic_id in phase["atomic_ids"]
    ]
    if phase_atomic_ids != atomic_ids:
        errors.append("阶段没有按顺序且仅一次覆盖全部原子状态。")
    for ordinal, phase in enumerate(analysis["phases"]):
        if phase["ordinal"] != ordinal:
            errors.append("阶段 ordinal 不连续。")
        if phase["start_slot_index"] > phase["end_slot_index"]:
            errors.append("阶段起点晚于终点。")
        if ordinal and analysis["phases"][ordinal - 1]["end_slot_index"] + 1 != phase["start_slot_index"]:
            errors.append("完整曲线的相邻阶段没有连续覆盖。")
    return errors


def validate_ledger_recomputation(profile: dict[str, Any]) -> list[str]:
    analysis = profile["analysis"]
    if analysis["status"] != "complete":
        ledger = analysis["window_ledger"]
        if ledger != {
            "status": "unavailable",
            "reason": "incomplete_slots",
            "facts": [],
            "threshold_slots": [],
        }:
            return ["数据不足画像的窗口账本没有失败关闭。"]
        return []
    errors: list[str] = []
    values = [slot["value"] for slot in profile["slots"]]
    start = values[0]
    extreme = min(values)
    end = values[-1]
    expected = {
        "start_to_extreme_change": extreme - start,
        "loss_magnitude": start - extreme,
        "extreme_to_end_rebound": end - extreme,
        "end_residual_from_start": start - end,
        "window_rebound_ratio": (
            (end - extreme) / (start - extreme)
            if start > extreme
            else None
        ),
    }
    if profile["metric"]["unit"] == "count":
        fixed = profile["metric"]["denominator"]["value"]
        expected["fixed_cohort_visibility_gap_integral"] = sum(
            max(fixed - value, 0) for value in values
        )
        expected["window_start_visibility_gap_integral"] = sum(
            max(start - value, 0) for value in values
        )
    actual = fact_values(analysis)
    if set(actual) != set(expected):
        errors.append("窗口账本事实集合不完整或混入未授权指标。")
    for metric, expected_value in expected.items():
        rounded = round(expected_value, 6) if expected_value is not None else None
        if actual.get(metric) != rounded:
            errors.append(f"窗口账本 {metric} 不可由槽值重算。")
    for fact in analysis["derived_facts"]:
        if not fact["formula"] or len(fact["operands"]) < 2 or not fact["source_refs"]:
            errors.append(f"派生事实 {fact['metric']} 缺少公式、操作数或来源。")
    normalized = [
        value / profile["metric"]["denominator"]["value"]
        for value in values
    ]
    for threshold in analysis["window_ledger"]["threshold_slots"]:
        expected_indices = [
            index
            for index, value in enumerate(normalized)
            if value < threshold["threshold_visible_ratio"]
        ]
        if threshold["slot_indices"] != expected_indices:
            errors.append("阈值槽索引不可由原始槽重算。")
        if threshold["observed_slot_count"] != len(expected_indices):
            errors.append("阈值槽数量与索引不闭合。")
        if threshold["continuous_duration_claimed"] is not False:
            errors.append("阈值槽被错误写成连续时长。")
    return errors


def validate_curve_expectations(
    s0: dict[str, Any],
    s1: dict[str, Any],
    s2: dict[str, Any],
    s1_verifier: Any,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    errors: list[str] = []
    profiles: dict[str, dict[str, Any]] = {}
    cases = s2.get("curve_expectations", [])
    if [case.get("curve_id") for case in cases] != [
        f"CURVE-{index:02d}" for index in range(1, 10)
    ]:
        return ["S2 必须按 CURVE-01 至 CURVE-09 冻结预期。"], profiles
    for case in cases:
        curve_id = case["curve_id"]
        foundation = compile_trend_profile_v1(
            s1_verifier.request_for_curve(s0, s1, curve_id)
        )
        profile = analyze_trend_profile_v1(foundation)
        profiles[curve_id] = profile
        analysis = profile["analysis"]
        if analysis["status"] != case["analysis_status"]:
            errors.append(f"{curve_id} analysis_status 不正确。")
        if analysis["pattern"]["status"] != case["pattern_status"]:
            errors.append(f"{curve_id} pattern_status 不正确。")
        if analysis["pattern"]["label"] != case["pattern_label"]:
            errors.append(f"{curve_id} pattern_label 不正确。")
        if case["key_point_indices"] is None:
            if analysis["key_points"]:
                errors.append(f"{curve_id} 数据不足却生成关键点。")
        elif key_point_indices(analysis) != case["key_point_indices"]:
            errors.append(f"{curve_id} 关键点与冻结预期不一致。")
        if not set(case["required_atomic_states"]).issubset(
            atomic_vocabulary(analysis)
        ):
            errors.append(f"{curve_id} 缺少冻结的原子状态。")
        if case["ledger"] is None:
            if analysis["derived_facts"] or analysis["window_ledger"]["status"] != "unavailable":
                errors.append(f"{curve_id} 数据不足却生成窗口账本。")
        else:
            actual_facts = fact_values(analysis)
            for metric, expected_value in case["ledger"].items():
                if actual_facts.get(metric) != expected_value:
                    errors.append(f"{curve_id} {metric} 与冻结预期不一致。")
            actual_thresholds = {
                str(item["threshold_visible_ratio"]): item["observed_slot_count"]
                for item in analysis["window_ledger"]["threshold_slots"]
            }
            if actual_thresholds != case["threshold_slot_counts"]:
                errors.append(f"{curve_id} 阈值槽分布不正确。")
        errors.extend(f"{curve_id}：{error}" for error in validate_phase_closure(profile))
        errors.extend(f"{curve_id}：{error}" for error in validate_ledger_recomputation(profile))
        if analysis["algorithm_version"] != ANALYSIS_ALGORITHM_VERSION:
            errors.append(f"{curve_id} 分析算法版本不正确。")
        if analysis["rule"] != ANALYSIS_RULE:
            errors.append(f"{curve_id} 分析规则未逐字段冻结。")
        if len(analysis["evidence_refs"]) != len(set(analysis["evidence_refs"])):
            errors.append(f"{curve_id} evidence_refs 存在重复。")
    return errors, profiles


def _shift_iso(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_metamorphic_cases(
    s0: dict[str, Any],
    s1: dict[str, Any],
    s2: dict[str, Any],
    s1_verifier: Any,
    profiles: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    operations = [case.get("operation") for case in s2.get("metamorphic_cases", [])]
    if operations != [
        "repeat",
        "scale_count_and_denominator",
        "shift_window",
        "rename_country_and_event",
        "inject_explicit_missing",
    ]:
        return ["S2 变形测试集合不完整。"]
    request = s1_verifier.request_for_curve(s0, s1, "CURVE-01")
    baseline = profiles["CURVE-01"]

    repeated = analyze_trend_profile_v1(compile_trend_profile_v1(deepcopy(request)))
    if repeated != baseline or analyze_trend_profile_v1(repeated) != repeated:
        errors.append("重复编译或重复分析不是逐字段幂等。")

    scaled_request = deepcopy(request)
    scaled_request["metric"]["denominator"]["value"] *= 10
    for slot in scaled_request["slots"]:
        slot["value"] *= 10
    scaled = analyze_trend_profile_v1(compile_trend_profile_v1(scaled_request))
    if scaled["analysis"]["pattern"] != baseline["analysis"]["pattern"]:
        errors.append("等比例放大数量和分母改变了模式。")
    if key_point_indices(scaled["analysis"]) != key_point_indices(baseline["analysis"]):
        errors.append("等比例放大改变了关键点索引。")
    if phase_signature(scaled["analysis"]) != phase_signature(baseline["analysis"]):
        errors.append("等比例放大改变了阶段结构。")

    shifted_request = deepcopy(request)
    for field in ("window_start_utc", "window_end_utc", "data_through"):
        shifted_request["snapshot"][field] = _shift_iso(
            shifted_request["snapshot"][field], 86400
        )
    for slot in shifted_request["slots"]:
        slot["observed_at_utc"] = _shift_iso(slot["observed_at_utc"], 86400)
    shifted = analyze_trend_profile_v1(compile_trend_profile_v1(shifted_request))
    if shifted["analysis"]["pattern"] != baseline["analysis"]["pattern"]:
        errors.append("整体平移窗口改变了模式。")
    if key_point_indices(shifted["analysis"]) != key_point_indices(baseline["analysis"]):
        errors.append("整体平移窗口改变了关键点索引。")
    if [item[:3] for item in phase_signature(shifted["analysis"])] != [
        item[:3] for item in phase_signature(baseline["analysis"])
    ]:
        errors.append("整体平移窗口改变了阶段类型或边界。")

    renamed_request = deepcopy(request)
    renamed_request["snapshot"].update(
        {
            "event_reference": "country_outage/2000-01-01 00:00:00/XY/1/synthetic",
            "incident_id": "incident_trend_s2_renamed",
            "country_code": "XY",
            "publication_id": "publication_trend_s2_renamed",
        }
    )
    renamed = analyze_trend_profile_v1(compile_trend_profile_v1(renamed_request))
    if renamed["analysis"]["pattern"] != baseline["analysis"]["pattern"]:
        errors.append("重命名国家和事件改变了模式，存在事件捷径风险。")
    if key_point_indices(renamed["analysis"]) != key_point_indices(baseline["analysis"]):
        errors.append("重命名国家和事件改变了关键点。")
    if phase_signature(renamed["analysis"]) != phase_signature(baseline["analysis"]):
        errors.append("重命名国家和事件改变了阶段。")

    missing_request = deepcopy(request)
    missing_index = 4
    missing_request["slots"][missing_index]["state"] = "missing"
    missing_request["slots"][missing_index]["value"] = None
    missing = analyze_trend_profile_v1(compile_trend_profile_v1(missing_request))
    analysis = missing["analysis"]
    if (
        analysis["status"] != "insufficient_data"
        or analysis["key_points"]
        or analysis["phases"]
        or analysis["derived_facts"]
    ):
        errors.append("注入显式缺槽后没有禁止跨缺口输出。")
    return errors


def validate_document() -> list[str]:
    if not DOC_PATH.is_file():
        return [f"S2 验收记录不存在：{DOC_PATH}"]
    text = DOC_PATH.read_text(encoding="utf-8")
    return [
        f"S2 验收记录缺少边界语义：{phrase}"
        for phrase in REQUIRED_DOC_PHRASES
        if phrase not in text
    ]


def validate() -> list[str]:
    errors: list[str] = []
    try:
        s0 = load_json(S0_FIXTURE_PATH)
        s1 = load_json(S1_FIXTURE_PATH)
        s2 = load_json(S2_FIXTURE_PATH)
        schema = load_json(SCHEMA_PATH)
        s1_verifier = load_s1_verifier()
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        return [str(error)]
    if s2.get("schema_version") != "country_outage_trend_analysis_s2_acceptance_v1":
        errors.append("S2 fixture schema_version 不正确。")
    if s2.get("analysis_rule") != ANALYSIS_RULE:
        errors.append("S2 fixture 与代码分析规则不一致。")
    errors.extend(validate_schema_shell(schema))
    curve_errors, profiles = validate_curve_expectations(
        s0, s1, s2, s1_verifier
    )
    errors.extend(curve_errors)
    errors.extend(
        validate_metamorphic_cases(s0, s1, s2, s1_verifier, profiles)
    )
    errors.extend(validate_document())
    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("国家中断趋势分析 S2 候选验证：失败", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("国家中断趋势分析 S2 候选验证：通过")
    print(f"- schema_sha256={sha256(SCHEMA_PATH)}")
    print(f"- fixture_sha256={sha256(S2_FIXTURE_PATH)}")
    print("- 9 条冻结曲线、5 类变形测试、关键点/阶段/账本重算通过")
    print("- 只证明本地确定性分析层，不证明 API、页面、报告、追问或生产效果")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
