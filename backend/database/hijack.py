import json
import psycopg2
from psycopg2 import extras
import traceback
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.logger import database_logger
from database.utils import if_table_exist


def create_hijack_table(conn, hijack_table):
    """
        If the table does not exist, create the table
        :param conn: database connection
        :param hijack_table: hijack table name
        :return:
        """
    cursor = conn.cursor()
    sql = """
                CREATE TABLE if not exists {}(
                source              varchar(8),
                prefix		        varchar(60),
                hijack_eventid      int,
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
                end_as              text,
                is_hijack           boolean,
                filter_reason       text,
                pre_vp_paths            jsonb,
                eve_vp_paths            jsonb,
                next_vp_paths            jsonb,
                hijack_level        varchar(8),
                hijack_level_info   text,
                event_info          text,            
                primary key(source, prefix, hijack_eventid)
                );
                """.format(hijack_table)
    cursor.execute(sql)
    conn.commit()

    # 创建优化索引
    try:
        # 复合索引优化主键查询和时间范围查询
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{hijack_table}_source_prefix_time 
            ON {hijack_table} (source, prefix, s_time DESC, e_time DESC)
        """)
        
        # 按劫持者AS查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{hijack_table}_hijacker_as_time 
            ON {hijack_table} (hijacker_as, s_time DESC) WHERE hijacker_as IS NOT NULL
        """)
        
        # 按被劫持AS查询优化  
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{hijack_table}_hijacked_as_time 
            ON {hijack_table} (hijacked_as, s_time DESC) WHERE hijacked_as IS NOT NULL
        """)
        
        # 按劫持等级查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{hijack_table}_level_time 
            ON {hijack_table} (hijack_level, s_time DESC) WHERE hijack_level IS NOT NULL
        """)
        
        # 按国家查询优化
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{hijack_table}_hijacker_country_time 
            ON {hijack_table} (hijacker_country, s_time DESC) WHERE hijacker_country IS NOT NULL
        """)
        
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{hijack_table}_hijacked_country_time 
            ON {hijack_table} (hijacked_country, s_time DESC) WHERE hijacked_country IS NOT NULL
        """)
        
        
        conn.commit()
        database_logger.info(f'Successfully created optimized indexes for table {hijack_table}')
        
    except Exception as e:
        database_logger.warning(f'Failed to create some indexes for table {hijack_table}: {e}')
        conn.rollback()
    finally:
        cursor.close()

    #####  创建索引完成



def get_hijack_id(conn, hijack_table):
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    sql_hijack = """
        select source, prefix, max(hijack_eventid) from {} group by source, prefix;
    """.format(hijack_table)
    try:
        cursor.execute(sql_hijack)
        hijack_rows = cursor.fetchall()
    except Exception as e:
        database_logger.error(f'get hijack id from table {hijack_table} failed: {e}')
        hijack_rows = []
    finally:
        cursor.close()
    return hijack_rows


def hijack_start(moas_event_dict, source, prefix, moas_id, hijack_id, conn, table):
    """
    Write prefix hijack start information to database
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
           INSERT INTO {}
           (source, prefix, hijack_eventid, hijacked_as, hijacked_as_name, hijacked_as_org, hijacked_as_country,
            hijacked_as_descr, hijacked_as_admin, hijacker_as, hijacker_as_name, hijacker_as_org, 
            hijacker_as_country, hijacker_as_descr, hijacker_as_admin, s_time, e_time, duration,
            end_as, is_hijack, filter_reason, pre_vp_paths, eve_vp_paths,
            hijack_level, hijack_level_info, event_info)
           VALUES
           (%(source)s, %(prefix)s, %(hijack_eventid)s, %(hijacked_as)s, %(hijacked_as_name)s, 
            %(hijacked_as_org)s, %(hijacked_as_country)s, %(hijacked_as_descr)s, %(hijacked_as_admin)s, 
            %(hijacker_as)s, %(hijacker_as_name)s, %(hijacker_as_org)s, %(hijacker_as_country)s, 
            %(hijacker_as_descr)s, %(hijacker_as_admin)s, %(s_time)s, %(e_time)s, %(duration)s,
            %(end_as)s, %(is_hijack)s, %(filter_reason)s, %(pre_vp_paths)s, %(eve_vp_paths)s, 
            %(hijack_level)s, %(hijack_level_info)s, %(event_info)s);
       """.format(table)
    params = {
        'source': source, 
        'prefix': prefix,
        'hijack_eventid': hijack_id,
        'hijacked_as': moas_event_dict[prefix][moas_id]['hijacked_as'],
        'hijacked_as_name': moas_event_dict[prefix][moas_id]['hijacked_as_name'],
        'hijacked_as_org': moas_event_dict[prefix][moas_id]['hijacked_as_org'],
        'hijacked_as_country': moas_event_dict[prefix][moas_id]['hijacked_as_country'],
        'hijacked_as_descr': moas_event_dict[prefix][moas_id]['hijacked_as_descr'],
        'hijacked_as_admin': moas_event_dict[prefix][moas_id]['hijacked_as_admin'],
        'hijacker_as': moas_event_dict[prefix][moas_id]['hijacker_as'],
        'hijacker_as_name': moas_event_dict[prefix][moas_id]['hijacker_as_name'],
        'hijacker_as_org': moas_event_dict[prefix][moas_id]['hijacker_as_org'],
        'hijacker_as_country': moas_event_dict[prefix][moas_id]['hijacker_as_country'],
        'hijacker_as_descr': moas_event_dict[prefix][moas_id]['hijacker_as_descr'],
        'hijacker_as_admin': moas_event_dict[prefix][moas_id]['hijacker_as_admin'],
        's_time': moas_event_dict[prefix][moas_id]['s_time'],
        'e_time': moas_event_dict[prefix][moas_id]['e_time'],
        'duration': moas_event_dict[prefix][moas_id]['duration'],
        'end_as': moas_event_dict[prefix][moas_id]['end_as'],
        'is_hijack': moas_event_dict[prefix][moas_id]['is_hijack'],
        'filter_reason': moas_event_dict[prefix][moas_id]['filter_reason'],
        'pre_vp_paths': json.dumps(moas_event_dict[prefix][moas_id]['pre_vp_paths']),
        'eve_vp_paths': json.dumps(moas_event_dict[prefix][moas_id]['eve_vp_paths']),
        'hijack_level': moas_event_dict[prefix][moas_id]['level'],
        'hijack_level_info': moas_event_dict[prefix][moas_id]['level_info'],
        'event_info': moas_event_dict[prefix][moas_id]['event_info']
    }
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        database_logger.error(f' Write prefix hijack{hijack_id} start information to database {table} failed: {e}')
        conn.rollback()
    finally:
        cursor.close()


def hijack_end(moas_event_dict, source, prefix, moas_id, hijack_id, conn, table):
    """
    Write prefix hijack end information to database
    :param moas_event_dict: dictionary for recording prefix moas information
    :param source: source of hijack events
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
            e_time=%s, duration=%s, end_as=%s, next_vp_paths=%s
            where source=%s and prefix=%s and hijack_eventid=%s;
        """.format(table)
    params = (
        moas_event_dict[prefix][moas_id]['e_time'],
        moas_event_dict[prefix][moas_id]['duration'],
        moas_event_dict[prefix][moas_id]['end_as'],
        json.dumps(moas_event_dict[prefix][moas_id]['next_vp_paths']),
        source, 
        prefix,
        hijack_id
    )
    try:
        cursor.execute(sql, params)
        conn.commit()
    except Exception as e:
        database_logger.error(f' Write prefix hijack{hijack_id} end information to database {table} failed: {e}')
        conn.rollback()
    finally:
        cursor.close()

def get_hijack_de(conn, table, prefix, hijack_id, source) -> list:
    """
    返回劫持事件的详细信息
    :param conn: 数据库连接
    :param table: 数据表名
    :param prefix: 被劫持的前缀
    :param hijack_id: 前缀劫持id
    :return: 劫持事件的详细信息
    """
    row = list()
    if if_table_exist(conn, table):
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = """
            select prefix, hijack_eventid, hijacked_as, hijacked_as_name, hijacked_as_org, hijacked_as_country,
                   hijacker_as, hijacker_as_name, hijacker_as_org, hijacker_as_country, s_time, e_time, duration,
                   hijack_level, hijack_level_info, pre_vp_paths, eve_vp_paths, event_info, 
                   hijacker_as_descr, hijacker_as_admin, hijacked_as_descr, hijacked_as_admin, next_vp_paths
            from {}
            where prefix = '{}' and hijack_eventid = '{}' and source = '{}';
        """.format(table, prefix, hijack_id, source)
        try:
            cursor.execute(sql)
            row = cursor.fetchall()
        except Exception as e:
            database_logger.error(f'get hijack detail from table {table} failed: {e}')
            database_logger.error(traceback.format_exc())
            conn.rollback()
            row = []
        finally:
            cursor.close()
    return row

