#!/usr/bin/env python3
"""为 DB-first 缺口执行最小、文件型、定向 UPDATE 原始证据提取。

本入口只消费 ``iran-db-first.json`` 已批准的 4 个 ASN-prefix 配对和 13 个
五分钟 UPDATE 槽。``plan`` 只核验元数据并输出确定性计划；``run`` 对每个
指定 UPDATE artifact 单遍扫描一次，只按 prefix allowlist 保留 route
element。WITHDRAW 没有 AS_PATH，因此只按 prefix 保留；ANNOUNCE 额外记录
origin 与目标 ASN 的匹配状态，但匹配结果不参与过滤。

本入口不连接数据库、不读取 RIB、不 seed、不回放路由状态，也不生成因果或
恢复结论。所有输出都明确标记为 ``message_observation_only``，并以新的
create-only 目录发布。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.data_pipeline.route_event import canonical_json  # noqa: E402
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (  # noqa: E402
    UNKNOWN,
    derive_origin_asns,
)
from backend.data_pipeline.research.rrc25_country_outage.update_adapter import (  # noqa: E402
    AdaptedUpdateRecord,
    RawRecordEvidence,
    iter_adapted_update_records,
)
from dev.data_quality.rrc25_iran_bounded_pilot import (  # noqa: E402
    _raw_record_ref_id,
)
from dev.data_quality.rrc25_iran_execution_prep import (  # noqa: E402
    ExecutionPrepError,
    _assert_disjoint,
    _assert_mutation_target_allowed,
    _hash_regular,
    _load_prepared,
    _native_factory,
    _safe_directory,
    _selection_updates,
    _validate_generated_parser_attestation,
    load_json_metadata,
)


UTC = timezone.utc
SCHEMA_VERSION = "rrc25-iran-targeted-raw/v1"
PLAN_SCHEMA_VERSION = "rrc25-iran-targeted-raw-plan/v1"
PARSER_STATS_SCHEMA_VERSION = "rrc25-iran-targeted-parser-stats/v1"
MANIFEST_SCHEMA_VERSION = "rrc25-iran-targeted-raw-manifest/v1"
EVIDENCE_SCOPE = "message_observation_only"
EXPECTED_DB_FIRST_SCHEMA = "rrc25-iran-db-first/v2"
EXPECTED_PAIR_COUNT = 4
EXPECTED_INCIDENT_REF = "country_outage/2026-02-27 09:12:32/IR/1/r"
EXPECTED_WINDOW = {
    "start_utc": "2026-02-27T16:00:00Z",
    "end_exclusive_utc": "2026-03-06T08:40:00Z",
    "semantics": "half_open",
    "granularity_seconds": 300,
    "expected_slot_count": 1928,
}
EXPECTED_UPDATE_SLOTS = (
    "2026-02-27T22:30:00Z",
    "2026-02-27T22:35:00Z",
    "2026-02-27T22:40:00Z",
    "2026-02-28T10:35:00Z",
    "2026-02-28T10:40:00Z",
    "2026-02-28T10:45:00Z",
    "2026-02-28T10:50:00Z",
    "2026-02-28T10:55:00Z",
    "2026-02-28T14:20:00Z",
    "2026-02-28T14:25:00Z",
    "2026-02-28T14:30:00Z",
    "2026-02-28T14:35:00Z",
    "2026-02-28T14:40:00Z",
)
DB_FIRST_FINGERPRINT_FIELDS = (
    "schema_version",
    "scope",
    "source_release",
    "fact",
    "country_series",
    "event_fact_reconciliation",
    "fact_bucket_analysis",
    "sparse_feature_auxiliary",
    "phase_analysis",
    "metric_findings",
    "recovery_candidate",
    "gap_matrix",
    "minimal_raw_request",
    "assessment",
)
MAX_UPDATE_SLOT_COUNT = 13
MAX_RETAINED_ROUTE_EVENTS = 250_000
MAX_SELECTED_COMPRESSED_BYTES = 512 * 1024 * 1024
SOFT_RUNTIME_SECONDS = 540.0
DEFAULT_MAX_FRAME_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_SPOOL_BYTES = 4_000_000_000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COLLECTOR_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class TargetedRawError(ValueError):
    """定向 raw 请求、执行边界或发布结果不闭合。"""


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise TargetedRawError(f"{field} 必须是秒级 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise TargetedRawError(f"{field} 不是合法 UTC 时间") from error
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.microsecond
        or parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value
    ):
        raise TargetedRawError(f"{field} 必须是规范秒级 UTC Z 时间")
    return parsed.astimezone(UTC)


def _positive_asn(value: Any, field: str) -> str:
    text = str(value)
    if not text.isdigit() or int(text) <= 0 or str(int(text)) != text:
        raise TargetedRawError(f"{field} 必须是规范正整数 ASN 字符串")
    return text


def _canonical_prefix(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TargetedRawError(f"{field} 必须是前缀字符串")
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as error:
        raise TargetedRawError(f"{field} 不是规范网络前缀") from error
    canonical = network.with_prefixlen
    if value != canonical:
        raise TargetedRawError(f"{field} 必须是规范网络前缀")
    return canonical


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        rows = [_jsonable(item) for item in value]
        return sorted(rows, key=canonical_json)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TargetedRawError(f"不能安全序列化类型：{type(value).__name__}")


def _json_bytes(value: Any) -> bytes:
    return (canonical_json(_jsonable(value)) + "\n").encode("utf-8")


def _gzip_jsonl(rows: Iterable[Mapping[str, Any]]) -> bytes:
    body = b"".join(_json_bytes(row) for row in rows)
    return gzip.compress(body, compresslevel=9, mtime=0)


def _file_ref(path: Path) -> dict[str, Any]:
    digest, size = _hash_regular(path, maximum_bytes=512 * 1024 * 1024)
    return {"path": path.name, "sha256": digest, "size_bytes": size}


def _load_db_first(path_value: str | Path) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    path = Path(path_value).expanduser().absolute()
    digest, size = _hash_regular(path, maximum_bytes=256 * 1024 * 1024)
    payload = load_json_metadata(path, maximum_bytes=256 * 1024 * 1024)
    if not isinstance(payload, Mapping):
        raise TargetedRawError("DB-first JSON 顶层必须是对象")
    return payload, {
        "path": path.name,
        "sha256": digest,
        "size_bytes": size,
    }


def _request_pairs(request: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    rows = request.get("representative_entities")
    if not isinstance(rows, list) or len(rows) != EXPECTED_PAIR_COUNT:
        raise TargetedRawError("minimal_raw_request 必须精确包含 4 个代表实体")
    pairs: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise TargetedRawError(f"representative_entities[{index}] 必须是对象")
        if row.get("selection_state") != "ready":
            raise TargetedRawError("4 个代表实体必须全部处于 ready")
        asn = _positive_asn(row.get("asn"), f"representative_entities[{index}].asn")
        prefix = _canonical_prefix(
            row.get("selected_prefix"),
            f"representative_entities[{index}].selected_prefix",
        )
        family = row.get("preferred_ip_family")
        if family not in {4, 6} or ipaddress.ip_network(prefix).version != family:
            raise TargetedRawError("代表实体 preferred_ip_family 与 prefix 不一致")
        identity = (asn, prefix)
        if identity in identities:
            raise TargetedRawError("代表 ASN-prefix 配对重复")
        identities.add(identity)
        pairs.append(
            {
                "pair_index": index,
                "asn": asn,
                "prefix": prefix,
                "ip_family": family,
                "selection_reason_zh": row.get("selection_reason_zh"),
                "database_selection_state": "ready",
            }
        )
    if request.get("representative_asns") != [row["asn"] for row in pairs]:
        raise TargetedRawError("representative_asns 与 4 个代表实体不一致")
    if request.get("representative_prefixes") != [
        row["prefix"] for row in pairs
    ]:
        raise TargetedRawError("representative_prefixes 与 4 个代表实体不一致")
    return tuple(pairs)


def _request_slots(request: Mapping[str, Any]) -> tuple[str, ...]:
    scope = request.get("scope_limit")
    if (
        not isinstance(scope, Mapping)
        or scope.get("maximum_update_slot_count") != MAX_UPDATE_SLOT_COUNT
        or scope.get("full_window_replay") is not False
        or scope.get("all_asn_population") is not False
        or scope.get("only_key_slots_and_representative_entities") is not True
        or scope.get("initial_rib_read_requested") is not False
    ):
        raise TargetedRawError("minimal_raw_request scope_limit 未固定为定向 13 槽")
    rows = request.get("update_slots")
    if not isinstance(rows, list) or len(rows) != MAX_UPDATE_SLOT_COUNT:
        raise TargetedRawError("minimal_raw_request 必须精确包含 13 个 UPDATE 槽")
    slots: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise TargetedRawError(f"update_slots[{index}] 必须是对象")
        value = row.get("utc")
        parsed = _utc(value, f"update_slots[{index}].utc")
        if parsed.second or parsed.minute % 5:
            raise TargetedRawError("UPDATE 槽必须按五分钟对齐")
        slots.append(value)
    if len(set(slots)) != len(slots) or slots != sorted(slots):
        raise TargetedRawError("13 个 UPDATE 槽必须唯一且严格递增")
    if tuple(slots) != EXPECTED_UPDATE_SLOTS:
        raise TargetedRawError("13 个 UPDATE 槽偏离固定伊朗定向证据窗口")

    windows = request.get("evidence_windows")
    if not isinstance(windows, list) or not windows:
        raise TargetedRawError("minimal_raw_request 缺少 evidence_windows")
    window_slots: list[str] = []
    for index, row in enumerate(windows):
        if not isinstance(row, Mapping):
            raise TargetedRawError(f"evidence_windows[{index}] 必须是对象")
        start = _utc(row.get("start_utc"), f"evidence_windows[{index}].start_utc")
        end = _utc(
            row.get("end_exclusive_utc"),
            f"evidence_windows[{index}].end_exclusive_utc",
        )
        if (
            start >= end
            or start.second
            or end.second
            or start.minute % 5
            or end.minute % 5
            or int((end - start).total_seconds()) % 300
        ):
            raise TargetedRawError("evidence_window 必须是五分钟对齐半开窗口")
        expected = int((end - start).total_seconds()) // 300
        if row.get("update_slot_count") != expected:
            raise TargetedRawError("evidence_window update_slot_count 不一致")
        current = start
        while current < end:
            window_slots.append(current.strftime("%Y-%m-%dT%H:%M:%SZ"))
            current += timedelta(minutes=5)
    if window_slots != slots:
        raise TargetedRawError("13 个 UPDATE 槽必须精确等于请求窗口并集")

    critical = request.get("critical_slots")
    if not isinstance(critical, list) or not critical:
        raise TargetedRawError("minimal_raw_request 缺少 critical_slots")
    critical_values = []
    for index, row in enumerate(critical):
        if not isinstance(row, Mapping):
            raise TargetedRawError(f"critical_slots[{index}] 必须是对象")
        value = row.get("utc")
        _utc(value, f"critical_slots[{index}].utc")
        critical_values.append(value)
    if not set(critical_values).issubset(set(slots)):
        raise TargetedRawError("critical_slots 必须属于 13 个请求槽")
    return tuple(slots)


def _validate_db_first(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if payload.get("schema_version") != EXPECTED_DB_FIRST_SCHEMA:
        raise TargetedRawError("仅接受 rrc25-iran-db-first/v2")
    scope = payload.get("scope")
    execution = payload.get("execution")
    if (
        not isinstance(scope, Mapping)
        or scope.get("raw_read_performed") is not False
        or scope.get("database_write_performed") is not False
        or scope.get("backend_core_invoked") is not False
        or not isinstance(execution, Mapping)
        or execution.get("transaction_finalization") != "rollback_completed"
    ):
        raise TargetedRawError("DB-first 输入未证明只读且尚未执行 raw")
    scope_window = scope.get("window")
    if (
        scope.get("incident_ref") != EXPECTED_INCIDENT_REF
        or scope.get("country_code") != "IR"
        or scope.get("source") != "r"
        or scope.get("relationship_state") != "unresolved_not_causal"
        or not isinstance(scope_window, Mapping)
        or any(scope_window.get(key) != value for key, value in EXPECTED_WINDOW.items())
    ):
        raise TargetedRawError("DB-first 输入不属于固定伊朗事件与研究窗口")
    source_release = payload.get("source_release")
    if (
        not isinstance(source_release, Mapping)
        or not isinstance(source_release.get("release_id"), str)
        or not source_release["release_id"]
        or not isinstance(source_release.get("system_identifier"), str)
        or not source_release["system_identifier"]
        or any(
            not isinstance(source_release.get(field), str)
            or _SHA256_RE.fullmatch(source_release[field]) is None
            for field in (
                "state_sha256",
                "manifest_sha256",
                "database_manifest_sha256",
                "inventory_sha256",
            )
        )
    ):
        raise TargetedRawError("DB-first source_release 身份不闭合")
    if any(field not in payload for field in DB_FIRST_FINGERPRINT_FIELDS):
        raise TargetedRawError("DB-first 内容指纹缺少稳定研究字段")
    fingerprint_input = {
        "schema": "rrc25_iran_db_first_content_fingerprint/v2",
        "stable_research_content": {
            field: payload[field] for field in DB_FIRST_FINGERPRINT_FIELDS
        },
    }
    expected_fingerprint = hashlib.sha256(
        canonical_json(fingerprint_input).encode("utf-8")
    ).hexdigest()
    if payload.get("content_fingerprint_sha256") != expected_fingerprint:
        raise TargetedRawError("DB-first 内容指纹重算不一致")
    request = payload.get("minimal_raw_request")
    if not isinstance(request, Mapping):
        raise TargetedRawError("DB-first 输入缺少 minimal_raw_request")
    if (
        request.get("status") != "not_executed"
        or request.get("causal_claim_allowed") is not False
    ):
        raise TargetedRawError("minimal_raw_request 不是可执行的非因果请求")
    return request


def _validated_parser_contract(value: Any) -> dict[str, Any]:
    required = {
        "schema_version",
        "backend",
        "parser_name",
        "parser_version",
        "binary_sha256",
        "binary_execution_policy",
        "adapter_source_sha256",
        "semantic_fingerprint_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise TargetedRawError("冻结 native parser contract 字段不闭合")
    contract = dict(value)
    semantic = {
        key: contract[key]
        for key in sorted(required - {"semantic_fingerprint_sha256"})
    }
    expected = hashlib.sha256(canonical_json(semantic).encode("utf-8")).hexdigest()
    if (
        contract["schema_version"] != "rrc25-full-window-parser-attestation/v1"
        or contract["backend"] != "native"
        or contract["semantic_fingerprint_sha256"] != expected
        or any(
            not isinstance(contract[field], str) or not contract[field]
            for field in (
                "parser_name",
                "parser_version",
                "binary_execution_policy",
            )
        )
        or any(
            not isinstance(contract[field], str)
            or _SHA256_RE.fullmatch(contract[field]) is None
            for field in ("binary_sha256", "adapter_source_sha256")
        )
    ):
        raise TargetedRawError("冻结 native parser contract 内容或指纹非法")
    return contract


def build_plan(
    db_first: Mapping[str, Any],
    selection: Mapping[str, Any],
    *,
    native_parser_contract: Mapping[str, Any],
    targeted_executor_ref: Mapping[str, Any],
    db_first_ref: Mapping[str, Any] | None = None,
    preparation_ref: Mapping[str, Any] | None = None,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> dict[str, Any]:
    """纯函数：把 DB-first 请求与冻结 selection 收敛为精确 13 槽计划。"""

    if (
        isinstance(max_frame_bytes, bool)
        or not isinstance(max_frame_bytes, int)
        or not 12 <= max_frame_bytes <= DEFAULT_MAX_FRAME_BYTES
    ):
        raise TargetedRawError("max_frame_bytes 必须位于 [12, 64MiB]")
    request = _validate_db_first(db_first)
    pairs = _request_pairs(request)
    slots = _request_slots(request)
    parser_contract = _validated_parser_contract(native_parser_contract)
    try:
        all_updates = _selection_updates(selection)
    except (TypeError, ValueError) as error:
        raise TargetedRawError("prepared selection 未通过完整窗口核验") from error
    update_by_slot = {
        row.get("artifact_time_utc"): dict(row) for row in all_updates
    }
    if len(update_by_slot) != len(all_updates):
        raise TargetedRawError("prepared selection UPDATE 槽重复")
    artifacts = []
    selected_compressed_bytes = 0
    for slot in slots:
        artifact = update_by_slot.get(slot)
        if artifact is None:
            raise TargetedRawError(f"请求槽未在 prepared selection 中命中：{slot}")
        if (
            artifact.get("artifact_type") != "update"
            or artifact.get("compression") != "gz"
            or artifact.get("artifact_time_utc") != slot
            or not isinstance(artifact.get("collector_id"), str)
            or _COLLECTOR_RE.fullmatch(artifact["collector_id"]) is None
            or not isinstance(artifact.get("file_sha256"), str)
            or _SHA256_RE.fullmatch(artifact["file_sha256"]) is None
            or isinstance(artifact.get("size_bytes"), bool)
            or not isinstance(artifact.get("size_bytes"), int)
            or artifact["size_bytes"] <= 0
        ):
            raise TargetedRawError("命中的 artifact 不是规范 gzip UPDATE")
        artifacts.append(artifact)
        selected_compressed_bytes += artifact["size_bytes"]
    if selected_compressed_bytes > MAX_SELECTED_COMPRESSED_BYTES:
        raise TargetedRawError("13 槽压缩字节总量超过 512MiB 定向硬上限")
    window = selection.get("window")
    if not isinstance(window, Mapping):
        raise TargetedRawError("selection.window 缺失")
    selection_window = {
        "start_utc": window.get("start_utc"),
        "end_exclusive_utc": window.get("end_exclusive_utc"),
        "interval_semantics": window.get("interval_semantics"),
        "granularity_seconds": window.get("granularity_seconds"),
    }
    start = _utc(selection_window["start_utc"], "selection.window.start_utc")
    end = _utc(
        selection_window["end_exclusive_utc"],
        "selection.window.end_exclusive_utc",
    )
    if any(not start <= _utc(slot, "request slot") < end for slot in slots):
        raise TargetedRawError("请求槽越出 prepared selection 窗口")

    refs: dict[str, Any] = {}
    for name, ref in (
        ("db_first_json", db_first_ref),
        ("preparation_receipt", preparation_ref),
        ("targeted_executor_source", targeted_executor_ref),
    ):
        if ref is None:
            continue
        if (
            not isinstance(ref, Mapping)
            or not isinstance(ref.get("sha256"), str)
            or _SHA256_RE.fullmatch(ref["sha256"]) is None
            or not isinstance(ref.get("path"), str)
            or not ref["path"]
            or isinstance(ref.get("size_bytes"), bool)
            or not isinstance(ref.get("size_bytes"), int)
            or ref["size_bytes"] <= 0
        ):
            raise TargetedRawError(f"{name} ref 非法")
        refs[name] = dict(ref)

    semantic = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "evidence_scope": EVIDENCE_SCOPE,
        "execution_state": "planned_not_executed",
        "request_bindings": refs,
        "selection_id": selection.get("selection_id"),
        "selection_semantic_fingerprint_sha256": selection.get(
            "semantic_fingerprint_sha256"
        ),
        "selection_window": selection_window,
        "native_parser_contract": parser_contract,
        "requested_pairs": list(pairs),
        "requested_update_slots": list(slots),
        "selected_artifacts": artifacts,
        "limits": {
            "maximum_update_slot_count": MAX_UPDATE_SLOT_COUNT,
            "selected_update_slot_count": len(artifacts),
            "maximum_selected_compressed_bytes": MAX_SELECTED_COMPRESSED_BYTES,
            "selected_compressed_bytes": selected_compressed_bytes,
            "maximum_retained_route_events": MAX_RETAINED_ROUTE_EVENTS,
            "max_frame_bytes": max_frame_bytes,
            "soft_runtime_seconds": SOFT_RUNTIME_SECONDS,
        },
        "execution_policy": {
            "database_connections": 0,
            "database_writes": 0,
            "rib_files_opened": 0,
            "seed_performed": False,
            "state_replay_performed": False,
            "full_window_replay": False,
            "update_artifact_read_passes_per_file": 1,
            "retention_key": "canonical_prefix_allowlist",
            "withdraw_origin_policy": "prefix_only_no_as_path",
            "announce_origin_policy": "retain_by_prefix_then_annotate_target_match",
            "causal_claim_allowed": False,
        },
    }
    fingerprint = hashlib.sha256(canonical_json(semantic).encode("utf-8")).hexdigest()
    return {
        **semantic,
        "plan_id": "traw_v1_" + fingerprint[:32],
        "semantic_fingerprint_sha256": fingerprint,
    }


def _pairs_by_prefix(plan: Mapping[str, Any]) -> dict[str, tuple[Mapping[str, Any], ...]]:
    result: dict[str, list[Mapping[str, Any]]] = {}
    pairs = plan.get("requested_pairs")
    if not isinstance(pairs, list) or len(pairs) != EXPECTED_PAIR_COUNT:
        raise TargetedRawError("计划必须包含 4 个 ASN-prefix 配对")
    for pair in pairs:
        if not isinstance(pair, Mapping):
            raise TargetedRawError("计划 requested_pairs 含非对象")
        prefix = _canonical_prefix(pair.get("prefix"), "plan pair prefix")
        _positive_asn(pair.get("asn"), "plan pair asn")
        result.setdefault(prefix, []).append(dict(pair))
    return {key: tuple(value) for key, value in sorted(result.items())}


def _origin_annotation(
    event: Any, requested_pairs: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    if event.action == "withdraw":
        return {
            "origin_resolution": {
                "state": "not_applicable",
                "origins": [],
                "reason": "withdraw_has_no_as_path",
            },
            "requested_pair_matches": [
                {
                    "pair_index": pair["pair_index"],
                    "target_asn": pair["asn"],
                    "status": "not_applicable",
                    "reason": "withdraw_has_no_as_path_prefix_only_association",
                }
                for pair in requested_pairs
            ],
        }
    if event.action != "announce" or event.as_path is None:
        raise TargetedRawError("保留事件必须是合法 ANNOUNCE 或 WITHDRAW")
    resolution = derive_origin_asns(event.as_path)
    matches = []
    for pair in requested_pairs:
        target = int(pair["asn"])
        if resolution.state == UNKNOWN:
            status = "origin_unknown"
        elif target in resolution.origins:
            status = "matched_target_origin"
        else:
            status = "origin_does_not_match_target"
        matches.append(
            {
                "pair_index": pair["pair_index"],
                "target_asn": pair["asn"],
                "status": status,
            }
        )
    return {
        "origin_resolution": {
            "state": resolution.state,
            "origins": list(resolution.origins),
            "reason": resolution.reason,
        },
        "requested_pair_matches": matches,
    }


def _evidence_rows(
    event: Any,
    raw: RawRecordEvidence,
    pairs: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        event.artifact_id != raw.artifact_id
        or event.file_sha256 != raw.file_sha256
        or event.record_ordinal != raw.record_ordinal
        or event.artifact_slot_utc != raw.artifact_slot_utc
    ):
        raise TargetedRawError("RouteEvent 与 raw physical record 身份不一致")
    raw_id = _raw_record_ref_id(
        event.file_sha256, event.record_ordinal, event.element_ordinal
    )
    annotation = _origin_annotation(event, pairs)
    route_row = {
        **_jsonable(event),
        "raw_record_ref_id": raw_id,
        "requested_pairs": [dict(pair) for pair in pairs],
        **annotation,
        "evidence_scope": EVIDENCE_SCOPE,
        "lineage_status": "raw_traceable_message_observation",
        "causal_claim_allowed": False,
    }
    raw_row = {
        "raw_record_ref_id": raw_id,
        "route_event_id": event.route_event_id,
        "artifact_id": raw.artifact_id,
        "file_sha256": raw.file_sha256,
        "collector_id": raw.collector_id,
        "artifact_slot_utc": raw.artifact_slot_utc,
        "record_ordinal": raw.record_ordinal,
        "element_ordinal": event.element_ordinal,
        "record_offset": raw.record_offset,
        "record_length": raw.record_length,
        "raw_record_sha256": raw.raw_record_sha256,
        "event_time_utc": raw.event_time_utc,
        "vp_id": event.vp_id,
        "vp_asn": event.peer_asn,
        "prefix": event.prefix,
        "action": event.action,
        "evidence_scope": EVIDENCE_SCOPE,
        "verification_status": "native_parser_verified",
    }
    return route_row, raw_row


def execute_targeted_scan(
    plan: Mapping[str, Any],
    raw_root: str | Path,
    *,
    factory_builder: Callable[..., Any] = _native_factory,
    adapted_record_iterator: Callable[..., Iterable[AdaptedUpdateRecord]] = (
        iter_adapted_update_records
    ),
    max_spool_bytes: int = DEFAULT_MAX_SPOOL_BYTES,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """逐 artifact 单遍扫描；只保留 allowlist prefix，不做状态回放。"""

    if (
        plan.get("schema_version") != PLAN_SCHEMA_VERSION
        or plan.get("evidence_scope") != EVIDENCE_SCOPE
        or plan.get("execution_state") != "planned_not_executed"
    ):
        raise TargetedRawError("定向 raw 计划状态非法")
    artifacts = plan.get("selected_artifacts")
    slots = plan.get("requested_update_slots")
    if (
        not isinstance(artifacts, list)
        or not isinstance(slots, list)
        or len(artifacts) != MAX_UPDATE_SLOT_COUNT
        or len(slots) != MAX_UPDATE_SLOT_COUNT
        or [row.get("artifact_time_utc") for row in artifacts] != slots
    ):
        raise TargetedRawError("执行计划必须精确绑定 13 个请求 artifact")
    pairs_by_prefix = _pairs_by_prefix(plan)
    allowlist = frozenset(pairs_by_prefix)
    raw_root_path = _safe_directory(raw_root, "raw_root")
    window = plan.get("selection_window")
    limits = plan.get("limits")
    if not isinstance(window, Mapping) or not isinstance(limits, Mapping):
        raise TargetedRawError("执行计划缺少 window/limits")
    parser_contract = _validated_parser_contract(plan.get("native_parser_contract"))
    selected_compressed_bytes = sum(int(row["size_bytes"]) for row in artifacts)
    if (
        limits.get("maximum_selected_compressed_bytes")
        != MAX_SELECTED_COMPRESSED_BYTES
        or limits.get("selected_compressed_bytes") != selected_compressed_bytes
        or selected_compressed_bytes > MAX_SELECTED_COMPRESSED_BYTES
    ):
        raise TargetedRawError("执行计划的 13 槽压缩字节边界不闭合")

    def selector(elements: Sequence[Any]) -> tuple[bool, ...]:
        selected = []
        for element in elements:
            try:
                canonical = ipaddress.ip_network(
                    element.prefix, strict=False
                ).compressed
            except (AttributeError, TypeError, ValueError) as error:
                raise TargetedRawError(
                    "route element prefix 无法规范化后执行 allowlist"
                ) from error
            selected.append(canonical in allowlist)
        return tuple(selected)

    route_rows: list[Mapping[str, Any]] = []
    raw_rows: list[Mapping[str, Any]] = []
    artifact_stats: list[Mapping[str, Any]] = []
    seen_routes: set[str] = set()
    seen_raw: set[str] = set()
    retained_by_pair = {str(row["pair_index"]): 0 for row in plan["requested_pairs"]}
    aggregate_physical = 0
    aggregate_elements = 0
    aggregate_compressed = 0
    started_at = clock()

    def assert_soft_runtime(stage: str) -> None:
        now = clock()
        elapsed = now - started_at
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not isinstance(elapsed, (int, float))
            or elapsed < 0
        ):
            raise TargetedRawError("运行时 clock 非法或倒退")
        if elapsed > SOFT_RUNTIME_SECONDS:
            raise TargetedRawError(
                f"定向 raw 运行超过 540 秒软限：stage={stage}, "
                f"elapsed_seconds={elapsed:.3f}"
            )

    for artifact_index, artifact in enumerate(artifacts):
        assert_soft_runtime(f"before_artifact_{artifact_index}")
        try:
            factory = factory_builder(
                raw_root_path,
                (artifact,),
                window=window,
                max_spool_bytes=max_spool_bytes,
                max_frame_bytes=limits["max_frame_bytes"],
            )
            _validate_generated_parser_attestation(
                factory.parser_attestation,
                contract=parser_contract,
            )
            stream = factory(artifact)
            retained_before = len(route_rows)
            retained_announce = 0
            retained_withdraw = 0
            for adapted_index, adapted in enumerate(
                adapted_record_iterator(
                    stream,
                    artifact=artifact,
                    route_element_retention_selector=selector,
                )
            ):
                if adapted_index % 1024 == 0:
                    assert_soft_runtime(
                        f"artifact_{artifact_index}_record_{adapted_index}"
                    )
                if not isinstance(adapted, AdaptedUpdateRecord):
                    raise TargetedRawError("UPDATE 适配器返回非 AdaptedUpdateRecord")
                for event in adapted.route_events:
                    pairs = pairs_by_prefix.get(event.prefix)
                    if pairs is None:
                        raise TargetedRawError("适配器保留了 allowlist 外前缀")
                    if event.route_event_id in seen_routes:
                        raise TargetedRawError("RouteEvent 稳定身份重复")
                    route_row, raw_row = _evidence_rows(
                        event, adapted.raw_record, pairs
                    )
                    raw_id = raw_row["raw_record_ref_id"]
                    if raw_id in seen_raw:
                        raise TargetedRawError("raw record element 稳定身份重复")
                    seen_routes.add(event.route_event_id)
                    seen_raw.add(raw_id)
                    route_rows.append(route_row)
                    raw_rows.append(raw_row)
                    for pair in pairs:
                        retained_by_pair[str(pair["pair_index"])] += 1
                    retained_announce += event.action == "announce"
                    retained_withdraw += event.action == "withdraw"
                    if len(route_rows) > MAX_RETAINED_ROUTE_EVENTS:
                        raise TargetedRawError("保留 RouteEvent 数超过定向硬上限")
            stats_by_artifact = getattr(factory, "statistics_by_artifact", None)
            attestation = getattr(factory, "parser_attestation", None)
            if not isinstance(stats_by_artifact, Mapping) or not isinstance(
                attestation, Mapping
            ):
                raise TargetedRawError("Native parser 缺少 statistics/attestation")
            stats = stats_by_artifact.get(artifact["artifact_id"])
            if (
                not isinstance(stats, Mapping)
                or stats.get("status") != "complete"
                or stats.get("compressed_read_passes") != 1
                or stats.get("compressed_file_sha256")
                != artifact.get("file_sha256")
                or stats.get("compressed_size_bytes") != artifact.get("size_bytes")
            ):
                raise TargetedRawError("Native parser 单遍完整性统计不闭合")
            physical = stats.get("physical_record_count")
            elements = stats.get("route_element_count")
            compressed = stats.get("compressed_size_bytes")
            if any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (physical, elements, compressed)
            ):
                raise TargetedRawError("Native parser 计数字段非法")
            aggregate_physical += physical
            aggregate_elements += elements
            aggregate_compressed += compressed
            artifact_stats.append(
                {
                    "artifact_index": artifact_index,
                    "artifact_id": artifact["artifact_id"],
                    "artifact_time_utc": artifact["artifact_time_utc"],
                    "file_sha256": artifact["file_sha256"],
                    "size_bytes": artifact["size_bytes"],
                    "native_statistics": dict(stats),
                    "runtime_parser_attestation": dict(attestation),
                    "parser_attestation_fingerprint_sha256": attestation.get(
                        "attestation_fingerprint_sha256"
                    ),
                    "retained_route_event_count": len(route_rows) - retained_before,
                    "retained_announce_count": retained_announce,
                    "retained_withdraw_count": retained_withdraw,
                }
            )
            assert_soft_runtime(f"after_artifact_{artifact_index}")
        except TargetedRawError:
            raise
        except Exception as error:
            raise TargetedRawError(
                "定向 UPDATE artifact 扫描失败："
                f"index={artifact_index}, slot={artifact.get('artifact_time_utc')}, "
                f"{type(error).__name__}: {error}"
            ) from error

    if len(route_rows) != len(raw_rows):
        raise TargetedRawError("RouteEvent 与 raw ref 数量不闭合")
    if aggregate_compressed != selected_compressed_bytes:
        raise TargetedRawError("实际压缩读取字节与冻结 13 槽计划不一致")
    entity_observations = []
    for pair in plan["requested_pairs"]:
        count = retained_by_pair[str(pair["pair_index"])]
        entity_observations.append(
            {
                "pair_index": pair["pair_index"],
                "asn": pair["asn"],
                "prefix": pair["prefix"],
                "observation_count": count,
                "observation_state": (
                    "message_observations_present"
                    if count
                    else "no_observation_in_requested_window"
                ),
                "interpretation_zh": (
                    "在固定 13 槽内存在该前缀的 UPDATE 报文观测。"
                    if count
                    else (
                        "固定 13 槽内未命中该前缀；这不等于不可见、恢复或数据"
                        "缺失，且不会据此自动扩大窗口。"
                    )
                ),
                "window_expanded": False,
            }
        )
    parser_stats = {
        "schema_version": PARSER_STATS_SCHEMA_VERSION,
        "plan_id": plan["plan_id"],
        "evidence_scope": EVIDENCE_SCOPE,
        "execution_state": "completed",
        "artifact_stats": artifact_stats,
        "entity_observations": entity_observations,
        "aggregate": {
            "selected_update_artifact_count": len(artifacts),
            "compressed_read_pass_count": len(artifacts),
            "compressed_bytes_read": aggregate_compressed,
            "physical_record_count": aggregate_physical,
            "route_element_count_before_prefix_filter": aggregate_elements,
            "retained_route_event_count": len(route_rows),
            "retained_raw_record_ref_count": len(raw_rows),
            "retained_announce_count": sum(
                row["action"] == "announce" for row in route_rows
            ),
            "retained_withdraw_count": sum(
                row["action"] == "withdraw" for row in route_rows
            ),
            "retained_observation_count_by_pair_index": retained_by_pair,
            "elapsed_seconds_observed": clock() - started_at,
        },
        "non_actions": {
            "database_connections": 0,
            "database_writes": 0,
            "rib_files_opened": 0,
            "seed_performed": False,
            "state_replay_performed": False,
            "causal_claim_allowed": False,
        },
    }
    return {
        "route_rows": route_rows,
        "raw_rows": raw_rows,
        "parser_stats": parser_stats,
    }


def _write_create_only(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o440,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _assert_no_symlink_ancestors(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if not current.exists() and not current.is_symlink():
            continue
        if current.is_symlink():
            raise TargetedRawError(f"输出路径祖先不能是符号链接：{current}")


def publish_result(
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    output_directory: str | Path,
    *,
    raw_root: str | Path,
    prepared_directory: str | Path,
) -> dict[str, Any]:
    """以同级 staging + 原子 rename 发布不可覆盖的五文件结果。"""

    output = Path(output_directory).expanduser().resolve(strict=False)
    raw = _safe_directory(raw_root, "raw_root")
    prepared = _safe_directory(prepared_directory, "prepared_directory")
    _assert_disjoint(output, raw, "输出目录不得与 raw_root 重叠")
    _assert_disjoint(output, prepared, "输出目录不得与 prepared_directory 重叠")
    _assert_mutation_target_allowed(output, "输出目录")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output}")
    parent = output.parent
    _assert_no_symlink_ancestors(parent)
    parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    _assert_no_symlink_ancestors(parent)
    metadata = parent.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise TargetedRawError("输出父目录必须是非符号链接目录")
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging.", dir=str(parent))
    )
    try:
        route_path = staging / "route-events.jsonl.gz"
        raw_path = staging / "raw-record-refs.jsonl.gz"
        stats_path = staging / "parser-stats.json"
        _write_create_only(route_path, _gzip_jsonl(result["route_rows"]))
        _write_create_only(raw_path, _gzip_jsonl(result["raw_rows"]))
        _write_create_only(stats_path, _json_bytes(result["parser_stats"]))
        contents = {
            "route_events": _file_ref(route_path),
            "raw_record_refs": _file_ref(raw_path),
            "parser_stats": _file_ref(stats_path),
        }
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "package_schema_version": SCHEMA_VERSION,
            "plan": dict(plan),
            "evidence_scope": EVIDENCE_SCOPE,
            "acceptance_state": "message_observations_only_not_state_replay",
            "contents": contents,
            "counts": {
                "route_event_count": len(result["route_rows"]),
                "raw_record_ref_count": len(result["raw_rows"]),
                "update_artifact_count": MAX_UPDATE_SLOT_COUNT,
            },
            "entity_observations": result["parser_stats"][
                "entity_observations"
            ],
            "limitations_zh": [
                "仅扫描请求中的 13 个五分钟 UPDATE 槽，不代表完整研究窗口。",
                "仅保留 4 个数据库选定前缀；WITHDRAW 无 AS_PATH，只能按前缀关联。",
                "未读取 RIB、未 seed、未重放状态，不能回答完整前中后状态或恢复过程。",
                "ANNOUNCE origin 匹配仅是报文观测，不证明前兆因果或政治原因。",
            ],
            "database_connections": 0,
            "database_writes": 0,
            "rib_files_opened": 0,
            "state_replay_performed": False,
            "causal_claim_allowed": False,
        }
        manifest_path = staging / "MANIFEST.json"
        _write_create_only(manifest_path, _json_bytes(manifest))
        checksum_names = (
            "route-events.jsonl.gz",
            "raw-record-refs.jsonl.gz",
            "parser-stats.json",
            "MANIFEST.json",
        )
        checksum_rows = [
            f"{_file_ref(staging / name)['sha256']}  {name}"
            for name in checksum_names
        ]
        _write_create_only(
            staging / "SHA256SUMS",
            ("\n".join(checksum_rows) + "\n").encode("utf-8"),
        )
        directory_fd = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        if output.exists() or output.is_symlink():
            raise FileExistsError("输出目录在发布期间出现，拒绝覆盖")
        os.rename(staging, output)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    files = []
    for name in (
        "route-events.jsonl.gz",
        "raw-record-refs.jsonl.gz",
        "parser-stats.json",
        "MANIFEST.json",
        "SHA256SUMS",
    ):
        ref = _file_ref(output / name)
        files.append({"name": name, **{k: v for k, v in ref.items() if k != "path"}})
    return {
        "output_directory": str(output),
        "plan_id": plan["plan_id"],
        "evidence_scope": EVIDENCE_SCOPE,
        "files": files,
    }


def _load_plan_inputs(arguments: argparse.Namespace) -> tuple[
    Mapping[str, Any], Path, Path
]:
    raw_root = _safe_directory(arguments.raw_root, "raw_root")
    prepared = _load_prepared(arguments.prepared_directory, raw_root)
    db_first, db_ref = _load_db_first(arguments.db_first_json)
    preparation_path = prepared["root"] / "PREPARATION.json"
    preparation_sha, preparation_size = _hash_regular(
        preparation_path, maximum_bytes=16 * 1024 * 1024
    )
    executor_path = Path(__file__).resolve()
    executor_sha, executor_size = _hash_regular(
        executor_path, maximum_bytes=16 * 1024 * 1024
    )
    plan = build_plan(
        db_first,
        prepared["full-selection.json"],
        native_parser_contract=prepared["native-parser-contract.json"],
        targeted_executor_ref={
            "path": executor_path.relative_to(REPOSITORY_ROOT).as_posix(),
            "sha256": executor_sha,
            "size_bytes": executor_size,
        },
        db_first_ref=db_ref,
        preparation_ref={
            "path": "PREPARATION.json",
            "sha256": preparation_sha,
            "size_bytes": preparation_size,
        },
        max_frame_bytes=arguments.max_frame_bytes,
    )
    return plan, raw_root, prepared["root"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从 DB-first 请求提取 13 槽、4 前缀的最小 UPDATE 报文观测"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--db-first-json", type=Path, required=True)
        command.add_argument("--prepared-directory", type=Path, required=True)
        command.add_argument("--raw-root", type=Path, required=True)
        command.add_argument(
            "--max-frame-bytes", type=int, default=DEFAULT_MAX_FRAME_BYTES
        )
        if name == "run":
            command.add_argument("--output-directory", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        plan, raw_root, prepared_root = _load_plan_inputs(arguments)
        if arguments.command == "plan":
            sys.stdout.write(
                json.dumps(
                    _jsonable(plan),
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            )
            return 0
        result = execute_targeted_scan(plan, raw_root)
        published = publish_result(
            plan,
            result,
            arguments.output_directory,
            raw_root=raw_root,
            prepared_directory=prepared_root,
        )
        sys.stdout.write(
            json.dumps(
                _jsonable(published),
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )
    except (TargetedRawError, ExecutionPrepError, FileExistsError, OSError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
