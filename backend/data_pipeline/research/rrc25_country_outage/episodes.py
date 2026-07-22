"""国家中断 episode、wave 与恢复状态的纯函数识别。

本模块只消费已经合同化且按五分钟排序的 ``country-outage-sample``
字典。它不读取文件、数据库或网络，也不会把未知值转换为零。所有判定阈值
均由调用方显式传入的研究 profile 算法段和运行期数值基线提供。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


_OBSERVED_STATES = frozenset(("observed", "observed_zero"))


class EpisodeInputError(ValueError):
    """输入样本或显式算法参数不满足 episode 识别前置条件。"""


@dataclass(frozen=True)
class DurationEstimate:
    duration_state: str
    seconds: Optional[int]
    minimum_seconds: Optional[int]
    maximum_seconds: Optional[int]
    measured_to: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "duration_state": self.duration_state,
            "seconds": self.seconds,
            "minimum_seconds": self.minimum_seconds,
            "maximum_seconds": self.maximum_seconds,
            "measured_to": self.measured_to,
        }


@dataclass(frozen=True)
class RecoveryCandidate:
    kind: str
    start_at: str
    supporting_sample_ids: Tuple[str, ...]
    confirmed: bool
    reason_code: str


@dataclass(frozen=True)
class EpisodeSplitEvidence:
    decision: str
    left_sample_id: str
    right_sample_id: str
    full_recovery_confirmed: bool
    reason_code: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "left_sample_id": self.left_sample_id,
            "right_sample_id": self.right_sample_id,
            "full_recovery_confirmed": self.full_recovery_confirmed,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class WaveSplitEvidence:
    previous_trough_sample_id: str
    rebound_sample_id: str
    new_decline_sample_id: str
    rebound_amplitude: float
    new_decline_amplitude: float
    significance_threshold: float

    def to_dict(self) -> Dict[str, Any]:
        def amplitude(value: float, sample_id: str) -> Dict[str, Any]:
            return {
                "value": value,
                "unit": "ipv4_equivalent_address",
                "sample_id": sample_id,
            }

        return {
            "previous_trough_sample_id": self.previous_trough_sample_id,
            "rebound_sample_id": self.rebound_sample_id,
            "new_decline_sample_id": self.new_decline_sample_id,
            "rebound_amplitude": amplitude(
                self.rebound_amplitude, self.rebound_sample_id
            ),
            "new_decline_amplitude": amplitude(
                self.new_decline_amplitude, self.new_decline_sample_id
            ),
            "significance_threshold": amplitude(
                self.significance_threshold, self.rebound_sample_id
            ),
            "full_recovery_between_waves": False,
        }


@dataclass(frozen=True)
class WaveDetection:
    wave_id: str
    episode_id: str
    run_id: str
    ordinal: int
    onset_at: str
    detected_at: str
    trough_at: str
    rebound_at: Optional[str]
    relation_to_previous_wave: str
    causal_relation: str
    split_evidence: Optional[WaveSplitEvidence]
    supporting_sample_ids: Tuple[str, ...]

    def to_contract_record(self) -> Dict[str, Any]:
        """转换为 ``country-outage-wave/v1`` 可校验记录。"""

        return {
            "schema_version": "country-outage-wave/v1",
            "wave_id": self.wave_id,
            "episode_id": self.episode_id,
            "run_id": self.run_id,
            "ordinal": self.ordinal,
            "onset_at": self.onset_at,
            "detected_at": self.detected_at,
            "trough_at": self.trough_at,
            "rebound_at": self.rebound_at,
            "relation_to_previous_wave": self.relation_to_previous_wave,
            "causal_relation": self.causal_relation,
            "split_evidence": (
                None if self.split_evidence is None else self.split_evidence.to_dict()
            ),
            "supporting_sample_ids": list(self.supporting_sample_ids),
        }


@dataclass(frozen=True)
class EpisodeDetection:
    episode_id: str
    run_id: str
    collector_id: str
    country_code: str
    cohort_view: str
    algorithm_version: str
    onset_at: str
    detected_at: str
    peak_at: str
    trough_at: str
    partial_recovery_at: Optional[str]
    full_recovery_at: Optional[str]
    observation_end_at: str
    recovery_state: str
    duration: DurationEstimate
    supporting_sample_ids: Tuple[str, ...]
    wave_ids: Tuple[str, ...]
    split_evidence: Tuple[EpisodeSplitEvidence, ...]
    recovery_candidates: Tuple[RecoveryCandidate, ...]

    def to_contract_record(
        self, incident_mappings: Sequence[Mapping[str, Any]]
    ) -> Dict[str, Any]:
        """转换为 episode 合同记录。

        旧 Incident 映射不是状态样本能够推导的事实，因此必须由调用方显式
        提供，避免本识别器虚构关联或因果关系。
        """

        if not incident_mappings:
            raise EpisodeInputError("episode 合同要求显式提供至少一个旧 Incident 映射")
        return {
            "schema_version": "country-outage-episode/v1",
            "episode_id": self.episode_id,
            "run_id": self.run_id,
            "collector_id": self.collector_id,
            "country_code": self.country_code,
            "cohort_view": self.cohort_view,
            "algorithm_version": self.algorithm_version,
            "onset_at": self.onset_at,
            "detected_at": self.detected_at,
            "peak_at": self.peak_at,
            "trough_at": self.trough_at,
            "partial_recovery_at": self.partial_recovery_at,
            "full_recovery_at": self.full_recovery_at,
            "observation_end_at": self.observation_end_at,
            "recovery_state": self.recovery_state,
            "duration": self.duration.to_dict(),
            "supporting_sample_ids": list(self.supporting_sample_ids),
            "wave_ids": list(self.wave_ids),
            "split_evidence": [item.to_dict() for item in self.split_evidence],
            "incident_mappings": [dict(item) for item in incident_mappings],
        }


@dataclass(frozen=True)
class DetectionResult:
    episodes: Tuple[EpisodeDetection, ...]
    waves: Tuple[WaveDetection, ...]
    ignored_unknown_sample_ids: Tuple[str, ...]
    normal_band_lower: float
    normal_band_upper: float
    wave_significance_threshold: float
    episode_count_state: str


@dataclass(frozen=True)
class _Point:
    sample_id: str
    run_id: str
    collector_id: str
    country_code: str
    cohort_view: str
    start: datetime
    end: datetime
    start_text: str
    end_text: str
    continuity_state: str
    visible: Optional[float]
    damaged_ratio: Optional[float]


@dataclass(frozen=True)
class _Parameters:
    episode_version: str
    visible_ratio_below: float
    damaged_ratio_above: float
    episode_confirm_slots: int
    partial_ratio_at_least: float
    partial_confirm_slots: int
    full_confirm_slots: int
    baseline_median: float
    baseline_mad: float
    normal_band_lower: float
    normal_band_upper: float
    wave_threshold: float


class _WaveBuilder:
    def __init__(
        self,
        ordinal: int,
        onset: _Point,
        detected: _Point,
        initial_points: Sequence[_Point],
        split_evidence: Optional[WaveSplitEvidence],
    ) -> None:
        self.ordinal = ordinal
        self.onset = onset
        self.detected = detected
        self.points = list(initial_points)
        visible = [point for point in initial_points if point.visible is not None]
        self.trough = min(visible, key=lambda point: point.visible) if visible else onset
        self.rebound_peak: Optional[_Point] = None
        self.decline_run = []  # type: list[_Point]
        self.split_evidence = split_evidence


class _EpisodeBuilder:
    def __init__(
        self,
        points: Sequence[_Point],
        parameters: _Parameters,
        prior_split: Optional[EpisodeSplitEvidence],
    ) -> None:
        self.parameters = parameters
        self.onset = points[0]
        self.detected = points[-1]
        self.episode_id = _stable_id(
            "episode_v1_",
            {
                "run_id": self.onset.run_id,
                "collector_id": self.onset.collector_id,
                "country_code": self.onset.country_code,
                "cohort_view": self.onset.cohort_view,
                "onset_sample_id": self.onset.sample_id,
                "algorithm_version": parameters.episode_version,
            },
        )
        self.points = list(points)
        visible = [point for point in points if point.visible is not None]
        damaged = [point for point in points if point.damaged_ratio is not None]
        self.trough = min(visible, key=lambda point: point.visible) if visible else self.onset
        self.peak = max(damaged, key=lambda point: point.damaged_ratio) if damaged else self.onset
        self.wave_builders = [
            _WaveBuilder(1, self.onset, self.detected, points, None)
        ]
        self.episode_splits = []  # type: list[EpisodeSplitEvidence]
        if prior_split is not None:
            self.episode_splits.append(prior_split)
        self.recovery_candidates = []  # type: list[RecoveryCandidate]
        self.partial_run = []  # type: list[_Point]
        self.full_run = []  # type: list[_Point]
        self.partial_recovery_at: Optional[str] = None
        self.partial_confirmed = False
        self.ever_recovery_signal = False
        self.wave_detection_blocked = False


def detect_country_outage_episodes(
    samples: Sequence[Mapping[str, Any]],
    *,
    episode: Mapping[str, Any],
    recovery: Mapping[str, Any],
    wave: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> DetectionResult:
    """识别国家中断 episode、wave 与恢复状态。

    ``episode``、``recovery``、``wave`` 对应研究 profile 的三个算法段；
    ``baseline`` 必须显式包含运行期 ``median``、``mad`` 以及 profile 中的
    ``normal_band``（含 ``mad_multiplier`` 与 ``absolute_floor_ratio``）。
    """

    parameters = _parameters(episode, recovery, wave, baseline)
    points = _points(samples)
    if not points:
        return DetectionResult(
            episodes=(),
            waves=(),
            ignored_unknown_sample_ids=(),
            normal_band_lower=parameters.normal_band_lower,
            normal_band_upper=parameters.normal_band_upper,
            wave_significance_threshold=parameters.wave_threshold,
            episode_count_state="exact",
        )

    observation_end = points[-1].end_text
    episodes = []  # type: list[EpisodeDetection]
    waves_out = []  # type: list[WaveDetection]
    ignored_unknown = []  # type: list[str]
    pending_anomaly = []  # type: list[_Point]
    active: Optional[_EpisodeBuilder] = None
    previous_point: Optional[_Point] = None
    last_full_confirmation: Optional[_Point] = None
    last_unknown_split_left: Optional[_Point] = None
    hard_gap_seen = False

    for point_index, point in enumerate(points):
        adjacent = previous_point is None or previous_point.end == point.start
        hard_gap = point.continuity_state != "continuous" or not adjacent

        if hard_gap:
            hard_gap_seen = True
            ignored_unknown.append(point.sample_id)
            pending_anomaly = []
            if active is not None:
                interval = _post_gap_duration_interval(
                    points,
                    gap_index=point_index,
                    active=active,
                )
                _flush_unconfirmed_candidates(active, "continuity_became_unknown")
                if interval is not None:
                    minimum_end, maximum_end, recovery_points = interval
                    active.recovery_candidates.append(
                        RecoveryCandidate(
                            kind="full",
                            start_at=recovery_points[0].start_text,
                            supporting_sample_ids=tuple(
                                item.sample_id for item in recovery_points
                            ),
                            confirmed=False,
                            reason_code="continuity_gap_bounds_recovery",
                        )
                    )
                episode_result, wave_results = _finalize_episode(
                    active,
                    observation_end=observation_end,
                    recovery_state="unknown",
                    full_recovery_start=None,
                    full_recovery_confirmation=None,
                    duration_state="interval" if interval is not None else "unknown",
                    duration_interval_bounds=(
                        None
                        if interval is None
                        else (interval[0], interval[1])
                    ),
                )
                episodes.append(episode_result)
                waves_out.extend(wave_results)
                last_unknown_split_left = previous_point or active.points[-1]
                active = None
            last_full_confirmation = None
            previous_point = point
            continue

        anomaly = _anomaly_state(point, parameters)
        if anomaly is None:
            ignored_unknown.append(point.sample_id)

        if active is None:
            if anomaly is True:
                if pending_anomaly and pending_anomaly[-1].end != point.start:
                    pending_anomaly = []
                pending_anomaly.append(point)
                if len(pending_anomaly) >= parameters.episode_confirm_slots:
                    onset_points = pending_anomaly[-parameters.episode_confirm_slots :]
                    prior_split = None
                    if last_full_confirmation is not None:
                        prior_split = EpisodeSplitEvidence(
                            decision="new_episode",
                            left_sample_id=last_full_confirmation.sample_id,
                            right_sample_id=onset_points[0].sample_id,
                            full_recovery_confirmed=True,
                            reason_code="full_recovery_six_slots",
                        )
                    elif last_unknown_split_left is not None:
                        # 连续性缺口不能证明完全恢复，也不能证明后续异常是同一
                        # episode 或新 episode。合同以 continuity_unknown 保存
                        # 这条不确定分割证据，DetectionResult 同时把 episode 数
                        # 标为 unknown，调用方不得据此发布确定事件数。
                        prior_split = EpisodeSplitEvidence(
                            decision="same_episode_new_wave",
                            left_sample_id=last_unknown_split_left.sample_id,
                            right_sample_id=onset_points[0].sample_id,
                            full_recovery_confirmed=False,
                            reason_code="continuity_unknown",
                        )
                    active = _EpisodeBuilder(onset_points, parameters, prior_split)
                    last_unknown_split_left = None
                    pending_anomaly = []
            else:
                pending_anomaly = []
            previous_point = point
            continue

        active.points.append(point)
        if point.visible is not None and (
            active.trough.visible is None or point.visible < active.trough.visible
        ):
            active.trough = point
        if point.damaged_ratio is not None and (
            active.peak.damaged_ratio is None
            or point.damaged_ratio > active.peak.damaged_ratio
        ):
            active.peak = point

        full_recovery_start, full_recovery_confirmation = _update_recovery(
            active, point
        )
        if full_recovery_start is not None:
            episode_result, wave_results = _finalize_episode(
                active,
                observation_end=observation_end,
                recovery_state="fully_recovered",
                full_recovery_start=full_recovery_start,
                full_recovery_confirmation=full_recovery_confirmation,
                duration_state="exact",
            )
            episodes.append(episode_result)
            waves_out.extend(wave_results)
            last_full_confirmation = full_recovery_confirmation
            active = None
            pending_anomaly = []
            previous_point = point
            continue

        _update_waves(active, point)
        previous_point = point

    if active is not None:
        _flush_unconfirmed_candidates(active, "observation_ended_before_confirmation")
        last_point = active.points[-1]
        current_partial_confirmed = (
            len(active.partial_run) >= active.parameters.partial_confirm_slots
        )
        current_recovery_signal = (
            last_point.visible is not None
            and active.trough.visible is not None
            and last_point.visible > active.trough.visible
        )
        recovery_state = (
            "partially_recovered"
            if current_partial_confirmed
            else "recovering"
            if current_recovery_signal
            else "ongoing"
        )
        episode_result, wave_results = _finalize_episode(
            active,
            observation_end=observation_end,
            recovery_state=recovery_state,
            full_recovery_start=None,
            full_recovery_confirmation=None,
            duration_state="lower_bound",
        )
        episodes.append(episode_result)
        waves_out.extend(wave_results)

    return DetectionResult(
        episodes=tuple(episodes),
        waves=tuple(waves_out),
        ignored_unknown_sample_ids=tuple(_unique(ignored_unknown)),
        normal_band_lower=parameters.normal_band_lower,
        normal_band_upper=parameters.normal_band_upper,
        wave_significance_threshold=parameters.wave_threshold,
        episode_count_state="unknown" if hard_gap_seen else "exact",
    )


def _parameters(
    episode: Mapping[str, Any],
    recovery: Mapping[str, Any],
    wave: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> _Parameters:
    try:
        episode_version = str(episode["version"])
        combine_rule = episode["combine_rule"]
        visible_ratio_below = _finite_number(
            episode["ipv4_visible_ratio_below"], "ipv4_visible_ratio_below"
        )
        damaged_ratio_above = _finite_number(
            episode["damaged_as_ratio_above"], "damaged_as_ratio_above"
        )
        episode_confirm_slots = _positive_int(
            episode["confirm_consecutive_slots"], "confirm_consecutive_slots"
        )
        partial_ratio_at_least = _finite_number(
            recovery["partial_visible_ratio_at_least"],
            "partial_visible_ratio_at_least",
        )
        partial_confirm_slots = _positive_int(
            recovery["partial_confirm_consecutive_slots"],
            "partial_confirm_consecutive_slots",
        )
        full_confirm_slots = _positive_int(
            recovery["full_confirm_consecutive_slots"],
            "full_confirm_consecutive_slots",
        )
        baseline_median = _finite_number(baseline["median"], "baseline.median")
        baseline_mad = _finite_number(baseline["mad"], "baseline.mad")
        normal_band = baseline["normal_band"]
        normal_mad_multiplier = _finite_number(
            normal_band["mad_multiplier"], "normal_band.mad_multiplier"
        )
        absolute_floor_ratio = _finite_number(
            normal_band["absolute_floor_ratio"],
            "normal_band.absolute_floor_ratio",
        )
        wave_floor_ratio = _finite_number(
            wave["baseline_ratio_floor"], "wave.baseline_ratio_floor"
        )
        wave_mad_multiplier = _finite_number(
            wave["mad_multiplier"], "wave.mad_multiplier"
        )
    except KeyError as error:
        raise EpisodeInputError("缺少显式算法或基线参数: {}".format(error.args[0]))
    except TypeError as error:
        raise EpisodeInputError("normal_band 必须是对象") from error

    if combine_rule != "any":
        raise EpisodeInputError("当前 episode 算法仅接受显式 combine_rule=any")
    if not episode_version:
        raise EpisodeInputError("episode.version 不能为空")
    if baseline_median <= 0:
        raise EpisodeInputError("baseline.median 必须大于 0")
    if baseline_mad < 0:
        raise EpisodeInputError("baseline.mad 不能小于 0")
    if episode_confirm_slots < 2:
        raise EpisodeInputError("country_outage_episode_v1 至少需要两个确认槽")
    if full_confirm_slots != 6:
        raise EpisodeInputError("country_outage_episode_v1 的完全恢复确认窗固定为六槽")
    for name, value in (
        ("ipv4_visible_ratio_below", visible_ratio_below),
        ("damaged_as_ratio_above", damaged_ratio_above),
        ("partial_visible_ratio_at_least", partial_ratio_at_least),
    ):
        if value < 0 or value > 1:
            raise EpisodeInputError("{} 必须在 [0,1] 内".format(name))
    for name, value in (
        ("normal_band.mad_multiplier", normal_mad_multiplier),
        ("normal_band.absolute_floor_ratio", absolute_floor_ratio),
        ("wave.baseline_ratio_floor", wave_floor_ratio),
        ("wave.mad_multiplier", wave_mad_multiplier),
    ):
        if value < 0:
            raise EpisodeInputError("{} 不能小于 0".format(name))

    band = max(
        normal_mad_multiplier * baseline_mad,
        absolute_floor_ratio * baseline_median,
    )
    if baseline_median - band < partial_ratio_at_least * baseline_median:
        raise EpisodeInputError("完全恢复正常带下界不得低于部分恢复阈值")
    wave_threshold = max(
        wave_floor_ratio * baseline_median,
        wave_mad_multiplier * baseline_mad,
    )
    return _Parameters(
        episode_version=episode_version,
        visible_ratio_below=visible_ratio_below,
        damaged_ratio_above=damaged_ratio_above,
        episode_confirm_slots=episode_confirm_slots,
        partial_ratio_at_least=partial_ratio_at_least,
        partial_confirm_slots=partial_confirm_slots,
        full_confirm_slots=full_confirm_slots,
        baseline_median=baseline_median,
        baseline_mad=baseline_mad,
        normal_band_lower=baseline_median - band,
        normal_band_upper=baseline_median + band,
        wave_threshold=wave_threshold,
    )


def _points(samples: Sequence[Mapping[str, Any]]) -> Tuple[_Point, ...]:
    points = []  # type: list[_Point]
    identity: Optional[Tuple[str, str, str, str]] = None
    seen_ids = set()
    previous_start: Optional[datetime] = None
    for index, sample in enumerate(samples):
        try:
            sample_id = str(sample["sample_id"])
            sample_identity = (
                str(sample["run_id"]),
                str(sample["collector_id"]),
                str(sample["country_code"]),
                str(sample["cohort_view"]),
            )
            slot = sample["slot"]
            start = _parse_datetime(slot["start"], "slot.start")
            end = _parse_datetime(slot["end"], "slot.end")
            continuity = str(sample["continuity_state"])
            metrics = sample["metrics"]
            visible = _observed_value(
                metrics["visible_ipv4_address_union"],
                "metrics.visible_ipv4_address_union",
            )
            damaged = _observed_value(
                metrics["damaged_asn_ratio"], "metrics.damaged_asn_ratio"
            )
        except KeyError as error:
            raise EpisodeInputError(
                "样本 {} 缺少合同字段 {}".format(index, error.args[0])
            )
        except TypeError as error:
            raise EpisodeInputError("样本 {} 的合同对象类型错误".format(index)) from error

        if identity is None:
            identity = sample_identity
        elif sample_identity != identity:
            raise EpisodeInputError("所有样本必须属于同一 run/collector/country/cohort")
        if sample_id in seen_ids:
            raise EpisodeInputError("sample_id 不得重复: {}".format(sample_id))
        seen_ids.add(sample_id)
        if end <= start:
            raise EpisodeInputError("样本槽 end 必须晚于 start: {}".format(sample_id))
        if previous_start is not None and start <= previous_start:
            raise EpisodeInputError("样本必须按 slot.start 严格升序排列")
        if continuity not in ("continuous", "unknown_after_gap"):
            raise EpisodeInputError("未知 continuity_state: {}".format(continuity))
        previous_start = start
        points.append(
            _Point(
                sample_id=sample_id,
                run_id=sample_identity[0],
                collector_id=sample_identity[1],
                country_code=sample_identity[2],
                cohort_view=sample_identity[3],
                start=start,
                end=end,
                start_text=_format_datetime(start),
                end_text=_format_datetime(end),
                continuity_state=continuity,
                visible=visible,
                damaged_ratio=damaged,
            )
        )
    return tuple(points)


def _observed_value(measure: Mapping[str, Any], field: str) -> Optional[float]:
    state = measure.get("value_state")
    if state not in _OBSERVED_STATES:
        return None
    value = measure.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EpisodeInputError("{} 的 observed value 必须是有限数值".format(field))
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise EpisodeInputError("{} 的 observed value 必须是非负有限数值".format(field))
    if state == "observed_zero" and number != 0:
        raise EpisodeInputError("{} 的 observed_zero 必须等于 0".format(field))
    return number


def _anomaly_state(point: _Point, parameters: _Parameters) -> Optional[bool]:
    visible_trigger = (
        point.visible is not None
        and point.visible / parameters.baseline_median
        < parameters.visible_ratio_below
    )
    damaged_trigger = (
        point.damaged_ratio is not None
        and point.damaged_ratio > parameters.damaged_ratio_above
    )
    if visible_trigger or damaged_trigger:
        return True
    if point.visible is not None and point.damaged_ratio is not None:
        return False
    return None


def _update_recovery(
    active: _EpisodeBuilder, point: _Point
) -> Tuple[Optional[_Point], Optional[_Point]]:
    parameters = active.parameters
    if point.visible is None:
        _reject_run(active, "partial", "unknown_metric_interrupted_candidate")
        _reject_run(active, "full", "unknown_metric_interrupted_candidate")
        active.wave_detection_blocked = True
        return None, None

    partial = (
        point.visible / parameters.baseline_median
        >= parameters.partial_ratio_at_least
    )
    # 完全恢复除了回到可见性正常带，还必须让 episode 的完整异常谓词为
    # false。damaged_asn_ratio 仍超阈值或未知时都不能关闭 episode。
    full = (
        parameters.normal_band_lower
        <= point.visible
        <= parameters.normal_band_upper
        and _anomaly_state(point, parameters) is False
    )
    if partial:
        active.ever_recovery_signal = True
    _advance_recovery_run(active, "partial", point, partial)
    _advance_recovery_run(active, "full", point, full)

    if (
        not active.partial_confirmed
        and len(active.partial_run) >= parameters.partial_confirm_slots
    ):
        confirmed = active.partial_run[: parameters.partial_confirm_slots]
        # 字段表达“首次确认时点”，不是恢复候选区间的起点。五分钟样本
        # 只能在确认槽结束边界证明连续确认窗已经满足。
        active.partial_recovery_at = confirmed[-1].end_text
        active.partial_confirmed = True
        active.recovery_candidates.append(
            RecoveryCandidate(
                kind="partial",
                start_at=confirmed[0].start_text,
                supporting_sample_ids=tuple(item.sample_id for item in confirmed),
                confirmed=True,
                reason_code="confirmed_consecutive_slots",
            )
        )

    if len(active.full_run) >= parameters.full_confirm_slots:
        confirmed = active.full_run[: parameters.full_confirm_slots]
        active.recovery_candidates.append(
            RecoveryCandidate(
                kind="full",
                start_at=confirmed[0].start_text,
                supporting_sample_ids=tuple(item.sample_id for item in confirmed),
                confirmed=True,
                reason_code="confirmed_consecutive_slots",
            )
        )
        if not active.partial_confirmed:
            # 正常带理论上通常落在 99% 阈值内，但配置允许两者不同；只有
            # partial 自己满足确认窗时才写 partial_recovery_at。
            active.partial_recovery_at = None
        return confirmed[0], confirmed[-1]
    return None, None


def _advance_recovery_run(
    active: _EpisodeBuilder, kind: str, point: _Point, condition: bool
) -> None:
    run = active.partial_run if kind == "partial" else active.full_run
    if condition:
        if run and run[-1].end != point.start:
            _reject_run(active, kind, "non_contiguous_candidate")
            run = active.partial_run if kind == "partial" else active.full_run
        run.append(point)
    else:
        _reject_run(active, kind, "threshold_not_sustained")


def _reject_run(active: _EpisodeBuilder, kind: str, reason: str) -> None:
    run = active.partial_run if kind == "partial" else active.full_run
    already_confirmed = kind == "partial" and active.partial_confirmed
    if run and not already_confirmed:
        active.recovery_candidates.append(
            RecoveryCandidate(
                kind=kind,
                start_at=run[0].start_text,
                supporting_sample_ids=tuple(item.sample_id for item in run),
                confirmed=False,
                reason_code=reason,
            )
        )
    run.clear()


def _flush_unconfirmed_candidates(active: _EpisodeBuilder, reason: str) -> None:
    _reject_run(active, "partial", reason)
    _reject_run(active, "full", reason)


def _update_waves(active: _EpisodeBuilder, point: _Point) -> None:
    current = active.wave_builders[-1]
    current.points.append(point)
    if point.visible is None or active.wave_detection_blocked:
        current.decline_run = []
        return

    if current.trough.visible is None or point.visible < current.trough.visible:
        current.trough = point
        current.rebound_peak = None
        current.decline_run = []
        return

    threshold = active.parameters.wave_threshold
    if current.rebound_peak is None:
        if point.visible - current.trough.visible >= threshold:
            current.rebound_peak = point
            active.ever_recovery_signal = True
        return

    if point.visible > current.rebound_peak.visible:
        current.rebound_peak = point
        current.decline_run = []
        return

    decline = current.rebound_peak.visible - point.visible
    if decline < threshold:
        current.decline_run = []
        return

    if current.decline_run and current.decline_run[-1].end != point.start:
        current.decline_run = []
    current.decline_run.append(point)
    if len(current.decline_run) < active.parameters.episode_confirm_slots:
        return

    decline_points = current.decline_run[-active.parameters.episode_confirm_slots :]
    decline_trough = min(decline_points, key=lambda item: item.visible)
    split = WaveSplitEvidence(
        previous_trough_sample_id=current.trough.sample_id,
        rebound_sample_id=current.rebound_peak.sample_id,
        new_decline_sample_id=decline_points[0].sample_id,
        rebound_amplitude=current.rebound_peak.visible - current.trough.visible,
        new_decline_amplitude=current.rebound_peak.visible - decline_trough.visible,
        significance_threshold=threshold,
    )
    active.episode_splits.append(
        EpisodeSplitEvidence(
            decision="same_episode_new_wave",
            left_sample_id=current.trough.sample_id,
            right_sample_id=decline_points[0].sample_id,
            full_recovery_confirmed=False,
            reason_code="partial_rebound_only",
        )
    )
    active.wave_builders.append(
        _WaveBuilder(
            ordinal=current.ordinal + 1,
            onset=decline_points[0],
            detected=decline_points[-1],
            initial_points=decline_points,
            split_evidence=split,
        )
    )


def _post_gap_duration_interval(
    points: Sequence[_Point],
    *,
    gap_index: int,
    active: _EpisodeBuilder,
) -> Optional[Tuple[datetime, datetime, Tuple[_Point, ...]]]:
    """在连续性缺口后仅以完整正常确认窗给出持续时间区间。

    缺口前最后一个可靠状态仍属于活动 episode。若缺口后的第一段可靠、
    连续状态立即满足完整的六槽完全恢复条件，则真正的恢复确认时刻只能
    落在“缺口前最后状态之后最早可完成确认窗”与“缺口后实际观察到的
    确认窗结束”之间。该证据不能把恢复状态升级为确定值，也不能合成精确
    结束时间；它只允许 ``duration_state=interval``。
    """

    parameters = active.parameters
    recovery_points = []  # type: list[_Point]
    previous: Optional[_Point] = None
    for candidate in points[gap_index + 1 :]:
        if candidate.continuity_state != "continuous":
            return None
        if previous is not None and previous.end != candidate.start:
            return None
        previous = candidate
        full = (
            candidate.visible is not None
            and parameters.normal_band_lower
            <= candidate.visible
            <= parameters.normal_band_upper
            and _anomaly_state(candidate, parameters) is False
        )
        if not full:
            return None
        recovery_points.append(candidate)
        if len(recovery_points) == parameters.full_confirm_slots:
            break

    if len(recovery_points) != parameters.full_confirm_slots:
        return None

    last_known = active.points[-1]
    granularity = last_known.end - last_known.start
    if granularity <= timedelta(0):
        raise AssertionError("样本粒度必须为正")
    already_full_slots = min(
        len(active.full_run), parameters.full_confirm_slots - 1
    )
    remaining_slots = parameters.full_confirm_slots - already_full_slots
    minimum_end = last_known.end + granularity * remaining_slots
    maximum_end = recovery_points[-1].end
    if minimum_end > maximum_end:
        # 缺口太短而两侧确认窗又不能跨缺口拼接时，不制造反向区间。
        return None
    return minimum_end, maximum_end, tuple(recovery_points)


def _finalize_episode(
    active: _EpisodeBuilder,
    *,
    observation_end: str,
    recovery_state: str,
    full_recovery_start: Optional[_Point],
    full_recovery_confirmation: Optional[_Point],
    duration_state: str,
    duration_interval_bounds: Optional[Tuple[datetime, datetime]] = None,
) -> Tuple[EpisodeDetection, Tuple[WaveDetection, ...]]:
    waves = []  # type: list[WaveDetection]
    for builder in active.wave_builders:
        wave_id = _stable_id(
            "wave_v1_",
            {
                "episode_id": active.episode_id,
                "ordinal": builder.ordinal,
                "onset_sample_id": builder.onset.sample_id,
            },
        )
        rebound_at = (
            None if builder.rebound_peak is None else builder.rebound_peak.start_text
        )
        waves.append(
            WaveDetection(
                wave_id=wave_id,
                episode_id=active.episode_id,
                run_id=active.onset.run_id,
                ordinal=builder.ordinal,
                onset_at=builder.onset.start_text,
                detected_at=builder.detected.start_text,
                trough_at=builder.trough.start_text,
                rebound_at=rebound_at,
                relation_to_previous_wave=(
                    "first_wave"
                    if builder.ordinal == 1
                    else "same_episode_after_partial_rebound"
                ),
                causal_relation="not_assessed",
                split_evidence=builder.split_evidence,
                supporting_sample_ids=tuple(
                    _unique(item.sample_id for item in builder.points)
                ),
            )
        )

    if duration_state == "exact":
        if full_recovery_start is None or full_recovery_confirmation is None:
            raise AssertionError("exact duration 必须绑定完全恢复确认窗")
        confirmed_at = full_recovery_confirmation.end
        confirmed_at_text = full_recovery_confirmation.end_text
        seconds = int((confirmed_at - active.onset.start).total_seconds())
        duration = DurationEstimate(
            duration_state="exact",
            seconds=seconds,
            minimum_seconds=None,
            maximum_seconds=None,
            measured_to=confirmed_at_text,
        )
        full_recovery_at = confirmed_at_text
    elif duration_state == "lower_bound":
        measured = _parse_datetime(observation_end, "observation_end")
        duration = DurationEstimate(
            duration_state="lower_bound",
            seconds=None,
            minimum_seconds=max(
                0, int((measured - active.onset.start).total_seconds())
            ),
            maximum_seconds=None,
            measured_to=observation_end,
        )
        full_recovery_at = None
    elif duration_state == "interval":
        if duration_interval_bounds is None:
            raise AssertionError("interval duration 必须绑定上下界")
        minimum_end, maximum_end = duration_interval_bounds
        if minimum_end > maximum_end:
            raise AssertionError("interval duration 上界不得小于下界")
        duration = DurationEstimate(
            duration_state="interval",
            seconds=None,
            minimum_seconds=max(
                0, int((minimum_end - active.onset.start).total_seconds())
            ),
            maximum_seconds=max(
                0, int((maximum_end - active.onset.start).total_seconds())
            ),
            measured_to=_format_datetime(maximum_end),
        )
        full_recovery_at = None
    elif duration_state == "unknown":
        duration = DurationEstimate(
            duration_state="unknown",
            seconds=None,
            minimum_seconds=None,
            maximum_seconds=None,
            measured_to=None,
        )
        full_recovery_at = None
    else:
        raise AssertionError("不支持的内部 duration_state: {}".format(duration_state))

    episode = EpisodeDetection(
        episode_id=active.episode_id,
        run_id=active.onset.run_id,
        collector_id=active.onset.collector_id,
        country_code=active.onset.country_code,
        cohort_view=active.onset.cohort_view,
        algorithm_version=active.parameters.episode_version,
        onset_at=active.onset.start_text,
        detected_at=active.detected.start_text,
        peak_at=active.peak.start_text,
        trough_at=active.trough.start_text,
        partial_recovery_at=active.partial_recovery_at,
        full_recovery_at=full_recovery_at,
        observation_end_at=observation_end,
        recovery_state=recovery_state,
        duration=duration,
        supporting_sample_ids=tuple(
            _unique(item.sample_id for item in active.points)
        ),
        wave_ids=tuple(item.wave_id for item in waves),
        split_evidence=tuple(active.episode_splits),
        recovery_candidates=tuple(active.recovery_candidates),
    )
    return episode, tuple(waves)


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return prefix + hashlib.sha256(raw).hexdigest()[:24]


def _parse_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise EpisodeInputError("{} 必须是带时区时间字符串".format(field))
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as error:
        raise EpisodeInputError("{} 不是有效时间: {}".format(field, value)) from error
    if parsed.tzinfo is None:
        raise EpisodeInputError("{} 必须带时区".format(field))
    return parsed.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EpisodeInputError("{} 必须是有限数值".format(field))
    number = float(value)
    if not math.isfinite(number):
        raise EpisodeInputError("{} 必须是有限数值".format(field))
    return number


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EpisodeInputError("{} 必须是正整数".format(field))
    return value


def _unique(values: Iterable[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(values))
