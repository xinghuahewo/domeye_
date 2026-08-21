"""Domeye Core v2 API 路由。"""

from flask import Blueprint
from flask_restful import Api

from .country_outages import (
    CountryOutageAsnResource,
    CountryOutageAuditResource,
    CountryOutageOverviewResource,
    CountryOutagePathDownstreamResource,
    CountryOutageResolveResource,
    CountryOutageSeriesResource,
    CountryOutageTrendResource,
)
from .country_outage_chat_proxy import (
    CountryOutageChatCancelResource,
    CountryOutageChatConversationCollectionResource,
    CountryOutageChatConversationResource,
    CountryOutageChatTurnCollectionResource,
)


api_v2_bp = Blueprint("api_v2", __name__)
api = Api(api_v2_bp)

api.add_resource(CountryOutageResolveResource, "/events/resolve")
api.add_resource(
    CountryOutageOverviewResource,
    "/country-outages/<incident_id>/overview",
)
api.add_resource(
    CountryOutageSeriesResource,
    "/country-outages/<incident_id>/series",
)
api.add_resource(
    CountryOutageAsnResource,
    "/country-outages/<incident_id>/asns",
)
api.add_resource(
    CountryOutagePathDownstreamResource,
    "/country-outages/<incident_id>/path-downstreams",
)
api.add_resource(
    CountryOutageAuditResource,
    "/country-outages/<incident_id>/audit",
)
api.add_resource(
    CountryOutageTrendResource,
    "/country-outages/<incident_id>/trend",
)
api.add_resource(
    CountryOutageChatConversationCollectionResource,
    "/country-outage/chat/conversations",
)
api.add_resource(
    CountryOutageChatConversationResource,
    "/country-outage/chat/conversations/<conversation_id>",
)
api.add_resource(
    CountryOutageChatTurnCollectionResource,
    "/country-outage/chat/conversations/<conversation_id>/turns",
)
api.add_resource(
    CountryOutageChatCancelResource,
    (
        "/country-outage/chat/conversations/<conversation_id>/turns/"
        "<turn_id>/cancel"
    ),
)
