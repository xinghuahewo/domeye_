"""RRC25 伊朗国家中断研究的纯派生闭环装配层。

本模块只消费已经回放完成的 :class:`ReplaySnapshot`、显式槽级元数据和冻结
研究配置。它不读取 MRT、文件、数据库或网络，也不写研究包。职责是把现有
纯函数按以下顺序组合：国家影响、五分钟样本、数值基线、episode/wave、逐
ASN、研究证据、报告主张对账、质量门、中文报告和内存研究包清单。

装配器允许 0/1/N 个 episode，不预设“前兆”或因果关系。调用方可以显式指定
``primary_episode_id`` 作为单事件报告的主对象；未指定且仅有一个 episode 时
自动选中，其他 episode/wave 始终完整保留。若基线未解析，episode 判定会被
停止并以阻断诊断进入质量结果，而不是把未知值补成零。

``bounded_pilot`` 是流程贯通样本，不是完整研究验收。装配器会无条件增加一个
阻断性输入完整性违规，因此其结果始终为 ``incomplete/not_accepted``。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .baseline import BaselineObservation, NumericBaselineResult, derive_numeric_baseline
from .country_impact import (
    CONFLICT_MAPPING,
    MAPPED,
    UNKNOWN_MAPPING,
    CountryCohort,
    CountryMappingView,
    CountrySnapshotImpact,
    derive_country_cohort_and_impacts,
    snapshot_ids_v1,
)
from .episode_as import build_episode_as_records
from .episodes import DetectionResult, EpisodeDetection, detect_country_outage_episodes
from .package_manifest import build_package_manifest, canonical_json as package_canonical_json
from .profile import profile_sha256, validate_research_profile
from .reconciliation import build_reconciliation_result, canonical_json as reconciliation_canonical_json
from .reporting import build_research_report_zh
from .research_evidence import build_research_evidence_package
from .research_quality import (
    DiagnosticFact,
    DiagnosticViolation,
    ResearchQualityInput,
    evaluate_research_quality,
)
from .sample_builder import SampleSourceRef, SlotCount, build_country_outage_sample
from .state_replay import ReplaySnapshot, ResearchRouteEvent, RouteReplayState


_RUN_ID_RE = re.compile(r"^research_run_v1_[0-9a-f]{24}$")
_SNAPSHOT_ID_RE = re.compile(r"^snapshot_v1_[0-9a-f]{24}$")
_EPISODE_ID_RE = re.compile(r"^episode_v1_[0-9a-f]{24}$")
_ROUTE_ID_RE = re.compile(r"^rte_v1_[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OBSERVED_STATES = frozenset(("observed", "observed_zero"))
_OBSERVATION_CLAIM_TYPES = frozenset(
    (
        "report_event_time",
        "ipv4_decline",
        "recovery_state",
        "report_affected_asn_ratio",
        "report_visibility_class_counts",
        "database_affected_asn_ratio",
    )
)
_CAUSAL_MISSING_REASONS = {
    "active_withdrawal_intent": "RRC25单源不能观测主动撤回意图",
    "physical_cut": "缺少物理链路遥测",
    "bgp_session_closed": "单观测点会话状态不能证明全局机制",
    "traffic_impact": "缺少流量遥测",
    "government_intent": "RRC25单源不能观测政府意图",
}
_BOUNDED_PILOT_CODE = "bounded_pilot_not_full_profile"


class DerivedAssemblyError(ValueError):
    """纯派生输入无法安全组成研究闭环。"""


@dataclass(frozen=True)
class SlotResearchMetadata:
    """一个已回放快照的显式独立观测与来源血缘。"""

    snapshot_id: str
    announce_count: SlotCount
    withdraw_count: SlotCount
    vp_expected_count: SlotCount
    vp_observed_count: SlotCount
    source_refs: Tuple[SampleSourceRef, ...]
    route_event_ids: Tuple[str, ...] = ()
    route_link_missing_reason_zh: Optional[str] = None


@dataclass(frozen=True)
class DerivedResearchAssembly:
    """可由调用方原子写出的完整内存研究结果。"""

    run_id: str
    primary_episode_id: Optional[str]
    cohort: CountryCohort
    impacts: Tuple[CountrySnapshotImpact, ...]
    samples: Tuple[Mapping[str, Any], ...]
    baseline: NumericBaselineResult
    detection: Optional[DetectionResult]
    episodes: Tuple[Mapping[str, Any], ...]
    waves: Tuple[Mapping[str, Any], ...]
    episode_as_records: Tuple[Mapping[str, Any], ...]
    evidence_packages: Tuple[Mapping[str, Any], ...]
    reconciliation: Mapping[str, Any]
    quality: Mapping[str, Any]
    report_zh: str
    package_manifest: Mapping[str, Any]
    package_contents: Mapping[str, bytes]


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise DerivedAssemblyError("装配值不能规范化为稳定 JSON") from error


def _sha256_value(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _raw_record_ref_id(file_sha256: str, record: int, element: int) -> str:
    identity = {
        "schema": "raw_record_ref_id_v1",
        "file_sha256": file_sha256,
        "record_ordinal": record,
        "element_ordinal": element,
    }
    return "raw_v1_" + hashlib.sha256(
        _canonical_json(identity).encode("utf-8")
    ).hexdigest()[:32]


def _sequence(value: object, field: str) -> Tuple[Any, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise DerivedAssemblyError(f"{field} 必须是序列")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise DerivedAssemblyError(f"{field} 必须可迭代") from error


def _slot_metadata_index(
    snapshots: Sequence[ReplaySnapshot], values: Iterable[SlotResearchMetadata]
) -> Mapping[str, SlotResearchMetadata]:
    rows = _sequence(values, "slot_metadata")
    if any(not isinstance(item, SlotResearchMetadata) for item in rows):
        raise DerivedAssemblyError("slot_metadata 只能包含 SlotResearchMetadata")
    expected = snapshot_ids_v1(snapshots)
    if len(expected) != len(set(expected)):
        raise DerivedAssemblyError("快照稳定身份重复")
    by_id = {}
    for item in rows:
        assert isinstance(item, SlotResearchMetadata)
        if _SNAPSHOT_ID_RE.fullmatch(item.snapshot_id) is None:
            raise DerivedAssemblyError("slot_metadata.snapshot_id 非法")
        if item.snapshot_id in by_id:
            raise DerivedAssemblyError("slot_metadata.snapshot_id 不得重复")
        if not isinstance(item.source_refs, tuple) or not item.source_refs:
            raise DerivedAssemblyError("每个槽必须至少给出一条 source_ref")
        if any(not isinstance(ref, SampleSourceRef) for ref in item.source_refs):
            raise DerivedAssemblyError("slot_metadata.source_refs 类型非法")
        if not isinstance(item.route_event_ids, tuple):
            raise DerivedAssemblyError("route_event_ids 必须是 tuple")
        if tuple(sorted(set(item.route_event_ids))) != item.route_event_ids:
            raise DerivedAssemblyError("route_event_ids 必须去重排序")
        if any(_ROUTE_ID_RE.fullmatch(route_id) is None for route_id in item.route_event_ids):
            raise DerivedAssemblyError("route_event_ids 含非法稳定 ID")
        if item.route_event_ids and item.route_link_missing_reason_zh is not None:
            raise DerivedAssemblyError("已链接 RouteEvent 的槽不得携带缺失原因")
        if not item.route_event_ids and item.route_link_missing_reason_zh is not None:
            reason = item.route_link_missing_reason_zh
            if not isinstance(reason, str) or not reason.strip():
                raise DerivedAssemblyError("RouteEvent 缺失原因必须是非空中文说明")
        by_id[item.snapshot_id] = item
    if set(by_id) != set(expected):
        missing = sorted(set(expected) - set(by_id))
        extra = sorted(set(by_id) - set(expected))
        raise DerivedAssemblyError(
            f"slot_metadata 必须精确覆盖快照；missing={missing}, extra={extra}"
        )
    return MappingProxyType(by_id)


def _sample_baseline_observation(sample: Mapping[str, Any]) -> BaselineObservation:
    slot = sample.get("slot")
    metrics = sample.get("metrics")
    if not isinstance(slot, Mapping) or not isinstance(metrics, Mapping):
        raise DerivedAssemblyError("样本缺少 slot/metrics")
    visible = metrics.get("visible_ipv4_address_union")
    if not isinstance(visible, Mapping):
        raise DerivedAssemblyError("样本缺少 visible_ipv4_address_union")
    value = visible.get("value")
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise DerivedAssemblyError("基线候选值必须为数值或 null")
    return BaselineObservation(
        sample_id=str(sample.get("sample_id")),
        snapshot_id=str(sample.get("snapshot_id")),
        slot_start_utc=str(slot.get("start")),
        slot_end_exclusive_utc=str(slot.get("end")),
        continuity_state=str(sample.get("continuity_state")),
        value=value,
        value_state=str(visible.get("value_state")),
        missing_reason=visible.get("missing_reason"),
    )


def _automatic_incident_mappings(
    incidents: Sequence[Mapping[str, Any]], episode: EpisodeDetection
) -> Tuple[Mapping[str, Any], ...]:
    first_sample = episode.supporting_sample_ids[0]
    if not incidents:
        return (
            {
                "incident_ref": "未找到可关联的 legacy Incident",
                "relation": "no_correspondence",
                "causal": False,
                "evidence_sample_ids": [],
            },
        )
    mappings = []
    for index, incident in enumerate(incidents):
        if not isinstance(incident, Mapping):
            raise DerivedAssemblyError(f"incidents[{index}] 必须是对象")
        detail_reference = incident.get("detail_reference")
        if not isinstance(detail_reference, str) or not detail_reference.strip():
            raise DerivedAssemblyError("incident.detail_reference 不能为空")
        mappings.append(
            {
                "incident_ref": detail_reference,
                "relation": "possible_correspondence",
                "causal": False,
                "evidence_sample_ids": [first_sample],
            }
        )
    return tuple(mappings)


def _episode_record(
    episode: EpisodeDetection,
    incidents: Sequence[Mapping[str, Any]],
    explicit_mappings: Optional[Mapping[str, Sequence[Mapping[str, Any]]]],
) -> Mapping[str, Any]:
    if explicit_mappings is None:
        mappings = _automatic_incident_mappings(incidents, episode)
    else:
        raw = explicit_mappings.get(episode.episode_id)
        if raw is None:
            raise DerivedAssemblyError(
                f"显式 incident mapping 缺少 episode {episode.episode_id}"
            )
        mappings = _sequence(raw, f"incident_mappings[{episode.episode_id}]")
        if any(not isinstance(item, Mapping) for item in mappings):
            raise DerivedAssemblyError("incident mapping 只能包含对象")
    return episode.to_contract_record(mappings)


def _recovery_candidates(episode: EpisodeDetection) -> Tuple[Mapping[str, Any], ...]:
    return tuple(
        {
            "kind": item.kind,
            "start_at": item.start_at,
            "supporting_sample_ids": list(item.supporting_sample_ids),
            "confirmed": item.confirmed,
            "reason_code": item.reason_code,
        }
        for item in episode.recovery_candidates
    )


def _sample_route_links(
    episode: EpisodeDetection,
    samples_by_id: Mapping[str, Mapping[str, Any]],
    slot_metadata: Mapping[str, SlotResearchMetadata],
    *,
    mapped: bool,
) -> Tuple[Mapping[str, Any], ...]:
    if not mapped:
        return ()
    rows = []
    for sample_id in episode.supporting_sample_ids:
        sample = samples_by_id[sample_id]
        metadata = slot_metadata[str(sample["snapshot_id"])]
        if metadata.route_event_ids:
            rows.append(
                {
                    "sample_id": sample_id,
                    "link_state": "linked",
                    "route_event_ids": list(metadata.route_event_ids),
                    "missing_reason_zh": None,
                }
            )
        else:
            rows.append(
                {
                    "sample_id": sample_id,
                    "link_state": "unknown",
                    "route_event_ids": [],
                    "missing_reason_zh": metadata.route_link_missing_reason_zh
                    or "该五分钟槽没有提供可核验的 RouteEvent 样本血缘。",
                }
            )
    return tuple(rows)


def _quality_evidence_projection(
    route_events_by_id: Mapping[str, ResearchRouteEvent],
    used_route_ids: Iterable[str],
) -> Tuple[Tuple[Mapping[str, Any], ...], Tuple[Mapping[str, Any], ...], Tuple[Mapping[str, Any], ...]]:
    route_rows = []
    raw_rows = {}
    artifact_rows = {}
    for route_id in sorted(set(used_route_ids)):
        event = route_events_by_id.get(route_id)
        if not isinstance(event, ResearchRouteEvent):
            raise DerivedAssemblyError(f"RouteEvent 索引缺少完整记录：{route_id}")
        if event.route_event_id != route_id:
            raise DerivedAssemblyError("RouteEvent 索引键与稳定身份不一致")
        raw_id = _raw_record_ref_id(
            event.file_sha256, event.record_ordinal, event.element_ordinal
        )
        route_rows.append(
            {
                "route_event_id": event.route_event_id,
                "artifact_id": event.artifact_id,
                "file_sha256": event.file_sha256,
                "record_ordinal": event.record_ordinal,
                "element_ordinal": event.element_ordinal,
                "raw_record_ref_id": raw_id,
            }
        )
        raw_row = {
            "raw_record_ref_id": raw_id,
            "artifact_id": event.artifact_id,
            "file_sha256": event.file_sha256,
            "record_ordinal": event.record_ordinal,
            "element_ordinal": event.element_ordinal,
        }
        previous = raw_rows.get(raw_id)
        if previous is not None and previous != raw_row:
            raise DerivedAssemblyError("同一 raw record 稳定 ID 对应冲突坐标")
        raw_rows[raw_id] = raw_row
        artifact_row = {
            "artifact_id": event.artifact_id,
            "file_sha256": event.file_sha256,
        }
        previous_artifact = artifact_rows.get(event.artifact_id)
        if previous_artifact is not None and previous_artifact != artifact_row:
            raise DerivedAssemblyError("同一 artifact_id 对应冲突哈希")
        artifact_rows[event.artifact_id] = artifact_row
    return (
        tuple(route_rows),
        tuple(raw_rows[key] for key in sorted(raw_rows)),
        tuple(artifact_rows[key] for key in sorted(artifact_rows)),
    )


def _mapping_summary(mapping: CountryMappingView) -> Mapping[str, int]:
    return {
        "unique_asn_count": len(mapping.assignments),
        "target_country_asn_count": sum(
            item.mapping_state == MAPPED and item.countries == (mapping.target_country,)
            for item in mapping.assignments
        ),
        "conflict_asn_count": sum(
            item.mapping_state == CONFLICT_MAPPING for item in mapping.assignments
        ),
        "missing_country_count": sum(
            item.mapping_state == UNKNOWN_MAPPING for item in mapping.assignments
        ),
    }


def _record_ref(kind: str, ref: str, value: Any) -> Mapping[str, str]:
    return {"kind": kind, "ref": ref, "sha256": _sha256_value(value)}


def _evidence_registry(
    *,
    samples: Sequence[Mapping[str, Any]],
    episodes: Sequence[Mapping[str, Any]],
    waves: Sequence[Mapping[str, Any]],
    episode_as_records: Sequence[Mapping[str, Any]],
    route_events: Sequence[Mapping[str, Any]],
    raw_refs: Sequence[Mapping[str, Any]],
    evidence_packages: Sequence[Mapping[str, Any]],
) -> Tuple[Mapping[str, str], ...]:
    rows = []
    for value in samples:
        rows.append(_record_ref("sample", f"sample:{value['sample_id']}", value))
    for value in episodes:
        rows.append(_record_ref("episode", f"episode:{value['episode_id']}", value))
    for value in waves:
        rows.append(_record_ref("wave", f"wave:{value['wave_id']}", value))
    for value in episode_as_records:
        rows.append(
            _record_ref("episode_as", f"episode_as:{value['episode_as_id']}", value)
        )
    for value in route_events:
        rows.append(
            _record_ref("route_event", f"route_event:{value['route_event_id']}", value)
        )
    for value in raw_refs:
        rows.append(
            _record_ref("raw_record", f"raw_record:{value['raw_record_ref_id']}", value)
        )
    source_facts = {}
    for package in evidence_packages:
        sidecar = package.get("sidecar")
        if not isinstance(sidecar, Mapping):
            raise DerivedAssemblyError("研究证据包缺少 sidecar")
        for fact in sidecar.get("legacy_source_fact_refs", ()):
            if not isinstance(fact, Mapping):
                raise DerivedAssemblyError("legacy source fact 引用非法")
            fact_id = fact.get("source_fact_id")
            fact_hash = fact.get("source_fact_sha256")
            if not isinstance(fact_id, str) or not isinstance(fact_hash, str):
                raise DerivedAssemblyError("legacy source fact 身份非法")
            previous = source_facts.get(fact_id)
            if previous is not None and previous != fact_hash:
                raise DerivedAssemblyError("同一 legacy source fact 对应冲突哈希")
            source_facts[fact_id] = fact_hash
    for fact_id in sorted(source_facts):
        rows.append(
            {
                "kind": "source_fact",
                "ref": f"source_fact:{fact_id}",
                "sha256": source_facts[fact_id],
            }
        )
    limitation = {
        "scope": "rrc25_only",
        "limitation_zh": "RRC25 路由观测不能单独证明物理机制、真实流量、根因或政府意图。",
    }
    rows.append(_record_ref("limitation", "limitation:rrc25_observation_scope", limitation))
    refs = [row["ref"] for row in rows]
    if len(refs) != len(set(refs)):
        raise DerivedAssemblyError("对账证据登记表引用重复")
    return tuple(sorted(rows, key=lambda item: item["ref"]))


def _sample_at_start(
    samples: Sequence[Mapping[str, Any]], start: str
) -> Mapping[str, Any]:
    matches = [
        sample
        for sample in samples
        if isinstance(sample.get("slot"), Mapping)
        and sample["slot"].get("start") == start
    ]
    if len(matches) != 1:
        raise DerivedAssemblyError(f"时间 {start} 未唯一绑定五分钟样本")
    return matches[0]


def _derived_claim_values(
    *,
    primary_episode_id: Optional[str],
    baseline: NumericBaselineResult,
    episodes: Sequence[Mapping[str, Any]],
    samples: Sequence[Mapping[str, Any]],
    episode_as_records: Sequence[Mapping[str, Any]],
) -> Mapping[str, Mapping[str, Any]]:
    if primary_episode_id is None or not baseline.resolved:
        return {}
    episode = next(
        (item for item in episodes if item.get("episode_id") == primary_episode_id), None
    )
    if episode is None:
        raise DerivedAssemblyError("primary_episode_id 未绑定已检测 episode")
    assert baseline.median is not None
    trough_sample = _sample_at_start(samples, str(episode["trough_at"]))
    onset_sample = _sample_at_start(samples, str(episode["onset_at"]))
    visible = trough_sample["metrics"]["visible_ipv4_address_union"]
    if visible.get("value_state") not in _OBSERVED_STATES:
        raise DerivedAssemblyError("主 episode 低谷 IPv4 可见量为 unknown，不能复算下降比例")
    trough_value = visible.get("value")
    if isinstance(trough_value, bool) or not isinstance(trough_value, (int, float)):
        raise DerivedAssemblyError("主 episode 低谷 IPv4 可见量非法")
    decline = max(0.0, (baseline.median - float(trough_value)) / baseline.median)
    population = [
        item for item in episode_as_records if item.get("episode_id") == primary_episode_id
    ]
    affected = sum(item.get("cumulative_member") is True for item in population)
    fully_invisible = sum(
        item.get("overall_classification")
        in {
            "dual_stack_fully_invisible",
            "ipv4_only_fully_invisible",
            "ipv6_only_fully_invisible",
        }
        for item in population
    )
    partially_visible = sum(
        item.get("overall_classification") == "partially_visible" for item in population
    )
    total = len(population)
    ratio = None if total == 0 else affected / total
    if ratio is None:
        raise DerivedAssemblyError("主 episode 没有逐 ASN 人口，不能复算 ASN 比例")
    peak_sample = _sample_at_start(samples, str(episode["peak_at"]))
    return {
        "report_event_time": {
            "value": episode["onset_at"],
            "value_state": "recomputed",
            "unit": "event_time",
            "snapshot_id": onset_sample["snapshot_id"],
            "missing_reason": None,
        },
        "ipv4_decline": {
            "value": decline,
            "value_state": "recomputed",
            "unit": "baseline_fraction_decline",
            "snapshot_id": trough_sample["snapshot_id"],
            "missing_reason": None,
        },
        "recovery_state": {
            "value": episode["recovery_state"],
            "value_state": "recomputed",
            "unit": "recovery_state",
            "snapshot_id": peak_sample["snapshot_id"],
            "missing_reason": None,
        },
        "report_affected_asn_ratio": {
            "value": {"affected": affected, "total": total},
            "value_state": "recomputed",
            "unit": "asn_count_ratio_components",
            "snapshot_id": peak_sample["snapshot_id"],
            "missing_reason": None,
        },
        "report_visibility_class_counts": {
            "value": {
                "fully_invisible": fully_invisible,
                "partially_visible": partially_visible,
            },
            "value_state": "recomputed",
            "unit": "asn_count",
            "snapshot_id": peak_sample["snapshot_id"],
            "missing_reason": None,
        },
        "database_affected_asn_ratio": {
            "value": {"affected": affected, "total": total, "ratio": ratio},
            "value_state": "recomputed",
            "unit": "legacy_database_asn_ratio",
            "snapshot_id": peak_sample["snapshot_id"],
            "missing_reason": None,
        },
    }


def _same_claim_value(left: object, right: object) -> bool:
    if isinstance(left, (Mapping, list, tuple)) or isinstance(right, (Mapping, list, tuple)):
        return _canonical_json(left) == _canonical_json(right)
    return left == right


def _bind_assessments(
    *,
    claim_inventory: Mapping[str, Any],
    supplied: object,
    derived_values: Mapping[str, Mapping[str, Any]],
    primary_episode_id: Optional[str],
) -> Mapping[str, Mapping[str, Any]]:
    auto = supplied == "auto"
    if auto:
        rows: dict[str, Mapping[str, Any]] = {}
    elif isinstance(supplied, Mapping):
        rows = {str(key): deepcopy(value) for key, value in supplied.items()}
    elif isinstance(supplied, Sequence) and not isinstance(supplied, (str, bytes)):
        rows = {}
        for value in supplied:
            if not isinstance(value, Mapping) or not isinstance(value.get("claim_key"), str):
                raise DerivedAssemblyError("reconciliation_assessments 序列项非法")
            key = value["claim_key"]
            if key in rows:
                raise DerivedAssemblyError("reconciliation_assessments claim_key 重复")
            rows[key] = deepcopy(value)
    else:
        raise DerivedAssemblyError("reconciliation_assessments 必须是映射或序列")
    claims = claim_inventory.get("claims")
    if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
        raise DerivedAssemblyError("claim_inventory.claims 非法")
    by_key = {}
    for claim in claims:
        if not isinstance(claim, Mapping):
            raise DerivedAssemblyError("claim_inventory.claims 只能包含对象")
        key = claim.get("claim_key")
        claim_type = claim.get("claim_type")
        if not isinstance(key, str) or not isinstance(claim_type, str):
            raise DerivedAssemblyError("claim_inventory claim 身份非法")
        by_key[key] = claim_type
    if auto:
        causal_ratings = {
            "active_withdrawal_intent": "hypothesis_only",
            "physical_cut": "hypothesis_only",
            "bgp_session_closed": "unverifiable",
            "traffic_impact": "unverifiable",
            "government_intent": "hypothesis_only",
        }
        for claim in claims:
            key = str(claim["claim_key"])
            claim_type = str(claim["claim_type"])
            if claim_type in _OBSERVATION_CLAIM_TYPES:
                expected = derived_values.get(claim_type)
                rows[key] = {
                    "comparison_outcome": (
                        "not_computable"
                        if expected is None
                        else "consistent"
                        if _same_claim_value(claim.get("reported_value"), expected["value"])
                        else "different"
                    )
                }
            else:
                rows[key] = {
                    "comparison_outcome": "not_computable",
                    "unknown_rating": causal_ratings.get(
                        claim_type, "unverifiable"
                    ),
                }
    if set(rows) != set(by_key):
        raise DerivedAssemblyError("reconciliation_assessments 必须精确覆盖主张清单")
    for key, claim_type in by_key.items():
        raw = rows[key]
        if not isinstance(raw, Mapping):
            raise DerivedAssemblyError(f"assessment {key} 必须是对象")
        row = dict(raw)
        row["claim_key"] = key
        if claim_type in _OBSERVATION_CLAIM_TYPES:
            expected = derived_values.get(claim_type)
            if expected is None:
                if row.get("comparison_outcome") != "not_computable":
                    raise DerivedAssemblyError(
                        f"未选定可复算主 episode 时 {key} 必须为 not_computable"
                    )
                row.setdefault(
                    "recomputed_value",
                    {
                        "value": None,
                        "value_state": "unknown",
                        "unit": None,
                        "snapshot_id": None,
                        "missing_reason": "未选择可复算的主事件或基线尚未解析",
                    },
                )
            else:
                existing = row.get("recomputed_value")
                if existing is None:
                    row["recomputed_value"] = deepcopy(expected)
                elif not isinstance(existing, Mapping) or not _same_claim_value(
                    existing.get("value"), expected["value"]
                ):
                    raise DerivedAssemblyError(
                        f"assessment {key} 的复算值与派生数据不一致"
                    )
                if row.get("comparison_outcome") == "not_computable":
                    raise DerivedAssemblyError(f"assessment {key} 已可复算，不得标为不可算")
                row.setdefault(
                    "evidence_refs",
                    ["@primary_episode", "@primary_episode_samples"],
                )
                if claim_type in {
                    "report_affected_asn_ratio",
                    "report_visibility_class_counts",
                    "database_affected_asn_ratio",
                }:
                    row["evidence_refs"] = list(row["evidence_refs"]) + [
                        "@primary_episode_as"
                    ]
        else:
            row.setdefault(
                "recomputed_value",
                {
                    "value": None,
                    "value_state": "unknown",
                    "unit": None,
                    "snapshot_id": None,
                    "missing_reason": _CAUSAL_MISSING_REASONS.get(
                        claim_type, "RRC25单源不能复算该因果主张"
                    ),
                },
            )
            row.setdefault("evidence_refs", ["@scope_limitation"])
        row.setdefault("counterevidence_refs", [])
        row.setdefault("limitations_zh", ["本项结论受 RRC25 单观测源证据边界限制。"])
        row.setdefault("rationale_zh", "依据本次纯派生研究数据和显式证据边界完成对账。")
        rows[key] = row
    return rows


def _expand_assessment_refs(
    assessments: Mapping[str, Mapping[str, Any]],
    *,
    registry: Sequence[Mapping[str, str]],
    primary_episode_id: Optional[str],
    episodes: Sequence[Mapping[str, Any]],
    episode_as_records: Sequence[Mapping[str, Any]],
) -> Mapping[str, Mapping[str, Any]]:
    available = {row["ref"] for row in registry}
    selectors = {
        "@all_samples": sorted(ref for ref in available if ref.startswith("sample:")),
        "@all_episodes": sorted(ref for ref in available if ref.startswith("episode:")),
        "@all_waves": sorted(ref for ref in available if ref.startswith("wave:")),
        "@all_episode_as": sorted(ref for ref in available if ref.startswith("episode_as:")),
        "@all_route_events": sorted(ref for ref in available if ref.startswith("route_event:")),
        "@all_raw_records": sorted(ref for ref in available if ref.startswith("raw_record:")),
        "@all_source_facts": sorted(ref for ref in available if ref.startswith("source_fact:")),
        "@scope_limitation": ["limitation:rrc25_observation_scope"],
    }
    if primary_episode_id is not None:
        episode = next(
            item for item in episodes if item.get("episode_id") == primary_episode_id
        )
        selectors["@primary_episode"] = [f"episode:{primary_episode_id}"]
        selectors["@primary_episode_samples"] = [
            f"sample:{sample_id}" for sample_id in episode["supporting_sample_ids"]
        ]
        selectors["@primary_episode_as"] = [
            f"episode_as:{item['episode_as_id']}"
            for item in episode_as_records
            if item.get("episode_id") == primary_episode_id
        ]

    output = {}
    for key, assessment in assessments.items():
        row = deepcopy(dict(assessment))
        for field in ("evidence_refs", "counterevidence_refs"):
            raw_values = row.get(field, ())
            values = _sequence(raw_values, f"{key}.{field}")
            expanded = []
            for value in values:
                if not isinstance(value, str):
                    raise DerivedAssemblyError(f"{key}.{field} 只能包含字符串")
                if value.startswith("@"):
                    if value not in selectors:
                        raise DerivedAssemblyError(f"{key}.{field} 含不可用选择器 {value}")
                    expanded.extend(selectors[value])
                else:
                    expanded.append(value)
            if len(expanded) != len(set(expanded)):
                expanded = sorted(set(expanded))
            unknown = set(expanded) - available
            if unknown:
                raise DerivedAssemblyError(f"{key}.{field} 引用不存在：{sorted(unknown)}")
            row[field] = sorted(expanded)
        output[key] = row
    return output


def _quality_violations(
    *,
    execution_mode: str,
    baseline: NumericBaselineResult,
    supplied: Sequence[DiagnosticViolation],
    facts: Sequence[DiagnosticFact],
) -> Tuple[DiagnosticViolation, ...]:
    used_codes = {(item.gate_id, item.code) for item in tuple(facts) + tuple(supplied)}
    additions = []
    if execution_mode == "bounded_pilot":
        reserved = ("input_completeness", _BOUNDED_PILOT_CODE)
        if reserved in used_codes:
            raise DerivedAssemblyError(f"质量诊断 code {_BOUNDED_PILOT_CODE} 由装配器保留")
        additions.append(
            DiagnosticViolation(
                gate_id="input_completeness",
                code=_BOUNDED_PILOT_CODE,
                details_zh="有界流程贯通样本没有覆盖冻结 Profile 全窗口，因此不得作为完整研究验收。",
                severity="fail",
                blocking=True,
            )
        )
    if not baseline.resolved:
        reserved = ("input_completeness", "numeric_baseline_unresolved")
        if reserved in used_codes:
            raise DerivedAssemblyError("numeric_baseline_unresolved 诊断 code 由装配器保留")
        additions.append(
            DiagnosticViolation(
                gate_id="input_completeness",
                code="numeric_baseline_unresolved",
                details_zh=f"数值基线未解析：{baseline.unresolved_reason}，已停止 Episode 定论。",
                severity="fail",
                blocking=True,
            )
        )
    return tuple(supplied) + tuple(additions)


def _baseline_report_record(baseline: NumericBaselineResult) -> Mapping[str, Any]:
    if baseline.resolved:
        return {
            "baseline_id": baseline.baseline_id,
            "value_state": "observed",
            "median": baseline.median,
            "mad": baseline.mad,
            "normal_band_lower": baseline.normal_band_lower,
            "normal_band_upper": baseline.normal_band_upper,
            "actual_start_utc": baseline.candidate_start_utc,
            "actual_end_exclusive_utc": baseline.actual_end_exclusive_utc,
            "supporting_sample_ids": list(baseline.supporting_sample_ids),
        }
    return {
        "baseline_id": baseline.baseline_id,
        "value_state": "unknown",
        "median": None,
        "mad": None,
        "normal_band_lower": None,
        "normal_band_upper": None,
        "actual_start_utc": baseline.candidate_start_utc,
        "actual_end_exclusive_utc": baseline.actual_end_exclusive_utc,
        "supporting_sample_ids": list(baseline.supporting_sample_ids),
        "missing_reason": baseline.unresolved_reason,
    }


def _json_content(value: Any) -> bytes:
    return (_canonical_json(value) + "\n").encode("utf-8")


def _content_ref(kind: str, path: str, payload: bytes, record_count: int) -> Mapping[str, Any]:
    return {
        "kind": kind,
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "record_count": record_count,
    }


def assemble_derived_research(
    *,
    profile: Mapping[str, Any],
    run_id: str,
    execution_mode: str,
    baseline_snapshot: RouteReplayState | ReplaySnapshot,
    snapshots: Sequence[ReplaySnapshot],
    mapping: CountryMappingView,
    slot_metadata: Iterable[SlotResearchMetadata],
    incidents: Iterable[Mapping[str, Any]],
    claim_inventory: Mapping[str, Any],
    reconciliation_assessments: object,
    quality_facts: Sequence[DiagnosticFact],
    execution: Mapping[str, Any],
    semantic_fingerprints: Sequence[str],
    input_selection: Mapping[str, Any],
    reproduction_commands: Sequence[str],
    route_events_by_id: Optional[Mapping[str, ResearchRouteEvent]] = None,
    prefix_change_event_ids: Optional[
        Mapping[Tuple[int, str, str], Sequence[str]]
    ] = None,
    prefix_change_event_ids_by_episode_id: Optional[
        Mapping[str, Mapping[Tuple[int, str, str], Sequence[str]]]
    ] = None,
    evidence_bundle_parameters: Optional[Mapping[str, Any]] = None,
    evidence_bundle_parameters_by_episode_id: Optional[
        Mapping[str, Mapping[str, Any]]
    ] = None,
    incident_mappings_by_episode_id: Optional[
        Mapping[str, Sequence[Mapping[str, Any]]]
    ] = None,
    primary_episode_id: Optional[str] = None,
    confirmed_onset_at: Optional[str] = None,
    quality_violations: Sequence[DiagnosticViolation] = (),
    limitations_zh: Sequence[str] = (),
    package_bindings: Optional[Mapping[str, str]] = None,
) -> DerivedResearchAssembly:
    """从已回放状态和显式元数据组装完整研究结果，不执行任何 I/O。"""

    normalized_profile = validate_research_profile(profile)
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise DerivedAssemblyError("run_id 非法")
    if execution_mode not in {"full_profile", "bounded_pilot"}:
        raise DerivedAssemblyError("execution_mode 非法")
    snapshot_values = _sequence(snapshots, "snapshots")
    if not snapshot_values or any(not isinstance(item, ReplaySnapshot) for item in snapshot_values):
        raise DerivedAssemblyError("snapshots 必须包含至少一个 ReplaySnapshot")
    observed_times = tuple(item.slot_end_exclusive_utc for item in snapshot_values)
    if tuple(sorted(observed_times)) != observed_times or len(set(observed_times)) != len(observed_times):
        raise DerivedAssemblyError("snapshots 必须按唯一槽结束时间严格递增")
    if not isinstance(mapping, CountryMappingView):
        raise DerivedAssemblyError("mapping 必须是 CountryMappingView")
    if mapping.target_country != normalized_profile["country_code"]:
        raise DerivedAssemblyError("mapping 国家与研究 Profile 不一致")
    if mapping.view not in {"compatible", "revised"}:
        raise DerivedAssemblyError("mapping view 非法")
    collector_id = normalized_profile["collector_id"]
    for snapshot in snapshot_values:
        if any(entry.key.collector_id != collector_id for entry in snapshot.entries):
            raise DerivedAssemblyError("快照含有 Profile 之外的 collector")
    metadata_by_snapshot = _slot_metadata_index(snapshot_values, slot_metadata)
    incident_values = _sequence(incidents, "incidents")
    if any(not isinstance(item, Mapping) for item in incident_values):
        raise DerivedAssemblyError("incidents 只能包含对象")
    route_index = {} if route_events_by_id is None else dict(route_events_by_id)
    if any(
        not isinstance(key, str)
        or not isinstance(event, ResearchRouteEvent)
        or event.route_event_id != key
        for key, event in route_index.items()
    ):
        raise DerivedAssemblyError("route_events_by_id 索引非法")

    cohort, impacts = derive_country_cohort_and_impacts(
        baseline_snapshot, snapshot_values, mapping
    )
    samples = []
    for snapshot, impact in zip(snapshot_values, impacts):
        metadata = metadata_by_snapshot[impact.snapshot_id]
        samples.append(
            build_country_outage_sample(
                impact,
                snapshot,
                run_id=run_id,
                collector_id=collector_id,
                announce_count=metadata.announce_count,
                withdraw_count=metadata.withdraw_count,
                vp_expected_count=metadata.vp_expected_count,
                vp_observed_count=metadata.vp_observed_count,
                source_refs=metadata.source_refs,
            )
        )
    sample_values = tuple(samples)
    samples_by_id = {str(item["sample_id"]): item for item in sample_values}
    impacts_by_sample_id = {
        str(sample["sample_id"]): impact
        for sample, impact in zip(sample_values, impacts)
    }

    baseline = derive_numeric_baseline(
        tuple(_sample_baseline_observation(sample) for sample in sample_values),
        candidate_start_utc=str(normalized_profile["window"]["start_utc"]),
        numeric_policy=normalized_profile["baseline"]["numeric"],
        normal_band_policy=normalized_profile["baseline"]["normal_band"],
        confirmed_onset_at=confirmed_onset_at,
    )
    detection: Optional[DetectionResult]
    episode_objects: Tuple[EpisodeDetection, ...]
    wave_records: Tuple[Mapping[str, Any], ...]
    if baseline.resolved:
        assert baseline.median is not None and baseline.mad is not None
        detection = detect_country_outage_episodes(
            sample_values,
            episode=normalized_profile["algorithms"]["episode"],
            recovery=normalized_profile["algorithms"]["recovery"],
            wave=normalized_profile["algorithms"]["wave"],
            baseline={
                "median": baseline.median,
                "mad": baseline.mad,
                "normal_band": normalized_profile["baseline"]["normal_band"],
            },
        )
        episode_objects = detection.episodes
        wave_records = tuple(item.to_contract_record() for item in detection.waves)
    else:
        detection = None
        episode_objects = ()
        wave_records = ()

    episode_records = tuple(
        _episode_record(
            episode,
            incident_values,
            incident_mappings_by_episode_id,
        )
        for episode in episode_objects
    )
    detected_episode_ids = {item.episode_id for item in episode_objects}
    if incident_mappings_by_episode_id is not None and set(incident_mappings_by_episode_id) != detected_episode_ids:
        raise DerivedAssemblyError("incident_mappings_by_episode_id 必须精确覆盖检测结果")
    if primary_episode_id is not None:
        if not isinstance(primary_episode_id, str) or _EPISODE_ID_RE.fullmatch(primary_episode_id) is None:
            raise DerivedAssemblyError("primary_episode_id 非法")
        if primary_episode_id not in detected_episode_ids:
            raise DerivedAssemblyError("primary_episode_id 不属于检测结果")
        selected_primary = primary_episode_id
    elif len(episode_objects) == 1:
        selected_primary = episode_objects[0].episode_id
    else:
        selected_primary = None

    episode_as_values = []
    for episode in episode_objects:
        changes = prefix_change_event_ids
        if prefix_change_event_ids_by_episode_id is not None:
            changes = prefix_change_event_ids_by_episode_id.get(episode.episode_id)
            if changes is None:
                raise DerivedAssemblyError(
                    f"逐 Episode 前缀变化索引缺少 {episode.episode_id}"
                )
        episode_as_values.extend(
            build_episode_as_records(
                episode,
                samples_by_id,
                impacts_by_sample_id,
                cohort=cohort,
                mapping=mapping,
                route_events_by_id=route_index,
                prefix_change_event_ids=changes,
            )
        )
    if prefix_change_event_ids_by_episode_id is not None and set(
        prefix_change_event_ids_by_episode_id
    ) != detected_episode_ids:
        raise DerivedAssemblyError("prefix_change_event_ids_by_episode_id 必须精确覆盖检测结果")
    episode_as_records = tuple(episode_as_values)

    evidence_packages = []
    for episode, episode_record in zip(episode_objects, episode_records):
        parameters = evidence_bundle_parameters
        if evidence_bundle_parameters_by_episode_id is not None:
            parameters = evidence_bundle_parameters_by_episode_id.get(episode.episode_id)
            if parameters is None:
                raise DerivedAssemblyError(
                    f"逐 Episode Evidence 参数缺少 {episode.episode_id}"
                )
        waves_for_episode = tuple(
            wave for wave in wave_records if wave.get("episode_id") == episode.episode_id
        )
        samples_for_episode = tuple(
            samples_by_id[sample_id] for sample_id in episode.supporting_sample_ids
        )
        evidence_packages.append(
            build_research_evidence_package(
                incidents=incident_values,
                episode=episode_record,
                waves=waves_for_episode,
                samples=samples_for_episode,
                recovery_candidates=_recovery_candidates(episode),
                sample_route_event_links=_sample_route_links(
                    episode,
                    samples_by_id,
                    metadata_by_snapshot,
                    mapped=bool(incident_values),
                ),
                evidence_bundle_parameters=parameters,
                mapping_missing_reason_zh="没有可关联的 legacy Incident，未伪造事件身份。",
                limitations_zh=limitations_zh,
            )
        )
    if evidence_bundle_parameters_by_episode_id is not None and set(
        evidence_bundle_parameters_by_episode_id
    ) != detected_episode_ids:
        raise DerivedAssemblyError("evidence_bundle_parameters_by_episode_id 必须精确覆盖检测结果")
    evidence_package_values = tuple(evidence_packages)

    used_route_ids = {
        link["route_event_id"]
        for record in episode_as_records
        for link in record.get("evidence_links", ())
        if isinstance(link, Mapping) and isinstance(link.get("route_event_id"), str)
    }
    for package in evidence_package_values:
        sidecar = package["sidecar"]
        used_route_ids.update(
            row["route_event_id"] for row in sidecar.get("route_event_refs", ())
        )
    quality_routes, quality_raw, quality_artifacts = _quality_evidence_projection(
        route_index, used_route_ids
    )

    registry = _evidence_registry(
        samples=sample_values,
        episodes=episode_records,
        waves=wave_records,
        episode_as_records=episode_as_records,
        route_events=quality_routes,
        raw_refs=quality_raw,
        evidence_packages=evidence_package_values,
    )
    derived_claim_values = _derived_claim_values(
        primary_episode_id=selected_primary,
        baseline=baseline,
        episodes=episode_records,
        samples=sample_values,
        episode_as_records=episode_as_records,
    )
    bound_assessments = _bind_assessments(
        claim_inventory=claim_inventory,
        supplied=reconciliation_assessments,
        derived_values=derived_claim_values,
        primary_episode_id=selected_primary,
    )
    expanded_assessments = _expand_assessment_refs(
        bound_assessments,
        registry=registry,
        primary_episode_id=selected_primary,
        episodes=episode_records,
        episode_as_records=episode_as_records,
    )
    reconciliation = build_reconciliation_result(
        run_id=run_id,
        claim_inventory=claim_inventory,
        assessments=expanded_assessments,
        evidence_registry=registry,
    )

    facts = tuple(quality_facts)
    if any(not isinstance(item, DiagnosticFact) for item in facts):
        raise DerivedAssemblyError("quality_facts 只能包含 DiagnosticFact")
    supplied_violations = tuple(quality_violations)
    if any(not isinstance(item, DiagnosticViolation) for item in supplied_violations):
        raise DerivedAssemblyError("quality_violations 只能包含 DiagnosticViolation")
    quality_evaluation = evaluate_research_quality(
        ResearchQualityInput(
            facts=facts,
            violations=_quality_violations(
                execution_mode=execution_mode,
                baseline=baseline,
                supplied=supplied_violations,
                facts=facts,
            ),
            samples=sample_values,
            episodes=episode_records,
            waves=wave_records,
            episode_as_records=episode_as_records,
            route_events=quality_routes,
            raw_refs=quality_raw,
            artifacts=quality_artifacts,
            execution=execution,
            semantic_fingerprints=tuple(semantic_fingerprints),
        )
    )
    quality = quality_evaluation.to_dict()
    if execution_mode == "bounded_pilot" and (
        quality["run_state"] != "incomplete"
        or quality["acceptance_state"] != "not_accepted"
    ):
        raise DerivedAssemblyError("bounded_pilot 质量状态没有失败关闭")

    incident_ref = claim_inventory.get("incident_ref")
    if not isinstance(incident_ref, str) or not incident_ref:
        raise DerivedAssemblyError("claim_inventory.incident_ref 不能为空")
    baseline_report = _baseline_report_record(baseline)
    report = build_research_report_zh(
        profile=normalized_profile,
        run={
            "run_id": run_id,
            "incident_ref": incident_ref,
            "execution_mode": execution_mode,
            "execution": dict(execution),
            "acceptance_state": quality["acceptance_state"],
            "primary_episode_id": selected_primary,
        },
        input_selection=input_selection,
        mapping_summary=_mapping_summary(mapping),
        baseline=baseline_report,
        samples=sample_values,
        episodes=episode_records,
        waves=wave_records,
        episode_as_records=episode_as_records,
        reconciliation=reconciliation,
        quality=quality,
        reproduction_commands=reproduction_commands,
    )

    content_payloads = {
        "data/baseline.json": _json_content(baseline_report),
        "data/samples.json": _json_content(sample_values),
        "data/episodes.json": _json_content(episode_records),
        "data/waves.json": _json_content(wave_records),
        "data/episode-as.json": _json_content(episode_as_records),
        "evidence/research-evidence-packages.json": _json_content(
            evidence_package_values
        ),
        "reconciliation.json": _json_content(reconciliation),
        "quality.json": _json_content(quality),
        "report.md": report.encode("utf-8"),
    }
    record_counts = {
        "data/baseline.json": 1,
        "data/samples.json": len(sample_values),
        "data/episodes.json": len(episode_records),
        "data/waves.json": len(wave_records),
        "data/episode-as.json": len(episode_as_records),
        "evidence/research-evidence-packages.json": len(evidence_package_values),
        "reconciliation.json": 1,
        "quality.json": 1,
        "report.md": 1,
    }
    kinds = {
        "data/baseline.json": "baseline",
        "data/samples.json": "samples",
        "data/episodes.json": "episodes",
        "data/waves.json": "waves",
        "data/episode-as.json": "episode-as",
        "evidence/research-evidence-packages.json": "research-evidence",
        "reconciliation.json": "reconciliation",
        "quality.json": "quality",
        "report.md": "report",
    }
    contents = tuple(
        _content_ref(kinds[path], path, payload, record_counts[path])
        for path, payload in sorted(content_payloads.items())
    )
    bindings = {
        "profile": profile_sha256(normalized_profile),
        "mapping": mapping.source_sha256,
        "claim-inventory": _sha256_value(claim_inventory),
    }
    if package_bindings is not None:
        for key, value in package_bindings.items():
            if key in bindings and bindings[key] != value:
                raise DerivedAssemblyError(f"package binding {key} 与派生绑定冲突")
            if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
                raise DerivedAssemblyError(f"package binding {key} 非法")
            bindings[key] = value
    manifest = build_package_manifest(
        run_id=run_id,
        study_id=normalized_profile["study_id"],
        incident_ref=incident_ref,
        execution_mode=execution_mode,
        acceptance_state=str(quality["acceptance_state"]),
        bindings=bindings,
        contents=contents,
    )
    # 二次使用 package canonicalizer，确保装配结果与发布层的稳定 JSON 规则一致。
    package_canonical_json(manifest)
    reconciliation_canonical_json(reconciliation)
    return DerivedResearchAssembly(
        run_id=run_id,
        primary_episode_id=selected_primary,
        cohort=cohort,
        impacts=impacts,
        samples=sample_values,
        baseline=baseline,
        detection=detection,
        episodes=episode_records,
        waves=wave_records,
        episode_as_records=episode_as_records,
        evidence_packages=evidence_package_values,
        reconciliation=reconciliation,
        quality=quality,
        report_zh=report,
        package_manifest=manifest,
        package_contents=MappingProxyType(dict(sorted(content_payloads.items()))),
    )


__all__ = (
    "DerivedAssemblyError",
    "DerivedResearchAssembly",
    "SlotResearchMetadata",
    "assemble_derived_research",
)
