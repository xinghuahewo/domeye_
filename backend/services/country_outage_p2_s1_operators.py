"""RRC25 国家中断 P2-S1 的纯确定性原子 Operator。

本模块只做内存中的登记业务变换，不读取文件、网络、数据库或其他 Operator。
调用方必须传入已经绑定单一 publication 的输入 Envelope；Evidence 无法由冻结
Payload 表达时，通过 ``inherited_evidence_refs`` 显式继承。缺少这种继承将 fail
closed，而不会用 digest 伪造 Evidence。
"""

from __future__ import annotations

from datetime import datetime, timezone
from fractions import Fraction
from hashlib import sha256
from ipaddress import ip_network
import json
from typing import Any, Callable, Iterable, Mapping, Sequence


OPERATOR_VERSION = "1.0.0-design"
PATH_PROFILE_ID = "AS-PATH-CANONICALIZATION-1.0.0"
PATH_PROFILE_DIGEST = "eb4d2081ee69ab0254b7af461122cf315b6bcdf24551c22de7e8dccc6d965966"
DESIGN_CANDIDATE_ID = "country-outage-p2-s1-s1d-6-04135cee55b39ce5d574f7e4"
STRUCTURAL_VALIDATOR_ID = "country_outage_p2_s1_structural_binding_validator"

_PROFILE_BY_OPERATOR: dict[str, str | None] = {
    "OP-05": "PROFILE-AS-SEVERITY-RANK-1.0.0",
    "OP-06": "PROFILE-STATE-TARGET-1.0.0",
    "OP-07": "PROFILE-STATE-INTERVAL-1.0.0",
    "OP-08": None,
    "OP-09": "PROFILE-PEAK-SEVERITY-1.0.0",
    "OP-10": None,
    "OP-11": None,
    "OP-12": "PROFILE-FIRST-CROSSING-1.0.0",
    "OP-13": "PROFILE-STATE-INTERVAL-1.0.0",
    "OP-14": None,
    **{f"OP-{number:02d}": None for number in range(15, 29)},
    "OP-35": "PROFILE-STATE-TARGET-1.0.0",
    "OP-36": "PROFILE-FIRST-CROSSING-1.0.0",
}

_STATES = {"normal", "partial", "complete", "affected", "route_interrupted", "unknown"}
_DIGEST_CHARS = frozenset("0123456789abcdef")
_RESULT_FIELDS: dict[str, frozenset[str]] = {
    "OP-05": frozenset(("ordered_asns", "ranked_members", "rank_groups", "sort_profile_id", "input_digest", "evidence_refs")),
    "OP-06": frozenset(("outcome", "state_point_utc", "left_censored", "source_member_key", "input_digest", "evidence_refs")),
    "OP-07": frozenset(("intervals", "interval_count", "target_state", "grid_step_seconds", "window", "series_digest", "input_digest", "evidence_refs")),
    "OP-08": frozenset(("outcome", "state_point_utc", "classification", "source_member_key", "input_digest", "evidence_refs")),
    "OP-09": frozenset(("outcome", "peak_value", "peak_state_points", "input_digest", "evidence_refs")),
    "OP-10": frozenset(("asn", "numerator", "denominator", "ratio_exact", "outcome", "input_digest", "evidence_refs")),
    "OP-11": frozenset(("outcome", "duration_seconds", "intervals", "input_digest", "evidence_refs")),
    "OP-12": frozenset(("ranked", "unranked_left_censored", "unranked_no_crossing", "unranked_indeterminate_gap", "input_digest", "evidence_refs")),
    "OP-13": frozenset(("ranked", "unranked", "input_digest", "evidence_refs")),
    "OP-14": frozenset(("ranked", "unranked_zero_denominator", "input_digest", "evidence_refs")),
    "OP-15": frozenset(("outcome", "target_asn", "ordered_positions", "path_digest", "path_canonicalization_profile_id", "path_canonicalization_profile_digest", "input_digest", "evidence_refs", "edge_projection")),
    "OP-16": frozenset(("outcome", "target_asn", "left_neighbors", "right_neighbors", "path_digest", "position_receipt_digest", "evidence_refs", "edge_projections")),
    "OP-17": frozenset(("outcome", "relation", "witness_position_pairs", "path_digest", "input_digest", "evidence_refs")),
    "OP-18": frozenset(("members", "member_count", "set_digest", "input_digest", "evidence_refs")),
    "OP-19": frozenset(("anchor_asn", "members", "member_contributions", "member_count", "set_digest", "input_digest", "evidence_refs")),
    "OP-20": frozenset(("members", "member_count", "canonicalization_profile_digest", "set_digest", "evidence_refs")),
    "OP-21": frozenset(("members", "member_count", "direction_identity_profile_digest", "set_digest", "evidence_refs")),
    "OP-22": frozenset(("count", "input_set_digest", "evidence_refs")),
    "OP-23": frozenset(("count", "input_set_digest", "evidence_refs")),
    "OP-24": frozenset(("count", "input_set_digest", "evidence_refs")),
    "OP-25": frozenset(("members", "member_count", "left_digest", "right_digest", "set_digest", "evidence_refs", "edge_projection")),
    "OP-26": frozenset(("direction", "members", "member_count", "left_digest", "right_digest", "set_digest", "evidence_refs")),
    "OP-27": frozenset(("direction", "intersection_count", "denominator_count", "ratio_exact", "outcome", "left_digest", "right_digest", "evidence_refs", "edge_projection")),
    "OP-28": frozenset(("intersection_count", "union_count", "ratio_exact", "outcome", "left_digest", "right_digest", "evidence_refs")),
    "OP-35": frozenset(("outcome", "state_point_utc", "right_censored", "source_member_key", "input_digest", "evidence_refs")),
    "OP-36": frozenset(("outcome", "crossing_time_utc", "previous_time_utc", "previous_value", "crossing_value", "profile_digest", "input_digest", "evidence_refs")),
}


class OperatorContractError(ValueError):
    """输入不能满足冻结 Operator 合同时的 fail-closed 异常。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class OfflineStructuralFixtureContext:
    """离线合同测试使用的内容寻址结构夹具；不构成运行时 Host 信任。

    W1/W2 Registry 明确不给这些函数执行授权。W5 的受信 dispatcher 必须从
    Host-owned Store 解析同型回执并构造运行时解析器；普通请求不得携带本对象。
    """

    def __init__(
        self,
        *,
        design_candidate_id: str,
        op10_outputs: Mapping[str, Mapping[str, Any]] | None = None,
        op11_outputs: Mapping[str, Mapping[str, Any]] | None = None,
        op15_outputs: Mapping[str, Mapping[str, Any]] | None = None,
        op36_outputs: Mapping[str, Mapping[str, Any]] | None = None,
        tool12_result_sets: Mapping[str, Mapping[str, Any]] | None = None,
        projection_receipts: Mapping[str, Mapping[str, Any]] | None = None,
        population_binding_receipts: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        _require(design_candidate_id == DESIGN_CANDIDATE_ID, "design_candidate_mismatch")
        self.design_candidate_id = design_candidate_id
        self._op10_outputs = dict(op10_outputs or {})
        self._op11_outputs = dict(op11_outputs or {})
        self._op15_outputs = dict(op15_outputs or {})
        self._op36_outputs = dict(op36_outputs or {})
        self._tool12_result_sets = dict(tool12_result_sets or {})
        self._projection_receipts = dict(projection_receipts or {})
        self._population_binding_receipts = dict(population_binding_receipts or {})

    def resolve_op10_output(self, content_digest: str) -> Mapping[str, Any] | None:
        return self._op10_outputs.get(content_digest)

    def resolve_op11_output(self, content_digest: str) -> Mapping[str, Any] | None:
        return self._op11_outputs.get(content_digest)

    def resolve_op15_output(self, content_digest: str) -> Mapping[str, Any] | None:
        return self._op15_outputs.get(content_digest)

    def resolve_op36_output(self, content_digest: str) -> Mapping[str, Any] | None:
        return self._op36_outputs.get(content_digest)

    def resolve_tool12_result_set(self, content_digest: str) -> Mapping[str, Any] | None:
        return self._tool12_result_sets.get(content_digest)

    def resolve_projection_receipt(self, receipt_digest: str) -> Mapping[str, Any] | None:
        return self._projection_receipts.get(receipt_digest)

    def resolve_population_binding_receipt(self, receipt_digest: str) -> Mapping[str, Any] | None:
        return self._population_binding_receipts.get(receipt_digest)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise OperatorContractError("non_canonical_json_input", str(error)) from error


def _digest(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _DIGEST_CHARS


def _require(condition: bool, code: str, detail: str = "") -> None:
    if not condition:
        raise OperatorContractError(code, detail)


def _require_keys(value: Mapping[str, Any], required: Iterable[str], label: str) -> None:
    missing = sorted(set(required) - set(value))
    _require(not missing, "missing_required_field", f"{label}: {','.join(missing)}")


def _as_datetime(value: Any, label: str) -> datetime:
    _require(isinstance(value, str), "invalid_datetime", label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise OperatorContractError("invalid_datetime", label) from error
    _require(parsed.tzinfo is not None, "datetime_must_be_timezone_aware", label)
    return parsed.astimezone(timezone.utc)


def _seconds(start: Any, end: Any) -> int:
    delta = (_as_datetime(end, "end_utc") - _as_datetime(start, "start_utc")).total_seconds()
    _require(delta >= 0 and delta.is_integer(), "invalid_interval_duration")
    return int(delta)


def _validate_asn(value: Any, label: str = "asn") -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), "invalid_asn", label)
    _require(0 <= value <= 4_294_967_295, "invalid_asn", label)
    return value


def _validate_evidence_ref(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "invalid_evidence_ref")
    _require_keys(value, ("evidence_id", "source_digest", "member_key"), "evidence_ref")
    _require(isinstance(value["evidence_id"], str) and value["evidence_id"], "invalid_evidence_ref")
    _require(_is_digest(value["source_digest"]), "invalid_evidence_ref")
    _require(isinstance(value["member_key"], str) and value["member_key"], "invalid_evidence_ref")
    return dict(value)


def _evidence_key(value: Mapping[str, Any]) -> bytes:
    return _canonical(value)


def _merge_evidence(*groups: Iterable[Any]) -> list[dict[str, Any]]:
    indexed: dict[bytes, dict[str, Any]] = {}
    for group in groups:
        for raw in group:
            item = _validate_evidence_ref(raw)
            indexed[_evidence_key(item)] = item
    return [indexed[key] for key in sorted(indexed)]


def _require_evidence(evidence: Sequence[Mapping[str, Any]], code: str = "population_evidence_ref_required") -> None:
    _require(bool(evidence), code)


def _validate_identity(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "invalid_publication_identity")
    required = (
        "incident_id", "publication_id", "publication_revision", "publication_digest",
        "collector_id", "cohort_id", "cohort_digest", "window_start_utc", "window_end_utc",
        "data_through_utc", "registry_snapshot_id", "registry_snapshot_digest", "binding_generation",
    )
    _require_keys(value, required, "identity")
    _require(value["collector_id"] == "rrc25", "collector_must_be_rrc25")
    for field in ("publication_digest", "cohort_digest", "registry_snapshot_digest"):
        _require(_is_digest(value[field]), "invalid_identity_digest", field)
    for field in ("publication_revision", "binding_generation"):
        _require(isinstance(value[field], int) and value[field] >= 1, "invalid_identity_integer", field)
    start = _as_datetime(value["window_start_utc"], "window_start_utc")
    end = _as_datetime(value["window_end_utc"], "window_end_utc")
    through = _as_datetime(value["data_through_utc"], "data_through_utc")
    _require(start < end and start <= through, "invalid_identity_window")
    return dict(value)


def _validate_envelope(envelope: Any, operator_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    _require(isinstance(envelope, Mapping), "invalid_operator_envelope")
    required = (
        "identity", "operator_id", "operator_version", "parameter_profile_id",
        "parameter_profile_digest", "input_completeness", "inputs", "input_digests",
    )
    _require_keys(envelope, required, "operator_envelope")
    _require(envelope["operator_id"] == operator_id, "operator_id_mismatch")
    _require(envelope["operator_version"] == OPERATOR_VERSION, "operator_version_mismatch")
    expected_profile = _PROFILE_BY_OPERATOR[operator_id]
    _require(envelope["parameter_profile_id"] == expected_profile, "parameter_profile_mismatch")
    if expected_profile is None:
        _require(envelope["parameter_profile_digest"] is None, "unexpected_parameter_profile_digest")
    else:
        _require(_is_digest(envelope["parameter_profile_digest"]), "invalid_parameter_profile_digest")
    _require(envelope["input_completeness"] == "complete", "incomplete_input_population")
    _require(isinstance(envelope["inputs"], Mapping), "invalid_operator_inputs")
    digests = envelope["input_digests"]
    _require(isinstance(digests, list) and digests, "input_digests_required")
    _require(all(_is_digest(item) for item in digests), "invalid_input_digest")
    _require(len(set(digests)) == len(digests), "duplicate_input_digest")
    identity = _validate_identity(envelope["identity"])
    inputs = dict(envelope["inputs"])
    _require(inputs.get("identity") == identity, "cross_identity_input")
    return dict(envelope), inputs


def _result_envelope(
    envelope: Mapping[str, Any],
    result: Mapping[str, Any],
    evidence_refs: Iterable[Any],
    *,
    result_state: str = "computed",
    completeness: str = "complete",
) -> dict[str, Any]:
    evidence = _merge_evidence(evidence_refs)
    _require_evidence(evidence)
    operator_id = envelope["operator_id"]
    _require(frozenset(result) == _RESULT_FIELDS[operator_id], "operator_result_schema_field_mismatch", operator_id)
    output: dict[str, Any] = {
        "identity": dict(envelope["identity"]),
        "operator_id": operator_id,
        "operator_version": OPERATOR_VERSION,
        "parameter_profile_id": envelope["parameter_profile_id"],
        "parameter_profile_digest": envelope["parameter_profile_digest"],
        "input_digests": list(envelope["input_digests"]),
        "input_completeness": envelope["input_completeness"],
        "result_state": result_state,
        "completeness": completeness,
        "result": dict(result),
        "evidence_refs": evidence,
        "fact_lineage": sorted(set(envelope["input_digests"])),
    }
    output["output_digest"] = _digest(output)
    return output


def _validate_validator(value: Any) -> None:
    _require(isinstance(value, Mapping), "invalid_structural_binding_validator")
    _require(value.get("validator_id") == STRUCTURAL_VALIDATOR_ID, "untrusted_structural_binding_validator")
    _require(value.get("validator_version") == "1.0.0", "untrusted_structural_binding_validator")
    _require(_is_digest(value.get("contract_digest")) and _is_digest(value.get("implementation_digest")), "invalid_structural_binding_validator")


def _receipt_digest_is_valid(receipt: Mapping[str, Any]) -> bool:
    body = dict(receipt)
    claimed = body.pop("receipt_digest", None)
    return _is_digest(claimed) and claimed == _digest(body)


def _output_digest_is_valid(output: Mapping[str, Any]) -> bool:
    body = dict(output)
    claimed = body.pop("output_digest", None)
    return _is_digest(claimed) and claimed == _digest(body)


def _validate_offline_structural_context(value: Any) -> OfflineStructuralFixtureContext:
    _require(isinstance(value, OfflineStructuralFixtureContext), "offline_structural_context_required")
    _require(value.design_candidate_id == DESIGN_CANDIDATE_ID, "design_candidate_mismatch")
    return value


def validate_population_evidence_binding(
    receipt: Any,
    *,
    operator_id: str,
    operator_input_name: str,
    operator_input: Any,
    member_keys: Sequence[str],
    offline_structural_context: Any,
) -> dict[str, Any]:
    """校验 Host 的零业务变换人口 Evidence 回执。"""

    _require(isinstance(receipt, Mapping), "population_evidence_binding_required")
    _require(receipt.get("schema_version") == "country_outage_p2_s1_population_evidence_binding_receipt_v1", "invalid_population_evidence_binding")
    _require(receipt.get("receipt_kind") == "population_evidence_binding", "invalid_population_evidence_binding")
    _require(receipt.get("design_candidate_id") == DESIGN_CANDIDATE_ID, "design_candidate_mismatch")
    _require(receipt.get("operator_id") == operator_id, "population_binding_operator_mismatch")
    _require(receipt.get("operator_input_name") == operator_input_name, "population_binding_input_name_mismatch")
    _require(receipt.get("operator_input_digest") == _digest(operator_input), "population_binding_input_digest_mismatch")
    _require(receipt.get("set_completeness") == "complete", "incomplete_input_population")
    _require(receipt.get("member_count") == len(member_keys), "population_binding_member_count_mismatch")
    _require(receipt.get("member_keys_digest") == _digest(sorted(member_keys)), "population_binding_member_keys_digest_mismatch")
    source = receipt.get("source_population_ref")
    _require(isinstance(source, Mapping), "invalid_source_population_ref")
    _require(source.get("source_kind") in {"frozen_result_set", "operator_output_population"}, "invalid_source_population_ref")
    for field in ("content_digest", "manifest_digest", "completeness_receipt_digest"):
        _require(_is_digest(source.get(field)), "invalid_source_population_ref", field)
    evidence = _validate_evidence_ref(receipt.get("population_evidence_ref"))
    _require(evidence["source_digest"] == source["completeness_receipt_digest"], "population_evidence_source_digest_mismatch")
    _validate_validator(receipt.get("validator"))
    _require(receipt.get("business_transform_count") == 0, "structural_binding_business_transform_forbidden")
    _require(_receipt_digest_is_valid(receipt), "population_binding_receipt_digest_mismatch")
    context = _validate_offline_structural_context(offline_structural_context)
    trusted = context.resolve_population_binding_receipt(receipt["receipt_digest"])
    _require(trusted is not None and trusted == receipt, "untrusted_population_binding_receipt")
    return evidence


def _population_evidence(
    operator_id: str,
    input_name: str,
    operator_input: Any,
    member_keys: Sequence[str],
    inherited_evidence_refs: Iterable[Any],
    population_evidence_binding: Any,
    offline_structural_context: Any,
) -> list[dict[str, Any]]:
    _require(population_evidence_binding is not None, "population_evidence_binding_required")
    evidence: list[dict[str, Any]] = [validate_population_evidence_binding(
        population_evidence_binding,
        operator_id=operator_id,
        operator_input_name=input_name,
        operator_input=operator_input,
        member_keys=member_keys,
        offline_structural_context=offline_structural_context,
    )]
    evidence = _merge_evidence(evidence, inherited_evidence_refs)
    _require_evidence(evidence)
    return evidence


def _state_points(inputs: Mapping[str, Any]) -> list[dict[str, Any]]:
    points = inputs.get("ordered_state_points")
    _require(isinstance(points, list), "invalid_state_series")
    previous: datetime | None = None
    validated: list[dict[str, Any]] = []
    for point in points:
        _require(isinstance(point, Mapping), "invalid_state_point")
        _require_keys(point, ("state_point_utc", "classification", "member_key", "evidence_ref"), "state_point")
        moment = _as_datetime(point["state_point_utc"], "state_point_utc")
        _require(previous is None or previous < moment, "state_series_not_strictly_ordered")
        _require(point["classification"] in _STATES, "invalid_typed_state")
        _validate_evidence_ref(point["evidence_ref"])
        previous = moment
        validated.append(dict(point))
    return validated


def _member_digest(member: Any) -> str:
    return _digest(member)


def _canonical_set(members: Iterable[Any]) -> list[Any]:
    indexed: dict[bytes, Any] = {}
    for member in members:
        key = _canonical(member)
        _require(key not in indexed, "duplicate_set_member")
        indexed[key] = member
    return [indexed[key] for key in sorted(indexed)]


def _set_digest(members: Sequence[Any]) -> str:
    return _digest(list(members))


def _validate_complete_set(value: Any, member_type_id: str) -> list[Any]:
    _require(isinstance(value, Mapping), "invalid_complete_typed_set")
    _require_keys(value, ("member_type_id", "members", "declared_member_count", "set_completeness", "set_digest"), "typed_set")
    _require(value["member_type_id"] == member_type_id, "member_type_mismatch")
    _require(value["set_completeness"] == "complete", "incomplete_input_population")
    _require(isinstance(value["members"], list), "invalid_set_members")
    canonical = _canonical_set(value["members"])
    _require(value["declared_member_count"] == len(canonical), "member_count_mismatch")
    _require(value["set_digest"] == _set_digest(canonical), "set_digest_mismatch")
    return canonical


def _edge_endpoint(domain_digest: str, typed_value: int | None) -> dict[str, Any]:
    return {"domain_value_digest": domain_digest, "typed_value": typed_value}


def _edge(
    relation_type: str,
    from_endpoint: Mapping[str, Any],
    to_endpoint: Mapping[str, Any],
    relation_projection: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "relation_type": relation_type,
        "from_endpoint": dict(from_endpoint),
        "to_endpoint": dict(to_endpoint),
        "relation_projection": dict(relation_projection),
        "relation_projection_digest": _digest(relation_projection),
        "publishable": True,
    }


def op05_as_severity_rank(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = ()) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-05")
    _require(inputs.get("set_completeness") == "complete", "incomplete_input_population")
    members = inputs.get("members")
    _require(isinstance(members, list), "invalid_as_summary_set")
    population_evidence = _validate_evidence_ref(inputs.get("population_evidence_ref"))
    seen: set[int] = set()
    validated: list[dict[str, Any]] = []
    member_evidence: list[dict[str, Any]] = []
    for member in members:
        _require(isinstance(member, Mapping), "invalid_as_summary")
        _require_keys(member, ("asn", "peak_invisible_direction_count", "peak_complete_prefix_count", "fixed_prefix_count", "evidence_ref"), "as_summary")
        asn = _validate_asn(member["asn"])
        _require(asn not in seen, "duplicate_asn_summary")
        for field in ("peak_invisible_direction_count", "peak_complete_prefix_count", "fixed_prefix_count"):
            _require(isinstance(member[field], int) and member[field] >= 0, "invalid_severity_field", field)
        seen.add(asn)
        validated.append(dict(member))
        member_evidence.append(_validate_evidence_ref(member["evidence_ref"]))
    ordered = sorted(validated, key=lambda row: (-row["peak_invisible_direction_count"], -row["peak_complete_prefix_count"], row["asn"]))
    ranked: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    previous_key: tuple[int, int] | None = None
    current_rank = 0
    for position, row in enumerate(ordered, start=1):
        severity_key = (row["peak_invisible_direction_count"], row["peak_complete_prefix_count"])
        if severity_key != previous_key:
            current_rank = position
            groups.append({"rank": current_rank, "member_asns": [], "severity_key": list(severity_key)})
            previous_key = severity_key
        groups[-1]["member_asns"].append(row["asn"])
        ranked.append({"asn": row["asn"], "severity_rank_global": current_rank, "result_position": position, "severity_key": list(severity_key)})
    evidence = _merge_evidence([population_evidence], member_evidence, inherited_evidence_refs)
    result = {
        "ordered_asns": [row["asn"] for row in ordered],
        "ranked_members": ranked,
        "rank_groups": groups,
        "sort_profile_id": "PROFILE-AS-SEVERITY-RANK-1.0.0",
        "input_digest": _digest(inputs),
        "evidence_refs": evidence,
    }
    return _result_envelope(env, result, evidence, result_state="empty" if not ordered else "computed")


def op06_select_first_state_occurrence(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_binding: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-06")
    points = _state_points(inputs)
    target = inputs.get("target_state")
    _require(target in _STATES, "invalid_typed_state")
    matching = next((point for point in points if point["classification"] == target), None)
    population_evidence = _population_evidence("OP-06", "ordered_state_points", points, [point["member_key"] for point in points], inherited_evidence_refs, population_evidence_binding, offline_structural_context)
    if matching is None:
        evidence = _merge_evidence((point["evidence_ref"] for point in points), population_evidence)
        result = {"outcome": "no_match", "state_point_utc": None, "left_censored": False, "source_member_key": None, "input_digest": _digest(inputs), "evidence_refs": evidence}
        return _result_envelope(env, result, evidence, result_state="empty")
    censored = matching is points[0]
    evidence = _merge_evidence([matching["evidence_ref"]], population_evidence)
    result = {"outcome": "left_censored" if censored else "found", "state_point_utc": matching["state_point_utc"], "left_censored": censored, "source_member_key": matching["member_key"], "input_digest": _digest(inputs), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="left_censored" if censored else "computed")


def op07_derive_state_intervals(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_binding: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-07")
    points = _state_points(inputs)
    target = inputs.get("target_state")
    step = inputs.get("grid_step_seconds")
    window = inputs.get("window")
    _require(target in _STATES, "invalid_typed_state")
    _require(isinstance(step, int) and step > 0, "invalid_grid_step")
    _require(isinstance(window, Mapping), "invalid_window")
    _require_keys(window, ("start_utc", "end_utc"), "window")
    window_start = _as_datetime(window["start_utc"], "window.start_utc")
    window_end = _as_datetime(window["end_utc"], "window.end_utc")
    _require(window_start < window_end, "invalid_window")
    for point in points:
        moment = _as_datetime(point["state_point_utc"], "state_point_utc")
        _require(window_start <= moment < window_end, "state_point_outside_window")
        _require(int((moment - window_start).total_seconds()) % step == 0, "state_point_off_grid")
    intervals: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []

    def close_run() -> None:
        if not run:
            return
        start = _as_datetime(run[0]["state_point_utc"], "state_point_utc")
        end = min(_as_datetime(run[-1]["state_point_utc"], "state_point_utc").timestamp() + step, window_end.timestamp())
        end_dt = datetime.fromtimestamp(end, timezone.utc)
        intervals.append({
            "start_utc": run[0]["state_point_utc"],
            "end_utc": end_dt.isoformat().replace("+00:00", "Z"),
            "duration_seconds": int((end_dt - start).total_seconds()),
            "left_censored": start == window_start,
            "right_censored": end_dt == window_end,
            "member_digests": [_member_digest(point) for point in run],
        })
        run.clear()

    previous: datetime | None = None
    for point in points:
        moment = _as_datetime(point["state_point_utc"], "state_point_utc")
        contiguous = previous is not None and int((moment - previous).total_seconds()) == step
        if point["classification"] != target or (run and not contiguous):
            close_run()
        if point["classification"] == target:
            run.append(point)
        previous = moment
    close_run()
    population_evidence = _population_evidence("OP-07", "ordered_state_points", points, [point["member_key"] for point in points], inherited_evidence_refs, population_evidence_binding, offline_structural_context)
    evidence = _merge_evidence((point["evidence_ref"] for point in points), population_evidence)
    result = {"intervals": intervals, "interval_count": len(intervals), "target_state": target, "grid_step_seconds": step, "window": dict(window), "series_digest": inputs.get("series_digest"), "input_digest": _digest(inputs), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="empty" if not intervals else "computed")


def op08_select_last_state_at_cutoff(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_binding: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-08")
    points = _state_points(inputs)
    cutoff = _as_datetime(inputs.get("cutoff_utc"), "cutoff_utc")
    selected = next((point for point in reversed(points) if _as_datetime(point["state_point_utc"], "state_point_utc") <= cutoff), None)
    population_evidence = _population_evidence("OP-08", "ordered_state_points", points, [point["member_key"] for point in points], inherited_evidence_refs, population_evidence_binding, offline_structural_context)
    if selected is None:
        evidence = _merge_evidence((point["evidence_ref"] for point in points), population_evidence)
        result = {"outcome": "no_observation", "state_point_utc": None, "classification": None, "source_member_key": None, "input_digest": _digest(inputs), "evidence_refs": evidence}
        return _result_envelope(env, result, evidence, result_state="empty")
    evidence = _merge_evidence([selected["evidence_ref"]], population_evidence)
    result = {"outcome": "found", "state_point_utc": selected["state_point_utc"], "classification": selected["classification"], "source_member_key": selected["member_key"], "input_digest": _digest(inputs), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence)


def op09_select_peak_state_observation(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_binding: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-09")
    points = inputs.get("ordered_state_points")
    field = inputs.get("severity_field")
    _require(isinstance(points, list), "invalid_state_series")
    allowed = {"invisible_direction_count", "complete_prefix_count", "partial_prefix_count"}
    _require(field in allowed, "unregistered_severity_field")
    previous: datetime | None = None
    evidence_all: list[dict[str, Any]] = []
    values: list[tuple[int | float, dict[str, Any]]] = []
    series_kind: str | None = None
    for point in points:
        _require(isinstance(point, Mapping), "invalid_severity_point")
        moment = _as_datetime(point.get("state_point_utc"), "state_point_utc")
        _require(previous is None or previous < moment, "state_series_not_strictly_ordered")
        kind = "asn" if "asn" in point else "prefix" if "prefix" in point else "invalid"
        _require(kind != "invalid" and (series_kind is None or kind == series_kind), "mixed_severity_series")
        _require(not (kind == "prefix" and field != "invisible_direction_count"), "severity_field_not_registered_for_series")
        _require(field in point and isinstance(point[field], (int, float)) and not isinstance(point[field], bool), "missing_severity_value")
        _require(point[field] >= 0, "invalid_severity_value")
        evidence_all.append(_validate_evidence_ref(point.get("evidence_ref")))
        values.append((point[field], dict(point)))
        series_kind = kind
        previous = moment
    member_keys = [point["evidence_ref"]["member_key"] for _, point in values]
    population_evidence = _population_evidence("OP-09", "ordered_state_points", points, member_keys, inherited_evidence_refs, population_evidence_binding, offline_structural_context)
    evidence = _merge_evidence(evidence_all, population_evidence)
    if not values:
        result = {"outcome": "empty", "peak_value": None, "peak_state_points": [], "input_digest": _digest(inputs), "evidence_refs": evidence}
        return _result_envelope(env, result, evidence, result_state="empty")
    peak = max(value for value, _ in values)
    selected = [point for value, point in values if value == peak]
    refs = [{"state_point_utc": point["state_point_utc"], "member_key": point["evidence_ref"]["member_key"], "member_digest": _member_digest(point)} for point in selected]
    selected_evidence = _merge_evidence((point["evidence_ref"] for point in selected), population_evidence)
    result = {"outcome": "found", "peak_value": peak, "peak_state_points": refs, "input_digest": _digest(inputs), "evidence_refs": selected_evidence}
    return _result_envelope(env, result, selected_evidence)


def op10_compute_as_peak_complete_ratio(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = ()) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-10")
    asn = _validate_asn(inputs.get("asn"))
    numerator = inputs.get("peak_complete_prefix_count")
    denominator = inputs.get("fixed_prefix_count")
    _require(isinstance(numerator, int) and numerator >= 0, "invalid_ratio_numerator")
    _require(isinstance(denominator, int) and denominator >= 0, "invalid_ratio_denominator")
    _require(numerator <= denominator, "peak_complete_exceeds_fixed_prefix_count")
    _require(_is_digest(inputs.get("member_digest")), "invalid_member_digest")
    evidence = _merge_evidence(inherited_evidence_refs)
    _require_evidence(evidence, "member_evidence_ref_required")
    computable = denominator > 0
    result = {"asn": asn, "numerator": numerator, "denominator": denominator, "ratio_exact": f"{numerator}/{denominator}" if computable else None, "outcome": "computed" if computable else "not_computable_zero_denominator", "input_digest": _digest(inputs), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="computed" if computable else "not_computable", completeness="complete" if computable else "not_computable")


def op11_select_longest_interval(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_binding: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-11")
    _require(inputs.get("set_completeness") == "complete", "incomplete_input_population")
    intervals = inputs.get("intervals")
    _require(isinstance(intervals, list), "invalid_interval_set")
    interval_keys = [_digest(interval) for interval in intervals]
    evidence = _population_evidence("OP-11", "intervals", intervals, interval_keys, inherited_evidence_refs, population_evidence_binding, offline_structural_context)
    validated: list[dict[str, Any]] = []
    for interval in intervals:
        _require(isinstance(interval, Mapping), "invalid_state_interval")
        _require_keys(interval, ("start_utc", "end_utc", "duration_seconds", "left_censored", "right_censored", "member_digests"), "state_interval")
        duration = _seconds(interval["start_utc"], interval["end_utc"])
        _require(interval["duration_seconds"] == duration, "interval_duration_mismatch")
        _require(isinstance(interval["member_digests"], list) and interval["member_digests"] and all(_is_digest(item) for item in interval["member_digests"]), "invalid_interval_member_digests")
        validated.append(dict(interval))
    if not validated:
        result = {"outcome": "empty", "duration_seconds": None, "intervals": [], "input_digest": inputs.get("input_digest"), "evidence_refs": evidence}
        return _result_envelope(env, result, evidence, result_state="empty")
    longest = max(item["duration_seconds"] for item in validated)
    selected = sorted((item for item in validated if item["duration_seconds"] == longest), key=lambda item: _as_datetime(item["start_utc"], "start_utc"))
    result = {"outcome": "found", "duration_seconds": longest, "intervals": selected, "input_digest": inputs.get("input_digest"), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence)


def op12_rank_as_first_threshold_crossing(
    envelope: Mapping[str, Any],
    *,
    inherited_evidence_refs: Iterable[Any] = (),
    population_evidence_binding: Any = None,
    asn_bound_op36_receipts: Sequence[Mapping[str, Any]] | None = None,
    offline_structural_context: Any = None,
) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-12")
    _require(inputs.get("set_completeness") == "complete", "incomplete_input_population")
    _require(inputs.get("profile_digest") == env["parameter_profile_digest"], "profile_digest_mismatch")
    receipts = inputs.get("crossing_receipts")
    _require(isinstance(receipts, list), "invalid_crossing_receipt_set")
    bindings = asn_bound_op36_receipts
    _require(isinstance(bindings, Sequence) and not isinstance(bindings, (str, bytes)), "asn_bound_op36_receipts_required")
    by_digest: dict[str, Mapping[str, Any]] = {}
    for binding in bindings:
        _require(isinstance(binding, Mapping), "invalid_asn_bound_op36_receipt")
        output_digest = binding.get("op36_output_digest")
        _require(_is_digest(output_digest) and output_digest not in by_digest, "duplicate_or_invalid_op36_binding")
        by_digest[output_digest] = binding
    seen: set[int] = set()
    crossed: list[dict[str, Any]] = []
    unranked: dict[str, list[int]] = {"left_censored": [], "no_crossing": [], "indeterminate_gap": []}
    evidence_groups: list[Iterable[Any]] = []
    for receipt in receipts:
        _require(isinstance(receipt, Mapping), "invalid_op36_receipt")
        _require(receipt.get("operator_id") == "OP-36", "invalid_op36_receipt")
        _require(receipt.get("identity") == env["identity"], "cross_identity_receipt")
        output_digest = receipt.get("output_digest")
        binding = by_digest.get(output_digest)
        _require(binding is not None, "missing_op36_binding")
        validated_binding = _validate_asn_bound_op36_receipt(binding, receipt, env["identity"], offline_structural_context)
        asn = validated_binding["asn"]
        _require(asn not in seen, "duplicate_asn_receipt")
        _require(receipt.get("profile_digest") == inputs["profile_digest"], "profile_digest_mismatch")
        outcome = receipt.get("outcome")
        _require(outcome in {"crossed", "left_censored", "no_crossing", "indeterminate_gap"}, "invalid_crossing_outcome")
        if outcome == "crossed":
            _as_datetime(receipt.get("crossing_time_utc"), "crossing_time_utc")
            crossed.append(dict(receipt))
        else:
            _require(receipt.get("crossing_time_utc") is None, "unexpected_crossing_time")
            unranked[outcome].append(asn)
        evidence_groups.append(validated_binding["full_output"].get("evidence_refs", ()))
        evidence_groups.append(validated_binding["evidence_refs"])
        seen.add(asn)
    _require(set(by_digest) == {receipt["output_digest"] for receipt in receipts}, "ghost_op36_binding")
    crossed.sort(key=lambda item: (_as_datetime(item["crossing_time_utc"], "crossing_time_utc"), item["asn"]))
    ranked: list[dict[str, Any]] = []
    previous_time: str | None = None
    rank = 0
    for position, receipt in enumerate(crossed, start=1):
        if receipt["crossing_time_utc"] != previous_time:
            rank = position
            previous_time = receipt["crossing_time_utc"]
        ranked.append({"asn": receipt["asn"], "rank": rank, "crossing_time_utc": receipt["crossing_time_utc"], "receipt_digest": receipt["output_digest"]})
    population_evidence = _population_evidence("OP-12", "crossing_receipts", receipts, [receipt["output_digest"] for receipt in receipts], inherited_evidence_refs, population_evidence_binding, offline_structural_context)
    evidence = _merge_evidence(*evidence_groups, population_evidence)
    result = {"ranked": ranked, "unranked_left_censored": sorted(unranked["left_censored"]), "unranked_no_crossing": sorted(unranked["no_crossing"]), "unranked_indeterminate_gap": sorted(unranked["indeterminate_gap"]), "input_digest": _digest(inputs), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="empty" if not receipts else "computed")


def _validate_asn_bound_op11_receipt(
    binding: Any,
    op11_projection: Mapping[str, Any],
    identity: Mapping[str, Any],
    offline_structural_context: Any,
) -> dict[str, Any]:
    _require(isinstance(binding, Mapping), "asn_bound_op11_receipt_required")
    _require(binding.get("schema_version") == "country_outage_p2_s1_asn_bound_op11_receipt_v1", "invalid_asn_bound_op11_receipt")
    _require(binding.get("receipt_kind") == "asn_bound_op11_receipt", "invalid_asn_bound_op11_receipt")
    _require(binding.get("design_candidate_id") == DESIGN_CANDIDATE_ID, "design_candidate_mismatch")
    _require(binding.get("target_operator_id") == "OP-13", "asn_binding_target_mismatch")
    asn = _validate_asn(binding.get("asn"))
    _require(op11_projection.get("operator_id") == "OP-11", "invalid_op11_receipt")
    _require(op11_projection.get("identity") == identity, "cross_identity_receipt")
    context = _validate_offline_structural_context(offline_structural_context)
    op11_output = context.resolve_op11_output(binding.get("op11_output_digest"))
    _require(isinstance(op11_output, Mapping), "trusted_op11_output_not_found")
    _require(op11_output.get("operator_id") == "OP-11" and op11_output.get("identity") == identity, "cross_identity_receipt")
    _require(_output_digest_is_valid(op11_output), "op11_output_digest_mismatch")
    _require(binding.get("op11_output_digest") == op11_output.get("output_digest"), "op11_binding_output_digest_mismatch")
    op11_result = op11_output.get("result")
    projection_result = op11_projection.get("result")
    _require(isinstance(op11_result, Mapping) and isinstance(projection_result, Mapping), "invalid_op11_receipt")
    _require(set(projection_result) == {"outcome", "duration_seconds", "intervals"}, "op11_projection_not_closed")
    _require(projection_result == {field: op11_result.get(field) for field in ("outcome", "duration_seconds", "intervals")}, "op11_projection_mismatch")
    _require(binding.get("op11_input_digest") == op11_result.get("input_digest"), "op11_binding_input_digest_mismatch")
    _require(binding.get("source_node_result_digest") == op11_output.get("output_digest"), "source_node_result_digest_mismatch")
    _require(isinstance(binding.get("source_plan_id"), str) and binding["source_plan_id"], "invalid_plan_binding")
    _require(isinstance(binding.get("source_plan_revision"), int) and binding["source_plan_revision"] >= 1, "invalid_plan_binding")
    _require(isinstance(binding.get("source_plan_node_id"), str) and binding["source_plan_node_id"], "invalid_plan_binding")
    _require(_is_digest(binding.get("source_asn_binding_digest")), "invalid_asn_binding_digest")
    evidence = _merge_evidence(binding.get("evidence_refs", ()))
    _require_evidence(evidence)
    _validate_validator(binding.get("validator"))
    _require(binding.get("business_transform_count") == 0, "structural_binding_business_transform_forbidden")
    _require(_receipt_digest_is_valid(binding), "asn_binding_receipt_digest_mismatch")
    return {"asn": asn, "binding": dict(binding), "evidence_refs": evidence, "full_output": dict(op11_output)}


def _validate_source_plan_binding(binding: Mapping[str, Any], output_digest: str) -> list[dict[str, Any]]:
    _require(isinstance(binding.get("source_plan_id"), str) and binding["source_plan_id"], "invalid_plan_binding")
    _require(isinstance(binding.get("source_plan_revision"), int) and binding["source_plan_revision"] >= 1, "invalid_plan_binding")
    _require(isinstance(binding.get("source_plan_node_id"), str) and binding["source_plan_node_id"], "invalid_plan_binding")
    _require(binding.get("source_node_result_digest") == output_digest, "source_node_result_digest_mismatch")
    _require(_is_digest(binding.get("source_asn_binding_digest")), "invalid_asn_binding_digest")
    evidence = _merge_evidence(binding.get("evidence_refs", ()))
    _require_evidence(evidence)
    _validate_validator(binding.get("validator"))
    _require(binding.get("business_transform_count") == 0, "structural_binding_business_transform_forbidden")
    _require(_receipt_digest_is_valid(binding), "asn_binding_receipt_digest_mismatch")
    return evidence


def _validate_asn_bound_op10_receipt(
    binding: Any,
    op10_projection: Mapping[str, Any],
    identity: Mapping[str, Any],
    offline_structural_context: Any,
) -> dict[str, Any]:
    _require(isinstance(binding, Mapping), "asn_bound_op10_receipt_required")
    _require(binding.get("schema_version") == "country_outage_p2_s1_asn_bound_op10_receipt_v1", "invalid_asn_bound_op10_receipt")
    _require(binding.get("receipt_kind") == "asn_bound_op10_receipt" and binding.get("target_operator_id") == "OP-14", "invalid_asn_bound_op10_receipt")
    _require(binding.get("design_candidate_id") == DESIGN_CANDIDATE_ID, "design_candidate_mismatch")
    asn = _validate_asn(binding.get("asn"))
    _require(op10_projection.get("operator_id") == "OP-10" and op10_projection.get("identity") == identity, "cross_identity_receipt")
    context = _validate_offline_structural_context(offline_structural_context)
    full_output = context.resolve_op10_output(binding.get("op10_output_digest"))
    _require(isinstance(full_output, Mapping), "trusted_op10_output_not_found")
    _require(full_output.get("operator_id") == "OP-10" and full_output.get("identity") == identity, "cross_identity_receipt")
    _require(_output_digest_is_valid(full_output), "op10_output_digest_mismatch")
    output_digest = full_output["output_digest"]
    _require(binding.get("op10_output_digest") == output_digest == op10_projection.get("output_digest"), "op10_binding_output_digest_mismatch")
    full_result = full_output.get("result")
    projection_result = op10_projection.get("result")
    projection_fields = {"asn", "numerator", "denominator", "ratio_exact", "outcome"}
    _require(isinstance(full_result, Mapping) and isinstance(projection_result, Mapping), "invalid_op10_receipt")
    _require(set(projection_result) == projection_fields, "op10_projection_not_closed")
    _require(projection_result == {field: full_result.get(field) for field in projection_fields}, "op10_projection_mismatch")
    _require(asn == full_result.get("asn"), "asn_binding_mismatch")
    _require(binding.get("op10_input_digest") == full_result.get("input_digest"), "op10_binding_input_digest_mismatch")
    evidence = _validate_source_plan_binding(binding, output_digest)
    return {"asn": asn, "evidence_refs": evidence, "full_output": dict(full_output)}


def _validate_asn_bound_op36_receipt(
    binding: Any,
    op36_projection: Mapping[str, Any],
    identity: Mapping[str, Any],
    offline_structural_context: Any,
) -> dict[str, Any]:
    _require(isinstance(binding, Mapping), "asn_bound_op36_receipt_required")
    _require(binding.get("schema_version") == "country_outage_p2_s1_asn_bound_op36_receipt_v1", "invalid_asn_bound_op36_receipt")
    _require(binding.get("receipt_kind") == "asn_bound_op36_receipt" and binding.get("target_operator_id") == "OP-12", "invalid_asn_bound_op36_receipt")
    _require(binding.get("design_candidate_id") == DESIGN_CANDIDATE_ID, "design_candidate_mismatch")
    asn = _validate_asn(binding.get("asn"))
    _require(op36_projection.get("operator_id") == "OP-36" and op36_projection.get("identity") == identity, "cross_identity_receipt")
    context = _validate_offline_structural_context(offline_structural_context)
    full_output = context.resolve_op36_output(binding.get("op36_output_digest"))
    _require(isinstance(full_output, Mapping), "trusted_op36_output_not_found")
    _require(full_output.get("operator_id") == "OP-36" and full_output.get("identity") == identity, "cross_identity_receipt")
    _require(_output_digest_is_valid(full_output), "op36_output_digest_mismatch")
    output_digest = full_output["output_digest"]
    result = full_output.get("result")
    _require(isinstance(result, Mapping), "invalid_op36_output")
    _require(binding.get("op36_output_digest") == output_digest == op36_projection.get("output_digest"), "op36_binding_output_digest_mismatch")
    _require(binding.get("op36_input_digest") == result.get("input_digest") == op36_projection.get("input_digest"), "op36_binding_input_digest_mismatch")
    _require(asn == op36_projection.get("asn"), "asn_binding_mismatch")
    _require(op36_projection.get("outcome") == result.get("outcome"), "op36_projection_mismatch")
    _require(op36_projection.get("crossing_time_utc") == result.get("crossing_time_utc"), "op36_projection_mismatch")
    _require(op36_projection.get("profile_digest") == result.get("profile_digest"), "op36_projection_mismatch")
    _require(op36_projection.get("evidence_refs") == full_output.get("evidence_refs"), "op36_projection_mismatch")
    evidence = _validate_source_plan_binding(binding, output_digest)
    return {"asn": asn, "evidence_refs": evidence, "full_output": dict(full_output)}


def op13_rank_as_longest_duration(
    envelope: Mapping[str, Any],
    *,
    inherited_evidence_refs: Iterable[Any] = (),
    population_evidence_binding: Any = None,
    asn_bound_op11_receipts: Sequence[Mapping[str, Any]] | None = None,
    offline_structural_context: Any = None,
) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-13")
    _require(inputs.get("set_completeness") == "complete", "incomplete_input_population")
    outputs = inputs.get("longest_interval_receipts")
    _require(isinstance(outputs, list), "invalid_op11_receipt_set")
    bindings = asn_bound_op11_receipts
    _require(isinstance(bindings, Sequence) and not isinstance(bindings, (str, bytes)), "design_contract_input_binding_open")
    by_digest: dict[str, Mapping[str, Any]] = {}
    for binding in bindings:
        _require(isinstance(binding, Mapping), "invalid_asn_bound_op11_receipt")
        digest = binding.get("op11_output_digest")
        _require(_is_digest(digest), "invalid_asn_bound_op11_receipt")
        _require(digest not in by_digest, "duplicate_asn_binding")
        by_digest[digest] = binding
    output_digests: list[str] = []
    seen_asns: set[int] = set()
    ranked_sources: list[tuple[int, int, str]] = []
    unranked: list[dict[str, Any]] = []
    evidence_groups: list[Iterable[Any]] = []
    for output in outputs:
        _require(isinstance(output, Mapping), "invalid_op11_receipt")
        _require(output.get("operator_id") == "OP-11" and output.get("identity") == env["identity"], "cross_identity_receipt")
        digest = output.get("output_digest")
        _require(_is_digest(digest) and digest not in output_digests, "duplicate_or_invalid_op11_output")
        binding = by_digest.get(digest)
        _require(binding is not None, "missing_asn_binding")
        validated = _validate_asn_bound_op11_receipt(binding, output, env["identity"], offline_structural_context)
        asn = validated["asn"]
        _require(asn not in seen_asns, "asn_rebound_to_multiple_op11_outputs")
        result = output.get("result")
        outcome = result.get("outcome")
        _require(outcome in {"found", "empty"}, "invalid_op11_outcome")
        if outcome == "found":
            duration = result.get("duration_seconds")
            _require(isinstance(duration, int) and duration >= 0, "invalid_op11_duration")
            ranked_sources.append((duration, asn, digest))
        else:
            _require(result.get("duration_seconds") is None, "invalid_empty_op11_duration")
            unranked.append({"asn": asn, "reason": "empty", "receipt_digest": digest})
        output_digests.append(digest)
        seen_asns.add(asn)
        evidence_groups.append(validated["full_output"].get("evidence_refs", ()))
        evidence_groups.append(validated["evidence_refs"])
    _require(set(by_digest) == set(output_digests), "ghost_asn_binding")
    population_evidence = _population_evidence("OP-13", "longest_interval_receipts", outputs, output_digests, inherited_evidence_refs, population_evidence_binding, offline_structural_context)
    evidence = _merge_evidence(*evidence_groups, population_evidence)
    ranked_sources.sort(key=lambda item: (-item[0], item[1]))
    ranked: list[dict[str, Any]] = []
    previous_duration: int | None = None
    rank = 0
    for position, (duration, asn, digest) in enumerate(ranked_sources, start=1):
        if duration != previous_duration:
            rank = position
            previous_duration = duration
        ranked.append({"asn": asn, "rank": rank, "duration_seconds": duration, "receipt_digest": digest})
    result = {"ranked": ranked, "unranked": sorted(unranked, key=lambda item: item["asn"]), "input_digest": _digest(inputs), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="empty" if not outputs else "computed")


def op14_rank_as_peak_complete_ratio(
    envelope: Mapping[str, Any],
    *,
    inherited_evidence_refs: Iterable[Any] = (),
    population_evidence_binding: Any = None,
    asn_bound_op10_receipts: Sequence[Mapping[str, Any]] | None = None,
    offline_structural_context: Any = None,
) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-14")
    _require(inputs.get("set_completeness") == "complete", "incomplete_input_population")
    receipts = inputs.get("ratio_receipts")
    _require(isinstance(receipts, list), "invalid_ratio_receipt_set")
    bindings = asn_bound_op10_receipts
    _require(isinstance(bindings, Sequence) and not isinstance(bindings, (str, bytes)), "asn_bound_op10_receipts_required")
    by_digest: dict[str, Mapping[str, Any]] = {}
    for binding in bindings:
        _require(isinstance(binding, Mapping), "invalid_asn_bound_op10_receipt")
        output_digest = binding.get("op10_output_digest")
        _require(_is_digest(output_digest) and output_digest not in by_digest, "duplicate_or_invalid_op10_binding")
        by_digest[output_digest] = binding
    seen: set[int] = set()
    computed: list[tuple[Fraction, dict[str, Any]]] = []
    unranked: list[int] = []
    evidence_groups: list[Iterable[Any]] = []
    for receipt in receipts:
        _require(isinstance(receipt, Mapping), "invalid_op10_receipt")
        _require(receipt.get("operator_id") == "OP-10", "invalid_op10_receipt")
        _require(receipt.get("identity") == env["identity"], "cross_identity_receipt")
        binding = by_digest.get(receipt.get("output_digest"))
        _require(binding is not None, "missing_op10_binding")
        validated_binding = _validate_asn_bound_op10_receipt(binding, receipt, env["identity"], offline_structural_context)
        result = receipt.get("result")
        _require(isinstance(result, Mapping), "invalid_op10_receipt")
        asn = validated_binding["asn"]
        _require(asn not in seen, "duplicate_asn_receipt")
        outcome = result.get("outcome")
        if outcome == "computed":
            numerator, denominator = result.get("numerator"), result.get("denominator")
            _require(isinstance(numerator, int) and numerator >= 0 and isinstance(denominator, int) and denominator > 0, "invalid_exact_ratio")
            _require(result.get("ratio_exact") == f"{numerator}/{denominator}", "ratio_receipt_mismatch")
            computed.append((Fraction(numerator, denominator), dict(receipt)))
        elif outcome == "not_computable_zero_denominator":
            _require(result.get("denominator") == 0 and result.get("ratio_exact") is None, "ratio_receipt_mismatch")
            unranked.append(asn)
        else:
            raise OperatorContractError("invalid_ratio_outcome")
        evidence_groups.append(validated_binding["full_output"].get("evidence_refs", ()))
        evidence_groups.append(validated_binding["evidence_refs"])
        seen.add(asn)
    _require(set(by_digest) == {receipt["output_digest"] for receipt in receipts}, "ghost_op10_binding")
    computed.sort(key=lambda item: (-item[0], item[1]["result"]["asn"]))
    ranked: list[dict[str, Any]] = []
    previous: Fraction | None = None
    rank = 0
    for position, (ratio, receipt) in enumerate(computed, start=1):
        if ratio != previous:
            rank = position
            previous = ratio
        result = receipt["result"]
        ranked.append({"asn": result["asn"], "rank": rank, "numerator": result["numerator"], "denominator": result["denominator"], "receipt_digest": receipt["output_digest"]})
    population_evidence = _population_evidence("OP-14", "ratio_receipts", receipts, [receipt["output_digest"] for receipt in receipts], inherited_evidence_refs, population_evidence_binding, offline_structural_context)
    evidence = _merge_evidence(*evidence_groups, population_evidence)
    result = {"ranked": ranked, "unranked_zero_denominator": sorted(unranked), "input_digest": _digest(inputs), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="empty" if not receipts else "computed")


def _validate_path_profile(inputs: Mapping[str, Any]) -> None:
    _require(inputs.get("path_canonicalization_profile_id") == PATH_PROFILE_ID, "path_profile_mismatch")
    _require(inputs.get("path_canonicalization_profile_digest") == PATH_PROFILE_DIGEST, "path_profile_mismatch")


def _flatten_ordered_path(segments: Any) -> list[int]:
    _require(isinstance(segments, list) and segments, "path_segments_required")
    flattened: list[int] = []
    for segment in segments:
        _require(isinstance(segment, Mapping), "invalid_path_segment")
        _require(segment.get("segment_type") == "as_sequence", "ordered_path_has_unordered_segment")
        asns = segment.get("asns")
        _require(isinstance(asns, list) and asns, "invalid_path_segment")
        flattened.extend(_validate_asn(asn, "path_asn") for asn in asns)
    return flattened


def op15_locate_asn_positions(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = ()) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-15")
    _validate_path_profile(inputs)
    target = _validate_asn(inputs.get("target_asn"), "target_asn")
    status = inputs.get("common_path_status")
    _require(status in {"ordered", "unordered", "ambiguous", "invalid", "unknown", "not_applicable"}, "invalid_common_path_status")
    evidence = _merge_evidence(inherited_evidence_refs)
    _require_evidence(evidence, "path_evidence_ref_required")
    positions: list[int] = []
    edge_projection: dict[str, Any] | None = None
    if status == "ordered":
        path_digest = inputs.get("path_digest")
        _require(_is_digest(path_digest), "invalid_path_digest")
        _require(path_digest == _digest(inputs.get("path_segments")), "path_digest_mismatch")
        flattened = _flatten_ordered_path(inputs.get("path_segments"))
        positions = [position for position, asn in enumerate(flattened) if asn == target]
        outcome = "found" if positions else "not_found"
        if positions:
            projection = {"outcome": "found", "target_asn": target, "ordered_positions": positions, "path_digest": path_digest, "path_canonicalization_profile_id": PATH_PROFILE_ID, "path_canonicalization_profile_digest": PATH_PROFILE_DIGEST, "operator_input_digest": _digest(inputs)}
            edge_projection = _edge("path_contains", _edge_endpoint(path_digest, None), _edge_endpoint(_digest(target), target), projection)
    elif status in {"unknown", "not_applicable"}:
        _require(inputs.get("path_id") is None and inputs.get("path_digest") is None and inputs.get("path_segments") is None, "pathless_status_requires_null_path")
        path_digest = None
        outcome = status
    else:
        _require(_is_digest(inputs.get("path_digest")) and isinstance(inputs.get("path_segments"), list) and inputs["path_segments"], "path_status_requires_path")
        _require(inputs.get("path_digest") == _digest(inputs.get("path_segments")), "path_digest_mismatch")
        path_digest = inputs["path_digest"]
        outcome = status
    result = {"outcome": outcome, "target_asn": target, "ordered_positions": positions, "path_digest": path_digest, "path_canonicalization_profile_id": PATH_PROFILE_ID, "path_canonicalization_profile_digest": PATH_PROFILE_DIGEST, "input_digest": _digest(inputs), "evidence_refs": evidence, "edge_projection": edge_projection}
    state = "computed" if outcome in {"found", "not_found"} else "unknown" if outcome == "unknown" else "not_comparable"
    return _result_envelope(env, result, evidence, result_state=state, completeness="complete" if state in {"computed", "unknown"} else "not_computable")


def _validate_op15_receipt(
    receipt: Any,
    identity: Mapping[str, Any],
    path_digest: str,
    offline_structural_context: Any,
) -> dict[str, Any]:
    _require(isinstance(receipt, Mapping), "invalid_op15_receipt")
    _require(receipt.get("operator_id") == "OP-15", "invalid_op15_receipt")
    _require(receipt.get("identity") == identity, "cross_identity_receipt")
    _require(receipt.get("path_digest") == path_digest, "path_digest_mismatch")
    _require(receipt.get("path_canonicalization_profile_id") == PATH_PROFILE_ID and receipt.get("path_canonicalization_profile_digest") == PATH_PROFILE_DIGEST, "path_profile_mismatch")
    _validate_asn(receipt.get("target_asn"), "target_asn")
    _require(receipt.get("outcome") in {"found", "not_found", "unordered", "ambiguous", "invalid", "unknown", "not_applicable"}, "invalid_op15_outcome")
    positions = receipt.get("ordered_positions")
    _require(isinstance(positions, list) and all(isinstance(item, int) and item >= 0 for item in positions) and len(set(positions)) == len(positions), "invalid_path_positions")
    _require(positions == sorted(positions), "path_positions_not_sorted")
    _require((receipt["outcome"] == "found") == bool(positions), "op15_position_outcome_mismatch")
    output_digest = receipt.get("output_digest")
    _require(_is_digest(output_digest), "invalid_receipt_digest")
    evidence = _merge_evidence(receipt.get("evidence_refs", ()))
    context = _validate_offline_structural_context(offline_structural_context)
    full_output = context.resolve_op15_output(output_digest)
    _require(isinstance(full_output, Mapping), "offline_op15_output_not_found")
    _require(_output_digest_is_valid(full_output), "offline_op15_output_digest_mismatch")
    _require(full_output.get("operator_id") == "OP-15", "offline_op15_operator_mismatch")
    _require(full_output.get("identity") == identity, "cross_identity_receipt")
    result = full_output.get("result")
    _require(isinstance(result, Mapping), "offline_op15_result_invalid")
    expected = {
        "identity": full_output.get("identity"),
        "operator_id": full_output.get("operator_id"),
        "path_digest": result.get("path_digest"),
        "path_canonicalization_profile_id": result.get("path_canonicalization_profile_id"),
        "path_canonicalization_profile_digest": result.get("path_canonicalization_profile_digest"),
        "target_asn": result.get("target_asn"),
        "outcome": result.get("outcome"),
        "ordered_positions": result.get("ordered_positions"),
        "input_digest": result.get("input_digest"),
        "output_digest": full_output.get("output_digest"),
        "evidence_refs": full_output.get("evidence_refs"),
    }
    _require(dict(receipt) == expected, "op15_receipt_projection_mismatch")
    _require(evidence == _merge_evidence(full_output.get("evidence_refs", ())), "op15_receipt_evidence_mismatch")
    return dict(receipt)


def op16_project_direct_path_neighbors(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-16")
    path_digest = inputs.get("path_digest")
    _require(_is_digest(path_digest), "invalid_path_digest")
    _require(path_digest == _digest(inputs.get("path_segments")), "path_digest_mismatch")
    receipt = _validate_op15_receipt(inputs.get("op15_position_receipt"), env["identity"], path_digest, offline_structural_context)
    flattened = _flatten_ordered_path(inputs.get("path_segments"))
    positions = receipt["ordered_positions"]
    _require(all(position < len(flattened) and flattened[position] == receipt["target_asn"] for position in positions), "position_receipt_path_mismatch")
    evidence = _merge_evidence(receipt["evidence_refs"], inherited_evidence_refs)
    _require_evidence(evidence)
    left: list[dict[str, int]] = []
    right: list[dict[str, int]] = []
    edges: list[dict[str, Any]] = []
    if receipt["outcome"] == "found":
        for position in positions:
            if position > 0:
                item = {"target_position": position, "neighbor_position": position - 1, "neighbor_asn": flattened[position - 1]}
                left.append(item)
                projection = {"target_asn": receipt["target_asn"], "neighbor_side": "left", **item, "path_digest": path_digest, "position_receipt_digest": receipt["output_digest"]}
                edges.append(_edge("directly_adjacent_in_path", _edge_endpoint(_digest(receipt["target_asn"]), receipt["target_asn"]), _edge_endpoint(_digest(item["neighbor_asn"]), item["neighbor_asn"]), projection))
            if position + 1 < len(flattened):
                item = {"target_position": position, "neighbor_position": position + 1, "neighbor_asn": flattened[position + 1]}
                right.append(item)
                projection = {"target_asn": receipt["target_asn"], "neighbor_side": "right", **item, "path_digest": path_digest, "position_receipt_digest": receipt["output_digest"]}
                edges.append(_edge("directly_adjacent_in_path", _edge_endpoint(_digest(receipt["target_asn"]), receipt["target_asn"]), _edge_endpoint(_digest(item["neighbor_asn"]), item["neighbor_asn"]), projection))
        outcome = "computed"
    elif receipt["outcome"] == "not_found":
        outcome = "not_found"
    else:
        outcome = "not_comparable"
    result = {"outcome": outcome, "target_asn": receipt["target_asn"], "left_neighbors": left, "right_neighbors": right, "path_digest": path_digest, "position_receipt_digest": receipt["output_digest"], "evidence_refs": evidence, "edge_projections": edges}
    return _result_envelope(env, result, evidence, result_state="computed" if outcome != "not_comparable" else "not_comparable", completeness="complete" if outcome != "not_comparable" else "not_computable")


def op17_classify_ordered_asn_path_relation(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-17")
    path_digest = inputs.get("path_digest")
    _require(_is_digest(path_digest), "invalid_path_digest")
    left = _validate_op15_receipt(inputs.get("left_position_receipt"), env["identity"], path_digest, offline_structural_context)
    right = _validate_op15_receipt(inputs.get("right_position_receipt"), env["identity"], path_digest, offline_structural_context)
    evidence = _merge_evidence(left["evidence_refs"], right["evidence_refs"], inherited_evidence_refs)
    _require_evidence(evidence)
    comparable = left["outcome"] in {"found", "not_found"} and right["outcome"] in {"found", "not_found"}
    if not comparable:
        relation = None
        pairs: list[dict[str, int]] = []
        outcome = "not_comparable"
    elif not left["ordered_positions"] or not right["ordered_positions"]:
        relation, pairs, outcome = "not_cooccurring", [], "computed"
    elif left["target_asn"] == right["target_asn"]:
        relation = "same_asn"
        pairs = [{"left_position": l, "right_position": r} for l in left["ordered_positions"] for r in right["ordered_positions"]]
        outcome = "computed"
    else:
        pairs = [{"left_position": l, "right_position": r} for l in left["ordered_positions"] for r in right["ordered_positions"]]
        _require(all(pair["left_position"] != pair["right_position"] for pair in pairs), "different_asns_share_path_position")
        has_left = any(pair["left_position"] < pair["right_position"] for pair in pairs)
        has_right = any(pair["right_position"] < pair["left_position"] for pair in pairs)
        relation = "both_orders" if has_left and has_right else "left_before_right" if has_left else "right_before_left"
        outcome = "computed"
    result = {"outcome": outcome, "relation": relation, "witness_position_pairs": pairs, "path_digest": path_digest, "input_digest": _digest(inputs), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="computed" if outcome == "computed" else "not_comparable", completeness="complete" if outcome == "computed" else "not_computable")


def _path_evidence_members(inputs: Mapping[str, Any]) -> list[dict[str, Any]]:
    _require(inputs.get("set_completeness") == "complete", "incomplete_input_population")
    members = inputs.get("path_evidence_members")
    _require(isinstance(members, list), "invalid_path_evidence_set")
    validated: list[dict[str, Any]] = []
    for member in members:
        _require(isinstance(member, Mapping), "invalid_path_evidence")
        _require_keys(member, ("path_digest", "path_canonicalization_profile_id", "path_canonicalization_profile_digest", "prefix", "afi", "peer_asn_direction_ids", "evidence_ref"), "path_evidence")
        _require(_is_digest(member["path_digest"]), "invalid_path_digest")
        _validate_path_profile(member)
        _validate_evidence_ref(member["evidence_ref"])
        _require(member["afi"] in {4, 6}, "invalid_afi")
        directions = member["peer_asn_direction_ids"]
        _require(isinstance(directions, list) and directions and all(isinstance(item, str) and item for item in directions) and len(set(directions)) == len(directions), "invalid_peer_direction_set")
        validated.append(dict(member))
    return validated


def op18_project_path_prefix_set(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_binding: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-18")
    rows = _path_evidence_members(inputs)
    by_key: dict[tuple[int, int, int], dict[str, Any]] = {}
    for row in rows:
        try:
            network = ip_network(row["prefix"], strict=True)
        except ValueError as error:
            raise OperatorContractError("invalid_canonical_prefix", str(row["prefix"])) from error
        _require(network.version == row["afi"] and str(network) == row["prefix"], "prefix_afi_or_canonicalization_mismatch")
        by_key[(row["afi"], int(network.network_address), network.prefixlen)] = {"afi": row["afi"], "prefix": row["prefix"]}
    members = [by_key[key] for key in sorted(by_key)]
    population_evidence = _population_evidence("OP-18", "path_evidence_members", rows, [row["path_digest"] + ":" + row["prefix"] for row in rows], inherited_evidence_refs, population_evidence_binding, offline_structural_context)
    evidence = _merge_evidence((row["evidence_ref"] for row in rows), population_evidence)
    result = {"members": members, "member_count": len(members), "set_digest": _set_digest(members), "input_digest": inputs.get("input_digest"), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="empty" if not members else "computed")


def op19_project_observed_downstream_origin_set(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-19")
    _require(inputs.get("set_completeness") == "complete", "incomplete_input_population")
    anchor = _validate_asn(inputs.get("anchor_asn"), "anchor_asn")
    source = inputs.get("source_result_set_ref")
    _require(isinstance(source, Mapping) and source.get("source_population_id") == "window_path_association_evidence_rows", "invalid_source_result_set_ref")
    for field in ("manifest_digest", "content_digest", "freeze_receipt_digest", "query_receipt_digest", "source_dataset_digest"):
        _require(_is_digest(source.get(field)), "invalid_source_result_set_ref", field)
    _require(source.get("member_identity") == "path_association_id", "invalid_source_member_identity")
    query_digest = inputs.get("source_result_set_query_receipt_digest")
    _require(source.get("query_receipt_digest") == query_digest, "source_query_receipt_mismatch")
    _require(inputs.get("population_filter_receipt_digest") == query_digest, "population_filter_receipt_mismatch")
    population_evidence = _validate_evidence_ref(inputs.get("population_evidence_ref"))
    _require(population_evidence["source_digest"] == query_digest, "population_evidence_source_digest_mismatch")
    context = _validate_offline_structural_context(offline_structural_context)
    source_result_set = context.resolve_tool12_result_set(source.get("content_digest"))
    _require(isinstance(source_result_set, Mapping), "trusted_tool12_result_set_not_found")
    _require(source_result_set.get("identity") == env["identity"], "cross_identity_result_set")
    _require(source_result_set.get("result_set_id") == source.get("result_set_id") and source_result_set.get("result_set_revision") == source.get("result_set_revision"), "source_result_set_ref_mismatch")
    _require(source_result_set.get("content_digest") == source.get("content_digest") and source_result_set.get("manifest_digest") == source.get("manifest_digest"), "source_result_set_ref_mismatch")
    _require(source_result_set.get("query_receipt_digest") == query_digest and source_result_set.get("completeness") == "complete", "source_result_set_not_complete")
    normalized_query = source_result_set.get("normalized_query")
    _require(isinstance(normalized_query, Mapping) and normalized_query.get("anchor_asn") == anchor and normalized_query.get("anchor_before_known_origin") is True, "source_result_set_query_not_anchor_before")
    source_members = source_result_set.get("members")
    _require(isinstance(source_members, list), "invalid_tool12_result_set_members")
    source_population: dict[str, str] = {}
    for member in source_members:
        _require(isinstance(member, Mapping), "invalid_tool12_result_set_member")
        member_key = member.get("source_member_key")
        member_digest = member.get("source_member_digest")
        _require(isinstance(member_key, str) and member_key and _is_digest(member_digest), "invalid_tool12_result_set_member")
        _require(member_key not in source_population, "duplicate_tool12_result_set_member")
        source_population[member_key] = member_digest
    projection_receipt = context.resolve_projection_receipt(inputs.get("host_projection_receipt_digest"))
    _require(isinstance(projection_receipt, Mapping), "trusted_projection_receipt_not_found")
    _require(_receipt_digest_is_valid(projection_receipt), "host_projection_receipt_digest_mismatch")
    _require(projection_receipt.get("receipt_digest") == inputs.get("host_projection_receipt_digest"), "host_projection_receipt_digest_mismatch")
    _require(projection_receipt.get("design_candidate_id") == DESIGN_CANDIDATE_ID and projection_receipt.get("operator_id") == "OP-19", "host_projection_receipt_scope_mismatch")
    _require(projection_receipt.get("anchor_asn") == anchor and projection_receipt.get("source_result_set_content_digest") == source.get("content_digest") and projection_receipt.get("query_receipt_digest") == query_digest, "host_projection_receipt_scope_mismatch")
    _require(projection_receipt.get("business_transform_count") == 0, "structural_binding_business_transform_forbidden")
    associations = inputs.get("association_members")
    _require(isinstance(associations, list), "invalid_association_set")
    contributions: dict[int, dict[str, Any]] = {}
    projected_population: dict[str, str] = {}
    for association in associations:
        _require(isinstance(association, Mapping), "invalid_path_association")
        _require(association.get("anchor_asn") == anchor, "anchor_asn_mismatch")
        origin = _validate_asn(association.get("known_origin_asn"), "known_origin_asn")
        _require(association.get("origin_status") == "known" and association.get("observed_origin_asn") == origin, "known_origin_binding_mismatch")
        _require(_is_digest(association.get("source_member_digest")) and _is_digest(association.get("path_digest")), "invalid_association_digest")
        member_key = association.get("source_member_key")
        _require(member_key in source_population and source_population[member_key] == association["source_member_digest"], "projection_member_not_in_source_population")
        _require(member_key not in projected_population, "duplicate_projection_member")
        projected_population[member_key] = association["source_member_digest"]
        _validate_path_profile(association)
        evidence = _validate_evidence_ref(association.get("evidence_ref"))
        bucket = contributions.setdefault(origin, {"keys": set(), "digests": set(), "evidence": []})
        _require(association.get("source_member_key") not in bucket["keys"], "duplicate_association_member")
        bucket["keys"].add(association["source_member_key"])
        bucket["digests"].add(association["source_member_digest"])
        bucket["evidence"].append(evidence)
    _require(projected_population == source_population, "projection_population_not_one_to_one")
    _require(projection_receipt.get("source_member_keys_digest") == _digest(sorted(source_population)) and projection_receipt.get("source_member_digests_digest") == _digest(sorted(source_population.values())), "host_projection_source_population_mismatch")
    _require(projection_receipt.get("projected_member_keys_digest") == _digest(sorted(projected_population)) and projection_receipt.get("projected_member_digests_digest") == _digest(sorted(projected_population.values())), "host_projection_population_mismatch")
    members = sorted(contributions)
    contribution_rows: list[dict[str, Any]] = []
    for origin in members:
        bucket = contributions[origin]
        row = {"origin_asn": origin, "source_member_keys": sorted(bucket["keys"]), "source_member_digests": sorted(bucket["digests"]), "evidence_refs": _merge_evidence(bucket["evidence"])}
        row["contribution_digest"] = _digest(row)
        contribution_rows.append(row)
    evidence = _merge_evidence([population_evidence], *(row["evidence_refs"] for row in contribution_rows), inherited_evidence_refs)
    result = {"anchor_asn": anchor, "members": members, "member_contributions": contribution_rows, "member_count": len(members), "set_digest": _set_digest(members), "input_digest": _digest(inputs), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="empty" if not members else "computed")


def op20_project_canonical_path_set(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_binding: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-20")
    rows = _path_evidence_members(inputs)
    profile = inputs.get("canonicalization_profile_digest")
    _require(profile == PATH_PROFILE_DIGEST, "path_profile_mismatch")
    _require(all(row["path_canonicalization_profile_digest"] == profile for row in rows), "mixed_path_profile")
    members = sorted({row["path_digest"] for row in rows})
    population_evidence = _population_evidence("OP-20", "path_evidence_members", rows, [row["path_digest"] + ":" + row["prefix"] for row in rows], inherited_evidence_refs, population_evidence_binding, offline_structural_context)
    evidence = _merge_evidence((row["evidence_ref"] for row in rows), population_evidence)
    result = {"members": members, "member_count": len(members), "canonicalization_profile_digest": profile, "set_digest": _set_digest(members), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="empty" if not members else "computed")


def op21_project_peer_direction_set(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_binding: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-21")
    rows = _path_evidence_members(inputs)
    profile = inputs.get("direction_identity_profile_digest")
    _require(_is_digest(profile), "invalid_direction_identity_profile_digest")
    members = sorted({direction for row in rows for direction in row["peer_asn_direction_ids"]})
    population_evidence = _population_evidence("OP-21", "path_evidence_members", rows, [row["path_digest"] + ":" + row["prefix"] for row in rows], inherited_evidence_refs, population_evidence_binding, offline_structural_context)
    evidence = _merge_evidence((row["evidence_ref"] for row in rows), population_evidence)
    result = {"members": members, "member_count": len(members), "direction_identity_profile_digest": profile, "set_digest": _set_digest(members), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="empty" if not members else "computed")


def _count_complete_set(envelope: Mapping[str, Any], operator_id: str, kind: str, inherited_evidence_refs: Iterable[Any], population_evidence_binding: Any, offline_structural_context: Any) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, operator_id)
    _require(inputs.get("set_completeness") == "complete", "incomplete_input_population")
    members = inputs.get("members")
    _require(isinstance(members, list), "invalid_set_members")
    if kind == "path":
        _require(all(_is_digest(item) for item in members), "invalid_path_digest")
        canonical = _canonical_set(members)
    elif kind == "prefix":
        for item in members:
            _require(isinstance(item, Mapping) and item.get("afi") in {4, 6} and isinstance(item.get("prefix"), str), "invalid_prefix_key")
        canonical = _canonical_set(members)
    else:
        _require(all(isinstance(item, str) and item for item in members), "invalid_peer_direction_id")
        canonical = _canonical_set(members)
    _require(inputs.get("member_count") == len(canonical), "member_count_mismatch")
    _require(inputs.get("set_digest") == _set_digest(canonical), "set_digest_mismatch")
    evidence = _population_evidence(operator_id, "members", members, [_digest(member) for member in canonical], inherited_evidence_refs, population_evidence_binding, offline_structural_context)
    result = {"count": len(canonical), "input_set_digest": inputs["set_digest"], "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="empty" if not canonical else "computed")


def op22_count_unique_paths(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_binding: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    return _count_complete_set(envelope, "OP-22", "path", inherited_evidence_refs, population_evidence_binding, offline_structural_context)


def op23_count_unique_prefixes(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_binding: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    return _count_complete_set(envelope, "OP-23", "prefix", inherited_evidence_refs, population_evidence_binding, offline_structural_context)


def op24_count_unique_peer_directions(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_binding: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    return _count_complete_set(envelope, "OP-24", "direction", inherited_evidence_refs, population_evidence_binding, offline_structural_context)


def _two_sets(envelope: Mapping[str, Any], operator_id: str) -> tuple[dict[str, Any], dict[str, Any], list[Any], list[Any]]:
    env, inputs = _validate_envelope(envelope, operator_id)
    member_type = inputs.get("member_type_id")
    _require(isinstance(member_type, str) and member_type, "invalid_member_type_id")
    left = _validate_complete_set(inputs.get("left_set"), member_type)
    right = _validate_complete_set(inputs.get("right_set"), member_type)
    _require(inputs.get("left_digest") == inputs["left_set"].get("set_digest"), "left_digest_mismatch")
    _require(inputs.get("right_digest") == inputs["right_set"].get("set_digest"), "right_digest_mismatch")
    return env, inputs, left, right


def _set_map(members: Sequence[Any]) -> dict[bytes, Any]:
    return {_canonical(member): member for member in members}


def _two_set_evidence(
    operator_id: str,
    inputs: Mapping[str, Any],
    left: Sequence[Any],
    right: Sequence[Any],
    inherited_evidence_refs: Iterable[Any],
    population_evidence_bindings: Any,
    offline_structural_context: Any,
) -> list[dict[str, Any]]:
    _require(isinstance(population_evidence_bindings, Mapping), "population_evidence_bindings_required")
    left_evidence = _population_evidence(operator_id, "left_set", inputs["left_set"], [_digest(member) for member in left], (), population_evidence_bindings.get("left_set"), offline_structural_context)
    right_evidence = _population_evidence(operator_id, "right_set", inputs["right_set"], [_digest(member) for member in right], (), population_evidence_bindings.get("right_set"), offline_structural_context)
    return _merge_evidence(left_evidence, right_evidence, inherited_evidence_refs)


def op25_set_intersection(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_bindings: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs, left, right = _two_sets(envelope, "OP-25")
    left_map, right_map = _set_map(left), _set_map(right)
    members = [left_map[key] for key in sorted(set(left_map) & set(right_map))]
    set_digest = _set_digest(members)
    evidence = _two_set_evidence("OP-25", inputs, left, right, inherited_evidence_refs, population_evidence_bindings, offline_structural_context)
    edge_projection = None
    if members:
        projection = {"intersection_set_digest": set_digest, "intersection_count": len(members), "left_digest": inputs["left_digest"], "right_digest": inputs["right_digest"]}
        edge_projection = _edge("set_intersects", _edge_endpoint(inputs["left_digest"], None), _edge_endpoint(inputs["right_digest"], None), projection)
    result = {"members": members, "member_count": len(members), "left_digest": inputs["left_digest"], "right_digest": inputs["right_digest"], "set_digest": set_digest, "evidence_refs": evidence, "edge_projection": edge_projection}
    return _result_envelope(env, result, evidence, result_state="empty" if not members else "computed")


def op26_set_directional_difference(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_bindings: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs, left, right = _two_sets(envelope, "OP-26")
    left_map, right_map = _set_map(left), _set_map(right)
    members = [left_map[key] for key in sorted(set(left_map) - set(right_map))]
    evidence = _two_set_evidence("OP-26", inputs, left, right, inherited_evidence_refs, population_evidence_bindings, offline_structural_context)
    result = {"direction": "left_minus_right", "members": members, "member_count": len(members), "left_digest": inputs["left_digest"], "right_digest": inputs["right_digest"], "set_digest": _set_digest(members), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="empty" if not members else "computed")


def op27_set_directional_coverage(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_bindings: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs, left, right = _two_sets(envelope, "OP-27")
    left_keys, right_keys = set(_set_map(left)), set(_set_map(right))
    intersection_count = len(left_keys & right_keys)
    denominator = len(left_keys)
    evidence = _two_set_evidence("OP-27", inputs, left, right, inherited_evidence_refs, population_evidence_bindings, offline_structural_context)
    if denominator == 0:
        outcome, ratio = "not_computable_empty_denominator", None
    else:
        outcome, ratio = "computed", f"{intersection_count}/{denominator}"
    edge_projection = None
    if denominator and intersection_count == denominator:
        projection = {"direction": "intersection_over_left", "intersection_count": intersection_count, "denominator_count": denominator, "ratio_exact": "1/1", "outcome": "computed", "left_digest": inputs["left_digest"], "right_digest": inputs["right_digest"]}
        edge_projection = _edge("set_contains", _edge_endpoint(inputs["right_digest"], None), _edge_endpoint(inputs["left_digest"], None), projection)
        ratio = "1/1"
    result = {"direction": "intersection_over_left", "intersection_count": intersection_count, "denominator_count": denominator, "ratio_exact": ratio, "outcome": outcome, "left_digest": inputs["left_digest"], "right_digest": inputs["right_digest"], "evidence_refs": evidence, "edge_projection": edge_projection}
    return _result_envelope(env, result, evidence, result_state="computed" if outcome == "computed" else "not_computable", completeness="complete" if outcome == "computed" else "not_computable")


def op28_set_jaccard(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_bindings: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs, left, right = _two_sets(envelope, "OP-28")
    left_keys, right_keys = set(_set_map(left)), set(_set_map(right))
    intersection_count = len(left_keys & right_keys)
    union_count = len(left_keys | right_keys)
    evidence = _two_set_evidence("OP-28", inputs, left, right, inherited_evidence_refs, population_evidence_bindings, offline_structural_context)
    if union_count == 0:
        outcome, ratio = "not_comparable_both_empty", None
    else:
        outcome, ratio = "computed", f"{intersection_count}/{union_count}"
    result = {"intersection_count": intersection_count, "union_count": union_count, "ratio_exact": ratio, "outcome": outcome, "left_digest": inputs["left_digest"], "right_digest": inputs["right_digest"], "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="computed" if outcome == "computed" else "not_comparable", completeness="complete" if outcome == "computed" else "not_computable")


def op35_select_last_state_occurrence(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_binding: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-35")
    points = _state_points(inputs)
    target = inputs.get("target_state")
    _require(target in _STATES, "invalid_typed_state")
    matching = next((point for point in reversed(points) if point["classification"] == target), None)
    population_evidence = _population_evidence("OP-35", "ordered_state_points", points, [point["member_key"] for point in points], inherited_evidence_refs, population_evidence_binding, offline_structural_context)
    if matching is None:
        evidence = _merge_evidence((point["evidence_ref"] for point in points), population_evidence)
        result = {"outcome": "no_match", "state_point_utc": None, "right_censored": False, "source_member_key": None, "input_digest": _digest(inputs), "evidence_refs": evidence}
        return _result_envelope(env, result, evidence, result_state="empty")
    censored = matching is points[-1]
    evidence = _merge_evidence([matching["evidence_ref"]], population_evidence)
    result = {"outcome": "right_censored" if censored else "found", "state_point_utc": matching["state_point_utc"], "right_censored": censored, "source_member_key": matching["member_key"], "input_digest": _digest(inputs), "evidence_refs": evidence}
    return _result_envelope(env, result, evidence, result_state="right_censored" if censored else "computed")


def _threshold_matches(value: Fraction, threshold: Fraction, comparison: str) -> bool:
    if comparison == "gt":
        return value > threshold
    if comparison == "gte":
        return value >= threshold
    if comparison == "lt":
        return value < threshold
    if comparison == "lte":
        return value <= threshold
    raise OperatorContractError("invalid_threshold_comparison")


def op36_detect_first_threshold_crossing(envelope: Mapping[str, Any], *, inherited_evidence_refs: Iterable[Any] = (), population_evidence_binding: Any = None, offline_structural_context: Any = None) -> dict[str, Any]:
    env, inputs = _validate_envelope(envelope, "OP-36")
    profile = inputs.get("threshold_profile_instance")
    _require(isinstance(profile, Mapping), "invalid_threshold_profile")
    _require(profile.get("profile_id") == "PROFILE-FIRST-CROSSING-1.0.0" and profile.get("profile_version") == "1.0.0", "threshold_profile_mismatch")
    _require(profile.get("profile_digest") == env["parameter_profile_digest"], "profile_digest_mismatch")
    _require(profile.get("grid_step_seconds") == 300, "invalid_threshold_grid")
    _require(profile.get("gap_policy") == "indeterminate_if_any_gap_precedes_candidate_or_prevents_no_crossing_proof", "invalid_gap_policy")
    exact = profile.get("threshold_exact")
    _require(isinstance(exact, Mapping) and isinstance(exact.get("numerator"), int) and isinstance(exact.get("denominator"), int) and exact["denominator"] > 0, "invalid_threshold_exact")
    threshold = Fraction(exact["numerator"], exact["denominator"])
    comparison = profile.get("comparison")
    _require(comparison in {"gt", "gte", "lt", "lte"}, "invalid_threshold_comparison")
    points = inputs.get("ordered_numeric_points")
    _require(isinstance(points, list), "invalid_numeric_series")
    window_start = _as_datetime(env["identity"]["window_start_utc"], "window_start_utc")
    window_end = _as_datetime(env["identity"]["window_end_utc"], "window_end_utc")
    data_through = _as_datetime(env["identity"]["data_through_utc"], "data_through_utc")
    effective_end = min(window_end, data_through)
    previous_time: datetime | None = None
    validated: list[dict[str, Any]] = []
    gap_seen = not points
    last_known_false: dict[str, Any] | None = None
    first_true: dict[str, Any] | None = None
    for point in points:
        _require(isinstance(point, Mapping), "invalid_numeric_point")
        _require_keys(point, ("state_point_utc", "value", "value_state", "member_key", "evidence_ref"), "numeric_point")
        moment = _as_datetime(point["state_point_utc"], "state_point_utc")
        _require(window_start <= moment < effective_end, "numeric_point_outside_observation_window")
        _require(int((moment - window_start).total_seconds()) % 300 == 0, "numeric_point_off_grid")
        _require(previous_time is None or previous_time < moment, "numeric_series_not_strictly_ordered")
        if previous_time is None and moment != window_start:
            gap_seen = True
        if previous_time is not None and int((moment - previous_time).total_seconds()) != 300:
            gap_seen = True
        state = point["value_state"]
        _require(state in {"known", "missing", "unknown"}, "invalid_numeric_value_state")
        if state == "known":
            _require(isinstance(point["value"], (int, float)) and not isinstance(point["value"], bool), "known_numeric_value_required")
        else:
            _require(point["value"] is None, "nonknown_numeric_value_must_be_null")
            gap_seen = True
        _validate_evidence_ref(point["evidence_ref"])
        validated.append(dict(point))
        if state == "known":
            truth = _threshold_matches(Fraction(str(point["value"])), threshold, comparison)
            if truth:
                first_true = dict(point)
                break
            last_known_false = dict(point)
        previous_time = moment
    if first_true is None and (previous_time is None or int((effective_end - previous_time).total_seconds()) > 300):
        gap_seen = True
    population_evidence = _population_evidence("OP-36", "ordered_numeric_points", points, [point["member_key"] for point in points], inherited_evidence_refs, population_evidence_binding, offline_structural_context)
    all_evidence = _merge_evidence((point["evidence_ref"] for point in validated), population_evidence)
    if first_true is not None:
        if validated[0]["member_key"] == first_true["member_key"] and not gap_seen:
            outcome = "left_censored"
            crossing_time = first_true["state_point_utc"]
            previous = None
            crossing_value = first_true["value"]
            evidence = _merge_evidence([first_true["evidence_ref"]], population_evidence)
        elif gap_seen or last_known_false is None:
            outcome = "indeterminate_gap"
            crossing_time = previous = crossing_value = None
            evidence = all_evidence
        else:
            outcome = "crossed"
            crossing_time = first_true["state_point_utc"]
            previous = last_known_false
            crossing_value = first_true["value"]
            evidence = _merge_evidence([last_known_false["evidence_ref"], first_true["evidence_ref"]], population_evidence)
    elif gap_seen:
        outcome = "indeterminate_gap"
        crossing_time = previous = crossing_value = None
        evidence = all_evidence
    else:
        outcome = "no_crossing"
        crossing_time = previous = crossing_value = None
        evidence = all_evidence
    result = {
        "outcome": outcome,
        "crossing_time_utc": crossing_time,
        "previous_time_utc": previous["state_point_utc"] if isinstance(previous, Mapping) else None,
        "previous_value": previous["value"] if isinstance(previous, Mapping) else None,
        "crossing_value": crossing_value,
        "profile_digest": profile["profile_digest"],
        "input_digest": _digest(inputs),
        "evidence_refs": evidence,
    }
    state = "left_censored" if outcome == "left_censored" else "indeterminate_gap" if outcome == "indeterminate_gap" else "empty" if outcome == "no_crossing" else "computed"
    return _result_envelope(env, result, evidence, result_state=state, completeness="not_computable" if outcome == "indeterminate_gap" else "complete")


OPERATOR_FUNCTIONS: dict[str, Callable[..., dict[str, Any]]] = {
    "OP-05": op05_as_severity_rank,
    "OP-06": op06_select_first_state_occurrence,
    "OP-07": op07_derive_state_intervals,
    "OP-08": op08_select_last_state_at_cutoff,
    "OP-09": op09_select_peak_state_observation,
    "OP-10": op10_compute_as_peak_complete_ratio,
    "OP-11": op11_select_longest_interval,
    "OP-12": op12_rank_as_first_threshold_crossing,
    "OP-13": op13_rank_as_longest_duration,
    "OP-14": op14_rank_as_peak_complete_ratio,
    "OP-15": op15_locate_asn_positions,
    "OP-16": op16_project_direct_path_neighbors,
    "OP-17": op17_classify_ordered_asn_path_relation,
    "OP-18": op18_project_path_prefix_set,
    "OP-19": op19_project_observed_downstream_origin_set,
    "OP-20": op20_project_canonical_path_set,
    "OP-21": op21_project_peer_direction_set,
    "OP-22": op22_count_unique_paths,
    "OP-23": op23_count_unique_prefixes,
    "OP-24": op24_count_unique_peer_directions,
    "OP-25": op25_set_intersection,
    "OP-26": op26_set_directional_difference,
    "OP-27": op27_set_directional_coverage,
    "OP-28": op28_set_jaccard,
    "OP-35": op35_select_last_state_occurrence,
    "OP-36": op36_detect_first_threshold_crossing,
}


def execute_operator(
    envelope: Mapping[str, Any],
    *,
    inherited_evidence_refs: Iterable[Any] = (),
    population_evidence_binding: Any = None,
    population_evidence_bindings: Any = None,
    asn_bound_op10_receipts: Sequence[Mapping[str, Any]] | None = None,
    asn_bound_op11_receipts: Sequence[Mapping[str, Any]] | None = None,
    asn_bound_op36_receipts: Sequence[Mapping[str, Any]] | None = None,
    offline_structural_context: Any = None,
) -> dict[str, Any]:
    """执行恰好一个显式登记的 Operator；不做计划展开或批量 fan-out。"""

    _require(isinstance(envelope, Mapping), "invalid_operator_envelope")
    operator_id = envelope.get("operator_id")
    function = OPERATOR_FUNCTIONS.get(operator_id)
    _require(function is not None, "operator_not_registered", str(operator_id))
    kwargs: dict[str, Any] = {"inherited_evidence_refs": inherited_evidence_refs}
    if operator_id in {"OP-06", "OP-07", "OP-08", "OP-09", "OP-11", "OP-12", "OP-13", "OP-14", "OP-18", "OP-20", "OP-21", "OP-22", "OP-23", "OP-24", "OP-35", "OP-36"}:
        kwargs["population_evidence_binding"] = population_evidence_binding
    if operator_id in {"OP-25", "OP-26", "OP-27", "OP-28"}:
        kwargs["population_evidence_bindings"] = population_evidence_bindings
    if operator_id == "OP-13":
        kwargs["asn_bound_op11_receipts"] = asn_bound_op11_receipts
    if operator_id == "OP-12":
        kwargs["asn_bound_op36_receipts"] = asn_bound_op36_receipts
    if operator_id == "OP-14":
        kwargs["asn_bound_op10_receipts"] = asn_bound_op10_receipts
    if operator_id in {"OP-06", "OP-07", "OP-08", "OP-09", "OP-11", "OP-12", "OP-13", "OP-14", "OP-16", "OP-17", "OP-18", "OP-19", "OP-20", "OP-21", "OP-22", "OP-23", "OP-24", "OP-25", "OP-26", "OP-27", "OP-28", "OP-35", "OP-36"}:
        kwargs["offline_structural_context"] = offline_structural_context
    return function(envelope, **kwargs)


__all__ = [
    "OPERATOR_FUNCTIONS",
    "OPERATOR_VERSION",
    "OperatorContractError",
    "OfflineStructuralFixtureContext",
    "execute_operator",
    "validate_population_evidence_binding",
    *(function.__name__ for function in OPERATOR_FUNCTIONS.values()),
]
