#!/usr/bin/env python3
"""提供与只读 API 契约一致的固定开发快照。"""

import argparse
from copy import deepcopy
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, unquote, urlparse


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "api-snapshot.json"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$")


def load_fixture():
    with FIXTURE_PATH.open("r", encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


def _first(query, name, default=None):
    values = query.get(name)
    return values[0] if values else default


def _parse_time(value, end_of_day=False):
    if not isinstance(value, str) or not value:
        return None
    normalized = value.strip()
    if DATE_PATTERN.fullmatch(normalized):
        normalized += " 23:59:59" if end_of_day else " 00:00:00"
    elif DATETIME_PATTERN.fullmatch(normalized):
        normalized = normalized.replace("T", " ")
    else:
        return None
    try:
        return datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _requested_range(query):
    date_range = _first(query, "date") or _first(query, "datetime")
    if date_range:
        parts = date_range.split("_", 1)
        start = _parse_time(parts[0])
        end = _parse_time(parts[1], end_of_day=True) if len(parts) > 1 else None
        valid = start is not None and (len(parts) == 1 or end is not None)
        return start, end, valid

    raw_start = _first(query, "start_time")
    raw_end = _first(query, "end_time")
    start = _parse_time(raw_start)
    end = _parse_time(raw_end)
    valid = (not raw_start or start is not None) and (not raw_end or end is not None)
    return start, end, valid


def _filter_time_rows(rows, time_key, query):
    start, end, valid = _requested_range(query)
    if not valid:
        return []
    if start is None and end is None:
        return deepcopy(rows)
    filtered = []
    for row in rows:
        observed = _parse_time(row.get(time_key))
        if observed is None:
            continue
        if start is not None and observed < start:
            continue
        if end is not None and observed > end:
            continue
        filtered.append(deepcopy(row))
    return filtered


def _positive_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _event_page(fixture, query):
    rows = _filter_time_rows(fixture["events"]["data"], "start_time", query)
    event_type = (_first(query, "event_type") or "").strip()
    level = (_first(query, "level") or "").strip()
    country = (_first(query, "country") or "all").strip()
    keyword = (_first(query, "event_info") or "").strip().casefold()
    if event_type and event_type != "all":
        rows = [row for row in rows if row.get("event_type") == event_type]
    if level and level != "all":
        rows = [row for row in rows if row.get("level") == level]
    if country == "domestic":
        rows = [row for row in rows if row.get("attacked_country") == "中国"]
    elif country == "foreign":
        rows = [row for row in rows if row.get("attacked_country") != "中国"]
    if keyword:
        rows = [
            row for row in rows
            if keyword in " ".join(str(value) for value in row.values()).casefold()
        ]
    sort_mode = (_first(query, "sort_mode") or "start_timeB").strip()
    rows.sort(key=lambda row: row.get("start_time", ""), reverse=sort_mode.endswith("B"))
    page = _positive_int(_first(query, "page_num"), 1)
    page_size = _positive_int(_first(query, "page_size"), 10)
    offset = (page - 1) * page_size
    return {
        "total_page": math.ceil(len(rows) / page_size) if rows else 0,
        "record_count": str(len(rows)),
        "data": rows[offset:offset + page_size],
    }


def _feature_page(fixture, query, kind):
    fixture_key = "country_features" if kind == "country" else "as_features"
    rows = deepcopy(fixture[fixture_key]["data"])
    if kind == "country":
        country = (_first(query, "country") or "").strip()
        if country:
            rows = [row for row in rows if country in row.get("country", "")]
    else:
        asn = (_first(query, "asn") or "").strip().upper().removeprefix("AS")
        country = (_first(query, "country") or "").strip()
        if asn:
            rows = [row for row in rows if row.get("asn") == asn]
        if country:
            rows = [row for row in rows if row.get("country") == country]

    for row in rows:
        row["time_series_data"] = _filter_time_rows(
            row.get("time_series_data", []),
            "time",
            query,
        )
    page = _positive_int(_first(query, "page_num"), 1)
    requested_page_size = _positive_int(_first(query, "page_size"), 5)
    page_size = requested_page_size if requested_page_size in (5, 10, 20, 50) else 5
    offset = (page - 1) * page_size
    return {
        "total_page": math.ceil(len(rows) / page_size) if rows else 0,
        "record_count": len(rows),
        "current_page": page,
        "page_size": page_size,
        "data": rows[offset:offset + page_size],
    }


def _top_events(fixture):
    selected = []
    seen = set()
    for row in sorted(fixture["events"]["data"], key=lambda item: item["start_time"], reverse=True):
        if row["event_type"] in seen:
            continue
        selected.append(deepcopy(row))
        seen.add(row["event_type"])
    return selected[:10]


def payload_for(path, query, fixture):
    if path == "/api/v1/healthz":
        return deepcopy(fixture["health"])
    if path == "/api/v1/events":
        return _event_page(fixture, query)
    if path == "/api/v1/events/top":
        return _top_events(fixture)
    if path == "/api/v1/features/top":
        return _filter_time_rows(fixture["features"], "t", query)
    if path == "/api/v1/features/countries":
        return _feature_page(fixture, query, "country")
    if path == "/api/v1/features/ases":
        return _feature_page(fixture, query, "as")

    outage_routes = {
        "/api/v1/features/outages/country-as": "country_as",
        "/api/v1/features/outages/country-prefix": "country_prefix",
        "/api/v1/features/outages/as-prefix": "as_prefix",
        "/api/v1/features/outages/global-as": "global_as",
        "/api/v1/features/outages/global-prefix": "global_prefix",
    }
    outage_name = outage_routes.get(path)
    if outage_name:
        return _filter_time_rows(fixture["outages"][outage_name], "time_slot", query)
    if path == "/api/v1/dashboard/counts/total":
        return deepcopy(fixture["counts"])
    if path == "/api/v1/dashboard/counts/type":
        return deepcopy(fixture["type_count"])
    if path.startswith("/api/v1/") and path.count("/") >= 7:
        parts = path.strip("/").split("/")
        event_kind = parts[2]
        start_time = parts[3]
        window = fixture["data_window"]
        observed = _parse_time(start_time)
        if observed is None or not (
            _parse_time(window["start_time"]) <= observed <= _parse_time(window["end_time"])
        ):
            return {}
        detail = deepcopy(fixture["event_details"].get(event_kind, {}))
        if not detail:
            return {}
        detail["start_time"] = start_time
        return detail
    return None


def empty_payload(path):
    if path == "/api/v1/healthz":
        return None
    if path == "/api/v1/events":
        return {"data": [], "total_page": 0, "record_count": "0"}
    if path in ("/api/v1/features/countries", "/api/v1/features/ases"):
        return {"data": [], "total_page": 0, "record_count": 0, "current_page": 1, "page_size": 10}
    if path == "/api/v1/dashboard/counts/type":
        return {"event_type": "前缀劫持", "num": 0, "amplitude_type": True, "amplitude": "0%", "icon": "icon-dongtai"}
    if path.startswith("/api/v1/"):
        return []
    return None


class MockHandler(BaseHTTPRequestHandler):
    server_version = "DomeyeMock/1.0"

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # timeout 场景下客户端会在服务端延迟结束前主动断开。
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = unquote(parsed_url.path).rstrip("/") or "/"
        query = parse_qs(parsed_url.query, keep_blank_values=True)
        fixture = load_fixture()

        scenario = os.environ.get("DOMEYE_MOCK_SCENARIO", "normal").strip().lower()
        if scenario == "timeout":
            time.sleep(float(os.environ.get("DOMEYE_MOCK_DELAY_SECONDS", "3")))
        elif scenario == "error":
            self.send_json(503, {"status": False, "msg": "开发快照模拟服务异常"})
            return
        elif scenario == "empty":
            payload = empty_payload(path)
            if payload is not None:
                self.send_json(200, payload)
                return

        payload = payload_for(path, query, fixture)
        if payload is None:
            self.send_json(404, {"status": False, "msg": "开发快照未定义该接口"})
            return
        self.send_json(200, payload)

    def log_message(self, message, *args):
        print("[mock-api] {} - {}".format(self.address_string(), message % args), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Domeye 固定开发快照 API")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), MockHandler)
    print("固定开发快照 API：http://127.0.0.1:{}/api/v1/".format(args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
