from urllib.parse import quote
from unittest.mock import patch

import pandas as pd
import pytest

from services import events_service


CORE_EVENT_TYPES = {'前缀劫持', '子前缀劫持', '前缀中断', 'AS中断', '国家中断', '路由泄漏'}
EVENT_LEVELS = {'high', 'middle', 'low'}
EVENT_STATES = {'judge', 'notify', 'misreport', 'suspected', 'notified', 'abroad'}


EVENT_ITEM_SCHEMA = {
    'event_type': str,
    'level': str,
    'start_time': str,
    'end_time': str,
    'attacker_as': str,
    'attacked_as': str,
    'event_info': str,
    'detail_url': str,
    'affected_prefix': str,
    'attacker_org': str,
    'attacked_org': str,
    'attacker_country': str,
    'attacked_country': str,
    'state': str,
    'judge_reason': (str, type(None)),
    'judge_userid': (str, type(None)),
    'judge_username': (str, type(None)),
    'judge_time': str,
    'notify_userid': (str, type(None)),
    'notify_username': (str, type(None)),
    'notify_time': str,
}

EVENT_ROW = {
    'event_type': '前缀劫持',
    'level': 'high',
    's_time': '2026-02-01 00:00:00',
    'e_time': '2026-02-01 00:03:00',
    'attacker_as': '64513',
    'attacked_as': '64512',
    'event_info': '检测到源 AS 变化',
    'detail_url': 'hijack/2026-02-01 00:00:00/1.2.3.0-24/7/r',
    'affected_prefix': '1.2.3.0/24',
    'attacker_org': '异常来源机构',
    'attacked_org': '受影响机构',
    'attacker_country': '美国',
    'attacked_country': '中国',
    'state': 'judge',
    'judge_reason': None,
    'judge_userid': None,
    'judge_username': None,
    'judge_time': '2026-02-01 00:05:00',
    'notify_userid': None,
    'notify_username': None,
    'notify_time': '2026-02-01 00:06:00',
}

EVIDENCE_HIJACK_ROW = {
    'hijacked_as': '64512',
    'hijacked_as_name': '受影响网络',
    'hijacked_as_org': '受影响机构',
    'hijacked_as_country': '中国',
    'hijacked_as_descr': None,
    'hijacked_as_admin': None,
    'hijacker_as': '64513',
    'hijacker_as_name': '异常来源',
    'hijacker_as_org': '来源机构',
    'hijacker_as_country': '美国',
    'hijacker_as_descr': None,
    'hijacker_as_admin': None,
    'hijack_level': 'high',
    'hijack_level_info': '高风险',
    'event_info': '检测到源 AS 变化',
    'pre_vp_paths': {'2026-02-01 00:00:00': ['64500 64512']},
    'eve_vp_paths': {'2026-02-01 00:01:00': []},
    'next_vp_paths': {'2026-02-01 00:03:00': ['64500 64512']},
    's_time': '2026-02-01 00:00:00',
    'e_time': '2026-02-01 00:03:00',
    'duration': '0:03:00',
}

COMMON_DETAIL_SCHEMA = {
    'start_time': str,
    'end_time': str,
    'duration': str,
    'event_level': str,
    'event_descr': str,
    'event_info': str,
}

DETAIL_CASES = [
    pytest.param(
        'prefix_outage',
        'get_pre_outage_de',
        '1.2.3.0-24',
        {
            'asn': '64512',
            'as_name': '受影响网络',
            'org_name': '受影响机构',
            'country': '中国',
            'outage_level': 'high',
            'outage_level_descr': '高风险',
            'event_info': '前缀不可达',
            'as_type': 'ISP',
            'as_descr': None,
            'as_admin': None,
            'pre_vp_paths': {'2026-01-31 23:55:00': ['64500 64512']},
            'eve_vp_paths': {'2026-02-01 00:00:00': []},
            'next_vp_paths': None,
            's_time': '2026-02-01 00:00:00',
            'e_time': None,
            'duration': None,
        },
        {
            **COMMON_DETAIL_SCHEMA,
            'outage_prefix': str,
            'attacked_as': str,
            'attacked_as_name': str,
            'attacked_org': str,
            'attacked_country': str,
            'as_type': str,
            'as_descr': (str, type(None)),
            'as_admin': (str, type(None)),
            'pre_vp_paths': dict,
            'eve_vp_paths': dict,
            'next_vp_paths': list,
        },
        'prefix_outage_202602',
        'prefix',
        '1.2.3.0/24',
        id='前缀中断',
    ),
    pytest.param(
        'as_outage',
        'get_as_outage_de',
        '64512',
        {
            'as_name': '受影响网络',
            'org_name': '受影响机构',
            'country': '中国',
            'total_prefix_num': 120,
            'max_outage_prefix_num': 18,
            'outage_prefixes': ['1.2.3.0/24'],
            'outage_level': 'middle',
            'outage_level_descr': '中风险',
            'event_info': 'AS 路由集中撤回',
            'as_type': 'ISP',
            'as_descr': None,
            'as_admin': None,
            'pre_vp_paths': {'2026-01-31 23:55:00': ['64500 64512']},
            'eve_vp_paths': {'2026-02-01 00:00:00': []},
            'next_vp_paths': None,
            's_time': '2026-02-01 00:00:00',
            'e_time': None,
            'duration': None,
        },
        {
            **COMMON_DETAIL_SCHEMA,
            'outage_as': str,
            'attacked_as': str,
            'attacked_as_name': str,
            'attacked_org': str,
            'attacked_country': str,
            'total_prefix_num': int,
            'outage_prefix_num': int,
            'outage_prefixes': list,
            'as_type': str,
            'as_descr': (str, type(None)),
            'as_admin': (str, type(None)),
            'pre_vp_paths': dict,
            'eve_vp_paths': dict,
            'next_vp_paths': list,
        },
        'as_outage_202602',
        'asn',
        '64512',
        id='AS中断',
    ),
    pytest.param(
        'country_outage',
        'get_country_outage_de',
        'CN',
        {
            'country_chinese_name': '中国',
            'total_as_num': 900,
            'max_outage_as_num': 12,
            'outage_ases': ['64512', '64513'],
            'outage_level': 'low',
            'outage_level_descr': '低风险',
            'event_info': '国家级连通性下降',
            's_time': '2026-02-01 00:00:00',
            'e_time': None,
            'duration': None,
        },
        {
            **COMMON_DETAIL_SCHEMA,
            'outage_country': str,
            'attacked_country': str,
            'total_as_num': int,
            'outage_as_num': int,
            'outage_ases': list,
        },
        'country_outage_202602',
        'country',
        'CN',
        id='国家中断',
    ),
    pytest.param(
        'hijack',
        'get_hijack_de',
        '1.2.3.0-24',
        {
            'hijacked_as': '64512',
            'hijacked_as_name': '受影响网络',
            'hijacked_as_org': '受影响机构',
            'hijacked_as_country': '中国',
            'hijacked_as_descr': None,
            'hijacked_as_admin': None,
            'hijacker_as': '64513',
            'hijacker_as_name': '异常来源',
            'hijacker_as_org': '来源机构',
            'hijacker_as_country': '美国',
            'hijacker_as_descr': None,
            'hijacker_as_admin': None,
            'hijack_level': 'high',
            'hijack_level_info': '高风险',
            'event_info': '检测到源 AS 变化',
            'pre_vp_paths': {'2026-01-31 23:55:00': ['64500 64512']},
            'eve_vp_paths': {'2026-02-01 00:00:00': ['64500 64513']},
            'next_vp_paths': None,
            's_time': '2026-02-01 00:00:00',
            'e_time': None,
            'duration': None,
        },
        {
            **COMMON_DETAIL_SCHEMA,
            'hijacked_prefix': str,
            'attacked_as': str,
            'attacked_as_name': str,
            'attacked_org': str,
            'attacked_country': str,
            'attacked_as_descr': (str, type(None)),
            'attacked_as_admin': (str, type(None)),
            'attacker_as': str,
            'attacker_as_name': str,
            'attacker_org': str,
            'attacker_country': str,
            'attacker_as_descr': (str, type(None)),
            'attacker_as_admin': (str, type(None)),
            'pre_vp_paths': dict,
            'eve_vp_paths': dict,
            'next_vp_paths': list,
        },
        'hijack_202602',
        'prefix',
        '1.2.3.0/24',
        id='前缀劫持',
    ),
    pytest.param(
        'sub_hijack',
        'get_sub_hijack_de',
        '1.2.3.0-25',
        {
            'hijacked_prefix': '1.2.3.0/24',
            'hijacked_as': '["64512"]',
            'hijacked_as_name': '受影响网络',
            'hijacked_as_org': '受影响机构',
            'hijacked_as_country': '中国',
            'hijacked_as_descr': None,
            'hijacked_as_admin': None,
            'hijacker_as': '["64513"]',
            'hijacker_as_name': '异常来源',
            'hijacker_as_org': '来源机构',
            'hijacker_as_country': '美国',
            'hijacker_as_descr': None,
            'hijacker_as_admin': None,
            'sub_hijack_level': 'high',
            'level_info': '高风险',
            'event_info': '检测到更具体前缀异常',
            's_time': '2026-02-01 00:00:00',
            'e_time': None,
            'duration': None,
        },
        {
            **COMMON_DETAIL_SCHEMA,
            'hijacker_prefix': str,
            'hijacked_prefix': str,
            'attacked_as': str,
            'attacked_ases': list,
            'attacked_as_name': str,
            'attacked_org': str,
            'attacked_country': str,
            'attacked_as_descr': (str, type(None)),
            'attacked_as_admin': (str, type(None)),
            'attacker_as': str,
            'attacker_ases': list,
            'attacker_as_name': str,
            'attacker_org': str,
            'attacker_country': str,
            'attacker_as_descr': (str, type(None)),
            'attacker_as_admin': (str, type(None)),
        },
        'sub_hijack_202602',
        'prefix',
        '1.2.3.0/25',
        id='子前缀劫持',
    ),
    pytest.param(
        'leak',
        'get_leak_de',
        '1.2.3.0-24',
        {
            'prefix_ori_as': '64512',
            'ori_as_name': '受影响网络',
            'ori_as_org': '受影响机构',
            'ori_as_country': '中国',
            'leak_by': '64513',
            'leak_by_name': '泄漏来源',
            'leak_by_org': '来源机构',
            'leak_by_country': '美国',
            'leak_to': '64514',
            'leak_to_name': '传播目标',
            'leak_to_org': '目标机构',
            'leak_to_country': '德国',
            'as_path': '64512 64513 64514',
            'leak_level': 'middle',
            'leak_level_info': '中风险',
            'event_info': '检测到异常传播路径',
            's_time': '2026-02-01 00:00:00',
        },
        {
            **COMMON_DETAIL_SCHEMA,
            'leak_prefix': str,
            'attacked_as': str,
            'attacked_as_name': str,
            'attacked_org': str,
            'attacked_country': str,
            'attacker_as': str,
            'attacker_as_name': str,
            'attacker_org': str,
            'attacker_country': str,
            'leak_to': str,
            'leak_to_name': str,
            'leak_to_org': str,
            'leak_to_country': str,
            'as_path': str,
        },
        'leak_event_202602',
        'prefix',
        '1.2.3.0/24',
        id='路由泄漏',
    ),
]


def test_event_list_contract_and_query_params(client, assert_contract):
    with patch('services.events_service.get_event', return_value=[EVENT_ROW]) as query, \
         patch('services.events_service.get_total_page', return_value=(1, 1)):
        response = client.get(
            '/api/v1/events',
            query_string={
                'page_num': '1',
                'page_size': '50',
                'event_type': '前缀劫持',
                'date': '2026-02-01_2026-02-02',
            },
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert_contract(payload, {'total_page': int, 'record_count': str, 'data': list})
    assert len(payload['data']) == 1
    assert_contract(payload['data'][0], EVENT_ITEM_SCHEMA)
    assert payload['data'][0]['event_type'] in CORE_EVENT_TYPES
    assert payload['data'][0]['level'] in EVENT_LEVELS
    assert payload['data'][0]['state'] in EVENT_STATES
    assert payload['record_count'] == '1'
    assert payload['data'][0]['event_type'] == '前缀劫持'
    assert query.call_args.kwargs['page_num'] == 1
    assert query.call_args.kwargs['page_size'] == 50
    assert query.call_args.kwargs['start_time'] == '2026-02-01 00:00:00'
    assert query.call_args.kwargs['end_time'] == '2026-02-02 23:59:59'
    assert query.call_args.kwargs['state'] is None


def test_top_events_contract(client, assert_contract):
    with patch(
        'services.events_service.get_top_event',
        return_value=pd.DataFrame([EVENT_ROW]),
    ) as query:
        response = client.get(
            '/api/v1/events/top',
            query_string={'event_type': '["前缀劫持", "边界中断"]'},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert type(payload) is list
    assert len(payload) == 1
    assert_contract(payload[0], EVENT_ITEM_SCHEMA)
    assert payload[0]['event_type'] in CORE_EVENT_TYPES
    assert payload[0]['level'] in EVENT_LEVELS
    assert payload[0]['state'] in EVENT_STATES
    assert query.call_args.kwargs['event_type'] == ('前缀劫持',)


@pytest.mark.parametrize(
    ('event_type', 'query_name', 'problem', 'row', 'schema', 'table', 'identifier', 'value'),
    DETAIL_CASES,
)
def test_six_event_detail_contracts(
    client,
    assert_contract,
    event_type,
    query_name,
    problem,
    row,
    schema,
    table,
    identifier,
    value,
):
    start_time = '2026-02-01 00:00:00'
    path = '/api/v1/{}/{}/{}/7/r'.format(
        event_type,
        quote(start_time, safe=''),
        quote(problem, safe=''),
    )
    with patch(f'services.events_service.{query_name}', return_value=[row]) as query:
        response = client.get(path)

    assert response.status_code == 200
    payload = response.get_json()
    assert_contract(payload, schema)
    assert payload['event_level'] in EVENT_LEVELS
    assert payload['start_time'] == start_time
    assert payload['end_time'] == ''
    assert payload['duration'] == ''
    assert query.call_args.kwargs['table'] == table
    assert query.call_args.kwargs[identifier] == value
    assert query.call_args.kwargs['source'] == 'r'


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
def test_six_core_details_return_empty_for_missing_fact(client, event_type, query_name, problem):
    start_time = quote('2026-02-01 00:00:00', safe='')
    with patch(f'services.events_service.{query_name}', return_value=[]):
        response = client.get(f'/api/v1/{event_type}/{start_time}/{problem}/7/r')

    assert response.status_code == 200
    assert response.get_json() == {}


def test_unknown_detail_type_keeps_empty_object_behavior(client):
    start_time = quote('2026-02-01 00:00:00', safe='')
    response = client.get(f'/api/v1/unknown/{start_time}/target/7/r')

    assert response.status_code == 200
    assert response.get_json() == {}


def test_detail_route_rejects_non_integer_event_id(client):
    start_time = quote('2026-02-01 00:00:00', safe='')
    response = client.get(f'/api/v1/hijack/{start_time}/1.2.3.0-24/not-a-number/r')

    assert response.status_code == 404


def test_event_list_service_keeps_invalid_paging_defaults():
    params = {'page_num': 'invalid', 'page_size': '999'}
    with patch('services.events_service.get_event', return_value=[]) as query, \
         patch('services.events_service.get_total_page', return_value=(0, 0)), \
         patch('services.events_service.deal_event', return_value=[]):
        result = events_service.get_event_list_data(params, conn=object())

    assert result == {'total_page': 0, 'record_count': '0', 'data': []}
    assert query.call_args.kwargs['page_num'] == 1
    assert query.call_args.kwargs['page_size'] == 10


def test_event_evidence_bundle_has_stable_ids_phase_semantics_and_quality_limits(client):
    start_time = quote('2026-02-01 00:00:00', safe='')
    path = f'/api/v1/events/evidence-bundle/hijack/{start_time}/1.2.3.0-24/7/r'

    with patch('services.events_service.get_hijack_de', return_value=[EVIDENCE_HIJACK_ROW]):
        first = client.get(path)
        second = client.get(path)

    assert first.status_code == 200
    assert second.status_code == 200
    payload = first.get_json()
    repeated = second.get_json()

    assert payload['bundle_version'] == 'evidence_bundle_v1'
    assert payload['incident_id'].startswith('inc_v1_')
    assert repeated['incident_id'] == payload['incident_id']
    assert payload['event']['event_time_local'] == '2026-02-01T00:00:00+08:00'
    assert payload['event']['event_time_utc'] == '2026-01-31T16:00:00Z'
    assert payload['event']['end_time_utc'] == '2026-01-31T16:03:00Z'
    assert payload['event']['source_timezone'] == 'Asia/Shanghai'

    coverage = payload['phase_coverage']
    assert coverage['before']['status'] == 'observed_paths'
    assert coverage['during']['status'] == 'observed_no_path'
    assert coverage['after']['status'] == 'observed_paths'
    assert payload['data_quality']['observed_phase_count'] == 3
    assert payload['data_quality']['expected_phase_count'] == 3
    assert payload['data_quality']['vantage_point_identity_available'] is False
    assert payload['data_quality']['raw_bgp_message_available'] is False
    assert payload['assessment']['classification'] == 'observation_only'
    assert payload['assessment']['causal_conclusion'] is None
    assert payload['assessment']['counterevidence']
    assert any('不证明全网恢复' in item for item in payload['assessment']['counterevidence'])

    evidence_ids = [item['evidence_id'] for item in payload['evidence_items']]
    repeated_ids = [item['evidence_id'] for item in repeated['evidence_items']]
    assert evidence_ids == repeated_ids
    assert len(evidence_ids) == len(set(evidence_ids))
    assert all(item_id.startswith('ev_v1_') for item_id in evidence_ids)
    route_items = [
        item for item in payload['evidence_items']
        if item['kind'] == 'route_observation'
    ]
    assert route_items
    assert all(item['semantics'] == 'route_observation_not_causal_trace' for item in route_items)


def test_event_evidence_bundle_returns_404_when_fact_record_is_missing(client):
    start_time = quote('2026-02-01 00:00:00', safe='')
    path = f'/api/v1/events/evidence-bundle/hijack/{start_time}/1.2.3.0-24/7/r'

    with patch('services.events_service.get_hijack_de', return_value=[]):
        response = client.get(path)

    assert response.status_code == 404
    assert response.get_json() == {
        'status': False,
        'msg': '业务事实表中未找到该事件记录',
    }
