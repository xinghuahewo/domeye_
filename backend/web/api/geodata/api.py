from flask import request, json, jsonify
from flask_restful import Resource
import re
import os
import datetime
from flask import make_response

# TODO: 数据库连接和辅助函数应该从主应用或共享模块中导入
# from backend.database import conn_11, conn_226
# from backend.utils.get_info import get_boundary, deal_boundary, ...
from config.database import conn_11, conn_226
from config.config import BOUNDARY_TABLE, CONNECTION_TABLE
from utils.get_event import (get_boundary, deal_boundary,
                             get_connection, deal_connection,
                             get_boundary_total_page,
                             get_connection_total_page)


# --- Resource Classes ---

class BoundaryListResource(Resource):
    """
    获取国家边界信息列表
    Endpoint: /api/v1/geodata/boundaries
    """
    def get(self):
        # Original logic from boundary_index()
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
        export_as_country = request.args.get('export_as_country')
        peer_as_country = request.args.get('peer_as_country')
        sort_mode = request.args.get('sort_mode')
        print(sort_mode)

        boundary_table = BOUNDARY_TABLE
        boundary_rows = get_boundary(conn=conn_226, boundary_table=boundary_table, page_num=page_num, 
                                     page_size=page_size, export_as_country=export_as_country, 
                                     peer_as_country=peer_as_country, sort_mode=sort_mode)
        boundary_items = deal_boundary(boundary_rows=boundary_rows)
        total_page, record_count = get_boundary_total_page(conn=conn_11, boundary_table=boundary_table, 
                                                           page_size=page_size, 
                                                           export_as_country=export_as_country,
                                                           peer_as_country=peer_as_country)
        d = dict()
        d['total_page'] = total_page
        d['record_count'] = record_count
        d['data'] = boundary_items
        return d

class ConnectionListResource(Resource):
    """
    获取国家间连通性信息列表
    Endpoint: /api/v1/geodata/connections
    """
    def get(self):
        # Original logic from connection_index()
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
        vp_country_chinese_name = request.args.get('vp_country_chinese_name')
        dst_country_chinese_name = request.args.get('dst_country_chinese_name')
        sort_mode = request.args.get('sort_mode')

        connection_table = CONNECTION_TABLE
        connection_rows = get_connection(conn=conn_226, connection_table=connection_table, 
                                         page_num=page_num, page_size=page_size, 
                                         vp_country_chinese_name=vp_country_chinese_name,
                                         dst_country_chinese_name=dst_country_chinese_name,
                                         sort_mode=sort_mode)
        connection_items = deal_connection(connection_rows=connection_rows)
        total_page, record_count = get_connection_total_page(conn=conn_11, connection_table=connection_table, 
                                                           page_size=page_size,
                                                           vp_country_chinese_name=vp_country_chinese_name,
                                                           dst_country_chinese_name=dst_country_chinese_name)
        d = dict()
        d['total_page'] = total_page
        d['record_count'] = record_count
        d['data'] = connection_items
        return d

class BoundaryDisplayDataResource(Resource):
    """
    获取用于大屏展示的国家边界数据
    Endpoint: /api/v1/geodata/boundaries/display
    """
    def get(self):
        # 假设 country 参数从 query string 获取, 例如: ?country=China
        country = request.args.get('country')
        if not country:
            return {'status': False, 'msg': '缺少 country 参数'}, 400
        
        try:
            with open("screen_data/output_data/boundary.json", encoding="utf-8") as f:
                boundary_data = json.load(f)
                # 直接返回对应国家的数据字典
                return boundary_data.get(country, {})
        except FileNotFoundError:
            return {'status': False, 'msg': '边界数据文件未找到'}, 404
        except Exception as e:
            return {'status': False, 'msg': str(e)}, 500

class ConnectionDisplayDataResource(Resource):
    """
    获取用于大屏展示的国家连通性数据
    Endpoint: /api/v1/geodata/connections/display
    """
    def get(self):
        # 假设 country 参数从 query string 获取
        country = request.args.get('country')
        if not country:
            return {'status': False, 'msg': '缺少 country 参数'}, 400
            
        try:
            with open("screen_data/output_data/connection.json", encoding="utf-8") as f:
                connection_data = json.load(f)
                # 直接返回对应国家的数据字典
                return connection_data.get(country, {})
        except FileNotFoundError:
            return {'status': False, 'msg': '连通性数据文件未找到'}, 404
        except Exception as e:
            return {'status': False, 'msg': str(e)}, 500

class BoundaryScreenResource(Resource):
    """
    获取大屏边界数据的 JSON 文件内容
    Endpoint: /api/v1/geodata/boundaries/screenfile
    """
    def get(self):
        boundary_data_path = os.path.abspath('screen_data/output_data/boundary.json')
        try:
            with open(boundary_data_path, encoding="utf-8") as f:
                data = json.load(f)
            return data
        except FileNotFoundError:
            return {'status': False, 'msg': '边界数据文件未找到'}, 404
        except Exception as e:
            return {'status': False, 'msg': str(e)}, 500

class ConnectionScreenResource(Resource):
    """
    获取大屏连通性数据的 JSON 文件内容
    Endpoint: /api/v1/geodata/connections/screenfile
    """
    def get(self):
        connection_data_path = os.path.abspath('screen_data/output_data/connection.json')
        try:
            with open(connection_data_path, encoding="utf-8") as f:
                data = json.load(f)
            return data
        except FileNotFoundError:
            return {'status': False, 'msg': '连通性数据文件未找到'}, 404
        except Exception as e:
            return {'status': False, 'msg': str(e)}, 500 