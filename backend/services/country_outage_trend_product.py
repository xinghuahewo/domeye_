"""国家中断趋势 Evidence Graph v1 与有界问答编译器。

本模块只消费已经确定性编译的 TrendProfile 与 S3 Context。它不读取数据库、
网络或模型，也不生成假设、原因、影响、责任或窗口外结论。
"""

from __future__ import annotations

from copy import deepcopy
from collections import Counter
import hashlib
import json
from typing import Any, Mapping, Sequence

from services.country_outage_trend_profile import analyze_trend_profile_v1
from services.country_outage_trend_profile import (
    align_activity_context_v1,
    compare_address_families_v1,
    compile_trend_profile_v1,
)


EVIDENCE_GRAPH_SCHEMA_VERSION = "country_outage_evidence_graph_v1"
TREND_PRODUCT_SCHEMA_VERSION = "country_outage_trend_product_v1"
TREND_PRODUCT_ALGORITHM_VERSION = "country_outage_trend_product_s4_v1"
QA_RULE_VERSION = "country_outage_trend_qa_s4_v1"
NODE_TYPES = ("Claim", "Evidence", "Limitation", "Unknown")
RELATION_TYPES = ("supported_by", "limited_by", "unknown_about")
ASN_CODE_TO_STATE = {
    -1: "unknown",
    0: "fully_visible",
    1: "partially_visible",
    2: "fully_invisible",
}


class TrendProductValidationError(ValueError):
    """趋势制品或上下文身份不满足确定性事实合同。"""

    def __init__(self, code: str, field: str, message: str):
        super().__init__(message)
        self.code = code
        self.field = field


def _fail(code: str, field: str, message: str) -> None:
    raise TrendProductValidationError(code, field, message)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _same_identity(profile: Mapping[str, Any], context: Mapping[str, Any], field: str) -> None:
    for key in (
        "incident_id",
        "publication_id",
        "revision",
        "data_through",
        "collector_id",
        "collector_count",
        "window_start_utc",
        "window_end_utc",
    ):
        if context.get("snapshot", {}).get(key) != profile["snapshot"].get(key):
            _fail(
                "context_identity_conflict",
                f"{field}.snapshot.{key}",
                f"{field} 与 TrendProfile 快照身份不一致",
            )
    if context.get("time_grid") != profile.get("time_grid"):
        _fail(
            "context_time_grid_conflict",
            f"{field}.time_grid",
            f"{field} 与 TrendProfile 时间网格不一致",
        )
    if context.get("profile_id") not in {None, profile.get("profile_id")}:
        _fail(
            "context_profile_conflict",
            f"{field}.profile_id",
            f"{field} 与 TrendProfile ID 不一致",
        )
    if context.get("analysis_id") not in {
        None,
        profile.get("analysis", {}).get("analysis_id"),
    }:
        _fail(
            "context_analysis_conflict",
            f"{field}.analysis_id",
            f"{field} 与趋势分析 ID 不一致",
        )


def _evidence_node(
    *,
    node_id: str,
    evidence_kind: str,
    label: str,
    payload: Mapping[str, Any],
    source_refs: Sequence[str],
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": "Evidence",
        "evidence_kind": evidence_kind,
        "label": label,
        "snapshot_ref": {
            "incident_id": snapshot["incident_id"],
            "publication_id": snapshot["publication_id"],
            "revision": snapshot["revision"],
            "data_through": snapshot["data_through"],
        },
        "payload": deepcopy(dict(payload)),
        "source_refs": list(dict.fromkeys(source_refs)),
    }


def _limitation_node(graph_seed: str, code: str, text: str) -> dict[str, Any]:
    return {
        "node_id": f"{graph_seed}:limitation:{code}",
        "node_type": "Limitation",
        "code": code,
        "text": text,
    }


def _unknown_node(graph_seed: str, code: str, text: str) -> dict[str, Any]:
    return {
        "node_id": f"{graph_seed}:unknown:{code}",
        "node_type": "Unknown",
        "code": code,
        "text": text,
    }


def _format_number(value: Any) -> str:
    if value is None:
        return "未知"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def _slot_state(point: Mapping[str, Any]) -> str:
    state = str(point.get("slot_state") or "observed")
    if state not in {
        "observed",
        "missing",
        "unknown",
        "source_unavailable",
        "processing_gap",
        "parse_failed",
        "not_observed",
    }:
        return "unknown"
    return state


def _profile_request_from_resources(
    overview: Mapping[str, Any],
    series: Mapping[str, Any],
    *,
    metric_id: str,
    label: str,
    statistical_population: str,
    denominator: int,
    value_field: str,
) -> dict[str, Any]:
    identity = overview.get("event_identity") or {}
    scope = overview.get("observation_scope") or {}
    points = series.get("series") or []
    if not isinstance(points, list) or not points:
        _fail("series_unavailable", "series.series", "趋势时序不可用")
    legacy_reference = identity.get("legacy_reference")
    if not isinstance(legacy_reference, str) or not legacy_reference:
        _fail(
            "event_reference_unavailable",
            "overview.event_identity.legacy_reference",
            "事件缺少可审计的五段式引用",
        )
    interval_seconds = series.get("interval_seconds") or scope.get("interval_seconds")
    if not isinstance(interval_seconds, int) or interval_seconds <= 0:
        _fail("interval_unavailable", "series.interval_seconds", "趋势时间槽间隔不可用")
    snapshot = {
        "event_type": "country_outage",
        "event_reference": legacy_reference,
        "incident_id": overview.get("incident_id") or identity.get("incident_id"),
        "country_code": identity.get("country_code"),
        "collector_id": scope.get("collector_id"),
        "collector_count": scope.get("collector_count"),
        "publication_id": overview.get("publication_id"),
        "revision": overview.get("revision"),
        "data_through": overview.get("data_through"),
        "is_final": overview.get("is_final"),
        "window_start_utc": overview.get("window_start_utc") or scope.get("window_start_utc"),
        "window_end_utc": overview.get("window_end_utc") or scope.get("window_end_utc"),
        "timezone": scope.get("timezone"),
    }
    slots = []
    incident_id = snapshot["incident_id"]
    for index, point in enumerate(points):
        state = _slot_state(point)
        value = point.get(value_field) if state == "observed" else None
        if state == "observed" and not isinstance(value, (int, float)):
            state = "unknown"
            value = None
        slots.append(
            {
                "index": index,
                "observed_at_utc": point.get("observed_at_utc"),
                "state": state,
                "value": value,
                "source_ref": (
                    f"/api/v2/country-outages/{incident_id}/series"
                    f"#/series/{index}/{value_field}"
                ),
            }
        )
    return {
        "schema_version": "country_outage_trend_profile_input_v1",
        "snapshot": snapshot,
        "metric": {
            "metric_id": metric_id,
            "label": label,
            "unit": "count",
            "statistical_population": statistical_population,
            "denominator": {
                "value": denominator,
                "unit": "count",
                "statistical_population": statistical_population,
            },
        },
        "time_grid": {
            "slot_seconds": interval_seconds,
            "expected_slot_count": len(slots),
        },
        "baseline": {"type": "fixed_cohort"},
        "slots": slots,
    }


def _longest_state_run(states: Sequence[str], selected: set[str]) -> int:
    longest = current = 0
    for state in states:
        if state in selected:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _aggregate_asn_context(
    profile: Mapping[str, Any],
    pages: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if not pages:
        return None
    analyzed = analyze_trend_profile_v1(profile)
    page_identity = pages[0]
    for key in ("incident_id", "publication_id", "revision", "data_through"):
        if page_identity.get(key) != analyzed["snapshot"].get(key):
            _fail("asn_page_identity_conflict", f"asn_pages.{key}", "ASN 分页与趋势快照不一致")
    items = [item for page in pages for item in page.get("items", [])]
    expected_count = analyzed["time_grid"]["expected_slot_count"]
    sample_indices = sorted(
        {
            0,
            expected_count - 1,
            *(
                phase["end_slot_index"]
                for phase in analyzed["analysis"].get("phases", [])
            ),
        }
    )
    views = []
    for row in items:
        raw_states = row.get("states") or []
        if len(raw_states) != expected_count:
            _fail("asn_slot_count_conflict", "asn_pages.items.states", "ASN 状态槽数与趋势不一致")
        states = [ASN_CODE_TO_STATE.get(value, "unknown") for value in raw_states]
        views.append(
            {
                "asn": int(row["asn"]),
                "address_families": [
                    "ipv4" if value == 4 else "ipv6"
                    for value in row.get("address_families", [])
                    if value in {4, 6}
                ],
                "per_family_state_status": "unavailable_in_current_observation_contract",
                "baseline_prefix_count": row.get("baseline_prefix_count"),
                "baseline_prefix_vp_count": row.get("baseline_prefix_vp_count"),
                "start_state": states[0],
                "end_state": states[-1],
                "persistent_not_at_start": states[-1] != states[0],
                "state_slot_counts": {
                    state: states.count(state) for state in ASN_CODE_TO_STATE.values()
                },
                "longest_runs": {
                    state: _longest_state_run(states, {state})
                    for state in ASN_CODE_TO_STATE.values()
                },
                "longest_observed_non_fully_visible_run": _longest_state_run(
                    states, {"partially_visible", "fully_invisible"}
                ),
                "sampled_states": [
                    {"slot_index": index, "state": states[index]}
                    for index in sample_indices
                ],
                "transitions": [
                    {
                        "from_slot_index": index - 1,
                        "to_slot_index": index,
                        "from_state": states[index - 1],
                        "to_state": states[index],
                    }
                    for index in range(1, len(states))
                    if states[index] != states[index - 1]
                ],
            }
        )
    slot_population = []
    for index in range(expected_count):
        counts = Counter(
            next(
                sample["state"]
                for sample in view["sampled_states"]
                if sample["slot_index"] == index
            )
            if index in sample_indices
            else ASN_CODE_TO_STATE.get(
                next(
                    item for item in items if int(item["asn"]) == view["asn"]
                )["states"][index],
                "unknown",
            )
            for view in views
        )
        slot_population.append(
            {
                "slot_index": index,
                "state_counts": {
                    state: counts.get(state, 0)
                    for state in ASN_CODE_TO_STATE.values()
                },
                "total_asn_count": len(views),
            }
        )
    scale = sorted(
        views,
        key=lambda item: (-(item.get("baseline_prefix_vp_count") or 0), item["asn"]),
    )
    persistence = sorted(
        views,
        key=lambda item: (
            -item["longest_observed_non_fully_visible_run"],
            -(item.get("baseline_prefix_vp_count") or 0),
            item["asn"],
        ),
    )
    material = {
        "profile_id": analyzed["profile_id"],
        "analysis_id": analyzed["analysis"]["analysis_id"],
        "asns": views,
    }
    return {
        "schema_version": "country_outage_trend_context_v1",
        "algorithm_version": "country_outage_trend_context_s4_adapter_v1",
        "context_type": "asn_state_context",
        "context_id": f"trend_context_v1_{_digest(material)[:32]}",
        "profile_id": analyzed["profile_id"],
        "analysis_id": analyzed["analysis"]["analysis_id"],
        "snapshot": deepcopy(analyzed["snapshot"]),
        "time_grid": deepcopy(analyzed["time_grid"]),
        "asn_states": list(ASN_CODE_TO_STATE.values()),
        "sample_indices": sample_indices,
        "asn_count": len(views),
        "baseline_prefix_vp_count": sum(
            item.get("baseline_prefix_vp_count") or 0 for item in views
        ),
        "slot_population": slot_population,
        "transition_matrices": [],
        "asns": views,
        "priority_views": {
            "by_observation_scale": [
                {
                    "asn": item["asn"],
                    "baseline_prefix_vp_count": item.get("baseline_prefix_vp_count"),
                    "longest_observed_non_fully_visible_run": item["longest_observed_non_fully_visible_run"],
                }
                for item in scale
            ],
            "by_persistence": [
                {
                    "asn": item["asn"],
                    "baseline_prefix_vp_count": item.get("baseline_prefix_vp_count"),
                    "longest_observed_non_fully_visible_run": item["longest_observed_non_fully_visible_run"],
                }
                for item in persistence
            ],
            "single_impact_score": None,
        },
        "limitations": [
            "asn_order_is_not_user_or_service_impact",
            "asn_order_is_not_cause_responsibility_or_propagation",
            "unknown_is_independent_population_state",
            "per_family_asn_state_unavailable_in_current_observation_contract",
        ],
    }


def _activity_context(
    profile: Mapping[str, Any],
    series: Mapping[str, Any],
) -> dict[str, Any] | None:
    points = series.get("series") or []
    tracks = []
    for track_id, metric_id, population in (
        ("update_total", "update_total", "update_message"),
        ("announce_count", "announce_count", "announce_message"),
        ("withdraw_count", "withdraw_count", "withdraw_message"),
    ):
        if not any(isinstance(point.get(metric_id), int) for point in points):
            continue
        tracks.append(
            {
                "track_id": track_id,
                "metric_id": metric_id,
                "unit": "count",
                "statistical_population": population,
                "slots": [
                    {
                        "index": index,
                        "state": (
                            _slot_state(point)
                            if isinstance(point.get(metric_id), int)
                            else "unknown"
                        ),
                        "value": (
                            point.get(metric_id)
                            if _slot_state(point) == "observed"
                            and isinstance(point.get(metric_id), int)
                            else None
                        ),
                        "source_ref": (
                            f"/api/v2/country-outages/{profile['snapshot']['incident_id']}"
                            f"/series#/series/{index}/{metric_id}"
                        ),
                    }
                    for index, point in enumerate(points)
                ],
            }
        )
    if not tracks or profile["analysis"]["status"] != "complete":
        return None
    return align_activity_context_v1(profile, tracks)


def compile_country_outage_trend_product_from_resources(
    overview: Mapping[str, Any],
    series: Mapping[str, Any],
    asn_pages: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """把同一 v2 发布的 overview/series/asns 适配为权威趋势制品。"""
    for key in (
        "incident_id",
        "publication_id",
        "revision",
        "data_through",
        "cohort_id",
        "window_start_utc",
        "window_end_utc",
    ):
        if overview.get(key) != series.get(key):
            _fail("resource_identity_conflict", f"series.{key}", "overview 与 series 发布身份不一致")
    cohort = overview.get("cohort") or {}
    denominator = cohort.get("prefix_vp_count")
    if not isinstance(denominator, int) or denominator <= 0:
        _fail("cohort_unavailable", "overview.cohort.prefix_vp_count", "固定 cohort 分母不可用")
    main = analyze_trend_profile_v1(
        compile_trend_profile_v1(
            _profile_request_from_resources(
                overview,
                series,
                metric_id="visible_prefix_vp_count",
                label="可见 Prefix×VP 数量",
                statistical_population="fixed_prefix_vp",
                denominator=denominator,
                value_field="visible_prefix_vp_count",
            )
        )
    )
    family_profiles = {}
    for family, field, denominator_field, population in (
        ("ipv4", "ipv4_visible_prefix_vp_count", "ipv4_prefix_vp_count", "ipv4_fixed_prefix_vp"),
        ("ipv6", "ipv6_visible_prefix_vp_count", "ipv6_prefix_vp_count", "ipv6_fixed_prefix_vp"),
    ):
        family_denominator = cohort.get(denominator_field)
        if isinstance(family_denominator, int) and family_denominator > 0:
            family_profiles[family] = analyze_trend_profile_v1(
                compile_trend_profile_v1(
                    _profile_request_from_resources(
                        overview,
                        series,
                        metric_id="visible_prefix_vp_count",
                        label=f"{family.upper()} 可见 Prefix×VP 数量",
                        statistical_population=population,
                        denominator=family_denominator,
                        value_field=field,
                    )
                )
            )
    address_family = (
        compare_address_families_v1(family_profiles["ipv4"], family_profiles["ipv6"])
        if set(family_profiles) == {"ipv4", "ipv6"}
        else None
    )
    asn_context = _aggregate_asn_context(main, asn_pages)
    activity_context = _activity_context(main, series)
    product = compile_trend_product_v1(
        main,
        address_family_context=address_family,
        asn_context=asn_context,
        activity_context=activity_context,
    )
    return {
        **product,
        "event_identity": deepcopy(dict(overview.get("event_identity") or {})),
        "observation_scope": deepcopy(dict(overview.get("observation_scope") or {})),
        "capabilities": deepcopy(dict(overview.get("capabilities") or {})),
    }


def get_country_outage_trend_product(
    incident_id: str,
    *,
    publication_id: str | None = None,
) -> dict[str, Any]:
    """读取同一不可变发布的现有 v2 资源并生成只读趋势制品。"""
    from services.country_outage_service import (
        get_country_outage_asns,
        get_country_outage_overview,
        get_country_outage_series,
    )

    overview = get_country_outage_overview(incident_id, publication_id=publication_id)
    series = get_country_outage_series(incident_id, publication_id=publication_id)
    first = get_country_outage_asns(
        incident_id,
        publication_id=publication_id,
        page=1,
        page_size=60,
    )
    pages = [first]
    for page in range(2, int(first.get("page_count") or 1) + 1):
        pages.append(
            get_country_outage_asns(
                incident_id,
                publication_id=publication_id,
                page=page,
                page_size=60,
            )
        )
    return compile_country_outage_trend_product_from_resources(
        overview, series, pages
    )


def compile_evidence_graph_v1(
    profile: Mapping[str, Any],
    *,
    address_family_context: Mapping[str, Any] | None = None,
    asn_context: Mapping[str, Any] | None = None,
    activity_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """把确定性分析结果编译为 Claim/Evidence/Limitation/Unknown 图。"""
    analyzed = analyze_trend_profile_v1(profile)
    contexts = {
        "address_family": address_family_context,
        "asn": asn_context,
        "activity": activity_context,
    }
    for name, context in contexts.items():
        if context is not None:
            _same_identity(analyzed, context, name)

    seed = f"evidence_graph_v1_{_digest({'profile_id': analyzed['profile_id'], 'analysis_id': analyzed['analysis']['analysis_id'], 'contexts': {key: value.get('context_id') if value else None for key, value in contexts.items()}})[:32]}"
    snapshot = analyzed["snapshot"]
    limitations = [
        _limitation_node(seed, "rrc25_single_collector", "仅代表 RRC25 单 collector 的 BGP 控制面观测。"),
        _limitation_node(seed, "control_plane_only", "不包含用户流量、时延、DNS 或服务可用性数据。"),
        _limitation_node(seed, "window_only", "只描述固定观测窗口；窗口结束后的状态未知。"),
        _limitation_node(seed, "window_start_not_normal", "窗口起点是观测参照，不是历史正常基线。"),
    ]
    unknowns = [
        _unknown_node(seed, "cause", "造成观测变化的原因未知。"),
        _unknown_node(seed, "attack", "是否存在攻击或安全事件未知。"),
        _unknown_node(seed, "user_impact", "用户或业务影响未知。"),
        _unknown_node(seed, "post_window_recovery", "窗口结束后是否完全恢复未知。"),
        _unknown_node(seed, "responsibility", "攻击、政策与责任主体未知。"),
    ]
    limitation_ids = [item["node_id"] for item in limitations]
    unknown_ids = [item["node_id"] for item in unknowns]

    evidence: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []

    def add_claim(
        claim_kind: str,
        text: str,
        evidence_nodes: Sequence[dict[str, Any]],
        *,
        values: Mapping[str, Any] | None = None,
        claim_limitations: Sequence[str] | None = None,
        claim_unknowns: Sequence[str] | None = None,
    ) -> None:
        claim_id = f"{seed}:claim:{claim_kind}"
        evidence_ids = []
        for node in evidence_nodes:
            if not any(existing["node_id"] == node["node_id"] for existing in evidence):
                evidence.append(node)
            evidence_ids.append(node["node_id"])
            edges.append({"from": claim_id, "relation": "supported_by", "to": node["node_id"]})
        selected_limitations = list(claim_limitations or limitation_ids)
        selected_unknowns = list(claim_unknowns or unknown_ids)
        for node_id in selected_limitations:
            edges.append({"from": claim_id, "relation": "limited_by", "to": node_id})
        for node_id in selected_unknowns:
            edges.append({"from": claim_id, "relation": "unknown_about", "to": node_id})
        claims.append(
            {
                "node_id": claim_id,
                "node_type": "Claim",
                "claim_kind": claim_kind,
                "text": text,
                "values": deepcopy(dict(values or {})),
                "evidence_refs": evidence_ids,
                "limitation_refs": selected_limitations,
                "unknown_refs": selected_unknowns,
                "conclusion_level": "rrc25_control_plane_observation",
            }
        )

    quality_node = _evidence_node(
        node_id=f"{seed}:evidence:quality",
        evidence_kind="quality",
        label="时间槽质量与人口",
        payload={
            "quality": analyzed["quality"],
            "metric": analyzed["metric"],
            "time_grid": analyzed["time_grid"],
        },
        source_refs=[slot["source_ref"] for slot in analyzed["slots"]],
        snapshot=snapshot,
    )
    add_claim(
        "quality",
        (
            f"本趋势已观测 {analyzed['quality']['observed_slot_count']} / "
            f"{analyzed['quality']['expected_slot_count']} 个时间槽，质量状态为 "
            f"{analyzed['quality']['status']}。"
        ),
        [quality_node],
        values={
            "observed_slot_count": analyzed["quality"]["observed_slot_count"],
            "expected_slot_count": analyzed["quality"]["expected_slot_count"],
            "quality_status": analyzed["quality"]["status"],
        },
    )

    analysis = analyzed["analysis"]
    if analysis["status"] == "complete":
        points = {item["kind"]: item for item in analysis["key_points"]}
        facts = {item["metric"]: item for item in analysis["derived_facts"]}
        end_fact = facts["end_residual_from_start"]
        window_node = _evidence_node(
            node_id=f"{seed}:evidence:window-ledger",
            evidence_kind="derived_fact",
            label="窗口关键点与可重算账本",
            payload={
                "start": points["start"],
                "extreme": points["extreme_minimum"],
                "end": points["end"],
                "residual": end_fact,
            },
            source_refs=end_fact["source_refs"],
            snapshot=snapshot,
        )
        add_claim(
            "window_state",
            (
                f"窗口起点为 {_format_number(points['start']['value'])}，谷值为 "
                f"{_format_number(points['extreme_minimum']['value'])}，终点为 "
                f"{_format_number(points['end']['value'])}；终点相对起点残留 "
                f"{_format_number(end_fact['value'])} {end_fact['unit']}。"
            ),
            [window_node],
            values={
                "start": points["start"]["value"],
                "extreme": points["extreme_minimum"]["value"],
                "end": points["end"]["value"],
                "end_residual_from_start": end_fact["value"],
                "unit": end_fact["unit"],
            },
        )
        fastest = points["largest_single_slot_drop_end"]
        fastest_node = _evidence_node(
            node_id=f"{seed}:evidence:fastest-drop",
            evidence_kind="key_point",
            label="最大单槽下降",
            payload=fastest,
            source_refs=[fastest["source_ref"]],
            snapshot=snapshot,
        )
        add_claim(
            "fastest_change",
            (
                f"最快恶化落在槽 {fastest['slot_index']}（{fastest['observed_at_utc']}），"
                f"单槽变化为 {_format_number(fastest['change_from_previous'])} "
                f"{fastest['unit']}。"
            ),
            [fastest_node],
            values={
                "slot_index": fastest["slot_index"],
                "observed_at_utc": fastest["observed_at_utc"],
                "change_from_previous": fastest["change_from_previous"],
                "visible_prefix_vp_delta": fastest["change_from_previous"],
                "unit": fastest["unit"],
            },
        )
        phase_node = _evidence_node(
            node_id=f"{seed}:evidence:phases",
            evidence_kind="phase_sequence",
            label="确定性阶段序列",
            payload={
                "pattern": analysis["pattern"],
                "phases": analysis["phases"],
            },
            source_refs=analysis["evidence_refs"],
            snapshot=snapshot,
        )
        add_claim(
            "phase_sequence",
            "窗口内阶段序列为："
            + " → ".join(phase["kind"] for phase in analysis["phases"])
            + "。",
            [phase_node],
            values={
                "phase_ids": [phase["phase_id"] for phase in analysis["phases"]],
                "pattern_status": analysis["pattern"]["status"],
                "pattern_label": analysis["pattern"]["label"],
            },
        )

    if address_family_context is not None:
        comparison = address_family_context.get("comparison", {})
        af_node = _evidence_node(
            node_id=f"{seed}:evidence:address-families",
            evidence_kind="address_family_context",
            label="IPv4 与 IPv6 分母和时间对照",
            payload=address_family_context,
            source_refs=[
                ref
                for family in address_family_context.get("families", {}).values()
                for point in family.get("key_points", [])
                for ref in ([point.get("source_ref")] if point.get("source_ref") else [])
            ],
            snapshot=snapshot,
        )
        add_claim(
            "address_family_comparison",
            (
                "IPv4 与 IPv6 使用独立分母进行比率对照；"
                f"比较状态为 {comparison.get('status', 'unknown')}，"
                "观测差异不解释为原因或用户影响。"
            ),
            [af_node],
            values={
                "ipv4_denominator": address_family_context.get("families", {}).get("ipv4", {}).get("denominator", {}).get("value"),
                "ipv6_denominator": address_family_context.get("families", {}).get("ipv6", {}).get("denominator", {}).get("value"),
                "maximum_divergence": comparison.get("maximum_divergence"),
                "extreme_alignment": comparison.get("extreme_alignment"),
            },
        )

    if asn_context is not None:
        asns = asn_context.get("asns", [])
        persistent = [item for item in asns if item.get("persistent_not_at_start")]
        unknown_end = [item for item in asns if item.get("end_state") == "unknown"]
        asn_node = _evidence_node(
            node_id=f"{seed}:evidence:asn-context",
            evidence_kind="asn_state_context",
            label="ASN 四状态迁移、持续性与观测规模",
            payload={
                "asn_count": asn_context.get("asn_count"),
                "persistent_not_at_start": persistent,
                "unknown_end": unknown_end,
                "priority_views": asn_context.get("priority_views"),
                "transition_matrices": asn_context.get("transition_matrices"),
            },
            source_refs=[],
            snapshot=snapshot,
        )
        add_claim(
            "asn_persistence",
            (
                f"{len(persistent)} 个 ASN 的窗口终点状态未回到各自起点状态；"
                f"其中 {len(unknown_end)} 个 ASN 的终点状态未知。"
            ),
            [asn_node],
            values={
                "persistent_not_at_start_count": len(persistent),
                "unknown_end_count": len(unknown_end),
                "asn_count": asn_context.get("asn_count"),
            },
        )

    if activity_context is not None:
        relations = activity_context.get("temporal_relations", [])
        activity_node = _evidence_node(
            node_id=f"{seed}:evidence:activity-alignment",
            evidence_kind="activity_alignment",
            label="活动与状态的时间对应",
            payload={
                "temporal_relations": relations,
                "cross_population_arithmetic_performed": activity_context.get("cross_population_arithmetic_performed"),
                "causal_claim": activity_context.get("causal_claim"),
            },
            source_refs=[
                slot["source_ref"]
                for track in activity_context.get("tracks", [])
                for slot in track.get("slots", [])
            ],
            snapshot=snapshot,
        )
        add_claim(
            "activity_alignment",
            "活动轨道与状态关键点仅按同槽、相邻槽或滞后槽建立时间对应，不作因果解释。",
            [activity_node],
            values={
                "relation_count": len(relations),
                "relation_types": sorted({item.get("relation") for item in relations}),
            },
        )

    nodes = [*claims, *evidence, *limitations, *unknowns]
    graph_material = {
        "schema_version": EVIDENCE_GRAPH_SCHEMA_VERSION,
        "algorithm_version": TREND_PRODUCT_ALGORITHM_VERSION,
        "profile_id": analyzed["profile_id"],
        "analysis_id": analysis["analysis_id"],
        "snapshot": snapshot,
        "nodes": nodes,
        "edges": edges,
    }
    graph_id = f"evidence_graph_v1_{_digest(graph_material)[:32]}"
    return {
        **graph_material,
        "graph_id": graph_id,
        "node_types": list(NODE_TYPES),
        "relation_types": list(RELATION_TYPES),
        "hypothesis_nodes_allowed": False,
        "causal_relations_allowed": False,
    }


def validate_evidence_graph_v1(graph: Mapping[str, Any]) -> list[str]:
    """返回图合同偏差；空数组表示结构与身份约束通过。"""
    errors: list[str] = []
    if graph.get("schema_version") != EVIDENCE_GRAPH_SCHEMA_VERSION:
        errors.append("Evidence Graph schema_version 不正确")
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return [*errors, "Evidence Graph nodes/edges 必须是数组"]
    node_ids = {node.get("node_id") for node in nodes if isinstance(node, Mapping)}
    claims = [node for node in nodes if isinstance(node, Mapping) and node.get("node_type") == "Claim"]
    for node in nodes:
        if not isinstance(node, Mapping) or node.get("node_type") not in NODE_TYPES:
            errors.append("Evidence Graph 出现非白名单节点类型")
            continue
        if node.get("node_id") in {None, ""}:
            errors.append("Evidence Graph 节点缺少稳定 ID")
    for edge in edges:
        if not isinstance(edge, Mapping) or edge.get("relation") not in RELATION_TYPES:
            errors.append("Evidence Graph 出现非白名单关系")
            continue
        if edge.get("from") not in node_ids or edge.get("to") not in node_ids:
            errors.append("Evidence Graph 关系引用不存在的节点")
    for claim in claims:
        linked = {
            edge.get("relation")
            for edge in edges
            if isinstance(edge, Mapping) and edge.get("from") == claim.get("node_id")
        }
        for required in RELATION_TYPES:
            if required not in linked:
                errors.append(f"Claim {claim.get('node_id')} 缺少 {required}")
    if graph.get("hypothesis_nodes_allowed") is not False:
        errors.append("Evidence Graph 必须显式禁止 Hypothesis")
    if graph.get("causal_relations_allowed") is not False:
        errors.append("Evidence Graph 必须显式禁止因果关系")
    return errors


def compile_trend_product_v1(
    profile: Mapping[str, Any],
    *,
    address_family_context: Mapping[str, Any] | None = None,
    asn_context: Mapping[str, Any] | None = None,
    activity_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """形成页面、报告、追问和下载可共同消费的冻结分析制品。"""
    analyzed = analyze_trend_profile_v1(profile)
    graph = compile_evidence_graph_v1(
        analyzed,
        address_family_context=address_family_context,
        asn_context=asn_context,
        activity_context=activity_context,
    )
    errors = validate_evidence_graph_v1(graph)
    if errors:
        _fail("invalid_evidence_graph", "evidence_graph", "；".join(errors))
    contexts = {
        "address_family": deepcopy(dict(address_family_context)) if address_family_context else None,
        "asn": deepcopy(dict(asn_context)) if asn_context else None,
        "activity": deepcopy(dict(activity_context)) if activity_context else None,
    }
    claims = [node for node in graph["nodes"] if node["node_type"] == "Claim"]
    product_material = {
        "schema_version": TREND_PRODUCT_SCHEMA_VERSION,
        "algorithm_version": TREND_PRODUCT_ALGORITHM_VERSION,
        "profile": analyzed,
        "contexts": contexts,
        "evidence_graph": graph,
        "reading_journey": [
            "identity_and_quality",
            "window_state_claim",
            "trend_phases_and_key_points",
            "window_ledger",
            "address_family_comparison",
            "asn_transition_and_persistence",
            "activity_time_alignment",
            "claim_evidence_limitation_unknown",
            "bounded_qa_and_download",
        ],
        "claim_ids": [claim["node_id"] for claim in claims],
    }
    product_id = f"trend_product_v1_{_digest(product_material)[:32]}"
    return {
        **product_material,
        "product_id": product_id,
        "snapshot": deepcopy(analyzed["snapshot"]),
        "profile_id": analyzed["profile_id"],
        "analysis_id": analyzed["analysis"]["analysis_id"],
        "graph_id": graph["graph_id"],
        "qa_rule_version": QA_RULE_VERSION,
        "render_contract": {
            "source_product_id": product_id,
            "surfaces": ["page", "report", "qa", "markdown", "pdf", "json_download"],
            "model_may_rewrite_deterministic_values": False,
        },
    }


def _answer_from_claim(
    product: Mapping[str, Any],
    claim_kind: str,
    *,
    answer_prefix: str | None = None,
) -> dict[str, Any]:
    claim = next(
        (
            node
            for node in product["evidence_graph"]["nodes"]
            if node.get("node_type") == "Claim" and node.get("claim_kind") == claim_kind
        ),
        None,
    )
    if claim is None:
        return {
            "status": "insufficient_data",
            "answer": "当前冻结分析制品没有足够事实回答该问题。",
            "evidence_refs": [],
            "limitation_refs": [],
            "unknown_refs": [],
        }
    return {
        "status": "answered",
        "answer": f"{answer_prefix or ''}{claim['text']}",
        "claim_refs": [claim["node_id"]],
        "evidence_refs": claim["evidence_refs"],
        "limitation_refs": claim["limitation_refs"],
        "unknown_refs": claim["unknown_refs"],
    }


def answer_trend_question_v1(product: Mapping[str, Any], question: str) -> dict[str, Any]:
    """按白名单算子回答事件内问题；越界问题只返回证据边界。"""
    if product.get("schema_version") != TREND_PRODUCT_SCHEMA_VERSION:
        _fail("unsupported_product", "schema_version", "趋势制品版本不兼容")
    normalized = "".join(str(question).lower().split())
    if not normalized:
        _fail("empty_question", "question", "问题不能为空")
    forbidden = {
        "cause": ("原因", "为什么", "根因", "导致", "造成"),
        "attack": ("攻击", "黑客", "劫持"),
        "user_impact": ("用户影响", "业务影响", "全国断网", "无法访问"),
        "responsibility": ("责任", "负责", "政策", "政府", "运营商责任"),
        "post_window_recovery": ("完全恢复", "窗口后", "后来恢复", "现在恢复"),
    }
    unknowns = {
        node.get("code"): node
        for node in product["evidence_graph"]["nodes"]
        if node.get("node_type") == "Unknown"
    }
    for code, words in forbidden.items():
        if any(word in normalized for word in words):
            node = unknowns[code]
            return {
                "schema_version": "country_outage_trend_answer_v1",
                "qa_rule_version": QA_RULE_VERSION,
                "product_id": product["product_id"],
                "status": "abstained",
                "operator": "evidence_boundary",
                "answer": f"现有 RRC25 BGP 控制面证据不足以判断。{node['text']}",
                "claim_refs": [],
                "evidence_refs": [],
                "limitation_refs": [
                    node["node_id"]
                    for node in product["evidence_graph"]["nodes"]
                    if node.get("node_type") == "Limitation"
                ],
                "unknown_refs": [node["node_id"]],
            }

    operators = [
        (("update", "announce", "withdraw", "同槽", "相邻槽", "滞后槽"), "activity_alignment", "activity_alignment"),
        (("asn", "持续", "迁移", "未回到"), "asn_persistence", "asn_persistence"),
        (("ipv4", "ipv6", "地址族", "分化"), "address_family_comparison", "address_family_comparison"),
        (("最快", "最大单槽下降", "恶化最快"), "fastest_change", "fastest_change"),
        (("谷值", "最低", "最小"), "window_extreme", "window_state"),
        (("终点", "回到起点", "残留", "回升"), "window_ledger", "window_state"),
        (("阶段", "单波", "多波", "震荡", "平台"), "phase_sequence", "phase_sequence"),
        (("证据", "为什么这么说", "依据"), "evidence_navigation", "window_state"),
    ]
    for words, operator, claim_kind in operators:
        if any(word in normalized for word in words):
            answer = _answer_from_claim(product, claim_kind)
            return {
                "schema_version": "country_outage_trend_answer_v1",
                "qa_rule_version": QA_RULE_VERSION,
                "product_id": product["product_id"],
                "operator": operator,
                **answer,
            }
    return {
        "schema_version": "country_outage_trend_answer_v1",
        "qa_rule_version": QA_RULE_VERSION,
        "product_id": product["product_id"],
        "status": "unsupported",
        "operator": "none",
        "answer": "该问题不在当前趋势分析白名单内；未生成新事实。",
        "claim_refs": [],
        "evidence_refs": [],
        "limitation_refs": [],
        "unknown_refs": [],
    }
