"""RRC25 伊朗完整窗口的独立 analysis-RIB anchor 外围纵切。

本模块只处理 selection 中冻结的 21 张窗口内 RIB 和 1 张严格早于窗口的
baseline reference RIB。每张 RIB 都形成独立、内容寻址的路由快照 anchor；
它不会把 RIB 注入 UPDATE 回放状态，也不会重置或改写 UPDATE 国家主曲线。

安全边界：

* 压缩 RIB 在打开前必须已有 create-only raw reservation；失败与重试不退款；
* 每份压缩源只用一次 pass 构建解压 spool，同时核验 SHA256、size、gzip
  EOF/CRC 和读取期间普通文件身份；
* spool 解析复用既有 record-boundary seek 合同，在 420 秒主动 checkpoint，
  540/590 秒分别为排他软/硬边界，外层进程在 596 秒前必须退出；
* 任一时刻只允许一个 active spool，解压字节始终严格小于 5GB，anchor 闭合
  后以 create-only attempt/success 收据退役；
* 输出只包含规范 JSON/JSONL gzip 文件，不连接或写入任何数据库。

当前纵切提供 fixture 可执行闭环、dry-run 所需纯计划、只读 verify，以及与
UPDATE 边界快照的纯哈希对账接口。真实窗口调度与 UPDATE journal 集成由上层
协调器后续显式完成。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
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
import time
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from ...route_event import AsPathSegment, ParsedRouteElement

from .country_impact import (
    RAW_RETENTION_UNION_SEMANTICS,
    RawRetentionMappingUnion,
    build_raw_retention_mapping_union,
    derive_origin_asns,
    mapping_bundle_sha256,
    mapping_view_from_frozen_snapshot,
    mapping_view_from_revised_snapshot,
)
from .file_artifacts import (
    PublishedArtifact,
    canonical_json,
    write_canonical_json,
    write_canonical_jsonl_gzip,
)
from .full_window_selection import (
    FullWindowSelectionError,
    validate_complete_selection_against_profile,
)
from .full_window_journal import (
    FullWindowJournalError,
    frozen_journal_head,
    load_full_window_head,
)
from . import full_window_journal as _full_window_journal
from .coordinator import DEFAULT_PRODUCTION_ROOTS, DEFAULT_PROTECTED_ROOTS
from .profile import profile_sha256
from .replay_persistence import (
    route_replay_state_from_payload,
    route_replay_state_to_payload,
)
from .rib_adapter import ObservedVpAccumulator, iter_rib_spool_artifact_records
from .rib_parser import (
    RibPeerIndexContext,
    RibRecordBoundary,
    RibSpoolError,
    build_rib_decompressed_spool,
    verify_rib_decompressed_spool,
)
from .state_replay import (
    RouteReplayState,
    build_research_route_event,
    extend_streaming_rib_seed,
)


UTC = timezone.utc

PLAN_SCHEMA_VERSION = "rrc25-analysis-rib-anchor-plan/v1"
RAW_GENESIS_SCHEMA_VERSION = "rrc25-analysis-rib-raw-genesis/v1"
RAW_RESERVATION_SCHEMA_VERSION = "rrc25-analysis-rib-raw-reservation/v1"
RAW_OPEN_CLAIM_SCHEMA_VERSION = "rrc25-analysis-rib-raw-open-claim/v1"
PRIOR_RAW_ACCOUNTING_SCHEMA_VERSION = (
    "rrc25-analysis-rib-prior-raw-accounting/v1"
)
PRIOR_JOURNAL_VERIFICATION_SCHEMA_VERSION = (
    "rrc25-analysis-rib-prior-journal-terminal-deep-verification/v1"
)
RETENTION_POLICY_SCHEMA_VERSION = "rrc25-analysis-rib-retention-policy/v1"
EXECUTION_ATTEMPT_SCHEMA_VERSION = "rrc25-analysis-rib-execution-attempt/v1"
EXECUTION_ACTIVE_SCHEMA_VERSION = "rrc25-analysis-rib-execution-active/v1"
EXECUTION_OUTCOME_SCHEMA_VERSION = "rrc25-analysis-rib-execution-outcome/v1"
SUPERVISOR_RECEIPT_SCHEMA_VERSION = (
    "rrc25-analysis-rib-supervisor-receipt/v1"
)
FAILED_SPOOL_RETIREMENT_SCHEMA_VERSION = (
    "rrc25-analysis-rib-failed-spool-retirement/v1"
)
CHECKPOINT_SCHEMA_VERSION = "rrc25-analysis-rib-anchor-checkpoint/v1"
ANCHOR_RECEIPT_SCHEMA_VERSION = "rrc25-analysis-rib-anchor-receipt/v1"
RETIREMENT_ATTEMPT_SCHEMA_VERSION = (
    "rrc25-analysis-rib-spool-retirement-attempt/v1"
)
RETIREMENT_RECEIPT_SCHEMA_VERSION = (
    "rrc25-analysis-rib-spool-retirement-receipt/v1"
)
PARSER_ATTESTATION_SCHEMA_VERSION = (
    "rrc25-analysis-rib-parser-source-attestation/v1"
)
RECONCILIATION_SCHEMA_VERSION = (
    "rrc25-analysis-rib-update-boundary-reconciliation/v1"
)
SHARD_SEMANTIC_SCHEMA = "rrc25_analysis_rib_anchor_shard_semantic_v1"
PROJECTION_SEMANTICS = (
    "independent_rib_visible_route_projection_by_vp_afi_prefix_v1"
)
ANCHOR_SEMANTIC_SCHEMA = "rrc25_analysis_rib_anchor_semantic_v1"
FINGERPRINT_SCHEMA = "rrc25_analysis_rib_anchor_fingerprint_v1"

EXPECTED_ANALYSIS_RIB_COUNT = 21
EXPECTED_BASELINE_RIB_COUNT = 1
EXPECTED_ANCHOR_COUNT = 22
EXPECTED_NEW_RAW_ANCHOR_COUNT = 21
RIB_INTERVAL = timedelta(hours=8)

DEFAULT_PLANNED_CHECKPOINT_SECONDS = 420.0
DEFAULT_SOFT_STOP_SECONDS = 540.0
DEFAULT_HARD_STOP_SECONDS = 590.0
DEFAULT_EXTERNAL_SHELL_HARD_STOP_SECONDS = 596.0
DEFAULT_MAX_RAW_READ_BYTES = 50_000_000_000
DEFAULT_MAX_TEMPORARY_BYTES = 5_000_000_000
DEFAULT_BATCH_RECORDS = 16_384
DEFAULT_BATCH_ROUTE_EVENTS = 131_072

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ID_RE = re.compile(r"^art_v1_[0-9a-f]{32}$")
_ATTEMPT_ID_RE = re.compile(r"^attempt_v1_[0-9a-f]{32}$")
_ANCHOR_ID_RE = re.compile(r"^rib_anchor_v1_[0-9a-f]{32}$")
_IDENTITY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)
_BINDING_FIELDS = frozenset(
    {"profile_sha256", "input_selection_sha256", "code_sha256", "mapping_sha256"}
)
_MUTATION_PROTECTED_ROOTS = tuple(
    Path(value).expanduser().resolve(strict=False)
    for value in (*DEFAULT_PROTECTED_ROOTS, *DEFAULT_PRODUCTION_ROOTS)
)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


class AnalysisRibAnchorError(ValueError):
    """analysis-RIB anchor 输入、资源或证据链不闭合。"""


class SimulatedAnalysisRibCrash(RuntimeError):
    """仅供 fixture 注入不可恢复进程窗口；ACTIVE 必须留给 reconcile。"""


class AnalysisRibTerminationRequested(RuntimeError):
    """父 supervisor 的 TERM 已中止当前 segment。"""


Clock = Callable[[], float]


@dataclass(frozen=True)
class VerifiedPriorRawAccounting:
    """由 full-window terminal journal 全链核验得到的只读 raw 基线。"""

    journal_root: str
    run_id: str
    bindings: Mapping[str, str]
    terminal_receipt_ref: Mapping[str, str]
    genesis_seed_shards: Tuple[Mapping[str, Any], ...]
    shard_chain_sha256: str
    completed_artifact_count: int
    total_artifacts: int
    verified_receipt_count: int
    cumulative_reserved_raw_bytes: int
    fingerprint_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PRIOR_RAW_ACCOUNTING_SCHEMA_VERSION,
            "journal_root": self.journal_root,
            "run_id": self.run_id,
            "bindings": dict(self.bindings),
            "terminal_receipt_ref": dict(self.terminal_receipt_ref),
            "genesis_seed_shards": [dict(row) for row in self.genesis_seed_shards],
            "shard_chain_sha256": self.shard_chain_sha256,
            "completed_artifact_count": self.completed_artifact_count,
            "total_artifacts": self.total_artifacts,
            "verified_receipt_count": self.verified_receipt_count,
            "cumulative_reserved_raw_bytes": self.cumulative_reserved_raw_bytes,
            "fingerprint_sha256": self.fingerprint_sha256,
        }


@dataclass(frozen=True)
class AnalysisRibRetentionPolicy:
    """冻结 compatible+revised 并集，禁止注入任意 origin predicate。"""

    mapping_bundle_sha256: str
    union: RawRetentionMappingUnion
    evidence: Mapping[str, Any]

    def retain_origin_asn(self, asn: int) -> bool:
        # unknown/conflict 不能被误判为非伊朗，因此只有双视图均明确非目标时丢弃。
        return self.union.raw_retention_membership(asn) is not False

    def to_dict(self) -> dict[str, Any]:
        return dict(self.evidence)


@dataclass(frozen=True)
class AnalysisRibDescriptor:
    """selection 中一张独立 anchor RIB 的冻结身份。"""

    anchor_index: int
    role: str
    ingestion_mode: str
    artifact_id: str
    file_sha256: str
    size_bytes: int
    collector_id: str
    artifact_time_utc: str
    relative_path: str
    compression: str

    def artifact(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": "rib",
            "artifact_time_utc": self.artifact_time_utc,
            "collector_id": self.collector_id,
            "relative_path": self.relative_path,
            "file_sha256": self.file_sha256,
            "size_bytes": self.size_bytes,
            "compression": self.compression,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_index": self.anchor_index,
            "role": self.role,
            "ingestion_mode": self.ingestion_mode,
            **self.artifact(),
        }


@dataclass(frozen=True)
class AnalysisRibPlan:
    selection_id: str
    selection_semantic_sha256: str
    profile_sha256: str
    artifacts: Tuple[AnalysisRibDescriptor, ...]
    prior_raw_accounting: VerifiedPriorRawAccounting
    prior_raw_read_bytes: int
    planned_new_raw_read_bytes: int
    projected_cumulative_raw_read_bytes: int
    max_raw_read_bytes: int
    execution_allowed: bool
    blocker: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "selection_id": self.selection_id,
            "selection_semantic_sha256": self.selection_semantic_sha256,
            "profile_sha256": self.profile_sha256,
            "anchor_count": len(self.artifacts),
            "analysis_rib_count": sum(
                item.role == "analysis_rib" for item in self.artifacts
            ),
            "imported_seed_anchor_count": sum(
                item.ingestion_mode == "imported_full_window_seed"
                for item in self.artifacts
            ),
            "new_raw_analysis_rib_count": sum(
                item.role == "analysis_rib" and item.ingestion_mode == "new_raw"
                for item in self.artifacts
            ),
            "baseline_reference_rib_count": sum(
                item.role == "baseline_reference_rib" for item in self.artifacts
            ),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "prior_raw_accounting": self.prior_raw_accounting.to_dict(),
            "resources": {
                "prior_raw_read_bytes": self.prior_raw_read_bytes,
                "planned_new_raw_read_bytes": self.planned_new_raw_read_bytes,
                "projected_cumulative_raw_read_bytes": (
                    self.projected_cumulative_raw_read_bytes
                ),
                "max_raw_read_bytes_exclusive": self.max_raw_read_bytes,
                "max_temporary_bytes_exclusive": DEFAULT_MAX_TEMPORARY_BYTES,
                "planned_checkpoint_seconds": DEFAULT_PLANNED_CHECKPOINT_SECONDS,
                "soft_stop_seconds_exclusive": DEFAULT_SOFT_STOP_SECONDS,
                "hard_stop_seconds_exclusive": DEFAULT_HARD_STOP_SECONDS,
                "database_writes": 0,
            },
            "execution_allowed": self.execution_allowed,
            "blocker": self.blocker,
            "update_curve_policy": "independent_anchor_never_reset_update_curve",
        }


@dataclass(frozen=True)
class RawReservationToken:
    attempt_id: str
    path: str
    sha256: str
    sequence: int
    descriptor: AnalysisRibDescriptor
    reserved_raw_bytes: int
    cumulative_reserved_raw_bytes: int


@dataclass(frozen=True)
class AnchorSegmentResult:
    status: str
    reason: Optional[str]
    artifact_id: str
    checkpoint_path: Optional[str]
    anchor_receipt_path: Optional[str]
    retirement_receipt_path: Optional[str]
    next_record_ordinal: int
    next_record_offset: int
    process_seconds: float
    peak_temporary_bytes: int


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise AnalysisRibAnchorError(f"{field} 必须是小写 SHA256")
    return value


def _nonnegative(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AnalysisRibAnchorError(f"{field} 必须是非负整数")
    return value


def _positive(value: Any, field: str) -> int:
    result = _nonnegative(value, field)
    if result == 0:
        raise AnalysisRibAnchorError(f"{field} 必须大于 0")
    return result


def _positive_seconds(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnalysisRibAnchorError(f"{field} 必须是正有限数")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise AnalysisRibAnchorError(f"{field} 必须是正有限数")
    return result


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AnalysisRibAnchorError(f"{field} 必须是秒级 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise AnalysisRibAnchorError(f"{field} 不是合法 UTC 时间") from error
    if parsed.microsecond or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise AnalysisRibAnchorError(f"{field} 必须是秒级 UTC")
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise AnalysisRibAnchorError(f"{field} 不是规范 UTC")
    return parsed


def _safe_relative(value: Any, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise AnalysisRibAnchorError(f"{field} 必须是非空相对路径")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise AnalysisRibAnchorError(f"{field} 必须是安全相对路径")
    return path


def _bindings(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _BINDING_FIELDS:
        raise AnalysisRibAnchorError("bindings 必须且只能包含四个冻结 SHA256")
    return {name: _sha(value[name], f"bindings.{name}") for name in sorted(value)}


def _fingerprinted(schema_version: str, semantic: Mapping[str, Any]) -> dict[str, Any]:
    body = {"schema_version": schema_version, **dict(semantic)}
    fingerprint = hashlib.sha256(
        canonical_json({"schema": FINGERPRINT_SCHEMA, "payload": body}).encode(
            "utf-8"
        )
    ).hexdigest()
    return {**body, "fingerprint_sha256": fingerprint}


def _verify_fingerprint(
    value: Any, schema_version: str, field: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AnalysisRibAnchorError(f"{field} 必须是对象")
    body = dict(value)
    fingerprint = body.pop("fingerprint_sha256", None)
    if body.get("schema_version") != schema_version:
        raise AnalysisRibAnchorError(f"{field} schema_version 不受支持")
    expected = hashlib.sha256(
        canonical_json({"schema": FINGERPRINT_SCHEMA, "payload": body}).encode(
            "utf-8"
        )
    ).hexdigest()
    if fingerprint != expected:
        raise AnalysisRibAnchorError(f"{field} fingerprint 不一致")
    return dict(value)


def _stable_identity(metadata: os.stat_result) -> dict[str, int]:
    return {name: int(getattr(metadata, name)) for name in _IDENTITY_FIELDS}


def _same_identity(first: Mapping[str, int], second: Mapping[str, int]) -> bool:
    if set(first) != set(_IDENTITY_FIELDS) or set(second) != set(_IDENTITY_FIELDS):
        return False
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (*first.values(), *second.values())
    ):
        return False
    return all(first[name] == second[name] for name in _IDENTITY_FIELDS)


def _read_regular(path: Path, *, maximum_bytes: int) -> Tuple[bytes, str]:
    maximum = _positive(maximum_bytes, "maximum_bytes")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AnalysisRibAnchorError(f"无法安全读取普通文件：{path.name}") from error
    digest = hashlib.sha256()
    blocks: list[bytes] = []
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AnalysisRibAnchorError("读取目标不是普通文件")
        while True:
            block = os.read(descriptor, min(1024 * 1024, maximum + 1 - size))
            if not block:
                break
            size += len(block)
            if size > maximum:
                raise AnalysisRibAnchorError("读取目标超过显式大小上限")
            blocks.append(block)
            digest.update(block)
        after = os.fstat(descriptor)
        if _stable_identity(before) != _stable_identity(after):
            raise AnalysisRibAnchorError("普通文件在读取期间发生变化")
    finally:
        os.close(descriptor)
    return b"".join(blocks), digest.hexdigest()


def _hash_regular(path: Path, *, maximum_bytes: int) -> Tuple[str, int]:
    maximum = _positive(maximum_bytes, "maximum_bytes")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AnalysisRibAnchorError(f"无法安全哈希普通文件：{path.name}") from error
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AnalysisRibAnchorError("哈希目标不是普通文件")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            size += len(block)
            if size > maximum:
                raise AnalysisRibAnchorError("哈希目标超过显式大小上限")
            digest.update(block)
        after = os.fstat(descriptor)
        if _stable_identity(before) != _stable_identity(after):
            raise AnalysisRibAnchorError("普通文件在哈希期间发生变化")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _load_canonical_json(
    path: Path, *, schema_version: Optional[str] = None, maximum_bytes: int = 64 * 1024 * 1024
) -> dict[str, Any]:
    raw, _digest = _read_regular(path, maximum_bytes=maximum_bytes)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisRibAnchorError(f"{path.name} 不是合法 JSON") from error
    if not isinstance(payload, Mapping):
        raise AnalysisRibAnchorError(f"{path.name} 必须是 JSON 对象")
    if raw != (canonical_json(dict(payload)) + "\n").encode("utf-8"):
        raise AnalysisRibAnchorError(f"{path.name} 不是规范 JSON")
    if schema_version is not None:
        _verify_fingerprint(payload, schema_version, path.name)
    return dict(payload)


def _content_json(
    root: Path,
    directory: str,
    prefix: str,
    payload: Mapping[str, Any],
) -> Tuple[PublishedArtifact, str]:
    encoded = (canonical_json(dict(payload)) + "\n").encode("utf-8")
    content_sha = hashlib.sha256(encoded).hexdigest()
    relative = f"{directory}/{prefix}-{content_sha}.json"
    published = write_canonical_json(
        root / relative,
        payload,
        kind=prefix,
        mode=0o440,
    )
    if published.sha256 != content_sha:
        raise AnalysisRibAnchorError("内容寻址 JSON 发布后 SHA256 漂移")
    return published, relative


def _content_json_idempotent(
    root: Path,
    directory: str,
    prefix: str,
    payload: Mapping[str, Any],
) -> Tuple[PublishedArtifact, str]:
    """发布内容寻址 JSON；同内容重放等价于一次成功发布。"""

    encoded = (canonical_json(dict(payload)) + "\n").encode("utf-8")
    content_sha = hashlib.sha256(encoded).hexdigest()
    relative = f"{directory}/{prefix}-{content_sha}.json"
    path = root / relative
    try:
        published = write_canonical_json(
            path,
            payload,
            kind=prefix,
            mode=0o440,
        )
    except FileExistsError:
        existing, existing_sha = _read_regular(
            path, maximum_bytes=max(len(encoded), 4 * 1024 * 1024)
        )
        if existing != encoded or existing_sha != content_sha:
            raise AnalysisRibAnchorError("内容寻址 JSON 已存在但内容不一致")
        published = PublishedArtifact(
            path=path,
            sha256=existing_sha,
            size_bytes=len(existing),
            record_count=1,
            kind=prefix,
        )
    if published.sha256 != content_sha:
        raise AnalysisRibAnchorError("内容寻址 JSON 幂等发布后 SHA256 漂移")
    return published, relative


def _atomic_mutable_json(path: Path, payload: Mapping[str, Any]) -> None:
    """同目录 fsync + replace 更新唯一可变 ACTIVE 指针。"""

    encoded = (canonical_json(dict(payload)) + "\n").encode("utf-8")
    temporary = path.parent / f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise AnalysisRibAnchorError("ACTIVE 写入未取得进展")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


@contextmanager
def analysis_rib_execution_lock(
    anchor_root: os.PathLike[str] | str,
    *,
    nonblocking: bool = False,
):
    """单写者锁：覆盖 active-spool 检查、处理、receipt 与退役。"""

    root = Path(anchor_root).expanduser().resolve(strict=False)
    _assert_existing_anchor_mutation_root(root, "anchor_root")
    path = root / "execution" / "LOCK"
    try:
        descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise AnalysisRibAnchorError("execution LOCK 不可安全打开") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise AnalysisRibAnchorError("execution LOCK 必须是普通文件")
        operation = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError as error:
            raise AnalysisRibAnchorError("已有 analysis-RIB worker 持有单写者锁") from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _active_path(root: Path) -> Path:
    return root / "execution" / "ACTIVE.json"


def _load_active(root: Path) -> Optional[dict[str, Any]]:
    path = _active_path(root)
    if not path.exists() and not path.is_symlink():
        return None
    return _load_canonical_json(
        path,
        schema_version=EXECUTION_ACTIVE_SCHEMA_VERSION,
        maximum_bytes=4 * 1024 * 1024,
    )


def load_analysis_rib_active_attempt(
    anchor_root: os.PathLike[str] | str,
    *,
    bindings: Mapping[str, Any],
) -> Optional[Mapping[str, Any]]:
    """只读返回 ACTIVE 指针；用于父 supervisor 决定 resume/reconcile。"""

    root = Path(anchor_root)
    _assert_root_directory(root, "anchor_root")
    frozen_bindings = _bindings(bindings)
    if _load_genesis(root).get("bindings") != frozen_bindings:
        raise AnalysisRibAnchorError("ACTIVE 查询 bindings 与 genesis 不一致")
    active = _load_active(root)
    if active is not None and active.get("bindings") != frozen_bindings:
        raise AnalysisRibAnchorError("ACTIVE bindings 与 genesis 不一致")
    return None if active is None else dict(active)


def _clear_active(root: Path) -> None:
    path = _active_path(root)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _attempt_ref_for(
    root: Path,
    *,
    attempt_id: str,
    descriptor: AnalysisRibDescriptor,
    bindings: Mapping[str, str],
    reservation: Optional[RawReservationToken],
) -> Mapping[str, str]:
    payload = _fingerprinted(
        EXECUTION_ATTEMPT_SCHEMA_VERSION,
        {
            "attempt_id": attempt_id,
            "artifact": descriptor.to_dict(),
            "bindings": dict(bindings),
            "reservation_ref": (
                None
                if reservation is None
                else {
                    "path": reservation.path,
                    "sha256": reservation.sha256,
                    "sequence": reservation.sequence,
                    "reserved_raw_bytes": reservation.reserved_raw_bytes,
                    "cumulative_reserved_raw_bytes": (
                        reservation.cumulative_reserved_raw_bytes
                    ),
                }
            ),
            "state": "started_before_any_raw_open",
            "no_refund": True,
        },
    )
    relative = f"execution/attempts/attempt-{attempt_id}.json"
    path = root / relative
    try:
        published = write_canonical_json(
            path, payload, kind="analysis_rib_execution_attempt", mode=0o440
        )
    except FileExistsError:
        raise AnalysisRibAnchorError(
            "execution attempt_id 已存在；重试必须新增 reservation/attempt_id"
        )
    return {"path": relative, "sha256": published.sha256}


def _write_execution_outcome(
    root: Path,
    *,
    active: Mapping[str, Any],
    status: str,
    raw_observation: Mapping[str, Any],
    result: Optional[Mapping[str, Any]] = None,
    error: Optional[str] = None,
) -> Mapping[str, str]:
    payload = _fingerprinted(
        EXECUTION_OUTCOME_SCHEMA_VERSION,
        {
            "attempt_id": active["attempt_id"],
            "artifact": active["artifact"],
            "bindings": active["bindings"],
            "attempt_ref": active["attempt_ref"],
            "segment_sequence": active["segment_sequence"],
            "status": status,
            "raw_read_observation": dict(raw_observation),
            "no_refund": True,
            "result": None if result is None else dict(result),
            "error": error,
        },
    )
    published, relative = _content_json_idempotent(
        root, "execution/outcomes", "outcome", payload
    )
    return {"path": relative, "sha256": published.sha256}


def _raw_observation(
    root: Path,
    *,
    descriptor: AnalysisRibDescriptor,
) -> Mapping[str, Any]:
    spool = root / "spools" / f"{descriptor.artifact_id}.mrt"
    if spool.is_file() and not spool.is_symlink():
        return {
            "state": "exact",
            "bytes": descriptor.size_bytes,
            "basis": "published_spool_proves_complete_single_pass",
        }
    for path in (root / "receipts").glob("anchor-*.json"):
        receipt = _load_canonical_json(
            path,
            schema_version=ANCHOR_RECEIPT_SCHEMA_VERSION,
            maximum_bytes=64 * 1024 * 1024,
        )
        if receipt.get("artifact", {}).get("artifact_id") == descriptor.artifact_id:
            proof = receipt.get("compressed_single_pass_proof")
            if isinstance(proof, Mapping) and (
                proof.get("file_sha256") == descriptor.file_sha256
                and proof.get("size_bytes") == descriptor.size_bytes
                and proof.get("read_passes") == 1
            ):
                return {
                    "state": "exact",
                    "bytes": descriptor.size_bytes,
                    "basis": "published_anchor_preserves_complete_single_pass_proof",
                }
    return {
        "state": "unknown",
        "minimum_bytes": 0,
        "maximum_bytes": descriptor.size_bytes,
        "basis": "process_ended_before_complete_single_pass_proof",
    }


def _begin_or_resume_active(
    root: Path,
    *,
    descriptor: AnalysisRibDescriptor,
    bindings: Mapping[str, str],
    reservation: Optional[RawReservationToken],
    resume_checkpoint_path: Optional[os.PathLike[str] | str],
    attempt_id: Optional[str] = None,
) -> dict[str, Any]:
    current = _load_active(root)
    if resume_checkpoint_path is None:
        if current is not None:
            raise AnalysisRibAnchorError(
                "存在 ACTIVE attempt；必须先 resume 或 reconcile"
            )
        selected_attempt_id = (
            reservation.attempt_id
            if reservation is not None
            else attempt_id or "attempt_v1_" + secrets.token_hex(16)
        )
        if (
            not isinstance(selected_attempt_id, str)
            or _ATTEMPT_ID_RE.fullmatch(selected_attempt_id) is None
        ):
            raise AnalysisRibAnchorError("execution attempt_id 非法")
        attempt_ref = _attempt_ref_for(
            root,
            attempt_id=selected_attempt_id,
            descriptor=descriptor,
            bindings=bindings,
            reservation=reservation,
        )
        active = _fingerprinted(
            EXECUTION_ACTIVE_SCHEMA_VERSION,
            {
                "attempt_id": selected_attempt_id,
                "artifact": descriptor.to_dict(),
                "bindings": dict(bindings),
                "attempt_ref": dict(attempt_ref),
                "reservation_path": None if reservation is None else reservation.path,
                "segment_sequence": 0,
                "state": "running",
                "latest_checkpoint_path": None,
                "latest_outcome_ref": None,
            },
        )
        _atomic_mutable_json(_active_path(root), active)
        return active
    if current is None:
        raise AnalysisRibAnchorError("resume 缺少 ACTIVE attempt")
    checkpoint_text = str(resume_checkpoint_path)
    if Path(checkpoint_text).is_absolute():
        try:
            checkpoint_text = Path(checkpoint_text).relative_to(root).as_posix()
        except ValueError as error:
            raise AnalysisRibAnchorError("resume checkpoint 越出 anchor_root") from error
    checkpoint_text = _safe_relative(
        checkpoint_text, "resume_checkpoint_path"
    ).as_posix()
    expected_reservation = None if reservation is None else reservation.path
    if (
        current.get("artifact") != descriptor.to_dict()
        or current.get("bindings") != dict(bindings)
        or current.get("reservation_path") != expected_reservation
        or current.get("state") != "checkpointed"
        or current.get("latest_checkpoint_path") != checkpoint_text
    ):
        raise AnalysisRibAnchorError("resume 与 ACTIVE checkpoint 不一致")
    resumed = _fingerprinted(
        EXECUTION_ACTIVE_SCHEMA_VERSION,
        {
            key: value
            for key, value in current.items()
            if key not in {"schema_version", "fingerprint_sha256", "state"}
        }
        | {"state": "running"},
    )
    _atomic_mutable_json(_active_path(root), resumed)
    return resumed


def _checkpoint_active(
    root: Path,
    active: Mapping[str, Any],
    *,
    result: AnchorSegmentResult,
    outcome_ref: Mapping[str, str],
) -> dict[str, Any]:
    if result.checkpoint_path is None:
        raise AnalysisRibAnchorError("checkpoint result 缺少 checkpoint_path")
    updated = _fingerprinted(
        EXECUTION_ACTIVE_SCHEMA_VERSION,
        {
            "attempt_id": active["attempt_id"],
            "artifact": active["artifact"],
            "bindings": active["bindings"],
            "attempt_ref": active["attempt_ref"],
            "reservation_path": active["reservation_path"],
            "segment_sequence": int(active["segment_sequence"]) + 1,
            "state": "checkpointed",
            "latest_checkpoint_path": result.checkpoint_path,
            "latest_outcome_ref": dict(outcome_ref),
        },
    )
    _atomic_mutable_json(_active_path(root), updated)
    return updated


def _transition_active(
    root: Path,
    active: Mapping[str, Any],
    *,
    state: str,
    outcome_ref: Optional[Mapping[str, str]],
) -> dict[str, Any]:
    if state not in {"running", "checkpointed", "recovery_required"}:
        raise AnalysisRibAnchorError("ACTIVE 状态迁移非法")
    updated = _fingerprinted(
        EXECUTION_ACTIVE_SCHEMA_VERSION,
        {
            "attempt_id": active["attempt_id"],
            "artifact": active["artifact"],
            "bindings": active["bindings"],
            "attempt_ref": active["attempt_ref"],
            "reservation_path": active["reservation_path"],
            "segment_sequence": active["segment_sequence"],
            "state": state,
            "latest_checkpoint_path": active.get("latest_checkpoint_path"),
            "latest_outcome_ref": (
                active.get("latest_outcome_ref")
                if outcome_ref is None
                else dict(outcome_ref)
            ),
        },
    )
    _atomic_mutable_json(_active_path(root), updated)
    return updated


def _discard_failed_attempt_files(
    root: Path,
    *,
    descriptor: AnalysisRibDescriptor,
    attempt_id: str,
    reason: str,
) -> Mapping[str, Any]:
    candidates = [root / "spools" / f"{descriptor.artifact_id}.mrt"]
    candidates.extend(
        sorted(
            (root / "spools").glob(
                f".{descriptor.artifact_id}.mrt.tmp-*"
            )
        )
    )
    # 进程被杀时，通用不可覆盖发布器也可能留下点号 staging；锁内统一退役。
    for directory in (
        root / "shards",
        root / "checkpoints",
        root / "receipts",
        root / "retirements",
        root / "execution" / "outcomes",
    ):
        candidates.extend(sorted(directory.glob(".*.tmp-*")))
    retired = []
    touched_directories: set[Path] = set()
    for path in candidates:
        if not path.exists() and not path.is_symlink():
            continue
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise AnalysisRibAnchorError("失败 staging/spool 不是普通文件，拒绝清理")
        before = _stable_identity(metadata)
        path.unlink()
        touched_directories.add(path.parent)
        retired.append(
            {
                "path": path.relative_to(root).as_posix(),
                "stable_identity_before_unlink": before,
            }
        )
    for parent in sorted(touched_directories):
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    receipt = _fingerprinted(
        FAILED_SPOOL_RETIREMENT_SCHEMA_VERSION,
        {
            "attempt_id": attempt_id,
            "artifact": descriptor.to_dict(),
            "reason": reason,
            "retired_files": retired,
            "status": "failed_attempt_files_absent_and_directory_synced",
        },
    )
    _published, relative = _content_json_idempotent(
        root, "retirements", "failed-attempt-retirement", receipt
    )
    return {"path": relative, "retired_file_count": len(retired)}


def _result_payload(result: AnchorSegmentResult) -> Mapping[str, Any]:
    return asdict(result)


def _shard_semantic(
    *, kind: str, artifact_id: str, sequence: int, records: Sequence[Mapping[str, Any]]
) -> Tuple[str, str]:
    raw = b"".join(
        (canonical_json(dict(record)) + "\n").encode("utf-8")
        for record in records
    )
    records_sha = hashlib.sha256(raw).hexdigest()
    semantic = hashlib.sha256(
        canonical_json(
            {
                "schema": SHARD_SEMANTIC_SCHEMA,
                "kind": kind,
                "artifact_id": artifact_id,
                "sequence": sequence,
                "record_count": len(records),
                "records_sha256": records_sha,
            }
        ).encode("utf-8")
    ).hexdigest()
    return semantic, records_sha


def _publish_shard(
    root: Path,
    *,
    kind: str,
    artifact_id: str,
    sequence: int,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    values = tuple(dict(record) for record in records)
    semantic_sha, records_sha = _shard_semantic(
        kind=kind,
        artifact_id=artifact_id,
        sequence=sequence,
        records=values,
    )
    relative = f"shards/{kind}-{semantic_sha}.jsonl.gz"
    try:
        published = write_canonical_jsonl_gzip(
            root / relative,
            values,
            kind=kind,
            compresslevel=1,
            mode=0o440,
        )
    except FileExistsError:
        file_sha, file_size = _hash_regular(
            root / relative, maximum_bytes=DEFAULT_MAX_TEMPORARY_BYTES
        )
        existing_ref = {
            "kind": kind,
            "artifact_id": artifact_id,
            "sequence": sequence,
            "path": relative,
            "file_sha256": file_sha,
            "size_bytes": file_size,
            "record_count": len(values),
            "records_sha256": records_sha,
            "semantic_sha256": semantic_sha,
        }
        if _verify_shard_ref(root, existing_ref) != values:
            raise AnalysisRibAnchorError("内容寻址 shard 已存在但内容不一致")
        return existing_ref
    return {
        "kind": kind,
        "artifact_id": artifact_id,
        "sequence": sequence,
        "path": relative,
        "file_sha256": published.sha256,
        "size_bytes": published.size_bytes,
        "record_count": published.record_count,
        "records_sha256": records_sha,
        "semantic_sha256": semantic_sha,
    }


def _regular_size_or_zero(path: Path, *, field: str) -> int:
    if not path.exists() and not path.is_symlink():
        return 0
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise AnalysisRibAnchorError(f"{field} 必须是普通文件")
    return int(metadata.st_size)


def _staging_temporary_bytes(root: Path) -> int:
    """统计工作区所有未完成发布文件；未知 staging 不能被临时门遗漏。"""

    total = 0
    for directory in (
        root / "spools",
        root / "shards",
        root / "checkpoints",
        root / "receipts",
        root / "retirements",
        root / "raw-open-claims",
        root / "execution",
        root / "execution" / "attempts",
        root / "execution" / "outcomes",
        root / "execution" / "supervisors",
    ):
        for path in directory.glob(".*.tmp-*"):
            total += _regular_size_or_zero(path, field="staging")
    return total


def _active_attempt_temporary_bytes(
    root: Path,
    *,
    spool_proof: Optional[Mapping[str, Any]],
    shard_refs: Sequence[Mapping[str, Any]],
    checkpoint_paths: Sequence[str] = (),
) -> int:
    """实时统计 spool、当前 attempt 输出、最新 checkpoint 和发布 staging。"""

    paths: set[str] = set()
    if spool_proof is not None:
        paths.add(_safe_relative(spool_proof["path"], "spool.path").as_posix())
    for ref in shard_refs:
        paths.add(_safe_relative(ref["path"], "shard.path").as_posix())
    for checkpoint_path in checkpoint_paths:
        paths.add(_safe_relative(checkpoint_path, "checkpoint.path").as_posix())
    total = _staging_temporary_bytes(root)
    for relative in paths:
        total += _regular_size_or_zero(root / relative, field="active attempt file")
    return total


def _estimated_jsonl_gzip_staging_bytes(
    records: Sequence[Mapping[str, Any]],
) -> int:
    # gzip 在极端不可压缩输入上可能略膨胀；1MiB 覆盖头尾与块开销。
    return sum(
        len((canonical_json(dict(row)) + "\n").encode("utf-8"))
        for row in records
    ) + 1024 * 1024


def _normalize_descriptor(
    row: Mapping[str, Any], *, anchor_index: int, role: str, ingestion_mode: str
) -> AnalysisRibDescriptor:
    if not isinstance(row, Mapping):
        raise AnalysisRibAnchorError(f"{role} 必须是 artifact 对象")
    if row.get("artifact_type") != "rib" or row.get("compression") != "gz":
        raise AnalysisRibAnchorError(f"{role} 只接受 gzip RIB")
    artifact_id = row.get("artifact_id")
    if not isinstance(artifact_id, str) or _ARTIFACT_ID_RE.fullmatch(artifact_id) is None:
        raise AnalysisRibAnchorError(f"{role}.artifact_id 非法")
    relative = _safe_relative(row.get("relative_path"), f"{role}.relative_path")
    if relative.parts[0] != row.get("collector_id"):
        raise AnalysisRibAnchorError(f"{role}.relative_path 越出 collector")
    return AnalysisRibDescriptor(
        anchor_index=anchor_index,
        role=role,
        ingestion_mode=ingestion_mode,
        artifact_id=artifact_id,
        file_sha256=_sha(row.get("file_sha256"), f"{role}.file_sha256"),
        size_bytes=_positive(row.get("size_bytes"), f"{role}.size_bytes"),
        collector_id=str(row.get("collector_id")),
        artifact_time_utc=_utc(
            row.get("artifact_time_utc"), f"{role}.artifact_time_utc"
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        relative_path=relative.as_posix(),
        compression="gz",
    )


def build_analysis_rib_plan(
    selection: Mapping[str, Any],
    profile: Mapping[str, Any],
    *,
    prior_raw_accounting: VerifiedPriorRawAccounting,
    bindings: Mapping[str, Any],
    max_raw_read_bytes: int = DEFAULT_MAX_RAW_READ_BYTES,
) -> AnalysisRibPlan:
    """精确冻结 21 analysis RIB + 1 baseline，并做累计 raw 排他门。"""

    frozen_bindings = _bindings(bindings)
    accounting = _refresh_prior_raw_accounting(
        prior_raw_accounting, bindings=frozen_bindings
    )
    prior = accounting.cumulative_reserved_raw_bytes
    raw_limit = _positive(max_raw_read_bytes, "max_raw_read_bytes")
    try:
        normalized_profile = validate_complete_selection_against_profile(
            selection, profile
        )
    except FullWindowSelectionError as error:
        raise AnalysisRibAnchorError("selection/Profile 完整窗口合同不闭合") from error
    if normalized_profile["collector_id"] != "rrc25":
        raise AnalysisRibAnchorError("analysis-RIB anchor 只接受 collector_id=rrc25")
    if normalized_profile["country_code"] != "IR":
        raise AnalysisRibAnchorError("analysis-RIB anchor 当前只验收 IR")
    expected = normalized_profile["input_selection"]["analysis_ribs"][
        "expected_slot_count"
    ]
    if expected != EXPECTED_ANALYSIS_RIB_COUNT:
        raise AnalysisRibAnchorError("Profile 必须精确冻结 21 张 analysis RIB")
    roles = selection.get("roles")
    if not isinstance(roles, Mapping):
        raise AnalysisRibAnchorError("selection.roles 缺失")
    analysis = roles.get("analysis_ribs")
    baseline = roles.get("baseline_reference_rib")
    if not isinstance(analysis, list) or len(analysis) != EXPECTED_ANALYSIS_RIB_COUNT:
        raise AnalysisRibAnchorError("selection 必须精确包含 21 张 analysis RIB")
    if not isinstance(baseline, Mapping):
        raise AnalysisRibAnchorError("selection 必须精确包含 1 张 baseline RIB")

    start = _utc(selection["window"]["start_utc"], "selection.window.start_utc")
    baseline_descriptor = _normalize_descriptor(
        baseline,
        anchor_index=0,
        role="baseline_reference_rib",
        ingestion_mode="new_raw",
    )
    if _utc(baseline_descriptor.artifact_time_utc, "baseline time") >= start:
        raise AnalysisRibAnchorError("baseline reference RIB 必须严格早于窗口起点")
    descriptors = [baseline_descriptor]
    for index, row in enumerate(analysis):
        descriptor = _normalize_descriptor(
            row,
            anchor_index=index + 1,
            role="analysis_rib",
            ingestion_mode=(
                "imported_full_window_seed" if index == 0 else "new_raw"
            ),
        )
        expected_time = start + index * RIB_INTERVAL
        if _utc(descriptor.artifact_time_utc, "analysis RIB time") != expected_time:
            raise AnalysisRibAnchorError("21 张 analysis RIB 必须按八小时严格连续")
        descriptors.append(descriptor)
    identities = [item.artifact_id for item in descriptors]
    if len(set(identities)) != EXPECTED_ANCHOR_COUNT:
        raise AnalysisRibAnchorError("baseline 与 analysis RIB 身份必须 22 份唯一")
    state_seed = roles.get("state_seed_rib")
    if (
        not isinstance(state_seed, Mapping)
        or state_seed.get("artifact_id") != descriptors[1].artifact_id
    ):
        raise AnalysisRibAnchorError("伊朗 B2 state seed 必须等于窗口起点 analysis RIB")

    if sum(
        item.ingestion_mode == "imported_full_window_seed" for item in descriptors
    ) != 1 or sum(
        item.role == "analysis_rib" and item.ingestion_mode == "new_raw"
        for item in descriptors
    ) != 20:
        raise AnalysisRibAnchorError("B2 必须精确导入 1 张 seed，并新读 20 张 analysis RIB")
    planned = sum(
        item.size_bytes for item in descriptors if item.ingestion_mode == "new_raw"
    )
    projected = prior + planned
    allowed = projected < raw_limit
    blocker = None if allowed else "cumulative_raw_read_limit_reached"
    return AnalysisRibPlan(
        selection_id=str(selection["selection_id"]),
        selection_semantic_sha256=_sha(
            selection.get("semantic_fingerprint_sha256"),
            "selection.semantic_fingerprint_sha256",
        ),
        profile_sha256=profile_sha256(normalized_profile),
        artifacts=tuple(descriptors),
        prior_raw_accounting=accounting,
        prior_raw_read_bytes=prior,
        planned_new_raw_read_bytes=planned,
        projected_cumulative_raw_read_bytes=projected,
        max_raw_read_bytes=raw_limit,
        execution_allowed=allowed,
        blocker=blocker,
    )


def _assert_root_directory(path: Path, field: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise AnalysisRibAnchorError(f"{field} 不存在或不可读") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise AnalysisRibAnchorError(f"{field} 必须是非符号链接目录")


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _assert_not_protected_source(path: Path, field: str) -> None:
    """拒绝把受保护/生产目录误当作 analysis 可写根。

    原始 MRT 与 sealed journal 可以作为只读来源位于别处；这个检查只用于
    即将发生 create/replace/unlink 的目标。
    """

    resolved = path.expanduser().resolve(strict=False)
    for protected in _MUTATION_PROTECTED_ROOTS:
        if resolved == protected or protected in resolved.parents:
            raise AnalysisRibAnchorError(
                f"{field} 不得写入受保护或生产目录：{protected}"
            )


def _assert_mutation_root(
    path: Path,
    field: str,
    *,
    source_roots: Sequence[Path] = (),
    must_exist: bool = True,
) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    _assert_not_protected_source(resolved, field)
    repository = _REPOSITORY_ROOT.resolve(strict=False)
    if _paths_overlap(resolved, repository):
        raise AnalysisRibAnchorError(f"{field} 不得与代码仓库重叠")
    for source in source_roots:
        normalized = source.expanduser().resolve(strict=False)
        if _paths_overlap(resolved, normalized):
            raise AnalysisRibAnchorError(f"{field} 不得与只读来源重叠：{normalized}")
    if must_exist:
        _assert_root_directory(resolved, field)
    return resolved


def _existing_anchor_source_roots(root: Path) -> Tuple[Path, ...]:
    genesis = _load_genesis(root)
    artifact_root = genesis.get("artifact_root")
    prior = genesis.get("prior_raw_accounting")
    journal_root = prior.get("journal_root") if isinstance(prior, Mapping) else None
    if not isinstance(artifact_root, str) or not artifact_root:
        raise AnalysisRibAnchorError("anchor genesis 缺少只读 artifact_root")
    if not isinstance(journal_root, str) or not journal_root:
        raise AnalysisRibAnchorError("anchor genesis 缺少只读 journal_root")
    return (Path(artifact_root), Path(journal_root))


def _assert_existing_anchor_mutation_root(path: Path, field: str) -> Path:
    root = path.expanduser().resolve(strict=False)
    _assert_mutation_root(root, field)
    sources = _existing_anchor_source_roots(root)
    return _assert_mutation_root(root, field, source_roots=sources)


def load_verified_prior_raw_accounting(
    journal_root: os.PathLike[str] | str,
    *,
    bindings: Mapping[str, Any],
) -> VerifiedPriorRawAccounting:
    """核验已完成 full-window journal，并冻结其 terminal raw 累计值。

    这里刻意不接受整数。analysis-RIB 预算只能接在一个无 ACTIVE、全 receipt
    ancestry 已验证、且 ``completed == total`` 的 UPDATE journal 后面。
    """

    frozen_bindings = _bindings(bindings)
    root = Path(journal_root).expanduser().resolve(strict=False)
    _assert_root_directory(root, "full_window_journal_root")
    try:
        head = load_full_window_head(
            root,
            expected_bindings=frozen_bindings,
            recover_committed_successor=False,
        )
        frozen = frozen_journal_head(head)
    except (FullWindowJournalError, OSError, ValueError) as error:
        raise AnalysisRibAnchorError(
            "prior raw 必须来自已验证的 full-window frozen journal"
        ) from error
    completed = _nonnegative(
        frozen.get("completed_artifact_count"), "frozen.completed_artifact_count"
    )
    total = _positive(frozen.get("total_artifacts"), "frozen.total_artifacts")
    if completed != total:
        raise AnalysisRibAnchorError(
            "prior raw journal 尚未完成全部 UPDATE artifact，禁止并行拆分 50GB 预算"
        )
    if frozen.get("bindings") != frozen_bindings:
        raise AnalysisRibAnchorError("prior raw journal bindings 不一致")
    terminal_ref = frozen.get("terminal_receipt_ref")
    if not isinstance(terminal_ref, Mapping) or set(terminal_ref) != {
        "path",
        "sha256",
    }:
        raise AnalysisRibAnchorError("prior raw terminal receipt ref 不闭合")
    terminal = {
        "path": _safe_relative(
            terminal_ref.get("path"), "terminal_receipt_ref.path"
        ).as_posix(),
        "sha256": _sha(
            terminal_ref.get("sha256"), "terminal_receipt_ref.sha256"
        ),
    }
    seed_shards_raw = frozen.get("genesis_seed_shards")
    if not isinstance(seed_shards_raw, list):
        raise AnalysisRibAnchorError("full-window genesis seed shards 缺失")
    seed_shards = []
    for row in seed_shards_raw:
        if not isinstance(row, Mapping) or set(row) != {
            "kind",
            "path",
            "sha256",
            "size_bytes",
            "record_count",
        }:
            raise AnalysisRibAnchorError("full-window genesis seed shard ref 非法")
        seed_shards.append(
            {
                "kind": row.get("kind"),
                "path": _safe_relative(
                    row.get("path"), "genesis_seed_shard.path"
                ).as_posix(),
                "sha256": _sha(
                    row.get("sha256"), "genesis_seed_shard.sha256"
                ),
                "size_bytes": _nonnegative(
                    row.get("size_bytes"), "genesis_seed_shard.size_bytes"
                ),
                "record_count": _nonnegative(
                    row.get("record_count"), "genesis_seed_shard.record_count"
                ),
            }
        )
    if [row["kind"] for row in seed_shards] != [
        "seed_bootstrap_attestation",
        "seed_raw_record_refs",
        "seed_route_events",
    ]:
        raise AnalysisRibAnchorError(
            "full-window genesis 必须精确冻结 bootstrap/raw refs/RouteEvent 三类 seed evidence"
        )
    semantic = {
        "journal_root": str(root),
        "run_id": frozen.get("run_id"),
        "bindings": frozen_bindings,
        "terminal_receipt_ref": terminal,
        "genesis_seed_shards": seed_shards,
        "shard_chain_sha256": _sha(
            frozen.get("shard_chain_sha256"), "frozen.shard_chain_sha256"
        ),
        "completed_artifact_count": completed,
        "total_artifacts": total,
        "verified_receipt_count": _positive(
            frozen.get("verified_receipt_count"), "frozen.verified_receipt_count"
        ),
        "cumulative_reserved_raw_bytes": _nonnegative(
            frozen.get("cumulative_reserved_raw_bytes"),
            "frozen.cumulative_reserved_raw_bytes",
        ),
    }
    if not isinstance(semantic["run_id"], str) or not semantic["run_id"]:
        raise AnalysisRibAnchorError("prior raw journal run_id 非法")
    proof = _fingerprinted(PRIOR_RAW_ACCOUNTING_SCHEMA_VERSION, semantic)
    return VerifiedPriorRawAccounting(
        journal_root=str(root),
        run_id=str(semantic["run_id"]),
        bindings=frozen_bindings,
        terminal_receipt_ref=terminal,
        genesis_seed_shards=tuple(seed_shards),
        shard_chain_sha256=str(semantic["shard_chain_sha256"]),
        completed_artifact_count=completed,
        total_artifacts=total,
        verified_receipt_count=int(semantic["verified_receipt_count"]),
        cumulative_reserved_raw_bytes=int(
            semantic["cumulative_reserved_raw_bytes"]
        ),
        fingerprint_sha256=proof["fingerprint_sha256"],
    )


def compute_prior_journal_verification_candidate(
    journal_root: os.PathLike[str] | str,
    *,
    bindings: Mapping[str, Any],
) -> Mapping[str, Any]:
    """显式深验一次完整 UPDATE journal，生成确定性的 sealed 候选。

    这是唯一允许调用 ``frozen_journal_head`` 全 ancestry 深验的 analysis
    入口。候选不含墙钟时间，因此相同 terminal 身份会得到相同内容哈希。
    """

    frozen_bindings = _bindings(bindings)
    root = Path(journal_root).expanduser().resolve(strict=False)
    _assert_root_directory(root, "full_window_journal_root")
    accounting = load_verified_prior_raw_accounting(
        root, bindings=frozen_bindings
    )
    current_raw, current_sha = _read_regular(
        root / "CURRENT", maximum_bytes=1024 * 1024
    )
    terminal_path = root / _safe_relative(
        accounting.terminal_receipt_ref["path"], "terminal_receipt_ref.path"
    )
    terminal_raw, terminal_sha = _read_regular(
        terminal_path, maximum_bytes=16 * 1024 * 1024
    )
    if terminal_sha != accounting.terminal_receipt_ref["sha256"]:
        raise AnalysisRibAnchorError("深验后 terminal receipt SHA256 漂移")
    return _fingerprinted(
        PRIOR_JOURNAL_VERIFICATION_SCHEMA_VERSION,
        {
            "verification_scope": (
                "one_time_full_terminal_receipt_ancestry_attempt_outcome_shard_"
                "and_raw_accumulator_deep_verification"
            ),
            "prior_raw_accounting": accounting.to_dict(),
            "lightweight_terminal_identity": {
                "current_ref": {
                    "path": "CURRENT",
                    "sha256": current_sha,
                    "size_bytes": len(current_raw),
                },
                "terminal_receipt_ref": {
                    **dict(accounting.terminal_receipt_ref),
                    "size_bytes": len(terminal_raw),
                },
                "run_id": accounting.run_id,
                "completed_artifact_count": accounting.completed_artifact_count,
                "total_artifacts": accounting.total_artifacts,
                "shard_chain_sha256": accounting.shard_chain_sha256,
            },
            "database_writes": 0,
        },
    )


def _verify_prior_journal_candidate_light(
    value: Any,
    *,
    journal_root: os.PathLike[str] | str,
    bindings: Mapping[str, Any],
) -> VerifiedPriorRawAccounting:
    """只读取 receipt、CURRENT 与 terminal receipt，不遍历 ancestry。"""

    candidate = _verify_fingerprint(
        value,
        PRIOR_JOURNAL_VERIFICATION_SCHEMA_VERSION,
        "prior journal verification receipt",
    )
    base_fields = {
        "schema_version",
        "verification_scope",
        "prior_raw_accounting",
        "lightweight_terminal_identity",
        "database_writes",
        "fingerprint_sha256",
    }
    if set(candidate) not in {frozenset(base_fields), frozenset((*base_fields, "supervision"))}:
        raise AnalysisRibAnchorError("prior journal verification receipt 字段不闭合")
    if (
        candidate.get("verification_scope")
        != "one_time_full_terminal_receipt_ancestry_attempt_outcome_shard_and_raw_accumulator_deep_verification"
        or candidate.get("database_writes") != 0
    ):
        raise AnalysisRibAnchorError("prior journal verification receipt 语义非法")
    supervision = candidate.get("supervision")
    if supervision is not None and (
        not isinstance(supervision, Mapping)
        or supervision.get("semantics")
        != "independent_process_group_420_observe_540_term_590_kill_596_exit_v1"
        or supervision.get("policy")
        != {
            "observation_seconds": DEFAULT_PLANNED_CHECKPOINT_SECONDS,
            "term_seconds": DEFAULT_SOFT_STOP_SECONDS,
            "kill_seconds": DEFAULT_HARD_STOP_SECONDS,
            "parent_exit_seconds_exclusive": (
                DEFAULT_EXTERNAL_SHELL_HARD_STOP_SECONDS
            ),
        }
        or supervision.get("actions")
        != {
            "term_sent": False,
            "kill_sent": False,
            "child_reaped_within_parent_deadline": True,
        }
        or supervision.get("child_exit_code") != 0
        or supervision.get("database_writes") != 0
        or not isinstance(supervision.get("elapsed_seconds"), (int, float))
        or isinstance(supervision.get("elapsed_seconds"), bool)
        or not 0 < float(supervision["elapsed_seconds"]) < DEFAULT_SOFT_STOP_SECONDS
    ):
        raise AnalysisRibAnchorError("prior journal 深验 supervision 证据不闭合")
    frozen_bindings = _bindings(bindings)
    accounting = _prior_raw_accounting_from_payload(
        candidate.get("prior_raw_accounting")
    )
    root = Path(journal_root).expanduser().resolve(strict=False)
    _assert_root_directory(root, "full_window_journal_root")
    if (
        Path(accounting.journal_root).expanduser().resolve(strict=False) != root
        or dict(accounting.bindings) != frozen_bindings
    ):
        raise AnalysisRibAnchorError("prior verification receipt journal/bindings 不一致")
    identity = candidate.get("lightweight_terminal_identity")
    if not isinstance(identity, Mapping) or set(identity) != {
        "current_ref",
        "terminal_receipt_ref",
        "run_id",
        "completed_artifact_count",
        "total_artifacts",
        "shard_chain_sha256",
    }:
        raise AnalysisRibAnchorError("prior verification lightweight identity 不闭合")
    current_ref = identity.get("current_ref")
    terminal_ref = identity.get("terminal_receipt_ref")
    if (
        not isinstance(current_ref, Mapping)
        or set(current_ref) != {"path", "sha256", "size_bytes"}
        or current_ref.get("path") != "CURRENT"
        or not isinstance(terminal_ref, Mapping)
        or set(terminal_ref) != {"path", "sha256", "size_bytes"}
    ):
        raise AnalysisRibAnchorError("prior verification terminal refs 非法")
    current_raw, current_sha = _read_regular(
        root / "CURRENT", maximum_bytes=1024 * 1024
    )
    if (
        current_sha != _sha(current_ref.get("sha256"), "current_ref.sha256")
        or len(current_raw)
        != _nonnegative(current_ref.get("size_bytes"), "current_ref.size_bytes")
    ):
        raise AnalysisRibAnchorError("CURRENT 身份在深验后漂移")
    try:
        current_payload = json.loads(current_raw.decode("utf-8"))
        current = _full_window_journal._verify_fingerprint(
            current_payload,
            _full_window_journal.CURRENT_SCHEMA_VERSION,
            "CURRENT",
        )
    except (UnicodeDecodeError, json.JSONDecodeError, FullWindowJournalError) as error:
        raise AnalysisRibAnchorError("CURRENT 轻量身份非法") from error
    normalized_terminal = {
        "path": _safe_relative(
            terminal_ref.get("path"), "terminal_receipt_ref.path"
        ).as_posix(),
        "sha256": _sha(
            terminal_ref.get("sha256"), "terminal_receipt_ref.sha256"
        ),
    }
    terminal_raw, terminal_sha = _read_regular(
        root / normalized_terminal["path"], maximum_bytes=16 * 1024 * 1024
    )
    if (
        terminal_sha != normalized_terminal["sha256"]
        or len(terminal_raw)
        != _nonnegative(
            terminal_ref.get("size_bytes"), "terminal_receipt_ref.size_bytes"
        )
    ):
        raise AnalysisRibAnchorError("terminal receipt 身份在深验后漂移")
    try:
        terminal_payload = json.loads(terminal_raw.decode("utf-8"))
        terminal = _full_window_journal._verify_fingerprint(
            terminal_payload,
            _full_window_journal.BOUNDARY_RECEIPT_SCHEMA_VERSION,
            "terminal boundary receipt",
        )
    except (UnicodeDecodeError, json.JSONDecodeError, FullWindowJournalError) as error:
        raise AnalysisRibAnchorError("terminal receipt 轻量身份非法") from error
    if (
        current.get("receipt_ref") != normalized_terminal
        or current.get("run_id") != accounting.run_id
        or current.get("sequence") != accounting.completed_artifact_count
        or terminal.get("run_id") != accounting.run_id
        or terminal.get("bindings") != frozen_bindings
        or terminal.get("sequence") != accounting.completed_artifact_count
        or terminal.get("next_artifact_index")
        != accounting.completed_artifact_count
        or terminal.get("total_artifacts") != accounting.total_artifacts
        or terminal.get("shard_chain_sha256") != accounting.shard_chain_sha256
        or identity.get("run_id") != accounting.run_id
        or identity.get("completed_artifact_count")
        != accounting.completed_artifact_count
        or identity.get("total_artifacts") != accounting.total_artifacts
        or identity.get("shard_chain_sha256") != accounting.shard_chain_sha256
        or normalized_terminal != dict(accounting.terminal_receipt_ref)
    ):
        raise AnalysisRibAnchorError("prior terminal ref/SHA/run/sequence 身份漂移")
    try:
        active = _full_window_journal._load_active_attempt(root)
    except FullWindowJournalError as error:
        raise AnalysisRibAnchorError("prior journal ACTIVE 状态不可轻量核验") from error
    if active is not None:
        raise AnalysisRibAnchorError("prior journal 深验后出现 ACTIVE attempt")
    return accounting


def publish_prior_journal_verification_receipt(
    verification_root: os.PathLike[str] | str,
    *,
    candidate: Mapping[str, Any],
    journal_root: os.PathLike[str] | str,
    bindings: Mapping[str, Any],
    supervision: Mapping[str, Any],
) -> Mapping[str, Any]:
    """轻验候选后在安全目录 create-only 发布内容寻址 receipt。"""

    root = _assert_mutation_root(
        Path(verification_root),
        "prior_verification_root",
        source_roots=(Path(journal_root),),
    )
    _verify_prior_journal_candidate_light(
        candidate, journal_root=journal_root, bindings=bindings
    )
    candidate_semantic = {
        key: value
        for key, value in dict(candidate).items()
        if key not in {"schema_version", "fingerprint_sha256", "supervision"}
    }
    receipt = _fingerprinted(
        PRIOR_JOURNAL_VERIFICATION_SCHEMA_VERSION,
        {**candidate_semantic, "supervision": dict(supervision)},
    )
    accounting = _verify_prior_journal_candidate_light(
        receipt, journal_root=journal_root, bindings=bindings
    )
    encoded = (canonical_json(receipt) + "\n").encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    relative = f"prior-journal-verification-{digest}.json"
    target = root / relative
    try:
        published = write_canonical_json(
            target,
            receipt,
            kind="analysis_rib_prior_journal_verification",
            mode=0o440,
        )
    except FileExistsError:
        existing, existing_sha = _read_regular(
            target, maximum_bytes=max(len(encoded), 4 * 1024 * 1024)
        )
        if existing != encoded or existing_sha != digest:
            raise AnalysisRibAnchorError(
                "prior journal 内容寻址 receipt 已存在但内容冲突"
            )
        published = PublishedArtifact(
            path=target,
            sha256=existing_sha,
            size_bytes=len(existing),
            record_count=1,
            kind="analysis_rib_prior_journal_verification",
        )
    if published.sha256 != digest:
        raise AnalysisRibAnchorError("prior journal receipt 发布后 SHA256 漂移")
    return {
        "path": str(target),
        "sha256": digest,
        "prior_raw_accounting": accounting.to_dict(),
        "verification_scope": receipt["verification_scope"],
        "supervision": dict(supervision),
        "database_writes": 0,
    }


def load_prior_raw_accounting_from_verification_receipt(
    receipt_path: os.PathLike[str] | str,
    *,
    journal_root: os.PathLike[str] | str,
    bindings: Mapping[str, Any],
) -> VerifiedPriorRawAccounting:
    """后续 analysis 子命令的轻量入口；绝不调用 full ancestry verifier。"""

    path = Path(receipt_path).expanduser().resolve(strict=False)
    raw, digest = _read_regular(path, maximum_bytes=4 * 1024 * 1024)
    expected_name = f"prior-journal-verification-{digest}.json"
    if path.name != expected_name:
        raise AnalysisRibAnchorError("prior verification receipt 不是内容寻址路径")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisRibAnchorError("prior verification receipt JSON 非法") from error
    if (
        not isinstance(value, Mapping)
        or raw != (canonical_json(dict(value)) + "\n").encode("utf-8")
    ):
        raise AnalysisRibAnchorError("prior verification receipt 不是规范 JSON")
    if "supervision" not in value:
        raise AnalysisRibAnchorError("prior verification receipt 缺少 supervisor 证据")
    return _verify_prior_journal_candidate_light(
        value, journal_root=journal_root, bindings=bindings
    )


def _refresh_prior_raw_accounting(
    value: VerifiedPriorRawAccounting,
    *,
    bindings: Mapping[str, Any],
) -> VerifiedPriorRawAccounting:
    if not isinstance(value, VerifiedPriorRawAccounting):
        raise AnalysisRibAnchorError(
            "prior_raw_accounting 必须由 frozen journal 核验接口产生"
        )
    if value.bindings != _bindings(bindings):
        raise AnalysisRibAnchorError("prior raw accounting bindings 不一致")
    return value


def _prior_raw_accounting_from_payload(
    value: Any,
) -> VerifiedPriorRawAccounting:
    payload = _verify_fingerprint(
        value, PRIOR_RAW_ACCOUNTING_SCHEMA_VERSION, "prior_raw_accounting"
    )
    required = {
        "schema_version",
        "journal_root",
        "run_id",
        "bindings",
        "terminal_receipt_ref",
        "genesis_seed_shards",
        "shard_chain_sha256",
        "completed_artifact_count",
        "total_artifacts",
        "verified_receipt_count",
        "cumulative_reserved_raw_bytes",
        "fingerprint_sha256",
    }
    if set(payload) != required:
        raise AnalysisRibAnchorError("prior_raw_accounting 字段不闭合")
    root = payload.get("journal_root")
    run_id = payload.get("run_id")
    terminal = payload.get("terminal_receipt_ref")
    if not isinstance(root, str) or not root or not isinstance(run_id, str) or not run_id:
        raise AnalysisRibAnchorError("prior_raw_accounting journal/run 身份非法")
    if not isinstance(terminal, Mapping) or set(terminal) != {"path", "sha256"}:
        raise AnalysisRibAnchorError("prior_raw_accounting terminal ref 非法")
    normalized_terminal = {
        "path": _safe_relative(terminal.get("path"), "terminal.path").as_posix(),
        "sha256": _sha(terminal.get("sha256"), "terminal.sha256"),
    }
    shards_raw = payload.get("genesis_seed_shards")
    if not isinstance(shards_raw, list) or any(
        not isinstance(row, Mapping) for row in shards_raw
    ):
        raise AnalysisRibAnchorError("prior_raw_accounting seed shards 非法")
    normalized_shards = tuple(dict(row) for row in shards_raw)
    return VerifiedPriorRawAccounting(
        journal_root=root,
        run_id=run_id,
        bindings=_bindings(payload.get("bindings")),
        terminal_receipt_ref=normalized_terminal,
        genesis_seed_shards=normalized_shards,
        shard_chain_sha256=_sha(
            payload.get("shard_chain_sha256"), "shard_chain_sha256"
        ),
        completed_artifact_count=_nonnegative(
            payload.get("completed_artifact_count"), "completed_artifact_count"
        ),
        total_artifacts=_positive(payload.get("total_artifacts"), "total_artifacts"),
        verified_receipt_count=_positive(
            payload.get("verified_receipt_count"), "verified_receipt_count"
        ),
        cumulative_reserved_raw_bytes=_nonnegative(
            payload.get("cumulative_reserved_raw_bytes"),
            "cumulative_reserved_raw_bytes",
        ),
        fingerprint_sha256=_sha(
            payload.get("fingerprint_sha256"), "fingerprint_sha256"
        ),
    )


def _retention_policy_evidence(
    union: RawRetentionMappingUnion,
    *,
    bundle_sha256: str,
) -> dict[str, Any]:
    views = []
    for view in union.views:
        assignment_sha = hashlib.sha256(
            canonical_json([asdict(row) for row in view.assignments]).encode("utf-8")
        ).hexdigest()
        views.append(
            {
                "view": view.view,
                "source_sha256": view.source_sha256,
                "source_ref": view.source_ref,
                "assignment_semantic_sha256": assignment_sha,
            }
        )
    return _fingerprinted(
        RETENTION_POLICY_SCHEMA_VERSION,
        {
            "mapping_bundle_sha256": _sha(
                bundle_sha256, "retention.mapping_bundle_sha256"
            ),
            "target_country": union.target_country,
            "union_semantics": RAW_RETENTION_UNION_SEMANTICS,
            "unknown_or_conflict_policy": "retain_unless_both_views_explicit_non_target",
            "views": views,
            "explicit_target_asns": list(union.explicit_target_asns),
        },
    )


def build_analysis_rib_retention_policy(
    compatible_snapshot: Mapping[str, Any],
    revised_snapshot: Mapping[str, Any],
    *,
    bindings: Mapping[str, Any],
) -> AnalysisRibRetentionPolicy:
    """从两份冻结 mapping snapshot 内部构造唯一 raw-retention 并集。"""

    frozen_bindings = _bindings(bindings)
    try:
        bundle_sha = mapping_bundle_sha256(
            compatible_snapshot, revised_snapshot
        )
        if bundle_sha != frozen_bindings["mapping_sha256"]:
            raise AnalysisRibAnchorError(
                "compatible/revised mapping bundle 与冻结 binding 不一致"
            )
        compatible = mapping_view_from_frozen_snapshot(compatible_snapshot)
        revised = mapping_view_from_revised_snapshot(
            revised_snapshot, compatible_snapshot
        )
        union = build_raw_retention_mapping_union((compatible, revised))
    except AnalysisRibAnchorError:
        raise
    except (TypeError, ValueError) as error:
        raise AnalysisRibAnchorError(
            "compatible+revised raw retention union 无法闭合"
        ) from error
    evidence = _retention_policy_evidence(union, bundle_sha256=bundle_sha)
    return AnalysisRibRetentionPolicy(bundle_sha, union, evidence)


def _validate_retention_policy(
    value: AnalysisRibRetentionPolicy,
    *,
    bindings: Mapping[str, Any],
) -> dict[str, Any]:
    frozen_bindings = _bindings(bindings)
    if not isinstance(value, AnalysisRibRetentionPolicy):
        raise AnalysisRibAnchorError(
            "retention_policy 必须由 compatible+revised 冻结快照构造"
        )
    if value.mapping_bundle_sha256 != frozen_bindings["mapping_sha256"]:
        raise AnalysisRibAnchorError("retention policy mapping binding 不一致")
    expected = _retention_policy_evidence(
        value.union, bundle_sha256=value.mapping_bundle_sha256
    )
    if value.to_dict() != expected:
        raise AnalysisRibAnchorError("retention policy 证据与并集对象不一致")
    _verify_fingerprint(expected, RETENTION_POLICY_SCHEMA_VERSION, "retention policy")
    return expected


def initialize_anchor_workspace(
    anchor_root: os.PathLike[str] | str,
    *,
    artifact_root: os.PathLike[str] | str,
    plan: AnalysisRibPlan,
    bindings: Mapping[str, Any],
    retention_policy: AnalysisRibRetentionPolicy,
    max_raw_read_bytes: int = DEFAULT_MAX_RAW_READ_BYTES,
) -> Mapping[str, Any]:
    """create-only 初始化独立 anchor 根和 raw reservation genesis。"""

    if not isinstance(plan, AnalysisRibPlan):
        raise AnalysisRibAnchorError("plan 必须是 AnalysisRibPlan")
    frozen_bindings = _bindings(bindings)
    retention_evidence = _validate_retention_policy(
        retention_policy, bindings=frozen_bindings
    )
    if frozen_bindings["profile_sha256"] != plan.profile_sha256:
        raise AnalysisRibAnchorError("bindings.profile_sha256 与计划不一致")
    if (
        frozen_bindings["input_selection_sha256"]
        != plan.selection_semantic_sha256
    ):
        raise AnalysisRibAnchorError("bindings.input_selection_sha256 与计划不一致")
    accounting = _refresh_prior_raw_accounting(
        plan.prior_raw_accounting, bindings=frozen_bindings
    )
    if accounting.cumulative_reserved_raw_bytes != plan.prior_raw_read_bytes:
        raise AnalysisRibAnchorError("计划 prior raw 与 frozen journal 累计不一致")
    if not plan.execution_allowed:
        raise AnalysisRibAnchorError("完整 22 张 RIB 的累计 raw 计划未通过排他门")
    raw_limit = _positive(max_raw_read_bytes, "max_raw_read_bytes")
    if plan.projected_cumulative_raw_read_bytes >= raw_limit:
        raise AnalysisRibAnchorError("累计 raw 计划达到或超过排他上限")

    root = Path(anchor_root).expanduser().resolve(strict=False)
    raw_root = Path(artifact_root).expanduser().resolve(strict=False)
    _assert_root_directory(raw_root, "artifact_root")
    journal_root = Path(accounting.journal_root).expanduser().resolve(strict=False)
    _assert_mutation_root(
        root,
        "anchor_root",
        source_roots=(raw_root, journal_root),
        must_exist=False,
    )
    try:
        root.mkdir(mode=0o750, parents=False, exist_ok=False)
        for name in (
            "ledger",
            "spools",
            "shards",
            "checkpoints",
            "receipts",
            "retirements",
            "raw-open-claims",
            "execution",
            "execution/attempts",
            "execution/outcomes",
            "execution/supervisors",
        ):
            (root / name).mkdir(mode=0o750, exist_ok=False)
    except OSError as error:
        raise AnalysisRibAnchorError("anchor_root 必须以 create-only 方式初始化") from error
    (root / "ledger" / "LOCK").touch(mode=0o600, exist_ok=False)
    (root / "execution" / "LOCK").touch(mode=0o600, exist_ok=False)
    semantic = {
        "selection_id": plan.selection_id,
        "bindings": frozen_bindings,
        "artifacts": [item.to_dict() for item in plan.artifacts],
        "artifact_root": str(raw_root),
        "prior_raw_accounting": accounting.to_dict(),
        "retention_policy": retention_evidence,
        "prior_raw_read_bytes": plan.prior_raw_read_bytes,
        "max_raw_read_bytes_exclusive": raw_limit,
        "reservation_policy": "create_only_full_compressed_artifact_no_refund_v1",
        "database_writes": 0,
    }
    genesis = _fingerprinted(RAW_GENESIS_SCHEMA_VERSION, semantic)
    write_canonical_json(
        root / "ledger" / "GENESIS.json",
        genesis,
        kind="analysis_rib_raw_genesis",
        mode=0o440,
    )
    return genesis


def _load_genesis(root: Path) -> dict[str, Any]:
    return _load_canonical_json(
        root / "ledger" / "GENESIS.json",
        schema_version=RAW_GENESIS_SCHEMA_VERSION,
        maximum_bytes=1024 * 1024,
    )


def _reservation_paths(root: Path) -> Tuple[Path, ...]:
    return tuple(sorted((root / "ledger").glob("reservation-*.json")))


def _load_reservations(root: Path) -> Tuple[dict[str, Any], ...]:
    genesis = _load_genesis(root)
    previous_sha = hashlib.sha256(
        (canonical_json(genesis) + "\n").encode("utf-8")
    ).hexdigest()
    cumulative = _nonnegative(
        genesis.get("prior_raw_read_bytes"), "genesis.prior_raw_read_bytes"
    )
    values: list[dict[str, Any]] = []
    for expected_sequence, path in enumerate(_reservation_paths(root), start=1):
        receipt = _load_canonical_json(
            path,
            schema_version=RAW_RESERVATION_SCHEMA_VERSION,
            maximum_bytes=1024 * 1024,
        )
        if (
            receipt.get("sequence") != expected_sequence
            or receipt.get("previous_ledger_file_sha256") != previous_sha
            or receipt.get("cumulative_before") != cumulative
        ):
            raise AnalysisRibAnchorError("raw reservation ledger 链或累计值不连续")
        reserved = _positive(receipt.get("reserved_raw_bytes"), "reserved_raw_bytes")
        cumulative += reserved
        if receipt.get("cumulative_after") != cumulative:
            raise AnalysisRibAnchorError("raw reservation cumulative_after 不一致")
        raw, file_sha = _read_regular(path, maximum_bytes=1024 * 1024)
        del raw
        previous_sha = file_sha
        values.append(receipt)
    return tuple(values)


def cumulative_reserved_raw_bytes(anchor_root: os.PathLike[str] | str) -> int:
    root = Path(anchor_root)
    _assert_root_directory(root, "anchor_root")
    genesis = _load_genesis(root)
    reservations = _load_reservations(root)
    if reservations:
        return int(reservations[-1]["cumulative_after"])
    return int(genesis["prior_raw_read_bytes"])


def reserve_raw_read(
    anchor_root: os.PathLike[str] | str,
    descriptor: AnalysisRibDescriptor,
    *,
    attempt_id: Optional[str] = None,
) -> RawReservationToken:
    """在打开压缩 RIB 前 create-only 预留完整制品字节；永不退款。"""

    if not isinstance(descriptor, AnalysisRibDescriptor):
        raise AnalysisRibAnchorError("descriptor 类型非法")
    if descriptor.ingestion_mode != "new_raw":
        raise AnalysisRibAnchorError(
            "state-seed anchor 必须从 full-window genesis 导入，禁止重复读取 raw"
        )
    root = Path(anchor_root).expanduser().resolve(strict=False)
    _assert_existing_anchor_mutation_root(root, "anchor_root")
    identifier = attempt_id or "attempt_v1_" + secrets.token_hex(16)
    if not isinstance(identifier, str) or _ATTEMPT_ID_RE.fullmatch(identifier) is None:
        raise AnalysisRibAnchorError("attempt_id 非法")
    lock_path = root / "ledger" / "LOCK"
    lock_descriptor = os.open(
        lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        if not stat.S_ISREG(os.fstat(lock_descriptor).st_mode):
            raise AnalysisRibAnchorError("raw ledger LOCK 必须是普通文件")
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        genesis = _load_genesis(root)
        reservations = _load_reservations(root)
        if any(row.get("attempt_id") == identifier for row in reservations):
            raise AnalysisRibAnchorError("attempt_id 已存在，create-only ledger 拒绝复用")
        authorized_artifacts = genesis.get("artifacts")
        if (
            not isinstance(authorized_artifacts, list)
            or descriptor.to_dict() not in authorized_artifacts
        ):
            raise AnalysisRibAnchorError("artifact 未列入冻结的 22-anchor 计划")
        cumulative_before = (
            int(reservations[-1]["cumulative_after"])
            if reservations
            else int(genesis["prior_raw_read_bytes"])
        )
        cumulative_after = cumulative_before + descriptor.size_bytes
        limit = _positive(
            genesis.get("max_raw_read_bytes_exclusive"),
            "genesis.max_raw_read_bytes_exclusive",
        )
        if cumulative_after >= limit:
            raise AnalysisRibAnchorError(
                "raw reservation 将达到或超过 50GB 排他上限，已在打开前拒绝"
            )
        previous_path = (
            _reservation_paths(root)[-1]
            if reservations
            else root / "ledger" / "GENESIS.json"
        )
        _raw, previous_sha = _read_regular(
            previous_path, maximum_bytes=1024 * 1024
        )
        sequence = len(reservations) + 1
        semantic = {
            "sequence": sequence,
            "attempt_id": identifier,
            "selection_id": genesis["selection_id"],
            "bindings": genesis["bindings"],
            "artifact": descriptor.to_dict(),
            "reserved_raw_bytes": descriptor.size_bytes,
            "cumulative_before": cumulative_before,
            "cumulative_after": cumulative_after,
            "previous_ledger_file_sha256": previous_sha,
            "refund_policy": "never_refund_failed_crashed_or_retried_attempt",
            "status": "reserved_outcome_unknown_until_anchor_receipt",
        }
        receipt = _fingerprinted(RAW_RESERVATION_SCHEMA_VERSION, semantic)
        relative = f"ledger/reservation-{sequence:04d}-{identifier}.json"
        published = write_canonical_json(
            root / relative,
            receipt,
            kind="analysis_rib_raw_reservation",
            mode=0o440,
        )
        return RawReservationToken(
            attempt_id=identifier,
            path=relative,
            sha256=published.sha256,
            sequence=sequence,
            descriptor=descriptor,
            reserved_raw_bytes=descriptor.size_bytes,
            cumulative_reserved_raw_bytes=cumulative_after,
        )
    finally:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(lock_descriptor)


def load_analysis_rib_reservation(
    anchor_root: os.PathLike[str] | str,
    attempt_id: str,
) -> RawReservationToken:
    """从不可变 ledger 重新构造 reservation token，供独立 resume 子进程使用。"""

    if not isinstance(attempt_id, str) or _ATTEMPT_ID_RE.fullmatch(attempt_id) is None:
        raise AnalysisRibAnchorError("attempt_id 非法")
    root = Path(anchor_root)
    _assert_root_directory(root, "anchor_root")
    reservations = _load_reservations(root)
    matches = [row for row in reservations if row.get("attempt_id") == attempt_id]
    if len(matches) != 1:
        raise AnalysisRibAnchorError("attempt_id 必须恰好对应一份 raw reservation")
    row = matches[0]
    descriptor = AnalysisRibDescriptor(**{
        key: row["artifact"][key]
        for key in (
            "anchor_index",
            "role",
            "ingestion_mode",
            "artifact_id",
            "file_sha256",
            "size_bytes",
            "collector_id",
            "artifact_time_utc",
            "relative_path",
            "compression",
        )
    })
    paths = [
        path
        for path in _reservation_paths(root)
        if path.name.endswith(f"-{attempt_id}.json")
    ]
    if len(paths) != 1:
        raise AnalysisRibAnchorError("reservation ledger 路径与 attempt_id 不唯一")
    _raw, file_sha = _read_regular(paths[0], maximum_bytes=1024 * 1024)
    token = RawReservationToken(
        attempt_id=attempt_id,
        path=paths[0].relative_to(root).as_posix(),
        sha256=file_sha,
        sequence=int(row["sequence"]),
        descriptor=descriptor,
        reserved_raw_bytes=int(row["reserved_raw_bytes"]),
        cumulative_reserved_raw_bytes=int(row["cumulative_after"]),
    )
    _verify_reservation(root, token)
    return token


def _verify_reservation(root: Path, token: RawReservationToken) -> Mapping[str, Any]:
    if not isinstance(token, RawReservationToken):
        raise AnalysisRibAnchorError("raw reservation token 类型非法")
    path = root / _safe_relative(token.path, "reservation.path")
    receipt = _load_canonical_json(
        path,
        schema_version=RAW_RESERVATION_SCHEMA_VERSION,
        maximum_bytes=1024 * 1024,
    )
    _raw, file_sha = _read_regular(path, maximum_bytes=1024 * 1024)
    if (
        file_sha != token.sha256
        or receipt.get("attempt_id") != token.attempt_id
        or receipt.get("sequence") != token.sequence
        or receipt.get("artifact") != token.descriptor.to_dict()
        or receipt.get("reserved_raw_bytes") != token.reserved_raw_bytes
        or receipt.get("cumulative_after")
        != token.cumulative_reserved_raw_bytes
    ):
        raise AnalysisRibAnchorError("raw reservation token 与 create-only 收据不一致")
    return receipt


def _claim_raw_open(
    root: Path,
    *,
    reservation: RawReservationToken,
    bindings: Mapping[str, str],
) -> Mapping[str, str]:
    """把 reservation 消耗为唯一一次 raw-open 权限；失败后不能复用。"""

    semantic = _fingerprinted(
        RAW_OPEN_CLAIM_SCHEMA_VERSION,
        {
            "attempt_id": reservation.attempt_id,
            "bindings": dict(bindings),
            "artifact": reservation.descriptor.to_dict(),
            "reservation_ref": {
                "path": reservation.path,
                "sha256": reservation.sha256,
                "sequence": reservation.sequence,
                "cumulative_reserved_raw_bytes": (
                    reservation.cumulative_reserved_raw_bytes
                ),
            },
            "authorization": "exactly_one_compressed_raw_open_attempt",
            "retry_policy": "new_reservation_required_after_any_failed_open_attempt",
        },
    )
    relative = f"raw-open-claims/raw-open-{reservation.attempt_id}.json"
    try:
        published = write_canonical_json(
            root / relative,
            semantic,
            kind="analysis_rib_raw_open_claim",
            mode=0o440,
        )
    except FileExistsError as error:
        raise AnalysisRibAnchorError(
            "raw reservation 已被一次 open attempt 消耗；重试必须新增 reservation"
        ) from error
    return {"path": relative, "sha256": published.sha256}


def _verify_raw_open_claim(
    root: Path,
    value: Mapping[str, Any],
    *,
    reservation: RawReservationToken,
    bindings: Mapping[str, str],
) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise AnalysisRibAnchorError("raw_open_claim_ref 字段不闭合")
    relative = _safe_relative(value.get("path"), "raw_open_claim_ref.path").as_posix()
    path = root / relative
    payload = _load_canonical_json(
        path,
        schema_version=RAW_OPEN_CLAIM_SCHEMA_VERSION,
        maximum_bytes=1024 * 1024,
    )
    _raw, file_sha = _read_regular(path, maximum_bytes=1024 * 1024)
    expected_reservation = {
        "path": reservation.path,
        "sha256": reservation.sha256,
        "sequence": reservation.sequence,
        "cumulative_reserved_raw_bytes": reservation.cumulative_reserved_raw_bytes,
    }
    if (
        file_sha != _sha(value.get("sha256"), "raw_open_claim_ref.sha256")
        or payload.get("attempt_id") != reservation.attempt_id
        or payload.get("bindings") != dict(bindings)
        or payload.get("artifact") != reservation.descriptor.to_dict()
        or payload.get("reservation_ref") != expected_reservation
    ):
        raise AnalysisRibAnchorError("raw open claim 与 reservation 不一致")
    return {"path": relative, "sha256": file_sha}


def _module_source_sha256(module_file: str) -> str:
    raw, digest = _read_regular(Path(module_file), maximum_bytes=16 * 1024 * 1024)
    if not raw:
        raise AnalysisRibAnchorError("parser source 文件为空")
    return digest


def build_parser_source_attestation(code_identity_sha256: str) -> Mapping[str, Any]:
    """绑定当前 RIB parser/adapter 源码，不依赖外部二进制或数据库。"""

    from . import rib_adapter, rib_parser  # 延迟导入用于读取真实模块文件。

    code_sha = _sha(code_identity_sha256, "code_identity_sha256")
    semantic = {
        "implementation": "domeye_native_rrc25_rib_parser_adapter",
        "parser_contract": "table_dump_and_table_dump_v2_record_boundary_v1",
        "rib_parser_source_sha256": _module_source_sha256(rib_parser.__file__),
        "rib_adapter_source_sha256": _module_source_sha256(rib_adapter.__file__),
        "code_identity_sha256": code_sha,
        "execution_policy": "in_process_native_source_no_external_binary",
    }
    return _fingerprinted(PARSER_ATTESTATION_SCHEMA_VERSION, semantic)


def _safe_artifact_path(root: Path, descriptor: AnalysisRibDescriptor) -> Path:
    _assert_root_directory(root, "artifact_root")
    relative = _safe_relative(descriptor.relative_path, "artifact.relative_path")
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as error:
            raise AnalysisRibAnchorError("原始制品父目录不存在") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise AnalysisRibAnchorError("原始制品父路径不得为符号链接")
    path = root.joinpath(*relative.parts)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise AnalysisRibAnchorError("原始 RIB 不存在或不可读") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise AnalysisRibAnchorError("原始 RIB 必须是非符号链接普通文件")
    if metadata.st_size != descriptor.size_bytes:
        raise AnalysisRibAnchorError("原始 RIB size 与 selection 不一致")
    return path


def _event_payload(event: Any) -> dict[str, Any]:
    return {
        "artifact_id": event.artifact_id,
        "file_sha256": event.file_sha256,
        "collector_id": event.collector_id,
        "artifact_slot_utc": event.artifact_slot_utc,
        "record_ordinal": event.record_ordinal,
        "element_ordinal": event.element_ordinal,
        "route_event_id": event.route_event_id,
        "event_time_utc": event.event_time_utc,
        "peer_ip": event.peer_ip,
        "peer_asn": event.peer_asn,
        "vp_id": event.vp_id,
        "action": event.action,
        "afi_safi": event.afi_safi,
        "prefix": event.prefix,
        "as_path": [
            {"segment_type": item.segment_type, "asns": list(item.asns)}
            for item in (event.as_path or ())
        ],
        "quality_flags": list(event.quality_flags),
    }


def _route_event_from_payload(value: Mapping[str, Any]) -> Any:
    required = {
        "artifact_id",
        "file_sha256",
        "collector_id",
        "artifact_slot_utc",
        "record_ordinal",
        "element_ordinal",
        "route_event_id",
        "event_time_utc",
        "peer_ip",
        "peer_asn",
        "vp_id",
        "action",
        "afi_safi",
        "prefix",
        "as_path",
        "quality_flags",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AnalysisRibAnchorError("RouteEvent shard 行字段不闭合")
    path_raw = value.get("as_path")
    flags_raw = value.get("quality_flags")
    if not isinstance(path_raw, list) or not isinstance(flags_raw, list):
        raise AnalysisRibAnchorError("RouteEvent AS_PATH/quality_flags 非法")
    segments = []
    for row in path_raw:
        if not isinstance(row, Mapping) or set(row) != {"segment_type", "asns"}:
            raise AnalysisRibAnchorError("RouteEvent AS_PATH segment 非法")
        asns = row.get("asns")
        if not isinstance(asns, list):
            raise AnalysisRibAnchorError("RouteEvent AS_PATH ASN 列表非法")
        segments.append(AsPathSegment(row.get("segment_type"), tuple(asns)))
    event = build_research_route_event(
        artifact_id=value.get("artifact_id"),
        file_sha256=value.get("file_sha256"),
        collector_id=value.get("collector_id"),
        artifact_slot_utc=value.get("artifact_slot_utc"),
        record_ordinal=value.get("record_ordinal"),
        element_ordinal=value.get("element_ordinal"),
        element=ParsedRouteElement(
            event_time_utc=value.get("event_time_utc"),
            peer_ip=value.get("peer_ip"),
            peer_asn=value.get("peer_asn"),
            action=value.get("action"),
            prefix=value.get("prefix"),
            afi_safi=value.get("afi_safi"),
            as_path=tuple(segments),
            quality_flags=tuple(flags_raw),
        ),
    )
    if _event_payload(event) != dict(value):
        raise AnalysisRibAnchorError("RouteEvent 行无法按规范身份无损重建")
    return event


def _projection_rows(state: RouteReplayState) -> Tuple[dict[str, Any], ...]:
    rows = []
    for entry in state.entries:
        origin = derive_origin_asns(entry.as_path)
        rows.append(
            {
                "collector_id": entry.key.collector_id,
                "vp_id": entry.key.vp_id,
                "afi_safi": entry.key.afi_safi,
                "prefix": entry.key.prefix,
                "peer_ip": entry.peer_ip,
                "peer_asn": entry.peer_asn,
                "as_path": [
                    {
                        "segment_type": segment.segment_type,
                        "asns": list(segment.asns),
                    }
                    for segment in entry.as_path
                ],
                "origin_state": origin.state,
                "origin_asns": list(origin.origins),
                "origin_reason": origin.reason,
                "quality_flags": list(entry.quality_flags),
            }
        )
    return tuple(rows)


def _projection_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        canonical_json(
            {"semantics": PROJECTION_SEMANTICS, "rows": list(rows)}
        ).encode("utf-8")
    ).hexdigest()


def build_source_independent_route_projection(
    state: RouteReplayState,
) -> Mapping[str, Any]:
    """生成 RIB/UPDATE 可共用、排除 provenance/time 的快照投影。"""

    if not isinstance(state, RouteReplayState):
        raise AnalysisRibAnchorError("state 必须是 RouteReplayState")
    rows = _projection_rows(state)
    return {
        "semantics": PROJECTION_SEMANTICS,
        "semantic_sha256": _projection_sha256(rows),
        "rows": list(rows),
    }


def build_update_boundary_snapshot(
    state: RouteReplayState,
    *,
    collector_id: str,
    boundary_at_utc: str,
) -> Mapping[str, Any]:
    """把 UPDATE 回放边界编码为可与独立 RIB anchor 对账的纯数据接口。"""

    if not isinstance(collector_id, str) or not collector_id:
        raise AnalysisRibAnchorError("collector_id 必须是非空字符串")
    boundary = _utc(boundary_at_utc, "boundary_at_utc").strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    if any(entry.key.collector_id != collector_id for entry in state.entries):
        raise AnalysisRibAnchorError("UPDATE state collector 与声明不一致")
    route_state = route_replay_state_to_payload(state)
    projection = build_source_independent_route_projection(state)
    return {
        "schema_version": "rrc25-update-boundary-snapshot/v1",
        "source_kind": "independent_update_replay_boundary",
        "collector_id": collector_id,
        "boundary_at_utc": boundary,
        "route_state_semantic_sha256": route_state[
            "state_fingerprint_sha256"
        ],
        "projection_semantic_sha256": projection["semantic_sha256"],
    }


def _checkpoint_binding(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    return dict(value.checkpoint_binding())


def _checkpoint_path(root: Path, payload: Mapping[str, Any]) -> Tuple[str, str]:
    _published, relative = _content_json(
        root, "checkpoints", "anchor-checkpoint", payload
    )
    _raw, file_sha = _read_regular(root / relative, maximum_bytes=2_000_000_000)
    return relative, file_sha


def _load_checkpoint(
    root: Path,
    path_value: os.PathLike[str] | str,
    *,
    descriptor: AnalysisRibDescriptor,
    bindings: Mapping[str, str],
) -> dict[str, Any]:
    path = Path(path_value)
    if path.is_absolute():
        try:
            relative = path.relative_to(root)
        except ValueError as error:
            raise AnalysisRibAnchorError("checkpoint_path 必须位于 anchor_root 内") from error
        path = root / _safe_relative(relative.as_posix(), "checkpoint_path")
    else:
        path = root / _safe_relative(str(path), "checkpoint_path")
    payload = _load_canonical_json(
        path,
        schema_version=CHECKPOINT_SCHEMA_VERSION,
        maximum_bytes=2_000_000_000,
    )
    if payload.get("artifact") != descriptor.to_dict():
        raise AnalysisRibAnchorError("checkpoint artifact 绑定不一致")
    if payload.get("bindings") != dict(bindings):
        raise AnalysisRibAnchorError("checkpoint bindings 不一致")
    if payload.get("position", {}).get("boundary") != "after_complete_physical_record":
        raise AnalysisRibAnchorError("checkpoint 不是完整 MRT record 边界")
    return payload


def _verify_shard_ref(root: Path, ref: Mapping[str, Any]) -> Tuple[dict[str, Any], ...]:
    required = {
        "kind",
        "artifact_id",
        "sequence",
        "path",
        "file_sha256",
        "size_bytes",
        "record_count",
        "records_sha256",
        "semantic_sha256",
    }
    if not isinstance(ref, Mapping) or set(ref) != required:
        raise AnalysisRibAnchorError("shard ref 字段不闭合")
    path = root / _safe_relative(ref["path"], "shard.path")
    raw, file_sha = _read_regular(path, maximum_bytes=2_000_000_000)
    if file_sha != _sha(ref["file_sha256"], "shard.file_sha256"):
        raise AnalysisRibAnchorError("shard 文件 SHA256 不一致")
    if len(raw) != _nonnegative(ref["size_bytes"], "shard.size_bytes"):
        raise AnalysisRibAnchorError("shard size 不一致")
    try:
        uncompressed = gzip.decompress(raw)
    except (OSError, EOFError) as error:
        raise AnalysisRibAnchorError("shard gzip EOF/CRC 校验失败") from error
    if uncompressed and not uncompressed.endswith(b"\n"):
        raise AnalysisRibAnchorError("shard JSONL 缺少完整换行边界")
    records = []
    for index, line in enumerate(uncompressed.splitlines()):
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AnalysisRibAnchorError("shard 含非法 JSONL") from error
        if not isinstance(value, Mapping):
            raise AnalysisRibAnchorError(f"shard[{index}] 必须是对象")
        if line != canonical_json(dict(value)).encode("utf-8"):
            raise AnalysisRibAnchorError("shard JSONL 不是规范编码")
        records.append(dict(value))
    if len(records) != ref["record_count"]:
        raise AnalysisRibAnchorError("shard record_count 不一致")
    semantic, records_sha = _shard_semantic(
        kind=ref["kind"],
        artifact_id=ref["artifact_id"],
        sequence=ref["sequence"],
        records=records,
    )
    if records_sha != ref["records_sha256"] or semantic != ref["semantic_sha256"]:
        raise AnalysisRibAnchorError("shard 语义 SHA256 不一致")
    return tuple(records)


def _consume_imported_seed_shard(
    accounting: VerifiedPriorRawAccounting,
    *,
    kind: str,
    consumer: Callable[[Mapping[str, Any]], None],
) -> Mapping[str, Any]:
    """流式读取已由 full-window ancestry 冻结的 seed genesis shard。"""

    matches = [
        dict(row) for row in accounting.genesis_seed_shards if row.get("kind") == kind
    ]
    if len(matches) != 1:
        raise AnalysisRibAnchorError(f"full-window seed shard {kind} 必须唯一")
    ref = matches[0]
    root = Path(accounting.journal_root)
    relative = _safe_relative(ref.get("path"), f"{kind}.path")
    path = root / relative
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AnalysisRibAnchorError(f"无法安全打开 imported seed shard：{kind}") from error
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AnalysisRibAnchorError("imported seed shard 必须是普通文件")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            size += len(block)
            if size > 5_000_000_000:
                raise AnalysisRibAnchorError("imported seed shard 达到 5GB 读取上限")
            digest.update(block)
        after_hash = os.fstat(descriptor)
        if _stable_identity(before) != _stable_identity(after_hash):
            raise AnalysisRibAnchorError("imported seed shard 在哈希期间变化")
    finally:
        os.close(descriptor)
    if digest.hexdigest() != ref.get("sha256") or size != ref.get("size_bytes"):
        raise AnalysisRibAnchorError("imported seed shard SHA256/size 不一致")

    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AnalysisRibAnchorError(f"无法复核 imported seed shard：{kind}") from error
    count = 0
    try:
        opened = os.fstat(descriptor)
        if _stable_identity(opened) != _stable_identity(before):
            raise AnalysisRibAnchorError("imported seed shard 在哈希与解码之间变化")
        with os.fdopen(descriptor, "rb", buffering=0) as raw:
            descriptor = -1
            try:
                with gzip.GzipFile(filename="", mode="rb", fileobj=raw) as stream:
                    for line in stream:
                        if not line.endswith(b"\n"):
                            raise AnalysisRibAnchorError(
                                "imported seed shard 存在不完整 JSONL 行"
                            )
                        try:
                            row = json.loads(line.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError) as error:
                            raise AnalysisRibAnchorError(
                                "imported seed shard JSONL 非法"
                            ) from error
                        if not isinstance(row, Mapping) or line != (
                            canonical_json(dict(row)) + "\n"
                        ).encode("utf-8"):
                            raise AnalysisRibAnchorError(
                                "imported seed shard 不是规范对象 JSONL"
                            )
                        consumer(dict(row))
                        count += 1
            except (OSError, EOFError) as error:
                raise AnalysisRibAnchorError(
                    "imported seed shard gzip EOF/CRC 校验失败"
                ) from error
            closed_identity = os.fstat(raw.fileno())
            if _stable_identity(opened) != _stable_identity(closed_identity):
                raise AnalysisRibAnchorError("imported seed shard 在解码期间变化")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if count != ref.get("record_count"):
        raise AnalysisRibAnchorError("imported seed shard record_count 不一致")
    return ref


def _seed_event_from_full_window_row(row: Mapping[str, Any]) -> Any:
    required = {
        "schema_version",
        "route_event_id",
        "artifact_id",
        "file_sha256",
        "collector_id",
        "artifact_slot_utc",
        "record_ordinal",
        "element_ordinal",
        "event_time_utc",
        "peer_ip",
        "peer_asn",
        "vp_id",
        "action",
        "afi_safi",
        "prefix",
        "as_path",
        "quality_flags",
        "raw_record_ref_id",
        "raw_record_ref_ids",
    }
    if set(row) != required:
        raise AnalysisRibAnchorError("full-window seed RouteEvent 行字段不闭合")
    normalized = {
        field: row[field]
        for field in (
            "artifact_id",
            "file_sha256",
            "collector_id",
            "artifact_slot_utc",
            "record_ordinal",
            "element_ordinal",
            "route_event_id",
            "event_time_utc",
            "peer_ip",
            "peer_asn",
            "vp_id",
            "action",
            "afi_safi",
            "prefix",
            "as_path",
            "quality_flags",
        )
    }
    event = _route_event_from_payload(normalized)
    raw_id = row.get("raw_record_ref_id")
    if row.get("raw_record_ref_ids") != [raw_id]:
        raise AnalysisRibAnchorError("seed RouteEvent raw ref ID 列表不闭合")
    return event


def _retire_spool(
    root: Path,
    *,
    descriptor: AnalysisRibDescriptor,
    spool_proof: Mapping[str, Any],
    anchor_receipt_path: str,
    anchor_receipt_sha256: str,
    crash_hook: Optional[Callable[[str], None]] = None,
) -> str:
    spool_path = root / _safe_relative(spool_proof["path"], "spool.path")
    verified = verify_rib_decompressed_spool(
        spool_path,
        expected_decompressed_sha256=spool_proof["sha256"],
        expected_decompressed_size_bytes=spool_proof["size_bytes"],
    )
    before = _stable_identity(spool_path.lstat())
    attempt = _fingerprinted(
        RETIREMENT_ATTEMPT_SCHEMA_VERSION,
        {
            "artifact_id": descriptor.artifact_id,
            "anchor_receipt_path": anchor_receipt_path,
            "anchor_receipt_sha256": anchor_receipt_sha256,
            "spool": {
                **dict(spool_proof),
                "verified_sha256": verified.sha256,
                "verified_size_bytes": verified.size_bytes,
                "stable_identity_before_unlink": before,
            },
            "status": "retirement_authorized_after_anchor_verified",
        },
    )
    attempt_artifact, attempt_relative = _content_json_idempotent(
        root, "retirements", "retirement-attempt", attempt
    )
    try:
        spool_path.unlink()
        directory_descriptor = os.open(spool_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as error:
        raise AnalysisRibAnchorError("已生成 spool 退役 attempt，但 unlink 失败") from error
    if spool_path.exists() or spool_path.is_symlink():
        raise AnalysisRibAnchorError("spool 退役后路径仍存在")
    if crash_hook is not None:
        crash_hook("after_spool_unlink_before_success")
    success = _fingerprinted(
        RETIREMENT_RECEIPT_SCHEMA_VERSION,
        {
            "artifact_id": descriptor.artifact_id,
            "anchor_receipt_path": anchor_receipt_path,
            "anchor_receipt_sha256": anchor_receipt_sha256,
            "attempt_ref": {
                "path": attempt_relative,
                "sha256": attempt_artifact.sha256,
            },
            "retired_spool": {
                "path": spool_proof["path"],
                "sha256": spool_proof["sha256"],
                "size_bytes": spool_proof["size_bytes"],
                "stable_identity_before_unlink": before,
            },
            "status": "retired_and_directory_fsynced",
        },
    )
    _published, relative = _content_json_idempotent(
        root, "retirements", "retirement-success", success
    )
    return relative


def _verify_and_replay_imported_seed(
    accounting: VerifiedPriorRawAccounting,
    descriptor: AnalysisRibDescriptor,
) -> Mapping[str, Any]:
    bootstrap_rows: list[Mapping[str, Any]] = []
    bootstrap_ref = _consume_imported_seed_shard(
        accounting,
        kind="seed_bootstrap_attestation",
        consumer=lambda row: bootstrap_rows.append(dict(row)),
    )
    if len(bootstrap_rows) != 1:
        raise AnalysisRibAnchorError("seed bootstrap attestation 必须恰有一条")
    bootstrap = bootstrap_rows[0]
    seed_artifact_ref = bootstrap.get("seed_artifact_ref")
    if not isinstance(seed_artifact_ref, Mapping) or (
        seed_artifact_ref.get("artifact_id") != descriptor.artifact_id
        or seed_artifact_ref.get("file_sha256") != descriptor.file_sha256
        or seed_artifact_ref.get("size_bytes") != descriptor.size_bytes
    ):
        raise AnalysisRibAnchorError("seed bootstrap artifact ref 与 analysis seed 不一致")
    if not isinstance(bootstrap.get("seed_parser"), Mapping) or not isinstance(
        bootstrap.get("seed_spool_attestation"), Mapping
    ):
        raise AnalysisRibAnchorError("seed bootstrap parser/spool attestation 缺失")
    state: Optional[RouteReplayState] = None
    pending_events = []
    coordinates: dict[str, Tuple[str, str, int, int]] = {}
    observed_vps: set[str] = set()

    def consume_event(row: Mapping[str, Any]) -> None:
        nonlocal state
        event = _seed_event_from_full_window_row(row)
        if (
            event.artifact_id != descriptor.artifact_id
            or event.file_sha256 != descriptor.file_sha256
            or event.collector_id != descriptor.collector_id
            or event.artifact_slot_utc != descriptor.artifact_time_utc
        ):
            raise AnalysisRibAnchorError("imported seed RouteEvent 越出 seed artifact")
        if event.route_event_id in coordinates:
            raise AnalysisRibAnchorError("imported seed RouteEvent 身份重复")
        coordinates[event.route_event_id] = (
            event.artifact_id,
            event.file_sha256,
            event.record_ordinal,
            event.element_ordinal,
        )
        observed_vps.add(event.vp_id)
        pending_events.append(event)
        if len(pending_events) >= DEFAULT_BATCH_ROUTE_EVENTS:
            state = extend_streaming_rib_seed(state, tuple(pending_events))
            pending_events.clear()

    event_ref = _consume_imported_seed_shard(
        accounting, kind="seed_route_events", consumer=consume_event
    )
    if pending_events:
        state = extend_streaming_rib_seed(state, tuple(pending_events))
    if state is None:
        state = extend_streaming_rib_seed(None, ())
    raw_ids: set[str] = set()
    physical: set[Tuple[str, int]] = set()

    def consume_raw(row: Mapping[str, Any]) -> None:
        required = {
            "schema_version",
            "raw_record_ref_id",
            "route_event_id",
            "artifact_id",
            "file_sha256",
            "artifact_slot_utc",
            "record_ordinal",
            "element_ordinal",
            "record_offset",
            "record_length",
            "record_hash",
            "raw_record_sha256",
            "verification_status",
            "verification_basis",
        }
        if set(row) != required:
            raise AnalysisRibAnchorError("full-window seed raw ref 行字段不闭合")
        if coordinates.get(row.get("route_event_id")) != (
            row.get("artifact_id"),
            row.get("file_sha256"),
            row.get("record_ordinal"),
            row.get("element_ordinal"),
        ):
            raise AnalysisRibAnchorError("imported seed raw ref 与 RouteEvent 不一致")
        raw_id = row.get("raw_record_ref_id")
        if not isinstance(raw_id, str) or raw_id in raw_ids:
            raise AnalysisRibAnchorError("imported seed raw ref 身份非法或重复")
        if (
            row.get("record_hash") != row.get("raw_record_sha256")
            or row.get("verification_status") != "verified"
        ):
            raise AnalysisRibAnchorError("imported seed raw physical record 未核验")
        raw_ids.add(raw_id)
        physical.add((str(row.get("artifact_id")), int(row.get("record_ordinal"))))

    raw_ref = _consume_imported_seed_shard(
        accounting, kind="seed_raw_record_refs", consumer=consume_raw
    )
    if len(raw_ids) != len(coordinates):
        raise AnalysisRibAnchorError("imported seed RouteEvent/raw ref 未一一闭合")
    state_payload = route_replay_state_to_payload(state)
    if bootstrap.get("seed_route_state") != state_payload:
        raise AnalysisRibAnchorError("seed RouteEvent 重放与 bootstrap route state 不一致")
    refs = [bootstrap_ref, event_ref, raw_ref]
    chain = hashlib.sha256(
        canonical_json([row["sha256"] for row in refs]).encode("utf-8")
    ).hexdigest()
    return {
        "bootstrap": bootstrap,
        "refs": refs,
        "shard_chain_sha256": chain,
        "state": state,
        "state_payload": state_payload,
        "observed_vp_ids": sorted(observed_vps),
        "route_event_count": len(coordinates),
        "raw_ref_count": len(raw_ids),
        "raw_record_count": len(physical),
    }


def _import_full_window_seed_anchor_unlocked(
    anchor_root: os.PathLike[str] | str,
    *,
    descriptor: AnalysisRibDescriptor,
    bindings: Mapping[str, Any],
    retention_policy: AnalysisRibRetentionPolicy,
    max_temporary_bytes: int = DEFAULT_MAX_TEMPORARY_BYTES,
) -> AnchorSegmentResult:
    """从已验证 full-window genesis 导入窗口起点 seed；绝不再打开 seed raw。"""

    root = Path(anchor_root)
    _assert_root_directory(root, "anchor_root")
    if descriptor.ingestion_mode != "imported_full_window_seed":
        raise AnalysisRibAnchorError("import 接口只接受窗口起点 state-seed descriptor")
    temporary_limit = _positive(max_temporary_bytes, "max_temporary_bytes")
    if temporary_limit > DEFAULT_MAX_TEMPORARY_BYTES:
        raise AnalysisRibAnchorError("analysis RIB 临时上限不得放宽到 5GB 以上")
    frozen_bindings = _bindings(bindings)
    genesis = _load_genesis(root)
    if genesis.get("bindings") != frozen_bindings:
        raise AnalysisRibAnchorError("imported seed bindings 与 genesis 不一致")
    retention_evidence = _validate_retention_policy(
        retention_policy, bindings=frozen_bindings
    )
    if genesis.get("retention_policy") != retention_evidence:
        raise AnalysisRibAnchorError("imported seed retention policy 与 genesis 不一致")
    accounting = _prior_raw_accounting_from_payload(
        genesis.get("prior_raw_accounting")
    )
    accounting = _refresh_prior_raw_accounting(
        accounting, bindings=frozen_bindings
    )
    existing = []
    for path in (root / "receipts").glob("anchor-*.json"):
        payload = _load_canonical_json(
            path,
            schema_version=ANCHOR_RECEIPT_SCHEMA_VERSION,
            maximum_bytes=64 * 1024 * 1024,
        )
        if payload.get("artifact", {}).get("artifact_id") == descriptor.artifact_id:
            existing.append(path)
    if existing:
        raise AnalysisRibAnchorError("imported seed anchor 已存在，拒绝重复发布")

    bootstrap_rows: list[Mapping[str, Any]] = []
    bootstrap_ref = _consume_imported_seed_shard(
        accounting,
        kind="seed_bootstrap_attestation",
        consumer=lambda row: bootstrap_rows.append(dict(row)),
    )
    if len(bootstrap_rows) != 1:
        raise AnalysisRibAnchorError("seed bootstrap attestation 必须恰有一条")
    bootstrap = bootstrap_rows[0]
    seed_artifact_ref = bootstrap.get("seed_artifact_ref")
    if not isinstance(seed_artifact_ref, Mapping) or (
        seed_artifact_ref.get("artifact_id") != descriptor.artifact_id
        or seed_artifact_ref.get("file_sha256") != descriptor.file_sha256
        or seed_artifact_ref.get("size_bytes") != descriptor.size_bytes
    ):
        raise AnalysisRibAnchorError("seed bootstrap artifact ref 与 analysis seed 不一致")
    if not isinstance(bootstrap.get("seed_parser"), Mapping) or not isinstance(
        bootstrap.get("seed_spool_attestation"), Mapping
    ):
        raise AnalysisRibAnchorError("seed bootstrap parser/spool attestation 缺失")

    state: Optional[RouteReplayState] = None
    pending_events = []
    event_coordinates: dict[str, Tuple[str, str, int, int]] = {}
    observed_vps: set[str] = set()

    def consume_event(row: Mapping[str, Any]) -> None:
        nonlocal state
        event = _seed_event_from_full_window_row(row)
        if (
            event.artifact_id != descriptor.artifact_id
            or event.file_sha256 != descriptor.file_sha256
            or event.collector_id != descriptor.collector_id
            or event.artifact_slot_utc != descriptor.artifact_time_utc
        ):
            raise AnalysisRibAnchorError("imported seed RouteEvent 越出 seed artifact")
        if event.route_event_id in event_coordinates:
            raise AnalysisRibAnchorError("imported seed RouteEvent 身份重复")
        event_coordinates[event.route_event_id] = (
            event.artifact_id,
            event.file_sha256,
            event.record_ordinal,
            event.element_ordinal,
        )
        observed_vps.add(event.vp_id)
        pending_events.append(event)
        if len(pending_events) >= DEFAULT_BATCH_ROUTE_EVENTS:
            state = extend_streaming_rib_seed(state, tuple(pending_events))
            pending_events.clear()

    event_ref = _consume_imported_seed_shard(
        accounting, kind="seed_route_events", consumer=consume_event
    )
    if pending_events:
        state = extend_streaming_rib_seed(state, tuple(pending_events))
        pending_events.clear()
    if state is None:
        state = extend_streaming_rib_seed(None, ())

    raw_ref_ids: set[str] = set()
    physical_records: set[Tuple[str, int]] = set()

    def consume_raw_ref(row: Mapping[str, Any]) -> None:
        required = {
            "schema_version",
            "raw_record_ref_id",
            "route_event_id",
            "artifact_id",
            "file_sha256",
            "artifact_slot_utc",
            "record_ordinal",
            "element_ordinal",
            "record_offset",
            "record_length",
            "record_hash",
            "raw_record_sha256",
            "verification_status",
            "verification_basis",
        }
        if set(row) != required:
            raise AnalysisRibAnchorError("full-window seed raw ref 行字段不闭合")
        event_id = row.get("route_event_id")
        coordinate = event_coordinates.get(event_id)
        if coordinate != (
            row.get("artifact_id"),
            row.get("file_sha256"),
            row.get("record_ordinal"),
            row.get("element_ordinal"),
        ):
            raise AnalysisRibAnchorError("imported seed raw ref 与 RouteEvent 不一致")
        raw_id = row.get("raw_record_ref_id")
        if not isinstance(raw_id, str) or raw_id in raw_ref_ids:
            raise AnalysisRibAnchorError("imported seed raw ref 身份非法或重复")
        if (
            row.get("record_hash") != row.get("raw_record_sha256")
            or row.get("verification_status") != "verified"
        ):
            raise AnalysisRibAnchorError("imported seed raw physical record 未核验")
        raw_ref_ids.add(raw_id)
        physical_records.add((str(row.get("artifact_id")), int(row.get("record_ordinal"))))

    raw_ref = _consume_imported_seed_shard(
        accounting, kind="seed_raw_record_refs", consumer=consume_raw_ref
    )
    if len(raw_ref_ids) != len(event_coordinates):
        raise AnalysisRibAnchorError("imported seed RouteEvent/raw ref 未一一闭合")

    attested_state_payload = bootstrap.get("seed_route_state")
    if not isinstance(attested_state_payload, Mapping):
        raise AnalysisRibAnchorError("seed bootstrap 缺少 route state")
    recomputed_state_payload = route_replay_state_to_payload(state)
    if dict(attested_state_payload) != recomputed_state_payload:
        raise AnalysisRibAnchorError("seed RouteEvent 重放与 bootstrap route state 不一致")
    projection_payload = build_source_independent_route_projection(state)
    imported_output_records = (
        recomputed_state_payload,
        *tuple(projection_payload["rows"]),
    )
    estimated = _estimated_jsonl_gzip_staging_bytes(imported_output_records)
    if _active_attempt_temporary_bytes(
        root, spool_proof=None, shard_refs=()
    ) + estimated >= temporary_limit:
        raise AnalysisRibAnchorError("imported seed 输出将达到或超过 5GB 排他上限")
    state_ref = _publish_shard(
        root,
        kind="route_state",
        artifact_id=descriptor.artifact_id,
        sequence=0,
        records=(recomputed_state_payload,),
    )
    projection_ref = _publish_shard(
        root,
        kind="route_projection",
        artifact_id=descriptor.artifact_id,
        sequence=1,
        records=tuple(projection_payload["rows"]),
    )
    imported_output_bytes = _active_attempt_temporary_bytes(
        root,
        spool_proof=None,
        shard_refs=(state_ref, projection_ref),
    )
    if imported_output_bytes >= temporary_limit:
        raise AnalysisRibAnchorError("imported seed 实时临时占用达到或超过 5GB")
    imported_refs = [bootstrap_ref, event_ref, raw_ref]
    imported_chain = hashlib.sha256(
        canonical_json([row["sha256"] for row in imported_refs]).encode("utf-8")
    ).hexdigest()
    parser_attestation = build_parser_source_attestation(
        frozen_bindings["code_sha256"]
    )
    anchor_semantic = {
        "schema": ANCHOR_SEMANTIC_SCHEMA,
        "artifact": descriptor.to_dict(),
        "bindings": frozen_bindings,
        "evidence_mode": "imported_full_window_seed_genesis",
        "parser_source_attestation_fingerprint_sha256": parser_attestation[
            "fingerprint_sha256"
        ],
        "retention_policy_fingerprint_sha256": retention_evidence[
            "fingerprint_sha256"
        ],
        "route_state_semantic_sha256": recomputed_state_payload[
            "state_fingerprint_sha256"
        ],
        "projection_semantic_sha256": projection_payload["semantic_sha256"],
        "imported_seed_shard_chain_sha256": imported_chain,
        "observed_vp_ids": sorted(observed_vps),
    }
    anchor_semantic_sha = hashlib.sha256(
        canonical_json(anchor_semantic).encode("utf-8")
    ).hexdigest()
    anchor_id = "rib_anchor_v1_" + anchor_semantic_sha[:32]
    receipt = _fingerprinted(
        ANCHOR_RECEIPT_SCHEMA_VERSION,
        {
            "anchor_id": anchor_id,
            "anchor_semantic_sha256": anchor_semantic_sha,
            "status": "complete",
            "evidence_mode": "imported_full_window_seed_genesis",
            "artifact": descriptor.to_dict(),
            "boundary_at_utc": descriptor.artifact_time_utc,
            "bindings": frozen_bindings,
            "parser_source_attestation": parser_attestation,
            "retention_policy": retention_evidence,
            "prior_raw_accounting_fingerprint_sha256": accounting.fingerprint_sha256,
            "imported_seed_evidence": {
                "journal_run_id": accounting.run_id,
                "terminal_receipt_ref": dict(accounting.terminal_receipt_ref),
                "genesis_shards": imported_refs,
                "shard_chain_sha256": imported_chain,
                "seed_parser": dict(bootstrap["seed_parser"]),
                "seed_spool_attestation": dict(bootstrap["seed_spool_attestation"]),
            },
            "checkpoint_policy": {
                "record_boundary": "imported_verified_seed_checkpoint",
                "new_raw_opened": False,
                "new_raw_reservation_created": False,
            },
            "observed_vp_ids": sorted(observed_vps),
            "counts": {
                "route_state_entries": len(state.entries),
                "route_events": len(event_coordinates),
                "raw_records": len(physical_records),
                "raw_refs": len(raw_ref_ids),
            },
            "route_state": {
                "semantic_sha256": recomputed_state_payload[
                    "state_fingerprint_sha256"
                ],
                "shard": state_ref,
            },
            "projection": {
                "semantics": PROJECTION_SEMANTICS,
                "semantic_sha256": projection_payload["semantic_sha256"],
                "shard": projection_ref,
            },
            "route_event_shards": [],
            "raw_record_shards": [],
            "raw_ref_shards": [],
            "reservation_ref": None,
            "raw_open_claim_ref": None,
            "resources": {
                "reserved_raw_bytes": 0,
                "cumulative_reserved_raw_bytes": (
                    accounting.cumulative_reserved_raw_bytes
                ),
                "peak_temporary_bytes": imported_output_bytes,
                "output_bytes_excluding_receipt": (
                    state_ref["size_bytes"] + projection_ref["size_bytes"]
                ),
                "database_writes": 0,
            },
            "spool_retirement_required": False,
            "update_curve_policy": "independent_anchor_never_reset_update_curve",
        },
    )
    anchor_artifact, anchor_relative = _content_json(
        root, "receipts", "anchor", receipt
    )
    del anchor_artifact
    return AnchorSegmentResult(
        status="complete",
        reason="imported_verified_full_window_seed_without_raw_reread",
        artifact_id=descriptor.artifact_id,
        checkpoint_path=None,
        anchor_receipt_path=anchor_relative,
        retirement_receipt_path=None,
        next_record_ordinal=0,
        next_record_offset=0,
        process_seconds=0.0,
        peak_temporary_bytes=imported_output_bytes,
    )


def _run_analysis_rib_anchor_segment_unlocked(
    anchor_root: os.PathLike[str] | str,
    *,
    artifact_root: os.PathLike[str] | str,
    descriptor: AnalysisRibDescriptor,
    bindings: Mapping[str, Any],
    reservation: RawReservationToken,
    retention_policy: AnalysisRibRetentionPolicy,
    resume_checkpoint_path: Optional[os.PathLike[str] | str] = None,
    clock: Clock = time.monotonic,
    planned_checkpoint_seconds: float = DEFAULT_PLANNED_CHECKPOINT_SECONDS,
    soft_stop_seconds: float = DEFAULT_SOFT_STOP_SECONDS,
    hard_stop_seconds: float = DEFAULT_HARD_STOP_SECONDS,
    max_temporary_bytes: int = DEFAULT_MAX_TEMPORARY_BYTES,
    process_supervisor_hard_timeout_seconds: float = DEFAULT_HARD_STOP_SECONDS,
    batch_records: int = DEFAULT_BATCH_RECORDS,
    batch_route_events: int = DEFAULT_BATCH_ROUTE_EVENTS,
    crash_hook: Optional[Callable[[str], None]] = None,
) -> AnchorSegmentResult:
    """处理一张 RIB 的一个有界 record-boundary segment。"""

    root = Path(anchor_root).expanduser().resolve(strict=False)
    raw_root = Path(artifact_root).expanduser().resolve(strict=False)
    _assert_existing_anchor_mutation_root(root, "anchor_root")
    _assert_root_directory(raw_root, "artifact_root")
    if _load_genesis(root).get("artifact_root") != str(raw_root):
        raise AnalysisRibAnchorError("artifact_root 与 anchor genesis 冻结来源不一致")
    if descriptor.ingestion_mode != "new_raw":
        raise AnalysisRibAnchorError(
            "imported seed anchor 禁止进入 raw/spool segment 执行路径"
        )
    frozen_bindings = _bindings(bindings)
    retention_evidence = _validate_retention_policy(
        retention_policy, bindings=frozen_bindings
    )
    if _load_genesis(root).get("retention_policy") != retention_evidence:
        raise AnalysisRibAnchorError("运行时 retention policy 与 genesis 不一致")
    reservation_receipt = _verify_reservation(root, reservation)
    if reservation_receipt.get("bindings") != frozen_bindings:
        raise AnalysisRibAnchorError("reservation bindings 与当前执行不一致")
    if reservation.descriptor != descriptor:
        raise AnalysisRibAnchorError("reservation 与当前 anchor artifact 不一致")
    planned = _positive_seconds(
        planned_checkpoint_seconds, "planned_checkpoint_seconds"
    )
    soft = _positive_seconds(soft_stop_seconds, "soft_stop_seconds")
    hard = _positive_seconds(hard_stop_seconds, "hard_stop_seconds")
    if (
        not planned < soft < hard
        or soft != DEFAULT_SOFT_STOP_SECONDS
        or hard != DEFAULT_HARD_STOP_SECONDS
    ):
        raise AnalysisRibAnchorError("analysis RIB 必须保持 planned<540<590 冻结边界")
    supervisor_timeout = _positive_seconds(
        process_supervisor_hard_timeout_seconds,
        "process_supervisor_hard_timeout_seconds",
    )
    if supervisor_timeout != hard:
        raise AnalysisRibAnchorError(
            "每个 RIB 必须由独立子进程 supervisor 在 590 秒硬超时终止"
        )
    temporary_limit = _positive(max_temporary_bytes, "max_temporary_bytes")
    if temporary_limit > DEFAULT_MAX_TEMPORARY_BYTES:
        raise AnalysisRibAnchorError("analysis RIB 临时上限不得放宽到 5GB 以上")
    record_batch_limit = _positive(batch_records, "batch_records")
    event_batch_limit = _positive(batch_route_events, "batch_route_events")

    start = float(clock())
    if not math.isfinite(start):
        raise AnalysisRibAnchorError("clock 必须返回有限数")

    parser_attestation = build_parser_source_attestation(
        frozen_bindings["code_sha256"]
    )
    state: Optional[RouteReplayState] = None
    observed_vps: set[str] = set()
    shard_refs: list[dict[str, Any]] = []
    next_record_ordinal = 0
    next_record_offset = 0
    previous_boundary: Any = None
    peer_context: Any = None
    segment_sequence = 0
    parse_complete = False
    raw_open_claim_ref: Mapping[str, str]
    attempt_checkpoint_paths: list[str] = []

    if resume_checkpoint_path is None:
        active_spools = tuple((root / "spools").glob("*.mrt"))
        if active_spools:
            raise AnalysisRibAnchorError("新 artifact 开始前必须先退役上一份 spool")
        raw_open_claim_ref = _claim_raw_open(
            root,
            reservation=reservation,
            bindings=frozen_bindings,
        )
        raw_path = _safe_artifact_path(raw_root, descriptor)
        source_before = _stable_identity(raw_path.lstat())
        spool_relative = f"spools/{descriptor.artifact_id}.mrt"
        preexisting_temporary = _active_attempt_temporary_bytes(
            root, spool_proof=None, shard_refs=()
        )
        if preexisting_temporary >= temporary_limit:
            raise AnalysisRibAnchorError(
                "spool 构建前 staging 已达到或超过 5GB 排他上限"
            )
        build = build_rib_decompressed_spool(
            raw_path,
            root / spool_relative,
            expected_compressed_sha256=descriptor.file_sha256,
            expected_compressed_size_bytes=descriptor.size_bytes,
            max_temporary_bytes=temporary_limit - preexisting_temporary,
        )
        source_after = _stable_identity(raw_path.lstat())
        if not _same_identity(source_before, source_after):
            raise AnalysisRibAnchorError("压缩 RIB 在 single pass 外围检查期间变化")
        spool_identity = _stable_identity((root / spool_relative).lstat())
        spool_proof: dict[str, Any] = {
            "path": spool_relative,
            "sha256": build.sha256,
            "size_bytes": build.size_bytes,
            "stable_identity": spool_identity,
            "compressed_single_pass": {
                "file_sha256": build.compressed_sha256,
                "size_bytes": build.compressed_size_bytes,
                "read_passes": 1,
                "gzip_eof_crc_verified": True,
                "stable_identity_before": source_before,
                "stable_identity_after": source_after,
            },
        }
    else:
        checkpoint = _load_checkpoint(
            root,
            resume_checkpoint_path,
            descriptor=descriptor,
            bindings=frozen_bindings,
        )
        checkpoint_input = Path(resume_checkpoint_path)
        if checkpoint_input.is_absolute():
            checkpoint_input = checkpoint_input.relative_to(root)
        attempt_checkpoint_paths.append(
            _safe_relative(
                checkpoint_input.as_posix(), "resume_checkpoint_path"
            ).as_posix()
        )
        if checkpoint.get("parser_source_attestation") != parser_attestation:
            raise AnalysisRibAnchorError("checkpoint parser source attestation 漂移")
        if checkpoint.get("retention_policy") != retention_evidence:
            raise AnalysisRibAnchorError("checkpoint raw retention policy 漂移")
        if checkpoint.get("reservation_ref") != {
            "path": reservation.path,
            "sha256": reservation.sha256,
            "attempt_id": reservation.attempt_id,
            "sequence": reservation.sequence,
            "cumulative_reserved_raw_bytes": reservation.cumulative_reserved_raw_bytes,
        }:
            raise AnalysisRibAnchorError("checkpoint raw reservation 绑定不一致")
        raw_open_claim_ref = _verify_raw_open_claim(
            root,
            checkpoint.get("raw_open_claim_ref"),
            reservation=reservation,
            bindings=frozen_bindings,
        )
        position = checkpoint["position"]
        parse_complete = position.get("parse_complete")
        if not isinstance(parse_complete, bool):
            raise AnalysisRibAnchorError("checkpoint parse_complete 必须是布尔值")
        next_record_ordinal = _nonnegative(
            position.get("next_record_ordinal"), "checkpoint.next_record_ordinal"
        )
        next_record_offset = _nonnegative(
            position.get("next_record_offset"), "checkpoint.next_record_offset"
        )
        previous_boundary = position.get("previous_record_boundary")
        peer_context = position.get("peer_index_context")
        spool_proof = dict(checkpoint["spool_proof"])
        spool_path = root / _safe_relative(spool_proof["path"], "spool.path")
        if _stable_identity(spool_path.lstat()) != spool_proof["stable_identity"]:
            raise AnalysisRibAnchorError("resume spool 稳定 identity 不一致")
        state = route_replay_state_from_payload(checkpoint["route_state"])
        observed_raw = checkpoint.get("observed_vp_ids")
        if not isinstance(observed_raw, list) or observed_raw != sorted(set(observed_raw)):
            raise AnalysisRibAnchorError("checkpoint observed_vp_ids 非规范")
        observed_vps.update(observed_raw)
        refs = checkpoint.get("shards")
        if not isinstance(refs, list):
            raise AnalysisRibAnchorError("checkpoint shards 必须是数组")
        for ref in refs:
            _verify_shard_ref(root, ref)
            shard_refs.append(dict(ref))
        segment_sequence = 1 + max(
            (int(ref["sequence"]) for ref in shard_refs), default=-1
        )

    spool_path = root / _safe_relative(spool_proof["path"], "spool.path")
    if spool_proof["size_bytes"] >= temporary_limit:
        raise AnalysisRibAnchorError("active spool 达到或超过 5GB 排他上限")
    peak_temporary_bytes = 0

    def enforce_temporary_gate(
        *,
        additional_bytes: int = 0,
    ) -> int:
        nonlocal peak_temporary_bytes
        additional = _nonnegative(additional_bytes, "additional_temporary_bytes")
        current = _active_attempt_temporary_bytes(
            root,
            spool_proof=spool_proof,
            shard_refs=shard_refs,
            checkpoint_paths=attempt_checkpoint_paths,
        )
        projected = current + additional
        peak_temporary_bytes = max(peak_temporary_bytes, current)
        if projected >= temporary_limit:
            raise AnalysisRibAnchorError(
                "spool+shard+checkpoint+staging 将达到或超过 5GB 排他上限"
            )
        return current

    enforce_temporary_gate()
    accumulator = ObservedVpAccumulator(descriptor.collector_id)
    pending_events: list[Any] = []
    pending_raw: list[Mapping[str, Any]] = []
    pending_raw_refs: list[Mapping[str, Any]] = []
    pending_record_count = 0
    latest_boundary = previous_boundary
    latest_peer_context = peer_context

    def elapsed() -> float:
        value = float(clock()) - start
        if not math.isfinite(value) or value < 0:
            raise AnalysisRibAnchorError("clock 不得倒退且必须返回有限数")
        return value

    def observe_checkpoint(
        boundary: RibRecordBoundary,
        current_peer_context: Optional[RibPeerIndexContext],
    ) -> None:
        nonlocal latest_boundary, latest_peer_context
        # record 热路径只保存 frozen 对象引用，发布 checkpoint 时才物化。
        latest_boundary = boundary
        latest_peer_context = current_peer_context

    def flush_batch() -> None:
        nonlocal state, pending_record_count, segment_sequence
        if pending_events:
            state = extend_streaming_rib_seed(state, tuple(pending_events))
        elif state is None:
            state = extend_streaming_rib_seed(None, ())
        if pending_events or pending_raw:
            publish_groups: list[Tuple[str, Sequence[Mapping[str, Any]]]] = []
            if pending_events:
                publish_groups.extend(
                    (
                        (
                            "route_events",
                            tuple(_event_payload(event) for event in pending_events),
                        ),
                        (
                            "raw_refs",
                            tuple(dict(row) for row in pending_raw_refs),
                        ),
                    )
                )
            if pending_raw:
                publish_groups.append(
                    ("raw_records", tuple(dict(row) for row in pending_raw))
                )
            for kind, records in publish_groups:
                enforce_temporary_gate(
                    additional_bytes=_estimated_jsonl_gzip_staging_bytes(records)
                )
                shard_refs.append(
                    _publish_shard(
                        root,
                        kind=kind,
                        artifact_id=descriptor.artifact_id,
                        sequence=segment_sequence,
                        records=records,
                    )
                )
                enforce_temporary_gate()
            segment_sequence += 1
        pending_events.clear()
        pending_raw.clear()
        pending_raw_refs.clear()
        pending_record_count = 0

    def publish_checkpoint(
        reason: str, *, completed_parse: bool = False
    ) -> AnchorSegmentResult:
        flush_batch()
        observed_vps.update(accumulator.observed_vp_ids)
        if latest_boundary is None:
            raise AnalysisRibAnchorError("checkpoint 前尚无完整 physical record")
        current_state = state if state is not None else extend_streaming_rib_seed(None, ())
        position = {
            "next_record_ordinal": next_record_ordinal,
            "next_record_offset": next_record_offset,
            "boundary": "after_complete_physical_record",
            "parse_complete": completed_parse,
            "previous_record_boundary": _checkpoint_binding(latest_boundary),
            "peer_index_context": _checkpoint_binding(latest_peer_context),
        }
        payload = _fingerprinted(
            CHECKPOINT_SCHEMA_VERSION,
            {
                "artifact": descriptor.to_dict(),
                "bindings": frozen_bindings,
                "parser_source_attestation": parser_attestation,
                "retention_policy": retention_evidence,
                "raw_open_claim_ref": dict(raw_open_claim_ref),
                "reservation_ref": {
                    "path": reservation.path,
                    "sha256": reservation.sha256,
                    "attempt_id": reservation.attempt_id,
                    "sequence": reservation.sequence,
                    "cumulative_reserved_raw_bytes": (
                        reservation.cumulative_reserved_raw_bytes
                    ),
                },
                "spool_proof": spool_proof,
                "position": position,
                "route_state": route_replay_state_to_payload(current_state),
                "observed_vp_ids": sorted(observed_vps),
                "shards": shard_refs,
                "resources": {
                    "process_seconds": elapsed(),
                    "peak_temporary_bytes": spool_proof["size_bytes"],
                    "database_writes": 0,
                    "planned_checkpoint_seconds": planned,
                    "soft_stop_seconds_exclusive": soft,
                    "hard_stop_seconds_exclusive": hard,
                    "process_supervisor_hard_timeout_seconds": supervisor_timeout,
                },
                "reason": reason,
            },
        )
        encoded_checkpoint_bytes = len(
            (canonical_json(payload) + "\n").encode("utf-8")
        )
        enforce_temporary_gate(additional_bytes=encoded_checkpoint_bytes + 1024 * 1024)
        relative, _file_sha = _checkpoint_path(root, payload)
        attempt_checkpoint_paths.append(relative)
        enforce_temporary_gate()
        observed_elapsed = elapsed()
        observed_reason = reason
        if observed_elapsed >= hard:
            observed_reason = "hard_runtime_approval_required"
        elif observed_elapsed >= soft:
            observed_reason = "soft_runtime_stop"
        return AnchorSegmentResult(
            status="checkpointed",
            reason=observed_reason,
            artifact_id=descriptor.artifact_id,
            checkpoint_path=relative,
            anchor_receipt_path=None,
            retirement_receipt_path=None,
            next_record_ordinal=next_record_ordinal,
            next_record_offset=next_record_offset,
            process_seconds=observed_elapsed,
            peak_temporary_bytes=peak_temporary_bytes,
        )

    if not parse_complete:
        adapter = iter_rib_spool_artifact_records(
            spool_path,
            expected_decompressed_sha256=spool_proof["sha256"],
            expected_decompressed_size_bytes=spool_proof["size_bytes"],
            next_record_ordinal=next_record_ordinal,
            next_record_offset=next_record_offset,
            previous_record_boundary=previous_boundary,
            peer_index_context=peer_context,
            artifact=descriptor.artifact(),
            origin_asn_predicate=retention_policy.retain_origin_asn,
            vp_observer=accumulator.observe,
            checkpoint_observer=observe_checkpoint,
        )
        try:
            for record in adapter:
                pending_raw.append(asdict(record.raw_record))
                pending_record_count += 1
                for event in record.route_events:
                    pending_events.append(event)
                    pending_raw_refs.append(asdict(event.raw_ref))
                next_record_ordinal = record.raw_record.record_ordinal + 1
                next_record_offset = (
                    record.raw_record.record_offset + record.raw_record.record_length
                )
                if (
                    pending_record_count >= record_batch_limit
                    or len(pending_events) >= event_batch_limit
                ):
                    flush_batch()
                if elapsed() >= planned:
                    return publish_checkpoint("planned_record_boundary_checkpoint")
        finally:
            adapter.close()

    if next_record_offset != spool_proof["size_bytes"]:
        raise AnalysisRibAnchorError("RIB 解析结束 offset 与 spool size 不一致")
    flush_batch()
    observed_vps.update(accumulator.observed_vp_ids)
    if elapsed() >= soft:
        return publish_checkpoint(
            "parse_complete_finalize_deferred", completed_parse=True
        )

    current_state = state if state is not None else extend_streaming_rib_seed(None, ())
    state_payload = route_replay_state_to_payload(current_state)
    projection_payload = build_source_independent_route_projection(current_state)
    projection = tuple(projection_payload["rows"])
    projection_sha = str(projection_payload["semantic_sha256"])
    enforce_temporary_gate(
        additional_bytes=_estimated_jsonl_gzip_staging_bytes((state_payload,))
    )
    state_ref = _publish_shard(
        root,
        kind="route_state",
        artifact_id=descriptor.artifact_id,
        sequence=segment_sequence,
        records=(state_payload,),
    )
    shard_refs.append(state_ref)
    enforce_temporary_gate()
    enforce_temporary_gate(
        additional_bytes=_estimated_jsonl_gzip_staging_bytes(projection)
    )
    projection_ref = _publish_shard(
        root,
        kind="route_projection",
        artifact_id=descriptor.artifact_id,
        sequence=segment_sequence + 1,
        records=projection,
    )
    shard_refs.append(projection_ref)
    enforce_temporary_gate()
    all_refs = list(shard_refs)
    route_event_refs = [ref for ref in shard_refs if ref["kind"] == "route_events"]
    raw_record_refs = [ref for ref in shard_refs if ref["kind"] == "raw_records"]
    raw_refs = [ref for ref in shard_refs if ref["kind"] == "raw_refs"]
    chain = lambda refs: hashlib.sha256(
        canonical_json([ref["semantic_sha256"] for ref in refs]).encode("utf-8")
    ).hexdigest()
    anchor_semantic = {
        "schema": ANCHOR_SEMANTIC_SCHEMA,
        "artifact": descriptor.to_dict(),
        "bindings": frozen_bindings,
        "evidence_mode": "new_raw_single_pass",
        "parser_source_attestation_fingerprint_sha256": parser_attestation[
            "fingerprint_sha256"
        ],
        "retention_policy_fingerprint_sha256": retention_evidence[
            "fingerprint_sha256"
        ],
        "route_state_semantic_sha256": state_payload[
            "state_fingerprint_sha256"
        ],
        "projection_semantic_sha256": projection_sha,
        "route_event_shard_chain_sha256": chain(route_event_refs),
        "raw_record_shard_chain_sha256": chain(raw_record_refs),
        "raw_ref_shard_chain_sha256": chain(raw_refs),
        "observed_vp_ids": sorted(observed_vps),
    }
    anchor_semantic_sha = hashlib.sha256(
        canonical_json(anchor_semantic).encode("utf-8")
    ).hexdigest()
    anchor_id = "rib_anchor_v1_" + anchor_semantic_sha[:32]
    output_bytes = sum(int(ref["size_bytes"]) for ref in all_refs)
    receipt = _fingerprinted(
        ANCHOR_RECEIPT_SCHEMA_VERSION,
        {
            "anchor_id": anchor_id,
            "anchor_semantic_sha256": anchor_semantic_sha,
            "status": "complete",
            "evidence_mode": "new_raw_single_pass",
            "artifact": descriptor.to_dict(),
            "boundary_at_utc": descriptor.artifact_time_utc,
            "bindings": frozen_bindings,
            "parser_source_attestation": parser_attestation,
            "retention_policy": retention_evidence,
            "compressed_single_pass_proof": spool_proof[
                "compressed_single_pass"
            ],
            "spool_proof": spool_proof,
            "checkpoint_policy": {
                "record_boundary": "after_complete_physical_record",
                "planned_checkpoint_seconds": planned,
                "soft_stop_seconds_exclusive": soft,
                "hard_stop_seconds_exclusive": hard,
                "process_supervisor_hard_timeout_seconds": supervisor_timeout,
                "enforcement_scope": (
                    "external_parent_420_observe_540_term_590_kill_596_exit_required"
                ),
            },
            "observed_vp_ids": sorted(observed_vps),
            "counts": {
                "route_state_entries": len(current_state.entries),
                "route_events": sum(ref["record_count"] for ref in route_event_refs),
                "raw_records": sum(ref["record_count"] for ref in raw_record_refs),
                "raw_refs": sum(ref["record_count"] for ref in raw_refs),
            },
            "route_state": {
                "semantic_sha256": state_payload["state_fingerprint_sha256"],
                "shard": state_ref,
            },
            "projection": {
                "semantics": PROJECTION_SEMANTICS,
                "semantic_sha256": projection_sha,
                "shard": projection_ref,
            },
            "route_event_shards": route_event_refs,
            "raw_record_shards": raw_record_refs,
            "raw_ref_shards": raw_refs,
            "reservation_ref": {
                "path": reservation.path,
                "sha256": reservation.sha256,
                "attempt_id": reservation.attempt_id,
                "sequence": reservation.sequence,
            },
            "raw_open_claim_ref": dict(raw_open_claim_ref),
            "resources": {
                "reserved_raw_bytes": reservation.reserved_raw_bytes,
                "cumulative_reserved_raw_bytes": (
                    reservation.cumulative_reserved_raw_bytes
                ),
                "process_seconds": elapsed(),
                "peak_temporary_bytes": peak_temporary_bytes,
                "output_bytes_excluding_receipt": output_bytes,
                "database_writes": 0,
            },
            "spool_retirement_required": True,
            "update_curve_policy": "independent_anchor_never_reset_update_curve",
        },
    )
    receipt_estimate = len((canonical_json(receipt) + "\n").encode("utf-8"))
    enforce_temporary_gate(additional_bytes=receipt_estimate + 1024 * 1024)
    anchor_artifact, anchor_relative = _content_json(
        root, "receipts", "anchor", receipt
    )
    enforce_temporary_gate()
    if crash_hook is not None:
        crash_hook("after_anchor_receipt_publish")
    retirement_relative = _retire_spool(
        root,
        descriptor=descriptor,
        spool_proof=spool_proof,
        anchor_receipt_path=anchor_relative,
        anchor_receipt_sha256=anchor_artifact.sha256,
        crash_hook=crash_hook,
    )
    return AnchorSegmentResult(
        status="complete",
        reason=None,
        artifact_id=descriptor.artifact_id,
        checkpoint_path=None,
        anchor_receipt_path=anchor_relative,
        retirement_receipt_path=retirement_relative,
        next_record_ordinal=next_record_ordinal,
        next_record_offset=next_record_offset,
        process_seconds=elapsed(),
        peak_temporary_bytes=peak_temporary_bytes,
    )


def _anchor_receipts_for_artifact(
    root: Path, artifact_id: str
) -> Tuple[Tuple[Path, Mapping[str, Any], str], ...]:
    matches = []
    for path in sorted((root / "receipts").glob("anchor-*.json")):
        payload = _load_canonical_json(
            path,
            schema_version=ANCHOR_RECEIPT_SCHEMA_VERSION,
            maximum_bytes=64 * 1024 * 1024,
        )
        if payload.get("artifact", {}).get("artifact_id") != artifact_id:
            continue
        _raw, file_sha = _read_regular(path, maximum_bytes=64 * 1024 * 1024)
        matches.append((path, payload, file_sha))
    return tuple(matches)


def import_full_window_seed_anchor(
    anchor_root: os.PathLike[str] | str,
    *,
    descriptor: AnalysisRibDescriptor,
    bindings: Mapping[str, Any],
    retention_policy: AnalysisRibRetentionPolicy,
    max_temporary_bytes: int = DEFAULT_MAX_TEMPORARY_BYTES,
    attempt_id: Optional[str] = None,
    crash_hook: Optional[Callable[[str], None]] = None,
) -> AnchorSegmentResult:
    """在 execution flock/ACTIVE FSM 中导入窗口起点 seed。"""

    root = Path(anchor_root).expanduser().resolve(strict=False)
    _assert_existing_anchor_mutation_root(root, "anchor_root")
    frozen_bindings = _bindings(bindings)
    with analysis_rib_execution_lock(root):
        if _anchor_receipts_for_artifact(root, descriptor.artifact_id):
            raise AnalysisRibAnchorError("imported seed anchor 已存在，拒绝重复发布")
        active = _begin_or_resume_active(
            root,
            descriptor=descriptor,
            bindings=frozen_bindings,
            reservation=None,
            resume_checkpoint_path=None,
            attempt_id=attempt_id,
        )
        try:
            result = _import_full_window_seed_anchor_unlocked(
                root,
                descriptor=descriptor,
                bindings=frozen_bindings,
                retention_policy=retention_policy,
                max_temporary_bytes=max_temporary_bytes,
            )
            if crash_hook is not None:
                crash_hook("after_anchor_receipt_publish")
        except SimulatedAnalysisRibCrash as error:
            outcome = _write_execution_outcome(
                root,
                active=active,
                status="crash_recovery_required",
                raw_observation={
                    "state": "exact",
                    "bytes": 0,
                    "basis": "imported_seed_never_opens_new_raw",
                },
                error=str(error),
            )
            _transition_active(
                root, active, state="recovery_required", outcome_ref=outcome
            )
            raise
        except Exception as error:
            _discard_failed_attempt_files(
                root,
                descriptor=descriptor,
                attempt_id=str(active["attempt_id"]),
                reason=type(error).__name__,
            )
            _write_execution_outcome(
                root,
                active=active,
                status="failed",
                raw_observation={
                    "state": "exact",
                    "bytes": 0,
                    "basis": "imported_seed_never_opens_new_raw",
                },
                error=f"{type(error).__name__}: {error}",
            )
            _clear_active(root)
            raise
        outcome = _write_execution_outcome(
            root,
            active=active,
            status="complete",
            raw_observation={
                "state": "exact",
                "bytes": 0,
                "basis": "imported_seed_never_opens_new_raw",
            },
            result=_result_payload(result),
        )
        del outcome
        _clear_active(root)
        return result


def run_analysis_rib_anchor_segment(
    anchor_root: os.PathLike[str] | str,
    *,
    artifact_root: os.PathLike[str] | str,
    descriptor: AnalysisRibDescriptor,
    bindings: Mapping[str, Any],
    reservation: RawReservationToken,
    retention_policy: AnalysisRibRetentionPolicy,
    resume_checkpoint_path: Optional[os.PathLike[str] | str] = None,
    clock: Clock = time.monotonic,
    planned_checkpoint_seconds: float = DEFAULT_PLANNED_CHECKPOINT_SECONDS,
    soft_stop_seconds: float = DEFAULT_SOFT_STOP_SECONDS,
    hard_stop_seconds: float = DEFAULT_HARD_STOP_SECONDS,
    max_temporary_bytes: int = DEFAULT_MAX_TEMPORARY_BYTES,
    process_supervisor_hard_timeout_seconds: float = DEFAULT_HARD_STOP_SECONDS,
    batch_records: int = DEFAULT_BATCH_RECORDS,
    batch_route_events: int = DEFAULT_BATCH_ROUTE_EVENTS,
    crash_hook: Optional[Callable[[str], None]] = None,
) -> AnchorSegmentResult:
    """单段公开入口：锁内完成 ACTIVE、outcome、receipt 与 spool 退役。"""

    root = Path(anchor_root)
    frozen_bindings = _bindings(bindings)
    with analysis_rib_execution_lock(root):
        if _anchor_receipts_for_artifact(root, descriptor.artifact_id):
            raise AnalysisRibAnchorError("artifact anchor 已存在，拒绝重复打开 raw")
        active = _begin_or_resume_active(
            root,
            descriptor=descriptor,
            bindings=frozen_bindings,
            reservation=reservation,
            resume_checkpoint_path=resume_checkpoint_path,
        )
        try:
            result = _run_analysis_rib_anchor_segment_unlocked(
                root,
                artifact_root=artifact_root,
                descriptor=descriptor,
                bindings=frozen_bindings,
                reservation=reservation,
                retention_policy=retention_policy,
                resume_checkpoint_path=resume_checkpoint_path,
                clock=clock,
                planned_checkpoint_seconds=planned_checkpoint_seconds,
                soft_stop_seconds=soft_stop_seconds,
                hard_stop_seconds=hard_stop_seconds,
                max_temporary_bytes=max_temporary_bytes,
                process_supervisor_hard_timeout_seconds=(
                    process_supervisor_hard_timeout_seconds
                ),
                batch_records=batch_records,
                batch_route_events=batch_route_events,
                crash_hook=crash_hook,
            )
        except SimulatedAnalysisRibCrash as error:
            outcome = _write_execution_outcome(
                root,
                active=active,
                status="crash_recovery_required",
                raw_observation=_raw_observation(root, descriptor=descriptor),
                error=str(error),
            )
            _transition_active(
                root, active, state="recovery_required", outcome_ref=outcome
            )
            raise
        except Exception as error:
            raw_observation = _raw_observation(root, descriptor=descriptor)
            _discard_failed_attempt_files(
                root,
                descriptor=descriptor,
                attempt_id=str(active["attempt_id"]),
                reason=type(error).__name__,
            )
            _write_execution_outcome(
                root,
                active=active,
                status=(
                    "terminated"
                    if isinstance(error, AnalysisRibTerminationRequested)
                    else "failed"
                ),
                raw_observation=raw_observation,
                error=f"{type(error).__name__}: {error}",
            )
            _clear_active(root)
            raise
        raw_observation = _raw_observation(root, descriptor=descriptor)
        outcome = _write_execution_outcome(
            root,
            active=active,
            status=result.status,
            raw_observation=raw_observation,
            result=_result_payload(result),
        )
        if result.status == "checkpointed":
            _checkpoint_active(root, active, result=result, outcome_ref=outcome)
        elif result.status == "complete":
            _clear_active(root)
        else:
            raise AnalysisRibAnchorError("segment result 状态非法")
        return result


def _descriptor_from_payload(value: Mapping[str, Any]) -> AnalysisRibDescriptor:
    required = {
        "anchor_index",
        "role",
        "ingestion_mode",
        "artifact_id",
        "artifact_type",
        "artifact_time_utc",
        "collector_id",
        "relative_path",
        "file_sha256",
        "size_bytes",
        "compression",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AnalysisRibAnchorError("execution artifact 字段不闭合")
    if value.get("artifact_type") != "rib":
        raise AnalysisRibAnchorError("execution artifact 类型非法")
    return AnalysisRibDescriptor(
        anchor_index=int(value["anchor_index"]),
        role=str(value["role"]),
        ingestion_mode=str(value["ingestion_mode"]),
        artifact_id=str(value["artifact_id"]),
        file_sha256=str(value["file_sha256"]),
        size_bytes=int(value["size_bytes"]),
        collector_id=str(value["collector_id"]),
        artifact_time_utc=str(value["artifact_time_utc"]),
        relative_path=str(value["relative_path"]),
        compression=str(value["compression"]),
    )


def _reconstruct_retirement_success(
    root: Path,
    *,
    descriptor: AnalysisRibDescriptor,
    anchor_path: str,
    anchor_sha: str,
) -> str:
    matches = []
    for path in sorted((root / "retirements").glob("retirement-attempt-*.json")):
        row = _load_canonical_json(
            path,
            schema_version=RETIREMENT_ATTEMPT_SCHEMA_VERSION,
            maximum_bytes=4 * 1024 * 1024,
        )
        if (
            row.get("artifact_id") == descriptor.artifact_id
            and row.get("anchor_receipt_path") == anchor_path
            and row.get("anchor_receipt_sha256") == anchor_sha
        ):
            _raw, file_sha = _read_regular(path, maximum_bytes=4 * 1024 * 1024)
            matches.append((path, row, file_sha))
    if len(matches) != 1:
        raise AnalysisRibAnchorError("缺失 retirement success 时 attempt 必须恰好一份")
    attempt_path, attempt, attempt_sha = matches[0]
    spool = attempt.get("spool")
    if not isinstance(spool, Mapping):
        raise AnalysisRibAnchorError("retirement attempt spool 证据缺失")
    spool_path = root / _safe_relative(spool.get("path"), "retirement.spool.path")
    if spool_path.exists() or spool_path.is_symlink():
        raise AnalysisRibAnchorError("重建 retirement success 前 spool 必须已不存在")
    success = _fingerprinted(
        RETIREMENT_RECEIPT_SCHEMA_VERSION,
        {
            "artifact_id": descriptor.artifact_id,
            "anchor_receipt_path": anchor_path,
            "anchor_receipt_sha256": anchor_sha,
            "attempt_ref": {
                "path": attempt_path.relative_to(root).as_posix(),
                "sha256": attempt_sha,
            },
            "retired_spool": {
                "path": spool["path"],
                "sha256": spool["sha256"],
                "size_bytes": spool["size_bytes"],
                "stable_identity_before_unlink": spool[
                    "stable_identity_before_unlink"
                ],
            },
            "status": "retired_and_directory_fsynced",
        },
    )
    _published, relative = _content_json_idempotent(
        root, "retirements", "retirement-success", success
    )
    return relative


def reconcile_analysis_rib_anchor_workspace(
    anchor_root: os.PathLike[str] | str,
    *,
    bindings: Mapping[str, Any],
) -> Mapping[str, Any]:
    """锁内收敛 kill/发布/退役崩溃窗口；从不新增 raw reservation。"""

    root = Path(anchor_root)
    frozen_bindings = _bindings(bindings)
    with analysis_rib_execution_lock(root):
        if _load_genesis(root).get("bindings") != frozen_bindings:
            raise AnalysisRibAnchorError("reconcile bindings 与 genesis 不一致")
        active = _load_active(root)
        if active is None:
            return {
                "status": "no_active_attempt",
                "active_cleared": True,
                "raw_reservation_created": False,
                "database_writes": 0,
            }
        if active.get("bindings") != frozen_bindings:
            raise AnalysisRibAnchorError("ACTIVE bindings 与 reconcile 不一致")
        descriptor = _descriptor_from_payload(active["artifact"])
        anchors = _anchor_receipts_for_artifact(root, descriptor.artifact_id)
        if len(anchors) > 1:
            raise AnalysisRibAnchorError("同一 artifact 出现多份 anchor receipt")
        raw_observation = (
            {
                "state": "exact",
                "bytes": 0,
                "basis": "imported_seed_never_opens_new_raw",
            }
            if descriptor.ingestion_mode == "imported_full_window_seed"
            else _raw_observation(root, descriptor=descriptor)
        )
        if anchors:
            anchor_path, anchor, anchor_sha = anchors[0]
            relative_anchor = anchor_path.relative_to(root).as_posix()
            retirement_path: Optional[str] = None
            recovery_action = "anchor_already_published"
            if anchor.get("spool_retirement_required") is True:
                spool_path = root / "spools" / f"{descriptor.artifact_id}.mrt"
                if spool_path.exists() or spool_path.is_symlink():
                    retirement_path = _retire_spool(
                        root,
                        descriptor=descriptor,
                        spool_proof=anchor["spool_proof"],
                        anchor_receipt_path=relative_anchor,
                        anchor_receipt_sha256=anchor_sha,
                    )
                    recovery_action = "published_anchor_spool_retired"
                else:
                    successes = []
                    for path in (root / "retirements").glob(
                        "retirement-success-*.json"
                    ):
                        row = _load_canonical_json(
                            path,
                            schema_version=RETIREMENT_RECEIPT_SCHEMA_VERSION,
                            maximum_bytes=4 * 1024 * 1024,
                        )
                        if row.get("artifact_id") == descriptor.artifact_id:
                            successes.append(path)
                    if successes:
                        _retirement_for_anchor(
                            root,
                            artifact_id=descriptor.artifact_id,
                            anchor_path=relative_anchor,
                            anchor_sha=anchor_sha,
                        )
                        retirement_path = successes[0].relative_to(root).as_posix()
                        recovery_action = "retirement_success_already_published"
                    else:
                        retirement_path = _reconstruct_retirement_success(
                            root,
                            descriptor=descriptor,
                            anchor_path=relative_anchor,
                            anchor_sha=anchor_sha,
                        )
                        recovery_action = "retirement_success_reconstructed"
            outcome = _write_execution_outcome(
                root,
                active=active,
                status="reconciled_complete",
                raw_observation=raw_observation,
                result={
                    "anchor_receipt_path": relative_anchor,
                    "retirement_receipt_path": retirement_path,
                    "recovery_action": recovery_action,
                },
            )
            _clear_active(root)
            return {
                "status": "reconciled_complete",
                "attempt_id": active["attempt_id"],
                "artifact_id": descriptor.artifact_id,
                "action": recovery_action,
                "outcome_ref": outcome,
                "active_cleared": True,
                "raw_reservation_created": False,
                "database_writes": 0,
            }
        spool_path = root / "spools" / f"{descriptor.artifact_id}.mrt"
        if (
            active.get("state") == "checkpointed"
            and spool_path.is_file()
            and not spool_path.is_symlink()
        ):
            return {
                "status": "resume_required",
                "attempt_id": active["attempt_id"],
                "artifact_id": descriptor.artifact_id,
                "checkpoint_path": active["latest_checkpoint_path"],
                "active_cleared": False,
                "raw_reservation_created": False,
                "database_writes": 0,
            }
        retirement = _discard_failed_attempt_files(
            root,
            descriptor=descriptor,
            attempt_id=str(active["attempt_id"]),
            reason="abnormal_process_exit_reconciled",
        )
        outcome = _write_execution_outcome(
            root,
            active=active,
            status="abnormal_exit_reconciled_failed",
            raw_observation=raw_observation,
            result={"failed_retirement": retirement},
        )
        _clear_active(root)
        return {
            "status": "reconciled_failed",
            "attempt_id": active["attempt_id"],
            "artifact_id": descriptor.artifact_id,
            "raw_read_observation": raw_observation,
            "outcome_ref": outcome,
            "active_cleared": True,
            "raw_reservation_created": False,
            "database_writes": 0,
        }


def record_analysis_rib_supervisor_receipt(
    anchor_root: os.PathLike[str] | str,
    *,
    bindings: Mapping[str, Any],
    attempt_id: str,
    artifact_id: str,
    child_pid: int,
    started_at_utc: str,
    finished_at_utc: str,
    returncode: int,
    observation_seconds: float,
    term_seconds: float,
    kill_seconds: float,
    observed_420: bool,
    term_sent: bool,
    kill_sent: bool,
    reconciliation: Optional[Mapping[str, Any]],
) -> Mapping[str, str]:
    """父进程记录实际监督动作；测试缩短策略不会获得 execution_ready。"""

    root = Path(anchor_root).expanduser().resolve(strict=False)
    _assert_existing_anchor_mutation_root(root, "anchor_root")
    frozen_bindings = _bindings(bindings)
    if _load_genesis(root).get("bindings") != frozen_bindings:
        raise AnalysisRibAnchorError("supervisor bindings 与 genesis 不一致")
    if not isinstance(attempt_id, str) or _ATTEMPT_ID_RE.fullmatch(attempt_id) is None:
        raise AnalysisRibAnchorError("supervisor attempt_id 非法")
    if not isinstance(artifact_id, str) or _ARTIFACT_ID_RE.fullmatch(artifact_id) is None:
        raise AnalysisRibAnchorError("supervisor artifact_id 非法")
    started = _utc(started_at_utc, "started_at_utc")
    finished = _utc(finished_at_utc, "finished_at_utc")
    if finished < started:
        raise AnalysisRibAnchorError("supervisor 完成时间早于开始时间")
    if isinstance(child_pid, bool) or not isinstance(child_pid, int) or child_pid <= 0:
        raise AnalysisRibAnchorError("child_pid 必须为正整数")
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        raise AnalysisRibAnchorError("returncode 必须为整数")
    for value, name in (
        (observed_420, "observed_420"),
        (term_sent, "term_sent"),
        (kill_sent, "kill_sent"),
    ):
        if not isinstance(value, bool):
            raise AnalysisRibAnchorError(f"{name} 必须是布尔值")
    observation = _positive_seconds(observation_seconds, "observation_seconds")
    term = _positive_seconds(term_seconds, "term_seconds")
    kill = _positive_seconds(kill_seconds, "kill_seconds")
    if not observation < term < kill:
        raise AnalysisRibAnchorError("supervisor 必须保持 observation<TERM<KILL")
    payload = _fingerprinted(
        SUPERVISOR_RECEIPT_SCHEMA_VERSION,
        {
            "attempt_id": attempt_id,
            "artifact_id": artifact_id,
            "bindings": frozen_bindings,
            "child_pid": child_pid,
            "started_at_utc": started_at_utc,
            "finished_at_utc": finished_at_utc,
            "returncode": returncode,
            "policy": {
                "observation_seconds": observation,
                "term_seconds": term,
                "kill_seconds": kill,
                "is_frozen_production_policy": (
                    observation == DEFAULT_PLANNED_CHECKPOINT_SECONDS
                    and term == DEFAULT_SOFT_STOP_SECONDS
                    and kill == DEFAULT_HARD_STOP_SECONDS
                ),
            },
            "actions": {
                "observed_420": observed_420,
                "term_sent": term_sent,
                "kill_sent": kill_sent,
            },
            "reconciliation": (
                None if reconciliation is None else dict(reconciliation)
            ),
            "status": "child_exit_observed_and_recorded",
        },
    )
    published, relative = _content_json_idempotent(
        root, "execution/supervisors", "supervisor", payload
    )
    return {"path": relative, "sha256": published.sha256}


def _receipt_file_sha(root: Path, relative: str) -> str:
    _raw, digest = _read_regular(
        root / _safe_relative(relative, "receipt.path"), maximum_bytes=64 * 1024 * 1024
    )
    return digest


def _retirement_for_anchor(
    root: Path, *, artifact_id: str, anchor_path: str, anchor_sha: str
) -> Mapping[str, Any]:
    matches = []
    for path in sorted((root / "retirements").glob("retirement-success-*.json")):
        row = _load_canonical_json(
            path,
            schema_version=RETIREMENT_RECEIPT_SCHEMA_VERSION,
            maximum_bytes=4 * 1024 * 1024,
        )
        if row.get("artifact_id") == artifact_id:
            matches.append(row)
    if len(matches) != 1:
        raise AnalysisRibAnchorError("每个 anchor 必须恰有一份 spool 退役成功收据")
    row = matches[0]
    if (
        row.get("anchor_receipt_path") != anchor_path
        or row.get("anchor_receipt_sha256") != anchor_sha
        or row.get("status") != "retired_and_directory_fsynced"
    ):
        raise AnalysisRibAnchorError("spool 退役收据与 anchor 不闭合")
    attempt_ref = row.get("attempt_ref")
    if not isinstance(attempt_ref, Mapping):
        raise AnalysisRibAnchorError("spool 退役 attempt ref 缺失")
    attempt_path = root / _safe_relative(attempt_ref.get("path"), "attempt.path")
    attempt = _load_canonical_json(
        attempt_path,
        schema_version=RETIREMENT_ATTEMPT_SCHEMA_VERSION,
        maximum_bytes=4 * 1024 * 1024,
    )
    if _receipt_file_sha(root, attempt_ref["path"]) != attempt_ref.get("sha256"):
        raise AnalysisRibAnchorError("spool 退役 attempt SHA256 不一致")
    if attempt.get("anchor_receipt_sha256") != anchor_sha:
        raise AnalysisRibAnchorError("spool 退役 attempt 与 anchor 不一致")
    retired = row.get("retired_spool")
    if not isinstance(retired, Mapping):
        raise AnalysisRibAnchorError("retired_spool 证据缺失")
    spool_path = root / _safe_relative(retired.get("path"), "retired_spool.path")
    if retired.get("stable_identity_before_unlink") != attempt.get("spool", {}).get(
        "stable_identity_before_unlink"
    ):
        raise AnalysisRibAnchorError("spool 退役前稳定 identity 证据不一致")
    if spool_path.exists() or spool_path.is_symlink():
        raise AnalysisRibAnchorError("已声明退役的 spool 路径仍存在")
    return row


def _verify_execution_closure(
    root: Path,
    *,
    bindings: Mapping[str, str],
    expected_by_id: Mapping[str, AnalysisRibDescriptor],
) -> Mapping[str, Any]:
    """核验执行 FSM/监督证据；开放 gate 作为 blocker 返回，不伪装成失败数据。"""

    blockers: list[str] = []
    lock_path = root / "execution" / "LOCK"
    try:
        lock_metadata = lock_path.lstat()
    except OSError:
        blockers.append("execution_lock_missing")
    else:
        if stat.S_ISLNK(lock_metadata.st_mode) or not stat.S_ISREG(
            lock_metadata.st_mode
        ):
            blockers.append("execution_lock_not_regular")
    if _load_active(root) is not None:
        blockers.append("active_attempt_not_reconciled")
    if any((root / "spools").iterdir()):
        blockers.append("spool_or_spool_staging_not_retired")
    if _staging_temporary_bytes(root) != 0:
        blockers.append("publication_staging_not_reconciled")

    attempts: dict[str, Mapping[str, Any]] = {}
    attempt_refs: dict[str, Mapping[str, str]] = {}
    for path in sorted((root / "execution" / "attempts").glob("attempt-*.json")):
        row = _load_canonical_json(
            path,
            schema_version=EXECUTION_ATTEMPT_SCHEMA_VERSION,
            maximum_bytes=4 * 1024 * 1024,
        )
        attempt_id = row.get("attempt_id")
        artifact_id = row.get("artifact", {}).get("artifact_id")
        if (
            not isinstance(attempt_id, str)
            or _ATTEMPT_ID_RE.fullmatch(attempt_id) is None
            or attempt_id in attempts
            or artifact_id not in expected_by_id
            or row.get("artifact") != expected_by_id[str(artifact_id)].to_dict()
            or row.get("bindings") != dict(bindings)
        ):
            raise AnalysisRibAnchorError("execution attempt 身份或绑定不闭合")
        _raw, file_sha = _read_regular(path, maximum_bytes=4 * 1024 * 1024)
        attempts[attempt_id] = row
        attempt_refs[attempt_id] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": file_sha,
        }

    outcomes_by_attempt: dict[str, list[Mapping[str, Any]]] = {}
    child_outcome_count: dict[str, int] = {}
    complete_artifacts: set[str] = set()
    failed_attempt_ids: set[str] = set()
    allowed_statuses = {
        "checkpointed",
        "complete",
        "failed",
        "terminated",
        "crash_recovery_required",
        "reconciled_complete",
        "abnormal_exit_reconciled_failed",
    }
    child_statuses = {
        "checkpointed",
        "complete",
        "failed",
        "terminated",
        "crash_recovery_required",
    }
    for path in sorted((root / "execution" / "outcomes").glob("outcome-*.json")):
        row = _load_canonical_json(
            path,
            schema_version=EXECUTION_OUTCOME_SCHEMA_VERSION,
            maximum_bytes=8 * 1024 * 1024,
        )
        attempt_id = row.get("attempt_id")
        attempt = attempts.get(str(attempt_id))
        if (
            attempt is None
            or row.get("attempt_ref") != attempt_refs[str(attempt_id)]
            or row.get("artifact") != attempt.get("artifact")
            or row.get("bindings") != dict(bindings)
            or row.get("no_refund") is not True
            or row.get("status") not in allowed_statuses
        ):
            raise AnalysisRibAnchorError("execution outcome 与 attempt 不闭合")
        descriptor = expected_by_id[str(row["artifact"]["artifact_id"])]
        observation = row.get("raw_read_observation")
        if not isinstance(observation, Mapping):
            raise AnalysisRibAnchorError("execution outcome raw observation 缺失")
        if descriptor.ingestion_mode == "imported_full_window_seed":
            valid_observation = (
                observation.get("state") == "exact"
                and observation.get("bytes") == 0
            )
        elif observation.get("state") == "exact":
            valid_observation = observation.get("bytes") == descriptor.size_bytes
        else:
            valid_observation = (
                observation.get("state") == "unknown"
                and observation.get("minimum_bytes") == 0
                and observation.get("maximum_bytes") == descriptor.size_bytes
            )
        if not valid_observation:
            raise AnalysisRibAnchorError("execution outcome raw 字节边界不诚实")
        outcomes_by_attempt.setdefault(str(attempt_id), []).append(row)
        if row["status"] in child_statuses:
            child_outcome_count[str(attempt_id)] = (
                child_outcome_count.get(str(attempt_id), 0) + 1
            )
        if row["status"] in {
            "failed",
            "terminated",
            "abnormal_exit_reconciled_failed",
        }:
            failed_attempt_ids.add(str(attempt_id))
        if row["status"] in {"complete", "reconciled_complete"}:
            if observation.get("state") != "exact":
                raise AnalysisRibAnchorError("完成 outcome 必须有精确 raw 观测")
            complete_artifacts.add(descriptor.artifact_id)

    if set(expected_by_id) - complete_artifacts:
        blockers.append("anchor_execution_complete_outcome_missing")
    if any(attempt_id not in outcomes_by_attempt for attempt_id in attempts):
        blockers.append("execution_attempt_outcome_missing")

    failed_retirements: dict[str, int] = {}
    for path in sorted(
        (root / "retirements").glob("failed-attempt-retirement-*.json")
    ):
        row = _load_canonical_json(
            path,
            schema_version=FAILED_SPOOL_RETIREMENT_SCHEMA_VERSION,
            maximum_bytes=4 * 1024 * 1024,
        )
        attempt_id = row.get("attempt_id")
        attempt = attempts.get(str(attempt_id))
        retired_files = row.get("retired_files")
        if (
            attempt is None
            or row.get("artifact") != attempt.get("artifact")
            or row.get("status")
            != "failed_attempt_files_absent_and_directory_synced"
            or not isinstance(retired_files, list)
        ):
            raise AnalysisRibAnchorError("failed attempt retirement 身份不闭合")
        for retired in retired_files:
            if not isinstance(retired, Mapping):
                raise AnalysisRibAnchorError("failed retirement 文件证据非法")
            retired_path = root / _safe_relative(
                retired.get("path"), "failed_retirement.path"
            )
            if retired_path.exists() or retired_path.is_symlink():
                raise AnalysisRibAnchorError("failed retirement 声明文件仍存在")
            if not isinstance(retired.get("stable_identity_before_unlink"), Mapping):
                raise AnalysisRibAnchorError("failed retirement 缺少稳定 identity")
        failed_retirements[str(attempt_id)] = (
            failed_retirements.get(str(attempt_id), 0) + 1
        )
    if any(failed_retirements.get(attempt_id, 0) != 1 for attempt_id in failed_attempt_ids):
        blockers.append("failed_attempt_retirement_evidence_missing")

    production_supervisors: dict[str, int] = {}
    for path in sorted(
        (root / "execution" / "supervisors").glob("supervisor-*.json")
    ):
        row = _load_canonical_json(
            path,
            schema_version=SUPERVISOR_RECEIPT_SCHEMA_VERSION,
            maximum_bytes=4 * 1024 * 1024,
        )
        attempt_id = row.get("attempt_id")
        attempt = attempts.get(str(attempt_id))
        policy = row.get("policy")
        actions = row.get("actions")
        if (
            attempt is None
            or row.get("artifact_id")
            != attempt.get("artifact", {}).get("artifact_id")
            or row.get("bindings") != dict(bindings)
            or row.get("status") != "child_exit_observed_and_recorded"
            or not isinstance(policy, Mapping)
            or not isinstance(actions, Mapping)
            or any(
                not isinstance(actions.get(name), bool)
                for name in ("observed_420", "term_sent", "kill_sent")
            )
        ):
            raise AnalysisRibAnchorError("supervisor receipt 身份或动作不闭合")
        if (
            policy.get("observation_seconds") == 420.0
            and policy.get("term_seconds") == 540.0
            and policy.get("kill_seconds") == DEFAULT_HARD_STOP_SECONDS
            and policy.get("is_frozen_production_policy") is True
        ):
            production_supervisors[str(attempt_id)] = (
                production_supervisors.get(str(attempt_id), 0) + 1
            )

    for attempt_id in attempts:
        required = max(1, child_outcome_count.get(attempt_id, 0))
        if production_supervisors.get(attempt_id, 0) < required:
            blockers.append("frozen_420_540_590_supervisor_evidence_missing")
            break
    if not attempts:
        blockers.append("execution_attempt_evidence_missing")
    unique_blockers = sorted(set(blockers))
    return {
        "execution_ready": not unique_blockers,
        "blocking_reasons": unique_blockers,
        "execution_attempt_count": len(attempts),
        "execution_outcome_count": sum(len(rows) for rows in outcomes_by_attempt.values()),
        "production_supervisor_receipt_count": sum(production_supervisors.values()),
    }


def verify_analysis_rib_anchor_root(
    anchor_root: os.PathLike[str] | str,
    *,
    selection: Mapping[str, Any],
    profile: Mapping[str, Any],
    bindings: Mapping[str, Any],
) -> Mapping[str, Any]:
    """不读取原始 MRT，只核验完整 22-anchor 制品、账本、引用与退役链。"""

    root = Path(anchor_root)
    _assert_root_directory(root, "anchor_root")
    frozen_bindings = _bindings(bindings)
    genesis = _load_genesis(root)
    accounting = _prior_raw_accounting_from_payload(
        genesis.get("prior_raw_accounting")
    )
    plan = build_analysis_rib_plan(
        selection,
        profile,
        prior_raw_accounting=accounting,
        bindings=frozen_bindings,
        max_raw_read_bytes=genesis["max_raw_read_bytes_exclusive"],
    )
    if genesis.get("bindings") != frozen_bindings:
        raise AnalysisRibAnchorError("genesis bindings 与 verify 输入不一致")
    if plan.profile_sha256 != frozen_bindings["profile_sha256"] or (
        plan.selection_semantic_sha256
        != frozen_bindings["input_selection_sha256"]
    ):
        raise AnalysisRibAnchorError("verify selection/profile binding 不一致")
    if (
        plan.prior_raw_accounting.to_dict()
        != genesis.get("prior_raw_accounting")
        or plan.prior_raw_read_bytes != genesis.get("prior_raw_read_bytes")
    ):
        raise AnalysisRibAnchorError("verify prior raw frozen accounting 不一致")
    genesis_retention = _verify_fingerprint(
        genesis.get("retention_policy"),
        RETENTION_POLICY_SCHEMA_VERSION,
        "genesis.retention_policy",
    )
    if (
        genesis_retention.get("mapping_bundle_sha256")
        != frozen_bindings["mapping_sha256"]
        or genesis_retention.get("target_country") != "IR"
        or genesis_retention.get("union_semantics")
        != RAW_RETENTION_UNION_SEMANTICS
        or genesis_retention.get("unknown_or_conflict_policy")
        != "retain_unless_both_views_explicit_non_target"
        or [row.get("view") for row in genesis_retention.get("views", [])]
        != ["compatible", "revised"]
    ):
        raise AnalysisRibAnchorError("genesis raw retention union 合同不闭合")
    reservations = _load_reservations(root)
    expected_by_id = {item.artifact_id: item for item in plan.artifacts}
    expected_new_raw_by_id = {
        item.artifact_id: item
        for item in plan.artifacts
        if item.ingestion_mode == "new_raw"
    }
    reserved_ids = [row["artifact"]["artifact_id"] for row in reservations]
    if (
        len(reservations) < EXPECTED_NEW_RAW_ANCHOR_COUNT
        or set(reserved_ids) != set(expected_new_raw_by_id)
        or any(artifact_id not in expected_new_raw_by_id for artifact_id in reserved_ids)
    ):
        raise AnalysisRibAnchorError(
            "raw reservation 必须只覆盖 20 analysis + 1 baseline；imported seed 禁止重复预留"
        )
    cumulative = int(reservations[-1]["cumulative_after"])
    if cumulative >= int(genesis["max_raw_read_bytes_exclusive"]):
        raise AnalysisRibAnchorError("raw reservation 累计达到排他上限")

    receipt_paths = tuple(sorted((root / "receipts").glob("anchor-*.json")))
    if len(receipt_paths) != EXPECTED_ANCHOR_COUNT:
        raise AnalysisRibAnchorError("完整 anchor 根必须恰有 22 份 anchor receipt")
    current_attestation = build_parser_source_attestation(
        frozen_bindings["code_sha256"]
    )
    anchors: list[Mapping[str, Any]] = []
    seen_ids: set[str] = set()
    for path in receipt_paths:
        receipt = _load_canonical_json(
            path,
            schema_version=ANCHOR_RECEIPT_SCHEMA_VERSION,
            maximum_bytes=64 * 1024 * 1024,
        )
        _raw, file_sha = _read_regular(path, maximum_bytes=64 * 1024 * 1024)
        if path.name != f"anchor-{file_sha}.json":
            raise AnalysisRibAnchorError("anchor receipt 文件名不是内容地址")
        artifact = receipt.get("artifact")
        if not isinstance(artifact, Mapping):
            raise AnalysisRibAnchorError("anchor artifact 缺失")
        artifact_id = artifact.get("artifact_id")
        descriptor = expected_by_id.get(artifact_id)
        if descriptor is None or artifact != descriptor.to_dict():
            raise AnalysisRibAnchorError("anchor receipt artifact 越出 selection")
        if artifact_id in seen_ids:
            raise AnalysisRibAnchorError("anchor receipt artifact 重复")
        seen_ids.add(str(artifact_id))
        evidence_mode = receipt.get("evidence_mode", "new_raw_single_pass")
        resources = receipt.get("resources")
        checkpoint_policy = receipt.get("checkpoint_policy")
        if not isinstance(resources, Mapping) or not isinstance(
            checkpoint_policy, Mapping
        ):
            raise AnalysisRibAnchorError("anchor resource/runtime policy 缺失")
        peak_temporary = _nonnegative(
            resources.get("peak_temporary_bytes"), "resources.peak_temporary_bytes"
        )
        if (
            receipt.get("status") != "complete"
            or receipt.get("bindings") != frozen_bindings
            or receipt.get("parser_source_attestation") != current_attestation
            or receipt.get("retention_policy") != genesis_retention
            or receipt.get("update_curve_policy")
            != "independent_anchor_never_reset_update_curve"
            or resources.get("database_writes") != 0
            or peak_temporary >= DEFAULT_MAX_TEMPORARY_BYTES
        ):
            raise AnalysisRibAnchorError("anchor receipt 安全合同不闭合")
        if descriptor.ingestion_mode == "new_raw":
            if evidence_mode != "new_raw_single_pass":
                raise AnalysisRibAnchorError("new-raw anchor evidence_mode 非法")
            receipt_planned_checkpoint = _positive_seconds(
                checkpoint_policy.get("planned_checkpoint_seconds"),
                "checkpoint_policy.planned_checkpoint_seconds",
            )
            single_pass = receipt.get("compressed_single_pass_proof")
            if (
                checkpoint_policy.get("record_boundary")
                != "after_complete_physical_record"
                or receipt_planned_checkpoint >= DEFAULT_SOFT_STOP_SECONDS
                or checkpoint_policy.get("soft_stop_seconds_exclusive")
                != DEFAULT_SOFT_STOP_SECONDS
                or checkpoint_policy.get("hard_stop_seconds_exclusive")
                != DEFAULT_HARD_STOP_SECONDS
                or checkpoint_policy.get(
                    "process_supervisor_hard_timeout_seconds"
                )
                != DEFAULT_HARD_STOP_SECONDS
                or checkpoint_policy.get("enforcement_scope")
                != "external_parent_420_observe_540_term_590_kill_596_exit_required"
                or receipt.get("spool_retirement_required") is not True
                or not isinstance(single_pass, Mapping)
                or single_pass.get("file_sha256") != descriptor.file_sha256
                or single_pass.get("size_bytes") != descriptor.size_bytes
                or single_pass.get("read_passes") != 1
                or single_pass.get("gzip_eof_crc_verified") is not True
                or not isinstance(single_pass.get("stable_identity_before"), Mapping)
                or not isinstance(single_pass.get("stable_identity_after"), Mapping)
                or not _same_identity(
                    single_pass["stable_identity_before"],
                    single_pass["stable_identity_after"],
                )
            ):
                raise AnalysisRibAnchorError(
                    "new-raw single-pass/runtime 合同不闭合"
                )
        elif (
            descriptor.ingestion_mode == "imported_full_window_seed"
            and evidence_mode == "imported_full_window_seed_genesis"
        ):
            if (
                checkpoint_policy.get("record_boundary")
                != "imported_verified_seed_checkpoint"
                or checkpoint_policy.get("new_raw_opened") is not False
                or checkpoint_policy.get("new_raw_reservation_created") is not False
                or resources.get("reserved_raw_bytes") != 0
                or receipt.get("spool_retirement_required") is not False
            ):
                raise AnalysisRibAnchorError("imported seed no-reread 合同不闭合")
        else:
            raise AnalysisRibAnchorError("anchor ingestion/evidence mode 不一致")
        if not isinstance(receipt.get("anchor_id"), str) or _ANCHOR_ID_RE.fullmatch(
            receipt["anchor_id"]
        ) is None:
            raise AnalysisRibAnchorError("anchor_id 非法")
        observed_vp_ids = receipt.get("observed_vp_ids")
        if (
            not isinstance(observed_vp_ids, list)
            or observed_vp_ids != sorted(set(observed_vp_ids))
            or any(not isinstance(value, str) or not value for value in observed_vp_ids)
        ):
            raise AnalysisRibAnchorError("anchor observed_vp_ids 非规范")
        imported_replay: Optional[Mapping[str, Any]] = None
        if evidence_mode == "imported_full_window_seed_genesis":
            imported = receipt.get("imported_seed_evidence")
            if not isinstance(imported, Mapping):
                raise AnalysisRibAnchorError("imported seed evidence 缺失")
            imported_replay = _verify_and_replay_imported_seed(
                accounting, descriptor
            )
            if (
                receipt.get("prior_raw_accounting_fingerprint_sha256")
                != accounting.fingerprint_sha256
                or imported.get("journal_run_id") != accounting.run_id
                or imported.get("terminal_receipt_ref")
                != dict(accounting.terminal_receipt_ref)
                or imported.get("genesis_shards") != imported_replay["refs"]
                or imported.get("shard_chain_sha256")
                != imported_replay["shard_chain_sha256"]
                or imported.get("seed_parser")
                != imported_replay["bootstrap"].get("seed_parser")
                or imported.get("seed_spool_attestation")
                != imported_replay["bootstrap"].get("seed_spool_attestation")
                or observed_vp_ids != imported_replay["observed_vp_ids"]
            ):
                raise AnalysisRibAnchorError("imported seed receipt 与 frozen genesis 不闭合")
        event_rows: list[Mapping[str, Any]] = []
        raw_rows: list[Mapping[str, Any]] = []
        raw_ref_rows: list[Mapping[str, Any]] = []
        for field, target in (
            ("route_event_shards", event_rows),
            ("raw_record_shards", raw_rows),
            ("raw_ref_shards", raw_ref_rows),
        ):
            refs = receipt.get(field)
            if not isinstance(refs, list):
                raise AnalysisRibAnchorError(f"anchor.{field} 必须是数组")
            for ref in refs:
                target.extend(_verify_shard_ref(root, ref))
        if imported_replay is not None and any(
            (event_rows, raw_rows, raw_ref_rows)
        ):
            raise AnalysisRibAnchorError("imported seed 不得复制或伪造本地 RouteEvent shard")
        event_ids = [row.get("route_event_id") for row in event_rows]
        raw_ref_ids = [row.get("route_event_id") for row in raw_ref_rows]
        if sorted(event_ids) != sorted(raw_ref_ids) or len(event_ids) != len(
            set(event_ids)
        ):
            raise AnalysisRibAnchorError("RouteEvent 与 raw refs 未一一闭合")
        raw_ref_by_event = {
            row.get("route_event_id"): dict(row) for row in raw_ref_rows
        }
        rebuilt_events = []
        for row in event_rows:
            rebuilt = _route_event_from_payload(row)
            if asdict(rebuilt.raw_ref) != raw_ref_by_event.get(
                rebuilt.route_event_id
            ):
                raise AnalysisRibAnchorError(
                    "RouteEvent 与 raw ref 稳定坐标内容不一致"
                )
            rebuilt_events.append(rebuilt)
        raw_record_keys = {
            (row.get("artifact_id"), row.get("record_ordinal")) for row in raw_rows
        }
        if any(
            (row.get("artifact_id"), row.get("record_ordinal"))
            not in raw_record_keys
            for row in raw_ref_rows
        ):
            raise AnalysisRibAnchorError("raw ref 缺少对应 physical record 证据")
        state_info = receipt.get("route_state")
        projection_info = receipt.get("projection")
        if not isinstance(state_info, Mapping) or not isinstance(
            projection_info, Mapping
        ):
            raise AnalysisRibAnchorError("anchor state/projection 引用缺失")
        state_records = _verify_shard_ref(root, state_info["shard"])
        projection_records = _verify_shard_ref(root, projection_info["shard"])
        if len(state_records) != 1:
            raise AnalysisRibAnchorError("route_state shard 必须恰有一条记录")
        state = route_replay_state_from_payload(state_records[0])
        recomputed_state = (
            imported_replay["state"]
            if imported_replay is not None
            else extend_streaming_rib_seed(None, tuple(rebuilt_events))
        )
        recomputed_state_payload = route_replay_state_to_payload(recomputed_state)
        if (
            state_records[0]["state_fingerprint_sha256"]
            != state_info.get("semantic_sha256")
            or state_records[0] != recomputed_state_payload
            or _projection_sha256(projection_records)
            != projection_info.get("semantic_sha256")
            or tuple(projection_records) != _projection_rows(recomputed_state)
        ):
            raise AnalysisRibAnchorError(
                "RouteEvent+raw ref 重放与 route-state/projection 语义不一致"
            )
        expected_counts = (
            {
                "route_state_entries": len(state.entries),
                "route_events": imported_replay["route_event_count"],
                "raw_records": imported_replay["raw_record_count"],
                "raw_refs": imported_replay["raw_ref_count"],
            }
            if imported_replay is not None
            else {
                "route_state_entries": len(state.entries),
                "route_events": len(event_rows),
                "raw_records": len(raw_rows),
                "raw_refs": len(raw_ref_rows),
            }
        )
        if receipt.get("counts") != expected_counts:
            raise AnalysisRibAnchorError("anchor counts 与分片不一致")
        route_event_refs = receipt["route_event_shards"]
        raw_record_refs = receipt["raw_record_shards"]
        raw_refs = receipt["raw_ref_shards"]

        def shard_chain(refs: Sequence[Mapping[str, Any]]) -> str:
            return hashlib.sha256(
                canonical_json(
                    [ref["semantic_sha256"] for ref in refs]
                ).encode("utf-8")
            ).hexdigest()

        anchor_semantic = {
            "schema": ANCHOR_SEMANTIC_SCHEMA,
            "artifact": descriptor.to_dict(),
            "bindings": frozen_bindings,
            "evidence_mode": evidence_mode,
            "parser_source_attestation_fingerprint_sha256": current_attestation[
                "fingerprint_sha256"
            ],
            "retention_policy_fingerprint_sha256": genesis_retention[
                "fingerprint_sha256"
            ],
            "route_state_semantic_sha256": state_info["semantic_sha256"],
            "projection_semantic_sha256": projection_info["semantic_sha256"],
            "observed_vp_ids": receipt["observed_vp_ids"],
        }
        if imported_replay is not None:
            anchor_semantic["imported_seed_shard_chain_sha256"] = imported_replay[
                "shard_chain_sha256"
            ]
        else:
            anchor_semantic.update(
                {
                    "route_event_shard_chain_sha256": shard_chain(
                        route_event_refs
                    ),
                    "raw_record_shard_chain_sha256": shard_chain(
                        raw_record_refs
                    ),
                    "raw_ref_shard_chain_sha256": shard_chain(raw_refs),
                }
            )
        expected_anchor_semantic_sha = hashlib.sha256(
            canonical_json(anchor_semantic).encode("utf-8")
        ).hexdigest()
        if (
            receipt.get("anchor_semantic_sha256")
            != expected_anchor_semantic_sha
            or receipt.get("anchor_id")
            != "rib_anchor_v1_" + expected_anchor_semantic_sha[:32]
        ):
            raise AnalysisRibAnchorError("anchor semantic SHA/ID 不一致")
        if imported_replay is not None:
            if (
                receipt.get("reservation_ref") is not None
                or receipt.get("raw_open_claim_ref") is not None
                or resources.get("cumulative_reserved_raw_bytes")
                != accounting.cumulative_reserved_raw_bytes
            ):
                raise AnalysisRibAnchorError("imported seed 伪造 raw reservation/open")
            anchors.append(receipt)
            continue

        reservation_ref = receipt.get("reservation_ref")
        if not isinstance(reservation_ref, Mapping) or set(reservation_ref) != {
            "path",
            "sha256",
            "attempt_id",
            "sequence",
        }:
            raise AnalysisRibAnchorError("anchor reservation_ref 缺失")
        reservation_path = root / _safe_relative(
            reservation_ref.get("path"), "reservation_ref.path"
        )
        reservation_payload = _load_canonical_json(
            reservation_path,
            schema_version=RAW_RESERVATION_SCHEMA_VERSION,
            maximum_bytes=1024 * 1024,
        )
        _reservation_raw, reservation_sha = _read_regular(
            reservation_path, maximum_bytes=1024 * 1024
        )
        if (
            reservation_sha != reservation_ref.get("sha256")
            or reservation_payload.get("artifact") != descriptor.to_dict()
            or reservation_payload.get("attempt_id")
            != reservation_ref.get("attempt_id")
            or reservation_payload.get("sequence") != reservation_ref.get("sequence")
            or receipt.get("resources", {}).get("reserved_raw_bytes")
            != reservation_payload.get("reserved_raw_bytes")
            or receipt.get("resources", {}).get("cumulative_reserved_raw_bytes")
            != reservation_payload.get("cumulative_after")
        ):
            raise AnalysisRibAnchorError("anchor raw reservation SHA 不一致")
        token = RawReservationToken(
            attempt_id=str(reservation_payload["attempt_id"]),
            path=reservation_path.relative_to(root).as_posix(),
            sha256=reservation_sha,
            sequence=int(reservation_payload["sequence"]),
            descriptor=descriptor,
            reserved_raw_bytes=int(reservation_payload["reserved_raw_bytes"]),
            cumulative_reserved_raw_bytes=int(
                reservation_payload["cumulative_after"]
            ),
        )
        _verify_raw_open_claim(
            root,
            receipt.get("raw_open_claim_ref"),
            reservation=token,
            bindings=frozen_bindings,
        )
        _retirement_for_anchor(
            root,
            artifact_id=str(artifact_id),
            anchor_path=path.relative_to(root).as_posix(),
            anchor_sha=file_sha,
        )
        anchors.append(receipt)
    if seen_ids != set(expected_by_id):
        raise AnalysisRibAnchorError("22 张 selection RIB 未全部形成 anchor")
    if tuple((root / "spools").glob("*.mrt")):
        raise AnalysisRibAnchorError("完整 anchor 根仍残留 active spool")
    anchor_set_sha = hashlib.sha256(
        canonical_json(
            sorted(receipt["anchor_semantic_sha256"] for receipt in anchors)
        ).encode("utf-8")
    ).hexdigest()
    execution = _verify_execution_closure(
        root,
        bindings=frozen_bindings,
        expected_by_id=expected_by_id,
    )
    return {
        "schema_version": "rrc25-analysis-rib-anchor-verification/v1",
        "verified": True,
        "anchor_count": len(anchors),
        "analysis_rib_count": EXPECTED_ANALYSIS_RIB_COUNT,
        "imported_seed_anchor_count": 1,
        "new_raw_analysis_rib_count": 20,
        "baseline_reference_rib_count": EXPECTED_BASELINE_RIB_COUNT,
        "cumulative_reserved_raw_read_bytes": cumulative,
        "max_raw_read_bytes_exclusive": genesis["max_raw_read_bytes_exclusive"],
        "anchor_set_semantic_sha256": anchor_set_sha,
        "database_writes": 0,
        "update_curve_policy": "independent_anchor_never_reset_update_curve",
        **execution,
        "acceptance_state": (
            "anchor_verified_pending_overall_research_acceptance"
            if execution["execution_ready"]
            else "anchor_semantics_verified_execution_gates_open"
        ),
    }


def reconcile_anchor_with_update_boundary(
    anchor_receipt: Mapping[str, Any],
    update_boundary_snapshot: Mapping[str, Any],
) -> Mapping[str, Any]:
    """仅比较独立快照语义哈希；绝不把 RIB 写回或重置 UPDATE 曲线。"""

    receipt = _verify_fingerprint(
        anchor_receipt, ANCHOR_RECEIPT_SCHEMA_VERSION, "anchor_receipt"
    )
    if receipt.get("artifact", {}).get("role") != "analysis_rib":
        raise AnalysisRibAnchorError(
            "baseline_reference_rib 仅作独立参考，不能冒充窗口内 UPDATE 对账边界"
        )
    if not isinstance(update_boundary_snapshot, Mapping):
        raise AnalysisRibAnchorError("update_boundary_snapshot 必须是对象")
    required = {
        "schema_version",
        "source_kind",
        "collector_id",
        "boundary_at_utc",
        "route_state_semantic_sha256",
        "projection_semantic_sha256",
    }
    if set(update_boundary_snapshot) != required:
        raise AnalysisRibAnchorError("UPDATE boundary snapshot 字段不闭合")
    if (
        update_boundary_snapshot.get("schema_version")
        != "rrc25-update-boundary-snapshot/v1"
        or update_boundary_snapshot.get("source_kind")
        != "independent_update_replay_boundary"
    ):
        raise AnalysisRibAnchorError("UPDATE boundary snapshot 身份非法")
    if (
        update_boundary_snapshot.get("collector_id")
        != receipt["artifact"]["collector_id"]
        or _utc(
            update_boundary_snapshot.get("boundary_at_utc"),
            "update_boundary_snapshot.boundary_at_utc",
        )
        != _utc(receipt["boundary_at_utc"], "anchor.boundary_at_utc")
    ):
        raise AnalysisRibAnchorError("RIB anchor 与 UPDATE snapshot 边界不一致")
    update_state_sha = _sha(
        update_boundary_snapshot.get("route_state_semantic_sha256"),
        "update route-state SHA",
    )
    update_projection_sha = _sha(
        update_boundary_snapshot.get("projection_semantic_sha256"),
        "update projection SHA",
    )
    comparisons = {
        "route_state_semantic_sha256_equal": (
            receipt["route_state"]["semantic_sha256"] == update_state_sha
        ),
        "projection_semantic_sha256_equal": (
            receipt["projection"]["semantic_sha256"] == update_projection_sha
        ),
    }
    # route-state SHA 绑定各自来源 raw ref，RIB 与 UPDATE 即使可见路由完全相同
    # 也通常不同；跨来源一致性的冻结判据只能是去除 provenance/time 后的投影。
    status = (
        "consistent"
        if comparisons["projection_semantic_sha256_equal"]
        else "mismatch"
    )
    semantic = {
        "anchor_id": receipt["anchor_id"],
        "artifact_id": receipt["artifact"]["artifact_id"],
        "boundary_at_utc": receipt["boundary_at_utc"],
        "status": status,
        "consistency_basis": "source_independent_projection_semantic_sha256",
        "comparisons": comparisons,
        "anchor_route_state_semantic_sha256": receipt["route_state"][
            "semantic_sha256"
        ],
        "update_route_state_semantic_sha256": update_state_sha,
        "anchor_projection_semantic_sha256": receipt["projection"][
            "semantic_sha256"
        ],
        "update_projection_semantic_sha256": update_projection_sha,
        "update_curve_action": "none_independent_reconciliation_only",
        "causal_claim_allowed": False,
    }
    return _fingerprinted(RECONCILIATION_SCHEMA_VERSION, semantic)


__all__ = (
    "ANCHOR_RECEIPT_SCHEMA_VERSION",
    "AnalysisRibAnchorError",
    "AnalysisRibDescriptor",
    "AnalysisRibPlan",
    "AnalysisRibRetentionPolicy",
    "AnchorSegmentResult",
    "EXPECTED_ANALYSIS_RIB_COUNT",
    "EXPECTED_ANCHOR_COUNT",
    "EXPECTED_BASELINE_RIB_COUNT",
    "EXPECTED_NEW_RAW_ANCHOR_COUNT",
    "PRIOR_JOURNAL_VERIFICATION_SCHEMA_VERSION",
    "RawReservationToken",
    "VerifiedPriorRawAccounting",
    "build_analysis_rib_plan",
    "build_analysis_rib_retention_policy",
    "build_parser_source_attestation",
    "build_source_independent_route_projection",
    "build_update_boundary_snapshot",
    "compute_prior_journal_verification_candidate",
    "cumulative_reserved_raw_bytes",
    "initialize_anchor_workspace",
    "import_full_window_seed_anchor",
    "load_verified_prior_raw_accounting",
    "load_prior_raw_accounting_from_verification_receipt",
    "publish_prior_journal_verification_receipt",
    "reconcile_anchor_with_update_boundary",
    "reserve_raw_read",
    "run_analysis_rib_anchor_segment",
    "verify_analysis_rib_anchor_root",
)
