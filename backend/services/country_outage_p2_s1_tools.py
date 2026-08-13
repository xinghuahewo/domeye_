"""RRC25 P2-S1 W1/W2 原子只读 Tool 运行时。

每次调用只消费 ``CountryOutageP2S1SourceStore`` 已验证的一种事实人口。这里的
排序只用于冻结分页顺序，不是业务排名；字段谓词只作用于已物化行。TOOL-12 的
``contains_asn`` 与 anchor 过滤只接受 W0 预物化索引成员，禁止查询时解析路径来
决定结果人口。
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from backend.services.country_outage_p2_s1_source_store import (
    CountryOutageP2S1SourceStore,
    SourcePopulationUnavailable,
    SourceStoreIntegrityError,
    canonical_json,
    digest_json,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_IDENTITY_FIELDS = (
    "incident_id",
    "publication_id",
    "publication_revision",
    "publication_digest",
    "collector_id",
    "cohort_id",
    "cohort_digest",
    "window_start_utc",
    "window_end_utc",
    "data_through_utc",
    "finality",
    "registry_snapshot_id",
    "registry_snapshot_digest",
    "binding_generation",
)
_FILTER_PROFILE_ID = "PROFILE-WINDOW-PATH-ASSOCIATION-FILTER-1.0.0"
_FILTER_PROFILE_DIGEST = "46ca0955b30a4d43088c214ec5bdf84fbf9b65987bd65047257e85e1d7778eb7"
_PATH_MEMBERSHIP_PROFILE_ID = "PROFILE-PATH-ASN-MEMBERSHIP-1.0.0"
_PATH_MEMBERSHIP_PROFILE_DIGEST = "28acec6edd232fd9aa38885175bcd715b9ea72f240efca6b3c5b7080394655e2"


class ToolQueryError(ValueError):
    """原子 Tool 整体失败；失败不携带可发布 ResultSet。"""

    def __init__(self, code: str, message: str, *, receipt: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.receipt = copy.deepcopy(dict(receipt)) if receipt is not None else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "failed",
            "error_code": self.code,
            "message": str(self),
            "tool_run_receipt": copy.deepcopy(self.receipt),
        }


@dataclass(frozen=True)
class _ToolSpec:
    tool_id: str
    name: str
    population_id: str
    max_page_size: int
    allowed_filters: tuple[str, ...]
    output_fields: tuple[str, ...]
    stable_sort: tuple[str, ...]
    sort_key: Callable[[Mapping[str, Any]], tuple[Any, ...]]


def _utc(value: Any, location: str) -> datetime:
    if not isinstance(value, str) or _UTC_RE.fullmatch(value) is None:
        raise ToolQueryError("invalid_input", f"{location} must be canonical UTC seconds")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ToolQueryError("invalid_input", f"{location} is not a valid UTC time") from exc
    return parsed


def _network(value: Any, location: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    if not isinstance(value, str):
        raise ToolQueryError("invalid_input", f"{location} must be a canonical CIDR")
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise ToolQueryError("invalid_input", f"{location} must be a canonical CIDR") from exc
    if str(network) != value:
        raise ToolQueryError("invalid_input", f"{location} must be a canonical CIDR")
    return network


def _prefix_sort(row: Mapping[str, Any]) -> tuple[int, int, int]:
    network = ipaddress.ip_network(row["prefix"], strict=True)
    return (network.version, int(network.network_address), network.prefixlen)


def _fixed_sort(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (*_prefix_sort(row), row["cohort_member_id"])


def _prefix_state_sort(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (row["state_point_utc"], *_prefix_sort(row))


def _asn_state_sort(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (row["asn"], row["state_point_utc"])


def _new_prefix_sort(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row["first_observed_at_utc"],
        *_prefix_sort(row),
        row["state_point_utc"],
        row["new_prefix_state_id"],
    )


def _path_sort(row: Mapping[str, Any]) -> tuple[Any, ...]:
    network = ipaddress.ip_network(row["prefix"], strict=True)
    return (
        row["anchor_asn"],
        row["known_origin_asn"],
        network.version,
        int(network.network_address),
        row["path_id"],
        row["path_association_id"],
    )


_SPECS = {
    "TOOL-07": _ToolSpec(
        "TOOL-07",
        "query_fixed_cohort_members",
        "fixed_cohort_member_rows",
        1000,
        ("asn", "prefix", "afi"),
        (
            "cohort_member_id",
            "prefix",
            "afi",
            "country_origin_asns",
            "expected_peer_asn_direction_ids",
            "expected_route_observation_keys",
            "membership_basis",
        ),
        ("afi ASC", "network_address ASC", "prefix_length ASC", "cohort_member_id ASC"),
        _fixed_sort,
    ),
    "TOOL-08": _ToolSpec(
        "TOOL-08",
        "query_prefix_states",
        "prefix_state_rows",
        2000,
        ("prefix", "afi", "state_point_utc", "time_range_half_open", "classification"),
        (
            "state_point_utc",
            "prefix",
            "afi",
            "classification",
            "expected_direction_count",
            "visible_direction_count",
            "invisible_direction_count",
            "unknown_direction_count",
        ),
        ("state_point_utc ASC", "afi ASC", "network_address ASC", "prefix_length ASC"),
        _prefix_state_sort,
    ),
    "TOOL-09": _ToolSpec(
        "TOOL-09",
        "query_as_states",
        "asn_state_rows",
        2000,
        ("asn", "state_point_utc", "time_range_half_open", "classification"),
        (
            "state_point_utc",
            "asn",
            "classification",
            "fixed_prefix_count",
            "partial_prefix_count",
            "complete_prefix_count",
            "unknown_prefix_count",
            "invisible_direction_count",
        ),
        ("asn ASC", "state_point_utc ASC"),
        _asn_state_sort,
    ),
    "TOOL-10": _ToolSpec(
        "TOOL-10",
        "query_new_prefix_states",
        "new_prefix_state_rows",
        2000,
        (
            "prefix",
            "afi",
            "first_observed_range_half_open",
            "state_point_utc",
            "time_range_half_open",
            "classification",
        ),
        (
            "new_prefix_state_id",
            "prefix",
            "afi",
            "first_observed_at_utc",
            "state_point_utc",
            "classification",
        ),
        (
            "first_observed_at_utc ASC",
            "afi ASC",
            "network_address ASC",
            "prefix_length ASC",
            "state_point_utc ASC",
            "new_prefix_state_id ASC",
        ),
        _new_prefix_sort,
    ),
    "TOOL-12": _ToolSpec(
        "TOOL-12",
        "query_window_path_associations",
        "window_path_association_evidence_rows",
        1000,
        (
            "anchor_asn",
            "known_origin_asn",
            "anchor_before_known_origin",
            "contains_asn",
            "prefix",
            "afi",
            "path_id",
            "peer_asn_direction_id",
            "ordered_sequence_eligible",
        ),
        (
            "path_association_id",
            "anchor_asn",
            "known_origin_asn",
            "origin_status",
            "observed_origin_asn",
            "prefix",
            "afi",
            "path_id",
            "path_digest",
            "path_canonicalization_profile_id",
            "path_canonicalization_profile_digest",
            "path_segments",
            "path_parse_status",
            "common_path_status",
            "ordered_sequence_eligible",
            "peer_asn_direction_ids",
            "route_observation_count",
        ),
        (
            "anchor_asn ASC",
            "known_origin_asn ASC",
            "afi ASC",
            "network_address ASC",
            "path_id ASC",
            "path_association_id ASC",
        ),
        _path_sort,
    ),
}


class CountryOutageP2S1Tools:
    """TOOL-07/08/09/10/12 的原子只读执行器。"""

    tool_version = "1.0.0"

    def __init__(
        self,
        source_store: CountryOutageP2S1SourceStore,
        *,
        page_token_key: bytes,
        query_receipt_key: bytes | None = None,
    ) -> None:
        if not isinstance(source_store, CountryOutageP2S1SourceStore):
            raise TypeError("source_store must be CountryOutageP2S1SourceStore")
        if not isinstance(page_token_key, bytes) or len(page_token_key) < 32:
            raise ValueError("page_token_key must contain at least 32 bytes")
        receipt_key = page_token_key if query_receipt_key is None else query_receipt_key
        if not isinstance(receipt_key, bytes) or len(receipt_key) < 32:
            raise ValueError("query_receipt_key must contain at least 32 bytes")
        self._store = source_store
        self._page_token_key = bytes(page_token_key)
        self._query_receipt_key = bytes(receipt_key)

    def query_fixed_cohort_members(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.execute("TOOL-07", request)

    def query_prefix_states(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.execute("TOOL-08", request)

    def query_as_states(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.execute("TOOL-09", request)

    def query_new_prefix_states(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.execute("TOOL-10", request)

    def query_window_path_associations(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.execute("TOOL-12", request)

    def execute(self, tool_id: str, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(tool_id, str):
            raise ToolQueryError("invalid_input", "tool_id must be a string")
        spec = _SPECS.get(tool_id)
        if spec is None:
            raise ToolQueryError("unsupported_filter", f"Tool is not implemented in W1/W2: {tool_id}")
        if not isinstance(request, Mapping):
            raise ToolQueryError("invalid_input", "request must be an object")
        allowed = {"identity", "page_size", "page_token", *spec.allowed_filters}
        unknown = sorted((key for key in request if key not in allowed), key=repr)
        if unknown:
            raise ToolQueryError("unsupported_filter", f"unsupported request fields: {unknown}")
        if "identity" not in request or "page_size" not in request:
            raise ToolQueryError("invalid_input", "identity and page_size are required")
        identity = self._validate_identity(request["identity"])
        page_size = request["page_size"]
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not (1 <= page_size <= spec.max_page_size):
            raise ToolQueryError("invalid_input", f"page_size must be 1..{spec.max_page_size}")
        page_token = request.get("page_token")
        if page_token is not None and not isinstance(page_token, str):
            raise ToolQueryError("invalid_page_token", "page_token must be null or string")
        normalized_filters = self._normalize_filters(spec, request, identity)
        normalized_query = {"filters": normalized_filters}
        query_digest = digest_json({"normalized_query": normalized_query})
        identity_digest = digest_json(identity)
        stable_sort = list(spec.stable_sort)
        stable_sort_digest = digest_json({"stable_sort": stable_sort})

        try:
            manifest = self._store.manifest
            self._assert_source_identity(identity, manifest["identity"])
            rows = self._store.load_population(spec.population_id)
            index = self._store.load_index(spec.population_id)
            if spec.tool_id == "TOOL-12":
                self._assert_tool12_source_rows(rows)
        except ToolQueryError:
            raise
        except SourcePopulationUnavailable as exc:
            raise ToolQueryError("source_view_unready", "source population semantics are not ready") from exc
        except SourceStoreIntegrityError as exc:
            raise ToolQueryError("source_incomplete", "verified source population is unavailable") from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolQueryError("source_schema_unready", "source population contract is unavailable") from exc

        population_manifest = next(
            (entry for entry in manifest["population_manifests"] if entry["population_id"] == spec.population_id),
            None,
        )
        if population_manifest is None:
            raise ToolQueryError("source_schema_unready", "population manifest is missing")
        self._assert_runtime_snapshot(rows, index, population_manifest)
        source_dataset_digest = digest_json(
            {
                "source_store_content_sha256": manifest["content_sha256"],
                "population_manifest": population_manifest,
            }
        )
        source_context = {
            "source_store_id": manifest["store_id"],
            "source_store_content_sha256": manifest["content_sha256"],
            "source_population_id": spec.population_id,
            "source_dataset_digest": source_dataset_digest,
            "source_schema_ref": population_manifest["schema_ref"],
            "source_schema_digest": population_manifest["schema_sha256"],
            "source_index_digest": index["content_sha256"],
            "source_materialization_receipt_digest": population_manifest["materialization_receipt_digest"],
            "source_population_binding": copy.deepcopy(
                index.get("secondary_indexes", {}).get("source_population_binding")
            ),
        }

        decoded_page_token: Mapping[str, Any] | None = None
        if page_token is not None:
            decoded_page_token = self._decode_page_token(page_token)
            self._validate_page_token(
                decoded_page_token,
                spec=spec,
                identity_digest=identity_digest,
                query_digest=query_digest,
                stable_sort_digest=stable_sort_digest,
                source_dataset_digest=source_dataset_digest,
                page_size=page_size,
            )

        candidate_keys, native_context = self._candidate_member_keys(
            spec,
            normalized_filters,
            index,
            identity,
            query_digest,
            identity_digest,
            source_context,
        )
        row_by_key = {row["member_key"]: row for row in rows}
        if len(row_by_key) != len(rows) or any(key not in row_by_key for key in candidate_keys):
            raise ToolQueryError("evidence_unclosed", "verified index/member closure changed during query")
        matched = [row_by_key[key] for key in candidate_keys if self._matches(spec, row_by_key[key], normalized_filters)]
        matched.sort(key=spec.sort_key)
        matched_member_keys = [row["member_key"] for row in matched]
        matched_member_keys_digest = digest_json({"member_keys": matched_member_keys})

        offset = 0
        if decoded_page_token is not None:
            last_key = decoded_page_token.get("last_member_key")
            last_sort_key = decoded_page_token.get("last_member_sort_key")
            positions = [
                index_number
                for index_number, row in enumerate(matched)
                if row["member_key"] == last_key and list(spec.sort_key(row)) == last_sort_key
            ]
            if len(positions) != 1:
                raise ToolQueryError("invalid_page_token", "page token cursor is not in the frozen query population")
            offset = positions[0] + 1
            if decoded_page_token.get("next_offset") != offset:
                raise ToolQueryError("invalid_page_token", "page token offset does not match cursor")

        page_rows = matched[offset : offset + page_size]
        next_offset = offset + len(page_rows)
        next_page_token = None
        if next_offset < len(matched):
            last = page_rows[-1]
            next_page_token = self._encode_page_token(
                {
                    "schema_version": "country_outage_p2_s1_page_token_v1",
                    "tool_id": spec.tool_id,
                    "tool_version": self.tool_version,
                    "identity_digest": identity_digest,
                    "query_digest": query_digest,
                    "stable_sort_digest": stable_sort_digest,
                    "source_dataset_digest": source_dataset_digest,
                    "page_size": page_size,
                    "last_member_key": last["member_key"],
                    "last_member_sort_key": list(spec.sort_key(last)),
                    "next_offset": next_offset,
                }
            )

        query_receipt = self._query_receipt(
            spec=spec,
            identity=identity,
            identity_digest=identity_digest,
            normalized_query=normalized_query,
            query_digest=query_digest,
            stable_sort_digest=stable_sort_digest,
            source_context=source_context,
            matched_member_keys_digest=matched_member_keys_digest,
            matched_total_count=len(matched),
            page_member_keys=[row["member_key"] for row in page_rows],
            page_offset=offset,
            next_offset=next_offset if next_page_token is not None else None,
            disposition="complete_empty" if not matched else "matched_population",
            native_context=native_context,
        )
        public_members = [self._public_member(spec, row) for row in page_rows]
        result_identity = {
            "source_identity": identity,
            "source_tool": {"tool_id": spec.tool_id, "tool_version": self.tool_version},
            "normalized_query": normalized_query,
            "stable_sort": stable_sort,
            "source_population_id": spec.population_id,
            "source_population_schema_ref": population_manifest["schema_ref"],
            "source_population_schema_digest": population_manifest["schema_sha256"],
            "source_dataset_digest": source_dataset_digest,
        }
        result_set_id = "result-set-sha256:" + digest_json(result_identity)
        run_semantic = {
            "result_set_id": result_set_id,
            "page_token_in": page_token,
            "page_offset": offset,
            "page_size": page_size,
            "query_receipt_digest": query_receipt["receipt_digest"],
        }
        tool_run_id = "tool-run-sha256:" + digest_json(run_semantic)
        result: dict[str, Any] = {
            "result_set_id": result_set_id,
            "tool_run_id": tool_run_id,
            "tool_id": spec.tool_id,
            "tool_version": self.tool_version,
            "query_digest": query_digest,
            "identity_digest": identity_digest,
            "source_population_id": spec.population_id,
            "source_dataset_digest": source_dataset_digest,
            "normalized_query": normalized_query,
            "stable_sort": stable_sort,
            "stable_sort_digest": stable_sort_digest,
            "members": public_members,
            "returned_count": len(public_members),
            "total_count": len(matched),
            "page_complete": True,
            "page_offset": offset,
            # A terminal continuation page is not the whole set.  Only a first
            # response containing the entire population may claim completeness;
            # the Host must assemble and freeze multi-page results separately.
            "set_completeness": (
                "complete"
                if page_token is None and next_page_token is None
                else "partial_page"
            ),
            "next_page_token": next_page_token,
            "query_receipt": query_receipt,
            "query_receipt_digest": query_receipt["receipt_digest"],
            "evidence_refs": sorted(
                {
                    f"source-store:{manifest['content_sha256']}",
                    f"source-index:{index['content_sha256']}",
                    f"materialization-receipt:{population_manifest['materialization_receipt_digest']}",
                    *{f"source-row:{row['row_digest']}" for row in page_rows},
                }
            ),
            "limitations": self._limitations(spec),
        }
        result["content_digest"] = digest_json(result)
        return result

    @staticmethod
    def _validate_identity(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != set(_IDENTITY_FIELDS):
            raise ToolQueryError("invalid_input", "identity fields do not match the frozen identity contract")
        identity = copy.deepcopy(dict(value))
        for field in ("incident_id", "publication_id", "cohort_id", "registry_snapshot_id"):
            if not isinstance(identity[field], str) or not identity[field]:
                raise ToolQueryError("invalid_input", f"identity.{field} must be a non-empty string")
        for field in ("publication_digest", "cohort_digest", "registry_snapshot_digest"):
            if not isinstance(identity[field], str) or _SHA256_RE.fullmatch(identity[field]) is None:
                raise ToolQueryError("invalid_input", f"identity.{field} must be sha256")
        if identity["collector_id"] != "rrc25":
            raise ToolQueryError("identity_mismatch", "collector_id must be rrc25")
        for field in ("publication_revision", "binding_generation"):
            if isinstance(identity[field], bool) or not isinstance(identity[field], int) or identity[field] < 1:
                raise ToolQueryError("invalid_input", f"identity.{field} must be a positive integer")
        if identity["finality"] not in ("event_end_unknown", "event_end_known"):
            raise ToolQueryError("invalid_input", "identity.finality is invalid")
        start = _utc(identity["window_start_utc"], "identity.window_start_utc")
        end = _utc(identity["window_end_utc"], "identity.window_end_utc")
        through = _utc(identity["data_through_utc"], "identity.data_through_utc")
        if not start <= end <= through:
            raise ToolQueryError("invalid_input", "identity time order must be window_start <= window_end <= data_through")
        return identity

    @staticmethod
    def _assert_source_identity(identity: Mapping[str, Any], source: Mapping[str, Any]) -> None:
        pairs = {
            "incident_id": "incident_id",
            "publication_id": "publication_id",
            "publication_revision": "publication_revision",
            "publication_digest": "publication_digest",
            "collector_id": "collector_id",
            "cohort_id": "cohort_id",
            "cohort_digest": "cohort_digest",
            "window_start_utc": "window_start_utc",
            "window_end_utc": "window_end_utc",
            "data_through_utc": "data_through_utc",
            "finality": "finality",
        }
        mismatches = [field for field, source_field in pairs.items() if identity[field] != source.get(source_field)]
        if mismatches:
            code = "stale_publication" if any(field.startswith("publication") for field in mismatches) else "identity_mismatch"
            raise ToolQueryError(code, f"source identity mismatch: {mismatches}")

    def _normalize_filters(
        self,
        spec: _ToolSpec,
        request: Mapping[str, Any],
        identity: Mapping[str, Any],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in spec.allowed_filters:
            if name not in request:
                continue
            value = request[name]
            if name in ("asn", "anchor_asn", "known_origin_asn", "contains_asn"):
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4294967295:
                    raise ToolQueryError("invalid_input", f"{name} must be an unsigned 32-bit ASN")
            elif name == "prefix":
                network = _network(value, name)
                value = str(network)
            elif name == "afi":
                if isinstance(value, bool) or value not in (4, 6):
                    raise ToolQueryError("invalid_input", "afi must be 4 or 6")
            elif name in ("state_point_utc",):
                self._validate_grid_time(value, name, identity)
            elif name in ("time_range_half_open", "first_observed_range_half_open"):
                value = self._normalize_time_range(value, name, identity)
            elif name == "classification":
                allowed = (
                    {"normal", "affected", "route_interrupted", "unknown"}
                    if spec.tool_id == "TOOL-09"
                    else {"normal", "partial", "complete", "unknown"}
                )
                if not isinstance(value, str) or value not in allowed:
                    raise ToolQueryError("invalid_input", f"classification is invalid for {spec.tool_id}")
            elif name in ("anchor_before_known_origin", "ordered_sequence_eligible"):
                if not isinstance(value, bool):
                    raise ToolQueryError("invalid_input", f"{name} must be boolean")
                if name == "anchor_before_known_origin" and value is False:
                    raise ToolQueryError("unsupported_filter", "anchor_before_known_origin=false has no row population")
            elif name in ("path_id", "peer_asn_direction_id"):
                if not isinstance(value, str) or not value:
                    raise ToolQueryError("invalid_input", f"{name} must be a non-empty string")
            result[name] = copy.deepcopy(value)
        if "state_point_utc" in result and "time_range_half_open" in result:
            raise ToolQueryError("invalid_input", "state_point_utc and time_range_half_open are mutually exclusive")
        if result.get("anchor_before_known_origin") is True and "anchor_asn" not in result:
            raise ToolQueryError("invalid_input", "anchor_before_known_origin=true requires anchor_asn")
        if "prefix" in result and "afi" in result:
            if ipaddress.ip_network(result["prefix"]).version != result["afi"]:
                raise ToolQueryError("invalid_input", "prefix and afi disagree")
        return result

    def _validate_grid_time(self, value: Any, location: str, identity: Mapping[str, Any]) -> None:
        point = _utc(value, location)
        start = _utc(identity["window_start_utc"], "identity.window_start_utc")
        through = _utc(identity["data_through_utc"], "identity.data_through_utc")
        grid = self._store.manifest["identity"]["grid_seconds"]
        if not start <= point <= through or int((point - start).total_seconds()) % grid != 0:
            raise ToolQueryError("invalid_input", f"{location} must be an exact available publication grid point")

    def _normalize_time_range(self, value: Any, location: str, identity: Mapping[str, Any]) -> dict[str, str]:
        if not isinstance(value, Mapping) or set(value) != {"start_utc", "end_utc"}:
            raise ToolQueryError("invalid_input", f"{location} must contain only start_utc/end_utc")
        start = _utc(value["start_utc"], f"{location}.start_utc")
        end = _utc(value["end_utc"], f"{location}.end_utc")
        window_start = _utc(identity["window_start_utc"], "identity.window_start_utc")
        through = _utc(identity["data_through_utc"], "identity.data_through_utc")
        grid = self._store.manifest["identity"]["grid_seconds"]
        if not window_start <= start < end or start > through or end > through + timedelta(seconds=grid):
            raise ToolQueryError("invalid_input", f"{location} is outside the bound publication")
        if int((start - window_start).total_seconds()) % grid != 0 or int((end - window_start).total_seconds()) % grid != 0:
            raise ToolQueryError("invalid_input", f"{location} endpoints must be publication grid points")
        return {"start_utc": value["start_utc"], "end_utc": value["end_utc"]}

    def _candidate_member_keys(
        self,
        spec: _ToolSpec,
        filters: Mapping[str, Any],
        index: Mapping[str, Any],
        identity: Mapping[str, Any],
        query_digest: str,
        identity_digest: str,
        source_context: Mapping[str, Any],
    ) -> tuple[list[str], dict[str, Any]]:
        all_keys = [entry["member_key"] for entry in index["members"]]
        if spec.tool_id != "TOOL-12":
            return all_keys, {"filter_mode": "verified_single_population_predicate_scan"}
        secondary = index.get("secondary_indexes")
        if not isinstance(secondary, Mapping):
            raise ToolQueryError("evidence_unclosed", "TOOL-12 native indexes are missing")
        anchor_index = secondary.get("anchor_before_known_origin")
        path_index = secondary.get("path_asn_membership")
        if not isinstance(anchor_index, Mapping) or not isinstance(path_index, Mapping):
            raise ToolQueryError("evidence_unclosed", "TOOL-12 native filter indexes are missing")
        if (
            anchor_index.get("filter_profile_id") != _FILTER_PROFILE_ID
            or anchor_index.get("filter_profile_digest") != _FILTER_PROFILE_DIGEST
            or path_index.get("profile_id") != _PATH_MEMBERSHIP_PROFILE_ID
            or path_index.get("profile_digest") != _PATH_MEMBERSHIP_PROFILE_DIGEST
        ):
            raise ToolQueryError("evidence_unclosed", "TOOL-12 filter profile drift")
        candidate = set(all_keys)
        target_contains = filters.get("contains_asn")
        if target_contains is not None:
            candidate &= set(path_index.get("members_by_asn", {}).get(str(target_contains), []))
        target_anchor = filters.get("anchor_asn")
        eligible = None
        if target_anchor is not None:
            eligible_asns = anchor_index.get("eligible_anchor_asns")
            if not isinstance(eligible_asns, list):
                raise ToolQueryError("evidence_unclosed", "eligible anchor closure is missing")
            eligible = target_anchor in eligible_asns
            if not eligible:
                receipt = self._query_receipt(
                    spec=spec,
                    identity=self._validate_identity(identity),
                    identity_digest=identity_digest,
                    normalized_query={"filters": copy.deepcopy(dict(filters))},
                    query_digest=query_digest,
                    stable_sort_digest=digest_json({"stable_sort": list(spec.stable_sort)}),
                    source_context=source_context,
                    matched_member_keys_digest=digest_json({"member_keys": []}),
                    matched_total_count=0,
                    page_member_keys=[],
                    page_offset=0,
                    next_offset=None,
                    disposition="unsupported_noneligible_anchor",
                    native_context=self._native_context(anchor_index, path_index, filters, False),
                )
                raise ToolQueryError(
                    "unsupported_filter",
                    "anchor ASN is outside the bound publication eligible anchor population",
                    receipt=receipt,
                )
            candidate &= set(anchor_index.get("members_by_anchor_asn", {}).get(str(target_anchor), []))
        return [key for key in all_keys if key in candidate], self._native_context(
            anchor_index, path_index, filters, eligible
        )

    @staticmethod
    def _native_context(
        anchor_index: Mapping[str, Any],
        path_index: Mapping[str, Any],
        filters: Mapping[str, Any],
        eligible: bool | None,
    ) -> dict[str, Any]:
        native_filter_applied = filters.get("contains_asn") is not None or filters.get("anchor_asn") is not None
        return {
            "filter_mode": (
                "pre_materialized_native_index"
                if native_filter_applied
                else "verified_single_population_predicate_scan"
            ),
            "native_filter_applied": native_filter_applied,
            "filter_profile_id": _FILTER_PROFILE_ID,
            "filter_profile_digest": _FILTER_PROFILE_DIGEST,
            "path_asn_membership_index_id": path_index.get("index_id"),
            "anchor_before_known_origin_index_id": anchor_index.get("index_id"),
            "anchor_population_source_ref": anchor_index.get("anchor_population_source_ref"),
            "anchor_population_manifest_digest": anchor_index.get("anchor_manifest_digest"),
            "anchor_population_content_digest": anchor_index.get("anchor_content_digest"),
            "anchor_population_freeze_digest": anchor_index.get("anchor_freeze_digest"),
            "eligible_anchor_asns_digest": anchor_index.get("eligible_anchor_asns_digest"),
            "eligible_anchor_asn_count": anchor_index.get("eligible_anchor_asn_count"),
            "association_population_source_ref": anchor_index.get("association_population_source_ref"),
            "association_manifest_digest": anchor_index.get("association_manifest_digest"),
            "association_content_digest": anchor_index.get("association_content_digest"),
            "association_freeze_digest": anchor_index.get("association_freeze_digest"),
            "association_source_row_count": anchor_index.get("association_source_row_count"),
            "association_source_rows_digest": anchor_index.get("association_source_rows_digest"),
            "association_population_complete": anchor_index.get("association_population_complete"),
            "target_contains_asn": filters.get("contains_asn"),
            "target_anchor_asn": filters.get("anchor_asn"),
            "anchor_before_known_origin": filters.get("anchor_before_known_origin"),
            "anchor_population_eligible": eligible,
            "source_row_invariants": [
                "source_native_path_status=known",
                "path_parse_status=common_path_status=ordered",
                "origin_status=known_and_observed_origin_asn=known_origin_asn",
            ],
        }

    @staticmethod
    def _assert_tool12_source_rows(rows: Sequence[Mapping[str, Any]]) -> None:
        """复验 W0 已物化路径语义；不据此选择成员或重建索引。"""

        for row in rows:
            if (
                row.get("source_native_path_status") != "known"
                or row.get("path_parse_status") != "ordered"
                or row.get("common_path_status") != "ordered"
                or row.get("origin_status") != "known"
                or row.get("observed_origin_asn") != row.get("known_origin_asn")
                or row.get("ordered_sequence_eligible") is not True
            ):
                raise ToolQueryError("evidence_unclosed", "TOOL-12 W0 materialized row invariants drifted")
            segments = row.get("path_segments")
            if not isinstance(segments, list) or not segments:
                raise ToolQueryError("evidence_unclosed", "TOOL-12 path segments are missing")
            sequence: list[int] = []
            for segment in segments:
                if not isinstance(segment, Mapping) or segment.get("segment_type") != "as_sequence":
                    raise ToolQueryError("evidence_unclosed", "TOOL-12 path is not ordered AS_SEQUENCE")
                asns = segment.get("asns")
                if not isinstance(asns, list) or not asns or any(
                    isinstance(asn, bool) or not isinstance(asn, int) for asn in asns
                ):
                    raise ToolQueryError("evidence_unclosed", "TOOL-12 path ASN sequence is invalid")
                sequence.extend(asns)
            collapsed = [
                asn for index, asn in enumerate(sequence)
                if index == 0 or sequence[index - 1] != asn
            ]
            if (
                row.get("path_digest") != digest_json(segments)
                or not collapsed
                or collapsed[-1] != row.get("known_origin_asn")
                or row.get("anchor_asn") not in collapsed[:-1]
            ):
                raise ToolQueryError("evidence_unclosed", "TOOL-12 path origin-tail or anchor-before invariant drifted")

    @staticmethod
    def _assert_runtime_snapshot(
        rows: Sequence[Mapping[str, Any]],
        index: Mapping[str, Any],
        population_manifest: Mapping[str, Any],
    ) -> None:
        """在回执签发前重算已载入对象的冻结字节摘要，阻断验证后内存替换。"""

        try:
            row_bytes = b"".join((canonical_json(row) + "\n").encode("utf-8") for row in rows)
            index_bytes = (canonical_json(index) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ToolQueryError("evidence_unclosed", "source snapshot is not canonical JSON") from exc
        row_ref = population_manifest.get("row_file")
        index_ref = population_manifest.get("index_file")
        if (
            not isinstance(row_ref, Mapping)
            or not isinstance(index_ref, Mapping)
            or len(row_bytes) != row_ref.get("size_bytes")
            or hashlib.sha256(row_bytes).hexdigest() != row_ref.get("sha256")
            or len(index_bytes) != index_ref.get("size_bytes")
            or hashlib.sha256(index_bytes).hexdigest() != index_ref.get("sha256")
        ):
            raise ToolQueryError("evidence_unclosed", "source snapshot changed after Store verification")

    @staticmethod
    def _matches(spec: _ToolSpec, row: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
        for name, value in filters.items():
            if name in ("contains_asn", "anchor_before_known_origin"):
                continue
            if name == "asn":
                if spec.tool_id == "TOOL-07":
                    if value not in row["country_origin_asns"]:
                        return False
                elif row["asn"] != value:
                    return False
            elif name == "anchor_asn" and row["anchor_asn"] != value:
                return False
            elif name == "known_origin_asn" and row["known_origin_asn"] != value:
                return False
            elif name == "prefix" and row["prefix"] != value:
                return False
            elif name == "afi" and row["afi"] != ("ipv4" if value == 4 else "ipv6"):
                return False
            elif name == "state_point_utc" and row["state_point_utc"] != value:
                return False
            elif name == "time_range_half_open":
                if not value["start_utc"] <= row["state_point_utc"] < value["end_utc"]:
                    return False
            elif name == "first_observed_range_half_open":
                if not value["start_utc"] <= row["first_observed_at_utc"] < value["end_utc"]:
                    return False
            elif name == "classification" and row["classification"] != value:
                return False
            elif name == "path_id" and row["path_id"] != value:
                return False
            elif name == "peer_asn_direction_id" and value not in row["peer_asn_direction_ids"]:
                return False
            elif name == "ordered_sequence_eligible" and row["ordered_sequence_eligible"] is not value:
                return False
        return True

    @staticmethod
    def _public_member(spec: _ToolSpec, row: Mapping[str, Any]) -> dict[str, Any]:
        member = {field: copy.deepcopy(row[field]) for field in spec.output_fields}
        if "afi" in member:
            member["afi"] = 4 if row["afi"] == "ipv4" else 6
        member["evidence_ref"] = {
            "evidence_id": f"source-member:{row['member_key']}",
            "source_digest": row["row_digest"],
            "member_key": row["member_key"],
        }
        return member

    def _query_receipt(
        self,
        *,
        spec: _ToolSpec,
        identity: Mapping[str, Any],
        identity_digest: str,
        normalized_query: Mapping[str, Any],
        query_digest: str,
        stable_sort_digest: str,
        source_context: Mapping[str, Any],
        matched_member_keys_digest: str,
        matched_total_count: int,
        page_member_keys: Sequence[str],
        page_offset: int,
        next_offset: int | None,
        disposition: str,
        native_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        receipt: dict[str, Any] = {
            "schema_version": "country_outage_p2_s1_query_receipt_v1",
            "tool_id": spec.tool_id,
            "tool_version": self.tool_version,
            "publication_id": identity["publication_id"],
            "identity_digest": identity_digest,
            "source_population_id": spec.population_id,
            **copy.deepcopy(dict(source_context)),
            "source_completeness": "complete",
            "complete_claim_label": (
                "complete_within_window_path_association_population"
                if spec.tool_id == "TOOL-12"
                else "complete_within_verified_atomic_source_population"
            ),
            "normalized_query": copy.deepcopy(dict(normalized_query)),
            "query_digest": query_digest,
            "stable_sort_digest": stable_sort_digest,
            "path_association_index_digest": source_context["source_index_digest"] if spec.tool_id == "TOOL-12" else None,
            "path_association_materialization_receipt_digest": (
                source_context["source_materialization_receipt_digest"] if spec.tool_id == "TOOL-12" else None
            ),
            "matched_member_keys_digest": matched_member_keys_digest,
            "matched_total_count": matched_total_count,
            "page_member_keys_digest": digest_json({"member_keys": list(page_member_keys)}),
            "page_returned_count": len(page_member_keys),
            "page_offset": page_offset,
            "next_offset": next_offset,
            "disposition": disposition,
            **copy.deepcopy(dict(native_context)),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        receipt["receipt_auth_tag"] = hmac.new(
            self._query_receipt_key, receipt["receipt_digest"].encode("ascii"), hashlib.sha256
        ).hexdigest()
        return receipt

    def verify_query_receipt(self, receipt: Mapping[str, Any]) -> bool:
        if not isinstance(receipt, Mapping):
            return False
        try:
            semantic = copy.deepcopy(dict(receipt))
            auth_tag = semantic.pop("receipt_auth_tag", None)
            receipt_digest = semantic.pop("receipt_digest", None)
            if not isinstance(auth_tag, str) or not isinstance(receipt_digest, str):
                return False
            if digest_json(semantic) != receipt_digest:
                return False
            expected = hmac.new(self._query_receipt_key, receipt_digest.encode("ascii"), hashlib.sha256).hexdigest()
            return hmac.compare_digest(auth_tag, expected)
        except (TypeError, ValueError, UnicodeError):
            return False

    def _encode_page_token(self, payload: Mapping[str, Any]) -> str:
        raw = canonical_json(payload).encode("utf-8")
        signature = hmac.new(self._page_token_key, raw, hashlib.sha256).digest()
        return self._b64(raw) + "." + self._b64(signature)

    def _decode_page_token(self, token: str) -> Mapping[str, Any]:
        if not token or len(token) > 8192 or token.count(".") != 1:
            raise ToolQueryError("invalid_page_token", "page token encoding is invalid")
        payload_text, signature_text = token.split(".", 1)
        try:
            raw = self._unb64(payload_text)
            signature = self._unb64(signature_text)
        except (ValueError, UnicodeError) as exc:
            raise ToolQueryError("invalid_page_token", "page token encoding is invalid") from exc
        expected = hmac.new(self._page_token_key, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ToolQueryError("invalid_page_token", "page token authentication failed")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ToolQueryError("invalid_page_token", "page token payload is invalid") from exc
        if not isinstance(value, dict) or canonical_json(value).encode("utf-8") != raw:
            raise ToolQueryError("invalid_page_token", "page token payload is not canonical")
        return value

    def _validate_page_token(
        self,
        token: Mapping[str, Any],
        *,
        spec: _ToolSpec,
        identity_digest: str,
        query_digest: str,
        stable_sort_digest: str,
        source_dataset_digest: str,
        page_size: int,
    ) -> None:
        if token.get("identity_digest") != identity_digest:
            raise ToolQueryError("page_token_identity_mismatch", "page token belongs to another identity")
        expected = {
            "schema_version": "country_outage_p2_s1_page_token_v1",
            "tool_id": spec.tool_id,
            "tool_version": self.tool_version,
            "query_digest": query_digest,
            "stable_sort_digest": stable_sort_digest,
            "source_dataset_digest": source_dataset_digest,
            "page_size": page_size,
        }
        if any(token.get(field) != value for field, value in expected.items()):
            raise ToolQueryError("invalid_page_token", "page token query/sort/source/page size mismatch")
        required = {*expected, "identity_digest", "last_member_key", "last_member_sort_key", "next_offset"}
        if set(token) != required:
            raise ToolQueryError("invalid_page_token", "page token fields are invalid")
        if not isinstance(token["last_member_key"], str) or not isinstance(token["last_member_sort_key"], list):
            raise ToolQueryError("invalid_page_token", "page token cursor is invalid")
        if isinstance(token["next_offset"], bool) or not isinstance(token["next_offset"], int) or token["next_offset"] < 1:
            raise ToolQueryError("invalid_page_token", "page token offset is invalid")

    @staticmethod
    def _b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _unb64(value: str) -> bytes:
        if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
            raise ValueError("invalid base64url")
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if CountryOutageP2S1Tools._b64(decoded) != value:
            raise ValueError("non-canonical base64url")
        return decoded

    @staticmethod
    def _limitations(spec: _ToolSpec) -> list[str]:
        common = [
            "single_rrc25_publication_control_plane_only",
            "data_through_does_not_prove_event_end_or_recovery",
            "tool_returns_one_pre_materialized_fact_population_without_join_or_business_derivation",
        ]
        if spec.tool_id == "TOOL-12":
            common.extend(
                [
                    "complete_only_within_window_path_association_population",
                    "window_level_association_not_path_at_time",
                    "observed_downstream_is_not_customer_cone_or_business_relationship",
                ]
            )
        return common
