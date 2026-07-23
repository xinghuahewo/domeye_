"""完整 UPDATE 窗口的单 artifact 研究 worker。

该 worker 是 ``full_window_journal`` 与既有 UPDATE adapter/state replay 的最小
纵切。每次调用只处理 CURRENT 指向的一个五分钟 artifact；stream 完整耗尽并
核验 single-pass 统计后，才计算槽末状态和 mapped-compatible 国家曲线，最后
交给 journal 做 artifact 边界原子提交。

严格人口中的 AS_SET、origin/mapping unknown 会原样留在 route/raw shard 和
``strict_population`` 诊断中，但不会把 mapped-compatible 主曲线污染为伪零或
全局 unknown。窗口中新发现的确定 IR origin 只从本槽 ``first_seen`` 起加入
动态 cohort；已经发布的更早槽不会回填。

这里不读取 seed checkpoint 文件。生产入口必须先调用既有
``verify_full_seed_checkpoint``，再把其中验证过的 RouteReplayState、tracked
prefix 和 seed peer population 传给 ``initialize_compact_state_from_seed``。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import stat
import zlib
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Tuple

from ...route_event import AsPathSegment, ParsedRouteElement
from .country_impact import (
    CONFLICT,
    RESOLVED,
    UNKNOWN,
    CountryMappingView,
    build_country_mapping_view,
    derive_origin_asns,
    snapshot_id_v1,
)
from .full_window_journal import (
    ArtifactDescriptor,
    AttemptToken,
    CommittedArtifact,
    FullWindowJournalError,
    JournalHead,
    ShardInput,
    SinglePassProof,
    commit_artifact_boundary,
    initialize_full_window_journal,
    record_attempt_failure,
)
from .bounded_pilot_worker import (
    FULL_SEED_CHECKPOINT_FINGERPRINT_SCHEMA,
    FULL_SEED_CHECKPOINT_SCHEMA_VERSION,
    _verified_probe_terminal_accounting,
    _verified_seed_raw_reservation,
    validate_seed_spool_attestation,
    verify_full_seed_checkpoint,
)
from .country_impact import RawRetentionMappingUnion
from .file_artifacts import canonical_json
from .mapped_compatible_projection import build_mapped_compatible_projection
from .replay_persistence import (
    route_replay_state_from_payload,
    route_replay_state_to_payload,
)
from .state_replay import (
    CONTINUOUS,
    ReplaySnapshot,
    ResearchRouteEvent,
    RouteLastChange,
    RouteReplayState,
    apply_streaming_update_batch,
    build_five_minute_snapshot,
)
from .update_adapter import (
    END_OF_RIB_RECORD,
    KEEPALIVE_RECORD,
    NOTIFICATION_RECORD,
    OPEN_RECORD,
    STATE_CHANGE_RECORD,
    UPDATE_RECORD,
    AdaptedUpdateRecord,
    RawRecordEvidence,
    iter_adapted_update_records,
)


UTC = timezone.utc
COMPACT_STATE_SCHEMA_VERSION = "rrc25-full-window-compact-state/v1"
COUNTRY_SLOT_SCHEMA_VERSION = "rrc25-full-window-country-slot/v1"
ROUTE_EVENT_SHARD_SCHEMA_VERSION = "rrc25-full-window-route-event/v1"
RAW_AUDIT_SHARD_SCHEMA_VERSION = "rrc25-full-window-raw-audit/v1"
RAW_RECORD_REF_SHARD_SCHEMA_VERSION = "rrc25-full-window-raw-record-ref/v1"
CONTROL_RECORD_SHARD_SCHEMA_VERSION = "rrc25-full-window-control-record/v1"
RECORD_OBSERVATION_SHARD_SCHEMA_VERSION = (
    "rrc25-full-window-record-observation/v1"
)
VP_POPULATION_SEMANTICS = "seed_peer_index_union_current_known_state_v1"
VP_OBSERVED_SEMANTICS = "state_visible_vp_including_quiet_carry_forward_v1"
UPDATE_ACTIVE_VP_SEMANTICS = (
    "all_validated_update_message_peers_diagnostic_only_not_vp_coverage_denominator"
)
MAX_FULL_SEED_COMPRESSED_BYTES = 5_000_000_000
MAX_FULL_SEED_DECOMPRESSED_BYTES = 4_750_000_000
SEED_BOOTSTRAP_ATTESTATION_SCHEMA_VERSION = (
    "rrc25-full-window-seed-bootstrap-attestation/v3"
)
SEED_BOOTSTRAP_ATTESTATION_FINGERPRINT_SCHEMA = (
    "rrc25_full_window_seed_bootstrap_attestation_fingerprint_v3"
)
SEED_RETIREMENT_BOOTSTRAP_BINDING_SCHEMA_VERSION = (
    "rrc25-seed-retirement-bootstrap-binding/v1"
)
_MAPPING_FINGERPRINT_CACHE: dict[int, Tuple[CountryMappingView, str]] = {}


class FullWindowWorkerError(ValueError):
    """单 artifact UPDATE 不能按冻结研究语义安全提交。"""


RawRetentionMembership = Callable[[int], Optional[bool]]
RecordStreamFactory = Callable[[Mapping[str, Any]], Iterable[Any]]
Clock = Callable[[], float]


@dataclass(frozen=True)
class CompactState:
    route_state: RouteReplayState
    cursor_route_event_id: Optional[str]
    tracked_prefixes: Tuple[str, ...]
    known_vp_ids: Tuple[str, ...]
    vp_population_source_sha256: str
    compatible_mapping_fingerprint_sha256: str
    revised_mapping_fingerprint_sha256: str
    peer_session_states: Tuple[
        Tuple[str, str, str, Optional[str], Optional[str], Optional[str]], ...
    ]
    cohort_members: Tuple[Tuple[int, Optional[str], bool], ...]
    cohort_references: Tuple[Tuple[int, str, str, Optional[str], bool], ...]
    revised_cohort_members: Tuple[Tuple[int, Optional[str], bool], ...]
    revised_cohort_references: Tuple[
        Tuple[int, str, str, Optional[str], bool], ...
    ]
    last_slot_end_exclusive_utc: Optional[str]


@dataclass(frozen=True)
class VerifiedSeedBootstrap:
    """同一普通文件稳定性与 full-seed v2 语义均已核验的启动对象。"""

    checkpoint_path: Path
    checkpoint_file_sha256: str
    checkpoint_size_bytes: int
    checkpoint_fingerprint_sha256: str
    checkpoint_sequence: int
    route_state: RouteReplayState
    tracked_prefixes: Tuple[str, ...]
    expected_vp_ids: Tuple[str, ...]
    prior_raw_read_bytes: int
    seed_artifact_read_bytes: int
    seed_artifact_ref: Mapping[str, Any]
    seed_route_event_rows: Tuple[Mapping[str, Any], ...]
    seed_raw_record_ref_rows: Tuple[Mapping[str, Any], ...]
    checkpoint_bootstrap_context: Mapping[str, Any]
    seed_spool_attestation: Mapping[str, Any]
    seed_parser_attestation: Mapping[str, Any]


@dataclass(frozen=True)
class ArtifactBoundaryDerivation:
    """从冻结前态、RouteEvent 和逐 physical-record 观测纯派生的槽边界。"""

    compatible_country_slot: Mapping[str, Any]
    revised_country_slot: Mapping[str, Any]
    final_compact_state: Mapping[str, Any]


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise FullWindowWorkerError(f"{field} 必须是 SHA256")
    return value


def _utc(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise FullWindowWorkerError(f"{field} 必须是 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise FullWindowWorkerError(f"{field} 不是合法 UTC 时间") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed) or parsed.microsecond:
        raise FullWindowWorkerError(f"{field} 必须是秒级 UTC")
    canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical != value:
        raise FullWindowWorkerError(f"{field} 不是规范 UTC")
    return value


def _utc_event(value: Any, field: str) -> str:
    """规范化可带微秒的 RouteEvent 时间，不把 first_seen 降为槽边界。"""

    if not isinstance(value, str) or not value.endswith("Z"):
        raise FullWindowWorkerError(f"{field} 必须是 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise FullWindowWorkerError(f"{field} 不是合法 UTC 时间") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise FullWindowWorkerError(f"{field} 必须是 UTC 时间")
    if parsed.microsecond:
        canonical = parsed.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    else:
        canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical != value:
        raise FullWindowWorkerError(f"{field} 不是规范 UTC")
    return value


def _canonical_prefix(value: str) -> str:
    try:
        prefix = ipaddress.ip_network(value, strict=True).compressed
    except (TypeError, ValueError) as error:
        raise FullWindowWorkerError("tracked prefix 不是规范 CIDR") from error
    if prefix != value:
        raise FullWindowWorkerError("tracked prefix 不是规范 CIDR")
    return value


def _compact_route_state(
    state: RouteReplayState,
    *,
    cursor_route_event_id: Optional[str],
) -> Mapping[str, Any]:
    """丢弃历史 processed IDs/withdraw latest-change，只保留继续回放所需状态。"""

    if not isinstance(state, RouteReplayState):
        raise FullWindowWorkerError("route_state 类型非法")
    visible_changes = tuple(
        RouteLastChange(
            key=entry.key,
            action=entry.last_action,
            event_time_utc=entry.last_event_time_utc,
            as_path=entry.as_path,
            quality_flags=entry.quality_flags,
            raw_ref=entry.last_raw_ref,
        )
        for entry in state.entries
    )
    retained_ids = {entry.last_raw_ref.route_event_id for entry in state.entries}
    if cursor_route_event_id is not None:
        retained_ids.add(cursor_route_event_id)
    if state.last_order_key is not None and not retained_ids:
        raise FullWindowWorkerError("非空回放 cursor 缺少最小 RouteEvent 身份")
    compact = RouteReplayState(
        entries=state.entries,
        latest_changes=visible_changes,
        continuity_state=state.continuity_state,
        missing_reasons=state.missing_reasons,
        processed_route_event_ids=frozenset(retained_ids),
        last_order_key=state.last_order_key,
    )
    return route_replay_state_to_payload(compact)


def _target_contexts(projected: Any) -> Tuple[Tuple[int, str, str], ...]:
    values = set()
    for context in projected.audit.prefix_contexts:
        afi = "ipv4" if context.afi_safi == "ipv4_unicast" else "ipv6"
        for asn in context.target_origin_asns:
            values.add((asn, afi, context.prefix))
    return tuple(sorted(values))


def _mapped_projection_for_view(
    source: RouteReplayState | ReplaySnapshot,
    mapping: CountryMappingView,
) -> Any:
    """复用同一严格 mapped-only 分类器计算 compatible/revised 两个视图。

    既有投影器刻意只接受 ``compatible`` 标签。revised 在这里以同一组冻结
    assignments 构造一个仅供纯计算的 compatible 影子输入；返回结果随后由
    country-slot 重新绑定真实 view/source 身份。该适配不改变人口，也不把
    revised 伪称为主曲线。
    """

    if not isinstance(mapping, CountryMappingView):
        raise FullWindowWorkerError("mapping 必须是 CountryMappingView")
    if mapping.view == "compatible":
        projection_mapping = mapping
    elif mapping.view == "revised":
        projection_mapping = build_country_mapping_view(
            mapping.assignments,
            view="compatible",
            target_country=mapping.target_country,
            source_sha256=mapping.source_sha256,
            source_ref=mapping.source_ref,
        )
    else:  # pragma: no cover - CountryMappingView 已约束
        raise FullWindowWorkerError("mapping view 不受支持")
    return build_mapped_compatible_projection(source, projection_mapping)


def _mapping_view_fingerprint(mapping: CountryMappingView) -> str:
    if not isinstance(mapping, CountryMappingView):
        raise FullWindowWorkerError("mapping 必须是 CountryMappingView")
    cached = _MAPPING_FINGERPRINT_CACHE.get(id(mapping))
    if cached is not None and cached[0] is mapping:
        return cached[1]
    semantic = {
        "schema": "rrc25_full_window_mapping_view_fingerprint_v1",
        "view": mapping.view,
        "target_country": mapping.target_country,
        "source_sha256": mapping.source_sha256,
        "source_ref": mapping.source_ref,
        "assignments": [asdict(row) for row in mapping.assignments],
        "revised_lineage": (
            None if mapping.revised_lineage is None else asdict(mapping.revised_lineage)
        ),
    }
    fingerprint = hashlib.sha256(
        canonical_json(semantic).encode("utf-8")
    ).hexdigest()
    _MAPPING_FINGERPRINT_CACHE[id(mapping)] = (mapping, fingerprint)
    return fingerprint


def _file_identity(metadata: os.stat_result) -> Tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _semantic_sha256(schema: str, value: Any) -> str:
    return hashlib.sha256(
        canonical_json({"schema": schema, "value": value}).encode("utf-8")
    ).hexdigest()


def _hash_stable_regular(path: Path, *, maximum_bytes: int) -> Tuple[str, int]:
    """哈希一个非符号链接普通文件，并拒绝读取期间的身份漂移。"""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise FullWindowWorkerError(f"seed parser source 不可读：{path.name}") from error
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FullWindowWorkerError("seed parser source 必须是普通文件")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            size += len(block)
            if size > maximum_bytes:
                raise FullWindowWorkerError("seed parser source 超过读取上限")
            digest.update(block)
        after = os.fstat(descriptor)
        if _file_identity(before) != _file_identity(after):
            raise FullWindowWorkerError("seed parser source 在哈希期间变化")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _seed_parser_attestation(code_identity_sha256: str) -> Mapping[str, Any]:
    """冻结 full-seed 实际使用的 in-process RIB spool parser 源身份。"""

    source_root = Path(__file__).resolve().parent
    relatives = (
        "backend/data_pipeline/research/rrc25_country_outage/bounded_pilot_worker.py",
        "backend/data_pipeline/research/rrc25_country_outage/rib_adapter.py",
        "backend/data_pipeline/research/rrc25_country_outage/rib_parser.py",
    )
    rows = []
    for relative in relatives:
        source = source_root / Path(relative).name
        digest, size = _hash_stable_regular(source, maximum_bytes=32 * 1024 * 1024)
        rows.append({"path": relative, "size_bytes": size, "sha256": digest})
    semantic = {
        "schema_version": "rrc25-seed-parser-attestation/v1",
        "name": "domeye_rib_spool_parser",
        "execution_policy": "verified_in_process_source",
        "code_identity_sha256": _sha(code_identity_sha256, "code_identity_sha256"),
        "source_files": rows,
    }
    return {
        **semantic,
        "attestation_fingerprint_sha256": _semantic_sha256(
            "rrc25_seed_parser_attestation_fingerprint_v1", semantic
        ),
    }


def _read_stable_full_seed_checkpoint(path: Path) -> Tuple[Mapping[str, Any], str, int, Tuple[int, int, int, int, int]]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise FullWindowWorkerError("full seed checkpoint 不可读") from error
    digest = hashlib.sha256()
    chunks = []
    size = 0
    decoded_size = 0
    decoder: Optional[zlib.Decompress] = None
    first = True
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FullWindowWorkerError("full seed checkpoint 必须是普通文件")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            size += len(block)
            if size > MAX_FULL_SEED_COMPRESSED_BYTES:
                raise FullWindowWorkerError("full seed checkpoint 压缩文件超过 5GB")
            digest.update(block)
            if first:
                first = False
                if block.startswith(b"\x1f\x8b"):
                    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            if decoder is None:
                decoded = block
            else:
                try:
                    decoded = decoder.decompress(block)
                except zlib.error as error:
                    raise FullWindowWorkerError("full seed checkpoint gzip 损坏") from error
            decoded_size += len(decoded)
            if decoded_size > MAX_FULL_SEED_DECOMPRESSED_BYTES:
                raise FullWindowWorkerError("full seed checkpoint 解压后超过防 bomb 上限")
            chunks.append(decoded)
        if decoder is not None:
            try:
                tail = decoder.flush()
            except zlib.error as error:
                raise FullWindowWorkerError("full seed checkpoint gzip 尾部损坏") from error
            decoded_size += len(tail)
            if (
                decoded_size > MAX_FULL_SEED_DECOMPRESSED_BYTES
                or not decoder.eof
                or decoder.unused_data
            ):
                raise FullWindowWorkerError("full seed checkpoint gzip 未单成员完整闭合")
            chunks.append(tail)
        after = os.fstat(descriptor)
        if _file_identity(before) != _file_identity(after):
            raise FullWindowWorkerError("full seed checkpoint 在读取期间变化")
    finally:
        os.close(descriptor)
    try:
        decoded_bytes = b"".join(chunks)
        payload = json.loads(decoded_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FullWindowWorkerError("full seed checkpoint 不是合法 JSON") from error
    if not isinstance(payload, Mapping):
        raise FullWindowWorkerError("full seed checkpoint 顶层必须是对象")
    if decoded_bytes != (canonical_json(dict(payload)) + "\n").encode("utf-8"):
        raise FullWindowWorkerError("full seed checkpoint 解压内容不是规范 JSON")
    return dict(payload), digest.hexdigest(), size, _file_identity(after)


def _seed_event_from_verified_payload(row: Any) -> ResearchRouteEvent:
    if not isinstance(row, Mapping):
        raise FullWindowWorkerError("seed RouteEvent 必须是对象")
    try:
        path_raw = row["as_path"]
        as_path = None if path_raw is None else tuple(
            AsPathSegment(item["segment_type"], tuple(item["asns"]))
            for item in path_raw
        )
        return ResearchRouteEvent(
            artifact_id=row["artifact_id"],
            file_sha256=row["file_sha256"],
            collector_id=row["collector_id"],
            artifact_slot_utc=row["artifact_slot_utc"],
            record_ordinal=row["record_ordinal"],
            element_ordinal=row["element_ordinal"],
            route_event_id=row["route_event_id"],
            event_time_utc=row["event_time_utc"],
            peer_ip=row["peer_ip"],
            peer_asn=row["peer_asn"],
            vp_id=row["vp_id"],
            action=row["action"],
            afi_safi=row["afi_safi"],
            prefix=row["prefix"],
            as_path=as_path,
            quality_flags=tuple(row["quality_flags"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise FullWindowWorkerError("seed RouteEvent 无法恢复") from error


def _seed_evidence_rows(
    payload: Mapping[str, Any], route_state: RouteReplayState
) -> Tuple[Tuple[Mapping[str, Any], ...], Tuple[Mapping[str, Any], ...]]:
    events_raw = payload.get("route_events")
    audits_raw = payload.get("raw_audits")
    if not isinstance(events_raw, list) or not isinstance(audits_raw, list):
        raise FullWindowWorkerError("full seed checkpoint 缺少 RouteEvent/raw audit")
    try:
        audits = tuple(RawRecordEvidence(**dict(row)) for row in audits_raw)
    except (TypeError, ValueError) as error:
        raise FullWindowWorkerError("seed raw audit 无法恢复") from error
    raw_index = {(row.artifact_id, row.record_ordinal): row for row in audits}
    if len(raw_index) != len(audits):
        raise FullWindowWorkerError("seed raw audit physical record 身份重复")
    events = tuple(_seed_event_from_verified_payload(row) for row in events_raw)
    event_ids = {event.route_event_id for event in events}
    if len(event_ids) != len(events):
        raise FullWindowWorkerError("seed RouteEvent 身份重复")
    visible_ids = {entry.last_raw_ref.route_event_id for entry in route_state.entries}
    if not visible_ids.issubset(event_ids):
        raise FullWindowWorkerError("seed 可见状态未闭合到 RouteEvent")
    route_rows = []
    raw_rows = []
    for event in events:
        raw = raw_index.get((event.artifact_id, event.record_ordinal))
        if raw is None:
            raise FullWindowWorkerError("seed RouteEvent 未闭合到 physical raw record")
        route_rows.append(_event_row(event))
        raw_rows.append(_raw_ref_row(raw, event))
    raw_ids = {row["raw_record_ref_id"] for row in raw_rows}
    if len(raw_ids) != len(raw_rows) or any(
        row["raw_record_ref_id"] not in raw_ids for row in route_rows
    ):
        raise FullWindowWorkerError("seed element raw_v1 身份不闭合")
    return tuple(route_rows), tuple(raw_rows)


def load_verified_full_seed_bootstrap(
    checkpoint_path: os.PathLike[str] | str,
    *,
    selection: Mapping[str, Any],
    country_mapping: CountryMappingView,
    raw_retention_mapping: Optional[RawRetentionMappingUnion],
    seed_spool_attestation: Mapping[str, Any],
    window_end_exclusive_utc: str,
    code_identity_sha256: str,
) -> VerifiedSeedBootstrap:
    """先走既有 v2 verifier，再证明同一文件未变化并提取最小 seed 启动对象。"""

    path = Path(checkpoint_path)
    try:
        before = path.lstat()
    except OSError as error:
        raise FullWindowWorkerError("full seed checkpoint 不存在") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise FullWindowWorkerError("full seed checkpoint 必须是非符号链接普通文件")
    try:
        verification = verify_full_seed_checkpoint(
            path,
            selection=selection,
            country_mapping=country_mapping,
            raw_retention_mapping=raw_retention_mapping,
            seed_spool_attestation=seed_spool_attestation,
            pilot_end_exclusive_utc=window_end_exclusive_utc,
            code_identity_sha256=code_identity_sha256,
        )
    except ValueError as error:
        raise FullWindowWorkerError("full seed checkpoint v2 verify 失败") from error
    payload, file_sha, file_size, after_identity = _read_stable_full_seed_checkpoint(path)
    if _file_identity(before) != after_identity:
        raise FullWindowWorkerError("full seed checkpoint 在 verify 前后变化")
    semantic = dict(payload)
    fingerprint = semantic.pop("checkpoint_fingerprint_sha256", None)
    expected = hashlib.sha256(
        canonical_json(
            {
                "schema": FULL_SEED_CHECKPOINT_FINGERPRINT_SCHEMA,
                "checkpoint": semantic,
            }
        ).encode("utf-8")
    ).hexdigest()
    if (
        payload.get("schema_version") != FULL_SEED_CHECKPOINT_SCHEMA_VERSION
        or fingerprint != expected
        or fingerprint != verification.get("checkpoint_fingerprint_sha256")
    ):
        raise FullWindowWorkerError("full seed checkpoint fingerprint 未在同一文件闭合")
    position = payload.get("position")
    if not isinstance(position, Mapping) or position.get("phase") != "updates":
        raise FullWindowWorkerError("full seed checkpoint 尚未完成 seed parse")
    try:
        route_state = route_replay_state_from_payload(payload["state"])
    except (KeyError, ValueError) as error:
        raise FullWindowWorkerError("full seed checkpoint state 无法恢复") from error
    prefixes_raw = payload.get("tracked_prefixes")
    vps_raw = payload.get("observed_vp_ids")
    resources = payload.get("resources")
    progress = payload.get("seed_progress")
    if (
        not isinstance(prefixes_raw, list)
        or not isinstance(vps_raw, list)
        or not isinstance(resources, Mapping)
        or not isinstance(progress, Mapping)
    ):
        raise FullWindowWorkerError("full seed checkpoint 启动字段缺失")
    prefixes = tuple(_canonical_prefix(value) for value in prefixes_raw)
    vps = tuple(vps_raw)
    if prefixes != tuple(sorted(set(prefixes))) or vps != tuple(sorted(set(vps))):
        raise FullWindowWorkerError("full seed checkpoint prefix/VP 未排序去重")
    prior = resources.get("prior_new_raw_read_bytes")
    prior_raw_accounting = resources.get("prior_raw_accounting")
    seed_raw_reservation = resources.get("seed_raw_reservation")
    cumulative = resources.get("new_raw_read_bytes")
    if (
        isinstance(prior, bool)
        or not isinstance(prior, int)
        or prior < 0
        or not isinstance(prior_raw_accounting, Mapping)
        or not isinstance(seed_raw_reservation, Mapping)
        or isinstance(cumulative, bool)
        or not isinstance(cumulative, int)
        or cumulative < prior
    ):
        raise FullWindowWorkerError(
            "full seed checkpoint raw 累计/probe terminal accounting 非法"
        )
    seed_route_rows, seed_raw_rows = _seed_evidence_rows(payload, route_state)
    checkpoint_sequence = payload.get("checkpoint_sequence")
    if (
        isinstance(checkpoint_sequence, bool)
        or not isinstance(checkpoint_sequence, int)
        or checkpoint_sequence < 0
    ):
        raise FullWindowWorkerError("full seed checkpoint sequence 非法")
    seed_manifest = selection.get("roles", {}).get("state_seed_rib")
    if not isinstance(seed_manifest, Mapping):  # verifier 已要求；这里防止接口漂移
        raise FullWindowWorkerError("selection 缺少 state_seed_rib")
    try:
        probe_cumulative = prior_raw_accounting.get(
            "cumulative_reserved_new_raw_bytes"
        )
        if (
            isinstance(probe_cumulative, bool)
            or not isinstance(probe_cumulative, int)
            or probe_cumulative < 0
        ):
            raise ValueError("probe cumulative 非法")
        normalized_probe_accounting = _verified_probe_terminal_accounting(
            prior_raw_accounting,
            expected_prior_raw_bytes=probe_cumulative,
            selection_id=str(selection.get("selection_id")),
            selection_sha256=str(
                selection.get("semantic_fingerprint_sha256")
            ),
            code_identity_sha256=code_identity_sha256,
        )
        normalized_seed_reservation = _verified_seed_raw_reservation(
            seed_raw_reservation,
            probe_accounting=normalized_probe_accounting,
            expected_prior_raw_bytes=prior,
            selection_id=str(selection.get("selection_id")),
            seed_artifact=seed_manifest,
            code_identity_sha256=code_identity_sha256,
        )
        if normalized_seed_reservation[
            "cumulative_reserved_new_raw_bytes"
        ] != cumulative:
            raise ValueError("seed reservation cumulative 与 checkpoint 不一致")
    except ValueError as error:
        raise FullWindowWorkerError(
            "full seed checkpoint probe/seed durable raw accounting 无法再次闭合"
        ) from error
    try:
        normalized_spool_attestation = validate_seed_spool_attestation(
            seed_spool_attestation,
            seed_artifact=seed_manifest,
        )
    except ValueError as error:
        raise FullWindowWorkerError("seed spool attestation 无法再次闭合") from error
    expected_vp_ids_sha256 = _semantic_sha256(
        "rrc25_seed_expected_vp_ids_v1", list(vps)
    )
    vp_source_sha = hashlib.sha256(
        canonical_json(
            {
                "schema": "rrc25_seed_vp_population_source_v1",
                "checkpoint_file_sha256": file_sha,
                "checkpoint_fingerprint_sha256": fingerprint,
                "expected_vp_ids": list(vps),
            }
        ).encode("utf-8")
    ).hexdigest()
    checkpoint_binding_fields = (
        "code_identity_sha256",
        "selection_id",
        "selection_semantic_fingerprint_sha256",
        "mapping_fingerprint_sha256",
        "raw_retention_mapping_kind",
        "raw_retention_mapping_fingerprint_sha256",
        "seed_spool_attestation_fingerprint_sha256",
        "pilot_start_utc",
        "pilot_end_exclusive_utc",
    )
    seed_route_state_payload = route_replay_state_to_payload(route_state)
    checkpoint_context = {
        "checkpoint": {
            "schema_version": payload["schema_version"],
            "file_sha256": file_sha,
            "size_bytes": file_size,
            "checkpoint_fingerprint_sha256": fingerprint,
            "checkpoint_sequence": checkpoint_sequence,
            "checkpoint_bytes_packaged": False,
            "packaging_limitation": "checkpoint_identity_hash_only_not_checkpoint_bytes",
        },
        "checkpoint_bindings": {
            field: payload[field] for field in checkpoint_binding_fields
        },
        "position": dict(payload["position"]),
        "seed_progress": dict(payload["seed_progress"]),
        "resume_policy": payload["resume_policy"],
        "checkpoint_policy": dict(payload["checkpoint_policy"]),
        # verifier 已将该摘要与 selection/code/checkpoint fingerprint 闭合。
        # 继续将整个 terminal 摘要放入 bootstrap attestation，使
        # checkpoint 字节未封包时，offline finalizer 仍能看到 prior 的
        # create-only ledger 身份，而不是只剩一个可手填的整数。
        "prior_raw_accounting": dict(normalized_probe_accounting),
        "seed_raw_reservation": dict(normalized_seed_reservation),
        "seed_artifact_ref": {
            "artifact_id": progress.get("artifact_id"),
            "file_sha256": progress.get("file_sha256"),
            "size_bytes": progress.get("size_bytes"),
        },
        "expected_vp_ids": list(vps),
        "expected_vp_ids_sha256": expected_vp_ids_sha256,
        "vp_population_source_sha256": vp_source_sha,
        "tracked_prefixes": list(prefixes),
        "tracked_prefixes_sha256": _semantic_sha256(
            "rrc25_seed_tracked_prefixes_v1", list(prefixes)
        ),
        "seed_route_state": seed_route_state_payload,
        "route_state_semantic_sha256": _semantic_sha256(
            "rrc25_seed_route_state_v1",
            seed_route_state_payload,
        ),
        "seed_route_events_semantic_sha256": _semantic_sha256(
            "rrc25_seed_route_events_v1", list(seed_route_rows)
        ),
        "seed_raw_record_refs_semantic_sha256": _semantic_sha256(
            "rrc25_seed_raw_record_refs_v1", list(seed_raw_rows)
        ),
        "gaps": list(payload["gaps"]),
        "errors": list(payload["errors"]),
    }
    return VerifiedSeedBootstrap(
        checkpoint_path=path,
        checkpoint_file_sha256=file_sha,
        checkpoint_size_bytes=file_size,
        checkpoint_fingerprint_sha256=str(fingerprint),
        checkpoint_sequence=checkpoint_sequence,
        route_state=route_state,
        tracked_prefixes=prefixes,
        expected_vp_ids=vps,
        prior_raw_read_bytes=prior,
        seed_artifact_read_bytes=cumulative - prior,
        seed_artifact_ref={
            "artifact_id": progress.get("artifact_id"),
            "file_sha256": progress.get("file_sha256"),
            "size_bytes": progress.get("size_bytes"),
            "checkpoint_file_sha256": file_sha,
            "checkpoint_fingerprint_sha256": fingerprint,
        },
        seed_route_event_rows=seed_route_rows,
        seed_raw_record_ref_rows=seed_raw_rows,
        checkpoint_bootstrap_context=checkpoint_context,
        seed_spool_attestation=dict(normalized_spool_attestation),
        seed_parser_attestation=_seed_parser_attestation(code_identity_sha256),
    )


def _canonical_json_file_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        (canonical_json(dict(value)) + "\n").encode("utf-8")
    ).hexdigest()


def _validated_seed_retirement_binding(
    value: Any,
    *,
    bootstrap: VerifiedSeedBootstrap,
    additional_pre_update_raw_read_bytes: int,
) -> Mapping[str, Any]:
    """复核 CLI 已验证的退役事实，再把完整规范收据绑定进 genesis。"""

    required = {
        "schema_version",
        "success_receipt",
        "success_receipt_file_sha256",
        "raw_attempt_receipt",
        "raw_attempt_receipt_file_sha256",
        "spool_absence_verified",
        "compressed_raw_stable_identity_verified",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise FullWindowWorkerError("seed retirement bootstrap binding 字段不闭合")
    if value.get("schema_version") != SEED_RETIREMENT_BOOTSTRAP_BINDING_SCHEMA_VERSION:
        raise FullWindowWorkerError("seed retirement bootstrap binding schema 不支持")
    receipt = value.get("success_receipt")
    attempt = value.get("raw_attempt_receipt")
    if not isinstance(receipt, Mapping) or not isinstance(attempt, Mapping):
        raise FullWindowWorkerError("seed retirement receipt/attempt 必须是对象")
    if (
        _sha(value.get("success_receipt_file_sha256"), "retirement receipt SHA")
        != _canonical_json_file_sha256(receipt)
        or _sha(value.get("raw_attempt_receipt_file_sha256"), "retirement attempt SHA")
        != _canonical_json_file_sha256(attempt)
    ):
        raise FullWindowWorkerError("seed retirement 规范文件 SHA 不闭合")
    if (
        value.get("spool_absence_verified") is not True
        or value.get("compressed_raw_stable_identity_verified") is not True
        or receipt.get("schema_version")
        != "rrc25-seed-spool-retirement-receipt/v2"
        or attempt.get("schema_version")
        != "rrc25-seed-spool-retirement-raw-attempt-receipt/v1"
        or receipt.get("operation") != "seed_spool_retirement"
        or receipt.get("recoverable_by_rebuild_from_compressed_raw") is not True
    ):
        raise FullWindowWorkerError("seed retirement 未证明成功退役/可重建语义")
    checkpoint = receipt.get("checkpoint")
    checkpoint_context = bootstrap.checkpoint_bootstrap_context.get("checkpoint")
    if not isinstance(checkpoint, Mapping) or not isinstance(
        checkpoint_context, Mapping
    ):
        raise FullWindowWorkerError("seed retirement 缺少 checkpoint 绑定")
    if (
        checkpoint.get("checkpoint_sequence")
        != checkpoint_context.get("checkpoint_sequence")
        or checkpoint.get("checkpoint_fingerprint_sha256")
        != checkpoint_context.get("checkpoint_fingerprint_sha256")
    ):
        raise FullWindowWorkerError("seed retirement 与 bootstrap checkpoint 不一致")
    compressed = receipt.get("compressed_raw")
    if not isinstance(compressed, Mapping) or (
        compressed.get("artifact_id") != bootstrap.seed_artifact_ref.get("artifact_id")
        or compressed.get("sha256") != bootstrap.seed_artifact_ref.get("file_sha256")
        or compressed.get("size_bytes") != bootstrap.seed_artifact_ref.get("size_bytes")
        or compressed.get("hash_verified") is not True
    ):
        raise FullWindowWorkerError("seed retirement compressed raw 身份不闭合")
    attempt_ref = receipt.get("raw_verification_attempt_receipt")
    if not isinstance(attempt_ref, Mapping) or (
        attempt_ref.get("receipt_fingerprint_sha256")
        != attempt.get("receipt_fingerprint_sha256")
        or attempt_ref.get("attempt_id") != attempt.get("attempt_id")
        or attempt_ref.get("status") != attempt.get("status")
    ):
        raise FullWindowWorkerError("seed retirement attempt 引用不闭合")
    receipt_accounting = receipt.get("resource_accounting")
    attempt_accounting = attempt.get("raw_accounting")
    expected_checkpoint_cumulative = (
        bootstrap.prior_raw_read_bytes + bootstrap.seed_artifact_read_bytes
    )
    expected_after = expected_checkpoint_cumulative + additional_pre_update_raw_read_bytes
    if not isinstance(receipt_accounting, Mapping) or not isinstance(
        attempt_accounting, Mapping
    ) or (
        receipt_accounting.get("checkpoint_cumulative_new_raw_read_bytes")
        != expected_checkpoint_cumulative
        or receipt_accounting.get(
            "cumulative_new_raw_read_bytes_after_retirement_verification"
        )
        != expected_after
        or attempt_accounting.get(
            "cumulative_new_raw_read_bytes_after_reservation"
        )
        != expected_after
        or attempt_accounting.get("checkpoint_cumulative_new_raw_read_bytes")
        != expected_checkpoint_cumulative
    ):
        raise FullWindowWorkerError("seed retirement raw accounting 不闭合")
    return {
        field: (dict(value[field]) if isinstance(value[field], Mapping) else value[field])
        for field in sorted(required)
    }


def initialize_journal_from_verified_seed(
    output_root: os.PathLike[str] | str,
    *,
    bootstrap: VerifiedSeedBootstrap,
    run_id: str,
    bindings: Mapping[str, str],
    total_update_artifacts: int,
    compatible_mapping: CountryMappingView,
    revised_mapping: CountryMappingView,
    additional_pre_update_raw_read_bytes: int,
    bootstrap_bytes_per_second: float,
    retained_external_temporary_bytes: int,
    seed_retirement_binding: Mapping[str, Any],
) -> JournalHead:
    """用实际 seed receipt 累计初始化 UPDATE raw ledger，不自行推断读取量。"""

    if not isinstance(bootstrap, VerifiedSeedBootstrap):
        raise FullWindowWorkerError("bootstrap 必须来自 full seed v2 loader")
    if (
        isinstance(additional_pre_update_raw_read_bytes, bool)
        or not isinstance(additional_pre_update_raw_read_bytes, int)
        or additional_pre_update_raw_read_bytes < 0
    ):
        raise FullWindowWorkerError("additional pre-update raw bytes 非法")
    vp_source_sha = hashlib.sha256(
        canonical_json(
            {
                "schema": "rrc25_seed_vp_population_source_v1",
                "checkpoint_file_sha256": bootstrap.checkpoint_file_sha256,
                "checkpoint_fingerprint_sha256": bootstrap.checkpoint_fingerprint_sha256,
                "expected_vp_ids": list(bootstrap.expected_vp_ids),
            }
        ).encode("utf-8")
    ).hexdigest()
    compact = initialize_compact_state_from_seed(
        bootstrap.route_state,
        compatible_mapping=compatible_mapping,
        revised_mapping=revised_mapping,
        tracked_prefixes=bootstrap.tracked_prefixes,
        expected_vp_ids=bootstrap.expected_vp_ids,
        vp_population_source_sha256=vp_source_sha,
    )
    retirement = _validated_seed_retirement_binding(
        seed_retirement_binding,
        bootstrap=bootstrap,
        additional_pre_update_raw_read_bytes=additional_pre_update_raw_read_bytes,
    )
    if bootstrap.checkpoint_bootstrap_context.get(
        "vp_population_source_sha256"
    ) != vp_source_sha:
        raise FullWindowWorkerError("seed bootstrap VP source identity 漂移")
    attestation_semantic = {
        "schema_version": SEED_BOOTSTRAP_ATTESTATION_SCHEMA_VERSION,
        **dict(bootstrap.checkpoint_bootstrap_context),
        "seed_spool_attestation": dict(bootstrap.seed_spool_attestation),
        "seed_parser": dict(bootstrap.seed_parser_attestation),
        "seed_retirement": retirement,
        "initial_compact_state": dict(compact),
        "initial_compact_state_semantic_sha256": _semantic_sha256(
            "rrc25_full_window_initial_compact_state_v1", compact
        ),
        "offline_verification_scope": (
            "checkpoint_identity_and_seed_evidence_projection_without_checkpoint_bytes"
        ),
    }
    seed_bootstrap_attestation = {
        **attestation_semantic,
        "attestation_fingerprint_sha256": _semantic_sha256(
            SEED_BOOTSTRAP_ATTESTATION_FINGERPRINT_SCHEMA,
            attestation_semantic,
        ),
    }
    return initialize_full_window_journal(
        output_root,
        run_id=run_id,
        bindings=bindings,
        total_artifacts=total_update_artifacts,
        initial_compact_state=compact,
        preliminary_seed_read_bytes=bootstrap.prior_raw_read_bytes,
        seed_artifact_read_bytes=bootstrap.seed_artifact_read_bytes,
        additional_pre_update_raw_read_bytes=additional_pre_update_raw_read_bytes,
        bootstrap_bytes_per_second=bootstrap_bytes_per_second,
        genesis_shards=(
            ShardInput(
                "seed_bootstrap_attestation", (seed_bootstrap_attestation,)
            ),
            ShardInput("seed_route_events", bootstrap.seed_route_event_rows),
            ShardInput("seed_raw_record_refs", bootstrap.seed_raw_record_ref_rows),
        ),
        retained_external_temporary_bytes=retained_external_temporary_bytes,
    )


def initialize_compact_state_from_seed(
    seed_state: RouteReplayState,
    *,
    compatible_mapping: CountryMappingView,
    revised_mapping: CountryMappingView,
    tracked_prefixes: Iterable[str],
    expected_vp_ids: Iterable[str],
    vp_population_source_sha256: str,
) -> Mapping[str, Any]:
    """把已验证完整 seed 降为可轮换 scratch；不保留全历史 RouteEvent ID。"""

    if not isinstance(seed_state, RouteReplayState):
        raise FullWindowWorkerError("seed_state 必须是 RouteReplayState")
    if compatible_mapping.view != "compatible":
        raise FullWindowWorkerError("主曲线必须使用 compatible mapping")
    if revised_mapping.view != "revised":
        raise FullWindowWorkerError("第二视图必须使用 revised mapping")
    if (
        revised_mapping.target_country != compatible_mapping.target_country
        or revised_mapping.source_sha256 == compatible_mapping.source_sha256
        and revised_mapping.source_ref == compatible_mapping.source_ref
        and revised_mapping.assignments == compatible_mapping.assignments
    ):
        raise FullWindowWorkerError("revised mapping 必须是同一目标国的独立冻结视图")
    projection = _mapped_projection_for_view(seed_state, compatible_mapping)
    revised_projection = _mapped_projection_for_view(seed_state, revised_mapping)
    contexts = _target_contexts(projection)
    members = tuple((asn, None, True) for asn in sorted({row[0] for row in contexts}))
    references = tuple((asn, afi, prefix, None, True) for asn, afi, prefix in contexts)
    revised_contexts = _target_contexts(revised_projection)
    revised_members = tuple(
        (asn, None, True) for asn in sorted({row[0] for row in revised_contexts})
    )
    revised_references = tuple(
        (asn, afi, prefix, None, True)
        for asn, afi, prefix in revised_contexts
    )
    prefixes = tuple(sorted({_canonical_prefix(value) for value in tracked_prefixes}))
    state_prefixes = {entry.key.prefix for entry in seed_state.entries}
    if not state_prefixes.issubset(set(prefixes)):
        raise FullWindowWorkerError("tracked_prefixes 未覆盖 seed replay state")
    vps = tuple(sorted(set(expected_vp_ids) | {entry.key.vp_id for entry in seed_state.entries}))
    if any(not isinstance(value, str) or not value for value in vps):
        raise FullWindowWorkerError("expected_vp_ids 非法")
    cursor_id = None
    if seed_state.last_order_key is not None:
        # seed state 的 latest_changes 已由完整 checkpoint 验证；任取其最大 raw
        # 身份只用于满足 compact payload 的引用闭合，不作为排序来源。
        if not seed_state.processed_route_event_ids:
            raise FullWindowWorkerError("seed state cursor 缺少 RouteEvent 身份")
        cursor_id = sorted(seed_state.processed_route_event_ids)[-1]
    compact = CompactState(
        route_state=seed_state,
        cursor_route_event_id=cursor_id,
        tracked_prefixes=prefixes,
        known_vp_ids=vps,
        vp_population_source_sha256=_sha(
            vp_population_source_sha256, "vp_population_source_sha256"
        ),
        compatible_mapping_fingerprint_sha256=_mapping_view_fingerprint(
            compatible_mapping
        ),
        revised_mapping_fingerprint_sha256=_mapping_view_fingerprint(
            revised_mapping
        ),
        peer_session_states=tuple(
            (vp_id, "observable", "seed_route_population", None, None, None)
            for vp_id in vps
        ),
        cohort_members=members,
        cohort_references=references,
        revised_cohort_members=revised_members,
        revised_cohort_references=revised_references,
        last_slot_end_exclusive_utc=None,
    )
    return compact_state_to_payload(compact)


def compact_state_to_payload(state: CompactState) -> Mapping[str, Any]:
    if not isinstance(state, CompactState):
        raise FullWindowWorkerError("state 必须是 CompactState")
    return {
        "schema_version": COMPACT_STATE_SCHEMA_VERSION,
        "route_state": _compact_route_state(
            state.route_state,
            cursor_route_event_id=state.cursor_route_event_id,
        ),
        "cursor_route_event_id": state.cursor_route_event_id,
        "tracked_prefixes": list(state.tracked_prefixes),
        "vp_population": {
            "definition": VP_POPULATION_SEMANTICS,
            "source_sha256": state.vp_population_source_sha256,
            "known_vp_ids": list(state.known_vp_ids),
            "peer_sessions": [
                {
                    "vp_id": vp_id,
                    "availability": availability,
                    "basis": basis,
                    "last_observed_at": last_observed_at,
                    "last_control_observation_id": control_id,
                    "last_control_raw_record_sha256": control_sha,
                }
                for (
                    vp_id,
                    availability,
                    basis,
                    last_observed_at,
                    control_id,
                    control_sha,
                ) in state.peer_session_states
            ],
        },
        "mapping_views": {
            "compatible_fingerprint_sha256": state.compatible_mapping_fingerprint_sha256,
            "revised_fingerprint_sha256": state.revised_mapping_fingerprint_sha256,
            "required_measurement_views": ["compatibility", "revised"],
        },
        "compatible_cohort": {
            "membership_semantics": "baseline_plus_dynamic_first_seen_no_backfill_v1",
            "members": [
                {"asn": asn, "first_seen_at": first_seen, "baseline_member": baseline}
                for asn, first_seen, baseline in state.cohort_members
            ],
            "references": [
                {
                    "asn": asn,
                    "afi": afi,
                    "prefix": prefix,
                    "first_seen_at": first_seen,
                    "baseline_member": baseline,
                }
                for asn, afi, prefix, first_seen, baseline in state.cohort_references
            ],
        },
        "revised_cohort": {
            "membership_semantics": "baseline_plus_dynamic_first_seen_no_backfill_v1",
            "members": [
                {"asn": asn, "first_seen_at": first_seen, "baseline_member": baseline}
                for asn, first_seen, baseline in state.revised_cohort_members
            ],
            "references": [
                {
                    "asn": asn,
                    "afi": afi,
                    "prefix": prefix,
                    "first_seen_at": first_seen,
                    "baseline_member": baseline,
                }
                for asn, afi, prefix, first_seen, baseline in state.revised_cohort_references
            ],
        },
        "last_slot_end_exclusive_utc": state.last_slot_end_exclusive_utc,
    }


def compact_state_from_payload(payload: Any) -> CompactState:
    required = {
        "schema_version",
        "route_state",
        "cursor_route_event_id",
        "tracked_prefixes",
        "vp_population",
        "mapping_views",
        "compatible_cohort",
        "revised_cohort",
        "last_slot_end_exclusive_utc",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise FullWindowWorkerError("compact state 字段不闭合")
    if payload["schema_version"] != COMPACT_STATE_SCHEMA_VERSION:
        raise FullWindowWorkerError("compact state schema 不受支持")
    try:
        route_state = route_replay_state_from_payload(payload["route_state"])
    except ValueError as error:
        raise FullWindowWorkerError("compact route state 无法恢复") from error
    cursor = payload["cursor_route_event_id"]
    if cursor is not None and (
        not isinstance(cursor, str) or cursor not in route_state.processed_route_event_ids
    ):
        raise FullWindowWorkerError("compact cursor_route_event_id 非法")
    prefixes_raw = payload["tracked_prefixes"]
    if not isinstance(prefixes_raw, list):
        raise FullWindowWorkerError("tracked_prefixes 必须是数组")
    prefixes = tuple(_canonical_prefix(value) for value in prefixes_raw)
    if prefixes != tuple(sorted(set(prefixes))):
        raise FullWindowWorkerError("tracked_prefixes 必须去重排序")
    vp = payload["vp_population"]
    if not isinstance(vp, Mapping) or set(vp) != {
        "definition", "source_sha256", "known_vp_ids", "peer_sessions"
    }:
        raise FullWindowWorkerError("vp_population 字段不闭合")
    if vp["definition"] != VP_POPULATION_SEMANTICS:
        raise FullWindowWorkerError("vp population definition 非法")
    known_raw = vp["known_vp_ids"]
    if not isinstance(known_raw, list) or any(not isinstance(value, str) or not value for value in known_raw):
        raise FullWindowWorkerError("known_vp_ids 非法")
    known = tuple(known_raw)
    if known != tuple(sorted(set(known))):
        raise FullWindowWorkerError("known_vp_ids 必须去重排序")
    sessions_raw = vp["peer_sessions"]
    if not isinstance(sessions_raw, list):
        raise FullWindowWorkerError("peer_sessions 必须是数组")
    sessions = []
    for row in sessions_raw:
        required_session = {
            "vp_id",
            "availability",
            "basis",
            "last_observed_at",
            "last_control_observation_id",
            "last_control_raw_record_sha256",
        }
        if not isinstance(row, Mapping) or set(row) != required_session:
            raise FullWindowWorkerError("peer session state 字段不闭合")
        vp_id = row["vp_id"]
        availability = row["availability"]
        basis = row["basis"]
        if vp_id not in known or availability not in {"observable", "down", "unknown"}:
            raise FullWindowWorkerError("peer session VP/availability 非法")
        if basis not in {"seed_route_population", "update_message", "state_change"}:
            raise FullWindowWorkerError("peer session basis 非法")
        last_observed_at = row["last_observed_at"]
        if last_observed_at is not None:
            last_observed_at = _utc_event(last_observed_at, "session.last_observed_at")
        control_id = row["last_control_observation_id"]
        control_sha = row["last_control_raw_record_sha256"]
        if (control_id is None) != (control_sha is None):
            raise FullWindowWorkerError("peer session control ref 不闭合")
        if control_sha is not None:
            _sha(control_sha, "session.control raw SHA")
        sessions.append(
            (vp_id, availability, basis, last_observed_at, control_id, control_sha)
        )
    sessions_tuple = tuple(sorted(sessions))
    if len(sessions_tuple) != len(known) or {row[0] for row in sessions_tuple} != set(known):
        raise FullWindowWorkerError("peer session state 未精确覆盖 known VP")
    mapping_views = payload["mapping_views"]
    if not isinstance(mapping_views, Mapping) or set(mapping_views) != {
        "compatible_fingerprint_sha256",
        "revised_fingerprint_sha256",
        "required_measurement_views",
    } or mapping_views["required_measurement_views"] != [
        "compatibility",
        "revised",
    ]:
        raise FullWindowWorkerError("mapping_views 字段不闭合")
    compatible_mapping_fingerprint = _sha(
        mapping_views["compatible_fingerprint_sha256"],
        "compatible mapping fingerprint",
    )
    revised_mapping_fingerprint = _sha(
        mapping_views["revised_fingerprint_sha256"],
        "revised mapping fingerprint",
    )
    def parse_cohort(field: str) -> Tuple[
        Tuple[Tuple[int, Optional[str], bool], ...],
        Tuple[Tuple[int, str, str, Optional[str], bool], ...],
    ]:
        cohort = payload[field]
        if not isinstance(cohort, Mapping) or set(cohort) != {
            "membership_semantics",
            "members",
            "references",
        } or cohort["membership_semantics"] != "baseline_plus_dynamic_first_seen_no_backfill_v1":
            raise FullWindowWorkerError(f"{field} 字段不闭合")
        members = []
        for row in cohort["members"]:
            if not isinstance(row, Mapping) or set(row) != {
                "asn", "first_seen_at", "baseline_member"
            }:
                raise FullWindowWorkerError(f"{field} member 字段不闭合")
            first_seen = row["first_seen_at"]
            if first_seen is not None:
                first_seen = _utc_event(first_seen, "member.first_seen_at")
            asn = row["asn"]
            if isinstance(asn, bool) or not isinstance(asn, int) or asn <= 0:
                raise FullWindowWorkerError(f"{field} member ASN 非法")
            if type(row["baseline_member"]) is not bool:
                raise FullWindowWorkerError(f"{field} baseline_member 非法")
            if row["baseline_member"] != (first_seen is None):
                raise FullWindowWorkerError(f"{field} baseline/first_seen 语义矛盾")
            members.append((asn, first_seen, row["baseline_member"]))
        references = []
        for row in cohort["references"]:
            if not isinstance(row, Mapping) or set(row) != {
                "asn", "afi", "prefix", "first_seen_at", "baseline_member"
            }:
                raise FullWindowWorkerError(f"{field} reference 字段不闭合")
            if row["afi"] not in {"ipv4", "ipv6"}:
                raise FullWindowWorkerError(f"{field} reference afi 非法")
            prefix = _canonical_prefix(row["prefix"])
            expected_afi = "ipv4" if ":" not in prefix else "ipv6"
            if row["afi"] != expected_afi:
                raise FullWindowWorkerError(f"{field} reference prefix/afi 冲突")
            first_seen = row["first_seen_at"]
            if first_seen is not None:
                first_seen = _utc_event(first_seen, "reference.first_seen_at")
            if row["baseline_member"] != (first_seen is None):
                raise FullWindowWorkerError(f"{field} reference baseline/first_seen 矛盾")
            references.append(
                (row["asn"], row["afi"], prefix, first_seen, row["baseline_member"])
            )
        return tuple(sorted(members)), tuple(sorted(references))

    members, references = parse_cohort("compatible_cohort")
    revised_members, revised_references = parse_cohort("revised_cohort")
    last = payload["last_slot_end_exclusive_utc"]
    if last is not None:
        last = _utc(last, "last_slot_end_exclusive_utc")
    return CompactState(
        route_state=route_state,
        cursor_route_event_id=cursor,
        tracked_prefixes=prefixes,
        known_vp_ids=known,
        vp_population_source_sha256=_sha(vp["source_sha256"], "vp source SHA"),
        compatible_mapping_fingerprint_sha256=compatible_mapping_fingerprint,
        revised_mapping_fingerprint_sha256=revised_mapping_fingerprint,
        peer_session_states=sessions_tuple,
        cohort_members=members,
        cohort_references=references,
        revised_cohort_members=revised_members,
        revised_cohort_references=revised_references,
        last_slot_end_exclusive_utc=last,
    )


def artifact_descriptor_from_manifest(index: int, artifact: Mapping[str, Any]) -> ArtifactDescriptor:
    if not isinstance(artifact, Mapping):
        raise FullWindowWorkerError("artifact manifest row 必须是对象")
    slot = _utc(artifact.get("artifact_time_utc"), "artifact_time_utc")
    start = datetime.fromisoformat(slot[:-1] + "+00:00")
    end = start + timedelta(minutes=5)
    return ArtifactDescriptor(
        index=index,
        artifact_id=artifact.get("artifact_id"),
        file_sha256=artifact.get("file_sha256"),
        size_bytes=artifact.get("size_bytes"),
        collector_id=artifact.get("collector_id"),
        slot_start_utc=slot,
        slot_end_exclusive_utc=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _possible_target(element: ParsedRouteElement, membership: RawRetentionMembership) -> bool:
    if element.action == "withdraw":
        return False
    if element.as_path is None:
        return True
    resolution = derive_origin_asns(element.as_path)
    if resolution.state == UNKNOWN:
        return True
    return any(membership(asn) is not False for asn in resolution.origins)


def _retention_selector(
    tracked: set[str],
    membership: RawRetentionMembership,
) -> Callable[[Tuple[ParsedRouteElement, ...]], Tuple[bool, ...]]:
    def select(elements: Tuple[ParsedRouteElement, ...]) -> Tuple[bool, ...]:
        canonical = {
            element.prefix: ipaddress.ip_network(element.prefix, strict=False).compressed
            for element in elements
        }
        by_prefix: dict[str, list[ParsedRouteElement]] = {}
        for element in elements:
            by_prefix.setdefault(canonical[element.prefix], []).append(element)
        retained = set()
        for prefix, values in by_prefix.items():
            possible = any(
                _possible_target(element, membership)
                for element in values
                if element.action == "announce"
            )
            if prefix in tracked or possible:
                retained.add(prefix)
            if possible:
                tracked.add(prefix)
        return tuple(canonical[element.prefix] in retained for element in elements)

    return select


def raw_record_ref_id_v1(
    file_sha256: str, record_ordinal: int, element_ordinal: int
) -> str:
    identity = {
        "schema": "raw_record_ref_id_v1",
        "file_sha256": file_sha256,
        "record_ordinal": record_ordinal,
        "element_ordinal": element_ordinal,
    }
    return "raw_v1_" + hashlib.sha256(
        canonical_json(identity).encode("utf-8")
    ).hexdigest()[:32]


def _event_row(event: ResearchRouteEvent) -> Mapping[str, Any]:
    raw_id = raw_record_ref_id_v1(
        event.file_sha256, event.record_ordinal, event.element_ordinal
    )
    return {
        "schema_version": ROUTE_EVENT_SHARD_SCHEMA_VERSION,
        "route_event_id": event.route_event_id,
        "artifact_id": event.artifact_id,
        "file_sha256": event.file_sha256,
        "collector_id": event.collector_id,
        "artifact_slot_utc": event.artifact_slot_utc,
        "record_ordinal": event.record_ordinal,
        "element_ordinal": event.element_ordinal,
        "event_time_utc": event.event_time_utc,
        "peer_ip": event.peer_ip,
        "peer_asn": event.peer_asn,
        "vp_id": event.vp_id,
        "action": event.action,
        "afi_safi": event.afi_safi,
        "prefix": event.prefix,
        "as_path": None if event.as_path is None else [
            {"segment_type": segment.segment_type, "asns": list(segment.asns)}
            for segment in event.as_path
        ],
        "quality_flags": list(event.quality_flags),
        "raw_record_ref_id": raw_id,
        "raw_record_ref_ids": [raw_id],
    }


def _raw_ref_row(
    raw: RawRecordEvidence, event: ResearchRouteEvent
) -> Mapping[str, Any]:
    if (
        event.artifact_id != raw.artifact_id
        or event.file_sha256 != raw.file_sha256
        or event.record_ordinal != raw.record_ordinal
    ):
        raise FullWindowWorkerError("RouteEvent 与 physical raw record 不闭合")
    raw_id = raw_record_ref_id_v1(
        event.file_sha256, event.record_ordinal, event.element_ordinal
    )
    return {
        "schema_version": RAW_RECORD_REF_SHARD_SCHEMA_VERSION,
        "raw_record_ref_id": raw_id,
        "route_event_id": event.route_event_id,
        "artifact_id": raw.artifact_id,
        "file_sha256": raw.file_sha256,
        "artifact_slot_utc": raw.artifact_slot_utc,
        "record_ordinal": raw.record_ordinal,
        "element_ordinal": event.element_ordinal,
        "record_offset": raw.record_offset,
        "record_length": raw.record_length,
        "record_hash": raw.raw_record_sha256,
        "raw_record_sha256": raw.raw_record_sha256,
        "verification_status": "verified",
        "verification_basis": "complete_artifact_single_pass_sha256_and_record_hash",
    }


def _control_row(record: AdaptedUpdateRecord) -> Mapping[str, Any]:
    raw = record.raw_record
    session = record.peer_session_observation
    return {
        "schema_version": CONTROL_RECORD_SHARD_SCHEMA_VERSION,
        "record_kind": record.record_kind,
        "artifact_id": raw.artifact_id,
        "file_sha256": raw.file_sha256,
        "collector_id": raw.collector_id,
        "artifact_slot_utc": raw.artifact_slot_utc,
        "record_ordinal": raw.record_ordinal,
        "record_offset": raw.record_offset,
        "record_length": raw.record_length,
        "raw_record_sha256": raw.raw_record_sha256,
        "event_time_utc": raw.event_time_utc,
        "mrt_type": raw.mrt_type,
        "mrt_subtype": raw.mrt_subtype,
        "route_event_ids": [],
        "peer_session_observation": asdict(session) if session is not None else None,
        "control_record_semantics": (
            "control_observation_not_session_close_confirmation"
            if session is not None
            else "control_record_not_route_element_evidence"
        ),
    }


def _record_observation_row(record: AdaptedUpdateRecord) -> Mapping[str, Any]:
    """保存独立逐槽复算所需的完整 physical-record 计数与 peer 观测。"""

    raw = record.raw_record
    session = record.peer_session_observation
    return {
        "schema_version": RECORD_OBSERVATION_SHARD_SCHEMA_VERSION,
        "artifact_id": raw.artifact_id,
        "file_sha256": raw.file_sha256,
        "collector_id": raw.collector_id,
        "artifact_slot_utc": raw.artifact_slot_utc,
        "record_ordinal": raw.record_ordinal,
        "record_offset": raw.record_offset,
        "record_length": raw.record_length,
        "raw_record_sha256": raw.raw_record_sha256,
        "event_time_utc": raw.event_time_utc,
        "record_kind": record.record_kind,
        "announce_count": record.announce_count,
        "withdraw_count": record.withdraw_count,
        "update_peer_observations": [
            {"vp_id": vp_id, "event_time_utc": event_time_utc}
            for vp_id, event_time_utc in record.update_peer_observations
        ],
        "peer_session_observation": (
            asdict(session) if session is not None else None
        ),
        "semantics": (
            "complete_physical_record_observation_for_independent_slot_derivation"
        ),
    }


def _mapped_announce_discoveries(
    events: Sequence[ResearchRouteEvent], mapping: CountryMappingView
) -> Tuple[Mapping[str, Any], ...]:
    """从本槽每条确定 target announce 提取精确 first_seen，不依赖槽末可见性。"""

    by_reference: dict[Tuple[int, str, str], Mapping[str, Any]] = {}
    for event in events:
        if event.action != "announce" or event.as_path is None:
            continue
        resolution = derive_origin_asns(event.as_path)
        if resolution.state != RESOLVED or len(resolution.origins) != 1:
            continue
        asn = resolution.origins[0]
        if mapping.target_membership(asn) is not True:
            continue
        afi = "ipv4" if event.afi_safi == "ipv4_unicast" else "ipv6"
        key = (asn, afi, event.prefix)
        row = {
            "asn": asn,
            "afi": afi,
            "prefix": event.prefix,
            "first_seen_at": _utc_event(event.event_time_utc, "event_time_utc"),
            "route_event_id": event.route_event_id,
            "raw_record_ref_id": raw_record_ref_id_v1(
                event.file_sha256, event.record_ordinal, event.element_ordinal
            ),
        }
        prior = by_reference.get(key)
        if prior is None or (
            row["first_seen_at"], row["route_event_id"]
        ) < (prior["first_seen_at"], prior["route_event_id"]):
            by_reference[key] = row
    return tuple(by_reference[key] for key in sorted(by_reference))


def _address_union(prefixes: Iterable[str], *, version: int) -> int:
    networks = []
    for prefix in sorted(set(prefixes)):
        network = ipaddress.ip_network(prefix, strict=True)
        if network.version != version:
            raise FullWindowWorkerError("address union prefix 地址族冲突")
        networks.append(network)
    return sum(network.num_addresses for network in ipaddress.collapse_addresses(networks))


def _equivalent(addresses: int, denominator: int) -> int | float:
    return addresses // denominator if addresses % denominator == 0 else addresses / denominator


def _country_slot(
    snapshot: ReplaySnapshot,
    projection: Any,
    state: CompactState,
    *,
    full_announce_count: int,
    full_withdraw_count: int,
    retained_announce_count: int,
    retained_withdraw_count: int,
    physical_record_count: int,
    update_active_vps: Iterable[str],
    announce_discoveries: Sequence[Mapping[str, Any]],
    peer_session_states: Sequence[
        Tuple[str, str, str, Optional[str], Optional[str], Optional[str]]
    ],
    full_state_visible_vps: Iterable[str],
    observable_state_visible_vps: Iterable[str],
    mapping: CountryMappingView,
    cohort_members: Sequence[Tuple[int, Optional[str], bool]],
    cohort_references: Sequence[
        Tuple[int, str, str, Optional[str], bool]
    ],
    main_curve: bool,
) -> Tuple[Mapping[str, Any], Tuple[Tuple[int, Optional[str], bool], ...], Tuple[Tuple[int, str, str, Optional[str], bool], ...]]:
    observed_at = snapshot.slot_end_exclusive_utc
    current = set(_target_contexts(projection))
    member_by_asn = {
        asn: (first_seen, baseline)
        for asn, first_seen, baseline in cohort_members
    }
    reference_by_key = {
        (asn, afi, prefix): (first_seen, baseline)
        for asn, afi, prefix, first_seen, baseline in cohort_references
    }
    discoveries = []
    for discovery in announce_discoveries:
        asn = int(discovery["asn"])
        afi = str(discovery["afi"])
        prefix = str(discovery["prefix"])
        first_seen_at = str(discovery["first_seen_at"])
        if asn not in member_by_asn:
            member_by_asn[asn] = (first_seen_at, False)
            discoveries.append(
                {
                    "kind": "dynamic_asn",
                    "asn": asn,
                    "first_seen_at": first_seen_at,
                    "route_event_id": discovery["route_event_id"],
                    "raw_record_ref_id": discovery["raw_record_ref_id"],
                }
            )
        key = (asn, afi, prefix)
        if key not in reference_by_key:
            reference_by_key[key] = (first_seen_at, False)
            discoveries.append(
                {
                    "kind": "dynamic_prefix",
                    "asn": asn,
                    "afi": afi,
                    "prefix": prefix,
                    "first_seen_at": first_seen_at,
                    "route_event_id": discovery["route_event_id"],
                    "raw_record_ref_id": discovery["raw_record_ref_id"],
                }
            )
    # 槽末仍可见的 target relation 必须来自 seed reference 或本槽/前槽的
    # 确定 announce。否则 compact cohort 已损坏，不能用槽末时间猜 first_seen。
    if any(key not in reference_by_key for key in current):
        raise FullWindowWorkerError("槽末 target relation 缺少 first_seen reference")
    members = tuple(sorted((asn, first_seen, baseline) for asn, (first_seen, baseline) in member_by_asn.items()))
    references = tuple(
        sorted(
            (asn, afi, prefix, first_seen, baseline)
            for (asn, afi, prefix), (first_seen, baseline) in reference_by_key.items()
        )
    )
    active_asns = {row[0] for row in members}
    visible_by_asn = {asn: set() for asn in active_asns}
    for asn, afi, prefix in current:
        if asn in visible_by_asn:
            visible_by_asn[asn].add((afi, prefix))
    reference_sets = {asn: set() for asn in active_asns}
    for asn, afi, prefix, _first_seen, _baseline in references:
        reference_sets[asn].add((afi, prefix))
    damaged = tuple(
        sorted(asn for asn in active_asns if reference_sets[asn] - visible_by_asn[asn])
    )
    visible = tuple(sorted(asn for asn in active_asns if visible_by_asn[asn]))
    baseline_asns = tuple(
        sorted(asn for asn, _first_seen, baseline in members if baseline)
    )
    current_prefixes = {(afi, prefix) for _asn, afi, prefix in current}
    ipv4_addresses = _address_union(
        (prefix for afi, prefix in current_prefixes if afi == "ipv4"),
        version=4,
    )
    ipv6_addresses = _address_union(
        (prefix for afi, prefix in current_prefixes if afi == "ipv6"),
        version=6,
    )
    state_visible_vps = tuple(sorted(set(observable_state_visible_vps)))
    full_visible_vps = tuple(sorted(set(full_state_visible_vps)))
    active_vps = tuple(sorted(set(update_active_vps)))
    session_by_vp = {row[0]: row for row in peer_session_states}
    observable_vps = tuple(
        sorted(vp_id for vp_id, row in session_by_vp.items() if row[1] == "observable")
    )
    down_vps = tuple(
        sorted(vp_id for vp_id, row in session_by_vp.items() if row[1] == "down")
    )
    unknown_vps = tuple(
        sorted(vp_id for vp_id, row in session_by_vp.items() if row[1] == "unknown")
    )
    state_continuous = snapshot.continuity_state == CONTINUOUS
    coverage_complete = not down_vps and not unknown_vps
    projected_snapshot_id = snapshot_id_v1(projection.projected)
    metric_value_state = (
        (
            "observed"
            if coverage_complete
            else "observed_route_state_partial_vp_coverage"
        )
        if state_continuous
        else "unknown_state_gap"
    )
    asn_impacts = []
    for asn in sorted(active_asns):
        families = []
        fully_by_afi = {}
        any_lost = False
        for afi in ("ipv4", "ipv6"):
            reference_prefixes = tuple(
                sorted(prefix for family, prefix in reference_sets[asn] if family == afi)
            )
            visible_prefixes = tuple(
                sorted(prefix for family, prefix in visible_by_asn[asn] if family == afi)
            )
            lost_prefixes = tuple(sorted(set(reference_prefixes) - set(visible_prefixes)))
            lost_addresses = _address_union(
                lost_prefixes, version=4 if afi == "ipv4" else 6
            )
            denominator = 256 if afi == "ipv4" else 1 << 80
            fully_invisible = bool(reference_prefixes) and not visible_prefixes
            fully_by_afi[afi] = fully_invisible
            any_lost = any_lost or bool(lost_prefixes)
            families.append(
                {
                    "afi": afi,
                    "snapshot_id": projected_snapshot_id,
                    "value_state": metric_value_state,
                    "reference_prefixes": list(reference_prefixes) if state_continuous else None,
                    "visible_prefixes": list(visible_prefixes) if state_continuous else None,
                    "lost_prefixes": list(lost_prefixes) if state_continuous else None,
                    "lost_equivalent": _equivalent(lost_addresses, denominator) if state_continuous else None,
                    "fully_invisible": fully_invisible if state_continuous else None,
                }
            )
        if not state_continuous:
            classification = "unknown"
        elif not any_lost:
            classification = "not_affected"
        elif fully_by_afi["ipv4"] and fully_by_afi["ipv6"]:
            classification = "dual_stack_fully_invisible"
        elif fully_by_afi["ipv4"]:
            classification = "ipv4_only_fully_invisible"
        elif fully_by_afi["ipv6"]:
            classification = "ipv6_only_fully_invisible"
        else:
            classification = "partially_visible"
        asn_impacts.append(
            {
                "asn": asn,
                "snapshot_id": projected_snapshot_id,
                "value_state": metric_value_state,
                "baseline_member": asn in baseline_asns,
                "dynamic_member": asn not in baseline_asns,
                "visible": asn in visible if state_continuous else None,
                "damaged": asn in damaged if state_continuous else None,
                "classification": classification,
                "address_families": families,
            }
        )
    prefix_relations = []
    for context in projection.audit.prefix_contexts:
        if not context.target_origin_asns:
            continue
        prefix_relations.append(
            {
                "snapshot_id": projected_snapshot_id,
                "afi": "ipv4" if context.afi_safi == "ipv4_unicast" else "ipv6",
                "prefix": context.prefix,
                "vp_ids": list(context.vp_ids),
                "route_event_ids": list(context.route_event_ids),
                "origin_asns": list(context.origin_asns),
                "target_origin_asns": list(context.target_origin_asns),
                "non_target_origin_asns": list(context.non_target_origin_asns),
                "moas": context.moas,
                "semantics": f"mapped_{mapping.view}_prefix_relation_not_causal",
            }
        )
    metrics = {
        "snapshot_id": projected_snapshot_id,
        "value_state": (
            (
                "observed"
                if coverage_complete
                else "observed_route_state_partial_vp_coverage"
            )
            if state_continuous
            else "unknown_state_gap"
        ),
        "missing_reason": (
            (
                None
                if coverage_complete
                else "peer_session_unavailable_route_state_carried_not_withdrawn"
            )
            if state_continuous
            else "snapshot_state_gap"
        ),
        "vp_coverage_state": "complete" if coverage_complete else "partial",
        "visible_asn_count": len(visible) if state_continuous else None,
        "damaged_asn_count": len(damaged) if state_continuous else None,
        "cohort_asn_count": len(active_asns) if state_continuous else None,
        "visible_ipv4_prefix_count": sum(afi == "ipv4" for afi, _prefix in current_prefixes) if state_continuous else None,
        "visible_ipv6_prefix_count": sum(afi == "ipv6" for afi, _prefix in current_prefixes) if state_continuous else None,
        "visible_ipv4_address_union": ipv4_addresses if state_continuous else None,
        "visible_ipv4_24_equivalent": _equivalent(ipv4_addresses, 256) if state_continuous else None,
        "visible_ipv6_address_union": ipv6_addresses if state_continuous else None,
        "visible_ipv6_48_equivalent": _equivalent(ipv6_addresses, 1 << 80) if state_continuous else None,
        "damaged_asn_ratio": (len(damaged) / len(active_asns) if active_asns else 0.0) if state_continuous else None,
    }
    excluded_reason_counts = dict(projection.audit.excluded_reason_counts)
    strict_quality = "not_accepted" if projection.audit.excluded_entry_count or projection.audit.excluded_change_count else "strict_population_not_proven"
    bound_projection_id = "mvp_v1_" + hashlib.sha256(
        canonical_json(
            {
                "schema": "rrc25_full_window_mapping_projection_binding_v1",
                "base_projection_id": projection.projection_id,
                "mapping_view": mapping.view,
                "mapping_source_sha256": mapping.source_sha256,
                "mapping_source_ref": mapping.source_ref,
            }
        ).encode("utf-8")
    ).hexdigest()[:32]
    row = {
        "schema_version": COUNTRY_SLOT_SCHEMA_VERSION,
        "slot_start_utc": snapshot.slot_start_utc,
        "slot_end_exclusive_utc": observed_at,
        "boundary": "[start,end)",
        "snapshot_id": projected_snapshot_id,
        "projection_id": bound_projection_id,
        "projection_kind": "mapped_country_projection",
        "mapping_view": mapping.view,
        "measurement_view": (
            "compatibility" if mapping.view == "compatible" else "revised"
        ),
        "mapping_source_sha256": mapping.source_sha256,
        "mapping_source_ref": mapping.source_ref,
        "main_curve": main_curve,
        "main_curve_semantics": (
            "mapped_compatible_only_not_strict_population"
            if main_curve
            else "separate_revised_non_overwriting_projection"
        ),
        "metrics": metrics,
        "visible_asns": list(visible) if state_continuous else None,
        "damaged_asns": list(damaged) if state_continuous else None,
        "baseline_asns": list(baseline_asns) if state_continuous else None,
        "cohort_asns": sorted(active_asns) if state_continuous else None,
        "asn_impacts": asn_impacts,
        "prefix_relations": prefix_relations,
        "issues": [
            {
                "snapshot_id": projected_snapshot_id,
                "value_state": "unknown_mapping_or_origin",
                "reason": ref.reason,
                "prefix": ref.prefix,
                "vp_id": ref.vp_id,
                "route_event_id": ref.route_event_id,
                "candidate_origin_asns": list(ref.candidate_origin_asns),
            }
            for ref in projection.audit.excluded_refs
        ],
        "dynamic_discoveries": discoveries,
        "dynamic_discovery_semantics": "first_seen_no_backfill",
        "strict_population": {
            "acceptance_state": strict_quality,
            "excluded_entry_count": projection.audit.excluded_entry_count,
            "excluded_change_count": projection.audit.excluded_change_count,
            "excluded_reason_counts": excluded_reason_counts,
            "excluded_route_event_ids": list(projection.audit.excluded_route_event_ids),
            "blockers": list(projection.blockers),
            "unknown_not_zero_filled": True,
        },
        "update_counts": {
            "announce": full_announce_count,
            "withdraw": full_withdraw_count,
            "retained_announce": retained_announce_count,
            "retained_withdraw": retained_withdraw_count,
            "physical_records": physical_record_count,
        },
        "vp_population": {
            "definition": VP_POPULATION_SEMANTICS,
            "source_sha256": state.vp_population_source_sha256,
            "expected_count": len(state.known_vp_ids),
            "route_state_visible_count_including_unavailable": len(full_visible_vps),
            "route_state_visible_vp_ids_including_unavailable": list(full_visible_vps),
            "observable_state_visible_count": len(state_visible_vps),
            "observable_state_visible_vp_ids": list(state_visible_vps),
            # 兼容早期 fixture 字段；语义由 observed_semantics 明确为可观测状态。
            "state_visible_count": len(state_visible_vps),
            "state_visible_vp_ids": list(state_visible_vps),
            "observed_semantics": VP_OBSERVED_SEMANTICS,
            "observable_vp_ids": list(observable_vps),
            "down_vp_ids": list(down_vps),
            "unknown_vp_ids": list(unknown_vps),
            "coverage_complete": coverage_complete,
            "down_vp_route_semantics": "carried_state_not_implicit_withdrawal",
            "session_states": [
                {
                    "vp_id": vp_id,
                    "availability": availability,
                    "basis": basis,
                    "last_observed_at": last_at,
                    "last_control_observation_id": control_id,
                    "last_control_raw_record_sha256": control_sha,
                }
                for vp_id, availability, basis, last_at, control_id, control_sha in peer_session_states
            ],
            "update_active_peer_count": len(active_vps),
            "update_active_vp_ids": list(active_vps),
            "update_active_semantics": UPDATE_ACTIVE_VP_SEMANTICS,
        },
    }
    return row, members, references


def derive_artifact_boundary(
    prior_compact_state: Mapping[str, Any] | CompactState,
    artifact: ArtifactDescriptor,
    route_events: Sequence[ResearchRouteEvent],
    record_observations: Sequence[Mapping[str, Any]],
    *,
    compatible_mapping: CountryMappingView,
    revised_mapping: CountryMappingView,
) -> ArtifactBoundaryDerivation:
    """不读取 raw、不访问时钟，逐槽重算国家样本与后继 compact state。"""

    compact = (
        prior_compact_state
        if isinstance(prior_compact_state, CompactState)
        else compact_state_from_payload(prior_compact_state)
    )
    if not isinstance(artifact, ArtifactDescriptor):
        raise FullWindowWorkerError("artifact 必须是 ArtifactDescriptor")
    if compatible_mapping.view != "compatible" or revised_mapping.view != "revised":
        raise FullWindowWorkerError("逐槽派生必须同时提供 compatible/revised mapping")
    if revised_mapping.target_country != compatible_mapping.target_country:
        raise FullWindowWorkerError("compatible/revised target country 不一致")
    if (
        compact.compatible_mapping_fingerprint_sha256
        != _mapping_view_fingerprint(compatible_mapping)
        or compact.revised_mapping_fingerprint_sha256
        != _mapping_view_fingerprint(revised_mapping)
    ):
        raise FullWindowWorkerError("逐槽派生 mapping 与 compact state 不一致")
    if (
        compact.last_slot_end_exclusive_utc is not None
        and compact.last_slot_end_exclusive_utc != artifact.slot_start_utc
    ):
        raise FullWindowWorkerError("compact state 与 artifact 槽不连续")
    events = tuple(route_events)
    if any(not isinstance(event, ResearchRouteEvent) for event in events):
        raise FullWindowWorkerError("route_events 必须是 ResearchRouteEvent 序列")
    if len({event.route_event_id for event in events}) != len(events):
        raise FullWindowWorkerError("逐槽 RouteEvent 身份重复")
    if any(
        event.artifact_id != artifact.artifact_id
        or event.file_sha256 != artifact.file_sha256
        or event.collector_id != artifact.collector_id
        or event.artifact_slot_utc != artifact.slot_start_utc
        for event in events
    ):
        raise FullWindowWorkerError("RouteEvent 与 artifact 身份不闭合")

    if isinstance(record_observations, (str, bytes, bytearray, Mapping)):
        raise FullWindowWorkerError("record_observations 必须是有序对象序列")
    observations = tuple(record_observations)
    working_sessions = {row[0]: row for row in compact.peer_session_states}
    update_active_vps: set[str] = set()
    full_announce = 0
    full_withdraw = 0
    observed_ordinals = set()
    allowed_kinds = {
        UPDATE_RECORD,
        STATE_CHANGE_RECORD,
        OPEN_RECORD,
        NOTIFICATION_RECORD,
        KEEPALIVE_RECORD,
        END_OF_RIB_RECORD,
    }
    expected_offset = 0
    artifact_start = datetime.fromisoformat(
        artifact.slot_start_utc[:-1] + "+00:00"
    )
    artifact_end = datetime.fromisoformat(
        artifact.slot_end_exclusive_utc[:-1] + "+00:00"
    )
    events_by_ordinal: dict[int, list[ResearchRouteEvent]] = {}
    for event in events:
        events_by_ordinal.setdefault(event.record_ordinal, []).append(event)
    for expected_ordinal, row in enumerate(observations):
        required = {
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
        if not isinstance(row, Mapping) or set(row) != required:
            raise FullWindowWorkerError("record observation 字段不闭合")
        if (
            row.get("schema_version") != RECORD_OBSERVATION_SHARD_SCHEMA_VERSION
            or row.get("artifact_id") != artifact.artifact_id
            or row.get("file_sha256") != artifact.file_sha256
            or row.get("collector_id") != artifact.collector_id
            or row.get("artifact_slot_utc") != artifact.slot_start_utc
            or row.get("record_ordinal") != expected_ordinal
            or row.get("record_kind") not in allowed_kinds
            or row.get("semantics")
            != "complete_physical_record_observation_for_independent_slot_derivation"
        ):
            raise FullWindowWorkerError("record observation 与 artifact/顺序不闭合")
        record_offset = row.get("record_offset")
        record_length = row.get("record_length")
        if (
            isinstance(record_offset, bool)
            or not isinstance(record_offset, int)
            or record_offset != expected_offset
            or isinstance(record_length, bool)
            or not isinstance(record_length, int)
            or record_length < 12
        ):
            raise FullWindowWorkerError("record observation 解压流坐标不连续")
        expected_offset += record_length
        _sha(row.get("raw_record_sha256"), "record observation raw SHA")
        record_event_time = _utc_event(
            row.get("event_time_utc"), "record observation event_time_utc"
        )
        parsed_record_time = datetime.fromisoformat(
            record_event_time[:-1] + "+00:00"
        )
        if not artifact_start <= parsed_record_time < artifact_end:
            raise FullWindowWorkerError(
                "record observation 时间越出 artifact 半开槽"
            )
        observed_ordinals.add(expected_ordinal)
        announce_count = row.get("announce_count")
        withdraw_count = row.get("withdraw_count")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (announce_count, withdraw_count)
        ):
            raise FullWindowWorkerError("record observation UPDATE 计数非法")
        if row.get("record_kind") != UPDATE_RECORD and (
            announce_count or withdraw_count
        ):
            raise FullWindowWorkerError("非 UPDATE record 的路由元素计数必须为 0")
        full_announce += announce_count
        full_withdraw += withdraw_count
        peer_rows = row.get("update_peer_observations")
        if not isinstance(peer_rows, list):
            raise FullWindowWorkerError("update_peer_observations 必须是数组")
        if (
            row.get("record_kind") == UPDATE_RECORD
            and len(peer_rows) != 1
        ) or (row.get("record_kind") != UPDATE_RECORD and peer_rows):
            raise FullWindowWorkerError("UPDATE record 必须且只能携带一个 update peer")
        seen_peer_rows = set()
        for peer_row in peer_rows:
            if not isinstance(peer_row, Mapping) or set(peer_row) != {
                "vp_id",
                "event_time_utc",
            }:
                raise FullWindowWorkerError("update peer observation 字段不闭合")
            vp_id = peer_row.get("vp_id")
            event_time = _utc_event(
                peer_row.get("event_time_utc"), "update peer event_time_utc"
            )
            identity = (vp_id, event_time)
            if not isinstance(vp_id, str) or not vp_id or identity in seen_peer_rows:
                raise FullWindowWorkerError("update peer observation 身份非法或重复")
            if event_time != record_event_time:
                raise FullWindowWorkerError("update peer 时间与 physical record 不一致")
            seen_peer_rows.add(identity)
            update_active_vps.add(vp_id)
            prior = working_sessions.get(vp_id)
            working_sessions[vp_id] = (
                vp_id,
                "observable",
                "update_message",
                event_time,
                prior[4] if prior is not None else None,
                prior[5] if prior is not None else None,
            )
        session = row.get("peer_session_observation")
        if session is not None:
            if row.get("record_kind") != STATE_CHANGE_RECORD or not isinstance(
                session, Mapping
            ):
                raise FullWindowWorkerError("peer session observation 类型非法")
            vp_id = session.get("vp_id")
            if (
                not isinstance(vp_id, str)
                or not vp_id
                or session.get("artifact_id") != artifact.artifact_id
                or session.get("file_sha256") != artifact.file_sha256
                or session.get("record_ordinal") != expected_ordinal
                or session.get("raw_record_sha256") != row.get("raw_record_sha256")
            ):
                raise FullWindowWorkerError("peer session observation 与 record 不闭合")
            event_time = _utc_event(
                session.get("event_time_utc"), "peer session event_time_utc"
            )
            if event_time != record_event_time:
                raise FullWindowWorkerError("peer session 时间与 physical record 不一致")
            new_state = session.get("new_state_name")
            observation_id = session.get("observation_id")
            if (
                not isinstance(new_state, str)
                or not isinstance(observation_id, str)
                or not observation_id
            ):
                raise FullWindowWorkerError("peer session new_state_name 非法")
            working_sessions[vp_id] = (
                vp_id,
                "observable" if new_state == "established" else "down",
                "state_change",
                event_time,
                observation_id,
                session.get("raw_record_sha256"),
            )
        elif row.get("record_kind") == STATE_CHANGE_RECORD:
            raise FullWindowWorkerError("STATE_CHANGE 缺少 peer session observation")
    if not observations:
        raise FullWindowWorkerError("artifact record observations 不能为空")
    if any(event.record_ordinal not in observed_ordinals for event in events):
        raise FullWindowWorkerError("RouteEvent 未闭合到 record observation")
    for ordinal, retained_events in events_by_ordinal.items():
        observation = observations[ordinal]
        if observation.get("record_kind") != UPDATE_RECORD:
            raise FullWindowWorkerError("RouteEvent 只能闭合到 UPDATE record")
        if any(
            event.event_time_utc != observation.get("event_time_utc")
            for event in retained_events
        ):
            raise FullWindowWorkerError("RouteEvent 时间与 record observation 不一致")
        if len({event.element_ordinal for event in retained_events}) != len(
            retained_events
        ):
            raise FullWindowWorkerError("同一 physical record 的 element_ordinal 重复")
        retained_announces = sum(
            event.action == "announce" for event in retained_events
        )
        retained_withdraws = sum(
            event.action == "withdraw" for event in retained_events
        )
        if (
            retained_announces > observation.get("announce_count")
            or retained_withdraws > observation.get("withdraw_count")
        ):
            raise FullWindowWorkerError("保留 RouteEvent 数超过 physical record 全量计数")

    working_tracked = set(compact.tracked_prefixes)
    working_tracked.update(
        _canonical_prefix(event.prefix)
        for event in events
        if event.action == "announce"
    )
    state, changes = apply_streaming_update_batch(compact.route_state, events)
    state, snapshot = build_five_minute_snapshot(
        state,
        slot_start_utc=artifact.slot_start_utc,
        slot_end_exclusive_utc=artifact.slot_end_exclusive_utc,
        slot_changes=changes,
    )
    full_state_visible_vps = {entry.key.vp_id for entry in snapshot.entries}
    observable_vps = {
        vp_id for vp_id, row in working_sessions.items() if row[1] == "observable"
    }
    observable_snapshot = replace(
        snapshot,
        entries=tuple(
            entry for entry in snapshot.entries if entry.key.vp_id in observable_vps
        ),
        slot_changes=tuple(
            change
            for change in snapshot.slot_changes
            if change.key.vp_id in observable_vps
        ),
        route_count=(
            sum(entry.key.vp_id in observable_vps for entry in snapshot.entries)
            if snapshot.continuity_state == CONTINUOUS
            else None
        ),
    )
    projection = _mapped_projection_for_view(snapshot, compatible_mapping)
    revised_projection = _mapped_projection_for_view(snapshot, revised_mapping)
    announce_discoveries = _mapped_announce_discoveries(events, compatible_mapping)
    revised_announce_discoveries = _mapped_announce_discoveries(
        events, revised_mapping
    )
    known_vps = tuple(
        sorted(
            set(compact.known_vp_ids)
            | {entry.key.vp_id for entry in state.entries}
            | update_active_vps
            | set(working_sessions)
        )
    )
    for vp_id in known_vps:
        if vp_id not in working_sessions:
            working_sessions[vp_id] = (
                vp_id,
                "unknown",
                "seed_route_population",
                None,
                None,
                None,
            )
    peer_session_states = tuple(sorted(working_sessions.values()))
    state_for_slot = CompactState(
        route_state=state,
        cursor_route_event_id=(
            events[-1].route_event_id if events else compact.cursor_route_event_id
        ),
        tracked_prefixes=tuple(sorted(working_tracked)),
        known_vp_ids=known_vps,
        vp_population_source_sha256=compact.vp_population_source_sha256,
        compatible_mapping_fingerprint_sha256=compact.compatible_mapping_fingerprint_sha256,
        revised_mapping_fingerprint_sha256=compact.revised_mapping_fingerprint_sha256,
        peer_session_states=peer_session_states,
        cohort_members=compact.cohort_members,
        cohort_references=compact.cohort_references,
        revised_cohort_members=compact.revised_cohort_members,
        revised_cohort_references=compact.revised_cohort_references,
        last_slot_end_exclusive_utc=artifact.slot_end_exclusive_utc,
    )
    common = {
        "full_announce_count": full_announce,
        "full_withdraw_count": full_withdraw,
        "retained_announce_count": sum(
            event.action == "announce" for event in events
        ),
        "retained_withdraw_count": sum(
            event.action == "withdraw" for event in events
        ),
        "physical_record_count": len(observations),
        "update_active_vps": update_active_vps,
        "peer_session_states": peer_session_states,
        "full_state_visible_vps": full_state_visible_vps,
        "observable_state_visible_vps": {
            entry.key.vp_id for entry in observable_snapshot.entries
        },
    }
    country_row, members, references = _country_slot(
        snapshot,
        projection,
        state_for_slot,
        announce_discoveries=announce_discoveries,
        mapping=compatible_mapping,
        cohort_members=compact.cohort_members,
        cohort_references=compact.cohort_references,
        main_curve=True,
        **common,
    )
    revised_country_row, revised_members, revised_references = _country_slot(
        snapshot,
        revised_projection,
        state_for_slot,
        announce_discoveries=revised_announce_discoveries,
        mapping=revised_mapping,
        cohort_members=compact.revised_cohort_members,
        cohort_references=compact.revised_cohort_references,
        main_curve=False,
        **common,
    )
    final_state = CompactState(
        route_state=state,
        cursor_route_event_id=state_for_slot.cursor_route_event_id,
        tracked_prefixes=state_for_slot.tracked_prefixes,
        known_vp_ids=known_vps,
        vp_population_source_sha256=compact.vp_population_source_sha256,
        compatible_mapping_fingerprint_sha256=compact.compatible_mapping_fingerprint_sha256,
        revised_mapping_fingerprint_sha256=compact.revised_mapping_fingerprint_sha256,
        peer_session_states=peer_session_states,
        cohort_members=members,
        cohort_references=references,
        revised_cohort_members=revised_members,
        revised_cohort_references=revised_references,
        last_slot_end_exclusive_utc=artifact.slot_end_exclusive_utc,
    )
    return ArtifactBoundaryDerivation(
        compatible_country_slot=country_row,
        revised_country_slot=revised_country_row,
        final_compact_state=compact_state_to_payload(final_state),
    )


def _stream_proof(
    stream: Any,
    artifact: ArtifactDescriptor,
    *,
    process_seconds: float,
    retained_seed_spool_bytes: int,
) -> SinglePassProof:
    stats = getattr(stream, "statistics", None)
    if not isinstance(stats, Mapping):
        raise FullWindowWorkerError("UPDATE stream 缺少 statistics")
    peak = stats.get("peak_spool_bytes", 0)
    if isinstance(peak, bool) or not isinstance(peak, int) or peak < 0:
        raise FullWindowWorkerError("UPDATE stream peak_spool_bytes 非法")
    return SinglePassProof(
        status=stats.get("status"),
        compressed_file_sha256=stats.get("compressed_file_sha256"),
        compressed_size_bytes=stats.get("compressed_size_bytes"),
        compressed_bytes_read_observed=stats.get("compressed_bytes_read_observed"),
        compressed_read_passes=stats.get("compressed_read_passes"),
        process_seconds=process_seconds,
        peak_temporary_bytes=retained_seed_spool_bytes + peak,
        database_write_operations=0,
    )


def _verified_parser_attestation(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FullWindowWorkerError("parser_attestation 必须是对象")
    payload = dict(value)
    fingerprint = payload.pop("attestation_fingerprint_sha256", None)
    expected = hashlib.sha256(
        canonical_json(
            {
                "schema": "parser_attestation_fingerprint_v1",
                "attestation": payload,
            }
        ).encode("utf-8")
    ).hexdigest()
    if (
        value.get("schema_version") != "parser_attestation_v1"
        or fingerprint != expected
        or value.get("binary_execution_policy")
        not in {"verified_in_process_source", "verified_open_fd_exec"}
    ):
        raise FullWindowWorkerError("parser_attestation 身份/执行策略未闭合")
    return dict(value)


def run_one_update_artifact(
    head: JournalHead,
    token: AttemptToken,
    *,
    artifact_manifest_row: Mapping[str, Any],
    compatible_mapping: CountryMappingView,
    revised_mapping: CountryMappingView,
    raw_retention_membership: RawRetentionMembership,
    update_record_stream_factory: RecordStreamFactory,
    parser_attestation: Mapping[str, Any],
    retained_seed_spool_bytes: int = 0,
    clock: Clock,
    runtime_check_interval_records: int = 256,
    soft_stop_seconds: float = 540.0,
) -> CommittedArtifact:
    """完整读取并提交 CURRENT 的一个 UPDATE artifact；异常只写失败 outcome。"""

    if compatible_mapping.view != "compatible":
        raise FullWindowWorkerError("主曲线必须使用 compatible mapping")
    if revised_mapping.view != "revised":
        raise FullWindowWorkerError("第二视图必须使用 revised mapping")
    if revised_mapping.target_country != compatible_mapping.target_country:
        raise FullWindowWorkerError("compatible/revised target country 不一致")
    verified_parser_attestation = _verified_parser_attestation(parser_attestation)
    if not callable(raw_retention_membership) or not callable(update_record_stream_factory) or not callable(clock):
        raise FullWindowWorkerError("worker callable 参数非法")
    if isinstance(retained_seed_spool_bytes, bool) or not isinstance(retained_seed_spool_bytes, int) or retained_seed_spool_bytes < 0:
        raise FullWindowWorkerError("retained_seed_spool_bytes 非法")
    if (
        isinstance(runtime_check_interval_records, bool)
        or not isinstance(runtime_check_interval_records, int)
        or runtime_check_interval_records <= 0
    ):
        raise FullWindowWorkerError("runtime_check_interval_records 必须为正整数")
    if (
        isinstance(soft_stop_seconds, bool)
        or not isinstance(soft_stop_seconds, (int, float))
        or not 0 < float(soft_stop_seconds) < 600
    ):
        raise FullWindowWorkerError("soft_stop_seconds 必须位于 (0,600)")
    artifact = artifact_descriptor_from_manifest(head.next_artifact_index, artifact_manifest_row)
    if artifact != token.artifact:
        raise FullWindowWorkerError("attempt token 与 artifact manifest 不一致")
    try:
        compact = compact_state_from_payload(head.scratch["compact_state"])
        if (
            compact.compatible_mapping_fingerprint_sha256
            != _mapping_view_fingerprint(compatible_mapping)
            or compact.revised_mapping_fingerprint_sha256
            != _mapping_view_fingerprint(revised_mapping)
        ):
            raise FullWindowWorkerError(
                "本次 compatible/revised mapping 与 journal genesis 不一致"
            )
        if compact.last_slot_end_exclusive_utc is not None and compact.last_slot_end_exclusive_utc != artifact.slot_start_utc:
            raise FullWindowWorkerError("compact state 与下一个五分钟槽不连续")
        working_tracked = set(compact.tracked_prefixes)
        route_events = []
        raw_ref_rows = []
        control_rows = []
        record_observation_rows = []
        physical_count = 0

        start = float(clock())
        stream = update_record_stream_factory(dict(artifact_manifest_row))
        adapter = iter_adapted_update_records(
            stream,
            artifact=artifact_manifest_row,
            route_element_retention_selector=_retention_selector(
                working_tracked,
                raw_retention_membership,
            ),
        )
        try:
            for record in adapter:
                physical_count += 1
                record_observation_rows.append(_record_observation_row(record))
                route_events.extend(record.route_events)
                raw_ref_rows.extend(
                    _raw_ref_row(record.raw_record, event)
                    for event in record.route_events
                )
                if not record.route_events and record.record_kind in {
                    STATE_CHANGE_RECORD,
                    OPEN_RECORD,
                    NOTIFICATION_RECORD,
                    KEEPALIVE_RECORD,
                    END_OF_RIB_RECORD,
                }:
                    control_rows.append(_control_row(record))
                if physical_count % runtime_check_interval_records == 0:
                    hot_elapsed = float(clock()) - start
                    if hot_elapsed < 0:
                        raise FullWindowWorkerError("clock 不得倒退")
                    if hot_elapsed >= float(soft_stop_seconds):
                        raise FullWindowWorkerError(
                            "UPDATE artifact 在热循环达到软停边界；未提交任何槽语义"
                        )
        finally:
            close = getattr(adapter, "close", None)
            if callable(close):
                close()
        elapsed = float(clock()) - start
        if elapsed <= 0:
            raise FullWindowWorkerError("clock 未取得正运行时长")
        if elapsed >= float(soft_stop_seconds):
            raise FullWindowWorkerError(
                "UPDATE artifact 在 stream 阶段边界达到软停；未提交任何槽语义"
            )
        derived = derive_artifact_boundary(
            compact,
            artifact,
            tuple(route_events),
            tuple(record_observation_rows),
            compatible_mapping=compatible_mapping,
            revised_mapping=revised_mapping,
        )
        total_elapsed = float(clock()) - start
        if total_elapsed <= 0 or total_elapsed >= float(soft_stop_seconds):
            raise FullWindowWorkerError(
                "UPDATE artifact 在派生计算后达到软停；未提交任何槽语义"
            )
        proof = _stream_proof(
            stream,
            artifact,
            process_seconds=total_elapsed,
            retained_seed_spool_bytes=retained_seed_spool_bytes,
        )

        def publication_gate(stage: str) -> None:
            gate_elapsed = float(clock()) - start
            if gate_elapsed < 0:
                raise FullWindowWorkerError("clock 不得倒退")
            if gate_elapsed >= float(soft_stop_seconds):
                raise FullWindowWorkerError(
                    f"UPDATE artifact 在发布阶段达到软停({stage})；CURRENT 未推进"
                )

        return commit_artifact_boundary(
            head,
            token,
            proof=proof,
            compact_state=derived.final_compact_state,
            shards=(
                ShardInput("route_events", tuple(_event_row(event) for event in route_events)),
                ShardInput("raw_record_refs", tuple(raw_ref_rows)),
                ShardInput("control_records", tuple(control_rows)),
                ShardInput(
                    "record_observations", tuple(record_observation_rows)
                ),
                ShardInput(
                    "parser_attestations", (verified_parser_attestation,)
                ),
                ShardInput(
                    "country_slots",
                    (
                        derived.compatible_country_slot,
                        derived.revised_country_slot,
                    ),
                ),
            ),
            publication_gate=publication_gate,
        )
    except (FullWindowJournalError, FullWindowWorkerError, OSError, EOFError, ValueError) as error:
        stats = getattr(locals().get("stream"), "statistics", {})
        observed = stats.get("compressed_bytes_read_observed", 0) if isinstance(stats, Mapping) else 0
        if isinstance(observed, bool) or not isinstance(observed, int) or observed < 0:
            observed = 0
        try:
            record_attempt_failure(
                head.root,
                token,
                reason=f"{type(error).__name__}:{error}",
                observed_compressed_bytes=observed,
            )
        except (FullWindowJournalError, OSError):
            pass
        raise FullWindowWorkerError("单 UPDATE artifact 未提交") from error


__all__ = (
    "ArtifactBoundaryDerivation",
    "COMPACT_STATE_SCHEMA_VERSION",
    "COUNTRY_SLOT_SCHEMA_VERSION",
    "CompactState",
    "FullWindowWorkerError",
    "RECORD_OBSERVATION_SHARD_SCHEMA_VERSION",
    "VerifiedSeedBootstrap",
    "VP_OBSERVED_SEMANTICS",
    "VP_POPULATION_SEMANTICS",
    "artifact_descriptor_from_manifest",
    "compact_state_from_payload",
    "compact_state_to_payload",
    "derive_artifact_boundary",
    "initialize_compact_state_from_seed",
    "initialize_journal_from_verified_seed",
    "load_verified_full_seed_bootstrap",
    "raw_record_ref_id_v1",
    "run_one_update_artifact",
)
