"""RRC25 研究 RIB parser 到 ``ResearchRouteEvent`` 的流式适配桥。

本模块消费 :func:`iter_rib_mrt_records` 产生的 ``ParsedMrtRecord`` 流与
``input_resolver`` 已验证的 RIB artifact。每个 physical record 在被消费后
立即产出，不把整个 RIB 或全部 RouteEvent 物化到内存。

调用方可提供显式的 IR origin ASN predicate。TABLE_DUMP_V2 的过滤单位是
整个 prefix physical record：只要任一 resolved origin 命中 IR，或存在无法
排除 IR 的 ambiguous origin，就保留该 prefix 的所有 VP/MOAS elements；仅当
全部候选都明确为非 IR 时才整组丢弃。AS_SET、confederation 或空路径保持为
``retained_origin_unknown``，不会猜测唯一 origin，并保留 parser 的质量标记。
physical record ordinal、原始 element ordinal 和 raw hash 永远使用过滤前坐标。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import os
import re
import struct
from pathlib import PurePosixPath
from typing import Any, BinaryIO, Callable, Iterable, Iterator, Mapping, Optional, Tuple

from ...route_event import (
    ParsedMrtRecord,
    ParsedRouteElement,
    artifact_id_v1,
    vp_id_v1,
)
from .country_impact import CONFLICT, RESOLVED, UNKNOWN, derive_origin_asns
from .rib_parser import (
    ParsedNonTargetRibRecord,
    RibMrtParseError,
    RibMrtSeekContext,
    RibPeerIndexContext,
    RibRecordBoundary,
    iter_rib_mrt_records,
    iter_rib_mrt_records_from_offset,
    iter_rib_spool_records,
)
from .state_replay import (
    ResearchRouteEvent,
    StateReplayError,
    build_research_route_event,
)
from .update_adapter import RawRecordEvidence


UTC = timezone.utc
RIB_RECORD = "rib"
PEER_INDEX_RECORD = "peer_index"
RETAINED_UNFILTERED = "retained_unfiltered"
RETAINED_TARGET = "retained_target"
RETAINED_ORIGIN_UNKNOWN = "retained_origin_unknown"
RETAINED_PREFIX_CONTEXT = "retained_prefix_context"
DISCARDED_NON_TARGET = "discarded_non_target"

_TABLE_DUMP = 12
_TABLE_DUMP_V2 = 13
_TABLE_DUMP_SUBTYPES = frozenset((1, 2))
_PEER_INDEX_TABLE = 1
_RIB_V2_SUBTYPES = frozenset((2, 4))
_MRT_HEADER_LENGTH = 12
_RIB_SLOT_SECONDS = 8 * 60 * 60
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COLLECTOR_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class RibAdapterError(ValueError):
    """RIB artifact 或 ParsedMrtRecord 流不能安全提升为研究事件。"""


@dataclass(frozen=True)
class RibElementDecision:
    """一个过滤前 element 的 origin 与保留决策审计记录。"""

    element_ordinal: int
    origin_state: str
    origin_asns: Tuple[int, ...]
    filter_decision: str
    quality_flags: Tuple[str, ...]
    route_event_id: Optional[str]


@dataclass(frozen=True)
class AdaptedRibRecord:
    """一个 physical RIB record 的 raw 证据、决策和保留事件。"""

    record_kind: str
    raw_record: RawRecordEvidence
    source_element_count: int
    retained_element_count: int
    discarded_element_count: int
    element_decisions: Tuple[RibElementDecision, ...]
    route_events: Tuple[ResearchRouteEvent, ...]


@dataclass(frozen=True)
class _ArtifactIdentity:
    artifact_id: str
    file_sha256: str
    collector_id: str
    artifact_slot_utc: str
    slot_start_epoch: int
    slot_end_epoch: int


@dataclass(frozen=True)
class _Frame:
    mrt_type: int
    mrt_subtype: int
    event_time_utc: str
    event_epoch: int
    record_kind: str


OriginAsnPredicate = Callable[[int], bool]
VpObserver = Callable[[str, int, str], None]
RibCheckpointObserver = Callable[
    [RibRecordBoundary, Optional[RibPeerIndexContext]], None
]


class ObservedVpAccumulator:
    """只累计唯一 VP 身份；必须在 record stream 耗尽后读取最终值。"""

    def __init__(self, collector_id: str = "rrc25") -> None:
        if (
            not isinstance(collector_id, str)
            or _COLLECTOR_RE.fullmatch(collector_id) is None
        ):
            raise RibAdapterError("VP accumulator collector_id 非法")
        self._collector_id = collector_id
        self._ids_by_peer: dict[Tuple[str, int], str] = {}

    def observe(self, peer_ip: str, peer_asn: int, vp_id: str) -> None:
        try:
            canonical_ip = ipaddress.ip_address(peer_ip).compressed
            expected = vp_id_v1(self._collector_id, canonical_ip, peer_asn)
        except (TypeError, ValueError) as error:
            raise RibAdapterError("VP observer 收到非法 peer 身份") from error
        # accumulator 的 collector 被 vp_id 编码，避免跨 collector 混用。
        if vp_id != expected:
            raise RibAdapterError("VP observer 收到不一致的稳定 vp_id")
        key = (canonical_ip, peer_asn)
        existing = self._ids_by_peer.get(key)
        if existing is not None and existing != vp_id:  # pragma: no cover
            raise RibAdapterError("同一 peer 身份映射到冲突 vp_id")
        self._ids_by_peer[key] = vp_id

    @property
    def observed_vp_ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self._ids_by_peer.values()))

    @property
    def observed_vp_count(self) -> int:
        return len(self._ids_by_peer)


def _utc_second(value: object, field: str) -> Tuple[str, int]:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RibAdapterError(f"{field} 必须是秒级 UTC Z 时间")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise RibAdapterError(f"{field} 不是合法秒级 UTC 时间") from error
    return value, int(parsed.timestamp())


def _normalize_artifact(artifact: Mapping[str, Any]) -> _ArtifactIdentity:
    if not isinstance(artifact, Mapping):
        raise RibAdapterError("artifact 必须是 input_resolver 产出的对象")
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
    missing = tuple(field for field in required if field not in artifact)
    if missing:
        raise RibAdapterError("artifact 缺少字段：" + ",".join(missing))
    if artifact["artifact_type"] != "rib":
        raise RibAdapterError("RIB 适配层只接受 artifact_type=rib")
    file_sha256 = artifact["file_sha256"]
    if not isinstance(file_sha256, str) or _SHA256_RE.fullmatch(file_sha256) is None:
        raise RibAdapterError("artifact.file_sha256 非法")
    try:
        expected_artifact_id = artifact_id_v1(file_sha256)
    except ValueError as error:
        raise RibAdapterError("artifact.file_sha256 不能生成稳定身份") from error
    if artifact["artifact_id"] != expected_artifact_id:
        raise RibAdapterError("artifact_id 与 file_sha256 不一致")
    collector_id = artifact["collector_id"]
    if (
        not isinstance(collector_id, str)
        or _COLLECTOR_RE.fullmatch(collector_id) is None
    ):
        raise RibAdapterError("artifact.collector_id 非法")
    slot_text, slot_start = _utc_second(
        artifact["artifact_time_utc"], "artifact.artifact_time_utc"
    )
    if slot_start % _RIB_SLOT_SECONDS:
        raise RibAdapterError("RIB artifact_time_utc 未按八小时槽对齐")
    relative_path = artifact["relative_path"]
    if not isinstance(relative_path, str):
        raise RibAdapterError("artifact.relative_path 必须是字符串")
    pure_path = PurePosixPath(relative_path)
    if (
        pure_path.is_absolute()
        or not pure_path.parts
        or ".." in pure_path.parts
        or pure_path.parts[0] != collector_id
    ):
        raise RibAdapterError("artifact.relative_path 越出 collector")
    if artifact["compression"] != "gz":
        raise RibAdapterError("RIB 研究输入只接受 gzip 制品")
    size_bytes = artifact["size_bytes"]
    if (
        isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes <= 0
    ):
        raise RibAdapterError("artifact.size_bytes 必须是正整数")
    return _ArtifactIdentity(
        artifact_id=expected_artifact_id,
        file_sha256=file_sha256,
        collector_id=collector_id,
        artifact_slot_utc=slot_text,
        slot_start_epoch=slot_start,
        slot_end_epoch=slot_start + _RIB_SLOT_SECONDS,
    )


def _decode_frame(raw_record: object) -> _Frame:
    if not isinstance(raw_record, bytes) or len(raw_record) < _MRT_HEADER_LENGTH:
        raise RibAdapterError("raw_record 必须是完整 MRT physical record bytes")
    timestamp, mrt_type, mrt_subtype, payload_length = struct.unpack(
        "!IHHI", raw_record[:_MRT_HEADER_LENGTH]
    )
    if len(raw_record) != _MRT_HEADER_LENGTH + payload_length:
        raise RibAdapterError("MRT header length 与 raw_record 长度不一致")
    if mrt_type == _TABLE_DUMP:
        if mrt_subtype not in _TABLE_DUMP_SUBTYPES:
            raise RibAdapterError("TABLE_DUMP subtype 未获准")
        record_kind = RIB_RECORD
    elif mrt_type == _TABLE_DUMP_V2:
        if mrt_subtype == _PEER_INDEX_TABLE:
            record_kind = PEER_INDEX_RECORD
        elif mrt_subtype in _RIB_V2_SUBTYPES:
            record_kind = RIB_RECORD
        else:
            raise RibAdapterError("TABLE_DUMP_V2 subtype 未获准")
    else:
        raise RibAdapterError("RIB 适配层只接受 MRT type 12/13")
    try:
        event_time = datetime.fromtimestamp(timestamp, UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (OverflowError, OSError, ValueError) as error:
        raise RibAdapterError("MRT header 时间超出 UTC 可表示范围") from error
    return _Frame(
        mrt_type=mrt_type,
        mrt_subtype=mrt_subtype,
        event_time_utc=event_time,
        event_epoch=timestamp,
        record_kind=record_kind,
    )


def _decode_peer_index_identities(raw_record: bytes) -> Tuple[Tuple[str, int], ...]:
    """从已验证 PEER_INDEX_TABLE raw record 提取完整 peer 人口。"""

    payload = memoryview(raw_record)[_MRT_HEADER_LENGTH:]
    offset = 0

    def read(length: int, field: str) -> bytes:
        nonlocal offset
        if length < 0 or offset + length > len(payload):
            raise RibAdapterError(f"PEER_INDEX_TABLE.{field} 越界")
        start = offset
        offset += length
        return bytes(payload[start:offset])

    read(4, "collector_bgp_id")
    view_length = int.from_bytes(read(2, "view_name_length"), "big")
    try:
        read(view_length, "view_name").decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RibAdapterError("PEER_INDEX_TABLE view_name 不是合法 UTF-8") from error
    peer_count = int.from_bytes(read(2, "peer_count"), "big")
    peers = []
    for index in range(peer_count):
        peer_type = read(1, f"peer[{index}].type")[0]
        if peer_type & ~0x03:
            raise RibAdapterError("PEER_INDEX_TABLE peer type 含保留位")
        read(4, f"peer[{index}].bgp_id")
        address_width = 16 if peer_type & 0x01 else 4
        asn_width = 4 if peer_type & 0x02 else 2
        try:
            peer_ip = ipaddress.ip_address(
                read(address_width, f"peer[{index}].ip")
            ).compressed
        except ValueError as error:  # pragma: no cover - 固定长度保护
            raise RibAdapterError("PEER_INDEX_TABLE peer IP 非法") from error
        peer_asn = int.from_bytes(read(asn_width, f"peer[{index}].asn"), "big")
        peers.append((peer_ip, peer_asn))
    if offset != len(payload):
        raise RibAdapterError("PEER_INDEX_TABLE 含未消费尾部")
    return tuple(peers)


def _normalize_expected_hashes(
    value: Optional[Mapping[int, str]],
) -> Mapping[int, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RibAdapterError("expected_record_sha256_by_ordinal 必须是 mapping")
    normalized = {}
    for ordinal, digest in value.items():
        if (
            isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or ordinal < 0
        ):
            raise RibAdapterError("expected record ordinal 必须是非负整数")
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise RibAdapterError("expected record SHA256 非法")
        normalized[ordinal] = digest
    return normalized


def _nonnegative_coordinate(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RibAdapterError(f"{field} 必须是非负整数")
    return value


def _normalize_initial_peer_context(
    value: Optional[RibMrtSeekContext],
    *,
    start_record_ordinal: int,
    start_record_offset: int,
    collector_id: str,
) -> Optional[Tuple[Tuple[str, int], ...]]:
    if value is None:
        return None
    if not isinstance(value, RibMrtSeekContext):
        raise RibAdapterError("initial_seek_context 必须由 RIB seek scanner 产生")
    if (
        value.next_record_ordinal != start_record_ordinal
        or value.next_record_offset != start_record_offset
    ):
        raise RibAdapterError("initial_seek_context 与 record 恢复坐标不一致")
    peer_index = value.peer_index
    if peer_index is None:
        return None
    if not isinstance(peer_index, RibPeerIndexContext):  # pragma: no cover
        raise RibAdapterError("initial_seek_context.peer_index 非法")
    if (
        peer_index.record_ordinal >= start_record_ordinal
        or peer_index.record_offset >= start_record_offset
    ):
        raise RibAdapterError("peer index context 必须严格位于恢复点之前")
    if _SHA256_RE.fullmatch(peer_index.record_sha256) is None:
        raise RibAdapterError("peer index context raw SHA256 非法")
    if (
        isinstance(peer_index.record_length, bool)
        or not isinstance(peer_index.record_length, int)
        or peer_index.record_length < _MRT_HEADER_LENGTH
        or peer_index.record_offset + peer_index.record_length > start_record_offset
    ):
        raise RibAdapterError("peer index context record_length 非法")
    if not isinstance(peer_index.peers, tuple):
        raise RibAdapterError("peer index context peers 必须是确定顺序 tuple")
    normalized = []
    for index, peer in enumerate(peer_index.peers):
        if not isinstance(peer, tuple) or len(peer) != 2:
            raise RibAdapterError(f"peer index context peers[{index}] 非法")
        peer_ip, peer_asn = peer
        try:
            canonical_ip = ipaddress.ip_address(peer_ip).compressed
            vp_id_v1(collector_id, canonical_ip, peer_asn)
        except (TypeError, ValueError) as error:
            raise RibAdapterError(
                f"peer index context peers[{index}] 身份非法"
            ) from error
        key = (canonical_ip, peer_asn)
        normalized.append(key)
    return tuple(normalized)


def _raw_evidence(
    identity: _ArtifactIdentity,
    record: ParsedMrtRecord,
    frame: _Frame,
    raw_sha256: str,
) -> RawRecordEvidence:
    return RawRecordEvidence(
        artifact_id=identity.artifact_id,
        file_sha256=identity.file_sha256,
        collector_id=identity.collector_id,
        artifact_slot_utc=identity.artifact_slot_utc,
        record_ordinal=record.record_ordinal,
        record_offset=record.record_offset,
        record_length=len(record.raw_record),
        raw_record_sha256=raw_sha256,
        event_time_utc=frame.event_time_utc,
        event_epoch_microseconds=frame.event_epoch * 1_000_000,
        mrt_type=frame.mrt_type,
        mrt_subtype=frame.mrt_subtype,
    )


def _origin_assessment(
    element: ParsedRouteElement,
    predicate: Optional[OriginAsnPredicate],
) -> Tuple[str, Tuple[int, ...], Optional[bool]]:
    if element.as_path is None:
        raise RibAdapterError("RIB element.as_path 不得为 null")
    try:
        resolution = derive_origin_asns(element.as_path)
    except ValueError as error:
        raise RibAdapterError("RIB element AS_PATH 无法安全推导 origin") from error
    if resolution.state not in {RESOLVED, CONFLICT, UNKNOWN}:
        raise RibAdapterError("RIB element origin_state 非法")
    if predicate is None:
        return resolution.state, resolution.origins, None
    if resolution.state == UNKNOWN:
        # 空路径/confederation 无候选 ASN，无法证明与 IR 无关，保守保留整前缀。
        return resolution.state, resolution.origins, None
    if resolution.state == RESOLVED and len(resolution.origins) != 1:
        raise RibAdapterError("resolved origin 必须且只能有一个 ASN")
    accepted = False
    for origin_asn in resolution.origins:
        try:
            candidate = predicate(origin_asn)
        except Exception as error:
            raise RibAdapterError("origin ASN predicate 执行失败") from error
        if not isinstance(candidate, bool):
            raise RibAdapterError("origin ASN predicate 必须严格返回 bool")
        accepted = accepted or candidate
    return resolution.state, resolution.origins, accepted


def iter_adapted_rib_records(
    records: Iterable[ParsedMrtRecord],
    *,
    artifact: Mapping[str, Any],
    origin_asn_predicate: Optional[OriginAsnPredicate] = None,
    vp_observer: Optional[VpObserver] = None,
    include_discarded_element_decisions: bool = True,
    expected_record_sha256_by_ordinal: Optional[Mapping[int, str]] = None,
    start_record_ordinal: int = 0,
    start_record_offset: int = 0,
    initial_seek_context: Optional[RibMrtSeekContext] = None,
) -> Iterator[AdaptedRibRecord]:
    """逐 physical record 提升 RIB parser 流，不物化整个 RIB。

    非零起点必须与 seek scanner 产出的 context 一致；若起点是
    TABLE_DUMP_V2 RIB，context 会恢复其此前 peer table 人口与成员校验。
    """

    identity = _normalize_artifact(artifact)
    initial_ordinal = _nonnegative_coordinate(
        start_record_ordinal, "start_record_ordinal"
    )
    initial_offset = _nonnegative_coordinate(
        start_record_offset, "start_record_offset"
    )
    initial_peer_identities = _normalize_initial_peer_context(
        initial_seek_context,
        start_record_ordinal=initial_ordinal,
        start_record_offset=initial_offset,
        collector_id=identity.collector_id,
    )
    if origin_asn_predicate is not None and not callable(origin_asn_predicate):
        raise RibAdapterError("origin_asn_predicate 必须可调用")
    if vp_observer is not None and not callable(vp_observer):
        raise RibAdapterError("vp_observer 必须可调用")
    if not isinstance(include_discarded_element_decisions, bool):
        raise RibAdapterError(
            "include_discarded_element_decisions 必须严格为 bool"
        )
    expected_hashes = _normalize_expected_hashes(
        expected_record_sha256_by_ordinal
    )
    if isinstance(records, (str, bytes, bytearray, Mapping)):
        raise RibAdapterError("record stream 必须是 ParsedMrtRecord 可迭代对象")
    try:
        iterator = iter(records)
    except TypeError as error:
        raise RibAdapterError("record stream 不可迭代") from error

    expected_ordinal = initial_ordinal
    expected_offset = initial_offset
    processed_record_count = 0
    seen_expected_hashes = set()
    seen_route_event_ids = set()
    notified_vp_keys: set[Tuple[str, int]] = set()
    v2_peer_keys: Optional[set[Tuple[str, int]]] = (
        set(initial_peer_identities)
        if initial_peer_identities is not None
        else None
    )

    def observe_vp(peer_ip: str, peer_asn: int) -> None:
        # TABLE_DUMP_V2 的 peer table 已经给出完整 VP 人口。绝大多数后续
        # element 会重复引用这些 peer；先按 parser 已规范化的稳定 key 去重，
        # 避免为每个 element 重算一次 vp_id SHA256。
        key = (peer_ip, peer_asn)
        if key in notified_vp_keys:
            return
        try:
            vp_id = vp_id_v1(identity.collector_id, peer_ip, peer_asn)
        except ValueError as error:
            raise RibAdapterError("RIB 含非法 peer 身份") from error
        notified_vp_keys.add(key)
        if vp_observer is None:
            return
        try:
            vp_observer(peer_ip, peer_asn, vp_id)
        except Exception as error:
            if isinstance(error, RibAdapterError):
                raise
            raise RibAdapterError("vp_observer 执行失败") from error

    if initial_peer_identities is not None:
        for peer_ip, peer_asn in initial_peer_identities:
            observe_vp(peer_ip, peer_asn)

    for record in iterator:
        processed_record_count += 1
        if not isinstance(record, ParsedMrtRecord):
            raise RibAdapterError("record stream 含非 ParsedMrtRecord")
        if (
            isinstance(record.record_ordinal, bool)
            or not isinstance(record.record_ordinal, int)
            or record.record_ordinal < 0
        ):
            raise RibAdapterError("record_ordinal 必须是非负整数")
        if (
            isinstance(record.record_offset, bool)
            or not isinstance(record.record_offset, int)
            or record.record_offset < 0
        ):
            raise RibAdapterError("record_offset 必须是非负整数")
        if record.record_ordinal != expected_ordinal:
            raise RibAdapterError("record_ordinal 必须从声明恢复起点连续递增")
        if record.record_offset != expected_offset:
            raise RibAdapterError("record_offset 必须从声明恢复起点连续覆盖解压字节流")
        if not isinstance(record.elements, tuple):
            raise RibAdapterError("ParsedMrtRecord.elements 必须是确定顺序 tuple")

        frame = _decode_frame(record.raw_record)
        if not (
            identity.slot_start_epoch <= frame.event_epoch < identity.slot_end_epoch
        ):
            raise RibAdapterError("MRT record 时间越出 artifact 八小时槽")
        if frame.record_kind == PEER_INDEX_RECORD and record.elements:
            raise RibAdapterError("PEER_INDEX_TABLE 必须是零 element physical record")
        if frame.mrt_type == _TABLE_DUMP and len(record.elements) != 1:
            raise RibAdapterError("TABLE_DUMP physical record 必须恰有一个 element")

        raw_sha256 = hashlib.sha256(record.raw_record).hexdigest()
        expected_hash = expected_hashes.get(record.record_ordinal)
        if expected_hash is not None:
            seen_expected_hashes.add(record.record_ordinal)
            if raw_sha256 != expected_hash:
                raise RibAdapterError("physical record SHA256 与显式期望不一致")
        raw_evidence = _raw_evidence(identity, record, frame, raw_sha256)

        if frame.record_kind == PEER_INDEX_RECORD:
            peer_identities = _decode_peer_index_identities(record.raw_record)
            v2_peer_keys = set(peer_identities)
            for peer_ip, peer_asn in peer_identities:
                observe_vp(peer_ip, peer_asn)
        elif frame.mrt_type == _TABLE_DUMP:
            # V1 不依赖 V2 peer table；混合流不能沿用过期索引。
            v2_peer_keys = None
        elif v2_peer_keys is None:
            raise RibAdapterError("TABLE_DUMP_V2 RIB 缺少 PEER_INDEX_TABLE")

        if isinstance(record, ParsedNonTargetRibRecord):
            if (
                frame.mrt_type != _TABLE_DUMP_V2
                or frame.record_kind != RIB_RECORD
                or origin_asn_predicate is None
                or include_discarded_element_decisions
                or record.elements
                or isinstance(record.source_element_count, bool)
                or not isinstance(record.source_element_count, int)
                or record.source_element_count <= 0
            ):
                raise RibAdapterError(
                    "parser non-target elision record 与 adapter 合同不一致"
                )
            yield AdaptedRibRecord(
                record_kind=frame.record_kind,
                raw_record=raw_evidence,
                source_element_count=record.source_element_count,
                retained_element_count=0,
                discarded_element_count=record.source_element_count,
                element_decisions=(),
                route_events=(),
            )
            expected_ordinal += 1
            expected_offset += len(record.raw_record)
            continue

        # 先只分析当前 physical record。TABLE_DUMP_V2 的一个 RIB record 是
        # 单个 prefix 的全部 peer entries；过滤必须以整个前缀为单位，避免命中
        # IR 的同时丢掉其非 IR MOAS origin 或其他 VP 观测。
        assessments = []
        for element_ordinal, element in enumerate(record.elements):
            if not isinstance(element, ParsedRouteElement):
                raise RibAdapterError("RIB elements 含非 ParsedRouteElement")
            if element.action != "rib_snapshot":
                raise RibAdapterError("RIB artifact 只接受 rib_snapshot action")
            if (
                frame.mrt_type == _TABLE_DUMP_V2
                and (element.peer_ip, element.peer_asn) not in v2_peer_keys
            ):
                raise RibAdapterError("RIB element peer 不属于当前 PEER_INDEX_TABLE")
            origin_state, origins, target_membership = _origin_assessment(
                element, origin_asn_predicate
            )
            assessments.append(
                (element_ordinal, element, origin_state, origins, target_membership)
            )
        prefixes = {element.prefix for _ordinal, element, *_rest in assessments}
        if len(prefixes) > 1:
            raise RibAdapterError("单个 RIB physical record 不得混合多个 prefix")
        retain_prefix = origin_asn_predicate is None or any(
            membership is not False
            for _ordinal, _element, _state, _origins, membership in assessments
        )

        route_events = []
        decisions = []
        discarded = 0
        if not retain_prefix and not include_discarded_element_decisions:
            # assessments 已完整覆盖当前 prefix 的全部 entry，且每项 membership
            # 都严格为 False。worker 不消费逐 element discard 决策时，直接按
            # source 人口记账，避免再次遍历并分配短命对象。
            discarded = len(assessments)
        for (
            element_ordinal,
            element,
            origin_state,
            origins,
            target_membership,
        ) in assessments:
            if not retain_prefix:
                if not include_discarded_element_decisions:
                    continue
                discarded += 1
                try:
                    discarded_event = build_research_route_event(
                        artifact_id=identity.artifact_id,
                        file_sha256=identity.file_sha256,
                        collector_id=identity.collector_id,
                        artifact_slot_utc=identity.artifact_slot_utc,
                        record_ordinal=record.record_ordinal,
                        element_ordinal=element_ordinal,
                        element=element,
                    )
                except StateReplayError as error:
                    raise RibAdapterError(
                        "被过滤 RIB element 不符合研究回放合同"
                    ) from error
                if discarded_event.route_event_id in seen_route_event_ids:
                    raise RibAdapterError("稳定 RouteEvent 身份重复")
                seen_route_event_ids.add(discarded_event.route_event_id)
                decisions.append(
                    RibElementDecision(
                        element_ordinal=element_ordinal,
                        origin_state=origin_state,
                        origin_asns=origins,
                        filter_decision=DISCARDED_NON_TARGET,
                        quality_flags=discarded_event.quality_flags,
                        route_event_id=None,
                    )
                )
                continue
            if origin_asn_predicate is None:
                decision = RETAINED_UNFILTERED
            elif origin_state != RESOLVED:
                decision = RETAINED_ORIGIN_UNKNOWN
            elif target_membership is True:
                decision = RETAINED_TARGET
            else:
                decision = RETAINED_PREFIX_CONTEXT
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
                raise RibAdapterError(
                    "RIB element 不符合研究回放合同"
                ) from error
            if route_event.route_event_id in seen_route_event_ids:
                raise RibAdapterError("稳定 RouteEvent 身份重复")
            seen_route_event_ids.add(route_event.route_event_id)
            route_events.append(route_event)
            decisions.append(
                RibElementDecision(
                    element_ordinal=element_ordinal,
                    origin_state=origin_state,
                    origin_asns=origins,
                    filter_decision=decision,
                    quality_flags=route_event.quality_flags,
                    route_event_id=route_event.route_event_id,
                )
            )

        if frame.mrt_type == _TABLE_DUMP:
            # V2 的完整 VP 人口已由 PEER_INDEX_TABLE（或恢复 context）观察，
            # 不再逐 element 做重复 key lookup。V1 没有 peer table，仍逐项观察。
            for _ordinal, element, _state, _origins, _membership in assessments:
                observe_vp(element.peer_ip, element.peer_asn)

        yield AdaptedRibRecord(
            record_kind=frame.record_kind,
            raw_record=raw_evidence,
            source_element_count=len(record.elements),
            retained_element_count=len(route_events),
            discarded_element_count=discarded,
            element_decisions=tuple(decisions),
            route_events=tuple(route_events),
        )
        expected_ordinal += 1
        expected_offset += len(record.raw_record)

    if processed_record_count == 0:
        raise RibAdapterError("RIB record stream 为空")
    missing_expected = sorted(set(expected_hashes) - seen_expected_hashes)
    if missing_expected:
        raise RibAdapterError(
            "显式 record hash 引用了不存在的 ordinal："
            + ",".join(str(value) for value in missing_expected)
        )


def iter_rib_artifact_records(
    source: bytes | bytearray | memoryview | BinaryIO,
    *,
    artifact: Mapping[str, Any],
    origin_asn_predicate: Optional[OriginAsnPredicate] = None,
    vp_observer: Optional[VpObserver] = None,
    include_discarded_element_decisions: bool = True,
    expected_record_sha256_by_ordinal: Optional[Mapping[int, str]] = None,
    start_record_ordinal: int = 0,
    start_record_offset: int = 0,
    previous_record_boundary: Optional[
        RibRecordBoundary | Mapping[str, Any]
    ] = None,
    peer_index_context: Optional[
        RibPeerIndexContext | Mapping[str, Any]
    ] = None,
) -> Iterator[AdaptedRibRecord]:
    """从解压 bytes/stream 的声明 physical-record 起点流式提升事件。"""

    # 在 parser 读取任何字节之前先失败关闭 artifact 身份与槽位。
    _normalize_artifact(artifact)
    try:
        if start_record_ordinal == 0 and start_record_offset == 0:
            if previous_record_boundary is not None or peer_index_context is not None:
                raise RibAdapterError(
                    "流开头不得携带 previous boundary 或 peer context"
                )
            parsed_records: Iterable[ParsedMrtRecord] = iter_rib_mrt_records(
                source,
                origin_asn_predicate=origin_asn_predicate,
                elide_non_target_elements=(
                    origin_asn_predicate is not None
                    and not include_discarded_element_decisions
                ),
            )
            seek_context = None
        else:
            seek_records = iter_rib_mrt_records_from_offset(
                source,
                next_record_ordinal=start_record_ordinal,
                next_record_offset=start_record_offset,
                previous_record_boundary=previous_record_boundary,
                peer_index_context=peer_index_context,
                origin_asn_predicate=origin_asn_predicate,
                elide_non_target_elements=(
                    origin_asn_predicate is not None
                    and not include_discarded_element_decisions
                ),
            )
            parsed_records = seek_records
            seek_context = seek_records.seek_context
        yield from iter_adapted_rib_records(
            parsed_records,
            artifact=artifact,
            origin_asn_predicate=origin_asn_predicate,
            vp_observer=vp_observer,
            include_discarded_element_decisions=(
                include_discarded_element_decisions
            ),
            expected_record_sha256_by_ordinal=expected_record_sha256_by_ordinal,
            start_record_ordinal=start_record_ordinal,
            start_record_offset=start_record_offset,
            initial_seek_context=seek_context,
        )
    except RibMrtParseError as error:
        raise RibAdapterError("RIB MRT parser 失败") from error


def iter_rib_spool_artifact_records(
    spool_path: os.PathLike[str] | str,
    *,
    expected_decompressed_sha256: str,
    expected_decompressed_size_bytes: int,
    next_record_ordinal: int,
    next_record_offset: int,
    previous_record_boundary: Optional[
        RibRecordBoundary | Mapping[str, Any]
    ] = None,
    peer_index_context: Optional[
        RibPeerIndexContext | Mapping[str, Any]
    ] = None,
    artifact: Mapping[str, Any],
    origin_asn_predicate: Optional[OriginAsnPredicate] = None,
    vp_observer: Optional[VpObserver] = None,
    include_discarded_element_decisions: bool = True,
    checkpoint_observer: Optional[RibCheckpointObserver] = None,
    expected_record_sha256_by_ordinal: Optional[Mapping[int, str]] = None,
) -> Iterator[AdaptedRibRecord]:
    """核验 spool，并在同一 descriptor 上恢复 peer context 后提升事件。

    每个 physical record 完成 parser 与 adapter 处理后，先调用
    ``checkpoint_observer(boundary, current_peer_context)``，再把对应 adapted
    record 交给调用方。因此调用方收到 record 时，持久化坐标已与之对齐；
    observer 异常直接传播且该 record 不会被 yield。
    """

    # artifact 失败必须发生在打开、核验 spool 之前。
    _normalize_artifact(artifact)
    if checkpoint_observer is not None and not callable(checkpoint_observer):
        raise RibAdapterError("checkpoint_observer 必须可调用")
    records = None
    try:
        records = iter_rib_spool_records(
            spool_path,
            expected_decompressed_sha256=expected_decompressed_sha256,
            expected_decompressed_size_bytes=expected_decompressed_size_bytes,
            next_record_ordinal=next_record_ordinal,
            next_record_offset=next_record_offset,
            previous_record_boundary=previous_record_boundary,
            peer_index_context=peer_index_context,
            origin_asn_predicate=origin_asn_predicate,
            elide_non_target_elements=(
                origin_asn_predicate is not None
                and not include_discarded_element_decisions
            ),
        )
        adapted_records = iter_adapted_rib_records(
            records,
            artifact=artifact,
            origin_asn_predicate=origin_asn_predicate,
            vp_observer=vp_observer,
            include_discarded_element_decisions=(
                include_discarded_element_decisions
            ),
            expected_record_sha256_by_ordinal=expected_record_sha256_by_ordinal,
            start_record_ordinal=next_record_ordinal,
            start_record_offset=next_record_offset,
            initial_seek_context=records.seek_context,
        )
        for adapted in adapted_records:
            boundary = records.previous_record_boundary
            if (
                boundary is None
                or boundary.record_ordinal
                != adapted.raw_record.record_ordinal
                or boundary.record_offset != adapted.raw_record.record_offset
                or boundary.record_length != adapted.raw_record.record_length
                or boundary.record_sha256
                != adapted.raw_record.raw_record_sha256
            ):
                raise RibAdapterError(
                    "RIB parser checkpoint boundary 与 adapted record 不一致"
                )
            if checkpoint_observer is not None:
                checkpoint_observer(
                    boundary, records.current_peer_index_context
                )
            yield adapted
    except RibMrtParseError as error:
        raise RibAdapterError("RIB spool MRT parser 失败") from error
    finally:
        if records is not None:
            records.close()


__all__ = (
    "AdaptedRibRecord",
    "DISCARDED_NON_TARGET",
    "PEER_INDEX_RECORD",
    "ObservedVpAccumulator",
    "RETAINED_ORIGIN_UNKNOWN",
    "RETAINED_PREFIX_CONTEXT",
    "RETAINED_TARGET",
    "RETAINED_UNFILTERED",
    "RIB_RECORD",
    "RibAdapterError",
    "RibCheckpointObserver",
    "RibElementDecision",
    "iter_adapted_rib_records",
    "iter_rib_artifact_records",
    "iter_rib_spool_artifact_records",
)
