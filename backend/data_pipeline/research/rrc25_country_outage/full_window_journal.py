"""完整 UPDATE 窗口的 artifact 边界事务日志。

本模块只负责研究输出目录中的外围文件事务，不读取 MRT、不连接数据库，也不
解释路由语义。调用方必须先以 :func:`begin_artifact_attempt` 写入 create-only
的原始读取预留单据，才能打开一个五分钟 UPDATE。一次 attempt 保守按整个
压缩制品计费；失败、崩溃和重试均不会把已经预留的 raw bytes 归零。

成功槽位采用两份可覆盖的 ``scratch`` 状态和一个原子 ``CURRENT`` 指针。
scratch 不是输出或证据，最终包只能引用 create-only、内容寻址的 boundary
receipt 和 shard 哈希链。发布顺序固定为：shard -> inactive scratch ->
boundary receipt -> CURRENT。只有 scratch 而没有 receipt 时不得恢复；若
receipt 已闭合但 CURRENT 尚未推进，恢复可在唯一后继成立时修复指针。

所有接口都在完整 artifact 边界工作。这里刻意没有 ``next_record_ordinal``；
gzip UPDATE 的中途读取不能被冒充为可恢复的 single pass。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Tuple

from ...route_event import artifact_id_v1
from .file_artifacts import canonical_json


JOURNAL_SCHEMA_VERSION = "rrc25-full-window-journal/v1"
SCRATCH_SCHEMA_VERSION = "rrc25-full-window-scratch/v1"
BOUNDARY_RECEIPT_SCHEMA_VERSION = "rrc25-full-window-boundary-receipt/v1"
ATTEMPT_START_SCHEMA_VERSION = "rrc25-full-window-attempt-start/v1"
ATTEMPT_OUTCOME_SCHEMA_VERSION = "rrc25-full-window-attempt-outcome/v1"
RAW_GENESIS_SCHEMA_VERSION = "rrc25-full-window-raw-genesis/v1"
ACTIVE_ATTEMPT_SCHEMA_VERSION = "rrc25-full-window-active-attempt/v1"
RAW_ACCUMULATOR_SCHEMA_VERSION = "rrc25-full-window-raw-accumulator/v1"
CURRENT_SCHEMA_VERSION = "rrc25-full-window-current/v1"
FINGERPRINT_SCHEMA = "rrc25_full_window_journal_fingerprint_v1"
CHAIN_SCHEMA = b"rrc25-full-window-shard-chain/v1"

DEFAULT_ADMISSION_SECONDS = 420.0
DEFAULT_SOFT_STOP_SECONDS = 540.0
DEFAULT_HARD_RUNTIME_SECONDS = 600.0
DEFAULT_MAX_TEMPORARY_BYTES = 5_000_000_000
DEFAULT_MAX_RAW_BYTES = 50_000_000_000

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ID_RE = re.compile(r"^art_v1_[0-9a-f]{32}$")
_RUN_ID_RE = re.compile(r"^research_run_v1_[0-9a-f]{24}$")
_KIND_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_ATTEMPT_ID_RE = re.compile(r"^attempt_v1_[0-9a-f]{32}$")
_BINDING_FIELDS = frozenset(
    {
        "profile_sha256",
        "input_selection_sha256",
        "code_sha256",
        "mapping_sha256",
    }
)


class FullWindowJournalError(ValueError):
    """完整窗口事务、资源或内容绑定不成立。"""


class SimulatedJournalCrash(RuntimeError):
    """仅供 fixture 在明确发布边界模拟进程崩溃。"""


CrashHook = Callable[[str], None]
PublicationGate = Callable[[str], None]


@dataclass(frozen=True)
class ArtifactDescriptor:
    """一个必须完整单 pass 的五分钟 UPDATE 制品。"""

    index: int
    artifact_id: str
    file_sha256: str
    size_bytes: int
    collector_id: str
    slot_start_utc: str
    slot_end_exclusive_utc: str

    def to_dict(self) -> dict[str, Any]:
        _validate_artifact(self)
        return {
            "index": self.index,
            "artifact_id": self.artifact_id,
            "file_sha256": self.file_sha256,
            "size_bytes": self.size_bytes,
            "collector_id": self.collector_id,
            "slot_start_utc": self.slot_start_utc,
            "slot_end_exclusive_utc": self.slot_end_exclusive_utc,
        }


@dataclass(frozen=True)
class AttemptToken:
    attempt_id: str
    path: str
    sha256: str
    artifact: ArtifactDescriptor
    reserved_raw_bytes: int
    cumulative_reserved_raw_bytes: int


@dataclass(frozen=True)
class SinglePassProof:
    """调用方在完整耗尽 stream 后提供的可核验读取事实。"""

    status: str
    compressed_file_sha256: str
    compressed_size_bytes: int
    compressed_bytes_read_observed: int
    compressed_read_passes: int
    process_seconds: float
    peak_temporary_bytes: int
    database_write_operations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "compressed_file_sha256": self.compressed_file_sha256,
            "compressed_size_bytes": self.compressed_size_bytes,
            "compressed_bytes_read_observed": self.compressed_bytes_read_observed,
            "compressed_read_passes": self.compressed_read_passes,
            "process_seconds": self.process_seconds,
            "peak_temporary_bytes": self.peak_temporary_bytes,
            "database_write_operations": self.database_write_operations,
        }


@dataclass(frozen=True)
class ShardInput:
    """一个成功槽位要发布的确定顺序 JSONL 分片。"""

    kind: str
    records: Tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class ArtifactAdmission:
    allowed: bool
    estimated_process_seconds: float
    conservative_bytes_per_second: float
    throughput_sample_count: int
    cumulative_reserved_before: int
    cumulative_reserved_after: int
    reason: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "estimated_process_seconds": self.estimated_process_seconds,
            "conservative_bytes_per_second": self.conservative_bytes_per_second,
            "throughput_sample_count": self.throughput_sample_count,
            "cumulative_reserved_before": self.cumulative_reserved_before,
            "cumulative_reserved_after": self.cumulative_reserved_after,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class JournalHead:
    root: Path
    current: Mapping[str, Any]
    receipt: Mapping[str, Any]
    scratch: Mapping[str, Any]
    current_path: str
    receipt_path: str
    receipt_sha256: str

    @property
    def sequence(self) -> int:
        return int(self.receipt["sequence"])

    @property
    def next_artifact_index(self) -> int:
        return int(self.receipt["next_artifact_index"])

    @property
    def shard_chain_sha256(self) -> str:
        return str(self.receipt["shard_chain_sha256"])


@dataclass(frozen=True)
class CommittedArtifact:
    head: JournalHead
    shard_refs: Tuple[Mapping[str, Any], ...]
    outcome_ref: Mapping[str, Any]


def _fingerprinted(schema_version: str, semantic: Mapping[str, Any]) -> dict[str, Any]:
    payload = {"schema_version": schema_version, **dict(semantic)}
    digest = hashlib.sha256(
        canonical_json({"schema": FINGERPRINT_SCHEMA, "payload": payload}).encode(
            "utf-8"
        )
    ).hexdigest()
    return {**payload, "fingerprint_sha256": digest}


def _verify_fingerprint(payload: Any, schema_version: str, name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise FullWindowJournalError(f"{name} 必须是对象")
    semantic = dict(payload)
    fingerprint = semantic.pop("fingerprint_sha256", None)
    if not isinstance(fingerprint, str) or _SHA256_RE.fullmatch(fingerprint) is None:
        raise FullWindowJournalError(f"{name} fingerprint 非法")
    if semantic.get("schema_version") != schema_version:
        raise FullWindowJournalError(f"{name} schema_version 不受支持")
    expected = hashlib.sha256(
        canonical_json({"schema": FINGERPRINT_SCHEMA, "payload": semantic}).encode(
            "utf-8"
        )
    ).hexdigest()
    if fingerprint != expected:
        raise FullWindowJournalError(f"{name} fingerprint 不一致")
    return dict(payload)


def _nonnegative(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FullWindowJournalError(f"{field} 必须是非负整数")
    return value


def _positive_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FullWindowJournalError(f"{field} 必须是正有限数")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise FullWindowJournalError(f"{field} 必须是正有限数")
    return result


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise FullWindowJournalError(f"{field} 必须是 SHA256")
    return value


def _utc(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise FullWindowJournalError(f"{field} 必须是 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise FullWindowJournalError(f"{field} 不是合法 UTC 时间") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed) or parsed.microsecond:
        raise FullWindowJournalError(f"{field} 必须是秒级 UTC")
    canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical != value:
        raise FullWindowJournalError(f"{field} 不是规范 UTC")
    return value


def _validate_bindings(bindings: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(bindings, Mapping) or set(bindings) != _BINDING_FIELDS:
        raise FullWindowJournalError("bindings 字段不闭合")
    return {field: _sha(bindings[field], f"bindings.{field}") for field in sorted(bindings)}


def _validate_artifact(artifact: ArtifactDescriptor) -> None:
    if not isinstance(artifact, ArtifactDescriptor):
        raise FullWindowJournalError("artifact 必须是 ArtifactDescriptor")
    _nonnegative(artifact.index, "artifact.index")
    if not isinstance(artifact.artifact_id, str) or _ARTIFACT_ID_RE.fullmatch(artifact.artifact_id) is None:
        raise FullWindowJournalError("artifact_id 非法")
    file_sha = _sha(artifact.file_sha256, "artifact.file_sha256")
    expected_id = artifact_id_v1(file_sha)
    if artifact.artifact_id != expected_id:
        raise FullWindowJournalError("artifact_id 与 file_sha256 不一致")
    if _nonnegative(artifact.size_bytes, "artifact.size_bytes") == 0:
        raise FullWindowJournalError("artifact.size_bytes 必须大于零")
    if not isinstance(artifact.collector_id, str) or not artifact.collector_id:
        raise FullWindowJournalError("collector_id 非法")
    start = _utc(artifact.slot_start_utc, "artifact.slot_start_utc")
    end = _utc(artifact.slot_end_exclusive_utc, "artifact.slot_end_exclusive_utc")
    start_dt = datetime.fromisoformat(start[:-1] + "+00:00")
    end_dt = datetime.fromisoformat(end[:-1] + "+00:00")
    if (end_dt - start_dt).total_seconds() != 300:
        raise FullWindowJournalError("artifact 必须恰好覆盖五分钟")
    if start_dt.minute % 5 or start_dt.second:
        raise FullWindowJournalError("artifact 槽必须对齐五分钟")


def _safe_relative(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise FullWindowJournalError(f"{field} 必须是非空相对路径")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise FullWindowJournalError(f"{field} 必须是安全相对路径")
    return path.as_posix()


def _directory(path: Path, *, create: bool = False) -> None:
    if create and not path.exists() and not path.is_symlink():
        os.mkdir(path, 0o750)
    try:
        meta = path.lstat()
    except OSError as error:
        raise FullWindowJournalError(f"目录不存在：{path}") from error
    if stat.S_ISLNK(meta.st_mode) or not stat.S_ISDIR(meta.st_mode):
        raise FullWindowJournalError(f"路径必须是非符号链接目录：{path}")


def _layout(root: Path, *, create: bool) -> None:
    if create and not root.exists() and not root.is_symlink():
        os.mkdir(root, 0o750)
    _directory(root)
    for relative in (
        "scratch",
        "receipts",
        "shards",
        "raw-ledger",
        "raw-ledger/attempts",
        "raw-ledger/outcomes",
    ):
        _directory(root / relative, create=create)


def _read_regular(path: Path, *, maximum_bytes: int) -> Tuple[bytes, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise FullWindowJournalError(f"文件不可读：{path}") from error
    digest = hashlib.sha256()
    chunks = []
    total = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FullWindowJournalError("研究制品必须是普通文件")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum_bytes:
                raise FullWindowJournalError("研究制品超过读取上限")
            digest.update(block)
            chunks.append(block)
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in identity):
            raise FullWindowJournalError("研究制品在读取期间变化")
    finally:
        os.close(descriptor)
    return b"".join(chunks), digest.hexdigest()


def _hash_regular(path: Path, *, maximum_bytes: int) -> Tuple[str, int]:
    """流式哈希普通文件；不会把大型 scratch/shard 读回内存。"""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise FullWindowJournalError(f"文件不可读：{path}") from error
    digest = hashlib.sha256()
    total = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FullWindowJournalError("研究制品必须是普通文件")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum_bytes:
                raise FullWindowJournalError("研究制品超过读取上限")
            digest.update(block)
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in identity):
            raise FullWindowJournalError("研究制品在哈希期间变化")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), total


def _read_json(path: Path, *, maximum_bytes: int, expected_sha: Optional[str] = None) -> dict[str, Any]:
    encoded, digest = _read_regular(path, maximum_bytes=maximum_bytes)
    if expected_sha is not None and digest != expected_sha:
        raise FullWindowJournalError(f"文件 SHA256 不一致：{path}")
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FullWindowJournalError(f"文件不是合法 JSON：{path}") from error
    if not isinstance(payload, Mapping):
        raise FullWindowJournalError("JSON 顶层必须是对象")
    return dict(payload)


def _immutable_json(root: Path, directory: str, stem: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    encoded = (canonical_json(dict(payload)) + "\n").encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    relative = f"{directory}/{stem}-{digest}.json"
    target = root / relative
    if target.exists() or target.is_symlink():
        existing, existing_sha = _read_regular(target, maximum_bytes=max(len(encoded), 1))
        if existing_sha != digest or existing != encoded:
            raise FullWindowJournalError("内容寻址 JSON 既有字节冲突")
    else:
        temporary = target.parent / f".{target.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, target, follow_symlinks=False)
            _fsync_directory(target.parent)
        except FileExistsError:
            existing, existing_sha = _read_regular(target, maximum_bytes=max(len(encoded), 1))
            if existing_sha != digest or existing != encoded:
                raise FullWindowJournalError("并发发布的内容寻址 JSON 冲突")
        finally:
            temporary.unlink(missing_ok=True)
    return {"path": relative, "sha256": digest}


def _prospective_immutable_json_ref(
    directory: str, stem: str, payload: Mapping[str, Any]
) -> Mapping[str, str]:
    encoded = (canonical_json(dict(payload)) + "\n").encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return {"path": f"{directory}/{stem}-{digest}.json", "sha256": digest}


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _tree_regular_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise FullWindowJournalError("journal 初始化目录不得包含符号链接")
        if stat.S_ISREG(metadata.st_mode):
            total += metadata.st_size
    return total


def _check_initialization_temporary_budget(
    root: Path, *, retained_external_bytes: int, maximum_bytes: int
) -> None:
    observed = retained_external_bytes + _tree_regular_bytes(root)
    if observed >= maximum_bytes:
        raise FullWindowJournalError(
            "journal init 与保留 checkpoint 的临时空间合计达到 5GB 边界"
        )


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> str:
    encoded = (canonical_json(dict(payload)) + "\n").encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    temporary = path.parent / f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return digest


def _atomic_scratch_gzip(path: Path, payload: Mapping[str, Any]) -> Tuple[str, int]:
    temporary = path.parent / f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    with open(temporary, "xb", buffering=0) as raw:
        _write_scratch_gzip(raw, payload)
        os.fsync(raw.fileno())
    try:
        digest, size = _hash_regular(temporary, maximum_bytes=5_000_000_000)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return digest, size


class _DigestSink:
    """只接收 gzip 输出并计算摘要，避免复算 helper 落临时文件。"""

    def __init__(self) -> None:
        self.digest = hashlib.sha256()

    def write(self, value: bytes) -> int:
        self.digest.update(value)
        return len(value)

    def flush(self) -> None:
        return None


def _write_scratch_gzip(raw: Any, payload: Mapping[str, Any]) -> None:
    if not isinstance(payload, Mapping):
        raise FullWindowJournalError("scratch payload 必须是对象")
    with gzip.GzipFile(
        filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0
    ) as stream:
        stream.write((canonical_json(dict(payload)) + "\n").encode("utf-8"))


def scratch_payload_sha256(payload: Mapping[str, Any]) -> str:
    """按 journal 的精确 gzip 编码计算 scratch SHA，供离线逐槽复算比对。"""

    sink = _DigestSink()
    _write_scratch_gzip(sink, payload)
    return sink.digest.hexdigest()


def _publish_shard(root: Path, artifact: ArtifactDescriptor, shard: ShardInput) -> Mapping[str, Any]:
    if not isinstance(shard, ShardInput) or _KIND_RE.fullmatch(shard.kind) is None:
        raise FullWindowJournalError("shard.kind 非法")
    if any(not isinstance(record, Mapping) for record in shard.records):
        raise FullWindowJournalError("shard.records 只能包含对象")
    shard_dir = root / "shards" / shard.kind
    _directory(shard_dir, create=True)
    temporary = shard_dir / f".slot-{artifact.index:04d}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    count = 0
    with open(temporary, "xb", buffering=0) as raw:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0) as stream:
            for record in shard.records:
                stream.write((canonical_json(dict(record)) + "\n").encode("utf-8"))
                count += 1
        os.fsync(raw.fileno())
    try:
        digest, size = _hash_regular(temporary, maximum_bytes=5_000_000_000)
        target = shard_dir / f"slot-{artifact.index:04d}-{digest}.jsonl.gz"
        try:
            os.link(temporary, target, follow_symlinks=False)
            _fsync_directory(shard_dir)
        except FileExistsError:
            existing_sha, _existing_size = _hash_regular(target, maximum_bytes=5_000_000_000)
            if existing_sha != digest:
                raise FullWindowJournalError("内容寻址 shard 冲突")
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "kind": shard.kind,
        "path": target.relative_to(root).as_posix(),
        "sha256": digest,
        "size_bytes": size,
        "record_count": count,
    }


def _publish_genesis_shard(root: Path, shard: ShardInput) -> Mapping[str, Any]:
    """发布由已验证 seed 派生的不可变证据；不借用可轮换 scratch。"""

    if not isinstance(shard, ShardInput) or _KIND_RE.fullmatch(shard.kind) is None:
        raise FullWindowJournalError("genesis shard.kind 非法")
    if any(not isinstance(record, Mapping) for record in shard.records):
        raise FullWindowJournalError("genesis shard.records 只能包含对象")
    shard_dir = root / "shards" / shard.kind
    _directory(shard_dir, create=True)
    temporary = shard_dir / f".genesis.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    count = 0
    with open(temporary, "xb", buffering=0) as raw:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0) as stream:
            for record in shard.records:
                stream.write((canonical_json(dict(record)) + "\n").encode("utf-8"))
                count += 1
        os.fsync(raw.fileno())
    try:
        digest, size = _hash_regular(temporary, maximum_bytes=5_000_000_000)
        target = shard_dir / f"genesis-{digest}.jsonl.gz"
        try:
            os.link(temporary, target, follow_symlinks=False)
            _fsync_directory(shard_dir)
        except FileExistsError:
            existing_sha, _existing_size = _hash_regular(
                target, maximum_bytes=5_000_000_000
            )
            if existing_sha != digest:
                raise FullWindowJournalError("内容寻址 genesis shard 冲突")
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "kind": shard.kind,
        "path": target.relative_to(root).as_posix(),
        "sha256": digest,
        "size_bytes": size,
        "record_count": count,
    }


def _artifact_from_dict(value: Any, field: str = "artifact") -> ArtifactDescriptor:
    required = {
        "index",
        "artifact_id",
        "file_sha256",
        "size_bytes",
        "collector_id",
        "slot_start_utc",
        "slot_end_exclusive_utc",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise FullWindowJournalError(f"{field} 字段不闭合")
    artifact = ArtifactDescriptor(
        index=value["index"],
        artifact_id=value["artifact_id"],
        file_sha256=value["file_sha256"],
        size_bytes=value["size_bytes"],
        collector_id=value["collector_id"],
        slot_start_utc=value["slot_start_utc"],
        slot_end_exclusive_utc=value["slot_end_exclusive_utc"],
    )
    _validate_artifact(artifact)
    return artifact


def _initial_chain() -> str:
    return hashlib.sha256(CHAIN_SCHEMA).hexdigest()


def _advance_chain(previous: str, artifact: ArtifactDescriptor, shards: Sequence[Mapping[str, Any]]) -> str:
    _sha(previous, "previous shard chain")
    semantic = {
        "artifact": artifact.to_dict(),
        "shards": [dict(item) for item in sorted(shards, key=lambda row: (str(row["kind"]), str(row["path"])))],
    }
    return hashlib.sha256(
        CHAIN_SCHEMA
        + bytes.fromhex(previous)
        + hashlib.sha256(canonical_json(semantic).encode("utf-8")).digest()
    ).hexdigest()


def _advance_genesis_chain(shards: Sequence[Mapping[str, Any]]) -> str:
    ordered = [
        dict(item)
        for item in sorted(shards, key=lambda row: (str(row["kind"]), str(row["path"])))
    ]
    if not ordered:
        return _initial_chain()
    semantic = {"genesis_seed_shards": ordered}
    return hashlib.sha256(
        CHAIN_SCHEMA
        + bytes.fromhex(_initial_chain())
        + hashlib.sha256(canonical_json(semantic).encode("utf-8")).digest()
    ).hexdigest()


def initialize_full_window_journal(
    output_root: os.PathLike[str] | str,
    *,
    run_id: str,
    bindings: Mapping[str, str],
    total_artifacts: int,
    initial_compact_state: Mapping[str, Any],
    preliminary_seed_read_bytes: int,
    seed_artifact_read_bytes: int,
    additional_pre_update_raw_read_bytes: int,
    bootstrap_bytes_per_second: float,
    genesis_shards: Sequence[ShardInput] = (),
    retained_external_temporary_bytes: int = 0,
    maximum_temporary_bytes: int = DEFAULT_MAX_TEMPORARY_BYTES,
) -> JournalHead:
    """创建 journal genesis；既有路径一律拒绝覆盖。"""

    root = Path(output_root)
    if root.exists() or root.is_symlink():
        raise FileExistsError("full-window journal 根目录已存在")
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise FullWindowJournalError("run_id 非法")
    normalized_bindings = _validate_bindings(bindings)
    total = _nonnegative(total_artifacts, "total_artifacts")
    if total == 0:
        raise FullWindowJournalError("total_artifacts 必须大于零")
    if not isinstance(initial_compact_state, Mapping):
        raise FullWindowJournalError("initial_compact_state 必须是对象")
    preliminary = _nonnegative(preliminary_seed_read_bytes, "preliminary_seed_read_bytes")
    seed = _nonnegative(seed_artifact_read_bytes, "seed_artifact_read_bytes")
    additional = _nonnegative(
        additional_pre_update_raw_read_bytes,
        "additional_pre_update_raw_read_bytes",
    )
    initial_reserved = preliminary + seed + additional
    if initial_reserved >= DEFAULT_MAX_RAW_BYTES:
        raise FullWindowJournalError("journal genesis raw 累计已达到 50GB 硬边界")
    bootstrap = _positive_float(bootstrap_bytes_per_second, "bootstrap_bytes_per_second")
    retained_external = _nonnegative(
        retained_external_temporary_bytes,
        "retained_external_temporary_bytes",
    )
    maximum_temporary = _nonnegative(
        maximum_temporary_bytes, "maximum_temporary_bytes"
    )
    if maximum_temporary == 0 or retained_external >= maximum_temporary:
        raise FullWindowJournalError("journal init 外部临时空间已达到硬边界")
    if isinstance(genesis_shards, (str, bytes)) or not isinstance(
        genesis_shards, Sequence
    ):
        raise FullWindowJournalError("genesis_shards 必须是 ShardInput 序列")
    genesis_kinds = tuple(shard.kind for shard in genesis_shards)
    if len(genesis_kinds) != len(set(genesis_kinds)):
        raise FullWindowJournalError("genesis_shards kind 不得重复")
    os.mkdir(root, 0o750)
    _layout(root, create=True)

    genesis = _fingerprinted(
        RAW_GENESIS_SCHEMA_VERSION,
        {
            "run_id": run_id,
            "bindings": normalized_bindings,
            "preliminary_seed_read_bytes": preliminary,
            "seed_artifact_read_bytes": seed,
            "additional_pre_update_raw_read_bytes": additional,
            "initial_reserved_raw_bytes": initial_reserved,
            "accounting_semantics": "conservative_full_artifact_reservation_no_refund",
        },
    )
    genesis_ref = _immutable_json(root, "raw-ledger", "genesis", genesis)
    accumulator = _fingerprinted(
        RAW_ACCUMULATOR_SCHEMA_VERSION,
        {
            "run_id": run_id,
            "bindings": normalized_bindings,
            "raw_genesis_ref": dict(genesis_ref),
            "attempt_count": 0,
            "cumulative_reserved_raw_bytes": initial_reserved,
            "latest_attempt_ref": None,
        },
    )
    _atomic_json(root / "raw-ledger/ACCUMULATOR", accumulator)
    _check_initialization_temporary_budget(
        root,
        retained_external_bytes=retained_external,
        maximum_bytes=maximum_temporary,
    )
    published_genesis_shards = []
    for shard in genesis_shards:
        published_genesis_shards.append(_publish_genesis_shard(root, shard))
        _check_initialization_temporary_budget(
            root,
            retained_external_bytes=retained_external,
            maximum_bytes=maximum_temporary,
        )
    genesis_shard_refs = tuple(
        sorted(
            published_genesis_shards,
            key=lambda row: (row["kind"], row["path"]),
        )
    )
    genesis_chain = _advance_genesis_chain(genesis_shard_refs)
    scratch_payload = _fingerprinted(
        SCRATCH_SCHEMA_VERSION,
        {
            "run_id": run_id,
            "bindings": normalized_bindings,
            "sequence": 0,
            "next_artifact_index": 0,
            "total_artifacts": total,
            "active_scratch_slot": "a",
            "compact_state": dict(initial_compact_state),
            "runtime_estimator": {
                "bootstrap_bytes_per_second": bootstrap,
                "minimum_observed_bytes_per_second": None,
                "sample_count": 0,
            },
            "shard_chain_sha256": genesis_chain,
        },
    )
    scratch_relative = "scratch/state-a.jsonl.gz"
    scratch_sha, _scratch_size = _atomic_scratch_gzip(root / scratch_relative, scratch_payload)
    _check_initialization_temporary_budget(
        root,
        retained_external_bytes=retained_external,
        maximum_bytes=maximum_temporary,
    )
    receipt = _fingerprinted(
        BOUNDARY_RECEIPT_SCHEMA_VERSION,
        {
            "run_id": run_id,
            "bindings": normalized_bindings,
            "sequence": 0,
            "next_artifact_index": 0,
            "total_artifacts": total,
            "committed_artifact": None,
            "attempt_ref": None,
            "outcome_ref": None,
            "state_ref": {"slot": "a", "path": scratch_relative, "sha256": scratch_sha},
            "shards": [dict(item) for item in genesis_shard_refs],
            "shard_chain_sha256": genesis_chain,
            "previous_receipt_ref": None,
            "raw_genesis_ref": dict(genesis_ref),
        },
    )
    receipt_ref = _immutable_json(root, "receipts", "boundary-0000", receipt)
    current = _fingerprinted(
        CURRENT_SCHEMA_VERSION,
        {
            "run_id": run_id,
            "sequence": 0,
            "receipt_ref": dict(receipt_ref),
        },
    )
    _atomic_json(root / "CURRENT", current)
    return load_full_window_head(root, expected_bindings=normalized_bindings)


def _load_current(root: Path) -> dict[str, Any]:
    payload = _read_json(root / "CURRENT", maximum_bytes=1024 * 1024)
    return _verify_fingerprint(payload, CURRENT_SCHEMA_VERSION, "CURRENT")


def _load_receipt(root: Path, ref: Mapping[str, Any]) -> Tuple[dict[str, Any], str, str]:
    if not isinstance(ref, Mapping) or set(ref) != {"path", "sha256"}:
        raise FullWindowJournalError("receipt_ref 字段不闭合")
    relative = _safe_relative(ref["path"], "receipt_ref.path")
    expected_sha = _sha(ref["sha256"], "receipt_ref.sha256")
    payload = _read_json(root / relative, maximum_bytes=16 * 1024 * 1024, expected_sha=expected_sha)
    return _verify_fingerprint(payload, BOUNDARY_RECEIPT_SCHEMA_VERSION, "boundary receipt"), relative, expected_sha


def _closed_ref(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise FullWindowJournalError(f"{field} 字段不闭合")
    return {
        "path": _safe_relative(value["path"], f"{field}.path"),
        "sha256": _sha(value["sha256"], f"{field}.sha256"),
    }


def _load_attempt(root: Path, ref: Any) -> dict[str, Any]:
    normalized = _closed_ref(ref, "attempt_ref")
    if not normalized["path"].startswith("raw-ledger/attempts/attempt-start-"):
        raise FullWindowJournalError("attempt_ref 路径类型非法")
    payload = _read_json(
        root / normalized["path"],
        maximum_bytes=1024 * 1024,
        expected_sha=normalized["sha256"],
    )
    return _verify_fingerprint(payload, ATTEMPT_START_SCHEMA_VERSION, "attempt start")


def _load_outcome(root: Path, ref: Any) -> dict[str, Any]:
    normalized = _closed_ref(ref, "outcome_ref")
    if not normalized["path"].startswith("raw-ledger/outcomes/parse-outcome-"):
        raise FullWindowJournalError("outcome_ref 路径类型非法")
    payload = _read_json(
        root / normalized["path"],
        maximum_bytes=1024 * 1024,
        expected_sha=normalized["sha256"],
    )
    return _verify_fingerprint(payload, ATTEMPT_OUTCOME_SCHEMA_VERSION, "attempt outcome")


def _proof_from_dict(value: Any) -> SinglePassProof:
    required = {
        "status",
        "compressed_file_sha256",
        "compressed_size_bytes",
        "compressed_bytes_read_observed",
        "compressed_read_passes",
        "process_seconds",
        "peak_temporary_bytes",
        "database_write_operations",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise FullWindowJournalError("single-pass proof 字段不闭合")
    return SinglePassProof(**{field: value[field] for field in required})


def _validate_shard_ref(
    root: Path,
    value: Any,
    artifact: ArtifactDescriptor,
) -> dict[str, Any]:
    required = {"kind", "path", "sha256", "size_bytes", "record_count"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise FullWindowJournalError("shard ref 字段不闭合")
    kind = value["kind"]
    if not isinstance(kind, str) or _KIND_RE.fullmatch(kind) is None:
        raise FullWindowJournalError("shard kind 非法")
    digest = _sha(value["sha256"], "shard.sha256")
    size = _nonnegative(value["size_bytes"], "shard.size_bytes")
    count = _nonnegative(value["record_count"], "shard.record_count")
    relative = _safe_relative(value["path"], "shard.path")
    expected = f"shards/{kind}/slot-{artifact.index:04d}-{digest}.jsonl.gz"
    if relative != expected:
        raise FullWindowJournalError("shard 路径未绑定 kind/artifact index/SHA256")
    actual_sha, actual_size = _hash_regular(root / relative, maximum_bytes=5_000_000_000)
    if actual_sha != digest or actual_size != size:
        raise FullWindowJournalError("shard 文件 SHA256/size 不闭合")
    observed_count = 0
    try:
        with gzip.open(root / relative, "rb") as stream:
            for line in stream:
                if not line.endswith(b"\n"):
                    raise FullWindowJournalError("shard JSONL 存在不完整行")
                try:
                    row = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise FullWindowJournalError("shard JSONL 记录非法") from error
                if not isinstance(row, Mapping):
                    raise FullWindowJournalError("shard JSONL 顶层记录必须是对象")
                observed_count += 1
    except (OSError, EOFError) as error:
        raise FullWindowJournalError("shard gzip EOF/CRC 校验失败") from error
    if observed_count != count:
        raise FullWindowJournalError("shard record_count 不闭合")
    return {
        "kind": kind,
        "path": relative,
        "sha256": digest,
        "size_bytes": size,
        "record_count": count,
    }


def _validate_genesis_shard_ref(root: Path, value: Any) -> dict[str, Any]:
    required = {"kind", "path", "sha256", "size_bytes", "record_count"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise FullWindowJournalError("genesis shard ref 字段不闭合")
    kind = value["kind"]
    if not isinstance(kind, str) or _KIND_RE.fullmatch(kind) is None:
        raise FullWindowJournalError("genesis shard kind 非法")
    digest = _sha(value["sha256"], "genesis shard.sha256")
    size = _nonnegative(value["size_bytes"], "genesis shard.size_bytes")
    count = _nonnegative(value["record_count"], "genesis shard.record_count")
    relative = _safe_relative(value["path"], "genesis shard.path")
    expected = f"shards/{kind}/genesis-{digest}.jsonl.gz"
    if relative != expected:
        raise FullWindowJournalError("genesis shard 路径未绑定 kind/SHA256")
    actual_sha, actual_size = _hash_regular(
        root / relative, maximum_bytes=5_000_000_000
    )
    if actual_sha != digest or actual_size != size:
        raise FullWindowJournalError("genesis shard 文件 SHA256/size 不闭合")
    observed_count = 0
    try:
        with gzip.open(root / relative, "rb") as stream:
            for line in stream:
                if not line.endswith(b"\n"):
                    raise FullWindowJournalError("genesis shard JSONL 存在不完整行")
                try:
                    row = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise FullWindowJournalError("genesis shard JSONL 记录非法") from error
                if not isinstance(row, Mapping):
                    raise FullWindowJournalError("genesis shard 记录必须是对象")
                observed_count += 1
    except (OSError, EOFError) as error:
        raise FullWindowJournalError("genesis shard gzip EOF/CRC 校验失败") from error
    if observed_count != count:
        raise FullWindowJournalError("genesis shard record_count 不闭合")
    return {
        "kind": kind,
        "path": relative,
        "sha256": digest,
        "size_bytes": size,
        "record_count": count,
    }


def _validate_receipt_semantics(
    root: Path,
    receipt: Mapping[str, Any],
    *,
    receipt_path: str,
    receipt_sha256: str,
    expected_bindings: Mapping[str, str],
    previous_receipt: Optional[Mapping[str, Any]] = None,
    previous_ref: Optional[Mapping[str, str]] = None,
) -> None:
    required = {
        "schema_version",
        "fingerprint_sha256",
        "run_id",
        "bindings",
        "sequence",
        "next_artifact_index",
        "total_artifacts",
        "committed_artifact",
        "attempt_ref",
        "outcome_ref",
        "state_ref",
        "shards",
        "shard_chain_sha256",
        "previous_receipt_ref",
        "raw_genesis_ref",
    }
    if set(receipt) != required:
        raise FullWindowJournalError("boundary receipt 字段不闭合")
    bindings = _validate_bindings(expected_bindings)
    if receipt.get("bindings") != bindings:
        raise FullWindowJournalError("boundary receipt bindings 不一致")
    run_id = receipt.get("run_id")
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise FullWindowJournalError("boundary receipt run_id 非法")
    sequence = _nonnegative(receipt.get("sequence"), "receipt.sequence")
    next_index = _nonnegative(receipt.get("next_artifact_index"), "next_artifact_index")
    total = _nonnegative(receipt.get("total_artifacts"), "total_artifacts")
    if total == 0 or next_index > total or sequence != next_index:
        raise FullWindowJournalError("boundary receipt sequence/cursor/total 不闭合")
    _closed_ref(receipt.get("raw_genesis_ref"), "raw_genesis_ref")
    _sha(receipt.get("shard_chain_sha256"), "shard_chain_sha256")
    state_ref = receipt.get("state_ref")
    if not isinstance(state_ref, Mapping) or set(state_ref) != {"slot", "path", "sha256"}:
        raise FullWindowJournalError("state_ref 字段不闭合")

    if sequence == 0:
        if any(
            receipt.get(field) is not None
            for field in ("committed_artifact", "attempt_ref", "outcome_ref", "previous_receipt_ref")
        ):
            raise FullWindowJournalError("genesis receipt 不得伪造 artifact 输出")
        shard_values = receipt.get("shards")
        if not isinstance(shard_values, list):
            raise FullWindowJournalError("genesis receipt.shards 必须是数组")
        shards = tuple(
            _validate_genesis_shard_ref(root, value) for value in shard_values
        )
        if list(shards) != sorted(
            shards, key=lambda row: (row["kind"], row["path"])
        ) or len({row["kind"] for row in shards}) != len(shards):
            raise FullWindowJournalError("genesis shards 必须按 kind/path 唯一排序")
        if receipt.get("shard_chain_sha256") != _advance_genesis_chain(shards):
            raise FullWindowJournalError("genesis shard chain 非法")
        return

    if previous_receipt is None or previous_ref is None:
        raise FullWindowJournalError("非 genesis receipt 必须提供已验证前驱")
    normalized_previous = _closed_ref(receipt.get("previous_receipt_ref"), "previous_receipt_ref")
    if normalized_previous != dict(previous_ref):
        raise FullWindowJournalError("boundary receipt previous ref 不一致")
    if previous_receipt.get("run_id") != run_id or previous_receipt.get("bindings") != bindings:
        raise FullWindowJournalError("boundary receipt 与前驱身份不一致")
    if previous_receipt.get("sequence") != sequence - 1 or previous_receipt.get("next_artifact_index") != sequence - 1:
        raise FullWindowJournalError("boundary receipt 前驱 sequence/cursor 不连续")
    if previous_receipt.get("total_artifacts") != total:
        raise FullWindowJournalError("boundary receipt total_artifacts 发生变化")
    if previous_receipt.get("raw_genesis_ref") != receipt.get("raw_genesis_ref"):
        raise FullWindowJournalError("boundary receipt raw genesis 发生变化")

    artifact = _artifact_from_dict(receipt.get("committed_artifact"), "committed_artifact")
    if artifact.index != sequence - 1:
        raise FullWindowJournalError("committed artifact 与 sequence 不一致")
    attempt_ref = _closed_ref(receipt.get("attempt_ref"), "attempt_ref")
    attempt = _load_attempt(root, attempt_ref)
    if (
        attempt.get("run_id") != run_id
        or attempt.get("bindings") != bindings
        or attempt.get("artifact") != artifact.to_dict()
        or attempt.get("base_receipt_ref") != dict(previous_ref)
        or attempt.get("reserved_raw_bytes") != artifact.size_bytes
    ):
        raise FullWindowJournalError("attempt start 与 boundary receipt 不闭合")
    outcome_ref = _closed_ref(receipt.get("outcome_ref"), "outcome_ref")
    outcome = _load_outcome(root, outcome_ref)
    if (
        outcome.get("attempt_ref") != attempt_ref
        or outcome.get("attempt_id") != attempt.get("attempt_id")
        or outcome.get("outcome") != "complete_single_pass"
        or outcome.get("failure_reason") is not None
        or outcome.get("reservation_refunded_bytes") != 0
    ):
        raise FullWindowJournalError("attempt outcome 与成功边界不闭合")
    proof = _proof_from_dict(outcome.get("proof"))
    _verify_single_pass(proof, artifact)
    if outcome.get("observed_compressed_bytes") != artifact.size_bytes:
        raise FullWindowJournalError("attempt outcome raw bytes 不闭合")

    shard_values = receipt.get("shards")
    if not isinstance(shard_values, list):
        raise FullWindowJournalError("receipt.shards 必须是数组")
    shards = tuple(_validate_shard_ref(root, value, artifact) for value in shard_values)
    if list(shards) != sorted(shards, key=lambda row: (row["kind"], row["path"])):
        raise FullWindowJournalError("receipt.shards 必须按 kind/path 排序")
    if len({row["kind"] for row in shards}) != len(shards):
        raise FullWindowJournalError("receipt.shards kind 重复")
    expected_chain = _advance_chain(
        str(previous_receipt.get("shard_chain_sha256")), artifact, shards
    )
    if receipt.get("shard_chain_sha256") != expected_chain:
        raise FullWindowJournalError("boundary receipt shard chain 不一致")


def _load_scratch(root: Path, ref: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(ref, Mapping) or set(ref) != {"slot", "path", "sha256"}:
        raise FullWindowJournalError("state_ref 字段不闭合")
    slot = ref["slot"]
    if slot not in {"a", "b"}:
        raise FullWindowJournalError("scratch slot 非法")
    relative = _safe_relative(ref["path"], "state_ref.path")
    if relative != f"scratch/state-{slot}.jsonl.gz":
        raise FullWindowJournalError("scratch slot 与路径不一致")
    compressed, digest = _read_regular(root / relative, maximum_bytes=5_000_000_000)
    if digest != _sha(ref["sha256"], "state_ref.sha256"):
        raise FullWindowJournalError("scratch SHA256 不一致")
    try:
        raw = gzip.decompress(compressed)
    except (OSError, EOFError) as error:
        raise FullWindowJournalError("scratch gzip 不完整") from error
    lines = raw.splitlines()
    if len(lines) != 1 or not raw.endswith(b"\n"):
        raise FullWindowJournalError("scratch 必须恰有一条 JSONL")
    try:
        payload = json.loads(lines[0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FullWindowJournalError("scratch JSON 非法") from error
    return _verify_fingerprint(payload, SCRATCH_SCHEMA_VERSION, "scratch")


def _validate_head(head: JournalHead, expected_bindings: Mapping[str, str]) -> JournalHead:
    bindings = _validate_bindings(expected_bindings)
    current = head.current
    receipt = head.receipt
    scratch = head.scratch
    if set(current) != {
        "schema_version",
        "fingerprint_sha256",
        "run_id",
        "sequence",
        "receipt_ref",
    }:
        raise FullWindowJournalError("CURRENT 字段不闭合")
    if current.get("receipt_ref") != {
        "path": head.receipt_path,
        "sha256": head.receipt_sha256,
    }:
        raise FullWindowJournalError("CURRENT receipt_ref 与已加载 receipt 不一致")
    if current.get("run_id") != receipt.get("run_id") or receipt.get("run_id") != scratch.get("run_id"):
        raise FullWindowJournalError("journal run_id 链不一致")
    if receipt.get("bindings") != bindings or scratch.get("bindings") != bindings:
        raise FullWindowJournalError("journal bindings 不一致")
    sequence = _nonnegative(receipt.get("sequence"), "receipt.sequence")
    if current.get("sequence") != sequence or scratch.get("sequence") != sequence:
        raise FullWindowJournalError("journal sequence 不闭合")
    if receipt.get("next_artifact_index") != scratch.get("next_artifact_index"):
        raise FullWindowJournalError("journal cursor 不闭合")
    if receipt.get("shard_chain_sha256") != scratch.get("shard_chain_sha256"):
        raise FullWindowJournalError("journal shard chain 不闭合")
    if receipt.get("state_ref", {}).get("slot") != scratch.get("active_scratch_slot"):
        raise FullWindowJournalError("active scratch slot 不一致")
    if scratch.get("total_artifacts") != receipt.get("total_artifacts"):
        raise FullWindowJournalError("scratch total_artifacts 与 receipt 不一致")
    if scratch.get("shard_chain_sha256") != receipt.get("shard_chain_sha256"):
        raise FullWindowJournalError("scratch shard chain 与 receipt 不一致")
    previous = None
    previous_ref = None
    if sequence > 0:
        previous_ref = _closed_ref(
            receipt.get("previous_receipt_ref"), "previous_receipt_ref"
        )
        previous, _previous_path, _previous_sha = _load_receipt(root=head.root, ref=previous_ref)
    _validate_receipt_semantics(
        head.root,
        receipt,
        receipt_path=head.receipt_path,
        receipt_sha256=head.receipt_sha256,
        expected_bindings=bindings,
        previous_receipt=previous,
        previous_ref=previous_ref,
    )
    genesis_ref = _closed_ref(receipt.get("raw_genesis_ref"), "raw_genesis_ref")
    genesis_paths = sorted((head.root / "raw-ledger").glob("genesis-*.json"))
    if len(genesis_paths) != 1 or genesis_paths[0].relative_to(head.root).as_posix() != genesis_ref["path"]:
        raise FullWindowJournalError("receipt raw genesis 引用不是唯一 genesis")
    genesis = _verify_fingerprint(
        _read_json(
            genesis_paths[0],
            maximum_bytes=1024 * 1024,
            expected_sha=genesis_ref["sha256"],
        ),
        RAW_GENESIS_SCHEMA_VERSION,
        "raw genesis",
    )
    if genesis.get("run_id") != receipt.get("run_id") or genesis.get("bindings") != bindings:
        raise FullWindowJournalError("raw genesis 与 journal 身份不一致")
    accumulator = _load_raw_accumulator(head.root)
    if (
        accumulator.get("run_id") != receipt.get("run_id")
        or accumulator.get("bindings") != bindings
    ):
        raise FullWindowJournalError("raw accumulator 与 journal 身份不一致")
    return head


def _unique_receipt_successor(root: Path, head: JournalHead) -> Optional[Mapping[str, Any]]:
    candidates = []
    previous_ref = {"path": head.receipt_path, "sha256": head.receipt_sha256}
    next_sequence = head.sequence + 1
    for path in sorted(
        (root / "receipts").glob(f"boundary-{next_sequence:04d}-*.json")
    ):
        try:
            payload = _verify_fingerprint(
                _read_json(path, maximum_bytes=16 * 1024 * 1024),
                BOUNDARY_RECEIPT_SCHEMA_VERSION,
                "candidate boundary receipt",
            )
        except FullWindowJournalError:
            raise
        if payload.get("previous_receipt_ref") == previous_ref:
            candidates.append((path, payload))
    if not candidates:
        return None
    if len(candidates) != 1:
        raise FullWindowJournalError("CURRENT 存在多个已闭合后继 receipt，拒绝猜测")
    path, payload = candidates[0]
    encoded, digest = _read_regular(path, maximum_bytes=16 * 1024 * 1024)
    del encoded
    _validate_receipt_semantics(
        root,
        payload,
        receipt_path=path.relative_to(root).as_posix(),
        receipt_sha256=digest,
        expected_bindings=head.receipt["bindings"],
        previous_receipt=head.receipt,
        previous_ref=previous_ref,
    )
    successor_scratch = _load_scratch(root, payload.get("state_ref"))
    if (
        successor_scratch.get("sequence") != payload.get("sequence")
        or successor_scratch.get("next_artifact_index")
        != payload.get("next_artifact_index")
        or successor_scratch.get("shard_chain_sha256")
        != payload.get("shard_chain_sha256")
    ):
        raise FullWindowJournalError("successor receipt 与 scratch 不闭合")
    return {"path": path.relative_to(root).as_posix(), "sha256": digest, "payload": payload}


def load_full_window_head(
    output_root: os.PathLike[str] | str,
    *,
    expected_bindings: Mapping[str, str],
    recover_committed_successor: bool = True,
) -> JournalHead:
    """加载成功头；可修复 receipt 已闭合但 CURRENT 未推进的唯一后继。"""

    root = Path(output_root)
    _layout(root, create=False)
    current = _load_current(root)
    receipt, receipt_path, receipt_sha = _load_receipt(root, current.get("receipt_ref"))
    scratch = _load_scratch(root, receipt.get("state_ref"))
    head = _validate_head(
        JournalHead(root, current, receipt, scratch, "CURRENT", receipt_path, receipt_sha),
        expected_bindings,
    )
    if recover_committed_successor:
        successor = _unique_receipt_successor(root, head)
        if successor is not None:
            payload = successor["payload"]
            next_current = _fingerprinted(
                CURRENT_SCHEMA_VERSION,
                {
                    "run_id": payload["run_id"],
                    "sequence": payload["sequence"],
                    "receipt_ref": {"path": successor["path"], "sha256": successor["sha256"]},
                },
            )
            _atomic_json(root / "CURRENT", next_current)
            return load_full_window_head(
                root,
                expected_bindings=expected_bindings,
                recover_committed_successor=False,
            )
    return head


def verify_full_receipt_ancestry(head: JournalHead) -> int:
    """从冻结 terminal receipt 逐步验证到 genesis，并返回 receipt 数量。

    该操作会读取全部 immutable receipt、attempt/outcome 和 shard，适合最终
    封包验收，不应在每个五分钟 artifact 的热路径重复执行。
    """

    if not isinstance(head, JournalHead):
        raise FullWindowJournalError("head 必须是 JournalHead")
    if _load_active_attempt(head.root) is not None:
        raise FullWindowJournalError(
            "journal 仍有 ACTIVE attempt；须先在 execution lease 内 reconcile"
        )
    current = head.receipt
    current_ref = {"path": head.receipt_path, "sha256": head.receipt_sha256}
    expected_sequence = head.sequence
    visited = set()
    count = 0
    while True:
        identity = (current_ref["path"], current_ref["sha256"])
        if identity in visited:
            raise FullWindowJournalError("receipt ancestry 存在循环")
        visited.add(identity)
        if current.get("sequence") != expected_sequence:
            raise FullWindowJournalError("receipt ancestry sequence 不连续")
        previous = None
        previous_ref = None
        if expected_sequence > 0:
            previous_ref = _closed_ref(
                current.get("previous_receipt_ref"), "previous_receipt_ref"
            )
            previous, loaded_path, loaded_sha = _load_receipt(head.root, previous_ref)
            if loaded_path != previous_ref["path"] or loaded_sha != previous_ref["sha256"]:
                raise FullWindowJournalError("receipt ancestry 前驱引用不闭合")
        _validate_receipt_semantics(
            head.root,
            current,
            receipt_path=current_ref["path"],
            receipt_sha256=current_ref["sha256"],
            expected_bindings=head.receipt["bindings"],
            previous_receipt=previous,
            previous_ref=previous_ref,
        )
        count += 1
        if expected_sequence == 0:
            break
        current = previous
        current_ref = previous_ref
        expected_sequence -= 1
    if count != head.sequence + 1:
        raise FullWindowJournalError("receipt ancestry 数量不闭合")
    successor = _unique_receipt_successor(head.root, head)
    if successor is not None:
        raise FullWindowJournalError(
            "recovery_required: CURRENT 落后于唯一已闭合 successor receipt"
        )
    ancestry_paths = {path for path, _digest in visited}
    receipt_paths = {
        path.relative_to(head.root).as_posix()
        for path in (head.root / "receipts").glob("boundary-*.json")
    }
    extras = sorted(receipt_paths - ancestry_paths)
    if extras:
        raise FullWindowJournalError(
            "receipt 集合存在非 terminal ancestry 的 orphan/sibling: "
            + ",".join(extras)
        )
    recomputed, attempt_count = _recomputed_cumulative_reserved_raw_bytes(
        head.root
    )
    accumulator = _load_raw_accumulator(head.root)
    if (
        accumulator.get("attempt_count") != attempt_count
        or accumulator.get("cumulative_reserved_raw_bytes") != recomputed
    ):
        raise FullWindowJournalError("raw accumulator 与全量 attempt ledger 重算不一致")
    return count


def _raw_genesis(root: Path) -> Mapping[str, Any]:
    paths = sorted((root / "raw-ledger").glob("genesis-*.json"))
    if len(paths) != 1:
        raise FullWindowJournalError("raw ledger genesis 必须唯一")
    payload = _read_json(paths[0], maximum_bytes=1024 * 1024)
    return _verify_fingerprint(payload, RAW_GENESIS_SCHEMA_VERSION, "raw genesis")


def _attempt_receipts(root: Path) -> Tuple[Mapping[str, Any], ...]:
    output = []
    seen_ids = set()
    genesis = _raw_genesis(root)
    for path in sorted((root / "raw-ledger/attempts").glob("attempt-start-*.json")):
        payload = _verify_fingerprint(
            _read_json(path, maximum_bytes=1024 * 1024),
            ATTEMPT_START_SCHEMA_VERSION,
            "attempt start",
        )
        attempt_id = payload.get("attempt_id")
        if not isinstance(attempt_id, str) or _ATTEMPT_ID_RE.fullmatch(attempt_id) is None or attempt_id in seen_ids:
            raise FullWindowJournalError("attempt_id 非法或重复")
        if payload.get("run_id") != genesis.get("run_id") or payload.get("bindings") != genesis.get("bindings"):
            raise FullWindowJournalError("attempt start 与 raw genesis 身份不一致")
        seen_ids.add(attempt_id)
        output.append(payload)
    return tuple(output)


def _load_raw_accumulator(root: Path) -> Mapping[str, Any]:
    payload = _verify_fingerprint(
        _read_json(root / "raw-ledger/ACCUMULATOR", maximum_bytes=1024 * 1024),
        RAW_ACCUMULATOR_SCHEMA_VERSION,
        "raw accumulator",
    )
    required = {
        "schema_version",
        "fingerprint_sha256",
        "run_id",
        "bindings",
        "raw_genesis_ref",
        "attempt_count",
        "cumulative_reserved_raw_bytes",
        "latest_attempt_ref",
    }
    if set(payload) != required:
        raise FullWindowJournalError("raw accumulator 字段不闭合")
    genesis = _raw_genesis(root)
    genesis_paths = sorted((root / "raw-ledger").glob("genesis-*.json"))
    if len(genesis_paths) != 1:
        raise FullWindowJournalError("raw ledger genesis 必须唯一")
    genesis_ref = _outcome_ref(genesis_paths[0], root)
    if (
        payload.get("run_id") != genesis.get("run_id")
        or payload.get("bindings") != genesis.get("bindings")
        or payload.get("raw_genesis_ref") != genesis_ref
    ):
        raise FullWindowJournalError("raw accumulator 与 genesis 身份不一致")
    count = _nonnegative(payload.get("attempt_count"), "raw attempt_count")
    cumulative = _nonnegative(
        payload.get("cumulative_reserved_raw_bytes"),
        "cumulative_reserved_raw_bytes",
    )
    initial = _nonnegative(
        genesis.get("initial_reserved_raw_bytes"), "initial_reserved_raw_bytes"
    )
    latest = payload.get("latest_attempt_ref")
    if count == 0:
        if latest is not None or cumulative != initial:
            raise FullWindowJournalError("空 raw accumulator 状态不闭合")
    else:
        normalized = _closed_ref(latest, "latest_attempt_ref")
        attempt = _verify_fingerprint(
            _read_json(
                root / normalized["path"],
                maximum_bytes=1024 * 1024,
                expected_sha=normalized["sha256"],
            ),
            ATTEMPT_START_SCHEMA_VERSION,
            "latest attempt",
        )
        admission = attempt.get("admission")
        if (
            not isinstance(admission, Mapping)
            or admission.get("cumulative_reserved_after") != cumulative
        ):
            raise FullWindowJournalError("raw accumulator latest attempt 不闭合")
    return payload


def _advance_raw_accumulator(
    root: Path,
    *,
    attempt_ref: Mapping[str, str],
    attempt_payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    normalized_ref = _closed_ref(attempt_ref, "attempt_ref")
    current = _load_raw_accumulator(root)
    if current.get("latest_attempt_ref") == normalized_ref:
        return current
    admission = attempt_payload.get("admission")
    if not isinstance(admission, Mapping):
        raise FullWindowJournalError("attempt admission 缺失")
    before = _nonnegative(
        admission.get("cumulative_reserved_before"),
        "admission.cumulative_reserved_before",
    )
    after = _nonnegative(
        admission.get("cumulative_reserved_after"),
        "admission.cumulative_reserved_after",
    )
    if (
        current.get("cumulative_reserved_raw_bytes") != before
        or after != before + attempt_payload.get("reserved_raw_bytes", -1)
    ):
        raise FullWindowJournalError("raw accumulator 推进与 attempt reservation 不闭合")
    next_value = _fingerprinted(
        RAW_ACCUMULATOR_SCHEMA_VERSION,
        {
            "run_id": current["run_id"],
            "bindings": current["bindings"],
            "raw_genesis_ref": current["raw_genesis_ref"],
            "attempt_count": int(current["attempt_count"]) + 1,
            "cumulative_reserved_raw_bytes": after,
            "latest_attempt_ref": normalized_ref,
        },
    )
    _atomic_json(root / "raw-ledger/ACCUMULATOR", next_value)
    return _load_raw_accumulator(root)


def _recomputed_cumulative_reserved_raw_bytes(root: Path) -> Tuple[int, int]:
    genesis = _raw_genesis(root)
    attempts = _attempt_receipts(root)
    total = _nonnegative(
        genesis.get("initial_reserved_raw_bytes"), "initial_reserved_raw_bytes"
    )
    for attempt in attempts:
        total += _nonnegative(attempt.get("reserved_raw_bytes"), "reserved_raw_bytes")
    return total, len(attempts)


def cumulative_reserved_raw_bytes(output_root: os.PathLike[str] | str) -> int:
    root = Path(output_root)
    _layout(root, create=False)
    return int(_load_raw_accumulator(root)["cumulative_reserved_raw_bytes"])


def _runtime_estimator(head: JournalHead) -> Tuple[float, Optional[float], int]:
    value = head.scratch.get("runtime_estimator")
    if not isinstance(value, Mapping) or set(value) != {
        "bootstrap_bytes_per_second",
        "minimum_observed_bytes_per_second",
        "sample_count",
    }:
        raise FullWindowJournalError("runtime_estimator 字段不闭合")
    bootstrap = _positive_float(value["bootstrap_bytes_per_second"], "bootstrap throughput")
    observed_raw = value["minimum_observed_bytes_per_second"]
    observed = None if observed_raw is None else _positive_float(observed_raw, "observed throughput")
    samples = _nonnegative(value["sample_count"], "runtime sample_count")
    if (observed is None) != (samples == 0):
        raise FullWindowJournalError("runtime estimator 样本状态矛盾")
    return bootstrap, observed, samples


def plan_artifact_admission(
    head: JournalHead,
    artifact: ArtifactDescriptor,
    *,
    admission_seconds: float = DEFAULT_ADMISSION_SECONDS,
    max_raw_bytes: int = DEFAULT_MAX_RAW_BYTES,
) -> ArtifactAdmission:
    """在打开 raw 前按冻结 bootstrap/最慢历史吞吐做公开保守估计。"""

    _validate_artifact(artifact)
    if artifact.index != head.next_artifact_index:
        raise FullWindowJournalError("artifact index 与 CURRENT cursor 不一致")
    admission_limit = _positive_float(admission_seconds, "admission_seconds")
    raw_limit = _nonnegative(max_raw_bytes, "max_raw_bytes")
    bootstrap, observed, samples = _runtime_estimator(head)
    conservative = min(bootstrap, observed) if observed is not None else bootstrap
    estimate = artifact.size_bytes / conservative
    before = cumulative_reserved_raw_bytes(head.root)
    after = before + artifact.size_bytes
    reason = None
    if after >= raw_limit:
        reason = "cumulative_raw_reservation_exceeds_limit"
    elif estimate >= admission_limit:
        reason = "estimated_runtime_reaches_artifact_admission_boundary"
    return ArtifactAdmission(
        allowed=reason is None,
        estimated_process_seconds=estimate,
        conservative_bytes_per_second=conservative,
        throughput_sample_count=samples,
        cumulative_reserved_before=before,
        cumulative_reserved_after=after,
        reason=reason,
    )


@contextmanager
def full_window_execution_lock(output_root: os.PathLike[str] | str):
    """持有整个 artifact parse+publish 生命周期的单 worker 租约。

    进程被 TERM/KILL 时内核会释放 flock；后继进程只有取得该锁后才可把无
    receipt 的旧 attempt 判为 abandoned。直接调用 journal API 的测试/工具若
    不使用此上下文，不得调用 abandoned reconciliation。
    """

    root = Path(output_root)
    _layout(root, create=False)
    lock_path = root / "scratch/execution.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _active_attempt_path(root: Path) -> Path:
    return root / "raw-ledger/ACTIVE"


def _load_active_attempt(root: Path) -> Optional[Mapping[str, Any]]:
    path = _active_attempt_path(root)
    if not path.exists() and not path.is_symlink():
        return None
    return _verify_fingerprint(
        _read_json(path, maximum_bytes=2 * 1024 * 1024),
        ACTIVE_ATTEMPT_SCHEMA_VERSION,
        "ACTIVE attempt",
    )


def _clear_active_attempt(root: Path, *, expected_attempt_ref: Mapping[str, str]) -> None:
    active = _load_active_attempt(root)
    if active is None:
        return
    if active.get("attempt_ref") != dict(expected_attempt_ref):
        return
    path = _active_attempt_path(root)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise FullWindowJournalError("ACTIVE attempt 必须是普通文件")
    path.unlink()
    _fsync_directory(path.parent)


def reconcile_abandoned_active_attempt(head: JournalHead) -> Mapping[str, Any]:
    """在持有 execution lease 后闭合监督器已确认死亡的旧 attempt。"""

    if not isinstance(head, JournalHead):
        raise FullWindowJournalError("head 必须是 JournalHead")
    commit_lock_path = head.root / "scratch/commit.lock"
    attempt_lock_path = head.root / "scratch/attempt.lock"
    commit_descriptor = os.open(commit_lock_path, os.O_RDWR | os.O_CREAT, 0o640)
    attempt_descriptor = os.open(attempt_lock_path, os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(commit_descriptor, fcntl.LOCK_EX)
        fcntl.flock(attempt_descriptor, fcntl.LOCK_EX)
        fresh = load_full_window_head(
            head.root,
            expected_bindings=head.receipt["bindings"],
        )
        active = _load_active_attempt(head.root)
        if active is None:
            return {"action": "none", "head": fresh}
        if (
            active.get("run_id") != fresh.receipt.get("run_id")
            or active.get("bindings") != fresh.receipt.get("bindings")
        ):
            raise FullWindowJournalError("ACTIVE attempt 与 journal 身份不一致")
        attempt_ref = _closed_ref(active.get("attempt_ref"), "ACTIVE.attempt_ref")
        attempt_path = head.root / attempt_ref["path"]
        if not attempt_path.exists() and not attempt_path.is_symlink():
            _clear_active_attempt(head.root, expected_attempt_ref=attempt_ref)
            return {"action": "discarded_before_attempt_authorization", "head": fresh}
        attempt_payload = _verify_fingerprint(
            _read_json(
                attempt_path,
                maximum_bytes=1024 * 1024,
                expected_sha=attempt_ref["sha256"],
            ),
            ATTEMPT_START_SCHEMA_VERSION,
            "ACTIVE attempt start",
        )
        _advance_raw_accumulator(
            head.root,
            attempt_ref=attempt_ref,
            attempt_payload=attempt_payload,
        )
        artifact = _artifact_from_dict(attempt_payload.get("artifact"))
        token = AttemptToken(
            str(attempt_payload.get("attempt_id")),
            attempt_ref["path"],
            attempt_ref["sha256"],
            artifact,
            int(attempt_payload.get("reserved_raw_bytes")),
            int(attempt_payload.get("admission", {}).get("cumulative_reserved_after")),
        )
        if fresh.receipt.get("attempt_ref") == attempt_ref:
            _clear_active_attempt(head.root, expected_attempt_ref=attempt_ref)
            return {"action": "cleared_after_committed_receipt", "head": fresh}
        expected_base = {
            "path": fresh.receipt_path,
            "sha256": fresh.receipt_sha256,
        }
        if attempt_payload.get("base_receipt_ref") != expected_base:
            raise FullWindowJournalError(
                "ACTIVE attempt 既未被当前 receipt 消费，也不基于当前边界"
            )
        existing_terminal = _existing_terminal_outcome_ref(head.root, token)
        if existing_terminal is not None:
            terminal_ref = existing_terminal
            action = "cleared_after_existing_terminal"
        else:
            complete_ref = _complete_parse_outcome_ref(head.root, token)
            if complete_ref is not None:
                terminal_ref = _write_terminal_outcome(
                    head.root,
                    token,
                    outcome="publication_failed_after_complete_single_pass",
                    reason="abandoned_after_supervised_process_termination",
                    observed_state="exact",
                    observed=artifact.size_bytes,
                    lower_bound=artifact.size_bytes,
                    upper_bound=artifact.size_bytes,
                    completed_parse_outcome_ref=complete_ref,
                )
                action = "closed_complete_parse_without_receipt"
            else:
                terminal_ref = _write_terminal_outcome(
                    head.root,
                    token,
                    outcome="failed_before_complete_single_pass",
                    reason="abandoned_after_supervised_process_termination",
                    observed_state="unknown_after_process_termination",
                    observed=None,
                    lower_bound=0,
                    upper_bound=artifact.size_bytes,
                    completed_parse_outcome_ref=None,
                )
                action = "closed_precomplete_with_unknown_observed_interval"
        _clear_active_attempt(head.root, expected_attempt_ref=attempt_ref)
        return {"action": action, "terminal_outcome_ref": terminal_ref, "head": fresh}
    finally:
        fcntl.flock(attempt_descriptor, fcntl.LOCK_UN)
        fcntl.flock(commit_descriptor, fcntl.LOCK_UN)
        os.close(attempt_descriptor)
        os.close(commit_descriptor)


def begin_artifact_attempt(
    head: JournalHead,
    artifact: ArtifactDescriptor,
    *,
    admission_seconds: float = DEFAULT_ADMISSION_SECONDS,
    max_raw_bytes: int = DEFAULT_MAX_RAW_BYTES,
    track_active_attempt: bool = False,
) -> AttemptToken:
    """先持久化整制品 raw reservation；返回后调用方才可打开 UPDATE。"""

    lock_path = head.root / "scratch/attempt.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if not isinstance(track_active_attempt, bool):
            raise FullWindowJournalError("track_active_attempt 必须是布尔值")
        fresh = load_full_window_head(
            head.root,
            expected_bindings=head.receipt["bindings"],
        )
        if fresh.receipt_sha256 != head.receipt_sha256:
            raise FullWindowJournalError("CURRENT 已变化，必须基于新 head 规划")
        admission = plan_artifact_admission(
            fresh,
            artifact,
            admission_seconds=admission_seconds,
            max_raw_bytes=max_raw_bytes,
        )
        if not admission.allowed:
            raise FullWindowJournalError(f"artifact 打开前预检拒绝：{admission.reason}")
        attempt_id = "attempt_v1_" + secrets.token_hex(16)
        payload = _fingerprinted(
            ATTEMPT_START_SCHEMA_VERSION,
            {
                "run_id": fresh.receipt["run_id"],
                "bindings": fresh.receipt["bindings"],
                "attempt_id": attempt_id,
                "base_receipt_ref": {
                    "path": fresh.receipt_path,
                    "sha256": fresh.receipt_sha256,
                },
                "artifact": artifact.to_dict(),
                "reserved_raw_bytes": artifact.size_bytes,
                "admission": admission.to_dict(),
                "raw_open_authorization": "granted_after_this_receipt_is_durable",
            },
        )
        stem = f"attempt-start-{attempt_id}"
        expected_ref = _prospective_immutable_json_ref(
            "raw-ledger/attempts", stem, payload
        )
        if track_active_attempt:
            if _load_active_attempt(head.root) is not None:
                raise FullWindowJournalError(
                    "已有 ACTIVE attempt；须先在 execution lease 内完成 reconcile"
                )
            active = _fingerprinted(
                ACTIVE_ATTEMPT_SCHEMA_VERSION,
                {
                    "run_id": fresh.receipt["run_id"],
                    "bindings": fresh.receipt["bindings"],
                    "base_receipt_ref": {
                        "path": fresh.receipt_path,
                        "sha256": fresh.receipt_sha256,
                    },
                    "attempt_ref": dict(expected_ref),
                    "artifact": artifact.to_dict(),
                    "lifecycle": (
                        "active_until_terminal_outcome_or_committed_receipt"
                    ),
                },
            )
            _atomic_json(_active_attempt_path(head.root), active)
        ref = _immutable_json(head.root, "raw-ledger/attempts", stem, payload)
        if dict(ref) != dict(expected_ref):  # pragma: no cover - 内容寻址不变量
            raise FullWindowJournalError("attempt ref 与预写 ACTIVE 不一致")
        advanced = _advance_raw_accumulator(
            head.root,
            attempt_ref=ref,
            attempt_payload=payload,
        )
        cumulative = int(advanced["cumulative_reserved_raw_bytes"])
        if cumulative != admission.cumulative_reserved_after:
            raise FullWindowJournalError("raw reservation 发布后累计值与预检不一致")
        return AttemptToken(attempt_id, str(ref["path"]), str(ref["sha256"]), artifact, artifact.size_bytes, cumulative)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def record_attempt_failure(
    output_root: os.PathLike[str] | str,
    token: AttemptToken,
    *,
    reason: str,
    observed_compressed_bytes: int,
) -> Mapping[str, Any]:
    """可选地闭合失败；attempt reservation 无论是否闭合都不会退款。"""

    root = Path(output_root)
    if not isinstance(reason, str) or not reason.strip() or reason != reason.strip():
        raise FullWindowJournalError("failure reason 非法")
    observed = _nonnegative(observed_compressed_bytes, "observed_compressed_bytes")
    consumed = _consumed_success_outcome_ref(root, token)
    if consumed is not None:
        _clear_active_attempt(
            root,
            expected_attempt_ref={"path": token.path, "sha256": token.sha256},
        )
        return consumed
    completed_ref = _complete_parse_outcome_ref(root, token)
    upper = token.artifact.size_bytes
    if observed > upper:
        raise FullWindowJournalError("observed_compressed_bytes 超过 artifact size")
    if completed_ref is not None and observed != upper:
        observed = upper
    terminal_ref = _write_terminal_outcome(
        root,
        token,
        outcome=(
            "publication_failed_after_complete_single_pass"
            if completed_ref is not None
            else "failed_before_complete_single_pass"
        ),
        reason=reason,
        observed_state="exact",
        observed=observed,
        lower_bound=observed,
        upper_bound=observed,
        completed_parse_outcome_ref=completed_ref,
    )
    _clear_active_attempt(
        root, expected_attempt_ref={"path": token.path, "sha256": token.sha256}
    )
    return terminal_ref


def _outcome_ref(path: Path, root: Path) -> Mapping[str, str]:
    _encoded, digest = _read_regular(path, maximum_bytes=1024 * 1024)
    return {"path": path.relative_to(root).as_posix(), "sha256": digest}


def _consumed_success_outcome_ref(
    root: Path, token: AttemptToken
) -> Optional[Mapping[str, str]]:
    attempt = _verify_fingerprint(
        _read_json(
            root / token.path,
            maximum_bytes=1024 * 1024,
            expected_sha=token.sha256,
        ),
        ATTEMPT_START_SCHEMA_VERSION,
        "attempt start",
    )
    base_ref = _closed_ref(attempt.get("base_receipt_ref"), "base_receipt_ref")
    base, _base_path, _base_sha = _load_receipt(root, base_ref)
    next_sequence = int(base["sequence"]) + 1
    expected_attempt_ref = {"path": token.path, "sha256": token.sha256}
    matches = []
    for candidate in sorted(
        (root / "receipts").glob(f"boundary-{next_sequence:04d}-*.json")
    ):
        receipt = _verify_fingerprint(
            _read_json(candidate, maximum_bytes=16 * 1024 * 1024),
            BOUNDARY_RECEIPT_SCHEMA_VERSION,
            "candidate consumed receipt",
        )
        if receipt.get("attempt_ref") == expected_attempt_ref:
            matches.append(
                _closed_ref(receipt.get("outcome_ref"), "consumed outcome_ref")
            )
    if len(matches) > 1:
        raise FullWindowJournalError("同一 attempt 被多个 receipt 消费")
    return matches[0] if matches else None


def _complete_parse_outcome_ref(
    root: Path, token: AttemptToken
) -> Optional[Mapping[str, str]]:
    matches = []
    expected_attempt_ref = {"path": token.path, "sha256": token.sha256}
    for candidate in sorted(
        (root / "raw-ledger/outcomes").glob(
            f"parse-outcome-{token.attempt_id}-*.json"
        )
    ):
        payload = _verify_fingerprint(
            _read_json(candidate, maximum_bytes=1024 * 1024),
            ATTEMPT_OUTCOME_SCHEMA_VERSION,
            "attempt outcome",
        )
        if (
            payload.get("attempt_id") != token.attempt_id
            or payload.get("attempt_ref") != expected_attempt_ref
            or payload.get("outcome") != "complete_single_pass"
        ):
            raise FullWindowJournalError("parse outcome 与 attempt 不闭合")
        matches.append(_outcome_ref(candidate, root))
    if len(matches) > 1:
        raise FullWindowJournalError("同一 attempt 存在多个 complete parse outcome")
    return matches[0] if matches else None


def _existing_terminal_outcome_ref(
    root: Path, token: AttemptToken
) -> Optional[Mapping[str, str]]:
    matches = []
    expected_attempt_ref = {"path": token.path, "sha256": token.sha256}
    for candidate in sorted(
        (root / "raw-ledger/outcomes").glob(
            f"terminal-outcome-{token.attempt_id}-*.json"
        )
    ):
        payload = _verify_fingerprint(
            _read_json(candidate, maximum_bytes=1024 * 1024),
            ATTEMPT_OUTCOME_SCHEMA_VERSION,
            "terminal attempt outcome",
        )
        if (
            payload.get("attempt_id") != token.attempt_id
            or payload.get("attempt_ref") != expected_attempt_ref
        ):
            raise FullWindowJournalError("terminal outcome 与 attempt 不闭合")
        matches.append(_outcome_ref(candidate, root))
    if len(matches) > 1:
        raise FullWindowJournalError("同一 attempt 存在多个 terminal outcome")
    return matches[0] if matches else None


def _write_terminal_outcome(
    root: Path,
    token: AttemptToken,
    *,
    outcome: str,
    reason: str,
    observed_state: str,
    observed: Optional[int],
    lower_bound: int,
    upper_bound: int,
    completed_parse_outcome_ref: Optional[Mapping[str, str]],
) -> Mapping[str, Any]:
    existing = _existing_terminal_outcome_ref(root, token)
    if existing is not None:
        return existing
    if outcome not in {
        "failed_before_complete_single_pass",
        "publication_failed_after_complete_single_pass",
    }:
        raise FullWindowJournalError("terminal outcome 类型非法")
    if observed_state not in {"exact", "unknown_after_process_termination"}:
        raise FullWindowJournalError("terminal observed state 非法")
    lower = _nonnegative(lower_bound, "observed lower bound")
    upper = _nonnegative(upper_bound, "observed upper bound")
    if lower > upper or upper > token.artifact.size_bytes:
        raise FullWindowJournalError("terminal observed interval 非法")
    if observed_state == "exact":
        exact = _nonnegative(observed, "observed_compressed_bytes")
        if lower != exact or upper != exact:
            raise FullWindowJournalError("terminal exact observed interval 不闭合")
    elif observed is not None or lower != 0 or upper != token.artifact.size_bytes:
        raise FullWindowJournalError("进程终止后的 observed interval 必须保守覆盖整制品")
    completed = (
        None
        if completed_parse_outcome_ref is None
        else _closed_ref(completed_parse_outcome_ref, "completed_parse_outcome_ref")
    )
    if (outcome == "publication_failed_after_complete_single_pass") != (
        completed is not None
    ):
        raise FullWindowJournalError("terminal outcome 与 complete parse ref 矛盾")
    payload = _fingerprinted(
        ATTEMPT_OUTCOME_SCHEMA_VERSION,
        {
            "attempt_ref": {"path": token.path, "sha256": token.sha256},
            "attempt_id": token.attempt_id,
            "outcome": outcome,
            "failure_reason": reason,
            "observed_compressed_bytes_state": observed_state,
            "observed_compressed_bytes": observed,
            "observed_compressed_bytes_lower_bound": lower,
            "observed_compressed_bytes_upper_bound": upper,
            "completed_parse_outcome_ref": completed,
            "reservation_refunded_bytes": 0,
        },
    )
    return _immutable_json(
        root,
        "raw-ledger/outcomes",
        f"terminal-outcome-{token.attempt_id}",
        payload,
    )


def _verify_single_pass(proof: SinglePassProof, artifact: ArtifactDescriptor) -> None:
    if not isinstance(proof, SinglePassProof):
        raise FullWindowJournalError("proof 必须是 SinglePassProof")
    if proof.status != "complete":
        raise FullWindowJournalError("UPDATE stream 未完整耗尽")
    if proof.compressed_file_sha256 != artifact.file_sha256:
        raise FullWindowJournalError("UPDATE stream SHA256 与 artifact 不一致")
    if proof.compressed_size_bytes != artifact.size_bytes or proof.compressed_bytes_read_observed != artifact.size_bytes:
        raise FullWindowJournalError("UPDATE stream 必须完整读取一次制品")
    if proof.compressed_read_passes != 1:
        raise FullWindowJournalError("UPDATE stream 必须严格 single pass")
    seconds = _positive_float(proof.process_seconds, "proof.process_seconds")
    if seconds >= DEFAULT_HARD_RUNTIME_SECONDS:
        raise FullWindowJournalError("单 artifact 进程达到 600 秒硬边界")
    peak = _nonnegative(proof.peak_temporary_bytes, "proof.peak_temporary_bytes")
    if peak >= DEFAULT_MAX_TEMPORARY_BYTES:
        raise FullWindowJournalError("单 artifact 临时空间达到 5GB 硬边界")
    if _nonnegative(proof.database_write_operations, "database_write_operations") != 0:
        raise FullWindowJournalError("检测到数据库写操作")


def _success_outcome(root: Path, token: AttemptToken, proof: SinglePassProof) -> Mapping[str, Any]:
    payload = _fingerprinted(
        ATTEMPT_OUTCOME_SCHEMA_VERSION,
        {
            "attempt_ref": {"path": token.path, "sha256": token.sha256},
            "attempt_id": token.attempt_id,
            "outcome": "complete_single_pass",
            "failure_reason": None,
            "observed_compressed_bytes": proof.compressed_bytes_read_observed,
            "reservation_refunded_bytes": 0,
            "proof": proof.to_dict(),
        },
    )
    return _immutable_json(
        root,
        "raw-ledger/outcomes",
        f"parse-outcome-{token.attempt_id}",
        payload,
    )


def _commit_artifact_boundary_locked(
    head: JournalHead,
    token: AttemptToken,
    *,
    proof: SinglePassProof,
    compact_state: Mapping[str, Any],
    shards: Sequence[ShardInput],
    crash_hook: Optional[CrashHook] = None,
    publication_gate: Optional[PublicationGate] = None,
) -> CommittedArtifact:
    """完整校验后发布一个槽；任何半 artifact 语义都不会进入 CURRENT。"""

    fresh = load_full_window_head(head.root, expected_bindings=head.receipt["bindings"])
    if fresh.receipt_sha256 != head.receipt_sha256:
        raise FullWindowJournalError("CURRENT 已变化，拒绝基于旧 head 提交")
    artifact = token.artifact
    _validate_artifact(artifact)
    if artifact.index != fresh.next_artifact_index:
        raise FullWindowJournalError("attempt artifact 与当前 cursor 不一致")
    attempt_payload = _verify_fingerprint(
        _read_json(head.root / token.path, maximum_bytes=1024 * 1024, expected_sha=token.sha256),
        ATTEMPT_START_SCHEMA_VERSION,
        "attempt start",
    )
    if attempt_payload.get("attempt_id") != token.attempt_id or attempt_payload.get("artifact") != artifact.to_dict():
        raise FullWindowJournalError("attempt token 与 durable receipt 不一致")
    if attempt_payload.get("base_receipt_ref") != {"path": fresh.receipt_path, "sha256": fresh.receipt_sha256}:
        raise FullWindowJournalError("attempt 不是从当前成功边界授权")
    _verify_single_pass(proof, artifact)
    if not isinstance(compact_state, Mapping):
        raise FullWindowJournalError("compact_state 必须是对象")
    if isinstance(shards, (str, bytes)) or not isinstance(shards, Sequence):
        raise FullWindowJournalError("shards 必须是 ShardInput 序列")
    kinds = tuple(shard.kind for shard in shards)
    if len(kinds) != len(set(kinds)):
        raise FullWindowJournalError("同一槽 shard kind 不得重复")

    if publication_gate is not None:
        publication_gate("before_shards")
    outcome_ref = _success_outcome(head.root, token, proof)
    published_shards = []
    for shard in shards:
        published_shards.append(_publish_shard(head.root, artifact, shard))
        if publication_gate is not None:
            publication_gate(f"after_shard:{shard.kind}")
    shard_refs = tuple(
        sorted(
            published_shards,
            key=lambda row: (str(row["kind"]), str(row["path"])),
        )
    )
    chain = _advance_chain(fresh.shard_chain_sha256, artifact, shard_refs)
    bootstrap, observed, samples = _runtime_estimator(fresh)
    throughput = artifact.size_bytes / proof.process_seconds
    minimum_observed = throughput if observed is None else min(observed, throughput)
    next_sequence = fresh.sequence + 1
    inactive = "b" if fresh.receipt["state_ref"]["slot"] == "a" else "a"
    scratch_relative = f"scratch/state-{inactive}.jsonl.gz"
    scratch_payload = _fingerprinted(
        SCRATCH_SCHEMA_VERSION,
        {
            "run_id": fresh.receipt["run_id"],
            "bindings": fresh.receipt["bindings"],
            "sequence": next_sequence,
            "next_artifact_index": artifact.index + 1,
            "total_artifacts": fresh.receipt["total_artifacts"],
            "active_scratch_slot": inactive,
            "compact_state": dict(compact_state),
            "runtime_estimator": {
                "bootstrap_bytes_per_second": bootstrap,
                "minimum_observed_bytes_per_second": minimum_observed,
                "sample_count": samples + 1,
            },
            "shard_chain_sha256": chain,
        },
    )
    if publication_gate is not None:
        publication_gate("before_scratch")
    scratch_sha, _scratch_size = _atomic_scratch_gzip(head.root / scratch_relative, scratch_payload)
    if publication_gate is not None:
        publication_gate("after_scratch")
    if crash_hook is not None:
        crash_hook("after_scratch_publish")

    receipt = _fingerprinted(
        BOUNDARY_RECEIPT_SCHEMA_VERSION,
        {
            "run_id": fresh.receipt["run_id"],
            "bindings": fresh.receipt["bindings"],
            "sequence": next_sequence,
            "next_artifact_index": artifact.index + 1,
            "total_artifacts": fresh.receipt["total_artifacts"],
            "committed_artifact": artifact.to_dict(),
            "attempt_ref": {"path": token.path, "sha256": token.sha256},
            "outcome_ref": dict(outcome_ref),
            "state_ref": {"slot": inactive, "path": scratch_relative, "sha256": scratch_sha},
            "shards": [dict(item) for item in shard_refs],
            "shard_chain_sha256": chain,
            "previous_receipt_ref": {"path": fresh.receipt_path, "sha256": fresh.receipt_sha256},
            "raw_genesis_ref": fresh.receipt["raw_genesis_ref"],
        },
    )
    receipt_ref = _immutable_json(head.root, "receipts", f"boundary-{next_sequence:04d}", receipt)
    if crash_hook is not None:
        crash_hook("after_receipt_publish")
    current = _fingerprinted(
        CURRENT_SCHEMA_VERSION,
        {
            "run_id": fresh.receipt["run_id"],
            "sequence": next_sequence,
            "receipt_ref": dict(receipt_ref),
        },
    )
    _atomic_json(head.root / "CURRENT", current)
    if crash_hook is not None:
        crash_hook("after_current_publish")
    committed = load_full_window_head(
        head.root,
        expected_bindings=fresh.receipt["bindings"],
        recover_committed_successor=False,
    )
    if (
        committed.receipt_sha256 != receipt_ref["sha256"]
        or committed.receipt_path != receipt_ref["path"]
        or committed.receipt.get("attempt_ref")
        != {"path": token.path, "sha256": token.sha256}
    ):
        raise FullWindowJournalError("提交后 CURRENT 未指向本 attempt 的唯一 receipt")
    _clear_active_attempt(
        head.root,
        expected_attempt_ref={"path": token.path, "sha256": token.sha256},
    )
    return CommittedArtifact(committed, shard_refs, outcome_ref)


def commit_artifact_boundary(
    head: JournalHead,
    token: AttemptToken,
    *,
    proof: SinglePassProof,
    compact_state: Mapping[str, Any],
    shards: Sequence[ShardInput],
    crash_hook: Optional[CrashHook] = None,
    publication_gate: Optional[PublicationGate] = None,
) -> CommittedArtifact:
    """以单写者锁覆盖 fresh-check 到 CURRENT，拒绝 sibling receipt 分叉。"""

    if not isinstance(head, JournalHead):
        raise FullWindowJournalError("head 必须是 JournalHead")
    lock_path = head.root / "scratch/commit.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return _commit_artifact_boundary_locked(
            head,
            token,
            proof=proof,
            compact_state=compact_state,
            shards=shards,
            crash_hook=crash_hook,
            publication_gate=publication_gate,
        )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def frozen_journal_head(head: JournalHead) -> Mapping[str, Any]:
    """返回最终包可引用的冻结头；结果刻意不含可变 ``CURRENT``。"""

    verified_receipt_count = verify_full_receipt_ancestry(head)
    genesis = head.receipt
    genesis_ref = {"path": head.receipt_path, "sha256": head.receipt_sha256}
    while genesis["sequence"] > 0:
        genesis_ref = _closed_ref(
            genesis["previous_receipt_ref"], "previous_receipt_ref"
        )
        genesis, _path, _sha256 = _load_receipt(head.root, genesis_ref)
    return {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "run_id": head.receipt["run_id"],
        "bindings": head.receipt["bindings"],
        "completed_artifact_count": head.next_artifact_index,
        "total_artifacts": head.receipt["total_artifacts"],
        "terminal_receipt_ref": {
            "path": head.receipt_path,
            "sha256": head.receipt_sha256,
        },
        "genesis_receipt_ref": dict(genesis_ref),
        "genesis_seed_shards": [dict(row) for row in genesis["shards"]],
        "shard_chain_sha256": head.shard_chain_sha256,
        "verified_receipt_count": verified_receipt_count,
        "cumulative_reserved_raw_bytes": cumulative_reserved_raw_bytes(head.root),
        "scratch_is_evidence": False,
    }


__all__ = (
    "ArtifactAdmission",
    "ArtifactDescriptor",
    "AttemptToken",
    "CommittedArtifact",
    "DEFAULT_ADMISSION_SECONDS",
    "DEFAULT_HARD_RUNTIME_SECONDS",
    "DEFAULT_MAX_RAW_BYTES",
    "DEFAULT_MAX_TEMPORARY_BYTES",
    "DEFAULT_SOFT_STOP_SECONDS",
    "FullWindowJournalError",
    "JournalHead",
    "ShardInput",
    "SimulatedJournalCrash",
    "SinglePassProof",
    "begin_artifact_attempt",
    "commit_artifact_boundary",
    "cumulative_reserved_raw_bytes",
    "frozen_journal_head",
    "full_window_execution_lock",
    "initialize_full_window_journal",
    "load_full_window_head",
    "plan_artifact_admission",
    "record_attempt_failure",
    "reconcile_abandoned_active_attempt",
    "scratch_payload_sha256",
    "verify_full_receipt_ancestry",
)
