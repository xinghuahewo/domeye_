#!/usr/bin/env python3
"""P0 固定数据档的离线、只读数据质量门禁。

本程序只读取已经生成的 D2/D3/RouteEvent/Evidence/Metric 制品，不连接数据库，
不读取原始 MRT，也不修改输入。D2 旧候选尚未携带的逐项质量计数由本程序从
``incidents/links/quarantine`` 确定性流式复核；缺少可选对账摘要时交给纯函数
门禁失败关闭，绝不把缺字段猜成零。
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import sys
from types import ModuleType
from typing import Any, Callable, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GATE_RELATIVE_PATH = Path("backend/data_pipeline/quality/gate.py")
SCHEMA_RELATIVE_PATH = Path("contracts/data/data-quality-report.schema.json")
METRIC_SCHEMA_RELATIVE_PATH = Path("contracts/data/metric-series.schema.json")
CLI_RELATIVE_PATH = Path("dev/data_quality/p0_quality_gate.py")
D2_RUNNER_RELATIVE_PATH = Path("dev/data_quality/p0_normalize_candidate.py")
D3_SCANNER_RELATIVE_PATH = Path("backend/data_pipeline/route_event/artifacts.py")
D3_CLI_RELATIVE_PATH = Path("dev/data_quality/p0_artifact_manifest.py")
ASSURANCE_RELATIVE_PATH = Path("dev/data_quality/p0_single_run_assurance.py")
REPRODUCIBILITY_RELATIVE_PATH = Path("dev/data_quality/p0_reproducibility.py")

FROZEN_PROFILE = {
    "schema_version": 1,
    "id": "feb-mar-2026",
    "mode": "fixed",
    "timezone": "Asia/Shanghai",
    "window_start": "2026-02-01T00:00:00+08:00",
    "window_end_exclusive": "2026-04-01T00:00:00+08:00",
    "snapshot_time": "2026-03-31T23:59:59+08:00",
    "api_profile": "core",
}
EVENT_TYPES = (
    "hijack",
    "sub_hijack",
    "leak",
    "prefix_outage",
    "as_outage",
    "country_outage",
)
FACT_FAMILIES = {
    "hijack": "hijack",
    "sub_hijack": "sub_hijack",
    "leak": "leak_event",
    "prefix_outage": "prefix_outage",
    "as_outage": "as_outage",
    "country_outage": "country_outage",
}
D2_JSONL_FILES = (
    "incidents.jsonl.gz",
    "links.jsonl.gz",
    "collision_groups.jsonl.gz",
    "quarantine.jsonl.gz",
)
OUTPUT_INPUT_NAMES = {
    "d3": "d3-artifact-manifest.json",
    "d3_verification": "d3-artifact-verification-summary.json",
    "route": "route-event-reconciliation-summary.json",
    "metric": "metric-reconciliation-summary.json",
    "repro": "reproducibility-summary.json",
    "execution": "quality-gate-execution-context.json",
    "profile": "data-profile.json",
}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
INCIDENT_ID_RE = re.compile(r"^inc_v1_[0-9a-f]{24}$")
QUARANTINE_ID_RE = re.compile(r"^qr_v1_[0-9a-f]{32}$")
COLLISION_ID_RE = re.compile(r"^lcg_v1_[0-9a-f]{32}$")
DETAIL_TIME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$")
UTC_SECOND_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
ASN_RE = re.compile(r"^(0|[1-9][0-9]*)$")
REASON_RE = re.compile(r"^[a-z][a-z0-9_]*$")

MAX_JSON_BYTES = 512 * 1024 * 1024
MAX_JSONL_LINE_BYTES = 32 * 1024 * 1024
FAILURE_SAMPLE_LIMIT = 20
UTC = timezone.utc
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")

MISSING_PHASE_STATES = {
    "not_applicable",
    "source_fact_collision",
    "not_retained",
    "legacy_unknown",
}
OBSERVED_PHASE_STATES = {"observed_no_path_in_snapshot", "observed_paths"}
ALLOWED_PHASE_STATES = MISSING_PHASE_STATES | OBSERVED_PHASE_STATES
MISSING_FIELD_STATES = {
    "not_applicable",
    "source_fact_collision",
    "not_retained",
    "legacy_unknown",
}
NULLABLE_DERIVED_FIELDS = (
    "end_time_utc",
    "duration_seconds",
    "risk_level",
    "detector_version",
)
ASN_SOURCE_FIELDS = {"asn", "hijacked_as", "leak_by", "leak_to", "outage_ases"}
PREFIX_SOURCE_FIELDS = {"prefix", "hijacked_prefix", "sub_prefix", "outage_prefixes"}


class QualityCliError(RuntimeError):
    """输入闭包或文件安全边界不满足，不能生成可信报告。"""


def _reject_json_constant(value: str) -> None:
    raise QualityCliError(f"禁止非有限 JSON 常量：{value}")


def _unique_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise QualityCliError(f"JSON 存在重复字段：{key}")
        result[key] = value
    return result


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_bytes(value: Any) -> bytes:
    return (_canonical_json(value) + "\n").encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _lstat_regular(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise QualityCliError(f"无法读取{label}：{path}") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise QualityCliError(f"{label}不得是符号链接：{path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise QualityCliError(f"{label}必须是普通文件：{path}")
    return metadata


def _lstat_directory(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise QualityCliError(f"无法读取{label}：{path}") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise QualityCliError(f"{label}不得是符号链接：{path}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise QualityCliError(f"{label}必须是目录：{path}")
    return metadata


def _read_regular(path: Path, label: str, *, maximum_bytes: int = MAX_JSON_BYTES) -> bytes:
    initial = _lstat_regular(path, label)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise QualityCliError(f"无法只读打开{label}：{path}") from error
    chunks = []
    total = 0
    try:
        before = os.fstat(descriptor)
        immutable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if not stat.S_ISREG(before.st_mode) or any(
            getattr(initial, field) != getattr(before, field) for field in immutable
        ):
            raise QualityCliError(f"打开前{label}发生变化：{path}")
        while True:
            block = os.read(descriptor, 128 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum_bytes:
                raise QualityCliError(f"{label}超过 {maximum_bytes} 字节限制")
            chunks.append(block)
        after = os.fstat(descriptor)
        if any(getattr(before, field) != getattr(after, field) for field in immutable):
            raise QualityCliError(f"读取期间{label}发生变化：{path}")
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def _sha256_file(path: Path, label: str) -> Tuple[str, int]:
    initial = _lstat_regular(path, label)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise QualityCliError(f"无法只读打开{label}：{path}") from error
    digest = hashlib.sha256()
    total = 0
    try:
        before = os.fstat(descriptor)
        immutable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if not stat.S_ISREG(before.st_mode) or any(
            getattr(initial, field) != getattr(before, field) for field in immutable
        ):
            raise QualityCliError(f"打开前{label}发生变化：{path}")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            total += len(block)
        after = os.fstat(descriptor)
        if any(getattr(before, field) != getattr(after, field) for field in immutable):
            raise QualityCliError(f"读取期间{label}发生变化：{path}")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), total


def _load_json(path: Path, label: str) -> Tuple[Dict[str, Any], bytes, str]:
    payload = _read_regular(path, label)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise QualityCliError(f"{label}必须是严格 UTF-8") from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as error:
        raise QualityCliError(f"{label}不是合法 JSON：{error.msg}") from error
    if not isinstance(value, dict):
        raise QualityCliError(f"{label}顶层必须是 JSON 对象")
    return value, payload, hashlib.sha256(payload).hexdigest()


def _checksum_index(path: Path, label: str) -> Dict[str, str]:
    payload = _read_regular(path, label, maximum_bytes=16 * 1024 * 1024)
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise QualityCliError(f"{label}必须是 UTF-8") from error
    if not lines or payload[-1:] != b"\n":
        raise QualityCliError(f"{label}必须非空并以换行结尾")
    result: Dict[str, str] = {}
    for line_number, line in enumerate(lines, 1):
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\\r\n]+)", line)
        if match is None or match.group(2) in ("", ".", ".."):
            raise QualityCliError(f"{label}第 {line_number} 行格式非法")
        name = match.group(2)
        if name in result:
            raise QualityCliError(f"{label}重复列出文件：{name}")
        result[name] = match.group(1)
    return result


def _verify_checksum(path: Path, index: Mapping[str, str], label: str) -> str:
    expected = index.get(path.name)
    if expected is None:
        raise QualityCliError(f"{label}未进入 SHA256SUMS 闭包：{path.name}")
    actual, _ = _sha256_file(path, label)
    if actual != expected:
        raise QualityCliError(f"{label} SHA256 与 SHA256SUMS 不一致：{path.name}")
    return actual


def _verify_checksum_closure(directory: Path, index: Mapping[str, str], label: str) -> None:
    for name, expected in sorted(index.items()):
        path = directory / name
        actual, _ = _sha256_file(path, f"{label}闭包文件")
        if actual != expected:
            raise QualityCliError(f"{label}闭包文件 SHA256 不一致：{name}")


def _verified_flat_candidate_closure(
    checksum_path: Path,
    index: Mapping[str, str],
    label: str,
    *,
    already_streamed: Iterable[str] = (),
) -> Dict[str, Any]:
    """复核扁平候选的精确文件集合并返回可比较闭包身份。"""

    if checksum_path.name != "SHA256SUMS":
        raise QualityCliError(f"{label}校验文件必须命名为 SHA256SUMS")
    directory = checksum_path.parent
    _lstat_directory(directory, f"{label}目录")
    try:
        entries = list(os.scandir(directory))
    except OSError as error:
        raise QualityCliError(f"无法枚举{label}目录：{directory}") from error
    actual_names = set()
    sizes: Dict[str, int] = {}
    for entry in entries:
        metadata = entry.stat(follow_symlinks=False)
        if entry.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise QualityCliError(f"{label}只允许顶层普通文件：{entry.name}")
        actual_names.add(entry.name)
        sizes[entry.name] = metadata.st_size
    expected_names = set(index) | {"SHA256SUMS"}
    if actual_names != expected_names:
        raise QualityCliError(
            f"{label}文件集合不闭合；缺少={sorted(expected_names - actual_names)}，"
            f"多出={sorted(actual_names - expected_names)}"
        )
    streamed = set(already_streamed)
    if not streamed.issubset(index):
        raise QualityCliError(f"{label}流式复核文件不在 SHA256SUMS 中")
    for name, expected in sorted(index.items()):
        if name in streamed:
            continue
        actual, _ = _sha256_file(directory / name, f"{label}闭包文件")
        if actual != expected:
            raise QualityCliError(f"{label}闭包文件 SHA256 不一致：{name}")
    checksum_sha, _ = _sha256_file(checksum_path, f"{label} SHA256SUMS")
    return {
        "sha256sums_sha256": checksum_sha,
        "signed_file_count": len(index),
        "signed_size_bytes": sum(sizes[name] for name in index),
        "verified": True,
    }


def _require_same_candidate_directory(
    paths: Iterable[Path], checksum_path: Path, label: str
) -> None:
    try:
        checksum_parent = checksum_path.parent.resolve(strict=True)
        resolved_parents = {path.parent.resolve(strict=True) for path in paths}
    except OSError as error:
        raise QualityCliError(f"{label}候选目录无法解析") from error
    if resolved_parents != {checksum_parent}:
        raise QualityCliError(f"{label} manifest/摘要/SHA256SUMS 必须位于同一候选目录")


def _load_gate_module(pipeline_root: Path) -> Tuple[ModuleType, str]:
    _lstat_directory(pipeline_root, "pipeline-root")
    gate_path = pipeline_root / GATE_RELATIVE_PATH
    _lstat_regular(gate_path, "质量门禁核心")
    resolved = gate_path.resolve(strict=True)
    module_name = "domeye_p0_quality_gate_" + hashlib.sha256(
        os.fsencode(resolved)
    ).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise QualityCliError("无法创建质量门禁核心导入规范")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        sys.modules.pop(module_name, None)
        raise QualityCliError("无法导入质量门禁核心") from error
    for name in ("build_quality_report", "validate_report_semantics", "canonical_json"):
        if not callable(getattr(module, name, None)):
            raise QualityCliError(f"质量门禁核心缺少函数：{name}")
    digest, _ = _sha256_file(gate_path, "质量门禁核心")
    return module, digest


def _parse_utc_second(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or UTC_SECOND_RE.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return parsed


def _parse_detail_reference(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, str) or value != value.strip():
        return None
    parts = value.split("/")
    if len(parts) != 5:
        return None
    event_type, local_text, problem, event_id_text, source = parts
    if (
        event_type not in EVENT_TYPES
        or DETAIL_TIME_RE.fullmatch(local_text) is None
        or not problem
        or not event_id_text.isdigit()
        or not source
    ):
        return None
    try:
        local = datetime.strptime(local_text, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=BUSINESS_TIMEZONE
        )
    except ValueError:
        return None
    identity = {
        "schema": "incident_id_v1",
        "event_type": event_type,
        "start_time": local_text,
        "problem": problem,
        "event_id": int(event_id_text),
        "source": source,
    }
    return {
        "event_type": event_type,
        "local": local,
        "event_time_utc": local.astimezone(UTC),
        "incident_id": "inc_v1_" + _canonical_sha256(identity)[:24],
        "source": source,
        "source_table": f"{FACT_FAMILIES[event_type]}_{local.strftime('%Y%m')}",
    }


def _primary_key_text(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value:
        return value
    if value is not None:
        try:
            return _canonical_json(value)
        except (TypeError, ValueError):
            pass
    return fallback


class _Audit:
    """流式审计的临时本地 ID 索引。

    SQLite 文件只存在于尚未发布的全新 staging 目录，成功前删除；它不是来源
    PostgreSQL，也不改变任何候选输入，因此不计入来源数据库写操作。
    """

    def __init__(self, sqlite_path: Path, *, window_start: datetime, window_end: datetime):
        self.connection = sqlite3.connect(str(sqlite_path))
        self.connection.execute(
            "CREATE TABLE incidents (incident_id TEXT PRIMARY KEY, row_sha TEXT NOT NULL, line_no INTEGER NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE links (incident_id TEXT PRIMARY KEY, row_sha TEXT NOT NULL, line_no INTEGER NOT NULL, status TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE sidecar_ids (kind TEXT NOT NULL, stable_id TEXT NOT NULL, line_no INTEGER NOT NULL, PRIMARY KEY(kind,stable_id))"
        )
        self.window_start = window_start
        self.window_end = window_end
        self.counts = {
            "stable_id_conflict_count": 0,
            "end_before_start_count": 0,
            "local_utc_unverifiable_count": 0,
            "invalid_asn_count": 0,
            "invalid_prefix_count": 0,
            "unknown_derived_null_count": 0,
            "confirmed_missing_zero_fill_count": 0,
            "visible_outside_window_count": 0,
            "phase_state_missing_count": 0,
            "phase_missing_reason_count": 0,
        }
        self.failure_samples: Dict[str, list[Dict[str, Any]]] = {}
        self.event_type_counts = {event_type: 0 for event_type in EVENT_TYPES}
        self.fact_link_status_counts: Dict[str, int] = {}
        self.forward_unresolved_count = 0

    def close(self) -> None:
        self.connection.close()

    def failure(
        self,
        metric: str,
        check_id: str,
        *,
        filename: str,
        line_number: int,
        table: str,
        primary_key: Any,
        field: str,
        reason: str,
        event_time: Optional[str] = None,
    ) -> None:
        self.counts[metric] += 1
        rows = self.failure_samples.setdefault(check_id, [])
        if len(rows) >= FAILURE_SAMPLE_LIMIT:
            return
        if REASON_RE.fullmatch(reason) is None:
            raise AssertionError(f"内部原因码非法：{reason}")
        rows.append(
            {
                "source_ref": f"{filename}:{line_number}",
                "table": table,
                "primary_key": _primary_key_text(primary_key, f"line:{line_number}"),
                "field": field,
                "event_time": event_time if _parse_utc_second(event_time) is not None else None,
                "reason_codes": [reason],
                "evidence_locator": f"{filename}#L{line_number}",
            }
        )

    def _identity_failure(
        self,
        kind: str,
        *,
        filename: str,
        line_number: int,
        incident_id: Any,
        field: str,
        event_time: Optional[str],
    ) -> None:
        metric = "invalid_asn_count" if kind == "asn" else "invalid_prefix_count"
        self.failure(
            metric,
            "completeness-entity-identities",
            filename=filename,
            line_number=line_number,
            table="normalized_incident",
            primary_key=incident_id,
            field=field,
            reason="invalid_identity",
            event_time=event_time,
        )

    def incident(self, row: Mapping[str, Any], line_number: int, raw_line: bytes) -> None:
        filename = "incidents.jsonl.gz"
        incident_id = row.get("incident_id")
        event_time_text = row.get("event_time_utc")
        event_time = _parse_utc_second(event_time_text)
        locator = _parse_detail_reference(row.get("detail_reference"))
        if row.get("classification") != "observation_only" or row.get("causal_conclusion") is not None:
            raise QualityCliError(f"{filename} 第 {line_number} 行违反 observation_only 边界")
        stable_ok = (
            isinstance(incident_id, str)
            and INCIDENT_ID_RE.fullmatch(incident_id) is not None
            and row.get("incident_id_schema") == "incident_id_v1"
            and locator is not None
            and locator["incident_id"] == incident_id
            and row.get("event_type") == locator["event_type"]
            and row.get("source_code") == locator["source"]
            and row.get("source_table") == locator["source_table"]
        )
        if not stable_ok:
            self.failure(
                "stable_id_conflict_count",
                "uniqueness-stable-ids",
                filename=filename,
                line_number=line_number,
                table="normalized_incident",
                primary_key=incident_id,
                field="incident_id",
                reason="stable_id_conflict",
                event_time=event_time_text,
            )
        if isinstance(incident_id, str) and incident_id:
            try:
                self.connection.execute(
                    "INSERT INTO incidents(incident_id,row_sha,line_no) VALUES(?,?,?)",
                    (incident_id, hashlib.sha256(raw_line).hexdigest(), line_number),
                )
            except sqlite3.IntegrityError:
                self.failure(
                    "stable_id_conflict_count",
                    "uniqueness-stable-ids",
                    filename=filename,
                    line_number=line_number,
                    table="normalized_incident",
                    primary_key=incident_id,
                    field="incident_id",
                    reason="stable_id_conflict",
                    event_time=event_time_text,
                )

        if (
            event_time is None
            or locator is None
            or event_time != locator["event_time_utc"]
        ):
            self.failure(
                "local_utc_unverifiable_count",
                "time-local-utc-verifiable",
                filename=filename,
                line_number=line_number,
                table="normalized_incident",
                primary_key=incident_id,
                field="event_time_utc",
                reason="utc_conversion_unverifiable",
            )
        if event_time is not None and not self.window_start <= event_time < self.window_end:
            self.failure(
                "visible_outside_window_count",
                "window-outside-visible-records",
                filename=filename,
                line_number=line_number,
                table="normalized_incident",
                primary_key=incident_id,
                field="event_time_utc",
                reason="outside_fixed_window",
                event_time=event_time_text,
            )

        end_text = row.get("end_time_utc")
        end_time = None if end_text is None else _parse_utc_second(end_text)
        if end_text is not None and end_time is None:
            self.failure(
                "local_utc_unverifiable_count",
                "time-local-utc-verifiable",
                filename=filename,
                line_number=line_number,
                table="normalized_incident",
                primary_key=incident_id,
                field="end_time_utc",
                reason="utc_conversion_unverifiable",
                event_time=event_time_text,
            )
        if event_time is not None and end_time is not None and end_time < event_time:
            self.failure(
                "end_before_start_count",
                "time-end-before-start",
                filename=filename,
                line_number=line_number,
                table="normalized_incident",
                primary_key=incident_id,
                field="end_time_utc",
                reason="end_before_start",
                event_time=event_time_text,
            )

        event_type = row.get("event_type")
        if event_type in self.event_type_counts:
            self.event_type_counts[event_type] += 1

        seen_invalid = set()
        affected = row.get("affected_objects")
        if not isinstance(affected, list):
            raise QualityCliError(f"{filename} 第 {line_number} 行 affected_objects 非数组")
        for index, item in enumerate(affected):
            if not isinstance(item, Mapping):
                raise QualityCliError(f"{filename} 第 {line_number} 行 affected_objects[{index}] 非对象")
            object_type = item.get("object_type")
            object_id = item.get("object_id")
            invalid = False
            if object_type == "asn":
                invalid = not isinstance(object_id, str) or ASN_RE.fullmatch(object_id) is None
            elif object_type == "prefix":
                if not isinstance(object_id, str):
                    invalid = True
                else:
                    try:
                        invalid = str(ipaddress.ip_network(object_id, strict=True)) != object_id
                    except ValueError:
                        invalid = True
            if invalid and (object_type, object_id, index) not in seen_invalid:
                seen_invalid.add((object_type, object_id, index))
                self._identity_failure(
                    "asn" if object_type == "asn" else "prefix",
                    filename=filename,
                    line_number=line_number,
                    incident_id=incident_id,
                    field=f"affected_objects.{index}.object_id",
                    event_time=event_time_text,
                )

        field_quality = row.get("field_quality")
        if not isinstance(field_quality, list):
            raise QualityCliError(f"{filename} 第 {line_number} 行 field_quality 非数组")
        quality_by_field: Dict[str, list[Mapping[str, Any]]] = {}
        for quality_index, item in enumerate(field_quality):
            if not isinstance(item, Mapping) or not isinstance(item.get("field"), str):
                raise QualityCliError(f"{filename} 第 {line_number} 行 field_quality[{quality_index}] 非法")
            quality_by_field.setdefault(item["field"], []).append(item)
            if item.get("missing_reason") == "invalid_affected_object":
                source_field = item.get("source_field")
                if source_field == "detail_url.problem":
                    kind = "asn" if event_type == "as_outage" else "prefix" if event_type != "country_outage" else None
                else:
                    kind = "asn" if source_field in ASN_SOURCE_FIELDS else "prefix" if source_field in PREFIX_SOURCE_FIELDS else None
                marker = (kind, source_field, _canonical_json(item.get("raw_value")))
                if kind is not None and marker not in seen_invalid:
                    seen_invalid.add(marker)
                    self._identity_failure(
                        kind,
                        filename=filename,
                        line_number=line_number,
                        incident_id=incident_id,
                        field=f"field_quality.{quality_index}.raw_value",
                        event_time=event_time_text,
                    )

        collection_quality = row.get("collection_quality")
        if not isinstance(collection_quality, list):
            raise QualityCliError(f"{filename} 第 {line_number} 行 collection_quality 非数组")
        for quality_index, item in enumerate(collection_quality):
            if not isinstance(item, Mapping):
                raise QualityCliError(f"{filename} 第 {line_number} 行 collection_quality[{quality_index}] 非对象")
            source_field = item.get("field")
            rejected = item.get("rejected_values")
            if isinstance(rejected, list):
                kind = "asn" if source_field in ASN_SOURCE_FIELDS else "prefix" if source_field in PREFIX_SOURCE_FIELDS else None
                for rejected_index, rejected_value in enumerate(rejected):
                    marker = (kind, source_field, _canonical_json(rejected_value))
                    if kind is not None and marker not in seen_invalid:
                        seen_invalid.add(marker)
                        self._identity_failure(
                            kind,
                            filename=filename,
                            line_number=line_number,
                            incident_id=incident_id,
                            field=f"collection_quality.{quality_index}.rejected_values.{rejected_index}",
                            event_time=event_time_text,
                        )
            if item.get("values") is None and not isinstance(item.get("missing_reason"), str):
                self.failure(
                    "unknown_derived_null_count",
                    "missing-reason-complete",
                    filename=filename,
                    line_number=line_number,
                    table="normalized_incident",
                    primary_key=incident_id,
                    field=f"collection_quality.{quality_index}.values",
                    reason="unknown_missing_reason",
                    event_time=event_time_text,
                )

        for field in NULLABLE_DERIVED_FIELDS:
            if row.get(field) is not None:
                continue
            evidence = quality_by_field.get(field, [])
            explained = any(
                item.get("status") in MISSING_FIELD_STATES
                and isinstance(item.get("missing_reason"), str)
                and bool(item.get("missing_reason"))
                for item in evidence
            )
            if not explained:
                self.failure(
                    "unknown_derived_null_count",
                    "missing-reason-complete",
                    filename=filename,
                    line_number=line_number,
                    table="normalized_incident",
                    primary_key=incident_id,
                    field=field,
                    reason="unknown_missing_reason",
                    event_time=event_time_text,
                )
        for field, evidence in quality_by_field.items():
            if row.get(field) != 0 or isinstance(row.get(field), bool):
                continue
            if any(item.get("status") in MISSING_FIELD_STATES for item in evidence):
                self.failure(
                    "confirmed_missing_zero_fill_count",
                    "missing-no-zero-fill",
                    filename=filename,
                    line_number=line_number,
                    table="normalized_incident",
                    primary_key=incident_id,
                    field=field,
                    reason="confirmed_missing_zero_fill",
                    event_time=event_time_text,
                )

        phases = row.get("phase_coverage")
        if not isinstance(phases, Mapping):
            for phase in ("before", "during", "after"):
                self.failure(
                    "phase_state_missing_count",
                    "phase-six-event-coverage",
                    filename=filename,
                    line_number=line_number,
                    table="normalized_incident",
                    primary_key=incident_id,
                    field=f"phase_coverage.{phase}.status",
                    reason="phase_missing_reason_absent",
                    event_time=event_time_text,
                )
            return
        for phase in ("before", "during", "after"):
            item = phases.get(phase)
            if not isinstance(item, Mapping) or item.get("status") not in ALLOWED_PHASE_STATES:
                self.failure(
                    "phase_state_missing_count",
                    "phase-six-event-coverage",
                    filename=filename,
                    line_number=line_number,
                    table="normalized_incident",
                    primary_key=incident_id,
                    field=f"phase_coverage.{phase}.status",
                    reason="phase_missing_reason_absent",
                    event_time=event_time_text,
                )
                continue
            state = item["status"]
            reason = item.get("missing_reason")
            reason_ok = (
                isinstance(reason, str) and bool(reason)
                if state in MISSING_PHASE_STATES
                else reason is None
            )
            if not reason_ok:
                self.failure(
                    "phase_missing_reason_count",
                    "phase-six-event-coverage",
                    filename=filename,
                    line_number=line_number,
                    table="normalized_incident",
                    primary_key=incident_id,
                    field=f"phase_coverage.{phase}.missing_reason",
                    reason="phase_missing_reason_absent",
                    event_time=event_time_text,
                )
            observations = item.get("observations")
            if state in MISSING_PHASE_STATES and observations == 0 and not isinstance(observations, bool):
                self.failure(
                    "confirmed_missing_zero_fill_count",
                    "missing-no-zero-fill",
                    filename=filename,
                    line_number=line_number,
                    table="normalized_incident",
                    primary_key=incident_id,
                    field=f"phase_coverage.{phase}.observations",
                    reason="confirmed_missing_zero_fill",
                    event_time=event_time_text,
                )

    def link(self, row: Mapping[str, Any], line_number: int, raw_line: bytes) -> None:
        filename = "links.jsonl.gz"
        incident_id = row.get("incident_id")
        status_value = row.get("status")
        status_text = status_value if isinstance(status_value, str) else "invalid"
        if row.get("classification") != "observation_only" or row.get("causal_conclusion") is not None:
            raise QualityCliError(f"{filename} 第 {line_number} 行违反 observation_only 边界")
        if status_text not in {"matched", "legacy_collision", "unresolved"}:
            raise QualityCliError(f"{filename} 第 {line_number} 行 status 非法")
        if status_text == "unresolved":
            reasons = row.get("unresolved_reasons")
            if not isinstance(reasons, list) or not reasons:
                raise QualityCliError(f"{filename} 第 {line_number} 行 unresolved 缺少原因")
            self.forward_unresolved_count += 1
            samples = self.failure_samples.setdefault("references-forward-unresolved", [])
            if len(samples) < FAILURE_SAMPLE_LIMIT:
                samples.append(
                    {
                        "source_ref": f"{filename}:{line_number}",
                        "table": "normalized_incident_link",
                        "primary_key": _primary_key_text(incident_id, f"line:{line_number}"),
                        "field": "status",
                        "event_time": None,
                        "reason_codes": ["dangling_reference"],
                        "evidence_locator": f"{filename}#L{line_number}",
                    }
                )
        if not isinstance(incident_id, str) or INCIDENT_ID_RE.fullmatch(incident_id) is None:
            self.failure(
                "stable_id_conflict_count",
                "uniqueness-stable-ids",
                filename=filename,
                line_number=line_number,
                table="normalized_incident_link",
                primary_key=incident_id,
                field="incident_id",
                reason="stable_id_conflict",
            )
            return
        try:
            self.connection.execute(
                "INSERT INTO links(incident_id,row_sha,line_no,status) VALUES(?,?,?,?)",
                (incident_id, hashlib.sha256(raw_line).hexdigest(), line_number, status_text),
            )
        except sqlite3.IntegrityError:
            self.failure(
                "stable_id_conflict_count",
                "uniqueness-stable-ids",
                filename=filename,
                line_number=line_number,
                table="normalized_incident_link",
                primary_key=incident_id,
                field="incident_id",
                reason="stable_id_conflict",
            )
        self.fact_link_status_counts[status_text] = self.fact_link_status_counts.get(status_text, 0) + 1

    def quarantine(self, row: Mapping[str, Any], line_number: int, raw_line: bytes) -> None:
        filename = "quarantine.jsonl.gz"
        quarantine_id = row.get("quarantine_id")
        reasons = row.get("reason_codes")
        source_table = row.get("source_table")
        source_primary_key = row.get("source_primary_key")
        expected = None
        if (
            isinstance(source_table, str)
            and source_table
            and isinstance(source_primary_key, Mapping)
            and isinstance(reasons, list)
            and reasons
            and all(isinstance(reason, str) and reason for reason in reasons)
        ):
            expected = "qr_v1_" + _canonical_sha256(
                {
                    "schema": "quarantine_id_v1",
                    "source_table": source_table,
                    "source_primary_key": source_primary_key,
                    "reasons": sorted(set(reasons)),
                }
            )[:32]
        if (
            not isinstance(quarantine_id, str)
            or QUARANTINE_ID_RE.fullmatch(quarantine_id) is None
            or quarantine_id != expected
            or row.get("classification") != "observation_only"
            or row.get("causal_conclusion") is not None
            or not isinstance(reasons, list)
            or not reasons
        ):
            self.failure(
                "stable_id_conflict_count",
                "uniqueness-stable-ids",
                filename=filename,
                line_number=line_number,
                table=str(source_table or "quarantine"),
                primary_key=quarantine_id,
                field="quarantine_id",
                reason="stable_id_conflict",
            )
        if isinstance(quarantine_id, str) and quarantine_id:
            try:
                self.connection.execute(
                    "INSERT INTO sidecar_ids(kind,stable_id,line_no) VALUES('quarantine',?,?)",
                    (quarantine_id, line_number),
                )
            except sqlite3.IntegrityError:
                self.failure(
                    "stable_id_conflict_count",
                    "uniqueness-stable-ids",
                    filename=filename,
                    line_number=line_number,
                    table=str(source_table or "quarantine"),
                    primary_key=quarantine_id,
                    field="quarantine_id",
                    reason="stable_id_conflict",
                )

    def collision(self, row: Mapping[str, Any], line_number: int, raw_line: bytes) -> None:
        filename = "collision_groups.jsonl.gz"
        group_id = row.get("collision_group_id")
        source_table = row.get("source_table")
        source_primary_key = row.get("source_primary_key")
        incident_ids = row.get("incident_ids")
        expected = None
        if (
            isinstance(source_table, str)
            and source_table
            and isinstance(source_primary_key, Mapping)
            and isinstance(incident_ids, list)
            and len(incident_ids) >= 2
            and all(
                isinstance(item, str) and INCIDENT_ID_RE.fullmatch(item) is not None
                for item in incident_ids
            )
            and len(set(incident_ids)) == len(incident_ids)
        ):
            expected = "lcg_v1_" + _canonical_sha256(
                {
                    "schema": "legacy_collision_group_id_v1",
                    "source_table": source_table,
                    "source_primary_key": source_primary_key,
                    "incident_ids": sorted(set(incident_ids)),
                }
            )[:32]
        if (
            row.get("classification") != "observation_only"
            or row.get("causal_conclusion") is not None
            or not isinstance(group_id, str)
            or COLLISION_ID_RE.fullmatch(group_id) is None
            or group_id != expected
        ):
            self.failure(
                "stable_id_conflict_count",
                "uniqueness-stable-ids",
                filename=filename,
                line_number=line_number,
                table=str(source_table or "collision_group"),
                primary_key=group_id,
                field="collision_group_id",
                reason="stable_id_conflict",
            )
        if isinstance(group_id, str) and group_id:
            try:
                self.connection.execute(
                    "INSERT INTO sidecar_ids(kind,stable_id,line_no) VALUES('collision',?,?)",
                    (group_id, line_number),
                )
            except sqlite3.IntegrityError:
                self.failure(
                    "stable_id_conflict_count",
                    "uniqueness-stable-ids",
                    filename=filename,
                    line_number=line_number,
                    table=str(source_table or "collision_group"),
                    primary_key=group_id,
                    field="collision_group_id",
                    reason="stable_id_conflict",
                )

    def finalize_links(self) -> None:
        missing_links = self.connection.execute(
            "SELECT incident_id,line_no FROM incidents WHERE incident_id NOT IN (SELECT incident_id FROM links) ORDER BY incident_id LIMIT ?",
            (FAILURE_SAMPLE_LIMIT,),
        ).fetchall()
        orphan_links = self.connection.execute(
            "SELECT incident_id,line_no FROM links WHERE incident_id NOT IN (SELECT incident_id FROM incidents) ORDER BY incident_id LIMIT ?",
            (FAILURE_SAMPLE_LIMIT,),
        ).fetchall()
        missing_count = self.connection.execute(
            "SELECT count(*) FROM incidents WHERE incident_id NOT IN (SELECT incident_id FROM links)"
        ).fetchone()[0]
        orphan_count = self.connection.execute(
            "SELECT count(*) FROM links WHERE incident_id NOT IN (SELECT incident_id FROM incidents)"
        ).fetchone()[0]
        self.forward_unresolved_count += int(missing_count) + int(orphan_count)
        rows = self.failure_samples.setdefault("references-forward-unresolved", [])
        for incident_id, line_number in missing_links:
            if len(rows) >= FAILURE_SAMPLE_LIMIT:
                break
            rows.append(
                {
                    "source_ref": f"incidents.jsonl.gz:{line_number}",
                    "table": "normalized_incident",
                    "primary_key": incident_id,
                    "field": "incident_link",
                    "event_time": None,
                    "reason_codes": ["dangling_reference"],
                    "evidence_locator": f"incidents.jsonl.gz#L{line_number}",
                }
            )
        for incident_id, line_number in orphan_links:
            if len(rows) >= FAILURE_SAMPLE_LIMIT:
                break
            rows.append(
                {
                    "source_ref": f"links.jsonl.gz:{line_number}",
                    "table": "normalized_incident_link",
                    "primary_key": incident_id,
                    "field": "incident_id",
                    "event_time": None,
                    "reason_codes": ["dangling_reference"],
                    "evidence_locator": f"links.jsonl.gz#L{line_number}",
                }
            )
        if not rows:
            self.failure_samples.pop("references-forward-unresolved", None)


def _stream_jsonl(
    path: Path,
    inventory: Mapping[str, Any],
    callback: Callable[[Mapping[str, Any], int, bytes], None],
) -> None:
    digest, size_bytes = _sha256_file(path, path.name)
    if (
        digest != inventory.get("sha256")
        or size_bytes != inventory.get("size_bytes")
        or inventory.get("compression", {}).get("algorithm") != "gzip"
        or inventory.get("compression", {}).get("mtime") != 0
    ):
        raise QualityCliError(f"{path.name} 压缩文件清单不闭合")
    content_digest = hashlib.sha256()
    row_count = 0
    initial = _lstat_regular(path, path.name)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise QualityCliError(f"无法只读打开 {path.name}") from error
    try:
        before = os.fstat(descriptor)
        immutable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if not stat.S_ISREG(before.st_mode) or any(
            getattr(initial, field) != getattr(before, field) for field in immutable
        ):
            raise QualityCliError(f"打开前 {path.name} 发生变化")
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as raw_stream, gzip.GzipFile(
                fileobj=raw_stream, mode="rb"
            ) as stream:
                while True:
                    line = stream.readline(MAX_JSONL_LINE_BYTES + 1)
                    if not line:
                        break
                    row_count += 1
                    if len(line) > MAX_JSONL_LINE_BYTES:
                        raise QualityCliError(f"{path.name} 第 {row_count} 行超过上限")
                    if not line.endswith(b"\n") or line in (b"\n", b"\r\n"):
                        raise QualityCliError(f"{path.name} 第 {row_count} 行必须是单行 JSON")
                    content_digest.update(line)
                    try:
                        text = line[:-1].decode("utf-8")
                    except UnicodeDecodeError as error:
                        raise QualityCliError(f"{path.name} 第 {row_count} 行不是 UTF-8") from error
                    try:
                        row = json.loads(
                            text,
                            object_pairs_hook=_unique_object,
                            parse_constant=_reject_json_constant,
                        )
                    except json.JSONDecodeError as error:
                        raise QualityCliError(f"{path.name} 第 {row_count} 行 JSON 非法：{error.msg}") from error
                    if not isinstance(row, dict):
                        raise QualityCliError(f"{path.name} 第 {row_count} 行必须是 JSON 对象")
                    if _canonical_json(row).encode("utf-8") + b"\n" != line:
                        raise QualityCliError(f"{path.name} 第 {row_count} 行不是冻结规范序列化")
                    callback(row, row_count, line)
        except (OSError, EOFError, gzip.BadGzipFile) as error:
            raise QualityCliError(f"{path.name} gzip 解码失败") from error
        after = os.fstat(descriptor)
        if any(getattr(before, field) != getattr(after, field) for field in immutable):
            raise QualityCliError(f"读取期间 {path.name} 发生变化")
    finally:
        os.close(descriptor)
    if row_count != inventory.get("row_count"):
        raise QualityCliError(f"{path.name} 行数与 manifest 不一致")
    if content_digest.hexdigest() != inventory.get("content_sha256"):
        raise QualityCliError(f"{path.name} 解压内容 SHA256 与 manifest 不一致")


def _validate_d2_fingerprint(manifest: Mapping[str, Any]) -> None:
    source = manifest.get("source")
    provenance = source.get("provenance") if isinstance(source, Mapping) else None
    runner_sha = provenance.get("probe_sha256") if isinstance(provenance, Mapping) else None
    if not isinstance(runner_sha, str) or SHA256_RE.fullmatch(runner_sha) is None:
        raise QualityCliError("D2 manifest 缺少生成器 SHA256，无法复算候选指纹")
    payload = {
        "schema_version": "p0_normalization_candidate_v1",
        "data_profile": manifest.get("data_profile"),
        "source_release": {
            "release_id": source.get("release_id"),
            "system_identifier": source.get("database", {}).get("system_identifier"),
            "state_sha256": source.get("state_sha256"),
            "manifest_sha256": source.get("manifest_sha256"),
            "database_manifest_sha256": source.get("database_manifest_sha256"),
            "inventory_sha256": source.get("inventory_sha256"),
        },
        "runner_sha256": runner_sha,
        "normalizer_hashes": source.get("normalizer_hashes"),
        "source_table_counts": manifest.get("source_table_counts"),
        "files": manifest.get("files"),
        "summary": manifest.get("summary"),
        "sample": manifest.get("sample"),
        "classification": manifest.get("classification"),
        "causal_conclusion": manifest.get("causal_conclusion"),
    }
    expected = _canonical_sha256(payload)
    if manifest.get("candidate_fingerprint_sha256") != expected:
        raise QualityCliError("D2 candidate_fingerprint_sha256 复算不一致")


def _validate_program_provenance(
    pipeline_root: Path,
    d2: Mapping[str, Any],
    d3_verification: Mapping[str, Any],
) -> None:
    source = d2.get("source")
    provenance = source.get("provenance") if isinstance(source, Mapping) else None
    runner_sha = provenance.get("probe_sha256") if isinstance(provenance, Mapping) else None
    actual_runner_sha, _ = _sha256_file(
        pipeline_root / D2_RUNNER_RELATIVE_PATH, "D2 候选 runner"
    )
    if runner_sha != actual_runner_sha:
        raise QualityCliError("D2 候选 runner SHA256 与 pipeline-root 不一致")
    normalizer_hashes = source.get("normalizer_hashes") if isinstance(source, Mapping) else None
    if not isinstance(normalizer_hashes, Mapping) or not normalizer_hashes:
        raise QualityCliError("D2 manifest 缺少 normalizer 哈希闭包")
    for relative_name, expected in sorted(normalizer_hashes.items()):
        relative = Path(relative_name) if isinstance(relative_name, str) else Path("invalid")
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.parent != Path("backend/data_pipeline/normalize")
            or relative.suffix != ".py"
            or not isinstance(expected, str)
            or SHA256_RE.fullmatch(expected) is None
        ):
            raise QualityCliError("D2 normalizer 哈希条目非法")
        actual, _ = _sha256_file(pipeline_root / relative, "D2 normalizer")
        if actual != expected:
            raise QualityCliError(f"D2 normalizer SHA256 与 pipeline-root 不一致：{relative}")

    d3_provenance = d3_verification.get("provenance")
    if not isinstance(d3_provenance, Mapping):
        raise QualityCliError("D3 verification 缺少程序 provenance")
    expected_programs = (
        ("scanner", D3_SCANNER_RELATIVE_PATH),
        ("cli", D3_CLI_RELATIVE_PATH),
    )
    for label, relative in expected_programs:
        evidence = d3_provenance.get(label)
        actual, _ = _sha256_file(pipeline_root / relative, f"D3 {label}")
        if not isinstance(evidence, Mapping) or evidence.get("sha256") != actual:
            raise QualityCliError(f"D3 {label} SHA256 与 pipeline-root 不一致")
    scanner = d3_provenance.get("scanner")
    if scanner.get("module_path_verified") is not True:
        raise QualityCliError("D3 scanner 未提供模块路径复核证据")


def _validate_profile(
    profile: Mapping[str, Any],
    profile_sha256: str,
    d2: Mapping[str, Any],
    d3: Mapping[str, Any],
    d3_verification: Mapping[str, Any],
) -> None:
    if profile != FROZEN_PROFILE:
        raise QualityCliError("D5 只接受唯一 feb-mar-2026 固定数据档")
    d2_profile = d2.get("data_profile")
    if d2_profile != profile:
        raise QualityCliError("D2 manifest 数据档与唯一固定数据档不一致")
    d2_profile_sha = d2.get("source", {}).get("provenance", {}).get("data_profile_sha256")
    if d2_profile_sha != profile_sha256:
        raise QualityCliError("唯一固定数据档 SHA256 与 D2 provenance 不一致")
    d3_profile = d3.get("data_profile")
    if not isinstance(d3_profile, Mapping):
        raise QualityCliError("D3 manifest 缺少 data_profile")
    for field in ("id", "timezone", "window_start", "window_end_exclusive"):
        if d3_profile.get(field) != profile[field]:
            raise QualityCliError(f"D3 manifest 数据档字段不一致：{field}")
    d3_profile_sha = d3_verification.get("provenance", {}).get("data_profile", {}).get("sha256")
    if d3_profile_sha != profile_sha256:
        raise QualityCliError("唯一固定数据档 SHA256 与 D3 provenance 不一致")


def _validate_execution(value: Mapping[str, Any], d2: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "git_sha",
        "probe_fingerprint_sha256",
        "started_at",
        "finished_at",
        "generated_at",
        "database_access",
        "database_connection_attempts",
        "database_write_operations",
    }
    if set(value) != required or value.get("schema_version") != "p0_quality_gate_execution_v1":
        raise QualityCliError("execution context 字段不符合冻结合同")
    if (
        value.get("database_access") != "none"
        or value.get("database_connection_attempts") != 0
        or isinstance(value.get("database_connection_attempts"), bool)
        or value.get("database_write_operations") != 0
        or isinstance(value.get("database_write_operations"), bool)
    ):
        raise QualityCliError("质量门禁拒绝任何数据库连接或写操作证据")
    if not isinstance(value.get("git_sha"), str) or GIT_SHA_RE.fullmatch(value["git_sha"]) is None:
        raise QualityCliError("execution context git_sha 非法")
    if not isinstance(value.get("probe_fingerprint_sha256"), str) or SHA256_RE.fullmatch(value["probe_fingerprint_sha256"]) is None:
        raise QualityCliError("execution context probe fingerprint 非法")
    manifest_git = d2.get("source", {}).get("provenance", {}).get("git_sha")
    if manifest_git != value["git_sha"]:
        raise QualityCliError("execution context git_sha 与 D2 provenance 不一致")
    for field in ("started_at", "finished_at", "generated_at"):
        if _parse_utc_second(value.get(field)) is None:
            raise QualityCliError(f"execution context {field} 必须是 UTC 秒级时间")
    if _parse_utc_second(value["started_at"]) > _parse_utc_second(value["finished_at"]):
        raise QualityCliError("execution context finished_at 早于 started_at")


def _load_auxiliary(
    path_value: Optional[str],
    checksum_value: Optional[str],
    label: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[bytes], Optional[str]]:
    if bool(path_value) != bool(checksum_value):
        raise QualityCliError(f"{label}与其 SHA256SUMS 必须同时提供")
    if not path_value:
        return None, None, None
    path = Path(path_value)
    checksum_path = Path(checksum_value)
    index = _checksum_index(checksum_path, f"{label} SHA256SUMS")
    value, payload, digest = _load_json(path, label)
    if index.get(path.name) != digest:
        raise QualityCliError(f"{label}未通过 SHA256SUMS 校验")
    _verify_checksum_closure(checksum_path.parent, index, f"{label} SHA256SUMS")
    return value, payload, digest


def _candidate_profile_identity(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    keys = ("id", "timezone", "window_start", "window_end_exclusive")
    result = {key: value.get(key) for key in keys}
    return result if all(isinstance(item, str) and item for item in result.values()) else None


def _validate_reconciliation_fingerprint(
    summary: Mapping[str, Any], schema: str, label: str
) -> None:
    fingerprint = summary.get("summary_fingerprint_sha256")
    if not isinstance(fingerprint, str) or SHA256_RE.fullmatch(fingerprint) is None:
        raise QualityCliError(f"{label}缺少合法 summary fingerprint")
    payload = dict(summary)
    payload.pop("summary_fingerprint_sha256", None)
    expected = _canonical_sha256({"schema": schema, "summary": payload})
    if fingerprint != expected:
        raise QualityCliError(f"{label} summary fingerprint 复算不一致")


def _build_assurance_context(
    *,
    d2: Mapping[str, Any],
    d2_manifest_sha: str,
    d2_checksums: Mapping[str, str],
    d2_closure: Mapping[str, Any],
    d3: Mapping[str, Any],
    d3_manifest_sha: str,
    d3_summary_sha: str,
    d3_closure: Mapping[str, Any],
    metric_manifest: Mapping[str, Any],
    metric_manifest_sha: str,
    metric_summary: Mapping[str, Any],
    metric_closure: Mapping[str, Any],
    route_summary: Mapping[str, Any],
    route_summary_sha: str,
    route_closure: Mapping[str, Any],
) -> Dict[str, Any]:
    """从 D5 实际输入重建 assurance 必须精确匹配的身份与跨层绑定。"""

    identities = {
        "d2": {
            "candidate_fingerprint_sha256": d2.get("candidate_fingerprint_sha256"),
            "manifest_sha256": d2_manifest_sha,
            "sha256sums_sha256": d2_closure.get("sha256sums_sha256"),
            "incidents_sha256": d2_checksums.get("incidents.jsonl.gz"),
        },
        "d3": {
            "manifest_fingerprint_sha256": d3.get("manifest_fingerprint_sha256"),
            "manifest_sha256": d3_manifest_sha,
            "summary_sha256": d3_summary_sha,
            "sha256sums_sha256": d3_closure.get("sha256sums_sha256"),
        },
        "metric": {
            "candidate_fingerprint_sha256": metric_manifest.get(
                "candidate_fingerprint_sha256"
            ),
            "manifest_sha256": metric_manifest_sha,
            "reconciliation_fingerprint_sha256": metric_summary.get(
                "summary_fingerprint_sha256"
            ),
            "sha256sums_sha256": metric_closure.get("sha256sums_sha256"),
        },
        "route_event": {
            "index_fingerprint_sha256": route_summary.get(
                "index_fingerprint_sha256"
            ),
            "parent_d3_manifest_fingerprint_sha256": route_summary.get(
                "manifest_fingerprint_sha256"
            ),
            "reconciliation_summary_sha256": route_summary_sha,
            "sha256sums_sha256": route_closure.get("sha256sums_sha256"),
        },
    }

    metric_sources = (
        metric_manifest.get("sources")
        if isinstance(metric_manifest.get("sources"), Mapping)
        else {}
    )
    metric_d2 = (
        metric_sources.get("d2_normalization")
        if isinstance(metric_sources.get("d2_normalization"), Mapping)
        else {}
    )
    metric_d3 = (
        metric_sources.get("d3_artifacts")
        if isinstance(metric_sources.get("d3_artifacts"), Mapping)
        else {}
    )
    route_scope = (
        route_summary.get("build_scope")
        if isinstance(route_summary.get("build_scope"), Mapping)
        else {}
    )
    profiles = (
        _candidate_profile_identity(d2.get("data_profile")),
        _candidate_profile_identity(d3.get("data_profile")),
        _candidate_profile_identity(metric_manifest.get("data_profile")),
        _candidate_profile_identity(route_scope.get("data_profile")),
    )
    bindings = {
        "metric_to_final_d2": (
            metric_d2.get("fingerprint_sha256")
            == identities["d2"]["candidate_fingerprint_sha256"]
            and metric_d2.get("manifest_sha256") == identities["d2"]["manifest_sha256"]
            and metric_d2.get("checksums_sha256")
            == identities["d2"]["sha256sums_sha256"]
            and metric_d2.get("incidents_sha256")
            == identities["d2"]["incidents_sha256"]
        ),
        "metric_to_final_d3": (
            metric_d3.get("fingerprint_sha256")
            == identities["d3"]["manifest_fingerprint_sha256"]
            and metric_d3.get("manifest_sha256") == identities["d3"]["manifest_sha256"]
            and metric_d3.get("summary_sha256") == identities["d3"]["summary_sha256"]
            and metric_d3.get("checksums_sha256")
            == identities["d3"]["sha256sums_sha256"]
        ),
        "route_event_to_final_d3": (
            identities["route_event"]["parent_d3_manifest_fingerprint_sha256"]
            == identities["d3"]["manifest_fingerprint_sha256"]
        ),
        "shared_data_profile": (
            all(profile is not None for profile in profiles)
            and len({_canonical_json(profile) for profile in profiles}) == 1
        ),
    }
    return {
        "final_candidate_integrity": {
            "d2": dict(d2_closure),
            "d3": dict(d3_closure),
            "metric": dict(metric_closure),
            "route_event": dict(route_closure),
        },
        "final_candidate_identity": identities,
        "cross_artifact_binding": bindings,
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _deterministic_gzip_jsonl(rows: Iterable[Mapping[str, Any]]) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=buffer, mtime=0) as stream:
        for row in rows:
            stream.write(_canonical_bytes(row))
    return buffer.getvalue()


def _summary_markdown(
    report: Mapping[str, Any],
    *,
    d2_original_sha: str,
    d2_audited_sha: str,
    d3_sha: str,
    d3_coverage: Mapping[str, Any],
    route_summary: Optional[Mapping[str, Any]],
    missing_inputs: Sequence[str],
) -> str:
    gate = report["gate"]
    checks = report["check_summary"]
    route_count = route_summary.get("route_event_count") if isinstance(route_summary, Mapping) else None
    return """# P0 D5 数据质量门禁摘要

## 结论

- 门禁状态：`{status}`
- 准入等级：`{admission}`
- 阻断失败：{blocking}
- 警告：{warnings}
- 检查：{passed} 通过 / {failed} 失败 / {pending} 待定

## 固定数据档与原始覆盖

- 数据档：`{profile}`
- 窗口：`{start} <= t < {end}`
- D3 覆盖状态：`{coverage}`，覆盖率 `{ratio}`
- RouteEvent 记录数：`{route_count}`

RouteEvent 为 0 或未提供只表示当前没有可声明 `raw_traceable` 的索引记录，
不能把部分原始覆盖提升为全窗口覆盖，也不能据此生成因果结论。

## 只读与输入闭包

- 数据库连接次数：0
- 数据库写操作：0
- D2 原始 manifest SHA256：`{d2_original_sha}`
- D2 逐行审计副本 SHA256：`{d2_audited_sha}`
- D3 manifest SHA256：`{d3_sha}`
- 报告 fingerprint：`{report_fingerprint}`
- 缺少的可选摘要：{missing_inputs}

本检查只读取普通文件并复核 SHA256；不打开数据库、不读取原始 MRT、不修改
候选数据。缺少 Evidence、Metric、RouteEvent 或可复现性摘要时，门禁失败关闭，
不会把未知项猜成 0。
""".format(
        status=gate["status"],
        admission=gate["admission_level"],
        blocking=len(gate["blocking_failed_check_ids"]),
        warnings=len(gate["warning_check_ids"]),
        passed=checks["passed_check_count"],
        failed=checks["failed_check_count"],
        pending=checks["pending_check_count"],
        profile=report["data_profile"]["profile_id"],
        start=report["data_profile"]["window"]["start"],
        end=report["data_profile"]["window"]["end"],
        coverage=d3_coverage.get("coverage_status", "unknown"),
        ratio=d3_coverage.get("coverage_ratio", "unknown"),
        route_count="未提供" if route_count is None else route_count,
        d2_original_sha=d2_original_sha,
        d2_audited_sha=d2_audited_sha,
        d3_sha=d3_sha,
        report_fingerprint=report["report_fingerprint_sha256"],
        missing_inputs="、".join(missing_inputs) if missing_inputs else "无",
    )


def _prepare_output(output_dir: Path) -> Tuple[Path, Path]:
    parent = output_dir.parent
    _lstat_directory(parent, "输出父目录")
    try:
        output_dir.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise QualityCliError(f"无法检查输出目录：{output_dir}") from error
    else:
        raise QualityCliError(f"输出目录必须全新且禁止覆盖：{output_dir}")
    staging = parent / f".{output_dir.name}.tmp.{os.getpid()}.{secrets.token_hex(8)}"
    staging.mkdir(mode=0o750)
    return output_dir, staging


def run(args: argparse.Namespace) -> Dict[str, Any]:
    output_dir, staging = _prepare_output(Path(args.output_dir))
    completed = False
    audit: Optional[_Audit] = None
    try:
        pipeline_root = Path(args.pipeline_root)
        gate, gate_sha = _load_gate_module(pipeline_root)
        schema_path = pipeline_root / SCHEMA_RELATIVE_PATH
        schema, _schema_payload, schema_sha = _load_json(schema_path, "DataQualityReport Schema")
        if schema.get("$id") != "https://domeye.example/contracts/data/data-quality-report.schema.json":
            raise QualityCliError("DataQualityReport Schema 身份非法")
        cli_sha, _ = _sha256_file(Path(__file__), "P0 quality CLI")
        assurance_program_sha, _ = _sha256_file(
            pipeline_root / ASSURANCE_RELATIVE_PATH, "P0 single-run assurance"
        )
        reproducibility_program_sha, _ = _sha256_file(
            pipeline_root / REPRODUCIBILITY_RELATIVE_PATH, "P0 reproducibility validator"
        )

        profile_path = Path(args.data_profile)
        profile, profile_payload, profile_sha = _load_json(profile_path, "固定 data-profile")

        d2_manifest_path = Path(args.d2_manifest)
        d2_checksum_path = Path(args.d2_checksums)
        d2_index = _checksum_index(d2_checksum_path, "D2 SHA256SUMS")
        d2_manifest, d2_original_payload, d2_original_sha = _load_json(d2_manifest_path, "D2 manifest")
        _require_same_candidate_directory(
            (d2_manifest_path,), d2_checksum_path, "D2"
        )
        if d2_index.get(d2_manifest_path.name) != d2_original_sha:
            raise QualityCliError("D2 manifest 未通过 SHA256SUMS 校验")
        # 四个大型 JSONL 在后续流式扫描中同时复核 compressed/content SHA，
        # 此处只验证闭包内其余旁路文件，避免对全量候选多做一次无意义 I/O。
        d2_streamed_names = {d2_manifest_path.name, *D2_JSONL_FILES}
        d2_closure = _verified_flat_candidate_closure(
            d2_checksum_path,
            d2_index,
            "D2",
            already_streamed=d2_streamed_names,
        )
        for name, expected in sorted(d2_index.items()):
            if name in d2_streamed_names:
                continue
            actual, _ = _sha256_file(d2_checksum_path.parent / name, "D2 SHA256SUMS 闭包文件")
            if actual != expected:
                raise QualityCliError(f"D2 SHA256SUMS 闭包文件不一致：{name}")
        _validate_d2_fingerprint(d2_manifest)

        d3_manifest_path = Path(args.d3_manifest)
        d3_verification_path = Path(args.d3_verification_summary)
        d3_checksum_path = Path(args.d3_checksums)
        d3_index = _checksum_index(d3_checksum_path, "D3 SHA256SUMS")
        d3_manifest, d3_payload, d3_sha = _load_json(d3_manifest_path, "D3 artifact manifest")
        d3_verification, d3_verification_payload, d3_verification_sha = _load_json(
            d3_verification_path, "D3 verification summary"
        )
        _require_same_candidate_directory(
            (d3_manifest_path, d3_verification_path), d3_checksum_path, "D3"
        )
        if d3_index.get(d3_manifest_path.name) != d3_sha or d3_index.get(d3_verification_path.name) != d3_verification_sha:
            raise QualityCliError("D3 manifest/verification 未通过 SHA256SUMS 校验")
        _verify_checksum_closure(d3_checksum_path.parent, d3_index, "D3 SHA256SUMS")
        d3_closure = _verified_flat_candidate_closure(
            d3_checksum_path, d3_index, "D3"
        )
        verification = d3_verification.get("verification")
        if (
            d3_verification.get("summary_kind") != "p0_raw_artifact_manifest_summary_zh"
            or not isinstance(verification, Mapping)
            or d3_verification.get("manifest", {}).get("sha256") != d3_sha
            or d3_verification.get("manifest", {}).get("fingerprint_sha256") != d3_manifest.get("manifest_fingerprint_sha256")
        ):
            raise QualityCliError("D3 verification summary 与 manifest 不闭合")

        _validate_profile(profile, profile_sha, d2_manifest, d3_manifest, d3_verification)
        _validate_program_provenance(pipeline_root, d2_manifest, d3_verification)

        execution_path = Path(args.execution_context)
        execution_checksum_path = Path(args.execution_checksums)
        execution_index = _checksum_index(execution_checksum_path, "execution SHA256SUMS")
        execution, execution_payload, execution_sha = _load_json(execution_path, "execution context")
        if execution_index.get(execution_path.name) != execution_sha:
            raise QualityCliError("execution context 未通过 SHA256SUMS 校验")
        _verify_checksum_closure(execution_checksum_path.parent, execution_index, "execution SHA256SUMS")
        _validate_execution(execution, d2_manifest)

        route, route_payload, route_sha = _load_auxiliary(args.route_summary, args.route_checksums, "RouteEvent 摘要")
        metric, metric_payload, metric_sha = _load_auxiliary(args.metric_summary, args.metric_checksums, "Metric 摘要")
        if metric is not None:
            metric_schema_path = pipeline_root / METRIC_SCHEMA_RELATIVE_PATH
            metric_schema, _metric_schema_payload, metric_schema_sha = _load_json(
                metric_schema_path, "MetricSeries Schema"
            )
            if metric_schema.get("$id") != "https://domeye.example/contracts/data/metric-series.schema.json":
                raise QualityCliError("MetricSeries Schema 身份非法")
            if metric.get("schema_sha256") != metric_schema_sha:
                raise QualityCliError("Metric 对账摘要没有闭合到当前 MetricSeries Schema")
        repro, repro_payload, repro_sha = _load_auxiliary(args.reproducibility_summary, args.reproducibility_checksums, "可复现性摘要")
        assurance_context: Dict[str, Any] = {}
        if isinstance(repro, Mapping) and repro.get("schema_version") == "p0_single_run_assurance_v1":
            required = {
                "route": (route, args.route_summary, args.route_checksums),
                "metric": (metric, args.metric_summary, args.metric_checksums),
            }
            if any(value[0] is None for value in required.values()):
                raise QualityCliError("single-run assurance 要求 RouteEvent/Metric 全部提供")
            if not args.metric_manifest:
                raise QualityCliError("single-run assurance 要求 Metric manifest")

            route_summary_path = Path(args.route_summary)
            route_checksum_path = Path(args.route_checksums)
            route_index = _checksum_index(route_checksum_path, "RouteEvent SHA256SUMS")
            _require_same_candidate_directory(
                (route_summary_path,), route_checksum_path, "RouteEvent"
            )
            route_closure = _verified_flat_candidate_closure(
                route_checksum_path, route_index, "RouteEvent"
            )

            metric_summary_path = Path(args.metric_summary)
            metric_manifest_path = Path(args.metric_manifest)
            metric_checksum_path = Path(args.metric_checksums)
            metric_index = _checksum_index(metric_checksum_path, "Metric SHA256SUMS")
            _require_same_candidate_directory(
                (metric_summary_path, metric_manifest_path),
                metric_checksum_path,
                "Metric",
            )
            metric_manifest, _metric_manifest_payload, metric_manifest_sha = _load_json(
                metric_manifest_path, "Metric manifest"
            )
            if metric_index.get(metric_manifest_path.name) != metric_manifest_sha:
                raise QualityCliError("Metric manifest 未通过 SHA256SUMS 校验")
            metric_closure = _verified_flat_candidate_closure(
                metric_checksum_path, metric_index, "Metric"
            )
            _validate_reconciliation_fingerprint(
                metric,
                "metric_reconciliation_summary_fingerprint_v1",
                "Metric 对账摘要",
            )

            repro_path = Path(args.reproducibility_summary)
            repro_checksum_path = Path(args.reproducibility_checksums)
            repro_index = _checksum_index(repro_checksum_path, "Assurance SHA256SUMS")
            _require_same_candidate_directory(
                (repro_path,), repro_checksum_path, "Assurance"
            )
            _verified_flat_candidate_closure(
                repro_checksum_path, repro_index, "Assurance"
            )
            assurance_context = _build_assurance_context(
                d2=d2_manifest,
                d2_manifest_sha=d2_original_sha,
                d2_checksums=d2_index,
                d2_closure=d2_closure,
                d3=d3_manifest,
                d3_manifest_sha=d3_sha,
                d3_summary_sha=d3_verification_sha,
                d3_closure=d3_closure,
                metric_manifest=metric_manifest,
                metric_manifest_sha=metric_manifest_sha,
                metric_summary=metric,
                metric_closure=metric_closure,
                route_summary=route,
                route_summary_sha=route_sha,
                route_closure=route_closure,
            )

        start = datetime.fromisoformat(profile["window_start"]).astimezone(UTC)
        end = datetime.fromisoformat(profile["window_end_exclusive"]).astimezone(UTC)
        audit = _Audit(staging / ".quality-audit.sqlite3", window_start=start, window_end=end)
        files = d2_manifest.get("files")
        if not isinstance(files, Mapping) or any(not isinstance(files.get(name), Mapping) for name in D2_JSONL_FILES):
            raise QualityCliError("D2 manifest 缺少四个 JSONL inventory")
        callbacks = {
            "incidents.jsonl.gz": audit.incident,
            "links.jsonl.gz": audit.link,
            "collision_groups.jsonl.gz": audit.collision,
            "quarantine.jsonl.gz": audit.quarantine,
        }
        for name in D2_JSONL_FILES:
            path = d2_manifest_path.parent / name
            if d2_index.get(name) != files[name].get("sha256"):
                raise QualityCliError(f"D2 SHA256SUMS 与 manifest inventory 不一致：{name}")
            _stream_jsonl(path, files[name], callbacks[name])
        audit.finalize_links()
        audit.connection.commit()

        audited_d2 = deepcopy(d2_manifest)
        summary = audited_d2.get("summary")
        if not isinstance(summary, MutableMapping):
            raise QualityCliError("D2 manifest summary 非对象")
        for field, observed in audit.counts.items():
            declared = summary.get(field)
            if declared is not None and (
                not isinstance(declared, int)
                or isinstance(declared, bool)
                or declared < 0
                or declared != observed
            ):
                raise QualityCliError(f"D2 summary.{field} 与逐行审计不一致")
        if summary.get("event_type_counts") != audit.event_type_counts:
            raise QualityCliError("D2 summary.event_type_counts 与逐行审计不一致")
        if summary.get("fact_link_status_counts") != audit.fact_link_status_counts:
            raise QualityCliError("D2 summary.fact_link_status_counts 与逐行审计不一致")
        if summary.get("unexplained_forward_reference_count") != audit.forward_unresolved_count:
            raise QualityCliError("D2 正向未解释引用汇总与逐行审计不一致")
        summary.update(audit.counts)
        summary["event_type_counts"] = audit.event_type_counts
        summary["fact_link_status_counts"] = audit.fact_link_status_counts
        summary["unexplained_forward_reference_count"] = audit.forward_unresolved_count
        audited_d2["quality_failure_samples"] = {
            key: rows for key, rows in sorted(audit.failure_samples.items()) if rows
        }
        audited_d2_payload = _canonical_bytes(audited_d2)
        d2_audited_sha = hashlib.sha256(audited_d2_payload).hexdigest()

        input_sha256s = {
            "d2": d2_audited_sha,
            "d2_original": d2_original_sha,
            "d2_audited": d2_audited_sha,
            "d3": d3_sha,
            "route": route_sha,
            "metric": metric_sha,
            "repro": repro_sha,
            "execution": execution_sha,
            "d3_verification": d3_verification_sha,
            "profile": profile_sha,
        }
        context = {
            "profile_sha256": profile_sha,
            "git_sha": execution["git_sha"],
            "probe_fingerprint_sha256": execution["probe_fingerprint_sha256"],
            "data_artifact_sha256": d3_sha,
            "database_write_operations": 0,
            "started_at": execution["started_at"],
            "finished_at": execution["finished_at"],
            "generated_at": execution["generated_at"],
            "input_sha256s": input_sha256s,
            **assurance_context,
        }
        # 纯函数核心允许 ``route_event_summary=None`` 作为历史兼容警告；D5
        # 发布门禁要求可选层缺失也必须失败关闭。这里用“字段明确未知”的哨兵
        # 触发跨层计数检查，既不猜零，也不伪造一个 RouteEvent 输入制品。
        route_for_gate = route if route is not None else {
            "schema_version": "route_event_index_summary_v1",
            "route_event_count": None,
            "lineage_status": "unavailable",
        }
        result = gate.build_quality_report(
            audited_d2,
            d3_manifest,
            context=context,
            route_event_summary=route_for_gate,
            artifact_verification_summary=verification,
            metric_summary=metric,
            reproducibility_summary=repro,
        )
        gate.validate_report_semantics(result.report)

        input_files = {
            "d2-candidate-manifest.json": audited_d2_payload,
            "d2-original-candidate-manifest.json": d2_original_payload,
            OUTPUT_INPUT_NAMES["d3"]: d3_payload,
            OUTPUT_INPUT_NAMES["d3_verification"]: d3_verification_payload,
            OUTPUT_INPUT_NAMES["execution"]: execution_payload,
            OUTPUT_INPUT_NAMES["profile"]: profile_payload,
        }
        for key, payload in (
            ("route", route_payload),
            ("metric", metric_payload),
            ("repro", repro_payload),
        ):
            if payload is not None:
                input_files[OUTPUT_INPUT_NAMES[key]] = payload
        for name, payload in sorted(input_files.items()):
            _atomic_write(staging / name, payload)

        report_payload = result.report_bytes()
        failure_payload = _deterministic_gzip_jsonl(result.failure_details_zh)
        missing_inputs = [
            label
            for label, value in (
                ("RouteEvent", route),
                ("Metric", metric),
                ("可复现性", repro),
            )
            if value is None
        ]
        summary_payload = _summary_markdown(
            result.report,
            d2_original_sha=d2_original_sha,
            d2_audited_sha=d2_audited_sha,
            d3_sha=d3_sha,
            d3_coverage=d3_manifest.get("coverage", {}),
            route_summary=route,
            missing_inputs=missing_inputs,
        ).encode("utf-8")
        provenance = {
            "schema_version": "p0_quality_gate_input_closure_v1",
            "profile_id": profile["id"],
            "database_access": "none",
            "database_connection_attempts": 0,
            "database_write_operations": 0,
            "source_inputs": {
                "d2_original_manifest_sha256": d2_original_sha,
                "d2_audited_manifest_sha256": d2_audited_sha,
                **input_sha256s,
            },
            "programs": {
                CLI_RELATIVE_PATH.as_posix(): cli_sha,
                GATE_RELATIVE_PATH.as_posix(): gate_sha,
                SCHEMA_RELATIVE_PATH.as_posix(): schema_sha,
                ASSURANCE_RELATIVE_PATH.as_posix(): assurance_program_sha,
                REPRODUCIBILITY_RELATIVE_PATH.as_posix(): reproducibility_program_sha,
            },
            "report_fingerprint_sha256": result.report["report_fingerprint_sha256"],
        }
        output_payloads = {
            "data-quality-report.json": report_payload,
            "失败明细.jsonl.gz": failure_payload,
            "中文摘要.md": summary_payload,
            "输入闭包.json": _canonical_bytes(provenance),
        }
        for name, payload in sorted(output_payloads.items()):
            _atomic_write(staging / name, payload)

        checksum_names = sorted(
            path.name for path in staging.iterdir() if path.is_file() and not path.name.startswith(".")
        )
        checksum_lines = []
        for name in checksum_names:
            digest, _ = _sha256_file(staging / name, "D5 输出")
            checksum_lines.append(f"{digest}  {name}")
        _atomic_write(staging / "SHA256SUMS", ("\n".join(checksum_lines) + "\n").encode("utf-8"))

        audit.close()
        audit = None
        (staging / ".quality-audit.sqlite3").unlink()
        for path in staging.iterdir():
            path.chmod(0o440)
        _fsync_directory(staging)
        if output_dir.exists() or output_dir.is_symlink():
            raise QualityCliError(f"发布时输出目录已出现，拒绝覆盖：{output_dir}")
        staging.rename(output_dir)
        _fsync_directory(output_dir.parent)
        completed = True
        semantic_mode = (
            "d2_bounded_replay_64"
            if isinstance(repro, Mapping)
            and repro.get("schema_version") == "p0_single_run_assurance_v1"
            else (
                repro.get("semantic_validation", {}).get("mode")
                if isinstance(repro, Mapping)
                and isinstance(repro.get("semantic_validation"), Mapping)
                else "unavailable"
            )
        )
        full_semantic_status = (
            repro.get("full_semantic_validation", {}).get("status")
            if isinstance(repro, Mapping)
            and isinstance(repro.get("full_semantic_validation"), Mapping)
            else "unavailable"
        )
        return {
            "状态": result.report["gate"]["status"],
            "准入等级": result.report["gate"]["admission_level"],
            "语义复核范围": semantic_mode,
            "全量语义复现": full_semantic_status,
            "报告": str(output_dir / "data-quality-report.json"),
            "失败明细": str(output_dir / "失败明细.jsonl.gz"),
            "中文摘要": str(output_dir / "中文摘要.md"),
            "SHA256SUMS": str(output_dir / "SHA256SUMS"),
            "report_fingerprint_sha256": result.report["report_fingerprint_sha256"],
        }
    finally:
        if audit is not None:
            audit.close()
        if not completed and staging.exists() and staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="执行 P0 固定数据档离线质量门禁")
    parser.add_argument("--data-profile", required=True)
    parser.add_argument("--d2-manifest", required=True)
    parser.add_argument("--d2-checksums", required=True)
    parser.add_argument("--d3-manifest", required=True)
    parser.add_argument("--d3-verification-summary", required=True)
    parser.add_argument("--d3-checksums", required=True)
    parser.add_argument("--execution-context", required=True)
    parser.add_argument("--execution-checksums", required=True)
    parser.add_argument("--route-summary")
    parser.add_argument("--route-checksums")
    parser.add_argument("--metric-summary")
    parser.add_argument("--metric-manifest")
    parser.add_argument("--metric-checksums")
    parser.add_argument("--reproducibility-summary")
    parser.add_argument("--reproducibility-checksums")
    parser.add_argument("--pipeline-root", default=str(PROJECT_ROOT))
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except Exception as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result["状态"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
