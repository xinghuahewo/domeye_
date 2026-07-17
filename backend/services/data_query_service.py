import csv
import datetime
import io
import ipaddress
import json
import os
import re
import shutil
import uuid
from collections import defaultdict
from typing import Any
from urllib.parse import quote

import pandas as pd
from flask import make_response
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from werkzeug.utils import secure_filename

from config.config import BASE_DIR, BIG_COUNTRY, COUNTRY_INFO_FILE, PFX2AS_DICT_FILE
from config.database import conn_11
from utils.data_loader import as_info, prefix_info


TASK_STORAGE_DIR = os.path.join(os.path.dirname(BASE_DIR), 'data', 'query_tasks')
TASK_METADATA_FILE = 'task.json'
TASK_RECORDS_FILE = 'records.json'
TASK_UPLOAD_FILE_PREFIX = 'upload_source'
PREVIEW_ROW_LIMIT = 5
DATA_QUERY_TASK_VERSION = 3
PREVIEW_CACHE_DIR = 'preview_cache'

CANONICAL_FIELDS = [
    'asn',
    'prefix',
    'ip',
    'country',
    'source',
    's_time',
    'e_time',
    'event_type',
    'level',
    'as_name',
    'org_name',
]

FIELD_ALIASES = {
    'asn': ['asn', 'asn号', 'as号', 'asnumber', 'as_number', '自治域', '自治域号'],
    'prefix': ['prefix', 'ipprefix', '前缀', '网段'],
    'ip': ['ip', 'ip地址', 'ip来源', '来源ip', '源ip', '源地址', 'ipsource', 'sourceip'],
    'country': ['country', 'countryname', '国家', '国家地区', '国家/地区'],
    'source': ['source', '数据源', '来源'],
    's_time': ['s_time', 'stime', 'starttime', '开始时间', '起始时间', '开始时刻'],
    'e_time': ['e_time', 'etime', 'endtime', '结束时间', '终止时间', '结束时刻'],
    'event_type': ['event_type', 'eventtype', '事件类型', '类型'],
    'level': ['level', '等级', '级别', '事件等级'],
    'as_name': ['as_name', 'asname', 'as名称', '自治域名称'],
    'org_name': ['org_name', 'orgname', '组织名称', '机构名称', '组织', '机构'],
}

FIELD_MATCH_RULES = {
    'asn': [
        {'all': ['asn'], 'score': 12},
        {'all': ['asnumber'], 'score': 12},
        {'all': ['asnum'], 'score': 10},
        {'all': ['自治域'], 'score': 12},
        {'all': ['自治系统'], 'score': 12},
        {'all': ['编号'], 'score': 3},
    ],
    'prefix': [
        {'all': ['prefix'], 'score': 12},
        {'all': ['前缀'], 'score': 12},
        {'all': ['网段'], 'score': 10},
        {'all': ['cidr'], 'score': 10},
        {'all': ['子网'], 'score': 8},
    ],
    'ip': [
        {'all': ['ip'], 'score': 12},
        {'all': ['ipv4'], 'score': 12},
        {'all': ['ipv6'], 'score': 12},
        {'all': ['地址'], 'score': 4},
        {'all': ['来源地'], 'score': 5},
        {'all': ['归属地'], 'score': 5},
    ],
    'country': [
        {'all': ['country'], 'score': 12},
        {'all': ['国家'], 'score': 12},
        {'all': ['地区'], 'score': 6},
        {'all': ['区域'], 'score': 5},
        {'all': ['地域'], 'score': 5},
    ],
    'source': [
        {'all': ['source'], 'score': 12},
        {'all': ['数据源'], 'score': 12},
        {'all': ['来源'], 'score': 6},
        {'all': ['collector'], 'score': 10},
        {'all': ['采集点'], 'score': 10},
        {'all': ['采集'], 'score': 6},
    ],
    's_time': [
        {'all': ['stime'], 'score': 12},
        {'all': ['starttime'], 'score': 12},
        {'all': ['开始'], 'score': 10},
        {'all': ['起始'], 'score': 10},
        {'all': ['start'], 'score': 8},
        {'all': ['begin'], 'score': 8},
        {'all': ['时间'], 'score': 3},
        {'all': ['日期'], 'score': 2},
    ],
    'e_time': [
        {'all': ['etime'], 'score': 12},
        {'all': ['endtime'], 'score': 12},
        {'all': ['结束'], 'score': 10},
        {'all': ['终止'], 'score': 10},
        {'all': ['截止'], 'score': 10},
        {'all': ['end'], 'score': 8},
        {'all': ['stop'], 'score': 8},
        {'all': ['时间'], 'score': 3},
        {'all': ['日期'], 'score': 2},
    ],
    'event_type': [
        {'all': ['eventtype'], 'score': 12},
        {'all': ['事件类型'], 'score': 12},
        {'all': ['事件'], 'score': 4},
        {'all': ['类型'], 'score': 4},
    ],
    'level': [
        {'all': ['level'], 'score': 12},
        {'all': ['等级'], 'score': 12},
        {'all': ['级别'], 'score': 12},
        {'all': ['风险'], 'score': 6},
    ],
    'as_name': [
        {'all': ['asname'], 'score': 12},
        {'all': ['as名称'], 'score': 12},
        {'all': ['自治域名称'], 'score': 12},
        {'all': ['名称'], 'score': 3},
    ],
    'org_name': [
        {'all': ['orgname'], 'score': 12},
        {'all': ['组织名称'], 'score': 12},
        {'all': ['机构名称'], 'score': 12},
        {'all': ['组织'], 'score': 7},
        {'all': ['机构'], 'score': 7},
        {'all': ['单位'], 'score': 5},
        {'all': ['公司'], 'score': 4},
        {'all': ['运营商'], 'score': 4},
    ],
}

SOURCE_VALUE_ALIASES = {'r', 'rv', 'ripe', 'routeviews', 'route-views', 'route_views', 'collect', 'global'}

FAMILY_CONFIG = {
    'as_info': {
        'label': 'AS基础信息',
        'required_fields': {'asn'},
        'exclude_fields': {'asn'},
        'reason': '通过 ASN 关联到 AS 基础信息表',
    },
    'event_table': {
        'label': '事件总表',
        'required_fields': {'event_type', 'source', 's_time'},
        'exclude_fields': {'source', 'event_type', 'level', 's_time', 'e_time'},
        'reason': '通过事件类型、数据源和开始时间关联到事件总表',
    },
    'as_outage': {
        'label': 'AS中断',
        'required_fields': {'asn', 'source', 's_time'},
        'exclude_fields': {'source', 'asn', 'country', 's_time', 'e_time', 'outage_id'},
        'reason': '通过 ASN、数据源和开始时间关联到 AS 中断表',
    },
    'prefix_outage': {
        'label': 'Prefix中断',
        'required_fields': {'prefix', 'source', 's_time'},
        'exclude_fields': {'source', 'prefix', 'asn', 'country', 's_time', 'e_time', 'outage_id'},
        'reason': '通过 Prefix、数据源和开始时间关联到 Prefix 中断表',
    },
    'country_outage': {
        'label': '国家中断',
        'required_fields': {'country', 'source', 's_time'},
        'exclude_fields': {'source', 'country', 's_time', 'e_time', 'outage_id'},
        'reason': '通过国家、数据源和开始时间关联到国家中断表',
    },
    'feature_asn': {
        'label': 'AS时序特征',
        'required_fields': {'asn', 's_time'},
        'exclude_fields': {'t', 'source', 'asn', 'country'},
        'reason': '通过 ASN 和时间关联到 AS 时序特征表',
    },
    'feature_country': {
        'label': '国家时序特征',
        'required_fields': {'country', 's_time'},
        'exclude_fields': {'t', 'source', 'country'},
        'reason': '通过国家和时间关联到国家时序特征表',
    },
}

_SCHEMA_CACHE = None
_PREFIX_NETWORK_CACHE = None
_COUNTRY_NAME_CACHE = None
_COUNTRY_QUERY_NAME_CACHE = None
_PFX2AS_PREFIX_CACHE = None
_PFX2AS_NETWORK_CACHE = None

CONTEXT_EXPANSION_FAMILIES = [
    'prefix_outage',
    'as_outage',
    'as_info',
]

FAMILY_CONTEXT_FIELD_MAP = {
    'as_info': {
        'country': ['as_country_cn', 'as_country'],
        'org_name': ['org_name_cn', 'org_name'],
        'as_name': ['as_name_cn', 'as_name'],
    },
    'as_outage': {
        'asn': ['asn'],
        'country': ['country'],
        'as_name': ['as_name'],
        'org_name': ['org_name'],
        'source': ['source'],
        's_time': ['s_time'],
        'e_time': ['e_time'],
    },
    'prefix_outage': {
        'prefix': ['prefix'],
        'asn': ['asn'],
        'country': ['country'],
        'as_name': ['as_name'],
        'org_name': ['org_name'],
        'source': ['source'],
        's_time': ['s_time'],
        'e_time': ['e_time'],
    },
    'country_outage': {
        'country': ['country', 'country_chinese_name'],
        'source': ['source'],
        's_time': ['s_time'],
        'e_time': ['e_time'],
    },
}

FIELD_LABELS = {
    'asn': 'ASN',
    'prefix': 'Prefix',
    'ip': 'IP',
    'country': '国家',
    'source': '数据源',
    's_time': '开始时间',
    'e_time': '结束时间',
    'event_type': '事件类型',
    'level': '事件等级',
    'as_name': 'AS名称',
    'org_name': '机构名称',
}

FAMILY_QUERY_FIELDS = {
    'as_info': ['asn'],
    'feature_country': ['country', 'source', 's_time'],
    'feature_asn': ['asn', 'country', 'source', 's_time'],
    'event_table': ['event_type', 'source', 's_time', 'prefix', 'asn', 'country', 'org_name'],
    'as_outage': ['asn', 'country', 'source', 's_time'],
    'prefix_outage': ['prefix', 'asn', 'country', 'source', 's_time'],
    'country_outage': ['country', 'source', 's_time'],
}

FIELD_DERIVATION_RULES = [
    ({'ip'}, {'prefix', 'asn', 'country', 'source'}),
    ({'prefix'}, {'asn', 'country', 'source'}),
    ({'asn'}, {'country', 'as_name', 'org_name'}),
]

SUPPLEMENT_FIELD_ORDER = [
    'prefix',
    'asn',
    'country',
    'source',
    'as_name',
    'org_name',
]


def _utc_now_string() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _normalize_column_name(name: Any) -> str:
    return re.sub(r'[\s_\-./()]+', '', str(name or '').strip().lower())


def _tokenize_column_name(name: Any) -> set[str]:
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', str(name or '')).lower()
    tokens = re.findall(r'[a-z]+|\d+|[\u4e00-\u9fff]+', text)
    return {token for token in tokens if token}


_ALIAS_TO_FIELD = {}
for field_name, aliases in FIELD_ALIASES.items():
    for alias in aliases + [field_name]:
        _ALIAS_TO_FIELD[_normalize_column_name(alias)] = field_name


def _ensure_storage_dir():
    os.makedirs(TASK_STORAGE_DIR, exist_ok=True)


def _task_dir(task_id: str) -> str:
    return os.path.join(TASK_STORAGE_DIR, task_id)


def _task_metadata_path(task_id: str) -> str:
    return os.path.join(_task_dir(task_id), TASK_METADATA_FILE)


def _task_records_path(task_id: str) -> str:
    return os.path.join(_task_dir(task_id), TASK_RECORDS_FILE)


def _task_upload_path(task_id: str, stored_file_name: str) -> str:
    return os.path.join(_task_dir(task_id), stored_file_name)


def _preview_cache_dir(task_id: str) -> str:
    return os.path.join(_task_dir(task_id), PREVIEW_CACHE_DIR)


def _serialize_value(value: Any) -> Any:
    if value is None:
        return ''
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return ''
        return value.to_pydatetime().strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, datetime.datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, datetime.date):
        return value.strftime('%Y-%m-%d')
    if pd.isna(value):
        return ''
    return str(value).strip()


def _serialize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _serialize_value(value) for key, value in row.items()} for row in rows]


def _deduplicate_columns(columns: list[Any]) -> list[str]:
    counts = defaultdict(int)
    deduplicated = []
    for raw_name in columns:
        base_name = str(raw_name).strip() or 'unnamed'
        counts[base_name] += 1
        deduplicated.append(base_name if counts[base_name] == 1 else f'{base_name}_{counts[base_name]}')
    return deduplicated


def _sanitize_upload_filename(original: str) -> str:
    """Sanitize basename with secure_filename but preserve the client's file extension.

    secure_filename strips non-ASCII; e.g. 伊朗源地址情况.xlsx becomes xlsx without a dot,
    which breaks extension detection in _read_dataframe_from_payload.
    """
    raw = original or ''
    base, ext = os.path.splitext(raw)
    ext = ext.lower()
    safe_base = secure_filename(base) or 'upload'
    return f'{safe_base}{ext}'


def _read_dataframe_from_payload(file_name: str, payload: bytes) -> pd.DataFrame:
    suffix = os.path.splitext(file_name)[1].lower()
    if suffix in ['.xlsx', '.xls']:
        dataframe = pd.read_excel(io.BytesIO(payload), dtype=object)
    elif suffix == '.csv':
        dataframe = None
        for encoding in ['utf-8-sig', 'utf-8', 'gbk']:
            try:
                dataframe = pd.read_csv(io.BytesIO(payload), dtype=object, encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        if dataframe is None:
            raise ValueError('CSV 文件编码无法识别，请使用 UTF-8 或 GBK')
    else:
        raise ValueError('仅支持上传 .csv、.xls、.xlsx 文件')

    dataframe.columns = _deduplicate_columns(list(dataframe.columns))
    return dataframe.fillna('')


def _read_uploaded_dataframe(file_storage) -> tuple[pd.DataFrame, bytes, str]:
    file_name = _sanitize_upload_filename(file_storage.filename or '')
    payload = file_storage.read()
    file_storage.stream.seek(0)
    dataframe = _read_dataframe_from_payload(file_name, payload)
    return dataframe, payload, file_name


def _sample_values_for_column(dataframe: pd.DataFrame, column_name: str, limit=5) -> list[str]:
    return _unique_ordered(
        [_serialize_value(value) for value in dataframe[column_name].tolist() if _serialize_value(value)]
    )[:limit]


def _keyword_present(keyword: str, compact_name: str, tokens: set[str]) -> bool:
    normalized_keyword = _normalize_column_name(keyword)
    if not normalized_keyword:
        return False
    if re.search(r'[\u4e00-\u9fff]', normalized_keyword):
        return normalized_keyword in compact_name
    if len(normalized_keyword) <= 2:
        return normalized_keyword in tokens
    return normalized_keyword in compact_name or normalized_keyword in tokens


def _match_exact_alias(column_name: str) -> str | None:
    return _ALIAS_TO_FIELD.get(_normalize_column_name(column_name))


def _score_field_by_name(field_name: str, compact_name: str, tokens: set[str]) -> int:
    score = 0
    for alias in FIELD_ALIASES.get(field_name, []):
        alias_normalized = _normalize_column_name(alias)
        if alias_normalized and alias_normalized in compact_name:
            score = max(score, min(14, len(alias_normalized) + 4))

    for rule in FIELD_MATCH_RULES.get(field_name, []):
        if all(_keyword_present(keyword, compact_name, tokens) for keyword in rule['all']):
            score += rule['score']
    return score


def _looks_like_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except Exception:
        return False


def _looks_like_prefix(value: str) -> bool:
    if '/' not in value:
        return False
    try:
        ipaddress.ip_network(value, strict=False)
        return True
    except Exception:
        return False


def _looks_like_asn(value: str) -> bool:
    return bool(re.fullmatch(r'(AS)?\d+(\.0)?', str(value or '').strip(), re.IGNORECASE))


def _looks_like_datetime(value: str) -> bool:
    parsed = pd.to_datetime(value, errors='coerce')
    return parsed is not None and not pd.isna(parsed)


def _looks_like_source_value(value: str) -> bool:
    normalized = _normalize_column_name(value)
    return bool(normalized) and normalized in SOURCE_VALUE_ALIASES


def _score_field_by_samples(sample_values: list[str]) -> dict[str, int]:
    if not sample_values:
        return {}

    counts = {
        'prefix': sum(_looks_like_prefix(value) for value in sample_values),
        'ip': sum(_looks_like_ip(value) for value in sample_values),
        'asn': sum(_looks_like_asn(value) for value in sample_values),
        'time': sum(_looks_like_datetime(value) for value in sample_values),
        'source': sum(_looks_like_source_value(value) for value in sample_values),
    }
    total = len(sample_values)
    boosts = {}

    if counts['prefix'] / total >= 0.6:
        boosts['prefix'] = 10
    if counts['ip'] / total >= 0.6:
        boosts['ip'] = 10
    if counts['asn'] / total >= 0.6:
        boosts['asn'] = 9
    if counts['time'] / total >= 0.6:
        boosts['s_time'] = 5
        boosts['e_time'] = 5
    if counts['source'] / total >= 0.6:
        boosts['source'] = 7

    return boosts


def _match_standard_field(column_name: str, sample_values: list[str]) -> tuple[str | None, str | None, int]:
    exact_match = _match_exact_alias(column_name)
    if exact_match:
        return exact_match, 'exact_alias', 100

    compact_name = _normalize_column_name(column_name)
    tokens = _tokenize_column_name(column_name)
    sample_scores = _score_field_by_samples(sample_values)

    scored_fields = []
    for field_name in CANONICAL_FIELDS:
        total_score = _score_field_by_name(field_name, compact_name, tokens) + sample_scores.get(field_name, 0)
        if total_score > 0:
            scored_fields.append((field_name, total_score))

    if not scored_fields:
        return None, None, 0

    scored_fields.sort(key=lambda item: item[1], reverse=True)
    best_field, best_score = scored_fields[0]
    second_score = scored_fields[1][1] if len(scored_fields) > 1 else 0

    if best_score < 8 or best_score - second_score < 2:
        return None, None, 0

    match_method = 'keyword_rule'
    if sample_scores.get(best_field, 0) and best_score == sample_scores.get(best_field, 0):
        match_method = 'sample_inference'
    elif sample_scores.get(best_field, 0):
        match_method = 'keyword_and_sample'

    confidence = min(100, best_score * 5)
    return best_field, match_method, confidence


def _detect_upload_columns(dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    result = []
    for column_name in dataframe.columns:
        sample_values = _sample_values_for_column(dataframe, column_name, limit=3)
        standard_field, match_method, confidence = _match_standard_field(column_name, sample_values)
        result.append(
            {
                'original_name': column_name,
                'standard_field': standard_field,
                'matched': bool(standard_field),
                'match_method': match_method,
                'confidence': confidence,
                'sample_values': sample_values,
            }
        )
    return result


def _unique_ordered(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _classify_table_family(table_name: str) -> str | None:
    if table_name == 'as_info':
        return 'as_info'
    if table_name == 'feature_country':
        return 'feature_country'
    if table_name.startswith('event_table_'):
        return 'event_table'
    if table_name.startswith('as_outage_'):
        return 'as_outage'
    if table_name.startswith('prefix_outage_'):
        return 'prefix_outage'
    if table_name.startswith('country_outage_'):
        return 'country_outage'
    if re.match(r'^feature_[a-z]+_\d{6}$', table_name):
        return 'feature_asn'
    if re.match(r'^feature_other_\d{6}$', table_name):
        return 'feature_asn'
    return None


def _normalize_asn_from_prefix_meta(value: Any) -> str:
    raw_value = str(value or '').strip()
    if not raw_value:
        return ''
    raw_value = re.split(r'[,|_/]', raw_value, maxsplit=1)[0].strip()
    raw_value = re.sub(r'^(AS)', '', raw_value, flags=re.IGNORECASE).strip()
    if raw_value.isdigit():
        return raw_value
    match = re.search(r'\d+', raw_value)
    return match.group(0) if match else raw_value


def _pfx2as_quality(raw_asn: Any) -> int:
    return 2 if len(re.findall(r'\d+', str(raw_asn or ''))) <= 1 else 1


def _load_pfx2as_prefix_map() -> dict[str, dict[str, str]]:
    global _PFX2AS_PREFIX_CACHE
    if _PFX2AS_PREFIX_CACHE is not None:
        return _PFX2AS_PREFIX_CACHE

    try:
        with open(PFX2AS_DICT_FILE, 'r', encoding='utf-8') as file:
            raw_mapping = json.load(file)
    except Exception:
        _PFX2AS_PREFIX_CACHE = {}
        return _PFX2AS_PREFIX_CACHE

    prefix_map = {}
    quality_map = {}
    for raw_asn, prefixes in raw_mapping.items():
        if not isinstance(prefixes, dict):
            continue
        asn = _normalize_asn_from_prefix_meta(raw_asn)
        if not asn:
            continue
        quality = _pfx2as_quality(raw_asn)
        for prefix in prefixes.keys():
            prefix_key = str(prefix or '').strip()
            if not prefix_key:
                continue
            if quality <= quality_map.get(prefix_key, 0):
                continue
            prefix_map[prefix_key] = {'asn': asn}
            quality_map[prefix_key] = quality

    _PFX2AS_PREFIX_CACHE = prefix_map
    return _PFX2AS_PREFIX_CACHE


def _iter_network_bits(network: ipaddress._BaseNetwork):
    max_prefixlen = network.max_prefixlen
    network_value = int(network.network_address)
    for shift in range(max_prefixlen - 1, max_prefixlen - network.prefixlen - 1, -1):
        yield (network_value >> shift) & 1


def _iter_ip_bits(target_ip: ipaddress._BaseAddress):
    max_prefixlen = target_ip.max_prefixlen
    ip_value = int(target_ip)
    for shift in range(max_prefixlen - 1, -1, -1):
        yield (ip_value >> shift) & 1


def _build_prefix_trie(prefix_map: dict[str, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    roots = {
        4: {},
        6: {},
    }
    for prefix, meta in prefix_map.items():
        try:
            network = ipaddress.ip_network(str(prefix).strip(), strict=False)
        except Exception:
            continue
        node = roots[network.version]
        for bit in _iter_network_bits(network):
            node = node.setdefault(str(bit), {})
        node['_value'] = {
            'prefix': str(network),
            'meta': meta,
        }
    return roots


def _get_prefix_info_trie() -> dict[int, dict[str, Any]]:
    global _PREFIX_NETWORK_CACHE
    if _PREFIX_NETWORK_CACHE is None:
        _PREFIX_NETWORK_CACHE = _build_prefix_trie(prefix_info)
    return _PREFIX_NETWORK_CACHE


def _get_pfx2as_trie() -> dict[int, dict[str, Any]]:
    global _PFX2AS_NETWORK_CACHE
    if _PFX2AS_NETWORK_CACHE is None:
        _PFX2AS_NETWORK_CACHE = _build_prefix_trie(_load_pfx2as_prefix_map())
    return _PFX2AS_NETWORK_CACHE


def _find_longest_prefix_match_for_ip(
    target_ip: ipaddress._BaseAddress,
    trie_roots: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    node = trie_roots.get(target_ip.version) or {}
    best_match = node.get('_value')
    for bit in _iter_ip_bits(target_ip):
        node = node.get(str(bit))
        if node is None:
            break
        if '_value' in node:
            best_match = node['_value']
    return best_match or {}


def _find_longest_prefix_match_for_network(
    target_network: ipaddress._BaseNetwork,
    trie_roots: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    node = trie_roots.get(target_network.version) or {}
    best_match = node.get('_value')
    for bit in _iter_network_bits(target_network):
        node = node.get(str(bit))
        if node is None:
            break
        if '_value' in node:
            best_match = node['_value']
    return best_match or {}


def _resolve_ip_metadata(ip_value: str) -> dict[str, str]:
    try:
        target_ip = ipaddress.ip_address(str(ip_value).strip())
    except Exception:
        return {}

    prefix_info_match = _find_longest_prefix_match_for_ip(target_ip, _get_prefix_info_trie())
    if prefix_info_match:
        meta = prefix_info_match.get('meta') or {}
        asn = _normalize_asn_from_prefix_meta(meta.get('bgp') or meta.get('route'))
        return {
            'prefix': prefix_info_match['prefix'],
            'asn': asn,
            'country': str(meta.get('country') or '').strip(),
            'source': str(meta.get('source') or '').strip(),
            '_origin_label': 'IP归属信息',
        }

    fallback_match = _find_longest_prefix_match_for_ip(target_ip, _get_pfx2as_trie())
    if fallback_match:
        fallback_meta = fallback_match.get('meta') or {}
        return {
            'prefix': fallback_match['prefix'],
            'asn': str(fallback_meta.get('asn') or '').strip(),
            '_origin_label': 'Prefix-AS映射',
        }
    return {}


def _load_schema_catalog(conn=conn_11):
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is not None:
        return _SCHEMA_CACHE

    schema_catalog = {family: {'tables': [], 'fields': [], 'data_types': {}} for family in FAMILY_CONFIG}
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            """
            select table_name, column_name, data_type
            from information_schema.columns
            where table_schema = 'public'
            order by table_name, ordinal_position
            """
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()

    for row in rows:
        family = _classify_table_family(row['table_name'])
        if not family:
            continue
        family_item = schema_catalog[family]
        if row['table_name'] not in family_item['tables']:
            family_item['tables'].append(row['table_name'])
        if row['column_name'] not in family_item['fields']:
            family_item['fields'].append(row['column_name'])
        family_item['data_types'][row['column_name']] = row['data_type']

    for family_item in schema_catalog.values():
        family_item['tables'].sort(reverse=True)

    _SCHEMA_CACHE = schema_catalog
    return schema_catalog


def _candidate_families(recognized_fields: list[str]) -> list[str]:
    field_set = _expand_resolvable_fields(recognized_fields)
    families = []
    for family_name, config in FAMILY_CONFIG.items():
        if config['required_fields'].issubset(field_set):
            families.append(family_name)
    return families


def _expand_resolvable_fields(recognized_fields: list[str]) -> set[str]:
    resolved_fields = set(recognized_fields)
    changed = True
    while changed:
        changed = False
        for required_fields, derived_fields in FIELD_DERIVATION_RULES:
            if not required_fields.issubset(resolved_fields):
                continue
            new_fields = derived_fields - resolved_fields
            if not new_fields:
                continue
            resolved_fields.update(new_fields)
            changed = True
    return resolved_fields


def _build_resolution_hint(
    family_name: str,
    recognized_fields: list[str],
) -> tuple[str, list[str]]:
    recognized_set = set(recognized_fields)
    if family_name == 'as_info':
        if 'asn' in recognized_set:
            return 'ASN -> AS基础信息', ['asn']
        if 'prefix' in recognized_set:
            return 'Prefix -> Prefix归属信息/Prefix-AS映射 -> ASN -> AS基础信息', ['prefix']
        if 'ip' in recognized_set:
            return 'IP -> 前缀树匹配 -> Prefix -> ASN -> AS基础信息', ['ip']
    if family_name == 'feature_country':
        if 'country' in recognized_set and 's_time' in recognized_set:
            return '国家 + 时间 -> 国家时序特征', ['country', 's_time']
        if 'asn' in recognized_set and 's_time' in recognized_set:
            return 'ASN -> AS基础信息 -> 国家 + 时间 -> 国家时序特征', ['asn', 's_time']
        if 'prefix' in recognized_set and 's_time' in recognized_set:
            return 'Prefix -> ASN/国家 + 时间 -> 国家时序特征', ['prefix', 's_time']
        if 'ip' in recognized_set and 's_time' in recognized_set:
            return 'IP -> 前缀树匹配 -> Prefix/ASN/国家 + 时间 -> 国家时序特征', ['ip', 's_time']
    if family_name == 'feature_asn':
        if 'asn' in recognized_set and 's_time' in recognized_set:
            return 'ASN + 时间 -> AS时序特征', ['asn', 's_time']
        if 'prefix' in recognized_set and 's_time' in recognized_set:
            return 'Prefix -> ASN + 时间 -> AS时序特征', ['prefix', 's_time']
        if 'ip' in recognized_set and 's_time' in recognized_set:
            return 'IP -> 前缀树匹配 -> Prefix -> ASN + 时间 -> AS时序特征', ['ip', 's_time']
    if family_name == 'as_outage':
        if {'asn', 'source', 's_time'}.issubset(recognized_set):
            return 'ASN + 数据源 + 时间 -> AS中断', ['asn', 'source', 's_time']
        if {'prefix', 'source', 's_time'}.issubset(recognized_set):
            return 'Prefix -> ASN + 数据源 + 时间 -> AS中断', ['prefix', 'source', 's_time']
        if {'ip', 'source', 's_time'}.issubset(recognized_set):
            return 'IP -> 前缀树匹配 -> Prefix -> ASN + 数据源 + 时间 -> AS中断', ['ip', 'source', 's_time']
    if family_name == 'prefix_outage':
        if {'prefix', 'source', 's_time'}.issubset(recognized_set):
            return 'Prefix + 数据源 + 时间 -> Prefix中断', ['prefix', 'source', 's_time']
        if {'ip', 'source', 's_time'}.issubset(recognized_set):
            return 'IP -> 前缀树匹配 -> Prefix + 数据源 + 时间 -> Prefix中断', ['ip', 'source', 's_time']
    if family_name == 'country_outage':
        if {'country', 'source', 's_time'}.issubset(recognized_set):
            return '国家 + 数据源 + 时间 -> 国家中断', ['country', 'source', 's_time']
        if {'asn', 'source', 's_time'}.issubset(recognized_set):
            return 'ASN -> AS基础信息 -> 国家 + 数据源 + 时间 -> 国家中断', ['asn', 'source', 's_time']
        if {'prefix', 'source', 's_time'}.issubset(recognized_set):
            return 'Prefix -> 国家 + 数据源 + 时间 -> 国家中断', ['prefix', 'source', 's_time']
        if {'ip', 'source', 's_time'}.issubset(recognized_set):
            return 'IP -> 前缀树匹配 -> Prefix/国家 + 数据源 + 时间 -> 国家中断', ['ip', 'source', 's_time']
    return FAMILY_CONFIG[family_name]['reason'], sorted(FAMILY_CONFIG[family_name]['required_fields'])


def _build_database_field_candidates(recognized_fields: list[str], conn=conn_11) -> list[dict[str, Any]]:
    recognized_set = set(recognized_fields)
    resolvable_fields = _expand_resolvable_fields(recognized_fields)
    candidates = []
    for field_name in SUPPLEMENT_FIELD_ORDER:
        if field_name in recognized_set or field_name not in resolvable_fields:
            continue
        resolution_hint, resolution_entry_fields = _build_standard_field_resolution_hint(field_name, recognized_fields)
        candidates.append(
            {
                'field_id': f'canonical.{field_name}',
                'field_name': field_name,
                'table_family': '',
                'table_label': '标准字段补全',
                'label': field_name,
                'reason': f'补全标准字段 {_field_label(field_name)}',
                'resolution_hint': resolution_hint,
                'resolution_entry_fields': resolution_entry_fields,
                'data_type': 'text',
                'source_kind': 'canonical',
            }
        )
    return candidates


def _build_standard_field_resolution_hint(
    field_name: str,
    recognized_fields: list[str],
) -> tuple[str, list[str]]:
    recognized_set = set(recognized_fields)
    if field_name == 'prefix':
        if 'ip' in recognized_set:
            return 'IP -> Prefix', ['ip']
    if field_name == 'asn':
        if 'prefix' in recognized_set:
            return 'Prefix -> ASN', ['prefix']
        if 'ip' in recognized_set:
            return 'IP -> Prefix -> ASN', ['ip']
    if field_name == 'country':
        if 'asn' in recognized_set:
            return 'ASN -> AS基础信息 -> 国家', ['asn']
        if 'prefix' in recognized_set:
            return 'Prefix -> ASN/国家', ['prefix']
        if 'ip' in recognized_set:
            return 'IP -> Prefix/ASN/国家', ['ip']
    if field_name == 'source':
        if 'prefix' in recognized_set:
            return 'Prefix -> 数据源', ['prefix']
        if 'ip' in recognized_set:
            return 'IP -> 数据源', ['ip']
    if field_name == 'as_name':
        if 'asn' in recognized_set:
            return 'ASN -> AS基础信息 -> AS名称', ['asn']
        if 'prefix' in recognized_set:
            return 'Prefix -> ASN -> AS基础信息 -> AS名称', ['prefix']
        if 'ip' in recognized_set:
            return 'IP -> Prefix -> ASN -> AS基础信息 -> AS名称', ['ip']
    if field_name == 'org_name':
        if 'asn' in recognized_set:
            return 'ASN -> AS基础信息 -> 机构名称', ['asn']
        if 'prefix' in recognized_set:
            return 'Prefix -> ASN -> AS基础信息 -> 机构名称', ['prefix']
        if 'ip' in recognized_set:
            return 'IP -> Prefix -> ASN -> AS基础信息 -> 机构名称', ['ip']
    return f'补全标准字段 {_field_label(field_name)}', [field_name]


def _build_upload_column_preview(dataframe: pd.DataFrame, detected_columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preview = []
    for item in detected_columns:
        if item.get('sample_values'):
            preview.append(item)
            continue
        preview.append({**item, 'sample_values': _sample_values_for_column(dataframe, item['original_name'], limit=3)})
    return preview


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    matched_database_fields = task.get('matched_database_fields', [])
    return {
        'task_id': task['task_id'],
        'task_name': task['task_name'],
        'file_name': task['file_name'],
        'row_count': task['row_count'],
        'recognized_fields': task['recognized_fields'],
        'matched_field_count': len(matched_database_fields),
        'matched_database_fields': [
            {
                'field_id': item.get('field_id', ''),
                'field_name': item.get('field_name', ''),
                'label': item.get('label') or item.get('field_name', ''),
                'table_label': item.get('table_label', ''),
                'sample_hit_count': item.get('sample_hit_count', 0),
                'sample_total_count': item.get('sample_total_count', 0),
            }
            for item in matched_database_fields
        ],
        'selected_field_ids': task.get('selected_field_ids', []),
        'status': task['status'],
        'created_at': task['created_at'],
        'generated_at': task.get('generated_at', ''),
    }


def _write_json(path: str, payload: Any):
    with open(path, 'w', encoding='utf-8') as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def _read_json(path: str) -> Any:
    with open(path, 'r', encoding='utf-8') as file:
        return json.load(file)


def create_data_query_task(file_storage, task_name=None, conn=conn_11):
    if file_storage is None or not getattr(file_storage, 'filename', ''):
        return {'status': False, 'msg': '请先上传文件'}, 400

    _ensure_storage_dir()
    try:
        dataframe, payload, safe_file_name = _read_uploaded_dataframe(file_storage)
    except ValueError as error:
        return {'status': False, 'msg': str(error)}, 400
    except Exception:
        return {'status': False, 'msg': '文件解析失败，请检查文件格式'}, 400

    # 展示用文件名 / 默认任务名：保留用户原始名（含中文）；safe_file_name 仅用于扩展名校验等，无中文 stem 时会变成 upload.*
    original_basename = os.path.basename((file_storage.filename or '').strip())
    display_file_name = original_basename if original_basename else safe_file_name
    display_stem = os.path.splitext(display_file_name)[0]
    default_task_name = display_stem or os.path.splitext(safe_file_name)[0] or '数据查询任务'
    trimmed_task_name = task_name.strip() if isinstance(task_name, str) and task_name.strip() else None

    task_id = f"{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    task_dir = _task_dir(task_id)
    os.makedirs(task_dir, exist_ok=True)

    detected_columns = _detect_upload_columns(dataframe)
    upload_columns = _build_upload_column_preview(dataframe, detected_columns)
    recognized_fields = _unique_ordered(
        [item['standard_field'] for item in upload_columns if item.get('standard_field')]
    )
    preview_rows = _serialize_rows(dataframe.head(PREVIEW_ROW_LIMIT).to_dict(orient='records'))
    matched_database_fields = _annotate_database_field_candidates(
        upload_columns,
        preview_rows,
        _build_database_field_candidates(recognized_fields, conn=conn),
        conn=conn,
    )
    upload_suffix = os.path.splitext(safe_file_name)[1].lower()
    stored_file_name = f'{TASK_UPLOAD_FILE_PREFIX}{upload_suffix or ".bin"}'
    with open(_task_upload_path(task_id, stored_file_name), 'wb') as upload_file:
        upload_file.write(payload)

    task = {
        'task_id': task_id,
        'task_name': trimmed_task_name or default_task_name,
        'task_version': DATA_QUERY_TASK_VERSION,
        'file_name': display_file_name,
        'status': 'parsed',
        'created_at': _utc_now_string(),
        'generated_at': '',
        'row_count': int(len(dataframe)),
        'stored_file_name': stored_file_name,
        'original_columns': list(dataframe.columns),
        'recognized_fields': recognized_fields,
        'upload_columns': upload_columns,
        'preview_rows': preview_rows,
        'matched_database_fields': matched_database_fields,
        'selected_field_ids': [],
    }

    _write_json(_task_metadata_path(task_id), task)
    return {'status': True, 'data': task}


def list_data_query_tasks():
    _ensure_storage_dir()
    task_summaries = []
    for task_id in os.listdir(TASK_STORAGE_DIR):
        metadata_path = _task_metadata_path(task_id)
        if not os.path.exists(metadata_path):
            continue
        try:
            task = _read_json(metadata_path)
            try:
                task = _refresh_task_metadata(task)
            except Exception:
                pass
            task_summaries.append(_task_summary(task))
        except Exception:
            continue
    task_summaries.sort(key=lambda item: item['created_at'], reverse=True)
    return {'status': True, 'data': task_summaries}


def _task_requires_refresh(task: dict[str, Any]) -> bool:
    if task.get('task_version') != DATA_QUERY_TASK_VERSION:
        return True
    return bool(task.get('matched_database_fields')) and any(
        'sample_hit_count' not in item
        or 'sample_total_count' not in item
        or 'sample_value_examples' not in item
        or 'sample_trace_examples' not in item
        or item.get('sample_trace_examples') is None
        or 'resolution_hint' not in item
        or 'resolution_entry_fields' not in item
        for item in task['matched_database_fields']
    )


def _refresh_task_metadata(task: dict[str, Any]) -> dict[str, Any]:
    if not _task_requires_refresh(task):
        return task

    records = _load_task_records(task)
    task['matched_database_fields'] = _annotate_database_field_candidates(
        task.get('upload_columns', []),
        records,
        _build_database_field_candidates(task.get('recognized_fields', [])),
    )
    task['task_version'] = DATA_QUERY_TASK_VERSION
    preview_cache_dir = _preview_cache_dir(task['task_id'])
    if os.path.exists(preview_cache_dir):
        shutil.rmtree(preview_cache_dir, ignore_errors=True)
    _update_task_metadata(task)
    return task


def get_data_query_task_detail(task_id: str):
    metadata_path = _task_metadata_path(task_id)
    if not os.path.exists(metadata_path):
        return {'status': False, 'msg': '任务不存在'}, 404
    task = _read_json(metadata_path)
    try:
        task = _refresh_task_metadata(task)
    except Exception:
        pass
    return {'status': True, 'data': task}


def delete_data_query_task(task_id: str):
    task_dir = _task_dir(task_id)
    metadata_path = _task_metadata_path(task_id)
    if not os.path.exists(task_dir) or not os.path.exists(metadata_path):
        return {'status': False, 'msg': '任务不存在'}, 404

    try:
        shutil.rmtree(task_dir)
    except Exception:
        return {'status': False, 'msg': '任务删除失败'}, 500

    return {'status': True, 'msg': '任务删除成功'}


def _parse_datetime(value: Any) -> datetime.datetime | None:
    if value in [None, '']:
        return None
    try:
        parsed = pd.to_datetime(value, errors='coerce')
    except Exception:
        return None
    if parsed is None or pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _normalize_asn(value: Any) -> str:
    raw_value = str(value or '').strip()
    if not raw_value:
        return ''
    normalized = raw_value.upper()
    if normalized.startswith('AS'):
        normalized = normalized[2:]
    if normalized.endswith('.0'):
        normalized = normalized[:-2]
    return normalized


def _normalize_prefix(value: Any) -> str:
    return str(value or '').strip()


def _has_value(value: Any) -> bool:
    return value not in [None, '', [], {}, ()]


def _merge_non_empty_fields(base_row: dict[str, Any], override_row: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base_row or {})
    for field_name, value in (override_row or {}).items():
        if _has_value(value):
            merged[field_name] = value
    return merged


def _resolve_local_as_info_row(asn_value: Any) -> dict[str, Any]:
    normalized_asn = _normalize_asn(asn_value)
    if not normalized_asn:
        return {}

    public_asn = normalized_asn.split('_', 1)[0]
    info_row = as_info.get(public_asn) or as_info.get(normalized_asn) or {}
    if not isinstance(info_row, dict) or not info_row:
        return {}

    local_row = {
        'asn': public_asn,
        'as_country_cn': str(info_row.get('as_country_cn') or '').strip(),
        'as_country': str(info_row.get('as_country') or '').strip(),
        'as_name_cn': str(info_row.get('as_name_cn') or '').strip(),
        'as_name': str(info_row.get('as_name') or '').strip(),
        'org_name_cn': str(info_row.get('org_name_cn') or '').strip(),
        'org_name': str(info_row.get('org_name') or '').strip(),
    }
    if not any(
        [
            local_row.get('as_country_cn') or local_row.get('as_country'),
            local_row.get('as_name_cn') or local_row.get('as_name'),
            local_row.get('org_name_cn') or local_row.get('org_name'),
        ]
    ):
        return {}
    return local_row


def _load_country_name_cache() -> dict[str, str]:
    global _COUNTRY_NAME_CACHE, _COUNTRY_QUERY_NAME_CACHE
    if _COUNTRY_NAME_CACHE is not None:
        return _COUNTRY_NAME_CACHE

    country_name_cache = {}
    country_query_name_cache = {}
    try:
        dataframe = pd.read_excel(COUNTRY_INFO_FILE, dtype=object).fillna('')
    except Exception:
        dataframe = pd.DataFrame()

    for row in dataframe.to_dict(orient='records'):
        chinese_name = _serialize_value(row.get('chinese_short_name'))
        country_code = str(row.get('two_letter_code') or '').strip().upper()
        if not chinese_name or not country_code:
            continue
        country_query_name_cache[country_code] = chinese_name
        for alias in [
            row.get('chinese_short_name'),
            row.get('english_short_name'),
            row.get('english_full_name'),
            row.get('two_letter_code'),
            row.get('three_letter_code'),
        ]:
            normalized_alias = _normalize_column_name(alias)
            if normalized_alias:
                country_name_cache[normalized_alias] = country_code

    for country_cn, country_code in BIG_COUNTRY.items():
        normalized_code = str(country_code or '').strip().upper()
        if not normalized_code:
            continue
        country_name_cache[_normalize_column_name(country_cn)] = normalized_code
        country_name_cache[_normalize_column_name(country_code)] = normalized_code
        country_query_name_cache.setdefault(normalized_code, country_cn)

    country_name_cache.update(
        {
            'russia': 'RU',
            'china': 'CN',
            'prc': 'CN',
            'usa': 'US',
            'unitedstates': 'US',
            'unitedstatesofamerica': 'US',
            'brazil': 'BR',
            'india': 'IN',
            'uk': 'GB',
            'unitedkingdom': 'GB',
            'britain': 'GB',
            'germany': 'DE',
            'indonesia': 'ID',
            'australia': 'AU',
            'poland': 'PL',
        }
    )
    country_name_cache['eu'] = 'EU'
    country_query_name_cache.setdefault('EU', '欧盟')
    _COUNTRY_NAME_CACHE = country_name_cache
    _COUNTRY_QUERY_NAME_CACHE = country_query_name_cache
    return _COUNTRY_NAME_CACHE


def _normalize_country(value: Any) -> str:
    raw_value = str(value or '').strip()
    if not raw_value:
        return ''
    return _load_country_name_cache().get(_normalize_column_name(raw_value), raw_value)


def _country_query_value(value: Any) -> str:
    normalized_country = _normalize_country(value)
    if not normalized_country:
        return ''
    _load_country_name_cache()
    return (_COUNTRY_QUERY_NAME_CACHE or {}).get(normalized_country, normalized_country)


def _normalize_source(value: Any) -> str:
    raw_value = str(value or '').strip()
    if not raw_value:
        return ''

    source_map = {
        'r': 'r',
        'rv': 'r',
        'ripe': 'r',
        'routeviews': 'r',
        'routeview': 'r',
        'routeviews2': 'r',
        'collect': 'collect',
        'global': 'collect',
    }
    return source_map.get(_normalize_column_name(raw_value), raw_value)


def _normalize_context_value(field_name: str, value: Any) -> str:
    if field_name == 'asn':
        return _normalize_asn(value)
    if field_name == 'prefix':
        return _normalize_prefix(value)
    if field_name == 'country':
        return _normalize_country(value)
    if field_name == 'source':
        return _normalize_source(value)
    return _serialize_value(value)


def _field_label(field_name: str) -> str:
    return FIELD_LABELS.get(field_name, field_name)


def _resolve_prefix_metadata(prefix_value: str) -> dict[str, str]:
    raw_prefix = str(prefix_value or '').strip()
    if not raw_prefix:
        return {}

    try:
        target_network = ipaddress.ip_network(raw_prefix, strict=False)
    except Exception:
        return {}

    exact_prefix = str(target_network)

    prefix_info_match = _find_longest_prefix_match_for_network(target_network, _get_prefix_info_trie())
    if prefix_info_match:
        exact_meta = prefix_info_match.get('meta') or {}
        return {
            'prefix': exact_prefix,
            'asn': _normalize_asn_from_prefix_meta(exact_meta.get('bgp') or exact_meta.get('route')),
            'country': str(exact_meta.get('country') or '').strip(),
            'source': str(exact_meta.get('source') or '').strip(),
            '_origin_label': 'Prefix归属信息',
        }

    fallback_match = _find_longest_prefix_match_for_network(target_network, _get_pfx2as_trie())
    if fallback_match:
        fallback_meta = fallback_match.get('meta') or {}
        return {
            'prefix': exact_prefix,
            'asn': str(fallback_meta.get('asn') or '').strip(),
            '_origin_label': 'Prefix-AS映射',
        }
    return {}


def _merge_context_fields(
    canonical_row: dict[str, str],
    updates: dict[str, Any],
) -> bool:
    changed = False
    for field_name, value in updates.items():
        if field_name not in CANONICAL_FIELDS:
            continue
        normalized_value = _normalize_context_value(field_name, value)
        if not normalized_value or canonical_row.get(field_name):
            continue
        canonical_row[field_name] = normalized_value
        changed = True
    return changed


def _merge_context_fields_with_origin(
    canonical_row: dict[str, str],
    origin_map: dict[str, dict[str, Any]],
    updates: dict[str, Any],
    origin_descriptor: dict[str, Any],
) -> bool:
    changed = False
    for field_name, value in updates.items():
        if field_name not in CANONICAL_FIELDS:
            continue
        normalized_value = _normalize_context_value(field_name, value)
        if not normalized_value or canonical_row.get(field_name):
            continue
        canonical_row[field_name] = normalized_value
        origin_map[field_name] = {
            **origin_descriptor,
            'field': field_name,
        }
        changed = True
    return changed


def _context_updates_from_family_row(family_name: str, row: dict[str, Any]) -> dict[str, Any]:
    if not row:
        return {}

    updates = {}
    for canonical_field, source_fields in FAMILY_CONTEXT_FIELD_MAP.get(family_name, {}).items():
        for source_field in source_fields:
            value = row.get(source_field)
            if value not in [None, '']:
                updates[canonical_field] = value
                break
    return updates


def _trace_family_path(
    family_name: str,
    canonical_row: dict[str, str],
    origin_map: dict[str, dict[str, Any]],
    include_target=True,
) -> str:
    upload_fields = []
    provider_steps = []
    visited_fields = set()

    def walk(field_name: str):
        if field_name in visited_fields:
            return
        visited_fields.add(field_name)
        origin = origin_map.get(field_name)
        if not origin or origin.get('kind') == 'upload':
            if canonical_row.get(field_name):
                upload_fields.append(_field_label(field_name))
            return
        for dependency in origin.get('depends_on', []):
            if canonical_row.get(dependency):
                walk(dependency)
        provider_steps.append(origin.get('label') or _field_label(field_name))

    for field_name in FAMILY_QUERY_FIELDS.get(family_name, []):
        if canonical_row.get(field_name):
            walk(field_name)

    ordered_uploads = _unique_ordered(upload_fields)
    ordered_steps = _unique_ordered(provider_steps)
    segments = []
    if ordered_uploads:
        segments.append(' / '.join(ordered_uploads))
    segments.extend(ordered_steps)
    if include_target:
        segments.append(FAMILY_CONFIG[family_name]['label'])
    return ' -> '.join(_unique_ordered([segment for segment in segments if segment]))


def _trace_canonical_field_path(
    field_name: str,
    canonical_row: dict[str, str],
    origin_map: dict[str, dict[str, Any]],
) -> str:
    upload_fields = []
    provider_steps = []
    visited_fields = set()

    def walk(target_field: str):
        if target_field in visited_fields:
            return
        visited_fields.add(target_field)
        origin = origin_map.get(target_field)
        if not origin or origin.get('kind') == 'upload':
            if canonical_row.get(target_field):
                upload_fields.append(_field_label(target_field))
            return
        for dependency in origin.get('depends_on', []):
            if canonical_row.get(dependency):
                walk(dependency)
        provider_steps.append(origin.get('label') or _field_label(target_field))

    walk(field_name)
    segments = []
    ordered_uploads = _unique_ordered(upload_fields)
    if ordered_uploads:
        segments.append(' / '.join(ordered_uploads))
    segments.extend(_unique_ordered(provider_steps))
    segments.append(_field_label(field_name))
    return ' -> '.join([segment for segment in segments if segment])


def _build_canonical_field_diagnostic(
    field_name: str,
    canonical_row: dict[str, str],
    expanded_row: dict[str, str],
    origin_map: dict[str, dict[str, Any]],
) -> str:
    if expanded_row.get(field_name):
        return ''

    if field_name == 'prefix':
        if canonical_row.get('ip'):
            return 'IP -> 本地前缀归属信息 / Prefix-AS映射均未命中，无法推导 Prefix'
        return '缺少 Prefix，无法继续关联'

    if field_name == 'asn':
        if canonical_row.get('ip') and not expanded_row.get('prefix'):
            return 'IP -> 本地前缀归属信息 / Prefix-AS映射均未命中，无法推导 Prefix / ASN'
        if canonical_row.get('prefix'):
            return 'Prefix -> Prefix归属信息 / Prefix-AS映射均未命中，无法推导 ASN'
        return '缺少 ASN，无法继续关联'

    if field_name == 'country':
        if expanded_row.get('asn'):
            return 'ASN -> AS基础信息未命中，无法推导国家'
        if canonical_row.get('ip') or canonical_row.get('prefix'):
            return 'IP/Prefix 关联链未命中，无法推导国家'
        return '缺少国家，无法继续关联'

    if field_name == 'source':
        if canonical_row.get('ip') or canonical_row.get('prefix'):
            return 'IP/Prefix 关联链未命中，无法推导数据源'
        return '缺少数据源，无法继续关联'

    if field_name == 'as_name':
        if expanded_row.get('asn'):
            return 'ASN -> AS基础信息未命中，无法推导AS名称'
        return '缺少 ASN，无法继续关联'

    if field_name == 'org_name':
        if expanded_row.get('asn'):
            return 'ASN -> AS基础信息未命中，无法推导机构名称'
        return '缺少 ASN，无法继续关联'

    path = _trace_canonical_field_path(field_name, expanded_row, origin_map)
    if path:
        return f'{path} 未命中'
    return f'{_field_label(field_name)} 未命中'


def _build_family_resolution_diagnostic(
    family_name: str,
    canonical_row: dict[str, str],
    expanded_row: dict[str, str],
    origin_map: dict[str, dict[str, Any]],
) -> str:
    required_fields = FAMILY_CONFIG.get(family_name, {}).get('required_fields', set())
    missing_fields = [field_name for field_name in required_fields if not expanded_row.get(field_name)]
    messages = []

    for field_name in missing_fields:
        if field_name == 'asn':
            if canonical_row.get('ip') and not expanded_row.get('prefix'):
                messages.append('IP -> 本地前缀归属信息 / Prefix-AS映射均未命中，无法推导 Prefix / ASN')
            elif canonical_row.get('prefix'):
                messages.append('Prefix -> Prefix归属信息 / Prefix-AS映射均未命中，无法推导 ASN')
            else:
                messages.append('缺少 ASN，无法继续关联')
            continue

        if field_name == 'prefix':
            if canonical_row.get('ip'):
                messages.append('IP -> 本地前缀归属信息 / Prefix-AS映射均未命中，无法推导 Prefix')
            else:
                messages.append('缺少 Prefix，无法继续关联')
            continue

        if field_name == 'country':
            if expanded_row.get('asn'):
                messages.append('ASN -> AS基础信息未命中，无法推导国家')
            elif canonical_row.get('ip'):
                messages.append('IP 关联链未命中，无法推导国家')
            else:
                messages.append('缺少国家，无法继续关联')
            continue

        if field_name == 'source':
            messages.append('缺少数据源，无法继续关联')
            continue

        if field_name == 's_time':
            messages.append('缺少开始时间，无法继续关联')
            continue

        messages.append(f'缺少{_field_label(field_name)}，无法继续关联')

    if messages:
        return '；'.join(_unique_ordered(messages))

    partial_path = _trace_family_path(
        family_name,
        expanded_row,
        origin_map,
        include_target=False,
    )
    if partial_path:
        return f'{partial_path} -> {FAMILY_CONFIG[family_name]["label"]} 未命中'
    return f'{FAMILY_CONFIG[family_name]["label"]} 未命中'


def _expand_resolution_context(
    canonical_row: dict[str, str],
    target_families: list[str],
    conn=conn_11,
    schema_catalog=None,
    origin_map: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, str], dict[str, dict[str, Any]], dict[str, str], dict[str, dict[str, Any]]]:
    schema_catalog = schema_catalog or _load_schema_catalog(conn=conn)
    expanded_row = dict(canonical_row)
    origin_map = dict(origin_map or {})

    if expanded_row.get('ip'):
        ip_metadata = _resolve_ip_metadata(expanded_row['ip'])
        _merge_context_fields_with_origin(
            expanded_row,
            origin_map,
            ip_metadata,
            {
                'kind': 'ip_metadata',
                'label': ip_metadata.pop('_origin_label', 'IP归属信息'),
                'depends_on': ['ip'],
            },
        )
    if expanded_row.get('prefix'):
        prefix_metadata = _resolve_prefix_metadata(expanded_row['prefix'])
        _merge_context_fields_with_origin(
            expanded_row,
            origin_map,
            prefix_metadata,
            {
                'kind': 'prefix_metadata',
                'label': prefix_metadata.pop('_origin_label', 'Prefix归属信息'),
                'depends_on': ['prefix'],
            },
        )

    expansion_families = _unique_ordered(CONTEXT_EXPANSION_FAMILIES + target_families)
    for _ in range(len(expansion_families) + 1):
        changed = False
        for family_name in expansion_families:
            family_query_fields = [
                field_name for field_name in FAMILY_QUERY_FIELDS.get(family_name, []) if expanded_row.get(field_name)
            ]
            try:
                family_row = _resolve_row_for_family(
                    family_name,
                    expanded_row,
                    conn=conn,
                    schema_catalog=schema_catalog,
                )
            except Exception:
                family_row = {}
            changed = _merge_context_fields_with_origin(
                expanded_row,
                origin_map,
                _context_updates_from_family_row(family_name, family_row),
                {
                    'kind': 'family',
                    'family_name': family_name,
                    'label': FAMILY_CONFIG[family_name]['label'],
                    'depends_on': family_query_fields,
                },
            ) or changed
        if not changed:
            break

    resolved_families = {}
    family_traces = {}
    for family_name in target_families:
        try:
            resolved_families[family_name] = _resolve_row_for_family(
                family_name,
                expanded_row,
                conn=conn,
                schema_catalog=schema_catalog,
            )
        except Exception:
            resolved_families[family_name] = {}
        family_traces[family_name] = _trace_family_path(
            family_name,
            expanded_row,
            origin_map,
            include_target=bool(resolved_families[family_name]),
        )
    return expanded_row, resolved_families, family_traces, origin_map


def _build_canonical_row_with_origins(
    row: dict[str, Any],
    upload_columns: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    canonical_row = {field_name: '' for field_name in CANONICAL_FIELDS}
    origin_map = {}
    for item in upload_columns:
        standard_field = item.get('standard_field')
        if not standard_field or canonical_row.get(standard_field):
            continue
        value = row.get(item['original_name'], '')
        normalized_value = _serialize_value(value)
        canonical_row[standard_field] = normalized_value
        if normalized_value:
            origin_map[standard_field] = {
                'kind': 'upload',
                'label': _field_label(standard_field),
                'depends_on': [],
                'field': standard_field,
            }

    canonical_row['asn'] = _normalize_asn(canonical_row['asn'])
    canonical_row['prefix'] = _normalize_prefix(canonical_row['prefix'])
    canonical_row['country'] = _normalize_country(canonical_row['country'])
    canonical_row['source'] = _normalize_source(canonical_row['source'])

    if canonical_row.get('ip') and (not canonical_row.get('prefix') or not canonical_row.get('asn')):
        ip_metadata = _resolve_ip_metadata(canonical_row['ip'])
        if ip_metadata:
            _merge_context_fields_with_origin(
                canonical_row,
                origin_map,
                ip_metadata,
                {
                    'kind': 'ip_metadata',
                    'label': ip_metadata.pop('_origin_label', 'IP归属信息'),
                    'depends_on': ['ip'],
                },
            )

    return canonical_row, origin_map


def _build_canonical_row(row: dict[str, Any], upload_columns: list[dict[str, Any]]) -> dict[str, str]:
    canonical_row, _ = _build_canonical_row_with_origins(row, upload_columns)
    return canonical_row


def _annotate_database_field_candidates(
    upload_columns: list[dict[str, Any]],
    records: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    conn=conn_11,
) -> list[dict[str, Any]]:
    if not candidates:
        return []

    preview_records = records[:PREVIEW_ROW_LIMIT]
    annotated_candidates = [
        {
            **item,
            'sample_hit_count': 0,
            'sample_total_count': len(preview_records),
            'sample_value_examples': [],
            'sample_trace_examples': [],
            'sample_diagnostic_examples': [],
        }
        for item in candidates
    ]
    if not preview_records:
        return annotated_candidates

    family_fields = defaultdict(list)
    canonical_fields = []
    for item in annotated_candidates:
        if item.get('source_kind') == 'canonical':
            canonical_fields.append(item)
            continue
        family_fields[item['table_family']].append(item)

    try:
        schema_catalog = _load_schema_catalog(conn=conn)
    except Exception:
        return annotated_candidates

    for record in preview_records:
        canonical_row, origin_map = _build_canonical_row_with_origins(record, upload_columns)
        expanded_row, family_cache, family_traces, expanded_origin_map = _expand_resolution_context(
            canonical_row,
            [family_name for family_name in family_fields.keys() if family_name],
            conn=conn,
            schema_catalog=schema_catalog,
            origin_map=origin_map,
        )

        for field in canonical_fields:
            formatted_value = _format_export_value(expanded_row.get(field['field_name'], ''))
            if not formatted_value:
                diagnostic = _build_canonical_field_diagnostic(
                    field['field_name'],
                    canonical_row,
                    expanded_row,
                    expanded_origin_map,
                )
                if (
                    diagnostic
                    and diagnostic not in field['sample_diagnostic_examples']
                    and len(field['sample_diagnostic_examples']) < 3
                ):
                    field['sample_diagnostic_examples'].append(diagnostic)
                continue
            field['sample_hit_count'] += 1
            if formatted_value not in field['sample_value_examples'] and len(field['sample_value_examples']) < 3:
                field['sample_value_examples'].append(formatted_value)
            trace = _trace_canonical_field_path(field['field_name'], expanded_row, expanded_origin_map)
            if trace and trace not in field['sample_trace_examples'] and len(field['sample_trace_examples']) < 3:
                field['sample_trace_examples'].append(trace)

        for family_name, fields in family_fields.items():
            resolved_row = family_cache.get(family_name, {})
            for field in fields:
                formatted_value = _format_export_value(resolved_row.get(field['field_name'], ''))
                if not formatted_value:
                    diagnostic = _build_family_resolution_diagnostic(
                        family_name,
                        canonical_row,
                        expanded_row,
                        origin_map,
                    )
                    if (
                        diagnostic
                        and diagnostic not in field['sample_diagnostic_examples']
                        and len(field['sample_diagnostic_examples']) < 3
                    ):
                        field['sample_diagnostic_examples'].append(diagnostic)
                    continue
                field['sample_hit_count'] += 1
                if formatted_value not in field['sample_value_examples'] and len(field['sample_value_examples']) < 3:
                    field['sample_value_examples'].append(formatted_value)
                trace = family_traces.get(family_name, '')
                if trace and trace not in field['sample_trace_examples'] and len(field['sample_trace_examples']) < 3:
                    field['sample_trace_examples'].append(trace)

    for field in annotated_candidates:
        if field['sample_hit_count'] == 0 and not field['sample_trace_examples']:
            field['sample_trace_examples'] = field['sample_diagnostic_examples']
        field.pop('sample_diagnostic_examples', None)

    annotated_candidates.sort(
        key=lambda item: (-item['sample_hit_count'], item['table_label'], item['field_name'])
    )
    return annotated_candidates


def _month_strings(start_time: datetime.datetime | None, end_time: datetime.datetime | None) -> list[str]:
    base_time = start_time or end_time
    if base_time is None:
        return []

    end_time = end_time or base_time
    months = []
    current = datetime.datetime(base_time.year, base_time.month, 1)
    final = datetime.datetime(end_time.year, end_time.month, 1)
    while current <= final:
        months.append(current.strftime('%Y%m'))
        next_month = current.replace(day=28) + datetime.timedelta(days=4)
        current = next_month.replace(day=1)
    return months


def _query_best_row_from_table(conn, table_name: str, filters: dict[str, Any], time_column: str | None = None, preferred_time=None):
    usable_filters = {key: value for key, value in filters.items() if value not in [None, '']}
    query = sql.SQL('select * from {} where true').format(sql.Identifier(table_name))
    params = []
    for field_name, value in usable_filters.items():
        query += sql.SQL(' and {} = %s').format(sql.Identifier(field_name))
        params.append(value)

    if time_column and preferred_time:
        query += sql.SQL(' order by abs(extract(epoch from ({} - %s))) asc nulls last limit 1').format(
            sql.Identifier(time_column)
        )
        params.append(preferred_time)
    else:
        query += sql.SQL(' limit 1')

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(query, params)
        return cursor.fetchone() or {}
    finally:
        cursor.close()


def _candidate_tables_for_monthly_family(family_name: str, schema_catalog: dict[str, Any], canonical_row: dict[str, str]) -> list[str]:
    start_time = _parse_datetime(canonical_row.get('s_time'))
    end_time = _parse_datetime(canonical_row.get('e_time'))
    months = _month_strings(start_time, end_time)
    available_tables = schema_catalog.get(family_name, {}).get('tables', [])
    if not months:
        return available_tables[:3]

    selected = []
    for month_string in months:
        selected.extend([table_name for table_name in available_tables if table_name.endswith(month_string)])
    return _unique_ordered(selected) or available_tables[:3]


def _candidate_feature_asn_tables(schema_catalog: dict[str, Any], canonical_row: dict[str, str]) -> list[str]:
    available_tables = schema_catalog.get('feature_asn', {}).get('tables', [])
    start_time = _parse_datetime(canonical_row.get('s_time'))
    end_time = _parse_datetime(canonical_row.get('e_time'))
    months = _month_strings(start_time, end_time)
    if not months:
        return available_tables[:6]

    candidate_tables = []
    country = canonical_row.get('country', '')
    suffix = country if country in BIG_COUNTRY.values() else BIG_COUNTRY.get(_country_query_value(country))
    for month_string in months:
        if suffix:
            candidate_tables.append(f'feature_{suffix.lower()}_{month_string}')
        candidate_tables.append(f'feature_other_{month_string}')
        candidate_tables.extend(
            [table_name for table_name in available_tables if table_name.endswith(month_string)]
        )
    return [table_name for table_name in _unique_ordered(candidate_tables) if table_name in available_tables]


def _pick_best_event_row(conn, schema_catalog: dict[str, Any], canonical_row: dict[str, str]):
    tables = _candidate_tables_for_monthly_family('event_table', schema_catalog, canonical_row)
    if not tables:
        return {}

    preferred_time = _parse_datetime(canonical_row.get('s_time'))
    base_filters = {
        'source': canonical_row.get('source'),
        'event_type': canonical_row.get('event_type'),
        'level': canonical_row.get('level'),
    }
    ranked = []
    for table_name in tables:
        query = sql.SQL('select * from {} where true').format(sql.Identifier(table_name))
        params = []
        for field_name, value in base_filters.items():
            if value:
                query += sql.SQL(' and {} = %s').format(sql.Identifier(field_name))
                params.append(value)
        if preferred_time:
            query += sql.SQL(
                ' order by abs(extract(epoch from ({} - %s))) asc nulls last limit 20'
            ).format(sql.Identifier('s_time'))
            params.append(preferred_time)
        else:
            query += sql.SQL(' limit 20')

        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        finally:
            cursor.close()

        for row in rows:
            score = 0
            if canonical_row.get('prefix') and row.get('affected_prefix') == canonical_row['prefix']:
                score += 5
            if canonical_row.get('asn') and canonical_row['asn'] in [row.get('attacker_as', ''), row.get('attacked_as', '')]:
                score += 3
            normalized_attacker_country = _normalize_country(row.get('attacker_country', ''))
            normalized_attacked_country = _normalize_country(row.get('attacked_country', ''))
            if canonical_row.get('country') and canonical_row['country'] in [normalized_attacker_country, normalized_attacked_country]:
                score += 2
            if canonical_row.get('org_name') and canonical_row['org_name'] in [row.get('attacker_org', ''), row.get('attacked_org', '')]:
                score += 1
            delta = abs((row.get('s_time') - preferred_time).total_seconds()) if preferred_time and row.get('s_time') else 0
            ranked.append((score, -delta, row))

    if not ranked:
        return {}
    ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))
    return ranked[0][2]


def _resolve_row_for_family(family_name: str, canonical_row: dict[str, str], conn=conn_11, schema_catalog=None):
    preferred_time = _parse_datetime(canonical_row.get('s_time'))

    if family_name == 'as_info':
        if not canonical_row.get('asn'):
            return {}
        local_row = _resolve_local_as_info_row(canonical_row['asn'])
        needs_db_fallback = not all(
            [
                local_row.get('as_country_cn') or local_row.get('as_country'),
                local_row.get('as_name_cn') or local_row.get('as_name'),
                local_row.get('org_name_cn') or local_row.get('org_name'),
            ]
        )
        if not needs_db_fallback:
            return local_row

        db_row = _query_best_row_from_table(conn, 'as_info', {'asn': canonical_row['asn']})
        if not db_row:
            return local_row
        return _merge_non_empty_fields(db_row, local_row)

    schema_catalog = schema_catalog or _load_schema_catalog(conn=conn)

    if family_name == 'feature_country':
        if not canonical_row.get('country'):
            return {}
        return _query_best_row_from_table(
            conn,
            'feature_country',
            {'country': _country_query_value(canonical_row.get('country')), 'source': canonical_row.get('source')},
            time_column='t',
            preferred_time=preferred_time,
        )

    if family_name == 'feature_asn':
        if not canonical_row.get('asn'):
            return {}
        for table_name in _candidate_feature_asn_tables(schema_catalog, canonical_row):
            row = _query_best_row_from_table(
                conn,
                table_name,
                {
                    'asn': canonical_row.get('asn'),
                    'country': _country_query_value(canonical_row.get('country')),
                    'source': canonical_row.get('source'),
                },
                time_column='t',
                preferred_time=preferred_time,
            )
            if row:
                return row
        return {}

    if family_name == 'event_table':
        return _pick_best_event_row(conn, schema_catalog, canonical_row)

    if family_name == 'as_outage':
        if not canonical_row.get('asn'):
            return {}
        for table_name in _candidate_tables_for_monthly_family('as_outage', schema_catalog, canonical_row):
            row = _query_best_row_from_table(
                conn,
                table_name,
                {
                    'asn': canonical_row.get('asn'),
                    'source': canonical_row.get('source'),
                    'country': _country_query_value(canonical_row.get('country')),
                },
                time_column='s_time',
                preferred_time=preferred_time,
            )
            if row:
                return row
        return {}

    if family_name == 'prefix_outage':
        if not canonical_row.get('prefix'):
            return {}
        for table_name in _candidate_tables_for_monthly_family('prefix_outage', schema_catalog, canonical_row):
            row = _query_best_row_from_table(
                conn,
                table_name,
                {
                    'prefix': canonical_row.get('prefix'),
                    'source': canonical_row.get('source'),
                    'asn': canonical_row.get('asn'),
                    'country': _country_query_value(canonical_row.get('country')),
                },
                time_column='s_time',
                preferred_time=preferred_time,
            )
            if row:
                return row
        return {}

    if family_name == 'country_outage':
        if not canonical_row.get('country'):
            return {}
        for table_name in _candidate_tables_for_monthly_family('country_outage', schema_catalog, canonical_row):
            row = _query_best_row_from_table(
                conn,
                table_name,
                {'country': _country_query_value(canonical_row.get('country')), 'source': canonical_row.get('source')},
                time_column='s_time',
                preferred_time=preferred_time,
            )
            if row:
                return row
        return {}

    return {}


def _format_export_value(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, datetime.datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, datetime.date):
        return value.strftime('%Y-%m-%d')
    return str(value)


def _update_task_metadata(task: dict[str, Any]):
    _write_json(_task_metadata_path(task['task_id']), task)


def _load_task_records(task: dict[str, Any]) -> list[dict[str, Any]]:
    records_path = _task_records_path(task['task_id'])
    if os.path.exists(records_path):
        return _read_json(records_path)

    stored_file_name = task.get('stored_file_name', '')
    upload_path = _task_upload_path(task['task_id'], stored_file_name) if stored_file_name else ''
    if not upload_path or not os.path.exists(upload_path):
        raise FileNotFoundError('任务原始文件不存在')

    with open(upload_path, 'rb') as upload_file:
        dataframe = _read_dataframe_from_payload(task.get('file_name') or stored_file_name, upload_file.read())
    records = _serialize_rows(dataframe.to_dict(orient='records'))
    _write_json(records_path, records)
    return records


def _preview_cache_key(selected_field_ids: list[str] | None) -> str:
    selected_field_ids = selected_field_ids or []
    return '__all__' if not selected_field_ids else '__'.join(selected_field_ids)


def _preview_cache_path(task_id: str, selected_field_ids: list[str] | None) -> str:
    safe_key = re.sub(r'[^a-zA-Z0-9_.-]+', '_', _preview_cache_key(selected_field_ids))
    return os.path.join(_preview_cache_dir(task_id), f'{safe_key}.json')


def _read_preview_cache(task_id: str, selected_field_ids: list[str] | None) -> dict[str, Any] | None:
    cache_path = _preview_cache_path(task_id, selected_field_ids)
    if not os.path.exists(cache_path):
        return None
    try:
        return _read_json(cache_path)
    except Exception:
        return None


def _write_preview_cache(task_id: str, selected_field_ids: list[str] | None, payload: dict[str, Any]):
    cache_dir = _preview_cache_dir(task_id)
    os.makedirs(cache_dir, exist_ok=True)
    _write_json(_preview_cache_path(task_id, selected_field_ids), payload)


def _resolve_selected_fields(task: dict[str, Any], selected_field_ids: list[str] | None) -> tuple[list[dict[str, Any]], str | None]:
    field_map = {item['field_id']: item for item in task.get('matched_database_fields', [])}
    selected_field_ids = selected_field_ids or []
    selected_fields = [field_map[field_id] for field_id in selected_field_ids if field_id in field_map]
    if selected_field_ids and not selected_fields:
        return [], '勾选字段无效，请重新选择'
    return selected_fields, None


def _sanitize_export_filename_part(value: str, fallback: str) -> str:
    sanitized = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', str(value or '').strip())
    sanitized = re.sub(r'\s+', '_', sanitized)
    sanitized = re.sub(r'_+', '_', sanitized).strip('._ ')
    return sanitized or fallback


def _build_export_filename(task: dict[str, Any], selected_fields: list[dict[str, Any]]) -> str:
    base_name = os.path.splitext(task.get('file_name') or '')[0]
    safe_base_name = _sanitize_export_filename_part(base_name, 'data_query_result')

    selected_names = []
    for item in selected_fields:
        field_name = item.get('field_name') or item.get('label') or item.get('field_id') or 'field'
        safe_field_name = _sanitize_export_filename_part(field_name, 'field')
        if safe_field_name not in selected_names:
            selected_names.append(safe_field_name)

    field_suffix = '_'.join(selected_names[:6]) if selected_names else 'field'
    filename = f'{safe_base_name}_匹配字段_{field_suffix}.csv'
    if len(filename) > 180:
        filename = f'{safe_base_name[:80]}_匹配字段_{field_suffix[:80]}.csv'
    return filename


def _build_enriched_preview(
    task: dict[str, Any],
    records: list[dict[str, Any]],
    selected_fields: list[dict[str, Any]],
    conn=conn_11,
    limit: int | None = None,
):
    schema_catalog = _load_schema_catalog(conn=conn)
    preview_records = records[:limit] if limit else records
    headers = task['original_columns'] + [item['label'] for item in selected_fields]
    rows = []
    target_families = _unique_ordered([item['table_family'] for item in selected_fields if item.get('table_family')])

    for record in preview_records:
        canonical_row, origin_map = _build_canonical_row_with_origins(record, task['upload_columns'])
        expanded_row, family_cache, _, _ = _expand_resolution_context(
            canonical_row,
            target_families,
            conn=conn,
            schema_catalog=schema_catalog,
            origin_map=origin_map,
        )
        preview_row = {column_name: _format_export_value(record.get(column_name, '')) for column_name in task['original_columns']}
        for field in selected_fields:
            if field.get('source_kind') == 'canonical':
                preview_row[field['label']] = _format_export_value(expanded_row.get(field['field_name'], ''))
                continue
            family_name = field['table_family']
            preview_row[field['label']] = _format_export_value(family_cache[family_name].get(field['field_name'], ''))
        rows.append(preview_row)

    return headers, rows


def get_data_query_preview(task_id: str, selected_field_ids: list[str] | None, conn=conn_11):
    metadata_path = _task_metadata_path(task_id)
    if not os.path.exists(metadata_path):
        return {'status': False, 'msg': '任务不存在'}, 404

    task = _read_json(metadata_path)
    try:
        task = _refresh_task_metadata(task)
    except Exception:
        pass

    cached_preview = _read_preview_cache(task_id, selected_field_ids)
    if cached_preview is not None:
        return {'status': True, 'data': cached_preview}

    try:
        records = _load_task_records(task)
    except FileNotFoundError:
        return {'status': False, 'msg': '任务原始文件不存在，请重新上传'}, 404
    except Exception:
        return {'status': False, 'msg': '任务原始文件读取失败，请重新上传'}, 400

    selected_fields, error_message = _resolve_selected_fields(task, selected_field_ids)
    if error_message:
        return {'status': False, 'msg': error_message}, 400

    preview_columns, preview_rows = _build_enriched_preview(
        task,
        records,
        selected_fields,
        conn=conn,
        limit=PREVIEW_ROW_LIMIT,
    )
    preview_payload = {
        'preview_columns': preview_columns,
        'preview_rows': preview_rows,
    }
    _write_preview_cache(task_id, selected_field_ids, preview_payload)
    return {'status': True, 'data': preview_payload}


def generate_data_query_export(task_id: str, selected_field_ids: list[str] | None, conn=conn_11):
    metadata_path = _task_metadata_path(task_id)
    if not os.path.exists(metadata_path):
        return {'status': False, 'msg': '任务不存在'}, 404

    task = _read_json(metadata_path)
    try:
        task = _refresh_task_metadata(task)
    except Exception:
        pass
    try:
        records = _load_task_records(task)
    except FileNotFoundError:
        return {'status': False, 'msg': '任务原始文件不存在，请重新上传'}, 404
    except Exception:
        return {'status': False, 'msg': '任务原始文件读取失败，请重新上传'}, 400
    selected_field_ids = selected_field_ids or []
    if not selected_field_ids:
        return {'status': False, 'msg': '请至少勾选一个数据库字段'}, 400

    selected_fields, error_message = _resolve_selected_fields(task, selected_field_ids)
    if error_message:
        return {'status': False, 'msg': error_message}, 400

    headers, preview_rows = _build_enriched_preview(
        task,
        records,
        selected_fields,
        conn=conn,
    )
    rows = [[row.get(header, '') for header in headers] for row in preview_rows]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)

    task['selected_field_ids'] = selected_field_ids
    task['status'] = 'generated'
    task['generated_at'] = _utc_now_string()
    _update_task_metadata(task)

    export_filename = _build_export_filename(task, selected_fields)
    response = make_response('\ufeff' + output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8-sig'
    response.headers['Content-Disposition'] = (
        f"attachment; filename*=UTF-8''{quote(export_filename)}"
    )
    return response
