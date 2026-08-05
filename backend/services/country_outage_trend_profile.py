"""RRC25 国家中断趋势画像的确定性身份、质量与基线编译器。

S1 只建立 TrendProfile 地基。关键点、原子状态、阶段和窗口账本由后续阶段
在同一 profile_id 上补齐；本模块不读取数据库、网络、模型或窗口外数据。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


INPUT_SCHEMA_VERSION = "country_outage_trend_profile_input_v1"
PROFILE_SCHEMA_VERSION = "country_outage_trend_profile_v1"
ALGORITHM_VERSION = "country_outage_trend_foundation_s1_v1"
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
