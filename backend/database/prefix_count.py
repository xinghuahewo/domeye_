import json
import psycopg2
from psycopg2 import extras
import traceback
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.logger import database_logger
from database.utils import if_table_exist


def _column_exists(cursor, table_name, column_name):
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
        LIMIT 1
        """,
        (table_name, column_name),
    )
    return cursor.fetchone() is not None


def _column_type(cursor, table_name, column_name):
    cursor.execute(
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
        LIMIT 1
        """,
        (table_name, column_name),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get('data_type')
    return row[0]


def ensure_prefix_count_table_columns(conn, prefix_count_table):
    """Keep the routing resource table compatible with newly added metrics."""
    cursor = conn.cursor()
    try:
        has_vp = _column_exists(cursor, prefix_count_table, 'vp')
        has_collector = _column_exists(cursor, prefix_count_table, 'collector')
        if has_vp and not has_collector:
            cursor.execute(f"ALTER TABLE {prefix_count_table} RENAME COLUMN vp TO collector;")

        statements = [
            f"ALTER TABLE {prefix_count_table} ADD COLUMN IF NOT EXISTS ipv4_address_count bigint;",
            f"ALTER TABLE {prefix_count_table} ADD COLUMN IF NOT EXISTS ipv6_48_count bigint;",
            f"ALTER TABLE {prefix_count_table} ADD COLUMN IF NOT EXISTS vp_count integer;",
        ]
        for statement in statements:
            cursor.execute(statement)

        bigint_columns = [
            'ipv6_prefix_count',
            'ipv6_prefix_normal_upper',
            'ipv6_prefix_normal_lower',
        ]
        for column_name in bigint_columns:
            if _column_type(cursor, prefix_count_table, column_name) == 'integer':
                cursor.execute(
                    f"ALTER TABLE {prefix_count_table} ALTER COLUMN {column_name} TYPE bigint;"
                )
        conn.commit()
    except Exception as e:
        database_logger.error(f'ensure prefix count table columns failed: {e}')
        conn.rollback()
        raise
    finally:
        cursor.close()

def create_prefix_count_table(conn, prefix_count_table):
    """
    If the table does not exist, create the table
    :param conn: database connection
    :param prefix_count_table: prefix_count_table name
    :return:
    """
    if if_table_exist(conn, prefix_count_table):
        database_logger.info('prefix count table has already exist.')
        ensure_prefix_count_table_columns(conn, prefix_count_table)
        return

    cursor = conn.cursor()

    # 　使数据库启动TimescaleDB扩展
    sql = """
        CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
    """
    cursor.execute(sql)

    sql = """
            CREATE TABLE if not exists {}(
            collector         text,
            time                timestamp(0) without time zone,
            ipv4_prefix_count   int,
            ipv6_prefix_count   bigint,
            ipv4_address_count  bigint,
            ipv6_48_count       bigint,
            vp_count            int,
            private_as_count    int, 
            path_count          int,
            public_as_count     int,
            ipv4_prefix_normal_upper   int, 
            ipv4_prefix_normal_lower   int, 
            ipv6_prefix_normal_upper   bigint, 
            ipv6_prefix_normal_lower   bigint, 
            private_as_normal_upper    int, 
            private_as_normal_lower    int, 
            path_normal_upper          int, 
            path_normal_lower          int, 
            public_as_normal_upper     int, 
            public_as_normal_lower     int, 
            is_outlier          boolean
            );
            """.format(prefix_count_table)
    cursor.execute(sql)
    conn.commit()

    # 将表转化为超表
    try:
        sql = """
            SELECT create_hypertable('{}', 'time');
        """.format(prefix_count_table)
        cursor.execute(sql)
    except:
        # 如果已经转化为超表，则跳过此步
        pass

    
    # 设置自动删除1个月以前的数据
    sql = """
        SELECT add_drop_chunks_policy('{}', INTERVAL '7 days');
    """.format(prefix_count_table)
    try:
        cursor.execute(sql)
        conn.commit()
    except:
        conn.rollback()
    cursor.close()
    ensure_prefix_count_table_columns(conn, prefix_count_table)

def prefix_count_insert(prefix_count_dict, normal_range, collector, time, conn, table):
    """
    Write prefix_count information to database
    :param prefix_count_dict:  dictionary for recording prefix_count information
    :param collector: collector
    :param time: time
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
        INSERT INTO {}
        (collector, time, ipv4_prefix_count, ipv6_prefix_count, ipv4_address_count, ipv6_48_count, vp_count, private_as_count, path_count,
        public_as_count, is_outlier, ipv4_prefix_normal_upper, ipv4_prefix_normal_lower, 
        ipv6_prefix_normal_upper, ipv6_prefix_normal_lower, private_as_normal_upper, 
        private_as_normal_lower, path_normal_upper, path_normal_lower, public_as_normal_upper, 
        public_as_normal_lower)
        VALUES
        (%(collector)s, %(time)s, %(ipv4_prefix_count)s, %(ipv6_prefix_count)s, %(ipv4_address_count)s, %(ipv6_48_count)s, %(vp_count)s, %(private_as_count)s, 
         %(path_count)s, %(public_as_count)s, %(is_outlier)s, %(ipv4_prefix_normal_upper)s, %(ipv4_prefix_normal_lower)s, 
         %(ipv6_prefix_normal_upper)s, %(ipv6_prefix_normal_lower)s, %(private_as_normal_upper)s, 
         %(private_as_normal_lower)s, %(path_normal_upper)s, %(path_normal_lower)s, 
         %(public_as_normal_upper)s, %(public_as_normal_lower)s);
    """.format(table)
    params = {
        'collector': collector,
        'time': time,
        'ipv4_prefix_count': prefix_count_dict[collector][time]['ipv4_prefix_count'],
        'ipv6_prefix_count': prefix_count_dict[collector][time]['ipv6_prefix_count'],
        'ipv4_address_count': prefix_count_dict[collector][time]['ipv4_address_count'],
        'ipv6_48_count': prefix_count_dict[collector][time]['ipv6_48_count'],
        'vp_count': prefix_count_dict[collector][time]['vp_count'],
        'private_as_count': prefix_count_dict[collector][time]['private_as_count'],
        'path_count': prefix_count_dict[collector][time]['path_count'],
        'public_as_count': prefix_count_dict[collector][time]['public_as_count'],
        'is_outlier': prefix_count_dict[collector][time]['is_outlier'], 
        'ipv4_prefix_normal_upper': normal_range[collector]['ipv4_prefix_count']['upper_bound'],
        'ipv4_prefix_normal_lower': normal_range[collector]['ipv4_prefix_count']['lower_bound'],
        'ipv6_prefix_normal_upper': normal_range[collector]['ipv6_prefix_count']['upper_bound'],
        'ipv6_prefix_normal_lower': normal_range[collector]['ipv6_prefix_count']['lower_bound'],
        'private_as_normal_upper': normal_range[collector]['private_as_count']['upper_bound'],
        'private_as_normal_lower': normal_range[collector]['private_as_count']['lower_bound'],
        'path_normal_upper': normal_range[collector]['path_count']['upper_bound'],
        'path_normal_lower': normal_range[collector]['path_count']['lower_bound'],
        'public_as_normal_upper': normal_range[collector]['public_as_count']['upper_bound'],
        'public_as_normal_lower': normal_range[collector]['public_as_count']['lower_bound']
    }
    try:
        cursor.execute(sql, params)
        conn.commit()
        database_logger.info(
            f"[DB] prefix_count_insert ok: table={table}, collector={collector}, time={time}, is_outlier={params.get('is_outlier')}"
        )
    except Exception as e:
        database_logger.error(f'Write prefix_count information to database{table} failed: {e}')
        conn.rollback()
    finally:
        cursor.close()
