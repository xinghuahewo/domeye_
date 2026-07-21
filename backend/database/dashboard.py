"""首页只读聚合所需的数据库查询。"""

import psycopg2.extras
from psycopg2 import extensions

from config.config import FEATURE_COUNTRY_TABLE, SOURCE
from config.logger import database_logger
from database.utils import get_tables_by_time, if_table_exist


CORE_EVENT_TYPES = (
    '前缀劫持',
    '子前缀劫持',
    '前缀中断',
    'AS中断',
    '国家中断',
    '路由泄漏',
)


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
        database_logger.warning('cleanup dashboard read transaction failed: %s', error)


def get_dashboard_event_aggregates(conn, start_time, current_start, end_time):
    """在数据库内完成首页所需的时段、类型和影响对象聚合。"""

    started_idle = _transaction_started_idle(conn)
    statistics = []
    entities = []
    try:
        for table_name in get_tables_by_time('event_table', start_time, end_time):
            if not if_table_exist(conn, table_name):
                continue
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            try:
                cursor.execute(
                    """
                    SELECT
                        CASE WHEN s_time >= %s THEN 'current' ELSE 'previous' END AS period,
                        CASE
                            WHEN s_time >= %s THEN date_trunc('hour', s_time)
                            ELSE NULL
                        END AS bucket,
                        event_type,
                        level,
                        (e_time IS NULL) AS missing_end,
                        COUNT(*) AS event_count
                    FROM {}
                    WHERE event_type = ANY(%s)
                      AND s_time >= %s
                      AND s_time <= %s
                    GROUP BY 1, 2, 3, 4, 5
                    """.format(table_name),
                    (
                        current_start,
                        current_start,
                        list(CORE_EVENT_TYPES),
                        start_time,
                        end_time,
                    ),
                )
                statistics.extend(cursor.fetchall())
                cursor.execute(
                    """
                    SELECT attacked_as, attacked_country, level, COUNT(*) AS event_count
                    FROM {}
                    WHERE event_type = ANY(%s)
                      AND s_time >= %s
                      AND s_time <= %s
                    GROUP BY attacked_as, attacked_country, level
                    """.format(table_name),
                    (list(CORE_EVENT_TYPES), current_start, end_time),
                )
                entities.extend(cursor.fetchall())
            finally:
                cursor.close()
        return statistics, entities
    except Exception:
        conn.rollback()
        database_logger.exception('query dashboard event aggregates failed')
        raise
    finally:
        _finish_read(conn, started_idle)


def get_latest_collector_observation(conn, end_time):
    """返回截止时间之前最近的采集点特征时间。"""

    started_idle = _transaction_started_idle(conn)
    if not if_table_exist(conn, FEATURE_COUNTRY_TABLE):
        _finish_read(conn, started_idle)
        return None
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            """
            SELECT MAX(t) AS latest_observation
            FROM {}
            WHERE country = %s AND source = %s AND t <= %s
            """.format(FEATURE_COUNTRY_TABLE),
            ('collect', SOURCE, end_time),
        )
        row = cursor.fetchone()
        return row['latest_observation'] if row else None
    except Exception:
        conn.rollback()
        database_logger.exception('query latest collector observation failed')
        raise
    finally:
        cursor.close()
        _finish_read(conn, started_idle)
