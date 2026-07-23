#!/usr/bin/env python3
"""冻结完整窗口执行输入，并在正式 seed 前运行少量 Native 探针。

``prepare`` 只读取小型元数据，不打开 MRT、不连接数据库。它从已验证 manifest
重新解析完整半开 selection，冻结 compatible/revised 映射 bundle、当前代码身份、
seed spool attestation 与 Native parser contract，并以 create-only 目录发布。

``prepare`` 同时创建 probe raw ledger genesis。已有研究必须导入一份带来源
metadata SHA 的 create-only pre-ledger accounting receipt；仅全新且确认无历史 raw
读取的任务允许显式 zero genesis。``probe-native`` 最多读取五个显式 UPDATE
制品，worker 在打开第一个 raw 文件前先发布不可覆盖的 attempt reservation；
无论成功、失败或被监督器终止，reservation 都不退款。后续 seed 只接受该账本
唯一 terminal 的 ref/SHA，不再接受可手填的累计数字。该探针不是 A/B 回放，
不派生事件结论。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.data_pipeline.route_event import (  # noqa: E402
    NATIVE_UPDATE_EXECUTION_POLICY,
    NATIVE_UPDATE_PARSER_NAME,
    NATIVE_UPDATE_PARSER_VERSION,
    artifact_id_v1,
    make_native_update_record_stream_factory,
)
from backend.data_pipeline.route_event.artifacts import (  # noqa: E402
    PILOT_ABSOLUTE_MAX_PHYSICAL_RECORDS,
    PILOT_ABSOLUTE_MAX_ROUTE_EVENTS,
)
from backend.data_pipeline.research.rrc25_country_outage.bounded_pilot_worker import (  # noqa: E402
    validate_seed_spool_attestation,
)
from backend.data_pipeline.research.rrc25_country_outage.coordinator import (  # noqa: E402
    DEFAULT_PRODUCTION_ROOTS,
    DEFAULT_PROTECTED_ROOTS,
    load_json_metadata,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (  # noqa: E402
    mapping_bundle_sha256,
    mapping_view_from_frozen_snapshot,
    mapping_view_from_revised_snapshot,
)
from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import (  # noqa: E402
    canonical_json,
    write_canonical_json,
)
from backend.data_pipeline.research.rrc25_country_outage.full_window_selection import (  # noqa: E402
    validate_complete_selection_against_profile,
)
from backend.data_pipeline.research.rrc25_country_outage.input_resolver import (  # noqa: E402
    resolve_research_inputs,
)
from backend.data_pipeline.research.rrc25_country_outage.profile import (  # noqa: E402
    profile_sha256,
    validate_research_profile,
)
from dev.data_quality.rrc25_iran_bounded_pilot import (  # noqa: E402
    build_code_identity,
)
from dev.data_quality.rrc25_iran_full_window import (  # noqa: E402
    DEFAULT_MAX_SPOOL_BYTES,
    MAX_TEMPORARY_BYTES,
    _selection_updates,
    _validate_generated_parser_attestation,
    _wait_for_group_after_term,
    _worker_environment,
    _signal_process_tree,
    _supervisor_capability,
    _verified_internal_worker_argv,
)


UTC = timezone.utc
PREPARATION_SCHEMA_VERSION = "rrc25-iran-execution-preparation/v1"
PREPARATION_FINGERPRINT_SCHEMA = "rrc25_iran_execution_preparation_v1"
PARSER_CONTRACT_SCHEMA_VERSION = "rrc25-full-window-parser-attestation/v1"
PROBE_ATTEMPT_SCHEMA_VERSION = "rrc25-native-probe-attempt/v1"
PROBE_ATTEMPT_FINGERPRINT_SCHEMA = "rrc25_native_probe_attempt_v1"
PROBE_OUTCOME_SCHEMA_VERSION = "rrc25-native-probe-outcome/v1"
PROBE_OUTCOME_FINGERPRINT_SCHEMA = "rrc25_native_probe_outcome_v1"
PROBE_GENESIS_SCHEMA_VERSION = "rrc25-native-probe-raw-genesis/v1"
PROBE_GENESIS_FINGERPRINT_SCHEMA = "rrc25_native_probe_raw_genesis_v1"
PROBE_ACCOUNTING_SCHEMA_VERSION = "rrc25-native-probe-terminal-accounting/v1"
PROBE_ACCOUNTING_FINGERPRINT_SCHEMA = (
    "rrc25_native_probe_terminal_accounting_v1"
)
SEED_RAW_ATTEMPT_SCHEMA_VERSION = "rrc25-seed-raw-attempt/v1"
SEED_RAW_ATTEMPT_FINGERPRINT_SCHEMA = "rrc25_seed_raw_attempt_v1"
SEED_RAW_OUTCOME_SCHEMA_VERSION = "rrc25-seed-raw-outcome/v1"
SEED_RAW_OUTCOME_FINGERPRINT_SCHEMA = "rrc25_seed_raw_outcome_v1"
SEED_RAW_RESERVATION_SCHEMA_VERSION = "rrc25-seed-raw-reservation/v1"
SEED_RAW_RESERVATION_FINGERPRINT_SCHEMA = "rrc25_seed_raw_reservation_v1"
PREEXISTING_ACCOUNTING_SCHEMA_VERSION = (
    "rrc25-preexisting-raw-accounting-import/v2"
)
PREEXISTING_ACCOUNTING_FINGERPRINT_SCHEMA = (
    "rrc25_preexisting_raw_accounting_import_v2"
)
PRIOR_ACCOUNTING_DERIVATION_SCHEMA_VERSION = (
    "rrc25-prior-raw-accounting-derivation/v1"
)
FULL_FLOW_RAW_PROJECTION_SCHEMA_VERSION = "rrc25-full-flow-raw-projection/v1"
FULL_FLOW_RAW_PROJECTION_FINGERPRINT_SCHEMA = (
    "rrc25_full_flow_raw_projection_v1"
)
PROBE_THROUGHPUT_SCHEMA_VERSION = "rrc25-native-probe-throughput/v1"
PROBE_THROUGHPUT_FINGERPRINT_SCHEMA = "rrc25_native_probe_throughput_v1"
RAW_LIMIT_BYTES = 50_000_000_000
PROBE_MAX_ARTIFACTS = 5
PROBE_DEFAULT_SOFT_SECONDS = 120.0
PROBE_HARD_SECONDS = 600.0
PROBE_RUNTIME_CHECK_RECORDS = 256
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_RELATIVE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,255}$")
_PREPARED_FILES = (
    "research-profile.json",
    "mrt-artifact-manifest.json",
    "manifest-verification.json",
    "full-selection.json",
    "compatible-mapping.json",
    "revised-mapping.json",
    "seed-spool-attestation.json",
    "source-fact.json",
    "incident-policy.json",
    "claim-inventory.json",
    "code-identity.json",
    "full-window-bindings.json",
    "native-parser-contract.json",
    "full-flow-raw-projection.json",
)
_PROBE_LEDGER_RELATIVE = PurePosixPath("probe-ledger")
_PROBE_GENESIS_RELATIVE = _PROBE_LEDGER_RELATIVE / "GENESIS.json"
_PROBE_IMPORTED_PRIOR_RELATIVE = (
    _PROBE_LEDGER_RELATIVE / "PRIOR-ACCOUNTING-IMPORT.json"
)
_PROBE_LOCK_RELATIVE = _PROBE_LEDGER_RELATIVE / "LOCK"
_SEED_EXECUTION_LOCK_RELATIVE = _PROBE_LEDGER_RELATIVE / "SEED-EXECUTION.LOCK"
_PROBE_ATTEMPTS_RELATIVE = _PROBE_LEDGER_RELATIVE / "attempts"
_PROBE_OUTCOMES_RELATIVE = _PROBE_LEDGER_RELATIVE / "outcomes"
_SEED_RAW_ATTEMPTS_RELATIVE = _PROBE_LEDGER_RELATIVE / "seed-attempts"
_SEED_RAW_OUTCOMES_RELATIVE = _PROBE_LEDGER_RELATIVE / "seed-outcomes"
_PROBE_THROUGHPUT_RELATIVE = _PROBE_LEDGER_RELATIVE / "throughput"
_PREEXISTING_ACCOUNTING_REQUIRED_STUDY_IDS = frozenset(
    {"iran-rrc25-country-outage-202602-v1"}
)


class ExecutionPrepError(ValueError):
    """执行准备、探针账本或安全边界未闭合。"""


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ExecutionPrepError(f"{field} 必须是 64 位小写 SHA256")
    return value


def _fingerprinted(
    schema_version: str, fingerprint_schema: str, semantic: Mapping[str, Any]
) -> dict[str, Any]:
    payload = {"schema_version": schema_version, **dict(semantic)}
    payload["receipt_fingerprint_sha256"] = hashlib.sha256(
        canonical_json(
            {"schema": fingerprint_schema, "receipt": payload}
        ).encode("utf-8")
    ).hexdigest()
    return payload


def _hash_regular(path: Path, *, maximum_bytes: int) -> tuple[str, int]:
    try:
        initial = path.lstat()
    except OSError as error:
        raise ExecutionPrepError(f"冻结文件不可读：{path}") from error
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise ExecutionPrepError(f"冻结文件必须是非符号链接普通文件：{path}")
    if initial.st_size >= maximum_bytes:
        raise ExecutionPrepError(f"冻结文件达到大小边界：{path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    total = 0
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    try:
        before = os.fstat(descriptor)
        if any(getattr(initial, name) != getattr(before, name) for name in fields):
            raise ExecutionPrepError("冻结文件在打开前发生变化")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total >= maximum_bytes:
                raise ExecutionPrepError("冻结文件读取达到大小边界")
            digest.update(block)
        after = os.fstat(descriptor)
        if any(getattr(before, name) != getattr(after, name) for name in fields):
            raise ExecutionPrepError("冻结文件在读取期间发生变化")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), total


def _safe_directory(path_value: str | Path, field: str) -> Path:
    path = Path(path_value).expanduser().resolve(strict=True)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ExecutionPrepError(f"{field} 必须是非符号链接目录")
    return path


def _assert_disjoint(candidate: Path, other: Path, message: str) -> None:
    if candidate == other or candidate in other.parents or other in candidate.parents:
        raise ExecutionPrepError(message)


def _assert_mutation_target_allowed(candidate: Path, field: str) -> None:
    """所有 create-only 目标都必须避开代码、旧项目与生产根。"""

    _assert_disjoint(
        candidate,
        REPOSITORY_ROOT.resolve(strict=True),
        f"{field} 不得与代码仓库重叠",
    )
    for value in (*DEFAULT_PROTECTED_ROOTS, *DEFAULT_PRODUCTION_ROOTS):
        _assert_disjoint(
            candidate,
            Path(value).resolve(strict=False),
            f"{field} 不得与受保护旧项目或生产路径重叠",
        )


def _new_output_root(path_value: str | Path, *, raw_root: Path) -> Path:
    target = Path(path_value).expanduser().resolve(strict=False)
    parent = target.parent.resolve(strict=True)
    parent_meta = parent.lstat()
    if stat.S_ISLNK(parent_meta.st_mode) or not stat.S_ISDIR(parent_meta.st_mode):
        raise ExecutionPrepError("输出父目录必须是非符号链接目录")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{target}")
    _assert_disjoint(target, raw_root, "输出目录不得与 raw_root 重叠")
    _assert_mutation_target_allowed(target, "输出目录")
    target.mkdir(mode=0o750)
    return target


def _resolver_profile(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "study_id": profile["study_id"],
        "collector_id": profile["collector_id"],
        "country_code": profile["country_code"],
        "window": {
            "start_utc": profile["window"]["start_utc"],
            "end_exclusive_utc": profile["window"]["end_exclusive_utc"],
            "granularity_seconds": 300,
        },
    }


def _native_factory(
    raw_root: Path,
    artifacts: Sequence[Mapping[str, Any]],
    *,
    window: Mapping[str, Any],
    max_spool_bytes: int,
    max_frame_bytes: int,
) -> Any:
    total = sum(int(row["size_bytes"]) for row in artifacts)
    return make_native_update_record_stream_factory(
        raw_root,
        tuple(artifacts),
        data_profile={
            "window_start_utc": window["start_utc"],
            "window_end_exclusive_utc": window["end_exclusive_utc"],
        },
        pilot_limits={
            "max_artifact_count": len(artifacts),
            "max_compressed_bytes": total,
            "max_physical_records": PILOT_ABSOLUTE_MAX_PHYSICAL_RECORDS,
            "max_route_events": PILOT_ABSOLUTE_MAX_ROUTE_EVENTS,
            "max_spool_bytes": max_spool_bytes,
        },
        max_frame_bytes=max_frame_bytes,
    )


def _parser_contract(attestation: Mapping[str, Any]) -> Mapping[str, Any]:
    semantic = {
        "schema_version": PARSER_CONTRACT_SCHEMA_VERSION,
        "backend": "native",
        "parser_name": attestation.get("parser_name"),
        "parser_version": attestation.get("parser_version"),
        "binary_sha256": attestation.get("parser_binary_sha256"),
        "binary_execution_policy": attestation.get("binary_execution_policy"),
        "adapter_source_sha256": attestation.get("adapter_source_sha256"),
    }
    for field in ("binary_sha256", "adapter_source_sha256"):
        _sha(semantic[field], f"parser_contract.{field}")
    if (
        semantic["parser_name"] != NATIVE_UPDATE_PARSER_NAME
        or semantic["parser_version"] != NATIVE_UPDATE_PARSER_VERSION
        or semantic["binary_execution_policy"] != NATIVE_UPDATE_EXECUTION_POLICY
    ):
        raise ExecutionPrepError("Native parser runtime attestation 与冻结常量不一致")
    return {
        **semantic,
        "semantic_fingerprint_sha256": hashlib.sha256(
            canonical_json(semantic).encode("utf-8")
        ).hexdigest(),
    }


def _published_ref(root: Path, published: Any) -> Mapping[str, Any]:
    return {
        "path": published.path.relative_to(root).as_posix(),
        "sha256": published.sha256,
        "size_bytes": published.size_bytes,
    }


def _json_pointer_value(payload: Any, pointer: Any, field: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ExecutionPrepError(f"{field}.json_pointer 必须是非空 JSON Pointer")
    current = payload
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if part not in current:
                raise ExecutionPrepError(f"{field} 指向的来源字段不存在")
            current = current[part]
        elif isinstance(current, list):
            if not part.isdigit() or int(part) >= len(current):
                raise ExecutionPrepError(f"{field} 指向的来源数组位置不存在")
            current = current[int(part)]
        else:
            raise ExecutionPrepError(f"{field} 在标量处仍有未解析路径")
    return current


def _json_pointer_integer(payload: Any, pointer: Any, field: str) -> int:
    current = _json_pointer_value(payload, pointer, field)
    if isinstance(current, bool) or not isinstance(current, int) or current < 0:
        raise ExecutionPrepError(f"{field} 必须从来源 JSON 导出非负整数")
    return current


def _load_derivation_source_json(ref: Mapping[str, Any], field: str) -> Any:
    path = Path(str(ref["path"])).expanduser().resolve(strict=True)
    raw = path.read_bytes()
    if (
        hashlib.sha256(raw).hexdigest() != ref["sha256"]
        or len(raw) != ref["size_bytes"]
    ):
        raise ExecutionPrepError(f"{field} 来源 JSON 的 SHA/size 漂移")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExecutionPrepError(f"{field} 来源不是合法 JSON") from error


def _derive_prior_bound(
    value: Any,
    *,
    source_refs: Sequence[Mapping[str, Any]],
    field: str,
    require_attempt_provenance: bool = False,
) -> tuple[int, Mapping[str, Any]]:
    required_rule_fields = (
        {"operation", "terms", "conservative_attempt_count"}
        if require_attempt_provenance
        else {"operation", "terms"}
    )
    if (
        not isinstance(value, Mapping)
        or set(value) != required_rule_fields
        or value.get("operation") != "sum_source_json_integer_terms"
        or not isinstance(value.get("terms"), list)
        or not value["terms"]
    ):
        raise ExecutionPrepError(f"{field} 推导规则字段不闭合")
    refs_by_identity = {
        (str(ref["path"]), str(ref["sha256"])): ref for ref in source_refs
    }
    normalized_terms: list[Mapping[str, Any]] = []
    total = 0
    labels: set[str] = set()
    attempt_ids: set[str] = set()
    for index, term in enumerate(value["terms"]):
        term_field = f"{field}.terms[{index}]"
        base_term_fields = {
            "label",
            "source_path",
            "source_sha256",
            "json_pointer",
            "multiplier",
        }
        attempt_term_fields = {
            "attempt_id",
            "artifact_id_json_pointer",
            "artifact_file_sha256_json_pointer",
            "provenance_path",
            "provenance_sha256",
        }
        expected_term_fields = (
            base_term_fields | attempt_term_fields
            if require_attempt_provenance
            else base_term_fields
        )
        if not isinstance(term, Mapping) or set(term) != expected_term_fields:
            raise ExecutionPrepError(f"{term_field} 字段不闭合")
        label = term.get("label")
        multiplier = term.get("multiplier")
        if (
            not isinstance(label, str)
            or not label
            or label in labels
            or isinstance(multiplier, bool)
            or not isinstance(multiplier, int)
            or not 1 <= multiplier <= 32
            or (require_attempt_provenance and multiplier != 1)
        ):
            raise ExecutionPrepError(f"{term_field} label/multiplier 非法")
        labels.add(label)
        source_path = Path(str(term.get("source_path"))).expanduser().resolve(
            strict=True
        )
        source_sha = _sha(term.get("source_sha256"), f"{term_field}.source_sha256")
        ref = refs_by_identity.get((str(source_path), source_sha))
        if ref is None:
            raise ExecutionPrepError(f"{term_field} 未绑定 source_metadata_refs")
        source_payload = _load_derivation_source_json(ref, term_field)
        observed = _json_pointer_integer(
            source_payload, term.get("json_pointer"), term_field
        )
        attempt_proof: Mapping[str, Any] = {}
        if require_attempt_provenance:
            attempt_id = term.get("attempt_id")
            if (
                not isinstance(attempt_id, str)
                or not attempt_id
                or attempt_id in attempt_ids
            ):
                raise ExecutionPrepError(f"{term_field}.attempt_id 非法或重复")
            attempt_ids.add(attempt_id)
            artifact_id_pointer = term.get("artifact_id_json_pointer")
            artifact_sha_pointer = term.get("artifact_file_sha256_json_pointer")
            artifact_id = _json_pointer_value(
                source_payload, artifact_id_pointer, term_field
            )
            artifact_sha = _json_pointer_value(
                source_payload, artifact_sha_pointer, term_field
            )
            if (
                not isinstance(artifact_id, str)
                or not isinstance(artifact_sha, str)
                or _SHA256_RE.fullmatch(artifact_sha) is None
                or artifact_id != artifact_id_v1(artifact_sha)
            ):
                raise ExecutionPrepError(f"{term_field} artifact id/SHA 不闭合")
            provenance_path = Path(
                str(term.get("provenance_path"))
            ).expanduser().resolve(strict=True)
            provenance_sha = _sha(
                term.get("provenance_sha256"),
                f"{term_field}.provenance_sha256",
            )
            provenance_ref = refs_by_identity.get(
                (str(provenance_path), provenance_sha)
            )
            if provenance_ref is None:
                raise ExecutionPrepError(
                    f"{term_field} attempt provenance 未绑定 source_metadata_refs"
                )
            attempt_proof = {
                "attempt_id": attempt_id,
                "artifact_id": artifact_id,
                "artifact_file_sha256": artifact_sha,
                "provenance_ref": dict(provenance_ref),
            }
        contribution = observed * multiplier
        if contribution >= RAW_LIMIT_BYTES or total + contribution >= RAW_LIMIT_BYTES:
            raise ExecutionPrepError(f"{field} 推导结果达到 50GB 排他边界")
        total += contribution
        normalized_terms.append(
            {
                **dict(term),
                "source_path": str(source_path),
                "observed_integer": observed,
                "derived_contribution_bytes": contribution,
                **attempt_proof,
            }
        )
    if require_attempt_provenance and (
        value.get("conservative_attempt_count") != len(normalized_terms)
        or len(normalized_terms) != 3
    ):
        raise ExecutionPrepError(
            f"{field} 必须显式证明恰好 3 个保守历史 attempt"
        )
    return (
        total,
        {
            "operation": "sum_source_json_integer_terms",
            **(
                {"conservative_attempt_count": len(normalized_terms)}
                if require_attempt_provenance
                else {}
            ),
            "terms": normalized_terms,
            "derived_bytes": total,
        },
    )


def _derive_prior_bounds(
    evidence: Any,
    *,
    source_refs: Sequence[Mapping[str, Any]],
    evidence_ref: Mapping[str, Any],
) -> tuple[int, int, Mapping[str, Any]]:
    if not isinstance(evidence, Mapping) or set(evidence) != {
        "schema_version",
        "observed_lower_bound",
        "reserved_upper_bound",
        "upper_bound_semantics",
    }:
        raise ExecutionPrepError("prior accounting derivation evidence 字段不闭合")
    if (
        evidence.get("schema_version")
        != PRIOR_ACCOUNTING_DERIVATION_SCHEMA_VERSION
        or evidence.get("upper_bound_semantics")
        != "conservative_no_refund_pre_goal_reads"
    ):
        raise ExecutionPrepError("prior accounting derivation evidence 语义不受支持")
    lower, lower_proof = _derive_prior_bound(
        evidence["observed_lower_bound"],
        source_refs=source_refs,
        field="observed_lower_bound",
    )
    upper, upper_proof = _derive_prior_bound(
        evidence["reserved_upper_bound"],
        source_refs=source_refs,
        field="reserved_upper_bound",
        require_attempt_provenance=True,
    )
    if not 0 <= lower <= upper < RAW_LIMIT_BYTES or upper <= 0:
        raise ExecutionPrepError("来源证据推导的 prior lower/upper 不闭合")
    return (
        lower,
        upper,
        {
            "schema_version": PRIOR_ACCOUNTING_DERIVATION_SCHEMA_VERSION,
            "evidence_ref": dict(evidence_ref),
            "observed_lower_bound_proof": lower_proof,
            "reserved_upper_bound_proof": upper_proof,
            "upper_bound_semantics": evidence["upper_bound_semantics"],
            "proof_fingerprint_sha256": hashlib.sha256(
                canonical_json(
                    {
                        "schema": "rrc25_prior_raw_accounting_derivation_proof_v1",
                        "evidence_ref": dict(evidence_ref),
                        "lower": lower_proof,
                        "upper": upper_proof,
                    }
                ).encode("utf-8")
            ).hexdigest(),
        },
    )


def _prior_accounting_for_prepare(
    args: argparse.Namespace,
    *,
    study_id: str | None = None,
) -> tuple[Mapping[str, Any], Mapping[str, Any] | None]:
    receipt_value = getattr(args, "prior_accounting_receipt", None)
    expected_sha = getattr(args, "prior_accounting_receipt_sha256", None)
    zero = bool(getattr(args, "new_task_zero_genesis", False))
    if zero:
        if study_id in _PREEXISTING_ACCOUNTING_REQUIRED_STUDY_IDS:
            raise ExecutionPrepError(
                f"Study {study_id} 已有冻结历史 raw 读取，禁止 zero genesis"
            )
        if receipt_value is not None or expected_sha is not None:
            raise ExecutionPrepError(
                "new-task-zero-genesis 与 prior accounting import 不得同时使用"
            )
        return (
            {
                "kind": "explicit_new_task_zero_genesis",
                "accounting_state": "exact",
                "observed_lower_bound_new_raw_bytes": 0,
                "reserved_upper_bound_new_raw_bytes": 0,
                "cumulative_reserved_new_raw_bytes": 0,
                "semantics": "no_preexisting_raw_reads_for_new_task",
            },
            None,
        )
    if receipt_value is None or expected_sha is None:
        raise ExecutionPrepError(
            "已有研究必须同时提供 prior-accounting-receipt 与 expected SHA；"
            "只有全新无历史任务可显式使用 new-task-zero-genesis"
        )
    expected = _sha(expected_sha, "prior-accounting-receipt-sha256")
    path = Path(receipt_value).expanduser().resolve(strict=True)
    observed_sha, _size = _hash_regular(path, maximum_bytes=16 * 1024 * 1024)
    if observed_sha != expected:
        raise ExecutionPrepError("prior accounting receipt 文件 SHA256 不一致")
    receipt = _load_receipt(
        path,
        schema=PREEXISTING_ACCOUNTING_SCHEMA_VERSION,
        fingerprint_schema=PREEXISTING_ACCOUNTING_FINGERPRINT_SCHEMA,
    )
    required = {
        "schema_version",
        "accounting_state",
        "observed_lower_bound_new_raw_bytes",
        "reserved_upper_bound_new_raw_bytes",
        "source_run_path",
        "source_metadata_refs",
        "derivation",
        "codex_task_id",
        "frozen_at_utc",
        "history_limitation_zh",
        "semantics",
        "database_write_operations",
        "receipt_fingerprint_sha256",
    }
    lower = receipt.get("observed_lower_bound_new_raw_bytes")
    upper = receipt.get("reserved_upper_bound_new_raw_bytes")
    refs = receipt.get("source_metadata_refs")
    if (
        set(receipt) != required
        or receipt.get("accounting_state")
        not in {"exact", "conservative_upper_bound"}
        or isinstance(lower, bool)
        or not isinstance(lower, int)
        or isinstance(upper, bool)
        or not isinstance(upper, int)
        or not 0 <= lower <= upper < RAW_LIMIT_BYTES
        or upper <= 0
        or (
            receipt.get("accounting_state") == "exact" and lower != upper
        )
        or receipt.get("semantics") != "conservative_no_refund_pre_goal_reads"
        or receipt.get("database_write_operations") != 0
        or not isinstance(refs, list)
        or not refs
        or not isinstance(receipt.get("codex_task_id"), str)
        or not receipt["codex_task_id"].strip()
        or not isinstance(receipt.get("history_limitation_zh"), str)
        or "不能" not in receipt["history_limitation_zh"]
    ):
        raise ExecutionPrepError("prior accounting import receipt 字段或语义不闭合")
    try:
        frozen_at = datetime.fromisoformat(
            str(receipt.get("frozen_at_utc")).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ExecutionPrepError("prior accounting frozen_at_utc 非法") from error
    if frozen_at.tzinfo is None or frozen_at.utcoffset() != timedelta(0):
        raise ExecutionPrepError("prior accounting frozen_at_utc 必须是 UTC")
    _safe_directory(receipt.get("source_run_path"), "source_run_path")
    # 来源说明可同时绑定 run metadata 与仓库内中文冻结说明，不要求同一父目录。
    normalized_refs = []
    for index, ref in enumerate(refs):
        if not isinstance(ref, Mapping) or set(ref) != {
            "path",
            "sha256",
            "size_bytes",
        }:
            raise ExecutionPrepError(
                f"prior source_metadata_refs[{index}] 字段不闭合"
            )
        source_path = Path(str(ref.get("path"))).expanduser().resolve(strict=True)
        observed, size = _hash_regular(source_path, maximum_bytes=512 * 1024 * 1024)
        if observed != ref.get("sha256") or size != ref.get("size_bytes"):
            raise ExecutionPrepError("prior metadata ref SHA/size 与来源文件不一致")
        normalized_refs.append(dict(ref))
    if normalized_refs != sorted(
        normalized_refs, key=lambda row: (str(row["path"]), str(row["sha256"]))
    ) or len({row["path"] for row in normalized_refs}) != len(normalized_refs):
        raise ExecutionPrepError("prior metadata refs 必须按 path/SHA 排序且路径唯一")
    derivation = receipt.get("derivation")
    evidence_ref = (
        derivation.get("evidence_ref") if isinstance(derivation, Mapping) else None
    )
    if (
        not isinstance(evidence_ref, Mapping)
        or dict(evidence_ref) not in normalized_refs
    ):
        raise ExecutionPrepError("prior derivation evidence 未绑定 source metadata ref")
    evidence = _load_derivation_source_json(
        evidence_ref, "prior derivation evidence"
    )
    derived_lower, derived_upper, expected_derivation = _derive_prior_bounds(
        evidence,
        source_refs=normalized_refs,
        evidence_ref=evidence_ref,
    )
    if (
        derived_lower != lower
        or derived_upper != upper
        or derivation != expected_derivation
    ):
        raise ExecutionPrepError("prior lower/upper 与冻结来源推导证明不一致")
    return (
        {
            "kind": "imported_preexisting_accounting",
            "accounting_state": receipt["accounting_state"],
            "observed_lower_bound_new_raw_bytes": lower,
            "reserved_upper_bound_new_raw_bytes": upper,
            # 所有后续 admission/cumulative 均从 conservative upper 起算，
            # 不能把该值描述成 measured exact。
            "cumulative_reserved_new_raw_bytes": upper,
            "semantics": receipt["semantics"],
            "history_limitation_zh": receipt["history_limitation_zh"],
            "codex_task_id": receipt["codex_task_id"],
            "frozen_at_utc": receipt["frozen_at_utc"],
            "source_receipt_original_path": str(path),
            "source_receipt_file_sha256": observed_sha,
        },
        receipt,
    )


def _freeze_prior_receipt_output(
    directory_value: str | Path, *, receipt_fingerprint_sha256: str
) -> Path:
    """在安全目录中按内容指纹生成 create-only receipt 目标。"""

    supplied = Path(directory_value).expanduser()
    try:
        parent_metadata = supplied.lstat()
    except OSError as error:
        raise ExecutionPrepError("prior receipt 输出父目录不可读") from error
    if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(
        parent_metadata.st_mode
    ):
        raise ExecutionPrepError("prior receipt 输出父目录必须是非符号链接目录")
    parent = supplied.resolve(strict=True)
    _assert_mutation_target_allowed(parent, "prior receipt 输出目录")
    fingerprint = _sha(
        receipt_fingerprint_sha256, "prior receipt fingerprint"
    )
    target = parent / f"prior-accounting-{fingerprint}.json"
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"prior accounting receipt 已存在，拒绝覆盖：{target}")
    return target


def _explicit_source_directory(path_value: str | Path) -> Path:
    supplied = Path(path_value).expanduser()
    try:
        metadata = supplied.lstat()
    except OSError as error:
        raise ExecutionPrepError("source_run_path 不可读") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ExecutionPrepError("source_run_path 必须是非符号链接目录")
    return supplied.resolve(strict=True)


def _explicit_source_metadata_ref(
    path_value: str | Path, expected_sha256: str
) -> Mapping[str, Any]:
    supplied = Path(path_value).expanduser()
    try:
        metadata = supplied.lstat()
    except OSError as error:
        raise ExecutionPrepError(f"prior metadata source 不可读：{supplied}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ExecutionPrepError("prior metadata source 必须是非符号链接普通文件")
    expected = _sha(expected_sha256, "source-metadata-ref-sha256")
    path = supplied.resolve(strict=True)
    observed, size = _hash_regular(path, maximum_bytes=512 * 1024 * 1024)
    if observed != expected:
        raise ExecutionPrepError(f"prior metadata source SHA256 漂移：{path}")
    return {"path": str(path), "sha256": observed, "size_bytes": size}


def _run_freeze_prior_accounting(args: argparse.Namespace) -> Mapping[str, Any]:
    """从已存在的只读证据冻结 pre-ledger conservative receipt。"""

    task_id = str(args.codex_task_id).strip()
    if not task_id:
        raise ExecutionPrepError("codex-task-id 不得为空")
    limitation = str(args.history_limitation_zh).strip()
    if "不能" not in limitation:
        raise ExecutionPrepError("history-limitation-zh 必须明确包含‘不能’")
    frozen_text = str(args.frozen_at_utc)
    try:
        frozen_at = datetime.fromisoformat(frozen_text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ExecutionPrepError("frozen-at-utc 不是合法 UTC 时间") from error
    if frozen_at.tzinfo is None or frozen_at.utcoffset() != timedelta(0):
        raise ExecutionPrepError("frozen-at-utc 必须是 UTC")
    normalized_frozen_at = frozen_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if frozen_text != normalized_frozen_at:
        raise ExecutionPrepError("frozen-at-utc 必须使用 YYYY-MM-DDTHH:MM:SSZ 规范形式")

    paths = tuple(args.source_metadata_ref)
    expected_hashes = tuple(args.source_metadata_ref_sha256)
    if not paths or len(paths) != len(expected_hashes):
        raise ExecutionPrepError(
            "source-metadata-ref 与 source-metadata-ref-sha256 必须成对且非空"
        )
    source_run = _explicit_source_directory(args.source_run_path)
    refs = [
        _explicit_source_metadata_ref(path, digest)
        for path, digest in zip(paths, expected_hashes)
    ]
    evidence_ref = _explicit_source_metadata_ref(
        args.derivation_evidence,
        args.derivation_evidence_sha256,
    )
    refs.append(evidence_ref)
    refs.sort(key=lambda row: (str(row["path"]), str(row["sha256"])))
    if len({row["path"] for row in refs}) != len(refs):
        raise ExecutionPrepError("source metadata ref 解析后路径必须唯一")
    evidence = _load_derivation_source_json(
        evidence_ref, "prior derivation evidence"
    )
    lower, upper, derivation = _derive_prior_bounds(
        evidence,
        source_refs=refs,
        evidence_ref=evidence_ref,
    )
    receipt = _fingerprinted(
        PREEXISTING_ACCOUNTING_SCHEMA_VERSION,
        PREEXISTING_ACCOUNTING_FINGERPRINT_SCHEMA,
        {
            # pre-ledger 历史不能逐次拆分；即使 lower == upper，该
            # 生成器也不将它升格为 measured exact。
            "accounting_state": "conservative_upper_bound",
            "observed_lower_bound_new_raw_bytes": lower,
            "reserved_upper_bound_new_raw_bytes": upper,
            "source_run_path": str(source_run),
            "source_metadata_refs": refs,
            "derivation": derivation,
            "codex_task_id": task_id,
            "frozen_at_utc": normalized_frozen_at,
            "history_limitation_zh": limitation,
            "semantics": "conservative_no_refund_pre_goal_reads",
            "database_write_operations": 0,
        },
    )
    target = _freeze_prior_receipt_output(
        args.output_directory,
        receipt_fingerprint_sha256=receipt["receipt_fingerprint_sha256"],
    )
    published = write_canonical_json(
        target,
        receipt,
        kind="rrc25_preexisting_raw_accounting_import",
        mode=0o440,
    )
    # 用 prepare 的正式 consumer 立即回读，防止生成器与消费者漂移。
    normalized, loaded = _prior_accounting_for_prepare(
        argparse.Namespace(
            prior_accounting_receipt=str(target),
            prior_accounting_receipt_sha256=published.sha256,
            new_task_zero_genesis=False,
        )
    )
    if loaded != receipt or normalized.get("reserved_upper_bound_new_raw_bytes") != upper:
        raise ExecutionPrepError("prior accounting receipt 发布后回读不闭合")
    return {
        "ok": True,
        "command": "freeze-prior-accounting",
        "receipt_path": str(target),
        "receipt_sha256": published.sha256,
        "receipt_size_bytes": published.size_bytes,
        "accounting_state": "conservative_upper_bound",
        "observed_lower_bound_new_raw_bytes": lower,
        "reserved_upper_bound_new_raw_bytes": upper,
        "source_metadata_ref_count": len(refs),
        "database_connections": 0,
        "database_write_operations": 0,
    }


def _safe_prepared_relative(value: Any, field: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise ExecutionPrepError(f"{field} 必须是安全相对路径")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ExecutionPrepError(f"{field} 必须是安全相对路径")
    return relative


def _regular_ref(root: Path, path: Path) -> Mapping[str, Any]:
    try:
        relative = path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise ExecutionPrepError("probe ledger receipt 越出 prepared_directory") from error
    digest, size = _hash_regular(path, maximum_bytes=16 * 1024 * 1024)
    return {
        "path": relative.as_posix(),
        "sha256": digest,
        "size_bytes": size,
    }


def _load_referenced_probe_receipt(
    root: Path,
    ref: Any,
    *,
    schema: str,
    fingerprint_schema: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if not isinstance(ref, Mapping) or set(ref) != {"path", "sha256", "size_bytes"}:
        raise ExecutionPrepError("probe ledger receipt ref 字段不闭合")
    relative = _safe_prepared_relative(ref.get("path"), "probe receipt ref.path")
    if relative.parts[:1] != _PROBE_LEDGER_RELATIVE.parts:
        raise ExecutionPrepError("probe ledger receipt ref 越出 probe-ledger")
    path = root.joinpath(*relative.parts)
    observed = _regular_ref(root, path)
    if observed != dict(ref):
        raise ExecutionPrepError("probe ledger receipt ref SHA/size 不一致")
    return (
        _load_receipt(
            path,
            schema=schema,
            fingerprint_schema=fingerprint_schema,
        ),
        observed,
    )


def _probe_ledger_paths(root: Path) -> Mapping[str, Path]:
    ledger = root.joinpath(*_PROBE_LEDGER_RELATIVE.parts)
    attempts = root.joinpath(*_PROBE_ATTEMPTS_RELATIVE.parts)
    outcomes = root.joinpath(*_PROBE_OUTCOMES_RELATIVE.parts)
    seed_attempts = root.joinpath(*_SEED_RAW_ATTEMPTS_RELATIVE.parts)
    seed_outcomes = root.joinpath(*_SEED_RAW_OUTCOMES_RELATIVE.parts)
    throughput = root.joinpath(*_PROBE_THROUGHPUT_RELATIVE.parts)
    for path, field in (
        (ledger, "probe-ledger"),
        (attempts, "probe-ledger/attempts"),
        (outcomes, "probe-ledger/outcomes"),
        (seed_attempts, "probe-ledger/seed-attempts"),
        (seed_outcomes, "probe-ledger/seed-outcomes"),
        (throughput, "probe-ledger/throughput"),
    ):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ExecutionPrepError(f"{field} 必须是非符号链接目录")
    lock = root.joinpath(*_PROBE_LOCK_RELATIVE.parts)
    lock_metadata = lock.lstat()
    if stat.S_ISLNK(lock_metadata.st_mode) or not stat.S_ISREG(lock_metadata.st_mode):
        raise ExecutionPrepError("probe ledger LOCK 必须是非符号链接普通文件")
    seed_execution_lock = root.joinpath(*_SEED_EXECUTION_LOCK_RELATIVE.parts)
    seed_lock_metadata = seed_execution_lock.lstat()
    if stat.S_ISLNK(seed_lock_metadata.st_mode) or not stat.S_ISREG(
        seed_lock_metadata.st_mode
    ):
        raise ExecutionPrepError(
            "probe ledger SEED-EXECUTION.LOCK 必须是非符号链接普通文件"
        )
    return {
        "ledger": ledger,
        "attempts": attempts,
        "outcomes": outcomes,
        "seed_attempts": seed_attempts,
        "seed_outcomes": seed_outcomes,
        "throughput": throughput,
        "lock": lock,
        "seed_execution_lock": seed_execution_lock,
        "genesis": root.joinpath(*_PROBE_GENESIS_RELATIVE.parts),
    }


@contextmanager
def _probe_ledger_lock(root: Path, *, blocking: bool = True) -> Any:
    paths = _probe_ledger_paths(root)
    descriptor = os.open(
        paths["lock"], os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError as error:
            raise ExecutionPrepError("probe ledger 当前仍有 active worker") from error
        yield paths
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _probe_receipt_filename(
    *, kind: str, sequence: int, attempt_id: str
) -> str:
    if kind not in {"attempt", "outcome"}:
        raise ExecutionPrepError("probe receipt kind 非法")
    if sequence <= 0 or not re.fullmatch(r"probe_v1_[0-9a-f]{32}", attempt_id):
        raise ExecutionPrepError("probe receipt sequence/attempt_id 非法")
    return f"{kind}-{sequence:06d}-{attempt_id}.json"


def _probe_terminal_accounting(
    root: Path,
    prepared: Mapping[str, Any],
    *,
    supplied_terminal: str | Path | None = None,
    allow_unclosed_tail: bool = False,
) -> Mapping[str, Any]:
    """完整扫描 create-only probe ledger，并返回唯一 terminal 的冻结摘要。"""

    paths = _probe_ledger_paths(root)
    genesis = _load_receipt(
        paths["genesis"],
        schema=PROBE_GENESIS_SCHEMA_VERSION,
        fingerprint_schema=PROBE_GENESIS_FINGERPRINT_SCHEMA,
    )
    genesis_ref = _regular_ref(root, paths["genesis"])
    prepared_receipt = prepared.get("receipt")
    bindings = prepared.get("full-window-bindings.json")
    selection = prepared.get("full-selection.json")
    updates = _selection_updates(selection) if isinstance(selection, Mapping) else ()
    ledger_meta = prepared_receipt.get("probe_raw_ledger") if isinstance(
        prepared_receipt, Mapping
    ) else None
    prior_accounting = genesis.get("prior_accounting")
    if not isinstance(prior_accounting, Mapping):
        raise ExecutionPrepError("probe raw genesis 缺少 prior accounting")
    initial_upper = prior_accounting.get("reserved_upper_bound_new_raw_bytes")
    initial_lower = prior_accounting.get("observed_lower_bound_new_raw_bytes")
    if (
        not isinstance(bindings, Mapping)
        or not isinstance(selection, Mapping)
        or not isinstance(ledger_meta, Mapping)
        or genesis.get("ledger_id") != ledger_meta.get("ledger_id")
        or genesis.get("prepared_bindings") != bindings
        or genesis.get("selection_id") != selection.get("selection_id")
        or isinstance(initial_upper, bool)
        or not isinstance(initial_upper, int)
        or isinstance(initial_lower, bool)
        or not isinstance(initial_lower, int)
        or not 0 <= initial_lower <= initial_upper < RAW_LIMIT_BYTES
        or genesis.get("cumulative_reserved_new_raw_bytes") != initial_upper
        or genesis.get("prior_accounting") != ledger_meta.get("prior_accounting")
        or genesis.get("reservation_refund_policy")
        != "never_refund_even_on_failure_timeout_or_retry"
        or ledger_meta.get("genesis_ref") != genesis_ref
        or ledger_meta.get("initial_cumulative_reserved_new_raw_bytes")
        != initial_upper
        or ledger_meta.get("initial_observed_lower_bound_new_raw_bytes")
        != initial_lower
    ):
        raise ExecutionPrepError("probe raw genesis 与 PREPARATION 不闭合")
    prior_kind = prior_accounting.get("kind")
    imported_ref = prior_accounting.get("imported_receipt_ref")
    if prior_kind == "explicit_new_task_zero_genesis":
        if (
            initial_lower != 0
            or initial_upper != 0
            or imported_ref is not None
            or prior_accounting.get("accounting_state") != "exact"
            or prior_accounting.get("semantics")
            != "no_preexisting_raw_reads_for_new_task"
        ):
            raise ExecutionPrepError("显式 zero genesis prior accounting 非法")
    elif prior_kind == "imported_preexisting_accounting":
        imported, imported_observed_ref = _load_referenced_probe_receipt(
            root,
            imported_ref,
            schema=PREEXISTING_ACCOUNTING_SCHEMA_VERSION,
            fingerprint_schema=PREEXISTING_ACCOUNTING_FINGERPRINT_SCHEMA,
        )
        if (
            imported_observed_ref != imported_ref
            or prior_accounting.get("source_receipt_file_sha256")
            != imported_observed_ref.get("sha256")
            or imported.get("accounting_state")
            != prior_accounting.get("accounting_state")
            or imported.get("observed_lower_bound_new_raw_bytes") != initial_lower
            or imported.get("reserved_upper_bound_new_raw_bytes") != initial_upper
            or imported.get("semantics") != prior_accounting.get("semantics")
            or imported.get("codex_task_id")
            != prior_accounting.get("codex_task_id")
            or imported.get("frozen_at_utc")
            != prior_accounting.get("frozen_at_utc")
        ):
            raise ExecutionPrepError("imported prior accounting 与 genesis 不闭合")
    else:
        raise ExecutionPrepError("probe raw genesis prior accounting kind 非法")

    attempt_paths = tuple(sorted(paths["attempts"].glob("attempt-*.json")))
    outcome_paths = tuple(sorted(paths["outcomes"].glob("outcome-*.json")))
    outcomes_by_attempt: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for path in outcome_paths:
        outcome = _load_receipt(
            path,
            schema=PROBE_OUTCOME_SCHEMA_VERSION,
            fingerprint_schema=PROBE_OUTCOME_FINGERPRINT_SCHEMA,
        )
        attempt_id = outcome.get("attempt_id")
        if not isinstance(attempt_id, str) or attempt_id in outcomes_by_attempt:
            raise ExecutionPrepError("probe outcome attempt_id 非法或重复")
        outcomes_by_attempt[attempt_id] = (outcome, _regular_ref(root, path))

    terminal_ref: Mapping[str, Any] = genesis_ref
    cumulative = initial_upper
    observed_probe_lower = 0
    observed_probe_upper = 0
    chain_refs: list[Mapping[str, Any]] = [genesis_ref]
    unclosed: tuple[Mapping[str, Any], Mapping[str, Any]] | None = None
    for expected_sequence, path in enumerate(attempt_paths, start=1):
        attempt = _load_receipt(
            path,
            schema=PROBE_ATTEMPT_SCHEMA_VERSION,
            fingerprint_schema=PROBE_ATTEMPT_FINGERPRINT_SCHEMA,
        )
        attempt_ref = _regular_ref(root, path)
        attempt_id = attempt.get("attempt_id")
        expected_name = (
            _probe_receipt_filename(
                kind="attempt",
                sequence=expected_sequence,
                attempt_id=str(attempt_id),
            )
            if isinstance(attempt_id, str)
            else ""
        )
        artifacts = attempt.get("artifacts")
        artifact_indices = attempt.get("artifact_indices")
        expected_artifacts: list[Mapping[str, Any]] = []
        valid_artifact_indices = isinstance(artifact_indices, list) and all(
            isinstance(index, int) and not isinstance(index, bool)
            for index in artifact_indices
        )
        if valid_artifact_indices:
            try:
                expected_artifacts = [updates[index] for index in artifact_indices]
            except IndexError:
                expected_artifacts = []
        if (
            path.name != expected_name
            or attempt.get("sequence") != expected_sequence
            or attempt.get("ledger_id") != genesis.get("ledger_id")
            or attempt.get("prepared_bindings") != bindings
            or attempt.get("selection_id") != selection.get("selection_id")
            or attempt.get("previous_terminal_ref") != terminal_ref
            or attempt.get("prior_new_raw_bytes") != cumulative
            or not isinstance(artifacts, list)
            or not artifacts
            or len(artifacts) > PROBE_MAX_ARTIFACTS
            or not valid_artifact_indices
            or artifact_indices != sorted(set(artifact_indices))
            or artifacts != expected_artifacts
        ):
            raise ExecutionPrepError("probe attempt 链、身份或 artifact 人口不闭合")
        reserved = sum(
            int(row.get("size_bytes", -1))
            for row in artifacts
            if isinstance(row, Mapping)
        )
        if (
            len(artifacts)
            != sum(isinstance(row, Mapping) for row in artifacts)
            or reserved <= 0
            or attempt.get("reserved_new_raw_bytes") != reserved
            or attempt.get("cumulative_reserved_new_raw_bytes")
            != cumulative + reserved
            or cumulative + reserved >= RAW_LIMIT_BYTES
        ):
            raise ExecutionPrepError("probe attempt reservation/cumulative 非法")
        cumulative += reserved
        chain_refs.append(attempt_ref)
        outcome_pair = outcomes_by_attempt.pop(str(attempt_id), None)
        if outcome_pair is None:
            if expected_sequence != len(attempt_paths) or not allow_unclosed_tail:
                raise ExecutionPrepError("probe ledger 存在未闭合 attempt")
            unclosed = (attempt, attempt_ref)
            terminal_ref = attempt_ref
            continue
        outcome, outcome_ref = outcome_pair
        expected_outcome_name = _probe_receipt_filename(
            kind="outcome", sequence=expected_sequence, attempt_id=str(attempt_id)
        )
        observed_state = outcome.get("observed_compressed_bytes_state")
        observed_exact = outcome.get("observed_compressed_bytes_sum")
        observed_lower = outcome.get("observed_compressed_bytes_lower_bound_sum")
        observed_upper = outcome.get("observed_compressed_bytes_upper_bound_sum")
        if (
            Path(str(outcome_ref["path"])).name != expected_outcome_name
            or outcome.get("sequence") != expected_sequence
            or outcome.get("ledger_id") != genesis.get("ledger_id")
            or outcome.get("attempt_ref") != attempt_ref
            or outcome.get("cumulative_reserved_new_raw_bytes") != cumulative
            or outcome.get("next_seed_prior_new_raw_bytes") != cumulative
            or outcome.get("outcome")
            not in {
                "complete_single_pass",
                "failed_or_stopped_reservation_not_refunded",
            }
            or observed_state not in {"exact", "bounded_after_process_termination"}
            or isinstance(observed_lower, bool)
            or not isinstance(observed_lower, int)
            or isinstance(observed_upper, bool)
            or not isinstance(observed_upper, int)
            or not 0 <= observed_lower <= observed_upper <= reserved
            or (
                observed_state == "exact"
                and (observed_exact != observed_lower or observed_exact != observed_upper)
            )
            or (observed_state != "exact" and observed_exact is not None)
        ):
            raise ExecutionPrepError("probe outcome 与 attempt/reservation 不闭合")
        terminal_ref = outcome_ref
        observed_probe_lower += observed_lower
        observed_probe_upper += observed_upper
        chain_refs.append(outcome_ref)
    if outcomes_by_attempt:
        raise ExecutionPrepError("probe outcome 没有对应 attempt")

    if supplied_terminal is not None:
        supplied = Path(supplied_terminal).expanduser().resolve(strict=True)
        expected_terminal = root.joinpath(
            *_safe_prepared_relative(
                terminal_ref["path"], "terminal_receipt_ref.path"
            ).parts
        ).resolve(strict=True)
        if supplied != expected_terminal or _regular_ref(root, supplied) != terminal_ref:
            raise ExecutionPrepError("指定 terminal receipt 不是 probe ledger 唯一终点")
    semantic = {
        "ledger_id": genesis["ledger_id"],
        "prepared_directory": str(root),
        "prepared_receipt_ref": _regular_ref(root, root / "PREPARATION.json"),
        "prepared_bindings": dict(bindings),
        "selection_id": selection["selection_id"],
        "terminal_receipt_ref": dict(terminal_ref),
        "terminal_receipt_kind": (
            "unclosed_attempt"
            if unclosed is not None
            else (
                "zero_genesis"
                if prior_kind == "explicit_new_task_zero_genesis"
                else "imported_genesis"
            )
            if not attempt_paths
            else "outcome"
        ),
        "attempt_count": len(attempt_paths),
        "outcome_count": len(outcome_paths),
        "prior_accounting": dict(prior_accounting),
        "initial_observed_lower_bound_new_raw_bytes": initial_lower,
        "initial_reserved_upper_bound_new_raw_bytes": initial_upper,
        "probe_observed_lower_bound_new_raw_bytes": observed_probe_lower,
        "probe_observed_upper_bound_new_raw_bytes": observed_probe_upper,
        "cumulative_reserved_new_raw_bytes": cumulative,
        "cumulative_semantics": "nonrefundable_reserved_upper_bound",
        "reservation_refund_policy": "never_refund_even_on_failure_timeout_or_retry",
        "chain_refs_sha256": hashlib.sha256(
            canonical_json(chain_refs).encode("utf-8")
        ).hexdigest(),
    }
    accounting = {
        "schema_version": PROBE_ACCOUNTING_SCHEMA_VERSION,
        **semantic,
    }
    accounting["accounting_fingerprint_sha256"] = hashlib.sha256(
        canonical_json(
            {
                "schema": PROBE_ACCOUNTING_FINGERPRINT_SCHEMA,
                "accounting": accounting,
            }
        ).encode("utf-8")
    ).hexdigest()
    return {**accounting, "unclosed_attempt": unclosed}


def _raw_projection_artifact(row: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(row, Mapping):
        raise ExecutionPrepError(f"{field} 必须是制品对象")
    required = ("artifact_id", "file_sha256", "relative_path", "size_bytes")
    if any(name not in row for name in required):
        raise ExecutionPrepError(f"{field} 制品身份字段不闭合")
    size = row.get("size_bytes")
    if (
        not isinstance(row.get("artifact_id"), str)
        or not row["artifact_id"]
        or _SHA256_RE.fullmatch(str(row.get("file_sha256"))) is None
        or not isinstance(row.get("relative_path"), str)
        or not row["relative_path"]
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
    ):
        raise ExecutionPrepError(f"{field} 制品身份非法")
    return {name: row[name] for name in required}


def _build_full_flow_raw_projection(
    selection: Mapping[str, Any],
    *,
    prior_accounting: Mapping[str, Any],
) -> Mapping[str, Any]:
    """冻结从 prior 到最终 RIB 分析的一次成功路径和一个整制品重试余量。"""

    updates = _selection_updates(selection)
    roles = selection.get("roles")
    if not isinstance(roles, Mapping):
        raise ExecutionPrepError("selection.roles 缺失")
    seed = _raw_projection_artifact(
        roles.get("state_seed_rib"), "state_seed_rib"
    )
    baseline = _raw_projection_artifact(
        roles.get("baseline_reference_rib"), "baseline_reference_rib"
    )
    analysis_raw = roles.get("analysis_ribs")
    if not isinstance(analysis_raw, list) or not analysis_raw:
        raise ExecutionPrepError("analysis_ribs 人口缺失")
    analysis = [
        _raw_projection_artifact(row, f"analysis_ribs[{index}]")
        for index, row in enumerate(analysis_raw)
    ]
    analysis_new = [
        row for row in analysis if row["artifact_id"] != seed["artifact_id"]
    ]
    if len(analysis_new) != len(analysis) - 1:
        raise ExecutionPrepError("analysis_ribs 必须且只能包含一次 state seed")
    update_rows = [
        _raw_projection_artifact(row, f"analysis_updates[{index}]")
        for index, row in enumerate(updates)
    ]
    probe_indices = _probe_indices(len(update_rows), ())
    probe_rows = [update_rows[index] for index in probe_indices]
    prior_upper = prior_accounting.get("reserved_upper_bound_new_raw_bytes")
    if (
        isinstance(prior_upper, bool)
        or not isinstance(prior_upper, int)
        or prior_upper < 0
    ):
        raise ExecutionPrepError("prior accounting upper 非法")
    update_bytes = sum(int(row["size_bytes"]) for row in update_rows)
    probe_bytes = sum(int(row["size_bytes"]) for row in probe_rows)
    analysis_bytes = sum(int(row["size_bytes"]) for row in analysis_new)
    seed_bytes = int(seed["size_bytes"])
    baseline_bytes = int(baseline["size_bytes"])
    retry_margin = max(
        [
            seed_bytes,
            baseline_bytes,
            *(int(row["size_bytes"]) for row in update_rows),
            *(int(row["size_bytes"]) for row in analysis_new),
        ]
    )
    components = {
        "pre_goal_prior_reserved_upper": {
            "bytes": prior_upper,
            "source_receipt_file_sha256": prior_accounting.get(
                "source_receipt_file_sha256"
            ),
            "semantics": prior_accounting.get("semantics"),
        },
        "native_probe": {
            "artifact_count": len(probe_rows),
            "artifact_indices": list(probe_indices),
            "artifacts": probe_rows,
            "bytes": probe_bytes,
        },
        "seed_initial_read": {
            "artifact": seed,
            "artifact_count": 1,
            "bytes": seed_bytes,
        },
        "seed_retirement_verification_reread": {
            "artifact": seed,
            "artifact_count": 1,
            "bytes": seed_bytes,
        },
        "full_update_replay": {
            "artifact_count": len(update_rows),
            "artifacts_identity_sha256": hashlib.sha256(
                canonical_json(update_rows).encode("utf-8")
            ).hexdigest(),
            "bytes": update_bytes,
        },
        "analysis_rib_replay_excluding_seed": {
            "artifact_count": len(analysis_new),
            "artifacts": analysis_new,
            "bytes": analysis_bytes,
        },
        "baseline_reference_rib": {
            "artifact": baseline,
            "artifact_count": 1,
            "bytes": baseline_bytes,
        },
        "minimum_failure_retry_margin": {
            "method": "one_complete_largest_planned_raw_artifact",
            "minimum_retry_count": 1,
            "bytes": retry_margin,
        },
    }
    projected = (
        prior_upper
        + probe_bytes
        + seed_bytes
        + seed_bytes
        + update_bytes
        + analysis_bytes
        + baseline_bytes
        + retry_margin
    )
    if projected >= RAW_LIMIT_BYTES:
        raise ExecutionPrepError("完整流程累计 raw 投影达到 50GB 排他边界")
    semantic = {
        "selection_id": selection.get("selection_id"),
        "input_selection_sha256": selection.get("semantic_fingerprint_sha256"),
        "components": components,
        "projected_cumulative_new_raw_read_bytes": projected,
        "maximum_cumulative_new_raw_read_bytes_exclusive": RAW_LIMIT_BYTES,
        "projection_allowed": True,
        "projection_semantics": (
            "conservative_success_path_plus_one_largest_artifact_retry_"
            "actual_create_only_ledgers_remain_authoritative"
        ),
        "database_write_operations": 0,
    }
    return _fingerprinted(
        FULL_FLOW_RAW_PROJECTION_SCHEMA_VERSION,
        FULL_FLOW_RAW_PROJECTION_FINGERPRINT_SCHEMA,
        semantic,
    )


def _run_prepare(args: argparse.Namespace) -> Mapping[str, Any]:
    raw_root = _safe_directory(args.raw_root, "raw_root")
    profile = validate_research_profile(load_json_metadata(args.profile))
    manifest = load_json_metadata(args.manifest, maximum_bytes=512 * 1024 * 1024)
    verification = load_json_metadata(args.manifest_verification)
    selection = resolve_research_inputs(
        manifest, verification, _resolver_profile(profile)
    )
    validate_complete_selection_against_profile(selection, profile)
    updates = _selection_updates(selection)
    compatible_snapshot = load_json_metadata(
        args.compatible_mapping, maximum_bytes=64 * 1024 * 1024
    )
    revised_snapshot = load_json_metadata(
        args.revised_mapping, maximum_bytes=16 * 1024 * 1024
    )
    compatible = mapping_view_from_frozen_snapshot(compatible_snapshot)
    mapping_view_from_revised_snapshot(revised_snapshot, compatible_snapshot)
    if compatible.target_country != profile["country_code"]:
        raise ExecutionPrepError("compatible mapping target 与 Profile 国家不一致")
    seed_attestation = validate_seed_spool_attestation(
        load_json_metadata(args.seed_spool_attestation, maximum_bytes=1024 * 1024),
        seed_artifact=selection["roles"]["state_seed_rib"],
    )
    source_fact = load_json_metadata(args.source_fact, maximum_bytes=1024 * 1024)
    incident_policy = load_json_metadata(
        args.incident_policy, maximum_bytes=1024 * 1024
    )
    claim_inventory = load_json_metadata(
        args.claim_inventory, maximum_bytes=1024 * 1024
    )
    code_identity = build_code_identity()
    bindings = {
        "profile_sha256": profile_sha256(profile),
        "input_selection_sha256": selection["semantic_fingerprint_sha256"],
        "code_sha256": code_identity["identity_sha256"],
        "mapping_sha256": mapping_bundle_sha256(
            compatible_snapshot, revised_snapshot
        ),
    }
    prior_accounting, prior_import_receipt = _prior_accounting_for_prepare(
        args, study_id=profile["study_id"]
    )
    raw_projection = _build_full_flow_raw_projection(
        selection,
        prior_accounting=prior_accounting,
    )
    factory = _native_factory(
        raw_root,
        (updates[0],),
        window=selection["window"],
        max_spool_bytes=args.max_spool_bytes,
        max_frame_bytes=args.native_max_frame_bytes,
    )
    contract = _parser_contract(factory.parser_attestation)
    _validate_generated_parser_attestation(
        factory.parser_attestation, contract=contract
    )

    root = _new_output_root(args.output_directory, raw_root=raw_root)
    payloads = {
        "research-profile.json": profile,
        "mrt-artifact-manifest.json": manifest,
        "manifest-verification.json": verification,
        "full-selection.json": selection,
        "compatible-mapping.json": compatible_snapshot,
        "revised-mapping.json": revised_snapshot,
        "seed-spool-attestation.json": seed_attestation,
        "source-fact.json": source_fact,
        "incident-policy.json": incident_policy,
        "claim-inventory.json": claim_inventory,
        "code-identity.json": code_identity,
        "full-window-bindings.json": bindings,
        "native-parser-contract.json": contract,
        "full-flow-raw-projection.json": raw_projection,
    }
    refs: dict[str, Mapping[str, Any]] = {}
    try:
        for name in _PREPARED_FILES:
            published = write_canonical_json(
                root / name,
                payloads[name],
                kind="rrc25_execution_preparation",
                mode=0o440,
            )
            refs[name] = _published_ref(root, published)
        probe_ledger = root.joinpath(*_PROBE_LEDGER_RELATIVE.parts)
        probe_attempts = root.joinpath(*_PROBE_ATTEMPTS_RELATIVE.parts)
        probe_outcomes = root.joinpath(*_PROBE_OUTCOMES_RELATIVE.parts)
        seed_raw_attempts = root.joinpath(*_SEED_RAW_ATTEMPTS_RELATIVE.parts)
        seed_raw_outcomes = root.joinpath(*_SEED_RAW_OUTCOMES_RELATIVE.parts)
        probe_throughput = root.joinpath(*_PROBE_THROUGHPUT_RELATIVE.parts)
        probe_ledger.mkdir(mode=0o750, exist_ok=False)
        probe_attempts.mkdir(mode=0o750, exist_ok=False)
        probe_outcomes.mkdir(mode=0o750, exist_ok=False)
        seed_raw_attempts.mkdir(mode=0o750, exist_ok=False)
        seed_raw_outcomes.mkdir(mode=0o750, exist_ok=False)
        probe_throughput.mkdir(mode=0o750, exist_ok=False)
        probe_lock = root.joinpath(*_PROBE_LOCK_RELATIVE.parts)
        probe_lock.touch(mode=0o600, exist_ok=False)
        seed_execution_lock = root.joinpath(*_SEED_EXECUTION_LOCK_RELATIVE.parts)
        seed_execution_lock.touch(mode=0o600, exist_ok=False)
        ledger_id = "probe_ledger_v1_" + secrets.token_hex(16)
        imported_ref = None
        if prior_import_receipt is not None:
            imported = write_canonical_json(
                root.joinpath(*_PROBE_IMPORTED_PRIOR_RELATIVE.parts),
                prior_import_receipt,
                kind="rrc25_preexisting_raw_accounting_import",
                mode=0o440,
            )
            imported_ref = _published_ref(root, imported)
        frozen_prior = {
            **dict(prior_accounting),
            "imported_receipt_ref": imported_ref,
        }
        genesis = _fingerprinted(
            PROBE_GENESIS_SCHEMA_VERSION,
            PROBE_GENESIS_FINGERPRINT_SCHEMA,
            {
                "ledger_id": ledger_id,
                "prepared_bindings": bindings,
                "selection_id": selection["selection_id"],
                "prior_accounting": frozen_prior,
                "cumulative_reserved_new_raw_bytes": prior_accounting[
                    "cumulative_reserved_new_raw_bytes"
                ],
                "cumulative_semantics": (
                    "nonrefundable_reserved_upper_bound_not_measured_exact"
                    if prior_accounting["accounting_state"]
                    == "conservative_upper_bound"
                    else "nonrefundable_reserved_exact"
                ),
                "reservation_refund_policy": (
                    "never_refund_even_on_failure_timeout_or_retry"
                ),
                "raw_open_authorized": False,
                "raw_mrt_files_opened": 0,
                "database_write_operations": 0,
            },
        )
        published_genesis = write_canonical_json(
            root.joinpath(*_PROBE_GENESIS_RELATIVE.parts),
            genesis,
            kind="rrc25_native_probe_raw_genesis",
            mode=0o440,
        )
        genesis_ref = _published_ref(root, published_genesis)
        receipt = _fingerprinted(
            PREPARATION_SCHEMA_VERSION,
            PREPARATION_FINGERPRINT_SCHEMA,
            {
                "bindings": bindings,
                "selection_id": selection["selection_id"],
                "update_artifact_count": len(updates),
                "update_compressed_bytes": sum(
                    int(row["size_bytes"]) for row in updates
                ),
                "parser_contract_fingerprint_sha256": contract[
                    "semantic_fingerprint_sha256"
                ],
                "full_flow_raw_projection_fingerprint_sha256": raw_projection[
                    "receipt_fingerprint_sha256"
                ],
                "files": {name: refs[name] for name in sorted(refs)},
                "parent_manifest_fingerprint_sha256": selection[
                    "parent_manifest_fingerprint_sha256"
                ],
                "probe_raw_ledger": {
                    "ledger_id": ledger_id,
                    "root_path": _PROBE_LEDGER_RELATIVE.as_posix(),
                    "genesis_ref": genesis_ref,
                    "prior_accounting": frozen_prior,
                    "initial_cumulative_reserved_new_raw_bytes": prior_accounting[
                        "cumulative_reserved_new_raw_bytes"
                    ],
                    "initial_observed_lower_bound_new_raw_bytes": prior_accounting[
                        "observed_lower_bound_new_raw_bytes"
                    ],
                    "reservation_refund_policy": (
                        "never_refund_even_on_failure_timeout_or_retry"
                    ),
                },
                "raw_mrt_files_opened": 0,
                "new_raw_read_bytes": 0,
                "database_connections": 0,
                "database_write_operations": 0,
            },
        )
        published_receipt = write_canonical_json(
            root / "PREPARATION.json",
            receipt,
            kind="rrc25_execution_preparation_receipt",
            mode=0o440,
        )
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(root, 0o550)
    except BaseException:
        # 非空残留没有 PREPARATION 或不能通过 loader，永远不会被当成成功准备。
        raise
    return {
        "ok": True,
        "command": "prepare",
        "prepared_directory": str(root),
        "preparation_receipt": _published_ref(root, published_receipt),
        "bindings": bindings,
        "selection_id": selection["selection_id"],
        "update_artifact_count": len(updates),
        "probe_ledger_terminal_receipt": str(
            root.joinpath(*_PROBE_GENESIS_RELATIVE.parts)
        ),
        "probe_ledger_terminal_ref": genesis_ref,
        "prior_raw_accounting": frozen_prior,
        "full_flow_raw_projection": raw_projection,
        "raw_mrt_files_opened": 0,
        "database_write_operations": 0,
    }


def _load_receipt(path: Path, *, schema: str, fingerprint_schema: str) -> Mapping[str, Any]:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExecutionPrepError("receipt 不是合法 JSON") from error
    if not isinstance(payload, Mapping) or payload.get("schema_version") != schema:
        raise ExecutionPrepError("receipt schema 不受支持")
    if raw != (canonical_json(dict(payload)) + "\n").encode("utf-8"):
        raise ExecutionPrepError("receipt 不是规范 JSON")
    semantic = dict(payload)
    fingerprint = semantic.pop("receipt_fingerprint_sha256", None)
    expected = hashlib.sha256(
        canonical_json(
            {"schema": fingerprint_schema, "receipt": semantic}
        ).encode("utf-8")
    ).hexdigest()
    if fingerprint != expected:
        raise ExecutionPrepError("receipt fingerprint 不一致")
    return dict(payload)


def _load_prepared(path_value: str | Path, raw_root: Path) -> Mapping[str, Any]:
    root = _safe_directory(path_value, "prepared_directory")
    _assert_disjoint(root, raw_root, "prepared_directory 不得与 raw_root 重叠")
    receipt = _load_receipt(
        root / "PREPARATION.json",
        schema=PREPARATION_SCHEMA_VERSION,
        fingerprint_schema=PREPARATION_FINGERPRINT_SCHEMA,
    )
    refs = receipt.get("files")
    if not isinstance(refs, Mapping) or set(refs) != set(_PREPARED_FILES):
        raise ExecutionPrepError("PREPARATION files 不闭合")
    loaded: dict[str, Mapping[str, Any]] = {}
    for name in _PREPARED_FILES:
        ref = refs[name]
        if not isinstance(ref, Mapping) or ref.get("path") != name:
            raise ExecutionPrepError("PREPARATION file ref 非法")
        digest, size = _hash_regular(root / name, maximum_bytes=512 * 1024 * 1024)
        if digest != ref.get("sha256") or size != ref.get("size_bytes"):
            raise ExecutionPrepError("PREPARATION file hash/size 不一致")
        loaded[name] = load_json_metadata(
            root / name, maximum_bytes=512 * 1024 * 1024
        )
    code_identity = build_code_identity()
    if loaded["code-identity.json"] != code_identity:
        raise ExecutionPrepError("当前代码与 prepared code identity 不一致")
    bindings = loaded["full-window-bindings.json"]
    if bindings != receipt.get("bindings"):
        raise ExecutionPrepError("prepared bindings 与 receipt 不一致")
    profile = validate_complete_selection_against_profile(
        loaded["full-selection.json"], loaded["research-profile.json"]
    )
    selection = loaded["full-selection.json"]
    _selection_updates(selection)
    expected_bindings = {
        "profile_sha256": profile_sha256(profile),
        "input_selection_sha256": selection["semantic_fingerprint_sha256"],
        "code_sha256": code_identity["identity_sha256"],
        "mapping_sha256": mapping_bundle_sha256(
            loaded["compatible-mapping.json"], loaded["revised-mapping.json"]
        ),
    }
    if bindings != expected_bindings:
        raise ExecutionPrepError("prepared bindings 重算不一致")
    frozen_projection = loaded["full-flow-raw-projection.json"]
    projection_ledger_meta = receipt.get("probe_raw_ledger")
    if not isinstance(projection_ledger_meta, Mapping):
        raise ExecutionPrepError("PREPARATION 缺少 probe raw ledger")
    expected_projection = _build_full_flow_raw_projection(
        selection,
        prior_accounting=projection_ledger_meta.get("prior_accounting", {}),
    )
    if (
        frozen_projection != expected_projection
        or receipt.get("full_flow_raw_projection_fingerprint_sha256")
        != expected_projection.get("receipt_fingerprint_sha256")
    ):
        raise ExecutionPrepError("完整流程 raw 投影收据与 selection/prior 重算不一致")
    ledger_meta = receipt.get("probe_raw_ledger")
    if (
        not isinstance(ledger_meta, Mapping)
        or set(ledger_meta)
        != {
            "ledger_id",
            "root_path",
            "genesis_ref",
            "prior_accounting",
            "initial_cumulative_reserved_new_raw_bytes",
            "initial_observed_lower_bound_new_raw_bytes",
            "reservation_refund_policy",
        }
        or ledger_meta.get("root_path") != _PROBE_LEDGER_RELATIVE.as_posix()
        or ledger_meta.get("reservation_refund_policy")
        != "never_refund_even_on_failure_timeout_or_retry"
    ):
        raise ExecutionPrepError("PREPARATION probe raw ledger 元数据不闭合")
    prepared = {"root": root, "receipt": receipt, **loaded}
    # 这里只核验 zero genesis 与目录身份；完整 attempt/outcome 链由每次
    # reservation 前以及 seed-start/resume 前在同一 execution lock 内重算。
    paths = _probe_ledger_paths(root)
    genesis, genesis_ref = _load_referenced_probe_receipt(
        root,
        ledger_meta.get("genesis_ref"),
        schema=PROBE_GENESIS_SCHEMA_VERSION,
        fingerprint_schema=PROBE_GENESIS_FINGERPRINT_SCHEMA,
    )
    if (
        paths["genesis"] != root.joinpath(*_PROBE_GENESIS_RELATIVE.parts)
        or genesis_ref != ledger_meta.get("genesis_ref")
        or genesis.get("ledger_id") != ledger_meta.get("ledger_id")
        or genesis.get("prepared_bindings") != bindings
        or genesis.get("selection_id") != selection.get("selection_id")
        or genesis.get("prior_accounting") != ledger_meta.get("prior_accounting")
        or genesis.get("cumulative_reserved_new_raw_bytes")
        != ledger_meta.get("initial_cumulative_reserved_new_raw_bytes")
        or genesis.get("prior_accounting", {}).get(
            "observed_lower_bound_new_raw_bytes"
        )
        != ledger_meta.get("initial_observed_lower_bound_new_raw_bytes")
    ):
        raise ExecutionPrepError("PREPARATION probe raw genesis 不闭合")
    return prepared


def _probe_indices(count: int, explicit: Sequence[int]) -> tuple[int, ...]:
    if explicit:
        values = tuple(explicit)
    else:
        values = tuple(sorted({0, count // 4, count // 2, (3 * count) // 4, count - 1}))
    if (
        not values
        or len(values) > PROBE_MAX_ARTIFACTS
        or len(set(values)) != len(values)
        or any(index < 0 or index >= count for index in values)
        or tuple(sorted(values)) != values
    ):
        raise ExecutionPrepError("probe artifact index 必须升序、唯一且最多五个")
    return values


def _write_probe_receipt(
    root: Path,
    relative: PurePosixPath,
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    published = write_canonical_json(
        root.joinpath(*relative.parts),
        payload,
        kind="rrc25_native_probe_receipt",
        mode=0o440,
    )
    return _published_ref(root, published)


def _publish_probe_throughput_receipt(
    root: Path,
    *,
    prepared: Mapping[str, Any],
    outcome: Mapping[str, Any],
    outcome_ref: Mapping[str, Any],
    terminal_accounting: Mapping[str, Any],
) -> Mapping[str, Any]:
    """从完整 probe outcome 推导保守吞吐，不接受调用方手填速率。"""

    observed = outcome.get("observed_compressed_bytes_sum")
    elapsed = outcome.get("elapsed_seconds")
    if (
        outcome.get("outcome") != "complete_single_pass"
        or outcome.get("observed_compressed_bytes_state") != "exact"
        or isinstance(observed, bool)
        or not isinstance(observed, int)
        or observed <= 0
        or isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(float(elapsed))
        or float(elapsed) <= 0
        or float(elapsed) >= PROBE_HARD_SECONDS
        or terminal_accounting.get("terminal_receipt_ref") != outcome_ref
        or terminal_accounting.get("terminal_receipt_kind") != "outcome"
    ):
        raise ExecutionPrepError("probe outcome 不能形成保守吞吐收据")
    safety_divisor = 2
    conservative = max(1, int(observed / (float(elapsed) * safety_divisor)))
    semantic = {
        "prepared_bindings": dict(prepared["full-window-bindings.json"]),
        "selection_id": prepared["full-selection.json"]["selection_id"],
        "native_parser_contract": dict(
            prepared["native-parser-contract.json"]
        ),
        "probe_ledger_id": terminal_accounting["ledger_id"],
        "probe_terminal_accounting_fingerprint_sha256": terminal_accounting[
            "accounting_fingerprint_sha256"
        ],
        "probe_terminal_receipt_ref": dict(
            terminal_accounting["terminal_receipt_ref"]
        ),
        "probe_outcome_ref": dict(outcome_ref),
        "probe_outcome": dict(outcome),
        "observed_compressed_bytes": observed,
        "elapsed_seconds": float(elapsed),
        "derivation": {
            "method": "floor_exact_probe_bytes_per_second_divided_by_safety_factor",
            "safety_divisor": safety_divisor,
            "minimum_bytes_per_second": 1,
        },
        "conservative_bytes_per_second": conservative,
        "database_write_operations": 0,
    }
    receipt = _fingerprinted(
        PROBE_THROUGHPUT_SCHEMA_VERSION,
        PROBE_THROUGHPUT_FINGERPRINT_SCHEMA,
        semantic,
    )
    relative = _PROBE_THROUGHPUT_RELATIVE / (
        "throughput-" + receipt["receipt_fingerprint_sha256"] + ".json"
    )
    ref = _write_probe_receipt(root, relative, receipt)
    return {
        "path": str(root.joinpath(*relative.parts)),
        "ref": ref,
        "receipt": receipt,
    }


def _public_probe_accounting(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return {key: item for key, item in value.items() if key != "unclosed_attempt"}


def verify_probe_raw_ledger_terminal(
    prepared_directory: str | Path,
    terminal_receipt: str | Path,
    *,
    raw_root: str | Path,
) -> Mapping[str, Any]:
    """只读核验唯一 probe terminal；不接受调用方提供累计数字。"""

    resolved_raw = _safe_directory(raw_root, "raw_root")
    prepared = _load_prepared(prepared_directory, resolved_raw)
    with _probe_ledger_lock(prepared["root"], blocking=False):
        accounting = _probe_terminal_accounting(
            prepared["root"],
            prepared,
            supplied_terminal=terminal_receipt,
            allow_unclosed_tail=False,
        )
    return _public_probe_accounting(accounting)


def _seed_raw_receipt_filename(
    *, kind: str, sequence: int, attempt_id: str
) -> str:
    if kind not in {"attempt", "outcome"}:
        raise ExecutionPrepError("seed raw receipt kind 非法")
    if sequence <= 0 or re.fullmatch(r"seed_v1_[0-9a-f]{32}", attempt_id) is None:
        raise ExecutionPrepError("seed raw receipt sequence/attempt_id 非法")
    return f"seed-{kind}-{sequence:06d}-{attempt_id}.json"


def _seed_raw_artifact_identity(value: Mapping[str, Any]) -> Mapping[str, Any]:
    fields = (
        "artifact_id",
        "file_sha256",
        "size_bytes",
        "relative_path",
        "collector_id",
        "artifact_time_utc",
    )
    if not isinstance(value, Mapping) or any(field not in value for field in fields):
        raise ExecutionPrepError("state_seed_rib 身份字段不闭合")
    size = value.get("size_bytes")
    if (
        not isinstance(value.get("artifact_id"), str)
        or not value["artifact_id"]
        or _SHA256_RE.fullmatch(str(value.get("file_sha256"))) is None
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
        or not isinstance(value.get("relative_path"), str)
        or not value["relative_path"]
        or not isinstance(value.get("collector_id"), str)
        or not value["collector_id"]
        or not isinstance(value.get("artifact_time_utc"), str)
        or not value["artifact_time_utc"]
    ):
        raise ExecutionPrepError("state_seed_rib 身份非法")
    return {field: value[field] for field in fields}


def _fingerprinted_seed_reservation(semantic: Mapping[str, Any]) -> Mapping[str, Any]:
    value = {"schema_version": SEED_RAW_RESERVATION_SCHEMA_VERSION, **dict(semantic)}
    value["reservation_fingerprint_sha256"] = hashlib.sha256(
        canonical_json(
            {
                "schema": SEED_RAW_RESERVATION_FINGERPRINT_SCHEMA,
                "reservation": value,
            }
        ).encode("utf-8")
    ).hexdigest()
    return value


def _seed_raw_ledger_state(
    root: Path,
    prepared: Mapping[str, Any],
    *,
    probe_accounting: Mapping[str, Any],
    seed_artifact: Mapping[str, Any],
    allow_unclosed_tail: bool,
) -> Mapping[str, Any]:
    """扫描 seed raw create-only 链；每个 attempt 的整份 reservation 不退款。"""

    paths = _probe_ledger_paths(root)
    seed_identity = _seed_raw_artifact_identity(seed_artifact)
    bindings = prepared.get("full-window-bindings.json")
    selection = prepared.get("full-selection.json")
    if (
        not isinstance(bindings, Mapping)
        or not isinstance(selection, Mapping)
        or probe_accounting.get("prepared_bindings") != bindings
        or probe_accounting.get("selection_id") != selection.get("selection_id")
    ):
        raise ExecutionPrepError("seed raw ledger 与 prepared/probe 身份不闭合")
    base = probe_accounting.get("cumulative_reserved_new_raw_bytes")
    if isinstance(base, bool) or not isinstance(base, int) or base < 0:
        raise ExecutionPrepError("probe terminal cumulative 非法")

    attempt_name = re.compile(
        r"^seed-attempt-[0-9]{6}-seed_v1_[0-9a-f]{32}\.json$"
    )
    outcome_name = re.compile(
        r"^seed-outcome-[0-9]{6}-seed_v1_[0-9a-f]{32}\.json$"
    )

    def strict_seed_receipts(directory: Path, pattern: re.Pattern[str]) -> tuple[Path, ...]:
        values = []
        for path in directory.iterdir():
            metadata = path.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or pattern.fullmatch(path.name) is None
            ):
                raise ExecutionPrepError(
                    "seed raw ledger receipt 目录含未分类或非普通文件条目"
                )
            values.append(path)
        return tuple(sorted(values, key=lambda item: item.name))

    attempt_paths = strict_seed_receipts(paths["seed_attempts"], attempt_name)
    outcome_paths = strict_seed_receipts(paths["seed_outcomes"], outcome_name)
    outcomes: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for path in outcome_paths:
        outcome = _load_receipt(
            path,
            schema=SEED_RAW_OUTCOME_SCHEMA_VERSION,
            fingerprint_schema=SEED_RAW_OUTCOME_FINGERPRINT_SCHEMA,
        )
        attempt_id = outcome.get("attempt_id")
        if not isinstance(attempt_id, str) or attempt_id in outcomes:
            raise ExecutionPrepError("seed raw outcome attempt_id 非法或重复")
        outcomes[attempt_id] = (outcome, _regular_ref(root, path))

    cumulative = base
    previous_terminal_ref: Mapping[str, Any] | None = None
    latest_reservation: Mapping[str, Any] | None = None
    latest_outcome_ref: Mapping[str, Any] | None = None
    latest_outcome: Mapping[str, Any] | None = None
    unclosed: tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]] | None = None
    for expected_sequence, path in enumerate(attempt_paths, start=1):
        attempt = _load_receipt(
            path,
            schema=SEED_RAW_ATTEMPT_SCHEMA_VERSION,
            fingerprint_schema=SEED_RAW_ATTEMPT_FINGERPRINT_SCHEMA,
        )
        attempt_ref = _regular_ref(root, path)
        attempt_id = attempt.get("attempt_id")
        expected_name = (
            _seed_raw_receipt_filename(
                kind="attempt",
                sequence=expected_sequence,
                attempt_id=str(attempt_id),
            )
            if isinstance(attempt_id, str)
            else ""
        )
        reserved = seed_identity["size_bytes"]
        if (
            path.name != expected_name
            or attempt.get("ledger_id") != probe_accounting.get("ledger_id")
            or attempt.get("sequence") != expected_sequence
            or attempt.get("prepared_bindings") != bindings
            or attempt.get("selection_id") != selection.get("selection_id")
            or attempt.get("probe_terminal_accounting_fingerprint_sha256")
            != probe_accounting.get("accounting_fingerprint_sha256")
            or attempt.get("probe_terminal_receipt_ref")
            != probe_accounting.get("terminal_receipt_ref")
            or attempt.get("previous_seed_terminal_ref") != previous_terminal_ref
            or attempt.get("seed_artifact") != seed_identity
            or attempt.get("prior_cumulative_reserved_new_raw_bytes") != cumulative
            or attempt.get("reserved_new_raw_bytes") != reserved
            or attempt.get("cumulative_reserved_new_raw_bytes")
            != cumulative + reserved
            or cumulative + reserved >= RAW_LIMIT_BYTES
            or attempt.get("reservation_refund_policy")
            != "never_refund_even_on_failure_timeout_or_retry"
            or attempt.get("raw_open_authorized_after_this_receipt") is not True
            or attempt.get("database_write_operations") != 0
        ):
            raise ExecutionPrepError("seed raw attempt 链、身份或 reservation 不闭合")
        reservation = _fingerprinted_seed_reservation(
            {
                "ledger_id": attempt["ledger_id"],
                "prepared_directory": str(root),
                "prepared_bindings": dict(bindings),
                "selection_id": selection["selection_id"],
                "probe_terminal_accounting_fingerprint_sha256": attempt[
                    "probe_terminal_accounting_fingerprint_sha256"
                ],
                "probe_terminal_receipt_ref": dict(
                    attempt["probe_terminal_receipt_ref"]
                ),
                "attempt_ref": dict(attempt_ref),
                "attempt_id": attempt["attempt_id"],
                "sequence": expected_sequence,
                "seed_artifact": dict(seed_identity),
                "previous_seed_terminal_ref": (
                    None
                    if previous_terminal_ref is None
                    else dict(previous_terminal_ref)
                ),
                "prior_cumulative_reserved_new_raw_bytes": cumulative,
                "reserved_new_raw_bytes": reserved,
                "cumulative_reserved_new_raw_bytes": cumulative + reserved,
                "reservation_refund_policy": (
                    "never_refund_even_on_failure_timeout_or_retry"
                ),
            }
        )
        cumulative += reserved
        latest_reservation = reservation
        outcome_pair = outcomes.pop(str(attempt_id), None)
        if outcome_pair is None:
            if expected_sequence != len(attempt_paths) or not allow_unclosed_tail:
                raise ExecutionPrepError("seed raw ledger 存在未闭合 attempt")
            unclosed = (attempt, attempt_ref, reservation)
            latest_outcome = None
            latest_outcome_ref = None
            continue
        outcome, outcome_ref = outcome_pair
        expected_outcome_name = _seed_raw_receipt_filename(
            kind="outcome",
            sequence=expected_sequence,
            attempt_id=str(attempt_id),
        )
        observed_state = outcome.get("observed_compressed_bytes_state")
        observed_exact = outcome.get("observed_compressed_bytes")
        lower = outcome.get("observed_compressed_bytes_lower_bound")
        upper = outcome.get("observed_compressed_bytes_upper_bound")
        checkpoint_ref = outcome.get("checkpoint_ref")
        if (
            Path(str(outcome_ref["path"])).name != expected_outcome_name
            or outcome.get("ledger_id") != attempt.get("ledger_id")
            or outcome.get("sequence") != expected_sequence
            or outcome.get("attempt_id") != attempt_id
            or outcome.get("attempt_ref") != attempt_ref
            or outcome.get("seed_reservation_fingerprint_sha256")
            != reservation.get("reservation_fingerprint_sha256")
            or outcome.get("cumulative_reserved_new_raw_bytes") != cumulative
            or outcome.get("outcome")
            not in {
                "checkpoint_published_seed_read_exact",
                "failed_or_stopped_reservation_not_refunded",
            }
            or observed_state not in {"exact", "bounded_after_process_termination"}
            or isinstance(lower, bool)
            or not isinstance(lower, int)
            or isinstance(upper, bool)
            or not isinstance(upper, int)
            or not 0 <= lower <= upper <= reserved
            or (
                observed_state == "exact"
                and (observed_exact != lower or observed_exact != upper)
            )
            or (observed_state != "exact" and observed_exact is not None)
            or (
                outcome.get("outcome") == "checkpoint_published_seed_read_exact"
                and (
                    observed_state != "exact"
                    or observed_exact != reserved
                    or not isinstance(checkpoint_ref, Mapping)
                    or set(checkpoint_ref)
                    != {
                        "path",
                        "checkpoint_sequence",
                        "checkpoint_fingerprint_sha256",
                    }
                    or _SHA256_RE.fullmatch(
                        str(checkpoint_ref.get("checkpoint_fingerprint_sha256"))
                    )
                    is None
                    or not isinstance(checkpoint_ref.get("path"), str)
                    or not checkpoint_ref["path"]
                    or isinstance(checkpoint_ref.get("checkpoint_sequence"), bool)
                    or not isinstance(checkpoint_ref.get("checkpoint_sequence"), int)
                    or checkpoint_ref["checkpoint_sequence"] < 0
                    or outcome.get("failure") is not None
                )
            )
            or (
                outcome.get("outcome")
                == "failed_or_stopped_reservation_not_refunded"
                and (
                    checkpoint_ref is not None
                    or observed_state != "bounded_after_process_termination"
                    or lower != 0
                    or upper != reserved
                    or not isinstance(outcome.get("failure"), Mapping)
                    or not isinstance(outcome["failure"].get("type"), str)
                    or not outcome["failure"]["type"]
                    or not isinstance(outcome["failure"].get("message"), str)
                    or not outcome["failure"]["message"]
                )
            )
            or outcome.get("reservation_refund_policy")
            != "never_refund_even_on_failure_timeout_or_retry"
            or outcome.get("database_write_operations") != 0
        ):
            raise ExecutionPrepError("seed raw outcome 与 attempt/reservation 不闭合")
        previous_terminal_ref = outcome_ref
        latest_outcome = outcome
        latest_outcome_ref = outcome_ref
    if outcomes:
        raise ExecutionPrepError("seed raw outcome 没有对应 attempt")
    return {
        "ledger_id": probe_accounting["ledger_id"],
        "probe_terminal_accounting": _public_probe_accounting(probe_accounting),
        "seed_artifact": dict(seed_identity),
        "attempt_count": len(attempt_paths),
        "outcome_count": len(outcome_paths),
        "current_cumulative_reserved_new_raw_bytes": cumulative,
        "latest_reservation": latest_reservation,
        "latest_outcome_ref": latest_outcome_ref,
        "latest_outcome": latest_outcome,
        "unclosed_attempt": unclosed,
    }


def _seed_raw_context(
    prepared_directory: str | Path,
    terminal_receipt: str | Path,
    *,
    raw_root: str | Path,
    seed_artifact: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any], Path]:
    resolved_raw = _safe_directory(raw_root, "raw_root")
    prepared = _load_prepared(prepared_directory, resolved_raw)
    root = prepared["root"]
    probe = _probe_terminal_accounting(
        root,
        prepared,
        supplied_terminal=terminal_receipt,
        allow_unclosed_tail=False,
    )
    return prepared, probe, root


def verify_seed_raw_ledger(
    prepared_directory: str | Path,
    terminal_receipt: str | Path,
    *,
    raw_root: str | Path,
    seed_artifact: Mapping[str, Any],
    expected_reservation: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """只读核验 seed reservation 链；不打开 MRT。"""

    prepared, probe, root = _seed_raw_context(
        prepared_directory,
        terminal_receipt,
        raw_root=raw_root,
        seed_artifact=seed_artifact,
    )
    with _probe_ledger_lock(root, blocking=False):
        state = _seed_raw_ledger_state(
            root,
            prepared,
            probe_accounting=probe,
            seed_artifact=seed_artifact,
            allow_unclosed_tail=False,
        )
    if expected_reservation is not None and state.get("latest_reservation") != dict(
        expected_reservation
    ):
        raise ExecutionPrepError("checkpoint seed reservation 不是 ledger 唯一最新终点")
    return {
        key: value
        for key, value in state.items()
        if key not in {"unclosed_attempt", "latest_outcome"}
    }


def reconcile_abandoned_seed_raw_attempt(
    prepared_directory: str | Path,
    terminal_receipt: str | Path,
    *,
    raw_root: str | Path,
    seed_artifact: Mapping[str, Any],
    failure_type: str = "AbandonedSeedProcess",
    failure_message: str = "seed 进程在 durable ATTEMPT 后未发布 outcome",
) -> Mapping[str, Any]:
    """将 TERM/KILL 遗留的 seed attempt 闭合为 unknown，reservation 不退款。"""

    prepared, probe, root = _seed_raw_context(
        prepared_directory,
        terminal_receipt,
        raw_root=raw_root,
        seed_artifact=seed_artifact,
    )
    with _probe_ledger_lock(root, blocking=False):
        state = _seed_raw_ledger_state(
            root,
            prepared,
            probe_accounting=probe,
            seed_artifact=seed_artifact,
            allow_unclosed_tail=True,
        )
        unclosed = state.get("unclosed_attempt")
        if unclosed is None:
            return {
                "action": "no_unclosed_seed_attempt",
                **{
                    key: value
                    for key, value in state.items()
                    if key not in {"unclosed_attempt", "latest_outcome"}
                },
            }
        attempt, attempt_ref, reservation = unclosed
        reserved = int(attempt["reserved_new_raw_bytes"])
        outcome = _fingerprinted(
            SEED_RAW_OUTCOME_SCHEMA_VERSION,
            SEED_RAW_OUTCOME_FINGERPRINT_SCHEMA,
            {
                "ledger_id": attempt["ledger_id"],
                "sequence": attempt["sequence"],
                "attempt_id": attempt["attempt_id"],
                "attempt_ref": dict(attempt_ref),
                "seed_reservation_fingerprint_sha256": reservation[
                    "reservation_fingerprint_sha256"
                ],
                "outcome": "failed_or_stopped_reservation_not_refunded",
                "failure": {"type": failure_type, "message": failure_message},
                "checkpoint_ref": None,
                "observed_compressed_bytes_state": (
                    "bounded_after_process_termination"
                ),
                "observed_compressed_bytes": None,
                "observed_compressed_bytes_lower_bound": 0,
                "observed_compressed_bytes_upper_bound": reserved,
                "cumulative_reserved_new_raw_bytes": attempt[
                    "cumulative_reserved_new_raw_bytes"
                ],
                "reservation_refund_policy": (
                    "never_refund_even_on_failure_timeout_or_retry"
                ),
                "database_write_operations": 0,
            },
        )
        relative = _SEED_RAW_OUTCOMES_RELATIVE / _seed_raw_receipt_filename(
            kind="outcome",
            sequence=int(attempt["sequence"]),
            attempt_id=str(attempt["attempt_id"]),
        )
        outcome_ref = _write_probe_receipt(root, relative, outcome)
        closed = _seed_raw_ledger_state(
            root,
            prepared,
            probe_accounting=probe,
            seed_artifact=seed_artifact,
            allow_unclosed_tail=False,
        )
        return {
            "action": "closed_unknown_seed_reservation_not_refunded",
            "outcome_ref": outcome_ref,
            **{
                key: value
                for key, value in closed.items()
                if key not in {"unclosed_attempt", "latest_outcome"}
            },
        }


def reserve_seed_raw_attempt(
    prepared_directory: str | Path,
    terminal_receipt: str | Path,
    *,
    raw_root: str | Path,
    seed_artifact: Mapping[str, Any],
    attempt_id: str | None = None,
) -> Mapping[str, Any]:
    """seed-start 首次 raw open 前发布整份压缩 seed reservation。"""

    prepared, probe, root = _seed_raw_context(
        prepared_directory,
        terminal_receipt,
        raw_root=raw_root,
        seed_artifact=seed_artifact,
    )
    frozen_attempt_id = attempt_id or ("seed_v1_" + secrets.token_hex(16))
    if re.fullmatch(r"seed_v1_[0-9a-f]{32}", frozen_attempt_id) is None:
        raise ExecutionPrepError("seed attempt_id 非法")
    with _probe_ledger_lock(root):
        state = _seed_raw_ledger_state(
            root,
            prepared,
            probe_accounting=probe,
            seed_artifact=seed_artifact,
            allow_unclosed_tail=False,
        )
        prior = int(state["current_cumulative_reserved_new_raw_bytes"])
        seed_identity = _seed_raw_artifact_identity(seed_artifact)
        reserved = int(seed_identity["size_bytes"])
        cumulative = prior + reserved
        if cumulative >= RAW_LIMIT_BYTES:
            raise ExecutionPrepError("seed raw reservation 达到 50GB 排他边界")
        sequence = int(state["attempt_count"]) + 1
        attempt = _fingerprinted(
            SEED_RAW_ATTEMPT_SCHEMA_VERSION,
            SEED_RAW_ATTEMPT_FINGERPRINT_SCHEMA,
            {
                "ledger_id": probe["ledger_id"],
                "sequence": sequence,
                "attempt_id": frozen_attempt_id,
                "prepared_bindings": prepared["full-window-bindings.json"],
                "selection_id": prepared["full-selection.json"]["selection_id"],
                "probe_terminal_accounting_fingerprint_sha256": probe[
                    "accounting_fingerprint_sha256"
                ],
                "probe_terminal_receipt_ref": probe["terminal_receipt_ref"],
                "previous_seed_terminal_ref": state["latest_outcome_ref"],
                "seed_artifact": seed_identity,
                "prior_cumulative_reserved_new_raw_bytes": prior,
                "reserved_new_raw_bytes": reserved,
                "cumulative_reserved_new_raw_bytes": cumulative,
                "reservation_refund_policy": (
                    "never_refund_even_on_failure_timeout_or_retry"
                ),
                "raw_open_authorized_after_this_receipt": True,
                "database_write_operations": 0,
            },
        )
        relative = _SEED_RAW_ATTEMPTS_RELATIVE / _seed_raw_receipt_filename(
            kind="attempt", sequence=sequence, attempt_id=frozen_attempt_id
        )
        attempt_ref = _write_probe_receipt(root, relative, attempt)
        opened = _seed_raw_ledger_state(
            root,
            prepared,
            probe_accounting=probe,
            seed_artifact=seed_artifact,
            allow_unclosed_tail=True,
        )
        unclosed = opened.get("unclosed_attempt")
        if (
            unclosed is None
            or unclosed[1] != attempt_ref
            or opened.get("latest_reservation") != unclosed[2]
        ):
            raise ExecutionPrepError("seed raw attempt 发布后未成为唯一 active reservation")
        return dict(unclosed[2])


def close_seed_raw_attempt(
    prepared_directory: str | Path,
    terminal_receipt: str | Path,
    *,
    raw_root: str | Path,
    seed_artifact: Mapping[str, Any],
    reservation: Mapping[str, Any],
    checkpoint_ref: Mapping[str, Any] | None,
    exact_seed_read: bool,
    failure_type: str | None = None,
    failure_message: str | None = None,
) -> Mapping[str, Any]:
    """关闭当前 seed attempt；失败/未知同样不退还 reservation。"""

    prepared, probe, root = _seed_raw_context(
        prepared_directory,
        terminal_receipt,
        raw_root=raw_root,
        seed_artifact=seed_artifact,
    )
    with _probe_ledger_lock(root):
        state = _seed_raw_ledger_state(
            root,
            prepared,
            probe_accounting=probe,
            seed_artifact=seed_artifact,
            allow_unclosed_tail=True,
        )
        unclosed = state.get("unclosed_attempt")
        if unclosed is None or unclosed[2] != dict(reservation):
            raise ExecutionPrepError("seed close 的 reservation 不是唯一 active attempt")
        attempt, attempt_ref, normalized_reservation = unclosed
        reserved = int(attempt["reserved_new_raw_bytes"])
        if exact_seed_read:
            if not isinstance(checkpoint_ref, Mapping) or set(checkpoint_ref) != {
                "path",
                "checkpoint_sequence",
                "checkpoint_fingerprint_sha256",
            }:
                raise ExecutionPrepError("exact seed outcome 必须绑定已验证 checkpoint")
            _sha(
                checkpoint_ref.get("checkpoint_fingerprint_sha256"),
                "checkpoint_ref.checkpoint_fingerprint_sha256",
            )
            if (
                not isinstance(checkpoint_ref.get("path"), str)
                or not checkpoint_ref["path"]
                or isinstance(checkpoint_ref.get("checkpoint_sequence"), bool)
                or not isinstance(checkpoint_ref.get("checkpoint_sequence"), int)
                or checkpoint_ref["checkpoint_sequence"] < 0
            ):
                raise ExecutionPrepError("exact seed outcome checkpoint ref 非法")
            outcome_kind = "checkpoint_published_seed_read_exact"
            state_name = "exact"
            observed: int | None = reserved
            lower = reserved
            upper = reserved
            failure = None
        else:
            if checkpoint_ref is not None:
                raise ExecutionPrepError("unknown seed outcome 不得声称 checkpoint 绑定")
            outcome_kind = "failed_or_stopped_reservation_not_refunded"
            state_name = "bounded_after_process_termination"
            observed = None
            lower = 0
            upper = reserved
            failure = {
                "type": failure_type or "SeedWorkerDidNotPublishVerifiedCheckpoint",
                "message": failure_message or "seed worker 未发布已验证 checkpoint",
            }
        outcome = _fingerprinted(
            SEED_RAW_OUTCOME_SCHEMA_VERSION,
            SEED_RAW_OUTCOME_FINGERPRINT_SCHEMA,
            {
                "ledger_id": attempt["ledger_id"],
                "sequence": attempt["sequence"],
                "attempt_id": attempt["attempt_id"],
                "attempt_ref": dict(attempt_ref),
                "seed_reservation_fingerprint_sha256": normalized_reservation[
                    "reservation_fingerprint_sha256"
                ],
                "outcome": outcome_kind,
                "failure": failure,
                "checkpoint_ref": (
                    None if checkpoint_ref is None else dict(checkpoint_ref)
                ),
                "observed_compressed_bytes_state": state_name,
                "observed_compressed_bytes": observed,
                "observed_compressed_bytes_lower_bound": lower,
                "observed_compressed_bytes_upper_bound": upper,
                "cumulative_reserved_new_raw_bytes": attempt[
                    "cumulative_reserved_new_raw_bytes"
                ],
                "reservation_refund_policy": (
                    "never_refund_even_on_failure_timeout_or_retry"
                ),
                "database_write_operations": 0,
            },
        )
        relative = _SEED_RAW_OUTCOMES_RELATIVE / _seed_raw_receipt_filename(
            kind="outcome",
            sequence=int(attempt["sequence"]),
            attempt_id=str(attempt["attempt_id"]),
        )
        outcome_ref = _write_probe_receipt(root, relative, outcome)
        closed = _seed_raw_ledger_state(
            root,
            prepared,
            probe_accounting=probe,
            seed_artifact=seed_artifact,
            allow_unclosed_tail=False,
        )
        return {
            "outcome_ref": outcome_ref,
            **{
                key: value
                for key, value in closed.items()
                if key not in {"unclosed_attempt", "latest_outcome"}
            },
        }


def _failure_probe_outcome(
    *,
    attempt: Mapping[str, Any],
    attempt_ref: Mapping[str, Any],
    failure_type: str,
    failure_message: str,
    elapsed_seconds: float | None,
    observed_lower: int,
    observed_upper: int,
    observed_exact: int | None,
    completed_artifacts: Sequence[Mapping[str, Any]] = (),
) -> Mapping[str, Any]:
    state = "exact" if observed_exact is not None else "bounded_after_process_termination"
    return _fingerprinted(
        PROBE_OUTCOME_SCHEMA_VERSION,
        PROBE_OUTCOME_FINGERPRINT_SCHEMA,
        {
            "ledger_id": attempt["ledger_id"],
            "sequence": attempt["sequence"],
            "attempt_ref": dict(attempt_ref),
            "attempt_id": attempt["attempt_id"],
            "outcome": "failed_or_stopped_reservation_not_refunded",
            "completed_artifacts": [dict(row) for row in completed_artifacts],
            "failure": {
                "type": failure_type,
                "message": failure_message,
            },
            "elapsed_seconds": elapsed_seconds,
            "observed_compressed_bytes_state": state,
            "observed_compressed_bytes_sum": observed_exact,
            "observed_compressed_bytes_lower_bound_sum": observed_lower,
            "observed_compressed_bytes_upper_bound_sum": observed_upper,
            "cumulative_reserved_new_raw_bytes": attempt[
                "cumulative_reserved_new_raw_bytes"
            ],
            "next_seed_prior_new_raw_bytes": attempt[
                "cumulative_reserved_new_raw_bytes"
            ],
            "raw_replay_semantics": "parser_compatibility_probe_not_event_replay",
            "reservation_refund_policy": (
                "never_refund_even_on_failure_timeout_or_retry"
            ),
            "database_write_operations": 0,
        },
    )


def _run_probe_worker(args: argparse.Namespace) -> Mapping[str, Any]:
    started = time.monotonic()
    raw_root = _safe_directory(args.raw_root, "raw_root")
    prepared = _load_prepared(args.prepared_directory, raw_root)
    selection = prepared["full-selection.json"]
    updates = _selection_updates(selection)
    indices = _probe_indices(len(updates), args.artifact_index)
    artifacts = tuple(updates[index] for index in indices)
    reserved = sum(int(row["size_bytes"]) for row in artifacts)
    attempt_id = args.attempt_id
    if not isinstance(attempt_id, str) or re.fullmatch(
        r"probe_v1_[0-9a-f]{32}", attempt_id
    ) is None:
        raise ExecutionPrepError("内部 probe attempt_id 非法")
    root = prepared["root"]
    with _probe_ledger_lock(root):
        prior_accounting = _probe_terminal_accounting(
            root, prepared, allow_unclosed_tail=False
        )
        prior = int(prior_accounting["cumulative_reserved_new_raw_bytes"])
        cumulative = prior + reserved
        if cumulative >= RAW_LIMIT_BYTES:
            raise ExecutionPrepError("Native probe reservation 达到 50GB 排他边界")
        sequence = int(prior_accounting["attempt_count"]) + 1
        attempt = _fingerprinted(
            PROBE_ATTEMPT_SCHEMA_VERSION,
            PROBE_ATTEMPT_FINGERPRINT_SCHEMA,
            {
                "ledger_id": prior_accounting["ledger_id"],
                "sequence": sequence,
                "attempt_id": attempt_id,
                "prepared_bindings": prepared["full-window-bindings.json"],
                "selection_id": selection["selection_id"],
                "previous_terminal_ref": prior_accounting["terminal_receipt_ref"],
                "artifact_indices": list(indices),
                "artifacts": [dict(row) for row in artifacts],
                "prior_new_raw_bytes": prior,
                "reserved_new_raw_bytes": reserved,
                "cumulative_reserved_new_raw_bytes": cumulative,
                "reservation_refund_policy": (
                    "never_refund_even_on_failure_timeout_or_retry"
                ),
                "raw_open_authorized_after_this_receipt": True,
                "database_write_operations": 0,
            },
        )
        attempt_relative = _PROBE_ATTEMPTS_RELATIVE / _probe_receipt_filename(
            kind="attempt", sequence=sequence, attempt_id=attempt_id
        )
        attempt_ref = _write_probe_receipt(root, attempt_relative, attempt)
        factory: Any = None
        current_stream: Any = None
        completed: list[Mapping[str, Any]] = []
        try:
            factory = _native_factory(
                raw_root,
                artifacts,
                window=selection["window"],
                max_spool_bytes=args.max_spool_bytes,
                max_frame_bytes=args.native_max_frame_bytes,
            )
            contract = prepared["native-parser-contract.json"]
            _validate_generated_parser_attestation(
                factory.parser_attestation, contract=contract
            )
            for index, artifact in zip(indices, artifacts):
                current_stream = factory(dict(artifact))
                physical = 0
                elements = 0
                for record in current_stream:
                    physical += 1
                    elements += len(record.elements)
                    if physical % PROBE_RUNTIME_CHECK_RECORDS == 0 and (
                        time.monotonic() - started >= args.soft_timeout_seconds
                    ):
                        raise ExecutionPrepError("Native probe 达到软停边界")
                statistics = current_stream.statistics
                if (
                    statistics.get("status") != "complete"
                    or statistics.get("compressed_bytes_read_observed")
                    != artifact["size_bytes"]
                    or statistics.get("compressed_read_passes") != 1
                ):
                    raise ExecutionPrepError("Native probe 未形成完整 single-pass proof")
                completed.append(
                    {
                        "artifact_index": index,
                        "artifact_id": artifact["artifact_id"],
                        "physical_record_count": physical,
                        "route_element_count": elements,
                        "statistics": statistics,
                    }
                )
                current_stream = None
            elapsed = time.monotonic() - started
            if elapsed >= args.soft_timeout_seconds:
                raise ExecutionPrepError("Native probe 在完成边界达到软停")
            observed = sum(
                int(row["statistics"]["compressed_bytes_read_observed"])
                for row in completed
            )
            outcome = _fingerprinted(
                PROBE_OUTCOME_SCHEMA_VERSION,
                PROBE_OUTCOME_FINGERPRINT_SCHEMA,
                {
                    "ledger_id": attempt["ledger_id"],
                    "sequence": sequence,
                    "attempt_ref": attempt_ref,
                    "attempt_id": attempt_id,
                    "outcome": "complete_single_pass",
                    "completed_artifacts": completed,
                    "failure": None,
                    "elapsed_seconds": elapsed,
                    "observed_compressed_bytes_state": "exact",
                    "observed_compressed_bytes_sum": observed,
                    "observed_compressed_bytes_lower_bound_sum": observed,
                    "observed_compressed_bytes_upper_bound_sum": observed,
                    "cumulative_reserved_new_raw_bytes": cumulative,
                    "next_seed_prior_new_raw_bytes": cumulative,
                    "raw_replay_semantics": (
                        "parser_compatibility_probe_not_event_replay"
                    ),
                    "reservation_refund_policy": (
                        "never_refund_even_on_failure_timeout_or_retry"
                    ),
                    "database_write_operations": 0,
                },
            )
            outcome_relative = _PROBE_OUTCOMES_RELATIVE / _probe_receipt_filename(
                kind="outcome", sequence=sequence, attempt_id=attempt_id
            )
            outcome_ref = _write_probe_receipt(root, outcome_relative, outcome)
            terminal = _probe_terminal_accounting(
                root,
                prepared,
                supplied_terminal=root.joinpath(
                    *_safe_prepared_relative(
                        outcome_ref["path"], "outcome_ref.path"
                    ).parts
                ),
                allow_unclosed_tail=False,
            )
            throughput = _publish_probe_throughput_receipt(
                root,
                prepared=prepared,
                outcome=outcome,
                outcome_ref=outcome_ref,
                terminal_accounting=terminal,
            )
            return {
                "ok": True,
                "command": "probe-native",
                "attempt_ref": attempt_ref,
                "outcome_ref": outcome_ref,
                "artifact_indices": list(indices),
                "completed_artifact_count": len(completed),
                "probe_terminal_accounting": _public_probe_accounting(terminal),
                "probe_throughput_receipt_path": throughput["path"],
                "probe_throughput_receipt_ref": throughput["ref"],
                "conservative_bytes_per_second": throughput["receipt"][
                    "conservative_bytes_per_second"
                ],
                "next_seed_prior_new_raw_bytes": cumulative,
                "database_write_operations": 0,
            }
        except BaseException as error:
            completed_observed = sum(
                int(row["statistics"]["compressed_bytes_read_observed"])
                for row in completed
            )
            observed_current: int | None = None
            if current_stream is not None:
                value = current_stream.statistics.get(
                    "compressed_bytes_read_observed"
                )
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    observed_current = value
            exact = (
                completed_observed + observed_current
                if observed_current is not None
                else None
            )
            failure = _failure_probe_outcome(
                attempt=attempt,
                attempt_ref=attempt_ref,
                failure_type=type(error).__name__,
                failure_message=str(error),
                elapsed_seconds=time.monotonic() - started,
                observed_lower=completed_observed,
                observed_upper=(exact if exact is not None else reserved),
                observed_exact=exact,
                completed_artifacts=completed,
            )
            outcome_relative = _PROBE_OUTCOMES_RELATIVE / _probe_receipt_filename(
                kind="outcome", sequence=sequence, attempt_id=attempt_id
            )
            try:
                _write_probe_receipt(root, outcome_relative, failure)
            except (FileExistsError, OSError, ValueError):
                pass
            raise


def _probe_child_command(
    args: argparse.Namespace, *, capability: str, attempt_id: str
) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "__probe-worker",
        "--supervisor-capability",
        capability,
        "--prepared-directory",
        str(args.prepared_directory),
        "--raw-root",
        str(args.raw_root),
        "--attempt-id",
        attempt_id,
        "--soft-timeout-seconds",
        str(args.soft_timeout_seconds),
        "--max-spool-bytes",
        str(args.max_spool_bytes),
        "--native-max-frame-bytes",
        str(args.native_max_frame_bytes),
    ]
    for index in args.artifact_index:
        command.extend(("--artifact-index", str(index)))
    return command


def _reconcile_probe_attempt(
    args: argparse.Namespace,
    *,
    attempt_id: str | None,
    failure_type: str,
    failure_message: str,
) -> Mapping[str, Any]:
    raw_root = _safe_directory(args.raw_root, "raw_root")
    prepared = _load_prepared(args.prepared_directory, raw_root)
    root = prepared["root"]
    with _probe_ledger_lock(root, blocking=False):
        accounting = _probe_terminal_accounting(
            root, prepared, allow_unclosed_tail=True
        )
        unclosed = accounting.get("unclosed_attempt")
        if unclosed is None:
            return {
                "action": "no_unclosed_attempt",
                "probe_terminal_accounting": _public_probe_accounting(accounting),
            }
        attempt, attempt_ref = unclosed
        if attempt_id is not None and attempt.get("attempt_id") != attempt_id:
            raise ExecutionPrepError("待 reconcile attempt 与监督器 attempt_id 不一致")
        reserved = int(attempt["reserved_new_raw_bytes"])
        outcome = _failure_probe_outcome(
            attempt=attempt,
            attempt_ref=attempt_ref,
            failure_type=failure_type,
            failure_message=failure_message,
            elapsed_seconds=None,
            observed_lower=0,
            observed_upper=reserved,
            observed_exact=None,
        )
        relative = _PROBE_OUTCOMES_RELATIVE / _probe_receipt_filename(
            kind="outcome",
            sequence=int(attempt["sequence"]),
            attempt_id=str(attempt["attempt_id"]),
        )
        outcome_ref = _write_probe_receipt(root, relative, outcome)
        terminal = _probe_terminal_accounting(
            root,
            prepared,
            supplied_terminal=root.joinpath(
                *_safe_prepared_relative(
                    outcome_ref["path"], "outcome_ref.path"
                ).parts
            ),
            allow_unclosed_tail=False,
        )
        return {
            "action": "closed_unknown_interval_reservation_not_refunded",
            "outcome_ref": outcome_ref,
            "probe_terminal_accounting": _public_probe_accounting(terminal),
        }


def _supervise_probe(args: argparse.Namespace) -> Mapping[str, Any]:
    soft = float(args.soft_timeout_seconds)
    if not 0 < soft <= 540:
        raise ExecutionPrepError("probe soft timeout 必须位于 (0,540]")
    # 上一监督器若在 durable ATTEMPT 后自身崩溃，下一次执行先在无 active
    # execution lock 的条件下补一条 unknown interval outcome；reservation 不退款。
    _reconcile_probe_attempt(
        args,
        attempt_id=None,
        failure_type="AbandonedSupervisor",
        failure_message="上一次 probe 在 durable ATTEMPT 后未发布 terminal outcome",
    )
    attempt_id = "probe_v1_" + secrets.token_hex(16)
    capability = _supervisor_capability()
    process = subprocess.Popen(
        _probe_child_command(args, capability=capability, attempt_id=attempt_id),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=_worker_environment(capability),
    )
    started = time.monotonic()
    try:
        stdout, stderr = process.communicate(timeout=soft)
    except subprocess.TimeoutExpired:
        _signal_process_tree(process, signal.SIGTERM)
        hard_killed = _wait_for_group_after_term(
            process,
            hard_deadline=started + PROBE_HARD_SECONDS,
            clock=time.monotonic,
            sleeper=time.sleep,
        )
        stage = "600 秒硬杀" if hard_killed else f"{soft:g} 秒软停"
        reconciled = _reconcile_probe_attempt(
            args,
            attempt_id=attempt_id,
            failure_type="SupervisorTimeout",
            failure_message=stage,
        )
        raise ExecutionPrepError(
            "Native probe 达到"
            f"{stage}；ATTEMPT reservation 不退款；reconcile={reconciled['action']}"
        )
    if process.returncode != 0:
        detail = stderr.strip()[-4000:] or stdout.strip()[-4000:]
        reconciled = _reconcile_probe_attempt(
            args,
            attempt_id=attempt_id,
            failure_type="WorkerExitedWithoutTerminalOutcome",
            failure_message=detail or "worker 非零退出",
        )
        raise ExecutionPrepError(
            "Native probe worker 失败；reservation 不退款；"
            f"reconcile={reconciled['action']}；{detail}"
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise ExecutionPrepError("Native probe worker 输出不是 JSON") from error
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        raise ExecutionPrepError("Native probe worker 未返回成功")
    return dict(payload)


def _add_probe_arguments(
    parser: argparse.ArgumentParser, *, internal: bool = False
) -> None:
    parser.add_argument("--prepared-directory", required=True)
    parser.add_argument("--raw-root", required=True)
    if internal:
        parser.add_argument("--attempt-id", required=True)
    parser.add_argument(
        "--artifact-index", type=int, action="append", default=[]
    )
    parser.add_argument(
        "--soft-timeout-seconds", type=float, default=PROBE_DEFAULT_SOFT_SECONDS
    )
    parser.add_argument("--max-spool-bytes", type=int, default=DEFAULT_MAX_SPOOL_BYTES)
    parser.add_argument(
        "--native-max-frame-bytes", type=int, default=64 * 1024 * 1024
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RRC25 伊朗完整窗口执行输入冻结与 Native 小样本探针"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    freeze_prior = commands.add_parser(
        "freeze-prior-accounting",
        help=(
            "只读核验历史 run metadata 并 create-only 冻结 conservative prior receipt"
        ),
    )
    freeze_prior.add_argument("--source-run-path", required=True)
    freeze_prior.add_argument(
        "--source-metadata-ref", action="append", required=True
    )
    freeze_prior.add_argument(
        "--source-metadata-ref-sha256", action="append", required=True
    )
    freeze_prior.add_argument(
        "--derivation-evidence",
        required=True,
        help="从冻结来源 JSON 推导 lower/upper 的规则证据 JSON",
    )
    freeze_prior.add_argument(
        "--derivation-evidence-sha256",
        required=True,
        help="derivation evidence 的调用方冻结 SHA256",
    )
    freeze_prior.add_argument("--codex-task-id", required=True)
    freeze_prior.add_argument("--frozen-at-utc", required=True)
    freeze_prior.add_argument(
        "--history-limitation-zh",
        default=(
            "pre-ledger 历史 raw 读取不能逐次拆分；"
            "lower 是可验证下界，upper 是冻结保守上界，不能表述为精确实测值。"
        ),
    )
    freeze_prior.add_argument(
        "--output-directory",
        required=True,
        help="既有安全目录；收据文件名由内容指纹唯一确定",
    )
    prepare = commands.add_parser("prepare", help="冻结完整窗口执行输入，不打开 raw")
    prepare.add_argument("--profile", required=True)
    prepare.add_argument("--manifest", required=True)
    prepare.add_argument("--manifest-verification", required=True)
    prepare.add_argument("--compatible-mapping", required=True)
    prepare.add_argument("--revised-mapping", required=True)
    prepare.add_argument("--seed-spool-attestation", required=True)
    prepare.add_argument("--source-fact", required=True)
    prepare.add_argument("--incident-policy", required=True)
    prepare.add_argument("--claim-inventory", required=True)
    prepare.add_argument("--raw-root", required=True)
    prepare.add_argument("--output-directory", required=True)
    prepare.add_argument(
        "--prior-accounting-receipt",
        help=(
            "已有研究的 create-only pre-ledger accounting import receipt；"
            "值只从收据读取，不接受累计数字参数"
        ),
    )
    prepare.add_argument(
        "--prior-accounting-receipt-sha256",
        help="调用方冻结的 prior accounting receipt 文件 SHA256",
    )
    prepare.add_argument(
        "--new-task-zero-genesis",
        action="store_true",
        help=(
            "仅全新且确认无任何历史 raw 读取的 Study 允许显式使用；"
            "本伊朗 Study 固定拒绝 zero genesis"
        ),
    )
    prepare.add_argument("--max-spool-bytes", type=int, default=DEFAULT_MAX_SPOOL_BYTES)
    prepare.add_argument(
        "--native-max-frame-bytes", type=int, default=64 * 1024 * 1024
    )
    probe = commands.add_parser(
        "probe-native",
        help=(
            "在 prepared 内不可变 raw ledger 上监督执行最多五个 UPDATE；"
            "累计值只从唯一 terminal 推导"
        ),
    )
    _add_probe_arguments(probe)
    reconcile = commands.add_parser(
        "reconcile-probe-ledger",
        help="不打开 raw，把已死亡 worker 的 durable ATTEMPT 闭合为 unknown outcome",
    )
    reconcile.add_argument("--prepared-directory", required=True)
    reconcile.add_argument("--raw-root", required=True)
    verify_ledger = commands.add_parser(
        "verify-probe-ledger",
        help="只读核验 probe create-only ledger 及唯一 terminal ref/SHA",
    )
    verify_ledger.add_argument("--prepared-directory", required=True)
    verify_ledger.add_argument("--raw-root", required=True)
    verify_ledger.add_argument("--terminal-receipt", required=True)
    return parser


def _build_probe_worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    _add_probe_arguments(parser, internal=True)
    return parser


def _print(payload: Mapping[str, Any], *, stream: Any = sys.stdout) -> None:
    stream.write(canonical_json(dict(payload)) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    internal = bool(raw_argv and raw_argv[0] == "__probe-worker")
    try:
        parsed_argv = (
            _verified_internal_worker_argv(raw_argv) if internal else raw_argv
        )
        args = (
            _build_probe_worker_parser().parse_args(parsed_argv)
            if internal
            else build_parser().parse_args(parsed_argv)
        )
        if internal:
            result = _run_probe_worker(args)
        elif args.command == "freeze-prior-accounting":
            result = _run_freeze_prior_accounting(args)
        elif args.command == "prepare":
            result = _run_prepare(args)
        elif args.command == "probe-native":
            result = _supervise_probe(args)
        elif args.command == "reconcile-probe-ledger":
            reconciled = _reconcile_probe_attempt(
                args,
                attempt_id=None,
                failure_type="ExplicitReconcile",
                failure_message="显式闭合已死亡 worker 遗留的 durable ATTEMPT",
            )
            result = {
                "ok": True,
                "command": "reconcile-probe-ledger",
                **reconciled,
                "raw_mrt_files_opened": 0,
                "database_write_operations": 0,
            }
        elif args.command == "verify-probe-ledger":
            result = {
                "ok": True,
                "command": "verify-probe-ledger",
                "probe_terminal_accounting": verify_probe_raw_ledger_terminal(
                    args.prepared_directory,
                    args.terminal_receipt,
                    raw_root=args.raw_root,
                ),
                "raw_mrt_files_opened": 0,
                "database_write_operations": 0,
            }
        else:
            raise ExecutionPrepError("未知子命令")
        _print(result)
        return 0
    except (
        ExecutionPrepError,
        FileExistsError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        _print(
            {
                "ok": False,
                "error_type": type(error).__name__,
                "message_zh": str(error),
            },
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
