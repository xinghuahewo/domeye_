"""核心首页统计服务。"""

from ast import literal_eval
from collections import Counter, defaultdict
import datetime
import re

from dateutil.relativedelta import relativedelta

from config.data_window import resolve_query_now
from config.database import conn_11
from database.dashboard import (
    CORE_EVENT_TYPES,
    get_dashboard_event_aggregates,
    get_latest_collector_observation,
)
from utils.get_event import (
    deal_event_count,
    deal_type_event_count,
    get_event_count,
    get_type_event_count,
)


def _get_event_table_names(now=None):
    current_time = resolve_query_now(now)
    current_month = current_time.strftime('%Y%m')
    last_month = (current_time.date() - relativedelta(months=1)).strftime('%Y%m')
    return f'event_table_{current_month}', f'event_table_{last_month}'


def get_total_event_counts(country=None, conn=conn_11, now=None):
    effective_now = resolve_query_now(now)
    event_table, last_month_table = _get_event_table_names(now=effective_now)
    rows = get_event_count(
        conn=conn,
        last_month_table=last_month_table,
        event_table=event_table,
        country=country,
        now=effective_now,
    )
    return deal_event_count(event_rows=rows)


def get_type_event_counts(event_type=None, country='global', conn=conn_11, now=None):
    effective_now = resolve_query_now(now)
    event_table, last_month_table = _get_event_table_names(now=effective_now)
    today, yesterday = get_type_event_count(
        conn=conn,
        last_month_table=last_month_table,
        event_table=event_table,
        country=country,
        event_type=event_type,
        now=effective_now,
    )
    return deal_type_event_count(
        event_rows_td=today,
        event_rows_yd=yesterday,
        event_type=event_type,
    )


def _parse_dashboard_range(start_time, end_time):
    try:
        start = datetime.datetime.strptime(start_time or '', '%Y-%m-%d %H:%M:%S')
        end = datetime.datetime.strptime(end_time or '', '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None, None, ({'status': False, 'msg': '时间格式错误，应为 YYYY-MM-DD HH:MM:SS'}, 400)
    if start >= end:
        return None, None, ({'status': False, 'msg': '开始时间必须早于结束时间'}, 400)
    if end - start > datetime.timedelta(days=1):
        return None, None, ({'status': False, 'msg': '首页聚合最多支持 24 小时窗口'}, 400)
    return start, end, None


def _row_value(row, key):
    try:
        return row[key]
    except (KeyError, TypeError):
        return None


def _identifiers(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith(('[', '(')):
        try:
            return _identifiers(literal_eval(text))
        except (ValueError, SyntaxError):
            pass
    return [text]


def _asn_identifiers(value):
    identifiers = []
    for item in _identifiers(value):
        matches = re.findall(r'(?<![A-Z0-9])AS\s*(\d+)\b', item, flags=re.IGNORECASE)
        if matches:
            identifiers.extend(matches)
            continue
        normalized = item.strip()
        if normalized.isdigit():
            identifiers.append(normalized)
    return identifiers


def _row_count(row):
    value = _row_value(row, 'event_count')
    if value is None:
        value = _row_value(row, 'count')
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _event_entity_counts(rows, key, asn=False):
    counts = Counter()
    high_counts = Counter()
    for row in rows:
        values = (
            _asn_identifiers(_row_value(row, key))
            if asn
            else _identifiers(_row_value(row, key))
        )
        weight = _row_count(row)
        for value in set(values):
            counts[value] += weight
            if str(_row_value(row, 'level')).lower() == 'high':
                high_counts[value] += weight
    return counts, high_counts


def _event_rankings(counts, high_counts, asn=False, limit=6):
    ordered = sorted(counts, key=lambda item: (-counts[item], -high_counts[item], item))[:limit]
    if asn:
        return [
            {
                'asn': item,
                'name': 'AS{}'.format(item),
                'event_count': counts[item],
                'high_risk_count': high_counts[item],
            }
            for item in ordered
        ]
    return [
        {
            'name': item,
            'event_count': counts[item],
            'high_risk_count': high_counts[item],
        }
        for item in ordered
    ]


def _event_series(rows):
    buckets = defaultdict(Counter)
    for row in rows:
        if _row_value(row, 'period') != 'current':
            continue
        observed = _row_value(row, 'bucket')
        event_type = _row_value(row, 'event_type')
        if observed is None or event_type not in CORE_EVENT_TYPES:
            continue
        if isinstance(observed, str):
            try:
                observed = datetime.datetime.strptime(observed, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                continue
        buckets[observed][event_type] += _row_count(row)
    result = []
    for bucket in sorted(buckets):
        counts = {event_type: buckets[bucket][event_type] for event_type in CORE_EVENT_TYPES}
        result.append({
            'time': bucket.strftime('%Y-%m-%d %H:%M:%S'),
            'counts': counts,
            'total': sum(counts.values()),
        })
    return result


def get_dashboard_overview(start_time, end_time, conn=conn_11):
    start, end, error = _parse_dashboard_range(start_time, end_time)
    if error:
        return error

    previous_start = start - (end - start)
    statistics, entities = get_dashboard_event_aggregates(
        conn=conn,
        start_time=previous_start,
        current_start=start,
        end_time=end,
    )
    current_rows = [row for row in statistics if _row_value(row, 'period') == 'current']
    previous_rows = [row for row in statistics if _row_value(row, 'period') == 'previous']
    previous_count = sum(_row_count(row) for row in previous_rows)
    event_count = sum(_row_count(row) for row in current_rows)
    change_rate = None if previous_count == 0 else round(
        (event_count - previous_count) / previous_count * 100,
        1,
    )
    asn_counts, asn_high_counts = _event_entity_counts(entities, 'attacked_as', asn=True)
    country_counts, country_high_counts = _event_entity_counts(entities, 'attacked_country')
    asn_rankings = _event_rankings(asn_counts, asn_high_counts, asn=True)
    country_rankings = _event_rankings(country_counts, country_high_counts)
    latest_observation = get_latest_collector_observation(conn=conn, end_time=end)

    return {
        'start_time': start.strftime('%Y-%m-%d %H:%M:%S'),
        'end_time': end.strftime('%Y-%m-%d %H:%M:%S'),
        'timezone': 'Asia/Shanghai',
        'latest_observation': (
            latest_observation.strftime('%Y-%m-%d %H:%M:%S')
            if isinstance(latest_observation, datetime.datetime)
            else str(latest_observation) if latest_observation else None
        ),
        'event_count': event_count,
        'previous_event_count': previous_count,
        'event_change_rate': change_rate,
        'high_risk_count': sum(
            _row_count(row)
            for row in current_rows
            if str(_row_value(row, 'level')).lower() == 'high'
        ),
        'active_event_count': sum(
            _row_count(row)
            for row in current_rows
            if _row_value(row, 'event_type') != '路由泄漏'
            and bool(_row_value(row, 'missing_end'))
        ),
        'affected_asn_count': len(asn_counts),
        'affected_country_count': len(country_counts),
        'event_series': _event_series(statistics),
        'country_rankings': country_rankings,
        'asn_rankings': asn_rankings,
    }
