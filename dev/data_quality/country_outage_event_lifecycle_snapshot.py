#!/usr/bin/env python3
"""从既有只读事件详情冻结 224-310 国家中断生命周期输入。"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "country_outage_event_lifecycle_snapshot/v1"
WINDOW_START = datetime(2026, 2, 24, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 3, 11, tzinfo=timezone.utc)
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
INTERVAL_SECONDS = 300
REFERENCE_PATTERN = re.compile(
    r"^country_outage/(?P<start>[^/]+)/(?P<country>[A-Z]{2})/"
    r"(?P<event_id>[1-9][0-9]*)/(?P<source>[A-Za-z0-9_-]+)$"
)


class LifecycleSnapshotError(RuntimeError):
    """生命周期事实不能在冻结边界内安全形成快照。"""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_local_time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as error:
        raise LifecycleSnapshotError(f"{field} 不是秒级本地时间：{value!r}") from error
    return parsed.replace(tzinfo=BUSINESS_TIMEZONE).astimezone(timezone.utc)


def previous_complete_state_point(value: datetime) -> datetime:
    epoch_microseconds = int(value.timestamp() * 1_000_000)
    seconds = (epoch_microseconds - 1) // 1_000_000
    aligned = (seconds // INTERVAL_SECONDS) * INTERVAL_SECONDS
    return datetime.fromtimestamp(aligned, tz=timezone.utc)


def ceiling_state_point(value: datetime) -> datetime:
    epoch_seconds = int(value.timestamp())
    aligned = ((epoch_seconds + INTERVAL_SECONDS - 1) // INTERVAL_SECONDS) * INTERVAL_SECONDS
    return datetime.fromtimestamp(aligned, tz=timezone.utc)


def duration_seconds(value: str) -> int:
    match = re.fullmatch(
        r"(?:(?P<days>[0-9]+) days?, )?(?P<hours>[0-9]+):(?P<minutes>[0-5][0-9]):(?P<seconds>[0-5][0-9])",
        value,
    )
    if match is None:
        raise LifecycleSnapshotError(f"duration 格式无效：{value!r}")
    return (
        int(match.group("days") or 0) * 86_400
        + int(match.group("hours")) * 3_600
        + int(match.group("minutes")) * 60
        + int(match.group("seconds"))
    )


def load_incidents(path: Path) -> tuple[list[dict[str, str]], str]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise LifecycleSnapshotError(f"无法读取 incident TSV：{error}") from error
    try:
        decoded = gzip.decompress(raw).decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise LifecycleSnapshotError(f"incident TSV gzip 无效：{error}") from error
    rows = list(csv.DictReader(io.StringIO(decoded), delimiter="\t"))
    required = {
        "candidate_id",
        "incident_id",
        "legacy_reference",
        "country_code",
        "event_type",
        "source_code",
        "collector_id",
        "legacy_event_time_utc",
        "status",
    }
    if not rows or not required.issubset(rows[0]):
        raise LifecycleSnapshotError("incident TSV 缺少冻结字段")
    seen: set[str] = set()
    for row in rows:
        reference = row["legacy_reference"]
        match = REFERENCE_PATTERN.fullmatch(reference)
        if (
            match is None
            or reference in seen
            or row["event_type"] != "country_outage"
            or row["collector_id"] != "rrc25"
            or row["source_code"] != match.group("source")
            or row["country_code"] != match.group("country")
            or row["status"] != "complete"
        ):
            raise LifecycleSnapshotError(f"incident 输入身份无效：{reference!r}")
        seen.add(reference)
    rows.sort(key=lambda row: (row["legacy_event_time_utc"], row["legacy_reference"]))
    return rows, sha256_bytes(raw)


def fetch_detail(api_base: str, reference: str, timeout: float) -> dict[str, Any]:
    endpoint = api_base.rstrip("/") + "/" + "/".join(
        urllib.parse.quote(part, safe="") for part in reference.split("/")
    )
    request = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise LifecycleSnapshotError(f"事件详情 HTTP {response.status}：{reference}")
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise LifecycleSnapshotError(f"事件详情读取失败：{reference}：{error}") from error
    if not isinstance(payload, dict) or payload.get("status") is False:
        raise LifecycleSnapshotError(f"事件详情不可用：{reference}")
    return payload


def build_event(row: dict[str, str], detail: dict[str, Any]) -> dict[str, Any]:
    reference = row["legacy_reference"]
    match = REFERENCE_PATTERN.fullmatch(reference)
    assert match is not None
    local_start = match.group("start")
    if str(detail.get("start_time") or "") != local_start:
        raise LifecycleSnapshotError(f"事件开始时间冲突：{reference}")
    detected = parse_local_time(local_start, "start_time")
    if utc_text(detected) != row["legacy_event_time_utc"]:
        raise LifecycleSnapshotError(f"incident UTC 与引用冲突：{reference}")
    if not (WINDOW_START <= detected < WINDOW_END):
        raise LifecycleSnapshotError(f"事件首次检测越出 224-310：{reference}")

    cohort_point = previous_complete_state_point(detected)
    requested_window_start = cohort_point - timedelta(hours=1)
    projection_start = max(requested_window_start, WINDOW_START)
    left_missing_slots = int((projection_start - requested_window_start).total_seconds() // INTERVAL_SECONDS)

    raw_end = str(detail.get("end_time") or "").strip()
    raw_duration = str(detail.get("duration") or "").strip()
    event_end_utc: str | None = None
    if bool(raw_end) != bool(raw_duration):
        raise LifecycleSnapshotError(f"结束时间与时长必须同时存在或同时缺失：{reference}")
    if raw_end:
        ended = parse_local_time(raw_end, "end_time")
        if ended < detected:
            raise LifecycleSnapshotError(f"事件结束早于开始：{reference}")
        expected_duration = int((ended - detected).total_seconds())
        if duration_seconds(raw_duration) != expected_duration:
            raise LifecycleSnapshotError(f"事件时长与起止时间不守恒：{reference}")
        event_end_utc = utc_text(ended)
        projected_end = min(ceiling_state_point(ended), WINDOW_END)
        lifecycle_state = "event_end_recorded" if ended <= WINDOW_END else "event_end_outside_data_range"
        is_final_in_data_range = ended <= WINDOW_END
    else:
        projected_end = WINDOW_END
        lifecycle_state = "event_end_unknown"
        is_final_in_data_range = False

    return {
        "incident_id": row["incident_id"],
        "legacy_reference": reference,
        "country_code": row["country_code"],
        "source_code": row["source_code"],
        "collector_id": "rrc25",
        "detected_at_utc": utc_text(detected),
        "cohort_state_point_utc": utc_text(cohort_point),
        "window_start_utc": utc_text(projection_start),
        "requested_window_start_utc": utc_text(requested_window_start),
        "left_boundary_missing_slot_count": left_missing_slots,
        "event_end_at_utc": event_end_utc,
        "event_duration_seconds": duration_seconds(raw_duration) if raw_duration else None,
        "projection_end_state_point_utc": utc_text(projected_end),
        "lifecycle_state": lifecycle_state,
        "is_final_in_data_range": is_final_in_data_range,
        "lifecycle_source": "legacy_country_outage_event_fact",
    }


def build_snapshot(incident_path: Path, api_base: str, timeout: float) -> dict[str, Any]:
    incidents, incident_sha = load_incidents(incident_path)
    events = [build_event(row, fetch_detail(api_base, row["legacy_reference"], timeout)) for row in incidents]
    state_counts: dict[str, int] = {}
    for event in events:
        state = str(event["lifecycle_state"])
        state_counts[state] = state_counts.get(state, 0) + 1
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "complete",
        "collector_id": "rrc25",
        "window_start_utc": utc_text(WINDOW_START),
        "window_end_exclusive_utc": utc_text(WINDOW_END),
        "interval_seconds": INTERVAL_SECONDS,
        "event_count": len(events),
        "incident_input_sha256": incident_sha,
        "detail_source_semantics": "existing_read_only_legacy_event_fact",
        "window_semantics": "twelve_complete_slots_before_detection_then_lifecycle_or_range_cap",
        "lifecycle_state_counts": state_counts,
        "events": events,
    }
    payload["content_sha256"] = sha256_bytes(canonical_json(payload))
    payload["snapshot_id"] = "event_lifecycle_snapshot_v1_" + payload["content_sha256"][:32]
    return payload


def write_snapshot(path: Path, payload: dict[str, Any], resume: bool) -> None:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    if path.exists():
        existing = path.read_bytes()
        if resume and existing == raw:
            return
        raise LifecycleSnapshotError(f"输出已存在且不允许覆盖：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        raise LifecycleSnapshotError(f"临时输出已存在：{temporary}")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incident-tsv", required=True, type=Path)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    try:
        payload = build_snapshot(arguments.incident_tsv, arguments.api_base, arguments.timeout)
        write_snapshot(arguments.output, payload, arguments.resume)
    except (LifecycleSnapshotError, OSError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"status": "complete", "result": payload}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
