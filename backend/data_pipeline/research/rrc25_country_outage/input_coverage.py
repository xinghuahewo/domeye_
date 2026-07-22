"""伊朗 RRC25 研究窗口的制品覆盖与完整性对账。

调用方必须先用 :func:`verify_artifact_manifest` 对原始目录完成全量重扫、
SHA256、压缩流 EOF/CRC 和读取期间文件身份检查，再把不可变 manifest、验证
摘要和角色化 selection 传入本模块。本模块只做纯数据对账，不扫描目录、不读
MRT、不写文件，也不会把缺槽补成可用或零值。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .input_resolver import ResearchInputError, canonical_json


UTC = timezone.utc
COVERAGE_SCHEMA_VERSION = "rrc25-country-outage-input-coverage/v1"
COVERAGE_FINGERPRINT_SCHEMA = "rrc25_country_outage_input_coverage_fingerprint_v1"
UPDATE_INTERVAL = timedelta(minutes=5)
RIB_INTERVAL = timedelta(hours=8)


class ResearchCoverageError(ResearchInputError):
    """已验证 manifest、selection 与事件窗口覆盖无法闭合。"""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchCoverageError(f"{field} 必须是对象")
    return value


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ResearchCoverageError(f"{field} 必须是 UTC Z 时间")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise ResearchCoverageError(f"{field} 不是合法 UTC 时间") from error


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slots(start: datetime, end: datetime, interval: timedelta) -> Tuple[str, ...]:
    if start >= end or (end - start) % timedelta(minutes=5):
        raise ResearchCoverageError("事件窗口必须是按五分钟对齐的非空半开区间")
    values: List[str] = []
    cursor = start
    while cursor < end:
        values.append(_utc_text(cursor))
        cursor += interval
    return tuple(values)


def _compress_states(
    expected_slots: Sequence[str],
    states: Mapping[str, Tuple[str, str | None]],
    interval: timedelta,
) -> List[Dict[str, Any]]:
    """把连续同状态槽压缩为半开区间，避免为完整窗口复制 1,928 行。"""

    ranges: List[Dict[str, Any]] = []
    if not expected_slots:
        return ranges
    start = previous = _utc(expected_slots[0], "expected slot")
    state, reason = states[expected_slots[0]]
    count = 1
    for text in expected_slots[1:]:
        current = _utc(text, "expected slot")
        current_state, current_reason = states[text]
        if (
            current == previous + interval
            and current_state == state
            and current_reason == reason
        ):
            previous = current
            count += 1
            continue
        ranges.append(
            {
                "start_utc": _utc_text(start),
                "end_exclusive_utc": _utc_text(previous + interval),
                "slot_count": count,
                "value_state": state,
                "missing_reason": reason,
            }
        )
        start = previous = current
        state, reason = current_state, current_reason
        count = 1
    ranges.append(
        {
            "start_utc": _utc_text(start),
            "end_exclusive_utc": _utc_text(previous + interval),
            "slot_count": count,
            "value_state": state,
            "missing_reason": reason,
        }
    )
    return ranges


def _role_rows(roles: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for name in (
        "state_seed_rib",
        "baseline_reference_rib",
        "catch_up_updates",
        "analysis_updates",
        "analysis_ribs",
    ):
        value = roles.get(name)
        if value is None:
            continue
        rows = value if isinstance(value, list) else [value]
        if any(not isinstance(row, Mapping) for row in rows):
            raise ResearchCoverageError(f"selection.roles.{name} 必须是制品对象或数组")
        yield from rows


def _analysis_coverage(
    *,
    artifact_type: str,
    expected_slots: Sequence[str],
    interval: timedelta,
    available: Mapping[Tuple[str, str], Mapping[str, Any]],
    invalid: Mapping[Tuple[str, str], Mapping[str, Any]],
) -> Dict[str, Any]:
    states: Dict[str, Tuple[str, str | None]] = {}
    available_bytes = 0
    for slot in expected_slots:
        key = (artifact_type, slot)
        if key in available and key in invalid:
            raise ResearchCoverageError("同一槽不能同时是可用制品和隔离制品")
        if key in available:
            states[slot] = ("available", None)
            available_bytes += available[key]["size_bytes"]
        elif key in invalid:
            reason = invalid[key].get("missing_reason")
            if not isinstance(reason, str) or not reason:
                raise ResearchCoverageError("隔离制品缺少 missing_reason")
            states[slot] = ("parse_failed", reason)
        else:
            states[slot] = ("source_unavailable", "artifact_slot_missing")

    counts = {
        state: sum(1 for value_state, _reason in states.values() if value_state == state)
        for state in ("available", "parse_failed", "source_unavailable")
    }
    return {
        "artifact_type": artifact_type,
        "expected_count": len(expected_slots),
        "available_count": counts["available"],
        "parse_failed_count": counts["parse_failed"],
        "source_unavailable_count": counts["source_unavailable"],
        "available_size_bytes": available_bytes,
        "coverage_state": "complete"
        if counts["available"] == len(expected_slots)
        else "incomplete",
        "slot_ranges": _compress_states(expected_slots, states, interval),
    }


def reconcile_event_window_coverage(
    artifact_manifest: Mapping[str, Any],
    manifest_verification: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> Dict[str, Any]:
    """对账事件窗口所需 RIB/UPDATE 与已验证父 manifest。

    返回内容不含运行时钟和绝对路径，可直接进入研究运行 manifest。任何身份、
    角色或计数不一致都抛错；真实源缺口则保留为 ``incomplete`` 结果。
    """

    manifest = _mapping(artifact_manifest, "artifact_manifest")
    verification = _mapping(manifest_verification, "manifest_verification")
    selected = _mapping(selection, "selection")
    fingerprint = manifest.get("manifest_fingerprint_sha256")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("manifest_kind") != "mrt_artifact_manifest"
        or not isinstance(fingerprint, str)
        or len(fingerprint) != 64
    ):
        raise ResearchCoverageError("父 artifact manifest 身份非法")
    artifacts = manifest.get("artifacts")
    invalid_rows = manifest.get("invalid_in_window")
    if not isinstance(artifacts, list) or not isinstance(invalid_rows, list):
        raise ResearchCoverageError("父 manifest 必须包含 artifacts/invalid_in_window 数组")
    if (
        verification.get("verified") is not True
        or verification.get("manifest_fingerprint_sha256") != fingerprint
        or verification.get("artifact_count") != len(artifacts)
        or verification.get("invalid_in_window_count") != len(invalid_rows)
    ):
        raise ResearchCoverageError("父 manifest 验证摘要不能闭合")
    scan_policy = _mapping(manifest.get("scan_policy"), "manifest.scan_policy")
    if (
        scan_policy.get("compression_envelope_validation")
        != "full_stream_to_eof_crc_or_equivalent"
        or scan_policy.get("invalid_in_window")
        != "full_hash_quarantine_exclude_from_available_slots"
    ):
        raise ResearchCoverageError("父 manifest 未冻结压缩完整性/隔离策略")
    if selected.get("parent_manifest_fingerprint_sha256") != fingerprint:
        raise ResearchCoverageError("selection 未绑定同一父 manifest")

    collector = selected.get("collector_id")
    if not isinstance(collector, str) or not collector:
        raise ResearchCoverageError("selection.collector_id 非法")
    available: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    by_id: Dict[str, Mapping[str, Any]] = {}
    for row in artifacts:
        if not isinstance(row, Mapping):
            raise ResearchCoverageError("manifest artifact 必须是对象")
        if row.get("collector_id") != collector:
            continue
        kind = row.get("artifact_type")
        time_text = row.get("artifact_time_utc")
        artifact_id = row.get("artifact_id")
        if kind not in {"update", "rib"} or not isinstance(time_text, str):
            raise ResearchCoverageError("manifest artifact type/time 非法")
        if not isinstance(artifact_id, str) or artifact_id in by_id:
            raise ResearchCoverageError("manifest artifact_id 非法或重复")
        key = (kind, time_text)
        if key in available:
            raise ResearchCoverageError("manifest 同一 collector/type/time 槽重复")
        available[key] = row
        by_id[artifact_id] = row

    invalid: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for row in invalid_rows:
        if not isinstance(row, Mapping):
            raise ResearchCoverageError("invalid_in_window 必须是对象")
        if row.get("collector_id") != collector:
            continue
        kind = row.get("artifact_type")
        time_text = row.get("artifact_time_utc")
        if kind not in {"update", "rib"} or not isinstance(time_text, str):
            raise ResearchCoverageError("隔离制品 type/time 非法")
        key = (kind, time_text)
        if key in invalid:
            raise ResearchCoverageError("同一隔离槽不得重复")
        if row.get("value_state") != "parse_failed":
            raise ResearchCoverageError("隔离制品 value_state 必须是 parse_failed")
        invalid[key] = row

    roles = _mapping(selected.get("roles"), "selection.roles")
    selected_ids: Dict[str, Mapping[str, Any]] = {}
    for role_row in _role_rows(roles):
        artifact_id = role_row.get("artifact_id")
        parent_row = by_id.get(artifact_id)
        if parent_row is None or canonical_json(parent_row) != canonical_json(role_row):
            raise ResearchCoverageError("selection role 制品与父 manifest 不一致")
        selected_ids[artifact_id] = role_row
    if selected.get("selected_unique_artifact_count") != len(selected_ids):
        raise ResearchCoverageError("selection.selected_unique_artifact_count 不一致")
    selected_bytes = sum(row["size_bytes"] for row in selected_ids.values())
    if selected.get("selected_unique_size_bytes") != selected_bytes:
        raise ResearchCoverageError("selection.selected_unique_size_bytes 不一致")

    window = _mapping(selected.get("window"), "selection.window")
    if window.get("interval_semantics") != "half_open" or window.get("granularity_seconds") != 300:
        raise ResearchCoverageError("selection.window 必须是五分钟半开区间")
    start = _utc(window.get("start_utc"), "selection.window.start_utc")
    end = _utc(window.get("end_exclusive_utc"), "selection.window.end_exclusive_utc")
    update_slots = _slots(start, end, UPDATE_INTERVAL)
    rib_slots = _slots(start, end, RIB_INTERVAL)
    update_coverage = _analysis_coverage(
        artifact_type="update",
        expected_slots=update_slots,
        interval=UPDATE_INTERVAL,
        available=available,
        invalid=invalid,
    )
    rib_coverage = _analysis_coverage(
        artifact_type="rib",
        expected_slots=rib_slots,
        interval=RIB_INTERVAL,
        available=available,
        invalid=invalid,
    )

    advertised = _mapping(selected.get("coverage"), "selection.coverage")
    for key, actual in (
        ("analysis_updates", update_coverage),
        ("analysis_ribs", rib_coverage),
    ):
        row = _mapping(advertised.get(key), f"selection.coverage.{key}")
        if (
            row.get("expected_count") != actual["expected_count"]
            or row.get("observed_count") != actual["available_count"]
            or row.get("missing_count")
            != actual["parse_failed_count"] + actual["source_unavailable_count"]
        ):
            raise ResearchCoverageError(f"selection.coverage.{key} 与父 manifest 不一致")

    baseline_row = roles.get("baseline_reference_rib")
    baseline_state = "available" if isinstance(baseline_row, Mapping) else "source_unavailable"
    seed_row = roles.get("state_seed_rib")
    seed_state = "available" if isinstance(seed_row, Mapping) else "source_unavailable"
    complete = (
        update_coverage["coverage_state"] == "complete"
        and rib_coverage["coverage_state"] == "complete"
        and baseline_state == "available"
        and seed_state == "available"
        and selected.get("status") == "complete"
    )
    semantic = {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "selection_id": selected.get("selection_id"),
        "parent_manifest_fingerprint_sha256": fingerprint,
        "collector_id": collector,
        "country_code": selected.get("country_code"),
        "window": dict(window),
        "integrity_evidence": {
            "manifest_verified": True,
            "file_identity": "sha256_and_artifact_id_v1",
            "compressed_stream_validation": "full_stream_to_eof_crc_or_equivalent",
            "read_change_detection": "lstat_open_fstat_before_after_and_full_rescan",
            "invalid_artifact_policy": "full_hash_quarantine_exclude_from_available_slots",
        },
        "roles": {
            "state_seed_rib": {"value_state": seed_state},
            "baseline_reference_rib": {"value_state": baseline_state},
        },
        "analysis": {
            "updates": update_coverage,
            "ribs": rib_coverage,
        },
        "selected_unique_artifact_count": len(selected_ids),
        "selected_unique_size_bytes": selected_bytes,
        "coverage_state": "complete" if complete else "incomplete",
    }
    return {
        **semantic,
        "semantic_fingerprint_sha256": hashlib.sha256(
            canonical_json(
                {
                    "schema": COVERAGE_FINGERPRINT_SCHEMA,
                    "coverage": semantic,
                }
            ).encode("utf-8")
        ).hexdigest(),
    }


__all__ = [
    "COVERAGE_SCHEMA_VERSION",
    "ResearchCoverageError",
    "reconcile_event_window_coverage",
]
