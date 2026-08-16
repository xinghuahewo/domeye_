import json
from pandas import DataFrame
import psycopg2
import traceback
import sys
import os
import pandas as pd
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.logger import database_logger
from database.utils import time_cost, if_table_exist

@time_cost
def create_as_outage_table(conn, as_outage_table):
    """
    If the table does not exist, create the table
    :param conn: database connection
    :param as_outage_table: as outage table name
    :return:
    """
    cursor = conn.cursor()
    sql = """
        CREATE TABLE if not exists {}(
        source                  varchar(8), 
        asn		                text,
        outage_id	            int,
        is_private_as           boolean,
        private_as_city         text,
        s_time		            timestamp(0) without time zone  not NULL,
        e_time		            timestamp(0) without time zone,
        duration	            interval	DAY TO SECOND (0),
        max_outage_prefix_ratio	decimal(4, 3)	not NULL,
        max_outage_prefix_num	int not NULL,
        total_prefix_num	    int not NULL,
        outage_level		    varchar(8)	not NULL,
        outage_level_descr      text        not NULL,
        country		            text,
        as_name		            text,
        org_name 	            text,
        as_type		            text,
        as_descr                text, 
        as_admin                text, 
        pre_vp_paths            jsonb,
        eve_vp_paths            jsonb,
        next_vp_paths           jsonb,
        outage_prefixes         jsonb,
        event_info              text, 
        primary key(source, asn, outage_id)
        );
        """.format(as_outage_table)
    cursor.execute(sql)
    conn.commit()
    
    # 创建优化索引
    try:
        # 复合索引优化主键查询和时间范围查询
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{as_outage_table}_source_asn_time 
            ON {as_outage_table} (source, asn, s_time DESC, e_time DESC)
        """)

        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{as_outage_table}_asn_time 
            ON {as_outage_table} (asn, s_time DESC, e_time DESC)
        """)
        
        # 按中断等级查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{as_outage_table}_level_time 
            ON {as_outage_table} (outage_level, s_time DESC) WHERE outage_level IS NOT NULL
        """)
        
        # 按国家和时间查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{as_outage_table}_country_time 
            ON {as_outage_table} (country, s_time DESC) WHERE country IS NOT NULL
        """)
        
        
        conn.commit()
        database_logger.info(f'Successfully created optimized indexes for table {as_outage_table}')
        
    except Exception as e:
        database_logger.warning(f'Failed to create some indexes for table {as_outage_table}: {e}')
        conn.rollback()
    finally:
        cursor.close()


def get_as_outage_id(conn, as_outage_table):
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    sql = """
        select source, asn, max(outage_id) from {} group by source, asn;
    """.format(as_outage_table)
    try:
        cursor.execute(sql)
        as_rows = cursor.fetchall()
    except Exception as e:
        database_logger.error(f'get as outage id from table {as_outage_table} failed: {e}')
        conn.rollback()
        as_rows = []
    finally:
        cursor.close()
    return as_rows


def as_outage_start(as_outage_event: dict, source, origin: str, as_outage_id: int, conn, table: str):
    """
    Write AS outage start information to database
    :param as_outage_event: dictionary for recording AS outage information
    :param route_as_info: dictionary for storing AS dynamic information
    :param origin: number of the AS to which the prefix belongs
    :param as_outage_id: AS outage number
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
        INSERT INTO {} 
        (is_private_as, 
        private_as_city,
        country,
        as_name,
        org_name,
        as_type,
        as_descr, 
        as_admin, 
        s_time,
        e_time,
        duration,
        total_prefix_num,
        max_outage_prefix_num,
        max_outage_prefix_ratio,
        pre_vp_paths,
        eve_vp_paths,
        next_vp_paths, 
        outage_prefixes,
        outage_level,
        outage_level_descr,
        event_info, 
        source,
        asn,
        outage_id
        ) 
        VALUES 
        (%(is_private_as)s, %(private_as_city)s, %(country)s, %(as_name)s, %(org_name)s, %(as_type)s, 
        %(as_descr)s, %(as_admin)s, %(s_time)s, %(e_time)s, %(duration)s, 
        %(total_prefix_num)s, %(max_outage_prefix_num)s, %(max_outage_prefix_ratio)s, 
        %(pre_vp_paths)s, %(eve_vp_paths)s, %(next_vp_paths)s, %(outage_prefixes)s, 
        %(outage_level)s, %(outage_level_descr)s, %(event_info)s, %(source)s, %(asn)s, %(outage_id)s);
        """.format(table)
    params = {
        'is_private_as': as_outage_event[origin][as_outage_id]['is_private_as'],
        'private_as_city': as_outage_event[origin][as_outage_id]['private_as_city'],
        'country': as_outage_event[origin][as_outage_id]['country'],
        'as_name': as_outage_event[origin][as_outage_id]['as_name'],
        'org_name': as_outage_event[origin][as_outage_id]['org_name'],
        'as_type': as_outage_event[origin][as_outage_id]['as_type'],
        'as_descr': as_outage_event[origin][as_outage_id]['as_descr'],
        'as_admin': as_outage_event[origin][as_outage_id]['as_admin'],
        's_time': as_outage_event[origin][as_outage_id]['s_time'],
        'e_time': as_outage_event[origin][as_outage_id]['e_time'],
        'duration': as_outage_event[origin][as_outage_id]['duration'],
        'total_prefix_num': as_outage_event[origin][as_outage_id]['total_prefix_num'],
        'max_outage_prefix_num': as_outage_event[origin][as_outage_id]['max_outage_prefix_num'],
        'max_outage_prefix_ratio': as_outage_event[origin][as_outage_id]['max_outage_prefix_ratio'],
        'pre_vp_paths': json.dumps(as_outage_event[origin][as_outage_id]['pre_vp_paths']),
        'eve_vp_paths': json.dumps(as_outage_event[origin][as_outage_id]['eve_vp_paths']),
        'next_vp_paths': json.dumps(as_outage_event[origin][as_outage_id]['next_vp_paths']),
        'outage_prefixes': json.dumps(as_outage_event[origin][as_outage_id]['outage_prefixes']),
        'outage_level': as_outage_event[origin][as_outage_id]['outage_level'],
        'outage_level_descr': as_outage_event[origin][as_outage_id]['outage_level_descr'],
        'event_info': as_outage_event[origin][as_outage_id]['event_info'],
        'source': source, 
        'asn': origin,
        'outage_id': as_outage_id
    }
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        database_logger.error(f'write as outage start information to table {table} failed: {e}')
    finally:
        cursor.close()



def as_outage_end(as_outage_event: dict, source, origin: str, as_outage_id: int, conn, table: str):
    """
    Write AS outage end information to database
    :param as_outage_event: dictionary for recording AS outage information
    :param origin: number of the AS to which the prefix belongs
    :param as_outage_id: AS outage number
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
                UPDATE {} SET
                e_time=%s, duration=%s, next_vp_paths=%s, 
                max_outage_prefix_ratio=%s, max_outage_prefix_num=%s, total_prefix_num=%s,
                outage_level=%s, outage_level_descr=%s,
                outage_prefixes=%s
                where source=%s and asn=%s and outage_id=%s;
                """.format(table)
    params = (
        as_outage_event[origin][as_outage_id]['e_time'],
        as_outage_event[origin][as_outage_id]['duration'],
        json.dumps(as_outage_event[origin][as_outage_id]['next_vp_paths']),
        as_outage_event[origin][as_outage_id]['max_outage_prefix_ratio'],
        as_outage_event[origin][as_outage_id]['max_outage_prefix_num'],
        as_outage_event[origin][as_outage_id]['total_prefix_num'],
        as_outage_event[origin][as_outage_id]['outage_level'],
        as_outage_event[origin][as_outage_id]['outage_level_descr'],
        json.dumps(as_outage_event[origin][as_outage_id]['outage_prefixes']),
        source, 
        origin,
        as_outage_id
    )
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        database_logger.error(f'update as outage end information to table {table} failed: {e}')
    finally:
        cursor.close()



def as_outage_update(as_outage_event: dict, source, origin: str, as_outage_id: int, conn, table: str):
    """
    Update AS outage information to database
    :param as_outage_event: dictionary for recording AS outage information
    :param origin: number of the AS to which the prefix belongs
    :param as_outage_id: AS outage number
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
            UPDATE {} SET
            max_outage_prefix_ratio=%s, max_outage_prefix_num=%s, total_prefix_num=%s,
            outage_level=%s, outage_level_descr=%s,
            outage_prefixes=%s, event_info=%s
            where source=%s and asn=%s and outage_id=%s;
            """.format(table)
    params = (
        as_outage_event[origin][as_outage_id]['max_outage_prefix_ratio'],
        as_outage_event[origin][as_outage_id]['max_outage_prefix_num'],
        as_outage_event[origin][as_outage_id]['total_prefix_num'],
        as_outage_event[origin][as_outage_id]['outage_level'],
        as_outage_event[origin][as_outage_id]['outage_level_descr'],
        json.dumps(as_outage_event[origin][as_outage_id]['outage_prefixes']),
        as_outage_event[origin][as_outage_id]['event_info'],
        source, 
        origin,
        as_outage_id
    )
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        database_logger.error(f'update as outage information to table {table} failed: {e}')
    finally:
        cursor.close()


def get_as_outage_de(conn, table, asn, outage_id, source) -> list:    
    """
    返回AS中断的详情信息
    :param conn: 数据库连接
    :param table: 数据表名
    :param asn: AS编号
    :param outage_id: AS中断id
    :return: AS中断的详情信息
    """
    row = list()
    if if_table_exist(conn, table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
            select asn, as_name, country, org_name, s_time, duration, outage_level, pre_vp_paths, eve_vp_paths,
                   outage_prefixes, total_prefix_num, max_outage_prefix_num, as_type, e_time, 
                   outage_level_descr, event_info, as_descr, as_admin, next_vp_paths
            from {}
            where asn = '{}' and outage_id = '{}' and source = '{}';
        """.format(table, asn, outage_id, source)
        try:
            cursor.execute(sql)
            row = cursor.fetchall()
        except Exception as e:
            database_logger.error(f'get as outage detail from table {table} failed: {e}')
            database_logger.error(traceback.format_exc())
            conn.rollback()
            row = []
        finally:
            cursor.close()
    return row

def select_as_outage_asn_db(conn, as_outage_table, start_time, end_time, country) -> list:
    """从数据库中获取当前发生中断的AS

    Args:
        conn: 数据库连接
        table: 数据表名
        start_time: 开始时间
        end_time: 结束时间
        country: 国家

    Returns:
        list: 包含AS中断信息的列表
    """
    if country == '' or country is None:
        sql = f"""
        SELECT distinct asn
        FROM {as_outage_table}
        WHERE (e_time is null or e_time >= '{end_time}') 
            and (country != '未知' or country is not null)
        """ 
    else:
        sql = f"""
            SELECT distinct asn
            FROM {as_outage_table}
            WHERE (e_time is null or e_time >= '{end_time}') 
                and country = '{country}'
                and (country != '未知' or country is not null)
        """
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        data = cursor.fetchall()
        as_list = [item[0] for item in data]
    except Exception as e:
        conn.rollback()
        print(f"获取AS断网列表失败: {e}")
        print(traceback.format_exc())
        return []
    finally:
        cursor.close()
    return as_list


def select_as_outage_by_interval(conn, source, start_time, end_time, country, tables):
    """
    从数据库中获取指定时间范围内的AS中断信息
    支持多表联查，使用UNION连接。

    Args:
        conn: 数据库连接
        source: 数据源
        start_time: 开始时间
        end_time: 结束时间
        country: 国家
        tables: 数据表名列表

    Returns:
        list: AS中断信息表 [(asn, s_time, e_time), ...]
    """
    if not tables:
        return []
    
    # 确保tables是列表格式
    if isinstance(tables, str):
        tables = [tables]
    
    # 验证表是否存在，只查询存在的表
    existing_tables = []
    for table in tables:
        if if_table_exist(conn, table):
            existing_tables.append(table)
        else:
            print(f"警告: 表 {table} 不存在，跳过查询")
    
    if not existing_tables:
        print("错误: 所有指定的表都不存在")
        return []
    
    # 构建多表UNION查询
    union_queries = []
    for table in existing_tables:
        # country 为空时按全局聚合，不再加国家过滤。
        if country:
            table_query = f"""
            SELECT DISTINCT asn, s_time, e_time, '{table}' as source_table
            FROM {table}
            WHERE country = %s AND source = %s
            AND (
                s_time <= %s::timestamp
                AND (e_time >= %s::timestamp OR e_time IS NULL)
            )
            """
        else:
            table_query = f"""
            SELECT DISTINCT asn, s_time, e_time, '{table}' as source_table
            FROM {table}
            WHERE source = %s
            AND (
                s_time <= %s::timestamp
                AND (e_time >= %s::timestamp OR e_time IS NULL)
            )
            """
        union_queries.append(table_query)
    
    # 使用UNION ALL连接所有查询
    final_sql = " UNION ALL ".join(union_queries)
    
    final_sql = f"""
    SELECT DISTINCT asn, s_time, e_time
    FROM (
        {final_sql}
    ) AS combined_results
    ORDER BY asn, s_time
    """
    
    cursor = conn.cursor()
    try:
        # 为每个表准备相同的参数
        params = []
        for _ in existing_tables:
            if country:
                params.extend([country, source, end_time, start_time])
            else:
                params.extend([source, end_time, start_time])
        
        cursor.execute(final_sql, params)
        data = cursor.fetchall()
        
        print(f"从 {existing_tables} 个表中查询到 {len(data)} 条AS中断记录")
        return data
        
    except Exception as e:
        conn.rollback()
        print(f"获取AS中断记录失败: {e}")
        print(traceback.format_exc())
        return []
    finally:
        cursor.close()


def select_as_outage_detail_by_interval(conn, source, start_time, end_time, country, tables):
    """
    获取与指定时间范围重叠的AS中断明细。

    Returns:
        list: [(asn, country, as_name, org_name, as_type, outage_id,
                total_prefix_num, max_outage_prefix_num, max_outage_prefix_ratio,
                outage_level, outage_level_descr, s_time, e_time, duration), ...]
    """
    if not tables:
        return []

    if isinstance(tables, str):
        tables = [tables]

    existing_tables = []
    for table in tables:
        if if_table_exist(conn, table):
            existing_tables.append(table)
        else:
            print(f"警告: 表 {table} 不存在，跳过查询")

    if not existing_tables:
        print("错误: 所有指定的表都不存在")
        return []

    union_queries = []
    for table in existing_tables:
        table_query = f"""
        SELECT DISTINCT asn, country, as_name, org_name, as_type, outage_id,
                        total_prefix_num, max_outage_prefix_num, max_outage_prefix_ratio,
                        outage_level, outage_level_descr, s_time, e_time, duration
        FROM {table}
        WHERE country = %s AND source = %s
        AND (
            s_time <= %s::timestamp
            AND (e_time >= %s::timestamp OR e_time IS NULL)
        )
        """
        union_queries.append(table_query)

    final_sql = " UNION ALL ".join(union_queries)
    final_sql = f"""
    SELECT DISTINCT asn, country, as_name, org_name, as_type, outage_id,
                    total_prefix_num, max_outage_prefix_num, max_outage_prefix_ratio,
                    outage_level, outage_level_descr, s_time, e_time, duration
    FROM (
        {final_sql}
    ) AS combined_results
    ORDER BY s_time DESC, asn
    """

    cursor = conn.cursor()
    try:
        params = []
        for _ in existing_tables:
            params.extend([country, source, end_time, start_time])

        cursor.execute(final_sql, params)
        data = cursor.fetchall()

        print(f"从 {existing_tables} 个表中查询到 {len(data)} 条AS中断明细记录")
        return data
    except Exception as e:
        conn.rollback()
        print(f"获取AS中断明细记录失败: {e}")
        print(traceback.format_exc())
        return []
    finally:
        cursor.close()
