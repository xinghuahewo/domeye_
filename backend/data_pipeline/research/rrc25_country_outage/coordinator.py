"""伊朗 RRC25 国家中断研究的统一有界协调器。

协调器只接受已解析的 Profile、父 manifest、manifest 验证结果、映射快照和
显式代码 SHA-256。``prepare_research_plan`` 只处理这些元数据，绝不会打开
manifest 中的原始 MRT 路径。真正的逐 record 处理通过调用方注入的 executor
完成，因此本模块既不连接数据库，也不依赖旧项目或生产服务。

执行中每个 RIB 独立分块，UPDATE 按最多五个输入制品分块，并在每个
完整 physical-record 边界检查 540 秒
软停和 50GB/600 秒/5GB/数据库写硬门。输出使用不可覆盖的规范 JSON/JSONL
gzip；首次执行在隐藏 staging 目录完成后再发布，恢复只添加新制品，不覆盖
既有文件。检查点严格绑定 Profile、输入 selection、代码和映射四个哈希。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import time
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence, Tuple

from ...route_event import artifact_id_v1
from ..resource_gate import (
    DEFAULT_HARD_RUNTIME_SECONDS,
    DEFAULT_MAX_NEW_RAW_READ_BYTES,
    DEFAULT_MAX_TEMPORARY_BYTES,
    DEFAULT_SOFT_RUNTIME_SECONDS,
    ResourceLimits,
    ResourceUsage,
    WriteTarget,
    evaluate_resource_gate,
)
from .file_artifacts import (
    PublishedArtifact,
    ResearchArtifactError,
    build_checkpoint,
    canonical_json,
    verify_checkpoint,
    write_canonical_json,
    write_canonical_jsonl_gzip,
)
from .input_resolver import resolve_research_inputs
from .profile import (
    profile_sha256,
    research_run_id_v1,
    validate_research_profile,
)


PLAN_SCHEMA_VERSION = "rrc25-iran-research-plan/v1"
WORKER_PLAN_SCHEMA_VERSION = "rrc25-iran-research-worker-plan/v1"
RUN_STATE_SCHEMA_VERSION = "rrc25-iran-research-run-state/v1"
RESUME_STATE_SCHEMA_VERSION = "rrc25-iran-research-resume-state/v1"
SEMANTIC_CHAIN_SCHEMA = b"rrc25-iran-research-semantic-chain/v1"

DEFAULT_MAX_ARTIFACTS_PER_CHUNK = 5
DEFAULT_PROTECTED_ROOTS = (
    "/home/bgpdata/Domeye",
    "/home/bgpdata/Domeye-Core/backend/core",
)
DEFAULT_PRODUCTION_ROOTS = (
    "/home/bgpdata/Domeye-Core",
    "/var/www",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ID_RE = re.compile(r"^art_v1_[0-9a-f]{32}$")
_RUN_ID_RE = re.compile(r"^research_run_v1_[0-9a-f]{24}$")
_RUN_STATE_NAME_RE = re.compile(
    r"^run-state-([0-9]{6})-([0-9a-f]{64})\.json$"
)
_SAFE_KIND_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")

FULL_PROFILE_MODE = "full_profile"
BOUNDED_PILOT_MODE = "bounded_pilot"
LEGACY_FULL_WINDOW_MODE = "full_window"


class ResearchCoordinatorError(ValueError):
    """协调计划、注入执行或研究制品不满足失败关闭边界。"""


def normalize_execution_mode(value: object) -> str:
    """返回仓库冻结枚举；只读兼容早期协调器的 ``full_window``。"""

    if value == LEGACY_FULL_WINDOW_MODE:
        return FULL_PROFILE_MODE
    if value in {FULL_PROFILE_MODE, BOUNDED_PILOT_MODE}:
        return str(value)
    raise ResearchCoordinatorError("execution_mode 非法")


@dataclass(frozen=True)
class ResearchChunk:
    index: int
    artifacts: Tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "artifact_count": len(self.artifacts),
            "compressed_bytes": sum(int(item["size_bytes"]) for item in self.artifacts),
            "artifact_ids": [str(item["artifact_id"]) for item in self.artifacts],
            "artifacts": [dict(item) for item in self.artifacts],
        }

    def to_summary_dict(self) -> dict[str, object]:
        result = self.to_dict()
        result.pop("artifacts")
        return result


@dataclass(frozen=True)
class ResearchPlan:
    profile: Mapping[str, Any]
    input_selection: Mapping[str, Any]
    mapping_snapshot: Mapping[str, Any]
    code_sha256: str
    run_id: str
    bindings: Mapping[str, str]
    output_root: Path
    run_directory: Path
    chunks: Tuple[ResearchChunk, ...]
    limits: ResourceLimits
    resource_gate: Mapping[str, Any]
    ready: bool
    findings_zh: Tuple[str, ...]
    allow_existing_run: bool
    execution_mode: str
    pilot_end_exclusive: str | None
    unprocessed_profile_interval: Mapping[str, str] | None
    acceptance_blockers_zh: Tuple[str, ...]

    @property
    def flat_artifacts(self) -> Tuple[tuple[int, Mapping[str, Any]], ...]:
        return tuple(
            (chunk.index, artifact)
            for chunk in self.chunks
            for artifact in chunk.artifacts
        )

    def to_dict(self) -> dict[str, object]:
        worker_plan = build_worker_plan(self)
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "run_id": self.run_id,
            "bindings": dict(self.bindings),
            "input_selection_id": self.input_selection.get("selection_id"),
            "input_selection_status": self.input_selection.get("status"),
            "output_directory": str(self.run_directory),
            "chunks": [chunk.to_summary_dict() for chunk in self.chunks],
            "worker_plan": worker_plan,
            "worker_plan_sha256": worker_plan["worker_plan_sha256"],
            "limits": self.limits.to_dict(),
            "resource_gate": dict(self.resource_gate),
            "ready": self.ready,
            "findings_zh": list(self.findings_zh),
            "execution_mode": self.execution_mode,
            "pilot_end_exclusive": self.pilot_end_exclusive,
            "unprocessed_profile_interval": (
                dict(self.unprocessed_profile_interval)
                if self.unprocessed_profile_interval
                else None
            ),
            "acceptance_state": (
                "not_accepted"
                if self.execution_mode == BOUNDED_PILOT_MODE
                else "pending"
            ),
            "acceptance_blockers_zh": list(self.acceptance_blockers_zh),
            "dry_run_opens_raw_mrt": False,
            "database_connections": 0,
        }


@dataclass(frozen=True)
class ExecutionRecord:
    """executor 在一个完整 physical-record 边界交还的事实。"""

    artifact_id: str
    record_ordinal: int
    output_record: Mapping[str, Any]
    new_raw_bytes_read: int
    temporary_bytes: int = 0
    database_write_operations: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_id, str) or _ARTIFACT_ID_RE.fullmatch(self.artifact_id) is None:
            raise ResearchCoordinatorError("ExecutionRecord.artifact_id 非法")
        _nonnegative_integer(self.record_ordinal, "record_ordinal")
        if not isinstance(self.output_record, Mapping):
            raise ResearchCoordinatorError("output_record 必须是对象")
        _nonnegative_integer(self.new_raw_bytes_read, "new_raw_bytes_read")
        _nonnegative_integer(self.temporary_bytes, "temporary_bytes")
        _nonnegative_integer(
            self.database_write_operations, "database_write_operations"
        )
        # 立即证明输出可规范序列化，避免写分片时才暴露 NaN/自定义对象。
        try:
            canonical_json(dict(self.output_record))
        except ResearchArtifactError as error:
            raise ResearchCoordinatorError("output_record 不能规范序列化") from error


RecordExecutor = Callable[
    [Mapping[str, Any], int], Iterable[ExecutionRecord]
]
Clock = Callable[[], float]


@dataclass(frozen=True)
class CoordinatorRunResult:
    run_directory: Path
    run_state: Mapping[str, Any]
    run_state_relative_path: str
    run_state_sha256: str

    @property
    def status(self) -> str:
        return str(self.run_state["status"])

    def to_dict(self) -> dict[str, object]:
        return {
            "run_directory": str(self.run_directory),
            "run_state_relative_path": self.run_state_relative_path,
            "run_state_sha256": self.run_state_sha256,
            "run_state": dict(self.run_state),
        }


@dataclass(frozen=True)
class VerificationResult:
    run_directory: Path
    run_id: str
    status: str
    bindings: Mapping[str, str]
    semantic_fingerprint_sha256: str
    output_count: int
    record_count: int
    run_state_relative_path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "verified": True,
            "run_directory": str(self.run_directory),
            "run_id": self.run_id,
            "status": self.status,
            "bindings": dict(self.bindings),
            "semantic_fingerprint_sha256": self.semantic_fingerprint_sha256,
            "output_count": self.output_count,
            "record_count": self.record_count,
            "run_state_relative_path": self.run_state_relative_path,
        }


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ResearchCoordinatorError(f"{field} 必须是 64 位小写 SHA-256")
    return value


def _nonnegative_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResearchCoordinatorError(f"{field} 必须是非负整数")
    return value


def _nonnegative_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResearchCoordinatorError(f"{field} 必须是非负有限数")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ResearchCoordinatorError(f"{field} 必须是非负有限数")
    return result


def _validate_directory(path: Path, field: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ResearchCoordinatorError(f"{field} 不存在或不可读") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ResearchCoordinatorError(f"{field} 必须是非符号链接目录")


def _safe_relative(value: object, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ResearchCoordinatorError(f"{field} 必须是非空相对路径")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ResearchCoordinatorError(f"{field} 必须是安全相对路径")
    return path


def _hash_regular(path: Path, maximum_bytes: int | None = None) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ResearchCoordinatorError(f"无法只读打开研究制品：{path.name}") from error
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ResearchCoordinatorError("研究制品必须是普通文件")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            size += len(block)
            if maximum_bytes is not None and size > maximum_bytes:
                raise ResearchCoordinatorError("研究制品超过读取上限")
            digest.update(block)
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, name) != getattr(after, name) for name in identity):
            raise ResearchCoordinatorError("研究制品在校验期间发生变化")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _read_regular_bytes(path: Path, *, maximum_bytes: int) -> tuple[bytes, str]:
    if (
        isinstance(maximum_bytes, bool)
        or not isinstance(maximum_bytes, int)
        or maximum_bytes <= 0
    ):
        raise ResearchCoordinatorError("maximum_bytes 必须是正整数")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ResearchCoordinatorError(f"无法只读打开元数据：{path.name}") from error
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ResearchCoordinatorError("元数据必须是普通文件")
        while True:
            block = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - size))
            if not block:
                break
            size += len(block)
            if size > maximum_bytes:
                raise ResearchCoordinatorError("元数据超过读取上限")
            chunks.append(block)
            digest.update(block)
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, name) != getattr(after, name) for name in identity):
            raise ResearchCoordinatorError("元数据在只读加载期间发生变化")
    finally:
        os.close(descriptor)
    return b"".join(chunks), digest.hexdigest()


def load_json_metadata(
    path: str | os.PathLike[str], *, maximum_bytes: int = 512 * 1024 * 1024
) -> dict[str, Any]:
    """安全读取小型 JSON 元数据；禁止符号链接、重复键和非有限数。"""

    source = Path(path)
    payload, _digest = _read_regular_bytes(source, maximum_bytes=maximum_bytes)

    def pairs_hook(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ResearchCoordinatorError(f"JSON 元数据字段重复：{key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ResearchCoordinatorError(f"JSON 元数据禁止非有限数：{value}")

    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except ResearchCoordinatorError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ResearchCoordinatorError("元数据不是合法 UTF-8 JSON") from error
    if not isinstance(parsed, Mapping):
        raise ResearchCoordinatorError("JSON 元数据根节点必须是对象")
    return dict(parsed)


def _mapping_sha256(mapping_snapshot: Mapping[str, Any]) -> str:
    if not isinstance(mapping_snapshot, Mapping):
        raise ResearchCoordinatorError("mapping_snapshot 必须是对象")
    if not isinstance(mapping_snapshot.get("snapshot_id"), str):
        raise ResearchCoordinatorError("mapping_snapshot 缺少 snapshot_id")
    _sha256(
        mapping_snapshot.get("semantic_fingerprint_sha256"),
        "mapping_snapshot.semantic_fingerprint_sha256",
    )
    return hashlib.sha256(
        canonical_json(dict(mapping_snapshot)).encode("utf-8")
    ).hexdigest()


def _effective_limits(profile: Mapping[str, Any]) -> ResourceLimits:
    configured = ResourceLimits.from_profile(profile)
    hard = min(
        configured.max_worker_runtime_seconds, DEFAULT_HARD_RUNTIME_SECONDS
    )
    soft = min(configured.worker_soft_stop_seconds, DEFAULT_SOFT_RUNTIME_SECONDS)
    if soft >= hard:
        # Profile 本身保证 soft < configured hard；全局更严格硬门收紧时，
        # 仍保留至少一秒硬门裕量并绝不放宽 540 秒。
        soft = max(0.001, hard - 1.0)
    return ResourceLimits(
        max_new_raw_read_bytes=min(
            configured.max_new_raw_read_bytes, DEFAULT_MAX_NEW_RAW_READ_BYTES
        ),
        max_temporary_bytes=min(
            configured.max_temporary_bytes, DEFAULT_MAX_TEMPORARY_BYTES
        ),
        max_worker_runtime_seconds=hard,
        worker_soft_stop_seconds=soft,
        database_writes="forbidden",
        output_storage="filesystem_only",
    )


def effective_resource_limits(profile: Mapping[str, Any]) -> ResourceLimits:
    """验证 Profile 后返回不宽于全局审批边界的实际生效限额。"""

    return _effective_limits(validate_research_profile(profile))


def _assert_plan_effective_limits(plan: "ResearchPlan") -> None:
    """重算计划身份与限额，拒绝 dataclass 构造后的手工漂移。"""

    try:
        normalized_profile = validate_research_profile(plan.profile)
        expected_limits = effective_resource_limits(normalized_profile)
        expected_bindings = {
            "profile_sha256": profile_sha256(normalized_profile),
            "input_selection_sha256": _sha256(
                plan.input_selection.get("semantic_fingerprint_sha256"),
                "plan.input_selection.semantic_fingerprint_sha256",
            ),
            "code_sha256": _sha256(plan.code_sha256, "plan.code_sha256"),
            "mapping_sha256": _mapping_sha256(plan.mapping_snapshot),
        }
    except (TypeError, ValueError) as error:
        raise ResearchCoordinatorError("plan 无法重建冻结身份与资源边界") from error
    if dict(plan.bindings) != expected_bindings:
        raise ResearchCoordinatorError("plan.bindings 与当前 Profile/输入/代码/映射不一致")
    expected_run_id = research_run_id_v1(
        normalized_profile,
        input_manifest_sha256=expected_bindings["input_selection_sha256"],
        mapping_sha256=expected_bindings["mapping_sha256"],
        processing_sha256=expected_bindings["code_sha256"],
    )
    if plan.run_id != expected_run_id:
        raise ResearchCoordinatorError("plan.run_id 与当前冻结身份不一致")
    if plan.run_directory != plan.output_root / plan.run_id:
        raise ResearchCoordinatorError("plan.run_directory 与 output_root/run_id 不一致")
    if plan.limits != expected_limits:
        raise ResearchCoordinatorError("plan.limits 与冻结 Profile 的有效边界不一致")

    selection = plan.input_selection
    selection_semantic = {
        key: value
        for key, value in selection.items()
        if key not in {"selection_id", "semantic_fingerprint_sha256"}
    }
    actual_selection_hash = hashlib.sha256(
        canonical_json(selection_semantic).encode("utf-8")
    ).hexdigest()
    if actual_selection_hash != expected_bindings["input_selection_sha256"]:
        raise ResearchCoordinatorError("plan.input_selection 内容指纹不一致")

    scoped = selection.get("schema_version") == "rrc25-scoped-input-selection/v1"
    if scoped:
        expected_mode = BOUNDED_PILOT_MODE
        expected_pilot_end = selection.get("pilot_end_exclusive")
        expected_interval = selection.get("unprocessed_profile_interval")
        scope_failures = selection.get("scope_failures")
        if not isinstance(scope_failures, list):
            raise ResearchCoordinatorError("scoped selection scope_failures 非法")
        expected_blockers = [
            "仅完成 bounded pilot；冻结 Profile 的剩余区间尚未处理。"
        ]
        if scope_failures:
            expected_blockers.append(
                "当前执行范围仍有输入缺口："
                + ",".join(sorted(set(map(str, scope_failures))))
            )
        selected_ids = selection.get("selected_artifact_ids")
        if not isinstance(selected_ids, list) or any(
            not isinstance(value, str) for value in selected_ids
        ):
            raise ResearchCoordinatorError("scoped selection artifact IDs 非法")
    else:
        expected_mode = FULL_PROFILE_MODE
        expected_pilot_end = None
        expected_interval = None
        expected_blockers = []
        selected_ids = [
            str(item["artifact_id"])
            for item in _selected_artifacts(selection)
        ]
    if (
        plan.execution_mode != expected_mode
        or plan.pilot_end_exclusive != expected_pilot_end
        or plan.unprocessed_profile_interval != expected_interval
        or list(plan.acceptance_blockers_zh) != expected_blockers
    ):
        raise ResearchCoordinatorError("plan 执行范围或 acceptance blockers 漂移")
    if selection.get("status") != "complete":
        raise ResearchCoordinatorError("plan input selection 不完整，禁止执行")

    flattened: list[Mapping[str, Any]] = []
    for expected_index, chunk in enumerate(plan.chunks):
        if chunk.index != expected_index or not 1 <= len(chunk.artifacts) <= 5:
            raise ResearchCoordinatorError("plan chunks 序号或大小非法")
        artifact_types = [item.get("artifact_type") for item in chunk.artifacts]
        if "rib" in artifact_types and artifact_types != ["rib"]:
            raise ResearchCoordinatorError("plan 每个 RIB 必须独立分块")
        flattened.extend(chunk.artifacts)
    if [str(item.get("artifact_id")) for item in flattened] != selected_ids:
        raise ResearchCoordinatorError("plan chunks 与冻结 selected artifacts 不一致")
    if scoped:
        if selection.get("selected_unique_artifact_count") != len(flattened):
            raise ResearchCoordinatorError("scoped selection artifact count 漂移")
        if selection.get("selected_unique_size_bytes") != sum(
            int(item.get("size_bytes", -1)) for item in flattened
        ):
            raise ResearchCoordinatorError("scoped selection artifact bytes 漂移")


def _selected_artifacts(
    selection: Mapping[str, Any], *, pilot_end_exclusive: str | None = None
) -> tuple[Mapping[str, Any], ...]:
    roles = selection.get("roles")
    if not isinstance(roles, Mapping):
        raise ResearchCoordinatorError("input selection 缺少 roles")
    priority = (
        "baseline_reference_rib",
        "state_seed_rib",
        "catch_up_updates",
        "analysis_updates",
        "analysis_ribs",
    )
    selected_rows: dict[str, Mapping[str, Any]] = {}
    selected_priorities: dict[str, int] = {}
    selected_roles: dict[str, set[str]] = {}
    for role_index, role in enumerate(priority):
        value = roles.get(role)
        rows: Iterable[object]
        if isinstance(value, Mapping):
            rows = (value,)
        elif isinstance(value, (list, tuple)):
            rows = value
        elif value is None:
            rows = ()
        else:
            raise ResearchCoordinatorError(f"input selection role {role} 非法")
        for row in rows:
            if not isinstance(row, Mapping):
                raise ResearchCoordinatorError(f"input selection role {role} 含非法制品")
            artifact_id = row.get("artifact_id")
            if not isinstance(artifact_id, str) or _ARTIFACT_ID_RE.fullmatch(artifact_id) is None:
                raise ResearchCoordinatorError("selected artifact_id 非法")
            size = row.get("size_bytes")
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                raise ResearchCoordinatorError("selected artifact.size_bytes 非法")
            if (
                pilot_end_exclusive is not None
                and role in {"analysis_updates", "analysis_ribs"}
                and str(row.get("artifact_time_utc", ""))
                >= pilot_end_exclusive
            ):
                continue
            selected_rows.setdefault(artifact_id, row)
            selected_priorities.setdefault(artifact_id, role_index)
            selected_roles.setdefault(artifact_id, set()).add(role)
    ordered = sorted(
        selected_rows,
        key=lambda artifact_id: (
            selected_priorities[artifact_id],
            str(selected_rows[artifact_id].get("artifact_time_utc", "")),
            str(selected_rows[artifact_id].get("artifact_type", "")),
            artifact_id,
        ),
    )
    return tuple(
        {
            **dict(selected_rows[artifact_id]),
            "selection_roles": sorted(selected_roles[artifact_id]),
        }
        for artifact_id in ordered
    )


def _pilot_scope(
    profile: Mapping[str, Any],
    selection: Mapping[str, Any],
    pilot_end_exclusive: str | None,
) -> tuple[
    str,
    str | None,
    Mapping[str, str] | None,
    bool,
    tuple[str, ...],
]:
    if pilot_end_exclusive is None:
        return (
            FULL_PROFILE_MODE,
            None,
            None,
            selection.get("status") == "complete",
            (),
        )
    if not isinstance(pilot_end_exclusive, str):
        raise ResearchCoordinatorError("pilot_end_exclusive 必须是 UTC Z 时间")
    try:
        pilot_end = datetime.strptime(
            pilot_end_exclusive, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        start = datetime.strptime(
            profile["window"]["start_utc"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        full_end = datetime.strptime(
            profile["window"]["end_exclusive_utc"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError) as error:
        raise ResearchCoordinatorError(
            "pilot_end_exclusive 或 Profile 窗口时间非法"
        ) from error
    granularity = int(profile["window"]["granularity_seconds"])
    if not start < pilot_end < full_end:
        raise ResearchCoordinatorError(
            "bounded pilot 结束必须严格位于冻结 Profile 窗口内部"
        )
    if int((pilot_end - start).total_seconds()) % granularity:
        raise ResearchCoordinatorError("pilot_end_exclusive 必须按 Profile 粒度对齐")

    scope_failures: list[str] = []
    failures = selection.get("failures", ())
    if not isinstance(failures, list):
        raise ResearchCoordinatorError("input selection failures 非法")
    slot_codes = {
        "analysis_update_slots_missing",
        "analysis_update_slots_unexpected",
        "analysis_rib_slots_missing",
        "analysis_rib_slots_unexpected",
    }
    for failure in failures:
        if not isinstance(failure, Mapping):
            scope_failures.append("input_selection_failure_invalid")
            continue
        code = str(failure.get("code", "unknown"))
        if code in slot_codes:
            details = failure.get("details")
            slots = details.get("slots", ()) if isinstance(details, Mapping) else ()
            if any(isinstance(slot, str) and slot < pilot_end_exclusive for slot in slots):
                scope_failures.append(code)
        else:
            # seed/baseline/catch-up 等缺口均直接影响 pilot。
            scope_failures.append(code)
    interval = {
        "start_utc": pilot_end_exclusive,
        "end_exclusive_utc": profile["window"]["end_exclusive_utc"],
        "boundary": "[start,end)",
        "reason": "bounded_pilot_profile_remainder_not_processed",
    }
    return (
        BOUNDED_PILOT_MODE,
        pilot_end_exclusive,
        interval,
        not scope_failures,
        tuple(sorted(set(scope_failures))),
    )


def _scoped_selection(
    selection: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
    *,
    execution_mode: str,
    pilot_end_exclusive: str | None,
    scope_complete: bool,
    scope_failures: Sequence[str],
    unprocessed_interval: Mapping[str, str] | None,
) -> Mapping[str, Any]:
    if execution_mode == FULL_PROFILE_MODE:
        return selection
    start = datetime.strptime(
        profile["window"]["start_utc"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    pilot_end = datetime.strptime(
        str(pilot_end_exclusive), "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    duration_seconds = int((pilot_end - start).total_seconds())
    update_interval = int(
        profile["input_selection"]["analysis_updates"]["slot_interval_seconds"]
    )
    rib_interval = int(
        profile["input_selection"]["analysis_ribs"]["slot_interval_seconds"]
    )
    expected_updates = duration_seconds // update_interval
    expected_ribs = ((duration_seconds - 1) // rib_interval) + 1
    observed_updates = sum(
        "analysis_updates" in item.get("selection_roles", ())
        for item in artifacts
    )
    observed_ribs = sum(
        "analysis_ribs" in item.get("selection_roles", ()) for item in artifacts
    )
    baseline_count = sum(
        "baseline_reference_rib" in item.get("selection_roles", ())
        for item in artifacts
    )
    semantic = {
        "schema_version": "rrc25-scoped-input-selection/v1",
        "parent_selection_id": selection["selection_id"],
        "parent_selection_sha256": selection["semantic_fingerprint_sha256"],
        "execution_mode": execution_mode,
        "pilot_end_exclusive": pilot_end_exclusive,
        "selected_artifact_ids": [str(item["artifact_id"]) for item in artifacts],
        "selected_unique_artifact_count": len(artifacts),
        "selected_unique_size_bytes": sum(int(item["size_bytes"]) for item in artifacts),
        "selected_compressed_bytes": sum(int(item["size_bytes"]) for item in artifacts),
        "status": "complete" if scope_complete else "incomplete",
        "scope_failures": list(scope_failures),
        "unprocessed_profile_interval": (
            dict(unprocessed_interval) if unprocessed_interval else None
        ),
        "coverage": {
            "analysis_updates": {
                "expected_count": expected_updates,
                "observed_count": observed_updates,
                "missing_count": max(0, expected_updates - observed_updates),
            },
            "analysis_ribs": {
                "expected_count": expected_ribs,
                "observed_count": observed_ribs,
                "missing_count": max(0, expected_ribs - observed_ribs),
            },
            "baseline_reference_rib": {
                "expected_count": 1,
                "observed_count": baseline_count,
            },
        },
    }
    fingerprint = hashlib.sha256(
        canonical_json(semantic).encode("utf-8")
    ).hexdigest()
    selection_id = "rsel_scope_v1_" + fingerprint[:32]
    return {
        **semantic,
        "selection_id": selection_id,
        "semantic_fingerprint_sha256": fingerprint,
    }


def _chunks(
    artifacts: Sequence[Mapping[str, Any]], maximum_artifacts: int
) -> tuple[ResearchChunk, ...]:
    if (
        isinstance(maximum_artifacts, bool)
        or not isinstance(maximum_artifacts, int)
        or maximum_artifacts <= 0
        or maximum_artifacts > DEFAULT_MAX_ARTIFACTS_PER_CHUNK
    ):
        raise ResearchCoordinatorError(
            "maximum_artifacts_per_chunk 必须是 1..5 整数"
        )
    result: list[ResearchChunk] = []
    pending_updates: list[Mapping[str, Any]] = []

    def flush_updates() -> None:
        if not pending_updates:
            return
        result.append(
            ResearchChunk(
                index=len(result),
                artifacts=tuple(pending_updates),
            )
        )
        pending_updates.clear()

    for artifact in artifacts:
        artifact_type = artifact.get("artifact_type")
        if artifact_type == "rib":
            # RIB 的解析成本显著高于 UPDATE；必须单独计时，不得让
            # baseline + state-seed 共用一个 worker 时间估算。
            flush_updates()
            result.append(
                ResearchChunk(index=len(result), artifacts=(artifact,))
            )
        elif artifact_type == "update":
            pending_updates.append(artifact)
            if len(pending_updates) == maximum_artifacts:
                flush_updates()
        else:
            raise ResearchCoordinatorError("selected artifact_type 非法")
    flush_updates()
    return tuple(result)


def build_worker_plan(plan: ResearchPlan) -> dict[str, Any]:
    """构造不含本机绝对路径、可直接交给服务器只读 worker 的计划。"""

    if not isinstance(plan, ResearchPlan):
        raise ResearchCoordinatorError("plan 必须是 ResearchPlan")
    semantic = {
        "schema_version": WORKER_PLAN_SCHEMA_VERSION,
        "run_id": plan.run_id,
        "execution_mode": normalize_execution_mode(plan.execution_mode),
        "bindings": dict(plan.bindings),
        "input_selection_id": plan.input_selection["selection_id"],
        "profile_window": {
            "start_utc": plan.profile["window"]["start_utc"],
            "end_exclusive_utc": plan.profile["window"]["end_exclusive_utc"],
            "boundary": "[start,end)",
        },
        "pilot_end_exclusive": plan.pilot_end_exclusive,
        "remaining_profile_interval": (
            dict(plan.unprocessed_profile_interval)
            if plan.unprocessed_profile_interval
            else None
        ),
        "blocking_incomplete_reasons_zh": list(plan.acceptance_blockers_zh),
        "execution_allowed": plan.ready,
        "chunks": [chunk.to_dict() for chunk in plan.chunks],
        "resource_estimate": dict(plan.resource_gate["usage"]),
        "resource_limits": plan.limits.to_dict(),
        "database_connections": 0,
        "raw_paths_are_collector_relative": True,
    }
    fingerprint = hashlib.sha256(
        canonical_json(semantic).encode("utf-8")
    ).hexdigest()
    result = {**semantic, "worker_plan_sha256": fingerprint}
    verify_worker_plan(result, expected_bindings=plan.bindings)
    return result


def verify_worker_plan(
    worker_plan: Mapping[str, Any],
    *,
    expected_bindings: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """纯元数据验证 worker handoff；不会解析任何 relative_path 内容。"""

    if not isinstance(worker_plan, Mapping):
        raise ResearchCoordinatorError("worker_plan 必须是对象")
    expected_fields = {
        "schema_version",
        "run_id",
        "execution_mode",
        "bindings",
        "input_selection_id",
        "profile_window",
        "pilot_end_exclusive",
        "remaining_profile_interval",
        "blocking_incomplete_reasons_zh",
        "execution_allowed",
        "chunks",
        "resource_estimate",
        "resource_limits",
        "database_connections",
        "raw_paths_are_collector_relative",
        "worker_plan_sha256",
    }
    if set(worker_plan) != expected_fields:
        raise ResearchCoordinatorError("worker_plan 顶层字段不闭合")
    payload = dict(worker_plan)
    fingerprint = _sha256(
        payload.pop("worker_plan_sha256"), "worker_plan.worker_plan_sha256"
    )
    expected_fingerprint = hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()
    if fingerprint != expected_fingerprint:
        raise ResearchCoordinatorError("worker_plan 内容指纹不一致")
    if payload.get("schema_version") != WORKER_PLAN_SCHEMA_VERSION:
        raise ResearchCoordinatorError("worker_plan schema_version 不支持")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise ResearchCoordinatorError("worker_plan.run_id 非法")
    mode = normalize_execution_mode(payload.get("execution_mode"))
    if payload.get("execution_mode") != mode:
        raise ResearchCoordinatorError("新 worker_plan 必须使用规范 execution_mode")
    bindings = payload.get("bindings")
    binding_fields = {
        "profile_sha256",
        "input_selection_sha256",
        "code_sha256",
        "mapping_sha256",
    }
    if not isinstance(bindings, Mapping) or set(bindings) != binding_fields:
        raise ResearchCoordinatorError("worker_plan.bindings 不闭合")
    normalized_bindings = {
        field: _sha256(bindings[field], f"worker_plan.bindings.{field}")
        for field in sorted(binding_fields)
    }
    if expected_bindings is not None:
        if set(expected_bindings) != binding_fields:
            raise ResearchCoordinatorError("expected_bindings 字段不闭合")
        for field in sorted(binding_fields):
            if _sha256(
                expected_bindings[field], f"expected_bindings.{field}"
            ) != normalized_bindings[field]:
                raise ResearchCoordinatorError(
                    f"worker_plan {field} 绑定不一致"
                )
    if payload.get("database_connections") != 0:
        raise ResearchCoordinatorError("worker_plan 禁止数据库连接")
    if payload.get("raw_paths_are_collector_relative") is not True:
        raise ResearchCoordinatorError("worker_plan 原始路径边界未冻结")
    if not isinstance(payload.get("execution_allowed"), bool):
        raise ResearchCoordinatorError("worker_plan.execution_allowed 非法")
    chunks = payload.get("chunks")
    if not isinstance(chunks, list):
        raise ResearchCoordinatorError("worker_plan.chunks 必须是数组")
    artifact_ids: set[str] = set()
    compressed_bytes = 0
    allowed_roles = {
        "baseline_reference_rib",
        "state_seed_rib",
        "catch_up_updates",
        "analysis_updates",
        "analysis_ribs",
    }
    artifact_fields = {
        "artifact_id",
        "artifact_type",
        "artifact_time_utc",
        "collector_id",
        "relative_path",
        "file_sha256",
        "size_bytes",
        "compression",
        "selection_roles",
    }
    for expected_index, chunk in enumerate(chunks):
        if not isinstance(chunk, Mapping) or set(chunk) != {
            "index",
            "artifact_count",
            "compressed_bytes",
            "artifact_ids",
            "artifacts",
        }:
            raise ResearchCoordinatorError("worker_plan chunk 字段不闭合")
        if chunk.get("index") != expected_index:
            raise ResearchCoordinatorError("worker_plan chunk index 必须连续")
        artifacts = chunk.get("artifacts")
        if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 5:
            raise ResearchCoordinatorError("worker_plan 每个 chunk 必须含 1..5 个制品")
        if chunk.get("artifact_count") != len(artifacts):
            raise ResearchCoordinatorError("worker_plan chunk artifact_count 不一致")
        chunk_ids = []
        chunk_bytes = 0
        chunk_artifact_types: list[str] = []
        for artifact in artifacts:
            if not isinstance(artifact, Mapping) or set(artifact) != artifact_fields:
                raise ResearchCoordinatorError("worker_plan artifact 字段不闭合")
            artifact_id = artifact.get("artifact_id")
            if (
                not isinstance(artifact_id, str)
                or _ARTIFACT_ID_RE.fullmatch(artifact_id) is None
                or artifact_id in artifact_ids
            ):
                raise ResearchCoordinatorError("worker_plan artifact_id 非法或重复")
            artifact_ids.add(artifact_id)
            chunk_ids.append(artifact_id)
            file_sha256 = _sha256(
                artifact.get("file_sha256"), "worker_plan artifact.file_sha256"
            )
            if artifact_id != artifact_id_v1(file_sha256):
                raise ResearchCoordinatorError(
                    "worker_plan artifact_id 与文件 SHA-256 不一致"
                )
            if artifact.get("artifact_type") not in {"rib", "update"}:
                raise ResearchCoordinatorError("worker_plan artifact_type 非法")
            chunk_artifact_types.append(str(artifact["artifact_type"]))
            if artifact.get("compression") != "gz":
                raise ResearchCoordinatorError("worker_plan 只允许 gzip MRT")
            collector = artifact.get("collector_id")
            relative = _safe_relative(
                artifact.get("relative_path"), "worker_plan artifact.relative_path"
            )
            if not isinstance(collector, str) or relative.parts[0] != collector:
                raise ResearchCoordinatorError("worker_plan relative_path 越出 collector")
            try:
                datetime.strptime(
                    str(artifact.get("artifact_time_utc")), "%Y-%m-%dT%H:%M:%SZ"
                )
            except ValueError as error:
                raise ResearchCoordinatorError("worker_plan artifact_time_utc 非法") from error
            size = artifact.get("size_bytes")
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                raise ResearchCoordinatorError("worker_plan artifact.size_bytes 非法")
            chunk_bytes += size
            roles = artifact.get("selection_roles")
            if (
                not isinstance(roles, list)
                or not roles
                or roles != sorted(set(roles))
                or not set(roles).issubset(allowed_roles)
            ):
                raise ResearchCoordinatorError("worker_plan selection_roles 非法")
        if chunk.get("artifact_ids") != chunk_ids:
            raise ResearchCoordinatorError("worker_plan chunk artifact_ids 不一致")
        if chunk.get("compressed_bytes") != chunk_bytes:
            raise ResearchCoordinatorError("worker_plan chunk compressed_bytes 不一致")
        if "rib" in chunk_artifact_types and chunk_artifact_types != ["rib"]:
            raise ResearchCoordinatorError("worker_plan 每个 RIB 必须独立分块")
        compressed_bytes += chunk_bytes
    if payload.get("execution_allowed") is True and not artifact_ids:
        raise ResearchCoordinatorError("可执行 worker_plan 不得没有输入制品")
    estimate = payload.get("resource_estimate")
    if not isinstance(estimate, Mapping):
        raise ResearchCoordinatorError("worker_plan.resource_estimate 缺失")
    if estimate.get("new_raw_read_bytes") != compressed_bytes:
        raise ResearchCoordinatorError("worker_plan 原始读取估算与制品总量不一致")
    if mode == BOUNDED_PILOT_MODE:
        remaining = payload.get("remaining_profile_interval")
        if (
            not isinstance(remaining, Mapping)
            or not payload.get("pilot_end_exclusive")
            or not payload.get("blocking_incomplete_reasons_zh")
        ):
            raise ResearchCoordinatorError("bounded pilot 缺少 remaining interval blocker")
    elif (
        payload.get("pilot_end_exclusive") is not None
        or payload.get("remaining_profile_interval") is not None
    ):
        raise ResearchCoordinatorError("full_profile 不得携带 pilot 剩余区间")
    return dict(worker_plan)


def prepare_research_plan(
    *,
    profile: Mapping[str, Any],
    artifact_manifest: Mapping[str, Any],
    manifest_verification: Mapping[str, Any],
    mapping_snapshot: Mapping[str, Any],
    code_sha256: str,
    output_root: str | os.PathLike[str],
    maximum_artifacts_per_chunk: int = DEFAULT_MAX_ARTIFACTS_PER_CHUNK,
    allow_existing_run: bool = False,
    pilot_end_exclusive: str | None = None,
    estimated_worker_seconds: float | None = None,
    estimated_temporary_bytes: int | None = None,
    protected_roots: Sequence[str] = (),
    production_roots: Sequence[str] = (),
) -> ResearchPlan:
    """只基于元数据生成 dry-run 计划；不会解析或打开任何原始 MRT。"""

    normalized_profile = validate_research_profile(profile)
    selection = resolve_research_inputs(
        artifact_manifest,
        manifest_verification,
        normalized_profile,
    )
    (
        execution_mode,
        normalized_pilot_end,
        unprocessed_interval,
        scope_complete,
        scope_failures,
    ) = _pilot_scope(normalized_profile, selection, pilot_end_exclusive)
    artifacts = _selected_artifacts(
        selection, pilot_end_exclusive=normalized_pilot_end
    )
    scoped_selection = _scoped_selection(
        selection,
        artifacts,
        normalized_profile,
        execution_mode=execution_mode,
        pilot_end_exclusive=normalized_pilot_end,
        scope_complete=scope_complete,
        scope_failures=scope_failures,
        unprocessed_interval=unprocessed_interval,
    )
    processing_hash = _sha256(code_sha256, "code_sha256")
    mapping_hash = _mapping_sha256(mapping_snapshot)
    selection_hash = _sha256(
        scoped_selection.get("semantic_fingerprint_sha256"),
        "input_selection.semantic_fingerprint_sha256",
    )
    profile_hash = profile_sha256(normalized_profile)
    run_id = research_run_id_v1(
        normalized_profile,
        input_manifest_sha256=selection_hash,
        mapping_sha256=mapping_hash,
        processing_sha256=processing_hash,
    )
    bindings = {
        "profile_sha256": profile_hash,
        "input_selection_sha256": selection_hash,
        "code_sha256": processing_hash,
        "mapping_sha256": mapping_hash,
    }
    requested_root = Path(output_root)
    _validate_directory(requested_root, "output_root")
    root = requested_root.resolve(strict=True)
    _validate_directory(root, "output_root")
    run_directory = root / run_id
    chunks = _chunks(artifacts, maximum_artifacts_per_chunk)
    limits = _effective_limits(normalized_profile)
    target = WriteTarget(
        label="research-run-directory",
        location=str(run_directory),
        kind="artifact",
    )
    if estimated_worker_seconds is None or estimated_temporary_bytes is None:
        raise ResearchCoordinatorError(
            "dry-run 必须显式提供 worker 时间与临时空间估算"
        )
    normalized_worker_estimate = _nonnegative_number(
        estimated_worker_seconds, "estimated_worker_seconds"
    )
    normalized_temporary_estimate = _nonnegative_integer(
        estimated_temporary_bytes, "estimated_temporary_bytes"
    )
    if normalized_worker_estimate <= 0 or normalized_temporary_estimate <= 0:
        raise ResearchCoordinatorError(
            "dry-run 的 worker 时间与临时空间估算必须大于零"
        )
    usage = ResourceUsage(
        new_raw_read_bytes=sum(int(item["size_bytes"]) for item in artifacts),
        process_runtime_seconds=normalized_worker_estimate,
        temporary_bytes=normalized_temporary_estimate,
        output_bytes=0,
        write_targets=(target,),
        phase="estimated",
    )
    gate = evaluate_resource_gate(
        usage,
        limits=limits,
        protected_roots=tuple(DEFAULT_PROTECTED_ROOTS)
        + tuple(str(Path(item).resolve(strict=False)) for item in protected_roots),
        production_roots=tuple(DEFAULT_PRODUCTION_ROOTS)
        + tuple(str(Path(item).resolve(strict=False)) for item in production_roots),
    )
    findings: list[str] = []
    if not scope_complete:
        findings.append("当前执行范围的 input selection 不完整，禁止执行。")
    run_exists = run_directory.exists() or run_directory.is_symlink()
    if run_exists and not allow_existing_run:
        findings.append("目标 run 目录已存在，拒绝覆盖。")
    if allow_existing_run and not run_exists:
        findings.append("恢复模式要求目标 run 目录已经存在。")
    if not gate.execution_allowed:
        findings.extend(
            str(item.get("message_zh", "资源或写入边界未通过。"))
            for item in gate.to_dict()["findings"]
        )
    ready = not findings
    acceptance_blockers = []
    if execution_mode == BOUNDED_PILOT_MODE:
        acceptance_blockers.append(
            "仅完成 bounded pilot；冻结 Profile 的剩余区间尚未处理。"
        )
    if scope_failures:
        acceptance_blockers.append(
            "当前执行范围仍有输入缺口：" + ",".join(scope_failures)
        )
    return ResearchPlan(
        profile=normalized_profile,
        input_selection=scoped_selection,
        mapping_snapshot=dict(mapping_snapshot),
        code_sha256=processing_hash,
        run_id=run_id,
        bindings=bindings,
        output_root=root,
        run_directory=run_directory,
        chunks=chunks,
        limits=limits,
        resource_gate=gate.to_dict(),
        ready=ready,
        findings_zh=tuple(findings),
        allow_existing_run=allow_existing_run,
        execution_mode=execution_mode,
        pilot_end_exclusive=normalized_pilot_end,
        unprocessed_profile_interval=unprocessed_interval,
        acceptance_blockers_zh=tuple(acceptance_blockers),
    )


def _initial_chain() -> str:
    return hashlib.sha256(SEMANTIC_CHAIN_SCHEMA).hexdigest()


def _advance_chain(previous: str, record: Mapping[str, Any]) -> str:
    _sha256(previous, "semantic_chain_sha256")
    record_hash = hashlib.sha256(
        canonical_json(dict(record)).encode("utf-8") + b"\n"
    ).digest()
    return hashlib.sha256(
        SEMANTIC_CHAIN_SCHEMA + bytes.fromhex(previous) + record_hash
    ).hexdigest()


def _ensure_subdirectories(root: Path) -> None:
    for name in ("chunks", "state", "checkpoints"):
        target = root / name
        if target.exists() or target.is_symlink():
            _validate_directory(target, f"run/{name}")
        else:
            os.mkdir(target, 0o750)


def _output_ref(artifact: PublishedArtifact, root: Path) -> dict[str, object]:
    return {
        "kind": artifact.kind,
        "path": artifact.path.relative_to(root).as_posix(),
        "sha256": artifact.sha256,
        "record_count": artifact.record_count,
    }


def _write_or_adopt_canonical_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    kind: str,
) -> PublishedArtifact:
    """发布内容寻址 JSON；崩溃重试只采用字节完全一致的 orphan。"""

    encoded = (canonical_json(dict(payload)) + "\n").encode("utf-8")
    expected_hash = hashlib.sha256(encoded).hexdigest()
    try:
        return write_canonical_json(path, payload, kind=kind)
    except FileExistsError:
        actual_hash, actual_size = _hash_regular(path)
        if actual_hash != expected_hash or actual_size != len(encoded):
            raise ResearchCoordinatorError(
                f"既有 orphan 与待发布 {kind} 内容不一致"
            )
        return PublishedArtifact(path, actual_hash, actual_size, 1, kind)


def _write_or_adopt_canonical_jsonl_gzip(
    path: Path,
    records: Sequence[Mapping[str, Any]],
    *,
    kind: str,
) -> PublishedArtifact:
    """发布确定性 gzip；重试时只读核验并采用完全相同的 orphan。"""

    try:
        return write_canonical_jsonl_gzip(path, records, kind=kind)
    except FileExistsError:
        buffer = io.BytesIO()
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=buffer,
            mtime=0,
        ) as compressed:
            for record in records:
                compressed.write(
                    (canonical_json(dict(record)) + "\n").encode("utf-8")
                )
        expected = buffer.getvalue()
        expected_hash = hashlib.sha256(expected).hexdigest()
        actual_hash, actual_size = _hash_regular(path)
        if actual_hash != expected_hash or actual_size != len(expected):
            raise ResearchCoordinatorError(
                "既有 research_records orphan 与确定性重算不一致"
            )
        return PublishedArtifact(
            path, actual_hash, actual_size, len(records), kind
        )


def _publish_run_state(
    root: Path, payload: Mapping[str, Any], sequence: int
) -> tuple[str, PublishedArtifact]:
    encoded = (canonical_json(dict(payload)) + "\n").encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    relative = f"run-state-{sequence:06d}-{digest}.json"
    artifact = write_canonical_json(
        root / relative, payload, kind="coordinator_run_state"
    )
    if artifact.sha256 != digest:
        raise ResearchCoordinatorError("run-state 预计算哈希与发布哈希不一致")
    return relative, artifact


def _execution_dict(
    *, raw_bytes: int, peak_temporary: int, max_worker: float, output_bytes: int,
    database_writes: int,
) -> dict[str, object]:
    return {
        "database_write_operations": database_writes,
        "new_raw_bytes_read": raw_bytes,
        "peak_temporary_bytes": peak_temporary,
        "max_worker_seconds": max_worker,
        "output_bytes": output_bytes,
    }


def _validate_outputs(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ResearchCoordinatorError("outputs 必须是数组")
    result: list[dict[str, object]] = []
    paths: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {
            "kind", "path", "sha256", "record_count"
        }:
            raise ResearchCoordinatorError(f"outputs[{index}] 字段不闭合")
        kind = item.get("kind")
        if not isinstance(kind, str) or _SAFE_KIND_RE.fullmatch(kind) is None:
            raise ResearchCoordinatorError("output.kind 非法")
        path = _safe_relative(item.get("path"), "output.path").as_posix()
        if path in paths:
            raise ResearchCoordinatorError("output.path 不得重复")
        paths.add(path)
        result.append(
            {
                "kind": kind,
                "path": path,
                "sha256": _sha256(item.get("sha256"), "output.sha256"),
                "record_count": _nonnegative_integer(
                    item.get("record_count"), "output.record_count"
                ),
            }
        )
    return result


def _publish_resume_checkpoint(
    *,
    root: Path,
    plan: ResearchPlan,
    sequence: int,
    artifact_id: str,
    next_artifact_index: int,
    next_record_ordinal: int,
    next_segment_index: int,
    execution: Mapping[str, Any],
    semantic_chain_sha256: str,
    outputs: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    state = {
        "schema_version": RESUME_STATE_SCHEMA_VERSION,
        "run_id": plan.run_id,
        "bindings": dict(plan.bindings),
        "next_artifact_index": next_artifact_index,
        "next_record_ordinal": next_record_ordinal,
        "next_segment_index": next_segment_index,
        "execution": dict(execution),
        "semantic_chain_sha256": semantic_chain_sha256,
        "outputs": [dict(item) for item in outputs],
    }
    state_digest = hashlib.sha256(
        (canonical_json(state) + "\n").encode("utf-8")
    ).hexdigest()
    state_relative = (
        f"state/resume-state-{sequence:06d}-{state_digest}.json"
    )
    state_artifact = _write_or_adopt_canonical_json(
        root / state_relative, state, kind="coordinator_resume_state"
    )
    checkpoint = build_checkpoint(
        run_id=plan.run_id,
        phase="bounded_execute",
        profile_sha256=plan.bindings["profile_sha256"],
        input_selection_sha256=plan.bindings["input_selection_sha256"],
        code_sha256=plan.bindings["code_sha256"],
        mapping_sha256=plan.bindings["mapping_sha256"],
        artifact_id=artifact_id,
        next_record_ordinal=next_record_ordinal,
        state_ref={"path": state_relative, "sha256": state_artifact.sha256},
        published_shards=outputs,
    )
    checkpoint_digest = hashlib.sha256(
        (canonical_json(checkpoint) + "\n").encode("utf-8")
    ).hexdigest()
    checkpoint_relative = (
        f"checkpoints/checkpoint-{sequence:06d}-{checkpoint_digest}.json"
    )
    checkpoint_artifact = _write_or_adopt_canonical_json(
        root / checkpoint_relative,
        checkpoint,
        kind="coordinator_checkpoint",
    )
    return {
        "path": checkpoint_relative,
        "sha256": checkpoint_artifact.sha256,
        "state_path": state_relative,
        "state_sha256": state_artifact.sha256,
    }


def _observed_gate(
    *,
    plan: ResearchPlan,
    run_root: Path,
    raw_bytes: int,
    worker_seconds: float,
    temporary_bytes: int,
    output_bytes: int,
) -> Mapping[str, Any]:
    return _observed_gate_for_limits(
        limits=plan.limits,
        run_root=run_root,
        raw_bytes=raw_bytes,
        worker_seconds=worker_seconds,
        temporary_bytes=temporary_bytes,
        output_bytes=output_bytes,
    )


def _observed_gate_for_limits(
    *,
    limits: ResourceLimits,
    run_root: Path,
    raw_bytes: int,
    worker_seconds: float,
    temporary_bytes: int,
    output_bytes: int,
) -> Mapping[str, Any]:
    """以冻结边界重建 observed gate，供内容寻址 run-state 复核。"""

    usage = ResourceUsage(
        new_raw_read_bytes=raw_bytes,
        process_runtime_seconds=worker_seconds,
        temporary_bytes=temporary_bytes,
        output_bytes=output_bytes,
        write_targets=(
            WriteTarget(
                label="research-run-directory",
                location=str(run_root),
                kind="artifact",
            ),
        ),
        phase="observed",
    )
    return evaluate_resource_gate(
        usage,
        limits=limits,
        protected_roots=DEFAULT_PROTECTED_ROOTS,
        production_roots=DEFAULT_PRODUCTION_ROOTS,
    ).to_dict()


def _run(
    *,
    plan: ResearchPlan,
    run_root: Path,
    executor: RecordExecutor,
    clock: Clock,
    attempt_id: int,
    state_sequence: int,
    previous_state_ref: Mapping[str, str] | None,
    next_artifact_index: int,
    next_record_ordinal: int,
    next_segment_index: int,
    outputs: list[dict[str, object]],
    raw_bytes: int,
    peak_temporary: int,
    max_worker: float,
    output_bytes: int,
    database_writes: int,
    semantic_chain: str,
    attempt_started: float | None = None,
) -> tuple[dict[str, Any], str, PublishedArtifact]:
    if not callable(executor):
        raise ResearchCoordinatorError("executor 必须可调用")
    flat = plan.flat_artifacts
    if next_artifact_index < 0 or next_artifact_index > len(flat):
        raise ResearchCoordinatorError("next_artifact_index 越界")
    if attempt_started is None:
        attempt_started = _nonnegative_number(clock(), "clock")
    else:
        attempt_started = _nonnegative_number(
            attempt_started, "attempt_started"
        )
    buffer: list[Mapping[str, Any]] = []
    active_chunk: int | None = None
    last_artifact_id: str | None = None

    def flush_buffer(chunk_index: int) -> None:
        nonlocal next_segment_index, output_bytes, buffer
        if not buffer:
            return
        relative = (
            f"chunks/chunk-{chunk_index:05d}-"
            f"segment-{next_segment_index:06d}.jsonl.gz"
        )
        artifact = _write_or_adopt_canonical_jsonl_gzip(
            run_root / relative,
            tuple(buffer),
            kind="research_records",
        )
        outputs.append(_output_ref(artifact, run_root))
        output_bytes += artifact.size_bytes
        next_segment_index += 1
        buffer = []

    status = "completed"
    stop_gate: Mapping[str, Any] | None = None
    stop_artifact_id: str | None = None

    while next_artifact_index < len(flat):
        chunk_index, artifact = flat[next_artifact_index]
        artifact_id = str(artifact["artifact_id"])
        last_artifact_id = artifact_id
        if active_chunk is not None and chunk_index != active_chunk:
            flush_buffer(active_chunk)
        active_chunk = chunk_index
        try:
            records = iter(executor(artifact, next_record_ordinal))
        except Exception as error:
            raise ResearchCoordinatorError("executor 无法创建 record 流") from error
        expected_ordinal = next_record_ordinal
        artifact_complete = False
        while not artifact_complete:
            elapsed = _nonnegative_number(clock(), "clock") - attempt_started
            if elapsed < 0:
                raise ResearchCoordinatorError("clock 不得倒退")
            max_worker = max(max_worker, elapsed)
            gate = _observed_gate(
                plan=plan,
                run_root=run_root,
                raw_bytes=raw_bytes,
                worker_seconds=elapsed,
                temporary_bytes=peak_temporary,
                output_bytes=output_bytes,
            )
            if not gate["execution_allowed"]:
                status = "paused" if gate["decision"] == "soft_stop" else "blocked"
                stop_gate = gate
                stop_artifact_id = artifact_id
                break
            try:
                record = next(records)
            except StopIteration:
                artifact_complete = True
                next_artifact_index += 1
                next_record_ordinal = 0
                break
            except Exception as error:
                raise ResearchCoordinatorError("executor 在 record 边界前失败") from error
            if not isinstance(record, ExecutionRecord):
                raise ResearchCoordinatorError("executor 必须逐条返回 ExecutionRecord")
            if record.artifact_id != artifact_id:
                raise ResearchCoordinatorError("ExecutionRecord 越出当前 artifact")
            if record.record_ordinal != expected_ordinal:
                raise ResearchCoordinatorError("record_ordinal 必须连续且从恢复边界开始")
            expected_ordinal += 1
            next_record_ordinal = expected_ordinal
            if record.database_write_operations != 0:
                database_writes += record.database_write_operations
                status = "blocked"
                stop_artifact_id = artifact_id
                stop_gate = {
                    "decision": "forbidden",
                    "execution_allowed": False,
                    "checkpoint_required": True,
                    "approval_required": True,
                    "findings": [
                        {
                            "code": "database_write_operation_detected",
                            "category": "write_boundary",
                            "message_zh": "注入 executor 报告了数据库写操作，立即阻断。",
                        }
                    ],
                }
                break
            raw_bytes += record.new_raw_bytes_read
            peak_temporary = max(peak_temporary, record.temporary_bytes)
            buffer.append(dict(record.output_record))
            semantic_chain = _advance_chain(semantic_chain, record.output_record)
            elapsed = _nonnegative_number(clock(), "clock") - attempt_started
            if elapsed < 0:
                raise ResearchCoordinatorError("clock 不得倒退")
            max_worker = max(max_worker, elapsed)
            gate = _observed_gate(
                plan=plan,
                run_root=run_root,
                raw_bytes=raw_bytes,
                worker_seconds=elapsed,
                temporary_bytes=peak_temporary,
                output_bytes=output_bytes,
            )
            if not gate["execution_allowed"]:
                status = "paused" if gate["decision"] == "soft_stop" else "blocked"
                stop_gate = gate
                stop_artifact_id = artifact_id
                break
        if status != "completed":
            break
    if active_chunk is not None:
        flush_buffer(active_chunk)

    final_elapsed = _nonnegative_number(clock(), "clock") - attempt_started
    if final_elapsed < 0:
        raise ResearchCoordinatorError("clock 不得倒退")
    max_worker = max(max_worker, final_elapsed)
    final_gate = _observed_gate(
        plan=plan,
        run_root=run_root,
        raw_bytes=raw_bytes,
        worker_seconds=final_elapsed,
        temporary_bytes=peak_temporary,
        output_bytes=output_bytes,
    )
    if status != "blocked" and not final_gate["execution_allowed"]:
        if last_artifact_id is None:
            raise ResearchCoordinatorError("最终资源门缺少 artifact record 边界")
        status = "paused" if final_gate["decision"] == "soft_stop" else "blocked"
        stop_gate = final_gate
        stop_artifact_id = last_artifact_id

    execution = _execution_dict(
        raw_bytes=raw_bytes,
        peak_temporary=peak_temporary,
        max_worker=max_worker,
        output_bytes=output_bytes,
        database_writes=database_writes,
    )
    checkpoint_ref: Mapping[str, str] | None = None
    if status in {"paused", "blocked"}:
        if stop_artifact_id is None:
            raise ResearchCoordinatorError("停止状态缺少 artifact record 边界")
        checkpoint_ref = _publish_resume_checkpoint(
            root=run_root,
            plan=plan,
            sequence=state_sequence,
            artifact_id=stop_artifact_id,
            next_artifact_index=next_artifact_index,
            next_record_ordinal=next_record_ordinal,
            next_segment_index=next_segment_index,
            execution=execution,
            semantic_chain_sha256=semantic_chain,
            outputs=outputs,
        )
    settled_elapsed = _nonnegative_number(clock(), "clock") - attempt_started
    if settled_elapsed < 0:
        raise ResearchCoordinatorError("clock 不得倒退")
    max_worker = max(max_worker, settled_elapsed)
    settled_gate = _observed_gate(
        plan=plan,
        run_root=run_root,
        raw_bytes=raw_bytes,
        worker_seconds=settled_elapsed,
        temporary_bytes=peak_temporary,
        output_bytes=output_bytes,
    )
    if status != "blocked" and not settled_gate["execution_allowed"]:
        status = (
            "paused" if settled_gate["decision"] == "soft_stop" else "blocked"
        )
        stop_gate = settled_gate
        if stop_artifact_id is None:
            stop_artifact_id = last_artifact_id
    execution = _execution_dict(
        raw_bytes=raw_bytes,
        peak_temporary=peak_temporary,
        max_worker=max_worker,
        output_bytes=output_bytes,
        database_writes=database_writes,
    )
    if status in {"paused", "blocked"} and checkpoint_ref is None:
        if stop_artifact_id is None:
            raise ResearchCoordinatorError("收尾停止状态缺少 artifact record 边界")
        checkpoint_ref = _publish_resume_checkpoint(
            root=run_root,
            plan=plan,
            sequence=state_sequence,
            artifact_id=stop_artifact_id,
            next_artifact_index=next_artifact_index,
            next_record_ordinal=next_record_ordinal,
            next_segment_index=next_segment_index,
            execution=execution,
            semantic_chain_sha256=semantic_chain,
            outputs=outputs,
        )
        settled_elapsed = _nonnegative_number(clock(), "clock") - attempt_started
        if settled_elapsed < 0:
            raise ResearchCoordinatorError("clock 不得倒退")
        max_worker = max(max_worker, settled_elapsed)
        settled_gate = _observed_gate(
            plan=plan,
            run_root=run_root,
            raw_bytes=raw_bytes,
            worker_seconds=settled_elapsed,
            temporary_bytes=peak_temporary,
            output_bytes=output_bytes,
        )
        if status != "blocked" and settled_gate["decision"] != "allowed":
            status = (
                "paused"
                if settled_gate["decision"] == "soft_stop"
                else "blocked"
            )
            stop_gate = settled_gate
        execution = _execution_dict(
            raw_bytes=raw_bytes,
            peak_temporary=peak_temporary,
            max_worker=max_worker,
            output_bytes=output_bytes,
            database_writes=database_writes,
        )
    observed_resource_gate = _observed_gate_for_limits(
        limits=plan.limits,
        run_root=run_root,
        raw_bytes=raw_bytes,
        worker_seconds=max_worker,
        temporary_bytes=peak_temporary,
        output_bytes=output_bytes,
    )
    def build_state_payload(
        sequence: int,
        previous: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        incomplete_reasons = list(plan.acceptance_blockers_zh)
        if status == "paused":
            incomplete_reasons.append(
                "worker 达到软停边界，已在完整 record 后暂停。"
            )
        elif status == "blocked":
            incomplete_reasons.append(
                "执行触发硬资源或禁止写入边界，已阻断；运行时间按下界记录。"
            )
        return {
            "schema_version": RUN_STATE_SCHEMA_VERSION,
            "run_id": plan.run_id,
            "status": status,
            "execution_mode": plan.execution_mode,
            "research_run_state": (
                "incomplete"
                if status != "completed"
                or plan.execution_mode == BOUNDED_PILOT_MODE
                else "completed"
            ),
            "acceptance_state": (
                "not_accepted"
                if status != "completed"
                or plan.execution_mode == BOUNDED_PILOT_MODE
                else "pending"
            ),
            "unprocessed_profile_interval": (
                dict(plan.unprocessed_profile_interval)
                if plan.unprocessed_profile_interval
                else None
            ),
            "blocking_incomplete_reasons_zh": incomplete_reasons,
            "attempt_id": attempt_id,
            "state_sequence": sequence,
            "previous_state_ref": dict(previous) if previous is not None else None,
            "runtime_evidence_kind": (
                "lower_bound" if status == "blocked" else "pre_publish_observed"
            ),
            "bindings": dict(plan.bindings),
            "input_selection_id": plan.input_selection["selection_id"],
            "resource_limits": plan.limits.to_dict(),
            "execution": execution,
            "observed_resource_gate": observed_resource_gate,
            "outputs": outputs,
            "semantic_fingerprint_sha256": semantic_chain,
            "checkpoint_ref": dict(checkpoint_ref) if checkpoint_ref else None,
            "resource_stop": dict(stop_gate) if stop_gate else None,
        }

    latest_sequence = state_sequence
    state = build_state_payload(latest_sequence, previous_state_ref)
    relative, published = _publish_run_state(run_root, state, latest_sequence)

    # run-state 本身也属于同一进程的收尾工作。candidate 落盘后若门禁从
    # allowed 升到 soft，或从 allowed/soft 升到 hard，则只追加 correction
    # state；旧 state 永不覆盖。blocked 记录的是已观测运行时间下界，写入
    # blocked state 后无需为描述自身耗时而无限递归。
    post_state_elapsed = _nonnegative_number(clock(), "clock") - attempt_started
    if post_state_elapsed < 0:
        raise ResearchCoordinatorError("clock 不得倒退")
    post_state_gate = _observed_gate(
        plan=plan,
        run_root=run_root,
        raw_bytes=raw_bytes,
        worker_seconds=post_state_elapsed,
        temporary_bytes=peak_temporary,
        output_bytes=output_bytes,
    )
    severity = {"completed": 0, "paused": 1, "blocked": 2}
    gate_severity = (
        0
        if post_state_gate["decision"] == "allowed"
        else 1
        if post_state_gate["decision"] == "soft_stop"
        else 2
    )
    if gate_severity > severity[status]:
        max_worker = max(max_worker, post_state_elapsed)
        status = "paused" if gate_severity == 1 else "blocked"
        stop_gate = post_state_gate
        if stop_artifact_id is None:
            stop_artifact_id = last_artifact_id
        execution = _execution_dict(
            raw_bytes=raw_bytes,
            peak_temporary=peak_temporary,
            max_worker=max_worker,
            output_bytes=output_bytes,
            database_writes=database_writes,
        )
        if checkpoint_ref is None:
            if stop_artifact_id is None:
                raise ResearchCoordinatorError("状态升级缺少 EOF checkpoint 身份")
            checkpoint_ref = _publish_resume_checkpoint(
                root=run_root,
                plan=plan,
                sequence=latest_sequence + 1,
                artifact_id=stop_artifact_id,
                next_artifact_index=next_artifact_index,
                next_record_ordinal=next_record_ordinal,
                next_segment_index=next_segment_index,
                execution=execution,
                semantic_chain_sha256=semantic_chain,
                outputs=outputs,
            )
            checkpoint_elapsed = (
                _nonnegative_number(clock(), "clock") - attempt_started
            )
            if checkpoint_elapsed < 0:
                raise ResearchCoordinatorError("clock 不得倒退")
            checkpoint_gate = _observed_gate(
                plan=plan,
                run_root=run_root,
                raw_bytes=raw_bytes,
                worker_seconds=checkpoint_elapsed,
                temporary_bytes=peak_temporary,
                output_bytes=output_bytes,
            )
            max_worker = max(max_worker, checkpoint_elapsed)
            if checkpoint_gate["decision"] not in {"allowed", "soft_stop"}:
                status = "blocked"
                stop_gate = checkpoint_gate
            execution = _execution_dict(
                raw_bytes=raw_bytes,
                peak_temporary=peak_temporary,
                max_worker=max_worker,
                output_bytes=output_bytes,
                database_writes=database_writes,
            )
        observed_resource_gate = _observed_gate_for_limits(
            limits=plan.limits,
            run_root=run_root,
            raw_bytes=raw_bytes,
            worker_seconds=max_worker,
            temporary_bytes=peak_temporary,
            output_bytes=output_bytes,
        )
        previous = {"path": relative, "sha256": published.sha256}
        latest_sequence += 1
        state = build_state_payload(latest_sequence, previous)
        relative, published = _publish_run_state(
            run_root, state, latest_sequence
        )
        if status == "paused":
            correction_elapsed = (
                _nonnegative_number(clock(), "clock") - attempt_started
            )
            if correction_elapsed < 0:
                raise ResearchCoordinatorError("clock 不得倒退")
            correction_gate = _observed_gate(
                plan=plan,
                run_root=run_root,
                raw_bytes=raw_bytes,
                worker_seconds=correction_elapsed,
                temporary_bytes=peak_temporary,
                output_bytes=output_bytes,
            )
            if correction_gate["decision"] not in {"allowed", "soft_stop"}:
                status = "blocked"
                stop_gate = correction_gate
                max_worker = max(max_worker, correction_elapsed)
                execution = _execution_dict(
                    raw_bytes=raw_bytes,
                    peak_temporary=peak_temporary,
                    max_worker=max_worker,
                    output_bytes=output_bytes,
                    database_writes=database_writes,
                )
                observed_resource_gate = _observed_gate_for_limits(
                    limits=plan.limits,
                    run_root=run_root,
                    raw_bytes=raw_bytes,
                    worker_seconds=max_worker,
                    temporary_bytes=peak_temporary,
                    output_bytes=output_bytes,
                )
                previous = {"path": relative, "sha256": published.sha256}
                latest_sequence += 1
                state = build_state_payload(latest_sequence, previous)
                relative, published = _publish_run_state(
                    run_root, state, latest_sequence
                )
    return state, relative, published


def _publish_staging(staging: Path, final: Path) -> None:
    if final.exists() or final.is_symlink():
        raise FileExistsError(f"研究 run 目录已存在，拒绝覆盖：{final}")
    os.rename(staging, final)
    descriptor = os.open(final.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def execute_research(
    plan: ResearchPlan,
    executor: RecordExecutor,
    *,
    clock: Clock = time.monotonic,
) -> CoordinatorRunResult:
    """从零执行一个计划；目标 run 目录已存在时绝不覆盖。"""

    if not isinstance(plan, ResearchPlan) or not plan.ready or plan.allow_existing_run:
        raise ResearchCoordinatorError("execute 只接受 ready 的新运行计划")
    _assert_plan_effective_limits(plan)
    if plan.run_directory.exists() or plan.run_directory.is_symlink():
        raise FileExistsError(f"研究 run 目录已存在，拒绝覆盖：{plan.run_directory}")
    attempt_started = _nonnegative_number(clock(), "clock")
    staging = plan.output_root / (
        f".{plan.run_id}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    os.mkdir(staging, 0o750)
    _ensure_subdirectories(staging)
    state, relative, published = _run(
        plan=plan,
        run_root=staging,
        executor=executor,
        clock=clock,
        attempt_id=1,
        state_sequence=1,
        previous_state_ref=None,
        next_artifact_index=0,
        next_record_ordinal=0,
        next_segment_index=0,
        outputs=[],
        raw_bytes=0,
        peak_temporary=0,
        max_worker=0,
        output_bytes=0,
        database_writes=0,
        semantic_chain=_initial_chain(),
        attempt_started=attempt_started,
    )
    publish_elapsed = _nonnegative_number(clock(), "clock") - attempt_started
    if publish_elapsed < 0:
        raise ResearchCoordinatorError("clock 不得倒退")
    publish_gate = _observed_gate(
        plan=plan,
        run_root=staging,
        raw_bytes=int(state["execution"]["new_raw_bytes_read"]),
        worker_seconds=publish_elapsed,
        temporary_bytes=int(state["execution"]["peak_temporary_bytes"]),
        output_bytes=int(state["execution"]["output_bytes"]),
    )
    state_severity = {"completed": 0, "paused": 1, "blocked": 2}[state["status"]]
    publish_severity = (
        0
        if publish_gate["decision"] == "allowed"
        else 1
        if publish_gate["decision"] == "soft_stop"
        else 2
    )
    if publish_severity > state_severity:
        raise ResearchCoordinatorError(
            "staging 发布前累计运行时或资源门禁拒绝继续："
            + str(publish_gate["decision"])
        )
    _publish_staging(staging, plan.run_directory)
    return CoordinatorRunResult(
        run_directory=plan.run_directory,
        run_state=state,
        run_state_relative_path=relative,
        run_state_sha256=published.sha256,
    )


def _read_json_regular(path: Path, maximum_bytes: int) -> tuple[dict[str, Any], str]:
    payload_bytes, digest = _read_regular_bytes(
        path, maximum_bytes=maximum_bytes
    )
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ResearchCoordinatorError("研究 JSON 制品损坏") from error
    if not isinstance(payload, Mapping):
        raise ResearchCoordinatorError("研究 JSON 制品根节点必须是对象")
    return dict(payload), digest


def _latest_run_state(run_directory: Path) -> tuple[Path, dict[str, Any], str, int]:
    candidates: list[tuple[int, Path, str]] = []
    seen_sequences: set[int] = set()
    for child in run_directory.iterdir():
        matched = _RUN_STATE_NAME_RE.fullmatch(child.name)
        if matched:
            sequence = int(matched.group(1))
            if sequence in seen_sequences:
                raise ResearchCoordinatorError("run 目录存在重复 state_sequence")
            seen_sequences.add(sequence)
            candidates.append((sequence, child, matched.group(2)))
    if not candidates:
        raise ResearchCoordinatorError("run 目录缺少内容寻址 run-state")
    sequence, path, filename_hash = max(candidates, key=lambda item: item[0])
    payload, actual_hash = _read_json_regular(path, 64 * 1024 * 1024)
    if actual_hash != filename_hash:
        raise ResearchCoordinatorError("run-state 文件名哈希与内容不一致")
    return path, payload, actual_hash, sequence


def _verify_run_state_chain(
    root: Path,
    *,
    path: Path,
    state: Mapping[str, Any],
    state_hash: str,
    sequence: int,
) -> None:
    """核验 append-only run-state 的连续前驱哈希链。"""

    current_path = path
    current_state = dict(state)
    current_hash = state_hash
    current_sequence = sequence
    run_id = current_state.get("run_id")
    bindings = current_state.get("bindings")
    while True:
        if current_state.get("state_sequence") != current_sequence:
            raise ResearchCoordinatorError("run-state state_sequence 与文件名不一致")
        if current_state.get("run_id") != run_id or current_state.get("bindings") != bindings:
            raise ResearchCoordinatorError("run-state 前驱链身份漂移")
        previous = current_state.get("previous_state_ref")
        if current_sequence == 1:
            if previous is not None:
                raise ResearchCoordinatorError("首个 run-state 不得引用前驱")
            return
        if not isinstance(previous, Mapping) or set(previous) != {"path", "sha256"}:
            raise ResearchCoordinatorError("run-state 前驱引用不闭合")
        relative = _safe_relative(previous["path"], "previous_state_ref.path")
        if len(relative.parts) != 1:
            raise ResearchCoordinatorError("run-state 前驱必须位于 run 根目录")
        matched = _RUN_STATE_NAME_RE.fullmatch(relative.name)
        if matched is None or int(matched.group(1)) != current_sequence - 1:
            raise ResearchCoordinatorError("run-state 前驱序号不连续")
        previous_path = root / relative.name
        previous_state, previous_hash = _read_json_regular(
            previous_path, 64 * 1024 * 1024
        )
        expected_hash = _sha256(
            previous["sha256"], "previous_state_ref.sha256"
        )
        if previous_hash != expected_hash or matched.group(2) != expected_hash:
            raise ResearchCoordinatorError("run-state 前驱哈希不一致")
        if previous_path == current_path or previous_hash == current_hash:
            raise ResearchCoordinatorError("run-state 前驱链形成自引用")
        current_path = previous_path
        current_state = previous_state
        current_hash = previous_hash
        current_sequence -= 1


def _iter_jsonl_gzip(
    path: Path, *, maximum_uncompressed_bytes: int
) -> Iterator[Mapping[str, Any]]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    total = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ResearchCoordinatorError("JSONL gzip 不是普通文件")
        with os.fdopen(descriptor, "rb", closefd=False) as raw:
            with gzip.GzipFile(fileobj=raw, mode="rb") as compressed:
                for line_number, line in enumerate(compressed, start=1):
                    total += len(line)
                    if total >= maximum_uncompressed_bytes:
                        raise ResearchCoordinatorError("研究输出解压量达到临时空间硬边界")
                    if not line.endswith(b"\n"):
                        raise ResearchCoordinatorError("JSONL 记录缺少换行边界")
                    try:
                        value = json.loads(line)
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise ResearchCoordinatorError(
                            f"JSONL 第 {line_number} 条记录损坏"
                        ) from error
                    if not isinstance(value, Mapping):
                        raise ResearchCoordinatorError("JSONL 记录必须是对象")
                    yield value
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, name) != getattr(after, name) for name in identity):
            raise ResearchCoordinatorError("JSONL gzip 在校验期间发生变化")
    except (OSError, EOFError) as error:
        raise ResearchCoordinatorError("JSONL gzip EOF/CRC 校验失败") from error
    finally:
        os.close(descriptor)


def verify_research_run(
    run_directory: str | os.PathLike[str],
    *,
    expected_bindings: Mapping[str, str] | None = None,
) -> VerificationResult:
    """校验内容哈希、引用闭环、检查点绑定和语义指纹。"""

    root = Path(run_directory)
    _validate_directory(root, "run_directory")
    state_path, state, state_hash, sequence = _latest_run_state(root)
    required = {
        "schema_version",
        "run_id",
        "status",
        "execution_mode",
        "research_run_state",
        "acceptance_state",
        "unprocessed_profile_interval",
        "blocking_incomplete_reasons_zh",
        "attempt_id",
        "state_sequence",
        "previous_state_ref",
        "runtime_evidence_kind",
        "bindings",
        "input_selection_id",
        "resource_limits",
        "execution",
        "observed_resource_gate",
        "outputs",
        "semantic_fingerprint_sha256",
        "checkpoint_ref",
        "resource_stop",
    }
    if set(state) != required or state.get("schema_version") != RUN_STATE_SCHEMA_VERSION:
        raise ResearchCoordinatorError("run-state 字段或 schema_version 不闭合")
    run_id = state.get("run_id")
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise ResearchCoordinatorError("run-state.run_id 非法")
    if root.name != run_id:
        raise ResearchCoordinatorError("run 目录名与 run_id 不一致")
    if _nonnegative_integer(state.get("attempt_id"), "run-state.attempt_id") == 0:
        raise ResearchCoordinatorError("run-state attempt_id 必须大于零")
    if state.get("state_sequence") != sequence:
        raise ResearchCoordinatorError("run-state state_sequence 与文件名不一致")
    _verify_run_state_chain(
        root,
        path=state_path,
        state=state,
        state_hash=state_hash,
        sequence=sequence,
    )
    status = state.get("status")
    if status not in {"completed", "paused", "blocked"}:
        raise ResearchCoordinatorError("run-state.status 非法")
    expected_runtime_kind = (
        "lower_bound" if status == "blocked" else "pre_publish_observed"
    )
    if state.get("runtime_evidence_kind") != expected_runtime_kind:
        raise ResearchCoordinatorError("run-state runtime_evidence_kind 与状态不一致")
    execution_mode = state.get("execution_mode")
    normalized_execution_mode = normalize_execution_mode(execution_mode)
    research_run_state = state.get("research_run_state")
    acceptance_state = state.get("acceptance_state")
    if normalized_execution_mode == BOUNDED_PILOT_MODE:
        if (
            research_run_state != "incomplete"
            or acceptance_state != "not_accepted"
            or not isinstance(state.get("unprocessed_profile_interval"), Mapping)
            or not state.get("blocking_incomplete_reasons_zh")
        ):
            raise ResearchCoordinatorError(
                "bounded pilot 必须明确保持 incomplete/not_accepted 与未处理区间"
            )
    elif status == "completed":
        if research_run_state != "completed" or acceptance_state != "pending":
            raise ResearchCoordinatorError("完整执行状态与验收状态不一致")
    elif research_run_state != "incomplete" or acceptance_state != "not_accepted":
        raise ResearchCoordinatorError("暂停或阻断 run 必须 incomplete/not_accepted")
    if research_run_state == "incomplete" and not state.get(
        "blocking_incomplete_reasons_zh"
    ):
        raise ResearchCoordinatorError("incomplete run 必须给出阻断原因")
    bindings = state.get("bindings")
    binding_fields = {
        "profile_sha256",
        "input_selection_sha256",
        "code_sha256",
        "mapping_sha256",
    }
    if not isinstance(bindings, Mapping) or set(bindings) != binding_fields:
        raise ResearchCoordinatorError("run-state bindings 不闭合")
    normalized_bindings = {
        field: _sha256(bindings[field], f"bindings.{field}")
        for field in sorted(binding_fields)
    }
    if expected_bindings is not None:
        if set(expected_bindings) != binding_fields:
            raise ResearchCoordinatorError("expected_bindings 字段不闭合")
        for field in sorted(binding_fields):
            if _sha256(expected_bindings[field], f"expected_bindings.{field}") != normalized_bindings[field]:
                raise ResearchCoordinatorError(f"run {field} 绑定不一致")

    try:
        limits = ResourceLimits.from_profile(state.get("resource_limits"))
    except (TypeError, ValueError) as error:
        raise ResearchCoordinatorError("run-state resource_limits 非法") from error
    if (
        limits.max_new_raw_read_bytes > DEFAULT_MAX_NEW_RAW_READ_BYTES
        or limits.max_temporary_bytes > DEFAULT_MAX_TEMPORARY_BYTES
        or limits.max_worker_runtime_seconds > DEFAULT_HARD_RUNTIME_SECONDS
        or limits.worker_soft_stop_seconds > DEFAULT_SOFT_RUNTIME_SECONDS
    ):
        raise ResearchCoordinatorError("run-state resource_limits 放宽了全局边界")

    execution = state.get("execution")
    execution_fields = {
        "database_write_operations",
        "new_raw_bytes_read",
        "peak_temporary_bytes",
        "max_worker_seconds",
        "output_bytes",
    }
    if not isinstance(execution, Mapping) or set(execution) != execution_fields:
        raise ResearchCoordinatorError("run-state execution 字段不闭合")
    database_writes = _nonnegative_integer(
        execution["database_write_operations"],
        "execution.database_write_operations",
    )
    raw_bytes = _nonnegative_integer(
        execution["new_raw_bytes_read"], "execution.new_raw_bytes_read"
    )
    temporary_bytes = _nonnegative_integer(
        execution["peak_temporary_bytes"], "execution.peak_temporary_bytes"
    )
    worker_seconds = _nonnegative_number(
        execution["max_worker_seconds"], "execution.max_worker_seconds"
    )
    output_bytes = _nonnegative_integer(
        execution["output_bytes"], "execution.output_bytes"
    )
    expected_observed_gate = _observed_gate_for_limits(
        limits=limits,
        run_root=root,
        raw_bytes=raw_bytes,
        worker_seconds=worker_seconds,
        temporary_bytes=temporary_bytes,
        output_bytes=output_bytes,
    )
    if state.get("observed_resource_gate") != expected_observed_gate:
        raise ResearchCoordinatorError("run-state observed_resource_gate 无法复核")
    hard_limit_reached = (
        database_writes != 0
        or raw_bytes >= limits.max_new_raw_read_bytes
        or temporary_bytes >= limits.max_temporary_bytes
        or worker_seconds >= limits.max_worker_runtime_seconds
    )
    if hard_limit_reached and status != "blocked":
        raise ResearchCoordinatorError("达到硬资源/数据库边界的 run 必须标记 blocked")
    if status == "paused" and not (
        database_writes == 0
        and raw_bytes < limits.max_new_raw_read_bytes
        and temporary_bytes < limits.max_temporary_bytes
        and limits.worker_soft_stop_seconds
        <= worker_seconds
        < limits.max_worker_runtime_seconds
    ):
        raise ResearchCoordinatorError("paused run 不符合 540 秒软停语义")
    if status == "completed" and (
        database_writes != 0
        or raw_bytes >= limits.max_new_raw_read_bytes
        or temporary_bytes >= limits.max_temporary_bytes
        or worker_seconds >= limits.max_worker_runtime_seconds
    ):
        raise ResearchCoordinatorError("completed run 超出允许资源边界")
    resource_stop = state.get("resource_stop")
    if status == "completed" and resource_stop is not None:
        raise ResearchCoordinatorError("completed run 不得有 resource_stop")
    if status != "completed" and not isinstance(resource_stop, Mapping):
        raise ResearchCoordinatorError("暂停或阻断 run 必须保留 resource_stop")

    outputs = _validate_outputs(state.get("outputs"))
    chain = _initial_chain()
    record_count = 0
    for item in outputs:
        relative = _safe_relative(item["path"], "output.path")
        path = root.joinpath(*relative.parts)
        actual_hash, _size = _hash_regular(path)
        if actual_hash != item["sha256"]:
            raise ResearchCoordinatorError(f"输出哈希不一致：{item['path']}")
        count = 0
        if item["kind"] == "research_records":
            for record in _iter_jsonl_gzip(
                path, maximum_uncompressed_bytes=DEFAULT_MAX_TEMPORARY_BYTES
            ):
                chain = _advance_chain(chain, record)
                count += 1
        if count != item["record_count"]:
            raise ResearchCoordinatorError(f"输出记录数不一致：{item['path']}")
        record_count += count
    expected_chain = _sha256(
        state.get("semantic_fingerprint_sha256"),
        "semantic_fingerprint_sha256",
    )
    if chain != expected_chain:
        raise ResearchCoordinatorError("输出语义指纹不一致")

    checkpoint_ref = state.get("checkpoint_ref")
    if status == "completed":
        if checkpoint_ref is not None:
            raise ResearchCoordinatorError("completed run 不得保留活动检查点")
    else:
        if not isinstance(checkpoint_ref, Mapping) or set(checkpoint_ref) != {
            "path", "sha256", "state_path", "state_sha256"
        }:
            raise ResearchCoordinatorError("暂停或阻断 run 缺少检查点引用")
        checkpoint_path = root.joinpath(
            *_safe_relative(checkpoint_ref["path"], "checkpoint_ref.path").parts
        )
        checkpoint, checkpoint_hash = _read_json_regular(
            checkpoint_path, 64 * 1024 * 1024
        )
        if checkpoint_hash != _sha256(
            checkpoint_ref["sha256"], "checkpoint_ref.sha256"
        ):
            raise ResearchCoordinatorError("checkpoint 哈希不一致")
        verified_checkpoint = verify_checkpoint(
            checkpoint, expected_bindings=normalized_bindings
        )
        if verified_checkpoint["run_id"] != run_id:
            raise ResearchCoordinatorError("checkpoint.run_id 引用不闭合")
        state_relative = _safe_relative(
            checkpoint_ref["state_path"], "checkpoint_ref.state_path"
        )
        state_artifact_path = root.joinpath(*state_relative.parts)
        state_artifact, state_artifact_hash = _read_json_regular(
            state_artifact_path, 256 * 1024 * 1024
        )
        if state_artifact_hash != _sha256(
            checkpoint_ref["state_sha256"], "checkpoint_ref.state_sha256"
        ):
            raise ResearchCoordinatorError("resume-state 哈希不一致")
        if verified_checkpoint["state_ref"] != {
            "path": state_relative.as_posix(),
            "sha256": state_artifact_hash,
        }:
            raise ResearchCoordinatorError("checkpoint state_ref 引用不闭合")
        if state_artifact.get("schema_version") != RESUME_STATE_SCHEMA_VERSION:
            raise ResearchCoordinatorError("resume-state schema_version 非法")
        if state_artifact.get("run_id") != run_id or state_artifact.get("bindings") != normalized_bindings:
            raise ResearchCoordinatorError("resume-state 身份绑定不一致")
        resume_execution = state_artifact.get("execution")
        if not isinstance(resume_execution, Mapping) or set(resume_execution) != execution_fields:
            raise ResearchCoordinatorError("resume-state execution 字段不闭合")
        for field in (
            "database_write_operations",
            "new_raw_bytes_read",
            "peak_temporary_bytes",
            "output_bytes",
        ):
            if resume_execution.get(field) != execution.get(field):
                raise ResearchCoordinatorError(
                    "resume-state execution 与 latest run-state 不一致"
                )
        resume_worker_seconds = _nonnegative_number(
            resume_execution.get("max_worker_seconds"),
            "resume-state.execution.max_worker_seconds",
        )
        if resume_worker_seconds > worker_seconds:
            raise ResearchCoordinatorError(
                "resume-state worker 时间不得超过 latest run-state"
            )
        if _validate_outputs(state_artifact.get("outputs")) != outputs:
            raise ResearchCoordinatorError("resume-state outputs 与 run-state 不一致")
        if state_artifact.get("semantic_chain_sha256") != expected_chain:
            raise ResearchCoordinatorError("resume-state 语义指纹不一致")
        if verified_checkpoint["published_shards"] != outputs:
            raise ResearchCoordinatorError("checkpoint published_shards 引用不闭合")

    return VerificationResult(
        run_directory=root,
        run_id=run_id,
        status=status,
        bindings=normalized_bindings,
        semantic_fingerprint_sha256=expected_chain,
        output_count=len(outputs),
        record_count=record_count,
        run_state_relative_path=state_path.name,
    )


def resume_research(
    plan: ResearchPlan,
    executor: RecordExecutor,
    *,
    clock: Clock = time.monotonic,
) -> CoordinatorRunResult:
    """从已核验 soft-stop 检查点继续；四类哈希变化即拒绝。"""

    if not isinstance(plan, ResearchPlan) or not plan.ready or not plan.allow_existing_run:
        raise ResearchCoordinatorError("resume 只接受 ready 的恢复计划")
    _assert_plan_effective_limits(plan)
    verified = verify_research_run(
        plan.run_directory, expected_bindings=plan.bindings
    )
    if verified.status != "paused":
        raise ResearchCoordinatorError("只有 soft-stop paused run 可以恢复")
    _path, run_state, _hash, previous_sequence = _latest_run_state(
        plan.run_directory
    )
    if run_state.get("resource_limits") != plan.limits.to_dict():
        raise ResearchCoordinatorError("恢复计划与旧 run-state 冻结资源边界不一致")
    checkpoint_ref = run_state["checkpoint_ref"]
    state_path = plan.run_directory.joinpath(
        *_safe_relative(
            checkpoint_ref["state_path"], "checkpoint_ref.state_path"
        ).parts
    )
    resume_state, resume_hash = _read_json_regular(
        state_path, 256 * 1024 * 1024
    )
    if resume_hash != checkpoint_ref["state_sha256"]:
        raise ResearchCoordinatorError("恢复状态哈希变化")
    execution = run_state.get("execution")
    if not isinstance(execution, Mapping):
        raise ResearchCoordinatorError("resume-state.execution 缺失")
    outputs = _validate_outputs(resume_state.get("outputs"))
    attempt_started = _nonnegative_number(clock(), "clock")
    state, relative, published = _run(
        plan=plan,
        run_root=plan.run_directory,
        executor=executor,
        clock=clock,
        attempt_id=_nonnegative_integer(
            run_state.get("attempt_id"), "run_state.attempt_id"
        )
        + 1,
        state_sequence=previous_sequence + 1,
        previous_state_ref={
            "path": _path.name,
            "sha256": _hash,
        },
        next_artifact_index=_nonnegative_integer(
            resume_state.get("next_artifact_index"), "next_artifact_index"
        ),
        next_record_ordinal=_nonnegative_integer(
            resume_state.get("next_record_ordinal"), "next_record_ordinal"
        ),
        next_segment_index=_nonnegative_integer(
            resume_state.get("next_segment_index"), "next_segment_index"
        ),
        outputs=outputs,
        raw_bytes=_nonnegative_integer(
            execution.get("new_raw_bytes_read"), "execution.new_raw_bytes_read"
        ),
        peak_temporary=_nonnegative_integer(
            execution.get("peak_temporary_bytes"),
            "execution.peak_temporary_bytes",
        ),
        max_worker=_nonnegative_number(
            execution.get("max_worker_seconds"), "execution.max_worker_seconds"
        ),
        output_bytes=_nonnegative_integer(
            execution.get("output_bytes"), "execution.output_bytes"
        ),
        database_writes=_nonnegative_integer(
            execution.get("database_write_operations"),
            "execution.database_write_operations",
        ),
        semantic_chain=_sha256(
            resume_state.get("semantic_chain_sha256"),
            "resume_state.semantic_chain_sha256",
        ),
        attempt_started=attempt_started,
    )
    return CoordinatorRunResult(
        run_directory=plan.run_directory,
        run_state=state,
        run_state_relative_path=relative,
        run_state_sha256=published.sha256,
    )


__all__ = (
    "BOUNDED_PILOT_MODE",
    "CoordinatorRunResult",
    "DEFAULT_MAX_ARTIFACTS_PER_CHUNK",
    "DEFAULT_PRODUCTION_ROOTS",
    "DEFAULT_PROTECTED_ROOTS",
    "ExecutionRecord",
    "effective_resource_limits",
    "FULL_PROFILE_MODE",
    "LEGACY_FULL_WINDOW_MODE",
    "PLAN_SCHEMA_VERSION",
    "RESUME_STATE_SCHEMA_VERSION",
    "RUN_STATE_SCHEMA_VERSION",
    "RecordExecutor",
    "ResearchChunk",
    "ResearchCoordinatorError",
    "ResearchPlan",
    "VerificationResult",
    "WORKER_PLAN_SCHEMA_VERSION",
    "build_worker_plan",
    "execute_research",
    "load_json_metadata",
    "normalize_execution_mode",
    "prepare_research_plan",
    "resume_research",
    "verify_research_run",
    "verify_worker_plan",
)
