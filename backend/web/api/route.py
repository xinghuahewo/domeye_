"""Domeye Core API 路由白名单。"""

from flask import Blueprint
from flask_restful import Api

from .dashboard.api import DashboardOverviewResource, EventCountResource, TypeEventCountResource
from .events.api import (
    EventDetailResource,
    EventEvidenceBundleResource,
    EventListResource,
    EventStoryResource,
    TopEventResource,
)
from .features.api import (
    ASFeatureListResource,
    ASRecentEventsResource,
    ASWorkbenchResource,
    ASPrefixOutageFeatureResource,
    CountryASOutageFeatureResource,
    CountryFeatureListResource,
    CountryPrefixOutageFeatureResource,
    CountryWorkbenchResource,
    GlobalASOutageFeatureResource,
    GlobalPrefixOutageFeatureResource,
    TopFeatureResource,
)
from .health.api import HealthzResource
from .p0.api import (
    P0EvidenceResource,
    P0MetricResource,
    P0QualityResource,
    P0StatusResource,
)


api_v1_bp = Blueprint('api_v1', __name__)
api = Api(api_v1_bp)

api.add_resource(HealthzResource, '/healthz')

api.add_resource(P0StatusResource, '/p0/status')
api.add_resource(P0MetricResource, '/p0/metrics/<metric_name>')
api.add_resource(P0EvidenceResource, '/p0/evidence/<incident_id>')
api.add_resource(P0QualityResource, '/p0/quality')

api.add_resource(EventListResource, '/events')
api.add_resource(TopEventResource, '/events/top')
api.add_resource(
    EventEvidenceBundleResource,
    '/events/evidence-bundle/<event_type>/<start_time>/<problem>/<int:event_id>/<source>',
)
api.add_resource(
    EventStoryResource,
    '/events/story/<event_type>/<start_time>/<problem>/<int:event_id>/<source>',
)
api.add_resource(
    EventDetailResource,
    '/<event_type>/<start_time>/<problem>/<int:event_id>/<source>',
)

api.add_resource(TopFeatureResource, '/features/top')
api.add_resource(CountryFeatureListResource, '/features/countries')
api.add_resource(CountryWorkbenchResource, '/features/countries/overview')
api.add_resource(ASFeatureListResource, '/features/ases')
api.add_resource(ASWorkbenchResource, '/features/ases/overview')
api.add_resource(ASRecentEventsResource, '/features/ases/events')
api.add_resource(CountryASOutageFeatureResource, '/features/outages/country-as')
api.add_resource(CountryPrefixOutageFeatureResource, '/features/outages/country-prefix')
api.add_resource(ASPrefixOutageFeatureResource, '/features/outages/as-prefix')
api.add_resource(GlobalASOutageFeatureResource, '/features/outages/global-as')
api.add_resource(GlobalPrefixOutageFeatureResource, '/features/outages/global-prefix')

api.add_resource(EventCountResource, '/dashboard/counts/total')
api.add_resource(TypeEventCountResource, '/dashboard/counts/type')
api.add_resource(DashboardOverviewResource, '/dashboard/overview')
