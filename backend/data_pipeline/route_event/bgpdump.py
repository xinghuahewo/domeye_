"""bgpdump 1.6.2 的 P0 UPDATE 单次读取适配器。

本模块只负责把 manifest 中已经固定 SHA256 的 gzip UPDATE 制品转换成
``ParsedMrtRecord`` 流。它不会修改原始文件、不落命名或持久化解压制品，也不会
把 ``bgpdump`` 无法无歧义表达的 AS_PATH 提升为结构化证据。为解除子进程 stdout
延迟 flush 与 stdin 持续投喂之间的环路背压，已验证 frame 会短暂写入有硬
上限的匿名临时 spool；它不是可发布制品，流关闭时必定删除。

安全边界刻意收紧：

* 只接受 MRT BGP4MP/BGP4MP_ET（type 16/17）的 MESSAGE subtype 1/4，
  以及单独统计的 STATE_CHANGE subtype 0/5；
* RIB、local message、Add-Path、ROUTE-REFRESH 和未知 stdout 行全部失败关闭；
  结构完整的 OPEN/NOTIFICATION/KEEPALIVE 只保留为有哈希的 ``raw_record``，
  不伪造成 RouteEvent，并按消息类型分别计数；
* ``bgpdump -m`` 的 AS_PATH 按 1.6.2 固定文本语法保留 sequence、AS_SET、
  confederation sequence/set 四种 segment；空字符串、错误标记、截断或
  语法不完整文本均拒绝，绝不构造假的 ``AsPathSegment``；
* 压缩文件只读且只经过一次压缩字节读取；同一解压 frame 同时用于 framing、
  record hash 和 ``bgpdump`` stdin。
"""

from __future__ import annotations

from collections import Counter
from collections import OrderedDict
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import copy
import gzip
import hashlib
import ipaddress
import os
from pathlib import Path, PurePosixPath
import queue
import re
import stat
import struct
import subprocess
import tempfile
import threading
import time
from typing import Any, BinaryIO, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple
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
from .index import AsPathSegment, ParsedMrtRecord, ParsedRouteElement, RouteEventInputError


BGPDUMP_APPROVED_VERSION = "1.6.2"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"\bbgpdump version ([0-9]+\.[0-9]+\.[0-9]+)\b")
_UNSIGNED_RE = re.compile(r"^(0|[1-9][0-9]*)$")
_TIMESTAMP_RE = re.compile(r"^(0|[1-9][0-9]*)(?:\.([0-9]{6}))?$")
_AS_PATH_TOKEN_RE = re.compile(r"^(0|[1-9][0-9]*)$")

_MRT_HEADER_LENGTH = 12
_UPDATE_SUBTYPES = frozenset((1, 4))
_STATE_CHANGE_SUBTYPES = frozenset((0, 5))
_ALLOWED_MRT_TYPES = frozenset((16, 17))
_ALLOWED_OUTPUT_FORMATS = frozenset(("BGP4MP", "BGP4MP_ET"))
_PARSER_ATTESTATION_FINGERPRINT_SCHEMA = "parser_attestation_fingerprint_v1"
_BGP_MARKER = b"\xff" * 16
_BGP_MESSAGE_OPEN = 1
_BGP_MESSAGE_UPDATE = 2
_BGP_MESSAGE_NOTIFICATION = 3
_BGP_MESSAGE_KEEPALIVE = 4
_SILENT_CONTROL_MESSAGE_TYPES = frozenset(
    (_BGP_MESSAGE_OPEN, _BGP_MESSAGE_NOTIFICATION, _BGP_MESSAGE_KEEPALIVE)
)
_SPOOL_ENTRY_MAGIC = b"DMRTSP01"
_SPOOL_ENTRY_HEADER = struct.Struct("!8sQQQ32s")

# stdout 队列只用于解析线程与消费线程之间的短暂解耦。默认容量保持 4，避免
# 改变通用调用方既有的背压语义；研究型调用方可显式提高，但不能越过下面的
# 双重硬上限。source byte 预算按读入行（含换行）的实际长度逐项扣减，而不是
# 仅用 item 数推测内存。
BGPDUMP_ABSOLUTE_MAX_STDOUT_QUEUE_CAPACITY = 4096
BGPDUMP_ABSOLUTE_MAX_STDOUT_QUEUE_SOURCE_BYTES = 8 * 1024 * 1024

# stdout 热路径按一次 pipe read 得到的完整行成批移交。该上限不会改变队列的
# logical item/source-byte 双重硬门：batch 入队仍按其中每一行精确计数；它只把
# 原来每行一次的 Condition acquire/notify 降为每批一次。64 KiB 与 pipe 读取
# 上限一致，因此 worker 不会为了凑批而等待下一次 I/O/ordinal/EOF。
_STDOUT_READ_CHUNK_BYTES = 64 * 1024
_STDOUT_BATCH_MAX_LINES = 1024

# AS_PATH/peer IP 规范化结果仅在单个 stdout worker 生命周期内缓存。缓存同时受
# entry 数和原始 key 字节数约束；parsed 对象继续沿用上方 64x+4KiB 的保守内存
# 模型，最坏 retained heap 小于 80 MiB，且不会跨 artifact 累积。
_LINE_PARSE_CACHE_MAX_ENTRIES = 4096
_LINE_PARSE_CACHE_MAX_KEY_BYTES = 1024 * 1024

# _parse_output_line 只保留定长字段、规范化字符串及由输入字符一一约束数量的
# AS_PATH 整数/segment。按 CPython 对小对象、tuple 和 dataclass 的开销取 64x
# source bytes，再为每项预留 4 KiB 固定开销，是刻意偏大的 retained-heap 上界。
# 在两个绝对上限同时取满时，队列保留对象的估算硬上界为 528 MiB。
_PARSED_LINE_MEMORY_EXPANSION_FACTOR = 64
_PARSED_LINE_FIXED_OVERHEAD_BYTES = 4096


@dataclass(frozen=True)
class _UpdateShape:
    announce_counts: Tuple[Tuple[str, int], ...]
    withdraw_counts: Tuple[Tuple[str, int], ...]


class BgpdumpAdapterError(RouteEventInputError):
    """bgpdump UPDATE 制品不能安全提升为 ``ParsedMrtRecord``。"""


class BgpdumpConfigurationError(BgpdumpAdapterError):
    """解析器 allowlist、manifest 路径或运行参数不符合冻结边界。"""


class BgpdumpIntegrityError(BgpdumpAdapterError):
    """二进制、压缩文件或 MRT framing 完整性校验失败。"""


class BgpdumpOutputError(BgpdumpAdapterError):
    """bgpdump stdout/stderr、ordinal 或退出状态不符合合同。"""


@dataclass(frozen=True)
class _Frame:
    ordinal: int
    offset: int
    raw_record: bytes
    record_sha256: str
    mrt_timestamp: int
    mrt_type: int
    mrt_subtype: int
    microseconds: int
    bgp_message_type: Optional[int]
    update_shape: Optional[_UpdateShape]


@dataclass(frozen=True)
class _ProducerDone:
    compressed_sha256: str
    compressed_size_bytes: int
    record_count: int


@dataclass(frozen=True)
class _ParsedLine:
    ordinal: int
    event_time_utc: str
    epoch_seconds: int
    microseconds: int
    kind: str
    element: Optional[ParsedRouteElement]
    state_peer_ip: Optional[str] = None
    state_peer_asn: Optional[int] = None
    old_state: Optional[int] = None
    new_state: Optional[int] = None
    source_line_bytes: int = 0


@dataclass(frozen=True)
class _StdoutDone:
    group_count: int


@dataclass(frozen=True)
class _QueueEntry:
    items: Tuple[Any, ...]
    source_bytes: int


class _BoundedOutputQueue:
    """同时按 logical item 数和原始 stdout 行字节数实施背压。

    内部 entry 可以包含多行，但 ``max_items``、``qsize`` 和 peak 统计始终按
    ``_ParsedLine`` 的 logical 数量计算；sentinel 仍占一个 item。批处理因此不
    会放宽既有内存/元素硬上限。
    """

    def __init__(self, *, max_items: int, max_source_bytes: int) -> None:
        self._max_items = max_items
        self._max_source_bytes = max_source_bytes
        self._entries: "deque[_QueueEntry]" = deque()
        self._item_count = 0
        self._source_bytes = 0
        self._peak_items = 0
        self._peak_source_bytes = 0
        self._entry_put_count = 0
        self._entry_get_count = 0
        self._peak_entry_items = 0
        self._condition = threading.Condition()

    @staticmethod
    def _weight(item: Any) -> int:
        if isinstance(item, _ParsedLine):
            return item.source_line_bytes
        return 0

    def _put_items(
        self, items: Tuple[Any, ...], timeout: Optional[float] = None
    ) -> None:
        if not items:
            raise ValueError("stdout queue batch 不得为空")
        if len(items) > self._max_items:
            raise queue.Full
        weights = tuple(self._weight(item) for item in items)
        if any(weight < 0 for weight in weights):
            raise queue.Full
        weight = sum(weights)
        if weight > self._max_source_bytes:
            raise queue.Full
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while (
                self._item_count + len(items) > self._max_items
                or self._source_bytes + weight > self._max_source_bytes
            ):
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Full
                self._condition.wait(remaining)
            self._entries.append(_QueueEntry(items, weight))
            self._item_count += len(items)
            self._source_bytes += weight
            self._entry_put_count += 1
            self._peak_entry_items = max(self._peak_entry_items, len(items))
            self._peak_items = max(self._peak_items, self._item_count)
            self._peak_source_bytes = max(
                self._peak_source_bytes, self._source_bytes
            )
            self._condition.notify_all()

    def put(self, item: Any, timeout: Optional[float] = None) -> None:
        self._put_items((item,), timeout)

    def put_many(
        self, items: Sequence[Any], timeout: Optional[float] = None
    ) -> None:
        self._put_items(tuple(items), timeout)

    def get(self, timeout: Optional[float] = None) -> Any:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._entries:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Empty
                self._condition.wait(remaining)
            entry = self._entries.popleft()
            item = entry.items[0]
            item_weight = self._weight(item)
            if len(entry.items) > 1:
                self._entries.appendleft(
                    _QueueEntry(entry.items[1:], entry.source_bytes - item_weight)
                )
            self._item_count -= 1
            self._source_bytes -= item_weight
            self._entry_get_count += 1
            self._condition.notify_all()
            return item

    def get_many(self, timeout: Optional[float] = None) -> Tuple[Any, ...]:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._entries:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Empty
                self._condition.wait(remaining)
            entry = self._entries.popleft()
            self._item_count -= len(entry.items)
            self._source_bytes -= entry.source_bytes
            self._entry_get_count += 1
            self._condition.notify_all()
            return entry.items

    def qsize(self) -> int:
        with self._condition:
            return self._item_count

    def source_bytes(self) -> int:
        with self._condition:
            return self._source_bytes

    def snapshot(self) -> Dict[str, int]:
        with self._condition:
            return {
                "current_items": self._item_count,
                "current_source_bytes": self._source_bytes,
                "peak_items": self._peak_items,
                "peak_source_bytes": self._peak_source_bytes,
                "entry_put_count": self._entry_put_count,
                "entry_get_count": self._entry_get_count,
                "peak_entry_items": self._peak_entry_items,
            }

    @property
    def max_items(self) -> int:
        return self._max_items

    @property
    def max_source_bytes(self) -> int:
        return self._max_source_bytes


class _FailureState:
    """保留并发流水线里的第一个、也是最接近根因的异常。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._error: Optional[BaseException] = None
        self._wakers: list[Callable[[], None]] = []

    def set(self, error: BaseException) -> bool:
        with self._lock:
            if self._error is not None:
                return False
            self._error = error
            wakers = tuple(self._wakers)
        for wake in wakers:
            try:
                wake()
            except BaseException:
                # 唤醒只用于降低失败传播延迟；不得覆盖原始异常。
                pass
        return True

    def register_waker(self, wake: Callable[[], None]) -> None:
        with self._lock:
            self._wakers.append(wake)
            already_failed = self._error is not None
        if already_failed:
            wake()

    def get(self) -> Optional[BaseException]:
        with self._lock:
            return self._error


class _ProgressState:
    """跨线程记录最后一次有意义 I/O，供 idle watchdog 使用。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last = time.monotonic()
        self._last_event = "pipeline_initialized"
        self._last_detail: Dict[str, Any] = {}
        self._event_counts: Counter[str] = Counter()
        self._compressed_bytes_read = 0

    def touch(self, event: str = "generic_progress", **detail: Any) -> None:
        with self._lock:
            self._last = time.monotonic()
            self._last_event = event
            # 调用点的 keyword 顺序在 Python 3.7+ 已稳定；热路径无需为每次
            # I/O 进展重新排序并分配 item 列表。
            self._last_detail = dict(detail)
            self._event_counts[event] += 1
            observed = detail.get("compressed_bytes_read")
            if isinstance(observed, int) and not isinstance(observed, bool):
                self._compressed_bytes_read = max(
                    self._compressed_bytes_read, observed
                )

    def idle_seconds(self) -> float:
        with self._lock:
            return time.monotonic() - self._last

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "last_event": self._last_event,
                "last_detail": dict(self._last_detail),
                "event_counts": dict(sorted(self._event_counts.items())),
                "compressed_bytes_read": self._compressed_bytes_read,
            }


class _BoundedLineParseCache:
    """单 artifact、双硬上限的 stdout 规范化 LRU。"""

    def __init__(self) -> None:
        self._entries: "OrderedDict[Tuple[str, str], Tuple[int, Any]]" = (
            OrderedDict()
        )
        self._key_bytes = 0

    def get_or_compute(
        self,
        namespace: str,
        value: str,
        compute: Callable[[], Any],
    ) -> Any:
        key = (namespace, value)
        cached = self._entries.pop(key, None)
        if cached is not None:
            self._entries[key] = cached
            return cached[1]
        result = compute()
        key_bytes = len(namespace.encode("utf-8")) + len(value.encode("utf-8"))
        if key_bytes > _LINE_PARSE_CACHE_MAX_KEY_BYTES:
            return result
        while self._entries and (
            len(self._entries) >= _LINE_PARSE_CACHE_MAX_ENTRIES
            or self._key_bytes + key_bytes > _LINE_PARSE_CACHE_MAX_KEY_BYTES
        ):
            _old_key, (old_bytes, _old_value) = self._entries.popitem(last=False)
            self._key_bytes -= old_bytes
        self._entries[key] = (key_bytes, result)
        self._key_bytes += key_bytes
        return result


def _pwrite_all(descriptor: int, payload: bytes, offset: int) -> None:
    view = memoryview(payload)
    current = offset
    while view:
        written = os.pwrite(descriptor, view, current)
        if isinstance(written, bool) or not isinstance(written, int) or written <= 0:
            raise OSError("匿名 spool 未继续接收字节")
        current += written
        view = view[written:]


def _pread_exact(descriptor: int, length: int, offset: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        block = os.pread(descriptor, length - len(chunks), offset + len(chunks))
        if not block:
            raise BgpdumpIntegrityError(
                f"匿名 spool 短读：offset={offset}，expected={length}，actual={len(chunks)}"
            )
        chunks.extend(block)
    return bytes(chunks)


def _hash_fd_range(descriptor: int, offset: int, length: int) -> str:
    digest = hashlib.sha256()
    consumed = 0
    while consumed < length:
        block = os.pread(
            descriptor,
            min(1024 * 1024, length - consumed),
            offset + consumed,
        )
        if not block:
            raise BgpdumpIntegrityError(
                f"匿名 spool hash 短读：offset={offset}，expected={length}，actual={consumed}"
            )
        digest.update(block)
        consumed += len(block)
    return digest.hexdigest()


class _AnonymousFrameSpool:
    """FD-only 顺序 spool：解除 stdin/stdout 背压，不暴露明文路径。"""

    def __init__(self, max_spool_bytes: int) -> None:
        # TemporaryFile 在 Unix 上使用 O_TMPFILE 或创建后立即 unlink。
        # 下方的 st_nlink==0 是 fail-closed 证明，不接受可见命名文件。
        self._stream = tempfile.TemporaryFile(
            mode="w+b", buffering=0, prefix="domeye-p0-bgpdump-"
        )
        self._descriptor = self._stream.fileno()
        os.fchmod(self._descriptor, 0o600)
        metadata = os.fstat(self._descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 0
            or metadata.st_mode & 0o077
        ):
            self._stream.close()
            raise BgpdumpConfigurationError(
                "无法建立已 unlink 且仅 owner 可读写的匿名 spool fd"
            )
        self._identity = (metadata.st_dev, metadata.st_ino)
        self._max_spool_bytes = max_spool_bytes
        self._condition = threading.Condition()
        self._committed_spool_bytes = 0
        self._committed_raw_bytes = 0
        self._committed_records = 0
        self._producer_done: Optional[_ProducerDone] = None
        self._closed = False

    def _metadata(self) -> os.stat_result:
        metadata = os.fstat(self._descriptor)
        if (
            (metadata.st_dev, metadata.st_ino) != self._identity
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 0
            or metadata.st_mode & 0o077
        ):
            raise BgpdumpIntegrityError("匿名 spool fd 身份或权限发生变化")
        return metadata

    def append(self, frame: _Frame) -> None:
        raw_length = len(frame.raw_record)
        if hashlib.sha256(frame.raw_record).hexdigest() != frame.record_sha256:
            raise BgpdumpIntegrityError("spool 写入前 MRT record hash 不一致")
        with self._condition:
            if self._closed or self._producer_done is not None:
                raise BgpdumpIntegrityError("spool 完成或关闭后仍尝试写入")
            if frame.ordinal != self._committed_records:
                raise BgpdumpIntegrityError("spool record ordinal 不连续")
            if frame.offset != self._committed_raw_bytes:
                raise BgpdumpIntegrityError("spool raw record offset 不连续")
            entry_offset = self._committed_spool_bytes
            entry_length = _SPOOL_ENTRY_HEADER.size + raw_length
            entry_end = entry_offset + entry_length
            if entry_end > self._max_spool_bytes:
                raise BgpdumpIntegrityError(
                    "匿名 spool 超过 selection.max_spool_bytes 独立硬上限"
                )
            before = self._metadata()
            if before.st_size != entry_offset:
                raise BgpdumpIntegrityError("spool 写入前文件长度与已提交偏移不一致")
            envelope = _SPOOL_ENTRY_HEADER.pack(
                _SPOOL_ENTRY_MAGIC,
                frame.ordinal,
                frame.offset,
                raw_length,
                bytes.fromhex(frame.record_sha256),
            )
            try:
                _pwrite_all(self._descriptor, envelope, entry_offset)
                raw_offset = entry_offset + _SPOOL_ENTRY_HEADER.size
                _pwrite_all(self._descriptor, frame.raw_record, raw_offset)
            except OSError as error:
                raise BgpdumpIntegrityError("匿名 spool 写入失败") from error
            after = self._metadata()
            if after.st_size != entry_end:
                raise BgpdumpIntegrityError("spool 写入后文件长度与预期偏移不一致")
            if _pread_exact(
                self._descriptor, _SPOOL_ENTRY_HEADER.size, entry_offset
            ) != envelope:
                raise BgpdumpIntegrityError("spool 写入后 envelope 复核失败")
            if _hash_fd_range(self._descriptor, raw_offset, raw_length) != frame.record_sha256:
                raise BgpdumpIntegrityError("spool 写入后 MRT record hash 复核失败")
            # 只有 envelope/长度/hash 全部复核后才对消费者提交。
            self._committed_spool_bytes = entry_end
            self._committed_raw_bytes += raw_length
            self._committed_records += 1
            self._condition.notify_all()

    def finish(self, done: _ProducerDone) -> None:
        with self._condition:
            if self._closed or self._producer_done is not None:
                raise BgpdumpIntegrityError("spool producer done 状态重复")
            if done.record_count != self._committed_records:
                raise BgpdumpIntegrityError("spool record 数与 producer done 不一致")
            self._producer_done = done
            self._condition.notify_all()

    def wake_waiters(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def snapshot(self) -> Dict[str, Any]:
        with self._condition:
            return {
                "max_spool_bytes": self._max_spool_bytes,
                "committed_spool_bytes": self._committed_spool_bytes,
                "committed_raw_bytes": self._committed_raw_bytes,
                "committed_records": self._committed_records,
                "producer_done": self._producer_done is not None,
                "anonymous_nlink_zero": True,
            }

    def read_next(
        self,
        *,
        spool_offset: int,
        expected_ordinal: int,
        expected_raw_offset: int,
        max_frame_bytes: int,
        failure: _FailureState,
        cancel: threading.Event,
        progress: _ProgressState,
        idle_timeout_seconds: float,
        runtime_snapshot: Optional[Callable[[], Mapping[str, Any]]] = None,
    ) -> Tuple[Optional[_ProducerDone], Optional[bytes], int]:
        while True:
            error = failure.get()
            if error is not None:
                raise error
            with self._condition:
                committed = self._committed_spool_bytes
                done = self._producer_done
                if spool_offset < committed:
                    break
                if spool_offset > committed:
                    raise BgpdumpIntegrityError("spool 消费偏移越过已提交边界")
                if done is not None:
                    return done, None, spool_offset
                self._condition.wait(timeout=0.1)
            if progress.idle_seconds() > idle_timeout_seconds:
                diagnostics = progress.snapshot()
                if runtime_snapshot is not None:
                    diagnostics["runtime"] = dict(runtime_snapshot())
                raise BgpdumpOutputError(
                    f"bgpdump 超过 {idle_timeout_seconds:g} 秒无 spool/stdout 进展；"
                    f"wait_stage=spool_frame；diagnostics={diagnostics!r}"
                )
            if cancel.is_set():
                error = failure.get()
                if error is not None:
                    raise error
                raise BgpdumpAdapterError("bgpdump 流水线在 spool 完成前被取消")

        envelope_before = _pread_exact(
            self._descriptor, _SPOOL_ENTRY_HEADER.size, spool_offset
        )
        magic, ordinal, raw_offset, raw_length, expected_hash = (
            _SPOOL_ENTRY_HEADER.unpack(envelope_before)
        )
        if magic != _SPOOL_ENTRY_MAGIC:
            raise BgpdumpIntegrityError("spool envelope magic 非法")
        if ordinal != expected_ordinal or raw_offset != expected_raw_offset:
            raise BgpdumpIntegrityError("spool envelope ordinal/raw offset 不连续")
        if raw_length < _MRT_HEADER_LENGTH or raw_length > max_frame_bytes:
            raise BgpdumpIntegrityError("spool envelope record length 越界")
        entry_end = spool_offset + _SPOOL_ENTRY_HEADER.size + raw_length
        with self._condition:
            if entry_end > self._committed_spool_bytes:
                raise BgpdumpIntegrityError("spool envelope record 越过已提交边界")
        raw = _pread_exact(
            self._descriptor,
            raw_length,
            spool_offset + _SPOOL_ENTRY_HEADER.size,
        )
        envelope_after = _pread_exact(
            self._descriptor, _SPOOL_ENTRY_HEADER.size, spool_offset
        )
        if envelope_after != envelope_before:
            raise BgpdumpIntegrityError("spool envelope 在 pread 期间发生变化")
        if hashlib.sha256(raw).digest() != expected_hash:
            raise BgpdumpIntegrityError("spool main pread MRT record hash 复核失败")
        metadata = self._metadata()
        if metadata.st_size < entry_end:
            raise BgpdumpIntegrityError("spool main pread 后文件长度回退")
        return None, raw, entry_end

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        self._stream.close()


class _HashingReader:
    """在 gzip 首次读取压缩字节时同步计算 SHA256。"""

    def __init__(
        self, stream: BinaryIO, progress: Optional[_ProgressState] = None
    ) -> None:
        self._stream = stream
        self._digest = hashlib.sha256()
        self.bytes_read = 0
        self._progress = progress

    def read(self, size: int = -1) -> bytes:
        block = self._stream.read(size)
        if block:
            self._digest.update(block)
            self.bytes_read += len(block)
            if self._progress is not None:
                self._progress.touch(
                    "compressed_bytes_read",
                    compressed_bytes_read=self.bytes_read,
                )
        return block

    def tell(self) -> int:
        return self.bytes_read

    @property
    def hexdigest(self) -> str:
        return self._digest.hexdigest()


def _adapter_error(error: BaseException, message: str) -> BgpdumpAdapterError:
    if isinstance(error, BgpdumpAdapterError):
        return error
    return BgpdumpAdapterError(f"{message}：{type(error).__name__}: {error}")


def _put_bounded(
    target: Any, item: Any, cancel: threading.Event
) -> bool:
    while not cancel.is_set():
        try:
            target.put(item, timeout=0.1)
            return True
        except queue.Full:
            continue
    return False


def _put_many_bounded(
    target: _BoundedOutputQueue,
    items: Sequence[Any],
    cancel: threading.Event,
) -> bool:
    while not cancel.is_set():
        try:
            target.put_many(items, timeout=0.1)
            return True
        except queue.Full:
            continue
    return False


def _get_bounded(
    source: Any,
    *,
    failure: _FailureState,
    cancel: threading.Event,
    progress: _ProgressState,
    idle_timeout_seconds: float,
    wait_stage: str,
    runtime_snapshot: Optional[Callable[[], Mapping[str, Any]]] = None,
) -> Any:
    while True:
        error = failure.get()
        if error is not None:
            raise error
        try:
            return source.get(timeout=0.1)
        except queue.Empty:
            if progress.idle_seconds() > idle_timeout_seconds:
                diagnostics = progress.snapshot()
                if runtime_snapshot is not None:
                    try:
                        diagnostics["runtime"] = dict(runtime_snapshot())
                    except BaseException as error:
                        diagnostics["runtime_snapshot_error"] = type(error).__name__
                raise BgpdumpOutputError(
                    f"bgpdump 超过 {idle_timeout_seconds:g} 秒无 frame/stdout 进展；"
                    f"wait_stage={wait_stage}；diagnostics={diagnostics!r}"
                )
            if cancel.is_set():
                error = failure.get()
                if error is not None:
                    raise error
                raise BgpdumpAdapterError("bgpdump 流水线在完成前被取消")


def _get_many_bounded(
    source: _BoundedOutputQueue,
    *,
    failure: _FailureState,
    cancel: threading.Event,
    progress: _ProgressState,
    idle_timeout_seconds: float,
    wait_stage: str,
    runtime_snapshot: Optional[Callable[[], Mapping[str, Any]]] = None,
) -> Tuple[Any, ...]:
    while True:
        error = failure.get()
        if error is not None:
            raise error
        try:
            return source.get_many(timeout=0.1)
        except queue.Empty:
            if progress.idle_seconds() > idle_timeout_seconds:
                diagnostics = progress.snapshot()
                if runtime_snapshot is not None:
                    try:
                        diagnostics["runtime"] = dict(runtime_snapshot())
                    except BaseException as error:
                        diagnostics["runtime_snapshot_error"] = type(error).__name__
                raise BgpdumpOutputError(
                    f"bgpdump 超过 {idle_timeout_seconds:g} 秒无 frame/stdout 进展；"
                    f"wait_stage={wait_stage}；diagnostics={diagnostics!r}"
                )
            if cancel.is_set():
                error = failure.get()
                if error is not None:
                    raise error
                raise BgpdumpAdapterError("bgpdump 流水线在完成前被取消")


def _read_exact(stream: BinaryIO, length: int, *, allow_clean_eof: bool) -> Optional[bytes]:
    chunks = bytearray()
    while len(chunks) < length:
        block = stream.read(length - len(chunks))
        if not block:
            if not chunks and allow_clean_eof:
                return None
            raise BgpdumpIntegrityError(
                f"解压 MRT 流被截断：需要 {length} 字节，仅得到 {len(chunks)} 字节"
            )
        chunks.extend(block)
    return bytes(chunks)


def _write_all(stream: BinaryIO, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = stream.write(view)
        if written is None:
            # 标准无缓冲 pipe 返回整数；None 会让“完整 frame 已写入”不可证明。
            raise BrokenPipeError("bgpdump stdin.write 未报告写入字节数")
        if isinstance(written, bool) or not isinstance(written, int) or written <= 0:
            raise BrokenPipeError("bgpdump stdin 未继续接收 MRT frame")
        view = view[written:]


def _read_available_stdout(stream: BinaryIO, maximum: int) -> bytes:
    """最多执行一次底层 pipe read，不等待凑满 ``maximum``。

    ``BufferedReader.read(n)`` 可以等待更多字节，不能用于 producer/stdout
    并行流水线。优先 ``read1``；真实/测试 pipe 则直接 ``os.read(fileno)``。
    无 fd 的内存 fixture 才退回普通 ``read``，其 EOF/可用字节语义是确定的。
    """

    read1 = getattr(stream, "read1", None)
    if callable(read1):
        return read1(maximum)
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError):
        return stream.read(maximum)
    return os.read(descriptor, maximum)


def _normalize_epoch_time(
    value: str,
    output_format: str,
    cache: Optional[_BoundedLineParseCache] = None,
) -> Tuple[str, int, int]:
    if cache is not None:
        return cache.get_or_compute(
            f"time:{output_format}",
            value,
            lambda: _normalize_epoch_time(value, output_format),
        )
    matched = _TIMESTAMP_RE.fullmatch(value)
    if matched is None:
        raise BgpdumpOutputError("bgpdump time 必须是十进制 Unix 秒或六位微秒")
    seconds = int(matched.group(1))
    if seconds > 4_294_967_295:
        raise BgpdumpOutputError("bgpdump time 超出 MRT uint32 范围")
    fraction = matched.group(2)
    microseconds = int(fraction) if fraction is not None else 0
    if output_format == "BGP4MP_ET" and fraction is None:
        raise BgpdumpOutputError("BGP4MP_ET 行缺少六位微秒")
    if output_format == "BGP4MP" and fraction is not None:
        raise BgpdumpOutputError("BGP4MP 行不应携带扩展微秒")
    moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
    if microseconds:
        text = moment.strftime("%Y-%m-%dT%H:%M:%S") + f".{microseconds:06d}Z"
    else:
        text = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    return text, seconds, microseconds


def _strict_utc_epoch_us(value: Any, field: str) -> int:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BgpdumpConfigurationError(f"{field} 必须是 Z 结尾 UTC 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise BgpdumpConfigurationError(f"{field} 不是有效 UTC 时间") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise BgpdumpConfigurationError(f"{field} 必须是 UTC 时间")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = parsed - epoch
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _parse_uint(value: str, field: str, maximum: int) -> int:
    if _UNSIGNED_RE.fullmatch(value) is None:
        raise BgpdumpOutputError(f"{field} 必须是不带符号的十进制整数")
    parsed = int(value)
    if parsed > maximum:
        raise BgpdumpOutputError(f"{field} 超出合同范围")
    return parsed


def _normalize_ip(
    value: str,
    field: str,
    cache: Optional[_BoundedLineParseCache] = None,
) -> str:
    if cache is not None:
        return cache.get_or_compute(
            "ip",
            value,
            lambda: _normalize_ip(value, field),
        )
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError as error:
        raise BgpdumpOutputError(f"{field} 不是有效 IP") from error


def _normalize_prefix_and_afi(
    value: str,
    cache: Optional[_BoundedLineParseCache] = None,
) -> Tuple[str, str]:
    if cache is not None:
        return cache.get_or_compute(
            "prefix_afi",
            value,
            lambda: _normalize_prefix_and_afi(value),
        )
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as error:
        raise BgpdumpOutputError("bgpdump prefix 不是有效 CIDR") from error
    return (
        network.compressed,
        "ipv4_unicast" if network.version == 4 else "ipv6_unicast",
    )


def _normalize_prefix(value: str) -> str:
    # 兼容模块内既有私有测试/诊断入口；热路径使用组合函数避免重复解析。
    return _normalize_prefix_and_afi(value)[0]


def _prefix_afi_safi(value: str) -> str:
    return _normalize_prefix_and_afi(value)[1]


def _parse_asn_token(value: str) -> int:
    if _AS_PATH_TOKEN_RE.fullmatch(value) is None:
        raise BgpdumpOutputError("bgpdump AS_PATH ASN token 非法")
    asn = int(value)
    if asn > 4_294_967_295:
        raise BgpdumpOutputError("bgpdump AS_PATH ASN 超出 32 位范围")
    return asn


def _parse_as_path(
    value: str,
    cache: Optional[_BoundedLineParseCache] = None,
) -> Tuple[AsPathSegment, ...]:
    if cache is not None:
        return cache.get_or_compute(
            "as_path",
            value,
            lambda: _parse_as_path(value),
        )
    if not value:
        raise BgpdumpOutputError(
            "bgpdump 空 AS_PATH 无法区分合法空路径与属性缺失，拒绝提升"
        )
    if value in {"ASPATH ERROR", "! Error !"} or "..." in value:
        raise BgpdumpOutputError("bgpdump AS_PATH 错误或被 1.6.2 文本缓冲截断")
    # bgpdump 1.6.2 的 process_attr_aspath_string 使用以下无歧义文本语法：
    # sequence 以空格分隔，AS_SET={a,b}，confed set=[a,b]，confed
    # sequence=(a b)。超过内部 8000 字符缓冲时追加 ...，已在上面拒绝。
    delimiters = {
        "{": ("}", ",", "as_set"),
        "[": ("]", ",", "confederation_set"),
        "(": (")", " ", "confederation_sequence"),
    }
    segments: list[AsPathSegment] = []
    sequence: list[int] = []
    total_asns = 0

    def flush_sequence() -> None:
        nonlocal sequence
        if sequence:
            segments.append(AsPathSegment("as_sequence", tuple(sequence)))
            sequence = []

    index = 0
    expecting_item = True
    while index < len(value):
        if not expecting_item:
            if value[index] != " ":
                raise BgpdumpOutputError("bgpdump AS_PATH segment 间必须恰有一个空格")
            index += 1
            if index >= len(value) or value[index] == " ":
                raise BgpdumpOutputError("bgpdump AS_PATH 含多余空白")
            expecting_item = True
            continue

        character = value[index]
        if character in delimiters:
            flush_sequence()
            closing, separator, segment_type = delimiters[character]
            end = value.find(closing, index + 1)
            if end < 0:
                raise BgpdumpOutputError("bgpdump AS_PATH segment 未闭合")
            inner = value[index + 1 : end]
            if not inner or any(mark in inner for mark in "{}[]()"):
                raise BgpdumpOutputError("bgpdump AS_PATH segment 为空或嵌套")
            tokens = inner.split(separator)
            if any(not token for token in tokens):
                raise BgpdumpOutputError("bgpdump AS_PATH segment 分隔非法")
            asns = tuple(_parse_asn_token(token) for token in tokens)
            segments.append(AsPathSegment(segment_type, asns))
            total_asns += len(asns)
            index = end + 1
        else:
            end = index
            while end < len(value) and value[end].isdigit():
                end += 1
            if end == index:
                raise BgpdumpOutputError("bgpdump AS_PATH 含未知文本")
            sequence.append(_parse_asn_token(value[index:end]))
            total_asns += 1
            index = end
        if total_asns > 4096 or len(segments) > 4096:
            raise BgpdumpOutputError("bgpdump AS_PATH 超过 RouteEvent 合同上限")
        expecting_item = False

    if expecting_item:
        raise BgpdumpOutputError("bgpdump AS_PATH 以分隔符结束")
    flush_sequence()
    if len(segments) > 4096:
        raise BgpdumpOutputError("bgpdump AS_PATH segment 数超过合同上限")
    return tuple(segments)


def _parse_output_line(
    text: str,
    *,
    source_line_bytes: int,
    parse_cache: Optional[_BoundedLineParseCache] = None,
) -> _ParsedLine:
    fields = text.split("|")
    if len(fields) < 4:
        raise BgpdumpOutputError("bgpdump stdout 行字段不足")
    output_format = fields[0]
    if output_format not in _ALLOWED_OUTPUT_FORMATS:
        raise BgpdumpOutputError("bgpdump stdout 含未验收 format")
    ordinal = _parse_uint(fields[1], "bgpdump -p ordinal", 2**63 - 1)
    event_time, epoch_seconds, microseconds = _normalize_epoch_time(
        fields[2], output_format, parse_cache
    )
    kind = fields[3]

    if kind == "STATE":
        if len(fields) != 8:
            raise BgpdumpOutputError("bgpdump STATE 行字段数不符合 1.6.2 -m -p")
        return _ParsedLine(
            ordinal=ordinal,
            event_time_utc=event_time,
            epoch_seconds=epoch_seconds,
            microseconds=microseconds,
            kind=kind,
            element=None,
            state_peer_ip=_normalize_ip(fields[4], "STATE peer_ip", parse_cache),
            state_peer_asn=_parse_uint(fields[5], "STATE peer_asn", 4_294_967_295),
            old_state=_parse_uint(fields[6], "STATE old_state", 65_535),
            new_state=_parse_uint(fields[7], "STATE new_state", 65_535),
            source_line_bytes=source_line_bytes,
        )

    if kind == "W":
        if len(fields) != 7:
            raise BgpdumpOutputError("bgpdump withdraw 行字段数不符合 1.6.2 -m -p")
        prefix, afi_safi = _normalize_prefix_and_afi(fields[6], parse_cache)
        element = ParsedRouteElement(
            event_time_utc=event_time,
            peer_ip=_normalize_ip(fields[4], "withdraw peer_ip", parse_cache),
            peer_asn=_parse_uint(fields[5], "withdraw peer_asn", 4_294_967_295),
            action="withdraw",
            prefix=prefix,
            afi_safi=afi_safi,
            as_path=None,
        )
        return _ParsedLine(
            ordinal,
            event_time,
            epoch_seconds,
            microseconds,
            kind,
            element,
            source_line_bytes=source_line_bytes,
        )

    if kind == "A":
        # 未开启 -l/-u 且非 Add-Path 时，1.6.2 的 -m announce 固定以空列
        # 结束，共 16 列。严格字段数可以阻止未来输出漂移被静默错位解析。
        if len(fields) != 16 or fields[-1] != "":
            raise BgpdumpOutputError("bgpdump announce 行字段数不符合 1.6.2 -m -p")
        prefix, afi_safi = _normalize_prefix_and_afi(fields[6], parse_cache)
        element = ParsedRouteElement(
            event_time_utc=event_time,
            peer_ip=_normalize_ip(fields[4], "announce peer_ip", parse_cache),
            peer_asn=_parse_uint(fields[5], "announce peer_asn", 4_294_967_295),
            action="announce",
            prefix=prefix,
            afi_safi=afi_safi,
            as_path=_parse_as_path(fields[7], parse_cache),
        )
        return _ParsedLine(
            ordinal,
            event_time,
            epoch_seconds,
            microseconds,
            kind,
            element,
            source_line_bytes=source_line_bytes,
        )

    raise BgpdumpOutputError("bgpdump stdout 含未验收 action/record 行")


def _parse_nlri_count(payload: bytes, afi: int, field: str) -> int:
    maximum_bits = 32 if afi == 1 else 128 if afi == 2 else None
    if maximum_bits is None:
        raise BgpdumpIntegrityError(f"{field} AFI 不在 IPv4/IPv6 allowlist")
    offset = 0
    count = 0
    while offset < len(payload):
        prefix_length = payload[offset]
        offset += 1
        if prefix_length > maximum_bits:
            raise BgpdumpIntegrityError(f"{field} prefix length 超出 AFI 范围")
        octets = (prefix_length + 7) // 8
        if offset + octets > len(payload):
            raise BgpdumpIntegrityError(f"{field} NLRI 被截断")
        offset += octets
        count += 1
    return count


def _add_family_count(
    target: Counter[str], afi: int, safi: int, count: int, field: str
) -> None:
    if safi != 1 or afi not in {1, 2}:
        raise BgpdumpIntegrityError(
            f"{field} 只允许 IPv4/IPv6 unicast，观测到 AFI={afi}, SAFI={safi}"
        )
    if count:
        target["ipv4_unicast" if afi == 1 else "ipv6_unicast"] += count


def _parse_path_attributes(
    payload: bytes,
    announce: Counter[str],
    withdraw: Counter[str],
) -> None:
    offset = 0
    while offset < len(payload):
        if len(payload) - offset < 3:
            raise BgpdumpIntegrityError("BGP path attribute header 被截断")
        flags = payload[offset]
        attribute_type = payload[offset + 1]
        offset += 2
        if flags & 0x10:
            if len(payload) - offset < 2:
                raise BgpdumpIntegrityError("BGP extended attribute length 被截断")
            length = struct.unpack("!H", payload[offset : offset + 2])[0]
            offset += 2
        else:
            length = payload[offset]
            offset += 1
        if offset + length > len(payload):
            raise BgpdumpIntegrityError("BGP path attribute value 越界")
        value = payload[offset : offset + length]
        offset += length

        if attribute_type == 14:  # MP_REACH_NLRI
            if len(value) < 5:
                raise BgpdumpIntegrityError("MP_REACH_NLRI 属性过短")
            afi = struct.unpack("!H", value[:2])[0]
            safi = value[2]
            next_hop_length = value[3]
            nlri_start = 4 + next_hop_length
            if nlri_start >= len(value):
                raise BgpdumpIntegrityError("MP_REACH_NLRI next-hop/reserved 越界")
            if value[nlri_start] != 0:
                raise BgpdumpIntegrityError("MP_REACH_NLRI reserved 字节非零")
            nlri = value[nlri_start + 1 :]
            count = _parse_nlri_count(nlri, afi, "MP_REACH_NLRI")
            _add_family_count(announce, afi, safi, count, "MP_REACH_NLRI")
        elif attribute_type == 15:  # MP_UNREACH_NLRI
            if len(value) < 3:
                raise BgpdumpIntegrityError("MP_UNREACH_NLRI 属性过短")
            afi = struct.unpack("!H", value[:2])[0]
            safi = value[2]
            nlri = value[3:]
            count = _parse_nlri_count(nlri, afi, "MP_UNREACH_NLRI")
            _add_family_count(withdraw, afi, safi, count, "MP_UNREACH_NLRI")


def _validate_open_body(body: bytes) -> None:
    # RFC 4271 OPEN 固定体为 10 字节，最后一字节声明 optional parameters
    # 的总长度。P0 只准入 BGP-4 并校验边界/闭合；能力码和协商结果不在
    # 原始观测层被伪装成已接受会话。
    if len(body) < 10:
        raise BgpdumpIntegrityError("BGP OPEN body 被截断")
    if body[0] != 4:
        raise BgpdumpIntegrityError("BGP OPEN version 必须为 4")
    optional_length = body[9]
    if len(body) != 10 + optional_length:
        raise BgpdumpIntegrityError("BGP OPEN optional parameters 长度不闭合")
    offset = 10
    while offset < len(body):
        if len(body) - offset < 2:
            raise BgpdumpIntegrityError("BGP OPEN optional parameter header 被截断")
        parameter_type = body[offset]
        parameter_length = body[offset + 1]
        offset += 2
        if offset + parameter_length > len(body):
            raise BgpdumpIntegrityError("BGP OPEN optional parameter value 被截断")
        parameter_end = offset + parameter_length
        if parameter_type == 2:
            capability_offset = offset
            while capability_offset < parameter_end:
                if parameter_end - capability_offset < 2:
                    raise BgpdumpIntegrityError("BGP OPEN capability header 被截断")
                capability_length = body[capability_offset + 1]
                capability_offset += 2
                if capability_offset + capability_length > parameter_end:
                    raise BgpdumpIntegrityError("BGP OPEN capability value 被截断")
                capability_offset += capability_length
        offset += parameter_length


def _validate_notification_body(body: bytes) -> None:
    # Error Code + Error Subcode 为固定最短两字节；其余 Data 不解释但必须
    # 已由 common BGP length 精确框定。
    if len(body) < 2:
        raise BgpdumpIntegrityError("BGP NOTIFICATION body 被截断")


def _parse_bgp_message_shape(
    payload: bytes, mrt_subtype: int
) -> Tuple[int, Optional[_UpdateShape]]:
    """验证 BGP4MP message，并只把 UPDATE 提升为可路由元素 shape。

    RIS 的 ``updates.*`` 制品会夹带会话控制消息。OPEN、NOTIFICATION 和
    KEEPALIVE 没有 ``bgpdump -p`` 行，也没有可提升的路由元素，但仍属于必须
    进入 record hash-chain 的原始 physical record。
    """

    asn_octets = 2 if mrt_subtype == 1 else 4 if mrt_subtype == 4 else None
    if asn_octets is None:
        raise BgpdumpIntegrityError("BGP UPDATE subtype 未验收")
    fixed = asn_octets * 2 + 4  # peer/local ASN + interface index + AFI
    if len(payload) < fixed:
        raise BgpdumpIntegrityError("BGP4MP message header 被截断")
    afi = struct.unpack("!H", payload[asn_octets * 2 + 2 : fixed])[0]
    if afi not in {1, 2}:
        raise BgpdumpIntegrityError("BGP4MP peer address AFI 未验收")
    address_bytes = 4 if afi == 1 else 16
    bgp_offset = fixed + address_bytes * 2
    if len(payload) < bgp_offset + 19:
        raise BgpdumpIntegrityError("BGP message header 被截断")
    message = payload[bgp_offset:]
    if message[:16] != _BGP_MARKER:
        raise BgpdumpIntegrityError("BGP marker 非全 0xff")
    message_length = struct.unpack("!H", message[16:18])[0]
    if message_length < 19 or message_length != len(message):
        raise BgpdumpIntegrityError("BGP message length 与 MRT payload 不一致")
    message_type = message[18]
    body = message[19:]
    if message_type == _BGP_MESSAGE_OPEN:
        _validate_open_body(body)
        return message_type, None
    if message_type == _BGP_MESSAGE_NOTIFICATION:
        _validate_notification_body(body)
        return message_type, None
    if message_type == _BGP_MESSAGE_KEEPALIVE:
        if message_length != 19:
            raise BgpdumpIntegrityError("BGP KEEPALIVE 长度必须严格为 19")
        return message_type, None
    if message_type != _BGP_MESSAGE_UPDATE:
        raise BgpdumpIntegrityError(
            f"UPDATE artifact 含未获准 BGP message type={message_type}"
        )
    if len(body) < 4:
        raise BgpdumpIntegrityError("BGP UPDATE body 被截断")
    withdrawn_length = struct.unpack("!H", body[:2])[0]
    if 2 + withdrawn_length + 2 > len(body):
        raise BgpdumpIntegrityError("BGP UPDATE withdrawn routes 越界")
    withdrawn_payload = body[2 : 2 + withdrawn_length]
    attributes_offset = 2 + withdrawn_length
    attributes_length = struct.unpack(
        "!H", body[attributes_offset : attributes_offset + 2]
    )[0]
    attributes_start = attributes_offset + 2
    attributes_end = attributes_start + attributes_length
    if attributes_end > len(body):
        raise BgpdumpIntegrityError("BGP UPDATE path attributes 越界")

    announce: Counter[str] = Counter()
    withdraw: Counter[str] = Counter()
    standard_withdraw_count = _parse_nlri_count(
        withdrawn_payload, 1, "IPv4 withdrawn routes"
    )
    if standard_withdraw_count:
        withdraw["ipv4_unicast"] += standard_withdraw_count
    _parse_path_attributes(
        body[attributes_start:attributes_end], announce, withdraw
    )
    standard_announce_count = _parse_nlri_count(
        body[attributes_end:], 1, "IPv4 announced NLRI"
    )
    if standard_announce_count:
        announce["ipv4_unicast"] += standard_announce_count
    return (
        message_type,
        _UpdateShape(
            announce_counts=tuple(sorted(announce.items())),
            withdraw_counts=tuple(sorted(withdraw.items())),
        ),
    )


def _frame_from_raw_record(
    raw_record: bytes,
    *,
    ordinal: int,
    offset: int,
    max_frame_bytes: int,
    window_start_epoch_us: int,
    window_end_epoch_us: int,
    slot_start_epoch_us: int,
    slot_end_epoch_us: int,
) -> _Frame:
    """对 producer 内存字节与 main spool pread 字节执行同一套复核。"""

    if len(raw_record) < _MRT_HEADER_LENGTH:
        raise BgpdumpIntegrityError("MRT physical frame 短于 common header")
    mrt_timestamp, mrt_type, mrt_subtype, payload_length = struct.unpack(
        "!IHHI", raw_record[:_MRT_HEADER_LENGTH]
    )
    total_length = _MRT_HEADER_LENGTH + payload_length
    if total_length != len(raw_record):
        raise BgpdumpIntegrityError("MRT physical frame header length 与完整字节不一致")
    if total_length > max_frame_bytes:
        raise BgpdumpIntegrityError("MRT physical frame 超过适配器显式上限")
    if mrt_type not in _ALLOWED_MRT_TYPES:
        raise BgpdumpIntegrityError("UPDATE 适配器只允许 MRT type 16/17")
    if mrt_subtype not in _UPDATE_SUBTYPES | _STATE_CHANGE_SUBTYPES:
        raise BgpdumpIntegrityError(
            "UPDATE 适配器拒绝 local、Add-Path 或未知 BGP4MP subtype"
        )
    payload = raw_record[_MRT_HEADER_LENGTH:]
    microseconds = 0
    if mrt_type == 17:
        if payload_length < 4:
            raise BgpdumpIntegrityError("BGP4MP_ET frame 缺少四字节扩展微秒")
        microseconds = struct.unpack("!I", payload[:4])[0]
        if microseconds > 999_999:
            raise BgpdumpIntegrityError("BGP4MP_ET 扩展微秒超出 0..999999")
    event_epoch_us = mrt_timestamp * 1_000_000 + microseconds
    if not window_start_epoch_us <= event_epoch_us < window_end_epoch_us:
        raise BgpdumpIntegrityError("MRT record 时间越出父 manifest data-profile 固定窗口")
    if not slot_start_epoch_us <= event_epoch_us < slot_end_epoch_us:
        raise BgpdumpIntegrityError("MRT UPDATE record 时间越出文件名对应五分钟槽")
    semantic_payload = payload[4:] if mrt_type == 17 else payload
    bgp_message_type: Optional[int] = None
    update_shape: Optional[_UpdateShape] = None
    if mrt_subtype in _UPDATE_SUBTYPES:
        bgp_message_type, update_shape = _parse_bgp_message_shape(
            semantic_payload, mrt_subtype
        )
    return _Frame(
        ordinal=ordinal,
        offset=offset,
        raw_record=raw_record,
        record_sha256=hashlib.sha256(raw_record).hexdigest(),
        mrt_timestamp=mrt_timestamp,
        mrt_type=mrt_type,
        mrt_subtype=mrt_subtype,
        microseconds=microseconds,
        bgp_message_type=bgp_message_type,
        update_shape=update_shape,
    )


def _producer_worker(
    *,
    path: Path,
    expected_file_sha256: str,
    expected_size_bytes: int,
    stdin: BinaryIO,
    spool: _AnonymousFrameSpool,
    cancel: threading.Event,
    failure: _FailureState,
    progress: _ProgressState,
    max_frame_bytes: int,
    max_physical_records: int,
    window_start_epoch_us: int,
    window_end_epoch_us: int,
    slot_start_epoch_us: int,
    slot_end_epoch_us: int,
) -> None:
    descriptor: Optional[int] = None
    try:
        progress.touch("producer_started")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise BgpdumpIntegrityError("原始 MRT 制品不是普通文件")
        if before.st_size != expected_size_bytes:
            raise BgpdumpIntegrityError("原始 MRT 制品大小与 artifact manifest 不一致")
        with os.fdopen(descriptor, "rb", buffering=0) as compressed:
            descriptor = None
            hashing = _HashingReader(compressed, progress)
            record_count = 0
            offset = 0
            try:
                with gzip.GzipFile(fileobj=hashing, mode="rb") as decoded:
                    while not cancel.is_set():
                        header = _read_exact(
                            decoded, _MRT_HEADER_LENGTH, allow_clean_eof=True
                        )
                        if header is None:
                            break
                        mrt_timestamp, mrt_type, mrt_subtype, payload_length = struct.unpack(
                            "!IHHI", header
                        )
                        progress.touch(
                            "mrt_header_read",
                            ordinal=record_count,
                            mrt_type=mrt_type,
                            mrt_subtype=mrt_subtype,
                            payload_length=payload_length,
                        )
                        total_length = _MRT_HEADER_LENGTH + payload_length
                        if total_length > max_frame_bytes:
                            raise BgpdumpIntegrityError(
                                "MRT physical frame 超过适配器显式上限"
                            )
                        if mrt_type not in _ALLOWED_MRT_TYPES:
                            raise BgpdumpIntegrityError("UPDATE 适配器只允许 MRT type 16/17")
                        if mrt_subtype not in _UPDATE_SUBTYPES | _STATE_CHANGE_SUBTYPES:
                            raise BgpdumpIntegrityError(
                                "UPDATE 适配器拒绝 local、Add-Path 或未知 BGP4MP subtype"
                            )
                        payload = _read_exact(decoded, payload_length, allow_clean_eof=False)
                        assert payload is not None
                        if record_count >= max_physical_records:
                            raise BgpdumpIntegrityError(
                                "UPDATE pilot 超过 physical record 硬上限"
                            )
                        raw_record = header + payload
                        frame = _frame_from_raw_record(
                            raw_record,
                            ordinal=record_count,
                            offset=offset,
                            max_frame_bytes=max_frame_bytes,
                            window_start_epoch_us=window_start_epoch_us,
                            window_end_epoch_us=window_end_epoch_us,
                            slot_start_epoch_us=slot_start_epoch_us,
                            slot_end_epoch_us=slot_end_epoch_us,
                        )
                        expected_output_lines = (
                            1
                            if frame.mrt_subtype in _STATE_CHANGE_SUBTYPES
                            else sum(
                                count
                                for _family, count in frame.update_shape.announce_counts
                            )
                            + sum(
                                count
                                for _family, count in frame.update_shape.withdraw_counts
                            )
                            if frame.update_shape is not None
                            else 0
                        )
                        progress.touch(
                            "frame_validated",
                            ordinal=record_count,
                            expected_output_lines=expected_output_lines,
                            record_length=total_length,
                        )
                        # 先把完整已验证 frame 提交到匿名 spool，再投喂
                        # stdin。即使 bgpdump 延迟 flush stdout，main 也始终
                        # 能取得当前 ordinal，不会与 producer 形成环路等待。
                        spool.append(frame)
                        progress.touch(
                            "frame_spooled",
                            ordinal=record_count,
                            committed_spool_bytes=spool.snapshot()[
                                "committed_spool_bytes"
                            ],
                        )
                        try:
                            _write_all(stdin, raw_record)
                        except (BrokenPipeError, OSError) as error:
                            raise BgpdumpOutputError(
                                f"bgpdump stdin 断管，record_ordinal={record_count}"
                            ) from error
                        progress.touch(
                            "stdin_frame_written",
                            ordinal=record_count,
                            record_length=total_length,
                        )
                        record_count += 1
                        offset += total_length
            except (gzip.BadGzipFile, EOFError, zlib.error) as error:
                raise BgpdumpIntegrityError("gzip 解压失败或压缩流被截断") from error

            # GzipFile 通常已读到压缩 EOF；若实现仍留有尾部字节，在同一次
            # 打开的压缩流中继续读取并纳入 SHA，不启动第二遍文件读取。
            while hashing.read(1024 * 1024):
                pass
            after = os.fstat(compressed.fileno())
            immutable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(before, name) != getattr(after, name) for name in immutable):
                raise BgpdumpIntegrityError("解析期间原始 MRT 制品发生变化")
            if hashing.bytes_read != expected_size_bytes:
                raise BgpdumpIntegrityError("压缩字节读取量与 artifact manifest 不一致")
            if hashing.hexdigest != expected_file_sha256:
                raise BgpdumpIntegrityError("file SHA256 与 artifact manifest 不一致")
            if record_count == 0:
                raise BgpdumpIntegrityError("UPDATE 制品没有 MRT physical record")
            done = _ProducerDone(
                hashing.hexdigest,
                hashing.bytes_read,
                record_count,
            )
            spool.finish(done)
            progress.touch(
                "producer_done_spooled",
                record_count=record_count,
                compressed_size_bytes=hashing.bytes_read,
                peak_spool_bytes=spool.snapshot()["committed_spool_bytes"],
            )
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            failure.set(error)
        else:
            failure.set(_adapter_error(error, "读取或 framing 原始 MRT 失败"))
        cancel.set()
    finally:
        spool.wake_waiters()
        if descriptor is not None:
            os.close(descriptor)
        try:
            stdin.close()
        except (BrokenPipeError, OSError):
            pass


def _stdout_worker(
    *,
    stdout: BinaryIO,
    outputs: _BoundedOutputQueue,
    cancel: threading.Event,
    failure: _FailureState,
    progress: _ProgressState,
    max_stdout_line_bytes: int,
    max_output_lines: int,
) -> None:
    current_ordinal: Optional[int] = None
    group_count = 0
    output_line_count = 0
    parsing = True
    pending_bytes = bytearray()
    parse_cache = _BoundedLineParseCache()
    batch: list[_ParsedLine] = []
    batch_source_bytes = 0
    batch_line_limit = min(_STDOUT_BATCH_MAX_LINES, outputs.max_items)

    def flush_batch() -> bool:
        nonlocal batch, batch_source_bytes
        if not batch:
            return True
        first_ordinal = batch[0].ordinal
        last_ordinal = batch[-1].ordinal
        line_count = len(batch)
        source_bytes = batch_source_bytes
        if not _put_many_bounded(outputs, batch, cancel):
            return False
        # 批成功入队后才清空本地引用；失败/取消不伪报进度。
        batch = []
        batch_source_bytes = 0
        progress.touch(
            "stdout_batch_enqueued",
            first_ordinal=first_ordinal,
            last_ordinal=last_ordinal,
            line_count=line_count,
            source_bytes=source_bytes,
        )
        return True

    try:
        progress.touch("stdout_worker_started")
        while True:
            block = _read_available_stdout(stdout, _STDOUT_READ_CHUNK_BYTES)
            if not block:
                break
            progress.touch("stdout_chunk_read", chunk_bytes=len(block))
            if not parsing:
                continue
            pending_bytes.extend(block)
            try:
                start = 0
                while True:
                    newline = pending_bytes.find(b"\n", start)
                    if newline < 0:
                        break
                    source_line_bytes = newline - start + 1
                    if source_line_bytes > max_stdout_line_bytes:
                        raise BgpdumpOutputError("bgpdump stdout 单行超过显式上限")
                    raw_line = bytes(pending_bytes[start:newline])
                    text = raw_line.decode("utf-8", errors="strict")
                    if "\x00" in text or "\r" in text:
                        raise BgpdumpOutputError("bgpdump stdout 含非法控制字符")
                    parsed = _parse_output_line(
                        text,
                        source_line_bytes=source_line_bytes,
                        parse_cache=parse_cache,
                    )
                    output_line_count += 1
                    if output_line_count > max_output_lines:
                        raise BgpdumpOutputError(
                            "bgpdump stdout 超过 pilot route/state 行硬上限"
                        )
                    if current_ordinal is None:
                        current_ordinal = parsed.ordinal
                        group_count = 1
                    elif parsed.ordinal < current_ordinal:
                        raise BgpdumpOutputError("bgpdump -p ordinal 发生回退")
                    elif parsed.ordinal > current_ordinal:
                        # 合法控制消息没有 -p 行，所以 ordinal 可跳过；
                        # raw shape 匹配仍由 main 线程逐 frame 失败关闭。
                        group_count += 1
                        current_ordinal = parsed.ordinal
                    if (
                        batch
                        and (
                            len(batch) >= batch_line_limit
                            or batch_source_bytes + source_line_bytes
                            > outputs.max_source_bytes
                        )
                        and not flush_batch()
                    ):
                        return
                    batch.append(parsed)
                    batch_source_bytes += source_line_bytes
                    start = newline + 1
                if start:
                    del pending_bytes[:start]
                if len(pending_bytes) > max_stdout_line_bytes:
                    raise BgpdumpOutputError("bgpdump stdout 单行超过显式上限")
                # 不跨 blocking read 等待凑批，保证单行/高基数单 frame 在子进程
                # 暂不 EOF 时仍立即可消费；一次 pipe read 内仍可合并数百行。
                if not flush_batch():
                    return
            except (UnicodeDecodeError, BgpdumpAdapterError) as error:
                failure.set(_adapter_error(error, "解析 bgpdump stdout 失败"))
                cancel.set()
                parsing = False
        if parsing:
            if pending_bytes:
                raise BgpdumpOutputError("bgpdump stdout 末行缺少换行")
            if not flush_batch():
                return
            _put_bounded(outputs, _StdoutDone(group_count), cancel)
            progress.touch(
                "stdout_done_enqueued",
                group_count=group_count,
                output_line_count=output_line_count,
            )
    except BaseException as error:
        failure.set(_adapter_error(error, "消费 bgpdump stdout 失败"))
        cancel.set()


def _stderr_worker(
    *,
    stderr: BinaryIO,
    cancel: threading.Event,
    failure: _FailureState,
    progress: _ProgressState,
    captured: bytearray,
    max_stderr_bytes: int,
    done: threading.Event,
) -> None:
    total = 0
    try:
        progress.touch("stderr_worker_started")
        while True:
            block = stderr.read(64 * 1024)
            if not block:
                break
            progress.touch("stderr_bytes_read", block_bytes=len(block))
            total += len(block)
            if len(captured) < max_stderr_bytes:
                captured.extend(block[: max_stderr_bytes - len(captured)])
            if failure.get() is None:
                preview = bytes(captured).decode("utf-8", errors="replace").strip()
                failure.set(
                    BgpdumpOutputError(
                        "bgpdump stderr 非空"
                        + (f"：{preview}" if preview else "")
                    )
                )
                cancel.set()
        if total > max_stderr_bytes and failure.get() is None:
            failure.set(BgpdumpOutputError("bgpdump stderr 超过显式捕获上限"))
            cancel.set()
    except BaseException as error:
        failure.set(_adapter_error(error, "消费 bgpdump stderr 失败"))
        cancel.set()
    finally:
        done.set()


def _default_version_probe(
    path: Path, *, executable: Optional[Path] = None, pass_fds: Tuple[int, ...] = ()
) -> str:
    try:
        completed = subprocess.run(
            [str(path)],
            executable=None if executable is None else str(executable),
            pass_fds=pass_fds,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise BgpdumpConfigurationError("无法探测 bgpdump 版本") from error
    combined = completed.stdout + b"\n" + completed.stderr
    text = combined.decode("utf-8", errors="replace")
    versions = set(_VERSION_RE.findall(text))
    if len(versions) != 1:
        raise BgpdumpConfigurationError("bgpdump usage 未给出唯一版本")
    return versions.pop()


def _hash_open_descriptor(descriptor: int) -> Tuple[str, os.stat_result]:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as error:
        raise BgpdumpConfigurationError("无法定位 bgpdump 二进制 fd") from error
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise BgpdumpConfigurationError("bgpdump 路径必须是普通文件")
    digest = hashlib.sha256()
    while True:
        block = os.read(descriptor, 1024 * 1024)
        if not block:
            break
        digest.update(block)
    after = os.fstat(descriptor)
    immutable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, name) != getattr(after, name) for name in immutable):
        raise BgpdumpConfigurationError("校验期间 bgpdump 二进制发生变化")
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest(), after


def _open_binary_descriptor(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(path, flags)
    except OSError as error:
        raise BgpdumpConfigurationError("无法只读打开 bgpdump 二进制") from error


def _hash_regular_binary(path: Path, *, require_executable: bool = True) -> str:
    descriptor = _open_binary_descriptor(path)
    try:
        digest, metadata = _hash_open_descriptor(descriptor)
        if require_executable and metadata.st_mode & 0o111 == 0:
            raise BgpdumpConfigurationError("bgpdump 二进制没有执行权限")
    finally:
        os.close(descriptor)
    return digest


def _verified_fd_exec_path(descriptor: int) -> Path:
    for root in (Path("/proc/self/fd"), Path("/dev/fd")):
        candidate = root / str(descriptor)
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    raise BgpdumpConfigurationError(
        "运行环境缺少 /proc/self/fd 或 /dev/fd，禁止退回路径二次解析执行"
    )


def _assert_no_symlink_path(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as error:
            raise BgpdumpConfigurationError("manifest 原始制品路径不可读") from error
        if stat.S_ISLNK(mode):
            raise BgpdumpConfigurationError("manifest 原始制品路径禁止符号链接")
    return current


class BgpdumpRecordStream:
    """单个 artifact 的一次性、可审计 ``ParsedMrtRecord`` 流。"""

    def __init__(
        self,
        *,
        path: Path,
        artifact: Mapping[str, Any],
        bgpdump_path: Path,
        expected_version: str,
        allowed_binary_sha256: Tuple[str, ...],
        popen_factory: Callable[..., Any],
        version_probe: Callable[..., str],
        queue_capacity: int,
        max_stdout_queue_source_bytes: int,
        max_frame_bytes: int,
        max_spool_bytes: int,
        max_stdout_line_bytes: int,
        max_stderr_bytes: int,
        exit_timeout_seconds: float,
        idle_timeout_seconds: float,
        max_physical_records: int,
        max_route_events: int,
        window_start_epoch_us: int,
        window_end_epoch_us: int,
        slot_start_epoch_us: int,
        slot_end_epoch_us: int,
        attested_binary_sha256: str,
    ) -> None:
        self._path = path
        self._artifact = dict(artifact)
        self._bgpdump_path = bgpdump_path
        self._expected_version = expected_version
        self._allowed_binary_sha256 = allowed_binary_sha256
        self._popen_factory = popen_factory
        self._version_probe = version_probe
        self._queue_capacity = queue_capacity
        self._max_stdout_queue_source_bytes = max_stdout_queue_source_bytes
        self._max_frame_bytes = max_frame_bytes
        self._max_spool_bytes = max_spool_bytes
        self._max_stdout_line_bytes = max_stdout_line_bytes
        self._max_stderr_bytes = max_stderr_bytes
        self._exit_timeout_seconds = exit_timeout_seconds
        self._idle_timeout_seconds = idle_timeout_seconds
        self._max_physical_records = max_physical_records
        self._max_route_events = max_route_events
        self._window_start_epoch_us = window_start_epoch_us
        self._window_end_epoch_us = window_end_epoch_us
        self._slot_start_epoch_us = slot_start_epoch_us
        self._slot_end_epoch_us = slot_end_epoch_us
        self._attested_binary_sha256 = attested_binary_sha256
        self._started = False
        self._statistics: Dict[str, Any] = {
            "status": "not_started",
            "artifact_id": self._artifact["artifact_id"],
            "physical_record_count": 0,
            "route_record_count": 0,
            "state_change_record_count": 0,
            "open_record_count": 0,
            "notification_record_count": 0,
            "keepalive_record_count": 0,
            "route_element_count": 0,
            "announce_count": 0,
            "withdraw_count": 0,
            "state_change_transitions": [],
            "record_hash_chain_sha256": None,
            "compressed_file_sha256": None,
            "compressed_size_bytes": None,
            "compressed_read_passes": 0,
            "peak_spool_bytes": 0,
            "spool_persistence": "anonymous_unlinked_fd",
            "stdout_queue_capacity": self._queue_capacity,
            "stdout_queue_source_bytes_limit": self._max_stdout_queue_source_bytes,
        }
        self._progress: Optional[_ProgressState] = None

    @property
    def statistics(self) -> Dict[str, Any]:
        result = copy.deepcopy(self._statistics)
        if self._progress is not None:
            result["compressed_bytes_read_observed"] = self._progress.snapshot()[
                "compressed_bytes_read"
            ]
        else:
            result["compressed_bytes_read_observed"] = 0
        return result

    def __iter__(self) -> Iterable[ParsedMrtRecord]:
        if self._started:
            raise BgpdumpAdapterError("同一 BgpdumpRecordStream 只能消费一次")
        self._started = True
        return self._iterate()

    def _validate_parser_binary(self) -> Tuple[str, int, Path, os.stat_result]:
        descriptor = _open_binary_descriptor(self._bgpdump_path)
        try:
            binary_hash, metadata = _hash_open_descriptor(descriptor)
            if metadata.st_mode & 0o111 == 0:
                raise BgpdumpConfigurationError("bgpdump 二进制没有执行权限")
            if binary_hash not in self._allowed_binary_sha256:
                raise BgpdumpConfigurationError("bgpdump 二进制 SHA256 不在显式 allowlist")
            if binary_hash != self._attested_binary_sha256:
                raise BgpdumpConfigurationError("bgpdump 实际二进制与 factory attestation 不一致")
            executable = _verified_fd_exec_path(descriptor)
            version = self._version_probe(
                self._bgpdump_path,
                executable=executable,
                pass_fds=(descriptor,),
            )
            if version != self._expected_version:
                raise BgpdumpConfigurationError("bgpdump 运行版本与显式期望不一致")
            return binary_hash, descriptor, executable, metadata
        except BaseException:
            os.close(descriptor)
            raise

    def _iterate(self) -> Iterable[ParsedMrtRecord]:
        process: Any = None
        binary_descriptor: Optional[int] = None
        binary_metadata: Optional[os.stat_result] = None
        threads: list[threading.Thread] = []
        cancel = threading.Event()
        failure = _FailureState()
        progress = _ProgressState()
        self._progress = progress
        outputs = _BoundedOutputQueue(
            max_items=self._queue_capacity,
            max_source_bytes=self._max_stdout_queue_source_bytes,
        )
        spool: Optional[_AnonymousFrameSpool] = None
        stderr_done = threading.Event()
        stderr_capture = bytearray()
        success = False
        transitions: Dict[Tuple[int, int], int] = {}
        hash_chain = hashlib.sha256()

        try:
            spool = _AnonymousFrameSpool(self._max_spool_bytes)
            failure.register_waker(spool.wake_waiters)
            (
                binary_hash,
                binary_descriptor,
                executable,
                binary_metadata,
            ) = self._validate_parser_binary()
            self._statistics["status"] = "running"
            self._statistics["parser_version"] = self._expected_version
            self._statistics["parser_binary_sha256"] = binary_hash
            try:
                process = self._popen_factory(
                    [str(self._bgpdump_path), "-m", "-p", "-v", "/dev/stdin"],
                    executable=str(executable),
                    pass_fds=(binary_descriptor,),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise BgpdumpConfigurationError("无法启动获准 bgpdump 二进制") from error
            if process.stdin is None or process.stdout is None or process.stderr is None:
                raise BgpdumpConfigurationError("bgpdump 子进程未提供完整 stdin/stdout/stderr")

            producer = threading.Thread(
                target=_producer_worker,
                kwargs={
                    "path": self._path,
                    "expected_file_sha256": self._artifact["file_sha256"],
                    "expected_size_bytes": self._artifact["size_bytes"],
                    "stdin": process.stdin,
                    "spool": spool,
                    "cancel": cancel,
                    "failure": failure,
                    "progress": progress,
                    "max_frame_bytes": self._max_frame_bytes,
                    "max_physical_records": self._max_physical_records,
                    "window_start_epoch_us": self._window_start_epoch_us,
                    "window_end_epoch_us": self._window_end_epoch_us,
                    "slot_start_epoch_us": self._slot_start_epoch_us,
                    "slot_end_epoch_us": self._slot_end_epoch_us,
                },
                name="p0-bgpdump-producer",
                daemon=True,
            )
            stdout_reader = threading.Thread(
                target=_stdout_worker,
                kwargs={
                    "stdout": process.stdout,
                    "outputs": outputs,
                    "cancel": cancel,
                    "failure": failure,
                    "progress": progress,
                    "max_stdout_line_bytes": self._max_stdout_line_bytes,
                    "max_output_lines": (
                        self._max_route_events + self._max_physical_records
                    ),
                },
                name="p0-bgpdump-stdout",
                daemon=True,
            )
            stderr_reader = threading.Thread(
                target=_stderr_worker,
                kwargs={
                    "stderr": process.stderr,
                    "cancel": cancel,
                    "failure": failure,
                    "progress": progress,
                    "captured": stderr_capture,
                    "max_stderr_bytes": self._max_stderr_bytes,
                    "done": stderr_done,
                },
                name="p0-bgpdump-stderr",
                daemon=True,
            )
            threads = [producer, stdout_reader, stderr_reader]
            for thread in threads:
                thread.start()

            def runtime_snapshot() -> Dict[str, Any]:
                assert spool is not None
                output_snapshot = outputs.snapshot()
                return {
                    "process_returncode": process.poll(),
                    "spool": spool.snapshot(),
                    "outputs_queue_size": output_snapshot["current_items"],
                    "outputs_queue_source_bytes": output_snapshot[
                        "current_source_bytes"
                    ],
                    "outputs_queue": output_snapshot,
                    "producer_alive": producer.is_alive(),
                    "stdout_alive": stdout_reader.is_alive(),
                    "stderr_alive": stderr_reader.is_alive(),
                    "stderr_done": stderr_done.is_set(),
                }

            pending_outputs: "deque[Any]" = deque()

            def next_output(wait_stage: str) -> Any:
                if pending_outputs:
                    return pending_outputs.popleft()
                batch_items = _get_many_bounded(
                    outputs,
                    failure=failure,
                    cancel=cancel,
                    progress=progress,
                    idle_timeout_seconds=self._idle_timeout_seconds,
                    wait_stage=wait_stage,
                    runtime_snapshot=runtime_snapshot,
                )
                if not batch_items:
                    raise BgpdumpAdapterError("内部 stdout batch 为空")
                pending_outputs.extend(batch_items[1:])
                first = batch_items[0]
                progress.touch(
                    "stdout_batch_dequeued",
                    batch_items=len(batch_items),
                    first_type=type(first).__name__,
                    pending_items=len(pending_outputs),
                )
                return first

            producer_done: Optional[_ProducerDone] = None
            stdout_done: Optional[_StdoutDone] = None
            expected_stdout_group_count = 0
            spool_offset = 0
            expected_ordinal = 0
            expected_raw_offset = 0
            while producer_done is None:
                producer_item, raw_record, next_spool_offset = spool.read_next(
                    spool_offset=spool_offset,
                    expected_ordinal=expected_ordinal,
                    expected_raw_offset=expected_raw_offset,
                    max_frame_bytes=self._max_frame_bytes,
                    failure=failure,
                    cancel=cancel,
                    progress=progress,
                    idle_timeout_seconds=self._idle_timeout_seconds,
                    runtime_snapshot=runtime_snapshot,
                )
                if producer_item is not None:
                    producer_done = producer_item
                    progress.touch(
                        "producer_done_dequeued",
                        record_count=producer_done.record_count,
                    )
                    output_item = next_output("stdout_done")
                    if not isinstance(output_item, _StdoutDone):
                        raise BgpdumpOutputError(
                            "bgpdump 输出行多于 raw UPDATE/STATE shape 或 physical frame"
                        )
                    stdout_done = output_item
                    break
                if raw_record is None:
                    raise BgpdumpAdapterError("spool 未返回 frame 或 producer done")
                frame_item = _frame_from_raw_record(
                    raw_record,
                    ordinal=expected_ordinal,
                    offset=expected_raw_offset,
                    max_frame_bytes=self._max_frame_bytes,
                    window_start_epoch_us=self._window_start_epoch_us,
                    window_end_epoch_us=self._window_end_epoch_us,
                    slot_start_epoch_us=self._slot_start_epoch_us,
                    slot_end_epoch_us=self._slot_end_epoch_us,
                )
                spool_offset = next_spool_offset
                expected_ordinal += 1
                expected_raw_offset += len(raw_record)
                progress.touch(
                    "frame_spool_dequeued",
                    ordinal=frame_item.ordinal,
                    spool_offset=spool_offset,
                )
                if frame_item.mrt_subtype in _STATE_CHANGE_SUBTYPES:
                    expected_output_line_count = 1
                elif frame_item.update_shape is not None:
                    expected_output_line_count = sum(
                        count for _family, count in frame_item.update_shape.announce_counts
                    ) + sum(
                        count for _family, count in frame_item.update_shape.withdraw_counts
                    )
                elif frame_item.bgp_message_type in _SILENT_CONTROL_MESSAGE_TYPES:
                    expected_output_line_count = 0
                else:
                    raise BgpdumpOutputError("physical frame 缺少可验证的 stdout shape")
                if (
                    expected_output_line_count == 0
                    and frame_item.bgp_message_type not in _SILENT_CONTROL_MESSAGE_TYPES
                ):
                    raise BgpdumpOutputError(
                        "存在无 bgpdump -p 输出的 physical record；raw UPDATE 没有可提升路由元素"
                    )
                if expected_output_line_count > 0:
                    expected_stdout_group_count += 1
                if (
                    frame_item.update_shape is not None
                    and self._statistics["route_element_count"]
                    + expected_output_line_count
                    > self._max_route_events
                ):
                    raise BgpdumpOutputError("UPDATE pilot 超过 route event 硬上限")
                elements_list: list[ParsedRouteElement] = []
                kinds: set[str] = set()
                observed_announce: Counter[str] = Counter()
                observed_withdraw: Counter[str] = Counter()
                route_announce_count = 0
                route_withdraw_count = 0
                state_line: Optional[_ParsedLine] = None
                for _line_index in range(expected_output_line_count):
                    output_item = next_output("stdout_line")
                    if isinstance(output_item, _StdoutDone):
                        raise BgpdumpOutputError(
                            "physical record 的 bgpdump -p stdout 在满足 raw UPDATE/STATE shape 前结束"
                        )
                    if not isinstance(output_item, _ParsedLine):
                        raise BgpdumpAdapterError("内部 stdout 队列类型非法")
                    if frame_item.ordinal != output_item.ordinal:
                        raise BgpdumpOutputError(
                            "bgpdump -p ordinal 与 Python MRT framer/raw shape 不一致"
                        )
                    if (
                        output_item.epoch_seconds != frame_item.mrt_timestamp
                        or output_item.microseconds != frame_item.microseconds
                    ):
                        raise BgpdumpOutputError(
                            "bgpdump 行时间与 MRT common/extended header 不一致"
                        )
                    kinds.add(output_item.kind)
                    if output_item.kind == "STATE":
                        state_line = output_item
                    else:
                        element = output_item.element
                        if element is None:
                            raise BgpdumpOutputError(
                                "UPDATE group 含无法生成的 route element"
                            )
                        elements_list.append(element)
                        if output_item.kind == "A":
                            observed_announce[element.afi_safi] += 1
                            route_announce_count += 1
                        elif output_item.kind == "W":
                            observed_withdraw[element.afi_safi] += 1
                            route_withdraw_count += 1
                if expected_output_line_count == 0:
                    if (
                        frame_item.bgp_message_type not in _SILENT_CONTROL_MESSAGE_TYPES
                        or frame_item.update_shape is not None
                        or kinds
                    ):
                        raise BgpdumpOutputError("无输出 physical record 不是合法控制消息")
                    elements = ()
                    counter_by_type = {
                        _BGP_MESSAGE_OPEN: "open_record_count",
                        _BGP_MESSAGE_NOTIFICATION: "notification_record_count",
                        _BGP_MESSAGE_KEEPALIVE: "keepalive_record_count",
                    }
                    self._statistics[counter_by_type[frame_item.bgp_message_type]] += 1
                elif kinds == {"STATE"}:
                    if frame_item.mrt_subtype not in _STATE_CHANGE_SUBTYPES:
                        raise BgpdumpOutputError(
                            "STATE 行与 MRT BGP4MP subtype 不一致"
                        )
                    if expected_output_line_count != 1:
                        raise BgpdumpOutputError("一个 STATE_CHANGE frame 只能有一行")
                    if state_line is None:
                        raise BgpdumpOutputError("STATE_CHANGE frame 缺少 STATE 行")
                    line = state_line
                    assert line.old_state is not None and line.new_state is not None
                    transitions[(line.old_state, line.new_state)] = (
                        transitions.get((line.old_state, line.new_state), 0) + 1
                    )
                    elements: Tuple[ParsedRouteElement, ...] = ()
                    self._statistics["state_change_record_count"] += 1
                elif kinds.issubset({"A", "W"}) and kinds:
                    if frame_item.mrt_subtype not in _UPDATE_SUBTYPES:
                        raise BgpdumpOutputError(
                            "announce/withdraw 行与 MRT BGP4MP subtype 不一致"
                        )
                    elements = tuple(elements_list)
                    if len(elements) != expected_output_line_count:
                        raise BgpdumpOutputError("UPDATE group 含无法生成的 route element")
                    if frame_item.update_shape is None:
                        raise BgpdumpOutputError("UPDATE frame 缺少 raw BGP shape 验证")
                    if (
                        tuple(sorted(observed_announce.items()))
                        != frame_item.update_shape.announce_counts
                        or tuple(sorted(observed_withdraw.items()))
                        != frame_item.update_shape.withdraw_counts
                    ):
                        raise BgpdumpOutputError(
                            "bgpdump route element 数/AFI/SAFI 与 raw UPDATE NLRI 不一致"
                        )
                    if (
                        self._statistics["route_element_count"] + len(elements)
                        > self._max_route_events
                    ):
                        raise BgpdumpOutputError("UPDATE pilot 超过 route event 硬上限")
                    self._statistics["route_record_count"] += 1
                    self._statistics["route_element_count"] += len(elements)
                    self._statistics["announce_count"] += route_announce_count
                    self._statistics["withdraw_count"] += route_withdraw_count
                else:
                    raise BgpdumpOutputError(
                        "同一 physical record 混合 STATE 与路由元素"
                    )

                if hashlib.sha256(frame_item.raw_record).hexdigest() != frame_item.record_sha256:
                    raise BgpdumpIntegrityError("内存中 MRT record hash 校验失败")
                hash_chain.update(
                    struct.pack(
                        "!QQQ",
                        frame_item.ordinal,
                        frame_item.offset,
                        len(frame_item.raw_record),
                    )
                )
                hash_chain.update(bytes.fromhex(frame_item.record_sha256))
                self._statistics["physical_record_count"] += 1
                yield ParsedMrtRecord(
                    record_ordinal=frame_item.ordinal,
                    record_offset=frame_item.offset,
                    raw_record=frame_item.raw_record,
                    elements=elements,
                )

            assert producer_done is not None and stdout_done is not None
            if expected_stdout_group_count != stdout_done.group_count:
                raise BgpdumpOutputError(
                    "预期有输出的 physical record 数与 bgpdump ordinal group 数不一致"
                )
            if producer_done.record_count != self._statistics["physical_record_count"]:
                raise BgpdumpOutputError("适配器消费的 physical record 数不完整")
            classified_record_count = sum(
                self._statistics[field]
                for field in (
                    "route_record_count",
                    "state_change_record_count",
                    "open_record_count",
                    "notification_record_count",
                    "keepalive_record_count",
                )
            )
            if classified_record_count != self._statistics["physical_record_count"]:
                raise BgpdumpOutputError("physical record 分类计数不闭合")

            try:
                return_code = process.wait(timeout=self._exit_timeout_seconds)
            except subprocess.TimeoutExpired as error:
                raise BgpdumpOutputError("bgpdump 在 stdin EOF 后未按时退出") from error
            if not stderr_done.wait(timeout=self._exit_timeout_seconds):
                raise BgpdumpOutputError("bgpdump stderr 消费线程未按时结束")
            error = failure.get()
            if error is not None:
                raise error
            if return_code != 0:
                raise BgpdumpOutputError(f"bgpdump 非零退出：{return_code}")
            if stderr_capture:
                raise BgpdumpOutputError("bgpdump stderr 非空")
            assert binary_descriptor is not None and binary_metadata is not None
            final_binary_hash, final_binary_metadata = _hash_open_descriptor(
                binary_descriptor
            )
            immutable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if final_binary_hash != binary_hash or any(
                getattr(binary_metadata, name) != getattr(final_binary_metadata, name)
                for name in immutable
            ):
                raise BgpdumpConfigurationError("解析期间已固定 bgpdump inode 发生变化")

            self._statistics["compressed_file_sha256"] = producer_done.compressed_sha256
            self._statistics["compressed_size_bytes"] = producer_done.compressed_size_bytes
            self._statistics["compressed_read_passes"] = 1
            self._statistics["peak_spool_bytes"] = spool.snapshot()[
                "committed_spool_bytes"
            ]
            self._statistics["record_hash_chain_sha256"] = hash_chain.hexdigest()
            self._statistics["state_change_transitions"] = [
                {"old_state": old, "new_state": new, "count": count}
                for (old, new), count in sorted(transitions.items())
            ]
            self._statistics["status"] = "complete"
            success = True
        except GeneratorExit:
            raise
        except BaseException as error:
            self._statistics["status"] = "failed"
            self._statistics["failure_type"] = type(error).__name__
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise _adapter_error(error, "bgpdump UPDATE 适配失败") from error
        finally:
            cancel.set()
            if spool is not None:
                spool.wake_waiters()
                self._statistics["peak_spool_bytes"] = spool.snapshot()[
                    "committed_spool_bytes"
                ]
            if process is not None:
                try:
                    if process.stdin is not None:
                        process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
                try:
                    running = process.poll() is None
                except (AttributeError, OSError):
                    running = not success
                if running:
                    try:
                        process.terminate()
                        process.wait(timeout=min(self._exit_timeout_seconds, 5.0))
                    except BaseException:
                        try:
                            process.kill()
                        except BaseException:
                            pass
                for stream_name in ("stdout", "stderr"):
                    stream = getattr(process, stream_name, None)
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
            for thread in threads:
                thread.join(timeout=5.0)
            if spool is not None:
                spool.close()
            if binary_descriptor is not None:
                os.close(binary_descriptor)


class BgpdumpRecordStreamFactory:
    """把已验证 UPDATE pilot selection 映射成可注入的流工厂。"""

    def __init__(
        self,
        raw_root: os.PathLike[str] | str,
        artifacts: Sequence[Mapping[str, Any]],
        *,
        data_profile: Mapping[str, Any],
        pilot_limits: Mapping[str, Any],
        bgpdump_path: os.PathLike[str] | str,
        expected_version: str,
        allowed_binary_sha256: Sequence[str],
        popen_factory: Callable[..., Any] = subprocess.Popen,
        version_probe: Callable[..., str] = _default_version_probe,
        queue_capacity: int = 4,
        max_stdout_queue_source_bytes: int = 8 * 1024 * 1024,
        max_frame_bytes: int = 64 * 1024 * 1024,
        max_stdout_line_bytes: int = 65_536,
        max_stderr_bytes: int = 1024 * 1024,
        exit_timeout_seconds: float = 30.0,
        idle_timeout_seconds: float = 30.0,
    ) -> None:
        self._raw_root = Path(raw_root)
        try:
            root_mode = self._raw_root.lstat().st_mode
        except OSError as error:
            raise BgpdumpConfigurationError("raw_root 不可读") from error
        if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
            raise BgpdumpConfigurationError("raw_root 必须是非符号链接目录")
        self._bgpdump_path = Path(bgpdump_path)
        if expected_version != BGPDUMP_APPROVED_VERSION:
            raise BgpdumpConfigurationError("P0 原型只冻结 bgpdump 1.6.2")
        if isinstance(allowed_binary_sha256, (str, bytes)):
            raise BgpdumpConfigurationError("allowed_binary_sha256 必须是显式序列")
        hashes = tuple(allowed_binary_sha256)
        if not hashes or any(
            not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None
            for value in hashes
        ):
            raise BgpdumpConfigurationError("bgpdump binary SHA256 allowlist 非法或为空")
        if len(set(hashes)) != len(hashes):
            raise BgpdumpConfigurationError("bgpdump binary SHA256 allowlist 不得重复")
        attested_binary_sha256 = _hash_regular_binary(self._bgpdump_path)
        if attested_binary_sha256 not in hashes:
            raise BgpdumpConfigurationError("bgpdump 二进制 SHA256 不在显式 allowlist")
        for name, value, minimum in (
            ("queue_capacity", queue_capacity, 1),
            ("max_frame_bytes", max_frame_bytes, _MRT_HEADER_LENGTH),
            ("max_stdout_line_bytes", max_stdout_line_bytes, 128),
            ("max_stderr_bytes", max_stderr_bytes, 1),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise BgpdumpConfigurationError(f"{name} 不符合显式下限")
        if queue_capacity > BGPDUMP_ABSOLUTE_MAX_STDOUT_QUEUE_CAPACITY:
            raise BgpdumpConfigurationError("queue_capacity 超过 stdout 队列绝对硬上限")
        if (
            isinstance(max_stdout_queue_source_bytes, bool)
            or not isinstance(max_stdout_queue_source_bytes, int)
            or max_stdout_queue_source_bytes < max_stdout_line_bytes + 1
            or max_stdout_queue_source_bytes
            > BGPDUMP_ABSOLUTE_MAX_STDOUT_QUEUE_SOURCE_BYTES
        ):
            raise BgpdumpConfigurationError(
                "max_stdout_queue_source_bytes 非法或超过绝对硬上限"
            )
        if (
            isinstance(exit_timeout_seconds, bool)
            or not isinstance(exit_timeout_seconds, (int, float))
            or exit_timeout_seconds <= 0
        ):
            raise BgpdumpConfigurationError("exit_timeout_seconds 必须为正数")
        if (
            isinstance(idle_timeout_seconds, bool)
            or not isinstance(idle_timeout_seconds, (int, float))
            or idle_timeout_seconds <= 0
        ):
            raise BgpdumpConfigurationError("idle_timeout_seconds 必须为正数")
        if not callable(popen_factory) or not callable(version_probe):
            raise BgpdumpConfigurationError("子进程与版本探针必须可调用")

        if not isinstance(data_profile, Mapping):
            raise BgpdumpConfigurationError("data_profile 必须来自已验证 selection")
        window_start_epoch_us = _strict_utc_epoch_us(
            data_profile.get("window_start_utc"), "data_profile.window_start_utc"
        )
        window_end_epoch_us = _strict_utc_epoch_us(
            data_profile.get("window_end_exclusive_utc"),
            "data_profile.window_end_exclusive_utc",
        )
        if window_start_epoch_us >= window_end_epoch_us:
            raise BgpdumpConfigurationError("data_profile UTC 窗口非法")
        if not isinstance(pilot_limits, Mapping):
            raise BgpdumpConfigurationError("pilot_limits 必须来自已验证 selection")
        limit_maximums = {
            "max_artifact_count": PILOT_ABSOLUTE_MAX_ARTIFACTS,
            "max_compressed_bytes": PILOT_ABSOLUTE_MAX_COMPRESSED_BYTES,
            "max_physical_records": PILOT_ABSOLUTE_MAX_PHYSICAL_RECORDS,
            "max_route_events": PILOT_ABSOLUTE_MAX_ROUTE_EVENTS,
            "max_spool_bytes": PILOT_ABSOLUTE_MAX_SPOOL_BYTES,
        }
        normalized_limits: Dict[str, int] = {}
        for name, absolute_maximum in limit_maximums.items():
            value = pilot_limits.get(name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
                or value > absolute_maximum
            ):
                raise BgpdumpConfigurationError(f"pilot_limits.{name} 非法")
            normalized_limits[name] = value

        if isinstance(artifacts, (str, bytes)) or not isinstance(artifacts, Sequence):
            raise BgpdumpConfigurationError(
                "artifacts 必须是 selection.selected_artifacts 序列"
            )
        if not artifacts or len(artifacts) > normalized_limits["max_artifact_count"]:
            raise BgpdumpConfigurationError("artifacts 为空或超过 pilot artifact 硬上限")
        artifact_map: Dict[str, Dict[str, Any]] = {}
        path_map: Dict[str, PurePosixPath] = {}
        for artifact in artifacts:
            if not isinstance(artifact, Mapping):
                raise BgpdumpConfigurationError("manifest artifact 必须是对象")
            artifact_id = artifact.get("artifact_id")
            file_hash = artifact.get("file_sha256")
            if (
                not isinstance(file_hash, str)
                or _SHA256_RE.fullmatch(file_hash) is None
                or artifact_id != artifact_id_v1(file_hash)
            ):
                raise BgpdumpConfigurationError("artifact ID/SHA256 不一致")
            if artifact_id in artifact_map:
                raise BgpdumpConfigurationError("artifact_id 重复")
            collector = artifact.get("collector_id")
            if not isinstance(collector, str) or not collector:
                raise BgpdumpConfigurationError("artifact collector_id 非法")
            artifact_type = artifact.get("artifact_type")
            if artifact_type not in {"update", "rib"}:
                raise BgpdumpConfigurationError("artifact_type 仅允许 update/rib")
            if artifact.get("compression") != "gz":
                raise BgpdumpConfigurationError("bgpdump P0 适配器只接受 gzip 制品")
            size_bytes = artifact.get("size_bytes")
            if (
                isinstance(size_bytes, bool)
                or not isinstance(size_bytes, int)
                or size_bytes <= 0
            ):
                raise BgpdumpConfigurationError("artifact size_bytes 非法")
            if artifact_type != "update":
                raise BgpdumpConfigurationError("bgpdump pilot factory 只接受 selection UPDATE")
            artifact_time = artifact.get("artifact_time_utc")
            slot_start_epoch_us = _strict_utc_epoch_us(
                artifact_time, "artifact.artifact_time_utc"
            )
            if slot_start_epoch_us % (300 * 1_000_000):
                raise BgpdumpConfigurationError("UPDATE artifact 时间未对齐五分钟槽")
            relative_value = artifact.get("relative_path")
            if not isinstance(relative_value, str):
                raise BgpdumpConfigurationError("artifact relative_path 非法")
            relative = PurePosixPath(relative_value)
            if (
                relative.is_absolute()
                or not relative.parts
                or ".." in relative.parts
                or relative.parts[0] != collector
            ):
                raise BgpdumpConfigurationError("artifact relative_path 越出 collector")
            artifact_map[artifact_id] = dict(artifact)
            path_map[artifact_id] = relative

        if sum(row["size_bytes"] for row in artifact_map.values()) > normalized_limits[
            "max_compressed_bytes"
        ]:
            raise BgpdumpConfigurationError("selection 压缩字节超过 pilot 硬上限")

        self._artifacts = artifact_map
        self._paths = path_map
        self._expected_version = expected_version
        self._allowed_binary_sha256 = hashes
        self._popen_factory = popen_factory
        self._version_probe = version_probe
        self._queue_capacity = queue_capacity
        self._max_stdout_queue_source_bytes = max_stdout_queue_source_bytes
        self._max_frame_bytes = max_frame_bytes
        self._max_spool_bytes = normalized_limits["max_spool_bytes"]
        self._max_stdout_line_bytes = max_stdout_line_bytes
        self._max_stderr_bytes = max_stderr_bytes
        self._exit_timeout_seconds = float(exit_timeout_seconds)
        self._idle_timeout_seconds = float(idle_timeout_seconds)
        self._window_start_epoch_us = window_start_epoch_us
        self._window_end_epoch_us = window_end_epoch_us
        self._pilot_limits = normalized_limits
        self._attested_binary_sha256 = attested_binary_sha256
        self._streams: Dict[str, BgpdumpRecordStream] = {}

        adapter_source_sha256 = _hash_regular_binary(
            Path(__file__), require_executable=False
        )
        config = {
            "command_arguments": ["-m", "-p", "-v", "/dev/stdin"],
            "queue_capacity": queue_capacity,
            "max_stdout_queue_source_bytes": max_stdout_queue_source_bytes,
            "stdout_queue_retained_heap_upper_bound_bytes": (
                max_stdout_queue_source_bytes
                * _PARSED_LINE_MEMORY_EXPANSION_FACTOR
                + queue_capacity * _PARSED_LINE_FIXED_OVERHEAD_BYTES
            ),
            "stdout_read_chunk_bytes": _STDOUT_READ_CHUNK_BYTES,
            "stdout_batch_max_lines": _STDOUT_BATCH_MAX_LINES,
            "line_parse_cache_max_entries": _LINE_PARSE_CACHE_MAX_ENTRIES,
            "line_parse_cache_max_key_bytes": _LINE_PARSE_CACHE_MAX_KEY_BYTES,
            "line_parse_cache_retained_heap_upper_bound_bytes": (
                _LINE_PARSE_CACHE_MAX_KEY_BYTES
                * _PARSED_LINE_MEMORY_EXPANSION_FACTOR
                + _LINE_PARSE_CACHE_MAX_ENTRIES
                * _PARSED_LINE_FIXED_OVERHEAD_BYTES
            ),
            "max_frame_bytes": max_frame_bytes,
            "max_spool_bytes": self._max_spool_bytes,
            "max_stdout_line_bytes": max_stdout_line_bytes,
            "max_stderr_bytes": max_stderr_bytes,
            "exit_timeout_seconds": float(exit_timeout_seconds),
            "idle_timeout_seconds": float(idle_timeout_seconds),
            "window_start_utc": data_profile.get("window_start_utc"),
            "window_end_exclusive_utc": data_profile.get(
                "window_end_exclusive_utc"
            ),
            "pilot_limits": normalized_limits,
            "binary_execution_policy": "verified_open_fd_exec",
        }
        attestation: Dict[str, Any] = {
            "schema_version": "parser_attestation_v1",
            "parser_name": "bgpdump",
            "parser_version": expected_version,
            "parser_binary_sha256": attested_binary_sha256,
            "adapter_name": "domeye_bgpdump_adapter",
            "adapter_version": "1.2.0",
            "adapter_source_sha256": adapter_source_sha256,
            "binary_execution_policy": "verified_open_fd_exec",
            "configuration": copy.deepcopy(config),
            "configuration_sha256": hashlib.sha256(
                canonical_json(config).encode("utf-8")
            ).hexdigest(),
            "pilot_limits": copy.deepcopy(normalized_limits),
            "security_boundary": (
                "通过 /proc/self/fd 或 /dev/fd 执行已打开并校验的 inode；"
                "不退回再次解析原始路径；解压 frame 只进入"
                "已 unlink、0600 权限且受 selection.max_spool_bytes 限制的匿名 fd"
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
        self._parser_attestation = attestation

    @property
    def statistics_by_artifact(self) -> Dict[str, Dict[str, Any]]:
        return {
            artifact_id: stream.statistics
            for artifact_id, stream in sorted(self._streams.items())
        }

    @property
    def parser_attestation(self) -> Dict[str, Any]:
        return copy.deepcopy(self._parser_attestation)

    def __call__(self, artifact: Mapping[str, Any]) -> BgpdumpRecordStream:
        if not isinstance(artifact, Mapping):
            raise BgpdumpConfigurationError("RouteEvent index artifact 必须是对象")
        artifact_id = artifact.get("artifact_id")
        full = self._artifacts.get(artifact_id)
        if full is None:
            raise BgpdumpConfigurationError("RouteEvent index artifact 不在工厂 manifest")
        for field in (
            "file_sha256",
            "collector_id",
            "artifact_type",
            "artifact_time_utc",
            "relative_path",
            "compression",
            "size_bytes",
        ):
            if artifact.get(field) != full.get(field):
                raise BgpdumpConfigurationError(f"RouteEvent index artifact {field} 不一致")
        if full["artifact_type"] == "rib":
            raise BgpdumpConfigurationError(
                "P0 bgpdump 适配器未验收 RIB ordinal/属性保真，固定失败关闭"
            )
        if artifact_id in self._streams:
            raise BgpdumpConfigurationError("同一 artifact 在本工厂中不得重复解析")
        completed = [
            stream.statistics
            for stream in self._streams.values()
            if stream.statistics.get("status") == "complete"
        ]
        remaining_records = self._pilot_limits["max_physical_records"] - sum(
            row["physical_record_count"] for row in completed
        )
        remaining_events = self._pilot_limits["max_route_events"] - sum(
            row["route_element_count"] for row in completed
        )
        if remaining_records <= 0 or remaining_events <= 0:
            raise BgpdumpConfigurationError("UPDATE pilot 全局 record/event 硬上限已耗尽")
        path = _assert_no_symlink_path(self._raw_root, self._paths[artifact_id])
        slot_start_epoch_us = _strict_utc_epoch_us(
            full["artifact_time_utc"], "artifact.artifact_time_utc"
        )
        stream = BgpdumpRecordStream(
            path=path,
            artifact=full,
            bgpdump_path=self._bgpdump_path,
            expected_version=self._expected_version,
            allowed_binary_sha256=self._allowed_binary_sha256,
            popen_factory=self._popen_factory,
            version_probe=self._version_probe,
            queue_capacity=self._queue_capacity,
            max_stdout_queue_source_bytes=self._max_stdout_queue_source_bytes,
            max_frame_bytes=self._max_frame_bytes,
            max_spool_bytes=self._max_spool_bytes,
            max_stdout_line_bytes=self._max_stdout_line_bytes,
            max_stderr_bytes=self._max_stderr_bytes,
            exit_timeout_seconds=self._exit_timeout_seconds,
            idle_timeout_seconds=self._idle_timeout_seconds,
            max_physical_records=remaining_records,
            max_route_events=remaining_events,
            window_start_epoch_us=self._window_start_epoch_us,
            window_end_epoch_us=self._window_end_epoch_us,
            slot_start_epoch_us=slot_start_epoch_us,
            slot_end_epoch_us=slot_start_epoch_us + 300 * 1_000_000,
            attested_binary_sha256=self._attested_binary_sha256,
        )
        self._streams[artifact_id] = stream
        return stream


def make_bgpdump_record_stream_factory(
    raw_root: os.PathLike[str] | str,
    artifacts: Sequence[Mapping[str, Any]],
    **options: Any,
) -> BgpdumpRecordStreamFactory:
    """构造可直接注入 ``build_route_event_index`` 的 UPDATE 流工厂。"""

    return BgpdumpRecordStreamFactory(raw_root, artifacts, **options)


__all__ = (
    "BGPDUMP_APPROVED_VERSION",
    "BgpdumpAdapterError",
    "BgpdumpConfigurationError",
    "BgpdumpIntegrityError",
    "BgpdumpOutputError",
    "BgpdumpRecordStream",
    "BgpdumpRecordStreamFactory",
    "make_bgpdump_record_stream_factory",
)
