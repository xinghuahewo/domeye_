"""RRC25 国家中断趋势画像的确定性身份、质量与基线编译器。

S1 只建立 TrendProfile 地基。关键点、原子状态、阶段和窗口账本由后续阶段
在同一 profile_id 上补齐；本模块不读取数据库、网络、模型或窗口外数据。
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


INPUT_SCHEMA_VERSION = "country_outage_trend_profile_input_v1"
PROFILE_SCHEMA_VERSION = "country_outage_trend_profile_v1"
ALGORITHM_VERSION = "country_outage_trend_foundation_s1_v1"
ANALYSIS_ALGORITHM_VERSION = "country_outage_trend_analysis_s2_v1"
ANALYSIS_RULE = {
    "rule_version": "country_outage_trend_rule_s2_v1",
    "directional_change_percentage_points": 2,
    "abrupt_change_percentage_points": 8,
    "low_plateau_deficit_percentage_points": 20,
    "small_denominator_exclusive_upper_bound": 10,
    "oscillation_min_directional_transitions": 8,
    "oscillation_min_alternation_ratio": 0.8,
    "oscillation_min_total_variation_percentage_points": 50,
    "threshold_visible_ratios": [0.95, 0.9, 0.8],
    "rounding_decimal_places": 6,
    "rounding_mode": "half_even",
}
ALLOWED_SLOT_STATES = (
    "observed",
    "missing",
    "unknown",
    "source_unavailable",
    "processing_gap",
    "parse_failed",
    "not_observed",
)
ALLOWED_BASELINE_TYPES = (
    "fixed_cohort",
    "window_start",
    "contemporaneous_reference",
    "unavailable",
)
ALLOWED_UNITS = (
    "count",
    "ratio",
    "percentage_point",
)
COUNTRY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")


class TrendProfileValidationError(ValueError):
    """输入不能形成单一、可比较且可审计的 TrendProfile。"""

    def __init__(self, code: str, field: str, message: str):
        super().__init__(message)
        self.code = code
        self.field = field


def _fail(code: str, field: str, message: str) -> None:
    raise TrendProfileValidationError(code, field, message)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("invalid_type", field, f"{field} 必须是对象")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("invalid_string", field, f"{field} 必须是非空字符串")
    return value.strip()


def _integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _fail("invalid_integer", field, f"{field} 必须是整数")
    if minimum is not None and value < minimum:
        _fail("integer_below_minimum", field, f"{field} 不得小于 {minimum}")
    return value


def _number(value: Any, field: str) -> int | float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        _fail("invalid_number", field, f"{field} 必须是数值")
    return value


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        _fail("invalid_boolean", field, f"{field} 必须是布尔值")
    return value


def _iso8601(value: Any, field: str) -> tuple[str, datetime]:
    text = _string(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _fail("invalid_timestamp", field, f"{field} 不是有效 ISO-8601 时间")
    if parsed.tzinfo is None:
        _fail("timestamp_without_timezone", field, f"{field} 必须带时区")
    normalized = parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
    return normalized, parsed


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


def _normalize_snapshot(value: Any) -> dict[str, Any]:
    snapshot = _mapping(value, "snapshot")
    event_type = _string(snapshot.get("event_type"), "snapshot.event_type")
    if event_type != "country_outage":
        _fail(
            "unsupported_event_type",
            "snapshot.event_type",
            "只接受 country_outage 事件",
        )
    collector_id = _string(snapshot.get("collector_id"), "snapshot.collector_id")
    if collector_id != "rrc25":
        _fail(
            "unsupported_collector",
            "snapshot.collector_id",
            "国家中断趋势只允许 rrc25",
        )
    collector_count = _integer(
        snapshot.get("collector_count"),
        "snapshot.collector_count",
        minimum=1,
    )
    if collector_count != 1:
        _fail(
            "unsupported_collector_count",
            "snapshot.collector_count",
            "国家中断趋势只允许单 collector",
        )
    country_code = _string(snapshot.get("country_code"), "snapshot.country_code").upper()
    if not COUNTRY_CODE_PATTERN.fullmatch(country_code):
        _fail(
            "invalid_country_code",
            "snapshot.country_code",
            "country_code 必须是两个大写字母",
        )
    event_reference = _string(
        snapshot.get("event_reference"), "snapshot.event_reference"
    )
    reference_parts = event_reference.split("/")
    if (
        len(reference_parts) != 5
        or reference_parts[0] != "country_outage"
        or reference_parts[2].upper() != country_code
    ):
        _fail(
            "event_country_conflict",
            "snapshot.event_reference",
            "事件引用与 country_code 不一致",
        )
    window_start, window_start_dt = _iso8601(
        snapshot.get("window_start_utc"), "snapshot.window_start_utc"
    )
    window_end, window_end_dt = _iso8601(
        snapshot.get("window_end_utc"), "snapshot.window_end_utc"
    )
    data_through, data_through_dt = _iso8601(
        snapshot.get("data_through"), "snapshot.data_through"
    )
    if window_end_dt <= window_start_dt:
        _fail(
            "invalid_window",
            "snapshot.window_end_utc",
            "观测窗口终点必须晚于起点",
        )
    if data_through_dt < window_end_dt:
        _fail(
            "snapshot_before_window_end",
            "snapshot.data_through",
            "data_through 不得早于观测窗口终点",
        )
    return {
        "event_type": event_type,
        "event_reference": event_reference,
        "incident_id": _string(snapshot.get("incident_id"), "snapshot.incident_id"),
        "country_code": country_code,
        "collector_id": collector_id,
        "collector_count": collector_count,
        "publication_id": _string(
            snapshot.get("publication_id"), "snapshot.publication_id"
        ),
        "revision": _integer(snapshot.get("revision"), "snapshot.revision", minimum=1),
        "data_through": data_through,
        "is_final": _boolean(snapshot.get("is_final"), "snapshot.is_final"),
        "window_start_utc": window_start,
        "window_end_utc": window_end,
        "timezone": _string(snapshot.get("timezone"), "snapshot.timezone"),
    }


def _normalize_metric(value: Any) -> dict[str, Any]:
    metric = _mapping(value, "metric")
    unit = _string(metric.get("unit"), "metric.unit")
    if unit not in ALLOWED_UNITS:
        _fail("unsupported_unit", "metric.unit", f"不支持的单位：{unit}")
    population = _string(
        metric.get("statistical_population"),
        "metric.statistical_population",
    )
    denominator = _mapping(metric.get("denominator"), "metric.denominator")
    denominator_value = _integer(
        denominator.get("value"),
        "metric.denominator.value",
        minimum=1,
    )
    denominator_unit = _string(
        denominator.get("unit"), "metric.denominator.unit"
    )
    denominator_population = _string(
        denominator.get("statistical_population"),
        "metric.denominator.statistical_population",
    )
    if denominator_unit != "count":
        _fail(
            "invalid_denominator_unit",
            "metric.denominator.unit",
            "分母单位必须为 count",
        )
    if denominator_population != population:
        _fail(
            "population_conflict",
            "metric.denominator.statistical_population",
            "分母统计人口必须与指标人口一致",
        )
    normalized = {
        "metric_id": _string(metric.get("metric_id"), "metric.metric_id"),
        "label": _string(metric.get("label"), "metric.label"),
        "unit": unit,
        "statistical_population": population,
        "denominator": {
            "value": denominator_value,
            "unit": denominator_unit,
            "statistical_population": denominator_population,
        },
    }
    return normalized


def _normalize_grid(
    value: Any,
    snapshot: Mapping[str, Any],
) -> tuple[dict[str, Any], list[datetime]]:
    grid = _mapping(value, "time_grid")
    slot_seconds = _integer(
        grid.get("slot_seconds"), "time_grid.slot_seconds", minimum=1
    )
    expected_slot_count = _integer(
        grid.get("expected_slot_count"),
        "time_grid.expected_slot_count",
        minimum=1,
    )
    _, start = _iso8601(snapshot["window_start_utc"], "snapshot.window_start_utc")
    _, end = _iso8601(snapshot["window_end_utc"], "snapshot.window_end_utc")
    actual_seconds = int((end - start).total_seconds())
    expected_seconds = (expected_slot_count - 1) * slot_seconds
    if actual_seconds != expected_seconds:
        _fail(
            "time_grid_window_conflict",
            "time_grid",
            "时间网格不能精确覆盖观测窗口起终点",
        )
    timestamps = [
        start + timedelta(seconds=index * slot_seconds)
        for index in range(expected_slot_count)
    ]
    if expected_slot_count > 1 and any(
        int((timestamps[index] - timestamps[index - 1]).total_seconds())
        != slot_seconds
        for index in range(1, expected_slot_count)
    ):
        _fail("invalid_time_grid", "time_grid", "时间槽间隔不一致")
    return {
        "slot_seconds": slot_seconds,
        "expected_slot_count": expected_slot_count,
        "window_start_utc": snapshot["window_start_utc"],
        "window_end_utc": snapshot["window_end_utc"],
    }, timestamps


def _normalize_slots(
    value: Any,
    *,
    metric: Mapping[str, Any],
    grid: Mapping[str, Any],
    expected_timestamps: Sequence[datetime],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _fail("invalid_type", "slots", "slots 必须是数组")
    if len(value) != grid["expected_slot_count"]:
        _fail(
            "slot_count_conflict",
            "slots",
            "slots 数量必须等于 expected_slot_count；缺槽须以显式状态保留",
        )
    normalized: list[dict[str, Any]] = []
    denominator = metric["denominator"]["value"]
    maximum_value = {
        "count": denominator,
        "ratio": 1,
        "percentage_point": 100,
    }[metric["unit"]]
    for index, raw_slot in enumerate(value):
        field = f"slots[{index}]"
        slot = _mapping(raw_slot, field)
        actual_index = _integer(slot.get("index"), f"{field}.index", minimum=0)
        if actual_index != index:
            _fail(
                "slot_index_conflict",
                f"{field}.index",
                "槽索引必须从 0 连续递增",
            )
        observed_at, observed_at_dt = _iso8601(
            slot.get("observed_at_utc"), f"{field}.observed_at_utc"
        )
        if observed_at_dt != expected_timestamps[index]:
            _fail(
                "slot_time_conflict",
                f"{field}.observed_at_utc",
                "槽时间必须与固定时间网格一致",
            )
        state = _string(slot.get("state"), f"{field}.state")
        if state not in ALLOWED_SLOT_STATES:
            _fail("unsupported_slot_state", f"{field}.state", f"不支持的槽状态：{state}")
        raw_value = slot.get("value")
        if state == "observed":
            numeric = _number(raw_value, f"{field}.value")
            if numeric < 0 or numeric > maximum_value:
                _fail(
                    "value_out_of_population",
                    f"{field}.value",
                    f"观测值必须位于 0 与 {maximum_value} 之间",
                )
            normalized_value: int | float | None = numeric
        else:
            if raw_value is not None:
                _fail(
                    "non_observed_value_present",
                    f"{field}.value",
                    "非 observed 槽必须为 null，禁止补零",
                )
            normalized_value = None
        normalized.append(
            {
                "index": index,
                "observed_at_utc": observed_at,
                "state": state,
                "value": normalized_value,
                "source_ref": _string(slot.get("source_ref"), f"{field}.source_ref"),
            }
        )
    return normalized


def _quality(slots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(slot["state"]) for slot in slots)
    state_counts = {state: counts.get(state, 0) for state in ALLOWED_SLOT_STATES}
    observed_count = counts.get("observed", 0)
    non_observed_count = len(slots) - observed_count
    start_observed = bool(slots) and slots[0]["state"] == "observed"
    end_observed = bool(slots) and slots[-1]["state"] == "observed"
    if observed_count == len(slots):
        status = "complete"
    elif observed_count == 0:
        status = "unavailable"
    else:
        status = "degraded"
    limitations: list[str] = []
    if status == "unavailable":
        limitations.append("no_observed_slots")
    elif status == "degraded":
        limitations.append("non_observed_slots_present")
    if not start_observed:
        limitations.append("window_start_not_observed")
    if not end_observed:
        limitations.append("window_end_not_observed")
    return {
        "status": status,
        "expected_slot_count": len(slots),
        "observed_slot_count": observed_count,
        "non_observed_slot_count": non_observed_count,
        "slot_state_counts": state_counts,
        "non_observed_slots": [
            {"index": slot["index"], "state": slot["state"]}
            for slot in slots
            if slot["state"] != "observed"
        ],
        "window_start_observed": start_observed,
        "window_end_observed": end_observed,
        "limitations": limitations,
    }


def _normalize_reference_grid(
    value: Any,
    *,
    grid: Mapping[str, Any],
) -> dict[str, Any]:
    reference = _mapping(value, "baseline.reference_time_grid")
    normalized = {
        "slot_seconds": _integer(
            reference.get("slot_seconds"),
            "baseline.reference_time_grid.slot_seconds",
            minimum=1,
        ),
        "expected_slot_count": _integer(
            reference.get("expected_slot_count"),
            "baseline.reference_time_grid.expected_slot_count",
            minimum=1,
        ),
        "window_start_utc": _iso8601(
            reference.get("window_start_utc"),
            "baseline.reference_time_grid.window_start_utc",
        )[0],
        "window_end_utc": _iso8601(
            reference.get("window_end_utc"),
            "baseline.reference_time_grid.window_end_utc",
        )[0],
    }
    if normalized != dict(grid):
        _fail(
            "reference_time_grid_conflict",
            "baseline.reference_time_grid",
            "同期参照必须使用相同时间网格与窗口",
        )
    return normalized


def _baseline(
    value: Any,
    *,
    metric: Mapping[str, Any],
    grid: Mapping[str, Any],
    quality: Mapping[str, Any],
    slots: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    baseline = _mapping(value, "baseline")
    requested_type = _string(baseline.get("type"), "baseline.type")
    if requested_type not in ALLOWED_BASELINE_TYPES:
        _fail(
            "unsupported_baseline_type",
            "baseline.type",
            f"不支持的基线类型：{requested_type}",
        )
    population = metric["statistical_population"]
    unit = metric["unit"]
    result: dict[str, Any] = {
        "type": requested_type,
        "value": None,
        "unit": unit,
        "statistical_population": population,
        "source": None,
        "reference_id": None,
        "interpretation": "observation_reference_not_normal_baseline",
        "limitations": [],
    }
    if requested_type == "fixed_cohort":
        result["value"] = {
            "count": metric["denominator"]["value"],
            "ratio": 1,
            "percentage_point": 100,
        }[unit]
        result["source"] = "metric.denominator"
    elif requested_type == "window_start":
        if not quality["window_start_observed"]:
            result["type"] = "unavailable"
            result["source"] = "slots[0]"
            result["limitations"].append("window_start_not_observed")
        else:
            result["value"] = slots[0]["value"]
            result["source"] = "slots[0]"
    elif requested_type == "contemporaneous_reference":
        reference_population = _string(
            baseline.get("statistical_population"),
            "baseline.statistical_population",
        )
        reference_unit = _string(baseline.get("unit"), "baseline.unit")
        if reference_population != population:
            _fail(
                "reference_population_conflict",
                "baseline.statistical_population",
                "同期参照统计人口必须兼容",
            )
        if reference_unit != unit:
            _fail(
                "reference_unit_conflict",
                "baseline.unit",
                "同期参照单位必须兼容",
            )
        _normalize_reference_grid(baseline.get("reference_time_grid"), grid=grid)
        reference_value = _number(baseline.get("value"), "baseline.value")
        maximum_value = {
            "count": metric["denominator"]["value"],
            "ratio": 1,
            "percentage_point": 100,
        }[unit]
        if reference_value < 0 or reference_value > maximum_value:
            _fail(
                "reference_value_out_of_population",
                "baseline.value",
                f"同期参照值必须位于 0 与 {maximum_value} 之间",
            )
        result["value"] = reference_value
        result["source"] = "contemporaneous_reference"
        result["reference_id"] = _string(
            baseline.get("reference_id"), "baseline.reference_id"
        )
    else:
        result["limitations"].append("baseline_unavailable")
    return result


def compile_trend_profile_v1(request: Mapping[str, Any]) -> dict[str, Any]:
    """把单一固定快照趋势输入编译为确定性的 S1 TrendProfile。"""
    if not isinstance(request, Mapping):
        _fail("invalid_type", "$", "请求必须是对象")
    if request.get("schema_version") != INPUT_SCHEMA_VERSION:
        _fail(
            "unsupported_schema",
            "schema_version",
            f"schema_version 必须为 {INPUT_SCHEMA_VERSION}",
        )
    snapshot = _normalize_snapshot(request.get("snapshot"))
    metric = _normalize_metric(request.get("metric"))
    grid, timestamps = _normalize_grid(request.get("time_grid"), snapshot)
    slots = _normalize_slots(
        request.get("slots"),
        metric=metric,
        grid=grid,
        expected_timestamps=timestamps,
    )
    quality = _quality(slots)
    baseline = _baseline(
        request.get("baseline"),
        metric=metric,
        grid=grid,
        quality=quality,
        slots=slots,
    )
    identity_material = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "snapshot": snapshot,
        "metric": metric,
        "time_grid": grid,
        "baseline": baseline,
        "slots": slots,
    }
    input_digest = _digest(identity_material)
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": f"trend_profile_v1_{input_digest[:32]}",
        "input_digest": input_digest,
        "algorithm_version": ALGORITHM_VERSION,
        "profile_state": "identity_quality_baseline_complete",
        "snapshot": snapshot,
        "metric": metric,
        "time_grid": grid,
        "quality": quality,
        "baseline": baseline,
        "slots": slots,
        "analysis": {
            "status": "not_computed_in_s1",
            "key_points": [],
            "atomic_states": [],
            "phases": [],
            "derived_facts": [],
            "evidence_refs": [],
        },
    }


def profile_compatibility_v1(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> dict[str, Any]:
    """判断两个画像能否直接计算；只返回兼容性，不执行趋势比较。"""
    mismatches: list[str] = []
    fields = (
        ("snapshot.incident_id", left.get("snapshot", {}).get("incident_id"), right.get("snapshot", {}).get("incident_id")),
        ("snapshot.publication_id", left.get("snapshot", {}).get("publication_id"), right.get("snapshot", {}).get("publication_id")),
        ("snapshot.revision", left.get("snapshot", {}).get("revision"), right.get("snapshot", {}).get("revision")),
        ("snapshot.data_through", left.get("snapshot", {}).get("data_through"), right.get("snapshot", {}).get("data_through")),
        ("snapshot.collector_id", left.get("snapshot", {}).get("collector_id"), right.get("snapshot", {}).get("collector_id")),
        ("time_grid", left.get("time_grid"), right.get("time_grid")),
        ("metric.unit", left.get("metric", {}).get("unit"), right.get("metric", {}).get("unit")),
        (
            "metric.statistical_population",
            left.get("metric", {}).get("statistical_population"),
            right.get("metric", {}).get("statistical_population"),
        ),
        (
            "metric.denominator",
            left.get("metric", {}).get("denominator"),
            right.get("metric", {}).get("denominator"),
        ),
    )
    for field, left_value, right_value in fields:
        if left_value != right_value:
            mismatches.append(field)
    return {
        "compatible": not mismatches,
        "mismatches": mismatches,
        "rule": "same_snapshot_time_grid_population_unit_and_denominator",
    }


def _rounded(value: int | float) -> int | float:
    rounded = round(value, ANALYSIS_RULE["rounding_decimal_places"])
    if isinstance(value, int) or rounded.is_integer():
        return int(rounded)
    return rounded


def _ratio_value(value: int | float, metric: Mapping[str, Any]) -> float:
    unit = metric["unit"]
    if unit == "count":
        return value / metric["denominator"]["value"]
    if unit == "ratio":
        return float(value)
    if unit == "percentage_point":
        return value / 100
    _fail("unsupported_analysis_unit", "metric.unit", f"无法分析单位：{unit}")


def _point(
    profile: Mapping[str, Any],
    *,
    kind: str,
    index: int,
    change_from_previous: int | float | None = None,
) -> dict[str, Any]:
    slot = profile["slots"][index]
    result = {
        "point_id": f"{profile['profile_id']}:point:{kind}",
        "kind": kind,
        "slot_index": index,
        "observed_at_utc": slot["observed_at_utc"],
        "value": slot["value"],
        "unit": profile["metric"]["unit"],
        "statistical_population": profile["metric"]["statistical_population"],
        "source_ref": slot["source_ref"],
        "change_from_previous": (
            _rounded(change_from_previous)
            if change_from_previous is not None
            else None
        ),
    }
    return result


def _key_points(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    slots = profile["slots"]
    values = [slot["value"] for slot in slots]
    extreme_index = min(range(len(values)), key=lambda index: (values[index], index))
    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    largest_drop_index = min(
        range(1, len(values)), key=lambda index: (deltas[index - 1], index)
    )
    largest_recovery_index = min(
        range(1, len(values)), key=lambda index: (-deltas[index - 1], index)
    )
    return [
        _point(profile, kind="start", index=0),
        _point(profile, kind="end", index=len(slots) - 1),
        _point(profile, kind="extreme_minimum", index=extreme_index),
        _point(
            profile,
            kind="largest_single_slot_drop_end",
            index=largest_drop_index,
            change_from_previous=deltas[largest_drop_index - 1],
        ),
        _point(
            profile,
            kind="largest_single_slot_recovery_end",
            index=largest_recovery_index,
            change_from_previous=deltas[largest_recovery_index - 1],
        ),
    ]


def _pattern_features(
    profile: Mapping[str, Any],
    normalized_values: Sequence[float],
) -> dict[str, Any]:
    deltas_pp = [
        (normalized_values[index] - normalized_values[index - 1]) * 100
        for index in range(1, len(normalized_values))
    ]
    directional_threshold = ANALYSIS_RULE["directional_change_percentage_points"]
    directional = [
        delta for delta in deltas_pp if abs(delta) >= directional_threshold
    ]
    signs = [1 if delta > 0 else -1 for delta in directional]
    alternation_count = sum(
        1 for index in range(1, len(signs)) if signs[index] != signs[index - 1]
    )
    alternation_ratio = (
        alternation_count / (len(signs) - 1) if len(signs) > 1 else 0
    )
    abrupt_threshold = ANALYSIS_RULE["abrupt_change_percentage_points"]
    abrupt_drop_indices = [
        index
        for index, delta in enumerate(deltas_pp, start=1)
        if delta <= -abrupt_threshold
    ]
    extreme_index = min(
        range(len(normalized_values)),
        key=lambda index: (normalized_values[index], index),
    )
    meaningful_rise_before_extreme = any(
        deltas_pp[index - 1] >= directional_threshold
        for index in range(1, extreme_index + 1)
    )
    return {
        "range_percentage_points": _rounded(
            (max(normalized_values) - min(normalized_values)) * 100
        ),
        "total_variation_percentage_points": _rounded(
            sum(abs(delta) for delta in deltas_pp)
        ),
        "directional_transition_count": len(directional),
        "alternation_count": alternation_count,
        "alternation_ratio": _rounded(alternation_ratio),
        "abrupt_drop_indices": abrupt_drop_indices,
        "extreme_index": extreme_index,
        "meaningful_rise_before_extreme": meaningful_rise_before_extreme,
    }


def _classify_pattern(
    profile: Mapping[str, Any],
    features: Mapping[str, Any],
) -> dict[str, Any]:
    denominator = profile["metric"]["denominator"]["value"]
    if denominator < ANALYSIS_RULE["small_denominator_exclusive_upper_bound"]:
        return {
            "status": "matched",
            "label": "small_denominator",
            "warnings": ["small_denominator"],
        }
    if (
        features["range_percentage_points"] <= 2
        and features["total_variation_percentage_points"] <= 10
    ):
        return {"status": "matched", "label": "plateau", "warnings": []}
    if (
        features["directional_transition_count"]
        >= ANALYSIS_RULE["oscillation_min_directional_transitions"]
        and features["alternation_ratio"]
        >= ANALYSIS_RULE["oscillation_min_alternation_ratio"]
        and features["total_variation_percentage_points"]
        >= ANALYSIS_RULE["oscillation_min_total_variation_percentage_points"]
    ):
        return {"status": "matched", "label": "oscillation", "warnings": []}
    abrupt_drop_indices = features["abrupt_drop_indices"]
    if len(abrupt_drop_indices) >= 2:
        return {"status": "matched", "label": "multi_wave", "warnings": []}
    if (
        len(abrupt_drop_indices) == 1
        and not features["meaningful_rise_before_extreme"]
        and features["extreme_index"] < len(profile["slots"]) - 1
        and profile["slots"][-1]["value"] > profile["slots"][features["extreme_index"]]["value"]
    ):
        start_value = profile["slots"][0]["value"]
        end_value = profile["slots"][-1]["value"]
        if end_value < start_value:
            label = "single_wave_partial_rebound"
        elif end_value == start_value:
            label = "single_wave_return_to_window_start"
        else:
            label = "single_wave_above_window_start"
        return {
            "status": "matched",
            "label": label,
            "warnings": [],
        }
    if abrupt_drop_indices:
        return {"status": "mixed", "label": None, "warnings": []}
    return {"status": "unmatched", "label": None, "warnings": []}


def _atomic_states(
    profile: Mapping[str, Any],
    *,
    pattern: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    slots = profile["slots"]
    if profile["quality"]["status"] != "complete":
        return [
            {
                "atomic_id": f"{profile['profile_id']}:atomic:{slot['index']}",
                "slot_index": slot["index"],
                "observed_at_utc": slot["observed_at_utc"],
                "state": slot["state"],
                "tags": [],
                "delta": None,
                "delta_percentage_points": None,
                "source_refs": [slot["source_ref"]],
            }
            for slot in slots
            if slot["state"] != "observed"
        ]

    metric = profile["metric"]
    normalized = [_ratio_value(slot["value"], metric) for slot in slots]
    abrupt = ANALYSIS_RULE["abrupt_change_percentage_points"]
    directional = ANALYSIS_RULE["directional_change_percentage_points"]
    low_level = (
        1 - ANALYSIS_RULE["low_plateau_deficit_percentage_points"] / 100
    )
    small_denominator = (
        metric["denominator"]["value"]
        < ANALYSIS_RULE["small_denominator_exclusive_upper_bound"]
    )
    states: list[dict[str, Any]] = []
    for index, slot in enumerate(slots):
        tags: list[str] = []
        if index == 0:
            state = "stable"
            delta = None
            delta_pp = None
            source_refs = [slot["source_ref"]]
        else:
            previous = slots[index - 1]
            delta = slot["value"] - previous["value"]
            delta_pp = (normalized[index] - normalized[index - 1]) * 100
            if small_denominator and delta < 0:
                state = "decline"
            elif small_denominator and delta > 0:
                state = "rise"
            elif small_denominator:
                state = "stable"
            elif delta_pp <= -abrupt:
                state = "abrupt_drop"
            elif delta_pp >= abrupt:
                state = "abrupt_rise"
            elif delta_pp <= -directional:
                state = "decline"
            elif delta_pp >= directional:
                state = "rise"
            else:
                state = "stable"
            source_refs = [previous["source_ref"], slot["source_ref"]]
        if (
            not small_denominator
            and state == "stable"
            and normalized[index] <= low_level
        ):
            state = "low_plateau"
        if pattern and pattern.get("label") == "oscillation" and state in {
            "rise",
            "decline",
            "abrupt_rise",
            "abrupt_drop",
        }:
            tags.append("high_volatility")
        states.append(
            {
                "atomic_id": f"{profile['profile_id']}:atomic:{index}",
                "slot_index": index,
                "observed_at_utc": slot["observed_at_utc"],
                "state": state,
                "tags": tags,
                "delta": _rounded(delta) if delta is not None else None,
                "delta_percentage_points": (
                    _rounded(delta_pp) if delta_pp is not None else None
                ),
                "source_refs": source_refs,
            }
        )
    return states


def _phases(
    profile: Mapping[str, Any],
    atomic_states: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if profile["quality"]["status"] != "complete" or not atomic_states:
        return []
    phases: list[dict[str, Any]] = []
    start = 0
    for index in range(1, len(atomic_states) + 1):
        if (
            index < len(atomic_states)
            and atomic_states[index]["state"] == atomic_states[start]["state"]
            and atomic_states[index]["tags"] == atomic_states[start]["tags"]
        ):
            continue
        phase_atomic = atomic_states[start:index]
        start_slot = profile["slots"][phase_atomic[0]["slot_index"]]
        end_slot = profile["slots"][phase_atomic[-1]["slot_index"]]
        phase_index = len(phases)
        phases.append(
            {
                "phase_id": f"{profile['profile_id']}:phase:{phase_index}",
                "ordinal": phase_index,
                "kind": phase_atomic[0]["state"],
                "tags": phase_atomic[0]["tags"],
                "start_slot_index": start_slot["index"],
                "end_slot_index": end_slot["index"],
                "start_at_utc": start_slot["observed_at_utc"],
                "end_at_utc": end_slot["observed_at_utc"],
                "start_value": start_slot["value"],
                "end_value": end_slot["value"],
                "unit": profile["metric"]["unit"],
                "atomic_ids": [item["atomic_id"] for item in phase_atomic],
                "source_refs": list(
                    dict.fromkeys(
                        ref
                        for item in phase_atomic
                        for ref in item["source_refs"]
                    )
                ),
            }
        )
        start = index
    return phases


def _numeric_fact(
    profile: Mapping[str, Any],
    *,
    metric: str,
    value: int | float | None,
    unit: str,
    direction: str,
    formula: str,
    operands: list[dict[str, Any]],
    source_refs: list[str],
) -> dict[str, Any]:
    return {
        "fact_id": f"{profile['profile_id']}:fact:{metric}",
        "metric": metric,
        "value": _rounded(value) if value is not None else None,
        "unit": unit,
        "direction": direction,
        "formula": formula,
        "operands": operands,
        "rounding": {
            "decimal_places": ANALYSIS_RULE["rounding_decimal_places"],
            "mode": ANALYSIS_RULE["rounding_mode"],
        },
        "baseline_type": profile["baseline"]["type"],
        "statistical_population": profile["metric"]["statistical_population"],
        "source_refs": source_refs,
    }


def _window_ledger(
    profile: Mapping[str, Any],
    points: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if profile["quality"]["status"] != "complete":
        return {
            "status": "unavailable",
            "reason": "incomplete_slots",
            "facts": [],
            "threshold_slots": [],
        }
    by_kind = {point["kind"]: point for point in points}
    start = by_kind["start"]
    end = by_kind["end"]
    extreme = by_kind["extreme_minimum"]
    start_value = start["value"]
    end_value = end["value"]
    extreme_value = extreme["value"]
    loss = start_value - extreme_value
    rebound = end_value - extreme_value
    residual = start_value - end_value
    rebound_ratio = rebound / loss if loss > 0 else None
    point_refs = list(
        dict.fromkeys(
            [start["source_ref"], extreme["source_ref"], end["source_ref"]]
        )
    )
    unit = profile["metric"]["unit"]
    facts = [
        _numeric_fact(
            profile,
            metric="start_to_extreme_change",
            value=extreme_value - start_value,
            unit=unit,
            direction="decrease" if extreme_value < start_value else "unchanged",
            formula="extreme_value - start_value",
            operands=[
                {"name": "extreme_value", "value": extreme_value, "unit": unit},
                {"name": "start_value", "value": start_value, "unit": unit},
            ],
            source_refs=[start["source_ref"], extreme["source_ref"]],
        ),
        _numeric_fact(
            profile,
            metric="loss_magnitude",
            value=loss,
            unit=unit,
            direction="below_window_start" if loss > 0 else "none",
            formula="start_value - extreme_value",
            operands=[
                {"name": "start_value", "value": start_value, "unit": unit},
                {"name": "extreme_value", "value": extreme_value, "unit": unit},
            ],
            source_refs=[start["source_ref"], extreme["source_ref"]],
        ),
        _numeric_fact(
            profile,
            metric="extreme_to_end_rebound",
            value=rebound,
            unit=unit,
            direction="increase_from_window_extreme" if rebound > 0 else "none",
            formula="end_value - extreme_value",
            operands=[
                {"name": "end_value", "value": end_value, "unit": unit},
                {"name": "extreme_value", "value": extreme_value, "unit": unit},
            ],
            source_refs=[extreme["source_ref"], end["source_ref"]],
        ),
        _numeric_fact(
            profile,
            metric="end_residual_from_start",
            value=residual,
            unit=unit,
            direction="below_window_start" if residual > 0 else "at_or_above_window_start",
            formula="start_value - end_value",
            operands=[
                {"name": "start_value", "value": start_value, "unit": unit},
                {"name": "end_value", "value": end_value, "unit": unit},
            ],
            source_refs=[start["source_ref"], end["source_ref"]],
        ),
        _numeric_fact(
            profile,
            metric="window_rebound_ratio",
            value=rebound_ratio,
            unit="ratio",
            direction="toward_window_start_reference",
            formula="(end_value - extreme_value) / (start_value - extreme_value)",
            operands=[
                {"name": "end_value", "value": end_value, "unit": unit},
                {"name": "extreme_value", "value": extreme_value, "unit": unit},
                {"name": "start_value", "value": start_value, "unit": unit},
            ],
            source_refs=point_refs,
        ),
    ]
    if unit == "count":
        values = [slot["value"] for slot in profile["slots"]]
        fixed = profile["metric"]["denominator"]["value"]
        fixed_gap = sum(max(fixed - value, 0) for value in values)
        start_gap = sum(max(start_value - value, 0) for value in values)
        all_refs = [slot["source_ref"] for slot in profile["slots"]]
        facts.extend(
            [
                _numeric_fact(
                    profile,
                    metric="fixed_cohort_visibility_gap_integral",
                    value=fixed_gap,
                    unit="prefix_vp_slot",
                    direction="deficit_from_fixed_cohort",
                    formula="sum(max(fixed_cohort_value - slot_value, 0))",
                    operands=[
                        {"name": "fixed_cohort_value", "value": fixed, "unit": "count"},
                        {"name": "observed_slot_count", "value": len(values), "unit": "observed_slot"},
                    ],
                    source_refs=all_refs,
                ),
                _numeric_fact(
                    profile,
                    metric="window_start_visibility_gap_integral",
                    value=start_gap,
                    unit="prefix_vp_slot",
                    direction="deficit_from_window_start",
                    formula="sum(max(window_start_value - slot_value, 0))",
                    operands=[
                        {"name": "window_start_value", "value": start_value, "unit": "count"},
                        {"name": "observed_slot_count", "value": len(values), "unit": "observed_slot"},
                    ],
                    source_refs=all_refs,
                ),
            ]
        )
    normalized = [
        _ratio_value(slot["value"], profile["metric"])
        for slot in profile["slots"]
    ]
    threshold_slots = []
    for threshold in ANALYSIS_RULE["threshold_visible_ratios"]:
        indices = [
            index for index, value in enumerate(normalized) if value < threshold
        ]
        threshold_slots.append(
            {
                "threshold_id": (
                    f"{ANALYSIS_RULE['rule_version']}:visible_below:"
                    f"{str(threshold).replace('.', '_')}"
                ),
                "threshold_visible_ratio": threshold,
                "observed_slot_count": len(indices),
                "slot_indices": indices,
                "unit": "observed_slot",
                "slot_seconds": profile["time_grid"]["slot_seconds"],
                "continuous_duration_claimed": False,
            }
        )
    return {
        "status": "complete",
        "reason": None,
        "facts": facts,
        "threshold_slots": threshold_slots,
    }


def analyze_trend_profile_v1(profile: Mapping[str, Any]) -> dict[str, Any]:
    """在 S1 身份上生成 S2 关键点、原子状态、阶段和窗口账本。"""
    if not isinstance(profile, Mapping):
        _fail("invalid_type", "$", "TrendProfile 必须是对象")
    if profile.get("schema_version") != PROFILE_SCHEMA_VERSION:
        _fail("unsupported_schema", "schema_version", "TrendProfile schema 不兼容")
    if profile.get("profile_state") not in {
        "identity_quality_baseline_complete",
        "analysis_complete",
        "analysis_insufficient_data",
    }:
        _fail("invalid_profile_state", "profile_state", "TrendProfile 状态不可分析")
    identity_material = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "snapshot": profile.get("snapshot"),
        "metric": profile.get("metric"),
        "time_grid": profile.get("time_grid"),
        "baseline": profile.get("baseline"),
        "slots": profile.get("slots"),
    }
    expected_digest = _digest(identity_material)
    if (
        profile.get("input_digest") != expected_digest
        or profile.get("profile_id")
        != f"trend_profile_v1_{expected_digest[:32]}"
    ):
        _fail(
            "profile_identity_conflict",
            "profile_id",
            "TrendProfile 内容与冻结身份不一致",
        )
    expected_quality = _quality(profile["slots"])
    if profile.get("quality") != expected_quality:
        _fail(
            "profile_quality_conflict",
            "quality",
            "TrendProfile 质量不能由冻结槽状态重算",
        )
    if profile.get("analysis", {}).get("status") in {"complete", "insufficient_data"}:
        analysis = profile["analysis"]
        analysis_without_id = {
            key: value for key, value in analysis.items() if key != "analysis_id"
        }
        expected_analysis_id = (
            f"trend_analysis_s2_{_digest({'profile_id': profile['profile_id'], 'analysis': analysis_without_id})[:32]}"
        )
        if analysis.get("analysis_id") != expected_analysis_id:
            _fail(
                "analysis_identity_conflict",
                "analysis.analysis_id",
                "分析内容与 analysis_id 不一致",
            )
        return deepcopy(dict(profile))

    result = deepcopy(dict(profile))
    quality_status = result["quality"]["status"]
    if quality_status != "complete":
        atomic_states = _atomic_states(result)
        analysis_without_id = {
            "status": "insufficient_data",
            "algorithm_version": ANALYSIS_ALGORITHM_VERSION,
            "rule": deepcopy(ANALYSIS_RULE),
            "pattern": {
                "status": "insufficient_data",
                "label": None,
                "features": None,
                "warnings": ["do_not_bridge_non_observed_slots"],
            },
            "key_points": [],
            "atomic_states": atomic_states,
            "phases": [],
            "derived_facts": [],
            "window_ledger": {
                "status": "unavailable",
                "reason": "incomplete_slots",
                "facts": [],
                "threshold_slots": [],
            },
            "evidence_refs": [
                ref for item in atomic_states for ref in item["source_refs"]
            ],
            "limitations": [
                "non_observed_slots_prevent_cross_gap_analysis",
                "window_outside_state_unknown",
            ],
        }
        analysis_without_id["evidence_refs"] = list(
            dict.fromkeys(analysis_without_id["evidence_refs"])
        )
        analysis_id = (
            f"trend_analysis_s2_{_digest({'profile_id': result['profile_id'], 'analysis': analysis_without_id})[:32]}"
        )
        result["analysis"] = {"analysis_id": analysis_id, **analysis_without_id}
        result["profile_state"] = "analysis_insufficient_data"
        return result

    normalized_values = [
        _ratio_value(slot["value"], result["metric"])
        for slot in result["slots"]
    ]
    features = _pattern_features(result, normalized_values)
    pattern = _classify_pattern(result, features)
    pattern["features"] = features
    points = _key_points(result)
    atomic_states = _atomic_states(result, pattern=pattern)
    phases = _phases(result, atomic_states)
    ledger = _window_ledger(result, points)
    analysis_without_id = {
        "status": "complete",
        "algorithm_version": ANALYSIS_ALGORITHM_VERSION,
        "rule": deepcopy(ANALYSIS_RULE),
        "pattern": pattern,
        "key_points": points,
        "atomic_states": atomic_states,
        "phases": phases,
        "derived_facts": ledger["facts"],
        "window_ledger": ledger,
        "evidence_refs": list(
            dict.fromkeys(slot["source_ref"] for slot in result["slots"])
        ),
        "limitations": [
            "window_start_is_observation_reference_not_normal_baseline",
            "window_rebound_is_not_network_or_service_recovery",
            "window_outside_state_unknown",
            "pattern_label_is_optional_summary",
        ],
    }
    analysis_id = (
        f"trend_analysis_s2_{_digest({'profile_id': result['profile_id'], 'analysis': analysis_without_id})[:32]}"
    )
    result["analysis"] = {"analysis_id": analysis_id, **analysis_without_id}
    result["profile_state"] = "analysis_complete"
    return result
