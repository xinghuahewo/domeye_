from datetime import date, datetime
from unittest.mock import patch

from utils.get_event import get_event_count


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


class _QueryCursor:
    def __init__(self):
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((statement, params))

    def fetchall(self):
        return []

    def close(self):
        return None


class _QueryConnection:
    def __init__(self):
        self.cursor_instance = _QueryCursor()

    def cursor(self, **_kwargs):
        return self.cursor_instance


def test_total_event_count_query_uses_exactly_six_core_types():
    conn = _QueryConnection()
    with patch('utils.get_event.if_table_exist', return_value=True):
        get_event_count(
            conn=conn,
            last_month_table='event_table_202606',
            event_table='event_table_202607',
            country=None,
            now=datetime(2026, 7, 19, 12, 0, 0),
        )

    statement = conn.cursor_instance.statements[0][0]
    for event_type in ('前缀劫持', '子前缀劫持', '路由泄漏', '前缀中断', 'AS中断', '国家中断'):
        assert event_type in statement
    assert '边界中断' not in statement


def test_dashboard_overview_contract(client, assert_contract):
    statistics = [
        {
            'period': 'current',
            'bucket': datetime(2026, 3, 31, 18, 0, 0),
            'event_type': '前缀劫持',
            'level': 'high',
            'missing_end': True,
            'count': 1,
        },
        {
            'period': 'current',
            'bucket': datetime(2026, 3, 31, 18, 0, 0),
            'event_type': '路由泄漏',
            'level': 'middle',
            'missing_end': True,
            'count': 1,
        },
        {
            'period': 'current',
            'bucket': datetime(2026, 3, 31, 21, 0, 0),
            'event_type': '国家中断',
            'level': 'low',
            'missing_end': False,
            'count': 1,
        },
        {
            'period': 'previous',
            'bucket': None,
            'event_type': 'AS中断',
            'level': 'middle',
            'missing_end': False,
            'count': 1,
        },
    ]
    entities = [
        {
            'attacked_as': 'AS4134\n(CHINANET-BACKBONE)',
            'attacked_country': '中国',
            'level': 'high',
            'count': 1,
        },
        {
            'attacked_as': "['4134', '4837']",
            'attacked_country': '中国',
            'level': 'middle',
            'count': 1,
        },
        {
            'attacked_as': '',
            'attacked_country': '日本',
            'level': 'low',
            'count': 1,
        },
    ]
    with patch(
        'services.dashboard_service.get_dashboard_event_aggregates',
        return_value=(statistics, entities),
    ) as query, patch(
        'services.dashboard_service.get_latest_collector_observation',
        return_value=datetime(2026, 3, 31, 23, 59, 0),
    ):
        response = client.get(
            '/api/v1/dashboard/overview',
            query_string={
                'start_time': '2026-03-31 00:00:00',
                'end_time': '2026-03-31 23:59:59',
            },
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert_contract(payload, {
        'start_time': str,
        'end_time': str,
        'timezone': str,
        'latest_observation': str,
        'event_count': int,
        'previous_event_count': int,
        'event_change_rate': float,
        'high_risk_count': int,
        'active_event_count': int,
        'affected_asn_count': int,
        'affected_country_count': int,
        'event_series': list,
        'country_rankings': list,
        'asn_rankings': list,
    })
    assert payload['event_count'] == 3
    assert payload['previous_event_count'] == 1
    assert payload['event_change_rate'] == 200.0
    assert payload['high_risk_count'] == 1
    # 路由泄漏没有结束时间字段，不能因此被误判为持续中的异常。
    assert payload['active_event_count'] == 1
    assert payload['affected_asn_count'] == 2
    assert payload['affected_country_count'] == 2
    assert payload['event_series'][0]['counts']['前缀劫持'] == 1
    assert payload['event_series'][0]['counts']['路由泄漏'] == 1
    assert payload['country_rankings'][0] == {
        'name': '中国',
        'event_count': 2,
        'high_risk_count': 1,
    }
    assert payload['asn_rankings'][0] == {
        'asn': '4134',
        'name': 'AS4134',
        'event_count': 2,
        'high_risk_count': 1,
    }
    assert query.call_args.kwargs['start_time'] == datetime(2026, 3, 30, 0, 0, 1)
    assert query.call_args.kwargs['current_start'] == datetime(2026, 3, 31, 0, 0, 0)
    assert query.call_args.kwargs['end_time'] == datetime(2026, 3, 31, 23, 59, 59)


def test_dashboard_overview_rejects_invalid_range(client):
    response = client.get(
        '/api/v1/dashboard/overview',
        query_string={
            'start_time': '2026-03-31 23:59:59',
            'end_time': '2026-03-31 00:00:00',
        },
    )

    assert response.status_code == 400
    assert response.get_json()['status'] is False


def test_dashboard_overview_rejects_ranges_over_24_hours(client):
    response = client.get(
        '/api/v1/dashboard/overview',
        query_string={
            'start_time': '2026-03-01 00:00:00',
            'end_time': '2026-03-02 00:00:01',
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {
        'status': False,
        'msg': '首页聚合最多支持 24 小时窗口',
    }
