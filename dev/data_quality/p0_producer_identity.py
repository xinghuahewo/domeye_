#!/usr/bin/env python3
"""校验 P0 固定数据档的 R0 生产者身份机器清单。

本工具只读取仓库内 JSON，不连接数据库、不访问远端，也不修改任何数据。
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = (
    ROOT / "docs" / "data" / "P0数据基础R轨R0生产者身份清单.json"
)
SCHEMA_VERSION = "domeye_p0_producer_identity/v1"
DATASET_ID = "feb-mar-2026"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
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
EXPECTED_TABLES = frozenset(
    ["feature_country"]
    + [
        f"{family}_{month}"
        for month in ("202602", "202603")
        for family in MONTHLY_FAMILIES
    ]
)
IDENTITY_STATUSES = frozenset({"resolved", "mixed", "unrecoverable"})


class ProducerIdentityError(RuntimeError):
    """生产者身份清单不满足 RFA-01。"""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProducerIdentityError(f"无法读取生产者身份清单：{path}") from error
    if not isinstance(value, dict):
        raise ProducerIdentityError("生产者身份清单顶层必须是对象")
    return value


def _require_nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProducerIdentityError(f"{label}必须是非空字符串")
    return value


def _require_timestamp(value: Any, label: str) -> datetime:
    text = _require_nonempty_string(value, label)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ProducerIdentityError(f"{label}不是有效 ISO 8601 时间") from error
    if parsed.tzinfo is None:
        raise ProducerIdentityError(f"{label}必须携带时区")
    return parsed


def _validate_evidence(payload: dict[str, Any]) -> set[str]:
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ProducerIdentityError("evidence 必须是非空数组")
    ids: set[str] = set()
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise ProducerIdentityError(f"evidence[{index}] 必须是对象")
        evidence_id = _require_nonempty_string(
            item.get("id"), f"evidence[{index}].id"
        )
        if evidence_id in ids:
            raise ProducerIdentityError(f"证据 id 重复：{evidence_id}")
        ids.add(evidence_id)
        _require_nonempty_string(item.get("path"), f"evidence[{index}].path")
        sha256 = item.get("sha256")
        if not isinstance(sha256, str) or SHA256_RE.fullmatch(sha256) is None:
            raise ProducerIdentityError(
                f"evidence[{index}].sha256 不是有效 SHA256"
            )
        prefix_bytes = item.get("prefix_bytes")
        if prefix_bytes is not None and (
            not isinstance(prefix_bytes, int)
            or isinstance(prefix_bytes, bool)
            or prefix_bytes <= 0
        ):
            raise ProducerIdentityError(
                f"evidence[{index}].prefix_bytes 必须是正整数"
            )
    return ids


def _validate_identity(
    identity: Any,
    *,
    label: str,
    reason_codes: set[str],
) -> None:
    if not isinstance(identity, dict):
        raise ProducerIdentityError(f"{label}必须是对象")
    status = identity.get("status")
    if status == "resolved":
        git_sha = identity.get("git_sha")
        if not isinstance(git_sha, str) or GIT_SHA_RE.fullmatch(git_sha) is None:
            raise ProducerIdentityError(f"{label}.git_sha 不是完整 Git SHA")
        if identity.get("version_scheme") != "git_commit_content":
            raise ProducerIdentityError(
                f"{label}.version_scheme 必须为 git_commit_content"
            )
        return
    if status == "unrecoverable":
        reason_code = identity.get("reason_code")
        if reason_code not in reason_codes:
            raise ProducerIdentityError(
                f"{label}.reason_code 未在 reason_codes 中登记"
            )
        return
    raise ProducerIdentityError(
        f"{label}.status 只能是 resolved 或 unrecoverable"
    )


def _validate_pid_list(value: Any, label: str) -> None:
    if not isinstance(value, list) or not value:
        raise ProducerIdentityError(f"{label}必须是非空数组")
    if any(
        not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
        for pid in value
    ):
        raise ProducerIdentityError(f"{label}只能包含正整数 PID")
    if len(value) != len(set(value)):
        raise ProducerIdentityError(f"{label}存在重复 PID")


def _validate_group(
    group_id: str,
    group: Any,
    *,
    evidence_ids: set[str],
    reason_codes: set[str],
) -> str:
    if not isinstance(group, dict):
        raise ProducerIdentityError(f"producer_groups.{group_id} 必须是对象")
    status = group.get("identity_status")
    if status not in IDENTITY_STATUSES:
        raise ProducerIdentityError(
            f"producer_groups.{group_id}.identity_status 无效"
        )
    refs = group.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise ProducerIdentityError(
            f"producer_groups.{group_id}.evidence_refs 必须是非空数组"
        )
    unknown_refs = sorted(set(refs) - evidence_ids)
    if unknown_refs:
        raise ProducerIdentityError(
            f"producer_groups.{group_id} 引用了未知证据：{unknown_refs}"
        )
    segments = group.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ProducerIdentityError(
            f"producer_groups.{group_id}.segments 必须是非空数组"
        )
    observed_statuses: set[str] = set()
    producer_pids: set[int] = set()
    for index, segment in enumerate(segments):
        label = f"producer_groups.{group_id}.segments[{index}]"
        if not isinstance(segment, dict):
            raise ProducerIdentityError(f"{label}必须是对象")
        start = _require_timestamp(
            segment.get("observed_start"), f"{label}.observed_start"
        )
        end = _require_timestamp(
            segment.get("observed_end_inclusive"),
            f"{label}.observed_end_inclusive",
        )
        if end < start:
            raise ProducerIdentityError(f"{label}结束时间早于开始时间")
        _validate_pid_list(segment.get("producer_pids"), f"{label}.producer_pids")
        duplicated_pids = producer_pids.intersection(segment["producer_pids"])
        if duplicated_pids:
            raise ProducerIdentityError(
                f"producer_groups.{group_id} 的分段重复登记 PID："
                f"{sorted(duplicated_pids)}"
            )
        producer_pids.update(segment["producer_pids"])
        _validate_identity(
            segment.get("code_identity"),
            label=f"{label}.code_identity",
            reason_codes=reason_codes,
        )
        _validate_identity(
            segment.get("algorithm_version"),
            label=f"{label}.algorithm_version",
            reason_codes=reason_codes,
        )
        code_status = segment["code_identity"]["status"]
        algorithm_status = segment["algorithm_version"]["status"]
        if code_status != algorithm_status:
            raise ProducerIdentityError(
                f"{label}的代码身份和算法版本状态不一致"
            )
        if code_status == "resolved" and (
            segment["code_identity"]["git_sha"]
            != segment["algorithm_version"]["git_sha"]
        ):
            raise ProducerIdentityError(
                f"{label}的代码身份和算法版本 Git SHA 不一致"
            )
        observed_statuses.add(code_status)
    excluded = group.get("excluded_non_producer_processes", [])
    if not isinstance(excluded, list):
        raise ProducerIdentityError(
            f"producer_groups.{group_id}.excluded_non_producer_processes 必须是数组"
        )
    excluded_pids: set[int] = set()
    for index, item in enumerate(excluded):
        label = (
            f"producer_groups.{group_id}.excluded_non_producer_processes[{index}]"
        )
        if not isinstance(item, dict):
            raise ProducerIdentityError(f"{label}必须是对象")
        _validate_pid_list(item.get("producer_pids"), f"{label}.producer_pids")
        duplicated_pids = excluded_pids.intersection(item["producer_pids"])
        if duplicated_pids:
            raise ProducerIdentityError(
                f"{label}重复登记非生产者 PID：{sorted(duplicated_pids)}"
            )
        excluded_pids.update(item["producer_pids"])
        _require_nonempty_string(item.get("reason"), f"{label}.reason")
    overlap = producer_pids.intersection(excluded_pids)
    if overlap:
        raise ProducerIdentityError(
            f"producer_groups.{group_id} 同时把 PID 登记为生产者和非生产者："
            f"{sorted(overlap)}"
        )
    expected_status = (
        "mixed"
        if observed_statuses == {"resolved", "unrecoverable"}
        else next(iter(observed_statuses))
    )
    if status != expected_status:
        raise ProducerIdentityError(
            f"producer_groups.{group_id}.identity_status 与分段状态不一致"
        )
    return status


def validate_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    payload = _load_json(path)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ProducerIdentityError(
            f"schema_version 必须为 {SCHEMA_VERSION}"
        )
    dataset = payload.get("dataset")
    if not isinstance(dataset, dict) or dataset.get("id") != DATASET_ID:
        raise ProducerIdentityError(f"dataset.id 必须为 {DATASET_ID}")
    _require_timestamp(payload.get("captured_at"), "captured_at")

    reason_code_payload = payload.get("reason_codes")
    if not isinstance(reason_code_payload, dict) or not reason_code_payload:
        raise ProducerIdentityError("reason_codes 必须是非空对象")
    reason_codes = set(reason_code_payload)
    for reason_code, description in reason_code_payload.items():
        _require_nonempty_string(reason_code, "reason_codes 键")
        _require_nonempty_string(
            description, f"reason_codes.{reason_code}"
        )

    evidence_ids = _validate_evidence(payload)
    groups = payload.get("producer_groups")
    if not isinstance(groups, dict) or not groups:
        raise ProducerIdentityError("producer_groups 必须是非空对象")
    group_statuses = {
        group_id: _validate_group(
            group_id,
            group,
            evidence_ids=evidence_ids,
            reason_codes=reason_codes,
        )
        for group_id, group in groups.items()
    }

    tables = payload.get("tables")
    if not isinstance(tables, list):
        raise ProducerIdentityError("tables 必须是数组")
    names: list[str] = []
    for index, item in enumerate(tables):
        label = f"tables[{index}]"
        if not isinstance(item, dict):
            raise ProducerIdentityError(f"{label}必须是对象")
        name = _require_nonempty_string(item.get("table"), f"{label}.table")
        names.append(name)
        start = _require_timestamp(
            item.get("data_start"), f"{label}.data_start"
        )
        end = _require_timestamp(
            item.get("data_end_exclusive"), f"{label}.data_end_exclusive"
        )
        if end <= start:
            raise ProducerIdentityError(f"{label}数据区间无效")
        refs = item.get("producer_group_refs")
        if not isinstance(refs, list) or not refs:
            raise ProducerIdentityError(
                f"{label}.producer_group_refs 必须是非空数组"
            )
        unknown_groups = sorted(set(refs) - set(groups))
        if unknown_groups:
            raise ProducerIdentityError(
                f"{label}引用了未知生产者组：{unknown_groups}"
            )
        derived_statuses = {group_statuses[ref] for ref in refs}
        expected_status = (
            "mixed"
            if "mixed" in derived_statuses or len(derived_statuses) > 1
            else next(iter(derived_statuses))
        )
        if item.get("identity_status") != expected_status:
            raise ProducerIdentityError(
                f"{label}.identity_status 与生产者组状态不一致"
            )
    if len(names) != len(set(names)):
        raise ProducerIdentityError("tables 存在重复表名")
    actual_tables = set(names)
    missing = sorted(EXPECTED_TABLES - actual_tables)
    extra = sorted(actual_tables - EXPECTED_TABLES)
    if missing or extra:
        raise ProducerIdentityError(
            f"37 张表集合不一致：缺少={missing}，多出={extra}"
        )
    if len(tables) != 37:
        raise ProducerIdentityError(f"tables 必须恰好包含 37 条，当前为 {len(tables)}")

    return {
        "schema_version": "domeye_p0_producer_identity_validation/v1",
        "manifest": str(path),
        "dataset_id": DATASET_ID,
        "table_count": len(tables),
        "producer_group_count": len(groups),
        "evidence_count": len(evidence_ids),
        "blank_identity_count": 0,
        "unregistered_table_count": 0,
        "verdict": "passed",
    }


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="校验 P0 R0 生产者身份机器清单。",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="生产者身份 JSON 清单路径。",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    try:
        result = validate_manifest(arguments.manifest)
    except ProducerIdentityError as error:
        json.dump(
            {
                "schema_version": "domeye_p0_producer_identity_validation/v1",
                "manifest": str(arguments.manifest),
                "verdict": "failed",
                "error": str(error),
            },
            sys.stdout,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
        return 1
    json.dump(
        result,
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
