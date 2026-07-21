"""核心异常事件查询服务。

这里只组装事件总表和六类事实表中已经存在的数据。域名、报告、人工研判、
国家拓扑等增强能力不属于精简版首期范围，也不会在模块导入时加载大文件。
"""

import ast
import datetime
import hashlib
import json
import re
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta

from config.data_window import resolve_query_now
from config.database import conn_11, conn_13, conn_15
from database.as_outage import get_as_outage_de
from database.country_outage import get_country_outage_de
from database.hijack import get_hijack_de
from database.leak_event import get_leak_de
from database.prefix_outage import get_pre_outage_de
from database.sub_hijack import get_sub_hijack_de
from utils.get_event import (
    deal_event,
    deal_top_event,
    get_event,
    get_top_event,
    get_total_page,
)


CORE_EVENT_TYPES = (
    '前缀劫持',
    '子前缀劫持',
    '前缀中断',
    'AS中断',
    '国家中断',
    '路由泄漏',
)

EVENT_KIND_LABELS = {
    'hijack': '前缀劫持',
    'sub_hijack': '子前缀劫持',
    'prefix_outage': '前缀中断',
    'as_outage': 'AS中断',
    'country_outage': '国家中断',
    'leak': '路由泄漏',
}

EVENT_FACT_TABLES = {
    'hijack': 'hijack',
    'sub_hijack': 'sub_hijack',
    'prefix_outage': 'prefix_outage',
    'as_outage': 'as_outage',
    'country_outage': 'country_outage',
    'leak': 'leak_event',
}

BUSINESS_TIMEZONE = ZoneInfo('Asia/Shanghai')
UTC = datetime.timezone.utc


def _parse_page_size(raw_value):
    return int(raw_value) if raw_value in ['10', '50', '100', '200'] else 10


def _parse_page_num(raw_value):
    if raw_value in [None, ''] or str(raw_value).startswith('0'):
        return 1
    return int(raw_value) if str(raw_value).isdigit() else 1


def _parse_date_range(raw_value):
    if not raw_value:
        return None, None
    parts = raw_value.split('_', 1)

    def boundary(value, end_of_day=False):
        if value is None:
            return None
        try:
            parsed = datetime.datetime.strptime(value, '%Y-%m-%d')
        except ValueError:
            return value
        if end_of_day:
            parsed = parsed.replace(hour=23, minute=59, second=59)
        return parsed.strftime('%Y-%m-%d %H:%M:%S')

    return boundary(parts[0]), boundary(parts[1], end_of_day=True) if len(parts) > 1 else None


def _event_table_names(now=None):
    current_time = resolve_query_now(now)
    current_table = 'event_table_{}'.format(current_time.strftime('%Y%m'))
    last_month = current_time.date() - relativedelta(months=1)
    return current_table, 'event_table_{}'.format(last_month.strftime('%Y%m'))


def _year_month(start_time):
    return start_time[0:4], start_time[5:7]


def _safe_list(value):
    if value in [None, '']:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(value)
            except (TypeError, ValueError, SyntaxError, json.JSONDecodeError):
                continue
            if isinstance(parsed, (list, tuple, set)):
                return list(parsed)
        return [value]
    return [value]


def _parse_event_types(raw_value):
    if not raw_value:
        return CORE_EVENT_TYPES
    values = _safe_list(raw_value)
    if len(values) == 1 and isinstance(values[0], str) and ',' in values[0]:
        values = [item.strip() for item in values[0].split(',')]
    selected = tuple(item for item in values if item in CORE_EVENT_TYPES)
    return selected or CORE_EVENT_TYPES


def _first_row(rows):
    return rows[0] if rows else None


def _row_value(row, key, default=None):
    try:
        return row[key]
    except (KeyError, TypeError):
        return default


def _base_times(row):
    return {
        'start_time': str(row['s_time']),
        'end_time': str(_row_value(row, 'e_time')) if _row_value(row, 'e_time') is not None else '',
        'duration': str(_row_value(row, 'duration')) if _row_value(row, 'duration') is not None else '',
    }


def _get_prefix_outage_detail(start_time, problem, event_id, source):
    year, month = _year_month(start_time)
    prefix = problem.replace('-', '/')
    row = _first_row(get_pre_outage_de(
        conn=conn_15,
        table=f'prefix_outage_{year}{month}',
        prefix=prefix,
        outage_id=event_id,
        source=source,
    ))
    if row is None:
        return {}
    return {
        'outage_prefix': prefix,
        'attacked_as': row['asn'],
        'attacked_as_name': row['as_name'],
        'attacked_org': row['org_name'],
        'attacked_country': row['country'],
        'event_level': row['outage_level'],
        'event_descr': row['outage_level_descr'],
        'event_info': row['event_info'],
        'as_type': row['as_type'],
        'as_descr': _row_value(row, 'as_descr'),
        'as_admin': _row_value(row, 'as_admin'),
        'pre_vp_paths': row['pre_vp_paths'],
        'eve_vp_paths': row['eve_vp_paths'],
        'next_vp_paths': row['next_vp_paths'] or [],
        **_base_times(row),
    }


def _get_as_outage_detail(start_time, problem, event_id, source):
    year, month = _year_month(start_time)
    row = _first_row(get_as_outage_de(
        conn=conn_15,
        table=f'as_outage_{year}{month}',
        asn=problem,
        outage_id=event_id,
        source=source,
    ))
    if row is None:
        return {}
    return {
        'outage_as': problem,
        'attacked_as': problem,
        'attacked_as_name': row['as_name'],
        'attacked_org': row['org_name'],
        'attacked_country': row['country'],
        'total_prefix_num': row['total_prefix_num'],
        'outage_prefix_num': row['max_outage_prefix_num'],
        'outage_prefixes': row['outage_prefixes'] or [],
        'event_level': row['outage_level'],
        'event_descr': row['outage_level_descr'],
        'event_info': row['event_info'],
        'as_type': row['as_type'],
        'as_descr': _row_value(row, 'as_descr'),
        'as_admin': _row_value(row, 'as_admin'),
        'pre_vp_paths': row['pre_vp_paths'],
        'eve_vp_paths': row['eve_vp_paths'],
        'next_vp_paths': row['next_vp_paths'] or [],
        **_base_times(row),
    }


def _get_country_outage_detail(start_time, problem, event_id, source):
    year, month = _year_month(start_time)
    row = _first_row(get_country_outage_de(
        conn=conn_15,
        table=f'country_outage_{year}{month}',
        country=problem,
        outage_id=event_id,
        source=source,
    ))
    if row is None:
        return {}
    country_name = row['country_chinese_name']
    return {
        'outage_country': country_name,
        'attacked_country': country_name,
        'total_as_num': row['total_as_num'],
        'outage_as_num': row['max_outage_as_num'],
        'outage_ases': row['outage_ases'] or [],
        'event_level': row['outage_level'],
        'event_descr': row['outage_level_descr'],
        'event_info': row['event_info'],
        **_base_times(row),
    }


def _get_hijack_detail(start_time, problem, event_id, source):
    year, month = _year_month(start_time)
    prefix = problem.replace('-', '/')
    row = _first_row(get_hijack_de(
        conn=conn_11,
        table=f'hijack_{year}{month}',
        prefix=prefix,
        hijack_id=event_id,
        source=source,
    ))
    if row is None:
        return {}
    return {
        'hijacked_prefix': prefix,
        'attacked_as': row['hijacked_as'],
        'attacked_as_name': row['hijacked_as_name'],
        'attacked_org': row['hijacked_as_org'],
        'attacked_country': row['hijacked_as_country'],
        'attacked_as_descr': _row_value(row, 'hijacked_as_descr'),
        'attacked_as_admin': _row_value(row, 'hijacked_as_admin'),
        'attacker_as': row['hijacker_as'],
        'attacker_as_name': row['hijacker_as_name'],
        'attacker_org': row['hijacker_as_org'],
        'attacker_country': row['hijacker_as_country'],
        'attacker_as_descr': _row_value(row, 'hijacker_as_descr'),
        'attacker_as_admin': _row_value(row, 'hijacker_as_admin'),
        'event_level': row['hijack_level'],
        'event_descr': row['hijack_level_info'],
        'event_info': row['event_info'],
        'pre_vp_paths': row['pre_vp_paths'],
        'eve_vp_paths': row['eve_vp_paths'],
        'next_vp_paths': row['next_vp_paths'] or [],
        **_base_times(row),
    }


def _get_sub_hijack_detail(start_time, problem, event_id, source):
    year, month = _year_month(start_time)
    prefix = problem.replace('-', '/')
    row = _first_row(get_sub_hijack_de(
        conn=conn_11,
        table=f'sub_hijack_{year}{month}',
        prefix=prefix,
        sub_hijack_id=event_id,
        source=source,
    ))
    if row is None:
        return {}
    attacked_ases = _safe_list(row['hijacked_as'])
    attacker_ases = _safe_list(row['hijacker_as'])
    return {
        'hijacker_prefix': prefix,
        'hijacked_prefix': row['hijacked_prefix'],
        'attacked_as': row['hijacked_as'],
        'attacked_ases': attacked_ases,
        'attacked_as_name': row['hijacked_as_name'],
        'attacked_org': row['hijacked_as_org'],
        'attacked_country': row['hijacked_as_country'],
        'attacked_as_descr': _row_value(row, 'hijacked_as_descr'),
        'attacked_as_admin': _row_value(row, 'hijacked_as_admin'),
        'attacker_as': row['hijacker_as'],
        'attacker_ases': attacker_ases,
        'attacker_as_name': row['hijacker_as_name'],
        'attacker_org': row['hijacker_as_org'],
        'attacker_country': row['hijacker_as_country'],
        'attacker_as_descr': _row_value(row, 'hijacker_as_descr'),
        'attacker_as_admin': _row_value(row, 'hijacker_as_admin'),
        'event_level': row['sub_hijack_level'],
        'event_descr': row['level_info'],
        'event_info': row['event_info'],
        **_base_times(row),
    }


def _get_leak_detail(start_time, problem, event_id, source):
    year, month = _year_month(start_time)
    prefix = problem.replace('-', '/')
    row = _first_row(get_leak_de(
        conn=conn_13,
        table=f'leak_event_{year}{month}',
        prefix=prefix,
        leak_id=event_id,
        source=source,
    ))
    if row is None:
        return {}
    return {
        'leak_prefix': prefix,
        'attacked_as': row['prefix_ori_as'],
        'attacked_as_name': row['ori_as_name'],
        'attacked_org': row['ori_as_org'],
        'attacked_country': row['ori_as_country'],
        'attacker_as': row['leak_by'],
        'attacker_as_name': row['leak_by_name'],
        'attacker_org': row['leak_by_org'],
        'attacker_country': row['leak_by_country'],
        'leak_to': row['leak_to'],
        'leak_to_name': row['leak_to_name'],
        'leak_to_org': row['leak_to_org'],
        'leak_to_country': row['leak_to_country'],
        'as_path': row['as_path'],
        'event_level': row['leak_level'],
        'event_descr': row['leak_level_info'],
        'event_info': row['event_info'],
        'start_time': str(row['s_time']),
        'end_time': '',
        'duration': '',
    }


def _structured_value(value):
    if value in [None, '']:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return value
    if isinstance(value, str):
        for loader in (json.loads, ast.literal_eval):
            try:
                return loader(value)
            except (TypeError, ValueError, SyntaxError, json.JSONDecodeError):
                continue
    return value


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    )


def _stable_id(prefix, value):
    digest = hashlib.sha256(_canonical_json(value).encode('utf-8')).hexdigest()
    return '{}{}'.format(prefix, digest[:24])


def _business_timestamp(value):
    if value in [None, '']:
        return None, None
    text = re.sub(r'\s+', ' ', str(value)).strip()
    parsed = None
    for candidate in (text, text.replace('Z', '+00:00')):
        try:
            parsed = datetime.datetime.fromisoformat(candidate)
            break
        except ValueError:
            continue
    if parsed is None:
        return text, None
    if parsed.tzinfo is None:
        local = parsed.replace(tzinfo=BUSINESS_TIMEZONE)
    else:
        local = parsed.astimezone(BUSINESS_TIMEZONE)
    utc = local.astimezone(UTC)
    return local.isoformat(timespec='seconds'), utc.isoformat(timespec='seconds').replace('+00:00', 'Z')


def _path_text(value):
    if isinstance(value, (list, tuple)):
        return ' '.join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        candidate = value.get('path', value.get('as_path', value.get('route')))
        return _path_text(candidate) if candidate is not None else _canonical_json(value)
    return re.sub(r'\s+', ' ', str(value)).strip()


def _path_values(value):
    if value in [None, '']:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    paths = []
    for item in values:
        text = _path_text(item)
        if text:
            paths.append(text)
    return paths


def _route_observations(value, phase, label, source_field, fallback_time):
    structured = _structured_value(value)
    if structured in [None, '', []]:
        return []

    snapshots = []
    if isinstance(structured, dict):
        entries = sorted(structured.items(), key=lambda item: str(item[0]))
    else:
        entries = [(fallback_time, structured)]

    for observed_at, paths_value in entries:
        paths = _path_values(paths_value)
        local_time, utc_time = _business_timestamp(observed_at or fallback_time)
        identity = {
            'phase': phase,
            'source_field': source_field,
            'observed_at': local_time,
            'paths': paths,
        }
        snapshots.append({
            'evidence_id': _stable_id('ev_v1_', identity),
            'phase': phase,
            'kind': 'route_observation',
            'label': label,
            'source_field': source_field,
            'observed_at_local': local_time,
            'observed_at_utc': utc_time,
            'observation_state': 'paths_observed' if paths else 'no_path_in_snapshot',
            'path_count': len(paths),
            'paths': paths,
            'observer_identity': 'not_retained',
            'semantics': 'route_observation_not_causal_trace',
        })
    return snapshots


def _affected_object_evidence(detail, incident_id):
    candidates = (
        ('outage_prefixes', '受影响前缀集合'),
        ('outage_ases', '受影响 AS 集合'),
        ('attacked_ases', '受影响 AS 集合'),
    )
    items = []
    seen = set()
    for field, label in candidates:
        values = [str(item) for item in _safe_list(detail.get(field)) if str(item).strip()]
        if not values:
            continue
        identity = {'incident_id': incident_id, 'field': field, 'objects': values}
        evidence_id = _stable_id('ev_v1_', identity)
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        items.append({
            'evidence_id': evidence_id,
            'phase': 'context',
            'kind': 'affected_object_set',
            'label': label,
            'source_field': field,
            'object_count': len(values),
            'objects': values,
            'semantics': 'fact_table_affected_object_set',
        })
    return items


def _phase_coverage(route_items, phase):
    items = [item for item in route_items if item['phase'] == phase]
    path_count = sum(item['path_count'] for item in items)
    if not items:
        status = 'not_available'
    elif path_count:
        status = 'observed_paths'
    else:
        status = 'observed_no_path'
    return {
        'status': status,
        'snapshot_count': len(items),
        'path_count': path_count,
        'evidence_ids': [item['evidence_id'] for item in items],
    }


def _event_object(detail, fallback):
    for key in (
        'hijacker_prefix', 'hijacked_prefix', 'outage_prefix', 'leak_prefix',
        'outage_as', 'outage_country', 'attacked_as', 'attacked_country',
    ):
        value = detail.get(key)
        if value not in [None, '']:
            return str(value)
    return fallback.replace('-', '/')


def get_event_evidence_bundle_data(event_type, start_time, problem, event_id, source):
    """基于六类业务事实表返回确定性的只读证据包。"""

    if event_type not in EVENT_KIND_LABELS:
        return None
    detail = get_event_detail_data(
        event_type=event_type,
        start_time=start_time,
        problem=problem,
        event_id=event_id,
        source=source,
    )
    if not detail:
        return None

    canonical_reference = '{}/{}/{}/{}/{}'.format(
        event_type, start_time, problem, event_id, source,
    )
    incident_identity = {
        'schema': 'incident_id_v1',
        'event_type': event_type,
        'start_time': start_time,
        'problem': problem,
        'event_id': int(event_id),
        'source': source,
    }
    incident_id = _stable_id('inc_v1_', incident_identity)

    event_start_local, event_start_utc = _business_timestamp(detail.get('start_time') or start_time)
    event_end_local, event_end_utc = _business_timestamp(detail.get('end_time'))
    snapshot_local, snapshot_utc = _business_timestamp(resolve_query_now())

    fact_table = '{}_{}{}'.format(EVENT_FACT_TABLES[event_type], start_time[0:4], start_time[5:7])
    source_record = {
        'source_system': 'Domeye business fact table',
        'source_table': fact_table,
        'source_code': source,
        'record_locator': {
            'problem': problem,
            'event_id': int(event_id),
            'start_time': start_time,
        },
        'detail_reference': canonical_reference,
    }
    fact_identity = {
        'incident_id': incident_id,
        'source_record': source_record,
        'fact_record': detail,
    }
    evidence_items = [{
        'evidence_id': _stable_id('ev_v1_', fact_identity),
        'phase': 'context',
        'kind': 'fact_record',
        'label': '业务事实表原始记录',
        'source_field': 'fact_record',
        'observed_at_local': event_start_local,
        'observed_at_utc': event_start_utc,
        'field_count': len(detail),
        'semantics': 'detector_fact_record',
    }]

    route_items = []
    route_items.extend(_route_observations(
        detail.get('pre_vp_paths'), 'before', '异常前可见路径快照',
        'pre_vp_paths', detail.get('start_time') or start_time,
    ))
    route_items.extend(_route_observations(
        detail.get('eve_vp_paths'), 'during', '异常期间路径快照',
        'eve_vp_paths', detail.get('start_time') or start_time,
    ))
    route_items.extend(_route_observations(
        detail.get('next_vp_paths'), 'after', '异常后路径快照',
        'next_vp_paths', detail.get('end_time') or detail.get('start_time') or start_time,
    ))
    if detail.get('as_path') not in [None, '']:
        route_items.extend(_route_observations(
            detail.get('as_path'), 'during', '事件记录 AS_PATH 快照',
            'as_path', detail.get('start_time') or start_time,
        ))
    evidence_items.extend(route_items)
    evidence_items.extend(_affected_object_evidence(detail, incident_id))

    phase_coverage = {
        phase: _phase_coverage(route_items, phase)
        for phase in ('before', 'during', 'after')
    }
    observed_phase_count = sum(
        item['status'] != 'not_available' for item in phase_coverage.values()
    )

    supports = []
    counterevidence = []
    if (
        phase_coverage['before']['path_count'] > 0
        and phase_coverage['during']['status'] == 'observed_no_path'
    ):
        supports.append('异常前存在可见路径、异常期间快照未保留可见路径，支持“观测可见性下降”的描述。')
    if phase_coverage['after']['path_count'] > 0:
        counterevidence.append('异常后重新观测到可见路径，是“持续不可见”假设的反证，但不证明全网恢复。')

    gaps = [
        '当前事实字段未保留观测点身份，无法量化或复核 VP 覆盖范围。',
        '当前证据包未附原始 BGP 报文，无法进行逐报文重放。',
        '路径快照只能说明被观测到的路径状态，不能单独证明异常根因。',
    ]
    for phase, label in (('before', '异常前'), ('during', '异常期间'), ('after', '异常后')):
        if phase_coverage[phase]['status'] == 'not_available':
            gaps.append('{}路径快照缺失；这表示证据不可用，不表示该阶段没有路径。'.format(label))

    limitations = [
        'Route Observation / Path Snapshot 不是因果链路或根因证据。',
        '无时区业务时间按数据画像 Asia/Shanghai 解释，并派生 UTC 时间。',
        '异常后路径快照仅表示后续可见性观测，不等同于全网恢复确认。',
        '阶段字段缺失表示当前事实记录未保留该证据，不能解释为网络状态缺失。',
    ]

    return {
        'bundle_version': 'evidence_bundle_v1',
        'incident_id': incident_id,
        'incident_id_schema': 'incident_id_v1',
        'event': {
            'kind': event_type,
            'label': EVENT_KIND_LABELS[event_type],
            'object': _event_object(detail, problem),
            'level': str(detail.get('event_level') or ''),
            'summary': str(detail.get('event_info') or detail.get('event_descr') or ''),
            'duration': str(detail.get('duration') or ''),
            'event_time_local': event_start_local,
            'event_time_utc': event_start_utc,
            'end_time_local': event_end_local,
            'end_time_utc': event_end_utc,
            'source_timezone': 'Asia/Shanghai',
        },
        'data_snapshot': {
            'snapshot_time_local': snapshot_local,
            'snapshot_time_utc': snapshot_utc,
            'timezone': 'Asia/Shanghai',
        },
        'source_record': source_record,
        'phase_coverage': phase_coverage,
        'evidence_items': evidence_items,
        'assessment': {
            'classification': 'observation_only',
            'supports': supports,
            'counterevidence': counterevidence,
            'gaps': gaps,
            'causal_conclusion': None,
        },
        'data_quality': {
            'observed_phase_count': observed_phase_count,
            'expected_phase_count': 3,
            'route_observation_count': len(route_items),
            'evidence_item_count': len(evidence_items),
            'vantage_point_identity_available': False,
            'raw_bgp_message_available': False,
            'timezone_semantics': 'timestamp_without_time_zone interpreted as Asia/Shanghai',
            'limitations': limitations,
        },
        'fact_record': detail,
    }


def get_event_list_data(params, conn=conn_11):
    page_size = _parse_page_size(params.get('page_size'))
    page_num = _parse_page_num(params.get('page_num'))
    start_time, end_time = _parse_date_range(params.get('datetime') or params.get('date'))
    shared = {
        'conn': conn,
        'page_size': page_size,
        'source': params.get('source', 'r'),
        'level': params.get('level'),
        'event_type': params.get('event_type'),
        'country': params.get('country'),
        'attacker_as': params.get('attacker_as'),
        'attacked_as': params.get('attacked_as'),
        'attacker_org': params.get('attacker_org'),
        'attacked_org': params.get('attacked_org'),
        'attacker_country': params.get('attacker_country'),
        'attacked_country': params.get('attacked_country'),
        'event_info': params.get('event_info'),
        'start_time': start_time,
        'end_time': end_time,
        'state': None,
        'judge_reason': None,
        'judge_userid': None,
        'judge_username': None,
        'judge_time': None,
        'notify_userid': None,
        'notify_username': None,
        'notify_time': None,
    }
    rows = get_event(
        page_num=page_num,
        sort_mode=params.get('sort_mode'),
        **shared,
    )
    total_page, record_count = get_total_page(**shared)
    return {
        'total_page': total_page,
        'record_count': str(record_count),
        'data': deal_event(event_rows=rows),
    }


def get_top_event_items(event_type_str=None, conn=conn_11, now=None, page_size=10):
    effective_now = resolve_query_now(now)
    event_table, last_month_table = _event_table_names(now=effective_now)
    rows = get_top_event(
        conn=conn,
        last_month_table=last_month_table,
        event_table=event_table,
        country='is_domestic',
        page_size=page_size,
        event_type=_parse_event_types(event_type_str),
        now=effective_now,
    )
    return deal_top_event(top_event_rows=rows)


def get_event_detail_data(event_type, start_time, problem, event_id, source, query_params=None):
    handlers = {
        'prefix_outage': _get_prefix_outage_detail,
        'as_outage': _get_as_outage_detail,
        'country_outage': _get_country_outage_detail,
        'hijack': _get_hijack_detail,
        'sub_hijack': _get_sub_hijack_detail,
        'leak': _get_leak_detail,
    }
    handler = handlers.get(event_type)
    if handler is None:
        return {}
    return handler(start_time, problem, event_id, source)
