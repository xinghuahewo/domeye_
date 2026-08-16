"""有界 RRC25 国家中断研究 Profile 的严格加载与稳定身份。

Profile 只描述研究口径，不解析原始 MRT、不连接数据库，也不创建输出。
所有业务参数都必须出现在配置中；本模块不会填充默认值。时间仅接受规范
UTC ``Z`` 文本，研究窗口统一使用 ``[start, end)`` 半开语义。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Dict, Iterator, Mapping, Sequence


UTC = timezone.utc
PROFILE_SCHEMA_VERSION = "research_profile_v1"
PROFILE_KIND = "bounded_country_outage_research"
UTC_TEXT_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COLLECTOR_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
STUDY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")


class ResearchProfileError(ValueError):
    """研究 Profile 缺失、含糊或违反有界只读合同。"""


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ResearchProfileError(f"研究 Profile JSON 字段重复：{key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ResearchProfileError(f"研究 Profile 禁止非有限数值：{value}")


def _load_json_strict(path: Path) -> Mapping[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ResearchProfileError(f"无法读取研究 Profile：{path}") from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except ResearchProfileError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ResearchProfileError(f"研究 Profile 不是有效 UTF-8 JSON：{path}") from error
    if not isinstance(value, Mapping):
        raise ResearchProfileError("研究 Profile 根节点必须是对象")
    return value


def _keys(value: Any, field: str, required: Sequence[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchProfileError(f"{field} 必须是对象")
    required_set = set(required)
    actual_set = set(value)
    missing = sorted(required_set - actual_set)
    unknown = sorted(actual_set - required_set)
    if missing:
        raise ResearchProfileError(f"{field} 缺少显式字段：{','.join(missing)}")
    if unknown:
        raise ResearchProfileError(f"{field} 含未知字段：{','.join(unknown)}")
    return value


def _text(value: Any, field: str, *, choices: Sequence[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise ResearchProfileError(f"{field} 必须是非空字符串")
    if choices is not None and value not in choices:
        raise ResearchProfileError(f"{field} 必须是：{','.join(choices)}")
    return value


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ResearchProfileError(f"{field} 必须是布尔值")
    return value


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ResearchProfileError(f"{field} 必须是不小于 {minimum} 的整数")
    return value


def _number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResearchProfileError(f"{field} 必须是数值")
    number = float(value)
    if not math.isfinite(number):
        raise ResearchProfileError(f"{field} 必须是有限数值")
    if minimum is not None and number < minimum:
        raise ResearchProfileError(f"{field} 不得小于 {minimum}")
    if maximum is not None and number > maximum:
        raise ResearchProfileError(f"{field} 不得大于 {maximum}")
    return number


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or UTC_TEXT_RE.fullmatch(value) is None:
        raise ResearchProfileError(f"{field} 必须是规范 UTC 秒级 Z 时间")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise ResearchProfileError(f"{field} 不是有效 UTC 时间") from error
    return parsed


def _ordered_unique_strings(value: Any, field: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, list) or not value:
        raise ResearchProfileError(f"{field} 必须是非空字符串数组")
    if any(not isinstance(item, str) or not item for item in value):
        raise ResearchProfileError(f"{field} 只能包含非空字符串")
    if len(set(value)) != len(value):
        raise ResearchProfileError(f"{field} 不得重复")
    return list(value)


def _validate_window(profile: Mapping[str, Any]) -> tuple[datetime, datetime, int]:
    window = _keys(
        profile["window"],
        "window",
        (
            "start_utc",
            "end_exclusive_utc",
            "observation_end_utc",
            "granularity_seconds",
            "interval_semantics",
            "end_boundary_role",
        ),
    )
    start = _utc(window["start_utc"], "window.start_utc")
    end = _utc(window["end_exclusive_utc"], "window.end_exclusive_utc")
    observation_end = _utc(window["observation_end_utc"], "window.observation_end_utc")
    granularity = _integer(window["granularity_seconds"], "window.granularity_seconds", minimum=1)
    _text(window["interval_semantics"], "window.interval_semantics", choices=("half_open",))
    _text(
        window["end_boundary_role"],
        "window.end_boundary_role",
        choices=("state_boundary_only_excluded_from_updates",),
    )
    if start >= end:
        raise ResearchProfileError("window.start_utc 必须早于 end_exclusive_utc")
    if observation_end != end:
        raise ResearchProfileError("observation_end_utc 必须等于半开窗口结束边界")
    duration_seconds = int((end - start).total_seconds())
    if duration_seconds % granularity:
        raise ResearchProfileError("研究窗口必须与 granularity_seconds 整槽对齐")
    return start, end, granularity


def _validate_input_selection(
    profile: Mapping[str, Any], start: datetime, end: datetime, granularity: int
) -> None:
    selection = _keys(
        profile["input_selection"],
        "input_selection",
        (
            "filename_timestamp_timezone",
            "state_seed_rib",
            "baseline_reference_rib",
            "catch_up_updates",
            "analysis_updates",
            "analysis_ribs",
        ),
    )
    _text(
        selection["filename_timestamp_timezone"],
        "input_selection.filename_timestamp_timezone",
        choices=("UTC",),
    )
    state_seed = _keys(
        selection["state_seed_rib"],
        "input_selection.state_seed_rib",
        ("selection_policy", "allow_at_window_start", "complete_required"),
    )
    _text(
        state_seed["selection_policy"],
        "input_selection.state_seed_rib.selection_policy",
        choices=("complete_at_start_or_nearest_complete_before",),
    )
    if not _boolean(state_seed["allow_at_window_start"], "input_selection.state_seed_rib.allow_at_window_start"):
        raise ResearchProfileError("state_seed_rib 必须允许使用窗口起点的完整 RIB")
    if not _boolean(state_seed["complete_required"], "input_selection.state_seed_rib.complete_required"):
        raise ResearchProfileError("state_seed_rib 必须要求完整制品")

    reference = _keys(
        selection["baseline_reference_rib"],
        "input_selection.baseline_reference_rib",
        (
            "selection_policy",
            "strictly_before_window_start",
            "complete_required",
            "expected_count",
        ),
    )
    _text(
        reference["selection_policy"],
        "input_selection.baseline_reference_rib.selection_policy",
        choices=("nearest_complete_strictly_before_start",),
    )
    if not _boolean(reference["strictly_before_window_start"], "input_selection.baseline_reference_rib.strictly_before_window_start"):
        raise ResearchProfileError("baseline_reference_rib 必须严格早于窗口起点")
    if not _boolean(reference["complete_required"], "input_selection.baseline_reference_rib.complete_required"):
        raise ResearchProfileError("baseline_reference_rib 必须要求完整制品")
    if _integer(reference["expected_count"], "input_selection.baseline_reference_rib.expected_count", minimum=1) != 1:
        raise ResearchProfileError("baseline_reference_rib.expected_count 必须为 1")

    catch_up = _keys(
        selection["catch_up_updates"],
        "input_selection.catch_up_updates",
        ("condition", "start_boundary", "end_boundary"),
    )
    _text(
        catch_up["condition"],
        "input_selection.catch_up_updates.condition",
        choices=("required_when_state_seed_precedes_window_start",),
    )
    _text(
        catch_up["start_boundary"],
        "input_selection.catch_up_updates.start_boundary",
        choices=("state_seed_time_inclusive",),
    )
    _text(
        catch_up["end_boundary"],
        "input_selection.catch_up_updates.end_boundary",
        choices=("window_start_exclusive",),
    )

    analysis_updates = _keys(
        selection["analysis_updates"],
        "input_selection.analysis_updates",
        ("start_boundary", "end_boundary", "slot_interval_seconds", "expected_slot_count"),
    )
    _text(analysis_updates["start_boundary"], "input_selection.analysis_updates.start_boundary", choices=("window_start_inclusive",))
    _text(analysis_updates["end_boundary"], "input_selection.analysis_updates.end_boundary", choices=("window_end_exclusive",))
    update_interval = _integer(analysis_updates["slot_interval_seconds"], "input_selection.analysis_updates.slot_interval_seconds", minimum=1)
    if update_interval != granularity:
        raise ResearchProfileError("analysis_updates.slot_interval_seconds 必须等于研究粒度")
    expected_updates = int((end - start).total_seconds()) // update_interval
    if _integer(analysis_updates["expected_slot_count"], "input_selection.analysis_updates.expected_slot_count", minimum=1) != expected_updates:
        raise ResearchProfileError(f"analysis_updates.expected_slot_count 必须为半开窗口推导值 {expected_updates}")

    analysis_ribs = _keys(
        selection["analysis_ribs"],
        "input_selection.analysis_ribs",
        ("start_boundary", "end_boundary", "slot_interval_seconds", "expected_slot_count"),
    )
    _text(analysis_ribs["start_boundary"], "input_selection.analysis_ribs.start_boundary", choices=("window_start_inclusive",))
    _text(analysis_ribs["end_boundary"], "input_selection.analysis_ribs.end_boundary", choices=("window_end_exclusive",))
    rib_interval = _integer(analysis_ribs["slot_interval_seconds"], "input_selection.analysis_ribs.slot_interval_seconds", minimum=1)
    duration = int((end - start).total_seconds())
    expected_ribs = ((duration - 1) // rib_interval) + 1
    if _integer(analysis_ribs["expected_slot_count"], "input_selection.analysis_ribs.expected_slot_count", minimum=1) != expected_ribs:
        raise ResearchProfileError(f"analysis_ribs.expected_slot_count 必须为半开窗口推导值 {expected_ribs}")


def _validate_country_mapping(profile: Mapping[str, Any]) -> None:
    mapping = _keys(
        profile["country_mapping"],
        "country_mapping",
        (
            "compatibility_view",
            "revised_view",
            "unknown_policy",
            "conflict_policy",
            "source_binding",
        ),
    )
    _text(mapping["compatibility_view"], "country_mapping.compatibility_view", choices=("frozen_legacy_static_mapping",))
    _text(mapping["revised_view"], "country_mapping.revised_view", choices=("separate_non_overwriting_projection",))
    _text(mapping["unknown_policy"], "country_mapping.unknown_policy", choices=("preserve_as_country_mapping_unknown",))
    _text(mapping["conflict_policy"], "country_mapping.conflict_policy", choices=("preserve_as_country_mapping_conflict",))
    _text(mapping["source_binding"], "country_mapping.source_binding", choices=("resolver_manifest_path_and_sha256_required",))


def _validate_baseline(
    profile: Mapping[str, Any],
    start: datetime,
    end: datetime,
    granularity: int,
) -> None:
    baseline = _keys(profile["baseline"], "baseline", ("membership", "numeric", "normal_band"))
    membership = _keys(
        baseline["membership"],
        "baseline.membership",
        ("source", "mapping_view", "dynamic_ir_origins"),
    )
    _text(membership["source"], "baseline.membership.source", choices=("state_seed_rib",))
    _text(membership["mapping_view"], "baseline.membership.mapping_view", choices=("compatibility",))
    _text(membership["dynamic_ir_origins"], "baseline.membership.dynamic_ir_origins", choices=("include_during_window",))

    numeric = _keys(
        baseline["numeric"],
        "baseline.numeric",
        (
            "metric",
            "candidate_start",
            "initial_duration_seconds",
            "statistic",
            "dispersion",
            "max_relative_mad",
            "extension_direction",
            "extension_step_seconds",
            "max_duration_seconds",
            "stop_before_exclusion_boundary",
            "exclusion_boundary",
            "unstable_exhausted_state",
            "record_actual_window",
        ),
    )
    _text(numeric["metric"], "baseline.numeric.metric", choices=("ipv4_visible_unique_address_count",))
    _text(numeric["candidate_start"], "baseline.numeric.candidate_start", choices=("analysis_window_start",))
    initial = _integer(numeric["initial_duration_seconds"], "baseline.numeric.initial_duration_seconds", minimum=granularity)
    step = _integer(numeric["extension_step_seconds"], "baseline.numeric.extension_step_seconds", minimum=granularity)
    maximum = _integer(numeric["max_duration_seconds"], "baseline.numeric.max_duration_seconds", minimum=initial)
    for field, value in (("initial_duration_seconds", initial), ("extension_step_seconds", step), ("max_duration_seconds", maximum)):
        if value % granularity:
            raise ResearchProfileError(f"baseline.numeric.{field} 必须与研究粒度整槽对齐")
    _text(numeric["statistic"], "baseline.numeric.statistic", choices=("median",))
    _text(numeric["dispersion"], "baseline.numeric.dispersion", choices=("median_absolute_deviation",))
    _number(numeric["max_relative_mad"], "baseline.numeric.max_relative_mad", minimum=0.0, maximum=1.0)
    _text(numeric["extension_direction"], "baseline.numeric.extension_direction", choices=("forward",))
    if not _boolean(
        numeric["stop_before_exclusion_boundary"],
        "baseline.numeric.stop_before_exclusion_boundary",
    ):
        raise ResearchProfileError("基线扩展必须在候选排除边界前停止")
    boundary = _keys(
        numeric["exclusion_boundary"],
        "baseline.numeric.exclusion_boundary",
        ("at_utc", "role", "confirmation_state", "causal_claim_allowed"),
    )
    boundary_at = _utc(
        boundary["at_utc"], "baseline.numeric.exclusion_boundary.at_utc"
    )
    _text(
        boundary["role"],
        "baseline.numeric.exclusion_boundary.role",
        choices=("user_supplied_earliest_possible_precursor_boundary",),
    )
    _text(
        boundary["confirmation_state"],
        "baseline.numeric.exclusion_boundary.confirmation_state",
        choices=("candidate_not_confirmed",),
    )
    if _boolean(
        boundary["causal_claim_allowed"],
        "baseline.numeric.exclusion_boundary.causal_claim_allowed",
    ):
        raise ResearchProfileError("候选排除边界不得授权因果或前兆结论")
    boundary_offset = int((boundary_at - start).total_seconds())
    if boundary_at <= start or boundary_at > end:
        raise ResearchProfileError(
            "基线候选排除边界必须位于研究窗口起点之后且不晚于观察边界"
        )
    if boundary_offset % granularity:
        raise ResearchProfileError("基线候选排除边界必须与研究粒度整槽对齐")
    if boundary_offset < initial:
        raise ResearchProfileError("基线候选排除边界必须容纳完整初始基线窗口")
    _text(numeric["unstable_exhausted_state"], "baseline.numeric.unstable_exhausted_state", choices=("incomplete",))
    if not _boolean(numeric["record_actual_window"], "baseline.numeric.record_actual_window"):
        raise ResearchProfileError("必须记录实际使用的基线窗口")

    normal_band = _keys(
        baseline["normal_band"],
        "baseline.normal_band",
        ("method", "mad_multiplier", "absolute_floor_ratio", "version"),
    )
    _text(normal_band["method"], "baseline.normal_band.method", choices=("median_plus_minus_max_scaled_mad_and_absolute_floor",))
    _number(normal_band["mad_multiplier"], "baseline.normal_band.mad_multiplier", minimum=0.0)
    _number(normal_band["absolute_floor_ratio"], "baseline.normal_band.absolute_floor_ratio", minimum=0.0, maximum=1.0)
    _text(normal_band["version"], "baseline.normal_band.version")


def _validate_measurement(profile: Mapping[str, Any]) -> None:
    measurement = _keys(
        profile["measurement"],
        "measurement",
        (
            "address_families",
            "views",
            "country_prefix_aggregation",
            "ipv4_metrics",
            "ipv6_metrics",
            "as_visibility_classification",
            "moas_policy",
            "missing_value_policy",
        ),
    )
    if _ordered_unique_strings(measurement["address_families"], "measurement.address_families") != ["ipv4", "ipv6"]:
        raise ResearchProfileError("measurement.address_families 必须显式按 ipv4、ipv6 双栈排列")
    if _ordered_unique_strings(measurement["views"], "measurement.views") != ["compatibility", "revised"]:
        raise ResearchProfileError("measurement.views 必须显式包含 compatibility、revised")
    _text(measurement["country_prefix_aggregation"], "measurement.country_prefix_aggregation", choices=("deduplicated_address_union_per_afi",))
    if _ordered_unique_strings(measurement["ipv4_metrics"], "measurement.ipv4_metrics") != ["visible_unique_address_count", "slash24_equivalent_count"]:
        raise ResearchProfileError("measurement.ipv4_metrics 口径不完整")
    if _ordered_unique_strings(measurement["ipv6_metrics"], "measurement.ipv6_metrics") != ["slash48_equivalent_count"]:
        raise ResearchProfileError("measurement.ipv6_metrics 仅允许明确的 /48 等价值")
    _text(measurement["as_visibility_classification"], "measurement.as_visibility_classification", choices=("per_afi_then_dual_stack",))
    _text(measurement["moas_policy"], "measurement.moas_policy", choices=("preserve_all_origins_do_not_sum_as_totals_as_country_total",))
    _text(measurement["missing_value_policy"], "measurement.missing_value_policy", choices=("preserve_value_state_never_coerce_to_zero",))


def _validate_algorithms(profile: Mapping[str, Any]) -> None:
    algorithms = _keys(profile["algorithms"], "algorithms", ("episode", "wave", "recovery"))
    episode = _keys(
        algorithms["episode"],
        "algorithms.episode",
        (
            "version",
            "combine_rule",
            "ipv4_visible_ratio_below",
            "damaged_as_ratio_above",
            "confirm_consecutive_slots",
            "onset_assignment",
            "detected_assignment",
        ),
    )
    _text(episode["version"], "algorithms.episode.version")
    _text(episode["combine_rule"], "algorithms.episode.combine_rule", choices=("any",))
    _number(episode["ipv4_visible_ratio_below"], "algorithms.episode.ipv4_visible_ratio_below", minimum=0.0, maximum=1.0)
    _number(episode["damaged_as_ratio_above"], "algorithms.episode.damaged_as_ratio_above", minimum=0.0, maximum=1.0)
    _integer(episode["confirm_consecutive_slots"], "algorithms.episode.confirm_consecutive_slots", minimum=1)
    _text(episode["onset_assignment"], "algorithms.episode.onset_assignment", choices=("first_anomalous_slot",))
    _text(episode["detected_assignment"], "algorithms.episode.detected_assignment", choices=("confirmation_slot",))

    wave = _keys(
        algorithms["wave"],
        "algorithms.wave",
        (
            "version",
            "significance_method",
            "baseline_ratio_floor",
            "mad_multiplier",
            "new_episode_requires_full_recovery",
            "inter_wave_relation",
        ),
    )
    _text(wave["version"], "algorithms.wave.version")
    _text(wave["significance_method"], "algorithms.wave.significance_method", choices=("max_baseline_ratio_or_scaled_mad",))
    _number(wave["baseline_ratio_floor"], "algorithms.wave.baseline_ratio_floor", minimum=0.0, maximum=1.0)
    _number(wave["mad_multiplier"], "algorithms.wave.mad_multiplier", minimum=0.0)
    if not _boolean(wave["new_episode_requires_full_recovery"], "algorithms.wave.new_episode_requires_full_recovery"):
        raise ResearchProfileError("拆分新 episode 前必须确认完全恢复")
    _text(wave["inter_wave_relation"], "algorithms.wave.inter_wave_relation", choices=("unknown_not_causal",))

    recovery = _keys(
        algorithms["recovery"],
        "algorithms.recovery",
        (
            "version",
            "partial_visible_ratio_at_least",
            "partial_confirm_consecutive_slots",
            "full_rule",
            "full_confirm_consecutive_slots",
            "duration_states",
            "recovery_states",
        ),
    )
    _text(recovery["version"], "algorithms.recovery.version")
    _number(recovery["partial_visible_ratio_at_least"], "algorithms.recovery.partial_visible_ratio_at_least", minimum=0.0, maximum=1.0)
    _integer(recovery["partial_confirm_consecutive_slots"], "algorithms.recovery.partial_confirm_consecutive_slots", minimum=1)
    _text(recovery["full_rule"], "algorithms.recovery.full_rule", choices=("within_versioned_baseline_normal_band",))
    _integer(recovery["full_confirm_consecutive_slots"], "algorithms.recovery.full_confirm_consecutive_slots", minimum=1)
    if _ordered_unique_strings(recovery["duration_states"], "algorithms.recovery.duration_states") != ["exact", "lower_bound", "interval", "unknown"]:
        raise ResearchProfileError("algorithms.recovery.duration_states 必须冻结四类状态")
    if _ordered_unique_strings(recovery["recovery_states"], "algorithms.recovery.recovery_states") != ["ongoing", "recovering", "partially_recovered", "fully_recovered"]:
        raise ResearchProfileError("algorithms.recovery.recovery_states 必须冻结四类状态")


def _validate_resource_limits(profile: Mapping[str, Any]) -> None:
    limits = _keys(
        profile["resource_limits"],
        "resource_limits",
        (
            "max_new_raw_read_bytes",
            "max_temporary_bytes",
            "max_worker_runtime_seconds",
            "worker_soft_stop_seconds",
            "database_writes",
            "output_storage",
        ),
    )
    _integer(limits["max_new_raw_read_bytes"], "resource_limits.max_new_raw_read_bytes", minimum=1)
    _integer(limits["max_temporary_bytes"], "resource_limits.max_temporary_bytes", minimum=1)
    hard = _integer(limits["max_worker_runtime_seconds"], "resource_limits.max_worker_runtime_seconds", minimum=1)
    soft = _integer(limits["worker_soft_stop_seconds"], "resource_limits.worker_soft_stop_seconds", minimum=1)
    if soft >= hard:
        raise ResearchProfileError("worker_soft_stop_seconds 必须小于硬运行时限")
    _text(limits["database_writes"], "resource_limits.database_writes", choices=("forbidden",))
    _text(limits["output_storage"], "resource_limits.output_storage", choices=("filesystem_only",))


def _validate_output_policy(profile: Mapping[str, Any]) -> None:
    policy = _keys(
        profile["output_policy"],
        "output_policy",
        (
            "required_format",
            "optional_projection",
            "immutable_outputs",
            "atomic_publish",
            "overwrite_existing",
            "frontend_changes",
            "production_deployment",
        ),
    )
    _text(policy["required_format"], "output_policy.required_format", choices=("canonical_jsonl_gzip",))
    _text(policy["optional_projection"], "output_policy.optional_projection", choices=("parquet",))
    if not _boolean(policy["immutable_outputs"], "output_policy.immutable_outputs"):
        raise ResearchProfileError("研究输出必须不可变")
    if not _boolean(policy["atomic_publish"], "output_policy.atomic_publish"):
        raise ResearchProfileError("研究输出必须原子发布")
    if _boolean(policy["overwrite_existing"], "output_policy.overwrite_existing"):
        raise ResearchProfileError("研究输出不得覆盖既有制品")
    _text(policy["frontend_changes"], "output_policy.frontend_changes", choices=("forbidden",))
    _text(policy["production_deployment"], "output_policy.production_deployment", choices=("forbidden",))


def validate_research_profile(value: Mapping[str, Any]) -> Dict[str, Any]:
    """严格验证并返回规范键序配置；任何缺失参数都失败关闭。"""

    profile = _keys(
        value,
        "profile",
        (
            "$schema",
            "schema_version",
            "study_id",
            "profile_kind",
            "collector_id",
            "country_code",
            "time_basis",
            "window",
            "input_selection",
            "country_mapping",
            "baseline",
            "measurement",
            "algorithms",
            "resource_limits",
            "output_policy",
        ),
    )
    _text(profile["$schema"], "$schema", choices=("https://domeye.example/contracts/research/research-profile.schema.json",))
    _text(profile["schema_version"], "schema_version", choices=(PROFILE_SCHEMA_VERSION,))
    study_id = _text(profile["study_id"], "study_id")
    if STUDY_ID_RE.fullmatch(study_id) is None:
        raise ResearchProfileError("study_id 格式非法")
    _text(profile["profile_kind"], "profile_kind", choices=(PROFILE_KIND,))
    collector = _text(profile["collector_id"], "collector_id")
    if COLLECTOR_RE.fullmatch(collector) is None:
        raise ResearchProfileError("collector_id 格式非法")
    country = _text(profile["country_code"], "country_code")
    if COUNTRY_RE.fullmatch(country) is None:
        raise ResearchProfileError("country_code 必须是两个大写字母")
    _text(profile["time_basis"], "time_basis", choices=("UTC",))

    start, end, granularity = _validate_window(profile)
    _validate_input_selection(profile, start, end, granularity)
    _validate_country_mapping(profile)
    _validate_baseline(profile, start, end, granularity)
    _validate_measurement(profile)
    _validate_algorithms(profile)
    _validate_resource_limits(profile)
    _validate_output_policy(profile)

    # 规范 JSON round-trip 同时生成深拷贝并固定键序，不保留调用方可变对象。
    return json.loads(canonical_profile_bytes(profile).decode("utf-8"))


def load_research_profile(path: str | Path) -> Dict[str, Any]:
    """从 UTF-8 JSON 加载 Profile；不允许重复键、NaN 或隐式默认。"""

    return validate_research_profile(_load_json_strict(Path(path)))


def canonical_profile_bytes(profile: Mapping[str, Any]) -> bytes:
    """返回稳定、无空白、禁止 NaN 的 UTF-8 Profile 字节。"""

    try:
        text = json.dumps(
            profile,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ResearchProfileError("研究 Profile 含不可规范化值") from error
    return text.encode("utf-8")


def profile_sha256(profile: Mapping[str, Any]) -> str:
    """计算只依赖规范配置内容、与文件排版和路径无关的 SHA256。"""

    normalized = validate_research_profile(profile)
    return hashlib.sha256(canonical_profile_bytes(normalized)).hexdigest()


def research_run_id_v1(
    profile: Mapping[str, Any],
    *,
    input_manifest_sha256: str,
    mapping_sha256: str,
    processing_sha256: str,
) -> str:
    """绑定配置、输入、映射和处理版本，生成稳定研究运行身份。"""

    for field, value in (
        ("input_manifest_sha256", input_manifest_sha256),
        ("mapping_sha256", mapping_sha256),
        ("processing_sha256", processing_sha256),
    ):
        if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
            raise ResearchProfileError(f"{field} 必须是 64 位小写 SHA256")
    identity = {
        "schema": "research_run_id_v1",
        "profile_sha256": profile_sha256(profile),
        "input_manifest_sha256": input_manifest_sha256,
        "mapping_sha256": mapping_sha256,
        "processing_sha256": processing_sha256,
    }
    digest = hashlib.sha256(canonical_profile_bytes(identity)).hexdigest()
    return "research_run_v1_" + digest[:24]


def iter_update_slots(profile: Mapping[str, Any]) -> Iterator[str]:
    """按半开窗口生成 UPDATE 槽；结束边界永不成为输入槽。"""

    normalized = validate_research_profile(profile)
    start = _utc(normalized["window"]["start_utc"], "window.start_utc")
    end = _utc(normalized["window"]["end_exclusive_utc"], "window.end_exclusive_utc")
    step = timedelta(seconds=normalized["window"]["granularity_seconds"])
    current = start
    while current < end:
        yield current.strftime("%Y-%m-%dT%H:%M:%SZ")
        current += step
