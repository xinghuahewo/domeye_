#!/usr/bin/env python3
"""提供与只读 API 契约一致的固定开发快照。"""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "api-snapshot.json"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$")
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
EVENT_LABEL_KINDS = {
    "前缀劫持": "hijack",
    "子前缀劫持": "sub_hijack",
    "前缀中断": "prefix_outage",
    "AS中断": "as_outage",
    "国家中断": "country_outage",
    "路由泄漏": "leak",
}


def _semantic_guardrails(event_type, end_time=None):
    event_kind = EVENT_LABEL_KINDS.get(event_type, event_type)
    lifecycle_state = (
        "unavailable"
        if event_kind == "leak"
        else (
            "recorded"
            if end_time not in (None, "", "-", "None", "NaT")
            else "unknown"
        )
    )
    blocked_claims = ["causal_conclusion"]
    reason_codes = []
    if event_kind == "leak":
        blocked_claims.extend(["event_end", "duration", "recovery_state", "ongoing_state"])
        reason_codes.append("legacy_leak_lifecycle_missing")
    if event_kind == "prefix_outage":
        blocked_claims.extend(["responsible_as", "causal_attribution"])
        reason_codes.append("legacy_moas_attribution_bias")
    ratio_state = (
        "recompute_required"
        if event_kind in ("as_outage", "country_outage")
        else "not_applicable"
    )
    if ratio_state == "recompute_required":
        blocked_claims.append("stored_ratio_as_authoritative")
        reason_codes.append("legacy_ratio_recompute_required")
    return {
        "contract_version": "legacy_event_semantic_guardrails_v1",
        "lifecycle_state": lifecycle_state,
        "attribution_state": (
            "legacy_biased"
            if event_kind == "prefix_outage"
            else "detector_fact_only"
        ),
        "ratio_state": ratio_state,
        "blocked_claims": blocked_claims,
        "reason_codes": reason_codes,
    }


def _decorate_event_item(item):
    result = deepcopy(item)
    if EVENT_LABEL_KINDS.get(result.get("event_type")) == "leak":
        result["end_time"] = "-"
    result["semantic_guardrails"] = _semantic_guardrails(
        result.get("event_type"),
        result.get("end_time"),
    )
    return result


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
    attacked_country = (_first(query, "attacked_country") or "").strip()
    attacked_as = (_first(query, "attacked_as") or "").strip().upper().removeprefix("AS")
    keyword = (_first(query, "event_info") or "").strip().casefold()
    if event_type and event_type != "all":
        rows = [row for row in rows if row.get("event_type") == event_type]
    if level and level != "all":
        rows = [row for row in rows if row.get("level") == level]
    if country == "domestic":
        rows = [row for row in rows if row.get("attacked_country") == "中国"]
    elif country == "foreign":
        rows = [row for row in rows if row.get("attacked_country") != "中国"]
    if attacked_country:
        rows = [row for row in rows if row.get("attacked_country") == attacked_country]
    if attacked_as:
        rows = [
            row for row in rows
            if str(row.get("attacked_as") or "").upper().removeprefix("AS") == attacked_as
        ]
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
        "data": [
            _decorate_event_item(row)
            for row in rows[offset:offset + page_size]
        ],
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
        selected.append(_decorate_event_item(row))
        seen.add(row["event_type"])
    return selected[:10]


def _dashboard_overview(fixture, query):
    event_types = ["前缀劫持", "子前缀劫持", "前缀中断", "AS中断", "国家中断", "路由泄漏"]
    rows = _filter_time_rows(fixture["events"]["data"], "start_time", query)
    buckets = {}
    country_counts = {}
    country_high = {}
    asn_counts = {}
    asn_high = {}
    for row in rows:
        observed = _parse_time(row.get("start_time"))
        if observed is not None:
            bucket = observed.replace(minute=0, second=0).strftime("%Y-%m-%d %H:%M:%S")
            counts = buckets.setdefault(bucket, {event_type: 0 for event_type in event_types})
            event_type = row.get("event_type")
            if event_type in counts:
                counts[event_type] += 1
        country = str(row.get("attacked_country") or "").strip()
        if country:
            country_counts[country] = country_counts.get(country, 0) + 1
            if row.get("level") == "high":
                country_high[country] = country_high.get(country, 0) + 1
        asn = str(row.get("attacked_as") or "").strip()
        if asn:
            asn_counts[asn] = asn_counts.get(asn, 0) + 1
            if row.get("level") == "high":
                asn_high[asn] = asn_high.get(asn, 0) + 1

    def ordered(counts):
        return sorted(counts, key=lambda item: (-counts[item], item))[:6]

    feature_rows = _filter_time_rows(fixture["features"], "t", query)
    return {
        "start_time": _first(query, "start_time") or fixture["data_window"]["start_time"],
        "end_time": _first(query, "end_time") or fixture["data_window"]["end_time"],
        "timezone": fixture["data_window"]["timezone"],
        "latest_observation": feature_rows[-1]["t"] if feature_rows else None,
        "event_count": len(rows),
        "previous_event_count": 0,
        "event_change_rate": None,
        "high_risk_count": sum(1 for row in rows if row.get("level") == "high"),
        "active_event_count": sum(1 for row in rows if not row.get("end_time")),
        "affected_asn_count": len(asn_counts),
        "affected_country_count": len(country_counts),
        "event_series": [
            {"time": bucket, "counts": buckets[bucket], "total": sum(buckets[bucket].values())}
            for bucket in sorted(buckets)
        ],
        "country_rankings": [
            {"name": name, "event_count": country_counts[name], "high_risk_count": country_high.get(name, 0)}
            for name in ordered(country_counts)
        ],
        "asn_rankings": [
            {"asn": asn, "name": "AS{}".format(asn), "event_count": asn_counts[asn], "high_risk_count": asn_high.get(asn, 0)}
            for asn in ordered(asn_counts)
        ],
    }


def _country_overview(fixture, query):
    selected_name = (_first(query, "country") or "").strip()
    limit = min(12, max(3, _positive_int(_first(query, "limit"), 6)))
    event_rows = _filter_time_rows(fixture["events"]["data"], "start_time", query)
    anomaly_counts = {}
    high_counts = {}
    for event in event_rows:
        country = str(event.get("attacked_country") or "").strip()
        if not country:
            continue
        anomaly_counts[country] = anomaly_counts.get(country, 0) + 1
        if event.get("level") == "high":
            high_counts[country] = high_counts.get(country, 0) + 1

    profiles = {}
    for item in fixture["country_features"]["data"]:
        country = item["country"]
        points = _filter_time_rows(item.get("time_series_data", []), "time", query)
        if not points:
            continue
        announce = sum(int(point.get("announce") or 0) for point in points)
        withdraw = sum(int(point.get("withdraw") or 0) for point in points)
        update_total = announce + withdraw
        latest = points[-1]
        baseline = points[0]
        v4_change = int(latest["v4Prefix_num"]) - int(baseline["v4Prefix_num"])
        v6_change = int(latest["v6Prefix_num"]) - int(baseline["v6Prefix_num"])
        peak = max(points, key=lambda point: int(point.get("announce") or 0) + int(point.get("withdraw") or 0))
        v4_change_rate = abs(v4_change / int(baseline["v4Prefix_num"]) * 100) if int(baseline["v4Prefix_num"]) else 0.0
        v6_change_rate = abs(v6_change / int(baseline["v6Prefix_num"]) * 100) if int(baseline["v6Prefix_num"]) else 0.0
        profile = {
            "country": country,
            "announce": announce,
            "withdraw": withdraw,
            "update_total": update_total,
            "withdraw_rate": round(withdraw / update_total * 100, 1) if update_total else 0.0,
            "previous_update_total": 0,
            "update_change_rate": None,
            "sample_count": len(points),
            "latest_observation": latest["time"],
            "ipv4_prefixes": latest.get("v4Prefix_num"),
            "ipv6_prefixes": latest.get("v6Prefix_num"),
            "ipv4_addresses": latest.get("v4IP_num"),
            "ipv4_prefix_change": v4_change,
            "ipv6_prefix_change": v6_change,
            "ipv4_address_change": int(latest["v4IP_num"]) - int(baseline["v4IP_num"]),
            "resource_change": max(abs(v4_change), abs(v6_change)),
            "resource_change_rate": round(max(v4_change_rate, v6_change_rate), 1),
            "peak_updates": int(peak.get("announce") or 0) + int(peak.get("withdraw") or 0),
            "peak_time": peak["time"],
            "anomaly_count": anomaly_counts.get(country, 0),
            "high_risk_count": high_counts.get(country, 0),
            "sparkline": [
                {
                    "time": point["time"],
                    "announce": int(point.get("announce") or 0),
                    "withdraw": int(point.get("withdraw") or 0),
                }
                for point in points
            ],
            "series": [
                {
                    "time": point["time"],
                    "announce": point.get("announce"),
                    "withdraw": point.get("withdraw"),
                    "ipv4_prefixes": point.get("v4Prefix_num"),
                    "ipv6_prefixes": point.get("v6Prefix_num"),
                    "ipv4_addresses": point.get("v4IP_num"),
                }
                for point in points
            ],
        }
        profiles[country] = profile

    rows = list(profiles.values())
    updates = sorted(rows, key=lambda item: (-item["update_total"], item["country"]))[:limit]
    withdraw_rates = sorted(rows, key=lambda item: (-item["withdraw_rate"], item["country"]))[:limit]
    resource_changes = sorted(rows, key=lambda item: (-item["resource_change_rate"], item["country"]))[:limit]
    anomalies = sorted(
        [item for item in rows if item["anomaly_count"] > 0],
        key=lambda item: (-item["anomaly_count"], item["country"]),
    )[:limit]
    latest_observation = max(
        (item["latest_observation"] for item in rows if item["latest_observation"]),
        default=None,
    )
    return {
        "start_time": _first(query, "start_time") or fixture["data_window"]["start_time"],
        "end_time": _first(query, "end_time") or fixture["data_window"]["end_time"],
        "timezone": fixture["data_window"]["timezone"],
        "latest_observation": latest_observation,
        "country_count": len(rows),
        "countries_with_anomalies": sum(1 for item in rows if item["anomaly_count"] > 0),
        "update_leader": updates[0] if updates else None,
        "withdraw_rate_leader": withdraw_rates[0] if withdraw_rates else None,
        "resource_change_leader": resource_changes[0] if resource_changes else None,
        "update_rankings": updates,
        "withdraw_rate_rankings": withdraw_rates,
        "resource_change_rankings": resource_changes,
        "anomaly_rankings": anomalies,
        "selected_country": profiles.get(selected_name),
    }


def _asn_overview(fixture, query):
    selected_asn = (_first(query, "asn") or "").strip().upper().removeprefix("AS")
    limit = min(12, max(3, _positive_int(_first(query, "limit"), 6)))
    event_rows = _filter_time_rows(fixture["events"]["data"], "start_time", query)
    anomaly_counts = {}
    high_counts = {}
    for event in event_rows:
        asn = str(event.get("attacked_as") or "").strip().upper().removeprefix("AS")
        if not asn:
            continue
        anomaly_counts[asn] = anomaly_counts.get(asn, 0) + 1
        if event.get("level") == "high":
            high_counts[asn] = high_counts.get(asn, 0) + 1

    profiles = {}
    for item in fixture["as_features"]["data"]:
        asn = item["asn"]
        points = _filter_time_rows(item.get("time_series_data", []), "time", query)
        if not points:
            continue
        announce = sum(int(point.get("announce") or 0) for point in points)
        withdraw = sum(int(point.get("withdraw") or 0) for point in points)
        update_total = announce + withdraw
        latest = points[-1]
        baseline = points[0]
        v4_change = int(latest["v4Prefix_num"]) - int(baseline["v4Prefix_num"])
        v6_change = int(latest["v6Prefix_num"]) - int(baseline["v6Prefix_num"])
        peak = max(points, key=lambda point: int(point.get("announce") or 0) + int(point.get("withdraw") or 0))
        updates = [int(point.get("announce") or 0) + int(point.get("withdraw") or 0) for point in points]
        average = sum(updates) / len(updates)
        variance = sum((value - average) ** 2 for value in updates) / len(updates)
        v4_change_rate = abs(v4_change / int(baseline["v4Prefix_num"]) * 100) if int(baseline["v4Prefix_num"]) else 0.0
        v6_change_rate = abs(v6_change / int(baseline["v6Prefix_num"]) * 100) if int(baseline["v6Prefix_num"]) else 0.0
        profiles[asn] = {
            "asn": asn,
            "as_name": item.get("as_name", ""),
            "org_name": item.get("org_name", ""),
            "country": item.get("country", ""),
            "as_type": "Transit/Access",
            "global_rank": 1 if asn == "4134" else None,
            "country_rank": 1 if asn == "4134" else None,
            "important": asn == "4134",
            "announce": announce,
            "withdraw": withdraw,
            "update_total": update_total,
            "withdraw_rate": round(withdraw / update_total * 100, 1) if update_total else 0.0,
            "previous_update_total": 0,
            "update_change_rate": None,
            "sample_count": len(points),
            "latest_observation": latest["time"],
            "ipv4_prefixes": latest.get("v4Prefix_num"),
            "ipv6_prefixes": latest.get("v6Prefix_num"),
            "ipv4_addresses": latest.get("v4IP_num"),
            "ipv4_prefix_change": v4_change,
            "ipv6_prefix_change": v6_change,
            "ipv4_address_change": int(latest["v4IP_num"]) - int(baseline["v4IP_num"]),
            "resource_change": max(abs(v4_change), abs(v6_change)),
            "resource_change_rate": round(max(v4_change_rate, v6_change_rate), 1),
            "peak_updates": int(peak.get("announce") or 0) + int(peak.get("withdraw") or 0),
            "peak_time": peak["time"],
            "volatility": round((variance ** 0.5) / average * 100, 1) if average else 0.0,
            "anomaly_count": anomaly_counts.get(asn, 0),
            "high_risk_count": high_counts.get(asn, 0),
            "sparkline": [
                {"time": point["time"], "announce": int(point.get("announce") or 0), "withdraw": int(point.get("withdraw") or 0)}
                for point in points
            ],
            "series": [
                {
                    "time": point["time"],
                    "announce": point.get("announce"),
                    "withdraw": point.get("withdraw"),
                    "ipv4_prefixes": point.get("v4Prefix_num"),
                    "ipv6_prefixes": point.get("v6Prefix_num"),
                    "ipv4_addresses": point.get("v4IP_num"),
                }
                for point in points
            ],
        }

    rows = list(profiles.values())
    updates = sorted(rows, key=lambda item: (-item["update_total"], int(item["asn"])))[:limit]
    withdraw_rates = sorted(rows, key=lambda item: (-item["withdraw_rate"], int(item["asn"])))[:limit]
    resource_changes = sorted(rows, key=lambda item: (-item["resource_change_rate"], int(item["asn"])))[:limit]
    volatilities = sorted(rows, key=lambda item: (-item["volatility"], int(item["asn"])))[:limit]
    anomalies = sorted(
        [item for item in rows if item["anomaly_count"] > 0],
        key=lambda item: (-item["anomaly_count"], int(item["asn"])),
    )[:limit]
    latest_observation = max((item["latest_observation"] for item in rows), default=None)
    return {
        "start_time": _first(query, "start_time") or fixture["data_window"]["start_time"],
        "end_time": _first(query, "end_time") or fixture["data_window"]["end_time"],
        "timezone": fixture["data_window"]["timezone"],
        "latest_observation": latest_observation,
        "scope_kind": "operational_asn_cohort",
        "scope_note": "Mock：静态优先、重要与异常 ASN 的运维候选集；排行不代表全网。",
        "candidate_pool_size": 1000,
        "scope_size": len(rows),
        "feature_asn_count": len(rows),
        "important_asn_count": sum(1 for item in rows if item["important"]),
        "asns_with_anomalies": sum(1 for item in rows if item["anomaly_count"] > 0),
        "update_leader": updates[0] if updates else None,
        "withdraw_rate_leader": withdraw_rates[0] if withdraw_rates else None,
        "resource_change_leader": resource_changes[0] if resource_changes else None,
        "volatility_leader": volatilities[0] if volatilities else None,
        "update_rankings": updates,
        "withdraw_rate_rankings": withdraw_rates,
        "resource_change_rankings": resource_changes,
        "volatility_rankings": volatilities,
        "anomaly_rankings": anomalies,
        "selected_asn": profiles.get(selected_asn),
    }


def _stable_identifier(prefix, payload):
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return prefix + hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


def _iso_times(value):
    parsed = _parse_time(value)
    if parsed is None:
        return None, None
    local = parsed.replace(tzinfo=BUSINESS_TIMEZONE)
    utc = local.astimezone(timezone.utc)
    return local.isoformat(timespec="seconds"), utc.isoformat(timespec="seconds").replace("+00:00", "Z")


def _mock_path_text(value):
    if isinstance(value, (list, tuple)):
        return " ".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        candidate = value.get("path", value.get("as_path", value.get("route")))
        return _mock_path_text(candidate) if candidate is not None else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(str(value).split())


def _mock_route_items(value, phase, label, source_field, fallback_time):
    if value in (None, "", []):
        return []
    entries = sorted(value.items()) if isinstance(value, dict) else [(fallback_time, value)]
    items = []
    for observed_at, raw_paths in entries:
        candidates = raw_paths if isinstance(raw_paths, list) else [raw_paths]
        paths = [_mock_path_text(item) for item in candidates]
        paths = [path for path in paths if path]
        local_time, utc_time = _iso_times(observed_at or fallback_time)
        identity = {
            "phase": phase,
            "source_field": source_field,
            "observed_at": local_time,
            "paths": paths,
        }
        items.append({
            "evidence_id": _stable_identifier("ev_v1_", identity),
            "phase": phase,
            "kind": "route_observation",
            "label": label,
            "source_field": source_field,
            "observed_at_local": local_time,
            "observed_at_utc": utc_time,
            "observation_state": "paths_observed" if paths else "no_path_in_snapshot",
            "path_count": len(paths),
            "paths": paths,
            "observer_identity": "not_retained",
            "semantics": "route_observation_not_causal_trace",
        })
    return items


def _mock_evidence_bundle(path, fixture):
    prefix = "/api/v1/events/evidence-bundle/"
    if not path.startswith(prefix):
        return None
    parts = path[len(prefix):].split("/")
    if len(parts) != 5:
        return None
    event_kind, start_time, problem, event_id, source = parts
    detail = deepcopy(fixture["event_details"].get(event_kind, {}))
    if not detail or not event_id.isdigit():
        return None
    detail["start_time"] = start_time
    detail["semantic_guardrails"] = _semantic_guardrails(
        event_kind,
        detail.get("end_time"),
    )

    event_labels = {
        "hijack": "前缀劫持", "sub_hijack": "子前缀劫持",
        "prefix_outage": "前缀中断", "as_outage": "AS中断",
        "country_outage": "国家中断", "leak": "路由泄漏",
    }
    table_prefixes = {
        "hijack": "hijack", "sub_hijack": "sub_hijack",
        "prefix_outage": "prefix_outage", "as_outage": "as_outage",
        "country_outage": "country_outage", "leak": "leak_event",
    }
    if event_kind not in event_labels:
        return None

    incident_identity = {
        "schema": "incident_id_v1", "event_type": event_kind,
        "start_time": start_time, "problem": problem,
        "event_id": int(event_id), "source": source,
    }
    incident_id = _stable_identifier("inc_v1_", incident_identity)
    canonical_reference = "/".join(parts)
    source_record = {
        "source_system": "Domeye business fact table",
        "source_table": "{}_{}{}".format(table_prefixes[event_kind], start_time[:4], start_time[5:7]),
        "source_code": source,
        "record_locator": {"problem": problem, "event_id": int(event_id), "start_time": start_time},
        "detail_reference": canonical_reference,
    }
    start_local, start_utc = _iso_times(start_time)
    end_local, end_utc = _iso_times(detail.get("end_time"))
    snapshot_local, snapshot_utc = _iso_times(fixture["data_window"]["end_time"])

    fact_record_identity = {
        key: value
        for key, value in detail.items()
        if key != "semantic_guardrails"
    }
    fact_identity = {
        "incident_id": incident_id,
        "source_record": source_record,
        "fact_record": fact_record_identity,
    }
    evidence_items = [{
        "evidence_id": _stable_identifier("ev_v1_", fact_identity),
        "phase": "context", "kind": "fact_record",
        "label": "业务事实表原始记录", "source_field": "fact_record",
        "observed_at_local": start_local, "observed_at_utc": start_utc,
        "field_count": len(fact_record_identity), "semantics": "detector_fact_record",
    }]
    route_items = []
    route_items.extend(_mock_route_items(
        detail.get("pre_vp_paths"), "before", "异常前可见路径快照",
        "pre_vp_paths", start_time,
    ))
    route_items.extend(_mock_route_items(
        detail.get("eve_vp_paths"), "during", "异常期间路径快照",
        "eve_vp_paths", start_time,
    ))
    route_items.extend(_mock_route_items(
        detail.get("next_vp_paths"), "after", "异常后路径快照",
        "next_vp_paths", detail.get("end_time") or start_time,
    ))
    if detail.get("as_path"):
        route_items.extend(_mock_route_items(
            detail["as_path"], "during", "事件记录 AS_PATH 快照",
            "as_path", start_time,
        ))
    evidence_items.extend(route_items)

    for field, label in (("outage_prefixes", "受影响前缀集合"), ("outage_ases", "受影响 AS 集合"), ("attacked_ases", "受影响 AS 集合")):
        values = detail.get(field) or []
        if not isinstance(values, list) or not values:
            continue
        objects = [str(item) for item in values]
        identity = {"incident_id": incident_id, "field": field, "objects": objects}
        evidence_items.append({
            "evidence_id": _stable_identifier("ev_v1_", identity),
            "phase": "context", "kind": "affected_object_set",
            "label": label, "source_field": field,
            "object_count": len(objects), "objects": objects,
            "semantics": "fact_table_affected_object_set",
        })

    def coverage(phase):
        items = [item for item in route_items if item["phase"] == phase]
        path_count = sum(item["path_count"] for item in items)
        status = "not_available" if not items else ("observed_paths" if path_count else "observed_no_path")
        return {
            "status": status, "snapshot_count": len(items), "path_count": path_count,
            "evidence_ids": [item["evidence_id"] for item in items],
        }

    phase_coverage = {phase: coverage(phase) for phase in ("before", "during", "after")}
    counterevidence = []
    if phase_coverage["after"]["path_count"]:
        counterevidence.append("异常后重新观测到可见路径，是“持续不可见”假设的反证，但不证明全网恢复。")
    supports = []
    if phase_coverage["before"]["path_count"] and phase_coverage["during"]["status"] == "observed_no_path":
        supports.append("异常前存在可见路径、异常期间快照未保留可见路径，支持“观测可见性下降”的描述。")
    gaps = [
        "当前事实字段未保留观测点身份，无法量化或复核 VP 覆盖范围。",
        "当前证据包未附原始 BGP 报文，无法进行逐报文重放。",
        "路径快照只能说明被观测到的路径状态，不能单独证明异常根因。",
    ]
    reason_codes = set(detail["semantic_guardrails"]["reason_codes"])
    if "legacy_leak_lifecycle_missing" in reason_codes:
        gaps.append("历史路由泄漏记录未保留结束时间和时长；不得据此判断恢复、持续中或已结束。")
    if "legacy_moas_attribution_bias" in reason_codes:
        gaps.append("历史前缀中断的 AS 归属存在 MOAS 选择偏置；只能展示检测器记录，不能认定责任主体。")
    if "legacy_ratio_recompute_required" in reason_codes:
        gaps.append("历史中断比例字段不作为权威事实；需要时必须使用当前返回的分子和分母重新计算。")
    for phase, label in (("before", "异常前"), ("during", "异常期间"), ("after", "异常后")):
        if phase_coverage[phase]["status"] == "not_available":
            gaps.append("{}路径快照缺失；这表示证据不可用，不表示该阶段没有路径。".format(label))

    object_value = next((
        str(detail[key]) for key in (
            "hijacker_prefix", "hijacked_prefix", "outage_prefix", "leak_prefix",
            "outage_as", "outage_country", "attacked_as", "attacked_country",
        ) if detail.get(key) not in (None, "")
    ), problem.replace("-", "/"))
    return {
        "bundle_version": "evidence_bundle_v1",
        "incident_id": incident_id,
        "incident_id_schema": "incident_id_v1",
        "semantic_guardrails": detail["semantic_guardrails"],
        "event": {
            "kind": event_kind, "label": event_labels[event_kind], "object": object_value,
            "level": str(detail.get("event_level") or ""),
            "summary": str(detail.get("event_info") or detail.get("event_descr") or ""),
            "duration": str(detail.get("duration") or ""),
            "event_time_local": start_local, "event_time_utc": start_utc,
            "end_time_local": end_local, "end_time_utc": end_utc,
            "source_timezone": "Asia/Shanghai",
        },
        "data_snapshot": {
            "snapshot_time_local": snapshot_local, "snapshot_time_utc": snapshot_utc,
            "timezone": "Asia/Shanghai",
        },
        "source_record": source_record,
        "phase_coverage": phase_coverage,
        "evidence_items": evidence_items,
        "assessment": {
            "classification": "observation_only", "supports": supports,
            "counterevidence": counterevidence, "gaps": gaps,
            "causal_conclusion": None,
        },
        "data_quality": {
            "observed_phase_count": sum(item["status"] != "not_available" for item in phase_coverage.values()),
            "expected_phase_count": 3,
            "route_observation_count": len(route_items),
            "evidence_item_count": len(evidence_items),
            "vantage_point_identity_available": False,
            "raw_bgp_message_available": False,
            "timezone_semantics": "timestamp_without_time_zone interpreted as Asia/Shanghai",
            "limitations": [
                "Route Observation / Path Snapshot 不是因果链路或根因证据。",
                "无时区业务时间按数据画像 Asia/Shanghai 解释，并派生 UTC 时间。",
                "异常后路径快照仅表示后续可见性观测，不等同于全网恢复确认。",
                "阶段字段缺失表示当前事实记录未保留该证据，不能解释为网络状态缺失。",
            ],
        },
        "fact_record": detail,
    }


def payload_for(path, query, fixture):
    if path == "/api/v1/healthz":
        return deepcopy(fixture["health"])
    if path == "/api/v1/events":
        return _event_page(fixture, query)
    if path == "/api/v1/events/top":
        return _top_events(fixture)
    evidence_bundle = _mock_evidence_bundle(path, fixture)
    if evidence_bundle is not None:
        return evidence_bundle
    if path == "/api/v1/features/top":
        return _filter_time_rows(fixture["features"], "t", query)
    if path == "/api/v1/features/countries":
        return _feature_page(fixture, query, "country")
    if path == "/api/v1/features/countries/overview":
        return _country_overview(fixture, query)
    if path == "/api/v1/features/ases":
        return _feature_page(fixture, query, "as")
    if path == "/api/v1/features/ases/overview":
        return _asn_overview(fixture, query)
    if path == "/api/v1/features/ases/events":
        page = _event_page(fixture, query)
        page["match_mode"] = "asn_token_exact"
        page["asn"] = (_first(query, "asn") or "").strip().upper().removeprefix("AS")
        return page

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
    if path == "/api/v1/dashboard/overview":
        return _dashboard_overview(fixture, query)
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
        detail["semantic_guardrails"] = _semantic_guardrails(
            event_kind,
            detail.get("end_time"),
        )
        return detail
    return None


def empty_payload(path):
    if path == "/api/v1/healthz":
        return None
    if path == "/api/v1/events":
        return {"data": [], "total_page": 0, "record_count": "0"}
    if path in ("/api/v1/features/countries", "/api/v1/features/ases"):
        return {"data": [], "total_page": 0, "record_count": 0, "current_page": 1, "page_size": 10}
    if path == "/api/v1/features/countries/overview":
        return {
            "start_time": "", "end_time": "", "timezone": "Asia/Shanghai",
            "latest_observation": None, "country_count": 0, "countries_with_anomalies": 0,
            "update_leader": None, "withdraw_rate_leader": None,
            "resource_change_leader": None, "update_rankings": [],
            "withdraw_rate_rankings": [], "resource_change_rankings": [],
            "anomaly_rankings": [], "selected_country": None,
        }
    if path == "/api/v1/dashboard/counts/type":
        return {"event_type": "前缀劫持", "num": 0, "amplitude_type": True, "amplitude": "0%", "icon": "icon-dongtai"}
    if path == "/api/v1/dashboard/overview":
        return {
            "start_time": "", "end_time": "", "timezone": "Asia/Shanghai", "latest_observation": None,
            "event_count": 0, "previous_event_count": 0, "event_change_rate": None,
            "high_risk_count": 0, "active_event_count": 0,
            "affected_asn_count": 0, "affected_country_count": 0,
            "event_series": [], "country_rankings": [], "asn_rankings": [],
        }
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
