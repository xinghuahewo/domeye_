import json
import psycopg2
from psycopg2 import extras
import traceback
import datetime
import sys
import pandas as pd
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.logger import database_logger
from database.utils import time_cost, if_table_exist

@time_cost
def create_prefix_outage_table(conn, prefix_outage_table):
    """
    If the table does not exist, create the table
    :param conn: database connection
    :param prefix_outage_table: prefix outage table name
    :return:
    """
    cursor = conn.cursor()
    sql = """
        CREATE TABLE if not exists {}(
        source              varchar(8),
        prefix		        varchar(60),
        outage_id	        int,
        asn			        text,
        is_private_as       boolean,
        private_as_city     text,
        s_time		        timestamp(0) without time zone      not NULL,
        e_time		        timestamp(0) without time zone,
        duration	        interval     DAY TO SECOND (0),
        outage_level		varchar(8)	not NULL,
        outage_level_descr  text        not NULL,
        country		        text,
        as_name		        text,
        org_name 	        text,
        as_type		        text,
        as_descr            text,
        as_admin            text,
        pre_vp_paths        jsonb,
        eve_vp_paths        jsonb,
        next_vp_paths       jsonb,
        event_info          text, 
        primary key(source, prefix, outage_id, asn)
        );
        """.format(prefix_outage_table)
    cursor.execute(sql)
    conn.commit()
    
    # 创建优化索引
    try:
        # 复合索引优化主键查询
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{prefix_outage_table}_source_prefix_time 
            ON {prefix_outage_table} (source, prefix, s_time DESC, e_time DESC)
        """)

        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{prefix_outage_table}_prefix_time 
            ON {prefix_outage_table} (prefix, s_time DESC, e_time DESC)
        """)
        
        # 按ASN查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{prefix_outage_table}_asn_time 
            ON {prefix_outage_table} (asn, s_time DESC) WHERE asn IS NOT NULL
        """)
        
        # 按中断等级查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{prefix_outage_table}_level_time 
            ON {prefix_outage_table} (outage_level, s_time DESC) WHERE outage_level IS NOT NULL
        """)
        
        # 按国家查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{prefix_outage_table}_country_time 
            ON {prefix_outage_table} (country, s_time DESC) WHERE country IS NOT NULL
        """)
        
        conn.commit()
        database_logger.info(f'Successfully created optimized indexes for table {prefix_outage_table}')
        
    except Exception as e:
        database_logger.warning(f'Failed to create some indexes for table {prefix_outage_table}: {e}')
        conn.rollback()
    finally:
        cursor.close()

@time_cost
def get_prefix_outage_id(conn, prefix_outage_table):
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    sql = """
        select source, prefix, max(outage_id) from {} group by source, prefix;
    """.format(prefix_outage_table)
    try:
        cursor.execute(sql)
        prefix_rows = cursor.fetchall()
    except Exception as e:
        database_logger.error(f'get prefix outage id from table {prefix_outage_table} failed: {e}')
        prefix_rows = []
    finally:
        cursor.close()
    return prefix_rows


def prefix_outage_start(prefix_outage_event: dict, source, origin: str, prefix: str, prefix_outage_id: int, conn, table: str):
    """
    Write prefix outage start information to database
    :param prefix_outage_event: dictionary for recording prefix outage information
    :param origin: number of the AS to which the prefix belongs
    :param prefix: outage prefix
    :param prefix_outage_id: prefix outage number
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
                INSERT INTO {} 
                (asn,
                is_private_as,
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
                pre_vp_paths,
                eve_vp_paths,
                next_vp_paths, 
                outage_level,
                outage_level_descr,
                event_info, 
                source, 
                prefix,
                outage_id
                )
                VALUES 
                (%(asn)s, %(is_private_as)s, %(private_as_city)s, %(country)s, %(as_name)s, 
                %(org_name)s, %(as_type)s, %(as_descr)s, %(as_admin)s, %(s_time)s, %(e_time)s,
                %(duration)s, %(pre_vp_paths)s, %(eve_vp_paths)s, %(next_vp_paths)s, %(outage_level)s, %(outage_level_descr)s, 
                %(event_info)s, %(source)s, %(prefix)s, %(outage_id)s);
                """.format(table)
    params = {
        'asn': prefix_outage_event[prefix][prefix_outage_id]['asn'],
        'is_private_as': prefix_outage_event[prefix][prefix_outage_id]['is_private_as'],
        'private_as_city': prefix_outage_event[prefix][prefix_outage_id]['private_as_city'],
        'country': prefix_outage_event[prefix][prefix_outage_id]['country'],
        'as_name': prefix_outage_event[prefix][prefix_outage_id]['as_name'],
        'org_name': prefix_outage_event[prefix][prefix_outage_id]['org_name'],
        'as_type': prefix_outage_event[prefix][prefix_outage_id]['as_type'],
        'as_descr': prefix_outage_event[prefix][prefix_outage_id]['as_descr'],
        'as_admin': prefix_outage_event[prefix][prefix_outage_id]['as_admin'],
        's_time': prefix_outage_event[prefix][prefix_outage_id]['s_time'],
        'e_time': prefix_outage_event[prefix][prefix_outage_id]['e_time'],
        'duration': prefix_outage_event[prefix][prefix_outage_id]['duration'],
        'pre_vp_paths': json.dumps(prefix_outage_event[prefix][prefix_outage_id]['pre_vp_paths']),
        'eve_vp_paths': json.dumps(prefix_outage_event[prefix][prefix_outage_id]['eve_vp_paths']),
        'next_vp_paths': json.dumps(prefix_outage_event[prefix][prefix_outage_id]['next_vp_paths']),
        'outage_level': prefix_outage_event[prefix][prefix_outage_id]['outage_level'],
        'outage_level_descr': prefix_outage_event[prefix][prefix_outage_id]['outage_level_descr'],
        'event_info': prefix_outage_event[prefix][prefix_outage_id]['event_info'],
        'source': source, 
        'prefix': prefix,
        'outage_id': prefix_outage_id
    }
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        database_logger.error(f'write prefix outage start information to table {table} failed: {e}')
    finally:
        cursor.close()


def prefix_outage_end(prefix_outage_event: dict, source, origin: str, prefix: str, prefix_outage_id: int, conn, table: str):
    """
    Write prefix outage end information to database
    :param prefix_outage_event: dictionary for recording prefix break information
    :param origin: number of the AS to which the prefix belongs
    :param prefix: outage prefix
    :param prefix_outage_id: prefix outage number
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
                UPDATE {} SET
                e_time=%s, duration=%s, next_vp_paths=%s
                where source=%s and prefix=%s and outage_id=%s and asn=%s;
                """.format(table)
    params = (
        prefix_outage_event[prefix][prefix_outage_id]['e_time'],
        prefix_outage_event[prefix][prefix_outage_id]['duration'],
        json.dumps(prefix_outage_event[prefix][prefix_outage_id]['next_vp_paths']),
        source, 
        prefix,
        prefix_outage_id,
        origin
    )
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        database_logger.error(f'update prefix outage end information to table {table} failed: {e}')
    finally:
        cursor.close()

def get_pre_outage_de(conn, table, prefix, outage_id, source) -> list:
    """
    返回前缀中断的详情信息
    :param conn: 数据库连接
    :param table: 数据表名
    :param prefix: 中断前缀
    :param outage_id: 前缀中断id
    :return: 前缀中断的详情信息
    """
    row = list()
    if if_table_exist(conn, table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
            select prefix, asn, country, s_time, e_time, outage_level_descr, outage_level, 
                   as_name, org_name, as_type, duration, pre_vp_paths, eve_vp_paths, event_info, 
                   as_descr, as_admin, next_vp_paths
            from {}
            where prefix = '{}' and outage_id = '{}' and source = '{}';
        """.format(table, prefix, outage_id, source)
        try:
            cursor.execute(sql)
            row = cursor.fetchall()
        except Exception as e:
            database_logger.error(f'get prefix outage detail from table {table} failed: {e}')
            database_logger.error(traceback.format_exc())
            conn.rollback()
            row = []
        finally:
            cursor.close()
    return row

def select_prefix_outage_by_interval(conn, source, start_time, end_time, country, asn, tables):
    """
    获取与指定时间范围重叠的前缀中断记录。
    支持多表联查，使用UNION连接。
    
    Args:
        conn: 数据库连接
        source: 数据源
        start_time: 开始时间
        end_time: 结束时间
        country: 国家（可选）
        asn: AS号（可选）
        tables: 数据表名列表

    Returns:
        list: 包含前缀中断记录的列表 [(prefix, s_time, e_time), ...]
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
        if country:
            table_query = f"""
            SELECT DISTINCT prefix, s_time, e_time, '{table}' as source_table
            FROM {table}
            WHERE country = %s AND source = %s
            AND (
                s_time <= %s::timestamp
                AND (e_time >= %s::timestamp OR e_time IS NULL)
            )
            """
        elif asn:
            table_query = f"""
            SELECT DISTINCT prefix, s_time, e_time, '{table}' as source_table
            FROM {table}
            WHERE asn = %s AND source = %s
            AND (
                s_time <= %s::timestamp
                AND (e_time >= %s::timestamp OR e_time IS NULL)
            )
            """
        else:
            # country/asn 都为空时按全局聚合。
            table_query = f"""
            SELECT DISTINCT prefix, s_time, e_time, '{table}' as source_table
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
    
    # 对最终结果去重并排序
    final_sql = f"""
    SELECT DISTINCT prefix, s_time, e_time
    FROM (
        {final_sql}
    ) AS combined_results
    ORDER BY s_time DESC, prefix
    """
    
    cursor = conn.cursor()
    try:
        # 为每个表准备相同的参数
        params = []
        for _ in existing_tables:
            if country:
                params.extend([country, source, end_time, start_time])
            elif asn:
                params.extend([asn, source, end_time, start_time])
            else:
                params.extend([source, end_time, start_time])
        
        cursor.execute(final_sql, params)
        data = cursor.fetchall()
        
        print(f"从 {existing_tables} 个表中查询到 {len(data)} 条前缀中断记录")
        return data
        
    except Exception as e:
        conn.rollback()
        print(f"获取前缀中断记录失败: {e}")
        print(traceback.format_exc())
        return []
    finally:
        cursor.close()


def select_prefix_outage_detail_by_interval(conn, source, start_time, end_time, country, asn, tables):
    """
    获取与指定时间范围重叠的前缀中断明细。

    Returns:
        list: [(prefix, asn, country, as_name, org_name, as_type, outage_id,
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
        if country:
            table_query = f"""
            SELECT DISTINCT prefix, asn, country, as_name, org_name, as_type, outage_id,
                            outage_level, outage_level_descr, s_time, e_time, duration
            FROM {table}
            WHERE country = %s AND source = %s
            AND (
                s_time <= %s::timestamp
                AND (e_time >= %s::timestamp OR e_time IS NULL)
            )
            """
        else:
            table_query = f"""
            SELECT DISTINCT prefix, asn, country, as_name, org_name, as_type, outage_id,
                            outage_level, outage_level_descr, s_time, e_time, duration
            FROM {table}
            WHERE asn = %s AND source = %s
            AND (
                s_time <= %s::timestamp
                AND (e_time >= %s::timestamp OR e_time IS NULL)
            )
            """
        union_queries.append(table_query)

    final_sql = " UNION ALL ".join(union_queries)
    final_sql = f"""
    SELECT DISTINCT prefix, asn, country, as_name, org_name, as_type, outage_id,
                    outage_level, outage_level_descr, s_time, e_time, duration
    FROM (
        {final_sql}
    ) AS combined_results
    ORDER BY s_time DESC, prefix, asn
    """

    cursor = conn.cursor()
    try:
        params = []
        for _ in existing_tables:
            if country:
                params.extend([country, source, end_time, start_time])
            else:
                params.extend([asn, source, end_time, start_time])

        cursor.execute(final_sql, params)
        data = cursor.fetchall()

        print(f"从 {existing_tables} 个表中查询到 {len(data)} 条前缀中断明细记录")
        return data
    except Exception as e:
        conn.rollback()
        print(f"获取前缀中断明细记录失败: {e}")
        print(traceback.format_exc())
        return []
    finally:
        cursor.close()


def get_outage_prefixes_by_asn_at(conn, table, asn: str, t_outage, source: str) -> set:
    """
    获取某个 ASN 在指定时刻仍处于中断（回撤）状态的前缀集合。

    判定条件（与时间区间重叠）：
    - s_time <= t_outage
    - e_time is null OR e_time >= t_outage

    Args:
        conn: 数据库连接
        table: prefix_outage_YYYYMM 表名
        asn: ASN
        t_outage: datetime 或字符串（%Y-%m-%d %H:%M:%S）
        source: 数据源（r/c）

    Returns:
        set[str]: prefix 集合
    """
    if not if_table_exist(conn, table):
        return set()

    if isinstance(t_outage, str):
        t_outage = datetime.datetime.strptime(t_outage, "%Y-%m-%d %H:%M:%S")

    cursor = conn.cursor()
    sql = f"""
        SELECT DISTINCT prefix
        FROM {table}
        WHERE asn = %s AND source = %s
          AND s_time <= %s::timestamp
          AND (e_time IS NULL OR e_time >= %s::timestamp)
    """
    try:
        cursor.execute(sql, (str(asn), source, t_outage, t_outage))
        rows = cursor.fetchall()
        return set([r[0] for r in rows if r and r[0]])
    except Exception as e:
        conn.rollback()
        database_logger.error(f'get outage prefixes by asn at time failed: table={table}, asn={asn}, err={e}')
        database_logger.error(traceback.format_exc())
        return set()
    finally:
        cursor.close()
