#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
API Router Configuration
This file acts as the central API gateway, mapping URL endpoints to their corresponding Resource classes.
It follows the design pattern of fuxi/router.py for a clear and centralized routing definition.
"""

from flask import Blueprint
from flask_restful import Api

# --- Import All Resource Classes from Their Modules ---

# Auth Resources
from .auth.api import (
    UserLoginResource, UserRegisterResource, UserInfoEditResource,
    UserListResource, UserProfileResource, UserLogoutResource
)

# Event Resources
from .events.api import (
    EventListResource, TopEventResource, EventDetailResource,
    EventStateResource, EventJudgeResource, EventNotifyResource
)

# Feature Resources
from .features.api import (
    TopFeatureResource, CountryFeatureListResource, ASFeatureListResource,
    CountryASOutageFeatureResource, CountryPrefixOutageFeatureResource, ASPrefixOutageFeatureResource,
    GlobalASOutageFeatureResource, GlobalPrefixOutageFeatureResource,
    CountryASOutageExportResource, CountryPrefixOutageExportResource
)

# Geodata Resources
from .geodata.api import (
    BoundaryListResource, ConnectionListResource, BoundaryDisplayDataResource,
    ConnectionDisplayDataResource, BoundaryScreenResource, ConnectionScreenResource
)

# Report Resources
from .reports.api import (
    WordExportResource, ExcelExportResource, ExcelExportCountryResource,
    TemplateExportResource, FileDownloadResource,
    CountryOutageReportDataResource, CountryOutageReportExportResource
)

# Dashboard Resources
from .dashboard.api import (
    EventCountSortedResource, EventCountResource, GeoEventCountResource,
    TypeEventCountResource, VantagePointStateResource, SecurityScreenResource
)

# Node Status Resources
from .node_status.api import NodeStatusListResource

# Data Query Resources
from .data_query.api import (
    DataQueryTaskListResource, DataQueryTaskParseResource,
    DataQueryTaskDetailResource, DataQueryTaskGenerateResource,
    DataQueryTaskPreviewResource
)

# Health Resources
from .health.api import HealthzResource

# --- Create API v1 Blueprint ---
api_v1_bp = Blueprint('api_v1', __name__)
api = Api(api_v1_bp)

# --- Register All API Resources and Their Endpoints ---

# Health Route
api.add_resource(HealthzResource, '/healthz')

# Auth Routes (prefix: auth)ok
api.add_resource(UserLoginResource, '/login')
api.add_resource(UserRegisterResource, '/register')
api.add_resource(UserListResource, '/users')
api.add_resource(UserInfoEditResource, '/admin_edit')
api.add_resource(UserProfileResource, '/profile')
api.add_resource(UserLogoutResource, '/logout')

# Event Routes (prefix: events)ok
api.add_resource(EventListResource, '/events')
api.add_resource(TopEventResource, '/events/top')
api.add_resource(EventDetailResource, '/<event_type>/<start_time>/<problem>/<int:event_id>/<source>')
api.add_resource(EventStateResource, '/events/state')
api.add_resource(EventJudgeResource, '/events/judge')
api.add_resource(EventNotifyResource, '/events/notify')

# Feature Routes (prefix: features)
api.add_resource(TopFeatureResource, '/features/top')
api.add_resource(CountryFeatureListResource, '/features/countries')
api.add_resource(ASFeatureListResource, '/features/ases')
api.add_resource(CountryASOutageFeatureResource, '/features/outages/country-as')
api.add_resource(CountryPrefixOutageFeatureResource, '/features/outages/country-prefix')
api.add_resource(ASPrefixOutageFeatureResource, '/features/outages/as-prefix')
api.add_resource(GlobalASOutageFeatureResource, '/features/outages/global-as')
api.add_resource(GlobalPrefixOutageFeatureResource, '/features/outages/global-prefix')
api.add_resource(CountryASOutageExportResource, '/features/outages/export/country-as')
api.add_resource(CountryPrefixOutageExportResource, '/features/outages/export/country-prefix')

# Geodata Routes (prefix: geodata)
api.add_resource(BoundaryListResource, '/geodata/boundaries')
api.add_resource(ConnectionListResource, '/geodata/connections')
api.add_resource(BoundaryDisplayDataResource, '/geodata/boundaries/display')
api.add_resource(ConnectionDisplayDataResource, '/geodata/connections/display')
api.add_resource(BoundaryScreenResource, '/geodata/boundaries/screenfile')
api.add_resource(ConnectionScreenResource, '/geodata/connections/screenfile')

# Report Routes (prefix: reports) ok
api.add_resource(WordExportResource, '/reports/word-export')
api.add_resource(ExcelExportResource, '/reports/excel-export')
api.add_resource(ExcelExportCountryResource, '/reports/excel-export/country')
api.add_resource(TemplateExportResource, '/reports/template-export')
api.add_resource(FileDownloadResource, '/reports/download/<path:download_url>')

# 国家中断报告路由
api.add_resource(CountryOutageReportDataResource, '/reports/country-outage-data/<country>/<start_time>/<int:event_id>/<source>')
api.add_resource(CountryOutageReportExportResource, '/reports/country-outage-export/<country>/<start_time>/<int:event_id>/<source>')

# Dashboard Routes (prefix: dashboard)
api.add_resource(EventCountSortedResource, '/dashboard/counts/sorted')
api.add_resource(EventCountResource, '/dashboard/counts/total')
api.add_resource(GeoEventCountResource, '/dashboard/counts/geo')
api.add_resource(TypeEventCountResource, '/dashboard/counts/type')
api.add_resource(VantagePointStateResource, '/dashboard/vantage-points/state')
api.add_resource(SecurityScreenResource, '/dashboard/screens/security')

# Node Status Routes
api.add_resource(NodeStatusListResource, '/node-status')

# Data Query Routes
api.add_resource(DataQueryTaskListResource, '/data-query/tasks')
api.add_resource(DataQueryTaskParseResource, '/data-query/tasks/parse')
api.add_resource(DataQueryTaskDetailResource, '/data-query/tasks/<string:task_id>')
api.add_resource(DataQueryTaskGenerateResource, '/data-query/tasks/<string:task_id>/generate')
api.add_resource(DataQueryTaskPreviewResource, '/data-query/tasks/<string:task_id>/preview')
