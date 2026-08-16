"""P0 六类历史事实的确定性、无副作用规范化。

本模块刻意不导入数据库连接、检测核心或 Web 服务。调用方只需传入从
只读快照取得的普通映射；返回值只包含 JSON 可序列化对象。历史事实不会
被改写，缺失也不会被补成 0。
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import math
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo


BUSINESS_TIMEZONE_NAME = "Asia/Shanghai"
BUSINESS_TIMEZONE = ZoneInfo(BUSINESS_TIMEZONE_NAME)
UTC = timezone.utc

EVENT_TYPE_LABELS = {
    "hijack": "前缀劫持",
    "sub_hijack": "子前缀劫持",
    "leak": "路由泄漏",
    "prefix_outage": "前缀中断",
    "as_outage": "AS中断",
    "country_outage": "国家中断",
}
LABEL_EVENT_TYPES = {label: event_type for event_type, label in EVENT_TYPE_LABELS.items()}

# ``locator_key`` 是 detail_url 能表达的字段；``primary_key`` 是事实表真实主键。
# prefix_outage 缺失 ASN 的差异必须持续显式暴露，不能用第一行静默消解。
EVENT_SPECS: Dict[str, Dict[str, Any]] = {
    "hijack": {
        "family": "hijack",
        "problem_kind": "prefix",
        "problem_field": "prefix",
        "event_id_field": "hijack_eventid",
        "primary_key": ("source", "prefix", "hijack_eventid"),
        "locator_key": ("source", "prefix", "hijack_eventid"),
        "phases": {"before": "pre_vp_paths", "during": "eve_vp_paths", "after": "next_vp_paths"},
        "risk_fields": ("hijack_level", "level"),
    },
    "sub_hijack": {
        "family": "sub_hijack",
        "problem_kind": "prefix",
        "problem_field": "prefix",
        "event_id_field": "sub_hijack_eventid",
        "primary_key": ("source", "prefix", "sub_hijack_eventid"),
        "locator_key": ("source", "prefix", "sub_hijack_eventid"),
        "phases": {},
        "risk_fields": ("sub_hijack_level", "level"),
    },
    "leak": {
        "family": "leak_event",
        "problem_kind": "prefix",
        "problem_field": "prefix",
        "event_id_field": "leak_event_id",
        "primary_key": ("source", "prefix", "leak_event_id"),
        "locator_key": ("source", "prefix", "leak_event_id"),
        "phases": {"during": "as_path"},
        "risk_fields": ("leak_level", "level"),
    },
    "prefix_outage": {
        "family": "prefix_outage",
        "problem_kind": "prefix",
        "problem_field": "prefix",
        "event_id_field": "outage_id",
        "primary_key": ("source", "prefix", "outage_id", "asn"),
        "locator_key": ("source", "prefix", "outage_id"),
        "phases": {"before": "pre_vp_paths", "during": "eve_vp_paths", "after": "next_vp_paths"},
        "risk_fields": ("outage_level", "level"),
    },
    "as_outage": {
        "family": "as_outage",
        "problem_kind": "asn",
        "problem_field": "asn",
        "event_id_field": "outage_id",
        "primary_key": ("source", "asn", "outage_id"),
        "locator_key": ("source", "asn", "outage_id"),
        "phases": {"before": "pre_vp_paths", "during": "eve_vp_paths", "after": "next_vp_paths"},
        "risk_fields": ("outage_level", "level"),
    },
    "country_outage": {
        "family": "country_outage",
        "problem_kind": "country",
        "problem_field": "country",
        "event_id_field": "outage_id",
        "primary_key": ("source", "country", "outage_id"),
        "locator_key": ("source", "country", "outage_id"),
        "phases": {},
        "risk_fields": ("outage_level", "level"),
    },
}

DETAIL_TIME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$")
SOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
ASN_SET_RE = re.compile(r"^\{([0-9]+(?:,[0-9]+)*)\}$")
TABLE_RE = re.compile(
    r"^(hijack|sub_hijack|leak_event|prefix_outage|as_outage|country_outage)_([0-9]{6})$"
)
EMBEDDED_TIME_RE = re.compile(
    r"(?<![0-9])([0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2})(?![0-9])"
)


class NormalizationError(ValueError):
    """输入不满足 P0 规范化合同。"""


class LocatorError(NormalizationError):
    """detail_url 不能严格解析。"""


def _json_ready(value: Any) -> Any:
    """将数据库常见值确定性转换为 JSON 值，不猜测业务语义。"""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NormalizationError("禁止输出非有限浮点数")
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_ready(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (set, frozenset)):
        ready = [_json_ready(item) for item in value]
        return sorted(ready, key=canonical_json)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat(sep=" ", timespec="seconds")
        return value.isoformat(timespec="seconds")
    if isinstance(value, timedelta):
        return value.total_seconds()
    # Decimal、UUID 等只作为历史 payload 展示；稳定 ID identity 不应传入它们。
    return str(value)


def canonical_json(value: Any) -> str:
    """返回与现有 ``events_service._canonical_json`` 兼容的规范 JSON。"""

    return json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def stable_id(prefix: str, identity: Any, *, length: int = 24) -> str:
    """由规范 JSON 生成稳定 SHA256 截断 ID。"""

    if not re.fullmatch(r"[a-z][a-z0-9_]*_", prefix):
        raise NormalizationError("稳定 ID 前缀非法")
    if length < 16 or length > 64:
        raise NormalizationError("稳定 ID 截断长度必须位于 16 到 64 之间")
    digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    return prefix + digest[:length]


def incident_id_v1(
    event_type: str,
    start_time: str,
    problem: str,
    event_id: Any,
    source: str,
) -> str:
    """精确复现现有 ``incident_id_v1`` identity 与 24 位截断规则。"""

    if event_type not in EVENT_SPECS:
        raise NormalizationError("未知事件类型：{}".format(event_type))
    if not isinstance(start_time, str) or not DETAIL_TIME_RE.fullmatch(start_time):
        raise NormalizationError("incident start_time 必须是原始业务时间文本")
    if not isinstance(problem, str) or not problem:
        raise NormalizationError("incident problem 不能为空")
    if isinstance(event_id, bool) or not str(event_id).isdigit():
        raise NormalizationError("incident event_id 必须是非负整数")
    if not isinstance(source, str) or not source:
        raise NormalizationError("incident source 不能为空")
    identity = {
        "schema": "incident_id_v1",
        "event_type": event_type,
        "start_time": start_time,
        "problem": problem,
        "event_id": int(event_id),
        "source": source,
    }
    return stable_id("inc_v1_", identity, length=24)


def collision_group_id_v1(
    source_table: str,
    source_primary_key: Mapping[str, Any],
    incident_ids: Iterable[str],
) -> str:
    """为一条事实记录被多个 Incident 复用生成稳定碰撞组 ID。"""

    normalized_ids = sorted(set(incident_ids))
    if len(normalized_ids) < 2:
        raise NormalizationError("碰撞组至少需要两个不同 Incident")
    identity = {
        "schema": "legacy_collision_group_id_v1",
        "source_table": source_table,
        "source_primary_key": _json_ready(source_primary_key),
        "incident_ids": normalized_ids,
    }
    return stable_id("lcg_v1_", identity, length=32)


def quarantine_id_v1(
    source_table: str,
    source_primary_key: Mapping[str, Any],
    reasons: Iterable[str],
) -> str:
    """为隔离记录及其原因集合生成稳定 ID。"""

    normalized_reasons = sorted(set(str(reason) for reason in reasons if str(reason)))
    if not normalized_reasons:
        raise NormalizationError("quarantine 至少需要一个原因")
    identity = {
        "schema": "quarantine_id_v1",
        "source_table": source_table,
        "source_primary_key": _json_ready(source_primary_key),
        "reasons": normalized_reasons,
    }
    return stable_id("qr_v1_", identity, length=32)


def _parse_datetime(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise NormalizationError("{} 必须是时间文本或 datetime".format(field_name))
    text = value.strip()
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(candidate)
    except ValueError as error:
        raise NormalizationError("{} 不是有效时间：{}".format(field_name, value)) from error


def business_time_to_utc(value: Any) -> str:
    """将 naive 历史时间按 Asia/Shanghai 解释并输出 UTC RFC3339。"""

    parsed = _parse_datetime(value, field_name="业务时间")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BUSINESS_TIMEZONE)
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_asn(value: Any) -> str:
    """规范为不带 AS 前缀的十进制 ASN 字符串。"""

    if isinstance(value, bool) or value is None:
        raise NormalizationError("ASN 不能为空或布尔值")
    if isinstance(value, int):
        number = value
    else:
        text = str(value).strip()
        if text[:2].upper() == "AS":
            text = text[2:]
        if not re.fullmatch(r"[0-9]+", text):
            raise NormalizationError("ASN 必须是十进制整数")
        number = int(text)
    if number < 0 or number > 4294967295:
        raise NormalizationError("ASN 超出 32 位范围")
    return str(number)


def normalize_native_asn_key(value: Any) -> str:
    """规范事实表原生 ASN 主键，并兼容历史聚合事件的花括号集合。

    ``prefix_outage`` 与 ``as_outage`` 的 ``asn`` 列是 text。少量聚合
    事件把多个 ASN 保存为 ``{asn,...}``，该文本同时存在于原生主键和
    ``detail_url`` problem。它不能被误判为非法，也不能被合成一个虚构
    ASN；集合成员仍逐个执行 32 位校验，成员顺序作为原生键的一部分保留。
    """

    try:
        return normalize_asn(value)
    except NormalizationError as scalar_error:
        if not isinstance(value, str):
            raise scalar_error
        match = ASN_SET_RE.fullmatch(value.strip())
        if match is None:
            raise scalar_error
        members = [normalize_asn(item) for item in match.group(1).split(",")]
        if len(set(members)) != len(members):
            raise NormalizationError("ASN 集合主键包含重复成员")
        return "{{{}}}".format(",".join(members))


def normalize_prefix(value: Any) -> str:
    """规范 IPv4/IPv6 前缀；兼容 detail_url 的最后一个 ``-长度``。"""

    if not isinstance(value, str) or not value.strip():
        raise NormalizationError("前缀不能为空")
    text = value.strip()
    if "/" not in text:
        match = re.fullmatch(r"(.+)-([0-9]{1,3})", text)
        if match is None:
            raise NormalizationError("前缀必须包含 /，或使用 locator 的 -长度 形式")
        text = "{}/{}".format(match.group(1), match.group(2))
    try:
        return str(ipaddress.ip_network(text, strict=False))
    except ValueError as error:
        raise NormalizationError("无效前缀：{}".format(value)) from error


def normalize_country_code(value: Any) -> str:
    """规范 ISO 3166-1 alpha-2 形式；空字符串不生成“未知国家”。"""

    if not isinstance(value, str):
        raise NormalizationError("国家代码必须是字符串")
    text = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", text):
        raise NormalizationError("国家代码必须是两位 ASCII 字母")
    return text


def normalize_risk_level(value: Any) -> Optional[str]:
    """统一风险等级；缺失保持 ``None``，绝不伪造成低风险。"""

    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = str(value).strip().lower()
    aliases = {
        "low": "low",
        "l": "low",
        "低": "low",
        "middle": "middle",
        "medium": "middle",
        "mid": "middle",
        "m": "middle",
        "中": "middle",
        "high": "high",
        "h": "high",
        "高": "high",
        "unknown": "unknown",
        "未知": "unknown",
    }
    return aliases.get(text, "unknown")


def _structured_collection(value: Any) -> Tuple[Optional[List[Any]], str]:
    """返回原始集合与 presence 状态，保持“缺失”和“显式空集合”差异。"""

    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "not_retained"
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value), "observed_empty" if not value else "observed_nonempty"
    if isinstance(value, str):
        text = value.strip()
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(text)
            except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, (list, tuple, set, frozenset)):
                values = list(parsed)
                return values, "observed_empty" if not values else "observed_nonempty"
        if "," in text:
            values = [item.strip() for item in text.split(",") if item.strip()]
            return values, "observed_empty" if not values else "observed_nonempty"
        return [text], "observed_nonempty"
    return [value], "observed_nonempty"


def normalize_collection(value: Any, item_kind: str) -> Dict[str, Any]:
    """规范 ASN/前缀/国家集合并显式返回空集合语义。

    无法规范的成员不会被静默丢弃；结果进入 ``rejected_values``，整体状态变为
    ``legacy_unknown``。显式空集合的 ``supports_recovery`` 永远为 ``False``。
    """

    normalizers: Dict[str, Callable[[Any], str]] = {
        "asn": normalize_asn,
        "prefix": normalize_prefix,
        "country": normalize_country_code,
        "text": lambda item: str(item).strip(),
    }
    if item_kind not in normalizers:
        raise NormalizationError("未知集合成员类型：{}".format(item_kind))
    raw_values, presence = _structured_collection(value)
    if raw_values is None:
        return {
            "values": None,
            "status": "not_retained",
            "missing_reason": "legacy_field_not_retained",
            "rejected_values": [],
            "supports_recovery": False,
        }

    values: List[str] = []
    rejected: List[Any] = []
    for item in raw_values:
        try:
            normalized = normalizers[item_kind](item)
            if not normalized:
                raise NormalizationError("规范值为空")
            values.append(normalized)
        except (NormalizationError, ValueError, TypeError):
            rejected.append(_json_ready(item))
    unique_values = sorted(set(values))
    status = presence if not rejected else "legacy_unknown"
    return {
        "values": unique_values,
        "status": status,
        "missing_reason": "collection_contains_invalid_members" if rejected else None,
        "rejected_values": sorted(rejected, key=canonical_json),
        "supports_recovery": False,
    }


def _structured_observation(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    for loader in (json.loads, ast.literal_eval):
        try:
            return loader(text)
        except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
            continue
    return text


def _observation_is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, frozenset, Mapping)):
        return len(value) == 0
    return False


def normalize_phase(
    raw_value: Any,
    *,
    source_field: Optional[str],
    applicable: bool,
    retained: bool,
    collision: bool = False,
) -> Dict[str, Any]:
    """规范单阶段路径快照，且从不把空集合解释为恢复证据。"""

    common = {
        "source_field": source_field,
        "semantics": "route_observation_not_causal_trace",
        "supports_recovery": False,
    }
    if not applicable:
        return {
            **common,
            "status": "not_applicable",
            "missing_reason": "event_type_phase_not_applicable",
            "observations": None,
        }
    if collision:
        return {
            **common,
            "status": "source_fact_collision",
            "missing_reason": "fact_record_reused_by_multiple_incidents",
            "observations": None,
        }
    if not retained or raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
        return {
            **common,
            "status": "not_retained",
            "missing_reason": "legacy_field_not_retained",
            "observations": None,
        }
    structured = _structured_observation(raw_value)
    if _observation_is_empty(structured):
        return {
            **common,
            "status": "observed_no_path_in_snapshot",
            "missing_reason": None,
            "observations": [],
        }
    return {
        **common,
        "status": "observed_paths",
        "missing_reason": None,
        "observations": _json_ready(structured),
    }


def _normalize_problem(kind: str, value: Any) -> str:
    if kind == "prefix":
        return normalize_prefix(value)
    if kind == "asn":
        return normalize_native_asn_key(value)
    if kind == "country":
        return normalize_country_code(value)
    raise NormalizationError("未知 locator problem 类型")


def parse_detail_url(detail_url: Any) -> Dict[str, Any]:
    """严格解析六类五段式 detail_url，并保留原始 Incident identity 字段。"""

    if not isinstance(detail_url, str) or detail_url != detail_url.strip():
        raise LocatorError("detail_url 必须是无首尾空白的字符串")
    parts = detail_url.split("/")
    if len(parts) != 5:
        raise LocatorError("detail_url 必须恰好包含五段")
    event_type, start_time, problem, event_id_text, source = parts
    if event_type not in EVENT_SPECS:
        raise LocatorError("detail_url 事件类型不在六类范围")
    if not DETAIL_TIME_RE.fullmatch(start_time):
        raise LocatorError("detail_url 时间格式必须是 YYYY-MM-DD HH:MM:SS")
    try:
        parsed_time = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
    except ValueError as error:
        raise LocatorError("detail_url 时间值非法") from error
    if not problem:
        raise LocatorError("detail_url problem 不能为空")
    if not re.fullmatch(r"[0-9]+", event_id_text):
        raise LocatorError("detail_url event_id 必须是非负整数")
    if not SOURCE_RE.fullmatch(source):
        raise LocatorError("detail_url source 非法")
    spec = EVENT_SPECS[event_type]
    try:
        normalized_problem = _normalize_problem(spec["problem_kind"], problem)
    except NormalizationError as error:
        raise LocatorError(str(error)) from error
    event_id = int(event_id_text)
    source_table = "{}_{}".format(spec["family"], parsed_time.strftime("%Y%m"))
    locator_key = {
        "source": source,
        spec["problem_field"]: normalized_problem,
        spec["event_id_field"]: event_id,
    }
    missing_key_fields = sorted(set(spec["primary_key"]) - set(spec["locator_key"]))
    return {
        "event_type": event_type,
        "start_time": start_time,
        "event_time_utc": business_time_to_utc(start_time),
        "problem": problem,
        "normalized_problem": normalized_problem,
        "problem_kind": spec["problem_kind"],
        "event_id": event_id,
        "source": source,
        "source_table": source_table,
        "locator_key": locator_key,
        "locator_risks": [
            "native_key_component_not_in_detail_url:{}".format(field)
            for field in missing_key_fields
        ],
        "detail_reference": detail_url,
        "incident_id": incident_id_v1(event_type, start_time, problem, event_id, source),
    }


def _normalize_event_type(value: Any) -> Optional[str]:
    if value in EVENT_SPECS:
        return str(value)
    return LABEL_EVENT_TYPES.get(value)


def _event_type_from_table(source_table: Any) -> Optional[str]:
    if not isinstance(source_table, str):
        return None
    match = TABLE_RE.fullmatch(source_table)
    if match is None:
        return None
    family = match.group(1)
    for event_type, spec in EVENT_SPECS.items():
        if spec["family"] == family:
            return event_type
    return None


def _source_table_for_fact(event_type: str, row: Mapping[str, Any]) -> str:
    explicit = row.get("source_table")
    if isinstance(explicit, str) and explicit:
        return explicit
    source_time = row.get("s_time", row.get("start_time"))
    parsed = _parse_datetime(source_time, field_name="事实 s_time")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(BUSINESS_TIMEZONE).replace(tzinfo=None)
    return "{}_{}".format(EVENT_SPECS[event_type]["family"], parsed.strftime("%Y%m"))


def _normalize_key_field(field: str, value: Any) -> Any:
    if field == "source":
        if not isinstance(value, str) or not SOURCE_RE.fullmatch(value):
            raise NormalizationError("source 非法")
        return value
    if field in ("prefix",):
        return normalize_prefix(value)
    if field in ("asn",):
        return normalize_native_asn_key(value)
    if field in ("country",):
        return normalize_country_code(value)
    if field.endswith("id") or field == "outage_id":
        if isinstance(value, bool) or not str(value).isdigit():
            raise NormalizationError("事实事件 ID 非法")
        return int(value)
    return _json_ready(value)


def _native_primary_key(event_type: str, row: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    primary_key: Dict[str, Any] = {}
    errors: List[str] = []
    for field in EVENT_SPECS[event_type]["primary_key"]:
        raw = row.get(field)
        try:
            primary_key[field] = _normalize_key_field(field, raw)
        except NormalizationError:
            primary_key[field] = _json_ready(raw)
            errors.append(field)
    return primary_key, errors


def fact_source_primary_key(event_type: str, row: Mapping[str, Any]) -> Dict[str, Any]:
    """返回六类事实的完整规范主键；任一主键字段非法时拒绝。"""

    if event_type not in EVENT_SPECS:
        raise NormalizationError("未知事件类型：{}".format(event_type))
    primary_key, invalid_fields = _native_primary_key(event_type, row)
    if invalid_fields:
        raise NormalizationError(
            "事实主键字段非法：{}".format(",".join(sorted(invalid_fields)))
        )
    return primary_key


def _match_key(event_type: str, source_table: str, values: Mapping[str, Any]) -> str:
    identity = {
        "event_type": event_type,
        "source_table": source_table,
        "locator_key": {field: values.get(field) for field in EVENT_SPECS[event_type]["locator_key"]},
    }
    return canonical_json(identity)


def _parse_window_boundary(value: Any, name: str) -> datetime:
    parsed = _parse_datetime(value, field_name=name)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BUSINESS_TIMEZONE)
    return parsed.astimezone(UTC)


def _within_window(value: Any, start: datetime, end_exclusive: datetime) -> bool:
    parsed_utc = _parse_window_boundary(value, "记录时间")
    return start <= parsed_utc < end_exclusive


def _embedded_times(value: Any) -> List[str]:
    if not isinstance(value, str):
        return []
    return [match.group(1).replace("T", " ") for match in EMBEDDED_TIME_RE.finditer(value)]


def _fact_quarantine_reasons(
    event_type: str,
    source_table: str,
    row: Mapping[str, Any],
    invalid_key_fields: Sequence[str],
    window_start: datetime,
    window_end_exclusive: datetime,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    reasons: List[str] = []
    evidence: List[Dict[str, Any]] = []
    if invalid_key_fields:
        reasons.append("invalid_identity")
        evidence.append({"reason": "invalid_identity", "fields": sorted(invalid_key_fields)})

    start_value = row.get("s_time", row.get("start_time"))
    if start_value in (None, ""):
        reasons.append("invalid_time")
        evidence.append({"reason": "invalid_time", "field": "s_time"})
    else:
        try:
            if not _within_window(start_value, window_start, window_end_exclusive):
                reasons.append("legacy_window_contamination")
                evidence.append({"reason": "legacy_window_contamination", "field": "s_time"})
            local_time = _parse_datetime(start_value, field_name="事实 s_time")
            if local_time.tzinfo is not None:
                local_time = local_time.astimezone(BUSINESS_TIMEZONE).replace(tzinfo=None)
            expected_table = "{}_{}".format(
                EVENT_SPECS[event_type]["family"], local_time.strftime("%Y%m")
            )
            if source_table != expected_table:
                reasons.append("legacy_window_contamination")
                evidence.append(
                    {
                        "reason": "legacy_window_contamination",
                        "field": "source_table",
                        "expected": expected_table,
                        "observed": source_table,
                    }
                )
        except NormalizationError:
            reasons.append("invalid_time")
            evidence.append({"reason": "invalid_time", "field": "s_time"})

    # 历史 event_info 可能含有与主时间冲突的日期。仅把可严格解析且越界的
    # 时间作为污染证据，不尝试从自然语言补写事件时间。
    for embedded in _embedded_times(row.get("event_info")):
        try:
            if not _within_window(embedded, window_start, window_end_exclusive):
                reasons.append("legacy_window_contamination")
                evidence.append(
                    {
                        "reason": "legacy_window_contamination",
                        "field": "event_info",
                        "observed_time": embedded,
                    }
                )
        except NormalizationError:
            continue
    return sorted(set(reasons)), sorted(evidence, key=canonical_json)


def _parse_duration_seconds(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, timedelta):
        seconds = value.total_seconds()
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
    elif isinstance(value, str):
        text = value.strip()
        match = re.fullmatch(
            r"(?:(?P<days>[0-9]+)\s+days?,?\s*)?(?P<hours>[0-9]+):(?P<minutes>[0-9]{2}):(?P<seconds>[0-9]{2})",
            text,
        )
        if match is None:
            return None
        seconds = (
            int(match.group("days") or 0) * 86400
            + int(match.group("hours")) * 3600
            + int(match.group("minutes")) * 60
            + int(match.group("seconds"))
        )
    else:
        return None
    if not math.isfinite(seconds) or seconds < 0 or not float(seconds).is_integer():
        return None
    return int(seconds)


def _duration_fields(
    locator: Mapping[str, Any], fact: Optional[Mapping[str, Any]], collision: bool
) -> Tuple[Optional[str], Optional[int], List[Dict[str, Any]]]:
    quality: List[Dict[str, Any]] = []
    if fact is None:
        return None, None, [
            {"field": "end_time_utc", "status": "legacy_unknown", "missing_reason": "fact_unresolved"},
            {"field": "duration_seconds", "status": "legacy_unknown", "missing_reason": "fact_unresolved"},
        ]
    if collision:
        return None, None, [
            {"field": "end_time_utc", "status": "source_fact_collision", "missing_reason": "fact_record_reused_by_multiple_incidents"},
            {"field": "duration_seconds", "status": "source_fact_collision", "missing_reason": "fact_record_reused_by_multiple_incidents"},
        ]
    end_raw = fact.get("e_time", fact.get("end_time"))
    duration_raw = fact.get("duration")
    if end_raw in (None, ""):
        quality.extend(
            [
                {"field": "end_time_utc", "status": "not_retained", "missing_reason": "legacy_field_not_retained"},
                {"field": "duration_seconds", "status": "not_retained", "missing_reason": "event_end_not_retained"},
            ]
        )
        return None, None, quality
    try:
        end_time_utc = business_time_to_utc(end_raw)
        start_dt = _parse_window_boundary(locator["start_time"], "incident start")
        end_dt = _parse_window_boundary(end_raw, "fact end")
    except NormalizationError:
        return None, None, [
            {"field": "end_time_utc", "status": "legacy_unknown", "missing_reason": "invalid_fact_end_time"},
            {"field": "duration_seconds", "status": "legacy_unknown", "missing_reason": "invalid_fact_end_time"},
        ]
    elapsed = int((end_dt - start_dt).total_seconds())
    retained = _parse_duration_seconds(duration_raw)
    if elapsed < 0:
        return None, None, [
            {
                "field": "end_time_utc",
                "status": "legacy_unknown",
                "missing_reason": "end_before_start",
            },
            {
                "field": "duration_seconds",
                "status": "legacy_unknown",
                "missing_reason": "end_before_start",
            },
        ]
    if retained is None or retained != elapsed:
        return end_time_utc, None, [
            {
                "field": "duration_seconds",
                "status": "legacy_unknown",
                "missing_reason": "duration_not_retained_or_inconsistent",
            }
        ]
    return end_time_utc, retained, quality


def _risk(fact: Optional[Mapping[str, Any]], event_row: Mapping[str, Any], event_type: str) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    candidates: List[Tuple[str, Any]] = []
    for field in EVENT_SPECS[event_type]["risk_fields"]:
        if fact is not None and field in fact:
            candidates.append((field, fact.get(field)))
        if field in event_row:
            candidates.append(("event_table." + field, event_row.get(field)))
    for field, value in candidates:
        normalized = normalize_risk_level(value)
        if normalized is not None:
            quality = []
            if normalized == "unknown" and str(value).strip().lower() not in ("unknown", "未知"):
                quality.append(
                    {"field": "risk_level", "status": "legacy_unknown", "missing_reason": "unrecognized_risk_level", "source_field": field}
                )
            return normalized, quality
    return None, [
        {"field": "risk_level", "status": "not_retained", "missing_reason": "legacy_field_not_retained"}
    ]


def _add_affected(
    objects: Dict[Tuple[str, str, str], Dict[str, Any]],
    *,
    object_type: str,
    value: Any,
    role: str,
    source_field: str,
) -> Optional[Dict[str, Any]]:
    try:
        if object_type == "prefix":
            object_id = normalize_prefix(value)
        elif object_type == "asn":
            object_id = normalize_asn(value)
        elif object_type == "country":
            object_id = normalize_country_code(value)
        else:
            raise NormalizationError("未知影响对象类型")
    except NormalizationError:
        return {
            "field": "affected_objects",
            "status": "legacy_unknown",
            "missing_reason": "invalid_affected_object",
            "source_field": source_field,
            "raw_value": _json_ready(value),
        }
    key = (object_type, object_id, role)
    objects[key] = {
        "object_type": object_type,
        "object_id": object_id,
        "role": role,
        "source_field": source_field,
    }
    return None


def _affected_objects(
    event_type: str,
    locator: Mapping[str, Any],
    fact: Optional[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    objects: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    quality: List[Dict[str, Any]] = []
    collection_quality: List[Dict[str, Any]] = []
    problem_kind = EVENT_SPECS[event_type]["problem_kind"]
    if event_type == "as_outage":
        locator_collection = normalize_collection(locator["normalized_problem"], "asn")
        collection_quality.append({"field": "detail_url.problem", **locator_collection})
        for item in locator_collection["values"] or []:
            error = _add_affected(
                objects,
                object_type="asn",
                value=item,
                role="affected",
                source_field="detail_url.problem",
            )
            if error:
                quality.append(error)
    else:
        error = _add_affected(
            objects,
            object_type=problem_kind,
            value=locator["normalized_problem"],
            role="affected",
            source_field="detail_url.problem",
        )
        if error:
            quality.append(error)
    if fact is None:
        return list(objects.values()), quality, collection_quality

    single_fields: Dict[str, Sequence[Tuple[str, str]]] = {
        "hijack": (("prefix", "prefix"), ("hijacked_as", "asn")),
        "sub_hijack": (("prefix", "prefix"), ("hijacked_prefix", "prefix"), ("hijacked_as", "asn")),
        "leak": (("prefix", "prefix"), ("leak_to", "asn")),
        "prefix_outage": (("prefix", "prefix"), ("asn", "asn")),
        "as_outage": (("asn", "asn"),),
        "country_outage": (("country", "country"),),
    }
    for field, kind in single_fields[event_type]:
        value = fact.get(field)
        if value in (None, ""):
            continue
        # 这些历史字段以集合文本保存，必须展开为多个对象；不能把整个集合
        # 字符串提升为一个虚构 ASN。
        if (
            (event_type == "sub_hijack" and field == "hijacked_as")
            or (event_type in ("prefix_outage", "as_outage") and field == "asn")
        ):
            collection = normalize_collection(value, "asn")
            collection_quality.append({"field": field, **collection})
            for item in collection["values"] or []:
                error = _add_affected(objects, object_type="asn", value=item, role="affected", source_field=field)
                if error:
                    quality.append(error)
            continue
        error = _add_affected(objects, object_type=kind, value=value, role="affected", source_field=field)
        if error:
            quality.append(error)

    collection_fields: Dict[str, Sequence[Tuple[str, str]]] = {
        "as_outage": (("outage_prefixes", "prefix"),),
        "country_outage": (("outage_ases", "asn"),),
    }
    for field, kind in collection_fields.get(event_type, ()):
        collection = normalize_collection(fact.get(field), kind)
        collection_quality.append({"field": field, **collection})
        for item in collection["values"] or []:
            error = _add_affected(objects, object_type=kind, value=item, role="affected", source_field=field)
            if error:
                quality.append(error)
    ordered = [objects[key] for key in sorted(objects)]
    return ordered, sorted(quality, key=canonical_json), sorted(collection_quality, key=lambda item: item["field"])


def _phase_coverage(event_type: str, fact: Optional[Mapping[str, Any]], collision: bool) -> Dict[str, Any]:
    phase_fields = EVENT_SPECS[event_type]["phases"]
    coverage: Dict[str, Any] = {}
    for phase in ("before", "during", "after"):
        source_field = phase_fields.get(phase)
        # leak 的前后阶段是历史未保留；明确完全不适用阶段字段的类型则 not_applicable。
        structurally_applicable = bool(phase_fields) if event_type == "leak" else source_field is not None
        if event_type in ("sub_hijack", "country_outage"):
            structurally_applicable = False
        coverage[phase] = normalize_phase(
            fact.get(source_field) if fact is not None and source_field else None,
            source_field=source_field,
            applicable=structurally_applicable,
            retained=fact is not None and source_field is not None and source_field in fact,
            collision=collision,
        )
    return coverage


@dataclass
class _FactContext:
    index: int
    event_type: str
    source_table: str
    raw: Mapping[str, Any]
    primary_key: Dict[str, Any]
    match_key: Optional[str]
    quarantine_reasons: List[str]
    quarantine_evidence: List[Dict[str, Any]]
    candidate_reference_count: int = 0


@dataclass
class _EventContext:
    index: int
    raw: Mapping[str, Any]
    locator: Dict[str, Any]
    candidates: List[_FactContext]
    link_issues: List[str]
    collision_group_id: Optional[str] = None


def _quarantine_record(
    *,
    source_table: str,
    source_primary_key: Mapping[str, Any],
    reasons: Iterable[str],
    record_kind: str,
    legacy_payload: Mapping[str, Any],
    evidence: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    reason_list = sorted(set(reasons))
    quarantine_id = quarantine_id_v1(source_table, source_primary_key, reason_list)
    return {
        "quarantine_id": quarantine_id,
        "quarantine_id_schema": "quarantine_id_v1",
        "record_kind": record_kind,
        "source_table": source_table,
        "source_primary_key": _json_ready(source_primary_key),
        "reason_codes": reason_list,
        "evidence": [_json_ready(item) for item in (evidence or [])],
        "classification": "observation_only",
        "causal_conclusion": None,
        "legacy_payload": _json_ready(legacy_payload),
    }


def build_quarantine_record(
    *,
    source_table: str,
    source_primary_key: Mapping[str, Any],
    reasons: Iterable[str],
    record_kind: str,
    legacy_payload: Mapping[str, Any],
    evidence: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """构建可落盘的确定性 quarantine 信封。"""

    return _quarantine_record(
        source_table=source_table,
        source_primary_key=source_primary_key,
        reasons=reasons,
        record_kind=record_kind,
        legacy_payload=legacy_payload,
        evidence=evidence,
    )


def build_collision_group(
    *,
    source_table: str,
    source_primary_key: Mapping[str, Any],
    incident_ids: Iterable[str],
) -> Dict[str, Any]:
    """构建一条事实主键被多个 Incident 复用的确定性碰撞组。"""

    normalized_ids = sorted(set(incident_ids))
    group_id = collision_group_id_v1(
        source_table, source_primary_key, normalized_ids
    )
    return {
        "collision_group_id": group_id,
        "collision_group_id_schema": "legacy_collision_group_id_v1",
        "source_table": source_table,
        "source_primary_key": _json_ready(source_primary_key),
        "incident_ids": normalized_ids,
        "reason": "source_fact_reused_by_multiple_incidents",
        "field_status": "source_fact_collision",
        "classification": "observation_only",
        "causal_conclusion": None,
    }


def _prepare_facts(
    fact_rows: Any,
    *,
    window_start: datetime,
    window_end_exclusive: datetime,
) -> Tuple[List[_FactContext], List[Dict[str, Any]]]:
    flattened: List[Tuple[Optional[str], Mapping[str, Any]]] = []
    if isinstance(fact_rows, Mapping):
        for event_type in sorted(fact_rows):
            rows = fact_rows[event_type]
            if event_type not in EVENT_SPECS:
                raise NormalizationError("事实映射包含未知事件类型：{}".format(event_type))
            for row in rows:
                flattened.append((event_type, row))
    else:
        flattened.extend((None, row) for row in fact_rows)

    contexts: List[_FactContext] = []
    quarantine: List[Dict[str, Any]] = []
    for index, (declared_type, raw_row) in enumerate(flattened):
        if not isinstance(raw_row, Mapping):
            raise NormalizationError("事实记录必须是映射")
        row = dict(raw_row)
        event_type = declared_type or _normalize_event_type(row.get("event_type")) or _event_type_from_table(row.get("source_table"))
        if event_type not in EVENT_SPECS:
            source_table = str(row.get("source_table") or "unknown_fact_table")
            key = {"row_ordinal": index}
            quarantine.append(
                _quarantine_record(
                    source_table=source_table,
                    source_primary_key=key,
                    reasons=("invalid_identity",),
                    record_kind="fact_record",
                    legacy_payload=row,
                    evidence=({"reason": "invalid_identity", "field": "event_type"},),
                )
            )
            continue
        try:
            source_table = _source_table_for_fact(event_type, row)
        except NormalizationError:
            source_table = str(row.get("source_table") or (EVENT_SPECS[event_type]["family"] + "_unknown"))
        primary_key, invalid_fields = _native_primary_key(event_type, row)
        reasons, evidence = _fact_quarantine_reasons(
            event_type,
            source_table,
            row,
            invalid_fields,
            window_start,
            window_end_exclusive,
        )
        match_key = None
        if not invalid_fields:
            match_key = _match_key(event_type, source_table, primary_key)
        context = _FactContext(
            index=index,
            event_type=event_type,
            source_table=source_table,
            raw=row,
            primary_key=primary_key,
            match_key=match_key,
            quarantine_reasons=reasons,
            quarantine_evidence=evidence,
        )
        contexts.append(context)
        if reasons:
            quarantine.append(
                _quarantine_record(
                    source_table=source_table,
                    source_primary_key=primary_key,
                    reasons=reasons,
                    record_kind="fact_record",
                    legacy_payload=row,
                    evidence=evidence,
                )
            )
    return contexts, quarantine


def _event_quarantine(
    row: Mapping[str, Any], index: int, reasons: Sequence[str], evidence: Sequence[Mapping[str, Any]]
) -> Dict[str, Any]:
    detail_reference = row.get("detail_url")
    source_table = str(row.get("source_table") or "event_table_unknown")
    return _quarantine_record(
        source_table=source_table,
        source_primary_key={"detail_url": _json_ready(detail_reference), "row_ordinal": index},
        reasons=reasons,
        record_kind="event_reference",
        legacy_payload=row,
        evidence=evidence,
    )


def _build_incident(
    context: _EventContext,
    link_status: str,
    matched_fact: Optional[_FactContext],
) -> Dict[str, Any]:
    locator = context.locator
    fact = matched_fact.raw if matched_fact is not None else None
    collision = link_status == "legacy_collision"
    end_time_utc, duration_seconds, field_quality = _duration_fields(locator, fact, collision)
    risk_level, risk_quality = _risk(fact, context.raw, locator["event_type"])
    affected_objects, object_quality, collection_quality = _affected_objects(
        locator["event_type"], locator, fact
    )
    field_quality.extend(risk_quality)
    field_quality.extend(object_quality)
    field_quality.append(
        {
            "field": "detector_version",
            "status": "not_retained",
            "missing_reason": "legacy_field_not_retained",
        }
    )
    if locator["locator_risks"]:
        field_quality.append(
            {
                "field": "source_primary_key",
                "status": "legacy_unknown" if link_status == "unresolved" else "not_retained",
                "missing_reason": "detail_url_omits_native_key_component",
                "details": locator["locator_risks"],
            }
        )
    for issue in context.link_issues:
        field_quality.append(
            {
                "field": "source_primary_key",
                "status": "legacy_unknown",
                "missing_reason": issue,
            }
        )
    source_primary_key = (
        matched_fact.primary_key if matched_fact is not None else locator["locator_key"]
    )
    return {
        "schema_version": "p0_incident_normalization_v1",
        "incident_id": locator["incident_id"],
        "incident_id_schema": "incident_id_v1",
        "event_type": locator["event_type"],
        "source_code": locator["source"],
        "source_table": locator["source_table"],
        "source_primary_key": source_primary_key,
        "detail_reference": locator["detail_reference"],
        "event_time_utc": locator["event_time_utc"],
        "end_time_utc": end_time_utc,
        "duration_seconds": duration_seconds,
        "risk_level": risk_level,
        "affected_objects": affected_objects,
        "collection_quality": collection_quality,
        "phase_coverage": _phase_coverage(locator["event_type"], fact, collision),
        "fact_link_status": link_status,
        "field_quality": sorted(field_quality, key=canonical_json),
        "collision_group_id": context.collision_group_id,
        "quarantine_id": None,
        "detector_version": None,
        "classification": "observation_only",
        "causal_conclusion": None,
    }


def _fact_start_time_matches(
    locator: Mapping[str, Any], fact_row: Mapping[str, Any]
) -> bool:
    fact_start = fact_row.get("s_time", fact_row.get("start_time"))
    if fact_start in (None, ""):
        return False
    try:
        return business_time_to_utc(fact_start) == locator["event_time_utc"]
    except NormalizationError:
        return False


def normalize_event(
    event_row: Mapping[str, Any],
    fact_row: Optional[Mapping[str, Any]],
    context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """规范单个事件，供只读流式 runner 在完成关联判断后调用。

    ``context`` 支持：

    - ``fact_link_status``：``matched/legacy_collision/unresolved/quarantined``；
    - ``source_table``：runner 已知的事实表名；
    - ``collision_group_id`` / ``quarantine_id``：对应分流对象 ID。

    多行候选、碰撞组成员和窗口污染仍应由 runner 或
    :func:`normalize_event_facts` 在全局上下文中判断。本函数不会自行选择第一条事实。
    """

    if not isinstance(event_row, Mapping):
        raise NormalizationError("事件总表记录必须是映射")
    if fact_row is not None and not isinstance(fact_row, Mapping):
        raise NormalizationError("事实记录必须是映射或 None")
    normalized_context = dict(context or {})
    locator = parse_detail_url(event_row.get("detail_url"))
    declared_type = _normalize_event_type(event_row.get("event_type"))
    if event_row.get("event_type") not in (None, "") and declared_type != locator["event_type"]:
        raise NormalizationError("事件总表类型与 detail_url 不一致")
    if event_row.get("source") not in (None, "", locator["source"]):
        raise NormalizationError("事件总表 source 与 detail_url 不一致")

    status = normalized_context.get(
        "fact_link_status", "matched" if fact_row is not None else "unresolved"
    )
    if status not in ("matched", "legacy_collision", "unresolved", "quarantined"):
        raise NormalizationError("fact_link_status 非法")
    if status in ("matched", "legacy_collision") and fact_row is None:
        raise NormalizationError("已匹配状态必须提供事实记录")
    collision_group_id = normalized_context.get("collision_group_id")
    if status == "legacy_collision" and not collision_group_id:
        raise NormalizationError("legacy_collision 必须提供 collision_group_id")
    quarantine_id = normalized_context.get("quarantine_id")
    if status == "quarantined" and not quarantine_id:
        raise NormalizationError("quarantined 必须提供 quarantine_id")

    matched_fact: Optional[_FactContext] = None
    if fact_row is not None:
        row = dict(fact_row)
        source_table = normalized_context.get("source_table")
        if source_table is None:
            source_table = _source_table_for_fact(locator["event_type"], row)
        if source_table != locator["source_table"]:
            raise NormalizationError("事实表月份与 detail_url 不一致")
        primary_key = fact_source_primary_key(locator["event_type"], row)
        locator_match_key = _match_key(
            locator["event_type"], locator["source_table"], locator["locator_key"]
        )
        fact_match_key = _match_key(locator["event_type"], source_table, primary_key)
        if locator_match_key != fact_match_key:
            raise NormalizationError("事实主键与 detail_url locator 不一致")
        if status == "matched" and not _fact_start_time_matches(locator, row):
            raise NormalizationError("事实 s_time 与 detail_url 时间不一致")
        matched_fact = _FactContext(
            index=0,
            event_type=locator["event_type"],
            source_table=source_table,
            raw=row,
            primary_key=primary_key,
            match_key=fact_match_key,
            quarantine_reasons=[],
            quarantine_evidence=[],
        )
    event_context = _EventContext(
        index=0,
        raw=dict(event_row),
        locator=locator,
        candidates=[matched_fact] if matched_fact is not None else [],
        link_issues=list(normalized_context.get("link_issues", ())),
        collision_group_id=collision_group_id,
    )
    incident = _build_incident(event_context, status, matched_fact)
    incident["quarantine_id"] = quarantine_id
    return incident


def normalize_event_facts(
    event_rows: Iterable[Mapping[str, Any]],
    fact_rows: Any,
    *,
    window_start: Any,
    window_end_exclusive: Any,
) -> Dict[str, Any]:
    """将总表引用与六类事实旁路规范化为 Incident/link/异常分流。

    该函数不访问数据库且不修改输入。调用方必须从唯一数据档显式传入半开
    窗口，避免在规范化代码中复制固定日期。
    """

    start_utc = _parse_window_boundary(window_start, "window_start")
    end_utc = _parse_window_boundary(window_end_exclusive, "window_end_exclusive")
    if start_utc >= end_utc:
        raise NormalizationError("固定窗口必须满足 start < end_exclusive")

    facts, quarantine = _prepare_facts(
        fact_rows, window_start=start_utc, window_end_exclusive=end_utc
    )
    candidate_index: Dict[str, List[_FactContext]] = defaultdict(list)
    for fact in facts:
        if fact.match_key is not None and not fact.quarantine_reasons:
            candidate_index[fact.match_key].append(fact)

    events: List[_EventContext] = []
    for index, raw_event in enumerate(event_rows):
        if not isinstance(raw_event, Mapping):
            raise NormalizationError("事件总表记录必须是映射")
        event_row = dict(raw_event)
        try:
            locator = parse_detail_url(event_row.get("detail_url"))
        except LocatorError as error:
            quarantine.append(
                _event_quarantine(
                    event_row,
                    index,
                    ("invalid_identity",),
                    ({"reason": "invalid_identity", "detail": str(error)},),
                )
            )
            continue

        event_reasons: List[str] = []
        event_evidence: List[Dict[str, Any]] = []
        declared_type = _normalize_event_type(event_row.get("event_type"))
        if event_row.get("event_type") not in (None, "") and declared_type != locator["event_type"]:
            event_reasons.append("invalid_identity")
            event_evidence.append({"reason": "declared_event_type_mismatch"})
        declared_source = event_row.get("source")
        if declared_source not in (None, "") and declared_source != locator["source"]:
            event_reasons.append("invalid_identity")
            event_evidence.append({"reason": "source_mismatch"})
        event_start = event_row.get("s_time", event_row.get("start_time"))
        if event_start not in (None, ""):
            try:
                if business_time_to_utc(event_start) != locator["event_time_utc"]:
                    event_reasons.append("reference_time_mismatch")
                    event_evidence.append(
                        {
                            "reason": "event_time_mismatch",
                            "locator_time": locator["start_time"],
                            "event_row_time": _json_ready(event_start),
                        }
                    )
            except NormalizationError:
                event_reasons.append("invalid_time")
                event_evidence.append({"reason": "invalid_event_row_time"})
        if not _within_window(locator["start_time"], start_utc, end_utc):
            event_reasons.append("legacy_window_contamination")
            event_evidence.append({"reason": "event_time_outside_fixed_window"})
        explicit_table = event_row.get("source_table")
        if isinstance(explicit_table, str) and explicit_table.startswith("event_table_"):
            expected_event_table = "event_table_{}".format(locator["start_time"][:7].replace("-", ""))
            if explicit_table != expected_event_table:
                event_reasons.append("legacy_window_contamination")
                event_evidence.append(
                    {"reason": "event_partition_mismatch", "expected": expected_event_table, "observed": explicit_table}
                )
        if event_reasons:
            quarantine.append(
                _event_quarantine(event_row, index, sorted(set(event_reasons)), event_evidence)
            )
            continue

        match_key = _match_key(locator["event_type"], locator["source_table"], locator["locator_key"])
        candidates = sorted(
            candidate_index.get(match_key, []),
            key=lambda item: canonical_json(item.primary_key),
        )
        for candidate in candidates:
            candidate.candidate_reference_count += 1
        link_issues: List[str] = []
        if not candidates:
            link_issues.append("fact_not_found")
        elif len(candidates) > 1:
            link_issues.append("multiple_fact_candidates")
        elif not _fact_start_time_matches(locator, candidates[0].raw):
            link_issues.append("fact_start_time_mismatch")
        events.append(
            _EventContext(
                index=index,
                raw=event_row,
                locator=locator,
                candidates=candidates,
                link_issues=link_issues,
            )
        )

    # 只在一个 locator 精确落到同一条事实、且被两个不同 Incident 复用时建组。
    by_fact: Dict[int, List[_EventContext]] = defaultdict(list)
    for event in events:
        if len(event.candidates) == 1:
            by_fact[event.candidates[0].index].append(event)
    collision_groups: List[Dict[str, Any]] = []
    for fact_index, fact_events in by_fact.items():
        incident_ids = sorted(set(event.locator["incident_id"] for event in fact_events))
        if len(incident_ids) < 2:
            continue
        fact = next(item for item in facts if item.index == fact_index)
        group_id = collision_group_id_v1(fact.source_table, fact.primary_key, incident_ids)
        for event in fact_events:
            event.collision_group_id = group_id
        collision_groups.append(
            build_collision_group(
                source_table=fact.source_table,
                source_primary_key=fact.primary_key,
                incident_ids=incident_ids,
            )
        )

    incidents: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []
    for event in events:
        if len(event.candidates) == 0:
            status = "unresolved"
            matched = None
        elif len(event.candidates) > 1:
            status = "unresolved"
            matched = None
        else:
            matched = event.candidates[0]
            if event.collision_group_id:
                status = "legacy_collision"
            elif "fact_start_time_mismatch" in event.link_issues:
                status = "unresolved"
                matched = None
            else:
                status = "matched"
        incident = _build_incident(event, status, matched)
        incidents.append(incident)
        links.append(
            {
                "incident_id": event.locator["incident_id"],
                "detail_reference": event.locator["detail_reference"],
                "event_type": event.locator["event_type"],
                "source_table": event.locator["source_table"],
                "status": status,
                "matched_source_primary_key": matched.primary_key if matched is not None else None,
                "candidate_source_primary_keys": [item.primary_key for item in event.candidates],
                "locator_risks": event.locator["locator_risks"],
                "issues": event.link_issues,
                "collision_group_id": event.collision_group_id,
            }
        )

    # 没有成为任何候选的合法事实也是显式分流，不允许在旁路视图中消失。
    existing_quarantine_ids = {item["quarantine_id"] for item in quarantine}
    for fact in facts:
        if fact.quarantine_reasons or fact.candidate_reference_count > 0:
            continue
        record = _quarantine_record(
            source_table=fact.source_table,
            source_primary_key=fact.primary_key,
            reasons=("unreferenced_fact",),
            record_kind="fact_record",
            legacy_payload=fact.raw,
            evidence=({"reason": "fact_has_no_event_reference"},),
        )
        if record["quarantine_id"] not in existing_quarantine_ids:
            quarantine.append(record)
            existing_quarantine_ids.add(record["quarantine_id"])

    incidents.sort(key=lambda item: item["incident_id"])
    links.sort(key=lambda item: (item["incident_id"], item["detail_reference"]))
    collision_groups.sort(key=lambda item: item["collision_group_id"])
    quarantine.sort(key=lambda item: item["quarantine_id"])
    return {
        "schema_version": "p0_fact_normalization_v1",
        "window": {
            "start": start_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "end_exclusive": end_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "source_timezone": BUSINESS_TIMEZONE_NAME,
        },
        "classification": "observation_only",
        "causal_conclusion": None,
        "incidents": incidents,
        "links": links,
        "collision_groups": collision_groups,
        "quarantine": quarantine,
        "summary": {
            "incident_count": len(incidents),
            "matched_count": sum(item["fact_link_status"] == "matched" for item in incidents),
            "legacy_collision_count": sum(item["fact_link_status"] == "legacy_collision" for item in incidents),
            "unresolved_count": sum(item["fact_link_status"] == "unresolved" for item in incidents),
            "collision_group_count": len(collision_groups),
            "quarantine_count": len(quarantine),
        },
    }
