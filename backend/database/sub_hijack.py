import json
import psycopg2
from psycopg2 import extras
import traceback
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.logger import database_logger
from database.utils import if_table_exist
def create_sub_hijack_table(conn, sub_hijack_table):
    """
    If the table does not exist, create the table
    :param conn: database connection
    :param moas_table: subhijack table name
    :return:
    """
    cursor = conn.cursor()
    sql = """ 
            CREATE TABLE if not exists {}(
            source              varchar(8),
            prefix		        varchar(60),
            sub_hijack_eventid        int,
            is_sub_hijack       boolean,
            filter_reason       text,
            hijacked_prefix     varchar(60),
            hijacked_as         text,
            hijacked_as_name    text,
            hijacked_as_org     text,
            hijacked_as_country text,
            hijacked_as_descr   text,
            hijacked_as_admin   text,
            hijacker_as         text,
            hijacker_as_name    text,
            hijacker_as_org     text,
            hijacker_as_country text,
            hijacker_as_descr   text,
            hijacker_as_admin   text,
            s_time              timestamp(0) without time zone      not NULL,
            e_time              timestamp(0) without time zone,
            duration            interval     DAY TO SECOND (0),
            sub_hijack_level        varchar(8),
            level_info              text,
            event_info              text, 
            primary key(source, prefix, sub_hijack_eventid)
            );
            """.format(sub_hijack_table)
    cursor.execute(sql)
    conn.commit()
    
    # 创建优化索引
    try:
        # 复合索引优化主键查询和时间范围查询
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{sub_hijack_table}_source_prefix_time 
            ON {sub_hijack_table} (source, prefix, s_time DESC, e_time DESC)
        """)
        
        # 按劫持者AS查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{sub_hijack_table}_hijacker_as_time 
            ON {sub_hijack_table} (hijacker_as, s_time DESC) WHERE hijacker_as IS NOT NULL
        """)
        
        # 按被劫持AS查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{sub_hijack_table}_hijacked_as_time 
            ON {sub_hijack_table} (hijacked_as, s_time DESC) WHERE hijacked_as IS NOT NULL
        """)
        
        # 按子劫持等级查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{sub_hijack_table}_level_time 
            ON {sub_hijack_table} (sub_hijack_level, s_time DESC) WHERE sub_hijack_level IS NOT NULL
        """)
        
        # 按国家查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{sub_hijack_table}_hijacker_country_time 
            ON {sub_hijack_table} (hijacker_country, s_time DESC) WHERE hijacker_country IS NOT NULL
        """)
        
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{sub_hijack_table}_hijacked_country_time 
            ON {sub_hijack_table} (hijacked_country, s_time DESC) WHERE hijacked_country IS NOT NULL
        """)
        
        conn.commit()
        database_logger.info(f'Successfully created optimized indexes for table {sub_hijack_table}')
        
    except Exception as e:
        database_logger.warning(f'Failed to create some indexes for table {sub_hijack_table}: {e}')
        conn.rollback()
    finally:
        cursor.close()


def get_sub_hijack_id(conn, sub_hijack_table):
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    sql = """
        select source, prefix, max(sub_hijack_eventid) from {} group by source, prefix;
    """.format(sub_hijack_table)
    try:
        cursor.execute(sql)
        sub_hijack_rows = cursor.fetchall()
    except Exception as e:
        database_logger.error(f'get sub hijack id from table {sub_hijack_table} failed: {e}')
        sub_hijack_rows = []
    finally:
        cursor.close()
    return sub_hijack_rows


def sub_hijack_start(sub_hijack_dict, source, prefix, sub_hijack_id, conn, table):
    """
    Write subhijack start information to database
    :param sub_hijack_dict:  dictionary for recording subhijack information
    :param prefix: subhijack prefix
    :param sub_hijack_id: prefix subhijack id
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
        INSERT INTO {}
        (source, prefix, sub_hijack_eventid, is_sub_hijack, filter_reason, 
        hijacked_prefix, hijacked_as, hijacked_as_name, hijacked_as_org, 
        hijacked_as_country, hijacked_as_descr, hijacked_as_admin, 
        hijacker_as, hijacker_as_name, hijacker_as_org, 
        hijacker_as_country, hijacker_as_descr, hijacker_as_admin, 
        s_time, e_time, duration, sub_hijack_level, level_info, event_info)
        VALUES
        (%(source)s, %(prefix)s, %(sub_hijack_eventid)s, %(is_sub_hijack)s, %(filter_reason)s, 
        %(hijacked_prefix)s, %(hijacked_as)s, %(hijacked_as_name)s, %(hijacked_as_org)s, 
        %(hijacked_as_country)s, %(hijacked_as_descr)s, %(hijacked_as_admin)s, 
        %(hijacker_as)s, %(hijacker_as_name)s, %(hijacker_as_org)s,
        %(hijacker_as_country)s, %(hijacker_as_descr)s, %(hijacker_as_admin)s, 
        %(s_time)s, %(e_time)s, %(duration)s, %(sub_hijack_level)s, %(level_info)s, %(event_info)s)
    """.format(table)
    params = {
        'source': source, 
        'prefix': prefix,
        'sub_hijack_eventid': sub_hijack_id,
        'is_sub_hijack': sub_hijack_dict[prefix][sub_hijack_id]['is_sub_hijack'], 
        'filter_reason': sub_hijack_dict[prefix][sub_hijack_id]['filter_reason'], 
        'hijacked_prefix': sub_hijack_dict[prefix][sub_hijack_id]['hijacked_prefix'],
        'hijacked_as': sub_hijack_dict[prefix][sub_hijack_id]['hijacked_as'],
        'hijacked_as_name': sub_hijack_dict[prefix][sub_hijack_id]['hijacked_as_name'],
        'hijacked_as_org': sub_hijack_dict[prefix][sub_hijack_id]['hijacked_as_org'],
        'hijacked_as_country': sub_hijack_dict[prefix][sub_hijack_id]['hijacked_as_country'],
        'hijacked_as_descr': sub_hijack_dict[prefix][sub_hijack_id]['hijacked_as_descr'],
        'hijacked_as_admin': sub_hijack_dict[prefix][sub_hijack_id]['hijacked_as_admin'],
        'hijacker_as': sub_hijack_dict[prefix][sub_hijack_id]['hijacker_as'],
        'hijacker_as_name': sub_hijack_dict[prefix][sub_hijack_id]['hijacker_as_name'],
        'hijacker_as_org': sub_hijack_dict[prefix][sub_hijack_id]['hijacker_as_org'],
        'hijacker_as_country': sub_hijack_dict[prefix][sub_hijack_id]['hijacker_as_country'],
        'hijacker_as_descr': sub_hijack_dict[prefix][sub_hijack_id]['hijacker_as_descr'],
        'hijacker_as_admin': sub_hijack_dict[prefix][sub_hijack_id]['hijacker_as_admin'],
        's_time': sub_hijack_dict[prefix][sub_hijack_id]['s_time'],
        'e_time': sub_hijack_dict[prefix][sub_hijack_id]['e_time'],
        'duration': sub_hijack_dict[prefix][sub_hijack_id]['duration'],
        'sub_hijack_level': sub_hijack_dict[prefix][sub_hijack_id]['sub_hijack_level'],
        'level_info': sub_hijack_dict[prefix][sub_hijack_id]['level_info'], 
        'event_info': sub_hijack_dict[prefix][sub_hijack_id]['event_info'] 
    }
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        database_logger.error(f'Write subhijack{sub_hijack_id} start information to database{table} failed: {e}')
        conn.rollback()
    finally:
        cursor.close()


def sub_hijack_end(sub_hijack_dict, source, prefix, sub_hijack_id, conn, table):
    """
    Write subhijack end information to database
    :param sub_hijack_dict:  dictionary for recording subhijack information
    :param prefix: subhijack prefix
    :param sub_hijack_id: prefix subhijack id
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
        UPDATE {} SET
        e_time=%s, duration=%s
        where source=%s and prefix=%s and sub_hijack_eventid=%s;
    """.format(table)
    params = (
        sub_hijack_dict[prefix][sub_hijack_id]['e_time'],
        sub_hijack_dict[prefix][sub_hijack_id]['duration'],
        source,
        prefix,
        sub_hijack_id
    )
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        database_logger.error(f'Write subhijack{sub_hijack_id} end information to database{table} failed: {e}')
        conn.rollback()
    finally:
        cursor.close()

def get_sub_hijack_de(conn, table, prefix, sub_hijack_id, source) -> list:
    """
    返回子前缀劫持事件的详细信息
    :param conn: 数据库连接
    :param table: 数据表名
    :param prefix: 发动劫持的前缀
    :param sub_hijack_id: 子前缀劫持id
    :return: 子前缀劫持的详细信息
    """
    row = list()
    if if_table_exist(conn, table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
            select prefix, sub_hijack_eventid, hijacked_prefix, hijacked_as, hijacked_as_name, hijacked_as_org, hijacked_as_country,
                   hijacker_as, hijacker_as_name, hijacker_as_org, hijacker_as_country, s_time, e_time, duration,
                   sub_hijack_level, level_info, event_info, hijacker_as_descr, hijacker_as_admin, hijacked_as_descr, hijacked_as_admin
            from {}
            where prefix = '{}' and sub_hijack_eventid = '{}' and source = '{}';
        """.format(table, prefix, sub_hijack_id, source)
        try:
            cursor.execute(sql)
            row = cursor.fetchall()
        except Exception as e:
            database_logger.error(f'get sub hijack detail from table {table} failed: {e}')
            database_logger.error(traceback.format_exc())
            conn.rollback()
            row = []
        finally:
            cursor.close()
    return row
