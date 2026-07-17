import datetime
import traceback

import psycopg2

from config.database import conn_11


ALLOWED_ROLES = {'admin', 'operator', 'guest'}


def _close_cursor(cursor):
    if cursor is None:
        return
    try:
        cursor.close()
    except Exception:
        pass


def authenticate_user(userid, password, conn=conn_11):
    if userid in [None, ''] or password in [None, '']:
        return {'status': False, 'msg': '您输入的账号或密码为空！'}

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute(
            "SELECT userid, username, password, role FROM users WHERE userid = %s",
            (userid,),
        )
        user = cursor.fetchone()
        if not user:
            return {'status': False, 'msg': '账号不存在！'}
        if user['password'] != password:
            return {'status': False, 'msg': '密码错误！'}
        return {'status': True, 'userid': user['userid'], 'role': user['role']}
    except Exception as error:
        traceback.print_exc()
        conn.rollback()
        return {'status': False, 'msg': '登录失败！错误为：{}'.format(error)}
    finally:
        _close_cursor(cursor)


def create_user(creatorid, userid, username, password, role, conn=conn_11):
    if userid in [None, ''] or username in [None, ''] or password in [None, '']:
        return {'status': False, 'msg': '您输入的账号或用户名或密码为空！'}
    if role not in ALLOWED_ROLES:
        return {'status': False, 'msg': '不存在该角色！'}

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM users WHERE userid = %s", (userid,))
        if cursor.fetchone():
            return {'status': False, 'msg': '账号已存在！'}

        cursor.execute("SELECT username FROM users WHERE userid = %s", (creatorid,))
        creatorname = cursor.fetchone()['username']
        create_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO users (userid, username, password, role, creatorid, creatorname, create_time) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (userid, username, password, role, creatorid, creatorname, create_time),
        )
        conn.commit()
        return {'status': True, 'msg': '账号{}注册成功！'.format(userid)}
    except Exception as error:
        traceback.print_exc()
        conn.rollback()
        return {'status': False, 'msg': '注册失败！错误为：{}'.format(error)}
    finally:
        _close_cursor(cursor)


def update_user_by_admin(user_id, username=None, password=None, role=None, conn=conn_11):
    if not user_id:
        return {'status': False, 'msg': 'URL中的用户ID不能为空！'}

    normalized_username = 'username' if username in [None, ''] else "'{}'".format(username)
    normalized_password = 'password' if password in [None, ''] else "'{}'".format(password)

    if role in [None, '']:
        normalized_role = 'role'
    elif role not in ALLOWED_ROLES:
        return {'status': False, 'msg': '不存在该角色！'}
    else:
        normalized_role = "'{}'".format(role)

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM users WHERE userid = %s", (user_id,))
        if not cursor.fetchone():
            return {'status': False, 'msg': '账号不存在！'}

        sql = (
            "UPDATE users SET username = {username}, password = {password}, role = {role} "
            "WHERE userid = '{user_id}'"
        ).format(
            username=normalized_username,
            password=normalized_password,
            role=normalized_role,
            user_id=user_id,
        )
        cursor.execute(sql)
        conn.commit()

        cursor.execute("SELECT role FROM users WHERE userid = %s", (user_id,))
        user_role = cursor.fetchone()['role']
        if user_role == 'admin' and normalized_username != 'username':
            sql = "UPDATE users SET creatorname = {username} WHERE creatorid = '{user_id}'".format(
                username=normalized_username,
                user_id=user_id,
            )
            cursor.execute(sql)
            conn.commit()

        return {'status': True, 'msg': '更改成功！'}
    except Exception as error:
        traceback.print_exc()
        conn.rollback()
        return {'status': False, 'msg': '更改失败！错误为：{}'.format(error)}
    finally:
        _close_cursor(cursor)


def delete_user_by_admin(user_id, conn=conn_11):
    if not user_id:
        return {'status': False, 'msg': 'URL中的用户ID不能为空！'}

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM users WHERE userid = %s", (user_id,))
        if not cursor.fetchone():
            return {'status': False, 'msg': '用户不存在！'}

        cursor.execute("DELETE FROM users WHERE userid = %s", (user_id,))
        conn.commit()
        return {'status': True, 'msg': '用户删除成功！'}
    except Exception as error:
        traceback.print_exc()
        conn.rollback()
        return {'status': False, 'msg': '用户删除失败！错误为：{}'.format(error)}
    finally:
        _close_cursor(cursor)


def get_user_profile(userid, conn=conn_11):
    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT username, password FROM users WHERE userid = %s", (userid,))
        user_info = cursor.fetchone()
        if not user_info:
            return {'status': False, 'msg': '用户 {} 在数据库中未找到'.format(userid), 'status_code': 404}

        return {
            'status': True,
            'userid': userid,
            'username': user_info['username'],
            'password': user_info['password'],
        }
    except Exception as error:
        traceback.print_exc()
        conn.rollback()
        return {
            'status': False,
            'msg': '获取用户信息时发生内部错误: {}'.format(str(error)),
            'status_code': 500,
        }
    finally:
        _close_cursor(cursor)


def update_own_profile(userid, role, username=None, password=None, conn=conn_11):
    if not username and not password:
        return {'status': False, 'msg': '没有需要更新的内容！'}

    updates = []
    if username:
        updates.append("username = '{}'".format(username))
    if password:
        updates.append("password = '{}'".format(password))

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT * FROM users WHERE userid = %s", (userid,))
        if not cursor.fetchone():
            return {'status': False, 'msg': '用户不存在！'}

        sql = "UPDATE users SET {} WHERE userid = '{}'".format(', '.join(updates), userid)
        cursor.execute(sql)

        if role == 'admin' and username:
            sql = "UPDATE users SET creatorname = '{}' WHERE creatorid = '{}'".format(username, userid)
            cursor.execute(sql)

        conn.commit()
        return {'status': True, 'msg': '更改成功！'}
    except Exception as error:
        traceback.print_exc()
        conn.rollback()
        return {'status': False, 'msg': '更改失败！错误为：{}'.format(error)}
    finally:
        _close_cursor(cursor)
