#!/usr/bin/env python3
"""RRC25 伊朗研究窗口的 journal 初始化、分段执行与只读核验入口。

本入口只编排 ``full_window_worker`` 和 ``full_window_journal`` 已冻结的外围
API。它不会连接数据库；每次打开 UPDATE 之前先写入 raw ledger 预留单据，且
只在完整耗尽一个五分钟制品后推进 ``CURRENT``。``run-bounded`` 也只在 artifact
边界继续或停止，不提供 gzip 中途续跑。

``--selection`` 必须是上游已验证并冻结的 selection JSON；本入口不会把原始
artifact manifest 自行解释成另一套时间窗。UPDATE 的规范顺序唯一来自
``roles.analysis_updates``。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.data_pipeline.route_event import (  # noqa: E402
    BGPDUMP_APPROVED_VERSION,
    NATIVE_UPDATE_EXECUTION_POLICY,
    NATIVE_UPDATE_PARSER_NAME,
    NATIVE_UPDATE_PARSER_VERSION,
    make_bgpdump_record_stream_factory,
    make_native_update_record_stream_factory,
)
from backend.data_pipeline.route_event.artifacts import (  # noqa: E402
    PILOT_ABSOLUTE_MAX_PHYSICAL_RECORDS,
    PILOT_ABSOLUTE_MAX_ROUTE_EVENTS,
)
from backend.data_pipeline.research.rrc25_country_outage.coordinator import (  # noqa: E402
    DEFAULT_PRODUCTION_ROOTS,
    DEFAULT_PROTECTED_ROOTS,
    load_json_metadata,
)
from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import (  # noqa: E402
    canonical_json,
    write_canonical_json,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (  # noqa: E402
    build_raw_retention_mapping_union,
    mapping_bundle_sha256,
    mapping_view_from_frozen_snapshot,
    mapping_view_from_revised_snapshot,
)
from backend.data_pipeline.research.rrc25_country_outage.full_window_journal import (  # noqa: E402
    begin_artifact_attempt,
    cumulative_reserved_raw_bytes,
    frozen_journal_head,
    full_window_execution_lock,
    load_full_window_head,
    plan_artifact_admission,
    reconcile_abandoned_active_attempt,
)
from backend.data_pipeline.research.rrc25_country_outage.full_window_worker import (  # noqa: E402
    artifact_descriptor_from_manifest,
    initialize_journal_from_verified_seed,
    load_verified_full_seed_bootstrap,
    run_one_update_artifact,
)
from backend.data_pipeline.research.rrc25_country_outage.full_window_selection import (  # noqa: E402
    validate_complete_selection_against_profile,
)
from backend.data_pipeline.research.rrc25_country_outage.input_resolver import (  # noqa: E402
    SELECTION_ID_SCHEMA,
    SELECTION_SCHEMA_VERSION,
)
from backend.data_pipeline.research.rrc25_country_outage.profile import (  # noqa: E402
    profile_sha256,
    validate_research_profile,
)
from dev.data_quality.rrc25_iran_bounded_pilot import (  # noqa: E402
    build_code_identity,
)


UTC = timezone.utc
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^research_run_v1_[0-9a-f]{24}$")
_BINDING_FIELDS = frozenset(
    {"profile_sha256", "input_selection_sha256", "code_sha256", "mapping_sha256"}
)
DEFAULT_GLOBAL_SOFT_STOP_SECONDS = 540.0
DEFAULT_MAX_SPOOL_BYTES = 4_000_000_000
MAX_ARTIFACTS_PER_PROCESS = 5
INIT_SOFT_TIMEOUT_SECONDS = 540.0
INIT_HARD_TIMEOUT_SECONDS = 600.0
SUPERVISOR_REAP_TIMEOUT_SECONDS = 1.0
MAX_TEMPORARY_BYTES = 5_000_000_000
SUPERVISOR_CAPABILITY_ENV = (
    "DOMEYE_RRC25_FULL_WINDOW_SUPERVISOR_CAPABILITY"
)
_SUPERVISOR_CAPABILITY_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
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
PROBE_THROUGHPUT_SCHEMA_VERSION = "rrc25-native-probe-throughput/v1"
PROBE_THROUGHPUT_FINGERPRINT_SCHEMA = "rrc25_native_probe_throughput_v1"
FULL_FLOW_RAW_PROJECTION_SCHEMA_VERSION = "rrc25-full-flow-raw-projection/v1"
FULL_FLOW_RAW_PROJECTION_FINGERPRINT_SCHEMA = (
    "rrc25_full_flow_raw_projection_v1"
)
EXECUTION_CONTRACT_SCHEMA_VERSION = "rrc25-full-window-execution-contract/v1"
EXECUTION_CONTRACT_FINGERPRINT_SCHEMA = (
    "rrc25_full_window_execution_contract_v1"
)
EXECUTION_CONTRACT_FILE_NAME = "EXECUTION-CONTRACT.json"
_FILE_IDENTITY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


class FullWindowCliError(ValueError):
    """full-window CLI 输入或编排边界不闭合。"""


def _assert_journal_root_allowed(root_value: str | Path) -> Path:
    """研究 journal 不得与旧项目、生产部署根重叠或互相嵌套。"""

    candidate = Path(root_value).expanduser().resolve(strict=False)
    for value in (*DEFAULT_PROTECTED_ROOTS, *DEFAULT_PRODUCTION_ROOTS):
        protected = Path(value).resolve(strict=False)
        if (
            candidate == protected
            or protected in candidate.parents
            or candidate in protected.parents
        ):
            raise FullWindowCliError(
                f"journal_root 与受保护旧项目/生产路径重叠：{protected}"
            )
    return candidate


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise FullWindowCliError(f"{field} 必须是小写 SHA256")
    return value


def _load_bindings(path: str | Path) -> dict[str, str]:
    payload = load_json_metadata(path, maximum_bytes=1024 * 1024)
    if set(payload) != _BINDING_FIELDS:
        raise FullWindowCliError("bindings 必须且只能包含四个冻结 SHA256 字段")
    return {name: _sha(payload[name], f"bindings.{name}") for name in sorted(payload)}


def _load_canonical_fingerprinted_receipt(
    path: Path, *, schema_version: str, fingerprint_schema: str
) -> Mapping[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise FullWindowCliError("seed spool 退役收据不可读") from error
    chunks = []
    total = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FullWindowCliError(
                "seed spool 退役收据必须是非符号链接普通文件"
            )
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > 16 * 1024 * 1024:
                raise FullWindowCliError("seed spool 退役收据超过读取上限")
            chunks.append(block)
        after = os.fstat(descriptor)
        if any(
            getattr(before, field) != getattr(after, field)
            for field in _FILE_IDENTITY_FIELDS
        ):
            raise FullWindowCliError("seed spool 退役收据在读取期间变化")
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FullWindowCliError("seed spool 退役收据不是合法 JSON") from error
    if not isinstance(payload, Mapping) or payload.get("schema_version") != schema_version:
        raise FullWindowCliError("seed spool 退役收据 schema 不受支持")
    if raw != (canonical_json(dict(payload)) + "\n").encode("utf-8"):
        raise FullWindowCliError("seed spool 退役收据不是规范 JSON")
    semantic = dict(payload)
    fingerprint = semantic.pop("receipt_fingerprint_sha256", None)
    expected = hashlib.sha256(
        canonical_json(
            {"schema": fingerprint_schema, "receipt": semantic}
        ).encode("utf-8")
    ).hexdigest()
    if fingerprint != expected:
        raise FullWindowCliError("seed spool 退役收据 fingerprint 不一致")
    return dict(payload)


def _stable_identity(path: Path, expected: Any, *, field: str) -> None:
    if not isinstance(expected, Mapping) or set(expected) != set(_FILE_IDENTITY_FIELDS):
        raise FullWindowCliError(f"{field} stable identity 字段不闭合")
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise FullWindowCliError(f"{field} 必须是非符号链接普通文件")
    if any(
        isinstance(expected[name], bool)
        or not isinstance(expected[name], int)
        or expected[name] < 0
        or getattr(metadata, name) != expected[name]
        for name in _FILE_IDENTITY_FIELDS
    ):
        raise FullWindowCliError(f"{field} stat identity 与退役收据不一致")


def _directory_regular_bytes(path: Path, *, seen: set[tuple[int, int]]) -> int:
    total = 0
    for candidate in path.rglob("*"):
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise FullWindowCliError("临时目录计量不接受符号链接")
        if stat.S_ISREG(metadata.st_mode):
            identity = (metadata.st_dev, metadata.st_ino)
            if identity not in seen:
                seen.add(identity)
                total += metadata.st_size
    return total


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise FullWindowCliError(f"{field} 必须是秒级 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise FullWindowCliError(f"{field} 不是合法 UTC 时间") from error
    if parsed.microsecond or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise FullWindowCliError(f"{field} 必须是秒级 UTC")
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise FullWindowCliError(f"{field} 不是规范 UTC")
    return parsed


def _selection_updates(selection: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    if (
        selection.get("schema_version") != SELECTION_SCHEMA_VERSION
        or selection.get("status") != "complete"
        or selection.get("failures") != []
    ):
        raise FullWindowCliError("selection 必须是无缺口的规范 complete selection")
    window = selection.get("window")
    if not isinstance(window, Mapping):
        raise FullWindowCliError("selection.window 必须是对象")
    start = _utc(window.get("start_utc"), "selection.window.start_utc")
    end = _utc(
        window.get("end_exclusive_utc"),
        "selection.window.end_exclusive_utc",
    )
    if (
        window.get("interval_semantics") != "half_open"
        or window.get("granularity_seconds") != 300
        or start >= end
        or int((end - start).total_seconds()) % 300
    ):
        raise FullWindowCliError("selection.window 必须是五分钟对齐的半开窗口")
    roles = selection.get("roles")
    rows = roles.get("analysis_updates") if isinstance(roles, Mapping) else None
    if not isinstance(rows, list) or not rows:
        raise FullWindowCliError("selection.roles.analysis_updates 必须是非空数组")
    normalized: list[Mapping[str, Any]] = []
    identities: set[str] = set()
    prior: datetime | None = None
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise FullWindowCliError(f"analysis_updates[{index}] 必须是对象")
        if row.get("artifact_type") != "update" or row.get("compression") != "gz":
            raise FullWindowCliError("analysis_updates 只允许 gzip UPDATE 制品")
        descriptor = artifact_descriptor_from_manifest(index, row)
        descriptor.to_dict()
        if descriptor.artifact_id in identities:
            raise FullWindowCliError("analysis_updates artifact_id 重复")
        identities.add(descriptor.artifact_id)
        current = _utc(row.get("artifact_time_utc"), "artifact_time_utc")
        if current.second or current.minute % 5:
            raise FullWindowCliError("analysis_updates 未对齐五分钟槽")
        if prior is not None and current != prior + timedelta(minutes=5):
            raise FullWindowCliError("analysis_updates 必须按时间严格连续且不得重排")
        prior = current
        if not isinstance(row.get("relative_path"), str):
            raise FullWindowCliError("analysis_updates 缺少 relative_path")
        normalized.append(dict(row))
    expected_count = int((end - start).total_seconds()) // 300
    coverage = selection.get("coverage")
    update_coverage = (
        coverage.get("analysis_updates") if isinstance(coverage, Mapping) else None
    )
    if (
        len(normalized) != expected_count
        or prior is None
        or _utc(normalized[0]["artifact_time_utc"], "first artifact time")
        != start
        or prior + timedelta(minutes=5) != end
        or not isinstance(update_coverage, Mapping)
        or update_coverage.get("expected_count") != expected_count
        or update_coverage.get("observed_count") != expected_count
        or update_coverage.get("missing_count") != 0
    ):
        raise FullWindowCliError("analysis_updates 未精确覆盖 selection 半开窗口")
    return tuple(normalized)


def _verify_selection_binding(
    selection: Mapping[str, Any], bindings: Mapping[str, str]
) -> None:
    semantic = {
        key: value
        for key, value in selection.items()
        if key not in {"selection_id", "semantic_fingerprint_sha256"}
    }
    actual = hashlib.sha256(canonical_json(semantic).encode("utf-8")).hexdigest()
    declared = selection.get("semantic_fingerprint_sha256")
    if declared != actual or bindings["input_selection_sha256"] != actual:
        raise FullWindowCliError("selection 内容指纹与 bindings 不一致")
    expected_id = "rsel_v1_" + hashlib.sha256(
        canonical_json(
            {"schema": SELECTION_ID_SCHEMA, "selection": semantic}
        ).encode("utf-8")
    ).hexdigest()[:32]
    if selection.get("selection_id") != expected_id:
        raise FullWindowCliError("selection_id 与规范语义内容不一致")


def _verify_profile_selection_binding(
    profile_path: str | Path,
    *,
    selection: Mapping[str, Any],
    bindings: Mapping[str, str],
) -> Mapping[str, Any]:
    profile = validate_complete_selection_against_profile(
        selection,
        validate_research_profile(load_json_metadata(profile_path)),
    )
    if profile_sha256(profile) != bindings["profile_sha256"]:
        raise FullWindowCliError("研究 Profile 内容与 bindings.profile_sha256 不一致")
    return profile


def _load_mapping_context(
    compatible_path: str | Path,
    revised_path: str | Path,
    *,
    expected_mapping_sha256: str,
) -> tuple[Any, Any, Any]:
    compatible_snapshot = load_json_metadata(
        compatible_path, maximum_bytes=64 * 1024 * 1024
    )
    revised_snapshot = load_json_metadata(
        revised_path, maximum_bytes=16 * 1024 * 1024
    )
    actual_mapping_sha = mapping_bundle_sha256(
        compatible_snapshot, revised_snapshot
    )
    if actual_mapping_sha != expected_mapping_sha256:
        raise FullWindowCliError(
            "compatible/revised mapping bundle 与 bindings.mapping_sha256 不一致"
        )
    compatible = mapping_view_from_frozen_snapshot(compatible_snapshot)
    revised = mapping_view_from_revised_snapshot(revised_snapshot, compatible_snapshot)
    raw_union = build_raw_retention_mapping_union((compatible, revised))
    return compatible, revised, raw_union


def _validate_seed_retirement_for_init(
    args: argparse.Namespace,
    *,
    selection: Mapping[str, Any],
    bootstrap: Any,
) -> Mapping[str, int]:
    receipt_path = Path(args.seed_spool_retirement_receipt).expanduser().resolve(
        strict=True
    )
    _assert_journal_root_allowed(receipt_path)
    journal_root = Path(args.journal_root).expanduser().resolve(strict=False)
    if (
        receipt_path.parent == journal_root
        or receipt_path.parent in journal_root.parents
        or journal_root in receipt_path.parent.parents
    ):
        raise FullWindowCliError("seed spool 退役收据目录必须与 journal_root 独立")
    receipt = _load_canonical_fingerprinted_receipt(
        receipt_path,
        schema_version=SEED_RETIREMENT_RECEIPT_SCHEMA,
        fingerprint_schema=SEED_RETIREMENT_RECEIPT_FINGERPRINT_SCHEMA,
    )
    if (
        receipt.get("operation") != "seed_spool_retirement"
        or receipt.get("recoverable_by_rebuild_from_compressed_raw") is not True
    ):
        raise FullWindowCliError("seed spool 退役收据不是已验证成功语义")
    checkpoint = receipt.get("checkpoint")
    if not isinstance(checkpoint, Mapping) or (
        Path(str(checkpoint.get("path"))).expanduser().resolve(strict=True)
        != bootstrap.checkpoint_path.resolve(strict=True)
        or checkpoint.get("checkpoint_sequence") != bootstrap.checkpoint_sequence
        or checkpoint.get("checkpoint_fingerprint_sha256")
        != bootstrap.checkpoint_fingerprint_sha256
    ):
        raise FullWindowCliError("seed spool 退役收据与 full-seed checkpoint 不闭合")
    accounting = receipt.get("resource_accounting")
    if not isinstance(accounting, Mapping):
        raise FullWindowCliError("seed spool 退役收据缺少 raw accounting")
    checkpoint_cumulative = accounting.get(
        "checkpoint_cumulative_new_raw_read_bytes"
    )
    cumulative_after = accounting.get(
        "cumulative_new_raw_read_bytes_after_retirement_verification"
    )
    additional = args.additional_pre_update_raw_read_bytes
    if (
        isinstance(checkpoint_cumulative, bool)
        or not isinstance(checkpoint_cumulative, int)
        or isinstance(cumulative_after, bool)
        or not isinstance(cumulative_after, int)
        or checkpoint_cumulative
        != bootstrap.prior_raw_read_bytes + bootstrap.seed_artifact_read_bytes
        or cumulative_after < checkpoint_cumulative
        or additional != cumulative_after - checkpoint_cumulative
    ):
        raise FullWindowCliError(
            "additional raw bytes 必须精确等于退役累计减 checkpoint 累计"
        )
    spool = receipt.get("spool")
    if not isinstance(spool, Mapping) or not isinstance(spool.get("path"), str):
        raise FullWindowCliError("seed spool 退役收据缺少 spool 身份")
    spool_path = Path(spool["path"]).expanduser().resolve(strict=False)
    if spool_path.exists() or spool_path.is_symlink():
        raise FullWindowCliError("seed spool 仍存在，拒绝进入 full-window init")
    compressed = receipt.get("compressed_raw")
    seed_ref = bootstrap.seed_artifact_ref
    if (
        not isinstance(compressed, Mapping)
        or compressed.get("artifact_id") != seed_ref.get("artifact_id")
        or compressed.get("sha256") != seed_ref.get("file_sha256")
        or compressed.get("size_bytes") != seed_ref.get("size_bytes")
        or compressed.get("hash_verified") is not True
        or not isinstance(compressed.get("path"), str)
    ):
        raise FullWindowCliError("seed spool 退役收据的压缩原件绑定不一致")
    compressed_path = Path(compressed["path"]).expanduser().resolve(strict=True)
    _stable_identity(
        compressed_path,
        compressed.get("stable_file_identity"),
        field="compressed seed raw",
    )
    attempt_ref = receipt.get("raw_verification_attempt_receipt")
    if not isinstance(attempt_ref, Mapping) or not isinstance(
        attempt_ref.get("path"), str
    ):
        raise FullWindowCliError("seed spool 退役收据缺少 create-only attempt 引用")
    attempt_path = Path(attempt_ref["path"]).expanduser().resolve(strict=True)
    if attempt_path.parent != receipt_path.parent:
        raise FullWindowCliError("seed spool 退役 attempt 与成功收据目录不一致")
    attempt = _load_canonical_fingerprinted_receipt(
        attempt_path,
        schema_version=SEED_RETIREMENT_ATTEMPT_SCHEMA,
        fingerprint_schema=SEED_RETIREMENT_ATTEMPT_FINGERPRINT_SCHEMA,
    )
    selection_id = selection.get("selection_id")
    if (
        not isinstance(selection_id, str)
        or attempt.get("selection_id") != selection_id
        or attempt.get("checkpoint") != checkpoint
        or attempt.get("spool") != spool
        or attempt_ref.get("receipt_fingerprint_sha256")
        != attempt.get("receipt_fingerprint_sha256")
        or attempt_ref.get("attempt_id") != attempt.get("attempt_id")
        or attempt_ref.get("status") != attempt.get("status")
    ):
        raise FullWindowCliError("seed spool 退役 attempt/checkpoint/selection 未闭合")
    attempt_accounting = attempt.get("raw_accounting")
    if not isinstance(attempt_accounting, Mapping) or (
        attempt_accounting.get("cumulative_new_raw_read_bytes_after_reservation")
        != cumulative_after
        or attempt_accounting.get("checkpoint_cumulative_new_raw_read_bytes")
        != checkpoint_cumulative
        or attempt_accounting.get("full_artifact_reserved_bytes")
        != accounting.get("retirement_verification_new_raw_read_bytes")
    ):
        raise FullWindowCliError("seed spool 退役 attempt raw accounting 未闭合")
    seen: set[tuple[int, int]] = set()
    retained_external = _directory_regular_bytes(
        bootstrap.checkpoint_path.parent, seen=seen
    ) + _directory_regular_bytes(receipt_path.parent, seen=seen)
    if retained_external >= MAX_TEMPORARY_BYTES:
        raise FullWindowCliError("退役后保留 checkpoint/receipt 已达到 5GB 边界")
    return {
        "checkpoint_cumulative_raw_bytes": checkpoint_cumulative,
        "cumulative_after_retirement_raw_bytes": cumulative_after,
        "retained_external_temporary_bytes": retained_external,
        "seed_retirement_binding": {
            "schema_version": "rrc25-seed-retirement-bootstrap-binding/v1",
            "success_receipt": dict(receipt),
            "success_receipt_file_sha256": hashlib.sha256(
                (canonical_json(dict(receipt)) + "\n").encode("utf-8")
            ).hexdigest(),
            "raw_attempt_receipt": dict(attempt),
            "raw_attempt_receipt_file_sha256": hashlib.sha256(
                (canonical_json(dict(attempt)) + "\n").encode("utf-8")
            ).hexdigest(),
            "spool_absence_verified": True,
            "compressed_raw_stable_identity_verified": True,
        },
    }


def _load_parser_attestation(args: argparse.Namespace) -> Mapping[str, Any]:
    payload = load_json_metadata(args.parser_attestation, maximum_bytes=1024 * 1024)
    common = {
        "schema_version",
        "backend",
        "parser_name",
        "parser_version",
        "binary_sha256",
        "semantic_fingerprint_sha256",
    }
    required = (
        common
        if args.parser_backend == "bgpdump"
        else common | {"binary_execution_policy", "adapter_source_sha256"}
    )
    if set(payload) != required:
        raise FullWindowCliError("parser attestation 字段不闭合")
    semantic = {
        key: payload[key] for key in sorted(required - {"semantic_fingerprint_sha256"})
    }
    fingerprint = hashlib.sha256(
        canonical_json(semantic).encode("utf-8")
    ).hexdigest()
    if payload["semantic_fingerprint_sha256"] != fingerprint:
        raise FullWindowCliError("parser attestation 内容指纹不一致")
    identity_ok = payload["backend"] == args.parser_backend
    if args.parser_backend == "bgpdump":
        identity_ok = identity_ok and (
            payload["parser_name"] == "bgpdump"
            and payload["parser_version"] == BGPDUMP_APPROVED_VERSION
            and payload["binary_sha256"]
            == _sha(args.bgpdump_sha256, "bgpdump_sha256")
        )
    else:
        identity_ok = identity_ok and (
            payload["parser_name"] == NATIVE_UPDATE_PARSER_NAME
            and payload["parser_version"] == NATIVE_UPDATE_PARSER_VERSION
            and payload["binary_execution_policy"]
            == NATIVE_UPDATE_EXECUTION_POLICY
            and isinstance(payload["adapter_source_sha256"], str)
            and _SHA256_RE.fullmatch(payload["adapter_source_sha256"]) is not None
            and isinstance(payload["binary_sha256"], str)
            and _SHA256_RE.fullmatch(payload["binary_sha256"]) is not None
        )
    if (
        payload["schema_version"] != "rrc25-full-window-parser-attestation/v1"
        or not identity_ok
    ):
        raise FullWindowCliError("parser attestation 与显式后端/二进制身份不一致")
    return dict(payload)


def _validate_generated_parser_attestation(
    generated: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(generated, Mapping):
        raise FullWindowCliError("parser factory 未提供 attestation")
    payload = dict(generated)
    fingerprint = payload.pop("attestation_fingerprint_sha256", None)
    expected = hashlib.sha256(
        canonical_json(
            {"schema": "parser_attestation_fingerprint_v1", "attestation": payload}
        ).encode("utf-8")
    ).hexdigest()
    if (
        generated.get("schema_version") != "parser_attestation_v1"
        or fingerprint != expected
        or generated.get("parser_name") != contract.get("parser_name")
        or generated.get("parser_version") != contract.get("parser_version")
        or generated.get("parser_binary_sha256") != contract.get("binary_sha256")
    ):
        raise FullWindowCliError("运行时 parser attestation 与冻结 contract 不一致")
    if contract.get("backend") == "native" and (
        generated.get("binary_execution_policy")
        != contract.get("binary_execution_policy")
        or generated.get("adapter_source_sha256")
        != contract.get("adapter_source_sha256")
    ):
        raise FullWindowCliError("native source/execution policy 与冻结 contract 不一致")
    return dict(generated)


def _canonical_payload_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        (canonical_json(dict(value)) + "\n").encode("utf-8")
    ).hexdigest()


def _verify_embedded_fingerprinted_receipt(
    value: Any,
    *,
    schema_version: str,
    fingerprint_schema: str,
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != schema_version:
        raise FullWindowCliError(f"{field} schema 不受支持")
    semantic = dict(value)
    fingerprint = semantic.pop("receipt_fingerprint_sha256", None)
    expected = hashlib.sha256(
        canonical_json(
            {"schema": fingerprint_schema, "receipt": semantic}
        ).encode("utf-8")
    ).hexdigest()
    if fingerprint != expected:
        raise FullWindowCliError(f"{field} fingerprint 不一致")
    return dict(value)


def _load_probe_throughput_receipt(
    args: argparse.Namespace,
    *,
    bindings: Mapping[str, Any],
    selection: Mapping[str, Any],
    parser_contract: Mapping[str, Any],
) -> tuple[Mapping[str, Any], str]:
    path = Path(args.probe_throughput_receipt).expanduser().resolve(strict=True)
    receipt = _load_canonical_fingerprinted_receipt(
        path,
        schema_version=PROBE_THROUGHPUT_SCHEMA_VERSION,
        fingerprint_schema=PROBE_THROUGHPUT_FINGERPRINT_SCHEMA,
    )
    file_sha = _canonical_payload_sha256(receipt)
    if file_sha != _sha(
        args.probe_throughput_receipt_sha256,
        "probe_throughput_receipt_sha256",
    ):
        raise FullWindowCliError("probe throughput receipt 文件 SHA256 不一致")
    required = {
        "schema_version",
        "prepared_bindings",
        "selection_id",
        "native_parser_contract",
        "probe_ledger_id",
        "probe_terminal_accounting_fingerprint_sha256",
        "probe_terminal_receipt_ref",
        "probe_outcome_ref",
        "probe_outcome",
        "observed_compressed_bytes",
        "elapsed_seconds",
        "derivation",
        "conservative_bytes_per_second",
        "database_write_operations",
        "receipt_fingerprint_sha256",
    }
    outcome = receipt.get("probe_outcome")
    outcome_ref = receipt.get("probe_outcome_ref")
    elapsed = receipt.get("elapsed_seconds")
    observed = receipt.get("observed_compressed_bytes")
    conservative = receipt.get("conservative_bytes_per_second")
    derivation = receipt.get("derivation")
    if (
        set(receipt) != required
        or receipt.get("prepared_bindings") != dict(bindings)
        or receipt.get("selection_id") != selection.get("selection_id")
        or receipt.get("native_parser_contract") != dict(parser_contract)
        or receipt.get("database_write_operations") != 0
        or not isinstance(outcome_ref, Mapping)
        or set(outcome_ref) != {"path", "sha256", "size_bytes"}
        or receipt.get("probe_terminal_receipt_ref") != outcome_ref
        or isinstance(observed, bool)
        or not isinstance(observed, int)
        or observed <= 0
        or isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(float(elapsed))
        or not 0 < float(elapsed) < 600
        or isinstance(conservative, bool)
        or not isinstance(conservative, int)
        or conservative <= 0
        or derivation
        != {
            "method": "floor_exact_probe_bytes_per_second_divided_by_safety_factor",
            "safety_divisor": 2,
            "minimum_bytes_per_second": 1,
        }
        or conservative != max(1, int(observed / (float(elapsed) * 2)))
    ):
        raise FullWindowCliError("probe throughput receipt 身份或推导字段不闭合")
    if not isinstance(outcome, Mapping):
        raise FullWindowCliError("probe throughput receipt 缺少 probe outcome")
    outcome_semantic = dict(outcome)
    supplied_outcome_fingerprint = outcome_semantic.pop(
        "receipt_fingerprint_sha256", None
    )
    expected_outcome_fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": "rrc25_native_probe_outcome_v1",
                "receipt": outcome_semantic,
            }
        ).encode("utf-8")
    ).hexdigest()
    if (
        outcome.get("schema_version") != "rrc25-native-probe-outcome/v1"
        or supplied_outcome_fingerprint != expected_outcome_fingerprint
        or outcome.get("outcome") != "complete_single_pass"
        or outcome.get("observed_compressed_bytes_state") != "exact"
        or outcome.get("observed_compressed_bytes_sum") != observed
        or float(outcome.get("elapsed_seconds", -1)) != float(elapsed)
        or outcome_ref.get("sha256") != _canonical_payload_sha256(outcome)
        or outcome_ref.get("size_bytes")
        != len((canonical_json(dict(outcome)) + "\n").encode("utf-8"))
    ):
        raise FullWindowCliError("probe throughput 的原始 outcome 证明不闭合")
    return receipt, file_sha


def _load_full_flow_raw_projection(
    args: argparse.Namespace,
    *,
    selection: Mapping[str, Any],
) -> tuple[Mapping[str, Any], str]:
    path = Path(args.full_flow_raw_projection).expanduser().resolve(strict=True)
    receipt = _load_canonical_fingerprinted_receipt(
        path,
        schema_version=FULL_FLOW_RAW_PROJECTION_SCHEMA_VERSION,
        fingerprint_schema=FULL_FLOW_RAW_PROJECTION_FINGERPRINT_SCHEMA,
    )
    file_sha = _canonical_payload_sha256(receipt)
    if file_sha != _sha(
        args.full_flow_raw_projection_sha256,
        "full_flow_raw_projection_sha256",
    ):
        raise FullWindowCliError("完整流程 raw 投影收据文件 SHA256 不一致")
    components = receipt.get("components")
    if not isinstance(components, Mapping):
        raise FullWindowCliError("完整流程 raw 投影缺少 components")
    required_components = {
        "pre_goal_prior_reserved_upper",
        "native_probe",
        "seed_initial_read",
        "seed_retirement_verification_reread",
        "full_update_replay",
        "analysis_rib_replay_excluding_seed",
        "baseline_reference_rib",
        "minimum_failure_retry_margin",
    }
    component_bytes = []
    for name in required_components:
        row = components.get(name)
        size = row.get("bytes") if isinstance(row, Mapping) else None
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise FullWindowCliError(f"完整流程 raw 投影 {name}.bytes 非法")
        component_bytes.append(size)
    roles = selection.get("roles")
    analysis_ribs = roles.get("analysis_ribs") if isinstance(roles, Mapping) else None
    updates = _selection_updates(selection)
    seed = roles.get("state_seed_rib") if isinstance(roles, Mapping) else None
    baseline = (
        roles.get("baseline_reference_rib") if isinstance(roles, Mapping) else None
    )
    analysis_new = (
        [
            row
            for row in analysis_ribs
            if isinstance(row, Mapping)
            and isinstance(seed, Mapping)
            and row.get("artifact_id") != seed.get("artifact_id")
        ]
        if isinstance(analysis_ribs, list)
        else []
    )
    probe_indices = tuple(
        sorted(
            {
                0,
                len(updates) // 4,
                len(updates) // 2,
                (3 * len(updates)) // 4,
                len(updates) - 1,
            }
        )
    )
    expected_probe_bytes = sum(int(updates[index]["size_bytes"]) for index in probe_indices)
    expected_update_bytes = sum(int(row["size_bytes"]) for row in updates)
    expected_analysis_bytes = sum(int(row["size_bytes"]) for row in analysis_new)
    largest = max(
        [
            int(seed["size_bytes"]),
            int(baseline["size_bytes"]),
            *(int(row["size_bytes"]) for row in updates),
            *(int(row["size_bytes"]) for row in analysis_new),
        ]
    )
    projected = receipt.get("projected_cumulative_new_raw_read_bytes")
    if (
        set(components) != required_components
        or receipt.get("selection_id") != selection.get("selection_id")
        or receipt.get("input_selection_sha256")
        != selection.get("semantic_fingerprint_sha256")
        or receipt.get("projection_allowed") is not True
        or receipt.get("maximum_cumulative_new_raw_read_bytes_exclusive")
        != 50_000_000_000
        or isinstance(projected, bool)
        or not isinstance(projected, int)
        or projected != sum(component_bytes)
        or projected >= 50_000_000_000
        or components["native_probe"].get("artifact_indices")
        != list(probe_indices)
        or components["native_probe"].get("artifact_count") != len(probe_indices)
        or components["native_probe"].get("bytes") != expected_probe_bytes
        or components["seed_initial_read"].get("bytes") != seed.get("size_bytes")
        or components["seed_retirement_verification_reread"].get("bytes")
        != seed.get("size_bytes")
        or components["full_update_replay"].get("artifact_count") != len(updates)
        or components["full_update_replay"].get("bytes") != expected_update_bytes
        or components["analysis_rib_replay_excluding_seed"].get("artifact_count")
        != len(analysis_new)
        or components["analysis_rib_replay_excluding_seed"].get("bytes")
        != expected_analysis_bytes
        or components["baseline_reference_rib"].get("bytes")
        != baseline.get("size_bytes")
        or components["minimum_failure_retry_margin"].get("bytes") != largest
        or components["minimum_failure_retry_margin"].get("minimum_retry_count")
        != 1
        or receipt.get("database_write_operations") != 0
    ):
        raise FullWindowCliError("完整流程 raw 投影与 selection 动态重算不一致")
    return receipt, file_sha


def _current_code_identity(bindings: Mapping[str, Any]) -> Mapping[str, Any]:
    identity = build_code_identity()
    if identity.get("identity_sha256") != bindings.get("code_sha256"):
        raise FullWindowCliError("当前代码身份与 full-window bindings 漂移")
    return identity


def _execution_contract_payload(semantic: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = {"schema_version": EXECUTION_CONTRACT_SCHEMA_VERSION, **dict(semantic)}
    payload["receipt_fingerprint_sha256"] = hashlib.sha256(
        canonical_json(
            {
                "schema": EXECUTION_CONTRACT_FINGERPRINT_SCHEMA,
                "receipt": payload,
            }
        ).encode("utf-8")
    ).hexdigest()
    return payload


def _load_execution_contract(
    head: Any,
    *,
    bindings: Mapping[str, Any],
    selection: Mapping[str, Any],
    parser_contract: Mapping[str, Any],
) -> Mapping[str, Any]:
    path = head.root / EXECUTION_CONTRACT_FILE_NAME
    receipt = _load_canonical_fingerprinted_receipt(
        path,
        schema_version=EXECUTION_CONTRACT_SCHEMA_VERSION,
        fingerprint_schema=EXECUTION_CONTRACT_FINGERPRINT_SCHEMA,
    )
    code_identity = _current_code_identity(bindings)
    throughput = _verify_embedded_fingerprinted_receipt(
        receipt.get("probe_throughput_receipt"),
        schema_version=PROBE_THROUGHPUT_SCHEMA_VERSION,
        fingerprint_schema=PROBE_THROUGHPUT_FINGERPRINT_SCHEMA,
        field="execution contract probe throughput",
    )
    projection = _verify_embedded_fingerprinted_receipt(
        receipt.get("full_flow_raw_projection"),
        schema_version=FULL_FLOW_RAW_PROJECTION_SCHEMA_VERSION,
        fingerprint_schema=FULL_FLOW_RAW_PROJECTION_FINGERPRINT_SCHEMA,
        field="execution contract full-flow projection",
    )
    if (
        receipt.get("run_id") != head.receipt.get("run_id")
        or receipt.get("bindings") != dict(bindings)
        or receipt.get("selection_id") != selection.get("selection_id")
        or receipt.get("raw_genesis_ref") != head.receipt.get("raw_genesis_ref")
        or receipt.get("native_parser_contract") != dict(parser_contract)
        or receipt.get("code_identity") != code_identity
        or receipt.get("probe_throughput_receipt_file_sha256")
        != _canonical_payload_sha256(throughput)
        or receipt.get("full_flow_raw_projection_file_sha256")
        != _canonical_payload_sha256(projection)
        or projection.get("selection_id") != selection.get("selection_id")
        or projection.get("input_selection_sha256")
        != selection.get("semantic_fingerprint_sha256")
        or projection.get("projection_allowed") is not True
        or projection.get("projected_cumulative_new_raw_read_bytes", 50_000_000_000)
        >= 50_000_000_000
        or throughput.get("prepared_bindings") != dict(bindings)
        or throughput.get("selection_id") != selection.get("selection_id")
        or throughput.get("native_parser_contract") != dict(parser_contract)
        or receipt.get("database_write_operations") != 0
    ):
        raise FullWindowCliError("full-window execution contract 与 genesis/当前输入不闭合")
    return receipt


def _run_init(args: argparse.Namespace) -> Mapping[str, Any]:
    _assert_journal_root_allowed(args.journal_root)
    selection = load_json_metadata(args.selection)
    updates = _selection_updates(selection)
    bindings = _load_bindings(args.bindings)
    _verify_selection_binding(selection, bindings)
    _verify_profile_selection_binding(
        args.profile, selection=selection, bindings=bindings
    )
    code_sha = _sha(args.code_sha256, "code_sha256")
    if code_sha != bindings["code_sha256"]:
        raise FullWindowCliError("code_sha256 与 bindings.code_sha256 不一致")
    code_identity = _current_code_identity(bindings)
    if getattr(args, "parser_backend", None) != "native":
        raise FullWindowCliError("伊朗 P0 full-window 真实执行只允许 native parser")
    parser_contract = _load_parser_attestation(args)
    throughput_receipt, throughput_file_sha = _load_probe_throughput_receipt(
        args,
        bindings=bindings,
        selection=selection,
        parser_contract=parser_contract,
    )
    raw_projection, raw_projection_file_sha = _load_full_flow_raw_projection(
        args,
        selection=selection,
    )
    compatible, revised, raw_union = _load_mapping_context(
        args.compatible_mapping,
        args.revised_mapping,
        expected_mapping_sha256=bindings["mapping_sha256"],
    )
    attestation = load_json_metadata(
        args.seed_spool_attestation, maximum_bytes=1024 * 1024
    )
    bootstrap = load_verified_full_seed_bootstrap(
        args.full_seed_checkpoint,
        selection=selection,
        country_mapping=compatible,
        raw_retention_mapping=raw_union,
        seed_spool_attestation=attestation,
        window_end_exclusive_utc=args.window_end_exclusive,
        code_identity_sha256=code_sha,
    )
    retirement = _validate_seed_retirement_for_init(
        args,
        selection=selection,
        bootstrap=bootstrap,
    )
    if not isinstance(args.run_id, str) or _RUN_ID_RE.fullmatch(args.run_id) is None:
        raise FullWindowCliError("run_id 必须符合 research_run_v1_<24位小写十六进制>")
    head = initialize_journal_from_verified_seed(
        args.journal_root,
        bootstrap=bootstrap,
        run_id=args.run_id,
        bindings=bindings,
        total_update_artifacts=len(updates),
        compatible_mapping=compatible,
        revised_mapping=revised,
        additional_pre_update_raw_read_bytes=args.additional_pre_update_raw_read_bytes,
        bootstrap_bytes_per_second=throughput_receipt[
            "conservative_bytes_per_second"
        ],
        retained_external_temporary_bytes=retirement[
            "retained_external_temporary_bytes"
        ],
        seed_retirement_binding=retirement["seed_retirement_binding"],
    )
    raw_total = cumulative_reserved_raw_bytes(head.root)
    if raw_total != retirement["cumulative_after_retirement_raw_bytes"]:
        raise FullWindowCliError(
            "journal raw genesis 未精确等于 seed spool 退役后的累计"
        )
    contract = _execution_contract_payload(
        {
            "run_id": head.receipt["run_id"],
            "bindings": dict(bindings),
            "selection_id": selection["selection_id"],
            "raw_genesis_ref": dict(head.receipt["raw_genesis_ref"]),
            "initial_boundary_receipt_ref": {
                "path": head.receipt_path,
                "sha256": head.receipt_sha256,
            },
            "code_identity": code_identity,
            "native_parser_contract": dict(parser_contract),
            "probe_throughput_receipt": dict(throughput_receipt),
            "probe_throughput_receipt_file_sha256": throughput_file_sha,
            "full_flow_raw_projection": dict(raw_projection),
            "full_flow_raw_projection_file_sha256": raw_projection_file_sha,
            "database_write_operations": 0,
        }
    )
    published_contract = write_canonical_json(
        head.root / EXECUTION_CONTRACT_FILE_NAME,
        contract,
        kind="rrc25_full_window_execution_contract",
        mode=0o440,
    )
    reloaded_contract = _load_execution_contract(
        head,
        bindings=bindings,
        selection=selection,
        parser_contract=parser_contract,
    )
    if reloaded_contract != contract:
        raise FullWindowCliError("full-window execution contract 发布后回读不一致")
    return {
        "ok": True,
        "command": "init",
        "run_id": head.receipt["run_id"],
        "next_artifact_index": head.next_artifact_index,
        "total_artifacts": head.receipt["total_artifacts"],
        "terminal_receipt_ref": {
            "path": head.receipt_path,
            "sha256": head.receipt_sha256,
        },
        "cumulative_reserved_raw_bytes": raw_total,
        "execution_contract": {
            "path": EXECUTION_CONTRACT_FILE_NAME,
            "sha256": published_contract.sha256,
            "size_bytes": published_contract.size_bytes,
            "receipt_fingerprint_sha256": contract[
                "receipt_fingerprint_sha256"
            ],
        },
        "bootstrap_bytes_per_second": throughput_receipt[
            "conservative_bytes_per_second"
        ],
        "full_flow_projected_cumulative_raw_bytes": raw_projection[
            "projected_cumulative_new_raw_read_bytes"
        ],
        "retained_external_temporary_bytes": retirement[
            "retained_external_temporary_bytes"
        ],
        "database_write_operations": 0,
    }


def _init_child_command(
    args: argparse.Namespace, *, supervisor_capability: str
) -> list[str]:
    values = (
        ("journal-root", args.journal_root),
        ("bindings", args.bindings),
        ("profile", args.profile),
        ("selection", args.selection),
        ("full-seed-checkpoint", args.full_seed_checkpoint),
        ("compatible-mapping", args.compatible_mapping),
        ("revised-mapping", args.revised_mapping),
        ("seed-spool-attestation", args.seed_spool_attestation),
        (
            "seed-spool-retirement-receipt",
            args.seed_spool_retirement_receipt,
        ),
        ("window-end-exclusive", args.window_end_exclusive),
        ("code-sha256", args.code_sha256),
        ("parser-backend", args.parser_backend),
        ("parser-attestation", args.parser_attestation),
        ("probe-throughput-receipt", args.probe_throughput_receipt),
        (
            "probe-throughput-receipt-sha256",
            args.probe_throughput_receipt_sha256,
        ),
        ("full-flow-raw-projection", args.full_flow_raw_projection),
        (
            "full-flow-raw-projection-sha256",
            args.full_flow_raw_projection_sha256,
        ),
        ("run-id", args.run_id),
        (
            "additional-pre-update-raw-read-bytes",
            args.additional_pre_update_raw_read_bytes,
        ),
    )
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "__init-worker",
        "--supervisor-capability",
        supervisor_capability,
    ]
    for name, value in values:
        command.extend(("--" + name, str(value)))
    return command


def _quarantine_partial_init(root_value: str | Path) -> Path | None:
    """把超时 init 的新目录移出请求路径，保证它不能被当作成功 CURRENT 加载。"""

    root = Path(root_value)
    if not root.exists() and not root.is_symlink():
        return None
    suffix = f"timed-out-{os.getpid()}-{time.time_ns()}"
    destination = root.with_name(root.name + "." + suffix)
    try:
        root.rename(destination)
        return destination
    except OSError as rename_error:
        current = root / "CURRENT"
        if current.exists() or current.is_symlink():
            disabled = root / ("CURRENT." + suffix)
            try:
                current.rename(disabled)
                return root
            except OSError as current_error:
                raise FullWindowCliError(
                    "init 超时后无法隔离 CURRENT；必须人工封锁该输出目录"
                ) from current_error
        raise FullWindowCliError("init 超时后无法隔离残留输出目录") from rename_error


def _signal_process_tree(process: Any, requested_signal: signal.Signals) -> None:
    """优先终止新 session 的整个进程组，测试替身/平台不支持时安全回退。"""

    if requested_signal not in {signal.SIGTERM, signal.SIGKILL}:
        raise FullWindowCliError("只允许向研究 worker 发送 TERM/KILL")
    pid = getattr(process, "pid", None)
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        try:
            os.killpg(pid, requested_signal)
            return
        except ProcessLookupError:
            return
        except (AttributeError, PermissionError, OSError):
            pass
    fallback = (
        getattr(process, "terminate", None)
        if requested_signal == signal.SIGTERM
        else getattr(process, "kill", None)
    )
    if not callable(fallback):
        raise FullWindowCliError("无法终止研究 worker 进程组或主进程")
    fallback()


def _process_group_alive(process: Any) -> bool:
    """即使 session leader 已退出，也探测同 PGID 的遗留后代。"""

    pid = getattr(process, "pid", None)
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except (AttributeError, OSError):
            pass
        else:
            return True
    return getattr(process, "returncode", None) is None


def _wait_for_group_after_term(
    process: Any,
    *,
    hard_deadline: float,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
) -> bool:
    """TERM 后持续探测整个 PGID；硬截止仍存活则 KILL 并有界回收。"""

    remaining = max(0.0, hard_deadline - float(clock()))
    try:
        process.communicate(timeout=remaining)
    except subprocess.TimeoutExpired:
        pass
    while _process_group_alive(process):
        remaining = hard_deadline - float(clock())
        if remaining <= 0:
            _signal_process_tree(process, signal.SIGKILL)
            try:
                process.communicate(timeout=SUPERVISOR_REAP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            return True
        sleeper(min(0.05, remaining))
    return False


def _supervisor_capability() -> str:
    capability = secrets.token_urlsafe(32)
    if _SUPERVISOR_CAPABILITY_RE.fullmatch(capability) is None:
        raise FullWindowCliError("无法生成内部 worker capability")
    return capability


def _worker_environment(capability: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment[SUPERVISOR_CAPABILITY_ENV] = capability
    return environment


def _verified_internal_worker_argv(raw_argv: Sequence[str]) -> list[str]:
    """内部入口只接受父监督器同时经 argv/env 注入的一次性 capability。"""

    if (
        len(raw_argv) < 3
        or raw_argv[1] != "--supervisor-capability"
        or _SUPERVISOR_CAPABILITY_RE.fullmatch(raw_argv[2]) is None
    ):
        raise FullWindowCliError("内部 worker 缺少父监督器 capability")
    expected = os.environ.get(SUPERVISOR_CAPABILITY_ENV)
    if not isinstance(expected, str) or not secrets.compare_digest(
        raw_argv[2], expected
    ):
        raise FullWindowCliError("内部 worker capability 的 argv/env 绑定不一致")
    os.environ.pop(SUPERVISOR_CAPABILITY_ENV, None)
    return list(raw_argv[3:])


def _supervise_init(
    args: argparse.Namespace,
    *,
    soft_timeout_seconds: float = INIT_SOFT_TIMEOUT_SECONDS,
    hard_timeout_seconds: float = INIT_HARD_TIMEOUT_SECONDS,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> Mapping[str, Any]:
    """在独立 worker 中执行 init；540 秒终止，600 秒仍不退则强杀。"""

    soft = float(soft_timeout_seconds)
    hard = float(hard_timeout_seconds)
    if not 0 < soft < hard <= INIT_HARD_TIMEOUT_SECONDS:
        raise FullWindowCliError("init timeout 必须满足 0 < soft < hard <= 600")
    requested_root = _assert_journal_root_allowed(args.journal_root)
    if requested_root.exists() or requested_root.is_symlink():
        raise FileExistsError("full-window journal 根目录已存在")
    capability = _supervisor_capability()
    process = popen_factory(
        _init_child_command(args, supervisor_capability=capability),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=_worker_environment(capability),
    )
    started = float(clock())
    try:
        stdout, stderr = process.communicate(timeout=soft)
    except subprocess.TimeoutExpired:
        _signal_process_tree(process, signal.SIGTERM)
        hard_killed = _wait_for_group_after_term(
            process,
            hard_deadline=started + hard,
            clock=clock,
            sleeper=sleeper,
        )
        quarantined = _quarantine_partial_init(requested_root)
        stage = (
            f"{hard:g} 秒硬杀" if hard_killed else f"{soft:g} 秒软停"
        )
        location = str(quarantined) if quarantined is not None else "无残留目录"
        raise FullWindowCliError(
            f"init worker 达到{stage}；未发布可加载 journal，残留隔离位置：{location}"
        )
    except KeyboardInterrupt:
        _signal_process_tree(process, signal.SIGTERM)
        _wait_for_group_after_term(
            process,
            hard_deadline=float(clock()) + max(0.001, hard - soft),
            clock=clock,
            sleeper=sleeper,
        )
        _quarantine_partial_init(requested_root)
        raise
    if process.returncode != 0:
        quarantined = _quarantine_partial_init(requested_root)
        detail = stderr.strip()[-4000:] or stdout.strip()[-4000:]
        location = str(quarantined) if quarantined is not None else "无残留目录"
        raise FullWindowCliError(
            f"init worker 失败；残留隔离位置：{location}；"
            f"错误：{detail or '无错误输出'}"
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        _quarantine_partial_init(requested_root)
        raise FullWindowCliError("init worker 成功输出不是合法 JSON") from error
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        _quarantine_partial_init(requested_root)
        raise FullWindowCliError("init worker 未返回成功闭合结果")
    return dict(payload)


def _supervise_existing_command(
    original_argv: Sequence[str],
    *,
    soft_timeout_seconds: float = INIT_SOFT_TIMEOUT_SECONDS,
    hard_timeout_seconds: float = INIT_HARD_TIMEOUT_SECONDS,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> Mapping[str, Any]:
    """监督 run/verify 子进程；超时只停进程，不破坏已经闭合的 receipt/CURRENT。"""

    soft = float(soft_timeout_seconds)
    hard = float(hard_timeout_seconds)
    if not 0 < soft < hard <= INIT_HARD_TIMEOUT_SECONDS:
        raise FullWindowCliError("worker timeout 必须满足 0 < soft < hard <= 600")
    capability = _supervisor_capability()
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "__existing-worker",
        "--supervisor-capability",
        capability,
        *map(str, original_argv),
    ]
    process = popen_factory(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=_worker_environment(capability),
    )
    started = float(clock())
    try:
        stdout, stderr = process.communicate(timeout=soft)
    except subprocess.TimeoutExpired:
        _signal_process_tree(process, signal.SIGTERM)
        hard_killed = _wait_for_group_after_term(
            process,
            hard_deadline=started + hard,
            clock=clock,
            sleeper=sleeper,
        )
        stage = f"{hard:g} 秒硬杀" if hard_killed else f"{soft:g} 秒软停"
        raise FullWindowCliError(
            f"研究 worker 达到{stage}；已闭合 artifact 边界保留，可从 CURRENT 恢复"
        )
    except KeyboardInterrupt:
        _signal_process_tree(process, signal.SIGTERM)
        _wait_for_group_after_term(
            process,
            hard_deadline=float(clock()) + max(0.001, hard - soft),
            clock=clock,
            sleeper=sleeper,
        )
        raise
    if process.returncode != 0:
        detail = stderr.strip()[-4000:] or stdout.strip()[-4000:]
        raise FullWindowCliError(f"研究 worker 失败：{detail or '无错误输出'}")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise FullWindowCliError("研究 worker 成功输出不是合法 JSON") from error
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        raise FullWindowCliError("研究 worker 未返回成功闭合结果")
    return dict(payload)


def _make_stream_factory(
    args: argparse.Namespace,
    row: Mapping[str, Any],
    *,
    parser_contract: Mapping[str, Any],
) -> tuple[Any, Mapping[str, Any]]:
    start = _utc(row["artifact_time_utc"], "artifact_time_utc")
    end = start + timedelta(minutes=5)
    data_profile = {
        "window_start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_end_exclusive_utc": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    pilot_limits = {
        "max_artifact_count": 1,
        "max_compressed_bytes": int(row["size_bytes"]),
        "max_physical_records": PILOT_ABSOLUTE_MAX_PHYSICAL_RECORDS,
        "max_route_events": PILOT_ABSOLUTE_MAX_ROUTE_EVENTS,
        "max_spool_bytes": args.max_spool_bytes,
    }
    if args.parser_backend == "native":
        factory = make_native_update_record_stream_factory(
            args.raw_root,
            (row,),
            data_profile=data_profile,
            pilot_limits=pilot_limits,
            max_frame_bytes=args.native_max_frame_bytes,
        )
    else:
        if not args.bgpdump_path or not args.bgpdump_sha256:
            raise FullWindowCliError("bgpdump backend 必须显式提供 path/SHA256")
        factory = make_bgpdump_record_stream_factory(
            args.raw_root,
            (row,),
            data_profile=data_profile,
            pilot_limits=pilot_limits,
            bgpdump_path=args.bgpdump_path,
            expected_version=BGPDUMP_APPROVED_VERSION,
            allowed_binary_sha256=(
                _sha(args.bgpdump_sha256, "bgpdump_sha256"),
            ),
            queue_capacity=args.bgpdump_queue_capacity,
            max_stdout_queue_source_bytes=args.bgpdump_queue_source_bytes,
            idle_timeout_seconds=args.idle_timeout_seconds,
        )
    attestation = _validate_generated_parser_attestation(
        factory.parser_attestation,
        contract=parser_contract,
    )
    return factory, attestation


def _process_one(
    args: argparse.Namespace,
    *,
    head: Any,
    row: Mapping[str, Any],
    compatible: Any,
    revised: Any,
    raw_union: Any,
    parser_contract: Mapping[str, Any],
    bindings: Mapping[str, Any],
    selection: Mapping[str, Any],
    worker_soft_stop_seconds: float,
    clock: Callable[[], float],
) -> Any:
    with full_window_execution_lock(head.root):
        recovered = reconcile_abandoned_active_attempt(head)
        fresh = recovered["head"]
        if fresh.receipt_sha256 != head.receipt_sha256:
            raise FullWindowCliError(
                "execution lease 获取后 CURRENT 已变化，须重新规划当前槽"
            )
        # 紧贴 raw factory/ATTEMPT 之前再次重算代码并复核 genesis companion
        # contract，避免长生命周期父进程在槽间发生代码或 parser 漂移。
        _load_execution_contract(
            fresh,
            bindings=bindings,
            selection=selection,
            parser_contract=parser_contract,
        )
        descriptor = artifact_descriptor_from_manifest(
            fresh.next_artifact_index, row
        )
        factory, generated_attestation = _make_stream_factory(
            args, row, parser_contract=parser_contract
        )
        token = begin_artifact_attempt(
            fresh,
            descriptor,
            admission_seconds=args.admission_seconds,
            max_raw_bytes=args.max_raw_bytes,
            track_active_attempt=True,
        )
        return run_one_update_artifact(
            fresh,
            token,
            artifact_manifest_row=row,
            compatible_mapping=compatible,
            revised_mapping=revised,
            raw_retention_membership=raw_union.raw_retention_membership,
            update_record_stream_factory=factory,
            parser_attestation=generated_attestation,
            retained_seed_spool_bytes=args.retained_seed_spool_bytes,
            clock=clock,
            runtime_check_interval_records=args.runtime_check_interval_records,
            soft_stop_seconds=worker_soft_stop_seconds,
        )


def _run_updates(
    args: argparse.Namespace,
    *,
    bounded: bool,
    clock: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    _assert_journal_root_allowed(args.journal_root)
    selection = load_json_metadata(args.selection)
    updates = _selection_updates(selection)
    bindings = _load_bindings(args.bindings)
    _verify_selection_binding(selection, bindings)
    _verify_profile_selection_binding(
        args.profile, selection=selection, bindings=bindings
    )
    compatible, revised, raw_union = _load_mapping_context(
        args.compatible_mapping,
        args.revised_mapping,
        expected_mapping_sha256=bindings["mapping_sha256"],
    )
    if (
        isinstance(args.global_soft_stop_seconds, bool)
        or not 0 < float(args.global_soft_stop_seconds) <= 540.0
    ):
        raise FullWindowCliError("global_soft_stop_seconds 必须位于 (0,540]")
    if (
        isinstance(args.max_spool_bytes, bool)
        or not 0 < args.max_spool_bytes < 5_000_000_000
        or isinstance(args.retained_seed_spool_bytes, bool)
        or args.retained_seed_spool_bytes < 0
        or args.max_spool_bytes + args.retained_seed_spool_bytes
        >= 5_000_000_000
    ):
        raise FullWindowCliError("seed spool 与 UPDATE spool 的合计预算必须小于 5GB")
    if (
        isinstance(args.admission_seconds, bool)
        or not 0 < float(args.admission_seconds) <= 420.0
    ):
        raise FullWindowCliError("admission_seconds 必须位于 (0,420]")
    if (
        isinstance(args.max_raw_bytes, bool)
        or not 0 < args.max_raw_bytes <= 50_000_000_000
    ):
        raise FullWindowCliError("max_raw_bytes 必须位于 (0,50GB]")
    if (
        isinstance(args.runtime_check_interval_records, bool)
        or args.runtime_check_interval_records <= 0
    ):
        raise FullWindowCliError("runtime_check_interval_records 必须是正整数")
    if getattr(args, "parser_backend", None) != "native":
        raise FullWindowCliError("伊朗 P0 full-window 真实执行只允许 native parser")
    _current_code_identity(bindings)
    parser_attestation = _load_parser_attestation(args)
    head = load_full_window_head(args.journal_root, expected_bindings=bindings)
    with full_window_execution_lock(head.root):
        head = reconcile_abandoned_active_attempt(head)["head"]
    _load_execution_contract(
        head,
        bindings=bindings,
        selection=selection,
        parser_contract=parser_attestation,
    )
    if head.receipt["total_artifacts"] != len(updates):
        raise FullWindowCliError("journal total_artifacts 与 selection UPDATE 数量不一致")
    requested = args.max_artifacts if bounded else 1
    if (
        isinstance(requested, bool)
        or not isinstance(requested, int)
        or not 1 <= requested <= MAX_ARTIFACTS_PER_PROCESS
    ):
        raise FullWindowCliError("max_artifacts 必须位于 1..5")
    process_started = float(clock())
    completed: list[Mapping[str, Any]] = []
    stop_reason: str | None = None
    while len(completed) < requested and head.next_artifact_index < len(updates):
        row = updates[head.next_artifact_index]
        elapsed = float(clock()) - process_started
        if elapsed < 0:
            raise FullWindowCliError("clock 不得倒退")
        remaining = float(args.global_soft_stop_seconds) - elapsed
        descriptor = artifact_descriptor_from_manifest(head.next_artifact_index, row)
        admission = plan_artifact_admission(
            head,
            descriptor,
            admission_seconds=args.admission_seconds,
            max_raw_bytes=args.max_raw_bytes,
        )
        if not admission.allowed:
            raise FullWindowCliError(f"下一个 artifact 预检拒绝：{admission.reason}")
        if admission.estimated_process_seconds >= remaining:
            stop_reason = "进程软边界前剩余时间不足，已在 artifact 边界停止"
            break
        committed = _process_one(
            args,
            head=head,
            row=row,
            compatible=compatible,
            revised=revised,
            raw_union=raw_union,
            parser_contract=parser_attestation,
            bindings=bindings,
            selection=selection,
            worker_soft_stop_seconds=min(540.0, remaining),
            clock=clock,
        )
        head = committed.head
        completed.append(
            {
                "artifact_index": descriptor.index,
                "artifact_id": descriptor.artifact_id,
                "slot_start_utc": descriptor.slot_start_utc,
                "receipt_ref": {
                    "path": head.receipt_path,
                    "sha256": head.receipt_sha256,
                },
            }
        )
    if head.next_artifact_index >= len(updates):
        stop_reason = "selection 中全部 UPDATE 已完成"
    elif len(completed) >= requested:
        stop_reason = "达到本次显式 artifact 数量上限"
    return {
        "ok": True,
        "command": "run-bounded" if bounded else "run-one",
        "requested_artifact_count": requested,
        "completed_this_process": len(completed),
        "completed_artifacts": completed,
        "next_artifact_index": head.next_artifact_index,
        "total_artifacts": len(updates),
        "window_complete": head.next_artifact_index == len(updates),
        "stop_reason": stop_reason,
        "parser_attestation_sha256": parser_attestation[
            "semantic_fingerprint_sha256"
        ],
        "cumulative_reserved_raw_bytes": cumulative_reserved_raw_bytes(head.root),
        "database_write_operations": 0,
    }


def _run_verify(args: argparse.Namespace) -> Mapping[str, Any]:
    _assert_journal_root_allowed(args.journal_root)
    bindings = _load_bindings(args.bindings)
    head = load_full_window_head(
        args.journal_root,
        expected_bindings=bindings,
        recover_committed_successor=False,
    )
    # frozen_journal_head 内部会从 terminal receipt 完整走回 genesis；避免再
    # 显式调用一次 ancestry verifier，导致 1928 个槽的 shard 被重复全读。
    frozen = frozen_journal_head(head)
    receipt_count = frozen["verified_receipt_count"]
    ledger = cumulative_reserved_raw_bytes(head.root)
    if frozen["cumulative_reserved_raw_bytes"] != ledger:
        raise FullWindowCliError("frozen head 与 raw ledger 累计不一致")
    return {
        "ok": True,
        "command": "verify",
        "window_complete": head.next_artifact_index == head.receipt["total_artifacts"],
        "receipt_ancestry_count": receipt_count,
        "raw_ledger_cumulative_reserved_bytes": ledger,
        "frozen_head": frozen,
        "database_write_operations": 0,
    }


def _run_reconcile(args: argparse.Namespace) -> Mapping[str, Any]:
    """不打开 raw，仅在单 worker lease 内闭合已死亡进程留下的 ACTIVE。"""

    _assert_journal_root_allowed(args.journal_root)
    bindings = _load_bindings(args.bindings)
    head = load_full_window_head(args.journal_root, expected_bindings=bindings)
    with full_window_execution_lock(head.root):
        result = reconcile_abandoned_active_attempt(head)
    fresh = result["head"]
    return {
        "ok": True,
        "command": "reconcile",
        "action": result["action"],
        "next_artifact_index": fresh.next_artifact_index,
        "window_complete": (
            fresh.next_artifact_index == fresh.receipt["total_artifacts"]
        ),
        "terminal_outcome_ref": result.get("terminal_outcome_ref"),
        "database_write_operations": 0,
    }


def _add_common_journal(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--journal-root", required=True, help="full-window journal 根目录")
    parser.add_argument("--bindings", required=True, help="四项冻结 SHA256 bindings JSON")


def _add_execution(parser: argparse.ArgumentParser) -> None:
    _add_common_journal(parser)
    parser.add_argument("--profile", required=True, help="冻结研究 Profile JSON")
    parser.add_argument("--selection", required=True, help="已验证 selection JSON")
    parser.add_argument("--compatible-mapping", required=True, help="冻结 compatible 映射 JSON")
    parser.add_argument("--revised-mapping", required=True, help="冻结 revised delta JSON")
    parser.add_argument("--raw-root", required=True, help="只读 MRT 根目录")
    parser.add_argument("--bgpdump-path", help="bgpdump backend 使用的已批准可执行文件")
    parser.add_argument("--bgpdump-sha256", help="bgpdump backend 使用的二进制 SHA256")
    parser.add_argument(
        "--parser-backend",
        required=True,
        choices=("native", "bgpdump"),
        help="显式冻结解析后端；native 为主执行器，bgpdump 仅作 oracle/fixture",
    )
    parser.add_argument(
        "--parser-attestation",
        required=True,
        help="与显式后端、版本和二进制 SHA256 闭合的 parser attestation JSON",
    )
    parser.add_argument("--retained-seed-spool-bytes", type=int, default=0)
    parser.add_argument("--admission-seconds", type=float, default=420.0)
    parser.add_argument("--global-soft-stop-seconds", type=float, default=DEFAULT_GLOBAL_SOFT_STOP_SECONDS)
    parser.add_argument("--max-raw-bytes", type=int, default=50_000_000_000)
    parser.add_argument("--max-spool-bytes", type=int, default=DEFAULT_MAX_SPOOL_BYTES)
    parser.add_argument("--runtime-check-interval-records", type=int, default=256)
    parser.add_argument("--idle-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--bgpdump-queue-capacity", type=int, default=4096)
    parser.add_argument("--bgpdump-queue-source-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--native-max-frame-bytes", type=int, default=64 * 1024 * 1024)


def _add_init_arguments(parser: argparse.ArgumentParser) -> None:
    _add_common_journal(parser)
    parser.add_argument("--profile", required=True, help="冻结研究 Profile JSON")
    parser.add_argument("--selection", required=True, help="已验证 selection JSON")
    parser.add_argument("--full-seed-checkpoint", required=True)
    parser.add_argument("--compatible-mapping", required=True)
    parser.add_argument("--revised-mapping", required=True)
    parser.add_argument("--seed-spool-attestation", required=True)
    parser.add_argument("--seed-spool-retirement-receipt", required=True)
    parser.add_argument("--window-end-exclusive", required=True)
    parser.add_argument("--code-sha256", required=True)
    parser.add_argument(
        "--parser-backend",
        required=True,
        choices=("native", "bgpdump"),
        help="P0 真实 init 仅接受 native；bgpdump 会失败关闭",
    )
    parser.add_argument("--parser-attestation", required=True)
    parser.add_argument("--probe-throughput-receipt", required=True)
    parser.add_argument("--probe-throughput-receipt-sha256", required=True)
    parser.add_argument("--full-flow-raw-projection", required=True)
    parser.add_argument("--full-flow-raw-projection-sha256", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--additional-pre-update-raw-read-bytes", type=int, required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RRC25 伊朗事件完整 UPDATE 窗口的 artifact 边界研究执行器"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="从已验证 full seed 创建 journal genesis")
    _add_init_arguments(init)

    run_one = commands.add_parser("run-one", help="处理 CURRENT 的下一个单制品")
    _add_execution(run_one)
    run_bounded = commands.add_parser(
        "run-bounded", help="从 CURRENT 连续处理有限个制品，并逐制品提交"
    )
    _add_execution(run_bounded)
    run_bounded.add_argument("--max-artifacts", type=int, required=True)

    verify = commands.add_parser("verify", help="只读核验完整 receipt ancestry、冻结头和 raw ledger")
    _add_common_journal(verify)
    reconcile = commands.add_parser(
        "reconcile", help="不读取 MRT，闭合监督器终止后遗留的 ACTIVE attempt"
    )
    _add_common_journal(reconcile)
    return parser


def _build_init_worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    _add_init_arguments(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    internal_init_worker = bool(raw_argv and raw_argv[0] == "__init-worker")
    internal_existing_worker = bool(
        raw_argv and raw_argv[0] == "__existing-worker"
    )
    try:
        internal_argv = (
            _verified_internal_worker_argv(raw_argv)
            if internal_init_worker or internal_existing_worker
            else raw_argv
        )
        args = (
            _build_init_worker_parser().parse_args(internal_argv)
            if internal_init_worker
            else build_parser().parse_args(internal_argv)
        )
        if internal_init_worker:
            result = _run_init(args)
        elif internal_existing_worker:
            if args.command == "run-one":
                result = _run_updates(args, bounded=False)
            elif args.command == "run-bounded":
                result = _run_updates(args, bounded=True)
            elif args.command == "verify":
                result = _run_verify(args)
            elif args.command == "reconcile":
                result = _run_reconcile(args)
            else:
                raise FullWindowCliError("内部 worker 不允许 init")
        elif args.command == "init":
            result = _supervise_init(args)
        else:
            result = _supervise_existing_command(raw_argv)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(
            json.dumps(
                {"ok": False, "error": str(error), "error_type": type(error).__name__},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
