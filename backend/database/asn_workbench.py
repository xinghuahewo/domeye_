"""ASN 工作台所需的只读聚合查询。"""

import psycopg2.extras
from psycopg2 import extensions

from config.config import SOURCE
from config.logger import database_logger
from database.dashboard import CORE_EVENT_TYPES
from database.feature_asn import _resolve_feature_asn_slices, select_as_list_feature_db
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
        database_logger.warning('cleanup ASN workbench transaction failed: %s', error)


def _feature_union(conn, grouped_asns, start_time, end_time):
    parts = []
    params = []
    for table_base, asns in grouped_asns.items():
        if not asns:
            continue
        for physical_table, slice_start, slice_end in _resolve_feature_asn_slices(
            conn,
            table_base,
            start_time,
            end_time,
        ):
            parts.append(
                """
                SELECT
                    t,
                    asn,
                    announ_num,
                    withdraw_num,
                    v4prefix_num,
                    v6prefix_num,
                    v4ip_num
                FROM {}
                WHERE asn = ANY(%s)
                  AND source = %s
                  AND t >= %s
                  AND t < %s
                """.format(physical_table)
            )
            params.extend([list(asns), SOURCE, slice_start, slice_end])
    return parts, params


def get_as_feature_aggregates(conn, grouped_asns, previous_start, current_start, end_time):
    """聚合优先监测 ASN 当前与上一等长窗口的报文和资源快照。"""

    started_idle = _transaction_started_idle(conn)
    try:
        frame = select_as_list_feature_db(
            conn,
            grouped_asns,
            previous_start,
            end_time,
            SOURCE,
        )
        if frame.empty:
            return []
        frame = frame.sort_values(['asn', 't'])
        rows = []
        for asn, group in frame.groupby('asn', sort=False):
            current = group[group['t'] >= current_start]
            if current.empty:
                continue
            previous = group[group['t'] < current_start]
            updates = current['announce'] + current['withdraw']
            peak_index = updates.idxmax()
            latest = current.iloc[-1]
            baseline = previous.iloc[-1] if not previous.empty else None

            def latest_value(column):
                values = current[column].dropna()
                return None if values.empty else int(values.iloc[-1])

            def baseline_value(column):
                if baseline is None or baseline[column] is None:
                    return None
                return int(baseline[column])

            rows.append({
                'asn': str(asn),
                'announce': int(current['announce'].sum()),
                'withdraw': int(current['withdraw'].sum()),
                'previous_announce': int(previous['announce'].sum()) if not previous.empty else 0,
                'previous_withdraw': int(previous['withdraw'].sum()) if not previous.empty else 0,
                'sample_count': int(len(current.index)),
                'latest_observation': latest['t'],
                'ipv4_prefixes': latest_value('v4Prefix_num'),
                'ipv6_prefixes': latest_value('v6Prefix_num'),
                'ipv4_addresses': latest_value('v4IP_num'),
                'baseline_ipv4_prefixes': baseline_value('v4Prefix_num'),
                'baseline_ipv6_prefixes': baseline_value('v6Prefix_num'),
                'baseline_ipv4_addresses': baseline_value('v4IP_num'),
                'update_stddev': float(updates.std(ddof=0)) if len(updates.index) > 1 else 0.0,
                'update_average': float(updates.mean()),
                'peak_updates': int(updates.loc[peak_index]),
                'peak_time': current.loc[peak_index, 't'],
            })
        return rows
    except Exception:
        conn.rollback()
        database_logger.exception('query ASN feature aggregates failed')
        raise
    finally:
        _finish_read(conn, started_idle)


def get_as_event_counts(conn, start_time, end_time):
    """按受影响 ASN 与风险等级聚合六类核心异常。"""

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
                    SELECT attacked_as, level, COUNT(*) AS event_count
                    FROM {}
                    WHERE event_type = ANY(%s)
                      AND s_time >= %s
                      AND s_time <= %s
                      AND attacked_as IS NOT NULL
                    GROUP BY attacked_as, level
                    """.format(table_name),
                    (list(CORE_EVENT_TYPES), start_time, end_time),
                )
                rows.extend(cursor.fetchall())
            finally:
                cursor.close()
        return rows
    except Exception:
        conn.rollback()
        database_logger.exception('query ASN event counts failed')
        raise
    finally:
        _finish_read(conn, started_idle)


def get_as_exact_event_rows(conn, asn, start_time, end_time, page_size=10):
    """以数字边界精确匹配受影响 ASN，避免 3356 命中 53356。"""

    started_idle = _transaction_started_idle(conn)
    rows = []
    total_count = 0
    token_pattern = r'(^|[^0-9])(?:AS)?{}([^0-9]|$)'.format(asn)
    try:
        for table_name in get_tables_by_time('event_table', start_time, end_time):
            if not if_table_exist(conn, table_name):
                continue
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            try:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM {}
                    WHERE event_type = ANY(%s)
                      AND s_time >= %s
                      AND s_time <= %s
                      AND COALESCE(attacked_as, '') ~* %s
                    """.format(table_name),
                    (list(CORE_EVENT_TYPES), start_time, end_time, token_pattern),
                )
                total_count += int(cursor.fetchone()[0])
                cursor.execute(
                    """
                    SELECT
                        event_type, level, s_time, e_time, attacker_as, attacked_as,
                        event_info, detail_url, affected_prefix, attacker_org, attacked_org,
                        attacker_country, attacked_country, state, judge_reason,
                        judge_userid, judge_username, judge_time, notify_userid, notify_username,
                        notify_time
                    FROM {}
                    WHERE event_type = ANY(%s)
                      AND s_time >= %s
                      AND s_time <= %s
                      AND COALESCE(attacked_as, '') ~* %s
                    ORDER BY s_time DESC, detail_url DESC
                    LIMIT %s
                    """.format(table_name),
                    (list(CORE_EVENT_TYPES), start_time, end_time, token_pattern, page_size),
                )
                rows.extend(cursor.fetchall())
            finally:
                cursor.close()
        rows.sort(key=lambda row: (row['s_time'], row['detail_url']), reverse=True)
        return rows[:page_size], total_count
    except Exception:
        conn.rollback()
        database_logger.exception('query exact ASN event rows failed')
        raise
    finally:
        _finish_read(conn, started_idle)


def get_as_sparklines(conn, grouped_asns, start_time, end_time):
    """仅为排行涉及的 ASN 返回小时级双折线。"""

    started_idle = _transaction_started_idle(conn)
    parts, params = _feature_union(conn, grouped_asns, start_time, end_time)
    if not parts:
        _finish_read(conn, started_idle)
        return []
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            """
            WITH ranged AS ({})
            SELECT
                asn,
                date_trunc('hour', t) AS bucket,
                COALESCE(SUM(announ_num), 0)::bigint AS announce,
                COALESCE(SUM(withdraw_num), 0)::bigint AS withdraw
            FROM ranged
            GROUP BY asn, date_trunc('hour', t)
            ORDER BY asn, bucket
            """.format(' UNION ALL '.join(parts)),
            tuple(params),
        )
        return cursor.fetchall()
    except Exception:
        conn.rollback()
        database_logger.exception('query ASN sparklines failed')
        raise
    finally:
        cursor.close()
        _finish_read(conn, started_idle)


def get_as_feature_series(conn, table_base, asn, start_time, end_time):
    """返回单个 ASN 的五分钟级特征序列。"""

    if not asn:
        return []
    grouped = {table_base: [asn]}
    started_idle = _transaction_started_idle(conn)
    parts, params = _feature_union(conn, grouped, start_time, end_time)
    if not parts:
        _finish_read(conn, started_idle)
        return []
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            """
            WITH ranged AS ({})
            SELECT
                t AS time,
                announ_num AS announce,
                withdraw_num AS withdraw,
                v4prefix_num AS ipv4_prefixes,
                v6prefix_num AS ipv6_prefixes,
                v4ip_num AS ipv4_addresses
            FROM ranged
            ORDER BY t
            """.format(' UNION ALL '.join(parts)),
            tuple(params),
        )
        return cursor.fetchall()
    except Exception:
        conn.rollback()
        database_logger.exception('query ASN feature series failed')
        raise
    finally:
        cursor.close()
        _finish_read(conn, started_idle)
