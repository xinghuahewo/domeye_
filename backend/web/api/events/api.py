from flask import request
from flask_restful import Resource
from services.event_story_service import (
    EventStoryUnavailable,
    get_iran_event_story,
)
from services.country_outage_registry import CountryOutageRegistryError
from services.country_outage_service import (
    CountryOutageInvalidReference,
    CountryOutageNotFound,
    CountryOutageSourceUnavailable,
    get_country_outage_query_context,
    get_legacy_country_outage_observation,
    resolve_country_outage,
)
from services.events_service import (
    get_event_evidence_bundle_data,
    get_event_detail_data,
    get_event_list_data,
    get_top_event_items,
)
from services.features_service import get_country_feature_series

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


class EventObservationResource(Resource):
    """兼容旧引用路径的国家中断观测合同。"""

    def get(self, event_type, start_time, problem, event_id, source):
        reference = "{}/{}/{}/{}/{}".format(
            event_type, start_time, problem, event_id, source
        )
        try:
            resolution = resolve_country_outage(reference)
            context = get_country_outage_query_context(
                resolution["incident_id"]
            )
        except (CountryOutageInvalidReference, CountryOutageNotFound):
            return {
                "status": False,
                "msg": "该事件尚未建立数据观测合同",
                "observation_state": "not_configured",
            }, 404
        except (
            CountryOutageRegistryError,
            CountryOutageSourceUnavailable,
        ) as error:
            return {
                "status": False,
                "msg": str(error),
                "observation_state": "unavailable",
            }, 503
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
            # 旧事实仅保留引用身份；不可用时保持未知，不阻断观测数据。
            legacy_detail = {}

        resource_payload = None
        if context["resource_state"] == "available":
            resource_payload = get_country_feature_series(
                start_time=context["window_start_local"][:19].replace(
                    "T", " "
                ),
                end_time=context["window_end_local"][:19].replace("T", " "),
                country=context["country_name"],
                page_num="1",
                page_size="5",
            )
        resource_items = (
            resource_payload.get("data")
            if isinstance(resource_payload, dict)
            else None
        )
        resource_item = next(
            (
                item
                for item in resource_items or []
                if isinstance(item, dict)
                and item.get("country") == context["country_name"]
            ),
            None,
        )
        resource_series = (
            resource_item.get("time_series_data")
            if isinstance(resource_item, dict)
            else None
        )
        try:
            payload = get_legacy_country_outage_observation(
                reference,
                legacy_detail=legacy_detail,
                resource_series=resource_series,
            )
        except EventStoryUnavailable as error:
            return {
                "status": False,
                "msg": str(error),
                "observation_state": "unavailable",
            }, 503
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
