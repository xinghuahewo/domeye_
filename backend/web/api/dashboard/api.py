from flask import request
from flask_restful import Resource
from services import (
    get_geo_event_counts,
    get_security_screen_data,
    get_sorted_event_counts,
    get_total_event_counts,
    get_type_event_counts,
    get_vantage_point_state,
)

# --- Resource Classes ---

class EventCountSortedResource(Resource):
    """
    获取排序后的事件统计（按机构或AS）
    Endpoint: /api/v1/dashboard/counts/sorted
    """
    def get(self):
        obj = request.args.get('obj')  # org/as
        country = request.args.get('country')
        return get_sorted_event_counts(obj=obj, country=country)

class EventCountResource(Resource):
    """
    获取事件总数统计
    Endpoint: /api/v1/dashboard/counts/total
    """
    def get(self):
        country = request.args.get('country')
        return get_total_event_counts(country=country)

class GeoEventCountResource(Resource):
    """
    获取按地理位置统计的事件数
    Endpoint: /api/v1/dashboard/counts/geo
    """
    def get(self):
        return get_geo_event_counts()

class TypeEventCountResource(Resource):
    """
    获取按类型统计的事件数
    Endpoint: /api/v1/dashboard/counts/type
    """
    def get(self):
        event_type = request.args.get('event_type')
        return get_type_event_counts(event_type=event_type)

class VantagePointStateResource(Resource):
    """
    获取路由采集点的状态
    Endpoint: /api/v1/dashboard/vantage-points/state
    """
    def get(self):
        collector = request.args.get('collector') or request.args.get('vp')
        return get_vantage_point_state(collector=collector)

class SecurityScreenResource(Resource):
    """
    获取安全大屏的数据文件内容
    Endpoint: /api/v1/dashboard/screens/security
    """
    def get(self):
        return get_security_screen_data()
