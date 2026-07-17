import pytest
import os
import sys
import json
import types
from unittest.mock import patch
from datetime import datetime

# --- 动态添加项目根目录到 sys.path ---
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

    resource_names = [
        'WordExportResource',
        'ExcelExportResource',
        'ExcelExportCountryResource',
        'TemplateExportResource',
        'FileDownloadResource',
        'CountryOutageReportDataResource',
        'CountryOutageReportExportResource',
    ]
    for resource_name in resource_names:
        dummy_resource = type(resource_name, (Resource,), {})
        setattr(fake_reports_api_module, resource_name, dummy_resource)

    fake_reports_package.api = fake_reports_api_module
    sys.modules['web.api.reports'] = fake_reports_package
    sys.modules['web.api.reports.api'] = fake_reports_api_module


install_fake_reports_module()

# --- 导入Flask应用工厂 ---
from run import create_app
from services import dashboard_service

@pytest.fixture(scope='module')
def app():
    """ Pytest Fixture: 创建和配置用于测试的Flask应用实例 """
    os.environ['FLASK_CONFIG'] = 'testing'
    app = create_app()
    del os.environ['FLASK_CONFIG']
    yield app

@pytest.fixture
def client(app):
    """ Pytest Fixture: 创建一个Flask测试客户端 """
    return app.test_client()

# --- Dashboard API Tests ---

class TestDashboardAPI:
    """
    为看板（Dashboard）相关的API端点提供测试用例。
    这些测试使用了模拟（Mock）技术来隔离外部依赖，如数据库和辅助函数。
    """

    def test_event_count_api(self, client):
        """ 测试: /api/v1/dashboard/counts/total 端点 """
        print("\n--- [测试] 事件总数统计接口 (EventCountResource) ---")

        expected_dealt_items = {
            "total": 100, "confirmed": 50, "suspected": 20, "hijack": 10, "outage": 5
        }

        with patch('web.api.dashboard.api.get_total_event_counts', return_value=expected_dealt_items) as mock_service:

            response = client.get('/api/v1/dashboard/counts/total')

            assert response.status_code == 200
            response_data = json.loads(response.data)
            assert response_data == expected_dealt_items

            mock_service.assert_called_once_with(country=None)


    def test_type_event_count_api(self, client):
        """ 测试: /api/v1/dashboard/counts/type 端点 """
        print("\n--- [测试] 按类型统计事件数接口 (TypeEventCountResource) ---")

        test_event_type = 'hijack'
        expected_dealt_items = {
            "today": "some_dealt_data",
            "yesterday": "other_dealt_data"
        }

        with patch('web.api.dashboard.api.get_type_event_counts', return_value=expected_dealt_items) as mock_service:

            response = client.get(f'/api/v1/dashboard/counts/type?event_type={test_event_type}')

            assert response.status_code == 200
            assert json.loads(response.data) == expected_dealt_items

            mock_service.assert_called_once_with(event_type=test_event_type)

    def test_vantage_point_state_api(self, client):
        """ 测试: /api/v1/dashboard/vantage-points/state 端点 """
        print("\n--- [测试] 采集点状态接口 (VantagePointStateResource) ---")

        expected_dealt_items = {
            'collector': 'global',
            'time': '2026-03-13 12:00:00',
            'ipv4_prefix_count': 10,
            'ipv6_prefix_count': 20,
            'ipv4_address_count': 4096,
            'ipv6_48_count': 32,
            'vp_count': 3,
            'private_as_count': 2,
            'path_count': 15,
            'public_as_count': 5,
            'is_outlier': False,
            'ipv4_prefix_normal_upper': 12,
            'ipv4_prefix_normal_lower': 8,
            'ipv6_prefix_normal_upper': 24,
            'ipv6_prefix_normal_lower': 16,
            'private_as_normal_upper': 3,
            'private_as_normal_lower': 1,
            'path_normal_upper': 18,
            'path_normal_lower': 10,
            'public_as_normal_upper': 7,
            'public_as_normal_lower': 4,
        }

        with patch('web.api.dashboard.api.get_vantage_point_state', return_value=expected_dealt_items) as mock_service:
            response = client.get('/api/v1/dashboard/vantage-points/state', query_string={'collector': 'global'})

            assert response.status_code == 200
            assert json.loads(response.data) == expected_dealt_items

            mock_service.assert_called_once_with(collector='global')

    def test_event_count_service_generates_dynamic_table_names(self):
        """ 测试: service 能根据日期动态生成正确的表名 """
        fake_now = datetime(2023, 5, 20)
        expected_current_table = 'event_table_202305'
        expected_last_month_table = 'event_table_202304'

        with patch('services.dashboard_service.get_event_count', return_value=[]) as mock_get, \
             patch('services.dashboard_service.deal_event_count', return_value={}) as mock_deal:
            result = dashboard_service.get_total_event_counts(now=fake_now)

            mock_get.assert_called_once()
            call_kwargs = mock_get.call_args.kwargs
            assert call_kwargs['event_table'] == expected_current_table
            assert call_kwargs['last_month_table'] == expected_last_month_table
            mock_deal.assert_called_once_with(event_rows=[])
            assert result == {}

    def test_sorted_event_count_service_orchestrates_helpers(self):
        """ 测试: service 负责排序事件统计的查询与组装 """
        mock_event_rows = [('org', 3)]
        expected_items = [{'name': 'org', 'count': 3}]

        with patch('services.dashboard_service.get_sort_event_count', return_value=mock_event_rows) as mock_get, \
             patch('services.dashboard_service.deal_sort_event_count', return_value=expected_items) as mock_deal:
            result = dashboard_service.get_sorted_event_counts(obj='org', country='CN')

            mock_get.assert_called_once()
            assert mock_get.call_args.kwargs['obj'] == 'org'
            assert mock_get.call_args.kwargs['country'] == 'CN'
            mock_deal.assert_called_once()
            assert mock_deal.call_args.kwargs['event_rows'] == mock_event_rows
            assert mock_deal.call_args.kwargs['obj'] == 'org'
            assert mock_deal.call_args.kwargs['country'] == 'CN'
            assert result == expected_items
