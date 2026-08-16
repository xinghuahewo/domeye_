#!/usr/bin/env python3
"""生成固定二三月 P0 MetricSeries 只读候选制品。

数据来源严格分层：七个采集特征指标只读取 ``feature_country`` 的
``source=r,country=collect`` 行，并以已校验 D3 原始制品 manifest 中的
UPDATE 文件槽判断源是否可用；异常计数与两类中断并发只消费 D2 full
``incidents.jsonl.gz``，不回查、也不重新解释六类历史事实表。

数据库查询全部位于 ``REPEATABLE READ READ ONLY`` 事务中且最终 rollback。
输出先写同级临时目录，完成确定性 gzip、对账摘要、manifest、中文摘要和
SHA256SUMS 后再原子改名。历史并发区间边界不足时逐槽标为
``legacy_unknown``；结构性错误仍失败关闭，任何情形都不补零、不猜测。
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo


_BUNDLE_ROOT = Path(__file__).resolve().parents[2]
if str(_BUNDLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BUNDLE_ROOT))

from dev.data_quality.p0_probe import (
    LOOPBACK_HOST,
    PROFILE_RAW_KEYS,
    ProbeError,
    _assert_no_symlink_ancestors,
    _begin_readonly_transaction,
    _connect_database,
    _git_provenance,
    _load_project_data_profile,
    _read_database_env,
    _sha256,
    _validate_release_context,
    _verify_reader_security,
)


UTC = timezone.utc
CANDIDATE_PATH = Path(__file__).absolute()
SCHEMA_VERSION = "p0_metric_candidate_v1"
METRIC_FILE = "metric-series.jsonl.gz"
RECONCILIATION_FILE = "metric-reconciliation-summary.json"
MANIFEST_FILE = "manifest.json"
SUMMARY_FILE = "摘要.md"
METRICS_RELATIVE_DIR = Path("backend/data_pipeline/metrics")
METRIC_SCHEMA_RELATIVE_PATH = Path("contracts/data/metric-series.schema.json")
AJV_RELATIVE_PATH = Path("frontend/node_modules/@redocly/ajv/dist/2020")
ARTIFACT_FINGERPRINT_SCHEMA = "mrt_artifact_manifest_fingerprint_v1"
D3_INVALID_VALUE_STATE = "parse_failed"
D3_INVALID_MISSING_REASONS = frozenset(
    {"compressed_stream_invalid", "empty_file", "compression_magic_mismatch"}
)
D3_INVALID_RECORD_FIELDS = frozenset(
    {
        "collector_id",
        "artifact_type",
        "artifact_time_utc",
        "relative_path",
        "filename_family",
        "compression",
        "size_bytes",
        "file_sha256",
        "value_state",
        "missing_reason",
    }
)
D3_DUPLICATE_CONTENT_POLICY = {
    "valid_artifact": "reject_across_paths",
    "invalid_compressed_stream_invalid": "reject_across_paths",
    "invalid_empty_file": "allow_across_unique_paths_and_slots",
    "invalid_compression_magic_mismatch": "reject_across_paths",
}
D3_DIRECTORY_SCOPE_BASIS = (
    "utc_month_directories_intersecting_half_open_profile_window"
)
D3_ARTIFACT_FILE_RE = re.compile(
    r"^(?P<family>updates|bview|rib)\."
    r"(?P<date>[0-9]{8})\."
    r"(?P<time>[0-9]{4})\."
    r"(?P<compression>gz|bz2)$"
)
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
INCIDENT_ID_RE = re.compile(r"^inc_v1_[0-9a-f]{24}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ASN_RE = re.compile(r"^(0|[1-9][0-9]*)$")
GRANULARITY_SECONDS = 300
CONCURRENCY_SECONDS = 180
SOURCE_RECONCILIATION_SCOPE = (
    "independent_readonly_feature_rows_and_sqlite_interval_projection_v1"
)
SOURCE_RECONCILIATION_SAMPLE_LIMIT = 20
SOURCE_POINT_FIELDS = (
    "time",
    "value",
    "value_state",
    "missing_reason",
    "formula_inputs",
)
METRIC_VALUE_STATES = frozenset(
    {
        "observed_nonzero",
        "observed_zero",
        "not_observed",
        "source_unavailable",
        "processing_gap",
        "parse_failed",
        "not_retained",
        "not_applicable",
        "legacy_unknown",
        "invalid_identity",
        "legacy_window_contamination",
        "source_fact_collision",
    }
)
METRIC_MISSING_REASONS = frozenset(
    (METRIC_VALUE_STATES - {"observed_nonzero", "observed_zero"})
    | {"denominator_zero"}
)
EVENT_TYPES = frozenset(
    {"hijack", "sub_hijack", "leak", "prefix_outage", "as_outage", "country_outage"}
)
FEATURE_METRICS = (
    "bgp_announce_record_count",
    "bgp_withdraw_record_count",
    "bgp_update_record_count",
    "bgp_withdraw_ratio",
    "ipv4_24_equivalent_count",
    "ipv6_48_equivalent_count",
    "ipv4_equivalent_address_count",
)
INCIDENT_METRICS = (
    "anomaly_incident_count",
    "prefix_outage_concurrent_count",
    "as_outage_concurrent_count",
)
ALL_METRICS = tuple(sorted(FEATURE_METRICS + INCIDENT_METRICS))
KNOWN_PROCESSING_GAPS_UTC = tuple(
    datetime(2026, 3, 30, 23, 30, tzinfo=UTC) + timedelta(minutes=5 * index)
    for index in range(6)
)


class MetricCandidateError(ProbeError):
    """候选输入、只读查询或制品闭包不符合 P0 约束。"""


def _canonical_bytes(value: Any, *, newline: bool = False) -> bytes:
    try:
        payload = json.dumps(
            _json_ready(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise MetricCandidateError("候选对象包含不可序列化或非有限值") from error
    return payload + (b"\n" if newline else b"")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise MetricCandidateError("候选时间必须带时区")
        return _utc_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MetricCandidateError("候选对象包含非有限浮点数")
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    raise MetricCandidateError("候选对象包含未支持类型：{}".format(type(value).__name__))


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: Any, field: str, *, aligned: Optional[int] = None) -> datetime:
    if not isinstance(value, str) or "T" not in value:
        raise MetricCandidateError("{} 必须是带时区 ISO 8601 时间".format(field))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MetricCandidateError("{} 不是有效时间".format(field)) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond:
        raise MetricCandidateError("{} 必须带时区且只能精确到秒".format(field))
    normalized = parsed.astimezone(UTC)
    if aligned is not None and int(normalized.timestamp()) % aligned:
        raise MetricCandidateError("{} 未对齐 {} 秒网格".format(field, aligned))
    return normalized


def _expected_d3_directory_scope(start: datetime, end: datetime) -> dict[str, Any]:
    current = datetime(start.year, start.month, 1, tzinfo=UTC)
    months = []
    while current < end:
        months.append(current.strftime("%Y.%m"))
        current = (
            datetime(current.year + 1, 1, 1, tzinfo=UTC)
            if current.month == 12
            else datetime(current.year, current.month + 1, 1, tzinfo=UTC)
        )
    return {
        "basis": D3_DIRECTORY_SCOPE_BASIS,
        "included_month_directories": months,
        "missing_included_month_directory": "treat_as_empty",
        "other_month_directories": "excluded_without_inventory",
        "filename_utc_month_must_match_directory": True,
    }


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MetricCandidateError("JSON 存在重复字段：{}".format(key))
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise MetricCandidateError("JSON 禁止非有限常量：{}".format(value))


def _assert_regular(path: Path, label: str, *, maximum_bytes: Optional[int] = None) -> int:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise MetricCandidateError("无法读取{}：{}".format(label, path)) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise MetricCandidateError("{}必须是普通文件且禁止软链接：{}".format(label, path))
    if maximum_bytes is not None and metadata.st_size > maximum_bytes:
        raise MetricCandidateError("{}超过 {} 字节限制".format(label, maximum_bytes))
    return metadata.st_size


def _load_json(path: Path, label: str, *, maximum_bytes: int = 128 * 1024 * 1024) -> dict[str, Any]:
    _assert_regular(path, label, maximum_bytes=maximum_bytes)
    try:
        payload = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise MetricCandidateError("无法以 UTF-8 读取{}：{}".format(label, path)) from error
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as error:
        raise MetricCandidateError("{}不是有效 JSON：{}".format(label, error.msg)) from error
    if not isinstance(value, dict):
        raise MetricCandidateError("{}顶层必须是对象".format(label))
    return value


def _load_sha256sums(path: Path) -> dict[str, str]:
    _assert_regular(path, "SHA256SUMS", maximum_bytes=1024 * 1024)
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise MetricCandidateError("无法读取 SHA256SUMS：{}".format(path)) from error
    for number, line in enumerate(lines, 1):
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\\r\n]+)", line)
        if match is None:
            raise MetricCandidateError("SHA256SUMS 第 {} 行格式非法".format(number))
        digest, name = match.groups()
        if name in result:
            raise MetricCandidateError("SHA256SUMS 文件名重复：{}".format(name))
        result[name] = digest
    if not result:
        raise MetricCandidateError("SHA256SUMS 不能为空")
    return result


def _verify_checksum(path: Path, checksums: Mapping[str, str], label: str) -> str:
    expected = checksums.get(path.name)
    if expected is None:
        raise MetricCandidateError("SHA256SUMS 缺少{}：{}".format(label, path.name))
    actual = _sha256(path)
    if actual != expected:
        raise MetricCandidateError("{} SHA256 与 SHA256SUMS 不一致".format(label))
    return actual


def _profile_raw(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {key: profile[key] for key in PROFILE_RAW_KEYS}


def _assert_profile_projection(source: Mapping[str, Any], profile: Mapping[str, Any], label: str) -> None:
    expected = {
        "id": profile["id"],
        "timezone": profile["timezone"],
        "window_start": profile["window_start"],
        "window_end_exclusive": profile["window_end_exclusive"],
    }
    for key, value in expected.items():
        if source.get(key) != value:
            raise MetricCandidateError("{}数据档字段 {} 与唯一数据档不一致".format(label, key))


def _artifact_fingerprint(manifest: Mapping[str, Any]) -> str:
    payload = dict(manifest)
    supplied = payload.pop("manifest_fingerprint_sha256", None)
    expected = _canonical_sha256(
        {"schema": ARTIFACT_FINGERPRINT_SCHEMA, "manifest": payload}
    )
    if supplied != expected:
        raise MetricCandidateError("D3 artifact manifest fingerprint 校验失败")
    return expected


def _validate_d3_invalid_record(
    record: Mapping[str, Any],
    *,
    index: int,
    collectors: Sequence[str],
    window_start: datetime,
    window_end: datetime,
) -> tuple[str, str, datetime, str, int, str]:
    """严格验证 scanner 隔离记录，不从摘要猜测无效槽。"""

    if set(record) != D3_INVALID_RECORD_FIELDS:
        raise MetricCandidateError(
            "D3 invalid_in_window[{}] 字段集合不符合冻结合同".format(index)
        )
    collector = record.get("collector_id")
    artifact_type = record.get("artifact_type")
    relative = record.get("relative_path")
    family = record.get("filename_family")
    compression = record.get("compression")
    size_bytes = record.get("size_bytes")
    file_sha256 = record.get("file_sha256")
    missing_reason = record.get("missing_reason")
    if collector not in collectors:
        raise MetricCandidateError("D3 invalid_in_window collector 越出 allowlist")
    if artifact_type not in {"update", "rib"}:
        raise MetricCandidateError("D3 invalid_in_window artifact_type 非法")
    if record.get("value_state") != D3_INVALID_VALUE_STATE:
        raise MetricCandidateError("D3 invalid_in_window value_state 必须为 parse_failed")
    if missing_reason not in D3_INVALID_MISSING_REASONS:
        raise MetricCandidateError("D3 invalid_in_window missing_reason 未被合同枚举")
    if (
        isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
    ):
        raise MetricCandidateError("D3 invalid_in_window size_bytes 非法")
    if not isinstance(file_sha256, str) or not SHA256_RE.fullmatch(file_sha256):
        raise MetricCandidateError("D3 invalid_in_window file_sha256 非法")
    if missing_reason == "empty_file":
        if size_bytes != 0 or file_sha256 != EMPTY_SHA256:
            raise MetricCandidateError("D3 empty_file 的 size/SHA 与空内容不一致")
    elif size_bytes == 0:
        raise MetricCandidateError("D3 compression_magic_mismatch 不得伪装为空文件")
    if not isinstance(relative, str):
        raise MetricCandidateError("D3 invalid_in_window relative_path 非法")
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or not pure.parts
        or ".." in pure.parts
        or pure.parts[0] != collector
    ):
        raise MetricCandidateError("D3 invalid_in_window relative_path 不安全")
    matched = D3_ARTIFACT_FILE_RE.fullmatch(pure.name)
    if matched is None:
        raise MetricCandidateError("D3 invalid_in_window 文件名不符合 MRT 合同")
    expected_type = "update" if matched.group("family") == "updates" else "rib"
    if (
        artifact_type != expected_type
        or family != matched.group("family")
        or compression != matched.group("compression")
    ):
        raise MetricCandidateError("D3 invalid_in_window 文件名语义与字段不一致")
    slot = _parse_time(
        record.get("artifact_time_utc"),
        "D3 invalid_in_window artifact_time_utc",
        aligned=GRANULARITY_SECONDS if artifact_type == "update" else 8 * 3600,
    )
    try:
        filename_slot = datetime.strptime(
            matched.group("date") + matched.group("time"), "%Y%m%d%H%M"
        ).replace(tzinfo=UTC)
    except ValueError as error:
        raise MetricCandidateError("D3 invalid_in_window 文件名时间非法") from error
    if slot != filename_slot:
        raise MetricCandidateError("D3 invalid_in_window 文件名时间与字段不一致")
    if len(pure.parts) < 3 or pure.parts[1] != slot.strftime("%Y.%m"):
        raise MetricCandidateError(
            "D3 invalid_in_window 文件名 UTC 年月与所属月目录不一致"
        )
    if not window_start <= slot < window_end:
        raise MetricCandidateError("D3 invalid_in_window 位于固定窗口外")
    return collector, artifact_type, slot, relative, size_bytes, file_sha256


def _load_d3_input(
    manifest_path: Path,
    summary_path: Path,
    checksum_path: Path,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """验证 D3 manifest、校验和及 verify 摘要，并提取 UPDATE 槽。"""

    checksums = _load_sha256sums(checksum_path)
    manifest_sha = _verify_checksum(manifest_path, checksums, "D3 manifest")
    summary_sha = _verify_checksum(summary_path, checksums, "D3 verify 摘要")
    manifest = _load_json(manifest_path, "D3 artifact manifest")
    summary = _load_json(summary_path, "D3 artifact verify 摘要")
    if manifest.get("schema_version") != 1 or manifest.get("manifest_kind") != "mrt_artifact_manifest":
        raise MetricCandidateError("D3 artifact manifest schema/kind 不受支持")
    scan_policy = manifest.get("scan_policy")
    if (
        not isinstance(scan_policy, Mapping)
        or scan_policy.get("out_of_window") not in {"exclude_without_hash", "reject"}
        or scan_policy.get("invalid_in_window")
        != "full_hash_quarantine_exclude_from_available_slots"
        or scan_policy.get("compression_envelope_validation")
        != "full_stream_to_eof_crc_or_equivalent"
        or scan_policy.get("duplicate_content") != D3_DUPLICATE_CONTENT_POLICY
        or not isinstance(scan_policy.get("directory_scope"), Mapping)
    ):
        raise MetricCandidateError("D3 artifact manifest scan_policy 不受支持")
    fingerprint = _artifact_fingerprint(manifest)
    source_profile = manifest.get("data_profile")
    if not isinstance(source_profile, Mapping):
        raise MetricCandidateError("D3 artifact manifest 缺少 data_profile")
    _assert_profile_projection(source_profile, profile, "D3")
    if summary.get("verification", {}).get("verified") is not True:
        raise MetricCandidateError("D3 artifact manifest 没有已 verify 证明")
    summary_manifest = summary.get("manifest")
    if not isinstance(summary_manifest, Mapping):
        raise MetricCandidateError("D3 verify 摘要缺少 manifest 闭包")
    if summary_manifest.get("sha256") != manifest_sha:
        raise MetricCandidateError("D3 verify 摘要中的 manifest SHA256 不一致")
    if summary_manifest.get("fingerprint_sha256") != fingerprint:
        raise MetricCandidateError("D3 verify 摘要中的 fingerprint 不一致")

    collectors = manifest.get("collector_allowlist")
    if (
        not isinstance(collectors, list)
        or not collectors
        or any(not isinstance(item, str) or not item for item in collectors)
        or collectors != sorted(set(collectors))
    ):
        raise MetricCandidateError("D3 collector_allowlist 非法或非确定性排序")
    start = profile["parsed"]["start"].astimezone(UTC)
    end = profile["parsed"]["end_exclusive"].astimezone(UTC)
    expected_directory_scope = _expected_d3_directory_scope(start, end)
    if scan_policy["directory_scope"] != expected_directory_scope:
        raise MetricCandidateError("D3 directory_scope 与固定数据档 UTC 月份不一致")
    if summary.get("directory_scope") != expected_directory_scope:
        raise MetricCandidateError("D3 verify 摘要 directory_scope 闭包不一致")
    update_slots: set[datetime] = set()
    update_keys: set[tuple[str, datetime]] = set()
    update_count = 0
    manifest_paths: set[str] = set()
    manifest_file_hashes: set[str] = set()
    all_slot_keys: set[tuple[str, str, datetime]] = set()
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise MetricCandidateError("D3 artifact manifest 缺少 artifacts")
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, Mapping):
            raise MetricCandidateError("D3 artifacts[{}] 非对象".format(index))
        if artifact.get("artifact_type") not in {"update", "rib"}:
            raise MetricCandidateError("D3 artifact_type 非法")
        if artifact.get("collector_id") not in collectors:
            raise MetricCandidateError("D3 artifact 越出 collector allowlist")
        for field in ("file_sha256",):
            if not isinstance(artifact.get(field), str) or not SHA256_RE.fullmatch(artifact[field]):
                raise MetricCandidateError("D3 artifact {} 非法".format(field))
        relative = artifact.get("relative_path")
        size_bytes = artifact.get("size_bytes")
        if not isinstance(relative, str) or relative in manifest_paths:
            raise MetricCandidateError("D3 artifact relative_path 非法或重复")
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or not pure.parts
            or ".." in pure.parts
            or pure.parts[0] != artifact.get("collector_id")
        ):
            raise MetricCandidateError("D3 artifact relative_path 不安全")
        if (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes <= 0
        ):
            raise MetricCandidateError("D3 artifact size_bytes 必须为正整数")
        if artifact["file_sha256"] in manifest_file_hashes:
            raise MetricCandidateError("D3 artifact file_sha256 重复")
        manifest_paths.add(relative)
        manifest_file_hashes.add(artifact["file_sha256"])
        if artifact.get("artifact_type") != "update":
            continue
        slot = _parse_time(
            artifact.get("artifact_time_utc"),
            "D3 UPDATE artifact_time_utc",
            aligned=GRANULARITY_SECONDS,
        )
        if len(pure.parts) < 3 or pure.parts[1] != slot.strftime("%Y.%m"):
            raise MetricCandidateError(
                "D3 UPDATE 文件名 UTC 年月与所属月目录不一致"
            )
        if not start <= slot < end:
            raise MetricCandidateError("D3 UPDATE artifact 位于固定窗口外")
        key = (artifact.get("collector_id"), slot)
        if key in update_keys:
            raise MetricCandidateError("D3 同一 collector 存在重复 UPDATE 槽")
        update_keys.add(key)
        all_slot_keys.add((artifact.get("collector_id"), "update", slot))
        update_count += 1
        if slot in update_slots:
            raise MetricCandidateError("D3 多 collector/重复 UPDATE 槽无法映射到单一 global source")
        update_slots.add(slot)
    invalid_records = manifest.get("invalid_in_window")
    if not isinstance(invalid_records, list):
        raise MetricCandidateError("D3 artifact manifest 缺少 invalid_in_window")
    if invalid_records != sorted(
        invalid_records,
        key=lambda row: (
            row.get("collector_id")
            if isinstance(row, Mapping) and isinstance(row.get("collector_id"), str)
            else "",
            row.get("artifact_type")
            if isinstance(row, Mapping) and isinstance(row.get("artifact_type"), str)
            else "",
            row.get("artifact_time_utc")
            if isinstance(row, Mapping)
            and isinstance(row.get("artifact_time_utc"), str)
            else "",
            row.get("relative_path")
            if isinstance(row, Mapping) and isinstance(row.get("relative_path"), str)
            else "",
        ),
    ):
        raise MetricCandidateError("D3 invalid_in_window 未按冻结键确定性排序")
    invalid_update_slots: set[datetime] = set()
    invalid_update_keys: set[tuple[str, datetime]] = set()
    invalid_reason_counts: Counter[str] = Counter()
    invalid_size_bytes = 0
    for index, invalid in enumerate(invalid_records):
        if not isinstance(invalid, Mapping):
            raise MetricCandidateError(
                "D3 invalid_in_window[{}] 非对象".format(index)
            )
        collector, artifact_type, slot, relative, size_bytes, file_sha256 = (
            _validate_d3_invalid_record(
                invalid,
                index=index,
                collectors=collectors,
                window_start=start,
                window_end=end,
            )
        )
        slot_key = (collector, artifact_type, slot)
        if slot_key in all_slot_keys:
            raise MetricCandidateError("D3 有效与无效制品槽重叠或重复")
        all_slot_keys.add(slot_key)
        if relative in manifest_paths:
            raise MetricCandidateError("D3 有效与无效制品路径重复")
        # 多个独立空文件共享空内容 SHA 是隔离状态，不是 artifact 身份复用；
        # 非空 magic mismatch 则与有效 UPDATE 一样保留跨路径重复硬失败。
        if (
            invalid["missing_reason"] != "empty_file"
            and file_sha256 in manifest_file_hashes
        ):
            raise MetricCandidateError("D3 有效与非空无效制品内容 SHA 重复")
        manifest_paths.add(relative)
        if invalid["missing_reason"] != "empty_file":
            manifest_file_hashes.add(file_sha256)
        invalid_reason_counts[invalid["missing_reason"]] += 1
        invalid_size_bytes += size_bytes
        if artifact_type == "update":
            key = (collector, slot)
            if key in invalid_update_keys:
                raise MetricCandidateError("D3 同一 collector 存在重复无效 UPDATE 槽")
            invalid_update_keys.add(key)
            if slot in invalid_update_slots or slot in update_slots:
                raise MetricCandidateError("D3 UPDATE 槽无法映射到单一 global source")
            invalid_update_slots.add(slot)

    expected_invalid_by_reason = {
        reason: {
            "file_count": invalid_reason_counts[reason],
            "size_bytes": sum(
                row["size_bytes"]
                for row in invalid_records
                if row["missing_reason"] == reason
            ),
        }
        for reason in sorted(D3_INVALID_MISSING_REASONS)
    }
    expected_invalid_summary = {
        "file_count": len(invalid_records),
        "size_bytes": invalid_size_bytes,
        "by_missing_reason": expected_invalid_by_reason,
    }
    if manifest.get("summary", {}).get("invalid_in_window") != expected_invalid_summary:
        raise MetricCandidateError("D3 invalid_in_window 与 manifest summary 不一致")
    summary_invalid = summary.get("invalid_in_window")
    if (
        not isinstance(summary_invalid, Mapping)
        or summary_invalid.get("file_count") != len(invalid_records)
        or summary_invalid.get("size_bytes") != invalid_size_bytes
        or summary_invalid.get("by_missing_reason") != expected_invalid_by_reason
        or summary_invalid.get("records") != invalid_records
        or summary.get("verification", {}).get("invalid_in_window_count")
        != len(invalid_records)
    ):
        raise MetricCandidateError("D3 invalid_in_window 与 verify 摘要不一致")
    summary_update = manifest.get("summary", {}).get("by_artifact_type", {}).get("update", {})
    if summary_update.get("artifact_count") != update_count:
        raise MetricCandidateError("D3 UPDATE artifact_count 与 artifacts 不一致")
    if not update_slots:
        raise MetricCandidateError("D3 manifest 没有可用 UPDATE 槽")
    coverage_collectors = manifest.get("coverage", {}).get("by_collector")
    if not isinstance(coverage_collectors, list):
        raise MetricCandidateError("D3 manifest 缺少按 collector 的 coverage")
    expected_update_slots = len(
        _expected_slots(
            profile["parsed"]["start"].astimezone(UTC),
            profile["parsed"]["end_exclusive"].astimezone(UTC),
        )
    )
    coverage_by_id = {
        item.get("collector_id"): item
        for item in coverage_collectors
        if isinstance(item, Mapping) and isinstance(item.get("collector_id"), str)
    }
    if set(coverage_by_id) != set(collectors):
        raise MetricCandidateError("D3 coverage collector 集合与 allowlist 不一致")
    for collector in collectors:
        update_coverage = (
            coverage_by_id[collector]
            .get("by_artifact_type", {})
            .get("update")
        )
        collector_count = sum(
            artifact.get("artifact_type") == "update"
            and artifact.get("collector_id") == collector
            for artifact in artifacts
        )
        if (
            not isinstance(update_coverage, Mapping)
            or update_coverage.get("expected_slots") != expected_update_slots
            or update_coverage.get("available_slots") != collector_count
            or update_coverage.get("missing_slots") != expected_update_slots - collector_count
        ):
            raise MetricCandidateError("D3 UPDATE coverage 与 artifact 槽集合不一致")
    return {
        "manifest": manifest,
        "manifest_sha256": manifest_sha,
        "summary_sha256": summary_sha,
        "checksums_sha256": _sha256(checksum_path),
        "fingerprint_sha256": fingerprint,
        "collectors": list(collectors),
        "update_slots": tuple(sorted(update_slots)),
        "invalid_update_slots": tuple(sorted(invalid_update_slots)),
        "update_artifact_count": update_count,
        "invalid_update_artifact_count": len(invalid_update_slots),
        "invalid_in_window_count": len(invalid_records),
        "invalid_in_window_reason_counts": dict(sorted(invalid_reason_counts.items())),
        "file_names": {
            "manifest": manifest_path.name,
            "summary": summary_path.name,
            "checksums": checksum_path.name,
        },
    }


def _required_d2_tables() -> set[str]:
    families = (
        "event_table",
        "hijack",
        "sub_hijack",
        "leak_event",
        "prefix_outage",
        "as_outage",
        "country_outage",
    )
    return {"{}_{}".format(family, month) for month in ("202602", "202603") for family in families}


def _load_d2_input(candidate_dir: Path, profile: Mapping[str, Any]) -> dict[str, Any]:
    if candidate_dir.is_symlink() or not candidate_dir.is_dir():
        raise MetricCandidateError("D2 candidate-dir 必须存在且禁止软链接")
    manifest_path = candidate_dir / MANIFEST_FILE
    incidents_path = candidate_dir / "incidents.jsonl.gz"
    checksum_path = candidate_dir / "SHA256SUMS"
    checksums = _load_sha256sums(checksum_path)
    manifest_sha = _verify_checksum(manifest_path, checksums, "D2 manifest")
    incidents_sha = _verify_checksum(incidents_path, checksums, "D2 incidents")
    manifest = _load_json(manifest_path, "D2 normalization manifest")
    if manifest.get("schema_version") != "p0_normalization_candidate_v1":
        raise MetricCandidateError("D2 normalization manifest schema 不受支持")
    if not isinstance(manifest.get("data_profile"), Mapping):
        raise MetricCandidateError("D2 manifest 缺少 data_profile")
    _assert_profile_projection(manifest["data_profile"], profile, "D2")
    sample = manifest.get("sample")
    admission = manifest.get("admission")
    if not isinstance(sample, Mapping) or sample.get("enabled") is not False or sample.get("admissible") is not True:
        raise MetricCandidateError("MetricSeries 只接受 D2 full 候选，不接受 sample")
    if (
        not isinstance(admission, Mapping)
        or admission.get("eligible_for_release_gate") is not True
        or admission.get("status") != "legacy_candidate_ready"
        or admission.get("blocking_reasons") != []
    ):
        raise MetricCandidateError("D2 full 候选尚未通过引用一致性准入")
    fingerprint = manifest.get("candidate_fingerprint_sha256")
    if not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint):
        raise MetricCandidateError("D2 candidate fingerprint 非法")
    source_counts = manifest.get("source_table_counts")
    if not isinstance(source_counts, Mapping) or set(source_counts) != _required_d2_tables():
        raise MetricCandidateError("D2 full 候选没有完整二三月事件/事实分区计数")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in source_counts.values()):
        raise MetricCandidateError("D2 source_table_counts 含非法计数")
    inventory = manifest.get("files", {}).get("incidents.jsonl.gz")
    if not isinstance(inventory, Mapping):
        raise MetricCandidateError("D2 manifest 缺少 incidents inventory")
    if inventory.get("sha256") != incidents_sha:
        raise MetricCandidateError("D2 incidents inventory SHA256 不一致")
    if (
        not isinstance(inventory.get("content_sha256"), str)
        or not SHA256_RE.fullmatch(inventory["content_sha256"])
        or isinstance(inventory.get("row_count"), bool)
        or not isinstance(inventory.get("row_count"), int)
        or inventory["row_count"] < 0
    ):
        raise MetricCandidateError("D2 incidents inventory 字段非法")
    event_total = sum(
        count for name, count in source_counts.items() if name.startswith("event_table_")
    )
    fact_total = sum(
        count for name, count in source_counts.items() if not name.startswith("event_table_")
    )
    if event_total != inventory["row_count"] or fact_total != inventory["row_count"]:
        raise MetricCandidateError("D2 事件总表、六类事实表与 Incident 行数未精确对账")
    return {
        "manifest": manifest,
        "manifest_sha256": manifest_sha,
        "incidents_path": incidents_path,
        "incidents_sha256": incidents_sha,
        "checksums_sha256": _sha256(checksum_path),
        "fingerprint_sha256": fingerprint,
        "inventory": dict(inventory),
        "file_names": {
            "manifest": manifest_path.name,
            "incidents": incidents_path.name,
            "checksums": checksum_path.name,
        },
    }


def _load_metrics_module(pipeline_root: Path) -> tuple[Any, dict[str, str], Path]:
    supplied = pipeline_root.absolute()
    if supplied.is_symlink():
        raise MetricCandidateError("pipeline-root 禁止软链接")
    try:
        root = supplied.resolve(strict=True)
    except OSError as error:
        raise MetricCandidateError("无法解析 pipeline-root") from error
    metrics_dir = root / METRICS_RELATIVE_DIR
    init_path = metrics_dir / "__init__.py"
    series_path = metrics_dir / "series.py"
    for path in (init_path, series_path):
        _assert_regular(path, "MetricSeries pipeline 文件")
    package_name = "_domeye_p0_metrics_{}".format(
        hashlib.sha256(str(metrics_dir).encode("utf-8")).hexdigest()[:16]
    )
    spec = importlib.util.spec_from_file_location(
        package_name,
        init_path,
        submodule_search_locations=[str(metrics_dir)],
    )
    if spec is None or spec.loader is None:
        raise MetricCandidateError("无法创建 MetricSeries pipeline 加载器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(package_name, None)
        raise
    required = ("build_metric_series", "canonical_metric_series_bytes")
    if any(not callable(getattr(module, name, None)) for name in required):
        raise MetricCandidateError("MetricSeries pipeline 公共 API 不完整")
    definitions = getattr(module, "METRIC_DEFINITIONS", None)
    if definitions is None or set(definitions) != set(ALL_METRICS):
        raise MetricCandidateError("MetricSeries pipeline 准入指标不是冻结的 10 项")
    return (
        module,
        {
            str(init_path.relative_to(root)): _sha256(init_path),
            str(series_path.relative_to(root)): _sha256(series_path),
        },
        root,
    )


def _schema_validate_emitted_metric_series(
    metric_path: Path,
    schema_path: Path,
    ajv_module: Path,
) -> dict[str, Any]:
    """用仓库冻结的 AJV 2020 对落盘 gzip 中每条 MetricSeries 做严格校验。

    Node 端逐行解压、逐条验证，避免把十条全窗口序列再次整体复制到 Python
    或 Node 内存。候选不能只记录 Schema 哈希却从未执行 Schema。
    """

    _assert_regular(metric_path, "MetricSeries gzip")
    _assert_regular(schema_path, "MetricSeries JSON Schema")
    resolved_ajv = Path(ajv_module)
    if not resolved_ajv.exists() and not resolved_ajv.is_symlink():
        resolved_ajv = Path("{}.js".format(resolved_ajv))
    _assert_regular(resolved_ajv, "AJV 2020 模块")
    script = r"""
const fs = require('fs')
const readline = require('readline')
const zlib = require('zlib')
const Ajv2020 = require(process.argv[1]).default
const schema = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const ajv = new Ajv2020({allErrors:true,allowUnionTypes:true,strict:true,validateFormats:true})
ajv.addFormat('date-time', {
  type: 'string',
  validate: (value) => {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)) return false
    const timestamp = Date.parse(value)
    return Number.isFinite(timestamp) && new Date(timestamp).toISOString().replace('.000Z', 'Z') === value
  },
})
const validate = ajv.compile(schema)
;(async () => {
  const input = fs.createReadStream(process.argv[3]).pipe(zlib.createGunzip())
  const lines = readline.createInterface({input, crlfDelay: Infinity})
  let count = 0
  for await (const line of lines) {
    if (!line) throw new Error(`MetricSeries 第 ${count + 1} 行为空`)
    const payload = JSON.parse(line)
    if (!validate(payload)) {
      throw new Error(`MetricSeries 第 ${count + 1} 行 Schema 失败: ${ajv.errorsText(validate.errors, {separator:'; '})}`)
    }
    count += 1
  }
  process.stdout.write(String(count))
})().catch((error) => {
  process.stderr.write(String(error && error.message ? error.message : error))
  process.exit(1)
})
"""
    result = subprocess.run(
        ["node", "-e", script, str(resolved_ajv), str(schema_path), str(metric_path)],
        cwd=str(schema_path.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise MetricCandidateError(
            "MetricSeries AJV 2020 严格校验失败：{}".format(result.stderr.strip()[:4000])
        )
    try:
        validated_count = int(result.stdout.strip())
    except ValueError as error:
        raise MetricCandidateError("MetricSeries AJV 校验器没有返回有效计数") from error
    return {
        "strict_schema_status": "passed",
        "schema_invalid_count": 0,
        "schema_validated_series_count": validated_count,
        "schema_sha256": _sha256(schema_path),
        "validator": "ajv_2020_strict_streaming_jsonl_v1",
        "validator_module_sha256": _sha256(resolved_ajv),
    }


def _prepare_staging(output_dir: Path) -> tuple[Path, Path]:
    target = output_dir.absolute()
    _assert_no_symlink_ancestors(target)
    if target.exists() or target.is_symlink():
        raise MetricCandidateError("输出目录必须新建，拒绝已有路径：{}".format(target))
    parent = target.parent
    if parent.is_symlink() or not parent.is_dir():
        raise MetricCandidateError("输出目录父目录必须存在且禁止软链接")
    staging = parent / ".{}.tmp.{}".format(target.name, os.getpid())
    if staging.exists() or staging.is_symlink():
        raise MetricCandidateError("候选临时目录已存在：{}".format(staging))
    staging.mkdir(mode=0o750)
    return target, staging


def _cleanup_staging(path: Path) -> None:
    if path.exists() and path.is_dir() and not path.is_symlink() and ".tmp." in path.name:
        shutil.rmtree(path)


class DeterministicGzipJsonlWriter:
    """gzip mtime=0 且不携带文件名的确定性 JSONL writer。"""

    def __init__(self, path: Path):
        self.path = path
        self.row_count = 0
        self._content = hashlib.sha256()
        self._raw = path.open("xb")
        self._gzip = gzip.GzipFile(
            filename="", mode="wb", fileobj=self._raw, compresslevel=9, mtime=0
        )

    def write(self, value: Mapping[str, Any]) -> None:
        line = _canonical_bytes(value, newline=True)
        self._gzip.write(line)
        self._content.update(line)
        self.row_count += 1

    def close(self) -> None:
        if self._gzip is not None:
            self._gzip.close()
            self._gzip = None
        if self._raw is not None:
            self._raw.flush()
            os.fsync(self._raw.fileno())
            self._raw.close()
            self._raw = None

    def __enter__(self) -> "DeterministicGzipJsonlWriter":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def inventory(self) -> dict[str, Any]:
        if self._gzip is not None or self._raw is not None:
            raise MetricCandidateError("必须先关闭 MetricSeries writer")
        return {
            "name": self.path.name,
            "media_type": "application/x-ndjson+gzip",
            "compression": {
                "algorithm": "gzip",
                "level": 9,
                "mtime": 0,
                "header_filename": "",
            },
            "order": "metric_name_ascending",
            "row_count": self.row_count,
            "content_sha256": self._content.hexdigest(),
            "sha256": _sha256(self.path),
            "size_bytes": self.path.stat().st_size,
        }


def _read_emitted_metric_series(path: Path) -> list[dict[str, Any]]:
    """从刚写出的确定性 gzip 重新读取规范 MetricSeries。"""

    records: list[dict[str, Any]] = []
    try:
        stream = gzip.open(path, "rb")
    except OSError as error:
        raise MetricCandidateError("无法重新打开 MetricSeries gzip") from error
    try:
        for line_number, line in enumerate(stream, 1):
            record = _parse_json_line(line, line_number)
            if _canonical_bytes(record, newline=True) != line:
                raise MetricCandidateError(
                    "MetricSeries 第 {} 行不是规范 JSONL".format(line_number)
                )
            records.append(record)
    except (OSError, EOFError, gzip.BadGzipFile) as error:
        raise MetricCandidateError("MetricSeries gzip 内部重读失败") from error
    finally:
        stream.close()
    return records


def _metric_reconciliation_core(
    records: Sequence[Mapping[str, Any]],
    definitions: Mapping[str, Any],
) -> dict[str, Any]:
    """独立复核十项序列的公式合同、槽闭包和缺失语义。"""

    differences = 0
    outside_window = 0
    unclassified = 0
    unknown_missing_reason = 0
    missing_zero_fill = 0
    legacy_unknown = 0
    point_count = 0
    state_counts: Counter[str] = Counter()
    missing_reason_counts: Counter[str] = Counter()
    legacy_by_metric: Counter[str] = Counter()
    names: list[str] = []
    contract_matches: set[str] = set()

    for record in records:
        metric_name = record.get("metric_name")
        if not isinstance(metric_name, str):
            differences += 1
            metric_name = "<invalid>"
        names.append(metric_name)
        definition = definitions.get(metric_name)
        if definition is not None and all(
            record.get(field) == getattr(definition, field)
            for field in ("unit", "aggregation", "formula", "formula_version")
        ):
            contract_matches.add(metric_name)
        else:
            differences += 1

        window = record.get("window")
        points = record.get("points")
        if not isinstance(window, Mapping) or not isinstance(points, list):
            differences += 1
            continue
        try:
            window_start = _parse_time(window.get("start"), "MetricSeries.window.start", aligned=300)
            window_end = _parse_time(window.get("end"), "MetricSeries.window.end", aligned=300)
            expected_slots = _expected_slots(window_start, window_end)
        except MetricCandidateError:
            differences += 1
            outside_window += len(points)
            continue
        expected_texts = [_utc_text(slot) for slot in expected_slots]
        actual_texts = [point.get("time") if isinstance(point, Mapping) else None for point in points]
        if actual_texts != expected_texts:
            differences += 1
        if record.get("expected_sample_count") != len(expected_slots):
            differences += 1
        if len(points) != len(expected_slots):
            differences += 1

        series_states: Counter[str] = Counter()
        for point in points:
            point_count += 1
            if not isinstance(point, Mapping):
                differences += 1
                unclassified += 1
                unknown_missing_reason += 1
                continue
            raw_time = point.get("time")
            try:
                point_time = _parse_time(raw_time, "MetricSeries.points.time", aligned=300)
            except MetricCandidateError:
                outside_window += 1
                differences += 1
                point_time = None
            if point_time is not None and not window_start <= point_time < window_end:
                outside_window += 1
                differences += 1

            state = point.get("value_state")
            reason = point.get("missing_reason")
            value = point.get("value")
            if isinstance(state, str):
                state_counts[state] += 1
                series_states[state] += 1
            if isinstance(reason, str):
                missing_reason_counts[reason] += 1

            classified = True
            if state in {"observed_nonzero", "observed_zero"}:
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or reason is not None
                    or (state == "observed_zero" and value != 0)
                    or (state == "observed_nonzero" and value == 0)
                ):
                    classified = False
            elif state in METRIC_VALUE_STATES:
                valid_reason = (
                    reason in {"not_applicable", "denominator_zero"}
                    if state == "not_applicable"
                    else reason == state
                )
                if value is not None or not valid_reason:
                    classified = False
            else:
                classified = False
            if not classified:
                unclassified += 1
                differences += 1
            if value is None and reason not in METRIC_MISSING_REASONS:
                unknown_missing_reason += 1
            if value == 0 and (state != "observed_zero" or reason is not None):
                missing_zero_fill += 1
            if state == "legacy_unknown":
                legacy_unknown += 1
                legacy_by_metric[metric_name] += 1

            formula_inputs = point.get("formula_inputs")
            if metric_name == "bgp_withdraw_ratio":
                if not isinstance(formula_inputs, Mapping):
                    differences += 1
                else:
                    numerator = formula_inputs.get("numerator_withdraw_count")
                    denominator = formula_inputs.get("denominator_update_total")
                    if state in {"observed_nonzero", "observed_zero"}:
                        if (
                            isinstance(value, bool)
                            or not isinstance(value, (int, float))
                            or not math.isfinite(value)
                            or isinstance(numerator, bool)
                            or not isinstance(numerator, int)
                            or isinstance(denominator, bool)
                            or not isinstance(denominator, int)
                            or denominator <= 0
                            or not math.isclose(value, numerator / denominator, rel_tol=0, abs_tol=1e-15)
                        ):
                            differences += 1
                    elif state == "not_applicable" and reason == "denominator_zero":
                        if numerator != 0 or denominator != 0:
                            differences += 1
            elif formula_inputs is not None:
                differences += 1

        observed_count = (
            series_states["observed_nonzero"] + series_states["observed_zero"]
        )
        source_count = (
            len(points)
            - series_states["source_unavailable"]
            - series_states["parse_failed"]
        )
        if record.get("source_observed_sample_count") != source_count:
            differences += 1
        if record.get("metric_observed_sample_count") != observed_count:
            differences += 1
        coverage = record.get("coverage")
        if not isinstance(coverage, Mapping):
            differences += 1
        else:
            expected_count = len(expected_slots)
            expected_source_ratio = (
                round(source_count / expected_count, 10) if expected_count else None
            )
            expected_metric_ratio = (
                round(observed_count / expected_count, 10) if expected_count else None
            )
            for actual, expected in (
                (coverage.get("source_coverage_ratio"), expected_source_ratio),
                (coverage.get("metric_coverage_ratio"), expected_metric_ratio),
                (
                    coverage.get("source_gap_sample_count"),
                    series_states["source_unavailable"]
                    + series_states["parse_failed"],
                ),
                (
                    coverage.get("processing_gap_sample_count"),
                    series_states["processing_gap"],
                ),
                (coverage.get("classification_complete"), True),
            ):
                if actual != expected:
                    differences += 1

    duplicate_metric_count = len(names) - len(set(names))
    if duplicate_metric_count:
        differences += duplicate_metric_count
    series_payload = sorted(
        (dict(record) for record in records),
        key=lambda item: str(item.get("metric_name")),
    )
    admitted_count = len(contract_matches)
    return {
        "schema_version": "metric_reconciliation_v1",
        "series_count": len(records),
        "point_count": point_count,
        "admitted_metric_count": admitted_count,
        "formula_contract_coverage_ratio": round(
            admitted_count / len(ALL_METRICS), 10
        ),
        "reconciliation_difference_count": differences,
        "unclassified_gap_count": unclassified,
        "unknown_missing_reason_count": unknown_missing_reason,
        "confirmed_missing_zero_fill_count": missing_zero_fill,
        "outside_window_point_count": outside_window,
        "legacy_unknown_point_count": legacy_unknown,
        "legacy_unknown_point_count_by_metric": dict(sorted(legacy_by_metric.items())),
        "value_state_counts": dict(sorted(state_counts.items())),
        "missing_reason_counts": dict(sorted(missing_reason_counts.items())),
        "duplicate_metric_name_count": duplicate_metric_count,
        "series_fingerprint_sha256": _canonical_sha256(
            {"schema": "metric_series_set_fingerprint_v1", "series": series_payload}
        ),
    }


def _source_point_projection_reconciliation(
    emitted_records: Sequence[Mapping[str, Any]],
    expected_points_by_metric: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """把落盘点与独立源投影逐字段对账，并保留有限定位样本。

    这里不接收 builder 的中间对象。期望点必须由只读查询行或临时 SQLite
    源表重新投影；这样 builder 与 gzip 往返即使稳定地产生同一个错误，也会
    被源数据对账检出。
    """

    counts_by_metric: dict[str, Counter[str]] = {
        metric_name: Counter() for metric_name in expected_points_by_metric
    }
    counts_by_type: Counter[str] = Counter()
    failure_samples: list[dict[str, Any]] = []
    missing_field = object()

    def add_difference(
        metric_name: str,
        difference_type: str,
        *,
        count: int = 1,
        point_index: Optional[int] = None,
        time: Optional[str] = None,
        expected: Any = None,
        actual: Any = None,
    ) -> None:
        if count <= 0:
            return
        counts_by_metric.setdefault(metric_name, Counter())[difference_type] += count
        counts_by_type[difference_type] += count
        if len(failure_samples) >= SOURCE_RECONCILIATION_SAMPLE_LIMIT:
            return
        failure_samples.append(
            {
                "metric_name": metric_name,
                "difference_type": difference_type,
                "point_index": point_index,
                "time": time,
                "expected": _json_ready(expected),
                "actual": _json_ready(actual),
            }
        )

    emitted_by_metric: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    invalid_series_count = 0
    for record in emitted_records:
        metric_name = record.get("metric_name") if isinstance(record, Mapping) else None
        if not isinstance(metric_name, str):
            invalid_series_count += 1
            add_difference(
                "<invalid>",
                "invalid_series_identity",
                actual=record,
            )
            continue
        emitted_by_metric[metric_name].append(record)

    expected_metric_names = set(expected_points_by_metric)
    for metric_name in sorted(set(emitted_by_metric) - expected_metric_names):
        records = emitted_by_metric[metric_name]
        add_difference(
            metric_name,
            "unexpected_series",
            count=len(records),
            actual={"series_count": len(records)},
        )

    actual_point_count = 0
    for record in emitted_records:
        points = record.get("points") if isinstance(record, Mapping) else None
        if isinstance(points, list):
            actual_point_count += len(points)

    for metric_name in sorted(expected_points_by_metric):
        expected_points = list(expected_points_by_metric[metric_name])
        records = emitted_by_metric.get(metric_name, [])
        if not records:
            add_difference(
                metric_name,
                "missing_series",
                expected={"series_count": 1},
                actual={"series_count": 0},
            )
            if expected_points:
                add_difference(
                    metric_name,
                    "missing_point",
                    count=len(expected_points),
                    point_index=0,
                    time=expected_points[0].get("time"),
                    expected=expected_points[0],
                    actual=None,
                )
            continue
        if len(records) > 1:
            add_difference(
                metric_name,
                "duplicate_series",
                count=len(records) - 1,
                expected={"series_count": 1},
                actual={"series_count": len(records)},
            )
        actual_points = records[0].get("points")
        if not isinstance(actual_points, list):
            add_difference(
                metric_name,
                "invalid_points_container",
                expected={"point_count": len(expected_points)},
                actual=actual_points,
            )
            if expected_points:
                add_difference(
                    metric_name,
                    "missing_point",
                    count=len(expected_points),
                    point_index=0,
                    time=expected_points[0].get("time"),
                    expected=expected_points[0],
                    actual=None,
                )
            continue
        if len(actual_points) != len(expected_points):
            add_difference(
                metric_name,
                "point_count",
                expected=len(expected_points),
                actual=len(actual_points),
            )

        common_count = min(len(expected_points), len(actual_points))
        for point_index in range(common_count):
            expected_point = expected_points[point_index]
            actual_point = actual_points[point_index]
            if not isinstance(actual_point, Mapping):
                add_difference(
                    metric_name,
                    "invalid_point",
                    point_index=point_index,
                    time=expected_point.get("time"),
                    expected=expected_point,
                    actual=actual_point,
                )
                continue
            for field in SOURCE_POINT_FIELDS:
                expected_value = expected_point.get(field, missing_field)
                actual_value = actual_point.get(field, missing_field)
                if actual_value != expected_value:
                    add_difference(
                        metric_name,
                        field,
                        point_index=point_index,
                        time=expected_point.get("time"),
                        expected=(
                            {"field_present": False}
                            if expected_value is missing_field
                            else expected_value
                        ),
                        actual=(
                            {"field_present": False}
                            if actual_value is missing_field
                            else actual_value
                        ),
                    )

        if len(expected_points) > common_count:
            first_missing = expected_points[common_count]
            add_difference(
                metric_name,
                "missing_point",
                count=len(expected_points) - common_count,
                point_index=common_count,
                time=first_missing.get("time"),
                expected=first_missing,
                actual=None,
            )
        if len(actual_points) > common_count:
            first_extra = actual_points[common_count]
            add_difference(
                metric_name,
                "unexpected_point",
                count=len(actual_points) - common_count,
                point_index=common_count,
                time=(first_extra.get("time") if isinstance(first_extra, Mapping) else None),
                expected=None,
                actual=first_extra,
            )

    difference_count = sum(counts_by_type.values())
    expected_point_count = sum(
        len(points) for points in expected_points_by_metric.values()
    )
    return {
        "source_reconciliation_scope": SOURCE_RECONCILIATION_SCOPE,
        "source_reconciliation_expected_metric_count": len(expected_points_by_metric),
        "source_reconciliation_expected_point_count": expected_point_count,
        "source_reconciliation_actual_point_count": actual_point_count,
        "source_reconciliation_invalid_series_count": invalid_series_count,
        "source_reconciliation_difference_count": difference_count,
        "source_reconciliation_difference_count_by_metric": {
            metric_name: dict(sorted(counter.items()))
            for metric_name, counter in sorted(counts_by_metric.items())
        },
        "source_reconciliation_difference_count_by_type": dict(
            sorted(counts_by_type.items())
        ),
        "source_reconciliation_failure_sample_limit": SOURCE_RECONCILIATION_SAMPLE_LIMIT,
        "source_reconciliation_failure_sample_truncated_count": max(
            0, difference_count - len(failure_samples)
        ),
        "source_reconciliation_failure_samples": failure_samples,
    }


def _build_metric_reconciliation_summary(
    in_memory_records: Sequence[Mapping[str, Any]],
    emitted_records: Sequence[Mapping[str, Any]],
    definitions: Mapping[str, Any],
    schema_validation: Mapping[str, Any],
    expected_points_by_metric: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """合并内部结构复核、gzip 往返复核与独立源数据逐点对账。"""

    first = _metric_reconciliation_core(in_memory_records, definitions)
    second = _metric_reconciliation_core(emitted_records, definitions)
    deterministic = _canonical_bytes(first) == _canonical_bytes(second)
    summary = dict(second)
    internal_structural_differences = second["reconciliation_difference_count"]
    source_reconciliation = _source_point_projection_reconciliation(
        emitted_records, expected_points_by_metric
    )
    internal_roundtrip_differences = 0 if deterministic else 1
    summary["internal_structural_difference_count"] = internal_structural_differences
    summary["internal_roundtrip_difference_count"] = internal_roundtrip_differences
    summary.update(source_reconciliation)
    # 主 reconciliation 计数只由独立源投影的逐点/逐字段差异汇总，避免把
    # 同一个错误再按内部摘要不一致重复计数。结构复核和内部往返分别保留独立
    # 阻断字段，由候选准入和 D5 各自失败关闭。
    summary["reconciliation_difference_count"] = source_reconciliation[
        "source_reconciliation_difference_count"
    ]
    summary["reconciliation_difference_count_by_metric"] = source_reconciliation[
        "source_reconciliation_difference_count_by_metric"
    ]
    summary["reconciliation_difference_count_by_type"] = source_reconciliation[
        "source_reconciliation_difference_count_by_type"
    ]
    summary["reconciliation_failure_sample_limit"] = source_reconciliation[
        "source_reconciliation_failure_sample_limit"
    ]
    summary["reconciliation_failure_sample_truncated_count"] = source_reconciliation[
        "source_reconciliation_failure_sample_truncated_count"
    ]
    summary["reconciliation_failure_samples"] = source_reconciliation[
        "source_reconciliation_failure_samples"
    ]
    summary["deterministic_summary_match"] = deterministic
    summary["internal_rebuild"] = {
        "method": "in_memory_vs_emitted_gzip_reparse_v1",
        "first_summary_sha256": _canonical_sha256(first),
        "second_summary_sha256": _canonical_sha256(second),
    }
    summary.update(dict(schema_validation))
    summary["deterministic_summary_scope"] = "internal_memory_vs_emitted_roundtrip_only"
    summary["cross_run_reproducibility_claimed"] = False
    summary["cross_run_reproducibility_requirement"] = (
        "external_p0_reproducibility_summary_a_b_required"
    )
    summary["summary_fingerprint_sha256"] = _canonical_sha256(
        {"schema": "metric_reconciliation_summary_fingerprint_v1", "summary": summary}
    )
    return summary


def _json_file_inventory(path: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "media_type": "application/json",
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


FEATURE_QUERY = """
    SELECT "t", "v4prefix_num", "v6prefix_num", "v4ip_num",
           "announ_num", "withdraw_num"
    FROM "public"."feature_country"
    WHERE "source" = %s
      AND "country" = %s
      AND "t" >= %s::timestamp without time zone
      AND "t" < %s::timestamp without time zone
    ORDER BY "t"
"""


def _local_database_boundary(value: datetime, profile: Mapping[str, Any]) -> str:
    timezone_name = profile.get("timezone")
    if not isinstance(timezone_name, str):
        raise MetricCandidateError("数据档 timezone 非法")
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M:%S")


def _feature_row(row: Any, profile: Mapping[str, Any]) -> tuple[datetime, dict[str, int]]:
    if isinstance(row, Mapping):
        values = (
            row.get("t"),
            row.get("v4prefix_num"),
            row.get("v6prefix_num"),
            row.get("v4ip_num"),
            row.get("announ_num"),
            row.get("withdraw_num"),
        )
    else:
        values = tuple(row)
    if len(values) != 6:
        raise MetricCandidateError("feature_country 查询列数异常")
    raw_time = values[0]
    if not isinstance(raw_time, datetime):
        raise MetricCandidateError("feature_country.t 不是 datetime")
    if raw_time.tzinfo is None or raw_time.utcoffset() is None:
        raw_time = raw_time.replace(tzinfo=ZoneInfo(profile["timezone"]))
    slot = raw_time.astimezone(UTC)
    if slot.microsecond or int(slot.timestamp()) % GRANULARITY_SECONDS:
        raise MetricCandidateError("feature_country.t 未对齐五分钟网格")
    names = ("v4prefix_num", "v6prefix_num", "v4ip_num", "announ_num", "withdraw_num")
    result: dict[str, int] = {}
    for name, value in zip(names, values[1:]):
        if isinstance(value, Decimal):
            value = int(value) if value == value.to_integral_value() else value
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MetricCandidateError("feature_country.{} 必须是非负非空整数".format(name))
        result[name] = value
    return slot, result


def _query_feature_rows(
    connection: Any,
    *,
    profile: Mapping[str, Any],
    context: Mapping[str, Any],
    database_config: Mapping[str, str],
    window_start: datetime,
    window_end: datetime,
    statement_timeout_ms: int,
    security_verifier: Callable[..., Mapping[str, Any]],
) -> tuple[dict[datetime, dict[str, int]], dict[str, Any]]:
    cursor = connection.cursor()
    rows: dict[datetime, dict[str, int]] = {}
    try:
        _begin_readonly_transaction(cursor, statement_timeout_ms)
        security = security_verifier(
            cursor,
            expected_user=database_config["DOMEYE_CORE_DB_READER_USER"],
            expected_database=database_config["DOMEYE_CORE_DB_NAME"],
            expected_system=context["system_identifier"],
        )
        cursor.execute(
            FEATURE_QUERY,
            (
                "r",
                "collect",
                _local_database_boundary(window_start, profile),
                _local_database_boundary(window_end, profile),
            ),
        )
        while True:
            if hasattr(cursor, "fetchmany"):
                batch = cursor.fetchmany(1000)
            else:
                batch = cursor.fetchall()
            if not batch:
                break
            for raw in batch:
                slot, values = _feature_row(raw, profile)
                if not window_start <= slot < window_end:
                    raise MetricCandidateError("feature_country 查询返回窗口外行")
                if slot in rows:
                    raise MetricCandidateError("global dense feature_country 同一槽存在重复行")
                rows[slot] = values
            if not hasattr(cursor, "fetchmany"):
                break
        return rows, {
            "database": security.get("database"),
            "current_user": security.get("current_user", database_config["DOMEYE_CORE_DB_READER_USER"]),
            "system_identifier": security.get("system_identifier", context["system_identifier"]),
            "transaction_read_only": True,
            "transaction_isolation": "repeatable read",
        }
    finally:
        try:
            connection.rollback()
        except Exception:
            pass
        try:
            cursor.close()
        except Exception:
            pass


class _IncidentIndex:
    """使用临时 SQLite 保持 117 万 stable ID 去重和区间扫描不依赖内存。"""

    def __init__(self, path: Path):
        self.path = path
        self.connection = sqlite3.connect(str(path))
        self.connection.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            PRAGMA temp_store=FILE;
            CREATE TABLE incidents (
                incident_id TEXT PRIMARY KEY,
                payload_sha256 TEXT NOT NULL,
                event_epoch INTEGER NOT NULL,
                event_type TEXT NOT NULL
            );
            CREATE INDEX incidents_time ON incidents(event_epoch, incident_id);
            CREATE TABLE outage_intervals (
                kind TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                start_epoch INTEGER NOT NULL,
                end_epoch INTEGER NOT NULL
            );
            CREATE INDEX outage_order ON outage_intervals(kind, subject_id, start_epoch, end_epoch);
            CREATE TABLE outage_unknown_intervals (
                kind TEXT NOT NULL,
                start_epoch INTEGER NOT NULL,
                end_epoch INTEGER NOT NULL
            );
            CREATE INDEX outage_unknown_order ON outage_unknown_intervals(kind, start_epoch, end_epoch);
            """
        )

    def add_incident(
        self, incident_id: str, payload_sha256: str, event_epoch: int, event_type: str
    ) -> tuple[bool, bool]:
        try:
            self.connection.execute(
                "INSERT INTO incidents VALUES (?, ?, ?, ?)",
                (incident_id, payload_sha256, event_epoch, event_type),
            )
            return True, False
        except sqlite3.IntegrityError:
            row = self.connection.execute(
                "SELECT payload_sha256 FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
            return False, row is None or row[0] != payload_sha256

    def add_interval(self, kind: str, subject_id: str, start_epoch: int, end_epoch: int) -> None:
        self.connection.execute(
            "INSERT INTO outage_intervals VALUES (?, ?, ?, ?)",
            (kind, subject_id, start_epoch, end_epoch),
        )

    def add_unknown_interval(self, kind: str, start_epoch: int, end_epoch: int) -> None:
        self.connection.execute(
            "INSERT INTO outage_unknown_intervals VALUES (?, ?, ?)",
            (kind, start_epoch, end_epoch),
        )

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


def _parse_json_line(line: bytes, line_number: int) -> dict[str, Any]:
    if not line.endswith(b"\n"):
        raise MetricCandidateError("D2 incidents 第 {} 行缺少换行符".format(line_number))
    if len(line) > 64 * 1024 * 1024:
        raise MetricCandidateError("D2 incidents 第 {} 行超过 64 MiB".format(line_number))
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MetricCandidateError("D2 incidents 第 {} 行不是 UTF-8".format(line_number)) from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as error:
        raise MetricCandidateError("D2 incidents 第 {} 行 JSON 非法".format(line_number)) from error
    if not isinstance(value, dict):
        raise MetricCandidateError("D2 incidents 第 {} 行顶层不是对象".format(line_number))
    return value


def _end_quality_is_explicit(record: Mapping[str, Any]) -> bool:
    quality = record.get("field_quality")
    if not isinstance(quality, list):
        return False
    return any(isinstance(item, Mapping) and item.get("field") == "end_time_utc" for item in quality)


def _outage_subjects(
    record: Mapping[str, Any], kind: str
) -> Optional[tuple[str, ...]]:
    """提取一个中断 Incident 中可精确计数的规范对象集合。

    prefix outage 的业务身份必须仍是唯一前缀；AS outage 则允许 D2 已逐成员
    校验并展开的 ``{asn,...}`` 原生集合。后者每个成员都是并发公式中的一个
    distinct ASN，不能因为成员数大于一而把整段已知事实降级成 unknown。
    """

    objects = record.get("affected_objects")
    if not isinstance(objects, list):
        return None
    expected_type = "prefix" if kind == "prefix" else "asn"
    identities = {
        item.get("object_id")
        for item in objects
        if isinstance(item, Mapping)
        and item.get("object_type") == expected_type
        and item.get("role") == "affected"
        and isinstance(item.get("object_id"), str)
    }
    if kind == "asn":
        if not identities or any(ASN_RE.fullmatch(item) is None for item in identities):
            return None
        return tuple(sorted(identities, key=lambda item: (int(item), item)))
    if len(identities) != 1:
        return None
    subject_id = next(iter(identities))
    try:
        canonical = str(ipaddress.ip_network(subject_id, strict=True))
    except ValueError:
        return None
    return (subject_id,) if canonical == subject_id else None


def _scan_incidents(
    d2: Mapping[str, Any],
    index: _IncidentIndex,
    *,
    profile_start: datetime,
    profile_end: datetime,
) -> dict[str, Any]:
    inventory = d2["inventory"]
    raw_content_sha = hashlib.sha256()
    row_count = 0
    unique_count = 0
    duplicate_identical = 0
    duplicate_conflicting = 0
    event_type_counts: Counter[str] = Counter()
    limitations = {
        "prefix_outage_concurrent_count": Counter(),
        "as_outage_concurrent_count": Counter(),
    }
    try:
        stream = gzip.open(d2["incidents_path"], "rb")
    except OSError as error:
        raise MetricCandidateError("无法打开 D2 incidents gzip") from error
    try:
        for row_count, line in enumerate(stream, 1):
            raw_content_sha.update(line)
            record = _parse_json_line(line, row_count)
            if _canonical_bytes(record, newline=True) != line:
                raise MetricCandidateError("D2 incidents 第 {} 行不是规范 JSONL".format(row_count))
            incident_id = record.get("incident_id")
            event_type = record.get("event_type")
            if record.get("schema_version") != "p0_incident_normalization_v1":
                raise MetricCandidateError("D2 incidents 第 {} 行 schema_version 非法".format(row_count))
            if record.get("incident_id_schema") != "incident_id_v1":
                raise MetricCandidateError("D2 incidents 第 {} 行 incident_id_schema 非法".format(row_count))
            if not isinstance(incident_id, str) or not INCIDENT_ID_RE.fullmatch(incident_id):
                raise MetricCandidateError("D2 incidents 第 {} 行 incident_id 非法".format(row_count))
            if event_type not in EVENT_TYPES:
                raise MetricCandidateError("D2 incidents 第 {} 行 event_type 非法".format(row_count))
            if record.get("classification") != "observation_only" or record.get("causal_conclusion") is not None:
                raise MetricCandidateError("D2 incidents 第 {} 行越过观测/因果边界".format(row_count))
            event_time = _parse_time(record.get("event_time_utc"), "Incident.event_time_utc")
            if not profile_start <= event_time < profile_end:
                raise MetricCandidateError("D2 Incident 时间越出固定窗口")
            payload_sha = hashlib.sha256(_canonical_bytes(record)).hexdigest()
            inserted, conflicting = index.add_incident(
                incident_id, payload_sha, int(event_time.timestamp()), event_type
            )
            if not inserted:
                if conflicting:
                    duplicate_conflicting += 1
                else:
                    duplicate_identical += 1
                continue
            unique_count += 1
            event_type_counts[event_type] += 1
            if unique_count % 10000 == 0:
                index.commit()

            if event_type not in {"prefix_outage", "as_outage"}:
                continue
            metric_name = (
                "prefix_outage_concurrent_count"
                if event_type == "prefix_outage"
                else "as_outage_concurrent_count"
            )
            kind = "prefix" if event_type == "prefix_outage" else "asn"
            metric_limitations = limitations[metric_name]
            uncertainty_reasons: list[str] = []
            if record.get("fact_link_status") != "matched":
                uncertainty_reasons.append("outage_incident_not_matched")
            subject_ids = _outage_subjects(record, kind)
            if subject_ids is None:
                uncertainty_reasons.append(
                    "missing_or_ambiguous_affected_{}".format(kind)
                )
            end_raw = record.get("end_time_utc")
            end_time: Optional[datetime] = None
            if end_raw is None:
                reason = (
                    "end_time_explicitly_unavailable"
                    if _end_quality_is_explicit(record)
                    else "end_time_quality_missing"
                )
                uncertainty_reasons.append(reason)
            else:
                try:
                    end_time = _parse_time(end_raw, "Incident.end_time_utc")
                except MetricCandidateError:
                    uncertainty_reasons.append("invalid_end_time")
                if end_time is not None and end_time <= event_time:
                    uncertainty_reasons.append("nonpositive_outage_interval")
                    end_time = None

            if uncertainty_reasons:
                for reason in sorted(set(uncertainty_reasons)):
                    metric_limitations[reason] += 1
                # 有合法结束时间时只把真实区间标成未知；历史没有结束时间或
                # 结束时间自相矛盾时，无法安全推定恢复点，因此保守延伸到窗口末。
                uncertain_end = end_time if end_time is not None else profile_end
                clipped_start = max(event_time, profile_start)
                clipped_end = min(uncertain_end, profile_end)
                if clipped_start < clipped_end:
                    index.add_unknown_interval(
                        kind,
                        int(clipped_start.timestamp()),
                        int(clipped_end.timestamp()),
                    )
                continue

            assert end_time is not None and subject_ids is not None
            clipped_start = max(event_time, profile_start)
            clipped_end = min(end_time, profile_end)
            if clipped_start < clipped_end:
                for subject_id in subject_ids:
                    index.add_interval(
                        kind,
                        subject_id,
                        int(clipped_start.timestamp()),
                        int(clipped_end.timestamp()),
                    )
    except (OSError, EOFError, gzip.BadGzipFile) as error:
        raise MetricCandidateError("D2 incidents gzip 解压失败") from error
    finally:
        stream.close()
    index.commit()
    if row_count != inventory["row_count"]:
        raise MetricCandidateError("D2 incidents row_count 与 manifest 不一致")
    if raw_content_sha.hexdigest() != inventory["content_sha256"]:
        raise MetricCandidateError("D2 incidents content_sha256 与 manifest 不一致")
    manifest_incidents = d2["manifest"].get("summary", {}).get("incident_count")
    if manifest_incidents != row_count:
        raise MetricCandidateError("D2 manifest summary.incident_count 与 JSONL 不一致")
    return {
        "row_count": row_count,
        "unique_incident_count": unique_count,
        "duplicate_identical_count": duplicate_identical,
        "duplicate_conflicting_count": duplicate_conflicting,
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "metric_limitations": {
            name: dict(sorted(counter.items()))
            for name, counter in limitations.items()
            if counter
        },
    }


def _expected_slots(start: datetime, end: datetime) -> tuple[datetime, ...]:
    if start >= end or int(start.timestamp()) % GRANULARITY_SECONDS or int(end.timestamp()) % GRANULARITY_SECONDS:
        raise MetricCandidateError("MetricSeries 窗口必须是对齐的非空五分钟半开区间")
    return tuple(
        start + timedelta(seconds=index * GRANULARITY_SECONDS)
        for index in range(int((end - start).total_seconds()) // GRANULARITY_SECONDS)
    )


def _projected_missing_point(
    slot: datetime,
    state: str,
    *,
    ratio: bool = False,
) -> dict[str, Any]:
    """独立源投影使用的缺失点；不调用 MetricSeries builder 私有函数。"""

    return {
        "time": _utc_text(slot),
        "value": None,
        "value_state": state,
        "missing_reason": state,
        "formula_inputs": (
            {
                "numerator_withdraw_count": None,
                "denominator_update_total": None,
            }
            if ratio
            else None
        ),
    }


def _projected_observed_point(
    slot: datetime,
    value: Any,
    *,
    formula_inputs: Any = None,
) -> dict[str, Any]:
    """独立源投影使用的观测点；零值必须来自实际源行或实际采样。"""

    return {
        "time": _utc_text(slot),
        "value": value,
        "value_state": "observed_zero" if value == 0 else "observed_nonzero",
        "missing_reason": None,
        "formula_inputs": formula_inputs,
    }


def _feature_source_expected_points(
    metric_name: str,
    slots: Sequence[datetime],
    source_slots: Iterable[datetime],
    parse_failed_slots: Iterable[datetime],
    processing_gap_slots: Iterable[datetime],
    feature_rows: Mapping[datetime, Mapping[str, int]],
) -> list[dict[str, Any]]:
    """从 D3 源槽和只读 ``feature_country`` 行直接重算七项期望点。"""

    if metric_name not in FEATURE_METRICS:
        raise MetricCandidateError("未知 feature 源投影指标：{}".format(metric_name))
    source_set = set(source_slots)
    parse_failed_set = set(parse_failed_slots)
    processing_set = set(processing_gap_slots)
    points: list[dict[str, Any]] = []
    for slot in slots:
        is_ratio = metric_name == "bgp_withdraw_ratio"
        if slot in parse_failed_set:
            points.append(
                _projected_missing_point(slot, "parse_failed", ratio=is_ratio)
            )
            continue
        if slot not in source_set:
            points.append(
                _projected_missing_point(slot, "source_unavailable", ratio=is_ratio)
            )
            continue
        if slot in processing_set:
            points.append(
                _projected_missing_point(slot, "processing_gap", ratio=is_ratio)
            )
            continue
        values = feature_rows.get(slot)
        if values is None:
            raise MetricCandidateError("feature 源投影发现未分类的查询缺行")
        if metric_name == "bgp_announce_record_count":
            value = values["announ_num"]
        elif metric_name == "bgp_withdraw_record_count":
            value = values["withdraw_num"]
        elif metric_name == "bgp_update_record_count":
            value = values["announ_num"] + values["withdraw_num"]
        elif metric_name == "bgp_withdraw_ratio":
            numerator = values["withdraw_num"]
            denominator = values["announ_num"] + numerator
            formula_inputs = {
                "numerator_withdraw_count": numerator,
                "denominator_update_total": denominator,
            }
            if denominator == 0:
                points.append(
                    {
                        "time": _utc_text(slot),
                        "value": None,
                        "value_state": "not_applicable",
                        "missing_reason": "denominator_zero",
                        "formula_inputs": formula_inputs,
                    }
                )
            else:
                points.append(
                    _projected_observed_point(
                        slot,
                        numerator / denominator,
                        formula_inputs=formula_inputs,
                    )
                )
            continue
        elif metric_name == "ipv4_24_equivalent_count":
            value = values["v4prefix_num"]
        elif metric_name == "ipv6_48_equivalent_count":
            value = values["v6prefix_num"]
        elif metric_name == "ipv4_equivalent_address_count":
            value = values["v4ip_num"]
        else:  # pragma: no cover - 上方准入集合和分支共同封闭
            raise MetricCandidateError("feature 源投影指标未实现")
        points.append(_projected_observed_point(slot, value))
    return points


def _anomaly_source_expected_points(
    index: _IncidentIndex,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """从 SQLite stable Incident 槽独立聚合异常数，不复用 incident ID 行。"""

    counts: Counter[int] = Counter()
    cursor = index.connection.execute(
        "SELECT event_epoch, COUNT(*) FROM incidents "
        "WHERE event_epoch >= ? AND event_epoch < ? "
        "GROUP BY event_epoch ORDER BY event_epoch",
        (int(start.timestamp()), int(end.timestamp())),
    )
    for event_epoch, count in cursor:
        bucket = event_epoch - (event_epoch % GRANULARITY_SECONDS)
        counts[bucket] += count
    return [
        _projected_observed_point(slot, counts[int(slot.timestamp())])
        for slot in _expected_slots(start, end)
    ]


def _concurrency_source_expected_points(
    index: _IncidentIndex,
    kind: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """从原始 interval/unknown 表独立扫描 180 秒网格并重算最大并发。

    builder 路径先按对象合并区间再生成 change map；本路径直接使用原始区间
    的 start/end 双游标和引用计数，独立落实 ``[start,end)`` 语义。未知区间
    也从原始表单独扫描，只要某个 180 秒样本受影响，所属五分钟槽即为
    ``legacy_unknown``。
    """

    if kind not in {"prefix", "asn"}:
        raise MetricCandidateError("并发源投影 kind 非法")
    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())
    interval_parameters = (kind, end_epoch, start_epoch)
    starts = index.connection.execute(
        "SELECT subject_id, start_epoch, end_epoch FROM outage_intervals "
        "WHERE kind = ? AND start_epoch < ? AND end_epoch > ? "
        "ORDER BY start_epoch, end_epoch, subject_id",
        interval_parameters,
    )
    ends = index.connection.execute(
        "SELECT subject_id, start_epoch, end_epoch FROM outage_intervals "
        "WHERE kind = ? AND start_epoch < ? AND end_epoch > ? "
        "ORDER BY end_epoch, start_epoch, subject_id",
        interval_parameters,
    )
    unknown_parameters = (kind, end_epoch, start_epoch)
    unknown_starts = index.connection.execute(
        "SELECT start_epoch, end_epoch FROM outage_unknown_intervals "
        "WHERE kind = ? AND start_epoch < ? AND end_epoch > ? "
        "ORDER BY start_epoch, end_epoch",
        unknown_parameters,
    )
    unknown_ends = index.connection.execute(
        "SELECT start_epoch, end_epoch FROM outage_unknown_intervals "
        "WHERE kind = ? AND start_epoch < ? AND end_epoch > ? "
        "ORDER BY end_epoch, start_epoch",
        unknown_parameters,
    )

    next_start = next(starts, None)
    next_end = next(ends, None)
    next_unknown_start = next(unknown_starts, None)
    next_unknown_end = next(unknown_ends, None)
    active: Counter[str] = Counter()
    active_unknown_count = 0
    bucket_maximum: dict[int, int] = {}
    unknown_buckets: set[int] = set()
    sample = _ceil_grid(start_epoch, CONCURRENCY_SECONDS)
    while sample < end_epoch:
        while next_start is not None and next_start[1] <= sample:
            active[next_start[0]] += 1
            next_start = next(starts, None)
        while next_end is not None and next_end[2] <= sample:
            subject_id = next_end[0]
            active[subject_id] -= 1
            if active[subject_id] < 0:
                raise MetricCandidateError("并发源投影区间引用计数下溢")
            if active[subject_id] == 0:
                del active[subject_id]
            next_end = next(ends, None)
        while next_unknown_start is not None and next_unknown_start[0] <= sample:
            active_unknown_count += 1
            next_unknown_start = next(unknown_starts, None)
        while next_unknown_end is not None and next_unknown_end[1] <= sample:
            active_unknown_count -= 1
            if active_unknown_count < 0:
                raise MetricCandidateError("并发未知区间引用计数下溢")
            next_unknown_end = next(unknown_ends, None)

        bucket = sample - (sample % GRANULARITY_SECONDS)
        if start_epoch <= bucket < end_epoch:
            bucket_maximum[bucket] = max(bucket_maximum.get(bucket, 0), len(active))
            if active_unknown_count:
                unknown_buckets.add(bucket)
        sample += CONCURRENCY_SECONDS

    slots = _expected_slots(start, end)
    expected_bucket_set = {int(slot.timestamp()) for slot in slots}
    if set(bucket_maximum) != expected_bucket_set:
        raise MetricCandidateError("独立 180 秒源投影未覆盖全部五分钟槽")
    return [
        _projected_missing_point(slot, "legacy_unknown")
        if int(slot.timestamp()) in unknown_buckets
        else _projected_observed_point(
            slot, bucket_maximum[int(slot.timestamp())]
        )
        for slot in slots
    ]


def _anomaly_rows(index: _IncidentIndex, start: datetime, end: datetime) -> list[dict[str, Any]]:
    slots = _expected_slots(start, end)
    by_slot: dict[int, list[str]] = {int(slot.timestamp()): [] for slot in slots}
    cursor = index.connection.execute(
        "SELECT event_epoch, incident_id FROM incidents "
        "WHERE event_epoch >= ? AND event_epoch < ? ORDER BY event_epoch, incident_id",
        (int(start.timestamp()), int(end.timestamp())),
    )
    for event_epoch, incident_id in cursor:
        bucket = event_epoch - (event_epoch % GRANULARITY_SECONDS)
        if bucket in by_slot:
            by_slot[bucket].append(incident_id)
    return [
        {"time": _utc_text(slot), "incident_ids": by_slot[int(slot.timestamp())]}
        for slot in slots
    ]


def _merged_intervals(index: _IncidentIndex, kind: str) -> Iterator[tuple[str, int, int]]:
    cursor = index.connection.execute(
        "SELECT subject_id, start_epoch, end_epoch FROM outage_intervals "
        "WHERE kind = ? ORDER BY subject_id, start_epoch, end_epoch",
        (kind,),
    )
    current_subject: Optional[str] = None
    current_start = current_end = 0
    for subject_id, start_epoch, end_epoch in cursor:
        if current_subject == subject_id and start_epoch <= current_end:
            current_end = max(current_end, end_epoch)
            continue
        if current_subject is not None:
            yield current_subject, current_start, current_end
        current_subject = subject_id
        current_start, current_end = start_epoch, end_epoch
    if current_subject is not None:
        yield current_subject, current_start, current_end


def _merged_unknown_intervals(
    index: _IncidentIndex, kind: str
) -> Iterator[tuple[int, int]]:
    """合并任意身份的未知区间，避免按每条历史事件重复展开整窗采样。"""

    cursor = index.connection.execute(
        "SELECT start_epoch, end_epoch FROM outage_unknown_intervals "
        "WHERE kind = ? ORDER BY start_epoch, end_epoch",
        (kind,),
    )
    current_start: Optional[int] = None
    current_end = 0
    for start_epoch, end_epoch in cursor:
        if current_start is not None and start_epoch <= current_end:
            current_end = max(current_end, end_epoch)
            continue
        if current_start is not None:
            yield current_start, current_end
        current_start, current_end = start_epoch, end_epoch
    if current_start is not None:
        yield current_start, current_end


def _ceil_grid(epoch: int, interval: int) -> int:
    return epoch if epoch % interval == 0 else epoch + interval - (epoch % interval)


def _concurrency_rows(
    index: _IncidentIndex,
    kind: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())
    changes: dict[int, dict[str, int]] = defaultdict(dict)
    for subject_id, interval_start, interval_end in _merged_intervals(index, kind):
        first = _ceil_grid(max(interval_start, start_epoch), CONCURRENCY_SECONDS)
        stop = _ceil_grid(min(interval_end, end_epoch), CONCURRENCY_SECONDS)
        if first >= stop or first >= end_epoch:
            continue
        changes[first][subject_id] = changes[first].get(subject_id, 0) + 1
        if stop < end_epoch:
            changes[stop][subject_id] = changes[stop].get(subject_id, 0) - 1

    slots = _expected_slots(start, end)
    rows: dict[int, dict[str, Any]] = {
        int(slot.timestamp()): {"time": _utc_text(slot), "concurrency_samples": []}
        for slot in slots
    }
    active: set[str] = set()
    sample = _ceil_grid(start_epoch, CONCURRENCY_SECONDS)
    while sample < end_epoch:
        for subject_id, delta in sorted(changes.get(sample, {}).items()):
            if delta > 0:
                active.add(subject_id)
            elif delta < 0:
                active.discard(subject_id)
        bucket = sample - (sample % GRANULARITY_SECONDS)
        if bucket in rows:
            rows[bucket]["concurrency_samples"].append(
                {
                    "time": _utc_text(datetime.fromtimestamp(sample, tz=UTC)),
                    "distinct_subject_count": len(active),
                    "identity_validation": "d2_stable_incident_interval_index_v1",
                }
            )
        sample += CONCURRENCY_SECONDS
    if any(not row["concurrency_samples"] for row in rows.values()):
        raise MetricCandidateError("180 秒网格未覆盖所有五分钟槽")
    return [rows[int(slot.timestamp())] for slot in slots]


def _concurrency_unknown_slots(
    index: _IncidentIndex,
    kind: str,
    start: datetime,
    end: datetime,
) -> tuple[datetime, ...]:
    """返回因历史区间边界不完整而无法计算的五分钟槽。

    只在同一 180 秒采样语义下实际可能受影响的桶中标记 unknown；事件发生前
    的桶仍可计算，绝不把未知结束时间解释为“持续到窗口末”的观测事实。
    """

    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())
    buckets: set[int] = set()
    for interval_start, interval_end in _merged_unknown_intervals(index, kind):
        first = _ceil_grid(max(interval_start, start_epoch), CONCURRENCY_SECONDS)
        stop = _ceil_grid(min(interval_end, end_epoch), CONCURRENCY_SECONDS)
        sample = first
        while sample < stop and sample < end_epoch:
            bucket = sample - (sample % GRANULARITY_SECONDS)
            if start_epoch <= bucket < end_epoch:
                buckets.add(bucket)
            sample += CONCURRENCY_SECONDS
    return tuple(datetime.fromtimestamp(epoch, tz=UTC) for epoch in sorted(buckets))


def _feature_rows_for_metric(
    metric_name: str,
    feature_rows: Mapping[datetime, Mapping[str, int]],
) -> list[dict[str, Any]]:
    result = []
    for slot in sorted(feature_rows):
        values = feature_rows[slot]
        row: dict[str, Any] = {"time": _utc_text(slot)}
        if metric_name == "bgp_announce_record_count":
            row["announ_num"] = values["announ_num"]
        elif metric_name == "bgp_withdraw_record_count":
            row["withdraw_num"] = values["withdraw_num"]
        elif metric_name in {"bgp_update_record_count", "bgp_withdraw_ratio"}:
            row["announ_num"] = values["announ_num"]
            row["withdraw_num"] = values["withdraw_num"]
        elif metric_name == "ipv4_24_equivalent_count":
            row["v4prefix_num"] = values["v4prefix_num"]
        elif metric_name == "ipv6_48_equivalent_count":
            row["v6prefix_num"] = values["v6prefix_num"]
        elif metric_name == "ipv4_equivalent_address_count":
            row["v4ip_num"] = values["v4ip_num"]
        else:
            raise MetricCandidateError("未知 feature metric：{}".format(metric_name))
        result.append(row)
    return result


def _source_refs_for_feature(d3: Mapping[str, Any], context: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "source_layer": "raw_observation",
            "ref_id": "manifest:{}".format(d3["fingerprint_sha256"]),
            "locator": "artifact-manifest/update-slots",
            "sha256": d3["manifest_sha256"],
        },
        {
            "source_layer": "release_inventory",
            "ref_id": "database-inventory:{}".format(context["release_id"]),
            "locator": "public.feature_country/source=r/country=collect",
            "sha256": context["inventory_sha256"],
        },
    ]


def _source_refs_for_incidents(d2: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "source_layer": "detection_fact",
            "ref_id": "normalized-incidents:{}".format(d2["fingerprint_sha256"]),
            "locator": "incidents.jsonl.gz",
            "sha256": d2["incidents_sha256"],
        },
        {
            "source_layer": "release_inventory",
            "ref_id": "normalization-manifest:{}".format(d2["fingerprint_sha256"]),
            "locator": "normalization-candidate/manifest.json",
            "sha256": d2["manifest_sha256"],
        },
    ]


def _summary_markdown(manifest: Mapping[str, Any]) -> str:
    summary = manifest["summary"]
    admission = manifest["admission"]
    blocker_lines = []
    for metric_name in sorted(summary["metric_blockers"]):
        reasons = summary["metric_blockers"][metric_name]
        if reasons:
            rendered = "；".join("{}={}".format(key, value) for key, value in sorted(reasons.items()))
            blocker_lines.append("- `{}`：{}".format(metric_name, rendered))
    if not blocker_lines:
        blocker_lines.append("- 无")
    limitation_lines = []
    for metric_name in sorted(summary.get("metric_limitations", {})):
        reasons = summary["metric_limitations"][metric_name]
        rendered = "；".join(
            "{}={}".format(key, value) for key, value in sorted(reasons.items())
        )
        limitation_lines.append("- `{}`：{}".format(metric_name, rendered))
    if not limitation_lines:
        limitation_lines.append("- 无")
    return """# P0 D2 MetricSeries 候选摘要

## 候选结论

- 数据档：`{profile}`
- 指标窗口：`{start} <= t < {end}`
- 候选指纹：`{fingerprint}`
- 已生成指标：{generated}/10
- 准入状态：`{status}`
- legacy_unknown 点数：{legacy_unknown}
- 不可完整计算指标：`{not_fully_computable}`
- sample：`{sample}`

## 数据闭包

- D2 normalization fingerprint：`{d2}`
- D2 incidents SHA256：`{d2_incidents}`
- D3 artifact fingerprint：`{d3}`
- D3 manifest SHA256：`{d3_manifest}`
- D3 UPDATE 可用槽：{source_slots}
- D3 UPDATE 内容无效隔离槽：{invalid_source_slots}
- feature_country 已观测槽：{feature_slots}
- feature_country 位于无效原始槽而被忽略的行：{ignored_invalid_rows}
- feature_country processing_gap：{processing_gaps}
- distinct Incident：{incidents}

## 逐指标阻断

{blockers}

## 历史数据限制（显式缺失，不补 0）

{limitations}

## 解释边界

七个采集特征指标只使用 D3 已校验且内容有效的 UPDATE 槽判断源覆盖；D3
隔离的内容无效槽保持 `parse_failed`，即使 `feature_country` 恰有对应行
也不消费、不补 0。D3 有有效 UPDATE 而 `feature_country` 没有 collect 行的槽
明确标记为 `processing_gap`。异常指标
只消费 D2 full 规范 Incident；两类并发严格使用半开区间 `[start,end)` 和
Unix epoch 对齐的 180 秒采样点。

若历史中断事件没有可靠结束时间或唯一对象，仅把同一 180 秒采样语义下受
影响的五分钟槽输出为 `value=null,value_state=legacy_unknown`。这不表示事件
持续到窗口末，只表示这些槽无法得到精确并发数；事件前可计算槽仍保留实值。

所有指标均为 `observation_only`，`causal_conclusion` 固定为 `null`。缺失、
未保留、查询失败和不可计算值均未补为 0。
""".format(
        profile=manifest["data_profile"]["id"],
        start=manifest["metric_window_utc"]["start"],
        end=manifest["metric_window_utc"]["end_exclusive"],
        fingerprint=manifest["candidate_fingerprint_sha256"],
        generated=summary["generated_metric_count"],
        status=admission["status"],
        legacy_unknown=summary["legacy_unknown_point_count"],
        not_fully_computable=",".join(summary["not_fully_computable_metric_names"])
        or "无",
        sample=str(manifest["sample"]["enabled"]).lower(),
        d2=manifest["sources"]["d2_normalization"]["fingerprint_sha256"],
        d2_incidents=manifest["sources"]["d2_normalization"]["incidents_sha256"],
        d3=manifest["sources"]["d3_artifacts"]["fingerprint_sha256"],
        d3_manifest=manifest["sources"]["d3_artifacts"]["manifest_sha256"],
        source_slots=summary["feature_source_available_slot_count"],
        invalid_source_slots=summary["feature_invalid_source_slot_count"],
        feature_slots=summary["feature_observed_slot_count"],
        ignored_invalid_rows=summary[
            "feature_rows_on_invalid_source_slot_ignored_count"
        ],
        processing_gaps=summary["feature_processing_gap_slot_count"],
        incidents=summary["unique_incident_count"],
        blockers="\n".join(blocker_lines),
        limitations="\n".join(limitation_lines),
    )


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(
        _json_ready(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ).encode("utf-8") + b"\n"
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _write_checksums(directory: Path, names: Iterable[str]) -> None:
    lines = ["{}  {}".format(_sha256(directory / name), name) for name in sorted(names)]
    path = directory / "SHA256SUMS"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def generate_metric_candidate(
    connection: Any,
    *,
    profile: Mapping[str, Any],
    context: Mapping[str, Any],
    database_config: Mapping[str, str],
    provenance: Mapping[str, Any],
    project_root: Path,
    pipeline_root: Path,
    d2_candidate_dir: Path,
    d3_manifest_path: Path,
    d3_summary_path: Path,
    d3_checksum_path: Path,
    output_dir: Path,
    generated_at: str,
    ajv_module: Optional[Path] = None,
    sample_window_start: Optional[str] = None,
    sample_window_end_exclusive: Optional[str] = None,
    statement_timeout_ms: int = 3_600_000,
    security_verifier: Callable[..., Mapping[str, Any]] = _verify_reader_security,
) -> dict[str, Any]:
    """生成候选；fake connection 测试与 CLI 共用同一实现。"""

    profile_start = profile["parsed"]["start"].astimezone(UTC)
    profile_end = profile["parsed"]["end_exclusive"].astimezone(UTC)
    sample_enabled = sample_window_start is not None or sample_window_end_exclusive is not None
    if sample_enabled:
        if sample_window_start is None or sample_window_end_exclusive is None:
            raise MetricCandidateError("sample 窗口起止必须同时提供")
        metric_start = _parse_time(sample_window_start, "sample_window_start", aligned=300)
        metric_end = _parse_time(
            sample_window_end_exclusive, "sample_window_end_exclusive", aligned=300
        )
        if not profile_start <= metric_start < metric_end <= profile_end:
            raise MetricCandidateError("sample 窗口必须位于固定数据档内")
    else:
        metric_start, metric_end = profile_start, profile_end

    d2 = _load_d2_input(d2_candidate_dir, profile)
    d3 = _load_d3_input(
        d3_manifest_path, d3_summary_path, d3_checksum_path, profile
    )
    metrics, metric_hashes, resolved_pipeline_root = _load_metrics_module(pipeline_root)
    schema_path = resolved_pipeline_root / METRIC_SCHEMA_RELATIVE_PATH
    _assert_regular(schema_path, "MetricSeries JSON Schema")
    schema_sha = _sha256(schema_path)
    effective_ajv_module = (
        Path(ajv_module)
        if ajv_module is not None
        else resolved_pipeline_root / AJV_RELATIVE_PATH
    )

    target, staging = _prepare_staging(output_dir)
    completed = False
    index: Optional[_IncidentIndex] = None
    try:
        feature_rows, database_security = _query_feature_rows(
            connection,
            profile=profile,
            context=context,
            database_config=database_config,
            window_start=metric_start,
            window_end=metric_end,
            statement_timeout_ms=statement_timeout_ms,
            security_verifier=security_verifier,
        )

        index = _IncidentIndex(staging / ".metric-index.sqlite3")
        incident_summary = _scan_incidents(
            d2,
            index,
            profile_start=profile_start,
            profile_end=profile_end,
        )
        metric_limitations: dict[str, Counter[str]] = defaultdict(Counter)
        for metric_name, reasons in incident_summary.get(
            "metric_limitations", {}
        ).items():
            metric_limitations[metric_name].update(reasons)

        expected = _expected_slots(metric_start, metric_end)
        expected_set = set(expected)
        feature_source_slots = tuple(
            slot for slot in d3["update_slots"] if metric_start <= slot < metric_end
        )
        invalid_feature_source_slots = tuple(
            slot
            for slot in d3["invalid_update_slots"]
            if metric_start <= slot < metric_end
        )
        source_set = set(feature_source_slots)
        invalid_source_set = set(invalid_feature_source_slots)
        feature_set = set(feature_rows)
        metric_blockers: dict[str, Counter[str]] = {
            metric_name: Counter() for metric_name in ALL_METRICS
        }
        metric_error_messages: dict[str, list[str]] = defaultdict(list)
        if source_set & invalid_source_set:
            raise MetricCandidateError("D3 有效与无效 UPDATE 槽重叠")
        if not invalid_source_set.issubset(expected_set):
            raise MetricCandidateError("D3 无效 UPDATE 槽越出 MetricSeries 窗口")
        feature_rows_on_invalid_slots = feature_set & invalid_source_set
        extra_feature_slots = feature_set - source_set - invalid_source_set
        if extra_feature_slots:
            for metric_name in FEATURE_METRICS:
                metric_blockers[metric_name]["feature_rows_without_verified_update_artifact"] = len(extra_feature_slots)
        if invalid_source_set:
            for metric_name in FEATURE_METRICS:
                metric_limitations[metric_name][
                    "d3_invalid_update_artifact_slot_parse_failed"
                ] = len(invalid_source_set)
        if feature_rows_on_invalid_slots:
            for metric_name in FEATURE_METRICS:
                metric_limitations[metric_name][
                    "feature_row_on_invalid_update_artifact_ignored"
                ] = len(feature_rows_on_invalid_slots)
        if not source_set.issubset(expected_set):
            raise MetricCandidateError("D3 source slots 越出 MetricSeries 窗口")
        processing_gaps = tuple(sorted(source_set - feature_set))
        expected_processing_gaps = set(KNOWN_PROCESSING_GAPS_UTC) - invalid_source_set
        if not sample_enabled and set(processing_gaps) != expected_processing_gaps:
            for metric_name in FEATURE_METRICS:
                metric_blockers[metric_name]["processing_gap_set_differs_from_frozen_six_slots"] = len(
                    set(processing_gaps) ^ expected_processing_gaps
                )
        if d3["collectors"] != ["rrc25"]:
            for metric_name in FEATURE_METRICS:
                metric_blockers[metric_name]["collector_scope_not_fixed_rrc25"] = len(d3["collectors"])

        duplicate_total = (
            incident_summary["duplicate_identical_count"]
            + incident_summary["duplicate_conflicting_count"]
        )
        if duplicate_total:
            for metric_name in INCIDENT_METRICS:
                metric_blockers[metric_name]["duplicate_incident_ids"] = duplicate_total

        generated_at_utc = _parse_time(generated_at, "generated_at")
        if generated_at_utc < profile["parsed"]["snapshot"].astimezone(UTC):
            raise MetricCandidateError("generated_at 不得早于固定数据档 snapshot_time")
        generated_at_text = _utc_text(generated_at_utc)
        global_subject = {
            "subject_type": "global",
            "subject_id": "global",
            "display_name": "固定数据档全局观测",
        }
        feature_scope = {
            "scope_kind": "collector_set",
            "collector_ids": d3["collectors"],
            "limitation_reason": "仅覆盖 D3 已校验 collector；不代表全网",
        }
        incident_scope = {
            "scope_kind": "legacy_unknown",
            "collector_ids": [],
            "limitation_reason": "D2 历史 source_code=r 未保留稳定 collector/VP 映射",
        }
        # 独立源投影只消费最初的只读查询行、D3 源槽和临时 SQLite 源表。
        # 它与下方 builder 输入对象分开生成，禁止用同一 built 对象自证正确。
        source_expected_points: dict[str, list[dict[str, Any]]] = {
            metric_name: _feature_source_expected_points(
                metric_name,
                expected,
                feature_source_slots,
                invalid_feature_source_slots,
                processing_gaps,
                feature_rows,
            )
            for metric_name in FEATURE_METRICS
        }
        source_expected_points["anomaly_incident_count"] = (
            _anomaly_source_expected_points(index, metric_start, metric_end)
        )
        source_expected_points["prefix_outage_concurrent_count"] = (
            _concurrency_source_expected_points(
                index, "prefix", metric_start, metric_end
            )
        )
        source_expected_points["as_outage_concurrent_count"] = (
            _concurrency_source_expected_points(index, "asn", metric_start, metric_end)
        )
        if set(source_expected_points) != set(ALL_METRICS):
            raise MetricCandidateError("独立源投影未覆盖冻结的十项指标")
        built: dict[str, dict[str, Any]] = {}

        if not extra_feature_slots:
            usable_feature_rows = {
                slot: values
                for slot, values in feature_rows.items()
                if slot in source_set and slot not in set(processing_gaps)
            }
            for metric_name in FEATURE_METRICS:
                try:
                    built[metric_name] = metrics.build_metric_series(
                        metric_name,
                        subject=global_subject,
                        collector_scope=feature_scope,
                        window_start=_utc_text(metric_start),
                        window_end_exclusive=_utc_text(metric_end),
                        source_available_slots=[_utc_text(slot) for slot in feature_source_slots],
                        processing_gap_slots=[_utc_text(slot) for slot in processing_gaps],
                        source_parse_failed_slots=[
                            _utc_text(slot) for slot in invalid_feature_source_slots
                        ],
                        subject_rows=_feature_rows_for_metric(metric_name, usable_feature_rows),
                        source_refs=_source_refs_for_feature(d3, context),
                        generated_at=generated_at_text,
                    )
                except Exception as error:
                    metric_blockers[metric_name]["metric_builder_rejected_input"] += 1
                    metric_error_messages[metric_name].append(
                        "{}: {}".format(type(error).__name__, str(error))[:1000]
                    )
                    built.pop(metric_name, None)

        full_incident_slots = [_utc_text(slot) for slot in expected]
        try:
            built["anomaly_incident_count"] = metrics.build_metric_series(
                "anomaly_incident_count",
                subject=global_subject,
                collector_scope=incident_scope,
                window_start=_utc_text(metric_start),
                window_end_exclusive=_utc_text(metric_end),
                source_available_slots=full_incident_slots,
                processing_gap_slots=[],
                subject_rows=_anomaly_rows(index, metric_start, metric_end),
                source_refs=_source_refs_for_incidents(d2),
                generated_at=generated_at_text,
            )
        except Exception as error:
            metric_blockers["anomaly_incident_count"]["metric_builder_rejected_input"] += 1
            metric_error_messages["anomaly_incident_count"].append(
                "{}: {}".format(type(error).__name__, str(error))[:1000]
            )
            built.pop("anomaly_incident_count", None)

        for metric_name, kind in (
            ("prefix_outage_concurrent_count", "prefix"),
            ("as_outage_concurrent_count", "asn"),
        ):
            if metric_blockers[metric_name]:
                continue
            try:
                unknown_slots = _concurrency_unknown_slots(
                    index, kind, metric_start, metric_end
                )
                unknown_set = set(unknown_slots)
                concurrency_rows = _concurrency_rows(
                    index, kind, metric_start, metric_end
                )
                built[metric_name] = metrics.build_metric_series(
                    metric_name,
                    subject=global_subject,
                    collector_scope=incident_scope,
                    window_start=_utc_text(metric_start),
                    window_end_exclusive=_utc_text(metric_end),
                    source_available_slots=full_incident_slots,
                    processing_gap_slots=[],
                    subject_rows=[
                        row
                        for row in concurrency_rows
                        if _parse_time(row["time"], "concurrency row time", aligned=300)
                        not in unknown_set
                    ],
                    source_refs=_source_refs_for_incidents(d2),
                    generated_at=generated_at_text,
                    metric_missing_slots=[
                        {
                            "time": _utc_text(slot),
                            "value_state": "legacy_unknown",
                            "missing_reason": "legacy_unknown",
                        }
                        for slot in unknown_slots
                    ],
                )
            except Exception as error:
                metric_blockers[metric_name]["metric_builder_rejected_input"] += 1
                metric_error_messages[metric_name].append(
                    "{}: {}".format(type(error).__name__, str(error))[:1000]
                )
                built.pop(metric_name, None)

        index.close()
        index = None
        (staging / ".metric-index.sqlite3").unlink()

        with DeterministicGzipJsonlWriter(staging / METRIC_FILE) as writer:
            for metric_name in sorted(built):
                writer.write(built[metric_name])
        metric_file_inventory = writer.inventory()
        schema_validation = _schema_validate_emitted_metric_series(
            staging / METRIC_FILE,
            schema_path,
            effective_ajv_module,
        )
        if schema_validation["schema_sha256"] != schema_sha:
            raise MetricCandidateError("MetricSeries Schema 在候选生成期间发生变化")
        emitted_records = _read_emitted_metric_series(staging / METRIC_FILE)
        reconciliation = _build_metric_reconciliation_summary(
            [built[name] for name in sorted(built)],
            emitted_records,
            metrics.METRIC_DEFINITIONS,
            schema_validation,
            source_expected_points,
        )
        _write_json(staging / RECONCILIATION_FILE, reconciliation)
        reconciliation_file_inventory = _json_file_inventory(
            staging / RECONCILIATION_FILE
        )

        normalized_blockers = {
            name: dict(sorted(reasons.items()))
            for name, reasons in sorted(metric_blockers.items())
            if reasons
        }
        normalized_limitations = {
            name: dict(sorted(reasons.items()))
            for name, reasons in sorted(metric_limitations.items())
            if reasons
        }
        global_blocking_reasons = []
        if sample_enabled:
            global_blocking_reasons.append("fixture_sample_not_admissible")
        if len(built) != len(ALL_METRICS):
            global_blocking_reasons.append("not_all_ten_metrics_generated")
        if normalized_blockers:
            global_blocking_reasons.append("metric_specific_data_blockers")
        if reconciliation["admitted_metric_count"] != len(ALL_METRICS):
            global_blocking_reasons.append("metric_formula_contract_incomplete")
        if reconciliation["reconciliation_difference_count"] != 0:
            global_blocking_reasons.append("metric_reconciliation_mismatch")
        if reconciliation["internal_structural_difference_count"] != 0:
            global_blocking_reasons.append("metric_internal_structure_mismatch")
        if reconciliation["deterministic_summary_match"] is not True:
            global_blocking_reasons.append("metric_internal_rebuild_mismatch")
        eligible = not global_blocking_reasons
        sample = {
            "enabled": sample_enabled,
            "window_start": _utc_text(metric_start) if sample_enabled else None,
            "window_end_exclusive": _utc_text(metric_end) if sample_enabled else None,
            "admissible": False if sample_enabled else True,
        }
        summary = {
            "expected_metric_count": len(ALL_METRICS),
            "generated_metric_count": len(built),
            "generated_metric_names": sorted(built),
            "missing_metric_names": sorted(set(ALL_METRICS) - set(built)),
            "metric_blockers": normalized_blockers,
            "metric_limitations": normalized_limitations,
            "metric_error_messages": {
                name: sorted(set(messages))
                for name, messages in sorted(metric_error_messages.items())
                if messages
            },
            "feature_source_available_slot_count": len(feature_source_slots),
            "feature_invalid_source_slot_count": len(invalid_feature_source_slots),
            "feature_invalid_source_slots": [
                _utc_text(slot) for slot in invalid_feature_source_slots
            ],
            "feature_rows_on_invalid_source_slot_ignored_count": len(
                feature_rows_on_invalid_slots
            ),
            "feature_rows_on_invalid_source_slots_ignored": [
                _utc_text(slot) for slot in sorted(feature_rows_on_invalid_slots)
            ],
            "feature_observed_slot_count": len(feature_set & source_set),
            "feature_processing_gap_slot_count": len(processing_gaps),
            "feature_processing_gap_slots": [_utc_text(slot) for slot in processing_gaps],
            "legacy_unknown_point_count": reconciliation["legacy_unknown_point_count"],
            "legacy_unknown_point_count_by_metric": reconciliation[
                "legacy_unknown_point_count_by_metric"
            ],
            "not_fully_computable_metric_names": sorted(
                reconciliation["legacy_unknown_point_count_by_metric"]
            ),
            **incident_summary,
        }
        # 避免重复嵌套一份相同 blocker；顶层使用统一逐指标结构。
        summary.pop("metric_blockers", None)
        summary["metric_blockers"] = normalized_blockers
        summary.pop("metric_limitations", None)
        summary["metric_limitations"] = normalized_limitations
        source_payload = {
            "database": {
                "release_id": context["release_id"],
                "state_sha256": context["state_sha256"],
                "manifest_sha256": context["manifest_sha256"],
                "database_manifest_sha256": context["database_manifest_sha256"],
                "inventory_sha256": context["inventory_sha256"],
                "host": LOOPBACK_HOST,
                "port": context["port"],
                **database_security,
            },
            "d2_normalization": {
                "fingerprint_sha256": d2["fingerprint_sha256"],
                "manifest_sha256": d2["manifest_sha256"],
                "incidents_sha256": d2["incidents_sha256"],
                "checksums_sha256": d2["checksums_sha256"],
                "file_names": d2["file_names"],
            },
            "d3_artifacts": {
                "fingerprint_sha256": d3["fingerprint_sha256"],
                "manifest_sha256": d3["manifest_sha256"],
                "summary_sha256": d3["summary_sha256"],
                "checksums_sha256": d3["checksums_sha256"],
                "file_names": d3["file_names"],
                "collector_ids": d3["collectors"],
                "update_artifact_count": d3["update_artifact_count"],
                "invalid_update_artifact_count": d3[
                    "invalid_update_artifact_count"
                ],
                "invalid_in_window_count": d3["invalid_in_window_count"],
                "invalid_in_window_reason_counts": d3[
                    "invalid_in_window_reason_counts"
                ],
            },
            "contracts": {
                str(METRIC_SCHEMA_RELATIVE_PATH): schema_sha,
                "metric_pipeline_hashes": metric_hashes,
                "metric_candidate_runner_sha256": _sha256(CANDIDATE_PATH),
            },
        }
        fingerprint_payload = {
            "schema_version": SCHEMA_VERSION,
            "data_profile": _profile_raw(profile),
            "metric_window_utc": {
                "start": _utc_text(metric_start),
                "end_exclusive": _utc_text(metric_end),
            },
            "generated_at": generated_at_text,
            "sources": source_payload,
            "files": {
                METRIC_FILE: metric_file_inventory,
                RECONCILIATION_FILE: reconciliation_file_inventory,
            },
            "summary": summary,
            "sample": sample,
            "classification": "observation_only",
            "causal_conclusion": None,
        }
        fingerprint = _canonical_sha256(fingerprint_payload)
        portable_provenance = {
            key: provenance[key]
            for key in (
                "git_sha",
                "git_dirty",
                "git_status_sha256",
                "probe_sha256",
                "data_profile_sha256",
                "data_profile_loader_sha256",
            )
            if key in provenance
        }
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "candidate_kind": "readonly_global_metric_series",
            "candidate_fingerprint_sha256": fingerprint,
            "data_profile": _profile_raw(profile),
            "metric_window_utc": fingerprint_payload["metric_window_utc"],
            "generated_at": generated_at_text,
            "source_slot_policies": {
                "feature_metrics": (
                    "verified_d3_valid_update_artifact_slots_with_invalid_quarantine"
                ),
                "incident_metrics": "complete_d2_normalized_fact_partitions",
                "missing_values_coerced_to_zero": False,
                "concurrency_interval": "[start,end)",
                "concurrency_grid_seconds": CONCURRENCY_SECONDS,
                "concurrency_grid_anchor": "unix_epoch_utc",
                "legacy_unknown_policy": (
                    "only_slots_affected_by_outage_intervals_without_reliable_bounds_or_identity"
                ),
            },
            "sources": source_payload,
            "provenance": portable_provenance,
            "files": fingerprint_payload["files"],
            "summary": summary,
            "sample": sample,
            "admission": {
                "status": "metric_candidate_ready" if eligible else "not_eligible",
                "eligible_for_release_gate": eligible,
                "blocking_reasons": global_blocking_reasons,
                "traceability_grade": "legacy_compatible",
            },
            "classification": "observation_only",
            "causal_conclusion": None,
        }
        _write_json(staging / MANIFEST_FILE, manifest)
        with (staging / SUMMARY_FILE).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(_summary_markdown(manifest))
            stream.flush()
            os.fsync(stream.fileno())
        _write_checksums(
            staging,
            (METRIC_FILE, RECONCILIATION_FILE, MANIFEST_FILE, SUMMARY_FILE),
        )
        for path in staging.iterdir():
            path.chmod(0o440)
        if target.exists() or target.is_symlink():
            raise MetricCandidateError("发布候选时输出目录已出现，拒绝覆盖")
        staging.rename(target)
        completed = True
        return manifest
    finally:
        try:
            connection.rollback()
        except Exception:
            pass
        if index is not None:
            try:
                index.close()
            except Exception:
                pass
        if not completed:
            _cleanup_staging(staging)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="生成固定二三月 P0 D2 MetricSeries 只读候选")
    parser.add_argument("--database-env", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--pipeline-root", type=Path, required=True)
    parser.add_argument("--d2-candidate-dir", type=Path, required=True)
    parser.add_argument("--d3-manifest", type=Path, required=True)
    parser.add_argument("--d3-summary", type=Path, required=True)
    parser.add_argument("--d3-sha256sums", type=Path, required=True)
    parser.add_argument(
        "--ajv-module",
        type=Path,
        required=True,
        help="AJV 2020 模块路径；服务器固定使用 frontend/node_modules/@redocly/ajv/dist/2020",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--generated-at",
        required=True,
        help="本次候选生成时刻（UTC RFC3339）；重跑确定性验证须复用同一值",
    )
    parser.add_argument("--sample-window-start")
    parser.add_argument("--sample-window-end-exclusive")
    parser.add_argument("--statement-timeout-ms", type=int, default=3_600_000)
    parser.add_argument("--connect-timeout", type=int, default=5)
    arguments = parser.parse_args(argv)
    connection = None
    try:
        try:
            project_root = arguments.project_root.resolve(strict=True)
        except OSError as error:
            raise MetricCandidateError("无法解析 project-root") from error
        profile = _load_project_data_profile(project_root)
        context = _validate_release_context(
            profile=profile,
            state_path=arguments.state,
            release_dir=arguments.release_dir,
        )
        database_config = _read_database_env(arguments.database_env)
        provenance = _git_provenance(
            project_root,
            probe_path=CANDIDATE_PATH,
            data_profile_path=project_root / "config/data-profile.json",
        )
        connection = _connect_database(database_config, context, arguments.connect_timeout)
        generate_metric_candidate(
            connection,
            profile=profile,
            context=context,
            database_config=database_config,
            provenance=provenance,
            project_root=project_root,
            pipeline_root=arguments.pipeline_root,
            d2_candidate_dir=arguments.d2_candidate_dir,
            d3_manifest_path=arguments.d3_manifest,
            d3_summary_path=arguments.d3_summary,
            d3_checksum_path=arguments.d3_sha256sums,
            output_dir=arguments.output_dir,
            generated_at=arguments.generated_at,
            ajv_module=arguments.ajv_module,
            sample_window_start=arguments.sample_window_start,
            sample_window_end_exclusive=arguments.sample_window_end_exclusive,
            statement_timeout_ms=arguments.statement_timeout_ms,
        )
    except ProbeError as error:
        parser.error(str(error))
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
