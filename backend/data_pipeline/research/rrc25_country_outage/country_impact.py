"""RRC25 国家中断研究的国家/ASN 影响纯计算层。

本模块只消费 :mod:`state_replay` 已生成的不可变状态，不读取 MRT、文件、
数据库或网络。核心约束如下：

* origin 只在 AS_PATH 最后一个 segment 为 ``as_sequence`` 时取其末项；
  ``as_set``、confederation 和空路径均保留为显式冲突/未知，绝不猜测；
* 一个前缀只要至少被一个 VP 看见即为可见；不同 VP 的全部确定 origin
  关系均被保留，MOAS 不会被压平成一个 ASN；
* 国家地址量按地址族对前缀覆盖做集合并集，逐 ASN 的 MOAS 地址量不可相加
  代替国家总量；
* cohort 由 seed 状态中的静态 IR origin 与窗口内首次出现的动态 IR origin
  组成。动态 ASN/前缀只从首次观测快照起生效，避免未来信息污染早期样本；
* 基线、当前值、损伤集合和比例全部绑定同一个 ``snapshot_id``。状态缺口、
  国家映射不确定或 origin 无法判定时返回 unknown，而不是补零。

这里的“损伤”是观测定义：某 ASN 在当前快照缺少至少一个已经进入 cohort
参考集的前缀。它不是链路、会话或人为意图的因果结论。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import re
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

from ...route_event import AsPathSegment
from .state_replay import (
    CONTINUOUS,
    ReplaySnapshot,
    RouteReplayState,
    RouteStateEntry,
)


UTC = timezone.utc
RESOLVED = "resolved"
UNKNOWN = "unknown"
CONFLICT = "conflict"
MAPPED = "mapped"
UNKNOWN_MAPPING = "unknown"
CONFLICT_MAPPING = "conflict"
OBSERVED = "observed"
OBSERVED_ZERO = "observed_zero"
OBSERVED_EMPTY = "observed_empty"
UNKNOWN_STATE_GAP = "unknown_state_gap"
UNKNOWN_MAPPING_VALUE = "unknown_mapping"
MOAS_SEMANTICS = "origin_relationship_retained_not_additive"

_AFIS = ("ipv4", "ipv6")
_AFI_SAFI_TO_AFI = {
    "ipv4_unicast": "ipv4",
    "ipv6_unicast": "ipv6",
}
_MAPPING_VIEWS = frozenset(("compatible", "revised"))
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CountryImpactError(ValueError):
    """输入不能按冻结的国家影响语义安全计算。"""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _stable_id(prefix: str, identity: Mapping[str, Any], length: int = 24) -> str:
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    return prefix + digest[:length]


def _normalize_asn(value: object, field: str = "asn") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CountryImpactError(f"{field} 必须是整数")
    if value <= 0 or value > 4_294_967_295:
        raise CountryImpactError(f"{field} 越出 1..4294967295")
    return value


def _normalize_country(value: object) -> str:
    if not isinstance(value, str):
        raise CountryImpactError("country_code 必须是字符串")
    country = value.strip().upper()
    if country != value or _COUNTRY_RE.fullmatch(country) is None:
        raise CountryImpactError("country_code 必须是两位大写国家码")
    return country


def _normalize_utc(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CountryImpactError(f"{field} 必须是以 Z 结尾的 UTC 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise CountryImpactError(f"{field} 不是合法 UTC 时间") from error
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise CountryImpactError(f"{field} 必须是 UTC 时间")
    parsed = parsed.astimezone(UTC)
    if parsed.microsecond:
        raise CountryImpactError(f"{field} 必须精确到秒")
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _time_key(value: str) -> int:
    normalized = _normalize_utc(value, "observed_at")
    return int(datetime.fromisoformat(normalized[:-1] + "+00:00").timestamp())


@dataclass(frozen=True)
class OriginResolution:
    """一条 AS_PATH 的保真 origin 推导结果。"""

    state: str
    origins: Tuple[int, ...]
    reason: Optional[str]


def derive_origin_asns(as_path: Sequence[AsPathSegment]) -> OriginResolution:
    """按最后一个 AS_PATH segment 推导 origin，失败时显式保留不确定性。

    ``as_sequence`` 的最后一个 ASN 是唯一可确定分支。末段 ``as_set``
    保留候选 ASN 并标记 conflict；末段 confederation 或空路径标记 unknown。
    这比“从任意 segment 随便取最后一个数字”更保守，也不会丢失 AS_SET 候选。
    """

    if isinstance(as_path, (str, bytes, Mapping)):
        raise CountryImpactError("as_path 必须是 AsPathSegment 序列")
    try:
        segments = tuple(as_path)
    except TypeError as error:
        raise CountryImpactError("as_path 必须可迭代") from error
    if not segments:
        return OriginResolution(UNKNOWN, (), "empty_as_path")
    for segment in segments:
        if not isinstance(segment, AsPathSegment):
            raise CountryImpactError("as_path 只能包含 AsPathSegment")
        if segment.segment_type not in {
            "as_sequence",
            "as_set",
            "confederation_sequence",
            "confederation_set",
        }:
            raise CountryImpactError("AS_PATH segment_type 非法")
        if not isinstance(segment.asns, tuple) or not segment.asns:
            raise CountryImpactError("AS_PATH segment 必须包含 ASN")
        for asn in segment.asns:
            _normalize_asn(asn, "AS_PATH ASN")

    last = segments[-1]
    if last.segment_type == "as_sequence":
        return OriginResolution(RESOLVED, (last.asns[-1],), None)
    if last.segment_type == "as_set":
        return OriginResolution(
            CONFLICT,
            tuple(sorted(set(last.asns))),
            "origin_as_set",
        )
    return OriginResolution(UNKNOWN, (), "origin_confederation_segment")


@dataclass(frozen=True)
class MappingAssignment:
    """一个 ASN 在某映射视图中的确定、缺失或冲突事实。"""

    asn: int
    countries: Tuple[str, ...]
    mapping_state: str

    def __post_init__(self) -> None:
        _normalize_asn(self.asn)
        if self.mapping_state not in {MAPPED, UNKNOWN_MAPPING, CONFLICT_MAPPING}:
            raise CountryImpactError("mapping_state 非法")
        normalized = tuple(sorted({_normalize_country(code) for code in self.countries}))
        if normalized != self.countries:
            raise CountryImpactError("countries 必须已去重并按字典序排序")
        if self.mapping_state == MAPPED and len(normalized) != 1:
            raise CountryImpactError("mapped assignment 必须且只能有一个国家")
        if self.mapping_state == UNKNOWN_MAPPING and normalized:
            raise CountryImpactError("unknown assignment 不得伪造国家")
        if self.mapping_state == CONFLICT_MAPPING and not normalized:
            raise CountryImpactError("conflict assignment 必须保留至少一个候选国家")


@dataclass(frozen=True)
class CountryMappingView:
    """不访问文件的冻结 AS→国家映射投影。"""

    view: str
    target_country: str
    assignments: Tuple[MappingAssignment, ...]
    source_sha256: str
    source_ref: str

    def __post_init__(self) -> None:
        if self.view not in _MAPPING_VIEWS:
            raise CountryImpactError("mapping view 必须是 compatible 或 revised")
        _normalize_country(self.target_country)
        if not _SHA256_RE.fullmatch(self.source_sha256):
            raise CountryImpactError("source_sha256 必须是 64 位小写十六进制")
        if not isinstance(self.source_ref, str) or not self.source_ref.strip():
            raise CountryImpactError("source_ref 必须是非空字符串")
        if tuple(sorted(self.assignments, key=lambda row: row.asn)) != self.assignments:
            raise CountryImpactError("assignments 必须按 ASN 排序")
        asns = tuple(row.asn for row in self.assignments)
        if len(asns) != len(set(asns)):
            raise CountryImpactError("assignments 不得重复 ASN")

    def assignment_for(self, asn: int) -> MappingAssignment:
        target = _normalize_asn(asn)
        # 真实冻结映射超过十万行。保持 dataclass 不可变的同时使用二分查找，
        # 避免每条 RIB/UPDATE origin 都做 O(n) 线性扫描。
        left = 0
        right = len(self.assignments)
        while left < right:
            middle = (left + right) // 2
            if self.assignments[middle].asn < target:
                left = middle + 1
            else:
                right = middle
        position = left
        if (
            position < len(self.assignments)
            and self.assignments[position].asn == target
        ):
            return self.assignments[position]
        return MappingAssignment(target, (), UNKNOWN_MAPPING)

    def target_membership(self, asn: int) -> Optional[bool]:
        assignment = self.assignment_for(asn)
        if assignment.mapping_state != MAPPED:
            return None
        return assignment.countries[0] == self.target_country


def build_country_mapping_view(
    assignments: Iterable[MappingAssignment],
    *,
    view: str,
    target_country: str,
    source_sha256: str,
    source_ref: str,
) -> CountryMappingView:
    """从已冻结的内存赋值构造兼容或修订视图。"""

    if isinstance(assignments, (str, bytes, Mapping)):
        raise CountryImpactError("assignments 必须是 MappingAssignment 可迭代对象")
    try:
        values = tuple(assignments)
    except TypeError as error:
        raise CountryImpactError("assignments 必须可迭代") from error
    if any(not isinstance(value, MappingAssignment) for value in values):
        raise CountryImpactError("assignments 只能包含 MappingAssignment")
    return CountryMappingView(
        view=view,
        target_country=_normalize_country(target_country),
        assignments=tuple(sorted(values, key=lambda row: row.asn)),
        source_sha256=source_sha256,
        source_ref=source_ref,
    )


def mapping_view_from_frozen_snapshot(
    snapshot: Mapping[str, Any],
    *,
    view: str = "compatible",
) -> CountryMappingView:
    """把 :func:`freeze_as_country_mapping` 的结果转成纯计算视图。

    兼容视图仍保留 first-row-wins 的源值，但任何有冲突的 ASN 都标记
    ``conflict``，不会被当成确定 IR 或确定非 IR。修订视图必须由调用方用
    :func:`build_country_mapping_view` 显式提供，避免悄悄覆盖旧口径。
    """

    if view != "compatible":
        raise CountryImpactError("冻结旧映射只能直接生成 compatible 视图")
    if not isinstance(snapshot, Mapping):
        raise CountryImpactError("mapping snapshot 必须是对象")
    rows = snapshot.get("rows")
    conflicts = snapshot.get("conflicts")
    if not isinstance(rows, list) or not isinstance(conflicts, list):
        raise CountryImpactError("mapping snapshot 缺少 rows/conflicts")
    target = _normalize_country(snapshot.get("target_country"))
    source_sha256 = snapshot.get("source_file_sha256")
    if not isinstance(source_sha256, str) or not _SHA256_RE.fullmatch(source_sha256):
        raise CountryImpactError("mapping snapshot source_file_sha256 非法")

    conflict_countries: Dict[int, set[str]] = {}
    for conflict in conflicts:
        if not isinstance(conflict, Mapping):
            raise CountryImpactError("mapping conflict 必须是对象")
        asn = _normalize_asn(conflict.get("asn"), "conflict.asn")
        values = conflict_countries.setdefault(asn, set())
        for field in ("kept_country", "conflicting_country"):
            value = conflict.get(field)
            if value is not None:
                values.add(_normalize_country(value))

    assignments = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise CountryImpactError("mapping row 必须是对象")
        asn = _normalize_asn(row.get("asn"), "row.asn")
        country = row.get("country_code")
        if asn in conflict_countries:
            values = set(conflict_countries[asn])
            if country is not None:
                values.add(_normalize_country(country))
            assignments.append(
                MappingAssignment(asn, tuple(sorted(values)), CONFLICT_MAPPING)
            )
        elif country is None:
            assignments.append(MappingAssignment(asn, (), UNKNOWN_MAPPING))
        else:
            assignments.append(
                MappingAssignment(asn, (_normalize_country(country),), MAPPED)
            )

    return build_country_mapping_view(
        assignments,
        view=view,
        target_country=target,
        source_sha256=source_sha256,
        source_ref=str(snapshot.get("snapshot_id") or "mapping_snapshot"),
    )


@dataclass(frozen=True)
class OriginIssue:
    snapshot_id: str
    observed_at: Optional[str]
    afi: str
    prefix: str
    vp_id: str
    state: str
    candidate_origins: Tuple[int, ...]
    reason: str


@dataclass(frozen=True)
class PrefixOriginRelation:
    """同一快照中一个可见前缀的全部 VP 与确定 origin 关系。"""

    afi: str
    prefix: str
    vp_ids: Tuple[str, ...]
    observations: Tuple["VpOriginObservation", ...]
    origins: Tuple[int, ...]
    ambiguous_vp_ids: Tuple[str, ...]
    candidate_origins: Tuple[int, ...]
    moas: bool
    moas_semantics: str = MOAS_SEMANTICS


@dataclass(frozen=True)
class VpOriginObservation:
    """保留 prefix→VP→origin 与最近 RouteEvent 的一对一观测关系。"""

    vp_id: str
    origin_state: str
    origins: Tuple[int, ...]
    missing_reason: Optional[str]
    route_event_id: str


@dataclass(frozen=True)
class SnapshotProjection:
    snapshot_id: str
    observed_at: Optional[str]
    continuity_state: str
    missing_reasons: Tuple[str, ...]
    relations: Tuple[PrefixOriginRelation, ...]
    origin_issues: Tuple[OriginIssue, ...]


SnapshotLike = Union[RouteReplayState, ReplaySnapshot]


def _validate_snapshot_shell(snapshot: SnapshotLike) -> None:
    if not isinstance(snapshot, (RouteReplayState, ReplaySnapshot)):
        raise CountryImpactError("snapshot 必须是 RouteReplayState 或 ReplaySnapshot")
    if snapshot.continuity_state not in {CONTINUOUS, "unknown_after_gap"}:
        raise CountryImpactError("snapshot.continuity_state 非法")
    if snapshot.continuity_state == CONTINUOUS and snapshot.route_count != len(
        snapshot.entries
    ):
        raise CountryImpactError("continuous snapshot 的 route_count 与 entries 不一致")
    if snapshot.continuity_state != CONTINUOUS and snapshot.route_count is not None:
        raise CountryImpactError("unknown snapshot 的 route_count 必须为 None")
    if isinstance(snapshot, ReplaySnapshot):
        start = _normalize_utc(snapshot.slot_start_utc, "slot_start_utc")
        end = _normalize_utc(snapshot.slot_end_exclusive_utc, "slot_end_exclusive_utc")
        if _time_key(end) - _time_key(start) != 300:
            raise CountryImpactError("ReplaySnapshot 必须表达五分钟槽")
        if snapshot.boundary != "[start,end)":
            raise CountryImpactError("ReplaySnapshot boundary 必须是 [start,end)")


def _snapshot_entries(snapshot: SnapshotLike) -> Tuple[RouteStateEntry, ...]:
    _validate_snapshot_shell(snapshot)
    entries = snapshot.entries
    if any(not isinstance(entry, RouteStateEntry) for entry in entries):
        raise CountryImpactError("snapshot.entries 只能包含 RouteStateEntry")
    keys = tuple(entry.key for entry in entries)
    if len(keys) != len(set(keys)):
        raise CountryImpactError("snapshot 中存在重复 route state key")
    return tuple(sorted(entries, key=lambda entry: entry.key))


def _snapshot_observed_at(snapshot: SnapshotLike) -> Optional[str]:
    if isinstance(snapshot, ReplaySnapshot):
        return _normalize_utc(snapshot.slot_end_exclusive_utc, "slot_end_exclusive_utc")
    return None


def _entry_identity_rows(
    entries: Sequence[RouteStateEntry],
) -> list[Mapping[str, Any]]:
    rows = []
    for entry in entries:
        rows.append(
            {
                "collector_id": entry.key.collector_id,
                "vp_id": entry.key.vp_id,
                "afi_safi": entry.key.afi_safi,
                "prefix": entry.key.prefix,
                "peer_ip": entry.peer_ip,
                "peer_asn": entry.peer_asn,
                "as_path": [
                    {"segment_type": segment.segment_type, "asns": list(segment.asns)}
                    for segment in entry.as_path
                ],
                "quality_flags": list(entry.quality_flags),
                "last_action": entry.last_action,
                "last_event_time_utc": entry.last_event_time_utc,
                "raw_ref": {
                    "artifact_id": entry.last_raw_ref.artifact_id,
                    "file_sha256": entry.last_raw_ref.file_sha256,
                    "record_ordinal": entry.last_raw_ref.record_ordinal,
                    "element_ordinal": entry.last_raw_ref.element_ordinal,
                    "route_event_id": entry.last_raw_ref.route_event_id,
                },
            }
        )
    return rows


def _snapshot_identity(
    snapshot: SnapshotLike,
    *,
    normalized_entries: Optional[Sequence[RouteStateEntry]] = None,
) -> Dict[str, Any]:
    ordered = (
        _snapshot_entries(snapshot)
        if normalized_entries is None
        else tuple(normalized_entries)
    )
    return {
        "schema": "rrc25_country_impact_snapshot_id_v1",
        "slot_start": (
            snapshot.slot_start_utc if isinstance(snapshot, ReplaySnapshot) else None
        ),
        "observed_at": _snapshot_observed_at(snapshot),
        "boundary": snapshot.boundary if isinstance(snapshot, ReplaySnapshot) else None,
        "continuity_state": snapshot.continuity_state,
        "missing_reasons": list(snapshot.missing_reasons),
        "entries": _entry_identity_rows(ordered),
    }


def snapshot_id_v1(snapshot: SnapshotLike) -> str:
    """由完整状态语义确定性生成 ``snapshot_v1`` 身份。"""

    return _stable_id("snapshot_v1_", _snapshot_identity(snapshot))


def _snapshot_id_from_entries_json(
    snapshot: SnapshotLike, entries_json: str
) -> str:
    """用已规范化的人口片段计算与 ``_stable_id`` 完全一致的身份。"""

    identity = {
        "schema": "rrc25_country_impact_snapshot_id_v1",
        "slot_start": (
            snapshot.slot_start_utc
            if isinstance(snapshot, ReplaySnapshot)
            else None
        ),
        "observed_at": _snapshot_observed_at(snapshot),
        "boundary": (
            snapshot.boundary if isinstance(snapshot, ReplaySnapshot) else None
        ),
        "continuity_state": snapshot.continuity_state,
        "missing_reasons": list(snapshot.missing_reasons),
    }
    # canonical JSON 的顶层字段固定且按字典序排列。只有 entries 可能很大，
    # 因而直接嵌入已缓存的规范片段，避免共享人口的 216 个槽反复序列化。
    encoded = (
        '{"boundary":'
        + _canonical_json(identity["boundary"])
        + ',"continuity_state":'
        + _canonical_json(identity["continuity_state"])
        + ',"entries":'
        + entries_json
        + ',"missing_reasons":'
        + _canonical_json(identity["missing_reasons"])
        + ',"observed_at":'
        + _canonical_json(identity["observed_at"])
        + ',"schema":'
        + _canonical_json(identity["schema"])
        + ',"slot_start":'
        + _canonical_json(identity["slot_start"])
        + "}"
    )
    return "snapshot_v1_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def snapshot_ids_v1(snapshots: Sequence[SnapshotLike]) -> Tuple[str, ...]:
    """批量计算快照身份，并按共享 ``entries`` tuple 复用人口规范化。

    缓存只在本次调用内生效，并以对象身份加 ``is`` 校验防止错误复用。每个
    快照的时间、连续性和缺口仍独立进入稳定身份。
    """

    if isinstance(snapshots, (str, bytes)) or not isinstance(snapshots, Sequence):
        raise CountryImpactError("snapshots 必须是快照序列")
    cache: Dict[int, Tuple[Tuple[RouteStateEntry, ...], str]] = {}
    output = []
    for snapshot in snapshots:
        _validate_snapshot_shell(snapshot)
        key = id(snapshot.entries)
        cached = cache.get(key)
        if cached is None or cached[0] is not snapshot.entries:
            normalized = _snapshot_entries(snapshot)
            entries_json = _canonical_json(_entry_identity_rows(normalized))
            cache[key] = (snapshot.entries, entries_json)
        else:
            entries_json = cached[1]
        output.append(_snapshot_id_from_entries_json(snapshot, entries_json))
    return tuple(output)


def _project_snapshot_origins_with_id(
    snapshot: SnapshotLike,
    snapshot_id: str,
) -> SnapshotProjection:
    entries = _snapshot_entries(snapshot)
    observed_at = _snapshot_observed_at(snapshot)
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    issues = []
    for entry in entries:
        afi = _AFI_SAFI_TO_AFI.get(entry.key.afi_safi)
        if afi is None:
            raise CountryImpactError("状态快照只支持 IPv4/IPv6 unicast")
        try:
            prefix = ipaddress.ip_network(entry.key.prefix, strict=True).compressed
        except ValueError as error:
            raise CountryImpactError("状态快照包含非规范 CIDR") from error
        expected_afi = "ipv4" if ipaddress.ip_network(prefix).version == 4 else "ipv6"
        if afi != expected_afi:
            raise CountryImpactError("prefix 与 afi_safi 地址族冲突")
        bucket = grouped.setdefault(
            (afi, prefix),
            {
                "vp_ids": set(),
                "observations": [],
                "origins": set(),
                "ambiguous": set(),
                "candidates": set(),
            },
        )
        bucket["vp_ids"].add(entry.key.vp_id)
        resolution = derive_origin_asns(entry.as_path)
        bucket["observations"].append(
            VpOriginObservation(
                vp_id=entry.key.vp_id,
                origin_state=resolution.state,
                origins=resolution.origins,
                missing_reason=resolution.reason,
                route_event_id=entry.last_raw_ref.route_event_id,
            )
        )
        if resolution.state == RESOLVED:
            bucket["origins"].update(resolution.origins)
        else:
            bucket["ambiguous"].add(entry.key.vp_id)
            bucket["candidates"].update(resolution.origins)
            issues.append(
                OriginIssue(
                    snapshot_id=snapshot_id,
                    observed_at=observed_at,
                    afi=afi,
                    prefix=prefix,
                    vp_id=entry.key.vp_id,
                    state=resolution.state,
                    candidate_origins=resolution.origins,
                    reason=resolution.reason or "origin_unresolved",
                )
            )

    relations = []
    for (afi, prefix), bucket in sorted(grouped.items()):
        origins = tuple(sorted(bucket["origins"]))
        candidates = tuple(sorted(bucket["candidates"]))
        relations.append(
            PrefixOriginRelation(
                afi=afi,
                prefix=prefix,
                vp_ids=tuple(sorted(bucket["vp_ids"])),
                observations=tuple(
                    sorted(
                        bucket["observations"],
                        key=lambda item: (item.vp_id, item.route_event_id),
                    )
                ),
                origins=origins,
                ambiguous_vp_ids=tuple(sorted(bucket["ambiguous"])),
                candidate_origins=candidates,
                moas=len(set(origins) | set(candidates)) > 1,
            )
        )
    return SnapshotProjection(
        snapshot_id=snapshot_id,
        observed_at=observed_at,
        continuity_state=snapshot.continuity_state,
        missing_reasons=tuple(sorted(set(snapshot.missing_reasons))),
        relations=tuple(relations),
        origin_issues=tuple(
            sorted(issues, key=lambda item: (item.afi, item.prefix, item.vp_id))
        ),
    )


def project_snapshot_origins(snapshot: SnapshotLike) -> SnapshotProjection:
    """把逐 VP 状态投影为“至少一个 VP 可见”的前缀/origin 关系。"""

    return _project_snapshot_origins_with_id(snapshot, snapshot_id_v1(snapshot))


def project_snapshot_origins_series(
    snapshots: Sequence[SnapshotLike],
) -> Tuple[SnapshotProjection, ...]:
    """批量投影共享同一不可变状态人口的快照序列。

    ``ReplaySnapshot`` 在无变化和显式 gap 槽会复用同一个 ``entries`` tuple。
    prefix/VP/origin 关系只由该人口决定，因此每个共享人口只扫描一次；快照
    身份、时间、连续性、缺口和 issue 坐标仍逐槽独立生成。
    """

    ids = snapshot_ids_v1(snapshots)
    cache: Dict[
        int,
        Tuple[Tuple[RouteStateEntry, ...], SnapshotProjection],
    ] = {}
    output = []
    for snapshot, snapshot_id in zip(snapshots, ids):
        key = id(snapshot.entries)
        cached = cache.get(key)
        if cached is None or cached[0] is not snapshot.entries:
            projection = _project_snapshot_origins_with_id(snapshot, snapshot_id)
            cache[key] = (snapshot.entries, projection)
        else:
            template = cached[1]
            observed_at = _snapshot_observed_at(snapshot)
            projection = SnapshotProjection(
                snapshot_id=snapshot_id,
                observed_at=observed_at,
                continuity_state=snapshot.continuity_state,
                missing_reasons=tuple(sorted(set(snapshot.missing_reasons))),
                relations=template.relations,
                origin_issues=tuple(
                    replace(
                        issue,
                        snapshot_id=snapshot_id,
                        observed_at=observed_at,
                    )
                    for issue in template.origin_issues
                ),
            )
        output.append(projection)
    return tuple(output)


@dataclass(frozen=True)
class CohortPrefixReference:
    asn: int
    afi: str
    prefix: str
    first_seen_at: Optional[str]
    source_snapshot_id: str
    baseline_member: bool


@dataclass(frozen=True)
class CohortIssue:
    snapshot_id: str
    observed_at: Optional[str]
    value_state: str
    missing_reason: str
    details: str


@dataclass(frozen=True)
class CountryCohort:
    country_code: str
    cohort_view: str
    mapping_source_sha256: str
    mapping_source_ref: str
    baseline_snapshot_id: str
    baseline_asns: Tuple[int, ...]
    dynamic_asns: Tuple[int, ...]
    member_first_seen: Tuple[Tuple[int, Optional[str]], ...]
    prefix_references: Tuple[CohortPrefixReference, ...]
    covered_snapshots: Tuple[Tuple[str, str], ...]
    issues: Tuple[CohortIssue, ...]


def _origin_target_state(
    resolution_state: str,
    origins: Sequence[int],
    mapping: CountryMappingView,
) -> Tuple[Optional[bool], Optional[str]]:
    """判断一条确定/候选 origin 关系是否属于目标国家。"""

    memberships = []
    for asn in origins:
        memberships.append(mapping.target_membership(asn))
    if resolution_state == RESOLVED:
        if len(memberships) != 1:
            raise CountryImpactError("resolved origin 必须只有一个 ASN")
        if memberships[0] is None:
            return None, "as_country_mapping_unresolved"
        return memberships[0], None
    if resolution_state == CONFLICT:
        if not memberships or any(value is None for value in memberships):
            return None, "origin_or_mapping_conflict"
        if any(memberships):
            return None, "origin_as_set_may_include_target"
        return False, None
    return None, "origin_unresolved"


def _projection_target_relations(
    projection: SnapshotProjection,
    mapping: CountryMappingView,
) -> Tuple[Dict[int, Dict[str, set[str]]], Tuple[CohortIssue, ...]]:
    by_asn: Dict[int, Dict[str, set[str]]] = {}
    issues = []
    for relation in projection.relations:
        for asn in relation.origins:
            membership, reason = _origin_target_state(RESOLVED, (asn,), mapping)
            if membership is True:
                by_asn.setdefault(asn, {"ipv4": set(), "ipv6": set()})[
                    relation.afi
                ].add(relation.prefix)
            elif membership is None:
                issues.append(
                    CohortIssue(
                        snapshot_id=projection.snapshot_id,
                        observed_at=projection.observed_at,
                        value_state=UNKNOWN_MAPPING_VALUE,
                        missing_reason="country_mapping_unresolved",
                        details=f"asn:{asn}:{reason}",
                    )
                )
        if relation.ambiguous_vp_ids:
            state = CONFLICT if relation.candidate_origins else UNKNOWN
            membership, reason = _origin_target_state(
                state, relation.candidate_origins, mapping
            )
            if membership is None:
                issues.append(
                    CohortIssue(
                        snapshot_id=projection.snapshot_id,
                        observed_at=projection.observed_at,
                        value_state=UNKNOWN_MAPPING_VALUE,
                        missing_reason="origin_resolution_unresolved",
                        details=f"{relation.afi}:{relation.prefix}:{reason}",
                    )
                )
    return by_asn, tuple(issues)


def build_country_cohort(
    baseline_snapshot: SnapshotLike,
    window_snapshots: Iterable[ReplaySnapshot],
    mapping: CountryMappingView,
    *,
    _precomputed_projections: Optional[Sequence[SnapshotProjection]] = None,
) -> CountryCohort:
    """构建 seed 静态 cohort 与按首次观测生效的动态国家 cohort。"""

    if not isinstance(mapping, CountryMappingView):
        raise CountryImpactError("mapping 必须是 CountryMappingView")
    if isinstance(window_snapshots, (str, bytes, Mapping)):
        raise CountryImpactError("window_snapshots 必须是 ReplaySnapshot 序列")
    try:
        snapshots = tuple(window_snapshots)
    except TypeError as error:
        raise CountryImpactError("window_snapshots 必须可迭代") from error
    if any(not isinstance(snapshot, ReplaySnapshot) for snapshot in snapshots):
        raise CountryImpactError("window_snapshots 只能包含 ReplaySnapshot")
    if _precomputed_projections is None:
        all_projections = project_snapshot_origins_series(
            (baseline_snapshot, *snapshots)
        )
    else:
        all_projections = tuple(_precomputed_projections)
        if len(all_projections) != len(snapshots) + 1 or any(
            not isinstance(row, SnapshotProjection) for row in all_projections
        ):
            raise CountryImpactError("预计算 projection 没有精确覆盖 seed 与窗口")
    baseline = all_projections[0]
    projections = all_projections[1:]
    observed_times = tuple(projection.observed_at for projection in projections)
    if any(value is None for value in observed_times):  # pragma: no cover
        raise CountryImpactError("窗口快照必须有 observed_at")
    if tuple(sorted(observed_times)) != observed_times or len(set(observed_times)) != len(observed_times):
        raise CountryImpactError("窗口快照必须按唯一 observed_at 严格递增")

    baseline_by_asn, baseline_mapping_issues = _projection_target_relations(
        baseline, mapping
    )
    baseline_asns = tuple(sorted(baseline_by_asn))
    first_seen: Dict[int, Optional[str]] = {asn: None for asn in baseline_asns}
    references: Dict[Tuple[int, str, str], CohortPrefixReference] = {}
    for asn, families in baseline_by_asn.items():
        for afi in _AFIS:
            for prefix in sorted(families[afi]):
                references[(asn, afi, prefix)] = CohortPrefixReference(
                    asn=asn,
                    afi=afi,
                    prefix=prefix,
                    first_seen_at=None,
                    source_snapshot_id=baseline.snapshot_id,
                    baseline_member=True,
                )

    issues = list(baseline_mapping_issues)
    if baseline.continuity_state != CONTINUOUS:
        issues.append(
            CohortIssue(
                snapshot_id=baseline.snapshot_id,
                observed_at=None,
                value_state=UNKNOWN_STATE_GAP,
                missing_reason="baseline_state_gap",
                details=":".join(baseline.missing_reasons) or "unknown-after-gap",
            )
        )

    dynamic = set()
    covered = []
    relation_cache: Dict[
        int,
        Tuple[
            Tuple[PrefixOriginRelation, ...],
            Dict[int, Dict[str, set[str]]],
        ],
    ] = {}
    for projection in projections:
        if projection.observed_at is None:  # pragma: no cover
            raise CountryImpactError("窗口 projection 缺少 observed_at")
        covered.append((projection.snapshot_id, projection.observed_at))
        cached_relations = relation_cache.get(id(projection.relations))
        if (
            cached_relations is not None
            and cached_relations[0] is projection.relations
        ):
            by_asn = cached_relations[1]
            projection_issues = ()
            population_reused = True
        else:
            by_asn, projection_issues = _projection_target_relations(
                projection, mapping
            )
            # Issue 中含快照坐标，只有完全无 issue 的确定映射人口可安全复用。
            if not projection_issues:
                relation_cache[id(projection.relations)] = (
                    projection.relations,
                    by_asn,
                )
            population_reused = False
        issues.extend(projection_issues)
        if projection.continuity_state != CONTINUOUS:
            issues.append(
                CohortIssue(
                    snapshot_id=projection.snapshot_id,
                    observed_at=projection.observed_at,
                    value_state=UNKNOWN_STATE_GAP,
                    missing_reason="snapshot_state_gap",
                    details=":".join(projection.missing_reasons) or "unknown-after-gap",
                )
            )
        if not population_reused:
            for asn, families in by_asn.items():
                if asn not in first_seen:
                    first_seen[asn] = projection.observed_at
                    dynamic.add(asn)
                for afi in _AFIS:
                    for prefix in sorted(families[afi]):
                        key = (asn, afi, prefix)
                        if key not in references:
                            references[key] = CohortPrefixReference(
                                asn=asn,
                                afi=afi,
                                prefix=prefix,
                                first_seen_at=projection.observed_at,
                                source_snapshot_id=projection.snapshot_id,
                                baseline_member=asn in baseline_asns,
                            )

    unique_issues = {
        (
            issue.snapshot_id,
            issue.observed_at,
            issue.value_state,
            issue.missing_reason,
            issue.details,
        ): issue
        for issue in issues
    }
    return CountryCohort(
        country_code=mapping.target_country,
        cohort_view=mapping.view,
        mapping_source_sha256=mapping.source_sha256,
        mapping_source_ref=mapping.source_ref,
        baseline_snapshot_id=baseline.snapshot_id,
        baseline_asns=baseline_asns,
        dynamic_asns=tuple(sorted(dynamic)),
        member_first_seen=tuple(sorted(first_seen.items())),
        prefix_references=tuple(
            references[key] for key in sorted(references)
        ),
        covered_snapshots=tuple(covered),
        issues=tuple(
            sorted(
                unique_issues.values(),
                key=lambda item: (
                    item.observed_at or "",
                    item.snapshot_id,
                    item.missing_reason,
                    item.details,
                ),
            )
        ),
    )


@dataclass(frozen=True)
class MeasuredValue:
    snapshot_id: str
    value: Optional[Union[int, float]]
    value_state: str
    missing_reason: Optional[str]


@dataclass(frozen=True)
class MeasuredAsnSet:
    snapshot_id: str
    value: Optional[Tuple[int, ...]]
    value_state: str
    missing_reason: Optional[str]


@dataclass(frozen=True)
class SameSnapshotRatio:
    snapshot_id: str
    numerator: Optional[int]
    denominator: Optional[int]
    value: Optional[float]
    value_state: str
    missing_reason: Optional[str]


@dataclass(frozen=True)
class AddressFamilyDamage:
    afi: str
    reference_prefixes: Optional[Tuple[str, ...]]
    visible_prefixes: Optional[Tuple[str, ...]]
    lost_prefixes: Optional[Tuple[str, ...]]
    lost_equivalent: Optional[Union[int, float]]
    fully_invisible: Optional[bool]
    value_state: str
    missing_reason: Optional[str]
    moas_prefixes: Optional[Tuple[str, ...]]
    moas_semantics: str = MOAS_SEMANTICS


@dataclass(frozen=True)
class AsnDamage:
    asn: int
    baseline_member: bool
    dynamic_member: bool
    visible: Optional[bool]
    damaged: Optional[bool]
    address_families: Tuple[AddressFamilyDamage, AddressFamilyDamage]
    overall_classification: str
    value_state: str
    missing_reason: Optional[str]


@dataclass(frozen=True)
class CountryMetrics:
    visible_asn_count: MeasuredValue
    damaged_asn_count: MeasuredValue
    baseline_asn_count: MeasuredValue
    visible_ipv4_prefix_count: MeasuredValue
    visible_ipv6_prefix_count: MeasuredValue
    visible_ipv4_address_union: MeasuredValue
    visible_ipv4_24_equivalent: MeasuredValue
    visible_ipv6_48_equivalent: MeasuredValue
    damaged_asn_ratio: SameSnapshotRatio


@dataclass(frozen=True)
class CountrySnapshotImpact:
    snapshot_id: str
    observed_at: str
    country_code: str
    cohort_view: str
    continuity_state: str
    metrics: CountryMetrics
    visible_asns: MeasuredAsnSet
    damaged_asns: MeasuredAsnSet
    baseline_asns: MeasuredAsnSet
    prefix_relations: Tuple[PrefixOriginRelation, ...]
    asn_impacts: Tuple[AsnDamage, ...]
    issues: Tuple[CohortIssue, ...]


def _measure(snapshot_id: str, value: Union[int, float]) -> MeasuredValue:
    return MeasuredValue(
        snapshot_id=snapshot_id,
        value=value,
        value_state=OBSERVED_ZERO if value == 0 else OBSERVED,
        missing_reason=None,
    )


def _unknown_measure(snapshot_id: str, state: str, reason: str) -> MeasuredValue:
    return MeasuredValue(snapshot_id, None, state, reason)


def _asn_set(snapshot_id: str, values: Iterable[int]) -> MeasuredAsnSet:
    normalized = tuple(sorted(set(values)))
    return MeasuredAsnSet(
        snapshot_id=snapshot_id,
        value=normalized,
        value_state=OBSERVED if normalized else OBSERVED_EMPTY,
        missing_reason=None,
    )


def _unknown_asn_set(snapshot_id: str, state: str, reason: str) -> MeasuredAsnSet:
    return MeasuredAsnSet(snapshot_id, None, state, reason)


def _address_union(prefixes: Iterable[str], afi: str) -> int:
    networks = []
    for prefix in sorted(set(prefixes)):
        try:
            network = ipaddress.ip_network(prefix, strict=True)
        except ValueError as error:
            raise CountryImpactError("prefix 不是规范 CIDR") from error
        expected = 4 if afi == "ipv4" else 6
        if network.version != expected:
            raise CountryImpactError("prefix 与地址族不一致")
        networks.append(network)
    return sum(network.num_addresses for network in ipaddress.collapse_addresses(networks))


def _equivalent(prefixes: Iterable[str], afi: str) -> Union[int, float]:
    addresses = _address_union(prefixes, afi)
    denominator = 256 if afi == "ipv4" else 1 << 80
    if addresses % denominator == 0:
        return addresses // denominator
    return addresses / denominator


def _issues_effective_at(
    cohort: CountryCohort, observed_at: str
) -> Tuple[CohortIssue, ...]:
    current = _time_key(observed_at)
    return tuple(
        issue
        for issue in cohort.issues
        if issue.observed_at is None or _time_key(issue.observed_at) <= current
    )


def _active_references(
    cohort: CountryCohort, observed_at: str
) -> Tuple[CohortPrefixReference, ...]:
    current = _time_key(observed_at)
    return tuple(
        reference
        for reference in cohort.prefix_references
        if reference.first_seen_at is None
        or _time_key(reference.first_seen_at) <= current
    )


def _active_asns(cohort: CountryCohort, observed_at: str) -> Tuple[int, ...]:
    current = _time_key(observed_at)
    return tuple(
        asn
        for asn, first_seen_at in cohort.member_first_seen
        if first_seen_at is None or _time_key(first_seen_at) <= current
    )


def _unknown_impact(
    projection: SnapshotProjection,
    cohort: CountryCohort,
    state: str,
    reason: str,
    issues: Tuple[CohortIssue, ...],
) -> CountrySnapshotImpact:
    metric = _unknown_measure(projection.snapshot_id, state, reason)
    ratio = SameSnapshotRatio(
        projection.snapshot_id, None, None, None, state, reason
    )
    metrics = CountryMetrics(
        visible_asn_count=metric,
        damaged_asn_count=metric,
        baseline_asn_count=metric,
        visible_ipv4_prefix_count=metric,
        visible_ipv6_prefix_count=metric,
        visible_ipv4_address_union=metric,
        visible_ipv4_24_equivalent=metric,
        visible_ipv6_48_equivalent=metric,
        damaged_asn_ratio=ratio,
    )
    unknown_set = _unknown_asn_set(projection.snapshot_id, state, reason)
    return CountrySnapshotImpact(
        snapshot_id=projection.snapshot_id,
        observed_at=projection.observed_at or "",
        country_code=cohort.country_code,
        cohort_view=cohort.cohort_view,
        continuity_state=projection.continuity_state,
        metrics=metrics,
        visible_asns=unknown_set,
        damaged_asns=unknown_set,
        baseline_asns=unknown_set,
        prefix_relations=projection.relations,
        asn_impacts=(),
        issues=issues,
    )


def compute_country_snapshot_impact(
    snapshot: ReplaySnapshot,
    cohort: CountryCohort,
    *,
    _precomputed_projection: Optional[SnapshotProjection] = None,
) -> CountrySnapshotImpact:
    """从同一五分钟状态快照计算国家与逐 ASN 损伤事实。

    ``cohort`` 可以先扫描完整窗口构建，但动态成员和动态参考前缀只在其
    ``first_seen_at`` 到达后激活，因此不会把后续发现回填到更早的分母。
    """

    if not isinstance(snapshot, ReplaySnapshot):
        raise CountryImpactError("影响计算必须使用 ReplaySnapshot")
    if not isinstance(cohort, CountryCohort):
        raise CountryImpactError("cohort 必须是 CountryCohort")
    projection = (
        project_snapshot_origins(snapshot)
        if _precomputed_projection is None
        else _precomputed_projection
    )
    if not isinstance(projection, SnapshotProjection):
        raise CountryImpactError("预计算 projection 类型非法")
    if projection.observed_at is None:  # pragma: no cover
        raise CountryImpactError("窗口快照缺少 observed_at")
    covered = dict(cohort.covered_snapshots)
    if covered.get(projection.snapshot_id) != projection.observed_at:
        raise CountryImpactError("当前快照不属于构建 cohort 时冻结的窗口")
    issues = _issues_effective_at(cohort, projection.observed_at)
    if projection.continuity_state != CONTINUOUS:
        return _unknown_impact(
            projection,
            cohort,
            UNKNOWN_STATE_GAP,
            "snapshot_state_gap",
            issues,
        )
    mapping_issues = tuple(
        issue for issue in issues if issue.value_state == UNKNOWN_MAPPING_VALUE
    )
    if mapping_issues:
        return _unknown_impact(
            projection,
            cohort,
            UNKNOWN_MAPPING_VALUE,
            "country_mapping_or_origin_unresolved",
            issues,
        )
    state_issues = tuple(
        issue for issue in issues if issue.value_state == UNKNOWN_STATE_GAP
    )
    if state_issues:
        return _unknown_impact(
            projection,
            cohort,
            UNKNOWN_STATE_GAP,
            "prior_state_gap",
            issues,
        )

    active_asns = _active_asns(cohort, projection.observed_at)
    active_set = set(active_asns)
    references = _active_references(cohort, projection.observed_at)
    reference_by_asn: Dict[int, Dict[str, set[str]]] = {
        asn: {"ipv4": set(), "ipv6": set()} for asn in active_asns
    }
    for reference in references:
        if reference.asn in active_set:
            reference_by_asn[reference.asn][reference.afi].add(reference.prefix)

    visible_by_asn: Dict[int, Dict[str, set[str]]] = {
        asn: {"ipv4": set(), "ipv6": set()} for asn in active_asns
    }
    country_prefixes = {"ipv4": set(), "ipv6": set()}
    moas_by_asn: Dict[int, Dict[str, set[str]]] = {
        asn: {"ipv4": set(), "ipv6": set()} for asn in active_asns
    }
    for relation in projection.relations:
        target_origins = active_set.intersection(relation.origins)
        if not target_origins:
            continue
        # 国家级前缀只计一次，不因 VP 数或 MOAS origin 数重复。
        country_prefixes[relation.afi].add(relation.prefix)
        for asn in target_origins:
            visible_by_asn[asn][relation.afi].add(relation.prefix)
            if relation.moas:
                moas_by_asn[asn][relation.afi].add(relation.prefix)

    visible_asns = []
    damaged_asns = []
    asn_impacts = []
    baseline_set = set(cohort.baseline_asns)
    dynamic_set = set(cohort.dynamic_asns)
    for asn in active_asns:
        family_impacts = []
        any_visible = False
        any_damaged = False
        fully_by_afi = {}
        for afi in _AFIS:
            reference_prefixes = tuple(sorted(reference_by_asn[asn][afi]))
            visible_prefixes = tuple(sorted(visible_by_asn[asn][afi]))
            lost_prefixes = tuple(
                sorted(set(reference_prefixes) - set(visible_prefixes))
            )
            if visible_prefixes:
                any_visible = True
            if lost_prefixes:
                any_damaged = True
            fully_invisible = bool(reference_prefixes) and not visible_prefixes
            fully_by_afi[afi] = fully_invisible
            family_impacts.append(
                AddressFamilyDamage(
                    afi=afi,
                    reference_prefixes=reference_prefixes,
                    visible_prefixes=visible_prefixes,
                    lost_prefixes=lost_prefixes,
                    lost_equivalent=_equivalent(lost_prefixes, afi),
                    fully_invisible=fully_invisible,
                    value_state=OBSERVED,
                    missing_reason=None,
                    moas_prefixes=tuple(sorted(moas_by_asn[asn][afi])),
                )
            )
        if any_visible:
            visible_asns.append(asn)
        if any_damaged:
            damaged_asns.append(asn)
        if not any_damaged:
            classification = "not_affected"
        elif fully_by_afi["ipv4"] and fully_by_afi["ipv6"]:
            classification = "dual_stack_fully_invisible"
        elif fully_by_afi["ipv4"]:
            classification = "ipv4_only_fully_invisible"
        elif fully_by_afi["ipv6"]:
            classification = "ipv6_only_fully_invisible"
        else:
            classification = "partially_visible"
        asn_impacts.append(
            AsnDamage(
                asn=asn,
                baseline_member=asn in baseline_set,
                dynamic_member=asn in dynamic_set,
                visible=any_visible,
                damaged=any_damaged,
                address_families=(family_impacts[0], family_impacts[1]),
                overall_classification=classification,
                value_state=OBSERVED,
                missing_reason=None,
            )
        )

    v4_union = _address_union(country_prefixes["ipv4"], "ipv4")
    metrics = CountryMetrics(
        visible_asn_count=_measure(projection.snapshot_id, len(visible_asns)),
        damaged_asn_count=_measure(projection.snapshot_id, len(damaged_asns)),
        baseline_asn_count=_measure(projection.snapshot_id, len(active_asns)),
        visible_ipv4_prefix_count=_measure(
            projection.snapshot_id, len(country_prefixes["ipv4"])
        ),
        visible_ipv6_prefix_count=_measure(
            projection.snapshot_id, len(country_prefixes["ipv6"])
        ),
        visible_ipv4_address_union=_measure(projection.snapshot_id, v4_union),
        visible_ipv4_24_equivalent=_measure(
            projection.snapshot_id,
            _equivalent(country_prefixes["ipv4"], "ipv4"),
        ),
        visible_ipv6_48_equivalent=_measure(
            projection.snapshot_id,
            _equivalent(country_prefixes["ipv6"], "ipv6"),
        ),
        damaged_asn_ratio=(
            SameSnapshotRatio(
                snapshot_id=projection.snapshot_id,
                numerator=len(damaged_asns),
                denominator=len(active_asns),
                value=len(damaged_asns) / len(active_asns),
                value_state=OBSERVED_ZERO if not damaged_asns else OBSERVED,
                missing_reason=None,
            )
            if active_asns
            else SameSnapshotRatio(
                snapshot_id=projection.snapshot_id,
                numerator=None,
                denominator=None,
                value=None,
                value_state=UNKNOWN_MAPPING_VALUE,
                missing_reason="empty_country_cohort",
            )
        ),
    )
    return CountrySnapshotImpact(
        snapshot_id=projection.snapshot_id,
        observed_at=projection.observed_at,
        country_code=cohort.country_code,
        cohort_view=cohort.cohort_view,
        continuity_state=projection.continuity_state,
        metrics=metrics,
        visible_asns=_asn_set(projection.snapshot_id, visible_asns),
        damaged_asns=_asn_set(projection.snapshot_id, damaged_asns),
        baseline_asns=_asn_set(projection.snapshot_id, active_asns),
        prefix_relations=projection.relations,
        asn_impacts=tuple(asn_impacts),
        issues=issues,
    )


def derive_country_cohort_and_impacts(
    baseline_snapshot: SnapshotLike,
    window_snapshots: Sequence[ReplaySnapshot],
    mapping: CountryMappingView,
) -> Tuple[CountryCohort, Tuple[CountrySnapshotImpact, ...]]:
    """用一次共享人口投影生成 cohort 与全部五分钟影响。"""

    if isinstance(window_snapshots, (str, bytes)) or not isinstance(
        window_snapshots, Sequence
    ):
        raise CountryImpactError("window_snapshots 必须是 ReplaySnapshot 序列")
    snapshots = tuple(window_snapshots)
    projections = project_snapshot_origins_series((baseline_snapshot, *snapshots))
    cohort = build_country_cohort(
        baseline_snapshot,
        snapshots,
        mapping,
        _precomputed_projections=projections,
    )
    impacts = tuple(
        compute_country_snapshot_impact(
            snapshot,
            cohort,
            _precomputed_projection=projection,
        )
        for snapshot, projection in zip(snapshots, projections[1:])
    )
    return cohort, impacts


__all__ = (
    "AddressFamilyDamage",
    "AsnDamage",
    "CohortIssue",
    "CohortPrefixReference",
    "CONFLICT",
    "CONFLICT_MAPPING",
    "CountryCohort",
    "CountryImpactError",
    "CountryMappingView",
    "CountryMetrics",
    "CountrySnapshotImpact",
    "MAPPED",
    "MOAS_SEMANTICS",
    "MappingAssignment",
    "MeasuredAsnSet",
    "MeasuredValue",
    "OriginIssue",
    "OriginResolution",
    "PrefixOriginRelation",
    "RESOLVED",
    "SameSnapshotRatio",
    "SnapshotProjection",
    "UNKNOWN",
    "UNKNOWN_MAPPING",
    "VpOriginObservation",
    "build_country_cohort",
    "build_country_mapping_view",
    "compute_country_snapshot_impact",
    "derive_country_cohort_and_impacts",
    "derive_origin_asns",
    "mapping_view_from_frozen_snapshot",
    "project_snapshot_origins",
    "project_snapshot_origins_series",
    "snapshot_id_v1",
    "snapshot_ids_v1",
)
