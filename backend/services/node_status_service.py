from config.config import VP_RES_TABLE
from config.database import conn_11
from database.vp_resource import count_latest_vp_resources, list_latest_vp_resources


def _parse_page_size(raw_value):
    return int(raw_value) if raw_value in ['10', '50', '100', '200'] else 10


def _parse_page_num(raw_value):
    if raw_value in [None, ''] or str(raw_value).startswith('0'):
        return 1
    if str(raw_value).isdigit():
        return int(raw_value)
    return 1


def get_node_status_list(asn='', page_num=None, page_size=None, conn=conn_11, table_name=VP_RES_TABLE):
    page_num = _parse_page_num(page_num)
    page_size = _parse_page_size(page_size)
    asn_like = '%%' if asn in [None, ''] else f'%{asn.strip()}%'

    record_count = count_latest_vp_resources(conn=conn, table_name=table_name, asn_like=asn_like)
    total_page = max(1, (record_count + page_size - 1) // page_size)
    rows = list_latest_vp_resources(
        conn=conn,
        table_name=table_name,
        asn_like=asn_like,
        page_num=page_num,
        page_size=page_size,
    )

    data = []
    for row in rows:
        as_rank = row.get('as_rank')
        data.append({
            'asn': row.get('asn', ''),
            'as_name': row.get('as_name', ''),
            'as_rank': as_rank if as_rank is not None else '',
            'ipv4_prefixes': int(row.get('ipv4_prefix_count') or 0),
            'ipv6_prefixes': int(row.get('ipv6_prefix_count') or 0),
            'latest_time': row.get('latest_time', ''),
            'status': '异常' if row.get('is_outlier') else '正常',
        })

    return {
        'total_page': total_page,
        'record_count': record_count,
        'data': data,
    }
