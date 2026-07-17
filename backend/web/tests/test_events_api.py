from unittest.mock import patch

import pytest

from services import events_service


def test_event_list_api_delegates_query_params(client):
    expected = {'total_page': 1, 'record_count': '1', 'data': [{'event_type': '前缀劫持'}]}
    with patch('web.api.events.api.get_event_list_data', return_value=expected) as service:
        response = client.get('/api/v1/events', query_string={'page_num': '1', 'event_type': '前缀劫持'})

    assert response.status_code == 200
    assert response.get_json() == expected
    service.assert_called_once_with(params={'page_num': '1', 'event_type': '前缀劫持'})


def test_event_list_service_keeps_core_filters():
    params = {'page_num': '2', 'page_size': '50', 'date': '2026-07-01_2026-07-17'}
    with patch('services.events_service.get_event', return_value=[]) as query, \
         patch('services.events_service.get_total_page', return_value=(0, 0)), \
         patch('services.events_service.deal_event', return_value=[]):
        result = events_service.get_event_list_data(params, conn=object())

    assert result == {'total_page': 0, 'record_count': '0', 'data': []}
    assert query.call_args.kwargs['page_num'] == 2
    assert query.call_args.kwargs['page_size'] == 50
    assert query.call_args.kwargs['start_time'] == '2026-07-01'
    assert query.call_args.kwargs['end_time'] == '2026-07-17'
    assert query.call_args.kwargs['state'] is None


def test_top_events_reject_non_core_type_without_eval():
    with patch('services.events_service.get_top_event', return_value=[]) as query, \
         patch('services.events_service.deal_top_event', return_value=[]):
        result = events_service.get_top_event_items(
            '["前缀劫持", "边界中断"]',
            conn=object(),
        )

    assert result == []
    assert query.call_args.kwargs['event_type'] == ('前缀劫持',)


@pytest.mark.parametrize(
    ('event_type', 'query_name', 'problem'),
    [
        ('prefix_outage', 'get_pre_outage_de', '1.2.3.0-24'),
        ('as_outage', 'get_as_outage_de', '64512'),
        ('country_outage', 'get_country_outage_de', 'CN'),
        ('hijack', 'get_hijack_de', '1.2.3.0-24'),
        ('sub_hijack', 'get_sub_hijack_de', '1.2.3.0-25'),
        ('leak', 'get_leak_de', '1.2.3.0-24'),
    ],
)
def test_six_core_details_return_empty_for_missing_fact(event_type, query_name, problem):
    with patch(f'services.events_service.{query_name}', return_value=[]):
        result = events_service.get_event_detail_data(
            event_type=event_type,
            start_time='2026-07-17 00:00:00',
            problem=problem,
            event_id=1,
            source='r',
        )
    assert result == {}


def test_hijack_detail_contains_facts_without_heavy_enrichment():
    row = {
        'hijacked_as': '64512',
        'hijacked_as_name': '受影响网络',
        'hijacked_as_org': '受影响机构',
        'hijacked_as_country': '中国',
        'hijacked_as_descr': '描述',
        'hijacked_as_admin': '联系人',
        'hijacker_as': '64513',
        'hijacker_as_name': '异常来源',
        'hijacker_as_org': '来源机构',
        'hijacker_as_country': '美国',
        'hijacker_as_descr': '描述',
        'hijacker_as_admin': '联系人',
        's_time': '2026-07-17 00:00:00',
        'e_time': None,
        'duration': None,
        'hijack_level': 'high',
        'hijack_level_info': '高风险',
        'pre_vp_paths': [],
        'eve_vp_paths': [['64500', '64513']],
        'next_vp_paths': None,
        'event_info': '检测到源 AS 变化',
    }
    with patch('services.events_service.get_hijack_de', return_value=[row]):
        result = events_service.get_event_detail_data(
            'hijack', '2026-07-17 00:00:00', '1.2.3.0-24', 1, 'r'
        )

    assert result['hijacked_prefix'] == '1.2.3.0/24'
    assert result['attacker_as'] == '64513'
    assert result['eve_vp_paths'] == [['64500', '64513']]
    for removed_field in ('domain_list', 'graph', 'time_list', 'announ_list', 'withdraw_list'):
        assert removed_field not in result
