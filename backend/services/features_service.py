import csv
import datetime
import ipaddress
import json as std_json
from collections import defaultdict
from functools import lru_cache
from io import StringIO
from urllib.parse import quote

from flask import make_response

from config.config import BIG_COUNTRY, FEATURE_COUNTRY_TABLE, FEATURE_OTHER_TABLE, PFX2AS_DICT_FILE, SOURCE
from config.database import conn_11
from database.feature_asn import select_as_feature_db
from database.feature_country import select_country_feature_db
from database.prefix_outage import select_prefix_outage_detail_by_interval
from database.utils import get_tables_by_time
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


COUNTRY_EXPORT_SLUG = {
    '伊朗': 'iran',
    '中国': 'china',
    '美国': 'usa',
    '俄罗斯': 'russia',
    '日本': 'japan',
    '韩国': 'korea',
    '德国': 'germany',
    '英国': 'uk',
    '法国': 'france',
    '澳大利亚': 'australia',
    '印度': 'india',
    '巴西': 'brazil',
    '加拿大': 'canada',
    '墨西哥': 'mexico',
    '阿富汗': 'afghanistan',
    '巴基斯坦': 'pakistan',
    '沙特阿拉伯': 'saudi_arabia',
    '阿联酋': 'uae',
    '埃及': 'egypt',
    '南非': 'south_africa',
}


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


def _format_export_value(value):
    if value is None:
        return ''
    if isinstance(value, datetime.datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return str(value)


def _build_csv_response(headers, rows, filename, title=None):
    output = StringIO()
    writer = csv.writer(output)
    if title:
        writer.writerow([title])
    writer.writerow(headers)
    writer.writerows(rows)

    response = make_response('\ufeff' + output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8-sig'
    response.headers['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(filename)}"
    return response


def _collector_name():
    return 'rrc25'


def _date_range_tag(start_time_dt, end_time_dt):
    return f"{start_time_dt.strftime('%m%d')}_{end_time_dt.strftime('%m%d')}"


def _country_slug(country):
    return COUNTRY_EXPORT_SLUG.get(country, str(country).strip().lower())


def _normalize_prefix(prefix):
    return str(ipaddress.ip_network(str(prefix).strip(), strict=False))


def _split_prefixes_by_version(prefixes):
    v4, v6 = [], []
    for prefix in prefixes:
        try:
            net = ipaddress.ip_network(str(prefix).strip(), strict=False)
        except Exception:
            continue
        if net.version == 4:
            v4.append(net)
        else:
            v6.append(net)
    return v4, v6


def _collapse_networks(networks):
    if not networks:
        return []
    return list(ipaddress.collapse_addresses(networks))


def _v6_64_blocks(net):
    if net.prefixlen > 64:
        return 0
    return 1 << (64 - net.prefixlen)


def _compute_prefix_space(prefixes):
    v4_networks, v6_networks = _split_prefixes_by_version(prefixes)
    collapsed_v4 = _collapse_networks(v4_networks)
    collapsed_v6 = _collapse_networks(v6_networks)
    v4_total = sum(int(net.num_addresses) for net in collapsed_v4)
    v6_total = sum(_v6_64_blocks(net) for net in collapsed_v6)
    return v4_total, v6_total


def _format_ratio_value(numerator, denominator):
    if numerator == 0:
        return 0.0, '0'
    if denominator <= 0:
        return -1.0, ''
    ratio = numerator / denominator * 100
    if float(ratio).is_integer():
        return ratio, str(int(ratio))
    return ratio, f'{ratio:.2f}'


@lru_cache(maxsize=1)
def _load_pfx2as_dict():
    with open(PFX2AS_DICT_FILE, 'r', encoding='utf-8') as file:
        return std_json.load(file)


def get_top_feature_data(start_time, end_time, target, conn=conn_11):
    if not all([start_time, end_time, target]):
        return {'status': False, 'msg': '缺少必需参数：start_time, end_time, target'}

    query_type = _get_query_type(target)

    try:
        if query_type == 'as':
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


def get_country_as_outage_export(country, start_time, end_time, conn=conn_11):
    if not country:
        return {'status': False, 'msg': '国家不能为空！'}, 400

    start_time_dt, end_time_dt, error = _parse_datetime_range(start_time, end_time, export_mode=True)
    if error:
        return error

    tables = get_tables_by_time('prefix_outage', start_time_dt, end_time_dt)
    rows = select_prefix_outage_detail_by_interval(
        conn=conn,
        source=SOURCE,
        start_time=start_time_dt,
        end_time=end_time_dt,
        country=country,
        asn=None,
        tables=tables,
    )

    outage_prefixes_by_asn = defaultdict(set)
    for prefix, asn, *_ in rows:
        if not prefix or not asn:
            continue
        try:
            outage_prefixes_by_asn[str(asn)].add(_normalize_prefix(prefix))
        except Exception:
            continue

    pfx2as_dict = _load_pfx2as_dict()
    export_rows = []
    for asn, outage_prefixes in outage_prefixes_by_asn.items():
        total_prefixes = set((pfx2as_dict.get(str(asn)) or {}).keys())
        v4_total, v6_total = _compute_prefix_space(total_prefixes)
        v4_outage, v6_outage = _compute_prefix_space(outage_prefixes)

        if v4_total > 0:
            v4_outage = min(v4_outage, v4_total)
        if v6_total > 0:
            v6_outage = min(v6_outage, v6_total)

        v4_ratio_num, v4_ratio = _format_ratio_value(v4_outage, v4_total)
        v6_ratio_num, v6_ratio = _format_ratio_value(v6_outage, v6_total)

        is_full_v4 = v4_total > 0 and v4_outage >= v4_total
        is_full_v6 = v6_total > 0 and v6_outage >= v6_total
        is_full = (is_full_v4 and is_full_v6) or (v4_total == 0 and is_full_v6) or (v6_total == 0 and is_full_v4)

        export_rows.append((
            v4_ratio_num,
            v6_ratio_num,
            int(asn) if str(asn).isdigit() else asn,
            [
                _format_export_value(asn),
                _format_export_value(v4_outage),
                _format_export_value(v4_total),
                v4_ratio,
                _format_export_value(v6_outage),
                _format_export_value(v6_total),
                v6_ratio,
                '是' if is_full else '否',
            ],
        ))

    export_rows.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    csv_rows = [item[3] for item in export_rows]
    filename = f'{_collector_name()}_asn_outage_space_ratio_{_date_range_tag(start_time_dt, end_time_dt)}.csv'
    headers = [
        '自治域号',
        '断网IPv4地址数',
        '正常IPv4地址数',
        '不可见IPv4地址占比',
        '断网IPv6地址数',
        '正常IPv6地址数',
        '不可见IPv6地址占比',
        '地址是否完全不可见',
    ]
    title = f'表 1 {country}部分自治域地址回撤情况'
    return _build_csv_response(headers, csv_rows, filename, title=title)


def get_country_prefix_outage_export(country, start_time, end_time, conn=conn_11):
    if not country:
        return {'status': False, 'msg': '国家不能为空！'}, 400

    start_time_dt, end_time_dt, error = _parse_datetime_range(start_time, end_time, export_mode=True)
    if error:
        return error

    tables = get_tables_by_time('prefix_outage', start_time_dt, end_time_dt)
    rows = select_prefix_outage_detail_by_interval(
        conn=conn,
        source=SOURCE,
        start_time=start_time_dt,
        end_time=end_time_dt,
        country=country,
        asn=None,
        tables=tables,
    )

    csv_rows = [
        [
            s_time.strftime('%m%d') if isinstance(s_time, datetime.datetime) else '',
            _format_export_value(prefix),
            _format_export_value(asn),
            _format_export_value(as_name),
            _format_export_value(org_name),
            _format_export_value(s_time),
            _format_export_value(e_time),
        ]
        for (
            prefix,
            asn,
            _country_name,
            as_name,
            org_name,
            _as_type,
            _outage_id,
            _outage_level,
            _outage_level_descr,
            s_time,
            e_time,
            _duration,
        ) in rows
    ]

    filename = f'{_collector_name()}_prefix_outage_{_date_range_tag(start_time_dt, end_time_dt)}_{_country_slug(country)}.csv'
    headers = ['day', 'prefix', 'asn', 'as_name', 'org_name', 's_time', 'e_time']
    return _build_csv_response(headers, csv_rows, filename)
