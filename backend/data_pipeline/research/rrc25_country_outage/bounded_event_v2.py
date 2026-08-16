"""国家中断 Observation → Incident/Episode/Wave v2 的纯状态转换器。

本模块不读取文件、数据库或网络。输入必须是已经绑定同一 cohort 的不可变
五分钟状态观察；检测、峰值、低谷和恢复里程碑分别计算，禁止用一个时间字段
覆盖另一个时间字段。未知 Prefix×VP 状态不会被补成零，也不会触发恢复。
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Optional, Sequence


OBSERVATION_SCHEMA_VERSION = "country-outage-observation/v2"
INCIDENT_SCHEMA_VERSION = "country-outage-incident/v2"
EPISODE_SCHEMA_VERSION = "country-outage-episode/v2"
WAVE_SCHEMA_VERSION = "country-outage-wave/v2"
MILESTONE_SCHEMA_VERSION = "country-outage-milestone/v2"
MODEL_VERSION = "country_outage_event_model_v2"

_CONTINUITY_STATES = frozenset(("continuous", "unknown_after_gap"))
_DURATION_STATES = frozenset(("exact", "lower_bound", "interval", "unknown"))
_RECOVERY_STATES = frozenset(
    ("ongoing", "recovering", "partially_recovered", "fully_recovered", "unknown")
)
_SNAPSHOT_RE = re.compile(r"^snapshot_v2_[0-9a-f]{24}$")
_COHORT_RE = re.compile(r"^cohort_v2_[0-9a-f]{24}$")
_INCIDENT_RE = re.compile(r"^incident_v2_[0-9a-f]{24}$")


class BoundedEventModelError(ValueError):
    """Observation 或事件状态不满足 v2 的同快照合同。"""


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise BoundedEventModelError("事件模型包含不可规范序列化值") from error


def stable_id(prefix: str, identity: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        canonical_json(dict(identity)).encode("utf-8")
    ).hexdigest()
    return prefix + digest[:24]


def _utc(value: object, field: str) -> tuple[str, datetime]:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BoundedEventModelError(f"{field} 必须是 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise BoundedEventModelError(f"{field} 不是合法 UTC 时间") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed) or parsed.microsecond:
        raise BoundedEventModelError(f"{field} 必须是秒级 UTC")
    canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical != value:
        raise BoundedEventModelError(f"{field} 不是规范 UTC 时间")
    return canonical, parsed


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BoundedEventModelError(f"{field} 必须是非负整数")
    return value


def _ratio(value: object, field: str, *, allow_unknown: bool = False) -> Optional[float]:
    if value is None and allow_unknown:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BoundedEventModelError(f"{field} 必须是比例数值")
    numeric = float(value)
    if not math.isfinite(numeric) or not 0 <= numeric <= 1:
        raise BoundedEventModelError(f"{field} 必须位于 [0,1]")
    return numeric


def _sorted_asns(value: object, field: str) -> list[int]:
    if not isinstance(value, list):
        raise BoundedEventModelError(f"{field} 必须是 ASN 数组")
    if any(
        isinstance(asn, bool)
        or not isinstance(asn, int)
        or not 1 <= asn <= 4_294_967_295
        for asn in value
    ):
        raise BoundedEventModelError(f"{field} 包含非法 ASN")
    if value != sorted(set(value)):
        raise BoundedEventModelError(f"{field} 必须去重升序")
    return value


def validate_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    """校验并复制一条 v2 Observation。"""

    if not isinstance(value, Mapping):
        raise BoundedEventModelError("Observation 必须是对象")
    required = {
        "schema_version",
        "snapshot_id",
        "observed_at",
        "slot",
        "continuity_state",
        "cohort",
        "address_families",
        "dual_stack",
        "dynamic",
        "prefix_vp",
        "metrics",
        "update_counts",
        "state_result_ref",
    }
    if set(value) != required:
        raise BoundedEventModelError("Observation 顶层字段不闭合")
    if value.get("schema_version") != OBSERVATION_SCHEMA_VERSION:
        raise BoundedEventModelError("Observation schema_version 非法")
    snapshot_id = value.get("snapshot_id")
    if not isinstance(snapshot_id, str) or _SNAPSHOT_RE.fullmatch(snapshot_id) is None:
        raise BoundedEventModelError("snapshot_id 非法")
    observed_at, observed_dt = _utc(value.get("observed_at"), "observed_at")
    continuity = value.get("continuity_state")
    if continuity not in _CONTINUITY_STATES:
        raise BoundedEventModelError("continuity_state 非法")

    slot = value.get("slot")
    if not isinstance(slot, Mapping) or set(slot) != {
        "start_utc",
        "end_exclusive_utc",
        "boundary",
        "role",
    }:
        raise BoundedEventModelError("slot 字段不闭合")
    start_text, start_dt = _utc(slot.get("start_utc"), "slot.start_utc")
    end_text, end_dt = _utc(
        slot.get("end_exclusive_utc"), "slot.end_exclusive_utc"
    )
    if slot.get("boundary") != "[start,end)":
        raise BoundedEventModelError("slot.boundary 必须是 [start,end)")
    role = slot.get("role")
    if role not in {"window_start", "slot_end"}:
        raise BoundedEventModelError("slot.role 非法")
    if role == "window_start":
        if start_dt != end_dt or observed_dt != start_dt:
            raise BoundedEventModelError("窗口起点 Observation 必须是零宽状态边界")
    elif (end_dt - start_dt).total_seconds() != 300 or observed_dt != end_dt:
        raise BoundedEventModelError("槽末 Observation 必须绑定完整五分钟槽")

    cohort = value.get("cohort")
    if not isinstance(cohort, Mapping):
        raise BoundedEventModelError("cohort 必须是对象")
    cohort_id = cohort.get("cohort_id")
    if not isinstance(cohort_id, str) or _COHORT_RE.fullmatch(cohort_id) is None:
        raise BoundedEventModelError("cohort_id 非法")
    baseline_asn_count = _nonnegative_int(
        cohort.get("baseline_asn_count"), "cohort.baseline_asn_count"
    )
    baseline_prefix_vp_count = _nonnegative_int(
        cohort.get("baseline_prefix_vp_count"),
        "cohort.baseline_prefix_vp_count",
    )
    if not isinstance(cohort.get("mapping_version"), str) or not cohort[
        "mapping_version"
    ]:
        raise BoundedEventModelError("cohort.mapping_version 非法")
    if cohort.get("completeness_state") not in {
        "complete",
        "known_population_with_explicit_unknown_exclusions",
        "unknown",
    }:
        raise BoundedEventModelError("cohort.completeness_state 非法")

    families = value.get("address_families")
    if not isinstance(families, Mapping) or set(families) != {"ipv4", "ipv6"}:
        raise BoundedEventModelError("address_families 必须恰含 IPv4/IPv6")
    for family_name, family in families.items():
        if not isinstance(family, Mapping):
            raise BoundedEventModelError(f"{family_name} 必须是对象")
        if set(family) != {
            "baseline_origin_asns",
            "visible_origin_asns",
            "visible_prefixes_ref",
            "classifications",
        }:
            raise BoundedEventModelError(f"{family_name} 字段不闭合")
        baseline = _sorted_asns(
            family.get("baseline_origin_asns"),
            f"{family_name}.baseline_origin_asns",
        )
        visible = _sorted_asns(
            family.get("visible_origin_asns"),
            f"{family_name}.visible_origin_asns",
        )
        if not set(visible).issubset(set(baseline)):
            raise BoundedEventModelError(
                f"{family_name} visible_origin_asns 必须属于基线人口"
            )
        prefix_ref = family.get("visible_prefixes_ref")
        if (
            not isinstance(prefix_ref, Mapping)
            or set(prefix_ref) != {"path", "snapshot_id", "afi"}
            or prefix_ref.get("path") != "route-states.jsonl.gz"
            or prefix_ref.get("snapshot_id") != snapshot_id
            or prefix_ref.get("afi") != family_name
        ):
            raise BoundedEventModelError(f"{family_name} visible_prefixes_ref 非法")
        classifications = family.get("classifications")
        if not isinstance(classifications, Mapping) or set(classifications) != {
            "fully_visible",
            "partially_visible",
            "fully_invisible",
            "unknown",
        }:
            raise BoundedEventModelError(f"{family_name} 分类字段不闭合")
        classified = []
        for class_name, members in classifications.items():
            values = _sorted_asns(
                members, f"{family_name}.classifications.{class_name}"
            )
            classified.extend(values)
        if sorted(classified) != baseline or len(classified) != len(set(classified)):
            raise BoundedEventModelError(f"{family_name} 分类没有互斥覆盖基线人口")

    dual = value.get("dual_stack")
    if not isinstance(dual, Mapping):
        raise BoundedEventModelError("dual_stack 必须是对象")
    baseline_asns = _sorted_asns(
        dual.get("baseline_origin_asns"), "dual_stack.baseline_origin_asns"
    )
    visible_asns = _sorted_asns(
        dual.get("visible_origin_asns"), "dual_stack.visible_origin_asns"
    )
    affected_asns = _sorted_asns(
        dual.get("affected_asns"), "dual_stack.affected_asns"
    )
    if len(baseline_asns) != baseline_asn_count:
        raise BoundedEventModelError("双栈基线 ASN 数与 cohort 不一致")
    if not set(visible_asns).issubset(set(baseline_asns)):
        raise BoundedEventModelError("双栈可见 ASN 必须属于基线")
    if not set(affected_asns).issubset(set(baseline_asns)):
        raise BoundedEventModelError("受损 ASN 必须属于基线")
    dual_classes = dual.get("classifications")
    if not isinstance(dual_classes, Mapping) or set(dual_classes) != {
        "fully_visible",
        "partially_visible",
        "fully_invisible",
        "ipv4_invisible_ipv6_visible",
        "unknown",
    }:
        raise BoundedEventModelError("双栈分类字段不闭合")
    primary_members = []
    for class_name in (
        "fully_visible",
        "partially_visible",
        "fully_invisible",
        "unknown",
    ):
        primary_members.extend(
            _sorted_asns(
                dual_classes[class_name],
                f"dual_stack.classifications.{class_name}",
            )
        )
    if (
        sorted(primary_members) != baseline_asns
        or len(primary_members) != len(set(primary_members))
    ):
        raise BoundedEventModelError("双栈主分类没有互斥覆盖基线人口")
    label_members = _sorted_asns(
        dual_classes["ipv4_invisible_ipv6_visible"],
        "dual_stack.classifications.ipv4_invisible_ipv6_visible",
    )
    if not set(label_members).issubset(
        set(dual_classes["partially_visible"])
    ):
        raise BoundedEventModelError("IPv4 不可见/IPv6 可见标签必须属于部分可见")

    dynamic = value.get("dynamic")
    if not isinstance(dynamic, Mapping) or set(dynamic) != {
        "denominator_policy",
        "ipv4_visible_origin_asns",
        "ipv6_visible_origin_asns",
        "dual_stack_visible_origin_asns",
        "visible_prefixes_ref",
    }:
        raise BoundedEventModelError("dynamic 字段不闭合")
    if dynamic.get("denominator_policy") != "reported_separately":
        raise BoundedEventModelError("dynamic.denominator_policy 非法")
    dynamic_v4 = _sorted_asns(
        dynamic.get("ipv4_visible_origin_asns"),
        "dynamic.ipv4_visible_origin_asns",
    )
    dynamic_v6 = _sorted_asns(
        dynamic.get("ipv6_visible_origin_asns"),
        "dynamic.ipv6_visible_origin_asns",
    )
    dynamic_union = _sorted_asns(
        dynamic.get("dual_stack_visible_origin_asns"),
        "dynamic.dual_stack_visible_origin_asns",
    )
    if dynamic_union != sorted(set(dynamic_v4) | set(dynamic_v6)):
        raise BoundedEventModelError("dynamic 双栈并集不一致")
    dynamic_ref = dynamic.get("visible_prefixes_ref")
    if (
        not isinstance(dynamic_ref, Mapping)
        or set(dynamic_ref) != {"path", "snapshot_id"}
        or dynamic_ref.get("path") != "route-states.jsonl.gz"
        or dynamic_ref.get("snapshot_id") != snapshot_id
    ):
        raise BoundedEventModelError("dynamic.visible_prefixes_ref 非法")

    prefix_vp = value.get("prefix_vp")
    if not isinstance(prefix_vp, Mapping):
        raise BoundedEventModelError("prefix_vp 必须是对象")
    base_pv = _nonnegative_int(
        prefix_vp.get("baseline_count"), "prefix_vp.baseline_count"
    )
    if base_pv != baseline_prefix_vp_count:
        raise BoundedEventModelError("Prefix×VP 基线分母与 cohort 不一致")
    pv_visible = prefix_vp.get("visible_count")
    pv_lost = prefix_vp.get("lost_count")
    pv_ratio = _ratio(
        prefix_vp.get("visible_ratio"),
        "prefix_vp.visible_ratio",
        allow_unknown=True,
    )
    if continuity == "continuous":
        visible_count = _nonnegative_int(pv_visible, "prefix_vp.visible_count")
        lost_count = _nonnegative_int(pv_lost, "prefix_vp.lost_count")
        if visible_count + lost_count != base_pv:
            raise BoundedEventModelError("Prefix×VP 分子与损失数未闭合")
        expected = visible_count / base_pv if base_pv else 1.0
        if pv_ratio is None or not math.isclose(
            pv_ratio, expected, rel_tol=0, abs_tol=1e-15
        ):
            raise BoundedEventModelError("Prefix×VP 比例与同快照分子分母不一致")
    elif any(item is not None for item in (pv_visible, pv_lost, pv_ratio)):
        raise BoundedEventModelError("输入缺口后的 Prefix×VP 数值必须为 unknown")

    metrics = value.get("metrics")
    if not isinstance(metrics, Mapping):
        raise BoundedEventModelError("metrics 必须是对象")
    affected_count = metrics.get("affected_asn_count")
    affected_ratio = _ratio(
        metrics.get("affected_asn_ratio"),
        "metrics.affected_asn_ratio",
        allow_unknown=True,
    )
    visible_count = metrics.get("visible_origin_asn_count")
    visible_ratio = _ratio(
        metrics.get("visible_origin_asn_ratio"),
        "metrics.visible_origin_asn_ratio",
        allow_unknown=True,
    )
    if continuity == "continuous":
        affected_count = _nonnegative_int(
            affected_count, "metrics.affected_asn_count"
        )
        visible_count = _nonnegative_int(
            visible_count, "metrics.visible_origin_asn_count"
        )
        expected_affected = (
            affected_count / baseline_asn_count if baseline_asn_count else 0.0
        )
        expected_visible = (
            visible_count / baseline_asn_count if baseline_asn_count else 1.0
        )
        if affected_count != len(affected_asns):
            raise BoundedEventModelError("受损 ASN 数与成员集合不一致")
        if affected_ratio is None or not math.isclose(
            affected_ratio, expected_affected, rel_tol=0, abs_tol=1e-15
        ):
            raise BoundedEventModelError("受损 ASN 比例没有绑定同快照分子分母")
        if visible_ratio is None or not math.isclose(
            visible_ratio, expected_visible, rel_tol=0, abs_tol=1e-15
        ):
            raise BoundedEventModelError("可见 ASN 比例没有绑定同快照分子分母")
    elif any(
        item is not None
        for item in (affected_count, affected_ratio, visible_count, visible_ratio)
    ):
        raise BoundedEventModelError("输入缺口后的 ASN 指标必须为 unknown")

    update_counts = value.get("update_counts")
    if not isinstance(update_counts, Mapping) or set(update_counts) != {
        "announce",
        "withdraw",
        "retained_announce",
        "retained_withdraw",
    }:
        raise BoundedEventModelError("update_counts 字段不闭合")
    for field in update_counts:
        _nonnegative_int(update_counts[field], f"update_counts.{field}")

    state_ref = value.get("state_result_ref")
    if not isinstance(state_ref, Mapping) or set(state_ref) != {
        "path",
        "format",
        "snapshot_id",
    }:
        raise BoundedEventModelError("state_result_ref 字段不闭合")
    if (
        not isinstance(state_ref.get("path"), str)
        or not state_ref["path"]
        or state_ref.get("format") != "route_state_snapshots_jsonl_gzip"
        or state_ref.get("snapshot_id") != snapshot_id
    ):
        raise BoundedEventModelError("state_result_ref 非法")
    return json.loads(canonical_json(dict(value)))


def _milestone(
    observation: Mapping[str, Any],
    *,
    metric: str,
    metric_value: Any,
    algorithm_version: str,
    precision: str = "five_minute_state",
) -> dict[str, Any]:
    return {
        "schema_version": MILESTONE_SCHEMA_VERSION,
        "at": observation["observed_at"],
        "snapshot_id": observation["snapshot_id"],
        "algorithm_version": algorithm_version,
        "metric": metric,
        "metric_value": metric_value,
        "time_precision": precision,
        "unknown_reason": None,
    }


def _severity(observation: Mapping[str, Any]) -> Optional[float]:
    affected = observation["metrics"]["affected_asn_ratio"]
    prefix_ratio = observation["prefix_vp"]["visible_ratio"]
    if affected is None or prefix_ratio is None:
        return None
    return max(float(affected), 1.0 - float(prefix_ratio))


def _in_band(value: Optional[float], band: Optional[Mapping[str, Any]]) -> bool:
    if value is None or not isinstance(band, Mapping):
        return False
    lower = _ratio(band.get("lower"), "normal_band.lower")
    upper = _ratio(band.get("upper"), "normal_band.upper")
    assert lower is not None and upper is not None
    if lower > upper:
        raise BoundedEventModelError("normal band 下界大于上界")
    return lower <= value <= upper


def _episode_waves(
    observations: Sequence[Mapping[str, Any]],
    *,
    episode_id: str,
    significance_floor: float,
    confirm_slots: int,
) -> list[dict[str, Any]]:
    if not observations:
        return []
    severities = [_severity(row) for row in observations]
    if any(value is None for value in severities):
        return []
    numeric = [float(value) for value in severities if value is not None]
    starts = [0]
    peak_index = 0
    best_recovery_index: Optional[int] = None
    for index in range(1, len(numeric)):
        if numeric[index] >= numeric[peak_index]:
            peak_index = index
            best_recovery_index = None
            continue
        if best_recovery_index is None or numeric[index] < numeric[best_recovery_index]:
            best_recovery_index = index
        if best_recovery_index is None or index + confirm_slots > len(numeric):
            continue
        threshold = numeric[best_recovery_index] + significance_floor
        run = numeric[index : index + confirm_slots]
        if (
            index > best_recovery_index
            and all(value >= threshold for value in run)
        ):
            starts.append(index)
            peak_index = index
            best_recovery_index = None
    waves = []
    for ordinal, start_index in enumerate(starts, start=1):
        end_index = (
            starts[ordinal] - 1 if ordinal < len(starts) else len(observations) - 1
        )
        segment = observations[start_index : end_index + 1]
        peak_offset = max(
            range(len(segment)),
            key=lambda offset: (_severity(segment[offset]) or 0.0, -offset),
        )
        wave_id = stable_id(
            "wave_v2_",
            {
                "episode_id": episode_id,
                "ordinal": ordinal,
                "start_snapshot_id": segment[0]["snapshot_id"],
            },
        )
        waves.append(
            {
                "schema_version": WAVE_SCHEMA_VERSION,
                "wave_id": wave_id,
                "episode_id": episode_id,
                "ordinal": ordinal,
                "onset_at": segment[0]["observed_at"],
                "onset_snapshot_id": segment[0]["snapshot_id"],
                "peak_at": segment[peak_offset]["observed_at"],
                "peak_snapshot_id": segment[peak_offset]["snapshot_id"],
                "observation_end_at": segment[-1]["observed_at"],
                "causal_relation": "not_assessed",
                "algorithm_version": "country_outage_wave_v2",
            }
        )
    return waves


def derive_incident_episode_v2(
    observations: Iterable[Mapping[str, Any]],
    *,
    legacy_ref: str,
    detected_at: str,
    source: str,
    country_code: str,
    collector_id: str,
    source_context: Mapping[str, Any],
    normal_band: Optional[Mapping[str, Mapping[str, Any]]],
    damaged_ratio_threshold: float = 0.03,
    detection_confirm_slots: int = 2,
    recovery_ratio_threshold: float = 0.99,
    recovery_confirm_slots: int = 6,
    wave_significance_floor: float = 0.005,
    wave_confirm_slots: int = 2,
) -> dict[str, Any]:
    """从同一 cohort 的 Observation 序列派生 Incident/Episode/Wave v2。"""

    if isinstance(observations, (str, bytes, Mapping)):
        raise BoundedEventModelError("observations 必须是对象序列")
    rows = [validate_observation(row) for row in observations]
    if not rows:
        raise BoundedEventModelError("至少需要一个 Observation")
    times = [_utc(row["observed_at"], "observed_at")[1] for row in rows]
    if times != sorted(times) or len(times) != len(set(times)):
        raise BoundedEventModelError("Observation 时间必须严格递增")
    cohort_ids = {row["cohort"]["cohort_id"] for row in rows}
    if len(cohort_ids) != 1:
        raise BoundedEventModelError("一个事件推导只能使用同一 cohort")
    if rows[0]["slot"]["role"] != "window_start":
        raise BoundedEventModelError("第一条 Observation 必须是窗口起点")
    if any(row["slot"]["role"] != "slot_end" for row in rows[1:]):
        raise BoundedEventModelError("其余 Observation 必须是槽末状态")
    for previous, current in zip(times, times[1:]):
        if (current - previous).total_seconds() != 300:
            raise BoundedEventModelError("Observation 序列存在五分钟缺槽")
    if not isinstance(legacy_ref, str) or not legacy_ref:
        raise BoundedEventModelError("legacy_ref 不能为空")
    detected_text, _detected = _utc(detected_at, "detected_at")
    if not isinstance(source_context, Mapping) or not source_context:
        raise BoundedEventModelError("source_context 不能为空")
    if country_code != "IR" or collector_id != "rrc25":
        raise BoundedEventModelError("当前 v2 入口只接受固定 IR/RRC25")
    if (
        isinstance(damaged_ratio_threshold, bool)
        or not 0 < float(damaged_ratio_threshold) < 1
        or detection_confirm_slots < 1
        or recovery_confirm_slots < 1
        or wave_confirm_slots < 1
    ):
        raise BoundedEventModelError("事件算法参数非法")

    incident_id = stable_id(
        "incident_v2_",
        {
            "legacy_ref": legacy_ref,
            "source": source,
            "country_code": country_code,
            "collector_id": collector_id,
            "detected_at": detected_text,
        },
    )
    if _INCIDENT_RE.fullmatch(incident_id) is None:  # pragma: no cover
        raise BoundedEventModelError("incident_id 生成失败")

    anomaly = [
        row["continuity_state"] == "continuous"
        and row["metrics"]["affected_asn_ratio"] is not None
        and float(row["metrics"]["affected_asn_ratio"])
        > float(damaged_ratio_threshold)
        for row in rows
    ]
    partial_ok = [
        row["continuity_state"] == "continuous"
        and row["metrics"]["visible_origin_asn_ratio"] is not None
        and row["prefix_vp"]["visible_ratio"] is not None
        and float(row["metrics"]["visible_origin_asn_ratio"])
        >= float(recovery_ratio_threshold)
        and float(row["prefix_vp"]["visible_ratio"])
        >= float(recovery_ratio_threshold)
        for row in rows
    ]
    full_ok = []
    for row in rows:
        visible = row["metrics"]["visible_origin_asn_ratio"]
        prefix_vp = row["prefix_vp"]["visible_ratio"]
        full_ok.append(
            row["continuity_state"] == "continuous"
            and _in_band(
                None if visible is None else float(visible),
                None if normal_band is None else normal_band.get("visible_origin_asn_ratio"),
            )
            and _in_band(
                None if prefix_vp is None else float(prefix_vp),
                None if normal_band is None else normal_band.get("visible_prefix_vp_ratio"),
            )
        )

    episode_ranges: list[tuple[int, int, Optional[int], Optional[int]]] = []
    active_start: Optional[int] = None
    partial_index: Optional[int] = None
    full_index: Optional[int] = None
    index = 0
    while index < len(rows):
        if active_start is None:
            if (
                index + detection_confirm_slots <= len(rows)
                and all(anomaly[index : index + detection_confirm_slots])
            ):
                active_start = index
                partial_index = None
                full_index = None
            else:
                index += 1
                continue
        if (
            partial_index is None
            and index + recovery_confirm_slots <= len(rows)
            and all(partial_ok[index : index + recovery_confirm_slots])
        ):
            partial_index = index
        if (
            index + recovery_confirm_slots <= len(rows)
            and all(full_ok[index : index + recovery_confirm_slots])
        ):
            full_index = index
            close_index = index + recovery_confirm_slots - 1
            episode_ranges.append(
                (active_start, close_index, partial_index, full_index)
            )
            active_start = None
            partial_index = None
            full_index = None
            index = close_index + 1
            continue
        index += 1
    if active_start is not None:
        episode_ranges.append(
            (active_start, len(rows) - 1, partial_index, None)
        )

    episodes = []
    waves = []
    for ordinal, (start, end, partial, full) in enumerate(episode_ranges, start=1):
        segment = rows[start : end + 1]
        peak_index = max(
            range(start, end + 1),
            key=lambda offset: (
                float(rows[offset]["metrics"]["affected_asn_ratio"] or -1),
                -offset,
            ),
        )
        trough_index = min(
            range(start, end + 1),
            key=lambda offset: (
                float(rows[offset]["prefix_vp"]["visible_ratio"] or 2),
                offset,
            ),
        )
        episode_id = stable_id(
            "episode_v2_",
            {
                "incident_id": incident_id,
                "ordinal": ordinal,
                "onset_snapshot_id": rows[start]["snapshot_id"],
            },
        )
        onset_precision = (
            "left_censored_at_window_start"
            if start == 0 and anomaly[0]
            else "five_minute_state"
        )
        onset = _milestone(
            rows[start],
            metric="damaged_asn_ratio",
            metric_value=rows[start]["metrics"]["affected_asn_ratio"],
            algorithm_version="country_outage_episode_v2",
            precision=onset_precision,
        )
        peak = _milestone(
            rows[peak_index],
            metric="damaged_asn_ratio",
            metric_value=rows[peak_index]["metrics"]["affected_asn_ratio"],
            algorithm_version="country_outage_peak_v2",
        )
        trough = _milestone(
            rows[trough_index],
            metric="visible_prefix_vp_ratio",
            metric_value=rows[trough_index]["prefix_vp"]["visible_ratio"],
            algorithm_version="country_outage_trough_v2",
        )
        partial_milestone = (
            None
            if partial is None
            else _milestone(
                rows[partial],
                metric="visible_origin_asn_ratio_and_visible_prefix_vp_ratio",
                metric_value={
                    "visible_origin_asn_ratio": rows[partial]["metrics"][
                        "visible_origin_asn_ratio"
                    ],
                    "visible_prefix_vp_ratio": rows[partial]["prefix_vp"][
                        "visible_ratio"
                    ],
                },
                algorithm_version="country_outage_partial_recovery_v2",
            )
        )
        full_milestone = (
            None
            if full is None
            else _milestone(
                rows[full],
                metric="both_state_metrics_within_frozen_normal_band",
                metric_value={
                    "visible_origin_asn_ratio": rows[full]["metrics"][
                        "visible_origin_asn_ratio"
                    ],
                    "visible_prefix_vp_ratio": rows[full]["prefix_vp"][
                        "visible_ratio"
                    ],
                },
                algorithm_version="country_outage_full_recovery_v2",
            )
        )
        observation_end = rows[end]["observed_at"]
        onset_dt = _utc(onset["at"], "onset_at")[1]
        end_dt = _utc(observation_end, "observation_end_at")[1]
        if full_milestone is not None:
            duration_state = "exact"
            recovery_state = "fully_recovered"
            duration_seconds = int(
                (
                    _utc(full_milestone["at"], "full_recovery_at")[1]
                    - onset_dt
                ).total_seconds()
            )
        else:
            duration_state = (
                "interval" if onset_precision.startswith("left_censored") else "lower_bound"
            )
            if any(
                row["prefix_vp"]["visible_ratio"] is None
                or row["metrics"]["visible_origin_asn_ratio"] is None
                for row in segment
            ):
                recovery_state = "unknown"
            elif partial_milestone is not None:
                recovery_state = "partially_recovered"
            else:
                trough_offset = trough_index - start
                latest_severity = _severity(segment[-1])
                trough_severity = _severity(segment[trough_offset])
                recovery_state = (
                    "recovering"
                    if latest_severity is not None
                    and trough_severity is not None
                    and latest_severity < trough_severity
                    else "ongoing"
                )
            duration_seconds = max(0, int((end_dt - onset_dt).total_seconds()))
        episode = {
            "schema_version": EPISODE_SCHEMA_VERSION,
            "episode_id": episode_id,
            "incident_id": incident_id,
            "ordinal": ordinal,
            "source": source,
            "country_code": country_code,
            "collector_id": collector_id,
            "cohort_id": rows[start]["cohort"]["cohort_id"],
            "detected_at": detected_text,
            "onset_at": onset["at"],
            "peak_at": peak["at"],
            "trough_at": trough["at"],
            "partial_recovery_at": (
                None if partial_milestone is None else partial_milestone["at"]
            ),
            "full_recovery_at": (
                None if full_milestone is None else full_milestone["at"]
            ),
            "observation_end_at": observation_end,
            "duration_state": duration_state,
            "duration_seconds": duration_seconds,
            "recovery_state": recovery_state,
            "peak_snapshot_id": peak["snapshot_id"],
            "trough_snapshot_id": trough["snapshot_id"],
            "algorithm_version": MODEL_VERSION,
            "milestones": {
                "onset": onset,
                "peak": peak,
                "trough": trough,
                "partial_recovery": partial_milestone,
                "full_recovery": full_milestone,
            },
            "observation_snapshot_ids": [
                row["snapshot_id"] for row in segment
            ],
            "source_context": json.loads(canonical_json(dict(source_context))),
            "legacy_ref": legacy_ref,
        }
        if episode["duration_state"] not in _DURATION_STATES:
            raise BoundedEventModelError("duration_state 非法")
        if episode["recovery_state"] not in _RECOVERY_STATES:
            raise BoundedEventModelError("recovery_state 非法")
        episode_waves = _episode_waves(
            segment,
            episode_id=episode_id,
            significance_floor=float(wave_significance_floor),
            confirm_slots=wave_confirm_slots,
        )
        episode["wave_ids"] = [row["wave_id"] for row in episode_waves]
        waves.extend(episode_waves)
        episodes.append(episode)

    if episodes:
        last_episode = episodes[-1]
        peak_episode = max(
            episodes,
            key=lambda row: (
                float(row["milestones"]["peak"]["metric_value"]),
                -times.index(
                    _utc(row["peak_at"], "episode.peak_at")[1]
                ),
            ),
        )
        trough_episode = min(
            episodes,
            key=lambda row: (
                float(row["milestones"]["trough"]["metric_value"]),
                times.index(
                    _utc(row["trough_at"], "episode.trough_at")[1]
                ),
            ),
        )
        if any(row["duration_state"] == "lower_bound" for row in episodes):
            incident_duration_state = "lower_bound"
        elif any(row["duration_state"] == "interval" for row in episodes):
            incident_duration_state = "interval"
        elif all(row["duration_state"] == "exact" for row in episodes):
            incident_duration_state = "exact"
        else:
            incident_duration_state = "unknown"
        incident_recovery_state = last_episode["recovery_state"]
        incident_fields = {
            "onset_at": episodes[0]["onset_at"],
            "peak_at": peak_episode["peak_at"],
            "trough_at": trough_episode["trough_at"],
            "partial_recovery_at": last_episode["partial_recovery_at"],
            "full_recovery_at": last_episode["full_recovery_at"],
            "peak_snapshot_id": peak_episode["peak_snapshot_id"],
            "trough_snapshot_id": trough_episode["trough_snapshot_id"],
        }
    else:
        incident_duration_state = "unknown"
        incident_recovery_state = "unknown"
        incident_fields = {
            "onset_at": None,
            "peak_at": None,
            "trough_at": None,
            "partial_recovery_at": None,
            "full_recovery_at": None,
            "peak_snapshot_id": None,
            "trough_snapshot_id": None,
        }
    incident = {
        "schema_version": INCIDENT_SCHEMA_VERSION,
        "incident_id": incident_id,
        "source": source,
        "country_code": country_code,
        "collector_id": collector_id,
        "detected_at": detected_text,
        **incident_fields,
        "observation_end_at": rows[-1]["observed_at"],
        "duration_state": incident_duration_state,
        "recovery_state": incident_recovery_state,
        "cohort_id": rows[0]["cohort"]["cohort_id"],
        "algorithm_version": MODEL_VERSION,
        "legacy_ref": legacy_ref,
        "episode_ids": [row["episode_id"] for row in episodes],
        "observation_snapshot_ids": [row["snapshot_id"] for row in rows],
        "source_context": json.loads(canonical_json(dict(source_context))),
        "derived_at": rows[-1]["observed_at"],
    }
    return {
        "incident": incident,
        "episodes": episodes,
        "waves": waves,
    }


def render_event_info_zh(incident: Mapping[str, Any]) -> str:
    """只从结构化 Incident 生成兼容中文摘要。"""

    if (
        not isinstance(incident, Mapping)
        or incident.get("schema_version") != INCIDENT_SCHEMA_VERSION
    ):
        raise BoundedEventModelError("incident 不是 v2 结构化事件")
    onset = incident.get("onset_at") or "未知"
    peak = incident.get("peak_at") or "未知"
    recovery = incident.get("recovery_state") or "unknown"
    return (
        f"RRC25 观测到伊朗国家路由状态异常；检测记录时间"
        f"{incident.get('detected_at')}，状态 onset={onset}，peak={peak}，"
        f"观察截止={incident.get('observation_end_at')}，恢复状态={recovery}。"
    )


__all__ = (
    "BoundedEventModelError",
    "EPISODE_SCHEMA_VERSION",
    "INCIDENT_SCHEMA_VERSION",
    "MILESTONE_SCHEMA_VERSION",
    "MODEL_VERSION",
    "OBSERVATION_SCHEMA_VERSION",
    "WAVE_SCHEMA_VERSION",
    "canonical_json",
    "derive_incident_episode_v2",
    "render_event_info_zh",
    "stable_id",
    "validate_observation",
)
