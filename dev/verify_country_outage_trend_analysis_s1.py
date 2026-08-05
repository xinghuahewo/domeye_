#!/usr/bin/env python3
"""验证 S1 TrendProfile 身份、质量、基线与可比性候选。"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.country_outage_trend_profile import (  # noqa: E402
    ALGORITHM_VERSION,
    ALLOWED_SLOT_STATES,
    PROFILE_SCHEMA_VERSION,
    TrendProfileValidationError,
    compile_trend_profile_v1,
    profile_compatibility_v1,
)


S0_FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "dev"
    / "fixtures"
    / "country-outage-trend-analysis-s0-v1.json"
)
S1_FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "dev"
    / "fixtures"
    / "country-outage-trend-analysis-s1-v1.json"
)
SCHEMA_PATH = (
    REPOSITORY_ROOT
    / "contracts"
    / "agent"
    / "country-outage-trend-profile-v1.schema.json"
)
DOC_PATH = REPOSITORY_ROOT / "docs" / "国家中断趋势分析S1验收记录.md"

EXPECTED_TOP_LEVEL_FIELDS = {
    "schema_version",
    "profile_id",
    "input_digest",
    "algorithm_version",
    "profile_state",
    "snapshot",
    "metric",
    "time_grid",
    "quality",
    "baseline",
    "slots",
    "analysis",
}
EXPECTED_CURVES = tuple(f"CURVE-{index:02d}" for index in range(1, 10))
REQUIRED_DOC_PHRASES = (
    "只闭合 TrendProfile 的身份、质量、基线和可比性",
    "从始至终只使用 RRC25",
    "不是关键点、阶段、页面、报告或生产证据",
    "TAE-01",
    "TAE-02",
    "TAE-03",
    "TAE-04",
    "缺失不补零、不连线",
    "窗口起点不是正常基线",
    "fixed_cohort",
    "window_start",
    "contemporaneous_reference",
    "unavailable",
    "not_computed_in_s1",
)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须为对象：{path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def curve_by_id(s0: dict[str, Any], curve_id: str) -> dict[str, Any]:
    for curve in s0["acceptance_curves"]:
        if curve["id"] == curve_id:
            return curve
    raise KeyError(curve_id)


def baseline_input(
    baseline_type: str,
    *,
    s1: dict[str, Any],
    override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    baseline: dict[str, Any] = {"type": baseline_type}
    if baseline_type == "contemporaneous_reference":
        configured = override or {}
        baseline.update(
            {
                "value": configured.get("value", 97),
                "unit": configured.get("unit", "count"),
                "statistical_population": configured.get(
                    "statistical_population", "fixed_prefix_vp"
                ),
                "reference_id": configured.get(
                    "reference_id", "reference_s1_same_grid"
                ),
                "reference_time_grid": {
                    "slot_seconds": 300,
                    "expected_slot_count": 12,
                    "window_start_utc": s1["snapshot_template"][
                        "window_start_utc"
                    ],
                    "window_end_utc": s1["snapshot_template"]["window_end_utc"],
                },
            }
        )
    return baseline


def request_for_curve(
    s0: dict[str, Any],
    s1: dict[str, Any],
    curve_id: str,
    *,
    baseline_type: str | None = None,
    baseline_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    curve = curve_by_id(s0, curve_id)
    configured_type = baseline_type or curve["baseline_type"]
    start = datetime.fromisoformat(
        s1["snapshot_template"]["window_start_utc"].replace("Z", "+00:00")
    )
    metric = deepcopy(s1["metric_template"])
    metric["denominator"] = {
        "value": curve["denominator"],
        "unit": "count",
        "statistical_population": metric["statistical_population"],
    }
    slots = []
    for slot in curve["slots"]:
        observed_at = start + timedelta(seconds=slot["offset_seconds"])
        slots.append(
            {
                "index": slot["index"],
                "observed_at_utc": observed_at.astimezone(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "state": slot["state"],
                "value": slot["value"],
                "source_ref": (
                    "dev/fixtures/country-outage-trend-analysis-s0-v1.json#"
                    f"/acceptance_curves/{int(curve_id[-2:]) - 1}/slots/"
                    f"{slot['index']}"
                ),
            }
        )
    return {
        "schema_version": "country_outage_trend_profile_input_v1",
        "snapshot": deepcopy(s1["snapshot_template"]),
        "metric": metric,
        "time_grid": {"slot_seconds": 300, "expected_slot_count": 12},
        "baseline": baseline_input(
            configured_type,
            s1=s1,
            override=baseline_override,
        ),
        "slots": slots,
    }


def validate_schema_shell(schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("TrendProfile Schema 必须使用 JSON Schema 2020-12。")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        errors.append("TrendProfile Schema 顶层必须禁止额外字段。")
    properties = schema.get("properties", {})
    if set(properties) != EXPECTED_TOP_LEVEL_FIELDS:
        errors.append("TrendProfile Schema 顶层字段与 S1 冻结输出不一致。")
    if properties.get("schema_version", {}).get("const") != PROFILE_SCHEMA_VERSION:
        errors.append("TrendProfile Schema 没有冻结正确 schema_version。")
    slot_states = set(
        schema.get("$defs", {})
        .get("slot", {})
        .get("properties", {})
        .get("state", {})
        .get("enum", [])
    )
    if slot_states != set(ALLOWED_SLOT_STATES):
        errors.append("TrendProfile Schema 没有保持全部槽状态独立。")
    return errors


def validate_profile_shape(profile: dict[str, Any], curve_id: str) -> list[str]:
    errors: list[str] = []
    if set(profile) != EXPECTED_TOP_LEVEL_FIELDS:
        errors.append(f"{curve_id} TrendProfile 顶层字段漂移。")
    if profile.get("schema_version") != PROFILE_SCHEMA_VERSION:
        errors.append(f"{curve_id} schema_version 不正确。")
    if profile.get("algorithm_version") != ALGORITHM_VERSION:
        errors.append(f"{curve_id} algorithm_version 不正确。")
    profile_id = profile.get("profile_id", "")
    digest = profile.get("input_digest", "")
    if profile_id != f"trend_profile_v1_{digest[:32]}":
        errors.append(f"{curve_id} profile_id 不能由 input_digest 重算。")
    if profile.get("snapshot", {}).get("collector_id") != "rrc25":
        errors.append(f"{curve_id} collector 不是 rrc25。")
    if profile.get("snapshot", {}).get("collector_count") != 1:
        errors.append(f"{curve_id} collector_count 不是 1。")
    if profile.get("analysis") != {
        "status": "not_computed_in_s1",
        "key_points": [],
        "atomic_states": [],
        "phases": [],
        "derived_facts": [],
        "evidence_refs": [],
    }:
        errors.append(f"{curve_id} S1 越界计算了关键点或阶段。")
    baseline = profile.get("baseline", {})
    if baseline.get("interpretation") != "observation_reference_not_normal_baseline":
        errors.append(f"{curve_id} 基线缺少非正常带声明。")
    return errors


def validate_curve_cases(
    s0: dict[str, Any],
    s1: dict[str, Any],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    errors: list[str] = []
    profiles: dict[str, dict[str, Any]] = {}
    cases = s1.get("curve_cases", [])
    if [case.get("curve_id") for case in cases] != list(EXPECTED_CURVES):
        return ["S1 curve_cases 必须按九条 S0 曲线完整冻结。"], profiles
    for case in cases:
        curve_id = case["curve_id"]
        request = request_for_curve(
            s0,
            s1,
            curve_id,
            baseline_type=case["baseline_type"],
        )
        try:
            profile = compile_trend_profile_v1(request)
        except TrendProfileValidationError as error:
            errors.append(f"{curve_id} 无法编译：{error.code}/{error.field}：{error}")
            continue
        profiles[curve_id] = profile
        errors.extend(validate_profile_shape(profile, curve_id))
        quality = profile["quality"]
        if quality["status"] != case["expected_quality"]:
            errors.append(f"{curve_id} 质量状态错误：{quality['status']}")
        if profile["baseline"]["type"] != case["expected_effective_baseline"]:
            errors.append(f"{curve_id} 有效基线类型错误。")
        counts = Counter(slot["state"] for slot in profile["slots"])
        for state in ALLOWED_SLOT_STATES:
            if quality["slot_state_counts"][state] != counts.get(state, 0):
                errors.append(f"{curve_id} 槽状态 {state} 计数不闭合。")
        if quality["observed_slot_count"] + quality["non_observed_slot_count"] != 12:
            errors.append(f"{curve_id} 槽人口不闭合。")
        for slot in profile["slots"]:
            if slot["state"] != "observed" and slot["value"] is not None:
                errors.append(f"{curve_id} 非 observed 槽被补值。")
    return errors, profiles


def validate_baseline_cases(s0: dict[str, Any], s1: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    cases = s1.get("baseline_cases", [])
    if [case.get("type") for case in cases] != [
        "fixed_cohort",
        "window_start",
        "contemporaneous_reference",
        "unavailable",
    ]:
        return ["S1 必须且只能冻结四类基线场景。"]
    for case in cases:
        request = request_for_curve(
            s0,
            s1,
            "CURVE-01",
            baseline_type=case["type"],
            baseline_override=case,
        )
        try:
            profile = compile_trend_profile_v1(request)
        except TrendProfileValidationError as error:
            errors.append(f"{case['id']} 无法编译：{error.code}：{error}")
            continue
        baseline = profile["baseline"]
        if baseline["type"] != case["type"]:
            errors.append(f"{case['id']} 基线类型未保持。")
        if baseline["source"] != case["expected_value_source"]:
            errors.append(f"{case['id']} 基线来源不正确。")
        if baseline["interpretation"] != "observation_reference_not_normal_baseline":
            errors.append(f"{case['id']} 缺少非正常基线声明。")
    return errors


def validate_identity_and_compatibility(
    s0: dict[str, Any],
    s1: dict[str, Any],
    profiles: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    request = request_for_curve(s0, s1, "CURVE-01")
    first = compile_trend_profile_v1(request)
    second = compile_trend_profile_v1(deepcopy(request))
    if first != second:
        errors.append("相同冻结输入重复编译没有得到逐字段相同 TrendProfile。")
    revised = deepcopy(request)
    revised["snapshot"]["revision"] = 2
    revised["snapshot"]["publication_id"] = "publication_trend_s1_revision_2"
    revised_profile = compile_trend_profile_v1(revised)
    if revised_profile["profile_id"] == first["profile_id"]:
        errors.append("新 revision 没有生成可区分的 profile_id。")
    changed_slot = deepcopy(request)
    changed_slot["slots"][1]["value"] = 99
    changed_profile = compile_trend_profile_v1(changed_slot)
    if changed_profile["profile_id"] == first["profile_id"]:
        errors.append("槽数据变化没有生成可区分的 profile_id。")
    if not profile_compatibility_v1(first, second)["compatible"]:
        errors.append("同一 TrendProfile 被错误判为不可直接比较。")
    incompatible = profile_compatibility_v1(first, revised_profile)
    if incompatible["compatible"] or not {
        "snapshot.publication_id",
        "snapshot.revision",
    }.issubset(incompatible["mismatches"]):
        errors.append("publication/revision 冲突没有失败关闭。")
    small = profiles.get("CURVE-07")
    if small is not None:
        denominator_conflict = profile_compatibility_v1(first, small)
        if denominator_conflict["compatible"] or "metric.denominator" not in denominator_conflict["mismatches"]:
            errors.append("不同分母被错误允许直接计算。")
    return errors


def validate_failure_cases(s0: dict[str, Any], s1: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for case in s1.get("failure_cases", []):
        mutation = case["mutation"]
        if mutation == "reference_grid":
            request = request_for_curve(
                s0,
                s1,
                "CURVE-01",
                baseline_type="contemporaneous_reference",
            )
            request["baseline"]["reference_time_grid"]["slot_seconds"] = case[
                "value"
            ]
        elif mutation == "missing_value":
            request = request_for_curve(s0, s1, "CURVE-08")
            request["slots"][2]["value"] = case["value"]
        else:
            request = request_for_curve(s0, s1, "CURVE-01")
            if mutation == "collector_id":
                request["snapshot"]["collector_id"] = case["value"]
            elif mutation == "collector_count":
                request["snapshot"]["collector_count"] = case["value"]
            elif mutation == "event_country":
                request["snapshot"]["country_code"] = case["value"]
            elif mutation == "window_end":
                request["snapshot"]["window_end_utc"] = case["value"]
                request["snapshot"]["data_through"] = case["value"]
            elif mutation == "slot_time":
                request["slots"][1]["observed_at_utc"] = case["value"]
            elif mutation == "denominator_population":
                request["metric"]["denominator"]["statistical_population"] = case[
                    "value"
                ]
            else:
                errors.append(f"未知 failure mutation：{mutation}")
                continue
        try:
            compile_trend_profile_v1(request)
        except TrendProfileValidationError as error:
            if error.code != case["expected_code"]:
                errors.append(
                    f"{case['id']} 错误码 {error.code} != {case['expected_code']}"
                )
        else:
            errors.append(f"{case['id']} 没有失败关闭。")
    return errors


def validate_document() -> list[str]:
    if not DOC_PATH.is_file():
        return [f"S1 验收记录不存在：{DOC_PATH}"]
    text = DOC_PATH.read_text(encoding="utf-8")
    return [
        f"S1 验收记录缺少边界语义：{phrase}"
        for phrase in REQUIRED_DOC_PHRASES
        if phrase not in text
    ]


def validate() -> list[str]:
    errors: list[str] = []
    try:
        s0 = load_json(S0_FIXTURE_PATH)
        s1 = load_json(S1_FIXTURE_PATH)
        schema = load_json(SCHEMA_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [str(error)]
    if s1.get("schema_version") != "country_outage_trend_analysis_s1_acceptance_v1":
        errors.append("S1 验收 fixture schema_version 不正确。")
    if s1.get("status") != "candidate_foundation_not_end_to_end_evidence":
        errors.append("S1 fixture 没有保持非端到端证据边界。")
    if s1.get("snapshot_template", {}).get("collector_id") != "rrc25":
        errors.append("S1 fixture 不是 RRC25-only。")
    errors.extend(validate_schema_shell(schema))
    curve_errors, profiles = validate_curve_cases(s0, s1)
    errors.extend(curve_errors)
    errors.extend(validate_baseline_cases(s0, s1))
    errors.extend(validate_identity_and_compatibility(s0, s1, profiles))
    errors.extend(validate_failure_cases(s0, s1))
    errors.extend(validate_document())
    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("国家中断趋势分析 S1 候选验证：失败", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("国家中断趋势分析 S1 候选验证：通过")
    print(f"- schema_sha256={sha256(SCHEMA_PATH)}")
    print(f"- fixture_sha256={sha256(S1_FIXTURE_PATH)}")
    print("- 9 条冻结曲线、4 类基线、8 类失败关闭场景通过")
    print("- 只证明身份、质量、基线与可比性；分析状态仍为 not_computed_in_s1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
