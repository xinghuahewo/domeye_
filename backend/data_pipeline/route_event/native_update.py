"""研究型原生 MRT BGP4MP UPDATE/STATE_CHANGE 单次读取解析器。

该模块是 ``bgpdump`` 文本适配器的并行研究实现，不替换兼容 oracle。它直接从
gzip 解压流解析 MRT physical record 与 BGP UPDATE，避免子进程 stdin/stdout、
文本膨胀和逐行字段规范化，同时保留相同的 ``ParsedMrtRecord`` 合同：

* gzip 压缩字节只读一次，并在同一次打开中核验 size/SHA256/文件稳定性；
* raw record 的 ordinal、解压 offset、完整 bytes 和 hash-chain 均保持；
* 只接受 MRT type 16/17、STATE_CHANGE subtype 0/5、MESSAGE subtype 1/4；
* Add-Path、LOCAL、未知 AFI/SAFI、未知属性、重复属性和不闭合结构失败关闭；
* UPDATE 元素顺序冻结为 bgpdump 1.6.2 ``-m -p`` 顺序：标准 IPv4 withdraw、
  MP IPv4/IPv6 withdraw、标准 IPv4 announce、MP IPv4/IPv6 announce；
* 标准 IPv4/MP End-of-RIB 只作为 control record 保留，不伪造成 RouteEvent；
* AS_PATH 保留 sequence/set/confederation segment。2-byte ASN 会按 RFC 6793
  的 AS4_PATH 规则合并；AS4 message 中出现 AS4_PATH 被视为歧义并拒绝。

本实现没有因跳过可选属性而假装完整：仅 allowlist 内且长度/flags 合法的属性
可以通过。遇到新属性时应先增加黄金 fixture 与 bgpdump 差分，再扩展 allowlist。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import ipaddress
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import struct
import sys
from typing import Any, BinaryIO, Dict, Iterable, Mapping, Optional, Sequence, Tuple
import zlib

from .artifacts import (
    PILOT_ABSOLUTE_MAX_ARTIFACTS,
    PILOT_ABSOLUTE_MAX_COMPRESSED_BYTES,
    PILOT_ABSOLUTE_MAX_PHYSICAL_RECORDS,
    PILOT_ABSOLUTE_MAX_ROUTE_EVENTS,
    PILOT_ABSOLUTE_MAX_SPOOL_BYTES,
    artifact_id_v1,
    canonical_json,
)
from .index import (
    AsPathSegment,
    ParsedMrtRecord,
    ParsedRouteElement,
    RouteEventInputError,
)


NATIVE_UPDATE_PARSER_NAME = "native_bgp4mp_update"
NATIVE_UPDATE_PARSER_VERSION = "1.1.0"
NATIVE_UPDATE_COMMAND_TOKEN = "in_process_native_bgp4mp_v1"
NATIVE_UPDATE_EXECUTION_POLICY = "verified_in_process_source"

_PARSER_ATTESTATION_FINGERPRINT_SCHEMA = "parser_attestation_fingerprint_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COLLECTOR_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_MRT_HEADER_LENGTH = 12
_MRT_TYPES = frozenset((16, 17))
_STATE_SUBTYPES = frozenset((0, 5))
_MESSAGE_SUBTYPES = frozenset((1, 4))
_ALLOWED_SUBTYPES = _STATE_SUBTYPES | _MESSAGE_SUBTYPES
_ASN_WIDTH_BY_SUBTYPE = {0: 2, 1: 2, 4: 4, 5: 4}
_ADDRESS_WIDTH_BY_AFI = {1: 4, 2: 16}

_BGP_MARKER = b"\xff" * 16
_BGP_OPEN = 1
_BGP_UPDATE = 2
_BGP_NOTIFICATION = 3
_BGP_KEEPALIVE = 4
_BGP_MAX_MESSAGE_BYTES = 65_535

_AS_SET = 1
_AS_SEQUENCE = 2
_AS_CONFED_SEQUENCE = 3
_AS_CONFED_SET = 4
_AS_SEGMENT_NAMES = {
    _AS_SET: "as_set",
    _AS_SEQUENCE: "as_sequence",
    _AS_CONFED_SEQUENCE: "confederation_sequence",
    _AS_CONFED_SET: "confederation_set",
}
_AS4_ALLOWED_SEGMENTS = frozenset((_AS_SET, _AS_SEQUENCE))

_ATTR_ORIGIN = 1
_ATTR_AS_PATH = 2
_ATTR_NEXT_HOP = 3
_ATTR_MED = 4
_ATTR_LOCAL_PREF = 5
_ATTR_ATOMIC_AGGREGATE = 6
_ATTR_AGGREGATOR = 7
_ATTR_COMMUNITIES = 8
_ATTR_ORIGINATOR_ID = 9
_ATTR_CLUSTER_LIST = 10
_ATTR_MP_REACH = 14
_ATTR_MP_UNREACH = 15
_ATTR_EXT_COMMUNITIES = 16
_ATTR_AS4_PATH = 17
_ATTR_AS4_AGGREGATOR = 18
_ATTR_LARGE_COMMUNITIES = 32
_ATTR_ONLY_TO_CUSTOMER = 35
_KNOWN_ATTRIBUTES = frozenset(
    (
        _ATTR_ORIGIN,
        _ATTR_AS_PATH,
        _ATTR_NEXT_HOP,
        _ATTR_MED,
        _ATTR_LOCAL_PREF,
        _ATTR_ATOMIC_AGGREGATE,
        _ATTR_AGGREGATOR,
        _ATTR_COMMUNITIES,
        _ATTR_ORIGINATOR_ID,
        _ATTR_CLUSTER_LIST,
        _ATTR_MP_REACH,
        _ATTR_MP_UNREACH,
        _ATTR_EXT_COMMUNITIES,
        _ATTR_AS4_PATH,
        _ATTR_AS4_AGGREGATOR,
        _ATTR_LARGE_COMMUNITIES,
        _ATTR_ONLY_TO_CUSTOMER,
    )
)

# extended-length bit(0x10) is checked separately. The remaining high bits are
# optional/transitive/partial. Partial is legal only for optional transitive.
_ATTRIBUTE_BASE_FLAGS = {
    _ATTR_ORIGIN: frozenset((0x40,)),
    _ATTR_AS_PATH: frozenset((0x40,)),
    _ATTR_NEXT_HOP: frozenset((0x40,)),
    _ATTR_MED: frozenset((0x80,)),
    _ATTR_LOCAL_PREF: frozenset((0x40,)),
    _ATTR_ATOMIC_AGGREGATE: frozenset((0x40,)),
    _ATTR_AGGREGATOR: frozenset((0xC0, 0xE0)),
    _ATTR_COMMUNITIES: frozenset((0xC0, 0xE0)),
    _ATTR_ORIGINATOR_ID: frozenset((0x80,)),
    _ATTR_CLUSTER_LIST: frozenset((0x80,)),
    _ATTR_MP_REACH: frozenset((0x80,)),
    _ATTR_MP_UNREACH: frozenset((0x80,)),
    _ATTR_EXT_COMMUNITIES: frozenset((0xC0, 0xE0)),
    _ATTR_AS4_PATH: frozenset((0xC0, 0xE0)),
    _ATTR_AS4_AGGREGATOR: frozenset((0xC0, 0xE0)),
    _ATTR_LARGE_COMMUNITIES: frozenset((0xC0, 0xE0)),
    _ATTR_ONLY_TO_CUSTOMER: frozenset((0xC0, 0xE0)),
}

_FSM_STATES = frozenset(range(1, 7))
_FAMILY_ORDER = ("ipv4_unicast", "ipv6_unicast")


class NativeUpdateParserError(RouteEventInputError):
    """原生 UPDATE 输入不能安全提升为 ``ParsedMrtRecord``。"""


class NativeUpdateConfigurationError(NativeUpdateParserError):
    """工厂配置、manifest 或文件路径不符合冻结边界。"""


class NativeUpdateIntegrityError(NativeUpdateParserError):
    """gzip、MRT/BGP framing、属性或文件哈希不符合合同。"""


@dataclass(frozen=True)
class _ArtifactSpec:
    artifact: Dict[str, Any]
    relative_path: PurePosixPath
    slot_start_epoch_us: int
    slot_end_epoch_us: int


@dataclass(frozen=True)
class _ParsedAttributes:
    as_path: Optional[Tuple[AsPathSegment, ...]]
    origin_present: bool
    next_hop_present: bool
    mp_announces: Dict[str, Tuple[str, ...]]
    mp_withdraws: Dict[str, Tuple[str, ...]]


@dataclass(frozen=True)
class _DecodedRecord:
    kind: str
    elements: Tuple[ParsedRouteElement, ...]
    state_transition: Optional[Tuple[int, int]] = None


class _HashingReader:
    """压缩流首次读取时同步统计 SHA256/字节数。"""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._digest = hashlib.sha256()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        block = self._stream.read(size)
        if block:
            self._digest.update(block)
            self.bytes_read += len(block)
        return block

    def tell(self) -> int:
        return self.bytes_read

    @property
    def hexdigest(self) -> str:
        return self._digest.hexdigest()


def _is_end_of_rib_update_body(body: bytes) -> bool:
    """仅接受 RFC 4724 的空 IPv4 UPDATE 或单一 MP_UNREACH EOR。"""

    if body == b"\x00\x00\x00\x00":
        return True
    if len(body) < 4 or body[:2] != b"\x00\x00":
        return False
    attribute_bytes = int.from_bytes(body[2:4], "big")
    if len(body) != 4 + attribute_bytes or attribute_bytes == 0:
        return False
    attributes = body[4:]
    if len(attributes) < 3:
        return False
    flags, attribute_type = attributes[0], attributes[1]
    if attribute_type != _ATTR_MP_UNREACH or flags not in {0x80, 0x90}:
        return False
    if flags & 0x10:
        if len(attributes) < 4:
            return False
        length = int.from_bytes(attributes[2:4], "big")
        value = attributes[4:]
    else:
        length = attributes[2]
        value = attributes[3:]
    if length != 3 or len(value) != 3:
        return False
    afi = int.from_bytes(value[:2], "big")
    safi = value[2]
    return afi in _ADDRESS_WIDTH_BY_AFI and safi == 1


def _read_exact(
    stream: BinaryIO, length: int, *, allow_clean_eof: bool
) -> Optional[bytes]:
    chunks = bytearray()
    while len(chunks) < length:
        block = stream.read(length - len(chunks))
        if not block:
            if not chunks and allow_clean_eof:
                return None
            raise NativeUpdateIntegrityError(
                f"解压 MRT 流被截断：expected={length}, actual={len(chunks)}"
            )
        chunks.extend(block)
    return bytes(chunks)


def _hash_regular_file(path: Path) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise NativeUpdateConfigurationError("待哈希路径必须是普通文件")
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(descriptor)
        immutable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in immutable):
            raise NativeUpdateConfigurationError("哈希期间普通文件发生变化")
        return digest.hexdigest()
    except OSError as error:
        raise NativeUpdateConfigurationError("普通文件不可只读哈希") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _strict_utc_epoch_us(value: object, field: str) -> int:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise NativeUpdateConfigurationError(f"{field} 必须是 Z 结尾 UTC 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise NativeUpdateConfigurationError(f"{field} 不是有效 UTC 时间") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise NativeUpdateConfigurationError(f"{field} 必须是 UTC 时间")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = parsed - epoch
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _event_time(timestamp: int, microseconds: int) -> Tuple[str, int]:
    epoch_us = timestamp * 1_000_000 + microseconds
    moment = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if microseconds:
        text = moment.strftime("%Y-%m-%dT%H:%M:%S") + f".{microseconds:06d}Z"
    else:
        text = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    return text, epoch_us


def _assert_no_symlink_path(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as error:
            raise NativeUpdateConfigurationError("manifest 原始制品路径不可读") from error
        if stat.S_ISLNK(mode):
            raise NativeUpdateConfigurationError("manifest 原始制品路径禁止符号链接")
    return current


def _decode_prefixes(payload: bytes, afi: int, field: str) -> Tuple[str, ...]:
    maximum_bits = 32 if afi == 1 else 128 if afi == 2 else None
    if maximum_bits is None:
        raise NativeUpdateIntegrityError(f"{field} AFI 只允许 IPv4/IPv6")
    address_bytes = maximum_bits // 8
    offset = 0
    prefixes = []
    while offset < len(payload):
        prefix_length = payload[offset]
        offset += 1
        if prefix_length > maximum_bits:
            raise NativeUpdateIntegrityError(f"{field} prefix length 越界")
        octets = (prefix_length + 7) // 8
        if offset + octets > len(payload):
            raise NativeUpdateIntegrityError(f"{field} NLRI 被截断")
        packed = payload[offset : offset + octets]
        offset += octets
        if prefix_length % 8 and packed:
            unused_mask = (1 << (8 - prefix_length % 8)) - 1
            if packed[-1] & unused_mask:
                raise NativeUpdateIntegrityError(f"{field} NLRI host bits 非零")
        padded = packed + b"\x00" * (address_bytes - len(packed))
        family = socket.AF_INET if afi == 1 else socket.AF_INET6
        address = socket.inet_ntop(family, padded)
        prefixes.append(f"{address}/{prefix_length}")
    return tuple(prefixes)


def _parse_as_path(
    payload: bytes,
    *,
    asn_width: int,
    field: str,
    allowed_types: frozenset[int] = frozenset(_AS_SEGMENT_NAMES),
) -> Tuple[AsPathSegment, ...]:
    if asn_width not in {2, 4}:
        raise NativeUpdateIntegrityError(f"{field} ASN width 非法")
    offset = 0
    segments = []
    total_asns = 0
    while offset < len(payload):
        if len(payload) - offset < 2:
            raise NativeUpdateIntegrityError(f"{field} segment header 被截断")
        segment_type = payload[offset]
        count = payload[offset + 1]
        offset += 2
        if segment_type not in allowed_types:
            raise NativeUpdateIntegrityError(f"{field} segment type 未获准")
        if count == 0:
            raise NativeUpdateIntegrityError(f"{field} segment 不得为空")
        byte_count = count * asn_width
        if offset + byte_count > len(payload):
            raise NativeUpdateIntegrityError(f"{field} ASN 列表被截断")
        asns = tuple(
            int.from_bytes(payload[index : index + asn_width], "big")
            for index in range(offset, offset + byte_count, asn_width)
        )
        offset += byte_count
        total_asns += len(asns)
        if total_asns > 4096 or len(segments) >= 4096:
            raise NativeUpdateIntegrityError(f"{field} 超过 RouteEvent 合同上限")
        segments.append(AsPathSegment(_AS_SEGMENT_NAMES[segment_type], asns))
    return _collapse_as_sequences(tuple(segments))


def _collapse_as_sequences(
    path: Tuple[AsPathSegment, ...],
) -> Tuple[AsPathSegment, ...]:
    """匹配 bgpdump 文本 oracle：相邻 AS_SEQUENCE 的原始边界不可见。"""

    collapsed = []
    for segment in path:
        if (
            collapsed
            and segment.segment_type == "as_sequence"
            and collapsed[-1].segment_type == "as_sequence"
        ):
            combined = collapsed[-1].asns + segment.asns
            if len(combined) > 4096:
                raise NativeUpdateIntegrityError("合并 AS_SEQUENCE 后超过合同上限")
            collapsed[-1] = AsPathSegment("as_sequence", combined)
        else:
            collapsed.append(segment)
    return tuple(collapsed)


def _as_path_count(path: Tuple[AsPathSegment, ...]) -> int:
    return sum(
        1 if segment.segment_type in {"as_set", "confederation_set"} else len(segment.asns)
        for segment in path
    )


def _merge_as4_path(
    old_path: Tuple[AsPathSegment, ...],
    as4_path: Tuple[AsPathSegment, ...],
) -> Tuple[AsPathSegment, ...]:
    """复现 bgpdump 1.6.2/RFC 6793 的 AS_PATH + AS4_PATH merge。"""

    old_count = _as_path_count(old_path)
    new_count = _as_path_count(as4_path)
    if old_count < new_count:
        return old_path
    keep_count = old_count - new_count
    kept = []
    consumed = 0
    for segment in old_path:
        if consumed >= keep_count:
            break
        contribution = (
            1
            if segment.segment_type in {"as_set", "confederation_set"}
            else len(segment.asns)
        )
        remaining = keep_count - consumed
        if contribution <= remaining:
            kept.append(segment)
            consumed += contribution
            continue
        if segment.segment_type in {"as_set", "confederation_set"}:
            raise NativeUpdateIntegrityError("AS4_PATH merge 在 AS_SET 内产生歧义")
        kept.append(AsPathSegment(segment.segment_type, segment.asns[:remaining]))
        consumed += remaining
    if consumed != keep_count:
        raise NativeUpdateIntegrityError("AS4_PATH merge 计数不闭合")
    merged = _collapse_as_sequences(tuple(kept) + as4_path)
    if len(merged) > 4096:
        raise NativeUpdateIntegrityError("AS4_PATH merge 后 segment 超过合同上限")
    return merged


def _validate_mp_next_hop(afi: int, payload: bytes) -> None:
    allowed = {1: {4}, 2: {16, 32}}.get(afi)
    if allowed is None or len(payload) not in allowed:
        raise NativeUpdateIntegrityError("MP_REACH next-hop 长度与 AFI 不一致")
    width = 4 if afi == 1 else 16
    for offset in range(0, len(payload), width):
        ipaddress.ip_address(payload[offset : offset + width])


def _parse_mp_reach(value: bytes) -> Tuple[str, Tuple[str, ...]]:
    if len(value) < 5:
        raise NativeUpdateIntegrityError("MP_REACH_NLRI 属性过短")
    afi = struct.unpack("!H", value[:2])[0]
    safi = value[2]
    if afi not in {1, 2} or safi != 1:
        raise NativeUpdateIntegrityError("MP_REACH 只允许 IPv4/IPv6 unicast")
    next_hop_length = value[3]
    reserved_offset = 4 + next_hop_length
    if reserved_offset >= len(value):
        raise NativeUpdateIntegrityError("MP_REACH next-hop/reserved 越界")
    _validate_mp_next_hop(afi, value[4:reserved_offset])
    if value[reserved_offset] != 0:
        raise NativeUpdateIntegrityError("MP_REACH reserved 字节非零")
    family = "ipv4_unicast" if afi == 1 else "ipv6_unicast"
    return family, _decode_prefixes(value[reserved_offset + 1 :], afi, "MP_REACH")


def _parse_mp_unreach(value: bytes) -> Tuple[str, Tuple[str, ...]]:
    if len(value) < 3:
        raise NativeUpdateIntegrityError("MP_UNREACH_NLRI 属性过短")
    afi = struct.unpack("!H", value[:2])[0]
    safi = value[2]
    if afi not in {1, 2} or safi != 1:
        raise NativeUpdateIntegrityError("MP_UNREACH 只允许 IPv4/IPv6 unicast")
    family = "ipv4_unicast" if afi == 1 else "ipv6_unicast"
    return family, _decode_prefixes(value[3:], afi, "MP_UNREACH")


def _validate_attribute_value(attribute_type: int, value: bytes, asn_width: int) -> None:
    exact = {
        _ATTR_ORIGIN: 1,
        _ATTR_NEXT_HOP: 4,
        _ATTR_MED: 4,
        _ATTR_LOCAL_PREF: 4,
        _ATTR_ATOMIC_AGGREGATE: 0,
        _ATTR_AGGREGATOR: asn_width + 4,
        _ATTR_ORIGINATOR_ID: 4,
        _ATTR_AS4_AGGREGATOR: 8,
        _ATTR_ONLY_TO_CUSTOMER: 4,
    }
    if attribute_type in exact and len(value) != exact[attribute_type]:
        raise NativeUpdateIntegrityError("BGP path attribute 长度不符合类型")
    if attribute_type == _ATTR_ORIGIN and value[0] not in {0, 1, 2}:
        raise NativeUpdateIntegrityError("ORIGIN 值非法")
    multiples = {
        _ATTR_COMMUNITIES: 4,
        _ATTR_CLUSTER_LIST: 4,
        _ATTR_EXT_COMMUNITIES: 8,
        _ATTR_LARGE_COMMUNITIES: 12,
    }
    multiple = multiples.get(attribute_type)
    if multiple is not None and len(value) % multiple:
        raise NativeUpdateIntegrityError("BGP path attribute 数组长度不闭合")


def _parse_attributes(payload: bytes, *, asn_width: int) -> _ParsedAttributes:
    offset = 0
    seen = set()
    as_path: Optional[Tuple[AsPathSegment, ...]] = None
    as4_path: Optional[Tuple[AsPathSegment, ...]] = None
    origin_present = False
    next_hop_present = False
    mp_announces: Dict[str, Tuple[str, ...]] = {}
    mp_withdraws: Dict[str, Tuple[str, ...]] = {}
    while offset < len(payload):
        if len(payload) - offset < 3:
            raise NativeUpdateIntegrityError("BGP path attribute header 被截断")
        flags = payload[offset]
        attribute_type = payload[offset + 1]
        offset += 2
        if flags & 0x0F:
            raise NativeUpdateIntegrityError("BGP path attribute reserved flags 非零")
        if attribute_type not in _KNOWN_ATTRIBUTES:
            raise NativeUpdateIntegrityError(
                f"BGP path attribute type={attribute_type} 未在冻结 allowlist"
            )
        if attribute_type in seen:
            raise NativeUpdateIntegrityError("BGP path attribute 重复")
        seen.add(attribute_type)
        if (flags & 0xE0) not in _ATTRIBUTE_BASE_FLAGS[attribute_type]:
            raise NativeUpdateIntegrityError("BGP path attribute flags 与类型不一致")
        if flags & 0x10:
            if len(payload) - offset < 2:
                raise NativeUpdateIntegrityError("extended attribute length 被截断")
            length = struct.unpack("!H", payload[offset : offset + 2])[0]
            offset += 2
        else:
            length = payload[offset]
            offset += 1
        if offset + length > len(payload):
            raise NativeUpdateIntegrityError("BGP path attribute value 越界")
        value = payload[offset : offset + length]
        offset += length
        _validate_attribute_value(attribute_type, value, asn_width)

        if attribute_type == _ATTR_ORIGIN:
            origin_present = True
        elif attribute_type == _ATTR_AS_PATH:
            as_path = _parse_as_path(value, asn_width=asn_width, field="AS_PATH")
        elif attribute_type == _ATTR_NEXT_HOP:
            ipaddress.ip_address(value)
            next_hop_present = True
        elif attribute_type == _ATTR_AS4_PATH:
            as4_path = _parse_as_path(
                value,
                asn_width=4,
                field="AS4_PATH",
                allowed_types=_AS4_ALLOWED_SEGMENTS,
            )
        elif attribute_type == _ATTR_MP_REACH:
            family, prefixes = _parse_mp_reach(value)
            mp_announces[family] = prefixes
        elif attribute_type == _ATTR_MP_UNREACH:
            family, prefixes = _parse_mp_unreach(value)
            mp_withdraws[family] = prefixes

    if as4_path is not None:
        if asn_width == 4:
            raise NativeUpdateIntegrityError("AS4 message 不得携带歧义 AS4_PATH")
        if as_path is None:
            raise NativeUpdateIntegrityError("AS4_PATH 缺少可合并 AS_PATH")
        as_path = _merge_as4_path(as_path, as4_path)
    return _ParsedAttributes(
        as_path=as_path,
        origin_present=origin_present,
        next_hop_present=next_hop_present,
        mp_announces=mp_announces,
        mp_withdraws=mp_withdraws,
    )


def _validate_open(body: bytes) -> None:
    if len(body) < 10 or body[0] != 4:
        raise NativeUpdateIntegrityError("BGP OPEN version/body 非法")
    optional_length = body[9]
    if len(body) != 10 + optional_length:
        raise NativeUpdateIntegrityError("BGP OPEN optional parameters 不闭合")
    offset = 10
    while offset < len(body):
        if len(body) - offset < 2:
            raise NativeUpdateIntegrityError("OPEN optional parameter header 截断")
        parameter_type = body[offset]
        length = body[offset + 1]
        offset += 2
        end = offset + length
        if end > len(body):
            raise NativeUpdateIntegrityError("OPEN optional parameter value 截断")
        if parameter_type == 2:
            cursor = offset
            while cursor < end:
                if end - cursor < 2:
                    raise NativeUpdateIntegrityError("OPEN capability header 截断")
                capability_length = body[cursor + 1]
                cursor += 2
                if cursor + capability_length > end:
                    raise NativeUpdateIntegrityError("OPEN capability value 截断")
                cursor += capability_length
        offset = end


def _identity(
    payload: bytes, *, asn_width: int
) -> Tuple[int, str, int, int]:
    fixed = asn_width * 2 + 4
    if len(payload) < fixed:
        raise NativeUpdateIntegrityError("BGP4MP peer identity 被截断")
    peer_asn = int.from_bytes(payload[:asn_width], "big")
    interface_index, afi = struct.unpack(
        "!HH", payload[asn_width * 2 : fixed]
    )
    address_width = _ADDRESS_WIDTH_BY_AFI.get(afi)
    if address_width is None:
        raise NativeUpdateIntegrityError("BGP4MP AFI 只允许 IPv4/IPv6")
    address_end = fixed + address_width * 2
    if len(payload) < address_end:
        raise NativeUpdateIntegrityError("BGP4MP peer/local address 被截断")
    family = socket.AF_INET if afi == 1 else socket.AF_INET6
    peer_ip = socket.inet_ntop(family, payload[fixed : fixed + address_width])
    return peer_asn, peer_ip, interface_index, address_end


def _decode_record(
    raw_record: bytes,
    *,
    window_start_epoch_us: int,
    window_end_epoch_us: int,
    slot_start_epoch_us: int,
    slot_end_epoch_us: int,
) -> _DecodedRecord:
    if len(raw_record) < _MRT_HEADER_LENGTH:
        raise NativeUpdateIntegrityError("MRT physical record 过短")
    timestamp, mrt_type, mrt_subtype, payload_length = struct.unpack(
        "!IHHI", raw_record[:_MRT_HEADER_LENGTH]
    )
    if len(raw_record) != _MRT_HEADER_LENGTH + payload_length:
        raise NativeUpdateIntegrityError("MRT header length 与 raw bytes 不一致")
    if mrt_type not in _MRT_TYPES or mrt_subtype not in _ALLOWED_SUBTYPES:
        raise NativeUpdateIntegrityError("拒绝未知、LOCAL 或 Add-Path MRT type/subtype")
    payload = raw_record[_MRT_HEADER_LENGTH:]
    microseconds = 0
    if mrt_type == 17:
        if len(payload) < 4:
            raise NativeUpdateIntegrityError("BGP4MP_ET 缺少扩展微秒")
        microseconds = struct.unpack("!I", payload[:4])[0]
        if microseconds > 999_999:
            raise NativeUpdateIntegrityError("BGP4MP_ET 扩展微秒越界")
        payload = payload[4:]
    event_time_utc, event_epoch_us = _event_time(timestamp, microseconds)
    if not window_start_epoch_us <= event_epoch_us < window_end_epoch_us:
        raise NativeUpdateIntegrityError("MRT record 时间越出 data profile")
    if not slot_start_epoch_us <= event_epoch_us < slot_end_epoch_us:
        raise NativeUpdateIntegrityError("MRT record 时间越出五分钟 artifact 槽")
    asn_width = _ASN_WIDTH_BY_SUBTYPE[mrt_subtype]
    peer_asn, peer_ip, _interface, body_offset = _identity(
        payload, asn_width=asn_width
    )

    if mrt_subtype in _STATE_SUBTYPES:
        if len(payload) != body_offset + 4:
            raise NativeUpdateIntegrityError("STATE_CHANGE payload 截断或含尾部")
        old_state, new_state = struct.unpack("!HH", payload[body_offset:])
        if old_state not in _FSM_STATES or new_state not in _FSM_STATES:
            raise NativeUpdateIntegrityError("STATE_CHANGE FSM state 必须为 1..6")
        return _DecodedRecord("state_change", (), (old_state, new_state))

    message = payload[body_offset:]
    if len(message) < 19 or message[:16] != _BGP_MARKER:
        raise NativeUpdateIntegrityError("BGP marker/header 非法")
    message_length = struct.unpack("!H", message[16:18])[0]
    if (
        message_length < 19
        or message_length > _BGP_MAX_MESSAGE_BYTES
        or message_length != len(message)
    ):
        raise NativeUpdateIntegrityError("BGP message length 不闭合")
    message_type = message[18]
    body = message[19:]
    if message_type == _BGP_OPEN:
        _validate_open(body)
        return _DecodedRecord("open", ())
    if message_type == _BGP_NOTIFICATION:
        if len(body) < 2:
            raise NativeUpdateIntegrityError("BGP NOTIFICATION body 被截断")
        return _DecodedRecord("notification", ())
    if message_type == _BGP_KEEPALIVE:
        if body:
            raise NativeUpdateIntegrityError("BGP KEEPALIVE body 必须为空")
        return _DecodedRecord("keepalive", ())
    if message_type != _BGP_UPDATE:
        raise NativeUpdateIntegrityError("BGP message type 未获准")
    if len(body) < 4:
        raise NativeUpdateIntegrityError("BGP UPDATE body 被截断")
    withdrawn_length = struct.unpack("!H", body[:2])[0]
    if 2 + withdrawn_length + 2 > len(body):
        raise NativeUpdateIntegrityError("BGP UPDATE withdraw 区越界")
    standard_withdraws = _decode_prefixes(
        body[2 : 2 + withdrawn_length], 1, "IPv4 withdraw"
    )
    attributes_length_offset = 2 + withdrawn_length
    attributes_length = struct.unpack(
        "!H", body[attributes_length_offset : attributes_length_offset + 2]
    )[0]
    attributes_start = attributes_length_offset + 2
    attributes_end = attributes_start + attributes_length
    if attributes_end > len(body):
        raise NativeUpdateIntegrityError("BGP UPDATE attributes 区越界")
    attributes = _parse_attributes(
        body[attributes_start:attributes_end], asn_width=asn_width
    )
    standard_announces = _decode_prefixes(
        body[attributes_end:], 1, "IPv4 announce"
    )

    withdraws: list[Tuple[str, str]] = [
        (prefix, "ipv4_unicast") for prefix in standard_withdraws
    ]
    for family in _FAMILY_ORDER:
        withdraws.extend(
            (prefix, family) for prefix in attributes.mp_withdraws.get(family, ())
        )
    announces: list[Tuple[str, str]] = [
        (prefix, "ipv4_unicast") for prefix in standard_announces
    ]
    for family in _FAMILY_ORDER:
        announces.extend(
            (prefix, family) for prefix in attributes.mp_announces.get(family, ())
        )
    if not withdraws and not announces:
        if _is_end_of_rib_update_body(body):
            return _DecodedRecord("end_of_rib", ())
        raise NativeUpdateIntegrityError("无 NLRI 的非 EOR UPDATE 被拒绝")
    if announces:
        if not attributes.origin_present:
            raise NativeUpdateIntegrityError("announce UPDATE 缺少 ORIGIN")
        if attributes.as_path is None or not attributes.as_path:
            raise NativeUpdateIntegrityError("announce UPDATE 缺少非空 AS_PATH")
        if standard_announces and not attributes.next_hop_present:
            raise NativeUpdateIntegrityError("标准 IPv4 announce 缺少 NEXT_HOP")

    elements = []
    for prefix, family in withdraws:
        elements.append(
            ParsedRouteElement(
                event_time_utc=event_time_utc,
                peer_ip=peer_ip,
                peer_asn=peer_asn,
                action="withdraw",
                prefix=prefix,
                afi_safi=family,
                as_path=None,
            )
        )
    for prefix, family in announces:
        elements.append(
            ParsedRouteElement(
                event_time_utc=event_time_utc,
                peer_ip=peer_ip,
                peer_asn=peer_asn,
                action="announce",
                prefix=prefix,
                afi_safi=family,
                as_path=attributes.as_path,
            )
        )
    return _DecodedRecord("update", tuple(elements))


class NativeUpdateRecordStream:
    """单个 gzip UPDATE artifact 的一次性原生 ParsedMrtRecord 流。"""

    def __init__(
        self,
        *,
        path: Path,
        spec: _ArtifactSpec,
        window_start_epoch_us: int,
        window_end_epoch_us: int,
        max_frame_bytes: int,
        max_physical_records: int,
        max_route_events: int,
        parser_binary_sha256: str,
    ) -> None:
        self._path = path
        self._spec = spec
        self._window_start_epoch_us = window_start_epoch_us
        self._window_end_epoch_us = window_end_epoch_us
        self._max_frame_bytes = max_frame_bytes
        self._max_physical_records = max_physical_records
        self._max_route_events = max_route_events
        self._parser_binary_sha256 = parser_binary_sha256
        self._started = False
        self._statistics: Dict[str, Any] = {
            "status": "not_started",
            "artifact_id": spec.artifact["artifact_id"],
            "physical_record_count": 0,
            "route_record_count": 0,
            "state_change_record_count": 0,
            "open_record_count": 0,
            "notification_record_count": 0,
            "keepalive_record_count": 0,
            "end_of_rib_record_count": 0,
            "route_element_count": 0,
            "announce_count": 0,
            "withdraw_count": 0,
            "state_change_transitions": [],
            "record_hash_chain_sha256": None,
            "compressed_file_sha256": None,
            "compressed_size_bytes": None,
            "compressed_bytes_read_observed": None,
            "compressed_read_passes": 0,
            "parser_mode": NATIVE_UPDATE_EXECUTION_POLICY,
            "parser_version": NATIVE_UPDATE_PARSER_VERSION,
            "parser_binary_sha256": parser_binary_sha256,
        }

    @property
    def statistics(self) -> Dict[str, Any]:
        return copy.deepcopy(self._statistics)

    def __iter__(self) -> Iterable[ParsedMrtRecord]:
        if self._started:
            raise NativeUpdateParserError("同一 NativeUpdateRecordStream 只能消费一次")
        self._started = True
        return self._iterate()

    def _iterate(self) -> Iterable[ParsedMrtRecord]:
        descriptor: Optional[int] = None
        hashing: Optional[_HashingReader] = None
        hash_chain = hashlib.sha256()
        transitions: Dict[Tuple[int, int], int] = {}
        expected_offset = 0
        route_events = 0
        self._statistics["status"] = "running"
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self._path, flags)
            before = os.fstat(descriptor)
            expected_size = self._spec.artifact["size_bytes"]
            if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
                raise NativeUpdateIntegrityError("原始 MRT 文件类型/大小与 manifest 不一致")
            with os.fdopen(descriptor, "rb", buffering=0) as compressed:
                descriptor = None
                hashing = _HashingReader(compressed)
                try:
                    with gzip.GzipFile(fileobj=hashing, mode="rb") as decoded:
                        ordinal = 0
                        while True:
                            header = _read_exact(
                                decoded, _MRT_HEADER_LENGTH, allow_clean_eof=True
                            )
                            if header is None:
                                break
                            payload_length = struct.unpack("!I", header[8:12])[0]
                            total_length = _MRT_HEADER_LENGTH + payload_length
                            if total_length > self._max_frame_bytes:
                                raise NativeUpdateIntegrityError(
                                    "MRT physical record 超过 max_frame_bytes"
                                )
                            if ordinal >= self._max_physical_records:
                                raise NativeUpdateIntegrityError(
                                    "UPDATE 超过 physical record 硬上限"
                                )
                            payload = _read_exact(
                                decoded, payload_length, allow_clean_eof=False
                            )
                            assert payload is not None
                            raw_record = header + payload
                            decoded_record = _decode_record(
                                raw_record,
                                window_start_epoch_us=self._window_start_epoch_us,
                                window_end_epoch_us=self._window_end_epoch_us,
                                slot_start_epoch_us=self._spec.slot_start_epoch_us,
                                slot_end_epoch_us=self._spec.slot_end_epoch_us,
                            )
                            route_events += len(decoded_record.elements)
                            if route_events > self._max_route_events:
                                raise NativeUpdateIntegrityError(
                                    "UPDATE 超过 route event 硬上限"
                                )
                            record_hash = hashlib.sha256(raw_record).digest()
                            hash_chain.update(
                                struct.pack("!QQQ", ordinal, expected_offset, total_length)
                            )
                            hash_chain.update(record_hash)
                            record = ParsedMrtRecord(
                                record_ordinal=ordinal,
                                record_offset=expected_offset,
                                raw_record=raw_record,
                                elements=decoded_record.elements,
                            )
                            self._statistics["physical_record_count"] += 1
                            if decoded_record.kind == "update":
                                self._statistics["route_record_count"] += 1
                                self._statistics["route_element_count"] += len(
                                    decoded_record.elements
                                )
                                self._statistics["announce_count"] += sum(
                                    element.action == "announce"
                                    for element in decoded_record.elements
                                )
                                self._statistics["withdraw_count"] += sum(
                                    element.action == "withdraw"
                                    for element in decoded_record.elements
                                )
                            elif decoded_record.kind == "state_change":
                                self._statistics["state_change_record_count"] += 1
                                assert decoded_record.state_transition is not None
                                transition = decoded_record.state_transition
                                transitions[transition] = transitions.get(transition, 0) + 1
                            else:
                                self._statistics[f"{decoded_record.kind}_record_count"] += 1
                            yield record
                            ordinal += 1
                            expected_offset += total_length
                except (gzip.BadGzipFile, EOFError, zlib.error) as error:
                    raise NativeUpdateIntegrityError("gzip 解压失败或被截断") from error

                while hashing.read(1024 * 1024):
                    pass
                after = os.fstat(compressed.fileno())
                immutable = (
                    "st_dev",
                    "st_ino",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
                if any(getattr(before, field) != getattr(after, field) for field in immutable):
                    raise NativeUpdateIntegrityError("解析期间原始 MRT 文件发生变化")
                if hashing.bytes_read != expected_size:
                    raise NativeUpdateIntegrityError("压缩字节读取量与 manifest 不一致")
                if hashing.hexdigest != self._spec.artifact["file_sha256"]:
                    raise NativeUpdateIntegrityError("压缩文件 SHA256 与 manifest 不一致")
                if self._statistics["physical_record_count"] == 0:
                    raise NativeUpdateIntegrityError("UPDATE artifact 没有 physical record")
                classified = sum(
                    self._statistics[field]
                    for field in (
                        "route_record_count",
                        "state_change_record_count",
                        "open_record_count",
                        "notification_record_count",
                        "keepalive_record_count",
                        "end_of_rib_record_count",
                    )
                )
                if classified != self._statistics["physical_record_count"]:
                    raise NativeUpdateIntegrityError("physical record 分类计数不闭合")
                self._statistics["compressed_file_sha256"] = hashing.hexdigest
                self._statistics["compressed_size_bytes"] = hashing.bytes_read
                self._statistics["compressed_bytes_read_observed"] = hashing.bytes_read
                self._statistics["compressed_read_passes"] = 1
                self._statistics["record_hash_chain_sha256"] = hash_chain.hexdigest()
                self._statistics["state_change_transitions"] = [
                    {"old_state": old, "new_state": new, "count": count}
                    for (old, new), count in sorted(transitions.items())
                ]
                self._statistics["status"] = "complete"
        except GeneratorExit:
            raise
        except BaseException as error:
            self._statistics["status"] = "failed"
            self._statistics["failure_type"] = type(error).__name__
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(error, NativeUpdateParserError):
                raise
            raise NativeUpdateParserError(
                f"原生 UPDATE 适配失败：{type(error).__name__}: {error}"
            ) from error
        finally:
            if (
                hashing is not None
                and self._statistics["compressed_bytes_read_observed"] is None
            ):
                self._statistics["compressed_bytes_read_observed"] = (
                    hashing.bytes_read
                )
            if descriptor is not None:
                os.close(descriptor)


class NativeUpdateRecordStreamFactory:
    """与 ``BgpdumpRecordStreamFactory`` callable 接口兼容的原生工厂。"""

    def __init__(
        self,
        raw_root: os.PathLike[str] | str,
        artifacts: Sequence[Mapping[str, Any]],
        *,
        data_profile: Mapping[str, Any],
        pilot_limits: Mapping[str, Any],
        max_frame_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self._raw_root = Path(raw_root)
        try:
            root_mode = self._raw_root.lstat().st_mode
        except OSError as error:
            raise NativeUpdateConfigurationError("raw_root 不可读") from error
        if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
            raise NativeUpdateConfigurationError("raw_root 必须是非符号链接目录")
        if not isinstance(data_profile, Mapping):
            raise NativeUpdateConfigurationError("data_profile 必须是对象")
        window_start = _strict_utc_epoch_us(
            data_profile.get("window_start_utc"), "data_profile.window_start_utc"
        )
        window_end = _strict_utc_epoch_us(
            data_profile.get("window_end_exclusive_utc"),
            "data_profile.window_end_exclusive_utc",
        )
        if window_start >= window_end:
            raise NativeUpdateConfigurationError("data_profile UTC 窗口非法")
        if (
            isinstance(max_frame_bytes, bool)
            or not isinstance(max_frame_bytes, int)
            or max_frame_bytes < _MRT_HEADER_LENGTH
            or max_frame_bytes > 64 * 1024 * 1024
        ):
            raise NativeUpdateConfigurationError("max_frame_bytes 非法")
        if not isinstance(pilot_limits, Mapping):
            raise NativeUpdateConfigurationError("pilot_limits 必须是对象")
        maxima = {
            "max_artifact_count": PILOT_ABSOLUTE_MAX_ARTIFACTS,
            "max_compressed_bytes": PILOT_ABSOLUTE_MAX_COMPRESSED_BYTES,
            "max_physical_records": PILOT_ABSOLUTE_MAX_PHYSICAL_RECORDS,
            "max_route_events": PILOT_ABSOLUTE_MAX_ROUTE_EVENTS,
            "max_spool_bytes": PILOT_ABSOLUTE_MAX_SPOOL_BYTES,
        }
        limits: Dict[str, int] = {}
        for name, maximum in maxima.items():
            value = pilot_limits.get(name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
                or value > maximum
            ):
                raise NativeUpdateConfigurationError(f"pilot_limits.{name} 非法")
            limits[name] = value
        if isinstance(artifacts, (str, bytes)) or not isinstance(artifacts, Sequence):
            raise NativeUpdateConfigurationError("artifacts 必须是显式序列")
        if not artifacts or len(artifacts) > limits["max_artifact_count"]:
            raise NativeUpdateConfigurationError("artifacts 为空或超过硬上限")
        specs: Dict[str, _ArtifactSpec] = {}
        total_bytes = 0
        for row in artifacts:
            if not isinstance(row, Mapping):
                raise NativeUpdateConfigurationError("artifact 必须是对象")
            artifact = dict(row)
            file_hash = artifact.get("file_sha256")
            artifact_id = artifact.get("artifact_id")
            if (
                not isinstance(file_hash, str)
                or _SHA256_RE.fullmatch(file_hash) is None
                or artifact_id != artifact_id_v1(file_hash)
            ):
                raise NativeUpdateConfigurationError("artifact ID/SHA256 不一致")
            if artifact_id in specs:
                raise NativeUpdateConfigurationError("artifact_id 重复")
            collector = artifact.get("collector_id")
            if not isinstance(collector, str) or _COLLECTOR_RE.fullmatch(collector) is None:
                raise NativeUpdateConfigurationError("collector_id 非法")
            if artifact.get("artifact_type") != "update" or artifact.get("compression") != "gz":
                raise NativeUpdateConfigurationError("原生工厂只接受 gzip UPDATE")
            size_bytes = artifact.get("size_bytes")
            if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes <= 0:
                raise NativeUpdateConfigurationError("artifact size_bytes 非法")
            slot_start = _strict_utc_epoch_us(
                artifact.get("artifact_time_utc"), "artifact.artifact_time_utc"
            )
            if slot_start % 300_000_000:
                raise NativeUpdateConfigurationError("UPDATE artifact 时间未按五分钟对齐")
            relative_value = artifact.get("relative_path")
            if not isinstance(relative_value, str):
                raise NativeUpdateConfigurationError("artifact relative_path 非法")
            relative = PurePosixPath(relative_value)
            if (
                relative.is_absolute()
                or not relative.parts
                or ".." in relative.parts
                or relative.parts[0] != collector
            ):
                raise NativeUpdateConfigurationError("artifact relative_path 越出 collector")
            specs[artifact_id] = _ArtifactSpec(
                artifact=artifact,
                relative_path=relative,
                slot_start_epoch_us=slot_start,
                slot_end_epoch_us=slot_start + 300_000_000,
            )
            total_bytes += size_bytes
        if total_bytes > limits["max_compressed_bytes"]:
            raise NativeUpdateConfigurationError("selection 压缩字节超过硬上限")

        module_source = Path(__file__)
        runtime_binary = Path(sys.executable).resolve()
        source_sha256 = _hash_regular_file(module_source)
        runtime_sha256 = _hash_regular_file(runtime_binary)
        config: Dict[str, Any] = {
            "command_arguments": [NATIVE_UPDATE_COMMAND_TOKEN],
            "binary_execution_policy": NATIVE_UPDATE_EXECUTION_POLICY,
            "module_source_sha256": source_sha256,
            "python_runtime_sha256": runtime_sha256,
            "max_frame_bytes": max_frame_bytes,
            "max_spool_bytes": limits["max_spool_bytes"],
            "spool_mode": "not_used_in_process",
            "window_start_utc": data_profile.get("window_start_utc"),
            "window_end_exclusive_utc": data_profile.get(
                "window_end_exclusive_utc"
            ),
            "pilot_limits": copy.deepcopy(limits),
            "allowed_mrt_types": [16, 17],
            "allowed_mrt_subtypes": [0, 1, 4, 5],
            "allowed_afi_safi": ["ipv4_unicast", "ipv6_unicast"],
            "unknown_attribute_policy": "fail_closed",
            "recognized_control_records": [
                "open",
                "notification",
                "keepalive",
                "state_change",
                "end_of_rib",
            ],
        }
        attestation: Dict[str, Any] = {
            "schema_version": "parser_attestation_v1",
            "parser_name": NATIVE_UPDATE_PARSER_NAME,
            "parser_version": NATIVE_UPDATE_PARSER_VERSION,
            "parser_binary_sha256": runtime_sha256,
            "adapter_name": "domeye_native_update_adapter",
            "adapter_version": NATIVE_UPDATE_PARSER_VERSION,
            "adapter_source_sha256": source_sha256,
            "binary_execution_policy": NATIVE_UPDATE_EXECUTION_POLICY,
            "configuration": copy.deepcopy(config),
            "configuration_sha256": hashlib.sha256(
                canonical_json(config).encode("utf-8")
            ).hexdigest(),
            "pilot_limits": copy.deepcopy(limits),
            "security_boundary": (
                "原生解析器在已绑定 source SHA/Python runtime SHA 的当前进程执行；"
                "raw gzip 仅以 O_NOFOLLOW 只读打开，单次压缩读取并核验 size/SHA/"
                "inode 稳定性；未知属性、LOCAL 与 Add-Path 全部失败关闭"
            ),
        }
        attestation["attestation_fingerprint_sha256"] = hashlib.sha256(
            canonical_json(
                {
                    "schema": _PARSER_ATTESTATION_FINGERPRINT_SCHEMA,
                    "attestation": attestation,
                }
            ).encode("utf-8")
        ).hexdigest()
        self._specs = specs
        self._window_start_epoch_us = window_start
        self._window_end_epoch_us = window_end
        self._max_frame_bytes = max_frame_bytes
        self._limits = limits
        self._source_sha256 = source_sha256
        self._parser_attestation = attestation
        self._streams: Dict[str, NativeUpdateRecordStream] = {}

    @property
    def parser_attestation(self) -> Dict[str, Any]:
        return copy.deepcopy(self._parser_attestation)

    @property
    def statistics_by_artifact(self) -> Dict[str, Dict[str, Any]]:
        return {
            artifact_id: stream.statistics
            for artifact_id, stream in sorted(self._streams.items())
        }

    def __call__(self, artifact: Mapping[str, Any]) -> NativeUpdateRecordStream:
        if not isinstance(artifact, Mapping):
            raise NativeUpdateConfigurationError("artifact 必须是对象")
        artifact_id = artifact.get("artifact_id")
        spec = self._specs.get(artifact_id)
        if spec is None:
            raise NativeUpdateConfigurationError("artifact 不在工厂 manifest")
        for field in (
            "file_sha256",
            "collector_id",
            "artifact_type",
            "artifact_time_utc",
            "relative_path",
            "compression",
            "size_bytes",
        ):
            if artifact.get(field) != spec.artifact.get(field):
                raise NativeUpdateConfigurationError(f"artifact.{field} 与 manifest 冲突")
        if artifact_id in self._streams:
            raise NativeUpdateConfigurationError("同一 artifact 不能重复建立 stream")
        if _hash_regular_file(Path(__file__)) != self._source_sha256:
            raise NativeUpdateConfigurationError("native parser source 在工厂创建后变化")
        path = _assert_no_symlink_path(self._raw_root, spec.relative_path)
        stream = NativeUpdateRecordStream(
            path=path,
            spec=spec,
            window_start_epoch_us=self._window_start_epoch_us,
            window_end_epoch_us=self._window_end_epoch_us,
            max_frame_bytes=self._max_frame_bytes,
            max_physical_records=self._limits["max_physical_records"],
            max_route_events=self._limits["max_route_events"],
            parser_binary_sha256=self._parser_attestation[
                "parser_binary_sha256"
            ],
        )
        self._streams[artifact_id] = stream
        return stream


def make_native_update_record_stream_factory(
    raw_root: os.PathLike[str] | str,
    artifacts: Sequence[Mapping[str, Any]],
    **kwargs: Any,
) -> NativeUpdateRecordStreamFactory:
    return NativeUpdateRecordStreamFactory(raw_root, artifacts, **kwargs)


__all__ = (
    "NATIVE_UPDATE_COMMAND_TOKEN",
    "NATIVE_UPDATE_EXECUTION_POLICY",
    "NATIVE_UPDATE_PARSER_NAME",
    "NATIVE_UPDATE_PARSER_VERSION",
    "NativeUpdateConfigurationError",
    "NativeUpdateIntegrityError",
    "NativeUpdateParserError",
    "NativeUpdateRecordStream",
    "NativeUpdateRecordStreamFactory",
    "make_native_update_record_stream_factory",
)
