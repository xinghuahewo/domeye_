from datetime import date
from unittest.mock import patch


def test_total_event_count_contract_uses_service_conversion(client, assert_contract):
    rows = [{'days': date(2026, 2, 1), 'count': 12}]
    with patch('services.dashboard_service.get_event_count', return_value=rows) as query:
        response = client.get(
            '/api/v1/dashboard/counts/total',
            query_string={'country': 'domestic'},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert type(payload) is list
    assert len(payload) == 1
    assert_contract(payload[0], {'time': str, 'num': int})
    assert payload == [{'time': '2026-02-01', 'num': 12}]
    assert query.call_args.kwargs['country'] == 'domestic'


def test_total_event_count_empty_result_contract(client):
    with patch('services.dashboard_service.get_event_count', return_value=[]):
        response = client.get('/api/v1/dashboard/counts/total')

    assert response.status_code == 200
    assert response.get_json() == []


def test_type_event_count_contract_uses_service_conversion(client, assert_contract):
    today = [{'event_type': '前缀劫持', 'count': 6}]
    yesterday = [{'event_type': '前缀劫持', 'count': 4}]
    with patch(
        'services.dashboard_service.get_type_event_count',
        return_value=(today, yesterday),
    ) as query:
        response = client.get(
            '/api/v1/dashboard/counts/type',
            query_string={'event_type': '前缀劫持'},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert_contract(payload, {
        'event_type': str,
        'num': int,
        'amplitude_type': bool,
        'amplitude': str,
        'icon': str,
    })
    assert payload == {
        'event_type': '前缀劫持',
        'num': 6,
        'amplitude_type': True,
        'amplitude': '50.00%',
        'icon': 'icon-dongtai',
    }
    assert query.call_args.kwargs['event_type'] == '前缀劫持'
    assert query.call_args.kwargs['country'] == 'global'


def test_type_event_count_zero_baseline_contract(client, assert_contract):
    with patch(
        'services.dashboard_service.get_type_event_count',
        return_value=([], []),
    ):
        response = client.get(
            '/api/v1/dashboard/counts/type',
            query_string={'event_type': '路由泄漏'},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert_contract(payload, {
        'event_type': str,
        'num': int,
        'amplitude_type': bool,
        'amplitude': str,
        'icon': str,
    })
    assert payload['num'] == 0
    assert payload['amplitude_type'] is True
    assert payload['amplitude'] == '0%'
    assert payload['icon'] == 'iconfont icon-ditu'
