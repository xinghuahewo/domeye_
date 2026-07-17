from flask import request
from flask_restful import Resource
from services.events_service import (
    get_event_detail_data,
    get_event_list_data,
    get_top_event_items,
)

# --- Resource Classes ---

class EventListResource(Resource):
    """
    获取事件列表（核心接口）
    Endpoint: /api/v1/events/
    /event?country=foreign&page_num=1&page_size=10&event_type=all
    &level=all&attacker_as=&attacked_as=&attacker_org=&attacked_org=
    &attacked_country=
    &attacker_country=&event_info=&date=2025-07-17_2025-07-19&sort_mode=
    """
    def get(self):
        return get_event_list_data(params=request.args.to_dict(flat=True))


class TopEventResource(Resource):
    """
    获取置顶/最新事件
    Endpoint: /api/v1/events/top
    """
    def get(self):
        return get_top_event_items(event_type_str=request.args.get('event_type'))


### TODO get_feature 
class EventDetailResource(Resource):
    """
    获取单个事件的详细信息
    Endpoint: /api/v1/<event_type>/<start_time>/<problem>/<event_id>/<source>
    """
    def get(self, event_type, start_time, problem, event_id, source):
        return get_event_detail_data(
            event_type=event_type,
            start_time=start_time,
            problem=problem,
            event_id=event_id,
            source=source,
            query_params=request.args.to_dict(flat=True),
        )
