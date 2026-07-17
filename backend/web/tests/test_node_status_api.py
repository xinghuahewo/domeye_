import json
import os
import sys
import types
from unittest.mock import patch

import pytest


current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, '..', '..'))
sys.path.append(backend_dir)

from web.tests._fake_database import install_fake_database_module

install_fake_database_module()


def install_fake_outage_topo_module():
    fake_outage_topo_module = types.ModuleType('core.visualize.outage_topo')
    fake_outage_topo_module.generate_graph = lambda *args, **kwargs: {}
    fake_outage_topo_module.graph_to_dict = lambda *args, **kwargs: {}
    fake_outage_topo_module.build_country_topo_dict = lambda *args, **kwargs: {}
    sys.modules['core.visualize.outage_topo'] = fake_outage_topo_module


install_fake_outage_topo_module()


def install_fake_reports_module():
    from flask_restful import Resource

    fake_reports_package = types.ModuleType('web.api.reports')
    fake_reports_api_module = types.ModuleType('web.api.reports.api')

    for resource_name in [
        'WordExportResource',
        'ExcelExportResource',
        'ExcelExportCountryResource',
        'TemplateExportResource',
        'FileDownloadResource',
        'CountryOutageReportDataResource',
        'CountryOutageReportExportResource',
    ]:
        setattr(fake_reports_api_module, resource_name, type(resource_name, (Resource,), {}))

    fake_reports_package.api = fake_reports_api_module
    sys.modules['web.api.reports'] = fake_reports_package
    sys.modules['web.api.reports.api'] = fake_reports_api_module


install_fake_reports_module()

from run import create_app
from services import node_status_service


@pytest.fixture(scope='module')
def app():
    os.environ['FLASK_CONFIG'] = 'testing'
    app = create_app()
    del os.environ['FLASK_CONFIG']
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


class TestNodeStatusAPI:
    @patch('web.api.node_status.api.get_node_status_list', return_value={'data': [], 'total_page': 1, 'record_count': 0})
    def test_node_status_api_delegates_to_service(self, mock_service, client):
        response = client.get('/api/v1/node-status', query_string={'asn': '4134', 'page_num': '2', 'page_size': '50'})

        assert response.status_code == 200
        assert json.loads(response.data) == {'data': [], 'total_page': 1, 'record_count': 0}
        mock_service.assert_called_once_with(asn='4134', page_num='2', page_size='50')

    @patch('services.node_status_service.list_latest_vp_resources', return_value=[
        {
            'asn': '4134',
            'as_name': 'CHINANET-BACKBONE',
            'as_rank': 12,
            'ipv4_prefix_count': 5432,
            'ipv6_prefix_count': 123,
            'latest_time': '2026-03-23 00:00:00',
            'is_outlier': False,
        }
    ])
    @patch('services.node_status_service.count_latest_vp_resources', return_value=1)
    def test_node_status_service_normalizes_response(self, mock_count, mock_list):
        result = node_status_service.get_node_status_list(asn='4134', page_num='2', page_size='50')

        assert result == {
            'total_page': 1,
            'record_count': 1,
            'data': [
                {
                    'asn': '4134',
                    'as_name': 'CHINANET-BACKBONE',
                    'as_rank': 12,
                    'ipv4_prefixes': 5432,
                    'ipv6_prefixes': 123,
                    'latest_time': '2026-03-23 00:00:00',
                    'status': '正常',
                }
            ],
        }
        mock_count.assert_called_once()
        assert mock_count.call_args.kwargs['asn_like'] == '%4134%'
        assert mock_list.call_args.kwargs['page_num'] == 2
        assert mock_list.call_args.kwargs['page_size'] == 50

    @patch('services.node_status_service.list_latest_vp_resources', return_value=[])
    @patch('services.node_status_service.count_latest_vp_resources', return_value=0)
    def test_node_status_service_defaults_pagination(self, mock_count, mock_list):
        result = node_status_service.get_node_status_list(asn='', page_num='0', page_size='3')

        assert result == {'total_page': 1, 'record_count': 0, 'data': []}
        assert mock_count.call_args.kwargs['asn_like'] == '%%'
        assert mock_list.call_args.kwargs['page_num'] == 1
        assert mock_list.call_args.kwargs['page_size'] == 10
