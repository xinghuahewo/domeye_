"""RRC25 研究路径的 BGP4MP STATE_CHANGE 解析器。

本模块只解析 RFC 6396 定义的 BGP4MP/BGP4MP_ET STATE_CHANGE physical
record，并保留 peer/local 身份、FSM 前后状态和不可变原始记录坐标。解析结果
仅表示一个采集器所见的单个 peer session 状态变化；它不能单独证明该 VP 的
全部前缀被撤回，更不能直接证明国家级路由中断或其原因。

接口只接受内存 ``bytes``/binary stream 或既有 ``ParsedMrtRecord``，不读取
文件、数据库或真实 MRT。任何未知 type/subtype、AFI、扩展微秒、截断或尾部
字节都会失败关闭。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import io
import ipaddress
import re
import struct
from typing import BinaryIO, Iterator, Tuple, Union

from ...route_event.artifacts import artifact_id_v1, canonical_json
from ...route_event.index import ParsedMrtRecord, vp_id_v1


UTC = timezone.utc
MRT_BGP4MP = 16
MRT_BGP4MP_ET = 17
BGP4MP_STATE_CHANGE = 0
BGP4MP_STATE_CHANGE_AS4 = 5
AFI_IPV4 = 1
AFI_IPV6 = 2
PEER_SESSION_OBSERVATION_ID_SCHEMA = "peer_session_observation_id_v1"
PEER_SESSION_OBSERVATION_SEMANTICS = "single_peer_session_transition"
PEER_SESSION_PREFIX_INFERENCE = "not_permitted"
MAX_MRT_PAYLOAD_BYTES = 64 * 1024 * 1024

_MRT_HEADER_LENGTH = 12
_MRT_TYPES = frozenset((MRT_BGP4MP, MRT_BGP4MP_ET))
_ASN_WIDTH_BY_SUBTYPE = {
    BGP4MP_STATE_CHANGE: 2,
    BGP4MP_STATE_CHANGE_AS4: 4,
}
_ADDRESS_WIDTH_BY_AFI = {AFI_IPV4: 4, AFI_IPV6: 16}
_FSM_STATE_NAMES = {
    1: "idle",
    2: "connect",
    3: "active",
    4: "open_sent",
    5: "open_confirm",
    6: "established",
}
_COLLECTOR_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ID_RE = re.compile(r"^art_v1_[0-9a-f]{32}$")


class PeerSessionParseError(ValueError):
    """STATE_CHANGE 记录不能在冻结语义下安全解析。"""


@dataclass(frozen=True)
class PeerSessionObservation:
    """一个可回查原始 physical record 的 peer session 状态观测。

    ``semantics`` 与 ``prefix_withdrawal_inference`` 是有意冻结的边界：本对象
    不得被解释为该 VP 全前缀撤回，也不得单独作为国家中断的因果结论。
    """

    observation_id: str
    observation_id_schema: str
    collector_id: str
    vp_id: str
    event_time_utc: str
    event_epoch_microseconds: int
    mrt_type: int
    mrt_subtype: int
    microseconds: int
    peer_asn: int
    local_asn: int
    interface_index: int
    afi: int
    peer_ip: str
    local_ip: str
    old_state: int
    old_state_name: str
    new_state: int
    new_state_name: str
    artifact_id: str
    file_sha256: str
    record_ordinal: int
    record_offset: int
    record_length: int
    raw_record_sha256: str
    semantics: str = PEER_SESSION_OBSERVATION_SEMANTICS
    prefix_withdrawal_inference: str = PEER_SESSION_PREFIX_INFERENCE


@dataclass(frozen=True)
class _DecodedStateChange:
    event_time_utc: str
    event_epoch_microseconds: int
    mrt_type: int
    mrt_subtype: int
    microseconds: int
    peer_asn: int
    local_asn: int
    interface_index: int
    afi: int
    peer_ip: str
    local_ip: str
    old_state: int
    new_state: int


MrtSource = Union[bytes, bytearray, memoryview, BinaryIO]


def _require_nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PeerSessionParseError(f"{field} 必须是非负整数")
    return value


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise PeerSessionParseError(f"{field} 必须是 64 位小写十六进制")
    return value


def _normalize_collector(value: object) -> str:
    if not isinstance(value, str) or _COLLECTOR_RE.fullmatch(value) is None:
        raise PeerSessionParseError("collector_id 非法")
    return value


def _event_time(timestamp_seconds: int, microseconds: int) -> Tuple[str, int]:
    epoch_microseconds = timestamp_seconds * 1_000_000 + microseconds
    value = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
        seconds=timestamp_seconds, microseconds=microseconds
    )
    if microseconds:
        text = value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        text = value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return text, epoch_microseconds


def _decode_raw_record(raw_record: bytes) -> _DecodedStateChange:
    if not isinstance(raw_record, bytes):
        raise PeerSessionParseError("raw_record 必须是 bytes")
    if len(raw_record) < _MRT_HEADER_LENGTH:
        raise PeerSessionParseError("MRT physical record 短于 12 字节 common header")

    timestamp, mrt_type, mrt_subtype, payload_length = struct.unpack(
        "!IHHI", raw_record[:_MRT_HEADER_LENGTH]
    )
    if payload_length > MAX_MRT_PAYLOAD_BYTES:
        raise PeerSessionParseError("MRT payload 超过 64 MiB 安全上限")
    if len(raw_record) != _MRT_HEADER_LENGTH + payload_length:
        raise PeerSessionParseError("MRT header length 与 physical record 长度不一致")
    if mrt_type not in _MRT_TYPES:
        raise PeerSessionParseError("只接受 MRT type 16 BGP4MP 或 type 17 BGP4MP_ET")
    if mrt_subtype not in _ASN_WIDTH_BY_SUBTYPE:
        raise PeerSessionParseError("只接受 STATE_CHANGE subtype 0 或 subtype 5")

    payload = raw_record[_MRT_HEADER_LENGTH:]
    microseconds = 0
    if mrt_type == MRT_BGP4MP_ET:
        if len(payload) < 4:
            raise PeerSessionParseError("BGP4MP_ET 缺少四字节扩展微秒")
        microseconds = struct.unpack("!I", payload[:4])[0]
        if microseconds > 999_999:
            raise PeerSessionParseError("BGP4MP_ET 扩展微秒超出 0..999999")
        payload = payload[4:]

    asn_width = _ASN_WIDTH_BY_SUBTYPE[mrt_subtype]
    fixed_prefix_length = asn_width * 2 + 4
    if len(payload) < fixed_prefix_length:
        raise PeerSessionParseError("STATE_CHANGE 固定身份字段被截断")

    cursor = 0
    peer_asn = int.from_bytes(payload[cursor : cursor + asn_width], "big")
    cursor += asn_width
    local_asn = int.from_bytes(payload[cursor : cursor + asn_width], "big")
    cursor += asn_width
    interface_index, afi = struct.unpack("!HH", payload[cursor : cursor + 4])
    cursor += 4
    address_width = _ADDRESS_WIDTH_BY_AFI.get(afi)
    if address_width is None:
        raise PeerSessionParseError("STATE_CHANGE AFI 只允许 1(IPv4) 或 2(IPv6)")

    expected_length = fixed_prefix_length + address_width * 2 + 4
    if len(payload) != expected_length:
        raise PeerSessionParseError("STATE_CHANGE payload 存在截断或未知尾部字节")

    peer_ip_bytes = payload[cursor : cursor + address_width]
    cursor += address_width
    local_ip_bytes = payload[cursor : cursor + address_width]
    cursor += address_width
    old_state, new_state = struct.unpack("!HH", payload[cursor : cursor + 4])
    if old_state not in _FSM_STATE_NAMES or new_state not in _FSM_STATE_NAMES:
        raise PeerSessionParseError("STATE_CHANGE FSM state 必须是 RFC 4271 的 1..6")

    peer_ip = ipaddress.ip_address(peer_ip_bytes).compressed
    local_ip = ipaddress.ip_address(local_ip_bytes).compressed
    event_time_utc, event_epoch_microseconds = _event_time(timestamp, microseconds)
    return _DecodedStateChange(
        event_time_utc=event_time_utc,
        event_epoch_microseconds=event_epoch_microseconds,
        mrt_type=mrt_type,
        mrt_subtype=mrt_subtype,
        microseconds=microseconds,
        peer_asn=peer_asn,
        local_asn=local_asn,
        interface_index=interface_index,
        afi=afi,
        peer_ip=peer_ip,
        local_ip=local_ip,
        old_state=old_state,
        new_state=new_state,
    )


def peer_session_observation_id_v1(file_sha256: str, record_ordinal: int) -> str:
    """由完整文件哈希与 physical record ordinal 生成稳定观测 ID。"""

    file_hash = _require_sha256(file_sha256, "file_sha256")
    ordinal = _require_nonnegative_int(record_ordinal, "record_ordinal")
    identity = {
        "schema": PEER_SESSION_OBSERVATION_ID_SCHEMA,
        "file_sha256": file_hash,
        "record_ordinal": ordinal,
    }
    digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    return "pso_v1_" + digest[:32]


def parse_peer_session_observation(
    record: ParsedMrtRecord,
    *,
    collector_id: str,
    file_sha256: str,
    artifact_id: str,
) -> PeerSessionObservation:
    """把一个完整 ``ParsedMrtRecord`` 绑定为不可变会话观测。"""

    if not isinstance(record, ParsedMrtRecord):
        raise PeerSessionParseError("record 必须是 ParsedMrtRecord")
    ordinal = _require_nonnegative_int(record.record_ordinal, "record_ordinal")
    offset = _require_nonnegative_int(record.record_offset, "record_offset")
    if record.elements:
        raise PeerSessionParseError("STATE_CHANGE ParsedMrtRecord.elements 必须为空")
    collector = _normalize_collector(collector_id)
    file_hash = _require_sha256(file_sha256, "file_sha256")
    if not isinstance(artifact_id, str) or _ARTIFACT_ID_RE.fullmatch(artifact_id) is None:
        raise PeerSessionParseError("artifact_id 必须符合 art_v1 身份格式")
    try:
        expected_artifact_id = artifact_id_v1(file_hash)
    except ValueError as error:  # pragma: no cover - 已由本模块先行校验
        raise PeerSessionParseError("file_sha256 无法生成 artifact_id") from error
    if artifact_id != expected_artifact_id:
        raise PeerSessionParseError("artifact_id 与 file_sha256 不匹配")

    decoded = _decode_raw_record(record.raw_record)
    raw_record_sha256 = hashlib.sha256(record.raw_record).hexdigest()
    return PeerSessionObservation(
        observation_id=peer_session_observation_id_v1(file_hash, ordinal),
        observation_id_schema=PEER_SESSION_OBSERVATION_ID_SCHEMA,
        collector_id=collector,
        vp_id=vp_id_v1(collector, decoded.peer_ip, decoded.peer_asn),
        event_time_utc=decoded.event_time_utc,
        event_epoch_microseconds=decoded.event_epoch_microseconds,
        mrt_type=decoded.mrt_type,
        mrt_subtype=decoded.mrt_subtype,
        microseconds=decoded.microseconds,
        peer_asn=decoded.peer_asn,
        local_asn=decoded.local_asn,
        interface_index=decoded.interface_index,
        afi=decoded.afi,
        peer_ip=decoded.peer_ip,
        local_ip=decoded.local_ip,
        old_state=decoded.old_state,
        old_state_name=_FSM_STATE_NAMES[decoded.old_state],
        new_state=decoded.new_state,
        new_state_name=_FSM_STATE_NAMES[decoded.new_state],
        artifact_id=artifact_id,
        file_sha256=file_hash,
        record_ordinal=ordinal,
        record_offset=offset,
        record_length=len(record.raw_record),
        raw_record_sha256=raw_record_sha256,
    )


def _read_header_or_eof(stream: BinaryIO) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < _MRT_HEADER_LENGTH:
        part = stream.read(_MRT_HEADER_LENGTH - len(chunks))
        if not isinstance(part, bytes):
            raise PeerSessionParseError("binary stream.read() 必须返回 bytes")
        if not part:
            if not chunks:
                return None
            raise PeerSessionParseError("MRT common header 被截断")
        chunks.extend(part)
    return bytes(chunks)


def _read_exact(stream: BinaryIO, length: int, field: str) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        part = stream.read(length - len(chunks))
        if not isinstance(part, bytes):
            raise PeerSessionParseError("binary stream.read() 必须返回 bytes")
        if not part:
            raise PeerSessionParseError(f"{field} 被截断")
        chunks.extend(part)
    return bytes(chunks)


def iter_peer_session_mrt_records(source: MrtSource) -> Iterator[ParsedMrtRecord]:
    """顺序解析仅含 STATE_CHANGE 的解压 MRT 字节流并保留原始坐标。"""

    if isinstance(source, (bytes, bytearray, memoryview)):
        stream: BinaryIO = io.BytesIO(bytes(source))
    elif hasattr(source, "read"):
        stream = source  # type: ignore[assignment]
    else:
        raise PeerSessionParseError("source 必须是 bytes 或 binary stream")

    record_ordinal = 0
    record_offset = 0
    while True:
        header = _read_header_or_eof(stream)
        if header is None:
            if record_ordinal == 0:
                raise PeerSessionParseError("MRT stream 为空")
            return
        payload_length = struct.unpack("!I", header[8:12])[0]
        if payload_length > MAX_MRT_PAYLOAD_BYTES:
            raise PeerSessionParseError("MRT payload 超过 64 MiB 安全上限")
        payload = _read_exact(
            stream, payload_length, f"MRT record[{record_ordinal}].payload"
        )
        raw_record = header + payload
        _decode_raw_record(raw_record)
        yield ParsedMrtRecord(
            record_ordinal=record_ordinal,
            record_offset=record_offset,
            raw_record=raw_record,
            elements=(),
        )
        record_ordinal += 1
        record_offset += len(raw_record)


def parse_peer_session_mrt_bytes(
    value: Union[bytes, bytearray, memoryview],
    *,
    collector_id: str,
    file_sha256: str,
    artifact_id: str,
) -> Tuple[PeerSessionObservation, ...]:
    """解析极小内存 fixture，并直接绑定全部会话观测身份。"""

    return tuple(
        parse_peer_session_observation(
            record,
            collector_id=collector_id,
            file_sha256=file_sha256,
            artifact_id=artifact_id,
        )
        for record in iter_peer_session_mrt_records(value)
    )


__all__ = (
    "AFI_IPV4",
    "AFI_IPV6",
    "BGP4MP_STATE_CHANGE",
    "BGP4MP_STATE_CHANGE_AS4",
    "MAX_MRT_PAYLOAD_BYTES",
    "MRT_BGP4MP",
    "MRT_BGP4MP_ET",
    "PEER_SESSION_OBSERVATION_ID_SCHEMA",
    "PEER_SESSION_OBSERVATION_SEMANTICS",
    "PEER_SESSION_PREFIX_INFERENCE",
    "PeerSessionObservation",
    "PeerSessionParseError",
    "iter_peer_session_mrt_records",
    "parse_peer_session_mrt_bytes",
    "parse_peer_session_observation",
    "peer_session_observation_id_v1",
)
