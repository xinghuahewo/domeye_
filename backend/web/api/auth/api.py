from functools import wraps

from flask import request, make_response, jsonify
from flask_restful import Resource
from authlib.jose import jwt, JoseError
import re
from utils.get_event import get_user_list, get_user_total_page, deal_user_list

from config.database import conn_11
from config.config import SECRET_KEY
from services import (
    authenticate_user,
    create_user,
    delete_user_by_admin,
    get_user_profile,
    update_own_profile,
    update_user_by_admin,
)

SECRET_KEY = SECRET_KEY
def generate_token(userid, role):
    """生成用于邮箱验证的JWT（json web token）"""
    header = {'alg': 'HS256'}
    key = SECRET_KEY
    data = {'userid': userid, 'role': role}
    return jwt.encode(header=header, payload=data, key=key).decode('utf-8')

def validate_token(token):
    """用于验证用户注册和用户修改密码或邮箱的token, 并完成相应的确认操作"""
    key = SECRET_KEY
    try:
        data = jwt.decode(token, key)
        print(data)
    except JoseError:
        return 0, 0
    return data['userid'], data['role']

# 用户权限管理和鉴权
def login_check(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            auth_header = request.headers.get('Authorization', '')
            token = auth_header.split()[-1]
            if not token:
                raise ValueError("Token not found")

            userid, role = validate_token(token=token)
            if not userid or not role:
                return make_response(jsonify({'status': False, 'msg': '无效的Token或登录已过期！'}), 401)
        except (IndexError, ValueError):
            return make_response(jsonify({'status': False, 'msg': '您尚未登录或Token格式不正确！'}), 401)
        return func(*args, **kwargs)
    return wrapper

def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            auth_header = request.headers.get('Authorization', '')
            token = auth_header.split()[-1]
            if not token:
                raise ValueError("Token not found")

            userid, role = validate_token(token=token)
            if not userid or not role:
                return make_response(jsonify({'status': False, 'msg': '无效的Token或登录已过期！'}), 401)
            if role != 'admin':
                return make_response(jsonify({'status': False, 'msg': '您不具备该操作权限！'}), 403)
        except (IndexError, ValueError):
            return make_response(jsonify({'status': False, 'msg': '您尚未登录或Token格式不正确！'}), 401)
        return func(*args, **kwargs)
    return wrapper

class UserLoginResource(Resource):
    def post(self):
        payload = request.get_json(silent=True) or {}
        result = authenticate_user(
            userid=payload.get('userid'),
            password=payload.get('password'),
            conn=conn_11,
        )
        if not result['status']:
            return result

        token = generate_token(result['userid'], result['role'])
        return {'status': True, 'msg': '登录成功！', 'token': token}

class UserRegisterResource(Resource):
    method_decorators = [admin_required]

    def post(self):
        token = request.headers.get('Authorization', '').split()[-1]
        creatorid, _ = validate_token(token=token)
        payload = request.get_json(silent=True) or {}
        return create_user(
            creatorid=creatorid,
            userid=payload.get('userid'),
            username=payload.get('username'),
            password=payload.get('password'),
            role=payload.get('role'),
            conn=conn_11,
        )

class UserInfoEditResource(Resource):
    @admin_required
    def put(self, user_id=None):
        payload = request.get_json(silent=True) or {}
        return update_user_by_admin(
            user_id=user_id or payload.get('userid'),
            username=payload.get('username'),
            password=payload.get('password'),
            role=payload.get('role'),
            conn=conn_11,
        )

    @admin_required
    def delete(self, user_id=None):
        payload = request.get_json(silent=True) or {}
        return delete_user_by_admin(user_id=user_id or payload.get('userid'), conn=conn_11)



class UserListResource(Resource):
    method_decorators = [admin_required]
    def get(self):
        # Original logic from user_index()
        page_size = request.args.get('page_size')
        if page_size in ['10', '50', '100', '200']:
            page_size = int(page_size)
        else:
            page_size = 10
        pattern = re.compile('^[0-9]*$')
        if (request.args.get('page_num') in [None, '']) or (request.args.get('page_num').startswith('0')):
            page_num = 1
        elif pattern.match(request.args.get('page_num')):
            page_num = int(request.args.get('page_num'))
        else:
            page_num = 1
        userid = request.args.get('userid')
        username = request.args.get('username')
        role = request.args.get('role')
        creatorid = request.args.get('creatorid')
        creatorname = request.args.get('creatorname')
        create_time = request.args.get('create_time')
        sort_mode = request.args.get('sort_mode')

        user_rows = get_user_list(conn=conn_11, page_num=page_num, page_size=page_size, 
                                  userid=userid, username=username, role=role, 
                                  creatorid=creatorid, creatorname=creatorname, 
                                  create_time=create_time, sort_mode=sort_mode)
        user_items = deal_user_list(user_rows=user_rows)
        total_page, record_count = get_user_total_page(conn=conn_11, page_size=page_size,
                                                        userid=userid, username=username, role=role, 
                                                        creatorid=creatorid, creatorname=creatorname, 
                                                        create_time=create_time)
        d = dict()
        d['total_page'] = total_page
        d['record_count'] = record_count
        d['data'] = user_items
        return d

class UserProfileResource(Resource):
    method_decorators = [login_check]

    def get(self):
        token = request.headers.get('Authorization', '').split()[-1]
        userid, role = validate_token(token=token)
        result = get_user_profile(userid=userid, conn=conn_11)
        if not result['status']:
            return {'status': False, 'msg': result['msg']}, result.get('status_code', 500)
        return {
            'status': True,
            'userid': result['userid'],
            'username': result['username'],
            'password': result['password'],
            'role': role,
        }

    def post(self):
        try:
            token = request.headers.get('Authorization', '').split()[-1]
            userid, role = validate_token(token=token)
            if not userid:
                return {'status': False, 'msg': 'token存在问题！'}
        except:
            return {'status': False, 'msg': '您尚未登录！'}
        
        payload = request.get_json(silent=True) or {}
        return update_own_profile(
            userid=userid,
            role=role,
            username=payload.get('username'),
            password=payload.get('password'),
            conn=conn_11,
        )

class UserLogoutResource(Resource):
    def post(self):
        return make_response("Logout successful. Please discard the token on the client-side.", 200) 
