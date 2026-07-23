"""伊朗研究 pilot 的有界、可恢复 MRT 流式 worker。

该 worker 只负责“解析与回放层”：读取 input selection 中唯一的 seed RIB
以及 ``[window.start, pilot_end)`` 内的 UPDATE，生成受 IR 研究语义约束的
RouteEvent、五分钟快照、physical-record 审计与槽计数。它不写数据库、不
调用生产接口，也不生成报告或发布包。

关键边界：

* 原始制品路径逐级拒绝符号链接，打开后固定普通文件 inode/size；完整 pass
  必须同时复核 file SHA256。每次 pass 的实际压缩读取字节都累计，恢复时的
  重读不会被隐去；
* RIB/UPDATE 均只在完整 physical-record 边界检查资源门禁。540 秒软停会
  写不可覆盖的文件检查点；恢复从 record ordinal 边界重读并核对已处理 raw
  record，不依赖 gzip offset；
* UPDATE 只读一遍。动态发现的 IR/映射未知前缀从首次发现记录起纳入，发现
  前上下文显式标为 unknown，绝不为补历史而未经估算地倒带二读；
* ``retained_origin_unknown`` 不会被过滤。worker 输出其 prefix/record/VP/raw
  ref 人口，并指出严格全人口曲线可能被该不确定性阻断。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
from typing import (
    Any,
    Callable,
    FrozenSet,
    Iterable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from ...route_event import AsPathSegment, ParsedRouteElement
from ..resource_gate import (
    ResourceLimits,
    ResourceUsage,
    WriteTarget,
    evaluate_resource_gate,
)
from .country_impact import (
    CONFLICT,
    RESOLVED,
    UNKNOWN,
    CountryMappingView,
    RawRetentionMappingUnion,
    derive_origin_asns,
)
from .file_artifacts import canonical_json, write_canonical_json
from .input_resolver import SELECTION_SCHEMA_VERSION
from .replay_persistence import (
    route_replay_state_from_payload,
    route_replay_state_to_payload,
)
from .rib_adapter import (
    RETAINED_ORIGIN_UNKNOWN,
    AdaptedRibRecord,
    ObservedVpAccumulator,
    iter_rib_artifact_records,
    iter_rib_spool_artifact_records,
)
from .rib_parser import (
    RibPeerIndexContext,
    RibRecordBoundary,
    build_rib_decompressed_spool,
    verify_rib_decompressed_spool,
)
from .rib_prefilter import RibPrefilterError, validate_rib_prefilter
from .state_replay import (
    InputGap,
    RawRecordRef,
    ReplaySnapshot,
    ResearchRouteEvent,
    RouteLastChange,
    RouteReplayState,
    RouteStateEntry,
    RouteStateKey,
    apply_streaming_update_batch,
    build_five_minute_snapshot,
    build_research_route_event,
    extend_streaming_rib_seed,
)
from .update_adapter import (
    NOTIFICATION_RECORD,
    OPEN_RECORD,
    STATE_CHANGE_RECORD,
    RawRecordEvidence,
    iter_adapted_update_records,
)


UTC = timezone.utc
WORKER_SCHEMA_VERSION = "rrc25-bounded-pilot-worker-result/v1"
CHECKPOINT_SCHEMA_VERSION = "rrc25-bounded-pilot-worker-checkpoint/v1"
CHECKPOINT_FINGERPRINT_SCHEMA = (
    "rrc25_bounded_pilot_worker_checkpoint_fingerprint_v1"
)
FULL_SEED_CHECKPOINT_SCHEMA_VERSION = (
    "rrc25-bounded-pilot-worker-full-seed-checkpoint/v3"
)
FULL_SEED_CHECKPOINT_FINGERPRINT_SCHEMA = (
    "rrc25_bounded_pilot_worker_full_seed_checkpoint_fingerprint_v3"
)
SEED_SPOOL_ATTESTATION_SCHEMA_VERSION = "rrc25-seed-spool-attestation/v1"
PROBE_TERMINAL_ACCOUNTING_SCHEMA_VERSION = (
    "rrc25-native-probe-terminal-accounting/v1"
)
PROBE_TERMINAL_ACCOUNTING_FINGERPRINT_SCHEMA = (
    "rrc25_native_probe_terminal_accounting_v1"
)
SEED_RAW_RESERVATION_SCHEMA_VERSION = "rrc25-seed-raw-reservation/v1"
SEED_RAW_RESERVATION_FINGERPRINT_SCHEMA = "rrc25_seed_raw_reservation_v1"
DIAGNOSTIC_CHECKPOINT_SCHEMA_VERSION = (
    "rrc25-bounded-pilot-worker-diagnostic-checkpoint/v1"
)
DIAGNOSTIC_CHECKPOINT_FINGERPRINT_SCHEMA = (
    "rrc25_bounded_pilot_worker_diagnostic_checkpoint_fingerprint_v1"
)
SELECTION_ID_SCHEMA = "rrc25_country_outage_input_selection_id_v1"
_SHA256 = frozenset("0123456789abcdef")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FIVE_MINUTES = timedelta(minutes=5)
# full-seed checkpoint 的磁盘文件采用 deterministic gzip；512 MiB 约束因此
# 作用于真正占用 checkpoint 临时根的压缩字节。解压后的规范 JSON 另设独立
# 2 GB 硬上限，既给完整 IR seed 留出空间，也拒绝 gzip bomb。
_MAX_CHECKPOINT_BYTES = 512 * 1024 * 1024
_MAX_CHECKPOINT_UNCOMPRESSED_BYTES = 2_000_000_000
_FULL_SEED_CHECKPOINT_GZIP_LEVEL = 1
_DEFAULT_PLANNED_SEED_CHECKPOINT_SECONDS = 420.0
_FULL_SEED_CHECKPOINT_POLICY_FIELDS = frozenset(
    {
        "planned_seed_checkpoint_seconds",
        "worker_soft_stop_seconds",
        "max_worker_runtime_seconds",
        "active_root_retention_policy",
        "automatic_deletion",
        "archive_before_reclamation_required",
        "archive_hash_and_receipt_required",
        "capacity_exhaustion_behavior",
    }
)
_FULL_SEED_ACTIVE_ROOT_RETENTION_POLICY = (
    "immutable_accumulate_no_automatic_reclamation_v1"
)
_FULL_SEED_CAPACITY_EXHAUSTION_BEHAVIOR = "fail_closed_before_publish"
_FULL_SEED_CHECKPOINT_FIELDS = frozenset(
    {
        "schema_version",
        "checkpoint_fingerprint_sha256",
        "code_identity_sha256",
        "selection_id",
        "selection_semantic_fingerprint_sha256",
        "mapping_fingerprint_sha256",
        "raw_retention_mapping_kind",
        "raw_retention_mapping_fingerprint_sha256",
        "seed_spool_attestation_fingerprint_sha256",
        "pilot_start_utc",
        "pilot_end_exclusive_utc",
        "checkpoint_sequence",
        "position",
        "seed_progress",
        "seed_spool",
        "state",
        "seed_state_at_window_start",
        "resume_policy",
        "route_events",
        "raw_audits",
        "tracked_prefixes",
        "ambiguity",
        "observed_vp_ids",
        "gaps",
        "errors",
        "resources",
        "seed_read_ledger",
        "checkpoint_policy",
    }
)
# seed RIB 的状态合并成本与当前状态规模相关。按完整 physical-record 批量合并，
# 既避免对大量过滤后空记录反复重建全量状态，又给 540 秒软停预留可控的 flush
# 尾延迟；单个 physical record 始终保持原子性，即使它本身超过事件阈值。
_DEFAULT_SEED_BATCH_MAX_ROUTE_EVENTS = 1_048_576
_DEFAULT_SEED_BATCH_MAX_RECORDS = 65_536
_MAX_SEED_BATCH_MAX_ROUTE_EVENTS = 2_097_152
_MAX_SEED_BATCH_MAX_RECORDS = 262_144


class BoundedPilotWorkerError(ValueError):
    """worker 输入、原始制品或恢复检查点不能安全执行。"""


def _verified_probe_terminal_accounting(
    value: Any,
    *,
    expected_prior_raw_bytes: int,
    selection_id: str,
    selection_sha256: str,
    code_identity_sha256: Optional[str],
) -> Mapping[str, Any]:
    required = {
        "schema_version",
        "ledger_id",
        "prepared_directory",
        "prepared_receipt_ref",
        "prepared_bindings",
        "selection_id",
        "terminal_receipt_ref",
        "terminal_receipt_kind",
        "attempt_count",
        "outcome_count",
        "prior_accounting",
        "initial_observed_lower_bound_new_raw_bytes",
        "initial_reserved_upper_bound_new_raw_bytes",
        "probe_observed_lower_bound_new_raw_bytes",
        "probe_observed_upper_bound_new_raw_bytes",
        "cumulative_reserved_new_raw_bytes",
        "cumulative_semantics",
        "reservation_refund_policy",
        "chain_refs_sha256",
        "accounting_fingerprint_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise BoundedPilotWorkerError(
            "prior_raw_accounting 必须是已核验 probe terminal 的闭合摘要"
        )
    semantic = dict(value)
    supplied = semantic.pop("accounting_fingerprint_sha256", None)
    expected_fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": PROBE_TERMINAL_ACCOUNTING_FINGERPRINT_SCHEMA,
                "accounting": semantic,
            }
        ).encode("utf-8")
    ).hexdigest()
    bindings = value.get("prepared_bindings")
    initial_lower = value.get("initial_observed_lower_bound_new_raw_bytes")
    initial_upper = value.get("initial_reserved_upper_bound_new_raw_bytes")
    probe_lower = value.get("probe_observed_lower_bound_new_raw_bytes")
    probe_upper = value.get("probe_observed_upper_bound_new_raw_bytes")
    cumulative = value.get("cumulative_reserved_new_raw_bytes")
    counts = (value.get("attempt_count"), value.get("outcome_count"))
    if (
        value.get("schema_version") != PROBE_TERMINAL_ACCOUNTING_SCHEMA_VERSION
        or supplied != expected_fingerprint
        or value.get("selection_id") != selection_id
        or not isinstance(bindings, Mapping)
        or bindings.get("input_selection_sha256") != selection_sha256
        or (
            code_identity_sha256 is not None
            and bindings.get("code_sha256") != code_identity_sha256
        )
        or value.get("terminal_receipt_kind")
        not in {"zero_genesis", "imported_genesis", "outcome"}
        or value.get("cumulative_semantics")
        != "nonrefundable_reserved_upper_bound"
        or value.get("reservation_refund_policy")
        != "never_refund_even_on_failure_timeout_or_retry"
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in (
                initial_lower,
                initial_upper,
                probe_lower,
                probe_upper,
                cumulative,
                *counts,
            )
        )
        or initial_lower > initial_upper
        or probe_lower > probe_upper
        or cumulative < initial_upper
        or cumulative != expected_prior_raw_bytes
        or counts[1] != counts[0]
    ):
        raise BoundedPilotWorkerError(
            "probe terminal accounting 身份、上下界或累计值不闭合"
        )
    for name in ("prepared_receipt_ref", "terminal_receipt_ref"):
        ref = value.get(name)
        if (
            not isinstance(ref, Mapping)
            or set(ref) != {"path", "sha256", "size_bytes"}
            or not isinstance(ref.get("path"), str)
            or not isinstance(ref.get("sha256"), str)
            or len(ref["sha256"]) != 64
            or isinstance(ref.get("size_bytes"), bool)
            or not isinstance(ref.get("size_bytes"), int)
            or ref["size_bytes"] <= 0
        ):
            raise BoundedPilotWorkerError(f"probe accounting {name} 非法")
    return dict(value)


def _verified_seed_raw_reservation(
    value: Any,
    *,
    probe_accounting: Mapping[str, Any],
    expected_prior_raw_bytes: int,
    selection_id: str,
    seed_artifact: Mapping[str, Any],
    code_identity_sha256: Optional[str],
) -> Mapping[str, Any]:
    """验证 seed raw 在首次 open 前已 create-only 预留整份压缩制品。"""

    required = {
        "schema_version",
        "ledger_id",
        "prepared_directory",
        "prepared_bindings",
        "selection_id",
        "probe_terminal_accounting_fingerprint_sha256",
        "probe_terminal_receipt_ref",
        "attempt_ref",
        "attempt_id",
        "sequence",
        "seed_artifact",
        "previous_seed_terminal_ref",
        "prior_cumulative_reserved_new_raw_bytes",
        "reserved_new_raw_bytes",
        "cumulative_reserved_new_raw_bytes",
        "reservation_refund_policy",
        "reservation_fingerprint_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise BoundedPilotWorkerError("seed_raw_reservation 字段不闭合")
    semantic = dict(value)
    supplied = semantic.pop("reservation_fingerprint_sha256", None)
    expected_fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": SEED_RAW_RESERVATION_FINGERPRINT_SCHEMA,
                "reservation": semantic,
            }
        ).encode("utf-8")
    ).hexdigest()
    bindings = value.get("prepared_bindings")
    reserved = value.get("reserved_new_raw_bytes")
    prior = value.get("prior_cumulative_reserved_new_raw_bytes")
    cumulative = value.get("cumulative_reserved_new_raw_bytes")
    sequence = value.get("sequence")
    seed_fields = (
        "artifact_id",
        "file_sha256",
        "size_bytes",
        "relative_path",
        "collector_id",
        "artifact_time_utc",
    )
    expected_seed = {field: seed_artifact.get(field) for field in seed_fields}
    if (
        value.get("schema_version") != SEED_RAW_RESERVATION_SCHEMA_VERSION
        or supplied != expected_fingerprint
        or value.get("ledger_id") != probe_accounting.get("ledger_id")
        or value.get("selection_id") != selection_id
        or not isinstance(value.get("prepared_directory"), str)
        or not value["prepared_directory"]
        or not isinstance(bindings, Mapping)
        or bindings != probe_accounting.get("prepared_bindings")
        or (
            code_identity_sha256 is not None
            and bindings.get("code_sha256") != code_identity_sha256
        )
        or value.get("probe_terminal_accounting_fingerprint_sha256")
        != probe_accounting.get("accounting_fingerprint_sha256")
        or value.get("probe_terminal_receipt_ref")
        != probe_accounting.get("terminal_receipt_ref")
        or value.get("seed_artifact") != expected_seed
        or isinstance(prior, bool)
        or not isinstance(prior, int)
        or prior != expected_prior_raw_bytes
        or isinstance(reserved, bool)
        or not isinstance(reserved, int)
        or reserved != seed_artifact.get("size_bytes")
        or reserved <= 0
        or isinstance(cumulative, bool)
        or not isinstance(cumulative, int)
        or cumulative != prior + reserved
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence <= 0
        or not isinstance(value.get("attempt_id"), str)
        or re.fullmatch(r"seed_v1_[0-9a-f]{32}", value["attempt_id"]) is None
        or value.get("reservation_refund_policy")
        != "never_refund_even_on_failure_timeout_or_retry"
    ):
        raise BoundedPilotWorkerError(
            "seed raw reservation 与 probe/selection/code/artifact/cumulative 不闭合"
        )
    for name in ("probe_terminal_receipt_ref", "attempt_ref"):
        ref = value.get(name)
        if (
            not isinstance(ref, Mapping)
            or set(ref) != {"path", "sha256", "size_bytes"}
            or not isinstance(ref.get("path"), str)
            or _SHA256_RE.fullmatch(str(ref.get("sha256"))) is None
            or isinstance(ref.get("size_bytes"), bool)
            or not isinstance(ref.get("size_bytes"), int)
            or ref["size_bytes"] <= 0
        ):
            raise BoundedPilotWorkerError(f"seed reservation {name} 非法")
    previous = value.get("previous_seed_terminal_ref")
    if previous is not None and (
        not isinstance(previous, Mapping)
        or set(previous) != {"path", "sha256", "size_bytes"}
        or _SHA256_RE.fullmatch(str(previous.get("sha256"))) is None
        or isinstance(previous.get("size_bytes"), bool)
        or not isinstance(previous.get("size_bytes"), int)
        or previous["size_bytes"] <= 0
    ):
        raise BoundedPilotWorkerError("seed reservation previous terminal ref 非法")
    return dict(value)


@dataclass(frozen=True)
class SlotCount:
    slot_start_utc: str
    slot_end_exclusive_utc: str
    input_state: str
    announce_count: Optional[int]
    withdraw_count: Optional[int]
    retained_announce_count: Optional[int]
    retained_withdraw_count: Optional[int]
    physical_record_count: Optional[int]
    missing_reasons: Tuple[str, ...]


@dataclass(frozen=True)
class AmbiguityPopulation:
    ambiguous_element_count: int
    ambiguous_prefixes: Tuple[str, ...]
    ambiguous_record_refs: Tuple[Mapping[str, Any], ...]
    ambiguous_vp_ids: Tuple[str, ...]
    strict_population_state: str
    mapped_compatible_cohort_state: str
    quality_blockers: Tuple[str, ...]


@dataclass(frozen=True)
class BoundedPilotWorkerResult:
    schema_version: str
    selection_id: str
    pilot_start_utc: str
    pilot_end_exclusive_utc: str
    status: str
    incomplete_reason: Optional[str]
    state: RouteReplayState
    seed_state_at_window_start: Optional[RouteReplayState]
    snapshots: Tuple[ReplaySnapshot, ...]
    route_events: Tuple[ResearchRouteEvent, ...]
    raw_audits: Tuple[RawRecordEvidence, ...]
    slot_counts: Tuple[SlotCount, ...]
    observed_vp_ids: Tuple[str, ...]
    tracked_prefixes: Tuple[str, ...]
    pre_discovery_context_unknown: Tuple[Mapping[str, Any], ...]
    ambiguity: AmbiguityPopulation
    gaps: Tuple[InputGap, ...]
    errors: Tuple[Mapping[str, Any], ...]
    resources: Mapping[str, Any]
    checkpoint_path: Optional[str]


class _CompressedHashReader:
    """对一次 RIB gzip pass 计数并哈希，不提供 seek/倒带。"""

    def __init__(self, descriptor: int, expected_size: int) -> None:
        self._descriptor = descriptor
        self._stream = os.fdopen(descriptor, "rb", buffering=0)
        self._expected_size = expected_size
        self._before = os.fstat(descriptor)
        self._digest = hashlib.sha256()
        self.bytes_read = 0
        self._closed = False

    def read(self, size: int = -1) -> bytes:
        block = self._stream.read(size)
        if block:
            self._digest.update(block)
            self.bytes_read += len(block)
        return block

    def tell(self) -> int:
        return self.bytes_read

    def verify_complete(self, expected_sha256: str) -> None:
        while self.read(1024 * 1024):
            pass
        after = os.fstat(self._stream.fileno())
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(self._before, key) != getattr(after, key) for key in identity):
            raise BoundedPilotWorkerError("RIB 原始制品在读取期间发生变化")
        if self.bytes_read != self._expected_size:
            raise BoundedPilotWorkerError("RIB 实际压缩读取量与 manifest size 不一致")
        if self._digest.hexdigest() != expected_sha256:
            raise BoundedPilotWorkerError("RIB file_sha256 与完整 pass 不一致")

    def close(self) -> None:
        if not self._closed:
            self._stream.close()
            self._closed = True


class _DeferredSeedEvidence:
    """只在真正发布 checkpoint 时物化 seed 边界与 VP 人口。

    RRC25 的 peer index 通常在整个 RIB 中保持不变。若每个 physical record
    都调用 ``RibPeerIndexContext.checkpoint_binding()``，会反复重建完整 peer
    列表；同理，逐 record 读取 ``ObservedVpAccumulator.observed_vp_ids`` 会
    重复排序同一人口。本对象在 record 热路径只替换不可变对象引用，并把两类
    物化推迟到一次 worker 退出或 checkpoint 构造边界。

    从 checkpoint 恢复时保留既有 mapping，不改变 parser 的 resume 输入合同。
    """

    def __init__(self) -> None:
        self.previous_record_boundary: Optional[
            Mapping[str, Any] | RibRecordBoundary
        ] = None
        self.peer_index_context: Optional[
            Mapping[str, Any] | RibPeerIndexContext
        ] = None
        self._accumulator: Optional[ObservedVpAccumulator] = None

    def restore(
        self,
        previous_record_boundary: Mapping[str, Any],
        peer_index_context: Optional[Mapping[str, Any]],
    ) -> None:
        self.previous_record_boundary = previous_record_boundary
        self.peer_index_context = peer_index_context

    def attach_accumulator(self, accumulator: ObservedVpAccumulator) -> None:
        self._accumulator = accumulator

    def observe_boundary(
        self,
        boundary: RibRecordBoundary,
        peer_context: Optional[RibPeerIndexContext],
    ) -> None:
        # 热路径只保存 frozen dataclass 引用；不得在这里调用 checkpoint_binding。
        self.previous_record_boundary = boundary
        self.peer_index_context = peer_context

    @staticmethod
    def _binding(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        binding = value.checkpoint_binding()
        if not isinstance(binding, Mapping):  # pragma: no cover - parser 合同保护
            raise BoundedPilotWorkerError("seed checkpoint binding 必须是 mapping")
        return dict(binding)

    def checkpoint_bindings(
        self,
    ) -> Tuple[dict[str, Any], Optional[dict[str, Any]]]:
        if self.previous_record_boundary is None:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint 缺少 previous record boundary"
            )
        previous = self._binding(self.previous_record_boundary)
        peer = (
            self._binding(self.peer_index_context)
            if self.peer_index_context is not None
            else None
        )
        return previous, peer

    def merge_observed_vps(self, target: set[str]) -> None:
        """每个 worker segment 至多读取并排序一次 accumulator 人口。"""

        accumulator = self._accumulator
        if accumulator is None:
            return
        target.update(accumulator.observed_vp_ids)
        self._accumulator = None


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BoundedPilotWorkerError(f"{field} 必须是秒级 UTC Z 时间")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise BoundedPilotWorkerError(f"{field} 不是合法秒级 UTC 时间") from error
    return parsed


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256 for character in value)
    ):
        raise BoundedPilotWorkerError(f"{field} 必须是 64 位小写 SHA256")
    return value


def _full_seed_checkpoint_policy(
    *,
    planned_seed_checkpoint_seconds: float,
    worker_soft_stop_seconds: float,
    max_worker_runtime_seconds: float,
) -> Mapping[str, Any]:
    """返回 checkpoint 内冻结的主动保留与显式归档政策。"""

    return {
        "planned_seed_checkpoint_seconds": planned_seed_checkpoint_seconds,
        "worker_soft_stop_seconds": worker_soft_stop_seconds,
        "max_worker_runtime_seconds": max_worker_runtime_seconds,
        "active_root_retention_policy": (
            _FULL_SEED_ACTIVE_ROOT_RETENTION_POLICY
        ),
        "automatic_deletion": False,
        "archive_before_reclamation_required": True,
        "archive_hash_and_receipt_required": True,
        "capacity_exhaustion_behavior": (
            _FULL_SEED_CAPACITY_EXHAUSTION_BEHAVIOR
        ),
    }


def _validate_full_seed_checkpoint_policy(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != (
        _FULL_SEED_CHECKPOINT_POLICY_FIELDS
    ):
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint checkpoint_policy 字段不闭合"
        )
    for name in (
        "planned_seed_checkpoint_seconds",
        "worker_soft_stop_seconds",
        "max_worker_runtime_seconds",
    ):
        item = value[name]
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            or float(item) <= 0
        ):
            raise BoundedPilotWorkerError(
                f"完整 seed checkpoint checkpoint_policy.{name} 非法"
            )
    if not (
        float(value["planned_seed_checkpoint_seconds"])
        < float(value["worker_soft_stop_seconds"])
        < float(value["max_worker_runtime_seconds"])
    ):
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint checkpoint_policy 边界非法"
        )
    expected_lifecycle = {
        "active_root_retention_policy": (
            _FULL_SEED_ACTIVE_ROOT_RETENTION_POLICY
        ),
        "automatic_deletion": False,
        "archive_before_reclamation_required": True,
        "archive_hash_and_receipt_required": True,
        "capacity_exhaustion_behavior": (
            _FULL_SEED_CAPACITY_EXHAUSTION_BEHAVIOR
        ),
    }
    if any(value.get(name) != item for name, item in expected_lifecycle.items()):
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint checkpoint_policy 生命周期政策非法"
        )
    return dict(value)


def validate_seed_spool_attestation(
    attestation: Mapping[str, Any],
    *,
    seed_artifact: Mapping[str, Any],
) -> Mapping[str, Any]:
    """严格验证冻结的单-pass seed 解压身份与 manifest 绑定。

    attestation 仅提供容量与内容身份；首次构建 spool 时仍必须重新流式
    核验压缩与解压 SHA/size，不能用冻结文件替代真实完整读取。
    """

    if not isinstance(attestation, Mapping) or set(attestation) != {
        "schema_version",
        "artifact_binding",
        "decompressed",
        "measurement",
        "semantic_fingerprint_sha256",
    }:
        raise BoundedPilotWorkerError("seed spool attestation 顶层字段不闭合")
    if attestation.get("schema_version") != SEED_SPOOL_ATTESTATION_SCHEMA_VERSION:
        raise BoundedPilotWorkerError("seed spool attestation schema_version 不支持")
    artifact = attestation.get("artifact_binding")
    decompressed = attestation.get("decompressed")
    measurement = attestation.get("measurement")
    if not all(
        isinstance(value, Mapping)
        for value in (artifact, decompressed, measurement)
    ):
        raise BoundedPilotWorkerError("seed spool attestation 结构不闭合")
    if (
        set(artifact)
        != {"artifact_id", "file_sha256", "compressed_size_bytes"}
        or set(decompressed) != {"size_bytes", "sha256"}
        or set(measurement)
        != {"method", "measured_at_utc", "raw_read_pass_count"}
    ):
        raise BoundedPilotWorkerError("seed spool attestation 子字段不闭合")
    expected_artifact = {
        "artifact_id": seed_artifact.get("artifact_id"),
        "file_sha256": seed_artifact.get("file_sha256"),
        "compressed_size_bytes": seed_artifact.get("size_bytes"),
    }
    if dict(artifact) != expected_artifact:
        raise BoundedPilotWorkerError(
            "seed spool attestation 与 state_seed_rib 制品身份不一致"
        )
    _sha256(
        artifact.get("file_sha256"),
        "attestation.artifact_binding.file_sha256",
    )
    decompressed_size = decompressed.get("size_bytes")
    if (
        isinstance(decompressed_size, bool)
        or not isinstance(decompressed_size, int)
        or decompressed_size <= 0
    ):
        raise BoundedPilotWorkerError(
            "attestation.decompressed.size_bytes 必须为正整数"
        )
    _sha256(decompressed.get("sha256"), "attestation.decompressed.sha256")
    if (
        measurement.get("method")
        != "full_streaming_gzip_decompression_sha256_v1"
        or measurement.get("raw_read_pass_count") != 1
    ):
        raise BoundedPilotWorkerError("seed spool attestation measurement 非法")
    _utc(
        measurement.get("measured_at_utc"),
        "attestation.measurement.measured_at_utc",
    )
    semantic = {
        "schema_version": attestation["schema_version"],
        "artifact_binding": dict(artifact),
        "decompressed": dict(decompressed),
        "measurement": dict(measurement),
    }
    expected_fingerprint = hashlib.sha256(
        canonical_json(semantic).encode("utf-8")
    ).hexdigest()
    fingerprint = _sha256(
        attestation.get("semantic_fingerprint_sha256"),
        "attestation.semantic_fingerprint_sha256",
    )
    if fingerprint != expected_fingerprint:
        raise BoundedPilotWorkerError("seed spool attestation 内容指纹不一致")
    return {**semantic, "semantic_fingerprint_sha256": fingerprint}


def _validate_seed_spool_binding(
    value: Any,
    *,
    attestation: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "file_name",
        "size_bytes",
        "sha256",
    }:
        raise BoundedPilotWorkerError("完整 seed checkpoint seed_spool 字段不闭合")
    file_name = value.get("file_name")
    if (
        value.get("schema_version") != "rrc25-seed-decompressed-spool/v1"
        or not isinstance(file_name, str)
        or not file_name
        or Path(file_name).name != file_name
        or file_name in {".", ".."}
    ):
        raise BoundedPilotWorkerError("完整 seed checkpoint seed_spool 身份非法")
    expected = attestation["decompressed"]
    if (
        value.get("size_bytes") != expected["size_bytes"]
        or value.get("sha256") != expected["sha256"]
    ):
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint seed_spool 与 attestation 不一致"
        )
    return dict(value)


def _validate_previous_record_boundary(
    value: Any,
    *,
    next_record_ordinal: int,
    next_record_offset: int,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "record_ordinal",
        "record_offset",
        "record_length",
        "record_sha256",
    }:
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint previous_record_boundary 字段不闭合"
        )
    ordinal = value.get("record_ordinal")
    offset = value.get("record_offset")
    length = value.get("record_length")
    if (
        any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in (ordinal, offset, length)
        )
        or length < 12
        or ordinal + 1 != next_record_ordinal
        or offset + length != next_record_offset
    ):
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint previous_record_boundary 与 next 坐标不闭合"
        )
    _sha256(value.get("record_sha256"), "previous_record_boundary.record_sha256")
    return dict(value)


def _validate_peer_index_context(
    value: Any,
    *,
    next_record_offset: int,
) -> Optional[Mapping[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "record_ordinal",
        "record_offset",
        "record_length",
        "record_sha256",
        "peers",
    }:
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint peer_index_context 字段不闭合"
        )
    ordinal = value.get("record_ordinal")
    offset = value.get("record_offset")
    length = value.get("record_length")
    peers = value.get("peers")
    if (
        value.get("schema_version") != "rrc25-rib-peer-index-context/v1"
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in (ordinal, offset, length)
        )
        or length < 12
        or offset + length > next_record_offset
        or not isinstance(peers, list)
        or not peers
    ):
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint peer_index_context 非法"
        )
    _sha256(value.get("record_sha256"), "peer_index_context.record_sha256")
    for row in peers:
        if not isinstance(row, Mapping) or set(row) != {"peer_ip", "peer_asn"}:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint peer_index_context.peers 非法"
            )
        try:
            peer_ip = ipaddress.ip_address(row.get("peer_ip")).compressed
        except (TypeError, ValueError) as error:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint peer_index_context.peer_ip 非法"
            ) from error
        peer_asn = row.get("peer_asn")
        if (
            peer_ip != row.get("peer_ip")
            or isinstance(peer_asn, bool)
            or not isinstance(peer_asn, int)
            or not 0 <= peer_asn <= 0xFFFFFFFF
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint peer_index_context.peers 非法"
            )
    return {
        **dict(value),
        "peers": [dict(row) for row in peers],
    }


def _selection_identity(selection: Mapping[str, Any]) -> Tuple[str, str]:
    if not isinstance(selection, Mapping):
        raise BoundedPilotWorkerError("selection 必须是 input_resolver 对象")
    if selection.get("schema_version") != SELECTION_SCHEMA_VERSION:
        raise BoundedPilotWorkerError("selection schema_version 非法")
    semantic = {
        key: value
        for key, value in selection.items()
        if key not in {"selection_id", "semantic_fingerprint_sha256"}
    }
    semantic_hash = hashlib.sha256(canonical_json(semantic).encode("utf-8")).hexdigest()
    if selection.get("semantic_fingerprint_sha256") != semantic_hash:
        raise BoundedPilotWorkerError("selection semantic fingerprint 不一致")
    expected_id = "rsel_v1_" + hashlib.sha256(
        canonical_json({"schema": SELECTION_ID_SCHEMA, "selection": semantic}).encode(
            "utf-8"
        )
    ).hexdigest()[:32]
    if selection.get("selection_id") != expected_id:
        raise BoundedPilotWorkerError("selection_id 与冻结语义不一致")
    return expected_id, semantic_hash


def _mapping_identity(mapping: CountryMappingView) -> str:
    if not isinstance(mapping, CountryMappingView):
        raise BoundedPilotWorkerError("country_mapping 必须是冻结 CountryMappingView")
    semantic = {
        "view": mapping.view,
        "target_country": mapping.target_country,
        "source_sha256": mapping.source_sha256,
        "source_ref": mapping.source_ref,
        "revised_lineage": (
            asdict(mapping.revised_lineage)
            if mapping.revised_lineage is not None
            else None
        ),
        "assignments": [
            {
                "asn": row.asn,
                "countries": list(row.countries),
                "mapping_state": row.mapping_state,
            }
            for row in mapping.assignments
        ],
    }
    return hashlib.sha256(canonical_json(semantic).encode("utf-8")).hexdigest()


def _raw_retention_identity(
    raw_retention_mapping: Optional[RawRetentionMappingUnion],
    *,
    statistical_mapping: CountryMappingView,
    statistical_mapping_hash: str,
) -> Tuple[str, str]:
    """返回 raw 保留口径的类型与身份，绝不把 union 当统计视图。"""

    if raw_retention_mapping is None:
        kind = "single_statistical_mapping_legacy"
        semantic = {
            "kind": kind,
            "target_country": statistical_mapping.target_country,
            "statistical_mapping_fingerprint_sha256": statistical_mapping_hash,
        }
    else:
        if not isinstance(raw_retention_mapping, RawRetentionMappingUnion):
            raise BoundedPilotWorkerError(
                "raw_retention_mapping 必须是 RawRetentionMappingUnion"
            )
        if raw_retention_mapping.target_country != statistical_mapping.target_country:
            raise BoundedPilotWorkerError(
                "raw-retention union 与 statistical mapping 目标国家不一致"
            )
        view_bindings = tuple(
            {
                "view": view.view,
                "mapping_fingerprint_sha256": _mapping_identity(view),
                "source_sha256": view.source_sha256,
                "source_ref": view.source_ref,
            }
            for view in raw_retention_mapping.views
        )
        matching_statistical_views = tuple(
            row
            for row in view_bindings
            if row["view"] == statistical_mapping.view
            and row["mapping_fingerprint_sha256"] == statistical_mapping_hash
        )
        if len(matching_statistical_views) != 1:
            raise BoundedPilotWorkerError(
                "raw-retention union 未绑定当前 statistical mapping 视图"
            )
        kind = "compatible_revised_raw_retention_union"
        semantic = {
            "kind": kind,
            "semantics": raw_retention_mapping.semantics,
            "target_country": raw_retention_mapping.target_country,
            "views": view_bindings,
        }
    fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": "rrc25_bounded_pilot_raw_retention_identity_v1",
                "raw_retention_mapping": semantic,
            }
        ).encode("utf-8")
    ).hexdigest()
    return kind, fingerprint


def _artifact_relative(artifact: Mapping[str, Any]) -> PurePosixPath:
    if not isinstance(artifact, Mapping):
        raise BoundedPilotWorkerError("artifact 必须是对象")
    collector = artifact.get("collector_id")
    relative = artifact.get("relative_path")
    if not isinstance(collector, str) or not isinstance(relative, str):
        raise BoundedPilotWorkerError("artifact collector/relative_path 非法")
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[0] != collector
    ):
        raise BoundedPilotWorkerError("artifact relative_path 越出 collector")
    return path


def _safe_artifact_path(root: Path, artifact: Mapping[str, Any]) -> Path:
    try:
        root_meta = root.lstat()
    except OSError as error:
        raise BoundedPilotWorkerError("artifact_root 不可读") from error
    if stat.S_ISLNK(root_meta.st_mode) or not stat.S_ISDIR(root_meta.st_mode):
        raise BoundedPilotWorkerError("artifact_root 必须是非符号链接目录")
    relative = _artifact_relative(artifact)
    current = root
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as error:
            raise BoundedPilotWorkerError("manifest 原始制品路径不可读") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise BoundedPilotWorkerError("manifest 原始制品路径禁止符号链接")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise BoundedPilotWorkerError("原始制品父路径必须是目录")
    if not stat.S_ISREG(current.lstat().st_mode):
        raise BoundedPilotWorkerError("原始制品必须是普通文件")
    expected_size = artifact.get("size_bytes")
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size <= 0
        or current.stat().st_size != expected_size
    ):
        raise BoundedPilotWorkerError("原始制品 size 与 manifest 不一致")
    _sha256(artifact.get("file_sha256"), "artifact.file_sha256")
    return current


def _open_rib_reader(path: Path, artifact: Mapping[str, Any]) -> _CompressedHashReader:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise BoundedPilotWorkerError("无法只读打开 RIB 原始制品") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BoundedPilotWorkerError("RIB 原始制品不是普通文件")
        if metadata.st_size != artifact["size_bytes"]:
            raise BoundedPilotWorkerError("RIB 打开后 size 与 manifest 不一致")
        return _CompressedHashReader(descriptor, artifact["size_bytes"])
    except BaseException:
        os.close(descriptor)
        raise


def _path_to_payload(path: Optional[Tuple[AsPathSegment, ...]]) -> Any:
    if path is None:
        return None
    return [
        {"segment_type": segment.segment_type, "asns": list(segment.asns)}
        for segment in path
    ]


def _path_from_payload(value: Any) -> Optional[Tuple[AsPathSegment, ...]]:
    if value is None:
        return None
    if not isinstance(value, list):
        raise BoundedPilotWorkerError("checkpoint AS_PATH 非法")
    try:
        return tuple(
            AsPathSegment(row["segment_type"], tuple(row["asns"])) for row in value
        )
    except (KeyError, TypeError, ValueError) as error:
        raise BoundedPilotWorkerError("checkpoint AS_PATH 非法") from error


def _event_to_payload(event: ResearchRouteEvent) -> dict[str, Any]:
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
        "as_path": _path_to_payload(event.as_path),
        "quality_flags": list(event.quality_flags),
    }


def _event_from_payload(row: Any) -> ResearchRouteEvent:
    if not isinstance(row, Mapping):
        raise BoundedPilotWorkerError("checkpoint RouteEvent 非法")
    try:
        rebuilt = build_research_route_event(
            artifact_id=row["artifact_id"],
            file_sha256=row["file_sha256"],
            collector_id=row["collector_id"],
            artifact_slot_utc=row["artifact_slot_utc"],
            record_ordinal=row["record_ordinal"],
            element_ordinal=row["element_ordinal"],
            element=ParsedRouteElement(
                event_time_utc=row["event_time_utc"],
                peer_ip=row["peer_ip"],
                peer_asn=row["peer_asn"],
                action=row["action"],
                prefix=row["prefix"],
                afi_safi=row["afi_safi"],
                as_path=_path_from_payload(row["as_path"]),
                quality_flags=tuple(row["quality_flags"]),
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise BoundedPilotWorkerError("checkpoint RouteEvent 无法恢复") from error
    expected = _event_to_payload(rebuilt)
    if dict(row) != expected:
        raise BoundedPilotWorkerError("checkpoint RouteEvent 稳定身份或规范字段冲突")
    return rebuilt


def _raw_to_payload(raw: RawRecordEvidence) -> dict[str, Any]:
    return asdict(raw)


def _raw_from_payload(row: Any) -> RawRecordEvidence:
    if not isinstance(row, Mapping):
        raise BoundedPilotWorkerError("checkpoint raw audit 非法")
    try:
        rebuilt = RawRecordEvidence(**dict(row))
    except TypeError as error:
        raise BoundedPilotWorkerError("checkpoint raw audit 字段不闭合") from error
    if _raw_to_payload(rebuilt) != dict(row):
        raise BoundedPilotWorkerError("checkpoint raw audit 非规范")
    return rebuilt


def _raw_ref_to_payload(raw: RawRecordRef) -> dict[str, Any]:
    return asdict(raw)


def _key_to_payload(key: RouteStateKey) -> dict[str, Any]:
    return asdict(key)


def _entry_to_payload(entry: RouteStateEntry) -> dict[str, Any]:
    return {
        "key": _key_to_payload(entry.key),
        "peer_ip": entry.peer_ip,
        "peer_asn": entry.peer_asn,
        "as_path": _path_to_payload(entry.as_path),
        "quality_flags": list(entry.quality_flags),
        "last_action": entry.last_action,
        "last_event_time_utc": entry.last_event_time_utc,
        "last_raw_ref": _raw_ref_to_payload(entry.last_raw_ref),
    }


def _change_to_payload(change: RouteLastChange) -> dict[str, Any]:
    return {
        "key": _key_to_payload(change.key),
        "action": change.action,
        "event_time_utc": change.event_time_utc,
        "as_path": _path_to_payload(change.as_path),
        "quality_flags": list(change.quality_flags),
        "raw_ref": _raw_ref_to_payload(change.raw_ref),
    }


def _snapshot_to_payload(snapshot: ReplaySnapshot) -> dict[str, Any]:
    return {
        "slot_start_utc": snapshot.slot_start_utc,
        "slot_end_exclusive_utc": snapshot.slot_end_exclusive_utc,
        "boundary": snapshot.boundary,
        "continuity_state": snapshot.continuity_state,
        "missing_reasons": list(snapshot.missing_reasons),
        "route_count": snapshot.route_count,
        "entries": [_entry_to_payload(entry) for entry in snapshot.entries],
        "slot_changes": [_change_to_payload(change) for change in snapshot.slot_changes],
    }


def _snapshot_from_payload(row: Any) -> ReplaySnapshot:
    """借助状态反序列化器严格恢复一个快照的 entry/change。"""

    if not isinstance(row, Mapping):
        raise BoundedPilotWorkerError("checkpoint snapshot 非法")
    try:
        semantic = {
            "schema_version": "rrc25-country-outage-route-state/v1",
            "continuity_state": row["continuity_state"],
            "missing_reasons": row["missing_reasons"],
            "entries": row["entries"],
            "latest_changes": row["slot_changes"],
            "processed_route_event_ids": sorted(
                change["raw_ref"]["route_event_id"] for change in row["slot_changes"]
            ),
            "last_order_key": None,
        }
        # latest_changes 并不一定覆盖所有 entries，不能直接伪装完整 replay state。
        # 因而对结构字段做本地恢复，再由当前 worker 的 checkpoint 指纹绑定。
        del semantic
        entries = tuple(_entry_from_payload(value) for value in row["entries"])
        changes = tuple(_change_from_payload(value) for value in row["slot_changes"])
        snapshot = ReplaySnapshot(
            slot_start_utc=row["slot_start_utc"],
            slot_end_exclusive_utc=row["slot_end_exclusive_utc"],
            boundary=row["boundary"],
            continuity_state=row["continuity_state"],
            missing_reasons=tuple(row["missing_reasons"]),
            route_count=row["route_count"],
            entries=entries,
            slot_changes=changes,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise BoundedPilotWorkerError("checkpoint snapshot 无法恢复") from error
    if _snapshot_to_payload(snapshot) != dict(row):
        raise BoundedPilotWorkerError("checkpoint snapshot 非规范")
    return snapshot


def _raw_ref_from_payload(row: Any) -> RawRecordRef:
    if not isinstance(row, Mapping):
        raise BoundedPilotWorkerError("checkpoint raw ref 非法")
    try:
        return RawRecordRef(**dict(row))
    except TypeError as error:
        raise BoundedPilotWorkerError("checkpoint raw ref 字段不闭合") from error


def _key_from_payload(row: Any) -> RouteStateKey:
    if not isinstance(row, Mapping):
        raise BoundedPilotWorkerError("checkpoint state key 非法")
    try:
        key = RouteStateKey(**dict(row))
        network = ipaddress.ip_network(key.prefix, strict=True)
    except (TypeError, ValueError) as error:
        raise BoundedPilotWorkerError("checkpoint state key 无法恢复") from error
    expected_afi = "ipv4_unicast" if network.version == 4 else "ipv6_unicast"
    if network.compressed != key.prefix or key.afi_safi != expected_afi:
        raise BoundedPilotWorkerError("checkpoint state key prefix/AFI 不一致")
    return key


def _entry_from_payload(row: Any) -> RouteStateEntry:
    if not isinstance(row, Mapping):
        raise BoundedPilotWorkerError("checkpoint state entry 非法")
    try:
        path = _path_from_payload(row["as_path"])
        if path is None:
            raise BoundedPilotWorkerError("可见 entry 缺少 AS_PATH")
        return RouteStateEntry(
            key=_key_from_payload(row["key"]),
            peer_ip=row["peer_ip"],
            peer_asn=row["peer_asn"],
            as_path=path,
            quality_flags=tuple(row["quality_flags"]),
            last_action=row["last_action"],
            last_event_time_utc=row["last_event_time_utc"],
            last_raw_ref=_raw_ref_from_payload(row["last_raw_ref"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise BoundedPilotWorkerError("checkpoint state entry 无法恢复") from error


def _change_from_payload(row: Any) -> RouteLastChange:
    if not isinstance(row, Mapping):
        raise BoundedPilotWorkerError("checkpoint state change 非法")
    try:
        return RouteLastChange(
            key=_key_from_payload(row["key"]),
            action=row["action"],
            event_time_utc=row["event_time_utc"],
            as_path=_path_from_payload(row["as_path"]),
            quality_flags=tuple(row["quality_flags"]),
            raw_ref=_raw_ref_from_payload(row["raw_ref"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise BoundedPilotWorkerError("checkpoint state change 无法恢复") from error


def _gap_to_payload(gap: InputGap) -> dict[str, Any]:
    return asdict(gap)


def _gap_from_payload(row: Any) -> InputGap:
    if not isinstance(row, Mapping):
        raise BoundedPilotWorkerError("checkpoint gap 非法")
    try:
        return InputGap(**dict(row))
    except (TypeError, ValueError) as error:
        raise BoundedPilotWorkerError("checkpoint gap 无法恢复") from error


def _slot_count_from_payload(row: Any) -> SlotCount:
    if not isinstance(row, Mapping):
        raise BoundedPilotWorkerError("checkpoint slot count 非法")
    try:
        value = dict(row)
        value["missing_reasons"] = tuple(value["missing_reasons"])
        return SlotCount(**value)
    except (KeyError, TypeError, ValueError) as error:
        raise BoundedPilotWorkerError("checkpoint slot count 无法恢复") from error


def _origin_relevance(
    event: ResearchRouteEvent, mapping: CountryMappingView
) -> Tuple[bool, bool, bool]:
    """返回 possible_target、ambiguous_origin、mapped_target。"""

    if event.action == "withdraw":
        return False, False, False
    assert event.as_path is not None
    resolution = derive_origin_asns(event.as_path)
    if resolution.state == UNKNOWN:
        return True, True, False
    memberships = tuple(mapping.target_membership(asn) for asn in resolution.origins)
    mapped_target = any(value is True for value in memberships)
    possible = any(value is not False for value in memberships)
    ambiguous = resolution.state == CONFLICT or any(value is None for value in memberships)
    return possible, ambiguous, mapped_target


def _raw_retention_possible(
    event: ResearchRouteEvent | ParsedRouteElement,
    membership: Callable[[int], Optional[bool]],
) -> bool:
    """仅决定 raw/RouteEvent 是否保留，不产生任何统计归属。"""

    if event.action == "withdraw":
        return False
    if event.as_path is None:
        return True
    resolution = derive_origin_asns(event.as_path)
    if resolution.state == UNKNOWN:
        return True
    return any(membership(asn) is not False for asn in resolution.origins)


def _raw_record_ref(raw: RawRecordEvidence) -> dict[str, Any]:
    return {
        "artifact_id": raw.artifact_id,
        "file_sha256": raw.file_sha256,
        "artifact_slot_utc": raw.artifact_slot_utc,
        "record_ordinal": raw.record_ordinal,
        "record_offset": raw.record_offset,
        "record_length": raw.record_length,
        "raw_record_sha256": raw.raw_record_sha256,
    }


def _stream_stats(stream: Any) -> Mapping[str, Any]:
    stats = getattr(stream, "statistics", None)
    if not isinstance(stats, Mapping):
        raise BoundedPilotWorkerError(
            "UPDATE record stream 必须暴露可审计 statistics"
        )
    return stats


def _stream_bytes(stats: Mapping[str, Any]) -> int:
    complete = stats.get("compressed_size_bytes")
    observed = stats.get("compressed_bytes_read_observed", 0)
    value = complete if isinstance(complete, int) else observed
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BoundedPilotWorkerError("UPDATE stream 压缩读取字节统计非法")
    return value


def _resource_result(
    *,
    raw_bytes: int,
    elapsed: float,
    temporary_bytes: int,
    output_bytes: int,
    limits: ResourceLimits,
    checkpoint_directory: Path,
) -> Any:
    return evaluate_resource_gate(
        ResourceUsage(
            new_raw_read_bytes=raw_bytes,
            process_runtime_seconds=elapsed,
            temporary_bytes=temporary_bytes,
            output_bytes=output_bytes,
            write_targets=(
                WriteTarget(
                    label="bounded_pilot_checkpoint",
                    location=str(checkpoint_directory.resolve()),
                    kind="directory",
                ),
            ),
            phase="observed",
        ),
        limits=limits,
    )


def _assert_checkpoint_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise BoundedPilotWorkerError("checkpoint_directory 不存在或不可读") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise BoundedPilotWorkerError("checkpoint_directory 必须是非符号链接目录")


def _checkpoint_directory_bytes(path: Path) -> int:
    """统计 checkpoint 临时根的当前逻辑字节并拒绝不可分类条目。"""

    _assert_checkpoint_directory(path)
    total = 0
    try:
        entries = tuple(path.iterdir())
    except OSError as error:
        raise BoundedPilotWorkerError("checkpoint_directory 无法扫描") from error
    for entry in entries:
        try:
            metadata = entry.lstat()
        except OSError as error:
            raise BoundedPilotWorkerError(
                "checkpoint_directory 条目不可读"
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise BoundedPilotWorkerError(
                "checkpoint_directory 只能包含非符号链接普通文件"
            )
        total += metadata.st_size
    return total


def _seed_spool_destination(
    checkpoint_directory: Path,
    attestation: Mapping[str, Any],
) -> Path:
    fingerprint = _sha256(
        attestation.get("semantic_fingerprint_sha256"),
        "attestation.semantic_fingerprint_sha256",
    )
    decompressed_sha256 = _sha256(
        attestation["decompressed"].get("sha256"),
        "attestation.decompressed.sha256",
    )
    return checkpoint_directory / (
        f"seed-spool.{fingerprint[:16]}.{decompressed_sha256[:16]}.mrt"
    )


def _read_checkpoint(
    path: Path,
    *,
    fingerprint_schema: str = CHECKPOINT_FINGERPRINT_SCHEMA,
) -> Mapping[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise BoundedPilotWorkerError("resume checkpoint 不可读") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise BoundedPilotWorkerError("resume checkpoint 必须是非符号链接普通文件")
    if metadata.st_size <= 0 or metadata.st_size > _MAX_CHECKPOINT_BYTES:
        raise BoundedPilotWorkerError("resume checkpoint 大小越界")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        chunks = []
        size = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
            size += len(block)
            if size > _MAX_CHECKPOINT_BYTES:
                raise BoundedPilotWorkerError("resume checkpoint 超过读取上限")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, key) != getattr(after, key) for key in identity):
        raise BoundedPilotWorkerError("resume checkpoint 在读取期间发生变化")
    stored_bytes = b"".join(chunks)
    if stored_bytes.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(stored_bytes), mode="rb") as stream:
                payload_bytes = stream.read(_MAX_CHECKPOINT_UNCOMPRESSED_BYTES + 1)
                if len(payload_bytes) > _MAX_CHECKPOINT_UNCOMPRESSED_BYTES:
                    raise BoundedPilotWorkerError(
                        "resume checkpoint 解压后超过 2 GB 硬上限"
                    )
                if stream.read(1):
                    raise BoundedPilotWorkerError(
                        "resume checkpoint 解压后超过 2 GB 硬上限"
                    )
        except (OSError, EOFError) as error:
            raise BoundedPilotWorkerError(
                "resume checkpoint gzip EOF/CRC 校验失败"
            ) from error
    else:
        # 向后兼容已经发布的 v1/v2 plain canonical JSON checkpoint。
        payload_bytes = stored_bytes
        if len(payload_bytes) > _MAX_CHECKPOINT_UNCOMPRESSED_BYTES:
            raise BoundedPilotWorkerError("resume checkpoint JSON 超过 2 GB 硬上限")
    if not payload_bytes.endswith(b"\n") or payload_bytes.count(b"\n") != 1:
        raise BoundedPilotWorkerError("resume checkpoint 必须是一行规范 JSON")
    try:
        payload = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BoundedPilotWorkerError("resume checkpoint JSON 非法") from error
    if not isinstance(payload, Mapping):
        raise BoundedPilotWorkerError("resume checkpoint 顶层必须是对象")
    semantic = dict(payload)
    fingerprint = semantic.pop("checkpoint_fingerprint_sha256", None)
    expected = hashlib.sha256(
        canonical_json(
            {"schema": fingerprint_schema, "checkpoint": semantic}
        ).encode("utf-8")
    ).hexdigest()
    if fingerprint != expected:
        raise BoundedPilotWorkerError("resume checkpoint 内容指纹不一致")
    if canonical_json(dict(payload)).encode("utf-8") + b"\n" != payload_bytes:
        raise BoundedPilotWorkerError("resume checkpoint 不是规范 JSON 编码")
    return payload


def _checkpoint_storage_bytes(path: Path) -> int:
    """返回 checkpoint 临时根中实际占用的逻辑字节。"""

    try:
        metadata = path.lstat()
    except OSError as error:
        raise BoundedPilotWorkerError("checkpoint 文件不可读") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise BoundedPilotWorkerError("checkpoint 必须是非符号链接普通文件")
    if metadata.st_size <= 0 or metadata.st_size > _MAX_CHECKPOINT_BYTES:
        raise BoundedPilotWorkerError("checkpoint 存储字节越界")
    return metadata.st_size


def _checkpoint_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    semantic = dict(context)
    semantic["schema_version"] = CHECKPOINT_SCHEMA_VERSION
    fingerprint = hashlib.sha256(
        canonical_json(
            {"schema": CHECKPOINT_FINGERPRINT_SCHEMA, "checkpoint": semantic}
        ).encode("utf-8")
    ).hexdigest()
    return {**semantic, "checkpoint_fingerprint_sha256": fingerprint}


def _publish_checkpoint(
    directory: Path,
    *,
    selection_id: str,
    sequence: int,
    context: Mapping[str, Any],
) -> Tuple[Path, int]:
    payload = _checkpoint_payload(context)
    fingerprint = payload["checkpoint_fingerprint_sha256"]
    name = f"{selection_id}.worker.{sequence:04d}.{fingerprint[:16]}.json"
    target = directory / name
    published = write_canonical_json(
        target, payload, kind="bounded_pilot_worker_checkpoint"
    )
    return published.path, published.size_bytes


def _full_seed_checkpoint_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    semantic = dict(context)
    semantic["schema_version"] = FULL_SEED_CHECKPOINT_SCHEMA_VERSION
    fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": FULL_SEED_CHECKPOINT_FINGERPRINT_SCHEMA,
                "checkpoint": semantic,
            }
        ).encode("utf-8")
    ).hexdigest()
    return {**semantic, "checkpoint_fingerprint_sha256": fingerprint}


def _publish_full_seed_checkpoint(
    directory: Path,
    *,
    selection_id: str,
    sequence: int,
    context: Mapping[str, Any],
    maximum_temporary_bytes: int,
) -> Tuple[Path, int, int]:
    if (
        isinstance(maximum_temporary_bytes, bool)
        or not isinstance(maximum_temporary_bytes, int)
        or maximum_temporary_bytes <= 0
    ):
        raise BoundedPilotWorkerError("maximum_temporary_bytes 必须为正整数")
    current_directory_bytes = _checkpoint_directory_bytes(directory)
    prepared = dict(context)
    resources = dict(prepared.get("resources", {}))
    prepared["resources"] = resources
    compressed_payload: bytes | None = None
    encoded_size = 0
    for _iteration in range(16):
        payload = _full_seed_checkpoint_payload(prepared)
        encoded = (canonical_json(payload) + "\n").encode("utf-8")
        encoded_size = len(encoded)
        if encoded_size > _MAX_CHECKPOINT_UNCOMPRESSED_BYTES:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint 规范 JSON 超过 2 GB，拒绝发布"
            )
        compressed_buffer = io.BytesIO()
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=_FULL_SEED_CHECKPOINT_GZIP_LEVEL,
            fileobj=compressed_buffer,
            mtime=0,
        ) as compressed:
            compressed.write(encoded)
        candidate = compressed_buffer.getvalue()
        if len(candidate) > _MAX_CHECKPOINT_BYTES:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint 压缩文件超过 512 MiB，拒绝发布"
            )
        projected_total = current_directory_bytes + len(candidate)
        if projected_total >= maximum_temporary_bytes:
            raise BoundedPilotWorkerError(
                "checkpoint 目录写入瞬间总量达到临时空间审批边界"
            )
        prior_peak = resources.get("peak_temporary_bytes", 0)
        if (
            isinstance(prior_peak, bool)
            or not isinstance(prior_peak, int)
            or prior_peak < 0
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint peak_temporary_bytes 非法"
            )
        actual_peak = max(prior_peak, projected_total)
        if resources.get("peak_temporary_bytes") == actual_peak:
            compressed_payload = candidate
            break
        resources["peak_temporary_bytes"] = actual_peak
    else:
        raise BoundedPilotWorkerError("完整 seed checkpoint 大小计算未收敛")
    fingerprint = payload["checkpoint_fingerprint_sha256"]
    name = (
        f"{selection_id}.worker.{sequence:04d}.full-seed."
        f"{fingerprint[:16]}.json.gz"
    )
    if compressed_payload is None:  # pragma: no cover - 上方收敛或抛错。
        raise BoundedPilotWorkerError("完整 seed checkpoint 压缩结果缺失")
    target = directory / name
    if target.exists() or target.is_symlink():
        raise FileExistsError("完整 seed checkpoint 已存在，拒绝覆盖")
    temporary = directory / f".{name}.tmp-{os.getpid()}-{time.time_ns()}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o640,
    )
    try:
        view = memoryview(compressed_payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("完整 seed checkpoint 写入未取得进展")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, target, follow_symlinks=False)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    if target.stat().st_size != len(compressed_payload):
        raise BoundedPilotWorkerError("完整 seed checkpoint 实际压缩字节与预计算不一致")
    observed_total = _checkpoint_directory_bytes(directory)
    if observed_total != projected_total:
        raise BoundedPilotWorkerError("checkpoint 目录在发布期间发生变化")
    return target, len(compressed_payload), actual_peak


def _diagnostic_checkpoint_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    """构造不可恢复的小型诊断检查点。

    UPDATE 流在 record 边界触发软停时，关闭 bgpdump 流可能需要等待子进程与
    后台线程退出。完整 worker checkpoint 还会两次规范化/校验 seed 与当前
    RouteReplayState，并复制 pending event，不能作为超时前的第一份落盘证据。
    诊断检查点只证明触发时的输入绑定、完整 record 边界和计数，明确不携带
    恢复所需状态，任何入口都不得把它当作可恢复 checkpoint。
    """

    semantic = dict(context)
    semantic["schema_version"] = DIAGNOSTIC_CHECKPOINT_SCHEMA_VERSION
    fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": DIAGNOSTIC_CHECKPOINT_FINGERPRINT_SCHEMA,
                "checkpoint": semantic,
            }
        ).encode("utf-8")
    ).hexdigest()
    return {**semantic, "checkpoint_fingerprint_sha256": fingerprint}


def _publish_diagnostic_checkpoint(
    directory: Path,
    *,
    selection_id: str,
    sequence: int,
    context: Mapping[str, Any],
) -> Tuple[Path, int]:
    payload = _diagnostic_checkpoint_payload(context)
    fingerprint = payload["checkpoint_fingerprint_sha256"]
    name = (
        f"{selection_id}.worker.{sequence:04d}.diagnostic."
        f"{fingerprint[:16]}.json"
    )
    published = write_canonical_json(
        directory / name,
        payload,
        kind="bounded_pilot_worker_diagnostic_checkpoint",
    )
    return published.path, published.size_bytes


def _expected_slots(start: datetime, end: datetime) -> Tuple[datetime, ...]:
    values = []
    current = start
    while current < end:
        values.append(current)
        current += _FIVE_MINUTES
    return tuple(values)


def verify_full_seed_checkpoint(
    checkpoint_path: os.PathLike[str] | str,
    *,
    selection: Mapping[str, Any],
    country_mapping: CountryMappingView,
    raw_retention_mapping: Optional[RawRetentionMappingUnion] = None,
    seed_spool_attestation: Mapping[str, Any],
    pilot_end_exclusive_utc: str,
    code_identity_sha256: str,
) -> Mapping[str, Any]:
    """只读验证完整 seed checkpoint，不打开任何 MRT 制品。"""

    selection_id, selection_hash = _selection_identity(selection)
    mapping_hash = _mapping_identity(country_mapping)
    raw_retention_kind, raw_retention_hash = _raw_retention_identity(
        raw_retention_mapping,
        statistical_mapping=country_mapping,
        statistical_mapping_hash=mapping_hash,
    )
    if selection.get("country_code") != country_mapping.target_country:
        raise BoundedPilotWorkerError("selection 国家与冻结 mapping target 不一致")
    window = selection.get("window")
    roles = selection.get("roles")
    if not isinstance(window, Mapping) or not isinstance(roles, Mapping):
        raise BoundedPilotWorkerError("selection 缺少 window/roles")
    start = _utc(window.get("start_utc"), "selection.window.start_utc")
    end = _utc(window.get("end_exclusive_utc"), "selection.window.end_exclusive_utc")
    pilot_end = _utc(pilot_end_exclusive_utc, "pilot_end_exclusive_utc")
    if (
        window.get("interval_semantics") != "half_open"
        or window.get("granularity_seconds") != 300
        or start >= pilot_end
        or pilot_end > end
        or (pilot_end - start) % _FIVE_MINUTES
        or start.minute % 5
        or pilot_end.minute % 5
    ):
        raise BoundedPilotWorkerError(
            "pilot 必须位于 selection 内并对齐五分钟半开边界"
        )
    code_hash = _sha256(code_identity_sha256, "code_identity_sha256")
    seed = roles.get("state_seed_rib")
    if not isinstance(seed, Mapping):
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint 必须绑定 state_seed_rib"
        )
    normalized_attestation = validate_seed_spool_attestation(
        seed_spool_attestation,
        seed_artifact=seed,
    )

    restored = _read_checkpoint(
        Path(checkpoint_path),
        fingerprint_schema=FULL_SEED_CHECKPOINT_FINGERPRINT_SCHEMA,
    )
    if restored.get("schema_version") != FULL_SEED_CHECKPOINT_SCHEMA_VERSION:
        raise BoundedPilotWorkerError(
            "checkpoint 不是完整 seed checkpoint schema"
        )
    if set(restored) != _FULL_SEED_CHECKPOINT_FIELDS:
        raise BoundedPilotWorkerError("完整 seed checkpoint 顶层字段不闭合")
    required_bindings = {
        "code_identity_sha256": code_hash,
        "selection_id": selection_id,
        "selection_semantic_fingerprint_sha256": selection_hash,
        "mapping_fingerprint_sha256": mapping_hash,
        "raw_retention_mapping_kind": raw_retention_kind,
        "raw_retention_mapping_fingerprint_sha256": raw_retention_hash,
        "seed_spool_attestation_fingerprint_sha256": normalized_attestation[
            "semantic_fingerprint_sha256"
        ],
        "pilot_start_utc": _utc_text(start),
        "pilot_end_exclusive_utc": _utc_text(pilot_end),
    }
    if any(restored.get(key) != value for key, value in required_bindings.items()):
        if restored.get("code_identity_sha256") != code_hash:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint code_identity_sha256 不一致"
            )
        if (
            restored.get("raw_retention_mapping_kind") != raw_retention_kind
            or restored.get("raw_retention_mapping_fingerprint_sha256")
            != raw_retention_hash
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint raw-retention union 身份不一致"
            )
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint 与 selection/mapping/pilot 不绑定"
        )
    if restored.get("resume_policy") != "worker_full_seed_record_offset_v2":
        raise BoundedPilotWorkerError("完整 seed checkpoint resume_policy 非法")

    position = restored.get("position")
    progress = restored.get("seed_progress")
    resources = restored.get("resources")
    ambiguity = restored.get("ambiguity")
    policy = restored.get("checkpoint_policy")
    if not all(
        isinstance(value, Mapping)
        for value in (position, progress, resources, ambiguity, policy)
    ):
        raise BoundedPilotWorkerError("完整 seed checkpoint 结构不闭合")
    if set(position) != {
        "phase",
        "update_index",
        "next_record_ordinal",
        "boundary",
    }:
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint position 字段不闭合"
        )
    phase = position.get("phase")
    position_next = position.get("next_record_ordinal")
    if (
        phase not in {"seed_rib", "updates"}
        or position.get("update_index") != 0
        or isinstance(position_next, bool)
        or not isinstance(position_next, int)
        or position_next < 0
        or position.get("boundary") != "after_complete_physical_record"
        or (phase == "updates" and position_next != 0)
    ):
        raise BoundedPilotWorkerError("完整 seed checkpoint position 非法")
    if set(progress) != {
        "artifact_id",
        "file_sha256",
        "collector_id",
        "artifact_time_utc",
        "size_bytes",
        "next_record_ordinal",
        "next_record_offset",
        "seed_parse_complete",
        "previous_record_boundary",
        "peer_index_context",
    }:
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint seed_progress 字段不闭合"
        )
    for key in (
        "artifact_id",
        "file_sha256",
        "collector_id",
        "artifact_time_utc",
        "size_bytes",
    ):
        if progress.get(key) != seed.get(key):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint seed 制品绑定不一致"
            )
    progress_next = progress.get("next_record_ordinal")
    progress_offset = progress.get("next_record_offset")
    seed_parse_complete = progress.get("seed_parse_complete")
    spool = _validate_seed_spool_binding(
        restored.get("seed_spool"),
        attestation=normalized_attestation,
    )
    if (
        isinstance(progress_next, bool)
        or not isinstance(progress_next, int)
        or progress_next < 0
        or isinstance(progress_offset, bool)
        or not isinstance(progress_offset, int)
        or progress_offset < 0
        or progress_offset > spool["size_bytes"]
        or not isinstance(seed_parse_complete, bool)
    ):
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint seed_progress 非法"
        )
    previous_record_boundary = _validate_previous_record_boundary(
        progress.get("previous_record_boundary"),
        next_record_ordinal=progress_next,
        next_record_offset=progress_offset,
    )
    peer_index_context = _validate_peer_index_context(
        progress.get("peer_index_context"),
        next_record_offset=progress_offset,
    )
    if (
        (
            phase == "seed_rib"
            and (
                seed_parse_complete
                or position_next != progress_next
                or progress_offset >= spool["size_bytes"]
            )
        )
        or (
            phase == "updates"
            and (
                not seed_parse_complete
                or progress_offset != spool["size_bytes"]
            )
        )
    ):
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint seed_progress 非法"
        )

    try:
        state = route_replay_state_from_payload(restored["state"])
        baseline_payload = restored["seed_state_at_window_start"]
        baseline = (
            route_replay_state_from_payload(baseline_payload)
            if baseline_payload is not None
            else None
        )
        events = tuple(_event_from_payload(row) for row in restored["route_events"])
        audits = tuple(_raw_from_payload(row) for row in restored["raw_audits"])
    except (KeyError, TypeError, ValueError) as error:
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint state/event/raw 无法恢复"
        ) from error
    if (
        state.continuity_state != "continuous"
        or state.missing_reasons
        or restored.get("gaps") != []
        or restored.get("errors") != []
        or (phase == "seed_rib" and baseline is not None)
        or (phase == "updates" and baseline != state)
        or any(event.action != "rib_snapshot" for event in events)
        or len({event.route_event_id for event in events}) != len(events)
        or state.processed_route_event_ids
        != frozenset(event.route_event_id for event in events)
    ):
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint state/phase/RouteEvent 不闭合"
        )

    expected_seed_identity = (
        seed.get("artifact_id"),
        seed.get("file_sha256"),
        seed.get("collector_id"),
        seed.get("artifact_time_utc"),
    )
    audit_index: dict[Tuple[str, int], RawRecordEvidence] = {}
    prior_record = -1
    prior_end = -1
    for raw in audits:
        key = (raw.artifact_id, raw.record_ordinal)
        numeric = (
            raw.record_ordinal,
            raw.record_offset,
            raw.record_length,
            raw.event_epoch_microseconds,
            raw.mrt_type,
            raw.mrt_subtype,
        )
        if (
            key in audit_index
            or (
                raw.artifact_id,
                raw.file_sha256,
                raw.collector_id,
                raw.artifact_slot_utc,
            )
            != expected_seed_identity
            or raw.record_ordinal >= progress_next
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in numeric
            )
            or raw.record_length < 12
            or raw.record_ordinal <= prior_record
            or raw.record_offset < prior_end
            or raw.record_offset + raw.record_length > progress_offset
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint raw audit 越出已完成记录边界"
            )
        _sha256(
            raw.raw_record_sha256,
            "完整 seed checkpoint raw_audit.raw_record_sha256",
        )
        audit_index[key] = raw
        prior_record = raw.record_ordinal
        prior_end = raw.record_offset + raw.record_length
    if any(
        (
            event.artifact_id,
            event.file_sha256,
            event.collector_id,
            event.artifact_slot_utc,
        )
        != expected_seed_identity
        or event.record_ordinal >= progress_next
        or (event.artifact_id, event.record_ordinal) not in audit_index
        for event in events
    ):
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint RouteEvent 缺少 raw/seed 证据"
        )

    def sorted_strings(value: Any, name: str) -> set[str]:
        if (
            not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
            or value != sorted(set(value))
        ):
            raise BoundedPilotWorkerError(f"完整 seed checkpoint {name} 非法")
        return set(value)

    prefixes = sorted_strings(restored.get("tracked_prefixes"), "tracked_prefixes")
    vps = sorted_strings(restored.get("observed_vp_ids"), "observed_vp_ids")
    try:
        canonical_prefixes = all(
            ipaddress.ip_network(prefix, strict=True).compressed == prefix
            for prefix in prefixes
        )
    except ValueError:
        canonical_prefixes = False
    if (
        not canonical_prefixes
        or prefixes != {event.prefix for event in events}
        or {event.vp_id for event in events} - vps
    ):
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint prefix/VP 与 RouteEvent 不闭合"
        )

    if set(ambiguity) != {
        "element_count",
        "prefixes",
        "record_refs",
        "vp_ids",
        "mapped_target_relation_count",
    }:
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint ambiguity 字段不闭合"
        )
    ambiguous_events = []
    mapped_count = 0
    for event in events:
        _possible, is_ambiguous, is_mapped = _origin_relevance(
            event, country_mapping
        )
        mapped_count += int(is_mapped)
        if is_ambiguous:
            ambiguous_events.append(event)
    ambiguous_prefixes = sorted_strings(
        ambiguity.get("prefixes"), "ambiguity.prefixes"
    )
    ambiguous_vps = sorted_strings(ambiguity.get("vp_ids"), "ambiguity.vp_ids")
    ambiguous_refs = ambiguity.get("record_refs")
    if (
        ambiguity.get("element_count") != len(ambiguous_events)
        or ambiguity.get("mapped_target_relation_count") != mapped_count
        or ambiguous_prefixes != {event.prefix for event in ambiguous_events}
        or ambiguous_vps != {event.vp_id for event in ambiguous_events}
        or not isinstance(ambiguous_refs, list)
    ):
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint ambiguity 与 RouteEvent 不闭合"
        )
    seen_ambiguous_records = set()
    for row in ambiguous_refs:
        if not isinstance(row, Mapping):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint ambiguity raw ref 非法"
            )
        key = (row.get("artifact_id"), row.get("record_ordinal"))
        if (
            key in seen_ambiguous_records
            or key not in audit_index
            or dict(row) != _raw_record_ref(audit_index[key])
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint ambiguity raw ref 不闭合"
            )
        seen_ambiguous_records.add(key)

    expected_resource_fields = {
        "prior_new_raw_read_bytes",
        "prior_raw_accounting",
        "seed_raw_reservation",
        "new_raw_read_bytes",
        "peak_temporary_bytes",
        "database_writes",
        "cumulative_worker_runtime_seconds",
        "max_worker_elapsed_seconds",
    }
    if set(resources) != expected_resource_fields or resources.get("database_writes") != 0:
        raise BoundedPilotWorkerError("完整 seed checkpoint resources 非法")
    prior_raw_bytes = resources.get("prior_new_raw_read_bytes")
    raw_bytes = resources.get("new_raw_read_bytes")
    peak_temp = resources.get("peak_temporary_bytes")
    cumulative_runtime = resources.get("cumulative_worker_runtime_seconds")
    max_runtime = resources.get("max_worker_elapsed_seconds")
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (prior_raw_bytes, raw_bytes, peak_temp)
        )
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0
            for value in (cumulative_runtime, max_runtime)
        )
        or raw_bytes < prior_raw_bytes
        or float(max_runtime) > float(cumulative_runtime)
        or peak_temp
        < spool["size_bytes"]
        + _checkpoint_storage_bytes(Path(checkpoint_path))
    ):
        raise BoundedPilotWorkerError("完整 seed checkpoint resources 非法")
    prior_accounting_value = resources.get("prior_raw_accounting")
    if prior_accounting_value is None:
        raise BoundedPilotWorkerError(
            "full seed checkpoint 缺少 probe terminal accounting"
        )
    else:
        probe_base = prior_accounting_value.get(
            "cumulative_reserved_new_raw_bytes"
        )
        if isinstance(probe_base, bool) or not isinstance(probe_base, int):
            raise BoundedPilotWorkerError("checkpoint probe cumulative 非法")
        normalized_probe = _verified_probe_terminal_accounting(
            prior_accounting_value,
            expected_prior_raw_bytes=probe_base,
            selection_id=selection_id,
            selection_sha256=selection_hash,
            code_identity_sha256=code_identity_sha256,
        )
        reservation_value = resources.get("seed_raw_reservation")
        if reservation_value is None:
            if raw_bytes != prior_raw_bytes:
                raise BoundedPilotWorkerError(
                    "复用 seed spool 的 checkpoint 不得增加压缩 raw 字节"
                )
        else:
            normalized_reservation = _verified_seed_raw_reservation(
                reservation_value,
                probe_accounting=normalized_probe,
                expected_prior_raw_bytes=int(prior_raw_bytes),
                selection_id=selection_id,
                seed_artifact=seed,
                code_identity_sha256=code_identity_sha256,
            )
            if (
                normalized_reservation["cumulative_reserved_new_raw_bytes"]
                != raw_bytes
            ):
                raise BoundedPilotWorkerError(
                    "checkpoint raw cumulative 与 durable seed reservation 不闭合"
                )

    ledger = restored.get("seed_read_ledger")
    if not isinstance(ledger, list) or not ledger:
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint seed_read_ledger 非法"
        )
    prior_sequence = 0
    prior_completed = 0
    prior_completed_offset = 0
    ledger_bytes = 0
    for index, row in enumerate(ledger):
        if not isinstance(row, Mapping) or set(row) != {
            "checkpoint_sequence",
            "resume_from_record_ordinal",
            "resume_from_record_offset",
            "completed_through_record_ordinal_exclusive",
            "completed_through_record_offset",
            "new_compressed_raw_bytes_read",
            "seed_parse_complete",
        }:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint seed_read_ledger 字段不闭合"
            )
        sequence = row.get("checkpoint_sequence")
        resume_from = row.get("resume_from_record_ordinal")
        resume_offset = row.get("resume_from_record_offset")
        completed = row.get("completed_through_record_ordinal_exclusive")
        completed_offset = row.get("completed_through_record_offset")
        read_bytes = row.get("new_compressed_raw_bytes_read")
        complete_pass = row.get("seed_parse_complete")
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in (
                    sequence,
                    resume_from,
                    resume_offset,
                    completed,
                    completed_offset,
                    read_bytes,
                )
            )
            or sequence <= prior_sequence
            or resume_from != prior_completed
            or resume_offset != prior_completed_offset
            or completed < resume_from
            or completed_offset < resume_offset
            or completed_offset > spool["size_bytes"]
            or read_bytes > seed["size_bytes"]
            or not isinstance(complete_pass, bool)
            or (complete_pass and index != len(ledger) - 1)
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint seed_read_ledger 非法"
            )
        prior_sequence = sequence
        prior_completed = completed
        prior_completed_offset = completed_offset
        ledger_bytes += read_bytes
    expected_compressed_raw_bytes = (
        0 if resources.get("seed_raw_reservation") is None else seed["size_bytes"]
    )
    if (
        prior_completed != progress_next
        or prior_completed_offset != progress_offset
        or ledger_bytes != raw_bytes - prior_raw_bytes
        or ledger[-1]["seed_parse_complete"] != seed_parse_complete
        or raw_bytes - prior_raw_bytes != expected_compressed_raw_bytes
        or any(
            row["new_compressed_raw_bytes_read"]
            != (expected_compressed_raw_bytes if index == 0 else 0)
            for index, row in enumerate(ledger)
        )
    ):
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint seed_read_ledger 与资源/进度不闭合"
        )

    normalized_policy = _validate_full_seed_checkpoint_policy(policy)
    sequence = restored.get("checkpoint_sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
        raise BoundedPilotWorkerError(
            "完整 seed checkpoint checkpoint_sequence 非法"
        )

    return {
        "schema_version": "rrc25-bounded-pilot-worker-full-seed-verification/v2",
        "verified": True,
        "checkpoint_schema_version": FULL_SEED_CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_fingerprint_sha256": restored[
            "checkpoint_fingerprint_sha256"
        ],
        "checkpoint_sequence": sequence,
        "bindings": dict(required_bindings),
        "position": dict(position),
        "seed_progress": dict(progress),
        "seed_spool": dict(spool),
        "previous_record_boundary": dict(previous_record_boundary),
        "peer_index_context": (
            dict(peer_index_context) if peer_index_context is not None else None
        ),
        "resources": dict(resources),
        "seed_read_pass_count": len(ledger),
        "checkpoint_policy": normalized_policy,
        "seed_spool_reclamation_eligibility": {
            "eligible": phase == "updates" and seed_parse_complete,
            "requires_explicit_archive_or_reclamation_command": True,
            "explicit_command": "seed-retire-spool",
            "automatic_deletion": False,
            "current_command_handles_spool": False,
        },
    }


def run_bounded_pilot_worker(
    selection: Mapping[str, Any],
    *,
    artifact_root: os.PathLike[str] | str,
    country_mapping: CountryMappingView,
    raw_retention_mapping: Optional[RawRetentionMappingUnion] = None,
    seed_spool_attestation: Optional[Mapping[str, Any]] = None,
    seed_rib_prefilter: Optional[Mapping[str, Any]] = None,
    pilot_end_exclusive_utc: str,
    update_record_stream_factory: Callable[[Mapping[str, Any]], Iterable[Any]],
    checkpoint_directory: os.PathLike[str] | str,
    resume_checkpoint_path: Optional[os.PathLike[str] | str] = None,
    seed_sample_checkpoint_path: Optional[os.PathLike[str] | str] = None,
    code_identity_sha256: Optional[str] = None,
    planned_seed_checkpoint_seconds: float = (
        _DEFAULT_PLANNED_SEED_CHECKPOINT_SECONDS
    ),
    prior_new_raw_read_bytes: int = 0,
    prior_raw_accounting: Optional[Mapping[str, Any]] = None,
    seed_raw_reservation: Optional[Mapping[str, Any]] = None,
    reuse_existing_seed_spool: bool = False,
    seed_batch_max_route_events: int = _DEFAULT_SEED_BATCH_MAX_ROUTE_EVENTS,
    seed_batch_max_records: int = _DEFAULT_SEED_BATCH_MAX_RECORDS,
    stop_after_seed: bool = False,
    analysis_update_artifact_ids: Optional[Sequence[str]] = None,
    resource_limits: Optional[ResourceLimits] = None,
    clock: Callable[[], float] = time.monotonic,
    process_started_at: Optional[float] = None,
) -> BoundedPilotWorkerResult:
    """执行一个不超过冻结 pilot 终点的解析/回放分块。

    运行时门禁按本次 worker/CLI 进程的同一 wall-clock 起点累计，不能在
    chunk 或 artifact 边界重置。coordinator 与 worker 同进程时应传入
    ``process_started_at``，从而把 worker 启动前的输入核验也计入 540/600
    秒边界；独立调用时则从进入本函数开始计时。``prior_new_raw_read_bytes``
    是同一研究任务在本次 seed 前已发生的读取累计，只进入资源门与 resources，
    不进入仅描述本次压缩 seed pass 的 ``seed_read_ledger``。
    """

    if not callable(update_record_stream_factory) or not callable(clock):
        raise BoundedPilotWorkerError("stream factory/clock 必须可调用")
    if (
        isinstance(prior_new_raw_read_bytes, bool)
        or not isinstance(prior_new_raw_read_bytes, int)
        or prior_new_raw_read_bytes < 0
    ):
        raise BoundedPilotWorkerError(
            "prior_new_raw_read_bytes 必须是非负整数"
        )
    observed_start = clock()
    if isinstance(observed_start, bool) or not isinstance(
        observed_start, (int, float)
    ):
        raise BoundedPilotWorkerError("clock 必须返回有限数")
    observed_start = float(observed_start)
    if not math.isfinite(observed_start):
        raise BoundedPilotWorkerError("clock 必须返回有限数")
    if process_started_at is None:
        run_started = observed_start
    else:
        if isinstance(process_started_at, bool) or not isinstance(
            process_started_at, (int, float)
        ):
            raise BoundedPilotWorkerError("process_started_at 必须是有限数")
        run_started = float(process_started_at)
        if not math.isfinite(run_started) or run_started > observed_start:
            raise BoundedPilotWorkerError(
                "process_started_at 必须是不晚于当前 clock 的有限数"
            )

    selection_id, selection_hash = _selection_identity(selection)
    if prior_raw_accounting is None:
        if stop_after_seed:
            raise BoundedPilotWorkerError(
                "stop_after_seed 必须提供已核验 probe terminal accounting，"
                "不得只传 prior_new_raw_read_bytes"
            )
        normalized_prior_raw_accounting = None
    else:
        probe_base = prior_raw_accounting.get(
            "cumulative_reserved_new_raw_bytes"
        )
        if isinstance(probe_base, bool) or not isinstance(probe_base, int):
            raise BoundedPilotWorkerError(
                "probe terminal accounting cumulative 非法"
            )
        normalized_prior_raw_accounting = _verified_probe_terminal_accounting(
            prior_raw_accounting,
            expected_prior_raw_bytes=probe_base,
            selection_id=selection_id,
            selection_sha256=selection_hash,
            code_identity_sha256=code_identity_sha256,
        )
    normalized_seed_raw_reservation: Optional[Mapping[str, Any]] = None
    mapping_hash = _mapping_identity(country_mapping)
    raw_retention_kind, raw_retention_hash = _raw_retention_identity(
        raw_retention_mapping,
        statistical_mapping=country_mapping,
        statistical_mapping_hash=mapping_hash,
    )
    raw_retention_membership = (
        country_mapping.target_membership
        if raw_retention_mapping is None
        else raw_retention_mapping.raw_retention_membership
    )
    if selection.get("country_code") != country_mapping.target_country:
        raise BoundedPilotWorkerError("selection 国家与冻结 mapping target 不一致")
    window = selection.get("window")
    roles = selection.get("roles")
    if not isinstance(window, Mapping) or not isinstance(roles, Mapping):
        raise BoundedPilotWorkerError("selection 缺少 window/roles")
    start = _utc(window.get("start_utc"), "selection.window.start_utc")
    end = _utc(window.get("end_exclusive_utc"), "selection.window.end_exclusive_utc")
    pilot_end = _utc(pilot_end_exclusive_utc, "pilot_end_exclusive_utc")
    if (
        window.get("interval_semantics") != "half_open"
        or window.get("granularity_seconds") != 300
        or start >= pilot_end
        or pilot_end > end
        or (pilot_end - start) % _FIVE_MINUTES
        or start.minute % 5
        or pilot_end.minute % 5
    ):
        raise BoundedPilotWorkerError("pilot 必须位于 selection 内并对齐五分钟半开边界")
    raw_root = Path(artifact_root)
    checkpoint_root = Path(checkpoint_directory)
    _assert_checkpoint_directory(checkpoint_root)
    limits = resource_limits or ResourceLimits()
    if not isinstance(limits, ResourceLimits):
        raise BoundedPilotWorkerError("resource_limits 必须是 ResourceLimits")
    if not isinstance(stop_after_seed, bool):
        raise BoundedPilotWorkerError("stop_after_seed 必须是布尔值")
    if not isinstance(reuse_existing_seed_spool, bool):
        raise BoundedPilotWorkerError(
            "reuse_existing_seed_spool 必须是布尔值"
        )
    if reuse_existing_seed_spool and resume_checkpoint_path is not None:
        raise BoundedPilotWorkerError(
            "reuse_existing_seed_spool 只适用于 seed-start，不适用于 resume"
        )
    for value, label, maximum in (
        (
            seed_batch_max_route_events,
            "seed_batch_max_route_events",
            _MAX_SEED_BATCH_MAX_ROUTE_EVENTS,
        ),
        (
            seed_batch_max_records,
            "seed_batch_max_records",
            _MAX_SEED_BATCH_MAX_RECORDS,
        ),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            or value > maximum
        ):
            raise BoundedPilotWorkerError(
                f"{label} 必须位于 [1,{maximum}]"
            )
    if isinstance(planned_seed_checkpoint_seconds, bool) or not isinstance(
        planned_seed_checkpoint_seconds, (int, float)
    ):
        raise BoundedPilotWorkerError(
            "planned_seed_checkpoint_seconds 必须是有限正数"
        )
    planned_seed_checkpoint_seconds = float(planned_seed_checkpoint_seconds)
    if (
        not math.isfinite(planned_seed_checkpoint_seconds)
        or planned_seed_checkpoint_seconds <= 0
    ):
        raise BoundedPilotWorkerError(
            "planned_seed_checkpoint_seconds 必须是有限正数"
        )
    full_seed_checkpoint_enabled = (
        code_identity_sha256 is not None
        or resume_checkpoint_path is not None
        or stop_after_seed
    )
    if full_seed_checkpoint_enabled:
        if planned_seed_checkpoint_seconds >= limits.worker_soft_stop_seconds:
            raise BoundedPilotWorkerError(
                "planned_seed_checkpoint_seconds 必须严格小于 worker 软停"
            )
        if code_identity_sha256 is None:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint 必须显式提供 code_identity_sha256；"
                "旧 worker 软停文件仍由 coordinator/replay_persistence 负责"
            )
        code_identity_sha256 = _sha256(
            code_identity_sha256, "code_identity_sha256"
        )
    if resume_checkpoint_path is not None and seed_sample_checkpoint_path is not None:
        raise BoundedPilotWorkerError(
            "resume_checkpoint_path 与 seed_sample_checkpoint_path 互斥"
        )

    updates_raw = roles.get("analysis_updates")
    if not isinstance(updates_raw, list) or any(
        not isinstance(row, Mapping) for row in updates_raw
    ):
        raise BoundedPilotWorkerError("selection.analysis_updates 必须是对象数组")
    available_by_slot: dict[str, Mapping[str, Any]] = {}
    available_by_id: dict[str, Mapping[str, Any]] = {}
    for artifact in updates_raw:
        slot = _utc(artifact.get("artifact_time_utc"), "update artifact time")
        if start <= slot < pilot_end:
            key = _utc_text(slot)
            artifact_id = artifact.get("artifact_id")
            if not isinstance(artifact_id, str) or not artifact_id:
                raise BoundedPilotWorkerError("pilot UPDATE artifact_id 非法")
            if key in available_by_slot or artifact_id in available_by_id:
                raise BoundedPilotWorkerError("pilot UPDATE 槽重复")
            available_by_slot[key] = artifact
            available_by_id[artifact_id] = artifact
    if analysis_update_artifact_ids is None:
        execution_ids = set(available_by_id)
    else:
        if isinstance(analysis_update_artifact_ids, (str, bytes)) or not isinstance(
            analysis_update_artifact_ids, Sequence
        ):
            raise BoundedPilotWorkerError(
                "analysis_update_artifact_ids 必须是字符串序列"
            )
        requested = tuple(analysis_update_artifact_ids)
        if any(not isinstance(value, str) or not value for value in requested):
            raise BoundedPilotWorkerError(
                "analysis_update_artifact_ids 必须是非空字符串"
            )
        if len(set(requested)) != len(requested):
            raise BoundedPilotWorkerError(
                "analysis_update_artifact_ids 不得重复"
            )
        unknown = sorted(set(requested) - set(available_by_id))
        if unknown:
            raise BoundedPilotWorkerError(
                "analysis_update_artifact_ids 不属于当前 pilot selection"
            )
        execution_ids = set(requested)
    by_slot = {
        slot: artifact
        for slot, artifact in available_by_slot.items()
        if artifact["artifact_id"] in execution_ids
    }
    intentionally_unprocessed_slots = set(available_by_slot) - set(by_slot)
    slot_times = _expected_slots(start, pilot_end)

    seed = roles.get("state_seed_rib")
    if full_seed_checkpoint_enabled:
        if not isinstance(seed, Mapping):
            raise BoundedPilotWorkerError(
                "full seed 必须提供 state_seed_rib"
            )
        if normalized_prior_raw_accounting is None:
            raise BoundedPilotWorkerError("seed reservation 缺少 probe terminal 锚点")
        if seed_raw_reservation is None:
            if (
                not reuse_existing_seed_spool
                and resume_checkpoint_path is None
            ):
                raise BoundedPilotWorkerError(
                    "full seed 首次构建必须提供 pre-open durable seed raw "
                    "reservation；只有显式复用已核验 spool 时可省略"
                )
        else:
            if reuse_existing_seed_spool:
                raise BoundedPilotWorkerError(
                    "复用既有 seed spool 不得发布压缩 raw reservation"
                )
            normalized_seed_raw_reservation = _verified_seed_raw_reservation(
                seed_raw_reservation,
                probe_accounting=normalized_prior_raw_accounting,
                expected_prior_raw_bytes=prior_new_raw_read_bytes,
                selection_id=selection_id,
                seed_artifact=seed,
                code_identity_sha256=code_identity_sha256,
            )
    elif seed_raw_reservation is not None:
        if not isinstance(seed, Mapping) or normalized_prior_raw_accounting is None:
            raise BoundedPilotWorkerError("seed reservation 不得脱离 seed/probe 身份")
        normalized_seed_raw_reservation = _verified_seed_raw_reservation(
            seed_raw_reservation,
            probe_accounting=normalized_prior_raw_accounting,
            expected_prior_raw_bytes=prior_new_raw_read_bytes,
            selection_id=selection_id,
            seed_artifact=seed,
            code_identity_sha256=code_identity_sha256,
        )
    if seed is not None and not isinstance(seed, Mapping):
        raise BoundedPilotWorkerError("state_seed_rib 必须是对象或 null")
    normalized_seed_spool_attestation: Optional[Mapping[str, Any]] = None
    prefilter_materialize_rib_ordinals: Optional[FrozenSet[int]] = None
    if full_seed_checkpoint_enabled:
        if seed is None or seed_spool_attestation is None:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint 必须提供 state_seed_rib 与 seed spool attestation"
            )
        normalized_seed_spool_attestation = validate_seed_spool_attestation(
            seed_spool_attestation,
            seed_artifact=seed,
        )
        if seed_rib_prefilter is not None:
            try:
                prefilter_materialize_rib_ordinals = validate_rib_prefilter(
                    seed_rib_prefilter,
                    expected_spool_sha256=(
                        normalized_seed_spool_attestation["decompressed"][
                            "sha256"
                        ]
                    ),
                    expected_spool_size_bytes=(
                        normalized_seed_spool_attestation["decompressed"][
                            "size_bytes"
                        ]
                    ),
                    seed_artifact_id=seed["artifact_id"],
                    seed_file_sha256=seed["file_sha256"],
                    artifact_slot_utc=seed["artifact_time_utc"],
                    raw_retention_mapping=(
                        country_mapping
                        if raw_retention_mapping is None
                        else raw_retention_mapping
                    ),
                )
            except RibPrefilterError as error:
                raise BoundedPilotWorkerError(
                    "seed RIB prefilter sidecar 验证失败"
                ) from error
    elif seed_rib_prefilter is not None:
        raise BoundedPilotWorkerError(
            "seed RIB prefilter 只能用于完整 seed checkpoint 模式"
        )

    # 可恢复上下文。只保存 IR 研究子集和 raw 元数据，不保存全量解析对象。
    phase = "seed_rib"
    update_index = 0
    next_record_ordinal = 0
    state: Optional[RouteReplayState] = None
    baseline_state: Optional[RouteReplayState] = None
    snapshots: list[ReplaySnapshot] = []
    route_events: list[ResearchRouteEvent] = []
    raw_audits: list[RawRecordEvidence] = []
    slot_counts: list[SlotCount] = []
    tracked_prefixes: set[str] = set()
    pending_seed_events: list[ResearchRouteEvent] = []
    pending_seed_record_count = 0
    seed_batch_flush_failed = False
    pending_update_events: list[ResearchRouteEvent] = []
    pre_discovery: list[Mapping[str, Any]] = []
    ambiguous_prefixes: set[str] = set()
    ambiguous_records: dict[Tuple[str, int], Mapping[str, Any]] = {}
    ambiguous_vps: set[str] = set()
    ambiguous_element_count = 0
    mapped_target_relation_count = 0
    observed_vps: set[str] = set()
    gaps: list[InputGap] = []
    errors: list[Mapping[str, Any]] = []
    raw_bytes = prior_new_raw_read_bytes
    peak_temp = 0
    checkpoint_sequence = 1
    restored_cumulative_worker_runtime = 0.0
    restored_max_worker_elapsed = 0.0
    seed_sample_next_record_ordinal: Optional[int] = None
    seed_progress_next_record_ordinal = 0
    seed_progress_next_record_offset = 0
    seed_spool_binding: Optional[Mapping[str, Any]] = None
    deferred_seed_evidence = _DeferredSeedEvidence()
    seed_read_ledger: list[Mapping[str, Any]] = []

    if resume_checkpoint_path is not None:
        restored = _read_checkpoint(
            Path(resume_checkpoint_path),
            fingerprint_schema=FULL_SEED_CHECKPOINT_FINGERPRINT_SCHEMA,
        )
        required_bindings = {
            "code_identity_sha256": code_identity_sha256,
            "selection_id": selection_id,
            "selection_semantic_fingerprint_sha256": selection_hash,
            "mapping_fingerprint_sha256": mapping_hash,
            "raw_retention_mapping_kind": raw_retention_kind,
            "raw_retention_mapping_fingerprint_sha256": raw_retention_hash,
            "seed_spool_attestation_fingerprint_sha256": (
                normalized_seed_spool_attestation[
                    "semantic_fingerprint_sha256"
                ]
            ),
            "pilot_start_utc": _utc_text(start),
            "pilot_end_exclusive_utc": _utc_text(pilot_end),
        }
        if any(restored.get(key) != value for key, value in required_bindings.items()):
            if restored.get("code_identity_sha256") != code_identity_sha256:
                raise BoundedPilotWorkerError(
                    "完整 seed checkpoint code_identity_sha256 不一致"
                )
            if (
                restored.get("raw_retention_mapping_kind")
                != raw_retention_kind
                or restored.get("raw_retention_mapping_fingerprint_sha256")
                != raw_retention_hash
            ):
                raise BoundedPilotWorkerError(
                    "完整 seed checkpoint raw-retention union 身份不一致"
                )
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint 与 selection/mapping/pilot 不绑定"
            )
        if restored.get("schema_version") != FULL_SEED_CHECKPOINT_SCHEMA_VERSION:
            raise BoundedPilotWorkerError("resume_checkpoint_path 不是完整 seed checkpoint")
        if set(restored) != _FULL_SEED_CHECKPOINT_FIELDS:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint 顶层字段不闭合"
            )
        if restored.get("resume_policy") != "worker_full_seed_record_offset_v2":
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint resume_policy 非法"
            )
        position = restored.get("position")
        seed_progress = restored.get("seed_progress")
        resources = restored.get("resources")
        ambiguity = restored.get("ambiguity")
        checkpoint_policy = restored.get("checkpoint_policy")
        if not all(
            isinstance(value, Mapping)
            for value in (
                position,
                seed_progress,
                resources,
                ambiguity,
                checkpoint_policy,
            )
        ):
            raise BoundedPilotWorkerError("完整 seed checkpoint 结构不闭合")
        if set(position) != {
            "phase",
            "update_index",
            "next_record_ordinal",
            "boundary",
        }:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint position 字段不闭合"
            )
        phase = position.get("phase")
        update_index = position.get("update_index")
        next_record_ordinal = position.get("next_record_ordinal")
        if (
            phase not in {"seed_rib", "updates"}
            or update_index != 0
            or isinstance(next_record_ordinal, bool)
            or not isinstance(next_record_ordinal, int)
            or next_record_ordinal < 0
            or position.get("boundary") != "after_complete_physical_record"
            or (phase == "updates" and next_record_ordinal != 0)
        ):
            raise BoundedPilotWorkerError("完整 seed checkpoint position 非法")
        if set(seed_progress) != {
            "artifact_id",
            "file_sha256",
            "collector_id",
            "artifact_time_utc",
            "size_bytes",
            "next_record_ordinal",
            "next_record_offset",
            "seed_parse_complete",
            "previous_record_boundary",
            "peer_index_context",
        }:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint seed_progress 字段不闭合"
            )
        if seed is None:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint 与缺失的 state_seed_rib 不闭合"
            )
        expected_seed_progress = {
            "artifact_id": seed.get("artifact_id"),
            "file_sha256": seed.get("file_sha256"),
            "collector_id": seed.get("collector_id"),
            "artifact_time_utc": seed.get("artifact_time_utc"),
            "size_bytes": seed.get("size_bytes"),
        }
        if any(
            seed_progress.get(key) != value
            for key, value in expected_seed_progress.items()
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint seed 制品绑定不一致"
            )
        seed_progress_next_record_ordinal = seed_progress.get(
            "next_record_ordinal"
        )
        seed_progress_next_record_offset = seed_progress.get(
            "next_record_offset"
        )
        seed_complete = seed_progress.get("seed_parse_complete")
        seed_spool_binding = _validate_seed_spool_binding(
            restored.get("seed_spool"),
            attestation=normalized_seed_spool_attestation,
        )
        if (
            isinstance(seed_progress_next_record_ordinal, bool)
            or not isinstance(seed_progress_next_record_ordinal, int)
            or seed_progress_next_record_ordinal < 0
            or isinstance(seed_progress_next_record_offset, bool)
            or not isinstance(seed_progress_next_record_offset, int)
            or seed_progress_next_record_offset < 0
            or seed_progress_next_record_offset > seed_spool_binding["size_bytes"]
            or not isinstance(seed_complete, bool)
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint seed_progress 非法"
            )
        seed_previous_record_boundary = _validate_previous_record_boundary(
            seed_progress.get("previous_record_boundary"),
            next_record_ordinal=seed_progress_next_record_ordinal,
            next_record_offset=seed_progress_next_record_offset,
        )
        seed_peer_index_context = _validate_peer_index_context(
            seed_progress.get("peer_index_context"),
            next_record_offset=seed_progress_next_record_offset,
        )
        deferred_seed_evidence.restore(
            seed_previous_record_boundary, seed_peer_index_context
        )
        if (
            (phase == "seed_rib" and seed_complete)
            or (phase == "seed_rib" and next_record_ordinal != seed_progress_next_record_ordinal)
            or (
                phase == "seed_rib"
                and seed_progress_next_record_offset
                >= seed_spool_binding["size_bytes"]
            )
            or (
                phase == "updates"
                and (
                    not seed_complete
                    or seed_progress_next_record_offset
                    != seed_spool_binding["size_bytes"]
                )
            )
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint seed_progress 非法"
            )
        _validate_full_seed_checkpoint_policy(checkpoint_policy)

        try:
            state = route_replay_state_from_payload(restored["state"])
            baseline_payload = restored["seed_state_at_window_start"]
            baseline_state = (
                route_replay_state_from_payload(baseline_payload)
                if baseline_payload is not None
                else None
            )
            route_events = [
                _event_from_payload(row) for row in restored["route_events"]
            ]
            raw_audits = [
                _raw_from_payload(row) for row in restored["raw_audits"]
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint state/event/raw 无法恢复"
            ) from error
        if (
            state.continuity_state != "continuous"
            or state.missing_reasons
            or (phase == "seed_rib" and baseline_state is not None)
            or (phase == "updates" and baseline_state != state)
            or restored.get("gaps") != []
            or restored.get("errors") != []
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint 不得携带 gap/error 或不闭合 baseline"
            )
        gaps = []
        errors = []
        if (
            any(event.action != "rib_snapshot" for event in route_events)
            or len({event.route_event_id for event in route_events})
            != len(route_events)
            or state.processed_route_event_ids
            != frozenset(event.route_event_id for event in route_events)
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint RouteEvent 与 state 不闭合"
            )

        def restored_sorted_strings(name: str) -> set[str]:
            value = restored.get(name)
            if (
                not isinstance(value, list)
                or any(not isinstance(item, str) or not item for item in value)
                or value != sorted(set(value))
            ):
                raise BoundedPilotWorkerError(
                    f"完整 seed checkpoint {name} 非法"
                )
            return set(value)

        tracked_prefixes = restored_sorted_strings("tracked_prefixes")
        observed_vps = restored_sorted_strings("observed_vp_ids")
        try:
            prefixes_are_canonical = all(
                ipaddress.ip_network(prefix, strict=True).compressed == prefix
                for prefix in tracked_prefixes
            )
        except ValueError:
            prefixes_are_canonical = False
        if (
            not prefixes_are_canonical
            or tracked_prefixes != {event.prefix for event in route_events}
            or {event.vp_id for event in route_events} - observed_vps
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint prefix/VP 与 RouteEvent 不闭合"
            )

        if set(ambiguity) != {
            "element_count",
            "prefixes",
            "record_refs",
            "vp_ids",
            "mapped_target_relation_count",
        }:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint ambiguity 字段不闭合"
            )
        ambiguous_element_count = ambiguity.get("element_count")
        mapped_target_relation_count = ambiguity.get(
            "mapped_target_relation_count"
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (
                ambiguous_element_count,
                mapped_target_relation_count,
            )
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint ambiguity 计数非法"
            )
        ambiguous_prefix_values = ambiguity.get("prefixes")
        ambiguous_record_values = ambiguity.get("record_refs")
        ambiguous_vp_values = ambiguity.get("vp_ids")
        if not all(
            isinstance(value, list)
            for value in (
                ambiguous_prefix_values,
                ambiguous_record_values,
                ambiguous_vp_values,
            )
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint ambiguity 人口非法"
            )
        ambiguous_prefixes = set(ambiguous_prefix_values)
        ambiguous_vps = set(ambiguous_vp_values)
        if (
            ambiguous_prefix_values != sorted(ambiguous_prefixes)
            or ambiguous_vp_values != sorted(ambiguous_vps)
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint ambiguity 人口必须去重排序"
            )
        ambiguous_records = {}
        for row in ambiguous_record_values:
            if not isinstance(row, Mapping):
                raise BoundedPilotWorkerError(
                    "完整 seed checkpoint ambiguity raw ref 非法"
                )
            key = (row.get("artifact_id"), row.get("record_ordinal"))
            if key in ambiguous_records:
                raise BoundedPilotWorkerError(
                    "完整 seed checkpoint ambiguity raw ref 重复"
                )
            ambiguous_records[key] = dict(row)
        computed_ambiguous_events = []
        computed_mapped_target_relation_count = 0
        for event in route_events:
            _possible, is_ambiguous, is_mapped_target = _origin_relevance(
                event, country_mapping
            )
            computed_mapped_target_relation_count += int(is_mapped_target)
            if is_ambiguous:
                computed_ambiguous_events.append(event)
        if (
            ambiguous_element_count != len(computed_ambiguous_events)
            or mapped_target_relation_count
            != computed_mapped_target_relation_count
            or ambiguous_prefixes
            != {event.prefix for event in computed_ambiguous_events}
            or ambiguous_vps != {event.vp_id for event in computed_ambiguous_events}
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint ambiguity 与 RouteEvent 不闭合"
            )

        def nonnegative_resource(name: str, *, integer: bool) -> int | float:
            value = resources.get(name)
            expected_types = (int,) if integer else (int, float)
            if isinstance(value, bool) or not isinstance(value, expected_types):
                raise BoundedPilotWorkerError(
                    f"完整 seed checkpoint resources.{name} 非法"
                )
            normalized = int(value) if integer else float(value)
            if normalized < 0 or (not integer and not math.isfinite(normalized)):
                raise BoundedPilotWorkerError(
                    f"完整 seed checkpoint resources.{name} 非法"
                )
            return normalized

        restored_prior_new_raw_read_bytes = int(
            nonnegative_resource("prior_new_raw_read_bytes", integer=True)
        )
        raw_bytes = int(nonnegative_resource("new_raw_read_bytes", integer=True))
        peak_temp = int(nonnegative_resource("peak_temporary_bytes", integer=True))
        restored_cumulative_worker_runtime = float(
            nonnegative_resource(
                "cumulative_worker_runtime_seconds", integer=False
            )
        )
        restored_max_worker_elapsed = float(
            nonnegative_resource("max_worker_elapsed_seconds", integer=False)
        )
        if (
            set(resources)
            != {
                "prior_new_raw_read_bytes",
                "prior_raw_accounting",
                "seed_raw_reservation",
                "new_raw_read_bytes",
                "peak_temporary_bytes",
                "database_writes",
                "cumulative_worker_runtime_seconds",
                "max_worker_elapsed_seconds",
            }
            or resources.get("database_writes") != 0
            or restored_prior_new_raw_read_bytes != prior_new_raw_read_bytes
            or resources.get("prior_raw_accounting")
            != normalized_prior_raw_accounting
            or resources.get("seed_raw_reservation")
            != normalized_seed_raw_reservation
            or (
                normalized_seed_raw_reservation is not None
                and normalized_seed_raw_reservation[
                    "cumulative_reserved_new_raw_bytes"
                ]
                != raw_bytes
            )
            or raw_bytes < restored_prior_new_raw_read_bytes
            or restored_max_worker_elapsed > restored_cumulative_worker_runtime
            or peak_temp
            < seed_spool_binding["size_bytes"]
            + _checkpoint_storage_bytes(Path(resume_checkpoint_path))
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint resources 非法"
            )
        sequence = restored.get("checkpoint_sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint checkpoint_sequence 非法"
            )
        checkpoint_sequence = sequence + 1

        ledger = restored.get("seed_read_ledger")
        if not isinstance(ledger, list) or not ledger:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint seed_read_ledger 非法"
            )
        normalized_ledger = []
        prior_sequence = 0
        prior_completed = 0
        prior_completed_offset = 0
        for index, row in enumerate(ledger):
            if not isinstance(row, Mapping) or set(row) != {
                "checkpoint_sequence",
                "resume_from_record_ordinal",
                "resume_from_record_offset",
                "completed_through_record_ordinal_exclusive",
                "completed_through_record_offset",
                "new_compressed_raw_bytes_read",
                "seed_parse_complete",
            }:
                raise BoundedPilotWorkerError(
                    "完整 seed checkpoint seed_read_ledger 字段不闭合"
                )
            row_sequence = row.get("checkpoint_sequence")
            resume_from = row.get("resume_from_record_ordinal")
            resume_offset = row.get("resume_from_record_offset")
            completed_through = row.get(
                "completed_through_record_ordinal_exclusive"
            )
            completed_offset = row.get("completed_through_record_offset")
            compressed_bytes = row.get("new_compressed_raw_bytes_read")
            parse_complete = row.get("seed_parse_complete")
            if (
                any(
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                    for value in (
                        row_sequence,
                        resume_from,
                        resume_offset,
                        completed_through,
                        completed_offset,
                        compressed_bytes,
                    )
                )
                or row_sequence <= prior_sequence
                or resume_from != prior_completed
                or resume_offset != prior_completed_offset
                or completed_through < resume_from
                or completed_offset < resume_offset
                or completed_offset > seed_spool_binding["size_bytes"]
                or compressed_bytes > seed["size_bytes"]
                or not isinstance(parse_complete, bool)
                or (parse_complete and index != len(ledger) - 1)
            ):
                raise BoundedPilotWorkerError(
                    "完整 seed checkpoint seed_read_ledger 非法"
                )
            prior_sequence = row_sequence
            prior_completed = completed_through
            prior_completed_offset = completed_offset
            normalized_ledger.append(dict(row))
        expected_compressed_raw_bytes = (
            0
            if normalized_seed_raw_reservation is None
            else seed["size_bytes"]
        )
        if (
            sum(
                row["new_compressed_raw_bytes_read"]
                for row in normalized_ledger
            )
            != raw_bytes - restored_prior_new_raw_read_bytes
            or prior_completed != seed_progress_next_record_ordinal
            or prior_completed_offset != seed_progress_next_record_offset
            or normalized_ledger[-1]["seed_parse_complete"] != seed_complete
            or raw_bytes - restored_prior_new_raw_read_bytes
            != expected_compressed_raw_bytes
            or any(
                row["new_compressed_raw_bytes_read"]
                != (expected_compressed_raw_bytes if index == 0 else 0)
                for index, row in enumerate(normalized_ledger)
            )
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint seed_read_ledger 与资源/进度不闭合"
            )
        seed_read_ledger = normalized_ledger

        raw_index_for_validation: dict[Tuple[str, int], RawRecordEvidence] = {}
        prior_record_ordinal = -1
        prior_record_end = -1
        expected_seed_identity = (
            seed.get("artifact_id"),
            seed.get("file_sha256"),
            seed.get("collector_id"),
            seed.get("artifact_time_utc"),
        )
        for raw in raw_audits:
            key = (raw.artifact_id, raw.record_ordinal)
            if (
                key in raw_index_for_validation
                or (
                    raw.artifact_id,
                    raw.file_sha256,
                    raw.collector_id,
                    raw.artifact_slot_utc,
                )
                != expected_seed_identity
                or raw.record_ordinal >= seed_progress_next_record_ordinal
                or raw.record_ordinal <= prior_record_ordinal
                or raw.record_offset < prior_record_end
                or raw.record_offset + raw.record_length
                > seed_progress_next_record_offset
            ):
                raise BoundedPilotWorkerError(
                    "完整 seed checkpoint raw audit 越出已完成记录边界"
                )
            _sha256(
                raw.raw_record_sha256,
                "完整 seed checkpoint raw_audit.raw_record_sha256",
            )
            raw_index_for_validation[key] = raw
            prior_record_ordinal = raw.record_ordinal
            prior_record_end = raw.record_offset + raw.record_length
        if any(
            (
                event.artifact_id,
                event.file_sha256,
                event.collector_id,
                event.artifact_slot_utc,
            )
            != expected_seed_identity
            or event.record_ordinal >= seed_progress_next_record_ordinal
            or (event.artifact_id, event.record_ordinal)
            not in raw_index_for_validation
            for event in route_events
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint RouteEvent 缺少 raw/seed 证据"
            )
        if any(
            key not in raw_index_for_validation
            or row != _raw_record_ref(raw_index_for_validation[key])
            for key, row in ambiguous_records.items()
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint ambiguity raw ref 不闭合"
            )
        next_record_ordinal = (
            seed_progress_next_record_ordinal if phase == "seed_rib" else 0
        )

    if seed_sample_checkpoint_path is not None:
        restored = _read_checkpoint(Path(seed_sample_checkpoint_path))
        required_bindings = {
            "selection_id": selection_id,
            "selection_semantic_fingerprint_sha256": selection_hash,
            "mapping_fingerprint_sha256": mapping_hash,
            "pilot_start_utc": _utc_text(start),
            "pilot_end_exclusive_utc": _utc_text(pilot_end),
        }
        if any(restored.get(key) != value for key, value in required_bindings.items()):
            raise BoundedPilotWorkerError(
                "seed sample checkpoint 与 selection/mapping/pilot 不绑定"
            )
        if restored.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise BoundedPilotWorkerError("seed sample checkpoint schema 非法")
        expected_checkpoint_fields = {
            "schema_version",
            "checkpoint_fingerprint_sha256",
            "selection_id",
            "selection_semantic_fingerprint_sha256",
            "mapping_fingerprint_sha256",
            "pilot_start_utc",
            "pilot_end_exclusive_utc",
            "checkpoint_sequence",
            "position",
            "state",
            "seed_state_at_window_start",
            "resume_policy",
            "snapshot_refs",
            "route_event_refs",
            "raw_audits",
            "slot_counts",
            "tracked_prefixes",
            "pending_update_events",
            "pre_discovery_context_unknown",
            "ambiguity",
            "observed_vp_ids",
            "gaps",
            "errors",
            "resources",
        }
        if set(restored) != expected_checkpoint_fields:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint 顶层字段不闭合"
            )
        if restored.get("resume_policy") != "coordinator_replay_persistence_only":
            raise BoundedPilotWorkerError(
                "seed sample checkpoint resume_policy 非法"
            )
        position = restored.get("position")
        resources = restored.get("resources")
        ambiguity = restored.get("ambiguity")
        if not all(
            isinstance(value, Mapping) for value in (position, resources, ambiguity)
        ):
            raise BoundedPilotWorkerError("seed sample checkpoint 结构不闭合")
        if set(position) != {
            "phase",
            "update_index",
            "next_record_ordinal",
            "boundary",
        }:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint position 字段不闭合"
            )
        phase = position.get("phase")
        update_index = position.get("update_index")
        next_record_ordinal = position.get("next_record_ordinal")
        if (
            phase != "seed_rib"
            or update_index != 0
            or isinstance(next_record_ordinal, bool)
            or not isinstance(next_record_ordinal, int)
            or next_record_ordinal <= 0
            or position.get("boundary") != "after_complete_physical_record"
        ):
            raise BoundedPilotWorkerError("seed sample checkpoint position 非法")

        def empty_list(name: str) -> None:
            value = restored.get(name)
            if value != []:
                raise BoundedPilotWorkerError(
                    f"seed sample checkpoint {name} 必须为空"
                )

        empty_list("snapshot_refs")
        empty_list("slot_counts")
        empty_list("pending_update_events")
        empty_list("errors")
        if restored.get("seed_state_at_window_start") is not None:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint 不得预先固化 window-start state"
            )

        try:
            state = route_replay_state_from_payload(restored["state"])
            raw_audits = [_raw_from_payload(row) for row in restored["raw_audits"]]
            gaps = [_gap_from_payload(row) for row in restored["gaps"]]
        except (KeyError, TypeError, ValueError) as error:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint state/raw/gap 无法恢复"
            ) from error
        if (
            gaps
            or state.continuity_state != "continuous"
            or state.missing_reasons
        ):
            raise BoundedPilotWorkerError(
                "seed sample checkpoint seed 阶段不得预先带有输入缺口"
            )

        route_refs = restored.get("route_event_refs")
        if not isinstance(route_refs, list):
            raise BoundedPilotWorkerError(
                "seed sample checkpoint route_event_refs 非法"
            )
        entry_by_route_id = {
            entry.last_raw_ref.route_event_id: entry for entry in state.entries
        }
        change_by_key = {change.key: change for change in state.latest_changes}
        if (
            any(entry.last_action != "rib_snapshot" for entry in state.entries)
            or any(
                change.action != "rib_snapshot" for change in state.latest_changes
            )
            or set(change_by_key) != {entry.key for entry in state.entries}
            or any(
                (
                    entry.last_action,
                    entry.last_event_time_utc,
                    entry.as_path,
                    entry.quality_flags,
                    entry.last_raw_ref,
                )
                != (
                    change_by_key[entry.key].action,
                    change_by_key[entry.key].event_time_utc,
                    change_by_key[entry.key].as_path,
                    change_by_key[entry.key].quality_flags,
                    change_by_key[entry.key].raw_ref,
                )
                for entry in state.entries
            )
            or len(entry_by_route_id) != len(state.entries)
            or set(entry_by_route_id) != set(state.processed_route_event_ids)
            or len(route_refs) != len(state.processed_route_event_ids)
        ):
            raise BoundedPilotWorkerError(
                "seed sample checkpoint 不足以无损恢复 RouteEvent"
            )
        route_events = []
        seen_route_ids: set[str] = set()
        for row in route_refs:
            if not isinstance(row, Mapping):
                raise BoundedPilotWorkerError(
                    "seed sample checkpoint RouteEvent ref 非法"
                )
            route_id = row.get("route_event_id")
            entry = entry_by_route_id.get(route_id)
            if entry is None or route_id in seen_route_ids:
                raise BoundedPilotWorkerError(
                    "seed sample checkpoint RouteEvent ref 不闭合"
                )
            if dict(row) != _raw_ref_to_payload(entry.last_raw_ref):
                raise BoundedPilotWorkerError(
                    "seed sample checkpoint RouteEvent ref 与 state 冲突"
                )
            event = build_research_route_event(
                artifact_id=entry.last_raw_ref.artifact_id,
                file_sha256=entry.last_raw_ref.file_sha256,
                collector_id=entry.last_raw_ref.collector_id,
                artifact_slot_utc=entry.last_raw_ref.artifact_slot_utc,
                record_ordinal=entry.last_raw_ref.record_ordinal,
                element_ordinal=entry.last_raw_ref.element_ordinal,
                element=ParsedRouteElement(
                    event_time_utc=entry.last_event_time_utc,
                    peer_ip=entry.peer_ip,
                    peer_asn=entry.peer_asn,
                    action="rib_snapshot",
                    prefix=entry.key.prefix,
                    afi_safi=entry.key.afi_safi,
                    as_path=entry.as_path,
                    quality_flags=entry.quality_flags,
                ),
            )
            if event.route_event_id != route_id:
                raise BoundedPilotWorkerError(
                    "seed sample checkpoint RouteEvent 稳定身份冲突"
                )
            route_events.append(event)
            seen_route_ids.add(route_id)
        expected_last_order_key = (
            max(
                (
                    int(
                        _utc(
                            event.event_time_utc, "checkpoint RouteEvent time"
                        ).timestamp()
                    )
                    * 1_000_000,
                    int(
                        _utc(
                            event.artifact_slot_utc,
                            "checkpoint RouteEvent artifact slot",
                        ).timestamp()
                    )
                    * 1_000_000,
                    event.record_ordinal,
                    event.element_ordinal,
                )
                for event in route_events
            )
            if route_events
            else None
        )
        if state.last_order_key != expected_last_order_key:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint last_order_key 与 RouteEvent 不闭合"
            )

        def restored_sorted_strings(name: str) -> set[str]:
            value = restored.get(name)
            if (
                not isinstance(value, list)
                or any(not isinstance(item, str) or not item for item in value)
                or value != sorted(set(value))
            ):
                raise BoundedPilotWorkerError(
                    f"seed sample checkpoint {name} 非法"
                )
            return set(value)

        tracked_prefixes = restored_sorted_strings("tracked_prefixes")
        observed_vps = restored_sorted_strings("observed_vp_ids")
        try:
            prefixes_are_canonical = all(
                ipaddress.ip_network(prefix, strict=True).compressed == prefix
                for prefix in tracked_prefixes
            )
        except ValueError:
            prefixes_are_canonical = False
        if not prefixes_are_canonical:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint tracked_prefixes 非规范"
            )
        if {event.prefix for event in route_events} != tracked_prefixes:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint tracked_prefixes 与 state 不闭合"
            )
        if {entry.key.vp_id for entry in state.entries} - observed_vps:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint observed_vp_ids 与 state 不闭合"
            )
        pre_discovery = restored.get("pre_discovery_context_unknown")
        if pre_discovery != []:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint seed 阶段不得有动态发现上下文"
            )
        pre_discovery = []

        try:
            ambiguous_prefix_values = ambiguity["prefixes"]
            ambiguous_record_values = ambiguity["record_refs"]
            ambiguous_vp_values = ambiguity["vp_ids"]
            ambiguous_element_count = ambiguity["element_count"]
            mapped_target_relation_count = ambiguity["mapped_target_relation_count"]
        except KeyError as error:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint ambiguity 字段不闭合"
            ) from error
        if set(ambiguity) != {
            "element_count",
            "prefixes",
            "record_refs",
            "vp_ids",
            "mapped_target_relation_count",
        }:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint ambiguity 字段不闭合"
            )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (ambiguous_element_count, mapped_target_relation_count)
        ):
            raise BoundedPilotWorkerError(
                "seed sample checkpoint ambiguity 计数非法"
            )
        if not all(
            isinstance(value, list)
            for value in (
                ambiguous_prefix_values,
                ambiguous_record_values,
                ambiguous_vp_values,
            )
        ):
            raise BoundedPilotWorkerError(
                "seed sample checkpoint ambiguity 人口非法"
            )
        ambiguous_prefixes = set(ambiguous_prefix_values)
        ambiguous_vps = set(ambiguous_vp_values)
        if (
            ambiguous_prefix_values != sorted(ambiguous_prefixes)
            or ambiguous_vp_values != sorted(ambiguous_vps)
        ):
            raise BoundedPilotWorkerError(
                "seed sample checkpoint ambiguity 人口必须去重排序"
            )
        ambiguous_records = {}
        for row in ambiguous_record_values:
            if not isinstance(row, Mapping):
                raise BoundedPilotWorkerError(
                    "seed sample checkpoint ambiguity raw ref 非法"
                )
            key = (row.get("artifact_id"), row.get("record_ordinal"))
            if key in ambiguous_records:
                raise BoundedPilotWorkerError(
                    "seed sample checkpoint ambiguity raw ref 重复"
                )
            ambiguous_records[key] = dict(row)

        computed_ambiguous_events = []
        computed_mapped_target_relation_count = 0
        for event in route_events:
            _possible, is_ambiguous, is_mapped_target = _origin_relevance(
                event, country_mapping
            )
            computed_mapped_target_relation_count += int(is_mapped_target)
            if is_ambiguous:
                computed_ambiguous_events.append(event)
        if (
            ambiguous_element_count != len(computed_ambiguous_events)
            or mapped_target_relation_count
            != computed_mapped_target_relation_count
            or ambiguous_prefixes
            != {event.prefix for event in computed_ambiguous_events}
            or ambiguous_vps
            != {event.vp_id for event in computed_ambiguous_events}
        ):
            raise BoundedPilotWorkerError(
                "seed sample checkpoint ambiguity 与 RouteEvent 不闭合"
            )

        def nonnegative_resource(name: str, *, integer: bool) -> int | float:
            value = resources.get(name)
            expected_types = (int,) if integer else (int, float)
            if isinstance(value, bool) or not isinstance(value, expected_types):
                raise BoundedPilotWorkerError(
                    f"seed sample checkpoint resources.{name} 非法"
                )
            normalized = int(value) if integer else float(value)
            if normalized < 0 or (not integer and not math.isfinite(normalized)):
                raise BoundedPilotWorkerError(
                    f"seed sample checkpoint resources.{name} 非法"
                )
            return normalized

        raw_bytes = int(nonnegative_resource("new_raw_read_bytes", integer=True))
        peak_temp = int(nonnegative_resource("peak_temporary_bytes", integer=True))
        restored_cumulative_worker_runtime = float(
            nonnegative_resource("cumulative_worker_runtime_seconds", integer=False)
        )
        restored_max_worker_elapsed = float(
            nonnegative_resource("max_worker_elapsed_seconds", integer=False)
        )
        if (
            set(resources)
            != {
                "new_raw_read_bytes",
                "peak_temporary_bytes",
                "database_writes",
                "cumulative_worker_runtime_seconds",
                "max_worker_elapsed_seconds",
            }
            or raw_bytes <= 0
            or resources.get("database_writes") != 0
            or restored_max_worker_elapsed > restored_cumulative_worker_runtime
        ):
            raise BoundedPilotWorkerError(
                "seed sample checkpoint resources 与 seed 边界不闭合"
            )
        sequence = restored.get("checkpoint_sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint checkpoint_sequence 非法"
            )
        checkpoint_sequence = sequence + 1

        sample_gap = InputGap(
            _utc_text(start),
            _utc_text(pilot_end),
            "seed_rib_truncated_bounded_sample",
        )
        gaps.append(sample_gap)
        state = extend_streaming_rib_seed(state, (), input_gaps=(sample_gap,))
        if seed is not None:
            seed_time = _utc(seed.get("artifact_time_utc"), "state_seed_rib time")
            if seed_time < start:
                catch_up_gap = InputGap(
                    _utc_text(seed_time),
                    _utc_text(start),
                    "seed_catch_up_updates_out_of_worker_scope",
                )
                gaps.append(catch_up_gap)
                state = extend_streaming_rib_seed(
                    state, (), input_gaps=(catch_up_gap,)
                )
        baseline_state = state
        seed_sample_next_record_ordinal = next_record_ordinal
        phase = "updates"
        update_index = 0
        next_record_ordinal = 0

    raw_index = {
        (row.artifact_id, row.record_ordinal): row for row in raw_audits
    }
    if len(raw_index) != len(raw_audits):
        raise BoundedPilotWorkerError("checkpoint raw audit 坐标重复")
    if seed_sample_next_record_ordinal is not None:
        if seed is None:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint 与缺失的 state_seed_rib 不闭合"
            )
        expected_seed_identity = (
            seed.get("artifact_id"),
            seed.get("file_sha256"),
            seed.get("collector_id"),
            seed.get("artifact_time_utc"),
        )
        prior_record_ordinal = -1
        prior_record_end = -1
        for raw in raw_audits:
            raw_event_time = _utc(
                raw.event_time_utc,
                "seed sample checkpoint raw_audit.event_time_utc",
            )
            _sha256(
                raw.raw_record_sha256,
                "seed sample checkpoint raw_audit.raw_record_sha256",
            )
            numeric_values = (
                raw.record_ordinal,
                raw.record_offset,
                raw.record_length,
                raw.event_epoch_microseconds,
                raw.mrt_type,
                raw.mrt_subtype,
            )
            if (
                (
                    raw.artifact_id,
                    raw.file_sha256,
                    raw.collector_id,
                    raw.artifact_slot_utc,
                )
                != expected_seed_identity
                or raw.record_ordinal >= seed_sample_next_record_ordinal
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                    for value in numeric_values
                )
                or raw.record_length < 12
                or (
                    raw.mrt_type == 12
                    and raw.mrt_subtype not in {1, 2}
                )
                or (
                    raw.mrt_type == 13
                    and raw.mrt_subtype not in {1, 2, 4}
                )
                or raw.mrt_type not in {12, 13}
                or raw.event_epoch_microseconds
                != int(raw_event_time.timestamp()) * 1_000_000
                or raw.record_ordinal <= prior_record_ordinal
                or raw.record_offset < prior_record_end
            ):
                raise BoundedPilotWorkerError(
                    "seed sample checkpoint raw audit 越出 seed 已完成记录边界"
                )
            prior_record_ordinal = raw.record_ordinal
            prior_record_end = raw.record_offset + raw.record_length
        for event in route_events:
            raw = raw_index.get((event.artifact_id, event.record_ordinal))
            if (
                raw is None
                or (
                    event.artifact_id,
                    event.file_sha256,
                    event.collector_id,
                    event.artifact_slot_utc,
                )
                != expected_seed_identity
            ):
                raise BoundedPilotWorkerError(
                    "seed sample checkpoint RouteEvent 与 raw/seed 证据不闭合"
                )
        for key, row in ambiguous_records.items():
            raw = raw_index.get(key)
            if raw is None or dict(row) != _raw_record_ref(raw):
                raise BoundedPilotWorkerError(
                    "seed sample checkpoint ambiguity raw ref 与 raw audit 不闭合"
                )
        route_record_keys = {
            (event.artifact_id, event.record_ordinal) for event in route_events
        }
        if route_record_keys - set(raw_index):
            raise BoundedPilotWorkerError(
                "seed sample checkpoint RouteEvent 缺少 raw audit"
            )
        if ambiguous_vps - observed_vps:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint ambiguity VP 不在已观测人口"
            )
        if raw_bytes > seed["size_bytes"]:
            raise BoundedPilotWorkerError(
                "seed sample checkpoint raw bytes 超过 seed 制品大小"
            )

    chunk_started = run_started
    chunk_active = False
    cumulative_worker_runtime = restored_cumulative_worker_runtime
    max_worker_elapsed = restored_max_worker_elapsed

    def current_process_elapsed() -> float:
        now = clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise BoundedPilotWorkerError("clock 必须返回有限数")
        elapsed = float(now) - run_started
        if not math.isfinite(elapsed) or elapsed < 0:
            raise BoundedPilotWorkerError("clock 不得倒退且必须返回有限数")
        return elapsed

    def refresh_runtime_evidence() -> float:
        nonlocal cumulative_worker_runtime, max_worker_elapsed
        elapsed = current_process_elapsed()
        cumulative_worker_runtime = restored_cumulative_worker_runtime + elapsed
        max_worker_elapsed = max(restored_max_worker_elapsed, elapsed)
        return elapsed

    last_gate = _resource_result(
        raw_bytes=raw_bytes,
        elapsed=current_process_elapsed(),
        temporary_bytes=peak_temp,
        output_bytes=0,
        limits=limits,
        checkpoint_directory=checkpoint_root,
    )

    def begin_chunk() -> None:
        nonlocal chunk_started, chunk_active
        chunk_started = clock()
        chunk_active = True

    def end_chunk() -> float:
        nonlocal chunk_active
        if not chunk_active:
            return 0.0
        elapsed = max(0.0, float(clock() - chunk_started))
        chunk_active = False
        return elapsed

    def flush_seed_batch() -> None:
        """把完整 RIB record 组成的有界批次原子并入状态。"""

        nonlocal state, pending_seed_record_count, seed_batch_flush_failed
        if not pending_seed_events:
            pending_seed_record_count = 0
            return
        try:
            next_state = extend_streaming_rib_seed(state, tuple(pending_seed_events))
        except ValueError:
            # 防止外层异常处理再次尝试同一个失败批次并写出不闭合 checkpoint。
            seed_batch_flush_failed = True
            raise
        state = next_state
        route_events.extend(pending_seed_events)
        pending_seed_events.clear()
        pending_seed_record_count = 0

    def checkpoint_context() -> dict[str, Any]:
        refresh_runtime_evidence()
        if state is None:
            current_state = extend_streaming_rib_seed(None, ())
        else:
            current_state = state
        return {
            "selection_id": selection_id,
            "selection_semantic_fingerprint_sha256": selection_hash,
            "mapping_fingerprint_sha256": mapping_hash,
            "pilot_start_utc": _utc_text(start),
            "pilot_end_exclusive_utc": _utc_text(pilot_end),
            "checkpoint_sequence": checkpoint_sequence,
            "position": {
                "phase": phase,
                "update_index": update_index,
                "next_record_ordinal": next_record_ordinal,
                "boundary": "after_complete_physical_record",
            },
            "state": route_replay_state_to_payload(current_state),
            "seed_state_at_window_start": (
                route_replay_state_to_payload(baseline_state)
                if baseline_state is not None
                else None
            ),
            "resume_policy": "coordinator_replay_persistence_only",
            "snapshot_refs": [
                {
                    "slot_start_utc": row.slot_start_utc,
                    "slot_end_exclusive_utc": row.slot_end_exclusive_utc,
                    "continuity_state": row.continuity_state,
                    "missing_reasons": list(row.missing_reasons),
                    "route_count": row.route_count,
                    "entry_count": len(row.entries),
                    "slot_change_raw_refs": [
                        _raw_ref_to_payload(change.raw_ref)
                        for change in row.slot_changes
                    ],
                }
                for row in snapshots
            ],
            "route_event_refs": [
                _raw_ref_to_payload(row.raw_ref) for row in route_events
            ],
            "raw_audits": [_raw_to_payload(row) for row in raw_audits],
            "slot_counts": [
                {**asdict(row), "missing_reasons": list(row.missing_reasons)}
                for row in slot_counts
            ],
            "tracked_prefixes": sorted(tracked_prefixes),
            "pending_update_events": [
                _event_to_payload(row) for row in pending_update_events
            ],
            "pre_discovery_context_unknown": list(pre_discovery),
            "ambiguity": {
                "element_count": ambiguous_element_count,
                "prefixes": sorted(ambiguous_prefixes),
                "record_refs": [
                    ambiguous_records[key] for key in sorted(ambiguous_records)
                ],
                "vp_ids": sorted(ambiguous_vps),
                "mapped_target_relation_count": mapped_target_relation_count,
            },
            "observed_vp_ids": sorted(observed_vps),
            "gaps": [_gap_to_payload(row) for row in gaps],
            "errors": list(errors),
            "resources": {
                "new_raw_read_bytes": raw_bytes,
                "peak_temporary_bytes": peak_temp,
                "database_writes": 0,
                "cumulative_worker_runtime_seconds": cumulative_worker_runtime,
                "max_worker_elapsed_seconds": max_worker_elapsed,
            },
        }

    def full_seed_checkpoint_context() -> dict[str, Any]:
        """构造仅供完整 seed record-boundary 续跑的闭合 checkpoint。"""

        if (
            code_identity_sha256 is None
            or seed is None
            or normalized_seed_spool_attestation is None
            or seed_spool_binding is None
            or deferred_seed_evidence.previous_record_boundary is None
        ):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint 缺少 code identity 或 seed 制品"
            )
        if phase not in {"seed_rib", "updates"}:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint 只能发布 seed_rib/updates 边界"
            )
        if pending_seed_events:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint 发布前必须合并 pending seed batch"
            )
        if snapshots or slot_counts or pending_update_events or pre_discovery:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint 不得包含 UPDATE 阶段输出"
            )
        if gaps or errors:
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint 不得携带 gap/error"
            )
        current_state = state if state is not None else extend_streaming_rib_seed(None, ())
        complete = phase == "updates"
        if complete != (baseline_state is not None):
            raise BoundedPilotWorkerError(
                "完整 seed checkpoint phase 与 baseline 不闭合"
            )
        previous_record_boundary, peer_index_context = (
            deferred_seed_evidence.checkpoint_bindings()
        )
        refresh_runtime_evidence()
        return {
            "code_identity_sha256": code_identity_sha256,
            "selection_id": selection_id,
            "selection_semantic_fingerprint_sha256": selection_hash,
            "mapping_fingerprint_sha256": mapping_hash,
            "raw_retention_mapping_kind": raw_retention_kind,
            "raw_retention_mapping_fingerprint_sha256": raw_retention_hash,
            "seed_spool_attestation_fingerprint_sha256": (
                normalized_seed_spool_attestation[
                    "semantic_fingerprint_sha256"
                ]
            ),
            "pilot_start_utc": _utc_text(start),
            "pilot_end_exclusive_utc": _utc_text(pilot_end),
            "checkpoint_sequence": checkpoint_sequence,
            "position": {
                "phase": phase,
                "update_index": 0,
                "next_record_ordinal": (
                    seed_progress_next_record_ordinal if phase == "seed_rib" else 0
                ),
                "boundary": "after_complete_physical_record",
            },
            "seed_progress": {
                "artifact_id": seed["artifact_id"],
                "file_sha256": seed["file_sha256"],
                "collector_id": seed["collector_id"],
                "artifact_time_utc": seed["artifact_time_utc"],
                "size_bytes": seed["size_bytes"],
                "next_record_ordinal": seed_progress_next_record_ordinal,
                "next_record_offset": seed_progress_next_record_offset,
                "seed_parse_complete": complete,
                "previous_record_boundary": previous_record_boundary,
                "peer_index_context": peer_index_context,
            },
            "seed_spool": dict(seed_spool_binding),
            "state": route_replay_state_to_payload(current_state),
            "seed_state_at_window_start": (
                route_replay_state_to_payload(baseline_state)
                if baseline_state is not None
                else None
            ),
            "resume_policy": "worker_full_seed_record_offset_v2",
            "route_events": [_event_to_payload(row) for row in route_events],
            "raw_audits": [_raw_to_payload(row) for row in raw_audits],
            "tracked_prefixes": sorted(tracked_prefixes),
            "ambiguity": {
                "element_count": ambiguous_element_count,
                "prefixes": sorted(ambiguous_prefixes),
                "record_refs": [
                    ambiguous_records[key] for key in sorted(ambiguous_records)
                ],
                "vp_ids": sorted(ambiguous_vps),
                "mapped_target_relation_count": mapped_target_relation_count,
            },
            "observed_vp_ids": sorted(observed_vps),
            "gaps": [],
            "errors": [],
            "resources": {
                "prior_new_raw_read_bytes": prior_new_raw_read_bytes,
                "prior_raw_accounting": (
                    dict(normalized_prior_raw_accounting)
                    if normalized_prior_raw_accounting is not None
                    else None
                ),
                "seed_raw_reservation": (
                    dict(normalized_seed_raw_reservation)
                    if normalized_seed_raw_reservation is not None
                    else None
                ),
                "new_raw_read_bytes": raw_bytes,
                "peak_temporary_bytes": peak_temp,
                "database_writes": 0,
                "cumulative_worker_runtime_seconds": cumulative_worker_runtime,
                "max_worker_elapsed_seconds": max_worker_elapsed,
            },
            "seed_read_ledger": [dict(row) for row in seed_read_ledger],
            "checkpoint_policy": _full_seed_checkpoint_policy(
                planned_seed_checkpoint_seconds=(
                    planned_seed_checkpoint_seconds
                ),
                worker_soft_stop_seconds=limits.worker_soft_stop_seconds,
                max_worker_runtime_seconds=limits.max_worker_runtime_seconds,
            ),
        }

    def update_diagnostic_context(
        *,
        artifact: Mapping[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        """返回不遍历大状态、不可用于恢复的 UPDATE 停机证据。"""

        current_runtime = refresh_runtime_evidence()
        current_state = (
            state if state is not None else extend_streaming_rib_seed(None, ())
        )
        last_audit = raw_audits[-1] if raw_audits else None
        return {
            "selection_id": selection_id,
            "selection_semantic_fingerprint_sha256": selection_hash,
            "mapping_fingerprint_sha256": mapping_hash,
            "raw_retention_mapping_kind": raw_retention_kind,
            "raw_retention_mapping_fingerprint_sha256": raw_retention_hash,
            "pilot_start_utc": _utc_text(start),
            "pilot_end_exclusive_utc": _utc_text(pilot_end),
            "checkpoint_sequence": checkpoint_sequence,
            "diagnostic_reason": reason,
            "position": {
                "phase": phase,
                "update_index": update_index,
                "next_record_ordinal": next_record_ordinal,
                "boundary": "after_complete_physical_record",
            },
            "active_artifact": {
                "artifact_id": artifact["artifact_id"],
                "file_sha256": artifact["file_sha256"],
                "collector_id": artifact["collector_id"],
                "artifact_time_utc": artifact["artifact_time_utc"],
                "size_bytes": artifact["size_bytes"],
            },
            "last_retained_raw_audit": (
                _raw_to_payload(last_audit) if last_audit is not None else None
            ),
            "state_summary": {
                "entry_count": len(current_state.entries),
                "latest_change_count": len(current_state.latest_changes),
                "processed_route_event_id_count": len(
                    current_state.processed_route_event_ids
                ),
                "continuity_state": current_state.continuity_state,
                "missing_reasons": list(current_state.missing_reasons),
                "last_order_key": (
                    list(current_state.last_order_key)
                    if current_state.last_order_key is not None
                    else None
                ),
            },
            "buffer_summary": {
                "pending_update_event_count": len(pending_update_events),
                "route_event_count": len(route_events),
                "raw_audit_count": len(raw_audits),
                "snapshot_count": len(snapshots),
                "slot_count": len(slot_counts),
            },
            "resume_supported": False,
            "resume_policy": "diagnostic_only_never_resume",
            "recovery_payload_state": "omitted_to_meet_stop_deadline",
            "omitted_recovery_payloads": [
                "state",
                "seed_state_at_window_start",
                "pending_update_events",
                "route_event_refs",
                "raw_audits",
                "snapshots",
            ],
            "publication_order": "before_update_stream_close",
            "resources_at_publication": {
                "new_raw_read_bytes": raw_bytes,
                "peak_temporary_bytes": peak_temp,
                "database_writes": 0,
                "current_chunk_elapsed_seconds": max(
                    0.0, float(clock() - chunk_started)
                ),
                "current_process_runtime_seconds": current_runtime,
                "cumulative_worker_runtime_seconds_before_current_chunk": (
                    restored_cumulative_worker_runtime
                ),
                "cumulative_worker_runtime_seconds": cumulative_worker_runtime,
            },
            "resource_limits": limits.to_dict(),
        }

    def publish_update_diagnostic(
        *,
        artifact: Mapping[str, Any],
        reason: str,
    ) -> Tuple[str, int]:
        path, size = _publish_diagnostic_checkpoint(
            checkpoint_root,
            selection_id=selection_id,
            sequence=checkpoint_sequence,
            context=update_diagnostic_context(artifact=artifact, reason=reason),
        )
        return str(path), size

    def finish(
        status: str,
        reason: Optional[str],
        *,
        published_checkpoint: Optional[Tuple[str, int]] = None,
        full_seed_checkpoint: bool = False,
        suppress_checkpoint: bool = False,
    ) -> BoundedPilotWorkerResult:
        nonlocal last_gate, peak_temp
        if pending_seed_events:
            raise BoundedPilotWorkerError("seed RIB pending batch 未在结束前并入状态")
        deferred_seed_evidence.merge_observed_vps(observed_vps)
        end_chunk()
        elapsed = refresh_runtime_evidence()
        last_gate = _resource_result(
            raw_bytes=raw_bytes,
            elapsed=elapsed,
            temporary_bytes=peak_temp,
            output_bytes=0,
            limits=limits,
            checkpoint_directory=checkpoint_root,
        )
        if status == "complete" and last_gate.decision != "allowed":
            status = "incomplete"
            reason = last_gate.decision
        checkpoint_path: Optional[str] = None
        output_bytes = 0
        if published_checkpoint is not None:
            if status == "complete" or full_seed_checkpoint:
                raise BoundedPilotWorkerError("完整 worker 不得引用诊断检查点")
            checkpoint_path, output_bytes = published_checkpoint
        elif status != "complete" and not suppress_checkpoint:
            if full_seed_checkpoint:
                path, output_bytes, observed_peak = _publish_full_seed_checkpoint(
                    checkpoint_root,
                    selection_id=selection_id,
                    sequence=checkpoint_sequence,
                    context=full_seed_checkpoint_context(),
                    maximum_temporary_bytes=limits.max_temporary_bytes,
                )
                peak_temp = max(peak_temp, observed_peak)
            else:
                path, output_bytes = _publish_checkpoint(
                    checkpoint_root,
                    selection_id=selection_id,
                    sequence=checkpoint_sequence,
                    context=checkpoint_context(),
                )
            checkpoint_path = str(path)
        elapsed = refresh_runtime_evidence()
        last_gate = _resource_result(
            raw_bytes=raw_bytes,
            elapsed=elapsed,
            temporary_bytes=peak_temp,
            output_bytes=output_bytes,
            limits=limits,
            checkpoint_directory=checkpoint_root,
        )
        if full_seed_checkpoint and last_gate.decision != "allowed":
            status = "incomplete"
            reason = last_gate.decision
        current_state = state if state is not None else extend_streaming_rib_seed(None, ())
        strict_state = "unknown" if ambiguous_element_count else "measurable"
        compatible_state = (
            "unknown_source_gap"
            if current_state.continuity_state != "continuous"
            else "measurable"
            if mapped_target_relation_count
            else "unknown_no_mapped_target_relation"
        )
        blockers = []
        if ambiguous_element_count:
            blockers.append("country_impact_ambiguous_relation_global_unknown")
        if pre_discovery:
            blockers.append("dynamic_prefix_pre_discovery_context_unknown")
        if current_state.continuity_state != "continuous":
            blockers.append("route_state_input_gap")
        return BoundedPilotWorkerResult(
            schema_version=WORKER_SCHEMA_VERSION,
            selection_id=selection_id,
            pilot_start_utc=_utc_text(start),
            pilot_end_exclusive_utc=_utc_text(pilot_end),
            status=status,
            incomplete_reason=reason,
            state=current_state,
            seed_state_at_window_start=baseline_state,
            snapshots=tuple(snapshots),
            route_events=tuple(route_events),
            raw_audits=tuple(raw_audits),
            slot_counts=tuple(slot_counts),
            observed_vp_ids=tuple(sorted(observed_vps)),
            tracked_prefixes=tuple(sorted(tracked_prefixes)),
            pre_discovery_context_unknown=tuple(pre_discovery),
            ambiguity=AmbiguityPopulation(
                ambiguous_element_count=ambiguous_element_count,
                ambiguous_prefixes=tuple(sorted(ambiguous_prefixes)),
                ambiguous_record_refs=tuple(
                    ambiguous_records[key] for key in sorted(ambiguous_records)
                ),
                ambiguous_vp_ids=tuple(sorted(ambiguous_vps)),
                strict_population_state=strict_state,
                mapped_compatible_cohort_state=compatible_state,
                quality_blockers=tuple(sorted(blockers)),
            ),
            gaps=tuple(gaps),
            errors=tuple(errors),
            resources={
                "prior_new_raw_read_bytes": prior_new_raw_read_bytes,
                "prior_raw_accounting": (
                    dict(normalized_prior_raw_accounting)
                    if normalized_prior_raw_accounting is not None
                    else None
                ),
                "seed_raw_reservation": (
                    dict(normalized_seed_raw_reservation)
                    if normalized_seed_raw_reservation is not None
                    else None
                ),
                "new_raw_read_bytes": raw_bytes,
                "process_runtime_seconds": elapsed,
                "cumulative_worker_runtime_seconds": cumulative_worker_runtime,
                "max_worker_elapsed_seconds": max_worker_elapsed,
                "peak_temporary_bytes": peak_temp,
                "database_writes": 0,
                "resource_gate": last_gate.to_dict(),
            },
            checkpoint_path=checkpoint_path,
        )

    def preflight_artifact(artifact: Mapping[str, Any]) -> Optional[BoundedPilotWorkerResult]:
        nonlocal last_gate
        _safe_artifact_path(raw_root, artifact)
        # 一次 pass 最坏会读完整个压缩文件；在打开前保守预留，避免 read-ahead
        # 使实际计数越过 50GB 后才停止。
        projected = raw_bytes + artifact["size_bytes"]
        projected_gate = _resource_result(
            raw_bytes=projected,
            elapsed=current_process_elapsed(),
            temporary_bytes=peak_temp,
            output_bytes=0,
            limits=limits,
            checkpoint_directory=checkpoint_root,
        )
        if projected_gate.decision != "allowed":
            last_gate = projected_gate
            return finish("incomplete", projected_gate.decision)
        return None

    def audit_or_compare(
        raw: RawRecordEvidence, *, skipped: bool, retain: bool
    ) -> None:
        key = (raw.artifact_id, raw.record_ordinal)
        existing = raw_index.get(key)
        if skipped:
            if existing is not None and existing != raw:
                raise BoundedPilotWorkerError(
                    "恢复重读的 physical record 与 checkpoint raw audit 不一致"
                )
            return
        if not retain:
            return
        if existing is not None:
            raise BoundedPilotWorkerError("physical record raw audit 坐标重复")
        raw_index[key] = raw
        raw_audits.append(raw)

    # 1. seed RIB：analysis_ribs 即使包含同一制品也不会在本 worker 被打开。
    if phase == "seed_rib":
        if seed is None:
            gap = InputGap(
                _utc_text(start), _utc_text(pilot_end), "state_seed_rib_missing"
            )
            gaps.append(gap)
            state = extend_streaming_rib_seed(state, (), input_gaps=(gap,))
            baseline_state = state
            phase = "updates"
            next_record_ordinal = 0
        else:
            seed_time = _utc(seed.get("artifact_time_utc"), "state_seed_rib time")
            if seed_time > start:
                raise BoundedPilotWorkerError("state_seed_rib 晚于研究窗口")
            seed_pass_resume_ordinal = next_record_ordinal
            seed_pass_resume_offset = seed_progress_next_record_offset
            begin_chunk()
            accumulator = ObservedVpAccumulator(seed["collector_id"])
            deferred_seed_evidence.attach_accumulator(accumulator)

            def observe_seed_checkpoint(
                boundary: RibRecordBoundary,
                peer_context: Optional[RibPeerIndexContext],
            ) -> None:
                deferred_seed_evidence.observe_boundary(boundary, peer_context)

            stopped = False
            adapter = None
            reader = None
            decoded = None
            compressed_raw_bytes_this_segment = 0
            pass_base = raw_bytes
            try:
                if full_seed_checkpoint_enabled:
                    if normalized_seed_spool_attestation is None:
                        raise BoundedPilotWorkerError(
                            "完整 seed spool 缺少冻结 attestation"
                        )
                    expected_spool = _seed_spool_destination(
                        checkpoint_root, normalized_seed_spool_attestation
                    )
                    if resume_checkpoint_path is None:
                        current_directory_bytes = _checkpoint_directory_bytes(
                            checkpoint_root
                        )
                        projected_temporary_bytes = (
                            current_directory_bytes
                            + (
                                0
                                if reuse_existing_seed_spool
                                else normalized_seed_spool_attestation[
                                    "decompressed"
                                ]["size_bytes"]
                            )
                            + _MAX_CHECKPOINT_BYTES
                        )
                        projected_gate = _resource_result(
                            raw_bytes=(
                                raw_bytes
                                if reuse_existing_seed_spool
                                else raw_bytes + seed["size_bytes"]
                            ),
                            elapsed=current_process_elapsed(),
                            temporary_bytes=projected_temporary_bytes,
                            output_bytes=_MAX_CHECKPOINT_BYTES,
                            limits=limits,
                            checkpoint_directory=checkpoint_root,
                        )
                        if projected_gate.decision != "allowed":
                            last_gate = projected_gate
                            return finish(
                                "incomplete",
                                projected_gate.decision,
                                suppress_checkpoint=True,
                            )
                        if reuse_existing_seed_spool:
                            spool_identity = verify_rib_decompressed_spool(
                                expected_spool,
                                expected_decompressed_sha256=(
                                    normalized_seed_spool_attestation[
                                        "decompressed"
                                    ]["sha256"]
                                ),
                                expected_decompressed_size_bytes=(
                                    normalized_seed_spool_attestation[
                                        "decompressed"
                                    ]["size_bytes"]
                                ),
                            )
                            seed_spool_binding = (
                                spool_identity.checkpoint_binding()
                            )
                        else:
                            compressed_path = _safe_artifact_path(raw_root, seed)
                            compressed_raw_bytes_this_segment = seed["size_bytes"]
                            # 失败路径保守按完整 source pass 计数，绝不低估审批量。
                            raw_bytes += compressed_raw_bytes_this_segment
                            spool_result = build_rib_decompressed_spool(
                                compressed_path,
                                expected_spool,
                                expected_compressed_sha256=seed["file_sha256"],
                                expected_compressed_size_bytes=seed["size_bytes"],
                                max_temporary_bytes=(
                                    limits.max_temporary_bytes
                                    - current_directory_bytes
                                    - _MAX_CHECKPOINT_BYTES
                                ),
                                expected_decompressed_sha256=(
                                    normalized_seed_spool_attestation[
                                        "decompressed"
                                    ]["sha256"]
                                ),
                                expected_decompressed_size_bytes=(
                                    normalized_seed_spool_attestation[
                                        "decompressed"
                                    ]["size_bytes"]
                                ),
                            )
                            seed_spool_binding = (
                                spool_result.checkpoint_binding()
                            )
                    else:
                        if (
                            seed_spool_binding is None
                            or seed_spool_binding["file_name"]
                            != expected_spool.name
                        ):
                            raise BoundedPilotWorkerError(
                                "完整 seed checkpoint spool 文件名身份不一致"
                            )
                    spool_path = checkpoint_root / seed_spool_binding["file_name"]
                    current_directory_bytes = _checkpoint_directory_bytes(
                        checkpoint_root
                    )
                    reserved_temporary_bytes = (
                        current_directory_bytes + _MAX_CHECKPOINT_BYTES
                    )
                    reserved_gate = _resource_result(
                        raw_bytes=raw_bytes,
                        elapsed=current_process_elapsed(),
                        temporary_bytes=reserved_temporary_bytes,
                        output_bytes=_MAX_CHECKPOINT_BYTES,
                        limits=limits,
                        checkpoint_directory=checkpoint_root,
                    )
                    if reserved_gate.decision != "allowed":
                        last_gate = reserved_gate
                        return finish(
                            "incomplete",
                            reserved_gate.decision,
                            suppress_checkpoint=True,
                        )
                    peak_temp = max(peak_temp, current_directory_bytes)
                    adapter = iter_rib_spool_artifact_records(
                        spool_path,
                        expected_decompressed_sha256=seed_spool_binding["sha256"],
                        expected_decompressed_size_bytes=seed_spool_binding[
                            "size_bytes"
                        ],
                        next_record_ordinal=seed_pass_resume_ordinal,
                        next_record_offset=seed_pass_resume_offset,
                        previous_record_boundary=(
                            deferred_seed_evidence.previous_record_boundary
                        ),
                        peer_index_context=deferred_seed_evidence.peer_index_context,
                        artifact=seed,
                        origin_asn_predicate=lambda asn: (
                            raw_retention_membership(asn) is not False
                        ),
                        vp_observer=accumulator.observe,
                        include_discarded_element_decisions=False,
                        prefilter_materialize_rib_ordinals=(
                            prefilter_materialize_rib_ordinals
                        ),
                        checkpoint_observer=observe_seed_checkpoint,
                    )
                else:
                    early = preflight_artifact(seed)
                    if early is not None:
                        return early
                    path = _safe_artifact_path(raw_root, seed)
                    reader = _open_rib_reader(path, seed)
                    decoded = gzip.GzipFile(fileobj=reader, mode="rb")
                    adapter = iter_rib_artifact_records(
                        decoded,
                        artifact=seed,
                        origin_asn_predicate=lambda asn: (
                            raw_retention_membership(asn) is not False
                        ),
                        vp_observer=accumulator.observe,
                        include_discarded_element_decisions=False,
                        prefilter_materialize_rib_ordinals=(
                            prefilter_materialize_rib_ordinals
                        ),
                    )

                for record in adapter:
                    skipped = (
                        not full_seed_checkpoint_enabled
                        and record.raw_record.record_ordinal < next_record_ordinal
                    )
                    unknown_decisions = tuple(
                        decision
                        for decision in record.element_decisions
                        if decision.filter_decision == RETAINED_ORIGIN_UNKNOWN
                    )
                    audit_or_compare(
                        record.raw_record,
                        skipped=skipped,
                        retain=bool(record.route_events) or bool(unknown_decisions),
                    )
                    if not skipped:
                        if record.route_events:
                            pending_seed_events.extend(record.route_events)
                            pending_seed_record_count += 1
                        for event in record.route_events:
                            tracked_prefixes.add(event.prefix)
                            possible, ambiguous, mapped_target = _origin_relevance(
                                event, country_mapping
                            )
                            del possible
                            mapped_target_relation_count += int(mapped_target)
                            if ambiguous:
                                ambiguous_element_count += 1
                                ambiguous_prefixes.add(event.prefix)
                                ambiguous_vps.add(event.vp_id)
                        if unknown_decisions:
                            ambiguous_records[
                                (
                                    record.raw_record.artifact_id,
                                    record.raw_record.record_ordinal,
                                )
                            ] = _raw_record_ref(record.raw_record)
                        next_record_ordinal = record.raw_record.record_ordinal + 1
                        seed_progress_next_record_ordinal = next_record_ordinal
                        seed_progress_next_record_offset = (
                            record.raw_record.record_offset
                            + record.raw_record.record_length
                        )
                        if (
                            len(pending_seed_events)
                            >= seed_batch_max_route_events
                            or pending_seed_record_count
                            >= seed_batch_max_records
                        ):
                            flush_seed_batch()
                    if not full_seed_checkpoint_enabled:
                        raw_bytes = pass_base + reader.bytes_read
                    elapsed = current_process_elapsed()
                    if (
                        full_seed_checkpoint_enabled
                        and elapsed >= planned_seed_checkpoint_seconds
                    ):
                        flush_seed_batch()
                        if (
                            seed_progress_next_record_ordinal
                            <= seed_pass_resume_ordinal
                            or seed_progress_next_record_offset
                            <= seed_pass_resume_offset
                        ):
                            return finish(
                                "incomplete",
                                "seed_rib_zero_progress",
                                suppress_checkpoint=True,
                            )
                        seed_read_ledger.append(
                            {
                                "checkpoint_sequence": checkpoint_sequence,
                                "resume_from_record_ordinal": seed_pass_resume_ordinal,
                                "resume_from_record_offset": seed_pass_resume_offset,
                                "completed_through_record_ordinal_exclusive": (
                                    seed_progress_next_record_ordinal
                                ),
                                "completed_through_record_offset": (
                                    seed_progress_next_record_offset
                                ),
                                "new_compressed_raw_bytes_read": (
                                    compressed_raw_bytes_this_segment
                                ),
                                "seed_parse_complete": False,
                            }
                        )
                        return finish(
                            "incomplete",
                            "planned_seed_checkpoint",
                            full_seed_checkpoint=True,
                        )
                    last_gate = _resource_result(
                        raw_bytes=raw_bytes,
                        elapsed=elapsed,
                        temporary_bytes=(
                            reserved_temporary_bytes
                            if full_seed_checkpoint_enabled
                            else peak_temp
                        ),
                        output_bytes=0,
                        limits=limits,
                        checkpoint_directory=checkpoint_root,
                    )
                    if last_gate.decision != "allowed":
                        flush_seed_batch()
                        stopped = True
                        break
                if stopped:
                    return finish(
                        "incomplete",
                        last_gate.decision,
                        suppress_checkpoint=full_seed_checkpoint_enabled,
                    )
                if full_seed_checkpoint_enabled:
                    if seed_progress_next_record_offset != seed_spool_binding[
                        "size_bytes"
                    ]:
                        raise BoundedPilotWorkerError(
                            "seed spool 完整解析结束 offset 与解压大小不一致"
                        )
                    flush_seed_batch()
                    seed_read_ledger.append(
                        {
                            "checkpoint_sequence": checkpoint_sequence,
                            "resume_from_record_ordinal": seed_pass_resume_ordinal,
                            "resume_from_record_offset": seed_pass_resume_offset,
                            "completed_through_record_ordinal_exclusive": (
                                seed_progress_next_record_ordinal
                            ),
                            "completed_through_record_offset": (
                                seed_progress_next_record_offset
                            ),
                            "new_compressed_raw_bytes_read": (
                                compressed_raw_bytes_this_segment
                            ),
                            "seed_parse_complete": True,
                        }
                    )
                else:
                    reader.verify_complete(seed["file_sha256"])
                    raw_bytes = pass_base + reader.bytes_read
                    flush_seed_batch()
            except (OSError, EOFError, gzip.BadGzipFile, ValueError) as error:
                if not full_seed_checkpoint_enabled and reader is not None:
                    raw_bytes = pass_base + reader.bytes_read
                if pending_seed_events:
                    if seed_batch_flush_failed:
                        raise BoundedPilotWorkerError(
                            "seed RIB 批量状态合并失败，拒绝写出不闭合 checkpoint"
                        ) from error
                    flush_seed_batch()
                error_row = {
                    "phase": "seed_rib",
                    "artifact_id": seed.get("artifact_id"),
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
                error_reason = getattr(error, "reason", None)
                cause = getattr(error, "__cause__", None)
                if error_reason is None and cause is not None:
                    error_reason = getattr(cause, "reason", None)
                if isinstance(error_reason, str) and error_reason:
                    error_row["reason"] = error_reason
                errors.append(error_row)
                return finish("incomplete", "seed_rib_parse_or_integrity_failure")
            finally:
                if adapter is not None and hasattr(adapter, "close"):
                    adapter.close()
                if not full_seed_checkpoint_enabled:
                    if decoded is not None:
                        decoded.close()
                    if reader is not None:
                        reader.close()
            deferred_seed_evidence.merge_observed_vps(observed_vps)
            if seed_time < start:
                gap = InputGap(
                    _utc_text(seed_time),
                    _utc_text(start),
                    "seed_catch_up_updates_out_of_worker_scope",
                )
                gaps.append(gap)
                state = extend_streaming_rib_seed(state, (), input_gaps=(gap,))
            baseline_state = state
            end_chunk()
            phase = "updates"
            next_record_ordinal = 0

    if stop_after_seed and phase == "updates":
        return finish(
            "incomplete",
            "stop_after_seed",
            full_seed_checkpoint=True,
        )

    if state is None:  # pragma: no cover - 上述分支总会初始化。
        state = extend_streaming_rib_seed(None, ())
    if baseline_state is None:
        baseline_state = state

    # 通用 UPDATE 适配器默认仍会提升全量元素；本单事件 worker 在完整
    # physical record 上先确定相关 prefix，只为已跟踪或可能属于目标国家的
    # prefix 构造稳定 RouteEvent。独立集合用于让首次动态发现仍由下方主循环
    # 写出 pre_discovery 证据，而同一 record 内的 withdraw/announce 不会因
    # 元素顺序不同而得到不同保留结果。
    retention_tracked_prefixes = set(tracked_prefixes)

    def retain_research_elements(
        elements: Tuple[ParsedRouteElement, ...],
    ) -> Tuple[bool, ...]:
        grouped: dict[str, list[ParsedRouteElement]] = {}
        canonical_by_text: dict[str, str] = {}
        for element in elements:
            canonical_prefix = canonical_by_text.get(element.prefix)
            if canonical_prefix is None:
                # update_adapter 已对 selector 前的每个元素完成 prefix/AFI
                # 失败关闭核验；这里按 record 内原始文本缓存规范化结果，避免
                # 同一高基数 UPDATE 对 prefix 重复解析。
                canonical_prefix = ipaddress.ip_network(
                    element.prefix, strict=False
                ).compressed
                canonical_by_text[element.prefix] = canonical_prefix
            grouped.setdefault(canonical_prefix, []).append(element)
        retained_prefixes: set[str] = set()
        newly_tracked: set[str] = set()
        for prefix, prefix_elements in grouped.items():
            possible = any(
                _raw_retention_possible(element, raw_retention_membership)
                for element in prefix_elements
                if element.action == "announce"
            )
            if prefix in retention_tracked_prefixes or possible:
                retained_prefixes.add(prefix)
            if possible:
                newly_tracked.add(prefix)
        retention_tracked_prefixes.update(newly_tracked)
        return tuple(
            canonical_by_text[element.prefix] in retained_prefixes
            for element in elements
        )

    # 2. pilot UPDATE：每个 artifact 只进行一次 pass，完整槽后才应用 retained 子集。
    while update_index < len(slot_times):
        slot_start = slot_times[update_index]
        slot_end = slot_start + _FIVE_MINUTES
        slot_text = _utc_text(slot_start)
        artifact = by_slot.get(slot_text)
        if artifact is None:
            missing_reason = (
                "analysis_update_slot_not_selected_for_execution"
                if slot_text in intentionally_unprocessed_slots
                else "analysis_update_slot_missing"
            )
            gap = InputGap(slot_text, _utc_text(slot_end), missing_reason)
            gaps.append(gap)
            state, snapshot = build_five_minute_snapshot(
                state,
                slot_start_utc=slot_text,
                slot_end_exclusive_utc=_utc_text(slot_end),
                input_gaps=(gap,),
            )
            snapshots.append(snapshot)
            slot_counts.append(
                SlotCount(
                    slot_start_utc=slot_text,
                    slot_end_exclusive_utc=_utc_text(slot_end),
                    input_state="missing",
                    announce_count=None,
                    withdraw_count=None,
                    retained_announce_count=None,
                    retained_withdraw_count=None,
                    physical_record_count=None,
                    missing_reasons=(gap.missing_reason,),
                )
            )
            update_index += 1
            next_record_ordinal = 0
            continue

        begin_chunk()
        early = preflight_artifact(artifact)
        if early is not None:
            return early
        pass_base = raw_bytes
        artifact_events_base = len(route_events)
        tracked_before = set(tracked_prefixes)
        pre_discovery_base = len(pre_discovery)
        ambiguity_count_base = ambiguous_element_count
        mapped_count_base = mapped_target_relation_count
        ambiguous_prefixes_before = set(ambiguous_prefixes)
        ambiguous_vps_before = set(ambiguous_vps)
        ambiguous_records_before = dict(ambiguous_records)
        retention_tracked_before = set(retention_tracked_prefixes)
        stream = None
        adapter = None
        stopped = False
        full_announce = 0
        full_withdraw = 0
        physical_count = 0
        try:
            stream = update_record_stream_factory(dict(artifact))
            adapter = iter_adapted_update_records(
                stream,
                artifact=artifact,
                route_element_retention_selector=retain_research_elements,
            )
            for record in adapter:
                skipped = record.raw_record.record_ordinal < next_record_ordinal
                retained_before_record = len(pending_update_events)
                if not skipped:
                    physical_count += 1
                    full_announce += record.announce_count
                    full_withdraw += record.withdraw_count
                    by_prefix: dict[str, list[ResearchRouteEvent]] = {}
                    for event in record.route_events:
                        by_prefix.setdefault(event.prefix, []).append(event)
                    for prefix, prefix_events in sorted(by_prefix.items()):
                        already_tracked = prefix in tracked_prefixes
                        raw_retention_assessments = [
                            _raw_retention_possible(
                                event, raw_retention_membership
                            )
                            for event in prefix_events
                            if event.action == "announce"
                        ]
                        possible = any(raw_retention_assessments)
                        if not already_tracked and possible:
                            tracked_prefixes.add(prefix)
                            pre_discovery.append(
                                {
                                    "prefix": prefix,
                                    "state": "unknown_before_first_dynamic_discovery",
                                    "discovered_at_utc": min(
                                        event.event_time_utc for event in prefix_events
                                    ),
                                    "raw_record_ref": _raw_record_ref(record.raw_record),
                                    "policy": "single_pass_no_backfill",
                                }
                            )
                        if prefix not in tracked_prefixes:
                            continue
                        for event in prefix_events:
                            pending_update_events.append(event)
                            route_events.append(event)
                            _possible, ambiguous, mapped_target = _origin_relevance(
                                event, country_mapping
                            )
                            mapped_target_relation_count += int(mapped_target)
                            if ambiguous:
                                ambiguous_element_count += 1
                                ambiguous_prefixes.add(prefix)
                                ambiguous_vps.add(event.vp_id)
                                ambiguous_records[
                                    (
                                        record.raw_record.artifact_id,
                                        record.raw_record.record_ordinal,
                                    )
                                ] = _raw_record_ref(record.raw_record)
                    next_record_ordinal = record.raw_record.record_ordinal + 1

                audit_or_compare(
                    record.raw_record,
                    skipped=skipped,
                    retain=(
                        record.record_kind
                        in {STATE_CHANGE_RECORD, OPEN_RECORD, NOTIFICATION_RECORD}
                        or len(pending_update_events) > retained_before_record
                    ),
                )

                stats = _stream_stats(stream)
                raw_bytes = pass_base + _stream_bytes(stats)
                peak = stats.get("peak_spool_bytes", 0)
                if isinstance(peak, int) and not isinstance(peak, bool) and peak >= 0:
                    peak_temp = max(peak_temp, peak)
                elapsed = current_process_elapsed()
                # 完整 artifact 的 raw 上界已在打开前保守预留。热循环仍在
                # 每个 physical-record 边界读取 wall clock/实际资源，但只有
                # 触及任一阈值时才构造完整 gate finding（其中包含路径分类与
                # 多个 dataclass）；避免十几万次等价的 allowed 决策。
                if (
                    raw_bytes >= limits.max_new_raw_read_bytes
                    or elapsed >= limits.worker_soft_stop_seconds
                    or peak_temp >= limits.max_temporary_bytes
                ):
                    last_gate = _resource_result(
                        raw_bytes=raw_bytes,
                        elapsed=elapsed,
                        temporary_bytes=peak_temp,
                        output_bytes=0,
                        limits=limits,
                        checkpoint_directory=checkpoint_root,
                    )
                    if last_gate.decision != "allowed":
                        stopped = True
                        break
            if stopped:
                # 先发布小型、明确不可恢复的 record-boundary 诊断证据，再关闭
                # bgpdump generator。后者需要清理子进程/线程，不能阻塞第一份
                # 停机证据直到外层 600 秒 hard timeout 将进程杀死。
                diagnostic = publish_update_diagnostic(
                    artifact=artifact,
                    reason=last_gate.decision,
                )
                if adapter is not None:
                    adapter.close()
                    adapter = None
                stats = _stream_stats(stream)
                raw_bytes = pass_base + _stream_bytes(stats)
                peak = stats.get("peak_spool_bytes", 0)
                if isinstance(peak, int) and not isinstance(peak, bool) and peak >= 0:
                    peak_temp = max(peak_temp, peak)
                return finish(
                    "incomplete",
                    last_gate.decision,
                    published_checkpoint=diagnostic,
                )

            stats = _stream_stats(stream)
            if (
                stats.get("status") != "complete"
                or stats.get("compressed_file_sha256") != artifact["file_sha256"]
                or stats.get("compressed_size_bytes") != artifact["size_bytes"]
                or stats.get("compressed_read_passes") != 1
            ):
                raise BoundedPilotWorkerError(
                    "UPDATE 完整 pass 缺少 size/file_sha256/单次读取证明"
                )
            raw_bytes = pass_base + _stream_bytes(stats)
            peak = stats.get("peak_spool_bytes", 0)
            if isinstance(peak, int) and not isinstance(peak, bool) and peak >= 0:
                peak_temp = max(peak_temp, peak)
            # stdout/raw pass 完成与状态合并之间也是资源边界。此前只在
            # adapter yield 内检查，因而最后一个 record 若在软限前到达，
            # 后续不可见的 CPU 合并可越过 540/600 秒而不触发 checkpoint。
            last_gate = _resource_result(
                raw_bytes=raw_bytes,
                elapsed=current_process_elapsed(),
                temporary_bytes=peak_temp,
                output_bytes=0,
                limits=limits,
                checkpoint_directory=checkpoint_root,
            )
            if last_gate.decision != "allowed":
                diagnostic = publish_update_diagnostic(
                    artifact=artifact,
                    reason=last_gate.decision,
                )
                return finish(
                    "incomplete",
                    last_gate.decision,
                    published_checkpoint=diagnostic,
                )
            state, changes = apply_streaming_update_batch(
                state, pending_update_events
            )
            state, snapshot = build_five_minute_snapshot(
                state,
                slot_start_utc=slot_text,
                slot_end_exclusive_utc=_utc_text(slot_end),
                slot_changes=changes,
            )
            snapshots.append(snapshot)
            slot_counts.append(
                SlotCount(
                    slot_start_utc=slot_text,
                    slot_end_exclusive_utc=_utc_text(slot_end),
                    input_state="observed",
                    announce_count=full_announce,
                    withdraw_count=full_withdraw,
                    retained_announce_count=sum(
                        event.action == "announce" for event in pending_update_events
                    ),
                    retained_withdraw_count=sum(
                        event.action == "withdraw" for event in pending_update_events
                    ),
                    physical_record_count=physical_count,
                    missing_reasons=(),
                )
            )
            pending_update_events.clear()
            update_index += 1
            next_record_ordinal = 0
            end_chunk()
            elapsed = current_process_elapsed()
            last_gate = _resource_result(
                raw_bytes=raw_bytes,
                elapsed=elapsed,
                temporary_bytes=peak_temp,
                output_bytes=0,
                limits=limits,
                checkpoint_directory=checkpoint_root,
            )
            if last_gate.decision != "allowed":
                diagnostic = publish_update_diagnostic(
                    artifact=artifact,
                    reason=last_gate.decision,
                )
                return finish(
                    "incomplete",
                    last_gate.decision,
                    published_checkpoint=diagnostic,
                )
        except (OSError, EOFError, gzip.BadGzipFile, ValueError) as error:
            # 一个 UPDATE artifact 未完整验哈时，其本次新增语义不得进入最终状态。
            # raw audit 仍保留，供定位已完成 physical record；恢复会逐条重核。
            route_events[:] = route_events[:artifact_events_base]
            pending_update_events.clear()
            tracked_prefixes = tracked_before
            pre_discovery[:] = pre_discovery[:pre_discovery_base]
            ambiguous_element_count = ambiguity_count_base
            mapped_target_relation_count = mapped_count_base
            ambiguous_prefixes = ambiguous_prefixes_before
            ambiguous_vps = ambiguous_vps_before
            ambiguous_records = ambiguous_records_before
            retention_tracked_prefixes = retention_tracked_before
            if stream is not None:
                try:
                    stats = _stream_stats(stream)
                    raw_bytes = pass_base + _stream_bytes(stats)
                    peak = stats.get("peak_spool_bytes", 0)
                    if isinstance(peak, int) and not isinstance(peak, bool) and peak >= 0:
                        peak_temp = max(peak_temp, peak)
                except BoundedPilotWorkerError:
                    pass
            errors.append(
                {
                    "phase": "updates",
                    "artifact_id": artifact.get("artifact_id"),
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )
            return finish("incomplete", "update_parse_or_integrity_failure")
        finally:
            if adapter is not None:
                adapter.close()

    phase = "complete"
    return finish("complete", None)


__all__ = (
    "AmbiguityPopulation",
    "BoundedPilotWorkerError",
    "BoundedPilotWorkerResult",
    "CHECKPOINT_SCHEMA_VERSION",
    "FULL_SEED_CHECKPOINT_SCHEMA_VERSION",
    "SEED_SPOOL_ATTESTATION_SCHEMA_VERSION",
    "SlotCount",
    "WORKER_SCHEMA_VERSION",
    "run_bounded_pilot_worker",
    "validate_seed_spool_attestation",
    "verify_full_seed_checkpoint",
)
