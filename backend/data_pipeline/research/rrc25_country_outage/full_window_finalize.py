"""完整窗口 journal 的纯派生、结果封包与语义复现。

本模块只消费已经闭合的 ``full_window_journal`` 及显式冻结的配置文件。它不
读取 MRT、不连接数据库、不修改 ``CURRENT`` 或 scratch，也不把 peer session
失效解释成隐式 withdrawal。完整窗口 worker 已发布的 carried route-state 数值
会原样保留；当 VP 覆盖不完整时，样本继续使用
``observed_route_state_partial_vp_coverage``，质量报告和中文报告同时披露该限制。

最终包复用既有 baseline、episode、episode-as、质量、对账、中文报告和
package manifest 纯函数。由于 journal 保存的是预计算槽而
不是完整 ``ReplaySnapshot``，这里提供严格适配层；算法内部可以消费 carried
state 数值，但对外样本从不把 partial VP coverage 改写成完整观测。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import ctypes
import errno
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
import subprocess
import time
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Optional, Sequence, Tuple

from ...route_event import AsPathSegment, ParsedRouteElement, artifact_id_v1
from .baseline import BaselineObservation, NumericBaselineResult, derive_numeric_baseline
from .bounded_pilot_worker import (
    FULL_SEED_CHECKPOINT_SCHEMA_VERSION,
    _verified_probe_terminal_accounting,
    _verified_seed_raw_reservation,
    _validate_full_seed_checkpoint_policy,
    _validate_peer_index_context,
    _validate_previous_record_boundary,
    _mapping_identity as _pilot_mapping_identity,
    _raw_retention_identity as _pilot_raw_retention_identity,
    validate_seed_spool_attestation,
)
from .country_impact import (
    AddressFamilyDamage,
    AsnDamage,
    CohortIssue,
    CohortPrefixReference,
    CountryCohort,
    CountryMappingView,
    CountryMetrics,
    CountrySnapshotImpact,
    MeasuredAsnSet,
    MeasuredValue,
    PrefixOriginRelation,
    SameSnapshotRatio,
    VpOriginObservation,
    derive_origin_asns,
    build_raw_retention_mapping_union,
    mapping_view_from_frozen_snapshot,
    mapping_view_from_revised_snapshot,
    mapping_bundle_sha256,
    mapping_snapshot_sha256,
)
from .derived_assembly import (
    _automatic_prefix_change_event_ids,
    _baseline_report_record,
    _bind_assessments,
    _derived_claim_values,
    _evidence_registry,
    _expand_assessment_refs,
    _incident_episode_mapping_records,
    _mapping_summary,
    _source_temporal_report_records,
)
from .episode_as import build_episode_as_records
from .episodes import DetectionResult, EpisodeDetection, detect_country_outage_episodes
from .file_artifacts import (
    PublishedArtifact,
    canonical_json,
    write_canonical_json,
    write_canonical_jsonl_gzip,
)
from .full_window_journal import (
    FullWindowJournalError,
    frozen_journal_head,
    load_full_window_head,
)
from . import full_window_journal as _journal_contract
from .full_window_worker import (
    CONTROL_RECORD_SHARD_SCHEMA_VERSION,
    COUNTRY_SLOT_SCHEMA_VERSION,
    RAW_RECORD_REF_SHARD_SCHEMA_VERSION,
    RECORD_OBSERVATION_SHARD_SCHEMA_VERSION,
    ROUTE_EVENT_SHARD_SCHEMA_VERSION,
    SEED_BOOTSTRAP_ATTESTATION_FINGERPRINT_SCHEMA,
    SEED_BOOTSTRAP_ATTESTATION_SCHEMA_VERSION,
    _mapping_view_fingerprint,
    compact_state_from_payload,
    compact_state_to_payload,
    derive_artifact_boundary,
)
from .full_window_selection import validate_complete_selection_against_profile
from .package_manifest import (
    build_package_manifest,
    publish_package_metadata,
    verify_published_package,
)
from .profile import profile_sha256, validate_research_profile
from .reconciliation import build_reconciliation_result
from .reporting import build_research_report_zh
from .replay_persistence import (
    route_replay_state_from_payload,
    route_replay_state_to_payload,
)
from .research_quality import (
    DiagnosticFact,
    DiagnosticViolation,
    GATE_ORDER,
    ResearchQualityInput,
    evaluate_research_quality,
)
from .source_fact import FrozenIncidentFact, load_frozen_incident_fact
from .state_replay import (
    CONTINUOUS,
    ResearchRouteEvent,
    apply_catch_up_updates,
    build_research_route_event,
    seed_state_from_rib,
)


FINALIZATION_SCHEMA_VERSION = "rrc25-full-window-finalization/v1"
INCIDENT_POLICY_SCHEMA_VERSION = "rrc25-iran-incident-episode-link-policy/v1"
# 完整窗口内部样本保留 partial VP 扩展语义；对既有 v1 合同、算法与 Evidence
# 的投影由 `_contract_sample` 显式生成，扩展字段另发严格 sidecar，绝不静默
# 塞进 additionalProperties=false 的 v1 合同。
ANALYSIS_SAMPLE_SCHEMA_VERSION = "rrc25-full-window-analysis-sample/v1"
SEMANTIC_CORE_FINGERPRINT_SCHEMA = "rrc25_full_window_finalization_semantic_core_v1"
DEFAULT_MAX_PACKAGE_SOURCE_BYTES = 5_000_000_000
DEFAULT_FINALIZATION_SOFT_STOP_SECONDS = 540.0
RECORD_OBSERVATION_SHARD_SEMANTIC_SCHEMA = (
    "rrc25_full_window_record_observation_shard_semantic_v1"
)
RECORD_OBSERVATION_STREAM_SEMANTIC_SCHEMA = (
    "rrc25_full_window_record_observation_stream_semantic_v1"
)
CONTROL_RECORD_SHARD_SEMANTIC_SCHEMA = (
    "rrc25_full_window_control_record_shard_semantic_v1"
)
CONTROL_RECORD_STREAM_SEMANTIC_SCHEMA = (
    "rrc25_full_window_control_record_stream_semantic_v1"
)
SEED_OFFLINE_VERIFICATION_SCOPE = (
    "checkpoint_identity_and_seed_evidence_projection_without_checkpoint_bytes"
)
SEED_RETIREMENT_RECEIPT_SCHEMA = "rrc25-seed-spool-retirement-receipt/v2"
SEED_RETIREMENT_RECEIPT_FINGERPRINT_SCHEMA = (
    "rrc25_seed_spool_retirement_receipt_fingerprint_v2"
)
SEED_RETIREMENT_ATTEMPT_SCHEMA = (
    "rrc25-seed-spool-retirement-raw-attempt-receipt/v1"
)
SEED_RETIREMENT_ATTEMPT_FINGERPRINT_SCHEMA = (
    "rrc25_seed_spool_retirement_raw_attempt_receipt_fingerprint_v1"
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^research_run_v1_[0-9a-f]{24}$")
_SAMPLE_ID_RE = re.compile(r"^sample_v1_[0-9a-f]{24}$")
_SAFE_CODE_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_PARTIAL_VP_STATE = "observed_route_state_partial_vp_coverage"
_NUMERIC_STATES = frozenset({"observed", "observed_zero", _PARTIAL_VP_STATE})
_UNKNOWN_STATES = frozenset(
    {
        "unknown_source_gap",
        "unknown_parse_failure",
        "unknown_mapping",
        "unknown_state_gap",
    }
)


class FullWindowFinalizeError(ValueError):
    """冻结 journal 或最终研究包不能按既定语义闭合。"""


@dataclass(frozen=True)
class FinalizedPackage:
    root: Path
    manifest: Mapping[str, Any]
    frozen_journal_head: Mapping[str, Any]
    semantic_core_sha256: str
    compatible_episode_count: int
    revised_episode_count: int
    partial_vp_slot_count: int
    resource_receipt_path: Path


@dataclass(frozen=True)
class FullWindowBusinessOutputs:
    """完整窗口最终包的纯业务产物；不包含任何发布副作用。"""

    semantic_core: Mapping[str, Any]
    semantic_core_sha256: str
    object_files: Mapping[str, Tuple[str, Mapping[str, Any]]]
    sequence_files: Mapping[
        str, Tuple[str, Tuple[Mapping[str, Any], ...]]
    ]
    byte_files: Mapping[str, Tuple[str, bytes]]
    counts: Mapping[str, int]


@dataclass(frozen=True)
class _ShardBinding:
    sequence: int
    artifact: Optional[Mapping[str, Any]]
    ref: Mapping[str, Any]


@dataclass(frozen=True)
class _JournalData:
    frozen_head: Mapping[str, Any]
    terminal_scratch: Mapping[str, Any]
    shard_bindings: Tuple[_ShardBinding, ...]
    compatible_slots: Tuple[Mapping[str, Any], ...]
    revised_slots: Tuple[Mapping[str, Any], ...]
    route_rows: Tuple[Mapping[str, Any], ...]
    raw_rows: Tuple[Mapping[str, Any], ...]
    seed_route_rows: Tuple[Mapping[str, Any], ...]
    seed_raw_rows: Tuple[Mapping[str, Any], ...]
    control_record_count: int
    control_record_semantic_sha256: str
    record_observation_count: int
    record_observation_semantic_sha256: str
    parser_attestations: Tuple[Mapping[str, Any], ...]
    seed_bootstrap_attestation: Mapping[str, Any]
    artifacts: Tuple[Mapping[str, Any], ...]
    execution: Mapping[str, Any]


@dataclass(frozen=True)
class _FinalizationInputs:
    journal_root: Path
    profile: Mapping[str, Any]
    source_fact_snapshot: Mapping[str, Any]
    source_fact: FrozenIncidentFact
    incident_policy: Mapping[str, Any]
    compatible_mapping_snapshot: Mapping[str, Any]
    revised_mapping_snapshot: Mapping[str, Any]
    compatible_mapping: CountryMappingView
    revised_mapping: CountryMappingView
    code_identity: Mapping[str, Any]
    input_selection: Mapping[str, Any]
    claim_inventory: Mapping[str, Any]
    bindings: Mapping[str, str]
    journal: _JournalData
    independent_derivation_verification: Mapping[str, Any]
    frozen_hashes: Mapping[str, str]


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise FullWindowFinalizeError(f"{field} 必须是 64 位小写 SHA256")
    return value


def _nonnegative(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FullWindowFinalizeError(f"{field} 必须是非负整数")
    return value


def _check_finalization_soft_stop(
    *,
    started_monotonic: Optional[float],
    monotonic: Callable[[], float],
    phase: str,
) -> None:
    """长循环的 540 秒合作式停止门；到门即失败且不得进入发布。"""

    if started_monotonic is None:
        return
    elapsed = monotonic() - started_monotonic
    if not math.isfinite(elapsed) or elapsed < 0:
        raise FullWindowFinalizeError(f"{phase} 的 monotonic 计时非法")
    if elapsed >= DEFAULT_FINALIZATION_SOFT_STOP_SECONDS:
        raise FullWindowFinalizeError(
            f"{phase} 达到或超过 540 秒软停止门；本次正常停止且不得发布"
        )


def _framed_semantic_update(digest: "hashlib._Hash", value: Mapping[str, Any]) -> None:
    payload = canonical_json(dict(value)).encode("utf-8")
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _utc(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise FullWindowFinalizeError(f"{field} 必须是 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise FullWindowFinalizeError(f"{field} 不是合法 UTC 时间") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed) or parsed.microsecond:
        raise FullWindowFinalizeError(f"{field} 必须是秒级 UTC")
    canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical != value:
        raise FullWindowFinalizeError(f"{field} 不是规范 UTC")
    return value


def _utc_event(value: Any, field: str) -> str:
    """规范化可带微秒的 BGP4MP_ET/RouteEvent 时间。"""

    if not isinstance(value, str) or not value.endswith("Z"):
        raise FullWindowFinalizeError(f"{field} 必须是 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise FullWindowFinalizeError(f"{field} 不是合法 UTC 时间") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise FullWindowFinalizeError(f"{field} 必须是 UTC 时间")
    if parsed.microsecond:
        canonical = parsed.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    else:
        canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical != value:
        raise FullWindowFinalizeError(f"{field} 不是规范 UTC")
    return value


def _read_stable_regular(path: Path, *, maximum_bytes: int) -> bytes:
    """拒绝符号链接、超限和读中变更的小型文件读取。"""

    try:
        initial = path.lstat()
    except OSError as error:
        raise FullWindowFinalizeError(f"冻结输入不可读：{path}") from error
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise FullWindowFinalizeError(f"冻结输入必须是普通文件：{path}")
    if initial.st_size > maximum_bytes:
        raise FullWindowFinalizeError(f"冻结输入超过大小限制：{path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    chunks: list[bytes] = []
    total = 0
    identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    try:
        before = os.fstat(descriptor)
        if any(getattr(initial, name) != getattr(before, name) for name in identity):
            raise FullWindowFinalizeError("冻结输入在打开前发生变化")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum_bytes:
                raise FullWindowFinalizeError(f"冻结输入超过大小限制：{path}")
            chunks.append(block)
        after = os.fstat(descriptor)
        if any(getattr(before, name) != getattr(after, name) for name in identity):
            raise FullWindowFinalizeError("冻结输入在读取期间发生变化")
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def _load_json(path: os.PathLike[str] | str, *, maximum_bytes: int = 256 * 1024 * 1024) -> Mapping[str, Any]:
    raw = _read_stable_regular(Path(path), maximum_bytes=maximum_bytes)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FullWindowFinalizeError(f"冻结 JSON 非法：{path}") from error
    if not isinstance(payload, Mapping):
        raise FullWindowFinalizeError(f"冻结 JSON 顶层必须是对象：{path}")
    return dict(payload)


def _read_hashed_json(root: Path, ref: Mapping[str, Any], *, field: str) -> Mapping[str, Any]:
    if not isinstance(ref, Mapping) or set(ref) != {"path", "sha256"}:
        raise FullWindowFinalizeError(f"{field} 引用字段不闭合")
    relative = ref.get("path")
    if not isinstance(relative, str):
        raise FullWindowFinalizeError(f"{field}.path 非法")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise FullWindowFinalizeError(f"{field}.path 不是安全相对路径")
    expected = _sha(ref.get("sha256"), f"{field}.sha256")
    raw = _read_stable_regular(root.joinpath(*pure.parts), maximum_bytes=32 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise FullWindowFinalizeError(f"{field} SHA256 不一致")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FullWindowFinalizeError(f"{field} 不是合法 JSON") from error
    if not isinstance(value, Mapping):
        raise FullWindowFinalizeError(f"{field} 顶层必须是对象")
    return dict(value)


def _iter_shard_rows(root: Path, ref: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    required = {"kind", "path", "sha256", "size_bytes", "record_count"}
    if not isinstance(ref, Mapping) or set(ref) != required:
        raise FullWindowFinalizeError("shard ref 字段不闭合")
    relative = ref.get("path")
    if not isinstance(relative, str):
        raise FullWindowFinalizeError("shard path 非法")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise FullWindowFinalizeError("shard path 不是安全相对路径")
    path = root.joinpath(*pure.parts)
    expected_sha = _sha(ref.get("sha256"), "shard.sha256")
    expected_size = _nonnegative(ref.get("size_bytes"), "shard.size_bytes")
    expected_count = _nonnegative(ref.get("record_count"), "shard.record_count")
    try:
        initial = path.lstat()
    except OSError as error:
        raise FullWindowFinalizeError(f"shard 不可读：{relative}") from error
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise FullWindowFinalizeError("shard 必须是非符号链接普通文件")
    if initial.st_size != expected_size:
        raise FullWindowFinalizeError("shard size 与 receipt 不一致")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    compressed_size = 0
    count = 0

    class _DigestReader:
        def read(self, size: int = -1) -> bytes:
            nonlocal compressed_size
            block = os.read(descriptor, 1024 * 1024 if size < 0 else min(size, 1024 * 1024))
            if block:
                digest.update(block)
                compressed_size += len(block)
            return block

        def readable(self) -> bool:
            return True

    identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    try:
        before = os.fstat(descriptor)
        if any(getattr(initial, name) != getattr(before, name) for name in identity):
            raise FullWindowFinalizeError("shard 在打开前发生变化")
        try:
            with gzip.GzipFile(fileobj=_DigestReader(), mode="rb") as stream:
                for line in stream:
                    if not line.endswith(b"\n"):
                        raise FullWindowFinalizeError("shard JSONL 存在不完整行")
                    try:
                        row = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise FullWindowFinalizeError("shard JSONL 记录非法") from error
                    if not isinstance(row, Mapping):
                        raise FullWindowFinalizeError("shard JSONL 顶层记录必须是对象")
                    count += 1
                    yield dict(row)
        except (OSError, EOFError) as error:
            raise FullWindowFinalizeError("shard gzip EOF/CRC 校验失败") from error
        # gzip 可能未要求底层 reader 再读一次 EOF；显式耗尽以完成压缩字节哈希。
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            compressed_size += len(block)
        after = os.fstat(descriptor)
        if any(getattr(before, name) != getattr(after, name) for name in identity):
            raise FullWindowFinalizeError("shard 在读取期间发生变化")
    finally:
        os.close(descriptor)
    if compressed_size != expected_size or digest.hexdigest() != expected_sha:
        raise FullWindowFinalizeError("shard SHA256/size 与 receipt 不一致")
    if count != expected_count:
        raise FullWindowFinalizeError("shard record_count 与 receipt 不一致")


def _selection_fingerprint(selection: Mapping[str, Any]) -> str:
    semantic = {
        key: value
        for key, value in selection.items()
        if key not in {"selection_id", "semantic_fingerprint_sha256"}
    }
    digest = _canonical_hash(semantic)
    if selection.get("semantic_fingerprint_sha256") != digest:
        raise FullWindowFinalizeError("input selection semantic fingerprint 不一致")
    return digest


def _validate_code_identity(identity: Mapping[str, Any], expected: str) -> Mapping[str, Any]:
    if set(identity) != {"schema_version", "files", "identity_sha256"}:
        raise FullWindowFinalizeError("code identity 顶层字段不闭合")
    if identity.get("schema_version") != "domeye-research-code-identity/v1":
        raise FullWindowFinalizeError("code identity schema 不支持")
    files = identity.get("files")
    if not isinstance(files, list) or not files:
        raise FullWindowFinalizeError("code identity 必须包含非空逐文件清单")
    semantic = {"schema_version": identity["schema_version"], "files": identity["files"]}
    digest = _canonical_hash(semantic)
    if identity.get("identity_sha256") != digest or digest != expected:
        raise FullWindowFinalizeError("code identity 与 journal bindings 不一致")
    repository_root = Path(__file__).resolve().parents[4]
    required_paths = {
        "backend/data_pipeline/research/rrc25_country_outage/full_window_finalize.py",
        "backend/data_pipeline/research/rrc25_country_outage/full_window_journal.py",
        "backend/data_pipeline/research/rrc25_country_outage/full_window_worker.py",
        "dev/data_quality/rrc25_iran_full_window.py",
        "dev/data_quality/rrc25_iran_finalize.py",
    }
    observed_paths = set()
    for index, row in enumerate(files):
        if not isinstance(row, Mapping) or set(row) != {"path", "size_bytes", "sha256"}:
            raise FullWindowFinalizeError(f"code identity files[{index}] 字段不闭合")
        relative = row.get("path")
        if not isinstance(relative, str):
            raise FullWindowFinalizeError("code identity path 必须是字符串")
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
            or relative in observed_paths
        ):
            raise FullWindowFinalizeError("code identity path 非法或重复")
        observed_paths.add(relative)
        expected_size = _nonnegative(row.get("size_bytes"), "code identity size_bytes")
        expected_sha = _sha(row.get("sha256"), "code identity file sha256")
        raw = _read_stable_regular(
            repository_root.joinpath(*pure.parts), maximum_bytes=64 * 1024 * 1024
        )
        if len(raw) != expected_size or hashlib.sha256(raw).hexdigest() != expected_sha:
            raise FullWindowFinalizeError(f"当前代码与冻结身份不一致：{relative}")
    if not required_paths <= observed_paths:
        raise FullWindowFinalizeError("code identity 缺少 full-window/finalize 必需执行文件")
    return dict(identity)


def _semantic_value_sha256(schema: str, value: Any) -> str:
    return _canonical_hash({"schema": schema, "value": value})


def _validate_embedded_receipt_fingerprint(
    value: Mapping[str, Any], *, schema_version: str, fingerprint_schema: str
) -> None:
    if value.get("schema_version") != schema_version:
        raise FullWindowFinalizeError("seed retirement 内嵌 receipt schema 非法")
    semantic = dict(value)
    fingerprint = semantic.pop("receipt_fingerprint_sha256", None)
    expected = _canonical_hash(
        {"schema": fingerprint_schema, "receipt": semantic}
    )
    if fingerprint != expected:
        raise FullWindowFinalizeError("seed retirement 内嵌 receipt fingerprint 不闭合")


def _validate_seed_bootstrap_attestation(
    *,
    attestation: Mapping[str, Any],
    seed_route_rows: Sequence[Mapping[str, Any]],
    seed_raw_rows: Sequence[Mapping[str, Any]],
    execution: Mapping[str, Any],
    bindings: Mapping[str, str],
    code_identity: Mapping[str, Any],
    selection: Mapping[str, Any],
    compatible_mapping: CountryMappingView,
    revised_mapping: CountryMappingView,
) -> None:
    value = attestation
    required = {
        "schema_version",
        "checkpoint",
        "checkpoint_bindings",
        "position",
        "seed_progress",
        "resume_policy",
        "checkpoint_policy",
        "prior_raw_accounting",
        "seed_raw_reservation",
        "seed_artifact_ref",
        "expected_vp_ids",
        "expected_vp_ids_sha256",
        "vp_population_source_sha256",
        "tracked_prefixes",
        "tracked_prefixes_sha256",
        "route_state_semantic_sha256",
        "seed_route_state",
        "seed_route_events_semantic_sha256",
        "seed_raw_record_refs_semantic_sha256",
        "gaps",
        "errors",
        "seed_spool_attestation",
        "seed_parser",
        "seed_retirement",
        "initial_compact_state",
        "initial_compact_state_semantic_sha256",
        "offline_verification_scope",
        "attestation_fingerprint_sha256",
    }
    if set(value) != required or value.get("schema_version") != SEED_BOOTSTRAP_ATTESTATION_SCHEMA_VERSION:
        raise FullWindowFinalizeError("seed bootstrap attestation 字段/schema 不闭合")
    semantic = dict(value)
    supplied_fingerprint = semantic.pop("attestation_fingerprint_sha256")
    if supplied_fingerprint != _semantic_value_sha256(
        SEED_BOOTSTRAP_ATTESTATION_FINGERPRINT_SCHEMA, semantic
    ):
        raise FullWindowFinalizeError("seed bootstrap attestation fingerprint 不闭合")
    checkpoint = value.get("checkpoint")
    if not isinstance(checkpoint, Mapping) or set(checkpoint) != {
        "schema_version",
        "file_sha256",
        "size_bytes",
        "checkpoint_fingerprint_sha256",
        "checkpoint_sequence",
        "checkpoint_bytes_packaged",
        "packaging_limitation",
    }:
        raise FullWindowFinalizeError("seed checkpoint identity 字段不闭合")
    _sha(checkpoint.get("file_sha256"), "seed checkpoint file sha")
    _sha(checkpoint.get("checkpoint_fingerprint_sha256"), "seed checkpoint fingerprint")
    if (
        checkpoint.get("schema_version") != FULL_SEED_CHECKPOINT_SCHEMA_VERSION
        or _nonnegative(checkpoint.get("size_bytes"), "seed checkpoint size") <= 0
        or _nonnegative(checkpoint.get("checkpoint_sequence"), "seed checkpoint sequence") < 0
        or checkpoint.get("checkpoint_bytes_packaged") is not False
        or checkpoint.get("packaging_limitation")
        != "checkpoint_identity_hash_only_not_checkpoint_bytes"
    ):
        raise FullWindowFinalizeError("seed checkpoint 身份/未封包限制非法")
    if value.get("offline_verification_scope") != SEED_OFFLINE_VERIFICATION_SCOPE:
        raise FullWindowFinalizeError("seed 离线核验范围枚举非法")

    # checkpoint 字节本身不进最终包，因此 bootstrap attestation 必须
    # 携带 verifier 已闭合的 probe terminal 与 pre-open seed reservation。
    # probe 的 cumulative 只代表 probe terminal；journal preliminary 则还可
    # 包含此前被杀 seed attempt 的不退款 reservation，二者不能再被强制相等。
    try:
        probe_payload = value.get("prior_raw_accounting")
        if not isinstance(probe_payload, Mapping):
            raise ValueError("probe terminal accounting 非对象")
        probe_cumulative = _nonnegative(
            probe_payload.get("cumulative_reserved_new_raw_bytes"),
            "probe terminal cumulative raw bytes",
        )
        prior_raw_accounting = _verified_probe_terminal_accounting(
            probe_payload,
            expected_prior_raw_bytes=probe_cumulative,
            selection_id=str(selection.get("selection_id")),
            selection_sha256=str(selection.get("semantic_fingerprint_sha256")),
            code_identity_sha256=str(code_identity.get("identity_sha256")),
        )
        selected_seed = selection.get("roles", {}).get("state_seed_rib")
        if not isinstance(selected_seed, Mapping):
            raise ValueError("selection 缺少 seed")
        preliminary = _nonnegative(
            execution.get("preliminary_seed_read_bytes"),
            "genesis preliminary seed raw bytes",
        )
        seed_bytes = _nonnegative(
            execution.get("seed_artifact_read_bytes"),
            "genesis seed artifact raw bytes",
        )
        seed_raw_reservation = _verified_seed_raw_reservation(
            value.get("seed_raw_reservation"),
            probe_accounting=prior_raw_accounting,
            expected_prior_raw_bytes=preliminary,
            selection_id=str(selection.get("selection_id")),
            seed_artifact=selected_seed,
            code_identity_sha256=str(code_identity.get("identity_sha256")),
        )
        if (
            seed_raw_reservation["cumulative_reserved_new_raw_bytes"]
            != preliminary + seed_bytes
        ):
            raise ValueError("seed reservation cumulative 与 journal genesis 不一致")
    except ValueError as error:
        raise FullWindowFinalizeError(
            "seed bootstrap 的 probe/seed durable raw accounting 未闭合"
        ) from error
    if (
        set(bindings)
        != {
            "profile_sha256",
            "input_selection_sha256",
            "code_sha256",
            "mapping_sha256",
        }
        or prior_raw_accounting.get("prepared_bindings") != dict(bindings)
    ):
        raise FullWindowFinalizeError(
            "probe terminal accounting 未绑定 journal profile/selection/code/mapping"
        )

    expected_vps = value.get("expected_vp_ids")
    tracked_prefixes = value.get("tracked_prefixes")
    if (
        not isinstance(expected_vps, list)
        or expected_vps != sorted(set(expected_vps))
        or any(not isinstance(item, str) or not item for item in expected_vps)
        or not isinstance(tracked_prefixes, list)
        or tracked_prefixes != sorted(set(tracked_prefixes))
        or any(not isinstance(item, str) or not item for item in tracked_prefixes)
    ):
        raise FullWindowFinalizeError("seed VP/prefix 人口必须排序去重")
    if (
        value.get("expected_vp_ids_sha256")
        != _semantic_value_sha256("rrc25_seed_expected_vp_ids_v1", expected_vps)
        or value.get("tracked_prefixes_sha256")
        != _semantic_value_sha256("rrc25_seed_tracked_prefixes_v1", tracked_prefixes)
    ):
        raise FullWindowFinalizeError("seed VP/prefix semantic SHA 不闭合")
    # seed raw refs 通过同一 seed artifact 身份从全量已闭合 raw rows 精确取回。
    seed_artifact = value.get("seed_artifact_ref")
    if not isinstance(seed_artifact, Mapping) or set(seed_artifact) != {
        "artifact_id", "file_sha256", "size_bytes"
    }:
        raise FullWindowFinalizeError("seed_artifact_ref 字段不闭合")
    selected_seed = selection.get("roles", {}).get("state_seed_rib")
    if not isinstance(selected_seed, Mapping) or any(
        seed_artifact.get(field) != selected_seed.get(field)
        for field in ("artifact_id", "file_sha256", "size_bytes")
    ):
        raise FullWindowFinalizeError("seed_artifact_ref 与 selection 不一致")
    if (
        value.get("seed_route_events_semantic_sha256")
        != _semantic_value_sha256(
            "rrc25_seed_route_events_v1", list(seed_route_rows)
        )
        or value.get("seed_raw_record_refs_semantic_sha256")
        != _semantic_value_sha256(
            "rrc25_seed_raw_record_refs_v1", list(seed_raw_rows)
        )
    ):
        raise FullWindowFinalizeError("seed RouteEvent/raw shard semantic SHA 不闭合")
    expected_vp_source = _canonical_hash(
        {
            "schema": "rrc25_seed_vp_population_source_v1",
            "checkpoint_file_sha256": checkpoint["file_sha256"],
            "checkpoint_fingerprint_sha256": checkpoint["checkpoint_fingerprint_sha256"],
            "expected_vp_ids": expected_vps,
        }
    )
    if value.get("vp_population_source_sha256") != expected_vp_source:
        raise FullWindowFinalizeError("seed VP population source SHA 不闭合")

    seed_route_state_payload = value.get("seed_route_state")
    if not isinstance(seed_route_state_payload, Mapping):
        raise FullWindowFinalizeError("seed attestation 缺少完整 route state payload")
    try:
        attested_route_state = route_replay_state_from_payload(seed_route_state_payload)
        seed_events = tuple(_route_event_from_row(row) for row in seed_route_rows)
        rib_events = tuple(event for event in seed_events if event.action == "rib_snapshot")
        catch_up_events = tuple(event for event in seed_events if event.action != "rib_snapshot")
        replayed_route_state = seed_state_from_rib(rib_events)
        if catch_up_events:
            replayed_route_state = apply_catch_up_updates(
                replayed_route_state, catch_up_events
            )
    except (TypeError, ValueError) as error:
        raise FullWindowFinalizeError("seed RouteEvent 无法离线重放为 route state") from error
    if (
        canonical_json(route_replay_state_to_payload(attested_route_state))
        != canonical_json(dict(seed_route_state_payload))
        or canonical_json(route_replay_state_to_payload(replayed_route_state))
        != canonical_json(dict(seed_route_state_payload))
        or value.get("route_state_semantic_sha256")
        != _semantic_value_sha256(
            "rrc25_seed_route_state_v1", seed_route_state_payload
        )
    ):
        raise FullWindowFinalizeError("seed route state 未由包内 seed RouteEvents 唯一重放闭合")

    compact_payload = value.get("initial_compact_state")
    if not isinstance(compact_payload, Mapping):
        raise FullWindowFinalizeError("seed attestation 缺少初始 compact state")
    try:
        compact = compact_state_from_payload(compact_payload)
    except (TypeError, ValueError) as error:
        raise FullWindowFinalizeError("初始 compact state 合同非法") from error
    if canonical_json(compact_state_to_payload(compact)) != canonical_json(dict(compact_payload)):
        raise FullWindowFinalizeError("初始 compact state 不能规范 round-trip")
    if (
        value.get("initial_compact_state_semantic_sha256")
        != _semantic_value_sha256(
            "rrc25_full_window_initial_compact_state_v1", compact_payload
        )
        or list(compact.known_vp_ids) != expected_vps
        or list(compact.tracked_prefixes) != tracked_prefixes
        or compact.vp_population_source_sha256 != value.get("vp_population_source_sha256")
        or compact.compatible_mapping_fingerprint_sha256
        != _mapping_view_fingerprint(compatible_mapping)
        or compact.revised_mapping_fingerprint_sha256
        != _mapping_view_fingerprint(revised_mapping)
        or compact.route_state.entries != attested_route_state.entries
        or compact.route_state.continuity_state != attested_route_state.continuity_state
        or compact.route_state.missing_reasons != attested_route_state.missing_reasons
        or compact.route_state.last_order_key != attested_route_state.last_order_key
    ):
        raise FullWindowFinalizeError("初始 compact state 与 VP/prefix/mapping 身份不闭合")

    bindings = value.get("checkpoint_bindings")
    if not isinstance(bindings, Mapping) or set(bindings) != {
        "code_identity_sha256",
        "selection_id",
        "selection_semantic_fingerprint_sha256",
        "mapping_fingerprint_sha256",
        "raw_retention_mapping_kind",
        "raw_retention_mapping_fingerprint_sha256",
        "seed_spool_attestation_fingerprint_sha256",
        "pilot_start_utc",
        "pilot_end_exclusive_utc",
    }:
        raise FullWindowFinalizeError("seed checkpoint bindings 字段不闭合")
    if (
        bindings.get("code_identity_sha256") != code_identity.get("identity_sha256")
        or bindings.get("selection_id") != selection.get("selection_id")
        or bindings.get("selection_semantic_fingerprint_sha256")
        != selection.get("semantic_fingerprint_sha256")
    ):
        raise FullWindowFinalizeError("seed checkpoint 未绑定当前 code/selection")
    mapping_identity = _pilot_mapping_identity(compatible_mapping)
    raw_union = build_raw_retention_mapping_union(
        (compatible_mapping, revised_mapping)
    )
    raw_kind, raw_identity = _pilot_raw_retention_identity(
        raw_union,
        statistical_mapping=compatible_mapping,
        statistical_mapping_hash=mapping_identity,
    )
    selection_window = selection.get("window")
    if (
        not isinstance(selection_window, Mapping)
        or bindings.get("mapping_fingerprint_sha256") != mapping_identity
        or bindings.get("raw_retention_mapping_kind") != raw_kind
        or bindings.get("raw_retention_mapping_fingerprint_sha256") != raw_identity
        or bindings.get("pilot_start_utc") != selection_window.get("start_utc")
        or bindings.get("pilot_end_exclusive_utc")
        != selection_window.get("end_exclusive_utc")
    ):
        raise FullWindowFinalizeError(
            "seed checkpoint mapping/raw-retention/pilot window bindings 不闭合"
        )
    spool = value.get("seed_spool_attestation")
    try:
        normalized_spool = validate_seed_spool_attestation(
            spool, seed_artifact=selected_seed
        )
    except ValueError as error:
        raise FullWindowFinalizeError("seed spool attestation 非法") from error
    if (
        canonical_json(normalized_spool) != canonical_json(spool)
        or bindings.get("seed_spool_attestation_fingerprint_sha256")
        != spool.get("semantic_fingerprint_sha256")
    ):
        raise FullWindowFinalizeError("seed spool attestation 未与 checkpoint 闭合")
    position = value.get("position")
    progress = value.get("seed_progress")
    if (
        not isinstance(position, Mapping)
        or set(position) != {
            "phase", "update_index", "next_record_ordinal", "boundary"
        }
        or position.get("phase") != "updates"
        or position.get("update_index") != 0
        or position.get("next_record_ordinal") != 0
        or position.get("boundary") != "after_complete_physical_record"
        or value.get("resume_policy") != "worker_full_seed_record_offset_v2"
        or value.get("gaps") != []
        or value.get("errors") != []
    ):
        raise FullWindowFinalizeError("seed checkpoint 未证明完整无缺口进入 updates 阶段")
    progress_fields = {
        "artifact_id", "file_sha256", "collector_id", "artifact_time_utc",
        "size_bytes", "next_record_ordinal", "next_record_offset",
        "seed_parse_complete", "previous_record_boundary", "peer_index_context",
    }
    if not isinstance(progress, Mapping) or set(progress) != progress_fields:
        raise FullWindowFinalizeError("seed_progress 字段不闭合")
    if (
        any(
            progress.get(field) != selected_seed.get(field)
            for field in (
                "artifact_id", "file_sha256", "collector_id",
                "artifact_time_utc", "size_bytes",
            )
        )
        or progress.get("seed_parse_complete") is not True
        or isinstance(progress.get("next_record_ordinal"), bool)
        or not isinstance(progress.get("next_record_ordinal"), int)
        or progress.get("next_record_ordinal") <= 0
        or progress.get("next_record_offset") != spool["decompressed"]["size_bytes"]
    ):
        raise FullWindowFinalizeError("seed_progress 未与完整 seed artifact/spool 闭合")
    try:
        _validate_previous_record_boundary(
            progress.get("previous_record_boundary"),
            next_record_ordinal=progress["next_record_ordinal"],
            next_record_offset=progress["next_record_offset"],
        )
        _validate_peer_index_context(
            progress.get("peer_index_context"),
            next_record_offset=progress["next_record_offset"],
        )
        _validate_full_seed_checkpoint_policy(value.get("checkpoint_policy"))
    except ValueError as error:
        raise FullWindowFinalizeError("seed progress/checkpoint policy 非法") from error

    parser = value.get("seed_parser")
    if not isinstance(parser, Mapping) or set(parser) != {
        "schema_version", "name", "execution_policy", "code_identity_sha256",
        "source_files", "attestation_fingerprint_sha256",
    }:
        raise FullWindowFinalizeError("seed parser attestation 字段不闭合")
    parser_semantic = dict(parser)
    parser_fingerprint = parser_semantic.pop("attestation_fingerprint_sha256")
    if (
        parser.get("schema_version") != "rrc25-seed-parser-attestation/v1"
        or parser.get("name") != "domeye_rib_spool_parser"
        or parser.get("execution_policy") != "verified_in_process_source"
        or parser.get("code_identity_sha256") != code_identity.get("identity_sha256")
        or parser_fingerprint
        != _semantic_value_sha256(
            "rrc25_seed_parser_attestation_fingerprint_v1", parser_semantic
        )
    ):
        raise FullWindowFinalizeError("seed parser 身份/fingerprint 非法")
    code_rows = {row["path"]: row for row in code_identity["files"]}
    parser_paths = (
        "backend/data_pipeline/research/rrc25_country_outage/bounded_pilot_worker.py",
        "backend/data_pipeline/research/rrc25_country_outage/rib_adapter.py",
        "backend/data_pipeline/research/rrc25_country_outage/rib_parser.py",
    )
    if (
        not isinstance(parser.get("source_files"), list)
        or [row.get("path") for row in parser["source_files"]] != list(parser_paths)
        or any(code_rows.get(row["path"]) != row for row in parser["source_files"])
    ):
        raise FullWindowFinalizeError("seed parser source files 未闭合到当前 code identity")

    retirement = value.get("seed_retirement")
    if not isinstance(retirement, Mapping) or set(retirement) != {
        "schema_version", "success_receipt", "success_receipt_file_sha256",
        "raw_attempt_receipt", "raw_attempt_receipt_file_sha256",
        "spool_absence_verified", "compressed_raw_stable_identity_verified",
    }:
        raise FullWindowFinalizeError("seed retirement binding 字段不闭合")
    success = retirement.get("success_receipt")
    attempt = retirement.get("raw_attempt_receipt")
    if not isinstance(success, Mapping) or not isinstance(attempt, Mapping):
        raise FullWindowFinalizeError("seed retirement 规范 receipts 缺失")
    _validate_embedded_receipt_fingerprint(
        success,
        schema_version=SEED_RETIREMENT_RECEIPT_SCHEMA,
        fingerprint_schema=SEED_RETIREMENT_RECEIPT_FINGERPRINT_SCHEMA,
    )
    _validate_embedded_receipt_fingerprint(
        attempt,
        schema_version=SEED_RETIREMENT_ATTEMPT_SCHEMA,
        fingerprint_schema=SEED_RETIREMENT_ATTEMPT_FINGERPRINT_SCHEMA,
    )
    canonical_file_sha = lambda item: hashlib.sha256(
        (canonical_json(dict(item)) + "\n").encode("utf-8")
    ).hexdigest()
    if (
        retirement.get("schema_version") != "rrc25-seed-retirement-bootstrap-binding/v1"
        or retirement.get("success_receipt_file_sha256") != canonical_file_sha(success)
        or retirement.get("raw_attempt_receipt_file_sha256") != canonical_file_sha(attempt)
        or retirement.get("spool_absence_verified") is not True
        or retirement.get("compressed_raw_stable_identity_verified") is not True
        or success.get("operation") != "seed_spool_retirement"
        or success.get("recoverable_by_rebuild_from_compressed_raw") is not True
        or attempt.get("operation")
        != "seed_spool_retirement_raw_verification_attempt"
        or attempt.get("selection_id") != selection.get("selection_id")
    ):
        raise FullWindowFinalizeError("seed retirement success/file identity 非法")
    success_checkpoint = success.get("checkpoint")
    attempt_checkpoint = attempt.get("checkpoint")
    success_spool = success.get("spool")
    attempt_spool = attempt.get("spool")
    compressed = success.get("compressed_raw")
    compressed_expected = attempt.get("compressed_raw_expected")
    attempt_ref = success.get("raw_verification_attempt_receipt")
    decompressed_spool = spool.get("decompressed")
    if (
        not isinstance(success_checkpoint, Mapping)
        or attempt_checkpoint != success_checkpoint
        or success_checkpoint.get("checkpoint_sequence")
        != checkpoint.get("checkpoint_sequence")
        or success_checkpoint.get("checkpoint_fingerprint_sha256")
        != checkpoint.get("checkpoint_fingerprint_sha256")
        or not isinstance(success_spool, Mapping)
        or attempt_spool != success_spool
        or not isinstance(decompressed_spool, Mapping)
        or success_spool.get("sha256") != decompressed_spool.get("sha256")
        or success_spool.get("size_bytes") != decompressed_spool.get("size_bytes")
        or not isinstance(compressed, Mapping)
        or compressed.get("artifact_id") != selected_seed.get("artifact_id")
        or compressed.get("sha256") != selected_seed.get("file_sha256")
        or compressed.get("size_bytes") != selected_seed.get("size_bytes")
        or compressed.get("hash_verified") is not True
        or not isinstance(compressed_expected, Mapping)
        or compressed_expected.get("artifact_id") != selected_seed.get("artifact_id")
        or compressed_expected.get("file_sha256") != selected_seed.get("file_sha256")
        or compressed_expected.get("size_bytes") != selected_seed.get("size_bytes")
        or (
            "relative_path" in selected_seed
            and compressed_expected.get("relative_path")
            != selected_seed.get("relative_path")
        )
        or not isinstance(attempt_ref, Mapping)
        or attempt_ref.get("attempt_id") != attempt.get("attempt_id")
        or attempt_ref.get("status") != attempt.get("status")
        or attempt_ref.get("receipt_fingerprint_sha256")
        != attempt.get("receipt_fingerprint_sha256")
    ):
        raise FullWindowFinalizeError(
            "seed retirement attempt/checkpoint/spool/compressed raw 引用不闭合"
        )
    accounting = execution
    receipt_accounting = success.get("resource_accounting")
    attempt_accounting = attempt.get("raw_accounting")
    checkpoint_cumulative = (
        accounting["preliminary_seed_read_bytes"]
        + accounting["seed_artifact_read_bytes"]
    )
    if (
        not isinstance(receipt_accounting, Mapping)
        or not isinstance(attempt_accounting, Mapping)
        or receipt_accounting.get("checkpoint_cumulative_new_raw_read_bytes")
        != checkpoint_cumulative
        or receipt_accounting.get(
            "cumulative_new_raw_read_bytes_after_retirement_verification"
        ) != accounting["initial_reserved_raw_bytes"]
        or attempt_accounting.get("checkpoint_cumulative_new_raw_read_bytes")
        != checkpoint_cumulative
        or attempt_accounting.get("cumulative_new_raw_read_bytes_after_reservation")
        != accounting["initial_reserved_raw_bytes"]
        or attempt_accounting.get("full_artifact_reserved_bytes")
        != selected_seed.get("size_bytes")
        or receipt_accounting.get("retirement_verification_new_raw_read_bytes")
        != selected_seed.get("size_bytes")
        or receipt_accounting.get("reservation_policy")
        != "full_artifact_reserved_before_open_failed_or_crashed_attempts_still_count"
        or attempt_accounting.get("reservation_policy")
        != "full_artifact_reserved_before_open_failed_or_crashed_attempts_still_count"
    ):
        raise FullWindowFinalizeError("seed retirement raw accounting 与 genesis 不闭合")


def _validate_incident_policy(value: Mapping[str, Any], incident: FrozenIncidentFact) -> Mapping[str, Any]:
    required = {
        "schema_version",
        "incident_id",
        "incident_ref",
        "target_selector",
        "relation",
        "causal",
        "evidence_selection",
        "relationship_state",
        "precursor_causality_state",
        "limitations_zh",
    }
    if set(value) != required or value.get("schema_version") != INCIDENT_POLICY_SCHEMA_VERSION:
        raise FullWindowFinalizeError("incident mapping policy 字段或 schema 不闭合")
    if (
        value.get("incident_id") != incident.incident.get("incident_id")
        or value.get("incident_ref") != incident.incident.get("detail_reference")
        or value.get("target_selector") != "all_detected_compatible_episodes"
        or value.get("relation") not in {"possible_correspondence", "legacy_reconciliation", "temporal_overlap"}
        or value.get("causal") is not False
        or value.get("evidence_selection") != "all_episode_supporting_samples"
        or value.get("relationship_state") != "unresolved_not_causal"
        or value.get("precursor_causality_state") != "undetermined"
    ):
        raise FullWindowFinalizeError("Incident→Episode policy 必须保持显式非因果语义")
    limitations = value.get("limitations_zh")
    if not isinstance(limitations, list) or not limitations or any(
        not isinstance(item, str) or not item.strip() for item in limitations
    ):
        raise FullWindowFinalizeError("incident mapping policy 必须提供中文限制")
    return dict(value)


def _journal_receipts(root: Path, terminal_ref: Mapping[str, Any]) -> Tuple[Tuple[Mapping[str, Any], Mapping[str, Any]], ...]:
    rows = []
    ref: Optional[Mapping[str, Any]] = dict(terminal_ref)
    while ref is not None:
        receipt = _read_hashed_json(root, ref, field="boundary receipt")
        rows.append((dict(ref), receipt))
        previous = receipt.get("previous_receipt_ref")
        ref = None if previous is None else dict(previous)
    rows.reverse()
    sequences = [receipt.get("sequence") for _ref, receipt in rows]
    if sequences != list(range(len(rows))):
        raise FullWindowFinalizeError("boundary receipt ancestry sequence 不连续")
    return tuple(rows)


def _validate_attempt_outcome_group(
    attempt: Mapping[str, Any], outcomes: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any]:
    """验证一个 attempt 的闭包，并返回一次计量的精确值或保守区间。

    ``__ledger_ref`` 是读取 create-only ledger 后附加的本地校验元数据，不属于
    outcome 指纹内容。它只用于证明 publication-failed terminal 精确引用同一
    attempt 的 complete parse outcome，不能由包内声明自行替代。
    """

    reserved = _nonnegative(attempt.get("reserved_raw_bytes"), "reserved_raw_bytes")
    complete: Optional[Mapping[str, Any]] = None
    terminal: Optional[Mapping[str, Any]] = None
    for outcome in outcomes:
        if outcome.get("reservation_refunded_bytes") != 0:
            raise FullWindowFinalizeError("attempt outcome reservation 不得退款")
        result = outcome.get("outcome")
        if result == "complete_single_pass":
            if complete is not None or outcome.get("failure_reason") is not None:
                raise FullWindowFinalizeError("同一 attempt 只能有一个 complete parse outcome")
            try:
                artifact = _journal_contract._artifact_from_dict(
                    attempt.get("artifact"), "attempt.artifact"
                )
                proof = _journal_contract._proof_from_dict(outcome.get("proof"))
                _journal_contract._verify_single_pass(proof, artifact)
            except FullWindowJournalError as error:
                raise FullWindowFinalizeError("complete outcome single-pass proof 非法") from error
            observed = _nonnegative(
                outcome.get("observed_compressed_bytes"),
                "complete.observed_compressed_bytes",
            )
            if observed != reserved or reserved != artifact.size_bytes:
                raise FullWindowFinalizeError("complete outcome 必须完整读取且绑定 artifact size")
            complete = outcome
            continue
        if result not in {
            "failed_before_complete_single_pass",
            "publication_failed_after_complete_single_pass",
        }:
            raise FullWindowFinalizeError("attempt terminal outcome 枚举非法")
        reason = outcome.get("failure_reason")
        if (
            terminal is not None
            or not isinstance(reason, str)
            or not reason.strip()
            or reason != reason.strip()
            or "proof" in outcome
        ):
            raise FullWindowFinalizeError("terminal failure 必须唯一、有原因且不得含 proof")
        observed_state = outcome.get("observed_compressed_bytes_state")
        lower = _nonnegative(
            outcome.get("observed_compressed_bytes_lower_bound"),
            "terminal.observed_compressed_bytes_lower_bound",
        )
        upper = _nonnegative(
            outcome.get("observed_compressed_bytes_upper_bound"),
            "terminal.observed_compressed_bytes_upper_bound",
        )
        if lower > upper or upper > reserved:
            raise FullWindowFinalizeError("terminal observed interval 越出 reservation")
        if observed_state == "exact":
            exact = _nonnegative(
                outcome.get("observed_compressed_bytes"),
                "terminal.observed_compressed_bytes",
            )
            if lower != exact or upper != exact:
                raise FullWindowFinalizeError("terminal exact observed interval 不闭合")
        elif observed_state == "unknown_after_process_termination":
            if (
                outcome.get("observed_compressed_bytes") is not None
                or lower != 0
                or upper != reserved
            ):
                raise FullWindowFinalizeError(
                    "进程终止后的 observed interval 必须保守覆盖整制品"
                )
        else:
            raise FullWindowFinalizeError("terminal observed state 枚举非法")
        terminal = outcome
    terminal_state = None if terminal is None else terminal.get("outcome")
    completed_ref = (
        None if terminal is None else terminal.get("completed_parse_outcome_ref")
    )
    if terminal_state == "failed_before_complete_single_pass":
        if complete is not None or completed_ref is not None:
            raise FullWindowFinalizeError("parse 前失败不得绑定 complete outcome")
    elif terminal_state == "publication_failed_after_complete_single_pass":
        complete_ref = None if complete is None else complete.get("__ledger_ref")
        if (
            complete is None
            or not isinstance(complete_ref, Mapping)
            or completed_ref != complete_ref
            or terminal.get("observed_compressed_bytes_state") != "exact"
            or terminal.get("observed_compressed_bytes")
            != complete.get("observed_compressed_bytes")
        ):
            raise FullWindowFinalizeError(
                "发布失败必须精确绑定同 attempt 的 complete parse outcome"
            )

    exact_once: Optional[int] = 0
    lower_once = 0
    upper_once = 0
    if complete is not None:
        exact_once = _nonnegative(
            complete.get("observed_compressed_bytes"), "complete.observed"
        )
        lower_once = exact_once
        upper_once = exact_once
    elif terminal is not None:
        lower_once = _nonnegative(
            terminal.get("observed_compressed_bytes_lower_bound"),
            "terminal.observed lower",
        )
        upper_once = _nonnegative(
            terminal.get("observed_compressed_bytes_upper_bound"),
            "terminal.observed upper",
        )
        exact_once = (
            _nonnegative(
                terminal.get("observed_compressed_bytes"), "terminal.observed"
            )
            if terminal.get("observed_compressed_bytes_state") == "exact"
            else None
        )
    return {
        "observed_compressed_bytes": exact_once,
        "observed_compressed_bytes_lower_bound": lower_once,
        "observed_compressed_bytes_upper_bound": upper_once,
        "has_complete": complete is not None,
        "has_terminal": terminal is not None,
    }


def _raw_ledger_accounting(
    root: Path, *, receipt_complete_attempt_ids: Sequence[str]
) -> Mapping[str, Any]:
    attempts: dict[str, Mapping[str, Any]] = {}
    attempt_ref_by_id: dict[str, Mapping[str, str]] = {}
    outcomes: dict[str, list[Mapping[str, Any]]] = {}
    reservation = 0
    observed_exact = 0
    observed_lower = 0
    observed_upper = 0
    observed_all_exact = True
    initial_reserved: Optional[int] = None
    initial_observed: Optional[int] = None
    preliminary_seed_read_bytes: Optional[int] = None
    seed_artifact_read_bytes: Optional[int] = None
    additional_pre_update_raw_read_bytes: Optional[int] = None
    genesis_identity: Optional[Tuple[Any, Any]] = None
    ledger = root / "raw-ledger"
    for path in sorted(ledger.rglob("*.json")):
        relative = path.relative_to(root).as_posix()
        raw = _read_stable_regular(path, maximum_bytes=32 * 1024 * 1024)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FullWindowFinalizeError("raw ledger JSON 非法") from error
        if not isinstance(payload, Mapping):
            raise FullWindowFinalizeError("raw ledger 顶层必须是对象")
        payload = dict(payload)
        if relative.startswith("raw-ledger/attempts/"):
            try:
                _journal_contract._verify_fingerprint(
                    payload,
                    _journal_contract.ATTEMPT_START_SCHEMA_VERSION,
                    "attempt start",
                )
            except FullWindowJournalError as error:
                raise FullWindowFinalizeError("raw ledger attempt fingerprint 非法") from error
            attempt_id = payload.get("attempt_id")
            if not isinstance(attempt_id, str) or attempt_id in attempts:
                raise FullWindowFinalizeError("raw ledger attempt_id 非法或重复")
            attempts[attempt_id] = payload
            attempt_ref_by_id[attempt_id] = {
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            reservation += _nonnegative(payload.get("reserved_raw_bytes"), "reserved_raw_bytes")
        elif relative.startswith("raw-ledger/outcomes/"):
            try:
                _journal_contract._verify_fingerprint(
                    payload,
                    _journal_contract.ATTEMPT_OUTCOME_SCHEMA_VERSION,
                    "attempt outcome",
                )
            except FullWindowJournalError as error:
                raise FullWindowFinalizeError("raw ledger outcome fingerprint 非法") from error
            attempt_id = payload.get("attempt_id")
            if not isinstance(attempt_id, str):
                raise FullWindowFinalizeError("raw ledger outcome attempt_id 非法")
            payload["__ledger_ref"] = {
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            outcomes.setdefault(attempt_id, []).append(payload)
        elif relative.startswith("raw-ledger/genesis-"):
            if initial_reserved is not None:
                raise FullWindowFinalizeError("raw ledger genesis 必须唯一")
            try:
                _journal_contract._verify_fingerprint(
                    payload,
                    _journal_contract.RAW_GENESIS_SCHEMA_VERSION,
                    "raw genesis",
                )
            except FullWindowJournalError as error:
                raise FullWindowFinalizeError("raw genesis fingerprint 非法") from error
            initial_reserved = _nonnegative(
                payload.get("initial_reserved_raw_bytes"), "initial_reserved_raw_bytes"
            )
            preliminary = _nonnegative(
                payload.get("preliminary_seed_read_bytes"),
                "preliminary_seed_read_bytes",
            )
            seed = _nonnegative(
                payload.get("seed_artifact_read_bytes"), "seed_artifact_read_bytes"
            )
            additional = _nonnegative(
                payload.get("additional_pre_update_raw_read_bytes"),
                "additional_pre_update_raw_read_bytes",
            )
            if preliminary + seed + additional != initial_reserved:
                raise FullWindowFinalizeError("raw genesis reservation 分项不闭合")
            # 三项均来自 seed-retirement 已核验的实际读取；50GB 门仍使用不退款
            # reservation 上界，observed 只作为实测披露。
            initial_observed = initial_reserved
            preliminary_seed_read_bytes = preliminary
            seed_artifact_read_bytes = seed
            additional_pre_update_raw_read_bytes = additional
            genesis_identity = (payload.get("run_id"), payload.get("bindings"))
    if set(outcomes) - set(attempts):
        raise FullWindowFinalizeError("raw ledger outcome 引用不存在的 attempt")
    try:
        accumulator = _journal_contract._load_raw_accumulator(root)
    except (OSError, FullWindowJournalError) as error:
        raise FullWindowFinalizeError("raw ledger ACCUMULATOR 核验失败") from error
    if (
        accumulator.get("attempt_count") != len(attempts)
        or accumulator.get("cumulative_reserved_raw_bytes")
        != (0 if initial_reserved is None else initial_reserved) + reservation
    ):
        raise FullWindowFinalizeError("raw ledger ACCUMULATOR 与全量 ledger 重算不一致")
    complete_ids = []
    publication_failed_ids = []
    terminal_ids = []
    for attempt_id, grouped in outcomes.items():
        if any(outcome.get("attempt_ref") != attempt_ref_by_id[attempt_id] for outcome in grouped):
            raise FullWindowFinalizeError("raw ledger outcome→attempt 引用不闭合")
        measured = _validate_attempt_outcome_group(
            attempts[attempt_id], grouped
        )
        observed_lower += measured["observed_compressed_bytes_lower_bound"]
        observed_upper += measured["observed_compressed_bytes_upper_bound"]
        if measured["observed_compressed_bytes"] is None:
            observed_all_exact = False
        else:
            observed_exact += measured["observed_compressed_bytes"]
        if measured["has_complete"]:
            complete_ids.append(attempt_id)
        if measured["has_terminal"]:
            terminal_ids.append(attempt_id)
        if any(
            outcome.get("outcome") == "publication_failed_after_complete_single_pass"
            for outcome in grouped
        ):
            publication_failed_ids.append(attempt_id)
    if (
        initial_reserved is None
        or initial_observed is None
        or genesis_identity is None
        or preliminary_seed_read_bytes is None
        or seed_artifact_read_bytes is None
        or additional_pre_update_raw_read_bytes is None
    ):
        raise FullWindowFinalizeError("raw ledger 缺少唯一 genesis")
    for attempt in attempts.values():
        if (attempt.get("run_id"), attempt.get("bindings")) != genesis_identity:
            raise FullWindowFinalizeError("raw ledger attempt 与 genesis 身份不一致")
    consumed = tuple(receipt_complete_attempt_ids)
    consumed_set = set(consumed)
    complete_set = set(complete_ids)
    publication_failed_set = set(publication_failed_ids)
    if (
        len(consumed) != len(consumed_set)
        or not consumed_set <= complete_set
        or consumed_set & publication_failed_set
        or complete_set != consumed_set | publication_failed_set
    ):
        raise FullWindowFinalizeError(
            "parse complete 必须且只能由成功 receipt 或 publication-failed terminal 闭合"
        )
    unclosed = set(attempts) - consumed_set - set(terminal_ids)
    return {
        "attempt_count": len(attempts),
        "outcome_count": sum(len(grouped) for grouped in outcomes.values()),
        "initial_reserved_raw_bytes": initial_reserved,
        "preliminary_seed_read_bytes": preliminary_seed_read_bytes,
        "seed_artifact_read_bytes": seed_artifact_read_bytes,
        "additional_pre_update_raw_read_bytes": additional_pre_update_raw_read_bytes,
        "cumulative_reserved_raw_bytes_upper_bound": initial_reserved + reservation,
        "initial_observed_raw_bytes": initial_observed,
        "initial_unobserved_reserved_raw_bytes": initial_reserved - initial_observed,
        "observed_compressed_bytes_state": (
            "exact" if observed_all_exact else "bounded_after_process_termination"
        ),
        "observed_compressed_bytes_sum": (
            initial_observed + observed_exact if observed_all_exact else None
        ),
        "observed_compressed_bytes_lower_bound_sum": initial_observed + observed_lower,
        "observed_compressed_bytes_upper_bound_sum": initial_observed + observed_upper,
        "unclosed_attempt_count": len(unclosed),
        "complete_outcome_attempt_ids": tuple(sorted(complete_ids)),
        "publication_failed_attempt_ids": tuple(sorted(publication_failed_ids)),
        "terminal_failure_attempt_ids": tuple(sorted(terminal_ids)),
    }


_RECORD_OBSERVATION_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_id",
        "file_sha256",
        "collector_id",
        "artifact_slot_utc",
        "record_ordinal",
        "record_offset",
        "record_length",
        "raw_record_sha256",
        "event_time_utc",
        "record_kind",
        "announce_count",
        "withdraw_count",
        "update_peer_observations",
        "peer_session_observation",
        "semantics",
    }
)
_RECORD_OBSERVATION_KINDS = frozenset(
    {
        "update",
        "state_change",
        "open",
        "notification",
        "keepalive",
        "end_of_rib",
    }
)
_CONTROL_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "record_kind",
        "artifact_id",
        "file_sha256",
        "collector_id",
        "artifact_slot_utc",
        "record_ordinal",
        "record_offset",
        "record_length",
        "raw_record_sha256",
        "event_time_utc",
        "mrt_type",
        "mrt_subtype",
        "route_event_ids",
        "peer_session_observation",
        "control_record_semantics",
    }
)
_CONTROL_RECORD_KINDS = frozenset(
    {"state_change", "open", "notification", "keepalive", "end_of_rib"}
)


def _stream_control_record_shard(
    root: Path,
    ref: Mapping[str, Any],
    *,
    sequence: int,
    artifact: Mapping[str, Any],
    started_monotonic: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    """逐行核验 control shard 并立即释放，只返回 count/hash。"""

    slot_start = _utc(artifact.get("slot_start_utc"), "artifact.slot_start_utc")
    parsed_start = datetime.fromisoformat(slot_start[:-1] + "+00:00")
    parsed_end = parsed_start + timedelta(minutes=5)
    digest = hashlib.sha256()
    digest.update(CONTROL_RECORD_SHARD_SEMANTIC_SCHEMA.encode("ascii"))
    digest.update(b"\0")
    count = 0
    previous_ordinal = -1
    for row_index, row in enumerate(_iter_shard_rows(root, ref)):
        if row_index % 4096 == 0:
            _check_finalization_soft_stop(
                started_monotonic=started_monotonic,
                monotonic=monotonic,
                phase=f"receipt {sequence} control record 流式核验",
            )
        ordinal = row.get("record_ordinal")
        record_offset = row.get("record_offset")
        record_length = row.get("record_length")
        session = row.get("peer_session_observation")
        expected_semantics = (
            "control_observation_not_session_close_confirmation"
            if session is not None
            else "control_record_not_route_element_evidence"
        )
        if (
            set(row) != _CONTROL_RECORD_FIELDS
            or row.get("schema_version") != CONTROL_RECORD_SHARD_SCHEMA_VERSION
            or row.get("artifact_id") != artifact.get("artifact_id")
            or row.get("file_sha256") != artifact.get("file_sha256")
            or row.get("collector_id") != artifact.get("collector_id")
            or row.get("artifact_slot_utc") != slot_start
            or row.get("record_kind") not in _CONTROL_RECORD_KINDS
            or row.get("route_event_ids") != []
            or row.get("control_record_semantics") != expected_semantics
            or isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or ordinal <= previous_ordinal
            or isinstance(record_offset, bool)
            or not isinstance(record_offset, int)
            or record_offset < 0
            or isinstance(record_length, bool)
            or not isinstance(record_length, int)
            or record_length < 12
            or (session is not None and row.get("record_kind") != "state_change")
        ):
            raise FullWindowFinalizeError(
                f"receipt {sequence} control record 字段/身份/顺序不闭合"
            )
        _sha(row.get("raw_record_sha256"), "control record raw SHA")
        event_time = _utc_event(row.get("event_time_utc"), "control.event_time_utc")
        parsed_event = datetime.fromisoformat(event_time[:-1] + "+00:00")
        if not parsed_start <= parsed_event < parsed_end:
            raise FullWindowFinalizeError(
                f"receipt {sequence} control record event_time 越出当前半开槽"
            )
        _framed_semantic_update(digest, row)
        previous_ordinal = ordinal
        count += 1
    if count != ref.get("record_count"):
        raise FullWindowFinalizeError("control record count 与 shard ref 不一致")
    return {
        "schema_version": CONTROL_RECORD_SHARD_SEMANTIC_SCHEMA,
        "sequence": sequence,
        "path": ref.get("path"),
        "record_count": count,
        "semantic_sha256": digest.hexdigest(),
    }


def _control_record_stream_sha256(
    shard_summaries: Sequence[Mapping[str, Any]],
) -> str:
    digest = hashlib.sha256()
    digest.update(CONTROL_RECORD_STREAM_SEMANTIC_SCHEMA.encode("ascii"))
    digest.update(b"\0")
    for summary in shard_summaries:
        _framed_semantic_update(digest, summary)
    return digest.hexdigest()


def _consume_record_observation_shard(
    root: Path,
    ref: Mapping[str, Any],
    *,
    sequence: int,
    artifact: Mapping[str, Any],
    retained_route_rows: Sequence[Mapping[str, Any]],
    retained_raw_rows: Sequence[Mapping[str, Any]],
    capture_rows: bool,
    started_monotonic: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Tuple[Mapping[str, Any], Tuple[Mapping[str, Any], ...]]:
    """单次解压核验 observation；可选捕获当前槽行供同一进程立即派生。"""

    slot_start = _utc(artifact.get("slot_start_utc"), "artifact.slot_start_utc")
    parsed_start = datetime.fromisoformat(slot_start[:-1] + "+00:00")
    parsed_end = parsed_start + timedelta(minutes=5)
    artifact_id = artifact.get("artifact_id")
    file_sha = artifact.get("file_sha256")
    collector_id = artifact.get("collector_id")

    raw_by_ordinal: dict[int, list[Mapping[str, Any]]] = {}
    for raw in retained_raw_rows:
        ordinal = raw.get("record_ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            raise FullWindowFinalizeError("retained raw record ordinal 非法")
        raw_by_ordinal.setdefault(ordinal, []).append(raw)
    routes_by_ordinal: dict[int, list[Mapping[str, Any]]] = {}
    for route in retained_route_rows:
        ordinal = route.get("record_ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            raise FullWindowFinalizeError("retained RouteEvent record ordinal 非法")
        routes_by_ordinal.setdefault(ordinal, []).append(route)

    seen_retained_ordinals: set[int] = set()
    semantic_digest = hashlib.sha256()
    semantic_digest.update(RECORD_OBSERVATION_SHARD_SEMANTIC_SCHEMA.encode("ascii"))
    semantic_digest.update(b"\0")
    expected_offset = 0
    count = 0
    captured_rows: list[Mapping[str, Any]] = []
    for expected_ordinal, row in enumerate(_iter_shard_rows(root, ref)):
        if expected_ordinal % 4096 == 0:
            _check_finalization_soft_stop(
                started_monotonic=started_monotonic,
                monotonic=monotonic,
                phase=f"receipt {sequence} record observation 流式核验",
            )
        if set(row) != _RECORD_OBSERVATION_FIELDS:
            raise FullWindowFinalizeError("record observation 字段不闭合")
        record_offset = row.get("record_offset")
        record_length = row.get("record_length")
        announce_count = row.get("announce_count")
        withdraw_count = row.get("withdraw_count")
        if (
            row.get("schema_version") != RECORD_OBSERVATION_SHARD_SCHEMA_VERSION
            or row.get("artifact_id") != artifact_id
            or row.get("file_sha256") != file_sha
            or row.get("collector_id") != collector_id
            or row.get("artifact_slot_utc") != slot_start
            or row.get("record_ordinal") != expected_ordinal
            or row.get("record_kind") not in _RECORD_OBSERVATION_KINDS
            or row.get("semantics")
            != "complete_physical_record_observation_for_independent_slot_derivation"
            or isinstance(record_offset, bool)
            or not isinstance(record_offset, int)
            or record_offset != expected_offset
            or isinstance(record_length, bool)
            or not isinstance(record_length, int)
            or record_length < 12
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (announce_count, withdraw_count)
            )
        ):
            raise FullWindowFinalizeError(
                f"receipt {sequence} record observation 身份/顺序/计数不闭合"
            )
        if row.get("record_kind") != "update" and (announce_count or withdraw_count):
            raise FullWindowFinalizeError("非 UPDATE observation 不得携带路由元素计数")
        expected_offset += record_length
        _sha(row.get("raw_record_sha256"), "record observation raw SHA")
        canonical_time = _utc_event(
            row.get("event_time_utc"), "record observation.event_time_utc"
        )
        observed_time = datetime.fromisoformat(canonical_time[:-1] + "+00:00")
        if not parsed_start <= observed_time < parsed_end:
            raise FullWindowFinalizeError(
                f"receipt {sequence} record observation event_time 越出当前半开槽"
            )

        retained_raw = raw_by_ordinal.get(expected_ordinal, ())
        retained_routes = routes_by_ordinal.get(expected_ordinal, ())
        if retained_raw or retained_routes:
            if not retained_raw or not retained_routes:
                raise FullWindowFinalizeError(
                    "retained RouteEvent/raw 与 record observation 人口不闭合"
                )
            if row.get("record_kind") != "update":
                raise FullWindowFinalizeError(
                    "retained RouteEvent 只能闭合到 UPDATE record observation"
                )
            for raw in retained_raw:
                if (
                    raw.get("raw_record_sha256") != row.get("raw_record_sha256")
                    or raw.get("record_hash") != row.get("raw_record_sha256")
                    or raw.get("record_offset") != record_offset
                    or raw.get("record_length") != record_length
                ):
                    raise FullWindowFinalizeError(
                        "retained raw record ref 与 physical record observation 坐标/哈希不一致"
                    )
            if any(
                route.get("event_time_utc") != canonical_time
                for route in retained_routes
            ):
                raise FullWindowFinalizeError(
                    "retained RouteEvent 时间与 UPDATE record observation 不一致"
                )
            seen_retained_ordinals.add(expected_ordinal)

        _framed_semantic_update(semantic_digest, row)
        if capture_rows:
            captured_rows.append(dict(row))
        count += 1
    if count == 0:
        raise FullWindowFinalizeError("每个 UPDATE artifact 必须有 record observation")
    if seen_retained_ordinals != set(raw_by_ordinal) or seen_retained_ordinals != set(
        routes_by_ordinal
    ):
        raise FullWindowFinalizeError(
            "retained RouteEvent/raw 未全部闭合到 physical record observation"
        )
    if count != ref.get("record_count"):
        raise FullWindowFinalizeError("record observation count 与 shard ref 不一致")
    summary = {
        "schema_version": RECORD_OBSERVATION_SHARD_SEMANTIC_SCHEMA,
        "sequence": sequence,
        "path": ref.get("path"),
        "record_count": count,
        "semantic_sha256": semantic_digest.hexdigest(),
    }
    return summary, tuple(captured_rows)


def _stream_record_observation_shard(
    root: Path,
    ref: Mapping[str, Any],
    *,
    sequence: int,
    artifact: Mapping[str, Any],
    retained_route_rows: Sequence[Mapping[str, Any]],
    retained_raw_rows: Sequence[Mapping[str, Any]],
    started_monotonic: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    """兼容旧接口：逐行核验一个槽，只返回 count/hash。"""

    summary, _rows = _consume_record_observation_shard(
        root,
        ref,
        sequence=sequence,
        artifact=artifact,
        retained_route_rows=retained_route_rows,
        retained_raw_rows=retained_raw_rows,
        capture_rows=False,
        started_monotonic=started_monotonic,
        monotonic=monotonic,
    )
    return summary


def _read_record_observation_shard_once(
    root: Path,
    ref: Mapping[str, Any],
    *,
    sequence: int,
    artifact: Mapping[str, Any],
    retained_route_rows: Sequence[Mapping[str, Any]],
    retained_raw_rows: Sequence[Mapping[str, Any]],
    started_monotonic: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Tuple[Mapping[str, Any], Tuple[Mapping[str, Any], ...]]:
    """只解压一次并同时返回已校验的当前槽 observation。"""

    return _consume_record_observation_shard(
        root,
        ref,
        sequence=sequence,
        artifact=artifact,
        retained_route_rows=retained_route_rows,
        retained_raw_rows=retained_raw_rows,
        capture_rows=True,
        started_monotonic=started_monotonic,
        monotonic=monotonic,
    )


def _record_observation_stream_sha256(
    shard_summaries: Sequence[Mapping[str, Any]],
) -> str:
    digest = hashlib.sha256()
    digest.update(RECORD_OBSERVATION_STREAM_SEMANTIC_SCHEMA.encode("ascii"))
    digest.update(b"\0")
    for summary in shard_summaries:
        _framed_semantic_update(digest, summary)
    return digest.hexdigest()


def _collect_journal_data(
    root: Path,
    *,
    bindings: Mapping[str, str],
    started_monotonic: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> _JournalData:
    active_attempt = root / "raw-ledger/ACTIVE"
    if active_attempt.exists() or active_attempt.is_symlink():
        raise FullWindowFinalizeError(
            "journal 仍有 ACTIVE attempt；须先在 execution lease 内 reconcile，拒绝封包"
        )
    try:
        head = load_full_window_head(
            root,
            expected_bindings=bindings,
            recover_committed_successor=False,
        )
        frozen = frozen_journal_head(head)
    except (FullWindowJournalError, OSError) as error:
        raise FullWindowFinalizeError("journal 冻结头或 receipt ancestry 核验失败") from error
    if frozen["completed_artifact_count"] != frozen["total_artifacts"]:
        raise FullWindowFinalizeError("journal 尚未完成全部 artifact，拒绝派生封包")
    receipts = _journal_receipts(root, frozen["terminal_receipt_ref"])
    bindings_out: list[_ShardBinding] = []
    artifacts: list[Mapping[str, Any]] = []
    route_rows: list[Mapping[str, Any]] = []
    raw_rows: list[Mapping[str, Any]] = []
    seed_route_rows: list[Mapping[str, Any]] = []
    seed_raw_rows: list[Mapping[str, Any]] = []
    seed_bootstrap_rows: list[Mapping[str, Any]] = []
    control_record_count = 0
    control_record_shard_summaries: list[Mapping[str, Any]] = []
    record_observation_count = 0
    record_observation_shard_summaries: list[Mapping[str, Any]] = []
    parser_rows: list[Mapping[str, Any]] = []
    compatible_slots: list[Mapping[str, Any]] = []
    revised_slots: list[Mapping[str, Any]] = []
    max_worker = 0.0
    max_temp = 0
    database_writes = 0

    for _receipt_ref, receipt in receipts:
        sequence = _nonnegative(receipt.get("sequence"), "receipt.sequence")
        _check_finalization_soft_stop(
            started_monotonic=started_monotonic,
            monotonic=monotonic,
            phase=f"journal load receipt {sequence}",
        )
        artifact = receipt.get("committed_artifact")
        if sequence == 0:
            if artifact is not None:
                raise FullWindowFinalizeError("genesis receipt 不得含 committed artifact")
        else:
            if not isinstance(artifact, Mapping):
                raise FullWindowFinalizeError("非 genesis receipt 缺少 committed artifact")
            descriptor = dict(artifact)
            if descriptor.get("index") != sequence - 1:
                raise FullWindowFinalizeError("artifact index 与 receipt sequence 不一致")
            artifacts.append(descriptor)
            outcome_ref = receipt.get("outcome_ref")
            outcome = _read_hashed_json(root, outcome_ref, field="attempt outcome")
            proof = outcome.get("proof")
            if not isinstance(proof, Mapping):
                raise FullWindowFinalizeError("成功 receipt 缺少 single-pass proof")
            seconds = proof.get("process_seconds")
            peak = proof.get("peak_temporary_bytes")
            writes = proof.get("database_write_operations")
            if (
                isinstance(seconds, bool)
                or not isinstance(seconds, (int, float))
                or not math.isfinite(float(seconds))
                or float(seconds) <= 0
            ):
                raise FullWindowFinalizeError("single-pass process_seconds 非法")
            max_worker = max(max_worker, float(seconds))
            max_temp = max(max_temp, _nonnegative(peak, "peak_temporary_bytes"))
            database_writes += _nonnegative(writes, "database_write_operations")

        shards = receipt.get("shards")
        if not isinstance(shards, list):
            raise FullWindowFinalizeError("receipt.shards 必须是数组")
        kinds = [item.get("kind") for item in shards if isinstance(item, Mapping)]
        if len(kinds) != len(shards) or len(kinds) != len(set(kinds)):
            raise FullWindowFinalizeError("同一 receipt shard kind 必须唯一")
        expected_kinds = (
            {
                "seed_bootstrap_attestation",
                "seed_route_events",
                "seed_raw_record_refs",
            }
            if sequence == 0
            else {
                "route_events",
                "raw_record_refs",
                "control_records",
                "record_observations",
                "parser_attestations",
                "country_slots",
            }
        )
        if set(kinds) != expected_kinds:
            raise FullWindowFinalizeError(
                f"receipt {sequence} shard kinds 不闭合：{sorted(kinds)}"
            )
        receipt_route_rows: list[Mapping[str, Any]] = []
        receipt_raw_rows: list[Mapping[str, Any]] = []
        control_ref: Optional[Mapping[str, Any]] = None
        observation_ref: Optional[Mapping[str, Any]] = None
        for ref in sorted(shards, key=lambda item: (item["kind"], item["path"])):
            binding = _ShardBinding(sequence, None if artifact is None else dict(artifact), dict(ref))
            bindings_out.append(binding)
            kind = ref["kind"]
            if kind == "control_records":
                control_ref = dict(ref)
                continue
            if kind == "record_observations":
                observation_ref = dict(ref)
                continue
            rows = tuple(_iter_shard_rows(root, ref))
            if sequence > 0 and kind in {
                "route_events",
                "raw_record_refs",
            }:
                assert isinstance(artifact, Mapping)
                slot_start = _utc(artifact.get("slot_start_utc"), "artifact.slot_start_utc")
                parsed_start = datetime.fromisoformat(slot_start[:-1] + "+00:00")
                parsed_end = parsed_start + timedelta(minutes=5)
                for row in rows:
                    if (
                        row.get("artifact_id") != artifact.get("artifact_id")
                        or row.get("file_sha256") != artifact.get("file_sha256")
                        or row.get("artifact_slot_utc") != slot_start
                    ):
                        raise FullWindowFinalizeError(
                            f"receipt {sequence} {kind} 行未绑定当前 committed artifact"
                        )
                    event_time = row.get("event_time_utc")
                    if kind in {
                        "route_events",
                    }:
                        canonical_time = _utc_event(
                            event_time, f"{kind}.event_time_utc"
                        )
                        observed_time = datetime.fromisoformat(
                            canonical_time[:-1] + "+00:00"
                        )
                        if not parsed_start <= observed_time < parsed_end:
                            raise FullWindowFinalizeError(
                                f"receipt {sequence} {kind} event_time 越出当前半开槽"
                            )
            if kind in {"seed_route_events", "route_events"}:
                route_rows.extend(rows)
                if kind == "seed_route_events":
                    seed_route_rows.extend(rows)
                else:
                    receipt_route_rows.extend(rows)
            elif kind in {"seed_raw_record_refs", "raw_record_refs"}:
                raw_rows.extend(rows)
                if kind == "seed_raw_record_refs":
                    seed_raw_rows.extend(rows)
                else:
                    receipt_raw_rows.extend(rows)
            elif kind == "seed_bootstrap_attestation":
                if len(rows) != 1:
                    raise FullWindowFinalizeError(
                        "genesis 必须恰有一条 seed bootstrap attestation"
                    )
                seed_bootstrap_rows.extend(rows)
            elif kind == "parser_attestations":
                if len(rows) != 1:
                    raise FullWindowFinalizeError(
                        f"receipt {sequence} 必须恰有一个 parser attestation"
                    )
                parser_rows.extend(rows)
            elif kind == "country_slots":
                if len(rows) != 2 or artifact is None:
                    raise FullWindowFinalizeError("每个 UPDATE 必须恰有 compatible/revised 两条国家槽")
                by_view = {row.get("mapping_view"): row for row in rows}
                if set(by_view) != {"compatible", "revised"}:
                    raise FullWindowFinalizeError("country slot 缺少 compatible/revised 对照")
                compatible = dict(by_view["compatible"])
                revised = dict(by_view["revised"])
                for view, row in (("compatible", compatible), ("revised", revised)):
                    if (
                        row.get("schema_version") != COUNTRY_SLOT_SCHEMA_VERSION
                        or row.get("slot_start_utc") != artifact.get("slot_start_utc")
                        or row.get("slot_end_exclusive_utc") != artifact.get("slot_end_exclusive_utc")
                        or row.get("mapping_view") != view
                        or row.get("main_curve") is not (view == "compatible")
                    ):
                        raise FullWindowFinalizeError("country slot 与 artifact/view 身份不一致")
                    row["_source_shard_ref"] = dict(ref)
                    row["_source_receipt_sequence"] = sequence
                if compatible.get("update_counts") != revised.get("update_counts"):
                    raise FullWindowFinalizeError("compatible/revised 槽的原始 UPDATE 计数不一致")
                compatible_slots.append(compatible)
                revised_slots.append(revised)

        if sequence > 0:
            if (
                control_ref is None
                or observation_ref is None
                or not isinstance(artifact, Mapping)
            ):
                raise FullWindowFinalizeError(
                    f"receipt {sequence} 缺少 control/record observation shard"
                )
            control_summary = _stream_control_record_shard(
                root,
                control_ref,
                sequence=sequence,
                artifact=artifact,
                started_monotonic=started_monotonic,
                monotonic=monotonic,
            )
            control_record_shard_summaries.append(control_summary)
            control_record_count += int(control_summary["record_count"])
            observation_summary = _stream_record_observation_shard(
                root,
                observation_ref,
                sequence=sequence,
                artifact=artifact,
                retained_route_rows=receipt_route_rows,
                retained_raw_rows=receipt_raw_rows,
                started_monotonic=started_monotonic,
                monotonic=monotonic,
            )
            record_observation_shard_summaries.append(observation_summary)
            record_observation_count += int(observation_summary["record_count"])

    route_by_id: dict[str, Mapping[str, Any]] = {}
    for row in route_rows:
        if row.get("schema_version") != ROUTE_EVENT_SHARD_SCHEMA_VERSION:
            raise FullWindowFinalizeError("RouteEvent shard schema 不支持")
        route_id = row.get("route_event_id")
        if not isinstance(route_id, str) or route_id in route_by_id:
            raise FullWindowFinalizeError("RouteEvent ID 非法或重复")
        route_by_id[route_id] = row
    raw_by_id: dict[str, Mapping[str, Any]] = {}
    for row in raw_rows:
        if row.get("schema_version") != RAW_RECORD_REF_SHARD_SCHEMA_VERSION:
            raise FullWindowFinalizeError("raw record ref shard schema 不支持")
        raw_id = row.get("raw_record_ref_id")
        if not isinstance(raw_id, str) or raw_id in raw_by_id:
            raise FullWindowFinalizeError("raw record ref ID 非法或重复")
        raw_by_id[raw_id] = row
        route_id = row.get("route_event_id")
        if route_id not in route_by_id or route_by_id[route_id].get("raw_record_ref_id") != raw_id:
            raise FullWindowFinalizeError("RouteEvent→raw record ref 1:1 element 闭合失败")
    if set(route_by_id) != {row.get("route_event_id") for row in raw_rows}:
        raise FullWindowFinalizeError("存在未闭合到 raw record ref 的 RouteEvent")
    if len(parser_rows) != frozen["total_artifacts"]:
        raise FullWindowFinalizeError("parser attestation 没有逐 UPDATE artifact 闭合")
    for row in parser_rows:
        semantic = dict(row)
        fingerprint = semantic.pop("attestation_fingerprint_sha256", None)
        expected = hashlib.sha256(
            canonical_json(
                {
                    "schema": "parser_attestation_fingerprint_v1",
                    "attestation": semantic,
                }
            ).encode("utf-8")
        ).hexdigest()
        if (
            row.get("schema_version") != "parser_attestation_v1"
            or fingerprint != expected
            or row.get("binary_execution_policy")
            not in {"verified_in_process_source", "verified_open_fd_exec"}
        ):
            raise FullWindowFinalizeError("parser attestation 身份/执行策略未闭合")
    if len(seed_bootstrap_rows) != 1:
        raise FullWindowFinalizeError("journal 缺少唯一 seed bootstrap attestation")

    # seed 原件不在 UPDATE receipt 的 artifact 列表中；从已验证 raw refs 补齐。
    artifact_by_id = {str(row["artifact_id"]): dict(row) for row in artifacts}
    for raw in raw_rows:
        artifact_id = str(raw["artifact_id"])
        file_sha = _sha(raw.get("file_sha256"), "raw.file_sha256")
        expected_id = artifact_id_v1(file_sha)
        if artifact_id != expected_id:
            raise FullWindowFinalizeError("raw artifact_id 与 file SHA256 不一致")
        existing = artifact_by_id.get(artifact_id)
        if existing is None:
            artifact_by_id[artifact_id] = {
                "artifact_id": artifact_id,
                "file_sha256": file_sha,
                "collector_id": raw.get("collector_id", "rrc25"),
                "artifact_slot_utc": raw.get("artifact_slot_utc"),
                "artifact_role": "seed_state_evidence",
            }
        elif existing.get("file_sha256") != file_sha:
            raise FullWindowFinalizeError("同一 artifact_id 对应冲突 SHA256")

    receipt_complete_attempt_ids = []
    for _receipt_ref, receipt in receipts[1:]:
        outcome = _read_hashed_json(root, receipt.get("outcome_ref"), field="receipt outcome")
        attempt_id = outcome.get("attempt_id")
        if not isinstance(attempt_id, str):
            raise FullWindowFinalizeError("成功 receipt outcome 缺少 attempt_id")
        receipt_complete_attempt_ids.append(attempt_id)
    ledger_accounting = _raw_ledger_accounting(
        root, receipt_complete_attempt_ids=receipt_complete_attempt_ids
    )
    if (
        ledger_accounting["cumulative_reserved_raw_bytes_upper_bound"]
        != frozen["cumulative_reserved_raw_bytes"]
    ):
        raise FullWindowFinalizeError("完整 raw ledger reservation 与 frozen head 不一致")
    execution = {
        "database_write_operations": database_writes,
        # 进程被强制终止时无法可信知道 gzip 已读精确值；此时必须保留 null，
        # 并由显式下/上界披露，不能把 reservation 或上界伪装成实测值。
        "new_raw_bytes_read": ledger_accounting["observed_compressed_bytes_sum"],
        "new_raw_bytes_read_lower_bound": ledger_accounting[
            "observed_compressed_bytes_lower_bound_sum"
        ],
        "new_raw_bytes_read_upper_bound": ledger_accounting[
            "observed_compressed_bytes_upper_bound_sum"
        ],
        "new_raw_bytes_read_state": ledger_accounting[
            "observed_compressed_bytes_state"
        ],
        "peak_temporary_bytes": max_temp,
        "max_worker_seconds": max_worker,
        **ledger_accounting,
        "raw_accounting_semantics": (
            "reservation_is_nonrefundable_gate_observed_bytes_are_exact_or_explicit_interval"
        ),
        "finalization_reads_real_mrt": False,
    }
    return _JournalData(
        frozen_head=dict(frozen),
        terminal_scratch=dict(head.scratch),
        shard_bindings=tuple(bindings_out),
        compatible_slots=tuple(compatible_slots),
        revised_slots=tuple(revised_slots),
        route_rows=tuple(route_rows),
        raw_rows=tuple(raw_rows),
        seed_route_rows=tuple(seed_route_rows),
        seed_raw_rows=tuple(seed_raw_rows),
        control_record_count=control_record_count,
        control_record_semantic_sha256=_control_record_stream_sha256(
            control_record_shard_summaries
        ),
        record_observation_count=record_observation_count,
        record_observation_semantic_sha256=_record_observation_stream_sha256(
            record_observation_shard_summaries
        ),
        parser_attestations=tuple(parser_rows),
        seed_bootstrap_attestation=dict(seed_bootstrap_rows[0]),
        artifacts=tuple(artifact_by_id[key] for key in sorted(artifact_by_id)),
        execution=execution,
    )


def _sample_id(run_id: str, row: Mapping[str, Any]) -> str:
    identity = {
        "schema": "rrc25_full_window_analysis_sample_id_v1",
        "run_id": run_id,
        "snapshot_id": row.get("snapshot_id"),
        "mapping_view": row.get("mapping_view"),
        "slot_start_utc": row.get("slot_start_utc"),
        "slot_end_exclusive_utc": row.get("slot_end_exclusive_utc"),
    }
    return "sample_v1_" + _canonical_hash(identity)[:24]


def _metric_measure(
    *,
    sample_id: str,
    snapshot_id: str,
    value: Any,
    source_state: str,
    missing_reason: Optional[str],
) -> Mapping[str, Any]:
    if source_state in _NUMERIC_STATES:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FullWindowFinalizeError("carried-state 已观测指标必须是数值")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise FullWindowFinalizeError("carried-state 已观测指标必须非负有限")
        state = source_state
        if source_state != _PARTIAL_VP_STATE:
            state = "observed_zero" if numeric == 0 else "observed"
        reason = None if source_state != _PARTIAL_VP_STATE else missing_reason
        if source_state == _PARTIAL_VP_STATE and reason != (
            "peer_session_unavailable_route_state_carried_not_withdrawn"
        ):
            raise FullWindowFinalizeError("partial VP 指标缺少 carried-state 明示原因")
        return {
            "sample_id": sample_id,
            "snapshot_id": snapshot_id,
            "value": value,
            "value_state": state,
            "missing_reason": reason,
        }
    if source_state not in _UNKNOWN_STATES or value is not None:
        raise FullWindowFinalizeError("指标 value_state/value 组合非法")
    if not isinstance(missing_reason, str) or not missing_reason:
        raise FullWindowFinalizeError("unknown 指标必须提供 missing_reason")
    return {
        "sample_id": sample_id,
        "snapshot_id": snapshot_id,
        "value": None,
        "value_state": source_state,
        "missing_reason": missing_reason,
    }


def _set_measure(
    *,
    sample_id: str,
    snapshot_id: str,
    values: Any,
    source_state: str,
    missing_reason: Optional[str],
) -> Mapping[str, Any]:
    if source_state in _NUMERIC_STATES:
        if not isinstance(values, list) or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in values
        ):
            raise FullWindowFinalizeError("ASN 集合必须是正整数数组")
        normalized = sorted(set(values))
        if normalized != values:
            raise FullWindowFinalizeError("ASN 集合必须去重排序")
        state = (
            _PARTIAL_VP_STATE
            if source_state == _PARTIAL_VP_STATE
            else "observed" if values else "observed_empty"
        )
        return {
            "sample_id": sample_id,
            "snapshot_id": snapshot_id,
            "value": list(values),
            "value_state": state,
            "missing_reason": (
                missing_reason if state == _PARTIAL_VP_STATE else None
            ),
        }
    if source_state not in _UNKNOWN_STATES or values is not None:
        raise FullWindowFinalizeError("unknown ASN 集合不得补值")
    return {
        "sample_id": sample_id,
        "snapshot_id": snapshot_id,
        "value": None,
        "value_state": source_state,
        "missing_reason": missing_reason,
    }


def _analysis_sample(row: Mapping[str, Any], *, run_id: str) -> Mapping[str, Any]:
    if row.get("schema_version") != COUNTRY_SLOT_SCHEMA_VERSION:
        raise FullWindowFinalizeError("country slot schema 不支持")
    snapshot_id = row.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id.startswith("snapshot_v1_"):
        raise FullWindowFinalizeError("country slot snapshot_id 非法")
    sample_id = _sample_id(run_id, row)
    metrics = row.get("metrics")
    counts = row.get("update_counts")
    vp = row.get("vp_population")
    if not isinstance(metrics, Mapping) or not isinstance(counts, Mapping) or not isinstance(vp, Mapping):
        raise FullWindowFinalizeError("country slot 缺少 metrics/update_counts/vp_population")
    source_state = metrics.get("value_state")
    missing_reason = metrics.get("missing_reason")
    if source_state not in _NUMERIC_STATES | _UNKNOWN_STATES:
        raise FullWindowFinalizeError("country slot metrics.value_state 非法")
    if source_state == _PARTIAL_VP_STATE and (
        metrics.get("vp_coverage_state") != "partial"
        or vp.get("coverage_complete") is not False
        or vp.get("down_vp_route_semantics") != "carried_state_not_implicit_withdrawal"
    ):
        raise FullWindowFinalizeError("partial VP 槽没有明确 carried-state 非隐式撤回语义")
    if source_state == "observed" and (
        metrics.get("vp_coverage_state") != "complete"
        or vp.get("coverage_complete") is not True
    ):
        raise FullWindowFinalizeError("完整观测状态与 VP coverage 不一致")
    state_continuous = source_state != "unknown_state_gap"

    def metric(name: str) -> Mapping[str, Any]:
        return _metric_measure(
            sample_id=sample_id,
            snapshot_id=snapshot_id,
            value=metrics.get(name),
            source_state=str(source_state),
            missing_reason=missing_reason,
        )

    def observed_count(value: Any) -> Mapping[str, Any]:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise FullWindowFinalizeError("槽级 raw count 必须是非负整数")
        return {
            "sample_id": sample_id,
            "snapshot_id": snapshot_id,
            "value": value,
            "value_state": "observed_zero" if value == 0 else "observed",
            "missing_reason": None,
        }

    expected_count = vp.get("expected_count")
    observed_count_value = vp.get("observable_state_visible_count")
    metric_values = {
        "visible_asn_count": metric("visible_asn_count"),
        "damaged_asn_count": metric("damaged_asn_count"),
        "baseline_asn_count": _metric_measure(
            sample_id=sample_id,
            snapshot_id=snapshot_id,
            value=(None if row.get("baseline_asns") is None else len(row["baseline_asns"])),
            source_state=str(source_state),
            missing_reason=missing_reason,
        ),
        "visible_ipv4_prefix_count": metric("visible_ipv4_prefix_count"),
        "visible_ipv6_prefix_count": metric("visible_ipv6_prefix_count"),
        "visible_ipv4_address_union": metric("visible_ipv4_address_union"),
        "visible_ipv4_24_equivalent": metric("visible_ipv4_24_equivalent"),
        "visible_ipv6_48_equivalent": metric("visible_ipv6_48_equivalent"),
        "announce_count": observed_count(counts.get("retained_announce")),
        "withdraw_count": observed_count(counts.get("retained_withdraw")),
        "vp_expected_count": observed_count(expected_count),
        "vp_observed_count": observed_count(observed_count_value),
    }
    ratio = metric("damaged_asn_ratio")
    if source_state in _NUMERIC_STATES:
        numerator = metrics.get("damaged_asn_count")
        denominator = metrics.get("cohort_asn_count")
        if (
            isinstance(numerator, bool)
            or not isinstance(numerator, int)
            or isinstance(denominator, bool)
            or not isinstance(denominator, int)
            or denominator < 1
            or not math.isclose(
                float(ratio["value"]), numerator / denominator, rel_tol=0, abs_tol=1e-15
            )
        ):
            raise FullWindowFinalizeError("damaged ASN ratio 与同槽分子分母不一致")
        ratio = {
            **ratio,
            "numerator": {"sample_id": sample_id, "snapshot_id": snapshot_id, "value": numerator},
            "denominator": {"sample_id": sample_id, "snapshot_id": snapshot_id, "value": denominator},
        }
    else:
        ratio = {**ratio, "numerator": None, "denominator": None}
    metric_values["damaged_asn_ratio"] = ratio
    source_ref = row.get("_source_shard_ref")
    if not isinstance(source_ref, Mapping):
        raise FullWindowFinalizeError("country slot 缺少 ancestry shard 来源")
    source_package_ref_id = row.get("_source_package_ref_id")
    if source_package_ref_id is None:
        source_package_ref_id = "journal-ancestry/" + str(source_ref["path"])
    elif (
        not isinstance(source_package_ref_id, str)
        or PurePosixPath(source_package_ref_id).is_absolute()
        or not PurePosixPath(source_package_ref_id).parts
        or any(
            part in {"", ".", ".."}
            for part in PurePosixPath(source_package_ref_id).parts
        )
    ):
        raise FullWindowFinalizeError("country slot 包内 source ref 不是安全相对路径")
    sample = {
        "schema_version": ANALYSIS_SAMPLE_SCHEMA_VERSION,
        "sample_id": sample_id,
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "collector_id": "rrc25",
        "country_code": "IR",
        "cohort_view": row.get("mapping_view"),
        "slot": {
            "start": _utc(row.get("slot_start_utc"), "slot.start"),
            "end": _utc(row.get("slot_end_exclusive_utc"), "slot.end"),
            "boundary": "[start,end)",
            "granularity_seconds": 300,
        },
        "continuity_state": "continuous" if state_continuous else "unknown_after_gap",
        "metrics": metric_values,
        "asn_sets": {
            "visible": _set_measure(
                sample_id=sample_id,
                snapshot_id=snapshot_id,
                values=row.get("visible_asns"),
                source_state=str(source_state),
                missing_reason=missing_reason,
            ),
            "damaged": _set_measure(
                sample_id=sample_id,
                snapshot_id=snapshot_id,
                values=row.get("damaged_asns"),
                source_state=str(source_state),
                missing_reason=missing_reason,
            ),
            "baseline": _set_measure(
                sample_id=sample_id,
                snapshot_id=snapshot_id,
                values=row.get("baseline_asns"),
                source_state=str(source_state),
                missing_reason=missing_reason,
            ),
        },
        "source_refs": [
            {
                "ref_type": "immutable_package_state_shard",
                "ref_id": source_package_ref_id,
                "sha256": source_ref["sha256"],
            }
        ],
        "measurement_semantics": {
            "curve": "carried_route_state",
            "vp_coverage_state": metrics.get("vp_coverage_state"),
            "source_value_state": source_state,
            "down_vp_route_semantics": vp.get("down_vp_route_semantics"),
            "down_vp_ids": list(vp.get("down_vp_ids", ())),
            "unknown_vp_ids": list(vp.get("unknown_vp_ids", ())),
            "implicit_withdrawal_from_peer_state_change": False,
            "algorithm_numeric_policy": "carried_state_value_used_with_partial_coverage_disclosed",
            "update_count_scope": "retained_tracked_prefix_set_not_country_intent",
            "retained_announce_count": counts.get("retained_announce"),
            "retained_withdraw_count": counts.get("retained_withdraw"),
            "collector_total_announce_count": counts.get("announce"),
            "collector_total_withdraw_count": counts.get("withdraw"),
        },
    }
    if _SAMPLE_ID_RE.fullmatch(sample_id) is None:
        raise FullWindowFinalizeError("派生 sample_id 非法")
    return sample


def _algorithm_sample(sample: Mapping[str, Any]) -> Mapping[str, Any]:
    """为既有数值算法提供透明适配，不改变对外样本的 partial 标签。"""

    result = json.loads(canonical_json(sample))
    for name in ("visible_ipv4_address_union", "damaged_asn_ratio"):
        measure = result["metrics"][name]
        if measure.get("value_state") == _PARTIAL_VP_STATE:
            value = measure.get("value")
            measure["value_state"] = "observed_zero" if value == 0 else "observed"
            measure["missing_reason"] = None
    return result


def _contract_sample(sample: Mapping[str, Any]) -> Mapping[str, Any]:
    """生成严格 v1 投影；partial/collector-total 语义只存在配套 sidecar。"""

    result = json.loads(canonical_json(sample))
    result["schema_version"] = "country-outage-sample/v1"
    result.pop("measurement_semantics", None)
    for measure in result["metrics"].values():
        if measure.get("value_state") == _PARTIAL_VP_STATE:
            measure["value"] = None
            measure["value_state"] = "unknown_state_gap"
            measure["missing_reason"] = "partial_vp_coverage_carried_state_in_sidecar"
            if "numerator" in measure:
                measure["numerator"] = None
                measure["denominator"] = None
    for measure in result["asn_sets"].values():
        if measure.get("value_state") == _PARTIAL_VP_STATE:
            measure["value"] = None
            measure["value_state"] = "unknown_state_gap"
            measure["missing_reason"] = "partial_vp_coverage_carried_state_in_sidecar"
    for source_ref in result["source_refs"]:
        source_ref["ref_type"] = "state_shard"
    return result


def _sample_semantics_sidecar(sample: Mapping[str, Any]) -> Mapping[str, Any]:
    semantics = sample.get("measurement_semantics")
    if not isinstance(semantics, Mapping):
        raise FullWindowFinalizeError("完整窗口样本缺少 measurement semantics")
    return {
        "schema_version": "rrc25-full-window-sample-measurement-semantics/v1",
        "sample_id": sample["sample_id"],
        "snapshot_id": sample["snapshot_id"],
        "cohort_view": sample["cohort_view"],
        "slot": dict(sample["slot"]),
        "source_value_state": semantics["source_value_state"],
        "metric_value_states": {
            name: measure["value_state"] for name, measure in sample["metrics"].items()
        },
        "asn_set_value_states": {
            name: measure["value_state"] for name, measure in sample["asn_sets"].items()
        },
        "carried_metrics": json.loads(canonical_json(sample["metrics"])),
        "carried_asn_sets": json.loads(canonical_json(sample["asn_sets"])),
        "measurement_semantics": dict(semantics),
        "source_refs": [dict(row) for row in sample["source_refs"]],
    }


def _route_event_from_row(row: Mapping[str, Any]) -> ResearchRouteEvent:
    path_raw = row.get("as_path")
    if path_raw is None:
        path = None
    elif isinstance(path_raw, list):
        try:
            path = tuple(
                AsPathSegment(str(item["segment_type"]), tuple(item["asns"]))
                for item in path_raw
            )
        except (KeyError, TypeError, ValueError) as error:
            raise FullWindowFinalizeError("RouteEvent AS_PATH 无法恢复") from error
    else:
        raise FullWindowFinalizeError("RouteEvent as_path 类型非法")
    try:
        event = build_research_route_event(
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
                as_path=path,
                quality_flags=tuple(row.get("quality_flags", ())),
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise FullWindowFinalizeError("RouteEvent shard 记录无法恢复") from error
    if event.route_event_id != row.get("route_event_id") or event.vp_id != row.get("vp_id"):
        raise FullWindowFinalizeError("RouteEvent 稳定身份与 shard 内容不一致")
    return event


def _prefix_relation(
    row: Mapping[str, Any], route_index: Mapping[str, ResearchRouteEvent]
) -> PrefixOriginRelation:
    afi = row.get("afi")
    prefix = row.get("prefix")
    route_ids = row.get("route_event_ids")
    if afi not in {"ipv4", "ipv6"} or not isinstance(prefix, str) or not isinstance(route_ids, list):
        raise FullWindowFinalizeError("prefix relation 基本字段非法")
    observations = []
    ambiguous = set()
    candidates = set()
    for route_id in route_ids:
        event = route_index.get(str(route_id))
        if event is None:
            raise FullWindowFinalizeError("prefix relation 引用不存在的 RouteEvent")
        resolution = derive_origin_asns(event.as_path or ())
        if resolution.state != "resolved":
            ambiguous.add(event.vp_id)
            candidates.update(resolution.origins)
        observations.append(
            VpOriginObservation(
                vp_id=event.vp_id,
                origin_state=resolution.state,
                origins=resolution.origins,
                missing_reason=resolution.reason,
                route_event_id=event.route_event_id,
            )
        )
    origins = row.get("origin_asns")
    if not isinstance(origins, list) or any(
        isinstance(value, bool) or not isinstance(value, int) for value in origins
    ):
        raise FullWindowFinalizeError("prefix relation origin_asns 非法")
    return PrefixOriginRelation(
        afi=afi,
        prefix=prefix,
        vp_ids=tuple(sorted(set(row.get("vp_ids", ())))),
        observations=tuple(sorted(observations, key=lambda item: (item.vp_id, item.route_event_id))),
        origins=tuple(sorted(set(origins))),
        ambiguous_vp_ids=tuple(sorted(ambiguous)),
        candidate_origins=tuple(sorted(candidates)),
        moas=bool(row.get("moas")),
    )


def _cohort_from_slots(
    slots: Sequence[Mapping[str, Any]], mapping: CountryMappingView
) -> CountryCohort:
    if not slots:
        raise FullWindowFinalizeError("无法从空国家槽构造 cohort")
    baseline_asns = tuple(slots[0].get("baseline_asns") or ())
    if tuple(sorted(set(baseline_asns))) != baseline_asns:
        raise FullWindowFinalizeError("baseline_asns 必须去重排序")
    member_first_seen: dict[int, Optional[str]] = {int(asn): None for asn in baseline_asns}
    prefix_first_seen: dict[tuple[int, str, str], Optional[str]] = {}
    prefix_source_snapshot: dict[tuple[int, str, str], str] = {}
    issues: dict[tuple[str, str, str], CohortIssue] = {}
    covered = []

    for slot in slots:
        snapshot_id = str(slot["snapshot_id"])
        observed_at = str(slot["slot_end_exclusive_utc"])
        covered.append((snapshot_id, observed_at))
        for discovery in slot.get("dynamic_discoveries", ()):
            if not isinstance(discovery, Mapping):
                raise FullWindowFinalizeError("dynamic discovery 必须是对象")
            kind = discovery.get("kind")
            asn = discovery.get("asn")
            first_seen = discovery.get("first_seen_at")
            if isinstance(asn, bool) or not isinstance(asn, int) or asn <= 0:
                raise FullWindowFinalizeError("dynamic discovery ASN 非法")
            if not isinstance(first_seen, str):
                raise FullWindowFinalizeError("dynamic discovery 缺少 first_seen")
            if kind == "dynamic_asn":
                previous = member_first_seen.get(asn)
                if previous is None and asn in member_first_seen:
                    raise FullWindowFinalizeError("baseline ASN 不得再次声明为 dynamic")
                if previous is not None and previous != first_seen:
                    raise FullWindowFinalizeError("dynamic ASN first_seen 冲突")
                member_first_seen[asn] = first_seen
            elif kind == "dynamic_prefix":
                afi = discovery.get("afi")
                prefix = discovery.get("prefix")
                if afi not in {"ipv4", "ipv6"} or not isinstance(prefix, str):
                    raise FullWindowFinalizeError("dynamic prefix 坐标非法")
                key = (asn, afi, prefix)
                previous = prefix_first_seen.get(key)
                if previous is not None and previous != first_seen:
                    raise FullWindowFinalizeError("dynamic prefix first_seen 冲突")
                prefix_first_seen[key] = first_seen
                prefix_source_snapshot[key] = snapshot_id
            else:
                raise FullWindowFinalizeError("dynamic discovery kind 非法")
        for impact in slot.get("asn_impacts", ()):
            if not isinstance(impact, Mapping):
                raise FullWindowFinalizeError("asn impact 必须是对象")
            asn = impact.get("asn")
            if isinstance(asn, bool) or not isinstance(asn, int) or asn <= 0:
                raise FullWindowFinalizeError("asn impact ASN 非法")
            if asn not in member_first_seen:
                # 同槽 discovery 在 impacts 之前已处理；缺失说明 first_seen 血缘丢失。
                raise FullWindowFinalizeError("动态 ASN 缺少精确 first_seen discovery")
            for family in impact.get("address_families", ()):
                if not isinstance(family, Mapping) or family.get("afi") not in {"ipv4", "ipv6"}:
                    raise FullWindowFinalizeError("ASN address family 非法")
                for prefix in family.get("reference_prefixes") or ():
                    key = (asn, str(family["afi"]), str(prefix))
                    if key not in prefix_first_seen:
                        if impact.get("baseline_member") is not True:
                            raise FullWindowFinalizeError("动态 prefix 缺少精确 first_seen discovery")
                        prefix_first_seen[key] = None
                        prefix_source_snapshot[key] = str(slots[0]["snapshot_id"])
        for issue in slot.get("issues", ()):
            if not isinstance(issue, Mapping):
                raise FullWindowFinalizeError("country slot issue 必须是对象")
            reason = str(issue.get("reason") or "mapping_or_origin_unresolved")
            details = "{}:{}:{}".format(
                issue.get("prefix") or "unknown-prefix",
                issue.get("vp_id") or "unknown-vp",
                issue.get("route_event_id") or "unknown-route-event",
            )
            key = (snapshot_id, reason, details)
            issues[key] = CohortIssue(
                snapshot_id=snapshot_id,
                observed_at=observed_at,
                value_state="unknown_mapping",
                missing_reason=reason,
                details=details,
            )

    references = tuple(
        CohortPrefixReference(
            asn=asn,
            afi=afi,
            prefix=prefix,
            first_seen_at=first_seen,
            source_snapshot_id=prefix_source_snapshot[(asn, afi, prefix)],
            baseline_member=asn in set(baseline_asns),
        )
        for (asn, afi, prefix), first_seen in sorted(prefix_first_seen.items())
    )
    dynamic_asns = tuple(sorted(asn for asn, first_seen in member_first_seen.items() if first_seen is not None))
    return CountryCohort(
        country_code="IR",
        cohort_view=mapping.view,
        mapping_source_sha256=mapping.source_sha256,
        mapping_source_ref=mapping.source_ref,
        baseline_snapshot_id=str(slots[0]["snapshot_id"]),
        baseline_asns=baseline_asns,
        dynamic_asns=dynamic_asns,
        member_first_seen=tuple(sorted(member_first_seen.items())),
        prefix_references=references,
        covered_snapshots=tuple(covered),
        issues=tuple(issues[key] for key in sorted(issues)),
    )


def _internal_value_state(source_state: str, value: Any) -> str:
    if source_state in _NUMERIC_STATES:
        return "observed_zero" if value == 0 else "observed"
    return source_state


def _impact_from_slot(
    slot: Mapping[str, Any],
    route_index: Mapping[str, ResearchRouteEvent],
    *,
    partial_as_unknown: bool = False,
) -> CountrySnapshotImpact:
    snapshot_id = str(slot["snapshot_id"])
    metrics = slot.get("metrics")
    if not isinstance(metrics, Mapping):
        raise FullWindowFinalizeError("country slot metrics 缺失")
    source_state = str(metrics.get("value_state"))
    missing_reason = metrics.get("missing_reason")
    if partial_as_unknown and source_state == _PARTIAL_VP_STATE:
        source_state = "unknown_state_gap"
        missing_reason = "partial_vp_coverage_carried_state_in_sidecar"

    def measured(name: str) -> MeasuredValue:
        value = metrics.get(name)
        return MeasuredValue(
            snapshot_id,
            value,
            _internal_value_state(source_state, value),
            None if source_state in _NUMERIC_STATES else str(missing_reason),
        )

    if source_state in _NUMERIC_STATES:
        numerator = metrics.get("damaged_asn_count")
        denominator = metrics.get("cohort_asn_count")
        ratio_value = metrics.get("damaged_asn_ratio")
        ratio = SameSnapshotRatio(
            snapshot_id,
            int(numerator),
            int(denominator),
            float(ratio_value),
            "observed_zero" if ratio_value == 0 else "observed",
            None,
        )
        set_state = lambda values: "observed" if values else "observed_empty"
        visible_asns = tuple(slot.get("visible_asns") or ())
        damaged_asns = tuple(slot.get("damaged_asns") or ())
        baseline_asns = tuple(slot.get("baseline_asns") or ())
        visible_set = MeasuredAsnSet(snapshot_id, visible_asns, set_state(visible_asns), None)
        damaged_set = MeasuredAsnSet(snapshot_id, damaged_asns, set_state(damaged_asns), None)
        baseline_set = MeasuredAsnSet(snapshot_id, baseline_asns, set_state(baseline_asns), None)
    else:
        ratio = SameSnapshotRatio(snapshot_id, None, None, None, source_state, str(missing_reason))
        visible_set = damaged_set = baseline_set = MeasuredAsnSet(
            snapshot_id, None, source_state, str(missing_reason)
        )

    relation_values = tuple(
        _prefix_relation(row, route_index) for row in slot.get("prefix_relations", ())
    )
    moas_by_family_prefix = {
        (relation.afi, relation.prefix)
        for relation in relation_values
        if relation.moas
    }
    asn_impacts = []
    for raw_impact in slot.get("asn_impacts", ()):
        families = []
        for raw_family in raw_impact.get("address_families", ()):
            afi = str(raw_family.get("afi"))
            family_state = str(raw_family.get("value_state"))
            if partial_as_unknown and family_state == _PARTIAL_VP_STATE:
                family_state = "unknown_state_gap"
            family_internal = _internal_value_state(
                family_state, raw_family.get("lost_equivalent")
            )
            reason = None if family_state in _NUMERIC_STATES else str(raw_family.get("missing_reason") or missing_reason)
            refs = raw_family.get("reference_prefixes")
            visible = raw_family.get("visible_prefixes")
            lost = raw_family.get("lost_prefixes")
            families.append(
                AddressFamilyDamage(
                    afi=afi,
                    reference_prefixes=None if refs is None else tuple(refs),
                    visible_prefixes=None if visible is None else tuple(visible),
                    lost_prefixes=None if lost is None else tuple(lost),
                    lost_equivalent=raw_family.get("lost_equivalent"),
                    fully_invisible=raw_family.get("fully_invisible"),
                    value_state=family_internal,
                    missing_reason=reason,
                    moas_prefixes=(
                        None
                        if refs is None
                        else tuple(
                            sorted(
                                prefix
                                for prefix in refs
                                if (afi, prefix) in moas_by_family_prefix
                            )
                        )
                    ),
                )
            )
        if len(families) != 2 or {item.afi for item in families} != {"ipv4", "ipv6"}:
            raise FullWindowFinalizeError("ASN impact 必须恰含 IPv4/IPv6")
        asn_impacts.append(
            AsnDamage(
                asn=int(raw_impact["asn"]),
                baseline_member=bool(raw_impact.get("baseline_member")),
                dynamic_member=bool(raw_impact.get("dynamic_member")),
                visible=raw_impact.get("visible"),
                damaged=raw_impact.get("damaged"),
                address_families=tuple(sorted(families, key=lambda item: item.afi)),
                overall_classification=str(raw_impact.get("classification")),
                value_state=_internal_value_state(source_state, metrics.get("damaged_asn_count")),
                missing_reason=None if source_state in _NUMERIC_STATES else str(missing_reason),
            )
        )
    issues = tuple(
        CohortIssue(
            snapshot_id=snapshot_id,
            observed_at=str(slot["slot_end_exclusive_utc"]),
            value_state="unknown_mapping",
            missing_reason=str(row.get("reason") or "mapping_or_origin_unresolved"),
            details="{}:{}:{}".format(
                row.get("prefix") or "unknown-prefix",
                row.get("vp_id") or "unknown-vp",
                row.get("route_event_id") or "unknown-route-event",
            ),
        )
        for row in slot.get("issues", ())
    )
    return CountrySnapshotImpact(
        snapshot_id=snapshot_id,
        observed_at=str(slot["slot_end_exclusive_utc"]),
        country_code="IR",
        cohort_view=str(slot["mapping_view"]),
        continuity_state=CONTINUOUS if source_state in _NUMERIC_STATES else "unknown_after_gap",
        metrics=CountryMetrics(
            visible_asn_count=measured("visible_asn_count"),
            damaged_asn_count=measured("damaged_asn_count"),
            baseline_asn_count=MeasuredValue(
                snapshot_id,
                None if slot.get("baseline_asns") is None else len(slot["baseline_asns"]),
                _internal_value_state(
                    source_state,
                    None if slot.get("baseline_asns") is None else len(slot["baseline_asns"]),
                ),
                None if source_state in _NUMERIC_STATES else str(missing_reason),
            ),
            visible_ipv4_prefix_count=measured("visible_ipv4_prefix_count"),
            visible_ipv6_prefix_count=measured("visible_ipv6_prefix_count"),
            visible_ipv4_address_union=measured("visible_ipv4_address_union"),
            visible_ipv4_24_equivalent=measured("visible_ipv4_24_equivalent"),
            visible_ipv6_48_equivalent=measured("visible_ipv6_48_equivalent"),
            damaged_asn_ratio=ratio,
        ),
        visible_asns=visible_set,
        damaged_asns=damaged_set,
        baseline_asns=baseline_set,
        prefix_relations=relation_values,
        asn_impacts=tuple(sorted(asn_impacts, key=lambda item: item.asn)),
        issues=issues,
    )


def _baseline_and_detection(
    samples: Sequence[Mapping[str, Any]], profile: Mapping[str, Any]
) -> Tuple[NumericBaselineResult, Optional[DetectionResult], Tuple[Mapping[str, Any], ...]]:
    analytic = tuple(_algorithm_sample(sample) for sample in samples)
    observations = tuple(
        BaselineObservation(
            sample_id=str(sample["sample_id"]),
            snapshot_id=str(sample["snapshot_id"]),
            slot_start_utc=str(sample["slot"]["start"]),
            slot_end_exclusive_utc=str(sample["slot"]["end"]),
            continuity_state=str(sample["continuity_state"]),
            value=sample["metrics"]["visible_ipv4_address_union"]["value"],
            value_state=str(sample["metrics"]["visible_ipv4_address_union"]["value_state"]),
            missing_reason=sample["metrics"]["visible_ipv4_address_union"].get("missing_reason"),
        )
        for sample in analytic
    )
    baseline = derive_numeric_baseline(
        observations,
        candidate_start_utc=str(profile["window"]["start_utc"]),
        numeric_policy=profile["baseline"]["numeric"],
        normal_band_policy=profile["baseline"]["normal_band"],
    )
    if not baseline.resolved:
        return baseline, None, analytic
    assert baseline.median is not None and baseline.mad is not None
    detection = detect_country_outage_episodes(
        analytic,
        episode=profile["algorithms"]["episode"],
        recovery=profile["algorithms"]["recovery"],
        wave=profile["algorithms"]["wave"],
        baseline={
            "median": baseline.median,
            "mad": baseline.mad,
            "normal_band": profile["baseline"]["normal_band"],
        },
    )
    return baseline, detection, analytic


def _episode_records(
    detection: Optional[DetectionResult],
    *,
    policy: Mapping[str, Any],
    revised: bool,
) -> Tuple[Tuple[Mapping[str, Any], ...], Tuple[Mapping[str, Any], ...]]:
    if detection is None:
        return (), ()
    records = []
    for episode in detection.episodes:
        if revised:
            mapping = {
                "incident_ref": policy["incident_ref"],
                "relation": "no_correspondence",
                "causal": False,
                "evidence_sample_ids": [],
            }
        else:
            mapping = {
                "incident_ref": policy["incident_ref"],
                "relation": policy["relation"],
                "causal": False,
                "evidence_sample_ids": sorted(episode.supporting_sample_ids),
            }
        records.append(episode.to_contract_record((mapping,)))
    waves = tuple(item.to_contract_record() for item in detection.waves)
    return tuple(records), waves


def _episode_as_and_prefixes(
    *,
    detection: Optional[DetectionResult],
    samples: Sequence[Mapping[str, Any]],
    impacts: Sequence[CountrySnapshotImpact],
    cohort: CountryCohort,
    mapping: CountryMappingView,
    route_index: Mapping[str, ResearchRouteEvent],
) -> Tuple[
    Tuple[Mapping[str, Any], ...],
    Tuple[Mapping[str, Any], ...],
    Mapping[str, int],
    Tuple[Mapping[str, Any], ...],
]:
    if detection is None:
        return (), (), {}, ()
    samples_by_id = {str(item["sample_id"]): item for item in samples}
    impacts_by_sample_id = {
        str(sample["sample_id"]): impact for sample, impact in zip(samples, impacts)
    }
    records: list[Mapping[str, Any]] = []
    unattributed: dict[str, int] = {}
    for episode in detection.episodes:
        changes, diagnostics = _automatic_prefix_change_event_ids(
            episode=episode,
            cohort=cohort,
            route_events_by_id=route_index,
        )
        for reason, count in diagnostics.items():
            unattributed[reason] = unattributed.get(reason, 0) + count
        records.extend(
            build_episode_as_records(
                episode,
                samples_by_id,
                impacts_by_sample_id,
                cohort=cohort,
                mapping=mapping,
                route_events_by_id=route_index,
                prefix_change_event_ids=changes,
            )
        )
    prefix_rows = []
    for record in records:
        link_by_prefix: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for link in record.get("evidence_links", ()):
            route = route_index.get(str(link.get("route_event_id")))
            if route is None:
                continue
            afi = "ipv4" if route.afi_safi == "ipv4_unicast" else "ipv6"
            link_by_prefix.setdefault((afi, route.prefix), []).append(dict(link))
        for afi, family in sorted(record.get("address_families", {}).items()):
            if not isinstance(family, Mapping):
                raise FullWindowFinalizeError("episode-as address family 非法")
            for membership_name in (
                "trigger_prefixes",
                "peak_prefixes",
                "cumulative_prefixes",
                "observation_end_prefixes",
            ):
                membership = family.get(membership_name)
                if not isinstance(membership, Mapping):
                    raise FullWindowFinalizeError("episode-as prefix set 缺失")
                values = membership.get("value")
                if values is None:
                    continue
                for prefix in values:
                    prefix_rows.append(
                        {
                            "schema_version": "rrc25-full-window-episode-prefix-impact/v1",
                            "episode_id": record["episode_id"],
                            "episode_as_id": record["episode_as_id"],
                            "asn": record["asn"],
                            "afi": afi,
                            "prefix": prefix,
                            "membership": membership_name,
                            "value_state": membership.get("value_state"),
                            "evidence_links": sorted(
                                link_by_prefix.get((afi, prefix), ()),
                                key=lambda item: str(item.get("route_event_id")),
                            ),
                            "withdraw_origin_attribution": "not_inferred_without_origin",
                        }
                    )
    prefix_rows.sort(
        key=lambda item: (
            item["episode_id"],
            item["asn"],
            item["afi"],
            item["prefix"],
            item["membership"],
        )
    )
    sample_by_id = {str(item["sample_id"]): item for item in samples}
    support_by_episode = {
        episode.episode_id: tuple(episode.supporting_sample_ids)
        for episode in detection.episodes
    }
    semantics_records = []
    episode_semantics: dict[str, Mapping[str, Any]] = {}
    for record in records:
        supporting = [
            sample_by_id[sample_id]
            for sample_id in support_by_episode.get(str(record["episode_id"]), ())
            if sample_id in sample_by_id
        ]
        partial = any(
            sample["metrics"]["visible_ipv4_address_union"]["value_state"]
            == _PARTIAL_VP_STATE
            for sample in supporting
        )
        semantics = {
            "curve": "carried_route_state",
            "vp_coverage_state": "partial" if partial else "complete",
            "source_value_state": _PARTIAL_VP_STATE if partial else "observed",
            "down_vp_route_semantics": "carried_state_not_implicit_withdrawal",
            "implicit_withdrawal_from_peer_state_change": False,
            "withdraw_origin_inferred": False,
        }
        episode_semantics[str(record["episode_id"])] = semantics
        semantics_records.append(
            {
                "schema_version": "rrc25-full-window-episode-as-measurement-semantics/v1",
                "episode_as_id": record["episode_as_id"],
                "episode_id": record["episode_id"],
                "asn": record["asn"],
                "measurement_semantics": semantics,
            }
        )
    decorated_prefixes = tuple(
        {
            **dict(row),
            "measurement_semantics": episode_semantics.get(
                str(row["episode_id"]),
                {
                    "curve": "carried_route_state",
                    "vp_coverage_state": "unknown",
                    "source_value_state": "unknown",
                    "down_vp_route_semantics": "carried_state_not_implicit_withdrawal",
                    "implicit_withdrawal_from_peer_state_change": False,
                    "withdraw_origin_inferred": False,
                },
            ),
        }
        for row in prefix_rows
    )
    return (
        tuple(records),
        decorated_prefixes,
        dict(sorted(unattributed.items())),
        tuple(semantics_records),
    )


def _quality_evidence_rows(
    journal: _JournalData,
) -> Tuple[Tuple[Mapping[str, Any], ...], Tuple[Mapping[str, Any], ...]]:
    routes = tuple(
        {
            **dict(row),
            "raw_closure_state": "verified_raw_audit",
        }
        for row in journal.route_rows
    )
    raw = tuple(
        {
            **dict(row),
            "raw_closure_state": "verified_raw_audit",
            "missing_reason_zh": None,
        }
        for row in journal.raw_rows
    )
    return routes, raw


def _validate_parser_attestations_against_artifacts(
    attestations: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
) -> None:
    """逐槽绑定 parser 配置/上限，并冻结全窗 parser/runtime 身份。"""

    if len(attestations) != len(artifacts) or not attestations:
        raise FullWindowFinalizeError("parser attestation 与 UPDATE artifact 数量不闭合")
    stable_identity: Optional[Mapping[str, Any]] = None
    identity_fields = (
        "parser_name",
        "parser_version",
        "parser_binary_sha256",
        "adapter_name",
        "adapter_version",
        "adapter_source_sha256",
        "binary_execution_policy",
        "security_boundary",
    )
    for index, (attestation, artifact) in enumerate(zip(attestations, artifacts)):
        configuration = attestation.get("configuration")
        limits = attestation.get("pilot_limits")
        if not isinstance(configuration, Mapping) or not isinstance(limits, Mapping):
            raise FullWindowFinalizeError(f"parser attestation {index} 缺少配置或上限")
        if _canonical_hash(dict(configuration)) != attestation.get(
            "configuration_sha256"
        ):
            raise FullWindowFinalizeError(f"parser attestation {index} configuration SHA 不闭合")
        slot_start = _utc(artifact.get("slot_start_utc"), "artifact.slot_start_utc")
        parsed_start = datetime.fromisoformat(slot_start[:-1] + "+00:00")
        slot_end = (parsed_start + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        size_bytes = _nonnegative(artifact.get("size_bytes"), "artifact.size_bytes")
        if size_bytes <= 0:
            raise FullWindowFinalizeError("UPDATE artifact size_bytes 必须为正")
        if (
            configuration.get("window_start_utc") != slot_start
            or configuration.get("window_end_exclusive_utc") != slot_end
            or limits.get("max_artifact_count") != 1
            or limits.get("max_compressed_bytes") != size_bytes
            or canonical_json(configuration.get("pilot_limits"))
            != canonical_json(dict(limits))
            or configuration.get("binary_execution_policy")
            != attestation.get("binary_execution_policy")
        ):
            raise FullWindowFinalizeError(
                f"parser attestation {index} 未与 committed artifact 槽/字节上限精确绑定"
            )
        required_limits = {
            "max_artifact_count",
            "max_compressed_bytes",
            "max_physical_records",
            "max_route_events",
            "max_spool_bytes",
        }
        if set(limits) != required_limits or any(
            isinstance(limits[name], bool)
            or not isinstance(limits[name], int)
            or limits[name] <= 0
            for name in required_limits
        ):
            raise FullWindowFinalizeError(f"parser attestation {index} pilot_limits 不闭合")
        identity = {field: attestation.get(field) for field in identity_fields}
        for field in ("parser_binary_sha256", "adapter_source_sha256"):
            _sha(identity[field], f"parser_attestation.{field}")
        if any(not isinstance(identity[field], str) or not identity[field] for field in identity_fields):
            raise FullWindowFinalizeError("parser/runtime 稳定身份字段非法")
        if stable_identity is None:
            stable_identity = identity
        elif identity != stable_identity:
            raise FullWindowFinalizeError("完整窗口 parser/source/runtime 身份发生漂移")


def _validate_independent_artifact_derivations(
    *,
    journal_root: Path,
    frozen_head: Mapping[str, Any],
    seed_bootstrap_attestation: Mapping[str, Any],
    compatible_mapping: CountryMappingView,
    revised_mapping: CountryMappingView,
    terminal_scratch: Optional[Mapping[str, Any]] = None,
    runtime_bootstrap_bytes_per_second: Optional[float] = None,
    started_monotonic: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    """从不可变 parse observations 逐槽重算两视图及 receipt state_ref。"""

    receipts = _journal_receipts(
        journal_root, frozen_head["terminal_receipt_ref"]
    )
    initial = seed_bootstrap_attestation.get("initial_compact_state")
    if not isinstance(initial, Mapping):
        raise FullWindowFinalizeError("独立逐槽复算缺少 seed initial compact state")
    runtime = (
        None if terminal_scratch is None else terminal_scratch.get("runtime_estimator")
    )
    if runtime_bootstrap_bytes_per_second is None:
        if not isinstance(runtime, Mapping):
            raise FullWindowFinalizeError("terminal scratch 缺少 runtime estimator")
        bootstrap = runtime.get("bootstrap_bytes_per_second")
    else:
        bootstrap = runtime_bootstrap_bytes_per_second
    if (
        isinstance(bootstrap, bool)
        or not isinstance(bootstrap, (int, float))
        or not math.isfinite(float(bootstrap))
        or float(bootstrap) <= 0
    ):
        raise FullWindowFinalizeError("runtime bootstrap throughput 非法")
    prior_compact: Mapping[str, Any] = dict(initial)
    minimum_observed: Optional[float] = None
    sample_count = 0
    final_scratch: Optional[Mapping[str, Any]] = None
    for receipt_ref, receipt in receipts[1:]:
        sequence = _nonnegative(receipt.get("sequence"), "receipt.sequence")
        _check_finalization_soft_stop(
            started_monotonic=started_monotonic,
            monotonic=monotonic,
            phase=f"独立逐槽复算 receipt {sequence}",
        )
        artifact_payload = receipt.get("committed_artifact")
        try:
            artifact = _journal_contract._artifact_from_dict(
                artifact_payload, "receipt.committed_artifact"
            )
        except FullWindowJournalError as error:
            raise FullWindowFinalizeError("独立逐槽复算 artifact 非法") from error
        refs = {
            ref.get("kind"): ref
            for ref in receipt.get("shards", ())
            if isinstance(ref, Mapping)
        }
        if not {"route_events", "record_observations", "country_slots"} <= set(refs):
            raise FullWindowFinalizeError("独立逐槽复算缺少不可变输入 shard")
        route_events = tuple(
            _route_event_from_row(row)
            for row in _iter_shard_rows(journal_root, refs["route_events"])
        )
        observations = tuple(
            _iter_shard_rows(journal_root, refs["record_observations"])
        )
        published_slots = tuple(
            _iter_shard_rows(journal_root, refs["country_slots"])
        )
        try:
            derived = derive_artifact_boundary(
                prior_compact,
                artifact,
                route_events,
                observations,
                compatible_mapping=compatible_mapping,
                revised_mapping=revised_mapping,
            )
        except (TypeError, ValueError) as error:
            raise FullWindowFinalizeError(
                f"receipt {sequence} 不可从不可变 parse observations 独立派生"
            ) from error
        _check_finalization_soft_stop(
            started_monotonic=started_monotonic,
            monotonic=monotonic,
            phase=f"独立逐槽复算 receipt {sequence}",
        )
        published_by_view = {
            row.get("mapping_view"): row for row in published_slots
        }
        if (
            set(published_by_view) != {"compatible", "revised"}
            or canonical_json(published_by_view["compatible"])
            != canonical_json(derived.compatible_country_slot)
            or canonical_json(published_by_view["revised"])
            != canonical_json(derived.revised_country_slot)
        ):
            raise FullWindowFinalizeError(
                f"receipt {sequence} country slots 与独立逐槽复算不一致"
            )

        outcome = _read_hashed_json(
            journal_root, receipt.get("outcome_ref"), field="derivation outcome"
        )
        proof = outcome.get("proof")
        if not isinstance(proof, Mapping):
            raise FullWindowFinalizeError("独立逐槽复算缺少 single-pass proof")
        seconds = proof.get("process_seconds")
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(float(seconds))
            or float(seconds) <= 0
        ):
            raise FullWindowFinalizeError("逐槽 proof process_seconds 非法")
        throughput = artifact.size_bytes / float(seconds)
        minimum_observed = (
            throughput
            if minimum_observed is None
            else min(minimum_observed, throughput)
        )
        sample_count += 1
        state_ref = receipt.get("state_ref")
        if not isinstance(state_ref, Mapping):
            raise FullWindowFinalizeError("receipt state_ref 非法")
        scratch_payload = _journal_contract._fingerprinted(
            _journal_contract.SCRATCH_SCHEMA_VERSION,
            {
                "run_id": frozen_head["run_id"],
                "bindings": frozen_head["bindings"],
                "sequence": sequence,
                "next_artifact_index": artifact.index + 1,
                "total_artifacts": frozen_head["total_artifacts"],
                "active_scratch_slot": state_ref.get("slot"),
                "compact_state": dict(derived.final_compact_state),
                "runtime_estimator": {
                    "bootstrap_bytes_per_second": float(bootstrap),
                    "minimum_observed_bytes_per_second": minimum_observed,
                    "sample_count": sample_count,
                },
                "shard_chain_sha256": receipt.get("shard_chain_sha256"),
            },
        )
        scratch_sha = _journal_contract.scratch_payload_sha256(scratch_payload)
        if scratch_sha != state_ref.get("sha256"):
            raise FullWindowFinalizeError(
                f"receipt {sequence} 后继 compact/runtime state_ref 未由独立复算闭合"
            )
        prior_compact = dict(derived.final_compact_state)
        final_scratch = scratch_payload

    if final_scratch is None:
        raise FullWindowFinalizeError("独立逐槽复算没有产生终态")
    if terminal_scratch is not None and canonical_json(final_scratch) != canonical_json(
        terminal_scratch
    ):
        raise FullWindowFinalizeError("独立逐槽复算终态与 terminal scratch 不一致")
    return {
        "schema_version": "rrc25-full-window-independent-derivation-verification/v1",
        "verified_artifact_count": sample_count,
        "input_basis": "seed_compact_plus_route_events_plus_complete_record_observations",
        "compatible_and_revised_country_slots_recomputed": True,
        "every_receipt_state_ref_recomputed": True,
        "runtime_bootstrap_bytes_per_second": float(bootstrap),
        "initial_compact_state_semantic_sha256": _semantic_value_sha256(
            "rrc25_full_window_initial_compact_state_v1", initial
        ),
        "terminal_compact_state_semantic_sha256": _semantic_value_sha256(
            "rrc25_full_window_terminal_compact_state_v1", prior_compact
        ),
    }


def derive_finalization_slot_once(
    *,
    journal_root: os.PathLike[str] | str,
    receipt_ref: Mapping[str, Any],
    prior_compact_state: Mapping[str, Any],
    runtime_estimator: Mapping[str, Any],
    compatible_mapping: CountryMappingView,
    revised_mapping: CountryMappingView,
    expected_bindings: Mapping[str, str],
    started_monotonic: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    """对一个已提交 UPDATE 槽做一次且仅一次的深验证和纯派生。

    该接口是可恢复 finalization workspace 的最小读取边界。当前槽的
    ``record_observations`` 在物理记录、retained RouteEvent/raw 坐标闭合时
    被捕获，并直接交给 :func:`derive_artifact_boundary`；不会为了派生再次解压。
    ``control_records`` 同样只流式读取一次。返回值不保留 observation 全量行，
    只保留可封装的派生行、摘要和后继 compact/runtime 状态。
    """

    root = Path(journal_root)
    receipt = _read_hashed_json(root, receipt_ref, field="slot boundary receipt")
    sequence = _nonnegative(receipt.get("sequence"), "receipt.sequence")
    if sequence <= 0:
        raise FullWindowFinalizeError("逐槽 finalization 不接受 genesis receipt")
    previous_ref = receipt.get("previous_receipt_ref")
    previous = _read_hashed_json(
        root, previous_ref, field="slot previous boundary receipt"
    )
    try:
        receipt = _journal_contract._verify_fingerprint(
            receipt,
            _journal_contract.BOUNDARY_RECEIPT_SCHEMA_VERSION,
            "slot boundary receipt",
        )
        previous = _journal_contract._verify_fingerprint(
            previous,
            _journal_contract.BOUNDARY_RECEIPT_SCHEMA_VERSION,
            "slot previous boundary receipt",
        )
        bindings = _journal_contract._validate_bindings(expected_bindings)
        normalized_previous_ref = _journal_contract._closed_ref(
            receipt.get("previous_receipt_ref"), "previous_receipt_ref"
        )
        if (
            normalized_previous_ref != dict(previous_ref)
            or receipt.get("bindings") != bindings
            or previous.get("bindings") != bindings
            or receipt.get("run_id") != previous.get("run_id")
            or previous.get("sequence") != sequence - 1
            or receipt.get("next_artifact_index") != sequence
            or receipt.get("total_artifacts") != previous.get("total_artifacts")
            or receipt.get("raw_genesis_ref") != previous.get("raw_genesis_ref")
        ):
            raise FullWindowJournalError("slot receipt envelope 与前驱不闭合")
        artifact = _journal_contract._artifact_from_dict(
            receipt.get("committed_artifact"), "receipt.committed_artifact"
        )
        if artifact.index != sequence - 1:
            raise FullWindowJournalError("slot artifact index 与 sequence 不闭合")
        attempt_ref = _journal_contract._closed_ref(
            receipt.get("attempt_ref"), "attempt_ref"
        )
        attempt = _journal_contract._load_attempt(root, attempt_ref)
        if (
            attempt.get("run_id") != receipt.get("run_id")
            or attempt.get("bindings") != bindings
            or attempt.get("artifact") != artifact.to_dict()
            or attempt.get("base_receipt_ref") != dict(previous_ref)
            or attempt.get("reserved_raw_bytes") != artifact.size_bytes
        ):
            raise FullWindowJournalError("slot attempt 与 receipt 不闭合")
        outcome_ref = _journal_contract._closed_ref(
            receipt.get("outcome_ref"), "outcome_ref"
        )
        outcome = _journal_contract._load_outcome(root, outcome_ref)
        if (
            outcome.get("attempt_ref") != attempt_ref
            or outcome.get("attempt_id") != attempt.get("attempt_id")
            or outcome.get("outcome") != "complete_single_pass"
            or outcome.get("failure_reason") is not None
            or outcome.get("reservation_refunded_bytes") != 0
            or outcome.get("observed_compressed_bytes") != artifact.size_bytes
        ):
            raise FullWindowJournalError("slot outcome 与 receipt 不闭合")
        proof = _journal_contract._proof_from_dict(outcome.get("proof"))
        _journal_contract._verify_single_pass(proof, artifact)
    except FullWindowJournalError as error:
        raise FullWindowFinalizeError(
            f"receipt {sequence} 边界事务语义不闭合"
        ) from error

    shard_values = receipt.get("shards")
    if not isinstance(shard_values, list):
        raise FullWindowFinalizeError(f"receipt {sequence} shards 非法")
    refs = {
        str(ref.get("kind")): dict(ref)
        for ref in shard_values
        if isinstance(ref, Mapping)
    }
    expected_kinds = {
        "route_events",
        "raw_record_refs",
        "control_records",
        "record_observations",
        "parser_attestations",
        "country_slots",
    }
    if set(refs) != expected_kinds or len(refs) != len(shard_values):
        raise FullWindowFinalizeError(
            f"receipt {sequence} finalization shard kinds 不闭合"
        )
    normalized_refs = []
    for ref in shard_values:
        if not isinstance(ref, Mapping) or set(ref) != {
            "kind",
            "path",
            "sha256",
            "size_bytes",
            "record_count",
        }:
            raise FullWindowFinalizeError(
                f"receipt {sequence} shard ref 字段不闭合"
            )
        kind = ref.get("kind")
        digest = _sha(ref.get("sha256"), "shard.sha256")
        relative = ref.get("path")
        if not isinstance(kind, str) or _SAFE_CODE_RE.fullmatch(kind) is None:
            raise FullWindowFinalizeError("slot shard kind 非法")
        if not isinstance(relative, str):
            raise FullWindowFinalizeError("slot shard path 非法")
        pure = PurePosixPath(relative)
        expected_path = f"shards/{kind}/slot-{artifact.index:04d}-{digest}.jsonl.gz"
        if (
            pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or relative != expected_path
        ):
            raise FullWindowFinalizeError(
                "slot shard path 未绑定 kind/artifact index/SHA256"
            )
        _nonnegative(ref.get("size_bytes"), "shard.size_bytes")
        _nonnegative(ref.get("record_count"), "shard.record_count")
        normalized_refs.append(dict(ref))
    if normalized_refs != sorted(
        normalized_refs, key=lambda row: (row["kind"], row["path"])
    ) or receipt.get("shard_chain_sha256") != _journal_contract._advance_chain(
        str(previous.get("shard_chain_sha256")), artifact, normalized_refs
    ):
        raise FullWindowFinalizeError(
            f"receipt {sequence} shard 顺序/hash chain 不闭合"
        )

    route_rows = tuple(_iter_shard_rows(root, refs["route_events"]))
    raw_rows = tuple(_iter_shard_rows(root, refs["raw_record_refs"]))
    parser_rows = tuple(_iter_shard_rows(root, refs["parser_attestations"]))
    published_slots = tuple(_iter_shard_rows(root, refs["country_slots"]))
    if len(parser_rows) != 1:
        raise FullWindowFinalizeError(
            f"receipt {sequence} parser attestation 数量不闭合"
        )
    parser_semantic = dict(parser_rows[0])
    parser_fingerprint = parser_semantic.pop(
        "attestation_fingerprint_sha256", None
    )
    expected_parser_fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": "parser_attestation_fingerprint_v1",
                "attestation": parser_semantic,
            }
        ).encode("utf-8")
    ).hexdigest()
    if (
        parser_rows[0].get("schema_version") != "parser_attestation_v1"
        or parser_fingerprint != expected_parser_fingerprint
        or parser_rows[0].get("binary_execution_policy")
        not in {"verified_in_process_source", "verified_open_fd_exec"}
    ):
        raise FullWindowFinalizeError(
            f"receipt {sequence} parser attestation 身份不闭合"
        )

    route_by_id: dict[str, Mapping[str, Any]] = {}
    for row in route_rows:
        if row.get("schema_version") != ROUTE_EVENT_SHARD_SCHEMA_VERSION:
            raise FullWindowFinalizeError("RouteEvent shard schema 不支持")
        route_id = row.get("route_event_id")
        if not isinstance(route_id, str) or route_id in route_by_id:
            raise FullWindowFinalizeError("RouteEvent ID 非法或重复")
        route_by_id[route_id] = row
    raw_by_route_id: dict[str, Mapping[str, Any]] = {}
    for row in raw_rows:
        if row.get("schema_version") != RAW_RECORD_REF_SHARD_SCHEMA_VERSION:
            raise FullWindowFinalizeError("raw record ref shard schema 不支持")
        route_id = row.get("route_event_id")
        raw_id = row.get("raw_record_ref_id")
        if (
            not isinstance(route_id, str)
            or not isinstance(raw_id, str)
            or route_id in raw_by_route_id
            or route_id not in route_by_id
            or route_by_id[route_id].get("raw_record_ref_id") != raw_id
        ):
            raise FullWindowFinalizeError("RouteEvent/raw record ref 1:1 闭合失败")
        raw_by_route_id[route_id] = row
    if set(raw_by_route_id) != set(route_by_id):
        raise FullWindowFinalizeError("存在未闭合到 raw record ref 的 RouteEvent")

    control_summary = _stream_control_record_shard(
        root,
        refs["control_records"],
        sequence=sequence,
        artifact=artifact.to_dict(),
        started_monotonic=started_monotonic,
        monotonic=monotonic,
    )
    observation_summary, observations = _read_record_observation_shard_once(
        root,
        refs["record_observations"],
        sequence=sequence,
        artifact=artifact.to_dict(),
        retained_route_rows=route_rows,
        retained_raw_rows=raw_rows,
        started_monotonic=started_monotonic,
        monotonic=monotonic,
    )
    route_events = tuple(_route_event_from_row(row) for row in route_rows)
    try:
        derived = derive_artifact_boundary(
            prior_compact_state,
            artifact,
            route_events,
            observations,
            compatible_mapping=compatible_mapping,
            revised_mapping=revised_mapping,
        )
    except (TypeError, ValueError) as error:
        raise FullWindowFinalizeError(
            f"receipt {sequence} 不可从单次读取的 observations 派生"
        ) from error

    published_by_view = {row.get("mapping_view"): row for row in published_slots}
    if (
        len(published_slots) != 2
        or set(published_by_view) != {"compatible", "revised"}
        or canonical_json(published_by_view["compatible"])
        != canonical_json(derived.compatible_country_slot)
        or canonical_json(published_by_view["revised"])
        != canonical_json(derived.revised_country_slot)
    ):
        raise FullWindowFinalizeError(
            f"receipt {sequence} country slots 与单次读取派生不一致"
        )

    bootstrap = runtime_estimator.get("bootstrap_bytes_per_second")
    minimum_observed = runtime_estimator.get("minimum_observed_bytes_per_second")
    sample_count = runtime_estimator.get("sample_count")
    if (
        isinstance(bootstrap, bool)
        or not isinstance(bootstrap, (int, float))
        or not math.isfinite(float(bootstrap))
        or float(bootstrap) <= 0
        or isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or sample_count != sequence - 1
        or (
            minimum_observed is not None
            and (
                isinstance(minimum_observed, bool)
                or not isinstance(minimum_observed, (int, float))
                or not math.isfinite(float(minimum_observed))
                or float(minimum_observed) <= 0
            )
        )
    ):
        raise FullWindowFinalizeError(
            f"receipt {sequence} prior runtime estimator 不闭合"
        )
    # ``outcome`` 已在 receipt envelope 验证时从 create-only ledger 读取并验指纹；
    # 不为 runtime estimator 再做一次相同 I/O。
    proof = outcome.get("proof")
    seconds = None if not isinstance(proof, Mapping) else proof.get("process_seconds")
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, (int, float))
        or not math.isfinite(float(seconds))
        or float(seconds) <= 0
    ):
        raise FullWindowFinalizeError(
            f"receipt {sequence} single-pass process_seconds 非法"
        )
    throughput = artifact.size_bytes / float(seconds)
    next_minimum = (
        throughput
        if minimum_observed is None
        else min(float(minimum_observed), throughput)
    )
    next_runtime = {
        "bootstrap_bytes_per_second": float(bootstrap),
        "minimum_observed_bytes_per_second": next_minimum,
        "sample_count": sample_count + 1,
    }
    state_ref = receipt.get("state_ref")
    if not isinstance(state_ref, Mapping):
        raise FullWindowFinalizeError(f"receipt {sequence} state_ref 非法")
    scratch_payload = _journal_contract._fingerprinted(
        _journal_contract.SCRATCH_SCHEMA_VERSION,
        {
            "run_id": receipt.get("run_id"),
            "bindings": dict(expected_bindings),
            "sequence": sequence,
            "next_artifact_index": artifact.index + 1,
            "total_artifacts": receipt.get("total_artifacts"),
            "active_scratch_slot": state_ref.get("slot"),
            "compact_state": dict(derived.final_compact_state),
            "runtime_estimator": next_runtime,
            "shard_chain_sha256": receipt.get("shard_chain_sha256"),
        },
    )
    if _journal_contract.scratch_payload_sha256(scratch_payload) != state_ref.get(
        "sha256"
    ):
        raise FullWindowFinalizeError(
            f"receipt {sequence} state_ref 未由单次读取派生闭合"
        )

    shard_bytes = sum(
        _nonnegative(ref.get("size_bytes"), "shard.size_bytes")
        for ref in refs.values()
    )
    return {
        "schema_version": "rrc25-full-window-finalization-slot-derivation/v1",
        "sequence": sequence,
        "artifact": artifact.to_dict(),
        "journal_receipt_ref": dict(receipt_ref),
        "previous_journal_receipt_ref": dict(previous_ref),
        "journal_shard_chain_sha256": receipt.get("shard_chain_sha256"),
        "route_event_rows": [dict(row) for row in route_rows],
        "raw_record_ref_rows": [dict(row) for row in raw_rows],
        "parser_attestation": dict(parser_rows[0]),
        "country_slots": [
            dict(published_by_view["compatible"]),
            dict(published_by_view["revised"]),
        ],
        "control_record_summary": dict(control_summary),
        "record_observation_summary": dict(observation_summary),
        "next_compact_state": dict(derived.final_compact_state),
        "next_runtime_estimator": next_runtime,
        "state_ref_sha256_verified": True,
        "resource_accounting": {
            "source_package_bytes_read": shard_bytes,
            "record_observation_compressed_bytes_read": _nonnegative(
                refs["record_observations"].get("size_bytes"),
                "record observation size_bytes",
            ),
            "database_write_operations": 0,
        },
    }


def verify_frozen_full_window_inputs(
    *,
    journal_root: os.PathLike[str] | str,
    profile: Mapping[str, Any],
    source_fact_snapshot: Mapping[str, Any],
    incident_policy: Mapping[str, Any],
    compatible_mapping_snapshot: Mapping[str, Any],
    revised_mapping_snapshot: Mapping[str, Any],
    code_identity: Mapping[str, Any],
    input_selection: Mapping[str, Any],
    claim_inventory: Mapping[str, Any],
    bindings: Mapping[str, Any],
    started_monotonic: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> _FinalizationInputs:
    """冻结全部纯派生输入，并核验它们与完整 journal 的四项绑定。"""

    try:
        normalized_profile = validate_complete_selection_against_profile(
            input_selection, profile
        )
        incident = load_frozen_incident_fact(source_fact_snapshot)
        compatible = mapping_view_from_frozen_snapshot(compatible_mapping_snapshot)
        revised = mapping_view_from_revised_snapshot(
            revised_mapping_snapshot, compatible_mapping_snapshot
        )
    except (TypeError, ValueError, KeyError) as error:
        raise FullWindowFinalizeError("Profile、旧事实或国家映射冻结输入非法") from error
    if set(bindings) != {
        "profile_sha256",
        "input_selection_sha256",
        "code_sha256",
        "mapping_sha256",
    }:
        raise FullWindowFinalizeError("bindings 必须精确包含 journal 四项 SHA256")
    normalized_bindings = {
        key: _sha(value, f"bindings.{key}") for key, value in sorted(bindings.items())
    }
    if profile_sha256(normalized_profile) != normalized_bindings["profile_sha256"]:
        raise FullWindowFinalizeError("Profile 与 journal binding 不一致")
    selection_sha = _selection_fingerprint(input_selection)
    if selection_sha != normalized_bindings["input_selection_sha256"]:
        raise FullWindowFinalizeError("input selection 与 journal binding 不一致")
    roles = input_selection.get("roles")
    updates = roles.get("analysis_updates") if isinstance(roles, Mapping) else None
    if not isinstance(updates, list) or not updates:
        raise FullWindowFinalizeError("selection 缺少 analysis_updates")
    compatible_mapping_sha = mapping_snapshot_sha256(compatible_mapping_snapshot)
    revised_mapping_sha = mapping_snapshot_sha256(revised_mapping_snapshot)
    mapping_sha = mapping_bundle_sha256(
        compatible_mapping_snapshot, revised_mapping_snapshot
    )
    if mapping_sha != normalized_bindings["mapping_sha256"]:
        raise FullWindowFinalizeError("compatible/revised mapping bundle 与 journal binding 不一致")
    normalized_code = _validate_code_identity(
        code_identity, normalized_bindings["code_sha256"]
    )
    normalized_policy = _validate_incident_policy(incident_policy, incident)
    if (
        claim_inventory.get("study_id") != normalized_profile["study_id"]
        or claim_inventory.get("incident_ref")
        != incident.incident.get("detail_reference")
        or not isinstance(claim_inventory.get("claims"), list)
        or not claim_inventory["claims"]
    ):
        raise FullWindowFinalizeError("claim inventory 与 Study/Incident 不一致")
    journal = _collect_journal_data(
        Path(journal_root),
        bindings=normalized_bindings,
        started_monotonic=started_monotonic,
        monotonic=monotonic,
    )
    _validate_seed_bootstrap_attestation(
        attestation=journal.seed_bootstrap_attestation,
        seed_route_rows=journal.seed_route_rows,
        seed_raw_rows=journal.seed_raw_rows,
        execution=journal.execution,
        bindings=normalized_bindings,
        code_identity=normalized_code,
        selection=input_selection,
        compatible_mapping=compatible,
        revised_mapping=revised,
    )
    if len(updates) != journal.frozen_head["total_artifacts"]:
        raise FullWindowFinalizeError("selection UPDATE 数与 journal total_artifacts 不一致")
    ordered_artifacts = []
    for sequence in range(1, journal.frozen_head["total_artifacts"] + 1):
        matches = [
            binding.artifact
            for binding in journal.shard_bindings
            if binding.sequence == sequence and binding.artifact is not None
        ]
        unique = {canonical_json(value): value for value in matches}
        if len(unique) != 1:
            raise FullWindowFinalizeError("journal ancestry 未唯一绑定逐槽 artifact")
        ordered_artifacts.append(next(iter(unique.values())))
    for index, (selected, artifact) in enumerate(zip(updates, ordered_artifacts)):
        expected = {
            "artifact_id": selected.get("artifact_id"),
            "file_sha256": selected.get("file_sha256"),
            "size_bytes": selected.get("size_bytes"),
            "collector_id": selected.get("collector_id"),
            "slot_start_utc": selected.get("artifact_time_utc"),
        }
        if any(artifact.get(key) != value for key, value in expected.items()):
            raise FullWindowFinalizeError(f"journal artifact {index} 与 selection 不一致")
    _validate_parser_attestations_against_artifacts(
        journal.parser_attestations, ordered_artifacts
    )
    independent_derivation = _validate_independent_artifact_derivations(
        journal_root=Path(journal_root),
        frozen_head=journal.frozen_head,
        seed_bootstrap_attestation=journal.seed_bootstrap_attestation,
        compatible_mapping=compatible,
        revised_mapping=revised,
        terminal_scratch=journal.terminal_scratch,
        started_monotonic=started_monotonic,
        monotonic=monotonic,
    )
    for view, slots, mapping in (
        ("compatible", journal.compatible_slots, compatible),
        ("revised", journal.revised_slots, revised),
    ):
        if len(slots) != len(updates):
            raise FullWindowFinalizeError(f"{view} 国家槽没有覆盖全部 UPDATE")
        for slot, selected in zip(slots, updates):
            if (
                slot.get("slot_start_utc") != selected.get("artifact_time_utc")
                or slot.get("mapping_source_sha256") != mapping.source_sha256
                or slot.get("mapping_source_ref") != mapping.source_ref
            ):
                raise FullWindowFinalizeError(f"{view} 国家槽与 selection/mapping 不一致")
    frozen_hashes = {
        "profile": normalized_bindings["profile_sha256"],
        "source-fact": _canonical_hash(dict(source_fact_snapshot)),
        "incident-policy": _canonical_hash(normalized_policy),
        "compatible-mapping": compatible_mapping_sha,
        "revised-mapping": revised_mapping_sha,
        "mapping-bundle": mapping_sha,
        "code-identity": normalized_bindings["code_sha256"],
        "input-selection": selection_sha,
        "claim-inventory": _canonical_hash(dict(claim_inventory)),
        "journal-head": _canonical_hash(journal.frozen_head),
    }
    return _FinalizationInputs(
        journal_root=Path(journal_root),
        profile=normalized_profile,
        source_fact_snapshot=dict(source_fact_snapshot),
        source_fact=incident,
        incident_policy=normalized_policy,
        compatible_mapping_snapshot=dict(compatible_mapping_snapshot),
        revised_mapping_snapshot=dict(revised_mapping_snapshot),
        compatible_mapping=compatible,
        revised_mapping=revised,
        code_identity=normalized_code,
        input_selection=dict(input_selection),
        claim_inventory=dict(claim_inventory),
        bindings=normalized_bindings,
        journal=journal,
        independent_derivation_verification=independent_derivation,
        frozen_hashes=frozen_hashes,
    )


def _select_primary_episode(
    episodes: Sequence[Mapping[str, Any]], samples: Sequence[Mapping[str, Any]]
) -> Optional[str]:
    if not episodes:
        return None
    sample_by_start = {str(row["slot"]["start"]): row for row in samples}
    ranked = []
    for episode in episodes:
        trough = sample_by_start[str(episode["trough_at"])]
        value = trough["metrics"]["visible_ipv4_address_union"]["value"]
        ranked.append((float("inf") if value is None else float(value), str(episode["onset_at"]), str(episode["episode_id"])))
    return min(ranked)[2]


def _build_reconciliation(
    *,
    inputs: _FinalizationInputs,
    baseline: NumericBaselineResult,
    episodes: Sequence[Mapping[str, Any]],
    waves: Sequence[Mapping[str, Any]],
    samples: Sequence[Mapping[str, Any]],
    algorithm_samples: Sequence[Mapping[str, Any]],
    episode_as_records: Sequence[Mapping[str, Any]],
    quality_routes: Sequence[Mapping[str, Any]],
    quality_raw: Sequence[Mapping[str, Any]],
    primary_episode_id: Optional[str],
) -> Mapping[str, Any]:
    registry = _evidence_registry(
        samples=samples,
        episodes=episodes,
        waves=waves,
        episode_as_records=episode_as_records,
        route_events=quality_routes,
        raw_refs=quality_raw,
    )
    derived = _derived_claim_values(
        primary_episode_id=primary_episode_id,
        baseline=baseline,
        episodes=episodes,
        samples=algorithm_samples,
        episode_as_records=episode_as_records,
    )
    assessments = _bind_assessments(
        claim_inventory=inputs.claim_inventory,
        supplied="auto",
        derived_values=derived,
        primary_episode_id=primary_episode_id,
    )
    assessments = _expand_assessment_refs(
        assessments,
        registry=registry,
        primary_episode_id=primary_episode_id,
        episodes=episodes,
        episode_as_records=episode_as_records,
    )
    return build_reconciliation_result(
        run_id=str(inputs.journal.frozen_head["run_id"]),
        claim_inventory=inputs.claim_inventory,
        assessments=assessments,
        evidence_registry=registry,
    )


def _build_quality(
    *,
    inputs: _FinalizationInputs,
    samples: Sequence[Mapping[str, Any]],
    episodes: Sequence[Mapping[str, Any]],
    waves: Sequence[Mapping[str, Any]],
    episode_as_records: Sequence[Mapping[str, Any]],
    quality_routes: Sequence[Mapping[str, Any]],
    quality_raw: Sequence[Mapping[str, Any]],
    semantic_core_sha256: str,
    prefix_unattributed: Mapping[str, int],
) -> Mapping[str, Any]:
    unknown_slots = sum(
        sample["metrics"]["visible_ipv4_address_union"]["value_state"]
        in _UNKNOWN_STATES
        for sample in samples
    )
    partial_slots = sum(
        sample["metrics"]["visible_ipv4_address_union"]["value_state"]
        == _PARTIAL_VP_STATE
        for sample in samples
    )
    issue_count = sum(len(slot.get("issues", ())) for slot in inputs.journal.compatible_slots)
    execution = inputs.journal.execution
    resource_ok = (
        execution["database_write_operations"] == 0
        and execution["cumulative_reserved_raw_bytes_upper_bound"]
        < 50_000_000_000
        and execution["unclosed_attempt_count"] == 0
        and execution["peak_temporary_bytes"] < 5_000_000_000
        and execution["max_worker_seconds"] < 600
    )
    facts = (
        DiagnosticFact("input_completeness", "full_window_selection_and_journal_complete", True, "冻结 selection 与 journal 已完整覆盖 Profile 全窗口。"),
        DiagnosticFact("parse_completeness", "parser_attestation_per_artifact_verified", True, "每个 UPDATE artifact 均有内容寻址 parser attestation。"),
        DiagnosticFact(
            "state_continuity",
            "carried_state_and_country_slots_independently_rederived",
            unknown_slots == 0,
            "已从 seed compact、不可变 RouteEvent 与完整 physical-record observations 逐槽重算双映射国家槽和每个 receipt 后继 state_ref；状态缺口不会被补零。",
        ),
        DiagnosticFact("vp_coverage", "vp_coverage_explicitly_disclosed", partial_slots == 0, "已逐槽核验 VP 覆盖；不完整覆盖保留 partial 标签且 peer down 不作为隐式撤回。", blocking=False),
        DiagnosticFact("mapping_coverage", "compatible_mapping_issues_checked", issue_count == 0, "compatible 主曲线的映射未决项已逐槽检查。"),
        DiagnosticFact("stable_identity", "content_addressed_identifiers_verified", True, "RouteEvent、raw record、artifact 与研究记录稳定身份已核验。"),
        DiagnosticFact("reference_closure", "journal_and_evidence_reference_closure_verified", True, "journal ancestry 及 RouteEvent 到 raw record 的引用已闭合。"),
        DiagnosticFact("missing_semantics", "unknown_and_partial_semantics_preserved", True, "unknown 与 partial 均保留显式状态和原因，没有伪装为零或完整观测。"),
        DiagnosticFact("resource_usage", "journal_resource_accounting_within_limits", resource_ok, "50GB 门按含 genesis 与全部重试的累计 reservation 上界核验；observed 仅作实测披露，且最终化不读取真实 MRT、不写数据库。"),
        DiagnosticFact("reproducibility", "external_second_directory_reproduction_pending", False, "尚未在第二个独立空目录完成复现；首包必须保持 reproduction_pending/not_accepted。", blocking=True),
    )
    violations = []
    if partial_slots:
        violations.append(
            DiagnosticViolation(
                "vp_coverage",
                "partial_vp_slots",
                f"共有 {partial_slots} 个样本为 partial VP coverage；逐 ASN/前缀结果继承 carried-state 限制，不能解释为全观测人口。",
                severity="warn",
                blocking=False,
            )
        )
    if prefix_unattributed:
        violations.append(
            DiagnosticViolation(
                "reference_closure",
                "withdraw_origin_not_inferred",
                "部分 WITHDRAW 没有可证明的 origin ASN；已保留未归因状态，没有按前序路径猜测归因。",
                severity="warn",
                blocking=False,
            )
        )
    evaluation = evaluate_research_quality(
        ResearchQualityInput(
            facts=facts,
            violations=tuple(violations),
            samples=tuple(samples),
            episodes=tuple(episodes),
            waves=tuple(waves),
            episode_as_records=tuple(episode_as_records),
            route_events=tuple(quality_routes),
            raw_refs=tuple(quality_raw),
            artifacts=tuple(inputs.journal.artifacts),
            execution=execution,
            # 首包不能用同一值复制两次冒充独立复现。最终 accepted 只由
            # reproduce 成功后另行发布的 create-only acceptance receipt 表达。
            semantic_fingerprints=(),
        )
    )
    return {
        "schema_version": "rrc25-full-window-quality-and-accounting/v1",
        "research_quality": evaluation.to_dict(),
        "run_state": evaluation.run_state,
        "acceptance_state": evaluation.acceptance_state,
        "vp_coverage_disclosure": {
            "partial_slot_count": partial_slots,
            "total_slot_count": len(samples),
            "published_value_state": _PARTIAL_VP_STATE,
            "down_vp_route_semantics": "carried_state_not_implicit_withdrawal",
            "implicit_withdrawal_from_peer_state_change": False,
        },
        "raw_accounting": dict(execution),
        "population_limitations": {
            "compatible_main_curve": True,
            "revised_view_is_non_overwriting_comparison": True,
            "withdraw_origin_inferred": False,
            "unattributed_prefix_change_counts": dict(sorted(prefix_unattributed.items())),
        },
        "external_reproduction": {
            "state": "reproduction_pending_not_accepted",
            "semantic_core_sha256": semantic_core_sha256,
        },
        "finalization_resource_gate": {
            "state": "pending_external_create_only_receipt_until_atomic_publish",
            "required_schema": "rrc25-full-window-finalization-resource-receipt/v1",
            "limits": {
                "soft_seconds": 540,
                "hard_seconds": 600,
                "maximum_staging_bytes_exclusive": 5_000_000_000,
                "database_write_operations": 0,
                "real_mrt_raw_bytes_read": 0,
            },
        },
    }


def _build_report(
    *,
    inputs: _FinalizationInputs,
    baseline: NumericBaselineResult,
    samples: Sequence[Mapping[str, Any]],
    episodes: Sequence[Mapping[str, Any]],
    waves: Sequence[Mapping[str, Any]],
    episode_as_records: Sequence[Mapping[str, Any]],
    reconciliation: Mapping[str, Any],
    quality: Mapping[str, Any],
    primary_episode_id: Optional[str],
) -> str:
    base = build_research_report_zh(
        profile=inputs.profile,
        run={
            "run_id": inputs.journal.frozen_head["run_id"],
            "incident_ref": inputs.source_fact.incident["detail_reference"],
            "execution_mode": "full_profile",
            "execution": dict(inputs.journal.execution),
            "acceptance_state": quality["acceptance_state"],
            "primary_episode_id": primary_episode_id,
        },
        input_selection=inputs.input_selection,
        mapping_summary=_mapping_summary(inputs.compatible_mapping),
        baseline=_baseline_report_record(baseline),
        samples=samples,
        episodes=episodes,
        waves=waves,
        episode_as_records=episode_as_records,
        reconciliation=reconciliation,
        quality=quality["research_quality"],
        reproduction_commands=(
            "python3 dev/data_quality/rrc25_iran_finalize.py verify-only --package-root <首次输出目录>",
            "python3 dev/data_quality/rrc25_iran_finalize.py reproduce --reference-package-root <首次输出目录> --output-root <第二个空目录> <同一组冻结输入参数>",
        ),
        source_temporal_evidence=_source_temporal_report_records((inputs.source_fact.incident,)),
    )
    disclosure = [
        "",
        "## 8. carried-state 与 VP 覆盖特别说明",
        "",
        f"主曲线中共有 {quality['vp_coverage_disclosure']['partial_slot_count']} 个槽保留 `{_PARTIAL_VP_STATE}`。",
        "peer session 变为 down 只表示该 VP 当前不可观测；已有路由状态继续按 carried-state 呈现，绝不生成隐式 WITHDRAW。",
        "样本 `announce_count`/`withdraw_count` 仅统计 retained tracked-prefix 集合；collector 全量 UPDATE 计数位于 sample measurement-semantics sidecar。两者都不证明 IR 或任何主体的主动意图。",
        "compatible 视图是主曲线；revised 视图只用于独立对照，不覆盖 compatible 结果。",
        "Incident→Episode 仅为可能对应关系，`causal=false`；当前不能据此确认前兆或根因。",
        "本研究的双目录复现范围仅为 `pure_derivation_from_same_frozen_journal`；按用户选择未重跑真实 MRT，`raw_replay_reproduction=not_performed_by_user_choice`，不得称为原始全链 A/B。",
        "",
    ]
    return base + "\n" + "\n".join(disclosure)


def _prepare_publication_staging(target: Path) -> Path:
    target = target.absolute()
    if target.name in {"", ".", ".."}:
        raise FullWindowFinalizeError("输出目标名称非法")
    parent = target.parent
    try:
        metadata = parent.lstat()
    except OSError as error:
        raise FullWindowFinalizeError("输出目标父目录不存在") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise FullWindowFinalizeError("输出目标父目录必须是非符号链接目录")
    if target.exists() or target.is_symlink():
        raise FileExistsError("最终包目标必须最初不存在，拒绝覆盖")
    staging = parent / f".{target.name}.rrc25-finalize-staging-{os.getpid()}-{secrets.token_hex(8)}"
    os.mkdir(staging, 0o750)
    return staging


def _fsync_and_make_tree_read_only(
    root: Path,
    *,
    started_monotonic: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    files = []
    directories = [root]
    for index, path in enumerate(root.rglob("*")):
        if index % 128 == 0:
            _check_finalization_soft_stop(
                started_monotonic=started_monotonic,
                monotonic=monotonic,
                phase="最终包 fsync inventory",
            )
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise FullWindowFinalizeError("staging 内不得出现符号链接")
        if stat.S_ISDIR(metadata.st_mode):
            directories.append(path)
        elif stat.S_ISREG(metadata.st_mode):
            files.append(path)
        else:
            raise FullWindowFinalizeError("staging 内只允许普通文件和目录")
    for index, path in enumerate(files):
        if index % 128 == 0:
            _check_finalization_soft_stop(
                started_monotonic=started_monotonic,
                monotonic=monotonic,
                phase="最终包文件 fsync",
            )
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o440)
        finally:
            os.close(descriptor)
    for index, path in enumerate(
        sorted(directories, key=lambda value: len(value.parts), reverse=True)
    ):
        if index % 128 == 0:
            _check_finalization_soft_stop(
                started_monotonic=started_monotonic,
                monotonic=monotonic,
                phase="最终包目录 fsync",
            )
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o550)
        finally:
            os.close(descriptor)


def _rename_directory_no_replace(source: Path, target: Path) -> None:
    """同一文件系统目录发布；优先使用内核 no-replace，父目录锁为兜底。"""

    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    target_bytes = os.fsencode(target)
    if hasattr(libc, "renamex_np"):
        result = libc.renamex_np(
            ctypes.c_char_p(source_bytes), ctypes.c_char_p(target_bytes), ctypes.c_uint(0x4)
        )
        if result == 0:
            return
        observed_errno = ctypes.get_errno()
        if observed_errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError("最终包目标在发布竞态中已存在")
        if observed_errno not in {errno.ENOSYS, errno.EINVAL}:
            raise OSError(observed_errno, os.strerror(observed_errno))
    if hasattr(libc, "renameat2"):
        result = libc.renameat2(
            ctypes.c_int(-100),
            ctypes.c_char_p(source_bytes),
            ctypes.c_int(-100),
            ctypes.c_char_p(target_bytes),
            ctypes.c_uint(1),
        )
        if result == 0:
            return
        observed_errno = ctypes.get_errno()
        if observed_errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError("最终包目标在发布竞态中已存在")
        if observed_errno not in {errno.ENOSYS, errno.EINVAL}:
            raise OSError(observed_errno, os.strerror(observed_errno))
    # 不支持内核 no-replace 的平台只在同一父目录排他锁内回退；再次检查目标，
    # 避免合作发布者竞态。这里绝不调用可覆盖已有非空目标的 shutil/mv。
    if target.exists() or target.is_symlink():
        raise FileExistsError("最终包目标在发布竞态中已存在")
    os.rename(source, target)


def _ensure_parent(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise FullWindowFinalizeError("输出路径不是安全相对路径")
    parent = root.joinpath(*pure.parts[:-1])
    parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    current = root
    for part in pure.parts[:-1]:
        current = current / part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise FullWindowFinalizeError("输出父路径被符号链接或普通文件替换")
    return root.joinpath(*pure.parts)


def _write_bytes_create_only(destination: Path, payload: bytes, *, kind: str) -> PublishedArtifact:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"输出已存在，拒绝覆盖：{destination}")
    temporary = destination.parent / f".{destination.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o440,
        )
        os.fchmod(descriptor, 0o440)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FullWindowFinalizeError("最终化文件写入未取得进展")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(temporary, destination, follow_symlinks=False)
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return PublishedArtifact(
        destination,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        1,
        kind,
    )


def _copy_verified_regular(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_size: Optional[int],
    record_count: int,
    kind: str,
    started_monotonic: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> PublishedArtifact:
    initial = source.lstat()
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise FullWindowFinalizeError("journal ancestry 源必须是普通文件")
    if expected_size is not None and initial.st_size != expected_size:
        raise FullWindowFinalizeError("journal ancestry 源 size 已变化")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("journal ancestry 目标已存在")
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    temporary = destination.parent / f".{destination.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    target_fd: Optional[int] = None
    digest = hashlib.sha256()
    total = 0
    try:
        before = os.fstat(source_fd)
        target_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
        os.fchmod(target_fd, 0o440)
        while True:
            _check_finalization_soft_stop(
                started_monotonic=started_monotonic,
                monotonic=monotonic,
                phase="journal ancestry 单文件复制",
            )
            block = os.read(source_fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            total += len(block)
            view = memoryview(block)
            while view:
                written = os.write(target_fd, view)
                if written <= 0:
                    raise FullWindowFinalizeError("journal ancestry 复制未取得进展")
                view = view[written:]
        after = os.fstat(source_fd)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in identity):
            raise FullWindowFinalizeError("journal ancestry 在复制期间发生变化")
        os.fsync(target_fd)
        os.close(target_fd)
        target_fd = None
        if digest.hexdigest() != expected_sha256 or (
            expected_size is not None and total != expected_size
        ):
            raise FullWindowFinalizeError("journal ancestry 复制 SHA256/size 不一致")
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        os.close(source_fd)
        if target_fd is not None:
            os.close(target_fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return PublishedArtifact(destination, expected_sha256, total, record_count, kind)


def _artifact_content_ref(root: Path, artifact: PublishedArtifact) -> Mapping[str, Any]:
    return {
        "kind": artifact.kind,
        "path": artifact.path.relative_to(root).as_posix(),
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
        "record_count": artifact.record_count,
    }


def _regular_tree_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise FullWindowFinalizeError("staging 中发现符号链接")
        if stat.S_ISREG(metadata.st_mode):
            total += metadata.st_size
    return total


def _retained_staging_inventory(target: Path) -> Tuple[int, Tuple[str, ...]]:
    """计入同目标此前失败留下的 staging；只读披露，绝不自动删除。"""

    pattern = f".{target.name}.rrc25-finalize-staging-*"
    total = 0
    paths = []
    for candidate in sorted(target.parent.glob(pattern)):
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise FullWindowFinalizeError("历史 staging 匹配项必须是非符号链接目录")
        total += _regular_tree_bytes(candidate)
        paths.append(str(candidate.resolve()))
    return total, tuple(paths)


def _preflight_staging_upper_bound(
    *,
    inputs: _FinalizationInputs,
    ancestry: Sequence[Mapping[str, Any]],
    object_files: Mapping[str, Tuple[str, Mapping[str, Any]]],
    sequence_files: Mapping[str, Tuple[str, Sequence[Mapping[str, Any]]]],
    byte_files: Mapping[str, Tuple[str, bytes]],
) -> int:
    ancestry_bytes = sum(
        row["size_bytes"]
        if row["size_bytes"] is not None
        else inputs.journal_root.joinpath(*PurePosixPath(row["path"]).parts).stat().st_size
        for row in ancestry
    )
    json_bytes = sum(
        len((canonical_json(dict(value)) + "\n").encode("utf-8"))
        for _kind, value in object_files.values()
    )
    raw_jsonl = sum(
        sum(len((canonical_json(dict(row)) + "\n").encode("utf-8")) for row in values)
        for _kind, values in sequence_files.values()
    )
    gzip_upper = raw_jsonl + raw_jsonl // 100 + 1024 * 1024 * max(1, len(sequence_files))
    direct_bytes = sum(len(payload) for _kind, payload in byte_files.values())
    # manifest/SHA256SUMS 的实际大小远小于 16 MiB；这里保守预留，避免在硬边界
    # 附近先建 staging 再失败。
    return ancestry_bytes + json_bytes + gzip_upper + direct_bytes + 16 * 1024 * 1024


def _journal_ancestry_refs(
    inputs: _FinalizationInputs,
    *,
    started_monotonic: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Tuple[Mapping[str, Any], ...]:
    refs: dict[str, Mapping[str, Any]] = {}
    for index, (receipt_ref, receipt) in enumerate(_journal_receipts(
        inputs.journal_root, inputs.journal.frozen_head["terminal_receipt_ref"]
    )):
        _check_finalization_soft_stop(
            started_monotonic=started_monotonic,
            monotonic=monotonic,
            phase=f"journal ancestry inventory receipt {index}",
        )
        refs[str(receipt_ref["path"])] = {
            "path": receipt_ref["path"],
            "sha256": receipt_ref["sha256"],
            "size_bytes": None,
            "record_count": 1,
            "kind": "journal-receipt",
        }
        outcome = receipt.get("outcome_ref")
        if isinstance(outcome, Mapping):
            refs[str(outcome["path"])] = {
                "path": outcome["path"],
                "sha256": outcome["sha256"],
                "size_bytes": None,
                "record_count": 1,
                "kind": "journal-outcome",
            }
        attempt = receipt.get("attempt_ref")
        if isinstance(attempt, Mapping):
            refs[str(attempt["path"])] = {
                "path": attempt["path"],
                "sha256": attempt["sha256"],
                "size_bytes": None,
                "record_count": 1,
                "kind": "journal-attempt",
            }
        genesis = receipt.get("raw_genesis_ref")
        if isinstance(genesis, Mapping):
            refs[str(genesis["path"])] = {
                "path": genesis["path"],
                "sha256": genesis["sha256"],
                "size_bytes": None,
                "record_count": 1,
                "kind": "journal-raw-genesis",
            }
        for shard in receipt.get("shards", ()):
            refs[str(shard["path"])] = {
                "path": shard["path"],
                "sha256": shard["sha256"],
                "size_bytes": shard["size_bytes"],
                "record_count": shard["record_count"],
                "kind": "journal-" + str(shard["kind"]),
            }
    # raw reservation 从不退款，因此 audit 必须封入完整 create-only raw ledger，
    # 不得只复制成功 receipt 指向的 attempt/outcome。失败重试和未闭合 attempt
    # 同样计入 cumulative_reserved_raw_bytes。
    ledger_root = inputs.journal_root / "raw-ledger"
    for index, path in enumerate(sorted(ledger_root.rglob("*"))):
        if index % 128 == 0:
            _check_finalization_soft_stop(
                started_monotonic=started_monotonic,
                monotonic=monotonic,
                phase="raw ledger inventory",
            )
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise FullWindowFinalizeError("raw ledger 不得包含符号链接")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise FullWindowFinalizeError("raw ledger 只能包含普通文件")
        relative = path.relative_to(inputs.journal_root).as_posix()
        raw = _read_stable_regular(path, maximum_bytes=32 * 1024 * 1024)
        digest = hashlib.sha256(raw).hexdigest()
        previous = refs.get(relative)
        if previous is not None and previous["sha256"] != digest:
            raise FullWindowFinalizeError("receipt 与 raw ledger inventory SHA256 冲突")
        refs[relative] = {
            "path": relative,
            "sha256": digest,
            "size_bytes": len(raw),
            "record_count": 1,
            "kind": previous["kind"] if previous is not None else "journal-raw-ledger",
        }
    return tuple(refs[key] for key in sorted(refs))


def _publish_package_under_target_lock(
    *,
    inputs: _FinalizationInputs,
    output_root: Path,
    object_files: Mapping[str, Tuple[str, Mapping[str, Any]]],
    sequence_files: Mapping[str, Tuple[str, Sequence[Mapping[str, Any]]]],
    byte_files: Mapping[str, Tuple[str, bytes]],
    acceptance_state: str,
    publication_hook: Optional[Callable[[str, Path], None]] = None,
    started_monotonic: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    target = output_root.absolute()
    ancestry = _journal_ancestry_refs(
        inputs,
        started_monotonic=started_monotonic,
        monotonic=monotonic,
    )
    retained_staging_bytes, _retained_staging_paths = _retained_staging_inventory(target)
    projected = _preflight_staging_upper_bound(
        inputs=inputs,
        ancestry=ancestry,
        object_files=object_files,
        sequence_files=sequence_files,
        byte_files=byte_files,
    )
    if retained_staging_bytes + projected >= DEFAULT_MAX_PACKAGE_SOURCE_BYTES:
        raise FullWindowFinalizeError("最终化 staging 保守上界达到或超过十进制 5 GB")
    staging = _prepare_publication_staging(target)
    output_root = staging
    refs = []
    staging_bytes = 0
    for relative, (kind, value) in sorted(object_files.items()):
        _check_finalization_soft_stop(
            started_monotonic=started_monotonic,
            monotonic=monotonic,
            phase="最终包对象写入",
        )
        path = _ensure_parent(output_root, relative)
        artifact = write_canonical_json(path, value, kind=kind, mode=0o440)
        refs.append(_artifact_content_ref(output_root, artifact))
        staging_bytes += artifact.size_bytes
        if retained_staging_bytes + staging_bytes >= DEFAULT_MAX_PACKAGE_SOURCE_BYTES:
            raise FullWindowFinalizeError("最终化 staging 实际字节达到或超过十进制 5 GB")
    if publication_hook is not None:
        publication_hook("after_content_publish", staging)
    for relative, (kind, values) in sorted(sequence_files.items()):
        _check_finalization_soft_stop(
            started_monotonic=started_monotonic,
            monotonic=monotonic,
            phase="最终包序列写入",
        )
        path = _ensure_parent(output_root, relative)
        artifact = write_canonical_jsonl_gzip(path, values, kind=kind, mode=0o440)
        refs.append(_artifact_content_ref(output_root, artifact))
        staging_bytes += artifact.size_bytes
        if retained_staging_bytes + staging_bytes >= DEFAULT_MAX_PACKAGE_SOURCE_BYTES:
            raise FullWindowFinalizeError("最终化 staging 实际字节达到或超过十进制 5 GB")
    for relative, (kind, payload) in sorted(byte_files.items()):
        _check_finalization_soft_stop(
            started_monotonic=started_monotonic,
            monotonic=monotonic,
            phase="最终包报告写入",
        )
        path = _ensure_parent(output_root, relative)
        artifact = _write_bytes_create_only(path, payload, kind=kind)
        refs.append(_artifact_content_ref(output_root, artifact))
        staging_bytes += artifact.size_bytes
        if retained_staging_bytes + staging_bytes >= DEFAULT_MAX_PACKAGE_SOURCE_BYTES:
            raise FullWindowFinalizeError("最终化 staging 实际字节达到或超过十进制 5 GB")
    source_total = sum(
        (row["size_bytes"] if row["size_bytes"] is not None else inputs.journal_root.joinpath(*PurePosixPath(row["path"]).parts).stat().st_size)
        for row in ancestry
    )
    if source_total >= DEFAULT_MAX_PACKAGE_SOURCE_BYTES:
        raise FullWindowFinalizeError("独立 journal ancestry 副本超过十进制 5 GB 安全上限")
    for row in ancestry:
        _check_finalization_soft_stop(
            started_monotonic=started_monotonic,
            monotonic=monotonic,
            phase="journal ancestry 复制",
        )
        relative = "journal-ancestry/" + str(row["path"])
        destination = _ensure_parent(output_root, relative)
        source = inputs.journal_root.joinpath(*PurePosixPath(str(row["path"])).parts)
        artifact = _copy_verified_regular(
            source,
            destination,
            expected_sha256=str(row["sha256"]),
            expected_size=row["size_bytes"],
            record_count=int(row["record_count"]),
            kind=str(row["kind"]),
            started_monotonic=started_monotonic,
            monotonic=monotonic,
        )
        refs.append(_artifact_content_ref(output_root, artifact))
        staging_bytes += artifact.size_bytes
        if retained_staging_bytes + staging_bytes >= DEFAULT_MAX_PACKAGE_SOURCE_BYTES:
            raise FullWindowFinalizeError("最终化 staging 实际字节达到或超过十进制 5 GB")
    if _regular_tree_bytes(staging) != staging_bytes:
        raise FullWindowFinalizeError("staging 增量字节账与实际 regular bytes 不一致")
    manifest = build_package_manifest(
        run_id=str(inputs.journal.frozen_head["run_id"]),
        study_id=str(inputs.profile["study_id"]),
        incident_ref=str(inputs.source_fact.incident["detail_reference"]),
        execution_mode="full_profile",
        acceptance_state=acceptance_state,
        bindings={**inputs.frozen_hashes, "journal-shard-chain": inputs.journal.frozen_head["shard_chain_sha256"]},
        contents=refs,
    )
    manifest_path, sums_path = publish_package_metadata(output_root, manifest)
    os.chmod(manifest_path, 0o440)
    os.chmod(sums_path, 0o440)
    staging_bytes += manifest_path.stat().st_size + sums_path.stat().st_size
    if retained_staging_bytes + staging_bytes >= DEFAULT_MAX_PACKAGE_SOURCE_BYTES:
        raise FullWindowFinalizeError("最终化 staging 含 metadata 后达到或超过十进制 5 GB")
    if _regular_tree_bytes(staging) != staging_bytes:
        raise FullWindowFinalizeError("最终 staging 增量字节账与实际 regular bytes 不一致")
    _check_finalization_soft_stop(
        started_monotonic=started_monotonic,
        monotonic=monotonic,
        phase="最终包发布前核验",
    )
    verify_published_package(output_root)
    _validate_published_contracts(output_root)
    _check_finalization_soft_stop(
        started_monotonic=started_monotonic,
        monotonic=monotonic,
        phase="最终包发布前核验",
    )
    if publication_hook is not None:
        publication_hook("after_staging_verify", staging)
    _fsync_and_make_tree_read_only(
        staging,
        started_monotonic=started_monotonic,
        monotonic=monotonic,
    )
    verify_published_package(staging)
    if publication_hook is not None:
        publication_hook("before_atomic_directory_publish", staging)
    _check_finalization_soft_stop(
        started_monotonic=started_monotonic,
        monotonic=monotonic,
        phase="最终包原子发布前",
    )
    lock_path = target.parent / ".rrc25-full-window-finalize.publish.lock"
    lock_fd = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        _rename_directory_no_replace(staging, target)
        parent_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    if publication_hook is not None:
        publication_hook("after_atomic_directory_publish", target)
    return verify_published_package(target)


def _publish_package(
    *,
    inputs: _FinalizationInputs,
    output_root: Path,
    object_files: Mapping[str, Tuple[str, Mapping[str, Any]]],
    sequence_files: Mapping[str, Tuple[str, Sequence[Mapping[str, Any]]]],
    byte_files: Mapping[str, Tuple[str, bytes]],
    acceptance_state: str,
    publication_hook: Optional[Callable[[str, Path], None]] = None,
    started_monotonic: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    """同目标单写者锁覆盖 inventory、写入、核验和 atomic rename。"""

    target = output_root.absolute()
    lock_path = target.parent / f".{target.name}.rrc25-finalize.target.lock"
    lock_fd = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return _publish_package_under_target_lock(
            inputs=inputs,
            output_root=target,
            object_files=object_files,
            sequence_files=sequence_files,
            byte_files=byte_files,
            acceptance_state=acceptance_state,
            publication_hook=publication_hook,
            started_monotonic=started_monotonic,
            monotonic=monotonic,
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _resource_receipt_default_path(package_root: Path) -> Path:
    return package_root.parent / f"{package_root.name}.finalization-resource-receipt.json"


def _validate_published_contracts(package_root: Path) -> None:
    """通过冻结仓库内 Ajv 入口实际校验 v1 输出与严格 sidecar。"""

    repository_root = Path(__file__).resolve().parents[4]
    validator = repository_root / "dev/data_quality/validate_rrc25_full_window_package_contracts.cjs"
    try:
        completed = subprocess.run(
            ["node", str(validator), "--package-root", str(package_root.absolute())],
            cwd=repository_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FullWindowFinalizeError("最终包 JSON Schema 校验器不可用或超时") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip()[-2000:] or completed.stdout.strip()[-2000:]
        raise FullWindowFinalizeError(f"最终包 JSON Schema 校验失败：{detail}")


def _publish_finalization_resource_receipt(
    *,
    package_root: Path,
    receipt_path: Path,
    started_monotonic: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    elapsed = monotonic() - started_monotonic
    output_bytes = _regular_tree_bytes(package_root)
    retained_staging_bytes, retained_staging_paths = _retained_staging_inventory(
        package_root
    )
    if elapsed >= 600:
        raise FullWindowFinalizeError("最终化总用时达到或超过 600 秒，资源 receipt 拒绝通过")
    if retained_staging_bytes + output_bytes >= DEFAULT_MAX_PACKAGE_SOURCE_BYTES:
        raise FullWindowFinalizeError("最终化 staging/output 达到或超过十进制 5 GB")
    manifest_raw = _read_stable_regular(
        package_root / "package-manifest.json", maximum_bytes=16 * 1024 * 1024
    )
    finalization = _load_json(package_root / "metadata/finalization.json")
    semantic = {
        "schema_version": "rrc25-full-window-finalization-resource-receipt/v1",
        "package_root": str(package_root.resolve()),
        "package_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "package_semantic_fingerprint_sha256": verify_published_package(package_root)[
            "semantic_fingerprint_sha256"
        ],
        "semantic_core_sha256": finalization["semantic_core_sha256"],
        "audit_run_sha256": finalization["audit_run_sha256"],
        "elapsed_seconds": round(elapsed, 6),
        "soft_stop_seconds": 540,
        "hard_stop_seconds": 600,
        "soft_stop_crossed": elapsed >= 540,
        "retained_existing_staging_bytes": retained_staging_bytes,
        "retained_existing_staging_paths": list(retained_staging_paths),
        "peak_staging_regular_bytes": retained_staging_bytes + output_bytes,
        "output_regular_bytes": output_bytes,
        "maximum_staging_bytes_exclusive": DEFAULT_MAX_PACKAGE_SOURCE_BYTES,
        "database_write_operations": 0,
        "real_mrt_raw_bytes_read": 0,
        "status": "pass",
    }
    receipt = {
        **semantic,
        "receipt_sha256": _canonical_hash(
            {"schema": "rrc25_full_window_finalization_resource_receipt_v1", "receipt": semantic}
        ),
    }
    receipt_path = receipt_path.absolute()
    try:
        parent = receipt_path.parent.lstat()
    except OSError as error:
        raise FullWindowFinalizeError("resource receipt 父目录不存在") from error
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise FullWindowFinalizeError("resource receipt 父目录非法")
    artifact = _write_bytes_create_only(
        receipt_path,
        (canonical_json(receipt) + "\n").encode("utf-8"),
        kind="finalization-resource-receipt",
    )
    os.chmod(artifact.path, 0o440)
    return receipt


def verify_finalization_resource_receipt(
    package_root: os.PathLike[str] | str,
    receipt_path: os.PathLike[str] | str,
) -> Mapping[str, Any]:
    package = Path(package_root).absolute()
    receipt = _load_json(receipt_path, maximum_bytes=4 * 1024 * 1024)
    semantic = dict(receipt)
    supplied = semantic.pop("receipt_sha256", None)
    if (
        receipt.get("schema_version")
        != "rrc25-full-window-finalization-resource-receipt/v1"
        or supplied
        != _canonical_hash(
            {"schema": "rrc25_full_window_finalization_resource_receipt_v1", "receipt": semantic}
        )
        or receipt.get("package_root") != str(package.resolve())
        or receipt.get("status") != "pass"
        or receipt.get("database_write_operations") != 0
        or receipt.get("real_mrt_raw_bytes_read") != 0
        or not isinstance(receipt.get("elapsed_seconds"), (int, float))
        or receipt["elapsed_seconds"] >= 600
        or not isinstance(receipt.get("peak_staging_regular_bytes"), int)
        or receipt["peak_staging_regular_bytes"] >= DEFAULT_MAX_PACKAGE_SOURCE_BYTES
        or not isinstance(receipt.get("retained_existing_staging_bytes"), int)
        or receipt.get("output_regular_bytes") != _regular_tree_bytes(package)
    ):
        raise FullWindowFinalizeError("finalization resource receipt 内容或资源边界非法")
    manifest_raw = _read_stable_regular(
        package / "package-manifest.json", maximum_bytes=16 * 1024 * 1024
    )
    manifest = verify_published_package(package)
    finalization = _load_json(package / "metadata/finalization.json")
    retained_bytes, retained_paths = _retained_staging_inventory(package)
    if (
        receipt.get("package_manifest_sha256") != hashlib.sha256(manifest_raw).hexdigest()
        or receipt.get("package_semantic_fingerprint_sha256")
        != manifest["semantic_fingerprint_sha256"]
        or receipt.get("semantic_core_sha256") != finalization["semantic_core_sha256"]
        or receipt.get("audit_run_sha256") != finalization["audit_run_sha256"]
        or receipt.get("retained_existing_staging_bytes") != retained_bytes
        or receipt.get("retained_existing_staging_paths") != list(retained_paths)
        or receipt.get("peak_staging_regular_bytes")
        != retained_bytes + receipt.get("output_regular_bytes")
    ):
        raise FullWindowFinalizeError("resource receipt 未绑定当前 package")
    return dict(receipt)


def derive_full_window_business_outputs(
    inputs: _FinalizationInputs,
    *,
    journal_ancestry_inventory: Sequence[Mapping[str, Any]],
) -> FullWindowBusinessOutputs:
    """从已验证内存输入纯派生完整业务产物。

    调用方负责在进入本函数前核验 journal/segment ancestry，并把清单作为值传入。
    本函数不读取 ``journal_root``、不重读 observation，也不创建或发布文件。
    """

    if not isinstance(inputs, _FinalizationInputs):
        raise FullWindowFinalizeError("inputs 必须是已验证 _FinalizationInputs")
    normalized_inventory = []
    observed_paths = set()
    expected_inventory_keys = {
        "path",
        "sha256",
        "size_bytes",
        "record_count",
        "kind",
    }
    for index, raw in enumerate(journal_ancestry_inventory):
        if not isinstance(raw, Mapping) or set(raw) != expected_inventory_keys:
            raise FullWindowFinalizeError(
                f"journal ancestry inventory {index} 字段非法"
            )
        path = raw.get("path")
        pure = PurePosixPath(path) if isinstance(path, str) else None
        if (
            pure is None
            or pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
            or path in observed_paths
        ):
            raise FullWindowFinalizeError(
                f"journal ancestry inventory {index} 路径非法或重复"
            )
        size = raw.get("size_bytes")
        if size is not None:
            size = _nonnegative(size, f"journal ancestry inventory {index}.size_bytes")
        record_count = _nonnegative(
            raw.get("record_count"),
            f"journal ancestry inventory {index}.record_count",
        )
        kind = raw.get("kind")
        if not isinstance(kind, str) or not kind:
            raise FullWindowFinalizeError(
                f"journal ancestry inventory {index}.kind 非法"
            )
        observed_paths.add(path)
        normalized_inventory.append(
            {
                "path": path,
                "sha256": _sha(
                    raw.get("sha256"),
                    f"journal ancestry inventory {index}.sha256",
                ),
                "size_bytes": size,
                "record_count": record_count,
                "kind": kind,
            }
        )
    if not normalized_inventory:
        raise FullWindowFinalizeError("journal ancestry inventory 不得为空")
    ancestry_inventory = tuple(
        sorted(normalized_inventory, key=lambda row: str(row["path"]))
    )
    run_id = str(inputs.journal.frozen_head["run_id"])
    compatible_internal_samples = tuple(
        _analysis_sample(row, run_id=run_id) for row in inputs.journal.compatible_slots
    )
    revised_internal_samples = tuple(
        _analysis_sample(row, run_id=run_id) for row in inputs.journal.revised_slots
    )
    # 对外 machine contract 继续是既有 additionalProperties=false 的 v1；新增的
    # partial-VP、carried-state 与 collector-total 语义只进入独立 sidecar。
    compatible_samples = tuple(_contract_sample(row) for row in compatible_internal_samples)
    revised_samples = tuple(_contract_sample(row) for row in revised_internal_samples)
    compatible_sample_semantics = tuple(
        _sample_semantics_sidecar(row) for row in compatible_internal_samples
    )
    revised_sample_semantics = tuple(
        _sample_semantics_sidecar(row) for row in revised_internal_samples
    )
    route_index = {
        str(row["route_event_id"]): _route_event_from_row(row)
        for row in inputs.journal.route_rows
    }
    compatible_cohort = _cohort_from_slots(
        inputs.journal.compatible_slots, inputs.compatible_mapping
    )
    revised_cohort = _cohort_from_slots(
        inputs.journal.revised_slots, inputs.revised_mapping
    )
    compatible_impacts = tuple(
        _impact_from_slot(row, route_index, partial_as_unknown=True)
        for row in inputs.journal.compatible_slots
    )
    revised_impacts = tuple(
        _impact_from_slot(row, route_index, partial_as_unknown=True)
        for row in inputs.journal.revised_slots
    )
    compatible_baseline, compatible_detection, compatible_algorithm_samples = _baseline_and_detection(
        compatible_internal_samples, inputs.profile
    )
    revised_baseline, revised_detection, revised_algorithm_samples = _baseline_and_detection(
        revised_internal_samples, inputs.profile
    )
    compatible_episodes, compatible_waves = _episode_records(
        compatible_detection, policy=inputs.incident_policy, revised=False
    )
    revised_episodes, revised_waves = _episode_records(
        revised_detection, policy=inputs.incident_policy, revised=True
    )
    (
        compatible_episode_as,
        compatible_prefixes,
        prefix_unattributed,
        compatible_episode_as_semantics,
    ) = _episode_as_and_prefixes(
        detection=compatible_detection,
        samples=compatible_internal_samples,
        impacts=compatible_impacts,
        cohort=compatible_cohort,
        mapping=inputs.compatible_mapping,
        route_index=route_index,
    )
    (
        revised_episode_as,
        revised_prefixes,
        revised_unattributed,
        revised_episode_as_semantics,
    ) = _episode_as_and_prefixes(
        detection=revised_detection,
        samples=revised_internal_samples,
        impacts=revised_impacts,
        cohort=revised_cohort,
        mapping=inputs.revised_mapping,
        route_index=route_index,
    )
    incident_mappings = _incident_episode_mapping_records(
        run_id=run_id,
        incidents=(inputs.source_fact.incident,),
        episodes=compatible_episodes,
    )
    limitations = tuple(
        sorted(
            set(inputs.source_fact.temporal_evidence.limitations_zh)
            | set(inputs.incident_policy["limitations_zh"])
            | {
                "RRC25 carried route-state 在 VP 覆盖不完整时只能描述部分观测，不能外推为全网人口。",
                "peer session down 不会被解释为隐式 WITHDRAW。",
                "WITHDRAW 不证明主动意图、物理断路、流量影响或政府意图。",
            }
        )
    )
    quality_routes, quality_raw = _quality_evidence_rows(inputs.journal)
    primary_episode_id = _select_primary_episode(
        compatible_episodes, compatible_algorithm_samples
    )
    reconciliation = _build_reconciliation(
        inputs=inputs,
        baseline=compatible_baseline,
        episodes=compatible_episodes,
        waves=compatible_waves,
        samples=compatible_samples,
        algorithm_samples=compatible_algorithm_samples,
        episode_as_records=compatible_episode_as,
        quality_routes=quality_routes,
        quality_raw=quality_raw,
        primary_episode_id=primary_episode_id,
    )
    stable_input_hashes = {
        key: value
        for key, value in inputs.frozen_hashes.items()
        if key != "journal-head"
    }
    artifact_descriptors = tuple(
        {
            key: row.get(key)
            for key in (
                "artifact_id",
                "artifact_type",
                "artifact_time_utc",
                "collector_id",
                "file_sha256",
                "size_bytes",
            )
        }
        for row in inputs.input_selection["roles"]["analysis_updates"]
    )
    business_stream_hashes = {
        "route_events": _canonical_hash(inputs.journal.route_rows),
        "raw_record_refs": _canonical_hash(inputs.journal.raw_rows),
        "control_records": inputs.journal.control_record_semantic_sha256,
        # physical-record 人口可能达到千万级；这里只保留逐槽流式计算的
        # 有域分隔语义链，不把所有 observation 常驻内存。
        "record_observations": inputs.journal.record_observation_semantic_sha256,
        "parser_attestations": _canonical_hash(inputs.journal.parser_attestations),
        "compatible_country_slots": _canonical_hash(
            tuple(
                {key: value for key, value in row.items() if not key.startswith("_source_")}
                for row in inputs.journal.compatible_slots
            )
        ),
        "revised_country_slots": _canonical_hash(
            tuple(
                {key: value for key, value in row.items() if not key.startswith("_source_")}
                for row in inputs.journal.revised_slots
            )
        ),
    }
    semantic_core = {
        "schema": SEMANTIC_CORE_FINGERPRINT_SCHEMA,
        "stable_input_hashes": stable_input_hashes,
        "artifact_descriptors_in_slot_order": artifact_descriptors,
        "business_stream_semantic_hashes": business_stream_hashes,
        "compatible": {
            "samples": compatible_samples,
            "sample_measurement_semantics": compatible_sample_semantics,
            "baseline": _baseline_report_record(compatible_baseline),
            "episodes": compatible_episodes,
            "waves": compatible_waves,
            "episode_as": compatible_episode_as,
            "episode_as_measurement_semantics": compatible_episode_as_semantics,
            "prefix_impacts": compatible_prefixes,
        },
        "revised": {
            "samples": revised_samples,
            "sample_measurement_semantics": revised_sample_semantics,
            "baseline": _baseline_report_record(revised_baseline),
            "episodes": revised_episodes,
            "waves": revised_waves,
            "episode_as": revised_episode_as,
            "episode_as_measurement_semantics": revised_episode_as_semantics,
            "prefix_impacts": revised_prefixes,
        },
        "incident_episode_mappings": incident_mappings,
        "reconciliation": reconciliation,
    }
    semantic_core_sha256 = _canonical_hash(semantic_core)
    audit_run_sha256 = _canonical_hash(
        {
            "schema": "rrc25_full_window_finalization_audit_run_v1",
            "frozen_journal_head": inputs.journal.frozen_head,
            "execution": inputs.journal.execution,
            "independent_derivation_verification": (
                inputs.independent_derivation_verification
            ),
            "journal_ancestry_refs": ancestry_inventory,
        }
    )
    all_unattributed = dict(prefix_unattributed)
    for reason, count in revised_unattributed.items():
        all_unattributed[reason] = all_unattributed.get(reason, 0) + count
    quality = _build_quality(
        inputs=inputs,
        samples=compatible_internal_samples,
        episodes=compatible_episodes,
        waves=compatible_waves,
        episode_as_records=compatible_episode_as,
        quality_routes=quality_routes,
        quality_raw=quality_raw,
        semantic_core_sha256=semantic_core_sha256,
        prefix_unattributed=all_unattributed,
    )
    report = _build_report(
        inputs=inputs,
        baseline=compatible_baseline,
        samples=compatible_samples,
        episodes=compatible_episodes,
        waves=compatible_waves,
        episode_as_records=compatible_episode_as,
        reconciliation=reconciliation,
        quality=quality,
        primary_episode_id=primary_episode_id,
    )
    partial_count = int(quality["vp_coverage_disclosure"]["partial_slot_count"])
    finalization = {
        "schema_version": FINALIZATION_SCHEMA_VERSION,
        "run_id": run_id,
        "semantic_core_sha256": semantic_core_sha256,
        "audit_run_sha256": audit_run_sha256,
        "frozen_journal_head": dict(inputs.journal.frozen_head),
        "independent_derivation_verification": dict(
            inputs.independent_derivation_verification
        ),
        "primary_episode_selection": {
            "episode_id": primary_episode_id,
            "policy": "minimum_visible_ipv4_address_union_then_earliest_onset_noncausal",
            "causal": False,
        },
        "counts": {
            "compatible_samples": len(compatible_samples),
            "compatible_episodes": len(compatible_episodes),
            "revised_samples": len(revised_samples),
            "revised_episodes": len(revised_episodes),
            "route_events": len(inputs.journal.route_rows),
            "raw_record_refs": len(inputs.journal.raw_rows),
            "control_records": inputs.journal.control_record_count,
            "record_observations": inputs.journal.record_observation_count,
            "partial_vp_slots": partial_count,
        },
        "record_observation_stream": {
            "schema_version": RECORD_OBSERVATION_STREAM_SEMANTIC_SCHEMA,
            "record_count": inputs.journal.record_observation_count,
            "semantic_sha256": inputs.journal.record_observation_semantic_sha256,
            "retention": "streamed_per_shard_not_materialized_in_finalizer_memory",
        },
        "control_record_stream": {
            "schema_version": CONTROL_RECORD_STREAM_SEMANTIC_SCHEMA,
            "record_count": inputs.journal.control_record_count,
            "semantic_sha256": inputs.journal.control_record_semantic_sha256,
            "retention": "streamed_per_shard_not_materialized_in_finalizer_memory",
        },
        "curve_policy": {
            "main": "compatible",
            "comparison": "revised_non_overwriting",
            "route_state": "carried_state",
            "down_vp_route_semantics": "carried_state_not_implicit_withdrawal",
        },
        "journal_state_policy": {
            "scratch_is_evidence": False,
            "scratch_copied_into_package": False,
            "receipt_state_ref_role": (
                "deterministic_digest_recomputed_from_immutable_parse_observations"
            ),
            "offline_verification_basis": (
                "seed_compact_route_events_complete_record_observations_receipts_and_raw_ledger"
            ),
        },
        "raw_ledger_inventory": {
            "file_count": sum(
                str(row["path"]).startswith("raw-ledger/")
                for row in ancestry_inventory
            ),
            "semantic_sha256": _canonical_hash(
                tuple(
                    {"path": row["path"], "sha256": row["sha256"]}
                    for row in ancestry_inventory
                    if str(row["path"]).startswith("raw-ledger/")
                )
            ),
            "reservation_semantics": "all_attempt_starts_no_refund_including_failures_and_retries",
        },
        "reproduction_state": "pending_independent_second_empty_directory",
        "acceptance_state": "not_accepted",
        "reproduction_scope": "pure_derivation_from_same_frozen_journal",
        "raw_replay_reproduction": "not_performed_by_user_choice",
    }
    object_files = {
        "metadata/finalization.json": ("finalization", finalization),
        "frozen/profile.json": ("profile", inputs.profile),
        "frozen/source-fact.json": ("source-fact", inputs.source_fact_snapshot),
        "frozen/incident-policy.json": ("incident-policy", inputs.incident_policy),
        "frozen/compatible-mapping.json": ("compatible-mapping", inputs.compatible_mapping_snapshot),
        "frozen/revised-mapping.json": ("revised-mapping", inputs.revised_mapping_snapshot),
        "frozen/code-identity.json": ("code-identity", inputs.code_identity),
        "frozen/input-selection.json": ("input-selection", inputs.input_selection),
        "frozen/claim-inventory.json": ("claim-inventory", inputs.claim_inventory),
        "frozen/bindings.json": ("bindings", inputs.bindings),
        "data/compatible-baseline.json": ("baseline", _baseline_report_record(compatible_baseline)),
        "data/revised-baseline.json": ("baseline", _baseline_report_record(revised_baseline)),
        "reconciliation.json": ("reconciliation", reconciliation),
        "quality-and-accounting.json": ("quality", quality),
    }
    sequence_files = {
        "data/compatible-country-samples.jsonl.gz": ("samples", compatible_samples),
        "data/revised-country-samples.jsonl.gz": ("samples", revised_samples),
        "data/compatible-sample-measurement-semantics.jsonl.gz": (
            "sample-measurement-semantics",
            compatible_sample_semantics,
        ),
        "data/revised-sample-measurement-semantics.jsonl.gz": (
            "sample-measurement-semantics",
            revised_sample_semantics,
        ),
        "data/compatible-episodes.jsonl.gz": ("episodes", compatible_episodes),
        "data/compatible-waves.jsonl.gz": ("waves", compatible_waves),
        "data/revised-episodes.jsonl.gz": ("episodes", revised_episodes),
        "data/revised-waves.jsonl.gz": ("waves", revised_waves),
        "data/compatible-episode-as.jsonl.gz": ("episode-as", compatible_episode_as),
        "data/compatible-episode-as-measurement-semantics.jsonl.gz": (
            "episode-as-measurement-semantics",
            compatible_episode_as_semantics,
        ),
        "data/compatible-prefix-impact.jsonl.gz": ("prefix-impact", compatible_prefixes),
        "data/revised-episode-as.jsonl.gz": ("episode-as", revised_episode_as),
        "data/revised-episode-as-measurement-semantics.jsonl.gz": (
            "episode-as-measurement-semantics",
            revised_episode_as_semantics,
        ),
        "data/revised-prefix-impact.jsonl.gz": ("prefix-impact", revised_prefixes),
        "data/incident-episode-mappings.jsonl.gz": ("incident-mappings", incident_mappings),
    }
    byte_files = {
        "报告/RRC25伊朗国家路由中断事件复算与对账报告.md": (
            "report",
            report.encode("utf-8"),
        )
    }
    return FullWindowBusinessOutputs(
        semantic_core=semantic_core,
        semantic_core_sha256=semantic_core_sha256,
        object_files=object_files,
        sequence_files=sequence_files,
        byte_files=byte_files,
        counts=dict(finalization["counts"]),
    )


def finalize_full_window_package(
    *,
    journal_root: os.PathLike[str] | str,
    output_root: os.PathLike[str] | str,
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
    resource_receipt_path: Optional[os.PathLike[str] | str] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> FinalizedPackage:
    """从已完成 journal 纯派生并发布一个独立、不可覆盖研究包。"""

    started_monotonic = monotonic()
    package_target = Path(output_root).absolute()
    resource_target = (
        _resource_receipt_default_path(package_target)
        if resource_receipt_path is None
        else Path(resource_receipt_path).absolute()
    )
    if resource_target.exists() or resource_target.is_symlink():
        raise FileExistsError("finalization resource receipt 已存在，拒绝覆盖")
    inputs = verify_frozen_full_window_inputs(
        journal_root=journal_root,
        profile=profile,
        source_fact_snapshot=source_fact_snapshot,
        incident_policy=incident_policy,
        compatible_mapping_snapshot=compatible_mapping_snapshot,
        revised_mapping_snapshot=revised_mapping_snapshot,
        code_identity=code_identity,
        input_selection=input_selection,
        claim_inventory=claim_inventory,
        bindings=bindings,
        started_monotonic=started_monotonic,
        monotonic=monotonic,
    )
    ancestry_inventory = _journal_ancestry_refs(
        inputs,
        started_monotonic=started_monotonic,
        monotonic=monotonic,
    )
    business = derive_full_window_business_outputs(
        inputs,
        journal_ancestry_inventory=ancestry_inventory,
    )
    manifest = _publish_package(
        inputs=inputs,
        output_root=package_target,
        object_files=business.object_files,
        sequence_files=business.sequence_files,
        byte_files=business.byte_files,
        acceptance_state="not_accepted",
        publication_hook=publication_hook,
        started_monotonic=started_monotonic,
        monotonic=monotonic,
    )
    _publish_finalization_resource_receipt(
        package_root=package_target,
        receipt_path=resource_target,
        started_monotonic=started_monotonic,
        monotonic=monotonic,
    )
    return FinalizedPackage(
        root=package_target,
        manifest=manifest,
        frozen_journal_head=inputs.journal.frozen_head,
        semantic_core_sha256=business.semantic_core_sha256,
        compatible_episode_count=business.counts["compatible_episodes"],
        revised_episode_count=business.counts["revised_episodes"],
        partial_vp_slot_count=business.counts["partial_vp_slots"],
        resource_receipt_path=resource_target,
    )


def _verify_packaged_journal_ancestry(
    directory: Path,
    manifest: Mapping[str, Any],
    finalization: Mapping[str, Any],
    *,
    started_monotonic: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    frozen = finalization.get("frozen_journal_head")
    if not isinstance(frozen, Mapping):
        raise FullWindowFinalizeError("finalization 缺少 frozen journal head")
    ancestry_root = directory / "journal-ancestry"
    rows = _journal_receipts(ancestry_root, frozen["terminal_receipt_ref"])
    previous = None
    previous_ref = None
    raw_genesis_ref = None
    initial_reserved = 0
    initial_observed = 0
    receipt_complete_attempt_ids: list[str] = []
    for index, (receipt_ref, receipt) in enumerate(rows):
        _check_finalization_soft_stop(
            started_monotonic=started_monotonic,
            monotonic=monotonic,
            phase=f"包内 receipt ancestry 核验 {index}",
        )
        try:
            _journal_contract._validate_receipt_semantics(
                ancestry_root,
                receipt,
                receipt_path=str(receipt_ref["path"]),
                receipt_sha256=str(receipt_ref["sha256"]),
                expected_bindings=frozen["bindings"],
                previous_receipt=previous,
                previous_ref=previous_ref,
            )
        except (OSError, FullWindowJournalError) as error:
            raise FullWindowFinalizeError("包内 receipt ancestry 离线语义核验失败") from error
        current_genesis = receipt.get("raw_genesis_ref")
        if raw_genesis_ref is None:
            raw_genesis_ref = current_genesis
            genesis = _read_hashed_json(
                ancestry_root, current_genesis, field="packaged raw genesis"
            )
            try:
                _journal_contract._verify_fingerprint(
                    genesis,
                    _journal_contract.RAW_GENESIS_SCHEMA_VERSION,
                    "packaged raw genesis",
                )
            except FullWindowJournalError as error:
                raise FullWindowFinalizeError("包内 raw genesis fingerprint 非法") from error
            initial_reserved = _nonnegative(
                genesis.get("initial_reserved_raw_bytes"),
                "raw_genesis.initial_reserved_raw_bytes",
            )
            preliminary = _nonnegative(
                genesis.get("preliminary_seed_read_bytes"),
                "raw_genesis.preliminary_seed_read_bytes",
            )
            seed = _nonnegative(
                genesis.get("seed_artifact_read_bytes"),
                "raw_genesis.seed_artifact_read_bytes",
            )
            additional = _nonnegative(
                genesis.get("additional_pre_update_raw_read_bytes"),
                "raw_genesis.additional_pre_update_raw_read_bytes",
            )
            if preliminary + seed + additional != initial_reserved:
                raise FullWindowFinalizeError("包内 raw genesis reservation 分项不闭合")
            initial_observed = initial_reserved
        elif current_genesis != raw_genesis_ref:
            raise FullWindowFinalizeError("包内 receipt ancestry raw genesis 不唯一")
        if receipt.get("sequence", 0) > 0:
            outcome = _read_hashed_json(
                ancestry_root, receipt.get("outcome_ref"), field="packaged receipt outcome"
            )
            attempt_id = outcome.get("attempt_id")
            if not isinstance(attempt_id, str):
                raise FullWindowFinalizeError("包内 receipt outcome 缺少 attempt_id")
            receipt_complete_attempt_ids.append(attempt_id)
        state_ref = receipt.get("state_ref")
        if (
            not isinstance(state_ref, Mapping)
            or not str(state_ref.get("path", "")).startswith("scratch/state-")
            or (ancestry_root / str(state_ref.get("path"))).exists()
        ):
            raise FullWindowFinalizeError("scratch 必须明确排除且不得冒充不可变证据")
        previous = receipt
        previous_ref = receipt_ref
    if (
        len(rows) != frozen.get("verified_receipt_count")
        or rows[-1][1].get("shard_chain_sha256") != frozen.get("shard_chain_sha256")
        or rows[-1][1].get("sequence") != frozen.get("completed_artifact_count")
    ):
        raise FullWindowFinalizeError("包内 receipt 数或 chain 与 frozen head 不一致")
    policy = finalization.get("journal_state_policy")
    if (
        not isinstance(policy, Mapping)
        or policy.get("scratch_is_evidence") is not False
        or policy.get("scratch_copied_into_package") is not False
        or policy.get("receipt_state_ref_role")
        != "deterministic_digest_recomputed_from_immutable_parse_observations"
        or policy.get("offline_verification_basis")
        != "seed_compact_route_events_complete_record_observations_receipts_and_raw_ledger"
    ):
        raise FullWindowFinalizeError("finalization 未声明 scratch 非证据边界")

    genesis_shards = {
        str(ref.get("kind")): ref for ref in rows[0][1].get("shards", ())
        if isinstance(ref, Mapping)
    }
    if set(genesis_shards) != {
        "seed_bootstrap_attestation", "seed_route_events", "seed_raw_record_refs"
    }:
        raise FullWindowFinalizeError("包内 genesis seed shards 不闭合")
    seed_attestation_rows = tuple(
        _iter_shard_rows(ancestry_root, genesis_shards["seed_bootstrap_attestation"])
    )
    if len(seed_attestation_rows) != 1:
        raise FullWindowFinalizeError("包内 seed bootstrap attestation 必须唯一")

    control_summaries = []
    control_count = 0
    observation_summaries = []
    observation_count = 0
    for receipt_index, (_receipt_ref, receipt) in enumerate(rows[1:], start=1):
        _check_finalization_soft_stop(
            started_monotonic=started_monotonic,
            monotonic=monotonic,
            phase=f"包内 record observation 核验 {receipt_index}",
        )
        shard_refs = {
            str(ref.get("kind")): ref
            for ref in receipt.get("shards", ())
            if isinstance(ref, Mapping)
        }
        if not {
            "route_events",
            "raw_record_refs",
            "control_records",
            "record_observations",
        } <= set(shard_refs):
            raise FullWindowFinalizeError("包内 receipt 缺少 retained raw observation 闭合输入")
        retained_routes = tuple(
            _iter_shard_rows(ancestry_root, shard_refs["route_events"])
        )
        retained_raw = tuple(
            _iter_shard_rows(ancestry_root, shard_refs["raw_record_refs"])
        )
        artifact = receipt.get("committed_artifact")
        if not isinstance(artifact, Mapping):
            raise FullWindowFinalizeError("包内非 genesis receipt 缺少 artifact")
        control_summary = _stream_control_record_shard(
            ancestry_root,
            shard_refs["control_records"],
            sequence=receipt_index,
            artifact=artifact,
            started_monotonic=started_monotonic,
            monotonic=monotonic,
        )
        control_summaries.append(control_summary)
        control_count += int(control_summary["record_count"])
        summary = _stream_record_observation_shard(
            ancestry_root,
            shard_refs["record_observations"],
            sequence=receipt_index,
            artifact=artifact,
            retained_route_rows=retained_routes,
            retained_raw_rows=retained_raw,
            started_monotonic=started_monotonic,
            monotonic=monotonic,
        )
        observation_summaries.append(summary)
        observation_count += int(summary["record_count"])
    observation_stream = finalization.get("record_observation_stream")
    control_stream = finalization.get("control_record_stream")
    finalization_counts = finalization.get("counts")
    if (
        not isinstance(control_stream, Mapping)
        or control_stream.get("schema_version")
        != CONTROL_RECORD_STREAM_SEMANTIC_SCHEMA
        or control_stream.get("record_count") != control_count
        or control_stream.get("semantic_sha256")
        != _control_record_stream_sha256(control_summaries)
        or control_stream.get("retention")
        != "streamed_per_shard_not_materialized_in_finalizer_memory"
        or not isinstance(observation_stream, Mapping)
        or observation_stream.get("schema_version")
        != RECORD_OBSERVATION_STREAM_SEMANTIC_SCHEMA
        or observation_stream.get("record_count") != observation_count
        or observation_stream.get("semantic_sha256")
        != _record_observation_stream_sha256(observation_summaries)
        or observation_stream.get("retention")
        != "streamed_per_shard_not_materialized_in_finalizer_memory"
        or not isinstance(finalization_counts, Mapping)
        or finalization_counts.get("control_records") != control_count
        or finalization_counts.get("record_observations") != observation_count
    ):
        raise FullWindowFinalizeError(
            "包内 control/record observation 流式 count/语义链与 finalization 不一致"
        )
    frozen_code = _load_json(directory / "frozen/code-identity.json")
    frozen_selection = _load_json(directory / "frozen/input-selection.json")
    frozen_bindings = _load_json(directory / "frozen/bindings.json")
    code_identity = _validate_code_identity(
        frozen_code, _sha(frozen_bindings.get("code_sha256"), "bindings.code_sha256")
    )
    try:
        compatible_mapping = mapping_view_from_frozen_snapshot(
            _load_json(directory / "frozen/compatible-mapping.json")
        )
        revised_mapping = mapping_view_from_revised_snapshot(
            _load_json(directory / "frozen/revised-mapping.json"),
            _load_json(directory / "frozen/compatible-mapping.json"),
        )
    except (TypeError, ValueError, KeyError) as error:
        raise FullWindowFinalizeError("包内 seed mapping inputs 非法") from error
    quality_for_seed = _load_json(directory / "quality-and-accounting.json")
    execution_for_seed = quality_for_seed.get("raw_accounting")
    if not isinstance(execution_for_seed, Mapping):
        raise FullWindowFinalizeError("包内 seed 核验缺少 raw accounting")
    _validate_seed_bootstrap_attestation(
        attestation=seed_attestation_rows[0],
        seed_route_rows=tuple(
            _iter_shard_rows(ancestry_root, genesis_shards["seed_route_events"])
        ),
        seed_raw_rows=tuple(
            _iter_shard_rows(ancestry_root, genesis_shards["seed_raw_record_refs"])
        ),
        execution=execution_for_seed,
        bindings=frozen_bindings,
        code_identity=code_identity,
        selection=frozen_selection,
        compatible_mapping=compatible_mapping,
        revised_mapping=revised_mapping,
    )
    frozen_derivation = finalization.get(
        "independent_derivation_verification"
    )
    if not isinstance(frozen_derivation, Mapping):
        raise FullWindowFinalizeError("包内缺少独立逐槽复算证明")
    bootstrap = frozen_derivation.get("runtime_bootstrap_bytes_per_second")
    if (
        isinstance(bootstrap, bool)
        or not isinstance(bootstrap, (int, float))
        or not math.isfinite(float(bootstrap))
        or float(bootstrap) <= 0
    ):
        raise FullWindowFinalizeError("包内逐槽复算 bootstrap throughput 非法")
    offline_derivation = _validate_independent_artifact_derivations(
        journal_root=ancestry_root,
        frozen_head=frozen,
        seed_bootstrap_attestation=seed_attestation_rows[0],
        compatible_mapping=compatible_mapping,
        revised_mapping=revised_mapping,
        runtime_bootstrap_bytes_per_second=float(bootstrap),
        started_monotonic=started_monotonic,
        monotonic=monotonic,
    )
    if canonical_json(offline_derivation) != canonical_json(frozen_derivation):
        raise FullWindowFinalizeError("包内独立逐槽复算证明与 immutable ancestry 不一致")

    contents = {item["path"]: item for item in manifest["contents"]}
    ledger_rows = sorted(
        (
            path.removeprefix("journal-ancestry/") , item
        )
        for path, item in contents.items()
        if path.startswith("journal-ancestry/raw-ledger/")
    )
    inventory = finalization.get("raw_ledger_inventory")
    if (
        not isinstance(inventory, Mapping)
        or inventory.get("file_count") != len(ledger_rows)
        or inventory.get("semantic_sha256")
        != _canonical_hash(
            tuple({"path": path, "sha256": item["sha256"]} for path, item in ledger_rows)
        )
        or inventory.get("reservation_semantics")
        != "all_attempt_starts_no_refund_including_failures_and_retries"
    ):
        raise FullWindowFinalizeError("包内完整 raw ledger inventory 不闭合")
    attempts: dict[str, Mapping[str, Any]] = {}
    attempt_ref_by_id: dict[str, Mapping[str, str]] = {}
    outcomes: list[Mapping[str, Any]] = []
    reserved_updates = 0
    packaged_accumulator: Optional[Mapping[str, Any]] = None
    for ledger_index, (relative, item) in enumerate(ledger_rows):
        if ledger_index % 128 == 0:
            _check_finalization_soft_stop(
                started_monotonic=started_monotonic,
                monotonic=monotonic,
                phase="包内 raw ledger 核验",
            )
        if relative == "raw-ledger/ACTIVE":
            raise FullWindowFinalizeError("包内 raw ledger 不得包含可变 ACTIVE attempt")
        payload = _load_json(ancestry_root / relative, maximum_bytes=32 * 1024 * 1024)
        if relative.startswith("raw-ledger/attempts/"):
            try:
                _journal_contract._verify_fingerprint(
                    payload,
                    _journal_contract.ATTEMPT_START_SCHEMA_VERSION,
                    "packaged attempt",
                )
            except FullWindowJournalError as error:
                raise FullWindowFinalizeError("包内 attempt fingerprint 非法") from error
            attempt_id = payload.get("attempt_id")
            if (
                not isinstance(attempt_id, str)
                or attempt_id in attempts
                or payload.get("run_id") != frozen.get("run_id")
                or payload.get("bindings") != frozen.get("bindings")
            ):
                raise FullWindowFinalizeError("包内 attempt 身份/bindings 非法")
            attempts[attempt_id] = payload
            attempt_ref_by_id[attempt_id] = {
                "path": relative,
                "sha256": item["sha256"],
            }
            reserved_updates += _nonnegative(
                payload.get("reserved_raw_bytes"), "attempt.reserved_raw_bytes"
            )
        elif relative.startswith("raw-ledger/outcomes/"):
            try:
                _journal_contract._verify_fingerprint(
                    payload,
                    _journal_contract.ATTEMPT_OUTCOME_SCHEMA_VERSION,
                    "packaged outcome",
                )
            except FullWindowJournalError as error:
                raise FullWindowFinalizeError("包内 outcome fingerprint 非法") from error
            payload["__ledger_ref"] = {
                "path": relative,
                "sha256": item["sha256"],
            }
            outcomes.append(payload)
        elif relative == "raw-ledger/ACCUMULATOR":
            if packaged_accumulator is not None:
                raise FullWindowFinalizeError("包内 raw ACCUMULATOR 必须唯一")
            try:
                _journal_contract._verify_fingerprint(
                    payload,
                    _journal_contract.RAW_ACCUMULATOR_SCHEMA_VERSION,
                    "packaged raw accumulator",
                )
            except FullWindowJournalError as error:
                raise FullWindowFinalizeError("包内 raw ACCUMULATOR fingerprint 非法") from error
            packaged_accumulator = payload
    grouped_outcomes: dict[str, list[Mapping[str, Any]]] = {}
    for outcome in outcomes:
        attempt_id = outcome.get("attempt_id")
        attempt = attempts.get(str(attempt_id))
        if (
            attempt is None
            or outcome.get("attempt_ref") != attempt_ref_by_id.get(str(attempt_id))
        ):
            raise FullWindowFinalizeError("包内 outcome→attempt 引用未闭合")
        grouped_outcomes.setdefault(str(attempt_id), []).append(outcome)
    observed_updates_exact = 0
    observed_updates_lower = 0
    observed_updates_upper = 0
    observed_all_exact = True
    complete_ids = set()
    terminal_ids = set()
    publication_failed_ids = set()
    for attempt_id, grouped in grouped_outcomes.items():
        measured = _validate_attempt_outcome_group(
            attempts[attempt_id], grouped
        )
        observed_updates_lower += measured[
            "observed_compressed_bytes_lower_bound"
        ]
        observed_updates_upper += measured[
            "observed_compressed_bytes_upper_bound"
        ]
        if measured["observed_compressed_bytes"] is None:
            observed_all_exact = False
        else:
            observed_updates_exact += measured["observed_compressed_bytes"]
        if measured["has_complete"]:
            complete_ids.add(attempt_id)
        if measured["has_terminal"]:
            terminal_ids.add(attempt_id)
        if any(
            outcome.get("outcome") == "publication_failed_after_complete_single_pass"
            for outcome in grouped
        ):
            publication_failed_ids.add(attempt_id)
    receipt_complete_set = set(receipt_complete_attempt_ids)
    if (
        len(receipt_complete_attempt_ids) != len(receipt_complete_set)
        or not receipt_complete_set <= complete_ids
        or receipt_complete_set & publication_failed_ids
        or complete_ids != receipt_complete_set | publication_failed_ids
    ):
        raise FullWindowFinalizeError(
            "包内 complete outcome 未由成功 receipt 或 publication-failed terminal 唯一闭合"
        )
    if initial_reserved + reserved_updates != frozen.get("cumulative_reserved_raw_bytes"):
        raise FullWindowFinalizeError("完整 raw ledger reservation 与 frozen cumulative 不一致")
    if (
        packaged_accumulator is None
        or packaged_accumulator.get("run_id") != frozen.get("run_id")
        or packaged_accumulator.get("bindings") != frozen.get("bindings")
        or packaged_accumulator.get("raw_genesis_ref") != raw_genesis_ref
        or packaged_accumulator.get("attempt_count") != len(attempts)
        or packaged_accumulator.get("cumulative_reserved_raw_bytes")
        != initial_reserved + reserved_updates
    ):
        raise FullWindowFinalizeError("包内 ACCUMULATOR 与完整 attempt ledger 不闭合")
    observed_sum = (
        initial_observed + observed_updates_exact if observed_all_exact else None
    )
    observed_lower_sum = initial_observed + observed_updates_lower
    observed_upper_sum = initial_observed + observed_updates_upper
    unclosed_count = len(set(attempts) - receipt_complete_set - terminal_ids)
    quality = _load_json(directory / "quality-and-accounting.json")
    accounting = quality.get("raw_accounting")
    if (
        not isinstance(accounting, Mapping)
        or accounting.get("initial_reserved_raw_bytes") != initial_reserved
        or accounting.get("initial_observed_raw_bytes") != initial_observed
        or accounting.get("cumulative_reserved_raw_bytes_upper_bound")
        != initial_reserved + reserved_updates
        or accounting.get("observed_compressed_bytes_sum") != observed_sum
        or accounting.get("observed_compressed_bytes_state")
        != ("exact" if observed_all_exact else "bounded_after_process_termination")
        or accounting.get("observed_compressed_bytes_lower_bound_sum")
        != observed_lower_sum
        or accounting.get("observed_compressed_bytes_upper_bound_sum")
        != observed_upper_sum
        or accounting.get("unclosed_attempt_count") != unclosed_count
    ):
        raise FullWindowFinalizeError("包内 raw accounting 与完整 ledger 不一致")
    for sample_path in (
        "data/compatible-country-samples.jsonl.gz",
        "data/revised-country-samples.jsonl.gz",
    ):
        try:
            _check_finalization_soft_stop(
                started_monotonic=started_monotonic,
                monotonic=monotonic,
                phase="包内 sample source ref 核验",
            )
            with gzip.open(directory / sample_path, "rt", encoding="utf-8") as stream:
                for line in stream:
                    sample = json.loads(line)
                    for source_ref in sample.get("source_refs", ()):
                        target = source_ref.get("ref_id")
                        registered = contents.get(target)
                        if (
                            source_ref.get("ref_type") != "state_shard"
                            or registered is None
                            or registered.get("sha256") != source_ref.get("sha256")
                            or not (directory / str(target)).is_file()
                        ):
                            raise FullWindowFinalizeError("sample source_ref 未解析到包内实际 shard")
        except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FullWindowFinalizeError("包内 sample shard 无法离线核验") from error


def verify_finalized_package(
    root: os.PathLike[str] | str,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    started_monotonic = monotonic()
    directory = Path(root)
    manifest = verify_published_package(directory)
    _check_finalization_soft_stop(
        started_monotonic=started_monotonic,
        monotonic=monotonic,
        phase="最终包 manifest 核验",
    )
    _validate_published_contracts(directory)
    finalization = _load_json(directory / "metadata/finalization.json")
    quality = _load_json(directory / "quality-and-accounting.json")
    required = {
        "metadata/finalization.json",
        "quality-and-accounting.json",
        "frozen/bindings.json",
        "data/compatible-country-samples.jsonl.gz",
        "data/compatible-sample-measurement-semantics.jsonl.gz",
        "data/compatible-episode-as-measurement-semantics.jsonl.gz",
        "报告/RRC25伊朗国家路由中断事件复算与对账报告.md",
    }
    observed = {item["path"] for item in manifest["contents"]}
    if not required <= observed:
        raise FullWindowFinalizeError("最终包缺少规定机器结果或中文报告")
    _verify_packaged_journal_ancestry(
        directory,
        manifest,
        finalization,
        started_monotonic=started_monotonic,
        monotonic=monotonic,
    )
    semantic = _sha(finalization.get("semantic_core_sha256"), "semantic_core_sha256")
    if (
        finalization.get("reproduction_state")
        != "pending_independent_second_empty_directory"
        or finalization.get("acceptance_state") != "not_accepted"
        or finalization.get("reproduction_scope")
        != "pure_derivation_from_same_frozen_journal"
        or finalization.get("raw_replay_reproduction")
        != "not_performed_by_user_choice"
        or manifest.get("acceptance_state") != "not_accepted"
        or quality.get("acceptance_state") != "not_accepted"
    ):
        raise FullWindowFinalizeError("独立复现 receipt 前 package 不得标记 accepted")
    resource_path = _resource_receipt_default_path(directory.absolute())
    resource_verified = False
    if resource_path.exists() and not resource_path.is_symlink():
        verify_finalization_resource_receipt(directory, resource_path)
        resource_verified = True
    return {
        "verified": True,
        "package_root": str(directory.resolve()),
        "release_id": manifest["release_id"],
        "package_semantic_fingerprint_sha256": manifest["semantic_fingerprint_sha256"],
        "semantic_core_sha256": semantic,
        "acceptance_state": "not_accepted",
        "reproduction_state": "pending",
        "finalization_resource_receipt_verified": resource_verified,
        "finalization_resource_receipt_path": str(resource_path),
    }


def _non_reproduction_blockers(package_root: Path) -> Tuple[str, ...]:
    quality = _load_json(package_root / "quality-and-accounting.json")
    research = quality.get("research_quality")
    gates = research.get("gates") if isinstance(research, Mapping) else None
    if not isinstance(gates, list):
        raise FullWindowFinalizeError("quality 缺少研究门明细")
    return tuple(
        str(gate.get("gate_id"))
        for gate in gates
        if isinstance(gate, Mapping)
        and gate.get("gate_id") != "reproducibility"
        and gate.get("status") == "fail"
        and gate.get("blocking") is True
    )


def reproduce_semantics(
    *,
    reference_package_root: os.PathLike[str] | str,
    output_root: os.PathLike[str] | str,
    acceptance_receipt_path: os.PathLike[str] | str,
    journal_root: os.PathLike[str] | str,
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
    second_resource_receipt_path: Optional[os.PathLike[str] | str] = None,
) -> Mapping[str, Any]:
    """在第二个不存在目标发布同一业务语义，并独立发布 accepted receipt。"""

    receipt_target = Path(acceptance_receipt_path).absolute()
    if receipt_target.exists() or receipt_target.is_symlink():
        raise FileExistsError("reproduction acceptance receipt 已存在，拒绝覆盖")
    reference_root = Path(reference_package_root).absolute()
    reference = verify_finalized_package(reference_root)
    reference_resource = _resource_receipt_default_path(reference_root)
    verify_finalization_resource_receipt(reference_root, reference_resource)
    second = finalize_full_window_package(
        journal_root=journal_root,
        output_root=output_root,
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
        resource_receipt_path=second_resource_receipt_path,
    )
    second_root = second.root.absolute()
    second_verified = verify_finalized_package(second_root)
    verify_finalization_resource_receipt(second_root, second.resource_receipt_path)
    if reference["semantic_core_sha256"] != second_verified["semantic_core_sha256"]:
        raise FullWindowFinalizeError("两个独立目录的业务 semantic core 不一致，拒绝 accepted")
    reference_blockers = _non_reproduction_blockers(reference_root)
    second_blockers = _non_reproduction_blockers(second_root)
    if reference_blockers or second_blockers:
        raise FullWindowFinalizeError(
            f"除 reproducibility 外仍有 blocking gate：{reference_blockers + second_blockers}"
        )
    normalized_bindings = {
        key: _sha(value, f"bindings.{key}") for key, value in sorted(bindings.items())
    }
    if _load_json(reference_root / "frozen/bindings.json") != normalized_bindings or _load_json(
        second_root / "frozen/bindings.json"
    ) != normalized_bindings:
        raise FullWindowFinalizeError("两个 package 未绑定同一 journal 四项 inputs")

    packages = []
    for role, root, verified, resource_path in (
        ("reference", reference_root, reference, reference_resource),
        ("reproduction", second_root, second_verified, second.resource_receipt_path),
    ):
        manifest_raw = _read_stable_regular(
            root / "package-manifest.json", maximum_bytes=16 * 1024 * 1024
        )
        finalization = _load_json(root / "metadata/finalization.json")
        resource_raw = _read_stable_regular(resource_path, maximum_bytes=4 * 1024 * 1024)
        packages.append(
            {
                "role": role,
                "package_root": str(root.resolve()),
                "release_id": verified["release_id"],
                "package_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                "package_semantic_fingerprint_sha256": verified[
                    "package_semantic_fingerprint_sha256"
                ],
                "audit_run_sha256": finalization["audit_run_sha256"],
                "resource_receipt_path": str(resource_path.resolve()),
                "resource_receipt_file_sha256": hashlib.sha256(resource_raw).hexdigest(),
            }
        )
    semantic = {
        "schema_version": "rrc25-full-window-reproduction-acceptance/v1",
        "acceptance_state": "accepted",
        "reproduction_scope": "pure_derivation_from_same_frozen_journal",
        "raw_replay_reproduction": "not_performed_by_user_choice",
        "semantic_core_sha256": reference["semantic_core_sha256"],
        "input_bindings": normalized_bindings,
        "packages": packages,
        "checks": {
            "two_distinct_directories": reference_root != second_root,
            "business_semantic_core_equal": True,
            "package_semantic_hashes_may_differ_due_to_audit_identity": True,
            "resource_receipts_verified": True,
            "non_reproduction_blocking_gate_count": 0,
        },
    }
    if reference_root == second_root:
        raise FullWindowFinalizeError("独立复现必须使用不同目录")
    receipt = {
        **semantic,
        "receipt_sha256": _canonical_hash(
            {"schema": "rrc25_full_window_reproduction_acceptance_v1", "receipt": semantic}
        ),
    }
    try:
        parent = receipt_target.parent.lstat()
    except OSError as error:
        raise FullWindowFinalizeError("acceptance receipt 父目录不存在") from error
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise FullWindowFinalizeError("acceptance receipt 父目录非法")
    artifact = _write_bytes_create_only(
        receipt_target,
        (canonical_json(receipt) + "\n").encode("utf-8"),
        kind="reproduction-acceptance",
    )
    os.chmod(artifact.path, 0o440)
    return dict(receipt)


def verify_reproduction_acceptance_receipt(
    receipt_path: os.PathLike[str] | str,
) -> Mapping[str, Any]:
    receipt = _load_json(receipt_path, maximum_bytes=8 * 1024 * 1024)
    if receipt.get("schema_version") == "rrc25-full-window-reproduction-acceptance/v2":
        # v2 的 accepted 语义是从同一已验 finalization segment index 向两个
        # 空目录独立装配；不是 raw MRT A/B。延迟导入避免 workspace→finalizer
        # 单槽派生依赖形成模块初始化环。
        from .full_window_finalize_workspace import (
            FullWindowFinalizeWorkspaceError,
            verify_workspace_reproduction_acceptance_receipt,
        )

        try:
            return verify_workspace_reproduction_acceptance_receipt(receipt_path)
        except FullWindowFinalizeWorkspaceError as error:
            raise FullWindowFinalizeError(
                "v2 segmented reproduction acceptance receipt 核验失败"
            ) from error
    semantic = dict(receipt)
    supplied = semantic.pop("receipt_sha256", None)
    if (
        receipt.get("schema_version") != "rrc25-full-window-reproduction-acceptance/v1"
        or receipt.get("acceptance_state") != "accepted"
        or receipt.get("reproduction_scope")
        != "pure_derivation_from_same_frozen_journal"
        or receipt.get("raw_replay_reproduction")
        != "not_performed_by_user_choice"
        or supplied
        != _canonical_hash(
            {"schema": "rrc25_full_window_reproduction_acceptance_v1", "receipt": semantic}
        )
    ):
        raise FullWindowFinalizeError("reproduction acceptance receipt fingerprint 非法")
    packages = receipt.get("packages")
    if not isinstance(packages, list) or [row.get("role") for row in packages] != [
        "reference",
        "reproduction",
    ]:
        raise FullWindowFinalizeError("acceptance receipt package 对不闭合")
    observed_core = set()
    observed_roots = set()
    for row in packages:
        root = Path(str(row["package_root"])).absolute()
        verified = verify_finalized_package(root)
        observed_core.add(verified["semantic_core_sha256"])
        observed_roots.add(str(root))
        manifest_raw = _read_stable_regular(
            root / "package-manifest.json", maximum_bytes=16 * 1024 * 1024
        )
        resource_path = Path(str(row["resource_receipt_path"])).absolute()
        resource_raw = _read_stable_regular(resource_path, maximum_bytes=4 * 1024 * 1024)
        verify_finalization_resource_receipt(root, resource_path)
        finalization = _load_json(root / "metadata/finalization.json")
        if (
            row.get("package_manifest_sha256") != hashlib.sha256(manifest_raw).hexdigest()
            or row.get("package_semantic_fingerprint_sha256")
            != verified["package_semantic_fingerprint_sha256"]
            or row.get("audit_run_sha256") != finalization["audit_run_sha256"]
            or row.get("resource_receipt_file_sha256")
            != hashlib.sha256(resource_raw).hexdigest()
            or _non_reproduction_blockers(root)
        ):
            raise FullWindowFinalizeError("acceptance receipt package/audit/resource 绑定失效")
    if (
        len(observed_roots) != 2
        or observed_core != {receipt.get("semantic_core_sha256")}
        or any(
            _load_json(Path(row["package_root"]) / "frozen/bindings.json")
            != receipt.get("input_bindings")
            for row in packages
        )
    ):
        raise FullWindowFinalizeError("acceptance receipt 未证明同输入双目录业务语义一致")
    return dict(receipt)
