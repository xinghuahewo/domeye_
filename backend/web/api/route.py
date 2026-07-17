"""Domeye Core API 路由白名单。"""

from flask import Blueprint
from flask_restful import Api

from .dashboard.api import EventCountResource, TypeEventCountResource
from .events.api import EventDetailResource, EventListResource, TopEventResource
from .features.api import (
    ASFeatureListResource,
    ASPrefixOutageFeatureResource,
    CountryASOutageFeatureResource,
    CountryFeatureListResource,
    CountryPrefixOutageFeatureResource,
    GlobalASOutageFeatureResource,
    GlobalPrefixOutageFeatureResource,
    TopFeatureResource,
)
from .health.api import HealthzResource


api_v1_bp = Blueprint('api_v1', __name__)
api = Api(api_v1_bp)

api.add_resource(HealthzResource, '/healthz')

api.add_resource(EventListResource, '/events')
api.add_resource(TopEventResource, '/events/top')
api.add_resource(
    EventDetailResource,
    '/<event_type>/<start_time>/<problem>/<int:event_id>/<source>',
)

api.add_resource(TopFeatureResource, '/features/top')
api.add_resource(CountryFeatureListResource, '/features/countries')
api.add_resource(ASFeatureListResource, '/features/ases')
api.add_resource(CountryASOutageFeatureResource, '/features/outages/country-as')
api.add_resource(CountryPrefixOutageFeatureResource, '/features/outages/country-prefix')
api.add_resource(ASPrefixOutageFeatureResource, '/features/outages/as-prefix')
api.add_resource(GlobalASOutageFeatureResource, '/features/outages/global-as')
api.add_resource(GlobalPrefixOutageFeatureResource, '/features/outages/global-prefix')

api.add_resource(EventCountResource, '/dashboard/counts/total')
api.add_resource(TypeEventCountResource, '/dashboard/counts/type')
