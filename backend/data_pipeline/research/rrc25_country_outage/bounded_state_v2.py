"""RRC25 伊朗有界重放的 cohort 冻结与逐槽状态投影。

本模块不读取 MRT、文件或数据库。它只消费确定性回放状态，并把同一
``collector + VP + AFI/SAFI + prefix`` 键上的当前路由，与 08:00 bview
冻结的人口逐项比较。动态出现的伊朗 origin 只单独报告，不改变基线分母。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from .bounded_event_v2 import (
    OBSERVATION_SCHEMA_VERSION,
    canonical_json,
    stable_id,
    validate_observation,
)
from .country_impact import CONFLICT, RESOLVED, UNKNOWN, derive_origin_asns
from .state_replay import (
    CONTINUOUS,
    RouteLastChange,
    RouteStateEntry,
    RouteStateKey,
)


COHORT_SCHEMA_VERSION = "rrc25-country-cohort/v2"
ASN_STATE_SCHEMA_VERSION = "rrc25-country-asn-state/v2"
ROUTE_STATE_SCHEMA_VERSION = "rrc25-country-route-state/v2"
CLASSIFICATION_VERSION = "rrc25_country_visibility_v2"
TargetMembership = Callable[[int], Optional[bool]]
_AFI_NAMES = {
    "ipv4_unicast": "ipv4",
    "ipv6_unicast": "ipv6",
}


class BoundedStateError(ValueError):
    """状态、cohort 或同快照投影不满足冻结口径。"""


def _utc(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BoundedStateError(f"{field_name} 必须是 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise BoundedStateError(f"{field_name} 不是合法 UTC 时间") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed) or parsed.microsecond:
        raise BoundedStateError(f"{field_name} 必须是秒级 UTC")
    canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical != value:
        raise BoundedStateError(f"{field_name} 不是规范 UTC 时间")
    return canonical


def _key_identity(key: RouteStateKey) -> dict[str, str]:
    return {
        "collector_id": key.collector_id,
        "vp_id": key.vp_id,
        "afi_safi": key.afi_safi,
        "prefix": key.prefix,
    }


def route_state_key_id(key: RouteStateKey) -> str:
    if not isinstance(key, RouteStateKey):
        raise BoundedStateError("route state key 类型非法")
    return stable_id("routekey_v2_", _key_identity(key))


def resolved_origin(entry: RouteStateEntry) -> tuple[str, Optional[int], str | None]:
    if not isinstance(entry, RouteStateEntry):
        raise BoundedStateError("route state entry 类型非法")
    resolution = derive_origin_asns(entry.as_path)
    if resolution.state == RESOLVED and len(resolution.origins) == 1:
        return RESOLVED, resolution.origins[0], None
    if resolution.state == CONFLICT:
        return CONFLICT, None, resolution.reason
    return UNKNOWN, None, resolution.reason


@dataclass(frozen=True)
class FrozenCohort:
    cohort_id: str
    collector_id: str
    country_code: str
    mapping_version: str
    seed_observed_at: str
    baseline_keys_by_asn_afi: Mapping[
        tuple[int, str], frozenset[RouteStateKey]
    ]
    baseline_prefixes_by_asn_afi: Mapping[
        tuple[int, str], frozenset[str]
    ]
    baseline_entry_by_key: Mapping[RouteStateKey, RouteStateEntry]
    quality: Mapping[str, Any]

    @property
    def baseline_asns(self) -> tuple[int, ...]:
        return tuple(
            sorted({asn for asn, _afi in self.baseline_keys_by_asn_afi})
        )

    @property
    def baseline_key_count(self) -> int:
        return len(self.baseline_entry_by_key)

    def to_json(self) -> dict[str, Any]:
        families: dict[str, dict[str, Any]] = {}
        for afi in ("ipv4", "ipv6"):
            asns = sorted(
                asn
                for asn, family in self.baseline_keys_by_asn_afi
                if family == afi
            )
            prefixes = sorted(
                {
                    prefix
                    for (asn, family), values
                    in self.baseline_prefixes_by_asn_afi.items()
                    if family == afi
                    for prefix in values
                }
            )
            families[afi] = {
                "origin_asns": asns,
                "origin_asn_count": len(asns),
                "prefixes": prefixes,
                "prefix_count": len(prefixes),
                "prefix_vp_count": sum(
                    len(values)
                    for (asn, family), values
                    in self.baseline_keys_by_asn_afi.items()
                    if family == afi
                ),
            }
        members = []
        for (asn, afi), keys in sorted(self.baseline_keys_by_asn_afi.items()):
            members.append(
                {
                    "asn": asn,
                    "afi": afi,
                    "prefixes": sorted(
                        self.baseline_prefixes_by_asn_afi[(asn, afi)]
                    ),
                    "prefix_count": len(
                        self.baseline_prefixes_by_asn_afi[(asn, afi)]
                    ),
                    "prefix_vp_count": len(keys),
                    "route_state_key_ids": sorted(
                        route_state_key_id(key) for key in keys
                    ),
                }
            )
        return {
            "schema_version": COHORT_SCHEMA_VERSION,
            "cohort_id": self.cohort_id,
            "collector_id": self.collector_id,
            "country_code": self.country_code,
            "mapping_version": self.mapping_version,
            "source": "state_seed_rib",
            "seed_observed_at": self.seed_observed_at,
            "denominator_policy": (
                "fixed_seed_definite_ir_origin_dynamic_reported_separately"
            ),
            "baseline_origin_asns": list(self.baseline_asns),
            "baseline_origin_asn_count": len(self.baseline_asns),
            "baseline_prefix_vp_count": self.baseline_key_count,
            "address_families": families,
            "members": members,
            "quality": json.loads(canonical_json(dict(self.quality))),
        }


def freeze_ir_cohort(
    entries: Iterable[RouteStateEntry],
    *,
    target_membership: TargetMembership,
    mapping_version: str,
    seed_observed_at: str,
    collector_id: str = "rrc25",
    country_code: str = "IR",
) -> FrozenCohort:
    """从 seed RIB 的确定 IR origin 路由冻结 Prefix×VP 人口。"""

    if not callable(target_membership):
        raise BoundedStateError("target_membership 必须可调用")
    if not isinstance(mapping_version, str) or not mapping_version:
        raise BoundedStateError("mapping_version 不能为空")
    seed_time = _utc(seed_observed_at, "seed_observed_at")
    values = tuple(entries)
    if any(not isinstance(entry, RouteStateEntry) for entry in values):
        raise BoundedStateError("entries 只能包含 RouteStateEntry")
    selected: dict[RouteStateKey, RouteStateEntry] = {}
    unknown_origin = 0
    conflict_origin = 0
    mapping_unknown = 0
    mapping_conflict_or_unknown_asns: set[int] = set()
    non_target = 0
    for entry in values:
        if entry.key.collector_id != collector_id:
            raise BoundedStateError("seed state 混入其他 collector")
        state, origin, _reason = resolved_origin(entry)
        if state == UNKNOWN:
            unknown_origin += 1
            continue
        if state == CONFLICT:
            conflict_origin += 1
            continue
        assert origin is not None
        membership = target_membership(origin)
        if membership is True:
            selected[entry.key] = entry
        elif membership is None:
            mapping_unknown += 1
            mapping_conflict_or_unknown_asns.add(origin)
        else:
            non_target += 1
    if not selected:
        raise BoundedStateError("seed RIB 未得到任何确定 IR Prefix×VP")

    by_asn_afi: dict[tuple[int, str], set[RouteStateKey]] = {}
    prefixes: dict[tuple[int, str], set[str]] = {}
    for key, entry in selected.items():
        state, origin, _reason = resolved_origin(entry)
        if state != RESOLVED or origin is None:  # pragma: no cover
            raise BoundedStateError("cohort 内部 origin 不再可解析")
        afi = _AFI_NAMES.get(key.afi_safi)
        if afi is None:
            raise BoundedStateError("cohort 出现非单播 IPv4/IPv6")
        by_asn_afi.setdefault((origin, afi), set()).add(key)
        prefixes.setdefault((origin, afi), set()).add(key.prefix)

    identity = {
        "collector_id": collector_id,
        "country_code": country_code,
        "mapping_version": mapping_version,
        "seed_observed_at": seed_time,
        "members": [
            {
                "asn": asn,
                "afi": afi,
                "route_state_key_ids": sorted(
                    route_state_key_id(key) for key in keys
                ),
            }
            for (asn, afi), keys in sorted(by_asn_afi.items())
        ],
    }
    cohort_id = stable_id("cohort_v2_", identity)
    quality = {
        "completeness_state": (
            "known_population_with_explicit_unknown_exclusions"
        ),
        "unknown_origin_route_count": unknown_origin,
        "conflict_origin_route_count": conflict_origin,
        "mapping_unknown_or_conflict_route_count": mapping_unknown,
        "mapping_unknown_or_conflict_asns": sorted(
            mapping_conflict_or_unknown_asns
        ),
        "explicit_non_target_route_count": non_target,
    }
    return FrozenCohort(
        cohort_id=cohort_id,
        collector_id=collector_id,
        country_code=country_code,
        mapping_version=mapping_version,
        seed_observed_at=seed_time,
        baseline_keys_by_asn_afi={
            key: frozenset(value) for key, value in by_asn_afi.items()
        },
        baseline_prefixes_by_asn_afi={
            key: frozenset(value) for key, value in prefixes.items()
        },
        baseline_entry_by_key=dict(selected),
        quality=quality,
    )


def _current_origin_by_key(
    entries: Iterable[RouteStateEntry],
) -> tuple[
    dict[RouteStateKey, Optional[int]],
    dict[RouteStateKey, RouteStateEntry],
]:
    origins: dict[RouteStateKey, Optional[int]] = {}
    entry_by_key: dict[RouteStateKey, RouteStateEntry] = {}
    for entry in entries:
        if not isinstance(entry, RouteStateEntry):
            raise BoundedStateError("state entries 类型非法")
        if entry.key in entry_by_key:
            raise BoundedStateError("state entries 出现重复键")
        state, origin, _reason = resolved_origin(entry)
        origins[entry.key] = origin if state == RESOLVED else None
        entry_by_key[entry.key] = entry
    return origins, entry_by_key


def _family_state(
    cohort: FrozenCohort,
    current_origins: Mapping[RouteStateKey, Optional[int]],
    *,
    afi: str,
    continuity_state: str,
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    baseline_asns = sorted(
        asn
        for asn, family in cohort.baseline_keys_by_asn_afi
        if family == afi
    )
    classifications = {
        "fully_visible": [],
        "partially_visible": [],
        "fully_invisible": [],
        "unknown": [],
    }
    details: dict[int, dict[str, Any]] = {}
    for asn in baseline_asns:
        keys = cohort.baseline_keys_by_asn_afi[(asn, afi)]
        baseline_prefixes = cohort.baseline_prefixes_by_asn_afi[(asn, afi)]
        if continuity_state != CONTINUOUS:
            visible_keys: set[RouteStateKey] = set()
            current_prefixes: set[str] = set()
            classification = "unknown"
        else:
            visible_keys = {
                key for key in keys if current_origins.get(key) == asn
            }
            current_prefixes = {key.prefix for key in visible_keys}
            if len(visible_keys) == len(keys):
                classification = "fully_visible"
            elif visible_keys:
                classification = "partially_visible"
            else:
                classification = "fully_invisible"
        classifications[classification].append(asn)
        details[asn] = {
            "baseline_prefix_count": len(baseline_prefixes),
            "visible_prefix_count": (
                None if continuity_state != CONTINUOUS else len(current_prefixes)
            ),
            "baseline_prefix_vp_count": len(keys),
            "visible_prefix_vp_count": (
                None if continuity_state != CONTINUOUS else len(visible_keys)
            ),
            "classification": classification,
            "visible_prefixes": sorted(current_prefixes),
        }
    visible_asns = sorted(
        classifications["fully_visible"] + classifications["partially_visible"]
    )
    return (
        {
            "baseline_origin_asns": baseline_asns,
            "visible_origin_asns": visible_asns,
            "classifications": classifications,
        },
        details,
    )


@dataclass
class AsnMilestoneTracker:
    first_damaged_at: dict[tuple[int, str], str] = field(default_factory=dict)
    first_fully_invisible_at: dict[tuple[int, str], str] = field(
        default_factory=dict
    )
    first_recovered_at: dict[tuple[int, str], str] = field(default_factory=dict)
    _ever_damaged: set[tuple[int, str]] = field(default_factory=set)

    def update(
        self,
        *,
        asn: int,
        afi: str,
        classification: str,
        observed_at: str,
    ) -> dict[str, Any]:
        key = (asn, afi)
        if classification in {"partially_visible", "fully_invisible"}:
            self._ever_damaged.add(key)
            self.first_damaged_at.setdefault(key, observed_at)
        if classification == "fully_invisible":
            self.first_fully_invisible_at.setdefault(key, observed_at)
        if classification == "fully_visible" and key in self._ever_damaged:
            self.first_recovered_at.setdefault(key, observed_at)
        return {
            "first_damaged_at": self.first_damaged_at.get(key),
            "first_fully_invisible_at": self.first_fully_invisible_at.get(key),
            "first_recovered_at": self.first_recovered_at.get(key),
            "is_recovered_at_snapshot": (
                classification == "fully_visible" and key in self._ever_damaged
            ),
        }


def project_country_snapshot(
    entries: Sequence[RouteStateEntry],
    *,
    cohort: FrozenCohort,
    target_membership: TargetMembership,
    observed_at: str,
    slot_start_utc: str,
    slot_end_exclusive_utc: str,
    slot_role: str,
    continuity_state: str,
    update_counts: Mapping[str, int],
    milestone_tracker: Optional[AsnMilestoneTracker] = None,
    slot_changes: Sequence[RouteLastChange] = (),
    latest_changes: Sequence[RouteLastChange] = (),
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """生成同 snapshot 的国家、ASN 与 RouteState 投影。"""

    observed = _utc(observed_at, "observed_at")
    start = _utc(slot_start_utc, "slot_start_utc")
    end = _utc(slot_end_exclusive_utc, "slot_end_exclusive_utc")
    if slot_role not in {"window_start", "slot_end"}:
        raise BoundedStateError("slot_role 非法")
    expected_update_fields = {
        "announce",
        "withdraw",
        "retained_announce",
        "retained_withdraw",
    }
    if (
        set(update_counts) != expected_update_fields
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in update_counts.values()
        )
    ):
        raise BoundedStateError("update_counts 字段或数值非法")
    if any(not isinstance(change, RouteLastChange) for change in slot_changes):
        raise BoundedStateError("slot_changes 类型非法")
    if any(not isinstance(change, RouteLastChange) for change in latest_changes):
        raise BoundedStateError("latest_changes 类型非法")
    latest_by_key = {change.key: change for change in latest_changes}
    if len(latest_by_key) != len(latest_changes):
        raise BoundedStateError("latest_changes 出现重复状态键")

    current_origins, entry_by_key = _current_origin_by_key(entries)
    family_payload: dict[str, dict[str, Any]] = {}
    family_details: dict[str, dict[int, dict[str, Any]]] = {}
    for afi in ("ipv4", "ipv6"):
        payload, details = _family_state(
            cohort,
            current_origins,
            afi=afi,
            continuity_state=continuity_state,
        )
        family_payload[afi] = payload
        family_details[afi] = details

    baseline_asns = list(cohort.baseline_asns)
    dual_classes = {
        "fully_visible": [],
        "partially_visible": [],
        "fully_invisible": [],
        "ipv4_invisible_ipv6_visible": [],
        "unknown": [],
    }
    for asn in baseline_asns:
        applicable = [
            family_details[afi][asn]["classification"]
            for afi in ("ipv4", "ipv6")
            if asn in family_details[afi]
        ]
        if not applicable or "unknown" in applicable:
            classification = "unknown"
        elif all(value == "fully_visible" for value in applicable):
            classification = "fully_visible"
        elif all(value == "fully_invisible" for value in applicable):
            classification = "fully_invisible"
        else:
            classification = "partially_visible"
        dual_classes[classification].append(asn)
        if (
            asn in family_details["ipv4"]
            and asn in family_details["ipv6"]
            and family_details["ipv4"][asn]["classification"]
            == "fully_invisible"
            and family_details["ipv6"][asn]["classification"]
            in {"fully_visible", "partially_visible"}
        ):
            dual_classes["ipv4_invisible_ipv6_visible"].append(asn)

    dynamic_by_afi = {"ipv4": set(), "ipv6": set()}
    dynamic_prefixes: dict[str, set[str]] = {"ipv4": set(), "ipv6": set()}
    baseline_asn_set = set(baseline_asns)
    for key, origin in current_origins.items():
        if origin is None or origin in baseline_asn_set:
            continue
        if target_membership(origin) is True:
            afi = _AFI_NAMES[key.afi_safi]
            dynamic_by_afi[afi].add(origin)
            dynamic_prefixes[afi].add(key.prefix)

    snapshot_id = stable_id(
        "snapshot_v2_",
        {
            "cohort_id": cohort.cohort_id,
            "observed_at": observed,
            "continuity_state": continuity_state,
        },
    )
    for afi in ("ipv4", "ipv6"):
        family_payload[afi]["visible_prefixes_ref"] = {
            "path": "route-states.jsonl.gz",
            "snapshot_id": snapshot_id,
            "afi": afi,
        }

    if continuity_state == CONTINUOUS:
        visible_asns = sorted(
            dual_classes["fully_visible"] + dual_classes["partially_visible"]
        )
        affected_asns = sorted(
            dual_classes["partially_visible"] + dual_classes["fully_invisible"]
        )
        visible_key_count = sum(
            1
            for key, baseline_entry in cohort.baseline_entry_by_key.items()
            if current_origins.get(key)
            == resolved_origin(baseline_entry)[1]
        )
        affected_count: Optional[int] = len(affected_asns)
        visible_asn_count: Optional[int] = len(visible_asns)
        affected_ratio: Optional[float] = (
            affected_count / len(baseline_asns) if baseline_asns else 0.0
        )
        visible_asn_ratio: Optional[float] = (
            visible_asn_count / len(baseline_asns) if baseline_asns else 1.0
        )
        lost_key_count: Optional[int] = (
            cohort.baseline_key_count - visible_key_count
        )
        prefix_vp_ratio: Optional[float] = (
            visible_key_count / cohort.baseline_key_count
            if cohort.baseline_key_count
            else 1.0
        )
    else:
        visible_asns = []
        affected_asns = []
        affected_count = None
        visible_asn_count = None
        affected_ratio = None
        visible_asn_ratio = None
        visible_key_count = None
        lost_key_count = None
        prefix_vp_ratio = None
        for afi in ("ipv4", "ipv6"):
            family_payload[afi]["visible_origin_asns"] = []
        dual_classes = {
            "fully_visible": [],
            "partially_visible": [],
            "fully_invisible": [],
            "ipv4_invisible_ipv6_visible": [],
            "unknown": baseline_asns,
        }

    observation = {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "observed_at": observed,
        "slot": {
            "start_utc": start,
            "end_exclusive_utc": end,
            "boundary": "[start,end)",
            "role": slot_role,
        },
        "continuity_state": continuity_state,
        "cohort": {
            "cohort_id": cohort.cohort_id,
            "baseline_asn_count": len(baseline_asns),
            "baseline_prefix_vp_count": cohort.baseline_key_count,
            "mapping_version": cohort.mapping_version,
            "completeness_state": cohort.quality["completeness_state"],
        },
        "address_families": family_payload,
        "dual_stack": {
            "baseline_origin_asns": baseline_asns,
            "visible_origin_asns": visible_asns,
            "affected_asns": affected_asns,
            "classifications": dual_classes,
        },
        "dynamic": {
            "denominator_policy": "reported_separately",
            "ipv4_visible_origin_asns": sorted(dynamic_by_afi["ipv4"]),
            "ipv6_visible_origin_asns": sorted(dynamic_by_afi["ipv6"]),
            "dual_stack_visible_origin_asns": sorted(
                dynamic_by_afi["ipv4"] | dynamic_by_afi["ipv6"]
            ),
            "visible_prefixes_ref": {
                "path": "route-states.jsonl.gz",
                "snapshot_id": snapshot_id,
            },
        },
        "prefix_vp": {
            "baseline_count": cohort.baseline_key_count,
            "visible_count": visible_key_count,
            "lost_count": lost_key_count,
            "visible_ratio": prefix_vp_ratio,
        },
        "metrics": {
            "affected_asn_count": affected_count,
            "affected_asn_ratio": affected_ratio,
            "visible_origin_asn_count": visible_asn_count,
            "visible_origin_asn_ratio": visible_asn_ratio,
        },
        "update_counts": dict(update_counts),
        "state_result_ref": {
            "path": "route-states.jsonl.gz",
            "format": "route_state_snapshots_jsonl_gzip",
            "snapshot_id": snapshot_id,
        },
    }
    validated = validate_observation(observation)

    tracker = milestone_tracker or AsnMilestoneTracker()
    asn_rows: list[dict[str, Any]] = []
    for afi in ("ipv4", "ipv6"):
        for asn, detail in sorted(family_details[afi].items()):
            milestones = tracker.update(
                asn=asn,
                afi=afi,
                classification=detail["classification"],
                observed_at=observed,
            )
            asn_rows.append(
                {
                    "schema_version": ASN_STATE_SCHEMA_VERSION,
                    "snapshot_id": snapshot_id,
                    "observed_at": observed,
                    "cohort_id": cohort.cohort_id,
                    "asn": asn,
                    "afi": afi,
                    **{
                        key: detail[key]
                        for key in (
                            "baseline_prefix_count",
                            "visible_prefix_count",
                            "baseline_prefix_vp_count",
                            "visible_prefix_vp_count",
                            "classification",
                        )
                    },
                    **milestones,
                    "classification_algorithm_version": CLASSIFICATION_VERSION,
                    "supporting_changes": [
                        {
                            "action": change.action,
                            "event_time_utc": change.event_time_utc,
                            "artifact_id": change.raw_ref.artifact_id,
                            "file_sha256": change.raw_ref.file_sha256,
                            "artifact_slot_utc": (
                                change.raw_ref.artifact_slot_utc
                            ),
                            "route_event_id": change.raw_ref.route_event_id,
                            "prefix": change.key.prefix,
                            "vp_id": change.key.vp_id,
                        }
                        for change in slot_changes
                        if change.key
                        in cohort.baseline_keys_by_asn_afi[(asn, afi)]
                    ],
                    "state_result_ref": {
                        "path": "route-states.jsonl.gz",
                        "snapshot_id": snapshot_id,
                        "asn": asn,
                        "afi": afi,
                    },
                }
            )

    route_rows: list[dict[str, Any]] = []
    interesting_keys = set(cohort.baseline_entry_by_key)
    for key, origin in current_origins.items():
        if origin is not None and target_membership(origin) is True:
            interesting_keys.add(key)
    for key in sorted(interesting_keys):
        entry = entry_by_key.get(key)
        latest = latest_by_key.get(key)
        origin = current_origins.get(key)
        baseline_entry = cohort.baseline_entry_by_key.get(key)
        baseline_origin = (
            None if baseline_entry is None else resolved_origin(baseline_entry)[1]
        )
        route_rows.append(
            {
                "schema_version": ROUTE_STATE_SCHEMA_VERSION,
                "snapshot_id": snapshot_id,
                "observed_at": observed,
                "route_state_key_id": route_state_key_id(key),
                **_key_identity(key),
                "visible": entry is not None,
                "current_origin_asn": origin,
                "baseline_origin_asn": baseline_origin,
                "baseline_member_visible": (
                    baseline_origin is not None and origin == baseline_origin
                ),
                "population_role": (
                    "baseline" if baseline_entry is not None else "dynamic"
                ),
                "last_action": (
                    latest.action
                    if latest is not None
                    else (None if entry is None else entry.last_action)
                ),
                "last_event_time_utc": (
                    latest.event_time_utc
                    if latest is not None
                    else (None if entry is None else entry.last_event_time_utc)
                ),
                "last_source": (
                    None
                    if latest is None and entry is None
                    else {
                        "artifact_id": (
                            latest.raw_ref.artifact_id
                            if latest is not None
                            else entry.last_raw_ref.artifact_id
                        ),
                        "file_sha256": (
                            latest.raw_ref.file_sha256
                            if latest is not None
                            else entry.last_raw_ref.file_sha256
                        ),
                        "artifact_slot_utc": (
                            latest.raw_ref.artifact_slot_utc
                            if latest is not None
                            else entry.last_raw_ref.artifact_slot_utc
                        ),
                        "record_ordinal": (
                            latest.raw_ref.record_ordinal
                            if latest is not None
                            else entry.last_raw_ref.record_ordinal
                        ),
                        "element_ordinal": (
                            latest.raw_ref.element_ordinal
                            if latest is not None
                            else entry.last_raw_ref.element_ordinal
                        ),
                        "route_event_id": (
                            latest.raw_ref.route_event_id
                            if latest is not None
                            else entry.last_raw_ref.route_event_id
                        ),
                    }
                ),
            }
        )
    return validated, asn_rows, route_rows


def summarize_slot_changes(
    changes: Iterable[RouteLastChange],
) -> dict[str, int]:
    counts = {
        "retained_announce": 0,
        "retained_withdraw": 0,
    }
    for change in changes:
        if not isinstance(change, RouteLastChange):
            raise BoundedStateError("slot changes 类型非法")
        if change.action == "announce":
            counts["retained_announce"] += 1
        elif change.action == "withdraw":
            counts["retained_withdraw"] += 1
    return counts


def normal_band_from_catch_up(
    ratios: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """用 catch-up 的中位数与 MAD 冻结双指标正常波动带。"""

    if not ratios:
        return None
    metric_names = (
        "visible_origin_asn_ratio",
        "visible_prefix_vp_ratio",
    )
    result: dict[str, Any] = {}
    for metric in metric_names:
        values = []
        for row in ratios:
            value = row.get(metric)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= float(value) <= 1
            ):
                return None
            values.append(float(value))
        ordered = sorted(values)
        middle = len(ordered) // 2
        median = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / 2
        )
        deviations = sorted(abs(value - median) for value in values)
        mad = (
            deviations[middle]
            if len(deviations) % 2
            else (deviations[middle - 1] + deviations[middle]) / 2
        )
        half_width = max(3 * mad, 0.001)
        result[metric] = {
            "lower": max(0.0, median - half_width),
            "upper": min(1.0, median + half_width),
            "median": median,
            "mad": mad,
            "sample_count": len(values),
        }
    return result


__all__ = (
    "ASN_STATE_SCHEMA_VERSION",
    "AsnMilestoneTracker",
    "BoundedStateError",
    "CLASSIFICATION_VERSION",
    "COHORT_SCHEMA_VERSION",
    "FrozenCohort",
    "ROUTE_STATE_SCHEMA_VERSION",
    "freeze_ir_cohort",
    "normal_band_from_catch_up",
    "project_country_snapshot",
    "resolved_origin",
    "route_state_key_id",
    "summarize_slot_changes",
)
