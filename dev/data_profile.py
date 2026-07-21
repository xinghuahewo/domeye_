#!/usr/bin/env python3
"""读取并严格校验项目唯一数据档。"""

from datetime import datetime, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "config" / "data-profile.json"
REQUIRED_KEYS = {
    "schema_version",
    "id",
    "mode",
    "timezone",
    "window_start",
    "window_end_exclusive",
    "snapshot_time",
    "api_profile",
}


class DataProfileError(RuntimeError):
    """数据档格式或语义不符合项目约束。"""


def _parse_profile_time(value, field, timezone):
    if not isinstance(value, str) or "T" not in value:
        raise DataProfileError("{} 必须是带时区的 ISO 8601 秒级时间".format(field))
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise DataProfileError("{} 不是有效的 ISO 8601 时间".format(field)) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataProfileError("{} 必须显式包含 UTC 偏移".format(field))
    if parsed.microsecond != 0:
        raise DataProfileError("{} 只能精确到秒".format(field))
    if parsed.utcoffset() != parsed.astimezone(timezone).utcoffset():
        raise DataProfileError("{} 的 UTC 偏移与 timezone 不一致".format(field))
    return parsed


def load_data_profile(path=PROFILE_PATH):
    """返回经过完整结构、时区、窗口和端口校验的数据档。"""

    profile_path = Path(path)
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DataProfileError("无法读取数据档：{}".format(profile_path)) from error
    if not isinstance(profile, dict) or set(profile) != REQUIRED_KEYS:
        raise DataProfileError("数据档顶层字段必须与项目约定完全一致")
    if profile["schema_version"] != 1:
        raise DataProfileError("不支持的数据档 schema_version")
    if profile["id"] != "feb-mar-2026" or profile["mode"] != "fixed":
        raise DataProfileError("当前开发阶段只允许 feb-mar-2026 固定数据档")
    if not isinstance(profile["api_profile"], str) or not profile["api_profile"]:
        raise DataProfileError("api_profile 不能为空")
    try:
        timezone = ZoneInfo(profile["timezone"])
    except (TypeError, ZoneInfoNotFoundError) as error:
        raise DataProfileError("timezone 不是可用的 IANA 时区") from error

    start = _parse_profile_time(profile["window_start"], "window_start", timezone)
    end_exclusive = _parse_profile_time(
        profile["window_end_exclusive"], "window_end_exclusive", timezone
    )
    snapshot = _parse_profile_time(profile["snapshot_time"], "snapshot_time", timezone)
    if not start < end_exclusive:
        raise DataProfileError("window_start 必须早于 window_end_exclusive")
    if snapshot + timedelta(seconds=1) != end_exclusive:
        raise DataProfileError("固定快照必须恰好位于排他窗口终点前一秒")

    result = dict(profile)
    result["parsed"] = {
        "start": start,
        "end_exclusive": end_exclusive,
        "snapshot": snapshot,
    }
    result["local"] = {
        "start": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_exclusive": end_exclusive.strftime("%Y-%m-%d %H:%M:%S"),
        "snapshot": snapshot.strftime("%Y-%m-%d %H:%M:%S"),
        "frontend_start": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "frontend_end": snapshot.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return result
