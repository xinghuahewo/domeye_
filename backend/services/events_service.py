"""核心异常事件查询服务。

这里只组装事件总表和六类事实表中已经存在的数据。域名、报告、人工研判、
国家拓扑等增强能力不属于精简版首期范围，也不会在模块导入时加载大文件。
"""

import ast
import datetime
import json

from dateutil.relativedelta import relativedelta

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
    return parts[0], parts[1] if len(parts) > 1 else None


def _event_table_names(now=None):
    current_time = now or datetime.datetime.now()
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
    event_table, last_month_table = _event_table_names(now=now)
    rows = get_top_event(
        conn=conn,
        last_month_table=last_month_table,
        event_table=event_table,
        country='is_domestic',
        page_size=page_size,
        event_type=_parse_event_types(event_type_str),
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
