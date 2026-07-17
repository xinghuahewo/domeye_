from contextlib import ExitStack
from datetime import datetime
from unittest.mock import patch

import pandas as pd
import pytest

from services import features_service


FEATURE_POINT_SCHEMA = {
    't': str,
    'announce': int,
    'withdraw': int,
    'v4Prefix_num': int,
    'v6Prefix_num': int,
    'v4IP_num': int,
}

AS_FEATURE_POINT_SCHEMA = {
    't': str,
    'asn': str,
    'announce': int,
    'withdraw': int,
}

SERIES_POINT_SCHEMA = {
    'time': str,
    'announce': int,
    'withdraw': int,
    'v4Prefix_num': int,
    'v6Prefix_num': int,
    'v4IP_num': int,
}

PAGE_SCHEMA = {
    'total_page': int,
    'record_count': int,
    'current_page': int,
    'page_size': int,
    'data': list,
}

OUTAGE_POINT_SCHEMA = {
    'time_slot': str,
    'outage_count': int,
}

RANGE = {
    'start_time': '2026-02-01 00:00:00',
    'end_time': '2026-02-01 00:03:00',
}


def feature_frame():
    return pd.DataFrame([
        {
            't': datetime(2026, 2, 1, 0, 0, 0),
            'announce': 12,
            'withdraw': 3,
            'v4Prefix_num': 100,
            'v6Prefix_num': 20,
            'v4IP_num': 25600,
        },
    ])


def as_feature_frame():
    return pd.DataFrame([
        {
            't': datetime(2026, 2, 1, 0, 0, 0),
            'asn': '1299',
            'announce': 12,
            'withdraw': 3,
        },
    ])


@pytest.mark.parametrize(
    ('target', 'database_target'),
    [
        pytest.param('中国', '中国', id='国家'),
        pytest.param('collector', 'collect', id='采集点'),
    ],
)
def test_top_feature_country_and_collector_contract(
    client,
    assert_contract,
    target,
    database_target,
):
    with patch(
        'services.features_service.select_country_feature_db',
        return_value=feature_frame(),
    ) as query:
        response = client.get(
            '/api/v1/features/top',
            query_string={**RANGE, 'target': target},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert type(payload) is list
    assert len(payload) == 1
    assert_contract(payload[0], FEATURE_POINT_SCHEMA)
    assert payload[0]['t'] == RANGE['start_time']
    assert query.call_args.kwargs['target'] == database_target


def test_top_feature_as_contract_and_lazy_static_loading(client, assert_contract):
    with patch('services.features_service.data_loader.ensure_core_data_loaded') as ensure, \
         patch('services.features_service.get_as_country', return_value='瑞典'), \
         patch(
             'services.features_service.select_as_feature_db',
             return_value=as_feature_frame(),
         ) as query:
        response = client.get(
            '/api/v1/features/top',
            query_string={**RANGE, 'target': 'AS1299'},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload) == 1
    assert_contract(payload[0], AS_FEATURE_POINT_SCHEMA)
    ensure.assert_called_once_with()
    assert query.call_args.kwargs['target'] == '1299'
    assert query.call_args.kwargs['table_name'] == 'feature_other'


def test_top_feature_missing_parameters_keeps_existing_error_contract(client, assert_contract):
    response = client.get('/api/v1/features/top', query_string={'target': 'collector'})

    assert response.status_code == 200
    payload = response.get_json()
    assert_contract(payload, {'status': bool, 'msg': str})
    assert payload == {
        'status': False,
        'msg': '缺少必需参数：start_time, end_time, target',
    }


def test_top_feature_query_failure_keeps_500_contract(client, assert_contract):
    with patch(
        'services.features_service.select_country_feature_db',
        side_effect=RuntimeError('数据库不可用'),
    ):
        response = client.get(
            '/api/v1/features/top',
            query_string={**RANGE, 'target': '中国'},
        )

    assert response.status_code == 500
    assert_contract(response.get_json(), {'status': bool, 'msg': str})
    assert response.get_json() == {'status': False, 'msg': '数据查询失败'}


def test_country_feature_list_contract(client, assert_contract):
    with patch('services.features_service.data_loader.ensure_core_data_loaded') as ensure, \
         patch.object(features_service.data_loader, 'country_list', ['中国']), \
         patch(
             'utils.get_event.select_country_feature_db',
             return_value=feature_frame(),
         ) as query:
        response = client.get(
            '/api/v1/features/countries',
            query_string={**RANGE, 'country': '中', 'page_num': '1', 'page_size': '5'},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert_contract(payload, PAGE_SCHEMA)
    assert payload['record_count'] == 1
    assert len(payload['data']) == 1
    assert_contract(payload['data'][0], {'country': str, 'time_series_data': list})
    assert payload['data'][0]['country'] == '中国'
    assert len(payload['data'][0]['time_series_data']) == 1
    assert_contract(payload['data'][0]['time_series_data'][0], SERIES_POINT_SCHEMA)
    ensure.assert_called_once_with()
    assert query.call_args.kwargs['target'] == '中国'


def test_as_feature_list_contract(client, assert_contract):
    ases = pd.DataFrame([{'asn': '4134', 'as_country_cn': '中国'}])
    metadata = {
        '4134': {
            'as_name': '中国电信',
            'as_country_cn': '中国',
            'org_name_cn': '中国电信集团',
        },
    }
    series = feature_frame().assign(asn='4134')

    with patch('services.features_service.data_loader.ensure_core_data_loaded') as ensure, \
         patch.object(features_service.data_loader, 'ases_1000', ases), \
         patch.object(features_service, 'as_info', metadata), \
         patch(
             'utils.get_event.select_as_list_feature_db',
             return_value=series,
         ) as query:
        response = client.get(
            '/api/v1/features/ases',
            query_string={**RANGE, 'country': '中国', 'page_num': '1', 'page_size': '5'},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert_contract(payload, PAGE_SCHEMA)
    assert payload['record_count'] == 1
    assert len(payload['data']) == 1
    item = payload['data'][0]
    assert_contract(item, {
        'asn': str,
        'as_name': str,
        'country': str,
        'org_name': str,
        'time_series_data': list,
    })
    assert item['asn'] == '4134'
    assert item['as_name'] == '中国电信'
    assert len(item['time_series_data']) == 1
    assert_contract(item['time_series_data'][0], SERIES_POINT_SCHEMA)
    ensure.assert_called_once_with()
    assert query.call_args.args[1] == {'feature_CN': ['4134']}


@pytest.mark.parametrize(
    'path',
    ['/api/v1/features/countries', '/api/v1/features/ases'],
)
def test_feature_lists_require_complete_time_range(client, assert_contract, path):
    response = client.get(path, query_string={'start_time': RANGE['start_time']})

    assert response.status_code == 200
    payload = response.get_json()
    assert_contract(payload, {'status': bool, 'msg': str})
    assert payload == {'status': False, 'msg': '开始时间和结束时间不能为空！'}


OUTAGE_CASES = [
    pytest.param(
        '/api/v1/features/outages/country-as',
        'asn',
        {'country': '中国'},
        {'country': '中国'},
        id='国家AS中断',
    ),
    pytest.param(
        '/api/v1/features/outages/country-prefix',
        'prefix',
        {'country': '中国'},
        {'country': '中国', 'asn': None},
        id='国家前缀中断',
    ),
    pytest.param(
        '/api/v1/features/outages/as-prefix',
        'prefix',
        {'asn': '4134'},
        {'country': None, 'asn': '4134'},
        id='AS前缀中断',
    ),
    pytest.param(
        '/api/v1/features/outages/global-as',
        'asn',
        {},
        {'country': None},
        id='全局AS中断',
    ),
    pytest.param(
        '/api/v1/features/outages/global-prefix',
        'prefix',
        {},
        {'country': None, 'asn': None},
        id='全局前缀中断',
    ),
]


@pytest.mark.parametrize(('path', 'outage_type', 'filters', 'expected_filters'), OUTAGE_CASES)
def test_five_outage_series_contracts(
    client,
    assert_contract,
    path,
    outage_type,
    filters,
    expected_filters,
):
    start_time = datetime(2026, 2, 1, 0, 0, 0)
    end_time = datetime(2026, 2, 1, 0, 3, 0)
    if outage_type == 'asn':
        rows = pd.DataFrame([{'asn': '4134', 's_time': start_time, 'e_time': None}])
        query_target = 'services.features_service.get_as_outage_by_interval'
    else:
        rows = pd.DataFrame([
            {'prefix': '1.2.3.0/24', 's_time': start_time, 'e_time': None},
        ])
        query_target = 'services.features_service.get_prefix_outage_by_interval'

    with ExitStack() as stack:
        query = stack.enter_context(patch(query_target, return_value=rows))
        if outage_type == 'prefix':
            ensure = stack.enter_context(
                patch('services.features_service.data_loader.ensure_core_data_loaded')
            )
            stack.enter_context(
                patch.object(features_service, 'prefix_info', {'1.2.3.0/24': {}})
            )
        response = client.get(path, query_string={**RANGE, **filters})

    assert response.status_code == 200
    payload = response.get_json()
    assert type(payload) is list
    assert len(payload) == 2
    for point in payload:
        assert_contract(point, OUTAGE_POINT_SCHEMA)
    assert payload == [
        {'time_slot': '2026-02-01 00:00:00', 'outage_count': 1},
        {'time_slot': '2026-02-01 00:03:00', 'outage_count': 1},
    ]
    assert query.call_args.kwargs['start_time'] == start_time
    assert query.call_args.kwargs['end_time'] == end_time
    for field, value in expected_filters.items():
        assert query.call_args.kwargs[field] == value
    if outage_type == 'prefix':
        ensure.assert_called_once_with()


@pytest.mark.parametrize(
    'path',
    [case.values[0] for case in OUTAGE_CASES],
)
def test_five_outage_series_require_complete_time_range(client, assert_contract, path):
    response = client.get(path, query_string={'start_time': RANGE['start_time']})

    assert response.status_code == 200
    payload = response.get_json()
    assert_contract(payload, {'status': bool, 'msg': str})
    assert payload == {'status': False, 'msg': '开始时间和结束时间不能为空！'}
