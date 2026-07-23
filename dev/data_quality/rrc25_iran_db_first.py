#!/usr/bin/env python3
"""从固定开发数据库导出伊朗事件的最小、只读、DB-first 研究包。

本入口有意停在数据库证据层：它不读取 MRT/raw，不调用旧检测核心，也不把
聚合特征解释为物理断路、主动封锁或其他因果结论。所有业务查询都在同一个
``REPEATABLE READ READ ONLY`` 事务中执行，并在返回结果前显式回滚。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

# 同时支持 ``python -m ...`` 与直接执行本文件；不从当前工作目录猜项目根。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dev.data_quality import p0_probe


class DBFirstError(RuntimeError):
    """DB-first 输入、安全边界、数据库结果或发布过程不符合约定。"""


SCHEMA_VERSION = "rrc25-iran-db-first/v2"
INCIDENT_REF = "country_outage/2026-02-27 09:12:32/IR/1/r"
INCIDENT_SOURCE = "r"
INCIDENT_COUNTRY = "IR"
INCIDENT_COUNTRY_ZH = "伊朗"
INCIDENT_ID = 1
INCIDENT_LOCATOR_TIME = datetime(2026, 2, 27, 9, 12, 32)
EMBEDDED_EVENT_TIME = datetime(2026, 2, 28, 22, 34, 40)
WINDOW_START = datetime(2026, 2, 28, 0, 0, 0)
WINDOW_END_EXCLUSIVE = datetime(2026, 3, 6, 16, 40, 0)
BASELINE_END_EXCLUSIVE = datetime(2026, 2, 28, 6, 0, 0)
AS_HISTORY_START = datetime(2026, 2, 1, 0, 0, 0)
GRANULARITY = timedelta(minutes=5)
EXPECTED_SLOT_COUNT = 1_928
EXPECTED_LEGACY_AFFECTED_ASN = 176
EXPECTED_LEGACY_TOTAL_ASN = 556
TIMEZONE = ZoneInfo("Asia/Shanghai")
PHASE_ANCHORS = (
    ("第一段起点", datetime(2026, 2, 28, 6, 35, 0)),
    ("第二段起点", datetime(2026, 2, 28, 18, 45, 0)),
    ("第三段起点", datetime(2026, 2, 28, 22, 30, 0)),
)
REPRESENTATIVE_ASN_RULES = (
    ("48715", "旧事实历史峰值为 76/76，用于完全中断候选样本", 4),
    ("42337", "旧事实历史峰值为 468/761，用于大型部分中断候选样本", 4),
    ("39501", "旧事实历史峰值为 72/73，用于末波接近完全中断候选样本", 4),
    ("61008", "旧事实包含 IPv6 /29，用于 IPv6 候选样本", 6),
)
RAW_EVIDENCE_WINDOWS = (
    (
        "precursor_candidate",
        datetime(2026, 2, 28, 6, 30),
        datetime(2026, 2, 28, 6, 45),
    ),
    (
        "main_wave_1",
        datetime(2026, 2, 28, 18, 35),
        datetime(2026, 2, 28, 19, 0),
    ),
    (
        "main_wave_2",
        datetime(2026, 2, 28, 22, 20),
        datetime(2026, 2, 28, 22, 45),
    ),
)
REQUIRED_TABLES = {
    "as_outage_202602",
    "prefix_outage_202602",
    "feature_country",
    "feature_other_202602",
    "feature_other_202603",
    "country_outage_202602",
}


FACT_QUERY = """
    /* rrc25_iran_db_first:fact */
    SELECT "source", "country", "outage_id", "s_time", "e_time",
           "duration", "outage_level", "max_outage_as_ratio",
           "max_outage_as_num", "total_as_num", "outage_ases",
           "event_info", "country_chinese_name", "outage_level_descr"
    FROM "public"."country_outage_202602"
    WHERE "source" = %s
      AND "country" = %s
      AND "outage_id" = %s
      AND "s_time" = %s::timestamp without time zone
"""

COUNTRY_QUERY = """
    /* rrc25_iran_db_first:country_series */
    SELECT "t", "v4prefix_num", "v6prefix_num", "v4ip_num",
           "announ_num", "withdraw_num"
    FROM "public"."feature_country"
    WHERE "source" = %s
      AND "country" = %s
      AND "t" >= %s::timestamp without time zone
      AND "t" < %s::timestamp without time zone
    ORDER BY "t"
"""

AS_OUTAGE_QUERY = """
    /* rrc25_iran_db_first:as_outage_facts */
    SELECT "source", "asn", "outage_id", "s_time", "e_time",
           "max_outage_prefix_ratio", "max_outage_prefix_num",
           "total_prefix_num", "outage_level"
    FROM "public"."as_outage_202602"
    WHERE "source" = %s
      AND "country" = %s
      AND "s_time" < %s::timestamp without time zone
      AND ("e_time" IS NULL OR "e_time" > %s::timestamp without time zone)
    ORDER BY "s_time", "asn", "outage_id"
"""

PREFIX_OUTAGE_QUERY = """
    /* rrc25_iran_db_first:prefix_outage_facts */
    SELECT "source", "prefix", "outage_id", "asn", "s_time", "e_time",
           "outage_level"
    FROM "public"."prefix_outage_202602"
    WHERE "source" = %s
      AND "country" = %s
      AND "s_time" < %s::timestamp without time zone
      AND ("e_time" IS NULL OR "e_time" > %s::timestamp without time zone)
    ORDER BY "s_time", "prefix", "asn", "outage_id"
"""

AS_HISTORY_QUERY = """
    /* rrc25_iran_db_first:asn_history */
    SELECT "t", "asn", "v4prefix_num", "v6prefix_num", "v4ip_num",
           "announ_num", "withdraw_num"
    FROM "public"."feature_other_202602"
    WHERE "source" = %s
      AND "country" = %s
      AND "t" >= %s::timestamp without time zone
      AND "t" < %s::timestamp without time zone
    UNION ALL
    SELECT "t", "asn", "v4prefix_num", "v6prefix_num", "v4ip_num",
           "announ_num", "withdraw_num"
    FROM "public"."feature_other_202603"
    WHERE "source" = %s
      AND "country" = %s
      AND "t" >= %s::timestamp without time zone
      AND "t" < %s::timestamp without time zone
    ORDER BY 1, 2
"""


def _local_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=TIMEZONE)
    return value.astimezone(TIMEZONE).isoformat(timespec="seconds")


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=TIMEZONE)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _as_local_naive(value: Any, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise DBFirstError("{} 必须是 datetime".format(label))
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(TIMEZONE).replace(tzinfo=None)
    return value


def _row_values(row: Any, names: Sequence[str], label: str) -> tuple[Any, ...]:
    if isinstance(row, Mapping):
        try:
            return tuple(row[name] for name in names)
        except KeyError as error:
            raise DBFirstError("{} 缺少列 {}".format(label, error.args[0])) from error
    values = tuple(row)
    if len(values) != len(names):
        raise DBFirstError(
            "{} 列数异常：期望 {}，实际 {}".format(label, len(names), len(values))
        )
    return values


def _nonnegative_int(value: Any, label: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise DBFirstError("{} 必须是整数".format(label))
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DBFirstError("{} 必须是非负整数{}".format(label, "或 null" if nullable else ""))
    return value


def _number(value: Any, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise DBFirstError("{} 必须是数值".format(label))
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _normalize_asn(value: Any, label: str) -> str:
    text = str(value)
    if not text.isdigit() or int(text) <= 0:
        raise DBFirstError("{} 不是正整数 ASN".format(label))
    return str(int(text))


def _asn_sort_key(value: str) -> tuple[int, str]:
    return (int(value), value)


def _parse_outage_ases(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise DBFirstError("country_outage.outage_ases 不是有效 JSON") from error
    if not isinstance(value, list):
        raise DBFirstError("country_outage.outage_ases 必须是数组")
    normalized = [_normalize_asn(item, "country_outage.outage_ases") for item in value]
    if len(set(normalized)) != len(normalized):
        raise DBFirstError("country_outage.outage_ases 存在重复 ASN")
    return sorted(normalized, key=_asn_sort_key)


def _embedded_summary_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise DBFirstError("country_outage.event_info 必须是文本")
    matches = re.findall(r"\b2026-02-28[ T]22:34:40\b", value)
    if len(matches) != 1:
        raise DBFirstError(
            "country_outage.event_info 必须精确包含一次 2026-02-28 22:34:40"
        )
    parsed = datetime.strptime(
        matches[0].replace("T", " "), "%Y-%m-%d %H:%M:%S"
    )
    if parsed != EMBEDDED_EVENT_TIME:
        raise DBFirstError("country_outage.event_info 内嵌时间与冻结候选时间不一致")
    return parsed


def _normalize_fact(rows: Sequence[Any]) -> dict[str, Any]:
    if len(rows) != 1:
        raise DBFirstError(
            "固定 Incident 必须精确命中一条事实记录，实际命中 {}".format(len(rows))
        )
    names = (
        "source",
        "country",
        "outage_id",
        "s_time",
        "e_time",
        "duration",
        "outage_level",
        "max_outage_as_ratio",
        "max_outage_as_num",
        "total_as_num",
        "outage_ases",
        "event_info",
        "country_chinese_name",
        "outage_level_descr",
    )
    values = _row_values(rows[0], names, "country_outage 事实行")
    row = dict(zip(names, values))
    start = _as_local_naive(row["s_time"], "country_outage.s_time")
    if (
        row["source"] != INCIDENT_SOURCE
        or row["country"] != INCIDENT_COUNTRY
        or row["outage_id"] != INCIDENT_ID
        or start != INCIDENT_LOCATOR_TIME
    ):
        raise DBFirstError("country_outage 事实行与固定 Incident locator 不一致")
    end = (
        _as_local_naive(row["e_time"], "country_outage.e_time")
        if row["e_time"] is not None
        else None
    )
    affected = _nonnegative_int(
        row["max_outage_as_num"], "country_outage.max_outage_as_num"
    )
    total = _nonnegative_int(row["total_as_num"], "country_outage.total_as_num")
    assert affected is not None and total is not None
    if affected > total:
        raise DBFirstError("country_outage 受影响 ASN 数不得大于总 ASN 数")
    outage_ases = _parse_outage_ases(row["outage_ases"])
    ratio = _number(row["max_outage_as_ratio"], "country_outage.max_outage_as_ratio")
    duration = None if row["duration"] is None else str(row["duration"])
    summary_updated_at = _embedded_summary_time(row["event_info"])
    summary_lag_seconds = int((summary_updated_at - start).total_seconds())
    if summary_lag_seconds != 134_528:
        raise DBFirstError("旧 locator 与 event_info 时间差不再是 134528 秒")
    internal_consistency = {
        "outage_set_count_matches_affected_count": len(outage_ases) == affected,
        "ratio_matches_counts_with_rounding": (
            total > 0 and abs(float(ratio) - affected / total) <= 0.001
        ),
        "matches_expected_legacy_176_556": (
            affected == EXPECTED_LEGACY_AFFECTED_ASN
            and total == EXPECTED_LEGACY_TOTAL_ASN
        ),
    }
    return {
        "incident_ref": INCIDENT_REF,
        "source_table": "public.country_outage_202602",
        "locator": {
            "source": row["source"],
            "country": row["country"],
            "outage_id": row["outage_id"],
            "s_time_local": _local_iso(start),
        },
        "e_time_local": _local_iso(end),
        "duration_legacy": duration,
        "outage_level": row["outage_level"],
        "outage_level_descr": row["outage_level_descr"],
        "country_chinese_name": row["country_chinese_name"],
        "max_outage_as_ratio": ratio,
        "affected_asn_count": affected,
        "total_asn_count": total,
        "affected_asns": outage_ases,
        "event_info": row["event_info"],
        "event_type": "country_outage",
        "risk": {
            "level": row["outage_level"],
            "description": row["outage_level_descr"],
            "semantics": "legacy_detector_risk",
        },
        "duration": {
            "value": duration,
            "value_state": "unknown" if end is None or duration is None else "reported",
            "missing_reason": (
                "legacy_event_has_no_end_time"
                if end is None
                else "legacy_duration_absent"
                if duration is None
                else None
            ),
        },
        "temporal_semantics": {
            "legacy_detected_at_local": _local_iso(start),
            "legacy_detected_at_utc": _utc_iso(start),
            "legacy_summary_updated_at_local": _local_iso(summary_updated_at),
            "legacy_summary_updated_at_utc": _utc_iso(summary_updated_at),
            "difference_seconds": summary_lag_seconds,
            "single_event_time_merge_allowed": False,
            "relationship_state": "unresolved_not_causal",
        },
        "expected_legacy_reference": {
            "affected_asn_count": EXPECTED_LEGACY_AFFECTED_ASN,
            "total_asn_count": EXPECTED_LEGACY_TOTAL_ASN,
        },
        "internal_consistency": internal_consistency,
        "semantics": "legacy_mutable_peak_summary",
        "causal_level": "observation_only",
    }


def _expected_slots() -> list[datetime]:
    count = int((WINDOW_END_EXCLUSIVE - WINDOW_START) / GRANULARITY)
    if count != EXPECTED_SLOT_COUNT:
        raise DBFirstError("冻结窗口不再等于 1928 个五分钟槽")
    return [WINDOW_START + index * GRANULARITY for index in range(count)]


def _normalize_country_rows(rows: Iterable[Any]) -> tuple[dict[datetime, dict[str, int | None]], list[str]]:
    names = (
        "t",
        "v4prefix_num",
        "v6prefix_num",
        "v4ip_num",
        "announ_num",
        "withdraw_num",
    )
    normalized: dict[datetime, dict[str, int | None]] = {}
    off_grid: list[str] = []
    for index, raw in enumerate(rows):
        values = _row_values(raw, names, "feature_country 行 {}".format(index))
        observed_at = _as_local_naive(values[0], "feature_country.t")
        metrics = {
            name: _nonnegative_int(value, "feature_country.{}".format(name), nullable=True)
            for name, value in zip(names[1:], values[1:])
        }
        if observed_at in normalized:
            raise DBFirstError("feature_country 同一时间出现重复 IR/r 行")
        if (
            observed_at < WINDOW_START
            or observed_at >= WINDOW_END_EXCLUSIVE
            or observed_at.second
            or observed_at.microsecond
            or int((observed_at - WINDOW_START).total_seconds()) % 300
        ):
            off_grid.append(_local_iso(observed_at) or "")
            continue
        normalized[observed_at] = metrics
    return normalized, sorted(off_grid)


def _missing_ranges(missing: Sequence[datetime]) -> list[dict[str, Any]]:
    if not missing:
        return []
    ranges: list[dict[str, Any]] = []
    start = previous = missing[0]
    count = 1
    for current in missing[1:]:
        if current == previous + GRANULARITY:
            previous = current
            count += 1
            continue
        ranges.append(
            {
                "start_local": _local_iso(start),
                "end_exclusive_local": _local_iso(previous + GRANULARITY),
                "slot_count": count,
            }
        )
        start = previous = current
        count = 1
    ranges.append(
        {
            "start_local": _local_iso(start),
            "end_exclusive_local": _local_iso(previous + GRANULARITY),
            "slot_count": count,
        }
    )
    return ranges


def _country_series(rows: Iterable[Any]) -> dict[str, Any]:
    by_slot, off_grid = _normalize_country_rows(rows)
    slots = _expected_slots()
    missing = [slot for slot in slots if slot not in by_slot]
    points = []
    for slot in slots:
        metrics = by_slot.get(slot)
        points.append(
            {
                "observed_at_local": _local_iso(slot),
                "value_state": "observed" if metrics is not None else "missing",
                "missing_reason": None if metrics is not None else "database_row_absent",
                "announce_count": None if metrics is None else metrics["announ_num"],
                "withdraw_count": None if metrics is None else metrics["withdraw_num"],
                "ipv4_24_equivalent": (
                    None if metrics is None else metrics["v4prefix_num"]
                ),
                "ipv6_48_equivalent": (
                    None if metrics is None else metrics["v6prefix_num"]
                ),
                "ipv4_address_equivalent": (
                    None if metrics is None else metrics["v4ip_num"]
                ),
            }
        )
    metric_fields = (
        "announce_count",
        "withdraw_count",
        "ipv4_24_equivalent",
        "ipv6_48_equivalent",
        "ipv4_address_equivalent",
    )
    metric_value_accounting = {}
    for field in metric_fields:
        observed_values = [
            point[field] for point in points if point["value_state"] == "observed"
        ]
        null_count = sum(value is None for value in observed_values)
        zero_count = sum(value == 0 for value in observed_values if value is not None)
        metric_value_accounting[field] = {
            "observed_row_count": len(observed_values),
            "null_count": null_count,
            "zero_count": zero_count,
            "nonzero_count": len(observed_values) - null_count - zero_count,
        }
    return {
        "source": "public.feature_country/source=r/country=伊朗",
        "granularity_seconds": 300,
        "window_semantics": "half_open",
        "coverage": {
            "expected_slot_count": EXPECTED_SLOT_COUNT,
            "observed_slot_count": len(by_slot),
            "missing_slot_count": len(missing),
            "off_grid_row_count": len(off_grid),
            "coverage_ratio": round(len(by_slot) / EXPECTED_SLOT_COUNT, 12),
            "missing_ranges": _missing_ranges(missing),
            "off_grid_samples": off_grid[:20],
            "status": "complete" if not missing and not off_grid else "observed_gap",
            "metric_value_accounting": metric_value_accounting,
        },
        "metric_semantics": {
            "announce_count": "该五分钟槽旧处理器累计 ANNOUNCE 数",
            "withdraw_count": "该五分钟槽旧处理器累计 WITHDRAW 数",
            "ipv4_24_equivalent": "旧算法折算的 IPv4 /24 等价值，不是原始前缀条目数",
            "ipv6_48_equivalent": "旧算法折算的 IPv6 /48 等价值，不是原始前缀条目数",
            "ipv4_address_equivalent": "旧算法 IPv4 /24 等价值乘 256，不是去重地址并集",
            "cross_family_sum_allowed": False,
            "unknown_is_zero": False,
        },
        "points": points,
    }


def _normalize_prefix(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise DBFirstError("{} 必须是文本前缀".format(label))
    try:
        return str(ipaddress.ip_network(value, strict=False))
    except ValueError as error:
        raise DBFirstError("{} 不是有效 IP 前缀：{}".format(label, value)) from error


def _normalize_as_outage_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    names = (
        "source",
        "asn",
        "outage_id",
        "s_time",
        "e_time",
        "max_outage_prefix_ratio",
        "max_outage_prefix_num",
        "total_prefix_num",
        "outage_level",
    )
    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, int]] = set()
    previous_time: datetime | None = None
    for index, raw in enumerate(rows):
        values = _row_values(raw, names, "as_outage 行 {}".format(index))
        if values[0] != INCIDENT_SOURCE:
            raise DBFirstError("as_outage.source 不是固定 r")
        asn = _normalize_asn(values[1], "as_outage.asn")
        outage_id = _nonnegative_int(values[2], "as_outage.outage_id")
        assert outage_id is not None
        identity = (asn, outage_id)
        if identity in identities:
            raise DBFirstError("as_outage 固定主键重复")
        identities.add(identity)
        start = _as_local_naive(values[3], "as_outage.s_time")
        end = (
            _as_local_naive(values[4], "as_outage.e_time")
            if values[4] is not None
            else None
        )
        if end is not None and end < start:
            raise DBFirstError("as_outage.e_time 早于 s_time")
        if start >= datetime(2026, 3, 1) or (
            end is not None and end <= datetime(2026, 2, 28)
        ):
            raise DBFirstError("as_outage 查询返回未与研究日相交的行")
        if previous_time is not None and start < previous_time:
            raise DBFirstError("as_outage 行未按 s_time 排序")
        previous_time = start
        ratio = _number(values[5], "as_outage.max_outage_prefix_ratio")
        if not 0 <= float(ratio) <= 1:
            raise DBFirstError("as_outage.max_outage_prefix_ratio 超出 [0,1]")
        maximum = _nonnegative_int(
            values[6], "as_outage.max_outage_prefix_num"
        )
        total = _nonnegative_int(values[7], "as_outage.total_prefix_num")
        assert maximum is not None and total is not None
        if maximum > total:
            raise DBFirstError("as_outage 最大中断前缀数大于总前缀数")
        normalized.append(
            {
                "source": values[0],
                "asn": asn,
                "outage_id": outage_id,
                "s_time": start,
                "e_time": end,
                "max_outage_prefix_ratio": ratio,
                "max_outage_prefix_num": maximum,
                "total_prefix_num": total,
                "outage_level": values[8],
            }
        )
    return normalized


def _normalize_prefix_outage_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    names = (
        "source",
        "prefix",
        "outage_id",
        "asn",
        "s_time",
        "e_time",
        "outage_level",
    )
    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, int, str]] = set()
    previous_time: datetime | None = None
    for index, raw in enumerate(rows):
        values = _row_values(raw, names, "prefix_outage 行 {}".format(index))
        if values[0] != INCIDENT_SOURCE:
            raise DBFirstError("prefix_outage.source 不是固定 r")
        prefix = _normalize_prefix(values[1], "prefix_outage.prefix")
        outage_id = _nonnegative_int(values[2], "prefix_outage.outage_id")
        assert outage_id is not None
        asn = _normalize_asn(values[3], "prefix_outage.asn")
        identity = (prefix, outage_id, asn)
        if identity in identities:
            raise DBFirstError("prefix_outage 固定主键重复")
        identities.add(identity)
        start = _as_local_naive(values[4], "prefix_outage.s_time")
        end = (
            _as_local_naive(values[5], "prefix_outage.e_time")
            if values[5] is not None
            else None
        )
        if end is not None and end < start:
            raise DBFirstError("prefix_outage.e_time 早于 s_time")
        if start >= datetime(2026, 3, 1) or (
            end is not None and end <= datetime(2026, 2, 28)
        ):
            raise DBFirstError("prefix_outage 查询返回未与研究日相交的行")
        if previous_time is not None and start < previous_time:
            raise DBFirstError("prefix_outage 行未按 s_time 排序")
        previous_time = start
        normalized.append(
            {
                "source": values[0],
                "prefix": prefix,
                "outage_id": outage_id,
                "asn": asn,
                "s_time": start,
                "e_time": end,
                "outage_level": values[6],
            }
        )
    return normalized


def _active_at(row: Mapping[str, Any], anchor: datetime) -> bool:
    return row["s_time"] <= anchor and (
        row["e_time"] is None or anchor < row["e_time"]
    )


def _prefix_sort_key(value: str) -> tuple[int, int, int]:
    network = ipaddress.ip_network(value)
    return (network.version, int(network.network_address), network.prefixlen)


def _fact_bucket(
    as_rows: Sequence[Mapping[str, Any]],
    prefix_rows: Sequence[Mapping[str, Any]],
    *,
    label: str,
    start: datetime,
) -> dict[str, Any]:
    end = start + GRANULARITY
    selected_as = [row for row in as_rows if start <= row["s_time"] < end]
    selected_prefix = [
        row for row in prefix_rows if start <= row["s_time"] < end
    ]
    asns = {row["asn"] for row in selected_as}
    prefixes = {row["prefix"] for row in selected_prefix}
    prefix_asns = {row["asn"] for row in selected_prefix}
    return {
        "label": label,
        "start_local": _local_iso(start),
        "end_exclusive_local": _local_iso(end),
        "as_outage_fact_count": len(selected_as),
        "as_outage_unique_asn_count": len(asns),
        "prefix_outage_fact_count": len(selected_prefix),
        "prefix_outage_unique_prefix_count": len(prefixes),
        "prefix_outage_unique_asn_count": len(prefix_asns),
        "as_outage_asns": sorted(asns, key=_asn_sort_key),
        "prefix_outage_asns": sorted(prefix_asns, key=_asn_sort_key),
        "semantics": "fact_start_time_in_half_open_five_minute_bucket",
    }


def _event_fact_analysis(
    as_rows: Iterable[Any],
    prefix_rows: Iterable[Any],
    fact: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized_as = _normalize_as_outage_rows(as_rows)
    normalized_prefix = _normalize_prefix_outage_rows(prefix_rows)
    active_as_rows = [
        row for row in normalized_as if _active_at(row, EMBEDDED_EVENT_TIME)
    ]
    active_prefix_rows = [
        row for row in normalized_prefix if _active_at(row, EMBEDDED_EVENT_TIME)
    ]
    ratios_by_asn: dict[str, float] = {}
    for row in active_as_rows:
        ratios_by_asn[row["asn"]] = max(
            ratios_by_asn.get(row["asn"], 0.0),
            float(row["max_outage_prefix_ratio"]),
        )
    active_asns = set(ratios_by_asn)
    full = {asn for asn, ratio in ratios_by_asn.items() if ratio == 1.0}
    partial = {
        asn for asn, ratio in ratios_by_asn.items() if 0.0 < ratio < 1.0
    }
    unclassified = active_asns - full - partial
    active_prefixes = {row["prefix"] for row in active_prefix_rows}
    active_prefix_asn_pairs = {
        (row["prefix"], row["asn"]) for row in active_prefix_rows
    }
    prefix_asns = {row["asn"] for row in active_prefix_rows}
    representative_candidates = []
    for asn, reason, preferred_family in REPRESENTATIVE_ASN_RULES:
        matching_as_rows = [row for row in active_as_rows if row["asn"] == asn]
        matching_prefixes = sorted(
            {
                row["prefix"]
                for row in active_prefix_rows
                if row["asn"] == asn
                and ipaddress.ip_network(row["prefix"]).version == preferred_family
            },
            key=_prefix_sort_key,
        )
        selected_as_row = (
            max(
                matching_as_rows,
                key=lambda row: (
                    float(row["max_outage_prefix_ratio"]),
                    row["max_outage_prefix_num"],
                    row["outage_id"],
                ),
            )
            if matching_as_rows
            else None
        )
        representative_candidates.append(
            {
                "asn": asn,
                "selection_reason_zh": reason,
                "preferred_ip_family": preferred_family,
                "selection_state": (
                    "ready"
                    if selected_as_row is not None and matching_prefixes
                    else "database_candidate_missing"
                ),
                "selected_prefix": (
                    matching_prefixes[0] if matching_prefixes else None
                ),
                "active_matching_prefix_count": len(matching_prefixes),
                "legacy_peak_outage_prefix_ratio": (
                    selected_as_row["max_outage_prefix_ratio"]
                    if selected_as_row is not None
                    else None
                ),
                "legacy_peak_outage_prefix_num": (
                    selected_as_row["max_outage_prefix_num"]
                    if selected_as_row is not None
                    else None
                ),
                "legacy_total_prefix_num": (
                    selected_as_row["total_prefix_num"]
                    if selected_as_row is not None
                    else None
                ),
            }
        )
    country_asns = set(fact["affected_asns"])
    missing_from_as_facts = country_asns - active_asns
    extra_in_as_facts = active_asns - country_asns
    repeated_prefix_asn_fact_count = (
        len(active_prefix_rows) - len(active_prefix_asn_pairs)
    )
    buckets = [
        _fact_bucket(
            normalized_as,
            normalized_prefix,
            label=label,
            start=anchor,
        )
        for label, anchor in PHASE_ANCHORS
    ]
    analysis = {
        "event_anchor_local": _local_iso(EMBEDDED_EVENT_TIME),
        "event_anchor_utc": _utc_iso(EMBEDDED_EVENT_TIME),
        "source_tables": [
            "public.as_outage_202602",
            "public.prefix_outage_202602",
        ],
        "as_outage": {
            "fact_row_count": len(active_as_rows),
            "unique_asn_count": len(active_asns),
            "active_asns": sorted(active_asns, key=_asn_sort_key),
            "legacy_peak_ratio_1_asn_count": len(full),
            "legacy_peak_ratio_1_asns": sorted(full, key=_asn_sort_key),
            "legacy_peak_ratio_between_0_and_1_asn_count": len(partial),
            "legacy_peak_ratio_between_0_and_1_asns": sorted(
                partial, key=_asn_sort_key
            ),
            "unclassified_asn_count": len(unclassified),
            "classification_rule": (
                "仅在锚点仍活跃的 AS 事实中读取生命周期内持久化的 "
                "max_outage_prefix_ratio：1 与 0 到 1 之间分别分组；"
                "多行取同 ASN 最大值。该值不是锚点当刻比例。"
            ),
        },
        "prefix_outage": {
            "fact_row_count": len(active_prefix_rows),
            "unique_prefix_count": len(active_prefixes),
            "distinct_prefix_asn_count": len(active_prefix_asn_pairs),
            "repeated_prefix_asn_fact_count": repeated_prefix_asn_fact_count,
            "unique_asn_count": len(prefix_asns),
            "unique_prefixes": sorted(active_prefixes, key=_prefix_sort_key),
            "unique_asns": sorted(prefix_asns, key=_asn_sort_key),
            "semantics": (
                "active_legacy_prefix_outage_fact_at_anchor；研究主计数使用 "
                "distinct(prefix,asn)，同时单列仅按 prefix 去重的计数"
            ),
        },
        "representative_entity_candidates": representative_candidates,
        "country_outage_bidirectional_reconciliation": {
            "country_fact_asn_count": len(country_asns),
            "as_fact_asn_count": len(active_asns),
            "intersection_count": len(country_asns & active_asns),
            "missing_from_as_outage_facts": sorted(
                missing_from_as_facts, key=_asn_sort_key
            ),
            "extra_in_as_outage_facts": sorted(
                extra_in_as_facts, key=_asn_sort_key
            ),
            "exact_set_match": not missing_from_as_facts and not extra_in_as_facts,
        },
        "expected_reference": {
            "active_asn_count": 176,
            "active_prefix_fact_count": 2_883,
            "active_distinct_prefix_asn_count": 2_842,
            "legacy_peak_ratio_1_asn_count": 97,
            "legacy_peak_ratio_between_0_and_1_asn_count": 79,
        },
        "reference_matches": {
            "active_asn_176": len(active_asns) == 176,
            "prefix_fact_2883": len(active_prefix_rows) == 2_883,
            "distinct_prefix_asn_2842": len(active_prefix_asn_pairs) == 2_842,
            "legacy_peak_ratio_groups_97_79": (
                len(full) == 97 and len(partial) == 79
            ),
        },
        "status": (
            "exactly_reconciled"
            if not missing_from_as_facts
            and not extra_in_as_facts
            and not unclassified
            else "not_exactly_reconciled"
        ),
        "causal_level": "observation_only",
        "limitations_zh": [
            "这里的活跃表示旧 outage 事实的时间区间覆盖锚点，不是健康路由的活跃集合。",
            "同一 prefix 的多条事实可能来自不同 ASN、事件 ID 或 MOAS；因此同时保留事实行数和唯一前缀数。",
            "旧事实仍不提供可独立验证的 VP 连续性或原始 MRT 坐标。",
        ],
    }
    return analysis, buckets


def _normalize_as_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    names = (
        "t",
        "asn",
        "v4prefix_num",
        "v6prefix_num",
        "v4ip_num",
        "announ_num",
        "withdraw_num",
    )
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[datetime, str]] = set()
    previous_time: datetime | None = None
    for index, raw in enumerate(rows):
        values = _row_values(raw, names, "feature_other 行 {}".format(index))
        observed_at = _as_local_naive(values[0], "feature_other.t")
        if observed_at < AS_HISTORY_START or observed_at >= WINDOW_END_EXCLUSIVE:
            raise DBFirstError("feature_other 查询返回冻结历史范围外行")
        asn = _normalize_asn(values[1], "feature_other.asn")
        identity = (observed_at, asn)
        if identity in seen:
            raise DBFirstError("feature_other 同一时间和 ASN 出现重复行")
        seen.add(identity)
        # ASN 是 text，数据库在同一时刻按字典序返回；这里只要求时间有序。
        if previous_time is not None and observed_at < previous_time:
            raise DBFirstError("feature_other 行未按时间排序")
        previous_time = observed_at
        normalized.append(
            {
                "t": observed_at,
                "asn": asn,
                "v4prefix_num": _nonnegative_int(
                    values[2], "feature_other.v4prefix_num", nullable=True
                ),
                "v6prefix_num": _nonnegative_int(
                    values[3], "feature_other.v6prefix_num", nullable=True
                ),
                "v4ip_num": _nonnegative_int(
                    values[4], "feature_other.v4ip_num", nullable=True
                ),
                "announ_num": _nonnegative_int(
                    values[5], "feature_other.announ_num", nullable=True
                ),
                "withdraw_num": _nonnegative_int(
                    values[6], "feature_other.withdraw_num", nullable=True
                ),
            }
        )
    return normalized


def _state_snapshot(rows: Sequence[dict[str, Any]], at: datetime) -> dict[str, Any]:
    state: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["t"] > at:
            break
        state[row["asn"]] = row
    active: list[str] = []
    inactive: list[str] = []
    unknown: list[str] = []
    for asn, row in state.items():
        v4 = row["v4prefix_num"]
        v6 = row["v6prefix_num"]
        if (v4 is not None and v4 > 0) or (v6 is not None and v6 > 0):
            active.append(asn)
        elif v4 == 0 and v6 == 0:
            inactive.append(asn)
        else:
            unknown.append(asn)
    return {
        "snapshot_at_local": _local_iso(at),
        "active_asns": sorted(active, key=_asn_sort_key),
        "inactive_asns": sorted(inactive, key=_asn_sort_key),
        "unknown_state_asns": sorted(unknown, key=_asn_sort_key),
        "known_asn_count": len(state),
        "active_asn_count": len(active),
        "inactive_asn_count": len(inactive),
        "unknown_state_asn_count": len(unknown),
        "state_semantics": "last_changed_row_carried_forward_since_month_start",
        "population_semantics": "lower_bound_without_month_start_full_seed",
    }


def _sparse_feature_auxiliary(
    rows: Iterable[Any], fact: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = _normalize_as_rows(rows)
    before_at = datetime(2026, 2, 28, 22, 30, 0)
    after_at = datetime(2026, 2, 28, 22, 35, 0)
    before = _state_snapshot(normalized, before_at)
    after = _state_snapshot(normalized, after_at)
    before_set = set(before["active_asns"])
    after_set = set(after["active_asns"])
    fact_set = set(fact["affected_asns"])
    became_inactive = before_set - after_set
    overlap = became_inactive & fact_set
    fact_not_observed_in_transition = fact_set - became_inactive
    transition_not_in_fact = became_inactive - fact_set
    reconciliation = {
        "event_anchor_local": _local_iso(EMBEDDED_EVENT_TIME),
        "event_anchor_utc": _utc_iso(EMBEDDED_EVENT_TIME),
        "sampling_rule": (
            "22:34:40 不在五分钟网格；用最近闭合槽 22:30 与下一槽 22:35 "
            "对账，不把两槽之间的变化强行定位到某一秒。"
        ),
        "before_snapshot": before,
        "after_snapshot": after,
        "became_inactive_asns": sorted(became_inactive, key=_asn_sort_key),
        "became_inactive_asn_count": len(became_inactive),
        "fact_affected_asns": sorted(fact_set, key=_asn_sort_key),
        "fact_affected_asn_count": len(fact_set),
        "intersection_asns": sorted(overlap, key=_asn_sort_key),
        "intersection_count": len(overlap),
        "fact_asns_not_observed_in_transition": sorted(
            fact_not_observed_in_transition, key=_asn_sort_key
        ),
        "transition_asns_not_in_fact": sorted(
            transition_not_in_fact, key=_asn_sort_key
        ),
        "legacy_denominator": fact["total_asn_count"],
        "observed_before_active_lower_bound": before["active_asn_count"],
        "status": (
            "consistent_lower_bound"
            if became_inactive == fact_set
            and before["active_asn_count"] == fact["total_asn_count"]
            and before["unknown_state_asn_count"] == 0
            else "not_exactly_reconciled"
        ),
        "causal_level": "observation_only",
        "limitations_zh": [
            "ASN 月表只保存发生变化的 ASN 行；从月初携带最后状态仍缺少完整 RIB seed，因此活跃集合是下界。",
            "事实表 outage_ases 是旧检测器某次峰值快照，不保证与 22:30 或 22:35 的分母同快照。",
            "集合变化不证明主动撤回、会话关闭、物理断路或政府行为。",
        ],
    }
    snapshots = [
        _state_snapshot(normalized, anchor)
        for anchor in (
            PHASE_ANCHORS[0][1],
            PHASE_ANCHORS[1][1],
            PHASE_ANCHORS[2][1],
        )
    ]
    return reconciliation, snapshots


def _point_index(series: Mapping[str, Any]) -> dict[datetime, Mapping[str, Any]]:
    result: dict[datetime, Mapping[str, Any]] = {}
    for point in series["points"]:
        value = point["observed_at_local"]
        observed_at = datetime.fromisoformat(value).astimezone(TIMEZONE).replace(tzinfo=None)
        result[observed_at] = point
    return result


def _metric_values(
    points: Iterable[Mapping[str, Any]], field: str
) -> list[int]:
    return [
        int(point[field])
        for point in points
        if point["value_state"] == "observed" and point[field] is not None
    ]


def _segment_summary(
    index: Mapping[datetime, Mapping[str, Any]],
    *,
    name: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    points = [
        index[slot]
        for slot in sorted(index)
        if start <= slot < end and index[slot]["value_state"] == "observed"
    ]
    expected = int((end - start) / GRANULARITY)
    output: dict[str, Any] = {
        "name": name,
        "start_local": _local_iso(start),
        "end_exclusive_local": _local_iso(end),
        "expected_slot_count": expected,
        "observed_slot_count": len(points),
        "missing_slot_count": expected - len(points),
    }
    for field in (
        "announce_count",
        "withdraw_count",
        "ipv4_24_equivalent",
        "ipv6_48_equivalent",
    ):
        values = _metric_values(points, field)
        output[field] = {
            "observed_value_count": len(values),
            "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
            "sum": (
                sum(values)
                if values and field in {"announce_count", "withdraw_count"}
                else None
            ),
        }
    return output


def _phase_analysis(
    series: Mapping[str, Any],
    asn_snapshots: Sequence[Mapping[str, Any]],
    fact_buckets: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    index = _point_index(series)
    anchor_records = []
    for (label, anchor), asn_snapshot, fact_bucket in zip(
        PHASE_ANCHORS, asn_snapshots, fact_buckets
    ):
        anchor_records.append(
            {
                "label": label,
                "observed_at_local": _local_iso(anchor),
                "country_metrics": index.get(anchor),
                "active_asn_lower_bound": asn_snapshot["active_asn_count"],
                "known_asn_count": asn_snapshot["known_asn_count"],
                "outage_fact_bucket": dict(fact_bucket),
                "value_state": (
                    "observed"
                    if index.get(anchor, {}).get("value_state") == "observed"
                    else "missing"
                ),
                "causal_level": "observation_only",
            }
        )
    return {
        "anchors": anchor_records,
        "segments": [
            _segment_summary(
                index,
                name="06:35—18:45 早期变化段",
                start=PHASE_ANCHORS[0][1],
                end=PHASE_ANCHORS[1][1],
            ),
            _segment_summary(
                index,
                name="18:45—22:30 加剧段",
                start=PHASE_ANCHORS[1][1],
                end=PHASE_ANCHORS[2][1],
            ),
            _segment_summary(
                index,
                name="22:30—窗口末 主中断与恢复观察段",
                start=PHASE_ANCHORS[2][1],
                end=WINDOW_END_EXCLUSIVE,
            ),
        ],
        "interpretation": (
            "三个时间仅作为报告驱动的观察锚点；本导出器不据此预设 episode、"
            "wave、前兆关系或因果关系。"
        ),
    }


def _median(values: Sequence[int]) -> int | float | None:
    if not values:
        return None
    value = statistics.median(values)
    return int(value) if float(value).is_integer() else float(value)


def _metric_findings(series: Mapping[str, Any]) -> dict[str, Any]:
    index = _point_index(series)
    baseline_points = [
        point
        for slot, point in index.items()
        if WINDOW_START <= slot < BASELINE_END_EXCLUSIVE
        and point["value_state"] == "observed"
    ]
    baselines = {
        field: _median(_metric_values(baseline_points, field))
        for field in (
            "ipv4_address_equivalent",
            "ipv4_24_equivalent",
            "ipv6_48_equivalent",
        )
    }
    baseline_v4_address = baselines["ipv4_address_equivalent"]

    def observation(label: str, at: datetime) -> dict[str, Any]:
        point = index.get(at)
        value = (
            point.get("ipv4_address_equivalent")
            if point is not None and point.get("value_state") == "observed"
            else None
        )
        percent = (
            round(100.0 * value / float(baseline_v4_address), 3)
            if value is not None
            and baseline_v4_address is not None
            and baseline_v4_address > 0
            else None
        )
        return {
            "label": label,
            "observed_at_local": _local_iso(at),
            "observed_at_utc": _utc_iso(at),
            "value_state": (
                "observed"
                if point is not None and point.get("value_state") == "observed"
                else "missing"
            ),
            "ipv4_address_equivalent": value,
            "ipv4_percent_of_baseline": percent,
            "ipv4_decline_percent": (
                round(100.0 - percent, 3) if percent is not None else None
            ),
            "announce_count": point.get("announce_count") if point else None,
            "withdraw_count": point.get("withdraw_count") if point else None,
        }

    activity_start = datetime(2026, 2, 28, 17, 0)
    activity_end = datetime(2026, 2, 28, 19, 0)
    activity_points = [
        point
        for slot, point in index.items()
        if activity_start <= slot < activity_end
        and point["value_state"] == "observed"
    ]
    announce_values = _metric_values(activity_points, "announce_count")
    withdraw_values = _metric_values(activity_points, "withdraw_count")
    observations = [
        observation("候选早期扰动低点", datetime(2026, 2, 28, 6, 40)),
        observation("报告 16:14 主张邻近槽", datetime(2026, 2, 28, 16, 15)),
        observation("第一显著波次低点", datetime(2026, 2, 28, 18, 45)),
        observation("第二显著波次低点", datetime(2026, 2, 28, 22, 30)),
        observation("后续观察点", datetime(2026, 3, 3, 8, 35)),
        observation("半开窗口最后槽", datetime(2026, 3, 6, 16, 35)),
    ]
    by_label = {item["label"]: item for item in observations}
    activity_announce = sum(announce_values) if announce_values else None
    activity_withdraw = sum(withdraw_values) if withdraw_values else None
    return {
        "baseline": {
            "window_start_local": _local_iso(WINDOW_START),
            "window_end_exclusive_local": _local_iso(BASELINE_END_EXCLUSIVE),
            "statistic": "median",
            "observed_slot_count": len(baseline_points),
            "values": baselines,
        },
        "activity_window_17_19": {
            "start_local": _local_iso(activity_start),
            "end_exclusive_local": _local_iso(activity_end),
            "expected_slot_count": 24,
            "observed_slot_count": len(activity_points),
            "announce_count_sum": activity_announce,
            "withdraw_count_sum": activity_withdraw,
        },
        "observations": observations,
        "expected_reference": {
            "ipv4_address_equivalent_baseline": 10_146_432,
            "activity_17_19_announce_count": 4_354_758,
            "activity_17_19_withdraw_count": 210_811,
            "early_decline_percent": 0.675,
            "first_wave_decline_percent": 3.667,
            "second_wave_decline_percent": 5.691,
        },
        "reference_matches": {
            "baseline": baseline_v4_address == 10_146_432,
            "activity_17_19": (
                activity_announce == 4_354_758
                and activity_withdraw == 210_811
            ),
            "declines": (
                by_label["候选早期扰动低点"]["ipv4_decline_percent"] == 0.675
                and by_label["第一显著波次低点"]["ipv4_decline_percent"]
                == 3.667
                and by_label["第二显著波次低点"]["ipv4_decline_percent"]
                == 5.691
            ),
        },
        "semantics": (
            "IPv4 地址等值来自旧 v4ip_num（/24 等值乘 256），"
            "不是逐前缀去重地址并集；所有百分比仅在同一 IPv4 单位内计算。"
        ),
    }


def _recovery_candidate(series: Mapping[str, Any]) -> dict[str, Any]:
    index = _point_index(series)
    baseline_points = [
        point
        for slot, point in index.items()
        if WINDOW_START <= slot < BASELINE_END_EXCLUSIVE
        and point["value_state"] == "observed"
    ]
    baseline_v4 = _median(_metric_values(baseline_points, "ipv4_24_equivalent"))
    baseline_v6 = _median(_metric_values(baseline_points, "ipv6_48_equivalent"))
    baseline_v4_address = _median(
        _metric_values(baseline_points, "ipv4_address_equivalent")
    )
    dimensions = [
        ("ipv4_24_equivalent", baseline_v4),
        ("ipv6_48_equivalent", baseline_v6),
    ]
    usable = [(field, value) for field, value in dimensions if value is not None and value > 0]
    candidate: datetime | None = None
    confirmation_end: datetime | None = None
    search_start = PHASE_ANCHORS[2][1]
    slots = [
        slot
        for slot in sorted(index)
        if search_start <= slot < WINDOW_END_EXCLUSIVE
    ]
    consecutive = 0
    run_start: datetime | None = None
    for slot in slots:
        point = index[slot]
        qualifies = point["value_state"] == "observed" and bool(usable)
        if qualifies:
            for field, baseline in usable:
                value = point[field]
                if value is None or value < float(baseline) * 0.99:
                    qualifies = False
                    break
        if qualifies:
            if consecutive == 0:
                run_start = slot
            consecutive += 1
            if consecutive == 6:
                candidate = run_start
                confirmation_end = slot + GRANULARITY
                break
        else:
            consecutive = 0
            run_start = None
    return {
        "status": "candidate_observed" if candidate is not None else "not_observed",
        "candidate_at_local": _local_iso(candidate),
        "confirmation_end_exclusive_local": _local_iso(confirmation_end),
        "rule": {
            "baseline_window_local": {
                "start": _local_iso(WINDOW_START),
                "end_exclusive": _local_iso(BASELINE_END_EXCLUSIVE),
            },
            "baseline_statistic": "median",
            "threshold_ratio": 0.99,
            "required_consecutive_slots": 6,
            "usable_dimensions": [field for field, _ in usable],
            "ipv4_24_equivalent_baseline": baseline_v4,
            "ipv4_address_equivalent_baseline": baseline_v4_address,
            "ipv6_48_equivalent_baseline": baseline_v6,
        },
        "semantics": "database_aggregate_recovery_candidate_not_full_network_recovery",
        "causal_level": "observation_only",
        "limitations_zh": [
            "恢复候选只来自 feature_country 聚合曲线，不含 VP 连续性和逐前缀状态。",
            "达到 99% 基线仅是研究候选，不能证明全网、流量或业务已经恢复。",
        ],
    }


def _gap_matrix(
    series: Mapping[str, Any],
    event_facts: Mapping[str, Any],
    sparse_auxiliary: Mapping[str, Any],
    fact: Mapping[str, Any],
) -> list[dict[str, Any]]:
    coverage = series["coverage"]
    return [
        {
            "evidence_layer": "固定旧事实",
            "status": "available",
            "available": "locator、176/556 候选、旧峰值受影响 ASN 集合",
            "missing": "同快照分子分母、不可变峰值历史",
        },
        {
            "evidence_layer": "国家五分钟聚合",
            "status": (
                "available"
                if coverage["status"] == "complete"
                else "partial"
            ),
            "available": "ANNOUNCE、WITHDRAW、IPv4 /24 等值、IPv6 /48 等值",
            "missing": "{} 个数据库槽及原始前缀列表".format(
                coverage["missing_slot_count"]
            ),
        },
        {
            "evidence_layer": "AS 中断事实",
            "status": (
                "available"
                if event_facts["status"] == "exactly_reconciled"
                else "partial"
            ),
            "available": (
                "锚点活跃事实 {} 个唯一 ASN；其中旧生命周期峰值比例"
                "等于 1 / 介于 0 与 1 为 {}/{}"
            ).format(
                event_facts["as_outage"]["unique_asn_count"],
                event_facts["as_outage"]["legacy_peak_ratio_1_asn_count"],
                event_facts["as_outage"][
                    "legacy_peak_ratio_between_0_and_1_asn_count"
                ],
            ),
            "missing": "锚点当刻比例、稳定 VP 与逐报文复核",
        },
        {
            "evidence_layer": "Prefix 中断事实",
            "status": "available",
            "available": "{} 条事实、{} 个 distinct(prefix,asn)、{} 个唯一前缀、{} 个唯一 ASN".format(
                event_facts["prefix_outage"]["fact_row_count"],
                event_facts["prefix_outage"]["distinct_prefix_asn_count"],
                event_facts["prefix_outage"]["unique_prefix_count"],
                event_facts["prefix_outage"]["unique_asn_count"],
            ),
            "missing": "MOAS/重复事实的原始路径关系与 raw 坐标",
        },
        {
            "evidence_layer": "ASN 稀疏特征旁证",
            "status": "partial",
            "available": "月初以来最后变化行携带的活跃 ASN 下界",
            "missing": "月初完整 RIB seed；22:30 下界为 {}，旧分母为 {}".format(
                sparse_auxiliary["observed_before_active_lower_bound"],
                fact["total_asn_count"],
            ),
        },
        {
            "evidence_layer": "观测点与路径",
            "status": "partial",
            "available": (
                "旧 AS/Prefix 事实表保存 pre/eve AS_PATH 快照；"
                "本包不把该结构解释成稳定观测点"
            ),
            "missing": (
                "稳定 collector/VP/peer 身份、raw 坐标、同快照覆盖，"
                "以及覆盖不足的 next/恢复路径"
            ),
        },
        {
            "evidence_layer": "原始记录与因果",
            "status": "missing",
            "available": "本导出器明确未访问 raw",
            "missing": "MRT record、raw hash、主动撤回/物理断路/意图证明",
        },
    ]


def _minimal_raw_request(event_facts: Mapping[str, Any]) -> dict[str, Any]:
    representatives = event_facts["representative_entity_candidates"]
    ready = [item for item in representatives if item["selection_state"] == "ready"]
    update_slots = []
    evidence_windows = []
    for name, start, end in RAW_EVIDENCE_WINDOWS:
        slots = []
        current = start
        while current < end:
            slots.append(
                {
                    "local": _local_iso(current),
                    "utc": _utc_iso(current),
                }
            )
            current += GRANULARITY
        update_slots.extend(slots)
        evidence_windows.append(
            {
                "name": name,
                "start_local": _local_iso(start),
                "end_exclusive_local": _local_iso(end),
                "start_utc": _utc_iso(start),
                "end_exclusive_utc": _utc_iso(end),
                "update_slot_count": len(slots),
            }
        )
    return {
        "status": (
            "not_executed"
            if len(ready) == len(REPRESENTATIVE_ASN_RULES)
            else "blocked_missing_database_candidate"
        ),
        "purpose": "仅补齐关键槽和代表实体的 VP/prefix/raw 证据，不进行全窗口 A/B 复现",
        "critical_slots": [
            {
                "local": _local_iso(slot),
                "utc": _utc_iso(slot),
            }
            for _, slot in PHASE_ANCHORS
        ],
        "evidence_windows": evidence_windows,
        "update_slots": update_slots,
        "representative_entities": representatives,
        "representative_asns": [item["asn"] for item in ready],
        "representative_prefixes": [
            item["selected_prefix"] for item in ready if item["selected_prefix"]
        ],
        "representative_prefix_source": (
            "按批准的四类 ASN 从 public.prefix_outage_202602 的锚点活跃事实中，"
            "按指定地址族、网络地址和前缀长度确定性选择"
        ),
        "requested_fields": [
            "collector_id",
            "vp_id",
            "vp_asn",
            "afi_safi",
            "prefix",
            "announcement_or_withdrawal",
            "as_path",
            "raw_record_ref",
            "artifact_sha256",
        ],
        "causal_claim_allowed": False,
        "scope_limit": {
            "full_window_replay": False,
            "all_asn_population": False,
            "only_key_slots_and_representative_entities": True,
            "maximum_update_slot_count": len(update_slots),
            "initial_rib_read_requested": False,
            "state_seed_requires_separate_gap_justification": True,
        },
    }


def _validate_fixed_profile(profile: Mapping[str, Any]) -> None:
    if profile.get("timezone") != "Asia/Shanghai":
        raise DBFirstError("唯一 data-profile 时区不是 Asia/Shanghai")
    parsed = profile.get("parsed")
    if not isinstance(parsed, Mapping):
        raise DBFirstError("唯一 data-profile 缺少 parsed 窗口")
    start = parsed.get("start")
    end = parsed.get("end_exclusive")
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise DBFirstError("唯一 data-profile parsed 窗口非法")
    fixed_start = WINDOW_START.replace(tzinfo=TIMEZONE)
    fixed_end = WINDOW_END_EXCLUSIVE.replace(tzinfo=TIMEZONE)
    if start.tzinfo is None:
        start = start.replace(tzinfo=TIMEZONE)
    if end.tzinfo is None:
        end = end.replace(tzinfo=TIMEZONE)
    if start.astimezone(TIMEZONE) > fixed_start or end.astimezone(TIMEZONE) < fixed_end:
        raise DBFirstError("固定伊朗研究窗口不在唯一 data-profile 覆盖范围内")


def _validate_context_tables(context: Mapping[str, Any]) -> None:
    hashes = context.get("legacy_schema_hashes")
    if not isinstance(hashes, Mapping):
        raise DBFirstError("发布 context 缺少 legacy_schema_hashes")
    missing = sorted(REQUIRED_TABLES - set(hashes))
    if missing:
        raise DBFirstError("发布 inventory 缺少 DB-first 必需表：{}".format(", ".join(missing)))


FINGERPRINT_FIELDS = (
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


def _content_fingerprint(payload: Mapping[str, Any]) -> str:
    missing = [field for field in FINGERPRINT_FIELDS if field not in payload]
    if missing:
        raise DBFirstError(
            "语义指纹输入缺少稳定研究字段：{}".format(", ".join(missing))
        )
    stable = {field: payload[field] for field in FINGERPRINT_FIELDS}
    return p0_probe._canonical_sha256(
        {
            "schema": "rrc25_iran_db_first_content_fingerprint/v2",
            "stable_research_content": stable,
        }
    )


def export_database(
    connection: Any,
    *,
    profile: Mapping[str, Any],
    context: Mapping[str, Any],
    database_config: Mapping[str, str],
    statement_timeout_ms: int = 900_000,
    security_verifier: Any = p0_probe._verify_reader_security,
) -> dict[str, Any]:
    """在一个只读快照中导出固定 Incident，并在返回前完成 rollback。"""

    _validate_fixed_profile(profile)
    _validate_context_tables(context)
    cursor = connection.cursor()
    payload: dict[str, Any] | None = None
    try:
        p0_probe._begin_readonly_transaction(cursor, statement_timeout_ms)
        security = security_verifier(
            cursor,
            expected_user=database_config["DOMEYE_CORE_DB_READER_USER"],
            expected_database=database_config["DOMEYE_CORE_DB_NAME"],
            expected_system=context["system_identifier"],
        )
        cursor.execute(
            FACT_QUERY,
            (
                INCIDENT_SOURCE,
                INCIDENT_COUNTRY,
                INCIDENT_ID,
                INCIDENT_LOCATOR_TIME,
            ),
        )
        fact = _normalize_fact(cursor.fetchall())
        cursor.execute(
            COUNTRY_QUERY,
            (
                INCIDENT_SOURCE,
                INCIDENT_COUNTRY_ZH,
                WINDOW_START,
                WINDOW_END_EXCLUSIVE,
            ),
        )
        country_series = _country_series(cursor.fetchall())
        cursor.execute(
            AS_OUTAGE_QUERY,
            (
                INCIDENT_SOURCE,
                INCIDENT_COUNTRY_ZH,
                datetime(2026, 3, 1, 0, 0, 0),
                datetime(2026, 2, 28, 0, 0, 0),
            ),
        )
        as_outage_rows = cursor.fetchall()
        cursor.execute(
            PREFIX_OUTAGE_QUERY,
            (
                INCIDENT_SOURCE,
                INCIDENT_COUNTRY_ZH,
                datetime(2026, 3, 1, 0, 0, 0),
                datetime(2026, 2, 28, 0, 0, 0),
            ),
        )
        prefix_outage_rows = cursor.fetchall()
        event_facts, fact_buckets = _event_fact_analysis(
            as_outage_rows, prefix_outage_rows, fact
        )
        cursor.execute(
            AS_HISTORY_QUERY,
            (
                INCIDENT_SOURCE,
                INCIDENT_COUNTRY_ZH,
                AS_HISTORY_START,
                WINDOW_END_EXCLUSIVE,
                INCIDENT_SOURCE,
                INCIDENT_COUNTRY_ZH,
                datetime(2026, 3, 1, 0, 0, 0),
                WINDOW_END_EXCLUSIVE,
            ),
        )
        sparse_auxiliary, asn_snapshots = _sparse_feature_auxiliary(
            cursor.fetchall(), fact
        )
        phases = _phase_analysis(
            country_series, asn_snapshots, fact_buckets
        )
        metric_findings = _metric_findings(country_series)
        recovery = _recovery_candidate(country_series)
        gaps = _gap_matrix(
            country_series, event_facts, sparse_auxiliary, fact
        )
        raw_request = _minimal_raw_request(event_facts)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "scope": {
                "incident_ref": INCIDENT_REF,
                "incident_locator_time_local": _local_iso(INCIDENT_LOCATOR_TIME),
                "embedded_message_candidate_time_local": _local_iso(
                    EMBEDDED_EVENT_TIME
                ),
                "relationship_state": "unresolved_not_causal",
                "country_code": INCIDENT_COUNTRY,
                "country_name_zh": INCIDENT_COUNTRY_ZH,
                "source": INCIDENT_SOURCE,
                "window": {
                    "start_local": _local_iso(WINDOW_START),
                    "end_exclusive_local": _local_iso(WINDOW_END_EXCLUSIVE),
                    "start_utc": _utc_iso(WINDOW_START),
                    "end_exclusive_utc": _utc_iso(WINDOW_END_EXCLUSIVE),
                    "semantics": "half_open",
                    "granularity_seconds": 300,
                    "expected_slot_count": EXPECTED_SLOT_COUNT,
                },
                "raw_read_performed": False,
                "database_write_performed": False,
                "backend_core_invoked": False,
            },
            "source_release": {
                "release_id": context["release_id"],
                "system_identifier": context["system_identifier"],
                "state_sha256": context["state_sha256"],
                "manifest_sha256": context["manifest_sha256"],
                "database_manifest_sha256": context["database_manifest_sha256"],
                "inventory_sha256": context["inventory_sha256"],
                "required_table_schema_md5": {
                    name: context["legacy_schema_hashes"][name]
                    for name in sorted(REQUIRED_TABLES)
                },
            },
            "database_security": security,
            "fact": fact,
            "country_series": country_series,
            "event_fact_reconciliation": event_facts,
            "fact_bucket_analysis": fact_buckets,
            "sparse_feature_auxiliary": sparse_auxiliary,
            "phase_analysis": phases,
            "metric_findings": metric_findings,
            "recovery_candidate": recovery,
            "gap_matrix": gaps,
            "minimal_raw_request": raw_request,
            "assessment": {
                "classification": "observation_only",
                "causal_conclusion": None,
                "supports": [
                    "固定旧事实行身份与其内部字段对账",
                    "数据库内伊朗国家级五分钟聚合曲线",
                    "AS/Prefix 中断事实在 22:34:40 的活跃集合与三锚点起始桶",
                    "稀疏 ASN 变化表只作为辅助下界，不主导事件集合对账",
                ],
                "does_not_support": [
                    "前兆与主事件的因果关系",
                    "主动撤回、BGP 会话关闭、物理断路、流量影响或政府意图",
                    "全网完全恢复",
                ],
            },
            "execution": {
                "transaction_mode": "repeatable_read_read_only",
                "transaction_finalization": "pending_rollback",
                "output_semantics": "create_only",
            },
        }
    except BaseException:
        try:
            connection.rollback()
        except Exception:
            pass
        try:
            cursor.close()
        except Exception:
            pass
        raise
    try:
        connection.rollback()
    except Exception as error:
        try:
            cursor.close()
        except Exception:
            pass
        raise DBFirstError("只读事务 rollback 失败") from error
    try:
        cursor.close()
    except Exception as error:
        raise DBFirstError("只读 cursor 关闭失败") from error
    assert payload is not None
    payload["execution"]["transaction_finalization"] = "rollback_completed"
    payload["content_fingerprint_sha256"] = _content_fingerprint(payload)
    return payload


def _markdown(payload: Mapping[str, Any]) -> str:
    fact = payload["fact"]
    coverage = payload["country_series"]["coverage"]
    event_facts = payload["event_fact_reconciliation"]
    sparse_auxiliary = payload["sparse_feature_auxiliary"]
    metric_findings = payload["metric_findings"]
    recovery = payload["recovery_candidate"]
    lines = [
        "# RRC25 伊朗事件数据库先行复算摘要",
        "",
        "本摘要只陈述数据库观测，不读取原始 MRT，也不作前兆或因果认定。",
        "",
        "## 固定范围",
        "",
        "- Incident：`{}`".format(payload["scope"]["incident_ref"]),
        "- 北京时间窗口：`{}` 至 `{}`（左闭右开）".format(
            payload["scope"]["window"]["start_local"],
            payload["scope"]["window"]["end_exclusive_local"],
        ),
        "- 五分钟槽：预期 {}，观测 {}，缺失 {}，覆盖率 {:.2%}".format(
            coverage["expected_slot_count"],
            coverage["observed_slot_count"],
            coverage["missing_slot_count"],
            coverage["coverage_ratio"],
        ),
        "- 旧检测时间：`{}`（`{}`）；旧摘要更新时间：`{}`（`{}`）；相差 {} 秒，二者不得合并为单一事件时间。".format(
            fact["temporal_semantics"]["legacy_detected_at_local"],
            fact["temporal_semantics"]["legacy_detected_at_utc"],
            fact["temporal_semantics"]["legacy_summary_updated_at_local"],
            fact["temporal_semantics"]["legacy_summary_updated_at_utc"],
            fact["temporal_semantics"]["difference_seconds"],
        ),
        "- 事件类型：`{}`；风险：`{}`；持续时间状态：`{}`。".format(
            fact["event_type"],
            fact["risk"]["level"],
            fact["duration"]["value_state"],
        ),
        "",
        "## 22:34:40 事件事实对账",
        "",
        "- 旧事实峰值：`{}/{}`；受影响集合实际包含 {} 个 ASN。".format(
            fact["affected_asn_count"],
            fact["total_asn_count"],
            len(fact["affected_asns"]),
        ),
        "- 活跃 AS 中断事实：{} 个唯一 ASN；其旧生命周期峰值比例等于 1 / 介于 0 与 1：`{}/{}`。".format(
            event_facts["as_outage"]["unique_asn_count"],
            event_facts["as_outage"]["legacy_peak_ratio_1_asn_count"],
            event_facts["as_outage"][
                "legacy_peak_ratio_between_0_and_1_asn_count"
            ],
        ),
        "- 上述 `97/79` 是持久化历史峰值分类，不代表 22:34:40 当刻完全/部分比例。",
        "- 活跃 Prefix 中断事实：{} 行，{} 个 `distinct(prefix,asn)`，仅按 prefix 去重为 {}。".format(
            event_facts["prefix_outage"]["fact_row_count"],
            event_facts["prefix_outage"]["distinct_prefix_asn_count"],
            event_facts["prefix_outage"]["unique_prefix_count"],
        ),
        "- country_outage 与 as_outage 双向集合精确匹配：`{}`；主对账状态：`{}`。".format(
            event_facts["country_outage_bidirectional_reconciliation"][
                "exact_set_match"
            ],
            event_facts["status"],
        ),
        "- 稀疏 feature_other 仅作旁证：22:30/22:35 活跃 ASN 下界为 `{}/{}`。".format(
            sparse_auxiliary["before_snapshot"]["active_asn_count"],
            sparse_auxiliary["after_snapshot"]["active_asn_count"],
        ),
        "",
        "## 三个观察锚点",
        "",
        "| 锚点 | 国家行 | IPv4 /24 等值 | IPv6 /48 等值 | AS事实/ASN | Prefix事实/Prefix/ASN |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for anchor in payload["phase_analysis"]["anchors"]:
        point = anchor["country_metrics"] or {}
        bucket = anchor["outage_fact_bucket"]
        lines.append(
            "| {} | {} | {} | {} | {}/{} | {}/{}/{} |".format(
                anchor["observed_at_local"],
                anchor["value_state"],
                point.get("ipv4_24_equivalent"),
                point.get("ipv6_48_equivalent"),
                bucket["as_outage_fact_count"],
                bucket["as_outage_unique_asn_count"],
                bucket["prefix_outage_fact_count"],
                bucket["prefix_outage_unique_prefix_count"],
                bucket["prefix_outage_unique_asn_count"],
            )
        )
    observations = {
        item["label"]: item for item in metric_findings["observations"]
    }
    activity = metric_findings["activity_window_17_19"]
    lines.extend(
        [
            "",
            "## 指标复算结论",
            "",
            "- 固定六小时基线 `[00:00, 06:00)` 的 IPv4 地址等值中位数：`{}`。".format(
                metric_findings["baseline"]["values"][
                    "ipv4_address_equivalent"
                ]
            ),
            "- `17:00–19:00` ANNOUNCE/WITHDRAW 合计：`{} / {}`（{} / 24 槽）。".format(
                activity["announce_count_sum"],
                activity["withdraw_count_sum"],
                activity["observed_slot_count"],
            ),
            "- IPv4 地址等值降幅：06:40 `{}`%，18:45 `{}`%，22:30 `{}`%。".format(
                observations["候选早期扰动低点"]["ipv4_decline_percent"],
                observations["第一显著波次低点"]["ipv4_decline_percent"],
                observations["第二显著波次低点"]["ipv4_decline_percent"],
            ),
            "- 报告 16:14 邻近槽 16:15 为基线的 `{}`%，不能作为约 6% 低点。".format(
                observations["报告 16:14 主张邻近槽"][
                    "ipv4_percent_of_baseline"
                ]
            ),
            "",
            "## 恢复候选",
            "",
            "- 状态：`{}`".format(recovery["status"]),
            "- 候选时间：`{}`".format(recovery["candidate_at_local"]),
            "- 口径：国家聚合双栈可用维度连续 6 槽达到基线中位数的 99%。"
            "这不是全网恢复确认。",
            "",
            "## 数据缺口",
            "",
            "| 层级 | 状态 | 已有 | 缺失 |",
            "|---|---|---|---|",
        ]
    )
    for gap in payload["gap_matrix"]:
        lines.append(
            "| {} | {} | {} | {} |".format(
                gap["evidence_layer"],
                gap["status"],
                str(gap["available"]).replace("|", "\\|"),
                str(gap["missing"]).replace("|", "\\|"),
            )
        )
    lines.extend(
        [
            "",
            "## 最小 raw 请求",
            "",
            "- 状态：`not_executed`",
            "- 关键槽数：{}".format(
                len(payload["minimal_raw_request"]["critical_slots"])
            ),
            "- 定向窗口/UPDATE 槽：{} / {}。".format(
                len(payload["minimal_raw_request"]["evidence_windows"]),
                len(payload["minimal_raw_request"]["update_slots"]),
            ),
            "- 代表 ASN：{}".format(
                "、".join(payload["minimal_raw_request"]["representative_asns"])
                or "无"
            ),
            "- 代表 Prefix：{}".format(
                "、".join(payload["minimal_raw_request"]["representative_prefixes"])
                or "无"
            ),
            "- 明确不运行全窗口 A/B，不允许据此作因果结论。",
            "",
            "内容指纹：`{}`".format(payload["content_fingerprint_sha256"]),
            "",
        ]
    )
    return "\n".join(lines)


def _write_create_only(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def publish_artifacts(payload: Mapping[str, Any], output_directory: Path) -> dict[str, Any]:
    """以 create-only 目录发布 JSON、中文摘要与 SHA256SUMS。"""

    output_directory = output_directory.absolute()
    p0_probe._assert_no_symlink_ancestors(output_directory)
    if output_directory.exists() or output_directory.is_symlink():
        raise DBFirstError("输出目录已存在，拒绝覆盖：{}".format(output_directory))
    parent = output_directory.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    p0_probe._assert_no_symlink_ancestors(parent)
    staging = Path(
        tempfile.mkdtemp(
            prefix=".{}.staging.".format(output_directory.name),
            dir=str(parent),
        )
    )
    try:
        json_bytes = (
            json.dumps(
                p0_probe._json_ready(dict(payload)),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        markdown_bytes = _markdown(payload).encode("utf-8")
        json_path = staging / "iran-db-first.json"
        markdown_path = staging / "伊朗数据库先行复算摘要.md"
        _write_create_only(json_path, json_bytes)
        _write_create_only(markdown_path, markdown_bytes)
        checksums = [
            "{}  {}".format(p0_probe._sha256(json_path), json_path.name),
            "{}  {}".format(p0_probe._sha256(markdown_path), markdown_path.name),
        ]
        checksum_path = staging / "SHA256SUMS"
        _write_create_only(
            checksum_path, ("\n".join(checksums) + "\n").encode("utf-8")
        )
        directory_fd = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        if output_directory.exists() or output_directory.is_symlink():
            raise DBFirstError("输出目录在发布期间出现，拒绝覆盖")
        os.rename(staging, output_directory)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {
        "output_directory": str(output_directory),
        "files": [
            {
                "name": name,
                "sha256": p0_probe._sha256(output_directory / name),
                "size_bytes": (output_directory / name).stat().st_size,
            }
            for name in (
                "iran-db-first.json",
                "伊朗数据库先行复算摘要.md",
                "SHA256SUMS",
            )
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="导出固定伊朗国家中断事件的 DB-first 只读研究包"
    )
    parser.add_argument("--database-env", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--statement-timeout-ms", type=int, default=900_000)
    parser.add_argument("--connect-timeout", type=int, default=5)
    arguments = parser.parse_args(argv)
    connection = None
    try:
        try:
            project_root = arguments.project_root.resolve(strict=True)
        except OSError as error:
            raise DBFirstError("无法解析项目根目录：{}".format(arguments.project_root)) from error
        profile = p0_probe._load_project_data_profile(project_root)
        context = p0_probe._validate_release_context(
            profile=profile,
            state_path=arguments.state,
            release_dir=arguments.release_dir,
        )
        _validate_fixed_profile(profile)
        _validate_context_tables(context)
        database_config = p0_probe._read_database_env(arguments.database_env)
        connection = p0_probe._connect_database(
            dict(database_config), context, arguments.connect_timeout
        )
        payload = export_database(
            connection,
            profile=profile,
            context=context,
            database_config=database_config,
            statement_timeout_ms=arguments.statement_timeout_ms,
        )
        result = publish_artifacts(payload, arguments.output_directory)
        sys.stdout.write(
            json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n"
        )
    except (DBFirstError, p0_probe.ProbeError) as error:
        parser.error(str(error))
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
