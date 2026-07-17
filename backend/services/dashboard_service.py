"""核心首页统计服务。"""

import datetime

from dateutil.relativedelta import relativedelta

from config.database import conn_11
from utils.get_event import (
    deal_event_count,
    deal_type_event_count,
    get_event_count,
    get_type_event_count,
)


def _get_event_table_names(now=None):
    current_time = now or datetime.datetime.now()
    current_month = current_time.strftime('%Y%m')
    last_month = (current_time.date() - relativedelta(months=1)).strftime('%Y%m')
    return f'event_table_{current_month}', f'event_table_{last_month}'


def get_total_event_counts(country=None, conn=conn_11, now=None):
    event_table, last_month_table = _get_event_table_names(now=now)
    rows = get_event_count(
        conn=conn,
        last_month_table=last_month_table,
        event_table=event_table,
        country=country,
    )
    return deal_event_count(event_rows=rows)


def get_type_event_counts(event_type=None, country='global', conn=conn_11, now=None):
    event_table, last_month_table = _get_event_table_names(now=now)
    today, yesterday = get_type_event_count(
        conn=conn,
        last_month_table=last_month_table,
        event_table=event_table,
        country=country,
        event_type=event_type,
    )
    return deal_type_event_count(
        event_rows_td=today,
        event_rows_yd=yesterday,
        event_type=event_type,
    )
