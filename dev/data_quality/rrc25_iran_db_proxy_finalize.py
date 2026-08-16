#!/usr/bin/env python3
"""用数据库聚合曲线与定向消息证据完成伊朗事件的只读最终化。

本入口只消费已经冻结并带哈希的文件制品：

* ``iran-db-first.json``；
* 定向 raw 的五文件结果包（其中 gzip 内容是 JSONL，不是 MRT）；
* 研究 profile、报告主张清单和旧 source-fact 快照。

它不会连接数据库，不会打开 MRT/RIB，不会 seed 或回放路由状态。数据库中的
``ipv4_address_equivalent`` 是旧 ``v4ip_num * 256`` 等价值，不是去重地址
并集；因此这里只生成独立的 metric-only proxy candidate，绝不生成
``country-outage-sample/v1``、``country-outage-episode/v1`` 或
``country-outage-wave/v1`` 合同。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import statistics
import sys
import tempfile
from typing import Any, Iterable, Mapping, MutableMapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.data_pipeline.research.rrc25_country_outage.profile import (  # noqa: E402
    validate_research_profile,
)
from backend.data_pipeline.research.rrc25_country_outage.reconciliation import (  # noqa: E402
    build_reconciliation_result,
    canonical_json,
)
from backend.data_pipeline.research.rrc25_country_outage.source_fact import (  # noqa: E402
    load_frozen_incident_fact,
)


UTC = timezone.utc
BEIJING = timezone(timedelta(hours=8))

DB_FIRST_SCHEMA_VERSION = "rrc25-iran-db-first/v2"
TARGETED_MANIFEST_SCHEMA_VERSION = "rrc25-iran-targeted-raw-manifest/v1"
TARGETED_STATS_SCHEMA_VERSION = "rrc25-iran-targeted-parser-stats/v1"
TARGETED_EVIDENCE_SCOPE = "message_observation_only"
PROXY_SCHEMA_VERSION = "rrc25-iran-db-metric-proxy-analysis/v1"
QUALITY_SCHEMA_VERSION = "rrc25-iran-db-proxy-quality/v1"
MANIFEST_SCHEMA_VERSION = "rrc25-iran-db-proxy-finalization-manifest/v1"
RECONCILIATION_DECISION_VERSION = (
    "rrc25-iran-db-proxy-reconciliation-decision/v2"
)
EXPECTED_INCIDENT_REF = "country_outage/2026-02-27 09:12:32/IR/1/r"
EXPECTED_STUDY_ID = "iran-rrc25-country-outage-202602-v1"
EXPECTED_TARGETED_FILES = frozenset(
    (
        "route-events.jsonl.gz",
        "raw-record-refs.jsonl.gz",
        "parser-stats.json",
        "MANIFEST.json",
        "SHA256SUMS",
    )
)
TARGETED_CHECKSUM_FILES = (
    "route-events.jsonl.gz",
    "raw-record-refs.jsonl.gz",
    "parser-stats.json",
    "MANIFEST.json",
)
OUTPUT_CONTENT_FILES = (
    "数据库指标代理分析报告.md",
    "proxy-analysis.json",
    "reconciliation-result.json",
    "QUALITY.json",
)
OUTPUT_CHECKSUM_FILES = OUTPUT_CONTENT_FILES + ("MANIFEST.json",)
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
MAX_JSON_BYTES = 256 * 1024 * 1024
MAX_TARGETED_JSONL_ROWS = 250_000
MAX_TARGETED_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ProxyFinalizeError(ValueError):
    """最终化输入、哈希、语义或输出边界不闭合。"""


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProxyFinalizeError(f"JSON 存在重复字段：{key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ProxyFinalizeError(f"JSON 禁止非有限数值：{value}")


def _json_loads_strict(payload: str, label: str) -> Any:
    try:
        return json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except ProxyFinalizeError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ProxyFinalizeError(f"{label} 不是严格 UTF-8 JSON") from error


def _canonical_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ProxyFinalizeError("对象无法规范化为确定性 JSON") from error
    return text.encode("utf-8")


def _pretty_json_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ProxyFinalizeError("输出无法序列化为确定性 JSON") from error
    return (text + "\n").encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _expected_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ProxyFinalizeError(f"{field} 必须是 64 位小写 SHA-256")
    return value


def _regular_file(path: str | Path, label: str) -> Path:
    candidate = Path(path).expanduser()
    try:
        metadata = candidate.lstat()
    except OSError as error:
        raise ProxyFinalizeError(f"无法读取{label}：{candidate}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ProxyFinalizeError(f"{label}必须是非符号链接普通文件：{candidate}")
    return candidate.resolve()


def _hash_file(path: Path, *, maximum_bytes: int = MAX_JSON_BYTES) -> tuple[str, int]:
    metadata = path.stat()
    if metadata.st_size > maximum_bytes:
        raise ProxyFinalizeError(f"文件超过读取上限：{path.name}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
    if size != metadata.st_size:
        raise ProxyFinalizeError(f"读取期间文件大小漂移：{path.name}")
    return digest.hexdigest(), size


def _load_hashed_json(
    path: str | Path,
    expected_sha256: str,
    label: str,
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    source = _regular_file(path, label)
    actual_sha, size = _hash_file(source)
    if actual_sha != _expected_sha256(expected_sha256, f"{label} expected_sha256"):
        raise ProxyFinalizeError(f"{label} SHA-256 不匹配")
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ProxyFinalizeError(f"{label}无法按 UTF-8 读取") from error
    value = _json_loads_strict(text, label)
    if not isinstance(value, Mapping):
        raise ProxyFinalizeError(f"{label}根节点必须是对象")
    return value, {
        "logical_name": label,
        "basename": source.name,
        "sha256": actual_sha,
        "size_bytes": size,
    }


def _parse_sha256sums(path: Path, allowed_names: Iterable[str]) -> dict[str, str]:
    allowed = frozenset(allowed_names)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ProxyFinalizeError(f"无法读取校验清单：{path}") from error
    result: dict[str, str] = {}
    for line in lines:
        if not line:
            raise ProxyFinalizeError("SHA256SUMS 不得包含空行")
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\x00]+)", line)
        if match is None:
            raise ProxyFinalizeError(f"SHA256SUMS 行格式非法：{line!r}")
        digest, name = match.groups()
        if name not in allowed or name in result:
            raise ProxyFinalizeError(f"SHA256SUMS 包含未知或重复文件：{name}")
        result[name] = digest
    return result


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ProxyFinalizeError(f"{field} 必须是 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ProxyFinalizeError(f"{field} 时间非法") from error
    if parsed.utcoffset() != timedelta(0) or parsed.microsecond:
        raise ProxyFinalizeError(f"{field} 必须是 UTC 秒级时间")
    return parsed.astimezone(UTC)


def _local_time(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ProxyFinalizeError(f"{field} 必须是带时区时间")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ProxyFinalizeError(f"{field} 时间非法") from error
    if parsed.utcoffset() != timedelta(hours=8) or parsed.microsecond:
        raise ProxyFinalizeError(f"{field} 必须是 +08:00 秒级时间")
    return parsed


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local_text(value: datetime) -> str:
    return value.astimezone(BEIJING).isoformat(timespec="seconds")


def _assert_output_disjoint_from_inputs(
    output_directory: str | Path,
    input_paths: Sequence[str | Path],
) -> None:
    output = Path(output_directory).expanduser().resolve(strict=False)
    for raw_input in input_paths:
        source = Path(raw_input).expanduser().resolve(strict=False)
        if output == source or output in source.parents or source in output.parents:
            raise ProxyFinalizeError(
                f"输出目录不得与输入路径嵌套或重合：{output} / {source}"
            )


def _stable_id(prefix: str, identity: Mapping[str, Any]) -> str:
    return prefix + _sha256_bytes(_canonical_bytes(identity))[:24]


def _verify_db_first(
    payload: Mapping[str, Any],
    ref: Mapping[str, Any],
    path: Path,
    profile: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], Mapping[str, Any]]:
    if payload.get("schema_version") != DB_FIRST_SCHEMA_VERSION:
        raise ProxyFinalizeError("DB-first schema_version 非法")
    fingerprint_payload = {
        field: payload.get(field) for field in DB_FIRST_FINGERPRINT_FIELDS
    }
    if set(payload) & set(DB_FIRST_FINGERPRINT_FIELDS) != set(
        DB_FIRST_FINGERPRINT_FIELDS
    ):
        raise ProxyFinalizeError("DB-first 语义指纹字段不完整")
    expected_fingerprint = _sha256_bytes(
        _canonical_bytes(
            {
                "schema": "rrc25_iran_db_first_content_fingerprint/v2",
                "stable_research_content": fingerprint_payload,
            }
        )
    )
    if payload.get("content_fingerprint_sha256") != expected_fingerprint:
        raise ProxyFinalizeError("DB-first 内容指纹不闭合")

    checksum_path = _regular_file(path.parent / "SHA256SUMS", "DB-first SHA256SUMS")
    sums = _parse_sha256sums(
        checksum_path,
        (
            path.name,
            "伊朗数据库先行复算摘要.md",
            "SHA256SUMS",
        ),
    )
    if sums.get(path.name) != ref["sha256"]:
        raise ProxyFinalizeError("DB-first SHA256SUMS 未绑定正式 JSON")

    scope = payload.get("scope")
    series = payload.get("country_series")
    security = payload.get("database_security")
    execution = payload.get("execution")
    if not all(
        isinstance(value, Mapping)
        for value in (scope, series, security, execution)
    ):
        raise ProxyFinalizeError("DB-first scope/series/security/execution 缺失")
    if (
        scope.get("incident_ref") != EXPECTED_INCIDENT_REF
        or scope.get("country_code") != "IR"
        or scope.get("raw_read_performed") is not False
        or scope.get("database_write_performed") is not False
        or security.get("transaction_isolation") != "repeatable read"
        or security.get("transaction_read_only") is not True
        or security.get("default_transaction_read_only") is not True
        or execution.get("transaction_finalization") != "rollback_completed"
        or execution.get("transaction_mode") != "repeatable_read_read_only"
    ):
        raise ProxyFinalizeError("DB-first 只读身份或事务门不闭合")

    window = scope.get("window")
    profile_window = profile["window"]
    if not isinstance(window, Mapping) or (
        window.get("start_utc") != profile_window["start_utc"]
        or window.get("end_exclusive_utc")
        != profile_window["end_exclusive_utc"]
        or window.get("granularity_seconds")
        != profile_window["granularity_seconds"]
        or window.get("semantics") != "half_open"
    ):
        raise ProxyFinalizeError("DB-first 与冻结 profile 窗口不一致")
    coverage = series.get("coverage")
    points_raw = series.get("points")
    metric_semantics = series.get("metric_semantics")
    if (
        not isinstance(coverage, Mapping)
        or not isinstance(points_raw, list)
        or not isinstance(metric_semantics, Mapping)
        or metric_semantics.get("ipv4_address_equivalent")
        != "旧算法 IPv4 /24 等价值乘 256，不是去重地址并集"
        or metric_semantics.get("unknown_is_zero") is not False
        or metric_semantics.get("cross_family_sum_allowed") is not False
    ):
        raise ProxyFinalizeError("DB-first 国家曲线缺失")
    expected_count = int(
        (
            _utc(profile_window["end_exclusive_utc"], "profile.window.end")
            - _utc(profile_window["start_utc"], "profile.window.start")
        ).total_seconds()
        // profile_window["granularity_seconds"]
    )
    if (
        coverage.get("status") != "complete"
        or coverage.get("expected_slot_count") != expected_count
        or coverage.get("observed_slot_count") != expected_count
        or coverage.get("missing_slot_count") != 0
        or coverage.get("off_grid_row_count") != 0
        or len(points_raw) != expected_count
    ):
        raise ProxyFinalizeError("DB-first 国家曲线不是完整连续窗口")

    points: list[dict[str, Any]] = []
    expected_time = _utc(profile_window["start_utc"], "profile.window.start")
    granularity = timedelta(seconds=profile_window["granularity_seconds"])
    for index, raw in enumerate(points_raw):
        if not isinstance(raw, Mapping):
            raise ProxyFinalizeError(f"DB-first point[{index}] 必须是对象")
        local = _local_time(
            raw.get("observed_at_local"), f"country_series.points[{index}]"
        )
        observed = local.astimezone(UTC)
        if observed != expected_time:
            raise ProxyFinalizeError(f"DB-first point[{index}] 不连续或未对齐")
        value = raw.get("ipv4_address_equivalent")
        ipv4_24 = raw.get("ipv4_24_equivalent")
        ipv6_48 = raw.get("ipv6_48_equivalent")
        announce = raw.get("announce_count")
        withdraw = raw.get("withdraw_count")
        if (
            raw.get("value_state") != "observed"
            or raw.get("missing_reason") is not None
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            or isinstance(ipv4_24, bool)
            or not isinstance(ipv4_24, int)
            or ipv4_24 <= 0
            or value != ipv4_24 * 256
            or isinstance(ipv6_48, bool)
            or not isinstance(ipv6_48, int)
            or ipv6_48 <= 0
            or isinstance(announce, bool)
            or not isinstance(announce, int)
            or announce < 0
            or isinstance(withdraw, bool)
            or not isinstance(withdraw, int)
            or withdraw < 0
        ):
            raise ProxyFinalizeError(
                f"DB-first point[{index}] 指标值或等价值公式非法"
            )
        points.append(
            {
                "index": index,
                "start": observed,
                "end": observed + granularity,
                "start_utc": _utc_text(observed),
                "start_local": _local_text(observed),
                "value": value,
            }
        )
        expected_time += granularity
    if expected_time != _utc(profile_window["end_exclusive_utc"], "profile.window.end"):
        raise ProxyFinalizeError("DB-first 国家曲线结束边界不闭合")
    return points, series


def _read_gzip_jsonl(path: Path, label: str) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    uncompressed_bytes = 0
    try:
        with gzip.open(path, "rb") as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                uncompressed_bytes += len(raw_line)
                if uncompressed_bytes > MAX_TARGETED_UNCOMPRESSED_BYTES:
                    raise ProxyFinalizeError(f"{label} 解压内容超过上限")
                if len(rows) >= MAX_TARGETED_JSONL_ROWS:
                    raise ProxyFinalizeError(f"{label} 记录数超过上限")
                if not raw_line.endswith(b"\n"):
                    raise ProxyFinalizeError(f"{label} 第 {line_number} 行缺少换行")
                try:
                    text = raw_line[:-1].decode("utf-8")
                except UnicodeError as error:
                    raise ProxyFinalizeError(f"{label} 不是 UTF-8 JSONL") from error
                row = _json_loads_strict(text, f"{label}[{line_number}]")
                if not isinstance(row, Mapping):
                    raise ProxyFinalizeError(f"{label} 每行必须是对象")
                rows.append(row)
    except (OSError, EOFError) as error:
        raise ProxyFinalizeError(f"{label} gzip 完整性失败") from error
    return rows


def _verify_targeted_package(
    directory: str | Path,
    expected_manifest_sha256: str,
    *,
    expected_db_first_sha256: str,
    expected_db_first_size: int,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(directory).expanduser()
    try:
        root_meta = root.lstat()
    except OSError as error:
        raise ProxyFinalizeError(f"无法读取 targeted-raw 目录：{root}") from error
    if stat.S_ISLNK(root_meta.st_mode) or not stat.S_ISDIR(root_meta.st_mode):
        raise ProxyFinalizeError("targeted-raw 必须是非符号链接目录")
    entries = {
        item.name
        for item in root.iterdir()
        if not item.name.startswith(".")
    }
    if entries != EXPECTED_TARGETED_FILES:
        raise ProxyFinalizeError(
            "targeted-raw 必须精确包含五文件包；"
            f"missing={sorted(EXPECTED_TARGETED_FILES - entries)}, "
            f"extra={sorted(entries - EXPECTED_TARGETED_FILES)}"
        )
    paths = {
        name: _regular_file(root / name, f"targeted-raw/{name}")
        for name in EXPECTED_TARGETED_FILES
    }
    refs: dict[str, dict[str, Any]] = {}
    for name, path in paths.items():
        digest, size = _hash_file(
            path,
            maximum_bytes=(
                1024 * 1024 * 1024
                if name.endswith(".jsonl.gz")
                else MAX_JSON_BYTES
            ),
        )
        refs[name] = {"name": name, "sha256": digest, "size_bytes": size}
    if refs["MANIFEST.json"]["sha256"] != _expected_sha256(
        expected_manifest_sha256, "targeted manifest expected_sha256"
    ):
        raise ProxyFinalizeError("targeted-raw MANIFEST.json 外部哈希不匹配")
    sums = _parse_sha256sums(paths["SHA256SUMS"], TARGETED_CHECKSUM_FILES)
    if set(sums) != set(TARGETED_CHECKSUM_FILES):
        raise ProxyFinalizeError("targeted-raw SHA256SUMS 未精确覆盖四个内容文件")
    for name in TARGETED_CHECKSUM_FILES:
        if sums[name] != refs[name]["sha256"]:
            raise ProxyFinalizeError(f"targeted-raw {name} 哈希不闭合")

    manifest = _json_loads_strict(
        paths["MANIFEST.json"].read_text(encoding="utf-8"),
        "targeted-raw MANIFEST",
    )
    stats = _json_loads_strict(
        paths["parser-stats.json"].read_text(encoding="utf-8"),
        "targeted-raw parser-stats",
    )
    if not isinstance(manifest, Mapping) or not isinstance(stats, Mapping):
        raise ProxyFinalizeError("targeted-raw manifest/stats 必须是对象")
    if (
        manifest.get("schema_version") != TARGETED_MANIFEST_SCHEMA_VERSION
        or manifest.get("package_schema_version")
        != "rrc25-iran-targeted-raw/v1"
        or manifest.get("evidence_scope") != TARGETED_EVIDENCE_SCOPE
        or manifest.get("acceptance_state")
        != "message_observations_only_not_state_replay"
        or manifest.get("database_connections") != 0
        or manifest.get("database_writes") != 0
        or manifest.get("rib_files_opened") != 0
        or manifest.get("state_replay_performed") is not False
        or manifest.get("causal_claim_allowed") is not False
        or stats.get("schema_version") != TARGETED_STATS_SCHEMA_VERSION
        or stats.get("evidence_scope") != TARGETED_EVIDENCE_SCOPE
        or stats.get("execution_state") != "completed"
    ):
        raise ProxyFinalizeError("targeted-raw 消息级证据边界非法")

    plan = manifest.get("plan")
    if not isinstance(plan, Mapping) or (
        plan.get("schema_version") != "rrc25-iran-targeted-raw-plan/v1"
        or plan.get("evidence_scope") != TARGETED_EVIDENCE_SCOPE
        or plan.get("execution_state") != "planned_not_executed"
    ):
        raise ProxyFinalizeError("targeted-raw 缺少受约束的执行 plan")
    plan_semantic = dict(plan)
    supplied_plan_id = plan_semantic.pop("plan_id", None)
    supplied_plan_fingerprint = plan_semantic.pop(
        "semantic_fingerprint_sha256", None
    )
    expected_plan_fingerprint = _sha256_bytes(_canonical_bytes(plan_semantic))
    if (
        supplied_plan_fingerprint != expected_plan_fingerprint
        or supplied_plan_id
        != "traw_v1_" + expected_plan_fingerprint[:32]
    ):
        raise ProxyFinalizeError("targeted-raw plan 内容寻址身份不闭合")
    execution_policy = plan.get("execution_policy")
    if not isinstance(execution_policy, Mapping) or (
        execution_policy.get("database_connections") != 0
        or execution_policy.get("database_writes") != 0
        or execution_policy.get("rib_files_opened") != 0
        or execution_policy.get("seed_performed") is not False
        or execution_policy.get("state_replay_performed") is not False
        or execution_policy.get("full_window_replay") is not False
        or execution_policy.get("update_artifact_read_passes_per_file") != 1
        or execution_policy.get("causal_claim_allowed") is not False
    ):
        raise ProxyFinalizeError("targeted-raw plan 执行边界非法")
    parser_contract = plan.get("native_parser_contract")
    if not isinstance(parser_contract, Mapping):
        raise ProxyFinalizeError("targeted-raw plan 缺少 native parser contract")
    contract_semantic = dict(parser_contract)
    supplied_contract_fingerprint = contract_semantic.pop(
        "semantic_fingerprint_sha256", None
    )
    if (
        parser_contract.get("schema_version")
        != "rrc25-full-window-parser-attestation/v1"
        or parser_contract.get("backend") != "native"
        or supplied_contract_fingerprint
        != _sha256_bytes(_canonical_bytes(contract_semantic))
    ):
        raise ProxyFinalizeError("targeted-raw native parser contract 不闭合")
    request_bindings = plan.get("request_bindings")
    db_binding = (
        request_bindings.get("db_first_json")
        if isinstance(request_bindings, Mapping)
        else None
    )
    if not isinstance(db_binding, Mapping) or (
        db_binding.get("sha256") != expected_db_first_sha256
        or db_binding.get("size_bytes") != expected_db_first_size
    ):
        raise ProxyFinalizeError("targeted-raw 未绑定同一 DB-first 输入")
    selection_window = plan.get("selection_window")
    profile_window = profile.get("window")
    if not isinstance(selection_window, Mapping) or not isinstance(
        profile_window, Mapping
    ) or (
        selection_window.get("start_utc") != profile_window.get("start_utc")
        or selection_window.get("end_exclusive_utc")
        != profile_window.get("end_exclusive_utc")
        or selection_window.get("granularity_seconds")
        != profile_window.get("granularity_seconds")
        or selection_window.get("interval_semantics") != "half_open"
    ):
        raise ProxyFinalizeError("targeted-raw plan 与冻结 profile 窗口不一致")
    selected_artifacts = plan.get("selected_artifacts")
    requested_slots = plan.get("requested_update_slots")
    limits = plan.get("limits")
    if (
        not isinstance(selected_artifacts, list)
        or not isinstance(requested_slots, list)
        or not isinstance(limits, Mapping)
        or not selected_artifacts
        or len(selected_artifacts) != len(requested_slots)
        or len(set(requested_slots)) != len(requested_slots)
    ):
        raise ProxyFinalizeError("targeted-raw plan 的 artifact/槽集合非法")
    artifact_ids: set[str] = set()
    artifact_by_id: dict[str, Mapping[str, Any]] = {}
    artifact_slots: list[str] = []
    selected_compressed_bytes = 0
    for index, artifact in enumerate(selected_artifacts):
        if not isinstance(artifact, Mapping):
            raise ProxyFinalizeError(
                f"targeted-raw selected_artifacts[{index}] 必须是对象"
            )
        artifact_id = artifact.get("artifact_id")
        slot = artifact.get("artifact_time_utc")
        size = artifact.get("size_bytes")
        if (
            not isinstance(artifact_id, str)
            or not artifact_id
            or artifact_id in artifact_ids
            or not isinstance(slot, str)
            or artifact.get("artifact_type") != "update"
            or artifact.get("collector_id") != "rrc25"
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
        ):
            raise ProxyFinalizeError(
                f"targeted-raw selected_artifacts[{index}] 身份非法"
            )
        _utc(slot, f"targeted selected_artifacts[{index}].artifact_time_utc")
        _expected_sha256(
            artifact.get("file_sha256"),
            f"targeted selected_artifacts[{index}].file_sha256",
        )
        artifact_ids.add(artifact_id)
        artifact_by_id[artifact_id] = artifact
        artifact_slots.append(slot)
        selected_compressed_bytes += size
    selected_slot_count = limits.get("selected_update_slot_count")
    maximum_slot_count = limits.get("maximum_update_slot_count")
    planned_compressed_bytes = limits.get("selected_compressed_bytes")
    maximum_compressed_bytes = limits.get("maximum_selected_compressed_bytes")
    numeric_limits = (
        selected_slot_count,
        maximum_slot_count,
        planned_compressed_bytes,
        maximum_compressed_bytes,
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in numeric_limits
    ) or requested_slots != artifact_slots or (
        selected_slot_count != len(selected_artifacts)
        or maximum_slot_count < len(selected_artifacts)
        or planned_compressed_bytes != selected_compressed_bytes
        or maximum_compressed_bytes < selected_compressed_bytes
    ):
        raise ProxyFinalizeError("targeted-raw plan 的槽数或压缩字节不闭合")
    non_actions = stats.get("non_actions")
    if not isinstance(non_actions, Mapping) or (
        non_actions.get("database_connections") != 0
        or non_actions.get("database_writes") != 0
        or non_actions.get("rib_files_opened") != 0
        or non_actions.get("seed_performed") is not False
        or non_actions.get("state_replay_performed") is not False
        or non_actions.get("causal_claim_allowed") is not False
    ):
        raise ProxyFinalizeError("targeted-raw non_actions 不闭合")

    contents = manifest.get("contents")
    content_names = {
        "route_events": "route-events.jsonl.gz",
        "raw_record_refs": "raw-record-refs.jsonl.gz",
        "parser_stats": "parser-stats.json",
    }
    if not isinstance(contents, Mapping) or set(contents) != set(content_names):
        raise ProxyFinalizeError("targeted-raw manifest.contents 非法")
    for key, name in content_names.items():
        item = contents.get(key)
        if not isinstance(item, Mapping) or (
            item.get("sha256") != refs[name]["sha256"]
            or item.get("size_bytes") != refs[name]["size_bytes"]
        ):
            raise ProxyFinalizeError(f"targeted-raw contents.{key} 不闭合")

    routes = _read_gzip_jsonl(paths["route-events.jsonl.gz"], "RouteEvent")
    raw_refs = _read_gzip_jsonl(paths["raw-record-refs.jsonl.gz"], "raw ref")
    counts = manifest.get("counts")
    aggregate = stats.get("aggregate")
    if not isinstance(counts, Mapping) or not isinstance(aggregate, Mapping):
        raise ProxyFinalizeError("targeted-raw counts/aggregate 缺失")
    if (
        counts.get("route_event_count") != len(routes)
        or counts.get("raw_record_ref_count") != len(raw_refs)
        or aggregate.get("retained_route_event_count") != len(routes)
        or aggregate.get("retained_raw_record_ref_count") != len(raw_refs)
        or len(routes) != len(raw_refs)
        or counts.get("update_artifact_count") != len(selected_artifacts)
        or aggregate.get("selected_update_artifact_count")
        != len(selected_artifacts)
        or aggregate.get("compressed_read_pass_count")
        != len(selected_artifacts)
        or aggregate.get("compressed_bytes_read")
        != selected_compressed_bytes
        or stats.get("plan_id") != plan.get("plan_id")
    ):
        raise ProxyFinalizeError("targeted-raw RouteEvent/raw ref 计数不闭合")

    artifact_stats = stats.get("artifact_stats")
    if not isinstance(artifact_stats, list) or len(artifact_stats) != len(
        selected_artifacts
    ):
        raise ProxyFinalizeError("targeted-raw artifact_stats 数量不闭合")
    artifact_stats_by_id: dict[str, Mapping[str, Any]] = {}
    for index, (artifact_stat, artifact) in enumerate(
        zip(artifact_stats, selected_artifacts)
    ):
        if not isinstance(artifact_stat, Mapping):
            raise ProxyFinalizeError(
                f"targeted-raw artifact_stats[{index}] 必须是对象"
            )
        native = artifact_stat.get("native_statistics")
        attestation = artifact_stat.get("runtime_parser_attestation")
        if not isinstance(native, Mapping) or not isinstance(
            attestation, Mapping
        ):
            raise ProxyFinalizeError(
                f"targeted-raw artifact_stats[{index}] 缺少 parser 证明"
            )
        if (
            artifact_stat.get("artifact_index") != index
            or artifact_stat.get("artifact_id") != artifact.get("artifact_id")
            or artifact_stat.get("artifact_time_utc")
            != artifact.get("artifact_time_utc")
            or artifact_stat.get("file_sha256") != artifact.get("file_sha256")
            or artifact_stat.get("size_bytes") != artifact.get("size_bytes")
            or native.get("status") != "complete"
            or native.get("artifact_id") != artifact.get("artifact_id")
            or native.get("compressed_file_sha256")
            != artifact.get("file_sha256")
            or native.get("compressed_size_bytes") != artifact.get("size_bytes")
            or native.get("compressed_bytes_read_observed")
            != artifact.get("size_bytes")
            or native.get("compressed_read_passes") != 1
        ):
            raise ProxyFinalizeError(
                f"targeted-raw artifact_stats[{index}] 单遍读取证明不闭合"
            )
        attestation_semantic = dict(attestation)
        supplied_attestation_fingerprint = attestation_semantic.pop(
            "attestation_fingerprint_sha256", None
        )
        expected_attestation_fingerprint = _sha256_bytes(
            _canonical_bytes(
                {
                    "schema": "parser_attestation_fingerprint_v1",
                    "attestation": attestation_semantic,
                }
            )
        )
        configuration = attestation.get("configuration")
        if (
            attestation.get("schema_version") != "parser_attestation_v1"
            or supplied_attestation_fingerprint
            != expected_attestation_fingerprint
            or artifact_stat.get("parser_attestation_fingerprint_sha256")
            != expected_attestation_fingerprint
            or attestation.get("parser_name")
            != parser_contract.get("parser_name")
            or attestation.get("parser_version")
            != parser_contract.get("parser_version")
            or attestation.get("parser_binary_sha256")
            != parser_contract.get("binary_sha256")
            or attestation.get("adapter_source_sha256")
            != parser_contract.get("adapter_source_sha256")
            or attestation.get("binary_execution_policy")
            != parser_contract.get("binary_execution_policy")
            or not isinstance(configuration, Mapping)
            or attestation.get("configuration_sha256")
            != _sha256_bytes(_canonical_bytes(configuration))
        ):
            raise ProxyFinalizeError(
                f"targeted-raw artifact_stats[{index}] parser attestation 不闭合"
            )
        artifact_stats_by_id[str(artifact["artifact_id"])] = artifact_stat

    route_by_id: dict[str, Mapping[str, Any]] = {}
    raw_by_route: dict[str, Mapping[str, Any]] = {}
    raw_ids: set[str] = set()
    for index, route in enumerate(routes):
        route_id = route.get("route_event_id")
        raw_id = route.get("raw_record_ref_id")
        artifact = artifact_by_id.get(route.get("artifact_id"))
        slot = route.get("artifact_slot_utc")
        event_time = route.get("event_time_utc")
        if (
            not isinstance(route_id, str)
            or not route_id
            or route_id in route_by_id
            or not isinstance(raw_id, str)
            or not raw_id
            or route.get("evidence_scope") != TARGETED_EVIDENCE_SCOPE
            or route.get("lineage_status")
            != "raw_traceable_message_observation"
            or route.get("causal_claim_allowed") is not False
            or not isinstance(artifact, Mapping)
            or route.get("artifact_slot_utc") not in requested_slots
            or artifact.get("artifact_time_utc") != slot
            or artifact.get("file_sha256") != route.get("file_sha256")
            or not isinstance(event_time, str)
        ):
            raise ProxyFinalizeError(f"RouteEvent[{index}] 消息证据身份非法")
        slot_time = _utc(slot, f"RouteEvent[{index}].artifact_slot_utc")
        observed_time = _utc(event_time, f"RouteEvent[{index}].event_time_utc")
        if not (
            slot_time
            <= observed_time
            < slot_time + timedelta(seconds=profile_window["granularity_seconds"])
        ):
            raise ProxyFinalizeError(f"RouteEvent[{index}] 不在绑定 artifact 槽内")
        route_by_id[route_id] = route
    for index, raw in enumerate(raw_refs):
        route_id = raw.get("route_event_id")
        raw_id = raw.get("raw_record_ref_id")
        if (
            route_id not in route_by_id
            or route_id in raw_by_route
            or not isinstance(raw_id, str)
            or not raw_id
            or raw_id in raw_ids
            or raw.get("evidence_scope") != TARGETED_EVIDENCE_SCOPE
            or raw.get("verification_status") != "native_parser_verified"
        ):
            raise ProxyFinalizeError(f"raw ref[{index}] 消息证据身份非法")
        route = route_by_id[route_id]
        for field in (
            "raw_record_ref_id",
            "artifact_id",
            "file_sha256",
            "collector_id",
            "artifact_slot_utc",
            "event_time_utc",
            "vp_id",
            "record_ordinal",
            "element_ordinal",
            "prefix",
            "action",
        ):
            if raw.get(field) != route.get(field):
                raise ProxyFinalizeError(
                    f"RouteEvent/raw ref 字段不一致：{route_id}/{field}"
                )
        if not isinstance(raw.get("raw_record_sha256"), str) or _SHA256_RE.fullmatch(
            raw["raw_record_sha256"]
        ) is None:
            raise ProxyFinalizeError("raw ref 缺少有效 raw_record_sha256")
        if (
            isinstance(raw.get("record_offset"), bool)
            or not isinstance(raw.get("record_offset"), int)
            or raw["record_offset"] < 0
            or isinstance(raw.get("record_length"), bool)
            or not isinstance(raw.get("record_length"), int)
            or raw["record_length"] <= 0
        ):
            raise ProxyFinalizeError("raw ref 缺少有效 record offset/length")
        raw_by_route[route_id] = raw
        raw_ids.add(raw_id)
    if set(route_by_id) != set(raw_by_route):
        raise ProxyFinalizeError("RouteEvent/raw ref 不是一一闭合")

    routes_by_artifact: dict[str, list[Mapping[str, Any]]] = {
        artifact_id: [] for artifact_id in artifact_stats_by_id
    }
    for route in route_by_id.values():
        routes_by_artifact[str(route["artifact_id"])].append(route)
    for artifact_id, artifact_stat in artifact_stats_by_id.items():
        artifact_routes = routes_by_artifact[artifact_id]
        if (
            artifact_stat.get("retained_route_event_count")
            != len(artifact_routes)
            or artifact_stat.get("retained_announce_count")
            != sum(row.get("action") == "announce" for row in artifact_routes)
            or artifact_stat.get("retained_withdraw_count")
            != sum(row.get("action") == "withdraw" for row in artifact_routes)
        ):
            raise ProxyFinalizeError(
                f"targeted-raw artifact {artifact_id} 保留计数不闭合"
            )
    if (
        aggregate.get("retained_announce_count")
        != sum(row.get("action") == "announce" for row in route_by_id.values())
        or aggregate.get("retained_withdraw_count")
        != sum(row.get("action") == "withdraw" for row in route_by_id.values())
    ):
        raise ProxyFinalizeError("targeted-raw 汇总 A/W 计数不闭合")

    manifest_entities = manifest.get("entity_observations")
    stats_entities = stats.get("entity_observations")
    requested_pairs = plan.get("requested_pairs")
    if (
        not isinstance(manifest_entities, list)
        or not isinstance(stats_entities, list)
        or not isinstance(requested_pairs, list)
        or _canonical_bytes(manifest_entities) != _canonical_bytes(stats_entities)
        or len(manifest_entities) != len(requested_pairs)
    ):
        raise ProxyFinalizeError("targeted-raw 实体观测与 plan 不闭合")
    observations_by_pair: dict[int, list[Mapping[str, Any]]] = {
        index: [] for index in range(len(requested_pairs))
    }
    for route_id, route in route_by_id.items():
        route_pairs = route.get("requested_pairs")
        pair_matches = route.get("requested_pair_matches")
        if (
            not isinstance(route_pairs, list)
            or len(route_pairs) != 1
            or not isinstance(pair_matches, list)
            or len(pair_matches) != 1
        ):
            raise ProxyFinalizeError(
                f"RouteEvent 的请求实体关联不是一对一：{route_id}"
            )
        route_pair = route_pairs[0]
        pair_match = pair_matches[0]
        pair_index = (
            route_pair.get("pair_index")
            if isinstance(route_pair, Mapping)
            else None
        )
        if (
            isinstance(pair_index, bool)
            or not isinstance(pair_index, int)
            or pair_index not in observations_by_pair
            or not isinstance(pair_match, Mapping)
            or pair_match.get("pair_index") != pair_index
        ):
            raise ProxyFinalizeError(
                f"RouteEvent 的 pair_index 非法：{route_id}"
            )
        planned_pair = requested_pairs[pair_index]
        if not isinstance(planned_pair, Mapping) or any(
            route_pair.get(field) != planned_pair.get(field)
            for field in ("pair_index", "asn", "prefix", "ip_family")
        ) or route.get("prefix") != planned_pair.get("prefix"):
            raise ProxyFinalizeError(
                f"RouteEvent 未绑定 plan 中的请求实体：{route_id}"
            )
        observations_by_pair[pair_index].append(route)

    entity_details = []
    for pair_index, raw_entity in enumerate(manifest_entities):
        pair = requested_pairs[pair_index]
        if not isinstance(raw_entity, Mapping) or not isinstance(pair, Mapping):
            raise ProxyFinalizeError(
                f"targeted-raw entity_observations[{pair_index}] 非法"
            )
        observations = observations_by_pair[pair_index]
        announce_count = sum(row.get("action") == "announce" for row in observations)
        withdraw_count = sum(row.get("action") == "withdraw" for row in observations)
        matched_announce_count = 0
        for route in observations:
            match = route["requested_pair_matches"][0]
            if str(match.get("target_asn")) != str(pair.get("asn")):
                raise ProxyFinalizeError("RouteEvent target_asn 未绑定计划 ASN")
            if route.get("action") == "announce":
                origin_resolution = route.get("origin_resolution")
                if not isinstance(origin_resolution, Mapping) or not isinstance(
                    origin_resolution.get("origins"), list
                ):
                    raise ProxyFinalizeError("ANNOUNCE 缺少结构化 origin 解析")
                origin_state = origin_resolution.get("state")
                target = int(str(pair.get("asn")))
                if origin_state == "resolved":
                    expected_match_status = (
                        "matched_target_origin"
                        if target in origin_resolution["origins"]
                        else "origin_does_not_match_target"
                    )
                elif origin_state == "unknown":
                    expected_match_status = "origin_unknown"
                else:
                    raise ProxyFinalizeError("ANNOUNCE origin 状态非法")
                if match.get("status") != expected_match_status:
                    raise ProxyFinalizeError(
                        "ANNOUNCE origin 注释与实际解析结果不一致"
                    )
                if match.get("status") == "matched_target_origin":
                    matched_announce_count += 1
            elif route.get("action") == "withdraw":
                origin_resolution = route.get("origin_resolution")
                if (
                    match.get("status") != "not_applicable"
                    or route.get("as_path") is not None
                    or not isinstance(origin_resolution, Mapping)
                    or origin_resolution.get("state") != "not_applicable"
                    or origin_resolution.get("origins") != []
                ):
                    raise ProxyFinalizeError(
                        "WITHDRAW 的目标 origin 关联必须是 not_applicable"
                    )
            else:
                raise ProxyFinalizeError("RouteEvent action 非 announce/withdraw")
        if (
            raw_entity.get("pair_index") != pair_index
            or str(raw_entity.get("asn")) != str(pair.get("asn"))
            or raw_entity.get("prefix") != pair.get("prefix")
            or raw_entity.get("observation_count") != len(observations)
            or raw_entity.get("window_expanded") is not False
        ):
            raise ProxyFinalizeError(
                f"targeted-raw entity_observations[{pair_index}] 计数不闭合"
            )
        entity_details.append(
            {
                "pair_index": pair_index,
                "asn": str(pair.get("asn")),
                "prefix": pair.get("prefix"),
                "ip_family": pair.get("ip_family"),
                "observation_state": raw_entity.get("observation_state"),
                "observation_count": len(observations),
                "announce_count": announce_count,
                "withdraw_count": withdraw_count,
                "announce_target_origin_match_count": matched_announce_count,
                "distinct_vp_count": len(
                    {
                        route.get("vp_id")
                        for route in observations
                        if isinstance(route.get("vp_id"), str)
                        and route.get("vp_id")
                    }
                ),
                "window_expanded": False,
                "interpretation_zh": raw_entity.get("interpretation_zh"),
            }
        )
    expected_pair_counts = {
        str(row["pair_index"]): row["observation_count"]
        for row in entity_details
    }
    if (
        aggregate.get("retained_observation_count_by_pair_index")
        != expected_pair_counts
    ):
        raise ProxyFinalizeError("targeted-raw 分实体汇总计数不闭合")

    return {
        "manifest": manifest,
        "plan": plan,
        "stats": stats,
        "routes": routes,
        "raw_refs": raw_refs,
        "files": refs,
        "message_evidence": {
            "evidence_scope": TARGETED_EVIDENCE_SCOPE,
            "route_event_count": len(routes),
            "raw_record_ref_count": len(raw_refs),
            "entity_observations": entity_details,
            "mapping_to_state_performed": False,
            "state_claim_allowed": False,
            "causal_claim_allowed": False,
        },
    }


def _point_ref(point: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "index": point["index"],
        "observed_at_utc": point["start_utc"],
        "observed_at_local": point["start_local"],
        "ipv4_address_equivalent": point["value"],
    }


def _new_wave(
    *,
    ordinal: int,
    points: Sequence[Mapping[str, Any]],
    split: Mapping[str, Any] | None,
) -> MutableMapping[str, Any]:
    trough = min(points, key=lambda row: row["value"])
    return {
        "ordinal": ordinal,
        "onset": points[0],
        "detected": points[-1],
        "trough": trough,
        "rebound_peak": None,
        "decline_run": [],
        "split": split,
    }


def _update_wave(
    active: MutableMapping[str, Any],
    point: Mapping[str, Any],
    *,
    threshold: float,
    confirm_slots: int,
) -> None:
    current = active["waves"][-1]
    if point["value"] < current["trough"]["value"]:
        current["trough"] = point
        current["rebound_peak"] = None
        current["decline_run"] = []
        return
    if current["rebound_peak"] is None:
        if point["value"] - current["trough"]["value"] >= threshold:
            current["rebound_peak"] = point
        return
    if point["value"] > current["rebound_peak"]["value"]:
        current["rebound_peak"] = point
        current["decline_run"] = []
        return
    decline = current["rebound_peak"]["value"] - point["value"]
    if decline < threshold:
        current["decline_run"] = []
        return
    run = current["decline_run"]
    if run and run[-1]["end"] != point["start"]:
        run.clear()
    run.append(point)
    if len(run) < confirm_slots:
        return
    decline_points = run[-confirm_slots:]
    decline_trough = min(decline_points, key=lambda row: row["value"])
    split = {
        "decision": "same_proxy_episode_new_proxy_wave",
        "causal_relation": "not_assessed",
        "full_recovery_between_waves": False,
        "previous_trough": _point_ref(current["trough"]),
        "rebound": _point_ref(current["rebound_peak"]),
        "new_decline": _point_ref(decline_points[0]),
        "rebound_amplitude": current["rebound_peak"]["value"]
        - current["trough"]["value"],
        "new_decline_amplitude": current["rebound_peak"]["value"]
        - decline_trough["value"],
        "significance_threshold": threshold,
        "unit": "legacy_ipv4_address_equivalent_from_v4ip_num",
    }
    active["waves"].append(
        _new_wave(
            ordinal=current["ordinal"] + 1,
            points=decline_points,
            split=split,
        )
    )


def _finalize_wave(
    wave: Mapping[str, Any],
    *,
    episode_proxy_id: str,
    db_sha256: str,
) -> dict[str, Any]:
    proxy_id = _stable_id(
        "metric_proxy_wave_v1_",
        {
            "db_first_sha256": db_sha256,
            "episode_proxy_id": episode_proxy_id,
            "ordinal": wave["ordinal"],
            "onset_at_utc": wave["onset"]["start_utc"],
        },
    )
    rebound = wave.get("rebound_peak")
    return {
        "proxy_wave_id": proxy_id,
        "candidate_kind": "metric_only_not_country_outage_wave",
        "ordinal": wave["ordinal"],
        "onset_at_utc": wave["onset"]["start_utc"],
        "onset_at_local": wave["onset"]["start_local"],
        "detected_at_utc": wave["detected"]["start_utc"],
        "detected_at_local": wave["detected"]["start_local"],
        "trough": _point_ref(wave["trough"]),
        "rebound_at_utc": None if rebound is None else rebound["start_utc"],
        "rebound_at_local": None if rebound is None else rebound["start_local"],
        "relation_to_previous_wave": (
            "first_proxy_wave"
            if wave["ordinal"] == 1
            else "same_proxy_episode_after_metric_rebound"
        ),
        "causal_relation": "not_assessed",
        "split_evidence": wave.get("split"),
    }


def _finalize_episode(
    active: Mapping[str, Any],
    *,
    db_sha256: str,
    observation_end: datetime,
    full_recovery: Mapping[str, Any] | None,
    partial_slots: int,
) -> dict[str, Any]:
    onset = active["onset"]
    proxy_id = _stable_id(
        "metric_proxy_episode_v1_",
        {
            "db_first_sha256": db_sha256,
            "onset_at_utc": onset["start_utc"],
            "metric": "legacy_ipv4_address_equivalent_from_v4ip_num",
        },
    )
    if full_recovery is not None:
        recovery_state = "fully_recovered_proxy"
        measured_to = full_recovery["confirmation_end"]
        duration_state = "exact"
    else:
        current_partial = len(active["partial_run"]) >= partial_slots
        recovery_state = (
            "partially_recovered_proxy"
            if current_partial
            else "recovering_proxy"
            if active["last"]["value"] > active["trough"]["value"]
            else "ongoing_proxy"
        )
        measured_to = observation_end
        duration_state = "lower_bound"
    seconds = int((measured_to - onset["start"]).total_seconds())
    waves = [
        _finalize_wave(
            wave,
            episode_proxy_id=proxy_id,
            db_sha256=db_sha256,
        )
        for wave in active["waves"]
    ]
    partial = active.get("partial_confirmed")
    return {
        "proxy_episode_id": proxy_id,
        "candidate_kind": "metric_only_not_country_outage_episode",
        "onset_at_utc": onset["start_utc"],
        "onset_at_local": onset["start_local"],
        "detected_at_utc": active["detected"]["start_utc"],
        "detected_at_local": active["detected"]["start_local"],
        "trough": _point_ref(active["trough"]),
        "partial_recovery_candidate": partial,
        "full_recovery_candidate": (
            None
            if full_recovery is None
            else {
                "start_at_utc": full_recovery["start"]["start_utc"],
                "start_at_local": full_recovery["start"]["start_local"],
                "confirmed_at_utc": _utc_text(
                    full_recovery["confirmation_end"]
                ),
                "confirmed_at_local": _local_text(
                    full_recovery["confirmation_end"]
                ),
                "confirmation_slot_count": full_recovery["slot_count"],
            }
        ),
        "observation_end_at_utc": _utc_text(measured_to),
        "observation_end_at_local": _local_text(measured_to),
        "recovery_state_proxy": recovery_state,
        "duration": {
            "duration_state": duration_state,
            "seconds": seconds if duration_state == "exact" else None,
            "minimum_seconds": seconds if duration_state == "lower_bound" else None,
            "maximum_seconds": seconds if duration_state == "exact" else None,
            "measured_to_utc": _utc_text(measured_to),
        },
        "wave_count": len(waves),
        "waves": waves,
    }


def build_proxy_analysis(
    db_first: Mapping[str, Any],
    points: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
    *,
    db_first_sha256: str,
    targeted_message_evidence: Mapping[str, Any],
    input_bindings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """生成旧等价值上的独立 metric-only proxy，不生成状态合同。"""

    numeric = profile["baseline"]["numeric"]
    algorithms = profile["algorithms"]
    normal_band_profile = profile["baseline"]["normal_band"]
    start = _utc(profile["window"]["start_utc"], "profile.window.start")
    baseline_end = start + timedelta(
        seconds=numeric["initial_duration_seconds"]
    )
    exclusion = _utc(
        numeric["exclusion_boundary"]["at_utc"],
        "profile.baseline.exclusion_boundary",
    )
    if baseline_end > exclusion:
        raise ProxyFinalizeError("proxy 基线不得跨过候选排除边界")
    baseline_points = [
        point for point in points if start <= point["start"] < baseline_end
    ]
    expected_baseline_count = (
        numeric["initial_duration_seconds"]
        // profile["window"]["granularity_seconds"]
    )
    if len(baseline_points) != expected_baseline_count:
        raise ProxyFinalizeError("proxy 六小时基线槽数不完整")
    values = [point["value"] for point in baseline_points]
    median = float(statistics.median(values))
    mad = float(statistics.median(abs(value - median) for value in values))
    relative_mad = mad / median
    if relative_mad > numeric["max_relative_mad"]:
        raise ProxyFinalizeError("旧等价值 proxy 基线不满足冻结稳定门")

    episode_profile = algorithms["episode"]
    recovery_profile = algorithms["recovery"]
    wave_profile = algorithms["wave"]
    anomaly_threshold = median * episode_profile["ipv4_visible_ratio_below"]
    band_half = max(
        normal_band_profile["mad_multiplier"] * mad,
        normal_band_profile["absolute_floor_ratio"] * median,
    )
    normal_lower = median - band_half
    normal_upper = median + band_half
    wave_threshold = max(
        wave_profile["baseline_ratio_floor"] * median,
        wave_profile["mad_multiplier"] * mad,
    )
    confirm_slots = episode_profile["confirm_consecutive_slots"]
    partial_slots = recovery_profile["partial_confirm_consecutive_slots"]
    full_slots = recovery_profile["full_confirm_consecutive_slots"]
    partial_threshold = (
        median * recovery_profile["partial_visible_ratio_at_least"]
    )
    observation_end = _utc(
        profile["window"]["observation_end_utc"],
        "profile.window.observation_end",
    )

    episodes: list[dict[str, Any]] = []
    pending: list[Mapping[str, Any]] = []
    active: MutableMapping[str, Any] | None = None
    for point in points:
        anomaly = point["value"] < anomaly_threshold
        if active is None:
            if anomaly:
                pending.append(point)
                if len(pending) >= confirm_slots:
                    onset_points = pending[-confirm_slots:]
                    active = {
                        "onset": onset_points[0],
                        "detected": onset_points[-1],
                        "last": onset_points[-1],
                        "trough": min(
                            onset_points, key=lambda row: row["value"]
                        ),
                        "partial_run": [],
                        "full_run": [],
                        "partial_confirmed": None,
                        "waves": [
                            _new_wave(
                                ordinal=1,
                                points=onset_points,
                                split=None,
                            )
                        ],
                    }
                    pending = []
            else:
                pending = []
            continue

        active["last"] = point
        if point["value"] < active["trough"]["value"]:
            active["trough"] = point
        if point["value"] >= partial_threshold:
            active["partial_run"].append(point)
            if (
                active["partial_confirmed"] is None
                and len(active["partial_run"]) == partial_slots
            ):
                run = active["partial_run"]
                active["partial_confirmed"] = {
                    "start_at_utc": run[0]["start_utc"],
                    "start_at_local": run[0]["start_local"],
                    "confirmed_at_utc": _utc_text(run[-1]["end"]),
                    "confirmed_at_local": _local_text(run[-1]["end"]),
                    "confirmation_slot_count": partial_slots,
                    "later_relapse_observed": False,
                }
        else:
            if active["partial_confirmed"] is not None:
                active["partial_confirmed"]["later_relapse_observed"] = True
            active["partial_run"] = []

        in_normal_band = normal_lower <= point["value"] <= normal_upper
        if in_normal_band and not anomaly:
            active["full_run"].append(point)
        else:
            active["full_run"] = []
        if len(active["full_run"]) == full_slots:
            run = active["full_run"]
            full = {
                "start": run[0],
                "confirmation_end": run[-1]["end"],
                "slot_count": full_slots,
            }
            episodes.append(
                _finalize_episode(
                    active,
                    db_sha256=db_first_sha256,
                    observation_end=observation_end,
                    full_recovery=full,
                    partial_slots=partial_slots,
                )
            )
            active = None
            pending = []
            continue
        _update_wave(
            active,
            point,
            threshold=wave_threshold,
            confirm_slots=confirm_slots,
        )

    if active is not None:
        episodes.append(
            _finalize_episode(
                active,
                db_sha256=db_first_sha256,
                observation_end=observation_end,
                full_recovery=None,
                partial_slots=partial_slots,
            )
        )

    return {
        "schema_version": PROXY_SCHEMA_VERSION,
        "analysis_kind": "database_metric_proxy",
        "candidate_only": True,
        "route_state_semantics": False,
        "vp_population_available": False,
        "workflow_state": "completed",
        "acceptance_state": "not_accepted",
        "evidence_scope": "database_aggregate_compatibility_metric_only",
        "incident_ref": EXPECTED_INCIDENT_REF,
        "source_metric": {
            "field": "country_series.points[].ipv4_address_equivalent",
            "unit": "legacy_ipv4_address_equivalent_from_v4ip_num",
            "formula": "legacy_v4ip_num_slash24_equivalent_times_256",
            "is_deduplicated_address_union": False,
            "is_country_outage_state_snapshot": False,
            "description_zh": (
                "该值是旧 v4ip_num 的 /24 等价值乘 256，不是去重 IPv4 "
                "地址并集，也不包含逐 ASN 或逐 VP 状态。"
            ),
        },
        "input_bindings": [dict(row) for row in input_bindings],
        "baseline": {
            "window_start_utc": _utc_text(start),
            "window_end_exclusive_utc": _utc_text(baseline_end),
            "observed_slot_count": len(baseline_points),
            "statistic": "median",
            "median": median,
            "mad": mad,
            "relative_mad": relative_mad,
            "max_relative_mad": numeric["max_relative_mad"],
            "stability_state": "stable_for_metric_proxy",
        },
        "thresholds": {
            "anomaly_below": anomaly_threshold,
            "anomaly_confirm_consecutive_slots": confirm_slots,
            "partial_recovery_at_least": partial_threshold,
            "partial_confirm_consecutive_slots": partial_slots,
            "normal_band_lower": normal_lower,
            "normal_band_upper": normal_upper,
            "full_confirm_consecutive_slots": full_slots,
            "wave_significance": wave_threshold,
            "metric_unit": "legacy_ipv4_address_equivalent_from_v4ip_num",
        },
        "episode_count": len(episodes),
        "episodes": episodes,
        "targeted_message_evidence": dict(targeted_message_evidence),
        "state_mapping": {
            "route_event_to_state_mapping_performed": False,
            "raw_ref_to_state_mapping_performed": False,
            "country_outage_sample_generated": False,
            "country_outage_episode_generated": False,
            "country_outage_wave_generated": False,
        },
        "limitations_zh": [
            "proxy 使用旧 IPv4 等价值，不是 OpenSpec 要求的去重地址并集。",
            "数据库曲线没有逐槽受损 ASN 比例、同快照人口、VP 覆盖和前缀集合。",
            "定向 RouteEvent/raw ref 仅是消息证据，未映射到路由状态。",
            "结果不得用于确认完整传播范围、完全恢复、前兆因果或政治原因。",
        ],
    }


def _evidence_registry(
    *,
    db_ref: Mapping[str, Any],
    profile_ref: Mapping[str, Any],
    inventory_ref: Mapping[str, Any],
    source_fact_ref: Mapping[str, Any],
    targeted: Mapping[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "kind": "source_fact",
            "ref": "inputs/db-first/iran-db-first.json",
            "sha256": db_ref["sha256"],
        },
        {
            "kind": "source_fact",
            "ref": "inputs/frozen-source-fact.json",
            "sha256": source_fact_ref["sha256"],
        },
        {
            "kind": "report_page",
            "ref": "inputs/frozen-claim-inventory.json",
            "sha256": inventory_ref["sha256"],
        },
        {
            "kind": "route_event",
            "ref": "inputs/targeted-raw/route-events.jsonl.gz",
            "sha256": targeted["files"]["route-events.jsonl.gz"]["sha256"],
        },
        {
            "kind": "raw_record",
            "ref": "inputs/targeted-raw/raw-record-refs.jsonl.gz",
            "sha256": targeted["files"]["raw-record-refs.jsonl.gz"]["sha256"],
        },
        {
            "kind": "limitation",
            "ref": "inputs/frozen-profile/metric-and-causal-boundary",
            "sha256": profile_ref["sha256"],
        },
    ]


def _known(value: Any, unit: str) -> dict[str, Any]:
    return {
        "value": value,
        "value_state": "recomputed",
        "unit": unit,
        "snapshot_id": None,
        "missing_reason": None,
    }


def _unknown(reason: str) -> dict[str, Any]:
    return {
        "value": None,
        "value_state": "unknown",
        "unit": None,
        "snapshot_id": None,
        "missing_reason": reason,
    }


def _assessment(
    outcome: str,
    value: Mapping[str, Any],
    *,
    evidence: Sequence[str],
    rationale: str,
    limitations: Sequence[str],
    counterevidence: Sequence[str] = (),
    unknown_rating: str | None = None,
) -> dict[str, Any]:
    result = {
        "comparison_outcome": outcome,
        "recomputed_value": dict(value),
        "evidence_refs": list(evidence),
        "counterevidence_refs": list(counterevidence),
        "limitations_zh": list(limitations),
        "rationale_zh": rationale,
    }
    if unknown_rating is not None:
        result["unknown_rating"] = unknown_rating
    return result


def _build_assessments(
    proxy: Mapping[str, Any],
    db_first: Mapping[str, Any],
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    if proxy.get("episode_count") != 1:
        raise ProxyFinalizeError("伊朗 DB metric proxy 必须恰好得到一个候选 episode")
    episode = proxy["episodes"][0]
    trough = episode["trough"]
    baseline = proxy["baseline"]["median"]
    decline = (baseline - trough["ipv4_address_equivalent"]) / baseline
    db_ref = "inputs/db-first/iran-db-first.json"
    source_ref = "inputs/frozen-source-fact.json"
    route_ref = "inputs/targeted-raw/route-events.jsonl.gz"
    raw_ref = "inputs/targeted-raw/raw-record-refs.jsonl.gz"
    limitation_ref = "inputs/frozen-profile/metric-and-causal-boundary"
    common_proxy_limit = (
        "复算值来自旧 IPv4 等价值的 metric-only proxy，不是去重地址并集或路由状态快照。",
    )
    affected_unknown = (
        "数据库没有同一 cohort、同一时点的逐 ASN 可见状态，当前不能复算该人口主张。",
    )
    fact = db_first["fact"]
    inventory_claims = inventory.get("claims")
    if not isinstance(inventory_claims, list):
        raise ProxyFinalizeError("主张清单 claims 缺失")
    claim_by_key = {
        claim.get("claim_key"): claim
        for claim in inventory_claims
        if isinstance(claim, Mapping)
    }
    required_claim_keys = {
        "report_event_time",
        "ipv4_decline",
        "recovery_state",
        "report_affected_asn_ratio",
        "report_visibility_class_counts",
        "database_affected_asn_ratio",
    }
    if not required_claim_keys <= set(claim_by_key):
        raise ProxyFinalizeError("主张清单缺少数值对账项")

    reported_event_time = claim_by_key["report_event_time"].get(
        "reported_value"
    )
    if not isinstance(reported_event_time, str):
        raise ProxyFinalizeError("报告事件时间主张非法")
    try:
        parsed_reported_event = datetime.fromisoformat(reported_event_time)
    except ValueError as error:
        raise ProxyFinalizeError("报告事件时间主张不是合法带时区时间") from error
    if parsed_reported_event.utcoffset() is None:
        raise ProxyFinalizeError("报告事件时间主张必须带时区")
    reported_event_utc = parsed_reported_event.astimezone(UTC)
    proxy_onset = _utc(episode["onset_at_utc"], "proxy episode onset")
    event_outcome = (
        "consistent" if proxy_onset == reported_event_utc else "different"
    )

    reported_decline = claim_by_key["ipv4_decline"].get("reported_value")
    if (
        isinstance(reported_decline, bool)
        or not isinstance(reported_decline, (int, float))
        or not math.isfinite(float(reported_decline))
    ):
        raise ProxyFinalizeError("报告 IPv4 降幅主张非法")
    decline_policy = claim_by_key["ipv4_decline"].get(
        "recomputation_policy"
    )
    if decline_policy != "same_snapshot_deduplicated_ipv4_address_union":
        raise ProxyFinalizeError("报告 IPv4 降幅复算策略偏离冻结合同")
    decline_tolerance = 0.005
    decline_is_numerically_close = (
        abs(decline - float(reported_decline)) <= decline_tolerance
    )
    # 当前输入只能复算旧 v4ip_num × 256 等价值。即使数值接近，也没有
    # 满足冻结主张要求的同快照去重 IPv4 地址并集口径，不能标为 confirmed。
    decline_outcome = "different"

    reported_recovery_state = claim_by_key["recovery_state"].get(
        "reported_value"
    )
    if not isinstance(reported_recovery_state, str):
        raise ProxyFinalizeError("报告恢复状态主张非法")
    partial_recovery = episode["partial_recovery_candidate"]
    full_recovery = episode["full_recovery_candidate"]
    recovery_outcome = (
        "consistent"
        if (
            reported_recovery_state
            == "partially_recovered_not_fully_recovered"
            and episode["recovery_state_proxy"]
            == "partially_recovered_proxy"
            and isinstance(partial_recovery, Mapping)
            and full_recovery is None
        )
        else "different"
    )
    if isinstance(partial_recovery, Mapping):
        partial_description = (
            f"首次99%候选于{partial_recovery['start_at_local']}开始、"
            f"{partial_recovery['confirmed_at_local']}确认，后续反复="
            f"{str(partial_recovery['later_relapse_observed']).lower()}"
        )
    else:
        partial_description = "未形成连续六槽的99%部分恢复候选"
    full_description = (
        "未形成完全恢复候选"
        if full_recovery is None
        else f"完全恢复候选于{full_recovery['confirmed_at_local']}确认"
    )

    reported_database_ratio = claim_by_key[
        "database_affected_asn_ratio"
    ].get("reported_value")
    recomputed_database_ratio = {
        "affected": fact["affected_asn_count"],
        "total": fact["total_asn_count"],
        "ratio": round(
            fact["affected_asn_count"] / fact["total_asn_count"],
            4,
        ),
    }
    if not isinstance(reported_database_ratio, Mapping):
        raise ProxyFinalizeError("旧数据库 ASN 比例主张非法")
    database_ratio_outcome = (
        "consistent"
        if (
            reported_database_ratio.get("affected")
            == recomputed_database_ratio["affected"]
            and reported_database_ratio.get("total")
            == recomputed_database_ratio["total"]
            and isinstance(reported_database_ratio.get("ratio"), (int, float))
            and not isinstance(reported_database_ratio.get("ratio"), bool)
            and abs(
                float(reported_database_ratio["ratio"])
                - recomputed_database_ratio["ratio"]
            )
            <= 0.0001
        )
        else "different"
    )
    return {
        "report_event_time": _assessment(
            event_outcome,
            _known(
                {
                    "onset_at_utc": episode["onset_at_utc"],
                    "detected_at_utc": episode["detected_at_utc"],
                    "trough_at_utc": trough["observed_at_utc"],
                },
                "database_metric_proxy_time_boundaries",
            ),
            evidence=(db_ref,),
            counterevidence=(source_ref,),
            limitations=common_proxy_limit,
            rationale=(
                f"报告时间为{_local_text(reported_event_utc)}；proxy "
                f"在{episode['onset_at_local']}开始并于"
                f"{episode['detected_at_local']}确认。"
            ),
        ),
        "ipv4_decline": _assessment(
            decline_outcome,
            _known(
                round(decline, 5),
                "legacy_ipv4_equivalent_baseline_fraction_decline",
            ),
            evidence=(db_ref,),
            limitations=common_proxy_limit,
            rationale=(
                f"旧等价值基线至{trough['observed_at_local']}低谷下降"
                f"{decline * 100:.3f}%；与报告{float(reported_decline) * 100:.3f}%"
                f"按±{decline_tolerance * 100:.1f}个百分点近似容差比较，"
                f"数值接近={str(decline_is_numerically_close).lower()}；"
                "但冻结策略要求同快照去重IPv4地址并集，当前只有旧等价值，"
                "因此口径不同。"
            ),
        ),
        "recovery_state": _assessment(
            recovery_outcome,
            _known(
                {
                    "state_at_observation_end": episode[
                        "recovery_state_proxy"
                    ],
                    "partial_recovery_candidate": episode[
                        "partial_recovery_candidate"
                    ],
                    "full_recovery_candidate": episode[
                        "full_recovery_candidate"
                    ],
                },
                "database_metric_proxy_recovery_state",
            ),
            evidence=(db_ref,),
            limitations=common_proxy_limit,
            rationale=(
                f"观察截止状态为{episode['recovery_state_proxy']}；"
                f"{partial_description}；{full_description}。"
            ),
        ),
        "report_affected_asn_ratio": _assessment(
            "not_computable",
            _unknown("缺少同一cohort与同一时点的逐ASN状态"),
            evidence=(db_ref, source_ref),
            limitations=affected_unknown,
            rationale="199/595不能由可变旧事实或稀疏活动下界替代复算。",
            unknown_rating="unverifiable",
        ),
        "report_visibility_class_counts": _assessment(
            "not_computable",
            _unknown("缺少按地址族冻结的逐ASN基线与当前可见前缀集合"),
            evidence=(db_ref, source_ref),
            limitations=affected_unknown,
            rationale="73/126需要互斥的逐地址族同快照分类，生命周期峰值97/79不具同一语义。",
            unknown_rating="unverifiable",
        ),
        "database_affected_asn_ratio": _assessment(
            database_ratio_outcome,
            _known(
                recomputed_database_ratio,
                "legacy_database_asn_ratio",
            ),
            evidence=(db_ref, source_ref),
            limitations=(
                "这里只确认旧数据库source fact为176/556且内部一致，不把它升级为同快照网络真值。",
            ),
            rationale=(
                "冻结source fact、国家集合与锚点活跃AS集合闭合为"
                f"{recomputed_database_ratio['affected']}/"
                f"{recomputed_database_ratio['total']}。"
            ),
        ),
        "active_withdrawal_intent": _assessment(
            "not_computable",
            _unknown("RRC25消息观测不能证明主动撤回意图"),
            evidence=(route_ref, raw_ref),
            limitations=(
                "定向报文只证明消息级ANNOUNCE/WITHDRAW观测，不证明行为主体与意图。",
            ),
            rationale="消息证据可定位报文，但主动意图仍只能保留为假设。",
            unknown_rating="hypothesis_only",
        ),
        "physical_cut": _assessment(
            "not_computable",
            _unknown("缺少物理链路遥测"),
            evidence=(limitation_ref,),
            limitations=("RRC25不包含物理线路状态。",),
            rationale="路由变化不能区分物理断路和其他机制。",
            unknown_rating="hypothesis_only",
        ),
        "bgp_session_closed": _assessment(
            "not_computable",
            _unknown("缺少结构化且连续的会话状态证据"),
            evidence=(route_ref, raw_ref),
            limitations=("消息级路由记录不能证明全局BGP会话关闭。",),
            rationale="定向记录没有形成会话状态序列。",
            unknown_rating="unverifiable",
        ),
        "traffic_impact": _assessment(
            "not_computable",
            _unknown("缺少业务流量遥测"),
            evidence=(limitation_ref,),
            limitations=("路由可见性不能替代NetFlow或业务流量监测。",),
            rationale="当前输入不能计算报告的百分之三点五流量主张。",
            unknown_rating="unverifiable",
        ),
        "government_intent": _assessment(
            "not_computable",
            _unknown("缺少决策主体与政治意图证据"),
            evidence=(limitation_ref,),
            limitations=("RRC25单一观测源不包含政府决策或意图证据。",),
            rationale="时间先后和路由变化均不能证明政府意图。",
            unknown_rating="hypothesis_only",
        ),
    }


def _render_report(
    proxy: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    targeted: Mapping[str, Any],
) -> str:
    episode = proxy["episodes"][0]
    trough = episode["trough"]
    partial = episode["partial_recovery_candidate"]
    summary = reconciliation["summary"]
    lines = [
        "# RRC25 伊朗事件数据库指标代理最终化报告",
        "",
        "## 结论",
        "",
        "文件流程已完成，但研究验收状态为 `not_accepted`。本报告只使用旧数据库",
        "`ipv4_address_equivalent` 等价值形成 metric-only proxy；它不是去重 IPv4",
        "地址并集，也不是完整路由状态，不能替代正式 Episode/Wave 合同。",
        "",
        "## 代理时间线",
        "",
        f"- 候选开始：`{episode['onset_at_local']}`（`{episode['onset_at_utc']}`）。",
        f"- 连续两槽确认：`{episode['detected_at_local']}`（`{episode['detected_at_utc']}`）。",
        (
            f"- 低谷：`{trough['observed_at_local']}`，旧 IPv4 地址等价值 "
            f"`{trough['ipv4_address_equivalent']:,}`。"
        ),
        (
            f"- 截止状态：`{episode['recovery_state_proxy']}`；完全恢复候选为 "
            f"`{episode['full_recovery_candidate']}`。"
        ),
    ]
    if isinstance(partial, Mapping):
        lines.extend(
            [
                (
                    f"- 首次 99% 候选从 `{partial['start_at_local']}` 开始，"
                    f"在 `{partial['confirmed_at_local']}` 完成六槽确认；"
                    f"后续反复：`{str(partial['later_relapse_observed']).lower()}`。"
                )
            ]
        )
    lines.extend(
        [
            f"- proxy wave 数：`{episode['wave_count']}`；波次关系均为非因果观测。",
            "",
            "## 报告主张对账",
            "",
            (
                f"评级汇总：confirmed `{summary['confirmed']}`，revised "
                f"`{summary['revised']}`，unverifiable "
                f"`{summary['unverifiable']}`，hypothesis_only "
                f"`{summary['hypothesis_only']}`。"
            ),
            "",
            "| 主张 | 评级 | 复算状态 |",
            "| --- | --- | --- |",
        ]
    )
    for claim in reconciliation["claims"]:
        value = claim["recomputed_value"]
        display = (
            value["value"]
            if value["value_state"] == "recomputed"
            else value["missing_reason"]
        )
        lines.append(
            f"| `{claim['claim_type']}` | `{claim['rating']}` | "
            f"{str(display).replace('|', '／')} |"
        )
    message = targeted["message_evidence"]
    lines.extend(
        [
            "",
            "## 定向消息证据",
            "",
            (
                f"已验证 RouteEvent `{message['route_event_count']}` 条、raw ref "
                f"`{message['raw_record_ref_count']}` 条及其一一引用。它们仅作为"
                "消息证据，未映射到状态，也未用于计算恢复或传播人口。"
            ),
            "",
            "| ASN / Prefix | A / W | origin 匹配 | VP | 解释 |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
    )
    for entity in message["entity_observations"]:
        lines.append(
            "| `AS{} / {}` | `{} / {}` | `{}` | `{}` | {} |".format(
                entity["asn"],
                entity["prefix"],
                entity["announce_count"],
                entity["withdraw_count"],
                entity["announce_target_origin_match_count"],
                entity["distinct_vp_count"],
                str(entity["interpretation_zh"]).replace("|", "／"),
            )
        )
    lines.extend(
        [
            "",
            "## 未通过研究验收的原因",
            "",
            "- 缺少去重 IPv4 地址并集和逐槽受损 ASN 比例。",
            "- 缺少同快照 cohort、前缀集合和 VP 连续性。",
            "- 未读取 RIB、未 seed、未执行状态回放。",
            "- `199/595` 与 `73/126` 仍不可验证，前兆及政治原因仍不得下因果结论。",
            "",
        ]
    )
    return "\n".join(lines)


def _create_only(path: Path, payload: bytes) -> None:
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
        if current.exists() or current.is_symlink():
            if current.is_symlink():
                raise ProxyFinalizeError(f"输出路径祖先不能是符号链接：{current}")


def _publish(
    output_directory: str | Path,
    *,
    report: str,
    proxy: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    quality: Mapping[str, Any],
    input_bindings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    output = Path(output_directory).expanduser().resolve(strict=False)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output}")
    parent = output.parent
    _assert_no_symlink_ancestors(parent)
    parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    _assert_no_symlink_ancestors(parent)
    parent_meta = parent.lstat()
    if stat.S_ISLNK(parent_meta.st_mode) or not stat.S_ISDIR(parent_meta.st_mode):
        raise ProxyFinalizeError("输出父目录必须是非符号链接目录")
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging.", dir=str(parent))
    )
    try:
        payloads = {
            "数据库指标代理分析报告.md": report.encode("utf-8"),
            "proxy-analysis.json": _pretty_json_bytes(proxy),
            "reconciliation-result.json": _pretty_json_bytes(reconciliation),
            "QUALITY.json": _pretty_json_bytes(quality),
        }
        for name in OUTPUT_CONTENT_FILES:
            _create_only(staging / name, payloads[name])
        contents = {}
        for name in OUTPUT_CONTENT_FILES:
            digest, size = _hash_file(staging / name)
            contents[name] = {"sha256": digest, "size_bytes": size}
        semantic_fingerprint = _sha256_bytes(
            _canonical_bytes(
                {
                    "schema": "rrc25-iran-db-proxy-finalization-semantic/v1",
                    "inputs": list(input_bindings),
                    "contents": contents,
                    "workflow_state": "completed",
                    "acceptance_state": "not_accepted",
                }
            )
        )
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "workflow_state": "completed",
            "acceptance_state": "not_accepted",
            "run_id": reconciliation["run_id"],
            "incident_ref": EXPECTED_INCIDENT_REF,
            "input_bindings": [dict(row) for row in input_bindings],
            "contents": contents,
            "semantic_fingerprint_sha256": semantic_fingerprint,
            "non_actions": {
                "database_connections": 0,
                "database_writes": 0,
                "mrt_files_opened": 0,
                "rib_files_opened": 0,
                "seed_performed": False,
                "state_replay_performed": False,
            },
        }
        _create_only(staging / "MANIFEST.json", _pretty_json_bytes(manifest))
        checksum_rows = []
        for name in OUTPUT_CHECKSUM_FILES:
            digest, _ = _hash_file(staging / name)
            checksum_rows.append(f"{digest}  {name}")
        _create_only(
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
    return {
        "output_directory": str(output),
        "workflow_state": "completed",
        "acceptance_state": "not_accepted",
        "run_id": reconciliation["run_id"],
        "semantic_fingerprint_sha256": manifest[
            "semantic_fingerprint_sha256"
        ],
        "files": [
            {
                "name": name,
                "sha256": _hash_file(
                    output / name,
                    maximum_bytes=MAX_JSON_BYTES,
                )[0],
                "size_bytes": (output / name).stat().st_size,
            }
            for name in OUTPUT_CHECKSUM_FILES + ("SHA256SUMS",)
        ],
    }


def finalize(
    *,
    db_first_json: str | Path,
    db_first_sha256: str,
    targeted_raw_directory: str | Path,
    targeted_manifest_sha256: str,
    profile_path: str | Path,
    profile_sha256: str,
    claim_inventory_path: str | Path,
    claim_inventory_sha256: str,
    source_fact_path: str | Path,
    source_fact_sha256: str,
    output_directory: str | Path,
) -> dict[str, Any]:
    """校验全部冻结输入并原子发布 create-only 最终化目录。"""

    _assert_output_disjoint_from_inputs(
        output_directory,
        (
            db_first_json,
            Path(db_first_json).expanduser().parent,
            targeted_raw_directory,
            profile_path,
            claim_inventory_path,
            source_fact_path,
        ),
    )
    db_path = _regular_file(db_first_json, "DB-first JSON")
    db_first, db_ref = _load_hashed_json(
        db_path, db_first_sha256, "db_first_json"
    )
    profile_raw, profile_ref = _load_hashed_json(
        profile_path, profile_sha256, "research_profile"
    )
    profile = validate_research_profile(profile_raw)
    inventory, inventory_ref = _load_hashed_json(
        claim_inventory_path,
        claim_inventory_sha256,
        "claim_inventory",
    )
    source_fact, source_fact_ref = _load_hashed_json(
        source_fact_path,
        source_fact_sha256,
        "source_fact",
    )
    frozen_fact = load_frozen_incident_fact(source_fact)
    inventory_scope = inventory.get("scope")
    if (
        profile.get("study_id") != EXPECTED_STUDY_ID
        or profile.get("collector_id") != "rrc25"
        or profile.get("country_code") != "IR"
        or inventory.get("study_id") != EXPECTED_STUDY_ID
        or inventory.get("incident_ref") != EXPECTED_INCIDENT_REF
        or not isinstance(inventory_scope, Mapping)
        or inventory_scope.get("collector_id") != "rrc25"
        or inventory_scope.get("country_code") != "IR"
        or inventory_scope.get("evidence_scope") != "rrc25_only"
    ):
        raise ProxyFinalizeError(
            "profile 与主张清单未绑定固定伊朗 RRC25 研究身份"
        )
    points, _ = _verify_db_first(db_first, db_ref, db_path, profile)
    if (
        frozen_fact.legacy_affected_asn_count
        != db_first["fact"]["affected_asn_count"]
        or frozen_fact.legacy_total_asn_count
        != db_first["fact"]["total_asn_count"]
        or set(frozen_fact.affected_asns)
        != set(db_first["fact"]["affected_asns"])
        or db_first["fact"]["incident_ref"] != EXPECTED_INCIDENT_REF
    ):
        raise ProxyFinalizeError("冻结 source fact 与 DB-first 旧事实不一致")
    targeted = _verify_targeted_package(
        targeted_raw_directory,
        targeted_manifest_sha256,
        expected_db_first_sha256=db_ref["sha256"],
        expected_db_first_size=db_ref["size_bytes"],
        profile=profile,
    )
    raw_request = db_first.get("minimal_raw_request")
    requested_entities = (
        raw_request.get("representative_entities")
        if isinstance(raw_request, Mapping)
        else None
    )
    requested_slots = (
        raw_request.get("update_slots")
        if isinstance(raw_request, Mapping)
        else None
    )
    targeted_pairs = targeted["plan"].get("requested_pairs")
    if (
        not isinstance(requested_entities, list)
        or not isinstance(requested_slots, list)
        or not isinstance(targeted_pairs, list)
    ):
        raise ProxyFinalizeError("DB-first 最小 raw 请求或 targeted pair 缺失")
    db_pair_identities = []
    for index, entity in enumerate(requested_entities):
        if not isinstance(entity, Mapping):
            raise ProxyFinalizeError(
                f"DB-first representative_entities[{index}] 必须是对象"
            )
        db_pair_identities.append(
            (
                str(entity.get("asn")),
                entity.get("selected_prefix"),
                entity.get("preferred_ip_family"),
            )
        )
    targeted_pair_identities = []
    for index, pair in enumerate(targeted_pairs):
        if not isinstance(pair, Mapping) or pair.get("pair_index") != index:
            raise ProxyFinalizeError(
                f"targeted requested_pairs[{index}] 身份或顺序非法"
            )
        targeted_pair_identities.append(
            (
                str(pair.get("asn")),
                pair.get("prefix"),
                pair.get("ip_family"),
            )
        )
    db_slots = [
        slot.get("utc") if isinstance(slot, Mapping) else None
        for slot in requested_slots
    ]
    if (
        db_pair_identities != targeted_pair_identities
        or db_slots != targeted["plan"].get("requested_update_slots")
    ):
        raise ProxyFinalizeError(
            "targeted-raw 的实体或槽集合未与 DB-first 最小请求闭合"
        )
    targeted_binding = {
        "logical_name": "targeted_raw_package",
        "manifest_sha256": targeted["files"]["MANIFEST.json"]["sha256"],
        "sha256sums_sha256": targeted["files"]["SHA256SUMS"]["sha256"],
        "route_events_sha256": targeted["files"][
            "route-events.jsonl.gz"
        ]["sha256"],
        "raw_record_refs_sha256": targeted["files"][
            "raw-record-refs.jsonl.gz"
        ]["sha256"],
    }
    input_bindings = [
        dict(db_ref),
        dict(profile_ref),
        dict(inventory_ref),
        dict(source_fact_ref),
        targeted_binding,
    ]
    input_bindings.sort(key=lambda row: row["logical_name"])
    proxy = build_proxy_analysis(
        db_first,
        points,
        profile,
        db_first_sha256=db_ref["sha256"],
        targeted_message_evidence=targeted["message_evidence"],
        input_bindings=input_bindings,
    )
    run_id = _stable_id(
        "research_run_v1_",
        {
            "schema": MANIFEST_SCHEMA_VERSION,
            "inputs": input_bindings,
            "proxy_semantics": PROXY_SCHEMA_VERSION,
            "reconciliation_decision": RECONCILIATION_DECISION_VERSION,
        },
    )
    reconciliation = build_reconciliation_result(
        run_id=run_id,
        claim_inventory=inventory,
        assessments=_build_assessments(proxy, db_first, inventory),
        evidence_registry=_evidence_registry(
            db_ref=db_ref,
            profile_ref=profile_ref,
            inventory_ref=inventory_ref,
            source_fact_ref=source_fact_ref,
            targeted=targeted,
        ),
    )
    expected_summary = {
        "confirmed": 1,
        "revised": 3,
        "unverifiable": 4,
        "hypothesis_only": 3,
    }
    if reconciliation["summary"] != expected_summary:
        raise ProxyFinalizeError("11 项对账评级数量偏离冻结决策")
    if (
        reconciliation.get("run_id") != run_id
        or reconciliation.get("incident_ref") != EXPECTED_INCIDENT_REF
        or len(reconciliation.get("claims", ())) != 11
    ):
        raise ProxyFinalizeError("对账输出未绑定固定运行与 Incident")
    quality = {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "workflow_state": "completed",
        "acceptance_state": "not_accepted",
        "run_id": run_id,
        "incident_ref": EXPECTED_INCIDENT_REF,
        "checks": {
            "all_input_hashes_verified": True,
            "db_first_content_fingerprint_verified": True,
            "db_first_curve_complete": True,
            "profile_validated": True,
            "claim_inventory_consumed_by_strict_reconciliation": True,
            "source_fact_validated_and_matched": True,
            "targeted_five_file_package_verified": True,
            "route_event_raw_ref_one_to_one": True,
            "reconciliation_exactly_eleven_claims": (
                len(reconciliation["claims"]) == 11
            ),
            "rating_summary_matches_frozen_decision": True,
            "deterministic_output_fields_only": True,
        },
        "message_evidence": targeted["message_evidence"],
        "blocking_reasons": [
            "legacy_ipv4_equivalent_is_not_deduplicated_address_union",
            "same_snapshot_damaged_asn_population_absent",
            "vp_and_prefix_state_continuity_absent",
            "targeted_raw_is_message_observation_only",
            "rib_seed_and_state_replay_not_performed",
        ],
        "non_actions": {
            "database_connections": 0,
            "database_writes": 0,
            "mrt_files_opened": 0,
            "rib_files_opened": 0,
            "seed_performed": False,
            "state_replay_performed": False,
            "route_event_to_state_mapping_performed": False,
        },
    }
    report = _render_report(proxy, reconciliation, targeted)
    return _publish(
        output_directory,
        report=report,
        proxy=proxy,
        reconciliation=reconciliation,
        quality=quality,
        input_bindings=input_bindings,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="以冻结 DB-first 与定向消息证据生成非验收的 metric proxy 最终化包"
    )
    parser.add_argument("--db-first-json", required=True)
    parser.add_argument("--db-first-sha256", required=True)
    parser.add_argument("--targeted-raw-directory", required=True)
    parser.add_argument("--targeted-manifest-sha256", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--profile-sha256", required=True)
    parser.add_argument("--claim-inventory", required=True)
    parser.add_argument("--claim-inventory-sha256", required=True)
    parser.add_argument("--source-fact", required=True)
    parser.add_argument("--source-fact-sha256", required=True)
    parser.add_argument("--output-directory", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    result = finalize(
        db_first_json=arguments.db_first_json,
        db_first_sha256=arguments.db_first_sha256,
        targeted_raw_directory=arguments.targeted_raw_directory,
        targeted_manifest_sha256=arguments.targeted_manifest_sha256,
        profile_path=arguments.profile,
        profile_sha256=arguments.profile_sha256,
        claim_inventory_path=arguments.claim_inventory,
        claim_inventory_sha256=arguments.claim_inventory_sha256,
        source_fact_path=arguments.source_fact,
        source_fact_sha256=arguments.source_fact_sha256,
        output_directory=arguments.output_directory,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ProxyFinalizeError, FileExistsError) as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(2)
