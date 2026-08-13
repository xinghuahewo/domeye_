#!/usr/bin/env python3
"""离线构建 RRC25 P2-S1 六种原子 source view。

本模块只做 source projection/materialization，不执行任何调查 Tool、排序、Join 或结论生成。
输入必须是带权威摘要的离线投影包；TOOL-10 的固定方向人口由本模块按冻结 Profile 投影，
TOOL-11 只验收上游离线 replay 已生成的 exact rows，查询期不允许回放。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SHA256_RE = set("0123456789abcdef")
POPULATION_IDS = (
    "fixed_cohort_member_rows",
    "prefix_state_rows",
    "asn_state_rows",
    "new_prefix_state_rows",
    "materialized_route_state_rows_at_exact_time",
    "window_path_association_evidence_rows",
)
SCHEMA_FILES = {
    "fixed_cohort_member_rows": "fixed-cohort-member-row.schema.json",
    "prefix_state_rows": "prefix-state-row.schema.json",
    "asn_state_rows": "asn-state-row.schema.json",
    "new_prefix_state_rows": "new-prefix-state-row.schema.json",
    "materialized_route_state_rows_at_exact_time": "materialized-route-state-row.schema.json",
    "window_path_association_evidence_rows": "window-path-association-row.schema.json",
}
MEMBER_KEY_FIELDS = {
    "fixed_cohort_member_rows": ("publication_id", "cohort_id", "cohort_member_id"),
    "prefix_state_rows": ("publication_id", "state_point_utc", "prefix", "afi"),
    "asn_state_rows": ("publication_id", "state_point_utc", "asn"),
    "new_prefix_state_rows": ("publication_id", "new_prefix_state_id", "state_point_utc"),
    "materialized_route_state_rows_at_exact_time": (
        "publication_id", "state_point_utc", "route_observation_key"
    ),
    "window_path_association_evidence_rows": ("publication_id", "path_association_id"),
}
ROW_SCHEMA_VERSIONS = {
    "fixed_cohort_member_rows": "country_outage_p2_s1_fixed_cohort_member_row_v1",
    "prefix_state_rows": "country_outage_p2_s1_prefix_state_row_v1",
    "asn_state_rows": "country_outage_p2_s1_asn_state_row_v1",
    "new_prefix_state_rows": "country_outage_p2_s1_new_prefix_state_row_v1",
    "materialized_route_state_rows_at_exact_time": "country_outage_p2_s1_materialized_route_state_row_v1",
    "window_path_association_evidence_rows": "country_outage_p2_s1_window_path_association_row_v1",
}
NEW_PREFIX_PROFILE_ID = "PROFILE-NEW-PREFIX-FIXED-FIRST-OBSERVED-DIRECTIONS-1.0.0"
EXACT_ROUTE_PROFILE_ID = "PROFILE-EXACT-ROUTE-STATE-AUTHORITATIVE-INPUT-1.0.0"
PATH_PROFILE_ID = "AS-PATH-CANONICALIZATION-1.0.0"
PATH_PROFILE_DIGEST = "eb4d2081ee69ab0254b7af461122cf315b6bcdf24551c22de7e8dccc6d965966"
PATH_MEMBERSHIP_PROFILE_ID = "PROFILE-PATH-ASN-MEMBERSHIP-1.0.0"
PATH_MEMBERSHIP_PROFILE_DIGEST = "28acec6edd232fd9aa38885175bcd715b9ea72f240efca6b3c5b7080394655e2"
WINDOW_FILTER_PROFILE_ID = "PROFILE-WINDOW-PATH-ASSOCIATION-FILTER-1.0.0"
WINDOW_FILTER_PROFILE_DIGEST = "46ca0955b30a4d43088c214ec5bdf84fbf9b65987bd65047257e85e1d7778eb7"


class SourceMaterializationError(ValueError):
    """输入不能证明 source view 语义或完整性。"""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _no_duplicate_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SourceMaterializationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def load_json_strict(path: Path) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceMaterializationError(f"invalid JSON input {path}: {exc}") from exc
    _reject_non_json_types(value, "$")
    return value


def _reject_non_json_types(value: Any, location: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SourceMaterializationError(f"non-finite number at {location}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_non_json_types(item, f"{location}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SourceMaterializationError(f"non-string key at {location}")
            _reject_non_json_types(item, f"{location}.{key}")
        return
    raise SourceMaterializationError(f"non-JSON type at {location}: {type(value).__name__}")


def _require_mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SourceMaterializationError(f"{location} must be object")
    return value


def _require_list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise SourceMaterializationError(f"{location} must be array")
    return value


def _require_exact_keys(value: Mapping[str, Any], required: Iterable[str], location: str) -> None:
    required_set = set(required)
    actual = set(value)
    if actual != required_set:
        missing = sorted(required_set - actual)
        extra = sorted(actual - required_set)
        raise SourceMaterializationError(f"{location} keys mismatch missing={missing} extra={extra}")


def _string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise SourceMaterializationError(f"{location} must be non-empty string")
    return value


def _integer(value: Any, location: str, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SourceMaterializationError(f"{location} must be integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise SourceMaterializationError(f"{location} exceeds {maximum}")
    return value


def _sha(value: Any, location: str) -> str:
    result = _string(value, location)
    if len(result) != 64 or any(char not in SHA256_RE for char in result):
        raise SourceMaterializationError(f"{location} must be lowercase sha256")
    return result


def _utc(value: Any, location: str) -> dt.datetime:
    raw = _string(value, location)
    try:
        parsed = dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise SourceMaterializationError(f"{location} must be whole-second UTC") from exc
    return parsed


def _prefix(value: Any, afi: Any, location: str) -> tuple[str, str]:
    raw = _string(value, f"{location}.prefix")
    family = _string(afi, f"{location}.afi")
    if family not in ("ipv4", "ipv6"):
        raise SourceMaterializationError(f"{location}.afi invalid")
    try:
        network = ipaddress.ip_network(raw, strict=True)
    except ValueError as exc:
        raise SourceMaterializationError(f"{location}.prefix must be canonical network") from exc
    if (network.version == 4) != (family == "ipv4"):
        raise SourceMaterializationError(f"{location} prefix/afi mismatch")
    return str(network), family


def _unique_sorted_strings(value: Any, location: str, *, nonempty: bool = False) -> list[str]:
    items = [_string(item, f"{location}[]") for item in _require_list(value, location)]
    if len(set(items)) != len(items) or (nonempty and not items):
        raise SourceMaterializationError(f"{location} must be unique{' and non-empty' if nonempty else ''}")
    return sorted(items)


def _unique_sorted_asns(value: Any, location: str, *, nonempty: bool = False) -> list[int]:
    items = [_integer(item, f"{location}[]", maximum=4294967295) for item in _require_list(value, location)]
    if len(set(items)) != len(items) or (nonempty and not items):
        raise SourceMaterializationError(f"{location} must be unique{' and non-empty' if nonempty else ''}")
    return sorted(items)


def _load_profiles(contract_root: Path) -> tuple[dict[str, Mapping[str, Any]], bytes]:
    path = contract_root / "source-profiles.json"
    payload = _require_mapping(load_json_strict(path), "source_profiles")
    _require_exact_keys(payload, ("schema_version", "profiles"), "source_profiles")
    if payload["schema_version"] != "country_outage_p2_s1_source_profiles_v1":
        raise SourceMaterializationError("source profile schema mismatch")
    profiles: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(_require_list(payload["profiles"], "source_profiles.profiles")):
        profile = _require_mapping(raw, f"source_profiles.profiles[{index}]")
        profile_id = _string(profile.get("profile_id"), "profile_id")
        profile_digest = _sha(profile.get("profile_digest"), f"{profile_id}.profile_digest")
        semantic = dict(profile)
        semantic.pop("profile_digest")
        if digest_json(semantic) != profile_digest:
            raise SourceMaterializationError(f"source profile digest mismatch: {profile_id}")
        profiles[profile_id] = profile
    return profiles, (canonical_json(payload) + "\n").encode("utf-8")


def _validate_identity(raw: Any) -> dict[str, Any]:
    value = _require_mapping(raw, "identity")
    keys = ("incident_id", "publication_id", "revision", "collector_id", "cohort_id", "window_start_utc", "data_through", "grid_seconds")
    _require_exact_keys(value, keys, "identity")
    result = {
        "incident_id": _string(value["incident_id"], "identity.incident_id"),
        "publication_id": _string(value["publication_id"], "identity.publication_id"),
        "revision": _integer(value["revision"], "identity.revision", minimum=1),
        "collector_id": _string(value["collector_id"], "identity.collector_id"),
        "cohort_id": _string(value["cohort_id"], "identity.cohort_id"),
        "window_start_utc": _string(value["window_start_utc"], "identity.window_start_utc"),
        "data_through": _string(value["data_through"], "identity.data_through"),
        "grid_seconds": _integer(value["grid_seconds"], "identity.grid_seconds", minimum=1),
    }
    if result["collector_id"] != "rrc25":
        raise SourceMaterializationError("collector_id must be rrc25")
    if _utc(result["window_start_utc"], "identity.window_start_utc") > _utc(result["data_through"], "identity.data_through"):
        raise SourceMaterializationError("identity window is inverted")
    return result


def _validate_source_refs(raw: Any, publication_id: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    keys = ("source_kind", "dataset_id", "manifest_sha256", "content_sha256", "publication_id", "authority")
    for index, item in enumerate(_require_list(raw, "source_refs")):
        value = _require_mapping(item, f"source_refs[{index}]")
        _require_exact_keys(value, keys, f"source_refs[{index}]")
        ref = {key: _string(value[key], f"source_refs[{index}].{key}") for key in keys}
        _sha(ref["manifest_sha256"], "manifest_sha256")
        _sha(ref["content_sha256"], "content_sha256")
        if ref["publication_id"] != publication_id:
            raise SourceMaterializationError("source ref publication mismatch")
        if ref["authority"] not in ("rrc25_authoritative_artifact", "rrc25_authoritative_offline_projection"):
            raise SourceMaterializationError("source ref authority invalid")
        result.append(ref)
    if not result:
        raise SourceMaterializationError("source_refs must not be empty")
    identities = [(item["source_kind"], item["dataset_id"]) for item in result]
    if len(set(identities)) != len(identities):
        raise SourceMaterializationError("source_refs contain duplicate identities")
    return sorted(result, key=lambda item: (item["source_kind"], item["dataset_id"]))


def _audit_refs(raw: Any, location: str) -> list[str]:
    return _unique_sorted_strings(raw, location, nonempty=True)


def _fixed_rows(raw: Any, identity: Mapping[str, Any]) -> list[dict[str, Any]]:
    keys = ("cohort_member_id", "prefix", "afi", "country_origin_asns", "expected_peer_asn_direction_ids", "expected_route_observation_keys", "membership_basis", "source_record_refs")
    result = []
    for index, item in enumerate(_require_list(raw, "fixed_cohort_members")):
        value = _require_mapping(item, f"fixed_cohort_members[{index}]")
        _require_exact_keys(value, keys, f"fixed_cohort_members[{index}]")
        prefix, afi = _prefix(value["prefix"], value["afi"], f"fixed_cohort_members[{index}]")
        directions = _unique_sorted_strings(value["expected_peer_asn_direction_ids"], "expected_peer_asn_direction_ids", nonempty=True)
        observations = _unique_sorted_strings(value["expected_route_observation_keys"], "expected_route_observation_keys", nonempty=True)
        if len(directions) > len(observations):
            raise SourceMaterializationError("expected directions cannot exceed route observation keys")
        basis = _string(value["membership_basis"], "membership_basis")
        if basis not in ("country_origin_known", "country_origin_moas", "country_origin_ambiguous"):
            raise SourceMaterializationError("membership_basis invalid")
        result.append({
            "publication_id": identity["publication_id"], "cohort_id": identity["cohort_id"],
            "cohort_member_id": _string(value["cohort_member_id"], "cohort_member_id"), "prefix": prefix, "afi": afi,
            "country_origin_asns": _unique_sorted_asns(value["country_origin_asns"], "country_origin_asns", nonempty=True),
            "expected_peer_asn_direction_ids": directions, "expected_route_observation_keys": observations,
            "membership_basis": basis, "source_record_refs": _audit_refs(value["source_record_refs"], "source_record_refs"),
        })
    return result


def _prefix_rows(raw: Any, identity: Mapping[str, Any]) -> list[dict[str, Any]]:
    keys = ("state_point_utc", "prefix", "afi", "classification", "expected_direction_count", "visible_direction_count", "invisible_direction_count", "unknown_direction_count", "source_record_refs")
    result = []
    for index, item in enumerate(_require_list(raw, "prefix_states")):
        value = _require_mapping(item, f"prefix_states[{index}]")
        _require_exact_keys(value, keys, f"prefix_states[{index}]")
        prefix, afi = _prefix(value["prefix"], value["afi"], f"prefix_states[{index}]")
        state = _string(value["state_point_utc"], "state_point_utc")
        _validate_grid_time(state, identity, "state_point_utc")
        counts = [_integer(value[key], key) for key in keys[4:8]]
        expected, visible, invisible, unknown = counts
        if visible + invisible + unknown != expected:
            raise SourceMaterializationError("prefix direction counts do not reconcile")
        classification = _string(value["classification"], "classification")
        expected_class = _visibility_classification(expected, visible, invisible, unknown, True)
        if classification != expected_class:
            raise SourceMaterializationError("prefix classification/count mismatch")
        result.append({"publication_id": identity["publication_id"], "cohort_id": identity["cohort_id"], "state_point_utc": state, "prefix": prefix, "afi": afi, "classification": classification, "expected_direction_count": expected, "visible_direction_count": visible, "invisible_direction_count": invisible, "unknown_direction_count": unknown, "source_record_refs": _audit_refs(value["source_record_refs"], "source_record_refs")})
    return result


def _asn_rows(raw: Any, identity: Mapping[str, Any]) -> list[dict[str, Any]]:
    keys = ("state_point_utc", "asn", "classification", "fixed_prefix_count", "partial_prefix_count", "complete_prefix_count", "unknown_prefix_count", "invisible_direction_count", "source_record_refs")
    result = []
    for index, item in enumerate(_require_list(raw, "asn_states")):
        value = _require_mapping(item, f"asn_states[{index}]")
        _require_exact_keys(value, keys, f"asn_states[{index}]")
        state = _string(value["state_point_utc"], "state_point_utc")
        _validate_grid_time(state, identity, "state_point_utc")
        fixed, partial, complete, unknown, invisible = [_integer(value[key], key) for key in keys[3:8]]
        if partial + complete + unknown > fixed:
            raise SourceMaterializationError("ASN prefix counts exceed fixed population")
        classification = _string(value["classification"], "classification")
        if classification not in ("normal", "affected", "route_interrupted", "unknown"):
            raise SourceMaterializationError("ASN classification invalid")
        result.append({"publication_id": identity["publication_id"], "cohort_id": identity["cohort_id"], "state_point_utc": state, "asn": _integer(value["asn"], "asn", maximum=4294967295), "classification": classification, "fixed_prefix_count": fixed, "partial_prefix_count": partial, "complete_prefix_count": complete, "unknown_prefix_count": unknown, "invisible_direction_count": invisible, "source_record_refs": _audit_refs(value["source_record_refs"], "source_record_refs")})
    return result


def _validate_grid_time(raw: str, identity: Mapping[str, Any], location: str) -> dt.datetime:
    point = _utc(raw, location)
    start = _utc(identity["window_start_utc"], "window_start_utc")
    end = _utc(identity["data_through"], "data_through")
    delta = int((point - start).total_seconds())
    if point < start or point > end or delta % identity["grid_seconds"]:
        raise SourceMaterializationError(f"{location} is outside exact publication grid")
    return point


def _visibility_classification(expected: int, visible: int, invisible: int, unknown: int, source_complete: bool) -> str:
    if not source_complete or unknown:
        return "unknown"
    if expected <= 0 or visible + invisible != expected:
        raise SourceMaterializationError("visibility population cannot be classified")
    if visible == 0:
        return "complete"
    if visible == expected:
        return "normal"
    return "partial"


def _new_prefix_rows(raw: Any, identity: Mapping[str, Any], profile_digest: str) -> list[dict[str, Any]]:
    keys = ("prefix", "afi", "first_observed_at_utc", "first_observed_view_complete", "expected_peer_asn_direction_ids", "state_points", "source_record_refs")
    point_keys = ("state_point_utc", "source_complete", "direction_states", "source_record_refs")
    result: list[dict[str, Any]] = []
    end = _utc(identity["data_through"], "data_through")
    step = dt.timedelta(seconds=identity["grid_seconds"])
    for index, item in enumerate(_require_list(raw, "new_prefix_projection_inputs")):
        value = _require_mapping(item, f"new_prefix_projection_inputs[{index}]")
        _require_exact_keys(value, keys, f"new_prefix_projection_inputs[{index}]")
        prefix, afi = _prefix(value["prefix"], value["afi"], f"new_prefix_projection_inputs[{index}]")
        first_raw = _string(value["first_observed_at_utc"], "first_observed_at_utc")
        first = _validate_grid_time(first_raw, identity, "first_observed_at_utc")
        if value["first_observed_view_complete"] is not True:
            raise SourceMaterializationError("new-prefix first-observed exact view is not complete")
        directions = _unique_sorted_strings(value["expected_peer_asn_direction_ids"], "expected_peer_asn_direction_ids", nonempty=True)
        point_map: dict[str, Mapping[str, Any]] = {}
        for pindex, raw_point in enumerate(_require_list(value["state_points"], "state_points")):
            point = _require_mapping(raw_point, f"state_points[{pindex}]")
            _require_exact_keys(point, point_keys, f"state_points[{pindex}]")
            point_raw = _string(point["state_point_utc"], "state_point_utc")
            _validate_grid_time(point_raw, identity, "state_point_utc")
            if point_raw in point_map:
                raise SourceMaterializationError("duplicate new-prefix state point")
            direction_states = _require_mapping(point["direction_states"], "direction_states")
            if set(direction_states) - set(directions):
                raise SourceMaterializationError("new-prefix state has direction outside frozen denominator")
            for direction, state in direction_states.items():
                if state not in ("visible", "invisible", "unknown", "missing"):
                    raise SourceMaterializationError(f"invalid direction state: {direction}")
            point_map[point_raw] = point
        expected_points: list[str] = []
        cursor = first
        while cursor <= end:
            expected_points.append(cursor.strftime("%Y-%m-%dT%H:%M:%SZ"))
            cursor += step
        if set(point_map) != set(expected_points):
            raise SourceMaterializationError("new-prefix state track is not dense through data_through")
        first_states = _require_mapping(point_map[first_raw]["direction_states"], "first.direction_states")
        if not all(first_states.get(direction) == "visible" for direction in directions):
            raise SourceMaterializationError("frozen denominator must be exactly first-observed visible directions")
        state_id = "new_prefix_state_v1_" + digest_json([identity["publication_id"], prefix, afi, first_raw])[:32]
        base_refs = _audit_refs(value["source_record_refs"], "source_record_refs")
        for point_raw in expected_points:
            point = point_map[point_raw]
            states = _require_mapping(point["direction_states"], "direction_states")
            visible = sum(states.get(direction, "missing") == "visible" for direction in directions)
            invisible = sum(states.get(direction, "missing") == "invisible" for direction in directions)
            unknown = len(directions) - visible - invisible
            source_complete = point["source_complete"] is True
            classification = _visibility_classification(len(directions), visible, invisible, unknown, source_complete)
            result.append({"publication_id": identity["publication_id"], "new_prefix_state_id": state_id, "prefix": prefix, "afi": afi, "first_observed_at_utc": first_raw, "state_point_utc": point_raw, "classification": classification, "expected_peer_asn_direction_ids": directions, "visible_direction_count": visible, "invisible_direction_count": invisible, "unknown_direction_count": unknown, "projection_profile_id": NEW_PREFIX_PROFILE_ID, "projection_profile_digest": profile_digest, "source_record_refs": sorted(set(base_refs + _audit_refs(point["source_record_refs"], "point.source_record_refs")))})
    return result


def _validate_segments(raw: Any, location: str) -> list[dict[str, Any]]:
    result = []
    for index, item in enumerate(_require_list(raw, location)):
        segment = _require_mapping(item, f"{location}[{index}]")
        _require_exact_keys(segment, ("segment_type", "asns"), f"{location}[{index}]")
        segment_type = _string(segment["segment_type"], "segment_type")
        if segment_type not in ("as_sequence", "as_set", "confederation_sequence", "confederation_set"):
            raise SourceMaterializationError("invalid path segment type")
        asns = [_integer(asn, "path ASN", maximum=4294967295) for asn in _require_list(segment["asns"], "asns")]
        if not asns:
            raise SourceMaterializationError("empty path segment")
        if segment_type in ("as_set", "confederation_set"):
            asns = sorted(asns)
        result.append({"segment_type": segment_type, "asns": asns})
    return result


def _route_rows(raw: Any, identity: Mapping[str, Any]) -> list[dict[str, Any]]:
    view_keys = ("state_point_utc", "source_dataset_digest", "checkpoint_id", "projection_receipt_digest", "projection_receipt_publication_id", "projection_receipt_state_point_utc", "no_future_read_verified", "source_complete", "rows", "source_record_refs")
    row_keys = ("prefix", "afi", "peer_asn_direction_id", "route_observation_key", "vp_id", "peer_id", "visibility", "origin_status", "origin_asns", "path_status", "common_path_status", "path_id", "path_digest", "path_canonicalization_profile_id", "path_canonicalization_profile_digest", "path_segments", "last_event_id", "last_update_utc", "quality_flags", "source_record_refs")
    result = []
    seen_views: set[str] = set()
    for index, item in enumerate(_require_list(raw, "exact_route_state_views")):
        view = _require_mapping(item, f"exact_route_state_views[{index}]")
        _require_exact_keys(view, view_keys, f"exact_route_state_views[{index}]")
        state = _string(view["state_point_utc"], "state_point_utc")
        state_time = _validate_grid_time(state, identity, "state_point_utc")
        if state in seen_views:
            raise SourceMaterializationError("duplicate exact RouteState view")
        seen_views.add(state)
        _sha(view["source_dataset_digest"], "source_dataset_digest")
        checkpoint = _string(view["checkpoint_id"], "checkpoint_id")
        projection = _sha(view["projection_receipt_digest"], "projection_receipt_digest")
        if view["projection_receipt_publication_id"] != identity["publication_id"] or view["projection_receipt_state_point_utc"] != state:
            raise SourceMaterializationError("projection receipt identity mismatch")
        if view["no_future_read_verified"] is not True or view["source_complete"] is not True:
            raise SourceMaterializationError("exact RouteState view lacks completeness/no-future proof")
        view_refs = _audit_refs(view["source_record_refs"], "view.source_record_refs")
        route_keys: set[str] = set()
        for rindex, raw_row in enumerate(_require_list(view["rows"], "view.rows")):
            row = _require_mapping(raw_row, f"view.rows[{rindex}]")
            _require_exact_keys(row, row_keys, f"view.rows[{rindex}]")
            prefix, afi = _prefix(row["prefix"], row["afi"], f"view.rows[{rindex}]")
            last_update = _string(row["last_update_utc"], "last_update_utc")
            if _utc(last_update, "last_update_utc") > state_time:
                raise SourceMaterializationError("RouteState row reads a future update")
            route_key = _string(row["route_observation_key"], "route_observation_key")
            if route_key in route_keys:
                raise SourceMaterializationError("duplicate route_observation_key in exact view")
            route_keys.add(route_key)
            visibility = _string(row["visibility"], "visibility")
            origin_status = _string(row["origin_status"], "origin_status")
            path_status = _string(row["path_status"], "path_status")
            common_status = _string(row["common_path_status"], "common_path_status")
            if visibility not in ("visible", "invisible", "unknown", "missing") or origin_status not in ("known", "unknown", "ambiguous", "not_applicable") or path_status not in ("known", "unknown", "ambiguous", "not_applicable") or common_status not in ("ordered", "unordered", "ambiguous", "invalid", "unknown", "not_applicable"):
                raise SourceMaterializationError("invalid RouteState status")
            origins = _unique_sorted_asns(row["origin_asns"], "origin_asns")
            segments = _validate_segments(row["path_segments"], "path_segments")
            if path_status == "known":
                if not segments or row["path_id"] is None:
                    raise SourceMaterializationError("known path lacks native path fields")
                if row["path_canonicalization_profile_id"] != PATH_PROFILE_ID or row["path_canonicalization_profile_digest"] != PATH_PROFILE_DIGEST:
                    raise SourceMaterializationError("path canonicalization profile mismatch")
                if _sha(row["path_digest"], "path_digest") != digest_json(segments):
                    raise SourceMaterializationError("path digest mismatch")
            elif segments or any(row[key] is not None for key in ("path_id", "path_digest", "path_canonicalization_profile_id", "path_canonicalization_profile_digest")):
                raise SourceMaterializationError("non-known path must not fabricate path fields")
            if origin_status == "known" and not origins:
                raise SourceMaterializationError("known origin requires origin_asns")
            if origin_status != "known" and origins:
                raise SourceMaterializationError("non-known origin must not select origin_asns")
            result.append({"publication_id": identity["publication_id"], "state_point_utc": state, "prefix": prefix, "afi": afi, "peer_asn_direction_id": _string(row["peer_asn_direction_id"], "peer_asn_direction_id"), "route_observation_key": route_key, "vp_id": _string(row["vp_id"], "vp_id"), "peer_id": _string(row["peer_id"], "peer_id"), "visibility": visibility, "origin_status": origin_status, "origin_asns": origins, "path_status": path_status, "common_path_status": common_status, "path_id": row["path_id"], "path_digest": row["path_digest"], "path_canonicalization_profile_id": row["path_canonicalization_profile_id"], "path_canonicalization_profile_digest": row["path_canonicalization_profile_digest"], "path_segments": segments, "last_event_id": _string(row["last_event_id"], "last_event_id"), "last_update_utc": last_update, "checkpoint_id": checkpoint, "projection_receipt_digest": projection, "quality_flags": _unique_sorted_strings(row["quality_flags"], "quality_flags"), "source_record_refs": sorted(set(view_refs + _audit_refs(row["source_record_refs"], "row.source_record_refs")))})
    return result


def _window_rows(raw_rows: Any, raw_anchor: Any, identity: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    anchor = _require_mapping(raw_anchor, "eligible_anchor_population")
    anchor_keys = ("source_ref", "result_set_id", "manifest_digest", "content_digest", "freeze_digest", "complete", "eligible_anchor_asns", "eligible_anchor_asns_digest", "source_record_refs")
    _require_exact_keys(anchor, anchor_keys, "eligible_anchor_population")
    if anchor["complete"] is not True:
        raise SourceMaterializationError("eligible anchor ResultSet is not complete")
    eligible = _unique_sorted_asns(anchor["eligible_anchor_asns"], "eligible_anchor_asns", nonempty=True)
    eligible_digest = _sha(anchor["eligible_anchor_asns_digest"], "eligible_anchor_asns_digest")
    if digest_json(eligible) != eligible_digest:
        raise SourceMaterializationError("eligible anchor population digest mismatch")
    for key in ("manifest_digest", "content_digest", "freeze_digest"):
        _sha(anchor[key], f"eligible_anchor_population.{key}")
    anchor_meta = {"anchor_population_source_ref": _string(anchor["source_ref"], "source_ref"), "anchor_result_set_id": _string(anchor["result_set_id"], "result_set_id"), "anchor_manifest_digest": anchor["manifest_digest"], "anchor_content_digest": anchor["content_digest"], "anchor_freeze_digest": anchor["freeze_digest"], "eligible_anchor_asns": eligible, "eligible_anchor_asns_digest": eligible_digest, "eligible_anchor_asn_count": len(eligible)}
    keys = ("anchor_asn", "known_origin_asn", "observed_origin_asn", "prefix", "afi", "path_id", "path_digest", "path_canonicalization_profile_id", "path_canonicalization_profile_digest", "path_segments", "peer_asn_direction_ids", "route_observation_count", "source_record_refs")
    result = []
    for index, item in enumerate(_require_list(raw_rows, "window_path_associations")):
        row = _require_mapping(item, f"window_path_associations[{index}]")
        _require_exact_keys(row, keys, f"window_path_associations[{index}]")
        anchor_asn = _integer(row["anchor_asn"], "anchor_asn", maximum=4294967295)
        origin = _integer(row["known_origin_asn"], "known_origin_asn", maximum=4294967295)
        observed = _integer(row["observed_origin_asn"], "observed_origin_asn", maximum=4294967295)
        if anchor_asn not in eligible or origin != observed:
            raise SourceMaterializationError("path association anchor/origin binding invalid")
        prefix, afi = _prefix(row["prefix"], row["afi"], f"window_path_associations[{index}]")
        if row["path_canonicalization_profile_id"] != PATH_PROFILE_ID or row["path_canonicalization_profile_digest"] != PATH_PROFILE_DIGEST:
            raise SourceMaterializationError("window path profile mismatch")
        segments = _validate_segments(row["path_segments"], "path_segments")
        if any(segment["segment_type"] != "as_sequence" for segment in segments):
            raise SourceMaterializationError("window association requires ordered AS_SEQUENCE path")
        if _sha(row["path_digest"], "path_digest") != digest_json(segments):
            raise SourceMaterializationError("window path digest mismatch")
        sequence = [asn for segment in segments for asn in segment["asns"]]
        collapsed = [asn for position, asn in enumerate(sequence) if position == 0 or sequence[position - 1] != asn]
        if not collapsed or collapsed[-1] != origin or anchor_asn not in collapsed[:-1]:
            raise SourceMaterializationError("known origin must be tail and strictly after anchor")
        path_id = _string(row["path_id"], "path_id")
        association_id = "path_association_v1_" + digest_json([identity["publication_id"], anchor_asn, origin, prefix, afi, path_id])[:32]
        result.append({"publication_id": identity["publication_id"], "path_association_id": association_id, "anchor_asn": anchor_asn, "known_origin_asn": origin, "origin_status": "known", "observed_origin_asn": observed, "prefix": prefix, "afi": afi, "path_id": path_id, "path_digest": row["path_digest"], "path_canonicalization_profile_id": PATH_PROFILE_ID, "path_canonicalization_profile_digest": PATH_PROFILE_DIGEST, "path_segments": segments, "path_parse_status": "known", "common_path_status": "ordered", "ordered_sequence_eligible": True, "peer_asn_direction_ids": _unique_sorted_strings(row["peer_asn_direction_ids"], "peer_asn_direction_ids", nonempty=True), "route_observation_count": _integer(row["route_observation_count"], "route_observation_count", minimum=1), "source_record_refs": sorted(set(_audit_refs(anchor["source_record_refs"], "anchor.source_record_refs") + _audit_refs(row["source_record_refs"], "row.source_record_refs")))})
    return result, anchor_meta


def _finish_rows(population_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    key_fields = MEMBER_KEY_FIELDS[population_id]
    result = []
    keys_seen: set[str] = set()
    for row in rows:
        value = {"schema_version": ROW_SCHEMA_VERSIONS[population_id], **row}
        key_values = [value[field] for field in key_fields]
        member_key = "p2s1_member_v1_" + digest_json([population_id, key_values])
        if member_key in keys_seen:
            raise SourceMaterializationError(f"duplicate member identity in {population_id}")
        keys_seen.add(member_key)
        value["member_key"] = member_key
        value["row_digest"] = digest_json(value)
        result.append(value)
    return sorted(result, key=lambda item: item["member_key"])


def _secondary_indexes(population_id: str, rows: Sequence[Mapping[str, Any]], anchor_meta: Mapping[str, Any] | None) -> dict[str, Any]:
    indexes: dict[str, Any] = {}
    if population_id == "materialized_route_state_rows_at_exact_time":
        membership: dict[str, list[str]] = {}
        for row in rows:
            if row["visibility"] != "visible" or row["common_path_status"] not in ("ordered", "unordered"):
                continue
            members = sorted({asn for segment in row["path_segments"] for asn in segment["asns"]})
            for asn in members:
                membership.setdefault(str(asn), []).append(row["member_key"])
        membership = {key: sorted(value) for key, value in sorted(membership.items(), key=lambda item: int(item[0]))}
        indexes = {"path_asn_membership": {"index_id": "path_asn_membership_v1_" + digest_json(membership)[:32], "profile_id": PATH_MEMBERSHIP_PROFILE_ID, "profile_digest": PATH_MEMBERSHIP_PROFILE_DIGEST, "members_by_asn": membership, "indexed_member_keys_digest": digest_json(sorted({key for values in membership.values() for key in values}))}}
    elif population_id == "window_path_association_evidence_rows":
        membership: dict[str, list[str]] = {}
        anchor_before: dict[str, list[str]] = {}
        for row in rows:
            path_members = sorted({asn for segment in row["path_segments"] for asn in segment["asns"]})
            for asn in path_members:
                membership.setdefault(str(asn), []).append(row["member_key"])
            anchor_before.setdefault(str(row["anchor_asn"]), []).append(row["member_key"])
        for index in (membership, anchor_before):
            for key in index:
                index[key].sort()
        indexes = {"path_asn_membership": {"index_id": "window_path_asn_membership_v1_" + digest_json(membership)[:32], "profile_id": PATH_MEMBERSHIP_PROFILE_ID, "profile_digest": PATH_MEMBERSHIP_PROFILE_DIGEST, "members_by_asn": dict(sorted(membership.items(), key=lambda item: int(item[0])))}, "anchor_before_known_origin": {"index_id": "anchor_before_known_origin_v1_" + digest_json(anchor_before)[:32], "filter_profile_id": WINDOW_FILTER_PROFILE_ID, "filter_profile_digest": WINDOW_FILTER_PROFILE_DIGEST, "members_by_anchor_asn": dict(sorted(anchor_before.items(), key=lambda item: int(item[0]))), **dict(anchor_meta or {})}}
    return indexes


def _write_json(path: Path, payload: Mapping[str, Any]) -> tuple[str, int]:
    raw = (canonical_json(payload) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return sha256_bytes(raw), len(raw)


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> tuple[str, int]:
    raw = b"".join((canonical_json(row) + "\n").encode("utf-8") for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return sha256_bytes(raw), len(raw)


def build_source_store(input_path: Path, output_path: Path, *, contract_root: Path | None = None) -> dict[str, Any]:
    contract_root = contract_root or Path(__file__).resolve().parents[1] / "contracts/data/country-outage-p2-s1"
    payload = _require_mapping(load_json_strict(input_path), "input")
    top_keys = ("schema_version", "identity", "source_refs", "fixed_cohort_members", "prefix_states", "asn_states", "new_prefix_projection_inputs", "exact_route_state_views", "eligible_anchor_population", "window_path_associations")
    _require_exact_keys(payload, top_keys, "input")
    if payload["schema_version"] != "country_outage_p2_s1_authoritative_source_bundle_v1":
        raise SourceMaterializationError("input schema_version mismatch")
    identity = _validate_identity(payload["identity"])
    source_refs = _validate_source_refs(payload["source_refs"], identity["publication_id"])
    source_refs_by_kind = {ref["source_kind"]: ref for ref in source_refs}
    required_source_kinds = {
        "event_cohort", "event_metric", "exact_route_state_projection",
        "window_path_association", "affected_as_result_set",
    }
    if not required_source_kinds.issubset(source_refs_by_kind):
        raise SourceMaterializationError(
            f"authoritative source refs missing: {sorted(required_source_kinds - set(source_refs_by_kind))}"
        )
    for view in _require_list(payload["exact_route_state_views"], "exact_route_state_views"):
        if _require_mapping(view, "exact_route_state_view").get("source_dataset_digest") != source_refs_by_kind["exact_route_state_projection"]["content_sha256"]:
            raise SourceMaterializationError("exact RouteState source dataset digest is not authoritative")
    anchor_input = _require_mapping(payload["eligible_anchor_population"], "eligible_anchor_population")
    anchor_source = source_refs_by_kind["affected_as_result_set"]
    if (
        anchor_input.get("source_ref") != anchor_source["dataset_id"]
        or anchor_input.get("manifest_digest") != anchor_source["manifest_sha256"]
        or anchor_input.get("content_digest") != anchor_source["content_sha256"]
    ):
        raise SourceMaterializationError("eligible anchor population is not bound to authoritative ResultSet source")
    profiles, profiles_raw = _load_profiles(contract_root)
    new_profile_digest = profiles[NEW_PREFIX_PROFILE_ID]["profile_digest"]
    rows_by_population: dict[str, list[dict[str, Any]]] = {
        "fixed_cohort_member_rows": _fixed_rows(payload["fixed_cohort_members"], identity),
        "prefix_state_rows": _prefix_rows(payload["prefix_states"], identity),
        "asn_state_rows": _asn_rows(payload["asn_states"], identity),
        "new_prefix_state_rows": _new_prefix_rows(payload["new_prefix_projection_inputs"], identity, new_profile_digest),
        "materialized_route_state_rows_at_exact_time": _route_rows(payload["exact_route_state_views"], identity),
    }
    window_rows, anchor_meta = _window_rows(payload["window_path_associations"], payload["eligible_anchor_population"], identity)
    rows_by_population["window_path_association_evidence_rows"] = window_rows
    rows_by_population = {population_id: _finish_rows(population_id, rows) for population_id, rows in rows_by_population.items()}
    if output_path.exists():
        raise SourceMaterializationError(f"output already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{output_path.name}.", dir=output_path.parent))
    try:
        (temp / "source-profiles.json").write_bytes(profiles_raw)
        population_manifests = []
        for population_id in POPULATION_IDS:
            rows = rows_by_population[population_id]
            row_ref = Path("populations") / f"{population_id}.jsonl"
            row_sha, row_size = _write_jsonl(temp / row_ref, rows)
            member_keys_digest = digest_json([row["member_key"] for row in rows])
            secondary = _secondary_indexes(population_id, rows, anchor_meta if population_id == "window_path_association_evidence_rows" else None)
            index_payload: dict[str, Any] = {"schema_version": "country_outage_p2_s1_source_index_v1", "population_id": population_id, "publication_id": identity["publication_id"], "member_key_fields": list(MEMBER_KEY_FIELDS[population_id]), "members": [{"member_key": row["member_key"], "row_ordinal": ordinal, "row_digest": row["row_digest"]} for ordinal, row in enumerate(rows)], "member_keys_digest": member_keys_digest, "secondary_indexes": secondary}
            index_payload["content_sha256"] = digest_json(index_payload)
            index_ref = Path("indexes") / f"{population_id}.index.json"
            index_sha, index_size = _write_json(temp / index_ref, index_payload)
            schema_path = contract_root / SCHEMA_FILES[population_id]
            schema_sha = sha256_bytes(schema_path.read_bytes())
            profile_bindings: dict[str, Any] = {}
            if population_id == "new_prefix_state_rows":
                profile_bindings = {"new_prefix_projection_profile_id": NEW_PREFIX_PROFILE_ID, "new_prefix_projection_profile_digest": new_profile_digest}
            elif population_id == "materialized_route_state_rows_at_exact_time":
                profile_bindings = {"exact_route_state_input_profile_id": EXACT_ROUTE_PROFILE_ID, "exact_route_state_input_profile_digest": profiles[EXACT_ROUTE_PROFILE_ID]["profile_digest"], "path_asn_membership_profile_id": PATH_MEMBERSHIP_PROFILE_ID, "path_asn_membership_profile_digest": PATH_MEMBERSHIP_PROFILE_DIGEST, "path_asn_membership_index_digest": index_payload["content_sha256"]}
            elif population_id == "window_path_association_evidence_rows":
                profile_bindings = {"path_association_filter_profile_id": WINDOW_FILTER_PROFILE_ID, "path_association_filter_profile_digest": WINDOW_FILTER_PROFILE_DIGEST, "path_association_index_digest": index_payload["content_sha256"], **anchor_meta}
            receipt: dict[str, Any] = {"schema_version": "country_outage_p2_s1_materialization_receipt_v1", "status": "materialized_ready", "population_id": population_id, "publication_id": identity["publication_id"], "source_refs": source_refs, "schema_sha256": schema_sha, "row_file_sha256": row_sha, "index_digest": index_payload["content_sha256"], "row_count": len(rows), "member_keys_digest": member_keys_digest, "materializer_id": "country_outage_p2_s1_source_view_builder", "materializer_version": "1.0.0", "profile_bindings": profile_bindings}
            receipt_semantic_digest = digest_json(receipt)
            receipt["receipt_id"] = "p2s1_materialization_receipt_v1_" + receipt_semantic_digest
            receipt["content_sha256"] = digest_json(receipt)
            receipt_ref = Path("receipts") / f"{receipt['content_sha256']}.json"
            _write_json(temp / receipt_ref, receipt)
            population_manifests.append({"population_id": population_id, "schema_ref": SCHEMA_FILES[population_id], "schema_sha256": schema_sha, "readiness": "ready", "blocking_codes": [], "row_file": {"path": row_ref.as_posix(), "sha256": row_sha, "size_bytes": row_size}, "index_file": {"path": index_ref.as_posix(), "sha256": index_sha, "size_bytes": index_size}, "row_count": len(rows), "member_keys_digest": member_keys_digest, "materialization_receipt_digest": receipt["content_sha256"], "materialization_receipt_ref": receipt_ref.as_posix(), "source_refs": source_refs})
        manifest: dict[str, Any] = {"schema_version": "country_outage_p2_s1_source_store_manifest_v1", "identity": identity, "source_profiles_ref": {"path": "source-profiles.json", "sha256": sha256_bytes(profiles_raw)}, "population_manifests": population_manifests}
        store_semantic_digest = digest_json(manifest)
        manifest["store_id"] = "country_outage_p2_s1_source_store_v1_" + store_semantic_digest
        manifest["content_sha256"] = digest_json(manifest)
        _write_json(temp / "manifest.json", manifest)
        os.replace(temp, output_path)
        return manifest
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--contract-root", type=Path)
    args = parser.parse_args(argv)
    manifest = build_source_store(args.input, args.output, contract_root=args.contract_root)
    print(canonical_json({"status": "built", "store_id": manifest["store_id"], "manifest": str(args.output / "manifest.json"), "population_count": len(manifest["population_manifests"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
