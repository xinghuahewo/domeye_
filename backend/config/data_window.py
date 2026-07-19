"""开发和冻结制品查询使用的可选时间锚点。"""

import datetime
import os
import re


SNAPSHOT_ENV = "DOMEYE_DATA_SNAPSHOT_TIME"
WINDOW_START_ENV = "DOMEYE_DATA_WINDOW_START"
WINDOW_END_ENV = "DOMEYE_DATA_WINDOW_END_EXCLUSIVE"
ENFORCE_ENV = "DOMEYE_ENFORCE_DATA_WINDOW"
TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$")


def _parse_timestamp(name, raw_value):
    value = raw_value.strip()
    if not TIMESTAMP_PATTERN.fullmatch(value):
        raise RuntimeError("{} 必须使用 YYYY-MM-DD HH:MM:SS 格式".format(name))
    try:
        return datetime.datetime.strptime(value.replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError as error:
        raise RuntimeError("{} 不是有效的日历时间".format(name)) from error


def _enforced():
    value = os.environ.get(ENFORCE_ENV, "").strip().lower()
    if value in ("", "false", "0", "no", "off"):
        return False
    if value in ("true", "1", "yes", "on"):
        return True
    raise RuntimeError("{} 只能为 true 或 false".format(ENFORCE_ENV))


def configured_data_window():
    """返回强制开发窗口；生产默认不启用。"""

    if not _enforced():
        return None
    start_raw = os.environ.get(WINDOW_START_ENV, "")
    end_raw = os.environ.get(WINDOW_END_ENV, "")
    snapshot_raw = os.environ.get(SNAPSHOT_ENV, "")
    if not start_raw or not end_raw or not snapshot_raw:
        raise RuntimeError("启用开发窗口时必须同时配置起点、排他终点和快照时间")

    start = _parse_timestamp(WINDOW_START_ENV, start_raw)
    end_exclusive = _parse_timestamp(WINDOW_END_ENV, end_raw)
    snapshot = _parse_timestamp(SNAPSHOT_ENV, snapshot_raw)
    if start >= end_exclusive:
        raise RuntimeError("开发数据窗口起点必须早于排他终点")
    if not start <= snapshot < end_exclusive:
        raise RuntimeError("开发快照时间必须位于数据窗口内")
    return start, end_exclusive


def validate_data_window_config():
    configured_data_window()


def resolve_query_now(explicit=None):
    """返回查询参考时间；未配置时保持原有系统时间行为。"""

    if explicit is not None:
        return explicit

    raw_value = os.environ.get(SNAPSHOT_ENV, "").strip()
    if not raw_value:
        return datetime.datetime.now()

    return _parse_timestamp(SNAPSHOT_ENV, raw_value)
