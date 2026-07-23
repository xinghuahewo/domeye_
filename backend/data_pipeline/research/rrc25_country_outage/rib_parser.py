"""研究闭环使用的 RFC 6396 RIB MRT 小样本解析器。

本模块解析解压后的 MRT 字节流，并提供由调用方显式绑定 hash/size 的 gzip
制品到不可覆盖解压 spool 的底层能力；不写数据库，也不改变生产
``BgpdumpRecordStreamFactory`` 对 RIB 的失败关闭边界。输出复用 RouteEvent 的
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
import gzip
import hashlib
import io
import ipaddress
import os
from pathlib import Path
import re
import secrets
import stat
import struct
from typing import Any, BinaryIO, Iterator, Mapping, Optional, Tuple, Union
import zlib

from ...route_event.index import AsPathSegment, ParsedMrtRecord, ParsedRouteElement


MRT_HEADER_LENGTH = 12
MAX_MRT_PAYLOAD_BYTES = 64 * 1024 * 1024
RIB_DECOMPRESSED_SPOOL_SCHEMA_VERSION = "rrc25-seed-decompressed-spool/v1"
RIB_PEER_INDEX_CONTEXT_SCHEMA_VERSION = "rrc25-rib-peer-index-context/v1"
_SPOOL_COPY_BLOCK_BYTES = 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

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


class RibSpoolError(RibMrtParseError):
    """RIB 解压 spool 无法按不可变身份与资源边界安全处理。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class RibMrtSeekError(RibMrtParseError):
    """RIB MRT 恢复坐标无法从解压 spool 严格验证。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class RibSpoolIdentity:
    """一份已发布并核验的解压 RIB spool 身份。"""

    path: Path
    decompressed_sha256: str
    decompressed_size_bytes: int

    @property
    def schema_version(self) -> str:
        return RIB_DECOMPRESSED_SPOOL_SCHEMA_VERSION

    @property
    def file_name(self) -> str:
        return self.path.name

    @property
    def sha256(self) -> str:
        return self.decompressed_sha256

    @property
    def size_bytes(self) -> int:
        return self.decompressed_size_bytes

    def checkpoint_binding(self) -> dict[str, object]:
        """生成 checkpoint 中 basename-only 的严格 spool 绑定。"""

        return {
            "schema_version": self.schema_version,
            "file_name": self.file_name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class RibSpoolBuildResult:
    """压缩源与其不可变解压 spool 的双重身份。"""

    spool: RibSpoolIdentity
    compressed_sha256: str
    compressed_size_bytes: int

    @property
    def path(self) -> Path:
        return self.spool.path

    @property
    def sha256(self) -> str:
        return self.spool.sha256

    @property
    def size_bytes(self) -> int:
        return self.spool.size_bytes

    def checkpoint_binding(self) -> dict[str, object]:
        return self.spool.checkpoint_binding()


@dataclass(frozen=True)
class RibRecordBoundary:
    """checkpoint 持久化的上一完整 MRT physical-record 身份。"""

    record_ordinal: int
    record_offset: int
    record_length: int
    record_sha256: str

    def checkpoint_binding(self) -> dict[str, object]:
        return {
            "record_ordinal": self.record_ordinal,
            "record_offset": self.record_offset,
            "record_length": self.record_length,
            "record_sha256": self.record_sha256,
        }


@dataclass(frozen=True)
class RibPeerIndexContext:
    """恢复点适用且可直接回读核验的 TABLE_DUMP_V2 peer context。"""

    record_ordinal: int
    record_offset: int
    record_length: int
    record_sha256: str
    peers: Tuple[Tuple[str, int], ...]

    def checkpoint_binding(self) -> dict[str, object]:
        return {
            "schema_version": RIB_PEER_INDEX_CONTEXT_SCHEMA_VERSION,
            "record_ordinal": self.record_ordinal,
            "record_offset": self.record_offset,
            "record_length": self.record_length,
            "record_sha256": self.record_sha256,
            "peers": [
                {"peer_ip": peer_ip, "peer_asn": peer_asn}
                for peer_ip, peer_asn in self.peers
            ],
        }


@dataclass(frozen=True)
class RibMrtSeekContext:
    """通过直接边界回读确认的下一 physical record 坐标。"""

    next_record_ordinal: int
    next_record_offset: int
    previous_record_boundary: Optional[RibRecordBoundary]
    peer_index: Optional[RibPeerIndexContext]


@dataclass(frozen=True)
class _Peer:
    peer_ip: str
    peer_asn: int


class _DigestingReader:
    """给 gzip 解码器提供受压缩制品声明大小约束的只读 reader。"""

    def __init__(self, stream: BinaryIO, expected_size_bytes: int) -> None:
        self._stream = stream
        self._expected_size_bytes = expected_size_bytes
        self._digest = hashlib.sha256()
        self.size_bytes = 0

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()

    def read(self, size: int = -1) -> bytes:
        value = self._stream.read(size)
        if not isinstance(value, (bytes, bytearray)):
            raise RibSpoolError(
                "compressed_reader_contract",
                "压缩源 reader.read() 必须返回 bytes",
            )
        raw = bytes(value)
        if size >= 0 and len(raw) > size:
            raise RibSpoolError(
                "compressed_reader_contract",
                "压缩源 reader.read(size) 返回超过请求长度",
            )
        next_size = self.size_bytes + len(raw)
        if next_size > self._expected_size_bytes:
            raise RibSpoolError(
                "compressed_size_mismatch",
                "压缩源读取量超过 expected_compressed_size_bytes",
            )
        self._digest.update(raw)
        self.size_bytes = next_size
        return raw


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise RibSpoolError("invalid_argument", f"{field} 必须是 64 位小写 SHA256")
    return value


def _nonnegative_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RibSpoolError("invalid_argument", f"{field} 必须是非负整数")
    return value


def _positive_integer(value: object, field: str) -> int:
    normalized = _nonnegative_integer(value, field)
    if normalized == 0:
        raise RibSpoolError("invalid_argument", f"{field} 必须是正整数")
    return normalized


def _stable_file_identity(value: os.stat_result) -> Tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _open_regular_readonly(path: Path, *, role: str) -> Tuple[BinaryIO, os.stat_result]:
    try:
        path_meta = path.lstat()
    except OSError as error:
        raise RibSpoolError(f"{role}_unavailable", f"{role} 不存在或不可读") from error
    if stat.S_ISLNK(path_meta.st_mode) or not stat.S_ISREG(path_meta.st_mode):
        raise RibSpoolError(
            f"{role}_not_regular",
            f"{role} 必须是非符号链接普通文件",
        )
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened_meta = os.fstat(descriptor)
        if not stat.S_ISREG(opened_meta.st_mode):
            raise RibSpoolError(
                f"{role}_not_regular",
                f"{role} 必须是普通文件",
            )
        if (path_meta.st_dev, path_meta.st_ino) != (
            opened_meta.st_dev,
            opened_meta.st_ino,
        ):
            raise RibSpoolError(
                f"{role}_changed",
                f"{role} 在检查与打开之间发生变化",
            )
        stream = os.fdopen(descriptor, "rb", buffering=0)
        descriptor = None
        return stream, opened_meta
    except RibSpoolError:
        raise
    except OSError as error:
        raise RibSpoolError(f"{role}_unavailable", f"{role} 无法安全打开") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _open_destination_parent(destination: Path) -> Tuple[int, str]:
    if destination.name in {"", ".", ".."}:
        raise RibSpoolError("invalid_argument", "spool destination 必须是文件路径")
    parent = destination.parent
    try:
        parent_meta = parent.lstat()
    except OSError as error:
        raise RibSpoolError(
            "destination_parent_unavailable",
            "spool 目标父目录不存在或不可读",
        ) from error
    if stat.S_ISLNK(parent_meta.st_mode) or not stat.S_ISDIR(parent_meta.st_mode):
        raise RibSpoolError(
            "destination_parent_not_directory",
            "spool 目标父路径必须是非符号链接目录",
        )
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        descriptor = os.open(parent, flags)
        opened_meta = os.fstat(descriptor)
        if not stat.S_ISDIR(opened_meta.st_mode):
            raise RibSpoolError(
                "destination_parent_not_directory",
                "spool 目标父路径不是目录",
            )
        if (parent_meta.st_dev, parent_meta.st_ino) != (
            opened_meta.st_dev,
            opened_meta.st_ino,
        ):
            raise RibSpoolError(
                "destination_parent_changed",
                "spool 目标父目录在检查与打开之间发生变化",
            )
        try:
            os.stat(destination.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise RibSpoolError(
                "destination_exists",
                "spool 目标已存在，拒绝覆盖",
            )
        result = descriptor
        descriptor = None
        return result, destination.name
    except RibSpoolError:
        raise
    except OSError as error:
        raise RibSpoolError(
            "destination_unavailable",
            "spool 目标无法安全检查",
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _normalize_optional_decompressed_identity(
    expected_sha256: Optional[str], expected_size_bytes: Optional[int]
) -> Tuple[Optional[str], Optional[int]]:
    if (expected_sha256 is None) != (expected_size_bytes is None):
        raise RibSpoolError(
            "invalid_argument",
            "expected_decompressed_sha256 与 size 必须同时提供或同时省略",
        )
    if expected_sha256 is None:
        return None, None
    return (
        _sha256(expected_sha256, "expected_decompressed_sha256"),
        _nonnegative_integer(
            expected_size_bytes, "expected_decompressed_size_bytes"
        ),
    )


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        try:
            written = os.write(descriptor, view)
        except OSError as error:
            raise RibSpoolError(
                "temporary_write_failed", "RIB spool 临时文件写入失败"
            ) from error
        if written <= 0:
            raise RibSpoolError(
                "temporary_write_failed", "RIB spool 临时文件写入未取得进展"
            )
        view = view[written:]


def build_rib_decompressed_spool(
    compressed_path: os.PathLike[str] | str,
    destination: os.PathLike[str] | str,
    *,
    expected_compressed_sha256: str,
    expected_compressed_size_bytes: int,
    max_temporary_bytes: int,
    expected_decompressed_sha256: Optional[str] = None,
    expected_decompressed_size_bytes: Optional[int] = None,
) -> RibSpoolBuildResult:
    """严格核验 gzip 源并不可覆盖地发布一份可寻址解压 spool。

    解压字节数必须始终严格小于 ``max_temporary_bytes``；达到边界即在
    publish 前失败并清理同目录临时文件。压缩源和临时 spool 都只通过
    非符号链接普通文件 descriptor 访问，最终使用同目录 hard-link 的
    create-if-absent 语义原子发布。
    """

    try:
        source_path = Path(compressed_path)
        target_path = Path(destination)
    except TypeError as error:
        raise RibSpoolError(
            "invalid_argument", "compressed_path 与 destination 必须是文件路径"
        ) from error
    compressed_sha256 = _sha256(
        expected_compressed_sha256, "expected_compressed_sha256"
    )
    compressed_size = _positive_integer(
        expected_compressed_size_bytes, "expected_compressed_size_bytes"
    )
    temporary_limit = _positive_integer(
        max_temporary_bytes, "max_temporary_bytes"
    )
    expected_decompressed_hash, expected_decompressed_size = (
        _normalize_optional_decompressed_identity(
            expected_decompressed_sha256, expected_decompressed_size_bytes
        )
    )
    if (
        expected_decompressed_size is not None
        and expected_decompressed_size >= temporary_limit
    ):
        raise RibSpoolError(
            "temporary_limit_reached",
            "expected decompressed size 达到或超过 max_temporary_bytes",
        )

    parent_descriptor, target_name = _open_destination_parent(target_path)
    source_stream: Optional[BinaryIO] = None
    temporary_descriptor: Optional[int] = None
    temporary_name = (
        f".{target_name}.tmp-{os.getpid()}-{secrets.token_hex(12)}"
    )
    temporary_created = False
    try:
        source_stream, source_before = _open_regular_readonly(
            source_path, role="compressed_source"
        )
        if source_before.st_size != compressed_size:
            raise RibSpoolError(
                "compressed_size_mismatch",
                "压缩源普通文件大小与 expected_compressed_size_bytes 不一致",
            )
        try:
            temporary_flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
            )
            temporary_descriptor = os.open(
                temporary_name,
                temporary_flags,
                0o440,
                dir_fd=parent_descriptor,
            )
            temporary_created = True
            os.fchmod(temporary_descriptor, 0o440)
            temporary_meta = os.fstat(temporary_descriptor)
            if not stat.S_ISREG(temporary_meta.st_mode):
                raise RibSpoolError(
                    "temporary_not_regular",
                    "RIB spool 临时路径不是普通文件",
                )
        except RibSpoolError:
            raise
        except OSError as error:
            raise RibSpoolError(
                "temporary_create_failed",
                "RIB spool 不可覆盖临时文件创建失败",
            ) from error

        tracked_source = _DigestingReader(source_stream, compressed_size)
        decompressed_digest = hashlib.sha256()
        decompressed_size = 0
        try:
            with gzip.GzipFile(
                filename="", mode="rb", fileobj=tracked_source
            ) as decompressed:
                while True:
                    block = decompressed.read(_SPOOL_COPY_BLOCK_BYTES)
                    if not isinstance(block, bytes):  # pragma: no cover - gzip 合同
                        raise RibSpoolError(
                            "gzip_reader_contract",
                            "gzip reader 必须返回 bytes",
                        )
                    if not block:
                        break
                    next_size = decompressed_size + len(block)
                    if next_size >= temporary_limit:
                        raise RibSpoolError(
                            "temporary_limit_reached",
                            "RIB 解压字节数达到或超过 max_temporary_bytes",
                        )
                    _write_all(temporary_descriptor, block)
                    decompressed_digest.update(block)
                    decompressed_size = next_size
            # 确保 gzip 解码器没有在声明压缩制品结尾前提前停止。
            trailing = tracked_source.read(1)
            if trailing:
                raise RibSpoolError(
                    "compressed_trailing_bytes",
                    "gzip 解码结束后压缩源仍含未消费字节",
                )
        except RibSpoolError:
            raise
        except (gzip.BadGzipFile, EOFError, OSError, zlib.error) as error:
            raise RibSpoolError(
                "gzip_decompression_failed",
                "gzip 压缩源损坏或无法完整解压",
            ) from error

        source_after = os.fstat(source_stream.fileno())
        if _stable_file_identity(source_before) != _stable_file_identity(source_after):
            raise RibSpoolError(
                "compressed_source_changed",
                "压缩源在解压期间发生变化",
            )
        if tracked_source.size_bytes != compressed_size:
            raise RibSpoolError(
                "compressed_size_mismatch",
                "压缩源实际读取大小与 expected_compressed_size_bytes 不一致",
            )
        if tracked_source.sha256 != compressed_sha256:
            raise RibSpoolError(
                "compressed_sha256_mismatch",
                "压缩源 SHA256 与 expected_compressed_sha256 不一致",
            )

        decompressed_sha256 = decompressed_digest.hexdigest()
        if (
            expected_decompressed_size is not None
            and decompressed_size != expected_decompressed_size
        ):
            raise RibSpoolError(
                "decompressed_size_mismatch",
                "解压 spool 大小与 expected_decompressed_size_bytes 不一致",
            )
        if (
            expected_decompressed_hash is not None
            and decompressed_sha256 != expected_decompressed_hash
        ):
            raise RibSpoolError(
                "decompressed_sha256_mismatch",
                "解压 spool SHA256 与 expected_decompressed_sha256 不一致",
            )

        try:
            os.fsync(temporary_descriptor)
            written_meta = os.fstat(temporary_descriptor)
        except OSError as error:
            raise RibSpoolError(
                "temporary_sync_failed", "RIB spool 临时文件无法安全同步"
            ) from error
        if (
            not stat.S_ISREG(written_meta.st_mode)
            or written_meta.st_size != decompressed_size
        ):
            raise RibSpoolError(
                "temporary_identity_mismatch",
                "RIB spool 临时文件类型或大小发生变化",
            )
        os.close(temporary_descriptor)
        temporary_descriptor = None

        try:
            os.link(
                temporary_name,
                target_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            os.fsync(parent_descriptor)
        except FileExistsError as error:
            raise RibSpoolError(
                "destination_exists",
                "spool 目标已存在，拒绝覆盖",
            ) from error
        except OSError as error:
            raise RibSpoolError(
                "atomic_publish_failed", "RIB spool 原子发布失败"
            ) from error

        return RibSpoolBuildResult(
            spool=RibSpoolIdentity(
                path=target_path,
                decompressed_sha256=decompressed_sha256,
                decompressed_size_bytes=decompressed_size,
            ),
            compressed_sha256=tracked_source.sha256,
            compressed_size_bytes=tracked_source.size_bytes,
        )
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        if source_stream is not None:
            source_stream.close()
        if temporary_created:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        os.close(parent_descriptor)


def _open_verified_rib_spool(
    path: os.PathLike[str] | str,
    *,
    expected_decompressed_sha256: str,
    expected_decompressed_size_bytes: int,
) -> Tuple[BinaryIO, RibSpoolIdentity]:
    try:
        spool_path = Path(path)
    except TypeError as error:
        raise RibSpoolError("invalid_argument", "spool path 必须是文件路径") from error
    expected_sha256 = _sha256(
        expected_decompressed_sha256, "expected_decompressed_sha256"
    )
    expected_size = _nonnegative_integer(
        expected_decompressed_size_bytes, "expected_decompressed_size_bytes"
    )
    stream, before = _open_regular_readonly(spool_path, role="spool")
    try:
        if before.st_size != expected_size:
            raise RibSpoolError(
                "spool_size_mismatch",
                "RIB spool 大小与 expected_decompressed_size_bytes 不一致",
            )
        digest = hashlib.sha256()
        size = 0
        while True:
            try:
                block = stream.read(_SPOOL_COPY_BLOCK_BYTES)
            except OSError as error:
                raise RibSpoolError(
                    "spool_read_failed", "RIB spool 无法完整读取"
                ) from error
            if not isinstance(block, bytes):  # pragma: no cover - FileIO 合同
                raise RibSpoolError(
                    "spool_reader_contract", "RIB spool reader 必须返回 bytes"
                )
            if not block:
                break
            digest.update(block)
            size += len(block)
        after = os.fstat(stream.fileno())
        if _stable_file_identity(before) != _stable_file_identity(after):
            raise RibSpoolError("spool_changed", "RIB spool 在核验期间发生变化")
        if size != expected_size:
            raise RibSpoolError(
                "spool_size_mismatch", "RIB spool 实际读取大小与期望不一致"
            )
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise RibSpoolError(
                "spool_sha256_mismatch", "RIB spool SHA256 与期望不一致"
            )
        stream.seek(0, os.SEEK_SET)
        return stream, RibSpoolIdentity(
            path=spool_path,
            decompressed_sha256=actual_sha256,
            decompressed_size_bytes=size,
        )
    except Exception:
        stream.close()
        raise


def verify_rib_decompressed_spool(
    path: os.PathLike[str] | str,
    *,
    expected_decompressed_sha256: str,
    expected_decompressed_size_bytes: int,
) -> RibSpoolIdentity:
    """只读核验 spool 的普通文件类型、非 symlink、大小与 SHA256。"""

    stream, identity = _open_verified_rib_spool(
        path,
        expected_decompressed_sha256=expected_decompressed_sha256,
        expected_decompressed_size_bytes=expected_decompressed_size_bytes,
    )
    stream.close()
    return identity


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


def _parse_supported_record(
    *,
    mrt_type: int,
    subtype: int,
    payload: bytes,
    peers: Optional[Tuple[_Peer, ...]],
) -> Tuple[Tuple[ParsedRouteElement, ...], Optional[Tuple[_Peer, ...]]]:
    if mrt_type == TABLE_DUMP:
        # 避免混合/损坏流在之后错误复用过期的 V2 peer table。
        return _parse_table_dump(payload, subtype), None
    if mrt_type == TABLE_DUMP_V2:
        if subtype == PEER_INDEX_TABLE:
            parsed_peers = _parse_peer_index_table(payload)
            return (), parsed_peers
        return _parse_table_dump_v2_rib(payload, subtype, peers), peers
    raise RibMrtParseError(f"RIB 解析器不接受 MRT type {mrt_type}")


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

        elements, peers = _parse_supported_record(
            mrt_type=mrt_type,
            subtype=subtype,
            payload=payload,
            peers=peers,
        )

        yield ParsedMrtRecord(
            record_ordinal=record_ordinal,
            record_offset=record_offset,
            raw_record=raw_record,
            elements=elements,
        )
        record_ordinal += 1
        record_offset += len(raw_record)


def _seek_nonnegative(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RibMrtSeekError(
            "invalid_seek_coordinate", f"{field} 必须是非负整数"
        )
    return value


def _normalize_record_boundary(
    value: Optional[RibRecordBoundary | Mapping[str, Any]],
) -> Optional[RibRecordBoundary]:
    if value is None:
        return None
    if isinstance(value, RibRecordBoundary):
        raw: Mapping[str, Any] = value.checkpoint_binding()
    elif isinstance(value, Mapping):
        raw = value
    else:
        raise RibMrtSeekError(
            "invalid_previous_record_boundary",
            "previous_record_boundary 必须是 checkpoint 对象",
        )
    expected_fields = {
        "record_ordinal",
        "record_offset",
        "record_length",
        "record_sha256",
    }
    if set(raw) != expected_fields:
        raise RibMrtSeekError(
            "invalid_previous_record_boundary",
            "previous_record_boundary 字段不闭合",
        )
    ordinal = _seek_nonnegative(raw.get("record_ordinal"), "record_ordinal")
    offset = _seek_nonnegative(raw.get("record_offset"), "record_offset")
    length = raw.get("record_length")
    if (
        isinstance(length, bool)
        or not isinstance(length, int)
        or length < MRT_HEADER_LENGTH
        or length > MRT_HEADER_LENGTH + MAX_MRT_PAYLOAD_BYTES
    ):
        raise RibMrtSeekError(
            "invalid_previous_record_boundary",
            "previous_record_boundary.record_length 超出 MRT physical record 上限",
        )
    digest = raw.get("record_sha256")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise RibMrtSeekError(
            "invalid_previous_record_boundary",
            "previous_record_boundary.record_sha256 非法",
        )
    return RibRecordBoundary(ordinal, offset, length, digest)


def _normalize_peer_index_context(
    value: Optional[RibPeerIndexContext | Mapping[str, Any]],
) -> Optional[RibPeerIndexContext]:
    if value is None:
        return None
    if isinstance(value, RibPeerIndexContext):
        raw: Mapping[str, Any] = value.checkpoint_binding()
    elif isinstance(value, Mapping):
        raw = value
    else:
        raise RibMrtSeekError(
            "invalid_peer_index_context",
            "peer_index_context 必须是 checkpoint 对象",
        )
    expected_fields = {
        "schema_version",
        "record_ordinal",
        "record_offset",
        "record_length",
        "record_sha256",
        "peers",
    }
    if (
        set(raw) != expected_fields
        or raw.get("schema_version") != RIB_PEER_INDEX_CONTEXT_SCHEMA_VERSION
    ):
        raise RibMrtSeekError(
            "invalid_peer_index_context", "peer_index_context 字段或 schema 非法"
        )
    boundary = _normalize_record_boundary(
        {
            "record_ordinal": raw.get("record_ordinal"),
            "record_offset": raw.get("record_offset"),
            "record_length": raw.get("record_length"),
            "record_sha256": raw.get("record_sha256"),
        }
    )
    assert boundary is not None  # 构造对象不可能为 None
    peer_rows = raw.get("peers")
    if not isinstance(peer_rows, (list, tuple)):
        raise RibMrtSeekError(
            "invalid_peer_index_context", "peer_index_context.peers 必须是数组"
        )
    peers = []
    for index, row in enumerate(peer_rows):
        if not isinstance(row, Mapping) or set(row) != {"peer_ip", "peer_asn"}:
            raise RibMrtSeekError(
                "invalid_peer_index_context",
                f"peer_index_context.peers[{index}] 字段不闭合",
            )
        try:
            peer_ip = ipaddress.ip_address(row.get("peer_ip")).compressed
        except (TypeError, ValueError) as error:
            raise RibMrtSeekError(
                "invalid_peer_index_context",
                f"peer_index_context.peers[{index}].peer_ip 非法",
            ) from error
        peer_asn = row.get("peer_asn")
        if (
            isinstance(peer_asn, bool)
            or not isinstance(peer_asn, int)
            or not 0 <= peer_asn <= 0xFFFFFFFF
        ):
            raise RibMrtSeekError(
                "invalid_peer_index_context",
                f"peer_index_context.peers[{index}].peer_asn 非法",
            )
        key = (peer_ip, peer_asn)
        peers.append(key)
    return RibPeerIndexContext(
        record_ordinal=boundary.record_ordinal,
        record_offset=boundary.record_offset,
        record_length=boundary.record_length,
        record_sha256=boundary.record_sha256,
        peers=tuple(peers),
    )


def _seek_exact(stream: BinaryIO, offset: int, *, field: str) -> None:
    try:
        stream.seek(offset, os.SEEK_SET)
        if stream.tell() != offset:
            raise RibMrtSeekError(
                "seek_position_mismatch", f"{field} 无法准确 seek"
            )
    except RibMrtSeekError:
        raise
    except (OSError, ValueError, io.UnsupportedOperation) as error:
        raise RibMrtSeekError(
            "seek_source_not_seekable", f"{field} 无法 seek"
        ) from error


def _read_bound_physical_record(
    stream: BinaryIO,
    boundary: RibRecordBoundary,
    *,
    field: str,
) -> bytes:
    _seek_exact(stream, boundary.record_offset, field=field)
    try:
        header = _read_exact(stream, MRT_HEADER_LENGTH, f"{field}.header")
        _timestamp, _mrt_type, _subtype, payload_length = struct.unpack(
            "!IHHI", header
        )
        if payload_length > MAX_MRT_PAYLOAD_BYTES:
            raise RibMrtSeekError(
                "record_boundary_length_mismatch",
                f"{field} payload 超过安全上限",
            )
        if MRT_HEADER_LENGTH + payload_length != boundary.record_length:
            raise RibMrtSeekError(
                "record_boundary_length_mismatch",
                f"{field} header length 与 checkpoint record_length 不一致",
            )
        payload = _read_exact(stream, payload_length, f"{field}.payload")
    except RibMrtSeekError:
        raise
    except RibMrtParseError as error:
        raise RibMrtSeekError(
            "record_boundary_read_failed", f"{field} 无法完整回读"
        ) from error
    raw_record = header + payload
    if hashlib.sha256(raw_record).hexdigest() != boundary.record_sha256:
        raise RibMrtSeekError(
            "record_boundary_sha256_mismatch",
            f"{field} SHA256 与 checkpoint 不一致",
        )
    return raw_record


def _prepare_direct_rib_mrt_seek_context(
    stream: BinaryIO,
    *,
    next_record_ordinal: int,
    next_record_offset: int,
    previous_record_boundary: Optional[RibRecordBoundary | Mapping[str, Any]],
    peer_index_context: Optional[RibPeerIndexContext | Mapping[str, Any]],
) -> Tuple[RibMrtSeekContext, Optional[Tuple[_Peer, ...]]]:
    target_ordinal = _seek_nonnegative(
        next_record_ordinal, "next_record_ordinal"
    )
    target_offset = _seek_nonnegative(next_record_offset, "next_record_offset")
    if not all(hasattr(stream, method) for method in ("read", "seek", "tell")):
        raise RibMrtSeekError(
            "seek_source_not_seekable", "恢复 source 必须是可 seek binary stream"
        )
    previous = _normalize_record_boundary(previous_record_boundary)
    peer_context = _normalize_peer_index_context(peer_index_context)
    if target_ordinal == 0 or target_offset == 0:
        if target_ordinal != 0 or target_offset != 0:
            raise RibMrtSeekError(
                "invalid_seek_coordinate",
                "record ordinal 与 offset 必须同时位于流开头",
            )
        if previous is not None or peer_context is not None:
            raise RibMrtSeekError(
                "unexpected_seek_binding",
                "流开头不得携带 previous boundary 或 peer context",
            )
        _seek_exact(stream, 0, field="MRT stream start")
        return RibMrtSeekContext(0, 0, None, None), None

    if previous is None:
        raise RibMrtSeekError(
            "previous_record_boundary_required",
            "非零恢复点必须提供 previous_record_boundary",
        )
    if previous.record_ordinal + 1 != target_ordinal:
        raise RibMrtSeekError(
            "seek_ordinal_mismatch",
            "previous record ordinal 与 next_record_ordinal 不连续",
        )
    if previous.record_offset + previous.record_length != target_offset:
        raise RibMrtSeekError(
            "seek_offset_not_record_boundary",
            "previous record end 与 next_record_offset 不一致",
        )

    previous_raw = _read_bound_physical_record(
        stream, previous, field="previous_record_boundary"
    )
    peers: Optional[Tuple[_Peer, ...]] = None
    if peer_context is not None:
        if (
            peer_context.record_ordinal > previous.record_ordinal
            or peer_context.record_offset + peer_context.record_length
            > target_offset
        ):
            raise RibMrtSeekError(
                "invalid_peer_index_context",
                "peer index context 必须位于恢复点之前",
            )
        same_as_previous = (
            peer_context.record_ordinal == previous.record_ordinal
            and peer_context.record_offset == previous.record_offset
            and peer_context.record_length == previous.record_length
            and peer_context.record_sha256 == previous.record_sha256
        )
        if peer_context.record_ordinal == previous.record_ordinal and not same_as_previous:
            raise RibMrtSeekError(
                "invalid_peer_index_context",
                "同 ordinal 的 peer context 必须与 previous boundary 完全一致",
            )
        if (
            peer_context.record_ordinal < previous.record_ordinal
            and peer_context.record_offset + peer_context.record_length
            > previous.record_offset
        ):
            raise RibMrtSeekError(
                "invalid_peer_index_context",
                "较早 peer context 不得与 previous record 重叠",
            )
        if same_as_previous:
            peer_raw = previous_raw
        else:
            peer_raw = _read_bound_physical_record(
                stream,
                RibRecordBoundary(
                    peer_context.record_ordinal,
                    peer_context.record_offset,
                    peer_context.record_length,
                    peer_context.record_sha256,
                ),
                field="peer_index_context",
            )
        _timestamp, mrt_type, subtype, payload_length = struct.unpack(
            "!IHHI", peer_raw[:MRT_HEADER_LENGTH]
        )
        if mrt_type != TABLE_DUMP_V2 or subtype != PEER_INDEX_TABLE:
            raise RibMrtSeekError(
                "peer_index_record_type_mismatch",
                "peer context 绑定的 physical record 不是 PEER_INDEX_TABLE",
            )
        parsed_peers = _parse_peer_index_table(
            peer_raw[MRT_HEADER_LENGTH : MRT_HEADER_LENGTH + payload_length]
        )
        parsed_identities = tuple(
            (peer.peer_ip, peer.peer_asn) for peer in parsed_peers
        )
        if parsed_identities != peer_context.peers:
            raise RibMrtSeekError(
                "peer_index_population_mismatch",
                "peer context 人口与绑定 PEER_INDEX_TABLE 不一致",
            )
        peers = parsed_peers

    _seek_exact(stream, target_offset, field="next_record_offset")
    return (
        RibMrtSeekContext(
            next_record_ordinal=target_ordinal,
            next_record_offset=target_offset,
            previous_record_boundary=previous,
            peer_index=peer_context,
        ),
        peers,
    )


class RibMrtRecordSeekIterator(Iterator[ParsedMrtRecord]):
    """验证固定边界后直接 seek 并继续解析 MRT physical records。"""

    def __init__(
        self,
        source: MrtSource,
        *,
        next_record_ordinal: int,
        next_record_offset: int,
        previous_record_boundary: Optional[
            RibRecordBoundary | Mapping[str, Any]
        ] = None,
        peer_index_context: Optional[
            RibPeerIndexContext | Mapping[str, Any]
        ] = None,
    ) -> None:
        if isinstance(source, (bytes, bytearray, memoryview)):
            self._stream: BinaryIO = io.BytesIO(bytes(source))
        elif hasattr(source, "read"):
            self._stream = source  # type: ignore[assignment]
        else:
            raise RibMrtSeekError(
                "seek_source_not_seekable",
                "source 必须是 bytes 或可 seek binary stream",
            )
        self.seek_context, self._peers = _prepare_direct_rib_mrt_seek_context(
            self._stream,
            next_record_ordinal=next_record_ordinal,
            next_record_offset=next_record_offset,
            previous_record_boundary=previous_record_boundary,
            peer_index_context=peer_index_context,
        )
        self._record_ordinal = self.seek_context.next_record_ordinal
        self._record_offset = self.seek_context.next_record_offset
        self.previous_record_boundary = self.seek_context.previous_record_boundary
        self.current_peer_index_context = self.seek_context.peer_index

    def __iter__(self) -> "RibMrtRecordSeekIterator":
        return self

    def __next__(self) -> ParsedMrtRecord:
        header = _read_header_or_eof(self._stream)
        if header is None:
            raise StopIteration
        _timestamp, mrt_type, subtype, payload_length = struct.unpack(
            "!IHHI", header
        )
        if payload_length > MAX_MRT_PAYLOAD_BYTES:
            raise RibMrtParseError("MRT payload 超过 64 MiB 安全上限")
        payload = _read_exact(
            self._stream,
            payload_length,
            f"MRT record[{self._record_ordinal}].payload",
        )
        raw_record = header + payload
        elements, self._peers = _parse_supported_record(
            mrt_type=mrt_type,
            subtype=subtype,
            payload=payload,
            peers=self._peers,
        )
        result = ParsedMrtRecord(
            record_ordinal=self._record_ordinal,
            record_offset=self._record_offset,
            raw_record=raw_record,
            elements=elements,
        )
        self.previous_record_boundary = RibRecordBoundary(
            record_ordinal=self._record_ordinal,
            record_offset=self._record_offset,
            record_length=len(raw_record),
            record_sha256=hashlib.sha256(raw_record).hexdigest(),
        )
        if mrt_type == TABLE_DUMP_V2 and subtype == PEER_INDEX_TABLE:
            assert self._peers is not None  # parser 已成功建立 peer table
            self.current_peer_index_context = RibPeerIndexContext(
                record_ordinal=self._record_ordinal,
                record_offset=self._record_offset,
                record_length=len(raw_record),
                record_sha256=self.previous_record_boundary.record_sha256,
                peers=tuple(
                    (peer.peer_ip, peer.peer_asn) for peer in self._peers
                ),
            )
        elif mrt_type == TABLE_DUMP:
            self.current_peer_index_context = None
        self._record_ordinal += 1
        self._record_offset += len(raw_record)
        return result


def iter_rib_mrt_records_from_offset(
    source: MrtSource,
    *,
    next_record_ordinal: int,
    next_record_offset: int,
    previous_record_boundary: Optional[
        RibRecordBoundary | Mapping[str, Any]
    ] = None,
    peer_index_context: Optional[
        RibPeerIndexContext | Mapping[str, Any]
    ] = None,
) -> RibMrtRecordSeekIterator:
    """回读固定边界并直接 seek，绝不线性扫描到恢复目标。"""

    return RibMrtRecordSeekIterator(
        source,
        next_record_ordinal=next_record_ordinal,
        next_record_offset=next_record_offset,
        previous_record_boundary=previous_record_boundary,
        peer_index_context=peer_index_context,
    )


class RibSpoolRecordIterator(Iterator[ParsedMrtRecord]):
    """持有同一个已核验 spool descriptor 的 closeable seek iterator。"""

    def __init__(
        self,
        path: os.PathLike[str] | str,
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
    ) -> None:
        stream, identity = _open_verified_rib_spool(
            path,
            expected_decompressed_sha256=expected_decompressed_sha256,
            expected_decompressed_size_bytes=expected_decompressed_size_bytes,
        )
        self._stream: Optional[BinaryIO] = stream
        self.spool_identity = identity
        try:
            self._records = iter_rib_mrt_records_from_offset(
                stream,
                next_record_ordinal=next_record_ordinal,
                next_record_offset=next_record_offset,
                previous_record_boundary=previous_record_boundary,
                peer_index_context=peer_index_context,
            )
        except Exception:
            self.close()
            raise
        self.seek_context = self._records.seek_context

    @property
    def previous_record_boundary(self) -> Optional[RibRecordBoundary]:
        return self._records.previous_record_boundary

    @property
    def current_peer_index_context(self) -> Optional[RibPeerIndexContext]:
        return self._records.current_peer_index_context

    def __enter__(self) -> "RibSpoolRecordIterator":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __iter__(self) -> "RibSpoolRecordIterator":
        return self

    def __next__(self) -> ParsedMrtRecord:
        if self._stream is None:
            raise StopIteration
        try:
            return next(self._records)
        except StopIteration:
            self.close()
            raise
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None


def iter_rib_spool_records(
    path: os.PathLike[str] | str,
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
) -> RibSpoolRecordIterator:
    """核验 spool 身份，并从 next physical-record 坐标继续解析。"""

    return RibSpoolRecordIterator(
        path,
        expected_decompressed_sha256=expected_decompressed_sha256,
        expected_decompressed_size_bytes=expected_decompressed_size_bytes,
        next_record_ordinal=next_record_ordinal,
        next_record_offset=next_record_offset,
        previous_record_boundary=previous_record_boundary,
        peer_index_context=peer_index_context,
    )


def parse_rib_mrt_bytes(
    value: Union[bytes, bytearray, memoryview]
) -> Tuple[ParsedMrtRecord, ...]:
    """解析极小内存 fixture，并返回确定顺序 tuple。"""

    return tuple(iter_rib_mrt_records(value))


__all__ = (
    "MAX_MRT_PAYLOAD_BYTES",
    "RIB_DECOMPRESSED_SPOOL_SCHEMA_VERSION",
    "RIB_PEER_INDEX_CONTEXT_SCHEMA_VERSION",
    "RibMrtRecordSeekIterator",
    "RibMrtParseError",
    "RibMrtSeekContext",
    "RibMrtSeekError",
    "RibPeerIndexContext",
    "RibRecordBoundary",
    "RibSpoolBuildResult",
    "RibSpoolError",
    "RibSpoolIdentity",
    "RibSpoolRecordIterator",
    "build_rib_decompressed_spool",
    "iter_rib_mrt_records",
    "iter_rib_mrt_records_from_offset",
    "iter_rib_spool_records",
    "parse_rib_mrt_bytes",
    "verify_rib_decompressed_spool",
)
