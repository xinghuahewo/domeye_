"""RRC25 国家中断研究的 UPDATE/STATE_CHANGE 适配层。

本模块消费既有 :class:`BgpdumpRecordStreamFactory` 产生的
``ParsedMrtRecord`` 流，把 UPDATE route elements 绑定为稳定的
``ResearchRouteEvent``，同时为每个 physical record 保留完整文件身份、
record/element 坐标和原始记录 SHA256。STATE_CHANGE 交给冻结的
``peer_session`` 解析器；结构完整的 OPEN、NOTIFICATION、KEEPALIVE 只形成
原始记录引用，不生成路由事件。

适配器不读取文件、数据库或真实 MRT。即使上游已做完整性检查，本层仍会对
连续 ordinal/offset、MRT header、五分钟槽位、peer 身份、事件时间和消息类型
做失败关闭核验，避免 fake/custom stream 绕过研究合同。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
from pathlib import PurePosixPath
import re
import struct
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence, Tuple

from ...route_event import (
    AsPathSegment,
    ParsedMrtRecord,
    ParsedRouteElement,
    artifact_id_v1,
)
from .peer_session import (
    BGP4MP_STATE_CHANGE,
    BGP4MP_STATE_CHANGE_AS4,
    MRT_BGP4MP,
    MRT_BGP4MP_ET,
    PeerSessionObservation,
    PeerSessionParseError,
    parse_peer_session_observation,
)
from .state_replay import (
    ResearchRouteEvent,
    StateReplayError,
    build_research_route_event,
)


UTC = timezone.utc
UPDATE_RECORD = "update"
STATE_CHANGE_RECORD = "state_change"
OPEN_RECORD = "open"
NOTIFICATION_RECORD = "notification"
KEEPALIVE_RECORD = "keepalive"

_MRT_HEADER_LENGTH = 12
_BGP_HEADER_LENGTH = 19
_BGP_MESSAGE_OPEN = 1
_BGP_MESSAGE_UPDATE = 2
_BGP_MESSAGE_NOTIFICATION = 3
_BGP_MESSAGE_KEEPALIVE = 4
_BGP4MP_UPDATE = 1
_BGP4MP_MESSAGE_AS4 = 4
_UPDATE_SUBTYPES = frozenset((_BGP4MP_UPDATE, _BGP4MP_MESSAGE_AS4))
_STATE_CHANGE_SUBTYPES = frozenset(
    (BGP4MP_STATE_CHANGE, BGP4MP_STATE_CHANGE_AS4)
)
_ASN_WIDTH_BY_UPDATE_SUBTYPE = {
    _BGP4MP_UPDATE: 2,
    _BGP4MP_MESSAGE_AS4: 4,
}
_ADDRESS_WIDTH_BY_AFI = {1: 4, 2: 16}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COLLECTOR_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_AFI_SAFI = frozenset(("ipv4_unicast", "ipv6_unicast"))
_AS_PATH_SEGMENT_TYPES = frozenset(
    ("as_sequence", "as_set", "confederation_sequence", "confederation_set")
)


class UpdateAdapterError(ValueError):
    """ParsedMrtRecord 流不能安全提升为研究证据。"""


@dataclass(frozen=True)
class RawRecordEvidence:
    """一个可由文件哈希与解压流坐标回查的 physical record。"""

    artifact_id: str
    file_sha256: str
    collector_id: str
    artifact_slot_utc: str
    record_ordinal: int
    record_offset: int
    record_length: int
    raw_record_sha256: str
    event_time_utc: str
    event_epoch_microseconds: int
    mrt_type: int
    mrt_subtype: int

    @property
    def raw_record_hash(self) -> str:
        """兼容研究文档中的通用字段名；算法固定为 SHA256。"""

        return self.raw_record_sha256

    @property
    def record_hash(self) -> str:
        """与既有 RouteEvent raw_record 表字段保持同义。"""

        return self.raw_record_sha256


@dataclass(frozen=True)
class AdaptedUpdateRecord:
    """一个 physical record 的互斥研究表达。"""

    record_kind: str
    raw_record: RawRecordEvidence
    route_events: Tuple[ResearchRouteEvent, ...]
    peer_session_observation: Optional[PeerSessionObservation]
    # 两个计数始终描述 raw physical record 的完整元素人口，而不是可选
    # retention selector 留下的研究子集。这样 worker 可以在不为无关元素构造
    # 稳定 RouteEvent 的前提下，仍保留全量 ANNOUNCE/WITHDRAW 槽计数。
    announce_count: int = 0
    withdraw_count: int = 0


@dataclass(frozen=True)
class _ArtifactIdentity:
    artifact_id: str
    file_sha256: str
    collector_id: str
    artifact_slot_utc: str
    slot_start_epoch_microseconds: int
    slot_end_epoch_microseconds: int


@dataclass(frozen=True)
class _FrameEnvelope:
    mrt_type: int
    mrt_subtype: int
    event_time_utc: str
    event_epoch_microseconds: int
    peer_ip: Optional[str]
    peer_asn: Optional[int]
    bgp_message_type: Optional[int]


RecordStreamFactory = Callable[[Mapping[str, Any]], Iterable[ParsedMrtRecord]]
RouteElementRetentionSelector = Callable[
    [Tuple[ParsedRouteElement, ...]], Sequence[bool]
]


def _retention_mask(
    selector: Optional[RouteElementRetentionSelector],
    elements: Tuple[ParsedRouteElement, ...],
) -> Tuple[bool, ...]:
    if selector is None:
        return (True,) * len(elements)
    try:
        selected = selector(elements)
    except (TypeError, ValueError) as error:
        raise UpdateAdapterError("route element retention selector 执行失败") from error
    if isinstance(selected, (str, bytes, bytearray)) or not isinstance(
        selected, Sequence
    ):
        raise UpdateAdapterError("route element retention selector 必须返回布尔序列")
    mask = tuple(selected)
    if len(mask) != len(elements) or any(type(value) is not bool for value in mask):
        raise UpdateAdapterError(
            "route element retention selector 必须为每个元素返回一个严格布尔值"
        )
    return mask


def _validate_element_semantics(element: ParsedRouteElement) -> None:
    """核验 selector 可能丢弃的元素仍满足研究 RouteEvent 语义。"""

    try:
        network = ipaddress.ip_network(element.prefix, strict=False)
    except (TypeError, ValueError) as error:
        raise UpdateAdapterError("route element prefix 非法") from error
    expected_afi = "ipv4_unicast" if network.version == 4 else "ipv6_unicast"
    if element.afi_safi not in _AFI_SAFI or element.afi_safi != expected_afi:
        raise UpdateAdapterError("route element prefix 与 AFI/SAFI 不一致")
    if element.action == "withdraw":
        if element.as_path is not None:
            raise UpdateAdapterError("withdraw route element 不得携带 AS_PATH")
    else:
        if not isinstance(element.as_path, tuple):
            raise UpdateAdapterError("announce route element 的 AS_PATH 必须是 tuple")
        for segment in element.as_path:
            if (
                not isinstance(segment, AsPathSegment)
                or segment.segment_type not in _AS_PATH_SEGMENT_TYPES
                or not isinstance(segment.asns, tuple)
                or not segment.asns
            ):
                raise UpdateAdapterError("route element AS_PATH segment 非法")
            if any(
                isinstance(asn, bool)
                or not isinstance(asn, int)
                or asn < 0
                or asn > 4_294_967_295
                for asn in segment.asns
            ):
                raise UpdateAdapterError("route element AS_PATH ASN 非法")
    flags = element.quality_flags
    if (
        not isinstance(flags, tuple)
        or any(not isinstance(flag, str) or not flag for flag in flags)
        or len(set(flags)) != len(flags)
    ):
        raise UpdateAdapterError("route element quality_flags 非法")


def _utc_second(value: object, field: str) -> Tuple[str, int]:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise UpdateAdapterError(f"{field} 必须是秒级 UTC Z 时间")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise UpdateAdapterError(f"{field} 不是合法秒级 UTC 时间") from error
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return value, int((parsed - epoch).total_seconds()) * 1_000_000


def _event_time(timestamp: int, microseconds: int) -> Tuple[str, int]:
    epoch_microseconds = timestamp * 1_000_000 + microseconds
    value = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
        seconds=timestamp, microseconds=microseconds
    )
    if microseconds:
        text = value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        text = value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return text, epoch_microseconds


def _parsed_event_epoch_microseconds(value: object) -> int:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise UpdateAdapterError("ParsedRouteElement.event_time_utc 必须以 Z 结尾")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise UpdateAdapterError("ParsedRouteElement.event_time_utc 非法") from error
    if parsed.utcoffset() != timedelta(0):
        raise UpdateAdapterError("ParsedRouteElement.event_time_utc 必须是 UTC")
    parsed = parsed.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = parsed - epoch
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _normalize_artifact(artifact: Mapping[str, Any]) -> _ArtifactIdentity:
    if not isinstance(artifact, Mapping):
        raise UpdateAdapterError("artifact 必须是 input_resolver 产出的对象")
    required = (
        "artifact_id",
        "file_sha256",
        "collector_id",
        "artifact_type",
        "artifact_time_utc",
        "relative_path",
        "compression",
        "size_bytes",
    )
    missing = [field for field in required if field not in artifact]
    if missing:
        raise UpdateAdapterError("artifact 缺少字段：" + ",".join(missing))
    if artifact["artifact_type"] != "update":
        raise UpdateAdapterError("UPDATE 适配层只接受 artifact_type=update")
    file_sha256 = artifact["file_sha256"]
    if not isinstance(file_sha256, str) or _SHA256_RE.fullmatch(file_sha256) is None:
        raise UpdateAdapterError("artifact.file_sha256 非法")
    try:
        expected_artifact_id = artifact_id_v1(file_sha256)
    except ValueError as error:
        raise UpdateAdapterError("artifact.file_sha256 不能生成稳定身份") from error
    if artifact["artifact_id"] != expected_artifact_id:
        raise UpdateAdapterError("artifact_id 与 file_sha256 不一致")
    collector_id = artifact["collector_id"]
    if not isinstance(collector_id, str) or _COLLECTOR_RE.fullmatch(collector_id) is None:
        raise UpdateAdapterError("artifact.collector_id 非法")
    slot_text, slot_start = _utc_second(
        artifact["artifact_time_utc"], "artifact.artifact_time_utc"
    )
    if slot_start % (300 * 1_000_000):
        raise UpdateAdapterError("UPDATE artifact_time_utc 未按五分钟槽对齐")
    relative_path = artifact["relative_path"]
    if not isinstance(relative_path, str):
        raise UpdateAdapterError("artifact.relative_path 必须是字符串")
    pure_path = PurePosixPath(relative_path)
    if (
        pure_path.is_absolute()
        or not pure_path.parts
        or ".." in pure_path.parts
        or pure_path.parts[0] != collector_id
    ):
        raise UpdateAdapterError("artifact.relative_path 越出 collector")
    if artifact["compression"] != "gz":
        raise UpdateAdapterError("UPDATE 研究输入只接受 gzip 制品")
    size_bytes = artifact["size_bytes"]
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes <= 0:
        raise UpdateAdapterError("artifact.size_bytes 必须是正整数")
    return _ArtifactIdentity(
        artifact_id=expected_artifact_id,
        file_sha256=file_sha256,
        collector_id=collector_id,
        artifact_slot_utc=slot_text,
        slot_start_epoch_microseconds=slot_start,
        slot_end_epoch_microseconds=slot_start + 300 * 1_000_000,
    )


def _decode_frame(raw_record: object) -> _FrameEnvelope:
    if not isinstance(raw_record, bytes) or len(raw_record) < _MRT_HEADER_LENGTH:
        raise UpdateAdapterError("raw_record 必须是完整 MRT physical record bytes")
    timestamp, mrt_type, mrt_subtype, payload_length = struct.unpack(
        "!IHHI", raw_record[:_MRT_HEADER_LENGTH]
    )
    if len(raw_record) != _MRT_HEADER_LENGTH + payload_length:
        raise UpdateAdapterError("MRT header length 与 raw_record 长度不一致")
    if mrt_type not in {MRT_BGP4MP, MRT_BGP4MP_ET}:
        raise UpdateAdapterError("UPDATE 制品只接受 MRT type 16/17")
    if mrt_subtype not in _UPDATE_SUBTYPES | _STATE_CHANGE_SUBTYPES:
        raise UpdateAdapterError("未知、local 或 Add-Path BGP4MP subtype 被拒绝")

    payload = raw_record[_MRT_HEADER_LENGTH:]
    microseconds = 0
    if mrt_type == MRT_BGP4MP_ET:
        if len(payload) < 4:
            raise UpdateAdapterError("BGP4MP_ET 缺少四字节扩展微秒")
        microseconds = struct.unpack("!I", payload[:4])[0]
        if microseconds > 999_999:
            raise UpdateAdapterError("BGP4MP_ET 扩展微秒超出 0..999999")
        payload = payload[4:]
    event_time_utc, event_epoch_microseconds = _event_time(timestamp, microseconds)

    if mrt_subtype in _STATE_CHANGE_SUBTYPES:
        return _FrameEnvelope(
            mrt_type=mrt_type,
            mrt_subtype=mrt_subtype,
            event_time_utc=event_time_utc,
            event_epoch_microseconds=event_epoch_microseconds,
            peer_ip=None,
            peer_asn=None,
            bgp_message_type=None,
        )

    asn_width = _ASN_WIDTH_BY_UPDATE_SUBTYPE[mrt_subtype]
    identity_length = asn_width * 2 + 4
    if len(payload) < identity_length:
        raise UpdateAdapterError("BGP4MP UPDATE peer identity 被截断")
    cursor = 0
    peer_asn = int.from_bytes(payload[cursor : cursor + asn_width], "big")
    cursor += asn_width * 2
    _interface_index, afi = struct.unpack("!HH", payload[cursor : cursor + 4])
    cursor += 4
    address_width = _ADDRESS_WIDTH_BY_AFI.get(afi)
    if address_width is None:
        raise UpdateAdapterError("BGP4MP UPDATE AFI 只允许 IPv4/IPv6")
    if len(payload) < cursor + address_width * 2 + _BGP_HEADER_LENGTH:
        raise UpdateAdapterError("BGP4MP UPDATE 地址或 BGP message 被截断")
    peer_ip = ipaddress.ip_address(payload[cursor : cursor + address_width]).compressed
    cursor += address_width * 2
    message = payload[cursor:]
    if message[:16] != b"\xff" * 16:
        raise UpdateAdapterError("BGP marker 非法")
    message_length, message_type = struct.unpack("!HB", message[16:19])
    if message_length < _BGP_HEADER_LENGTH:
        raise UpdateAdapterError("BGP message length 小于 19")
    if message_length != len(message):
        raise UpdateAdapterError("BGP message length 与 physical record 不一致")
    body = message[_BGP_HEADER_LENGTH:]
    if message_type == _BGP_MESSAGE_OPEN:
        if len(body) < 10:
            raise UpdateAdapterError("BGP OPEN body 被截断")
        if body[0] != 4:
            raise UpdateAdapterError("BGP OPEN version 必须为 4")
        optional_length = body[9]
        if len(body) != 10 + optional_length:
            raise UpdateAdapterError("BGP OPEN optional parameters 长度不闭合")
        offset = 10
        while offset < len(body):
            if len(body) - offset < 2:
                raise UpdateAdapterError("BGP OPEN optional parameter header 被截断")
            parameter_type = body[offset]
            parameter_length = body[offset + 1]
            offset += 2
            if offset + parameter_length > len(body):
                raise UpdateAdapterError("BGP OPEN optional parameter value 被截断")
            parameter_end = offset + parameter_length
            if parameter_type == 2:
                capability_offset = offset
                while capability_offset < parameter_end:
                    if parameter_end - capability_offset < 2:
                        raise UpdateAdapterError("BGP OPEN capability header 被截断")
                    capability_length = body[capability_offset + 1]
                    capability_offset += 2
                    if capability_offset + capability_length > parameter_end:
                        raise UpdateAdapterError("BGP OPEN capability value 被截断")
                    capability_offset += capability_length
            offset += parameter_length
    elif message_type == _BGP_MESSAGE_NOTIFICATION:
        if len(body) < 2:
            raise UpdateAdapterError("BGP NOTIFICATION body 被截断")
    elif message_type == _BGP_MESSAGE_KEEPALIVE:
        if message_length != _BGP_HEADER_LENGTH:
            raise UpdateAdapterError("KEEPALIVE 必须严格为 19 字节")
    elif message_type != _BGP_MESSAGE_UPDATE:
        raise UpdateAdapterError(
            "只接受 UPDATE、OPEN、NOTIFICATION 或 KEEPALIVE BGP message"
        )
    return _FrameEnvelope(
        mrt_type=mrt_type,
        mrt_subtype=mrt_subtype,
        event_time_utc=event_time_utc,
        event_epoch_microseconds=event_epoch_microseconds,
        peer_ip=peer_ip,
        peer_asn=peer_asn,
        bgp_message_type=message_type,
    )


def _raw_evidence(
    identity: _ArtifactIdentity,
    record: ParsedMrtRecord,
    envelope: _FrameEnvelope,
) -> RawRecordEvidence:
    return RawRecordEvidence(
        artifact_id=identity.artifact_id,
        file_sha256=identity.file_sha256,
        collector_id=identity.collector_id,
        artifact_slot_utc=identity.artifact_slot_utc,
        record_ordinal=record.record_ordinal,
        record_offset=record.record_offset,
        record_length=len(record.raw_record),
        raw_record_sha256=hashlib.sha256(record.raw_record).hexdigest(),
        event_time_utc=envelope.event_time_utc,
        event_epoch_microseconds=envelope.event_epoch_microseconds,
        mrt_type=envelope.mrt_type,
        mrt_subtype=envelope.mrt_subtype,
    )


def iter_adapted_update_records(
    records: Iterable[ParsedMrtRecord],
    *,
    artifact: Mapping[str, Any],
    route_element_retention_selector: Optional[
        RouteElementRetentionSelector
    ] = None,
) -> Iterator[AdaptedUpdateRecord]:
    """逐条提升一个已验证 UPDATE artifact 的 ``ParsedMrtRecord`` 流。

    selector 只影响哪些元素被提升为稳定 ``ResearchRouteEvent``；raw frame、
    时间、peer 身份、action 以及完整元素计数仍逐条失败关闭核验。默认 ``None``
    保持全量提升语义。该入口允许单事件研究在进入高成本稳定身份构造前按完整
    physical record 做确定性保留，不改变通用适配器的默认合同。
    """

    identity = _normalize_artifact(artifact)
    if isinstance(records, (str, bytes, bytearray, Mapping)):
        raise UpdateAdapterError("record stream 必须是 ParsedMrtRecord 可迭代对象")
    try:
        iterator = iter(records)
    except TypeError as error:
        raise UpdateAdapterError("record stream 不可迭代") from error

    expected_ordinal = 0
    expected_offset = 0
    seen_route_event_ids: set[str] = set()
    for record in iterator:
        if not isinstance(record, ParsedMrtRecord):
            raise UpdateAdapterError("record stream 含非 ParsedMrtRecord")
        if (
            isinstance(record.record_ordinal, bool)
            or not isinstance(record.record_ordinal, int)
            or record.record_ordinal < 0
        ):
            raise UpdateAdapterError("record_ordinal 必须是非负整数")
        if (
            isinstance(record.record_offset, bool)
            or not isinstance(record.record_offset, int)
            or record.record_offset < 0
        ):
            raise UpdateAdapterError("record_offset 必须是非负整数")
        if record.record_ordinal != expected_ordinal:
            raise UpdateAdapterError("record_ordinal 必须从 0 连续递增")
        if record.record_offset != expected_offset:
            raise UpdateAdapterError("record_offset 必须覆盖连续解压字节流")
        if not isinstance(record.elements, tuple):
            raise UpdateAdapterError("ParsedMrtRecord.elements 必须是确定顺序 tuple")

        envelope = _decode_frame(record.raw_record)
        if not (
            identity.slot_start_epoch_microseconds
            <= envelope.event_epoch_microseconds
            < identity.slot_end_epoch_microseconds
        ):
            raise UpdateAdapterError("MRT record 时间越出 artifact 五分钟槽")
        raw_evidence = _raw_evidence(identity, record, envelope)

        if envelope.mrt_subtype in _STATE_CHANGE_SUBTYPES:
            if record.elements:
                raise UpdateAdapterError("STATE_CHANGE 不得携带 RouteEvent element")
            try:
                observation = parse_peer_session_observation(
                    record,
                    collector_id=identity.collector_id,
                    file_sha256=identity.file_sha256,
                    artifact_id=identity.artifact_id,
                )
            except PeerSessionParseError as error:
                raise UpdateAdapterError("STATE_CHANGE 解析失败") from error
            if (
                observation.event_epoch_microseconds
                != envelope.event_epoch_microseconds
                or observation.raw_record_sha256 != raw_evidence.raw_record_sha256
            ):
                raise UpdateAdapterError("STATE_CHANGE 解码结果与 raw evidence 冲突")
            yield AdaptedUpdateRecord(
                record_kind=STATE_CHANGE_RECORD,
                raw_record=raw_evidence,
                route_events=(),
                peer_session_observation=observation,
            )
        elif envelope.bgp_message_type in {
            _BGP_MESSAGE_OPEN,
            _BGP_MESSAGE_NOTIFICATION,
            _BGP_MESSAGE_KEEPALIVE,
        }:
            if record.elements:
                raise UpdateAdapterError("BGP 控制消息不得伪造成 RouteEvent")
            record_kind = {
                _BGP_MESSAGE_OPEN: OPEN_RECORD,
                _BGP_MESSAGE_NOTIFICATION: NOTIFICATION_RECORD,
                _BGP_MESSAGE_KEEPALIVE: KEEPALIVE_RECORD,
            }[envelope.bgp_message_type]
            yield AdaptedUpdateRecord(
                record_kind=record_kind,
                raw_record=raw_evidence,
                route_events=(),
                peer_session_observation=None,
            )
        elif envelope.bgp_message_type == _BGP_MESSAGE_UPDATE:
            if not record.elements:
                raise UpdateAdapterError("无 route element 的 UPDATE 不能在 P0 提升")
            # selector 必须在完整 record 的所有元素完成 raw/time/peer/action
            # 核验之后才运行，避免 fake/custom stream 借过滤绕过输入合同。
            for element_ordinal, element in enumerate(record.elements):
                if not isinstance(element, ParsedRouteElement):
                    raise UpdateAdapterError("UPDATE elements 含非 ParsedRouteElement")
                if element.action not in {"announce", "withdraw"}:
                    raise UpdateAdapterError("UPDATE artifact 不接受 rib_snapshot action")
                if (
                    _parsed_event_epoch_microseconds(element.event_time_utc)
                    != envelope.event_epoch_microseconds
                ):
                    raise UpdateAdapterError("route element 时间与 MRT header 冲突")
                try:
                    element_peer_ip = ipaddress.ip_address(element.peer_ip).compressed
                except ValueError as error:
                    raise UpdateAdapterError("route element peer_ip 非法") from error
                if (
                    element_peer_ip != envelope.peer_ip
                    or element.peer_asn != envelope.peer_asn
                ):
                    raise UpdateAdapterError("route element peer 身份与 raw BGP4MP 冲突")
                _validate_element_semantics(element)

            retention_mask = _retention_mask(
                route_element_retention_selector, record.elements
            )
            route_events = []
            for element_ordinal, (element, retain) in enumerate(
                zip(record.elements, retention_mask)
            ):
                if not retain:
                    continue
                try:
                    route_event = build_research_route_event(
                        artifact_id=identity.artifact_id,
                        file_sha256=identity.file_sha256,
                        collector_id=identity.collector_id,
                        artifact_slot_utc=identity.artifact_slot_utc,
                        record_ordinal=record.record_ordinal,
                        element_ordinal=element_ordinal,
                        element=element,
                    )
                except StateReplayError as error:
                    raise UpdateAdapterError("route element 不符合研究回放合同") from error
                if route_event.route_event_id in seen_route_event_ids:
                    raise UpdateAdapterError("稳定 RouteEvent 身份重复")
                seen_route_event_ids.add(route_event.route_event_id)
                route_events.append(route_event)
            yield AdaptedUpdateRecord(
                record_kind=UPDATE_RECORD,
                raw_record=raw_evidence,
                route_events=tuple(route_events),
                peer_session_observation=None,
                announce_count=sum(
                    element.action == "announce" for element in record.elements
                ),
                withdraw_count=sum(
                    element.action == "withdraw" for element in record.elements
                ),
            )
        else:  # pragma: no cover - _decode_frame 已枚举全部合法消息类型。
            raise UpdateAdapterError("未知 BGP message 未被安全分类")

        expected_ordinal += 1
        expected_offset += len(record.raw_record)

    if expected_ordinal == 0:
        raise UpdateAdapterError("UPDATE record stream 为空")


def iter_bgpdump_artifact_records(
    record_stream_factory: RecordStreamFactory,
    artifact: Mapping[str, Any],
) -> Iterator[AdaptedUpdateRecord]:
    """把 ``BgpdumpRecordStreamFactory`` 或等价 fake 工厂接入研究适配层。"""

    if not callable(record_stream_factory):
        raise UpdateAdapterError("record_stream_factory 必须可调用")
    identity = _normalize_artifact(artifact)
    normalized_artifact = dict(artifact)
    # 上游工厂仍接收完整 input_resolver artifact；identity 的创建保证关键字段
    # 已在调用工厂前失败关闭。
    try:
        records = record_stream_factory(normalized_artifact)
    except (TypeError, ValueError, OSError) as error:
        raise UpdateAdapterError("record_stream_factory 无法建立记录流") from error
    yield from iter_adapted_update_records(records, artifact=normalized_artifact)


__all__ = (
    "AdaptedUpdateRecord",
    "KEEPALIVE_RECORD",
    "NOTIFICATION_RECORD",
    "OPEN_RECORD",
    "RawRecordEvidence",
    "STATE_CHANGE_RECORD",
    "UPDATE_RECORD",
    "UpdateAdapterError",
    "iter_adapted_update_records",
    "iter_bgpdump_artifact_records",
)
