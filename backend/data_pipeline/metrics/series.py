"""P0 MetricSeries 的确定性、无数据库依赖适配器。

调用方必须先完成只读查询，并把来源存在槽、处理缺口槽和对象行分别传入。
本模块不连接数据库、不捕获查询异常，也不会把缺行、缺源或不可计算值补成 0。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import math
import re
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


UTC = timezone.utc
GRANULARITY_SECONDS = 300
SCHEMA_VERSION = "metric-series/v1"
BUSINESS_TIMEZONE = "Asia/Shanghai"

ASN_RE = re.compile(r"^(0|[1-9][0-9]*)$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
COLLECTOR_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
INCIDENT_ID_RE = re.compile(r"^inc_v1_[0-9a-f]{24}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class MetricSeriesError(ValueError):
    """输入无法在不猜测数据语义的前提下生成 MetricSeries。"""


@dataclass(frozen=True)
class MetricDefinition:
    """一个已冻结指标的物理单位、公式和输入形态。"""

    unit: str
    aggregation: str
    formula: str
    formula_version: str
    input_kind: str


METRIC_DEFINITIONS: Mapping[str, MetricDefinition] = MappingProxyType({
    "bgp_announce_record_count": MetricDefinition(
        "bgp_update_record",
        "sum_observation_values",
        "sum(feature_country.announ_num)",
        "announce_count_v1",
        "announce",
    ),
    "bgp_withdraw_record_count": MetricDefinition(
        "bgp_update_record",
        "sum_observation_values",
        "sum(feature_country.withdraw_num)",
        "withdraw_count_v1",
        "withdraw",
    ),
    "bgp_update_record_count": MetricDefinition(
        "bgp_update_record",
        "sum_components",
        "sum(feature_country.announ_num) + sum(feature_country.withdraw_num)",
        "update_total_v1",
        "update_total",
    ),
    "bgp_withdraw_ratio": MetricDefinition(
        "ratio_0_1",
        "ratio_of_sums",
        "sum(feature_country.withdraw_num) / (sum(feature_country.announ_num) + sum(feature_country.withdraw_num))",
        "withdraw_ratio_v1",
        "withdraw_ratio",
    ),
    "ipv4_24_equivalent_count": MetricDefinition(
        "ipv4_24_equivalent",
        "last_observation",
        "last_observation(feature_country.v4prefix_num)",
        "ipv4_24e_v1",
        "v4prefix_num",
    ),
    "ipv6_48_equivalent_count": MetricDefinition(
        "ipv6_48_equivalent",
        "last_observation",
        "last_observation(feature_country.v6prefix_num)",
        "ipv6_48e_v1",
        "v6prefix_num",
    ),
    "ipv4_equivalent_address_count": MetricDefinition(
        "ipv4_equivalent_address",
        "last_observation",
        "last_observation(feature_country.v4ip_num)",
        "ipv4_address_v1",
        "v4ip_num",
    ),
    "anomaly_incident_count": MetricDefinition(
        "anomaly_incident",
        "count_distinct_incidents",
        "count(distinct Incident.incident_id)",
        "anomaly_incident_count_v1",
        "incident_ids",
    ),
    "prefix_outage_concurrent_count": MetricDefinition(
        "prefix_count",
        "max_concurrent",
        "max_180s(count(distinct prefix_outage_YYYYMM.prefix))",
        "prefix_outage_concurrency_v1",
        "prefix_concurrency_samples",
    ),
    "as_outage_concurrent_count": MetricDefinition(
        "asn_count",
        "max_concurrent",
        "max_180s(count(distinct as_outage_YYYYMM.asn))",
        "as_outage_concurrency_v1",
        "asn_concurrency_samples",
    ),
})

SPARSE_ASN_ZERO_METRICS = frozenset(
    {
        "bgp_announce_record_count",
        "bgp_withdraw_record_count",
        "bgp_update_record_count",
    }
)
RESOURCE_INPUT_KINDS = frozenset({"v4prefix_num", "v6prefix_num", "v4ip_num"})

# 显式指标缺失是一个独立于来源缺失/处理缺口的窄接口。P0 只准许两项
# 历史中断并发指标使用 legacy_unknown；以后若要扩大范围，必须在此处完成
# 指标级审阅，不能由调用方传入任意状态绕过冻结语义。
EXPLICIT_METRIC_MISSING_STATES: Mapping[str, frozenset[str]] = MappingProxyType({
    "prefix_outage_concurrent_count": frozenset({"legacy_unknown"}),
    "as_outage_concurrent_count": frozenset({"legacy_unknown"}),
})


def canonical_metric_series_bytes(series: Mapping[str, Any]) -> bytes:
    """输出排序键、无空白、禁止 NaN 的稳定 UTF-8 JSON。"""

    try:
        return json.dumps(
            series,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise MetricSeriesError("MetricSeries 含不可序列化或非有限值") from error


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: Any, field: str, *, aligned: bool) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise MetricSeriesError(f"{field} 不是有效 ISO 8601 时间") from error
    else:
        raise MetricSeriesError(f"{field} 必须是带时区的 ISO 8601 时间")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MetricSeriesError(f"{field} 必须带时区")
    if parsed.microsecond:
        raise MetricSeriesError(f"{field} 只能精确到秒")
    normalized = parsed.astimezone(UTC)
    if aligned and int(normalized.timestamp()) % GRANULARITY_SECONDS:
        raise MetricSeriesError(f"{field} 未对齐 300 秒网格")
    return normalized


def _sequence(value: Any, field: str) -> List[Any]:
    if isinstance(value, BaseException):
        raise MetricSeriesError(f"{field} 收到查询异常；调用方必须直接上抛") from value
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise MetricSeriesError(f"{field} 必须是显式序列")
    try:
        return list(value)
    except TypeError as error:
        raise MetricSeriesError(f"{field} 必须是显式序列") from error


def _normalize_slots(values: Iterable[Any], field: str) -> Tuple[datetime, ...]:
    normalized = [_parse_time(value, field, aligned=True) for value in _sequence(values, field)]
    if len(set(normalized)) != len(normalized):
        raise MetricSeriesError(f"{field} 含重复或时区归一化后重叠的槽")
    return tuple(sorted(normalized))


def _require_exact_keys(value: Mapping[str, Any], expected: Sequence[str], field: str) -> None:
    actual = set(value)
    wanted = set(expected)
    if actual != wanted:
        missing = sorted(str(item) for item in wanted - actual)
        extra = sorted(str(item) for item in actual - wanted)
        details = []
        if missing:
            details.append("缺少 " + ",".join(missing))
        if extra:
            details.append("多出 " + ",".join(extra))
        raise MetricSeriesError(f"{field} 字段不符合合同：" + "；".join(details))


def _normalize_subject(subject: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(subject, Mapping):
        raise MetricSeriesError("subject 必须是映射")
    _require_exact_keys(subject, ("subject_type", "subject_id", "display_name"), "subject")
    subject_type = subject["subject_type"]
    subject_id = subject["subject_id"]
    display_name = subject["display_name"]
    if subject_type not in {"global", "country", "asn", "prefix"}:
        raise MetricSeriesError("subject.subject_type 非法")
    if not isinstance(subject_id, str):
        raise MetricSeriesError("subject.subject_id 必须是规范字符串")
    if subject_type == "global" and subject_id != "global":
        raise MetricSeriesError("global 对象的 subject_id 必须为 global")
    if subject_type == "country" and not COUNTRY_RE.fullmatch(subject_id):
        raise MetricSeriesError("国家 subject_id 必须是两位大写代码")
    if subject_type == "asn" and not ASN_RE.fullmatch(subject_id):
        raise MetricSeriesError("ASN subject_id 必须是不带 AS 前缀的十进制字符串")
    if subject_type == "prefix":
        try:
            canonical = str(ipaddress.ip_network(subject_id, strict=True))
        except ValueError as error:
            raise MetricSeriesError("prefix subject_id 非法或含主机位") from error
        if canonical != subject_id:
            raise MetricSeriesError("prefix subject_id 必须使用规范文本")
    if display_name is not None and (not isinstance(display_name, str) or not display_name):
        raise MetricSeriesError("subject.display_name 必须为 null 或非空字符串")
    return {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "display_name": display_name,
    }


def _normalize_collector_scope(scope: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(scope, Mapping):
        raise MetricSeriesError("collector_scope 必须是映射")
    _require_exact_keys(
        scope, ("scope_kind", "collector_ids", "limitation_reason"), "collector_scope"
    )
    kind = scope["scope_kind"]
    if kind not in {"collector_set", "all_available_collectors", "legacy_unknown"}:
        raise MetricSeriesError("collector_scope.scope_kind 非法")
    collectors = _sequence(scope["collector_ids"], "collector_scope.collector_ids")
    if any(not isinstance(item, str) or not COLLECTOR_RE.fullmatch(item) for item in collectors):
        raise MetricSeriesError("collector_scope.collector_ids 含非法 ID")
    if len(set(collectors)) != len(collectors):
        raise MetricSeriesError("collector_scope.collector_ids 不得重复")
    reason = scope["limitation_reason"]
    if reason is not None and (not isinstance(reason, str) or not reason):
        raise MetricSeriesError("collector_scope.limitation_reason 必须为 null 或非空字符串")
    if kind == "legacy_unknown":
        if collectors or reason is None:
            raise MetricSeriesError("legacy_unknown 必须使用空 collector_ids 和明确限制说明")
    elif not collectors:
        raise MetricSeriesError("已知采集范围必须至少包含一个 collector")
    return {
        "scope_kind": kind,
        "collector_ids": sorted(collectors),
        "limitation_reason": reason,
    }


def _normalize_source_refs(source_refs: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    allowed_layers = {
        "raw_observation",
        "route_event",
        "detection_fact",
        "derived_metric",
        "release_inventory",
        "data_quality_report",
    }
    for index, ref in enumerate(_sequence(source_refs, "source_refs")):
        if not isinstance(ref, Mapping):
            raise MetricSeriesError(f"source_refs[{index}] 必须是映射")
        _require_exact_keys(ref, ("source_layer", "ref_id", "locator", "sha256"), f"source_refs[{index}]")
        if ref["source_layer"] not in allowed_layers:
            raise MetricSeriesError(f"source_refs[{index}].source_layer 非法")
        if not isinstance(ref["ref_id"], str) or not ref["ref_id"]:
            raise MetricSeriesError(f"source_refs[{index}].ref_id 不能为空")
        if not isinstance(ref["locator"], str) or not ref["locator"]:
            raise MetricSeriesError(f"source_refs[{index}].locator 不能为空")
        digest = ref["sha256"]
        if digest is not None and (not isinstance(digest, str) or not SHA256_RE.fullmatch(digest)):
            raise MetricSeriesError(f"source_refs[{index}].sha256 非法")
        refs.append(
            {
                "source_layer": ref["source_layer"],
                "ref_id": ref["ref_id"],
                "locator": ref["locator"],
                "sha256": digest,
            }
        )
    if not refs:
        raise MetricSeriesError("source_refs 不能为空")
    encoded = [canonical_metric_series_bytes(ref) for ref in refs]
    if len(set(encoded)) != len(encoded):
        raise MetricSeriesError("source_refs 不得重复")
    return [ref for _, ref in sorted(zip(encoded, refs), key=lambda item: item[0])]


def _normalize_ranking_scope(scope: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if scope is None:
        return {"scope_kind": "not_ranked", "candidate_count": None, "filter_rules": []}
    if not isinstance(scope, Mapping):
        raise MetricSeriesError("ranking_scope 必须是映射")
    _require_exact_keys(scope, ("scope_kind", "candidate_count", "filter_rules"), "ranking_scope")
    kind = scope["scope_kind"]
    candidate_count = scope["candidate_count"]
    rules = _sequence(scope["filter_rules"], "ranking_scope.filter_rules")
    if any(not isinstance(rule, str) or not rule for rule in rules):
        raise MetricSeriesError("ranking_scope.filter_rules 只能包含非空字符串")
    if kind == "not_ranked":
        if candidate_count is not None or rules:
            raise MetricSeriesError("not_ranked 不得带候选数或过滤规则")
    elif kind == "global_all_subjects":
        if isinstance(candidate_count, bool) or not isinstance(candidate_count, int) or candidate_count < 1:
            raise MetricSeriesError("global_all_subjects 必须带正候选数")
    elif kind in {"operational_asn_cohort", "explicit_subject_set"}:
        if (
            isinstance(candidate_count, bool)
            or not isinstance(candidate_count, int)
            or candidate_count < 1
            or not rules
        ):
            raise MetricSeriesError("受限排名必须带正候选数和明确过滤规则")
    else:
        raise MetricSeriesError("ranking_scope.scope_kind 非法")
    return {
        "scope_kind": kind,
        "candidate_count": candidate_count,
        "filter_rules": sorted(rules),
    }


def _nonnegative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MetricSeriesError(f"{field} 必须是非负整数")
    return value


def _nonnegative_number(value: Any, field: str) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricSeriesError(f"{field} 必须是非负有限数值")
    if not math.isfinite(value) or value < 0:
        raise MetricSeriesError(f"{field} 必须是非负有限数值")
    # 规范化 0.0/-0.0，避免同一零值生成不同 JSON 字节。
    return 0 if value == 0 else value


def _normalize_incident_ids(value: Any, field: str) -> Tuple[str, ...]:
    incident_ids = _sequence(value, field)
    if any(not isinstance(item, str) or not INCIDENT_ID_RE.fullmatch(item) for item in incident_ids):
        raise MetricSeriesError(f"{field} 必须只包含已规范 incident_id_v1")
    return tuple(sorted(set(incident_ids)))


def _normalize_concurrency_samples(
    value: Any,
    *,
    field: str,
    bucket_start: datetime,
    entity_kind: str,
) -> Tuple[Tuple[datetime, int], ...]:
    """把身份样本或上游已验证的 distinct 计数压缩为 ``(time,count)``。

    候选生成器在百万级历史区间上维护规范身份 active set。若把该 set 在每个
    180 秒点复制进五分钟行，内存会与“事件持续时长 × 身份数”成比例。计数
    形态只允许携带固定的上游身份校验声明；普通调用方仍可传 subject_ids，
    本函数逐个规范化、去重后立即丢弃身份列表。
    """

    samples: List[Tuple[datetime, int]] = []
    for index, sample in enumerate(_sequence(value, field)):
        if not isinstance(sample, Mapping):
            raise MetricSeriesError(f"{field}[{index}] 必须是映射")
        keys = set(sample)
        identity_fields = {"time", "subject_ids"}
        count_fields = {"time", "distinct_subject_count", "identity_validation"}
        if keys == identity_fields:
            sample_kind = "identities"
        elif keys == count_fields:
            sample_kind = "validated_count"
        else:
            raise MetricSeriesError(
                f"{field}[{index}] 必须提供 subject_ids，或提供固定上游身份校验的 distinct_subject_count"
            )
        sample_time = _parse_time(sample["time"], f"{field}[{index}].time", aligned=False)
        if not bucket_start <= sample_time < bucket_start + timedelta(seconds=GRANULARITY_SECONDS):
            raise MetricSeriesError(f"{field}[{index}].time 不在所属五分钟槽内")
        if int(sample_time.timestamp()) % 180:
            raise MetricSeriesError(f"{field}[{index}].time 未对齐 180 秒并发采样网格")
        if sample_kind == "validated_count":
            if sample["identity_validation"] != "d2_stable_incident_interval_index_v1":
                raise MetricSeriesError(f"{field}[{index}].identity_validation 非法")
            count = _nonnegative_integer(
                sample["distinct_subject_count"],
                f"{field}[{index}].distinct_subject_count",
            )
        else:
            subject_ids = _sequence(sample["subject_ids"], f"{field}[{index}].subject_ids")
            normalized_ids = set()
            for subject_id in subject_ids:
                if not isinstance(subject_id, str):
                    raise MetricSeriesError(f"{field}[{index}].subject_ids 含非字符串")
                if entity_kind == "asn":
                    if not ASN_RE.fullmatch(subject_id):
                        raise MetricSeriesError(f"{field}[{index}] 含非规范 ASN")
                else:
                    try:
                        canonical = str(ipaddress.ip_network(subject_id, strict=True))
                    except ValueError as error:
                        raise MetricSeriesError(f"{field}[{index}] 含非法前缀") from error
                    if canonical != subject_id:
                        raise MetricSeriesError(f"{field}[{index}] 含非规范前缀")
                normalized_ids.add(subject_id)
            count = len(normalized_ids)
        samples.append((sample_time, count))
    sample_times = [sample[0] for sample in samples]
    if len(set(sample_times)) != len(sample_times):
        raise MetricSeriesError(f"{field} 含重复或重叠采样时点")
    return tuple(sorted(samples))


def _expected_row_fields(input_kind: str) -> Tuple[str, ...]:
    if input_kind == "announce":
        return ("time", "announ_num")
    if input_kind == "withdraw":
        return ("time", "withdraw_num")
    if input_kind in {"update_total", "withdraw_ratio"}:
        return ("time", "announ_num", "withdraw_num")
    if input_kind in RESOURCE_INPUT_KINDS:
        return ("time", input_kind)
    if input_kind == "incident_ids":
        return ("time", "incident_ids")
    if input_kind in {"prefix_concurrency_samples", "asn_concurrency_samples"}:
        return ("time", "concurrency_samples")
    raise MetricSeriesError("内部指标输入类型未实现")


def _normalize_rows(
    rows: Any,
    *,
    input_kind: str,
    window_start: datetime,
    window_end: datetime,
    source_available: frozenset,
    processing_gaps: frozenset,
) -> Dict[datetime, Dict[str, Any]]:
    normalized: Dict[datetime, Dict[str, Any]] = {}
    expected_fields = _expected_row_fields(input_kind)
    for index, row in enumerate(_sequence(rows, "subject_rows")):
        if not isinstance(row, Mapping):
            raise MetricSeriesError(f"subject_rows[{index}] 必须是映射")
        _require_exact_keys(row, expected_fields, f"subject_rows[{index}]")
        slot = _parse_time(row["time"], f"subject_rows[{index}].time", aligned=True)
        if not window_start <= slot < window_end:
            raise MetricSeriesError(f"subject_rows[{index}] 越出半开窗口")
        if slot in normalized:
            raise MetricSeriesError("subject_rows 含重复或重叠槽")
        if slot not in source_available:
            raise MetricSeriesError("subject_rows 不能落在 source_unavailable 槽")
        if slot in processing_gaps:
            raise MetricSeriesError("subject_rows 与 processing_gap 槽冲突")
        payload: Dict[str, Any] = {"time": _utc_text(slot)}
        if input_kind == "announce":
            payload["announ_num"] = _nonnegative_integer(row["announ_num"], "announ_num")
        elif input_kind == "withdraw":
            payload["withdraw_num"] = _nonnegative_integer(row["withdraw_num"], "withdraw_num")
        elif input_kind in {"update_total", "withdraw_ratio"}:
            payload["announ_num"] = _nonnegative_integer(row["announ_num"], "announ_num")
            payload["withdraw_num"] = _nonnegative_integer(row["withdraw_num"], "withdraw_num")
        elif input_kind in RESOURCE_INPUT_KINDS:
            observed = row[input_kind]
            payload[input_kind] = (
                None if observed is None else _nonnegative_number(observed, input_kind)
            )
        elif input_kind == "incident_ids":
            payload["incident_ids"] = _normalize_incident_ids(
                row["incident_ids"], f"subject_rows[{index}].incident_ids"
            )
        elif input_kind in {"prefix_concurrency_samples", "asn_concurrency_samples"}:
            payload["concurrency_samples"] = _normalize_concurrency_samples(
                row["concurrency_samples"],
                field=f"subject_rows[{index}].concurrency_samples",
                bucket_start=slot,
                entity_kind="prefix" if input_kind.startswith("prefix") else "asn",
            )
        normalized[slot] = payload
    return normalized


def _normalize_metric_missing_slots(
    values: Any,
    *,
    metric_name: str,
    window_start: datetime,
    window_end: datetime,
    source_available: frozenset,
    processing_gaps: frozenset,
    rows: Mapping[datetime, Mapping[str, Any]],
) -> Dict[datetime, Dict[str, str]]:
    """校验调用方逐槽声明的指标级缺失。

    这个接口不接受 ``source_unavailable`` 或 ``processing_gap``；两者必须继续
    由各自的槽集合表达。显式缺失也不得覆盖已观测行。这样可以保证每个槽只
    有一个互斥分类，并阻止普通指标把未知值便捷地降级成 ``legacy_unknown``。
    """

    allowed_states = EXPLICIT_METRIC_MISSING_STATES.get(metric_name, frozenset())
    normalized: Dict[datetime, Dict[str, str]] = {}
    for index, item in enumerate(_sequence(values, "metric_missing_slots")):
        if not isinstance(item, Mapping):
            raise MetricSeriesError(f"metric_missing_slots[{index}] 必须是映射")
        _require_exact_keys(
            item,
            ("time", "value_state", "missing_reason"),
            f"metric_missing_slots[{index}]",
        )
        slot = _parse_time(
            item["time"], f"metric_missing_slots[{index}].time", aligned=True
        )
        if not window_start <= slot < window_end:
            raise MetricSeriesError(f"metric_missing_slots[{index}] 越出半开窗口")
        if slot in normalized:
            raise MetricSeriesError("metric_missing_slots 含重复或重叠槽")
        state = item["value_state"]
        reason = item["missing_reason"]
        if state not in allowed_states or reason != state:
            raise MetricSeriesError(
                f"{metric_name} 不允许该显式 metric missing 状态/原因"
            )
        if slot not in source_available:
            raise MetricSeriesError(
                "metric_missing_slots 不能与 source_unavailable 槽冲突"
            )
        if slot in processing_gaps:
            raise MetricSeriesError(
                "metric_missing_slots 不能与 processing_gap 槽冲突"
            )
        if slot in rows:
            raise MetricSeriesError("metric_missing_slots 不能覆盖已观测 subject_rows")
        normalized[slot] = {"value_state": state, "missing_reason": reason}
    return normalized


def _missing_point(slot: datetime, state: str, *, ratio: bool) -> Dict[str, Any]:
    return {
        "time": _utc_text(slot),
        "value": None,
        "value_state": state,
        "missing_reason": state,
        "formula_inputs": (
            {"numerator_withdraw_count": None, "denominator_update_total": None}
            if ratio
            else None
        ),
    }


def _observed_point(slot: datetime, value: Any, *, formula_inputs: Any = None) -> Dict[str, Any]:
    return {
        "time": _utc_text(slot),
        "value": value,
        "value_state": "observed_zero" if value == 0 else "observed_nonzero",
        "missing_reason": None,
        "formula_inputs": formula_inputs,
    }


def _point_from_row(slot: datetime, row: Mapping[str, Any], input_kind: str) -> Dict[str, Any]:
    if input_kind == "announce":
        return _observed_point(slot, row["announ_num"])
    if input_kind == "withdraw":
        return _observed_point(slot, row["withdraw_num"])
    if input_kind == "update_total":
        return _observed_point(slot, row["announ_num"] + row["withdraw_num"])
    if input_kind == "withdraw_ratio":
        numerator = row["withdraw_num"]
        denominator = row["announ_num"] + numerator
        inputs = {
            "numerator_withdraw_count": numerator,
            "denominator_update_total": denominator,
        }
        if denominator == 0:
            return {
                "time": _utc_text(slot),
                "value": None,
                "value_state": "not_applicable",
                "missing_reason": "denominator_zero",
                "formula_inputs": inputs,
            }
        return _observed_point(slot, numerator / denominator, formula_inputs=inputs)
    if input_kind in RESOURCE_INPUT_KINDS:
        value = row[input_kind]
        if value is None:
            return _missing_point(slot, "not_observed", ratio=False)
        return _observed_point(slot, value)
    if input_kind == "incident_ids":
        return _observed_point(slot, len(row["incident_ids"]))
    if input_kind in {"prefix_concurrency_samples", "asn_concurrency_samples"}:
        samples = row["concurrency_samples"]
        # 空 sample 集合只说明这个五分钟槽没有可复核的 180 秒采样点，不能
        # 证明并发为 0。真实零值必须至少带一个显式采样点，且该点的
        # subject_ids 为空。这样即使调用方误把“未采样”传成空数组，也只会
        # 得到带原因的 null，而不会越过 P0 的禁止补零边界。
        if not samples:
            return _missing_point(slot, "legacy_unknown", ratio=False)
        value = max((count for _, count in samples), default=0)
        return _observed_point(slot, value)
    raise MetricSeriesError("内部指标输入类型未实现")


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    if denominator == 0:
        return None
    return round(numerator / denominator, 10)


def build_metric_series(
    metric_name: str,
    *,
    subject: Mapping[str, Any],
    collector_scope: Mapping[str, Any],
    window_start: Any,
    window_end_exclusive: Any,
    source_available_slots: Iterable[Any],
    processing_gap_slots: Iterable[Any],
    subject_rows: Any,
    source_refs: Iterable[Mapping[str, Any]],
    generated_at: Any,
    source_parse_failed_slots: Iterable[Any] = (),
    ranking_scope: Optional[Mapping[str, Any]] = None,
    sparse_asn_activity: bool = False,
    metric_missing_slots: Any = (),
) -> Dict[str, Any]:
    """由显式槽分类和对象行构造一个完整五分钟 MetricSeries。

    ``source_available_slots`` 表示原始来源通过准入完整性校验；
    ``source_parse_failed_slots`` 表示发现文件但完整性失败。两者之外的窗口槽
    分类为 ``source_unavailable``。``processing_gap_slots`` 必须是来源可用槽的子集。
    ASN 稀疏表只有在 ``sparse_asn_activity=True`` 且指标属于三个更新计数时，
    才能把“来源存在但对象无行”解释为可证明的 ``observed_zero``。
    ``metric_missing_slots`` 是指标级缺失的显式、互斥声明；P0 当前只允许两项
    并发指标用它表达历史结束时间未保留造成的 ``legacy_unknown``。
    """

    if metric_name not in METRIC_DEFINITIONS:
        raise MetricSeriesError("metric_name 未进入 P0 准入清单")
    definition = METRIC_DEFINITIONS[metric_name]
    normalized_subject = _normalize_subject(subject)
    if not isinstance(sparse_asn_activity, bool):
        raise MetricSeriesError("sparse_asn_activity 必须是布尔值")
    if sparse_asn_activity and (
        normalized_subject["subject_type"] != "asn"
        or metric_name not in SPARSE_ASN_ZERO_METRICS
    ):
        raise MetricSeriesError("ASN 稀疏无行补零只允许三个更新计数指标")

    start = _parse_time(window_start, "window_start", aligned=True)
    end = _parse_time(window_end_exclusive, "window_end_exclusive", aligned=True)
    if start >= end:
        raise MetricSeriesError("半开窗口起点必须早于终点")
    expected_count = int((end - start).total_seconds()) // GRANULARITY_SECONDS
    if start + timedelta(seconds=expected_count * GRANULARITY_SECONDS) != end:
        raise MetricSeriesError("窗口长度必须是 300 秒的整数倍")
    expected_slots = tuple(
        start + timedelta(seconds=index * GRANULARITY_SECONDS)
        for index in range(expected_count)
    )
    expected_set = frozenset(expected_slots)

    source_slots = _normalize_slots(source_available_slots, "source_available_slots")
    parse_failed_slots = _normalize_slots(
        source_parse_failed_slots, "source_parse_failed_slots"
    )
    processing_slots = _normalize_slots(processing_gap_slots, "processing_gap_slots")
    if any(slot not in expected_set for slot in source_slots):
        raise MetricSeriesError("source_available_slots 含窗口外槽")
    source_set = frozenset(source_slots)
    parse_failed_set = frozenset(parse_failed_slots)
    if any(slot not in expected_set for slot in parse_failed_set):
        raise MetricSeriesError("source_parse_failed_slots 含窗口外槽")
    if source_set & parse_failed_set:
        raise MetricSeriesError("source_available_slots 与 source_parse_failed_slots 必须互斥")
    if any(slot not in expected_set for slot in processing_slots):
        raise MetricSeriesError("processing_gap_slots 含窗口外槽")
    processing_set = frozenset(processing_slots)
    if not processing_set.issubset(source_set):
        raise MetricSeriesError("processing_gap_slots 必须是 source_available_slots 的子集")

    rows = _normalize_rows(
        subject_rows,
        input_kind=definition.input_kind,
        window_start=start,
        window_end=end,
        source_available=source_set,
        processing_gaps=processing_set,
    )
    explicit_missing = _normalize_metric_missing_slots(
        metric_missing_slots,
        metric_name=metric_name,
        window_start=start,
        window_end=end,
        source_available=source_set,
        processing_gaps=processing_set,
        rows=rows,
    )

    points: List[Dict[str, Any]] = []
    subject_active_count = 0
    for slot in expected_slots:
        if slot in parse_failed_set:
            points.append(
                _missing_point(
                    slot,
                    "parse_failed",
                    ratio=definition.input_kind == "withdraw_ratio",
                )
            )
            continue
        if slot not in source_set:
            points.append(_missing_point(slot, "source_unavailable", ratio=definition.input_kind == "withdraw_ratio"))
            continue
        if slot in processing_set:
            points.append(_missing_point(slot, "processing_gap", ratio=definition.input_kind == "withdraw_ratio"))
            continue
        if slot in explicit_missing:
            points.append(
                _missing_point(
                    slot,
                    explicit_missing[slot]["value_state"],
                    ratio=definition.input_kind == "withdraw_ratio",
                )
            )
            continue
        row = rows.get(slot)
        if row is None:
            if sparse_asn_activity:
                points.append(_observed_point(slot, 0))
                continue
            if definition.input_kind in RESOURCE_INPUT_KINDS:
                points.append(_missing_point(slot, "not_observed", ratio=False))
                continue
            raise MetricSeriesError(
                "来源存在但对象行缺失；请显式分类 processing_gap，或仅对允许的 ASN 更新计数声明稀疏语义"
            )
        point = _point_from_row(slot, row, definition.input_kind)
        points.append(point)
        # 对象活动表示该对象在稀疏来源中有显式行，与派生值是否可计算分开。
        # 因此撤回率分母为 0 的对象行仍计活动，而 ASN 无行补出的 0 不计活动。
        subject_active_count += 1

    source_observed_count = len(source_set)
    metric_observed_count = sum(
        point["value_state"] in {"observed_nonzero", "observed_zero"} for point in points
    )
    generated = _parse_time(generated_at, "generated_at", aligned=False)
    normalized_refs = _normalize_source_refs(source_refs)

    series = {
        "schema_version": SCHEMA_VERSION,
        "metric_name": metric_name,
        "subject": normalized_subject,
        "collector_scope": _normalize_collector_scope(collector_scope),
        "window": {
            "start": _utc_text(start),
            "end": _utc_text(end),
            "boundary": "[start,end)",
            "timezone": BUSINESS_TIMEZONE,
        },
        "granularity_seconds": GRANULARITY_SECONDS,
        "unit": definition.unit,
        "aggregation": definition.aggregation,
        "formula": definition.formula,
        "formula_version": definition.formula_version,
        "expected_sample_count": expected_count,
        "source_observed_sample_count": source_observed_count,
        "metric_observed_sample_count": metric_observed_count,
        "subject_active_sample_count": subject_active_count,
        "coverage": {
            "source_coverage_ratio": _ratio(source_observed_count, expected_count),
            "metric_coverage_ratio": _ratio(metric_observed_count, expected_count),
            "subject_activity_density": _ratio(subject_active_count, source_observed_count),
            "source_gap_sample_count": expected_count - source_observed_count,
            "processing_gap_sample_count": len(processing_set),
            "classification_complete": True,
        },
        "points": points,
        "source_refs": normalized_refs,
        "generated_at": _utc_text(generated),
        "ranking_scope": _normalize_ranking_scope(ranking_scope),
    }
    # 在返回前执行一次严格 JSON 编码，防止 NaN 或意外对象越过边界。
    canonical_metric_series_bytes(series)
    return series


__all__ = (
    "BUSINESS_TIMEZONE",
    "EXPLICIT_METRIC_MISSING_STATES",
    "GRANULARITY_SECONDS",
    "METRIC_DEFINITIONS",
    "MetricDefinition",
    "MetricSeriesError",
    "SCHEMA_VERSION",
    "SPARSE_ASN_ZERO_METRICS",
    "build_metric_series",
    "canonical_metric_series_bytes",
)
