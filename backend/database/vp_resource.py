import os
import sys

from psycopg2 import sql
from psycopg2.extras import RealDictCursor, execute_values

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.logger import database_logger
from database.utils import if_table_exist


def create_vp_resource_table(conn, table_name):
    if if_table_exist(conn, table_name):
        database_logger.info(f'vp resource table {table_name} has already exist.')
        return

    cursor = conn.cursor()
    try:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
        cursor.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {table_name}(
                    time                timestamp(0) without time zone      not null,
                    asn                 text                                not null,
                    as_name             text                                null,
                    as_rank             integer                             null,
                    ipv4_prefix_count   bigint                              not null default 0,
                    ipv6_prefix_count   bigint                              not null default 0,
                    is_outlier          boolean                             not null default false,
                    PRIMARY KEY (time, asn)
                )
                """
            ).format(table_name=sql.Identifier(table_name))
        )
        conn.commit()

        cursor.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} (asn, time DESC)"
            ).format(
                index_name=sql.Identifier(f'idx_{table_name}_asn_time'),
                table_name=sql.Identifier(table_name),
            )
        )
        cursor.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} (time DESC)"
            ).format(
                index_name=sql.Identifier(f'idx_{table_name}_time'),
                table_name=sql.Identifier(table_name),
            )
        )
        conn.commit()

        try:
            cursor.execute("SELECT create_hypertable(%s, 'time', if_not_exists => TRUE);", (table_name,))
            conn.commit()
        except Exception:
            conn.rollback()

        try:
            cursor.execute("SELECT add_retention_policy(%s, INTERVAL '180 days');", (table_name,))
            conn.commit()
        except Exception:
            conn.rollback()
    except Exception as error:
        conn.rollback()
        database_logger.error(f'create vp resource table {table_name} failed: {error}')
        raise
    finally:
        cursor.close()


def upsert_vp_resource_rows(conn, table_name, rows):
    if not rows:
        return

    cursor = conn.cursor()
    try:
        insert_sql = sql.SQL(
            """
            INSERT INTO {table_name} (
                time,
                asn,
                as_name,
                as_rank,
                ipv4_prefix_count,
                ipv6_prefix_count,
                is_outlier
            ) VALUES %s
            ON CONFLICT (time, asn) DO UPDATE SET
                as_name = EXCLUDED.as_name,
                as_rank = EXCLUDED.as_rank,
                ipv4_prefix_count = EXCLUDED.ipv4_prefix_count,
                ipv6_prefix_count = EXCLUDED.ipv6_prefix_count,
                is_outlier = EXCLUDED.is_outlier
            """
        ).format(table_name=sql.Identifier(table_name))
        values = [
            (
                row['time'],
                row['asn'],
                row.get('as_name'),
                row.get('as_rank'),
                row.get('ipv4_prefix_count', 0),
                row.get('ipv6_prefix_count', 0),
                row.get('is_outlier', False),
            )
            for row in rows
        ]
        execute_values(cursor, insert_sql.as_string(conn), values, page_size=500)
        conn.commit()
    except Exception as error:
        conn.rollback()
        database_logger.error(f'upsert vp resource rows failed for {table_name}: {error}')
        raise
    finally:
        cursor.close()


def count_latest_vp_resources(conn, table_name, asn_like='%%'):
    cursor = conn.cursor()
    try:
        cursor.execute(
            sql.SQL(
                """
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT ON (asn) asn
                    FROM {table_name}
                    WHERE asn LIKE %s
                    ORDER BY asn, time DESC
                ) latest
                """
            ).format(table_name=sql.Identifier(table_name)),
            (asn_like,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    finally:
        cursor.close()


def list_latest_vp_resources(conn, table_name, asn_like='%%', page_num=1, page_size=10):
    offset = (page_num - 1) * page_size
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            sql.SQL(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (asn)
                        asn,
                        as_name,
                        as_rank,
                        ipv4_prefix_count,
                        ipv6_prefix_count,
                        time,
                        is_outlier
                    FROM {table_name}
                    WHERE asn LIKE %s
                    ORDER BY asn, time DESC
                )
                SELECT
                    asn,
                    COALESCE(as_name, '') AS as_name,
                    as_rank,
                    ipv4_prefix_count,
                    ipv6_prefix_count,
                    TO_CHAR(time, 'YYYY-MM-DD HH24:MI:SS') AS latest_time,
                    is_outlier
                FROM latest
                ORDER BY COALESCE(as_rank, 2147483647), asn
                LIMIT %s OFFSET %s
                """
            ).format(table_name=sql.Identifier(table_name)),
            (asn_like, page_size, offset),
        )
        return cursor.fetchall()
    finally:
        cursor.close()
