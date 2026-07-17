import sys
import os

from requests import get
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.logger import database_logger
from database.utils import time_cost, if_table_exist, get_tables_by_time
import traceback

from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import psycopg2.extras
from psycopg2 import extensions


def _read_transaction_started_idle(conn):
    return (
        conn is not None
        and getattr(conn, 'closed', 1) == 0
        and conn.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
    )


def _cleanup_implicit_read_transaction(conn, started_idle):
    if not started_idle or conn is None or getattr(conn, 'closed', 1) != 0:
        return

    try:
        if conn.get_transaction_status() != extensions.TRANSACTION_STATUS_IDLE:
            conn.rollback()
    except Exception as error:
        database_logger.warning(f'cleanup implicit read transaction failed: {error}')

@time_cost
def create_event_table(conn, event_table):
    """
    If the table does not exist, create the table
    :param conn: database connection
    :param event_table: event_table name
    :return:
    """
    cursor = conn.cursor()
    sql = """
            CREATE TABLE if not exists {}(
            source              varchar(2)   not NULL,
            event_type	        varchar(20),
            level               varchar(8),
            s_time              timestamp(0) without time zone      not NULL,
            e_time              timestamp(0) without time zone,
            duration            interval     DAY TO SECOND (0),
            attacker_as         text,
            attacked_as         text,
            affected_prefix     text, 
            event_info          text,
            detail_url          text,
            attacker_org        text,
            attacked_org        text,
            attacker_country    text,
            attacked_country    text,
            is_domestic         boolean,
            state               varchar(10),
            judge_userid        text,
            judge_username      text,
            judge_time          timestamp(0) without time zone,
            judge_reason        text,
            notify_userid       text,
            notify_username     text,
            notify_time         timestamp(0) without time zone,
            notify_reason       text,
            primary key(detail_url)
            );
            """.format(event_table)
    cursor.execute(sql)
    conn.commit()
    
    # 创建优化索引
    try:

        # 单独索引 source
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{event_table}_source 
            ON {event_table} (source)
        """)

        # 复合索引优化时间和事件类型查询
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{event_table}_type_time 
            ON {event_table} (source, event_type, level, s_time DESC, e_time DESC) WHERE event_type IS NOT NULL
        """)
        
        # 按等级查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{event_table}_level_time 
            ON {event_table} (source, level, s_time DESC) WHERE level IS NOT NULL
        """)
        
        # 按攻击者AS查询优化
        cursor.execute(f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{event_table}_attacker_as_time 
            ON {event_table} (source, attacker_as, s_time DESC) WHERE attacker_as IS NOT NULL
        """)
        
        # 按被攻击AS查询优化
        cursor.execute(f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{event_table}_attacked_as_time 
            ON {event_table} (source, attacked_as, s_time DESC) WHERE attacked_as IS NOT NULL
        """)
        
        # 按国家查询优化
        cursor.execute(f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{event_table}_attacker_country_time 
            ON {event_table} (source, attacker_country, s_time DESC) WHERE attacker_country IS NOT NULL
        """)
        
        cursor.execute(f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{event_table}_attacked_country_time 
            ON {event_table} (attacked_country, s_time DESC) WHERE attacked_country IS NOT NULL
        """)
        
        # 按是否国内事件查询优化
        cursor.execute(f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{event_table}_domestic_time 
            ON {event_table} (source, is_domestic, s_time DESC) WHERE is_domestic IS NOT NULL
        """)
        
        conn.commit()
        database_logger.info(f'Successfully created optimized indexes for table {event_table}')
        
    except Exception as e:
        database_logger.warning(f'Failed to create some indexes for table {event_table}: {e}')
        conn.rollback()
    finally:
        cursor.close()


def event_start(source, event_type, level, s_time, e_time, duration, attacker_as, attacked_as, affected_prefix, event_info, detail_url, 
                attacker_org, attacked_org, attacker_country, attacked_country, state, is_domestic, conn, table):
    """
    Write prefix hijack start information to database
    :param event_type: event_type
    :param level: event level
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
           INSERT INTO {}
           (source, event_type, level, s_time, e_time, duration, attacker_as, attacked_as, affected_prefix, 
           event_info, detail_url, attacker_org, attacked_org, attacker_country, attacked_country, 
           state, is_domestic)
           VALUES
           (%(source)s, %(event_type)s, %(level)s, %(s_time)s, %(e_time)s, %(duration)s, 
            %(attacker_as)s, %(attacked_as)s, %(affected_prefix)s, %(event_info)s, 
            %(detail_url)s, %(attacker_org)s, %(attacked_org)s, 
            %(attacker_country)s, %(attacked_country)s, %(state)s, %(is_domestic)s)
            ON CONFLICT (detail_url) DO NOTHING;
       """.format(table)
    params = {
        'source': source,
        'event_type': event_type,
        'level': level,
        's_time': s_time,
        'e_time': e_time,
        'duration': duration, 
        'attacker_as': attacker_as,
        'attacked_as': attacked_as,
        'affected_prefix': affected_prefix, 
        'event_info': event_info,
        'detail_url': detail_url,
        'attacker_org': attacker_org,
        'attacked_org': attacked_org,
        'attacker_country': attacker_country,
        'attacked_country': attacked_country,
        'state': state, 
        'is_domestic': is_domestic
    }
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        database_logger.error(f'write event information to table {table} failed: {e}')
    finally:
        cursor.close()


def event_end(detail_url, e_time, duration, conn, table):
    """
    Write prefix hijack end information to database
    :param moas_event_dict: dictionary for recording prefix moas information
    :param prefix: hijack prefix
    :param moas_id: prefix moas number
    :param hijack_id: prefix hijack number
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
            UPDATE {} SET
            e_time=%s, duration=%s
            where detail_url=%s;
        """.format(table)
    params = (
        e_time,
        duration, 
        detail_url
    )
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        database_logger.error(f'update event information to table {table} failed: {e}')
    finally:
        cursor.close()



def event_as_outage_update(detail_url, event_info, affected_prefix, level, conn, table):
    """
    Write prefix hijack end information to database
    :param moas_event_dict: dictionary for recording prefix moas information
    :param prefix: hijack prefix
    :param moas_id: prefix moas number
    :param hijack_id: prefix hijack number
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
            UPDATE {} SET
            event_info=%s, affected_prefix=%s, level=%s
            where detail_url=%s;
        """.format(table)
    params = (
        event_info,
        affected_prefix, 
        level, 
        detail_url 
    )
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        database_logger.error(f'update event information to table {table} failed: {e}')
    finally:
        cursor.close()


def event_country_outage_update(detail_url, event_info, attacker_as, attacked_as, level, conn, table):
    """
    Write prefix hijack end information to database
    :param moas_event_dict: dictionary for recording prefix moas information
    :param prefix: hijack prefix
    :param moas_id: prefix moas number
    :param hijack_id: prefix hijack number
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
            UPDATE {} SET
            event_info=%s, attacker_as=%s, attacked_as=%s, level=%s
            where detail_url=%s;
        """.format(table)
    params = (
        event_info,
        attacker_as, 
        attacked_as, 
        level, 
        detail_url 
    )
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        database_logger.error(f'update event information to table {table} failed: {e}')
    finally:
        cursor.close()

def get_event_judgeinfo(conn, event_table, detail_url):
    """
    获取某一个事件的研判相关信息
    :param conn: 数据库连接
    :param event_table: 事件总表
    :param detail_url: 详情url(事件总表的键)
    :return: 事件的rows
    """         
    started_idle = _read_transaction_started_idle(conn)
    row = list()
    if if_table_exist(conn, event_table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
            select state, judge_reason, judge_userid, judge_time
            from {}
            where detail_url = '{}';
        """.format(event_table, detail_url)
        try:
            cursor.execute(sql)
            row = cursor.fetchall()
        except Exception as e:
            database_logger.error(f'get event judge info from table {event_table} failed: {e}')
            database_logger.error(traceback.format_exc())
            conn.rollback()
            row = []
        finally:
            cursor.close()
            _cleanup_implicit_read_transaction(conn, started_idle)
    return row  

def get_top_event_db(conn, event_table, country, bj_week_ago, event_type, page_size):
    """从event_table中获取top事件

    Args:
        conn: 数据库连接
        event_table: 事件总表
        country: 国家
        bj_week_ago: 本周
        event_type: 事件类型
        page_size: 分页大小
    """    
    started_idle = _read_transaction_started_idle(conn)
    cursor = conn.cursor()
    event_rows = list()
    if if_table_exist(conn, event_table):
        sql = """
            select event_type, level, s_time, e_time, attacker_as, attacked_as, 
                event_info, detail_url, affected_prefix, attacker_org, attacked_org, 
                attacker_country, attacked_country, state, judge_reason,
                judge_userid, judge_username, judge_time, notify_userid, notify_username, notify_time
                from {}
                where is_domestic={} and level='high' and s_time > '{}'
                and event_type in {}
                and cast(split_part(detail_url, '/', 4) as INTEGER ) < 10
                and (duration >= '00:03:00' or duration is null)
                order by 
                    case 
                        when attacker_country = '中国' then 0
                        else 1
                    end,
                    s_time desc
        """.format(event_table, country, bj_week_ago, event_type, page_size)
        try:
            cursor.execute(sql)
            event_rows = cursor.fetchall()
        except Exception as e:
            print(f'get top event from table {event_table} failed: {e}')
            print(traceback.format_exc())
            conn.rollback()
            event_rows = []
        finally:
            cursor.close()
            _cleanup_implicit_read_transaction(conn, started_idle)
    return event_rows

def get_event_db_multi_month(conn, source, level, event_type, is_domestic, 
                            attacker_as, attacked_as, attacker_org, attacked_org, attacker_country, attacked_country, event_info, 
                            s_time_start, s_time_end, judge_reason, judge_userid, judge_username, 
                            notify_userid, notify_username, state,
                            judge_time_start, judge_time_end, notify_time_start, notify_time_end,
                            is_boundary_outage, sort_mode, page_size, offset):
    """
    支持最多6个月的跨月查询事件
    Args:
        conn: 数据库连接
    """
    started_idle = _read_transaction_started_idle(conn)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    event_rows = []

    try:
        # 解析时间范围
        start_time = datetime.strptime(s_time_start.strip("'"), '%Y-%m-%d %H:%M:%S')
        end_time = datetime.strptime(s_time_end.strip("'"), '%Y-%m-%d %H:%M:%S')
        
        # 计算需要查询的月份列表
        print(f"查询时间范围: {start_time} - {end_time}")
        tables = get_tables_by_time("event_table", start_time, end_time)

        if len(tables) > 6:
            database_logger.warning(f"查询跨度超过6个月，仅查询最近6个月: {tables[-6:]}")
            tables = tables[-6:]

        # 生成表名列表并检查表是否存在
        valid_tables = []
        for table_name in tables:
            if if_table_exist(conn, table_name):
                valid_tables.append(table_name)
            else:
                database_logger.warning(f"表 {table_name} 不存在，跳过查询")
        
        if not valid_tables:
            database_logger.error("没有找到任何有效的事件表")
            return []
        
        # 构建UNION ALL查询
        sql_parts = []
        for table_name in valid_tables:
            sql_part = _build_single_table_query(
                table_name, source, level, event_type, is_domestic,
                attacker_as, attacked_as, attacker_org, attacked_org, 
                attacker_country, attacked_country, event_info,
                s_time_start, s_time_end, judge_reason, judge_userid, judge_username,
                notify_userid, notify_username, state,
                judge_time_start, judge_time_end, notify_time_start, notify_time_end,
                is_boundary_outage
            )
            sql_parts.append(sql_part)
        
        # 组合最终SQL
        final_sql = f"""
            SELECT * FROM (
                {' UNION ALL '.join(sql_parts)}
            ) t
            ORDER BY {sort_mode}
            LIMIT {page_size} OFFSET {offset};
        """
        
        print(f"执行跨月查询，涉及表: {valid_tables}")
        print(final_sql)
        
        
        cursor.execute(final_sql)
        event_rows = cursor.fetchall()
        print(f"查询到 {len(event_rows)} 条记录")
        
    except Exception as e:
        print(f'跨月查询事件失败: {e}')
        print(traceback.format_exc())
        conn.rollback()
        event_rows = []
    finally:
        cursor.close()
        _cleanup_implicit_read_transaction(conn, started_idle)
    
    return event_rows

def _build_single_table_query(table_name, source, level, event_type, is_domestic,
                             attacker_as, attacked_as, attacker_org, attacked_org,
                             attacker_country, attacked_country, event_info,
                             s_time_start, s_time_end, judge_reason, judge_userid, judge_username,
                             notify_userid, notify_username, state,
                             judge_time_start, judge_time_end, notify_time_start, notify_time_end,
                             is_boundary_outage):
    """
    构建单个表的查询SQL
    """
    return f"""
        SELECT event_type, level, s_time, e_time, attacker_as, attacked_as, 
               event_info, detail_url, affected_prefix, attacker_org, attacked_org, 
               attacker_country, attacked_country, state, judge_reason,
               judge_userid, judge_username, judge_time, notify_userid, notify_username, notify_time
        FROM {table_name}
        WHERE source={source} AND level={level} AND event_type={event_type} AND is_domestic={is_domestic} 
              AND COALESCE(attacker_as, '') LIKE {attacker_as} 
              AND COALESCE(attacked_as, '') LIKE {attacked_as}
              AND COALESCE(attacker_org, '') LIKE {attacker_org} 
              AND COALESCE(attacked_org, '') LIKE {attacked_org}
              AND COALESCE(attacker_country, '') LIKE {attacker_country} 
              AND COALESCE(attacked_country, '') LIKE {attacked_country}
              AND event_info LIKE {event_info} 
              AND s_time >= {s_time_start} AND s_time <= {s_time_end}
              AND COALESCE(judge_reason, '') LIKE {judge_reason}
              AND COALESCE(judge_userid, '') LIKE {judge_userid} 
              AND COALESCE(judge_username, '') LIKE {judge_username}
              AND COALESCE(notify_userid, '') LIKE {notify_userid} 
              AND COALESCE(notify_username, '') LIKE {notify_username}
              AND state = {state} 
              AND COALESCE(judge_time, DATE '0001-01-01') >= {judge_time_start} 
              AND COALESCE(judge_time, DATE '0001-01-01') <= {judge_time_end}
              AND COALESCE(notify_time, DATE '0001-01-01') >= {notify_time_start} 
              AND COALESCE(notify_time, DATE '0001-01-01') <= {notify_time_end}
              AND event_type {is_boundary_outage} '边界中断'
              AND (duration >= '00:03:00' OR duration IS NULL)
              AND CAST(split_part(detail_url, '/', 4) AS INTEGER) < 10
    """

def get_event_count_multi_month(conn, source, level, event_type, is_domestic,
                               attacker_as, attacked_as, attacker_org, attacked_org,
                               attacker_country, attacked_country, event_info,
                               s_time_start, s_time_end, judge_reason, judge_userid, judge_username,
                               notify_userid, notify_username, state,
                               judge_time_start, judge_time_end, notify_time_start, notify_time_end,
                               is_boundary_outage):
    """
    获取跨月查询的总记录数，用于分页
    """
    started_idle = _read_transaction_started_idle(conn)
    cursor = conn.cursor()
    total_count = 0

    try:
        # 解析时间范围
        start_time = datetime.strptime(s_time_start.strip("'"), '%Y-%m-%d %H:%M:%S')
        end_time = datetime.strptime(s_time_end.strip("'"), '%Y-%m-%d %H:%M:%S')
        
        # 计算需要查询的月份列表
        tables = get_tables_by_time("event_table", start_time, end_time)

        if len(tables) > 6:
            tables = tables[-6:]

        # 生成表名列表并检查表是否存在
        valid_tables = []
        for table_name in tables:
            if if_table_exist(conn, table_name):
                valid_tables.append(table_name)
        
        if not valid_tables:
            return 0
        
        # 构建计数查询
        count_parts = []
        for table_name in valid_tables:
            count_part = f"""
                SELECT COUNT(*) as cnt
                FROM {table_name}
                WHERE source = {source} AND level={level} AND event_type={event_type} AND is_domestic={is_domestic} 
                      AND COALESCE(attacker_as, '') LIKE {attacker_as} 
                      AND COALESCE(attacked_as, '') LIKE {attacked_as}
                      AND COALESCE(attacker_org, '') LIKE {attacker_org} 
                      AND COALESCE(attacked_org, '') LIKE {attacked_org}
                      AND COALESCE(attacker_country, '') LIKE {attacker_country} 
                      AND COALESCE(attacked_country, '') LIKE {attacked_country}
                      AND event_info LIKE {event_info} 
                      AND s_time >= {s_time_start} AND s_time <= {s_time_end}
                      AND COALESCE(judge_reason, '') LIKE {judge_reason}
                      AND COALESCE(judge_userid, '') LIKE {judge_userid} 
                      AND COALESCE(judge_username, '') LIKE {judge_username}
                      AND COALESCE(notify_userid, '') LIKE {notify_userid} 
                      AND COALESCE(notify_username, '') LIKE {notify_username}
                      AND state = {state} 
                      AND COALESCE(judge_time, DATE '0001-01-01') >= {judge_time_start} 
                      AND COALESCE(judge_time, DATE '0001-01-01') <= {judge_time_end}
                      AND COALESCE(notify_time, DATE '0001-01-01') >= {notify_time_start} 
                      AND COALESCE(notify_time, DATE '0001-01-01') <= {notify_time_end}
                      AND event_type {is_boundary_outage} '边界中断'
                      AND (duration >= '00:03:00' OR duration IS NULL)
                      AND CAST(split_part(detail_url, '/', 4) AS INTEGER) < 10
            """
            count_parts.append(count_part)
        
        # 组合计数SQL
        count_sql = f"""
            SELECT SUM(cnt) as total_count FROM (
                {' UNION ALL '.join(count_parts)}
            ) t;
        """
        
        cursor.execute(count_sql)
        result = cursor.fetchone()
        total_count = result[0] if result and result[0] else 0
        
    except Exception as e:
        print(f'获取跨月查询总数失败: {e}')
        print(traceback.format_exc())
        conn.rollback()
        total_count = 0
    finally:
        cursor.close()
        _cleanup_implicit_read_transaction(conn, started_idle)
    
    return total_count
