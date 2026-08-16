from flask import request
from flask_restful import Resource
from services.dashboard_service import (
    get_dashboard_overview,
    get_total_event_counts,
    get_type_event_counts,
)

# --- Resource Classes ---

class EventCountResource(Resource):
    """
    获取事件总数统计
    Endpoint: /api/v1/dashboard/counts/total
    """
    def get(self):
        country = request.args.get('country')
        return get_total_event_counts(country=country)

class TypeEventCountResource(Resource):
    """
    获取按类型统计的事件数
    Endpoint: /api/v1/dashboard/counts/type
    """
    def get(self):
        event_type = request.args.get('event_type')
        return get_type_event_counts(event_type=event_type)


class DashboardOverviewResource(Resource):
    """获取首页 24 小时六类异常聚合。"""

    def get(self):
        return get_dashboard_overview(
            start_time=request.args.get('start_time'),
            end_time=request.args.get('end_time'),
        )
