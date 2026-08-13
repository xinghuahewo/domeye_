"""RRC25 P2-S1 source view 的只读完整性解析器。

该类只验证并载入一个原子事实人口，不提供 ASN/prefix/time 查询、排序、Join 或调查语义。
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


EXPECTED_POPULATIONS = (
    "fixed_cohort_member_rows",
    "prefix_state_rows",
    "asn_state_rows",
    "new_prefix_state_rows",
    "materialized_route_state_rows_at_exact_time",
    "window_path_association_evidence_rows",
)
EXPECTED_SCHEMAS = {
    "fixed_cohort_member_rows": ("fixed-cohort-member-row.schema.json", "country_outage_p2_s1_fixed_cohort_member_row_v1"),
    "prefix_state_rows": ("prefix-state-row.schema.json", "country_outage_p2_s1_prefix_state_row_v1"),
    "asn_state_rows": ("asn-state-row.schema.json", "country_outage_p2_s1_asn_state_row_v1"),
    "new_prefix_state_rows": ("new-prefix-state-row.schema.json", "country_outage_p2_s1_new_prefix_state_row_v1"),
    "materialized_route_state_rows_at_exact_time": ("materialized-route-state-row.schema.json", "country_outage_p2_s1_materialized_route_state_row_v1"),
    "window_path_association_evidence_rows": ("window-path-association-row.schema.json", "country_outage_p2_s1_window_path_association_row_v1"),
}
MEMBER_KEY_FIELDS = {
    "fixed_cohort_member_rows": ("publication_id", "cohort_id", "cohort_member_id"),
    "prefix_state_rows": ("publication_id", "state_point_utc", "prefix", "afi"),
    "asn_state_rows": ("publication_id", "state_point_utc", "asn"),
    "new_prefix_state_rows": ("publication_id", "new_prefix_state_id", "state_point_utc"),
    "materialized_route_state_rows_at_exact_time": ("publication_id", "state_point_utc", "route_observation_key"),
    "window_path_association_evidence_rows": ("publication_id", "path_association_id"),
}
ROW_FIELDS = {
    "fixed_cohort_member_rows": ("schema_version", "publication_id", "cohort_id", "cohort_member_id", "prefix", "afi", "country_origin_asns", "expected_peer_asn_direction_ids", "expected_route_observation_keys", "membership_basis", "member_key", "row_digest", "source_record_refs"),
    "prefix_state_rows": ("schema_version", "publication_id", "cohort_id", "state_point_utc", "prefix", "afi", "classification", "expected_direction_count", "visible_direction_count", "invisible_direction_count", "unknown_direction_count", "member_key", "row_digest", "source_record_refs"),
    "asn_state_rows": ("schema_version", "publication_id", "cohort_id", "state_point_utc", "asn", "classification", "fixed_prefix_count", "partial_prefix_count", "complete_prefix_count", "unknown_prefix_count", "invisible_direction_count", "member_key", "row_digest", "source_record_refs"),
    "new_prefix_state_rows": ("schema_version", "publication_id", "new_prefix_state_id", "prefix", "afi", "first_observed_at_utc", "state_point_utc", "classification", "expected_peer_asn_direction_ids", "visible_direction_count", "invisible_direction_count", "unknown_direction_count", "projection_profile_id", "projection_profile_digest", "member_key", "row_digest", "source_record_refs"),
    "materialized_route_state_rows_at_exact_time": ("schema_version", "publication_id", "state_point_utc", "prefix", "afi", "peer_asn_direction_id", "route_observation_key", "vp_id", "peer_id", "visibility", "origin_status", "origin_asns", "path_status", "common_path_status", "path_id", "path_digest", "path_canonicalization_profile_id", "path_canonicalization_profile_digest", "path_segments", "last_event_id", "last_update_utc", "checkpoint_id", "projection_receipt_digest", "quality_flags", "member_key", "row_digest", "source_record_refs"),
    "window_path_association_evidence_rows": ("schema_version", "publication_id", "path_association_id", "anchor_asn", "known_origin_asn", "origin_status", "observed_origin_asn", "prefix", "afi", "path_id", "path_digest", "path_canonicalization_profile_id", "path_canonicalization_profile_digest", "path_segments", "source_native_path_status", "path_parse_status", "common_path_status", "ordered_sequence_eligible", "peer_asn_direction_ids", "route_observation_count", "member_key", "row_digest", "source_record_refs"),
}
FROZEN_SOURCE_PROFILE_DIGESTS = {
    "PROFILE-NEW-PREFIX-FIXED-FIRST-OBSERVED-DIRECTIONS-1.0.0": "e6518772ed2866d80e586b6506be0b8217074c610d4189313229fad088c0feeb",
    "PROFILE-EXACT-ROUTE-STATE-AUTHORITATIVE-INPUT-1.0.0": "bfcee78e2bcb4b5fed159bb8da545f37518150480147754a89d4063ba66daa6f",
}


class SourceStoreIntegrityError(ValueError):
    """Source store 的身份、摘要、成员或只读边界不可信。"""


class SourcePopulationUnavailable(SourceStoreIntegrityError):
    """人口被明确标记为 source semantics 未就绪。"""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _no_duplicate_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceStoreIntegrityError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_json_types(value: Any, location: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SourceStoreIntegrityError(f"non-finite number at {location}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_non_json_types(item, f"{location}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_non_json_types(item, f"{location}.{key}")
        return
    raise SourceStoreIntegrityError(f"non-JSON type at {location}")


def _parse_canonical_json(raw: bytes, location: str) -> Any:
    try:
        text = raw.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_no_duplicate_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SourceStoreIntegrityError(f"invalid JSON at {location}: {exc}") from exc
    _reject_non_json_types(value, location)
    if raw != (canonical_json(value) + "\n").encode("utf-8"):
        raise SourceStoreIntegrityError(f"non-canonical JSON at {location}")
    return value


class CountryOutageP2S1SourceStore:
    """验证内容寻址 source store；不解释任何调查业务语义。"""

    def __init__(self, root: Path | str, *, contract_root: Path | str | None = None) -> None:
        self.root = self._trusted_directory(Path(root), "source store root")
        self.contract_root = self._trusted_directory(
            Path(contract_root)
            if contract_root is not None
            else Path(__file__).resolve().parents[2] / "contracts/data/country-outage-p2-s1",
            "source contract root",
        )
        self._manifest: Mapping[str, Any] | None = None
        self._verified_rows: dict[str, tuple[Mapping[str, Any], ...]] = {}
        self._verified_indexes: dict[str, Mapping[str, Any]] = {}

    @staticmethod
    def _trusted_directory(path: Path, location: str) -> Path:
        """拒绝调用方传入的最终根本身为符号链接。"""

        absolute = path.absolute()
        try:
            if not absolute.is_dir() or absolute.is_symlink():
                raise SourceStoreIntegrityError(f"unsafe directory at {location}")
        except OSError as exc:
            raise SourceStoreIntegrityError(f"unsafe directory at {location}") from exc
        # macOS 的 /var 本身映射到 /private/var；只在确认调用方最终根不是
        # symlink 后规范化系统祖先，避免把受控临时目录全部误拒绝。
        return absolute.resolve()

    def _safe_path(self, relative: Any, location: str) -> Path:
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise SourceStoreIntegrityError(f"unsafe path at {location}")
        lexical = self.root / relative
        path = lexical.resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise SourceStoreIntegrityError(f"path escapes store at {location}") from exc
        current = self.root
        for part in Path(relative).parts:
            if part in ("", ".", ".."):
                raise SourceStoreIntegrityError(f"unsafe path at {location}")
            current = current / part
            if current.is_symlink():
                raise SourceStoreIntegrityError(f"symlink forbidden at {location}")
        if lexical != path or path.is_symlink() or not path.is_file():
            raise SourceStoreIntegrityError(f"symlink forbidden at {location}")
        return path

    @staticmethod
    def _verify_content_digest(value: Mapping[str, Any], location: str) -> None:
        expected = value.get("content_sha256")
        if not isinstance(expected, str):
            raise SourceStoreIntegrityError(f"missing content digest at {location}")
        semantic = dict(value)
        semantic.pop("content_sha256", None)
        if digest_json(semantic) != expected:
            raise SourceStoreIntegrityError(f"content digest mismatch at {location}")

    def _read_file(self, relative: Any, expected_sha: Any, expected_size: Any, location: str) -> bytes:
        path = self._safe_path(relative, location)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise SourceStoreIntegrityError(f"missing file at {location}") from exc
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or len(raw) != expected_size:
            raise SourceStoreIntegrityError(f"size mismatch at {location}")
        if not isinstance(expected_sha, str) or hashlib.sha256(raw).hexdigest() != expected_sha:
            raise SourceStoreIntegrityError(f"file digest mismatch at {location}")
        return raw

    def verify(self) -> Mapping[str, Any]:
        raw = self._safe_path("manifest.json", "manifest.json").read_bytes()
        manifest = _parse_canonical_json(raw, "manifest.json")
        if not isinstance(manifest, dict) or manifest.get("schema_version") != "country_outage_p2_s1_source_store_manifest_v1":
            raise SourceStoreIntegrityError("manifest schema mismatch")
        self._verify_content_digest(manifest, "manifest.json")
        if not isinstance(manifest.get("store_id"), str) or not manifest["store_id"].startswith("country_outage_p2_s1_source_store_v1_"):
            raise SourceStoreIntegrityError("store_id invalid")
        store_semantic = dict(manifest)
        store_id = store_semantic.pop("store_id")
        store_semantic.pop("content_sha256")
        if store_id != "country_outage_p2_s1_source_store_v1_" + digest_json(store_semantic):
            raise SourceStoreIntegrityError("store_id digest mismatch")
        identity = manifest.get("identity")
        required_identity = {
            "incident_id", "publication_id", "publication_revision", "publication_digest",
            "collector_id", "cohort_id", "cohort_digest", "window_start_utc",
            "window_end_utc", "data_through_utc", "finality", "grid_seconds",
        }
        if (
            not isinstance(identity, dict)
            or set(identity) != required_identity
            or identity.get("collector_id") != "rrc25"
            or not isinstance(identity.get("publication_id"), str)
        ):
            raise SourceStoreIntegrityError("manifest identity invalid")
        profile_ref = manifest.get("source_profiles_ref")
        if not isinstance(profile_ref, dict):
            raise SourceStoreIntegrityError("source profile ref missing")
        profile_raw = self._safe_path(profile_ref.get("path"), "source_profiles_ref").read_bytes()
        if hashlib.sha256(profile_raw).hexdigest() != profile_ref.get("sha256"):
            raise SourceStoreIntegrityError("source profile digest mismatch")
        profiles = _parse_canonical_json(profile_raw, "source-profiles.json")
        if not isinstance(profiles, dict) or profiles.get("schema_version") != "country_outage_p2_s1_source_profiles_v1":
            raise SourceStoreIntegrityError("source profile schema mismatch")
        observed_profiles = {}
        for profile in profiles.get("profiles", []):
            if not isinstance(profile, dict) or not isinstance(profile.get("profile_id"), str):
                raise SourceStoreIntegrityError("source profile entry invalid")
            semantic = dict(profile)
            profile_digest = semantic.pop("profile_digest", None)
            if profile_digest != digest_json(semantic):
                raise SourceStoreIntegrityError("source profile internal digest mismatch")
            observed_profiles[profile["profile_id"]] = profile_digest
        if observed_profiles != FROZEN_SOURCE_PROFILE_DIGESTS:
            raise SourceStoreIntegrityError("frozen source profile set mismatch")
        populations = manifest.get("population_manifests")
        if not isinstance(populations, list) or len(populations) != 6:
            raise SourceStoreIntegrityError("population manifest count must be six")
        ids = [item.get("population_id") if isinstance(item, dict) else None for item in populations]
        if tuple(ids) != EXPECTED_POPULATIONS:
            raise SourceStoreIntegrityError("population manifests must contain six ordered atomic populations")
        for entry in populations:
            self._verify_population_entry(entry, identity)
        self._manifest = manifest
        return manifest

    def _verify_population_entry(self, entry: Mapping[str, Any], identity: Mapping[str, Any]) -> None:
        population_id = entry["population_id"]
        expected_schema, row_schema_version = EXPECTED_SCHEMAS[population_id]
        if entry.get("schema_ref") != expected_schema:
            raise SourceStoreIntegrityError(f"schema ref mismatch: {population_id}")
        schema_path = self.contract_root / expected_schema
        if (
            schema_path.parent != self.contract_root
            or schema_path.is_symlink()
            or not schema_path.is_file()
            or hashlib.sha256(schema_path.read_bytes()).hexdigest() != entry.get("schema_sha256")
        ):
            raise SourceStoreIntegrityError(f"schema digest mismatch: {population_id}")
        readiness = entry.get("readiness")
        codes = entry.get("blocking_codes")
        if readiness == "blocked_source_semantics":
            if not isinstance(codes, list) or not codes:
                raise SourceStoreIntegrityError(f"blocked population lacks codes: {population_id}")
            raise SourcePopulationUnavailable(f"population blocked: {population_id}: {codes}")
        if readiness != "ready" or codes != []:
            raise SourceStoreIntegrityError(f"invalid readiness contract: {population_id}")
        for ref in entry.get("source_refs", []):
            if not isinstance(ref, dict) or ref.get("publication_id") != identity["publication_id"]:
                raise SourceStoreIntegrityError(f"source ref identity mismatch: {population_id}")
        row_ref = entry.get("row_file")
        index_ref = entry.get("index_file")
        if not isinstance(row_ref, dict) or not isinstance(index_ref, dict):
            raise SourceStoreIntegrityError(f"file refs missing: {population_id}")
        row_raw = self._read_file(row_ref.get("path"), row_ref.get("sha256"), row_ref.get("size_bytes"), f"{population_id}.rows")
        rows = self._parse_rows(row_raw, population_id, row_schema_version, identity["publication_id"])
        if len(rows) != entry.get("row_count"):
            raise SourceStoreIntegrityError(f"row count mismatch: {population_id}")
        index_raw = self._read_file(index_ref.get("path"), index_ref.get("sha256"), index_ref.get("size_bytes"), f"{population_id}.index")
        index = _parse_canonical_json(index_raw, f"{population_id}.index")
        if not isinstance(index, dict) or index.get("population_id") != population_id or index.get("publication_id") != identity["publication_id"]:
            raise SourceStoreIntegrityError(f"index identity mismatch: {population_id}")
        self._verify_content_digest(index, f"{population_id}.index")
        if index.get("member_key_fields") != list(MEMBER_KEY_FIELDS[population_id]):
            raise SourceStoreIntegrityError(f"member key fields mismatch: {population_id}")
        expected_members = [{"member_key": row["member_key"], "row_ordinal": ordinal, "row_digest": row["row_digest"]} for ordinal, row in enumerate(rows)]
        if index.get("members") != expected_members:
            raise SourceStoreIntegrityError(f"index members mismatch: {population_id}")
        member_keys_digest = digest_json([row["member_key"] for row in rows])
        if index.get("member_keys_digest") != member_keys_digest or entry.get("member_keys_digest") != member_keys_digest:
            raise SourceStoreIntegrityError(f"member key closure mismatch: {population_id}")
        receipt_ref = entry.get("materialization_receipt_ref")
        receipt_path = self._safe_path(receipt_ref, f"{population_id}.receipt")
        receipt_raw = receipt_path.read_bytes()
        receipt = _parse_canonical_json(receipt_raw, f"{population_id}.receipt")
        if not isinstance(receipt, dict):
            raise SourceStoreIntegrityError(f"receipt invalid: {population_id}")
        self._verify_content_digest(receipt, f"{population_id}.receipt")
        receipt_semantic = dict(receipt)
        receipt_semantic.pop("content_sha256", None)
        receipt_id = receipt_semantic.pop("receipt_id", None)
        if receipt_id != "p2s1_materialization_receipt_v1_" + digest_json(receipt_semantic):
            raise SourceStoreIntegrityError(f"receipt_id digest mismatch: {population_id}")
        if receipt.get("content_sha256") != entry.get("materialization_receipt_digest") or receipt.get("population_id") != population_id or receipt.get("publication_id") != identity["publication_id"]:
            raise SourceStoreIntegrityError(f"receipt binding mismatch: {population_id}")
        if receipt_ref != f"receipts/{receipt['content_sha256']}.json":
            raise SourceStoreIntegrityError(f"receipt path is not content addressed: {population_id}")
        if receipt.get("row_file_sha256") != row_ref.get("sha256") or receipt.get("index_digest") != index.get("content_sha256") or receipt.get("member_keys_digest") != member_keys_digest or receipt.get("row_count") != len(rows) or receipt.get("schema_sha256") != entry.get("schema_sha256"):
            raise SourceStoreIntegrityError(f"receipt completeness mismatch: {population_id}")
        self._verify_special_index(population_id, rows, index.get("secondary_indexes"), receipt)
        self._verified_rows[population_id] = tuple(rows)
        self._verified_indexes[population_id] = index

    @staticmethod
    def _parse_rows(raw: bytes, population_id: str, schema_version: str, publication_id: str) -> list[Mapping[str, Any]]:
        rows = []
        if raw:
            for ordinal, line in enumerate(raw.splitlines(keepends=True)):
                row = _parse_canonical_json(line, f"{population_id}.row[{ordinal}]")
                if not isinstance(row, dict) or row.get("schema_version") != schema_version or row.get("publication_id") != publication_id:
                    raise SourceStoreIntegrityError(f"row identity mismatch: {population_id}[{ordinal}]")
                if set(row) != set(ROW_FIELDS[population_id]):
                    raise SourceStoreIntegrityError(f"row fields mismatch: {population_id}[{ordinal}]")
                semantic = dict(row)
                row_digest = semantic.pop("row_digest", None)
                if digest_json(semantic) != row_digest:
                    raise SourceStoreIntegrityError(f"row digest mismatch: {population_id}[{ordinal}]")
                expected_member_key = "p2s1_member_v1_" + digest_json(
                    [population_id, [row[field] for field in MEMBER_KEY_FIELDS[population_id]]]
                )
                if row.get("member_key") != expected_member_key:
                    raise SourceStoreIntegrityError(f"member key digest mismatch: {population_id}[{ordinal}]")
                rows.append(row)
        member_keys = [row.get("member_key") for row in rows]
        if member_keys != sorted(member_keys) or len(set(member_keys)) != len(member_keys):
            raise SourceStoreIntegrityError(f"row member order/uniqueness mismatch: {population_id}")
        return rows

    @staticmethod
    def _verify_special_index(
        population_id: str,
        rows: Sequence[Mapping[str, Any]],
        secondary: Any,
        receipt: Mapping[str, Any],
    ) -> None:
        if not isinstance(secondary, dict):
            raise SourceStoreIntegrityError(f"secondary index invalid: {population_id}")
        profile_bindings = receipt.get("profile_bindings")
        source_binding = secondary.get("source_population_binding")
        if (
            not isinstance(profile_bindings, dict)
            or not isinstance(source_binding, dict)
            or profile_bindings.get("source_population_binding") != source_binding
            or source_binding.get("source_population_complete") is not True
            or source_binding.get("materialized_row_count") != len(rows)
        ):
            raise SourceStoreIntegrityError(f"source population completeness binding mismatch: {population_id}")
        cardinality = source_binding.get("source_population_projection_cardinality")
        if cardinality not in {"one_to_one", "one_to_many_materialized_projection"}:
            raise SourceStoreIntegrityError(f"source population cardinality contract invalid: {population_id}")
        if cardinality == "one_to_one" and source_binding.get("source_population_row_count") != len(rows):
            raise SourceStoreIntegrityError(f"source population one-to-one row count mismatch: {population_id}")
        for field in (
            "source_population_manifest_digest", "source_population_content_digest",
            "source_population_freeze_digest", "source_population_rows_digest",
        ):
            value = source_binding.get(field)
            if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise SourceStoreIntegrityError(f"source population digest invalid: {population_id}.{field}")
        source_ref = next(
            (
                ref for ref in receipt.get("source_refs", [])
                if isinstance(ref, dict)
                and ref.get("dataset_id") == source_binding.get("source_population_source_ref")
                and ref.get("manifest_sha256") == source_binding.get("source_population_manifest_digest")
                and ref.get("content_sha256") == source_binding.get("source_population_content_digest")
            ),
            None,
        )
        if source_ref is None:
            raise SourceStoreIntegrityError(f"source population authoritative ref mismatch: {population_id}")
        if population_id == "materialized_route_state_rows_at_exact_time":
            membership = secondary.get("path_asn_membership")
            if not isinstance(membership, dict) or membership.get("profile_digest") != "28acec6edd232fd9aa38885175bcd715b9ea72f240efca6b3c5b7080394655e2":
                raise SourceStoreIntegrityError("RouteState path membership index missing")
            expected: dict[str, list[str]] = {}
            for row in rows:
                if row["visibility"] == "visible" and row["common_path_status"] in ("ordered", "unordered"):
                    for asn in sorted({asn for segment in row["path_segments"] for asn in segment["asns"]}):
                        expected.setdefault(str(asn), []).append(row["member_key"])
            if membership.get("members_by_asn") != {key: sorted(value) for key, value in sorted(expected.items(), key=lambda item: int(item[0]))}:
                raise SourceStoreIntegrityError("RouteState path membership index content mismatch")
        if population_id == "window_path_association_evidence_rows":
            for row in rows:
                if (
                    row.get("source_native_path_status") != "known"
                    or row.get("path_parse_status") != "ordered"
                    or row.get("common_path_status") != "ordered"
                    or row.get("ordered_sequence_eligible") is not True
                    or row.get("origin_status") != "known"
                    or row.get("observed_origin_asn") != row.get("known_origin_asn")
                ):
                    raise SourceStoreIntegrityError("window path status projection mismatch")
                segments = row.get("path_segments")
                if not isinstance(segments, list) or not segments:
                    raise SourceStoreIntegrityError("window path segments missing")
                sequence: list[int] = []
                for segment in segments:
                    if not isinstance(segment, dict) or segment.get("segment_type") != "as_sequence":
                        raise SourceStoreIntegrityError("window path is not ordered AS_SEQUENCE")
                    asns = segment.get("asns")
                    if not isinstance(asns, list) or not asns or any(
                        isinstance(asn, bool) or not isinstance(asn, int) or not (0 <= asn <= 4294967295)
                        for asn in asns
                    ):
                        raise SourceStoreIntegrityError("window path ASN sequence invalid")
                    sequence.extend(asns)
                if row.get("path_digest") != digest_json(segments):
                    raise SourceStoreIntegrityError("window path digest mismatch")
                collapsed = [
                    asn for index, asn in enumerate(sequence)
                    if index == 0 or sequence[index - 1] != asn
                ]
                if (
                    not collapsed
                    or collapsed[-1] != row.get("known_origin_asn")
                    or row.get("anchor_asn") not in collapsed[:-1]
                ):
                    raise SourceStoreIntegrityError("window origin tail or anchor-before invariant mismatch")
            membership = secondary.get("path_asn_membership")
            if (
                not isinstance(membership, dict)
                or membership.get("profile_id") != "PROFILE-PATH-ASN-MEMBERSHIP-1.0.0"
                or membership.get("profile_digest") != "28acec6edd232fd9aa38885175bcd715b9ea72f240efca6b3c5b7080394655e2"
            ):
                raise SourceStoreIntegrityError("window path membership index missing")
            expected_membership: dict[str, list[str]] = {}
            for row in rows:
                if row["ordered_sequence_eligible"] and row["common_path_status"] == "ordered":
                    for asn in sorted({asn for segment in row["path_segments"] for asn in segment["asns"]}):
                        expected_membership.setdefault(str(asn), []).append(row["member_key"])
            if membership.get("members_by_asn") != {
                key: sorted(value)
                for key, value in sorted(expected_membership.items(), key=lambda item: int(item[0]))
            }:
                raise SourceStoreIntegrityError("window path membership index content mismatch")
            anchor = secondary.get("anchor_before_known_origin")
            if not isinstance(anchor, dict) or anchor.get("filter_profile_digest") != "46ca0955b30a4d43088c214ec5bdf84fbf9b65987bd65047257e85e1d7778eb7":
                raise SourceStoreIntegrityError("window anchor-before index missing")
            expected: dict[str, list[str]] = {}
            for row in rows:
                expected.setdefault(str(row["anchor_asn"]), []).append(row["member_key"])
            if anchor.get("members_by_anchor_asn") != {key: sorted(value) for key, value in sorted(expected.items(), key=lambda item: int(item[0]))}:
                raise SourceStoreIntegrityError("window anchor-before index content mismatch")
            eligible = anchor.get("eligible_anchor_asns")
            if anchor.get("eligible_anchor_asn_count") != len(eligible or []) or anchor.get("eligible_anchor_asns_digest") != digest_json(eligible or []):
                raise SourceStoreIntegrityError("eligible anchor population closure mismatch")
            profile = profile_bindings
            if not isinstance(profile, dict) or profile.get("association_population_complete") is not True:
                raise SourceStoreIntegrityError("window association population completeness receipt missing")
            if profile.get("association_source_row_count") != len(rows):
                raise SourceStoreIntegrityError("window association population row count mismatch")
            for field in (
                "association_population_source_ref", "association_manifest_digest",
                "association_content_digest", "association_freeze_digest",
                "association_source_rows_digest",
            ):
                if anchor.get(field) != profile.get(field):
                    raise SourceStoreIntegrityError("window association population binding mismatch")
            source_ref = next(
                (
                    ref for ref in receipt.get("source_refs", [])
                    if isinstance(ref, dict) and ref.get("source_kind") == "window_path_association"
                ),
                None,
            )
            if (
                not isinstance(source_ref, dict)
                or source_ref.get("dataset_id") != profile.get("association_population_source_ref")
                or source_ref.get("manifest_sha256") != profile.get("association_manifest_digest")
                or source_ref.get("content_sha256") != profile.get("association_content_digest")
            ):
                raise SourceStoreIntegrityError("window association authoritative source binding mismatch")

    @property
    def manifest(self) -> Mapping[str, Any]:
        if self._manifest is None:
            self.verify()
        assert self._manifest is not None
        return self._manifest

    def load_population(self, population_id: str) -> tuple[Mapping[str, Any], ...]:
        """载入完整单一人口；该方法故意不接受过滤、排序或分页参数。"""
        if population_id not in EXPECTED_POPULATIONS:
            raise KeyError(population_id)
        if self._manifest is None:
            self.verify()
        return self._verified_rows[population_id]

    def load_index(self, population_id: str) -> Mapping[str, Any]:
        """返回已通过同一完整性闭包的单人口索引；不执行过滤或业务计算。"""

        if population_id not in EXPECTED_POPULATIONS:
            raise KeyError(population_id)
        if self._manifest is None:
            self.verify()
        return self._verified_indexes[population_id]
