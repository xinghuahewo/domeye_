"""Domeye Core v2 API 路由。"""

from flask import Blueprint
from flask_restful import Api

from .country_outages import (
    CountryOutageAsnResource,
    CountryOutageAuditResource,
    CountryOutageOverviewResource,
    CountryOutageResolveResource,
    CountryOutageSeriesResource,
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
    CountryOutageAuditResource,
    "/country-outages/<incident_id>/audit",
)
