import psycopg2
from psycopg2 import extras

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.logger import database_logger
from database.utils import if_table_exist
import traceback

def create_leak_event_table(conn, leak_event_table):
    """
    If the table does not exist, create a table to store the leak event (level is high or middle)
    :param conn: database connection
    :param leak_event_table: leak phenomenon table name
    :return:
    """
    cursor = conn.cursor()
    sql = """
            CREATE TABLE if not exists {}(
            source                    varchar(8),  
            prefix		              varchar(60),
            leak_event_id             int,
            is_leak             int, 
            filter_reason       text,
            s_time              timestamp(0) without time zone      not NULL,
            leak_by             text,
            leak_by_name        text,
            leak_by_org         text,
            leak_by_country     text,
            leak_by_descr       text,
            leak_by_admin       text,
            leak_to             text,
            leak_to_name        text,
            leak_to_org         text,
            leak_to_country     text, 
            leak_to_descr       text, 
            leak_to_admin       text,   
            leak_vp             text,
            as_path             text,
            prefix_ori_as       text,
            ori_as_country      text,
            ori_as_org          text,
            ori_as_name         text, 
            ori_as_descr        text, 
            ori_as_admin        text,
            leak_level          varchar(8),
            leak_level_info     text,
            event_info          text, 
            primary key(source, prefix, leak_event_id)
            );
        """.format(leak_event_table)
    cursor.execute(sql)
    conn.commit()
    
    # 创建优化索引
    try:
        # 复合索引优化主键查询和时间查询
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{leak_event_table}_source_prefix_time 
            ON {leak_event_table} (source, prefix, s_time DESC)
        """)
        
        # 按泄露者AS查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{leak_event_table}_leak_by_time 
            ON {leak_event_table} (leak_by, s_time DESC) WHERE leak_by IS NOT NULL
        """)
        
        # 按被泄露AS查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{leak_event_table}_leak_to_time 
            ON {leak_event_table} (leak_to, s_time DESC) WHERE leak_to IS NOT NULL
        """)
        
        
        # 按泄露等级查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{leak_event_table}_level_time 
            ON {leak_event_table} (leak_level, s_time DESC) WHERE leak_level IS NOT NULL
        """)
        
        
        # 按国家查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{leak_event_table}_leak_by_country_time 
            ON {leak_event_table} (leak_by_country, s_time DESC) WHERE leak_by_country IS NOT NULL
        """)
        
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{leak_event_table}_leak_to_country_time 
            ON {leak_event_table} (leak_to_country, s_time DESC) WHERE leak_to_country IS NOT NULL
        """)
        
        conn.commit()
        database_logger.info(f'Successfully created optimized indexes for table {leak_event_table}')
        
    except Exception as e:
        database_logger.warning(f'Failed to create some indexes for table {leak_event_table}: {e}')
        conn.rollback()


def get_leak_id(conn, leak_event_table):
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    sql_leak_event = """
        select source, prefix, max(leak_event_id) from {} group by source, prefix;
    """.format(leak_event_table)
    try:
        cursor.execute(sql_leak_event)
        conn.commit()
        leak_event_rows = cursor.fetchall()
    except Exception as e:
        database_logger.error(f'get leak event id from table {leak_event_table} failed: {e}')
        conn.rollback()
        leak_event_rows = []
    finally:
        cursor.close()
    return leak_event_rows


def leak_event_record(conn, phenomenon_dict, source, prefix, phenomenon_id, event_id, leak_event_table, is_leak, filter_reason):
    """
    Write leak event information to database
    :param conn: database connection
    :param phenomenon_dict: dictionary for recording leak phenomenon information
    :param prefix: leak prefix
    :param phenomenon_id: phenomenon id
    :param event_id: event_id
    :param leak_event_table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
            INSERT INTO {}
            (source, prefix, leak_event_id, is_leak, filter_reason,
            s_time, leak_by, leak_by_name, leak_by_org, leak_by_country, 
            leak_by_descr, leak_by_admin, leak_to, leak_to_name, leak_to_org, 
            leak_to_country, leak_to_descr, leak_to_admin, leak_vp, as_path, 
            prefix_ori_as, ori_as_country, ori_as_org, ori_as_name, 
            ori_as_descr, ori_as_admin, leak_level, leak_level_info, event_info)
            VALUES 
            (%(source)s, %(prefix)s, %(leak_event_id)s, %(is_leak)s, %(filter_reason)s, %(s_time)s, %(leak_by)s, 
            %(leak_by_name)s, %(leak_by_org)s, %(leak_by_country)s, %(leak_by_descr)s, %(leak_by_admin)s, 
            %(leak_to)s, %(leak_to_name)s, %(leak_to_org)s, %(leak_to_country)s, 
            %(leak_to_descr)s, %(leak_to_admin)s, %(leak_vp)s, %(as_path)s, 
            %(prefix_ori_as)s, %(ori_as_country)s, %(ori_as_org)s, %(ori_as_name)s, 
            %(ori_as_descr)s, %(ori_as_admin)s, %(leak_level)s, %(leak_level_info)s, %(event_info)s)
        """.format(leak_event_table)
    params = {
        'source': source,
        'prefix': prefix,
        'leak_event_id': event_id,
        'is_leak': is_leak,
        'filter_reason': filter_reason,
        's_time': phenomenon_dict[prefix][phenomenon_id]['s_time'],
        'leak_by': phenomenon_dict[prefix][phenomenon_id]['leak_by'],
        'leak_by_name': phenomenon_dict[prefix][phenomenon_id]['leak_by_name'],
        'leak_by_org': phenomenon_dict[prefix][phenomenon_id]['leak_by_org'],
        'leak_by_country': phenomenon_dict[prefix][phenomenon_id]['leak_by_country'],
        'leak_by_descr': phenomenon_dict[prefix][phenomenon_id]['leak_by_descr'],
        'leak_by_admin': phenomenon_dict[prefix][phenomenon_id]['leak_by_admin'],
        'leak_to': phenomenon_dict[prefix][phenomenon_id]['leak_to'],
        'leak_to_name': phenomenon_dict[prefix][phenomenon_id]['leak_to_name'],
        'leak_to_org': phenomenon_dict[prefix][phenomenon_id]['leak_to_org'],
        'leak_to_country': phenomenon_dict[prefix][phenomenon_id]['leak_to_country'],
        'leak_to_descr': phenomenon_dict[prefix][phenomenon_id]['leak_to_descr'],
        'leak_to_admin': phenomenon_dict[prefix][phenomenon_id]['leak_to_admin'],
        'leak_vp': phenomenon_dict[prefix][phenomenon_id]['leak_vp'],
        'as_path': str(phenomenon_dict[prefix][phenomenon_id]['as_path']),
        'prefix_ori_as': phenomenon_dict[prefix][phenomenon_id]['prefix_ori_as'],
        'ori_as_country': phenomenon_dict[prefix][phenomenon_id]['ori_as_country'],
        'ori_as_org': phenomenon_dict[prefix][phenomenon_id]['ori_as_org'],
        'ori_as_name': phenomenon_dict[prefix][phenomenon_id]['ori_as_name'],
        'ori_as_descr': phenomenon_dict[prefix][phenomenon_id]['ori_as_descr'],
        'ori_as_admin': phenomenon_dict[prefix][phenomenon_id]['ori_as_admin'],
        'leak_level': phenomenon_dict[prefix][phenomenon_id]['leak_level'],
        'leak_level_info': phenomenon_dict[prefix][phenomenon_id]['leak_level_info'], 
        'event_info': phenomenon_dict[prefix][phenomenon_id]['event_info']
    }
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        database_logger.error(f' Write leak{event_id} start information to database {leak_event_table} failed: {e}')
        conn.rollback()
    finally:
        cursor.close()


def get_leak_de(conn, table, prefix, leak_id, source) -> list:
    """
    返回劫持事件的详细信息
    :param conn: 数据库连接
    :param table: 数据表名
    :param prefix: 泄露的前缀
    :param leak_id: 前缀泄露id
    :return: 泄露事件的详细信息
    """
    row = list()
    if if_table_exist(conn, table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
            select prefix, leak_event_id, leak_by, leak_by_name, leak_by_org, leak_by_country,
                   leak_to, leak_to_name, leak_to_org, leak_to_country, s_time, leak_level, 
                   leak_level_info, as_path, prefix_ori_as, ori_as_country, ori_as_org, ori_as_name, 
                   event_info, leak_to_descr, leak_to_admin, leak_by_descr, leak_by_admin, 
                   ori_as_descr, ori_as_admin
            from {}
            where prefix = '{}' and leak_event_id = '{}' and source = '{}';
        """.format(table, prefix, leak_id, source)
        try:
            cursor.execute(sql)
            row = cursor.fetchall()
        except Exception as e:
            database_logger.error(f'get leak detail from table {table} failed: {e}')
            database_logger.error(traceback.format_exc())
            conn.rollback()
            row = []
        finally:
            cursor.close()
    return row