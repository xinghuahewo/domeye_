"""只保留“origin 可解析且国家映射确定”的兼容研究投影。

该模块是一个只读纯函数层，不读取 MRT、文件、数据库或网络，也不改变严格
人口口径。它只为 ``compatible`` 国家映射提供一个显式降级视图：

* 当前路由和变化只有在 origin 为单一 ``as_sequence`` 尾部 ASN，且该 ASN
  的国家映射为 ``mapped`` 时才保留；
* 确定属于目标国家和确定不属于目标国家的观测一并保留，从而不破坏已解析
  MOAS 的上下文；
* AS_SET、confederation、空路径、缺少路径以及映射 unknown/conflict 均被排除
  并留下稳定的 prefix/VP/RouteEvent 引用；
* 输入连续性状态原样传递。输入为 unknown 时，投影计数仍为 ``None``，不会
  把未知伪装成零。

返回对象明确标记为 ``mapped_compatible_projection``，不能被称作严格全人口，
也不能作为回放 checkpoint 继续写入事件。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from typing import Any, Dict, Optional, Sequence, Tuple, Union

from .country_impact import (
    CONFLICT_MAPPING,
    MAPPED,
    RESOLVED,
    UNKNOWN_MAPPING,
    CountryMappingView,
    derive_origin_asns,
)
from .state_replay import (
    CONTINUOUS,
    UNKNOWN_AFTER_GAP,
    ReplaySnapshot,
    RouteLastChange,
    RouteReplayState,
    RouteStateEntry,
)


PROJECTION_KIND = "mapped_compatible_projection"

_LIMITATIONS = (
    "only_resolved_origin_with_mapped_country",
    "not_a_strict_full_population",
    "mapped_non_target_origins_retained_for_moas_context",
    "read_only_projection_not_replay_checkpoint",
)


class MappedCompatibleProjectionError(ValueError):
    """输入不能安全地生成兼容映射投影。"""


@dataclass(frozen=True, order=True)
class MappedRouteReference:
    """一个被投影保留的确定 origin 观测。"""

    source_kind: str
    collector_id: str
    afi_safi: str
    prefix: str
    vp_id: str
    route_event_id: str
    action: str
    origin_asn: int
    country_code: str
    target_country_member: bool


@dataclass(frozen=True, order=True)
class ExcludedRouteReference:
    """一个未被猜测、因而从兼容投影排除的观测。"""

    source_kind: str
    collector_id: str
    afi_safi: str
    prefix: str
    vp_id: str
    route_event_id: str
    action: str
    reason: str
    candidate_origin_asns: Tuple[int, ...]


@dataclass(frozen=True, order=True)
class MappedPrefixContext:
    """当前可见项中，一个前缀的确定映射 MOAS 上下文。"""

    afi_safi: str
    prefix: str
    collector_ids: Tuple[str, ...]
    vp_ids: Tuple[str, ...]
    route_event_ids: Tuple[str, ...]
    origin_asns: Tuple[int, ...]
    country_codes: Tuple[str, ...]
    target_origin_asns: Tuple[int, ...]
    non_target_origin_asns: Tuple[int, ...]
    moas: bool


@dataclass(frozen=True)
class ProjectionAuditSummary:
    """投影人口和被排除不确定性的确定性摘要。"""

    input_entry_count: int
    retained_entry_count: int
    excluded_entry_count: int
    input_change_count: int
    retained_change_count: int
    excluded_change_count: int
    excluded_reason_counts: Tuple[Tuple[str, int], ...]
    excluded_prefixes: Tuple[str, ...]
    excluded_vp_ids: Tuple[str, ...]
    excluded_route_event_ids: Tuple[str, ...]
    retained_refs: Tuple[MappedRouteReference, ...]
    excluded_refs: Tuple[ExcludedRouteReference, ...]
    prefix_contexts: Tuple[MappedPrefixContext, ...]


SnapshotLike = Union[RouteReplayState, ReplaySnapshot]


@dataclass(frozen=True)
class MappedCompatibleProjection:
    """带口径限制、阻断原因与审计摘要的显式兼容投影。"""

    projection_id: str
    projection_kind: str
    source_kind: str
    mapping_view: str
    mapping_source_sha256: str
    target_country: str
    continuity_state: str
    missing_reasons: Tuple[str, ...]
    route_count: Optional[int]
    projected: SnapshotLike
    audit: ProjectionAuditSummary
    limitations: Tuple[str, ...]
    blockers: Tuple[str, ...]


@dataclass(frozen=True)
class _Classification:
    retained: bool
    origin_asn: Optional[int]
    country_code: Optional[str]
    target_country_member: Optional[bool]
    reason: Optional[str]
    candidate_origin_asns: Tuple[int, ...]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _classify_path(
    as_path: Optional[Sequence[Any]], mapping: CountryMappingView
) -> _Classification:
    if as_path is None:
        return _Classification(
            retained=False,
            origin_asn=None,
            country_code=None,
            target_country_member=None,
            reason="missing_as_path",
            candidate_origin_asns=(),
        )
    resolution = derive_origin_asns(as_path)
    if resolution.state != RESOLVED:
        return _Classification(
            retained=False,
            origin_asn=None,
            country_code=None,
            target_country_member=None,
            reason=resolution.reason or "origin_unresolved",
            candidate_origin_asns=resolution.origins,
        )
    if len(resolution.origins) != 1:  # pragma: no cover - derive 已固定该不变量
        raise MappedCompatibleProjectionError("resolved origin 必须只有一个 ASN")
    origin_asn = resolution.origins[0]
    assignment = mapping.assignment_for(origin_asn)
    if assignment.mapping_state != MAPPED:
        reason = {
            UNKNOWN_MAPPING: "country_mapping_unknown",
            CONFLICT_MAPPING: "country_mapping_conflict",
        }.get(assignment.mapping_state, "country_mapping_unresolved")
        return _Classification(
            retained=False,
            origin_asn=None,
            country_code=None,
            target_country_member=None,
            reason=reason,
            candidate_origin_asns=(origin_asn,),
        )
    country_code = assignment.countries[0]
    return _Classification(
        retained=True,
        origin_asn=origin_asn,
        country_code=country_code,
        target_country_member=country_code == mapping.target_country,
        reason=None,
        candidate_origin_asns=(origin_asn,),
    )


def _reference_fields(
    item: Union[RouteStateEntry, RouteLastChange], source_kind: str
) -> Dict[str, Any]:
    if isinstance(item, RouteStateEntry):
        raw_ref = item.last_raw_ref
        action = item.last_action
    elif isinstance(item, RouteLastChange):
        raw_ref = item.raw_ref
        action = item.action
    else:  # pragma: no cover - 调用前已验证
        raise MappedCompatibleProjectionError("投影项类型非法")
    return {
        "source_kind": source_kind,
        "collector_id": item.key.collector_id,
        "afi_safi": item.key.afi_safi,
        "prefix": item.key.prefix,
        "vp_id": item.key.vp_id,
        "route_event_id": raw_ref.route_event_id,
        "action": action,
    }


def _classify_items(
    items: Sequence[Union[RouteStateEntry, RouteLastChange]],
    *,
    source_kind: str,
    mapping: CountryMappingView,
) -> Tuple[
    Tuple[Union[RouteStateEntry, RouteLastChange], ...],
    Tuple[MappedRouteReference, ...],
    Tuple[ExcludedRouteReference, ...],
]:
    retained_items = []
    retained_refs = []
    excluded_refs = []
    for item in items:
        classification = _classify_path(item.as_path, mapping)
        fields = _reference_fields(item, source_kind)
        if classification.retained:
            if (
                classification.origin_asn is None
                or classification.country_code is None
                or classification.target_country_member is None
            ):  # pragma: no cover - _classify_path 已固定该不变量
                raise MappedCompatibleProjectionError("保留项缺少确定映射")
            retained_items.append(item)
            retained_refs.append(
                MappedRouteReference(
                    **fields,
                    origin_asn=classification.origin_asn,
                    country_code=classification.country_code,
                    target_country_member=classification.target_country_member,
                )
            )
        else:
            excluded_refs.append(
                ExcludedRouteReference(
                    **fields,
                    reason=classification.reason or "unresolved",
                    candidate_origin_asns=classification.candidate_origin_asns,
                )
            )
    retained_items.sort(
        key=lambda item: (
            item.key,
            _reference_fields(item, source_kind)["route_event_id"],
        )
    )
    return (
        tuple(retained_items),
        tuple(sorted(retained_refs)),
        tuple(sorted(excluded_refs)),
    )


def _validated_entry_classification(
    entries: Sequence[RouteStateEntry],
    mapping: CountryMappingView,
) -> Tuple[
    Tuple[Union[RouteStateEntry, RouteLastChange], ...],
    Tuple[MappedRouteReference, ...],
    Tuple[ExcludedRouteReference, ...],
]:
    if any(not isinstance(item, RouteStateEntry) for item in entries):
        raise MappedCompatibleProjectionError("source.entries 类型非法")
    entry_keys = tuple(item.key for item in entries)
    if len(entry_keys) != len(set(entry_keys)):
        raise MappedCompatibleProjectionError("source.entries 存在重复状态键")
    return _classify_items(entries, source_kind="entry", mapping=mapping)


def _prefix_contexts(
    retained_entry_refs: Sequence[MappedRouteReference],
) -> Tuple[MappedPrefixContext, ...]:
    grouped: Dict[Tuple[str, str], Dict[str, set[Any]]] = {}
    for ref in retained_entry_refs:
        bucket = grouped.setdefault(
            (ref.afi_safi, ref.prefix),
            {
                "collectors": set(),
                "vps": set(),
                "route_events": set(),
                "origins": set(),
                "countries": set(),
                "target_origins": set(),
                "non_target_origins": set(),
            },
        )
        bucket["collectors"].add(ref.collector_id)
        bucket["vps"].add(ref.vp_id)
        bucket["route_events"].add(ref.route_event_id)
        bucket["origins"].add(ref.origin_asn)
        bucket["countries"].add(ref.country_code)
        destination = "target_origins" if ref.target_country_member else "non_target_origins"
        bucket[destination].add(ref.origin_asn)

    contexts = []
    for (afi_safi, prefix), bucket in sorted(grouped.items()):
        origins = tuple(sorted(bucket["origins"]))
        contexts.append(
            MappedPrefixContext(
                afi_safi=afi_safi,
                prefix=prefix,
                collector_ids=tuple(sorted(bucket["collectors"])),
                vp_ids=tuple(sorted(bucket["vps"])),
                route_event_ids=tuple(sorted(bucket["route_events"])),
                origin_asns=origins,
                country_codes=tuple(sorted(bucket["countries"])),
                target_origin_asns=tuple(sorted(bucket["target_origins"])),
                non_target_origin_asns=tuple(sorted(bucket["non_target_origins"])),
                moas=len(origins) > 1,
            )
        )
    return tuple(contexts)


def _projection_id(
    *,
    source_kind: str,
    mapping: CountryMappingView,
    continuity_state: str,
    missing_reasons: Tuple[str, ...],
    route_count: Optional[int],
    audit: ProjectionAuditSummary,
) -> str:
    identity = {
        "schema": "mapped_compatible_projection_id_v1",
        "projection_kind": PROJECTION_KIND,
        "source_kind": source_kind,
        "mapping": {
            "view": mapping.view,
            "target_country": mapping.target_country,
            "source_sha256": mapping.source_sha256,
            "source_ref": mapping.source_ref,
        },
        "continuity_state": continuity_state,
        "missing_reasons": list(missing_reasons),
        "route_count": route_count,
        "audit": asdict(audit),
    }
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    return "mcp_v1_" + digest[:32]


def _build_mapped_compatible_projection(
    source: SnapshotLike,
    mapping: CountryMappingView,
    *,
    classified_entries: Optional[
        Tuple[
            Tuple[Union[RouteStateEntry, RouteLastChange], ...],
            Tuple[MappedRouteReference, ...],
            Tuple[ExcludedRouteReference, ...],
        ]
    ] = None,
) -> MappedCompatibleProjection:
    if not isinstance(mapping, CountryMappingView):
        raise MappedCompatibleProjectionError("mapping 必须是 CountryMappingView")
    if mapping.view != "compatible":
        raise MappedCompatibleProjectionError("该投影只接受 compatible 映射视图")
    if not isinstance(source, (RouteReplayState, ReplaySnapshot)):
        raise MappedCompatibleProjectionError(
            "source 必须是 RouteReplayState 或 ReplaySnapshot"
        )
    if source.continuity_state not in {CONTINUOUS, UNKNOWN_AFTER_GAP}:
        raise MappedCompatibleProjectionError("source.continuity_state 非法")
    if isinstance(source, RouteReplayState):
        source_kind = "route_replay_state"
        changes: Sequence[RouteLastChange] = source.latest_changes
        change_kind = "latest_change"
    else:
        source_kind = "replay_snapshot"
        changes = source.slot_changes
        change_kind = "slot_change"
        if source.boundary != "[start,end)":
            raise MappedCompatibleProjectionError("ReplaySnapshot boundary 非法")
        expected_count = (
            len(source.entries)
            if source.continuity_state == CONTINUOUS
            else None
        )
        if source.route_count != expected_count:
            raise MappedCompatibleProjectionError(
                "ReplaySnapshot route_count 与连续性冲突"
            )
    if any(not isinstance(item, RouteLastChange) for item in changes):
        raise MappedCompatibleProjectionError("source changes 类型非法")

    if classified_entries is None:
        classified_entries = _validated_entry_classification(
            source.entries, mapping
        )
    retained_entries, entry_refs, excluded_entry_refs = classified_entries
    retained_changes, change_refs, excluded_change_refs = _classify_items(
        changes,
        source_kind=change_kind,
        mapping=mapping,
    )
    typed_entries = tuple(
        item for item in retained_entries if isinstance(item, RouteStateEntry)
    )
    typed_changes = tuple(
        item for item in retained_changes if isinstance(item, RouteLastChange)
    )
    if len(typed_entries) != len(retained_entries) or len(typed_changes) != len(
        retained_changes
    ):  # pragma: no cover - 输入分支已固定类型
        raise MappedCompatibleProjectionError("投影内部类型冲突")

    if isinstance(source, RouteReplayState):
        projected: SnapshotLike = replace(
            source,
            entries=typed_entries,
            latest_changes=typed_changes,
        )
        route_count = projected.route_count
    else:
        projected = replace(
            source,
            entries=typed_entries,
            slot_changes=typed_changes,
            route_count=(
                len(typed_entries)
                if source.continuity_state == CONTINUOUS
                else None
            ),
        )
        route_count = projected.route_count

    excluded_refs = tuple(sorted(excluded_entry_refs + excluded_change_refs))
    reason_counts: Dict[str, int] = {}
    for ref in excluded_refs:
        reason_counts[ref.reason] = reason_counts.get(ref.reason, 0) + 1
    audit = ProjectionAuditSummary(
        input_entry_count=len(source.entries),
        retained_entry_count=len(typed_entries),
        excluded_entry_count=len(source.entries) - len(typed_entries),
        input_change_count=len(changes),
        retained_change_count=len(typed_changes),
        excluded_change_count=len(changes) - len(typed_changes),
        excluded_reason_counts=tuple(sorted(reason_counts.items())),
        excluded_prefixes=tuple(sorted({ref.prefix for ref in excluded_refs})),
        excluded_vp_ids=tuple(sorted({ref.vp_id for ref in excluded_refs})),
        excluded_route_event_ids=tuple(
            sorted({ref.route_event_id for ref in excluded_refs})
        ),
        retained_refs=tuple(sorted(entry_refs + change_refs)),
        excluded_refs=excluded_refs,
        prefix_contexts=_prefix_contexts(entry_refs),
    )

    blockers = ["strict_population_completeness_not_proven"]
    reasons = set(reason_counts)
    if reasons & {
        "origin_as_set",
        "origin_confederation_segment",
        "empty_as_path",
        "missing_as_path",
        "origin_unresolved",
    }:
        blockers.append("strict_population_blocked_by_unresolved_origin")
    if reasons & {
        "country_mapping_unknown",
        "country_mapping_conflict",
        "country_mapping_unresolved",
    }:
        blockers.append("strict_population_blocked_by_unresolved_mapping")
    if source.continuity_state != CONTINUOUS:
        blockers.append("continuity_unknown_after_input_gap")

    projection_id = _projection_id(
        source_kind=source_kind,
        mapping=mapping,
        continuity_state=source.continuity_state,
        missing_reasons=source.missing_reasons,
        route_count=route_count,
        audit=audit,
    )
    return MappedCompatibleProjection(
        projection_id=projection_id,
        projection_kind=PROJECTION_KIND,
        source_kind=source_kind,
        mapping_view=mapping.view,
        mapping_source_sha256=mapping.source_sha256,
        target_country=mapping.target_country,
        continuity_state=source.continuity_state,
        missing_reasons=source.missing_reasons,
        route_count=route_count,
        projected=projected,
        audit=audit,
        limitations=_LIMITATIONS,
        blockers=tuple(blockers),
    )


def build_mapped_compatible_projection(
    source: SnapshotLike,
    mapping: CountryMappingView,
) -> MappedCompatibleProjection:
    """生成一个可审计的 mapped-only compatible 投影，且不修改输入。

    ``projected`` 与输入保持同一 dataclass 类型，方便现有纯计算层读取。它只
    是研究视图，不是严格人口，也不得作为后续状态回放的 checkpoint。
    """

    return _build_mapped_compatible_projection(source, mapping)


def build_mapped_compatible_projection_series(
    sources: Sequence[SnapshotLike],
    mapping: CountryMappingView,
) -> Tuple[MappedCompatibleProjection, ...]:
    """批量投影共享同一不可变状态人口的连续快照。

    回放层在没有路由变化的相邻槽之间会复用同一个 ``entries`` tuple。逐槽
    重做完整人口的 origin/国家分类会让成本退化为“槽数 × 状态人口”，并在
    缺口窗口中反复得到完全相同的结果。本函数只在 tuple 对象身份严格相同
    时复用一次分类与结构校验；变化项、连续性、槽时间、审计摘要和稳定身份
    仍按每个 source 独立计算。缓存仅存在于本次函数调用内，不跨运行持有状态。
    """

    if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence):
        raise MappedCompatibleProjectionError("sources 必须是快照序列")
    if not isinstance(mapping, CountryMappingView):
        raise MappedCompatibleProjectionError("mapping 必须是 CountryMappingView")
    if mapping.view != "compatible":
        raise MappedCompatibleProjectionError("该投影只接受 compatible 映射视图")
    cache: Dict[
        int,
        Tuple[
            Tuple[RouteStateEntry, ...],
            Tuple[
                Tuple[Union[RouteStateEntry, RouteLastChange], ...],
                Tuple[MappedRouteReference, ...],
                Tuple[ExcludedRouteReference, ...],
            ],
        ],
    ] = {}
    projection_cache: Dict[
        Tuple[Any, ...],
        Tuple[
            Tuple[RouteStateEntry, ...],
            Tuple[RouteLastChange, ...],
            MappedCompatibleProjection,
        ],
    ] = {}
    projections = []
    for source in sources:
        if not isinstance(source, (RouteReplayState, ReplaySnapshot)):
            raise MappedCompatibleProjectionError(
                "sources 只能包含 RouteReplayState 或 ReplaySnapshot"
            )
        if isinstance(source, RouteReplayState):
            changes = source.latest_changes
            boundary = None
        else:
            changes = source.slot_changes
            boundary = source.boundary
        projection_key = (
            type(source),
            id(source.entries),
            id(changes),
            source.continuity_state,
            source.missing_reasons,
            source.route_count,
            boundary,
        )
        prior_projection = projection_cache.get(projection_key)
        if (
            prior_projection is not None
            and prior_projection[0] is source.entries
            and prior_projection[1] is changes
        ):
            previous = prior_projection[2]
            if isinstance(source, RouteReplayState):
                projected = replace(
                    source,
                    entries=previous.projected.entries,
                    latest_changes=previous.projected.latest_changes,
                )
            else:
                projected = replace(
                    source,
                    entries=previous.projected.entries,
                    slot_changes=previous.projected.slot_changes,
                    route_count=previous.route_count,
                )
            projections.append(replace(previous, projected=projected))
            continue
        cache_key = id(source.entries)
        cached = cache.get(cache_key)
        if cached is not None and cached[0] is source.entries:
            classified_entries = cached[1]
        else:
            classified_entries = _validated_entry_classification(
                source.entries, mapping
            )
            cache[cache_key] = (source.entries, classified_entries)
        projection = _build_mapped_compatible_projection(
            source,
            mapping,
            classified_entries=classified_entries,
        )
        projection_cache[projection_key] = (
            source.entries,
            changes,
            projection,
        )
        projections.append(projection)
    return tuple(projections)


__all__ = (
    "PROJECTION_KIND",
    "ExcludedRouteReference",
    "MappedCompatibleProjection",
    "MappedCompatibleProjectionError",
    "MappedPrefixContext",
    "MappedRouteReference",
    "ProjectionAuditSummary",
    "build_mapped_compatible_projection",
    "build_mapped_compatible_projection_series",
)
