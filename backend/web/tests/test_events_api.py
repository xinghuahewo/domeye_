import json
import os
import sys
import types
from datetime import datetime
from unittest.mock import MagicMock, patch

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
from services import events_service
from web.tests.test_auth_api import generate_test_token


@pytest.fixture(scope='module')
def app():
    os.environ['FLASK_CONFIG'] = 'testing'
    app = create_app()
    del os.environ['FLASK_CONFIG']
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


class TestEventsAPI:
    @patch('web.api.events.api.get_event_list_data')
    def test_event_list_api(self, mock_service, client):
        mock_service.return_value = {'total_page': 10, 'record_count': '100', 'data': [{"event": "mock_event"}]}

        query_params = {
            'page_size': '10',
            'page_num': '1',
            'event_type': 'hijack',
            'attacker_as': '4837',
        }
        response = client.get('/api/v1/events', query_string=query_params)

        assert response.status_code == 200
        response_data = json.loads(response.data)
        assert response_data['total_page'] == 10
        assert response_data['record_count'] == '100'
        assert response_data['data'] == [{"event": "mock_event"}]
        mock_service.assert_called_once_with(params=query_params)

    @patch('services.events_service.get_event')
    @patch('services.events_service.get_total_page', return_value=(10, 100))
    @patch('services.events_service.deal_event', return_value=[{"event": "mock_event"}])
    def test_event_list_service(self, mock_deal, mock_total, mock_get):
        query_params = {
            'page_size': '10',
            'page_num': '1',
            'event_type': 'hijack',
            'attacker_as': '4837',
        }

        result = events_service.get_event_list_data(query_params)

        assert result == {'total_page': 10, 'record_count': '100', 'data': [{"event": "mock_event"}]}
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args.kwargs
        assert call_kwargs['page_size'] == 10
        assert call_kwargs['page_num'] == 1
        assert call_kwargs['event_type'] == 'hijack'
        assert call_kwargs['attacker_as'] == '4837'
        mock_total.assert_called_once()

    @patch('web.api.events.api.get_top_event_items', return_value=[{"top_event": "mock"}])
    def test_top_event_api(self, mock_service, client):
        event_type_list = "['prefix_outage','as_outage','hijack']"

        response = client.get('/api/v1/events/top', query_string={'event_type': event_type_list})

        assert response.status_code == 200
        assert json.loads(response.data) == [{"top_event": "mock"}]
        mock_service.assert_called_once_with(event_type_str=event_type_list)

    @patch('services.events_service.get_top_event')
    @patch('services.events_service.deal_top_event', return_value=[{"top_event": "mock"}])
    def test_top_event_service(self, mock_deal, mock_get):
        event_type_list = "['prefix_outage','as_outage','hijack']"

        result = events_service.get_top_event_items(event_type_list, now=datetime(2024, 5, 10))

        assert result == [{"top_event": "mock"}]
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args.kwargs
        assert call_kwargs['event_type'] == tuple(eval(event_type_list))
        assert call_kwargs['event_table'] == 'event_table_202405'
        assert call_kwargs['last_month_table'] == 'event_table_202404'

    @patch('web.api.events.api.get_event_detail_data')
    def test_event_detail_api_hijack(self, mock_service, client):
        mock_service.return_value = {'event_info': 'Test event info'}
        url = '/api/v1/hijack/2024-05-10%2012:00:00/1.2.3.0-24/123/r'
        response = client.get(url)

        assert response.status_code == 200
        assert json.loads(response.data) == {'event_info': 'Test event info'}
        mock_service.assert_called_once_with(
            event_type='hijack',
            start_time='2024-05-10 12:00:00',
            problem='1.2.3.0-24',
            event_id=123,
            source='r',
            query_params={},
        )

    @patch('services.events_service.get_hijack_de')
    @patch('services.events_service.get_as_info', return_value={})
    @patch('services.events_service.get_prefix_domain_auth', return_value=[])
    @patch('services.events_service.get_prefix_domain', return_value=[])
    @patch('services.events_service.get_admin_info', return_value=([], [], []))
    @patch('services.events_service.get_as_feature', return_value=([], [], []))
    def test_event_detail_service_hijack(self, mock_feature, mock_admin, mock_dom, mock_dom_auth, mock_as, mock_get_detail):
        mock_hijack_row = {
            'hijacked_prefix': '1.2.3.0/24',
            'hijacked_as': 'AS64512',
            'hijacker_as': 'AS64513',
            'hijacked_as_name': 'Victim AS',
            'hijacked_as_org': 'Victim Org',
            'hijacked_as_country': 'US',
            'hijacker_as_name': 'Attacker AS',
            'hijacker_as_org': 'Attacker Org',
            'hijacker_as_country': 'RU',
            's_time': '2024-05-10 12:00:00',
            'e_time': '2024-05-10 13:00:00',
            'duration': 3600,
            'hijack_level_info': 'Critical',
            'hijack_level': 5,
            'pre_vp_paths': [],
            'eve_vp_paths': [],
            'next_vp_paths': [],
            'event_info': 'Test event info',
        }
        mock_get_detail.return_value = [mock_hijack_row]

        result = events_service.get_event_detail_data(
            event_type='hijack',
            start_time='2024-05-10 12:00:00',
            problem='1.2.3.0-24',
            event_id=123,
            source='r',
            query_params={},
        )

        assert result['hijacked_prefix'] == '1.2.3.0/24'
        assert result['attacked_as'] == 'AS64512'
        assert result['attacker_as'] == 'AS64513'
        assert result['event_info'] == 'Test event info'
        mock_get_detail.assert_called_once()
        call_kwargs = mock_get_detail.call_args.kwargs
        assert call_kwargs['prefix'] == '1.2.3.0/24'
        assert call_kwargs['hijack_id'] == 123
        assert call_kwargs['source'] == 'r'

    @patch('web.api.events.api.get_event_state', return_value=({'status': True, 'state': 'notify'}, 200))
    def test_event_state_api(self, mock_service, client):
        response = client.get('/api/v1/events/state', query_string={'detail_url': 'hijack/2024-05-10 12:00:00/1.2.3.0-24/123/r'})

        assert response.status_code == 200
        assert json.loads(response.data) == {'status': True, 'state': 'notify'}
        mock_service.assert_called_once_with(detail_url='hijack/2024-05-10 12:00:00/1.2.3.0-24/123/r')

    @patch('services.events_service.conn_11')
    def test_event_state_service(self, mock_conn):
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {'state': 'suspected'}

        result, status_code = events_service.get_event_state('hijack/2024-05-10 12:00:00/1.2.3.0-24/123/r')

        assert status_code == 200
        assert result == {'status': True, 'state': 'suspected'}
        select_sql = mock_cursor.execute.call_args[0][0]
        assert 'select state from event_table_202405' in select_sql.lower()

    @patch('services.events_service.conn_11')
    def test_event_judge_service(self, mock_conn):
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {'username': '测试管理员'}

        detail_url = 'hijack/2024-05-10 12:00:00/1.2.3.0-24/123/r'
        result, status_code = events_service.judge_event(
            detail_url=detail_url,
            state='suspected',
            judge_reason='This is a test judgment.',
            userid='admin',
        )

        assert status_code == 200
        assert result['status'] is True
        mock_conn.commit.assert_called_once()
        update_sql = mock_cursor.execute.call_args_list[1][0][0]
        assert 'UPDATE event_table_202405' in update_sql
        assert 'SET state = %s' in update_sql

        update_params = mock_cursor.execute.call_args_list[1][0][1]
        assert update_params[0] == 'suspected'
        assert update_params[1] == 'This is a test judgment.'
        assert update_params[5] == detail_url

    @patch('services.events_service.conn_11')
    def test_event_notify_service(self, mock_conn):
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            {'username': '测试管理员'},
            {'state': 'notify'},
        ]

        detail_url = 'hijack/2024-05-10 12:00:00/1.2.3.0-24/123/r'
        result, status_code = events_service.notify_event(detail_url=detail_url, userid='admin')

        assert status_code == 200
        assert result['status'] is True
        mock_conn.commit.assert_called_once()
        update_sql = mock_cursor.execute.call_args_list[2][0][0]
        assert 'UPDATE event_table_202405' in update_sql
        update_params = mock_cursor.execute.call_args_list[2][0][1]
        assert update_params[0] == 'notified'
        assert update_params[4] == detail_url

    @patch('web.api.events.api.notify_event', return_value=({'status': True, 'msg': '事件通报成功！'}, 200))
    def test_event_notify_api(self, mock_service, client):
        admin_token = generate_test_token('admin', 'admin')
        detail_url = 'hijack/2024-05-10 12:00:00/1.2.3.0-24/123/r'

        response = client.post(
            '/api/v1/events/notify',
            json={'detail_url': detail_url},
            headers={'Authorization': admin_token},
        )

        assert response.status_code == 200
        assert json.loads(response.data) == {'status': True, 'msg': '事件通报成功！'}
        mock_service.assert_called_once_with(detail_url=detail_url, userid='admin')
