from flask import request
from flask_restful import Resource
from services.event_story_service import EventStoryUnavailable, get_iran_event_story
from services.events_service import (
    get_event_evidence_bundle_data,
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


class EventEvidenceBundleResource(Resource):
    """返回六类业务事实记录的只读 Evidence Bundle。"""

    def get(self, event_type, start_time, problem, event_id, source):
        payload = get_event_evidence_bundle_data(
            event_type=event_type,
            start_time=start_time,
            problem=problem,
            event_id=event_id,
            source=source,
        )
        if payload is None:
            return {'status': False, 'msg': '业务事实表中未找到该事件记录'}, 404
        return payload


class EventStoryResource(Resource):
    """返回满足事件详情页产品合同的研究型事件叙事。"""

    def get(self, event_type, start_time, problem, event_id, source):
        reference = "{}/{}/{}/{}/{}".format(
            event_type, start_time, problem, event_id, source
        )
        try:
            legacy_detail = get_event_detail_data(
                event_type=event_type,
                start_time=start_time,
                problem=problem,
                event_id=event_id,
                source=source,
                query_params=request.args.to_dict(flat=True),
            )
        except Exception:
            # 研究包是主叙事来源；旧事实仅用于口径对账，旧库不可用时保持未知。
            legacy_detail = {}
        try:
            payload = get_iran_event_story(
                legacy_reference=reference,
                legacy_detail=legacy_detail,
            )
        except EventStoryUnavailable as error:
            return {
                "status": False,
                "msg": str(error),
                "story_state": "unavailable",
            }, 503
        if payload is None:
            return {
                "status": False,
                "msg": "该事件尚未建立产品合同叙事",
                "story_state": "not_configured",
            }, 404
        return payload


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
