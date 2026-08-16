"""研究流水线的不可变文件制品与 record 边界检查点。

本模块只写调用方显式给出的研究输出目录，不认识原始 MRT 根目录、数据库或
生产路径。所有发布都采用同目录临时文件、``fsync`` 和 hard-link 的
create-if-absent 语义；目标已存在时拒绝覆盖。JSONL gzip 固定 ``mtime=0``、
空 filename 和规范 JSON，因此同一有序记录在不同空目录中产生相同字节。
"""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
from typing import Any, Iterable, Mapping, Sequence, Tuple


CHECKPOINT_SCHEMA_VERSION = "rrc25-country-outage-checkpoint/v1"
CHECKPOINT_FINGERPRINT_SCHEMA = "rrc25_country_outage_checkpoint_fingerprint_v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^research_run_v1_[0-9a-f]{24}$")


class ResearchArtifactError(ValueError):
    """研究制品不能按不可变、内容可核验语义发布或恢复。"""


@dataclass(frozen=True)
class PublishedArtifact:
    path: Path
    sha256: str
    size_bytes: int
    record_count: int
    kind: str


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ResearchArtifactError("研究制品包含不可规范序列化的 JSON 值") from error


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ResearchArtifactError(f"{field} 必须是 64 位小写十六进制")
    return value


def _nonnegative(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResearchArtifactError(f"{field} 必须是非负整数")
    return value


def _safe_relative(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResearchArtifactError(f"{field} 必须是非空相对路径")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
        raise ResearchArtifactError(f"{field} 必须是安全相对路径")
    return path.as_posix()


def _prepare_target(destination: os.PathLike[str] | str) -> Tuple[Path, Path]:
    target = Path(destination)
    parent = target.parent
    try:
        parent_meta = parent.lstat()
    except OSError as error:
        raise ResearchArtifactError("研究制品目标父目录不存在或不可读") from error
    if stat.S_ISLNK(parent_meta.st_mode) or not stat.S_ISDIR(parent_meta.st_mode):
        raise ResearchArtifactError("研究制品目标父路径必须是非符号链接目录")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"研究制品已存在，拒绝覆盖：{target}")
    temporary = parent / f".{target.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    return target, temporary


def _publish_temporary(temporary: Path, target: Path) -> None:
    try:
        os.link(temporary, target, follow_symlinks=False)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        raise FileExistsError(f"研究制品已存在，拒绝覆盖：{target}")
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _hash_regular(path: Path) -> Tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ResearchArtifactError("研究制品不是普通文件")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, name) != getattr(after, name) for name in identity):
            raise ResearchArtifactError("研究制品在哈希期间发生变化")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def write_canonical_json(
    destination: os.PathLike[str] | str,
    payload: Mapping[str, Any],
    *,
    kind: str,
    mode: int = 0o640,
) -> PublishedArtifact:
    """不可覆盖地发布一份规范 JSON（末尾恰有一个换行）。"""

    if not isinstance(payload, Mapping):
        raise ResearchArtifactError("JSON 制品 payload 必须是对象")
    if not isinstance(kind, str) or not kind:
        raise ResearchArtifactError("JSON 制品 kind 不能为空")
    target, temporary = _prepare_target(destination)
    encoded = (canonical_json(dict(payload)) + "\n").encode("utf-8")
    descriptor = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ResearchArtifactError("研究 JSON 制品写入未取得进展")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        digest, size = _hash_regular(temporary)
        _publish_temporary(temporary, target)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return PublishedArtifact(target, digest, size, 1, kind)


def write_canonical_jsonl_gzip(
    destination: os.PathLike[str] | str,
    records: Iterable[Mapping[str, Any]],
    *,
    kind: str,
    compresslevel: int = 9,
    mode: int = 0o640,
) -> PublishedArtifact:
    """把有序对象流发布为确定性 JSONL gzip 分片。"""

    if isinstance(records, (str, bytes, Mapping)):
        raise ResearchArtifactError("records 必须是对象可迭代流")
    if not isinstance(kind, str) or not kind:
        raise ResearchArtifactError("JSONL 制品 kind 不能为空")
    if isinstance(compresslevel, bool) or not isinstance(compresslevel, int) or not 0 <= compresslevel <= 9:
        raise ResearchArtifactError("compresslevel 必须是 0..9 整数")
    target, temporary = _prepare_target(destination)
    descriptor = None
    count = 0
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb", buffering=0) as raw:
            descriptor = None
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=compresslevel,
                fileobj=raw,
                mtime=0,
            ) as compressed:
                try:
                    iterator = iter(records)
                except TypeError as error:
                    raise ResearchArtifactError("records 必须可迭代") from error
                for index, record in enumerate(iterator):
                    if not isinstance(record, Mapping):
                        raise ResearchArtifactError(f"records[{index}] 必须是对象")
                    compressed.write((canonical_json(dict(record)) + "\n").encode("utf-8"))
                    count += 1
            raw.flush()
            os.fsync(raw.fileno())
        digest, size = _hash_regular(temporary)
        _publish_temporary(temporary, target)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return PublishedArtifact(target, digest, size, count, kind)


def _normalize_shard_refs(value: Any) -> Tuple[dict[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ResearchArtifactError("published_shards 必须是对象数组")
    normalized = []
    paths = set()
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise ResearchArtifactError(f"published_shards[{index}] 必须是对象")
        allowed = {"kind", "path", "sha256", "record_count"}
        if set(row) != allowed:
            raise ResearchArtifactError("published_shard 字段必须精确闭合")
        kind = row.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ResearchArtifactError("published_shard.kind 不能为空")
        path = _safe_relative(row.get("path"), "published_shard.path")
        if path in paths:
            raise ResearchArtifactError("published_shard.path 不得重复")
        paths.add(path)
        normalized.append(
            {
                "kind": kind,
                "path": path,
                "sha256": _sha256(row.get("sha256"), "published_shard.sha256"),
                "record_count": _nonnegative(
                    row.get("record_count"), "published_shard.record_count"
                ),
            }
        )
    return tuple(sorted(normalized, key=lambda row: (row["kind"], row["path"])))


def build_checkpoint(
    *,
    run_id: str,
    phase: str,
    profile_sha256: str,
    input_selection_sha256: str,
    code_sha256: str,
    mapping_sha256: str,
    artifact_id: str,
    next_record_ordinal: int,
    state_ref: Mapping[str, Any],
    published_shards: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """构造只允许从完整 physical record 边界恢复的内容寻址检查点。"""

    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise ResearchArtifactError("run_id 不符合 research_run_v1")
    if not isinstance(phase, str) or not re.fullmatch(r"^[a-z][a-z0-9._-]{0,63}$", phase):
        raise ResearchArtifactError("phase 非法")
    if not isinstance(artifact_id, str) or not re.fullmatch(r"^art_v1_[0-9a-f]{32}$", artifact_id):
        raise ResearchArtifactError("artifact_id 非法")
    if not isinstance(state_ref, Mapping) or set(state_ref) != {"path", "sha256"}:
        raise ResearchArtifactError("state_ref 必须精确包含 path/sha256")
    semantic = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "run_id": run_id,
        "phase": phase,
        "bindings": {
            "profile_sha256": _sha256(profile_sha256, "profile_sha256"),
            "input_selection_sha256": _sha256(
                input_selection_sha256, "input_selection_sha256"
            ),
            "code_sha256": _sha256(code_sha256, "code_sha256"),
            "mapping_sha256": _sha256(mapping_sha256, "mapping_sha256"),
        },
        "input_position": {
            "artifact_id": artifact_id,
            "next_record_ordinal": _nonnegative(
                next_record_ordinal, "next_record_ordinal"
            ),
            "boundary": "complete_physical_record",
        },
        "state_ref": {
            "path": _safe_relative(state_ref.get("path"), "state_ref.path"),
            "sha256": _sha256(state_ref.get("sha256"), "state_ref.sha256"),
        },
        "published_shards": list(_normalize_shard_refs(published_shards)),
    }
    fingerprint = hashlib.sha256(
        canonical_json(
            {"schema": CHECKPOINT_FINGERPRINT_SCHEMA, "checkpoint": semantic}
        ).encode("utf-8")
    ).hexdigest()
    return {**semantic, "checkpoint_fingerprint_sha256": fingerprint}


def verify_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    expected_bindings: Mapping[str, str],
) -> dict[str, Any]:
    """复核检查点内容指纹及配置/输入/代码/映射哈希绑定。"""

    if not isinstance(checkpoint, Mapping):
        raise ResearchArtifactError("checkpoint 必须是对象")
    payload = dict(checkpoint)
    fingerprint = payload.pop("checkpoint_fingerprint_sha256", None)
    if not isinstance(fingerprint, str) or SHA256_RE.fullmatch(fingerprint) is None:
        raise ResearchArtifactError("checkpoint fingerprint 非法")
    expected = hashlib.sha256(
        canonical_json(
            {"schema": CHECKPOINT_FINGERPRINT_SCHEMA, "checkpoint": payload}
        ).encode("utf-8")
    ).hexdigest()
    if fingerprint != expected:
        raise ResearchArtifactError("checkpoint 内容指纹不一致")
    required_top_level = {
        "schema_version",
        "run_id",
        "phase",
        "bindings",
        "input_position",
        "state_ref",
        "published_shards",
    }
    if set(payload) != required_top_level:
        raise ResearchArtifactError("checkpoint 顶层字段不闭合")
    if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ResearchArtifactError("checkpoint schema_version 不受支持")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise ResearchArtifactError("checkpoint.run_id 非法")
    phase = payload.get("phase")
    if not isinstance(phase, str) or re.fullmatch(
        r"^[a-z][a-z0-9._-]{0,63}$", phase
    ) is None:
        raise ResearchArtifactError("checkpoint.phase 非法")
    bindings = payload.get("bindings")
    if not isinstance(bindings, Mapping):
        raise ResearchArtifactError("checkpoint.bindings 缺失")
    required = {
        "profile_sha256",
        "input_selection_sha256",
        "code_sha256",
        "mapping_sha256",
    }
    if set(expected_bindings) != required or set(bindings) != required:
        raise ResearchArtifactError("checkpoint binding 字段不闭合")
    for field in sorted(required):
        if _sha256(expected_bindings[field], f"expected_bindings.{field}") != bindings[field]:
            raise ResearchArtifactError(f"checkpoint {field} 绑定不一致")
    position = payload.get("input_position")
    if (
        not isinstance(position, Mapping)
        or set(position) != {"artifact_id", "next_record_ordinal", "boundary"}
        or position.get("boundary") != "complete_physical_record"
    ):
        raise ResearchArtifactError("checkpoint 不是完整 physical record 边界")
    artifact_id = position.get("artifact_id")
    if not isinstance(artifact_id, str) or re.fullmatch(
        r"^art_v1_[0-9a-f]{32}$", artifact_id
    ) is None:
        raise ResearchArtifactError("checkpoint artifact_id 非法")
    _nonnegative(position.get("next_record_ordinal"), "next_record_ordinal")
    _normalize_shard_refs(payload.get("published_shards"))
    state_ref = payload.get("state_ref")
    if not isinstance(state_ref, Mapping) or set(state_ref) != {"path", "sha256"}:
        raise ResearchArtifactError("checkpoint.state_ref 字段不闭合")
    _safe_relative(state_ref.get("path"), "state_ref.path")
    _sha256(state_ref.get("sha256"), "state_ref.sha256")
    return dict(checkpoint)


__all__ = (
    "CHECKPOINT_SCHEMA_VERSION",
    "PublishedArtifact",
    "ResearchArtifactError",
    "build_checkpoint",
    "canonical_json",
    "verify_checkpoint",
    "write_canonical_json",
    "write_canonical_jsonl_gzip",
)
