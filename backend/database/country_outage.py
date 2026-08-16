import json
import psycopg2
from psycopg2 import extras
import traceback
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.logger import database_logger
from database.utils import if_table_exist, time_cost
from psycopg2 import sql
from database.country_outage_v2_repository import (
    CountryOutageV2RepositoryError,
    create_v2_tables,
    get_v2,
    identifier,
    persist_v2,
)


COUNTRY_OUTAGE_INCIDENT_V2_TABLE = "country_outage_incident_v2"
COUNTRY_OUTAGE_EPISODE_V2_TABLE = "country_outage_episode_v2"
COUNTRY_OUTAGE_OBSERVATION_V2_TABLE = "country_outage_observation_v2"
CountryOutageRepositoryError = CountryOutageV2RepositoryError
_identifier = identifier


def create_country_outage_v2_tables(conn):
    """兼容入口：创建全局 Incident/Episode/Observation v2 表。"""

    return create_v2_tables(conn)


def persist_country_outage_v2(**kwargs):
    """兼容入口：单事务持久化 v2 和旧字段投影。"""

    return persist_v2(**kwargs)


def get_country_outage_v2(conn, *, incident_id=None, legacy_ref=None):
    """兼容入口：读取结构化 Incident v2。"""

    return get_v2(conn, incident_id=incident_id, legacy_ref=legacy_ref)

@time_cost
def create_country_outage_table(conn, country_outage_table):
    """
    If the table does not exist, create the table
    :param conn: database connection
    :param country_outage_table: country outage table name
    :return:
    """
    table_identifier = _identifier(country_outage_table)
    cursor = conn.cursor()
    create_sql = sql.SQL("""
        CREATE TABLE if not exists {}(
        source                  varchar(8), 
        country		            varchar(100),
        outage_id	            int,
        country_chinese_name    text,
        s_time		            timestamp(0) without time zone  not NULL,
        e_time		            timestamp(0) without time zone,
        duration	            interval	DAY TO SECOND (0),
        outage_level            varchar(8)  not NULL,
        outage_level_descr      text        not NULL,
        max_outage_as_ratio	    decimal(4, 3)	not NULL,
        max_outage_as_num		int not NULL,
        total_as_num	        int not NULL,
        outage_ases	            jsonb,
        event_info              text,
        incident_id_v2          text,
        peak_snapshot_id        text,
        legacy_semantics        jsonb,
        primary key(source, country, outage_id)
        );
        """).format(table_identifier)
    cursor.execute(create_sql)
    cursor.execute(
        sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS incident_id_v2 text").format(
            table_identifier
        )
    )
    cursor.execute(
        sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS peak_snapshot_id text").format(
            table_identifier
        )
    )
    cursor.execute(
        sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS legacy_semantics jsonb").format(
            table_identifier
        )
    )
    conn.commit()
    
    # 创建优化索引
    try:
        # 复合索引优化主键查询和时间范围查询
        cursor.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} "
                    "(source, country, s_time DESC, e_time DESC)").format(
                _identifier(
                    "idx_" + country_outage_table + "_source_country_time"
                ),
                table_identifier,
            )
        )
        
        # 按中断等级查询优化
        cursor.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} "
                    "(outage_level, s_time DESC) "
                    "WHERE outage_level IS NOT NULL").format(
                _identifier("idx_" + country_outage_table + "_level_time"),
                table_identifier,
            )
        )
        
        conn.commit()
        database_logger.info(f'Successfully created optimized indexes for table {country_outage_table}')
        
    except Exception as e:
        database_logger.warning(f'Failed to create some indexes for table {country_outage_table}: {e}')
        conn.rollback()
    finally:
        cursor.close()
    create_country_outage_v2_tables(conn)


def get_country_outage_id(conn, country_outage_table):
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    sql = """
        select source, country, max(outage_id) from {} group by source, country;
    """.format(country_outage_table)
    try:
        cursor.execute(sql)
        country_rows = cursor.fetchall()
    except Exception as e:
        database_logger.error(f'get country outage id from table {country_outage_table} failed: {e}')
        conn.rollback()
        country_rows = []
    finally:
        cursor.close()
    return country_rows


def country_outage_start(country_outage_event: dict, source, country: str, country_outage_id: int, conn, table: str):
    """
    Write country outage start information to database
    :param country_outage_event: dictionary for recording country outage information
    :param country: outage country
    :param country_outage_id: country outage number
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
        INSERT INTO {}
        (s_time,
        e_time,
        duration,
        country_chinese_name,
        total_as_num,
        max_outage_as_num,
        max_outage_as_ratio,
        outage_level,
        outage_level_descr,
        outage_ases,
        event_info, 
        source, 
        country,
        outage_id
        ) 
        VALUES 
        (%(s_time)s, %(e_time)s, %(duration)s, %(country_chinese_name)s, %(total_as_num)s, %(max_outage_as_num)s, 
        %(max_outage_as_ratio)s, %(outage_level)s, %(outage_level_descr)s, %(outage_ases)s, %(event_info)s, 
        %(source)s, %(country)s, %(outage_id)s);
        """.format(table)
    params = {
        's_time': country_outage_event[country][country_outage_id]['s_time'],
        'e_time': country_outage_event[country][country_outage_id]['e_time'],
        'duration': country_outage_event[country][country_outage_id]['duration'],
        'country_chinese_name': country_outage_event[country][country_outage_id]['country_chinese_name'],
        'total_as_num': country_outage_event[country][country_outage_id]['total_as_num'],
        'max_outage_as_num': country_outage_event[country][country_outage_id]['max_outage_as_num'],
        'max_outage_as_ratio': country_outage_event[country][country_outage_id]['max_outage_as_ratio'],
        'outage_level': country_outage_event[country][country_outage_id]['outage_level'],
        'outage_level_descr': country_outage_event[country][country_outage_id]['outage_level_descr'],
        'outage_ases': json.dumps(country_outage_event[country][country_outage_id]['outage_ases']),
        'event_info': country_outage_event[country][country_outage_id]['event_info'],
        'source': source, 
        'country': country,
        'outage_id': country_outage_id
    }
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        database_logger.error(f'write country outage start information to table {table} failed: {e}')
    finally:
        cursor.close()



def country_outage_end(country_outage_event: dict, source, country: str, country_outage_id: int, conn, table: str):
    """
    Write country outage end information to database
    :param country_outage_event: dictionary for recording country outage information
    :param country: outage country
    :param country_outage_id: country outage number
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
                UPDATE {} SET
                e_time=%s, duration=%s,
                max_outage_as_ratio=%s,
                max_outage_as_num=%s,
                total_as_num=%s,
                outage_level=%s,
                outage_level_descr=%s,
                outage_ases=%s
                where source=%s and country=%s and outage_id=%s;
                """.format(table)
    params = (
        country_outage_event[country][country_outage_id]['e_time'],
        country_outage_event[country][country_outage_id]['duration'],
        country_outage_event[country][country_outage_id]['max_outage_as_ratio'],
        country_outage_event[country][country_outage_id]['max_outage_as_num'],
        country_outage_event[country][country_outage_id]['total_as_num'],
        country_outage_event[country][country_outage_id]['outage_level'],
        country_outage_event[country][country_outage_id]['outage_level_descr'],
        json.dumps(country_outage_event[country][country_outage_id]['outage_ases']),
        source, 
        country,
        country_outage_id
    )
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        database_logger.error(f'update country outage end information to table {table} failed: {e}')
    finally:
        cursor.close()



def country_outage_update(country_outage_event: dict, source, country: str, country_outage_id: int, conn, table: str):
    """
    Write country outage end information to database
    :param country_outage_event: dictionary for recording country outage information
    :param country: outage country
    :param country_outage_id: country outage number
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
                UPDATE {} 
                SET max_outage_as_ratio=%s, max_outage_as_num=%s, total_as_num=%s,
                outage_level=%s, outage_level_descr=%s,
                outage_ases=%s, event_info=%s
                where source=%s and country=%s and outage_id=%s;
                """.format(table)
    params = (
        country_outage_event[country][country_outage_id]['max_outage_as_ratio'],
        country_outage_event[country][country_outage_id]['max_outage_as_num'],
        country_outage_event[country][country_outage_id]['total_as_num'],
        country_outage_event[country][country_outage_id]['outage_level'],
        country_outage_event[country][country_outage_id]['outage_level_descr'],
        json.dumps(country_outage_event[country][country_outage_id]['outage_ases']),
        country_outage_event[country][country_outage_id]['event_info'],
        source, 
        country,
        country_outage_id
    )
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        database_logger.error(f'update country outage information to table {table} failed: {e}')
    finally:
        cursor.close()


def get_country_outage_de(conn, table, country, outage_id, source) -> list:
    """
    返回国家中断的详情信息
    :param conn: 数据库连接
    :param table: 数据表名
    :param country: 国家两字母表示
    :param outage_id: 国家中断id
    :return: 国家中断的详情信息
    """
    row = list()
    if if_table_exist(conn, table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        query = sql.SQL("""
            select country_chinese_name, total_as_num, s_time, e_time, duration, outage_level, max_outage_as_num,
                   outage_level_descr, outage_ases, event_info,
                   to_jsonb(country_row) ->> 'incident_id_v2' as incident_id_v2,
                   to_jsonb(country_row) ->> 'peak_snapshot_id' as peak_snapshot_id,
                   to_jsonb(country_row) -> 'legacy_semantics' as legacy_semantics
            from {} as country_row
            where country = %s and outage_id = %s and source = %s;
        """).format(_identifier(table))
        try:
            cursor.execute(query, (country, outage_id, source))
            row = cursor.fetchall()
        except Exception as e:
            database_logger.error(f'get country outage detail from table {table} failed: {e}')
            database_logger.error(traceback.format_exc())
            conn.rollback()
            row = []
        finally:
            cursor.close()
    return row


def get_country_as_outage_details(conn, as_outage_table, country_code, event_start_time, event_end_time, source) -> list:
    """
    获取国家内所有AS在指定时间段的中断状态明细
    用于生成报告中的"AS路由回撤状态表"
    
    :param conn: 数据库连接
    :param as_outage_table: AS中断事件表名 (as_outage_YYYYMM)
    :param country_code: 国家两字母代码
    :param event_start_time: 事件开始时间
    :param event_end_time: 事件结束时间
    :param source: 数据源
    :return: AS中断明细列表 [{asn, as_name, org_name, country, s_time, e_time, 
                             total_prefix_num, max_outage_prefix_num, outage_prefixes}]
    """
    rows = []
    if not if_table_exist(conn, as_outage_table):
        return rows
    
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    sql = """
        SELECT asn, as_name, org_name, country, s_time, e_time, duration,
               total_prefix_num, max_outage_prefix_num, outage_prefixes, outage_level
        FROM {}
        WHERE country LIKE %s 
          AND source = %s
          AND s_time >= %s
          AND (e_time <= %s OR e_time IS NULL)
        ORDER BY max_outage_prefix_num DESC
    """.format(as_outage_table)
    
    try:
        # country字段存储的是中文名，需要模糊匹配
        cursor.execute(sql, (f'%', source, event_start_time, event_end_time))
        rows = cursor.fetchall()
    except Exception as e:
        database_logger.error(f'get country AS outage details from table {as_outage_table} failed: {e}')
        database_logger.error(traceback.format_exc())
        conn.rollback()
        rows = []
    finally:
        cursor.close()
    return rows


def get_country_prefix_outage_details(conn, prefix_outage_table, country_code, event_start_time, event_end_time, source) -> list:
    """
    获取国家内所有前缀在指定时间段的回撤明细
    用于生成报告中的"前缀回撤明细表"
    
    :param conn: 数据库连接
    :param prefix_outage_table: 前缀中断事件表名 (prefix_outage_YYYYMM)
    :param country_code: 国家两字母代码
    :param event_start_time: 事件开始时间
    :param event_end_time: 事件结束时间
    :param source: 数据源
    :return: 前缀中断明细列表 [{prefix, asn, s_time, e_time, duration}]
    """
    rows = []
    if not if_table_exist(conn, prefix_outage_table):
        return rows
    
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    sql = """
        SELECT prefix, asn, s_time, e_time, duration, country, as_name, org_name, outage_level
        FROM {}
        WHERE source = %s
          AND s_time >= %s
          AND (e_time <= %s OR e_time IS NULL)
        ORDER BY s_time ASC
    """.format(prefix_outage_table)
    
    try:
        cursor.execute(sql, (source, event_start_time, event_end_time))
        rows = cursor.fetchall()
    except Exception as e:
        database_logger.error(f'get country prefix outage details from table {prefix_outage_table} failed: {e}')
        database_logger.error(traceback.format_exc())
        conn.rollback()
        rows = []
    finally:
        cursor.close()
    return rows


def get_country_allocated_as_count(conn, country_code) -> int:
    """
    获取国家累计分配的AS数量（从as_info表统计）
    
    :param conn: 数据库连接
    :param country_code: 国家两字母代码
    :return: AS数量
    """
    cursor = conn.cursor()
    sql = """
        SELECT COUNT(DISTINCT asn) 
        FROM as_info 
        WHERE as_country_cn LIKE %s OR as_country_cn = %s
    """
    try:
        cursor.execute(sql, (f'%{country_code}%', country_code))
        result = cursor.fetchone()
        return result[0] if result else 0
    except Exception as e:
        database_logger.error(f'get country allocated AS count failed: {e}')
        conn.rollback()
        return 0
    finally:
        cursor.close()
