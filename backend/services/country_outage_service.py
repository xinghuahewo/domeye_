"""通用国家中断观测查询服务。"""

from __future__ import annotations

import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from services.country_outage_registry import (
    CountryOutagePublicationNotFound,
    country_outage_publication,
    find_country_outage_by_incident,
    find_country_outage_by_reference,
    package_directory,
)
from services.event_story_service import (
    EventStoryUnavailable,
    get_country_outage_observation,
)
from services.events_service import get_event_detail_data


LEGACY_INCIDENT_PREFIX = "legacy_country_outage_v1."
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
REFERENCE_PATTERN = re.compile(
    r"^country_outage/(?P<start>[^/]+)/(?P<country>[A-Za-z]{2})/"
    r"(?P<event_id>[1-9][0-9]*)/(?P<source>[A-Za-z0-9_-]+)$"
)
CAPABILITY_CONTRACT_VERSION = "country_outage_capabilities_v1"


class CountryOutageNotFound(LookupError):
    """事件事实不存在，或事件没有已发布的观测。"""


class CountryOutageInvalidReference(ValueError):
    """引用不是合法的国家中断事件引用。"""


class CountryOutageSourceUnavailable(RuntimeError):
    """旧事实数据源暂时不可读取。"""


def canonical_country_outage_reference(value: str) -> str:
    match = REFERENCE_PATTERN.fullmatch(value.strip())
    if match is None:
        raise CountryOutageInvalidReference(value)
    try:
        start = datetime.strptime(
            match.group("start").replace("+", " "),
            "%Y-%m-%d %H:%M:%S",
        ).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError as error:
        raise CountryOutageInvalidReference(value) from error
    return "/".join(
        (
            "country_outage",
            start,
            match.group("country").upper(),
            match.group("event_id"),
            match.group("source"),
        )
    )


def _legacy_incident_id(legacy_reference: str) -> str:
    encoded = base64.urlsafe_b64encode(
        legacy_reference.encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"{LEGACY_INCIDENT_PREFIX}{encoded}"


def _reference_from_legacy_incident(incident_id: str) -> str | None:
    if not incident_id.startswith(LEGACY_INCIDENT_PREFIX):
        return None
    encoded = incident_id[len(LEGACY_INCIDENT_PREFIX) :]
    if not encoded:
        return None
    try:
        padding = "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
        canonical = canonical_country_outage_reference(decoded)
    except (ValueError, UnicodeDecodeError):
        return None
    return canonical


def _reference_parts(
    legacy_reference: str,
) -> tuple[str, str, int, str]:
    canonical = canonical_country_outage_reference(legacy_reference)
    _, start_time, country_code, event_id, source = canonical.split("/")
    return start_time, country_code, int(event_id), source


def _legacy_detail(legacy_reference: str) -> dict[str, Any]:
    start_time, country_code, event_id, source = _reference_parts(
        legacy_reference
    )
    try:
        detail = get_event_detail_data(
            event_type="country_outage",
            start_time=start_time,
            problem=country_code,
            event_id=event_id,
            source=source,
            query_params={},
        )
    except Exception as error:
        raise CountryOutageSourceUnavailable(
            "国家中断旧事实数据源暂时不可用"
        ) from error
    if not isinstance(detail, Mapping) or not detail:
        raise CountryOutageNotFound(legacy_reference)
    return dict(detail)


def _iso_times(
    local_value: Any,
) -> tuple[str | None, str | None]:
    if not isinstance(local_value, str) or not local_value:
        return None, None
    try:
        parsed = datetime.strptime(
            local_value[:19], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=BUSINESS_TIMEZONE)
    except ValueError:
        return None, None
    return (
        parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        parsed.isoformat(timespec="seconds"),
    )


def _legacy_capabilities() -> dict[str, dict[str, str]]:
    unavailable = {
        "state": "unavailable",
        "reason": "旧事实表没有该增强观测数据",
    }
    return {
        "legacy_summary": {"state": "available"},
        "fixed_cohort": dict(unavailable),
        "country_resources": dict(unavailable),
        "update_activity": dict(unavailable),
        "address_families": dict(unavailable),
        "asn_matrix": dict(unavailable),
        "audit": {
            "state": "available",
            "reason": "仅提供旧事实来源与口径审计",
        },
        "normal_band": {
            "state": "not_applicable",
            "reason": "旧事实摘要不具有可计算正常带的时间序列",
        },
    }


def _processing_status(
    configured: Any,
    *,
    is_final: bool,
    data_through: str | None,
) -> dict[str, Any]:
    if isinstance(configured, Mapping):
        return {
            "state": str(configured.get("state") or "idle"),
            "updated_at": configured.get("updated_at"),
            "attempted_through": configured.get("attempted_through"),
            "reason": configured.get("reason"),
            "last_complete_data_through": (
                configured.get("last_complete_data_through")
                or data_through
            ),
        }
    return {
        "state": "final" if is_final else "idle",
        "updated_at": None,
        "attempted_through": None,
        "reason": None,
        "last_complete_data_through": data_through,
    }


def _legacy_observation(
    legacy_reference: str,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    canonical = canonical_country_outage_reference(legacy_reference)
    start_time, country_code, event_id, source = _reference_parts(canonical)
    values = dict(detail or _legacy_detail(canonical))
    start_utc, start_local = _iso_times(
        values.get("start_time") or start_time
    )
    end_utc, end_local = _iso_times(values.get("end_time"))
    country_name = str(
        values.get("outage_country")
        or values.get("attacked_country")
        or country_code
    )
    incident_id = _legacy_incident_id(canonical)
    publication_id = (
        "publication_legacy_v1_"
        + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    )
    is_final = end_utc is not None
    return {
        "schema_version": "country_outage_observation_v2",
        "revision": 1,
        "publication_state": "published",
        "observation_state": "legacy_summary",
        "data_mode": "legacy",
        "data_through": None,
        "updated_at": None,
        "is_final": is_final,
        "processing_status": _processing_status(
            None,
            is_final=is_final,
            data_through=None,
        ),
        "missing_slot_count": 0,
        "publication_id": publication_id,
        "event_identity": {
            "incident_id": incident_id,
            "legacy_reference": canonical,
            "legacy_record_time_local": start_local,
            "event_type": "country_outage",
            "country_code": country_code,
            "country_name": country_name,
            "display_name": f"{country_name} BGP 路由观测",
        },
        "observation_scope": {
            "collector_id": source,
            "collector_ids": [source],
            "collector_count": 1,
            "vantage_point_count": None,
            "vantage_point_semantics": "旧事实记录未保留观测点人口",
            "window_start_utc": start_utc,
            "window_start_local": start_local,
            "window_end_utc": end_utc,
            "window_end_local": end_local,
            "timezone": "Asia/Shanghai",
            "interval_seconds": None,
            "observation_count": 0,
            "expected_observation_count": None,
            "quality_status": "legacy_summary",
            "last_observation_at_utc": None,
            "last_observation_at_local": None,
            "replay_completed_at_utc": None,
            "replay_completed_at_local": None,
            "left_boundary": "旧事实摘要没有增强观测窗口",
            "right_boundary": "旧事实摘要没有增强观测窗口",
        },
        "cohort": None,
        "normal_band": {
            "state": "not_applicable",
            "label": "正常带不适用",
            "reason": "旧事实摘要没有同口径时间序列。",
        },
        "rule_marker": None,
        "capability_contract_version": CAPABILITY_CONTRACT_VERSION,
        "capabilities": _legacy_capabilities(),
        "legacy_summary": {
            "event_id": event_id,
            "source": source,
            "start_time_local": start_local,
            "end_time_local": end_local,
            "duration": values.get("duration") or None,
            "total_asn_count": values.get("total_as_num"),
            "affected_asn_count": values.get("outage_as_num"),
            "affected_asns": values.get("outage_ases") or [],
            "risk_level": values.get("event_level") or None,
            "description": values.get("event_descr") or None,
            "summary": values.get("event_info") or None,
        },
        "metric_definitions": [],
        "series": [],
        "metric_extrema": {},
        "resource_series": [],
        "resource_metric_extrema": {},
        "country_update_series": [],
        "country_update_metric_extrema": {},
        "annotations": [],
        "asn_state": {
            "state_codes": {
                "-1": "unknown",
                "0": "fully_visible",
                "1": "partially_visible",
                "2": "fully_invisible",
            },
            "observed_at_utc": [],
            "observed_at_local": [],
            "timelines": [],
        },
        "limitations": [
            "当前事件只有旧数据库事实摘要，没有固定 cohort 状态。",
            "旧记录未保留逐观测点、Prefix×VP、双栈状态或原始 BGP 报文证据。",
            "未知与缺失不得解释为零、未发生、恢复或正常。",
        ],
        "audit": {
            "publication_id": publication_id,
            "run_id": None,
            "artifact_set_id": None,
            "engine_version": "legacy-country-outage-adapter/1",
            "algorithm_version": None,
            "mapping_version": None,
            "quality_status": "legacy_summary",
            "source_system": "legacy_country_outage_fact_table",
            "source_table": f"country_outage_{start_time[:4]}{start_time[5:7]}",
            "source_reference": canonical,
            "evidence_level": "legacy_summary",
            "consumed_deliverable_hashes_verified": False,
            "verified_hashes": {},
            "route_state_file": {
                "filename": None,
                "recorded_sha256": None,
                "row_count": None,
                "request_path_scanned": False,
            },
            "input_summary": {
                "rib_count": None,
                "catch_up_update_count": None,
                "formal_update_count": None,
                "input_compressed_bytes": None,
                "rib_physical_records": None,
                "rib_entries": None,
                "update_physical_records": None,
                "update_route_events": None,
            },
            "revision_history": [],
        },
    }


def _registration_by_reference(legacy_reference: str) -> dict[str, Any]:
    canonical = canonical_country_outage_reference(legacy_reference)
    registration = find_country_outage_by_reference(canonical)
    if registration is None:
        raise CountryOutageNotFound(canonical)
    return registration


def _registration_by_incident(incident_id: str) -> dict[str, Any]:
    registration = find_country_outage_by_incident(incident_id)
    if registration is None:
        raise CountryOutageNotFound(incident_id)
    return registration


def _observation(
    registration: Mapping[str, Any],
    *,
    legacy_detail: Mapping[str, Any] | None = None,
    resource_series: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if not registration.get("package_uri"):
        observation = _legacy_observation(
            str(registration["legacy_reference"]),
            legacy_detail,
        )
        observation.update(
            {
                "revision": int(registration.get("revision") or 1),
                "publication_state": str(
                    registration.get("publication_state") or "published"
                ),
                "observation_state": str(
                    registration.get("observation_state") or "legacy_summary"
                ),
                "data_mode": str(registration.get("data_mode") or "legacy"),
                "data_through": registration.get("data_through"),
                "updated_at": registration.get("updated_at"),
                "is_final": bool(
                    registration.get("is_final", observation["is_final"])
                ),
            }
        )
    else:
        observation = get_country_outage_observation(
            registration=registration,
            legacy_detail=legacy_detail,
            package_directory=package_directory(registration),
            resource_series=resource_series,
        )
    observation["publication_id"] = registration["publication_id"]
    observation["processing_status"] = _processing_status(
        registration.get("processing_status"),
        is_final=bool(observation["is_final"]),
        data_through=observation.get("data_through"),
    )
    missing_slots = registration.get("missing_slots")
    observation["missing_slot_count"] = (
        len(missing_slots) if isinstance(missing_slots, list) else 0
    )
    audit = observation.get("audit")
    if isinstance(audit, dict):
        audit["publication_id"] = registration["publication_id"]
        audit["revision_history"] = registration.get(
            "_publication_history", []
        )
        audit["supersedes_publication_id"] = registration.get(
            "supersedes_publication_id"
        )
        audit["correction_reason"] = registration.get("correction_reason")
        audit["processing_status"] = observation["processing_status"]
        audit["missing_slot_count"] = observation["missing_slot_count"]
    return observation


def resolve_country_outage(legacy_reference: str) -> dict[str, Any]:
    canonical = canonical_country_outage_reference(legacy_reference)
    registration = find_country_outage_by_reference(canonical)
    if registration is None:
        detail = _legacy_detail(canonical)
        observation = _legacy_observation(canonical, detail)
        return {
            "schema_version": "country_outage_resolution_v2",
            "incident_id": observation["event_identity"]["incident_id"],
            "publication_id": observation["publication_id"],
            "legacy_reference": canonical,
            "event_type": "country_outage",
            "observation_state": "legacy_summary",
            "latest_revision": 1,
            "data_mode": "legacy",
            "data_through": None,
            "is_final": observation["is_final"],
            "processing_status": observation["processing_status"],
            "missing_slot_count": observation["missing_slot_count"],
            "capability_contract_version": CAPABILITY_CONTRACT_VERSION,
            "capabilities": observation["capabilities"],
        }
    effective = country_outage_publication(registration)
    return {
        "schema_version": "country_outage_resolution_v2",
        "incident_id": effective["incident_id"],
        "publication_id": effective["publication_id"],
        "legacy_reference": canonical,
        "event_type": "country_outage",
        "observation_state": str(
            effective.get("observation_state") or "state_complete"
        ),
        "latest_revision": int(effective.get("revision") or 1),
        "data_mode": str(effective.get("data_mode") or "replay"),
        "data_through": effective.get("data_through"),
        "is_final": bool(effective.get("is_final", True)),
        "processing_status": _processing_status(
            effective.get("processing_status"),
            is_final=bool(effective.get("is_final", True)),
            data_through=effective.get("data_through"),
        ),
        "missing_slot_count": len(effective.get("missing_slots") or []),
        "capability_contract_version": CAPABILITY_CONTRACT_VERSION,
        "capabilities": effective.get("capabilities") or {},
    }


def _observation_by_incident(
    incident_id: str,
    publication_id: str | None = None,
) -> dict[str, Any]:
    registration = find_country_outage_by_incident(incident_id)
    if registration is not None:
        return _observation(
            country_outage_publication(registration, publication_id)
        )
    legacy_reference = _reference_from_legacy_incident(incident_id)
    if legacy_reference is None:
        raise CountryOutageNotFound(incident_id)
    observation = _legacy_observation(legacy_reference)
    if publication_id not in (None, observation["publication_id"]):
        raise CountryOutagePublicationNotFound(publication_id)
    return observation


def _common_metadata(observation: Mapping[str, Any]) -> dict[str, Any]:
    identity = observation["event_identity"]
    scope = observation["observation_scope"]
    cohort = observation.get("cohort")
    return {
        "revision": observation["revision"],
        "publication_id": observation["publication_id"],
        "publication_state": observation["publication_state"],
        "observation_state": observation["observation_state"],
        "data_mode": observation["data_mode"],
        "data_through": observation["data_through"],
        "updated_at": observation["updated_at"],
        "is_final": observation["is_final"],
        "processing_status": observation["processing_status"],
        "missing_slot_count": observation["missing_slot_count"],
        "incident_id": identity["incident_id"],
        "cohort_id": (
            cohort.get("cohort_id") if isinstance(cohort, Mapping) else None
        ),
        "window_start_utc": scope.get("window_start_utc"),
        "window_end_utc": scope.get("window_end_utc"),
        "capability_contract_version": observation.get(
            "capability_contract_version",
            CAPABILITY_CONTRACT_VERSION,
        ),
    }


def get_country_outage_query_context(
    incident_id: str,
    *,
    publication_id: str | None = None,
) -> dict[str, Any]:
    registration = find_country_outage_by_incident(incident_id)
    effective_registration = (
        country_outage_publication(registration, publication_id)
        if registration is not None
        else None
    )
    observation = (
        _observation(effective_registration)
        if effective_registration is not None
        else _observation_by_incident(incident_id, publication_id)
    )
    resource_source = (
        effective_registration.get("resource_source")
        if effective_registration is not None
        else None
    )
    resource_source = (
        resource_source if isinstance(resource_source, Mapping) else {}
    )
    return {
        "registration": effective_registration,
        "country_name": str(
            resource_source.get("country_name")
            or observation["event_identity"]["country_name"]
        ),
        "window_start_local": observation["observation_scope"].get(
            "window_start_local"
        ),
        "window_end_local": observation["observation_scope"].get(
            "window_end_local"
        ),
        "resource_state": resource_source.get("state", "unavailable"),
    }


def get_country_outage_overview(
    incident_id: str,
    *,
    publication_id: str | None = None,
) -> dict[str, Any]:
    observation = _observation_by_incident(incident_id, publication_id)
    return {
        "schema_version": "country_outage_overview_v2",
        **_common_metadata(observation),
        "event_identity": observation["event_identity"],
        "observation_scope": observation["observation_scope"],
        "cohort": observation["cohort"],
        "normal_band": observation["normal_band"],
        "rule_marker": observation["rule_marker"],
        "capabilities": observation["capabilities"],
        "legacy_summary": observation.get("legacy_summary"),
        "limitations": observation["limitations"],
    }


def get_country_outage_series(
    incident_id: str,
    *,
    publication_id: str | None = None,
    resource_series: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    registration = find_country_outage_by_incident(incident_id)
    observation = (
        _observation(
            country_outage_publication(registration, publication_id),
            resource_series=resource_series,
        )
        if registration is not None
        else _observation_by_incident(incident_id, publication_id)
    )
    return {
        "schema_version": "country_outage_series_v2",
        **_common_metadata(observation),
        "interval_seconds": observation["observation_scope"].get(
            "interval_seconds"
        ),
        "metric_definitions": observation["metric_definitions"],
        "series": observation["series"],
        "metric_extrema": observation["metric_extrema"],
        "resource_series": observation["resource_series"],
        "resource_metric_extrema": observation["resource_metric_extrema"],
        "country_update_series": observation["country_update_series"],
        "country_update_metric_extrema": observation[
            "country_update_metric_extrema"
        ],
        "annotations": observation["annotations"],
    }


def _filter_asn_rows(
    rows: list[dict[str, Any]],
    *,
    query: str,
    address_family: str,
    state: str,
) -> list[dict[str, Any]]:
    normalized_query = query.strip().lower().removeprefix("as")
    result: list[dict[str, Any]] = []
    for row in rows:
        if normalized_query and normalized_query not in str(row["asn"]):
            continue
        families = row.get("address_families") or []
        if address_family == "ipv4" and 4 not in families:
            continue
        if address_family == "ipv6" and 6 not in families:
            continue
        if address_family == "dual" and not ({4, 6} <= set(families)):
            continue
        counts = row.get("state_slot_counts") or {}
        if state in {"partial", "partially_visible"} and not counts.get(
            "partially_visible"
        ):
            continue
        if state in {"invisible", "fully_invisible"} and not counts.get(
            "fully_invisible"
        ):
            continue
        if state == "unknown" and not counts.get("unknown"):
            continue
        result.append(row)
    return result


def _sort_asn_rows(
    rows: list[dict[str, Any]],
    sort: str,
) -> list[dict[str, Any]]:
    if sort in {"partial", "longest_partially_visible_desc"}:
        key = lambda row: (
            -row["longest_partially_visible_slots"],
            -row["state_slot_counts"]["partially_visible"],
            int(row["asn"]),
        )
    elif sort in {"prefix_vp", "baseline_prefix_vp_count_desc"}:
        key = lambda row: (-row["baseline_prefix_vp_count"], int(row["asn"]))
    elif sort in {"asn", "asn_asc"}:
        key = lambda row: (int(row["asn"]),)
    else:
        key = lambda row: (
            -row["longest_fully_invisible_slots"],
            -row["state_slot_counts"]["fully_invisible"],
            int(row["asn"]),
        )
    return sorted(rows, key=key)


def _duration_histogram(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    return {
        label: {
            str(slots): count
            for slots, count in sorted(
                Counter(int(row[key]) for row in rows).items()
            )
        }
        for label, key in (
            ("fully_visible", "longest_fully_visible_slots"),
            ("partially_visible", "longest_partially_visible_slots"),
            ("fully_invisible", "longest_fully_invisible_slots"),
        )
    }


def get_country_outage_asns(
    incident_id: str,
    *,
    publication_id: str | None = None,
    page: int = 1,
    page_size: int = 60,
    query: str = "",
    address_family: str = "all",
    state: str = "all",
    sort: str = "longest_fully_invisible_desc",
) -> dict[str, Any]:
    observation = _observation_by_incident(incident_id, publication_id)
    page = max(1, page)
    page_size = min(60, max(1, page_size))
    rows = _sort_asn_rows(
        _filter_asn_rows(
            observation["asn_state"]["timelines"],
            query=query,
            address_family=address_family,
            state=state,
        ),
        sort,
    )
    total = len(rows)
    page_count = max(1, (total + page_size - 1) // page_size)
    page = min(page, page_count)
    start = (page - 1) * page_size
    return {
        "schema_version": "country_outage_asn_page_v2",
        **_common_metadata(observation),
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
        "total": total,
        "observed_at_utc": observation["asn_state"]["observed_at_utc"],
        "observed_at_local": observation["asn_state"]["observed_at_local"],
        "state_codes": observation["asn_state"]["state_codes"],
        "duration_histogram": _duration_histogram(rows),
        "items": rows[start : start + page_size],
    }


def get_country_outage_audit(
    incident_id: str,
    *,
    publication_id: str | None = None,
) -> dict[str, Any]:
    observation = _observation_by_incident(incident_id, publication_id)
    return {
        "schema_version": "country_outage_audit_v2",
        **_common_metadata(observation),
        **observation["audit"],
    }


def get_legacy_country_outage_observation(
    legacy_reference: str,
    *,
    legacy_detail: Mapping[str, Any] | None = None,
    resource_series: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    canonical = canonical_country_outage_reference(legacy_reference)
    registration = find_country_outage_by_reference(canonical)
    if registration is None:
        return _legacy_observation(canonical, legacy_detail)
    return _observation(
        country_outage_publication(registration),
        legacy_detail=legacy_detail,
        resource_series=resource_series,
    )


__all__ = [
    "CountryOutageInvalidReference",
    "CountryOutageNotFound",
    "CountryOutagePublicationNotFound",
    "CountryOutageSourceUnavailable",
    "EventStoryUnavailable",
    "canonical_country_outage_reference",
    "get_country_outage_asns",
    "get_country_outage_audit",
    "get_country_outage_overview",
    "get_country_outage_query_context",
    "get_country_outage_series",
    "get_legacy_country_outage_observation",
    "resolve_country_outage",
]
