"""P0 Evidence Bundle v2 的确定性、无副作用组装器。

输入是 D2 旁路规范 Incident、D3 RouteEvent/原始记录引用和
MetricSeries 对象。本模块不连接数据库、不读原始文件、不修改输入，也不将
缺失阶段或指标补成 0。AS_PATH 及阶段路径始终只是 Route Observation /
Path Snapshot；本模块不生成因果或恢复结论。

``reproducibility.output_sha256`` 按以下可复算规则生成：先将该字段置为
64 个 ``0``，再对整个 Bundle 的规范 JSON 字节求 SHA256。这避免自引用
哈希，同时使同输入、同版本产生相同字节。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


UTC = timezone.utc
BUNDLE_VERSION = "evidence_bundle_v2"
SCHEMA_ID = "https://domeye.example/contracts/data/evidence-bundle-v2.schema.json"
SCHEMA_VERSION = "2.0.0"

EVENT_TYPES = frozenset(
    {"hijack", "sub_hijack", "leak", "prefix_outage", "as_outage", "country_outage"}
)
PHASES = ("before", "during", "after")
EVIDENCE_PHASES = frozenset((*PHASES, "context"))
QUALITY_FLAGS = frozenset(
    {
        "source_fact_collision",
        "invalid_identity",
        "legacy_window_contamination",
        "source_fact_orphan",
        "locator_incomplete",
        "time_partition_mismatch",
        "legacy_mutable_state",
        "partial_raw_coverage",
        "vp_identity_unavailable",
        "processing_gap",
        "phase_not_retained",
    }
)
MISSING_REASONS = frozenset(
    {
        "not_observed",
        "not_retained",
        "not_applicable",
        "source_unavailable",
        "parse_failed",
        "legacy_unknown",
        "processing_gap",
        "source_fact_collision",
        "invalid_identity",
        "legacy_window_contamination",
        "source_fact_orphan",
        "quarantined",
    }
)
VALUE_STATES = frozenset(
    {
        "observed_nonzero",
        "observed_zero",
        "not_observed",
        "not_retained",
        "not_applicable",
        "source_unavailable",
        "processing_gap",
        "parse_failed",
        "legacy_unknown",
        "source_fact_collision",
        "invalid_identity",
        "legacy_window_contamination",
    }
)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
INCIDENT_ID_RE = re.compile(r"^inc_v1_[0-9a-f]{24}$")
ROUTE_EVENT_ID_RE = re.compile(r"^rte_v1_[0-9a-f]{32}$")
RAW_REF_ID_RE = re.compile(r"^raw_v1_[0-9a-f]{32}$")
ARTIFACT_ID_RE = re.compile(r"^art_v1_[0-9a-f]{32}$")
VP_ID_RE = re.compile(r"^vp_v1_[0-9a-f]{32}$")
COLLECTOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class EvidenceBundleError(ValueError):
    """输入不能在不伪造证据或缺失语义的前提下组装。"""


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvidenceBundleError("禁止输出非有限浮点数")
        return 0 if value == 0 else value
    if isinstance(value, Mapping):
        return {
            str(key): _json_ready(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_json_ready(item) for item in value]
        return sorted(items, key=_canonical_json)
    raise EvidenceBundleError("输入含不可序列化对象：{}".format(type(value).__name__))


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_evidence_bundle_bytes(bundle: Mapping[str, Any]) -> bytes:
    """返回键排序、无空白、禁止 NaN 的稳定 UTF-8 JSON。"""

    if not isinstance(bundle, Mapping):
        raise EvidenceBundleError("Evidence Bundle 必须是映射")
    return _canonical_json(bundle).encode("utf-8")


def _stable_id(prefix: str, identity: Mapping[str, Any], length: int) -> str:
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    return prefix + digest[:length]


def evidence_id_v2(
    incident_id: str,
    evidence_kind: str,
    source_ref_ids: Iterable[str],
    *,
    phase: str = "context",
    field_paths: Iterable[str] = (),
) -> str:
    """按 D1 字典的 Incident + kind + source_ref + phase/field 生成 Evidence ID。"""

    if not INCIDENT_ID_RE.fullmatch(incident_id):
        raise EvidenceBundleError("incident_id 不符合 incident_id_v1")
    if not isinstance(evidence_kind, str) or not evidence_kind:
        raise EvidenceBundleError("evidence_kind 不能为空")
    if phase not in EVIDENCE_PHASES:
        raise EvidenceBundleError("证据阶段非法")
    refs = sorted(set(_nonempty_strings(source_ref_ids, "source_ref_ids")))
    paths = sorted(set(_field_paths(field_paths, allow_empty=True)))
    if not refs:
        raise EvidenceBundleError("Evidence 必须有可定位来源")
    identity = {
        "schema": "evidence_id_v2",
        "incident_id": incident_id,
        "evidence_kind": evidence_kind,
        "source_ref_ids": refs,
        "phase": phase,
        "field_paths": paths,
    }
    return _stable_id("ev_v2_", identity, 32)


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidenceBundleError("{} 必须是映射".format(field))
    return value


def _nonempty_strings(values: Iterable[Any], field: str) -> List[str]:
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise EvidenceBundleError("{} 必须是字符串序列".format(field))
    try:
        items = list(values)
    except TypeError as error:
        raise EvidenceBundleError("{} 必须是序列".format(field)) from error
    if any(not isinstance(item, str) or not item for item in items):
        raise EvidenceBundleError("{} 只能包含非空字符串".format(field))
    return items


def _field_paths(values: Iterable[Any], *, allow_empty: bool = False) -> List[str]:
    paths = _nonempty_strings(values, "field_paths")
    if any(not path.startswith("/") for path in paths):
        raise EvidenceBundleError("字段路径必须使用 JSON Pointer 形式")
    if not allow_empty and not paths:
        raise EvidenceBundleError("字段路径不能为空")
    return paths


def _utc_text(value: Any, field: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise EvidenceBundleError("{} 不是有效时间".format(field)) from error
    else:
        raise EvidenceBundleError("{} 必须是带时区的 ISO 8601 时间".format(field))
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond:
        raise EvidenceBundleError("{} 必须带时区且精确到秒".format(field))
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise EvidenceBundleError("{} 必须是 64 位小写 SHA256".format(field))
    return value


def _quality_flags(values: Iterable[Any]) -> List[str]:
    flags = sorted(set(_nonempty_strings(values, "quality_flags")))
    unknown = [flag for flag in flags if flag not in QUALITY_FLAGS]
    if unknown:
        raise EvidenceBundleError("未进入 Evidence 合同的质量标志：{}".format(",".join(unknown)))
    return flags


def _program_version(value: Any, field: str) -> Dict[str, Any]:
    item = _require_mapping(value, field)
    required = {"name", "version", "code_sha256", "config_sha256"}
    if set(item) != required:
        raise EvidenceBundleError("{} 字段必须精确为 {}".format(field, sorted(required)))
    if not isinstance(item["name"], str) or not item["name"]:
        raise EvidenceBundleError("{}.name 不能为空".format(field))
    if not isinstance(item["version"], str) or not item["version"]:
        raise EvidenceBundleError("{}.version 不能为空".format(field))
    config_hash = item["config_sha256"]
    if config_hash is not None:
        config_hash = _sha256(config_hash, field + ".config_sha256")
    return {
        "name": item["name"],
        "version": item["version"],
        "code_sha256": _sha256(item["code_sha256"], field + ".code_sha256"),
        "config_sha256": config_hash,
    }


def _normalize_snapshot(value: Mapping[str, Any]) -> Dict[str, Any]:
    snapshot = _require_mapping(value, "data_snapshot")
    required = {
        "profile_id",
        "profile_sha256",
        "window_start",
        "window_end_exclusive",
        "snapshot_time",
        "business_timezone",
        "database_release_id",
        "overlay_inventory_sha256",
        "raw_source_status",
    }
    if set(snapshot) != required:
        raise EvidenceBundleError("data_snapshot 字段不完整或含未知字段")
    for field in ("profile_id", "business_timezone", "database_release_id"):
        if not isinstance(snapshot[field], str) or not snapshot[field]:
            raise EvidenceBundleError("data_snapshot.{} 不能为空".format(field))
    if snapshot["raw_source_status"] not in {"full", "partial", "unavailable"}:
        raise EvidenceBundleError("data_snapshot.raw_source_status 非法")
    result = {
        "profile_id": snapshot["profile_id"],
        "profile_sha256": _sha256(snapshot["profile_sha256"], "profile_sha256"),
        "window_start": _utc_text(snapshot["window_start"], "window_start"),
        "window_end_exclusive": _utc_text(snapshot["window_end_exclusive"], "window_end_exclusive"),
        "snapshot_time": _utc_text(snapshot["snapshot_time"], "snapshot_time"),
        "business_timezone": snapshot["business_timezone"],
        "database_release_id": snapshot["database_release_id"],
        "overlay_inventory_sha256": _sha256(
            snapshot["overlay_inventory_sha256"], "overlay_inventory_sha256"
        ),
        "raw_source_status": snapshot["raw_source_status"],
    }
    if result["window_start"] >= result["window_end_exclusive"]:
        raise EvidenceBundleError("数据档窗口必须满足 start < end_exclusive")
    return result


def _source_fact_id(source_table: str, primary_key: Mapping[str, Any]) -> str:
    return _stable_id(
        "sf_v1_",
        {
            "schema": "source_fact_ref_id_v1",
            "source_table": source_table,
            "source_primary_key": _json_ready(primary_key),
        },
        24,
    )


def _mapping_id(source_fact_id: str, incident_ids: Sequence[str], status: str) -> str:
    return _stable_id(
        "sfm_v2_",
        {
            "schema": "source_fact_mapping_id_v2",
            "source_fact_id": source_fact_id,
            "incident_ids": sorted(set(incident_ids)),
            "mapping_status": status,
        },
        24,
    )


def _fact_locator(primary_key: Mapping[str, Any]) -> str:
    return ",".join(
        "{}={}".format(name, _canonical_json(value))
        for name, value in sorted(primary_key.items(), key=lambda pair: str(pair[0]))
    )


def _source_fact(
    incident: Mapping[str, Any], *, record_hash: Optional[str], collision: bool
) -> Tuple[Dict[str, Any], str]:
    event_type = incident["event_type"]
    source_table = incident.get("source_table")
    primary_key = _require_mapping(incident.get("source_primary_key"), "source_primary_key")
    if not isinstance(source_table, str) or not source_table:
        raise EvidenceBundleError("Incident source_table 不能为空")
    expected_prefix = {
        "hijack": "hijack_",
        "sub_hijack": "sub_hijack_",
        "leak": "leak_event_",
        "prefix_outage": "prefix_outage_",
        "as_outage": "as_outage_",
        "country_outage": "country_outage_",
    }[event_type]
    if not re.fullmatch(re.escape(expected_prefix) + r"20260[23]", source_table):
        raise EvidenceBundleError(
            "{} 的真实来源表必须位于 202602/202603 且使用冻结表族".format(event_type)
        )
    partition_month = source_table.rsplit("_", 1)[1]
    source_fact_id = _source_fact_id(source_table, primary_key)
    key_fields = [
        {"name": str(name), "value": _json_ready(value)}
        for name, value in sorted(primary_key.items(), key=lambda pair: str(pair[0]))
    ]
    return (
        {
            "source_fact_id": source_fact_id,
            "fact_type": event_type,
            "table_name": source_table,
            "partition_month": partition_month,
            "fact_locator": _fact_locator(primary_key),
            "key_fields": key_fields,
            "fact_role": "mixed_state" if collision else "primary",
            "start_time": _utc_text(incident["event_time_utc"], "incident.event_time_utc"),
            "end_time": (
                None
                if incident.get("end_time_utc") is None
                else _utc_text(incident["end_time_utc"], "incident.end_time_utc")
            ),
            "record_hash": None if record_hash is None else _sha256(record_hash, "source_fact_record_hash"),
        },
        source_fact_id,
    )


def _entity_ref(value: Mapping[str, Any]) -> Dict[str, str]:
    item = _require_mapping(value, "affected_objects[]")
    entity_type = item.get("object_type", item.get("entity_type"))
    entity_id = item.get("object_id", item.get("entity_id"))
    if entity_type not in {"prefix", "asn", "country", "collector", "vantage_point"}:
        raise EvidenceBundleError("影响实体类型非法")
    if not isinstance(entity_id, str) or not entity_id:
        raise EvidenceBundleError("影响实体 ID 不能为空")
    role = item.get("role", "affected")
    if role not in {"affected", "observed_origin", "suspected_origin", "observer", "scope"}:
        role = "affected"
    return {"entity_type": entity_type, "entity_id": entity_id, "role": role}


def _legacy_temporal_summary(
    incident: Mapping[str, Any], *, default_summary: str
) -> str:
    """将 legacy 双时间冲突显式投影到 Bundle 摘要。

    标准 Incident 合同仍需用 locator 时间维持稳定身份，但研究侧可以附加
    ``legacy_temporal_evidence``。一旦附加，就必须完整保持“仅身份锚点、
    文案候选、关系未解析且非因果”的失败关闭语义。
    """

    temporal_value = incident.get("legacy_temporal_evidence")
    if temporal_value is None:
        return default_summary
    temporal = _require_mapping(
        temporal_value, "incident.legacy_temporal_evidence"
    )
    locator = _require_mapping(
        temporal.get("locator_record_start"),
        "incident.legacy_temporal_evidence.locator_record_start",
    )
    candidate = _require_mapping(
        temporal.get("embedded_message_candidate"),
        "incident.legacy_temporal_evidence.embedded_message_candidate",
    )
    locator_utc = _utc_text(
        locator.get("utc"),
        "incident.legacy_temporal_evidence.locator_record_start.utc",
    )
    candidate_utc = _utc_text(
        candidate.get("utc"),
        "incident.legacy_temporal_evidence.embedded_message_candidate.utc",
    )
    if (
        locator.get("role") != "source_record_identity_only"
        or candidate.get("role") != "candidate_event_time_from_legacy_text"
        or temporal.get("relationship_state") != "unresolved_not_causal"
        or temporal.get("single_event_time_merge_allowed") is not False
        or temporal.get("precursor_causality_state") != "undetermined"
    ):
        raise EvidenceBundleError(
            "legacy 双时间证据不得把 locator 或文案候选冒充确认事件时间"
        )
    if incident.get("event_time_utc") != locator_utc:
        raise EvidenceBundleError("Incident 身份时间与 legacy locator 锚点不一致")
    return (
        f"{default_summary}旧 locator 时间 {locator_utc} 仅用于源记录身份，"
        f"不是已确认事件起点；旧文案候选时间为 {candidate_utc}。"
        "两者关系未解析且非因果，不得合并为单一事件时间或据此确认前兆。"
    )


def _incident_payload(incident: Mapping[str, Any]) -> Dict[str, Any]:
    event_type = incident.get("event_type")
    if event_type not in EVENT_TYPES:
        raise EvidenceBundleError("Incident event_type 不在六类范围")
    incident_id = incident.get("incident_id")
    if not isinstance(incident_id, str) or not INCIDENT_ID_RE.fullmatch(incident_id):
        raise EvidenceBundleError("Incident ID 非法")
    if incident.get("classification") not in (None, "observation_only"):
        raise EvidenceBundleError("Incident 分类只能是 observation_only")
    if incident.get("causal_conclusion") is not None:
        raise EvidenceBundleError("禁止传入因果结论")
    entities = sorted(
        (_entity_ref(item) for item in incident.get("affected_objects", ())),
        key=_canonical_json,
    )
    if not entities:
        raise EvidenceBundleError("Incident 必须有至少一个受影响实体")
    source = incident.get("source_code")
    detail = incident.get("detail_reference")
    if not isinstance(source, str) or not source:
        raise EvidenceBundleError("Incident source_code 不能为空")
    if not isinstance(detail, str) or not detail:
        raise EvidenceBundleError("Incident detail_reference 不能为空")
    labels = {
        "hijack": "前缀劫持",
        "sub_hijack": "子前缀劫持",
        "leak": "路由泄漏",
        "prefix_outage": "前缀中断",
        "as_outage": "AS 中断",
        "country_outage": "国家中断",
    }
    default_summary = "固定数据窗口内记录到{} 类型的历史检测事实。".format(
        labels[event_type]
    )
    return {
        "incident_id": incident_id,
        "incident_id_schema": "incident_id_v1",
        "event_type": event_type,
        "source": source,
        "start_time": _utc_text(incident["event_time_utc"], "incident.event_time_utc"),
        "end_time": (
            None
            if incident.get("end_time_utc") is None
            else _utc_text(incident["end_time_utc"], "incident.end_time_utc")
        ),
        "source_timezone": "Asia/Shanghai",
        "affected_entities": entities,
        "summary": _legacy_temporal_summary(
            incident, default_summary=default_summary
        ),
        "detail_url": detail,
        "detection_version": incident.get("detector_version"),
    }


def _registry_item(
    *,
    incident_id: str,
    phase: str,
    kind: str,
    stance: str,
    label: str,
    semantics: str,
    observation_summary: str,
    observed_at: Optional[Any],
    source_ref_ids: Iterable[str],
    field_paths: Iterable[str],
) -> Dict[str, Any]:
    refs = sorted(set(_nonempty_strings(source_ref_ids, "evidence.source_ref_ids")))
    paths = sorted(set(_field_paths(field_paths)))
    return {
        "evidence_id": evidence_id_v2(
            incident_id, kind, refs, phase=phase, field_paths=paths
        ),
        "phase": phase,
        "kind": kind,
        "stance": stance,
        "label": label,
        "semantics": semantics,
        "observation_summary": observation_summary,
        "observed_at": None if observed_at is None else _utc_text(observed_at, "observed_at"),
        "source_ref_ids": refs,
        "field_paths": paths,
    }


def _path_count(observations: Any) -> int:
    if observations is None:
        return 0
    if isinstance(observations, Mapping):
        return max(1, len(observations))
    if isinstance(observations, (list, tuple, set, frozenset)):
        return len(observations)
    if isinstance(observations, str):
        return 1 if observations.strip() else 0
    return 1


def _missing_reason_for_phase(status: str) -> str:
    return {
        "not_retained": "not_retained",
        "not_applicable": "not_applicable",
        "legacy_unknown": "legacy_unknown",
        "source_fact_collision": "source_fact_collision",
    }.get(status, "legacy_unknown")


def _phase_base(
    incident: Mapping[str, Any], source_fact_id: str
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    raw_coverage = _require_mapping(incident.get("phase_coverage"), "phase_coverage")
    phases: Dict[str, Dict[str, Any]] = {}
    registry: List[Dict[str, Any]] = []
    incident_id = incident["incident_id"]
    for phase in PHASES:
        phase_value = _require_mapping(raw_coverage.get(phase), "phase_coverage." + phase)
        source_status = phase_value.get("status")
        observations = phase_value.get("observations")
        field_path = "/phase_coverage/{}".format(phase)
        if source_status == "observed_paths":
            count = _path_count(observations)
            if count < 1:
                raise EvidenceBundleError("observed_paths 不得携带空观测")
            item = _registry_item(
                incident_id=incident_id,
                phase=phase,
                kind="path_snapshot",
                stance="support",
                label="{} 阶段路径快照".format(phase),
                semantics="path_snapshot",
                observation_summary="历史事实表保留了该阶段的 AS_PATH 快照集合。",
                observed_at=incident["event_time_utc"],
                source_ref_ids=(source_fact_id,),
                field_paths=(field_path,),
            )
            registry.append(item)
            phases[phase] = {
                "status": "observed_paths",
                "snapshot_count": 1,
                "path_count": count,
                "evidence_ids": [item["evidence_id"]],
                "route_event_ref_ids": [],
                "missing_reasons": [],
                "quality_flags": ["vp_identity_unavailable"],
            }
        elif source_status == "observed_no_path_in_snapshot":
            item = _registry_item(
                incident_id=incident_id,
                phase=phase,
                kind="path_snapshot",
                stance="context",
                label="{} 阶段空路径快照".format(phase),
                semantics="path_snapshot",
                observation_summary="该历史阶段快照集合为空；这不证明网络已恢复或全网无路由。",
                observed_at=incident["event_time_utc"],
                source_ref_ids=(source_fact_id,),
                field_paths=(field_path,),
            )
            registry.append(item)
            phases[phase] = {
                "status": "observed_no_path",
                "snapshot_count": 1,
                "path_count": 0,
                "evidence_ids": [item["evidence_id"]],
                "route_event_ref_ids": [],
                "missing_reasons": [],
                "quality_flags": ["vp_identity_unavailable"],
            }
        else:
            reason = _missing_reason_for_phase(str(source_status))
            flags: List[str] = []
            if reason == "not_retained":
                flags.append("phase_not_retained")
            if reason == "source_fact_collision":
                flags.extend(("source_fact_collision", "legacy_mutable_state"))
            item = _registry_item(
                incident_id=incident_id,
                phase=phase,
                kind="quality_finding",
                stance="counterevidence",
                label="{} 阶段不可用".format(phase),
                semantics="quality_finding",
                observation_summary="该阶段没有可复核观测，已按明确缺失原因保留。",
                observed_at=None,
                source_ref_ids=(source_fact_id,),
                field_paths=(field_path,),
            )
            registry.append(item)
            phases[phase] = {
                "status": "compromised" if reason == "source_fact_collision" else "not_available",
                "snapshot_count": 0,
                "path_count": 0,
                "evidence_ids": [item["evidence_id"]],
                "route_event_ref_ids": [],
                "missing_reasons": [reason],
                "quality_flags": sorted(set(flags)),
            }
    return phases, registry


def _normalize_raw_refs(values: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    required = {
        "raw_record_ref_id",
        "artifact_id",
        "file_sha256",
        "record_offset",
        "record_length",
        "record_hash",
        "record_ordinal",
        "element_ordinal",
        "collector_id",
        "vp_id",
        "vp_asn",
        "verification_status",
    }
    for index, value in enumerate(values):
        item = _require_mapping(value, "raw_record_refs[{}]".format(index))
        if set(item) != required:
            raise EvidenceBundleError("raw_record_refs[{}] 字段不符合合同".format(index))
        if not isinstance(item["raw_record_ref_id"], str) or not RAW_REF_ID_RE.fullmatch(
            item["raw_record_ref_id"]
        ):
            raise EvidenceBundleError("原始记录引用 ID 非法")
        if not isinstance(item["artifact_id"], str) or not ARTIFACT_ID_RE.fullmatch(
            item["artifact_id"]
        ):
            raise EvidenceBundleError("原始制品 ID 非法")
        file_hash = _sha256(item["file_sha256"], "raw.file_sha256")
        expected_artifact_id = _stable_id(
            "art_v1_",
            {"schema": "artifact_id_v1", "file_sha256": file_hash},
            32,
        )
        if item["artifact_id"] != expected_artifact_id:
            raise EvidenceBundleError("原始制品 ID 与 file_sha256 不一致")
        _sha256(item["record_hash"], "raw.record_hash")
        for field, minimum in (
            ("record_offset", 0),
            ("record_length", 12),
            ("record_ordinal", 0),
            ("element_ordinal", 0),
        ):
            number = item[field]
            if isinstance(number, bool) or not isinstance(number, int) or number < minimum:
                raise EvidenceBundleError("raw.{} 非法".format(field))
        if not isinstance(item["collector_id"], str) or not COLLECTOR_ID_RE.fullmatch(
            item["collector_id"]
        ):
            raise EvidenceBundleError("原始引用 collector_id 非法")
        if not isinstance(item["vp_id"], str) or not VP_ID_RE.fullmatch(item["vp_id"]):
            raise EvidenceBundleError("原始引用 vp_id 非法")
        if (
            isinstance(item["vp_asn"], bool)
            or not isinstance(item["vp_asn"], int)
            or not 0 <= item["vp_asn"] <= 4294967295
        ):
            raise EvidenceBundleError("原始引用 vp_asn 非法")
        if item["verification_status"] not in {"verified", "failed"}:
            raise EvidenceBundleError("原始引用 verification_status 非法")
        refs.append(_json_ready(item))
    refs.sort(key=lambda item: item["raw_record_ref_id"])
    ids = [item["raw_record_ref_id"] for item in refs]
    if len(ids) != len(set(ids)):
        raise EvidenceBundleError("原始记录引用 ID 不得重复")
    return refs


def _normalize_route_links(
    values: Iterable[Mapping[str, Any]], raw_by_id: Mapping[str, Mapping[str, Any]]
) -> List[Dict[str, Any]]:
    links: List[Dict[str, Any]] = []
    required = {
        "route_event_id",
        "route_event_id_schema",
        "schema_version",
        "relation",
        "semantics",
        "lineage_status",
        "observed_at",
        "collector_id",
        "vp_id",
        "vp_asn",
        "raw_record_ref_ids",
        "phase",
    }
    for index, value in enumerate(values):
        item = _require_mapping(value, "route_event_refs[{}]".format(index))
        if set(item) != required:
            raise EvidenceBundleError(
                "route_event_refs[{}] 必须比输出合同额外携带一个 phase".format(index)
            )
        phase = item["phase"]
        if phase not in PHASES:
            raise EvidenceBundleError("RouteEvent phase 非法")
        route = {key: _json_ready(value) for key, value in item.items() if key != "phase"}
        route_id = route["route_event_id"]
        if not isinstance(route_id, str) or not ROUTE_EVENT_ID_RE.fullmatch(route_id):
            raise EvidenceBundleError("RouteEvent ID 非法")
        raw_ids = route["raw_record_ref_ids"]
        if not isinstance(raw_ids, list) or len(raw_ids) != len(set(raw_ids)):
            raise EvidenceBundleError("RouteEvent raw_record_ref_ids 必须是无重复数组")
        if route["route_event_id_schema"] != "route_event_id_v1":
            raise EvidenceBundleError("RouteEvent ID schema 非法")
        if route["schema_version"] != "route_event_v1":
            raise EvidenceBundleError("RouteEvent schema_version 非法")
        if route["relation"] not in {"supports_observation", "counterevidence", "context"}:
            raise EvidenceBundleError("RouteEvent relation 非法")
        if route["semantics"] not in {"route_observation", "path_snapshot"}:
            raise EvidenceBundleError("RouteEvent 路径语义非法")
        unresolved = sorted(set(raw_ids) - set(raw_by_id))
        if unresolved:
            raise EvidenceBundleError("RouteEvent 引用的原始记录未解析：{}".format(",".join(unresolved)))
        if route["lineage_status"] == "raw_traceable":
            if len(raw_ids) != 1:
                raise EvidenceBundleError("raw_traceable RouteEvent 必须精确定位一个原始 record element")
            for raw_id in raw_ids:
                raw = raw_by_id[raw_id]
                if raw["verification_status"] != "verified":
                    raise EvidenceBundleError("raw_traceable 原始记录必须已验证")
                for field in ("collector_id", "vp_id", "vp_asn"):
                    if route[field] != raw[field]:
                        raise EvidenceBundleError("RouteEvent 与原始记录的 {} 不一致".format(field))
                expected_route_id = _stable_id(
                    "rte_v1_",
                    {
                        "schema": "route_event_id_v1",
                        "file_sha256": raw["file_sha256"],
                        "record_ordinal": raw["record_ordinal"],
                        "element_ordinal": raw["element_ordinal"],
                    },
                    32,
                )
                if route_id != expected_route_id:
                    raise EvidenceBundleError("RouteEvent ID 与不可变原始坐标不一致")
        elif route["lineage_status"] == "legacy_untraceable":
            if raw_ids:
                raise EvidenceBundleError("legacy_untraceable RouteEvent 不得伪造原始引用")
        else:
            raise EvidenceBundleError("RouteEvent lineage_status 非法")
        route["observed_at"] = _utc_text(route["observed_at"], "RouteEvent observed_at")
        route["raw_record_ref_ids"] = sorted(raw_ids)
        links.append({"phase": phase, "route_event_ref": route})
    links.sort(key=lambda item: item["route_event_ref"]["route_event_id"])
    ids = [item["route_event_ref"]["route_event_id"] for item in links]
    if len(ids) != len(set(ids)):
        raise EvidenceBundleError("RouteEvent ID 不得重复")
    used_raw = {
        raw_id
        for link in links
        for raw_id in link["route_event_ref"]["raw_record_ref_ids"]
    }
    unused_raw = sorted(set(raw_by_id) - used_raw)
    if unused_raw:
        raise EvidenceBundleError("原始记录必须被 RouteEvent 解析引用：{}".format(",".join(unused_raw)))
    return links


def _project_route_event_records(
    values: Iterable[Mapping[str, Any]],
    lineage: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """将 D3 ``RouteEventIndex.get_route_event`` 的公开输出无损投影为 v2 引用。

    阶段是调查组装语义，不从 AS_PATH 推断；因此每条输入必须由调用方
    显式给出 ``phase``。原始哈希验证状态也不从字段存在性推断，必须
    显式为 ``verified`` 才能进入 raw_traceable Bundle。
    """

    lineage_value = _require_mapping(lineage, "processing_lineage")
    parser = lineage_value.get("parser")
    importer = lineage_value.get("importer")
    import_run_id = lineage_value.get("import_run_id")
    projected_routes: List[Dict[str, Any]] = []
    projected_raw: Dict[str, Dict[str, Any]] = {}
    wrapper_fields = {"phase", "relation", "verification_status", "route_event"}
    required_route_fields = {
        "schema_version",
        "record_kind",
        "route_event_id_schema",
        "route_event_id",
        "collector_id",
        "event_time_utc",
        "vp_id",
        "vp_peer_ip",
        "vp_asn",
        "raw_ref",
        "parser_name",
        "parser_version",
        "importer_name",
        "importer_version",
        "import_run_id",
        "lineage_status",
    }
    for index, value in enumerate(values):
        wrapper = _require_mapping(value, "route_event_records[{}]".format(index))
        if set(wrapper) != wrapper_fields:
            raise EvidenceBundleError(
                "route_event_records[{}] 字段必须精确为 {}".format(
                    index, sorted(wrapper_fields)
                )
            )
        route_event = _require_mapping(
            wrapper["route_event"], "route_event_records[{}].route_event".format(index)
        )
        missing = sorted(required_route_fields - set(route_event))
        if missing:
            raise EvidenceBundleError("D3 RouteEvent 缺少字段：{}".format(",".join(missing)))
        if (
            route_event["schema_version"] != "route_event_v1"
            or route_event["record_kind"] != "route_event"
            or route_event["route_event_id_schema"] != "route_event_id_v1"
            or route_event["lineage_status"] != "raw_traceable"
        ):
            raise EvidenceBundleError("只能投影 D3 已准入的 raw_traceable route_event_v1")
        if wrapper["verification_status"] != "verified":
            raise EvidenceBundleError("D3 RouteEvent 原始引用未验证，不得声明 raw_traceable")
        if wrapper["phase"] not in PHASES:
            raise EvidenceBundleError("D3 RouteEvent phase 非法")
        if wrapper["relation"] not in {"supports_observation", "counterevidence", "context"}:
            raise EvidenceBundleError("D3 RouteEvent relation 非法")
        route_id = route_event["route_event_id"]
        if not isinstance(route_id, str) or not ROUTE_EVENT_ID_RE.fullmatch(route_id):
            raise EvidenceBundleError("D3 RouteEvent ID 非法")
        if route_event["vp_id"] is None or route_event["vp_asn"] is None:
            raise EvidenceBundleError("raw_traceable D3 RouteEvent 必须有完整 VP 身份")
        try:
            peer_ip = str(ipaddress.ip_address(route_event["vp_peer_ip"]))
        except (TypeError, ValueError) as error:
            raise EvidenceBundleError("raw_traceable D3 RouteEvent 的 peer IP 非法") from error
        expected_vp_id = _stable_id(
            "vp_v1_",
            {
                "schema": "vp_id_v1",
                "collector_id": route_event["collector_id"],
                "peer_ip": peer_ip,
                "peer_asn": route_event["vp_asn"],
            },
            32,
        )
        if route_event["vp_id"] != expected_vp_id:
            raise EvidenceBundleError("D3 RouteEvent vp_id 与 collector/peer IP/peer ASN 不一致")
        raw = _require_mapping(route_event["raw_ref"], "D3 RouteEvent.raw_ref")
        raw_fields = {
            "artifact_id",
            "file_sha256",
            "record_ordinal",
            "element_ordinal",
            "record_offset",
            "record_length",
            "record_hash",
        }
        if set(raw) != raw_fields:
            raise EvidenceBundleError("D3 RouteEvent.raw_ref 字段不符合 route-event_v1")
        raw_id = _stable_id(
            "raw_v1_",
            {
                "schema": "raw_record_ref_id_v1",
                "file_sha256": raw["file_sha256"],
                "record_ordinal": raw["record_ordinal"],
                "element_ordinal": raw["element_ordinal"],
            },
            32,
        )
        raw_output = {
            "raw_record_ref_id": raw_id,
            "artifact_id": raw["artifact_id"],
            "file_sha256": raw["file_sha256"],
            "record_offset": raw["record_offset"],
            "record_length": raw["record_length"],
            "record_hash": raw["record_hash"],
            "record_ordinal": raw["record_ordinal"],
            "element_ordinal": raw["element_ordinal"],
            "collector_id": route_event["collector_id"],
            "vp_id": route_event["vp_id"],
            "vp_asn": route_event["vp_asn"],
            "verification_status": "verified",
        }
        existing = projected_raw.get(raw_id)
        if existing is not None and _canonical_json(existing) != _canonical_json(raw_output):
            raise EvidenceBundleError("同一 raw_record_ref_id 投影到不同内容")
        projected_raw[raw_id] = raw_output
        projected_routes.append(
            {
                "route_event_id": route_id,
                "route_event_id_schema": "route_event_id_v1",
                "schema_version": "route_event_v1",
                "relation": wrapper["relation"],
                "semantics": "route_observation",
                "lineage_status": "raw_traceable",
                "observed_at": route_event["event_time_utc"],
                "collector_id": route_event["collector_id"],
                "vp_id": route_event["vp_id"],
                "vp_asn": route_event["vp_asn"],
                "raw_record_ref_ids": [raw_id],
                "phase": wrapper["phase"],
            }
        )
        if not isinstance(parser, Mapping) or (
            parser.get("name") != route_event["parser_name"]
            or parser.get("version") != route_event["parser_version"]
        ):
            raise EvidenceBundleError("D3 RouteEvent 与 Bundle parser 版本不一致")
        if not isinstance(importer, Mapping) or (
            importer.get("name") != route_event["importer_name"]
            or importer.get("version") != route_event["importer_version"]
        ):
            raise EvidenceBundleError("D3 RouteEvent 与 Bundle importer 版本不一致")
        if import_run_id != route_event["import_run_id"]:
            raise EvidenceBundleError("D3 RouteEvent 与 Bundle import_run_id 不一致")
    return projected_routes, [projected_raw[key] for key in sorted(projected_raw)]


def _metric_series_id(series: Mapping[str, Any]) -> str:
    identity = {
        "schema": "metric_series_id_v1",
        "metric_name": series["metric_name"],
        "subject": series["subject"],
        "collector_scope": series["collector_scope"],
        "window": series["window"],
        "formula_version": series["formula_version"],
    }
    return _stable_id("ms_v1_", identity, 24)


def _metric_window(series: Mapping[str, Any]) -> Dict[str, Any]:
    metric = _require_mapping(series, "metric_series[]")
    if metric.get("schema_version") != "metric-series/v1":
        raise EvidenceBundleError("只能组装已准入的 metric-series/v1")
    collector = _require_mapping(metric.get("collector_scope"), "metric.collector_scope")
    collector_ids = sorted(set(_nonempty_strings(collector.get("collector_ids", ()), "collector_ids")))
    if not collector_ids:
        raise EvidenceBundleError("Evidence MetricWindow 不能伪造未知 Collector 范围")
    window = _require_mapping(metric.get("window"), "metric.window")
    coverage = _require_mapping(metric.get("coverage"), "metric.coverage")
    subject = _require_mapping(metric.get("subject"), "metric.subject")
    ranking = _require_mapping(metric.get("ranking_scope"), "metric.ranking_scope")
    scope_map = {
        "not_ranked": "not_ranked",
        "global_all_subjects": "all_observed",
        "operational_asn_cohort": "operational_asn_cohort",
        "explicit_subject_set": "selected_entities",
    }
    aggregation_map = {
        "sum_observation_values": "sum",
        "sum_components": "sum",
        "ratio_of_sums": "ratio",
        "last_observation": "last",
        "count_distinct_incidents": "sum",
        "max_concurrent": "max",
    }
    if ranking.get("scope_kind") not in scope_map:
        raise EvidenceBundleError("MetricSeries ranking_scope 无法投影到 Evidence")
    if metric.get("aggregation") not in aggregation_map:
        raise EvidenceBundleError("MetricSeries aggregation 无法投影到 Evidence")
    missing: Dict[str, int] = {}
    for point in metric.get("points", ()):  # 不根据数值推断缺失，只读明确状态。
        state = _require_mapping(point, "metric.points[]").get("value_state")
        if state in {"observed_nonzero", "observed_zero"}:
            continue
        reason = point.get("missing_reason")
        if reason == "denominator_zero":
            reason = "not_applicable"
        if reason not in MISSING_REASONS:
            raise EvidenceBundleError("MetricSeries 含未准入的缺失原因")
        missing[reason] = missing.get(reason, 0) + 1
    source_refs = []
    for ref in metric.get("source_refs", ()):
        ref_id = _require_mapping(ref, "metric.source_refs[]").get("ref_id")
        if not isinstance(ref_id, str) or not ref_id:
            raise EvidenceBundleError("MetricSeries source_ref 不可定位")
        source_refs.append(ref_id)
    if not source_refs:
        raise EvidenceBundleError("MetricSeries 必须有来源引用")
    granularity = metric.get("granularity_seconds")
    if granularity != 300:
        raise EvidenceBundleError("P0 Evidence 当前只接收 5m MetricSeries")
    return {
        "metric_series_id": _metric_series_id(metric),
        "metric_name": metric["metric_name"],
        "subject": {
            "subject_type": subject["subject_type"],
            "subject_id": subject["subject_id"],
            "scope_kind": scope_map[ranking["scope_kind"]],
            "candidate_count": ranking["candidate_count"],
        },
        "collector_scope": collector_ids,
        "window_start": _utc_text(window["start"], "metric.window.start"),
        "window_end_exclusive": _utc_text(window["end"], "metric.window.end"),
        "granularity": "5m",
        "unit": metric["unit"],
        "aggregation": aggregation_map[metric["aggregation"]],
        "formula_version": metric["formula_version"],
        "expected_sample_count": metric["expected_sample_count"],
        "source_observed_sample_count": metric["source_observed_sample_count"],
        "derived_observed_sample_count": metric["metric_observed_sample_count"],
        "subject_active_sample_count": metric["subject_active_sample_count"],
        "source_coverage_ratio": coverage["source_coverage_ratio"],
        "derived_coverage_ratio": coverage["metric_coverage_ratio"],
        "missing_counts": [
            {"reason": reason, "count": missing[reason]} for reason in sorted(missing)
        ],
        "source_refs": sorted(set(source_refs)),
    }


def _coverage_dimension(expected: int, observed: int, *, applicable: bool = True) -> Dict[str, Any]:
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        raise EvidenceBundleError("覆盖期望数必须是非负整数")
    if isinstance(observed, bool) or not isinstance(observed, int) or observed < 0 or observed > expected:
        raise EvidenceBundleError("覆盖实得数必须位于 0..expected")
    if not applicable:
        return {"expected_count": 0, "observed_count": 0, "coverage_ratio": None, "status": "not_applicable"}
    ratio = None if expected == 0 else round(observed / expected, 9)
    status = "full" if observed == expected else ("none" if observed == 0 else "partial")
    return {
        "expected_count": expected,
        "observed_count": observed,
        "coverage_ratio": ratio,
        "status": status,
    }


def _normalize_coverage(value: Mapping[str, Any]) -> Dict[str, Any]:
    coverage = _require_mapping(value, "raw_source_coverage")
    if set(coverage) != {"expected_count", "observed_count"}:
        raise EvidenceBundleError("raw_source_coverage 只能含 expected_count/observed_count")
    return _coverage_dimension(coverage["expected_count"], coverage["observed_count"])


def _field_quality(
    incident: Mapping[str, Any], source_fact_id: str, collision_group_id: Optional[str]
) -> List[Dict[str, Any]]:
    result = [
        {
            "field_path": "/incident/start_time",
            "value_state": "observed_nonzero",
            "missing_reason": None,
            "source_ref_ids": [source_fact_id],
            "correction_ref_ids": [],
            "quality_flags": [],
        }
    ]
    for quality in incident.get("field_quality", ()):
        item = _require_mapping(quality, "incident.field_quality[]")
        status = item.get("status")
        if status in {"matched", "observed", "observed_paths", "observed_nonzero"}:
            value_state = "observed_nonzero"
            reason = None
        elif status in VALUE_STATES:
            value_state = status
            reason = status
        else:
            # D2 的细粒度 reason 不是 Evidence MissingReason；按其已冻结 status 投影。
            value_state = "legacy_unknown"
            reason = "legacy_unknown"
        if value_state in {"observed_nonzero", "observed_zero"}:
            reason = None
        field = item.get("field")
        if not isinstance(field, str) or not field:
            raise EvidenceBundleError("Incident field_quality.field 不能为空")
        refs = [source_fact_id]
        flags: List[str] = []
        if value_state == "source_fact_collision":
            if collision_group_id is None:
                raise EvidenceBundleError("碰撞字段必须引用 collision_group_id")
            refs.append(collision_group_id)
            flags.extend(("source_fact_collision", "legacy_mutable_state"))
        result.append(
            {
                "field_path": "/incident/{}".format(field.replace(".", "/")),
                "value_state": value_state,
                "missing_reason": reason,
                "source_ref_ids": sorted(refs),
                "correction_ref_ids": [],
                "quality_flags": sorted(set(flags)),
            }
        )
    dedup: Dict[str, Dict[str, Any]] = {}
    for item in result:
        # 同一字段优先保留显式缺失/碰撞，不用默认 observed 覆盖。
        existing = dedup.get(item["field_path"])
        if existing is None or existing["value_state"].startswith("observed"):
            dedup[item["field_path"]] = item
    return [dedup[path] for path in sorted(dedup)]


def _missing_counts(phases: Mapping[str, Mapping[str, Any]], metrics: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for phase in phases.values():
        for reason in phase["missing_reasons"]:
            counts[reason] = counts.get(reason, 0) + 1
    for metric in metrics:
        for item in metric["missing_counts"]:
            counts[item["reason"]] = counts.get(item["reason"], 0) + item["count"]
    return [{"reason": reason, "count": counts[reason]} for reason in sorted(counts)]


def _processing_lineage(
    *,
    lineage: Mapping[str, Any],
    generated_at: str,
    raw_complete: bool,
    quality_flags: Sequence[str],
) -> Dict[str, Any]:
    value = _require_mapping(lineage, "processing_lineage")
    allowed = {"parser", "importer", "detector", "normalizer", "bundle_generator", "import_run_id"}
    if set(value) != allowed:
        raise EvidenceBundleError("processing_lineage 字段必须精确为 {}".format(sorted(allowed)))
    parser = None if value["parser"] is None else _program_version(value["parser"], "parser")
    importer = None if value["importer"] is None else _program_version(value["importer"], "importer")
    detector = None if value["detector"] is None else _program_version(value["detector"], "detector")
    normalizer = _program_version(value["normalizer"], "normalizer")
    generator = _program_version(value["bundle_generator"], "bundle_generator")
    import_run_id = value["import_run_id"]
    if import_run_id is not None and (not isinstance(import_run_id, str) or not import_run_id):
        raise EvidenceBundleError("import_run_id 必须为 null 或非空字符串")
    if raw_complete and (parser is None or importer is None or import_run_id is None):
        raise EvidenceBundleError("raw_traceable 必须保留 parser/importer/import_run_id")
    return {
        "lineage_status": "raw_traceable" if raw_complete else "legacy_untraceable",
        "parser": parser,
        "importer": importer,
        "detector": detector,
        "normalizer": normalizer,
        "bundle_generator": generator,
        "import_run_id": import_run_id if raw_complete else None,
        "route_event_schema_version": "route_event_v1" if raw_complete else None,
        "incident_schema_version": "incident_id_v1",
        "generated_at": generated_at,
        "quality_flags": sorted(set(quality_flags)),
    }


def _parameters(value: Mapping[str, Any], incident_id: str) -> List[Dict[str, Any]]:
    parameters = dict(value)
    parameters.setdefault("incident_id", incident_id)
    if not parameters:
        raise EvidenceBundleError("reproducibility parameters 不能为空")
    return [
        {"name": str(name), "value": _json_ready(item)}
        for name, item in sorted(parameters.items(), key=lambda pair: str(pair[0]))
    ]


def validate_reference_closure(bundle: Mapping[str, Any]) -> None:
    """校验 Evidence、RouteEvent、raw、MetricSeries 和阶段引用闭合。"""

    payload = _require_mapping(bundle, "bundle")
    registry = payload.get("evidence_registry")
    if not isinstance(registry, list):
        raise EvidenceBundleError("evidence_registry 必须是数组")
    registry_ids = [item.get("evidence_id") for item in registry if isinstance(item, Mapping)]
    if len(registry_ids) != len(registry) or len(registry_ids) != len(set(registry_ids)):
        raise EvidenceBundleError("Evidence 注册表 ID 必须唯一")
    registered = set(registry_ids)
    evidence_refs = list(payload.get("supporting_evidence_refs", ())) + list(
        payload.get("counterevidence_refs", ())
    )
    for phase in payload.get("phase_coverage", {}).values():
        evidence_refs.extend(phase.get("evidence_ids", ()))
    for limitation in payload.get("limitations", ()):
        evidence_refs.extend(limitation.get("evidence_refs", ()))
    unresolved_evidence = sorted(set(evidence_refs) - registered)
    if unresolved_evidence:
        raise EvidenceBundleError("Evidence ID 引用未闭合：{}".format(",".join(unresolved_evidence)))

    route_ids = {item["route_event_id"] for item in payload.get("route_event_refs", ())}
    raw_ids = {item["raw_record_ref_id"] for item in payload.get("raw_record_refs", ())}
    metric_ids = {item["metric_series_id"] for item in payload.get("metric_windows", ())}
    fact_ids = {
        item["source_fact_id"] for item in payload.get("source_fact_mapping", {}).get("source_facts", ())
    }
    correction_ids = {item["correction_id"] for item in payload.get("correction_refs", ())}
    auxiliary = set()
    mapping = payload.get("source_fact_mapping", {})
    for field in ("collision_group_id", "quarantine_id"):
        if mapping.get(field):
            auxiliary.add(mapping[field])
    resolvable_sources = route_ids | raw_ids | metric_ids | fact_ids | correction_ids | auxiliary
    for item in registry:
        unresolved = sorted(set(item["source_ref_ids"]) - resolvable_sources)
        if unresolved:
            raise EvidenceBundleError("证据来源未解析：{}".format(",".join(unresolved)))
    for item in payload.get("field_quality", ()):
        unresolved = sorted(set(item["source_ref_ids"]) - resolvable_sources)
        if unresolved:
            raise EvidenceBundleError("字段质量来源未解析：{}".format(",".join(unresolved)))
    for phase in payload.get("phase_coverage", {}).values():
        unresolved = sorted(set(phase["route_event_ref_ids"]) - route_ids)
        if unresolved:
            raise EvidenceBundleError("阶段 RouteEvent 引用未闭合：{}".format(",".join(unresolved)))
    for route in payload.get("route_event_refs", ()):
        unresolved = sorted(set(route["raw_record_ref_ids"]) - raw_ids)
        if unresolved:
            raise EvidenceBundleError("RouteEvent 原始引用未闭合：{}".format(",".join(unresolved)))


def build_evidence_bundle_v2(
    incident: Mapping[str, Any],
    *,
    data_snapshot: Mapping[str, Any],
    processing_lineage: Mapping[str, Any],
    raw_source_coverage: Mapping[str, Any],
    generated_at: Any,
    input_snapshot_sha256: str,
    query_fingerprint_sha256: str,
    source_hash_verification_status: str,
    route_event_refs: Iterable[Mapping[str, Any]] = (),
    raw_record_refs: Iterable[Mapping[str, Any]] = (),
    route_event_records: Iterable[Mapping[str, Any]] = (),
    metric_series: Iterable[Mapping[str, Any]] = (),
    source_fact_record_hash: Optional[str] = None,
    collision_group: Optional[Mapping[str, Any]] = None,
    correction_refs: Iterable[Mapping[str, Any]] = (),
    reproducibility_parameters: Optional[Mapping[str, Any]] = None,
    v1_bundle_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """将一个 D2 规范 Incident 组装为严格 Evidence Bundle v2。

    优先使用 ``route_event_records`` 直接传入 D3 ``RouteEventIndex`` 的完整
    RouteEvent；每条 wrapper 显式携带 ``phase/relation/verification_status``。
    低层兼容入口 ``route_event_refs`` 的每个对象与 Schema 中 ``RouteEventRef`` 一致，
    但输入额外必须携带 ``phase=before|during|after``；输出会去掉该组装
    字段。任一原始引用未解析、未验证或 VP/版本不完整时，禁止
    声明 ``raw_traceable``。
    """

    source_incident = deepcopy(_require_mapping(incident, "incident"))
    normalized_snapshot = _normalize_snapshot(data_snapshot)
    generated = _utc_text(generated_at, "generated_at")
    incident_payload = _incident_payload(source_incident)
    incident_id = incident_payload["incident_id"]
    if not (
        normalized_snapshot["window_start"]
        <= incident_payload["start_time"]
        < normalized_snapshot["window_end_exclusive"]
    ):
        raise EvidenceBundleError("Incident 开始时间越出固定半开窗口")
    if (
        incident_payload["end_time"] is not None
        and incident_payload["end_time"] < incident_payload["start_time"]
    ):
        raise EvidenceBundleError("Incident end_time 早于 start_time")
    link_status = source_incident.get("fact_link_status")
    if link_status not in {"matched", "legacy_collision"}:
        raise EvidenceBundleError(
            "Evidence v2 可见 Incident 只接收 matched/legacy_collision；未解析或隔离记录不得伪造可见 Incident"
        )
    is_collision = link_status == "legacy_collision"
    source_fact, source_fact_id = _source_fact(
        source_incident, record_hash=source_fact_record_hash, collision=is_collision
    )

    collision_payload = None
    collision_group_id = None
    mapping_incident_ids = [incident_id]
    if is_collision:
        value = _require_mapping(collision_group, "collision_group")
        collision_group_id = value.get("collision_group_id")
        if collision_group_id != source_incident.get("collision_group_id"):
            raise EvidenceBundleError("Incident 与 collision_group ID 不一致")
        mapping_incident_ids = sorted(set(_nonempty_strings(value.get("incident_ids", ()), "incident_ids")))
        if len(mapping_incident_ids) < 2 or incident_id not in mapping_incident_ids:
            raise EvidenceBundleError("碰撞组必须包含当前 Incident 及另一 Incident")
        conflicted = sorted(set(_field_paths(value.get("conflicted_fields", ()))))
        collision_payload = {
            "collision_group_id": collision_group_id,
            "incident_ids": mapping_incident_ids,
            "source_fact_ids": [source_fact_id],
            "conflicted_fields": conflicted,
            "resolution_state": value.get("resolution_state", "unresolved"),
        }

    mapping_status = "collision" if is_collision else "exact"
    source_mapping = {
        "mapping_id": _mapping_id(source_fact_id, mapping_incident_ids, mapping_status),
        "mapping_status": mapping_status,
        "incident_ids": mapping_incident_ids,
        "source_facts": [source_fact],
        "collision_group_id": collision_group_id,
        "quarantine_id": None,
        "quality_flags": (
            ["source_fact_collision", "legacy_mutable_state", "partial_raw_coverage"]
            if is_collision
            else (["partial_raw_coverage"] if normalized_snapshot["raw_source_status"] == "partial" else [])
        ),
    }
    disposition = {
        "status": "legacy_collision" if is_collision else "visible",
        "visibility": "visible",
        "reason_codes": ["source_fact_collision"] if is_collision else [],
        "collision": collision_payload,
        "quarantine": None,
    }

    route_record_values = list(route_event_records)
    route_ref_values = list(route_event_refs)
    raw_ref_values = list(raw_record_refs)
    if route_record_values and (route_ref_values or raw_ref_values):
        raise EvidenceBundleError(
            "route_event_records 不得与低层 route_event_refs/raw_record_refs 混用"
        )
    if route_record_values:
        route_ref_values, raw_ref_values = _project_route_event_records(
            route_record_values, processing_lineage
        )
    normalized_raw = _normalize_raw_refs(raw_ref_values)
    raw_by_id = {item["raw_record_ref_id"]: item for item in normalized_raw}
    route_links = _normalize_route_links(route_ref_values, raw_by_id)
    route_refs = [item["route_event_ref"] for item in route_links]
    raw_complete = bool(route_refs) and all(
        item["lineage_status"] == "raw_traceable" for item in route_refs
    )
    if raw_complete and source_hash_verification_status != "verified":
        raise EvidenceBundleError("raw_traceable 必须通过原始哈希验证")
    if source_hash_verification_status not in {"verified", "partial", "not_available"}:
        raise EvidenceBundleError("source_hash_verification_status 非法")

    phases, phase_registry = _phase_base(source_incident, source_fact_id)
    registry = list(phase_registry)
    fact_evidence = _registry_item(
        incident_id=incident_id,
        phase="context",
        kind="fact_record",
        stance="support",
        label="六类异常源事实",
        semantics="fact_record",
        observation_summary="该 Incident 可定位到只读历史事实表与完整业务主键。",
        observed_at=incident_payload["start_time"],
        source_ref_ids=(source_fact_id,),
        field_paths=("/source_fact_mapping",),
    )
    registry.append(fact_evidence)
    supporting = {fact_evidence["evidence_id"]}
    counter = {
        item["evidence_id"] for item in phase_registry if item["stance"] == "counterevidence"
    }
    path_evidence_ids = {
        item["evidence_id"] for item in phase_registry if item["kind"] == "path_snapshot"
    }

    for link in route_links:
        route = link["route_event_ref"]
        raw_ids = route["raw_record_ref_ids"]
        route_stance = {
            "supports_observation": "support",
            "counterevidence": "counterevidence",
            "context": "context",
        }[route["relation"]]
        route_item = _registry_item(
            incident_id=incident_id,
            phase=link["phase"],
            kind=route["semantics"],
            stance=route_stance,
            label="{} RouteEvent 观测".format(link["phase"]),
            semantics=route["semantics"],
            observation_summary="该 RouteEvent 仅表示指定 Collector/VP 的路由观测。",
            observed_at=route["observed_at"],
            source_ref_ids=(route["route_event_id"], *raw_ids),
            field_paths=("/route_event_refs",),
        )
        registry.append(route_item)
        phases[link["phase"]]["route_event_ref_ids"].append(route["route_event_id"])
        phases[link["phase"]]["evidence_ids"].append(route_item["evidence_id"])
        if phases[link["phase"]]["status"] == "not_available":
            phases[link["phase"]].update(
                {
                    "status": "observed_paths",
                    "snapshot_count": 1,
                    "path_count": 1,
                    "missing_reasons": [],
                    "quality_flags": [],
                }
            )
        else:
            phases[link["phase"]]["snapshot_count"] += 1
            phases[link["phase"]]["path_count"] += 1
        if route_item["stance"] == "counterevidence":
            counter.add(route_item["evidence_id"])
        elif route_item["stance"] == "support":
            supporting.add(route_item["evidence_id"])
        if raw_ids:
            raw_item = _registry_item(
                incident_id=incident_id,
                phase=link["phase"],
                kind="raw_record",
                stance=route_item["stance"],
                label="已校验原始 MRT 记录引用",
                semantics="raw_bgp_record",
                observation_summary="该 RouteEvent 可定位到按哈希、偏移和序号校验的原始记录。",
                observed_at=route["observed_at"],
                source_ref_ids=raw_ids,
                field_paths=("/raw_record_refs",),
            )
            registry.append(raw_item)
            phases[link["phase"]]["evidence_ids"].append(raw_item["evidence_id"])
            if raw_item["stance"] == "counterevidence":
                counter.add(raw_item["evidence_id"])
            elif raw_item["stance"] == "support":
                supporting.add(raw_item["evidence_id"])

    metric_windows = sorted(
        (_metric_window(item) for item in metric_series), key=lambda item: item["metric_series_id"]
    )
    if len({item["metric_series_id"] for item in metric_windows}) != len(metric_windows):
        raise EvidenceBundleError("MetricSeries ID 不得重复")
    for metric in metric_windows:
        metric_item = _registry_item(
            incident_id=incident_id,
            phase="context",
            kind="metric_window",
            stance="context",
            label="{} 指标窗口".format(metric["metric_name"]),
            semantics="metric_observation",
            observation_summary="该指标窗口分开记录来源覆盖、派生覆盖和缺失原因。",
            observed_at=metric["window_end_exclusive"],
            source_ref_ids=(metric["metric_series_id"],),
            field_paths=("/metric_windows",),
        )
        registry.append(metric_item)

    raw_coverage = _normalize_coverage(raw_source_coverage)
    observed_phases = sum(
        phases[phase]["status"] in {"observed_paths", "observed_no_path"} for phase in PHASES
    )
    missing_counts = _missing_counts(phases, metric_windows)
    # ``legacy_unknown`` 是已分类、可解释的历史缺失原因，不是“原因未知”。
    # unknown_missing_reason_count 只统计没有进入冻结 MissingReason 枚举的缺口；
    # 当前构建器会在更早阶段拒绝这类输入，因此正常候选必须为零。
    unknown_missing_count = sum(
        item["count"] for item in missing_counts if item["reason"] not in MISSING_REASONS
    )

    if not raw_complete:
        raw_gap = _registry_item(
            incident_id=incident_id,
            phase="context",
            kind="quality_finding",
            stance="counterevidence",
            label="原始记录血缘不完整",
            semantics="quality_finding",
            observation_summary="该证据包不具备完整可解析的 RouteEvent、原始引用和处理版本。",
            observed_at=None,
            source_ref_ids=(source_fact_id,),
            field_paths=("/route_event_refs", "/raw_record_refs", "/processing_lineage"),
        )
        registry.append(raw_gap)
        counter.add(raw_gap["evidence_id"])
    else:
        raw_gap = None

    limitation_items: List[Dict[str, Any]] = []
    if normalized_snapshot["raw_source_status"] == "partial":
        limitation_items.append(
            {
                "code": "partial_raw_coverage",
                "severity": "warning" if raw_complete else "blocking",
                "scope": "bundle",
                "description": "固定二三月数据档的原始 MRT 只有部分覆盖。",
                "affected_fields": ["/data_snapshot/raw_source_status", "/coverage_summary/raw_source"],
                "evidence_refs": [] if raw_gap is None else [raw_gap["evidence_id"]],
            }
        )
    elif normalized_snapshot["raw_source_status"] == "unavailable":
        limitation_items.append(
            {
                "code": "raw_source_unavailable",
                "severity": "blocking",
                "scope": "raw_record",
                "description": "原始 BGP 制品不可用，仅能按历史事实降级展示。",
                "affected_fields": ["/raw_record_refs"],
                "evidence_refs": [] if raw_gap is None else [raw_gap["evidence_id"]],
            }
        )
    if not raw_complete:
        limitation_items.extend(
            [
                {
                    "code": "vp_identity_unavailable",
                    "severity": "warning",
                    "scope": "route_event",
                    "description": "历史事实未完整保留稳定 VP 身份。",
                    "affected_fields": ["/route_event_refs"],
                    "evidence_refs": [raw_gap["evidence_id"]],
                },
                {
                    "code": "processing_lineage_unavailable",
                    "severity": "warning",
                    "scope": "bundle",
                    "description": "历史数据没有完整原始解析与导入版本血缘。",
                    "affected_fields": ["/processing_lineage"],
                    "evidence_refs": [raw_gap["evidence_id"]],
                },
            ]
        )
    limited_phases = [
        phase
        for phase in PHASES
        if phases[phase]["status"] in {"not_available", "compromised"}
        and "not_retained" in phases[phase]["missing_reasons"]
    ]
    missing_phase_evidence = sorted(
        {
            evidence_id
            for phase in limited_phases
            for evidence_id in phases[phase]["evidence_ids"]
        }
    )
    if missing_phase_evidence:
        limitation_items.append(
            {
                "code": "phase_not_retained",
                "severity": "warning",
                "scope": "phase",
                "description": "一个或多个事件阶段没有可复核观测，已显式保留缺失原因。",
                "affected_fields": [
                    "/phase_coverage/{}".format(phase) for phase in limited_phases
                ],
                "evidence_refs": missing_phase_evidence,
            }
        )
    if path_evidence_ids or route_refs:
        limitation_items.append(
            {
                "code": "path_snapshot_not_causal",
                "severity": "warning",
                "scope": "phase",
                "description": "AS_PATH 快照与 Route Observation 不证明真实转发、全网传播或网络根因。",
                "affected_fields": ["/phase_coverage", "/route_event_refs"],
                "evidence_refs": sorted(path_evidence_ids),
            }
        )
    if is_collision:
        collision_evidence = _registry_item(
            incident_id=incident_id,
            phase="context",
            kind="quality_finding",
            stance="counterevidence",
            label="源事实主键碰撞",
            semantics="quality_finding",
            observation_summary="多个 Incident 复用同一可变历史事实，冲突字段不能完整归属。",
            observed_at=None,
            source_ref_ids=(source_fact_id, collision_group_id),
            field_paths=collision_payload["conflicted_fields"],
        )
        registry.append(collision_evidence)
        counter.add(collision_evidence["evidence_id"])
        limitation_items.append(
            {
                "code": "source_fact_collision",
                "severity": "blocking",
                "scope": "source_fact",
                "description": "同一历史事实被多个 Incident 复用，不生成未经限定的阶段判断。",
                "affected_fields": collision_payload["conflicted_fields"],
                "evidence_refs": [collision_evidence["evidence_id"]],
            }
        )
    if any(
        item["reason"] == "processing_gap" and item["count"] > 0 for item in missing_counts
    ):
        metric_evidence_ids = [
            item["evidence_id"] for item in registry if item["kind"] == "metric_window"
        ]
        limitation_items.append(
            {
                "code": "metric_processing_gap",
                "severity": "warning",
                "scope": "metric",
                "description": "指标窗口包含已分类的处理缺口，这些槽位不补 0。",
                "affected_fields": ["/metric_windows"],
                "evidence_refs": metric_evidence_ids,
            }
        )

    registry.sort(key=lambda item: item["evidence_id"])
    registry_ids = [item["evidence_id"] for item in registry]
    if len(registry_ids) != len(set(registry_ids)):
        raise EvidenceBundleError("Evidence ID identity 碰撞；必须升级 ID schema")
    for phase in PHASES:
        phases[phase]["evidence_ids"] = sorted(set(phases[phase]["evidence_ids"]))
        phases[phase]["route_event_ref_ids"] = sorted(set(phases[phase]["route_event_ref_ids"]))
        phases[phase]["quality_flags"] = sorted(set(phases[phase]["quality_flags"]))

    lineage_flags: List[str] = []
    if normalized_snapshot["raw_source_status"] == "partial":
        lineage_flags.append("partial_raw_coverage")
    if not raw_complete:
        lineage_flags.append("vp_identity_unavailable")
    if any("not_retained" in phases[phase]["missing_reasons"] for phase in PHASES):
        lineage_flags.append("phase_not_retained")
    if is_collision:
        lineage_flags.extend(("source_fact_collision", "legacy_mutable_state"))
    normalized_lineage = _processing_lineage(
        lineage=processing_lineage,
        generated_at=generated,
        raw_complete=raw_complete,
        quality_flags=_quality_flags(lineage_flags),
    )

    normalized_corrections = sorted(
        (_json_ready(_require_mapping(item, "correction_refs[]")) for item in correction_refs),
        key=lambda item: item.get("correction_id", ""),
    )
    raw_admission = raw_complete and not is_collision
    admission_level = (
        "raw_traceable" if raw_admission else ("not_accepted" if is_collision else "legacy_compatible")
    )
    route_expected = max(1, len(route_refs))
    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "bundle_id": _stable_id(
            "eb_v2_",
            {
                "schema": "evidence_bundle_id_v2",
                "incident_id": incident_id,
                "source_fact_mapping_id": source_mapping["mapping_id"],
                "input_snapshot_sha256": _sha256(input_snapshot_sha256, "input_snapshot_sha256"),
                "query_fingerprint_sha256": _sha256(
                    query_fingerprint_sha256, "query_fingerprint_sha256"
                ),
            },
            32,
        ),
        "compatibility": {
            "previous_contract": "evidence_bundle_v1",
            "incident_id_schema": "incident_id_v1",
            "v1_fields_reinterpreted": False,
            "v1_bundle_ref": v1_bundle_ref,
        },
        "subject_kind": "incident",
        "incident": incident_payload,
        "source_fact_mapping": source_mapping,
        "disposition": disposition,
        "data_snapshot": normalized_snapshot,
        "route_event_refs": route_refs,
        "raw_record_refs": normalized_raw,
        "processing_lineage": normalized_lineage,
        "metric_windows": metric_windows,
        "field_quality": _field_quality(source_incident, source_fact_id, collision_group_id),
        "phase_coverage": phases,
        "evidence_registry": registry,
        "supporting_evidence_refs": sorted(supporting),
        "counterevidence_refs": sorted(counter),
        "coverage_summary": {
            "admission_level": admission_level,
            "raw_source": raw_coverage,
            "route_event": _coverage_dimension(route_expected, len(route_refs)),
            "phase": _coverage_dimension(3, observed_phases),
            "evidence_reference_closure": "passed",
            "unexplained_source_fact_count": 0,
            "collision_group_count": 1 if is_collision else 0,
            "quarantine_count": 0,
            "unknown_missing_reason_count": unknown_missing_count,
            "missing_counts": missing_counts,
        },
        "correction_refs": normalized_corrections,
        "reproducibility": {
            "schema_id": SCHEMA_ID,
            "schema_version": SCHEMA_VERSION,
            "generator": normalized_lineage["bundle_generator"],
            "input_snapshot_sha256": input_snapshot_sha256,
            "query_fingerprint_sha256": query_fingerprint_sha256,
            "parameters": _parameters(reproducibility_parameters or {}, incident_id),
            "output_sha256": "0" * 64,
            "generated_at": generated,
            "business_timezone": normalized_snapshot["business_timezone"],
            "reference_validation_status": "passed",
            "source_hash_verification_status": source_hash_verification_status,
        },
        "limitations": sorted(limitation_items, key=_canonical_json),
        "conclusion": {
            "classification": "observation_only",
            "observation_summary": "该证据包仅汇总可定位历史事实、路由观测、指标覆盖与已分类缺失。",
            "causal_conclusion": None,
        },
    }
    validate_reference_closure(bundle)
    bundle["reproducibility"]["output_sha256"] = hashlib.sha256(
        canonical_evidence_bundle_bytes(bundle)
    ).hexdigest()
    # 哈希写入后再校验一次引用闭合，防止后续维护引入分支差异。
    validate_reference_closure(bundle)
    canonical_evidence_bundle_bytes(bundle)
    return bundle


__all__ = (
    "EvidenceBundleError",
    "build_evidence_bundle_v2",
    "canonical_evidence_bundle_bytes",
    "evidence_id_v2",
    "validate_reference_closure",
)
