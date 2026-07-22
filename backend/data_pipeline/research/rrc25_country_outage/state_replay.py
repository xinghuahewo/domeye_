"""RRC25 国家中断研究的确定性路由状态回放纯函数。

本模块只消费已经解析并绑定不可变原始制品坐标的
``ParsedRouteElement``，不读取 MRT、数据库或文件。路由状态键严格为
``collector + VP + AFI/SAFI + canonical prefix``：RIB/ANNOUNCE 替换该键，
WITHDRAW 只删除同一 VP 的同一前缀。

事件固定按 ``event_time -> artifact_slot -> record_ordinal ->
element_ordinal`` 排序。任何相同排序键、重复 RouteEvent 身份或被篡改的稳定
身份都会失败关闭。五分钟快照采用 ``[start, end)``；边界时刻的事件只进入
下一槽。关键输入一旦出现缺口，后续连续性保持 ``unknown_after_gap``，数值
计数
返回 ``None``，不会把未知伪装成零。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import ipaddress
import re
from typing import Dict, FrozenSet, Iterable, Mapping, Optional, Sequence, Tuple

from ...route_event import (
    AsPathSegment,
    ParsedRouteElement,
    artifact_id_v1,
    route_event_id_v1,
    vp_id_v1,
)


UTC = timezone.utc
CONTINUOUS = "continuous"
UNKNOWN_AFTER_GAP = "unknown_after_gap"
_ACTIONS = frozenset(("rib_snapshot", "announce", "withdraw"))
_UPDATE_ACTIONS = frozenset(("announce", "withdraw"))
_AFI_SAFI = frozenset(("ipv4_unicast", "ipv6_unicast"))
_SEGMENT_TYPES = frozenset(
    ("as_sequence", "as_set", "confederation_sequence", "confederation_set")
)
_COLLECTOR_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class StateReplayError(ValueError):
    """研究事件或回放输入不能在冻结语义下安全处理。"""


@dataclass(frozen=True, order=True)
class RouteStateKey:
    """一个 VP 对一个规范前缀的单播可见性状态键。"""

    collector_id: str
    vp_id: str
    afi_safi: str
    prefix: str


@dataclass(frozen=True)
class RawRecordRef:
    """不依赖 gzip offset 的稳定原始记录引用。"""

    artifact_id: str
    file_sha256: str
    collector_id: str
    artifact_slot_utc: str
    record_ordinal: int
    element_ordinal: int
    route_event_id: str


@dataclass(frozen=True)
class ResearchRouteEvent:
    """回放使用的规范研究事件，显式保留原始制品与元素坐标。"""

    artifact_id: str
    file_sha256: str
    collector_id: str
    artifact_slot_utc: str
    record_ordinal: int
    element_ordinal: int
    route_event_id: str
    event_time_utc: str
    peer_ip: str
    peer_asn: int
    vp_id: str
    action: str
    afi_safi: str
    prefix: str
    as_path: Optional[Tuple[AsPathSegment, ...]]
    quality_flags: Tuple[str, ...]

    @property
    def key(self) -> RouteStateKey:
        return RouteStateKey(
            self.collector_id, self.vp_id, self.afi_safi, self.prefix
        )

    @property
    def raw_ref(self) -> RawRecordRef:
        return RawRecordRef(
            artifact_id=self.artifact_id,
            file_sha256=self.file_sha256,
            collector_id=self.collector_id,
            artifact_slot_utc=self.artifact_slot_utc,
            record_ordinal=self.record_ordinal,
            element_ordinal=self.element_ordinal,
            route_event_id=self.route_event_id,
        )


@dataclass(frozen=True)
class RouteStateEntry:
    """当前可见路由；AS_PATH/质量标记/raw ref 均来自最近替换事件。"""

    key: RouteStateKey
    peer_ip: str
    peer_asn: int
    as_path: Tuple[AsPathSegment, ...]
    quality_flags: Tuple[str, ...]
    last_action: str
    last_event_time_utc: str
    last_raw_ref: RawRecordRef


@dataclass(frozen=True)
class RouteLastChange:
    """每个状态键的最近变化；WITHDRAW 后仍保留其 raw ref。"""

    key: RouteStateKey
    action: str
    event_time_utc: str
    as_path: Optional[Tuple[AsPathSegment, ...]]
    quality_flags: Tuple[str, ...]
    raw_ref: RawRecordRef


@dataclass(frozen=True)
class InputGap:
    """一个关键输入缺口，采用半开 UTC 区间。"""

    start_utc: str
    end_exclusive_utc: str
    missing_reason: str

    def __post_init__(self) -> None:
        start_text, start_epoch = _normalize_utc(self.start_utc, "gap.start_utc")
        end_text, end_epoch = _normalize_utc(
            self.end_exclusive_utc, "gap.end_exclusive_utc"
        )
        if start_epoch >= end_epoch:
            raise StateReplayError("输入缺口必须是非空半开区间")
        if not isinstance(self.missing_reason, str) or not self.missing_reason.strip():
            raise StateReplayError("输入缺口必须给出 missing_reason")
        if self.missing_reason != self.missing_reason.strip():
            raise StateReplayError("missing_reason 不得带首尾空白")
        object.__setattr__(self, "start_utc", start_text)
        object.__setattr__(self, "end_exclusive_utc", end_text)


@dataclass(frozen=True)
class RouteReplayState:
    """不可变回放状态；未知连续性下 ``route_count`` 必须为 ``None``。"""

    entries: Tuple[RouteStateEntry, ...]
    latest_changes: Tuple[RouteLastChange, ...]
    continuity_state: str
    missing_reasons: Tuple[str, ...]
    processed_route_event_ids: FrozenSet[str]
    last_order_key: Optional[Tuple[int, int, int, int]]

    @property
    def route_count(self) -> Optional[int]:
        if self.continuity_state != CONTINUOUS:
            return None
        return len(self.entries)


@dataclass(frozen=True)
class ReplaySnapshot:
    """一个五分钟槽末状态，表示槽 ``[start, end)`` 的结果。"""

    slot_start_utc: str
    slot_end_exclusive_utc: str
    boundary: str
    continuity_state: str
    missing_reasons: Tuple[str, ...]
    route_count: Optional[int]
    entries: Tuple[RouteStateEntry, ...]
    slot_changes: Tuple[RouteLastChange, ...]


@dataclass(frozen=True)
class ReplayWindowResult:
    """窗口快照与窗口末最终状态。"""

    window_start_utc: str
    window_end_exclusive_utc: str
    snapshots: Tuple[ReplaySnapshot, ...]
    final_state: RouteReplayState


def _normalize_utc(value: object, field: str) -> Tuple[str, int]:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise StateReplayError(f"{field} 必须是以 Z 结尾的 UTC 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise StateReplayError(f"{field} 不是合法 UTC 时间") from error
    if parsed.utcoffset() != timedelta(0):
        raise StateReplayError(f"{field} 必须是 UTC 时间")
    parsed = parsed.astimezone(UTC)
    if parsed.microsecond:
        normalized = parsed.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    else:
        normalized = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = parsed - epoch
    epoch_microseconds = (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )
    return normalized, epoch_microseconds


def _normalize_collector(value: object) -> str:
    if not isinstance(value, str) or not _COLLECTOR_RE.fullmatch(value):
        raise StateReplayError("collector_id 非法")
    return value


def _normalize_nonnegative(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StateReplayError(f"{field} 必须是非负整数")
    return value


def _normalize_asn(value: object, field: str) -> int:
    normalized = _normalize_nonnegative(value, field)
    if normalized > 4_294_967_295:
        raise StateReplayError(f"{field} 超出 32 位 ASN 范围")
    return normalized


def _normalize_ip(value: object) -> str:
    if not isinstance(value, str):
        raise StateReplayError("peer_ip 必须是字符串")
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError as error:
        raise StateReplayError("peer_ip 不是合法 IP 地址") from error


def _normalize_prefix(value: object) -> Tuple[str, str]:
    if not isinstance(value, str):
        raise StateReplayError("prefix 必须是 CIDR 字符串")
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as error:
        raise StateReplayError("prefix 不是合法 CIDR") from error
    afi_safi = "ipv4_unicast" if network.version == 4 else "ipv6_unicast"
    return network.compressed, afi_safi


def _normalize_as_path(
    value: Optional[Tuple[AsPathSegment, ...]], action: str
) -> Optional[Tuple[AsPathSegment, ...]]:
    if action == "withdraw":
        if value is not None:
            raise StateReplayError("withdraw 不得携带 AS_PATH")
        return None
    if not isinstance(value, tuple):
        raise StateReplayError("RIB/announce 的 AS_PATH 必须是 tuple")
    normalized = []
    for segment in value:
        if not isinstance(segment, AsPathSegment):
            raise StateReplayError("AS_PATH segment 必须是 AsPathSegment")
        if segment.segment_type not in _SEGMENT_TYPES:
            raise StateReplayError("AS_PATH segment_type 非法")
        if not isinstance(segment.asns, tuple) or not segment.asns:
            raise StateReplayError("AS_PATH segment 必须包含至少一个 ASN")
        asns = tuple(_normalize_asn(asn, "AS_PATH ASN") for asn in segment.asns)
        normalized.append(AsPathSegment(segment.segment_type, asns))
    return tuple(normalized)


def _normalize_quality_flags(value: object) -> Tuple[str, ...]:
    if not isinstance(value, tuple):
        raise StateReplayError("quality_flags 必须是 tuple")
    if any(not isinstance(flag, str) or not flag for flag in value):
        raise StateReplayError("quality_flags 只能包含非空字符串")
    if len(set(value)) != len(value):
        raise StateReplayError("quality_flags 不得重复")
    return tuple(sorted(value))


def build_research_route_event(
    *,
    artifact_id: str,
    file_sha256: str,
    collector_id: str,
    artifact_slot_utc: str,
    record_ordinal: int,
    element_ordinal: int,
    element: ParsedRouteElement,
) -> ResearchRouteEvent:
    """将一个解析元素绑定稳定 artifact/record/element 身份。"""

    if not isinstance(file_sha256, str) or not _SHA256_RE.fullmatch(file_sha256):
        raise StateReplayError("file_sha256 必须是 64 位小写十六进制")
    expected_artifact_id = artifact_id_v1(file_sha256)
    if artifact_id != expected_artifact_id:
        raise StateReplayError("artifact_id 与 file_sha256 的稳定身份不一致")
    collector = _normalize_collector(collector_id)
    slot_text, _slot_epoch = _normalize_utc(
        artifact_slot_utc, "artifact_slot_utc"
    )
    record = _normalize_nonnegative(record_ordinal, "record_ordinal")
    ordinal = _normalize_nonnegative(element_ordinal, "element_ordinal")
    if not isinstance(element, ParsedRouteElement):
        raise StateReplayError("element 必须是 ParsedRouteElement")
    event_text, _event_epoch = _normalize_utc(
        element.event_time_utc, "event_time_utc"
    )
    peer_ip = _normalize_ip(element.peer_ip)
    peer_asn = _normalize_asn(element.peer_asn, "peer_asn")
    if element.action not in _ACTIONS:
        raise StateReplayError("action 非法")
    prefix, expected_afi_safi = _normalize_prefix(element.prefix)
    if element.afi_safi not in _AFI_SAFI:
        raise StateReplayError("afi_safi 仅支持 IPv4/IPv6 unicast")
    if element.afi_safi != expected_afi_safi:
        raise StateReplayError("afi_safi 与规范 prefix 地址族不一致")
    as_path = _normalize_as_path(element.as_path, element.action)
    flags = _normalize_quality_flags(element.quality_flags)
    return ResearchRouteEvent(
        artifact_id=expected_artifact_id,
        file_sha256=file_sha256,
        collector_id=collector,
        artifact_slot_utc=slot_text,
        record_ordinal=record,
        element_ordinal=ordinal,
        route_event_id=route_event_id_v1(file_sha256, record, ordinal),
        event_time_utc=event_text,
        peer_ip=peer_ip,
        peer_asn=peer_asn,
        vp_id=vp_id_v1(collector, peer_ip, peer_asn),
        action=element.action,
        afi_safi=element.afi_safi,
        prefix=prefix,
        as_path=as_path,
        quality_flags=flags,
    )


def _validated_event(event: object) -> ResearchRouteEvent:
    if not isinstance(event, ResearchRouteEvent):
        raise StateReplayError("回放输入必须是 ResearchRouteEvent")
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
    if event != rebuilt:
        raise StateReplayError("研究事件的稳定身份或规范字段发生冲突")
    return rebuilt


def _event_order_key(event: ResearchRouteEvent) -> Tuple[int, int, int, int]:
    _event_text, event_epoch = _normalize_utc(
        event.event_time_utc, "event_time_utc"
    )
    _slot_text, slot_epoch = _normalize_utc(
        event.artifact_slot_utc, "artifact_slot_utc"
    )
    return (
        event_epoch,
        slot_epoch,
        event.record_ordinal,
        event.element_ordinal,
    )


def _ordered_events(
    events: Iterable[ResearchRouteEvent],
    *,
    allowed_actions: FrozenSet[str],
    prior_state: Optional[RouteReplayState],
) -> Tuple[ResearchRouteEvent, ...]:
    if isinstance(events, (str, bytes, Mapping)):
        raise StateReplayError("events 必须是 ResearchRouteEvent 可迭代对象")
    try:
        normalized = tuple(_validated_event(event) for event in events)
    except TypeError as error:
        raise StateReplayError("events 必须可迭代") from error
    for event in normalized:
        if event.action not in allowed_actions:
            raise StateReplayError("当前回放阶段收到不允许的 action")
    ordered = tuple(sorted(normalized, key=_event_order_key))
    seen_keys = set()
    seen_ids = (
        set(prior_state.processed_route_event_ids) if prior_state is not None else set()
    )
    for event in ordered:
        order_key = _event_order_key(event)
        if order_key in seen_keys:
            raise StateReplayError(
                "多个事件具有相同确定性排序键，拒绝猜测顺序"
            )
        seen_keys.add(order_key)
        if event.route_event_id in seen_ids:
            raise StateReplayError("RouteEvent 稳定身份重复或被跨阶段重放")
        seen_ids.add(event.route_event_id)
    if (
        prior_state is not None
        and ordered
        and prior_state.last_order_key is not None
        and _event_order_key(ordered[0]) <= prior_state.last_order_key
    ):
        raise StateReplayError("新阶段事件早于或等于已回放的最后排序键")
    return ordered


def _empty_state() -> RouteReplayState:
    return RouteReplayState(
        entries=(),
        latest_changes=(),
        continuity_state=CONTINUOUS,
        missing_reasons=(),
        processed_route_event_ids=frozenset(),
        last_order_key=None,
    )


def _apply_ordered_events(
    state: RouteReplayState,
    ordered: Sequence[ResearchRouteEvent],
) -> Tuple[RouteReplayState, Tuple[RouteLastChange, ...]]:
    entries: Dict[RouteStateKey, RouteStateEntry] = {
        entry.key: entry for entry in state.entries
    }
    changes: Dict[RouteStateKey, RouteLastChange] = {
        change.key: change for change in state.latest_changes
    }
    processed = set(state.processed_route_event_ids)
    slot_changes = []
    last_order_key = state.last_order_key
    for event in ordered:
        key = event.key
        previous = entries.get(key)
        if previous is not None and (
            previous.peer_ip != event.peer_ip or previous.peer_asn != event.peer_asn
        ):
            raise StateReplayError("同一状态键关联到冲突的 VP 身份")
        change = RouteLastChange(
            key=key,
            action=event.action,
            event_time_utc=event.event_time_utc,
            as_path=event.as_path,
            quality_flags=event.quality_flags,
            raw_ref=event.raw_ref,
        )
        changes[key] = change
        slot_changes.append(change)
        if event.action == "withdraw":
            entries.pop(key, None)
        else:
            if event.as_path is None:  # pragma: no cover - builder 已失败关闭
                raise StateReplayError("可见路由缺少 AS_PATH")
            entries[key] = RouteStateEntry(
                key=key,
                peer_ip=event.peer_ip,
                peer_asn=event.peer_asn,
                as_path=event.as_path,
                quality_flags=event.quality_flags,
                last_action=event.action,
                last_event_time_utc=event.event_time_utc,
                last_raw_ref=event.raw_ref,
            )
        processed.add(event.route_event_id)
        last_order_key = _event_order_key(event)
    return (
        replace(
            state,
            entries=tuple(entries[key] for key in sorted(entries)),
            latest_changes=tuple(changes[key] for key in sorted(changes)),
            processed_route_event_ids=frozenset(processed),
            last_order_key=last_order_key,
        ),
        tuple(slot_changes),
    )


def _validated_gaps(gaps: Iterable[InputGap]) -> Tuple[InputGap, ...]:
    if isinstance(gaps, (str, bytes, Mapping)):
        raise StateReplayError("input_gaps 必须是 InputGap 可迭代对象")
    try:
        values = tuple(gaps)
    except TypeError as error:
        raise StateReplayError("input_gaps 必须可迭代") from error
    if any(not isinstance(gap, InputGap) for gap in values):
        raise StateReplayError("input_gaps 只能包含 InputGap")
    return tuple(
        sorted(values, key=lambda gap: _normalize_utc(gap.start_utc, "gap.start")[1])
    )


def _apply_gaps(
    state: RouteReplayState, gaps: Iterable[InputGap]
) -> RouteReplayState:
    values = _validated_gaps(gaps)
    if not values:
        return state
    reasons = tuple(
        sorted(set(state.missing_reasons) | {gap.missing_reason for gap in values})
    )
    return replace(
        state,
        continuity_state=UNKNOWN_AFTER_GAP,
        missing_reasons=reasons,
    )


def seed_state_from_rib(
    rib_events: Iterable[ResearchRouteEvent],
    *,
    input_gaps: Iterable[InputGap] = (),
) -> RouteReplayState:
    """从完整 RIB route elements 建立初始 VP 前缀状态。"""

    ordered = _ordered_events(
        rib_events,
        allowed_actions=frozenset(("rib_snapshot",)),
        prior_state=None,
    )
    state, _changes = _apply_ordered_events(_empty_state(), ordered)
    return _apply_gaps(state, input_gaps)


def extend_streaming_rib_seed(
    state: Optional[RouteReplayState],
    rib_events: Iterable[ResearchRouteEvent],
    *,
    input_gaps: Iterable[InputGap] = (),
) -> RouteReplayState:
    """按完整 physical-record 批次流式扩展 seed RIB 状态。

    RIB entry 的 ``originated_time`` 在 physical record 之间不保证单调，因而
    不能把每一批简单交给需要全局时间单调的 UPDATE 回放。本函数仍严格采用
    冻结的确定性排序键；同一状态键只保留排序键最大的 RIB entry，并把所有
    已验证 RouteEvent 身份记入去重集合。如此逐批处理的最终状态与
    :func:`seed_state_from_rib` 对所有批次并集一次排序的结果相同，同时不会
    物化完整 RIB。

    ``state=None`` 表示第一批。调用方只能将同一个 seed RIB 的完整记录批次
    依次传入；UPDATE 阶段不得调用本函数。
    """

    current = _empty_state() if state is None else state
    if not isinstance(current, RouteReplayState):
        raise StateReplayError("state 必须是 RouteReplayState 或 None")
    if isinstance(rib_events, (str, bytes, Mapping)):
        raise StateReplayError("rib_events 必须是 ResearchRouteEvent 可迭代对象")
    try:
        normalized = tuple(_validated_event(event) for event in rib_events)
    except TypeError as error:
        raise StateReplayError("rib_events 必须可迭代") from error
    if any(event.action != "rib_snapshot" for event in normalized):
        raise StateReplayError("流式 seed 阶段只接受 rib_snapshot")

    ordered = tuple(sorted(normalized, key=_event_order_key))
    processed = set(current.processed_route_event_ids)
    seen_order_keys = set()
    for event in ordered:
        order_key = _event_order_key(event)
        if order_key in seen_order_keys:
            raise StateReplayError("多个 RIB 事件具有相同确定性排序键")
        seen_order_keys.add(order_key)
        if event.route_event_id in processed:
            raise StateReplayError("RouteEvent 稳定身份重复或被跨批次重放")
        processed.add(event.route_event_id)

    entries: Dict[RouteStateKey, RouteStateEntry] = {
        entry.key: entry for entry in current.entries
    }
    changes: Dict[RouteStateKey, RouteLastChange] = {
        change.key: change for change in current.latest_changes
    }
    last_order_key = current.last_order_key
    for event in ordered:
        key = event.key
        previous = entries.get(key)
        if previous is not None and (
            previous.peer_ip != event.peer_ip or previous.peer_asn != event.peer_asn
        ):
            raise StateReplayError("同一状态键关联到冲突的 VP 身份")
        prior_change = changes.get(key)
        event_key = _event_order_key(event)
        if prior_change is not None:
            prior_key = (
                _normalize_utc(
                    prior_change.event_time_utc, "latest_change.event_time_utc"
                )[1],
                _normalize_utc(
                    prior_change.raw_ref.artifact_slot_utc,
                    "latest_change.artifact_slot_utc",
                )[1],
                prior_change.raw_ref.record_ordinal,
                prior_change.raw_ref.element_ordinal,
            )
            if event_key <= prior_key:
                last_order_key = max(last_order_key or event_key, event_key)
                continue
        if event.as_path is None:  # pragma: no cover - builder 已失败关闭
            raise StateReplayError("RIB 可见路由缺少 AS_PATH")
        change = RouteLastChange(
            key=key,
            action=event.action,
            event_time_utc=event.event_time_utc,
            as_path=event.as_path,
            quality_flags=event.quality_flags,
            raw_ref=event.raw_ref,
        )
        changes[key] = change
        entries[key] = RouteStateEntry(
            key=key,
            peer_ip=event.peer_ip,
            peer_asn=event.peer_asn,
            as_path=event.as_path,
            quality_flags=event.quality_flags,
            last_action=event.action,
            last_event_time_utc=event.event_time_utc,
            last_raw_ref=event.raw_ref,
        )
        last_order_key = max(last_order_key or event_key, event_key)

    result = replace(
        current,
        entries=tuple(entries[key] for key in sorted(entries)),
        latest_changes=tuple(changes[key] for key in sorted(changes)),
        processed_route_event_ids=frozenset(processed),
        last_order_key=last_order_key,
    )
    return _apply_gaps(result, input_gaps)


def apply_streaming_update_batch(
    state: RouteReplayState,
    update_events: Iterable[ResearchRouteEvent],
    *,
    input_gaps: Iterable[InputGap] = (),
) -> Tuple[RouteReplayState, Tuple[RouteLastChange, ...]]:
    """应用一个已完整读取的 UPDATE 槽批次并返回该槽变化。

    调用方可以逐 physical record 收集经过研究过滤的少量事件，但必须等制品
    完整耗尽后再调用，从而既不物化全量 UPDATE，也不会因文件内时间轻微乱序
    改变冻结的 ``event_time`` 优先排序语义。
    """

    if not isinstance(state, RouteReplayState):
        raise StateReplayError("state 必须是 RouteReplayState")
    ordered = _ordered_events(
        update_events,
        allowed_actions=_UPDATE_ACTIONS,
        prior_state=state,
    )
    result, changes = _apply_ordered_events(state, ordered)
    return _apply_gaps(result, input_gaps), changes


def build_five_minute_snapshot(
    state: RouteReplayState,
    *,
    slot_start_utc: str,
    slot_end_exclusive_utc: str,
    slot_changes: Iterable[RouteLastChange] = (),
    input_gaps: Iterable[InputGap] = (),
) -> Tuple[RouteReplayState, ReplaySnapshot]:
    """在一个完整五分钟制品边界生成快照，不读取或重放事件。"""

    if not isinstance(state, RouteReplayState):
        raise StateReplayError("state 必须是 RouteReplayState")
    start_text, start_epoch = _normalize_utc(slot_start_utc, "slot_start_utc")
    end_text, end_epoch = _normalize_utc(
        slot_end_exclusive_utc, "slot_end_exclusive_utc"
    )
    if end_epoch - start_epoch != 300 * 1_000_000:
        raise StateReplayError("快照槽必须恰为五分钟")
    start = datetime.fromtimestamp(start_epoch / 1_000_000, UTC)
    end = datetime.fromtimestamp(end_epoch / 1_000_000, UTC)
    if (
        start.second
        or start.microsecond
        or start.minute % 5
        or end.second
        or end.microsecond
        or end.minute % 5
    ):
        raise StateReplayError("快照端点必须对齐 UTC 五分钟边界")
    if isinstance(slot_changes, (str, bytes, Mapping)):
        raise StateReplayError("slot_changes 必须是 RouteLastChange 可迭代对象")
    try:
        changes = tuple(slot_changes)
    except TypeError as error:
        raise StateReplayError("slot_changes 必须可迭代") from error
    if any(not isinstance(change, RouteLastChange) for change in changes):
        raise StateReplayError("slot_changes 只能包含 RouteLastChange")
    for change in changes:
        event_epoch = _normalize_utc(change.event_time_utc, "change.event_time_utc")[1]
        if event_epoch < start_epoch or event_epoch >= end_epoch:
            raise StateReplayError("slot_changes 必须位于快照半开槽内")
    current = _apply_gaps(state, input_gaps)
    return current, ReplaySnapshot(
        slot_start_utc=start_text,
        slot_end_exclusive_utc=end_text,
        boundary="[start,end)",
        continuity_state=current.continuity_state,
        missing_reasons=current.missing_reasons,
        route_count=current.route_count,
        entries=current.entries,
        slot_changes=changes,
    )


def apply_catch_up_updates(
    state: RouteReplayState,
    update_events: Iterable[ResearchRouteEvent],
    *,
    input_gaps: Iterable[InputGap] = (),
) -> RouteReplayState:
    """把 seed RIB 回放到研究窗口起点，不产生研究窗口样本。"""

    if not isinstance(state, RouteReplayState):
        raise StateReplayError("state 必须是 RouteReplayState")
    ordered = _ordered_events(
        update_events,
        allowed_actions=_UPDATE_ACTIONS,
        prior_state=state,
    )
    result, _changes = _apply_ordered_events(state, ordered)
    return _apply_gaps(result, input_gaps)


def replay_five_minute_window(
    state: RouteReplayState,
    update_events: Iterable[ResearchRouteEvent],
    *,
    window_start_utc: str,
    window_end_exclusive_utc: str,
    input_gaps: Iterable[InputGap] = (),
) -> ReplayWindowResult:
    """回放半开研究窗口，并在每个五分钟槽末输出不可变快照。"""

    if not isinstance(state, RouteReplayState):
        raise StateReplayError("state 必须是 RouteReplayState")
    start_text, start_epoch = _normalize_utc(window_start_utc, "window_start_utc")
    end_text, end_epoch = _normalize_utc(
        window_end_exclusive_utc, "window_end_exclusive_utc"
    )
    start = datetime.fromtimestamp(start_epoch / 1_000_000, UTC)
    end = datetime.fromtimestamp(end_epoch / 1_000_000, UTC)
    if start >= end or (end - start) % timedelta(minutes=5):
        raise StateReplayError("研究窗口必须是五分钟整数倍的非空半开区间")
    if (
        start.second
        or start.microsecond
        or start.minute % 5
        or end.second
        or end.microsecond
        or end.minute % 5
    ):
        raise StateReplayError("研究窗口端点必须对齐 UTC 五分钟边界")

    ordered = _ordered_events(
        update_events,
        allowed_actions=_UPDATE_ACTIONS,
        prior_state=state,
    )
    for event in ordered:
        event_epoch = _event_order_key(event)[0]
        if event_epoch < start_epoch or event_epoch >= end_epoch:
            raise StateReplayError("窗口事件必须位于冻结的半开区间 [start,end)")

    gaps = _validated_gaps(input_gaps)
    for gap in gaps:
        _gap_start_text, gap_start = _normalize_utc(gap.start_utc, "gap.start")
        _gap_end_text, gap_end = _normalize_utc(
            gap.end_exclusive_utc, "gap.end"
        )
        if gap_end <= start_epoch or gap_start >= end_epoch:
            raise StateReplayError("窗口 input_gap 必须与冻结研究窗口相交")

    snapshots = []
    event_index = 0
    gap_index = 0
    current = state
    slot_start = start
    while slot_start < end:
        slot_end = slot_start + timedelta(minutes=5)
        slot_end_epoch = int(slot_end.timestamp() * 1_000_000)
        slot_events = []
        while (
            event_index < len(ordered)
            and _event_order_key(ordered[event_index])[0] < slot_end_epoch
        ):
            slot_events.append(ordered[event_index])
            event_index += 1
        current, slot_changes = _apply_ordered_events(current, slot_events)

        newly_effective_gaps = []
        while gap_index < len(gaps):
            _text, gap_start_epoch = _normalize_utc(
                gaps[gap_index].start_utc, "gap.start"
            )
            if gap_start_epoch >= slot_end_epoch:
                break
            newly_effective_gaps.append(gaps[gap_index])
            gap_index += 1
        current = _apply_gaps(current, newly_effective_gaps)
        snapshots.append(
            ReplaySnapshot(
                slot_start_utc=slot_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                slot_end_exclusive_utc=slot_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                boundary="[start,end)",
                continuity_state=current.continuity_state,
                missing_reasons=current.missing_reasons,
                route_count=current.route_count,
                entries=current.entries,
                slot_changes=slot_changes,
            )
        )
        slot_start = slot_end

    return ReplayWindowResult(
        window_start_utc=start_text,
        window_end_exclusive_utc=end_text,
        snapshots=tuple(snapshots),
        final_state=current,
    )


__all__ = (
    "CONTINUOUS",
    "UNKNOWN_AFTER_GAP",
    "InputGap",
    "RawRecordRef",
    "ReplaySnapshot",
    "ReplayWindowResult",
    "ResearchRouteEvent",
    "RouteLastChange",
    "RouteReplayState",
    "RouteStateEntry",
    "RouteStateKey",
    "StateReplayError",
    "apply_catch_up_updates",
    "apply_streaming_update_batch",
    "build_five_minute_snapshot",
    "build_research_route_event",
    "extend_streaming_rib_seed",
    "replay_five_minute_window",
    "seed_state_from_rib",
)
