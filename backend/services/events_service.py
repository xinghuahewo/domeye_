import datetime
import traceback

from dateutil.relativedelta import relativedelta
import psycopg2

from config.database import conn_11, conn_13, conn_15
from core.visualize.outage_topo import generate_graph, graph_to_dict, build_country_topo_dict
from database.as_outage import get_as_outage_de
from database.country_outage import get_country_outage_de
from database.hijack import get_hijack_de
from database.leak_event import get_leak_de
from database.prefix_outage import get_pre_outage_de
from database.sub_hijack import get_sub_hijack_de
from utils import data_loader
from utils.data_loader import as_info, country_info, domain_info, prefix_info
from utils.get_as_info import get_as_country_cn, get_as_info, get_as_name, get_as_org_name, get_admin_info
from utils.get_event import (
    deal_event,
    deal_top_event,
    get_as_feature,
    get_boundary_outage_de,
    get_event,
    get_top_event,
    get_total_page,
)
from utils.get_prefix_info import get_prefix_domain, get_prefix_domain_auth
from utils.utils import check_ip_in_subnet

FEATURE_TABLE = 'bgp_feature'
EMPTY_GRAPH = {'nodes': [], 'links': []}


def _parse_page_size(raw_value):
    return int(raw_value) if raw_value in ['10', '50', '100', '200'] else 10


def _parse_page_num(raw_value):
    if raw_value in [None, ''] or str(raw_value).startswith('0'):
        return 1
    if str(raw_value).isdigit():
        return int(raw_value)
    return 1


def _parse_date_range(raw_value):
    if not raw_value:
        return None, None
    parts = raw_value.split('_')
    start_time = parts[0] if parts else None
    end_time = parts[1] if len(parts) > 1 else None
    return start_time, end_time


def _event_table_names(now=None):
    current_time = now or datetime.datetime.now()
    current_table = 'event_table_{}'.format(current_time.strftime('%Y%m'))
    last_month_table = 'event_table_{}'.format((current_time.date() - relativedelta(months=1)).strftime('%Y%m'))
    return current_table, last_month_table


def _year_month(start_time):
    return start_time[0:4], start_time[5:7]


def _detail_table(detail_url):
    if not detail_url:
        return None
    date = str(detail_url.split('/')[1])[0:7].replace('-', '')
    return 'event_table_' + date


def _build_domain_list(prefix):
    data_loader.ensure_domain_data_loaded()
    domain_list = []
    count = 1

    for domain_name in get_prefix_domain_auth(prefix_info, prefix):
        try:
            domain_data = domain_info[domain_name]
        except Exception:
            continue
        domain_list.append({
            'id': count,
            'is_auth': True,
            'domain': domain_name,
            'domain_prefix': prefix,
            'domain_ip': check_ip_in_subnet(prefix=prefix, is_auth=True, domain_data=domain_data),
            'domain_title': domain_data['title'],
            'domain_industry': domain_data['industry'],
        })
        count += 1

    for domain_name in get_prefix_domain(prefix_info, prefix):
        try:
            domain_data = domain_info[domain_name]
        except Exception:
            continue
        domain_list.append({
            'id': count,
            'is_auth': False,
            'domain': domain_name,
            'domain_prefix': prefix,
            'domain_ip': check_ip_in_subnet(prefix=prefix, is_auth=False, domain_data=domain_data),
            'domain_title': domain_data['title'],
            'domain_industry': domain_data['industry'],
        })
        count += 1

    return domain_list


def _build_neighbor_graph(country, anchor_as):
    nodes = set()
    try:
        graph = generate_graph(country)
        nodes.update(graph.neighbors(anchor_as))
        for neighbor in graph.neighbors(anchor_as):
            nodes.update(graph.neighbors(neighbor))
        nodes.add(anchor_as)
        subgraph = graph.subgraph(nodes)
        return graph_to_dict(subgraph, 'as', anchor_as)
    except Exception:
        return EMPTY_GRAPH


def _build_boundary_graph(country, export_as, peer_as):
    nodes = set()
    try:
        graph = generate_graph(country)
        nodes.update(graph.neighbors(export_as))
        for neighbor in graph.neighbors(export_as):
            nodes.update(graph.neighbors(neighbor))
        nodes.add(export_as)
        subgraph = graph.subgraph(nodes)
        return graph_to_dict(subgraph, 'boundary', export_as, peer_as)
    except Exception:
        return EMPTY_GRAPH


def _get_event_feature_data(event_type, start_time, problem, event_id, source):
    year, month = _year_month(start_time)
    if event_type == 'prefix_outage':
        table = event_type + '_' + '{}{}'.format(year, month)
        prefix = problem.replace('-', '/')
        row = get_pre_outage_de(conn=conn_15, table=table, prefix=prefix, outage_id=event_id, source=source)
        feature_data = {}
        asn = ''
        event_time = ''
        for item in row:
            asn = item['asn']
            event_time = item['s_time']
        feature_data['time_list'], feature_data['announ_list'], feature_data['withdraw_list'] = get_as_feature(
            conn=conn_13,
            feature_table=FEATURE_TABLE,
            asn=asn,
            t=event_time,
        )
        return feature_data

    if event_type == 'as_outage':
        s_time = start_time.replace("%20", " ")
        event_time = datetime.datetime.strptime(s_time, "%Y-%m-%d %H:%M:%S")
        feature_data = {}
        feature_data['time_list'], feature_data['announ_list'], feature_data['withdraw_list'] = get_as_feature(
            conn=conn_13,
            feature_table=FEATURE_TABLE,
            asn=problem,
            t=event_time,
        )
        return feature_data

    if event_type in ['country_outage', 'sub_hijack']:
        return {}

    if event_type == 'hijack':
        table = event_type + '_' + '{}{}'.format(year, month)
        prefix = problem.replace('-', '/')
        row = get_hijack_de(conn=conn_11, table=table, prefix=prefix, hijack_id=event_id, source=source)
        feature_data = {}
        hijacker_as = ''
        event_time = ''
        for item in row:
            hijacker_as = item['hijacker_as']
            event_time = item['s_time']
        feature_data['time_list'], feature_data['announ_list'], feature_data['withdraw_list'] = get_as_feature(
            conn=conn_13,
            feature_table=FEATURE_TABLE,
            asn=hijacker_as,
            t=event_time,
        )
        return feature_data

    if event_type == 'leak':
        table = 'leak_event_' + '{}{}'.format(year, month)
        prefix = problem.replace('-', '/')
        row = get_leak_de(conn=conn_13, table=table, prefix=prefix, leak_id=event_id, source=source)
        feature_data = {}
        asn = ''
        event_time = ''
        for item in row:
            asn = item['leak_by']
            event_time = item['s_time']
        feature_data['time_list'], feature_data['announ_list'], feature_data['withdraw_list'] = get_as_feature(
            conn=conn_13,
            feature_table=FEATURE_TABLE,
            asn=asn,
            t=event_time,
        )
        return feature_data

    if event_type == 'boundary_outage':
        table = event_type + '_' + '{}{}'.format(year, month)
        export_as, peer_as = problem.split('--')
        get_boundary_outage_de(
            conn=conn_15,
            table=table,
            export_as=export_as,
            peer_as=peer_as,
            outage_id=event_id,
            source=source,
        )
        feature_data = {}
        s_time = start_time.replace("%20", " ")
        event_time = datetime.datetime.strptime(s_time, "%Y-%m-%d %H:%M:%S")
        feature_data['time_list'], feature_data['announ_list'], feature_data['withdraw_list'] = get_as_feature(
            conn=conn_13,
            feature_table=FEATURE_TABLE,
            asn=export_as,
            t=event_time,
        )
        return feature_data

    return {}


def _get_prefix_outage_detail(start_time, problem, event_id, source):
    year, month = _year_month(start_time)
    table = 'prefix_outage_' + '{}{}'.format(year, month)
    prefix = problem.replace('-', '/')
    row = get_pre_outage_de(conn=conn_15, table=table, prefix=prefix, outage_id=event_id, source=source)
    detail = {}
    asn = ''
    event_time = ''
    for item in row:
        detail['outage_prefix'] = prefix
        asn = item['asn']
        detail['as_of_prefix'] = get_as_info(as_info, asn)
        detail['attacked_as'] = item['asn']
        detail['attacked_as_name'] = item['as_name']
        detail['attacked_org'] = item['org_name']
        detail['attacked_country'] = item['country']
        detail['attacker_as'] = item['asn']
        detail['attacker_as_name'] = item['as_name']
        detail['attacker_org'] = item['org_name']
        detail['attacker_country'] = item['country']
        detail['start_time'], detail['end_time'] = str(item['s_time']), str(item['e_time'])
        detail['event_descr'] = item['outage_level_descr']
        detail['event_level'] = item['outage_level']
        detail['type_of_as'] = item['as_type']
        detail['duration'] = str(item['duration'])
        detail['pre_vp_paths'] = item['pre_vp_paths']
        detail['eve_vp_paths'] = item['eve_vp_paths']
        detail['next_vp_paths'] = item['next_vp_paths'] if item['next_vp_paths'] else []
        detail['asn'] = item['asn'].split('_')[1] if '_' in item['asn'] else item['asn']
        detail['event_info'] = item['event_info']
        event_time = item['s_time']

    detail['attacked_admin'], detail['attacked_tech'], detail['attacked_abuse'] = get_admin_info(as_info, detail['attacked_as'])
    detail['domain_list'] = _build_domain_list(prefix)
    detail['time_list'], detail['announ_list'], detail['withdraw_list'] = get_as_feature(
        conn=conn_13,
        feature_table=FEATURE_TABLE,
        asn=asn,
        t=event_time,
    )
    detail['graph'] = _build_neighbor_graph(detail['attacked_country'], detail['attacked_as'])
    return detail


def _get_as_outage_detail(start_time, problem, event_id, source):
    year, month = _year_month(start_time)
    table = 'as_outage_' + '{}{}'.format(year, month)
    asn = problem
    row = get_as_outage_de(conn=conn_15, table=table, asn=asn, outage_id=event_id, source=source)
    detail = {}
    event_time = ''
    for item in row:
        detail['outage_as'] = get_as_info(as_info, asn)
        detail['attacked_as'] = asn
        detail['attacked_as_name'] = item['as_name']
        detail['attacked_org'] = item['org_name']
        detail['attacked_country'] = item['country']
        detail['attacker_as'] = asn
        detail['attacker_as_name'] = item['as_name']
        detail['attacker_org'] = item['org_name']
        detail['attacker_country'] = item['country']
        detail['start_time'] = str(item['s_time'])
        detail['duration'] = str(item['duration'])
        detail['event_level'] = item['outage_level']
        detail['name_of_as'] = item['as_name']
        detail['as_prefix_num'] = item['total_prefix_num']
        detail['outage_prefix_num'] = item['max_outage_prefix_num']
        detail['as_type'] = item['as_type']
        detail['end_time'] = str(item['e_time'])
        detail['event_descr'] = item['outage_level_descr']
        detail['pre_vp_paths'] = item['pre_vp_paths']
        detail['eve_vp_paths'] = item['eve_vp_paths']
        detail['next_vp_paths'] = item['next_vp_paths'] if item['next_vp_paths'] else []
        detail['outage_prefixes'] = item['outage_prefixes']
        detail['event_info'] = item['event_info']
        detail['asn'] = asn.split('_')[1] if '_' in asn else asn
        event_time = item['s_time']

    count = 1
    detail['domain_list'] = []
    for prefix in detail['outage_prefixes']:
        for domain_name in get_prefix_domain_auth(prefix_info, prefix):
            try:
                domain_data = domain_info[domain_name]
            except Exception:
                continue
            detail['domain_list'].append({
                'id': count,
                'is_auth': True,
                'domain': domain_name,
                'domain_prefix': prefix,
                'domain_ip': check_ip_in_subnet(prefix=prefix, is_auth=True, domain_data=domain_data),
                'domain_title': domain_data['title'],
                'domain_industry': domain_data['industry'],
            })
            count += 1

        for domain_name in get_prefix_domain(prefix_info, prefix):
            try:
                domain_data = domain_info[domain_name]
            except Exception:
                continue
            detail['domain_list'].append({
                'id': count,
                'is_auth': False,
                'domain': domain_name,
                'domain_prefix': prefix,
                'domain_ip': check_ip_in_subnet(prefix=prefix, is_auth=False, domain_data=domain_data),
                'domain_title': domain_data['title'],
                'domain_industry': domain_data['industry'],
            })
            count += 1

        detail['attacked_admin'], detail['attacked_tech'], detail['attacked_abuse'] = get_admin_info(as_info, detail['attacked_as'])

    detail['time_list'], detail['announ_list'], detail['withdraw_list'] = get_as_feature(
        conn=conn_13,
        feature_table=FEATURE_TABLE,
        asn=asn,
        t=event_time,
    )
    detail['graph'] = _build_neighbor_graph(detail['attacked_country'], detail['attacked_as'])
    return detail


def _get_country_outage_detail(start_time, problem, event_id, source, query_params):
    year, month = _year_month(start_time)
    table = 'country_outage_' + '{}{}'.format(year, month)
    row = get_country_outage_de(conn=conn_15, table=table, country=problem, outage_id=event_id, source=source)
    detail = {}
    for item in row:
        detail['outage_country'] = item['country_chinese_name']
        detail['attacker_country'] = item['country_chinese_name']
        detail['attacked_country'] = item['country_chinese_name']
        detail['total_as_num'] = item['total_as_num']
        detail['start_time'] = str(item['s_time'])
        detail['end_time'] = str(item['e_time'])
        detail['duration'] = str(item['duration'])
        detail['event_level'] = item['outage_level']
        detail['outage_as_num'] = item['max_outage_as_num']
        detail['event_descr'] = item['outage_level_descr']
        detail['outage_ases'] = item['outage_ases'] if item['outage_ases'] else []
        detail['event_info'] = item['event_info']

    detail['outage_as_info'] = []
    for asn in detail['outage_ases']:
        detail['outage_as_info'].append({
            'as': asn,
            'as_name': get_as_name(as_info, asn),
            'as_org': get_as_org_name(as_info, asn),
            'as_country': get_as_country_cn(as_info, asn),
        })

    try:
        topo_mode = query_params.get('topo_mode', 'auto')
        k_hop = int(query_params.get('k_hop', 2))
        max_nodes = int(query_params.get('max_nodes', 2000))
        max_edges = int(query_params.get('max_edges', 5000))
    except Exception:
        topo_mode, k_hop, max_nodes, max_edges = 'auto', 2, 2000, 5000

    detail['graph'] = build_country_topo_dict(
        country_cn=detail['attacked_country'],
        outage_ases=detail['outage_ases'],
        topo_mode=topo_mode,
        k_hop=k_hop,
        max_nodes=max_nodes,
        max_edges=max_edges,
    )
    return detail


def _get_hijack_detail(start_time, problem, event_id, source):
    year, month = _year_month(start_time)
    table = 'hijack_' + '{}{}'.format(year, month)
    prefix = problem.replace('-', '/')
    row = get_hijack_de(conn=conn_11, table=table, prefix=prefix, hijack_id=event_id, source=source)
    detail = {}
    hijacker_as = ''
    event_time = ''
    for item in row:
        detail['hijacked_prefix'] = prefix
        detail['hijacked_as'] = get_as_info(as_info, item['hijacked_as'])
        detail['hijacked_asn'], detail['hijacker_asn'] = item['hijacked_as'], item['hijacker_as']
        detail['attacked_as'] = item['hijacked_as']
        detail['attacked_as_name'] = item['hijacked_as_name']
        detail['attacked_org'] = item['hijacked_as_org']
        detail['attacked_country'] = item['hijacked_as_country']
        detail['hijacker_as'] = get_as_info(as_info, item['hijacker_as'])
        detail['attacker_as'] = item['hijacker_as']
        detail['attacker_as_name'] = item['hijacker_as_name']
        detail['attacker_org'] = item['hijacker_as_org']
        detail['attacker_country'] = item['hijacker_as_country']
        detail['start_time'] = str(item['s_time'])
        detail['end_time'] = str(item['e_time'])
        detail['duration'] = str(item['duration'])
        detail['event_descr'] = str(item['hijack_level_info'])
        detail['event_level'] = item['hijack_level']
        detail['pre_vp_paths'] = item['pre_vp_paths']
        detail['eve_vp_paths'] = item['eve_vp_paths']
        detail['next_vp_paths'] = item['next_vp_paths'] if item['next_vp_paths'] else []
        detail['event_info'] = item['event_info']
        hijacker_as = item['hijacker_as']
        event_time = item['s_time']

    detail['domain_list'] = _build_domain_list(prefix)
    detail['attacked_admin'], detail['attacked_tech'], detail['attacked_abuse'] = get_admin_info(as_info, detail['attacked_as'])
    detail['attacker_admin'], detail['attacker_tech'], detail['attacker_abuse'] = get_admin_info(as_info, detail['attacker_as'])
    detail['time_list'], detail['announ_list'], detail['withdraw_list'] = get_as_feature(
        conn=conn_13,
        feature_table=FEATURE_TABLE,
        asn=hijacker_as,
        t=event_time,
    )
    return detail


def _get_sub_hijack_detail(start_time, problem, event_id, source):
    year, month = _year_month(start_time)
    table = 'sub_hijack_' + '{}{}'.format(year, month)
    prefix = problem.replace('-', '/')
    row = get_sub_hijack_de(conn=conn_11, table=table, prefix=prefix, sub_hijack_id=event_id, source=source)
    detail = {}
    domain_prefix = ''
    for item in row:
        detail['hijacker_prefix'] = prefix
        detail['hijacked_prefix'] = item['hijacked_prefix']
        detail['hijacked_as'] = get_as_info(as_info, item['hijacked_as'])
        detail['hijacked_asn'], detail['hijacker_asn'] = item['hijacked_as'], item['hijacker_as']
        detail['attacked_as'] = item['hijacked_as']
        detail['attacked_as_name'] = item['hijacked_as_name']
        detail['attacked_org'] = item['hijacked_as_org']
        detail['attacked_country'] = item['hijacked_as_country']
        detail['hijacker_as'] = get_as_info(as_info, item['hijacker_as'])
        detail['attacker_as'] = item['hijacker_as']
        detail['attacker_as_name'] = item['hijacker_as_name']
        detail['attacker_org'] = item['hijacker_as_org']
        detail['attacker_country'] = item['hijacker_as_country']
        detail['start_time'] = str(item['s_time'])
        detail['end_time'] = str(item['e_time'])
        detail['duration'] = str(item['duration'])
        detail['event_level'] = item['sub_hijack_level']
        detail['event_descr'] = item['level_info']
        detail['event_info'] = item['event_info']
        domain_prefix = item['hijacked_prefix']

    detail['hijacker_as_info'] = []
    for asn in eval(detail['hijacker_asn']):
        detail['hijacker_as_info'].append({
            'as': asn,
            'as_name': get_as_name(as_info, asn),
            'as_org': get_as_org_name(as_info, asn),
            'as_country': get_as_country_cn(as_info, asn),
        })

    detail['hijacked_as_info'] = []
    for asn in eval(detail['hijacked_asn']):
        detail['hijacked_as_info'].append({
            'as': asn,
            'as_name': get_as_name(as_info, asn),
            'as_org': get_as_org_name(as_info, asn),
            'as_country': get_as_country_cn(as_info, asn),
        })

    detail['domain_list'] = _build_domain_list(domain_prefix)
    attacker_as = eval(detail['attacker_as'])[0]
    attacked_as = eval(detail['attacked_as'])[0]
    detail['attacked_admin'], detail['attacked_tech'], detail['attacked_abuse'] = get_admin_info(as_info, attacked_as)
    detail['attacker_admin'], detail['attacker_tech'], detail['attacker_abuse'] = get_admin_info(as_info, attacker_as)
    return detail


def _get_leak_detail(start_time, problem, event_id, source):
    year, month = _year_month(start_time)
    table = 'leak_event_' + '{}{}'.format(year, month)
    prefix = problem.replace('-', '/')
    row = get_leak_de(conn=conn_13, table=table, prefix=prefix, leak_id=event_id, source=source)
    detail = {}
    asn = ''
    event_time = ''
    for item in row:
        detail['leak_prefix'] = prefix
        detail['attacked_as'] = item['prefix_ori_as']
        detail['attacked_as_name'] = item['ori_as_name']
        detail['attacked_org'] = item['ori_as_org']
        detail['attacked_country'] = item['ori_as_country']
        detail['attacker_as'] = item['leak_by']
        detail['attacker_as_name'] = item['leak_by_name']
        detail['attacker_org'] = item['leak_by_org']
        detail['attacker_country'] = item['leak_by_country']
        detail['leak_by'] = item['leak_by']
        detail['leak_by_name'] = item['leak_by_name']
        detail['leak_by_org'] = item['leak_by_org']
        detail['leak_by_country'] = item['leak_by_country']
        detail['leak_to'] = item['leak_to']
        detail['leak_to_name'] = item['leak_to_name']
        detail['leak_to_org'] = item['leak_to_org']
        detail['leak_to_country'] = item['leak_to_country']
        detail['ori_as'] = item['prefix_ori_as']
        detail['ori_as_name'] = item['ori_as_name']
        detail['ori_as_org'] = item['ori_as_org']
        detail['ori_as_country'] = item['ori_as_country']
        detail['event_descr'] = item['leak_level_info']
        detail['as_path'] = item['as_path']
        detail['event_level'] = item['leak_level']
        detail['event_time'] = str(item['s_time'])
        detail['event_info'] = item['event_info']
        asn = item['leak_by']
        event_time = item['s_time']

    detail['domain_list'] = _build_domain_list(prefix)
    detail['attacked_admin'], detail['attacked_tech'], detail['attacked_abuse'] = get_admin_info(as_info, detail['attacked_as'])
    detail['attacker_admin'], detail['attacker_tech'], detail['attacker_abuse'] = get_admin_info(as_info, detail['attacker_as'])
    detail['time_list'], detail['announ_list'], detail['withdraw_list'] = get_as_feature(
        conn=conn_13,
        feature_table=FEATURE_TABLE,
        asn=asn,
        t=event_time,
    )
    return detail


def _get_boundary_outage_detail(start_time, problem, event_id, source):
    year, month = _year_month(start_time)
    table = 'boundary_outage_' + '{}{}'.format(year, month)
    export_as, peer_as = problem.split('--')
    row = get_boundary_outage_de(
        conn=conn_15,
        table=table,
        export_as=export_as,
        peer_as=peer_as,
        outage_id=event_id,
        source=source,
    )
    detail = {}
    event_time = ''
    for item in row:
        detail['export_as_info'] = get_as_info(as_info, export_as)
        detail['peer_as_info'] = get_as_info(as_info, peer_as)
        detail['export_as_name'] = item['export_as_name']
        detail['export_as_country'] = item['export_as_country']
        detail['export_as_org'] = item['export_as_org']
        detail['peer_as_name'] = item['peer_as_name']
        detail['peer_as_country'] = item['peer_as_country']
        detail['peer_as_org'] = item['peer_as_org']
        detail['start_time'] = str(item['s_time'])
        detail['duration'] = str(item['duration'])
        detail['end_time'] = str(item['e_time'])
        detail['event_level'] = item['outage_level']
        detail['max_used_count'] = item['max_used_count']
        detail['export_as_type'] = item['export_as_type']
        detail['peer_as_type'] = item['peer_as_type']
        detail['event_descr'] = item['outage_level_descr']
        detail['event_info'] = item['event_info']
        detail['export_as'] = export_as
        detail['peer_as'] = peer_as
        event_time = item['s_time']

    detail['export_admin'], detail['export_tech'], detail['export_abuse'] = get_admin_info(as_info, detail['export_as'])
    detail['peer_admin'], detail['peer_tech'], detail['peer_abuse'] = get_admin_info(as_info, detail['peer_as'])
    detail['time_list'], detail['announ_list'], detail['withdraw_list'] = get_as_feature(
        conn=conn_13,
        feature_table=FEATURE_TABLE,
        asn=export_as,
        t=event_time,
    )
    detail['graph'] = _build_boundary_graph(detail['export_as_country'], detail['export_as'], detail['peer_as'])
    return detail


def get_event_list_data(params, conn=conn_11):
    page_size = _parse_page_size(params.get('page_size'))
    page_num = _parse_page_num(params.get('page_num'))

    source = params.get('source', 'r')
    event_type = params.get('event_type')
    level = params.get('level')
    country = params.get('country')
    attacker_as = params.get('attacker_as')
    attacked_as = params.get('attacked_as')
    attacker_org = params.get('attacker_org')
    attacked_org = params.get('attacked_org')
    attacker_country = params.get('attacker_country')
    attacked_country = params.get('attacked_country')
    event_info = params.get('event_info')
    sort_mode = params.get('sort_mode')
    start_time, end_time = _parse_date_range(params.get('datetime'))

    state = params.get('state') if country not in ['all', 'foreign'] else None
    judge_reason = params.get('judge_reason')
    judge_userid = params.get('judge_userid')
    judge_username = params.get('judge_username')
    judge_time = params.get('judge_time')
    notify_userid = params.get('notify_userid')
    notify_username = params.get('notify_username')
    notify_time = params.get('notify_time')

    event_rows = get_event(
        conn=conn,
        page_num=page_num,
        page_size=page_size,
        source=source,
        level=level,
        event_type=event_type,
        country=country,
        attacker_as=attacker_as,
        attacked_as=attacked_as,
        attacker_org=attacker_org,
        attacked_org=attacked_org,
        attacker_country=attacker_country,
        attacked_country=attacked_country,
        event_info=event_info,
        start_time=start_time,
        end_time=end_time,
        sort_mode=sort_mode,
        state=state,
        judge_reason=judge_reason,
        judge_userid=judge_userid,
        judge_username=judge_username,
        judge_time=judge_time,
        notify_userid=notify_userid,
        notify_username=notify_username,
        notify_time=notify_time,
    )
    event_items = deal_event(event_rows=event_rows)
    total_page, record_count = get_total_page(
        conn=conn,
        page_size=page_size,
        source=source,
        level=level,
        event_type=event_type,
        country=country,
        attacker_as=attacker_as,
        attacked_as=attacked_as,
        attacker_org=attacker_org,
        attacked_org=attacked_org,
        attacker_country=attacker_country,
        attacked_country=attacked_country,
        event_info=event_info,
        start_time=start_time,
        end_time=end_time,
        state=state,
        judge_reason=judge_reason,
        judge_userid=judge_userid,
        judge_username=judge_username,
        judge_time=judge_time,
        notify_userid=notify_userid,
        notify_username=notify_username,
        notify_time=notify_time,
    )
    return {
        'total_page': total_page,
        'record_count': str(record_count),
        'data': event_items,
    }


def get_top_event_items(event_type_str, conn=conn_11, now=None, page_size=10):
    event_type = tuple(eval(event_type_str))
    event_table, last_month_table = _event_table_names(now=now)
    top_event_rows = get_top_event(
        conn=conn,
        last_month_table=last_month_table,
        event_table=event_table,
        country='is_domestic',
        page_size=page_size,
        event_type=event_type,
    )
    return deal_top_event(top_event_rows=top_event_rows)


def get_event_detail_data(event_type, start_time, problem, event_id, source, query_params=None):
    query_params = query_params or {}

    if query_params.get('type'):
        return _get_event_feature_data(
            event_type=event_type,
            start_time=start_time,
            problem=problem,
            event_id=event_id,
            source=source,
        )

    if event_type == 'prefix_outage':
        return _get_prefix_outage_detail(start_time, problem, event_id, source)
    if event_type == 'as_outage':
        return _get_as_outage_detail(start_time, problem, event_id, source)
    if event_type == 'country_outage':
        return _get_country_outage_detail(start_time, problem, event_id, source, query_params)
    if event_type == 'hijack':
        return _get_hijack_detail(start_time, problem, event_id, source)
    if event_type == 'sub_hijack':
        return _get_sub_hijack_detail(start_time, problem, event_id, source)
    if event_type == 'leak':
        return _get_leak_detail(start_time, problem, event_id, source)
    if event_type == 'boundary_outage':
        return _get_boundary_outage_detail(start_time, problem, event_id, source)
    return {}


def get_event_state(detail_url, conn=None):
    if detail_url in [None, '']:
        return {'status': False, 'state': ''}

    event_table = _detail_table(detail_url)

    try:
        database_conn = conn or conn_11
        cursor = database_conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        sql = "select state from {} where detail_url = '{}';".format(event_table, detail_url)
        cursor.execute(sql)
        row = cursor.fetchone()
        state = row['state'] if row else None
        cursor.close()
        if state is None:
            return {'status': False, 'state': ''}, 404
        return {'status': True, 'state': state}, 200
    except Exception:
        traceback.print_exc()
        (conn or conn_11).rollback()
        return {'status': False, 'state': ''}, 500


def judge_event(detail_url, state, judge_reason, userid='admin', conn=None, now=None):
    database_conn = conn or conn_11
    judge_time = (now or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    event_table = _detail_table(detail_url)

    try:
        cursor = database_conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT username FROM users WHERE userid = %s", (userid,))
        judge_username = cursor.fetchone()['username']
        sql = """
                UPDATE {} SET state = %s, judge_reason = %s, judge_userid = %s,
                judge_username = %s, judge_time = %s
                WHERE detail_url = %s
              """.format(event_table)
        cursor.execute(sql, (state, judge_reason, userid, judge_username, judge_time, detail_url))
        database_conn.commit()
        cursor.close()
        return {'status': True, 'msg': '事件研判成功！'}, 200
    except Exception as error:
        traceback.print_exc()
        database_conn.rollback()
        return {'status': False, 'msg': f'研判操作失败！错误为：{error}'}, 500


def notify_event(detail_url, userid='admin', conn=None, now=None):
    database_conn = conn or conn_11
    notify_time = (now or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    event_table = _detail_table(detail_url)

    try:
        cursor = database_conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT username FROM users WHERE userid = %s", (userid,))
        notify_username = cursor.fetchone()['username']
        cursor.execute("SELECT state FROM {} WHERE detail_url = %s".format(event_table), (detail_url,))
        pre_state = cursor.fetchone()
        if not pre_state or pre_state['state'] != 'notify':
            cursor.close()
            return {'status': False, 'msg': '该事件不是待通报事件！'}, 400

        sql = """
                UPDATE {} SET state = %s, notify_userid = %s,
                notify_username = %s, notify_time = %s
                WHERE detail_url = %s
              """.format(event_table)
        cursor.execute(sql, ('notified', userid, notify_username, notify_time, detail_url))
        database_conn.commit()
        cursor.close()
        return {'status': True, 'msg': '事件通报成功！'}, 200
    except Exception as error:
        traceback.print_exc()
        database_conn.rollback()
        return {'status': False, 'msg': f'通报操作失败！错误为：{error}'}, 500
