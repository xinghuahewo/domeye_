import datetime

from config.config import BIG_COUNTRY, FEATURE_COUNTRY_TABLE, FEATURE_OTHER_TABLE, SOURCE
from config.database import conn_11
from database.feature_asn import select_as_feature_db
from database.feature_country import select_country_feature_db
from utils import data_loader
from utils.data_loader import as_info, prefix_info
from utils.get_as_info import get_as_country
from utils.get_event import (
    deal_features,
    deal_outage,
    get_as_features_list,
    get_as_outage_by_interval,
    get_country_feature_list,
    get_prefix_outage_by_interval,
)


def _parse_datetime_range(start_time, end_time, export_mode=False):
    if not start_time or not end_time:
        if export_mode:
            return None, None, ({'status': False, 'msg': '开始时间和结束时间不能为空！'}, 400)
        return None, None, {'status': False, 'msg': '开始时间和结束时间不能为空！'}

    try:
        start_time_dt = datetime.datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
        end_time_dt = datetime.datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        if export_mode:
            return None, None, ({'status': False, 'msg': '时间格式错误，应为 YYYY-MM-DD HH:MM:SS'}, 400)
        return None, None, {'status': False, 'msg': '时间格式错误，应为 YYYY-MM-DD HH:MM:SS'}

    if start_time_dt > end_time_dt:
        if export_mode:
            return None, None, ({'status': False, 'msg': '开始时间不能晚于结束时间！'}, 400)
        return None, None, {'status': False, 'msg': '开始时间不能晚于结束时间！'}

    return start_time_dt, end_time_dt, None


def _parse_page_num(raw_value):
    if raw_value in [None, ''] or str(raw_value).startswith('0'):
        return 1
    if str(raw_value).isdigit():
        return int(raw_value)
    return 1


def _parse_page_size(raw_value):
    return int(raw_value) if str(raw_value) in ['5', '10', '20', '50'] else 5


def _get_query_type(query):
    trimmed_input = query.strip().lower()
    if trimmed_input.startswith('as') and trimmed_input[2:].isdigit():
        return 'as'
    if trimmed_input.isdigit():
        return 'as'
    if trimmed_input.startswith('rrc') and trimmed_input[3:].isdigit():
        return 'collector'
    if query in ['路由采集点', 'collector']:
        return 'collector'
    return 'country'


def get_top_feature_data(start_time, end_time, target, conn=conn_11):
    if not all([start_time, end_time, target]):
        return {'status': False, 'msg': '缺少必需参数：start_time, end_time, target'}

    query_type = _get_query_type(target)

    try:
        if query_type == 'as':
            data_loader.ensure_core_data_loaded()
            target = ''.join(ch for ch in target if ch.isdigit())
            as_country = get_as_country(as_info, target)
            table_name = f"feature_{BIG_COUNTRY[as_country]}" if as_country in BIG_COUNTRY else FEATURE_OTHER_TABLE
            data = select_as_feature_db(
                conn=conn,
                target=target,
                source=SOURCE,
                start_time=start_time,
                end_time=end_time,
                table_name=table_name,
            )
        elif query_type == 'country':
            data = select_country_feature_db(
                conn=conn,
                target=target,
                source=SOURCE,
                start_time=start_time,
                end_time=end_time,
                table_name=FEATURE_COUNTRY_TABLE,
            )
        elif query_type == 'collector':
            data = select_country_feature_db(
                conn=conn,
                target='collect',
                source=SOURCE,
                start_time=start_time,
                end_time=end_time,
                table_name=FEATURE_COUNTRY_TABLE,
            )
        else:
            return {'status': False, 'msg': '未知的查询类型'}, 500
    except Exception:
        return {'status': False, 'msg': '数据查询失败'}, 500

    try:
        return deal_features(data)
    except Exception as error:
        return str(error), 500


def get_country_feature_series(start_time, end_time, country='', page_num=None, page_size=None, conn=conn_11):
    if not start_time or not end_time:
        return {'status': False, 'msg': '开始时间和结束时间不能为空！'}

    data_loader.ensure_core_data_loaded()
    return get_country_feature_list(
        conn=conn,
        start_time=start_time,
        end_time=end_time,
        country=country,
        all_country_list=data_loader.country_list,
        page_num=_parse_page_num(page_num),
        page_size=_parse_page_size(page_size),
    )

def get_as_feature_series(start_time, end_time, asn='', country='', page_num=None, page_size=None, conn=conn_11):
    if not start_time or not end_time:
        return {'status': False, 'msg': '开始时间和结束时间不能为空！'}

    data_loader.ensure_core_data_loaded()
    if country:
        ases = data_loader.ases_1000[data_loader.ases_1000['as_country_cn'] == country]['asn'].tolist()
    else:
        ases = data_loader.ases_1000['asn'].tolist()

    if asn:
        ases = [asn]

    return get_as_features_list(
        conn=conn,
        as_info=as_info,
        start_time=start_time,
        end_time=end_time,
        ases=ases,
        page_num=_parse_page_num(page_num),
        page_size=_parse_page_size(page_size),
    )


def get_as_outage_feature(country, start_time, end_time, conn=conn_11):
    start_time_dt, end_time_dt, error = _parse_datetime_range(start_time, end_time)
    if error:
        return error

    result = get_as_outage_by_interval(
        conn=conn,
        source=SOURCE,
        country=country,
        start_time=start_time_dt,
        end_time=end_time_dt,
    )
    return deal_outage(
        result,
        type='asn',
        start_time=start_time_dt,
        end_time=end_time_dt,
        prefixes=[],
        interval_minutes=3,
    )


def get_prefix_outage_feature(country, asn, start_time, end_time, conn=conn_11, prefixes=None):
    start_time_dt, end_time_dt, error = _parse_datetime_range(start_time, end_time)
    if error:
        return error

    if prefixes is None:
        data_loader.ensure_core_data_loaded()
    effective_prefixes = prefix_info.keys() if prefixes is None else prefixes
    result = get_prefix_outage_by_interval(
        conn=conn,
        source=SOURCE,
        country=country,
        asn=asn,
        start_time=start_time_dt,
        end_time=end_time_dt,
    )
    return deal_outage(
        result,
        type='prefix',
        start_time=start_time_dt,
        end_time=end_time_dt,
        prefixes=effective_prefixes,
        interval_minutes=3,
    )
