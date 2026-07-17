import datetime
import json
import os
import sys
import types

import pandas as pd
import pytest
from unittest.mock import patch


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
from services import features_service


@pytest.fixture(scope='module')
def app():
    os.environ['FLASK_CONFIG'] = 'testing'
    app = create_app()
    del os.environ['FLASK_CONFIG']
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


class TestFeaturesAPI:
    @patch('web.api.features.api.get_top_feature_data', return_value=[{'t': '2024-01-01 00:00:00'}])
    def test_top_feature_api_delegates_to_service(self, mock_service, client):
        params = {
            'start_time': '2024-01-01 00:00:00',
            'end_time': '2024-01-02 00:00:00',
            'target': 'AS123',
        }

        response = client.get('/api/v1/features/top', query_string=params)

        assert response.status_code == 200
        assert json.loads(response.data) == [{'t': '2024-01-01 00:00:00'}]
        mock_service.assert_called_once_with(
            start_time=params['start_time'],
            end_time=params['end_time'],
            target=params['target'],
        )

    @patch('services.features_service.deal_features', return_value=[{'t': '2024-01-01 00:00:00'}])
    @patch('services.features_service.select_country_feature_db')
    @patch('services.features_service.select_as_feature_db')
    @patch('services.features_service.get_as_country', return_value='中国')
    def test_top_feature_service_routes_as_country_and_collector(
        self,
        mock_get_as_country,
        mock_select_as_feature_db,
        mock_select_country_feature_db,
        mock_deal_features,
    ):
        time_params = {'start_time': '2024-01-01 00:00:00', 'end_time': '2024-01-02 00:00:00'}

        result = features_service.get_top_feature_data(target='AS123', **time_params)
        assert result == [{'t': '2024-01-01 00:00:00'}]
        mock_get_as_country.assert_called_once()
        mock_select_as_feature_db.assert_called_once()
        assert mock_select_as_feature_db.call_args.kwargs['target'] == '123'
        assert mock_select_as_feature_db.call_args.kwargs['table_name'] == 'feature_CN'

        mock_select_as_feature_db.reset_mock()
        mock_select_country_feature_db.reset_mock()
        mock_deal_features.reset_mock()
        mock_deal_features.return_value = [{'t': '2024-01-01 00:00:00'}]

        features_service.get_top_feature_data(target='中国', **time_params)
        mock_select_country_feature_db.assert_called_once()
        assert mock_select_country_feature_db.call_args.kwargs['target'] == '中国'

        mock_select_country_feature_db.reset_mock()
        features_service.get_top_feature_data(target='collector', **time_params)
        mock_select_country_feature_db.assert_called_once()
        assert mock_select_country_feature_db.call_args.kwargs['target'] == 'collect'

    @patch('web.api.features.api.get_country_feature_series', return_value={'data': 'mock_country_list'})
    def test_country_feature_list_api(self, mock_service, client):
        query_params = {
            'start_time': '2024-01-01 00:00:00',
            'end_time': '2024-01-02 00:00:00',
            'country': '中国',
            'page_num': '2',
            'page_size': '10',
        }

        response = client.get('/api/v1/features/countries', query_string=query_params)

        assert response.status_code == 200
        assert json.loads(response.data) == {'data': 'mock_country_list'}
        mock_service.assert_called_once_with(
            start_time=query_params['start_time'],
            end_time=query_params['end_time'],
            country='中国',
            page_num='2',
            page_size='10',
        )

    @patch('services.features_service.get_country_feature_list', return_value={'data': 'mock_country_list'})
    @patch('services.features_service.data_loader.country_list', ['中国', '美国'])
    def test_country_feature_service_normalizes_pagination(self, mock_get_country_feature_list):
        result = features_service.get_country_feature_series(
            start_time='2024-01-01 00:00:00',
            end_time='2024-01-02 00:00:00',
            country='中国',
            page_num='2',
            page_size='10',
        )

        assert result == {'data': 'mock_country_list'}
        call_kwargs = mock_get_country_feature_list.call_args.kwargs
        assert call_kwargs['country'] == '中国'
        assert call_kwargs['page_num'] == 2
        assert call_kwargs['page_size'] == 10
        assert call_kwargs['all_country_list'] == ['中国', '美国']

    @patch('web.api.features.api.get_as_feature_series', return_value={'data': 'mock_as_list'})
    def test_as_feature_list_api(self, mock_service, client):
        query_params = {
            'start_time': '2024-01-01 00:00:00',
            'end_time': '2024-01-02 00:00:00',
            'asn': '12345',
            'country': '美国',
            'page_num': '1',
            'page_size': '20',
        }

        response = client.get('/api/v1/features/ases', query_string=query_params)

        assert response.status_code == 200
        assert json.loads(response.data) == {'data': 'mock_as_list'}
        mock_service.assert_called_once_with(
            start_time=query_params['start_time'],
            end_time=query_params['end_time'],
            asn='12345',
            country='美国',
            page_num='1',
            page_size='20',
        )

    @patch(
        'services.features_service.data_loader.ases_1000',
        pd.DataFrame(
            [
                {'asn': '12345', 'as_country_cn': '美国'},
                {'asn': '23456', 'as_country_cn': '中国'},
            ]
        ),
    )
    @patch('services.features_service.get_as_features_list', return_value={'data': 'mock_as_list'})
    def test_as_feature_service_filters_country_and_asn(self, mock_get_as_features_list):
        result = features_service.get_as_feature_series(
            start_time='2024-01-01 00:00:00',
            end_time='2024-01-02 00:00:00',
            asn='12345',
            country='美国',
            page_num='1',
            page_size='20',
        )

        assert result == {'data': 'mock_as_list'}
        call_kwargs = mock_get_as_features_list.call_args.kwargs
        assert call_kwargs['ases'] == ['12345']
        assert call_kwargs['page_num'] == 1
        assert call_kwargs['page_size'] == 20

    def test_missing_time_params(self, client):
        response = client.get('/api/v1/features/countries', query_string={'country': '中国'})
        assert response.status_code == 200
        assert json.loads(response.data)['status'] is False

        response = client.get('/api/v1/features/ases', query_string={'asn': '123'})
        assert response.status_code == 200
        assert json.loads(response.data)['status'] is False

    @patch('web.api.features.api.get_as_outage_feature', return_value=[{'time_slot': '2024-01-01 00:00:00', 'outage_count': 3}])
    def test_global_as_outage_api(self, mock_service, client):
        query_params = {
            'start_time': '2024-01-01 00:00:00',
            'end_time': '2024-01-02 00:00:00',
        }

        response = client.get('/api/v1/features/outages/global-as', query_string=query_params)

        assert response.status_code == 200
        assert json.loads(response.data) == [{'time_slot': '2024-01-01 00:00:00', 'outage_count': 3}]
        mock_service.assert_called_once_with(country=None, start_time=query_params['start_time'], end_time=query_params['end_time'])

    @patch('services.features_service.deal_outage', return_value=[{'time_slot': '2024-01-01 00:00:00', 'outage_count': 3}])
    @patch('services.features_service.get_as_outage_by_interval', return_value='mock_df')
    def test_global_as_outage_service(self, mock_get_as_outage_by_interval, mock_deal_outage):
        result = features_service.get_as_outage_feature(
            country=None,
            start_time='2024-01-01 00:00:00',
            end_time='2024-01-02 00:00:00',
        )

        assert result == [{'time_slot': '2024-01-01 00:00:00', 'outage_count': 3}]
        assert mock_get_as_outage_by_interval.call_args.kwargs['country'] is None
        assert mock_deal_outage.call_args.kwargs['type'] == 'asn'

    @patch('web.api.features.api.get_prefix_outage_feature', return_value=[{'time_slot': '2024-01-01 00:00:00', 'outage_count': 5}])
    def test_global_prefix_outage_api(self, mock_service, client):
        query_params = {
            'start_time': '2024-01-01 00:00:00',
            'end_time': '2024-01-02 00:00:00',
        }

        response = client.get('/api/v1/features/outages/global-prefix', query_string=query_params)

        assert response.status_code == 200
        assert json.loads(response.data) == [{'time_slot': '2024-01-01 00:00:00', 'outage_count': 5}]
        mock_service.assert_called_once_with(country=None, asn=None, start_time=query_params['start_time'], end_time=query_params['end_time'])

    @patch('services.features_service.deal_outage', return_value=[{'time_slot': '2024-01-01 00:00:00', 'outage_count': 5}])
    @patch('services.features_service.get_prefix_outage_by_interval', return_value='mock_df')
    @patch('services.features_service.prefix_info', {'1.1.1.0/24': {}, '2.2.2.0/24': {}})
    def test_global_prefix_outage_service(self, mock_get_prefix_outage_by_interval, mock_deal_outage):
        result = features_service.get_prefix_outage_feature(
            country=None,
            asn=None,
            start_time='2024-01-01 00:00:00',
            end_time='2024-01-02 00:00:00',
        )

        assert result == [{'time_slot': '2024-01-01 00:00:00', 'outage_count': 5}]
        call_kwargs = mock_get_prefix_outage_by_interval.call_args.kwargs
        assert call_kwargs['country'] is None
        assert call_kwargs['asn'] is None
        assert list(mock_deal_outage.call_args.kwargs['prefixes']) == ['1.1.1.0/24', '2.2.2.0/24']

    @patch('web.api.features.api.get_country_as_outage_export')
    def test_country_as_outage_export_api(self, mock_service, client):
        mock_service.return_value = ('ok', 200)

        response = client.get(
            '/api/v1/features/outages/export/country-as',
            query_string={
                'country': '伊朗',
                'start_time': '2024-01-01 00:00:00',
                'end_time': '2024-01-02 00:00:00',
            },
        )

        assert response.status_code == 200
        mock_service.assert_called_once_with(
            country='伊朗',
            start_time='2024-01-01 00:00:00',
            end_time='2024-01-02 00:00:00',
        )

    @patch('services.features_service._load_pfx2as_dict', return_value={'12345': {'1.1.1.0/24': {}, '2001:db8::/48': {}}})
    @patch('services.features_service.get_tables_by_time', return_value=['prefix_outage_202401'])
    @patch('services.features_service.select_prefix_outage_detail_by_interval')
    def test_country_as_outage_export_service_uses_prefix_outage_details_for_space_ratio(
        self,
        mock_select_prefix_outage_detail_by_interval,
        mock_get_tables_by_time,
        mock_load_pfx2as_dict,
        app,
    ):
        mock_select_prefix_outage_detail_by_interval.return_value = [
            (
                '1.1.1.0/24',
                '12345',
                datetime.datetime(2024, 1, 1, 0, 0, 0),
                datetime.datetime(2024, 1, 1, 1, 0, 0),
                None,
            )
        ]

        with app.app_context():
            response = features_service.get_country_as_outage_export(
                country='伊朗',
                start_time='2024-01-01 00:00:00',
                end_time='2024-01-02 00:00:00',
            )

        assert response.status_code == 200
        mock_get_tables_by_time.assert_called_once()
        assert mock_get_tables_by_time.call_args.args[0] == 'prefix_outage'
        mock_select_prefix_outage_detail_by_interval.assert_called_once()
        mock_load_pfx2as_dict.assert_called_once()

        content = response.data.decode('utf-8-sig')
        assert '自治域号,断网IPv4地址数,正常IPv4地址数,不可见IPv4地址占比,断网IPv6地址数,正常IPv6地址数,不可见IPv6地址占比,地址是否完全不可见' in content
        assert '12345,256,256,100,0,65536,0,否' in content
