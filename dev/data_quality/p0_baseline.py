#!/usr/bin/env python3
"""把固定只读数据库探针与原始制品审计合并为确定性的 P0 数据基线。

本工具不连接数据库。当前二三月 37 张表的唯一事实来源是
``p0-quality-probe.json``；基础发布中的 ``database-inventory.json`` 只用于
校验来源发布链，绝不复用其中的行数或时间边界。
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).absolute()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dev.data_profile import DataProfileError, load_data_profile  # noqa: E402


DEFAULT_PROFILE = ROOT / "config" / "data-profile.json"
DEFAULT_COVERAGE = ROOT / "docs" / "data" / "P0六类事件字段覆盖矩阵.csv"
DEFAULT_RAW_INVENTORY = ROOT / "docs" / "data" / "P0原始制品审计.json"

PROFILE_RAW_KEYS = (
    "schema_version",
    "id",
    "mode",
    "timezone",
    "window_start",
    "window_end_exclusive",
    "snapshot_time",
    "api_profile",
)
MONTHLY_FAMILIES = (
    "event_table",
    "hijack",
    "sub_hijack",
    "leak_event",
    "prefix_outage",
    "as_outage",
    "country_outage",
    "feature_other",
    "feature_us",
    "feature_br",
    "feature_cn",
    "feature_ru",
    "feature_in",
    "feature_gb",
    "feature_id",
    "feature_de",
    "feature_au",
    "feature_pl",
)
AS_ACTIVITY_SERIES = (
    "feature_other",
    "feature_us",
    "feature_br",
    "feature_cn",
    "feature_ru",
    "feature_in",
    "feature_gb",
    "feature_id",
    "feature_de",
    "feature_au",
    "feature_pl",
)
EVENT_TYPES = (
    "hijack",
    "sub_hijack",
    "leak",
    "prefix_outage",
    "as_outage",
    "country_outage",
)
REQUIRED_COVERAGE_COLUMNS = {
    "event_type",
    "fact_table_family",
    "problem_field",
    "event_id_field",
    "start_time_field",
    "end_time_field",
    "duration_field",
    "affected_object_fields",
    "before_field",
    "during_field",
    "after_field",
    "vp_identity_status",
    "raw_reference_status",
    "lineage_level",
    "notes",
}
REQUIRED_PROBE_CHECKS = {
    "security.readonly",
    "tables.required",
    "tables.window",
    "tables.primary_key_and_duplicates",
    "schema.legacy_inventory_link",
    "timeseries.coverage_measured",
    "timeseries.unclassified_missing",
    "references.locator_format",
    "references.forward",
    "references.ambiguity",
    "references.reverse",
    "references.time_partition",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
RELEASE_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z(?:-[a-z0-9][a-z0-9._-]{0,47})?$")
EXPECTED_READER = "domeye_core_reader"
EXPECTED_DATABASE = "bgp_project"
LOOPBACK_HOST = "127.0.0.1"
GRANULARITY_SECONDS = 300


class BaselineError(RuntimeError):
    """输入制品或 P0 语义不符合约定。"""


def _load_json(path: Path, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError("JSON 不允许非有限数值：{}".format(value))

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise BaselineError("无法读取{}：{}".format(label, path)) from error
    if not isinstance(value, dict):
        raise BaselineError("{}顶层必须是对象：{}".format(label, path))
    return value


def _sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError as error:
        raise BaselineError("无法计算文件 SHA256：{}".format(path)) from error


def _canonical_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise BaselineError("质量探针包含不可规范化的数据") from error
    return hashlib.sha256(encoded).hexdigest()


def _profile_months(start: datetime, end_exclusive: datetime) -> list[str]:
    months = []
    cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cursor < end_exclusive:
        months.append(cursor.strftime("%Y%m"))
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)
    return months


def _expected_tables(months: list[str]) -> list[str]:
    return ["feature_country"] + [
        "{}_{}".format(family, month)
        for month in months
        for family in MONTHLY_FAMILIES
    ]


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise BaselineError("{}不是有效 SHA256".format(label))
    return value


def _require_int(value: Any, label: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise BaselineError("{}必须是大于等于 {} 的整数".format(label, minimum))
    return value


def _load_profile(project_root: Path) -> dict[str, Any]:
    profile_path = project_root / "config" / "data-profile.json"
    try:
        profile = load_data_profile(profile_path)
    except DataProfileError as error:
        raise BaselineError(str(error)) from error
    raw = {key: profile[key] for key in PROFILE_RAW_KEYS}
    expected_slots = int(
        (profile["parsed"]["end_exclusive"] - profile["parsed"]["start"]).total_seconds()
        // GRANULARITY_SECONDS
    )
    if expected_slots != 16992:
        raise BaselineError("P0 固定档必须恰好包含 16992 个五分钟槽")
    return {
        "loaded": profile,
        "raw": raw,
        "expected_slots": expected_slots,
        "path": profile_path,
    }


def _validate_release_context(
    *, profile: dict[str, Any], state_path: Path, release_dir: Path
) -> dict[str, Any]:
    if state_path.is_symlink() or not state_path.is_file():
        raise BaselineError("state 必须是非软链接普通文件：{}".format(state_path))
    if release_dir.is_symlink() or not release_dir.is_dir():
        raise BaselineError("来源发布目录必须存在且禁止软链接：{}".format(release_dir))
    state = _load_json(state_path, "开发数据库状态")
    manifest_path = release_dir / "manifest.json"
    database_manifest_path = release_dir / "database-manifest.json"
    inventory_path = release_dir / "database-inventory.json"
    manifest = _load_json(manifest_path, "发布总清单")
    database_manifest = _load_json(database_manifest_path, "数据库组件清单")
    inventory = _load_json(inventory_path, "基础发布 database-inventory")

    release_id = state.get("release_id")
    if not isinstance(release_id, str) or RELEASE_ID_RE.fullmatch(release_id) is None:
        raise BaselineError("state release_id 无效")
    if manifest.get("release_id") != release_id or database_manifest.get("release_id") != release_id:
        raise BaselineError("state、manifest 与 database-manifest 的 release_id 不一致")
    try:
        configured_release = Path(str(state["release_dir"])).resolve(strict=True)
        supplied_release = release_dir.resolve(strict=True)
    except (KeyError, OSError) as error:
        raise BaselineError("无法解析 state 中的 release_dir") from error
    if configured_release != supplied_release:
        raise BaselineError("state release_dir 与输入发布目录不一致")

    hashes = state.get("hashes")
    if not isinstance(hashes, dict):
        raise BaselineError("state 缺少 hashes")
    actual_hashes = {
        "release_manifest": _sha256(manifest_path),
        "database_manifest": _sha256(database_manifest_path),
        "inventory": _sha256(inventory_path),
    }
    for name, actual in actual_hashes.items():
        if _require_sha(hashes.get(name), "state {}".format(name)) != actual:
            raise BaselineError("state {} SHA256 与来源文件不一致".format(name))

    inventory_ref = database_manifest.get("inventory")
    if not isinstance(inventory_ref, dict):
        raise BaselineError("database-manifest 缺少 inventory 引用")
    if (
        inventory_ref.get("name") != "database-inventory.json"
        or inventory_ref.get("sha256") != actual_hashes["inventory"]
    ):
        raise BaselineError("database-manifest 与 database-inventory SHA256 不一致")
    if manifest.get("database") != database_manifest:
        raise BaselineError("manifest 内嵌 database 组件与 database-manifest 不一致")
    if not isinstance(inventory.get("schema_version"), int):
        raise BaselineError("基础发布 database-inventory 缺少 schema_version")

    loaded = profile["loaded"]
    if (
        state.get("data_start") != loaded["local"]["start"]
        or state.get("data_end_exclusive") != loaded["local"]["end_exclusive"]
    ):
        raise BaselineError("state 数据窗口与 config/data-profile.json 不一致")
    if state.get("phase") != "verified":
        raise BaselineError("开发数据库状态不是 verified")
    port = _require_int(state.get("port"), "state port", 1)
    if port > 65535:
        raise BaselineError("state port 超出有效范围")
    system_identifier = state.get("system_identifier")
    if not isinstance(system_identifier, str) or not system_identifier.isdigit():
        raise BaselineError("state system_identifier 无效")

    return {
        "release_id": release_id,
        "release_dir": str(supplied_release),
        "port": port,
        "system_identifier": system_identifier,
        "state_sha256": _sha256(state_path),
        "manifest_sha256": actual_hashes["release_manifest"],
        "database_manifest_sha256": actual_hashes["database_manifest"],
        "inventory_sha256": actual_hashes["inventory"],
        "inventory_schema_version": inventory["schema_version"],
        "paths": {
            "state": state_path,
            "manifest": manifest_path,
            "database_manifest": database_manifest_path,
            "inventory": inventory_path,
        },
    }


def _coverage_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if set(reader.fieldnames or []) != REQUIRED_COVERAGE_COLUMNS:
                raise BaselineError("六类事件覆盖矩阵列集合不符合约定")
            rows = [{key: (value or "").strip() for key, value in row.items()} for row in reader]
    except OSError as error:
        raise BaselineError("无法读取六类事件覆盖矩阵：{}".format(path)) from error
    by_type: dict[str, dict[str, str]] = {}
    for row in rows:
        event_type = row["event_type"]
        if event_type in by_type:
            raise BaselineError("六类事件覆盖矩阵事件类型重复：{}".format(event_type))
        by_type[event_type] = row
    if len(rows) != len(EVENT_TYPES) or set(by_type) != set(EVENT_TYPES):
        raise BaselineError("六类事件覆盖矩阵必须且只能包含六种事件")
    return [by_type[event_type] for event_type in EVENT_TYPES]


def _validate_probe(
    *,
    probe_path: Path,
    profile: dict[str, Any],
    context: dict[str, Any],
    expected_tables: list[str],
) -> dict[str, Any]:
    probe = _load_json(probe_path, "P0 质量探针")
    if probe.get("schema_version") != 1 or probe.get("probe_kind") != "p0_quality_probe":
        raise BaselineError("质量探针 schema_version 或 probe_kind 不符合约定")
    if probe.get("data_profile") != profile["raw"]:
        raise BaselineError("质量探针 data_profile 与 config/data-profile.json 不完全一致")

    source = probe.get("source")
    if not isinstance(source, dict):
        raise BaselineError("质量探针缺少 source")
    source_expectations = {
        "release_id": context["release_id"],
        "state_sha256": context["state_sha256"],
        "manifest_sha256": context["manifest_sha256"],
        "database_manifest_sha256": context["database_manifest_sha256"],
        "inventory_sha256": context["inventory_sha256"],
    }
    for key, expected in source_expectations.items():
        if source.get(key) != expected:
            raise BaselineError("质量探针 source.{} 与 state/release 上下文不一致".format(key))
    database = source.get("database")
    if not isinstance(database, dict):
        raise BaselineError("质量探针缺少 source.database")
    if (
        database.get("host") != LOOPBACK_HOST
        or database.get("port") != context["port"]
        or database.get("name") != EXPECTED_DATABASE
        or database.get("system_identifier") != context["system_identifier"]
    ):
        raise BaselineError("质量探针数据库身份与固定只读实例不一致")
    if source.get("current_user") != EXPECTED_READER:
        raise BaselineError("质量探针不是由约定只读用户生成")
    if (
        source.get("transaction_read_only") is not True
        or source.get("default_transaction_read_only") is not True
        or source.get("transaction_isolation") != "repeatable read"
    ):
        raise BaselineError("质量探针未在 repeatable read 只读事务中生成")

    project_root = profile["path"].parents[1].resolve()
    try:
        source_project_root = Path(str(source["project_root"])).resolve(strict=True)
        source_probe_path = Path(str(source["probe_path"]))
        source_profile_path = Path(str(source["data_profile_path"])).resolve(strict=True)
    except (KeyError, OSError) as error:
        raise BaselineError("质量探针缺少可解析的执行来源路径") from error
    if source_project_root != project_root:
        raise BaselineError("质量探针 project_root 与本次项目根目录不一致")
    if source_probe_path.is_symlink() or not source_probe_path.is_file():
        raise BaselineError("质量探针执行程序必须是仍可复核的非软链接普通文件")
    if source_profile_path != profile["path"].resolve():
        raise BaselineError("质量探针 data_profile_path 与唯一数据档不一致")
    probe_sha256 = _require_sha(source.get("probe_sha256"), "质量探针 probe_sha256")
    profile_sha256 = _require_sha(
        source.get("data_profile_sha256"), "质量探针 data_profile_sha256"
    )
    loader_sha256 = _require_sha(
        source.get("data_profile_loader_sha256"),
        "质量探针 data_profile_loader_sha256",
    )
    if probe_sha256 != _sha256(source_probe_path):
        raise BaselineError("质量探针执行程序 SHA256 与保留文件不一致")
    if profile_sha256 != _sha256(profile["path"]):
        raise BaselineError("质量探针数据档 SHA256 与唯一数据档不一致")
    if loader_sha256 != _sha256(project_root / "dev" / "data_profile.py"):
        raise BaselineError("质量探针数据档加载器 SHA256 与项目文件不一致")
    git_sha = source.get("git_sha")
    if not isinstance(git_sha, str) or GIT_SHA_RE.fullmatch(git_sha) is None:
        raise BaselineError("质量探针 git_sha 无效")
    if git_sha != _git_sha(project_root):
        raise BaselineError("质量探针 git_sha 与项目 HEAD 不一致")
    if not isinstance(source.get("git_dirty"), bool):
        raise BaselineError("质量探针 git_dirty 必须是布尔值")
    _require_sha(source.get("git_status_sha256"), "质量探针 git_status_sha256")

    tables = probe.get("tables")
    if not isinstance(tables, list):
        raise BaselineError("质量探针缺少 tables 数组")
    by_name: dict[str, dict[str, Any]] = {}
    for table in tables:
        if not isinstance(table, dict) or not isinstance(table.get("name"), str):
            raise BaselineError("质量探针包含非法表记录")
        name = table["name"]
        if name in by_name:
            raise BaselineError("质量探针表名重复：{}".format(name))
        _require_int(table.get("row_count"), "表 {} row_count".format(name))
        by_name[name] = table
    if len(expected_tables) != 37 or set(by_name) != set(expected_tables):
        missing = sorted(set(expected_tables) - set(by_name))
        extra = sorted(set(by_name) - set(expected_tables))
        raise BaselineError(
            "质量探针必须且只能包含当前 37 张表；缺少={}，多出={}".format(missing, extra)
        )

    checks = probe.get("checks")
    if not isinstance(checks, list):
        raise BaselineError("质量探针缺少 checks 数组")
    checks_by_id: dict[str, dict[str, Any]] = {}
    for item in checks:
        if not isinstance(item, dict) or not isinstance(item.get("check_id"), str):
            raise BaselineError("质量探针包含非法检查记录")
        check_id = item["check_id"]
        if check_id in checks_by_id:
            raise BaselineError("质量探针 check_id 重复：{}".format(check_id))
        if item.get("status") not in {"pass", "fail", "pending"}:
            raise BaselineError("质量探针检查状态非法：{}".format(check_id))
        checks_by_id[check_id] = item
    if set(checks_by_id) != REQUIRED_PROBE_CHECKS:
        raise BaselineError(
            "质量探针检查集合不符合约定；缺少={}，多出={}".format(
                sorted(REQUIRED_PROBE_CHECKS - set(checks_by_id)),
                sorted(set(checks_by_id) - REQUIRED_PROBE_CHECKS),
            )
        )
    failed_count = sum(item["status"] == "fail" for item in checks)
    if _require_int(probe.get("blocking_failure_count"), "blocking_failure_count") != failed_count:
        raise BaselineError("质量探针 blocking_failure_count 与 fail 检查数不一致")
    pending_count = sum(item["status"] == "pending" for item in checks)
    if _require_int(probe.get("pending_check_count"), "pending_check_count") != pending_count:
        raise BaselineError("质量探针 pending_check_count 与 pending 检查数不一致")
    quality_gate = probe.get("quality_gate")
    expected_failed = [item["check_id"] for item in checks if item["status"] == "fail"]
    expected_pending = [item["check_id"] for item in checks if item["status"] == "pending"]
    expected_gate = {
        "status": "fail" if expected_failed else "pending" if expected_pending else "pass",
        "blocking_failure_count": len(expected_failed),
        "blocking_failures": expected_failed,
        "pending_check_count": len(expected_pending),
        "pending_checks": expected_pending,
    }
    if quality_gate != expected_gate:
        raise BaselineError("质量探针 quality_gate 与 checks 汇总不一致")
    if checks_by_id["security.readonly"]["status"] != "pass":
        raise BaselineError("质量探针 security.readonly 未通过")

    if not isinstance(probe.get("timeseries_coverage"), dict):
        raise BaselineError("质量探针缺少 timeseries_coverage")
    if not isinstance(probe.get("reference_integrity"), dict):
        raise BaselineError("质量探针缺少 reference_integrity")
    fingerprint = _require_sha(probe.get("result_fingerprint_sha256"), "质量探针指纹")
    expected_fingerprint = _canonical_sha256(
        {
            "schema_version": probe["schema_version"],
            "probe_kind": probe["probe_kind"],
            "data_profile": probe["data_profile"],
            "release_id": source["release_id"],
            "inventory_sha256": source["inventory_sha256"],
            "provenance": {
                key: source[key]
                for key in (
                    "git_sha",
                    "git_dirty",
                    "git_status_sha256",
                    "probe_sha256",
                    "data_profile_sha256",
                    "data_profile_loader_sha256",
                )
            },
            "tables": probe["tables"],
            "timeseries_coverage": probe["timeseries_coverage"],
            "reference_integrity": probe["reference_integrity"],
            "checks": probe["checks"],
        }
    )
    if fingerprint != expected_fingerprint:
        raise BaselineError("质量探针结果指纹 result_fingerprint_sha256 校验失败")

    return {
        "raw": probe,
        "tables": [by_name[name] for name in expected_tables],
        "checks": checks_by_id,
        "failed_check_ids": sorted(
            check_id for check_id, item in checks_by_id.items() if item["status"] == "fail"
        ),
        "pending_check_ids": sorted(
            check_id for check_id, item in checks_by_id.items() if item["status"] == "pending"
        ),
    }


def _validate_raw_inventory(
    *, raw_path: Path, profile: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    raw = _load_json(raw_path, "P0 原始制品审计")
    if raw.get("schema_version") != 1 or raw.get("audit_kind") != "p0_raw_artifact_inventory":
        raise BaselineError("原始制品审计 schema_version 或 audit_kind 不符合约定")
    raw_profile = raw.get("data_profile")
    if not isinstance(raw_profile, dict):
        raise BaselineError("原始制品审计缺少 data_profile")
    expected_raw_profile = {
        "id": profile["raw"]["id"],
        "profile_sha256": _sha256(profile["path"]),
        "timezone": profile["raw"]["timezone"],
        "window_start": profile["raw"]["window_start"],
        "window_end_exclusive": profile["raw"]["window_end_exclusive"],
        "snapshot_time": profile["raw"]["snapshot_time"],
    }
    if raw_profile != expected_raw_profile:
        raise BaselineError("原始制品审计 data_profile 与 config/data-profile.json 不一致")
    if raw.get("status") not in {"unavailable", "partial", "complete"}:
        raise BaselineError("原始制品审计 status 非法")
    conflict = raw.get("source_release_conflict")
    if not isinstance(conflict, dict):
        raise BaselineError("原始制品审计缺少 source_release_conflict 来源引用")
    if (
        conflict.get("release_id") != context["release_id"]
        or conflict.get("database_manifest_sha256") != context["database_manifest_sha256"]
    ):
        raise BaselineError("原始制品审计的来源发布引用与 state/release 不一致")
    if not isinstance(raw.get("coverage"), dict):
        raise BaselineError("原始制品审计缺少 coverage")
    return raw


def _parse_local_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise BaselineError("{}必须是本地时间字符串".format(label))
    try:
        parsed = datetime.fromisoformat(value.replace(" ", "T", 1))
    except ValueError as error:
        raise BaselineError("{}时间格式非法：{}".format(label, value)) from error
    if parsed.tzinfo is not None:
        raise BaselineError("{}必须是数据库本地无时区时间".format(label))
    return parsed


def _validate_timeseries_structure(
    timeseries: dict[str, Any], expected_slots: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if timeseries.get("granularity_seconds") != GRANULARITY_SECONDS:
        raise BaselineError("质量探针时序粒度不是五分钟")
    source_series = timeseries.get("source_series")
    if not isinstance(source_series, list) or len(source_series) != 1:
        raise BaselineError("质量探针必须且只能包含一个 source_series")
    source = source_series[0]
    if not isinstance(source, dict) or source.get("series_id") != "feature_country.collect":
        raise BaselineError("质量探针 source_series 必须是 feature_country.collect")
    if source.get("table_names") != ["feature_country"] or source.get("subject_filter") != {"country": "collect"}:
        raise BaselineError("feature_country.collect 的表或 subject_filter 不符合约定")
    for field in (
        "expected_sample_count",
        "raw_observed_timestamp_count",
        "observed_sample_count",
        "missing_sample_count",
        "off_grid_sample_count",
    ):
        _require_int(source.get(field), "feature_country.collect {}".format(field))
    if source.get("expected_sample_count") != expected_slots:
        raise BaselineError("feature_country.collect 的 expected_sample_count 不是 {}".format(expected_slots))
    if source.get("granularity_seconds") != GRANULARITY_SECONDS:
        raise BaselineError("feature_country.collect 的粒度不是五分钟")
    if source["observed_sample_count"] + source["missing_sample_count"] != expected_slots:
        raise BaselineError("feature_country.collect 的 observed + missing 不等于 expected")

    activity_series = timeseries.get("activity_series")
    if not isinstance(activity_series, list):
        raise BaselineError("质量探针 timeseries_coverage 缺少 activity_series")
    by_id: dict[str, dict[str, Any]] = {}
    for item in activity_series:
        if not isinstance(item, dict) or not isinstance(item.get("series_id"), str):
            raise BaselineError("质量探针包含非法 AS 活动时序记录")
        series_id = item["series_id"]
        if series_id in by_id:
            raise BaselineError("质量探针活动时序 series_id 重复：{}".format(series_id))
        if series_id not in AS_ACTIVITY_SERIES:
            raise BaselineError("质量探针包含未知 AS 活动时序：{}".format(series_id))
        _require_int(item.get("activity_timestamp_count"), "{} activity_timestamp_count".format(series_id))
        if item.get("series_semantics") != "subject_activity_sparse":
            raise BaselineError("{} 未声明 subject_activity_sparse 语义".format(series_id))
        expected_names = ["{}_202602".format(series_id), "{}_202603".format(series_id)]
        if item.get("table_names") != expected_names:
            raise BaselineError("{} 的活动表集合不符合约定".format(series_id))
        for forbidden in ("expected_sample_count", "missing_sample_count", "coverage_ratio"):
            if forbidden in item:
                raise BaselineError("稀疏活动时序 {} 禁止包含 {}".format(series_id, forbidden))
        by_id[series_id] = item
    if set(by_id) != set(AS_ACTIVITY_SERIES):
        raise BaselineError("质量探针必须且只能包含 11 类 AS 稀疏活动时序")

    totals = timeseries.get("totals")
    if not isinstance(totals, dict):
        raise BaselineError("质量探针 timeseries_coverage 缺少 totals")
    recomputed = {
        "source_series_count": 1,
        "source_series_with_missing_samples": int(source["missing_sample_count"] > 0),
        "source_series_with_off_grid_samples": int(source["off_grid_sample_count"] > 0),
        "source_missing_sample_count": source["missing_sample_count"],
        "source_off_grid_sample_count": source["off_grid_sample_count"],
        "activity_series_count": len(activity_series),
        "activity_timestamp_count": sum(item["activity_timestamp_count"] for item in activity_series),
    }
    if totals != recomputed:
        raise BaselineError("质量探针时序 totals 与 source/activity series 汇总不一致")
    return source, [by_id[name] for name in AS_ACTIVITY_SERIES]


def _classify_source_grid(
    *, feature: dict[str, Any], raw: dict[str, Any], profile: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    loaded = profile["loaded"]
    start = loaded["parsed"]["start"].replace(tzinfo=None)
    end_exclusive = loaded["parsed"]["end_exclusive"].replace(tzinfo=None)
    expected_slots = profile["expected_slots"]
    source_end = datetime(2026, 2, 24, 8, 0, 0)
    processing_slots = [
        datetime(2026, 3, 31, 7, 30, 0) + timedelta(minutes=5 * index)
        for index in range(6)
    ]
    source_unavailable_slots = int((source_end - start).total_seconds() // GRANULARITY_SECONDS)
    expected_observed = expected_slots - source_unavailable_slots - len(processing_slots)
    coverage = raw["coverage"]
    missing_range = coverage.get("missing_local_range")
    raw_matches = isinstance(missing_range, dict) and all(
        (
            raw.get("status") == "partial",
            coverage.get("update_expected_slot_count") == expected_slots,
            coverage.get("update_available_slot_count") == expected_slots - source_unavailable_slots,
            missing_range.get("start") == profile["raw"]["window_start"],
            missing_range.get("end_exclusive") == "2026-02-24T08:00:00+08:00",
            missing_range.get("missing_update_slot_count") == source_unavailable_slots,
        )
    )
    first_observed = _parse_local_time(feature.get("first_observed_at"), "feature_country first_observed_at")
    last_observed = _parse_local_time(feature.get("last_observed_at"), "feature_country last_observed_at")
    input_ranges = feature.get("missing_ranges")
    normalized_ranges: list[dict[str, Any]] = []
    ranges_well_formed = isinstance(input_ranges, list)
    if ranges_well_formed:
        for index, item in enumerate(input_ranges):
            if not isinstance(item, dict) or set(item) != {"start", "end_exclusive", "sample_count"}:
                ranges_well_formed = False
                break
            try:
                range_start = _parse_local_time(item.get("start"), "missing_ranges[{}].start".format(index))
                range_end = _parse_local_time(item.get("end_exclusive"), "missing_ranges[{}].end_exclusive".format(index))
                sample_count = _require_int(item.get("sample_count"), "missing_ranges[{}].sample_count".format(index), 1)
            except BaselineError:
                ranges_well_formed = False
                break
            normalized_ranges.append(
                {"start": range_start, "end_exclusive": range_end, "sample_count": sample_count}
            )
    expected_ranges = [
        {"start": start, "end_exclusive": source_end, "sample_count": source_unavailable_slots},
        {"start": processing_slots[0], "end_exclusive": processing_slots[-1] + timedelta(minutes=5), "sample_count": len(processing_slots)},
    ]
    ranges_exact = ranges_well_formed and normalized_ranges == expected_ranges

    assertions = {
        "raw_inventory_exact": raw_matches,
        "expected_sample_count_exact": feature["expected_sample_count"] == expected_slots,
        "raw_observed_timestamp_count_exact": feature["raw_observed_timestamp_count"]
        == expected_observed,
        "observed_sample_count_exact": feature["observed_sample_count"] == expected_observed,
        "missing_sample_count_exact": feature["missing_sample_count"]
        == source_unavailable_slots + len(processing_slots),
        "off_grid_sample_count_zero": feature["off_grid_sample_count"] == 0,
        "first_observed_exact": first_observed == source_end,
        "last_observed_exact": last_observed == end_exclusive - timedelta(minutes=5),
        "complete_missing_ranges_exact": ranges_exact,
        "coverage_semantics_exact": (
            feature.get("coverage_status") == "observed_gap"
            and feature.get("missing_reason") == "legacy_unknown"
            and isinstance(feature.get("coverage_ratio"), (int, float))
            and not isinstance(feature.get("coverage_ratio"), bool)
            and abs(feature["coverage_ratio"] - expected_observed / expected_slots) <= 5e-13
        ),
    }
    passed = all(assertions.values())
    evidence = {
        "semantic_scope": "source_observation_grid",
        "series_id": "feature_country",
        "granularity_seconds": GRANULARITY_SECONDS,
        "expected_slot_count": expected_slots,
        "observed_slot_count": feature["observed_sample_count"],
        "missing_slot_count": feature["missing_sample_count"],
        "source_unavailable": {
            "start": profile["raw"]["window_start"],
            "end_exclusive": "2026-02-24T08:00:00+08:00",
            "slot_count": source_unavailable_slots,
            "value_semantics": "source_unavailable",
        },
        "processing_gap": {
            "slots": [
                item.replace(tzinfo=loaded["parsed"]["start"].tzinfo).isoformat()
                for item in processing_slots
            ],
            "slot_count": len(processing_slots),
            "value_semantics": "processing_gap",
        },
        "unclassified_slot_count": 0 if passed else None,
        "zero_filled": False,
        "assertions": assertions,
        "input_missing_ranges": input_ranges,
    }
    return "pass" if passed else "fail", evidence


def _activity_statistics(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "series_id": item["series_id"],
            "semantic_scope": "activity_timestamps_not_source_coverage",
            "activity_timestamp_count": item["activity_timestamp_count"],
            "first_activity_at": item.get("first_activity_at"),
            "last_activity_at": item.get("last_activity_at"),
        }
        for item in series
    ]


def _check(check_id: str, status: str, summary: str, evidence: Any) -> dict[str, Any]:
    if status not in {"pass", "fail", "pending", "not_applicable"}:
        raise BaselineError("非法检查状态：{}".format(status))
    return {"check_id": check_id, "status": status, "summary": summary, "evidence": evidence}


def _git_sha(project_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def build_baseline(
    *,
    project_root: Path = ROOT,
    state_path: Path,
    release_dir: Path,
    quality_probe_path: Path,
    coverage_path: Path = DEFAULT_COVERAGE,
    raw_inventory_path: Path = DEFAULT_RAW_INVENTORY,
    generated_at: str,
) -> dict[str, Any]:
    if project_root.is_symlink():
        raise BaselineError("项目根目录必须存在且禁止软链接")
    try:
        resolved_project_root = project_root.resolve(strict=True)
    except OSError as error:
        raise BaselineError("无法解析项目根目录：{}".format(project_root)) from error
    if not resolved_project_root.is_dir():
        raise BaselineError("项目根目录必须存在且禁止软链接")
    profile = _load_profile(resolved_project_root)
    context = _validate_release_context(
        profile=profile, state_path=state_path, release_dir=release_dir
    )
    months = _profile_months(
        profile["loaded"]["parsed"]["start"],
        profile["loaded"]["parsed"]["end_exclusive"],
    )
    expected_tables = _expected_tables(months)
    coverage = _coverage_rows(coverage_path)
    raw = _validate_raw_inventory(
        raw_path=raw_inventory_path, profile=profile, context=context
    )
    probe = _validate_probe(
        probe_path=quality_probe_path,
        profile=profile,
        context=context,
        expected_tables=expected_tables,
    )
    source_series, activity_series = _validate_timeseries_structure(
        probe["raw"]["timeseries_coverage"], profile["expected_slots"]
    )
    classification_status, source_grid = _classify_source_grid(
        feature=source_series, raw=raw, profile=profile
    )

    unresolved_probe_pending = [
        check_id
        for check_id in probe["pending_check_ids"]
        if check_id != "timeseries.unclassified_missing"
    ]
    quality_status = (
        "fail"
        if probe["failed_check_ids"]
        else "pending"
        if unresolved_probe_pending
        else "pass"
    )
    raw_traceable = (
        raw.get("status") == "complete"
        and raw.get("vp_identity_available") is True
        and raw.get("record_level_reference_available") is True
        and raw.get("full_file_manifest_available") is True
        and raw.get("full_file_checksum_available") is True
        and raw.get("processing_lineage_available") is True
    )
    checks = [
        _check("profile.canonical", "pass", "唯一 data-profile 与固定 16992 槽窗口有效。", profile["raw"]),
        _check(
            "release.provenance",
            "pass",
            "state、发布清单与基础 inventory 的来源哈希互相一致。",
            {key: context[key] for key in ("release_id", "state_sha256", "manifest_sha256", "database_manifest_sha256", "inventory_sha256")},
        ),
        _check("probe.integrity", "pass", "质量探针结构、指纹、发布来源和只读事务验证通过。", probe["raw"]["result_fingerprint_sha256"]),
        _check("tables.live_inventory", "pass", "当前 37 张表完全来自 live 质量探针。", {"expected_count": 37, "observed_count": len(probe["tables"])}),
        _check("events.coverage_contract", "pass", "六类事件字段覆盖矩阵结构完整。", {"event_types": list(EVENT_TYPES)}),
        _check(
            "probe.quality_gates",
            quality_status,
            "探针阻断检查已通过；AS 稀疏活动时间不作为 source coverage 门。" if quality_status == "pass" else "探针仍有失败或未解决检查。",
            {"failed": probe["failed_check_ids"], "unresolved_pending": unresolved_probe_pending},
        ),
        _check(
            "timeseries.source_observation_grid",
            classification_status,
            "feature_country 源观测网格缺口已分为 6720 个 source_unavailable 与 6 个 processing_gap，未补零。" if classification_status == "pass" else "feature_country 源观测网格仍存在无法精确归类的缺口。",
            source_grid,
        ),
        _check(
            "raw.traceability",
            "pass" if raw_traceable else "pending",
            "全窗口原始记录可逐条追溯。" if raw_traceable else "原始制品仅部分覆盖，全窗口 raw_traceable 尚不可达。",
            {"status": raw.get("status"), "vp_identity_available": raw.get("vp_identity_available"), "record_level_reference_available": raw.get("record_level_reference_available")},
        ),
    ]
    blocking = [item["check_id"] for item in checks if item["status"] == "fail"]
    pending = [item["check_id"] for item in checks if item["status"] == "pending"]
    legacy_ids = {
        "profile.canonical",
        "release.provenance",
        "probe.integrity",
        "tables.live_inventory",
        "events.coverage_contract",
        "probe.quality_gates",
        "timeseries.source_observation_grid",
    }
    legacy_compatible = all(
        item["status"] == "pass" for item in checks if item["check_id"] in legacy_ids
    )
    d0_audit_ids = {
        "profile.canonical",
        "release.provenance",
        "probe.integrity",
        "tables.live_inventory",
        "events.coverage_contract",
        "timeseries.source_observation_grid",
    }
    d0_audit_complete = all(
        item["status"] == "pass" for item in checks if item["check_id"] in d0_audit_ids
    )

    return {
        "schema_version": 1,
        "baseline_kind": "p0_data_baseline",
        "generated_at": generated_at,
        "project_git_sha": _git_sha(resolved_project_root),
        "project_root": str(resolved_project_root),
        "generator": {
            "path": str(BASELINE_PATH),
            "sha256": _sha256(BASELINE_PATH),
        },
        "source_release": {
            "release_id": context["release_id"],
            "release_dir": context["release_dir"],
            "state_sha256": context["state_sha256"],
            "manifest_sha256": context["manifest_sha256"],
            "database_manifest_sha256": context["database_manifest_sha256"],
            "base_inventory_sha256": context["inventory_sha256"],
        },
        "data_profile": profile["raw"],
        "source_artifacts": {
            "state": {"path": str(state_path), "sha256": context["state_sha256"]},
            "release_manifest": {"path": str(context["paths"]["manifest"]), "sha256": context["manifest_sha256"]},
            "database_manifest": {"path": str(context["paths"]["database_manifest"]), "sha256": context["database_manifest_sha256"]},
            "base_database_inventory": {
                "path": str(context["paths"]["inventory"]),
                "sha256": context["inventory_sha256"],
                "source_schema_version": context["inventory_schema_version"],
                "usage": "source_provenance_only",
            },
            "quality_probe": {"path": str(quality_probe_path), "sha256": _sha256(quality_probe_path)},
            "coverage_matrix": {"path": str(coverage_path), "sha256": _sha256(coverage_path)},
            "raw_artifact_inventory": {"path": str(raw_inventory_path), "sha256": _sha256(raw_inventory_path)},
        },
        "scope": {
            "months": months,
            "expected_table_count": 37,
            "observed_table_count": len(probe["tables"]),
            "live_table_row_count": sum(table["row_count"] for table in probe["tables"]),
        },
        "tables": probe["tables"],
        "event_coverage": coverage,
        "timeseries_semantics": {
            "source_observation_grid": source_grid,
            "as_activity_timestamp_statistics": _activity_statistics(activity_series),
            "as_activity_warning": "11 类 AS 月表是稀疏活动表，DISTINCT t 不表示 source coverage，不能据此把缺时点补零。",
        },
        "raw_evidence": raw,
        "checks": checks,
        "summary": {
            "baseline_ready": d0_audit_complete,
            "d0_audit_complete": d0_audit_complete,
            "legacy_compatible": legacy_compatible,
            "raw_traceable": raw_traceable,
            "blocking_failure_count": len(blocking),
            "blocking_failures": blocking,
            "pending_check_count": len(pending),
            "pending_checks": pending,
            "p0_data_status": (
                "raw_traceable"
                if legacy_compatible and raw_traceable and not pending
                else "legacy_compatible"
                if legacy_compatible
                else "not_accepted"
            ),
        },
    }


def _parse_generated_at(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise BaselineError("--generated-at 必须是带时区 ISO 8601 时间") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BaselineError("--generated-at 必须显式携带时区")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_output(payload: dict[str, Any], output: str, force: bool) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if output == "-":
        sys.stdout.write(text)
        return
    path = Path(output)
    if path.exists() and not force:
        raise BaselineError("输出文件已存在；如需覆盖请显式提供 --force：{}".format(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".{}.tmp".format(path.name))
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="合并 live 只读探针并生成 P0 数据基线")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--quality-probe", type=Path, required=True)
    parser.add_argument("--coverage-matrix", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--raw-inventory", type=Path, default=DEFAULT_RAW_INVENTORY)
    parser.add_argument("--generated-at")
    parser.add_argument("--output", required=True, help="输出路径，使用 - 输出到标准输出")
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        payload = build_baseline(
            project_root=arguments.project_root,
            state_path=arguments.state,
            release_dir=arguments.release_dir,
            quality_probe_path=arguments.quality_probe,
            coverage_path=arguments.coverage_matrix,
            raw_inventory_path=arguments.raw_inventory,
            generated_at=_parse_generated_at(arguments.generated_at),
        )
        _write_output(payload, arguments.output, arguments.force)
    except BaselineError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
