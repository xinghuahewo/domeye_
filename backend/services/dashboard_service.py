import datetime
import json
import os

from dateutil.relativedelta import relativedelta

from config.config import ROUTING_RES_TABLE
from config.database import conn_11
from utils.get_event import (
    deal_event_count,
    deal_geo_event_count,
    deal_sort_event_count,
    deal_type_event_count,
    deal_vp_state,
    get_collector_state,
    get_event_count,
    get_geo_event_count,
    get_sort_event_count,
    get_type_event_count,
)


COUNTRY_INFO = {}


def _get_event_table_names(now=None):
    current_time = now or datetime.datetime.now()
    current_month = current_time.strftime('%Y%m')
    last_month = (current_time.date() - relativedelta(months=1)).strftime('%Y%m')
    return 'event_table_{}'.format(current_month), 'event_table_{}'.format(last_month)


def get_sorted_event_counts(obj=None, country=None, conn=conn_11, country_info=None, now=None):
    event_table, last_month_table = _get_event_table_names(now=now)
    effective_country_info = COUNTRY_INFO if country_info is None else country_info
    event_rows = get_sort_event_count(
        conn=conn,
        last_month_table=last_month_table,
        event_table=event_table,
        obj=obj,
        country=country,
    )
    return deal_sort_event_count(
        conn=conn,
        last_month_table=last_month_table,
        event_table=event_table,
        event_rows=event_rows,
        obj=obj,
        country=country,
        country_info=effective_country_info,
    )


def get_total_event_counts(country=None, conn=conn_11, now=None):
    event_table, last_month_table = _get_event_table_names(now=now)
    event_rows = get_event_count(
        conn=conn,
        last_month_table=last_month_table,
        event_table=event_table,
        country=country,
    )
    return deal_event_count(event_rows=event_rows)


def get_geo_event_counts(conn=conn_11, country_info=None, now=None):
    event_table, last_month_table = _get_event_table_names(now=now)
    effective_country_info = COUNTRY_INFO if country_info is None else country_info
    event_rows = get_geo_event_count(
        conn=conn,
        last_month_table=last_month_table,
        event_table=event_table,
    )
    return deal_geo_event_count(event_rows=event_rows, country_info=effective_country_info)


def get_type_event_counts(event_type=None, country='global', conn=conn_11, now=None):
    event_table, last_month_table = _get_event_table_names(now=now)
    event_rows_today, event_rows_yesterday = get_type_event_count(
        conn=conn,
        last_month_table=last_month_table,
        event_table=event_table,
        country=country,
        event_type=event_type,
    )
    return deal_type_event_count(
        event_rows_td=event_rows_today,
        event_rows_yd=event_rows_yesterday,
        event_type=event_type,
    )


def get_vantage_point_state(collector=None, conn=conn_11, prefix_count_table=ROUTING_RES_TABLE):
    vp_rows = get_collector_state(
        conn=conn,
        prefix_count_table=prefix_count_table,
        collector=collector,
    )
    return deal_vp_state(vp_rows=vp_rows)


def get_security_screen_data(screen_data_path=None):
    resolved_path = screen_data_path or os.path.abspath('reports/security_screen_data.json')
    try:
        with open(resolved_path, encoding='utf-8') as file:
            return json.load(file)
    except FileNotFoundError:
        return {'status': False, 'msg': '安全大屏数据文件未找到'}, 404
    except Exception as error:
        return {'status': False, 'msg': str(error)}, 500
