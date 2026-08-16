"""把同快照国家影响结果组装为严格 ``country-outage-sample/v1``。

本模块不推断 ANNOUNCE/WITHDRAW 或 VP 覆盖。协调器必须把这些槽级观测以
``SlotCount`` 显式传入；若不可得则使用合同化 unknown，而不是默认零。
所有 measure、ratio component 和 ASN 集合都绑定同一个 sample/snapshot ID。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .country_impact import (
    CountrySnapshotImpact,
    MeasuredAsnSet,
    MeasuredValue,
    SameSnapshotRatio,
)
from .state_replay import ReplaySnapshot


_RUN_ID_RE = re.compile(r"^research_run_v1_[0-9a-f]{24}$")
_SNAPSHOT_ID_RE = re.compile(r"^snapshot_v1_[0-9a-f]{24}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REASON_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_OBSERVED = frozenset(("observed", "observed_zero"))
_UNKNOWN = frozenset(
    (
        "unknown_source_gap",
        "unknown_parse_failure",
        "unknown_mapping",
        "unknown_state_gap",
    )
)
_SOURCE_KINDS = frozenset(
    ("state_shard", "route_event_shard", "input_artifact", "mapping_snapshot")
)


class SampleBuildError(ValueError):
    """国家样本输入不能满足同快照和缺失语义合同。"""


@dataclass(frozen=True)
class SlotCount:
    value: Optional[int]
    value_state: str
    missing_reason: Optional[str]

    def __post_init__(self) -> None:
        if self.value_state in _OBSERVED:
            if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 0:
                raise SampleBuildError("已观测 SlotCount 必须是非负整数")
            if self.value_state == "observed_zero" and self.value != 0:
                raise SampleBuildError("observed_zero 必须对应数值零")
            if self.value_state == "observed" and self.value == 0:
                raise SampleBuildError("数值零必须使用 observed_zero")
            if self.missing_reason is not None:
                raise SampleBuildError("已观测 SlotCount 不得携带 missing_reason")
        elif self.value_state in _UNKNOWN:
            if self.value is not None:
                raise SampleBuildError("未知 SlotCount 的 value 必须为 null")
            if not isinstance(self.missing_reason, str) or _REASON_RE.fullmatch(self.missing_reason) is None:
                raise SampleBuildError("未知 SlotCount 必须给出合同化 missing_reason")
        else:
            raise SampleBuildError("SlotCount.value_state 非法")


@dataclass(frozen=True)
class SampleSourceRef:
    ref_type: str
    ref_id: str
    sha256: str

    def __post_init__(self) -> None:
        if self.ref_type not in _SOURCE_KINDS:
            raise SampleBuildError("source ref_type 非法")
        if not isinstance(self.ref_id, str) or _REF_RE.fullmatch(self.ref_id) is None:
            raise SampleBuildError("source ref_id 非法")
        if not isinstance(self.sha256, str) or _SHA256_RE.fullmatch(self.sha256) is None:
            raise SampleBuildError("source sha256 非法")


def observed_slot_count(value: int) -> SlotCount:
    """显式构造真实槽计数；零值不会与 unknown 混淆。"""

    return SlotCount(value, "observed_zero" if value == 0 else "observed", None)


def unknown_slot_count(value_state: str, missing_reason: str) -> SlotCount:
    """显式构造未知槽计数。"""

    return SlotCount(None, value_state, missing_reason)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise SampleBuildError("样本包含不可规范序列化值") from error


def _sample_id(identity: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(dict(identity)).encode("utf-8")).hexdigest()
    return "sample_v1_" + digest[:24]


def _bound(sample_id: str, snapshot_id: str) -> dict[str, str]:
    return {"sample_id": sample_id, "snapshot_id": snapshot_id}


def _missing_reason(value: Optional[str], field: str) -> str:
    if not isinstance(value, str) or _REASON_RE.fullmatch(value) is None:
        raise SampleBuildError(f"{field}.missing_reason 不符合合同")
    return value


def _count_measure(
    measured: MeasuredValue | SlotCount,
    *,
    sample_id: str,
    snapshot_id: str,
    field: str,
) -> dict[str, Any]:
    if isinstance(measured, MeasuredValue) and measured.snapshot_id != snapshot_id:
        raise SampleBuildError(f"{field} snapshot_id 与父样本不一致")
    if not isinstance(measured, (MeasuredValue, SlotCount)):
        raise SampleBuildError(f"{field} 不是可绑定计数")
    value = measured.value
    state = measured.value_state
    reason = measured.missing_reason
    if state in _OBSERVED:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SampleBuildError(f"{field} 已观测计数必须是非负整数")
        if (value == 0) != (state == "observed_zero"):
            raise SampleBuildError(f"{field} 零值状态不一致")
        if reason is not None:
            raise SampleBuildError(f"{field} 已观测值不得携带缺失原因")
    elif state in _UNKNOWN:
        if value is not None:
            raise SampleBuildError(f"{field} 未知值不得补零")
        reason = _missing_reason(reason, field)
    else:
        raise SampleBuildError(f"{field}.value_state 非法")
    return {
        **_bound(sample_id, snapshot_id),
        "value": value,
        "value_state": state,
        "missing_reason": reason,
    }


def _decimal_measure(
    measured: MeasuredValue,
    *,
    sample_id: str,
    snapshot_id: str,
    field: str,
) -> dict[str, Any]:
    if not isinstance(measured, MeasuredValue) or measured.snapshot_id != snapshot_id:
        raise SampleBuildError(f"{field} snapshot_id 与父样本不一致")
    value = measured.value
    state = measured.value_state
    reason = measured.missing_reason
    if state in _OBSERVED:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SampleBuildError(f"{field} 已观测值必须是数值")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise SampleBuildError(f"{field} 已观测值必须是非负有限数")
        if (numeric == 0) != (state == "observed_zero"):
            raise SampleBuildError(f"{field} 零值状态不一致")
        if reason is not None:
            raise SampleBuildError(f"{field} 已观测值不得携带缺失原因")
    elif state in _UNKNOWN:
        if value is not None:
            raise SampleBuildError(f"{field} 未知值不得补零")
        reason = _missing_reason(reason, field)
    else:
        raise SampleBuildError(f"{field}.value_state 非法")
    return {
        **_bound(sample_id, snapshot_id),
        "value": value,
        "value_state": state,
        "missing_reason": reason,
    }


def _ratio_measure(
    ratio: SameSnapshotRatio,
    *,
    sample_id: str,
    snapshot_id: str,
) -> dict[str, Any]:
    if not isinstance(ratio, SameSnapshotRatio) or ratio.snapshot_id != snapshot_id:
        raise SampleBuildError("damaged_asn_ratio snapshot_id 与父样本不一致")
    if ratio.value_state in _OBSERVED:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (ratio.numerator, ratio.denominator)
        ):
            raise SampleBuildError("ratio 分子分母必须是非负整数")
        assert ratio.numerator is not None and ratio.denominator is not None
        if ratio.denominator < 1:
            raise SampleBuildError("ratio denominator 必须至少为 1")
        if isinstance(ratio.value, bool) or not isinstance(ratio.value, (int, float)):
            raise SampleBuildError("ratio value 必须是数值")
        expected = ratio.numerator / ratio.denominator
        if not math.isclose(float(ratio.value), expected, rel_tol=0, abs_tol=1e-15):
            raise SampleBuildError("ratio value 与同快照分子分母不一致")
        if (ratio.numerator == 0) != (ratio.value_state == "observed_zero"):
            raise SampleBuildError("ratio 零值状态不一致")
        if ratio.missing_reason is not None:
            raise SampleBuildError("已观测 ratio 不得携带 missing_reason")
        component = _bound(sample_id, snapshot_id)
        numerator = {**component, "value": ratio.numerator}
        denominator = {**component, "value": ratio.denominator}
        reason = None
    elif ratio.value_state in _UNKNOWN:
        if any(value is not None for value in (ratio.numerator, ratio.denominator, ratio.value)):
            raise SampleBuildError("未知 ratio 的分子、分母和值必须全为 null")
        numerator = None
        denominator = None
        reason = _missing_reason(ratio.missing_reason, "damaged_asn_ratio")
    else:
        raise SampleBuildError("damaged_asn_ratio.value_state 非法")
    return {
        **_bound(sample_id, snapshot_id),
        "numerator": numerator,
        "denominator": denominator,
        "value": ratio.value,
        "value_state": ratio.value_state,
        "missing_reason": reason,
    }


def _asn_set(
    measured: MeasuredAsnSet,
    *,
    sample_id: str,
    snapshot_id: str,
    field: str,
) -> dict[str, Any]:
    if not isinstance(measured, MeasuredAsnSet) or measured.snapshot_id != snapshot_id:
        raise SampleBuildError(f"asn_sets.{field} snapshot_id 与父样本不一致")
    value = measured.value
    state = measured.value_state
    reason = measured.missing_reason
    if state in {"observed", "observed_empty"}:
        if not isinstance(value, tuple) or tuple(sorted(set(value))) != value:
            raise SampleBuildError(f"asn_sets.{field} 必须是去重排序 tuple")
        if any(isinstance(asn, bool) or not isinstance(asn, int) or not 1 <= asn <= 4_294_967_295 for asn in value):
            raise SampleBuildError(f"asn_sets.{field} 含非法 ASN")
        if bool(value) != (state == "observed"):
            raise SampleBuildError(f"asn_sets.{field} 空集合状态不一致")
        if reason is not None:
            raise SampleBuildError(f"asn_sets.{field} 已观测集合不得有缺失原因")
        encoded_value: Any = list(value)
    elif state in _UNKNOWN:
        if value is not None:
            raise SampleBuildError(f"asn_sets.{field} 未知集合必须为 null")
        reason = _missing_reason(reason, f"asn_sets.{field}")
        encoded_value = None
    else:
        raise SampleBuildError(f"asn_sets.{field}.value_state 非法")
    return {
        **_bound(sample_id, snapshot_id),
        "value": encoded_value,
        "value_state": state,
        "missing_reason": reason,
    }


def build_country_outage_sample(
    impact: CountrySnapshotImpact,
    snapshot: ReplaySnapshot,
    *,
    run_id: str,
    collector_id: str,
    announce_count: SlotCount,
    withdraw_count: SlotCount,
    vp_expected_count: SlotCount,
    vp_observed_count: SlotCount,
    source_refs: Iterable[SampleSourceRef],
) -> dict[str, Any]:
    """生成一个引用闭合前可独立 Schema 校验的五分钟国家样本。"""

    if not isinstance(impact, CountrySnapshotImpact) or not isinstance(snapshot, ReplaySnapshot):
        raise SampleBuildError("impact/snapshot 类型非法")
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise SampleBuildError("run_id 非法")
    if not isinstance(collector_id, str) or re.fullmatch(r"^[a-z0-9][a-z0-9._-]*$", collector_id) is None:
        raise SampleBuildError("collector_id 非法")
    if not isinstance(impact.snapshot_id, str) or _SNAPSHOT_ID_RE.fullmatch(impact.snapshot_id) is None:
        raise SampleBuildError("snapshot_id 非法")
    if impact.observed_at != snapshot.slot_end_exclusive_utc:
        raise SampleBuildError("impact observed_at 与五分钟槽结束不一致")
    if impact.continuity_state != snapshot.continuity_state:
        raise SampleBuildError("impact continuity 与状态快照不一致")
    if any(entry.key.collector_id != collector_id for entry in snapshot.entries):
        raise SampleBuildError("状态快照包含其他 collector")
    if impact.cohort_view not in {"compatible", "revised"}:
        raise SampleBuildError("cohort_view 非法")

    if isinstance(source_refs, (str, bytes, Mapping)):
        raise SampleBuildError("source_refs 必须是 SampleSourceRef 流")
    try:
        sources = tuple(source_refs)
    except TypeError as error:
        raise SampleBuildError("source_refs 不可迭代") from error
    if not sources or any(not isinstance(item, SampleSourceRef) for item in sources):
        raise SampleBuildError("source_refs 必须至少包含一条合法引用")
    sources = tuple(sorted(sources, key=lambda item: (item.ref_type, item.ref_id, item.sha256)))
    if len(set(sources)) != len(sources):
        raise SampleBuildError("source_refs 不得重复")

    identity = {
        "schema": "country_outage_sample_id_v1",
        "run_id": run_id,
        "snapshot_id": impact.snapshot_id,
        "collector_id": collector_id,
        "country_code": impact.country_code,
        "cohort_view": impact.cohort_view,
        "slot_start": snapshot.slot_start_utc,
        "slot_end": snapshot.slot_end_exclusive_utc,
    }
    sample_id = _sample_id(identity)
    metrics = impact.metrics
    result = {
        "schema_version": "country-outage-sample/v1",
        "sample_id": sample_id,
        "run_id": run_id,
        "snapshot_id": impact.snapshot_id,
        "collector_id": collector_id,
        "country_code": impact.country_code,
        "cohort_view": impact.cohort_view,
        "slot": {
            "start": snapshot.slot_start_utc,
            "end": snapshot.slot_end_exclusive_utc,
            "boundary": "[start,end)",
            "granularity_seconds": 300,
        },
        "continuity_state": impact.continuity_state,
        "metrics": {
            "visible_asn_count": _count_measure(metrics.visible_asn_count, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="visible_asn_count"),
            "damaged_asn_count": _count_measure(metrics.damaged_asn_count, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="damaged_asn_count"),
            "baseline_asn_count": _count_measure(metrics.baseline_asn_count, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="baseline_asn_count"),
            "visible_ipv4_prefix_count": _count_measure(metrics.visible_ipv4_prefix_count, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="visible_ipv4_prefix_count"),
            "visible_ipv6_prefix_count": _count_measure(metrics.visible_ipv6_prefix_count, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="visible_ipv6_prefix_count"),
            "visible_ipv4_address_union": _count_measure(metrics.visible_ipv4_address_union, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="visible_ipv4_address_union"),
            "visible_ipv4_24_equivalent": _decimal_measure(metrics.visible_ipv4_24_equivalent, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="visible_ipv4_24_equivalent"),
            "visible_ipv6_48_equivalent": _decimal_measure(metrics.visible_ipv6_48_equivalent, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="visible_ipv6_48_equivalent"),
            "announce_count": _count_measure(announce_count, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="announce_count"),
            "withdraw_count": _count_measure(withdraw_count, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="withdraw_count"),
            "vp_expected_count": _count_measure(vp_expected_count, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="vp_expected_count"),
            "vp_observed_count": _count_measure(vp_observed_count, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="vp_observed_count"),
            "damaged_asn_ratio": _ratio_measure(metrics.damaged_asn_ratio, sample_id=sample_id, snapshot_id=impact.snapshot_id),
        },
        "asn_sets": {
            "visible": _asn_set(impact.visible_asns, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="visible"),
            "damaged": _asn_set(impact.damaged_asns, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="damaged"),
            "baseline": _asn_set(impact.baseline_asns, sample_id=sample_id, snapshot_id=impact.snapshot_id, field="baseline"),
        },
        "source_refs": [
            {"ref_type": item.ref_type, "ref_id": item.ref_id, "sha256": item.sha256}
            for item in sources
        ],
    }
    # 规范 JSON 检查可同时拒绝 NaN/Infinity；返回普通 dict 供 JSON Schema 校验。
    _canonical_json(result)
    return result


__all__ = (
    "SampleBuildError",
    "SampleSourceRef",
    "SlotCount",
    "build_country_outage_sample",
    "observed_slot_count",
    "unknown_slot_count",
)
