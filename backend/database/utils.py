"""
Database related operations
"""
import psycopg2
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime
from dateutil.relativedelta import relativedelta

from config.logger import database_logger


def get_conn(database: str, user: str, password: str, host: str, port: int):
    """
    Get database connection
    :param database: database name
    :param user: username
    :param password: password
    :param host: ip
    :param port: port
    :return: database connection
    """
    return psycopg2.connect(database=database, user=user, password=password, host=host, port=port)


def close_conn(conn):
    """
    Close database connection
    :param conn: database connection
    :return:
    """
    conn.close()



def if_table_exist(conn, table_name) -> bool:
    """
    判断数据表是否存在
    :param conn: 数据库连接
    :param table_name: 数据表名
    :return: 存在则返回True, 不存在则返回False
    """
    if not table_name:
        return False
    cursor = conn.cursor()
    # PostgreSQL 未加引号的标识符会折叠为小写；项目里表名通常以未加引号方式使用。
    table_name = str(table_name).lower()
    sql = "select count(*) from pg_class where relname = %s;"
    try:
        cursor.execute(sql, (table_name,))
        result = cursor.fetchall()
        return result[0][0] == 1
    except Exception as e:
        database_logger.error(f'Error checking if table {table_name} exists: {e}')
        return False
    finally:
        cursor.close()

# 计算消耗时间的函数
import time
def time_cost(func):
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        # 输出函数名和消耗时间
        database_logger.info(f"{func.__name__} cost: {end_time - start_time} seconds")
        return result
    return wrapper


def get_tables_by_time(table_prefix, start_time, end_time):
    """
    根据时间区间（一个月一张表）获取对应的表名列表
    Args:
        conn: 数据库连接
        table_prefix: 表命前缀
        start_time(datetime.datetime): 开始时间
        end_time(datetime.datetime): 结束时间
    Returns:
        list: 表名列表
    """
    # 获取start_time的年月 以确定时间表
    start_time_year_month = start_time.strftime('%Y%m')
    end_time_year_month = end_time.strftime('%Y%m')
    table_name = table_prefix + '_' + start_time_year_month
    table_name_list = []
    while start_time_year_month <= end_time_year_month:
        table_name_list.append(table_name)
        start_time = start_time + relativedelta(months=1)
        start_time_year_month = start_time.strftime('%Y%m')
        table_name = table_prefix + '_' + start_time_year_month
    return table_name_list
