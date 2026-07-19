"""仅在显式开发模式下限制 API 查询时间范围。"""

import datetime

from flask import request

from config.data_window import configured_data_window


def _timestamp(value):
    try:
        return datetime.datetime.strptime(value.replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except (AttributeError, ValueError):
        return None


def _date(value, end_of_day=False):
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    if end_of_day:
        return parsed.replace(hour=23, minute=59, second=59)
    return parsed


def _error(start, end_exclusive, reason):
    return {
        "status": False,
        "msg": "开发数据仅支持 {} 至 {}：{}".format(
            start.strftime("%Y-%m-%d %H:%M:%S"),
            (end_exclusive - datetime.timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"),
            reason,
        ),
    }, 400


def _inside(start, end, window_start, window_end):
    return start <= end and window_start <= start and end < window_end


def enforce_request_data_window():
    window = configured_data_window()
    if window is None or request.method == "OPTIONS":
        return None
    window_start, window_end = window
    path = request.path.rstrip("/")

    if path in (
        "/api/v1/healthz",
        "/api/v1/events/top",
        "/api/v1/dashboard/counts/total",
        "/api/v1/dashboard/counts/type",
    ):
        return None

    if path == "/api/v1/events":
        raw_range = request.args.get("datetime") or request.args.get("date")
        parts = raw_range.split("_", 1) if raw_range else []
        start = _date(parts[0]) if parts else None
        end = _date(parts[1], end_of_day=True) if len(parts) == 2 else None
        if start is None or end is None:
            return _error(window_start, window_end, "事件列表必须提供完整 date 范围")
        if not _inside(start, end, window_start, window_end):
            return _error(window_start, window_end, "事件日期超出窗口")
        return None

    if path.startswith("/api/v1/features/"):
        start = _timestamp(request.args.get("start_time"))
        end = _timestamp(request.args.get("end_time"))
        if start is None or end is None:
            return _error(window_start, window_end, "特征接口必须提供秒级起止时间")
        if not _inside(start, end, window_start, window_end):
            return _error(window_start, window_end, "特征时间超出窗口")
        return None

    if path == "/api/v1/dashboard/overview":
        start = _timestamp(request.args.get("start_time"))
        end = _timestamp(request.args.get("end_time"))
        if start is None or end is None:
            return _error(window_start, window_end, "首页聚合必须提供秒级起止时间")
        if not _inside(start, end, window_start, window_end):
            return _error(window_start, window_end, "首页聚合时间超出窗口")
        return None

    detail_start = (request.view_args or {}).get("start_time")
    if detail_start is not None:
        observed = _timestamp(detail_start)
        if observed is None or not window_start <= observed < window_end:
            return _error(window_start, window_end, "事件详情时间超出窗口")
    return None
