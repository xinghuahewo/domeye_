import sys
import os
import re
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.logger import database_logger
from config.config import SOURCE
from config.config import FEATURE_ASN_MONTHLY_ENABLED, FEATURE_ASN_OLD_SUFFIX
from database.utils import if_table_exist
from database.utils import get_tables_by_time
import datetime
import psycopg2.extras
import traceback
import pandas as pd

_MONTH_TABLE_RE = re.compile(r".*_\d{6}$")
# 临时兼容：2026-01-29 00:10:00 之前走“老表”(feature_other/feature_{US/...})
# 之后走“月表”(feature_other_YYYYMM/feature_{US/...}_YYYYMM)。
_FEATURE_ASN_MONTHLY_CUTOFF = datetime.datetime(2026, 1, 29, 0, 10, 0)


def _parse_dt(dt_like):
    if isinstance(dt_like, datetime.datetime):
        return dt_like
    if isinstance(dt_like, str):
        return datetime.datetime.strptime(dt_like, "%Y-%m-%d %H:%M:%S")
    raise TypeError(f"Unsupported datetime type: {type(dt_like)}")


def _end_exclusive(end_time):
    # t 字段是 timestamp(0)，用 +1s 将“<= end_time”语义转为 “< end_exclusive”
    return _parse_dt(end_time) + datetime.timedelta(seconds=1)


def _month_start(dt: datetime.datetime) -> datetime.datetime:
    return datetime.datetime(dt.year, dt.month, 1, 0, 0, 0)


def _next_month_start(dt: datetime.datetime) -> datetime.datetime:
    if dt.month == 12:
        return datetime.datetime(dt.year + 1, 1, 1, 0, 0, 0)
    return datetime.datetime(dt.year, dt.month + 1, 1, 0, 0, 0)


def _resolve_feature_asn_slices(conn, base_table: str, start_time, end_time) -> list:
    """
    将逻辑表名（feature_other / feature_US / ...）解析为实际查询切片列表：
    [(table_name, slice_start, slice_end_exclusive), ...]

    - 月表开启：优先使用 {base}_{YYYYMM}；缺月则回退到 {base}_old 并对时间做切片，避免与月表重复。
    - 月表关闭：使用 base；若不存在则回退 base_old。
    - 若传入已是月表名（*_YYYYMM）：直接用该表，不做拆分。
    """
    if not base_table:
        return []

    start_dt = _parse_dt(start_time)
    end_exc = _end_exclusive(end_time)

    # 已是月表名：不拆
    if _MONTH_TABLE_RE.match(base_table):
        if if_table_exist(conn, base_table):
            return [(base_table, start_dt, end_exc)]
        return []

    old_table = f"{base_table}{FEATURE_ASN_OLD_SUFFIX}"

    if not FEATURE_ASN_MONTHLY_ENABLED:
        if if_table_exist(conn, base_table):
            return [(base_table, start_dt, end_exc)]
        if if_table_exist(conn, old_table):
            return [(old_table, start_dt, end_exc)]
        return []

    # ===== 临时兼容逻辑：cutoff 之前查询 base_table =====
    # 若查询窗口完全在 cutoff 之前：直接用 base_table（或 old_table）返回，不走月表路由
    if end_exc <= _FEATURE_ASN_MONTHLY_CUTOFF:
        if if_table_exist(conn, base_table):
            return [(base_table, start_dt, end_exc)]
        if if_table_exist(conn, old_table):
            return [(old_table, start_dt, end_exc)]
        return []

    # 若窗口跨越 cutoff：切两段，前段用 base_table（或 old_table），后段走月表
    pre_slices = []
    if start_dt < _FEATURE_ASN_MONTHLY_CUTOFF < end_exc:
        pre_end = _FEATURE_ASN_MONTHLY_CUTOFF
        if if_table_exist(conn, base_table):
            pre_slices.append((base_table, start_dt, pre_end))
        elif if_table_exist(conn, old_table):
            pre_slices.append((old_table, start_dt, pre_end))
        # 进入后段月表查询
        start_dt = _FEATURE_ASN_MONTHLY_CUTOFF

    month_tables = get_tables_by_time(base_table, start_dt, _parse_dt(end_time))
    has_old = if_table_exist(conn, old_table)

    # 逐月切片：有月表用月表；无月表用 old 表（带时间切片）
    slices = []
    for month_table in month_tables:
        # 从表名推断当月边界
        # month_table 形如 "{base}_YYYYMM"
        month_suffix = month_table[-6:]
        month_dt = datetime.datetime.strptime(month_suffix + "01", "%Y%m%d")
        m_start = _month_start(month_dt)
        m_end = _next_month_start(month_dt)

        slice_start = max(start_dt, m_start)
        slice_end = min(end_exc, m_end)
        if slice_start >= slice_end:
            continue

        if if_table_exist(conn, month_table):
            slices.append((month_table, slice_start, slice_end))
        elif has_old:
            slices.append((old_table, slice_start, slice_end))

    if slices:
        return pre_slices + slices

    # 没有任何月表且无 old 切片时，回退到 base 或 old
    if if_table_exist(conn, base_table):
        return pre_slices + [(base_table, start_dt, end_exc)]
    if if_table_exist(conn, old_table):
        return pre_slices + [(old_table, start_dt, end_exc)]
    return pre_slices

def create_feature_asn_table(conn, table_name):
    """
    Create a table for saving features
    :param conn: database connection
    :param table_name: table name
    :return:
    """
    if if_table_exist(conn, table_name):
        print(f'feature table {table_name} has already exist.')
        return

    cursor = conn.cursor()

    # 　使数据库启动TimescaleDB扩展
    sql = """
        CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
    """
    try:
        cursor.execute(sql)
    except Exception as e:
        # database_logger.error(f'create timescaledb extension failed: {e}')
        print(traceback.format_exc())
        conn.rollback()



 
    sql = """
        CREATE TABLE IF NOT EXISTS {}(
            t               timestamp(0) without time zone      not NULL,
            source          text                                not NULL,
            asn             text                                not NULL,
            country         text                                NULL,
            v4Prefix_num    bigint                              NULL,
            v6Prefix_num    bigint                              NULL,
            v4IP_num        bigint                              NULL,
            announ_num      bigint                              NULL,
            withdraw_num    bigint                              NULL,
            PRIMARY KEY (t, source, asn, country)
        )
    """.format(table_name)
    cursor.execute(sql)
    conn.commit()

    try:
        # sql_index = f"CREATE INDEX idx_t_asn ON {table_name} (t, asn, source);"
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_t_asn ON {table_name} (t, asn)")

        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_source_time 
            ON {table_name} (source, t DESC)
        """)

        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_country_time
            ON {table_name} (country, t DESC, source)
        """)

        conn.commit()
        # print("created index success")
    except Exception as e:
        database_logger.error(f'create index for feature table {table_name} failed: {e}')
        print(traceback.format_exc())
        conn.rollback()
        

    # 将表转化为超表
    try:
        sql = """
            SELECT create_hypertable('{}', 't');
        """.format(table_name)
        cursor.execute(sql)
        print("转化成功")
    except:
        # 如果已经转化为超表，则跳过此步
        # print("转化失败")
        conn.rollback()
        pass

    
    # # 设置自动删除1个月以前的数据
    sql = """
        SELECT add_retention_policy('{}', INTERVAL '180 days');
    """.format(table_name)
    try:
        cursor.execute(sql)
        conn.commit()
        print("succeed")
    except:
        # print("failed")
        conn.rollback()
    finally:
        cursor.close()
        

def ensure_feature_asn_table_bigint(conn, table_name: str) -> None:
    """
    将已存在的 feature ASN 表字段升级为 bigint。

    目标字段：
    - v4prefix_num
    - v6prefix_num
    - announ_num
    - withdraw_num
    """
    if not table_name:
        return
    if not if_table_exist(conn, table_name):
        return

    cursor = conn.cursor()
    try:
        # 注意：PostgreSQL 未加引号的列名会被折叠为小写
        for col in ("v4prefix_num", "v6prefix_num", "announ_num", "withdraw_num"):
            cursor.execute(
                f"ALTER TABLE {table_name} ALTER COLUMN {col} TYPE bigint USING {col}::bigint;"
            )
        conn.commit()
    except Exception as e:
        database_logger.error(f"alter feature table {table_name} columns to bigint error: {e}")
        database_logger.error(traceback.format_exc())
        conn.rollback()
    finally:
        cursor.close()


def insert_feature(conn, t, source, asn, country, announ_num, withdraw_num, v4Prefix_num, v6Prefix_num, v4IP_num, table):
    """
    Insert a item of feature
    :param conn: database connection
    :param t: time
    :param source: source
    :param asn: AS number
    :param announ_num: number of announcement
    :param withdraw_num: number of withdrawal
    :param table: table name
    :return:
    """
    cursor = conn.cursor()
    sql = """
                INSERT INTO {}
                (t, source, asn, country, v4Prefix_num, v6Prefix_num, v4IP_num, announ_num, withdraw_num)
                VALUES 
                (
                %(t)s,
                %(source)s,
                %(asn)s,
                %(country)s,
                %(v4Prefix_num)s,
                %(v6Prefix_num)s,
                %(v4IP_num)s,
                %(announ_num)s,
                %(withdraw_num)s
                );
                """.format(table)
    params = {
        't': t,
        'source': source,
        'asn': asn,
        'country': country,
        'v4Prefix_num': v4Prefix_num,
        'v6Prefix_num': v6Prefix_num,
        'v4IP_num': v4IP_num,
        'announ_num': announ_num,
        'withdraw_num': withdraw_num
    }
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        database_logger.error(f'insert feature table {table} error: {e}')
        conn.rollback()
    finally:
        cursor.close()

def insert_feature_list(conn, insert_list, table):
    """
    Insert a list of items of feature
    :param conn: database connection
    :param insert_list: list of items
    :param table: table name
    :return:
    """
    cursor = conn.cursor()
    sql = """
        INSERT INTO {}
        (t, source, asn, country, v4Prefix_num, v6Prefix_num, v4IP_num, announ_num, withdraw_num)
        VALUES 
        (%(t)s, %(source)s, %(asn)s, %(country)s, %(v4Prefix_num)s, %(v6Prefix_num)s, %(v4IP_num)s, %(announ_num)s, %(withdraw_num)s)
    """.format(table)
    try:
        cursor.executemany(sql, insert_list)
        conn.commit()
    except Exception as e:
        database_logger.error(f'insert feature table {table} error: {e}')
        conn.rollback()
    finally:
        cursor.close()

def get_as_feature_db(conn, feature_table, asn, _6hour_ago: datetime.datetime, _6hour_later: datetime.datetime) -> list:
    """
    获取asn的特征，事件发生前6小时 - 事件发生后6小时
    用于画详细里面的特征图
    :param conn: 数据库连接
    :param feature_table: 特征表名
    :param asn: AS编号
    :param _6hour_ago: 事件发生前6小时
    :param _6hour_later: 事件发生后6小时
    :return:
    """
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    feature_rows = list()

    slices = _resolve_feature_asn_slices(conn, feature_table, _6hour_ago, _6hour_later)
    if not slices:
        cursor.close()
        return []

    parts = []
    params = []
    for table_name, s_start, s_end in slices:
        parts.append(
            f"""
                SELECT asn, announ_num, withdraw_num, t
                FROM {table_name}
                WHERE asn = %s AND t >= %s AND t < %s AND source = %s
            """
        )
        params.extend([str(asn), s_start, s_end, SOURCE])

    sql_feature = " UNION ALL ".join(parts) + " ORDER BY t ASC"
    try:
        cursor.execute(sql_feature, tuple(params))
        feature_rows = cursor.fetchall()
    except Exception as e:
        database_logger.error(f'get as feature from table {feature_table} failed: {e}')
        database_logger.error(traceback.format_exc())
        conn.rollback()
        feature_rows = []
    finally:
        cursor.close()
    return feature_rows


def select_as_feature_db(conn, target, source, start_time, end_time, table_name):
    """从数据库中获取AS数据回撤宣告数据
    :param conn: 数据库连接
    :param target: 目标AS
    :param source: 来源
    :param start_time: 开始时间
    :param end_time: 结束时间
    :param table_name: 表名
    :return: 数据
    """
    cursor = conn.cursor()
    try:
        slices = _resolve_feature_asn_slices(conn, table_name, start_time, end_time)
        if not slices:
            return pd.DataFrame()

        parts = []
        params = []
        for physical_table, s_start, s_end in slices:
            parts.append(
                f"""
                    SELECT t, asn, announ_num as announce, withdraw_num as withdraw
                    FROM {physical_table}
                    WHERE asn = %s AND source = %s AND t >= %s AND t < %s
                """
            )
            params.extend([str(target), source, s_start, s_end])

        sql = " UNION ALL ".join(parts) + " ORDER BY t ASC"
        cursor.execute(sql, tuple(params))
        data = cursor.fetchall()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data, columns=['t', 'asn', 'announce', 'withdraw'])
        # 重命名 将asn重命名为target
        # XXX
        # df.rename(columns={'asn': 'target'}, inplace=True)
    except Exception as e:
        conn.rollback()
        print(f"获取Features数据失败: {e}")
        print(traceback.format_exc())
        return pd.DataFrame()
    finally:
        cursor.close()
    return df

def select_as_list_feature_db(conn, grouped_as_list, start_time, end_time, source=SOURCE):
    """
    使用 UNION ALL 从多个表中一次性获取所有AS的Features数据。
    Args:
        conn: 数据库连接
        grouped_as_list: 一个字典，key是表名，value是该表对应的AS列表。
                         例如: {'feature_cn': [4134, 4809], 'feature_us': [701, 7018]}
        start_time: 开始时间
        end_time: 结束时间
    """
    if not grouped_as_list:
        return pd.DataFrame()

    cursor = conn.cursor()
    all_queries = []
    all_params = []

    for table_base, as_list in grouped_as_list.items():
        if not as_list:
            continue

        slices = _resolve_feature_asn_slices(conn, table_base, start_time, end_time)
        if not slices:
            continue

        placeholders = ','.join(['%s'] * len(as_list))
        for physical_table, s_start, s_end in slices:
            query_part = f"""
                (SELECT 
                    t, asn, 
                    announ_num AS announce, 
                    withdraw_num AS withdraw, 
                    v4prefix_num as v4Prefix_num, 
                    v6prefix_num as v6Prefix_num, 
                    v4ip_num as v4IP_num
                 FROM {physical_table}
                 WHERE asn IN ({placeholders}) AND t >= %s AND t < %s AND source = %s)
            """
            all_queries.append(query_part)
            all_params.extend(as_list)
            all_params.append(s_start)
            all_params.append(s_end)
            all_params.append(source)

    if not all_queries:
        return pd.DataFrame()

    # 使用 UNION ALL 连接所有子查询
    full_sql = " UNION ALL ".join(all_queries) + " ORDER BY asn, t"

    try:
        cursor.execute(full_sql, tuple(all_params))
        as_features_data = cursor.fetchall()
        if not as_features_data:
            return pd.DataFrame()
        # 直接指定dtype可以避免后续转换，提升效率
        as_features_df = pd.DataFrame(
            as_features_data, 
            columns=['t', 'asn', 'announce', 'withdraw', 'v4Prefix_num', 'v6Prefix_num', 'v4IP_num']
        ).astype({'announce': int, 'withdraw': int, 'v4Prefix_num': int, 'v6Prefix_num': int, 'v4IP_num': int})
    except Exception as e:
        conn.rollback()
        print(f"获取Features数据失败: {e}")
        print(traceback.format_exc())
        return pd.DataFrame()
    finally:
        cursor.close()
        
    return as_features_df


def get_as_baseline(conn, asn, source, event_start_time, table_name, hours_before=24):
    """
    获取事件发生前的 AS 基线值（用于计算降幅/比例等）。
    取事件开始前 hours_before 小时到事件开始前 5 分钟的平均值与最大值。

    Returns:
        dict: {v4Prefix_num, v6Prefix_num, v4IP_num, v4Prefix_max, v6Prefix_max, v4IP_max}
    """
    from datetime import timedelta

    if isinstance(event_start_time, str):
        event_start_time = datetime.datetime.strptime(event_start_time, "%Y-%m-%d %H:%M:%S")

    baseline_start = event_start_time - timedelta(hours=hours_before)
    baseline_end = event_start_time - timedelta(minutes=5)

    # 这里 baseline_end 在旧逻辑中是“开区间”(t < baseline_end)，所以用 -1s 保持一致。
    slices = _resolve_feature_asn_slices(
        conn, table_name, baseline_start, baseline_end - datetime.timedelta(seconds=1)
    )
    if not slices:
        return {
            'v4Prefix_num': 0,
            'v6Prefix_num': 0,
            'v4IP_num': 0,
            'v4Prefix_max': 0,
            'v6Prefix_max': 0,
            'v4IP_max': 0,
        }

    parts = []
    params = []
    for physical_table, s_start, s_end in slices:
        parts.append(
            f"""
                SELECT v4prefix_num, v6prefix_num, v4ip_num
                FROM {physical_table}
                WHERE asn = %s AND source = %s AND t >= %s AND t < %s
            """
        )
        params.extend([str(asn), source, s_start, s_end])

    sql = f"""
        SELECT
            AVG(v4prefix_num) as v4Prefix_num,
            AVG(v6prefix_num) as v6Prefix_num,
            AVG(v4ip_num) as v4IP_num,
            MAX(v4prefix_num) as v4Prefix_max,
            MAX(v6prefix_num) as v6Prefix_max,
            MAX(v4ip_num) as v4IP_max
        FROM ({' UNION ALL '.join(parts)}) AS sub
    """
    cursor = conn.cursor()
    try:
        cursor.execute(sql, tuple(params))
        result = cursor.fetchone()
        if result and result[0] is not None:
            return {
                'v4Prefix_num': int(result[0]) if result[0] else 0,
                'v6Prefix_num': int(result[1]) if result[1] else 0,
                'v4IP_num': int(result[2]) if result[2] else 0,
                'v4Prefix_max': int(result[3]) if result[3] else 0,
                'v6Prefix_max': int(result[4]) if result[4] else 0,
                'v4IP_max': int(result[5]) if result[5] else 0,
            }
        return {
            'v4Prefix_num': 0,
            'v6Prefix_num': 0,
            'v4IP_num': 0,
            'v4Prefix_max': 0,
            'v6Prefix_max': 0,
            'v4IP_max': 0,
        }
    except Exception as e:
        conn.rollback()
        print(f"获取AS基线数据失败: {e}")
        print(traceback.format_exc())
        return {
            'v4Prefix_num': 0,
            'v6Prefix_num': 0,
            'v4IP_num': 0,
            'v4Prefix_max': 0,
            'v6Prefix_max': 0,
            'v4IP_max': 0,
        }
    finally:
        cursor.close()


def get_as_current(conn, asn, source, event_start_time, table_name, minutes_after=60):
    """
    获取事件后 minutes_after 分钟内的最小值（用于回撤/中断期“当前值”）。

    Returns:
        dict: {v4Prefix_num, v6Prefix_num, v4IP_num}
    """
    from datetime import timedelta

    if isinstance(event_start_time, str):
        event_start_time = datetime.datetime.strptime(event_start_time, "%Y-%m-%d %H:%M:%S")

    current_start = event_start_time
    current_end = event_start_time + timedelta(minutes=minutes_after)

    slices = _resolve_feature_asn_slices(conn, table_name, current_start, current_end)
    if not slices:
        return {'v4Prefix_num': 0, 'v6Prefix_num': 0, 'v4IP_num': 0}

    parts = []
    params = []
    for physical_table, s_start, s_end in slices:
        parts.append(
            f"""
                SELECT v4prefix_num, v6prefix_num, v4ip_num
                FROM {physical_table}
                WHERE asn = %s AND source = %s AND t >= %s AND t < %s
            """
        )
        params.extend([str(asn), source, s_start, s_end])

    sql = f"""
        SELECT
            MIN(v4prefix_num) as v4Prefix_num,
            MIN(v6prefix_num) as v6Prefix_num,
            MIN(v4ip_num) as v4IP_num
        FROM ({' UNION ALL '.join(parts)}) AS sub
    """
    cursor = conn.cursor()
    try:
        cursor.execute(sql, tuple(params))
        result = cursor.fetchone()
        if result and result[0] is not None:
            return {
                'v4Prefix_num': int(result[0]) if result[0] else 0,
                'v6Prefix_num': int(result[1]) if result[1] else 0,
                'v4IP_num': int(result[2]) if result[2] else 0,
            }
        return {'v4Prefix_num': 0, 'v6Prefix_num': 0, 'v4IP_num': 0}
    except Exception as e:
        conn.rollback()
        print(f"获取AS当前值数据失败: {e}")
        print(traceback.format_exc())
        return {'v4Prefix_num': 0, 'v6Prefix_num': 0, 'v4IP_num': 0}
    finally:
        cursor.close()


def get_as_time_series(conn, asn, source, start_time, end_time, table_name):
    """
    获取 AS 在指定时间段的时序数据（用于图表/近邻采样）。

    Returns:
        list[dict]: [{t, v4Prefix_num, v6Prefix_num, v4IP_num, announce, withdraw}]
    """
    if isinstance(start_time, str):
        start_time = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
    if isinstance(end_time, str):
        end_time = datetime.datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")

    slices = _resolve_feature_asn_slices(conn, table_name, start_time, end_time)
    if not slices:
        return []

    parts = []
    params = []
    for physical_table, s_start, s_end in slices:
        parts.append(
            f"""
                SELECT t, v4prefix_num, v6prefix_num, v4ip_num, announ_num, withdraw_num
                FROM {physical_table}
                WHERE asn = %s AND source = %s AND t >= %s AND t < %s
            """
        )
        params.extend([str(asn), source, s_start, s_end])

    sql = " UNION ALL ".join(parts) + " ORDER BY t ASC"
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                't': str(row['t']),
                'v4Prefix_num': row['v4prefix_num'] or 0,
                'v6Prefix_num': row['v6prefix_num'] or 0,
                'v4IP_num': row['v4ip_num'] or 0,
                'announce': row['announ_num'] or 0,
                'withdraw': row['withdraw_num'] or 0,
            })
        return result
    except Exception as e:
        conn.rollback()
        print(f"获取AS时序数据失败: {e}")
        print(traceback.format_exc())
        return []
    finally:
        cursor.close()
