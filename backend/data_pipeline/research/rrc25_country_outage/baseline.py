"""RRC25 国家中断研究的稳健数值基线纯函数。

基线从冻结研究窗口起点开始，先使用连续六小时五分钟观测；若相对 MAD
超过阈值，则按 profile 的固定步长向前扩展，但不会跨过冻结的候选排除边界，
也不会跳过缺测槽。该边界只是用户提供的最早可能前兆边界，不是已确认的
episode onset，也不授权因果结论。达到最大时长仍不稳定时返回
``baseline_unresolved``，调用方必须停止 episode 定论。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import statistics
from typing import Iterable, Mapping, Optional, Sequence, Tuple


UTC = timezone.utc
_OBSERVED_STATES = frozenset(("observed", "observed_zero"))


class BaselineInputError(ValueError):
    """基线观测或冻结参数无法按既定口径安全处理。"""


@dataclass(frozen=True)
class BaselineObservation:
    sample_id: str
    snapshot_id: str
    slot_start_utc: str
    slot_end_exclusive_utc: str
    continuity_state: str
    value: Optional[float]
    value_state: str
    missing_reason: Optional[str]


@dataclass(frozen=True)
class NumericBaselineResult:
    baseline_id: str
    resolution_state: str
    unresolved_reason: Optional[str]
    candidate_start_utc: str
    actual_end_exclusive_utc: Optional[str]
    duration_seconds: int
    extension_count: int
    observation_count: int
    median: Optional[float]
    mad: Optional[float]
    relative_mad: Optional[float]
    normal_band_lower: Optional[float]
    normal_band_upper: Optional[float]
    supporting_sample_ids: Tuple[str, ...]
    exclusion_boundary_at_utc: str
    exclusion_boundary_role: str
    exclusion_boundary_confirmation_state: str
    exclusion_boundary_causal_claim_allowed: bool

    @property
    def resolved(self) -> bool:
        return self.resolution_state == "resolved"


def _utc(value: object, field: str) -> Tuple[str, datetime]:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BaselineInputError(f"{field} 必须是 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise BaselineInputError(f"{field} 不是合法 UTC 时间") from error
    if parsed.utcoffset() != timedelta(0):
        raise BaselineInputError(f"{field} 必须是 UTC")
    parsed = parsed.astimezone(UTC)
    if parsed.microsecond:
        normalized = parsed.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    else:
        normalized = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if normalized != value:
        raise BaselineInputError(f"{field} 不是规范 UTC 表示")
    return normalized, parsed


def _positive_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BaselineInputError(f"{field} 必须是正整数")
    return value


def _ratio(value: object, field: str, *, allow_zero: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BaselineInputError(f"{field} 必须是有限数")
    result = float(value)
    lower_ok = result >= 0 if allow_zero else result > 0
    if not math.isfinite(result) or not lower_ok or result > 1:
        raise BaselineInputError(f"{field} 必须位于合法比例范围")
    return result


def _nonnegative_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BaselineInputError(f"{field} 必须是非负有限数")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise BaselineInputError(f"{field} 必须是非负有限数")
    return result


def _stable_id(identity: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(identity), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "baseline_v1_" + hashlib.sha256(encoded).hexdigest()[:24]


def _unresolved(
    *,
    reason: str,
    candidate_start: str,
    observations: Sequence[BaselineObservation],
    duration_seconds: int,
    extension_count: int,
    exclusion_boundary: Mapping[str, object],
) -> NumericBaselineResult:
    sample_ids = tuple(item.sample_id for item in observations)
    identity = {
        "schema": "rrc25_numeric_baseline_v1",
        "candidate_start_utc": candidate_start,
        "resolution_state": "baseline_unresolved",
        "unresolved_reason": reason,
        "supporting_sample_ids": list(sample_ids),
        "exclusion_boundary": dict(exclusion_boundary),
    }
    return NumericBaselineResult(
        baseline_id=_stable_id(identity),
        resolution_state="baseline_unresolved",
        unresolved_reason=reason,
        candidate_start_utc=candidate_start,
        actual_end_exclusive_utc=(
            observations[-1].slot_end_exclusive_utc if observations else None
        ),
        duration_seconds=duration_seconds,
        extension_count=extension_count,
        observation_count=len(observations),
        median=None,
        mad=None,
        relative_mad=None,
        normal_band_lower=None,
        normal_band_upper=None,
        supporting_sample_ids=sample_ids,
        exclusion_boundary_at_utc=str(exclusion_boundary["at_utc"]),
        exclusion_boundary_role=str(exclusion_boundary["role"]),
        exclusion_boundary_confirmation_state=str(
            exclusion_boundary["confirmation_state"]
        ),
        exclusion_boundary_causal_claim_allowed=False,
    )


def _validated_observations(
    observations: Iterable[BaselineObservation], *, candidate_start: datetime
) -> Tuple[BaselineObservation, ...]:
    if isinstance(observations, (str, bytes, Mapping)):
        raise BaselineInputError("observations 必须是 BaselineObservation 流")
    try:
        values = tuple(observations)
    except TypeError as error:
        raise BaselineInputError("observations 不可迭代") from error
    if any(not isinstance(item, BaselineObservation) for item in values):
        raise BaselineInputError("observations 含非 BaselineObservation")
    seen_samples = set()
    seen_snapshots = set()
    expected_start = candidate_start
    normalized = []
    for index, item in enumerate(values):
        _start_text, start = _utc(item.slot_start_utc, f"observations[{index}].start")
        _end_text, end = _utc(
            item.slot_end_exclusive_utc, f"observations[{index}].end"
        )
        if start != expected_start or end - start != timedelta(minutes=5):
            raise BaselineInputError("基线观测必须从候选起点起连续覆盖五分钟槽")
        expected_start = end
        if not isinstance(item.sample_id, str) or not item.sample_id:
            raise BaselineInputError("sample_id 不能为空")
        if not isinstance(item.snapshot_id, str) or not item.snapshot_id:
            raise BaselineInputError("snapshot_id 不能为空")
        if item.sample_id in seen_samples or item.snapshot_id in seen_snapshots:
            raise BaselineInputError("基线 sample/snapshot 身份不得重复")
        seen_samples.add(item.sample_id)
        seen_snapshots.add(item.snapshot_id)
        if item.continuity_state not in {"continuous", "unknown_after_gap"}:
            raise BaselineInputError("continuity_state 非法")
        if item.value_state in _OBSERVED_STATES:
            if isinstance(item.value, bool) or not isinstance(item.value, (int, float)):
                raise BaselineInputError("已观测基线值必须是非负有限数")
            value = float(item.value)
            if not math.isfinite(value) or value < 0:
                raise BaselineInputError("已观测基线值必须是非负有限数")
            if item.value_state == "observed_zero" and value != 0:
                raise BaselineInputError("observed_zero 必须对应数值零")
            if item.value_state == "observed" and value == 0:
                raise BaselineInputError("数值零必须使用 observed_zero")
            if item.missing_reason is not None:
                raise BaselineInputError("已观测值不得携带 missing_reason")
        else:
            if item.value is not None or not isinstance(item.missing_reason, str) or not item.missing_reason:
                raise BaselineInputError("未知基线值必须保留 null 与 missing_reason")
        normalized.append(item)
    return tuple(normalized)


def derive_numeric_baseline(
    observations: Iterable[BaselineObservation],
    *,
    candidate_start_utc: str,
    numeric_policy: Mapping[str, object],
    normal_band_policy: Mapping[str, object],
) -> NumericBaselineResult:
    """按冻结六小时/扩展/MAD 规则计算数值基线。"""

    candidate_text, candidate_start = _utc(
        candidate_start_utc, "candidate_start_utc"
    )
    if not isinstance(numeric_policy, Mapping) or not isinstance(
        normal_band_policy, Mapping
    ):
        raise BaselineInputError("基线 policy 必须是对象")
    if numeric_policy.get("statistic") != "median" or numeric_policy.get(
        "dispersion"
    ) != "median_absolute_deviation":
        raise BaselineInputError("仅支持冻结的 median/MAD 基线")
    if numeric_policy.get("extension_direction") != "forward":
        raise BaselineInputError("基线只能按冻结规则向前扩展")
    if numeric_policy.get("unstable_exhausted_state") != "incomplete":
        raise BaselineInputError("不稳定耗尽状态必须是 incomplete")
    if numeric_policy.get("stop_before_exclusion_boundary") is not True:
        raise BaselineInputError("基线必须在候选排除边界前停止")
    initial = _positive_integer(
        numeric_policy.get("initial_duration_seconds"), "initial_duration_seconds"
    )
    step = _positive_integer(
        numeric_policy.get("extension_step_seconds"), "extension_step_seconds"
    )
    maximum = _positive_integer(
        numeric_policy.get("max_duration_seconds"), "max_duration_seconds"
    )
    if any(value % 300 for value in (initial, step, maximum)):
        raise BaselineInputError("基线时长必须是五分钟整数倍")
    if initial > maximum:
        raise BaselineInputError("初始基线时长不得超过最大时长")
    max_relative_mad = _ratio(
        numeric_policy.get("max_relative_mad"), "max_relative_mad"
    )
    mad_multiplier = _nonnegative_number(
        normal_band_policy.get("mad_multiplier"), "normal_band.mad_multiplier"
    )
    absolute_floor_ratio = _ratio(
        normal_band_policy.get("absolute_floor_ratio"),
        "normal_band.absolute_floor_ratio",
    )
    if normal_band_policy.get("method") != "median_plus_minus_max_scaled_mad_and_absolute_floor":
        raise BaselineInputError("normal_band.method 不受支持")

    boundary_value = numeric_policy.get("exclusion_boundary")
    if not isinstance(boundary_value, Mapping):
        raise BaselineInputError("exclusion_boundary 必须是显式对象")
    required_boundary_keys = {
        "at_utc",
        "role",
        "confirmation_state",
        "causal_claim_allowed",
    }
    if set(boundary_value) != required_boundary_keys:
        raise BaselineInputError("exclusion_boundary 字段必须完整且不得扩展")
    boundary_text, boundary_at = _utc(
        boundary_value["at_utc"], "exclusion_boundary.at_utc"
    )
    if (
        boundary_value["role"]
        != "user_supplied_earliest_possible_precursor_boundary"
        or boundary_value["confirmation_state"] != "candidate_not_confirmed"
        or boundary_value["causal_claim_allowed"] is not False
    ):
        raise BaselineInputError("候选排除边界不得冒充确认 onset 或授权因果结论")
    boundary_offset_seconds = int((boundary_at - candidate_start).total_seconds())
    if boundary_offset_seconds <= 0:
        raise BaselineInputError("候选排除边界必须晚于基线候选起点")
    if boundary_offset_seconds % 300:
        raise BaselineInputError("候选排除边界必须与五分钟槽对齐")
    if boundary_offset_seconds < initial:
        raise BaselineInputError("候选排除边界必须容纳完整初始基线窗口")
    exclusion_boundary = {
        "at_utc": boundary_text,
        "role": boundary_value["role"],
        "confirmation_state": boundary_value["confirmation_state"],
        "causal_claim_allowed": False,
    }
    values = _validated_observations(observations, candidate_start=candidate_start)

    duration = initial
    extension_count = 0
    while True:
        required = duration // 300
        selected = values[: min(required, len(values))]
        desired_end = candidate_start + timedelta(seconds=duration)
        if desired_end > boundary_at:
            return _unresolved(
                reason="candidate_exclusion_boundary_before_stable_extension",
                candidate_start=candidate_text,
                observations=tuple(
                    item
                    for item in values
                    if _utc(item.slot_end_exclusive_utc, "slot.end")[1]
                    <= boundary_at
                ),
                duration_seconds=boundary_offset_seconds,
                extension_count=extension_count,
                exclusion_boundary=exclusion_boundary,
            )
        if len(selected) < required:
            return _unresolved(
                reason="insufficient_contiguous_observations",
                candidate_start=candidate_text,
                observations=selected,
                duration_seconds=len(selected) * 300,
                extension_count=extension_count,
                exclusion_boundary=exclusion_boundary,
            )
        unknown = next(
            (
                item
                for item in selected
                if item.continuity_state != "continuous"
                or item.value_state not in _OBSERVED_STATES
            ),
            None,
        )
        if unknown is not None:
            return _unresolved(
                reason=unknown.missing_reason or "baseline_state_discontinuous",
                candidate_start=candidate_text,
                observations=selected,
                duration_seconds=duration,
                extension_count=extension_count,
                exclusion_boundary=exclusion_boundary,
            )
        numeric_values = [float(item.value) for item in selected if item.value is not None]
        median = float(statistics.median(numeric_values))
        mad = float(statistics.median(abs(value - median) for value in numeric_values))
        if median <= 0:
            return _unresolved(
                reason="baseline_median_nonpositive",
                candidate_start=candidate_text,
                observations=selected,
                duration_seconds=duration,
                extension_count=extension_count,
                exclusion_boundary=exclusion_boundary,
            )
        relative_mad = mad / median
        if relative_mad <= max_relative_mad:
            width = max(mad_multiplier * mad, absolute_floor_ratio * median)
            sample_ids = tuple(item.sample_id for item in selected)
            end_text = selected[-1].slot_end_exclusive_utc
            identity = {
                "schema": "rrc25_numeric_baseline_v1",
                "candidate_start_utc": candidate_text,
                "actual_end_exclusive_utc": end_text,
                "median": median,
                "mad": mad,
                "supporting_sample_ids": list(sample_ids),
                "exclusion_boundary": exclusion_boundary,
            }
            return NumericBaselineResult(
                baseline_id=_stable_id(identity),
                resolution_state="resolved",
                unresolved_reason=None,
                candidate_start_utc=candidate_text,
                actual_end_exclusive_utc=end_text,
                duration_seconds=duration,
                extension_count=extension_count,
                observation_count=len(selected),
                median=median,
                mad=mad,
                relative_mad=relative_mad,
                normal_band_lower=max(0.0, median - width),
                normal_band_upper=median + width,
                supporting_sample_ids=sample_ids,
                exclusion_boundary_at_utc=boundary_text,
                exclusion_boundary_role=str(boundary_value["role"]),
                exclusion_boundary_confirmation_state=str(
                    boundary_value["confirmation_state"]
                ),
                exclusion_boundary_causal_claim_allowed=False,
            )
        if duration >= maximum:
            return _unresolved(
                reason="max_duration_still_unstable",
                candidate_start=candidate_text,
                observations=selected,
                duration_seconds=duration,
                extension_count=extension_count,
                exclusion_boundary=exclusion_boundary,
            )
        duration = min(duration + step, maximum)
        extension_count += 1


__all__ = (
    "BaselineInputError",
    "BaselineObservation",
    "NumericBaselineResult",
    "derive_numeric_baseline",
)
