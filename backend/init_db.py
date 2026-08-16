"""
数据库初始化脚本：创建所有必要的表（包括当月月表、静态表、TimescaleDB 超表）
用法：python3 init_db.py [--month YYYYMM]
不指定 --month 则使用当前 UTC 月份。
"""
import sys
import os
import argparse
import datetime


def load_local_env():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(current_dir, '.env'),
        os.path.join(os.path.dirname(current_dir), '.env'),
    ]

    for env_path in candidates:
        if not os.path.exists(env_path):
            continue
        with open(env_path, 'r', encoding='utf-8') as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                if not key or key in os.environ:
                    continue
                os.environ[key] = value
        break


load_local_env()

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config.database import DATABASE, USER, PASSWORD, HOST, PORT
from config.config import (
    FEATURE_COUNTRY_TABLE, FEATURE_OTHER_TABLE,
    FEATURE_ASN_MONTHLY_ENABLED, BIG_COUNTRY,
    ROUTING_RES_TABLE, VP_RES_TABLE,
    COUNTRY_TOPOLOGY_EDGE_TABLE, COUNTRY_TOPOLOGY_SNAPSHOT_TABLE,
)
from database.utils import get_conn, if_table_exist
from database.event import create_event_table
from database.moas import create_moas_table
from database.hijack import create_hijack_table
from database.sub_hijack import create_sub_hijack_table
from database.leak_phenomenon import create_leak_phenomenon_table
from database.leak_event import create_leak_event_table
from database.prefix_outage import create_prefix_outage_table
from database.as_outage import create_as_outage_table
from database.country_outage import create_country_outage_table
from database.feature_asn import create_feature_asn_table
from database.feature_country import create_feature_country_table
from database.prefix_count import create_prefix_count_table, ensure_prefix_count_table_columns
from database.vp_resource import create_vp_resource_table
from database.country_topology import (
    create_country_topology_edge_table,
    create_country_topology_snapshot_table,
)
from database.login import create_login_table
from database.as_info import create_as_info_table


def _ok(name):
    print(f"  [OK] {name}")


def init_monthly_tables(conn, month_suffix: str):
    """创建当月的检测类月表"""
    tables = {
        f"event_table_{month_suffix}": create_event_table,
        f"moas_{month_suffix}": create_moas_table,
        f"hijack_{month_suffix}": create_hijack_table,
        f"sub_hijack_{month_suffix}": create_sub_hijack_table,
        f"leak_phenomenon_{month_suffix}": create_leak_phenomenon_table,
        f"leak_event_{month_suffix}": create_leak_event_table,
        f"prefix_outage_{month_suffix}": create_prefix_outage_table,
        f"as_outage_{month_suffix}": create_as_outage_table,
        f"country_outage_{month_suffix}": create_country_outage_table,
    }
    print(f"\n=== 创建月表 (月份: {month_suffix}) ===")
    for table_name, create_fn in tables.items():
        if not if_table_exist(conn, table_name):
            create_fn(conn, table_name)
            _ok(table_name)
        else:
            print(f"  [SKIP] {table_name} 已存在")


def init_feature_tables(conn, month_suffix: str):
    """创建特征类表（TimescaleDB 超表）"""
    print("\n=== 创建特征表 ===")

    if not if_table_exist(conn, FEATURE_COUNTRY_TABLE):
        create_feature_country_table(conn, FEATURE_COUNTRY_TABLE)
        _ok(FEATURE_COUNTRY_TABLE)
    else:
        print(f"  [SKIP] {FEATURE_COUNTRY_TABLE} 已存在")

    if FEATURE_ASN_MONTHLY_ENABLED:
        table_name = f"{FEATURE_OTHER_TABLE}_{month_suffix}"
        if not if_table_exist(conn, table_name):
            create_feature_asn_table(conn, table_name)
            _ok(table_name)
        else:
            print(f"  [SKIP] {table_name} 已存在")

        for country_cn, country_en in BIG_COUNTRY.items():
            table_name = f"feature_{country_en}_{month_suffix}"
            if not if_table_exist(conn, table_name):
                create_feature_asn_table(conn, table_name)
                _ok(table_name)
            else:
                print(f"  [SKIP] {table_name} 已存在")
    else:
        if not if_table_exist(conn, FEATURE_OTHER_TABLE):
            create_feature_asn_table(conn, FEATURE_OTHER_TABLE)
            _ok(FEATURE_OTHER_TABLE)
        else:
            print(f"  [SKIP] {FEATURE_OTHER_TABLE} 已存在")

        for country_cn, country_en in BIG_COUNTRY.items():
            table_name = f"feature_{country_en}"
            if not if_table_exist(conn, table_name):
                create_feature_asn_table(conn, table_name)
                _ok(table_name)
            else:
                print(f"  [SKIP] {table_name} 已存在")


def init_static_tables(conn):
    """创建不按月分表的静态/全局表"""
    print("\n=== 创建静态表 ===")

    if not if_table_exist(conn, "users"):
        create_login_table(conn, "users")
        _ok("users")
    else:
        print("  [SKIP] users 已存在")

    if not if_table_exist(conn, "as_info"):
        create_as_info_table(conn)
        _ok("as_info")
    else:
        print("  [SKIP] as_info 已存在")

    if not if_table_exist(conn, ROUTING_RES_TABLE):
        create_prefix_count_table(conn, ROUTING_RES_TABLE)
        _ok(ROUTING_RES_TABLE)
    else:
        ensure_prefix_count_table_columns(conn, ROUTING_RES_TABLE)
        print(f"  [SKIP] {ROUTING_RES_TABLE} 已存在")

    if not if_table_exist(conn, VP_RES_TABLE):
        create_vp_resource_table(conn, VP_RES_TABLE)
        _ok(VP_RES_TABLE)
    else:
        print(f"  [SKIP] {VP_RES_TABLE} 已存在")

    if not if_table_exist(conn, COUNTRY_TOPOLOGY_EDGE_TABLE):
        create_country_topology_edge_table(conn, COUNTRY_TOPOLOGY_EDGE_TABLE)
        _ok(COUNTRY_TOPOLOGY_EDGE_TABLE)
    else:
        print(f"  [SKIP] {COUNTRY_TOPOLOGY_EDGE_TABLE} 已存在")

    if not if_table_exist(conn, COUNTRY_TOPOLOGY_SNAPSHOT_TABLE):
        create_country_topology_snapshot_table(conn, COUNTRY_TOPOLOGY_SNAPSHOT_TABLE)
        _ok(COUNTRY_TOPOLOGY_SNAPSHOT_TABLE)
    else:
        print(f"  [SKIP] {COUNTRY_TOPOLOGY_SNAPSHOT_TABLE} 已存在")


def auto_init_db():
    """
    Flask 应用启动时自动调用：检查并创建缺失的表。
    使用当前 UTC 月份作为月表后缀。
    """
    month_suffix = datetime.datetime.utcnow().strftime("%Y%m")
    conn = get_conn(DATABASE, USER, PASSWORD, HOST, PORT)
    try:
        init_static_tables(conn)
        init_monthly_tables(conn, month_suffix)
        init_feature_tables(conn, month_suffix)
        print(f"[init_db] 数据库表初始化完成 (月份: {month_suffix})")
    except Exception as e:
        print(f"[init_db] 数据库表初始化失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="初始化 Domeye 数据库表")
    parser.add_argument(
        "--month",
        type=str,
        default=None,
        help="月份后缀，格式 YYYYMM，默认使用当前 UTC 月份",
    )
    args = parser.parse_args()
    month_suffix = args.month or datetime.datetime.utcnow().strftime("%Y%m")

    print(f"连接数据库: {HOST}:{PORT}/{DATABASE} (user={USER})")
    conn = get_conn(DATABASE, USER, PASSWORD, HOST, PORT)
    print("连接成功！")

    try:
        init_static_tables(conn)
        init_monthly_tables(conn, month_suffix)
        init_feature_tables(conn, month_suffix)
        print("\n===== 数据库初始化完成 =====\n")
    except Exception as e:
        print(f"\n初始化失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
