#!/usr/bin/env python3
"""P0 数据基础 R 轨 R2：遗留语义风险测量（只读）。

对 F2 / S2 / S4 各自给出"已确认（含量级）"或"已排除"结论。
所有结论必须来自数据测量，不得来自代码阅读。

- F2：32 位私有 ASN 判定失效。测量 4 字节私有段 ASN（4200000000–4294967294）
      在二三月数据中的出现频次，以及 `is_private_as` 是否把它们记为非私有。
- S2：泄漏闩锁。测量每前缀泄漏事件数分布，以及路由泄漏事件的结束时间缺失率。
- S4：MOAS 归属。测量前缀中断所记 ASN 是否系统性等于候选 origin 的字典序最小值。

边界（R2）：只测量，不修复；零写操作；不修改 `backend/core/`。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

WINDOW_MONTHS = ("202602", "202603")
# 4 字节私有 ASN 段（RFC 6996）；16 位私有段为 64512–65535。
PRIVATE_32_LO, PRIVATE_32_HI = 4200000000, 4294967294
PRIVATE_16_LO, PRIVATE_16_HI = 64512, 65535


def _connect():
    import psycopg2

    conn = psycopg2.connect(
        database=os.environ.get("DB_NAME", "bgp_project"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "5432")),
    )
    conn.set_session(readonly=True, autocommit=True)
    return conn


def _exists(cur, name: str) -> bool:
    cur.execute("SELECT count(*) FROM pg_class WHERE relname = %s;", (name.lower(),))
    return cur.fetchone()[0] == 1


# 只在整数形状的 ASN 文本上做数值比较，避免 AS-set / 下划线形式导致转换报错。
NUMERIC_ASN = r"^[0-9]+$"


def measure_f2(cur) -> dict[str, Any]:
    """F2：4 字节私有 ASN 是否出现，且是否被记为非私有。"""
    result: dict[str, Any] = {"per_table": {}, "misclassified_samples": []}
    total_private32 = 0
    total_private16 = 0

    for month in WINDOW_MONTHS:
        for table, col in (
            (f"prefix_outage_{month}", "asn"),
            (f"as_outage_{month}", "asn"),
            (f"feature_other_{month}", "asn"),
        ):
            if not _exists(cur, table):
                continue
            cur.execute(
                f"SELECT count(*) FROM {table} WHERE {col} ~ %s "
                f"AND ({col})::bigint BETWEEN %s AND %s;",
                (NUMERIC_ASN, PRIVATE_32_LO, PRIVATE_32_HI),
            )
            p32 = cur.fetchone()[0]
            cur.execute(
                f"SELECT count(*) FROM {table} WHERE {col} ~ %s "
                f"AND ({col})::bigint BETWEEN %s AND %s;",
                (NUMERIC_ASN, PRIVATE_16_LO, PRIVATE_16_HI),
            )
            p16 = cur.fetchone()[0]
            cur.execute(f"SELECT count(*) FROM {table};")
            total = cur.fetchone()[0]
            result["per_table"][table] = {
                "rows": total, "private_32bit": p32, "private_16bit": p16,
            }
            total_private32 += p32
            total_private16 += p16

        # is_private_as 是生产侧记录的分类结果，可直接检验误分类。
        table = f"prefix_outage_{month}"
        if _exists(cur, table):
            cur.execute(
                f"SELECT asn, is_private_as, count(*) FROM {table} "
                f"WHERE asn ~ %s AND (asn)::bigint BETWEEN %s AND %s "
                f"GROUP BY asn, is_private_as LIMIT 20;",
                (NUMERIC_ASN, PRIVATE_32_LO, PRIVATE_32_HI),
            )
            for asn, flag, cnt in cur.fetchall():
                result["misclassified_samples"].append(
                    {"table": table, "asn": asn, "is_private_as": flag, "rows": cnt}
                )

    result["total_private_32bit_occurrences"] = total_private32
    result["total_private_16bit_occurrences"] = total_private16
    return result


def measure_s2(cur) -> dict[str, Any]:
    """S2：泄漏闩锁——每前缀事件数分布 + 泄漏事件结束时间缺失率。"""
    result: dict[str, Any] = {"per_table": {}}
    for month in WINDOW_MONTHS:
        table = f"leak_event_{month}"
        if not _exists(cur, table):
            continue
        cur.execute(f"SELECT count(*), count(DISTINCT prefix) FROM {table};")
        rows, prefixes = cur.fetchone()
        cur.execute(
            f"SELECT c, count(*) FROM ("
            f"  SELECT prefix, count(*) AS c FROM {table} GROUP BY prefix"
            f") s GROUP BY c ORDER BY c LIMIT 10;"
        )
        distribution = [{"events_per_prefix": r[0], "prefix_count": r[1]} for r in cur.fetchall()]
        result["per_table"][table] = {
            "rows": rows,
            "distinct_prefixes": prefixes,
            "events_per_prefix_ratio": (rows / prefixes) if prefixes else None,
            "distribution": distribution,
        }

    # 事件总表侧：路由泄漏是否具备结束时间。
    for month in WINDOW_MONTHS:
        table = f"event_table_{month}"
        if not _exists(cur, table):
            continue
        cur.execute(
            f"SELECT count(*), count(e_time), count(duration) FROM {table} "
            f"WHERE event_type = %s;",
            ("路由泄漏",),
        )
        total, with_end, with_duration = cur.fetchone()
        cur.execute(
            f"SELECT event_type, count(*), count(e_time) FROM {table} "
            f"GROUP BY event_type ORDER BY count(*) DESC;"
        )
        by_type = [
            {"event_type": r[0], "rows": r[1], "with_e_time": r[2]} for r in cur.fetchall()
        ]
        result.setdefault("event_table", {})[table] = {
            "leak_rows": total,
            "leak_with_e_time": with_end,
            "leak_with_duration": with_duration,
            "by_event_type": by_type,
        }
    return result


def measure_s4(cur) -> dict[str, Any]:
    """S4：MOAS 归属——中断所记 ASN 是否等于候选 origin 的字典序最小值。

    候选 origin 取自同前缀的 hijack 记录（hijack 成立即意味着该前缀出现过 MOAS），
    时间窗取中断开始时刻前后各 1 天，避免跨事件误配。
    """
    result: dict[str, Any] = {"per_month": {}}
    for month in WINDOW_MONTHS:
        outage, hijack = f"prefix_outage_{month}", f"hijack_{month}"
        if not (_exists(cur, outage) and _exists(cur, hijack)):
            continue
        cur.execute(
            f"""
            WITH pair AS (
                SELECT o.prefix, o.outage_id, o.asn AS attributed,
                       h.hijacked_as, h.hijacker_as,
                       LEAST(h.hijacked_as, h.hijacker_as) AS lexicographic_min,
                       GREATEST(h.hijacked_as, h.hijacker_as) AS lexicographic_max
                FROM {outage} o
                JOIN {hijack} h
                  ON h.prefix = o.prefix
                 AND h.s_time BETWEEN o.s_time - INTERVAL '1 day'
                                  AND o.s_time + INTERVAL '1 day'
                WHERE h.hijacked_as IS NOT NULL AND h.hijacker_as IS NOT NULL
                  AND h.hijacked_as <> h.hijacker_as
            )
            SELECT count(*) AS matched_pairs,
                   count(*) FILTER (WHERE attributed = lexicographic_min) AS eq_min,
                   count(*) FILTER (WHERE attributed = lexicographic_max) AS eq_max,
                   count(*) FILTER (WHERE attributed NOT IN (lexicographic_min, lexicographic_max)) AS eq_neither
            FROM pair;
            """
        )
        matched, eq_min, eq_max, eq_neither = cur.fetchone()
        cur.execute(
            f"""
            SELECT o.prefix, o.asn, h.hijacked_as, h.hijacker_as,
                   LEAST(h.hijacked_as, h.hijacker_as)
            FROM {outage} o JOIN {hijack} h
              ON h.prefix = o.prefix
             AND h.s_time BETWEEN o.s_time - INTERVAL '1 day' AND o.s_time + INTERVAL '1 day'
            WHERE h.hijacked_as IS NOT NULL AND h.hijacker_as IS NOT NULL
              AND h.hijacked_as <> h.hijacker_as
            LIMIT 8;
            """
        )
        samples = [
            {"prefix": r[0], "attributed": r[1], "hijacked_as": r[2],
             "hijacker_as": r[3], "lexicographic_min": r[4]}
            for r in cur.fetchall()
        ]
        result["per_month"][month] = {
            "matched_pairs": matched,
            "attributed_equals_lexicographic_min": eq_min,
            "attributed_equals_lexicographic_max": eq_max,
            "attributed_equals_neither": eq_neither,
            "samples": samples,
        }
    return result


def main() -> int:
    report: dict[str, Any] = {
        "schema_version": "domeye_p0_legacy_risk_measurement/v1",
        "read_only": True,
        "note": (
            "本报告只登记测量事实。结论类型为数据测量；"
            "任何成因归属须另行以有界原始重算确认，不得据代码阅读结案。"
        ),
    }
    conn = _connect()
    try:
        cur = conn.cursor()
        report["F2"] = measure_f2(cur)
        report["S2"] = measure_s2(cur)
        report["S4"] = measure_s4(cur)
    finally:
        conn.close()

    out = os.environ.get("R2_OUTPUT", "/tmp/p0_r2_measurement.json")
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
    sys.stderr.write(f"R2 测量报告已写入 {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
