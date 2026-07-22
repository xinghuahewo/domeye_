"""把五分钟国家影响序列聚合为严格的逐 episode/ASN 合同记录。

本模块只消费已经生成的 ``CountrySnapshotImpact``、episode 识别结果、
合同样本映射、冻结 cohort/国家映射和调用方显式提供的 RouteEvent 索引。
它不读取文件、数据库或网络，也不会根据前缀或时间猜测原始记录坐标。

关键语义：

* trigger、episode 峰值、累计和观察终点集合分别计算，不能只比较人数；
* 动态 ASN 只从 ``first_seen_at`` 起进入逐 ASN 人口，未知状态不补零；
* 合同四个成员字段是“已确认受损集合”布尔值；unknown 样本不会产生正成员，
  其不确定性由对应 family 的 unknown measure/prefixSet/visibility 完整保留；
* IPv4 损失按去重地址数表达，IPv6 按去重 ``/48`` 等价值表达；
* ``overall_classification`` 与地址族 visibility 均绑定 episode 峰值快照；
* evidence link 只能从真实 ``ResearchRouteEvent`` 的稳定 raw ref 生成。
  ``CountrySnapshotImpact`` 自身没有完整 raw 坐标，因此索引缺失时允许输出
  空数组，但绝不生成占位 ID。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import re
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from ...route_event import ParsedRouteElement, artifact_id_v1, route_event_id_v1
from .country_impact import (
    AddressFamilyDamage,
    AsnDamage,
    CountryCohort,
    CountryMappingView,
    CountrySnapshotImpact,
    MOAS_SEMANTICS,
    derive_origin_asns,
)
from .episodes import EpisodeDetection
from .state_replay import ResearchRouteEvent, build_research_route_event


_RUN_ID_RE = re.compile(r"^research_run_v1_[0-9a-f]{24}$")
_EPISODE_ID_RE = re.compile(r"^episode_v1_[0-9a-f]{24}$")
_SAMPLE_ID_RE = re.compile(r"^sample_v1_[0-9a-f]{24}$")
_SNAPSHOT_ID_RE = re.compile(r"^snapshot_v1_[0-9a-f]{24}$")
_ROUTE_EVENT_ID_RE = re.compile(r"^rte_v1_[0-9a-f]{32}$")
_UNKNOWN_STATES = frozenset(
    (
        "unknown_source_gap",
        "unknown_parse_failure",
        "unknown_mapping",
        "unknown_state_gap",
    )
)
_OBSERVED_STATES = frozenset(("observed", "observed_zero"))
_AFIS = ("ipv4", "ipv6")
_AFI_SAFI = {"ipv4": "ipv4_unicast", "ipv6": "ipv6_unicast"}


class EpisodeAsBuildError(ValueError):
    """逐 ASN 输入不能在冻结语义下安全聚合。"""


@dataclass(frozen=True)
class _SampleImpact:
    sample_id: str
    snapshot_id: str
    start: str
    end: str
    start_epoch: int
    end_epoch: int
    impact: CountrySnapshotImpact


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
        raise EpisodeAsBuildError("逐 ASN 身份包含不可规范序列化值") from error


def _stable_id(prefix: str, identity: Mapping[str, Any], length: int = 24) -> str:
    digest = hashlib.sha256(_canonical_json(dict(identity)).encode("utf-8")).hexdigest()
    return prefix + digest[:length]


def _utc_second(value: object, field: str) -> Tuple[str, int]:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise EpisodeAsBuildError(f"{field} 必须是秒级 UTC Z 时间")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise EpisodeAsBuildError(f"{field} 不是合法秒级 UTC 时间") from error
    return value, int(parsed.timestamp())


def _asn(value: object, field: str = "asn") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EpisodeAsBuildError(f"{field} 必须是整数")
    if not 1 <= value <= 4_294_967_295:
        raise EpisodeAsBuildError(f"{field} 越出 1..4294967295")
    return value


def _prefix(value: object, afi: str, field: str) -> str:
    if not isinstance(value, str):
        raise EpisodeAsBuildError(f"{field} 必须是 CIDR 字符串")
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as error:
        raise EpisodeAsBuildError(f"{field} 不是规范 CIDR") from error
    if network.version != (4 if afi == "ipv4" else 6):
        raise EpisodeAsBuildError(f"{field} 与地址族不一致")
    return network.compressed


def _sample_bindings(
    episode: EpisodeDetection,
    samples_by_id: Mapping[str, Mapping[str, Any]],
    impacts_by_sample_id: Mapping[str, CountrySnapshotImpact],
) -> Tuple[_SampleImpact, ...]:
    if not isinstance(samples_by_id, Mapping) or not isinstance(
        impacts_by_sample_id, Mapping
    ):
        raise EpisodeAsBuildError("样本与 impact 必须按 sample_id 显式映射")
    required_ids = tuple(episode.supporting_sample_ids)
    if not required_ids or len(required_ids) != len(set(required_ids)):
        raise EpisodeAsBuildError("episode supporting_sample_ids 必须非空且唯一")
    bindings = []
    for sample_id in required_ids:
        if not isinstance(sample_id, str) or _SAMPLE_ID_RE.fullmatch(sample_id) is None:
            raise EpisodeAsBuildError("episode 含非法 sample_id")
        sample = samples_by_id.get(sample_id)
        impact = impacts_by_sample_id.get(sample_id)
        if not isinstance(sample, Mapping):
            raise EpisodeAsBuildError(f"缺少合同样本映射：{sample_id}")
        if not isinstance(impact, CountrySnapshotImpact):
            raise EpisodeAsBuildError(f"缺少 CountrySnapshotImpact：{sample_id}")
        if sample.get("sample_id") != sample_id:
            raise EpisodeAsBuildError(f"样本键与 sample_id 不一致：{sample_id}")
        if sample.get("run_id") != episode.run_id:
            raise EpisodeAsBuildError(f"样本 run_id 与 episode 不一致：{sample_id}")
        if sample.get("collector_id") != episode.collector_id:
            raise EpisodeAsBuildError(
                f"样本 collector_id 与 episode 不一致：{sample_id}"
            )
        if sample.get("country_code") != episode.country_code:
            raise EpisodeAsBuildError(f"样本 country_code 与 episode 不一致：{sample_id}")
        if sample.get("cohort_view") != episode.cohort_view:
            raise EpisodeAsBuildError(f"样本 cohort_view 与 episode 不一致：{sample_id}")
        snapshot_id = sample.get("snapshot_id")
        if not isinstance(snapshot_id, str) or _SNAPSHOT_ID_RE.fullmatch(snapshot_id) is None:
            raise EpisodeAsBuildError(f"样本 snapshot_id 非法：{sample_id}")
        if snapshot_id != impact.snapshot_id:
            raise EpisodeAsBuildError(f"样本与 impact 的 snapshot_id 不一致：{sample_id}")
        if (
            impact.country_code != episode.country_code
            or impact.cohort_view != episode.cohort_view
        ):
            raise EpisodeAsBuildError(f"impact 与 episode 国家口径不一致：{sample_id}")
        slot = sample.get("slot")
        if not isinstance(slot, Mapping):
            raise EpisodeAsBuildError(f"样本缺少 slot：{sample_id}")
        start, start_epoch = _utc_second(slot.get("start"), f"{sample_id}.slot.start")
        end, end_epoch = _utc_second(slot.get("end"), f"{sample_id}.slot.end")
        if end_epoch - start_epoch != 300:
            raise EpisodeAsBuildError(f"样本槽必须严格为五分钟：{sample_id}")
        if slot.get("boundary") != "[start,end)":
            raise EpisodeAsBuildError(f"样本槽边界必须是 [start,end)：{sample_id}")
        if impact.observed_at != end:
            raise EpisodeAsBuildError(f"impact observed_at 未绑定样本槽结束：{sample_id}")
        bindings.append(
            _SampleImpact(
                sample_id=sample_id,
                snapshot_id=snapshot_id,
                start=start,
                end=end,
                start_epoch=start_epoch,
                end_epoch=end_epoch,
                impact=impact,
            )
        )
    ordered = tuple(sorted(bindings, key=lambda item: item.start_epoch))
    if tuple(item.sample_id for item in ordered) != required_ids:
        raise EpisodeAsBuildError("episode supporting_sample_ids 必须按时间严格递增")
    if len({item.snapshot_id for item in ordered}) != len(ordered):
        raise EpisodeAsBuildError("episode supporting sample 不得复用 snapshot_id")
    for left, right in zip(ordered, ordered[1:]):
        if left.end_epoch != right.start_epoch:
            raise EpisodeAsBuildError("episode 样本必须是连续五分钟槽")
    return ordered


def _point_by_start(
    samples: Sequence[_SampleImpact], target: str, field: str
) -> _SampleImpact:
    _text, epoch = _utc_second(target, field)
    matches = tuple(item for item in samples if item.start_epoch == epoch)
    if len(matches) != 1:
        raise EpisodeAsBuildError(f"{field} 未唯一绑定 supporting sample")
    return matches[0]


def _point_by_end(
    samples: Sequence[_SampleImpact], target: str, field: str
) -> _SampleImpact:
    _text, epoch = _utc_second(target, field)
    matches = tuple(item for item in samples if item.end_epoch == epoch)
    if len(matches) != 1:
        raise EpisodeAsBuildError(f"{field} 未唯一绑定 supporting sample")
    return matches[0]


def _impact_state(impact: CountrySnapshotImpact) -> Tuple[str, Optional[str]]:
    measured = impact.metrics.damaged_asn_count
    if measured.snapshot_id != impact.snapshot_id:
        raise EpisodeAsBuildError(
            "impact damaged_asn_count 未绑定当前 snapshot_id"
        )
    state = measured.value_state
    if state in _OBSERVED_STATES:
        return "observed", None
    if state in _UNKNOWN_STATES:
        reason = measured.missing_reason
        if not isinstance(reason, str) or not reason:
            raise EpisodeAsBuildError("unknown impact 必须携带 missing_reason")
        return state, reason
    raise EpisodeAsBuildError("impact damaged_asn_count.value_state 非法")


def _damage_for(impact: CountrySnapshotImpact, asn: int) -> Optional[AsnDamage]:
    state, _reason = _impact_state(impact)
    if state != "observed":
        return None
    matches = tuple(value for value in impact.asn_impacts if value.asn == asn)
    if len(matches) > 1:
        raise EpisodeAsBuildError(f"impact 重复 ASN：{asn}")
    return matches[0] if matches else None


def _family_for(damage: AsnDamage, afi: str) -> AddressFamilyDamage:
    matches = tuple(item for item in damage.address_families if item.afi == afi)
    if len(matches) != 1:
        raise EpisodeAsBuildError(f"ASN {damage.asn} 缺少唯一 {afi} 影响")
    return matches[0]


def _active_at(cohort: CountryCohort, asn: int, observed_at: str) -> bool:
    matches = tuple(value for value in cohort.member_first_seen if value[0] == asn)
    if len(matches) != 1:
        raise EpisodeAsBuildError(f"cohort 缺少唯一 ASN 生效时间：{asn}")
    first_seen = matches[0][1]
    if first_seen is None:
        return True
    _text, first_epoch = _utc_second(first_seen, f"AS{asn}.first_seen_at")
    _observed, observed_epoch = _utc_second(observed_at, "impact.observed_at")
    return first_epoch <= observed_epoch


def _empty_family(afi: str) -> AddressFamilyDamage:
    return AddressFamilyDamage(
        afi=afi,
        reference_prefixes=(),
        visible_prefixes=(),
        lost_prefixes=(),
        lost_equivalent=0,
        fully_invisible=False,
        value_state="observed",
        missing_reason=None,
        moas_prefixes=(),
    )


def _family_at(
    sample: _SampleImpact, cohort: CountryCohort, asn: int, afi: str
) -> Tuple[Optional[AddressFamilyDamage], str, Optional[str]]:
    state, reason = _impact_state(sample.impact)
    if not _active_at(cohort, asn, sample.impact.observed_at):
        return _empty_family(afi), "observed", None
    if state != "observed":
        return None, state, reason
    damage = _damage_for(sample.impact, asn)
    if damage is None:
        raise EpisodeAsBuildError(
            f"已生效 ASN {asn} 在 observed impact 中缺少逐 ASN 明细"
        )
    if damage.value_state not in _OBSERVED_STATES | {"observed"}:
        if damage.value_state in _UNKNOWN_STATES:
            return None, damage.value_state, damage.missing_reason
        raise EpisodeAsBuildError(f"ASN {asn} value_state 非法")
    family = _family_for(damage, afi)
    if family.value_state in _UNKNOWN_STATES:
        return None, family.value_state, family.missing_reason
    if family.value_state != "observed":
        raise EpisodeAsBuildError(f"ASN {asn} {afi} value_state 非法")
    for name, values in (
        ("reference_prefixes", family.reference_prefixes),
        ("visible_prefixes", family.visible_prefixes),
        ("lost_prefixes", family.lost_prefixes),
    ):
        if values is None:
            raise EpisodeAsBuildError(f"ASN {asn} {afi}.{name} 已观测却为 null")
        normalized = tuple(sorted({_prefix(value, afi, name) for value in values}))
        if normalized != values:
            raise EpisodeAsBuildError(f"ASN {asn} {afi}.{name} 必须规范、去重、排序")
    return family, "observed", None


def _measure(value: int | float) -> Dict[str, Any]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EpisodeAsBuildError("measure 必须是数值")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise EpisodeAsBuildError("measure 必须是非负有限数")
    return {
        "value": value,
        "value_state": "observed_zero" if numeric == 0 else "observed",
        "missing_reason": None,
    }


def _unknown_measure(state: str, reason: Optional[str]) -> Dict[str, Any]:
    if state not in _UNKNOWN_STATES or not isinstance(reason, str) or not reason:
        raise EpisodeAsBuildError("未知 measure 状态或原因非法")
    return {"value": None, "value_state": state, "missing_reason": reason}


def _prefix_set(values: Iterable[str], afi: str) -> Dict[str, Any]:
    normalized = tuple(sorted({_prefix(value, afi, "prefix set") for value in values}))
    return {
        "value": list(normalized),
        "value_state": "observed" if normalized else "observed_empty",
        "missing_reason": None,
    }


def _unknown_prefix_set(state: str, reason: Optional[str]) -> Dict[str, Any]:
    if state not in _UNKNOWN_STATES or not isinstance(reason, str) or not reason:
        raise EpisodeAsBuildError("未知 prefixSet 状态或原因非法")
    return {"value": None, "value_state": state, "missing_reason": reason}


def _visibility(
    family: Optional[AddressFamilyDamage], state: str, reason: Optional[str]
) -> Dict[str, Any]:
    if state == "observed":
        if family is None or not isinstance(family.fully_invisible, bool):
            raise EpisodeAsBuildError("已观测 visibility 必须是布尔值")
        return {
            "fully_invisible": family.fully_invisible,
            "visibility_state": "observed",
            "missing_reason": None,
        }
    if state not in _UNKNOWN_STATES or not isinstance(reason, str) or not reason:
        raise EpisodeAsBuildError("未知 visibility 状态或原因非法")
    return {
        "fully_invisible": None,
        "visibility_state": state,
        "missing_reason": reason,
    }


def _lost_equivalent(prefixes: Sequence[str], afi: str) -> int | float:
    networks = [ipaddress.ip_network(_prefix(value, afi, "lost prefix")) for value in prefixes]
    address_count = sum(
        network.num_addresses for network in ipaddress.collapse_addresses(networks)
    )
    if afi == "ipv4":
        return address_count
    denominator = 1 << 80
    if address_count % denominator == 0:
        return address_count // denominator
    return address_count / denominator


def _family_contract(
    *,
    afi: str,
    baseline_prefix_count: int,
    trigger: Tuple[Optional[AddressFamilyDamage], str, Optional[str]],
    peak: Tuple[Optional[AddressFamilyDamage], str, Optional[str]],
    cumulative: Tuple[Optional[Tuple[str, ...]], str, Optional[str]],
    observation_end: Tuple[Optional[AddressFamilyDamage], str, Optional[str]],
) -> Dict[str, Any]:
    peak_family, peak_state, peak_reason = peak
    if peak_state == "observed":
        if peak_family is None or peak_family.lost_prefixes is None:
            raise EpisodeAsBuildError("峰值已观测但缺少 lost_prefixes")
        lost_prefixes = peak_family.lost_prefixes
        lost_count = _measure(len(lost_prefixes))
        lost_equivalent = _measure(_lost_equivalent(lost_prefixes, afi))
    else:
        lost_count = _unknown_measure(peak_state, peak_reason)
        lost_equivalent = _unknown_measure(peak_state, peak_reason)

    def family_prefixes(
        value: Tuple[Optional[AddressFamilyDamage], str, Optional[str]]
    ) -> Dict[str, Any]:
        family, state, reason = value
        if state == "observed":
            if family is None or family.lost_prefixes is None:
                raise EpisodeAsBuildError("已观测地址族缺少 lost_prefixes")
            return _prefix_set(family.lost_prefixes, afi)
        return _unknown_prefix_set(state, reason)

    cumulative_values, cumulative_state, cumulative_reason = cumulative
    cumulative_contract = (
        _prefix_set(cumulative_values or (), afi)
        if cumulative_state == "observed"
        else _unknown_prefix_set(cumulative_state, cumulative_reason)
    )
    return {
        "afi": afi,
        "baseline_prefix_count": _measure(baseline_prefix_count),
        "lost_prefix_count_at_peak": lost_count,
        "lost_equivalent_at_peak": lost_equivalent,
        "equivalent_unit": (
            "ipv4_equivalent_address" if afi == "ipv4" else "ipv6_48_equivalent"
        ),
        "visibility": _visibility(peak_family, peak_state, peak_reason),
        "trigger_prefixes": family_prefixes(trigger),
        "peak_prefixes": family_prefixes(peak),
        "cumulative_prefixes": cumulative_contract,
        "observation_end_prefixes": family_prefixes(observation_end),
        "moas_semantics": MOAS_SEMANTICS,
    }


def _overall_classification(
    peak_families: Mapping[str, Tuple[Optional[AddressFamilyDamage], str, Optional[str]]]
) -> str:
    values = []
    damaged = False
    for afi in _AFIS:
        family, state, _reason = peak_families[afi]
        if state != "observed" or family is None:
            return "unknown"
        values.append(bool(family.fully_invisible))
        damaged = damaged or bool(family.lost_prefixes)
    ipv4, ipv6 = values
    if ipv4 and ipv6:
        return "dual_stack_fully_invisible"
    if ipv4:
        return "ipv4_only_fully_invisible"
    if ipv6:
        return "ipv6_only_fully_invisible"
    return "partially_visible" if damaged else "not_affected"


def _raw_record_ref_id(event: ResearchRouteEvent) -> str:
    return _stable_id(
        "raw_v1_",
        {
            "schema": "raw_record_ref_id_v1",
            "file_sha256": event.file_sha256,
            "record_ordinal": event.record_ordinal,
            "element_ordinal": event.element_ordinal,
        },
        32,
    )


def _validated_route_event(event: object) -> ResearchRouteEvent:
    if not isinstance(event, ResearchRouteEvent):
        raise EpisodeAsBuildError("evidence index 只能包含 ResearchRouteEvent")
    expected_route_id = route_event_id_v1(
        event.file_sha256, event.record_ordinal, event.element_ordinal
    )
    if event.route_event_id != expected_route_id:
        raise EpisodeAsBuildError("RouteEvent ID 与 raw 坐标不一致")
    if event.artifact_id != artifact_id_v1(event.file_sha256):
        raise EpisodeAsBuildError("RouteEvent artifact_id 与文件哈希不一致")
    if _ROUTE_EVENT_ID_RE.fullmatch(event.route_event_id) is None:
        raise EpisodeAsBuildError("RouteEvent ID 格式非法")
    try:
        rebuilt = build_research_route_event(
            artifact_id=event.artifact_id,
            file_sha256=event.file_sha256,
            collector_id=event.collector_id,
            artifact_slot_utc=event.artifact_slot_utc,
            record_ordinal=event.record_ordinal,
            element_ordinal=event.element_ordinal,
            element=ParsedRouteElement(
                event_time_utc=event.event_time_utc,
                peer_ip=event.peer_ip,
                peer_asn=event.peer_asn,
                action=event.action,
                prefix=event.prefix,
                afi_safi=event.afi_safi,
                as_path=event.as_path,
                quality_flags=event.quality_flags,
            ),
        )
    except ValueError as error:
        raise EpisodeAsBuildError("RouteEvent 规范字段或 raw 坐标非法") from error
    if rebuilt != event:
        raise EpisodeAsBuildError("RouteEvent 稳定身份或规范字段发生冲突")
    return rebuilt


def _evidence_links(
    *,
    asn: int,
    affected_prefixes: Mapping[str, set[str]],
    samples: Sequence[_SampleImpact],
    collector_id: str,
    route_events_by_id: Mapping[str, ResearchRouteEvent],
    prefix_change_event_ids: Mapping[Tuple[int, str, str], Sequence[str]],
) -> list[Dict[str, Any]]:
    candidate_ids = set()
    # 可见 PrefixOriginRelation 只提供 RouteEvent ID；只有索引能补齐 raw 坐标。
    for sample in samples:
        for relation in sample.impact.prefix_relations:
            if relation.afi not in _AFIS or relation.prefix not in affected_prefixes.get(
                relation.afi, set()
            ):
                continue
            for observation in relation.observations:
                if asn in observation.origins:
                    candidate_ids.add(observation.route_event_id)

    explicit_ids = set()
    for afi in _AFIS:
        for prefix in affected_prefixes.get(afi, set()):
            key = (asn, afi, prefix)
            values = prefix_change_event_ids.get(key, ())
            if isinstance(values, (str, bytes, Mapping)):
                raise EpisodeAsBuildError("prefix_change_event_ids 的值必须是 ID 序列")
            for route_id in values:
                if not isinstance(route_id, str) or _ROUTE_EVENT_ID_RE.fullmatch(route_id) is None:
                    raise EpisodeAsBuildError("prefix_change_event_ids 含非法 RouteEvent ID")
                explicit_ids.add(route_id)
                candidate_ids.add(route_id)

    links = {}
    for route_id in sorted(candidate_ids):
        event = route_events_by_id.get(route_id)
        if event is None:
            if route_id in explicit_ids:
                raise EpisodeAsBuildError(f"显式前缀变化缺少 RouteEvent/raw ref：{route_id}")
            # impact 关系可独立发布；没有完整索引时不得伪造 raw ref。
            continue
        event = _validated_route_event(event)
        if event.collector_id != collector_id:
            raise EpisodeAsBuildError("RouteEvent collector 与 episode 不一致")
        afi = "ipv4" if event.afi_safi == "ipv4_unicast" else "ipv6"
        if event.afi_safi not in _AFI_SAFI.values():
            raise EpisodeAsBuildError("RouteEvent 地址族不支持")
        if event.prefix not in affected_prefixes.get(afi, set()):
            raise EpisodeAsBuildError("RouteEvent 前缀未关联到该 ASN 的受损集合")
        if event.action != "withdraw":
            resolution = derive_origin_asns(event.as_path or ())
            if asn not in resolution.origins:
                raise EpisodeAsBuildError("PrefixOriginRelation 与 RouteEvent origin 冲突")
        link = {
            "route_event_id": event.route_event_id,
            "raw_record_ref_id": _raw_record_ref_id(event),
            "artifact_id": event.artifact_id,
            "artifact_sha256": event.file_sha256,
            "record_ordinal": event.record_ordinal,
            "element_ordinal": event.element_ordinal,
        }
        existing = links.get(route_id)
        if existing is not None and existing != link:  # pragma: no cover - 字典键保护
            raise EpisodeAsBuildError("同一 RouteEvent 投影出冲突 raw ref")
        links[route_id] = link
    return [links[key] for key in sorted(links)]


def build_episode_as_records(
    episode: EpisodeDetection,
    samples_by_id: Mapping[str, Mapping[str, Any]],
    impacts_by_sample_id: Mapping[str, CountrySnapshotImpact],
    *,
    cohort: CountryCohort,
    mapping: CountryMappingView,
    route_events_by_id: Optional[Mapping[str, ResearchRouteEvent]] = None,
    prefix_change_event_ids: Optional[
        Mapping[Tuple[int, str, str], Sequence[str]]
    ] = None,
) -> Tuple[Dict[str, Any], ...]:
    """构造一个 episode 的完整逐 ASN 人口。

    ``route_events_by_id`` 是显式的 ``route_event_id -> ResearchRouteEvent``
    evidence index。``prefix_change_event_ids`` 用于补充 withdrawal 等不会出现在
    当前可见 ``PrefixOriginRelation`` 中的变化。二者均可为空；一旦显式声明
    某个变化 ID，则缺索引或前缀坐标冲突会失败关闭。
    """

    if not isinstance(episode, EpisodeDetection):
        raise EpisodeAsBuildError("episode 必须是 EpisodeDetection")
    if not isinstance(episode.run_id, str) or _RUN_ID_RE.fullmatch(episode.run_id) is None:
        raise EpisodeAsBuildError("episode.run_id 非法")
    if not isinstance(episode.episode_id, str) or _EPISODE_ID_RE.fullmatch(
        episode.episode_id
    ) is None:
        raise EpisodeAsBuildError("episode.episode_id 非法")
    if episode.country_code != "IR":
        raise EpisodeAsBuildError("当前 episode-as/v1 合同仅接受 IR")
    if episode.cohort_view not in {"compatible", "revised"}:
        raise EpisodeAsBuildError("episode.cohort_view 非法")
    if not isinstance(cohort, CountryCohort) or not isinstance(mapping, CountryMappingView):
        raise EpisodeAsBuildError("cohort/mapping 类型非法")
    if (
        cohort.country_code != episode.country_code
        or mapping.target_country != episode.country_code
        or cohort.cohort_view != episode.cohort_view
        or mapping.view != episode.cohort_view
    ):
        raise EpisodeAsBuildError("episode、cohort 与 mapping 口径不一致")
    if (
        cohort.mapping_source_sha256 != mapping.source_sha256
        or cohort.mapping_source_ref != mapping.source_ref
    ):
        raise EpisodeAsBuildError("cohort 与 mapping 来源证据不一致")

    route_index = {} if route_events_by_id is None else route_events_by_id
    change_index = {} if prefix_change_event_ids is None else prefix_change_event_ids
    if not isinstance(route_index, Mapping) or not isinstance(change_index, Mapping):
        raise EpisodeAsBuildError("evidence index 必须是 mapping")
    for key, event in route_index.items():
        if key != _validated_route_event(event).route_event_id:
            raise EpisodeAsBuildError("evidence index 键与 RouteEvent ID 不一致")

    samples = _sample_bindings(episode, samples_by_id, impacts_by_sample_id)
    trigger_sample = _point_by_start(samples, episode.onset_at, "episode.onset_at")
    peak_sample = _point_by_start(samples, episode.peak_at, "episode.peak_at")
    end_sample = _point_by_end(
        samples, episode.observation_end_at, "episode.observation_end_at"
    )
    _detected = _point_by_start(samples, episode.detected_at, "episode.detected_at")

    end_epoch = end_sample.end_epoch
    population = []
    first_seen_by_asn = dict(cohort.member_first_seen)
    if len(first_seen_by_asn) != len(cohort.member_first_seen):
        raise EpisodeAsBuildError("cohort.member_first_seen 重复 ASN")
    for value in first_seen_by_asn:
        asn = _asn(value)
        first_seen = first_seen_by_asn[asn]
        if first_seen is None:
            population.append(asn)
        else:
            _text, first_epoch = _utc_second(first_seen, f"AS{asn}.first_seen_at")
            if first_epoch <= end_epoch:
                population.append(asn)

    population_set = set(population)
    cohort_prefix_keys = {
        (reference.asn, reference.afi, reference.prefix)
        for reference in cohort.prefix_references
    }
    normalized_change_index = {}
    for raw_key, raw_ids in change_index.items():
        if not isinstance(raw_key, tuple) or len(raw_key) != 3:
            raise EpisodeAsBuildError(
                "prefix_change_event_ids 的键必须是 (asn, afi, prefix)"
            )
        change_asn = _asn(raw_key[0], "prefix_change_event_ids.asn")
        change_afi = raw_key[1]
        if change_afi not in _AFIS:
            raise EpisodeAsBuildError("prefix_change_event_ids.afi 非法")
        change_prefix = _prefix(
            raw_key[2], change_afi, "prefix_change_event_ids.prefix"
        )
        if raw_key != (change_asn, change_afi, change_prefix):
            raise EpisodeAsBuildError("prefix_change_event_ids 的键必须使用规范值")
        if change_asn not in population_set:
            raise EpisodeAsBuildError("前缀变化 ASN 不属于当前 episode 人口")
        if raw_key not in cohort_prefix_keys:
            raise EpisodeAsBuildError("前缀变化不属于冻结 cohort 参考关系")
        if (
            isinstance(raw_ids, (str, bytes, Mapping))
            or not isinstance(raw_ids, Sequence)
        ):
            raise EpisodeAsBuildError("prefix_change_event_ids 的值必须是 ID 序列")
        normalized_ids = tuple(raw_ids)
        for route_id in normalized_ids:
            if (
                not isinstance(route_id, str)
                or _ROUTE_EVENT_ID_RE.fullmatch(route_id) is None
            ):
                raise EpisodeAsBuildError(
                    "prefix_change_event_ids 含非法 RouteEvent ID"
                )
        if len(normalized_ids) != len(set(normalized_ids)):
            raise EpisodeAsBuildError("prefix_change_event_ids 不得重复 RouteEvent ID")
        normalized_change_index[raw_key] = normalized_ids
    change_index = normalized_change_index

    records = []
    for asn in sorted(population):
        assignment = mapping.assignment_for(asn)
        if assignment.mapping_state != "mapped" or assignment.countries != (
            episode.country_code,
        ):
            raise EpisodeAsBuildError(
                f"cohort ASN {asn} 没有确定的 {episode.country_code} 映射"
            )

        families_by_sample: Dict[str, Dict[str, Tuple[Optional[AddressFamilyDamage], str, Optional[str]]]] = {}
        for sample in samples:
            families_by_sample[sample.sample_id] = {
                afi: _family_at(sample, cohort, asn, afi) for afi in _AFIS
            }

        damaged_samples = []
        for sample in samples:
            state, _reason = _impact_state(sample.impact)
            if not _active_at(cohort, asn, sample.impact.observed_at) or state != "observed":
                continue
            damage = _damage_for(sample.impact, asn)
            if damage is None:
                raise EpisodeAsBuildError(f"observed impact 缺少 ASN {asn}")
            if damage.damaged is True:
                damaged_samples.append(sample)
            elif damage.damaged is not False:
                raise EpisodeAsBuildError(f"ASN {asn}.damaged 必须是布尔值")

        first_damaged_at = damaged_samples[0].end if damaged_samples else None
        last_damaged_at = damaged_samples[-1].end if damaged_samples else None
        trigger_member = trigger_sample in damaged_samples
        peak_member = peak_sample in damaged_samples
        cumulative_member = bool(damaged_samples)
        observation_end_member = end_sample in damaged_samples

        recovered_at = None
        if damaged_samples and not observation_end_member:
            last_damage_epoch = damaged_samples[-1].end_epoch
            trailing_healthy = []
            for sample in reversed(samples):
                if sample.end_epoch <= last_damage_epoch:
                    break
                state, _reason = _impact_state(sample.impact)
                if state != "observed" or not _active_at(
                    cohort, asn, sample.impact.observed_at
                ):
                    break
                damage = _damage_for(sample.impact, asn)
                if damage is None or damage.damaged is not False:
                    break
                trailing_healthy.append(sample)
            if trailing_healthy and trailing_healthy[0] is end_sample:
                recovered_at = tuple(reversed(trailing_healthy))[0].end

        address_families = {}
        affected_prefixes = {"ipv4": set(), "ipv6": set()}
        peak_families = families_by_sample[peak_sample.sample_id]
        for afi in _AFIS:
            cumulative_values = set()
            cumulative_state = "observed"
            cumulative_reason = None
            for sample in samples:
                if not _active_at(cohort, asn, sample.impact.observed_at):
                    continue
                family, state, reason = families_by_sample[sample.sample_id][afi]
                if state != "observed":
                    cumulative_state = state
                    cumulative_reason = reason
                    continue
                if family is None or family.lost_prefixes is None:
                    raise EpisodeAsBuildError("已观测累计集合缺少 lost_prefixes")
                cumulative_values.update(family.lost_prefixes)
                affected_prefixes[afi].update(family.lost_prefixes)
            cumulative = (
                (tuple(sorted(cumulative_values)), "observed", None)
                if cumulative_state == "observed"
                else (None, cumulative_state, cumulative_reason)
            )
            baseline_count = sum(
                1
                for reference in cohort.prefix_references
                if reference.asn == asn
                and reference.afi == afi
                and reference.first_seen_at is None
            )
            address_families[afi] = _family_contract(
                afi=afi,
                baseline_prefix_count=baseline_count,
                trigger=families_by_sample[trigger_sample.sample_id][afi],
                peak=peak_families[afi],
                cumulative=cumulative,
                observation_end=families_by_sample[end_sample.sample_id][afi],
            )

        links = _evidence_links(
            asn=asn,
            affected_prefixes=affected_prefixes,
            samples=samples,
            collector_id=episode.collector_id,
            route_events_by_id=route_index,
            prefix_change_event_ids=change_index,
        )
        episode_as_id = _stable_id(
            "episode_as_v1_",
            {
                "schema": "country_outage_episode_as_id_v1",
                "episode_id": episode.episode_id,
                "run_id": episode.run_id,
                "asn": asn,
                "country_code": episode.country_code,
                "cohort_view": episode.cohort_view,
            },
        )
        records.append(
            {
                "schema_version": "country-outage-episode-as/v1",
                "episode_as_id": episode_as_id,
                "episode_id": episode.episode_id,
                "run_id": episode.run_id,
                "asn": asn,
                "country_code": episode.country_code,
                "cohort_view": episode.cohort_view,
                "mapping_evidence": {
                    "mapping_state": assignment.mapping_state,
                    "mapping_view": mapping.view,
                    "mapping_sha256": mapping.source_sha256,
                    "source_ref": f"{mapping.source_ref}#AS{asn}",
                },
                "first_damaged_at": first_damaged_at,
                "last_damaged_at": last_damaged_at,
                "recovered_at": recovered_at,
                "trigger_member": trigger_member,
                "peak_member": peak_member,
                "cumulative_member": cumulative_member,
                "observation_end_member": observation_end_member,
                "address_families": address_families,
                "overall_classification": _overall_classification(peak_families),
                "evidence_links": links,
            }
        )
    return tuple(records)


__all__ = (
    "EpisodeAsBuildError",
    "build_episode_as_records",
)
