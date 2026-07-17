import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.logger import database_logger
from database.utils import if_table_exist, time_cost
import datetime
import psycopg2.extras
import traceback
import pandas as pd

def create_feature_country_table(conn, table_name):
    """
    Create a table for saving features   collcet/country
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
            country         text                                not NULL,
            v4Prefix_num    bigint                              NULL,
            v6Prefix_num    bigint                              NULL,
            v4IP_num        bigint                              NULL,
            announ_num      bigint                              NULL,
            withdraw_num    bigint                              NULL,
            PRIMARY KEY (t, source, country)
        )
    """.format(table_name)
    cursor.execute(sql)
    conn.commit()

    try:
        # sql_index = f"CREATE INDEX idx_t_asn ON {table_name} (t, asn, source);"
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_t_country ON {table_name} (t, country, source)")

        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_source_time 
            ON {table_name} (source, t DESC)
        """)

        conn.commit()
        print("created index success")
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
        print("转化失败")
        conn.rollback()
        pass

    
    # # 设置自动删除2个月以前的数据
    sql = """
        SELECT add_retention_policy('{}', INTERVAL '180 days');
    """.format(table_name)
    try:
        cursor.execute(sql)
        conn.commit()
        print("succeed")
    except:
        print("failed")
        conn.rollback()
    finally:
        cursor.close()
        

def insert_feature_country(conn, t, source, country, v4Prefix_num, v6Prefix_num, v4IP_num, announ_num, withdraw_num, table):
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
                (t, source, country, v4Prefix_num, v6Prefix_num, v4IP_num, announ_num, withdraw_num)
                VALUES 
                (
                %(t)s,
                %(source)s,
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

@time_cost
def select_country_feature_db(conn, target, source, start_time, end_time, table_name):
    """获取某个国家在某个时间段内的Features宣告回撤"""
    sql = f"""
        SELECT t, 
                announ_num as announce, 
                withdraw_num as withdraw, 
                v4prefix_num as v4Prefix_num, 
                v6prefix_num as v6Prefix_num, 
                v4ip_num as v4IP_num
        FROM {table_name}
        WHERE country = %s AND source = %s AND t >= %s AND t <= %s
        ORDER BY t ASC
    """
    cursor = conn.cursor()
    try:
        cursor.execute(sql, (target, source, start_time, end_time))
        data = cursor.fetchall()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data, columns=['t', 'announce', 'withdraw', 'v4Prefix_num', 'v6Prefix_num', 'v4IP_num'])
        df["target"] = target
    except Exception as e:
        conn.rollback()
        print(f"获取Features数据失败: {e}")
        print(traceback.format_exc())
        return pd.DataFrame()
    finally:
        cursor.close()
    return df


def get_country_baseline(conn, country, source, event_start_time, table_name='feature_country', hours_before=24):
    """
    获取事件发生前的基线值（用于计算降幅等）
    取事件开始前 hours_before 小时到事件开始前5分钟的平均值
    
    :param conn: 数据库连接
    :param country: 国家中文名
    :param source: 数据源
    :param event_start_time: 事件开始时间 (datetime)
    :param table_name: 特征表名
    :param hours_before: 取基线的时间窗口（小时）
    :return: dict {v4Prefix_num, v6Prefix_num, v4IP_num}
    """
    from datetime import timedelta
    
    if isinstance(event_start_time, str):
        event_start_time = datetime.datetime.strptime(event_start_time, "%Y-%m-%d %H:%M:%S")
    
    baseline_start = event_start_time - timedelta(hours=hours_before)
    baseline_end = event_start_time - timedelta(minutes=5)
    
    sql = f"""
        SELECT 
            AVG(v4prefix_num) as v4Prefix_num,
            AVG(v6prefix_num) as v6Prefix_num,
            AVG(v4ip_num) as v4IP_num,
            MAX(v4prefix_num) as v4Prefix_max,
            MAX(v6prefix_num) as v6Prefix_max,
            MAX(v4ip_num) as v4IP_max
        FROM {table_name}
        WHERE country = %s AND source = %s AND t >= %s AND t < %s
    """
    cursor = conn.cursor()
    try:
        cursor.execute(sql, (country, source, baseline_start, baseline_end))
        result = cursor.fetchone()
        if result and result[0] is not None:
            return {
                'v4Prefix_num': int(result[0]) if result[0] else 0,
                'v6Prefix_num': int(result[1]) if result[1] else 0,
                'v4IP_num': int(result[2]) if result[2] else 0,
                'v4Prefix_max': int(result[3]) if result[3] else 0,
                'v6Prefix_max': int(result[4]) if result[4] else 0,
                'v4IP_max': int(result[5]) if result[5] else 0
            }
        return {'v4Prefix_num': 0, 'v6Prefix_num': 0, 'v4IP_num': 0, 
                'v4Prefix_max': 0, 'v6Prefix_max': 0, 'v4IP_max': 0}
    except Exception as e:
        conn.rollback()
        print(f"获取基线数据失败: {e}")
        print(traceback.format_exc())
        return {'v4Prefix_num': 0, 'v6Prefix_num': 0, 'v4IP_num': 0,
                'v4Prefix_max': 0, 'v6Prefix_max': 0, 'v4IP_max': 0}
    finally:
        cursor.close()


def get_country_current(conn, country, source, event_start_time, table_name='feature_country', minutes_after=30):
    """
    获取事件发生后的当前值（最低值）
    取事件开始后 minutes_after 分钟内的最小值
    
    :param conn: 数据库连接
    :param country: 国家中文名
    :param source: 数据源
    :param event_start_time: 事件开始时间 (datetime)
    :param table_name: 特征表名
    :param minutes_after: 取值的时间窗口（分钟）
    :return: dict {v4Prefix_num, v6Prefix_num, v4IP_num, sample_time}
    """
    from datetime import timedelta
    
    if isinstance(event_start_time, str):
        event_start_time = datetime.datetime.strptime(event_start_time, "%Y-%m-%d %H:%M:%S")
    
    current_start = event_start_time
    current_end = event_start_time + timedelta(minutes=minutes_after)
    
    sql = f"""
        SELECT 
            MIN(v4prefix_num) as v4Prefix_num,
            MIN(v6prefix_num) as v6Prefix_num,
            MIN(v4ip_num) as v4IP_num
        FROM {table_name}
        WHERE country = %s AND source = %s AND t >= %s AND t <= %s
    """
    cursor = conn.cursor()
    try:
        cursor.execute(sql, (country, source, current_start, current_end))
        result = cursor.fetchone()
        if result and result[0] is not None:
            return {
                'v4Prefix_num': int(result[0]) if result[0] else 0,
                'v6Prefix_num': int(result[1]) if result[1] else 0,
                'v4IP_num': int(result[2]) if result[2] else 0
            }
        return {'v4Prefix_num': 0, 'v6Prefix_num': 0, 'v4IP_num': 0}
    except Exception as e:
        conn.rollback()
        print(f"获取当前值数据失败: {e}")
        print(traceback.format_exc())
        return {'v4Prefix_num': 0, 'v6Prefix_num': 0, 'v4IP_num': 0}
    finally:
        cursor.close()


def get_country_time_series(conn, country, source, start_time, end_time, table_name='feature_country'):
    """
    获取国家在指定时间段的时序数据（用于绘制图表）
    
    :param conn: 数据库连接
    :param country: 国家中文名
    :param source: 数据源
    :param start_time: 开始时间
    :param end_time: 结束时间
    :param table_name: 特征表名
    :return: list of dict [{t, v4Prefix_num, v6Prefix_num, v4IP_num, announce, withdraw}]
    """
    if isinstance(start_time, str):
        start_time = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
    if isinstance(end_time, str):
        end_time = datetime.datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
    
    sql = f"""
        SELECT t, v4prefix_num, v6prefix_num, v4ip_num, announ_num, withdraw_num
        FROM {table_name}
        WHERE country = %s AND source = %s AND t >= %s AND t <= %s
        ORDER BY t ASC
    """
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(sql, (country, source, start_time, end_time))
        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                't': str(row['t']),
                'v4Prefix_num': row['v4prefix_num'] or 0,
                'v6Prefix_num': row['v6prefix_num'] or 0,
                'v4IP_num': row['v4ip_num'] or 0,
                'announce': row['announ_num'] or 0,
                'withdraw': row['withdraw_num'] or 0
            })
        return result
    except Exception as e:
        conn.rollback()
        print(f"获取时序数据失败: {e}")
        print(traceback.format_exc())
        return []
    finally:
        cursor.close()
