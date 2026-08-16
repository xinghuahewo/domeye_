from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest

from backend.services import country_outage_p2_s1_operators as operators


D = "a" * 64
E = "b" * 64
F = "c" * 64


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def evidence(member_key="population", source_digest=E):
    return {"evidence_id": f"e-{member_key}", "source_digest": source_digest, "member_key": member_key}


def identity(publication_id="pub-1"):
    return {
        "incident_id": "incident-1", "publication_id": publication_id, "publication_revision": 1,
        "publication_digest": D, "collector_id": "rrc25", "cohort_id": "cohort-1",
        "cohort_digest": E, "window_start_utc": "2026-02-27T00:00:00Z",
        "window_end_utc": "2026-02-28T00:00:00Z", "data_through_utc": "2026-02-28T00:00:00Z",
        "registry_snapshot_id": "registry-1", "registry_snapshot_digest": F, "binding_generation": 1,
    }


PROFILES = {
    "OP-05": "PROFILE-AS-SEVERITY-RANK-1.0.0", "OP-06": "PROFILE-STATE-TARGET-1.0.0",
    "OP-07": "PROFILE-STATE-INTERVAL-1.0.0", "OP-09": "PROFILE-PEAK-SEVERITY-1.0.0",
    "OP-12": "PROFILE-FIRST-CROSSING-1.0.0", "OP-13": "PROFILE-STATE-INTERVAL-1.0.0",
    "OP-35": "PROFILE-STATE-TARGET-1.0.0", "OP-36": "PROFILE-FIRST-CROSSING-1.0.0",
    "OP-29": "PROFILE-TEMPORAL-COMPARABILITY-1.0.0", "OP-30": "PROFILE-VP-CONSISTENCY-1.0.0",
    "OP-31": "PROFILE-VP-CONSISTENCY-1.0.0", "OP-32": "PROFILE-VP-CONSISTENCY-1.0.0",
    "OP-37": "PROFILE-EVIDENCE-CONSISTENCY-1.0.0",
}


def envelope(operator_id, inputs, *, bound_identity=None):
    bound_identity = bound_identity or identity()
    payload = dict(inputs)
    payload["identity"] = bound_identity
    profile = PROFILES.get(operator_id)
    return {
        "identity": bound_identity, "operator_id": operator_id, "operator_version": "1.0.0-design",
        "parameter_profile_id": profile, "parameter_profile_digest": D if profile else None,
        "input_completeness": "complete", "inputs": payload, "input_digests": [digest(payload)],
    }


def population_binding(operator_id, input_name, operator_input, member_keys):
    completeness = F
    receipt = {
        "schema_version": "country_outage_p2_s1_population_evidence_binding_receipt_v1",
        "receipt_kind": "population_evidence_binding",
        "design_candidate_id": operators.DESIGN_CANDIDATE_ID,
        "operator_id": operator_id, "operator_input_name": input_name,
        "operator_input_digest": digest(operator_input),
        "source_population_ref": {
            "source_kind": "frozen_result_set", "artifact_id": "result-set-1", "artifact_revision": 1,
            "population_id": input_name, "content_digest": D, "manifest_digest": E,
            "completeness_receipt_digest": completeness,
        },
        "set_completeness": "complete", "member_count": len(member_keys),
        "member_keys_digest": digest(sorted(member_keys)),
        "population_evidence_ref": evidence(f"population:{input_name}", completeness),
        "validator": {"validator_id": operators.STRUCTURAL_VALIDATOR_ID, "validator_version": "1.0.0", "contract_digest": D, "implementation_digest": E},
        "business_transform_count": 0,
    }
    receipt["receipt_digest"] = digest(receipt)
    return receipt


def offline_context(*bindings, op10_outputs=(), op11_outputs=(), op15_outputs=(), op29_outputs=(), op36_outputs=(), tool12_result_sets=(), projection_receipts=()):
    return operators.OfflineStructuralFixtureContext(
        design_candidate_id=operators.DESIGN_CANDIDATE_ID,
        population_binding_receipts={item["receipt_digest"]: item for item in bindings},
        op29_outputs={item["output_digest"]: item for item in op29_outputs},
        op10_outputs={item["output_digest"]: item for item in op10_outputs},
        op11_outputs={item["output_digest"]: item for item in op11_outputs},
        op15_outputs={item["output_digest"]: item for item in op15_outputs},
        op36_outputs={item["output_digest"]: item for item in op36_outputs},
        tool12_result_sets={item["content_digest"]: item for item in tool12_result_sets},
        projection_receipts={item["receipt_digest"]: item for item in projection_receipts},
    )


def binding_kwargs(binding):
    return {"population_evidence_binding": binding, "offline_structural_context": offline_context(binding)}


def asn_operator_binding(source_operator_id, target_operator_id, asn, output):
    source = source_operator_id.lower().replace("-", "")
    receipt = {
        "schema_version": f"country_outage_p2_s1_asn_bound_{source}_receipt_v1",
        "receipt_kind": f"asn_bound_{source}_receipt",
        "design_candidate_id": operators.DESIGN_CANDIDATE_ID,
        "target_operator_id": target_operator_id,
        "asn": asn,
        f"{source}_output_digest": output["output_digest"],
        f"{source}_input_digest": output["result"]["input_digest"],
        "source_plan_id": "plan-1", "source_plan_revision": 1,
        "source_plan_node_id": f"node-{source}-{asn}",
        "source_node_result_digest": output["output_digest"],
        "source_asn_binding_digest": digest({"asn": asn, "node": f"node-{source}-{asn}"}),
        "evidence_refs": [evidence(f"binding-{source}-{asn}")],
        "validator": {"validator_id": operators.STRUCTURAL_VALIDATOR_ID, "validator_version": "1.0.0", "contract_digest": D, "implementation_digest": E},
        "business_transform_count": 0,
    }
    receipt["receipt_digest"] = digest(receipt)
    return receipt


def op10_projection(output):
    projected = deepcopy(output)
    projected["result"] = {key: output["result"][key] for key in ("asn", "numerator", "denominator", "ratio_exact", "outcome")}
    return projected


def op36_projection(output, asn):
    return {
        "identity": output["identity"], "operator_id": "OP-36", "asn": asn,
        "outcome": output["result"]["outcome"], "crossing_time_utc": output["result"]["crossing_time_utc"],
        "profile_digest": output["result"]["profile_digest"], "input_digest": output["result"]["input_digest"],
        "output_digest": output["output_digest"], "evidence_refs": output["evidence_refs"],
    }


def state_point(minute, classification):
    timestamp = f"2026-02-27T00:{minute:02d}:00Z"
    return {"state_point_utc": timestamp, "classification": classification, "member_key": timestamp, "evidence_ref": evidence(timestamp)}


def path_row(path_digest=D, prefix="109.74.224.0/20", directions=None):
    return {
        "path_digest": path_digest, "path_canonicalization_profile_id": operators.PATH_PROFILE_ID,
        "path_canonicalization_profile_digest": operators.PATH_PROFILE_DIGEST, "prefix": prefix,
        "afi": 4, "peer_asn_direction_ids": directions or ["rrc25:peer:1"], "evidence_ref": evidence(path_digest),
    }


def typed_set(members, member_type_id="asn"):
    ordered = sorted(members, key=lambda item: json.dumps(item, sort_keys=True))
    return {"member_type_id": member_type_id, "members": ordered, "declared_member_count": len(ordered), "set_completeness": "complete", "set_digest": digest(ordered)}


def temporal_profile(*, population_compatible=True, unit_compatible=True, pair=("peak", "peak")):
    return {
        "profile_id": "PROFILE-TEMPORAL-COMPARABILITY-1.0.0", "profile_digest": D,
        "fact_type_pair": list(pair), "population_compatible": population_compatible,
        "unit_compatible": unit_compatible, "time_basis": "publication_state_point_grid",
        "granularity_seconds": 300, "tolerance_seconds": 300,
    }


def timed_fact(name, time_utc, *, fact_type="peak", population="country", unit="count"):
    return {
        "fact_type_id": fact_type, "temporal_kind": "exact_point", "time_utc": time_utc,
        "population_id": population, "unit_id": unit, "fact_digest": digest({"fact": name}),
        "evidence_refs": [evidence(f"timed:{name}")],
    }


def typed_fact(name, predicate, *, truth="true", fact_type="peak", population="country", unit="count"):
    return {
        "fact_type_id": fact_type, "population_id": population, "unit_id": unit,
        "predicate_id": predicate, "truth_state": truth, "fact_digest": digest({"fact": name}),
        "evidence_refs": [evidence(f"fact:{name}")],
    }


def op29_receipt(output):
    return {
        "identity": output["identity"], "operator_id": "OP-29",
        **{key: output["result"][key] for key in ("left_digest", "right_digest", "relation", "comparable", "profile_digest")},
        "output_digest": output["output_digest"], "evidence_refs": output["evidence_refs"],
    }


def state_interval(start, end, member=D):
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return {
        "start_utc": start, "end_utc": end, "duration_seconds": int((end_dt - start_dt).total_seconds()),
        "left_censored": False, "right_censored": False, "member_digests": [member],
    }


def fixed_member(member_id, prefix, *, asns=(64500,), basis="country_origin_known"):
    return {
        "cohort_member_id": member_id, "prefix": prefix, "afi": 6 if ":" in prefix else 4,
        "country_origin_asns": list(asns), "expected_peer_asn_direction_ids": ["peer:1"],
        "expected_route_observation_keys": ["rrc25:peer:1"], "membership_basis": basis,
        "evidence_ref": evidence(f"cohort:{member_id}"),
    }


class OperatorW1Tests(unittest.TestCase):
    def test_op05_three_keys_competition_rank_and_position(self):
        members = [
            {"asn": 20, "peak_invisible_direction_count": 9, "peak_complete_prefix_count": 2, "fixed_prefix_count": 4, "evidence_ref": evidence("20")},
            {"asn": 10, "peak_invisible_direction_count": 9, "peak_complete_prefix_count": 2, "fixed_prefix_count": 5, "evidence_ref": evidence("10")},
            {"asn": 30, "peak_invisible_direction_count": 9, "peak_complete_prefix_count": 1, "fixed_prefix_count": 3, "evidence_ref": evidence("30")},
            {"asn": 40, "peak_invisible_direction_count": 8, "peak_complete_prefix_count": 99, "fixed_prefix_count": 99, "evidence_ref": evidence("40")},
        ]
        out = operators.op05_as_severity_rank(envelope("OP-05", {"members": members, "set_completeness": "complete", "population_evidence_ref": evidence()}))
        self.assertEqual(out["result"]["ordered_asns"], [10, 20, 30, 40])
        self.assertEqual([item["severity_rank_global"] for item in out["result"]["ranked_members"]], [1, 1, 3, 4])
        self.assertEqual([item["result_position"] for item in out["result"]["ranked_members"]], [1, 2, 3, 4])

    def test_state_first_last_cutoff_intervals_and_censoring(self):
        points = [state_point(0, "complete"), state_point(5, "complete"), state_point(10, "normal"), state_point(15, "complete")]
        common = population_binding("OP-06", "ordered_state_points", points, [p["member_key"] for p in points])
        first = operators.op06_select_first_state_occurrence(envelope("OP-06", {"ordered_state_points": points, "target_state": "complete", "series_digest": D}), **binding_kwargs(common))
        self.assertEqual(first["result"]["outcome"], "left_censored")
        last_binding = population_binding("OP-35", "ordered_state_points", points, [p["member_key"] for p in points])
        last = operators.op35_select_last_state_occurrence(envelope("OP-35", {"ordered_state_points": points, "target_state": "complete", "series_digest": D}), **binding_kwargs(last_binding))
        self.assertEqual(last["result"]["outcome"], "right_censored")
        cutoff_binding = population_binding("OP-08", "ordered_state_points", points, [p["member_key"] for p in points])
        cutoff = operators.op08_select_last_state_at_cutoff(envelope("OP-08", {"ordered_state_points": points, "cutoff_utc": "2026-02-27T00:11:00Z", "series_digest": D}), **binding_kwargs(cutoff_binding))
        self.assertEqual(cutoff["result"]["classification"], "normal")
        interval_binding = population_binding("OP-07", "ordered_state_points", points, [p["member_key"] for p in points])
        intervals = operators.op07_derive_state_intervals(envelope("OP-07", {"ordered_state_points": points, "target_state": "complete", "grid_step_seconds": 300, "window": {"start_utc": "2026-02-27T00:00:00Z", "end_utc": "2026-02-28T00:00:00Z"}, "series_digest": D}), **binding_kwargs(interval_binding))
        self.assertEqual([i["duration_seconds"] for i in intervals["result"]["intervals"]], [600, 300])
        self.assertTrue(intervals["result"]["intervals"][0]["left_censored"])
        self.assertFalse(intervals["result"]["intervals"][-1]["right_censored"])

    def test_interval_gap_and_unknown_break_runs(self):
        points = [state_point(0, "complete"), state_point(10, "complete"), state_point(15, "unknown"), state_point(20, "complete")]
        binding = population_binding("OP-07", "ordered_state_points", points, [p["member_key"] for p in points])
        out = operators.op07_derive_state_intervals(envelope("OP-07", {"ordered_state_points": points, "target_state": "complete", "grid_step_seconds": 300, "window": {"start_utc": "2026-02-27T00:00:00Z", "end_utc": "2026-02-28T00:00:00Z"}, "series_digest": D}), **binding_kwargs(binding))
        self.assertEqual(out["result"]["interval_count"], 3)

    def test_peak_ratio_and_exact_ratio_rank(self):
        points = [
            {"state_point_utc": "2026-02-27T00:00:00Z", "asn": 10, "classification": "affected", "fixed_prefix_count": 10, "partial_prefix_count": 1, "complete_prefix_count": 3, "unknown_prefix_count": 0, "invisible_direction_count": 8, "evidence_ref": evidence("p0")},
            {"state_point_utc": "2026-02-27T00:05:00Z", "asn": 10, "classification": "affected", "fixed_prefix_count": 10, "partial_prefix_count": 1, "complete_prefix_count": 3, "unknown_prefix_count": 0, "invisible_direction_count": 8, "evidence_ref": evidence("p1")},
        ]
        binding = population_binding("OP-09", "ordered_state_points", points, ["p0", "p1"])
        peak = operators.op09_select_peak_state_observation(envelope("OP-09", {"ordered_state_points": points, "severity_field": "complete_prefix_count", "series_digest": D}), **binding_kwargs(binding))
        self.assertEqual(len(peak["result"]["peak_state_points"]), 2)
        r1 = operators.op10_compute_as_peak_complete_ratio(envelope("OP-10", {"asn": 10, "peak_complete_prefix_count": 1, "fixed_prefix_count": 2, "member_digest": D}), inherited_evidence_refs=[evidence("r1")])
        r2 = operators.op10_compute_as_peak_complete_ratio(envelope("OP-10", {"asn": 20, "peak_complete_prefix_count": 2, "fixed_prefix_count": 4, "member_digest": E}), inherited_evidence_refs=[evidence("r2")])
        receipts = [op10_projection(r2), op10_projection(r1)]
        ratio_bindings = [asn_operator_binding("OP-10", "OP-14", output["result"]["asn"], output) for output in (r2, r1)]
        rank_binding = population_binding("OP-14", "ratio_receipts", receipts, [r["output_digest"] for r in receipts])
        ranked = operators.op14_rank_as_peak_complete_ratio(envelope("OP-14", {"ratio_receipts": receipts, "set_completeness": "complete"}), population_evidence_binding=rank_binding, asn_bound_op10_receipts=ratio_bindings, offline_structural_context=offline_context(rank_binding, op10_outputs=[r2, r1]))
        self.assertEqual([(r["asn"], r["rank"]) for r in ranked["result"]["ranked"]], [(10, 1), (20, 1)])

    def test_threshold_crossing_left_censored_gap_and_rank(self):
        profile = {"profile_id": "PROFILE-FIRST-CROSSING-1.0.0", "profile_version": "1.0.0", "metric_field": "complete_prefix_count", "threshold_exact": {"numerator": 5, "denominator": 1}, "comparison": "gte", "grid_step_seconds": 300, "gap_policy": "indeterminate_if_any_gap_precedes_candidate_or_prevents_no_crossing_proof", "profile_digest": D}
        points = [
            {"state_point_utc": "2026-02-27T00:00:00Z", "value": 1, "value_state": "known", "member_key": "n0", "evidence_ref": evidence("n0")},
            {"state_point_utc": "2026-02-27T00:05:00Z", "value": 5, "value_state": "known", "member_key": "n1", "evidence_ref": evidence("n1")},
        ]
        binding = population_binding("OP-36", "ordered_numeric_points", points, ["n0", "n1"])
        crossed = operators.op36_detect_first_threshold_crossing(envelope("OP-36", {"ordered_numeric_points": points, "threshold_profile_instance": profile, "series_digest": E}), **binding_kwargs(binding))
        self.assertEqual(crossed["result"]["outcome"], "crossed")
        receipt = op36_projection(crossed, 10)
        crossing_binding = asn_operator_binding("OP-36", "OP-12", 10, crossed)
        rb = population_binding("OP-12", "crossing_receipts", [receipt], [receipt["output_digest"]])
        ranked = operators.op12_rank_as_first_threshold_crossing(envelope("OP-12", {"crossing_receipts": [receipt], "set_completeness": "complete", "profile_digest": D}), population_evidence_binding=rb, asn_bound_op36_receipts=[crossing_binding], offline_structural_context=offline_context(rb, op36_outputs=[crossed]))
        self.assertEqual(ranked["result"]["ranked"][0]["asn"], 10)
        gap = deepcopy(points)
        gap[0]["value"], gap[0]["value_state"] = None, "missing"
        gb = population_binding("OP-36", "ordered_numeric_points", gap, ["n0", "n1"])
        self.assertEqual(operators.op36_detect_first_threshold_crossing(envelope("OP-36", {"ordered_numeric_points": gap, "threshold_profile_instance": profile, "series_digest": E}), **binding_kwargs(gb))["result"]["outcome"], "indeterminate_gap")
        leading_gap = deepcopy(points)
        leading_gap[0]["state_point_utc"] = "2026-02-27T00:05:00Z"
        leading_gap[0]["member_key"] = "n5"
        leading_gap[1]["state_point_utc"] = "2026-02-27T00:10:00Z"
        leading_gap[1]["member_key"] = "n10"
        lgb = population_binding("OP-36", "ordered_numeric_points", leading_gap, ["n5", "n10"])
        self.assertEqual(operators.op36_detect_first_threshold_crossing(envelope("OP-36", {"ordered_numeric_points": leading_gap, "threshold_profile_instance": profile, "series_digest": E}), **binding_kwargs(lgb))["result"]["outcome"], "indeterminate_gap")

    def test_op10_op36_trusted_projection_binding_attacks(self):
        full10 = operators.op10_compute_as_peak_complete_ratio(envelope("OP-10", {"asn": 10, "peak_complete_prefix_count": 1, "fixed_prefix_count": 2, "member_digest": D}), inherited_evidence_refs=[evidence("op10")])
        projected10 = op10_projection(full10)
        binding10 = asn_operator_binding("OP-10", "OP-14", 10, full10)
        population10 = population_binding("OP-14", "ratio_receipts", [projected10], [full10["output_digest"]])
        env10 = envelope("OP-14", {"ratio_receipts": [projected10], "set_completeness": "complete"})
        context10 = offline_context(population10, op10_outputs=[full10])
        with self.assertRaisesRegex(operators.OperatorContractError, "receipts_required"):
            operators.op14_rank_as_peak_complete_ratio(env10, population_evidence_binding=population10, offline_structural_context=context10)
        tampered_projection = deepcopy(projected10); tampered_projection["result"]["numerator"] = 0
        tampered_population = population_binding("OP-14", "ratio_receipts", [tampered_projection], [full10["output_digest"]])
        with self.assertRaisesRegex(operators.OperatorContractError, "projection_mismatch"):
            operators.op14_rank_as_peak_complete_ratio(envelope("OP-14", {"ratio_receipts": [tampered_projection], "set_completeness": "complete"}), population_evidence_binding=tampered_population, asn_bound_op10_receipts=[binding10], offline_structural_context=offline_context(tampered_population, op10_outputs=[full10]))
        swapped10 = deepcopy(binding10); swapped10["asn"] = 11
        with self.assertRaisesRegex(operators.OperatorContractError, "receipt_digest_mismatch|asn_binding_mismatch"):
            operators.op14_rank_as_peak_complete_ratio(env10, population_evidence_binding=population10, asn_bound_op10_receipts=[swapped10], offline_structural_context=context10)
        ghost10 = deepcopy(binding10); ghost10["op10_output_digest"] = F; ghost10["receipt_digest"] = digest({key: value for key, value in ghost10.items() if key != "receipt_digest"})
        with self.assertRaisesRegex(operators.OperatorContractError, "missing_op10_binding|ghost"):
            operators.op14_rank_as_peak_complete_ratio(env10, population_evidence_binding=population10, asn_bound_op10_receipts=[binding10, ghost10], offline_structural_context=context10)

        profile = {"profile_id": "PROFILE-FIRST-CROSSING-1.0.0", "profile_version": "1.0.0", "metric_field": "complete_prefix_count", "threshold_exact": {"numerator": 1, "denominator": 1}, "comparison": "gte", "grid_step_seconds": 300, "gap_policy": "indeterminate_if_any_gap_precedes_candidate_or_prevents_no_crossing_proof", "profile_digest": D}
        points = [{"state_point_utc": "2026-02-27T00:00:00Z", "value": 0, "value_state": "known", "member_key": "n0", "evidence_ref": evidence("n0")}, {"state_point_utc": "2026-02-27T00:05:00Z", "value": 1, "value_state": "known", "member_key": "n1", "evidence_ref": evidence("n1")}]
        source_population = population_binding("OP-36", "ordered_numeric_points", points, ["n0", "n1"])
        full36 = operators.op36_detect_first_threshold_crossing(envelope("OP-36", {"ordered_numeric_points": points, "threshold_profile_instance": profile, "series_digest": E}), **binding_kwargs(source_population))
        projected36 = op36_projection(full36, 10)
        binding36 = asn_operator_binding("OP-36", "OP-12", 10, full36)
        population36 = population_binding("OP-12", "crossing_receipts", [projected36], [full36["output_digest"]])
        env36 = envelope("OP-12", {"crossing_receipts": [projected36], "set_completeness": "complete", "profile_digest": D})
        context36 = offline_context(population36, op36_outputs=[full36])
        with self.assertRaisesRegex(operators.OperatorContractError, "receipts_required"):
            operators.op12_rank_as_first_threshold_crossing(env36, population_evidence_binding=population36, offline_structural_context=context36)
        tampered36 = deepcopy(projected36); tampered36["asn"] = 11
        tampered_population36 = population_binding("OP-12", "crossing_receipts", [tampered36], [full36["output_digest"]])
        with self.assertRaisesRegex(operators.OperatorContractError, "asn_binding_mismatch"):
            operators.op12_rank_as_first_threshold_crossing(envelope("OP-12", {"crossing_receipts": [tampered36], "set_completeness": "complete", "profile_digest": D}), population_evidence_binding=tampered_population36, asn_bound_op36_receipts=[binding36], offline_structural_context=offline_context(tampered_population36, op36_outputs=[full36]))
        crossed = deepcopy(full36); crossed["identity"] = identity("pub-2"); crossed["output_digest"] = digest({key: value for key, value in crossed.items() if key != "output_digest"})
        with self.assertRaisesRegex(operators.OperatorContractError, "cross_identity|not_found"):
            operators.op12_rank_as_first_threshold_crossing(env36, population_evidence_binding=population36, asn_bound_op36_receipts=[binding36], offline_structural_context=offline_context(population36, op36_outputs=[crossed]))

    def test_complete_empty_requires_valid_population_binding(self):
        env = envelope("OP-06", {"ordered_state_points": [], "target_state": "complete", "series_digest": D})
        with self.assertRaisesRegex(operators.OperatorContractError, "population_evidence_binding_required"):
            operators.op06_select_first_state_occurrence(env)
        with self.assertRaisesRegex(operators.OperatorContractError, "population_evidence_binding_required"):
            operators.op06_select_first_state_occurrence(env, inherited_evidence_refs=[evidence("arbitrary")])
        binding = population_binding("OP-06", "ordered_state_points", [], [])
        self.assertEqual(operators.op06_select_first_state_occurrence(env, **binding_kwargs(binding))["result"]["outcome"], "no_match")
        tampered = deepcopy(binding)
        tampered["member_count"] = 1
        with self.assertRaisesRegex(operators.OperatorContractError, "member_count_mismatch|receipt_digest_mismatch"):
            operators.op06_select_first_state_occurrence(env, population_evidence_binding=tampered, offline_structural_context=offline_context(tampered))
        cross = population_binding("OP-07", "ordered_state_points", [], [])
        with self.assertRaisesRegex(operators.OperatorContractError, "operator_mismatch"):
            operators.op06_select_first_state_occurrence(env, population_evidence_binding=cross, offline_structural_context=offline_context(cross))

    def test_op13_asn_binding_normal_ties_and_attacks(self):
        interval = {"start_utc": "2026-02-27T00:00:00Z", "end_utc": "2026-02-27T00:10:00Z", "duration_seconds": 600, "left_censored": True, "right_censored": False, "member_digests": [D]}
        full_outputs = []
        outputs = []
        bindings = []
        for asn in (20, 10):
            ib = population_binding("OP-11", "intervals", [interval], [digest(interval)])
            out = operators.op11_select_longest_interval(envelope("OP-11", {"intervals": [interval], "set_completeness": "complete", "input_digest": E}), **binding_kwargs(ib))
            # 使两个输出摘要不同，但业务时长相同。
            if outputs:
                second_interval = deepcopy(interval)
                second_interval["member_digests"] = [F]
                ib = population_binding("OP-11", "intervals", [second_interval], [digest(second_interval)])
                out = operators.op11_select_longest_interval(envelope("OP-11", {"intervals": [second_interval], "set_completeness": "complete", "input_digest": F}), **binding_kwargs(ib))
            full_outputs.append(out)
            projection = deepcopy(out)
            projection["result"] = {key: out["result"][key] for key in ("outcome", "duration_seconds", "intervals")}
            outputs.append(projection)
            b = {
                "schema_version": "country_outage_p2_s1_asn_bound_op11_receipt_v1", "receipt_kind": "asn_bound_op11_receipt",
                "design_candidate_id": operators.DESIGN_CANDIDATE_ID, "target_operator_id": "OP-13", "asn": asn,
                "op11_output_digest": out["output_digest"], "op11_input_digest": out["result"]["input_digest"],
                "source_plan_id": "plan-1", "source_plan_revision": 1, "source_plan_node_id": f"node-{asn}",
                "source_node_result_digest": out["output_digest"], "source_asn_binding_digest": digest({"asn": asn, "node": f"node-{asn}"}),
                "evidence_refs": [evidence(f"binding-{asn}")],
                "validator": {"validator_id": operators.STRUCTURAL_VALIDATOR_ID, "validator_version": "1.0.0", "contract_digest": D, "implementation_digest": E},
                "business_transform_count": 0,
            }
            b["receipt_digest"] = digest(b)
            bindings.append(b)
        pb = population_binding("OP-13", "longest_interval_receipts", outputs, [o["output_digest"] for o in outputs])
        env = envelope("OP-13", {"longest_interval_receipts": outputs, "set_completeness": "complete"})
        context = offline_context(pb, op11_outputs=full_outputs)
        result = operators.op13_rank_as_longest_duration(env, population_evidence_binding=pb, asn_bound_op11_receipts=bindings, offline_structural_context=context)
        self.assertEqual([(r["asn"], r["rank"]) for r in result["result"]["ranked"]], [(10, 1), (20, 1)])
        with self.assertRaisesRegex(operators.OperatorContractError, "missing_asn_binding"):
            operators.op13_rank_as_longest_duration(env, population_evidence_binding=pb, asn_bound_op11_receipts=bindings[:1], offline_structural_context=context)
        with self.assertRaisesRegex(operators.OperatorContractError, "duplicate_asn_binding"):
            operators.op13_rank_as_longest_duration(env, population_evidence_binding=pb, asn_bound_op11_receipts=bindings + [bindings[0]], offline_structural_context=context)
        ghost = deepcopy(bindings[0]); ghost["op11_output_digest"] = F; ghost["receipt_digest"] = digest({k: v for k, v in ghost.items() if k != "receipt_digest"})
        with self.assertRaisesRegex(operators.OperatorContractError, "ghost|missing"):
            operators.op13_rank_as_longest_duration(env, population_evidence_binding=pb, asn_bound_op11_receipts=bindings + [ghost], offline_structural_context=context)
        swapped = deepcopy(bindings); swapped[0]["asn"] = 999
        with self.assertRaisesRegex(operators.OperatorContractError, "receipt_digest_mismatch"):
            operators.op13_rank_as_longest_duration(env, population_evidence_binding=pb, asn_bound_op11_receipts=swapped, offline_structural_context=context)
        crossed = deepcopy(outputs); crossed[0]["identity"] = identity("pub-2")
        cpb = population_binding("OP-13", "longest_interval_receipts", crossed, [o["output_digest"] for o in crossed])
        with self.assertRaisesRegex(operators.OperatorContractError, "cross_identity"):
            operators.op13_rank_as_longest_duration(envelope("OP-13", {"longest_interval_receipts": crossed, "set_completeness": "complete"}), population_evidence_binding=cpb, asn_bound_op11_receipts=bindings, offline_structural_context=offline_context(cpb, op11_outputs=full_outputs))


class OperatorW2Tests(unittest.TestCase):
    def setUp(self):
        self.segments = [{"segment_type": "as_sequence", "asns": [33874, 49666, 48159, 58224, 49666]}]
        self.path_digest = digest(self.segments)
        self.path_evidence = evidence("path")

    def op15(self, target):
        return operators.op15_locate_asn_positions(envelope("OP-15", {"path_id": "path-1", "path_digest": self.path_digest, "path_canonicalization_profile_id": operators.PATH_PROFILE_ID, "path_canonicalization_profile_digest": operators.PATH_PROFILE_DIGEST, "path_segments": self.segments, "target_asn": target, "common_path_status": "ordered"}), inherited_evidence_refs=[self.path_evidence])

    def compact_op15(self, output):
        result = output["result"]
        return {"identity": output["identity"], "operator_id": "OP-15", "path_digest": result["path_digest"], "path_canonicalization_profile_id": result["path_canonicalization_profile_id"], "path_canonicalization_profile_digest": result["path_canonicalization_profile_digest"], "target_asn": result["target_asn"], "outcome": result["outcome"], "ordered_positions": result["ordered_positions"], "input_digest": result["input_digest"], "output_digest": output["output_digest"], "evidence_refs": output["evidence_refs"]}

    def test_path_positions_prepend_neighbors_and_order(self):
        left_output = self.op15(49666)
        right_output = self.op15(58224)
        left = self.compact_op15(left_output)
        right = self.compact_op15(right_output)
        context = offline_context(op15_outputs=[left_output, right_output])
        self.assertEqual(left["ordered_positions"], [1, 4])
        neighbors = operators.op16_project_direct_path_neighbors(envelope("OP-16", {"path_segments": self.segments, "path_digest": self.path_digest, "op15_position_receipt": left}), offline_structural_context=context)
        self.assertEqual([n["neighbor_asn"] for n in neighbors["result"]["right_neighbors"]], [48159])
        relation = operators.op17_classify_ordered_asn_path_relation(envelope("OP-17", {"left_position_receipt": left, "right_position_receipt": right, "path_digest": self.path_digest}), offline_structural_context=context)
        self.assertEqual(relation["result"]["relation"], "both_orders")

    def test_op15_compact_receipt_requires_resolved_full_output(self):
        left_output = self.op15(49666)
        right_output = self.op15(58224)
        left = self.compact_op15(left_output)
        right = self.compact_op15(right_output)
        context = offline_context(op15_outputs=[left_output, right_output])
        forged = deepcopy(left)
        forged["output_digest"] = F
        with self.assertRaisesRegex(operators.OperatorContractError, "offline_op15_output_not_found"):
            operators.op17_classify_ordered_asn_path_relation(envelope("OP-17", {"left_position_receipt": forged, "right_position_receipt": right, "path_digest": self.path_digest}), offline_structural_context=context)

    def test_unordered_path_is_not_linearized(self):
        unordered_segments = [{"segment_type": "as_set", "asns": [1, 2]}]
        env = envelope("OP-15", {"path_id": "path-1", "path_digest": digest(unordered_segments), "path_canonicalization_profile_id": operators.PATH_PROFILE_ID, "path_canonicalization_profile_digest": operators.PATH_PROFILE_DIGEST, "path_segments": unordered_segments, "target_asn": 1, "common_path_status": "unordered"})
        output = operators.op15_locate_asn_positions(env, inherited_evidence_refs=[self.path_evidence])
        self.assertEqual(output["result"]["outcome"], "unordered")
        self.assertEqual(output["result"]["ordered_positions"], [])

    def test_path_prefix_path_direction_projections(self):
        rows = [path_row(D, "109.74.224.0/20", ["p2", "p1"]), path_row(E, "109.74.224.0/20", ["p1"])]
        member_keys = [row["path_digest"] + ":" + row["prefix"] for row in rows]
        prefix_binding = population_binding("OP-18", "path_evidence_members", rows, member_keys)
        prefix = operators.op18_project_path_prefix_set(envelope("OP-18", {"path_evidence_members": rows, "set_completeness": "complete", "input_digest": D}), **binding_kwargs(prefix_binding))
        self.assertEqual(prefix["result"]["members"], [{"afi": 4, "prefix": "109.74.224.0/20"}])
        path_binding = population_binding("OP-20", "path_evidence_members", rows, member_keys)
        paths = operators.op20_project_canonical_path_set(envelope("OP-20", {"path_evidence_members": rows, "canonicalization_profile_digest": operators.PATH_PROFILE_DIGEST, "set_completeness": "complete"}), **binding_kwargs(path_binding))
        self.assertEqual(paths["result"]["members"], [D, E])
        direction_binding = population_binding("OP-21", "path_evidence_members", rows, member_keys)
        directions = operators.op21_project_peer_direction_set(envelope("OP-21", {"path_evidence_members": rows, "direction_identity_profile_digest": F, "set_completeness": "complete"}), **binding_kwargs(direction_binding))
        self.assertEqual(directions["result"]["members"], ["p1", "p2"])

    def test_observed_downstream_projection_not_customer_cone(self):
        source = {"result_set_id": "rs", "result_set_revision": 1, "manifest_digest": D, "content_digest": E, "freeze_receipt_digest": F, "query_receipt_digest": D, "source_population_id": "window_path_association_evidence_rows", "source_dataset_digest": E, "member_identity": "path_association_id"}
        association = {"source_member_key": "a1", "source_member_digest": D, "anchor_asn": 49666, "known_origin_asn": 58224, "origin_status": "known", "observed_origin_asn": 58224, "path_digest": E, "path_canonicalization_profile_id": operators.PATH_PROFILE_ID, "path_canonicalization_profile_digest": operators.PATH_PROFILE_DIGEST, "evidence_ref": evidence("a1")}
        source["query_receipt_digest"] = D
        source_result_set = {"identity": identity(), "result_set_id": "rs", "result_set_revision": 1, "content_digest": E, "manifest_digest": D, "query_receipt_digest": D, "completeness": "complete", "normalized_query": {"anchor_asn": 49666, "anchor_before_known_origin": True}, "members": [{"source_member_key": "a1", "source_member_digest": D}]}
        projection = {"design_candidate_id": operators.DESIGN_CANDIDATE_ID, "operator_id": "OP-19", "anchor_asn": 49666, "source_result_set_content_digest": E, "query_receipt_digest": D, "source_member_keys_digest": digest(["a1"]), "source_member_digests_digest": digest([D]), "projected_member_keys_digest": digest(["a1"]), "projected_member_digests_digest": digest([D]), "business_transform_count": 0}
        projection["receipt_digest"] = digest(projection)
        source["manifest_digest"] = D
        output = operators.op19_project_observed_downstream_origin_set(envelope("OP-19", {"anchor_asn": 49666, "source_result_set_ref": source, "association_members": [association], "set_completeness": "complete", "source_result_set_query_receipt_digest": D, "population_filter_receipt_digest": D, "host_projection_receipt_digest": projection["receipt_digest"], "population_evidence_ref": evidence(source_digest=D)}), offline_structural_context=offline_context(tool12_result_sets=[source_result_set], projection_receipts=[projection]))
        self.assertEqual(output["result"]["members"], [58224])
        self.assertNotIn("customer", json.dumps(output).lower())

    def test_independent_count_operators_and_tamper(self):
        cases = [("OP-22", [D, E], operators.op22_count_unique_paths), ("OP-23", [{"afi": 4, "prefix": "10.0.0.0/8"}], operators.op23_count_unique_prefixes), ("OP-24", ["peer-1", "peer-2"], operators.op24_count_unique_peer_directions)]
        for operator_id, members, function in cases:
            set_digest = digest(sorted(members, key=lambda item: json.dumps(item, sort_keys=True)))
            inputs = {"members": members, "member_count": len(members), "set_digest": set_digest, "set_completeness": "complete"}
            binding = population_binding(operator_id, "members", members, [digest(member) for member in sorted(members, key=lambda item: json.dumps(item, sort_keys=True))])
            self.assertEqual(function(envelope(operator_id, inputs), **binding_kwargs(binding))["result"]["count"], len(members))
            bad = deepcopy(inputs); bad["member_count"] += 1
            with self.assertRaisesRegex(operators.OperatorContractError, "member_count_mismatch"):
                function(envelope(operator_id, bad), **binding_kwargs(binding))

    def set_operation(self, operator_id, left_members, right_members, function):
        left, right = typed_set(left_members), typed_set(right_members)
        inputs = {"left_set": left, "right_set": right, "member_type_id": "asn", "left_digest": left["set_digest"], "right_digest": right["set_digest"]}
        bindings = {
            "left_set": population_binding(operator_id, "left_set", left, [digest(member) for member in left["members"]]),
            "right_set": population_binding(operator_id, "right_set", right, [digest(member) for member in right["members"]]),
        }
        return function(envelope(operator_id, inputs), population_evidence_bindings=bindings, offline_structural_context=offline_context(*bindings.values()))

    def test_set_operations_and_empty_semantics(self):
        intersection = self.set_operation("OP-25", [1, 2], [2, 3], operators.op25_set_intersection)
        self.assertEqual(intersection["result"]["members"], [2])
        difference = self.set_operation("OP-26", [1, 2], [2, 3], operators.op26_set_directional_difference)
        self.assertEqual(difference["result"]["members"], [1])
        coverage = self.set_operation("OP-27", [1, 2], [2, 3], operators.op27_set_directional_coverage)
        self.assertEqual(coverage["result"]["ratio_exact"], "1/2")
        contained = self.set_operation("OP-27", [2], [1, 2], operators.op27_set_directional_coverage)
        self.assertEqual(contained["result"]["edge_projection"]["from_endpoint"]["domain_value_digest"], contained["result"]["right_digest"])
        self.assertEqual(contained["result"]["edge_projection"]["to_endpoint"]["domain_value_digest"], contained["result"]["left_digest"])
        jaccard = self.set_operation("OP-28", [1, 2], [2, 3], operators.op28_set_jaccard)
        self.assertEqual(jaccard["result"]["ratio_exact"], "1/3")
        both_empty = self.set_operation("OP-28", [], [], operators.op28_set_jaccard)
        self.assertEqual(both_empty["result"]["outcome"], "not_comparable_both_empty")
        empty_left = self.set_operation("OP-27", [], [1], operators.op27_set_directional_coverage)
        self.assertEqual(empty_left["result"]["outcome"], "not_computable_empty_denominator")

    def test_set_population_attacks_fail_closed(self):
        left, right = typed_set([1]), typed_set([1])
        inputs = {"left_set": left, "right_set": right, "member_type_id": "asn", "left_digest": left["set_digest"], "right_digest": right["set_digest"]}
        env = envelope("OP-25", inputs)
        with self.assertRaisesRegex(operators.OperatorContractError, "population_evidence_bindings_required"):
            operators.op25_set_intersection(env)
        left_binding = population_binding("OP-25", "left_set", left, [digest(1)])
        right_binding = population_binding("OP-25", "right_set", right, [digest(1)])
        forged = deepcopy(left_binding); forged["population_evidence_ref"]["source_digest"] = D
        with self.assertRaisesRegex(operators.OperatorContractError, "source_digest_mismatch"):
            operators.op25_set_intersection(env, population_evidence_bindings={"left_set": forged, "right_set": right_binding}, offline_structural_context=offline_context(forged, right_binding))
        ghost = deepcopy(right_binding); ghost["member_count"] = 2; ghost["receipt_digest"] = digest({k: v for k, v in ghost.items() if k != "receipt_digest"})
        with self.assertRaisesRegex(operators.OperatorContractError, "member_count_mismatch"):
            operators.op25_set_intersection(env, population_evidence_bindings={"left_set": left_binding, "right_set": ghost}, offline_structural_context=offline_context(left_binding, ghost))


class OperatorW3W4Tests(unittest.TestCase):
    def test_op29_directed_relations_missing_not_comparable_and_attacks(self):
        left = timed_fact("left", "2026-02-27T00:00:00Z")
        right = timed_fact("right", "2026-02-27T00:05:00Z")
        env = envelope("OP-29", {"left_fact": left, "right_fact": right, "comparability_profile": temporal_profile(), "left_digest": left["fact_digest"], "right_digest": right["fact_digest"]})
        out = operators.op29_classify_temporal_evidence_relation(env)
        self.assertEqual((out["result"]["relation"], out["result"]["delta_seconds"]), ("left_precedes_within", 300))
        self.assertEqual(out["result"]["edge_projection"]["relation_type"], "precedes")

        missing = deepcopy(env); missing["inputs"]["left_fact"]["time_utc"] = None
        self.assertEqual(operators.op29_classify_temporal_evidence_relation(missing)["result"]["relation"], "missing_left")
        incompatible = deepcopy(env); incompatible["inputs"]["comparability_profile"]["unit_compatible"] = False
        self.assertEqual(operators.op29_classify_temporal_evidence_relation(incompatible)["result"]["relation"], "not_comparable")
        forged = deepcopy(env); forged["inputs"]["left_digest"] = F
        with self.assertRaisesRegex(operators.OperatorContractError, "left_digest_mismatch"):
            operators.op29_classify_temporal_evidence_relation(forged)
        off_grid = deepcopy(env); off_grid["inputs"]["right_fact"]["time_utc"] = "2026-02-27T00:04:00Z"
        with self.assertRaisesRegex(operators.OperatorContractError, "state_point_off_grid"):
            operators.op29_classify_temporal_evidence_relation(off_grid)

    def test_vp_consistency_precedence_empty_and_attacks(self):
        common = {"prefix": "10.0.0.0/8", "afi": 4, "state_point_utc": "2026-02-27T00:00:00Z", "expected_direction_set": ["d1", "d2"], "direction_profile_digest": D}
        visibility_rows = [{"direction_id": "d1", "visibility": "unknown", "evidence_ref": evidence("d1")}]
        env = envelope("OP-30", {**common, "actual_visibility_rows": visibility_rows})
        out = operators.op30_classify_vp_visibility_consistency(env, inherited_evidence_refs=[evidence("expected")])
        self.assertEqual(out["result"]["class"], "missing_present")
        self.assertEqual(out["result"]["missing_set"], ["d2"])
        forged = deepcopy(env); forged["inputs"]["actual_visibility_rows"].append({"direction_id": "ghost", "visibility": "visible", "evidence_ref": evidence("ghost")})
        with self.assertRaisesRegex(operators.OperatorContractError, "unexpected_actual_direction"):
            operators.op30_classify_vp_visibility_consistency(forged, inherited_evidence_refs=[evidence("expected")])

        origins = [
            {"direction_id": "d1", "origin_state": "known", "origin_asns": [64500], "evidence_ref": evidence("o1")},
            {"direction_id": "d2", "origin_state": "known", "origin_asns": [64501, 64502], "evidence_ref": evidence("o2")},
        ]
        env31 = envelope("OP-31", {**common, "actual_origin_rows": origins})
        out31 = operators.op31_classify_vp_origin_consistency(env31, inherited_evidence_refs=[evidence("expected")])
        self.assertEqual(out31["result"]["class"], "divergent")
        self.assertTrue(out31["result"]["moas_present"])

        env32 = envelope("OP-32", {
            "prefix": common["prefix"], "afi": 4, "state_point_utc": common["state_point_utc"],
            "expected_direction_set": ["d1", "d2"], "canonicalization_profile_digest": operators.PATH_PROFILE_DIGEST,
            "actual_path_rows": [
                {"direction_id": "d1", "path_state": "known_ordered", "path_digest": D, "evidence_ref": evidence("p1")},
                {"direction_id": "d2", "path_state": "unknown", "path_digest": None, "evidence_ref": evidence("p2")},
            ],
        })
        out32 = operators.op32_classify_vp_path_consistency(env32, inherited_evidence_refs=[evidence("expected")])
        self.assertEqual(out32["result"]["class"], "unknown_present")
        empty31 = envelope("OP-31", {**common, "expected_direction_set": [], "actual_origin_rows": []})
        self.assertEqual(operators.op31_classify_vp_origin_consistency(empty31, inherited_evidence_refs=[evidence("empty-pop")])["result"]["class"], "empty_expected")
        with self.assertRaisesRegex(operators.OperatorContractError, "expected_direction_population_evidence_required"):
            operators.op31_classify_vp_origin_consistency(empty31)

    def test_op33_exact_join_preserves_unmatched_and_rejects_future_fill(self):
        left = [
            {"prefix": "10.0.0.0/8", "afi": 4, "first_observed_at_utc": "2026-02-27T00:00:00Z", "state_point_utc": "2026-02-27T00:05:00Z", "classification": "partial", "evidence_ref": evidence("np1")},
            {"prefix": "2001:db8::/32", "afi": 6, "first_observed_at_utc": "2026-02-27T00:00:00Z", "state_point_utc": "2026-02-27T00:05:00Z", "classification": "normal", "evidence_ref": evidence("np2")},
        ]
        route = {"prefix": "10.0.0.0/8", "afi": 4, "state_point_utc": "2026-02-27T00:05:00Z", "route_observation_key": "d1", "visibility": "visible", "origin_asns": [64500], "common_path_status": "ordered", "path_digest": D, "path_canonicalization_profile_id": operators.PATH_PROFILE_ID, "path_canonicalization_profile_digest": operators.PATH_PROFILE_DIGEST, "evidence_ref": evidence("rs1")}
        right = [route, {**route, "state_point_utc": "2026-02-27T00:10:00Z", "route_observation_key": "future", "evidence_ref": evidence("future")}]
        env = envelope("OP-33", {"new_prefix_state_rows": left, "route_state_rows": right, "left_digest": digest(left), "right_digest": digest(right)})
        out = operators.op33_join_new_prefix_route_state(env)
        self.assertEqual(len(out["result"]["matched"]), 1)
        self.assertEqual(len(out["result"]["unmatched_left"]), 1)
        self.assertEqual(len(out["result"]["unmatched_right"]), 1)
        self.assertEqual(len(out["result"]["edge_projections"]), 1)
        forged = deepcopy(env); forged["inputs"]["right_digest"] = F
        with self.assertRaisesRegex(operators.OperatorContractError, "right_digest_mismatch"):
            operators.op33_join_new_prefix_route_state(forged)
        duplicate = deepcopy(env); duplicate["inputs"]["route_state_rows"].append(deepcopy(route)); duplicate["inputs"]["right_digest"] = digest(duplicate["inputs"]["route_state_rows"])
        with self.assertRaisesRegex(operators.OperatorContractError, "duplicate_route_state_join_member"):
            operators.op33_join_new_prefix_route_state(duplicate)

        empty_left = envelope("OP-33", {
            "new_prefix_state_rows": [], "route_state_rows": [route],
            "left_digest": digest([]), "right_digest": digest([route]),
        })
        empty_left_out = operators.op33_join_new_prefix_route_state(empty_left)
        self.assertEqual(empty_left_out["result"]["matched"], [])
        self.assertEqual(empty_left_out["result"]["unmatched_left"], [])
        self.assertEqual(empty_left_out["result"]["unmatched_right"], [route])

        empty_right = envelope("OP-33", {
            "new_prefix_state_rows": [left[0]], "route_state_rows": [],
            "left_digest": digest([left[0]]), "right_digest": digest([]),
        })
        empty_right_out = operators.op33_join_new_prefix_route_state(empty_right)
        self.assertEqual(empty_right_out["result"]["matched"], [])
        self.assertEqual(empty_right_out["result"]["unmatched_left"], [left[0]])
        self.assertEqual(empty_right_out["result"]["unmatched_right"], [])

        empty_both = envelope("OP-33", {
            "new_prefix_state_rows": [], "route_state_rows": [],
            "left_digest": digest([]), "right_digest": digest([]),
        })
        with self.assertRaisesRegex(operators.OperatorContractError, "population_evidence_ref_required"):
            operators.op33_join_new_prefix_route_state(empty_both)
        empty_both_out = operators.op33_join_new_prefix_route_state(
            empty_both, inherited_evidence_refs=[evidence("empty-populations")]
        )
        self.assertEqual(empty_both_out["result_state"], "empty")
        self.assertEqual(empty_both_out["result"]["join_cardinality"]["matched_binding_count"], 0)

    def _op29_and_receipt(self, left_digest, right_digest, relation="same_slot"):
        times = {
            "same_slot": ("2026-02-27T00:00:00Z", "2026-02-27T00:00:00Z"),
            "left_precedes_within": ("2026-02-27T00:00:00Z", "2026-02-27T00:05:00Z"),
            "left_precedes_outside": ("2026-02-27T00:00:00Z", "2026-02-27T00:10:00Z"),
        }
        left = timed_fact("temporal-left", times[relation][0]); left["fact_digest"] = left_digest
        right = timed_fact("temporal-right", times[relation][1]); right["fact_digest"] = right_digest
        env = envelope("OP-29", {"left_fact": left, "right_fact": right, "comparability_profile": temporal_profile(), "left_digest": left_digest, "right_digest": right_digest})
        output = operators.op29_classify_temporal_evidence_relation(env)
        return output, op29_receipt(output)

    def test_op37_only_same_slot_verified_exclusive_is_conflict_and_receipt_attacks(self):
        left = typed_fact("left", "prefix_up")
        right = typed_fact("right", "prefix_down")
        op29, receipt = self._op29_and_receipt(left["fact_digest"], right["fact_digest"])
        profile = {"profile_id": "PROFILE-EVIDENCE-CONSISTENCY-1.0.0", "profile_digest": D, "assertion_relation": "mutually_exclusive", "mutually_exclusive_predicate_ids": ["prefix_down", "prefix_up"]}
        env = envelope("OP-37", {"left_fact": left, "right_fact": right, "op29_temporal_receipt": receipt, "consistency_profile": profile, "left_digest": left["fact_digest"], "right_digest": right["fact_digest"]})
        out = operators.op37_classify_evidence_consistency(env, offline_structural_context=offline_context(op29_outputs=[op29]))
        self.assertEqual(out["result"]["class"], "conflict")
        self.assertEqual(out["result"]["edge_projection"]["relation_type"], "conflicts_with")
        forged = deepcopy(receipt); forged["relation"] = "left_precedes_outside"
        attacked = deepcopy(env); attacked["inputs"]["op29_temporal_receipt"] = forged
        with self.assertRaisesRegex(operators.OperatorContractError, "op29_receipt_projection_mismatch"):
            operators.op37_classify_evidence_consistency(attacked, offline_structural_context=offline_context(op29_outputs=[op29]))
        with self.assertRaisesRegex(operators.OperatorContractError, "offline_structural_context_required"):
            operators.op37_classify_evidence_consistency(env)

    def test_op38_half_open_overlap_empty_and_interval_attacks(self):
        window = {"start_utc": "2026-02-27T00:00:00Z", "end_utc": "2026-02-27T01:00:00Z"}
        left = [state_interval("2026-02-27T00:00:00Z", "2026-02-27T00:10:00Z")]
        right = [state_interval("2026-02-27T00:05:00Z", "2026-02-27T00:15:00Z", E)]
        env = envelope("OP-38", {"left_intervals": left, "right_intervals": right, "left_target_state": "complete", "right_target_state": "affected", "window": window, "grid_step_seconds": 300, "left_interval_set_digest": digest(left), "right_interval_set_digest": digest(right)})
        out = operators.op38_intersect_state_interval_sets(env, inherited_evidence_refs=[evidence("interval-pop")])
        self.assertEqual(out["result"]["overlap_intervals"][0]["duration_seconds"], 300)
        touching = deepcopy(env); touching_right = [state_interval("2026-02-27T00:10:00Z", "2026-02-27T00:15:00Z", E)]; touching["inputs"]["right_intervals"] = touching_right; touching["inputs"]["right_interval_set_digest"] = digest(touching_right)
        self.assertEqual(operators.op38_intersect_state_interval_sets(touching, inherited_evidence_refs=[evidence("interval-pop")])["result"]["outcome"], "disjoint")
        both_empty = deepcopy(env); both_empty["inputs"]["left_intervals"] = []; both_empty["inputs"]["right_intervals"] = []; both_empty["inputs"]["left_interval_set_digest"] = digest([]); both_empty["inputs"]["right_interval_set_digest"] = digest([])
        self.assertEqual(operators.op38_intersect_state_interval_sets(both_empty, inherited_evidence_refs=[evidence("empty-pop")])["result"]["outcome"], "empty_both")
        with self.assertRaisesRegex(operators.OperatorContractError, "interval_population_evidence_required"):
            operators.op38_intersect_state_interval_sets(both_empty)
        overlapping = deepcopy(env); overlapping["inputs"]["left_intervals"].append(state_interval("2026-02-27T00:05:00Z", "2026-02-27T00:15:00Z", F)); overlapping["inputs"]["left_interval_set_digest"] = digest(overlapping["inputs"]["left_intervals"])
        with self.assertRaisesRegex(operators.OperatorContractError, "overlapping_input_intervals"):
            operators.op38_intersect_state_interval_sets(overlapping, inherited_evidence_refs=[evidence("interval-pop")])

    def test_op39_prefix_projection_dedup_empty_and_attacks(self):
        rows = [fixed_member("m1", "10.0.0.0/8"), fixed_member("m2", "10.0.0.0/8", asns=(64501, 64502), basis="country_origin_moas"), fixed_member("m3", "2001:db8::/32")]
        env = envelope("OP-39", {"fixed_cohort_members": rows, "set_completeness": "complete", "input_digest": digest(rows)})
        out = operators.op39_project_fixed_cohort_prefix_set(env)
        self.assertEqual(out["result"]["members"], [{"afi": 4, "prefix": "10.0.0.0/8"}, {"afi": 6, "prefix": "2001:db8::/32"}])
        empty = envelope("OP-39", {"fixed_cohort_members": [], "set_completeness": "complete", "input_digest": digest([])})
        self.assertEqual(operators.op39_project_fixed_cohort_prefix_set(empty, inherited_evidence_refs=[evidence("empty-pop")])["result"]["member_count"], 0)
        with self.assertRaisesRegex(operators.OperatorContractError, "fixed_cohort_population_evidence_required"):
            operators.op39_project_fixed_cohort_prefix_set(empty)
        forged = deepcopy(env); forged["inputs"]["fixed_cohort_members"][0]["prefix"] = "10.1.0.0/8"; forged["inputs"]["input_digest"] = digest(forged["inputs"]["fixed_cohort_members"])
        with self.assertRaisesRegex(operators.OperatorContractError, "invalid_prefix"):
            operators.op39_project_fixed_cohort_prefix_set(forged)
        duplicate = deepcopy(env); duplicate["inputs"]["fixed_cohort_members"][1]["cohort_member_id"] = "m1"; duplicate["inputs"]["input_digest"] = digest(duplicate["inputs"]["fixed_cohort_members"])
        with self.assertRaisesRegex(operators.OperatorContractError, "duplicate_cohort_member_id"):
            operators.op39_project_fixed_cohort_prefix_set(duplicate)


class OperatorAtomicityAndBoundaryTests(unittest.TestCase):
    def test_all_expected_operators_are_individually_registered(self):
        expected = {f"OP-{n:02d}" for n in range(5, 34)} | {f"OP-{n:02d}" for n in range(35, 40)}
        self.assertEqual(set(operators.OPERATOR_FUNCTIONS), expected)
        self.assertEqual(len(set(operators.OPERATOR_FUNCTIONS.values())), len(expected))

    def test_operator_functions_do_not_call_other_operator_functions(self):
        names = {function.__name__ for function in operators.OPERATOR_FUNCTIONS.values()}
        for operator_id, function in operators.OPERATOR_FUNCTIONS.items():
            source = inspect.getsource(function)
            for other_name in names - {function.__name__}:
                self.assertNotIn(f"{other_name}(", source, operator_id)

    def test_module_has_no_file_network_tool_or_model_dependency(self):
        source = Path(operators.__file__).read_text(encoding="utf-8")
        for forbidden in ("requests", "urllib", "socket", "subprocess", "open(", "CountryOutageP2S1SourceStore", "query_tool", "model_client"):
            self.assertNotIn(forbidden, source)

    def test_cross_publication_and_incomplete_inputs_fail_closed(self):
        env = envelope("OP-05", {"members": [], "set_completeness": "complete", "population_evidence_ref": evidence()})
        env["inputs"]["identity"] = identity("pub-2")
        with self.assertRaisesRegex(operators.OperatorContractError, "cross_identity_input"):
            operators.execute_operator(env)
        incomplete = envelope("OP-05", {"members": [], "set_completeness": "complete", "population_evidence_ref": evidence()})
        incomplete["input_completeness"] = "incomplete"
        with self.assertRaisesRegex(operators.OperatorContractError, "incomplete_input_population"):
            operators.execute_operator(incomplete)

    def test_deterministic_replay(self):
        env = envelope("OP-05", {"members": [], "set_completeness": "complete", "population_evidence_ref": evidence()})
        self.assertEqual(operators.execute_operator(env), operators.execute_operator(deepcopy(env)))

    def test_result_payload_exact_keys_match_frozen_schema(self):
        schema = json.loads(Path("contracts/agent/country-outage-p2-s1-execution-unit-design/operator-contract.schema.json").read_text())
        for operator_id, expected in operators._RESULT_FIELDS.items():
            suffix = operator_id.removeprefix("OP-")
            definition = schema["$defs"][f"op{suffix}ResultPayload"]
            self.assertEqual(expected, frozenset(definition["properties"]), operator_id)
            self.assertEqual(expected, frozenset(definition["required"]), operator_id)
            self.assertFalse(definition["additionalProperties"], operator_id)

    def test_structural_binding_receipts_validate_draft202012_schema(self):
        receipts = [
            asn_operator_binding("OP-10", "OP-14", 10, {"output_digest": D, "result": {"input_digest": E}}),
            asn_operator_binding("OP-36", "OP-12", 10, {"output_digest": D, "result": {"input_digest": E}}),
        ]
        script = r'''
import json, sys
from jsonschema import Draft202012Validator, FormatChecker
schema=json.load(open("contracts/agent/country-outage-p2-s1-implementation/w1-w2-structural-binding.schema.json"))
Draft202012Validator.check_schema(schema)
validator=Draft202012Validator(schema, format_checker=FormatChecker())
errors=[]
for index, receipt in enumerate(json.load(sys.stdin)):
    errors.extend(f"{index}:{error.message}" for error in validator.iter_errors(receipt))
if errors:
    raise SystemExit("\n".join(errors))
'''
        completed = subprocess.run(
            ["uv", "run", "--with", "jsonschema[format-nongpl]==4.25.1", "python", "-c", script],
            input=json.dumps(receipts), text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)

    def test_actual_input_output_examples_validate_frozen_draft202012_schema(self):
        """每个 Operator 至少一个实际正例按冻结 Input/Output $def 完整验证。"""
        script = r'''
import json, sys
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
schema=json.load(open("contracts/agent/country-outage-p2-s1-execution-unit-design/operator-contract.schema.json"))
route_schema=json.load(open("contracts/data/route-event.schema.json"))
examples=json.load(sys.stdin)
Draft202012Validator.check_schema(schema)
registry=Registry().with_resource(route_schema["$id"], Resource.from_contents(route_schema))
root=Draft202012Validator(schema, format_checker=FormatChecker(), registry=registry)
errors=[]
for op, pair in examples.items():
    suffix=op[3:]
    for kind, payload in pair.items():
        definition=schema["$defs"][f"op{suffix}{kind}Envelope"]
        current=root.evolve(schema=definition)
        for error in current.iter_errors(payload):
            errors.append(f"{op}/{kind}:{'/'.join(map(str,error.absolute_path))}:{error.message}")
if errors:
    raise SystemExit("\n".join(errors))
'''
        examples = self._build_schema_examples()
        completed = subprocess.run(
            ["uv", "run", "--with", "jsonschema[format-nongpl]==4.25.1", "python", "-c", script],
            input=json.dumps(examples), text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)

    def test_op33_empty_population_inputs_and_outputs_validate_frozen_schema(self):
        """OP-33 的左右完整人口可分别或同时为空，空人口仍须继承人口 Evidence。"""
        left = {
            "prefix": "10.0.0.0/8", "afi": 4,
            "first_observed_at_utc": "2026-02-27T00:00:00Z",
            "state_point_utc": "2026-02-27T00:05:00Z",
            "classification": "partial", "evidence_ref": evidence("op33-empty-left-row"),
        }
        right = {
            "prefix": "10.0.0.0/8", "afi": 4,
            "state_point_utc": "2026-02-27T00:05:00Z",
            "route_observation_key": "d1", "visibility": "visible",
            "origin_asns": [64500], "common_path_status": "ordered",
            "path_digest": D,
            "path_canonicalization_profile_id": operators.PATH_PROFILE_ID,
            "path_canonicalization_profile_digest": operators.PATH_PROFILE_DIGEST,
            "evidence_ref": evidence("op33-empty-right-row"),
        }
        cases = []
        for left_rows, right_rows in (([], [right]), ([left], []), ([], [])):
            current = envelope("OP-33", {
                "new_prefix_state_rows": left_rows,
                "route_state_rows": right_rows,
                "left_digest": digest(left_rows),
                "right_digest": digest(right_rows),
            })
            inherited = [evidence("op33-empty-populations")] if not left_rows and not right_rows else ()
            output = operators.op33_join_new_prefix_route_state(
                current, inherited_evidence_refs=inherited
            )
            cases.append({"Input": current, "Output": output})

        script = r'''
import json, sys
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
schema=json.load(open("contracts/agent/country-outage-p2-s1-execution-unit-design/operator-contract.schema.json"))
route_schema=json.load(open("contracts/data/route-event.schema.json"))
cases=json.load(sys.stdin)
Draft202012Validator.check_schema(schema)
registry=Registry().with_resource(route_schema["$id"], Resource.from_contents(route_schema))
root=Draft202012Validator(schema, format_checker=FormatChecker(), registry=registry)
errors=[]
for index, pair in enumerate(cases):
    for kind, payload in pair.items():
        definition=schema["$defs"][f"op33{kind}Envelope"]
        for error in root.evolve(schema=definition).iter_errors(payload):
            errors.append(f"{index}/{kind}:{'/'.join(map(str,error.absolute_path))}:{error.message}")
if errors:
    raise SystemExit("\n".join(errors))
'''
        completed = subprocess.run(
            ["uv", "run", "--with", "jsonschema[format-nongpl]==4.25.1", "python", "-c", script],
            input=json.dumps(cases), text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)

    def _build_schema_examples(self):
        examples = {}
        def add(env, output): examples[env["operator_id"]] = {"Input": env, "Output": output}

        # W1 状态、比例和排名。
        members = [{"asn": 10, "peak_invisible_direction_count": 2, "peak_complete_prefix_count": 1, "fixed_prefix_count": 2, "evidence_ref": evidence("as10")}]
        env = envelope("OP-05", {"members": members, "set_completeness": "complete", "population_evidence_ref": evidence()}); add(env, operators.op05_as_severity_rank(env))
        points = [state_point(0, "normal"), state_point(5, "complete")]
        for op, function, extra in (
            ("OP-06", operators.op06_select_first_state_occurrence, {"target_state": "complete", "series_digest": D}),
            ("OP-07", operators.op07_derive_state_intervals, {"target_state": "complete", "grid_step_seconds": 300, "window": {"start_utc": "2026-02-27T00:00:00Z", "end_utc": "2026-02-28T00:00:00Z"}, "series_digest": D}),
            ("OP-08", operators.op08_select_last_state_at_cutoff, {"cutoff_utc": "2026-02-27T00:05:00Z", "series_digest": D}),
            ("OP-35", operators.op35_select_last_state_occurrence, {"target_state": "complete", "series_digest": D}),
        ):
            payload = {"ordered_state_points": points, **extra}; env = envelope(op, payload); b = population_binding(op, "ordered_state_points", points, [p["member_key"] for p in points]); add(env, function(env, **binding_kwargs(b)))
        severity = [{"state_point_utc": "2026-02-27T00:00:00Z", "asn": 10, "classification": "affected", "fixed_prefix_count": 2, "partial_prefix_count": 0, "complete_prefix_count": 1, "unknown_prefix_count": 0, "invisible_direction_count": 2, "evidence_ref": evidence("sev")}]
        env = envelope("OP-09", {"ordered_state_points": severity, "severity_field": "complete_prefix_count", "series_digest": D}); b = population_binding("OP-09", "ordered_state_points", severity, ["sev"]); add(env, operators.op09_select_peak_state_observation(env, **binding_kwargs(b)))
        env = envelope("OP-10", {"asn": 10, "peak_complete_prefix_count": 1, "fixed_prefix_count": 2, "member_digest": D}); op10 = operators.op10_compute_as_peak_complete_ratio(env, inherited_evidence_refs=[evidence("op10")]); add(env, op10)
        interval = {"start_utc": "2026-02-27T00:00:00Z", "end_utc": "2026-02-27T00:05:00Z", "duration_seconds": 300, "left_censored": False, "right_censored": False, "member_digests": [D]}
        env = envelope("OP-11", {"intervals": [interval], "set_completeness": "complete", "input_digest": D}); b11 = population_binding("OP-11", "intervals", [interval], [digest(interval)]); op11 = operators.op11_select_longest_interval(env, **binding_kwargs(b11)); add(env, op11)
        profile = {"profile_id": "PROFILE-FIRST-CROSSING-1.0.0", "profile_version": "1.0.0", "metric_field": "complete_prefix_count", "threshold_exact": {"numerator": 1, "denominator": 1}, "comparison": "gte", "grid_step_seconds": 300, "gap_policy": "indeterminate_if_any_gap_precedes_candidate_or_prevents_no_crossing_proof", "profile_digest": D}
        numeric = [{"state_point_utc": "2026-02-27T00:00:00Z", "value": 0, "value_state": "known", "member_key": "n0", "evidence_ref": evidence("n0")}, {"state_point_utc": "2026-02-27T00:05:00Z", "value": 1, "value_state": "known", "member_key": "n1", "evidence_ref": evidence("n1")}]
        env36 = envelope("OP-36", {"ordered_numeric_points": numeric, "threshold_profile_instance": profile, "series_digest": D}); b36 = population_binding("OP-36", "ordered_numeric_points", numeric, ["n0", "n1"]); op36 = operators.op36_detect_first_threshold_crossing(env36, **binding_kwargs(b36)); add(env36, op36)
        receipt36 = op36_projection(op36, 10); bind36 = asn_operator_binding("OP-36", "OP-12", 10, op36)
        env = envelope("OP-12", {"crossing_receipts": [receipt36], "set_completeness": "complete", "profile_digest": D}); b = population_binding("OP-12", "crossing_receipts", [receipt36], [receipt36["output_digest"]]); add(env, operators.op12_rank_as_first_threshold_crossing(env, population_evidence_binding=b, asn_bound_op36_receipts=[bind36], offline_structural_context=offline_context(b, op36_outputs=[op36])))
        projection11 = deepcopy(op11); projection11["result"] = {key: op11["result"][key] for key in ("outcome", "duration_seconds", "intervals")}
        binding13 = {"schema_version": "country_outage_p2_s1_asn_bound_op11_receipt_v1", "receipt_kind": "asn_bound_op11_receipt", "design_candidate_id": operators.DESIGN_CANDIDATE_ID, "target_operator_id": "OP-13", "asn": 10, "op11_output_digest": op11["output_digest"], "op11_input_digest": op11["result"]["input_digest"], "source_plan_id": "p", "source_plan_revision": 1, "source_plan_node_id": "n", "source_node_result_digest": op11["output_digest"], "source_asn_binding_digest": F, "evidence_refs": [evidence("b13")], "validator": {"validator_id": operators.STRUCTURAL_VALIDATOR_ID, "validator_version": "1.0.0", "contract_digest": D, "implementation_digest": E}, "business_transform_count": 0}; binding13["receipt_digest"] = digest(binding13)
        env = envelope("OP-13", {"longest_interval_receipts": [projection11], "set_completeness": "complete"}); b = population_binding("OP-13", "longest_interval_receipts", [projection11], [op11["output_digest"]]); add(env, operators.op13_rank_as_longest_duration(env, population_evidence_binding=b, asn_bound_op11_receipts=[binding13], offline_structural_context=offline_context(b, op11_outputs=[op11])))
        projected10 = op10_projection(op10); bind10 = asn_operator_binding("OP-10", "OP-14", 10, op10)
        env = envelope("OP-14", {"ratio_receipts": [projected10], "set_completeness": "complete"}); b = population_binding("OP-14", "ratio_receipts", [projected10], [op10["output_digest"]]); add(env, operators.op14_rank_as_peak_complete_ratio(env, population_evidence_binding=b, asn_bound_op10_receipts=[bind10], offline_structural_context=offline_context(b, op10_outputs=[op10])))

        # W2 路径结构、投影、计数与集合。
        segments = [{"segment_type": "as_sequence", "asns": [1, 2, 3]}]
        schema_path_digest = digest(segments)
        env15 = envelope("OP-15", {"path_id": "p", "path_digest": schema_path_digest, "path_canonicalization_profile_id": operators.PATH_PROFILE_ID, "path_canonicalization_profile_digest": operators.PATH_PROFILE_DIGEST, "path_segments": segments, "target_asn": 2, "common_path_status": "ordered"}); op15 = operators.op15_locate_asn_positions(env15, inherited_evidence_refs=[evidence("path")]); add(env15, op15)
        r15 = OperatorW2Tests().compact_op15(op15)
        path_context = offline_context(op15_outputs=[op15])
        env = envelope("OP-16", {"path_segments": segments, "path_digest": schema_path_digest, "op15_position_receipt": r15}); add(env, operators.op16_project_direct_path_neighbors(env, offline_structural_context=path_context))
        env = envelope("OP-17", {"left_position_receipt": r15, "right_position_receipt": r15, "path_digest": schema_path_digest}); add(env, operators.op17_classify_ordered_asn_path_relation(env, offline_structural_context=path_context))
        rows = [path_row()]; keys = [D + ":109.74.224.0/20"]
        for op, function, extra in (("OP-18", operators.op18_project_path_prefix_set, {"input_digest": D}), ("OP-20", operators.op20_project_canonical_path_set, {"canonicalization_profile_digest": operators.PATH_PROFILE_DIGEST}), ("OP-21", operators.op21_project_peer_direction_set, {"direction_identity_profile_digest": F})):
            env = envelope(op, {"path_evidence_members": rows, "set_completeness": "complete", **extra}); b = population_binding(op, "path_evidence_members", rows, keys); add(env, function(env, **binding_kwargs(b)))
        # OP-19使用独立的正常测试覆盖；此处复用该测试产物较冗长，构造完整受信闭包。
        source = {"result_set_id": "rs", "result_set_revision": 1, "manifest_digest": D, "content_digest": E, "freeze_receipt_digest": F, "query_receipt_digest": D, "source_population_id": "window_path_association_evidence_rows", "source_dataset_digest": E, "member_identity": "path_association_id"}
        assoc = {"source_member_key": "a", "source_member_digest": D, "anchor_asn": 2, "known_origin_asn": 3, "origin_status": "known", "observed_origin_asn": 3, "path_digest": D, "path_canonicalization_profile_id": operators.PATH_PROFILE_ID, "path_canonicalization_profile_digest": operators.PATH_PROFILE_DIGEST, "evidence_ref": evidence("a")}
        rs = {"identity": identity(), "result_set_id": "rs", "result_set_revision": 1, "content_digest": E, "manifest_digest": D, "query_receipt_digest": D, "completeness": "complete", "normalized_query": {"anchor_asn": 2, "anchor_before_known_origin": True}, "members": [{"source_member_key": "a", "source_member_digest": D}]}
        pr = {"design_candidate_id": operators.DESIGN_CANDIDATE_ID, "operator_id": "OP-19", "anchor_asn": 2, "source_result_set_content_digest": E, "query_receipt_digest": D, "source_member_keys_digest": digest(["a"]), "source_member_digests_digest": digest([D]), "projected_member_keys_digest": digest(["a"]), "projected_member_digests_digest": digest([D]), "business_transform_count": 0}; pr["receipt_digest"] = digest(pr)
        env = envelope("OP-19", {"anchor_asn": 2, "source_result_set_ref": source, "association_members": [assoc], "set_completeness": "complete", "source_result_set_query_receipt_digest": D, "population_filter_receipt_digest": D, "host_projection_receipt_digest": pr["receipt_digest"], "population_evidence_ref": evidence("population", D)}); add(env, operators.op19_project_observed_downstream_origin_set(env, offline_structural_context=offline_context(tool12_result_sets=[rs], projection_receipts=[pr])))
        count_cases = (("OP-22", [D], operators.op22_count_unique_paths), ("OP-23", [{"afi": 4, "prefix": "10.0.0.0/8"}], operators.op23_count_unique_prefixes), ("OP-24", ["peer"], operators.op24_count_unique_peer_directions))
        for op, ms, function in count_cases:
            canonical = sorted(ms, key=lambda item: json.dumps(item, sort_keys=True)); env = envelope(op, {"members": canonical, "member_count": len(canonical), "set_digest": digest(canonical), "set_completeness": "complete"}); b = population_binding(op, "members", canonical, [digest(m) for m in canonical]); add(env, function(env, **binding_kwargs(b)))
        for op, function in (("OP-25", operators.op25_set_intersection), ("OP-26", operators.op26_set_directional_difference), ("OP-27", operators.op27_set_directional_coverage), ("OP-28", operators.op28_set_jaccard)):
            left, right = typed_set([1, 2]), typed_set([2]); env = envelope(op, {"left_set": left, "right_set": right, "member_type_id": "asn", "left_digest": left["set_digest"], "right_digest": right["set_digest"]}); lbs = population_binding(op, "left_set", left, [digest(m) for m in left["members"]]); rbs = population_binding(op, "right_set", right, [digest(m) for m in right["members"]]); add(env, function(env, population_evidence_bindings={"left_set": lbs, "right_set": rbs}, offline_structural_context=offline_context(lbs, rbs)))

        # W3/W4：点时间关系、VP一致性、exact join、证据一致性、区间交集与cohort前缀投影。
        left_timed = timed_fact("schema-left", "2026-02-27T00:00:00Z")
        right_timed = timed_fact("schema-right", "2026-02-27T00:05:00Z")
        env29 = envelope("OP-29", {"left_fact": left_timed, "right_fact": right_timed, "comparability_profile": temporal_profile(), "left_digest": left_timed["fact_digest"], "right_digest": right_timed["fact_digest"]})
        output29 = operators.op29_classify_temporal_evidence_relation(env29); add(env29, output29)
        vp_common = {"prefix": "10.0.0.0/8", "afi": 4, "state_point_utc": "2026-02-27T00:00:00Z", "expected_direction_set": ["d1"], "direction_profile_digest": D}
        env = envelope("OP-30", {**vp_common, "actual_visibility_rows": [{"direction_id": "d1", "visibility": "visible", "evidence_ref": evidence("v1")}]}); add(env, operators.op30_classify_vp_visibility_consistency(env, inherited_evidence_refs=[evidence("vp-pop")]))
        env = envelope("OP-31", {**vp_common, "actual_origin_rows": [{"direction_id": "d1", "origin_state": "known", "origin_asns": [64500], "evidence_ref": evidence("o1")}]}); add(env, operators.op31_classify_vp_origin_consistency(env, inherited_evidence_refs=[evidence("vp-pop")]))
        env = envelope("OP-32", {"prefix": "10.0.0.0/8", "afi": 4, "state_point_utc": "2026-02-27T00:00:00Z", "expected_direction_set": ["d1"], "canonicalization_profile_digest": operators.PATH_PROFILE_DIGEST, "actual_path_rows": [{"direction_id": "d1", "path_state": "known_ordered", "path_digest": D, "evidence_ref": evidence("path1")}]}); add(env, operators.op32_classify_vp_path_consistency(env, inherited_evidence_refs=[evidence("vp-pop")]))
        new_prefix = [{"prefix": "10.0.0.0/8", "afi": 4, "first_observed_at_utc": "2026-02-27T00:00:00Z", "state_point_utc": "2026-02-27T00:05:00Z", "classification": "partial", "evidence_ref": evidence("np")}]
        route_state = [{"prefix": "10.0.0.0/8", "afi": 4, "state_point_utc": "2026-02-27T00:05:00Z", "route_observation_key": "d1", "visibility": "visible", "origin_asns": [64500], "common_path_status": "ordered", "path_digest": D, "path_canonicalization_profile_id": operators.PATH_PROFILE_ID, "path_canonicalization_profile_digest": operators.PATH_PROFILE_DIGEST, "evidence_ref": evidence("route")}]
        env = envelope("OP-33", {"new_prefix_state_rows": new_prefix, "route_state_rows": route_state, "left_digest": digest(new_prefix), "right_digest": digest(route_state)}); add(env, operators.op33_join_new_prefix_route_state(env))
        left_fact = typed_fact("schema-fact-left", "visible"); right_fact = typed_fact("schema-fact-right", "invisible")
        left_for_29 = timed_fact("schema-fact-time-left", "2026-02-27T00:00:00Z"); left_for_29["fact_digest"] = left_fact["fact_digest"]
        right_for_29 = timed_fact("schema-fact-time-right", "2026-02-27T00:00:00Z"); right_for_29["fact_digest"] = right_fact["fact_digest"]
        env29_conflict = envelope("OP-29", {"left_fact": left_for_29, "right_fact": right_for_29, "comparability_profile": temporal_profile(), "left_digest": left_fact["fact_digest"], "right_digest": right_fact["fact_digest"]})
        output29_conflict = operators.op29_classify_temporal_evidence_relation(env29_conflict)
        profile37 = {"profile_id": "PROFILE-EVIDENCE-CONSISTENCY-1.0.0", "profile_digest": D, "assertion_relation": "mutually_exclusive", "mutually_exclusive_predicate_ids": ["invisible", "visible"]}
        env = envelope("OP-37", {"left_fact": left_fact, "right_fact": right_fact, "op29_temporal_receipt": op29_receipt(output29_conflict), "consistency_profile": profile37, "left_digest": left_fact["fact_digest"], "right_digest": right_fact["fact_digest"]}); add(env, operators.op37_classify_evidence_consistency(env, offline_structural_context=offline_context(op29_outputs=[output29_conflict])))
        window38 = {"start_utc": "2026-02-27T00:00:00Z", "end_utc": "2026-02-27T01:00:00Z"}; left38 = [state_interval("2026-02-27T00:00:00Z", "2026-02-27T00:10:00Z")]; right38 = [state_interval("2026-02-27T00:05:00Z", "2026-02-27T00:15:00Z", E)]
        env = envelope("OP-38", {"left_intervals": left38, "right_intervals": right38, "left_target_state": "complete", "right_target_state": "affected", "window": window38, "grid_step_seconds": 300, "left_interval_set_digest": digest(left38), "right_interval_set_digest": digest(right38)}); add(env, operators.op38_intersect_state_interval_sets(env, inherited_evidence_refs=[evidence("interval-pop")]))
        fixed = [fixed_member("schema-fixed", "10.0.0.0/8")]; env = envelope("OP-39", {"fixed_cohort_members": fixed, "set_completeness": "complete", "input_digest": digest(fixed)}); add(env, operators.op39_project_fixed_cohort_prefix_set(env))
        self.assertEqual(set(examples), set(operators.OPERATOR_FUNCTIONS))
        return examples


if __name__ == "__main__":
    unittest.main()
