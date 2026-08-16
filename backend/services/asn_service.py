"""优先监测 ASN 的特征、静态信息与异常态势工作台服务。"""

from ast import literal_eval
from collections import Counter, defaultdict
from copy import deepcopy
import datetime
import re
import threading
import time

from config.config import BIG_COUNTRY, FEATURE_OTHER_TABLE
from config.database import conn_11
from database.asn_workbench import (
    get_as_event_counts,
    get_as_exact_event_rows,
    get_as_feature_aggregates,
    get_as_feature_series,
    get_as_sparklines,
)
from utils import data_loader
from utils.get_event import deal_event
from utils.get_other_info import get_as_importance


_ASN_CACHE = {}
_ASN_CACHE_LOCK = threading.Lock()
_ASN_CACHE_TTL_SECONDS = 30
_ASN_CACHE_MAX_ENTRIES = 32
_STATIC_PRIORITY_LIMIT = 100
_IMPORTANT_PRIORITY_LIMIT = 50
_ANOMALY_PRIORITY_LIMIT = 50


def _cached_asn_result(key):
    if key is None:
        return None
    now = time.monotonic()
    with _ASN_CACHE_LOCK:
        cached = _ASN_CACHE.get(key)
        if cached is None:
            return None
        created_at, payload = cached
        if now - created_at > _ASN_CACHE_TTL_SECONDS:
            _ASN_CACHE.pop(key, None)
            return None
        return deepcopy(payload)


def _store_asn_result(key, payload):
    if key is None:
        return payload
    now = time.monotonic()
    with _ASN_CACHE_LOCK:
        expired = [
            cache_key
            for cache_key, (created_at, _) in _ASN_CACHE.items()
            if now - created_at > _ASN_CACHE_TTL_SECONDS
        ]
        for cache_key in expired:
            _ASN_CACHE.pop(cache_key, None)
        if len(_ASN_CACHE) >= _ASN_CACHE_MAX_ENTRIES:
            oldest_key = min(_ASN_CACHE, key=lambda item: _ASN_CACHE[item][0])
            _ASN_CACHE.pop(oldest_key, None)
        _ASN_CACHE[key] = (now, deepcopy(payload))
    return payload


def _parse_range(start_time, end_time):
    try:
        start = datetime.datetime.strptime(start_time or '', '%Y-%m-%d %H:%M:%S')
        end = datetime.datetime.strptime(end_time or '', '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None, None, ({'status': False, 'msg': '时间格式错误，应为 YYYY-MM-DD HH:MM:SS'}, 400)
    if start >= end:
        return None, None, ({'status': False, 'msg': '开始时间必须早于结束时间'}, 400)
    if end - start > datetime.timedelta(days=1):
        return None, None, ({'status': False, 'msg': 'ASN 工作台最多支持 24 小时窗口'}, 400)
    return start, end, None


def _parse_limit(raw_value):
    try:
        value = int(raw_value or 6)
    except (TypeError, ValueError):
        value = 6
    return min(12, max(3, value))


def _normalize_asn(value):
    text = str(value or '').strip().upper()
    if text.startswith('AS'):
        text = text[2:]
    return text if text.isdigit() else ''


def _row_value(row, key):
    try:
        return row[key]
    except (KeyError, TypeError):
        return None


def _int_value(value, default=0):
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _nullable_int(value):
    return None if value is None else _int_value(value)


def _float_value(value, default=0.0):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rank_value(value):
    try:
        number = int(float(value))
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _time_value(value):
    if isinstance(value, datetime.datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return str(value) if value else None


def _change_rate(current, previous):
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


def _delta(current, baseline):
    if current is None or baseline is None:
        return None
    return current - baseline


def _asn_values(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        values = []
        for item in value:
            values.extend(_asn_values(item))
        return values
    text = str(value).strip()
    if not text:
        return []
    if text.startswith(('[', '(')):
        try:
            return _asn_values(literal_eval(text))
        except (ValueError, SyntaxError):
            pass
    return re.findall(r'(?i)(?:AS)?(\d+)', text)


def _event_counters(rows):
    counts = Counter()
    high_counts = Counter()
    for row in rows:
        weight = _int_value(_row_value(row, 'event_count'))
        for asn in set(_asn_values(_row_value(row, 'attacked_as'))):
            counts[asn] += weight
            if str(_row_value(row, 'level')).lower() == 'high':
                high_counts[asn] += weight
    return counts, high_counts


def _table_base(asn):
    country = str(data_loader.as_info.get(asn, {}).get('as_country_cn', '')).strip()
    suffix = BIG_COUNTRY.get(country)
    return 'feature_{}'.format(suffix) if suffix else FEATURE_OTHER_TABLE


def _group_asns(asns):
    grouped = defaultdict(list)
    for asn in asns:
        grouped[_table_base(asn)].append(asn)
    return dict(grouped)


def _operational_candidates(pool_asns, anomaly_counts, high_risk_counts, selected_asn):
    """限制真实特征扫描范围，同时保留静态重点、重要 AS 与窗口异常目标。"""

    candidates = list(pool_asns[:_STATIC_PRIORITY_LIMIT])
    important_asns = [
        item
        for item in pool_asns
        if get_as_importance(data_loader.important_as_dict, item)
    ][:_IMPORTANT_PRIORITY_LIMIT]
    anomaly_asns = sorted(
        (item for item in pool_asns if anomaly_counts[item] > 0),
        key=lambda item: (-anomaly_counts[item], -high_risk_counts[item], int(item)),
    )[:_ANOMALY_PRIORITY_LIMIT]
    candidates.extend(important_asns)
    candidates.extend(anomaly_asns)
    if selected_asn:
        candidates.append(selected_asn)
    return list(dict.fromkeys(candidates))


def _feature_point(row):
    return {
        'time': _time_value(_row_value(row, 'time')),
        'announce': _nullable_int(_row_value(row, 'announce')),
        'withdraw': _nullable_int(_row_value(row, 'withdraw')),
        'ipv4_prefixes': _nullable_int(_row_value(row, 'ipv4_prefixes')),
        'ipv6_prefixes': _nullable_int(_row_value(row, 'ipv6_prefixes')),
        'ipv4_addresses': _nullable_int(_row_value(row, 'ipv4_addresses')),
    }


def _static_profile(asn):
    info = data_loader.as_info.get(asn, {})
    org_name = info.get('org_name_cn') or info.get('org_name') or ''
    return {
        'asn': asn,
        'as_name': str(info.get('as_name') or ''),
        'org_name': str(org_name),
        'country': str(info.get('as_country_cn') or ''),
        'as_type': str(info.get('type_cn') or info.get('type') or ''),
        'global_rank': _rank_value(info.get('global_rank')),
        'country_rank': _rank_value(info.get('country_rank')),
        'important': get_as_importance(data_loader.important_as_dict, asn),
    }


def _asn_profile(row, anomaly_count=0, high_risk_count=0):
    asn = _normalize_asn(_row_value(row, 'asn'))
    profile = _static_profile(asn)
    announce = _int_value(_row_value(row, 'announce'))
    withdraw = _int_value(_row_value(row, 'withdraw'))
    previous_announce = _int_value(_row_value(row, 'previous_announce'))
    previous_withdraw = _int_value(_row_value(row, 'previous_withdraw'))
    update_total = announce + withdraw
    previous_update_total = previous_announce + previous_withdraw
    ipv4_prefixes = _nullable_int(_row_value(row, 'ipv4_prefixes'))
    ipv6_prefixes = _nullable_int(_row_value(row, 'ipv6_prefixes'))
    ipv4_addresses = _nullable_int(_row_value(row, 'ipv4_addresses'))
    baseline_ipv4_prefixes = _nullable_int(_row_value(row, 'baseline_ipv4_prefixes'))
    baseline_ipv6_prefixes = _nullable_int(_row_value(row, 'baseline_ipv6_prefixes'))
    baseline_ipv4_addresses = _nullable_int(_row_value(row, 'baseline_ipv4_addresses'))
    ipv4_prefix_change = _delta(ipv4_prefixes, baseline_ipv4_prefixes)
    ipv6_prefix_change = _delta(ipv6_prefixes, baseline_ipv6_prefixes)
    ipv4_address_change = _delta(ipv4_addresses, baseline_ipv4_addresses)
    resource_deltas = [
        abs(value)
        for value in (ipv4_prefix_change, ipv6_prefix_change)
        if value is not None
    ]
    resource_change_rates = [
        abs(value)
        for value in (
            _change_rate(ipv4_prefixes, baseline_ipv4_prefixes)
            if ipv4_prefixes is not None and baseline_ipv4_prefixes is not None
            else None,
            _change_rate(ipv6_prefixes, baseline_ipv6_prefixes)
            if ipv6_prefixes is not None and baseline_ipv6_prefixes is not None
            else None,
        )
        if value is not None
    ]
    update_average = _float_value(_row_value(row, 'update_average'))
    update_stddev = _float_value(_row_value(row, 'update_stddev'))
    profile.update({
        'announce': announce,
        'withdraw': withdraw,
        'update_total': update_total,
        'withdraw_rate': round(withdraw / update_total * 100, 1) if update_total else 0.0,
        'previous_update_total': previous_update_total,
        'update_change_rate': _change_rate(update_total, previous_update_total),
        'sample_count': _int_value(_row_value(row, 'sample_count')),
        'latest_observation': _time_value(_row_value(row, 'latest_observation')),
        'ipv4_prefixes': ipv4_prefixes,
        'ipv6_prefixes': ipv6_prefixes,
        'ipv4_addresses': ipv4_addresses,
        'ipv4_prefix_change': ipv4_prefix_change,
        'ipv6_prefix_change': ipv6_prefix_change,
        'ipv4_address_change': ipv4_address_change,
        'resource_change': max(resource_deltas) if resource_deltas else 0,
        'resource_change_rate': max(resource_change_rates) if resource_change_rates else None,
        'peak_updates': _int_value(_row_value(row, 'peak_updates')),
        'peak_time': _time_value(_row_value(row, 'peak_time')),
        'volatility': round(update_stddev / update_average * 100, 1) if update_average else 0.0,
        'anomaly_count': anomaly_count,
        'high_risk_count': high_risk_count,
        'sparkline': [],
        'series': [],
    })
    return profile


def _ranking(profiles, key, limit, predicate=None):
    candidates = [profile for profile in profiles if predicate is None or predicate(profile)]
    return sorted(
        candidates,
        key=lambda item: (-item[key], -item['high_risk_count'], int(item['asn'])),
    )[:limit]


def get_asn_workbench(start_time, end_time, asn='', limit=None, conn=conn_11):
    start, end, error = _parse_range(start_time, end_time)
    if error:
        return error
    ranking_limit = _parse_limit(limit)
    selected_asn = _normalize_asn(asn)
    if asn and not selected_asn:
        return {'status': False, 'msg': 'ASN 必须是纯数字或 AS 加数字'}, 400

    cache_key = (
        start.strftime('%Y-%m-%d %H:%M:%S'),
        end.strftime('%Y-%m-%d %H:%M:%S'),
        selected_asn,
        ranking_limit,
    ) if conn is conn_11 else None
    cached = _cached_asn_result(cache_key)
    if cached is not None:
        return cached

    data_loader.ensure_core_data_loaded()
    pool_asns = list(dict.fromkeys(str(item) for item in data_loader.ases_1000['asn'].tolist()))
    event_rows = get_as_event_counts(conn=conn, start_time=start, end_time=end)
    anomaly_counts, high_risk_counts = _event_counters(event_rows)
    query_asns = _operational_candidates(
        pool_asns,
        anomaly_counts,
        high_risk_counts,
        selected_asn,
    )
    previous_start = start - (end - start)
    feature_rows = get_as_feature_aggregates(
        conn=conn,
        grouped_asns=_group_asns(query_asns),
        previous_start=previous_start,
        current_start=start,
        end_time=end,
    )

    profiles = {}
    for row in feature_rows:
        row_asn = _normalize_asn(_row_value(row, 'asn'))
        if not row_asn:
            continue
        profiles[row_asn] = _asn_profile(
            row,
            anomaly_count=anomaly_counts[row_asn],
            high_risk_count=high_risk_counts[row_asn],
        )
    for row_asn in query_asns:
        if row_asn not in profiles:
            profiles[row_asn] = _asn_profile(
                {'asn': row_asn},
                anomaly_count=anomaly_counts[row_asn],
                high_risk_count=high_risk_counts[row_asn],
            )

    scope_profiles = [profiles[item] for item in query_asns if item in profiles]
    data_profiles = [profile for profile in scope_profiles if profile['sample_count'] > 0]
    update_rankings = _ranking(data_profiles, 'update_total', ranking_limit)
    withdraw_rate_rankings = _ranking(
        data_profiles,
        'withdraw_rate',
        ranking_limit,
        predicate=lambda item: item['update_total'] > 0,
    )
    resource_change_rankings = _ranking(
        data_profiles,
        'resource_change_rate',
        ranking_limit,
        predicate=lambda item: item['resource_change_rate'] is not None,
    )
    volatility_rankings = _ranking(
        data_profiles,
        'volatility',
        ranking_limit,
        predicate=lambda item: item['sample_count'] > 1,
    )
    anomaly_rankings = _ranking(
        scope_profiles,
        'anomaly_count',
        ranking_limit,
        predicate=lambda item: item['anomaly_count'] > 0,
    )

    selected = profiles.get(selected_asn) if selected_asn else None
    sparkline_asns = {
        profile['asn']
        for ranking in (
            update_rankings,
            withdraw_rate_rankings,
            resource_change_rankings,
            volatility_rankings,
            anomaly_rankings,
        )
        for profile in ranking
    }
    if selected_asn:
        sparkline_asns.add(selected_asn)
    sparkline_rows = get_as_sparklines(
        conn=conn,
        grouped_asns=_group_asns(sorted(sparkline_asns, key=int)),
        start_time=start,
        end_time=end,
    )
    sparks = defaultdict(list)
    for row in sparkline_rows:
        row_asn = _normalize_asn(_row_value(row, 'asn'))
        sparks[row_asn].append({
            'time': _time_value(_row_value(row, 'bucket')),
            'announce': _int_value(_row_value(row, 'announce')),
            'withdraw': _int_value(_row_value(row, 'withdraw')),
        })
    for profile in profiles.values():
        profile['sparkline'] = sparks.get(profile['asn'], [])
    if selected is not None:
        selected['series'] = [
            _feature_point(row)
            for row in get_as_feature_series(
                conn=conn,
                table_base=_table_base(selected_asn),
                asn=selected_asn,
                start_time=start,
                end_time=end,
            )
        ]

    latest_observation = max(
        (profile['latest_observation'] for profile in data_profiles if profile['latest_observation']),
        default=None,
    )
    payload = {
        'start_time': start.strftime('%Y-%m-%d %H:%M:%S'),
        'end_time': end.strftime('%Y-%m-%d %H:%M:%S'),
        'timezone': 'Asia/Shanghai',
        'latest_observation': latest_observation,
        'scope_kind': 'operational_asn_cohort',
        'scope_note': '静态优先 100、重要 ASN 最多 50、窗口异常 ASN 最多 50，并加入指定 ASN；排行仅在该候选集内比较。',
        'candidate_pool_size': len(pool_asns),
        'scope_size': len(query_asns),
        'feature_asn_count': len(data_profiles),
        'important_asn_count': sum(1 for profile in scope_profiles if profile['important']),
        'asns_with_anomalies': sum(1 for profile in scope_profiles if profile['anomaly_count'] > 0),
        'update_leader': update_rankings[0] if update_rankings else None,
        'withdraw_rate_leader': withdraw_rate_rankings[0] if withdraw_rate_rankings else None,
        'resource_change_leader': resource_change_rankings[0] if resource_change_rankings else None,
        'volatility_leader': volatility_rankings[0] if volatility_rankings else None,
        'update_rankings': update_rankings,
        'withdraw_rate_rankings': withdraw_rate_rankings,
        'resource_change_rankings': resource_change_rankings,
        'volatility_rankings': volatility_rankings,
        'anomaly_rankings': anomaly_rankings,
        'selected_asn': selected,
    }
    return _store_asn_result(cache_key, payload)


def get_asn_recent_events(start_time, end_time, asn='', page_size=None, conn=conn_11):
    start, end, error = _parse_range(start_time, end_time)
    if error:
        return error
    normalized_asn = _normalize_asn(asn)
    if not normalized_asn:
        return {'status': False, 'msg': 'ASN 必须是纯数字或 AS 加数字'}, 400
    try:
        size = int(page_size or 10)
    except (TypeError, ValueError):
        size = 10
    size = min(50, max(1, size))
    rows, record_count = get_as_exact_event_rows(
        conn=conn,
        asn=normalized_asn,
        start_time=start,
        end_time=end,
        page_size=size,
    )
    return {
        'match_mode': 'asn_token_exact',
        'asn': normalized_asn,
        'record_count': str(record_count),
        'total_page': 1 if record_count else 0,
        'data': deal_event(event_rows=rows),
    }
