"""伊朗研究使用的只读旧 Incident 事实快照。

本模块不访问 HTTP 或数据库。调用方先把只读 API 响应冻结为 Git JSON，
这里再严格校验来源定位、旧事实人口和稳定 Incident 身份，并复用 P0
规范化器生成 ``fact_link_status=matched`` 的 Incident。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any, Mapping, Tuple

from ...normalize import NormalizationError, normalize_event
from .file_artifacts import canonical_json


SOURCE_FACT_SNAPSHOT_SCHEMA_VERSION = (
    "iran-country-outage-source-fact-snapshot/v2"
)
_INCIDENT_ID_RE = re.compile(r"^inc_v1_[0-9a-f]{24}$")
_UTC_SECONDS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_UTC8_SECONDS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$"
)
_EMBEDDED_BEIJING_TIME_RE = re.compile(
    r"北京时间 (?P<local>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
)
_BEIJING_OFFSET = timezone(timedelta(hours=8))
_EXPECTED_ENDPOINT = (
    "/api/v1/events/evidence-bundle/country_outage/"
    "2026-02-27%2009%3A12%3A32/IR/1/r"
)
_EXPECTED_RETRIEVED_AT_UTC = "2026-07-22T09:36:22Z"
_EXPECTED_LOCATOR = {
    "problem": "IR",
    "event_id": 1,
    "start_time": "2026-02-27 09:12:32",
}
_EXPECTED_DETAIL_REFERENCE = "country_outage/2026-02-27 09:12:32/IR/1/r"
_EXPECTED_LOCATOR_LOCAL = "2026-02-27T09:12:32+08:00"
_EXPECTED_LOCATOR_UTC = "2026-02-27T01:12:32Z"
_EXPECTED_EMBEDDED_LOCAL = "2026-02-28T22:34:40+08:00"
_EXPECTED_EMBEDDED_UTC = "2026-02-28T14:34:40Z"
_EXPECTED_DIFFERENCE_SECONDS = 134528


class SourceFactSnapshotError(ValueError):
    """冻结 source-fact 快照与伊朗研究身份不闭合。"""


@dataclass(frozen=True)
class LegacyTimeAnchor:
    """旧记录中的一个时间锚点；role 决定它能否被解释为事件时间。"""

    local: str
    utc: str
    role: str


@dataclass(frozen=True)
class LegacyTemporalEvidence:
    """旧 locator 与内嵌文案时间冲突的封闭证据。"""

    locator_record_start: LegacyTimeAnchor
    embedded_message_candidate: LegacyTimeAnchor
    difference_seconds: int
    relationship_state: str
    single_event_time_merge_allowed: bool
    precursor_causality_state: str
    limitations_zh: Tuple[str, ...]


@dataclass(frozen=True)
class FrozenIncidentFact:
    incident: Mapping[str, Any]
    snapshot_sha256: str
    affected_asns: Tuple[str, ...]
    legacy_affected_asn_count: int
    legacy_total_asn_count: int
    temporal_evidence: LegacyTemporalEvidence


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceFactSnapshotError(f"{field} 必须是对象")
    return value


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SourceFactSnapshotError(f"{field} 必须是正整数")
    return value


def _utc_seconds(value: object, field: str) -> datetime:
    if not isinstance(value, str) or _UTC_SECONDS_RE.fullmatch(value) is None:
        raise SourceFactSnapshotError(f"{field} 必须是 UTC 秒精度 Z 时间")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise SourceFactSnapshotError(f"{field} 不是有效时间") from error


def _utc8_seconds(value: object, field: str) -> datetime:
    if not isinstance(value, str) or _UTC8_SECONDS_RE.fullmatch(value) is None:
        raise SourceFactSnapshotError(f"{field} 必须是 +08:00 秒精度时间")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise SourceFactSnapshotError(f"{field} 不是有效时间") from error
    if parsed.utcoffset() != timedelta(hours=8):
        raise SourceFactSnapshotError(f"{field} 必须使用 +08:00")
    return parsed


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_temporal_evidence(
    value: object,
    *,
    locator: Mapping[str, Any],
    event: Mapping[str, Any],
    fact: Mapping[str, Any],
) -> LegacyTemporalEvidence:
    temporal = _mapping(value, "temporal_evidence")
    if set(temporal) != {
        "locator_record_start",
        "embedded_message_candidate",
        "difference_seconds",
        "relationship_state",
        "single_event_time_merge_allowed",
        "precursor_causality_state",
        "limitations_zh",
    }:
        raise SourceFactSnapshotError("temporal_evidence 字段不闭合")

    locator_anchor = _mapping(
        temporal.get("locator_record_start"),
        "temporal_evidence.locator_record_start",
    )
    embedded_anchor = _mapping(
        temporal.get("embedded_message_candidate"),
        "temporal_evidence.embedded_message_candidate",
    )
    anchor_fields = {"local", "utc", "role"}
    if set(locator_anchor) != anchor_fields or set(embedded_anchor) != anchor_fields:
        raise SourceFactSnapshotError("temporal_evidence 时间锚点字段不闭合")

    locator_local = _utc8_seconds(
        locator_anchor.get("local"),
        "temporal_evidence.locator_record_start.local",
    )
    locator_utc = _utc_seconds(
        locator_anchor.get("utc"),
        "temporal_evidence.locator_record_start.utc",
    )
    embedded_local = _utc8_seconds(
        embedded_anchor.get("local"),
        "temporal_evidence.embedded_message_candidate.local",
    )
    embedded_utc = _utc_seconds(
        embedded_anchor.get("utc"),
        "temporal_evidence.embedded_message_candidate.utc",
    )
    if (
        _utc_text(locator_local) != locator_anchor.get("utc")
        or _utc_text(embedded_local) != embedded_anchor.get("utc")
    ):
        raise SourceFactSnapshotError("temporal_evidence 本地时间与 UTC 换算不一致")

    if locator != _EXPECTED_LOCATOR:
        raise SourceFactSnapshotError("当前 Incident locator 不一致")
    if (
        locator_anchor.get("local") != _EXPECTED_LOCATOR_LOCAL
        or locator_anchor.get("utc") != _EXPECTED_LOCATOR_UTC
        or locator_anchor.get("role") != "source_record_identity_only"
        or event.get("event_time_local") != locator_anchor.get("local")
        or event.get("event_time_utc") != locator_anchor.get("utc")
        or fact.get("start_time") != locator.get("start_time")
    ):
        raise SourceFactSnapshotError("locator 时间仅可作为旧记录身份锚点")

    summary = event.get("summary")
    event_info = fact.get("event_info")
    if (
        not isinstance(summary, str)
        or not isinstance(event_info, str)
        or summary != event_info
    ):
        raise SourceFactSnapshotError("event.summary 与 fact_record.event_info 不一致")
    embedded_matches = _EMBEDDED_BEIJING_TIME_RE.findall(summary)
    if len(embedded_matches) != 1:
        raise SourceFactSnapshotError("旧事件文案必须包含唯一北京时间")
    try:
        message_local = datetime.strptime(
            embedded_matches[0], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=_BEIJING_OFFSET)
    except ValueError as error:
        raise SourceFactSnapshotError("旧事件文案内嵌北京时间非法") from error
    if (
        embedded_anchor.get("local") != _EXPECTED_EMBEDDED_LOCAL
        or embedded_anchor.get("utc") != _EXPECTED_EMBEDDED_UTC
        or embedded_anchor.get("role")
        != "candidate_event_time_from_legacy_text"
        or message_local.isoformat(timespec="seconds")
        != embedded_anchor.get("local")
        or _utc_text(message_local) != embedded_anchor.get("utc")
    ):
        raise SourceFactSnapshotError("旧事件文案候选时间与 temporal evidence 不一致")

    difference_seconds = temporal.get("difference_seconds")
    actual_difference = int((embedded_utc - locator_utc).total_seconds())
    if (
        isinstance(difference_seconds, bool)
        or not isinstance(difference_seconds, int)
        or difference_seconds != _EXPECTED_DIFFERENCE_SECONDS
        or difference_seconds != actual_difference
    ):
        raise SourceFactSnapshotError("双时间锚点差值不一致")
    limitations = temporal.get("limitations_zh")
    if (
        not isinstance(limitations, list)
        or limitations
        != [
            "旧行 locator 时间与内嵌文案候选时间不能合并为单一事件时间。",
            "当前旧事实不能判断 locator 事件是否为文案候选事件的前兆，也不能建立因果关系。",
        ]
    ):
        raise SourceFactSnapshotError("双时间限制声明不闭合")
    if (
        temporal.get("relationship_state") != "unresolved_not_causal"
        or temporal.get("single_event_time_merge_allowed") is not False
        or temporal.get("precursor_causality_state") != "undetermined"
    ):
        raise SourceFactSnapshotError("双时间关系必须保持未解决且非因果")

    return LegacyTemporalEvidence(
        locator_record_start=LegacyTimeAnchor(
            local=locator_anchor["local"],
            utc=locator_anchor["utc"],
            role=locator_anchor["role"],
        ),
        embedded_message_candidate=LegacyTimeAnchor(
            local=embedded_anchor["local"],
            utc=embedded_anchor["utc"],
            role=embedded_anchor["role"],
        ),
        difference_seconds=difference_seconds,
        relationship_state=temporal["relationship_state"],
        single_event_time_merge_allowed=False,
        precursor_causality_state=temporal["precursor_causality_state"],
        limitations_zh=tuple(limitations),
    )


def load_frozen_incident_fact(snapshot: Mapping[str, Any]) -> FrozenIncidentFact:
    """校验冻结 API 快照并返回可供研究关联使用的 matched Incident。"""

    root = _mapping(snapshot, "source_fact_snapshot")
    if set(root) != {
        "schema_version",
        "retrieval",
        "expected_incident_id",
        "payload",
        "temporal_evidence",
    }:
        raise SourceFactSnapshotError("source_fact_snapshot 顶层字段不闭合")
    if root.get("schema_version") != SOURCE_FACT_SNAPSHOT_SCHEMA_VERSION:
        raise SourceFactSnapshotError("source_fact_snapshot schema_version 不支持")
    retrieval = _mapping(root.get("retrieval"), "retrieval")
    if set(retrieval) != {
        "method",
        "endpoint",
        "retrieved_at_utc",
        "production_mutation",
        "database_write_operations",
    }:
        raise SourceFactSnapshotError("retrieval 字段不闭合")
    if (
        retrieval.get("method") != "read_only_http_get"
        or retrieval.get("endpoint") != _EXPECTED_ENDPOINT
        or retrieval.get("retrieved_at_utc") != _EXPECTED_RETRIEVED_AT_UTC
        or retrieval.get("production_mutation") is not False
        or retrieval.get("database_write_operations") != 0
    ):
        raise SourceFactSnapshotError("source fact 检索身份或零写入约束不一致")
    _utc_seconds(retrieval.get("retrieved_at_utc"), "retrieval.retrieved_at_utc")

    expected_incident_id = root.get("expected_incident_id")
    if (
        not isinstance(expected_incident_id, str)
        or _INCIDENT_ID_RE.fullmatch(expected_incident_id) is None
    ):
        raise SourceFactSnapshotError("expected_incident_id 非法")
    payload = _mapping(root.get("payload"), "payload")
    source = _mapping(payload.get("source_record"), "payload.source_record")
    locator = _mapping(source.get("record_locator"), "source_record.record_locator")
    fact = _mapping(payload.get("fact_record"), "payload.fact_record")
    event = _mapping(payload.get("event"), "payload.event")
    detail_reference = source.get("detail_reference")
    if (
        payload.get("bundle_version") != "evidence_bundle_v1"
        or payload.get("incident_id") != expected_incident_id
        or payload.get("incident_id_schema") != "incident_id_v1"
        or source.get("source_system") != "Domeye business fact table"
        or source.get("source_table") != "country_outage_202602"
        or source.get("source_code") != "r"
        or locator != _EXPECTED_LOCATOR
        or detail_reference != _EXPECTED_DETAIL_REFERENCE
        or fact.get("start_time") != _EXPECTED_LOCATOR["start_time"]
        or event.get("event_time_local") != _EXPECTED_LOCATOR_LOCAL
        or event.get("event_time_utc") != _EXPECTED_LOCATOR_UTC
        or event.get("source_timezone") != "Asia/Shanghai"
    ):
        raise SourceFactSnapshotError("冻结 API payload 与目标 Incident 定位不一致")
    event_local = _utc8_seconds(event.get("event_time_local"), "event.event_time_local")
    _utc_seconds(event.get("event_time_utc"), "event.event_time_utc")
    if _utc_text(event_local) != event.get("event_time_utc"):
        raise SourceFactSnapshotError("event 本地时间与 UTC 换算不一致")

    temporal_evidence = _load_temporal_evidence(
        root.get("temporal_evidence"),
        locator=locator,
        event=event,
        fact=fact,
    )

    total = _positive_int(fact.get("total_as_num"), "fact_record.total_as_num")
    affected = _positive_int(fact.get("outage_as_num"), "fact_record.outage_as_num")
    outage_ases = fact.get("outage_ases")
    if (
        not isinstance(outage_ases, list)
        or any(
            not isinstance(value, str) or not value.isdigit() or int(value) <= 0
            for value in outage_ases
        )
        or len(outage_ases) != len(set(outage_ases))
        or len(outage_ases) != affected
        or affected > total
    ):
        raise SourceFactSnapshotError("旧事实 affected ASN 人口不闭合")
    if affected != 176 or total != 556:
        raise SourceFactSnapshotError("冻结旧事实不再是待对账的 176/556")

    normalized_fact = {
        "source": "r",
        "country": "IR",
        "outage_id": 1,
        "s_time": "2026-02-27 09:12:32",
        "e_time": fact.get("end_time") or "",
        "duration": fact.get("duration") or "",
        "country_chinese_name": fact.get("outage_country"),
        "total_as_num": total,
        "max_outage_as_num": affected,
        "max_outage_as_ratio": affected / total,
        "outage_level": fact.get("event_level"),
        "outage_level_descr": fact.get("event_descr"),
        "outage_ases": list(outage_ases),
        "event_info": fact.get("event_info"),
    }
    try:
        incident = normalize_event(
            {
                "detail_url": detail_reference,
                "event_type": "country_outage",
                "source": "r",
                "s_time": "2026-02-27 09:12:32",
            },
            normalized_fact,
            {
                "fact_link_status": "matched",
                "source_table": "country_outage_202602",
            },
        )
    except NormalizationError as error:
        raise SourceFactSnapshotError("冻结旧事实无法规范化为 matched Incident") from error
    if (
        incident.get("incident_id") != expected_incident_id
        or incident.get("fact_link_status") != "matched"
        or incident.get("source_primary_key")
        != {"source": "r", "country": "IR", "outage_id": 1}
    ):
        raise SourceFactSnapshotError("规范化 Incident 与冻结身份不闭合")

    # ``normalize_event`` 必须继续使用 legacy locator 时间生成稳定 Incident ID，
    # 但该时间在本研究中只是一条源记录身份锚点。把完整双时间证据附在研究侧
    # Incident 上，供 Evidence Bundle 摘要、研究 sidecar 与报告显式呈现；不得
    # 让下游仅看到一个无角色的 ``event_time_utc`` 后误读为确认 onset。
    incident = {
        **incident,
        "legacy_temporal_evidence": {
            "locator_record_start": {
                "local": temporal_evidence.locator_record_start.local,
                "utc": temporal_evidence.locator_record_start.utc,
                "role": temporal_evidence.locator_record_start.role,
            },
            "embedded_message_candidate": {
                "local": temporal_evidence.embedded_message_candidate.local,
                "utc": temporal_evidence.embedded_message_candidate.utc,
                "role": temporal_evidence.embedded_message_candidate.role,
            },
            "difference_seconds": temporal_evidence.difference_seconds,
            "relationship_state": temporal_evidence.relationship_state,
            "single_event_time_merge_allowed": False,
            "precursor_causality_state": temporal_evidence.precursor_causality_state,
            "limitations_zh": list(temporal_evidence.limitations_zh),
        },
    }

    return FrozenIncidentFact(
        incident=incident,
        snapshot_sha256=hashlib.sha256(
            canonical_json(root).encode("utf-8")
        ).hexdigest(),
        affected_asns=tuple(sorted(outage_ases, key=int)),
        legacy_affected_asn_count=affected,
        legacy_total_asn_count=total,
        temporal_evidence=temporal_evidence,
    )


__all__ = (
    "FrozenIncidentFact",
    "LegacyTemporalEvidence",
    "LegacyTimeAnchor",
    "SOURCE_FACT_SNAPSHOT_SCHEMA_VERSION",
    "SourceFactSnapshotError",
    "load_frozen_incident_fact",
)
