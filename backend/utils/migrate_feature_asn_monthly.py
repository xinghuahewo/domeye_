import argparse
import datetime

import psycopg2

from config.config import BIG_COUNTRY, FEATURE_ASN_OLD_SUFFIX, FEATURE_OTHER_TABLE
from database.feature_asn import create_feature_asn_table
from database.utils import if_table_exist


def _all_feature_asn_base_tables():
    tables = [FEATURE_OTHER_TABLE]
    for _, country_en in BIG_COUNTRY.items():
        tables.append(f"feature_{country_en}")
    # 去重但保持顺序
    seen = set()
    ordered = []
    for t in tables:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


def _validate_month(month: str) -> str:
    month = str(month).strip()
    if len(month) != 6 or not month.isdigit():
        raise ValueError("month must be YYYYMM, e.g. 202601")
    return month


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Migrate feature ASN tables to monthly tables.\n"
            "- Rename existing base tables to *_old\n"
            "- Create new monthly tables *_YYYYMM (bigint columns) via create_feature_asn_table"
        )
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--month", default=datetime.datetime.utcnow().strftime("%Y%m"))
    args = parser.parse_args()

    month = _validate_month(args.month)

    conn = psycopg2.connect(
        database=args.db, user=args.user, password=args.password, host=args.host, port=args.port
    )
    try:
        cursor = conn.cursor()

        base_tables = _all_feature_asn_base_tables()
        for base in base_tables:
            old = f"{base}{FEATURE_ASN_OLD_SUFFIX}"
            month_table = f"{base}_{month}"

            # 1) base -> old
            if if_table_exist(conn, base):
                if if_table_exist(conn, old):
                    print(f"SKIP rename: {base} exists but {old} already exists")
                else:
                    print(f"RENAME: {base} -> {old}")
                    cursor.execute(f"ALTER TABLE {base} RENAME TO {old};")
                    conn.commit()
            else:
                print(f"SKIP rename: {base} does not exist")

            # 2) create month table if needed
            if if_table_exist(conn, month_table):
                print(f"SKIP create: {month_table} already exists")
            else:
                print(f"CREATE: {month_table}")
                create_feature_asn_table(conn, month_table)

    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
