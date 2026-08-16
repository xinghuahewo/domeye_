"""把 sealed finalization segments 适配为完整窗口业务派生输入。

本模块是可恢复 finalization workspace 与既有纯业务派生器之间的窄桥：

* UPDATE 的 RouteEvent、raw ref、parser attestation、双视图国家槽以及
  control/record-observation 语义摘要，只从已封口、逐槽 hash-chain 保护的
  segment payload 读取；
* 原 journal 只读取边界 receipt、attempt/outcome、raw-ledger 小文件和 genesis
  的三个 seed shard；
* 绝不打开、哈希或解压原 ``record_observations``，也不读取 MRT 或数据库；
* 返回的 ancestry inventory 与 copy source 一一对应，只包含最终包真正能够
  自包含封存的文件。

这里有意复用 ``full_window_finalize`` 的严格领域校验和内部值对象。该适配器
与纯业务派生器属于同一受版本冻结的实现单元；若其内部契约变化，应由本模块
的端到端测试在代码冻结前直接失败。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Mapping, Optional, Sequence, Tuple

from . import full_window_finalize as _finalizer
from . import full_window_finalize_workspace as _workspace
from . import full_window_journal as _journal
from .country_impact import (
    mapping_bundle_sha256,
    mapping_snapshot_sha256,
    mapping_view_from_frozen_snapshot,
    mapping_view_from_revised_snapshot,
)
from .full_window_worker import (
    RAW_RECORD_REF_SHARD_SCHEMA_VERSION,
    ROUTE_EVENT_SHARD_SCHEMA_VERSION,
    raw_record_ref_id_v1,
)
from .profile import profile_sha256
from .source_fact import load_frozen_incident_fact
from .full_window_selection import validate_complete_selection_against_profile
from ...route_event import artifact_id_v1


_BOUNDARY_RECEIPT_FIELDS = {
    "schema_version",
    "fingerprint_sha256",
    "run_id",
    "bindings",
    "sequence",
    "next_artifact_index",
    "total_artifacts",
    "committed_artifact",
    "attempt_ref",
    "outcome_ref",
    "state_ref",
    "shards",
    "shard_chain_sha256",
    "previous_receipt_ref",
    "raw_genesis_ref",
}
_SHARD_REF_FIELDS = {"kind", "path", "sha256", "size_bytes", "record_count"}
_SEGMENT_PAYLOAD_FIELDS = {
    "schema_version",
    "fingerprint_sha256",
    "sequence",
    "artifact",
    "journal_receipt_ref",
    "previous_journal_receipt_ref",
    "journal_shard_chain_sha256",
    "route_event_rows",
    "raw_record_ref_rows",
    "parser_attestation",
    "country_slots",
    "control_record_summary",
    "record_observation_summary",
    "state_ref_sha256_verified",
    "resource_accounting",
}
_SUMMARY_FIELDS = {
    "schema_version",
    "sequence",
    "path",
    "record_count",
    "semantic_sha256",
}


class FullWindowSegmentProductError(_finalizer.FullWindowFinalizeError):
    """sealed segment 无法构成完整、可复算的业务输入。"""


@dataclass(frozen=True)
class SegmentProductInputs:
    """纯业务派生所需输入及与最终封包一致的自包含 ancestry。"""

    inputs: _finalizer._FinalizationInputs
    journal_ancestry_inventory: Tuple[Mapping[str, Any], ...]
    copy_sources: Tuple[Mapping[str, Any], ...]
    verification: Mapping[str, Any]


def _fail(message: str) -> None:
    raise FullWindowSegmentProductError(message)


def _safe_relative(value: Any, field: str) -> str:
    if not isinstance(value, str):
        _fail(f"{field} 必须是相对路径")
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(
        part in {"", ".", ".."} for part in pure.parts
    ):
        _fail(f"{field} 不是安全相对路径")
    return pure.as_posix()


def _regular_metadata(path: Path, *, expected_size: Optional[int] = None) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise FullWindowSegmentProductError(f"ancestry 文件不可读：{path}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        _fail(f"ancestry 文件必须是非符号链接普通文件：{path}")
    if expected_size is not None and metadata.st_size != expected_size:
        _fail(f"ancestry 文件 size 与 sealed ref 不一致：{path}")
    return metadata


def _normalize_shard_ref(
    ref: Any,
    *,
    artifact_index: Optional[int],
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(ref, Mapping) or set(ref) != _SHARD_REF_FIELDS:
        _fail(f"{field} 字段不闭合")
    kind = ref.get("kind")
    if not isinstance(kind, str) or not kind:
        _fail(f"{field}.kind 非法")
    digest = _finalizer._sha(ref.get("sha256"), f"{field}.sha256")
    size = _finalizer._nonnegative(ref.get("size_bytes"), f"{field}.size_bytes")
    count = _finalizer._nonnegative(
        ref.get("record_count"), f"{field}.record_count"
    )
    relative = _safe_relative(ref.get("path"), f"{field}.path")
    expected = (
        f"shards/{kind}/genesis-{digest}.jsonl.gz"
        if artifact_index is None
        else f"shards/{kind}/slot-{artifact_index:04d}-{digest}.jsonl.gz"
    )
    if relative != expected:
        _fail(f"{field}.path 未绑定 kind/槽位/SHA256")
    return {
        "kind": kind,
        "path": relative,
        "sha256": digest,
        "size_bytes": size,
        "record_count": count,
    }


def _validate_summary(
    summary: Any,
    *,
    schema: str,
    sequence: int,
    ref: Mapping[str, Any],
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(summary, Mapping) or set(summary) != _SUMMARY_FIELDS:
        _fail(f"{field} 字段不闭合")
    if (
        summary.get("schema_version") != schema
        or summary.get("sequence") != sequence
        or summary.get("path") != ref.get("path")
        or summary.get("record_count") != ref.get("record_count")
    ):
        _fail(f"{field} 未闭合到当前 journal shard ref")
    _finalizer._sha(summary.get("semantic_sha256"), f"{field}.semantic_sha256")
    return dict(summary)


def _load_journal_receipts_without_shards(
    *,
    journal_root: Path,
    receipt_refs: Sequence[Mapping[str, Any]],
    bindings: Mapping[str, str],
    expected_total: int,
) -> Tuple[
    Tuple[Tuple[Mapping[str, Any], Mapping[str, Any]], ...],
    Tuple[str, ...],
    float,
    int,
    int,
]:
    """核验边界 receipt/attempt/outcome，但不碰任何 UPDATE shard。"""

    if len(receipt_refs) != expected_total + 1:
        _fail("workspace journal_receipt_refs 数量与 total_slots 不闭合")
    rows = []
    complete_attempt_ids = []
    max_worker_seconds = 0.0
    peak_temporary_bytes = 0
    database_write_operations = 0
    previous_ref: Optional[Mapping[str, Any]] = None
    previous_receipt: Optional[Mapping[str, Any]] = None
    raw_genesis_ref: Optional[Mapping[str, Any]] = None
    shard_chain: Optional[str] = None
    run_id: Optional[str] = None

    for sequence, raw_ref in enumerate(receipt_refs):
        receipt = _finalizer._read_hashed_json(
            journal_root, raw_ref, field=f"journal receipt {sequence}"
        )
        try:
            receipt = _journal._verify_fingerprint(
                receipt,
                _journal.BOUNDARY_RECEIPT_SCHEMA_VERSION,
                f"journal receipt {sequence}",
            )
        except _journal.FullWindowJournalError as error:
            raise FullWindowSegmentProductError(
                f"journal receipt {sequence} fingerprint 非法"
            ) from error
        if set(receipt) != _BOUNDARY_RECEIPT_FIELDS:
            _fail(f"journal receipt {sequence} 字段不闭合")
        if (
            receipt.get("sequence") != sequence
            or receipt.get("next_artifact_index") != sequence
            or receipt.get("total_artifacts") != expected_total
            or receipt.get("bindings") != dict(bindings)
        ):
            _fail(f"journal receipt {sequence} 游标/总数/bindings 不闭合")
        current_run_id = receipt.get("run_id")
        if not isinstance(current_run_id, str) or not current_run_id:
            _fail(f"journal receipt {sequence} run_id 非法")
        if run_id is None:
            run_id = current_run_id
            raw_genesis_ref = receipt.get("raw_genesis_ref")
        elif (
            current_run_id != run_id
            or receipt.get("raw_genesis_ref") != raw_genesis_ref
        ):
            _fail(f"journal receipt {sequence} run/raw genesis 身份漂移")
        state_ref = receipt.get("state_ref")
        if (
            not isinstance(state_ref, Mapping)
            or set(state_ref) != {"slot", "path", "sha256"}
            or state_ref.get("slot") not in {"a", "b"}
            or state_ref.get("path") != f"scratch/state-{state_ref.get('slot')}.jsonl.gz"
        ):
            _fail(f"journal receipt {sequence} state_ref 非法")
        _finalizer._sha(state_ref.get("sha256"), "journal state_ref.sha256")

        artifact = None
        if sequence == 0:
            if any(
                receipt.get(name) is not None
                for name in (
                    "committed_artifact",
                    "attempt_ref",
                    "outcome_ref",
                    "previous_receipt_ref",
                )
            ):
                _fail("journal genesis receipt 伪造了 UPDATE 事务")
            if previous_ref is not None or previous_receipt is not None:
                _fail("journal genesis receipt 前驱非法")
        else:
            if receipt.get("previous_receipt_ref") != previous_ref:
                _fail(f"journal receipt {sequence} previous ref 不连续")
            try:
                artifact = _journal._artifact_from_dict(
                    receipt.get("committed_artifact"),
                    f"journal receipt {sequence}.artifact",
                )
                attempt_ref = _journal._closed_ref(
                    receipt.get("attempt_ref"), "attempt_ref"
                )
                outcome_ref = _journal._closed_ref(
                    receipt.get("outcome_ref"), "outcome_ref"
                )
                attempt = _journal._load_attempt(journal_root, attempt_ref)
                outcome = _journal._load_outcome(journal_root, outcome_ref)
                proof = _journal._proof_from_dict(outcome.get("proof"))
                _journal._verify_single_pass(proof, artifact)
            except (OSError, _journal.FullWindowJournalError) as error:
                raise FullWindowSegmentProductError(
                    f"journal receipt {sequence} attempt/outcome/proof 非法"
                ) from error
            if (
                artifact.index != sequence - 1
                or attempt.get("run_id") != run_id
                or attempt.get("bindings") != dict(bindings)
                or attempt.get("artifact") != artifact.to_dict()
                or attempt.get("base_receipt_ref") != previous_ref
                or attempt.get("reserved_raw_bytes") != artifact.size_bytes
                or outcome.get("attempt_ref") != attempt_ref
                or outcome.get("attempt_id") != attempt.get("attempt_id")
                or outcome.get("outcome") != "complete_single_pass"
                or outcome.get("failure_reason") is not None
                or outcome.get("reservation_refunded_bytes") != 0
                or outcome.get("observed_compressed_bytes") != artifact.size_bytes
            ):
                _fail(f"journal receipt {sequence} attempt/outcome 事务不闭合")
            complete_attempt_ids.append(str(outcome["attempt_id"]))
            max_worker_seconds = max(
                max_worker_seconds, float(proof.process_seconds)
            )
            peak_temporary_bytes = max(
                peak_temporary_bytes, int(proof.peak_temporary_bytes)
            )
            database_write_operations += int(proof.database_write_operations)

        raw_shards = receipt.get("shards")
        if not isinstance(raw_shards, list):
            _fail(f"journal receipt {sequence}.shards 非法")
        shards = tuple(
            _normalize_shard_ref(
                value,
                artifact_index=None if artifact is None else artifact.index,
                field=f"journal receipt {sequence}.shards[{index}]",
            )
            for index, value in enumerate(raw_shards)
        )
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
        if (
            list(shards)
            != sorted(shards, key=lambda row: (row["kind"], row["path"]))
            or len({row["kind"] for row in shards}) != len(shards)
            or {row["kind"] for row in shards} != expected_kinds
        ):
            _fail(f"journal receipt {sequence} shard kind/顺序不闭合")
        expected_chain = (
            _journal._advance_genesis_chain(shards)
            if sequence == 0
            else _journal._advance_chain(str(shard_chain), artifact, shards)
        )
        if receipt.get("shard_chain_sha256") != expected_chain:
            _fail(f"journal receipt {sequence} shard hash chain 不闭合")
        shard_chain = expected_chain
        normalized_ref = {
            "path": _safe_relative(raw_ref.get("path"), "journal receipt ref.path"),
            "sha256": _finalizer._sha(
                raw_ref.get("sha256"), "journal receipt ref.sha256"
            ),
        }
        rows.append((normalized_ref, dict(receipt)))
        previous_ref = normalized_ref
        previous_receipt = receipt

    return (
        tuple(rows),
        tuple(complete_attempt_ids),
        max_worker_seconds,
        peak_temporary_bytes,
        database_write_operations,
    )


def _validate_workspace_segments(
    *,
    workspace_root: Path,
    genesis: Mapping[str, Any],
    terminal: Mapping[str, Any],
    head: Mapping[str, Any],
    journal_receipts: Sequence[Tuple[Mapping[str, Any], Mapping[str, Any]]],
) -> Tuple[
    Tuple[Mapping[str, Any], ...],
    Tuple[Mapping[str, Any], ...],
    Tuple[Mapping[str, Any], ...],
]:
    """逐槽核验 segment/deep 链；只读取 segment 文件。"""

    total = int(genesis["total_slots"])
    receipt_refs = terminal.get("segment_receipt_refs")
    payload_refs = terminal.get("segment_payload_refs")
    deep_refs = terminal.get("deep_segment_receipt_refs")
    if (
        not isinstance(receipt_refs, list)
        or not isinstance(payload_refs, list)
        or not isinstance(deep_refs, list)
        or len(receipt_refs) != total
        or len(payload_refs) != total
        or len(deep_refs) != total
    ):
        _fail("TERMINAL segment/deep refs 未逐槽闭合")

    segment_receipts = []
    payloads = []
    deep_receipts = []
    previous_segment_ref = None
    previous_deep_ref = None
    deep_chain = _workspace._hash(
        {
            "schema": "rrc25_full_window_finalization_deep_chain_genesis_v1",
            "run_id": genesis["run_id"],
            "bindings": genesis["bindings"],
            "journal_terminal_receipt_ref": genesis[
                "journal_terminal_receipt_ref"
            ],
        }
    )
    cumulative_package = 0
    cumulative_observation = 0
    cumulative_seconds = 0.0
    maximum_slot_seconds = 0.0
    maximum_temporary = 0
    cumulative_payload_bytes = 0

    for sequence, (segment_ref, payload_ref, deep_ref) in enumerate(
        zip(receipt_refs, payload_refs, deep_refs), start=1
    ):
        receipt = _workspace._load_segment_receipt(workspace_root, segment_ref)
        payload = _workspace._load_segment_payload(workspace_root, payload_ref)
        deep = _workspace._load_deep_segment_receipt(workspace_root, deep_ref)
        if set(payload) != _SEGMENT_PAYLOAD_FIELDS:
            _fail(f"segment payload {sequence} 字段不闭合")
        journal_ref, journal_receipt = journal_receipts[sequence]
        if (
            receipt.get("sequence") != sequence
            or receipt.get("total_slots") != total
            or receipt.get("previous_segment_receipt_ref")
            != previous_segment_ref
            or receipt.get("segment_payload_ref") != payload_ref
            or receipt.get("journal_receipt_ref") != journal_ref
            or payload.get("sequence") != sequence
            or payload.get("journal_receipt_ref") != journal_ref
            or payload.get("previous_journal_receipt_ref")
            != journal_receipts[sequence - 1][0]
            or payload.get("journal_shard_chain_sha256")
            != journal_receipt.get("shard_chain_sha256")
            or receipt.get("journal_shard_chain_sha256")
            != journal_receipt.get("shard_chain_sha256")
            or payload.get("artifact")
            != journal_receipt.get("committed_artifact")
            or receipt.get("artifact")
            != journal_receipt.get("committed_artifact")
            or payload.get("state_ref_sha256_verified") is not True
            or receipt.get("state_ref_sha256_verified") is not True
        ):
            _fail(f"segment {sequence} 未闭合到 journal receipt/artifact/state_ref")
        current = receipt.get("slot_resource_accounting")
        cumulative = receipt.get("cumulative_resource_accounting")
        payload_resource = payload.get("resource_accounting")
        if (
            not isinstance(current, Mapping)
            or not isinstance(cumulative, Mapping)
            or not isinstance(payload_resource, Mapping)
            or current.get("database_write_operations") != 0
            or payload_resource.get("database_write_operations") != 0
        ):
            _fail(f"segment {sequence} resource accounting 非法")
        package_bytes = _finalizer._nonnegative(
            current.get("package_bytes_read"), "segment package_bytes_read"
        )
        observation_bytes = _finalizer._nonnegative(
            current.get("record_observation_bytes_read"),
            "segment record_observation_bytes_read",
        )
        slot_seconds = current.get("finalization_seconds")
        temporary = _finalizer._nonnegative(
            current.get("temporary_bytes"), "segment temporary_bytes"
        )
        if (
            isinstance(slot_seconds, bool)
            or not isinstance(slot_seconds, (int, float))
            or not math.isfinite(float(slot_seconds))
            or float(slot_seconds) < 0
            or payload_resource.get("source_package_bytes_read") != package_bytes
            or payload_resource.get("record_observation_compressed_bytes_read")
            != observation_bytes
        ):
            _fail(f"segment {sequence} resource accounting 与 payload 不一致")
        cumulative_package += package_bytes
        cumulative_observation += observation_bytes
        cumulative_seconds += float(slot_seconds)
        maximum_slot_seconds = max(maximum_slot_seconds, float(slot_seconds))
        maximum_temporary = max(maximum_temporary, temporary)
        expected_cumulative = {
            "cumulative_package_bytes_read": cumulative_package,
            "cumulative_record_observation_bytes_read": cumulative_observation,
            "cumulative_finalization_seconds": cumulative_seconds,
            "maximum_slot_seconds": maximum_slot_seconds,
            "maximum_temporary_bytes": maximum_temporary,
            "database_write_operations": 0,
        }
        if (
            any(
                not math.isclose(
                    float(cumulative.get(key)),
                    float(value),
                    rel_tol=0,
                    abs_tol=1e-9,
                )
                if isinstance(value, float)
                else cumulative.get(key) != value
                for key, value in expected_cumulative.items()
            )
            or deep.get("sequence") != sequence
            or deep.get("total_slots") != total
            or deep.get("previous_deep_segment_receipt_ref") != previous_deep_ref
            or deep.get("previous_deep_chain_sha256") != deep_chain
            or deep.get("segment_receipt_ref") != segment_ref
            or deep.get("segment_payload_ref") != payload_ref
            or deep.get("journal_receipt_ref") != journal_ref
            or deep.get("cumulative_resource_accounting") != cumulative
            or deep.get("record_observation_reread_count") != 0
            or deep.get("database_write_operations") != 0
        ):
            _fail(f"segment {sequence} cumulative/deep receipt 链不闭合")
        cumulative_payload_bytes += int(payload_ref["size_bytes"])
        expected_deep_chain = _workspace._deep_chain_advance(
            deep_chain,
            sequence=sequence,
            segment_receipt_ref=segment_ref,
            segment_payload_ref=payload_ref,
            cumulative_resource_accounting=cumulative,
            cumulative_segment_payload_bytes=cumulative_payload_bytes,
        )
        if (
            deep.get("deep_chain_sha256") != expected_deep_chain
            or deep.get("cumulative_segment_payload_bytes")
            != cumulative_payload_bytes
        ):
            _fail(f"segment {sequence} deep hash chain 不闭合")
        previous_segment_ref = dict(segment_ref)
        previous_deep_ref = dict(deep_ref)
        deep_chain = expected_deep_chain
        segment_receipts.append(dict(receipt))
        payloads.append(dict(payload))
        deep_receipts.append(dict(deep))

    if (
        terminal.get("terminal_segment_receipt_ref") != previous_segment_ref
        or terminal.get("terminal_deep_segment_receipt_ref") != previous_deep_ref
        or terminal.get("deep_chain_sha256") != deep_chain
        or head.get("current_segment_receipt_ref") != previous_segment_ref
        or head.get("current_deep_segment_receipt_ref") != previous_deep_ref
        or head.get("deep_chain_sha256") != deep_chain
        or terminal.get("resource_accounting")
        != segment_receipts[-1]["cumulative_resource_accounting"]
    ):
        _fail("workspace terminal/HEAD 未闭合到完整 segment/deep 链")
    return tuple(segment_receipts), tuple(payloads), tuple(deep_receipts)


def _validate_route_raw_population(
    route_rows: Sequence[Mapping[str, Any]],
    raw_rows: Sequence[Mapping[str, Any]],
) -> Tuple[Tuple[Mapping[str, Any], ...], Tuple[Mapping[str, Any], ...]]:
    route_by_id = {}
    for index, raw in enumerate(route_rows):
        row = dict(raw)
        if row.get("schema_version") != ROUTE_EVENT_SHARD_SCHEMA_VERSION:
            _fail(f"RouteEvent {index} schema 不支持")
        route_id = row.get("route_event_id")
        if not isinstance(route_id, str) or route_id in route_by_id:
            _fail("RouteEvent ID 非法或重复")
        event = _finalizer._route_event_from_row(row)
        expected_raw_id = raw_record_ref_id_v1(
            event.file_sha256, event.record_ordinal, event.element_ordinal
        )
        if (
            row.get("raw_record_ref_id") != expected_raw_id
            or row.get("raw_record_ref_ids") != [expected_raw_id]
            or row.get("artifact_id") != artifact_id_v1(event.file_sha256)
        ):
            _fail("RouteEvent 稳定身份未闭合到 artifact/raw ref")
        route_by_id[route_id] = row

    raw_by_route = {}
    raw_ids = set()
    for index, raw in enumerate(raw_rows):
        row = dict(raw)
        route_id = row.get("route_event_id")
        raw_id = row.get("raw_record_ref_id")
        if (
            row.get("schema_version") != RAW_RECORD_REF_SHARD_SCHEMA_VERSION
            or not isinstance(route_id, str)
            or route_id in raw_by_route
            or not isinstance(raw_id, str)
            or raw_id in raw_ids
            or route_id not in route_by_id
        ):
            _fail(f"raw record ref {index} 身份非法或重复")
        route = route_by_id[route_id]
        expected_raw_id = raw_record_ref_id_v1(
            str(row.get("file_sha256")),
            int(row.get("record_ordinal")),
            int(row.get("element_ordinal")),
        )
        if (
            raw_id != expected_raw_id
            or route.get("raw_record_ref_id") != raw_id
            or any(
                row.get(field) != route.get(field)
                for field in (
                    "artifact_id",
                    "file_sha256",
                    "artifact_slot_utc",
                    "record_ordinal",
                    "element_ordinal",
                )
            )
            or row.get("artifact_id") != artifact_id_v1(str(row.get("file_sha256")))
            or row.get("record_hash") != row.get("raw_record_sha256")
            or row.get("verification_status") != "verified"
        ):
            _fail("RouteEvent→raw record ref→artifact 身份/坐标不闭合")
        _finalizer._sha(row.get("raw_record_sha256"), "raw_record_sha256")
        raw_by_route[route_id] = row
        raw_ids.add(raw_id)
    if set(route_by_id) != set(raw_by_route):
        _fail("存在未 1:1 闭合到 raw record ref 的 RouteEvent")
    return (
        tuple(route_by_id[key] for key in route_by_id),
        tuple(raw_by_route[key] for key in route_by_id),
    )


def _build_ancestry(
    *,
    workspace_root: Path,
    journal_root: Path,
    terminal: Mapping[str, Any],
    seed_refs: Mapping[str, Mapping[str, Any]],
) -> Tuple[Tuple[Mapping[str, Any], ...], Tuple[Mapping[str, Any], ...]]:
    """建立与实际 copy source 一一对应的自包含 inventory。"""

    sources: dict[str, Mapping[str, Any]] = {}

    def add(
        source_path: Path,
        output_relative: str,
        *,
        sha256: str,
        size_bytes: int,
        record_count: int,
        kind: str,
        content_already_verified: bool,
    ) -> None:
        output = _safe_relative(output_relative, "ancestry output path")
        digest = _finalizer._sha(sha256, "ancestry.sha256")
        size = _finalizer._nonnegative(size_bytes, "ancestry.size_bytes")
        count = _finalizer._nonnegative(record_count, "ancestry.record_count")
        metadata = _regular_metadata(source_path, expected_size=size)
        if not content_already_verified:
            raw = _finalizer._read_stable_regular(
                source_path, maximum_bytes=32 * 1024 * 1024
            )
            if hashlib.sha256(raw).hexdigest() != digest:
                _fail(f"ancestry 小文件 SHA256 不闭合：{source_path}")
        row = {
            "source_path": str(source_path.absolute()),
            "output_relative_path": output,
            "sha256": digest,
            "size_bytes": metadata.st_size,
            "record_count": count,
            "kind": kind,
        }
        previous = sources.get(output)
        if previous is not None and previous != row:
            _fail(f"ancestry output path 冲突：{output}")
        sources[output] = row

    for relative, kind in (
        ("GENESIS", "finalization-workspace-genesis"),
        ("TERMINAL", "finalization-workspace-terminal"),
        ("DEEP-VERIFICATION", "finalization-workspace-deep-verification"),
    ):
        ref = _workspace._file_ref(workspace_root, workspace_root / relative)
        add(
            workspace_root / relative,
            relative,
            sha256=str(ref["sha256"]),
            size_bytes=int(ref["size_bytes"]),
            record_count=1,
            kind=kind,
            content_already_verified=True,
        )
    for field, kind in (
        ("segment_receipt_refs", "finalization-segment-receipt"),
        ("segment_payload_refs", "finalization-segment-payload"),
        ("deep_segment_receipt_refs", "finalization-deep-segment-receipt"),
    ):
        for ref in terminal[field]:
            relative = _safe_relative(ref["path"], f"TERMINAL.{field}.path")
            add(
                workspace_root.joinpath(*PurePosixPath(relative).parts),
                relative,
                sha256=str(ref["sha256"]),
                size_bytes=int(ref["size_bytes"]),
                record_count=1,
                kind=kind,
                content_already_verified=True,
            )

    for kind, ref in sorted(seed_refs.items()):
        relative = _safe_relative(ref["path"], f"seed {kind}.path")
        add(
            journal_root.joinpath(*PurePosixPath(relative).parts),
            "seed/" + relative,
            sha256=str(ref["sha256"]),
            size_bytes=int(ref["size_bytes"]),
            record_count=int(ref["record_count"]),
            kind="journal-" + kind,
            content_already_verified=True,
        )

    ledger_root = journal_root / "raw-ledger"
    for path in sorted(ledger_root.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            _fail("raw-ledger 不得包含符号链接")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            _fail("raw-ledger 只能包含普通文件")
        relative = path.relative_to(journal_root).as_posix()
        raw = _finalizer._read_stable_regular(
            path, maximum_bytes=32 * 1024 * 1024
        )
        add(
            path,
            relative,
            sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
            record_count=1,
            kind=(
                sources.get(relative, {}).get("kind")
                or "journal-raw-ledger"
            ),
            content_already_verified=True,
        )

    copy_sources = tuple(sources[key] for key in sorted(sources))
    inventory = tuple(
        {
            "path": row["output_relative_path"],
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
            "record_count": row["record_count"],
            "kind": row["kind"],
        }
        for row in copy_sources
    )
    if any(
        "record_observations" in str(row["source_path"])
        or row["kind"] == "journal-record_observations"
        for row in copy_sources
    ):
        _fail("自包含 ancestry 意外包含原 record_observations")
    return inventory, copy_sources


def build_segment_product_inputs(
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
) -> SegmentProductInputs:
    """从 sealed segments 构造完整 ``_FinalizationInputs`` 和自包含 ancestry。

    调用成功即证明：所有 UPDATE 业务输入来自 sealed segment；原
    ``record_observations`` 的打开/哈希/解压次数为零。
    """

    root = Path(workspace_root).absolute()
    try:
        normalized_profile = validate_complete_selection_against_profile(
            input_selection, profile
        )
        source_fact = load_frozen_incident_fact(source_fact_snapshot)
        compatible_mapping = mapping_view_from_frozen_snapshot(
            compatible_mapping_snapshot
        )
        revised_mapping = mapping_view_from_revised_snapshot(
            revised_mapping_snapshot, compatible_mapping_snapshot
        )
    except (KeyError, TypeError, ValueError) as error:
        raise FullWindowSegmentProductError(
            "Profile、source fact 或 mapping 冻结输入非法"
        ) from error
    if set(bindings) != {
        "profile_sha256",
        "input_selection_sha256",
        "code_sha256",
        "mapping_sha256",
    }:
        _fail("bindings 必须精确包含 journal 四项 SHA256")
    normalized_bindings = {
        key: _finalizer._sha(value, f"bindings.{key}")
        for key, value in sorted(bindings.items())
    }
    if profile_sha256(normalized_profile) != normalized_bindings["profile_sha256"]:
        _fail("Profile 与 journal binding 不一致")
    selection_sha = _finalizer._selection_fingerprint(input_selection)
    if selection_sha != normalized_bindings["input_selection_sha256"]:
        _fail("input selection 与 journal binding 不一致")
    compatible_mapping_sha = mapping_snapshot_sha256(
        compatible_mapping_snapshot
    )
    revised_mapping_sha = mapping_snapshot_sha256(revised_mapping_snapshot)
    mapping_sha = mapping_bundle_sha256(
        compatible_mapping_snapshot, revised_mapping_snapshot
    )
    if mapping_sha != normalized_bindings["mapping_sha256"]:
        _fail("mapping bundle 与 journal binding 不一致")
    normalized_code = _finalizer._validate_code_identity(
        code_identity, normalized_bindings["code_sha256"]
    )
    normalized_policy = _finalizer._validate_incident_policy(
        incident_policy, source_fact
    )
    if (
        claim_inventory.get("study_id") != normalized_profile["study_id"]
        or claim_inventory.get("incident_ref")
        != source_fact.incident.get("detail_reference")
        or not isinstance(claim_inventory.get("claims"), list)
        or not claim_inventory["claims"]
    ):
        _fail("claim inventory 与 Study/Incident 不一致")

    # receipt-only workspace 核验只读取 sealed workspace 文件。
    workspace_verification = _workspace.verify_finalization_workspace(root)
    genesis = _workspace._load_genesis(root)
    head = _workspace._load_head(root)
    terminal = _workspace._verify_fingerprinted(
        _workspace._load_json(root / "TERMINAL", "TERMINAL"),
        _workspace.WORKSPACE_TERMINAL_SCHEMA,
        "TERMINAL",
    )
    deep = _workspace._verify_fingerprinted(
        _workspace._load_json(root / "DEEP-VERIFICATION", "DEEP-VERIFICATION"),
        _workspace.WORKSPACE_DEEP_VERIFICATION_SCHEMA,
        "DEEP-VERIFICATION",
    )
    journal_root = Path(str(genesis.get("journal_root"))).absolute()
    roles = input_selection.get("roles")
    updates = roles.get("analysis_updates") if isinstance(roles, Mapping) else None
    if not isinstance(updates, list) or not updates:
        _fail("selection 缺少 analysis_updates")
    total = len(updates)
    if (
        genesis.get("bindings") != normalized_bindings
        or terminal.get("bindings") != normalized_bindings
        or deep.get("bindings") != normalized_bindings
        or genesis.get("code_identity_sha256")
        != normalized_code["identity_sha256"]
        or terminal.get("code_identity_sha256")
        != normalized_code["identity_sha256"]
        or deep.get("code_identity_sha256")
        != normalized_code["identity_sha256"]
        or genesis.get("study_id") != normalized_profile["study_id"]
        or str(genesis.get("incident_ref")).replace("+", " ")
        != str(source_fact.incident.get("detail_reference")).replace("+", " ")
        or genesis.get("total_slots") != total
        or terminal.get("total_slots") != total
        or terminal.get("completed_slots") != total
        or terminal.get("sealed") is not True
        or terminal.get("database_access") != "none"
        or deep.get("database_write_operations") != 0
    ):
        _fail("workspace genesis/terminal/deep 与冻结业务身份不一致")
    if (journal_root / "raw-ledger/ACTIVE").exists() or (
        journal_root / "raw-ledger/ACTIVE"
    ).is_symlink():
        _fail("journal raw-ledger 仍有 ACTIVE attempt")

    (
        journal_receipts,
        complete_attempt_ids,
        max_worker_seconds,
        peak_temporary_bytes,
        database_write_operations,
    ) = _load_journal_receipts_without_shards(
        journal_root=journal_root,
        receipt_refs=genesis["journal_receipt_refs"],
        bindings=normalized_bindings,
        expected_total=total,
    )
    if (
        genesis.get("journal_genesis_receipt_ref") != journal_receipts[0][0]
        or genesis.get("journal_terminal_receipt_ref") != journal_receipts[-1][0]
        or terminal.get("journal_terminal_receipt_ref") != journal_receipts[-1][0]
        or genesis.get("journal_terminal_shard_chain_sha256")
        != journal_receipts[-1][1].get("shard_chain_sha256")
        or terminal.get("journal_terminal_shard_chain_sha256")
        != journal_receipts[-1][1].get("shard_chain_sha256")
    ):
        _fail("workspace 未闭合到 journal genesis/terminal/shard chain")
    segment_receipts, payloads, _deep_receipts = _validate_workspace_segments(
        workspace_root=root,
        genesis=genesis,
        terminal=terminal,
        head=head,
        journal_receipts=journal_receipts,
    )

    genesis_shards = {
        str(ref["kind"]): dict(ref)
        for ref in journal_receipts[0][1]["shards"]
    }
    seed_bootstrap_rows = tuple(
        _finalizer._iter_shard_rows(
            journal_root, genesis_shards["seed_bootstrap_attestation"]
        )
    )
    seed_route_rows = tuple(
        _finalizer._iter_shard_rows(
            journal_root, genesis_shards["seed_route_events"]
        )
    )
    seed_raw_rows = tuple(
        _finalizer._iter_shard_rows(
            journal_root, genesis_shards["seed_raw_record_refs"]
        )
    )
    if len(seed_bootstrap_rows) != 1:
        _fail("journal genesis 缺少唯一 seed bootstrap attestation")

    route_rows = [dict(row) for row in seed_route_rows]
    raw_rows = [dict(row) for row in seed_raw_rows]
    parser_attestations = []
    compatible_slots = []
    revised_slots = []
    artifacts = []
    shard_bindings = [
        _finalizer._ShardBinding(0, None, dict(ref))
        for ref in journal_receipts[0][1]["shards"]
    ]
    control_summaries = []
    observation_summaries = []
    for sequence, (payload, segment_receipt) in enumerate(
        zip(payloads, segment_receipts), start=1
    ):
        journal_receipt = journal_receipts[sequence][1]
        artifact = dict(payload["artifact"])
        artifacts.append(artifact)
        selected = updates[sequence - 1]
        expected_artifact = {
            "artifact_id": selected.get("artifact_id"),
            "file_sha256": selected.get("file_sha256"),
            "size_bytes": selected.get("size_bytes"),
            "collector_id": selected.get("collector_id"),
            "slot_start_utc": selected.get("artifact_time_utc"),
        }
        if any(artifact.get(key) != value for key, value in expected_artifact.items()):
            _fail(f"segment artifact {sequence} 与 selection 不一致")
        refs = {str(ref["kind"]): dict(ref) for ref in journal_receipt["shards"]}
        shard_bindings.extend(
            _finalizer._ShardBinding(sequence, artifact, dict(ref))
            for ref in journal_receipt["shards"]
        )
        payload_routes = payload.get("route_event_rows")
        payload_raw = payload.get("raw_record_ref_rows")
        parser = payload.get("parser_attestation")
        country_slots = payload.get("country_slots")
        if (
            not isinstance(payload_routes, list)
            or not isinstance(payload_raw, list)
            or not isinstance(parser, Mapping)
            or not isinstance(country_slots, list)
            or len(country_slots) != 2
            or len(payload_routes) != refs["route_events"]["record_count"]
            or len(payload_raw) != refs["raw_record_refs"]["record_count"]
            or refs["parser_attestations"]["record_count"] != 1
            or refs["country_slots"]["record_count"] != 2
        ):
            _fail(f"segment payload {sequence} 业务人口与 journal refs 不闭合")
        slot_start = _finalizer._utc(
            artifact.get("slot_start_utc"), "segment artifact.slot_start_utc"
        )
        parsed_start = datetime.fromisoformat(slot_start[:-1] + "+00:00")
        parsed_end = parsed_start + timedelta(minutes=5)
        for kind, rows in (
            ("route_event", payload_routes),
            ("raw_record_ref", payload_raw),
        ):
            for row in rows:
                if (
                    not isinstance(row, Mapping)
                    or row.get("artifact_id") != artifact.get("artifact_id")
                    or row.get("file_sha256") != artifact.get("file_sha256")
                    or row.get("artifact_slot_utc") != slot_start
                ):
                    _fail(
                        f"segment {sequence} {kind} 未绑定当前 artifact/slot"
                    )
                if kind == "route_event":
                    event_time = _finalizer._utc_event(
                        row.get("event_time_utc"),
                        "segment RouteEvent.event_time_utc",
                    )
                    observed = datetime.fromisoformat(
                        event_time[:-1] + "+00:00"
                    )
                    if not parsed_start <= observed < parsed_end:
                        _fail(
                            f"segment {sequence} RouteEvent 越出当前半开槽"
                        )
        route_rows.extend(dict(row) for row in payload_routes)
        raw_rows.extend(dict(row) for row in payload_raw)
        parser_attestations.append(dict(parser))
        by_view = {
            row.get("mapping_view"): dict(row)
            for row in country_slots
            if isinstance(row, Mapping)
        }
        if set(by_view) != {"compatible", "revised"}:
            _fail(f"segment {sequence} country slots 缺少双视图")
        payload_source_ref = {
            "path": str(segment_receipt["segment_payload_ref"]["path"]),
            "sha256": segment_receipt["segment_payload_ref"]["sha256"],
            "size_bytes": segment_receipt["segment_payload_ref"]["size_bytes"],
            "record_count": 1,
            "kind": "finalization-segment-payload",
        }
        for view, mapping in (
            ("compatible", compatible_mapping),
            ("revised", revised_mapping),
        ):
            row = by_view[view]
            if (
                row.get("schema_version") != _finalizer.COUNTRY_SLOT_SCHEMA_VERSION
                or row.get("slot_start_utc") != artifact.get("slot_start_utc")
                or row.get("slot_end_exclusive_utc")
                != artifact.get("slot_end_exclusive_utc")
                or row.get("mapping_view") != view
                or row.get("main_curve") is not (view == "compatible")
                or row.get("mapping_source_sha256") != mapping.source_sha256
                or row.get("mapping_source_ref") != mapping.source_ref
            ):
                _fail(f"segment {sequence} {view} country slot 身份非法")
            row["_source_shard_ref"] = payload_source_ref
            row["_source_package_ref_id"] = payload_source_ref["path"]
            row["_source_receipt_sequence"] = sequence
        if by_view["compatible"].get("update_counts") != by_view["revised"].get(
            "update_counts"
        ):
            _fail(f"segment {sequence} 双视图 UPDATE 计数不一致")
        compatible_slots.append(by_view["compatible"])
        revised_slots.append(by_view["revised"])
        control_summaries.append(
            _validate_summary(
                payload.get("control_record_summary"),
                schema=_finalizer.CONTROL_RECORD_SHARD_SEMANTIC_SCHEMA,
                sequence=sequence,
                ref=refs["control_records"],
                field=f"segment {sequence}.control summary",
            )
        )
        observation_summaries.append(
            _validate_summary(
                payload.get("record_observation_summary"),
                schema=_finalizer.RECORD_OBSERVATION_SHARD_SEMANTIC_SCHEMA,
                sequence=sequence,
                ref=refs["record_observations"],
                field=f"segment {sequence}.observation summary",
            )
        )

    route_rows_validated, raw_rows_validated = _validate_route_raw_population(
        route_rows, raw_rows
    )
    _finalizer._validate_parser_attestations_against_artifacts(
        parser_attestations, artifacts
    )
    ledger = _finalizer._raw_ledger_accounting(
        journal_root, receipt_complete_attempt_ids=complete_attempt_ids
    )
    execution = {
        "database_write_operations": database_write_operations,
        "new_raw_bytes_read": ledger["observed_compressed_bytes_sum"],
        "new_raw_bytes_read_lower_bound": ledger[
            "observed_compressed_bytes_lower_bound_sum"
        ],
        "new_raw_bytes_read_upper_bound": ledger[
            "observed_compressed_bytes_upper_bound_sum"
        ],
        "new_raw_bytes_read_state": ledger["observed_compressed_bytes_state"],
        "peak_temporary_bytes": peak_temporary_bytes,
        "max_worker_seconds": max_worker_seconds,
        **ledger,
        "raw_accounting_semantics": (
            "reservation_is_nonrefundable_gate_observed_bytes_are_exact_or_explicit_interval"
        ),
        "finalization_reads_real_mrt": False,
    }
    if (
        execution["database_write_operations"] != 0
        or terminal["resource_accounting"]["database_write_operations"] != 0
        or deep["database_write_operations"] != 0
        or deep.get("maximum_slot_seconds", 600) >= 600
        or deep.get("maximum_temporary_bytes", 5_000_000_000)
        >= 5_000_000_000
        or execution["max_worker_seconds"] >= 600
        or execution["peak_temporary_bytes"] >= 5_000_000_000
        or execution["cumulative_reserved_raw_bytes_upper_bound"]
        >= 50_000_000_000
    ):
        _fail("execution/raw/finalization 资源门未通过")
    frozen_head = {
        "schema_version": _journal.JOURNAL_SCHEMA_VERSION,
        "run_id": journal_receipts[-1][1]["run_id"],
        "bindings": dict(normalized_bindings),
        "completed_artifact_count": total,
        "total_artifacts": total,
        "terminal_receipt_ref": dict(journal_receipts[-1][0]),
        "genesis_receipt_ref": dict(journal_receipts[0][0]),
        "genesis_seed_shards": [
            dict(row) for row in journal_receipts[0][1]["shards"]
        ],
        "shard_chain_sha256": journal_receipts[-1][1][
            "shard_chain_sha256"
        ],
        "verified_receipt_count": total + 1,
        "cumulative_reserved_raw_bytes": execution[
            "cumulative_reserved_raw_bytes_upper_bound"
        ],
        "scratch_is_evidence": False,
    }
    terminal_journal_receipt = journal_receipts[-1][1]
    terminal_segment_receipt = segment_receipts[-1]
    terminal_scratch = _journal._fingerprinted(
        _journal.SCRATCH_SCHEMA_VERSION,
        {
            "run_id": frozen_head["run_id"],
            "bindings": dict(normalized_bindings),
            "sequence": total,
            "next_artifact_index": total,
            "total_artifacts": total,
            "active_scratch_slot": terminal_journal_receipt["state_ref"]["slot"],
            "compact_state": dict(
                terminal_segment_receipt["next_compact_state"]
            ),
            "runtime_estimator": dict(
                terminal_segment_receipt["next_runtime_estimator"]
            ),
            "shard_chain_sha256": frozen_head["shard_chain_sha256"],
        },
    )
    if (
        _journal.scratch_payload_sha256(terminal_scratch)
        != terminal_journal_receipt["state_ref"]["sha256"]
    ):
        _fail("sealed segment 终态无法闭合 terminal journal state_ref")

    artifact_by_id = {str(row["artifact_id"]): dict(row) for row in artifacts}
    for raw in raw_rows_validated:
        artifact_id = str(raw["artifact_id"])
        file_sha = _finalizer._sha(raw.get("file_sha256"), "raw.file_sha256")
        if artifact_id != artifact_id_v1(file_sha):
            _fail("raw artifact_id 与 file SHA256 不一致")
        existing = artifact_by_id.get(artifact_id)
        if existing is None:
            artifact_by_id[artifact_id] = {
                "artifact_id": artifact_id,
                "file_sha256": file_sha,
                "collector_id": "rrc25",
                "artifact_slot_utc": raw.get("artifact_slot_utc"),
                "artifact_role": "seed_state_evidence",
            }
        elif existing.get("file_sha256") != file_sha:
            _fail("同一 artifact_id 对应冲突 SHA256")

    _finalizer._validate_seed_bootstrap_attestation(
        attestation=seed_bootstrap_rows[0],
        seed_route_rows=seed_route_rows,
        seed_raw_rows=seed_raw_rows,
        execution=execution,
        bindings=normalized_bindings,
        code_identity=normalized_code,
        selection=input_selection,
        compatible_mapping=compatible_mapping,
        revised_mapping=revised_mapping,
    )
    independent_derivation = {
        "schema_version": (
            "rrc25-full-window-independent-derivation-verification/v1"
        ),
        "verified_artifact_count": total,
        "input_basis": (
            "seed_compact_plus_route_events_plus_complete_record_observations"
        ),
        "compatible_and_revised_country_slots_recomputed": True,
        "every_receipt_state_ref_recomputed": True,
        "runtime_bootstrap_bytes_per_second": float(
            genesis["initial_runtime_estimator"][
                "bootstrap_bytes_per_second"
            ]
        ),
        "initial_compact_state_semantic_sha256": (
            _finalizer._semantic_value_sha256(
                "rrc25_full_window_initial_compact_state_v1",
                seed_bootstrap_rows[0]["initial_compact_state"],
            )
        ),
        "terminal_compact_state_semantic_sha256": (
            _finalizer._semantic_value_sha256(
                "rrc25_full_window_terminal_compact_state_v1",
                terminal_segment_receipt["next_compact_state"],
            )
        ),
    }
    journal_data = _finalizer._JournalData(
        frozen_head=frozen_head,
        terminal_scratch=terminal_scratch,
        shard_bindings=tuple(shard_bindings),
        compatible_slots=tuple(compatible_slots),
        revised_slots=tuple(revised_slots),
        route_rows=route_rows_validated,
        raw_rows=raw_rows_validated,
        seed_route_rows=tuple(seed_route_rows),
        seed_raw_rows=tuple(seed_raw_rows),
        control_record_count=sum(
            int(row["record_count"]) for row in control_summaries
        ),
        control_record_semantic_sha256=(
            _finalizer._control_record_stream_sha256(control_summaries)
        ),
        record_observation_count=sum(
            int(row["record_count"]) for row in observation_summaries
        ),
        record_observation_semantic_sha256=(
            _finalizer._record_observation_stream_sha256(
                observation_summaries
            )
        ),
        parser_attestations=tuple(parser_attestations),
        seed_bootstrap_attestation=dict(seed_bootstrap_rows[0]),
        artifacts=tuple(
            artifact_by_id[key] for key in sorted(artifact_by_id)
        ),
        execution=execution,
    )
    frozen_hashes = {
        "profile": normalized_bindings["profile_sha256"],
        "source-fact": _finalizer._canonical_hash(
            dict(source_fact_snapshot)
        ),
        "incident-policy": _finalizer._canonical_hash(normalized_policy),
        "compatible-mapping": compatible_mapping_sha,
        "revised-mapping": revised_mapping_sha,
        "mapping-bundle": mapping_sha,
        "code-identity": normalized_bindings["code_sha256"],
        "input-selection": selection_sha,
        "claim-inventory": _finalizer._canonical_hash(
            dict(claim_inventory)
        ),
        "journal-head": _finalizer._canonical_hash(frozen_head),
    }
    inputs = _finalizer._FinalizationInputs(
        journal_root=journal_root,
        profile=normalized_profile,
        source_fact_snapshot=dict(source_fact_snapshot),
        source_fact=source_fact,
        incident_policy=normalized_policy,
        compatible_mapping_snapshot=dict(compatible_mapping_snapshot),
        revised_mapping_snapshot=dict(revised_mapping_snapshot),
        compatible_mapping=compatible_mapping,
        revised_mapping=revised_mapping,
        code_identity=normalized_code,
        input_selection=dict(input_selection),
        claim_inventory=dict(claim_inventory),
        bindings=normalized_bindings,
        journal=journal_data,
        independent_derivation_verification=independent_derivation,
        frozen_hashes=frozen_hashes,
    )
    inventory, copy_sources = _build_ancestry(
        workspace_root=root,
        journal_root=journal_root,
        terminal=terminal,
        seed_refs=genesis_shards,
    )
    segment_payload_paths = {
        str(ref["path"])
        for ref in terminal["segment_payload_refs"]
    }
    if not {
        str(row["_source_shard_ref"]["path"])
        for row in compatible_slots + revised_slots
    } <= segment_payload_paths:
        _fail("country slot ancestry 未指向将被封存的 segment payload")
    verification = {
        "schema_version": "rrc25-full-window-segment-product-verification/v1",
        "verified": True,
        "workspace_terminal_fingerprint_sha256": terminal[
            "fingerprint_sha256"
        ],
        "workspace_deep_fingerprint_sha256": deep[
            "fingerprint_sha256"
        ],
        "verified_segment_count": total,
        "verified_journal_receipt_count": total + 1,
        "record_observation_reread_count": 0,
        "real_mrt_raw_bytes_read": 0,
        "database_write_operations": 0,
        "self_contained_ancestry_file_count": len(inventory),
        "self_contained_ancestry_semantic_sha256": (
            _finalizer._canonical_hash(inventory)
        ),
        "workspace_receipt_only_verification": dict(workspace_verification),
    }
    return SegmentProductInputs(
        inputs=inputs,
        journal_ancestry_inventory=inventory,
        copy_sources=copy_sources,
        verification=verification,
    )


def derive_business_outputs_from_segment_product(
    product: SegmentProductInputs,
) -> _finalizer.FullWindowBusinessOutputs:
    """从适配结果纯派生完整 object/sequence/report。"""

    if not isinstance(product, SegmentProductInputs):
        _fail("product 必须是 SegmentProductInputs")
    return _finalizer.derive_full_window_business_outputs(
        product.inputs,
        journal_ancestry_inventory=product.journal_ancestry_inventory,
    )


__all__ = (
    "FullWindowSegmentProductError",
    "SegmentProductInputs",
    "build_segment_product_inputs",
    "derive_business_outputs_from_segment_product",
)
