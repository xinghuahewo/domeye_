"""RRC25 seed RIB 的并行 native origin 预筛。

本模块只决定哪些 TABLE_DUMP_V2 RIB physical record 必须进入既有完整
parser。明确全非目标的 record 仍在 worker 进程中完整校验 prefix framing、
peer index、BGP attributes 与 AS_PATH；命中目标或 origin 不确定时只记录
ordinal，最终 RouteEvent 始终由顺序 parser/adapter 构造。
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import stat
import struct
from typing import Any, FrozenSet, Iterable, Mapping, Optional, Sequence, Tuple

from .country_impact import (
    MAPPED,
    CountryMappingView,
    RawRetentionMappingUnion,
)
from .file_artifacts import canonical_json
from .rib_parser import (
    MAX_MRT_PAYLOAD_BYTES,
    MRT_HEADER_LENGTH,
    PEER_INDEX_TABLE,
    RIB_IPV4_UNICAST,
    RIB_IPV6_UNICAST,
    TABLE_DUMP,
    TABLE_DUMP_IPV4,
    TABLE_DUMP_IPV6,
    TABLE_DUMP_V2,
    _Peer,
    _parse_peer_index_table,
    _v2_all_origins_non_target_entry_count,
)


UTC = timezone.utc
PREFILTER_SCHEMA_VERSION = "rrc25-parallel-rib-prefilter/v1"
PREFILTER_FINGERPRINT_SCHEMA = "rrc25_parallel_rib_prefilter_v1"
PREFILTER_ALGORITHM = (
    "native_v2_full_attribute_as_path_validation_parallel_then_ordered_replay_v1"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FILE_IDENTITY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


class RibPrefilterError(ValueError):
    """prefilter 输入、并行结果或 sidecar 不能安全用于顺序回放。"""


@dataclass(frozen=True)
class FrozenNonTargetPredicate:
    """两映射视图均明确映射到非目标国家的 ASN 集合及其身份。"""

    target_country: str
    known_non_target_asns: Tuple[int, ...]
    predicate_fingerprint_sha256: str
    source_bindings: Tuple[Tuple[str, str, str], ...]


_WORKER_DESCRIPTOR: Optional[int] = None
_WORKER_MMAP: Optional[mmap.mmap] = None
_WORKER_PEER_CONTEXTS: Tuple[Tuple[_Peer, ...], ...] = ()
_WORKER_NON_TARGET_ASNS: FrozenSet[int] = frozenset()


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise RibPrefilterError(f"{field} 必须是 64 位小写 SHA256")
    return value


def _stable_identity(metadata: os.stat_result) -> Tuple[int, ...]:
    return tuple(int(getattr(metadata, field)) for field in _FILE_IDENTITY_FIELDS)


def _mapping_non_target_asns(view: CountryMappingView) -> FrozenSet[int]:
    if not isinstance(view, CountryMappingView):
        raise RibPrefilterError("prefilter mapping view 类型非法")
    return frozenset(
        row.asn
        for row in view.assignments
        if row.mapping_state == MAPPED
        and row.countries[0] != view.target_country
    )


def freeze_non_target_predicate(
    mapping: CountryMappingView | RawRetentionMappingUnion,
) -> FrozenNonTargetPredicate:
    """冻结与 raw-retention 三值语义等价的“可明确丢弃 ASN”集合。"""

    if isinstance(mapping, RawRetentionMappingUnion):
        views = mapping.views
        target_country = mapping.target_country
        known = set(_mapping_non_target_asns(views[0]))
        for view in views[1:]:
            known.intersection_update(_mapping_non_target_asns(view))
        source_bindings = mapping.source_bindings
    elif isinstance(mapping, CountryMappingView):
        views = (mapping,)
        target_country = mapping.target_country
        known = set(_mapping_non_target_asns(mapping))
        source_bindings = (
            (mapping.view, mapping.source_sha256, mapping.source_ref),
        )
    else:
        raise RibPrefilterError("mapping 必须是冻结 mapping view 或 raw union")
    ordered = tuple(sorted(known))
    semantic = {
        "target_country": target_country,
        "source_bindings": [list(row) for row in source_bindings],
        "view_count": len(views),
        "known_non_target_asns": list(ordered),
        "semantics": "false_only_when_every_view_explicitly_maps_non_target",
    }
    fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": "rrc25_parallel_rib_non_target_predicate_v1",
                "predicate": semantic,
            }
        ).encode("utf-8")
    ).hexdigest()
    return FrozenNonTargetPredicate(
        target_country=target_country,
        known_non_target_asns=ordered,
        predicate_fingerprint_sha256=fingerprint,
        source_bindings=source_bindings,
    )


def _artifact_slot_epoch(value: object) -> int:
    if not isinstance(value, str):
        raise RibPrefilterError("artifact_slot_utc 必须是字符串")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError as error:
        raise RibPrefilterError("artifact_slot_utc 必须是秒级 UTC Z 时间") from error
    epoch = int(parsed.timestamp())
    if epoch % (8 * 60 * 60):
        raise RibPrefilterError("artifact_slot_utc 未对齐八小时槽")
    return epoch


def _record_layout(
    raw: mmap.mmap,
    *,
    slot_start_epoch: int,
) -> Tuple[
    Tuple[Tuple[_Peer, ...], ...],
    int,
    int,
    int,
]:
    """首遍只扫描 physical boundaries，并冻结 peer contexts。"""

    offset = 0
    ordinal = 0
    peer_contexts = []
    active_peer_context = False
    v2_rib_count = 0
    table_dump_count = 0
    while offset < len(raw):
        if offset + MRT_HEADER_LENGTH > len(raw):
            raise RibPrefilterError("MRT common header 截断")
        timestamp, mrt_type, subtype, payload_length = struct.unpack(
            "!IHHI", raw[offset : offset + MRT_HEADER_LENGTH]
        )
        if payload_length > MAX_MRT_PAYLOAD_BYTES:
            raise RibPrefilterError("MRT payload 超过 64 MiB 安全上限")
        end = offset + MRT_HEADER_LENGTH + payload_length
        if end > len(raw):
            raise RibPrefilterError(f"MRT record[{ordinal}] payload 截断")
        if not slot_start_epoch <= timestamp < slot_start_epoch + 8 * 60 * 60:
            raise RibPrefilterError(f"MRT record[{ordinal}] 时间越出 artifact 槽")
        if mrt_type == TABLE_DUMP:
            if subtype not in {TABLE_DUMP_IPV4, TABLE_DUMP_IPV6}:
                raise RibPrefilterError("TABLE_DUMP subtype 未获准")
            table_dump_count += 1
            active_peer_context = False
        elif mrt_type == TABLE_DUMP_V2:
            if subtype == PEER_INDEX_TABLE:
                peer_contexts.append(
                    _parse_peer_index_table(
                        raw[offset + MRT_HEADER_LENGTH : end]
                    )
                )
                active_peer_context = True
            elif subtype in {RIB_IPV4_UNICAST, RIB_IPV6_UNICAST}:
                if not active_peer_context:
                    raise RibPrefilterError(
                        "TABLE_DUMP_V2 RIB 缺少此前 PEER_INDEX_TABLE"
                    )
                v2_rib_count += 1
            else:
                raise RibPrefilterError("TABLE_DUMP_V2 subtype 未获准")
        else:
            raise RibPrefilterError("prefilter 只接受 TABLE_DUMP type 12/13")
        ordinal += 1
        offset = end
    if ordinal == 0:
        raise RibPrefilterError("RIB spool 为空")
    return tuple(peer_contexts), ordinal, v2_rib_count, table_dump_count


def _iter_v2_batches(
    raw: mmap.mmap,
    *,
    batch_records: int,
) -> Iterable[
    Tuple[int, Tuple[Tuple[int, int, int, int, int], ...]]
]:
    """第二遍按稳定 ordinal 生成可独立并行的 V2 RIB 边界批次。"""

    offset = 0
    ordinal = 0
    context_cursor = -1
    active_context_index = -1
    batch_index = 0
    batch = []
    while offset < len(raw):
        _timestamp, mrt_type, subtype, payload_length = struct.unpack(
            "!IHHI", raw[offset : offset + MRT_HEADER_LENGTH]
        )
        end = offset + MRT_HEADER_LENGTH + payload_length
        if mrt_type == TABLE_DUMP_V2 and subtype == PEER_INDEX_TABLE:
            context_cursor += 1
            active_context_index = context_cursor
        elif mrt_type == TABLE_DUMP:
            active_context_index = -1
        elif mrt_type == TABLE_DUMP_V2:
            if active_context_index < 0:
                raise RibPrefilterError("并行边界缺少 peer context")
            batch.append(
                (
                    ordinal,
                    offset,
                    payload_length,
                    subtype,
                    active_context_index,
                )
            )
            if len(batch) >= batch_records:
                yield batch_index, tuple(batch)
                batch_index += 1
                batch.clear()
        ordinal += 1
        offset = end
    if batch:
        yield batch_index, tuple(batch)


def _prefilter_worker_init(
    spool_path: str,
    expected_size_bytes: int,
    peer_contexts: Tuple[Tuple[_Peer, ...], ...],
    known_non_target_asns: Tuple[int, ...],
) -> None:
    global _WORKER_DESCRIPTOR
    global _WORKER_MMAP
    global _WORKER_PEER_CONTEXTS
    global _WORKER_NON_TARGET_ASNS

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(spool_path, flags)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != expected_size_bytes:
        os.close(descriptor)
        raise RibPrefilterError("prefilter worker spool 身份不一致")
    _WORKER_DESCRIPTOR = descriptor
    _WORKER_MMAP = mmap.mmap(descriptor, 0, access=mmap.ACCESS_READ)
    _WORKER_PEER_CONTEXTS = peer_contexts
    _WORKER_NON_TARGET_ASNS = frozenset(known_non_target_asns)


def _prefilter_membership(asn: int) -> bool:
    return asn not in _WORKER_NON_TARGET_ASNS


def _prefilter_batch(
    item: Tuple[int, Tuple[Tuple[int, int, int, int, int], ...]],
) -> Tuple[int, Tuple[int, ...], int, int]:
    batch_index, boundaries = item
    raw = _WORKER_MMAP
    if raw is None:
        raise RibPrefilterError("prefilter worker 未初始化 mmap")
    selected = []
    discarded_records = 0
    discarded_elements = 0
    for ordinal, offset, payload_length, subtype, context_index in boundaries:
        end = offset + MRT_HEADER_LENGTH + payload_length
        payload = memoryview(raw)[offset + MRT_HEADER_LENGTH : end]
        try:
            count = _v2_all_origins_non_target_entry_count(
                payload,
                subtype,
                _WORKER_PEER_CONTEXTS[context_index],
                _prefilter_membership,
            )
        finally:
            payload.release()
        if count is None:
            selected.append(ordinal)
        else:
            discarded_records += 1
            discarded_elements += count
    return (
        batch_index,
        tuple(selected),
        discarded_records,
        discarded_elements,
    )


def _receipt_payload(semantic: Mapping[str, Any]) -> Mapping[str, Any]:
    normalized = {**dict(semantic), "schema_version": PREFILTER_SCHEMA_VERSION}
    fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": PREFILTER_FINGERPRINT_SCHEMA,
                "prefilter": normalized,
            }
        ).encode("utf-8")
    ).hexdigest()
    return {
        **normalized,
        "receipt_fingerprint_sha256": fingerprint,
    }


def build_parallel_rib_prefilter(
    spool_path: os.PathLike[str] | str,
    *,
    expected_spool_sha256: str,
    expected_spool_size_bytes: int,
    seed_artifact_id: str,
    seed_file_sha256: str,
    artifact_slot_utc: str,
    raw_retention_mapping: CountryMappingView | RawRetentionMappingUnion,
    workers: int,
    batch_records: int = 4096,
) -> Mapping[str, Any]:
    """并行构建与 spool、artifact 和 raw-retention predicate 绑定的 sidecar。"""

    spool_sha = _sha256(expected_spool_sha256, "expected_spool_sha256")
    seed_sha = _sha256(seed_file_sha256, "seed_file_sha256")
    if (
        isinstance(expected_spool_size_bytes, bool)
        or not isinstance(expected_spool_size_bytes, int)
        or expected_spool_size_bytes <= 0
    ):
        raise RibPrefilterError("expected_spool_size_bytes 必须是正整数")
    if (
        isinstance(workers, bool)
        or not isinstance(workers, int)
        or not 1 <= workers <= 64
    ):
        raise RibPrefilterError("workers 必须位于 [1,64]")
    if (
        isinstance(batch_records, bool)
        or not isinstance(batch_records, int)
        or not 64 <= batch_records <= 65_536
    ):
        raise RibPrefilterError("batch_records 必须位于 [64,65536]")
    predicate = freeze_non_target_predicate(raw_retention_mapping)
    slot_start = _artifact_slot_epoch(artifact_slot_utc)
    path = Path(spool_path).expanduser().resolve(strict=True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    raw: Optional[mmap.mmap] = None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size != expected_spool_size_bytes
        ):
            raise RibPrefilterError("spool 类型或大小与 attestation 不一致")
        raw = mmap.mmap(descriptor, 0, access=mmap.ACCESS_READ)
        if hashlib.sha256(raw).hexdigest() != spool_sha:
            raise RibPrefilterError("spool SHA256 与 attestation 不一致")
        (
            peer_contexts,
            physical_record_count,
            v2_rib_count,
            table_dump_count,
        ) = _record_layout(raw, slot_start_epoch=slot_start)
        batches = tuple(
            _iter_v2_batches(raw, batch_records=batch_records)
        )

        results = []
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_prefilter_worker_init,
            initargs=(
                str(path),
                expected_spool_size_bytes,
                peer_contexts,
                predicate.known_non_target_asns,
            ),
        ) as executor:
            for result in executor.map(_prefilter_batch, batches, chunksize=1):
                results.append(result)
        after = os.fstat(descriptor)
        if (
            _stable_identity(before) != _stable_identity(after)
            or hashlib.sha256(raw).hexdigest() != spool_sha
        ):
            raise RibPrefilterError("spool 在并行 prefilter 期间发生变化")
    finally:
        if raw is not None:
            raw.close()
        os.close(descriptor)
    results.sort(key=lambda row: row[0])
    selected = tuple(
        ordinal
        for _batch_index, ordinals, _records, _elements in results
        for ordinal in ordinals
    )
    if selected != tuple(sorted(set(selected))):
        raise RibPrefilterError("并行 prefilter selected ordinals 不唯一或乱序")
    discarded_records = sum(row[2] for row in results)
    discarded_elements = sum(row[3] for row in results)
    if discarded_records + len(selected) != v2_rib_count:
        raise RibPrefilterError("并行 prefilter record 人口不闭合")
    return _receipt_payload(
        {
            "algorithm": PREFILTER_ALGORITHM,
            "spool": {
                "path": str(path),
                "sha256": spool_sha,
                "size_bytes": expected_spool_size_bytes,
            },
            "seed_artifact": {
                "artifact_id": seed_artifact_id,
                "file_sha256": seed_sha,
                "artifact_slot_utc": artifact_slot_utc,
            },
            "raw_retention_predicate": {
                "target_country": predicate.target_country,
                "predicate_fingerprint_sha256": (
                    predicate.predicate_fingerprint_sha256
                ),
                "source_bindings": [
                    list(row) for row in predicate.source_bindings
                ],
                "known_non_target_asn_count": len(
                    predicate.known_non_target_asns
                ),
            },
            "population": {
                "physical_record_count": physical_record_count,
                "peer_index_context_count": len(peer_contexts),
                "table_dump_v1_record_count": table_dump_count,
                "v2_rib_record_count": v2_rib_count,
                "materialized_v2_rib_record_count": len(selected),
                "discarded_v2_rib_record_count": discarded_records,
                "discarded_v2_element_count": discarded_elements,
            },
            "materialize_v2_rib_ordinals": list(selected),
        }
    )


def validate_rib_prefilter(
    receipt: Mapping[str, Any],
    *,
    expected_spool_sha256: str,
    expected_spool_size_bytes: int,
    seed_artifact_id: str,
    seed_file_sha256: str,
    artifact_slot_utc: str,
    raw_retention_mapping: CountryMappingView | RawRetentionMappingUnion,
) -> FrozenSet[int]:
    """验证 sidecar 身份并返回顺序 parser 必须完整物化的 V2 ordinals。"""

    if not isinstance(receipt, Mapping):
        raise RibPrefilterError("prefilter receipt 必须是对象")
    semantic = {
        key: value
        for key, value in receipt.items()
        if key not in {"schema_version", "receipt_fingerprint_sha256"}
    }
    if dict(receipt) != _receipt_payload(semantic):
        raise RibPrefilterError("prefilter receipt schema 或指纹不闭合")
    spool = receipt.get("spool")
    artifact = receipt.get("seed_artifact")
    predicate_row = receipt.get("raw_retention_predicate")
    population = receipt.get("population")
    ordinals = receipt.get("materialize_v2_rib_ordinals")
    predicate = freeze_non_target_predicate(raw_retention_mapping)
    if (
        receipt.get("algorithm") != PREFILTER_ALGORITHM
        or not isinstance(spool, Mapping)
        or spool.get("sha256") != _sha256(
            expected_spool_sha256, "expected_spool_sha256"
        )
        or spool.get("size_bytes") != expected_spool_size_bytes
        or not isinstance(artifact, Mapping)
        or artifact.get("artifact_id") != seed_artifact_id
        or artifact.get("file_sha256") != _sha256(
            seed_file_sha256, "seed_file_sha256"
        )
        or artifact.get("artifact_slot_utc") != artifact_slot_utc
        or not isinstance(predicate_row, Mapping)
        or predicate_row.get("target_country") != predicate.target_country
        or predicate_row.get("predicate_fingerprint_sha256")
        != predicate.predicate_fingerprint_sha256
        or predicate_row.get("source_bindings")
        != [list(row) for row in predicate.source_bindings]
        or predicate_row.get("known_non_target_asn_count")
        != len(predicate.known_non_target_asns)
        or not isinstance(population, Mapping)
        or not isinstance(ordinals, list)
    ):
        raise RibPrefilterError("prefilter receipt 与 spool/artifact/mapping 不绑定")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in ordinals
    ):
        raise RibPrefilterError("prefilter ordinals 必须是非负整数")
    ordered = tuple(ordinals)
    if ordered != tuple(sorted(set(ordered))):
        raise RibPrefilterError("prefilter ordinals 必须唯一递增")
    physical_count = population.get("physical_record_count")
    v2_count = population.get("v2_rib_record_count")
    materialized = population.get("materialized_v2_rib_record_count")
    discarded = population.get("discarded_v2_rib_record_count")
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (physical_count, v2_count, materialized, discarded)
        )
        or any(value >= physical_count for value in ordered)
        or materialized != len(ordered)
        or materialized + discarded != v2_count
    ):
        raise RibPrefilterError("prefilter population 与 ordinal 集合不闭合")
    return frozenset(ordered)


def load_rib_prefilter(path_value: os.PathLike[str] | str) -> Mapping[str, Any]:
    """读取一份规范、受限大小的 sidecar JSON。"""

    path = Path(path_value).expanduser().resolve(strict=True)
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size > 128 * 1024 * 1024
    ):
        raise RibPrefilterError("prefilter sidecar 必须是至多 128MiB 普通文件")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RibPrefilterError("prefilter sidecar JSON 非法") from error
    if not isinstance(payload, Mapping):
        raise RibPrefilterError("prefilter sidecar 根节点必须是对象")
    if raw != (canonical_json(dict(payload)) + "\n").encode("utf-8"):
        raise RibPrefilterError("prefilter sidecar 必须是规范 JSON")
    return dict(payload)


__all__ = (
    "FrozenNonTargetPredicate",
    "PREFILTER_ALGORITHM",
    "PREFILTER_SCHEMA_VERSION",
    "RibPrefilterError",
    "build_parallel_rib_prefilter",
    "freeze_non_target_predicate",
    "load_rib_prefilter",
    "validate_rib_prefilter",
)
