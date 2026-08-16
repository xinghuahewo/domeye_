#!/usr/bin/env python3
"""验证 S3 地址族、ASN 迁移/持续性/规模与活动时间对应。"""

from __future__ import annotations

from copy import deepcopy
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
    ASN_STATES,
    TREND_CONTEXT_ALGORITHM_VERSION,
    TREND_CONTEXT_SCHEMA_VERSION,
    TrendProfileValidationError,
    align_activity_context_v1,
    analyze_trend_profile_v1,
    compare_address_families_v1,
    compile_asn_state_context_v1,
    compile_trend_profile_v1,
)


S0_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s0-v1.json"
S1_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s1-v1.json"
S3_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s3-v1.json"
SCHEMA_PATH = REPOSITORY_ROOT / "contracts" / "agent" / "country-outage-trend-context-v1.schema.json"
DOC_PATH = REPOSITORY_ROOT / "docs" / "国家中断趋势分析S3验收记录.md"
S1_VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s1.py"

REQUIRED_DOC_PHRASES = (
    "IPv4 与 IPv6",
    "同时显示比率与分母",
    "小分母不等于同等绝对规模",
    "fully_visible",
    "partially_visible",
    "fully_invisible",
    "unknown",
    "unknown 独立参与人口闭合",
    "观测规模",
    "持续性",
    "不生成单一影响分数",
    "同槽、相邻槽和滞后槽",
    "只表示时间关系",
    "TAE-08",
    "TAE-09",
    "TAE-10",
    "不是 API、页面、报告、生产或最终验收证据",
)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须为对象：{path}")
    return value


def load_s1_verifier():
    specification = importlib.util.spec_from_file_location(
        "verify_country_outage_trend_analysis_s1_for_s3",
        S1_VERIFIER_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 S1 校验器：{S1_VERIFIER_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyzed_curve(
    s0: dict[str, Any],
    s1: dict[str, Any],
    s1_verifier: Any,
    curve_id: str,
    *,
    scale: int = 1,
    population: str = "fixed_prefix_vp",
    snapshot_mutation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request = s1_verifier.request_for_curve(s0, s1, curve_id)
    request["metric"]["statistical_population"] = population
    request["metric"]["denominator"]["statistical_population"] = population
    request["metric"]["denominator"]["value"] *= scale
    for slot in request["slots"]:
        if slot["value"] is not None:
            slot["value"] *= scale
    if snapshot_mutation:
        request["snapshot"].update(snapshot_mutation)
    return analyze_trend_profile_v1(compile_trend_profile_v1(request))


def build_address_family_profiles(
    s0: dict[str, Any],
    s1: dict[str, Any],
    s3: dict[str, Any],
    s1_verifier: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    profiles = []
    for family in ("ipv4", "ipv6"):
        case = s3["address_family_case"][family]
        profiles.append(
            analyzed_curve(
                s0,
                s1,
                s1_verifier,
                case["curve_id"],
                scale=case["scale"],
                population=case["statistical_population"],
            )
        )
    return profiles[0], profiles[1]


def build_asn_rows(s3: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in s3["asn_case"]["rows"]:
        rows.append(
            {
                "asn": row["asn"],
                "address_family": row["address_family"],
                "baseline_prefix_vp_count": row["baseline_prefix_vp_count"],
                "slots": [
                    {
                        "index": index,
                        "state": state,
                        "source_ref": (
                            "dev/fixtures/country-outage-trend-analysis-s3-v1.json#"
                            f"/asn/{row['asn']}/{row['address_family']}/{index}"
                        ),
                    }
                    for index, state in enumerate(row["states"])
                ],
            }
        )
    return rows


def build_activity_tracks(s3: dict[str, Any]) -> list[dict[str, Any]]:
    tracks = []
    for track in s3["activity_case"]["tracks"]:
        tracks.append(
            {
                "track_id": track["track_id"],
                "metric_id": track["metric_id"],
                "unit": "count",
                "statistical_population": track["statistical_population"],
                "slots": [
                    {
                        "index": index,
                        "state": "observed",
                        "value": value,
                        "source_ref": (
                            "dev/fixtures/country-outage-trend-analysis-s3-v1.json#"
                            f"/activity/{track['track_id']}/{index}"
                        ),
                    }
                    for index, value in enumerate(track["values"])
                ],
            }
        )
    return tracks


def validate_schema_shell(schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("S3 Context Schema 必须使用 JSON Schema 2020-12。")
    refs = [item.get("$ref") for item in schema.get("oneOf", [])]
    if refs != [
        "#/$defs/addressFamilyContext",
        "#/$defs/asnStateContext",
        "#/$defs/activityAlignmentContext",
    ]:
        errors.append("S3 Context Schema 必须且只能冻结三类上下文。")
    priority = schema.get("$defs", {}).get("asnStateContext", {}).get("properties", {}).get("priority_views", {})
    score_type = (
        priority.get("properties", {})
        .get("single_impact_score", {})
        .get("type")
    )
    if score_type != "null":
        errors.append("S3 Schema 必须禁止单一影响分数。")
    return errors


def validate_address_family_context(
    context: dict[str, Any],
    expected: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if context.get("schema_version") != TREND_CONTEXT_SCHEMA_VERSION:
        errors.append("地址族上下文 schema_version 不正确。")
    if context.get("context_type") != "address_family_comparison":
        errors.append("地址族上下文类型不正确。")
    families = context["families"]
    if families["ipv4"]["denominator"]["value"] != expected["ipv4_denominator"]:
        errors.append("IPv4 分母不正确。")
    if families["ipv6"]["denominator"]["value"] != expected["ipv6_denominator"]:
        errors.append("IPv6 分母不正确。")
    comparison = context["comparison"]
    if comparison["denominator_ratio"] != expected["denominator_ratio"]:
        errors.append("地址族分母比例不正确。")
    if expected["warning"] not in comparison["warnings"]:
        errors.append("地址族小分母差异没有显式警告。")
    maximum = comparison["maximum_divergence"]
    if maximum["slot_index"] != expected["maximum_divergence_slot_index"]:
        errors.append("地址族最大分化槽不正确。")
    if maximum["ipv4_minus_ipv6_percentage_points"] != expected["maximum_divergence_percentage_points"]:
        errors.append("地址族最大分化百分点不正确。")
    if comparison["extreme_alignment"]["relation"] != expected["extreme_relation"]:
        errors.append("地址族谷值时间关系不正确。")
    if families["ipv4"]["pattern"]["label"] != expected["ipv4_pattern"]:
        errors.append("IPv4 模式不正确。")
    if families["ipv6"]["pattern"]["label"] != expected["ipv6_pattern"]:
        errors.append("IPv6 模式不正确。")
    for slot in comparison["divergence_slots"]:
        if slot["ipv4_denominator"] != expected["ipv4_denominator"] or slot["ipv6_denominator"] != expected["ipv6_denominator"]:
            errors.append("地址族比率槽没有同时携带两个分母。")
            break
    return errors


def validate_asn_context(
    context: dict[str, Any],
    expected: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if context.get("context_type") != "asn_state_context":
        errors.append("ASN 上下文类型不正确。")
    if context["asn_states"] != list(ASN_STATES):
        errors.append("ASN 四状态顺序或集合不正确。")
    if context["asn_count"] != expected["asn_count"]:
        errors.append("ASN 人口不正确。")
    if context["baseline_prefix_vp_count"] != expected["baseline_prefix_vp_count"]:
        errors.append("ASN 基线 Prefix×VP 规模不闭合。")
    for slot in context["slot_population"]:
        counts = slot["state_counts"]
        if set(counts) != set(ASN_STATES) or sum(counts.values()) != context["asn_count"]:
            errors.append(f"ASN 槽 {slot['slot_index']} 四状态人口不闭合。")
        if counts["unknown"] != expected["unknown_count_each_slot"]:
            errors.append(f"ASN 槽 {slot['slot_index']} unknown 人口丢失。")
    if context["slot_population"][-1]["state_counts"] != expected["end_state_counts"]:
        errors.append("ASN 终点四状态分布不正确。")
    persistent = [item["asn"] for item in context["asns"] if item["persistent_not_at_start"]]
    if persistent != expected["persistent_not_at_start_asns"]:
        errors.append("持续未回到起点状态的 ASN 集合不正确。")
    unchanged = [item["asn"] for item in context["asns"] if not item["persistent_not_at_start"]]
    if unchanged != expected["end_equals_start_asns"]:
        errors.append("终点等于起点状态的 ASN 集合不正确。")
    for item in context["asns"]:
        if sum(item["state_slot_counts"].values()) != context["time_grid"]["expected_slot_count"]:
            errors.append(f"AS{item['asn']} 聚合状态槽数不闭合。")
        for family in item["address_families"].values():
            if sum(family["state_slot_counts"].values()) != context["time_grid"]["expected_slot_count"]:
                errors.append(f"AS{item['asn']} 地址族状态槽数不闭合。")
    for matrix in context["transition_matrices"]:
        if len(matrix["cells"]) != 16 or sum(cell["asn_count"] for cell in matrix["cells"]) != context["asn_count"]:
            errors.append("ASN 迁移矩阵没有 16 格人口闭合。")
    views = context["priority_views"]
    if [item["asn"] for item in views["by_observation_scale"]] != expected["scale_order"]:
        errors.append("ASN 观测规模视图排序不正确。")
    if [item["asn"] for item in views["by_persistence"]] != expected["persistence_order"]:
        errors.append("ASN 持续性视图排序不正确。")
    if views["single_impact_score"] is not None:
        errors.append("ASN 上下文生成了单一影响分数。")
    return errors


def validate_activity_context(
    context: dict[str, Any],
    expected: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if context.get("context_type") != "activity_alignment":
        errors.append("活动上下文类型不正确。")
    if len(context["tracks"]) != expected["track_count"]:
        errors.append("活动轨道数量不正确。")
    if len(context["aligned_slots"]) != expected["aligned_slot_count"]:
        errors.append("活动对齐槽数量不正确。")
    populations = [track["statistical_population"] for track in context["tracks"]]
    if len(populations) != len(set(populations)):
        errors.append("活动轨道统计人口被合并。")
    for slot in context["aligned_slots"]:
        if len(slot["activities"]) != expected["track_count"]:
            errors.append("活动对齐槽没有保留全部轨道。")
    relations = {item["relation"] for item in context["temporal_relations"]}
    if not set(expected["relations_present"]).issubset(relations):
        errors.append("活动与状态没有覆盖同槽、相邻槽和滞后槽关系。")
    if any(item["causal_interpretation"] is not None for item in context["temporal_relations"]):
        errors.append("时间关系混入因果解释。")
    for field in (
        "cross_population_arithmetic_performed",
        "common_impact_score",
        "causal_claim",
    ):
        if context[field] != expected[field]:
            errors.append(f"活动上下文越过边界：{field}")
    return errors


def validate_failure_cases(
    s0: dict[str, Any],
    s1: dict[str, Any],
    s3: dict[str, Any],
    s1_verifier: Any,
    base_profile: dict[str, Any],
    ipv4: dict[str, Any],
    ipv6: dict[str, Any],
    asn_rows: list[dict[str, Any]],
    tracks: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    for case in s3["failure_cases"]:
        mutation = case["mutation"]
        try:
            if mutation == "ipv6_revision":
                changed = analyzed_curve(
                    s0,
                    s1,
                    s1_verifier,
                    s3["address_family_case"]["ipv6"]["curve_id"],
                    population="ipv6_fixed_prefix_vp",
                    snapshot_mutation={"revision": 2},
                )
                compare_address_families_v1(ipv4, changed)
            elif mutation == "ipv6_population":
                changed = analyzed_curve(
                    s0,
                    s1,
                    s1_verifier,
                    s3["address_family_case"]["ipv6"]["curve_id"],
                    population="fixed_prefix_vp",
                )
                compare_address_families_v1(ipv4, changed)
            elif mutation == "duplicate_asn_family":
                compile_asn_state_context_v1(base_profile, [*deepcopy(asn_rows), deepcopy(asn_rows[0])])
            elif mutation == "remove_asn_slot":
                changed = deepcopy(asn_rows)
                changed[0]["slots"].pop()
                compile_asn_state_context_v1(base_profile, changed)
            elif mutation == "invalid_asn_state":
                changed = deepcopy(asn_rows)
                changed[0]["slots"][0]["state"] = "visible"
                compile_asn_state_context_v1(base_profile, changed)
            elif mutation == "duplicate_track":
                align_activity_context_v1(base_profile, [*deepcopy(tracks), deepcopy(tracks[0])])
            elif mutation == "activity_population":
                changed = deepcopy(tracks)
                changed[0]["statistical_population"] = "prefix_vp"
                align_activity_context_v1(base_profile, changed)
            elif mutation == "non_observed_activity_value":
                changed = deepcopy(tracks)
                changed[0]["slots"][0]["state"] = "missing"
                align_activity_context_v1(base_profile, changed)
            else:
                errors.append(f"未知 S3 failure mutation：{mutation}")
                continue
        except TrendProfileValidationError as error:
            if error.code != case["expected_code"]:
                errors.append(f"{case['id']} 错误码 {error.code} != {case['expected_code']}")
        else:
            errors.append(f"{case['id']} 没有失败关闭。")
    return errors


def validate_document() -> list[str]:
    if not DOC_PATH.is_file():
        return [f"S3 验收记录不存在：{DOC_PATH}"]
    text = DOC_PATH.read_text(encoding="utf-8")
    return [
        f"S3 验收记录缺少边界语义：{phrase}"
        for phrase in REQUIRED_DOC_PHRASES
        if phrase not in text
    ]


def validate() -> list[str]:
    errors: list[str] = []
    try:
        s0 = load_json(S0_FIXTURE_PATH)
        s1 = load_json(S1_FIXTURE_PATH)
        s3 = load_json(S3_FIXTURE_PATH)
        schema = load_json(SCHEMA_PATH)
        s1_verifier = load_s1_verifier()
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        return [str(error)]
    errors.extend(validate_schema_shell(schema))
    ipv4, ipv6 = build_address_family_profiles(s0, s1, s3, s1_verifier)
    family_context = compare_address_families_v1(ipv4, ipv6)
    errors.extend(
        validate_address_family_context(
            family_context,
            s3["address_family_case"]["expected"],
        )
    )
    base_profile = analyzed_curve(
        s0,
        s1,
        s1_verifier,
        s3["asn_case"]["profile_curve_id"],
    )
    asn_rows = build_asn_rows(s3)
    asn_context = compile_asn_state_context_v1(base_profile, asn_rows)
    errors.extend(validate_asn_context(asn_context, s3["asn_case"]["expected"]))
    tracks = build_activity_tracks(s3)
    activity_context = align_activity_context_v1(base_profile, tracks)
    errors.extend(
        validate_activity_context(
            activity_context,
            s3["activity_case"]["expected"],
        )
    )
    for context in (family_context, asn_context, activity_context):
        if context.get("algorithm_version") != TREND_CONTEXT_ALGORITHM_VERSION:
            errors.append("S3 上下文算法版本不正确。")
        if not context["context_id"].startswith("trend_context_v1_"):
            errors.append("S3 context_id 格式不正确。")
    if compare_address_families_v1(ipv4, ipv6) != family_context:
        errors.append("地址族上下文重复生成不确定。")
    if compile_asn_state_context_v1(base_profile, deepcopy(asn_rows)) != asn_context:
        errors.append("ASN 上下文重复生成不确定。")
    if align_activity_context_v1(base_profile, deepcopy(tracks)) != activity_context:
        errors.append("活动上下文重复生成不确定。")
    errors.extend(
        validate_failure_cases(
            s0,
            s1,
            s3,
            s1_verifier,
            base_profile,
            ipv4,
            ipv6,
            asn_rows,
            tracks,
        )
    )
    errors.extend(validate_document())
    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("国家中断趋势分析 S3 候选验证：失败", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("国家中断趋势分析 S3 候选验证：通过")
    print(f"- schema_sha256={sha256(SCHEMA_PATH)}")
    print(f"- fixture_sha256={sha256(S3_FIXTURE_PATH)}")
    print("- 地址族双分母、ASN 四状态人口闭合、四活动轨道时间对应通过")
    print("- 8 类身份/人口/状态失败关闭场景通过")
    print("- 只证明本地确定性上下文层，不证明 API、页面、报告、追问或生产效果")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
