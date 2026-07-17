import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.logger import database_logger
from utils import if_table_exist
import datetime
import psycopg2.extras
import traceback
import pandas as pd


#### TODO：feature表的创建

def create_feature_table(conn, table_name):
    """
    Create a table for saving features
    :param conn: database connection
    :param table_name: table name
    :return:
    """
    if if_table_exist(conn, table_name):
        # print(f'feature table has already exist.')
        return

    cursor = conn.cursor()

    # 　使数据库启动TimescaleDB扩展
    sql = """
        CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
    """
    cursor.execute(sql)
   
 
    sql = """
        CREATE TABLE IF NOT EXISTS {}(
            t               timestamp(0) without time zone      not NULL,
            source          text                                not NULL,
            asn             text                                not NULL,
            country         text                                NULL,
            v4Prefix_num    int                                 NULL,
            v6Prefix_num    int                                 NULL,
            v4IP_num        int                                 NULL,
            announ_num      int                                 NULL,
            withdraw_num    int                                 NULL,
        );
    """.format(table_name)
    cursor.execute(sql)
    conn.commit()

    try:
        sql_index = f"CREATE INDEX idx_t_asn ON {table_name} (t, asn, source);"
        cursor.execute(sql_index)
        conn.commit()
        print("created index success")
    except:
        print("created index failed")
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
        pass

    
    # # 设置自动删除1个月以前的数据
    sql = """
        SELECT add_retention_policy('{}', INTERVAL '60 days');
    """.format(table_name)
    try:
        cursor.execute(sql)
        conn.commit()
        print("succeed")
    except:
        print("failed")
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

def insert_feature_collect(conn, t, source, v4Prefix_num, v6Prefix_num, v4IP_num, announ_num, withdraw_num, table):
    """
    Insert a item of feature
    :param conn: database connection
    :param t: time
    :param source: source
    :param v4Prefix_num: number of v4 prefix
    :param v6Prefix_num: number of v6 prefix
    :param v4IP_num: number of v4 ip
    :param announ_num: number of announcement
    :param withdraw_num: number of withdrawal
    :param table: table name
    :return:
    """
    cursor = conn.cursor()
    sql = """
                INSERT INTO {}
                (t, source, v4Prefix_num, v6Prefix_num, v4IP_num, announ_num, withdraw_num)
                VALUES 
                (
                %(t)s,
                %(source)s,
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

def get_as_feature_db(conn, feature_table, asn, _6hour_ago: datetime.datetime, _6hour_later: datetime.datetime) -> list:
    """
    获取asn的特征，事件发生前6小时 - 事件发生后6小时
    :param conn: 数据库连接
    :param feature_table: 特征表名
    :param asn: AS编号
    :param _6hour_ago: 事件发生前6小时
    :param _6hour_later: 事件发生后6小时
    :return:
    """
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    feature_rows = list()

    if if_table_exist(conn, feature_table):
        sql_feature = """
                select asn, announ_num, withdraw_num, t
                from {} 
                where asn = '{}' and t >= '{}' and t <= '{}';
            """.format(feature_table, asn, _6hour_ago, _6hour_later)
        try:
            cursor.execute(sql_feature)
            print(sql_feature)
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
        sql = f"""
            SELECT t, asn, announ_num as announce, withdraw_num as withdraw
            FROM {table_name}
            WHERE asn = %s and source = %s and t >= %s and t <= %s
            order by t asc
        """
        cursor.execute(sql, (target, source, start_time, end_time))
        data = cursor.fetchall()
        df = pd.DataFrame(data, columns=['t', 'asn', 'announce', 'withdraw'])
        # 重命名 将asn重命名为target
        df.rename(columns={'asn': 'target'}, inplace=True)
    except Exception as e:
        conn.rollback()
        print(f"获取Features数据失败: {e}")
        print(traceback.format_exc())
        return None
    finally:
        cursor.close()
    return df

def select_country_feature_db(conn, target, source, start_time, end_time, table_name):
    """获取某个国家在某个时间段内的Features宣告回撤
    """
    sql = f"""
        SELECT t, 
                sum(announ_num) as announce, 
                sum(withdraw_num) as withdraw
        FROM {table_name}
        WHERE country = %s AND source = %s AND t >= %s AND t <= %s
        GROUP BY t
        ORDER BY t ASC
    """
    cursor = conn.cursor()
    try:
        cursor.execute(sql, (target, source, start_time, end_time))
        data = cursor.fetchall()
        df = pd.DataFrame(data, columns=['t', 'announce', 'withdraw'])
        df["target"] = target
    except Exception as e:
        conn.rollback()
        print(f"获取Features数据失败: {e}")
        print(traceback.format_exc())
        return None
    finally:
        cursor.close()
    return df

def select_collect_features(conn, source, start_time, end_time, table_name):
    """获取某个时间段内收集点的Features数据
    Returns:
        list: 包含Features数据的列表 asn为collect 代表所有as的总和
    """
    cursor = conn.cursor()
    try:
        sql = f"""
            SELECT t, announ_num as announce, withdraw_num as withdraw
            FROM {table_name}
            WHERE source = %s and t >= %s and t <= %s
            order by t asc
        """
        cursor.execute(sql, (source, start_time, end_time))
        data = cursor.fetchall()
        df = pd.DataFrame(data, columns=['t', 'announce', 'withdraw'])
        df["target"] = "路由采集点"
    except Exception as e:
        conn.rollback()
        print(f"获取Features数据失败: {e}")
        print(traceback.format_exc())
        return None
    finally:
        cursor.close()
    return df

def select_as_list_feature_db(conn, as_list, start_time, end_time, table_name):
    """获取AS的Features数据
    Args:
        conn: 数据库连接
        as_list: AS列表
        start_time: 开始时间
        end_time: 结束时间
        table_name: 表名
    """
    cursor = conn.cursor()
    placeholders = ','.join(['%s'] * len(as_list))
    try:
        sql = f"""
            SELECT t, asn, sum(announ_num) as announce, sum(bf.withdraw_num) as withdraw
            FROM {table_name}
            WHERE asn IN ({placeholders}) AND t >= '{start_time}' AND t <= '{end_time}'
            GROUP BY asn, t
            ORDER BY asn, t
        """
        cursor.execute(sql, as_list)
        as_features_data = cursor.fetchall()
        as_features_df = pd.DataFrame(as_features_data, columns=['t', 'asn', 'announce', 'withdraw'])
    except Exception as e:
        conn.rollback()
        print(f"获取Features数据失败: {e}")
        print(traceback.format_exc())
        return []
    finally:
        cursor.close()
    return as_features_df