"""RRC25 伊朗国家中断研究的纯派生闭环装配层。

本模块只消费已经回放完成的 :class:`ReplaySnapshot`、显式槽级元数据和冻结
研究配置。它不读取 MRT、文件、数据库或网络，也不写研究包。职责是把现有
纯函数按以下顺序组合：国家影响、五分钟样本、数值基线、episode/wave、逐
ASN、报告主张对账、质量门、中文报告和内存研究包清单。

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
    RESOLVED,
    UNKNOWN_MAPPING,
    CountryCohort,
    CountryMappingView,
    CountrySnapshotImpact,
    derive_origin_asns,
    derive_country_cohort_and_impacts,
    snapshot_ids_v1,
)
from .episode_as import build_episode_as_records
from .episodes import DetectionResult, EpisodeDetection, detect_country_outage_episodes
from .package_manifest import build_package_manifest, canonical_json as package_canonical_json
from .profile import profile_sha256, validate_research_profile
from .reconciliation import build_reconciliation_result, canonical_json as reconciliation_canonical_json
from .reporting import build_research_report_zh
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
_SAMPLE_ID_RE = re.compile(r"^sample_v1_[0-9a-f]{24}$")
_MAPPING_ID_RE = re.compile(r"^incident_episode_map_v1_[0-9a-f]{24}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
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
_INCIDENT_EPISODE_RELATIONS = frozenset(
    {"temporal_overlap", "legacy_reconciliation", "possible_correspondence"}
)
_PREFIX_ATTRIBUTION_REASON_ZH = {
    "withdraw_origin_unavailable": "withdraw 不携带可核验 origin，未分摊给 cohort ASN",
    "origin_conflict": "AS_SET 等 origin 冲突，未把候选 ASN 当作确定归因",
    "origin_unknown": "announce origin 无法确定，未建立 ASN 级 raw proof",
    "resolved_origin_not_in_cohort": "唯一 origin 不属于该前缀的冻结 cohort ASN",
}


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
    incident_episode_mappings: Tuple[Mapping[str, Any], ...]
    episode_as_records: Tuple[Mapping[str, Any], ...]
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
    """缺少显式证据时只输出 no_correspondence，绝不猜测对应关系。"""

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
                "relation": "no_correspondence",
                "causal": False,
                "evidence_sample_ids": [],
            }
        )
    return tuple(mappings)


def _incident_episode_mapping_records(
    *,
    run_id: str,
    incidents: Sequence[Mapping[str, Any]],
    episodes: Sequence[Mapping[str, Any]],
) -> Tuple[Mapping[str, Any], ...]:
    """反向输出一等的 Incident→0/1/N research episode 映射。"""

    records = []
    seen_incident_ids = set()
    seen_detail_refs = set()
    for index, incident in enumerate(incidents):
        incident_id = incident.get("incident_id")
        detail_ref = incident.get("detail_reference")
        if (
            not isinstance(incident_id, str)
            or not re.fullmatch(r"^inc_v1_[0-9a-f]{24}$", incident_id)
        ):
            raise DerivedAssemblyError(f"incidents[{index}].incident_id 非法")
        if not isinstance(detail_ref, str) or not detail_ref.strip():
            raise DerivedAssemblyError(f"incidents[{index}].detail_reference 非法")
        if incident_id in seen_incident_ids or detail_ref in seen_detail_refs:
            raise DerivedAssemblyError("incidents 不得重复 incident_id 或 detail_reference")
        seen_incident_ids.add(incident_id)
        seen_detail_refs.add(detail_ref)

        links = []
        for episode in episodes:
            matches = [
                mapping
                for mapping in _sequence(
                    episode.get("incident_mappings"), "episode.incident_mappings"
                )
                if isinstance(mapping, Mapping)
                and mapping.get("incident_ref") == detail_ref
                and mapping.get("relation") != "no_correspondence"
            ]
            if len(matches) > 1:
                raise DerivedAssemblyError(
                    f"episode {episode.get('episode_id')} 重复映射同一 Incident"
                )
            if not matches:
                continue
            mapping = matches[0]
            if mapping.get("causal") is not False:
                raise DerivedAssemblyError("Incident→episode 映射必须 causal=false")
            sample_ids = sorted(
                set(
                    _sequence(
                        mapping.get("evidence_sample_ids"),
                        "incident_mapping.evidence_sample_ids",
                    )
                )
            )
            links.append(
                {
                    "episode_id": episode["episode_id"],
                    "relation": mapping.get("relation"),
                    "causal": False,
                    "evidence_sample_ids": sample_ids,
                }
            )
        links.sort(key=lambda item: item["episode_id"])
        count = len(links)
        state = (
            "no_research_episode"
            if count == 0
            else "single_research_episode"
            if count == 1
            else "multiple_research_episodes"
        )
        missing_reason = (
            "当前连续状态证据没有形成可关联的 research episode，未伪造事件边界。"
            if count == 0
            else None
        )
        semantic = {
            "schema_version": "incident-episode-mapping/v1",
            "run_id": run_id,
            "incident_id": incident_id,
            "incident_ref": detail_ref,
            "source_fact_state": incident.get("fact_link_status"),
            "mapping_state": state,
            "causal": False,
            "episode_links": links,
            "missing_reason_zh": missing_reason,
        }
        record = {
            **semantic,
            "mapping_id": "incident_episode_map_v1_"
            + hashlib.sha256(
                _canonical_json(semantic).encode("utf-8")
            ).hexdigest()[:24],
        }
        validate_incident_episode_mapping_record(record, episodes=episodes)
        records.append(record)
    return tuple(sorted(records, key=lambda item: item["incident_id"]))


def validate_incident_episode_mapping_record(
    record: Mapping[str, Any], *, episodes: Sequence[Mapping[str, Any]] = ()
) -> None:
    """验证一等 Incident→0/1/N Episode 映射及 sample→Episode 闭合。"""

    if not isinstance(record, Mapping):
        raise DerivedAssemblyError("incident episode mapping 必须是对象")
    required = {
        "schema_version",
        "mapping_id",
        "run_id",
        "incident_id",
        "incident_ref",
        "source_fact_state",
        "mapping_state",
        "causal",
        "episode_links",
        "missing_reason_zh",
    }
    if set(record) != required:
        raise DerivedAssemblyError(
            f"incident episode mapping 字段必须精确为 {sorted(required)}"
        )
    if record.get("schema_version") != "incident-episode-mapping/v1":
        raise DerivedAssemblyError("incident episode mapping schema_version 非法")
    if not isinstance(record.get("run_id"), str) or _RUN_ID_RE.fullmatch(
        str(record.get("run_id"))
    ) is None:
        raise DerivedAssemblyError("incident episode mapping run_id 非法")
    incident_id = record.get("incident_id")
    if not isinstance(incident_id, str) or re.fullmatch(
        r"^inc_v1_[0-9a-f]{24}$", incident_id
    ) is None:
        raise DerivedAssemblyError("incident episode mapping incident_id 非法")
    if not isinstance(record.get("incident_ref"), str) or not str(
        record.get("incident_ref")
    ).strip():
        raise DerivedAssemblyError("incident episode mapping incident_ref 非法")
    if record.get("source_fact_state") not in {
        "matched",
        "legacy_collision",
        "unresolved",
    }:
        raise DerivedAssemblyError("incident episode mapping source_fact_state 非法")
    if record.get("causal") is not False:
        raise DerivedAssemblyError("Incident→Episode 映射必须 causal=false")
    mapping_id = record.get("mapping_id")
    if not isinstance(mapping_id, str) or _MAPPING_ID_RE.fullmatch(mapping_id) is None:
        raise DerivedAssemblyError("incident episode mapping mapping_id 非法")
    semantic = dict(record)
    semantic.pop("mapping_id")
    expected_id = "incident_episode_map_v1_" + hashlib.sha256(
        _canonical_json(semantic).encode("utf-8")
    ).hexdigest()[:24]
    if mapping_id != expected_id:
        raise DerivedAssemblyError("mapping_id 与规范内容不一致")

    episode_index = {}
    expected_links_by_episode = {}
    for episode in episodes:
        if not isinstance(episode, Mapping):
            raise DerivedAssemblyError("episodes 只能包含对象")
        episode_id = episode.get("episode_id")
        if not isinstance(episode_id, str) or _EPISODE_ID_RE.fullmatch(episode_id) is None:
            raise DerivedAssemblyError("episode_id 非法")
        if episode_id in episode_index:
            raise DerivedAssemblyError("episodes 不得重复 episode_id")
        if episode.get("run_id") != record.get("run_id"):
            raise DerivedAssemblyError(
                f"episode {episode_id}.run_id 与 incident mapping.run_id 不一致"
            )
        episode_index[episode_id] = episode
        incident_mappings = _sequence(
            episode.get("incident_mappings"),
            f"episode {episode_id}.incident_mappings",
        )
        matching = [
            item
            for item in incident_mappings
            if isinstance(item, Mapping)
            and item.get("incident_ref") == record.get("incident_ref")
        ]
        if len(matching) > 1:
            raise DerivedAssemblyError(
                f"episode {episode_id} 重复映射 incident_ref"
            )
        if not matching:
            continue
        episode_mapping = matching[0]
        relation = episode_mapping.get("relation")
        if episode_mapping.get("causal") is not False:
            raise DerivedAssemblyError("Episode 内 Incident 映射必须 causal=false")
        episode_sample_ids = _sequence(
            episode_mapping.get("evidence_sample_ids"),
            f"episode {episode_id}.incident_mapping.evidence_sample_ids",
        )
        if relation == "no_correspondence":
            if episode_sample_ids:
                raise DerivedAssemblyError("no_correspondence 不得引用支持样本")
            continue
        if relation not in _INCIDENT_EPISODE_RELATIONS:
            raise DerivedAssemblyError("Episode 内 incident_mapping.relation 非法")
        if (
            not episode_sample_ids
            or list(episode_sample_ids) != sorted(set(episode_sample_ids))
        ):
            raise DerivedAssemblyError(
                "Episode 内 incident_mapping.evidence_sample_ids 必须非空、去重并排序"
            )
        supporting = set(
            _sequence(
                episode.get("supporting_sample_ids"),
                f"episode {episode_id}.supporting_sample_ids",
            )
        )
        if not set(episode_sample_ids) <= supporting:
            raise DerivedAssemblyError(
                "Episode 内 incident_mapping 的 sample 不属于目标 Episode"
            )
        expected_links_by_episode[episode_id] = {
            "episode_id": episode_id,
            "relation": relation,
            "causal": False,
            "evidence_sample_ids": list(episode_sample_ids),
        }

    raw_links = _sequence(record.get("episode_links"), "episode_links")
    links = []
    for raw_link in raw_links:
        if not isinstance(raw_link, Mapping):
            raise DerivedAssemblyError("episode_links 只能包含对象")
        if set(raw_link) != {
            "episode_id",
            "relation",
            "causal",
            "evidence_sample_ids",
        }:
            raise DerivedAssemblyError("episode_link 字段不闭合")
        episode_id = raw_link.get("episode_id")
        if not isinstance(episode_id, str) or _EPISODE_ID_RE.fullmatch(episode_id) is None:
            raise DerivedAssemblyError("episode_link.episode_id 非法")
        if raw_link.get("relation") not in _INCIDENT_EPISODE_RELATIONS:
            raise DerivedAssemblyError("episode_link.relation 非法")
        if raw_link.get("causal") is not False:
            raise DerivedAssemblyError("episode_link 必须 causal=false")
        sample_ids = _sequence(
            raw_link.get("evidence_sample_ids"), "episode_link.evidence_sample_ids"
        )
        invalid_sample_id = any(
            not isinstance(value, str) or _SAMPLE_ID_RE.fullmatch(value) is None
            for value in sample_ids
        )
        if (
            not sample_ids
            or invalid_sample_id
            or list(sample_ids) != sorted(set(sample_ids))
        ):
            raise DerivedAssemblyError(
                "episode_link.evidence_sample_ids 必须非空、去重排序且格式合法"
            )
        episode = episode_index.get(episode_id)
        if episode is None:
            raise DerivedAssemblyError(f"episode_link 引用不存在的 Episode {episode_id}")
        supporting = set(
            _sequence(
                episode.get("supporting_sample_ids"),
                f"episode {episode_id}.supporting_sample_ids",
            )
        )
        if not set(sample_ids) <= supporting:
            raise DerivedAssemblyError("episode_link 的 sample 引用不属于目标 Episode")
        links.append(raw_link)
    link_ids = [item["episode_id"] for item in links]
    if link_ids != sorted(set(link_ids)):
        raise DerivedAssemblyError("episode_links 必须按 episode_id 去重排序")
    expected_state = (
        "no_research_episode"
        if not links
        else "single_research_episode"
        if len(links) == 1
        else "multiple_research_episodes"
    )
    if record.get("mapping_state") != expected_state:
        raise DerivedAssemblyError("mapping_state 与 Episode 链接基数不一致")
    expected_links = [
        expected_links_by_episode[episode_id]
        for episode_id in sorted(expected_links_by_episode)
    ]
    if [_canonical_json(item) for item in links] != [
        _canonical_json(item) for item in expected_links
    ]:
        raise DerivedAssemblyError(
            "Incident→Episode 映射与 Episode.incident_mappings 反向内容不一致"
        )
    reason = record.get("missing_reason_zh")
    if not links:
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or _HAN_RE.search(reason) is None
        ):
            raise DerivedAssemblyError("零 Episode 映射必须提供中文缺失原因")
    elif reason is not None:
        raise DerivedAssemblyError("已关联 Episode 的映射不得携带缺失原因")


def _automatic_prefix_change_event_ids(
    *,
    episode: EpisodeDetection,
    cohort: CountryCohort,
    route_events_by_id: Mapping[str, ResearchRouteEvent],
) -> Tuple[
    Mapping[Tuple[int, str, str], Tuple[str, ...]],
    Mapping[str, int],
]:
    """从当前 Episode 时间段内的 RouteEvent 建立逐 ASN/前缀变化索引。

    只有 announce 的 origin 已解析为唯一 ASN，且该 ASN 精确属于冻结 cohort
    的同一 AFI/prefix 时才建立硬链接。withdraw、AS_SET/conflict 与 unknown
    只计入未归因诊断，绝不分摊成 ASN 级 raw proof。
    """

    asns_by_prefix: dict[Tuple[str, str], set[int]] = {}
    for reference in cohort.prefix_references:
        asns_by_prefix.setdefault((reference.afi, reference.prefix), set()).add(
            reference.asn
        )
    result: dict[Tuple[int, str, str], set[str]] = {}
    unattributed: dict[str, int] = {}

    def mark(reason: str, count: int = 1) -> None:
        unattributed[reason] = unattributed.get(reason, 0) + count

    for route_id, event in sorted(route_events_by_id.items()):
        if event.action not in {"announce", "withdraw"}:
            continue
        if not (
            episode.onset_at
            <= event.event_time_utc
            < episode.observation_end_at
        ):
            continue
        if event.afi_safi == "ipv4_unicast":
            afi = "ipv4"
        elif event.afi_safi == "ipv6_unicast":
            afi = "ipv6"
        else:
            continue
        candidates = set(asns_by_prefix.get((afi, event.prefix), ()))
        if not candidates:
            continue
        if event.action == "withdraw":
            mark("withdraw_origin_unavailable", len(candidates))
            continue
        resolution = derive_origin_asns(event.as_path or ())
        if resolution.state != RESOLVED:
            mark(
                "origin_conflict" if resolution.state == "conflict" else "origin_unknown",
                len(candidates),
            )
            continue
        if len(resolution.origins) != 1 or resolution.origins[0] not in candidates:
            mark("resolved_origin_not_in_cohort")
            continue
        asn = resolution.origins[0]
        result.setdefault((asn, afi, event.prefix), set()).add(route_id)
    return (
        {
            key: tuple(sorted(values))
            for key, values in sorted(result.items())
        },
        dict(sorted(unattributed.items())),
    )


def _merge_prefix_change_event_ids(
    automatic: Mapping[Tuple[int, str, str], Sequence[str]],
    supplied: Optional[Mapping[Tuple[int, str, str], Sequence[str]]],
    *,
    episode: EpisodeDetection,
    cohort: CountryCohort,
    route_events_by_id: Mapping[str, ResearchRouteEvent],
) -> Mapping[Tuple[int, str, str], Tuple[str, ...]]:
    merged = {key: set(values) for key, values in automatic.items()}
    if supplied is not None:
        for key, values in supplied.items():
            if (
                not isinstance(key, tuple)
                or len(key) != 3
                or isinstance(key[0], bool)
                or not isinstance(key[0], int)
                or key[1] not in {"ipv4", "ipv6"}
                or not isinstance(key[2], str)
            ):
                raise DerivedAssemblyError(
                    "显式 prefix_change_event_ids 的键必须是 (asn, afi, prefix)"
                )
            if isinstance(values, (str, bytes, Mapping)):
                raise DerivedAssemblyError("prefix_change_event_ids 的值必须是 ID 序列")
            route_ids = tuple(values)
            invalid_route_id = any(
                not isinstance(route_id, str)
                or _ROUTE_ID_RE.fullmatch(route_id) is None
                for route_id in route_ids
            )
            if (
                not route_ids
                or invalid_route_id
                or len(route_ids) != len(set(route_ids))
            ):
                raise DerivedAssemblyError(
                    "显式 prefix_change_event_ids 必须非空且不得重复 RouteEvent ID"
                )
            asn, afi, prefix = key
            cohort_match = any(
                reference.asn == asn
                and reference.afi == afi
                and reference.prefix == prefix
                for reference in cohort.prefix_references
            )
            if not cohort_match:
                raise DerivedAssemblyError(
                    "显式 prefix change 目标不属于冻结 cohort 的精确 ASN/AFI/prefix"
                )
            expected_afi_safi = "ipv4_unicast" if afi == "ipv4" else "ipv6_unicast"
            for route_id in route_ids:
                event = route_events_by_id.get(route_id)
                if not isinstance(event, ResearchRouteEvent):
                    raise DerivedAssemblyError(
                        f"显式 prefix change 引用不存在的 RouteEvent {route_id}"
                    )
                resolution = derive_origin_asns(event.as_path or ())
                if (
                    event.action != "announce"
                    or event.afi_safi != expected_afi_safi
                    or event.prefix != prefix
                    or not (
                        episode.onset_at
                        <= event.event_time_utc
                        < episode.observation_end_at
                    )
                    or resolution.state != RESOLVED
                    or resolution.origins != (asn,)
                ):
                    raise DerivedAssemblyError(
                        "显式 prefix change 只有唯一 resolved origin 精确命中的 announce 才可作为 ASN 级 raw proof"
                    )
            merged.setdefault(key, set()).update(route_ids)
    return {key: tuple(sorted(values)) for key, values in sorted(merged.items())}


def _episode_record(
    episode: EpisodeDetection,
    incidents: Sequence[Mapping[str, Any]],
    explicit_mappings: Optional[Mapping[str, Sequence[Mapping[str, Any]]]],
) -> Mapping[str, Any]:
    if explicit_mappings is None:
        if any(
            incident.get("fact_link_status") in {"matched", "legacy_collision"}
            for incident in incidents
        ):
            raise DerivedAssemblyError(
                "检测到 research Episode 与 matched legacy Incident；必须显式提供逐 Episode incident mapping，禁止自动 possible_correspondence"
            )
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
        expected_ref_values = [
            incident.get("detail_reference") for incident in incidents
        ]
        if any(
            not isinstance(reference, str) or not reference.strip()
            for reference in expected_ref_values
        ):
            raise DerivedAssemblyError("incident.detail_reference 不能为空")
        expected_refs = set(expected_ref_values)
        actual_refs = [mapping.get("incident_ref") for mapping in mappings]
        if expected_refs and (
            any(
                not isinstance(reference, str) or not reference.strip()
                for reference in actual_refs
            )
            or set(actual_refs) != expected_refs
            or len(actual_refs) != len(set(actual_refs))
        ):
            raise DerivedAssemblyError(
                "显式逐 Episode incident mapping 必须用关联或 no_correspondence 精确覆盖全部 Incident"
            )
    return episode.to_contract_record(mappings)


def _linked_incident_refs(episode: Mapping[str, Any]) -> Tuple[str, ...]:
    """返回当前 Episode 明确关联的 Incident ref，排除 no_correspondence。"""

    refs = []
    for index, raw in enumerate(
        _sequence(episode.get("incident_mappings"), "episode.incident_mappings")
    ):
        if not isinstance(raw, Mapping):
            raise DerivedAssemblyError(
                f"episode.incident_mappings[{index}] 必须是对象"
            )
        ref = raw.get("incident_ref")
        if not isinstance(ref, str) or not ref.strip():
            raise DerivedAssemblyError("episode incident_ref 非法")
        if raw.get("causal") is not False:
            raise DerivedAssemblyError("Episode 内 Incident 映射必须 causal=false")
        if raw.get("relation") == "no_correspondence":
            continue
        if raw.get("relation") not in _INCIDENT_EPISODE_RELATIONS:
            raise DerivedAssemblyError("episode incident mapping relation 非法")
        refs.append(ref)
    if len(refs) != len(set(refs)):
        raise DerivedAssemblyError("Episode 不得重复关联同一 Incident ref")
    return tuple(sorted(refs))


def _quality_evidence_projection(
    route_events_by_id: Mapping[str, ResearchRouteEvent],
    used_route_ids: Iterable[str],
    verified_raw_refs: Iterable[Mapping[str, Any]] = (),
) -> Tuple[Tuple[Mapping[str, Any], ...], Tuple[Mapping[str, Any], ...], Tuple[Mapping[str, Any], ...]]:
    verified_by_id = {}
    for raw in verified_raw_refs:
        if not isinstance(raw, Mapping):
            raise DerivedAssemblyError("verified_raw_refs 只能包含对象")
        raw_id = raw.get("raw_record_ref_id")
        if not isinstance(raw_id, str):
            raise DerivedAssemblyError("verified raw record 缺少稳定 ID")
        if raw.get("verification_status") != "verified":
            raise DerivedAssemblyError("verified_raw_refs 不得包含未验证记录")
        normalized = dict(raw)
        previous = verified_by_id.get(raw_id)
        if previous is not None and previous != normalized:
            raise DerivedAssemblyError("同一 verified raw record 对应冲突内容")
        verified_by_id[raw_id] = normalized
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
        verified = verified_by_id.get(raw_id)
        if verified is not None and (
            verified.get("artifact_id") != event.artifact_id
            or verified.get("file_sha256") != event.file_sha256
            or verified.get("record_ordinal") != event.record_ordinal
            or verified.get("element_ordinal") != event.element_ordinal
        ):
            raise DerivedAssemblyError("verified raw audit 与 RouteEvent 坐标不一致")
        closure_state = (
            "verified_raw_audit" if verified is not None else "derived_coordinate_only"
        )
        route_rows.append(
            {
                "route_event_id": event.route_event_id,
                "artifact_id": event.artifact_id,
                "file_sha256": event.file_sha256,
                "record_ordinal": event.record_ordinal,
                "element_ordinal": event.element_ordinal,
                "raw_record_ref_id": raw_id,
                "raw_closure_state": closure_state,
            }
        )
        raw_row = (
            {
                **verified,
                "raw_closure_state": "verified_raw_audit",
                "missing_reason_zh": None,
            }
            if verified is not None
            else {
                "raw_record_ref_id": raw_id,
                "artifact_id": event.artifact_id,
                "file_sha256": event.file_sha256,
                "record_offset": None,
                "record_length": None,
                "record_hash": None,
                "record_ordinal": event.record_ordinal,
                "element_ordinal": event.element_ordinal,
                "verification_status": "derived_coordinate_only",
                "raw_closure_state": "unverified",
                "missing_reason_zh": "仅由 RouteEvent 坐标推导，尚未由正式 raw audit 核验 record hash 与字节范围。",
            }
        )
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
        if value.get("verification_status") != "verified":
            # 坐标推导记录可用于暴露缺口，但不能登记成 raw evidence。
            continue
        rows.append(
            _record_ref("raw_record", f"raw_record:{value['raw_record_ref_id']}", value)
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
    prefix_change_unattributed: Mapping[str, int],
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
    if prefix_change_unattributed:
        reserved = ("reference_closure", "prefix_change.asn_unattributed")
        if reserved in used_codes:
            raise DerivedAssemblyError(
                "prefix_change.asn_unattributed 诊断 code 由装配器保留"
            )
        total = sum(prefix_change_unattributed.values())
        details = "；".join(
            "{}={}（{}）".format(
                reason,
                prefix_change_unattributed[reason],
                _PREFIX_ATTRIBUTION_REASON_ZH.get(reason, "未定义归因原因"),
            )
            for reason in sorted(prefix_change_unattributed)
        )
        additions.append(
            DiagnosticViolation(
                gate_id="reference_closure",
                code="prefix_change.asn_unattributed",
                details_zh=(
                    f"共有 {total} 条 Episode 窗口内前缀变化未建立 ASN 级 raw proof：{details}。"
                ),
                severity="fail",
                blocking=True,
            )
        )
    return tuple(supplied) + tuple(additions)


def _baseline_report_record(baseline: NumericBaselineResult) -> Mapping[str, Any]:
    exclusion_boundary = {
        "at_utc": baseline.exclusion_boundary_at_utc,
        "role": baseline.exclusion_boundary_role,
        "confirmation_state": baseline.exclusion_boundary_confirmation_state,
        "causal_claim_allowed": baseline.exclusion_boundary_causal_claim_allowed,
    }
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
            "exclusion_boundary": exclusion_boundary,
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
        "exclusion_boundary": exclusion_boundary,
        "missing_reason": baseline.unresolved_reason,
    }


def _source_temporal_report_records(
    incidents: Sequence[Mapping[str, Any]],
) -> Tuple[Mapping[str, Any], ...]:
    """投影供中文报告呈现的 legacy 双时间语义。"""

    records = []
    for index, incident in enumerate(incidents):
        temporal = incident.get("legacy_temporal_evidence")
        if temporal is None:
            continue
        if not isinstance(temporal, Mapping):
            raise DerivedAssemblyError(
                f"incidents[{index}].legacy_temporal_evidence 必须是对象"
            )
        records.append(
            {
                "incident_id": incident.get("incident_id"),
                **deepcopy(dict(temporal)),
            }
        )
    return tuple(sorted(records, key=lambda item: str(item["incident_id"])))


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
    incident_mappings_by_episode_id: Optional[
        Mapping[str, Sequence[Mapping[str, Any]]]
    ] = None,
    primary_episode_id: Optional[str] = None,
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
    incident_values = tuple(
        dict(item) if isinstance(item, Mapping) else item
        for item in _sequence(incidents, "incidents")
    )
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
    incident_by_ref = {
        incident["detail_reference"]: incident for incident in incident_values
    }
    for episode_record in episode_records:
        unknown_refs = set(_linked_incident_refs(episode_record)) - set(incident_by_ref)
        if unknown_refs:
            raise DerivedAssemblyError(
                "Episode 引用不存在的 Incident：{}".format(sorted(unknown_refs))
            )
    detected_episode_ids = {item.episode_id for item in episode_objects}
    if incident_mappings_by_episode_id is not None and set(incident_mappings_by_episode_id) != detected_episode_ids:
        raise DerivedAssemblyError("incident_mappings_by_episode_id 必须精确覆盖检测结果")
    incident_episode_mappings = _incident_episode_mapping_records(
        run_id=run_id,
        incidents=incident_values,
        episodes=episode_records,
    )
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
    prefix_change_unattributed: dict[str, int] = {}
    for episode in episode_objects:
        supplied_changes = prefix_change_event_ids
        if prefix_change_event_ids_by_episode_id is not None:
            supplied_changes = prefix_change_event_ids_by_episode_id.get(
                episode.episode_id
            )
            if supplied_changes is None:
                raise DerivedAssemblyError(
                    f"逐 Episode 前缀变化索引缺少 {episode.episode_id}"
                )
        automatic_changes, automatic_unattributed = _automatic_prefix_change_event_ids(
            episode=episode,
            cohort=cohort,
            route_events_by_id=route_index,
        )
        for reason, count in automatic_unattributed.items():
            prefix_change_unattributed[reason] = (
                prefix_change_unattributed.get(reason, 0) + count
            )
        changes = _merge_prefix_change_event_ids(
            automatic_changes,
            supplied_changes,
            episode=episode,
            cohort=cohort,
            route_events_by_id=route_index,
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

    used_route_ids = {
        link["route_event_id"]
        for record in episode_as_records
        for link in record.get("evidence_links", ())
        if isinstance(link, Mapping) and isinstance(link.get("route_event_id"), str)
    }
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
                prefix_change_unattributed=prefix_change_unattributed,
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
        source_temporal_evidence=_source_temporal_report_records(
            incident_values
        ),
    )

    content_payloads = {
        "data/baseline.json": _json_content(baseline_report),
        "data/samples.json": _json_content(sample_values),
        "data/episodes.json": _json_content(episode_records),
        "data/waves.json": _json_content(wave_records),
        "data/incident-episode-mappings.json": _json_content(
            incident_episode_mappings
        ),
        "data/episode-as.json": _json_content(episode_as_records),
        "reconciliation.json": _json_content(reconciliation),
        "quality.json": _json_content(quality),
        "report.md": report.encode("utf-8"),
    }
    record_counts = {
        "data/baseline.json": 1,
        "data/samples.json": len(sample_values),
        "data/episodes.json": len(episode_records),
        "data/waves.json": len(wave_records),
        "data/incident-episode-mappings.json": len(
            incident_episode_mappings
        ),
        "data/episode-as.json": len(episode_as_records),
        "reconciliation.json": 1,
        "quality.json": 1,
        "report.md": 1,
    }
    kinds = {
        "data/baseline.json": "baseline",
        "data/samples.json": "samples",
        "data/episodes.json": "episodes",
        "data/waves.json": "waves",
        "data/incident-episode-mappings.json": "incident-episode-mappings",
        "data/episode-as.json": "episode-as",
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
        incident_episode_mappings=incident_episode_mappings,
        episode_as_records=episode_as_records,
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
    "validate_incident_episode_mapping_record",
)
