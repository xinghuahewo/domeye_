import json
import psycopg2
from psycopg2 import extras
import re
import traceback
import math
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.logger import database_logger
from database.utils import if_table_exist

"""
数据库中与用户的表的操作
建表(create)、用户注册(insert)、修改密码(update)、查询密码 用来做验证(select)
"""

#### TODO: 是否需要捕获异常？？？

def create_login_table(conn, login_table):
    """
    If the table does not exist, create the table
    :param conn: database connection
    :param login_table: login_table name
    :return:
    """
    cursor = conn.cursor()
    sql = """
            CREATE TABLE if not exists {}(
            userid         text,
            username       text,
            password       text,    
            role           text, 
            creatorid      text,
            creatorname    text,
            create_time     timestamp(0) without time zone  not NULL,     
            primary key(userid)
            );
            """.format(login_table)
    cursor.execute(sql)
    conn.commit()
    
    # 创建优化索引
    try:
        # 按用户名查询优化（登录验证）
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{login_table}_username 
            ON {login_table} (username) WHERE username IS NOT NULL
        """)
        
        conn.commit()
        database_logger.info(f'Successfully created optimized indexes for table {login_table}')
        
    except Exception as e:
        database_logger.warning(f'Failed to create some indexes for table {login_table}: {e}')
        conn.rollback()
    finally:
        cursor.close()


def user_register(userid, username, password, conn, table):
    """
    Write user_register information to database
    :param username:  注册用户名
    :param password: 密码
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
        INSERT INTO {}
        (userid, username, password)
        VALUES
        ((%(userid)s, %(username)s, %(password)s);
    """.format(table)
    params = {
        'userid': userid,
        'username': username,
        'password': password
    }
    cursor.execute(sql, params)
    conn.commit()
    cursor.close()


def change_password(username, password, conn, table):
    """
    修改密码
    :param username: dictionary for recording prefix moas information
    :param password: moas prefix
    :param conn: database connection
    :param table: name of the table to write the information to
    :return:
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
        UPDATE {} SET
        password=%s
        where username=%s;
    """.format(table)
    params = (
        password,
        username
    )
    cursor.execute(sql, params)
    conn.commit()
    cursor.close()


def database_login_check(username, conn, table):
    """
    Write prefix hijack start information to database
    :return: result
    """
    cursor = conn.cursor()
    # sql statement
    sql = """
           SELECT password 
           FROM {}
           WHERE username='{}';
       """.format(table, username)
    cursor.execute(sql)
    # 获取查询结果
    results = cursor.fetchall()
    # 提交当前事务，数据库永久保存    
    conn.commit()
    cursor.close()
    return results

def get_user_list_db(conn, userid, username, creatorid, creatorname, 
                  create_time_start, create_time_end, role, sort_mode, page_size, offset):
    """
    获取用户列表
    :param conn: 数据库连接
    :param userid: 用户账号（用户唯一id）
    :param username: 用户名
    :param creatorid: 创建人账号
    :param creatorname: 创建人用户名
    :param create_time_start: 创建时间开始
    :param create_time_end: 创建时间结束
    :param role: 用户角色
    :param sort_mode: 排序方式
    :param page_size: 分页长度
    :param offset: 偏移量
    :return: 用户列表
    """
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    users_table = 'users'
    user_rows = list()
    if if_table_exist(conn, users_table):
        sql = """
            select userid, username, role, password, creatorid, creatorname, create_time
            from {}
            where userid like {} and username like {} and creatorid like {} and creatorname like {} 
            and create_time >= {} and create_time <= {} and role = {}
            order by {}
            limit {} offset {};
        """.format(users_table, userid, username, creatorid, creatorname, create_time_start, create_time_end, 
                   role, sort_mode, page_size, offset)
        try:
            cursor.execute(sql)
            user_rows = cursor.fetchall()
        except Exception as e:
            database_logger.error(f'get user list from table {users_table} failed: {e}')
            database_logger.error(traceback.format_exc())
            conn.rollback()
            user_rows = []
        finally:
            cursor.close()
    return user_rows



def get_user_total_page_db(conn, userid, username, creatorid, creatorname, create_time_start, 
                       create_time_end, role, page_size):
    """
    获取用户列表总页数
    :param conn: 数据库连接
    :param userid: 用户账号
    :param username: 用户名
    :param creatorid: 创建人账号
    :param creatorname: 创建人用户名
    :param create_time_start: 创建时间开始
    :param create_time_end: 创建时间结束
    :param role: 用户角色
    :param page_size: 分页长度
    :return: 用户列表总页数、总条目数
    """ 
    
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    users_table = 'users'

    if if_table_exist(conn, users_table):
        sql = """
            select count(*)
            from {}
            where userid like {} and username like {} and creatorid like {} and creatorname like {} 
            and create_time >= {} and create_time <= {} and role = {};
            """.format(users_table, userid, username, creatorid, creatorname, create_time_start, 
                       create_time_end, role)
        try:
            cursor.execute(sql)
            record_count = cursor.fetchone()[0]
            total_page = math.ceil(record_count / page_size)
        except Exception as e:
            database_logger.error(f'get user total page from table {users_table} failed: {e}')
            database_logger.error(traceback.format_exc())
            conn.rollback()
            total_page, record_count = 0, 0
        finally:
            cursor.close()
    else:
        total_page, record_count = 0, 0

    return total_page, record_count



