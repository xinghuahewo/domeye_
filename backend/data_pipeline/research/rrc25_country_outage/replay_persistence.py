"""RRC25 研究回放状态的确定性序列化、发布与检查点恢复。

状态制品采用单记录 JSONL gzip，gzip 头固定，目标文件只允许在空路径创建。
检查点同时绑定 profile、输入选择、代码、映射和状态文件 SHA256；恢复时逐层
复核路径、普通文件身份、文件哈希、检查点指纹和状态语义指纹。任何失配均
失败关闭，不会退化为从不明状态继续。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping, Optional, Sequence, Tuple

from ...route_event import AsPathSegment, artifact_id_v1, route_event_id_v1, vp_id_v1
from .file_artifacts import (
    PublishedArtifact,
    ResearchArtifactError,
    build_checkpoint,
    canonical_json,
    verify_checkpoint,
    write_canonical_json,
    write_canonical_jsonl_gzip,
)
from .state_replay import (
    CONTINUOUS,
    UNKNOWN_AFTER_GAP,
    RawRecordRef,
    RouteLastChange,
    RouteReplayState,
    RouteStateEntry,
    RouteStateKey,
)


STATE_SCHEMA_VERSION = "rrc25-country-outage-route-state/v1"
STATE_FINGERPRINT_SCHEMA = "rrc25_country_outage_route_state_fingerprint_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_RE = re.compile(r"^art_v1_[0-9a-f]{32}$")
_ROUTE_EVENT_RE = re.compile(r"^rte_v1_[0-9a-f]{32}$")
_VP_RE = re.compile(r"^vp_v1_[0-9a-f]{32}$")
_ACTIONS = frozenset(("rib_snapshot", "announce", "withdraw"))
_VISIBLE_ACTIONS = frozenset(("rib_snapshot", "announce"))
_CONTINUITY = frozenset((CONTINUOUS, UNKNOWN_AFTER_GAP))
_AFI_SAFI = frozenset(("ipv4_unicast", "ipv6_unicast"))
_SEGMENT_TYPES = frozenset(
    ("as_sequence", "as_set", "confederation_sequence", "confederation_set")
)


@dataclass(frozen=True)
class PublishedReplayCheckpoint:
    """已发布状态与检查点文件；两者均不可覆盖。"""

    state_artifact: PublishedArtifact
    checkpoint_artifact: PublishedArtifact
    checkpoint: Mapping[str, Any]


@dataclass(frozen=True)
class RestoredReplayCheckpoint:
    """通过全部哈希和语义校验后的恢复结果。"""

    checkpoint: Mapping[str, Any]
    state: RouteReplayState
    checkpoint_sha256: str
    state_sha256: str


def _exact_mapping(value: Any, fields: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ResearchArtifactError(f"{name} 字段不闭合")
    return value


def _nonnegative(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResearchArtifactError(f"{name} 必须是非负整数")
    return value


def _sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ResearchArtifactError(f"{name} 必须是 64 位小写十六进制")
    return value


def _utc(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ResearchArtifactError(f"{name} 必须是 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ResearchArtifactError(f"{name} 不是合法 UTC 时间") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ResearchArtifactError(f"{name} 必须是 UTC 时间")
    if parsed.microsecond:
        canonical = parsed.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    else:
        canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical != value:
        raise ResearchArtifactError(f"{name} 不是规范 UTC 表示")
    return value


def _key_to_dict(key: RouteStateKey) -> dict[str, Any]:
    return {
        "collector_id": key.collector_id,
        "vp_id": key.vp_id,
        "afi_safi": key.afi_safi,
        "prefix": key.prefix,
    }


def _key_from_dict(value: Any, name: str) -> RouteStateKey:
    row = _exact_mapping(
        value, {"collector_id", "vp_id", "afi_safi", "prefix"}, name
    )
    collector = row["collector_id"]
    vp_id = row["vp_id"]
    afi_safi = row["afi_safi"]
    prefix = row["prefix"]
    if not isinstance(collector, str) or not collector:
        raise ResearchArtifactError(f"{name}.collector_id 非法")
    if not isinstance(vp_id, str) or _VP_RE.fullmatch(vp_id) is None:
        raise ResearchArtifactError(f"{name}.vp_id 非法")
    if afi_safi not in _AFI_SAFI:
        raise ResearchArtifactError(f"{name}.afi_safi 非法")
    if not isinstance(prefix, str):
        raise ResearchArtifactError(f"{name}.prefix 非法")
    try:
        network = ipaddress.ip_network(prefix, strict=True)
    except ValueError as error:
        raise ResearchArtifactError(f"{name}.prefix 不是规范 CIDR") from error
    expected_afi = "ipv4_unicast" if network.version == 4 else "ipv6_unicast"
    if network.compressed != prefix or afi_safi != expected_afi:
        raise ResearchArtifactError(f"{name}.prefix 与地址族不一致")
    return RouteStateKey(collector, vp_id, afi_safi, prefix)


def _path_to_list(path: Optional[Tuple[AsPathSegment, ...]]) -> Any:
    if path is None:
        return None
    return [
        {"segment_type": segment.segment_type, "asns": list(segment.asns)}
        for segment in path
    ]


def _path_from_list(value: Any, name: str, *, allow_none: bool) -> Optional[Tuple[AsPathSegment, ...]]:
    if value is None:
        if allow_none:
            return None
        raise ResearchArtifactError(f"{name} 不得为 null")
    if not isinstance(value, list):
        raise ResearchArtifactError(f"{name} 必须是数组")
    result = []
    for index, item in enumerate(value):
        row = _exact_mapping(item, {"segment_type", "asns"}, f"{name}[{index}]")
        segment_type = row["segment_type"]
        asns = row["asns"]
        if segment_type not in _SEGMENT_TYPES:
            raise ResearchArtifactError(f"{name}[{index}].segment_type 非法")
        if not isinstance(asns, list) or not asns:
            raise ResearchArtifactError(f"{name}[{index}].asns 必须是非空数组")
        normalized_asns = []
        for asn_index, asn in enumerate(asns):
            value_asn = _nonnegative(asn, f"{name}[{index}].asns[{asn_index}]")
            if value_asn > 4_294_967_295:
                raise ResearchArtifactError(f"{name}[{index}] ASN 超出 32 位范围")
            normalized_asns.append(value_asn)
        result.append(AsPathSegment(segment_type, tuple(normalized_asns)))
    return tuple(result)


def _flags(value: Any, name: str) -> Tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(flag, str) or not flag for flag in value
    ):
        raise ResearchArtifactError(f"{name} 必须是非空字符串数组")
    if value != sorted(set(value)):
        raise ResearchArtifactError(f"{name} 必须去重并排序")
    return tuple(value)


def _raw_to_dict(raw: RawRecordRef) -> dict[str, Any]:
    return {
        "artifact_id": raw.artifact_id,
        "file_sha256": raw.file_sha256,
        "collector_id": raw.collector_id,
        "artifact_slot_utc": raw.artifact_slot_utc,
        "record_ordinal": raw.record_ordinal,
        "element_ordinal": raw.element_ordinal,
        "route_event_id": raw.route_event_id,
    }


def _raw_from_dict(value: Any, name: str) -> RawRecordRef:
    row = _exact_mapping(
        value,
        {
            "artifact_id",
            "file_sha256",
            "collector_id",
            "artifact_slot_utc",
            "record_ordinal",
            "element_ordinal",
            "route_event_id",
        },
        name,
    )
    file_sha256 = _sha256(row["file_sha256"], f"{name}.file_sha256")
    artifact_id = row["artifact_id"]
    if not isinstance(artifact_id, str) or _ARTIFACT_RE.fullmatch(artifact_id) is None:
        raise ResearchArtifactError(f"{name}.artifact_id 非法")
    if artifact_id != artifact_id_v1(file_sha256):
        raise ResearchArtifactError(f"{name}.artifact_id 与文件哈希不一致")
    collector = row["collector_id"]
    if not isinstance(collector, str) or not collector:
        raise ResearchArtifactError(f"{name}.collector_id 非法")
    slot = _utc(row["artifact_slot_utc"], f"{name}.artifact_slot_utc")
    record = _nonnegative(row["record_ordinal"], f"{name}.record_ordinal")
    element = _nonnegative(row["element_ordinal"], f"{name}.element_ordinal")
    route_id = row["route_event_id"]
    if not isinstance(route_id, str) or _ROUTE_EVENT_RE.fullmatch(route_id) is None:
        raise ResearchArtifactError(f"{name}.route_event_id 非法")
    if route_id != route_event_id_v1(file_sha256, record, element):
        raise ResearchArtifactError(f"{name}.route_event_id 与原始坐标不一致")
    return RawRecordRef(
        artifact_id, file_sha256, collector, slot, record, element, route_id
    )


def _entry_to_dict(entry: RouteStateEntry) -> dict[str, Any]:
    return {
        "key": _key_to_dict(entry.key),
        "peer_ip": entry.peer_ip,
        "peer_asn": entry.peer_asn,
        "as_path": _path_to_list(entry.as_path),
        "quality_flags": list(entry.quality_flags),
        "last_action": entry.last_action,
        "last_event_time_utc": entry.last_event_time_utc,
        "last_raw_ref": _raw_to_dict(entry.last_raw_ref),
    }


def _entry_from_dict(value: Any, name: str) -> RouteStateEntry:
    row = _exact_mapping(
        value,
        {
            "key",
            "peer_ip",
            "peer_asn",
            "as_path",
            "quality_flags",
            "last_action",
            "last_event_time_utc",
            "last_raw_ref",
        },
        name,
    )
    key = _key_from_dict(row["key"], f"{name}.key")
    try:
        peer_ip = ipaddress.ip_address(row["peer_ip"]).compressed
    except (TypeError, ValueError) as error:
        raise ResearchArtifactError(f"{name}.peer_ip 非法") from error
    if peer_ip != row["peer_ip"]:
        raise ResearchArtifactError(f"{name}.peer_ip 不是规范表示")
    peer_asn = _nonnegative(row["peer_asn"], f"{name}.peer_asn")
    if peer_asn > 4_294_967_295:
        raise ResearchArtifactError(f"{name}.peer_asn 超出 32 位范围")
    if key.vp_id != vp_id_v1(key.collector_id, peer_ip, peer_asn):
        raise ResearchArtifactError(f"{name}.vp_id 与 peer 身份不一致")
    action = row["last_action"]
    if action not in _VISIBLE_ACTIONS:
        raise ResearchArtifactError(f"{name}.last_action 必须是可见路由动作")
    path = _path_from_list(row["as_path"], f"{name}.as_path", allow_none=False)
    flags = _flags(row["quality_flags"], f"{name}.quality_flags")
    event_time = _utc(row["last_event_time_utc"], f"{name}.last_event_time_utc")
    raw = _raw_from_dict(row["last_raw_ref"], f"{name}.last_raw_ref")
    if raw.collector_id != key.collector_id:
        raise ResearchArtifactError(f"{name} raw collector 与状态键不一致")
    return RouteStateEntry(key, peer_ip, peer_asn, path or (), flags, action, event_time, raw)


def _change_to_dict(change: RouteLastChange) -> dict[str, Any]:
    return {
        "key": _key_to_dict(change.key),
        "action": change.action,
        "event_time_utc": change.event_time_utc,
        "as_path": _path_to_list(change.as_path),
        "quality_flags": list(change.quality_flags),
        "raw_ref": _raw_to_dict(change.raw_ref),
    }


def _change_from_dict(value: Any, name: str) -> RouteLastChange:
    row = _exact_mapping(
        value,
        {"key", "action", "event_time_utc", "as_path", "quality_flags", "raw_ref"},
        name,
    )
    key = _key_from_dict(row["key"], f"{name}.key")
    action = row["action"]
    if action not in _ACTIONS:
        raise ResearchArtifactError(f"{name}.action 非法")
    path = _path_from_list(
        row["as_path"], f"{name}.as_path", allow_none=action == "withdraw"
    )
    if action == "withdraw" and path is not None:
        raise ResearchArtifactError(f"{name}.withdraw 不得携带 AS_PATH")
    if action != "withdraw" and path is None:
        raise ResearchArtifactError(f"{name} 可见动作必须携带 AS_PATH")
    flags = _flags(row["quality_flags"], f"{name}.quality_flags")
    event_time = _utc(row["event_time_utc"], f"{name}.event_time_utc")
    raw = _raw_from_dict(row["raw_ref"], f"{name}.raw_ref")
    if raw.collector_id != key.collector_id:
        raise ResearchArtifactError(f"{name} raw collector 与状态键不一致")
    return RouteLastChange(key, action, event_time, path, flags, raw)


def _state_semantic(state: RouteReplayState) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "continuity_state": state.continuity_state,
        "missing_reasons": list(state.missing_reasons),
        "processed_route_event_ids": sorted(state.processed_route_event_ids),
        "last_order_key": list(state.last_order_key) if state.last_order_key is not None else None,
        "entries": [_entry_to_dict(entry) for entry in state.entries],
        "latest_changes": [_change_to_dict(change) for change in state.latest_changes],
    }


def _decode_state_semantic(semantic: Any) -> RouteReplayState:
    row = _exact_mapping(
        semantic,
        {
            "schema_version",
            "continuity_state",
            "missing_reasons",
            "processed_route_event_ids",
            "last_order_key",
            "entries",
            "latest_changes",
        },
        "route_state",
    )
    if row["schema_version"] != STATE_SCHEMA_VERSION:
        raise ResearchArtifactError("route_state schema_version 不受支持")
    continuity = row["continuity_state"]
    if continuity not in _CONTINUITY:
        raise ResearchArtifactError("route_state.continuity_state 非法")
    reasons_raw = row["missing_reasons"]
    if not isinstance(reasons_raw, list) or any(
        not isinstance(reason, str) or not reason for reason in reasons_raw
    ):
        raise ResearchArtifactError("route_state.missing_reasons 非法")
    if reasons_raw != sorted(set(reasons_raw)):
        raise ResearchArtifactError("route_state.missing_reasons 必须去重并排序")
    if (continuity == CONTINUOUS and reasons_raw) or (
        continuity == UNKNOWN_AFTER_GAP and not reasons_raw
    ):
        raise ResearchArtifactError("route_state 连续性与缺失原因矛盾")

    event_ids_raw = row["processed_route_event_ids"]
    if not isinstance(event_ids_raw, list) or event_ids_raw != sorted(set(event_ids_raw)):
        raise ResearchArtifactError("processed_route_event_ids 必须去重并排序")
    if any(
        not isinstance(value, str) or _ROUTE_EVENT_RE.fullmatch(value) is None
        for value in event_ids_raw
    ):
        raise ResearchArtifactError("processed_route_event_ids 包含非法身份")

    order_raw = row["last_order_key"]
    if order_raw is None:
        last_order_key = None
    elif isinstance(order_raw, list) and len(order_raw) == 4:
        last_order_key = tuple(
            _nonnegative(value, f"last_order_key[{index}]")
            for index, value in enumerate(order_raw)
        )
    else:
        raise ResearchArtifactError("last_order_key 必须是 null 或四整数数组")
    if bool(event_ids_raw) != (last_order_key is not None):
        raise ResearchArtifactError("last_order_key 与已处理事件集合矛盾")

    entries_raw = row["entries"]
    changes_raw = row["latest_changes"]
    if not isinstance(entries_raw, list) or not isinstance(changes_raw, list):
        raise ResearchArtifactError("entries/latest_changes 必须是数组")
    entries = tuple(
        _entry_from_dict(value, f"entries[{index}]")
        for index, value in enumerate(entries_raw)
    )
    changes = tuple(
        _change_from_dict(value, f"latest_changes[{index}]")
        for index, value in enumerate(changes_raw)
    )
    if tuple(sorted(entries, key=lambda item: item.key)) != entries:
        raise ResearchArtifactError("entries 必须按状态键排序")
    if tuple(sorted(changes, key=lambda item: item.key)) != changes:
        raise ResearchArtifactError("latest_changes 必须按状态键排序")
    if len({entry.key for entry in entries}) != len(entries):
        raise ResearchArtifactError("entries 状态键重复")
    if len({change.key for change in changes}) != len(changes):
        raise ResearchArtifactError("latest_changes 状态键重复")

    entry_by_key = {entry.key: entry for entry in entries}
    change_by_key = {change.key: change for change in changes}
    visible_change_keys = {
        change.key for change in changes if change.action in _VISIBLE_ACTIONS
    }
    if set(entry_by_key) != visible_change_keys:
        raise ResearchArtifactError("可见 entries 与 latest_changes 动作不闭合")
    for key, entry in entry_by_key.items():
        change = change_by_key[key]
        if (
            entry.last_action != change.action
            or entry.last_event_time_utc != change.event_time_utc
            or entry.as_path != change.as_path
            or entry.quality_flags != change.quality_flags
            or entry.last_raw_ref != change.raw_ref
        ):
            raise ResearchArtifactError("entry 与同键 latest_change 不一致")

    processed = frozenset(event_ids_raw)
    referenced_ids = {change.raw_ref.route_event_id for change in changes}
    if not referenced_ids.issubset(processed):
        raise ResearchArtifactError("latest_changes 引用了未处理的 RouteEvent")
    return RouteReplayState(
        entries=entries,
        latest_changes=changes,
        continuity_state=continuity,
        missing_reasons=tuple(reasons_raw),
        processed_route_event_ids=processed,
        last_order_key=last_order_key,
    )


def route_replay_state_to_payload(state: RouteReplayState) -> dict[str, Any]:
    """把状态编码为带语义指纹的规范 JSON 对象。"""

    if not isinstance(state, RouteReplayState):
        raise ResearchArtifactError("state 必须是 RouteReplayState")
    semantic = _state_semantic(state)
    rebuilt = _decode_state_semantic(semantic)
    if rebuilt != state:
        raise ResearchArtifactError("RouteReplayState 不是规范状态")
    fingerprint = hashlib.sha256(
        canonical_json({"schema": STATE_FINGERPRINT_SCHEMA, "state": semantic}).encode(
            "utf-8"
        )
    ).hexdigest()
    return {**semantic, "state_fingerprint_sha256": fingerprint}


def route_replay_state_from_payload(payload: Any) -> RouteReplayState:
    """复核语义指纹并恢复不可变状态。"""

    if not isinstance(payload, Mapping):
        raise ResearchArtifactError("route_state payload 必须是对象")
    semantic = dict(payload)
    fingerprint = semantic.pop("state_fingerprint_sha256", None)
    if not isinstance(fingerprint, str) or _SHA256_RE.fullmatch(fingerprint) is None:
        raise ResearchArtifactError("route_state fingerprint 非法")
    expected = hashlib.sha256(
        canonical_json({"schema": STATE_FINGERPRINT_SCHEMA, "state": semantic}).encode(
            "utf-8"
        )
    ).hexdigest()
    if fingerprint != expected:
        raise ResearchArtifactError("route_state 内容指纹不一致")
    return _decode_state_semantic(semantic)


def _safe_relative(value: Any, name: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ResearchArtifactError(f"{name} 必须是非空相对路径")
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts or any(
        part in (".", "..", "") for part in relative.parts
    ):
        raise ResearchArtifactError(f"{name} 必须是安全相对路径")
    return relative


def _root_and_target(root: os.PathLike[str] | str, relative: Any, name: str) -> Path:
    root_path = Path(root)
    try:
        root_meta = root_path.lstat()
    except OSError as error:
        raise ResearchArtifactError("研究输出根目录不存在或不可读") from error
    if stat.S_ISLNK(root_meta.st_mode) or not stat.S_ISDIR(root_meta.st_mode):
        raise ResearchArtifactError("研究输出根目录必须是非符号链接目录")
    safe = _safe_relative(relative, name)
    current = root_path
    for part in safe.parts[:-1]:
        current = current / part
        try:
            meta = current.lstat()
        except OSError as error:
            raise ResearchArtifactError(f"{name} 父路径不存在或不可读") from error
        if stat.S_ISLNK(meta.st_mode) or not stat.S_ISDIR(meta.st_mode):
            raise ResearchArtifactError(f"{name} 父路径不得是符号链接")
    return root_path.joinpath(*safe.parts)


def _read_regular(path: Path, *, maximum_bytes: int) -> Tuple[bytes, str]:
    if isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int) or maximum_bytes <= 0:
        raise ResearchArtifactError("maximum_bytes 必须是正整数")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ResearchArtifactError(f"研究制品不可安全读取：{path.name}") from error
    digest = hashlib.sha256()
    blocks = []
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ResearchArtifactError("研究制品不是普通文件")
        while True:
            block = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - size))
            if not block:
                break
            size += len(block)
            if size > maximum_bytes:
                raise ResearchArtifactError("研究制品超过恢复读取上限")
            blocks.append(block)
            digest.update(block)
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in identity):
            raise ResearchArtifactError("研究制品在读取期间发生变化")
    finally:
        os.close(descriptor)
    return b"".join(blocks), digest.hexdigest()


def publish_replay_checkpoint(
    output_root: os.PathLike[str] | str,
    *,
    state_relative_path: str,
    checkpoint_relative_path: str,
    state: RouteReplayState,
    run_id: str,
    phase: str,
    profile_sha256: str,
    input_selection_sha256: str,
    code_sha256: str,
    mapping_sha256: str,
    artifact_id: str,
    next_record_ordinal: int,
    published_shards: Sequence[Mapping[str, Any]] = (),
) -> PublishedReplayCheckpoint:
    """先发布不可变状态，再发布指向它的完整 record 边界检查点。"""

    state_target = _root_and_target(
        output_root, state_relative_path, "state_relative_path"
    )
    checkpoint_target = _root_and_target(
        output_root, checkpoint_relative_path, "checkpoint_relative_path"
    )
    state_artifact = write_canonical_jsonl_gzip(
        state_target,
        (route_replay_state_to_payload(state),),
        kind="route_replay_state",
    )
    checkpoint = build_checkpoint(
        run_id=run_id,
        phase=phase,
        profile_sha256=profile_sha256,
        input_selection_sha256=input_selection_sha256,
        code_sha256=code_sha256,
        mapping_sha256=mapping_sha256,
        artifact_id=artifact_id,
        next_record_ordinal=next_record_ordinal,
        state_ref={
            "path": _safe_relative(state_relative_path, "state_relative_path").as_posix(),
            "sha256": state_artifact.sha256,
        },
        published_shards=published_shards,
    )
    checkpoint_artifact = write_canonical_json(
        checkpoint_target, checkpoint, kind="route_replay_checkpoint"
    )
    return PublishedReplayCheckpoint(
        state_artifact=state_artifact,
        checkpoint_artifact=checkpoint_artifact,
        checkpoint=checkpoint,
    )


def restore_replay_checkpoint(
    output_root: os.PathLike[str] | str,
    checkpoint_relative_path: str,
    *,
    expected_bindings: Mapping[str, str],
    maximum_checkpoint_bytes: int = 16 * 1024 * 1024,
    maximum_state_compressed_bytes: int = 1024 * 1024 * 1024,
    maximum_state_uncompressed_bytes: int = 4 * 1024 * 1024 * 1024,
) -> RestoredReplayCheckpoint:
    """从不可变文件恢复状态；输入绑定或任一哈希不一致即拒绝。"""

    checkpoint_path = _root_and_target(
        output_root, checkpoint_relative_path, "checkpoint_relative_path"
    )
    checkpoint_bytes, checkpoint_sha = _read_regular(
        checkpoint_path, maximum_bytes=maximum_checkpoint_bytes
    )
    try:
        checkpoint_payload = json.loads(checkpoint_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ResearchArtifactError("checkpoint 不是合法 UTF-8 JSON") from error
    checkpoint = verify_checkpoint(
        checkpoint_payload, expected_bindings=expected_bindings
    )
    state_ref = checkpoint["state_ref"]
    state_path = _root_and_target(output_root, state_ref["path"], "state_ref.path")
    state_bytes, state_sha = _read_regular(
        state_path, maximum_bytes=maximum_state_compressed_bytes
    )
    if state_sha != state_ref["sha256"]:
        raise ResearchArtifactError("checkpoint 引用的状态文件 SHA256 不一致")
    try:
        uncompressed = gzip.decompress(state_bytes)
    except (OSError, EOFError) as error:
        raise ResearchArtifactError("状态 gzip EOF/CRC 校验失败") from error
    if len(uncompressed) > maximum_state_uncompressed_bytes:
        raise ResearchArtifactError("状态制品超过解压读取上限")
    lines = uncompressed.splitlines()
    if len(lines) != 1 or not uncompressed.endswith(b"\n"):
        raise ResearchArtifactError("状态 JSONL 必须恰有一条完整记录")
    try:
        state_payload = json.loads(lines[0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ResearchArtifactError("状态制品不是合法 UTF-8 JSONL") from error
    state = route_replay_state_from_payload(state_payload)
    return RestoredReplayCheckpoint(
        checkpoint=checkpoint,
        state=state,
        checkpoint_sha256=checkpoint_sha,
        state_sha256=state_sha,
    )


__all__ = (
    "PublishedReplayCheckpoint",
    "RestoredReplayCheckpoint",
    "STATE_SCHEMA_VERSION",
    "ResearchArtifactError",
    "publish_replay_checkpoint",
    "restore_replay_checkpoint",
    "route_replay_state_from_payload",
    "route_replay_state_to_payload",
)
