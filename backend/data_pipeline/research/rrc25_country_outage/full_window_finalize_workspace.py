"""完整窗口最终化的可恢复、逐槽提交 workspace。

该模块只读取已经闭合的 full-window journal，不读取 MRT、不连接数据库。每个
UPDATE 槽在 ``ACTIVE`` 标记下独立验证，派生结果先写入内容寻址 segment，随后
发布 hash-chain receipt，最后才原子推进 ``HEAD``。进程在任意发布边界终止时，
恢复只会重做尚未发布 receipt 的槽；已经发布 receipt 但尚未推进 ``HEAD`` 的槽
会被 reconcile，而不会重复解压 record observation。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import time
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence, Tuple

from .country_impact import CountryMappingView
from .file_artifacts import canonical_json
from . import full_window_finalize as _finalizer
from . import full_window_journal as _journal
from .package_manifest import (
    ResearchPackageError,
    build_package_manifest,
    verify_published_package,
)
from .coordinator import DEFAULT_PRODUCTION_ROOTS, DEFAULT_PROTECTED_ROOTS


WORKSPACE_GENESIS_SCHEMA = "rrc25-full-window-finalization-workspace-genesis/v1"
WORKSPACE_HEAD_SCHEMA = "rrc25-full-window-finalization-workspace-head/v1"
WORKSPACE_ACTIVE_SCHEMA = "rrc25-full-window-finalization-workspace-active/v1"
WORKSPACE_SEGMENT_PAYLOAD_SCHEMA = (
    "rrc25-full-window-finalization-segment-payload/v1"
)
WORKSPACE_SEGMENT_RECEIPT_SCHEMA = (
    "rrc25-full-window-finalization-segment-receipt/v1"
)
WORKSPACE_DEEP_SEGMENT_RECEIPT_SCHEMA = (
    "rrc25-full-window-finalization-deep-segment-receipt/v1"
)
WORKSPACE_TERMINAL_SCHEMA = "rrc25-full-window-finalization-terminal/v1"
WORKSPACE_DEEP_VERIFICATION_SCHEMA = (
    "rrc25-full-window-finalization-deep-verification/v1"
)
WORKSPACE_ASSEMBLY_ACTIVE_SCHEMA = (
    "rrc25-full-window-finalization-assembly-active/v2"
)
WORKSPACE_ASSEMBLY_CHECKPOINT_SCHEMA = (
    "rrc25-full-window-finalization-assembly-checkpoint/v2"
)
WORKSPACE_ASSEMBLY_INDEX_SCHEMA = (
    "rrc25-full-window-finalization-segment-index/v1"
)
WORKSPACE_ASSEMBLY_METADATA_SCHEMA = (
    "rrc25-full-window-segment-assembly/v2"
)
WORKSPACE_PACKAGE_RESOURCE_SCHEMA = (
    "rrc25-full-window-segment-package-resource-receipt/v1"
)
WORKSPACE_REPRODUCTION_ACCEPTANCE_SCHEMA = (
    "rrc25-full-window-reproduction-acceptance/v2"
)
WORKSPACE_FINGERPRINT_SCHEMA = "rrc25_full_window_finalization_workspace_v1"
DEFAULT_CHILD_PLANNED_STOP_SECONDS = 420.0
DEFAULT_PARENT_TERM_SECONDS = 540.0
DEFAULT_PARENT_KILL_SECONDS = 590.0
DEFAULT_MAX_TEMPORARY_BYTES = 5_000_000_000
ASSEMBLY_METADATA_RESERVE_BYTES = 64 * 1024 * 1024
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4].resolve(strict=False)
_GLOBAL_MUTATION_PROTECTED_ROOTS = tuple(
    Path(value).expanduser().resolve(strict=False)
    for value in (*DEFAULT_PROTECTED_ROOTS, *DEFAULT_PRODUCTION_ROOTS)
)


class FullWindowFinalizeWorkspaceError(ValueError):
    """workspace 身份、状态机或资源闭合不成立。"""


class FinalizationWorkspaceLocked(FullWindowFinalizeWorkspaceError):
    """另一个最终化进程持有唯一执行锁。"""


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _reject_nearest_symlink(path: Path, field: str) -> None:
    """拒绝调用方可控的目标或最近既有父是符号链接。

    macOS 的 ``/var``、``/tmp`` 本身可能是系统级链接，因此只检查目标向上
    遇到的第一个既有条目；若调用方在可写树中插入链接，该链接必然是第一个
    既有条目并被拒绝。
    """

    candidate = path.expanduser().absolute()
    current = candidate
    while True:
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            parent = current.parent
            if parent == current:
                return
            current = parent
            continue
        except OSError as error:
            raise FullWindowFinalizeWorkspaceError(
                f"{field} 无法执行 mutation 路径安全检查：{current}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise FullWindowFinalizeWorkspaceError(
                f"{field} mutation 路径不得经过符号链接：{current}"
            )
        return


def _assert_global_mutation_path(
    path: os.PathLike[str] | str,
    field: str,
) -> Path:
    lexical = Path(path).expanduser().absolute()
    _reject_nearest_symlink(lexical, field)
    resolved = lexical.resolve(strict=False)
    for protected in _GLOBAL_MUTATION_PROTECTED_ROOTS:
        if _paths_overlap(resolved, protected):
            raise FullWindowFinalizeWorkspaceError(
                f"{field} 不得与受保护旧项目或生产根双向重叠：{protected}"
            )
    if _paths_overlap(resolved, _REPOSITORY_ROOT):
        raise FullWindowFinalizeWorkspaceError(
            f"{field} 不得与代码仓库双向重叠：{_REPOSITORY_ROOT}"
        )
    return resolved


def _assert_mutation_targets(
    targets: Sequence[Tuple[os.PathLike[str] | str, str]],
    *,
    source_roots: Sequence[os.PathLike[str] | str] = (),
) -> Tuple[Path, ...]:
    normalized_sources = tuple(
        Path(value).expanduser().resolve(strict=False)
        for value in source_roots
    )
    normalized_targets = []
    for raw, field in targets:
        target = _assert_global_mutation_path(raw, field)
        for source in normalized_sources:
            if _paths_overlap(target, source):
                raise FullWindowFinalizeWorkspaceError(
                    f"{field} 不得与 journal/raw/source root 双向重叠："
                    f"{source}"
                )
        normalized_targets.append(target)
    return tuple(normalized_targets)


@dataclass(frozen=True)
class FinalizationWorkspaceRun:
    workspace_root: Path
    completed_slots: int
    total_slots: int
    segment_slots_committed: int
    stop_reason: str
    sealed: bool
    terminal_path: Optional[Path]
    deep_verification_path: Optional[Path]


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _fingerprinted(schema: str, value: Mapping[str, Any]) -> dict[str, Any]:
    semantic = {"schema_version": schema, **dict(value)}
    return {
        **semantic,
        "fingerprint_sha256": _hash(
            {"schema": WORKSPACE_FINGERPRINT_SCHEMA, "payload": semantic}
        ),
    }


def _verify_fingerprinted(
    value: Any, schema: str, name: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FullWindowFinalizeWorkspaceError(f"{name} 必须是对象")
    semantic = dict(value)
    fingerprint = semantic.pop("fingerprint_sha256", None)
    if semantic.get("schema_version") != schema or fingerprint != _hash(
        {"schema": WORKSPACE_FINGERPRINT_SCHEMA, "payload": semantic}
    ):
        raise FullWindowFinalizeWorkspaceError(f"{name} schema/fingerprint 不闭合")
    return dict(value)


def _safe_relative(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise FullWindowFinalizeWorkspaceError(f"{field} 必须是相对路径")
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(
        part in {"", ".", ".."} for part in pure.parts
    ):
        raise FullWindowFinalizeWorkspaceError(f"{field} 不是安全相对路径")
    return pure.as_posix()


def _regular_bytes(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        initial = path.lstat()
    except OSError as error:
        raise FullWindowFinalizeWorkspaceError(f"文件不可读：{path}") from error
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise FullWindowFinalizeWorkspaceError(f"文件必须是普通文件：{path}")
    if initial.st_size > maximum_bytes:
        raise FullWindowFinalizeWorkspaceError(f"文件超过限制：{path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    chunks: list[bytes] = []
    digest_size = 0
    identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    try:
        before = os.fstat(descriptor)
        if any(getattr(initial, item) != getattr(before, item) for item in identity):
            raise FullWindowFinalizeWorkspaceError("文件在打开前发生变化")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest_size += len(block)
            if digest_size > maximum_bytes:
                raise FullWindowFinalizeWorkspaceError(f"文件超过限制：{path}")
            chunks.append(block)
        after = os.fstat(descriptor)
        if any(getattr(before, item) != getattr(after, item) for item in identity):
            raise FullWindowFinalizeWorkspaceError("文件在读取期间发生变化")
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def _decode_json(raw: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FullWindowFinalizeWorkspaceError(f"{name} 不是严格 JSON") from error
    if not isinstance(value, Mapping):
        raise FullWindowFinalizeWorkspaceError(f"{name} 顶层必须是对象")
    return dict(value)


def _load_json(path: Path, name: str) -> dict[str, Any]:
    return _decode_json(_regular_bytes(path, maximum_bytes=2_000_000_000), name)


def _validated_temporary_limit(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > DEFAULT_MAX_TEMPORARY_BYTES
    ):
        raise FullWindowFinalizeWorkspaceError(
            "临时空间排他上限必须位于 (0, 十进制 5GB]"
        )
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_plain_directory(path: Path, name: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise FullWindowFinalizeWorkspaceError(f"{name} 目录不可读：{path}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise FullWindowFinalizeWorkspaceError(f"{name} 必须是非符号链接目录：{path}")


def _unlink_regular_fsync(path: Path, name: str) -> None:
    """只退役已解析到的普通文件，并持久化目录项删除。"""

    _assert_global_mutation_path(path, name)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise FullWindowFinalizeWorkspaceError(f"{name} 无法检查：{path}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise FullWindowFinalizeWorkspaceError(f"{name} 必须是普通文件：{path}")
    path.unlink()
    _fsync_directory(path.parent)


def _write_fsync_file(path: Path, payload: bytes, *, mode: int) -> None:
    _assert_global_mutation_path(path, "create-only 写入目标")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FullWindowFinalizeWorkspaceError("create-only 写入没有进展")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_bytes(
    path: Path,
    payload: bytes,
    *,
    mode: int = 0o640,
    temporary_root: Optional[Path] = None,
    maximum_temporary_bytes: int = DEFAULT_MAX_TEMPORARY_BYTES,
) -> None:
    """以 temp+fsync+hard-link 原子、不可覆盖地发布普通文件。

    最终文件名只会在全部字节落盘后出现。进程若在发布前退出，只留下可安全
    退役的 ``.publish-tmp-*``；若在 link 后退出，最终文件已经完整且可验。
    """

    _assert_global_mutation_path(path, "create-only 发布目标")
    path.parent.mkdir(parents=True, exist_ok=True)
    _require_plain_directory(path.parent, "create-only 发布父")
    limit = _validated_temporary_limit(maximum_temporary_bytes)
    if len(payload) >= limit:
        raise FullWindowFinalizeWorkspaceError(
            f"发布 {path.name} 单文件达到十进制 5GB 排他边界"
        )
    if temporary_root is not None:
        _assert_temporary_budget(
            temporary_root,
            additional_bytes=len(payload),
            maximum_temporary_bytes=limit,
            phase=f"发布 {path.name}",
        )
    temporary = path.parent / (
        f".{path.name}.publish-tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    try:
        _write_fsync_file(temporary, payload, mode=mode)
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            raise FileExistsError(f"create-only 文件已存在：{path}")
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
            _fsync_directory(path.parent)
        except FileNotFoundError:
            pass


def _create_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    temporary_root: Optional[Path] = None,
    maximum_temporary_bytes: int = DEFAULT_MAX_TEMPORARY_BYTES,
) -> None:
    _create_bytes(
        path,
        (canonical_json(dict(value)) + "\n").encode("utf-8"),
        temporary_root=temporary_root,
        maximum_temporary_bytes=maximum_temporary_bytes,
    )


def _atomic_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    temporary_root: Optional[Path] = None,
    maximum_temporary_bytes: int = DEFAULT_MAX_TEMPORARY_BYTES,
) -> None:
    _assert_global_mutation_path(path, "原子推进目标")
    path.parent.mkdir(parents=True, exist_ok=True)
    _require_plain_directory(path.parent, "原子推进父")
    payload = (canonical_json(dict(value)) + "\n").encode("utf-8")
    limit = _validated_temporary_limit(maximum_temporary_bytes)
    if len(payload) >= limit:
        raise FullWindowFinalizeWorkspaceError(
            f"原子推进 {path.name} 单文件达到十进制 5GB 排他边界"
        )
    if temporary_root is not None:
        _assert_temporary_budget(
            temporary_root,
            additional_bytes=len(payload),
            maximum_temporary_bytes=limit,
            phase=f"原子推进 {path.name}",
        )
    temporary = path.parent / (
        f".{path.name}.replace-tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    try:
        _write_fsync_file(temporary, payload, mode=0o640)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _file_ref(root: Path, path: Path) -> dict[str, Any]:
    raw = _regular_bytes(path, maximum_bytes=2_000_000_000)
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }


def _load_ref(root: Path, ref: Mapping[str, Any], name: str) -> bytes:
    if not isinstance(ref, Mapping) or set(ref) != {"path", "sha256", "size_bytes"}:
        raise FullWindowFinalizeWorkspaceError(f"{name} ref 字段不闭合")
    relative = _safe_relative(ref.get("path"), f"{name}.path")
    size = ref.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise FullWindowFinalizeWorkspaceError(f"{name}.size_bytes 非法")
    raw = _regular_bytes(root / relative, maximum_bytes=max(1, size))
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != ref.get("sha256"):
        raise FullWindowFinalizeWorkspaceError(f"{name} hash/size 不一致")
    return raw


def _tree_size(root: Path) -> int:
    total = 0
    seen: set[tuple[int, int]] = set()
    if not root.exists() and not root.is_symlink():
        return 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise FullWindowFinalizeWorkspaceError("workspace 不得包含符号链接")
        if path.is_file():
            metadata = path.stat()
            identity = (metadata.st_dev, metadata.st_ino)
            if identity not in seen:
                total += metadata.st_size
                seen.add(identity)
    return total


def _assert_temporary_budget(
    root: Path,
    *,
    additional_bytes: int = 0,
    maximum_temporary_bytes: int = DEFAULT_MAX_TEMPORARY_BYTES,
    phase: str,
) -> int:
    limit = _validated_temporary_limit(maximum_temporary_bytes)
    if (
        isinstance(additional_bytes, bool)
        or not isinstance(additional_bytes, int)
        or additional_bytes < 0
    ):
        raise FullWindowFinalizeWorkspaceError("临时空间增量必须是非负整数")
    current = _tree_size(root)
    projected = current + additional_bytes
    if projected >= limit:
        raise FullWindowFinalizeWorkspaceError(
            f"{phase} 预计或实际临时空间达到十进制 5GB 排他边界"
        )
    return projected


def _retire_publish_temporaries(directory: Path, name: Optional[str] = None) -> int:
    """只退役本模块命名的未发布临时普通文件。"""

    _assert_global_mutation_path(directory, "publish temporary 退役目录")
    retired = 0
    if not directory.exists() or directory.is_symlink() or not directory.is_dir():
        return retired
    patterns = (
        (
            f".{name}.publish-tmp-*",
            f".{name}.replace-tmp-*",
            f".{name}.tmp-*",
        )
        if name is not None
        else (
            ".*.publish-tmp-*",
            ".*.replace-tmp-*",
            ".HEAD.tmp-*",
        )
    )
    candidates = {
        path for pattern in patterns for path in directory.glob(pattern)
    }
    for path in sorted(candidates):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise FullWindowFinalizeWorkspaceError(
                f"待退役 publish temporary 不是普通文件：{path}"
            )
        path.unlink()
        retired += 1
    if retired:
        _fsync_directory(directory)
    return retired


@contextmanager
def finalization_workspace_lock(
    workspace_root: os.PathLike[str] | str,
) -> Iterator[None]:
    root = Path(workspace_root)
    _assert_global_mutation_path(root, "finalization workspace")
    lock_path = root / "LOCK"
    descriptor = os.open(lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise FinalizationWorkspaceLocked("finalization workspace 已被占用") from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _load_genesis(root: Path) -> dict[str, Any]:
    return _verify_fingerprinted(
        _load_json(root / "GENESIS", "GENESIS"), WORKSPACE_GENESIS_SCHEMA, "GENESIS"
    )


def _load_head(root: Path) -> dict[str, Any]:
    return _verify_fingerprinted(
        _load_json(root / "HEAD", "HEAD"), WORKSPACE_HEAD_SCHEMA, "HEAD"
    )


def _workspace_temporary_limit(root: Path) -> int:
    genesis = _load_genesis(root)
    return _validated_temporary_limit(
        genesis.get(
            "maximum_temporary_bytes_exclusive",
            DEFAULT_MAX_TEMPORARY_BYTES,
        )
    )


def initialize_finalization_workspace(
    workspace_root: os.PathLike[str] | str,
    *,
    journal_root: os.PathLike[str] | str,
    bindings: Mapping[str, str],
    code_identity: Optional[Mapping[str, Any]] = None,
    study_id: str = "rrc25-iran-country-outage-20260227",
    incident_ref: str = "country_outage/2026-02-27+09:12:32/IR/1/r",
    maximum_temporary_bytes: int = DEFAULT_MAX_TEMPORARY_BYTES,
) -> Mapping[str, Any]:
    """create-only 初始化最终化 workspace；不解压 UPDATE observation。"""

    root = Path(workspace_root).absolute()
    source = Path(journal_root).absolute()
    _assert_mutation_targets(
        ((root, "finalization workspace"),),
        source_roots=(source,),
    )
    temporary_limit = _validated_temporary_limit(maximum_temporary_bytes)
    if root.exists() or root.is_symlink():
        raise FileExistsError("finalization workspace 已存在，拒绝覆盖")
    normalized_bindings = {str(key): str(value) for key, value in sorted(bindings.items())}
    try:
        head = _journal.load_full_window_head(
            source,
            expected_bindings=normalized_bindings,
            recover_committed_successor=False,
        )
    except (OSError, _journal.FullWindowJournalError) as error:
        raise FullWindowFinalizeWorkspaceError("完整 journal terminal head 非法") from error
    total = int(head.receipt.get("total_artifacts", -1))
    if total <= 0 or head.sequence != total:
        raise FullWindowFinalizeWorkspaceError("journal 尚未闭合全部 artifact")
    terminal_ref = {"path": head.receipt_path, "sha256": head.receipt_sha256}
    journal_receipts = _finalizer._journal_receipts(source, terminal_ref)
    if len(journal_receipts) != total + 1:
        raise FullWindowFinalizeWorkspaceError("journal receipt 数量与 terminal 不闭合")
    genesis_ref, journal_genesis = journal_receipts[0]
    try:
        _journal._validate_receipt_semantics(
            source,
            journal_genesis,
            receipt_path=str(genesis_ref["path"]),
            receipt_sha256=str(genesis_ref["sha256"]),
            expected_bindings=normalized_bindings,
        )
    except _journal.FullWindowJournalError as error:
        raise FullWindowFinalizeWorkspaceError("journal genesis 语义不闭合") from error
    seed_refs = {
        str(ref.get("kind")): ref
        for ref in journal_genesis.get("shards", ())
        if isinstance(ref, Mapping)
    }
    if set(seed_refs) != {
        "seed_bootstrap_attestation",
        "seed_route_events",
        "seed_raw_record_refs",
    }:
        raise FullWindowFinalizeWorkspaceError("journal genesis seed shards 不闭合")
    seed_rows = tuple(
        _finalizer._iter_shard_rows(source, seed_refs["seed_bootstrap_attestation"])
    )
    if len(seed_rows) != 1 or not isinstance(
        seed_rows[0].get("initial_compact_state"), Mapping
    ):
        raise FullWindowFinalizeWorkspaceError("seed initial compact state 缺失")
    terminal_runtime = head.scratch.get("runtime_estimator")
    bootstrap = (
        terminal_runtime.get("bootstrap_bytes_per_second")
        if isinstance(terminal_runtime, Mapping)
        else None
    )
    if (
        isinstance(bootstrap, bool)
        or not isinstance(bootstrap, (int, float))
        or not math.isfinite(float(bootstrap))
        or float(bootstrap) <= 0
    ):
        raise FullWindowFinalizeWorkspaceError("journal runtime bootstrap 非法")
    if code_identity is not None:
        _finalizer._validate_code_identity(
            code_identity, normalized_bindings["code_sha256"]
        )

    os.mkdir(root, 0o750)
    (root / "segments/payloads").mkdir(parents=True)
    (root / "segments/receipts").mkdir(parents=True)
    (root / "segments/deep-receipts").mkdir(parents=True)
    _create_bytes(
        root / "LOCK",
        b"rrc25 finalization workspace lock\n",
        temporary_root=root,
        maximum_temporary_bytes=temporary_limit,
    )
    genesis = _fingerprinted(
        WORKSPACE_GENESIS_SCHEMA,
        {
            "journal_root": str(source),
            "run_id": head.receipt.get("run_id"),
            "study_id": study_id,
            "incident_ref": incident_ref,
            "bindings": normalized_bindings,
            "code_identity_sha256": normalized_bindings["code_sha256"],
            "total_slots": total,
            "journal_genesis_receipt_ref": dict(genesis_ref),
            "journal_terminal_receipt_ref": terminal_ref,
            "journal_terminal_shard_chain_sha256": head.shard_chain_sha256,
            "journal_receipt_refs": [dict(ref) for ref, _receipt in journal_receipts],
            "initial_compact_state": dict(seed_rows[0]["initial_compact_state"]),
            "initial_runtime_estimator": {
                "bootstrap_bytes_per_second": float(bootstrap),
                "minimum_observed_bytes_per_second": None,
                "sample_count": 0,
            },
            "child_planned_stop_seconds": DEFAULT_CHILD_PLANNED_STOP_SECONDS,
            "parent_term_seconds": DEFAULT_PARENT_TERM_SECONDS,
            "parent_kill_seconds": DEFAULT_PARENT_KILL_SECONDS,
            "maximum_temporary_bytes_exclusive": temporary_limit,
            "database_write_operations": 0,
        },
    )
    _create_json(
        root / "GENESIS",
        genesis,
        temporary_root=root,
        maximum_temporary_bytes=temporary_limit,
    )
    deep_chain_genesis = _hash(
        {
            "schema": "rrc25_full_window_finalization_deep_chain_genesis_v1",
            "run_id": genesis["run_id"],
            "bindings": genesis["bindings"],
            "journal_terminal_receipt_ref": genesis["journal_terminal_receipt_ref"],
        }
    )
    initial_head = _fingerprinted(
        WORKSPACE_HEAD_SCHEMA,
        {
            "sequence": 0,
            "total_slots": total,
            "current_segment_receipt_ref": None,
            "current_deep_segment_receipt_ref": None,
            "segment_receipt_refs": [],
            "segment_payload_refs": [],
            "deep_segment_receipt_refs": [],
            "deep_chain_sha256": deep_chain_genesis,
            "next_compact_state": dict(seed_rows[0]["initial_compact_state"]),
            "next_runtime_estimator": dict(genesis["initial_runtime_estimator"]),
            "cumulative_package_bytes_read": 0,
            "cumulative_record_observation_bytes_read": 0,
            "cumulative_segment_payload_bytes": 0,
            "cumulative_finalization_seconds": 0.0,
            "maximum_slot_seconds": 0.0,
            "maximum_temporary_bytes": _tree_size(root),
            "database_write_operations": 0,
            "sealed": False,
            "terminal_ref": None,
            "deep_verification_ref": None,
        },
    )
    _create_json(
        root / "HEAD",
        initial_head,
        temporary_root=root,
        maximum_temporary_bytes=temporary_limit,
    )
    _assert_temporary_budget(
        root,
        maximum_temporary_bytes=temporary_limit,
        phase="finalization workspace 初始化",
    )
    return dict(initial_head)


def _segment_payload_ref(
    root: Path,
    sequence: int,
    payload: Mapping[str, Any],
    *,
    maximum_temporary_bytes: int,
) -> Mapping[str, Any]:
    encoded = gzip.compress(
        (canonical_json(dict(payload)) + "\n").encode("utf-8"),
        compresslevel=9,
        mtime=0,
    )
    digest = hashlib.sha256(encoded).hexdigest()
    path = root / "segments/payloads" / f"slot-{sequence:04d}-{digest}.json.gz"
    _create_bytes(
        path,
        encoded,
        temporary_root=root,
        maximum_temporary_bytes=maximum_temporary_bytes,
    )
    return _file_ref(root, path)


def _load_segment_payload(root: Path, ref: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = _load_ref(root, ref, "segment payload")
    try:
        decoded = gzip.decompress(raw)
    except (OSError, EOFError) as error:
        raise FullWindowFinalizeWorkspaceError("segment payload gzip 不完整") from error
    if not decoded.endswith(b"\n") or decoded.count(b"\n") != 1:
        raise FullWindowFinalizeWorkspaceError("segment payload 必须恰有一条记录")
    return _verify_fingerprinted(
        _decode_json(decoded[:-1], "segment payload"),
        WORKSPACE_SEGMENT_PAYLOAD_SCHEMA,
        "segment payload",
    )


def _segment_receipt_ref(
    root: Path,
    sequence: int,
    receipt: Mapping[str, Any],
    *,
    maximum_temporary_bytes: int,
) -> Mapping[str, Any]:
    encoded = (canonical_json(dict(receipt)) + "\n").encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    path = root / "segments/receipts" / f"slot-{sequence:04d}-{digest}.json"
    _create_bytes(
        path,
        encoded,
        temporary_root=root,
        maximum_temporary_bytes=maximum_temporary_bytes,
    )
    return _file_ref(root, path)


def _load_segment_receipt(root: Path, ref: Mapping[str, Any]) -> Mapping[str, Any]:
    return _verify_fingerprinted(
        _decode_json(_load_ref(root, ref, "segment receipt"), "segment receipt"),
        WORKSPACE_SEGMENT_RECEIPT_SCHEMA,
        "segment receipt",
    )


def _deep_segment_receipt_ref(
    root: Path,
    sequence: int,
    receipt: Mapping[str, Any],
    *,
    maximum_temporary_bytes: int,
) -> Mapping[str, Any]:
    encoded = (canonical_json(dict(receipt)) + "\n").encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    path = (
        root
        / "segments/deep-receipts"
        / f"slot-{sequence:04d}-{digest}.json"
    )
    _create_bytes(
        path,
        encoded,
        temporary_root=root,
        maximum_temporary_bytes=maximum_temporary_bytes,
    )
    return _file_ref(root, path)


def _load_deep_segment_receipt(
    root: Path, ref: Mapping[str, Any]
) -> Mapping[str, Any]:
    return _verify_fingerprinted(
        _decode_json(
            _load_ref(root, ref, "deep segment receipt"),
            "deep segment receipt",
        ),
        WORKSPACE_DEEP_SEGMENT_RECEIPT_SCHEMA,
        "deep segment receipt",
    )


def _deep_chain_advance(
    previous_sha256: str,
    *,
    sequence: int,
    segment_receipt_ref: Mapping[str, Any],
    segment_payload_ref: Mapping[str, Any],
    cumulative_resource_accounting: Mapping[str, Any],
    cumulative_segment_payload_bytes: int,
) -> str:
    return _hash(
        {
            "schema": "rrc25_full_window_finalization_deep_chain_advance_v1",
            "previous_deep_chain_sha256": previous_sha256,
            "sequence": sequence,
            "segment_receipt_ref": dict(segment_receipt_ref),
            "segment_payload_ref": dict(segment_payload_ref),
            "cumulative_resource_accounting": dict(
                cumulative_resource_accounting
            ),
            "cumulative_segment_payload_bytes": cumulative_segment_payload_bytes,
        }
    )


def _publish_deep_segment_receipt(
    root: Path,
    head: Mapping[str, Any],
    segment_receipt_ref: Mapping[str, Any],
    segment_receipt: Mapping[str, Any],
    *,
    maximum_temporary_bytes: int,
) -> Tuple[Mapping[str, Any], Mapping[str, Any]]:
    """逐槽验证 payload/receipt，并发布可恢复的 deep receipt 链节点。"""

    loaded_receipt = _load_segment_receipt(root, segment_receipt_ref)
    if loaded_receipt != segment_receipt:
        raise FullWindowFinalizeWorkspaceError(
            "deep checkpoint 读取的 segment receipt 与内存值不一致"
        )
    payload_ref = loaded_receipt.get("segment_payload_ref")
    payload = _load_segment_payload(root, payload_ref)
    sequence = int(loaded_receipt["sequence"])
    if (
        sequence != int(head["sequence"]) + 1
        or loaded_receipt.get("previous_segment_receipt_ref")
        != head.get("current_segment_receipt_ref")
        or payload.get("sequence") != sequence
        or payload.get("journal_receipt_ref")
        != loaded_receipt.get("journal_receipt_ref")
        or payload.get("state_ref_sha256_verified") is not True
    ):
        raise FullWindowFinalizeWorkspaceError(
            "deep checkpoint 的 segment/payload/HEAD 不闭合"
        )
    cumulative_payload = int(head["cumulative_segment_payload_bytes"]) + int(
        payload_ref["size_bytes"]
    )
    deep_chain = _deep_chain_advance(
        str(head["deep_chain_sha256"]),
        sequence=sequence,
        segment_receipt_ref=segment_receipt_ref,
        segment_payload_ref=payload_ref,
        cumulative_resource_accounting=loaded_receipt[
            "cumulative_resource_accounting"
        ],
        cumulative_segment_payload_bytes=cumulative_payload,
    )
    deep = _fingerprinted(
        WORKSPACE_DEEP_SEGMENT_RECEIPT_SCHEMA,
        {
            "sequence": sequence,
            "total_slots": head["total_slots"],
            "previous_deep_segment_receipt_ref": head[
                "current_deep_segment_receipt_ref"
            ],
            "previous_deep_chain_sha256": head["deep_chain_sha256"],
            "deep_chain_sha256": deep_chain,
            "segment_receipt_ref": dict(segment_receipt_ref),
            "segment_payload_ref": dict(payload_ref),
            "journal_receipt_ref": loaded_receipt["journal_receipt_ref"],
            "cumulative_resource_accounting": dict(
                loaded_receipt["cumulative_resource_accounting"]
            ),
            "cumulative_segment_payload_bytes": cumulative_payload,
            "verification_basis": (
                "single_slot_payload_receipt_and_state_ref_verified_before_head"
            ),
            "record_observation_reread_count": 0,
            "database_write_operations": 0,
        },
    )
    deep_ref = _deep_segment_receipt_ref(
        root,
        sequence,
        deep,
        maximum_temporary_bytes=maximum_temporary_bytes,
    )
    return deep_ref, deep


def _head_from_segment_receipt(
    previous_head: Mapping[str, Any],
    receipt_ref: Mapping[str, Any],
    receipt: Mapping[str, Any],
    deep_receipt_ref: Mapping[str, Any],
    deep_receipt: Mapping[str, Any],
    *,
    total: int,
) -> Mapping[str, Any]:
    sequence = int(receipt["sequence"])
    if (
        sequence != int(previous_head["sequence"]) + 1
        or deep_receipt.get("sequence") != sequence
        or deep_receipt.get("segment_receipt_ref") != receipt_ref
        or deep_receipt.get("segment_payload_ref")
        != receipt.get("segment_payload_ref")
        or deep_receipt.get("previous_deep_segment_receipt_ref")
        != previous_head.get("current_deep_segment_receipt_ref")
    ):
        raise FullWindowFinalizeWorkspaceError(
            "segment/deep receipt 无法原子推进 HEAD"
        )
    return _fingerprinted(
        WORKSPACE_HEAD_SCHEMA,
        {
            "sequence": sequence,
            "total_slots": total,
            "current_segment_receipt_ref": dict(receipt_ref),
            "current_deep_segment_receipt_ref": dict(deep_receipt_ref),
            "segment_receipt_refs": [
                *previous_head["segment_receipt_refs"],
                dict(receipt_ref),
            ],
            "segment_payload_refs": [
                *previous_head["segment_payload_refs"],
                dict(receipt["segment_payload_ref"]),
            ],
            "deep_segment_receipt_refs": [
                *previous_head["deep_segment_receipt_refs"],
                dict(deep_receipt_ref),
            ],
            "deep_chain_sha256": deep_receipt["deep_chain_sha256"],
            "next_compact_state": dict(receipt["next_compact_state"]),
            "next_runtime_estimator": dict(receipt["next_runtime_estimator"]),
            **dict(receipt["cumulative_resource_accounting"]),
            "cumulative_segment_payload_bytes": deep_receipt[
                "cumulative_segment_payload_bytes"
            ],
            "sealed": False,
            "terminal_ref": None,
            "deep_verification_ref": None,
        },
    )


def _retire_uncommitted_slot(
    root: Path, sequence: int, *, include_receipts: bool = False
) -> int:
    retired = 0
    candidate_groups = [
        tuple(
            sorted(
                (root / "segments/payloads").glob(
                    f"slot-{sequence:04d}-*.json.gz"
                )
            )
        ),
        tuple(
            sorted(
                (root / "segments/deep-receipts").glob(
                    f"slot-{sequence:04d}-*.json"
                )
            )
        ),
    ]
    if include_receipts:
        candidate_groups.append(
            tuple(
                sorted(
                    (root / "segments/receipts").glob(
                        f"slot-{sequence:04d}-*.json"
                    )
                )
            )
        )
    for candidates in candidate_groups:
        for path in candidates:
            _unlink_regular_fsync(path, "未提交槽候选")
            retired += 1
    for directory in (
        root,
        root / "segments/payloads",
        root / "segments/receipts",
        root / "segments/deep-receipts",
    ):
        retired += _retire_publish_temporaries(directory)
    if retired:
        _fsync_directory(root)
    return retired


def _reconcile_locked(root: Path) -> Mapping[str, Any]:
    temporary_limit = _workspace_temporary_limit(root)
    retired_temporaries = sum(
        _retire_publish_temporaries(directory)
        for directory in (
            root,
            root / "segments/payloads",
            root / "segments/receipts",
            root / "segments/deep-receipts",
        )
    )
    active_path = root / "ACTIVE"
    if not active_path.exists() and not active_path.is_symlink():
        head = _load_head(root)
        _assert_temporary_budget(
            root,
            maximum_temporary_bytes=temporary_limit,
            phase="finalization workspace reconcile",
        )
        return {
            "state": "clean",
            "head": head,
            "retired_files": retired_temporaries,
        }
    active = _verify_fingerprinted(
        _load_json(active_path, "ACTIVE"), WORKSPACE_ACTIVE_SCHEMA, "ACTIVE"
    )
    head = _load_head(root)
    sequence = active.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
        raise FullWindowFinalizeWorkspaceError("ACTIVE sequence 非法")
    if head["sequence"] >= sequence:
        _unlink_regular_fsync(active_path, "ACTIVE")
        return {
            "state": "active_retired_after_head_publish",
            "head": head,
            "retired_files": retired_temporaries,
        }
    if head["sequence"] != sequence - 1:
        raise FullWindowFinalizeWorkspaceError("ACTIVE 与 HEAD sequence 不连续")
    candidates = sorted(
        (root / "segments/receipts").glob(f"slot-{sequence:04d}-*.json")
    )
    if len(candidates) > 1:
        raise FullWindowFinalizeWorkspaceError("同一槽存在多个 segment receipt")
    if candidates:
        try:
            ref = _file_ref(root, candidates[0])
            receipt = _load_segment_receipt(root, ref)
            if (
                receipt.get("sequence") != sequence
                or receipt.get("previous_segment_receipt_ref")
                != head.get("current_segment_receipt_ref")
            ):
                raise FullWindowFinalizeWorkspaceError(
                    "reconcile segment receipt 链不闭合"
                )
            _load_segment_payload(root, receipt["segment_payload_ref"])
        except (OSError, FullWindowFinalizeWorkspaceError):
            retired = _retire_uncommitted_slot(
                root, sequence, include_receipts=True
            )
            _unlink_regular_fsync(active_path, "ACTIVE")
            return {
                "state": "torn_uncommitted_slot_retired_for_redo",
                "head": head,
                "retired_files": retired_temporaries + retired,
            }
        deep_candidates = sorted(
            (root / "segments/deep-receipts").glob(
                f"slot-{sequence:04d}-*.json"
            )
        )
        if len(deep_candidates) > 1:
            raise FullWindowFinalizeWorkspaceError(
                "同一槽存在多个 deep segment receipt"
            )
        if deep_candidates:
            try:
                deep_ref = _file_ref(root, deep_candidates[0])
                deep = _load_deep_segment_receipt(root, deep_ref)
                expected_deep_chain = _deep_chain_advance(
                    str(head["deep_chain_sha256"]),
                    sequence=sequence,
                    segment_receipt_ref=ref,
                    segment_payload_ref=receipt["segment_payload_ref"],
                    cumulative_resource_accounting=receipt[
                        "cumulative_resource_accounting"
                    ],
                    cumulative_segment_payload_bytes=(
                        int(head["cumulative_segment_payload_bytes"])
                        + int(receipt["segment_payload_ref"]["size_bytes"])
                    ),
                )
                if (
                    deep.get("segment_receipt_ref") != ref
                    or deep.get("previous_deep_segment_receipt_ref")
                    != head.get("current_deep_segment_receipt_ref")
                    or deep.get("deep_chain_sha256") != expected_deep_chain
                ):
                    raise FullWindowFinalizeWorkspaceError(
                        "reconcile deep segment receipt 链不闭合"
                    )
            except (OSError, FullWindowFinalizeWorkspaceError):
                retired = _retire_uncommitted_slot(
                    root, sequence, include_receipts=True
                )
                _unlink_regular_fsync(active_path, "ACTIVE")
                return {
                    "state": "torn_uncommitted_slot_retired_for_redo",
                    "head": head,
                    "retired_files": retired_temporaries + retired,
                }
        else:
            deep_ref, deep = _publish_deep_segment_receipt(
                root,
                head,
                ref,
                receipt,
                maximum_temporary_bytes=temporary_limit,
            )
        next_head = _head_from_segment_receipt(
            head,
            ref,
            receipt,
            deep_ref,
            deep,
            total=int(head["total_slots"]),
        )
        _atomic_json(
            root / "HEAD",
            next_head,
            temporary_root=root,
            maximum_temporary_bytes=temporary_limit,
        )
        _unlink_regular_fsync(active_path, "ACTIVE")
        return {
            "state": "receipt_reconciled_to_head",
            "head": next_head,
            "retired_files": retired_temporaries,
        }
    retired = _retire_uncommitted_slot(root, sequence)
    _unlink_regular_fsync(active_path, "ACTIVE")
    return {
        "state": "uncommitted_slot_retired_for_redo",
        "head": head,
        "retired_files": retired_temporaries + retired,
    }


def reconcile_finalization_workspace(
    workspace_root: os.PathLike[str] | str,
) -> Mapping[str, Any]:
    root = Path(workspace_root).absolute()
    with finalization_workspace_lock(root):
        return _reconcile_locked(root)


def _receipt_chain(root: Path, head: Mapping[str, Any]) -> Tuple[Tuple[Mapping[str, Any], Mapping[str, Any]], ...]:
    rows = []
    ref = head.get("current_segment_receipt_ref")
    expected = int(head["sequence"])
    while ref is not None:
        receipt = _load_segment_receipt(root, ref)
        if receipt.get("sequence") != expected:
            raise FullWindowFinalizeWorkspaceError("segment receipt sequence 不连续")
        rows.append((dict(ref), receipt))
        ref = receipt.get("previous_segment_receipt_ref")
        expected -= 1
    rows.reverse()
    if expected != 0 or [row[1]["sequence"] for row in rows] != list(
        range(1, int(head["sequence"]) + 1)
    ):
        raise FullWindowFinalizeWorkspaceError("segment receipt ancestry 不闭合")
    return tuple(rows)


def _verify_segment_chain(root: Path, head: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = _receipt_chain(root, head)
    package_bytes = 0
    observation_bytes = 0
    previous = None
    maximum_slot_seconds = 0.0
    cumulative_seconds = 0.0
    maximum_temporary = 0
    for ref, receipt in rows:
        if receipt.get("previous_segment_receipt_ref") != previous:
            raise FullWindowFinalizeWorkspaceError("segment hash chain 前驱不一致")
        payload = _load_segment_payload(root, receipt["segment_payload_ref"])
        if (
            payload.get("sequence") != receipt.get("sequence")
            or payload.get("journal_receipt_ref") != receipt.get("journal_receipt_ref")
            or payload.get("state_ref_sha256_verified") is not True
        ):
            raise FullWindowFinalizeWorkspaceError("segment payload 与 receipt 不闭合")
        current = receipt.get("slot_resource_accounting")
        cumulative = receipt.get("cumulative_resource_accounting")
        if not isinstance(current, Mapping) or not isinstance(cumulative, Mapping):
            raise FullWindowFinalizeWorkspaceError("segment resource accounting 缺失")
        package_bytes += int(current["package_bytes_read"])
        observation_bytes += int(current["record_observation_bytes_read"])
        cumulative_seconds += float(current["finalization_seconds"])
        maximum_slot_seconds = max(
            maximum_slot_seconds, float(current["finalization_seconds"])
        )
        maximum_temporary = max(maximum_temporary, int(current["temporary_bytes"]))
        if (
            cumulative.get("cumulative_package_bytes_read") != package_bytes
            or cumulative.get("cumulative_record_observation_bytes_read")
            != observation_bytes
            or not math.isclose(
                float(cumulative.get("cumulative_finalization_seconds")),
                cumulative_seconds,
                rel_tol=0,
                abs_tol=1e-9,
            )
            or not math.isclose(
                float(cumulative.get("maximum_slot_seconds")),
                maximum_slot_seconds,
                rel_tol=0,
                abs_tol=1e-9,
            )
            or cumulative.get("maximum_temporary_bytes") != maximum_temporary
            or cumulative.get("database_write_operations") != 0
        ):
            raise FullWindowFinalizeWorkspaceError("segment cumulative resource chain 不闭合")
        previous = dict(ref)
    return {
        "rows": rows,
        "cumulative_package_bytes_read": package_bytes,
        "cumulative_record_observation_bytes_read": observation_bytes,
        "cumulative_finalization_seconds": cumulative_seconds,
        "maximum_slot_seconds": maximum_slot_seconds,
        "maximum_temporary_bytes": maximum_temporary,
        "database_write_operations": 0,
        "segment_payload_bytes_verified": sum(
            int(receipt["segment_payload_ref"]["size_bytes"])
            for _ref, receipt in rows
        ),
    }


def _verify_terminal_deep_checkpoint(
    root: Path, head: Mapping[str, Any]
) -> Mapping[str, Any]:
    """常数槽读取地验证递增 deep receipt 终点，不重跑 1..N 整链。"""

    sequence = int(head.get("sequence", -1))
    if sequence <= 0:
        raise FullWindowFinalizeWorkspaceError("deep checkpoint 尚无已验证槽")
    segment_refs = head.get("segment_receipt_refs")
    payload_refs = head.get("segment_payload_refs")
    deep_refs = head.get("deep_segment_receipt_refs")
    if (
        not isinstance(segment_refs, list)
        or not isinstance(payload_refs, list)
        or not isinstance(deep_refs, list)
        or len(segment_refs) != sequence
        or len(payload_refs) != sequence
        or len(deep_refs) != sequence
        or head.get("current_segment_receipt_ref") != segment_refs[-1]
        or head.get("current_deep_segment_receipt_ref") != deep_refs[-1]
    ):
        raise FullWindowFinalizeWorkspaceError(
            "HEAD 的分段 deep checkpoint index 不闭合"
        )
    segment_ref = segment_refs[-1]
    receipt = _load_segment_receipt(root, segment_ref)
    payload_ref = payload_refs[-1]
    if receipt.get("segment_payload_ref") != payload_ref:
        raise FullWindowFinalizeWorkspaceError(
            "terminal segment receipt/payload ref 不闭合"
        )
    # payload 的语义与 state_ref 已在逐槽 deep receipt 发布前完成验证。日常
    # seal/verify 只核对内容寻址 ref，不再解压 payload；这样 1928 槽窗口的
    # 验真成本与槽数无关，同时末槽字节篡改仍会被 hash/size 捕获。
    _load_ref(root, payload_ref, "terminal segment payload receipt")
    deep_ref = deep_refs[-1]
    deep = _load_deep_segment_receipt(root, deep_ref)
    if (
        receipt.get("sequence") != sequence
        or receipt.get("state_ref_sha256_verified") is not True
        or deep.get("sequence") != sequence
        or deep.get("segment_receipt_ref") != segment_ref
        or deep.get("segment_payload_ref") != payload_ref
        or deep.get("deep_chain_sha256") != head.get("deep_chain_sha256")
        or deep.get("cumulative_resource_accounting")
        != {
            key: head[key]
            for key in (
                "cumulative_package_bytes_read",
                "cumulative_record_observation_bytes_read",
                "cumulative_finalization_seconds",
                "maximum_slot_seconds",
                "maximum_temporary_bytes",
                "database_write_operations",
            )
        }
        or deep.get("cumulative_segment_payload_bytes")
        != head.get("cumulative_segment_payload_bytes")
        or deep.get("record_observation_reread_count") != 0
        or deep.get("database_write_operations") != 0
        or deep.get("verification_basis")
        != "single_slot_payload_receipt_and_state_ref_verified_before_head"
    ):
        raise FullWindowFinalizeWorkspaceError(
            "terminal deep receipt 与 HEAD/segment/payload 不闭合"
        )
    return {
        "terminal_segment_receipt": receipt,
        "terminal_deep_segment_receipt": deep,
        "terminal_segment_receipt_ref": dict(segment_ref),
        "terminal_segment_payload_ref": dict(payload_ref),
        "terminal_deep_segment_receipt_ref": dict(deep_ref),
        "deep_chain_sha256": head["deep_chain_sha256"],
        "verified_segment_count": sequence,
        "segment_receipt_refs": [dict(ref) for ref in segment_refs],
        "segment_payload_refs": [dict(ref) for ref in payload_refs],
        "deep_segment_receipt_refs": [dict(ref) for ref in deep_refs],
        "cumulative_segment_payload_bytes": int(
            head["cumulative_segment_payload_bytes"]
        ),
        "segment_payload_decompression_count": 0,
        "record_observation_reread_count": 0,
    }


def _seal_locked(root: Path) -> Tuple[Path, Path]:
    genesis = _load_genesis(root)
    head = _load_head(root)
    if head["sequence"] != head["total_slots"]:
        raise FullWindowFinalizeWorkspaceError("workspace 尚未完成全部槽位")
    temporary_limit = _workspace_temporary_limit(root)
    verified = _verify_terminal_deep_checkpoint(root, head)
    resource_accounting = {
        key: head[key]
        for key in (
            "cumulative_package_bytes_read",
            "cumulative_record_observation_bytes_read",
            "cumulative_finalization_seconds",
            "maximum_slot_seconds",
            "maximum_temporary_bytes",
            "database_write_operations",
        )
    }
    terminal_path = root / "TERMINAL"
    expected_terminal = _fingerprinted(
        WORKSPACE_TERMINAL_SCHEMA,
        {
            "run_id": genesis["run_id"],
            "completed_slots": head["sequence"],
            "total_slots": genesis["total_slots"],
            "terminal_segment_receipt_ref": head["current_segment_receipt_ref"],
            "terminal_deep_segment_receipt_ref": head[
                "current_deep_segment_receipt_ref"
            ],
            "segment_receipt_refs": verified["segment_receipt_refs"],
            "segment_payload_refs": verified["segment_payload_refs"],
            "deep_segment_receipt_refs": verified[
                "deep_segment_receipt_refs"
            ],
            "deep_chain_sha256": verified["deep_chain_sha256"],
            "journal_terminal_receipt_ref": genesis[
                "journal_terminal_receipt_ref"
            ],
            "journal_terminal_shard_chain_sha256": genesis[
                "journal_terminal_shard_chain_sha256"
            ],
            "bindings": genesis["bindings"],
            "code_identity_sha256": genesis["code_identity_sha256"],
            "resource_accounting": resource_accounting,
            "database_access": "none",
            "sealed": True,
        },
    )
    if terminal_path.exists() or terminal_path.is_symlink():
        terminal = _verify_fingerprinted(
            _load_json(terminal_path, "TERMINAL"),
            WORKSPACE_TERMINAL_SCHEMA,
            "TERMINAL",
        )
        if terminal != expected_terminal:
            raise FullWindowFinalizeWorkspaceError(
                "既有 TERMINAL 与当前逐槽 deep checkpoint 不一致"
            )
    else:
        terminal = expected_terminal
        _create_json(
            terminal_path,
            terminal,
            temporary_root=root,
            maximum_temporary_bytes=temporary_limit,
        )
    terminal_ref = _file_ref(root, terminal_path)
    deep_path = root / "DEEP-VERIFICATION"
    expected_deep = _fingerprinted(
        WORKSPACE_DEEP_VERIFICATION_SCHEMA,
        {
            "terminal_ref": terminal_ref,
            "terminal_segment_receipt_ref": head[
                "current_segment_receipt_ref"
            ],
            "terminal_deep_segment_receipt_ref": head[
                "current_deep_segment_receipt_ref"
            ],
            "deep_chain_sha256": verified["deep_chain_sha256"],
            "verified_segment_count": head["sequence"],
            "bindings": genesis["bindings"],
            "code_identity_sha256": genesis["code_identity_sha256"],
            "segment_payload_bytes_read": verified[
                "cumulative_segment_payload_bytes"
            ],
            "cumulative_package_bytes_read": head[
                "cumulative_package_bytes_read"
            ],
            "cumulative_record_observation_bytes_read": head[
                "cumulative_record_observation_bytes_read"
            ],
            "maximum_slot_seconds": head["maximum_slot_seconds"],
            "cumulative_finalization_seconds": head[
                "cumulative_finalization_seconds"
            ],
            "maximum_temporary_bytes": head["maximum_temporary_bytes"],
            "database_write_operations": 0,
            "verification_basis": (
                "incremental_deep_segment_receipt_chain_without_full_chain_or_record_observation_reread"
            ),
        },
    )
    if deep_path.exists() or deep_path.is_symlink():
        deep = _verify_fingerprinted(
            _load_json(deep_path, "DEEP-VERIFICATION"),
            WORKSPACE_DEEP_VERIFICATION_SCHEMA,
            "DEEP-VERIFICATION",
        )
        if deep != expected_deep:
            raise FullWindowFinalizeWorkspaceError(
                "既有 DEEP-VERIFICATION 与当前 TERMINAL/checkpoint 不一致"
            )
    else:
        deep = expected_deep
        _create_json(
            deep_path,
            deep,
            temporary_root=root,
            maximum_temporary_bytes=temporary_limit,
        )
    deep_ref = _file_ref(root, deep_path)
    sealed_head = _fingerprinted(
        WORKSPACE_HEAD_SCHEMA,
        {
            **{
                key: value
                for key, value in head.items()
                if key not in {"schema_version", "fingerprint_sha256", "sealed", "terminal_ref", "deep_verification_ref"}
            },
            "sealed": True,
            "terminal_ref": terminal_ref,
            "deep_verification_ref": deep_ref,
        },
    )
    _atomic_json(
        root / "HEAD",
        sealed_head,
        temporary_root=root,
        maximum_temporary_bytes=temporary_limit,
    )
    return terminal_path, deep_path


def seal_finalization_workspace(
    workspace_root: os.PathLike[str] | str,
) -> Tuple[Path, Path]:
    root = Path(workspace_root).absolute()
    with finalization_workspace_lock(root):
        _reconcile_locked(root)
        return _seal_locked(root)


def run_finalization_workspace_segment(
    workspace_root: os.PathLike[str] | str,
    *,
    compatible_mapping: CountryMappingView,
    revised_mapping: CountryMappingView,
    max_slots: int = 1,
    planned_stop_seconds: float = DEFAULT_CHILD_PLANNED_STOP_SECONDS,
    maximum_temporary_bytes: Optional[int] = None,
    monotonic: Callable[[], float] = time.monotonic,
    crash_hook: Optional[Callable[[str, Path], None]] = None,
) -> FinalizationWorkspaceRun:
    """执行一个有界子进程段；420 秒后不再启动新槽。"""

    if isinstance(max_slots, bool) or not isinstance(max_slots, int) or max_slots <= 0:
        raise FullWindowFinalizeWorkspaceError("max_slots 必须是正整数")
    if not math.isfinite(planned_stop_seconds) or planned_stop_seconds <= 0:
        raise FullWindowFinalizeWorkspaceError("planned_stop_seconds 必须是正有限数")
    root = Path(workspace_root).absolute()
    started = monotonic()
    committed = 0
    stop_reason = "segment_slot_limit"
    with finalization_workspace_lock(root):
        genesis = _load_genesis(root)
        frozen_limit = _validated_temporary_limit(
            genesis["maximum_temporary_bytes_exclusive"]
        )
        temporary_limit = (
            frozen_limit
            if maximum_temporary_bytes is None
            else _validated_temporary_limit(maximum_temporary_bytes)
        )
        if temporary_limit > frozen_limit:
            raise FullWindowFinalizeWorkspaceError(
                "本次 finalization 临时上限不得放宽冻结的 5GB 门"
            )
        _assert_temporary_budget(
            root,
            maximum_temporary_bytes=temporary_limit,
            phase="finalization segment reconcile 前",
        )
        reconciled = _reconcile_locked(root)
        head = dict(reconciled["head"])
        _assert_temporary_budget(
            root,
            maximum_temporary_bytes=temporary_limit,
            phase="finalization segment 启动",
        )
        if head.get("sealed") is True:
            return FinalizationWorkspaceRun(
                root,
                int(head["sequence"]),
                int(head["total_slots"]),
                0,
                "already_sealed",
                True,
                root / "TERMINAL",
                root / "DEEP-VERIFICATION",
            )
        journal_refs = genesis["journal_receipt_refs"]
        for _index in range(max_slots):
            elapsed = monotonic() - started
            if not math.isfinite(elapsed) or elapsed < 0:
                raise FullWindowFinalizeWorkspaceError("segment monotonic 计时非法")
            if elapsed >= planned_stop_seconds:
                stop_reason = "child_planned_stop_before_new_slot"
                break
            if head["sequence"] >= head["total_slots"]:
                stop_reason = "all_slots_committed"
                break
            sequence = int(head["sequence"]) + 1
            active = _fingerprinted(
                WORKSPACE_ACTIVE_SCHEMA,
                {
                    "sequence": sequence,
                    "base_segment_receipt_ref": head[
                        "current_segment_receipt_ref"
                    ],
                    "journal_receipt_ref": dict(journal_refs[sequence]),
                    "publication_state": "slot_started_receipt_not_committed",
                },
            )
            _create_json(
                root / "ACTIVE",
                active,
                temporary_root=root,
                maximum_temporary_bytes=temporary_limit,
            )
            if crash_hook is not None:
                crash_hook("after_active_publish", root)
            slot_started = monotonic()
            derived = _finalizer.derive_finalization_slot_once(
                journal_root=genesis["journal_root"],
                receipt_ref=journal_refs[sequence],
                prior_compact_state=head["next_compact_state"],
                runtime_estimator=head["next_runtime_estimator"],
                compatible_mapping=compatible_mapping,
                revised_mapping=revised_mapping,
                expected_bindings=genesis["bindings"],
            )
            payload = _fingerprinted(
                WORKSPACE_SEGMENT_PAYLOAD_SCHEMA,
                {
                    key: value
                    for key, value in derived.items()
                    if key
                    not in {
                        "schema_version",
                        "next_compact_state",
                        "next_runtime_estimator",
                    }
                },
            )
            payload_ref = _segment_payload_ref(
                root,
                sequence,
                payload,
                maximum_temporary_bytes=temporary_limit,
            )
            if crash_hook is not None:
                crash_hook("after_segment_payload_publish", root)
            slot_seconds = monotonic() - slot_started
            if not math.isfinite(slot_seconds) or slot_seconds < 0:
                raise FullWindowFinalizeWorkspaceError("slot monotonic 计时非法")
            current_resource = {
                "package_bytes_read": int(
                    derived["resource_accounting"]["source_package_bytes_read"]
                ),
                "record_observation_bytes_read": int(
                    derived["resource_accounting"][
                        "record_observation_compressed_bytes_read"
                    ]
                ),
                "finalization_seconds": float(slot_seconds),
                "temporary_bytes": _tree_size(root),
                "database_write_operations": 0,
            }
            cumulative = {
                "cumulative_package_bytes_read": int(
                    head["cumulative_package_bytes_read"]
                )
                + current_resource["package_bytes_read"],
                "cumulative_record_observation_bytes_read": int(
                    head["cumulative_record_observation_bytes_read"]
                )
                + current_resource["record_observation_bytes_read"],
                "cumulative_finalization_seconds": float(
                    head["cumulative_finalization_seconds"]
                )
                + slot_seconds,
                "maximum_slot_seconds": max(
                    float(head["maximum_slot_seconds"]), slot_seconds
                ),
                "maximum_temporary_bytes": max(
                    int(head["maximum_temporary_bytes"]),
                    current_resource["temporary_bytes"],
                ),
                "database_write_operations": 0,
            }
            receipt = _fingerprinted(
                WORKSPACE_SEGMENT_RECEIPT_SCHEMA,
                {
                    "sequence": sequence,
                    "total_slots": head["total_slots"],
                    "previous_segment_receipt_ref": head[
                        "current_segment_receipt_ref"
                    ],
                    "journal_receipt_ref": dict(journal_refs[sequence]),
                    "journal_shard_chain_sha256": derived[
                        "journal_shard_chain_sha256"
                    ],
                    "artifact": dict(derived["artifact"]),
                    "segment_payload_ref": dict(payload_ref),
                    "next_compact_state": dict(derived["next_compact_state"]),
                    "next_runtime_estimator": dict(
                        derived["next_runtime_estimator"]
                    ),
                    "state_ref_sha256_verified": True,
                    "slot_resource_accounting": current_resource,
                    "cumulative_resource_accounting": cumulative,
                    "commit_semantics": "slot_payload_then_receipt_then_atomic_head",
                },
            )
            receipt_ref = _segment_receipt_ref(
                root,
                sequence,
                receipt,
                maximum_temporary_bytes=temporary_limit,
            )
            if crash_hook is not None:
                crash_hook("after_segment_receipt_publish", root)
            deep_receipt_ref, deep_receipt = _publish_deep_segment_receipt(
                root,
                head,
                receipt_ref,
                receipt,
                maximum_temporary_bytes=temporary_limit,
            )
            if crash_hook is not None:
                crash_hook("after_deep_segment_receipt_publish", root)
            head = dict(
                _head_from_segment_receipt(
                    head,
                    receipt_ref,
                    receipt,
                    deep_receipt_ref,
                    deep_receipt,
                    total=int(head["total_slots"]),
                )
            )
            _atomic_json(
                root / "HEAD",
                head,
                temporary_root=root,
                maximum_temporary_bytes=temporary_limit,
            )
            if crash_hook is not None:
                crash_hook("after_head_publish", root)
            _unlink_regular_fsync(root / "ACTIVE", "ACTIVE")
            committed += 1
        if head["sequence"] == head["total_slots"]:
            terminal, deep = _seal_locked(root)
            sealed = True
            stop_reason = "sealed_terminal"
        else:
            terminal = deep = None
            sealed = False
    return FinalizationWorkspaceRun(
        root,
        int(head["sequence"]),
        int(head["total_slots"]),
        committed,
        stop_reason,
        sealed,
        terminal,
        deep,
    )


def verify_finalization_workspace(
    workspace_root: os.PathLike[str] | str,
) -> Mapping[str, Any]:
    """receipt-only 核验 sealed 终点；不解压 segment/observation。"""

    root = Path(workspace_root).absolute()
    genesis = _load_genesis(root)
    head = _load_head(root)
    if head.get("sealed") is not True or head["sequence"] != genesis["total_slots"]:
        raise FullWindowFinalizeWorkspaceError("workspace 尚未 sealed")
    verified = _verify_terminal_deep_checkpoint(root, head)
    terminal = _verify_fingerprinted(
        _load_json(root / "TERMINAL", "TERMINAL"),
        WORKSPACE_TERMINAL_SCHEMA,
        "TERMINAL",
    )
    deep = _verify_fingerprinted(
        _load_json(root / "DEEP-VERIFICATION", "DEEP-VERIFICATION"),
        WORKSPACE_DEEP_VERIFICATION_SCHEMA,
        "DEEP-VERIFICATION",
    )
    terminal_ref = _file_ref(root, root / "TERMINAL")
    deep_ref = _file_ref(root, root / "DEEP-VERIFICATION")
    if (
        head.get("terminal_ref") != terminal_ref
        or head.get("deep_verification_ref") != deep_ref
        or terminal.get("completed_slots") != genesis["total_slots"]
        or terminal.get("total_slots") != genesis["total_slots"]
        or terminal.get("bindings") != genesis["bindings"]
        or terminal.get("code_identity_sha256") != genesis["code_identity_sha256"]
        or terminal.get("terminal_segment_receipt_ref")
        != head.get("current_segment_receipt_ref")
        or terminal.get("terminal_deep_segment_receipt_ref")
        != head.get("current_deep_segment_receipt_ref")
        or terminal.get("segment_receipt_refs")
        != head.get("segment_receipt_refs")
        or terminal.get("segment_payload_refs")
        != head.get("segment_payload_refs")
        or terminal.get("deep_segment_receipt_refs")
        != head.get("deep_segment_receipt_refs")
        or terminal.get("deep_chain_sha256") != head.get("deep_chain_sha256")
        or terminal.get("resource_accounting")
        != {
            key: head[key]
            for key in (
                "cumulative_package_bytes_read",
                "cumulative_record_observation_bytes_read",
                "cumulative_finalization_seconds",
                "maximum_slot_seconds",
                "maximum_temporary_bytes",
                "database_write_operations",
            )
        }
        or deep.get("terminal_ref") != terminal_ref
        or deep.get("verified_segment_count") != genesis["total_slots"]
        or deep.get("terminal_deep_segment_receipt_ref")
        != head.get("current_deep_segment_receipt_ref")
        or deep.get("deep_chain_sha256") != head.get("deep_chain_sha256")
        or deep.get("database_write_operations") != 0
        or deep.get("cumulative_record_observation_bytes_read")
        != head["cumulative_record_observation_bytes_read"]
    ):
        raise FullWindowFinalizeWorkspaceError("terminal/deep verification 与 HEAD 不闭合")
    return {
        "verified": True,
        "workspace_root": str(root),
        "completed_slots": genesis["total_slots"],
        "terminal_ref": terminal_ref,
        "deep_verification_ref": deep_ref,
        "terminal_segment_receipt_ref": head["current_segment_receipt_ref"],
        "bindings": dict(genesis["bindings"]),
        "code_identity_sha256": genesis["code_identity_sha256"],
        "resource_accounting": dict(terminal["resource_accounting"]),
        "segment_payload_bytes_verified": verified[
            "cumulative_segment_payload_bytes"
        ],
        "record_observation_reread_count": 0,
        "full_segment_chain_reread_count": 0,
    }


def _package_resource_receipt_path(package_root: Path) -> Path:
    return package_root.parent / (
        package_root.name + ".finalization-workspace-resource-receipt.json"
    )


def _content_ref(
    package_root: Path, relative: str, *, kind: str, record_count: int = 1
) -> Mapping[str, Any]:
    path = package_root / _safe_relative(relative, "package content path")
    ref = _file_ref(package_root, path)
    return {
        "kind": kind,
        **dict(ref),
        "record_count": record_count,
    }


def _copy_create_only(
    source: Path,
    destination: Path,
    *,
    temporary_root: Path,
    maximum_temporary_bytes: int,
) -> Mapping[str, Any]:
    raw = _regular_bytes(source, maximum_bytes=2_000_000_000)
    _retire_publish_temporaries(destination.parent, destination.name)
    if destination.exists() or destination.is_symlink():
        existing = _regular_bytes(destination, maximum_bytes=max(1, len(raw)))
        if existing != raw:
            raise FullWindowFinalizeWorkspaceError(
                f"可恢复 assembly 既有文件内容不一致：{destination}"
            )
    else:
        _create_bytes(
            destination,
            raw,
            temporary_root=temporary_root,
            maximum_temporary_bytes=maximum_temporary_bytes,
        )
    return _file_ref(temporary_root, destination)


def _copy_ref_create_only(
    source_root: Path,
    ref: Mapping[str, Any],
    destination_root: Path,
    *,
    maximum_temporary_bytes: int,
) -> Mapping[str, Any]:
    relative = _safe_relative(ref.get("path"), "segment package path")
    raw = _load_ref(source_root, ref, "assembly source segment")
    destination = destination_root / relative
    _retire_publish_temporaries(destination.parent, destination.name)
    if destination.exists() or destination.is_symlink():
        existing = _load_ref(
            destination_root,
            {
                "path": relative,
                "sha256": ref.get("sha256"),
                "size_bytes": ref.get("size_bytes"),
            },
            "可恢复 assembly destination segment",
        )
        if len(existing) != len(raw):
            raise FullWindowFinalizeWorkspaceError(
                "可恢复 assembly destination segment 长度漂移"
            )
    else:
        _create_bytes(
            destination,
            raw,
            temporary_root=destination_root,
            maximum_temporary_bytes=maximum_temporary_bytes,
        )
    copied = _file_ref(destination_root, destination)
    if copied != {
        "path": relative,
        "sha256": ref.get("sha256"),
        "size_bytes": ref.get("size_bytes"),
    }:
        raise FullWindowFinalizeWorkspaceError(
            "assembly copy 未保持 segment 内容身份"
        )
    return copied


def _create_or_verify_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    temporary_root: Path,
    maximum_temporary_bytes: int,
) -> Mapping[str, Any]:
    payload = (canonical_json(dict(value)) + "\n").encode("utf-8")
    _retire_publish_temporaries(path.parent, path.name)
    if path.exists() or path.is_symlink():
        existing = _regular_bytes(path, maximum_bytes=max(1, len(payload)))
        if existing != payload:
            raise FullWindowFinalizeWorkspaceError(
                f"可恢复 assembly 元数据内容不一致：{path}"
            )
    else:
        _create_bytes(
            path,
            payload,
            temporary_root=temporary_root,
            maximum_temporary_bytes=maximum_temporary_bytes,
        )
    return _file_ref(temporary_root, path)


def _assembly_checkpoint_path(workspace_root: Path) -> Path:
    return workspace_root / "ASSEMBLY-CHECKPOINT"


def _assembly_copy_plan(
    workspace_root: Path,
    terminal: Mapping[str, Any],
) -> Tuple[Mapping[str, Any], ...]:
    plan: list[Mapping[str, Any]] = []
    for relative, kind in (
        ("GENESIS", "workspace-genesis"),
        ("TERMINAL", "workspace-terminal"),
        ("DEEP-VERIFICATION", "workspace-deep-verification"),
    ):
        plan.append(
            {
                "kind": kind,
                "source_ref": _file_ref(
                    workspace_root, workspace_root / relative
                ),
            }
        )
    for refs, kind in (
        (terminal["segment_receipt_refs"], "finalization-segment-receipt"),
        (terminal["segment_payload_refs"], "finalization-segment-payload"),
        (
            terminal["deep_segment_receipt_refs"],
            "finalization-deep-segment-receipt",
        ),
    ):
        plan.extend(
            {"kind": kind, "source_ref": dict(ref)}
            for ref in refs
        )
    return tuple(plan)


def _build_complete_package_plan(
    workspace_root: Path,
    *,
    profile: Mapping[str, Any],
    source_fact_snapshot: Mapping[str, Any],
    incident_policy: Mapping[str, Any],
    compatible_mapping_snapshot: Mapping[str, Any],
    revised_mapping_snapshot: Mapping[str, Any],
    code_identity: Mapping[str, Any],
    input_selection: Mapping[str, Any],
    claim_inventory: Mapping[str, Any],
    bindings: Mapping[str, Any],
    maximum_projected_bytes: int,
) -> Any:
    """每次受监管 assembly 都独立执行 segment adapter + 完整业务计划。

    延迟导入避免 ``segment_product``/``segment_package`` 对 workspace receipt
    helper 的反向依赖形成模块初始化环。两个空目录不会共享内存中的 product 或
    plan；调用本 helper 两次就是两次独立的纯派生。
    """

    from .full_window_segment_product import (
        FullWindowSegmentProductError,
        build_segment_product_inputs,
        derive_business_outputs_from_segment_product,
    )
    from .full_window_segment_package import (
        FullWindowSegmentPackageError,
        build_full_window_segment_package_plan,
        verify_full_window_segment_package_plan,
    )

    try:
        product = build_segment_product_inputs(
            workspace_root,
            profile=profile,
            source_fact_snapshot=source_fact_snapshot,
            incident_policy=incident_policy,
            compatible_mapping_snapshot=compatible_mapping_snapshot,
            revised_mapping_snapshot=revised_mapping_snapshot,
            code_identity=code_identity,
            input_selection=input_selection,
            claim_inventory=claim_inventory,
            bindings=bindings,
        )
        business = derive_business_outputs_from_segment_product(product)
        plan = build_full_window_segment_package_plan(
            product,
            business,
            maximum_projected_bytes=maximum_projected_bytes,
        )
        verified = verify_full_window_segment_package_plan(plan)
    except (
        FullWindowSegmentProductError,
        FullWindowSegmentPackageError,
        _finalizer.FullWindowFinalizeError,
    ) as error:
        raise FullWindowFinalizeWorkspaceError(
            "完整 frozen business segment package plan 构建失败："
            f"{error}"
        ) from error
    if (
        verified.get("verified") is not True
        or verified.get("business_semantic_core_sha256")
        != plan.business_semantic_core_sha256
        or verified.get("finalization_segment_core_sha256")
        != plan.finalization_segment_core_sha256
        or verified.get("record_observation_reads") != 0
        or verified.get("real_mrt_raw_bytes_read") != 0
        or verified.get("database_write_operations") != 0
    ):
        raise FullWindowFinalizeWorkspaceError(
            "完整 segment package plan 未闭合双 core/零读取/零 DB 门"
        )
    return plan


def _package_plan_item_descriptor(item: Any) -> Mapping[str, Any]:
    return {
        "relative_path": str(item.relative_path),
        "kind": str(item.kind),
        "materialization": str(item.materialization),
        "sha256": str(item.sha256),
        "size_bytes": int(item.size_bytes),
        "record_count": int(item.record_count),
        "included_in_manifest": bool(item.included_in_manifest),
        "source_path": (
            str(item.source_path) if item.source_path is not None else None
        ),
    }


def _package_plan_sha256(plan: Any) -> str:
    return _hash(
        {
            "schema": "rrc25_full_window_complete_package_plan_identity_v1",
            "schema_version": plan.schema_version,
            "business_semantic_core_sha256": (
                plan.business_semantic_core_sha256
            ),
            "finalization_segment_core_sha256": (
                plan.finalization_segment_core_sha256
            ),
            "projected_regular_bytes": plan.projected_regular_bytes,
            "manifest": dict(plan.manifest),
            "items": [
                _package_plan_item_descriptor(item) for item in plan.items
            ],
        }
    )


def _assembly_plan_chain(plan: Any, count: int) -> str:
    items = tuple(plan.items)
    chain = _hash(
        {
            "schema": "rrc25_full_window_assembly_copy_chain_genesis_v2",
            "package_plan_sha256": _package_plan_sha256(plan),
            "total_copy_items": len(items),
        }
    )
    for index, item in enumerate(items[:count]):
        chain = _hash(
            {
                "schema": "rrc25_full_window_assembly_copy_chain_advance_v2",
                "previous_sha256": chain,
                "copy_index": index,
                "item": _package_plan_item_descriptor(item),
            }
        )
    return chain


def _assembly_checkpoint_value(
    *,
    staging: Path,
    terminal_sha256: str,
    plan: Any,
    completed_copy_items: int,
) -> Mapping[str, Any]:
    items = tuple(plan.items)
    last = (
        _package_plan_item_descriptor(items[completed_copy_items - 1])
        if completed_copy_items
        else None
    )
    return _fingerprinted(
        WORKSPACE_ASSEMBLY_CHECKPOINT_SCHEMA,
        {
            "staging_root": str(staging),
            "terminal_sha256": terminal_sha256,
            "package_plan_sha256": _package_plan_sha256(plan),
            "business_semantic_core_sha256": (
                plan.business_semantic_core_sha256
            ),
            "finalization_segment_core_sha256": (
                plan.finalization_segment_core_sha256
            ),
            "package_manifest_semantic_fingerprint_sha256": plan.manifest[
                "semantic_fingerprint_sha256"
            ],
            "total_copy_items": len(items),
            "completed_copy_items": completed_copy_items,
            "completed_regular_bytes": sum(
                int(item.size_bytes)
                for item in items[:completed_copy_items]
            ),
            "last_completed_plan_item": last,
            "copy_chain_sha256": _assembly_plan_chain(
                plan, completed_copy_items
            ),
            "record_observation_reads": 0,
            "database_write_operations": 0,
        },
    )


def _load_assembly_checkpoint(
    workspace_root: Path,
) -> Optional[Mapping[str, Any]]:
    path = _assembly_checkpoint_path(workspace_root)
    if not path.exists() and not path.is_symlink():
        return None
    return _verify_fingerprinted(
        _load_json(path, "ASSEMBLY-CHECKPOINT"),
        WORKSPACE_ASSEMBLY_CHECKPOINT_SCHEMA,
        "ASSEMBLY-CHECKPOINT",
    )


def _validate_assembly_checkpoint(
    workspace_root: Path,
    staging: Path,
    terminal_sha256: str,
    plan: Any,
) -> Mapping[str, Any]:
    items = tuple(plan.items)
    checkpoint = _load_assembly_checkpoint(workspace_root)
    if checkpoint is None:
        return _assembly_checkpoint_value(
            staging=staging,
            terminal_sha256=terminal_sha256,
            plan=plan,
            completed_copy_items=0,
        )
    count = checkpoint.get("completed_copy_items")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        or count > len(items)
        or checkpoint.get("staging_root") != str(staging)
        or checkpoint.get("terminal_sha256") != terminal_sha256
        or checkpoint.get("package_plan_sha256")
        != _package_plan_sha256(plan)
        or checkpoint.get("business_semantic_core_sha256")
        != plan.business_semantic_core_sha256
        or checkpoint.get("finalization_segment_core_sha256")
        != plan.finalization_segment_core_sha256
        or checkpoint.get(
            "package_manifest_semantic_fingerprint_sha256"
        )
        != plan.manifest["semantic_fingerprint_sha256"]
        or checkpoint.get("total_copy_items") != len(items)
        or checkpoint.get("completed_regular_bytes")
        != sum(int(item.size_bytes) for item in items[:count])
        or checkpoint.get("copy_chain_sha256")
        != _assembly_plan_chain(plan, count)
        or checkpoint.get("last_completed_plan_item")
        != (
            _package_plan_item_descriptor(items[count - 1])
            if count
            else None
        )
        or checkpoint.get("record_observation_reads") != 0
        or checkpoint.get("database_write_operations") != 0
    ):
        raise FullWindowFinalizeWorkspaceError(
            "ASSEMBLY-CHECKPOINT 与 staging/terminal/copy plan 不闭合"
        )
    if count:
        item = items[count - 1]
        _load_ref(
            staging,
            {
                "path": item.relative_path,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
            },
            "ASSEMBLY-CHECKPOINT last copied item",
        )
    return checkpoint


def _publish_or_advance_assembly_checkpoint(
    workspace_root: Path,
    checkpoint: Mapping[str, Any],
    *,
    maximum_temporary_bytes: int,
) -> None:
    path = _assembly_checkpoint_path(workspace_root)
    if path.exists() or path.is_symlink():
        _atomic_json(
            path,
            checkpoint,
            temporary_root=workspace_root,
            maximum_temporary_bytes=maximum_temporary_bytes,
        )
    else:
        _create_json(
            path,
            checkpoint,
            temporary_root=workspace_root,
            maximum_temporary_bytes=maximum_temporary_bytes,
        )


def _retire_assembly_checkpoint(workspace_root: Path) -> None:
    _retire_publish_temporaries(
        workspace_root, "ASSEMBLY-CHECKPOINT"
    )
    _unlink_regular_fsync(
        _assembly_checkpoint_path(workspace_root),
        "ASSEMBLY-CHECKPOINT",
    )


def _progress(
    hook: Optional[Callable[[str, Path], None]],
    phase: str,
    path: Path,
) -> None:
    if hook is not None:
        hook(phase, path)


def _workspace_semantic_core(
    terminal: Mapping[str, Any], deep: Mapping[str, Any]
) -> str:
    return _hash(
        {
            "schema": "rrc25_full_window_finalization_segment_semantic_core_v2",
            "completed_slots": terminal.get("completed_slots"),
            "total_slots": terminal.get("total_slots"),
            "terminal_segment_receipt_ref": terminal.get(
                "terminal_segment_receipt_ref"
            ),
            "segment_receipt_refs": terminal.get("segment_receipt_refs"),
            "journal_terminal_receipt_ref": terminal.get(
                "journal_terminal_receipt_ref"
            ),
            "journal_terminal_shard_chain_sha256": terminal.get(
                "journal_terminal_shard_chain_sha256"
            ),
            "bindings": terminal.get("bindings"),
            "code_identity_sha256": terminal.get("code_identity_sha256"),
            "deep_verification_fingerprint_sha256": deep.get(
                "fingerprint_sha256"
            ),
        }
    )


def _publish_or_verify_package_metadata(
    staging: Path,
    manifest: Mapping[str, Any],
    *,
    maximum_temporary_bytes: int,
) -> None:
    """可恢复地 create-only 发布 manifest/SHA256SUMS。

    ``publish_package_metadata`` 的单文件发布本身是原子的，但若进程恰好停在
    manifest 与 SHA256SUMS 之间，下一次不能再次调用其全有或全无入口。本
    helper 对两个确定性字节串分别执行“既有则精确核对，否则 create-only”。
    """

    manifest_bytes = (canonical_json(dict(manifest)) + "\n").encode("utf-8")
    rows = [
        (item["sha256"], item["path"])
        for item in manifest["contents"]
    ]
    rows.append(
        (
            hashlib.sha256(manifest_bytes).hexdigest(),
            "package-manifest.json",
        )
    )
    sums_bytes = "".join(
        f"{digest}  {path}\n"
        for digest, path in sorted(rows, key=lambda row: row[1])
    ).encode("utf-8")
    manifest_path = staging / "package-manifest.json"
    sums_path = staging / "SHA256SUMS"
    _retire_publish_temporaries(staging, manifest_path.name)
    _retire_publish_temporaries(staging, sums_path.name)
    for path, payload, name in (
        (manifest_path, manifest_bytes, "package-manifest.json"),
        (sums_path, sums_bytes, "SHA256SUMS"),
    ):
        if path.exists() or path.is_symlink():
            existing = _regular_bytes(path, maximum_bytes=max(1, len(payload)))
            if existing != payload:
                raise FullWindowFinalizeWorkspaceError(
                    f"可恢复 assembly {name} 与确定性内容不一致"
                )
        else:
            _create_bytes(
                path,
                payload,
                temporary_root=staging,
                maximum_temporary_bytes=maximum_temporary_bytes,
            )


def _materialize_complete_plan_item(
    staging: Path,
    item: Any,
    *,
    maximum_temporary_bytes: int,
) -> Mapping[str, Any]:
    """create-only 物化一个计划项并核验内容身份。

    generated 项使用 package plan 已冻结的确定性字节；copy 项只打开 adapter
    已核验的 source_path，绝不根据 journal 相对路径回退寻找 observation/MRT。
    """

    destination = staging / _safe_relative(
        item.relative_path, "complete package plan item path"
    )
    if item.generated_bytes is not None:
        if item.source_path is not None:
            raise FullWindowFinalizeWorkspaceError(
                "generated package plan item 不得同时含 source_path"
            )
        payload = item.generated_bytes
    else:
        if not isinstance(item.source_path, str):
            raise FullWindowFinalizeWorkspaceError(
                "copy package plan item 缺少绝对 source_path"
            )
        payload = _regular_bytes(
            Path(item.source_path),
            maximum_bytes=max(1, int(item.size_bytes)),
        )
    if (
        len(payload) != int(item.size_bytes)
        or hashlib.sha256(payload).hexdigest() != item.sha256
    ):
        raise FullWindowFinalizeWorkspaceError(
            f"complete package plan item SHA/size 漂移：{item.relative_path}"
        )
    _retire_publish_temporaries(destination.parent, destination.name)
    if destination.exists() or destination.is_symlink():
        existing = _regular_bytes(
            destination, maximum_bytes=max(1, int(item.size_bytes))
        )
        if existing != payload:
            raise FullWindowFinalizeWorkspaceError(
                f"可恢复完整 package 既有内容不一致：{item.relative_path}"
            )
    else:
        _create_bytes(
            destination,
            payload,
            temporary_root=staging,
            maximum_temporary_bytes=maximum_temporary_bytes,
        )
    observed = _file_ref(staging, destination)
    expected = {
        "path": item.relative_path,
        "sha256": item.sha256,
        "size_bytes": item.size_bytes,
    }
    if observed != expected:
        raise FullWindowFinalizeWorkspaceError(
            f"complete package plan item 发布身份不一致：{item.relative_path}"
        )
    return observed


def _build_assembly_staging(
    workspace_root: Path,
    staging: Path,
    *,
    package_plan: Any,
    maximum_temporary_bytes: int,
    progress_hook: Optional[Callable[[str, Path], None]] = None,
) -> Mapping[str, Any]:
    from .full_window_segment_package import (
        verify_full_window_segment_package_plan,
    )

    plan_verified = verify_full_window_segment_package_plan(package_plan)
    if (
        int(package_plan.projected_regular_bytes)
        >= maximum_temporary_bytes
        or plan_verified.get("record_observation_reads") != 0
        or plan_verified.get("real_mrt_raw_bytes_read") != 0
        or plan_verified.get("database_write_operations") != 0
    ):
        raise FullWindowFinalizeWorkspaceError(
            "完整 package plan 预检达到 5GB 排他边界或零读取门失败"
        )
    if staging.exists() or staging.is_symlink():
        _require_plain_directory(staging, "assembly staging")
    else:
        os.mkdir(staging, 0o750)
        _fsync_directory(staging.parent)
    _assert_temporary_budget(
        staging,
        maximum_temporary_bytes=maximum_temporary_bytes,
        phase="assembly staging 初始化",
    )
    terminal_sha = _file_ref(
        workspace_root, workspace_root / "TERMINAL"
    )["sha256"]
    checkpoint = _validate_assembly_checkpoint(
        workspace_root, staging, terminal_sha, package_plan
    )
    completed = int(checkpoint["completed_copy_items"])
    if _load_assembly_checkpoint(workspace_root) is None:
        _publish_or_advance_assembly_checkpoint(
            workspace_root,
            checkpoint,
            maximum_temporary_bytes=maximum_temporary_bytes,
        )
    for index, item in enumerate(package_plan.items):
        relative = _safe_relative(
            item.relative_path, "complete package plan path"
        )
        if index < completed:
            continue
        _progress(
            progress_hook,
            "before_assembly_copy",
            staging / relative,
        )
        copied = _materialize_complete_plan_item(
            staging,
            item,
            maximum_temporary_bytes=maximum_temporary_bytes,
        )
        if copied != {
            "path": item.relative_path,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
        }:
            raise FullWindowFinalizeWorkspaceError(
                "assembly checkpoint 前目标 ref 与完整计划不一致"
            )
        completed = index + 1
        checkpoint = _assembly_checkpoint_value(
            staging=staging,
            terminal_sha256=terminal_sha,
            plan=package_plan,
            completed_copy_items=completed,
        )
        _publish_or_advance_assembly_checkpoint(
            workspace_root,
            checkpoint,
            maximum_temporary_bytes=maximum_temporary_bytes,
        )
    observed_staging = _assert_temporary_budget(
        staging,
        maximum_temporary_bytes=maximum_temporary_bytes,
        phase="assembly staging 完成",
    )
    verified = _verify_workspace_assembled_package_receipt_only(
        staging,
        require_resource_receipt=False,
    )
    if (
        verified.get("manifest") != package_plan.manifest
        or verified.get("business_semantic_core_sha256")
        != package_plan.business_semantic_core_sha256
        or verified.get("finalization_segment_core_sha256")
        != package_plan.finalization_segment_core_sha256
    ):
        raise FullWindowFinalizeWorkspaceError(
            "已物化完整 package 与计划 manifest/双 core 不闭合"
        )
    return {
        "manifest": dict(package_plan.manifest),
        "business_semantic_core_sha256": (
            package_plan.business_semantic_core_sha256
        ),
        "finalization_segment_core_sha256": (
            package_plan.finalization_segment_core_sha256
        ),
        "staging_temporary_bytes": observed_staging,
        "record_observation_reads_during_assembly": 0,
        "real_mrt_raw_bytes_read_during_assembly": 0,
        "database_write_operations": 0,
    }


def _load_package_manifest_receipt_only(
    package_root: Path,
) -> Mapping[str, Any]:
    """只核对 manifest/SHA256SUMS 的自洽性，不遍历内容文件字节。"""

    raw = _regular_bytes(
        package_root / "package-manifest.json",
        maximum_bytes=16 * 1024 * 1024,
    )
    manifest = _decode_json(raw, "package-manifest.json")
    try:
        rebuilt = build_package_manifest(
            run_id=manifest.get("run_id"),
            study_id=manifest.get("study_id"),
            incident_ref=manifest.get("incident_ref"),
            execution_mode=manifest.get("execution_mode"),
            acceptance_state=manifest.get("acceptance_state"),
            bindings=manifest.get("bindings"),
            contents=manifest.get("contents"),
        )
    except (TypeError, ValueError, ResearchPackageError) as error:
        raise FullWindowFinalizeWorkspaceError(
            "package manifest receipt 语义非法"
        ) from error
    if manifest != rebuilt:
        raise FullWindowFinalizeWorkspaceError(
            "package manifest receipt 内容寻址身份不一致"
        )
    expected_sums = {
        item["path"]: item["sha256"] for item in rebuilt["contents"]
    }
    expected_sums["package-manifest.json"] = hashlib.sha256(raw).hexdigest()
    sums_raw = _regular_bytes(
        package_root / "SHA256SUMS",
        maximum_bytes=16 * 1024 * 1024,
    )
    try:
        lines = sums_raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise FullWindowFinalizeWorkspaceError(
            "SHA256SUMS receipt 不是 UTF-8"
        ) from error
    observed_sums: dict[str, str] = {}
    for line in lines:
        if len(line) < 67 or line[64:66] != "  ":
            raise FullWindowFinalizeWorkspaceError(
                "SHA256SUMS receipt 行格式非法"
            )
        digest, relative = line[:64], line[66:]
        if relative in observed_sums:
            raise FullWindowFinalizeWorkspaceError(
                "SHA256SUMS receipt 路径重复"
            )
        observed_sums[relative] = digest
    if observed_sums != expected_sums:
        raise FullWindowFinalizeWorkspaceError(
            "SHA256SUMS receipt 与 manifest 不闭合"
        )
    expected_paths = set(expected_sums) | {"SHA256SUMS"}
    observed_paths: set[str] = set()
    _require_plain_directory(package_root, "assembled package")
    for path in package_root.rglob("*"):
        if path.is_symlink():
            raise FullWindowFinalizeWorkspaceError(
                "assembled package receipt tree 不得包含符号链接"
            )
        if path.is_file():
            observed_paths.add(path.relative_to(package_root).as_posix())
        elif not path.is_dir():
            raise FullWindowFinalizeWorkspaceError(
                "assembled package receipt tree 包含非普通条目"
            )
    if observed_paths != expected_paths:
        raise FullWindowFinalizeWorkspaceError(
            "assembled package receipt tree 文件集合不闭合"
        )
    return rebuilt


def _manifest_content_ref(
    manifest: Mapping[str, Any], relative: str, kind: Optional[str]
) -> Mapping[str, Any]:
    matches = [
        row
        for row in manifest.get("contents", ())
        if isinstance(row, Mapping)
        and row.get("path") == relative
        and (kind is None or row.get("kind") == kind)
    ]
    if len(matches) != 1:
        raise FullWindowFinalizeWorkspaceError(
            f"manifest receipt 缺少唯一 {kind or 'any-kind'}:{relative}"
        )
    row = matches[0]
    return {
        "path": row["path"],
        "sha256": row["sha256"],
        "size_bytes": row["size_bytes"],
    }


def _verify_workspace_assembled_receipts(
    package_root: Path,
) -> Mapping[str, Any]:
    """日常常量语义验真：只读 receipt/metadata，不读 segment payload。"""

    manifest = _load_package_manifest_receipt_only(package_root)
    genesis = _verify_fingerprinted(
        _load_json(package_root / "GENESIS", "packaged GENESIS receipt"),
        WORKSPACE_GENESIS_SCHEMA,
        "packaged GENESIS receipt",
    )
    terminal = _verify_fingerprinted(
        _load_json(package_root / "TERMINAL", "packaged TERMINAL receipt"),
        WORKSPACE_TERMINAL_SCHEMA,
        "packaged TERMINAL receipt",
    )
    deep = _verify_fingerprinted(
        _load_json(
            package_root / "DEEP-VERIFICATION",
            "packaged DEEP-VERIFICATION receipt",
        ),
        WORKSPACE_DEEP_VERIFICATION_SCHEMA,
        "packaged DEEP-VERIFICATION receipt",
    )
    index = _verify_fingerprinted(
        _load_json(package_root / "segments/index.json", "segment index receipt"),
        WORKSPACE_ASSEMBLY_INDEX_SCHEMA,
        "segment index receipt",
    )
    metadata = _verify_fingerprinted(
        _load_json(
            package_root / "metadata/finalization.json",
            "finalization receipt",
        ),
        WORKSPACE_ASSEMBLY_METADATA_SCHEMA,
        "finalization receipt",
    )
    quality = _load_json(
        package_root / "quality-and-accounting.json",
        "quality-and-accounting receipt",
    )
    bindings = _load_json(
        package_root / "frozen/bindings.json", "bindings receipt"
    )
    terminal_ref = _file_ref(package_root, package_root / "TERMINAL")
    deep_ref = _file_ref(package_root, package_root / "DEEP-VERIFICATION")
    semantic_core = _workspace_semantic_core(terminal, deep)
    business_core = metadata.get("business_semantic_core_sha256")
    if (
        not isinstance(business_core, str)
        or len(business_core) != 64
        or any(character not in "0123456789abcdef" for character in business_core)
    ):
        raise FullWindowFinalizeWorkspaceError(
            "assembled package 缺少业务 semantic core"
        )
    from .full_window_segment_package import REQUIRED_BUSINESS_PATHS

    manifest_paths = {
        str(item.get("path"))
        for item in manifest.get("contents", ())
        if isinstance(item, Mapping)
    }
    if not REQUIRED_BUSINESS_PATHS <= manifest_paths:
        raise FullWindowFinalizeWorkspaceError(
            "assembled package manifest 缺少完整业务人口"
        )
    total = terminal.get("total_slots")
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
        raise FullWindowFinalizeWorkspaceError("TERMINAL total_slots 非法")
    receipt_sets = (
        (
            terminal.get("segment_receipt_refs"),
            "finalization-segment-receipt",
        ),
        (
            terminal.get("segment_payload_refs"),
            "finalization-segment-payload",
        ),
        (
            terminal.get("deep_segment_receipt_refs"),
            "finalization-deep-segment-receipt",
        ),
    )
    for refs, kind in receipt_sets:
        if not isinstance(refs, list) or len(refs) != total:
            raise FullWindowFinalizeWorkspaceError(
                f"TERMINAL {kind} receipt index 不闭合"
            )
        for ref in refs:
            relative = _safe_relative(ref.get("path"), f"{kind}.path")
            if _manifest_content_ref(manifest, relative, kind) != ref:
                raise FullWindowFinalizeWorkspaceError(
                    f"manifest 与 TERMINAL {kind} ref 不闭合"
                )
    fixed_refs = (
        ("GENESIS", "workspace-genesis"),
        ("TERMINAL", "workspace-terminal"),
        ("DEEP-VERIFICATION", "workspace-deep-verification"),
        ("segments/index.json", "segment-index"),
        ("metadata/finalization.json", "segment-assembly-metadata"),
        ("frozen/bindings.json", "frozen-bindings"),
    )
    for relative, kind in fixed_refs:
        if _manifest_content_ref(manifest, relative, None) != _file_ref(
            package_root, package_root / relative
        ):
            raise FullWindowFinalizeWorkspaceError(
                f"manifest fixed receipt ref 不闭合：{relative}"
            )
    resource = terminal.get("resource_accounting")
    if not isinstance(resource, Mapping):
        raise FullWindowFinalizeWorkspaceError(
            "packaged TERMINAL resource accounting 缺失"
        )
    if (
        terminal.get("completed_slots") != total
        or terminal.get("sealed") is not True
        or terminal.get("terminal_segment_receipt_ref")
        != terminal["segment_receipt_refs"][-1]
        or terminal.get("terminal_deep_segment_receipt_ref")
        != terminal["deep_segment_receipt_refs"][-1]
        or index.get("terminal_ref") != terminal_ref
        or index.get("deep_verification_ref") != deep_ref
        or index.get("segment_receipt_refs")
        != terminal["segment_receipt_refs"]
        or index.get("segment_payload_refs")
        != terminal["segment_payload_refs"]
        or index.get("deep_segment_receipt_refs")
        != terminal["deep_segment_receipt_refs"]
        or index.get("deep_chain_sha256") != terminal["deep_chain_sha256"]
        or deep.get("terminal_ref") != terminal_ref
        or deep.get("terminal_segment_receipt_ref")
        != terminal["terminal_segment_receipt_ref"]
        or deep.get("terminal_deep_segment_receipt_ref")
        != terminal["terminal_deep_segment_receipt_ref"]
        or deep.get("verified_segment_count") != total
        or deep.get("deep_chain_sha256") != terminal["deep_chain_sha256"]
        or deep.get("bindings") != terminal.get("bindings")
        or deep.get("code_identity_sha256")
        != terminal.get("code_identity_sha256")
        or deep.get("database_write_operations") != 0
        or index.get("semantic_core_sha256") != semantic_core
        or metadata.get("semantic_core_sha256") != semantic_core
        or index.get("business_semantic_core_sha256") != business_core
        or metadata.get("business_semantic_core_sha256") != business_core
        or quality.get("business_semantic_core_sha256") != business_core
        or index.get("finalization_segment_core_sha256") != semantic_core
        or metadata.get("finalization_segment_core_sha256")
        != semantic_core
        or quality.get("finalization_segment_core_sha256")
        != semantic_core
        or metadata.get("reproduction_scope")
        != "independent_package_assembly_from_same_verified_finalization_segments"
        or metadata.get("record_observation_reads_during_assembly") != 0
        or index.get("record_observation_reads_during_assembly") != 0
        or metadata.get("database_write_operations") != 0
        or terminal.get("bindings") != bindings
        or manifest.get("bindings") != bindings
        or genesis.get("bindings") != bindings
        or terminal.get("code_identity_sha256")
        != genesis.get("code_identity_sha256")
    ):
        raise FullWindowFinalizeWorkspaceError(
            "assembled package receipt/terminal/bindings/core 不闭合"
        )
    return {
        "verified": True,
        "verification_scope": "receipt_only_no_segment_content_walk",
        "package_root": str(package_root.resolve()),
        "manifest": manifest,
        "semantic_core_sha256": semantic_core,
        "business_semantic_core_sha256": business_core,
        "finalization_segment_core_sha256": semantic_core,
        "bindings": bindings,
        "code_identity_sha256": terminal["code_identity_sha256"],
        "terminal_ref": terminal_ref,
        "deep_verification_ref": deep_ref,
        "resource_accounting": terminal["resource_accounting"],
        "record_observation_reread_count": 0,
        "segment_payload_decompression_count": 0,
        "full_segment_chain_reread_count": 0,
    }


def _verify_workspace_assembled_contents(
    package_root: Path,
) -> Mapping[str, Any]:
    manifest = verify_published_package(package_root)
    genesis = _verify_fingerprinted(
        _load_json(package_root / "GENESIS", "packaged GENESIS"),
        WORKSPACE_GENESIS_SCHEMA,
        "packaged GENESIS",
    )
    terminal = _verify_fingerprinted(
        _load_json(package_root / "TERMINAL", "packaged TERMINAL"),
        WORKSPACE_TERMINAL_SCHEMA,
        "packaged TERMINAL",
    )
    deep = _verify_fingerprinted(
        _load_json(
            package_root / "DEEP-VERIFICATION", "packaged DEEP-VERIFICATION"
        ),
        WORKSPACE_DEEP_VERIFICATION_SCHEMA,
        "packaged DEEP-VERIFICATION",
    )
    index = _verify_fingerprinted(
        _load_json(package_root / "segments/index.json", "segment index"),
        WORKSPACE_ASSEMBLY_INDEX_SCHEMA,
        "segment index",
    )
    metadata = _verify_fingerprinted(
        _load_json(package_root / "metadata/finalization.json", "finalization"),
        WORKSPACE_ASSEMBLY_METADATA_SCHEMA,
        "finalization",
    )
    quality = _load_json(
        package_root / "quality-and-accounting.json",
        "quality-and-accounting",
    )
    bindings = _load_json(package_root / "frozen/bindings.json", "bindings")
    terminal_ref = _file_ref(package_root, package_root / "TERMINAL")
    deep_ref = _file_ref(package_root, package_root / "DEEP-VERIFICATION")
    resource = terminal.get("resource_accounting")
    if not isinstance(resource, Mapping):
        raise FullWindowFinalizeWorkspaceError(
            "packaged TERMINAL resource accounting 缺失"
        )
    synthetic_head = {
        "sequence": terminal["completed_slots"],
        "total_slots": terminal["total_slots"],
        "current_segment_receipt_ref": terminal[
            "terminal_segment_receipt_ref"
        ],
        "current_deep_segment_receipt_ref": terminal[
            "terminal_deep_segment_receipt_ref"
        ],
        "segment_receipt_refs": terminal["segment_receipt_refs"],
        "segment_payload_refs": terminal["segment_payload_refs"],
        "deep_segment_receipt_refs": terminal[
            "deep_segment_receipt_refs"
        ],
        "deep_chain_sha256": terminal["deep_chain_sha256"],
        "cumulative_segment_payload_bytes": deep[
            "segment_payload_bytes_read"
        ],
        **dict(resource),
    }
    verified_chain = _verify_terminal_deep_checkpoint(
        package_root, synthetic_head
    )
    semantic_core = _workspace_semantic_core(terminal, deep)
    business_core = metadata.get("business_semantic_core_sha256")
    from .full_window_segment_package import REQUIRED_BUSINESS_PATHS

    manifest_paths = {
        str(item.get("path"))
        for item in manifest.get("contents", ())
        if isinstance(item, Mapping)
    }
    if (
        not isinstance(business_core, str)
        or len(business_core) != 64
        or any(character not in "0123456789abcdef" for character in business_core)
        or not REQUIRED_BUSINESS_PATHS <= manifest_paths
        or terminal["completed_slots"] != terminal["total_slots"]
        or len(terminal["segment_receipt_refs"]) != terminal["total_slots"]
        or index.get("terminal_ref") != terminal_ref
        or index.get("deep_verification_ref") != deep_ref
        or index.get("segment_receipt_refs")
        != terminal["segment_receipt_refs"]
        or index.get("segment_payload_refs")
        != terminal["segment_payload_refs"]
        or index.get("deep_segment_receipt_refs")
        != terminal["deep_segment_receipt_refs"]
        or index.get("deep_chain_sha256")
        != terminal["deep_chain_sha256"]
        or deep.get("terminal_ref") != terminal_ref
        or deep.get("verified_segment_count") != terminal["total_slots"]
        or index.get("semantic_core_sha256") != semantic_core
        or metadata.get("semantic_core_sha256") != semantic_core
        or index.get("business_semantic_core_sha256") != business_core
        or metadata.get("business_semantic_core_sha256") != business_core
        or quality.get("business_semantic_core_sha256") != business_core
        or index.get("finalization_segment_core_sha256") != semantic_core
        or metadata.get("finalization_segment_core_sha256")
        != semantic_core
        or quality.get("finalization_segment_core_sha256")
        != semantic_core
        or metadata.get("reproduction_scope")
        != "independent_package_assembly_from_same_verified_finalization_segments"
        or metadata.get("record_observation_reads_during_assembly") != 0
        or index.get("record_observation_reads_during_assembly") != 0
        or metadata.get("database_write_operations") != 0
        or terminal.get("bindings") != bindings
        or manifest.get("bindings") != bindings
        or genesis.get("bindings") != bindings
        or terminal.get("code_identity_sha256")
        != genesis.get("code_identity_sha256")
        or verified_chain["terminal_deep_segment_receipt"].get(
            "cumulative_resource_accounting"
        )
        != terminal["resource_accounting"]
    ):
        raise FullWindowFinalizeWorkspaceError(
            "assembled package segment/terminal/bindings/core 不闭合"
        )
    return {
        "verified": True,
        "package_root": str(package_root.resolve()),
        "manifest": manifest,
        "semantic_core_sha256": semantic_core,
        "business_semantic_core_sha256": business_core,
        "finalization_segment_core_sha256": semantic_core,
        "bindings": bindings,
        "code_identity_sha256": terminal["code_identity_sha256"],
        "terminal_ref": terminal_ref,
        "deep_verification_ref": deep_ref,
        "resource_accounting": terminal["resource_accounting"],
        "record_observation_reread_count": 0,
        "full_segment_chain_reread_count": 0,
    }


def _publish_package_resource_receipt(
    package_root: Path,
    resource_path: Path,
    verified: Mapping[str, Any],
) -> Mapping[str, Any]:
    manifest_raw = _regular_bytes(
        package_root / "package-manifest.json", maximum_bytes=16 * 1024 * 1024
    )
    package_bytes = sum(
        path.stat().st_size
        for path in package_root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    receipt = _fingerprinted(
        WORKSPACE_PACKAGE_RESOURCE_SCHEMA,
        {
            "package_root": str(package_root.resolve()),
            "package_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "package_semantic_fingerprint_sha256": verified["manifest"][
                "semantic_fingerprint_sha256"
            ],
            "semantic_core_sha256": verified["semantic_core_sha256"],
            "business_semantic_core_sha256": verified[
                "business_semantic_core_sha256"
            ],
            "finalization_segment_core_sha256": verified[
                "finalization_segment_core_sha256"
            ],
            "terminal_ref": verified["terminal_ref"],
            "deep_verification_ref": verified["deep_verification_ref"],
            "segment_index_ref": _file_ref(
                package_root, package_root / "segments/index.json"
            ),
            "bindings": verified["bindings"],
            "code_identity_sha256": verified["code_identity_sha256"],
            "resource_accounting": {
                "assembly_package_bytes_read": package_bytes,
                "cumulative_source_package_bytes_read": verified[
                    "resource_accounting"
                ]["cumulative_package_bytes_read"],
                "cumulative_record_observation_bytes_read": verified[
                    "resource_accounting"
                ]["cumulative_record_observation_bytes_read"],
                "record_observation_reads_during_assembly": 0,
                "assembly_peak_temporary_bytes": int(
                    verified.get("assembly_peak_temporary_bytes", package_bytes)
                ),
                "temporary_bytes_exclusive_limit": int(
                    verified.get(
                        "temporary_bytes_exclusive_limit",
                        DEFAULT_MAX_TEMPORARY_BYTES,
                    )
                ),
                "maximum_slot_seconds": verified["resource_accounting"][
                    "maximum_slot_seconds"
                ],
                "cumulative_finalization_seconds": verified[
                    "resource_accounting"
                ]["cumulative_finalization_seconds"],
                "maximum_temporary_bytes": verified["resource_accounting"][
                    "maximum_temporary_bytes"
                ],
                "database_write_operations": 0,
            },
        },
    )
    _create_json(resource_path, receipt)
    return receipt


def _verify_package_resource_receipt(
    root: Path,
    resource_path: Path,
    verified: Mapping[str, Any],
) -> Mapping[str, Any]:
    receipt = _verify_fingerprinted(
        _load_json(resource_path, "package resource receipt"),
        WORKSPACE_PACKAGE_RESOURCE_SCHEMA,
        "package resource receipt",
    )
    manifest_raw = _regular_bytes(
        root / "package-manifest.json", maximum_bytes=16 * 1024 * 1024
    )
    accounting = receipt.get("resource_accounting")
    if not isinstance(accounting, Mapping):
        raise FullWindowFinalizeWorkspaceError(
            "package resource receipt accounting 缺失"
        )
    peak = accounting.get("assembly_peak_temporary_bytes")
    limit = accounting.get("temporary_bytes_exclusive_limit")
    numeric_nonnegative = (
        "assembly_package_bytes_read",
        "cumulative_source_package_bytes_read",
        "cumulative_record_observation_bytes_read",
        "maximum_temporary_bytes",
    )
    if any(
        isinstance(accounting.get(field), bool)
        or not isinstance(accounting.get(field), int)
        or accounting.get(field) < 0
        for field in numeric_nonnegative
    ):
        raise FullWindowFinalizeWorkspaceError(
            "package resource receipt 字节计数非法"
        )
    if (
        isinstance(peak, bool)
        or not isinstance(peak, int)
        or peak < 0
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit <= 0
        or peak >= limit
        or limit > DEFAULT_MAX_TEMPORARY_BYTES
        or receipt.get("package_root") != str(root.resolve())
        or receipt.get("package_manifest_sha256")
        != hashlib.sha256(manifest_raw).hexdigest()
        or receipt.get("package_semantic_fingerprint_sha256")
        != verified["manifest"]["semantic_fingerprint_sha256"]
        or receipt.get("semantic_core_sha256")
        != verified["semantic_core_sha256"]
        or receipt.get("business_semantic_core_sha256")
        != verified["business_semantic_core_sha256"]
        or receipt.get("finalization_segment_core_sha256")
        != verified["finalization_segment_core_sha256"]
        or receipt.get("terminal_ref") != verified["terminal_ref"]
        or receipt.get("deep_verification_ref")
        != verified["deep_verification_ref"]
        or receipt.get("segment_index_ref")
        != _file_ref(root, root / "segments/index.json")
        or receipt.get("bindings") != verified["bindings"]
        or receipt.get("code_identity_sha256")
        != verified["code_identity_sha256"]
        or accounting.get("record_observation_reads_during_assembly") != 0
        or accounting.get("database_write_operations") != 0
    ):
        raise FullWindowFinalizeWorkspaceError(
            "package resource receipt 与 manifest/deep/bindings/资源门不闭合"
        )
    return receipt


def _verify_workspace_assembled_package_receipt_only(
    package_root: os.PathLike[str] | str,
    *,
    resource_receipt_path: Optional[os.PathLike[str] | str] = None,
    require_resource_receipt: bool = True,
) -> Mapping[str, Any]:
    root = Path(package_root).absolute()
    verified = dict(_verify_workspace_assembled_receipts(root))
    resource_path = (
        Path(resource_receipt_path).absolute()
        if resource_receipt_path is not None
        else _package_resource_receipt_path(root)
    )
    if not require_resource_receipt and not resource_path.exists():
        verified["resource_receipt_verified"] = False
        verified["resource_receipt_path"] = str(resource_path)
        return verified
    _verify_package_resource_receipt(root, resource_path, verified)
    verified["resource_receipt_verified"] = True
    verified["resource_receipt_path"] = str(resource_path)
    return verified


def verify_workspace_assembled_package(
    package_root: os.PathLike[str] | str,
    *,
    resource_receipt_path: Optional[os.PathLike[str] | str] = None,
    require_resource_receipt: bool = True,
) -> Mapping[str, Any]:
    """离线核验 segment 包；默认同时核验 deep/resource，不读 journal。"""

    root = Path(package_root).absolute()
    verified = dict(_verify_workspace_assembled_contents(root))
    resource_path = (
        Path(resource_receipt_path).absolute()
        if resource_receipt_path is not None
        else _package_resource_receipt_path(root)
    )
    if not require_resource_receipt and not resource_path.exists():
        verified["resource_receipt_verified"] = False
        verified["resource_receipt_path"] = str(resource_path)
        return verified
    _verify_package_resource_receipt(root, resource_path, verified)
    verified["resource_receipt_verified"] = True
    verified["resource_receipt_path"] = str(resource_path)
    return verified


def _load_assembly_active(root: Path) -> Optional[Mapping[str, Any]]:
    path = root / "ASSEMBLY-ACTIVE"
    if not path.exists() and not path.is_symlink():
        return None
    return _verify_fingerprinted(
        _load_json(path, "ASSEMBLY-ACTIVE"),
        WORKSPACE_ASSEMBLY_ACTIVE_SCHEMA,
        "ASSEMBLY-ACTIVE",
    )


def _reconcile_assembly_locked(
    root: Path, package_plan: Optional[Any] = None
) -> Mapping[str, Any]:
    active = _load_assembly_active(root)
    if active is None:
        if _load_assembly_checkpoint(root) is not None:
            raise FullWindowFinalizeWorkspaceError(
                "ASSEMBLY-CHECKPOINT 存在但 ASSEMBLY-ACTIVE 缺失"
            )
        return {"state": "clean", "retired_staging": False}
    if package_plan is None:
        raise FullWindowFinalizeWorkspaceError(
            "存在 assembly ACTIVE 时必须提供完整冻结业务输入重建 package plan"
        )
    if (
        active.get("package_plan_sha256") != _package_plan_sha256(package_plan)
        or active.get("business_semantic_core_sha256")
        != package_plan.business_semantic_core_sha256
        or active.get("finalization_segment_core_sha256")
        != package_plan.finalization_segment_core_sha256
    ):
        raise FullWindowFinalizeWorkspaceError(
            "ASSEMBLY-ACTIVE 与本次完整业务 package plan 不闭合"
        )
    target = Path(str(active["target_root"])).absolute()
    staging = Path(str(active["staging_root"])).absolute()
    resource = Path(str(active["resource_receipt_path"])).absolute()
    retired_resource_temporaries = _retire_publish_temporaries(
        resource.parent, resource.name
    )
    if target.exists() or target.is_symlink():
        verified = _verify_workspace_assembled_package_receipt_only(
            target,
            resource_receipt_path=resource,
            require_resource_receipt=False,
        )
        verified = dict(verified)
        if (
            verified.get("manifest") != package_plan.manifest
            or verified.get("business_semantic_core_sha256")
            != package_plan.business_semantic_core_sha256
            or verified.get("finalization_segment_core_sha256")
            != package_plan.finalization_segment_core_sha256
        ):
            raise FullWindowFinalizeWorkspaceError(
                "rename 后 package 与冻结计划/双 core 不一致"
            )
        verified["assembly_peak_temporary_bytes"] = _tree_size(target)
        verified["temporary_bytes_exclusive_limit"] = _validated_temporary_limit(
            active.get(
                "maximum_temporary_bytes_exclusive",
                DEFAULT_MAX_TEMPORARY_BYTES,
            )
        )
        if (
            verified["assembly_peak_temporary_bytes"]
            >= verified["temporary_bytes_exclusive_limit"]
        ):
            raise FullWindowFinalizeWorkspaceError(
                "已 rename package 达到 5GB 排他边界"
            )
        if not resource.exists() and not resource.is_symlink():
            _publish_package_resource_receipt(target, resource, verified)
        else:
            _verify_workspace_assembled_package_receipt_only(
                target, resource_receipt_path=resource
            )
        _retire_assembly_checkpoint(root)
        _unlink_regular_fsync(root / "ASSEMBLY-ACTIVE", "ASSEMBLY-ACTIVE")
        return {
            "state": "rename_after_publish_reconciled_resource_receipt",
            "target_root": str(target),
            "resource_receipt_path": str(resource),
            "retired_staging": False,
            "retired_publish_temporaries": retired_resource_temporaries,
        }
    if staging.exists() or staging.is_symlink():
        _require_plain_directory(staging, "historical assembly staging")
        checkpoint = _validate_assembly_checkpoint(
            root,
            staging,
            str(active["terminal_sha256"]),
            package_plan,
        )
        completed = int(checkpoint["completed_copy_items"])
        if completed == len(package_plan.items):
            try:
                _verify_workspace_assembled_package_receipt_only(
                    staging, require_resource_receipt=False
                )
            except (OSError, FullWindowFinalizeWorkspaceError):
                return {
                    "state": "complete_copy_checkpoint_metadata_resume_required",
                    "staging_root": str(staging),
                    "completed_copy_items": completed,
                    "total_copy_items": len(package_plan.items),
                    "retired_staging": False,
                    "retired_publish_temporaries": retired_resource_temporaries,
                }
            return {
                "state": "verified_historical_staging_reusable",
                "staging_root": str(staging),
                "completed_copy_items": completed,
                "total_copy_items": len(package_plan.items),
                "retired_staging": False,
                "retired_publish_temporaries": retired_resource_temporaries,
            }
        return {
            "state": "partial_historical_staging_reusable",
            "staging_root": str(staging),
            "completed_copy_items": completed,
            "total_copy_items": len(package_plan.items),
            "retired_staging": False,
            "retired_publish_temporaries": retired_resource_temporaries,
        }
    _retire_assembly_checkpoint(root)
    _unlink_regular_fsync(root / "ASSEMBLY-ACTIVE", "ASSEMBLY-ACTIVE")
    return {
        "state": "empty_assembly_attempt_retired",
        "retired_staging": False,
        "retired_publish_temporaries": retired_resource_temporaries,
    }


def reconcile_workspace_publication(
    workspace_root: os.PathLike[str] | str,
    *,
    profile: Mapping[str, Any],
    source_fact_snapshot: Mapping[str, Any],
    incident_policy: Mapping[str, Any],
    compatible_mapping_snapshot: Mapping[str, Any],
    revised_mapping_snapshot: Mapping[str, Any],
    code_identity: Mapping[str, Any],
    input_selection: Mapping[str, Any],
    claim_inventory: Mapping[str, Any],
    bindings: Mapping[str, Any],
    maximum_temporary_bytes: Optional[int] = None,
) -> Mapping[str, Any]:
    root = Path(workspace_root).absolute()
    with finalization_workspace_lock(root):
        frozen_limit = _workspace_temporary_limit(root)
        temporary_limit = (
            frozen_limit
            if maximum_temporary_bytes is None
            else _validated_temporary_limit(maximum_temporary_bytes)
        )
        if temporary_limit > frozen_limit:
            raise FullWindowFinalizeWorkspaceError(
                "publication reconcile 临时上限不得放宽冻结门"
            )
        _reconcile_locked(root)
        _seal_locked(root)
        package_plan = _build_complete_package_plan(
            root,
            profile=profile,
            source_fact_snapshot=source_fact_snapshot,
            incident_policy=incident_policy,
            compatible_mapping_snapshot=compatible_mapping_snapshot,
            revised_mapping_snapshot=revised_mapping_snapshot,
            code_identity=code_identity,
            input_selection=input_selection,
            claim_inventory=claim_inventory,
            bindings=bindings,
            maximum_projected_bytes=temporary_limit,
        )
        return _reconcile_assembly_locked(root, package_plan)


def assemble_finalized_package_from_workspace(
    workspace_root: os.PathLike[str] | str,
    output_root: os.PathLike[str] | str,
    *,
    profile: Mapping[str, Any],
    source_fact_snapshot: Mapping[str, Any],
    incident_policy: Mapping[str, Any],
    compatible_mapping_snapshot: Mapping[str, Any],
    revised_mapping_snapshot: Mapping[str, Any],
    code_identity: Mapping[str, Any],
    input_selection: Mapping[str, Any],
    claim_inventory: Mapping[str, Any],
    bindings: Mapping[str, Any],
    resource_receipt_path: Optional[os.PathLike[str] | str] = None,
    publication_hook: Optional[Callable[[str, Path], None]] = None,
    maximum_temporary_bytes: Optional[int] = None,
) -> Mapping[str, Any]:
    """从 sealed segments 装配一个空目标；不会读取 record observations。"""

    workspace = Path(workspace_root).absolute()
    target = Path(output_root).absolute()
    resource = (
        Path(resource_receipt_path).absolute()
        if resource_receipt_path is not None
        else _package_resource_receipt_path(target)
    )
    _require_plain_directory(target.parent, "assembly target 父")
    _require_plain_directory(resource.parent, "assembly resource receipt 父")
    if (
        target == workspace
        or workspace in target.parents
        or target in workspace.parents
        or resource == target
        or target in resource.parents
        or resource == workspace
        or workspace in resource.parents
    ):
        raise FullWindowFinalizeWorkspaceError(
            "assembly workspace/target/resource 路径不得重叠"
        )
    with finalization_workspace_lock(workspace):
        frozen_limit = _workspace_temporary_limit(workspace)
        temporary_limit = (
            frozen_limit
            if maximum_temporary_bytes is None
            else _validated_temporary_limit(maximum_temporary_bytes)
        )
        if temporary_limit > frozen_limit:
            raise FullWindowFinalizeWorkspaceError(
                "assembly 临时上限不得放宽冻结的 5GB 门"
            )
        _assert_temporary_budget(
            workspace,
            maximum_temporary_bytes=temporary_limit,
            phase="assembly reconcile 前",
        )
        pending_active = _load_assembly_active(workspace)
        if (
            pending_active is not None
            and _validated_temporary_limit(
                pending_active["maximum_temporary_bytes_exclusive"]
            )
            > temporary_limit
        ):
            raise FullWindowFinalizeWorkspaceError(
                "既有 assembly attempt 的冻结临时上限高于本次严格上限"
            )
        _progress(publication_hook, "before_assembly_reconcile", workspace)
        _reconcile_locked(workspace)
        _seal_locked(workspace)
        package_plan = _build_complete_package_plan(
            workspace,
            profile=profile,
            source_fact_snapshot=source_fact_snapshot,
            incident_policy=incident_policy,
            compatible_mapping_snapshot=compatible_mapping_snapshot,
            revised_mapping_snapshot=revised_mapping_snapshot,
            code_identity=code_identity,
            input_selection=input_selection,
            claim_inventory=claim_inventory,
            bindings=bindings,
            maximum_projected_bytes=temporary_limit,
        )
        reconciliation = _reconcile_assembly_locked(
            workspace, package_plan
        )
        if target.exists() or target.is_symlink():
            _progress(
                publication_hook,
                "before_existing_package_verify",
                target,
            )
            verified = _verify_workspace_assembled_package_receipt_only(
                target, resource_receipt_path=resource
            )
            if (
                verified.get("manifest") != package_plan.manifest
                or verified.get("business_semantic_core_sha256")
                != package_plan.business_semantic_core_sha256
                or verified.get("finalization_segment_core_sha256")
                != package_plan.finalization_segment_core_sha256
            ):
                raise FullWindowFinalizeWorkspaceError(
                    "既有 package 未绑定本次完整 frozen inputs/双 core"
                )
            _progress(
                publication_hook,
                "after_existing_package_verify",
                target,
            )
            return {
                **verified,
                "publication_state": reconciliation["state"],
            }
        terminal_sha = _file_ref(workspace, workspace / "TERMINAL")["sha256"]
        staging = target.parent / (
            f".{target.name}.rrc25-segment-assembly-{terminal_sha[:16]}"
        )
        active_path = workspace / "ASSEMBLY-ACTIVE"
        active = _load_assembly_active(workspace)
        if active is None:
            active_payload = _fingerprinted(
                WORKSPACE_ASSEMBLY_ACTIVE_SCHEMA,
                {
                    "target_root": str(target),
                    "staging_root": str(staging),
                    "resource_receipt_path": str(resource),
                    "terminal_sha256": terminal_sha,
                    "package_plan_sha256": _package_plan_sha256(package_plan),
                    "business_semantic_core_sha256": (
                        package_plan.business_semantic_core_sha256
                    ),
                    "finalization_segment_core_sha256": (
                        package_plan.finalization_segment_core_sha256
                    ),
                    "maximum_temporary_bytes_exclusive": temporary_limit,
                    "publication_state": "staging_or_rename_pending",
                },
            )
            _create_json(
                active_path,
                active_payload,
                temporary_root=workspace,
                maximum_temporary_bytes=temporary_limit,
            )
        elif (
            active.get("target_root") != str(target)
            or active.get("staging_root") != str(staging)
            or active.get("resource_receipt_path") != str(resource)
        ):
            raise FullWindowFinalizeWorkspaceError(
                "存在另一目标的未闭合 assembly attempt"
            )
        if reconciliation.get("state") != "verified_historical_staging_reusable":
            _build_assembly_staging(
                workspace,
                staging,
                package_plan=package_plan,
                maximum_temporary_bytes=temporary_limit,
                progress_hook=publication_hook,
            )
        staging_temporary_bytes = _assert_temporary_budget(
            staging,
            maximum_temporary_bytes=temporary_limit,
            phase="assembly staging 发布前",
        )
        _progress(publication_hook, "before_staging_verify", staging)
        staged = _verify_workspace_assembled_package_receipt_only(
            staging, require_resource_receipt=False
        )
        _progress(publication_hook, "after_staging_verify", staging)
        if publication_hook is not None:
            publication_hook("before_atomic_directory_publish", staging)
        if target.exists() or target.is_symlink():
            raise FileExistsError("segment assembly 目标已存在，拒绝覆盖")
        os.rename(staging, target)
        _fsync_directory(target.parent)
        if publication_hook is not None:
            publication_hook("after_atomic_directory_publish", target)
        verified = dict(
            _verify_workspace_assembled_package_receipt_only(
                target, resource_receipt_path=resource, require_resource_receipt=False
            )
        )
        verified["assembly_peak_temporary_bytes"] = staging_temporary_bytes
        verified["temporary_bytes_exclusive_limit"] = temporary_limit
        if not resource.exists() and not resource.is_symlink():
            _publish_package_resource_receipt(target, resource, verified)
        else:
            raise FileExistsError("segment package resource receipt 已存在，拒绝覆盖")
        if publication_hook is not None:
            publication_hook("after_resource_receipt_publish", resource)
        _retire_assembly_checkpoint(workspace)
        _unlink_regular_fsync(active_path, "ASSEMBLY-ACTIVE")
        verified = dict(
            _verify_workspace_assembled_package_receipt_only(
                target, resource_receipt_path=resource
            )
        )
        verified.update(
            {
                "publication_state": "published_from_sealed_segments",
                "record_observation_reads_during_assembly": 0,
                "staged_semantic_core_sha256": staged[
                    "semantic_core_sha256"
                ],
                "business_semantic_core_sha256": verified[
                    "business_semantic_core_sha256"
                ],
                "finalization_segment_core_sha256": verified[
                    "finalization_segment_core_sha256"
                ],
                "assembly_peak_temporary_bytes": staging_temporary_bytes,
                "temporary_bytes_exclusive_limit": temporary_limit,
            }
        )
        return verified


def assemble_workspace_reproduction(
    workspace_root: os.PathLike[str] | str,
    *,
    reference_output_root: os.PathLike[str] | str,
    reproduction_output_root: os.PathLike[str] | str,
    acceptance_receipt_path: os.PathLike[str] | str,
    profile: Mapping[str, Any],
    source_fact_snapshot: Mapping[str, Any],
    incident_policy: Mapping[str, Any],
    compatible_mapping_snapshot: Mapping[str, Any],
    revised_mapping_snapshot: Mapping[str, Any],
    code_identity: Mapping[str, Any],
    input_selection: Mapping[str, Any],
    claim_inventory: Mapping[str, Any],
    bindings: Mapping[str, Any],
    publication_hook: Optional[Callable[[str, Path], None]] = None,
    maximum_temporary_bytes: Optional[int] = None,
) -> Mapping[str, Any]:
    """从同一已验 segments 独立装配两个空目录并发布 v2 accepted receipt。"""

    first_root = Path(reference_output_root).absolute()
    second_root = Path(reproduction_output_root).absolute()
    target = Path(acceptance_receipt_path).absolute()
    if first_root == second_root:
        raise FullWindowFinalizeWorkspaceError("独立装配必须使用两个不同目录")
    if (
        target == first_root
        or target == second_root
        or first_root in target.parents
        or second_root in target.parents
    ):
        raise FullWindowFinalizeWorkspaceError(
            "acceptance receipt 不得位于任一装配包内"
        )
    first = assemble_finalized_package_from_workspace(
        workspace_root,
        first_root,
        profile=profile,
        source_fact_snapshot=source_fact_snapshot,
        incident_policy=incident_policy,
        compatible_mapping_snapshot=compatible_mapping_snapshot,
        revised_mapping_snapshot=revised_mapping_snapshot,
        code_identity=code_identity,
        input_selection=input_selection,
        claim_inventory=claim_inventory,
        bindings=bindings,
        publication_hook=publication_hook,
        maximum_temporary_bytes=maximum_temporary_bytes,
    )
    _progress(publication_hook, "between_reproduction_assemblies", first_root)
    second = assemble_finalized_package_from_workspace(
        workspace_root,
        second_root,
        profile=profile,
        source_fact_snapshot=source_fact_snapshot,
        incident_policy=incident_policy,
        compatible_mapping_snapshot=compatible_mapping_snapshot,
        revised_mapping_snapshot=revised_mapping_snapshot,
        code_identity=code_identity,
        input_selection=input_selection,
        claim_inventory=claim_inventory,
        bindings=bindings,
        publication_hook=publication_hook,
        maximum_temporary_bytes=maximum_temporary_bytes,
    )
    if (
        first["business_semantic_core_sha256"]
        != second["business_semantic_core_sha256"]
        or first["finalization_segment_core_sha256"]
        != second["finalization_segment_core_sha256"]
    ):
        raise FullWindowFinalizeWorkspaceError(
            "两目录 business/segment 双 semantic core 不一致"
        )
    packages = []
    for role, root, verified in (
        ("reference", first_root, first),
        ("reproduction", second_root, second),
    ):
        manifest_raw = _regular_bytes(
            root / "package-manifest.json", maximum_bytes=16 * 1024 * 1024
        )
        resource_path = Path(str(verified["resource_receipt_path"]))
        resource_raw = _regular_bytes(resource_path, maximum_bytes=8 * 1024 * 1024)
        packages.append(
            {
                "role": role,
                "package_root": str(root),
                "package_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                "package_semantic_fingerprint_sha256": verified["manifest"][
                    "semantic_fingerprint_sha256"
                ],
                "release_id": verified["manifest"]["release_id"],
                "resource_receipt_path": str(resource_path),
                "resource_receipt_file_sha256": hashlib.sha256(
                    resource_raw
                ).hexdigest(),
                "terminal_ref": verified["terminal_ref"],
                "deep_verification_ref": verified["deep_verification_ref"],
                "segment_index_ref": _file_ref(
                    root, root / "segments/index.json"
                ),
                "business_semantic_core_sha256": verified[
                    "business_semantic_core_sha256"
                ],
                "finalization_segment_core_sha256": verified[
                    "finalization_segment_core_sha256"
                ],
            }
        )
    semantic = {
        "schema_version": WORKSPACE_REPRODUCTION_ACCEPTANCE_SCHEMA,
        "acceptance_state": "accepted",
        "reproduction_scope": "independent_package_assembly_from_same_verified_finalization_segments",
        "raw_replay_reproduction": "not_performed_by_user_choice",
        "semantic_core_sha256": first["semantic_core_sha256"],
        "business_semantic_core_sha256": first[
            "business_semantic_core_sha256"
        ],
        "finalization_segment_core_sha256": first[
            "finalization_segment_core_sha256"
        ],
        "input_bindings": first["bindings"],
        "code_identity_sha256": first["code_identity_sha256"],
        "packages": packages,
        "checks": {
            "two_distinct_empty_targets_used": True,
            "same_verified_segment_index": True,
            "complete_frozen_business_inputs_verified": True,
            "complete_business_product_population_verified": True,
            "semantic_core_equal": True,
            "business_semantic_core_equal": True,
            "finalization_segment_core_equal": True,
            "terminal_and_deep_receipts_verified": True,
            "record_observation_reads_during_both_assemblies": 0,
            "database_write_operations": 0,
        },
    }
    receipt = {
        **semantic,
        "receipt_sha256": _hash(
            {
                "schema": "rrc25_full_window_reproduction_acceptance_v2",
                "receipt": semantic,
            }
        ),
    }
    _retire_publish_temporaries(target.parent, target.name)
    _progress(
        publication_hook,
        "before_reproduction_acceptance_receipt_publish",
        target,
    )
    if target.exists() or target.is_symlink():
        existing = _load_json(target, "existing v2 acceptance receipt")
        if existing != receipt:
            raise FullWindowFinalizeWorkspaceError(
                "既有 v2 acceptance receipt 与本次双目录结果不一致"
            )
    else:
        _create_json(target, receipt)
    return receipt


def verify_workspace_reproduction_acceptance_receipt(
    receipt_path: os.PathLike[str] | str,
    *,
    deep_content_walk: bool = False,
) -> Mapping[str, Any]:
    """默认执行日常 receipt-only 验真；显式 deep 才遍历包内容。"""

    receipt = _load_json(Path(receipt_path).absolute(), "v2 acceptance receipt")
    semantic = dict(receipt)
    supplied = semantic.pop("receipt_sha256", None)
    if (
        receipt.get("schema_version") != WORKSPACE_REPRODUCTION_ACCEPTANCE_SCHEMA
        or receipt.get("acceptance_state") != "accepted"
        or receipt.get("reproduction_scope")
        != "independent_package_assembly_from_same_verified_finalization_segments"
        or supplied
        != _hash(
            {
                "schema": "rrc25_full_window_reproduction_acceptance_v2",
                "receipt": semantic,
            }
        )
    ):
        raise FullWindowFinalizeWorkspaceError("v2 acceptance receipt fingerprint 非法")
    packages = receipt.get("packages")
    if not isinstance(packages, list) or [row.get("role") for row in packages] != [
        "reference",
        "reproduction",
    ]:
        raise FullWindowFinalizeWorkspaceError("v2 acceptance package 对不闭合")
    required_checks = {
        "two_distinct_empty_targets_used": True,
        "same_verified_segment_index": True,
        "complete_frozen_business_inputs_verified": True,
        "complete_business_product_population_verified": True,
        "semantic_core_equal": True,
        "business_semantic_core_equal": True,
        "finalization_segment_core_equal": True,
        "terminal_and_deep_receipts_verified": True,
        "record_observation_reads_during_both_assemblies": 0,
        "database_write_operations": 0,
    }
    checks = receipt.get("checks")
    if not isinstance(checks, Mapping) or dict(checks) != required_checks:
        raise FullWindowFinalizeWorkspaceError(
            "v2 acceptance checks receipt 不闭合"
        )
    roots = set()
    business_cores = set()
    segment_cores = set()
    for row in packages:
        root = Path(str(row.get("package_root"))).absolute()
        resource = Path(str(row.get("resource_receipt_path"))).absolute()
        verified = (
            verify_workspace_assembled_package(
                root, resource_receipt_path=resource
            )
            if deep_content_walk
            else _verify_workspace_assembled_package_receipt_only(
                root, resource_receipt_path=resource
            )
        )
        manifest_raw = _regular_bytes(
            root / "package-manifest.json", maximum_bytes=16 * 1024 * 1024
        )
        resource_raw = _regular_bytes(resource, maximum_bytes=8 * 1024 * 1024)
        if (
            row.get("package_manifest_sha256")
            != hashlib.sha256(manifest_raw).hexdigest()
            or row.get("package_semantic_fingerprint_sha256")
            != verified["manifest"]["semantic_fingerprint_sha256"]
            or row.get("resource_receipt_file_sha256")
            != hashlib.sha256(resource_raw).hexdigest()
            or row.get("terminal_ref") != verified["terminal_ref"]
            or row.get("deep_verification_ref")
            != verified["deep_verification_ref"]
            or row.get("release_id") != verified["manifest"]["release_id"]
            or row.get("segment_index_ref")
            != _file_ref(root, root / "segments/index.json")
            or row.get("business_semantic_core_sha256")
            != verified["business_semantic_core_sha256"]
            or row.get("finalization_segment_core_sha256")
            != verified["finalization_segment_core_sha256"]
            or verified["bindings"] != receipt.get("input_bindings")
            or verified["code_identity_sha256"]
            != receipt.get("code_identity_sha256")
        ):
            raise FullWindowFinalizeWorkspaceError(
                "v2 acceptance package/deep/resource 绑定失效"
            )
        roots.add(str(root))
        business_cores.add(verified["business_semantic_core_sha256"])
        segment_cores.add(verified["finalization_segment_core_sha256"])
    if (
        len(roots) != 2
        or business_cores
        != {receipt.get("business_semantic_core_sha256")}
        or segment_cores
        != {receipt.get("finalization_segment_core_sha256")}
        or receipt.get("semantic_core_sha256")
        != receipt.get("finalization_segment_core_sha256")
    ):
        raise FullWindowFinalizeWorkspaceError(
            "v2 acceptance 未证明双目录完整业务/segment 双 core"
        )
    return dict(receipt)


__all__ = (
    "DEFAULT_CHILD_PLANNED_STOP_SECONDS",
    "DEFAULT_PARENT_KILL_SECONDS",
    "DEFAULT_PARENT_TERM_SECONDS",
    "FinalizationWorkspaceLocked",
    "FinalizationWorkspaceRun",
    "FullWindowFinalizeWorkspaceError",
    "finalization_workspace_lock",
    "initialize_finalization_workspace",
    "assemble_finalized_package_from_workspace",
    "assemble_workspace_reproduction",
    "reconcile_finalization_workspace",
    "reconcile_workspace_publication",
    "run_finalization_workspace_segment",
    "seal_finalization_workspace",
    "verify_finalization_workspace",
    "verify_workspace_assembled_package",
    "verify_workspace_reproduction_acceptance_receipt",
)
