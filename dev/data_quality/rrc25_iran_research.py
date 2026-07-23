#!/usr/bin/env python3
"""伊朗 RRC25 研究协调命令。

原有四个协调子命令共用同一 Profile/manifest/mapping/code 身份；其中
``execute``/``resume`` 仍只接受显式 fixture executor。新增的 ``seed-*``
子命令只负责真实 RIB seed 的有界、可恢复解析，不进入 UPDATE、派生、数据库
或发布阶段：首次执行把 gzip 单次完整解压成内容寻址 spool；每段默认在
420 秒主动写出不可变 record-boundary checkpoint，恢复时验证 spool 后按
解压 record offset 直接 seek，不再重读压缩制品。active checkpoint 不会
接受手填的 prior raw 数字；seed-start/resume 必须重新核验 execution-prep
中 create-only probe ledger 的唯一 terminal ref/SHA，并把 conservative
reserved upper accounting 写入 checkpoint resources。active checkpoint 不会
自动删除；``seed-archive-checkpoint`` 只在旧 checkpoint 已被更高序列且有
进展的完整 checkpoint 取代后执行显式归档。seed 完成后，
``seed-retire-spool`` 仅在压缩原件仍可按 manifest 复核并先发布规范退役收据
时显式释放 spool；它在 raw 打开前执行 420 秒 admission，哈希期间每 1MiB
观测同进程 540/600 秒门，并以 create-only attempt 收据保留失败读取累计。
执行和恢复路径均不会自动触发这两类回收。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from typing import Any, Iterable, Mapping, Sequence

# 允许从仓库根目录直接执行 ``python3 dev/data_quality/...py``。
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.data_pipeline.research.rrc25_country_outage.coordinator import (
    DEFAULT_PRODUCTION_ROOTS,
    DEFAULT_PROTECTED_ROOTS,
    DEFAULT_MAX_ARTIFACTS_PER_CHUNK,
    ExecutionRecord,
    ResearchCoordinatorError,
    build_worker_plan,
    effective_resource_limits,
    execute_research,
    load_json_metadata,
    prepare_research_plan,
    resume_research,
    verify_research_run,
)
from backend.data_pipeline.research.resource_gate import (
    ResourceUsage,
    WriteTarget,
    evaluate_resource_gate,
)
from backend.data_pipeline.research.rrc25_country_outage.bounded_pilot_worker import (
    _DEFAULT_SEED_BATCH_MAX_RECORDS,
    _DEFAULT_SEED_BATCH_MAX_ROUTE_EVENTS,
    BoundedPilotWorkerError,
    run_bounded_pilot_worker,
    validate_seed_spool_attestation,
    verify_full_seed_checkpoint,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    CountryImpactError,
    build_raw_retention_mapping_union,
    mapping_bundle_sha256,
    mapping_view_from_frozen_snapshot,
    mapping_view_from_revised_snapshot,
)
from backend.data_pipeline.research.rrc25_country_outage.input_resolver import (
    ResearchInputError,
    resolve_research_inputs,
)
from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import (
    canonical_json,
    write_canonical_json,
)
from backend.data_pipeline.research.rrc25_country_outage.profile import (
    ResearchProfileError,
    profile_sha256,
    validate_research_profile,
)
from dev.data_quality.rrc25_iran_bounded_pilot import (
    SparsePilotError,
    build_code_identity,
)
from dev.data_quality.rrc25_iran_execution_prep import (
    ExecutionPrepError,
    close_seed_raw_attempt,
    reconcile_abandoned_seed_raw_attempt,
    reserve_seed_raw_attempt,
    verify_seed_raw_ledger,
    verify_probe_raw_ledger_terminal,
)


FIXTURE_SCHEMA_VERSION = "rrc25-iran-fixture-executor/v1"
UTC = timezone.utc
DEFAULT_SEED_CHECKPOINT_SECONDS = 420.0
MAX_SEED_CHECKPOINT_SECONDS = 420.0
# worker 的 checkpoint 单文件硬上限；spool 与原子 checkpoint 会同时占空间。
MAX_SEED_CHECKPOINT_BYTES = 512 * 1024 * 1024
SEED_RESUME_CHECKPOINT_GROWTH_RESERVE_BYTES = 16 * 1024 * 1024
SEED_CHECKPOINT_ARCHIVE_RECEIPT_SCHEMA_VERSION = (
    "rrc25-seed-checkpoint-archive-receipt/v1"
)
SEED_CHECKPOINT_ARCHIVE_RECEIPT_FINGERPRINT_SCHEMA = (
    "rrc25_seed_checkpoint_archive_receipt_fingerprint_v1"
)
SEED_WORKSPACE_ISOLATION_RECEIPT_SCHEMA_VERSION = (
    "rrc25-seed-workspace-isolation-receipt/v1"
)
SEED_WORKSPACE_ISOLATION_RECEIPT_FINGERPRINT_SCHEMA = (
    "rrc25_seed_workspace_isolation_receipt_fingerprint_v1"
)
SEED_SPOOL_RETIREMENT_RECEIPT_SCHEMA_VERSION = (
    "rrc25-seed-spool-retirement-receipt/v2"
)
SEED_SPOOL_RETIREMENT_RECEIPT_FINGERPRINT_SCHEMA = (
    "rrc25_seed_spool_retirement_receipt_fingerprint_v2"
)
SEED_SPOOL_RETIREMENT_ATTEMPT_RECEIPT_SCHEMA_VERSION = (
    "rrc25-seed-spool-retirement-raw-attempt-receipt/v1"
)
SEED_SPOOL_RETIREMENT_ATTEMPT_RECEIPT_FINGERPRINT_SCHEMA = (
    "rrc25_seed_spool_retirement_raw_attempt_receipt_fingerprint_v1"
)
# 退役命令没有 record-boundary checkpoint。压缩原件只有 426,797,681B，
# 以 2MiB/s 的保守只读哈希吞吐估算约 204 秒；只有单 artifact 估算严格小于
# 420 秒，且加上同进程已耗时仍严格小于 540 秒软停，才允许打开原始文件。
SEED_SPOOL_RETIREMENT_RAW_ADMISSION_SECONDS = 420.0
SEED_SPOOL_RETIREMENT_CONSERVATIVE_BYTES_PER_SECOND = float(2 * 1024 * 1024)
SEED_ACTIVE_ROOT_RETENTION_POLICY = (
    "immutable_accumulate_no_automatic_reclamation_v1"
)


class FixtureExecutor:
    """只消费 JSON 夹具的 executor；不接受路径、DSN 或数据库句柄。"""

    def __init__(self, payload: Mapping[str, Any]):
        if not isinstance(payload, Mapping):
            raise ResearchCoordinatorError("fixture executor 根节点必须是对象")
        if set(payload) != {"schema_version", "records_by_artifact"}:
            raise ResearchCoordinatorError("fixture executor 顶层字段不闭合")
        if payload.get("schema_version") != FIXTURE_SCHEMA_VERSION:
            raise ResearchCoordinatorError("fixture executor schema_version 不支持")
        records = payload.get("records_by_artifact")
        if not isinstance(records, Mapping):
            raise ResearchCoordinatorError("records_by_artifact 必须是对象")
        normalized: dict[str, tuple[ExecutionRecord, ...]] = {}
        for artifact_id in sorted(records):
            values = records[artifact_id]
            if not isinstance(artifact_id, str) or not isinstance(values, list):
                raise ResearchCoordinatorError("fixture artifact 或 records 非法")
            converted = []
            for index, item in enumerate(values):
                if not isinstance(item, Mapping) or set(item) != {
                    "record_ordinal",
                    "output_record",
                    "new_raw_bytes_read",
                    "temporary_bytes",
                    "database_write_operations",
                }:
                    raise ResearchCoordinatorError(
                        f"fixture {artifact_id}[{index}] 字段不闭合"
                    )
                converted.append(
                    ExecutionRecord(
                        artifact_id=artifact_id,
                        record_ordinal=item["record_ordinal"],
                        output_record=item["output_record"],
                        new_raw_bytes_read=item["new_raw_bytes_read"],
                        temporary_bytes=item["temporary_bytes"],
                        database_write_operations=item[
                            "database_write_operations"
                        ],
                    )
                )
            normalized[artifact_id] = tuple(converted)
        self._records = normalized

    def __call__(
        self, artifact: Mapping[str, Any], start_record_ordinal: int
    ) -> Iterable[ExecutionRecord]:
        artifact_id = artifact.get("artifact_id")
        for record in self._records.get(str(artifact_id), ()):
            if record.record_ordinal >= start_record_ordinal:
                yield record


class SeedWorkflowError(ValueError):
    """真实 seed 分段的输入、身份或资源门禁不允许继续。"""


class SeedSpoolRetirementAttemptError(SeedWorkflowError):
    """已发布 raw attempt 收据后的可审计退役失败。"""

    def __init__(
        self,
        message: str,
        *,
        result: Mapping[str, Any],
        exit_code: int,
    ) -> None:
        super().__init__(message)
        self.result = dict(result)
        self.exit_code = int(exit_code)


class _SeedRuntimeBoundaryError(SeedWorkflowError):
    """同进程 540/600 秒边界在可中断的文件块之间被观测到。"""

    def __init__(
        self,
        *,
        phase: str,
        bytes_read: int,
        observation: Mapping[str, Any],
    ) -> None:
        self.phase = phase
        self.bytes_read = int(bytes_read)
        self.observation = dict(observation)
        self.exit_code = _runtime_exit_code(observation)
        boundary = (
            "600 秒硬边界"
            if observation["hard_limit_reached"]
            else "540 秒软停边界"
        )
        super().__init__(f"{phase} 在运行中达到{boundary}，失败关闭")


@contextmanager
def _seed_execution_lock(prepared_directory: str | Path) -> Any:
    """整段持有独立 seed 单写锁，避免 start/resume/reconcile 并发改现场。"""

    prepared = Path(prepared_directory).expanduser().resolve(strict=True)
    lock = prepared / "probe-ledger" / "SEED-EXECUTION.LOCK"
    try:
        metadata = lock.lstat()
    except OSError as error:
        raise SeedWorkflowError("prepared 缺少 seed execution lock") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SeedWorkflowError("seed execution lock 必须是非符号链接普通文件")
    descriptor = os.open(lock, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SeedWorkflowError("已有 seed start/resume/reconcile 正在执行") from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _seed_checkpoint_lifecycle_policy() -> Mapping[str, Any]:
    return {
        "active_root_policy": SEED_ACTIVE_ROOT_RETENTION_POLICY,
        "automatic_deletion": False,
        "capacity_exhaustion_behavior": "fail_closed_before_publish",
        "reclamation_requires_explicit_archive_command": True,
        "archive_command": "seed-archive-checkpoint",
        "archive_requirements": [
            "full_verify_old_and_successor",
            "immutable_hash_verified_copy",
            "canonical_receipt",
            "fsync_before_active_reclamation",
        ],
        "spool_handling": "excluded_from_checkpoint_archive_command",
        "message_zh": (
            "active checkpoint 默认不可变累积且不会自动删除；容量不足时在发布前"
            "失败关闭，旧 checkpoint 只能经显式归档命令回收。"
        ),
    }


def _seed_spool_reclamation_eligibility(
    verification: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    complete = bool(
        verification is not None
        and verification.get("position", {}).get("phase") == "updates"
        and verification.get("seed_progress", {}).get(
            "seed_parse_complete", False
        )
        is True
    )
    return {
        "eligible": complete,
        "requires_explicit_archive_or_reclamation_command": True,
        "explicit_command": "seed-retire-spool",
        "automatic_deletion": False,
        "current_command_handles_spool": False,
    }


def _utc_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SeedWorkflowError(f"{field} 必须是秒级 UTC Z 时间")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError as error:
        raise SeedWorkflowError(f"{field} 不是合法秒级 UTC 时间") from error
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_resolver_profile(
    profile: Mapping[str, Any], pilot_end_exclusive: str
) -> Mapping[str, Any]:
    normalized = validate_research_profile(profile)
    pilot_end = _utc_text(pilot_end_exclusive, "pilot_end_exclusive")
    start = normalized["window"]["start_utc"]
    full_end = normalized["window"]["end_exclusive_utc"]
    if not start < pilot_end <= full_end:
        raise SeedWorkflowError(
            "seed selection 终点必须晚于起点且不得超过冻结 Profile 研究窗口"
        )
    end_minute = datetime.strptime(
        pilot_end, "%Y-%m-%dT%H:%M:%SZ"
    ).minute
    if end_minute % 5:
        raise SeedWorkflowError("seed pilot 终点必须按五分钟对齐")
    return {
        "study_id": normalized["study_id"],
        "collector_id": normalized["collector_id"],
        "country_code": normalized["country_code"],
        "window": {
            "start_utc": start,
            "end_exclusive_utc": pilot_end,
            "granularity_seconds": 300,
        },
    }


def _load_bound_code_identity(
    path: str | Path, expected_sha256: str
) -> Mapping[str, Any]:
    frozen = load_json_metadata(path, maximum_bytes=8 * 1024 * 1024)
    if frozen.get("identity_sha256") != expected_sha256:
        raise SeedWorkflowError(
            "code-identity 文件与命令行 code-sha256 不一致"
        )
    current = build_code_identity()
    if current != frozen:
        raise SeedWorkflowError("当前研究代码与冻结 code-identity 不一致")
    return frozen


def _checked_directory(
    path_value: str | Path,
    field: str,
    *,
    require_empty: bool = False,
) -> Path:
    path = Path(path_value)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SeedWorkflowError(f"{field} 不存在或不可读") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SeedWorkflowError(f"{field} 必须是非符号链接目录")
    resolved = path.resolve(strict=True)
    if require_empty:
        try:
            if next(resolved.iterdir(), None) is not None:
                recovery = (
                    "；如为 killed-before-checkpoint 现场，先运行 "
                    "seed-reconcile-workspace"
                    if field == "checkpoint_directory"
                    else ""
                )
                raise SeedWorkflowError(f"{field} 必须为空，拒绝覆盖{recovery}")
        except OSError as error:
            raise SeedWorkflowError(f"{field} 无法扫描") from error
    return resolved


def _checked_resume_checkpoint(
    path_value: str | Path, checkpoint_directory: Path
) -> Path:
    path = Path(path_value)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SeedWorkflowError("resume checkpoint 不存在或不可读") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SeedWorkflowError("resume checkpoint 必须是非符号链接普通文件")
    resolved = path.resolve(strict=True)
    if resolved.parent != checkpoint_directory:
        raise SeedWorkflowError(
            "resume checkpoint 必须直接位于本次 checkpoint-directory"
        )
    return resolved


def _checkpoint_directory_bytes(path: Path) -> int:
    total = 0
    try:
        entries = tuple(path.iterdir())
    except OSError as error:
        raise SeedWorkflowError("checkpoint_directory 无法扫描") from error
    for entry in entries:
        try:
            metadata = entry.lstat()
        except OSError as error:
            raise SeedWorkflowError("checkpoint_directory 条目不可读") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SeedWorkflowError(
                "checkpoint_directory 只能包含非符号链接普通文件"
            )
        total += metadata.st_size
    return total


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _assert_seed_mutation_path_allowed(path: Path, field: str) -> None:
    """拒绝在代码、旧项目或生产根内执行 rename/copy/unlink。"""

    repository_root = REPOSITORY_ROOT.resolve(strict=True)
    if _paths_overlap(path, repository_root):
        raise SeedWorkflowError(f"{field} 不得与代码仓库重叠")
    for root_value in (*DEFAULT_PROTECTED_ROOTS, *DEFAULT_PRODUCTION_ROOTS):
        root = Path(root_value).resolve(strict=False)
        if _paths_overlap(path, root):
            raise SeedWorkflowError(f"{field} 不得与受保护旧项目或生产目录重叠")


def _checked_independent_history_directory(
    path_value: str | Path,
    *,
    active_directory: Path,
    field: str,
) -> Path:
    history = _checked_directory(path_value, field)
    if _paths_overlap(history, active_directory):
        raise SeedWorkflowError(f"{field} 必须与 active checkpoint root 独立")
    _assert_seed_mutation_path_allowed(history, field)
    return history


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


_FILE_IDENTITY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


def _hash_stable_regular_file_with_identity(
    path: Path,
) -> tuple[str, int, tuple[int, ...]]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SeedWorkflowError(f"文件不存在、不可读或为符号链接：{path}") from error
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SeedWorkflowError(f"文件必须是非符号链接普通文件：{path}")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
        after = os.fstat(descriptor)
        if any(
            getattr(before, name) != getattr(after, name)
            for name in _FILE_IDENTITY_FIELDS
        ):
            raise SeedWorkflowError(f"文件在哈希期间发生变化：{path}")
    finally:
        os.close(descriptor)
    return (
        digest.hexdigest(),
        size,
        tuple(int(getattr(after, name)) for name in _FILE_IDENTITY_FIELDS),
    )


def _hash_stable_regular_file(path: Path) -> tuple[str, int]:
    digest, size, _identity = _hash_stable_regular_file_with_identity(path)
    return digest, size


def _regular_file_identity(path: Path) -> tuple[int, ...]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SeedWorkflowError(f"文件不存在、不可读或为符号链接：{path}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SeedWorkflowError(f"文件必须是非符号链接普通文件：{path}")
        return tuple(
            int(getattr(metadata, name)) for name in _FILE_IDENTITY_FIELDS
        )
    finally:
        os.close(descriptor)


def _file_identity_payload(identity: Sequence[int]) -> Mapping[str, int]:
    if len(identity) != len(_FILE_IDENTITY_FIELDS):
        raise SeedWorkflowError("文件身份字段数量不闭合")
    return {
        name: int(value)
        for name, value in zip(_FILE_IDENTITY_FIELDS, identity)
    }


def _file_identity_from_payload(
    payload: Any,
    *,
    field: str,
) -> tuple[int, ...]:
    if not isinstance(payload, Mapping) or set(payload) != set(
        _FILE_IDENTITY_FIELDS
    ):
        raise SeedWorkflowError(f"{field} 文件身份字段不闭合")
    values: list[int] = []
    for name in _FILE_IDENTITY_FIELDS:
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SeedWorkflowError(f"{field}.{name} 必须是非负整数")
        values.append(value)
    return tuple(values)


def _copy_regular_create_only(source: Path, destination: Path) -> tuple[str, int]:
    """先完整复制并 fsync 临时文件，再以 create-only 语义发布。"""

    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"归档目标已存在，拒绝覆盖：{destination}")
    temporary = destination.parent / (
        f".{destination.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    source_fd = None
    target_fd = None
    digest = hashlib.sha256()
    size = 0
    try:
        source_fd = os.open(
            source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        source_before = os.fstat(source_fd)
        if not stat.S_ISREG(source_before.st_mode):
            raise SeedWorkflowError("待归档 checkpoint 必须是普通文件")
        target_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o640,
        )
        while True:
            block = os.read(source_fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
            view = memoryview(block)
            while view:
                written = os.write(target_fd, view)
                if written <= 0:
                    raise SeedWorkflowError("checkpoint 归档复制未取得进展")
                view = view[written:]
        os.fsync(target_fd)
        os.close(target_fd)
        target_fd = None
        source_after = os.fstat(source_fd)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(
            getattr(source_before, name) != getattr(source_after, name)
            for name in identity
        ):
            raise SeedWorkflowError("待归档 checkpoint 在复制期间发生变化")
        copied_sha, copied_size = _hash_stable_regular_file(temporary)
        if copied_sha != digest.hexdigest() or copied_size != size:
            raise SeedWorkflowError("checkpoint 归档副本 SHA/size 复核失败")
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError as error:
            raise FileExistsError(
                f"归档目标已存在，拒绝覆盖：{destination}"
            ) from error
        _fsync_directory(destination.parent)
    finally:
        if target_fd is not None:
            os.close(target_fd)
        if source_fd is not None:
            os.close(source_fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return digest.hexdigest(), size


def _archive_receipt_payload(semantic: Mapping[str, Any]) -> Mapping[str, Any]:
    normalized = {
        **dict(semantic),
        "schema_version": SEED_CHECKPOINT_ARCHIVE_RECEIPT_SCHEMA_VERSION,
    }
    fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": SEED_CHECKPOINT_ARCHIVE_RECEIPT_FINGERPRINT_SCHEMA,
                "receipt": normalized,
            }
        ).encode("utf-8")
    ).hexdigest()
    return {**normalized, "receipt_fingerprint_sha256": fingerprint}


def _seed_workspace_isolation_receipt_payload(
    semantic: Mapping[str, Any],
) -> Mapping[str, Any]:
    normalized = {
        **dict(semantic),
        "schema_version": SEED_WORKSPACE_ISOLATION_RECEIPT_SCHEMA_VERSION,
    }
    fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": SEED_WORKSPACE_ISOLATION_RECEIPT_FINGERPRINT_SCHEMA,
                "receipt": normalized,
            }
        ).encode("utf-8")
    ).hexdigest()
    return {**normalized, "receipt_fingerprint_sha256": fingerprint}


def _spool_retirement_receipt_payload(
    semantic: Mapping[str, Any],
) -> Mapping[str, Any]:
    normalized = {
        **dict(semantic),
        "schema_version": SEED_SPOOL_RETIREMENT_RECEIPT_SCHEMA_VERSION,
    }
    fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": SEED_SPOOL_RETIREMENT_RECEIPT_FINGERPRINT_SCHEMA,
                "receipt": normalized,
            }
        ).encode("utf-8")
    ).hexdigest()
    return {**normalized, "receipt_fingerprint_sha256": fingerprint}


def _spool_retirement_attempt_receipt_payload(
    semantic: Mapping[str, Any],
) -> Mapping[str, Any]:
    normalized = {
        **dict(semantic),
        "schema_version": (
            SEED_SPOOL_RETIREMENT_ATTEMPT_RECEIPT_SCHEMA_VERSION
        ),
    }
    fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": (
                    SEED_SPOOL_RETIREMENT_ATTEMPT_RECEIPT_FINGERPRINT_SCHEMA
                ),
                "receipt": normalized,
            }
        ).encode("utf-8")
    ).hexdigest()
    return {**normalized, "receipt_fingerprint_sha256": fingerprint}


def _load_prior_spool_retirement_attempts(
    directory: Path,
    *,
    spool_name: str,
    selection_id: str,
    checkpoint_fingerprint_sha256: str,
    seed_artifact: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    """复核同一 spool 的 create-only attempt 收据并返回累计预留证据。"""

    prefix = f"{spool_name}.raw-verification-attempt."
    suffix = ".json"
    attempts: list[Mapping[str, Any]] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if not path.name.startswith(prefix) or not path.name.endswith(suffix):
            continue
        try:
            metadata = path.lstat()
            payload_bytes = path.read_bytes()
            payload = json.loads(payload_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SeedWorkflowError("既有 spool raw attempt 收据不可读") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SeedWorkflowError("既有 spool raw attempt 收据必须是普通文件")
        if metadata.st_size > 2 * 1024 * 1024 or not isinstance(payload, Mapping):
            raise SeedWorkflowError("既有 spool raw attempt 收据过大或结构非法")
        expected_bytes = (canonical_json(dict(payload)) + "\n").encode("utf-8")
        if payload_bytes != expected_bytes:
            raise SeedWorkflowError("既有 spool raw attempt 收据不是规范 JSON")
        semantic = {
            key: value
            for key, value in payload.items()
            if key not in {"schema_version", "receipt_fingerprint_sha256"}
        }
        expected_payload = _spool_retirement_attempt_receipt_payload(semantic)
        raw_accounting = payload.get("raw_accounting")
        compressed = payload.get("compressed_raw_expected")
        checkpoint = payload.get("checkpoint")
        if (
            dict(payload) != expected_payload
            or payload.get("operation")
            != "seed_spool_retirement_raw_verification_attempt"
            or payload.get("selection_id") != selection_id
            or not isinstance(checkpoint, Mapping)
            or checkpoint.get("checkpoint_fingerprint_sha256")
            != checkpoint_fingerprint_sha256
            or not isinstance(compressed, Mapping)
            or compressed.get("artifact_id") != seed_artifact.get("artifact_id")
            or compressed.get("relative_path")
            != seed_artifact.get("relative_path")
            or compressed.get("file_sha256")
            != seed_artifact.get("file_sha256")
            or compressed.get("size_bytes") != seed_artifact.get("size_bytes")
            or not isinstance(raw_accounting, Mapping)
            or raw_accounting.get("full_artifact_reserved_bytes")
            != seed_artifact.get("size_bytes")
            or raw_accounting.get("reservation_policy")
            != "full_artifact_reserved_before_open_failed_or_crashed_attempts_still_count"
        ):
            raise SeedWorkflowError("既有 spool raw attempt 收据身份或累计语义不闭合")
        attempts.append(dict(payload))
    return tuple(attempts)


def _verify_canonical_receipt(
    path: Path,
    *,
    expected: Mapping[str, Any],
) -> None:
    digest, size = _hash_stable_regular_file(path)
    if size > 2 * 1024 * 1024:
        raise SeedWorkflowError("归档/退役收据超过 2MiB")
    try:
        payload_bytes = path.read_bytes()
        payload = json.loads(payload_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SeedWorkflowError("归档/退役收据不可读或 JSON 非法") from error
    expected_bytes = (canonical_json(dict(expected)) + "\n").encode("utf-8")
    if payload != dict(expected) or payload_bytes != expected_bytes:
        raise SeedWorkflowError("归档/退役收据复核失败")
    if hashlib.sha256(payload_bytes).hexdigest() != digest:
        raise SeedWorkflowError("归档/退役收据读取身份不稳定")


def _load_spool_retirement_receipt(path: Path) -> Mapping[str, Any]:
    """读取并自证一份已发布的退役成功收据，不信任其字段内容。"""

    digest, size = _hash_stable_regular_file(path)
    if size > 2 * 1024 * 1024:
        raise SeedWorkflowError("spool 退役成功收据超过 2MiB")
    try:
        payload_bytes = path.read_bytes()
        payload = json.loads(payload_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SeedWorkflowError("spool 退役成功收据不可读或 JSON 非法") from error
    if not isinstance(payload, Mapping):
        raise SeedWorkflowError("spool 退役成功收据根节点必须是对象")
    semantic = {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "receipt_fingerprint_sha256"}
    }
    expected = _spool_retirement_receipt_payload(semantic)
    expected_bytes = (canonical_json(dict(expected)) + "\n").encode("utf-8")
    if dict(payload) != expected or payload_bytes != expected_bytes:
        raise SeedWorkflowError("spool 退役成功收据规范内容或指纹复核失败")
    if hashlib.sha256(payload_bytes).hexdigest() != digest:
        raise SeedWorkflowError("spool 退役成功收据读取身份不稳定")
    return dict(payload)


def _validate_spool_retirement_success_receipt(
    receipt: Mapping[str, Any],
    *,
    receipt_path: Path,
    checkpoint_path: Path,
    verified: Mapping[str, Any],
    spool_path: Path,
    spool_binding: Mapping[str, Any],
    compressed_path: Path,
    compressed_identity: tuple[int, ...],
    seed_artifact: Mapping[str, Any],
    prior_attempts: Sequence[Mapping[str, Any]],
    max_new_raw_read_bytes: int,
) -> Mapping[str, Any]:
    """闭合验证成功收据、raw identity 与其 create-only attempt。"""

    checkpoint = receipt.get("checkpoint")
    spool = receipt.get("spool")
    compressed = receipt.get("compressed_raw")
    attempt_ref = receipt.get("raw_verification_attempt_receipt")
    accounting = receipt.get("resource_accounting")
    if (
        receipt.get("operation") != "seed_spool_retirement"
        or receipt.get("recoverable_by_rebuild_from_compressed_raw") is not True
        or not isinstance(checkpoint, Mapping)
        or checkpoint.get("path") != str(checkpoint_path)
        or checkpoint.get("checkpoint_sequence")
        != verified.get("checkpoint_sequence")
        or checkpoint.get("checkpoint_fingerprint_sha256")
        != verified.get("checkpoint_fingerprint_sha256")
    ):
        raise SeedWorkflowError("spool 退役成功收据与 checkpoint 身份不一致")
    if (
        not isinstance(spool, Mapping)
        or spool.get("path") != str(spool_path)
        or spool.get("sha256") != spool_binding.get("sha256")
        or spool.get("size_bytes") != spool_binding.get("size_bytes")
    ):
        raise SeedWorkflowError("spool 退役成功收据的 spool 身份不一致")
    stored_spool_identity = _file_identity_from_payload(
        spool.get("stable_file_identity"), field="receipt.spool"
    )
    if (
        not isinstance(compressed, Mapping)
        or compressed.get("path") != str(compressed_path)
        or compressed.get("relative_path")
        != seed_artifact.get("relative_path")
        or compressed.get("artifact_id") != seed_artifact.get("artifact_id")
        or compressed.get("sha256") != seed_artifact.get("file_sha256")
        or compressed.get("size_bytes") != seed_artifact.get("size_bytes")
        or compressed.get("hash_verified") is not True
    ):
        raise SeedWorkflowError("spool 退役成功收据的压缩原件身份不一致")
    stored_compressed_identity = _file_identity_from_payload(
        compressed.get("stable_file_identity"), field="receipt.compressed_raw"
    )
    if compressed_identity != stored_compressed_identity:
        raise SeedWorkflowError("压缩 seed 原件与成功收据记录的稳定文件身份不一致")
    if not isinstance(attempt_ref, Mapping):
        raise SeedWorkflowError("spool 退役成功收据缺少 raw attempt 引用")
    matching_attempts = [
        attempt
        for attempt in prior_attempts
        if attempt.get("attempt_id") == attempt_ref.get("attempt_id")
        and attempt.get("receipt_fingerprint_sha256")
        == attempt_ref.get("receipt_fingerprint_sha256")
    ]
    if len(matching_attempts) != 1:
        raise SeedWorkflowError("spool 退役成功收据未唯一关联既有 raw attempt")
    attempt = matching_attempts[0]
    if not prior_attempts or attempt != prior_attempts[-1]:
        raise SeedWorkflowError(
            "spool 成功收据之后存在未被其引用的额外 raw attempt，拒绝恢复退役"
        )
    expected_attempt_path = receipt_path.parent / (
        f"{spool_path.name}.raw-verification-attempt."
        f"{attempt['attempt_id']}.json"
    )
    if (
        attempt_ref.get("path") != str(expected_attempt_path)
        or attempt_ref.get("status") != attempt.get("status")
        or attempt_ref.get("durable_before_raw_open") is not True
        or attempt.get("spool") != spool
    ):
        raise SeedWorkflowError("spool 退役成功收据的 raw attempt 引用不闭合")
    attempt_accounting = attempt.get("raw_accounting")
    if not isinstance(accounting, Mapping) or not isinstance(
        attempt_accounting, Mapping
    ):
        raise SeedWorkflowError("spool 退役成功收据缺少原始读取核算")
    cumulative_after = accounting.get(
        "cumulative_new_raw_read_bytes_after_retirement_verification"
    )
    if (
        cumulative_after
        != attempt_accounting.get(
            "cumulative_new_raw_read_bytes_after_reservation"
        )
        or accounting.get("retirement_verification_new_raw_read_bytes")
        != attempt_accounting.get("full_artifact_reserved_bytes")
        or accounting.get("checkpoint_cumulative_new_raw_read_bytes")
        != attempt_accounting.get("checkpoint_cumulative_new_raw_read_bytes")
        or isinstance(cumulative_after, bool)
        or not isinstance(cumulative_after, int)
        or cumulative_after >= max_new_raw_read_bytes
    ):
        raise SeedWorkflowError("spool 退役成功收据的原始读取累计不闭合")
    return {
        "spool_identity": stored_spool_identity,
        "compressed_identity": stored_compressed_identity,
        "attempt": dict(attempt),
    }


def _checkpoint_progress_key(verification: Mapping[str, Any]) -> tuple[int, int, int]:
    phase = verification["position"]["phase"]
    progress = verification["seed_progress"]
    return (
        1 if phase == "updates" else 0,
        int(progress["next_record_ordinal"]),
        int(progress["next_record_offset"]),
    )


def _seed_write_gate(
    *,
    profile: Mapping[str, Any],
    checkpoint_directory: Path,
    seed_size_bytes: int,
    seed_spool_attestation: Mapping[str, Any],
    prior_raw_read_bytes: int,
    planned_seed_checkpoint_seconds: float,
    resume: bool,
    reuse_existing_seed_spool: bool = False,
    already_complete: bool = False,
    prior_checkpoint_size_bytes: int = 0,
) -> Mapping[str, Any]:
    if (
        isinstance(prior_raw_read_bytes, bool)
        or not isinstance(prior_raw_read_bytes, int)
        or prior_raw_read_bytes < 0
    ):
        raise SeedWorkflowError("prior raw read bytes 必须是非负整数")
    if (
        isinstance(planned_seed_checkpoint_seconds, bool)
        or not isinstance(planned_seed_checkpoint_seconds, (int, float))
    ):
        raise SeedWorkflowError("planned seed checkpoint 秒数必须是有限正数")
    planned = float(planned_seed_checkpoint_seconds)
    if (
        not math.isfinite(planned)
        or planned <= 0
        or planned > MAX_SEED_CHECKPOINT_SECONDS
    ):
        raise SeedWorkflowError(
            f"planned seed checkpoint 必须位于 (0,{MAX_SEED_CHECKPOINT_SECONDS:g}] 秒"
        )
    limits = effective_resource_limits(profile)
    if not isinstance(reuse_existing_seed_spool, bool):
        raise SeedWorkflowError("reuse existing seed spool 必须是布尔值")
    if resume and reuse_existing_seed_spool:
        raise SeedWorkflowError(
            "reuse existing seed spool 只适用于 seed-start"
        )
    spool_size_bytes = int(seed_spool_attestation["decompressed"]["size_bytes"])
    current_directory_bytes = _checkpoint_directory_bytes(checkpoint_directory)
    if (
        isinstance(prior_checkpoint_size_bytes, bool)
        or not isinstance(prior_checkpoint_size_bytes, int)
        or prior_checkpoint_size_bytes < 0
    ):
        raise SeedWorkflowError("prior checkpoint size 必须是非负整数")
    projected_new_raw_bytes = prior_raw_read_bytes
    if (
        not resume
        and not already_complete
        and not reuse_existing_seed_spool
    ):
        projected_new_raw_bytes += seed_size_bytes
    # 420 秒解析预算之外为 batch flush、checkpoint 原子写入与只读复核保留
    # 60 秒；临时空间使用冻结的完整解压实测量，并为 checkpoint 实际字节
    # 预留其 512 MiB 单文件上限。worker 仍按实时时钟与实际字节执行门禁。
    if already_complete:
        projected_temporary_bytes = 0
        projected_output_bytes = 0
        projection_method = "completed_readonly_noop"
    elif resume:
        projected_output_bytes = min(
            MAX_SEED_CHECKPOINT_BYTES,
            prior_checkpoint_size_bytes
            + SEED_RESUME_CHECKPOINT_GROWTH_RESERVE_BYTES,
        )
        projected_temporary_bytes = (
            current_directory_bytes + projected_output_bytes
        )
        projection_method = "conservative_estimate_with_runtime_exact_gate"
    elif reuse_existing_seed_spool:
        projected_output_bytes = MAX_SEED_CHECKPOINT_BYTES
        projected_temporary_bytes = (
            current_directory_bytes + projected_output_bytes
        )
        projection_method = "verified_existing_spool_plus_checkpoint"
    else:
        projected_temporary_bytes = (
            current_directory_bytes
            + spool_size_bytes
            + MAX_SEED_CHECKPOINT_BYTES
        )
        projected_output_bytes = MAX_SEED_CHECKPOINT_BYTES
        projection_method = "single_file_limit_reservation"
    usage = ResourceUsage(
        new_raw_read_bytes=projected_new_raw_bytes,
        process_runtime_seconds=planned + 60.0,
        temporary_bytes=projected_temporary_bytes,
        output_bytes=projected_output_bytes,
        write_targets=(
            WriteTarget(
                label="checkpoint_directory",
                location=str(checkpoint_directory),
                kind="checkpoint",
            ),
        ),
        phase="estimated",
    )
    gate = evaluate_resource_gate(
        usage,
        limits=limits,
        protected_roots=tuple(
            str(Path(root).resolve(strict=False))
            for root in DEFAULT_PROTECTED_ROOTS
        ),
        production_roots=tuple(
            str(Path(root).resolve(strict=False))
            for root in DEFAULT_PRODUCTION_ROOTS
        ),
    )
    result = gate.to_dict()
    result["temporary_projection"] = {
        "method": projection_method,
        "is_hard_upper_bound": already_complete or not resume,
        "spool_attested_size_bytes": spool_size_bytes,
        "current_checkpoint_root_bytes": current_directory_bytes,
        "prior_checkpoint_size_bytes": prior_checkpoint_size_bytes,
        "resume_growth_reserve_bytes": (
            SEED_RESUME_CHECKPOINT_GROWTH_RESERVE_BYTES if resume else 0
        ),
        "projected_new_checkpoint_bytes": projected_output_bytes,
        "projected_temporary_total_bytes": projected_temporary_bytes,
        "runtime_exact_gate_required": not already_complete,
    }
    return result


def _seed_context(
    args: argparse.Namespace,
    *,
    require_empty_checkpoint_directory: bool,
    resume_checkpoint_path: str | Path | None,
    reconcile_abandoned_seed: bool = False,
) -> Mapping[str, Any]:
    profile = validate_research_profile(load_json_metadata(args.profile))
    manifest = load_json_metadata(args.manifest, maximum_bytes=512 * 1024 * 1024)
    verification = load_json_metadata(args.manifest_verification)
    compatible_snapshot = load_json_metadata(
        args.mapping, maximum_bytes=64 * 1024 * 1024
    )
    revised_snapshot = load_json_metadata(
        args.revised_mapping, maximum_bytes=16 * 1024 * 1024
    )
    code_identity = _load_bound_code_identity(
        args.code_identity, args.code_sha256
    )
    selection = resolve_research_inputs(
        manifest,
        verification,
        _seed_resolver_profile(profile, args.pilot_end_exclusive),
    )
    if selection.get("status") != "complete":
        failure_codes = ",".join(
            str(row.get("code")) for row in selection.get("failures", [])
        )
        raise SeedWorkflowError(
            "seed/B1 selection 不完整，拒绝把输入缺口解释为事件："
            + failure_codes
        )
    seed = selection.get("roles", {}).get("state_seed_rib")
    if not isinstance(seed, Mapping):
        raise SeedWorkflowError("selection 缺少 state_seed_rib")
    seed_spool_attestation = validate_seed_spool_attestation(
        load_json_metadata(args.seed_spool_attestation, maximum_bytes=1024 * 1024),
        seed_artifact=seed,
    )
    compatible = mapping_view_from_frozen_snapshot(compatible_snapshot)
    revised = mapping_view_from_revised_snapshot(
        revised_snapshot, compatible_snapshot
    )
    raw_retention = build_raw_retention_mapping_union((compatible, revised))
    seed_rib_prefilter = (
        None
        if getattr(args, "seed_rib_prefilter", None) is None
        else load_json_metadata(
            args.seed_rib_prefilter,
            maximum_bytes=128 * 1024 * 1024,
        )
    )
    reuse_existing_seed_spool = bool(
        getattr(args, "reuse_existing_seed_spool", False)
    )
    if reuse_existing_seed_spool and resume_checkpoint_path is not None:
        raise SeedWorkflowError(
            "--reuse-existing-seed-spool 只适用于 seed-start"
        )

    checkpoint_directory = _checked_directory(
        args.checkpoint_directory,
        "checkpoint_directory",
        require_empty=require_empty_checkpoint_directory,
    )
    raw_root = _checked_directory(args.raw_root, "raw_root")
    if (
        checkpoint_directory == raw_root
        or checkpoint_directory in raw_root.parents
        or raw_root in checkpoint_directory.parents
    ):
        raise SeedWorkflowError("checkpoint_directory 不得与 raw_root 重叠或嵌套")
    repository_root = REPOSITORY_ROOT.resolve(strict=True)
    if (
        checkpoint_directory == repository_root
        or checkpoint_directory in repository_root.parents
        or repository_root in checkpoint_directory.parents
    ):
        raise SeedWorkflowError("checkpoint_directory 不得与代码仓库重叠或嵌套")

    try:
        prior_raw_accounting = verify_probe_raw_ledger_terminal(
            args.prepared_directory,
            args.probe_ledger_terminal,
            raw_root=raw_root,
        )
    except (ExecutionPrepError, OSError, ValueError) as error:
        raise SeedWorkflowError(
            "seed prior raw 必须来自 prepared 内唯一、已闭合的 probe terminal ledger"
        ) from error
    expected_bindings = {
        "profile_sha256": profile_sha256(profile),
        "input_selection_sha256": selection["semantic_fingerprint_sha256"],
        "code_sha256": code_identity["identity_sha256"],
        "mapping_sha256": mapping_bundle_sha256(
            compatible_snapshot, revised_snapshot
        ),
    }
    if (
        prior_raw_accounting.get("prepared_bindings") != expected_bindings
        or prior_raw_accounting.get("selection_id") != selection.get("selection_id")
        or prior_raw_accounting.get("terminal_receipt_kind")
        not in {"zero_genesis", "imported_genesis", "outcome"}
    ):
        raise SeedWorkflowError("probe terminal ledger 与 seed 冻结输入身份不一致")
    prior_new_raw_bytes = prior_raw_accounting.get(
        "cumulative_reserved_new_raw_bytes"
    )
    if (
        isinstance(prior_new_raw_bytes, bool)
        or not isinstance(prior_new_raw_bytes, int)
        or prior_new_raw_bytes < 0
    ):
        raise SeedWorkflowError("probe terminal ledger 累计 raw 字节非法")

    seed_artifact = selection["roles"]["state_seed_rib"]
    seed_reconciliation = None
    if reconcile_abandoned_seed:
        try:
            seed_reconciliation = reconcile_abandoned_seed_raw_attempt(
                args.prepared_directory,
                args.probe_ledger_terminal,
                raw_root=raw_root,
                seed_artifact=seed_artifact,
            )
        except (ExecutionPrepError, OSError, ValueError) as error:
            raise SeedWorkflowError(
                "seed durable reservation 遗留 attempt 无法安全闭合"
            ) from error
    try:
        seed_raw_ledger = verify_seed_raw_ledger(
            args.prepared_directory,
            args.probe_ledger_terminal,
            raw_root=raw_root,
            seed_artifact=seed_artifact,
        )
    except (ExecutionPrepError, OSError, ValueError) as error:
        raise SeedWorkflowError("seed raw durable reservation ledger 未闭合") from error

    checkpoint = None
    prior_raw_read_bytes = int(
        seed_raw_ledger["current_cumulative_reserved_new_raw_bytes"]
    )
    worker_prior_new_raw_bytes = prior_raw_read_bytes
    seed_raw_reservation = None
    prior_verification = None
    resume_checkpoint_verification_deferred = False
    if resume_checkpoint_path is not None:
        checkpoint = _checked_resume_checkpoint(
            resume_checkpoint_path, checkpoint_directory
        )
        resume_checkpoint_verification_deferred = bool(
            getattr(args, "defer_checkpoint_verification", False)
        )
        if resume_checkpoint_verification_deferred:
            # resume 的 worker 会在恢复任何状态前完整读取、验指纹并核验
            # selection/mapping/code/spool/ledger 身份。显式快速路径只使用已独立
            # 核验并闭合的 durable seed ledger 做 preflight，避免同一进程先把
            # 数 GB 解压 JSON 重复验证一遍；严格 checkpoint 核验仍 fail-closed。
            seed_raw_reservation = seed_raw_ledger.get("latest_reservation")
            if seed_raw_reservation is None:
                worker_prior_new_raw_bytes = int(prior_new_raw_bytes)
            elif isinstance(seed_raw_reservation, Mapping):
                reservation_prior = seed_raw_reservation.get(
                    "prior_cumulative_reserved_new_raw_bytes"
                )
                reservation_cumulative = seed_raw_reservation.get(
                    "cumulative_reserved_new_raw_bytes"
                )
                if (
                    isinstance(reservation_prior, bool)
                    or not isinstance(reservation_prior, int)
                    or reservation_prior < 0
                    or reservation_cumulative != prior_raw_read_bytes
                ):
                    raise SeedWorkflowError(
                        "seed durable ledger 的恢复累计与最新 reservation 不闭合"
                    )
                worker_prior_new_raw_bytes = reservation_prior
            else:
                raise SeedWorkflowError(
                    "seed durable ledger latest_reservation 非法"
                )
        else:
            prior_verification = verify_full_seed_checkpoint(
                checkpoint,
                selection=selection,
                country_mapping=compatible,
                raw_retention_mapping=raw_retention,
                seed_spool_attestation=seed_spool_attestation,
                pilot_end_exclusive_utc=args.pilot_end_exclusive,
                code_identity_sha256=args.code_sha256,
            )
            prior_raw_read_bytes = int(
                prior_verification["resources"]["new_raw_read_bytes"]
            )
            worker_prior_new_raw_bytes = int(
                prior_verification["resources"]["prior_new_raw_read_bytes"]
            )
            seed_raw_reservation = prior_verification["resources"].get(
                "seed_raw_reservation"
            )
            if (
                prior_verification["resources"].get("prior_raw_accounting")
                != prior_raw_accounting
                or seed_raw_ledger.get("latest_reservation")
                != seed_raw_reservation
                or seed_raw_ledger.get(
                    "current_cumulative_reserved_new_raw_bytes"
                )
                != prior_raw_read_bytes
            ):
                raise SeedWorkflowError(
                    "probe/seed durable ledger 与恢复 checkpoint 冻结累计不一致"
                )
    resource_gate = _seed_write_gate(
        profile=profile,
        checkpoint_directory=checkpoint_directory,
        seed_size_bytes=int(seed["size_bytes"]),
        seed_spool_attestation=seed_spool_attestation,
        prior_raw_read_bytes=prior_raw_read_bytes,
        planned_seed_checkpoint_seconds=args.planned_seed_checkpoint_seconds,
        resume=resume_checkpoint_path is not None,
        reuse_existing_seed_spool=reuse_existing_seed_spool,
        already_complete=(
            prior_verification is not None
            and prior_verification["position"]["phase"] == "updates"
        ),
        prior_checkpoint_size_bytes=(
            checkpoint.stat().st_size if checkpoint is not None else 0
        ),
    )
    return {
        "profile": profile,
        "selection": selection,
        "compatible_mapping": compatible,
        "revised_mapping": revised,
        "raw_retention_mapping": raw_retention,
        "seed_rib_prefilter": seed_rib_prefilter,
        "code_identity": code_identity,
        "seed_spool_attestation": seed_spool_attestation,
        "checkpoint_directory": checkpoint_directory,
        "raw_root": raw_root,
        "resume_checkpoint": checkpoint,
        "prior_checkpoint_verification": prior_verification,
        "resume_checkpoint_verification_deferred": (
            resume_checkpoint_verification_deferred
        ),
        "probe_terminal_prior_new_raw_read_bytes": prior_new_raw_bytes,
        "prior_new_raw_read_bytes": worker_prior_new_raw_bytes,
        "prior_raw_accounting": prior_raw_accounting,
        "seed_raw_reservation": seed_raw_reservation,
        "seed_raw_ledger": seed_raw_ledger,
        "seed_reconciliation": seed_reconciliation,
        "reuse_existing_seed_spool": reuse_existing_seed_spool,
        "resource_gate": resource_gate,
    }


def _seed_public_plan(context: Mapping[str, Any]) -> Mapping[str, Any]:
    selection = context["selection"]
    seed = selection["roles"]["state_seed_rib"]
    compatible = context["compatible_mapping"]
    revised = context["revised_mapping"]
    lineage = revised.revised_lineage
    prior = context["prior_checkpoint_verification"]
    gate = context["resource_gate"]
    attestation = context["seed_spool_attestation"]
    already_complete = (
        prior is not None and prior["position"]["phase"] == "updates"
    )
    return {
        "ok": bool(gate["execution_allowed"]),
        "schema_version": "rrc25-iran-full-seed-plan/v1",
        "opens_raw_mrt": False,
        "reuse_existing_seed_spool": bool(
            context.get("reuse_existing_seed_spool", False)
        ),
        "already_complete": already_complete,
        "database_connections": 0,
        "database_write_operations": 0,
        "prior_new_raw_read_bytes": context["prior_new_raw_read_bytes"],
        "prior_raw_accounting": context["prior_raw_accounting"],
        "selection_id": selection["selection_id"],
        "selection_semantic_fingerprint_sha256": selection[
            "semantic_fingerprint_sha256"
        ],
        "selection_end_semantics": (
            "pilot_end_exclusive 参数是兼容名称；其值实际冻结本次 seed 与后续 "
            "UPDATE journal 共用的 selection end，可等于完整 Profile 终点。"
        ),
        "pilot_start_utc": selection["window"]["start_utc"],
        "pilot_end_exclusive_utc": selection["window"]["end_exclusive_utc"],
        "seed_artifact": {
            key: seed[key]
            for key in (
                "artifact_id",
                "artifact_time_utc",
                "relative_path",
                "file_sha256",
                "size_bytes",
            )
        },
        "code_identity_sha256": context["code_identity"]["identity_sha256"],
        "seed_spool_attestation": {
            "semantic_fingerprint_sha256": attestation[
                "semantic_fingerprint_sha256"
            ],
            "decompressed_size_bytes": attestation["decompressed"]["size_bytes"],
            "decompressed_sha256": attestation["decompressed"]["sha256"],
        },
        "mapping": {
            "target_country": compatible.target_country,
            "compatible_source_sha256": compatible.source_sha256,
            "revised_source_sha256": revised.source_sha256,
            "revised_delta_asn_count": (
                len(lineage.delta_entries) if lineage is not None else 0
            ),
            "raw_retention_semantics": context[
                "raw_retention_mapping"
            ].semantics,
        },
        "checkpoint_lifecycle": _seed_checkpoint_lifecycle_policy(),
        "seed_spool_reclamation_eligibility": {
            "eligible": already_complete,
            "requires_explicit_archive_or_reclamation_command": True,
            "explicit_command": "seed-retire-spool",
            "automatic_deletion": False,
            "current_command_handles_spool": False,
            "message_zh": (
                "仅 seed 完成并通过完整 checkpoint 验证后具备显式归档或回收"
                "资格；seed-* 执行与 checkpoint 归档命令均不会自动处理 spool。"
            ),
        },
        "resume": (
            None
            if prior is None
            else {
                "checkpoint_fingerprint_sha256": prior[
                    "checkpoint_fingerprint_sha256"
                ],
                "position": prior["position"],
                "seed_progress": prior["seed_progress"],
                "cumulative_new_raw_read_bytes": prior["resources"][
                    "new_raw_read_bytes"
                ],
            }
        ),
        "resource_gate": gate,
    }


def _reject_update_stream(_artifact: Mapping[str, Any]) -> Iterable[Any]:
    raise SeedWorkflowError("seed-* 子命令禁止打开 UPDATE")


def _process_runtime_observation(
    *,
    clock: Any,
    process_started_at: float,
    planned_seconds: float,
    limits: Any,
) -> Mapping[str, Any]:
    now = clock()
    if (
        isinstance(now, bool)
        or not isinstance(now, (int, float))
        or not math.isfinite(float(now))
    ):
        raise SeedWorkflowError("clock 必须返回有限数")
    elapsed = float(now) - float(process_started_at)
    if not math.isfinite(elapsed) or elapsed < 0:
        raise SeedWorkflowError("clock 不得倒退且必须返回有限数")
    return {
        "elapsed_seconds": elapsed,
        "planned_checkpoint_seconds": float(planned_seconds),
        "worker_soft_stop_seconds": float(limits.worker_soft_stop_seconds),
        "max_worker_runtime_seconds": float(limits.max_worker_runtime_seconds),
        "planned_checkpoint_reached": elapsed >= float(planned_seconds),
        "soft_stop_reached": elapsed >= float(limits.worker_soft_stop_seconds),
        "hard_limit_reached": elapsed >= float(limits.max_worker_runtime_seconds),
        "remaining_to_planned_checkpoint_seconds": max(
            0.0, float(planned_seconds) - elapsed
        ),
    }


def _runtime_exit_code(observation: Mapping[str, Any]) -> int:
    if observation["hard_limit_reached"]:
        return 3
    if observation["soft_stop_reached"]:
        return 4
    return 0


def _process_clock_start(
    *,
    clock: Any,
    process_started_at: float | None = None,
) -> float:
    observed = clock()
    if (
        isinstance(observed, bool)
        or not isinstance(observed, (int, float))
        or not math.isfinite(float(observed))
    ):
        raise SeedWorkflowError("clock 必须返回有限数")
    observed = float(observed)
    if process_started_at is None:
        return observed
    if (
        isinstance(process_started_at, bool)
        or not isinstance(process_started_at, (int, float))
        or not math.isfinite(float(process_started_at))
        or float(process_started_at) > observed
    ):
        raise SeedWorkflowError("process_started_at 必须是不晚于当前 clock 的有限数")
    return float(process_started_at)


def _raise_if_seed_runtime_boundary(
    *,
    clock: Any,
    process_started_at: float,
    limits: Any,
    phase: str,
    bytes_read: int,
) -> Mapping[str, Any]:
    observation = _process_runtime_observation(
        clock=clock,
        process_started_at=process_started_at,
        planned_seconds=SEED_SPOOL_RETIREMENT_RAW_ADMISSION_SECONDS,
        limits=limits,
    )
    if _runtime_exit_code(observation):
        raise _SeedRuntimeBoundaryError(
            phase=phase,
            bytes_read=bytes_read,
            observation=observation,
        )
    return observation


def _seed_spool_retirement_raw_admission(
    *,
    compressed_size_bytes: int,
    process_runtime: Mapping[str, Any],
    limits: Any,
    conservative_bytes_per_second: float = (
        SEED_SPOOL_RETIREMENT_CONSERVATIVE_BYTES_PER_SECOND
    ),
) -> Mapping[str, Any]:
    if (
        isinstance(compressed_size_bytes, bool)
        or not isinstance(compressed_size_bytes, int)
        or compressed_size_bytes <= 0
    ):
        raise SeedWorkflowError("压缩 seed 已知大小必须是正整数")
    if (
        isinstance(conservative_bytes_per_second, bool)
        or not isinstance(conservative_bytes_per_second, (int, float))
        or not math.isfinite(float(conservative_bytes_per_second))
        or float(conservative_bytes_per_second) <= 0
    ):
        raise SeedWorkflowError("保守哈希吞吐必须是有限正数")
    elapsed = process_runtime.get("elapsed_seconds")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(float(elapsed))
        or float(elapsed) < 0
    ):
        raise SeedWorkflowError("退役 admission 缺少合法同进程已耗时")
    estimated = compressed_size_bytes / float(conservative_bytes_per_second)
    projected_process_elapsed = float(elapsed) + estimated
    artifact_allowed = estimated < SEED_SPOOL_RETIREMENT_RAW_ADMISSION_SECONDS
    process_allowed = projected_process_elapsed < float(
        limits.worker_soft_stop_seconds
    )
    runtime_still_open = not (
        process_runtime.get("soft_stop_reached")
        or process_runtime.get("hard_limit_reached")
    )
    allowed = artifact_allowed and process_allowed and runtime_still_open
    reason = "admitted"
    if not artifact_allowed:
        reason = "estimated_hash_runtime_reaches_420_second_admission_boundary"
    elif not process_allowed:
        reason = "projected_same_process_runtime_reaches_540_second_soft_boundary"
    elif not runtime_still_open:
        reason = "same_process_runtime_boundary_already_reached"
    return {
        "allowed": allowed,
        "reason": reason,
        "compressed_size_bytes": compressed_size_bytes,
        "conservative_bytes_per_second": float(conservative_bytes_per_second),
        "estimated_hash_seconds": estimated,
        "artifact_admission_seconds": (
            SEED_SPOOL_RETIREMENT_RAW_ADMISSION_SECONDS
        ),
        "same_process_elapsed_before_open_seconds": float(elapsed),
        "projected_same_process_elapsed_seconds": projected_process_elapsed,
        "worker_soft_stop_seconds": float(limits.worker_soft_stop_seconds),
        "max_worker_runtime_seconds": float(limits.max_worker_runtime_seconds),
        "strictly_below_artifact_admission": artifact_allowed,
        "strictly_below_same_process_soft_stop": process_allowed,
    }


def _hash_stable_regular_file_with_runtime_gate(
    path: Path,
    *,
    clock: Any,
    process_started_at: float,
    limits: Any,
    phase: str,
) -> tuple[str, int, tuple[int, ...], Mapping[str, Any]]:
    """逐 1MiB 观测同进程 540/600 门禁的稳定文件哈希。"""

    bytes_read = 0
    _raise_if_seed_runtime_boundary(
        clock=clock,
        process_started_at=process_started_at,
        limits=limits,
        phase=phase,
        bytes_read=bytes_read,
    )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SeedWorkflowError(
            f"文件不存在、不可读或为符号链接：{path}"
        ) from error
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SeedWorkflowError(f"文件必须是非符号链接普通文件：{path}")
        while True:
            _raise_if_seed_runtime_boundary(
                clock=clock,
                process_started_at=process_started_at,
                limits=limits,
                phase=phase,
                bytes_read=bytes_read,
            )
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            bytes_read += len(block)
            _raise_if_seed_runtime_boundary(
                clock=clock,
                process_started_at=process_started_at,
                limits=limits,
                phase=phase,
                bytes_read=bytes_read,
            )
        after = os.fstat(descriptor)
        if any(
            getattr(before, name) != getattr(after, name)
            for name in _FILE_IDENTITY_FIELDS
        ):
            raise SeedWorkflowError(f"文件在哈希期间发生变化：{path}")
    finally:
        os.close(descriptor)
    final_runtime = _raise_if_seed_runtime_boundary(
        clock=clock,
        process_started_at=process_started_at,
        limits=limits,
        phase=phase,
        bytes_read=bytes_read,
    )
    return (
        digest.hexdigest(),
        bytes_read,
        tuple(int(getattr(after, name)) for name in _FILE_IDENTITY_FIELDS),
        final_runtime,
    )


def _run_seed_segment(
    args: argparse.Namespace,
    *,
    resume: bool,
    clock: Any = time.monotonic,
) -> tuple[Mapping[str, Any], int]:
    with _seed_execution_lock(args.prepared_directory):
        return _run_seed_segment_locked(args, resume=resume, clock=clock)


def _run_seed_segment_locked(
    args: argparse.Namespace,
    *,
    resume: bool,
    clock: Any = time.monotonic,
) -> tuple[Mapping[str, Any], int]:
    process_started_at = _process_clock_start(clock=clock)
    reuse_existing_seed_spool = bool(
        getattr(args, "reuse_existing_seed_spool", False)
    )
    if resume and reuse_existing_seed_spool:
        raise SeedWorkflowError(
            "--reuse-existing-seed-spool 只适用于 seed-start"
        )
    context = _seed_context(
        args,
        require_empty_checkpoint_directory=(
            not resume and not reuse_existing_seed_spool
        ),
        resume_checkpoint_path=(args.resume_checkpoint if resume else None),
        reconcile_abandoned_seed=True,
    )
    plan = _seed_public_plan(context)
    if not context["resource_gate"]["execution_allowed"]:
        return plan, 3
    limits = effective_resource_limits(context["profile"])
    before_worker = _process_runtime_observation(
        clock=clock,
        process_started_at=process_started_at,
        planned_seconds=args.planned_seed_checkpoint_seconds,
        limits=limits,
    )
    if _runtime_exit_code(before_worker) or before_worker[
        "planned_checkpoint_reached"
    ]:
        return {
            **plan,
            "ok": False,
            "segment_state": "worker_not_started",
            "worker_reason": "remaining_runtime_budget_exhausted",
            "process_runtime": before_worker,
        }, (_runtime_exit_code(before_worker) or 4)
    prior = context["prior_checkpoint_verification"]
    if prior is not None and prior["position"]["phase"] == "updates":
        after_noop = _process_runtime_observation(
            clock=clock,
            process_started_at=process_started_at,
            planned_seconds=args.planned_seed_checkpoint_seconds,
            limits=limits,
        )
        exit_code = _runtime_exit_code(after_noop)
        return {
            "ok": exit_code == 0,
            "schema_version": "rrc25-iran-full-seed-segment-result/v2",
            "segment_state": "already_complete_noop",
            "worker_status": "not_started",
            "worker_reason": "seed_already_complete",
            "checkpoint_path": str(context["resume_checkpoint"]),
            "checkpoint_verification": prior,
            "selection_id": context["selection"]["selection_id"],
            "code_identity_sha256": args.code_sha256,
            "database_connections": 0,
            "database_write_operations": 0,
            "opens_raw_mrt": False,
            "opens_update_mrt": False,
            "resource_observation": prior["resources"],
            "process_runtime": after_noop,
            "prior_new_raw_read_bytes": context["prior_new_raw_read_bytes"],
            "checkpoint_lifecycle": _seed_checkpoint_lifecycle_policy(),
            "seed_spool_reclamation_eligibility": (
                _seed_spool_reclamation_eligibility(prior)
            ),
        }, exit_code
    seed_artifact = context["selection"]["roles"]["state_seed_rib"]
    active_seed_reservation = context.get("seed_raw_reservation")
    if not resume and not reuse_existing_seed_spool:
        try:
            active_seed_reservation = reserve_seed_raw_attempt(
                args.prepared_directory,
                args.probe_ledger_terminal,
                raw_root=context["raw_root"],
                seed_artifact=seed_artifact,
            )
        except (ExecutionPrepError, OSError, ValueError) as error:
            raise SeedWorkflowError(
                "seed raw 打开前 durable reservation 发布失败"
            ) from error
        if (
            active_seed_reservation[
                "prior_cumulative_reserved_new_raw_bytes"
            ]
            != context["prior_new_raw_read_bytes"]
        ):
            close_seed_raw_attempt(
                args.prepared_directory,
                args.probe_ledger_terminal,
                raw_root=context["raw_root"],
                seed_artifact=seed_artifact,
                reservation=active_seed_reservation,
                checkpoint_ref=None,
                exact_seed_read=False,
                failure_type="ConcurrentSeedLedgerAdvance",
                failure_message="context 与 reservation 之间 cumulative 发生变化",
            )
            raise SeedWorkflowError("seed reservation 与 pre-open cumulative 不一致")

    if (
        not reuse_existing_seed_spool
        and not isinstance(active_seed_reservation, Mapping)
    ):
        raise SeedWorkflowError("seed worker 缺少 durable raw reservation")
    if reuse_existing_seed_spool and active_seed_reservation is not None:
        raise SeedWorkflowError(
            "显式复用 seed spool 时不得携带压缩 raw reservation"
        )

    def close_unknown_seed_reservation(
        *, failure_type: str, failure_message: str
    ) -> Mapping[str, Any] | None:
        if resume or reuse_existing_seed_spool:
            return None
        return close_seed_raw_attempt(
            args.prepared_directory,
            args.probe_ledger_terminal,
            raw_root=context["raw_root"],
            seed_artifact=seed_artifact,
            reservation=active_seed_reservation,
            checkpoint_ref=None,
            exact_seed_read=False,
            failure_type=failure_type,
            failure_message=failure_message,
        )

    try:
        worker = run_bounded_pilot_worker(
            context["selection"],
            artifact_root=context["raw_root"],
            country_mapping=context["compatible_mapping"],
            raw_retention_mapping=context["raw_retention_mapping"],
            seed_spool_attestation=context["seed_spool_attestation"],
            seed_rib_prefilter=context.get("seed_rib_prefilter"),
            pilot_end_exclusive_utc=args.pilot_end_exclusive,
            update_record_stream_factory=_reject_update_stream,
            checkpoint_directory=context["checkpoint_directory"],
            resume_checkpoint_path=context["resume_checkpoint"],
            code_identity_sha256=args.code_sha256,
            planned_seed_checkpoint_seconds=args.planned_seed_checkpoint_seconds,
            prior_new_raw_read_bytes=context["prior_new_raw_read_bytes"],
            prior_raw_accounting=context["prior_raw_accounting"],
            seed_raw_reservation=active_seed_reservation,
            reuse_existing_seed_spool=reuse_existing_seed_spool,
            seed_batch_max_route_events=getattr(
                args,
                "seed_batch_max_route_events",
                _DEFAULT_SEED_BATCH_MAX_ROUTE_EVENTS,
            ),
            seed_batch_max_records=getattr(
                args,
                "seed_batch_max_records",
                _DEFAULT_SEED_BATCH_MAX_RECORDS,
            ),
            stop_after_seed=True,
            resource_limits=limits,
            clock=clock,
            process_started_at=process_started_at,
        )
    except BaseException as error:
        try:
            close_unknown_seed_reservation(
                failure_type=type(error).__name__,
                failure_message=str(error),
            )
        except (ExecutionPrepError, OSError, ValueError) as close_error:
            raise SeedWorkflowError(
                "seed worker 失败且 durable reservation outcome 无法闭合"
            ) from close_error
        raise
    controlled_reasons = {"planned_seed_checkpoint", "stop_after_seed"}
    if worker.incomplete_reason not in controlled_reasons:
        seed_ledger_outcome = close_unknown_seed_reservation(
            failure_type="SeedWorkerIncomplete",
            failure_message=str(worker.incomplete_reason),
        )
        after_failure = _process_runtime_observation(
            clock=clock,
            process_started_at=process_started_at,
            planned_seconds=args.planned_seed_checkpoint_seconds,
            limits=limits,
        )
        return {
            "ok": False,
            "schema_version": "rrc25-iran-full-seed-segment-result/v2",
            "segment_state": "worker_failed",
            "worker_status": worker.status,
            "worker_reason": worker.incomplete_reason,
            "checkpoint_path": worker.checkpoint_path,
            "checkpoint_verification": None,
            "errors": [dict(row) for row in worker.errors],
            "selection_id": context["selection"]["selection_id"],
            "code_identity_sha256": args.code_sha256,
            "database_connections": 0,
            "database_write_operations": 0,
            "opens_raw_mrt": (
                not resume and not reuse_existing_seed_spool
            ),
            "opens_update_mrt": False,
            "resource_observation": dict(worker.resources),
            "process_runtime": after_failure,
            "prior_new_raw_read_bytes": context["prior_new_raw_read_bytes"],
            "seed_raw_reservation": (
                dict(active_seed_reservation)
                if active_seed_reservation is not None
                else None
            ),
            "seed_raw_ledger_outcome": seed_ledger_outcome,
        }, (_runtime_exit_code(after_failure) or 4)
    if worker.checkpoint_path is None:
        close_unknown_seed_reservation(
            failure_type="SeedCheckpointMissing",
            failure_message="seed worker 未发布可恢复 checkpoint",
        )
        raise SeedWorkflowError("seed worker 未发布可恢复 checkpoint")
    after_worker = _process_runtime_observation(
        clock=clock,
        process_started_at=process_started_at,
        planned_seconds=args.planned_seed_checkpoint_seconds,
        limits=limits,
    )
    worker_runtime_exit = _runtime_exit_code(after_worker)
    if worker_runtime_exit:
        seed_ledger_outcome = close_unknown_seed_reservation(
            failure_type="PostWorkerRuntimeBoundary",
            failure_message="checkpoint 发布后未在时间门内完成核验",
        )
        return {
            "ok": False,
            "schema_version": "rrc25-iran-full-seed-segment-result/v2",
            "segment_state": "checkpoint_published_verification_skipped",
            "worker_status": worker.status,
            "worker_reason": (
                "post_worker_runtime_budget_exhausted_before_verification"
            ),
            "checkpoint_path": worker.checkpoint_path,
            "checkpoint_verification": None,
            "selection_id": context["selection"]["selection_id"],
            "code_identity_sha256": args.code_sha256,
            "database_connections": 0,
            "database_write_operations": 0,
            "opens_raw_mrt": (
                not resume and not reuse_existing_seed_spool
            ),
            "opens_update_mrt": False,
            "resource_observation": dict(worker.resources),
            "process_runtime": after_worker,
            "prior_new_raw_read_bytes": context["prior_new_raw_read_bytes"],
            "seed_raw_reservation": (
                dict(active_seed_reservation)
                if active_seed_reservation is not None
                else None
            ),
            "seed_raw_ledger_outcome": seed_ledger_outcome,
        }, worker_runtime_exit
    if resume and context.get("resume_checkpoint_verification_deferred"):
        # 旧 checkpoint 已由 worker 在恢复状态前完整核验；新 checkpoint 是本
        # 进程刚刚原子发布的结果。不要在同一 600 秒进程内再次解压数 GB JSON，
        # 将发布后只读核验显式交给 seed-verify（或下一次 seed-resume 的 worker）。
        after_publish = _process_runtime_observation(
            clock=clock,
            process_started_at=process_started_at,
            planned_seconds=args.planned_seed_checkpoint_seconds,
            limits=limits,
        )
        runtime_exit = _runtime_exit_code(after_publish)
        controlled = worker.incomplete_reason in controlled_reasons
        result = {
            "ok": controlled and runtime_exit == 0,
            "schema_version": "rrc25-iran-full-seed-segment-result/v2",
            "segment_state": "checkpoint_published_verification_deferred",
            "worker_status": worker.status,
            "worker_reason": worker.incomplete_reason,
            "checkpoint_path": worker.checkpoint_path,
            "checkpoint_verification": {
                "verified": False,
                "verification_state": "deferred_to_explicit_seed_verify",
                "required_command": "seed-verify",
                "resume_input_was_strictly_verified_by_worker": True,
            },
            "selection_id": context["selection"]["selection_id"],
            "code_identity_sha256": args.code_sha256,
            "database_connections": 0,
            "database_write_operations": 0,
            "opens_raw_mrt": False,
            "opens_update_mrt": False,
            "resource_observation": dict(worker.resources),
            "meaningful_progress": None,
            "meaningful_progress_state": "pending_checkpoint_verification",
            "process_runtime": after_publish,
            "prior_new_raw_read_bytes": context["prior_new_raw_read_bytes"],
            "seed_raw_reservation": (
                dict(active_seed_reservation)
                if active_seed_reservation is not None
                else None
            ),
            "seed_raw_ledger_outcome": None,
            "checkpoint_lifecycle": _seed_checkpoint_lifecycle_policy(),
            "seed_spool_reclamation_eligibility": {
                "eligible": False,
                "state": "unknown_until_seed_verify",
                "requires_explicit_archive_or_reclamation_command": True,
                "explicit_command": "seed-retire-spool",
                "automatic_deletion": False,
                "current_command_handles_spool": False,
            },
        }
        return result, (runtime_exit or (0 if controlled else 4))
    try:
        verified = verify_full_seed_checkpoint(
            worker.checkpoint_path,
            selection=context["selection"],
            country_mapping=context["compatible_mapping"],
            raw_retention_mapping=context["raw_retention_mapping"],
            seed_spool_attestation=context["seed_spool_attestation"],
            pilot_end_exclusive_utc=args.pilot_end_exclusive,
            code_identity_sha256=args.code_sha256,
        )
    except BaseException as error:
        try:
            close_unknown_seed_reservation(
                failure_type=type(error).__name__,
                failure_message="checkpoint 发布后完整核验失败：" + str(error),
            )
        except (ExecutionPrepError, OSError, ValueError) as close_error:
            raise SeedWorkflowError(
                "checkpoint 核验失败且 durable reservation outcome 无法闭合"
            ) from close_error
        raise
    seed_ledger_outcome = None
    if not resume and not reuse_existing_seed_spool:
        seed_ledger_outcome = close_seed_raw_attempt(
            args.prepared_directory,
            args.probe_ledger_terminal,
            raw_root=context["raw_root"],
            seed_artifact=seed_artifact,
            reservation=active_seed_reservation,
            checkpoint_ref={
                "path": str(worker.checkpoint_path),
                "checkpoint_sequence": verified["checkpoint_sequence"],
                "checkpoint_fingerprint_sha256": verified[
                    "checkpoint_fingerprint_sha256"
                ],
            },
            exact_seed_read=True,
        )
    phase = verified["position"]["phase"]
    prior_progress = (
        -1 if prior is None else int(prior["seed_progress"]["next_record_ordinal"])
    )
    current_progress = int(verified["seed_progress"]["next_record_ordinal"])
    made_progress = current_progress > prior_progress
    after_verify = _process_runtime_observation(
        clock=clock,
        process_started_at=process_started_at,
        planned_seconds=args.planned_seed_checkpoint_seconds,
        limits=limits,
    )
    runtime_exit = _runtime_exit_code(after_verify)
    controlled = worker.incomplete_reason in controlled_reasons and made_progress
    result = {
        "ok": controlled,
        "schema_version": "rrc25-iran-full-seed-segment-result/v2",
        "segment_state": (
            "seed_complete" if phase == "updates" else "paused_at_record_boundary"
        ),
        "worker_status": worker.status,
        "worker_reason": worker.incomplete_reason,
        "checkpoint_path": worker.checkpoint_path,
        "checkpoint_verification": verified,
        "selection_id": context["selection"]["selection_id"],
        "code_identity_sha256": args.code_sha256,
        "database_connections": 0,
        "database_write_operations": 0,
        "opens_raw_mrt": (
            not resume and not reuse_existing_seed_spool
        ),
        "opens_update_mrt": False,
        "resource_observation": dict(worker.resources),
        "meaningful_progress": made_progress,
        "process_runtime": after_verify,
        "prior_new_raw_read_bytes": context["prior_new_raw_read_bytes"],
        "seed_raw_reservation": (
            dict(active_seed_reservation)
            if active_seed_reservation is not None
            else None
        ),
        "seed_raw_ledger_outcome": seed_ledger_outcome,
        "checkpoint_lifecycle": _seed_checkpoint_lifecycle_policy(),
        "seed_spool_reclamation_eligibility": (
            _seed_spool_reclamation_eligibility(verified)
        ),
    }
    if not made_progress:
        result["worker_reason"] = "zero_progress_checkpoint_rejected"
    if runtime_exit:
        result["ok"] = False
    return result, (runtime_exit or (0 if controlled else 4))


def _add_plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", required=True, help="冻结研究 Profile JSON")
    parser.add_argument("--manifest", required=True, help="父 MRT manifest JSON")
    parser.add_argument(
        "--manifest-verification",
        required=True,
        help="父 manifest 只读验证结果 JSON",
    )
    parser.add_argument("--mapping", required=True, help="冻结 AS 国家映射 JSON")
    parser.add_argument("--code-sha256", required=True, help="处理代码 SHA-256")
    parser.add_argument("--output-root", required=True, help="文件型研究输出根目录")
    parser.add_argument(
        "--pilot-end-exclusive",
        help=(
            "可选 bounded pilot UTC 结束边界；不会改写冻结 Profile，"
            "未处理余下区间固定为 blocking incomplete"
        ),
    )
    parser.add_argument(
        "--maximum-artifacts-per-chunk",
        type=int,
        default=DEFAULT_MAX_ARTIFACTS_PER_CHUNK,
        help="每个有界分块最多输入制品数，默认 5",
    )
    parser.add_argument(
        "--estimated-worker-seconds",
        required=True,
        type=float,
        help="dry-run 的单分块 worker 秒数估算；达到 540 秒即不放行",
    )
    parser.add_argument(
        "--estimated-temporary-bytes",
        required=True,
        type=int,
        help="dry-run 的峰值临时字节估算；达到十进制 5GB 即不放行",
    )


def _add_seed_binding_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", required=True, help="冻结研究 Profile JSON")
    parser.add_argument("--manifest", required=True, help="父 MRT manifest JSON")
    parser.add_argument(
        "--manifest-verification",
        required=True,
        help="父 manifest 只读验证结果 JSON",
    )
    parser.add_argument("--mapping", required=True, help="冻结 compatible 映射")
    parser.add_argument(
        "--revised-mapping",
        required=True,
        help="冻结 revised delta 映射",
    )
    parser.add_argument(
        "--seed-spool-attestation",
        required=True,
        help="冻结的 seed 单-pass 解压 SHA/size attestation",
    )
    parser.add_argument(
        "--code-identity", required=True, help="当前研究代码身份 JSON"
    )
    parser.add_argument("--code-sha256", required=True, help="处理代码 SHA-256")
    parser.add_argument(
        "--pilot-end-exclusive",
        required=True,
        help=(
            "seed 与后续 UPDATE journal 共用的半开 selection UTC 结束边界；"
            "兼容参数名，可等于冻结 Profile 完整终点"
        ),
    )


def _add_seed_execution_arguments(parser: argparse.ArgumentParser) -> None:
    _add_seed_binding_arguments(parser)
    parser.add_argument(
        "--prepared-directory",
        required=True,
        help=(
            "execution-prep 的 create-only prepared 目录；其中 probe ledger "
            "必须与本次 seed 的四项冻结身份一致"
        ),
    )
    parser.add_argument(
        "--probe-ledger-terminal",
        required=True,
        help=(
            "prepared/probe-ledger 的唯一 terminal GENESIS 或 OUTCOME；"
            "seed 累计字节只从该 receipt 的 ref/SHA 全链重算，不接受裸整数"
        ),
    )
    parser.add_argument(
        "--raw-root",
        required=True,
        help="只读 MRT 根目录；本阶段只打开 selection 的 state_seed_rib",
    )
    parser.add_argument(
        "--checkpoint-directory",
        required=True,
        help="独立研究 checkpoint 目录",
    )
    parser.add_argument(
        "--planned-seed-checkpoint-seconds",
        type=float,
        default=DEFAULT_SEED_CHECKPOINT_SECONDS,
        help="主动保存 seed checkpoint 的秒数，默认且最大 420",
    )
    parser.add_argument(
        "--seed-rib-prefilter",
        help=(
            "可选：与完整解压 spool、seed artifact 和 raw-retention mapping "
            "绑定的并行 native prefilter sidecar"
        ),
    )
    parser.add_argument(
        "--seed-batch-max-route-events",
        type=int,
        default=_DEFAULT_SEED_BATCH_MAX_ROUTE_EVENTS,
        help=(
            "seed 状态归并前最多暂存的 RouteEvent；默认 1048576，"
            "用于避免反复重建完整状态"
        ),
    )
    parser.add_argument(
        "--seed-batch-max-records",
        type=int,
        default=_DEFAULT_SEED_BATCH_MAX_RECORDS,
        help=(
            "seed 状态归并前最多暂存的含目标事件 physical record；"
            "默认 65536"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="伊朗 RRC25 国家中断研究的有界文件型协调器"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    dry_run = subparsers.add_parser("dry-run", help="只做元数据解析和资源估算")
    _add_plan_arguments(dry_run)
    dry_run.add_argument(
        "--worker-plan-only",
        action="store_true",
        help="只向 stdout 输出可直接交给只读 worker 的内容寻址计划",
    )
    execute = subparsers.add_parser("execute", help="以注入夹具执行新研究 run")
    _add_plan_arguments(execute)
    execute.add_argument(
        "--fixture-executor",
        required=True,
        help="注入式 record executor JSON；当前不接受真实 MRT 路径",
    )
    resume = subparsers.add_parser("resume", help="从完整 record 检查点恢复")
    _add_plan_arguments(resume)
    resume.add_argument("--fixture-executor", required=True)
    verify = subparsers.add_parser("verify", help="只读校验研究 run")
    _add_plan_arguments(verify)
    seed_dry_run = subparsers.add_parser(
        "seed-dry-run",
        help="只读核验完整 seed 的输入、双口径映射、身份和资源边界",
    )
    _add_seed_execution_arguments(seed_dry_run)
    seed_dry_run.add_argument(
        "--resume-checkpoint",
        help="可选：把既有完整 seed checkpoint 累计资源纳入估算",
    )
    seed_start = subparsers.add_parser(
        "seed-start", help="从 record ordinal 0 执行一个有界 seed 分段"
    )
    _add_seed_execution_arguments(seed_start)
    seed_start.add_argument(
        "--reuse-existing-seed-spool",
        action="store_true",
        help=(
            "显式复用 checkpoint-directory 中与 attestation SHA/size "
            "一致的完整解压 spool；不打开压缩 raw，seed-resume 不适用"
        ),
    )
    seed_resume = subparsers.add_parser(
        "seed-resume", help="从已验证 record-boundary checkpoint 续跑 seed"
    )
    _add_seed_execution_arguments(seed_resume)
    seed_resume.add_argument("--resume-checkpoint", required=True)
    seed_resume.add_argument(
        "--defer-checkpoint-verification",
        action="store_true",
        help=(
            "快速恢复：由 worker 在恢复前仅完整验证输入 checkpoint 一次，"
            "新发布 checkpoint 标记为待 seed-verify，避免同进程重复解压验证"
        ),
    )
    seed_reconcile = subparsers.add_parser(
        "seed-reconcile-workspace",
        help=(
            "闭合 killed seed reservation，保留有效 checkpoint，并把不可恢复"
            "的 partial spool/checkpoint 原子移入同盘隔离目录"
        ),
    )
    _add_seed_execution_arguments(seed_reconcile)
    seed_reconcile.add_argument(
        "--quarantine-directory",
        required=True,
        help="必须不存在、且与 checkpoint-directory 同父目录的隔离目录",
    )
    seed_verify = subparsers.add_parser(
        "seed-verify", help="只读验证完整 seed checkpoint，不打开 MRT"
    )
    _add_seed_binding_arguments(seed_verify)
    seed_verify.add_argument("--checkpoint", required=True)
    seed_archive = subparsers.add_parser(
        "seed-archive-checkpoint",
        help="显式归档已被更新完整 checkpoint 取代的 active 旧 checkpoint",
    )
    _add_seed_binding_arguments(seed_archive)
    seed_archive.add_argument("--checkpoint-directory", required=True)
    seed_archive.add_argument("--old-checkpoint", required=True)
    seed_archive.add_argument("--successor-checkpoint", required=True)
    seed_archive.add_argument("--history-directory", required=True)
    seed_archive.add_argument("--dry-run", action="store_true")
    seed_retire_spool = subparsers.add_parser(
        "seed-retire-spool",
        help="seed 完成后显式写收据并退役可由压缩原件重建的 spool",
    )
    _add_seed_binding_arguments(seed_retire_spool)
    seed_retire_spool.add_argument("--checkpoint-directory", required=True)
    seed_retire_spool.add_argument("--checkpoint", required=True)
    seed_retire_spool.add_argument("--raw-root", required=True)
    seed_retire_spool.add_argument("--retirement-directory", required=True)
    seed_retire_spool.add_argument("--dry-run", action="store_true")
    return parser


def _load_seed_verification_context(
    args: argparse.Namespace,
) -> Mapping[str, Any]:
    """加载 seed checkpoint 只读验证所需的同一组冻结身份。"""

    profile = validate_research_profile(load_json_metadata(args.profile))
    manifest = load_json_metadata(args.manifest, maximum_bytes=512 * 1024 * 1024)
    verification = load_json_metadata(args.manifest_verification)
    compatible_snapshot = load_json_metadata(
        args.mapping, maximum_bytes=64 * 1024 * 1024
    )
    revised_snapshot = load_json_metadata(
        args.revised_mapping, maximum_bytes=16 * 1024 * 1024
    )
    code_identity = _load_bound_code_identity(
        args.code_identity, args.code_sha256
    )
    selection = resolve_research_inputs(
        manifest,
        verification,
        _seed_resolver_profile(profile, args.pilot_end_exclusive),
    )
    if selection.get("status") != "complete":
        raise SeedWorkflowError("seed/B1 selection 不完整，拒绝验证到错误输入身份")
    seed = selection.get("roles", {}).get("state_seed_rib")
    if not isinstance(seed, Mapping):
        raise SeedWorkflowError("selection 缺少 state_seed_rib")
    seed_spool_attestation = validate_seed_spool_attestation(
        load_json_metadata(args.seed_spool_attestation, maximum_bytes=1024 * 1024),
        seed_artifact=seed,
    )
    compatible = mapping_view_from_frozen_snapshot(compatible_snapshot)
    revised = mapping_view_from_revised_snapshot(
        revised_snapshot, compatible_snapshot
    )
    raw_retention = build_raw_retention_mapping_union((compatible, revised))
    return {
        "profile": profile,
        "selection": selection,
        "compatible_mapping": compatible,
        "raw_retention_mapping": raw_retention,
        "seed_spool_attestation": seed_spool_attestation,
        "code_identity": code_identity,
    }


def _verify_seed_checkpoint_with_context(
    checkpoint: str | Path,
    *,
    args: argparse.Namespace,
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    return verify_full_seed_checkpoint(
        checkpoint,
        selection=context["selection"],
        country_mapping=context["compatible_mapping"],
        raw_retention_mapping=context["raw_retention_mapping"],
        seed_spool_attestation=context["seed_spool_attestation"],
        pilot_end_exclusive_utc=args.pilot_end_exclusive,
        code_identity_sha256=args.code_sha256,
    )


def _verify_seed_only(
    args: argparse.Namespace,
    *,
    clock: Any = time.monotonic,
) -> Mapping[str, Any]:
    process_started_at = clock()
    if (
        isinstance(process_started_at, bool)
        or not isinstance(process_started_at, (int, float))
        or not math.isfinite(float(process_started_at))
    ):
        raise SeedWorkflowError("clock 必须返回有限数")
    process_started_at = float(process_started_at)
    context = _load_seed_verification_context(args)
    checkpoint_verification = verify_full_seed_checkpoint(
        args.checkpoint,
        selection=context["selection"],
        country_mapping=context["compatible_mapping"],
        raw_retention_mapping=context["raw_retention_mapping"],
        seed_spool_attestation=context["seed_spool_attestation"],
        pilot_end_exclusive_utc=args.pilot_end_exclusive,
        code_identity_sha256=args.code_sha256,
    )
    runtime = _process_runtime_observation(
        clock=clock,
        process_started_at=process_started_at,
        planned_seconds=DEFAULT_SEED_CHECKPOINT_SECONDS,
        limits=effective_resource_limits(context["profile"]),
    )
    runtime_exit = _runtime_exit_code(runtime)
    return {
        "ok": runtime_exit == 0,
        "schema_version": "rrc25-iran-full-seed-readonly-verification/v1",
        "opens_raw_mrt": False,
        "database_connections": 0,
        "database_write_operations": 0,
        "code_identity_sha256": context["code_identity"]["identity_sha256"],
        "selection_id": context["selection"]["selection_id"],
        "raw_retention_semantics": context["raw_retention_mapping"].semantics,
        "seed_spool_attestation_fingerprint_sha256": (
            context["seed_spool_attestation"]["semantic_fingerprint_sha256"]
        ),
        "checkpoint_verification": checkpoint_verification,
        "checkpoint_lifecycle": _seed_checkpoint_lifecycle_policy(),
        "seed_spool_reclamation_eligibility": checkpoint_verification[
            "seed_spool_reclamation_eligibility"
        ],
        "process_runtime": runtime,
        "runtime_exit_code": runtime_exit,
    }


def _run_seed_workspace_reconciliation(
    args: argparse.Namespace,
) -> Mapping[str, Any]:
    """闭合 killed attempt，并将不可恢复现场移入同盘隔离目录。

    有效 full-seed checkpoint 与其发布 spool 始终留在 active root，调用方
    应转用 ``seed-resume``。没有有效 checkpoint 时，partial spool、已发布但
    无 checkpoint 的 spool、atomic checkpoint 临时文件及 diagnostic 会被
    原子 rename 到同父目录；原始 MRT 不会在本命令中打开。
    """

    with _seed_execution_lock(args.prepared_directory):
        context = _seed_context(
            args,
            require_empty_checkpoint_directory=False,
            resume_checkpoint_path=None,
            reconcile_abandoned_seed=True,
        )
        active = Path(context["checkpoint_directory"]).resolve(strict=True)
        _assert_seed_mutation_path_allowed(active, "checkpoint_directory")
        if context.get("resource_gate", {}).get("execution_allowed") is not True:
            raise SeedWorkflowError(
                "seed workspace reconcile 写入门未通过，拒绝 rename/quarantine"
            )
        selection_id = context["selection"]["selection_id"]
        attestation = context["seed_spool_attestation"]
        expected_spool_name = (
            "seed-spool."
            f"{attestation['semantic_fingerprint_sha256'][:16]}."
            f"{attestation['decompressed']['sha256'][:16]}.mrt"
        )
        entries = tuple(sorted(active.iterdir(), key=lambda item: item.name))
        valid_checkpoints: list[tuple[Path, Mapping[str, Any]]] = []
        classified: dict[Path, str] = {}
        invalid_checkpoint_errors: dict[str, str] = {}
        unknown: list[str] = []
        full_checkpoint_prefix = f"{selection_id}.worker."
        temporary_prefix = f".{selection_id}.worker."

        for entry in entries:
            metadata = entry.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise SeedWorkflowError(
                    "seed workspace 只允许非符号链接普通文件，拒绝自动隔离"
                )
            name = entry.name
            if (
                name.startswith(full_checkpoint_prefix)
                and ".full-seed." in name
                and name.endswith(".json.gz")
            ):
                try:
                    verified = verify_full_seed_checkpoint(
                        entry,
                        selection=context["selection"],
                        country_mapping=context["compatible_mapping"],
                        raw_retention_mapping=context["raw_retention_mapping"],
                        seed_spool_attestation=attestation,
                        pilot_end_exclusive_utc=args.pilot_end_exclusive,
                        code_identity_sha256=args.code_sha256,
                    )
                except (BoundedPilotWorkerError, OSError, ValueError) as error:
                    classified[entry] = "invalid_full_seed_checkpoint"
                    invalid_checkpoint_errors[name] = (
                        f"{type(error).__name__}: {error}"
                    )
                else:
                    classified[entry] = "valid_full_seed_checkpoint"
                    valid_checkpoints.append((entry, verified))
                continue
            if name == expected_spool_name:
                classified[entry] = "published_seed_spool"
                continue
            if name.startswith(f".{expected_spool_name}.tmp-"):
                classified[entry] = "partial_seed_spool"
                continue
            if (
                name.startswith(temporary_prefix)
                and ".tmp-" in name
                and (".full-seed." in name or ".diagnostic." in name)
            ):
                classified[entry] = "partial_checkpoint_publish"
                continue
            if (
                name.startswith(full_checkpoint_prefix)
                and ".diagnostic." in name
                and name.endswith(".json")
            ):
                classified[entry] = "diagnostic_checkpoint"
                continue
            unknown.append(name)

        if unknown:
            raise SeedWorkflowError(
                "seed workspace 含未分类文件，拒绝自动移动："
                + ",".join(sorted(unknown))
            )
        by_sequence: dict[int, str] = {}
        for path, verification in valid_checkpoints:
            sequence = int(verification["checkpoint_sequence"])
            fingerprint = str(
                verification["checkpoint_fingerprint_sha256"]
            )
            prior_fingerprint = by_sequence.setdefault(sequence, fingerprint)
            if prior_fingerprint != fingerprint:
                raise SeedWorkflowError(
                    "seed workspace 同一 checkpoint sequence 出现分叉"
                )

        protected_spools = {
            str(verification["seed_spool"]["file_name"])
            for _path, verification in valid_checkpoints
        }
        to_isolate = []
        for path, kind in classified.items():
            if kind == "valid_full_seed_checkpoint":
                continue
            if kind == "published_seed_spool" and path.name in protected_spools:
                continue
            to_isolate.append((path, kind))
        to_isolate.sort(key=lambda item: item[0].name)

        latest = (
            max(
                valid_checkpoints,
                key=lambda item: (
                    int(item[1]["checkpoint_sequence"]), item[0].name
                ),
            )
            if valid_checkpoints
            else None
        )
        recommended_action = "seed-resume" if latest is not None else "seed-start"
        base_result = {
            "ok": True,
            "schema_version": "rrc25-seed-workspace-reconciliation-result/v1",
            "opens_raw_mrt": False,
            "database_connections": 0,
            "database_write_operations": 0,
            "selection_id": selection_id,
            "checkpoint_directory": str(active),
            "ledger_reconciliation": context["seed_reconciliation"],
            "cumulative_reserved_new_raw_bytes": context["seed_raw_ledger"][
                "current_cumulative_reserved_new_raw_bytes"
            ],
            "reservation_refund_policy": (
                "never_refund_even_on_failure_timeout_or_retry"
            ),
            "recommended_action": recommended_action,
            "resume_checkpoint": str(latest[0]) if latest is not None else None,
            "valid_checkpoint_count": len(valid_checkpoints),
            "invalid_checkpoint_errors": invalid_checkpoint_errors,
        }
        if not to_isolate:
            return {
                **base_result,
                "workspace_state": (
                    "resume_from_verified_checkpoint"
                    if latest is not None
                    else "clean_start_ready"
                ),
                "quarantine_performed": False,
                "isolated_entries": [],
            }

        quarantine = Path(args.quarantine_directory).expanduser()
        if quarantine.exists() or quarantine.is_symlink():
            raise SeedWorkflowError("quarantine_directory 必须不存在，拒绝覆盖")
        quarantine_parent = quarantine.parent.resolve(strict=True)
        if quarantine_parent != active.parent:
            raise SeedWorkflowError(
                "quarantine_directory 必须与 checkpoint_directory 同父目录"
            )
        if quarantine.name in {"", ".", ".."}:
            raise SeedWorkflowError("quarantine_directory 名称非法")
        _assert_seed_mutation_path_allowed(
            quarantine_parent / quarantine.name,
            "quarantine_directory",
        )
        quarantine.mkdir(mode=0o750, exist_ok=False)
        _fsync_directory(quarantine_parent)
        isolated_rows = []
        try:
            for source, kind in to_isolate:
                digest, size, identity = _hash_stable_regular_file_with_identity(
                    source
                )
                destination = quarantine / source.name
                os.rename(source, destination)
                after = destination.lstat()
                after_identity = tuple(
                    int(getattr(after, field)) for field in _FILE_IDENTITY_FIELDS
                )
                # rename 会合法更新 ctime；dev/inode/size/mtime 必须保持不变。
                if identity[:4] != after_identity[:4]:
                    raise SeedWorkflowError("隔离文件 rename 后稳定身份漂移")
                isolated_rows.append(
                    {
                        "file_name": source.name,
                        "classification": kind,
                        "sha256": digest,
                        "size_bytes": size,
                        "stable_file_identity": {
                            field: identity[index]
                            for index, field in enumerate(_FILE_IDENTITY_FIELDS)
                        },
                    }
                )
            _fsync_directory(active)
            _fsync_directory(quarantine)
            receipt = _seed_workspace_isolation_receipt_payload(
                {
                    "operation": "seed_workspace_orphan_isolation",
                    "selection_id": selection_id,
                    "checkpoint_directory": str(active),
                    "quarantine_directory": str(quarantine.resolve(strict=True)),
                    "isolated_entries": isolated_rows,
                    "retained_valid_checkpoints": [
                        {
                            "path": str(path),
                            "checkpoint_sequence": verification[
                                "checkpoint_sequence"
                            ],
                            "checkpoint_fingerprint_sha256": verification[
                                "checkpoint_fingerprint_sha256"
                            ],
                        }
                        for path, verification in valid_checkpoints
                    ],
                    "recommended_action": recommended_action,
                    "resume_checkpoint": (
                        str(latest[0]) if latest is not None else None
                    ),
                    "raw_accounting": {
                        "cumulative_reserved_new_raw_bytes": base_result[
                            "cumulative_reserved_new_raw_bytes"
                        ],
                        "reservation_refund_policy": base_result[
                            "reservation_refund_policy"
                        ],
                    },
                    "raw_mrt_files_opened": 0,
                    "database_write_operations": 0,
                }
            )
            published = write_canonical_json(
                quarantine / "ISOLATION.json",
                receipt,
                kind="rrc25_seed_workspace_isolation",
                mode=0o440,
            )
            _fsync_directory(quarantine)
        except BaseException as error:
            raise SeedWorkflowError(
                "seed workspace 隔离中断；已移动文件仍保留在 "
                f"{quarantine}，不得删除，需再次人工/命令核对"
            ) from error
        return {
            **base_result,
            "workspace_state": (
                "resume_from_verified_checkpoint"
                if latest is not None
                else "orphan_isolated_clean_start_ready"
            ),
            "quarantine_performed": True,
            "quarantine_directory": str(quarantine.resolve(strict=True)),
            "isolation_receipt": {
                "path": str(published.path),
                "sha256": published.sha256,
                "size_bytes": published.size_bytes,
                "receipt_fingerprint_sha256": receipt[
                    "receipt_fingerprint_sha256"
                ],
            },
            "isolated_entries": isolated_rows,
        }


def _run_seed_checkpoint_archive_locked(
    args: argparse.Namespace,
) -> Mapping[str, Any]:
    """显式归档已被更新 checkpoint 取代的旧 checkpoint。"""

    active = _checked_directory(args.checkpoint_directory, "checkpoint_directory")
    _assert_seed_mutation_path_allowed(active, "checkpoint_directory")
    old_path = _checked_resume_checkpoint(args.old_checkpoint, active)
    successor_path = _checked_resume_checkpoint(args.successor_checkpoint, active)
    if old_path == successor_path:
        raise SeedWorkflowError("旧 checkpoint 与 successor checkpoint 不得相同")
    history = _checked_independent_history_directory(
        args.history_directory,
        active_directory=active,
        field="history_directory",
    )
    _assert_seed_mutation_path_allowed(history, "history_directory")
    archive_path = history / old_path.name
    receipt_path = history / f"{old_path.name}.archive-receipt.json"
    if (
        archive_path.exists()
        or archive_path.is_symlink()
        or receipt_path.exists()
        or receipt_path.is_symlink()
    ):
        raise FileExistsError("归档副本或收据目标已存在，拒绝覆盖")

    context = _load_seed_verification_context(args)
    old = _verify_seed_checkpoint_with_context(old_path, args=args, context=context)
    successor = _verify_seed_checkpoint_with_context(
        successor_path, args=args, context=context
    )
    if old["bindings"] != successor["bindings"]:
        raise SeedWorkflowError("新旧 checkpoint 冻结身份不一致")
    if (
        int(successor["checkpoint_sequence"])
        <= int(old["checkpoint_sequence"])
        or _checkpoint_progress_key(successor) <= _checkpoint_progress_key(old)
        or successor["checkpoint_fingerprint_sha256"]
        == old["checkpoint_fingerprint_sha256"]
    ):
        raise SeedWorkflowError("旧 checkpoint 尚未被更高序列且有进展的完整 checkpoint 取代")

    old_sha, old_size = _hash_stable_regular_file(old_path)
    successor_sha, successor_size = _hash_stable_regular_file(successor_path)
    result = {
        "ok": True,
        "schema_version": "rrc25-seed-checkpoint-archive-result/v1",
        "dry_run": bool(args.dry_run),
        "active_removed": False,
        "successor_retained": True,
        "opens_raw_mrt": False,
        "database_connections": 0,
        "database_write_operations": 0,
        "checkpoint_lifecycle": _seed_checkpoint_lifecycle_policy(),
        "old_checkpoint": {
            "active_path": str(old_path),
            "checkpoint_sequence": old["checkpoint_sequence"],
            "checkpoint_fingerprint_sha256": old[
                "checkpoint_fingerprint_sha256"
            ],
            "sha256": old_sha,
            "size_bytes": old_size,
        },
        "successor_checkpoint": {
            "active_path": str(successor_path),
            "checkpoint_sequence": successor["checkpoint_sequence"],
            "checkpoint_fingerprint_sha256": successor[
                "checkpoint_fingerprint_sha256"
            ],
            "sha256": successor_sha,
            "size_bytes": successor_size,
        },
        "history_path": str(archive_path),
        "receipt_path": str(receipt_path),
        "would_release_active_temporary_bytes": old_size,
        "released_active_temporary_bytes": 0,
        "spool_handled": False,
    }
    if args.dry_run:
        return result

    copied_sha, copied_size = _copy_regular_create_only(old_path, archive_path)
    if copied_sha != old_sha or copied_size != old_size:
        raise SeedWorkflowError("归档副本与旧 checkpoint 身份不一致")
    archived_sha, archived_size = _hash_stable_regular_file(archive_path)
    if (archived_sha, archived_size) != (old_sha, old_size):
        raise SeedWorkflowError("已发布 checkpoint 归档目标 SHA/size 复核失败")
    receipt = _archive_receipt_payload(
        {
            "operation": "seed_checkpoint_archive",
            "archived_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "active_root": str(active),
            "history_root": str(history),
            "old_checkpoint": result["old_checkpoint"],
            "successor_checkpoint": result["successor_checkpoint"],
            "history_copy": {
                "path": str(archive_path),
                "sha256": copied_sha,
                "size_bytes": copied_size,
            },
            "active_reclamation": {
                "path": str(old_path),
                "allowed_only_after_this_receipt_is_published_and_verified": True,
            },
            "spool_handled": False,
        }
    )
    write_canonical_json(
        receipt_path,
        receipt,
        kind="seed_checkpoint_archive_receipt",
    )
    _verify_canonical_receipt(receipt_path, expected=receipt)

    final_old_sha, final_old_size = _hash_stable_regular_file(old_path)
    final_successor_sha, final_successor_size = _hash_stable_regular_file(
        successor_path
    )
    if (final_old_sha, final_old_size) != (old_sha, old_size):
        raise SeedWorkflowError("旧 checkpoint 在 active 回收前发生变化")
    if (final_successor_sha, final_successor_size) != (
        successor_sha,
        successor_size,
    ):
        raise SeedWorkflowError("successor checkpoint 在归档期间发生变化")
    old_path.unlink()
    _fsync_directory(active)
    return {
        **result,
        "active_removed": True,
        "released_active_temporary_bytes": old_size,
        "receipt_fingerprint_sha256": receipt[
            "receipt_fingerprint_sha256"
        ],
        "recovery": {
            "source": str(archive_path),
            "sha256": copied_sha,
            "size_bytes": copied_size,
            "message_zh": "归档副本经 SHA/size 和规范收据复核，可用于恢复 active 旧 checkpoint。",
        },
    }


def _run_seed_checkpoint_archive(args: argparse.Namespace) -> Mapping[str, Any]:
    """归档与 active unlink 始终持有同一 seed 单写锁。"""

    with _seed_execution_lock(args.prepared_directory):
        return _run_seed_checkpoint_archive_locked(args)


def _checked_raw_artifact(
    raw_root: Path,
    relative_path: Any,
) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise SeedWorkflowError("state_seed_rib.relative_path 非法")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise SeedWorkflowError("state_seed_rib.relative_path 必须是安全相对路径")
    candidate = raw_root / relative
    try:
        metadata = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise SeedWorkflowError("压缩 seed 原始制品不存在或不可读") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or raw_root not in resolved.parents
    ):
        raise SeedWorkflowError("压缩 seed 原始制品必须是 raw_root 内的普通文件")
    return resolved


def _assert_latest_full_seed_checkpoint(
    *,
    active: Path,
    checkpoint_path: Path,
    selection_id: str,
    checkpoint_sequence: int,
) -> None:
    pattern = re.compile(
        rf"^{re.escape(selection_id)}\.worker\.([0-9]+)\.full-seed\."
        r"[0-9a-f]{16}\.json(?:\.gz)?$"
    )
    _checkpoint_directory_bytes(active)
    for entry in active.iterdir():
        match = pattern.fullmatch(entry.name)
        if match is None:
            continue
        sequence = int(match.group(1))
        if sequence > checkpoint_sequence or (
            sequence == checkpoint_sequence and entry != checkpoint_path
        ):
            raise SeedWorkflowError("指定 checkpoint 不是 active root 中的最新完整 checkpoint")


def _spool_retirement_attempt_failure(
    *,
    message: str,
    base_result: Mapping[str, Any],
    attempt_receipt_path: Path,
    attempt_receipt: Mapping[str, Any],
    observed_raw_bytes_read: int | None,
    process_runtime: Mapping[str, Any],
    exit_code: int,
) -> SeedSpoolRetirementAttemptError:
    accounting = attempt_receipt["raw_accounting"]
    result = {
        **dict(base_result),
        "ok": False,
        "spool_removed": False,
        "released_active_temporary_bytes": 0,
        "error_type": "SeedSpoolRetirementAttemptError",
        "message_zh": message,
        "runtime_exit_code": int(exit_code),
        "process_runtime": dict(process_runtime),
        "attempt_receipt": {
            "path": str(attempt_receipt_path),
            "attempt_id": attempt_receipt["attempt_id"],
            "receipt_fingerprint_sha256": attempt_receipt[
                "receipt_fingerprint_sha256"
            ],
            "status": attempt_receipt["status"],
            "durable_before_raw_open": True,
        },
        "failed_attempt_raw_accounting": {
            "reservation_policy": accounting["reservation_policy"],
            "observed_raw_bytes_read_this_attempt": observed_raw_bytes_read,
            "accounted_raw_bytes_this_attempt": accounting[
                "full_artifact_reserved_bytes"
            ],
            "cumulative_new_raw_read_bytes_after_reservation": accounting[
                "cumulative_new_raw_read_bytes_after_reservation"
            ],
            "retry_rule_zh": (
                "该 create-only attempt 收据不会删除；重试会先复核并累计本次"
                "完整制品预留，因此失败或崩溃不会把原始读取伪装为零。"
            ),
        },
        "recovery": {
            "spool_preserved": True,
            "compressed_raw_preserved": True,
            "retry_same_command": True,
            "message_zh": (
                "修复运行时或完整性问题后重试同一 seed-retire-spool 命令；"
                "既有 attempt 收据会被纳入 50GB 累计门禁。"
            ),
        },
    }
    return SeedSpoolRetirementAttemptError(
        message,
        result=result,
        exit_code=exit_code,
    )


def _run_seed_spool_retirement_locked(
    args: argparse.Namespace,
    *,
    clock: Any = time.monotonic,
    process_started_at: float | None = None,
) -> Mapping[str, Any]:
    """seed 完成后，在可由压缩原件重建的前提下显式退役 spool。"""

    process_started_at = _process_clock_start(
        clock=clock,
        process_started_at=process_started_at,
    )
    active = _checked_directory(args.checkpoint_directory, "checkpoint_directory")
    _assert_seed_mutation_path_allowed(active, "checkpoint_directory")
    checkpoint_path = _checked_resume_checkpoint(args.checkpoint, active)
    raw_root = _checked_directory(args.raw_root, "raw_root")
    receipt_directory = _checked_independent_history_directory(
        args.retirement_directory,
        active_directory=active,
        field="retirement_directory",
    )
    _assert_seed_mutation_path_allowed(
        receipt_directory, "retirement_directory"
    )
    if _paths_overlap(receipt_directory, raw_root):
        raise SeedWorkflowError("retirement_directory 必须与 raw_root 独立")

    context = _load_seed_verification_context(args)
    verified = _verify_seed_checkpoint_with_context(
        checkpoint_path, args=args, context=context
    )
    if (
        verified["position"]["phase"] != "updates"
        or verified["seed_progress"]["seed_parse_complete"] is not True
        or not verified["seed_spool_reclamation_eligibility"]["eligible"]
    ):
        raise SeedWorkflowError("只有已完成 seed 的完整 checkpoint 才能退役 spool")
    _assert_latest_full_seed_checkpoint(
        active=active,
        checkpoint_path=checkpoint_path,
        selection_id=context["selection"]["selection_id"],
        checkpoint_sequence=int(verified["checkpoint_sequence"]),
    )
    limits = effective_resource_limits(context["profile"])

    spool_binding = verified["seed_spool"]
    spool_name = spool_binding["file_name"]
    if Path(spool_name).name != spool_name:
        raise SeedWorkflowError("checkpoint seed_spool.file_name 不是安全文件名")
    spool_path = active / spool_name
    spool_sha, spool_size, spool_identity, _spool_runtime = (
        _hash_stable_regular_file_with_runtime_gate(
            spool_path,
            clock=clock,
            process_started_at=process_started_at,
            limits=limits,
            phase="seed_spool_hash_verification",
        )
    )
    attested = context["seed_spool_attestation"]["decompressed"]
    if (
        spool_sha != spool_binding["sha256"]
        or spool_size != spool_binding["size_bytes"]
        or spool_sha != attested["sha256"]
        or spool_size != attested["size_bytes"]
    ):
        raise SeedWorkflowError("seed spool SHA/size 与 checkpoint/attestation 不一致")

    seed = context["selection"]["roles"]["state_seed_rib"]
    checkpoint_cumulative = int(verified["resources"]["new_raw_read_bytes"])
    prior_attempts = _load_prior_spool_retirement_attempts(
        receipt_directory,
        spool_name=spool_name,
        selection_id=context["selection"]["selection_id"],
        checkpoint_fingerprint_sha256=verified[
            "checkpoint_fingerprint_sha256"
        ],
        seed_artifact=seed,
    )
    prior_attempt_reserved = sum(
        int(row["raw_accounting"]["full_artifact_reserved_bytes"])
        for row in prior_attempts
    )
    cumulative_before = checkpoint_cumulative + prior_attempt_reserved
    projected_cumulative = cumulative_before + int(seed["size_bytes"])
    compressed_path = _checked_raw_artifact(raw_root, seed["relative_path"])
    compressed_identity_before = _regular_file_identity(compressed_path)
    compressed_size_before = int(compressed_identity_before[2])
    if compressed_size_before != seed["size_bytes"]:
        raise SeedWorkflowError("压缩 seed 原始制品 size 与 manifest 不一致")

    receipt_path = receipt_directory / (
        f"{spool_name}.retirement-receipt.json"
    )
    if receipt_path.exists() or receipt_path.is_symlink():
        existing_receipt = _load_spool_retirement_receipt(receipt_path)
        recovered = _validate_spool_retirement_success_receipt(
            existing_receipt,
            receipt_path=receipt_path,
            checkpoint_path=checkpoint_path,
            verified=verified,
            spool_path=spool_path,
            spool_binding=spool_binding,
            compressed_path=compressed_path,
            compressed_identity=compressed_identity_before,
            seed_artifact=seed,
            prior_attempts=prior_attempts,
            max_new_raw_read_bytes=int(limits.max_new_raw_read_bytes),
        )
        if spool_identity != recovered["spool_identity"]:
            raise SeedWorkflowError(
                "seed spool 与成功收据记录的稳定文件身份不一致"
            )
        before_finalize = _raise_if_seed_runtime_boundary(
            clock=clock,
            process_started_at=process_started_at,
            limits=limits,
            phase="seed_spool_retirement_success_receipt_finalize",
            bytes_read=0,
        )
        finalized = not bool(args.dry_run)
        if finalized:
            if _regular_file_identity(spool_path) != spool_identity:
                raise SeedWorkflowError("seed spool 在恢复退役前发生变化")
            if (
                _regular_file_identity(compressed_path)
                != recovered["compressed_identity"]
            ):
                raise SeedWorkflowError("压缩 seed 原件在恢复退役前发生变化")
            spool_path.unlink()
            _fsync_directory(active)
        return {
            "ok": True,
            "schema_version": "rrc25-seed-spool-retirement-result/v2",
            "dry_run": bool(args.dry_run),
            "spool_removed": finalized,
            "released_active_temporary_bytes": spool_size if finalized else 0,
            "would_release_active_temporary_bytes": spool_size,
            "opens_raw_mrt": False,
            "opens_compressed_raw_for_hash_verification": False,
            "parses_raw_mrt": False,
            "database_connections": 0,
            "database_write_operations": 0,
            "checkpoint_path": str(checkpoint_path),
            "checkpoint_sequence": verified["checkpoint_sequence"],
            "spool": dict(existing_receipt["spool"]),
            "compressed_raw": {
                **dict(existing_receipt["compressed_raw"]),
                "hash_reused_from_verified_success_receipt": True,
            },
            "attempt_receipt": dict(
                existing_receipt["raw_verification_attempt_receipt"]
            ),
            "receipt_path": str(receipt_path),
            "receipt_fingerprint_sha256": existing_receipt[
                "receipt_fingerprint_sha256"
            ],
            "recoverable_by_rebuild_from_compressed_raw": True,
            "resource_accounting": {
                **dict(existing_receipt["resource_accounting"]),
                "new_raw_read_bytes_this_invocation": 0,
                "existing_success_receipt_reused": True,
            },
            "process_runtime": before_finalize,
            "idempotent_finalize_from_success_receipt": True,
            "post_retirement_update_temporary_budget": {
                "seed_spool_counted": not finalized,
                "effective_only_after_spool_removed": True,
                "released_scratch_bytes": spool_size if finalized else 0,
                "message_zh": (
                    "已复核既有成功收据、raw attempt 与文件身份；本次未重读"
                    "压缩原件，仅完成可恢复的 spool 退役。"
                ),
            },
        }

    if projected_cumulative >= limits.max_new_raw_read_bytes:
        raise SeedWorkflowError(
            "压缩 seed 原件复核会使累计新原始读取达到 50GB 审批边界"
        )

    before_admission = _raise_if_seed_runtime_boundary(
        clock=clock,
        process_started_at=process_started_at,
        limits=limits,
        phase="seed_spool_retirement_raw_admission",
        bytes_read=0,
    )
    admission = _seed_spool_retirement_raw_admission(
        compressed_size_bytes=int(seed["size_bytes"]),
        process_runtime=before_admission,
        limits=limits,
    )
    if not admission["allowed"]:
        raise SeedWorkflowError(
            "压缩 seed 原件打开前 420 秒 admission 失败："
            + str(admission["reason"])
        )

    # 旧实现把 projected_cumulative 直接拼进 --prior-new-raw-bytes，会让恢复
    # 命令重新拥有任意重置累计值的能力。当前只证明原件可重建；在 seed 与退役
    # reservation 被安全 roll-forward 到下一份 prior ledger receipt 前，不生成
    # 可执行恢复命令，也不把 probe 旧 terminal 冒充为当前累计终点。
    recovery_argv = None
    recovery_accounting_requirement = (
        "publish_create_only_prior_raw_rollforward_receipt_covering_seed_and_"
        "retirement_reservations_before_rebuild"
    )
    result = {
        "ok": True,
        "schema_version": "rrc25-seed-spool-retirement-result/v2",
        "dry_run": bool(args.dry_run),
        "spool_removed": False,
        "opens_raw_mrt": not bool(args.dry_run),
        "opens_compressed_raw_for_hash_verification": not bool(args.dry_run),
        "parses_raw_mrt": False,
        "database_connections": 0,
        "database_write_operations": 0,
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sequence": verified["checkpoint_sequence"],
        "spool": {
            "path": str(spool_path),
            "sha256": spool_sha,
            "size_bytes": spool_size,
            "stable_file_identity": _file_identity_payload(spool_identity),
        },
        "compressed_raw": {
            "path": str(compressed_path),
            "relative_path": seed["relative_path"],
            "artifact_id": seed["artifact_id"],
            "expected_sha256": seed["file_sha256"],
            "expected_size_bytes": seed["size_bytes"],
            "hash_verified": False,
        },
        "receipt_path": str(receipt_path),
        "raw_verification_admission": admission,
        "process_runtime_before_raw_open": before_admission,
        "would_release_active_temporary_bytes": spool_size,
        "released_active_temporary_bytes": 0,
        "recoverable_by_rebuild_from_compressed_raw": True,
        "recovery_command_argv": recovery_argv,
        "recovery_accounting_requirement": recovery_accounting_requirement,
        "automatic": False,
        "resource_accounting": {
            "checkpoint_cumulative_new_raw_read_bytes": checkpoint_cumulative,
            "prior_retirement_attempt_count": len(prior_attempts),
            "prior_failed_or_unknown_attempt_reserved_bytes": (
                prior_attempt_reserved
            ),
            "cumulative_new_raw_read_bytes_before_retirement_verification": (
                cumulative_before
            ),
            "retirement_verification_new_raw_read_bytes": int(seed["size_bytes"]),
            "cumulative_new_raw_read_bytes_after_retirement_verification": (
                projected_cumulative
            ),
            "reservation_policy": (
                "full_artifact_reserved_before_open_failed_or_crashed_attempts_still_count"
            ),
            "seed_spool_hash_bytes_are_temporary_artifact_reads": spool_size,
            "database_writes": 0,
        },
        "post_retirement_update_temporary_budget": {
            "seed_spool_counted": False,
            "effective_only_after_spool_removed": not bool(args.dry_run),
            "released_scratch_bytes": 0 if args.dry_run else spool_size,
            "message_zh": (
                "spool 成功退役后不再计入后续 UPDATE 阶段临时空间；压缩原件"
                "仍保留并可按收据命令重建。"
            ),
        },
    }
    if args.dry_run:
        return result

    attempt_id = (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + secrets.token_hex(8)
    )
    attempt_receipt_path = receipt_directory / (
        f"{spool_name}.raw-verification-attempt.{attempt_id}.json"
    )
    attempt_receipt = _spool_retirement_attempt_receipt_payload(
        {
            "operation": "seed_spool_retirement_raw_verification_attempt",
            "attempt_id": attempt_id,
            "started_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "raw_verification_reserved_outcome_unknown_until_success_receipt",
            "selection_id": context["selection"]["selection_id"],
            "checkpoint": {
                "path": str(checkpoint_path),
                "checkpoint_sequence": verified["checkpoint_sequence"],
                "checkpoint_fingerprint_sha256": verified[
                    "checkpoint_fingerprint_sha256"
                ],
            },
            "spool": result["spool"],
            "compressed_raw_expected": {
                "path": str(compressed_path),
                "relative_path": seed["relative_path"],
                "artifact_id": seed["artifact_id"],
                "file_sha256": seed["file_sha256"],
                "size_bytes": seed["size_bytes"],
            },
            "raw_open_authorization": (
                "granted_only_after_this_receipt_is_durable_and_runtime_is_rechecked"
            ),
            "raw_accounting": {
                "reservation_policy": (
                    "full_artifact_reserved_before_open_failed_or_crashed_attempts_still_count"
                ),
                "checkpoint_cumulative_new_raw_read_bytes": checkpoint_cumulative,
                "prior_attempt_reserved_bytes": prior_attempt_reserved,
                "cumulative_new_raw_read_bytes_before_attempt": cumulative_before,
                "full_artifact_reserved_bytes": int(seed["size_bytes"]),
                "cumulative_new_raw_read_bytes_after_reservation": (
                    projected_cumulative
                ),
            },
            "runtime_policy": {
                "clock_semantics": "same_process_monotonic_clock_as_seed_start_resume",
                "raw_admission": admission,
                "worker_soft_stop_seconds": float(
                    limits.worker_soft_stop_seconds
                ),
                "max_worker_runtime_seconds": float(
                    limits.max_worker_runtime_seconds
                ),
                "runtime_check_granularity_bytes": 1024 * 1024,
                "failure_behavior": "fail_closed_preserve_spool_no_success_receipt",
            },
            "recovery": {
                "receipt_is_create_only": True,
                "retry_scans_and_counts_this_reservation": True,
                "spool_must_remain_until_verified_success_receipt": True,
            },
        }
    )
    write_canonical_json(
        attempt_receipt_path,
        attempt_receipt,
        kind="seed_spool_retirement_raw_attempt_receipt",
    )
    _verify_canonical_receipt(
        attempt_receipt_path,
        expected=attempt_receipt,
    )

    try:
        before_raw_open = _raise_if_seed_runtime_boundary(
            clock=clock,
            process_started_at=process_started_at,
            limits=limits,
            phase="seed_spool_retirement_before_raw_open",
            bytes_read=0,
        )
        refreshed_admission = _seed_spool_retirement_raw_admission(
            compressed_size_bytes=int(seed["size_bytes"]),
            process_runtime=before_raw_open,
            limits=limits,
        )
        if not refreshed_admission["allowed"]:
            raise SeedWorkflowError(
                "attempt 收据发布后同进程剩余预算不足，未打开压缩原件："
                + str(refreshed_admission["reason"])
            )
        (
            compressed_sha,
            compressed_size,
            compressed_identity,
            after_raw_hash,
        ) = _hash_stable_regular_file_with_runtime_gate(
            compressed_path,
            clock=clock,
            process_started_at=process_started_at,
            limits=limits,
            phase="compressed_seed_raw_hash_verification",
        )
    except _SeedRuntimeBoundaryError as error:
        raise _spool_retirement_attempt_failure(
            message=str(error),
            base_result=result,
            attempt_receipt_path=attempt_receipt_path,
            attempt_receipt=attempt_receipt,
            observed_raw_bytes_read=error.bytes_read,
            process_runtime=error.observation,
            exit_code=error.exit_code,
        ) from error
    except (OSError, SeedWorkflowError) as error:
        failure_runtime = _process_runtime_observation(
            clock=clock,
            process_started_at=process_started_at,
            planned_seconds=SEED_SPOOL_RETIREMENT_RAW_ADMISSION_SECONDS,
            limits=limits,
        )
        raise _spool_retirement_attempt_failure(
            message=str(error),
            base_result=result,
            attempt_receipt_path=attempt_receipt_path,
            attempt_receipt=attempt_receipt,
            observed_raw_bytes_read=None,
            process_runtime=failure_runtime,
            exit_code=(_runtime_exit_code(failure_runtime) or 2),
        ) from error
    if (
        compressed_sha != seed["file_sha256"]
        or compressed_size != seed["size_bytes"]
    ):
        raise _spool_retirement_attempt_failure(
            message="压缩 seed 原始制品 SHA/size 与 manifest 不一致",
            base_result=result,
            attempt_receipt_path=attempt_receipt_path,
            attempt_receipt=attempt_receipt,
            observed_raw_bytes_read=compressed_size,
            process_runtime=after_raw_hash,
            exit_code=2,
        )

    result = {
        **result,
        "compressed_raw": {
            "path": str(compressed_path),
            "relative_path": seed["relative_path"],
            "artifact_id": seed["artifact_id"],
            "sha256": compressed_sha,
            "size_bytes": compressed_size,
            "hash_verified": True,
            "stable_file_identity": _file_identity_payload(
                compressed_identity
            ),
        },
        "attempt_receipt": {
            "path": str(attempt_receipt_path),
            "attempt_id": attempt_id,
            "receipt_fingerprint_sha256": attempt_receipt[
                "receipt_fingerprint_sha256"
            ],
            "status": attempt_receipt["status"],
            "durable_before_raw_open": True,
        },
        "process_runtime_after_raw_hash": after_raw_hash,
    }

    try:
        _raise_if_seed_runtime_boundary(
            clock=clock,
            process_started_at=process_started_at,
            limits=limits,
            phase="seed_spool_retirement_before_success_receipt",
            bytes_read=compressed_size,
        )
    except _SeedRuntimeBoundaryError as error:
        raise _spool_retirement_attempt_failure(
            message=str(error),
            base_result=result,
            attempt_receipt_path=attempt_receipt_path,
            attempt_receipt=attempt_receipt,
            observed_raw_bytes_read=compressed_size,
            process_runtime=error.observation,
            exit_code=error.exit_code,
        ) from error

    receipt = _spool_retirement_receipt_payload(
        {
            "operation": "seed_spool_retirement",
            "retired_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "checkpoint": {
                "path": str(checkpoint_path),
                "checkpoint_sequence": verified["checkpoint_sequence"],
                "checkpoint_fingerprint_sha256": verified[
                    "checkpoint_fingerprint_sha256"
                ],
            },
            "spool": result["spool"],
            "compressed_raw": result["compressed_raw"],
            "raw_verification_attempt_receipt": result["attempt_receipt"],
            "recoverable_by_rebuild_from_compressed_raw": True,
            "recovery_command_argv": recovery_argv,
            "recovery_accounting_requirement": recovery_accounting_requirement,
            "resource_accounting": result["resource_accounting"],
            "active_reclamation": {
                "path": str(spool_path),
                "allowed_only_after_this_receipt_is_published_and_verified": True,
            },
        }
    )
    write_canonical_json(
        receipt_path,
        receipt,
        kind="seed_spool_retirement_receipt",
    )
    _verify_canonical_receipt(receipt_path, expected=receipt)

    try:
        before_unlink = _raise_if_seed_runtime_boundary(
            clock=clock,
            process_started_at=process_started_at,
            limits=limits,
            phase="seed_spool_retirement_before_spool_unlink",
            bytes_read=compressed_size,
        )
    except _SeedRuntimeBoundaryError as error:
        raise _spool_retirement_attempt_failure(
            message=str(error),
            base_result={**result, "success_receipt_path": str(receipt_path)},
            attempt_receipt_path=attempt_receipt_path,
            attempt_receipt=attempt_receipt,
            observed_raw_bytes_read=compressed_size,
            process_runtime=error.observation,
            exit_code=error.exit_code,
        ) from error
    if _regular_file_identity(spool_path) != spool_identity:
        raise SeedWorkflowError("seed spool 在退役前发生变化")
    if _regular_file_identity(compressed_path) != compressed_identity:
        raise SeedWorkflowError("压缩 seed 原始制品在退役前发生变化")
    spool_path.unlink()
    _fsync_directory(active)
    return {
        **result,
        "spool_removed": True,
        "released_active_temporary_bytes": spool_size,
        "process_runtime": before_unlink,
        "receipt_fingerprint_sha256": receipt[
            "receipt_fingerprint_sha256"
        ],
    }


def _run_seed_spool_retirement(
    args: argparse.Namespace,
    *,
    clock: Any = time.monotonic,
    process_started_at: float | None = None,
) -> Mapping[str, Any]:
    """退役 reservation、成功收据与 spool unlink 共用 seed 单写锁。"""

    with _seed_execution_lock(args.prepared_directory):
        return _run_seed_spool_retirement_locked(
            args,
            clock=clock,
            process_started_at=process_started_at,
        )


def _load_plan(args: argparse.Namespace, *, allow_existing: bool):
    return prepare_research_plan(
        profile=load_json_metadata(args.profile),
        artifact_manifest=load_json_metadata(args.manifest),
        manifest_verification=load_json_metadata(args.manifest_verification),
        mapping_snapshot=load_json_metadata(args.mapping),
        code_sha256=args.code_sha256,
        output_root=args.output_root,
        maximum_artifacts_per_chunk=args.maximum_artifacts_per_chunk,
        allow_existing_run=allow_existing,
        pilot_end_exclusive=args.pilot_end_exclusive,
        estimated_worker_seconds=args.estimated_worker_seconds,
        estimated_temporary_bytes=args.estimated_temporary_bytes,
    )


def _print_json(value: Mapping[str, Any], *, stream: Any = None) -> None:
    if stream is None:
        stream = sys.stdout
    stream.write(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "dry-run":
            plan = _load_plan(args, allow_existing=False)
            _print_json(
                build_worker_plan(plan) if args.worker_plan_only else plan.to_dict()
            )
            return 0 if plan.ready else 3
        if args.command == "execute":
            plan = _load_plan(args, allow_existing=False)
            if not plan.ready:
                _print_json(plan.to_dict(), stream=sys.stderr)
                return 3
            executor = FixtureExecutor(load_json_metadata(args.fixture_executor))
            result = execute_research(plan, executor)
            _print_json(result.to_dict())
            return 0 if result.status == "completed" else 4
        if args.command == "resume":
            plan = _load_plan(args, allow_existing=True)
            if not plan.ready:
                _print_json(plan.to_dict(), stream=sys.stderr)
                return 3
            executor = FixtureExecutor(load_json_metadata(args.fixture_executor))
            result = resume_research(plan, executor)
            _print_json(result.to_dict())
            return 0 if result.status == "completed" else 4
        if args.command == "verify":
            plan = _load_plan(args, allow_existing=True)
            if not plan.ready:
                _print_json(plan.to_dict(), stream=sys.stderr)
                return 3
            result = verify_research_run(
                plan.run_directory, expected_bindings=plan.bindings
            )
            _print_json(result.to_dict())
            return 0
        if args.command == "seed-dry-run":
            context = _seed_context(
                args,
                require_empty_checkpoint_directory=(
                    args.resume_checkpoint is None
                ),
                resume_checkpoint_path=args.resume_checkpoint,
            )
            result = _seed_public_plan(context)
            _print_json(result)
            return 0 if result["ok"] else 3
        if args.command == "seed-start":
            result, exit_code = _run_seed_segment(args, resume=False)
            _print_json(result, stream=(sys.stdout if exit_code == 0 else sys.stderr))
            return exit_code
        if args.command == "seed-resume":
            result, exit_code = _run_seed_segment(args, resume=True)
            _print_json(result, stream=(sys.stdout if exit_code == 0 else sys.stderr))
            return exit_code
        if args.command == "seed-reconcile-workspace":
            result = _run_seed_workspace_reconciliation(args)
            _print_json(result)
            return 0
        if args.command == "seed-verify":
            result = _verify_seed_only(args)
            _print_json(result, stream=(sys.stdout if result["ok"] else sys.stderr))
            return int(result["runtime_exit_code"])
        if args.command == "seed-archive-checkpoint":
            result = _run_seed_checkpoint_archive(args)
            _print_json(result)
            return 0
        if args.command == "seed-retire-spool":
            result = _run_seed_spool_retirement(args)
            _print_json(result)
            return 0
        raise ResearchCoordinatorError("未知子命令")
    except SeedSpoolRetirementAttemptError as error:
        _print_json(error.result, stream=sys.stderr)
        return error.exit_code
    except _SeedRuntimeBoundaryError as error:
        _print_json(
            {
                "ok": False,
                "error_type": type(error).__name__,
                "message_zh": str(error),
                "runtime_exit_code": error.exit_code,
                "phase": error.phase,
                "bytes_read_before_stop": error.bytes_read,
                "process_runtime": error.observation,
                "spool_removed": False,
                "raw_attempt_receipt_published": False,
                "message_recovery_zh": (
                    "raw 尚未获得 create-only attempt 收据授权；本次不计原始"
                    "制品预留，spool 保持不变。"
                ),
            },
            stream=sys.stderr,
        )
        return error.exit_code
    except (
        BoundedPilotWorkerError,
        CountryImpactError,
        FileExistsError,
        OSError,
        ResearchCoordinatorError,
        ResearchInputError,
        ResearchProfileError,
        SeedWorkflowError,
        SparsePilotError,
        ValueError,
    ) as error:
        _print_json(
            {
                "ok": False,
                "error_type": type(error).__name__,
                "message_zh": str(error),
            },
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
