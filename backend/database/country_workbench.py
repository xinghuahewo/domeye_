"""国家工作台所需的只读聚合查询。"""

import psycopg2.extras
from psycopg2 import extensions

from config.config import FEATURE_COUNTRY_TABLE, SOURCE
from config.logger import database_logger
from database.dashboard import CORE_EVENT_TYPES
from database.utils import get_tables_by_time, if_table_exist


def _transaction_started_idle(conn):
    return (
        conn is not None
        and getattr(conn, 'closed', 1) == 0
        and conn.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
    )


def _finish_read(conn, started_idle):
    if not started_idle or conn is None or getattr(conn, 'closed', 1) != 0:
        return
    try:
        if conn.get_transaction_status() != extensions.TRANSACTION_STATUS_IDLE:
            conn.rollback()
    except Exception as error:
        database_logger.warning('cleanup country workbench transaction failed: %s', error)


def get_country_feature_aggregates(conn, previous_start, current_start, end_time):
    """聚合当前与上一等长窗口的国家报文和资源快照。"""

    started_idle = _transaction_started_idle(conn)
    if not if_table_exist(conn, FEATURE_COUNTRY_TABLE):
        _finish_read(conn, started_idle)
        return []
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            """
            WITH ranged AS (
                SELECT
                    t,
                    country,
                    announ_num,
                    withdraw_num,
                    v4prefix_num,
                    v6prefix_num,
                    v4ip_num
                FROM {}
                WHERE source = %s
                  AND country <> 'collect'
                  AND t >= %s
                  AND t <= %s
            ),
            aggregated AS (
                SELECT
                    country,
                    COALESCE(SUM(announ_num) FILTER (WHERE t >= %s), 0)::bigint AS announce,
                    COALESCE(SUM(withdraw_num) FILTER (WHERE t >= %s), 0)::bigint AS withdraw,
                    COALESCE(SUM(announ_num) FILTER (WHERE t < %s), 0)::bigint AS previous_announce,
                    COALESCE(SUM(withdraw_num) FILTER (WHERE t < %s), 0)::bigint AS previous_withdraw,
                    COUNT(*) FILTER (WHERE t >= %s)::integer AS sample_count,
                    MAX(t) FILTER (WHERE t >= %s) AS latest_observation,
                    (ARRAY_AGG(v4prefix_num ORDER BY t DESC) FILTER (WHERE t >= %s))[1] AS ipv4_prefixes,
                    (ARRAY_AGG(v6prefix_num ORDER BY t DESC) FILTER (WHERE t >= %s))[1] AS ipv6_prefixes,
                    (ARRAY_AGG(v4ip_num ORDER BY t DESC) FILTER (WHERE t >= %s))[1] AS ipv4_addresses,
                    (ARRAY_AGG(v4prefix_num ORDER BY t DESC) FILTER (WHERE t < %s))[1] AS baseline_ipv4_prefixes,
                    (ARRAY_AGG(v6prefix_num ORDER BY t DESC) FILTER (WHERE t < %s))[1] AS baseline_ipv6_prefixes,
                    (ARRAY_AGG(v4ip_num ORDER BY t DESC) FILTER (WHERE t < %s))[1] AS baseline_ipv4_addresses
                FROM ranged
                GROUP BY country
            ),
            peaks AS (
                SELECT DISTINCT ON (country)
                    country,
                    t AS peak_time,
                    (COALESCE(announ_num, 0) + COALESCE(withdraw_num, 0))::bigint AS peak_updates
                FROM ranged
                WHERE t >= %s
                ORDER BY country, peak_updates DESC, t DESC
            )
            SELECT aggregated.*, peaks.peak_updates, peaks.peak_time
            FROM aggregated
            LEFT JOIN peaks USING (country)
            WHERE aggregated.sample_count > 0
            """.format(FEATURE_COUNTRY_TABLE),
            (
                SOURCE,
                previous_start,
                end_time,
                current_start,
                current_start,
                current_start,
                current_start,
                current_start,
                current_start,
                current_start,
                current_start,
                current_start,
                current_start,
                current_start,
                current_start,
                current_start,
            ),
        )
        return cursor.fetchall()
    except Exception:
        conn.rollback()
        database_logger.exception('query country feature aggregates failed')
        raise
    finally:
        cursor.close()
        _finish_read(conn, started_idle)


def get_country_event_counts(conn, start_time, end_time):
    """按国家与风险等级聚合六类核心异常。"""

    started_idle = _transaction_started_idle(conn)
    rows = []
    try:
        for table_name in get_tables_by_time('event_table', start_time, end_time):
            if not if_table_exist(conn, table_name):
                continue
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            try:
                cursor.execute(
                    """
                    SELECT attacked_country, level, COUNT(*) AS event_count
                    FROM {}
                    WHERE event_type = ANY(%s)
                      AND s_time >= %s
                      AND s_time <= %s
                      AND attacked_country IS NOT NULL
                    GROUP BY attacked_country, level
                    """.format(table_name),
                    (list(CORE_EVENT_TYPES), start_time, end_time),
                )
                rows.extend(cursor.fetchall())
            finally:
                cursor.close()
        return rows
    except Exception:
        conn.rollback()
        database_logger.exception('query country event counts failed')
        raise
    finally:
        _finish_read(conn, started_idle)


def get_country_sparklines(conn, countries, start_time, end_time):
    """仅为排行涉及的国家返回小时级双折线。"""

    if not countries:
        return []
    started_idle = _transaction_started_idle(conn)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            """
            SELECT
                country,
                date_trunc('hour', t) AS bucket,
                COALESCE(SUM(announ_num), 0)::bigint AS announce,
                COALESCE(SUM(withdraw_num), 0)::bigint AS withdraw
            FROM {}
            WHERE source = %s
              AND country = ANY(%s)
              AND t >= %s
              AND t <= %s
            GROUP BY country, date_trunc('hour', t)
            ORDER BY country, bucket
            """.format(FEATURE_COUNTRY_TABLE),
            (SOURCE, list(countries), start_time, end_time),
        )
        return cursor.fetchall()
    except Exception:
        conn.rollback()
        database_logger.exception('query country sparklines failed')
        raise
    finally:
        cursor.close()
        _finish_read(conn, started_idle)


def get_country_feature_series(conn, country, start_time, end_time):
    """返回单个国家的五分钟级特征序列。"""

    if not country:
        return []
    started_idle = _transaction_started_idle(conn)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            """
            SELECT
                t AS time,
                announ_num AS announce,
                withdraw_num AS withdraw,
                v4prefix_num AS ipv4_prefixes,
                v6prefix_num AS ipv6_prefixes,
                v4ip_num AS ipv4_addresses
            FROM {}
            WHERE source = %s
              AND country = %s
              AND t >= %s
              AND t <= %s
            ORDER BY t
            """.format(FEATURE_COUNTRY_TABLE),
            (SOURCE, country, start_time, end_time),
        )
        return cursor.fetchall()
    except Exception:
        conn.rollback()
        database_logger.exception('query country feature series failed')
        raise
    finally:
        cursor.close()
        _finish_read(conn, started_idle)
