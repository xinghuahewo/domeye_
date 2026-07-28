#!/usr/bin/env python3
"""P0 数据基础 R 轨 R1：独立语义对账门禁。

与「源精确对账」的区别（RFA-02，不可混用）：

- 源精确对账：把旁路数据与**源表**逐值比较，差异为 0 只证明**复制保真**，
  即旁路忠实复制了旧表。它不能证明旧表本身正确。
- 独立语义对账（本模块）：不引用源表作为基准，而是按各指标**自身声明的定义**
  在读取侧重算，再与库中已存值比较。它检验**语义自洽**，可以在复制 100% 保真的
  情况下仍然失败。

因此本模块的通过不代表数据正确，只代表所检验的语义恒等式成立；本模块的失败
也不指认责任方，只登记不一致事实，修复属后续阶段。

边界（R1）：只读、可重复、产出可归档报告；只定义并运行门禁，不修复其发现的问题。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from decimal import Decimal
from typing import Any, Sequence

GATE_NAME = "independent_semantic_reconciliation"
GATE_NAME_ZH = "独立语义对账"
GATE_SCHEMA = "domeye_p0_independent_semantic_reconciliation/v1"
NOT_THIS_GATE = "source_exact_reconciliation"

WINDOW_MONTHS = ("202602", "202603")
# 比率列为 numeric(4,3)，重算值需按同精度量化后再比，否则会把存储舍入误判为不一致。
RATIO_QUANT = Decimal("0.001")


def _load_env_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()


def _connect(dsn_env_prefix: str = "DB"):
    import psycopg2  # 延迟导入：无库环境下仍可 --describe

    conn = psycopg2.connect(
        database=os.environ.get(f"{dsn_env_prefix}_NAME", "bgp_project"),
        user=os.environ.get(f"{dsn_env_prefix}_USER", "postgres"),
        password=os.environ.get(f"{dsn_env_prefix}_PASSWORD", ""),
        host=os.environ.get(f"{dsn_env_prefix}_HOST", "127.0.0.1"),
        port=int(os.environ.get(f"{dsn_env_prefix}_PORT", "5432")),
    )
    conn.set_session(readonly=True, autocommit=True)
    return conn


def _table_exists(cur, name: str) -> bool:
    cur.execute("SELECT count(*) FROM pg_class WHERE relname = %s;", (name.lower(),))
    return cur.fetchone()[0] == 1


def _finding(check: str, table: str, detail: dict[str, Any]) -> dict[str, Any]:
    return {"check": check, "table": table, "detail": detail}


# --- 恒等式 1：AS 中断比率自洽 -------------------------------------------------
# 声明定义（BGPOutage）：max_outage_prefix_ratio = max_outage_prefix_num / total_prefix_num
def check_as_outage_ratio(cur, table: str) -> dict[str, Any]:
    cur.execute(
        f"SELECT source, asn, outage_id, max_outage_prefix_num, total_prefix_num,"
        f" max_outage_prefix_ratio FROM {table};"
    )
    rows = cur.fetchall()
    findings: list[dict[str, Any]] = []
    zero_denominator = 0
    for source, asn, outage_id, num, total, stored in rows:
        if not total:
            zero_denominator += 1
            continue
        recomputed = (Decimal(num) / Decimal(total)).quantize(RATIO_QUANT)
        if stored is None or Decimal(stored).quantize(RATIO_QUANT) != recomputed:
            findings.append(
                _finding(
                    "as_outage_ratio_identity",
                    table,
                    {
                        "source": source, "asn": asn, "outage_id": outage_id,
                        "stored": str(stored), "recomputed": str(recomputed),
                        "num": num, "total": total,
                    },
                )
            )
    return {"rows": len(rows), "zero_denominator": zero_denominator, "findings": findings}


# --- 恒等式 2：国家中断比率自洽 -----------------------------------------------
def check_country_outage_ratio(cur, table: str) -> dict[str, Any]:
    cur.execute(
        f"SELECT source, country, outage_id, max_outage_as_num, total_as_num,"
        f" max_outage_as_ratio FROM {table};"
    )
    rows = cur.fetchall()
    findings: list[dict[str, Any]] = []
    zero_denominator = 0
    for source, country, outage_id, num, total, stored in rows:
        if not total:
            zero_denominator += 1
            continue
        recomputed = (Decimal(num) / Decimal(total)).quantize(RATIO_QUANT)
        if stored is None or Decimal(stored).quantize(RATIO_QUANT) != recomputed:
            findings.append(
                _finding(
                    "country_outage_ratio_identity",
                    table,
                    {
                        "source": source, "country": country, "outage_id": outage_id,
                        "stored": str(stored), "recomputed": str(recomputed),
                        "num": num, "total": total,
                    },
                )
            )
    return {"rows": len(rows), "zero_denominator": zero_denominator, "findings": findings}


# --- 恒等式 3：中断子集关系 ---------------------------------------------------
# 中断前缀数不得超过总前缀数；中断 AS 数不得超过国家 AS 总数。
def check_subset_bounds(cur, table: str, num_col: str, total_col: str, key_col: str) -> dict[str, Any]:
    cur.execute(
        f"SELECT source, {key_col}, outage_id, {num_col}, {total_col} FROM {table}"
        f" WHERE {num_col} > {total_col};"
    )
    rows = cur.fetchall()
    return {
        "findings": [
            _finding(
                "outage_subset_bound",
                table,
                {"source": r[0], "key": r[1], "outage_id": r[2],
                 "num": r[3], "total": r[4]},
            )
            for r in rows
        ]
    }


# --- 恒等式 4：持续时间自洽 ---------------------------------------------------
# duration 应等于 e_time - s_time（两者均存在时）。
def check_duration_identity(cur, table: str) -> dict[str, Any]:
    cur.execute(
        f"SELECT count(*) FROM {table}"
        f" WHERE e_time IS NOT NULL AND duration IS NOT NULL"
        f"   AND duration <> (e_time - s_time);"
    )
    mismatched = cur.fetchone()[0]
    cur.execute(f"SELECT count(*) FROM {table} WHERE e_time IS NOT NULL AND s_time > e_time;")
    inverted = cur.fetchone()[0]
    findings = []
    if mismatched:
        findings.append(_finding("duration_identity", table, {"mismatched_rows": mismatched}))
    if inverted:
        findings.append(_finding("time_order_identity", table, {"inverted_rows": inverted}))
    return {"findings": findings}


# --- 恒等式 5：特征聚合对账（审计项 F3 的门禁化） ------------------------------
# 声明定义（BGPFeature.insert_to_db）：collect 行为全局计数，各国家行为分国家计数。
# 若二者在同一时刻不一致，说明存在未归属到任何国家的计数。
def check_feature_aggregate(cur, table: str = "feature_country") -> dict[str, Any]:
    cur.execute(
        f"""
        WITH per_country AS (
            SELECT t, source,
                   sum(announ_num)   AS sum_announ,
                   sum(withdraw_num) AS sum_withdraw
            FROM {table} WHERE country <> 'collect' GROUP BY t, source
        ), collect AS (
            SELECT t, source, announ_num, withdraw_num
            FROM {table} WHERE country = 'collect'
        )
        SELECT c.t, c.source, c.announ_num, p.sum_announ,
               c.withdraw_num, p.sum_withdraw
        FROM collect c JOIN per_country p ON p.t = c.t AND p.source = c.source
        WHERE c.announ_num IS DISTINCT FROM p.sum_announ
           OR c.withdraw_num IS DISTINCT FROM p.sum_withdraw
        ORDER BY c.t LIMIT 50;
        """
    )
    rows = cur.fetchall()
    cur.execute(f"SELECT count(DISTINCT t) FROM {table} WHERE country = 'collect';")
    slots = cur.fetchone()[0]
    return {
        "collect_slots": slots,
        "findings": [
            _finding(
                "feature_aggregate_identity",
                table,
                {
                    "t": str(r[0]), "source": r[1],
                    "collect_announ": r[2], "sum_country_announ": r[3],
                    "collect_withdraw": r[4], "sum_country_withdraw": r[5],
                    "announ_delta": (r[2] or 0) - (r[3] or 0),
                    "withdraw_delta": (r[4] or 0) - (r[5] or 0),
                },
            )
            for r in rows
        ],
    }


# --- 恒等式 6：v4 地址口径自洽 -------------------------------------------------
# 声明定义：v4ip_num = v4prefix_num * 256（去重 /24 覆盖块 × 256）。
def check_v4_address_identity(cur, table: str = "feature_country") -> dict[str, Any]:
    cur.execute(
        f"SELECT count(*) FROM {table}"
        f" WHERE v4prefix_num IS NOT NULL AND v4ip_num IS NOT NULL"
        f"   AND v4ip_num <> v4prefix_num * 256;"
    )
    mismatched = cur.fetchone()[0]
    findings = []
    if mismatched:
        findings.append(_finding("v4_address_identity", table, {"mismatched_rows": mismatched}))
    return {"findings": findings}


def describe() -> dict[str, Any]:
    return {
        "gate": GATE_NAME,
        "gate_zh": GATE_NAME_ZH,
        "schema_version": GATE_SCHEMA,
        "is_not": NOT_THIS_GATE,
        "semantics": (
            "按各指标自身声明的定义在读取侧重算并与已存值比较；不引用源表作为基准。"
            "通过不代表数据正确，只代表所检验的语义恒等式成立。"
        ),
        "read_only": True,
        "identities": [
            "as_outage_ratio_identity: max_outage_prefix_ratio == num/total",
            "country_outage_ratio_identity: max_outage_as_ratio == num/total",
            "outage_subset_bound: outage_num <= total_num",
            "duration_identity: duration == e_time - s_time",
            "time_order_identity: s_time <= e_time",
            "feature_aggregate_identity: collect == sum(per-country)  [审计项 F3]",
            "v4_address_identity: v4ip_num == v4prefix_num * 256",
        ],
    }


def run(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=f"{GATE_NAME_ZH}门禁（只读）")
    parser.add_argument("--describe", action="store_true", help="只输出门禁语义，不连库")
    parser.add_argument("--env-file", default="", help="读取 DB_* 配置的 .env 路径")
    parser.add_argument("--output", default="", help="报告写入路径；缺省写 stdout")
    args = parser.parse_args(argv)

    if args.describe:
        json.dump(describe(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.env_file:
        _load_env_file(args.env_file)

    report: dict[str, Any] = dict(describe())
    report["executed_at_utc"] = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report["results"] = {}
    all_findings: list[dict[str, Any]] = []

    conn = _connect()
    try:
        cur = conn.cursor()
        for month in WINDOW_MONTHS:
            for family, fn in (
                (f"as_outage_{month}", check_as_outage_ratio),
                (f"country_outage_{month}", check_country_outage_ratio),
            ):
                if not _table_exists(cur, family):
                    report["results"][family] = {"skipped": "table_absent"}
                    continue
                result = fn(cur, family)
                report["results"][family] = result
                all_findings.extend(result["findings"])

            for table, num_col, total_col, key_col in (
                (f"as_outage_{month}", "max_outage_prefix_num", "total_prefix_num", "asn"),
                (f"country_outage_{month}", "max_outage_as_num", "total_as_num", "country"),
            ):
                if not _table_exists(cur, table):
                    continue
                result = check_subset_bounds(cur, table, num_col, total_col, key_col)
                report["results"].setdefault(table, {}).setdefault("subset", result)
                all_findings.extend(result["findings"])

            for table in (
                f"event_table_{month}", f"as_outage_{month}",
                f"country_outage_{month}", f"prefix_outage_{month}",
            ):
                if not _table_exists(cur, table):
                    continue
                result = check_duration_identity(cur, table)
                report["results"].setdefault(table, {}).setdefault("duration", result)
                all_findings.extend(result["findings"])

        if _table_exists(cur, "feature_country"):
            agg = check_feature_aggregate(cur)
            report["results"]["feature_country"] = agg
            all_findings.extend(agg["findings"])
            v4 = check_v4_address_identity(cur)
            report["results"]["feature_country"].setdefault("v4", v4)
            all_findings.extend(v4["findings"])
        else:
            report["results"]["feature_country"] = {"skipped": "table_absent"}
    finally:
        conn.close()

    report["finding_count"] = len(all_findings)
    report["findings"] = all_findings[:200]
    report["verdict"] = "consistent" if not all_findings else "inconsistent"
    report["verdict_note"] = (
        "consistent 仅表示所检验的语义恒等式成立，不表示数据正确；"
        "inconsistent 只登记不一致事实，不指认责任方，修复不属本门禁。"
    )

    # PostgreSQL sum(bigint) 返回 numeric，psycopg2 映射为 Decimal；统一转字符串保真输出。
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
        sys.stderr.write(f"{GATE_NAME_ZH}报告已写入 {args.output}\n")
    else:
        sys.stdout.write(payload + "\n")
    return 0 if not all_findings else 2


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
