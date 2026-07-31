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
from .country_outage_agent_proxy import (
    CountryOutageAgentAbortResource,
    CountryOutageAgentArtifactResource,
    CountryOutageAgentExternalEvidenceCapabilityResource,
    CountryOutageAgentExternalAppendixArtifactResource,
    CountryOutageAgentEventResource,
    CountryOutageAgentQuestionResource,
    CountryOutageAgentReportCollectionResource,
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
api.add_resource(
    CountryOutageAgentExternalEvidenceCapabilityResource,
    "/country-outage/capabilities/external-evidence",
)
api.add_resource(
    CountryOutageAgentReportCollectionResource,
    "/country-outage/reports",
)
api.add_resource(
    CountryOutageAgentEventResource,
    "/country-outage/reports/<report_id>/events",
)
api.add_resource(
    CountryOutageAgentQuestionResource,
    "/country-outage/reports/<report_id>/questions",
)
api.add_resource(
    CountryOutageAgentAbortResource,
    "/country-outage/runs/<run_id>/abort",
)
api.add_resource(
    CountryOutageAgentArtifactResource,
    "/country-outage/reports/<report_id>/artifacts/<artifact_format>",
)
api.add_resource(
    CountryOutageAgentExternalAppendixArtifactResource,
    (
        "/country-outage/reports/<report_id>/questions/<question_id>/"
        "artifacts/external-appendix"
    ),
)
