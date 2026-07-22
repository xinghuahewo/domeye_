"""研究闭环使用的 RFC 6396 RIB MRT 小样本解析器。

本模块只解析解压后的 MRT 字节流，不打开真实制品、不写数据库，也不改变
生产 ``BgpdumpRecordStreamFactory`` 对 RIB 的失败关闭边界。输出复用
RouteEvent 的
``ParsedMrtRecord`` / ``ParsedRouteElement`` 合同，因此 physical record ordinal、
解压流 offset 和 element ordinal 可以直接参与稳定身份计算。

获准范围：

* TABLE_DUMP (type 12) 的 IPv4/IPv6 子类型；
* TABLE_DUMP_V2 (type 13) 的 PEER_INDEX_TABLE、RIB_IPV4_UNICAST 和
  RIB_IPV6_UNICAST。

multicast、RIB_GENERIC、Add-Path、未知类型和损坏/越界输入全部失败关闭。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import io
import ipaddress
import struct
from typing import BinaryIO, Iterator, Optional, Tuple, Union

from ...route_event.index import AsPathSegment, ParsedMrtRecord, ParsedRouteElement


MRT_HEADER_LENGTH = 12
MAX_MRT_PAYLOAD_BYTES = 64 * 1024 * 1024

TABLE_DUMP = 12
TABLE_DUMP_V2 = 13

TABLE_DUMP_IPV4 = 1
TABLE_DUMP_IPV6 = 2

PEER_INDEX_TABLE = 1
RIB_IPV4_UNICAST = 2
RIB_IPV4_MULTICAST = 3
RIB_IPV6_UNICAST = 4
RIB_IPV6_MULTICAST = 5
RIB_GENERIC = 6

_AS_PATH_ATTRIBUTE = 2
_AS_PATH_SEGMENT_TYPES = {
    1: "as_set",
    2: "as_sequence",
    3: "confederation_sequence",
    4: "confederation_set",
}


class RibMrtParseError(ValueError):
    """RIB MRT 输入无法按冻结范围安全解析。"""


@dataclass(frozen=True)
class _Peer:
    peer_ip: str
    peer_asn: int


class _Cursor:
    """对单个 MRT payload 做有界网络字节序读取。"""

    def __init__(self, value: bytes, context: str) -> None:
        self._value = memoryview(value)
        self._offset = 0
        self._context = context

    @property
    def remaining(self) -> int:
        return len(self._value) - self._offset

    def read(self, length: int, field: str) -> bytes:
        if length < 0 or self._offset + length > len(self._value):
            raise RibMrtParseError(
                f"{self._context}.{field} 越界：需要 {length} 字节，"
                f"仅剩 {self.remaining} 字节"
            )
        start = self._offset
        self._offset += length
        return bytes(self._value[start : self._offset])

    def uint8(self, field: str) -> int:
        return self.read(1, field)[0]

    def uint16(self, field: str) -> int:
        return struct.unpack("!H", self.read(2, field))[0]

    def uint32(self, field: str) -> int:
        return struct.unpack("!I", self.read(4, field))[0]

    def finish(self) -> None:
        if self.remaining:
            raise RibMrtParseError(
                f"{self._context} 含 {self.remaining} 字节未消费尾部"
            )


def _read_exact(stream: BinaryIO, length: int, field: str) -> bytes:
    chunks = []
    remaining = length
    while remaining:
        chunk = stream.read(remaining)
        if not isinstance(chunk, (bytes, bytearray)):
            raise RibMrtParseError(f"{field} 的 stream.read() 必须返回 bytes")
        if not chunk:
            received = length - remaining
            raise RibMrtParseError(
                f"{field} 截断：需要 {length} 字节，仅得到 {received} 字节"
            )
        raw = bytes(chunk)
        if len(raw) > remaining:
            raise RibMrtParseError(f"{field} 的 stream.read(size) 返回超过请求长度")
        chunks.append(raw)
        remaining -= len(raw)
    return b"".join(chunks)


def _read_header_or_eof(stream: BinaryIO) -> Optional[bytes]:
    first = stream.read(MRT_HEADER_LENGTH)
    if not isinstance(first, (bytes, bytearray)):
        raise RibMrtParseError("MRT stream.read() 必须返回 bytes")
    header = bytes(first)
    if not header:
        return None
    if len(header) < MRT_HEADER_LENGTH:
        header += _read_exact(
            stream, MRT_HEADER_LENGTH - len(header), "MRT common header"
        )
    return header


def _event_time_utc(epoch_seconds: int) -> str:
    try:
        return datetime.fromtimestamp(epoch_seconds, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (OverflowError, OSError, ValueError) as error:
        raise RibMrtParseError("MRT 时间戳超出 UTC 可表示范围") from error


def _normalize_address(raw: bytes, field: str) -> str:
    try:
        return ipaddress.ip_address(raw).compressed
    except ValueError as error:
        raise RibMrtParseError(f"{field} 不是有效 IP 地址") from error


def _normalize_full_prefix(raw: bytes, prefix_length: int, field: str) -> str:
    maximum = len(raw) * 8
    if prefix_length > maximum:
        raise RibMrtParseError(
            f"{field} 长度 {prefix_length} 超过地址族上限 {maximum}"
        )
    address = ipaddress.ip_address(raw)
    return ipaddress.ip_network((address, prefix_length), strict=False).compressed


def _normalize_compact_prefix(
    raw: bytes, prefix_length: int, address_bytes: int, field: str
) -> str:
    maximum = address_bytes * 8
    if prefix_length > maximum:
        raise RibMrtParseError(
            f"{field} 长度 {prefix_length} 超过地址族上限 {maximum}"
        )
    expected = (prefix_length + 7) // 8
    if len(raw) != expected:
        raise RibMrtParseError(f"{field} 编码长度与 prefix length 不一致")
    return _normalize_full_prefix(
        raw.ljust(address_bytes, b"\x00"), prefix_length, field
    )


def _parse_as_path(value: bytes, asn_width: int) -> Tuple[AsPathSegment, ...]:
    cursor = _Cursor(value, "AS_PATH")
    segments = []
    total_asns = 0
    while cursor.remaining:
        segment_code = cursor.uint8("segment_type")
        segment_type = _AS_PATH_SEGMENT_TYPES.get(segment_code)
        if segment_type is None:
            raise RibMrtParseError(
                f"AS_PATH 含未知 segment type {segment_code}"
            )
        asn_count = cursor.uint8("segment_length")
        if asn_count == 0:
            raise RibMrtParseError("AS_PATH segment 不得为空")
        if len(segments) >= 4096 or total_asns + asn_count > 4096:
            raise RibMrtParseError("AS_PATH 超过 RouteEvent 合同上限 4096")
        asns = []
        for index in range(asn_count):
            raw_asn = cursor.read(asn_width, f"asn[{index}]")
            asns.append(int.from_bytes(raw_asn, "big"))
        segments.append(AsPathSegment(segment_type, tuple(asns)))
        total_asns += asn_count
    return tuple(segments)


def _parse_bgp_attributes(
    value: bytes, *, asn_width: int
) -> Tuple[AsPathSegment, ...]:
    cursor = _Cursor(value, "BGP attributes")
    as_path: Optional[Tuple[AsPathSegment, ...]] = None
    while cursor.remaining:
        flags = cursor.uint8("flags")
        type_code = cursor.uint8("type_code")
        if flags & 0x0F:
            raise RibMrtParseError("BGP attribute flags 含未定义低四位")
        if flags & 0x10:
            length = cursor.uint16("extended_length")
        else:
            length = cursor.uint8("length")
        attribute = cursor.read(length, f"attribute[{type_code}]")
        if type_code != _AS_PATH_ATTRIBUTE:
            # 未知或本研究不消费的属性，仅在其边界完整时跳过。
            continue
        if as_path is not None:
            raise RibMrtParseError("BGP attributes 含重复 AS_PATH")
        if flags & 0xE0 != 0x40:
            raise RibMrtParseError("AS_PATH 必须使用 well-known transitive flags")
        as_path = _parse_as_path(attribute, asn_width)
    if as_path is None:
        raise RibMrtParseError("RIB entry 缺少 AS_PATH 属性")
    return as_path


def _path_quality_flags(
    path: Tuple[AsPathSegment, ...]
) -> Tuple[str, ...]:
    flags = set()
    if not path:
        flags.add("empty_as_path")
    if any(segment.segment_type == "as_set" for segment in path):
        flags.add("as_set_present")
    if any(segment.segment_type.startswith("confederation_") for segment in path):
        flags.add("confederation_segment_present")
    if path and path[-1].segment_type != "as_sequence":
        flags.add("origin_ambiguous")
    return tuple(sorted(flags))


def _route_element(
    *,
    originated_time: int,
    peer: _Peer,
    prefix: str,
    afi_safi: str,
    as_path: Tuple[AsPathSegment, ...],
) -> ParsedRouteElement:
    return ParsedRouteElement(
        event_time_utc=_event_time_utc(originated_time),
        peer_ip=peer.peer_ip,
        peer_asn=peer.peer_asn,
        action="rib_snapshot",
        prefix=prefix,
        afi_safi=afi_safi,
        as_path=as_path,
        quality_flags=_path_quality_flags(as_path),
    )


def _parse_table_dump(payload: bytes, subtype: int) -> Tuple[ParsedRouteElement, ...]:
    if subtype == TABLE_DUMP_IPV4:
        address_bytes = 4
        afi_safi = "ipv4_unicast"
    elif subtype == TABLE_DUMP_IPV6:
        address_bytes = 16
        afi_safi = "ipv6_unicast"
    else:
        raise RibMrtParseError(f"TABLE_DUMP 未获准 subtype {subtype}")

    cursor = _Cursor(payload, "TABLE_DUMP")
    cursor.uint16("view_number")
    cursor.uint16("sequence_number")
    prefix_raw = cursor.read(address_bytes, "prefix")
    prefix_length = cursor.uint8("prefix_length")
    cursor.uint8("status")
    originated_time = cursor.uint32("originated_time")
    peer_ip = _normalize_address(cursor.read(address_bytes, "peer_ip"), "peer_ip")
    peer_asn = cursor.uint16("peer_asn")
    attributes_length = cursor.uint16("attributes_length")
    attributes = cursor.read(attributes_length, "attributes")
    cursor.finish()

    prefix = _normalize_full_prefix(prefix_raw, prefix_length, "prefix")
    as_path = _parse_bgp_attributes(attributes, asn_width=2)
    return (
        _route_element(
            originated_time=originated_time,
            peer=_Peer(peer_ip, peer_asn),
            prefix=prefix,
            afi_safi=afi_safi,
            as_path=as_path,
        ),
    )


def _parse_peer_index_table(payload: bytes) -> Tuple[_Peer, ...]:
    cursor = _Cursor(payload, "PEER_INDEX_TABLE")
    cursor.read(4, "collector_bgp_id")
    view_length = cursor.uint16("view_name_length")
    view_name = cursor.read(view_length, "view_name")
    try:
        view_name.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RibMrtParseError("PEER_INDEX_TABLE view_name 不是合法 UTF-8") from error
    peer_count = cursor.uint16("peer_count")
    peers = []
    for index in range(peer_count):
        peer_type = cursor.uint8(f"peer[{index}].type")
        if peer_type & ~0x03:
            raise RibMrtParseError(
                f"PEER_INDEX_TABLE peer[{index}] type 含保留位"
            )
        cursor.read(4, f"peer[{index}].bgp_id")
        address_bytes = 16 if peer_type & 0x01 else 4
        asn_bytes = 4 if peer_type & 0x02 else 2
        peer_ip = _normalize_address(
            cursor.read(address_bytes, f"peer[{index}].ip"),
            f"peer[{index}].ip",
        )
        peer_asn = int.from_bytes(
            cursor.read(asn_bytes, f"peer[{index}].asn"), "big"
        )
        peers.append(_Peer(peer_ip, peer_asn))
    cursor.finish()
    return tuple(peers)


def _parse_table_dump_v2_rib(
    payload: bytes,
    subtype: int,
    peers: Optional[Tuple[_Peer, ...]],
) -> Tuple[ParsedRouteElement, ...]:
    if peers is None:
        raise RibMrtParseError("TABLE_DUMP_V2 RIB 缺少此前的 PEER_INDEX_TABLE")
    if subtype == RIB_IPV4_UNICAST:
        address_bytes = 4
        afi_safi = "ipv4_unicast"
    elif subtype == RIB_IPV6_UNICAST:
        address_bytes = 16
        afi_safi = "ipv6_unicast"
    elif subtype in {RIB_IPV4_MULTICAST, RIB_IPV6_MULTICAST}:
        raise RibMrtParseError("TABLE_DUMP_V2 multicast subtype 未获准")
    elif subtype == RIB_GENERIC:
        raise RibMrtParseError("TABLE_DUMP_V2 RIB_GENERIC 未获准")
    else:
        # RFC 8050 Add-Path 与未来/未知 subtype 均在本研究中失败关闭。
        raise RibMrtParseError(f"TABLE_DUMP_V2 未获准 subtype {subtype}")

    cursor = _Cursor(payload, "TABLE_DUMP_V2 RIB")
    cursor.uint32("sequence_number")
    prefix_length = cursor.uint8("prefix_length")
    prefix_octets = (prefix_length + 7) // 8
    prefix_raw = cursor.read(prefix_octets, "prefix")
    prefix = _normalize_compact_prefix(
        prefix_raw, prefix_length, address_bytes, "prefix"
    )
    entry_count = cursor.uint16("entry_count")
    elements = []
    for index in range(entry_count):
        peer_index = cursor.uint16(f"entry[{index}].peer_index")
        if peer_index >= len(peers):
            raise RibMrtParseError(
                f"TABLE_DUMP_V2 entry[{index}] peer_index {peer_index} 越界"
            )
        originated_time = cursor.uint32(f"entry[{index}].originated_time")
        attributes_length = cursor.uint16(f"entry[{index}].attributes_length")
        attributes = cursor.read(attributes_length, f"entry[{index}].attributes")
        as_path = _parse_bgp_attributes(attributes, asn_width=4)
        elements.append(
            _route_element(
                originated_time=originated_time,
                peer=peers[peer_index],
                prefix=prefix,
                afi_safi=afi_safi,
                as_path=as_path,
            )
        )
    cursor.finish()
    return tuple(elements)


MrtSource = Union[bytes, bytearray, memoryview, BinaryIO]


def iter_rib_mrt_records(source: MrtSource) -> Iterator[ParsedMrtRecord]:
    """顺序解析一个解压后的 RIB MRT 字节流。

    ``record_ordinal`` 从 0 连续递增，``record_offset`` 覆盖完整解压字节流；
    PEER_INDEX_TABLE 自身产生零元素 record，但仍保留原始 physical record。
    """

    if isinstance(source, (bytes, bytearray, memoryview)):
        stream: BinaryIO = io.BytesIO(bytes(source))
    elif hasattr(source, "read"):
        stream = source  # type: ignore[assignment]
    else:
        raise RibMrtParseError("source 必须是 bytes 或 binary stream")

    record_ordinal = 0
    record_offset = 0
    peers: Optional[Tuple[_Peer, ...]] = None
    while True:
        header = _read_header_or_eof(stream)
        if header is None:
            if record_ordinal == 0:
                raise RibMrtParseError("MRT stream 为空")
            return
        _timestamp, mrt_type, subtype, payload_length = struct.unpack(
            "!IHHI", header
        )
        if payload_length > MAX_MRT_PAYLOAD_BYTES:
            raise RibMrtParseError("MRT payload 超过 64 MiB 安全上限")
        payload = _read_exact(
            stream, payload_length, f"MRT record[{record_ordinal}].payload"
        )
        raw_record = header + payload

        if mrt_type == TABLE_DUMP:
            # 避免混合/损坏流在之后错误复用过期的 V2 peer table。
            peers = None
            elements = _parse_table_dump(payload, subtype)
        elif mrt_type == TABLE_DUMP_V2:
            if subtype == PEER_INDEX_TABLE:
                peers = _parse_peer_index_table(payload)
                elements = ()
            else:
                elements = _parse_table_dump_v2_rib(payload, subtype, peers)
        else:
            raise RibMrtParseError(f"RIB 解析器不接受 MRT type {mrt_type}")

        yield ParsedMrtRecord(
            record_ordinal=record_ordinal,
            record_offset=record_offset,
            raw_record=raw_record,
            elements=elements,
        )
        record_ordinal += 1
        record_offset += len(raw_record)


def parse_rib_mrt_bytes(
    value: Union[bytes, bytearray, memoryview]
) -> Tuple[ParsedMrtRecord, ...]:
    """解析极小内存 fixture，并返回确定顺序 tuple。"""

    return tuple(iter_rib_mrt_records(value))


__all__ = (
    "MAX_MRT_PAYLOAD_BYTES",
    "RibMrtParseError",
    "iter_rib_mrt_records",
    "parse_rib_mrt_bytes",
)
